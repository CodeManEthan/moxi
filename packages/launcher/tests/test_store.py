"""The launch log: its schema, the insert and a repeated request, state writes that only go
forward in every order, session ids and the resolver, the log read and picks."""

from __future__ import annotations

import itertools
import json
import os
import sqlite3
import stat
from datetime import datetime, timedelta, timezone

import pytest
from moxi import store as core_store
from moxi.paths import Layout

from moxi_launcher import store

CLAUDE_ID = "0b6f4a52-6a1d-4d0e-9a51-0c7f6a3d2e11"


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    yield connection
    connection.close()


def claude(conn, request_id="r1", session_id=CLAUDE_ID, **overrides):
    args = {"request_id": request_id, "actor": "someone", "harness": "claude",
            "target": "slot", "cwd": "/work", "values": {"model": "opus", "effort": "high"},
            "argv": ["claude", "Brief from launcher", "--session-id", session_id],
            "env": {"MOXI_LAUNCHER_REQUEST": request_id}, "posture": "operator",
            "session_id": session_id, "preset_kind": "seat", "preset_name": "lead"}
    args.update(overrides)
    return store.insert(conn, **args)


def codex(conn, request_id="c1", **overrides):
    args = {"request_id": request_id, "actor": "someone", "harness": "codex",
            "target": "terminal", "cwd": "/work", "values": {"sandbox": "workspace-write"},
            "argv": ["moxi-turns-codex-host", "--cwd", "/work"],
            "env": {"MOXI_LAUNCHER_REQUEST": request_id}, "posture": "go"}
    args.update(overrides)
    return store.insert(conn, **args)


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# -- schema ------------------------------------------------------------------------------


def test_a_new_store_is_version_one_in_wal_and_private(tmp_path):
    db = tmp_path / "state" / "launcher.db"
    conn = store.connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {name for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert tables == {"launches", "session_ids", "events", "picks"}
        indexes = {name for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
        assert len(indexes) == 5
    finally:
        conn.close()
    if os.name == "posix":
        assert stat.S_IMODE(db.stat().st_mode) == 0o600


def test_the_launches_columns_are_the_ones_part_two_reads(conn):
    columns = [row[1] for row in conn.execute("PRAGMA table_info(launches)")]
    assert columns == list(store.COLUMNS)
    assert columns[0] == "request_id" and columns[-1] == "updated_at"


def test_opening_again_runs_no_step(tmp_path):
    db = tmp_path / "launcher.db"
    store.connect(db).close()
    conn = store.connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_store_from_a_newer_launcher_is_refused(tmp_path):
    db = tmp_path / "launcher.db"
    store.connect(db).close()
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA user_version = 2")
    raw.close()
    with pytest.raises(core_store.StoreTooNew) as caught:
        store.connect(db)
    assert caught.value.exit_code == 3


def test_the_default_path_is_the_declared_store():
    assert store.path() == Layout("launcher").state_dir() / "launcher.db"


# -- insert ------------------------------------------------------------------------------


def test_insert_writes_a_spawning_row_and_the_minted_id(conn):
    inserted = claude(conn, brief="do the thing", handoff_path="/h/2026-10-30-lead.md",
                      handoff_key="lead")
    assert inserted.new
    row = store.get(conn, "r1")
    assert row == inserted.row
    assert row.state == "spawning" and row.reason is None
    assert row.created_at == row.updated_at and row.created_at.endswith("Z")
    assert row.values == {"model": "opus", "effort": "high"}
    assert row.argv[:2] == ["claude", "Brief from launcher"]
    assert row.env == {"MOXI_LAUNCHER_REQUEST": "r1"}
    assert row.relaunch_of is None and row.resume_of is None and row.endpoint is None
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]
    assert conn.execute("SELECT path, key, session_id, request_id, how FROM picks"
                        ).fetchall() == [("/h/2026-10-30-lead.md", "lead", CLAUDE_ID, "r1",
                                          "given")]


def test_insert_leaves_the_connection_at_normal_sync(conn):
    claude(conn)
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL


def test_a_codex_launch_has_no_session_id_yet(conn):
    codex(conn)
    assert store.get(conn, "c1").session_id is None
    assert count(conn, "session_ids") == 0


def test_a_handoff_with_no_key_writes_no_pick(conn):
    claude(conn, handoff_path="/h/x.md")
    assert store.get(conn, "r1").handoff_path == "/h/x.md"
    assert count(conn, "picks") == 0


def test_a_repeated_request_writes_nothing_and_returns_the_stored_row(conn):
    first = claude(conn, handoff_path="/h/x.md", handoff_key="lead")
    store.set_state(conn, "r1", "started", pid=4242)
    again = claude(conn, session_id="9d4f0c1e-0000-4000-8000-000000000001",
                   values={"model": "other"}, handoff_path="/h/y.md", handoff_key="lead")
    assert not again.new
    assert again.row.request_id == "r1"
    assert again.row.state == "started" and again.row.pid == 4242
    assert again.row.values == first.row.values
    assert count(conn, "launches") == 1
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted")]
    assert count(conn, "picks") == 1


def test_a_claude_launch_needs_its_session_id(conn):
    with pytest.raises(ValueError):
        claude(conn, session_id=None)
    assert count(conn, "launches") == 0


@pytest.mark.parametrize("field, value", [("harness", "gemini"), ("target", "tmux"),
                                          ("preset_kind", "blank")])
def test_insert_refuses_a_name_it_does_not_know(conn, field, value):
    with pytest.raises(ValueError):
        claude(conn, **{field: value})
    assert count(conn, "launches") == 0


def test_a_session_id_another_launch_holds_is_refused(conn):
    claude(conn)
    with pytest.raises(store.SessionTaken) as caught:
        claude(conn, request_id="r2")
    assert caught.value.request_id == "r1" and caught.value.exit_code == 3
    assert count(conn, "launches") == 1


@pytest.mark.parametrize("field", ["brief", "values_json", "argv_json"])
def test_a_value_past_the_cap_is_refused_not_cut(conn, field):
    big = "x" * (store.CAP + 1)
    overrides = {"brief": {"brief": big}, "values_json": {"values": {"note": big}},
                 "argv_json": {"argv": ["claude", big]}}[field]
    with pytest.raises(store.TooLarge) as caught:
        claude(conn, **overrides)
    assert caught.value.field == field and caught.value.exit_code == 3
    assert count(conn, "launches") == 0 and count(conn, "session_ids") == 0


def test_a_brief_of_exactly_the_cap_is_kept_whole(conn):
    brief = "é" * (store.CAP // 2)
    claude(conn, brief=brief)
    assert store.get(conn, "r1").brief == brief


def test_a_write_lock_held_past_the_timeout_is_busy(tmp_path):
    db = tmp_path / "launcher.db"
    holder = store.connect(db)
    waiter = store.connect(db, timeout=0.05)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(store.Busy) as caught:
            claude(waiter)
        assert caught.value.exit_code == 75
        holder.execute("ROLLBACK")
        assert count(waiter, "launches") == 0
        assert not waiter.in_transaction
    finally:
        holder.close()
        waiter.close()


# -- forward-only state writes ---------------------------------------------------------

RANK = {state: index for index, state in enumerate(store.STATES)}
ENDS = {"first-sent", "failed"}

#: The state writes a launch makes, as each step names them.
STEP_WRITES = [
    ("started", ("spawning",)),                      # the start went through
    ("failed", ("spawning", "started")),              # the start failed
    ("ready", ("spawning", "started")),              # the ready recorder
    ("sending", ("spawning", "started", "ready")),   # the sender's claim
    ("first-sent", ("sending",)),                    # the first turn started
    ("failed", ("sending",)),                         # the first turn failed
    ("failed", ("spawning", "started", "ready")),    # not ready in time
]


def moves_forward(before, after):
    if before in ENDS:
        return after == before
    return after == before or after in ENDS or RANK[after] > RANK[before]


def test_every_order_of_the_steps_writes_only_forward(conn):
    for number, order in enumerate(itertools.permutations(STEP_WRITES)):
        request_id = f"p{number}"
        codex(conn, request_id=request_id)
        state = "spawning"
        for new, leaving in order:
            moved = store.set_state(conn, request_id, new, leaving=leaving, reason=new)
            after = store.get(conn, request_id).state
            assert moved == (state in leaving), (order, new, state)
            assert after == (new if moved else state)
            assert moves_forward(state, after), (order, state, after)
            state = after


def test_every_order_of_the_plain_writes_writes_only_forward(conn):
    writes = list(store.LEAVES)
    for length in range(1, len(writes) + 1):
        for number, order in enumerate(itertools.permutations(writes, length)):
            request_id = f"q{length}-{number}"
            codex(conn, request_id=request_id)
            state = "spawning"
            for new in order:
                moved = store.set_state(conn, request_id, new)
                after = store.get(conn, request_id).state
                assert moved == (state in store.LEAVES[new])
                assert moves_forward(state, after), (order, state, after)
                state = after


def test_nothing_leaves_an_end(conn):
    codex(conn, request_id="a")
    codex(conn, request_id="b")
    store.set_state(conn, "a", "failed", reason="exited")
    store.set_state(conn, "b", "sending")
    store.set_state(conn, "b", "first-sent")
    for state in store.LEAVES:
        assert not store.set_state(conn, "a", state)
        assert not store.set_state(conn, "b", state)
    assert store.get(conn, "a").state == "failed"
    assert store.get(conn, "a").reason == "exited"
    assert store.get(conn, "b").state == "first-sent"


def test_a_reason_goes_with_its_state(conn):
    codex(conn)
    assert store.set_state(conn, "c1", "failed", reason="no-ready",
                           leaving=("spawning", "started", "ready"))
    row = store.get(conn, "c1")
    assert (row.state, row.reason) == ("failed", "no-ready")
    assert row.updated_at >= row.created_at


def test_facts_are_written_whatever_the_state(conn):
    claude(conn)
    store.set_state(conn, "r1", "failed", reason="exited")
    assert not store.set_state(conn, "r1", "started", pid=99, pid_started=1.5,
                               pane_id="%3", slot="2")
    row = store.get(conn, "r1")
    assert (row.state, row.pid, row.pid_started, row.pane_id, row.slot) == \
        ("failed", 99, 1.5, "%3", "2")
    assert store.set_facts(conn, "r1", pid=100)
    assert store.get(conn, "r1").pid == 100


def test_a_write_to_a_missing_row_changes_nothing(conn):
    assert not store.set_state(conn, "nobody", "started")
    assert not store.set_facts(conn, "nobody", pid=1)
    assert count(conn, "launches") == 0


def test_a_state_write_may_not_name_a_backward_move(conn):
    codex(conn)
    with pytest.raises(ValueError):
        store.set_state(conn, "c1", "started", leaving=("ready",))
    with pytest.raises(ValueError):
        store.set_state(conn, "c1", "spawning")
    with pytest.raises(ValueError):
        store.set_facts(conn, "c1", endpoint="me")
    assert store.get(conn, "c1").state == "spawning"


# -- session ids and the resolver --------------------------------------------------------


def test_a_claude_launch_resolves_by_its_minted_id(conn):
    claude(conn)
    row = store.resolve(conn, CLAUDE_ID)
    assert row.request_id == "r1"
    assert row.session_id == CLAUDE_ID and row.newest_session_id == CLAUDE_ID


def test_a_cleared_session_resolves_from_either_id_to_the_newest(conn):
    claude(conn, at="2026-10-30T10:00:00.000000Z")
    assert store.learn_session(conn, "r1", CLAUDE_ID, "startup") is False
    assert store.learn_session(conn, "r1", "after-clear", "clear",
                               at="2026-10-30T11:00:00.000000Z")
    assert store.learn_session(conn, "r1", "after-compact", "compact",
                               at="2026-10-30T12:00:00.000000Z")
    for sid in (CLAUDE_ID, "after-clear", "after-compact"):
        row = store.resolve(conn, sid)
        assert row.request_id == "r1"
        assert row.session_id == CLAUDE_ID
        assert row.newest_session_id == "after-compact"
    assert store.session_ids(conn, "r1") == [(CLAUDE_ID, "minted"), ("after-clear", "clear"),
                                            ("after-compact", "compact")]


def test_a_codex_launch_resolves_only_once_its_id_is_learned(conn):
    codex(conn)
    assert store.resolve(conn, "thread-1") is None
    assert store.learn_session(conn, "c1", "thread-1", "sender")
    assert store.get(conn, "c1").session_id == "thread-1"
    assert store.learn_session(conn, "c1", "thread-2", "clear")
    assert store.get(conn, "c1").session_id == "thread-1"
    assert store.resolve(conn, "thread-1").newest_session_id == "thread-2"


def test_learning_an_id_leaves_the_state(conn):
    codex(conn)
    store.set_state(conn, "c1", "failed", reason="exited")
    assert store.learn_session(conn, "c1", "thread-1", "startup")
    row = store.get(conn, "c1")
    assert (row.state, row.session_id) == ("failed", "thread-1")


def test_an_id_held_by_another_launch_stays_with_it(conn):
    claude(conn)
    codex(conn)
    assert not store.learn_session(conn, "c1", CLAUDE_ID, "startup")
    assert store.resolve(conn, CLAUDE_ID).request_id == "r1"


def test_learning_for_a_missing_launch_or_an_unknown_source(conn):
    assert not store.learn_session(conn, "nobody", "x", "startup")
    codex(conn)
    with pytest.raises(ValueError):
        store.learn_session(conn, "c1", "x", "guess")


def test_an_unknown_id_resolves_to_nothing(conn):
    claude(conn)
    assert store.resolve(conn, "not-there") is None


# -- events --------------------------------------------------------------------------------


def test_events_are_kept_in_order_with_their_detail(conn):
    claude(conn)
    first = store.add_event(conn, "screen", {"screen": "trust", "outcome": "answered"},
                            request_id="r1", at="2026-10-30T10:00:01.000000Z")
    second = store.add_event(conn, "gone", request_id="r1", session_id=CLAUDE_ID,
                             at="2026-10-30T10:00:02.000000Z")
    got = store.events(conn, "r1")
    assert [e.id for e in got] == [first, second]
    assert got[0].detail == {"screen": "trust", "outcome": "answered"}
    assert got[1].kind == "gone" and got[1].session_id == CLAUDE_ID and got[1].detail == {}


# -- picks ---------------------------------------------------------------------------------


def test_a_given_handoff_counts_unless_its_launch_failed(conn):
    claude(conn, request_id="ok", session_id="s-ok", handoff_path="/h/a.md",
           handoff_key="lead", at="2026-10-30T10:00:00.000000Z")
    claude(conn, request_id="bad", session_id="s-bad", handoff_path="/h/b.md",
           handoff_key="lead", at="2026-10-30T11:00:00.000000Z")
    assert store.picked(conn, "lead") == ["/h/b.md", "/h/a.md"]
    store.set_state(conn, "bad", "failed", reason="refused")
    assert store.picked(conn, "lead") == ["/h/a.md"]
    store.set_state(conn, "ok", "started")
    assert store.picked(conn, "lead") == ["/h/a.md"]


def test_a_shown_handoff_counts_whatever_the_launch(conn):
    claude(conn, request_id="r1")
    store.add_pick(conn, "/h/c.md", "lead", "shown", session_id=CLAUDE_ID, request_id="r1")
    store.set_state(conn, "r1", "failed", reason="exited")
    assert store.picked(conn, "lead") == ["/h/c.md"]
    assert store.picked(conn, "other") == []
    with pytest.raises(ValueError):
        store.add_pick(conn, "/h/c.md", "lead", "seen")


# -- the log -------------------------------------------------------------------------------


def three(conn):
    for number, hour in enumerate((10, 11, 12)):
        codex(conn, request_id=f"c{number}", at=f"2026-10-30T{hour}:00:00.000000Z")


def test_the_log_is_newest_first_and_pages_by_time(conn):
    three(conn)
    assert [r.request_id for r in store.log(conn)] == ["c2", "c1", "c0"]
    assert [r.request_id for r in store.log(conn, limit=2)] == ["c2", "c1"]
    page = store.log(conn, limit=2)
    assert [r.request_id for r in store.log(conn, before=page[-1].created_at)] == ["c0"]


def test_a_before_time_in_another_spelling_means_the_same_moment(conn):
    three(conn)
    assert [r.request_id for r in store.log(conn, before="2026-10-30T12:00:00Z")] == \
        ["c1", "c0"]
    assert [r.request_id for r in store.log(conn, before="2026-10-30T13:00:00+01:00")] == \
        ["c1", "c0"]
    assert [r.request_id for r in store.log(conn, before="2026-10-31")] == ["c2", "c1", "c0"]
    with pytest.raises(ValueError):
        store.log(conn, before="yesterday")


def test_log_json_expands_the_json_columns_and_marks_stale_rows(tmp_path):
    db = tmp_path / "launcher.db"
    conn = store.connect(db)
    made = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)
    try:
        codex(conn, request_id="old", at=store.stamp(made))
        codex(conn, request_id="done", at=store.stamp(made + timedelta(seconds=1)))
        store.set_state(conn, "done", "started")
        codex(conn, request_id="fresh", at=store.stamp(made + timedelta(seconds=170)))
    finally:
        conn.close()
    lines = store.log_lines(db=db, stale_after=180,
                            when=made + timedelta(seconds=200))
    rows = [json.loads(line) for line in lines]
    assert [r["request_id"] for r in rows] == ["fresh", "done", "old"]
    assert [r["stale"] for r in rows] == [False, False, True]
    assert rows[0]["values_json"] == {"sandbox": "workspace-write"}
    assert rows[0]["argv_json"] == ["moxi-turns-codex-host", "--cwd", "/work"]
    assert rows[0]["env_json"] == {"MOXI_LAUNCHER_REQUEST": "fresh"}
    assert set(rows[0]) == set(store.COLUMNS) | {"stale"}
    assert all("\n" not in line for line in lines)


def test_log_json_on_a_store_not_made_yet_is_empty_and_makes_nothing(tmp_path):
    db = tmp_path / "state" / "launcher.db"
    assert store.log_lines(db=db, stale_after=180) == []
    assert not db.exists()


def test_a_row_reads_stale_only_while_under_way():
    made = datetime(2026, 10, 30, tzinfo=timezone.utc)
    later = made + timedelta(seconds=181)
    base = dict.fromkeys(store.COLUMNS)
    base.update(created_at=store.stamp(made))
    for state in store.STATES:
        row = store.LaunchRow(**{**base, "state": state})
        assert row.stale(180, later) == (state in ("spawning", "sending"))
        assert not row.stale(180, made + timedelta(seconds=179))


def test_the_stale_bound_is_the_ready_and_first_waits():
    assert store.stale_after_s() == 60 + 120
    assert store.stale_after_s({"ready.wait_s": 5, "first.wait_s": 7}) == 12
