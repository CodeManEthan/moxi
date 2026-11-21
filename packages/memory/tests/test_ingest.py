"""The ingest child: records to rows for every harness, the key and its replace rule,
cursors, redaction and the secret-path rule, the writer lock, the session table's kinds,
the rebuild and the relabel, and the starters that return at once."""

import json
import os
import re
import time

import pytest
from moxi_dev.testing import wait_for
from moxi_sessions import api

from moxi_memory import index, ingest, links, rows, start, state, table

T = api.testing
TOKEN = "ghp_" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z5A"     # a 40-character token


class Config(dict):
    pass


def config(**over):
    base = {"harnesses": ["claude", "codex", "opencode"], "index.include_subagents": False,
            "index.tail_chars": 1000, "redact.sets": ["credentials", "broad"]}
    base.update(over)
    return Config(base)


class FakeLauncher:
    def __init__(self, answers=None, *, fail=False):
        self.answers = answers or {}
        self.fail = fail
        self.asked = []

    def launch_kind(self, session_id, timeout_s=1.0):
        self.asked.append(session_id)
        if self.fail:
            raise RuntimeError("the launch log is busy")
        return self.answers.get(session_id)


def launcher(answers=None, *, fail=False):
    return links.Link(links.READY, FakeLauncher(answers, fail=fail))


NO_LAUNCHER = links.Link(links.NONE)


def run(mode="walk", harness=None, sid=None, **kw):
    kw.setdefault("config", config())
    kw.setdefault("launcher", NO_LAUNCHER)
    kw.setdefault("then", lambda args: None)
    return ingest.run(mode, harness, sid, **kw)


def claude(sid="0190b000-0000-7000-8000-00000000c1a0", serial=0, **kw):
    t = T.ClaudeTranscript(api.root("claude"), session_id=sid, **kw)
    t._count = serial
    return t


def index_rows(table_name="rows"):
    with index.open_read() as r:
        cols = ("key, harness, session_id, agent_id, kind, text" if table_name == "rows"
                else "key, harness, session_id, failed, text")
        return r.conn.execute(f"SELECT {cols} FROM {table_name} ORDER BY key").fetchall()


def texts(table_name="rows"):
    return sorted(r[-1] for r in index_rows(table_name))


# -- each harness's main session -------------------------------------------------------

def test_each_harness_main_session_lands_in_tier_one_and_tier_two():
    t = claude()
    t.user("how do the widgets render")
    req = t.request()
    call = req.tool_use("Read", {"file_path": "/work/project/widget.py"})
    t.tool_result(call, "def render(): pass")
    t.assistant("the widgets render lazily")
    t.write()
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    r.user("codex question about sprockets")
    c = r.tool_call("exec", "ls sprockets")
    r.tool_output(c, "sprocket.py", )
    r.assistant("codex answer about sprockets")
    r.write()
    db = T.OpenCodeDb(os.path.dirname(api.root("opencode")))
    s = db.session()
    s.user("opencode question about gizmos")
    a = s.assistant()
    a.text("opencode answer about gizmos")
    a.tool("bash", {"command": "ls gizmos"}, output="gizmo.py")
    a.complete()
    db.write()

    report = run()
    assert not report.busy and report.sources == 3
    got = texts()
    for want in ("how do the widgets render", "the widgets render lazily",
                 "codex question about sprockets", "codex answer about sprockets",
                 "opencode question about gizmos", "opencode answer about gizmos"):
        assert want in got
    assert any(x.startswith("Read(/work/project/widget.py)") for x in got)
    tails = texts("tails")
    assert {"def render(): pass", "sprocket.py", "gizmo.py"} <= set(tails)
    with index.open_read() as rd:
        assert index.meta_get(rd.conn, "first_read") == "1"


def test_injected_meta_reasoning_and_compaction_are_in_no_tier_and_unknown_is_kept():
    t = claude()
    t.user("typed by the person", origin="human")
    t.user("/clear")                                        # a typed command: no origin
    t.user("a plugin sent this", origin="plugin")
    t.user("<task-notification>done</task-notification>", origin="task-notification")
    t.user("<local-command-stdout>output</local-command-stdout>")
    t.user("meta line", isMeta=True)
    t.compaction(summary="the summary of the earlier session")
    t.request().thinking("a private thought")
    t.write()
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    r.user("<recommended_plugins>plugq1</recommended_plugins>",
           kinds=["plugins.recommendations"])
    r.user("<recommended_plugins>plugq2</recommended_plugins>")
    r.user("codex typed text", kinds=["user.text"])
    r.write()
    db = T.OpenCodeDb(os.path.dirname(api.root("opencode")))
    s = db.session()
    m = s.user(None)
    m.text("synthetic only", synthetic=True)
    db.write()

    run()
    got = texts()
    assert "typed by the person" in got and "/clear" in got and "codex typed text" in got
    for gone in ("a plugin sent this", "done", "output", "meta line",
                 "the summary of the earlier session", "a private thought",
                 "plugq1", "plugq2", "synthetic only"):
        assert not any(gone in text for text in got), gone


def test_subagents_only_with_the_switch_and_under_the_main_session():
    t = claude()
    t.user("main prompt")
    sub = t.subagent("a0190b0000000001")
    sub.user("subagent work item")
    t.write()
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    r.user("codex main")
    child = r.spawn("0190c000-0000-7000-8000-0000000c0de9", "explore")
    child.turn()
    child.user("codex child work")
    r.write()
    child.write()

    run()
    assert not any("work" in x for x in texts())
    run("all", config=config(**{"index.include_subagents": True}))
    subs = {(r[2], r[3]) for r in index_rows() if "work" in r[-1]}
    assert subs == {(t.session_id, "a0190b0000000001"),
                    (r.thread_id, "0190c000-0000-7000-8000-0000000c0de9")}
    sc = state.connect()
    assert table.row(sc, "codex", "0190c000-0000-7000-8000-0000000c0de9") is None


def test_imported_codex_threads_are_skipped_and_compressed_ones_counted():
    r = T.CodexRollout(api.root("codex"), imported=True)
    r.turn()
    r.user("imported text")
    r.write()
    zst = api.root("codex") / "2026" / "10" / "30" / (
        "rollout-2026-10-30T10-00-00-0190c000-0000-7000-8000-00000000beef.jsonl.zst")
    zst.write_bytes(b"\x28\xb5\x2f\xfd")
    report = run()
    assert report.skipped == 1 and report.compressed == 1
    assert texts() == []
    with index.open_read() as rd:
        assert ingest.not_read(rd.conn) == 1


# -- the key, replace and cursors ------------------------------------------------------

def test_a_resume_copy_lands_once_and_two_blocks_give_two_rows():
    t = claude()
    t.user("first prompt")
    t.add({**json.loads(t.lines[-1]), "uuid": "two-blocks", "message": {
        "role": "user", "content": [{"type": "text", "text": "block text"},
                                    {"type": "tool_result", "tool_use_id": "x",
                                     "content": "block result"}]}})
    t.write()
    copy = claude("0190b000-0000-7000-8000-00000000c1a1")
    copy.lines = list(t.lines)
    copy.user("resumed prompt")
    copy.write()
    run()
    got = index_rows()
    assert [r[-1] for r in got].count("first prompt") == 1
    assert next(r for r in got if r[-1] == "first prompt")[2] == t.session_id
    keys = [r[0] for r in index_rows("tails") if r[-1] == "block result"]
    assert keys == ["claude|uuid:two-blocks|1"]


def test_an_opencode_tool_part_gives_a_call_and_a_result_and_a_grown_message_replaces():
    db = T.OpenCodeDb(os.path.dirname(api.root("opencode")))
    s = db.session()
    s.user("grow me")
    a = s.assistant()
    part = a.text("short answer")
    a.tool("bash", {"command": "ls"}, output="listing")
    db.write()
    run()
    a.update_part(part, {"text": "a longer answer now"})
    a.update()
    db.write()
    run("turn", "opencode", s.id)
    got = texts()
    assert "a longer answer now" in got and "short answer" not in got
    assert texts("tails") == ["listing"]
    assert sum(1 for x in got if x.startswith("bash(")) == 1


def test_a_cursor_sessions_cant_trust_restarts_cleanly_and_nothing_doubles():
    t = claude()
    t.user("before the reset")
    t.write()
    run()
    before = index_rows()
    with index.writer() as w:
        c = w.connect()
        c.execute("UPDATE sources SET cursor = 'not-a-cursor', reuse = NULL")
        c.close()
    t.user("after the reset")
    t.flush()
    run()
    after = index_rows()
    assert set(before) < set(after) and len(after) == len(before) + 1


def test_a_held_writer_lock_makes_a_child_exit_at_once():
    import subprocess

    import moxi.paths
    from moxi import files

    claude().write()
    with files.lock(index.writer_lock_path(), timeout_s=0):
        began = time.monotonic()
        done = subprocess.run([str(moxi.paths.script(start.SCRIPT)), "--walk"],
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 0 and "another ingest is running" in done.stdout
        assert time.monotonic() - began < 10
    assert index.current() is None


# -- redaction -------------------------------------------------------------------------

def test_a_credential_is_a_placeholder_in_every_kind_of_row():
    t = claude()
    t.user(f"my token is {TOKEN}")
    call = t.request().tool_use("Bash", {"command": f"curl -H {TOKEN} x"})
    t.tool_result(call, f"echoed {TOKEN}")
    t.write()
    run()
    for text in texts() + texts("tails"):
        assert TOKEN[:12] not in text
    assert any("[REDACTED:" in x for x in texts("tails"))


@pytest.mark.parametrize("where", ["head", "tail"])
def test_a_40_character_token_cut_at_the_limit_leaves_no_unredacted_part(where):
    sets = ("credentials", "broad")
    if where == "head":
        text = "w " * ((rows.MESSAGE_CHARS - 20) // 2) + TOKEN + " end"
        kept = rows.keep_head(text, rows.MESSAGE_CHARS, sets)
    else:
        text = "start " + TOKEN + " w" * 495
        kept = rows.keep_tail(text, 1000, sets)
    assert "characters cut" in kept
    for i in range(0, 40, 8):
        assert TOKEN[i:i + 12] not in kept


def test_an_unscanned_text_is_stored_as_its_placeholder(monkeypatch):
    import moxi_secrets
    monkeypatch.setattr(moxi_secrets, "redact",
                        lambda text, sets: "x [REDACTED:unscanned] y")
    assert rows.keep_head("anything", 100, ("credentials",)) == rows.UNSCANNED


@pytest.mark.parametrize("tool_input", [
    {"file_path": "/work/project/.env"}, {"filePath": "/work/project/.env"},
    {"command": "cat .env | head"}, {"cmd": ["cat", "/work/.env"]},
    json.dumps({"cmd": ["cat", ".env"]})])
def test_a_secret_path_call_keeps_its_summary_line_only_and_drops_its_tail(tool_input):
    t = claude()
    call = t.request().tool_use("Read", tool_input)
    t.tool_result(call, "SECRET_VALUE=hunter2")
    t.write()
    run()
    (text,) = texts()
    assert text.startswith("Read(") and ".env" in text and "\n" not in text
    assert texts("tails") == []


PLAIN = "hunter2-plain"         # a password no redaction set catches


@pytest.mark.parametrize("command, path", [
    (f"cat > /work/app/.env <<EOF\nDB_PASS={PLAIN}\nEOF", "/work/app/.env"),
    (f"cat ~/.ssh/id_rsa && echo {PLAIN}", "~/.ssh/id_rsa"),
    (f"echo PASS={PLAIN}>/work/app/.env", "/work/app/.env"),
    (f"printf '{{\"path\":\"/work/app/.env\",\"content\":\"DB_PASS={PLAIN}\"}}'",
     "/work/app/.env"),
    (f"printf '{{\"content\":\"DB_PASS={PLAIN}\",\"path\":\"/work/app/.env\"}}'",
     "/work/app/.env"),
    (f"tool --out=/work/app/.env,DB_PASS:{PLAIN}", "/work/app/.env"),
    (f"cat /work/app/.env:{PLAIN}", "/work/app/.env"),
    (f"cat /work/app/.env\x00{PLAIN}", "/work/app/.env")])
def test_a_secret_path_command_with_more_text_keeps_only_the_path(command, path):
    t = claude()
    call = t.request().tool_use("Bash", {"command": command, "description": "x"})
    t.tool_result(call, "")
    t.write()
    run()
    assert texts() == [f"Bash({path})"]


def test_a_patch_to_an_env_file_keeps_only_the_path():
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    c = r.tool_call("apply_patch", "*** Begin Patch\n*** Update File: /work/app/.env\n@@\n"
                    f"-ADMIN_PASS=old\n+ADMIN_PASS={PLAIN}\n*** End Patch\n")
    r.tool_output(c, f"Success. Updated /work/app/.env with {PLAIN}")
    r.write()
    run()
    assert texts() == ["apply_patch(/work/app/.env)"] and texts("tails") == []


@pytest.mark.parametrize("value", [
    "cat > /x/.env <<EOF\nA=1\nEOF", "cat ~/.ssh/id_rsa && echo done",
    ["bash", "-lc", "cat /x/.env | head -n 3"], "*** Update File: /x/.env\n+A=1",
    {"a": ["not this", {"b": "then /x/.env with words"}]}])
def test_the_path_found_is_one_word(value):
    found = rows.secret_path(value)
    assert found is not None and found.split() == [found]


def _runs(count, size, seed):
    """`count` random letter-and-digit runs of `size`, which redaction turns into short
    placeholders."""
    import random
    import string

    rng = random.Random(seed)
    return " ".join("".join(rng.choice(string.ascii_letters + string.digits)
                            for _ in range(size)) for _ in range(count))


@pytest.mark.parametrize("where", ["head", "tail"])
def test_redaction_that_shrinks_the_window_does_not_pull_a_token_into_the_cut(where):
    sets = ("credentials", "broad")
    blob = _runs(500, 200, 5)
    if where == "head":
        edge = rows.MESSAGE_CHARS + rows.MARGIN
        text = blob[:edge - 21] + " " + TOKEN + " plain words" * 20
        kept = rows.keep_head(text, rows.MESSAGE_CHARS, sets)
        assert kept == rows.redact(text, sets)        # the redacted whole fits
    else:
        edge = 1000 + rows.MARGIN
        text = "plain words " * 10 + TOKEN + " " + blob[:edge - 21]
        kept = rows.keep_tail(text, 1000, sets)
        whole = rows.redact(text, sets)
        assert kept == f"[... {len(whole) - 1000} earlier characters cut] {whole[-1000:]}"
    for i in range(0, 31):
        assert TOKEN[i:i + 10] not in kept


def test_a_cut_of_a_long_shrinking_text_keeps_the_whole_limit():
    sets = ("credentials", "broad")
    text = _runs(400, 200, 6) + " plain words" * 20000
    kept = rows.keep_head(text, rows.MESSAGE_CHARS, sets)
    body, marker = kept.rsplit(" [... ", 1)
    assert len(body) == rows.MESSAGE_CHARS and body == rows.redact(text, sets)[:len(body)]
    assert marker.endswith(" more characters cut]")


# -- the session table ----------------------------------------------------------------

def test_a_child_writes_label_newest_and_settles_only_after_a_model_answer(tmp_path):
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    t = claude(cwd=str(project))
    t.user("a prompt")
    t.write()
    fake = launcher()
    run(launcher=fake)
    sc = state.connect()
    row = table.row(sc, "claude", t.session_id)
    assert row.label == "proj" and not row.settled and row.newest > 0
    t.assistant("an answer")
    t.flush()
    run("turn", "claude", t.session_id, launcher=fake)
    row = table.row(sc, "claude", t.session_id)
    assert row.settled and row.kind is None and t.session_id in fake.api.asked


def test_kinds_scratch_none_failing_and_no_interface():
    sids = [f"0190b000-0000-7000-8000-0000000000{n}" for n in (10, 11, 12, 13)]

    def session(n, sid):
        t = claude(sid, serial=100 * (n + 1))
        t.user("q")
        t.assistant("a")
        t.write()

    session(0, sids[0])
    session(1, sids[1])
    sc = state.connect()
    run(launcher=launcher({sids[0]: "scratch"}), config=config(harnesses=["claude"]))
    assert table.row(sc, "claude", sids[0]).kind == "scratch"
    assert table.row(sc, "claude", sids[1]).settled
    listed = {x.session_id for x in table.project_sessions(sc, "project", ("c", "x"))}
    assert listed == {sids[1]}
    session(2, sids[2])
    session(3, sids[3])
    run("turn", "claude", sids[2], launcher=launcher(fail=True))
    assert not table.row(sc, "claude", sids[2]).settled
    run("turn", "claude", sids[3], launcher=NO_LAUNCHER)
    row = table.row(sc, "claude", sids[3])
    assert row.settled and row.kind is None
    run("turn", "claude", sids[2], launcher=launcher())
    assert table.row(sc, "claude", sids[2]).settled


# -- the rebuild ---------------------------------------------------------------------

def corpus():
    t = claude()
    t.user("alpha prompt")
    call = t.request().tool_use("Bash", {"command": "ls"})
    t.tool_result(call, "x" * 1500)
    t.assistant("alpha answer")
    sub = t.subagent()
    sub.user("sub work")
    t.write()
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    r.user("beta prompt")
    r.write()
    return t, r


def test_a_rebuild_equals_a_fresh_ingest_by_key_and_text_and_starts_a_walk():
    corpus()
    run()
    fresh = (index_rows(), index_rows("tails"))
    walks = []
    report = run("rebuild", then=walks.append)
    assert walks == [["--walk"]] and report.dropped == 0
    assert (index_rows(), index_rows("tails")) == fresh


def test_a_rebuild_with_subagents_off_and_tails_lowered():
    corpus()
    run("all", config=config(**{"index.include_subagents": True}))
    assert any(r[3] for r in index_rows())
    run("rebuild", config=config(**{"index.tail_chars": 200}))
    assert not any(r[3] for r in index_rows())
    assert all(len(r[-1]) <= 200 + len("[... 1300 earlier characters cut] ")
               for r in index_rows("tails"))


def test_a_rebuild_drops_gone_sessions_and_keeps_labels_and_relabel_rederives(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    t = claude(cwd=str(repo))
    t.user("labelled prompt")
    t.write()
    gone = claude("0190b000-0000-7000-8000-00000000dead", serial=500,
                  cwd=str(tmp_path / "nowhere"))
    gone.user("a deleted transcript")
    gone.write()
    sc = state.connect()
    run()
    assert table.row(sc, "claude", t.session_id).label == "repo"
    table.relabel(sc, "claude", t.session_id, "kept-label")
    os.remove(gone.path)
    report = run("rebuild")
    assert report.dropped == 1 and table.row(sc, "claude", gone.session_id) is None
    assert "a deleted transcript" not in texts()
    assert table.row(sc, "claude", t.session_id).label == "kept-label"
    with index.open_read() as rd:
        assert index.meta_get(rd.conn, "dropped") == "1"
        assert {r[0] for r in rd.conn.execute("SELECT label FROM rows")} == {"kept-label"}
    run("relabel")
    row = table.row(sc, "claude", t.session_id)
    assert row.label == "repo"
    with index.open_read() as rd:
        assert {r[0] for r in rd.conn.execute("SELECT label FROM rows")} == {"repo"}


def test_a_rebuild_keeps_the_table_rows_of_a_harness_config_leaves_out():
    corpus()
    run()
    sc = state.connect()
    table.write_start(sc, "codex", "codex-session", "codex-label", now=1.0)
    table.write_identity(sc, "codex", "codex-session", "planner", now=1.0)
    report = run("rebuild", config=config(harnesses=["claude"]))
    assert report.dropped == 0
    row = table.row(sc, "codex", "codex-session")
    assert row is not None and row.identity == "planner"
    with index.open_read() as rd:
        assert index.meta_get(rd.conn, "dropped") == "0"


# -- a first full read and the roots ---------------------------------------------------

def test_a_rebuild_marks_its_first_read_finished_only_when_every_root_was_listed(
        monkeypatch):
    corpus()
    run()
    real = api.corpus

    def corpus_failing(harness, **kw):
        if harness == "codex":
            raise OSError("the root can't be listed")
        return real(harness, **kw)

    monkeypatch.setattr(api, "corpus", corpus_failing)
    assert os.path.lexists(api.root("codex"))
    run("rebuild")
    with index.open_read() as rd:
        assert index.meta_get(rd.conn, "first_read") == "0"
    monkeypatch.setattr(api, "corpus", real)
    run()                                             # a walk that lists it finishes it
    with index.open_read() as rd:
        assert index.meta_get(rd.conn, "first_read") == "1"
    run("rebuild")                                    # every root listed
    with index.open_read() as rd:
        assert index.meta_get(rd.conn, "first_read") == "1"


# -- requests: a walk, full read, rebuild or relabel someone asked for ------------------

def waiting():
    sc = state.connect()
    try:
        return state.read_request(sc)
    finally:
        sc.close()


def ask(kind, asked=None):
    sc = state.connect()
    try:
        state.ask(sc, kind, asked)
    finally:
        sc.close()


def during(monkeypatch, action):
    """Run `action` once, while the next child holds the writer lock, as another child
    started meanwhile would."""
    real = ingest.Ingest.__init__
    done = []

    def init(self, *a, **kw):
        if not done:
            done.append(None)
            done[0] = action()
        real(self, *a, **kw)

    monkeypatch.setattr(ingest.Ingest, "__init__", init)
    return done


def generation():
    return index.current()


def other(mode, *, asked=False):
    """Run a child of `mode` in another process, as a child started meanwhile is, and
    return its report as (busy, idle), and what waits in the store after it."""
    import subprocess
    import sys

    code = ("import sys\nfrom moxi_memory import ingest, links\n"
            "r = ingest.run(sys.argv[1], asked=sys.argv[2] == '1', then=lambda a: None, "
            "launcher=links.Link(links.NONE))\nprint(int(r.busy), int(r.idle))\n")
    done = subprocess.run([sys.executable, "-c", code, mode, "1" if asked else "0"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    busy, idle = (bool(int(x)) for x in done.stdout.split())
    return busy, idle, waiting()


@pytest.mark.parametrize("holder", ["turn", "walk", "rebuild"])
@pytest.mark.parametrize("kind", ["rebuild", "relabel", "all", "walk"])
def test_a_request_that_finds_the_lock_held_is_started_by_the_holder(monkeypatch, holder,
                                                                     kind):
    t, _ = corpus()
    run()
    before = generation()
    done = during(monkeypatch, lambda: other(kind, asked=True))
    started = []
    run(holder, "claude", t.session_id, then=started.append)
    assert done[0][0]                               # it found the lock held
    assert started == [["--waiting"]]               # in place of a rebuild's own walk
    assert waiting().kind == kind
    report = run("waiting", then=started.append)
    assert report.mode == kind and not report.idle and waiting() is None
    held_by_rebuild = 1 if holder == "rebuild" else 0
    if kind in ("rebuild", "relabel"):
        assert generation() == before + held_by_rebuild + 1
        assert started[1:] == [["--walk"]]
    else:
        assert generation() == before + held_by_rebuild
        assert started[1:] == []


def test_requests_merge_to_the_highest_kind_and_the_earliest_time():
    ask("rebuild", 200.0)
    ask("rebuild", 100.0)
    assert waiting() == state.Request("rebuild", 100.0)
    ask("relabel", 300.0)
    assert waiting() == state.Request("relabel", 100.0)
    ask("rebuild", 50.0)                    # a rebuild asked while a relabel waits
    ask("all", 400.0)
    ask("walk", 500.0)
    assert waiting() == state.Request("relabel", 50.0)
    sc = state.connect()
    try:
        assert state.take_request(sc) == state.Request("relabel", 50.0)
        assert state.take_request(sc) is None
    finally:
        sc.close()
    ask("walk", 1.0)
    ask("all", 2.0)                          # a full read asked while a walk waits
    assert waiting() == state.Request("all", 1.0)


def test_requests_asked_at_once_from_many_processes_keep_the_highest_kind():
    import subprocess
    import sys

    state.connect().close()       # the store is made once, before the children ask
    code = ("import sys\nfrom moxi_memory import state\nc = state.connect()\n"
            "for _ in range(50):\n    state.ask(c, sys.argv[1])\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, kind])
             for kind in ("rebuild", "relabel", "rebuild", "all", "walk", "rebuild")]
    assert [p.wait(timeout=60) for p in procs] == [0] * len(procs)
    assert waiting().kind == "relabel"


def test_a_child_that_takes_the_lock_and_finds_nothing_waiting_does_nothing(monkeypatch):
    corpus()
    run()
    before = generation()
    real = index.writer

    def writer(env=None, **kw):
        # another child ran this request between its write and its try
        ctx = real(env, **kw)
        sc = state.connect(env)
        try:
            state.take_request(sc)
        finally:
            sc.close()
        return ctx

    monkeypatch.setattr(index, "writer", writer)
    started = []
    report = run("rebuild", then=started.append)
    assert report.idle and report.line().startswith("nothing waiting")
    assert generation() == before and started == []
    assert run("waiting", then=started.append).idle and started == []


def test_a_request_written_after_the_take_keeps_waiting_and_is_started_after(monkeypatch):
    corpus()
    run()
    during(monkeypatch, lambda: ask("relabel"))
    started = []
    report = run("rebuild", then=started.append)
    assert report.mode == "rebuild"
    assert waiting().kind == "relabel" and started == [["--waiting"]]


def test_a_waiting_child_that_finds_the_lock_held_exits_and_records_nothing(monkeypatch):
    t, _ = corpus()
    run()
    ask("all", 10.0)
    done = during(monkeypatch, lambda: other("waiting"))
    started = []
    run("turn", "claude", t.session_id, then=started.append)
    busy, _, seen = done[0]
    assert busy and seen == state.Request("all", 10.0)
    assert started == [["--waiting"]]


def test_a_request_left_by_a_killed_holder_is_started_by_the_next_holder():
    t, _ = corpus()
    run()
    ask("rebuild")        # its child failed its try; the holder was killed before its read
    started = []
    report = run("turn", "claude", t.session_id, then=started.append)
    assert not report.busy and started == [["--waiting"]]
    assert waiting().kind == "rebuild"              # a turn child never clears it


def test_a_child_that_stops_on_an_error_after_its_take_still_starts_the_waiting_one(
        monkeypatch):
    corpus()
    run()

    def broken(self, **kw):
        ask("relabel")
        raise RuntimeError("a read that stops the child")

    monkeypatch.setattr(ingest.Ingest, "walk", broken)
    started = []
    with pytest.raises(RuntimeError):
        run("rebuild", then=started.append)
    assert started == [["--waiting"]] and waiting().kind == "relabel"


def test_the_tidy_leaves_a_waiting_request_and_records_the_killed_rebuild():
    from moxi import files

    t, _ = corpus()
    run()
    with files.lock(index.rebuild_lock_path(), timeout_s=0):
        pass                                    # free, as a killed rebuild leaves it
    sc = state.connect()
    try:
        state.write_entry(sc, 1234.0, 1)
        state.ask(sc, "relabel", 1300.0)
        started = []
        run("turn", "claude", t.session_id, then=started.append)
        assert state.read_entry(sc) is None
        assert state.read_unfinished(sc) == 1234.0
        assert state.read_request(sc) == state.Request("relabel", 1300.0)
        assert started == [["--waiting"]]
        run("waiting")
        assert state.read_unfinished(sc) is None   # a rebuild has finished since
    finally:
        sc.close()


def test_a_trigger_walk_records_nothing_and_an_asked_walk_does(monkeypatch):
    t, _ = corpus()
    run()
    done = during(monkeypatch, lambda: (other("walk"), other("walk", asked=True)))
    run("turn", "claude", t.session_id)
    (busy, _, seen), (asked_busy, _, asked_seen) = done[0]
    assert busy and seen is None
    assert asked_busy and asked_seen.kind == "walk"
    with pytest.raises(SystemExit):
        ingest.main(["--rebuild", "--asked"])


# -- the starters ----------------------------------------------------------------------

def test_starters_return_before_their_child_finishes(tmp_path, monkeypatch):
    done = tmp_path / "done"
    script = tmp_path / "slow-child"
    script.write_text(f"#!/bin/sh\nsleep 2\necho \"$@\" > {done}\n")
    script.chmod(0o755)
    import moxi.paths
    monkeypatch.setattr(moxi.paths, "script", lambda name: script)
    for go in (start.start_walk, start.start_all, start.start_rebuild):
        began = time.monotonic()
        go()
        assert time.monotonic() - began < 1 and not done.exists()
    wait_for(done.exists, timeout=10)


def test_a_turn_starts_at_most_one_child_per_session_per_minute(monkeypatch):
    started = []
    monkeypatch.setattr(start, "spawn", lambda args: started.append(args) or 1)
    assert start.start_turn("claude", "s1", now=1000.0) == 1
    assert start.start_turn("claude", "s1", now=1030.0) is None
    assert start.start_turn("claude", "s2", now=1030.0) == 1
    assert start.start_turn("claude", "s1", now=1061.0) == 1
    assert start.start_turn("claude", "../x") is None
    assert started[0] == ["--turn", "claude", "s1"]


def test_the_real_child_ingests_detached():
    t = claude()
    t.user("detached child text")
    t.write()
    start.start_walk()
    wait_for(lambda: index.current() is not None and "detached child text" in texts(),
             timeout=30)


# -- the version rebuild: rows stored under an older redact ------------------------------

#: A token with the allow mark beside it, and one split by a zero-width space: rows an
#: older redact stored whole.
MARKED = f"marked {TOKEN}  # gitleaks:allow"
SPLIT = f"split {TOKEN[:12]}​{TOKEN[12:]}"
#: Pieces of the token that no stored text may hold once it is redacted again.
PIECES = (TOKEN[4:12], TOKEN[12:])
SUBAGENTS = {"index.include_subagents": True}
CHILD = "0190c000-0000-7000-8000-0000000c0de9"
GONE = "0190b000-0000-7000-8000-00000000dead"


def old_way(planted):
    """Store each row and tail that begins with a word of `planted` as its raw text, and
    take `redact_version` out of the generation's meta: a generation an older redact
    stored."""
    with index.writer() as w:
        conn = w.connect()
        try:
            for word, raw in planted.items():
                hit = 0
                for table_name in ("rows", "tails"):
                    hit += conn.execute(f"UPDATE {table_name} SET text = ? WHERE text LIKE ?",
                                        (raw, f"{word} %")).rowcount
                assert hit, word
            conn.execute("DELETE FROM meta WHERE key = ?", (index.REDACT_VERSION_KEY,))
        finally:
            conn.close()
    with index.open_read() as rd:
        assert index.stored_redact_version(rd.conn) == 1


def sources():
    with index.open_read() as rd:
        return set(rd.conn.execute("SELECT harness, source FROM sources"))


def redacted_again(report, before, words):
    """What every version rebuild gives: no session dropped, the version recorded, every
    old source still there, and each planted row searchable with placeholders and no
    piece of the token."""
    from moxi_memory import search

    assert report.dropped == 0
    with index.open_read() as rd:
        assert index.stored_redact_version(rd.conn) == rows.redact_version() == 2
        assert index.meta_get(rd.conn, ingest.REPLACED_KEY) == "1"
    assert before <= sources()
    stored = texts() + texts("tails")
    assert not any(piece in text for piece in PIECES for text in stored)
    for word in words:
        assert any(text.startswith(word) and "[REDACTED:" in text for text in stored), word
        hits = search.search(word, results=True, subagents=True).hits
        assert hits, word
    for piece in PIECES:
        assert not search.search(piece, results=True, subagents=True).hits


def row_of(word):
    return next(text for text in texts() + texts("tails") if text.startswith(word))


def test_a_version_rebuild_keeps_a_gone_subagents_rows_and_reads_its_main_again():
    on = config(**SUBAGENTS)
    t = claude()
    t.user(f"pelican {MARKED}")
    sub = t.subagent()
    sub._count = 100
    sub.user(f"heron {SPLIT}")
    call = sub.request().tool_use("Read", {"file_path": "/work/project/.env"})
    sub.tool_result(call, "A=1")
    t.write()
    run(config=on)
    calls = {("claude", str(sub.path))}
    with index.open_read() as rd:
        assert calls <= set(rd.conn.execute("SELECT harness, source FROM secret_calls"))
    old_way({"pelican": f"pelican {MARKED}", "heron": f"heron {SPLIT}"})
    before = sources()
    os.remove(sub.path)

    report = run("rebuild", config=on)
    redacted_again(report, before, ("pelican", "heron"))
    assert row_of("pelican") == rows.redact(f"pelican {MARKED}", ("credentials", "broad"))
    with index.open_read() as rd:
        assert calls <= set(rd.conn.execute("SELECT harness, source FROM secret_calls"))
        agents = {r[0] for r in rd.conn.execute(
            "SELECT agent_id FROM rows WHERE text LIKE 'heron %'")}
    assert agents == {"a0190b0000000001"}


def test_a_version_rebuild_keeps_a_gone_codex_parent_and_reads_its_spawned_thread():
    on = config(**SUBAGENTS)
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    r.user(f"osprey {MARKED}")
    c = r.tool_call("exec", "ls")
    r.tool_output(c, f"kestrel {SPLIT}")
    child = r.spawn(CHILD, "explore")
    child.turn()
    child.user(f"plover {SPLIT}")
    r.write()
    child.write()
    run(config=on)
    old_way({"osprey": f"osprey {MARKED}", "kestrel": f"kestrel {SPLIT}",
             "plover": f"plover {SPLIT}"})
    before = sources()
    os.remove(r.path)

    report = run("rebuild", config=on)
    redacted_again(report, before, ("osprey", "kestrel", "plover"))
    assert row_of("plover") == rows.redact(f"plover {SPLIT}", ("credentials", "broad"))
    assert table.row(state.connect(), "codex", r.thread_id) is not None


def test_a_version_rebuild_keeps_what_no_source_read_to_its_end_gives():
    t = claude()
    t.user("live prompt")
    sub = t.subagent()
    sub._count = 100
    sub.user(f"heron {SPLIT}")
    t.write()
    gone = claude(GONE, serial=500)
    gone.user(f"egret {SPLIT}")
    gone.write()
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    r.user(f"osprey {MARKED}")
    r.write()
    run(config=config(**SUBAGENTS))
    old_way({"heron": f"heron {SPLIT}", "egret": f"egret {SPLIT}",
             "osprey": f"osprey {MARKED}"})
    before = sources()
    os.remove(gone.path)

    # codex left out by `harnesses`, subagents off, one session wholly gone
    report = run("rebuild", config=config(harnesses=["claude", "opencode"]))
    redacted_again(report, before, ("heron", "egret", "osprey"))
    sc = state.connect()
    for harness, sid in (("claude", gone.session_id), ("codex", r.thread_id),
                         ("claude", t.session_id)):
        assert table.row(sc, harness, sid) is not None, sid


def test_a_version_rebuild_keeps_the_rows_of_a_root_that_couldnt_be_listed(monkeypatch):
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    r.user(f"osprey {MARKED}")
    r.write()
    t = claude()
    t.user(f"pelican {SPLIT}")
    t.write()
    run()
    old_way({"osprey": f"osprey {MARKED}", "pelican": f"pelican {SPLIT}"})
    before = sources()
    real = api.corpus

    def corpus_failing(harness, **kw):
        if harness == "codex":
            raise OSError("the root can't be listed")
        return real(harness, **kw)

    monkeypatch.setattr(api, "corpus", corpus_failing)
    report = run("rebuild")
    redacted_again(report, before, ("osprey", "pelican"))
    assert table.row(state.connect(), "codex", r.thread_id) is not None


# -- the trim of a stored cut ------------------------------------------------------------

SETS = ("credentials", "broad")


def head_cut(body, gone=500):
    return f"{body} [... {gone} more characters cut]"


def tail_cut(body, gone=500):
    return f"[... {gone} earlier characters cut] {body}"


def test_a_kept_head_cut_loses_the_run_at_its_cut_and_counts_it():
    fragment = TOKEN[:30]
    body = "some words before it " * 6 + fragment
    got = rows.recut_head(head_cut(body), rows.MESSAGE_CHARS, SETS,
                          edge_chars=rows.EDGE_CHARS)
    assert fragment not in got and TOKEN[:12] not in got
    kept, gone = re.fullmatch(r"(.*) \[\.\.\. ([0-9]+) more characters cut\]", got,
                              re.S).groups()
    assert body.startswith(kept) and int(gone) == 500 + len(body) - len(kept)
    # the whitespace it stops at is at least 64 characters from the cut
    assert len(body) - len(kept) >= rows.EDGE_CHARS


def test_a_kept_tail_cut_loses_its_first_run_and_counts_it():
    fragment = TOKEN[10:]
    body = fragment + " and some words after it" * 6
    got = rows.recut_tail(tail_cut(body), 1000, SETS, edge_chars=rows.EDGE_CHARS)
    assert fragment not in got
    gone, kept = re.fullmatch(r"\[\.\.\. ([0-9]+) earlier characters cut\] (.*)", got,
                              re.S).groups()
    assert body.endswith(kept) and int(gone) == 500 + len(body) - len(kept)


def test_a_token_split_by_a_control_python_reads_as_whitespace_loses_its_stored_part():
    body = "words before " + "A" * 70 + "\x1f" + "B" * 70
    got = rows.recut_head(head_cut(body), rows.MESSAGE_CHARS, SETS,
                          edge_chars=rows.EDGE_CHARS)
    assert "A" not in got.replace("[... ", "") and got.startswith("words before")
    got = rows.recut_tail(tail_cut("A" * 70 + "\x1f" + "B" * 70 + " words after"), 1000,
                          SETS, edge_chars=rows.EDGE_CHARS)
    assert got.endswith(" words after") and "B" not in got


def test_the_span_stops_at_a_plain_space_64_characters_out_and_see_through_counts_nothing():
    got = rows.recut_head(head_cut("keep " + "y" * 70), rows.MESSAGE_CHARS, SETS,
                          edge_chars=rows.EDGE_CHARS)
    assert got == "keep [... 571 more characters cut]"
    # 61 counted characters lie between the cut and the space before "a": the 40
    # zero-width spaces count nothing, so the span runs to the text's start
    body = "first " + "a" * 30 + " " + "b" * 30 + "​" * 40
    got = rows.recut_head(head_cut(body), rows.MESSAGE_CHARS, SETS,
                          edge_chars=rows.EDGE_CHARS)
    assert got == f" [... {500 + len(body)} more characters cut]"
    # a text with no stored cut, and a recut with no edge, are not trimmed
    assert rows.recut_head(body, rows.MESSAGE_CHARS, SETS,
                           edge_chars=rows.EDGE_CHARS) == body
    assert rows.recut_head(head_cut(body), rows.MESSAGE_CHARS, SETS) == head_cut(body)


def test_a_version_rebuild_trims_a_kept_cut_row_and_not_a_row_read_again():
    long = "word " * 4000
    t = claude()
    t.user(f"pelican {long}")
    t.write()
    gone = claude(GONE, serial=500)
    gone.user(f"egret {long}")
    gone.write()
    run()
    read_before = row_of("pelican")
    assert read_before.endswith("more characters cut]")
    planted = head_cut("egret " + "word " * 40 + TOKEN[:30])
    old_way({"egret": planted})
    os.remove(gone.path)
    run("rebuild")
    assert row_of("pelican") == read_before
    egret = row_of("egret")
    assert TOKEN[:12] not in egret and egret.startswith("egret word")
    gone_count = int(re.search(r"([0-9]+) more characters cut", egret).group(1))
    assert gone_count > 500


# -- once, and by hand -----------------------------------------------------------------

def test_an_ingest_into_an_old_generation_leaves_its_rows_and_starts_no_rebuild():
    t = claude()
    t.user(f"pelican {MARKED}")
    t.write()
    run()
    old_way({"pelican": f"pelican {MARKED}"})
    new = claude("0190b000-0000-7000-8000-00000000c1a1", serial=200)
    new.user(f"heron {SPLIT}")
    new.write()
    started = []
    report = run(then=started.append)
    assert report.dropped is None and started == []
    assert row_of("pelican") == f"pelican {MARKED}"
    assert not any(piece in row_of("heron") for piece in PIECES)
    with index.open_read() as rd:
        assert index.stored_redact_version(rd.conn) == 1
    assert state.read_request(state.connect()) is None


def test_the_version_rebuild_runs_once_and_a_failed_one_leaves_the_old_generation(
        monkeypatch):
    t = claude()
    t.user(f"pelican {MARKED}")
    t.write()
    gone = claude(GONE, serial=500)
    gone.user(f"egret {SPLIT}")
    gone.write()
    run()
    old_way({"pelican": f"pelican {MARKED}", "egret": f"egret {SPLIT}"})
    os.remove(gone.path)
    current = index.current()

    def raises(self):
        raise RuntimeError("the disk is full")

    with monkeypatch.context() as patched:
        patched.setattr(ingest.Ingest, "copy_unread", raises)
        with pytest.raises(RuntimeError):
            run("rebuild")
    assert index.current() == current
    with index.open_read() as rd:
        assert index.stored_redact_version(rd.conn) == 1
    started = []
    run(then=started.append)
    assert started == [] and state.read_request(state.connect()) is None

    report = run("rebuild")                 # the next typed reindex is a version rebuild
    assert report.dropped == 0 and "egret" in " ".join(texts())
    read_once = row_of("pelican")
    report = run("rebuild")                 # and the one after it is an ordinary rebuild
    assert report.dropped == 1 and not any(x.startswith("egret") for x in texts())
    assert row_of("pelican") == read_once
    with index.open_read() as rd:
        assert index.meta_get(rd.conn, ingest.REPLACED_KEY) is None
        assert index.stored_redact_version(rd.conn) == 2
