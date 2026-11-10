"""Names by key over roots the test kit writes: what each pair is answered, which pairs
have no item, the roots that were not listed, what is refused, and how the read sits
beside a list's."""

from __future__ import annotations

import inspect
import os

import pytest

from moxi import modules
from moxi_sessions import api, catalog as catalog_module, lists, module
from moxi_sessions.catalog import Catalog
from moxi_sessions.lists import FACTS_MAX, list_sessions, name_sessions, newest_sessions
from moxi_sessions.shape import CLAUDE, CODEX, OPENCODE
from moxi_sessions.source import HEAD_LINES
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb
from moxi_sessions.testing.claude import START
from moxi_sessions.testing.codex import spawn_meta

NOW = START + 600.0
HOUR = 3600.0

not_root = pytest.mark.skipif(os.geteuid() == 0,
                              reason="root reads a folder without permission")


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


def tid(n: int) -> str:
    return f"0190c000-0000-7000-8000-{n:012x}"


@pytest.fixture
def roots(tmp_path):
    """A root per harness, each empty: a projects root, a sessions root and a database."""
    projects = tmp_path / "projects"
    rollouts = tmp_path / "codex" / "sessions"
    projects.mkdir()
    rollouts.mkdir(parents=True)
    database = OpenCodeDb(tmp_path / "opencode").write()
    return {CLAUDE: projects, CODEX: rollouts, OPENCODE: database}


def claude(roots, n: int, *, name: str | None = None, cwd: str = "/work/proj-a",
           project: str = "-work-proj-a") -> ClaudeTranscript:
    """A Claude session with one typed prompt and a reply, named in its last turn when
    `name` is given."""
    t = ClaudeTranscript(roots[CLAUDE], session_id=sid(n), cwd=cwd, project=project)
    t.user(f"prompt of session {n}", origin="human")
    if name is not None:
        t.title(name)
    t.assistant("Done.")
    t.write()
    return t


def codex(roots, n: int, **kw) -> CodexRollout:
    r = CodexRollout(roots[CODEX], thread_id=tid(n), **kw)
    r.turn()
    r.user("Do the thing.")
    r.write()
    return r


def item(harness: str, session: str, name=None, folder=None, started=None) -> dict:
    return {"harness": harness, "session": session, "name": name, "folder": folder,
            "started": started}


@pytest.fixture
def turns_read(monkeypatch):
    """The arguments of every turn read the catalog makes, in order."""
    reads = []
    real = catalog_module.window

    def counted(source, **kw):
        reads.append(kw)
        return real(source, **kw)

    monkeypatch.setattr(catalog_module, "window", counted)
    return reads


# --- what a pair is answered -------------------------------------------------------------------

def test_a_typed_name_is_answered_with_the_folder_and_the_start(roots):
    claude(roots, 1, name="fix the tests")

    answer = name_sessions(Catalog(roots), [(CLAUDE, sid(1))])

    assert answer == {"sessions": [item(CLAUDE, sid(1), "fix the tests", "/work/proj-a",
                                        START)],
                      "roots": []}


def test_a_name_only_in_the_head_is_answered(roots):
    t = ClaudeTranscript(roots[CLAUDE], session_id=sid(1))
    t.title("given at launch")
    for n in range(HEAD_LINES // 2 + 1):
        t.user(f"prompt {n}", origin="human")
        t.assistant()
    t.write()

    answer = name_sessions(Catalog(roots), [(CLAUDE, sid(1))])

    assert answer["sessions"][0]["name"] == "given at launch"


def test_a_title_the_harness_made_up_gives_no_name(roots):
    t = ClaudeTranscript(roots[CLAUDE], session_id=sid(1), cwd="/work/proj-a")
    t.title("made up by the harness", ai=True)
    t.user("go", origin="human")
    t.title("made up again", ai=True)
    t.write()

    answer = name_sessions(Catalog(roots), [(CLAUDE, sid(1))])

    assert answer["sessions"] == [item(CLAUDE, sid(1), None, "/work/proj-a", START)]


def test_codex_and_opencode_sessions_have_a_folder_and_a_start_and_no_name(roots):
    codex(roots, 1, cwd="/work/proj-b", start=START + 5)
    db = OpenCodeDb(roots[OPENCODE].parent)
    session = db.session(title="a title of unknown hand", directory="/work/proj-b",
                         at=START + 7)
    session.user("hello")
    db.write()

    answer = name_sessions(Catalog(roots), [(CODEX, tid(1)), (OPENCODE, session.id)])

    assert answer["sessions"] == [
        item(CODEX, tid(1), None, "/work/proj-b", START + 5),
        item(OPENCODE, session.id, None, "/work/proj-b", START + 7)]


def test_the_name_is_the_one_a_list_row_carries(roots):
    claude(roots, 1, name="typed by hand")
    claude(roots, 2)
    codex(roots, 3)
    cat = Catalog(roots)
    rows = newest_sessions(cat, recent_s=HOUR, now=NOW)["rows"]

    answer = name_sessions(Catalog(roots), [(row["harness"], row["session"])
                                            for row in rows])

    assert len(rows) == 3
    assert answer["sessions"] == [
        {key: row[key] for key in ("harness", "session", "name", "folder", "started")}
        for row in rows]


def test_the_items_are_in_the_order_asked(roots):
    for n in (1, 2, 3):
        claude(roots, n, name=f"name {n}")
    codex(roots, 4)
    asked = [(CLAUDE, sid(2)), (CODEX, tid(4)), (CLAUDE, sid(3)), (CLAUDE, sid(1)),
             (CLAUDE, sid(2))]

    answer = name_sessions(Catalog(roots), asked)

    assert [(i["harness"], i["session"]) for i in answer["sessions"]] == asked
    assert [i["name"] for i in answer["sessions"]] == ["name 2", None, "name 3",
                                                       "name 1", "name 2"]


def test_no_keys_answers_no_sessions(roots):
    claude(roots, 1)

    assert name_sessions(Catalog(roots), []) == {"sessions": [], "roots": []}


# --- pairs with no item ------------------------------------------------------------------------

def test_a_pair_no_root_holds_has_no_item(roots):
    claude(roots, 1, name="held")

    answer = name_sessions(Catalog(roots), [(CLAUDE, sid(9)), (CLAUDE, sid(1)),
                                            (CODEX, sid(1)), (OPENCODE, "ses_none"),
                                            (CLAUDE, None)])

    assert answer == {"sessions": [item(CLAUDE, sid(1), "held", "/work/proj-a", START)],
                      "roots": []}


def test_a_subagent_has_no_item(roots):
    t = ClaudeTranscript(roots[CLAUDE], session_id=sid(1))
    t.user("go", origin="human")
    t.subagent("a01").user("look")
    t.write()
    db = OpenCodeDb(roots[OPENCODE].parent)
    parent = db.session()
    child = parent.child()
    db.write()

    answer = name_sessions(Catalog(roots), [(CLAUDE, "a01"), (CLAUDE, "agent-a01"),
                                            (OPENCODE, child.id)])

    assert answer == {"sessions": [], "roots": []}


def test_a_spawned_thread_and_a_compressed_rollout_have_no_item(roots):
    main = codex(roots, 1)
    codex(roots, 2, meta_fields=spawn_meta(tid(1)))
    packed = main.path.with_name(f"rollout-2026-10-30T10-00-00-{tid(3)}.jsonl.zst")
    packed.write_bytes(b"not read")

    answer = name_sessions(Catalog(roots), [(CODEX, tid(2)), (CODEX, tid(3)),
                                            (CODEX, tid(1))])

    assert [i["session"] for i in answer["sessions"]] == [tid(1)]


def test_a_session_whose_file_cannot_be_read_is_answered_with_nulls(roots):
    t = claude(roots, 1, name="typed by hand")
    cat = Catalog(roots)
    cat.gather()
    t.path.unlink()                      # gone after the listing that still names it

    answer = name_sessions(cat, [(CLAUDE, sid(1))])

    assert answer == {"sessions": [item(CLAUDE, sid(1))], "roots": []}


# --- the roots that were not listed ------------------------------------------------------------

def test_a_root_that_could_not_be_listed_is_named_and_the_others_answer(roots, tmp_path):
    claude(roots, 1, name="held")
    roots[CODEX] = tmp_path / "nowhere"

    answer = name_sessions(Catalog(roots), [(CODEX, tid(1)), (CLAUDE, sid(1)),
                                            (CLAUDE, sid(2))])

    assert [i["session"] for i in answer["sessions"]] == [sid(1)]
    assert len(answer["roots"]) == 1
    failure = answer["roots"][0]
    assert set(failure) == {"harness", "root", "reason"}
    assert (failure["harness"], failure["root"]) == (CODEX, str(tmp_path / "nowhere"))
    assert "does not exist" in failure["reason"]


@not_root
def test_a_root_listed_in_part_is_named_with_no_reason(roots):
    claude(roots, 1, name="seen")
    hidden = claude(roots, 2, project="-work-proj-b").path.parent
    mode = hidden.stat().st_mode
    hidden.chmod(0)
    try:
        answer = name_sessions(Catalog(roots), [(CLAUDE, sid(1)), (CLAUDE, sid(2))])
    finally:
        hidden.chmod(mode)

    assert [i["session"] for i in answer["sessions"]] == [sid(1)]
    assert answer["roots"] == [{"harness": CLAUDE, "root": str(roots[CLAUDE]),
                                "reason": None}]


# --- what is refused -----------------------------------------------------------------------------

def test_two_hundred_pairs_are_answered_and_one_more_is_refused(roots):
    claude(roots, 1, name="held")
    cat = Catalog(roots)
    asked = [(CLAUDE, sid(1))] + [(CLAUDE, sid(n)) for n in range(2, FACTS_MAX + 1)]

    assert FACTS_MAX == 200 and len(asked) == 200
    assert len(name_sessions(cat, asked)["sessions"]) == 1
    with pytest.raises(ValueError, match="at most 200"):
        name_sessions(cat, asked + [(CLAUDE, sid(999))])


@pytest.mark.parametrize("harness", ["cursor", "", "Claude", None, 3])
def test_a_harness_that_is_not_one_is_refused_before_anything_is_read(roots, harness):
    claude(roots, 1)
    cat = Catalog(roots)

    with pytest.raises(ValueError, match="harness is one of claude, codex, opencode"):
        name_sessions(cat, [(CLAUDE, sid(1)), (harness, sid(1))])
    assert cat.head_reads == 0 and cat._gathered is None


@pytest.mark.parametrize("key", ["claude", (CLAUDE,), (CLAUDE, "a", "b"), None, 7])
def test_a_key_that_is_not_a_pair_is_refused(roots, key):
    with pytest.raises(ValueError, match="a key is a .harness, session. pair"):
        name_sessions(Catalog(roots), [key])


# --- how little is read, and what a list keeps -------------------------------------------------

def test_one_turn_is_read_of_each_session_and_none_before_it(roots, turns_read):
    t = ClaudeTranscript(roots[CLAUDE], session_id=sid(1))
    t.user("the real prompt", origin="human")
    t.assistant()
    for n in range(3):
        t.user(f"notice {n}", origin="task-notification")
        t.assistant()
    t.write()
    claude(roots, 2)
    claude(roots, 3)                     # not asked for: nothing of it is read
    codex(roots, 4)
    cat = Catalog(roots)
    heads = cat.head_reads               # none yet

    name_sessions(cat, [(CLAUDE, sid(1)), (CLAUDE, sid(2)), (CODEX, tid(4))])

    assert turns_read == [{"turns": 1}, {"turns": 1}]
    assert (heads, cat.head_reads, cat.name_reads, cat.tail_reads) == (0, 3, 2, 0)


def test_an_unchanged_file_is_read_once(roots, turns_read):
    claude(roots, 1, name="typed by hand")
    cat = Catalog(roots, reuse_s=0)

    first = name_sessions(cat, [(CLAUDE, sid(1))])
    again = name_sessions(cat, [(CLAUDE, sid(1))])

    assert again == first
    assert len(turns_read) == 1 and (cat.head_reads, cat.name_reads) == (1, 1)


def test_a_row_listed_after_its_session_was_named_still_carries_its_prompt(roots):
    claude(roots, 1, name="typed by hand")
    cat = Catalog(roots)

    named = name_sessions(cat, [(CLAUDE, sid(1))])
    row = list_sessions(cat, recent_s=HOUR, now=NOW)["groups"][0]["rows"][0]

    assert named["sessions"][0]["name"] == "typed by hand"
    assert row["prompt"] == {"at": START, "text": "prompt of session 1",
                             "truncated": False}
    assert row["activity"] == {"at": START + 1, "who": "agent", "text": "Done."}
    assert row["name"] == "typed by hand"
    assert row == list_sessions(Catalog(roots), recent_s=HOUR,
                                now=NOW)["groups"][0]["rows"][0]


def test_a_session_a_list_read_first_is_named_with_no_further_read(roots, turns_read):
    claude(roots, 1, name="typed by hand")
    cat = Catalog(roots)
    list_sessions(cat, recent_s=HOUR, now=NOW)
    read, heads = len(turns_read), cat.head_reads

    answer = name_sessions(cat, [(CLAUDE, sid(1))])

    assert answer["sessions"] == [item(CLAUDE, sid(1), "typed by hand", "/work/proj-a",
                                       START)]
    assert len(turns_read) == read and cat.head_reads == heads and cat.name_reads == 0


# --- the name other modules call -----------------------------------------------------------------

def test_interface_5_adds_session_facts_and_nothing_it_is_built_from():
    assert module.INTERFACES == frozenset({2, 3, 4, 5})
    modules.reset()
    assert modules.load("sessions", interface=5).ok
    assert not modules.load("sessions", interface=6).ok
    assert "session_facts" in api.__all__
    assert api.session_facts is lists.session_facts
    assert list(inspect.signature(api.session_facts).parameters) == ["keys"]
    for name in ("name_sessions", "Catalog", "Facts", "catalog", "lists"):
        assert name not in api.__all__ and not hasattr(api, name), name


def test_session_facts_answers_over_the_roots_the_process_names(tmp_path, monkeypatch):
    claude_home, codex_home, data = (tmp_path / name for name in ("claude", "codex",
                                                                  "data"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    t = api.testing.ClaudeTranscript(claude_home / "projects", session_id=sid(1),
                                     cwd="/work/proj-a")
    t.user("Do the thing.", origin="human")
    t.title("typed by hand")
    t.write()
    catalog_module.reset()
    try:
        answer = api.session_facts([(CLAUDE, sid(1)), (CODEX, tid(1))])
        with pytest.raises(ValueError):
            api.session_facts([("cursor", sid(1))])
    finally:
        catalog_module.reset()

    assert answer == {"sessions": [item(CLAUDE, sid(1), "typed by hand", "/work/proj-a",
                                        START)],
                      "roots": []}
