"""The pass: lanes, reuse, reads, holds, gone sources, the import skip, project keys,
the lock, rebuilds and cancellation. After every pass a test makes, the stored claims
agree with the `claimed` view."""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import moxi.files
from moxi_sessions import api as sessions
from moxi_usage import cli, index, store

from conftest import write_config

ClaudeTranscript = sessions.testing.ClaudeTranscript
CodexRollout = sessions.testing.CodexRollout
OpenCodeDb = sessions.testing.OpenCodeDb
usage = sessions.testing.usage
token_usage = sessions.testing.token_usage
write_imports = sessions.testing.write_imports

NOW = 1793400000.0


class Readings:
    """Stands for the readings store: keeps what the pass hands it."""

    def __init__(self):
        self.added: list = []
        self.pruned: list[float] = []

    def add_codex(self, records, *, now, env=None):
        records = list(records)
        self.added.extend(records)
        return len(records)

    def prune(self, *, now, env=None):
        self.pruned.append(now)
        return 0


class Projects:
    """Stands for projects' API: `for_path` answers from a table of roots, and every
    path it is asked about is kept."""

    class Unreadable(Exception):
        pass

    class Project:
        def __init__(self, key):
            self.key = key

    def __init__(self, roots):
        self.roots = dict(roots)
        self.asked: list[str] = []

    def for_path(self, path):
        self.asked.append(path)
        for root, key in self.roots.items():
            if path == root or path.startswith(root.rstrip("/") + "/"):
                return self.Project(key)
        return None


@pytest.fixture
def readings():
    return Readings()


@pytest.fixture
def roots():
    return {name: Path(sessions.root(name)) for name in ("claude", "codex", "opencode")}


def run(readings, **kw):
    kw.setdefault("projects", None)
    kw.setdefault("now", NOW)
    kw.setdefault("workers", 1)
    result = index.pass_(readings=readings, **kw)
    conn = store.connect()
    try:
        assert store.check(conn) == []
    finally:
        conn.close()
    return result


def rows(sql, *args):
    conn = store.connect()
    try:
        return [dict(row) for row in conn.execute(sql, args)]
    finally:
        conn.close()


def files(lane=None):
    if lane is None:
        return rows("SELECT * FROM files ORDER BY lane, path, part")
    return rows("SELECT * FROM files WHERE lane = ? ORDER BY path, part", lane)


def requests(path=None):
    sql = ("SELECT f.lane, f.path, f.part, r.key, r.seq, r.ts, r.model, r.input, "
           "r.cache_read, r.cache_write, r.output, r.won FROM requests r "
           "JOIN files f ON f.id = r.file_id")
    if path is not None:
        return rows(sql + " WHERE f.path = ? ORDER BY r.seq, r.key", str(path))
    return rows(sql + " ORDER BY f.lane, f.path, f.part, r.seq, r.key")


def won():
    return rows("SELECT key, output FROM requests WHERE won = 1 ORDER BY key")


def transcript(root, session, **kw):
    t = ClaudeTranscript(root, session_id=session, **kw)
    t.user("go")
    t.assistant("one", usage=usage(output=11))
    t.user("more")
    t.assistant("two", usage=usage(output=22))
    return t


SESSION_A = "0190b000-0000-7000-8000-0000000000a1"
SESSION_B = "0190b000-0000-7000-8000-0000000000b2"
THREAD_A = "0190c000-0000-7000-8000-00000000c0a1"
THREAD_B = "0190c000-0000-7000-8000-00000000c0b2"


# --- the claude lane --------------------------------------------------------------------

def test_a_claude_session_its_subagent_and_workflow_agent_are_indexed(readings, roots):
    t = transcript(roots["claude"], SESSION_A)
    agent = t.subagent("a01")
    agent.user("look")
    agent.assistant("found", usage=usage(output=5), request_id="req_agent_1")
    flow = t.subagent("a02", workflow="wf_one")
    flow.user("step")
    flow.assistant("did", usage=usage(output=6), request_id="req_flow_1")
    t.title("the title")
    t.write()

    result = run(readings)

    assert result.read == 3 and result.reused == 0
    by_kind = {row["kind"]: row for row in files("claude")}
    assert set(by_kind) == {"main", "subagent", "workflow"}
    main = by_kind["main"]
    assert main["session_id"] == SESSION_A and main["agent_id"] is None
    assert main["origin"] == "cli" and main["cwd"] == "/work/project"
    assert main["title"] == "the title" and main["rank"] == 0
    assert main["first_ts"] is not None and main["last_ts"] >= main["first_ts"]
    assert by_kind["subagent"]["agent_id"] == "a01"
    assert by_kind["subagent"]["parent_session"] == SESSION_A
    assert sorted(r["output"] for r in won()) == [5, 6, 11, 22]
    assert readings.pruned == [NOW]


def test_an_unchanged_file_is_reused_and_a_grown_one_is_read_from_its_cursor(readings,
                                                                             roots):
    t = transcript(roots["claude"], SESSION_A)
    t.write()
    run(readings)
    again = run(readings)
    assert (again.read, again.reused) == (0, 1)

    t.user("and more")
    t.assistant("three", usage=usage(output=33))
    t.flush()
    grown = run(readings)
    assert (grown.read, grown.reused) == (1, 0)
    assert sorted(r["output"] for r in won()) == [11, 22, 33]


def test_a_record_that_comes_twice_leaves_the_index_as_one_copy_would(readings, roots):
    t = ClaudeTranscript(roots["claude"], session_id=SESSION_A)
    t.user("go")
    request = t.request(usage_block=usage(output=7))
    request.thinking("hmm")
    t.flush(terminated=True)
    run(readings)                         # the request so far: one line of it
    request.text("done")                  # the same request, a later line of it
    t.user("next")
    t.assistant("ok", usage=usage(output=9))
    t.flush()
    run(readings)
    incremental = requests()

    run(readings, rebuild=True)
    assert requests() == incremental
    assert len({r["key"] for r in incremental}) == len(incremental) == 2


def test_a_rewritten_file_drops_its_old_rows_first(readings, roots):
    t = transcript(roots["claude"], SESSION_A)
    t.write()
    run(readings)
    t.lines = t.lines[:2]                 # the session rewritten shorter
    t.write()
    run(readings)
    assert [r["output"] for r in won()] == [11]


def test_a_deleted_live_file_loses_its_row_and_its_keys_go_to_the_other_copy(readings,
                                                                             roots):
    one = transcript(roots["claude"], SESSION_A)
    one.write()
    two = transcript(roots["claude"], SESSION_B, project="-work-other")
    two.lines = list(one.lines)           # a copy holding the same requests
    two.write()
    run(readings)
    assert len(won()) == 2 and len(requests()) == 4
    first = rows("SELECT f.path FROM requests r JOIN files f ON f.id = r.file_id "
                 "WHERE r.won = 1")[0]["path"]
    os.remove(first)
    result = run(readings)
    assert result.deleted == 1
    assert len(won()) == 2 and {r["path"] for r in requests()} != {first}


# --- the zero-entry guard and held rows -------------------------------------------------

def test_a_live_lane_that_lists_clean_and_empty_keeps_its_rows_held(readings, roots):
    t = transcript(roots["claude"], SESSION_A)
    t.write()
    run(readings)
    shutil.move(roots["claude"], roots["claude"].with_name("projects.away"))
    roots["claude"].mkdir()                       # remounted empty

    result = run(readings)

    [row] = files("claude")
    assert "listed no sources" in row["held"]
    assert result.lanes["claude"]["state"] == "held" and result.held == 1
    assert sorted(r["output"] for r in won()) == [11, 22]


def test_a_lane_whose_root_is_gone_keeps_its_rows_held_and_they_clear_when_it_returns(
        readings, roots):
    t = transcript(roots["claude"], SESSION_A)
    t.write()
    run(readings)
    away = roots["claude"].with_name("projects.away")
    shutil.move(roots["claude"], away)

    result = run(readings)
    [row] = files("claude")
    assert "could not be listed" in row["held"]
    assert result.lanes["claude"]["state"] == "offline"
    assert len(won()) == 2

    shutil.move(away, roots["claude"])            # back, every file as it was
    result = run(readings)
    assert (result.read, result.reused) == (0, 1)
    [row] = files("claude")
    assert row["held"] is None


def test_an_archive_lane_that_lists_empty_or_goes_offline_keeps_its_rows(readings,
                                                                         tmp_path):
    archive = tmp_path / "old-machine"
    transcript(archive / "projects", SESSION_A).write()
    write_config(f'[[claude.archive_roots]]\npath = "{archive}"\nid = "old"\n')
    result = run(readings)
    [row] = files("archive:old")
    assert row["rank"] == 3 and row["held"] is None and result.read == 1

    shutil.move(archive / "projects", tmp_path / "away")
    (archive / "projects").mkdir()
    run(readings)
    [row] = files("archive:old")
    assert "listed no sources" in row["held"]

    shutil.rmtree(archive)
    result = run(readings)
    [row] = files("archive:old")
    assert "could not be listed" in row["held"]
    assert result.lanes["archive:old"]["state"] == "offline"
    assert sorted(r["output"] for r in won()) == [11, 22]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory")
def test_a_source_under_an_unreadable_directory_keeps_its_row(readings, roots):
    t = transcript(roots["claude"], SESSION_A)
    t.write()
    transcript(roots["claude"], SESSION_B, project="-work-other").write()
    run(readings)
    os.chmod(t.path.parent, 0)
    try:
        result = run(readings)
    finally:
        os.chmod(t.path.parent, 0o755)
    held = {row["session_id"]: row["held"] for row in files("claude")}
    assert held[SESSION_B] is None
    assert "under an unreadable directory" in held[SESSION_A]
    assert result.unreadable and len(won()) == 2

    result = run(readings)                        # readable again, unchanged
    assert {row["held"] for row in files("claude")} == {None}


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_a_source_that_cannot_be_read_keeps_its_rows_and_its_cursor(readings, roots):
    t = transcript(roots["claude"], SESSION_A)
    t.write()
    run(readings)
    [before] = files("claude")
    t.user("more")
    t.assistant("three", usage=usage(output=33))
    t.flush()
    os.chmod(t.path, 0)
    try:
        run(readings)
    finally:
        os.chmod(t.path, 0o644)
    [row] = files("claude")
    assert row["held"].startswith("unreadable:") and row["cursor"] == before["cursor"]
    assert sorted(r["output"] for r in won()) == [11, 22]

    run(readings)
    [row] = files("claude")
    assert row["held"] is None
    assert sorted(r["output"] for r in won()) == [11, 22, 33]


def test_a_rollout_replaced_by_a_compressed_copy_keeps_its_row(readings, roots):
    r = CodexRollout(roots["codex"], thread_id=THREAD_A)
    r.turn()
    r.request(token_usage(output=40))
    path = r.write()
    run(readings)
    os.rename(path, str(path) + ".zst")

    result = run(readings)

    [row] = files("codex")
    assert row["path"] == str(path) and "which sessions cannot read" in row["held"]
    assert result.lanes["codex"]["compressed"] == 1
    assert [r["output"] for r in won()] == [40]


# --- archive lanes ----------------------------------------------------------------------

def test_an_archive_copy_of_a_live_session_is_not_read_and_its_row_goes(readings, roots,
                                                                        tmp_path):
    archive = tmp_path / "old-machine"
    transcript(archive / "projects", SESSION_A).write()
    transcript(archive / "projects", SESSION_B).write()
    write_config(f'[[claude.archive_roots]]\npath = "{archive}"\nid = "old"\n')
    run(readings)
    assert len(files("archive:old")) == 2

    transcript(roots["claude"], SESSION_A).write()
    result = run(readings)
    assert [row["session_id"] for row in files("archive:old")] == [SESSION_B]
    assert result.deleted == 1


def test_the_pass_that_sees_a_live_transcript_gone_reads_its_archive_copy(readings, roots,
                                                                         tmp_path):
    archive = tmp_path / "old-machine"
    transcript(archive / "projects", SESSION_A).write()
    live = transcript(roots["claude"], SESSION_A).write()
    other = ClaudeTranscript(roots["claude"], session_id=SESSION_B)
    other.user("go")
    other.assistant("b", usage=usage(output=33), request_id="req_b", message_id="msg_b")
    other.write()
    write_config(f'[[claude.archive_roots]]\npath = "{archive}"\nid = "old"\n')
    run(readings)
    assert files("archive:old") == []

    os.unlink(live)
    run(readings)
    [row] = files("archive:old")
    assert row["session_id"] == SESSION_A
    assert {r["lane"] for r in requests() if r["won"]} == {"claude", "archive:old"}
    assert sorted(r["output"] for r in won()) == [11, 22, 33]


def test_a_live_row_held_on_an_empty_listing_still_keeps_its_archive_copy_unread(
        readings, roots, tmp_path):
    archive = tmp_path / "old-machine"
    transcript(archive / "projects", SESSION_A).write()
    live = transcript(roots["claude"], SESSION_A).write()
    write_config(f'[[claude.archive_roots]]\npath = "{archive}"\nid = "old"\n')
    run(readings)
    os.unlink(live)
    result = run(readings)
    assert result.lanes["claude"]["state"] == "held"
    assert files("archive:old") == []
    assert {r["lane"] for r in requests() if r["won"]} == {"claude"}


def test_archive_ranks_follow_the_config_and_a_reorder_moves_the_claim(readings,
                                                                       tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    for archive in (one, two):
        transcript(archive / "projects", SESSION_A).write()
    write_config(f'[[claude.archive_roots]]\npath = "{one}"\nid = "one"\n'
                 f'[[claude.archive_roots]]\npath = "{two}"\nid = "two"\n')
    run(readings)
    assert {r["lane"] for r in requests() if r["won"]} == {"archive:one"}

    write_config(f'[[claude.archive_roots]]\npath = "{two}"\nid = "two"\n'
                 f'[[claude.archive_roots]]\npath = "{one}"\nid = "one"\n')
    result = run(readings)
    assert result.read == 0 and result.reused == 2
    assert {row["lane"]: row["rank"] for row in files()} == {"archive:two": 3,
                                                            "archive:one": 4}
    assert {r["lane"] for r in requests() if r["won"]} == {"archive:two"}


def test_an_archive_lane_whose_id_left_the_config_is_dropped(readings, tmp_path):
    archive = tmp_path / "old-machine"
    transcript(archive / "projects", SESSION_A).write()
    write_config(f'[[claude.archive_roots]]\npath = "{archive}"\nid = "old"\n')
    run(readings)
    write_config("")
    result = run(readings)
    assert result.dropped_lanes == ["archive:old"] and files() == []


# --- the codex lane ---------------------------------------------------------------------

def rollout(root, thread, *, output=40, **kw):
    r = CodexRollout(root, thread_id=thread, **kw)
    r.turn()
    r.user("go")
    r.request(token_usage(output=output), response_id=f"resp_{thread[-4:]}")
    r.turn_end()
    r.write()
    return r


def test_a_rollout_is_indexed_with_its_origin_and_its_limits_go_to_the_readings(
        readings, roots):
    rollout(roots["codex"], THREAD_A)
    result = run(readings)
    [row] = files("codex")
    assert row["rank"] == 1 and row["origin"] == "exec" and row["originator"] == "codex_exec"
    assert row["excluded"] is None and row["session_id"] == THREAD_A
    assert [r["output"] for r in won()] == [40]
    assert readings.added and {r.kind for r in readings.added} == {"limits"}
    assert result.readings_added == len(readings.added)
    assert result.lanes["codex"]["imports_reason"] == "imports file not found"


def test_an_imported_rollout_is_skipped_and_a_mark_that_flips_later_moves_its_claim(
        readings, roots):
    rollout(roots["codex"], THREAD_A, output=40)
    rollout(roots["codex"], THREAD_B, output=50, start=1793358000.0)
    run(readings)
    assert sorted(r["output"] for r in won()) == [40, 50]

    write_imports(roots["codex"], [THREAD_B])          # listed after it was counted
    result = run(readings)
    assert result.read == 0
    row = {r["session_id"]: r for r in files("codex")}[THREAD_B]
    assert row["excluded"] == "imported" and row["cursor"] is None
    assert requests(row["path"]) == []
    assert [r["output"] for r in won()] == [40]
    assert result.lanes["codex"]["imported"] == 1

    write_imports(roots["codex"], [])                  # and no longer listed
    result = run(readings)
    assert result.read == 1
    assert {r["session_id"]: r["excluded"] for r in files("codex")} == {
        THREAD_A: None, THREAD_B: None}
    assert sorted(r["output"] for r in won()) == [40, 50]


def test_a_rollout_with_an_imported_turn_id_is_skipped(readings, roots):
    r = CodexRollout(roots["codex"], thread_id=THREAD_A, imported=True)
    r.turn()
    r.request(token_usage(output=40))
    r.write()
    run(readings)
    [row] = files("codex")
    assert row["excluded"] == "imported" and won() == []
    assert row["originator"] == "Codex Desktop"


def test_the_desktop_originator_alone_counts_the_rollout_and_is_kept_for_the_doctor(
        readings, roots):
    rollout(roots["codex"], THREAD_A, originator="Codex Desktop")
    write_imports(roots["codex"], [])
    result = run(readings)
    [row] = files("codex")
    assert row["excluded"] is None and row["originator"] == "Codex Desktop"
    assert [r["output"] for r in won()] == [40]
    assert result.lanes["codex"]["originator_only"] == 1
    assert result.lanes["codex"]["imports_reason"] is None


def test_an_unreadable_imports_file_counts_the_rollout_and_says_why(readings, roots):
    rollout(roots["codex"], THREAD_A)
    write_imports(roots["codex"], text="not json")
    result = run(readings)
    assert [r["output"] for r in won()] == [40]
    assert result.lanes["codex"]["imports_reason"].startswith("imports file unreadable")
    stored = index.json.loads(rows("SELECT value FROM meta WHERE key = 'lanes'")[0]["value"])
    assert stored["codex"]["imports_reason"].startswith("imports file unreadable")


# --- the opencode lane ------------------------------------------------------------------

def opencode(roots):
    return OpenCodeDb(roots["opencode"].parent, name=roots["opencode"].name)


def test_opencode_requests_are_filed_under_one_row_per_session(readings, roots):
    db = opencode(roots)
    parent = db.session(title="fix it", directory="/work/project")
    parent.user("go")
    reply = parent.assistant()
    reply.step(input=900, output=40, cache_read=100)
    reply.complete()
    child = parent.child(title="look", directory="/work/project")
    child.user("look")
    answer = child.assistant()
    answer.step(input=100, output=7)
    answer.complete()
    db.write()

    run(readings)

    by_part = {row["part"]: row for row in files("opencode")}
    assert set(by_part) == {parent.id, child.id}
    assert by_part[parent.id]["kind"] == "main" and by_part[parent.id]["title"] == "fix it"
    assert by_part[child.id]["kind"] == "subagent"
    assert by_part[child.id]["parent_session"] == parent.id
    assert {row["path"] for row in by_part.values()} == {str(roots["opencode"])}
    assert {row["cursor"] for row in by_part.values()} == {None}
    cursor = rows("SELECT value FROM meta WHERE key LIKE 'cursor:opencode:%'")
    assert len(cursor) == 1
    assert sorted(r["output"] for r in won()) == [7, 40]

    again = run(readings)
    assert again.read == 0 and sorted(r["output"] for r in won()) == [7, 40]


def test_opencode_messages_deleted_since_the_last_read_leave_the_index(readings, roots):
    db = opencode(roots)
    session = db.session()
    session.user("go")
    first = session.assistant()
    first.step(output=40)
    first.complete()
    session.user("more")
    second = session.assistant()
    second.step(output=50)
    second.complete()
    db.write()
    run(readings)
    assert sorted(r["output"] for r in won()) == [40, 50]

    db.delete(second.id)                              # a revert
    db.write()
    run(readings)
    assert [r["output"] for r in won()] == [40]

    db.delete_session(session.id)
    other = db.session()
    other.user("hi")
    db.write()
    result = run(readings)
    assert [row["part"] for row in files("opencode")] == [other.id]
    assert won() == [] and result.deleted == 1


def test_an_opencode_database_with_no_sessions_left_keeps_its_rows_held(readings, roots):
    db = opencode(roots)
    session = db.session()
    session.user("go")
    reply = session.assistant()
    reply.step(output=40)
    reply.complete()
    db.write()
    run(readings)
    db.delete_session(session.id)
    db.write()
    result = run(readings)
    [row] = files("opencode")
    assert "listed no sessions" in row["held"] and result.lanes["opencode"]["state"] == "held"
    assert [r["output"] for r in won()] == [40]


# --- project keys -----------------------------------------------------------------------

def test_a_key_recorded_while_a_worktree_exists_survives_its_removal_and_a_rebuild(
        readings, roots, tmp_path):
    worktree = tmp_path / "wt" / "build-1"
    (worktree / "src").mkdir(parents=True)
    projects = Projects({str(worktree): "moxi"})
    transcript(roots["claude"], SESSION_A, cwd=str(worktree / "src")).write()
    result = run(readings, projects=projects)
    assert result.projects_recorded == 1
    assert [row["project"] for row in files("claude")] == ["moxi"]

    shutil.rmtree(worktree)
    run(readings, projects=projects)
    run(readings, projects=projects, rebuild=True)
    assert [row["project"] for row in files("claude")] == ["moxi"]
    assert projects.asked == [str(worktree / "src")]


def test_a_cwd_that_no_longer_exists_is_never_looked_up_and_stays_without_a_key(
        readings, roots, tmp_path):
    projects = Projects({str(tmp_path / "gone"): "moxi"})
    transcript(roots["claude"], SESSION_A, cwd=str(tmp_path / "gone" / "src")).write()
    run(readings, projects=projects)
    assert [row["project"] for row in files("claude")] == [None]
    assert projects.asked == []


def test_a_session_whose_worktree_went_takes_its_key_from_the_sessions_start(
        readings, roots, tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    projects = Projects({str(worktree): "moxi"})
    t = transcript(roots["claude"], SESSION_A, cwd=str(worktree))
    t.write()
    run(readings, projects=projects)

    shutil.rmtree(worktree)
    agent = t.subagent("a01")                       # written after the worktree went
    agent.user("look")
    agent.assistant("found", usage=usage(output=5), request_id="req_agent_1")
    t.write()
    transcript(roots["claude"], SESSION_B, project="-work-other",
               cwd=str(worktree)).write()           # another session, same dead path
    result = run(readings, projects=projects)

    keys = {(row["session_id"], row["kind"]): row["project"] for row in files("claude")}
    assert keys == {(SESSION_A, "main"): "moxi", (SESSION_A, "subagent"): "moxi",
                    (SESSION_B, "main"): None}
    assert result.projects_recorded == 1 and projects.asked == [str(worktree)]


def test_without_projects_no_key_is_recorded(readings, roots, tmp_path, monkeypatch):
    import moxi.modules

    monkeypatch.setattr(moxi.modules, "available", lambda name: False)
    transcript(roots["claude"], SESSION_A, cwd=str(tmp_path)).write()
    result = index.pass_(readings=readings, now=NOW, workers=1)
    assert result.projects_recorded == 0
    assert [row["project"] for row in files("claude")] == [None]


def test_a_lookup_that_fails_leaves_the_row_for_a_later_pass(readings, roots, tmp_path):
    class Busy(Projects):
        def for_path(self, path):
            self.asked.append(path)
            raise self.Unreadable("locked")

    busy = Busy({})
    transcript(roots["claude"], SESSION_A, cwd=str(tmp_path)).write()
    run(readings, projects=busy)
    assert [row["project"] for row in files("claude")] == [None]
    projects = Projects({str(tmp_path): "moxi"})
    run(readings, projects=projects)
    assert [row["project"] for row in files("claude")] == ["moxi"]


# --- the lock ---------------------------------------------------------------------------

HOLD = textwrap.dedent("""
    import sys
    import moxi.files
    with moxi.files.lock(sys.argv[1]):
        print("held", flush=True)
        sys.stdin.readline()
""")


@pytest.fixture
def held_lock():
    """Another process holding the index's lock until the test ends."""
    path = store.lock_path()
    proc = subprocess.Popen([sys.executable, "-c", HOLD, str(path)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        yield proc
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)


def test_a_held_lock_makes_no_wait_exit_75(readings, roots, held_lock, capsys):
    assert cli.main(["index", "--no-wait"]) == 75
    assert "another pass holds the index" in capsys.readouterr().err
    with pytest.raises(moxi.files.LockTimeout):
        index.pass_(readings=readings, wait=0, projects=None)


def test_the_wait_for_the_lock_runs_on_the_injected_clock(readings, held_lock):
    ticks = []
    clock = iter(range(1000)).__next__

    def sleep(seconds):
        ticks.append(seconds)

    with pytest.raises(moxi.files.LockTimeout):
        index.pass_(readings=readings, wait=5, clock=lambda: float(clock()), sleep=sleep,
                    projects=None)
    assert len(ticks) == 4 and set(ticks) == {index.POLL}     # the fifth look is at 5


CONCURRENT = textwrap.dedent("""
    from moxi_usage import cli
    raise SystemExit(cli.main(["index", "--no-wait", "--json"]))
""")


def test_two_passes_at_once_never_both_write_and_leave_the_index_whole(readings, roots):
    for n in range(12):
        session = f"0190b000-0000-7000-8000-0000000001{n:02d}"
        t = ClaudeTranscript(roots["claude"], session_id=session, project=f"-p{n}")
        for m in range(20):
            t.user("go")
            t.assistant("ok", usage=usage(output=m + 1), request_id=f"req_{n}_{m}")
        t.write()
    procs = [subprocess.Popen([sys.executable, "-c", CONCURRENT], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) for _ in range(2)]
    codes = sorted(proc.wait(timeout=120) for proc in procs)
    for proc in procs:
        proc.stdout.close()
        proc.stderr.close()
    assert codes in ([0, 0], [0, 75])
    conn = store.connect()
    try:
        assert store.check(conn) == []
        assert conn.execute("SELECT count(*) FROM requests WHERE won = 1").fetchone()[0] \
            == 240
    finally:
        conn.close()
    assert run(readings).read == 0                  # the index was whole: nothing left


# --- rebuilds, workers, cancellation ----------------------------------------------------

def corpus(roots, sessions_count=6):
    for n in range(sessions_count):
        session = f"0190b000-0000-7000-8000-0000000002{n:02d}"
        t = ClaudeTranscript(roots["claude"], session_id=session, project=f"-p{n}")
        for m in range(5):
            t.user("go")
            t.assistant("ok", usage=usage(output=10 * n + m), request_id=f"req_{n}_{m}")
        t.write()
    for n in range(3):
        thread = f"0190c000-0000-7000-8000-0000000003{n:02d}"
        rollout(roots["codex"], thread, output=500 + n)


def test_a_rebuild_in_worker_processes_gives_the_index_a_pass_gave(readings, roots):
    corpus(roots)
    run(readings)
    before = requests()
    result = run(readings, rebuild=True, workers=2)
    assert result.workers == 2 and result.read == 9
    assert requests() == before


def test_many_fresh_sources_are_read_in_workers(readings, roots, monkeypatch):
    corpus(roots)
    monkeypatch.setattr(index, "POOL_ABOVE", 2)
    result = run(readings, workers=2)
    assert result.workers == 2 and result.read == 9 and len(won()) == 33
    assert len(readings.added) >= 3


def test_a_cancelled_pass_keeps_what_it_read(readings, roots):
    corpus(roots)
    asked = []

    def cancel():
        asked.append(1)
        return len(asked) > 6         # three lanes, the reads, then two sources

    result = run(readings, cancel=cancel)
    assert result.cancelled and 0 < result.read < 9
    assert rows("SELECT value FROM meta WHERE key = 'last_pass'") == []
    assert run(readings).read == 9 - result.read


def test_a_readings_store_that_fails_does_not_stop_the_index(roots):
    class Broken(Readings):
        def add_codex(self, records, *, now, env=None):
            raise OSError("disk full")

    rollout(roots["codex"], THREAD_A)
    result = run(Broken())
    assert "disk full" in result.readings_error and len(won()) == 1


def test_a_harness_this_machine_does_not_have_is_absent_not_offline(readings, tmp_path):
    archive = tmp_path / "unplugged"
    write_config(f'[[claude.archive_roots]]\npath = "{archive}"\nid = "usb"\n')
    result = run(readings)
    assert {lane: state["state"] for lane, state in result.lanes.items()} == {
        "claude": "absent", "codex": "absent", "opencode": "absent",
        "archive:usb": "offline"}


def test_an_unknown_lane_is_refused(readings):
    with pytest.raises(ValueError, match="no such lane"):
        index.pass_(readings=readings, lanes=["nowhere"], projects=None)


def test_one_lane_runs_alone(readings, roots):
    transcript(roots["claude"], SESSION_A).write()
    rollout(roots["codex"], THREAD_A)
    result = run(readings, lanes=["codex"])
    assert set(result.lanes) == {"codex"} and [r["lane"] for r in requests()] == ["codex"]


# --- the verb ---------------------------------------------------------------------------

def test_the_index_verb_runs_the_pass_and_checks_the_claims(roots, capsys):
    transcript(roots["claude"], SESSION_A).write()
    assert cli.main(["index", "--check"]) == 0
    out = capsys.readouterr().out
    assert "1 read" in out and "agree" in out
    assert "claude           online   1 files, 2 requests" in out
    assert "index: 1 files, 2 requests, 0 duplicates" in out and "version 1" in out

    conn = store.connect()
    try:
        conn.execute("UPDATE requests SET won = 0")          # by hand, past the claim
    finally:
        conn.close()
    assert cli.main(["index", "--check", "--json"]) == 1
    doc = index.json.loads(capsys.readouterr().out)
    assert doc["check"]["agree"] is False and len(doc["check"]["keys"]) == 2
    assert doc["pass"]["reused"] == 1 and doc["status"]["kind"] == "status"
    assert doc["status"]["total"]["files"] == 1 and doc["status"]["index"]["passed"]


def test_status_shows_each_lane_its_holds_duplicates_and_imports(readings, roots,
                                                                 tmp_path):
    from moxi_usage import api, reports

    one, two = tmp_path / "one", tmp_path / "two"
    for archive in (one, two):
        transcript(archive / "projects", SESSION_A).write()
    write_config(f'[[claude.archive_roots]]\npath = "{one}"\nid = "one"\n'
                 f'[[claude.archive_roots]]\npath = "{two}"\nid = "two"\n')
    rollout(roots["codex"], THREAD_A)
    imported = CodexRollout(roots["codex"], thread_id=THREAD_B, imported=True)
    imported.turn()
    imported.request(token_usage(output=7))
    imported.write()
    run(readings)
    shutil.rmtree(two)                                     # the second drive unplugged
    run(readings)

    found = api.report("status", fresh=False)
    lanes = {row["lane"]: row for row in found.rows}
    assert [row["lane"] for row in found.rows] == [
        "claude", "codex", "opencode", "archive:one", "archive:two"]
    assert (lanes["codex"]["files"], lanes["codex"]["excluded"],
            lanes["codex"]["requests"]) == (2, 1, 1)
    assert lanes["codex"]["imports_reason"] == "imports file not found"
    assert lanes["archive:one"]["duplicates"] == 0
    two_row = lanes["archive:two"]
    assert (two_row["state"], two_row["held"], two_row["duplicates"]) == ("offline", 1, 2)
    [(reason, count)] = two_row["held_reasons"].items()
    assert "could not be listed" in reason and count == 1
    total = found.total
    assert (total["files"], total["requests"], total["duplicates"], total["held"],
            total["excluded"]) == (4, 5, 2, 1, 1)
    assert [row["lane"] for row in total["held_rows"]] == ["archive:two"]
    assert total["held_rows"][0]["reason"] == reason
    assert total["duplicate_files"] == 1
    [dup] = total["duplicate_rows"]
    assert (dup["lane"], dup["of_lane"], dup["requests"]) == ("archive:two", "archive:one", 2)
    assert dup["path"].startswith(str(two)) and dup["of_path"].startswith(str(one))
    assert total["index_version"] == store.INDEX_VERSION
    assert total["last_pass"] == NOW == found.index["last_pass"]
    assert [row["lane"] for row in api.report("status", harness="codex",
                                              fresh=False).rows] == ["codex"]
    with pytest.raises(reports.ReportError, match="takes no since"):
        api.report("status", since="2026-10-01", fresh=False)


def test_the_index_verb_names_a_file_that_wholly_repeats_another(roots, capsys):
    first = transcript(roots["claude"], SESSION_A).write()
    again = transcript(roots["claude"], SESSION_B).write()      # the same two requests
    own = ClaudeTranscript(roots["claude"], session_id=THREAD_A)
    own.user("go")
    own.assistant("one", usage=usage(output=11))                # repeats one request
    own.assistant("own", usage=usage(output=5), request_id="req_own", message_id="msg_own")
    own.write()
    assert cli.main(["index"]) == 0
    out = capsys.readouterr().out
    assert "index: 3 files, 6 requests, 3 duplicates" in out
    [line] = [line for line in out.splitlines() if "duplicate:" in line]
    assert line == f"  duplicate: claude {again} repeats claude {first}"
    assert cli.main(["index", "--json"]) == 0
    total = index.json.loads(capsys.readouterr().out)["status"]["total"]
    assert total["duplicate_files"] == 1
    assert [(d["path"], d["of_path"], d["requests"]) for d in total["duplicate_rows"]] == [
        (str(again), str(first), 2)]


def test_status_with_no_index_yet_is_empty_and_says_so():
    from moxi_usage import api

    found = api.report("status", fresh=False)
    assert found.rows == [] and found.total["files"] == 0
    assert found.total["last_pass"] is None and "no index yet" in found.index["note"]


def test_a_pass_with_no_readings_given_stores_codex_limits_in_the_readings_store(roots):
    from moxi_usage import readings

    rollout(roots["codex"], THREAD_A, start=NOW - 600)
    result = index.pass_(now=NOW, projects=None, workers=1)
    assert result.readings_error is None and result.readings_added > 0
    conn = readings.open_store()
    try:
        stored = conn.execute("SELECT harness, source, session_id FROM readings").fetchall()
    finally:
        conn.close()
    assert len(stored) == result.readings_added
    assert {tuple(row) for row in stored} == {("codex", "rollout", THREAD_A)}


def test_the_index_verb_maps_a_refused_schema_to_3(readings, monkeypatch, capsys):
    def refuse(**kw):
        raise store.MigrationRefused(["archive:old"], 1, 2)

    monkeypatch.setattr(index, "pass_", refuse)
    assert cli.main(["index"]) == 3
    assert "archive:old" in capsys.readouterr().err


# --- the archive roots in the config ----------------------------------------------------

def test_archive_roots_are_read_in_config_order(tmp_path):
    from moxi_usage import settings

    write_config('[[claude.archive_roots]]\npath = "~/old"\nid = "b"\n'
                 '[[claude.archive_roots]]\npath = "/mnt/usb/"\nid = "a"\n')
    roots = settings.archive_roots(settings.current())
    assert [(r.id, r.lane, r.number) for r in roots] == [("b", "archive:b", 1),
                                                         ("a", "archive:a", 2)]
    assert roots[0].path == os.path.expanduser("~/old") and roots[1].path == "/mnt/usb"
    assert [lane.id for lane in index.configured_lanes()] == [
        "claude", "codex", "opencode", "archive:b", "archive:a"]


@pytest.mark.parametrize("rows, problem", [
    ('[claude]\narchive_roots = ["/mnt/old"]\n', "not a table"),
    ('[[claude.archive_roots]]\nid = "a"\n', "has no path"),
    ('[[claude.archive_roots]]\npath = "/x"\n', "has id None"),
    ('[[claude.archive_roots]]\npath = "/x"\nid = "a b"\n', "has id 'a b'"),
    ('[[claude.archive_roots]]\npath = "x"\nid = "a"\n', "relative path"),
    ('[[claude.archive_roots]]\npath = "/x"\nid = "a"\nwhere = 1\n', "unknown keys"),
    ('[[claude.archive_roots]]\npath = "/x"\nid = "a"\n'
     '[[claude.archive_roots]]\npath = "/y"\nid = "a"\n', "repeats the id"),
])
def test_the_config_check_refuses_an_archive_root_usage_could_not_read(rows, problem):
    from moxi.config import ConfigError
    from moxi_usage import settings

    with pytest.raises(ConfigError, match=problem):
        settings.check(rows)
