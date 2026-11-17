"""Search: the query cut and its quoting, ranking, the filters, scratch sessions in every
tier, the states that leave an answer incomplete, a missing index, the writer lock it
never takes, and the library's answer."""

import os
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest
from moxi import files
from moxi_sessions import api as sessions

from moxi_memory import api, index, ingest, links, search, state, table

T = sessions.testing

#: Queries measured to crash FTS5 when passed to MATCH as typed.
CRASHING = [
    "foo.bar()", "C++ template", "-flag", "user@host:/path", '"unterminated',
    "docker-compose port mapping", "what is the port mapping", "NEAR sqlite", "a AND OR b",
    "path/to/file.py:42", "(", ")", "*", "^start", "col:value", "a + b", "x NOT y",
    "'single'", "\\", '"' * 7, "\x00nul", "{brace}", "[bracket]", "á", "́",
    "è́", "emoji \U0001F600 here", "", "   ", "?!.,;", "OR", "AND", "NOT",
]


class FakeLauncher:
    def __init__(self, answers=None):
        self.answers = answers or {}

    def launch_kind(self, session_id, timeout_s=1.0):
        return self.answers.get(session_id)


def config(**over):
    base = {"harnesses": ["claude", "codex", "opencode"], "index.subagents": False,
            "index.tail_chars": 1000, "redact.sets": ["credentials", "broad"]}
    base.update(over)
    return base


def run(mode="walk", harness=None, sid=None, *, kinds=None, **kw):
    kw.setdefault("config", config())
    kw.setdefault("launcher", links.Link(links.READY, FakeLauncher(kinds))
                  if kinds is not None else links.Link(links.NONE))
    kw.setdefault("then", lambda args: None)
    return ingest.run(mode, harness, sid, **kw)


def claude(n, *, start=1_700_000_000.0, **kw):
    sid = f"0190b000-0000-7000-8000-{n:012d}"
    t = T.ClaudeTranscript(sessions.root("claude"), session_id=sid, start=start, **kw)
    t._count = 1000 * n
    return t


def keys(result):
    return [(h.harness, h.session_id) for h in result.hits]


class Holder:
    """A child process that runs `code` and prints `ready`, then waits to be told to
    go."""

    def __init__(self, code: str):
        body = textwrap.indent(textwrap.dedent(code), "    ")
        script = ("import os, sys\nfrom moxi_memory import index, state\n"
                  f"def main():\n{body}\nmain()\n")
        self.proc = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True)
        line = self.proc.stdout.readline().strip()
        if line != "ready":
            self.proc.kill()
            raise AssertionError(f"holder said {line!r}: {self.proc.stderr.read()}")

    def release(self) -> int:
        self.proc.stdin.close()
        return self.proc.wait(timeout=30)


# -- the query --------------------------------------------------------------------------

@pytest.fixture
def fts():
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE VIRTUAL TABLE t USING fts5(text, tokenize = '{index.TOKENIZER}')")
    conn.executemany("INSERT INTO t (text) VALUES (?)", [
        ("docker-compose port mapping conflicts on 5432",),
        ("foo.bar() returns None when the cache is cold",),
        ("ssh user@host:/path/to/file.py:42 for the rsync leg",),
    ])
    yield conn
    conn.close()


@pytest.mark.parametrize("query", CRASHING)
def test_no_typed_punctuation_breaks_a_query(query, fts):
    tokens = search.cut(query)
    if tokens:
        fts.execute("SELECT count(*) FROM t WHERE t MATCH ?",
                    (search.match(tokens),)).fetchone()


def test_the_raw_crash_cases_really_crash(fts):
    crashed = 0
    for query in CRASHING[:10]:
        try:
            fts.execute("SELECT count(*) FROM t WHERE t MATCH ?", (query,)).fetchone()
        except sqlite3.OperationalError:
            crashed += 1
    assert crashed >= 6


@pytest.mark.parametrize("query", CRASHING)
def test_a_punctuation_query_runs_against_a_real_index(query):
    t = claude(1)
    t.user("foo.bar() and a C++ template")
    t.write()
    run()
    result = api.search(query)
    assert result.rows == 1


def test_the_cut_matches_the_tokenizer_on_every_printable_ascii_and_latin_character():
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE VIRTUAL TABLE t USING fts5(x, tokenize = 'unicode61 "
                 "remove_diacritics 2')")
    conn.execute("CREATE VIRTUAL TABLE v USING fts5vocab(t, 'instance')")
    samples = [f"ab{chr(c)}cd" for c in range(0x20, 0x250)]
    samples += ["snake_case_name", "foo.bar()", "café naïve", "C++", "a-b/c:d"]
    conn.executemany("INSERT INTO t (rowid, x) VALUES (?, ?)", list(enumerate(samples)))
    for doc, text in enumerate(samples):
        theirs = [term for (term,) in conn.execute(
            "SELECT term FROM v WHERE doc = ? ORDER BY offset", (doc,))]
        ours = [fold(t) for t in search.cut(text)]
        assert ours == [fold(t) for t in theirs], text


def fold(token):
    """A token as the tokenizer stores it, near enough to compare: lower case, with no
    diacritics."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", token.lower())
    return "".join(ch for ch in plain if not unicodedata.combining(ch))


def test_tokens_are_quoted_kept_once_and_or_joined():
    assert search.cut("Port port PORT mapping") == ("Port", "mapping")
    assert search.match(('a"b', "c")) == '"a""b" OR "c"'
    assert search.cut("snake_case") == ("snake", "case")
    assert len(search.cut(" ".join(f"w{i}" for i in range(500)))) == search.MAX_TOKENS
    assert search.cut(None) == ()


def test_any_token_finds_a_row_and_a_stemmed_query_finds_other_forms():
    t = claude(1)
    t.user("the widget runs fast")
    t.assistant("unrelated sentence entirely")
    t.write()
    run()
    assert [h.snippet for h in api.search("running").hits] == ["the widget «runs» fast"]
    assert len(api.search("widget entirely").hits) == 2


def test_ranking_is_bm25_then_newest_then_row_id():
    t = claude(1, step=60.0)
    t.user("zebra")
    t.user("zebra")
    t.user("zebra zebra zebra and the zebra")
    t.write()
    run()
    hits = api.search("zebra").hits
    assert hits[0].snippet.count("«zebra»") == 4
    assert hits[1].time > hits[2].time      # a tie goes to the newer row


# -- the answer -------------------------------------------------------------------------

def test_an_empty_answer_says_how_many_rows_were_searched():
    t = claude(1)
    t.user("alpha")
    t.assistant("beta")
    t.write()
    run()
    result = api.search("gamma")
    assert result.hits == () and result.rows == 2
    assert result.filling is None and result.rebuilding is None


def test_a_missing_index_raises_and_an_unreadable_one_too():
    with pytest.raises(api.IndexMissing):
        api.search("anything")
    run()
    path = index.db_path(index.current())
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(f"{path}{suffix}")
        except FileNotFoundError:
            pass
    path.write_bytes(b"this is not a database at all, not even close" * 100)
    with pytest.raises(api.IndexUnreadable):
        api.search("anything")


def test_a_search_during_the_first_full_read_says_it_is_still_filling():
    with index.writer() as w:
        conn = w.connect()
        index.meta_set(conn, "listed.claude", 5)
        conn.execute("INSERT INTO sources (harness, source) VALUES ('claude', 'one')")
        conn.close()
    result = api.search("anything")
    assert result.filling == search.Filling(1, 5)


def test_the_first_walk_records_how_many_sources_it_listed():
    for n in (1, 2, 3):
        t = claude(n)
        t.user("x")
        t.write()
    run(config=config(harnesses=["claude"]))
    with index.open_read() as r:
        assert index.meta_get(r.conn, "listed.claude") == "3"
        assert search.filling(r.conn) is None


def test_a_search_during_a_rebuild_says_a_rebuild_is_running():
    t = claude(1)
    t.user("hello")
    t.write()
    run()
    holder = Holder("""
        with index.writer() as w:
            with w.rebuilding():
                print('ready', flush=True)
                sys.stdin.read()
    """)
    try:
        result = api.search("hello")
        assert result.rebuilding is not None and abs(result.rebuilding - time.time()) < 60
        assert len(result.hits) == 1
    finally:
        assert holder.release() == 0
    assert api.search("hello").rebuilding is None


def test_a_crashed_rebuilds_entry_reads_as_none_also_with_a_live_process_id():
    run()
    with files.lock(index.rebuild_lock_path(), timeout_s=0):
        pass
    sc = state.connect()
    state.write_entry(sc, time.time() - 600, os.getpid())
    assert api.search("anything").rebuilding is None
    sc.close()


def test_a_search_never_takes_the_writer_lock(monkeypatch):
    t = claude(1)
    t.user("hello")
    t.write()
    run()
    holder = Holder("""
        with index.writer():
            print('ready', flush=True)
            sys.stdin.read()
    """)
    try:
        taken = []
        real = files.lock
        monkeypatch.setattr(files, "lock", lambda path, **kw: taken.append(path) or real(
            path, **kw))
        assert len(api.search("hello").hits) == 1
        api.status(with_backlog=False)
        assert taken == []
    finally:
        assert holder.release() == 0


def test_a_search_that_opened_the_old_generation_reads_it_to_the_end(monkeypatch):
    kept = claude(1)
    kept.user("needle kept")
    kept.write()
    gone = claude(2)
    gone.user("needle gone")
    gone.write()
    run()
    os.remove(gone.path)
    old = index.current()
    real = search._count

    def rebuild_between(conn, table_name, filters):
        if table_name == "rows":
            run("rebuild")        # switches the pointer while this search reads
            assert index.current() != old
        return real(conn, table_name, filters)

    monkeypatch.setattr(search, "_count", rebuild_between)
    result = api.search("needle")
    assert result.generation == old and len(result.hits) == 2 and result.rows == 2
    monkeypatch.setattr(search, "_count", real)
    assert old in index.generations()        # left for the next child
    assert [h.snippet for h in api.search("needle").hits] == ["«needle» kept"]
    run()
    assert old not in index.generations()


# -- the filters ------------------------------------------------------------------------

def test_project_since_harness_and_limit(tmp_path):
    for name in ("alpha", "beta"):
        (tmp_path / name / ".git").mkdir(parents=True)
    a = claude(1, cwd=str(tmp_path / "alpha"), start=1_700_000_000.0)
    a.user("shared word one")
    a.write()
    b = claude(2, cwd=str(tmp_path / "beta"), start=1_800_000_000.0)
    b.user("shared word two")
    b.write()
    r = T.CodexRollout(sessions.root("codex"))
    r.turn()
    r.user("shared word codex")
    r.write()
    run()
    assert keys(api.search("shared", project="alpha")) == [("claude", a.session_id)]
    assert api.search("shared", project="alpha").rows == 1
    assert ("claude", b.session_id) in keys(api.search("shared", since=1_750_000_000))
    assert ("claude", a.session_id) not in keys(api.search("shared",
                                                           since=1_750_000_000))
    assert {h.harness for h in api.search("shared", harness="codex").hits} == {"codex"}
    assert len(api.search("shared", limit=1).hits) == 1
    with pytest.raises(ValueError):
        api.search("shared", limit=0)


def test_results_search_tool_result_tails_only_when_asked():
    t = claude(1)
    call = t.request().tool_use("Bash", {"command": "ls"})
    t.tool_result(call, "quokka.txt", is_error=True)
    t.write()
    run()
    assert api.search("quokka").hits == ()
    hits = api.search("quokka", results=True).hits
    assert [(h.tier, h.kind, h.failed) for h in hits] == [(2, "result", True)]


def test_subagents_only_when_asked():
    t = claude(1)
    t.user("main words")
    sub = t.subagent("a0190b0000000001")
    sub.user("narwhal subagent words")
    t.write()
    run(config=config(**{"index.subagents": True}))
    assert api.search("narwhal").hits == ()
    hit, = api.search("narwhal", subagents=True).hits
    assert (hit.tier, hit.session_id, hit.agent_id) == ("subagent", t.session_id,
                                                        "a0190b0000000001")


# -- launch kinds -----------------------------------------------------------------------

def test_a_scratch_session_is_found_only_with_scratch_in_every_tier():
    t = claude(1)
    t.user("ocelot prompt")
    call = t.request().tool_use("Bash", {"command": "ls"})
    t.tool_result(call, "ocelot output")
    t.assistant("ocelot answer")
    sub = t.subagent("a0190b0000000002")
    sub.user("ocelot subagent")
    t.write()
    run(kinds={t.session_id: "scratch"}, config=config(**{"index.subagents": True}))
    assert api.search("ocelot", results=True, subagents=True).hits == ()
    assert api.search("ocelot", results=True, subagents=True).rows == 0
    tiers = {h.tier for h in api.search("ocelot", results=True, subagents=True,
                                        scratch=True).hits}
    assert tiers == {1, 2, "subagent"}
    assert {h.tier for h in api.search("ocelot", scratch=True).hits} == {1}


def test_project_seat_one_off_and_no_kind_sessions_are_found_by_default():
    kinds = {}
    for n, kind in enumerate(("project", "seat", "one-off", None), 1):
        t = claude(n, entrypoint="sdk-cli" if kind is None else "cli")  # one headless
        t.user("pangolin")
        t.assistant("answered")
        t.write()
        kinds[t.session_id] = kind
    r = T.CodexRollout(sessions.root("codex"))
    r.turn()
    r.user("pangolin from codex exec")
    r.write()
    run(kinds=kinds)
    assert len(api.search("pangolin").hits) == 5


def test_a_kind_learned_after_the_rows_were_written_applies_at_the_next_search():
    t = claude(1)
    t.user("axolotl")
    t.write()
    run()
    assert len(api.search("axolotl").hits) == 1
    sc = state.connect()
    sc.execute("UPDATE sessions SET kind = 'scratch' WHERE session_id = ?",
               (t.session_id,))
    sc.close()
    assert api.search("axolotl").hits == ()
    assert len(api.search("axolotl", scratch=True).hits) == 1


def test_with_no_session_table_nothing_is_left_out(monkeypatch):
    t = claude(1)
    t.user("tapir")
    t.write()
    run()
    os.remove(state.path())
    assert len(api.search("tapir").hits) == 1


def test_the_session_table_is_attached_read_only():
    t = claude(1)
    t.user("tapir")
    t.write()
    run()
    with index.open_read() as r:
        assert r.attach_state(search.STATE)
        with pytest.raises(sqlite3.OperationalError):
            r.conn.execute(f"DELETE FROM {search.STATE}.sessions")
    assert table.row(state.connect(), "claude", t.session_id) is not None
