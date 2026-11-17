"""Archive folders through memory: the walk over every folder, a folder answering a run
only with no error in it and at the test before the decision, each folder's record, the
first full read on the live roots, the rebuild's keep rule and copy, search's unread line,
status, reindex's words and the doctor's folders row."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import shutil
from pathlib import Path

import pytest
from moxi_sessions import api
from test_ingest import NO_LAUNCHER, TOKEN, claude, config, run, texts

from moxi_memory import cli, doctor, index, ingest, roots, search, state, status, table

T = api.testing
ONE = "0190b000-0000-7000-8000-0000000000b1"
TWO = "0190b000-0000-7000-8000-0000000000b2"
THREE = "0190b000-0000-7000-8000-0000000000b3"
WAIT_S = 0.6


@pytest.fixture(autouse=True)
def arch(tmp_path, monkeypatch) -> Path:
    """One Claude archive folder and one Codex archive folder, named in a sessions config
    of the test's own."""
    T.forget_archive_answers()
    base = tmp_path / "arch"
    for name in ("claude", "codex"):
        (base / name).mkdir(parents=True)
    monkeypatch.setenv("MOXI_SESSIONS_CONFIG_HOME", str(tmp_path / "sessions-config"))
    configure(tmp_path, claude=[str(base / "claude")], codex=[str(base / "codex")])
    yield base
    T.forget_archive_answers()


def configure(tmp_path, *, claude=(), codex=(), text=None):
    target = tmp_path / "sessions-config" / "sessions"
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.toml").write_text(text if text is not None else (
        f"[claude]\narchives = {json.dumps(list(claude))}\n"
        f"[codex]\narchives = {json.dumps(list(codex))}\n"
        f"[archives]\nanswer_wait_s = {WAIT_S}\n"), encoding="utf-8")
    T.forget_archive_answers()


@pytest.fixture
def never_answers():
    with T.archive_tests(never_answer=True):
        yield


@pytest.fixture
def hold():
    """Hold how sessions tests archive folders, as `api.testing.archive_tests` takes it,
    until the test ends."""
    with contextlib.ExitStack() as stack:
        yield lambda **kw: stack.enter_context(T.archive_tests(**kw))


def in_archive(arch, sid, words, **kw):
    t = claude(sid, **kw)
    t.path = Path(arch / "claude" / t.project / f"{sid}.jsonl")
    t.user(words)
    t.assistant(f"answer to {words}")
    t.write()
    return t


def recorded(harness="claude"):
    with index.open_read() as rd:
        return {r.path: r for r in roots.read(rd.conn, harness)}


def meta(key):
    with index.open_read() as rd:
        return index.meta_get(rd.conn, key)


# -- reading order, and the walk -----------------------------------------------------

def test_a_session_in_both_is_read_from_the_live_root_and_from_the_archive_once_live_goes(
        arch):
    live = claude(ONE)
    live.user("the live copy")
    live.write()
    in_archive(arch, ONE, "the archive copy")
    run()
    assert "the live copy" in texts() and "the archive copy" not in texts()
    os.remove(live.path)
    report = run("rebuild")
    assert report.dropped == 0
    assert "the archive copy" in texts() and "the live copy" not in texts()


def test_an_offline_archive_is_skipped_recorded_named_and_listed_whole_once_it_answers(
        arch):
    with T.archive_tests(never_answer=True):
        claude(TWO).user("a live prompt")
        t = claude(TWO)
        t.user("a live prompt")
        t.write()
        in_archive(arch, ONE, "words kept in the archive")
        run()
        assert meta("first_read") == "1"             # the live roots finish it
        rec = recorded()[str(arch / "claude")]
        assert not rec.answered and rec.why == roots.OFFLINE and "no answer" in rec.reason
        assert not rec.listed_whole and rec.last_walk_at is None
        assert "words kept in the archive" not in texts()
        result = search.search("archive")
        assert str(arch / "claude") in result.unread
        assert any("2 transcript folders not read yet" in line for line in cli.incomplete(result))
        s = status.status()
        assert any(r["path"] == str(arch / "claude") and not r["answered"]
                   for r in s.roots["claude"])
        row = {r.name: r for r in doctor.rows()}["folders"]
        assert row.state == "warn" and str(arch / "claude") in row.detail
        assert row.fix == doctor.ANSWER_FIX
        said = cli._last_run_lines()
        assert any(str(arch / "claude") in line and "offline" in line for line in said), said

    run()
    rec = recorded()[str(arch / "claude")]
    assert rec.answered and rec.listed_whole and rec.last_walk_at is not None
    assert "words kept in the archive" in texts()
    assert search.search("archive").unread == ()
    assert {r.name: r for r in doctor.rows()}["folders"].state == "ok"


def test_the_climb_to_a_main_session_asks_only_the_roots_that_answered_the_run(
        arch, tmp_path, hold):
    configure(tmp_path, codex=[str(arch / "codex")])
    spawned = []
    hold(keep_s=0, on_test=spawned.append)            # no kept answer helps a later call
    r = T.CodexRollout(arch / "codex")
    r.turn()
    r.user("codex main in the archive")
    child = r.spawn("0190c000-0000-7000-8000-0000000c0de9", "explore")
    child.turn()
    child.user("codex child work")
    r.write()
    child.write()
    run(config=config(**{"index.include_subagents": True}))
    assert "codex child work" in texts()
    assert spawned == [str(arch / "codex")] * 2      # the start's test and the decision's


# -- the rebuild's keep rule ---------------------------------------------------------

def test_a_rebuild_with_an_archive_offline_keeps_and_copies_what_it_read_no_file_for(
        arch, hold):
    t = in_archive(arch, ONE, "archived prompt")
    call = t.request().tool_use("Bash", {"command": "cat .env"})
    t.tool_result(call, "root:x:0:0")
    call = t.request().tool_use("Bash", {"command": "ls"})
    t.tool_result(call, "y" * 1500)
    t.user(f"token {TOKEN}")
    sub = t.subagent()
    sub.user("sub work in the archive")
    t.write()
    run(config=config(**{"redact.sets": [], "index.include_subagents": True}))
    assert any(TOKEN in x for x in texts())
    sc = state.connect()
    table.relabel(sc, "claude", ONE, "table-label")
    with index.open_read() as rd:
        before = rd.conn.execute("SELECT source, cursor FROM sources WHERE session_id = ?",
                                 (ONE,)).fetchall()
        secret = rd.conn.execute("SELECT count(*) FROM secret_calls").fetchone()[0]
    assert secret == 1
    hold(never_answer=True)
    for _ in range(2):                               # and again while still offline
        report = run("rebuild", config=config(**{"index.tail_chars": 200}))
        assert report.dropped == 0 and report.kept == 1
        got = texts()
        assert "answer to archived prompt" in got and "token [REDACTED:github-pat]" in got
        assert not any(TOKEN in x for x in got)
        assert "sub work in the archive" not in got           # the switch, applied
        assert all(len(x) <= 200 + len("[... 1300 earlier characters cut] ")
                   for x in texts("tails"))
        with index.open_read() as rd:
            c = rd.conn
            assert {r[0] for r in c.execute("SELECT label FROM rows")} == {"table-label"}
            assert c.execute("SELECT source, cursor FROM sources WHERE session_id = ?",
                             (ONE,)).fetchall() == before
            assert c.execute("SELECT count(*) FROM secret_calls").fetchone()[0] == 1
            assert index.meta_get(c, "kept") == "1"
        assert table.row(sc, "claude", ONE) is not None
    rec = recorded()[str(arch / "claude")]
    assert rec.missed_rebuild_at is not None
    assert any("run `moxi memory reindex` again once it answers" in line
               for line in cli._last_run_lines())


def test_a_harness_whose_roots_all_answer_drops_a_gone_session(arch):
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    r.user("a codex session about to go")
    r.write()
    run()
    os.remove(r.path)
    report = run("rebuild")
    assert report.dropped == 1 and "a codex session about to go" not in texts()
    assert all(rec.answered for rec in recorded("codex").values())


def test_an_archive_taken_off_config_is_not_tested_and_its_sessions_leave_at_the_rebuild(
        arch, tmp_path, hold):
    in_archive(arch, ONE, "only in the archive")
    run()
    assert "only in the archive" in texts()
    configure(tmp_path, codex=[str(arch / "codex")])
    spawned = []
    hold(on_test=spawned.append)
    assert any("no longer names it" in line and str(arch / "claude") in line
               for line in cli._last_run_lines())
    row = {r.name: r for r in doctor.rows()}["folders"]
    assert row.state == "warn" and doctor.REMOVED_FIX in row.fix
    report = run("rebuild")
    assert str(arch / "claude") not in spawned
    assert report.dropped == 1 and "only in the archive" not in texts()
    assert str(arch / "claude") not in recorded()


def test_a_rebuild_with_no_old_generation_copies_nothing(arch, never_answers):
    in_archive(arch, ONE, "words")
    shutil.rmtree(index.index_dir(), ignore_errors=True)
    report = run("rebuild")
    assert report.kept == 0 and report.dropped == 0


def test_a_compressed_only_codex_session_counts_as_found_and_its_rows_are_copied(arch):
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    r.user("codex words before compression")
    r.write()
    run()
    zst = Path(str(r.path) + ".zst")
    zst.write_bytes(b"\x28\xb5\x2f\xfd")
    os.remove(r.path)
    report = run("rebuild")
    assert report.dropped == 0 and report.kept == 1
    assert "codex words before compression" in texts()


# -- a folder that fails during a run --------------------------------------------------

def fault(kind, arch, monkeypatch, stack, target):
    """Swap one fault into sessions' listing and reads for `target`'s file, until
    `monkeypatch`'s context and `stack` end."""
    if kind == "unlistable":
        os.chmod(target.parent, 0)
        return
    if kind == "unplugged":
        stack.enter_context(T.archive_tests(stand_in=lambda d, *, fresh: (
            api.ArchiveAnswer(d, ok=False, reason="unplugged") if fresh else None)))
        return
    real = api.records_since
    calls = {"n": 0}

    def records_since(item, cursor, **kw):
        src = item if isinstance(item, api.Source) else item.source
        if str(src.path) == str(target):
            calls["n"] += 1
            if kind == "gone":
                raise FileNotFoundError(errno.ENOENT, "No such file or directory")
            if calls["n"] > 1:                       # halfway: after the first batch
                raise api.Unreadable("Input/output error")
        return real(item, cursor, **kw)

    monkeypatch.setattr(api, "records_since", records_since)
    monkeypatch.setattr(ingest, "MAX_BYTES", 200)


FAULTS = ["unlistable", "eio", "gone", "unplugged"]


@pytest.mark.parametrize("kind", FAULTS)
def test_a_rebuild_with_an_archive_failing_keeps_its_harness_and_copies_whole(
        arch, monkeypatch, kind):
    t = in_archive(arch, ONE, "old words")
    live = claude(TWO, serial=300)
    live.user("a live session deleted later")
    live.write()
    run()
    # the archived session now holds other lines: a half read would store them
    t2 = claude(ONE, serial=900)
    t2.path = t.path
    for n in range(8):
        t2.user(f"new words {n} " + "z" * 60)
    t2.write()
    os.remove(live.path)
    with monkeypatch.context() as m, contextlib.ExitStack() as stack:
        fault(kind, arch, m, stack, t.path)
        try:
            report = run("rebuild")
        finally:
            os.chmod(t.path.parent, 0o755)
    got = texts()
    assert report.dropped == 0
    if kind == "unplugged":     # read whole before the decision: its own rows, no copy
        assert any(x.startswith("new words") for x in got) and report.kept == 1
    else:
        assert "old words" in got and not any(x.startswith("new words") for x in got)
    assert "a live session deleted later" in got
    rec = recorded()[str(arch / "claude")]
    assert not rec.answered
    if kind == "unplugged":
        assert rec.why == roots.OFFLINE
    else:
        assert rec.why == roots.FAILED and rec.first_error is not None
        assert str(t.path.parent if kind == "unlistable" else t.path) == rec.first_error[0]


@pytest.mark.parametrize("kind", FAULTS)
def test_a_walk_with_an_archive_failing_retries_what_failed_next_time(
        arch, monkeypatch, kind):
    t = in_archive(arch, ONE, "first words")
    run()
    before = recorded()[str(arch / "claude")].last_walk_at
    t.user("later words " + "z" * 300)
    t.write()
    with monkeypatch.context() as m, contextlib.ExitStack() as stack:
        fault(kind, arch, m, stack, t.path)
        try:
            run()
        finally:
            os.chmod(t.path.parent, 0o755)
    rec = recorded()[str(arch / "claude")]
    assert not rec.answered
    if kind == "unplugged":
        assert rec.last_walk_at == before and rec.retry == ()
    else:
        assert rec.last_walk_at > before and rec.retry   # read again by its session
    T.forget_archive_answers()
    os.utime(t.path, (1_000_000_000, 1_000_000_000))   # older than every last walk
    run()
    rec = recorded()[str(arch / "claude")]
    assert rec.answered and rec.retry == ()
    assert any(x.startswith("later words") for x in texts())


def test_an_archive_offline_at_the_start_and_answering_at_the_test_is_not_answered(
        arch, hold):
    in_archive(arch, ONE, "archive words")
    run()
    hold(stand_in=lambda d, *, fresh: (
        None if fresh else api.ArchiveAnswer(d, ok=False, reason="asleep")))
    report = run("rebuild")
    assert report.kept == 1 and "archive words" in texts()
    assert recorded()[str(arch / "claude")].why == roots.OFFLINE


def test_an_empty_archive_answers_only_when_memory_read_nothing_from_it(arch):
    run()
    assert recorded()[str(arch / "claude")].answered            # empty, nothing read
    t = in_archive(arch, ONE, "on the disk")
    run()
    shutil.rmtree(t.path.parent)                               # the mount point, left empty
    report = run("rebuild")
    rec = recorded()[str(arch / "claude")]
    assert rec.why == roots.EMPTY
    assert rec.reason.startswith("lists nothing, though memory read 1 source from it and "
                                 "it held entries at ")
    assert report.kept == 1 and "on the disk" in texts()


def test_an_archive_whose_copies_were_all_shadowed_keeps_them_once_left_empty(arch):
    live = claude(ONE)
    live.user("read from the live root")
    live.write()
    t = in_archive(arch, ONE, "the archive's copy")
    run()
    assert "read from the live root" in texts() and "the archive's copy" not in texts()
    os.remove(live.path)                             # the harness's cleanup
    shutil.rmtree(t.path.parent)                     # the mount point, left empty
    report = run("rebuild")
    assert report.dropped == 0 and "read from the live root" in texts()
    assert table.row(state.connect(), "claude", ONE) is not None
    rec = recorded()[str(arch / "claude")]
    assert rec.why == roots.EMPTY and rec.held_entries_at is not None
    assert rec.reason.startswith("lists nothing, though it held entries at ")
    row = {r.name: r for r in doctor.rows()}["folders"]
    assert "though it held entries at" in row.detail and doctor.EMPTIED_FIX in row.fix
    report = run("rebuild")                          # and the next rebuild still keeps it
    assert report.dropped == 0 and "read from the live root" in texts()
    assert recorded()[str(arch / "claude")].why == roots.EMPTY


def test_an_archive_seen_holding_entries_in_a_run_it_failed_is_not_answered_once_empty(
        arch):
    live = claude(ONE)
    live.user("read from the live root")
    live.write()
    t = in_archive(arch, ONE, "the archive's copy")
    locked = arch / "claude" / "locked"
    locked.mkdir()
    os.chmod(locked, 0)
    try:
        run()
    finally:
        os.chmod(locked, 0o755)
    failed = recorded()[str(arch / "claude")]
    assert failed.why == roots.FAILED
    os.remove(live.path)
    shutil.rmtree(t.path.parent)
    locked.rmdir()
    report = run("rebuild")
    assert report.dropped == 0 and "read from the live root" in texts()
    rec = recorded()[str(arch / "claude")]
    assert rec.why == roots.EMPTY and "though it held entries at" in rec.reason
    assert rec.held_entries_at == failed.last_walk_at   # set by the run it failed


def test_the_test_before_the_decision_sees_a_mount_point_left_empty(arch, hold):
    t = in_archive(arch, ONE, "on the unmounted disk")
    run()
    away = arch / "away"

    def unmount(d, *, fresh):
        if fresh and t.path.parent.exists():       # unmounted after the read
            shutil.move(str(t.path.parent), str(away))

    hold(stand_in=unmount)
    report = run("rebuild")
    assert recorded()[str(arch / "claude")].why == roots.EMPTY
    assert report.dropped == 0 and "on the unmounted disk" in texts()
    sc = state.connect()
    assert table.row(sc, "claude", ONE) is not None


def test_an_absent_live_root_never_read_lets_archives_be_read_and_one_read_keeps(arch):
    shutil.rmtree(api.root("claude"), ignore_errors=True)
    in_archive(arch, ONE, "archive only")
    run()
    assert "archive only" in texts() and meta("first_read") == "1"
    rec = recorded()[str(api.root("claude"))]
    assert rec.answered
    live = claude(TWO, serial=200)
    live.user("live then the root goes")
    live.write()
    run()
    shutil.rmtree(api.root("claude"))
    report = run("rebuild")
    assert recorded()[str(api.root("claude"))].why == roots.EMPTY
    assert report.dropped == 0 and "live then the root goes" in texts()


def test_a_sessions_config_that_cant_be_read_drops_nothing(arch, tmp_path):
    t = claude(ONE)
    t.user("kept through a broken config")
    t.write()
    run()
    os.remove(t.path)
    root = str(api.root("claude"))
    configure(tmp_path, text="[claude\nnot toml")
    report = run("rebuild")
    assert report.dropped == 0 and "kept through a broken config" in texts()
    rec = recorded()[root]
    assert rec.why == roots.FAILED and "can't list" in rec.first_error[1]


def test_a_turn_read_asks_the_live_root_only(arch, hold):
    spawned = []
    hold(on_test=spawned.append)
    t = claude(ONE)
    t.user("a turn")
    t.write()
    run("turn", "claude", ONE)
    assert "a turn" in texts() and spawned == []


def test_status_json_carries_each_root_and_the_kept_count(arch, never_answers):
    in_archive(arch, ONE, "words")
    run()
    run("rebuild")
    s = status.status().to_json()
    assert s["kept"] == 0
    assert {r["path"] for r in s["roots"]["claude"]} >= {str(arch / "claude")}
    assert json.dumps(s)
    assert NO_LAUNCHER
