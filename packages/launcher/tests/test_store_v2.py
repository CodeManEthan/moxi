"""The launch log at schema version 2: the step that carries a version 1 log forward with
no row lost, the session ids keyed by the pair, the base environment column, and runs (one
open run per launch, states that only go forward in every order, a stale run is one whose
job is gone, retention)."""

from __future__ import annotations

import itertools
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from moxi import store as core_store

from moxi_launcher import store

V1_DUMP = Path(__file__).with_name("launcher_v1.sql")
#: The ids the version 1 log holds for launch `r-slot`: minted, then a clear and a compact
#: learned in the same second, so only the row order says which is newest.
MINTED = "3c1d2e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
CLEARED = "7e8f9a0b-1c2d-4e3f-9a5b-6c7d8e9f0a1b"
COMPACTED = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
V1_TABLES = ("launches", "session_ids", "events", "picks")
V1_COLUMNS = store.COLUMNS[:-1]
T0 = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def at(seconds: float = 0) -> str:
    return store.stamp(T0 + timedelta(seconds=seconds))


def version_1(path: Path) -> Path:
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA journal_mode = WAL")
    raw.executescript(V1_DUMP.read_text())
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 1
    raw.close()
    return path


def rows(conn, table, columns="*"):
    return conn.execute(f"SELECT rowid, {columns} FROM {table} ORDER BY rowid").fetchall()


def snapshot(conn):
    return {"launches": rows(conn, "launches", ", ".join(V1_COLUMNS)),
            **{t: rows(conn, t) for t in V1_TABLES if t != "launches"}}


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    yield connection
    connection.close()


def launch(conn, request_id="r1", session_id=None, **overrides):
    session_id = session_id or f"{request_id}-0000-4000-8000-000000000000"
    args = {"request_id": request_id, "actor": "someone", "harness": "claude",
            "target": "slot", "cwd": "/work", "values": {"model": "opus"},
            "argv": ["claude", "--session-id", session_id],
            "env": {"MOXI_LAUNCHER_REQUEST": request_id}, "posture": "operator",
            "session_id": session_id, "slot": "3"}
    args.update(overrides)
    return store.insert(conn, **args).row


def run(conn, request_id="r1", **overrides):
    args = {"kind": "relaunch", "mode": "self", "request_id": request_id,
            "session_id": f"{request_id}-0000-4000-8000-000000000000", "actor": "someone",
            "at": at()}
    args.update(overrides)
    return store.insert_run(conn, **args)


# -- the step from version 1 ---------------------------------------------------------------


def test_a_version_1_log_is_carried_forward_with_no_row_lost(tmp_path):
    db = version_1(tmp_path / "launcher.db")
    raw = sqlite3.connect(db)
    before = snapshot(raw)
    raw.close()
    assert sum(len(v) for v in before.values()) == 14

    conn = store.connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert snapshot(conn) == before
        assert [r[0] for r in conn.execute("SELECT base_env_json FROM launches")] == [None] * 4
        assert all(row.base_env_json is None and row.base_env is None
                   for row in store.log(conn))
        for table in ("runs", "run_events", "pending_compacts", "warm_holds", "exits"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        conn.close()


def test_the_carried_log_reads_as_it_did(tmp_path):
    conn = store.connect(version_1(tmp_path / "launcher.db"))
    try:
        for sid in (MINTED, CLEARED, COMPACTED):
            row = store.resolve(conn, sid)
            assert (row.request_id, row.newest_session_id) == ("r-slot", COMPACTED)
        assert store.session_ids(conn, "r-slot") == [
            (MINTED, "minted"), (CLEARED, "clear"), (COMPACTED, "compact")]
        assert store.resolve(conn, "thread-1").newest_session_id == "thread-2"
        assert store.resolve(conn, "nobody") is None
        row = store.get(conn, "r-slot")
        assert (row.endpoint, row.state, row.pane_id) == (
            "4194903@1761735600.000000", "first-sent", "%7")
        assert [e.kind for e in store.events(conn, "r-slot")] == ["screen", "reclaimed"]
        assert store.picked(conn, "seat:lead") == ["/work/one/handoffs/h0.md",
                                                   "/work/one/handoffs/h1.md"]
        # And it takes new writes: an act's run, and a launch with its base environment.
        assert run(conn, "r-slot", session_id=COMPACTED).state == "armed"
        assert store.learn_session(conn, "r-codex", "thread-3", "compact")
    finally:
        conn.close()


def test_session_ids_are_keyed_by_the_pair_with_an_index_on_the_id(conn):
    key = sorted((row[5], row[1]) for row in conn.execute("PRAGMA table_info(session_ids)")
                 if row[5])
    assert key == [(1, "session_id"), (2, "request_id")]
    indexes = {name: cols for name, cols in (
        (n, [c[2] for c in conn.execute(f"PRAGMA index_info({n})")])
        for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' "
                                 "AND tbl_name = 'session_ids' AND sql IS NOT NULL"))}
    assert indexes == {"session_ids_session_id": ["session_id"]}
    launch(conn, "r1", session_id="shared")
    launch(conn, "r2", session_id="r2-own")
    # The schema holds one id for two launches (a resume writes that); one pair once.
    conn.execute("INSERT INTO session_ids VALUES ('shared', 'r2', 'resume', ?)", (at(),))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO session_ids VALUES ('shared', 'r2', 'resume', ?)", (at(),))


def test_learning_an_id_still_leaves_it_with_the_launch_that_has_it(conn):
    launch(conn, "r1", session_id="shared")
    launch(conn, "r2", session_id="r2-own")
    assert not store.learn_session(conn, "r2", "shared", "startup")
    assert store.session_ids(conn, "r2") == [("r2-own", "minted")]
    assert store.learn_session(conn, "r1", "after", "clear")
    assert not store.learn_session(conn, "r1", "after", "clear")


def test_a_step_that_fails_leaves_the_version_1_log_whole(tmp_path):
    db = version_1(tmp_path / "launcher.db")
    raw = sqlite3.connect(db)
    raw.execute("CREATE TABLE runs (x)")  # step 2 can't make its own `runs` now
    raw.commit()
    before = snapshot(raw)
    raw.close()
    with pytest.raises(sqlite3.OperationalError):
        store.connect(db)
    raw = sqlite3.connect(db)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 1
        assert snapshot(raw) == before
        columns = [r[1] for r in raw.execute("PRAGMA table_info(launches)")]
        assert "base_env_json" not in columns
        assert [r[1] for r in raw.execute("PRAGMA table_info(session_ids)") if r[5]] == [
            "session_id"]
    finally:
        raw.close()


def test_the_base_environment_column_is_empty_until_written(conn):
    row = launch(conn)
    assert row.base_env_json is None and store.get(conn, "r1").base_env is None
    conn.execute("UPDATE launches SET base_env_json = ? WHERE request_id = 'r1'",
                 ('{"HOME":"/home/someone","LANG":"C.UTF-8"}',))
    row = store.get(conn, "r1")
    assert row.base_env == {"HOME": "/home/someone", "LANG": "C.UTF-8"}
    assert row.as_json()["base_env_json"] == {"HOME": "/home/someone", "LANG": "C.UTF-8"}


def test_a_claim_names_its_endpoint_by_pid_and_start():
    assert store.parse_endpoint("4242@1761728401.500000") == (4242, 1761728401.5)
    assert store.parse_endpoint("4242") == (4242, None)
    assert store.parse_endpoint("endpoint-7") is None
    assert store.parse_endpoint("4242@later") is None
    assert not store.endpoint_gone("endpoint-7")


# -- runs ---------------------------------------------------------------------------------


def test_a_run_is_inserted_armed_with_a_sixteen_digit_id(conn):
    launch(conn)
    made = run(conn, job="slots:relaunch-x", handoff_path="/h.md", slot="3",
               turn_number=4)
    assert len(made.run_id) == 16 and int(made.run_id, 16) >= 0
    assert made == store.get_run(conn, made.run_id)
    assert (made.state, made.kind, made.mode, made.repeats) == ("armed", "relaunch", "self", 0)
    assert store.open_run(conn, "r1") == made
    assert made.as_json()["turn_number"] == 4


def test_one_open_run_per_launch(conn):
    launch(conn, "r1")
    launch(conn, "r2")
    first = run(conn, "r1")
    with pytest.raises(store.RunOpen) as caught:
        run(conn, "r1", kind="keep-warm", mode="ask", state="acting")
    assert caught.value.run == first and caught.value.exit_code == 3
    assert first.run_id in str(caught.value)
    assert run(conn, "r2").request_id == "r2"
    assert store.end_run(conn, first.run_id, "relaunched", new_request_id="r3")
    second = run(conn, "r1", kind="exit", mode="verb")
    assert store.open_run(conn, "r1") == second
    assert [r.run_id for r in store.runs(conn, request_id="r1")] == [second.run_id,
                                                                     first.run_id]


def test_the_open_run_index_holds_against_a_raw_insert(conn):
    launch(conn)
    made = run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO runs (run_id, kind, mode, request_id, session_id, actor, "
                     "state, created_at, updated_at) VALUES ('x', 'exit', 'verb', 'r1', "
                     "'s', 'a', 'waiting', ?, ?)", (at(), at()))
    assert store.get_run(conn, made.run_id).state == "armed"


def test_insert_run_refuses_what_it_does_not_know(conn):
    launch(conn)
    with pytest.raises(ValueError):
        run(conn, kind="sleep")
    with pytest.raises(ValueError):
        run(conn, state="ended")
    with pytest.raises(sqlite3.IntegrityError):
        run(conn, request_id="no-such-launch")


def test_a_crossing_is_recorded_once(conn):
    launch(conn, "r1")
    launch(conn, "r2")
    run(conn, "r1", kind="compact", mode="limit", crossing_key="s|1x|2026")
    with pytest.raises(sqlite3.IntegrityError):
        run(conn, "r2", kind="compact", mode="limit", crossing_key="s|1x|2026")


def moves_forward(before: str, after: str) -> bool:
    return before != "ended" and store.RUN_STATES.index(after) >= store.RUN_STATES.index(
        before)


@pytest.mark.parametrize("start", ["armed", "acting"])
def test_every_order_of_the_state_writes_only_goes_forward(conn, start):
    launch(conn)
    writes = ["waiting", "acting", "ended", "waiting", "acting"]
    for order in set(itertools.permutations(writes)):
        made = run(conn, state=start)
        state = start
        for target in order:
            moved = store.advance_run(conn, made.run_id, target)
            assert moved == moves_forward(state, target), (order, state, target)
            if moved:
                state = target
            assert store.get_run(conn, made.run_id).state == state
        store.end_run(conn, made.run_id, "done")


def test_a_job_learns_its_run_was_ended_under_it(conn):
    launch(conn)
    made = run(conn)
    assert store.advance_run(conn, made.run_id, "waiting")
    assert store.advance_run(conn, made.run_id, "waiting")
    assert store.end_run(conn, made.run_id, "error", word="job-gone")
    # Its next act checks first: nothing matches, and it does not act.
    assert not store.advance_run(conn, made.run_id, "acting", turn_number=9)
    assert not store.end_run(conn, made.run_id, "relaunched")
    ended = store.get_run(conn, made.run_id)
    assert (ended.outcome, ended.word, ended.turn_number) == ("error", "job-gone", None)


def test_a_state_write_names_only_states_it_may_leave(conn):
    launch(conn)
    made = run(conn)
    with pytest.raises(ValueError):
        store.advance_run(conn, made.run_id, "armed")
    with pytest.raises(ValueError):
        store.advance_run(conn, made.run_id, "waiting", leaving=["acting"])
    with pytest.raises(ValueError):
        store.advance_run(conn, made.run_id, "waiting", colour="red")
    assert not store.advance_run(conn, made.run_id, "acting", leaving=["waiting"])
    assert store.advance_run(conn, made.run_id, "acting", leaving=["armed"])


def test_a_long_detail_loses_its_front(conn):
    launch(conn)
    made = run(conn)
    detail = "start " + "x" * 9000 + " the last words"
    store.end_run(conn, made.run_id, "not-exited", word="mismatch", detail=detail)
    kept = store.get_run(conn, made.run_id).detail
    assert len(kept.encode()) <= store.DETAIL_CAP
    assert kept.endswith(" the last words") and not kept.startswith("start")
    assert store.cut("short") == "short" and store.cut(None) is None
    assert store.cut("é" * 3000).encode()  # never splits a character


def test_run_events_are_kept_in_order(conn):
    launch(conn)
    made = run(conn)
    store.add_run_event(conn, made.run_id, "wait", {"word": "person"}, at=at(2))
    store.add_run_event(conn, made.run_id, "armed", at=at(1))
    assert [(e.kind, e.detail) for e in store.run_events(conn, made.run_id)] == [
        ("armed", {}), ("wait", {"word": "person"})]


# -- stale runs ---------------------------------------------------------------------------


def never_gone(job):
    raise AssertionError(f"a run inside its grace was tested: {job}")


def test_a_run_is_never_stale_inside_its_grace(conn):
    launch(conn)
    made = run(conn)
    now = T0 + timedelta(seconds=store.RUN_GRACE_S)
    assert not store.run_stale(made, never_gone, now)


def test_a_run_with_no_job_after_its_grace_is_stale(conn):
    launch(conn)
    made = run(conn)
    now = T0 + timedelta(seconds=store.RUN_GRACE_S + 1)
    assert store.run_stale(made, never_gone, now)


def test_a_run_with_a_job_is_stale_exactly_when_the_job_is_gone(conn):
    launch(conn)
    made = run(conn, job="proc:4242@1761728401.500000")
    # A limit compact waits up to half an hour: past 1,200 s its job is still there.
    later = T0 + timedelta(seconds=1300)
    asked = []
    assert not store.run_stale(made, lambda job: asked.append(job) or False, later)
    assert store.run_stale(made, lambda job: True, later)
    assert asked == ["proc:4242@1761728401.500000"]
    store.end_run(conn, made.run_id, "done")
    assert not store.run_stale(store.get_run(conn, made.run_id), lambda job: True, later)


def test_an_insert_ends_a_run_whose_job_is_gone_and_takes_the_launch(conn):
    launch(conn)
    old = run(conn, kind="compact", mode="limit", job="proc:1@1.0", state="acting")
    new = run(conn, kind="exit", mode="verb", gone=lambda job: True, at=at(1300))
    ended = store.get_run(conn, old.run_id)
    assert (ended.state, ended.outcome, ended.word) == ("ended",) + store.JOB_GONE
    assert ended.updated_at == at(1300)
    assert [e.kind for e in store.run_events(conn, old.run_id)] == ["ended"]
    assert store.open_run(conn, "r1") == new


def test_an_insert_loses_to_a_run_whose_job_is_alive(conn):
    launch(conn)
    old = run(conn, kind="compact", mode="limit", job="proc:1@1.0", state="acting")
    with pytest.raises(store.RunOpen) as caught:
        run(conn, kind="relaunch", mode="self", gone=lambda job: False, at=at(1300))
    assert caught.value.run == old
    assert store.get_run(conn, old.run_id).state == "acting"


def test_an_insert_loses_to_a_job_that_wrote_its_run_while_it_was_tested(conn):
    launch(conn)
    old = run(conn, job="slots:relaunch-x")

    def gone(job):
        # The job moves on while the insert tests it: it is alive after all.
        store.advance_run(conn, old.run_id, "waiting", at=at(1299))
        return True

    with pytest.raises(store.RunOpen) as caught:
        run(conn, kind="exit", mode="verb", gone=gone, at=at(1300))
    assert caught.value.run.state == "waiting"
    assert store.get_run(conn, old.run_id).outcome is None


def test_without_a_test_an_open_run_is_never_ended(conn):
    launch(conn)
    old = run(conn)
    with pytest.raises(store.RunOpen):
        run(conn, at=at(10_000))
    assert store.get_run(conn, old.run_id).state == "armed"


# -- holds and exits ----------------------------------------------------------------------


def test_one_open_hold_of_a_name_per_launch(conn):
    launch(conn)
    conn.execute("INSERT INTO warm_holds VALUES ('r1', 'review', ?, NULL)", (at(),))
    conn.execute("INSERT INTO warm_holds VALUES ('r1', 'other', ?, NULL)", (at(),))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO warm_holds VALUES ('r1', 'review', ?, NULL)", (at(),))
    conn.execute("UPDATE warm_holds SET released_at = ? WHERE name = 'review'", (at(1),))
    conn.execute("INSERT INTO warm_holds VALUES ('r1', 'review', ?, NULL)", (at(2),))


# -- retention ----------------------------------------------------------------------------


def old_run(conn, request_id, days_ago, *, state="ended", events=1, exits=0):
    when = at(-days_ago * 86400)
    made = run(conn, request_id, at=when)
    for n in range(events):
        store.add_run_event(conn, made.run_id, "step", {"n": n}, at=when)
    for _ in range(exits):
        conn.execute("INSERT INTO exits (at, run_id, request_id, session_id, verdict) "
                     "VALUES (?, ?, ?, 's', 'exited')", (when, made.run_id, request_id))
    if state == "ended":
        store.end_run(conn, made.run_id, "done", at=when)
    return made


def test_prune_deletes_old_ended_runs_with_their_events_and_exits(conn):
    for rid in ("r1", "r2", "r3"):
        launch(conn, rid)
    gone = old_run(conn, "r1", 100, events=2, exits=1)
    kept_recent = old_run(conn, "r2", 10)
    kept_open = old_run(conn, "r3", 200, state="armed")
    conn.execute("INSERT INTO warm_holds VALUES ('r1', 'old', ?, ?)",
                 (at(-120 * 86400), at(-100 * 86400)))
    conn.execute("INSERT INTO warm_holds VALUES ('r1', 'open', ?, NULL)", (at(-200 * 86400),))
    conn.execute("INSERT INTO warm_holds VALUES ('r2', 'new', ?, ?)",
                 (at(-20 * 86400), at(-10 * 86400)))
    counts = store.prune(conn, 90, at=at())
    assert counts == {"run_events": 2, "exits": 1, "runs": 1, "warm_holds": 1}
    assert store.get_run(conn, gone.run_id) is None
    assert store.get_run(conn, kept_recent.run_id) is not None
    assert store.get_run(conn, kept_open.run_id).state == "armed"
    assert {n for (n,) in conn.execute("SELECT name FROM warm_holds")} == {"open", "new"}


def test_prune_takes_at_most_its_limit_of_each_table_and_finishes_later(conn):
    launch(conn, "r1")
    made = [old_run(conn, "r1", 100 + n, events=2) for n in range(3)]
    first = store.prune(conn, 90, at=at(), limit=2)
    assert first["run_events"] == 2 and first["runs"] == 1
    store.prune(conn, 90, at=at(), limit=2)
    store.prune(conn, 90, at=at(), limit=2)
    assert all(store.get_run(conn, m.run_id) is None for m in made)
    assert conn.execute("SELECT count(*) FROM run_events").fetchone()[0] == 0


def test_a_newer_store_than_version_2_is_refused(tmp_path):
    db = tmp_path / "launcher.db"
    store.connect(db).close()
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA user_version = 3")
    raw.close()
    with pytest.raises(core_store.StoreTooNew):
        store.connect(db)
