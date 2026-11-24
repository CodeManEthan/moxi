"""The session table: its writes, the two start-list queries, the rebuild's removal, and
the time bounds on a call that must not wait."""

import sqlite3
import time

import pytest
from moxi import store

from moxi_memory import state, table

ME = ("claude", "me")


@pytest.fixture
def conn():
    c = state.connect()
    yield c
    c.close()


def listed(conn, harness, sid, *, label="proj", identity=None, kind=None, newest=10.0,
           settled=True):
    """A row as a child leaves it after reading a model answer."""
    table.write_start(conn, harness, sid, label, now=1.0)
    if identity is not None:
        table.write_identity(conn, harness, sid, identity, now=1.0)
    table.write_read(conn, harness, sid, newest=newest, kind=kind, settled=settled, now=2.0)


# -- the store ------------------------------------------------------------------------

def test_the_store_lives_in_memorys_state_dir(conn):
    from moxi.paths import Layout

    assert state.path() == Layout("memory").state_dir() / "state.db"
    assert state.path().is_file()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(state.SCHEMA)


def test_a_store_that_isnt_there_is_not_made_without_create():
    with pytest.raises(store.StoreError):
        state.connect(create=False)
    assert not state.path().exists()


# -- the listener's writes ------------------------------------------------------------

def test_a_start_writes_the_row_with_its_label_and_the_time(conn):
    table.write_start(conn, "claude", "s1", "alpha", now=100.0)
    row = table.row(conn, "claude", "s1")
    assert row == table.Row("claude", "s1", None, "alpha", None, settled=False, newest=100.0,
                            written=100.0)


def test_a_later_start_keeps_the_label_and_the_newest_time(conn):
    table.write_start(conn, "claude", "s1", "alpha", now=100.0)
    table.write_read(conn, "claude", "s1", newest=150.0, kind="seat", settled=True, now=151.0)
    table.write_start(conn, "claude", "s1", "beta", now=200.0)
    row = table.row(conn, "claude", "s1")
    assert (row.label, row.kind, row.settled, row.newest, row.written) == \
        ("alpha", "seat", True, 150.0, 200.0)


def test_a_start_fills_a_label_the_row_lacks(conn):
    table.write_start(conn, "codex", "s1", None, now=100.0)
    assert table.row(conn, "codex", "s1").label is None
    table.write_start(conn, "codex", "s1", "alpha", now=110.0)
    assert table.row(conn, "codex", "s1").label == "alpha"


def test_the_identity_at_a_start_replaces_the_stored_one(conn):
    table.write_start(conn, "claude", "s1", "alpha", now=100.0)
    assert table.write_identity(conn, "claude", "s1", "planner@moxi", now=101.0)
    assert table.write_identity(conn, "claude", "s1", "builder", now=102.0)
    assert table.row(conn, "claude", "s1").identity == "builder"


@pytest.mark.parametrize("empty", [None, "", "  "])
def test_an_empty_identity_is_never_written(conn, empty):
    table.write_start(conn, "claude", "s1", "alpha", now=100.0)
    table.write_identity(conn, "claude", "s1", "planner", now=101.0)
    assert not table.write_identity(conn, "claude", "s1", empty, now=102.0)
    assert not table.write_late_identity(conn, "claude", "s1", empty, now=102.0)
    assert table.row(conn, "claude", "s1").identity == "planner"


def test_an_identity_for_a_session_with_no_row_writes_nothing(conn):
    assert not table.write_identity(conn, "claude", "ghost", "planner")
    assert table.row(conn, "claude", "ghost") is None


# The late-identity rule: a late answer writes only the row of its own session id, only
# when that row exists and still holds no identity, and never an empty identity.

def test_a_late_identity_fills_its_own_rows_empty_identity(conn):
    table.write_start(conn, "claude", "s1", "alpha", now=100.0)
    table.write_start(conn, "claude", "s2", "alpha", now=100.0)
    assert table.write_late_identity(conn, "claude", "s1", "planner", now=105.0)
    assert table.row(conn, "claude", "s1").identity == "planner"
    assert table.row(conn, "claude", "s2").identity is None


def test_a_late_identity_never_replaces_one_a_later_start_wrote(conn):
    table.write_start(conn, "claude", "s1", "alpha", now=100.0)
    table.write_identity(conn, "claude", "s1", "builder", now=110.0)
    assert not table.write_late_identity(conn, "claude", "s1", "planner", now=111.0)
    assert table.row(conn, "claude", "s1").identity == "builder"


def test_a_late_identity_never_makes_a_row_a_rebuild_removed(conn):
    table.write_start(conn, "claude", "s1", "alpha", now=100.0)
    assert table.remove_unseen(conn, [], started=200.0) == 1
    assert not table.write_late_identity(conn, "claude", "s1", "planner", now=201.0)
    assert table.row(conn, "claude", "s1") is None


# -- the child's writes ---------------------------------------------------------------

def test_a_child_makes_a_row_for_a_session_no_listener_saw(conn):
    table.write_read(conn, "codex", "s1", newest=50.0, label="alpha", kind=None,
                     settled=True, now=60.0)
    assert table.row(conn, "codex", "s1") == \
        table.Row("codex", "s1", None, "alpha", None, settled=True, newest=50.0, written=60.0)


def test_a_child_writes_the_label_and_kind_only_where_none_is_stored(conn):
    table.write_read(conn, "claude", "s1", newest=50.0, label="alpha", kind="seat", now=60.0)
    table.write_read(conn, "claude", "s1", newest=70.0, label="beta", kind="scratch",
                     now=80.0)
    row = table.row(conn, "claude", "s1")
    assert (row.label, row.kind, row.newest) == ("alpha", "seat", 70.0)


def test_a_kind_learned_after_settling_with_none_is_stored(conn):
    table.write_read(conn, "claude", "s1", newest=50.0, kind=None, settled=True, now=60.0)
    table.write_read(conn, "claude", "s1", newest=55.0, kind="scratch", settled=True,
                     now=61.0)
    assert table.row(conn, "claude", "s1").kind == "scratch"


def test_the_newest_time_only_moves_forward(conn):
    table.write_read(conn, "claude", "s1", newest=50.0, now=60.0)
    table.write_read(conn, "claude", "s1", newest=40.0, now=61.0)
    assert table.row(conn, "claude", "s1").newest == 50.0


def test_a_settled_kind_stays_settled(conn):
    table.write_read(conn, "claude", "s1", newest=50.0, settled=False, now=60.0)
    assert not table.row(conn, "claude", "s1").settled
    table.write_read(conn, "claude", "s1", newest=51.0, settled=True, now=61.0)
    table.write_read(conn, "claude", "s1", newest=52.0, settled=False, now=62.0)
    assert table.row(conn, "claude", "s1").settled


def test_a_relabel_changes_the_label_and_nothing_else(conn):
    listed(conn, "claude", "s1", label="old", identity="planner", kind="seat", newest=50.0)
    before = table.row(conn, "claude", "s1")
    assert table.relabel(conn, "claude", "s1", "new", now=99.0)
    after = table.row(conn, "claude", "s1")
    assert after == before._replace(label="new", written=99.0)
    assert not table.relabel(conn, "claude", "s1", " ")
    assert not table.relabel(conn, "claude", "ghost", "new")


def test_a_rebuild_removes_unseen_rows_but_not_rows_written_after_it_started(conn):
    table.write_read(conn, "claude", "seen", newest=1.0, now=10.0)
    table.write_read(conn, "claude", "gone", newest=1.0, now=10.0)
    table.write_read(conn, "codex", "gone", newest=1.0, now=10.0)
    table.write_start(conn, "claude", "began-during", "alpha", now=25.0)
    removed = table.remove_unseen(conn, [("claude", "seen"), ("codex", "other")],
                                  started=20.0)
    assert removed == 2
    left = {(r[0], r[1]) for r in conn.execute("SELECT harness, session_id FROM sessions")}
    assert left == {("claude", "seen"), ("claude", "began-during")}
    assert table.remove_unseen(conn, [("claude", "seen")], started=20.0) == 0


# -- the start lists ------------------------------------------------------------------

def test_the_agent_list_matches_the_qualified_identity_from_any_project(conn):
    listed(conn, "claude", "a1", label="alpha", identity="planner@moxi", newest=10.0)
    listed(conn, "codex", "a2", label="beta", identity="planner@moxi", newest=20.0)
    listed(conn, "claude", "b1", label="alpha", identity="planner", newest=30.0)
    listed(conn, "claude", "b2", label="alpha", identity="planner@core", newest=40.0)
    rows = table.agent_sessions(conn, "planner@moxi", ME)
    assert [(r.harness, r.session_id) for r in rows] == [("codex", "a2"), ("claude", "a1")]
    assert [r.session_id for r in table.agent_sessions(conn, "planner", ME)] == ["b1"]
    assert table.agent_sessions(conn, "", ME) == []


def test_both_lists_leave_out_this_session_unsettled_scratch_and_fork_sessions(conn):
    listed(conn, "claude", "me", identity="planner", newest=99.0)
    listed(conn, "codex", "me", identity="planner", newest=98.0)  # same id, other harness
    listed(conn, "claude", "scratch", identity="planner", kind="scratch", newest=97.0)
    listed(conn, "claude", "fork", identity="planner", kind="fork", newest=97.5)
    listed(conn, "claude", "unsettled", identity="planner", settled=False, newest=96.0)
    table.write_start(conn, "claude", "unread", "proj", now=95.0)
    table.write_identity(conn, "claude", "unread", "planner", now=95.0)
    for kind in ("project", "seat", "one-off", None):
        listed(conn, "claude", f"kind-{kind}", identity="planner", kind=kind, newest=50.0)
    expected = {("codex", "me"), ("claude", "kind-project"), ("claude", "kind-seat"),
                ("claude", "kind-one-off"), ("claude", "kind-None")}
    agent = table.agent_sessions(conn, "planner", ME)
    assert {(r.harness, r.session_id) for r in agent} == expected
    project = table.project_sessions(conn, "proj", ME)
    assert {(r.harness, r.session_id) for r in project} == expected


def test_the_project_list_leaves_out_the_agent_lists_sessions(conn):
    for i in range(8):
        listed(conn, "claude", f"mine-{i}", identity="planner", newest=100.0 + i)
    for i in range(3):
        listed(conn, "codex", f"other-{i}", identity=None, newest=50.0 + i)
    first = table.agent_sessions(conn, "planner", ME, limit=5)
    assert [r.session_id for r in first] == [f"mine-{i}" for i in (7, 6, 5, 4, 3)]
    second = table.project_sessions(conn, "proj", ME, limit=5,
                                    leave_out=[(r.harness, r.session_id) for r in first])
    assert [r.session_id for r in second] == ["mine-2", "mine-1", "mine-0", "other-2",
                                              "other-1"]
    assert second[-1] == table.Listed("codex", "other-1", 51.0, None)


def test_each_list_returns_at_most_ten_rows_newest_first(conn):
    for i in range(15):
        listed(conn, "claude", f"s{i:02}", identity="planner", newest=float(i))
    agent = table.agent_sessions(conn, "planner", ME, limit=50)
    project = table.project_sessions(conn, "proj", ME, limit=50)
    assert len(agent) == len(project) == table.MAX_ROWS == 10
    assert [r.newest for r in agent] == sorted((r.newest for r in agent), reverse=True)
    assert agent[0].session_id == "s14"


def test_the_list_queries_read_the_listable_index(conn):
    def plan(sql, params):
        return " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))

    agent = plan("SELECT harness, session_id, newest, identity FROM sessions "
                 "WHERE identity = ? AND settled = 1 AND kind IS NOT 'scratch' "
                 "AND kind IS NOT 'fork' "
                 "ORDER BY newest DESC LIMIT 10", ("planner",))
    project = plan("SELECT harness, session_id, newest, identity FROM sessions "
                   "WHERE label = ? AND settled = 1 AND kind IS NOT 'scratch' "
                   "AND kind IS NOT 'fork' "
                   "ORDER BY newest DESC LIMIT 10", ("proj",))
    assert "USING INDEX sessions_by_identity" in agent and "TEMP B-TREE" not in agent
    assert "USING INDEX sessions_by_label" in project and "TEMP B-TREE" not in project
    # The queries themselves name these indexes, and SQLite refuses a query the named
    # index can't serve, so a passing call is a call the index served.
    listed(conn, "claude", "s1", identity="planner")
    assert table.agent_sessions(conn, "planner", ME)
    assert table.project_sessions(conn, "proj", ME)


# -- the bounds -----------------------------------------------------------------------

def test_a_write_against_a_held_store_gives_up_inside_its_bound(conn):
    holder = state.connect()
    holder.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        with pytest.raises(state.Late):
            with state.bounded(conn, 0.25):
                table.write_start(conn, "claude", "s1", "alpha")
        took = time.monotonic() - started
        assert 0.2 <= took < 1.0
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert table.row(conn, "claude", "s1") is None
    table.write_start(conn, "claude", "s1", "alpha")  # the connection works on afterwards
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_an_open_with_a_short_timeout_against_a_held_store_is_late(conn):
    holder = state.connect()
    holder.execute("BEGIN EXCLUSIVE")
    try:
        quick = state.connect(timeout=0.1)  # the schema is in place: nothing to wait for
        quick.close()
        holder.execute("ROLLBACK")
        holder.execute("PRAGMA user_version = 0")  # as if the schema were still to come
        holder.execute("BEGIN EXCLUSIVE")
        with pytest.raises(state.Late):
            state.connect(timeout=0.1)
    finally:
        if holder.in_transaction:
            holder.execute("ROLLBACK")
        holder.execute(f"PRAGMA user_version = {len(state.SCHEMA)}")
        holder.close()


def test_a_query_past_its_bound_is_interrupted_by_the_progress_handler(conn):
    slow = ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
            "SELECT count(*) FROM n")
    started = time.monotonic()
    with pytest.raises(state.Late):
        with state.bounded(conn, 0.1):
            conn.execute(slow).fetchone()
    assert time.monotonic() - started < 1.0
    assert conn.execute("SELECT 1").fetchone() == (1,)  # the handler is gone again


def test_a_bounded_call_that_finishes_in_time_returns_its_answer(conn):
    listed(conn, "claude", "s1", identity="planner")
    with state.bounded(conn, 0.25):
        rows = table.agent_sessions(conn, "planner", ME)
    assert [r.session_id for r in rows] == ["s1"]


def test_a_late_write_in_a_transaction_is_rolled_back(conn):
    with pytest.raises(state.Late):
        with state.bounded(conn, 0.05):
            conn.execute("BEGIN IMMEDIATE")
            table.write_start(conn, "claude", "s1", "alpha")
            conn.execute("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
                         "SELECT count(*) FROM n").fetchone()
    assert not conn.in_transaction
    assert table.row(conn, "claude", "s1") is None


def test_an_error_that_isnt_a_bound_passes_through(conn):
    with pytest.raises(sqlite3.OperationalError):
        with state.bounded(conn, 1.0):
            conn.execute("SELECT * FROM no_such_table")


# -- the rebuild entry ----------------------------------------------------------------

def test_the_rebuild_entry_is_written_read_and_cleared(conn):
    assert state.read_entry(conn) is None
    state.write_entry(conn, 123.5, 4242)
    state.write_entry(conn, 124.5, 4343)
    assert state.read_entry(conn) == state.Entry(124.5, 4343)
    state.clear_entry(conn)
    assert state.read_entry(conn) is None
