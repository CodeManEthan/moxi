"""The index's store: where it lives, its schema steps, meta, files rows and request rows."""

import os
import stat
import sqlite3

import pytest

import moxi.store
from moxi.paths import Layout
from moxi_sessions import api as sessions
from moxi_usage import keys, store

UNKNOWN = sessions.UNKNOWN
Request = sessions.Request
ClaudeTranscript = sessions.testing.ClaudeTranscript
usage = sessions.testing.usage


@pytest.fixture
def conn():
    connection = store.open_index()
    yield connection
    connection.close()


def add_file(conn, path="/p/a.jsonl", *, lane="claude", **fields):
    values = {"harness": "claude", "rank": 0, "kind": "main", "read_at": 1.0, **fields}
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        file_id = store.upsert_file(conn, lane=lane, path=path, touched=touched, **values)
    return file_id


# --- where it lives and how it opens --------------------------------------------------

def test_the_index_is_state_usage_index_db_private_and_wal():
    conn = store.open_index()
    try:
        path = Layout("usage").state_dir() / "index.db"
        assert store.index_path() == path and path.exists()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.lock_path() == path.with_name("index.lock")
    finally:
        conn.close()


def test_migrating_an_empty_store_creates_the_schema_and_records_the_version(tmp_path):
    conn = store.connect(path=tmp_path / "index.db")
    try:
        assert store.lanes_with_rows(conn, ["archive:x"]) == []     # before any schema
        result = store.migrate(conn)
        assert result == store.Migration(0, store.INDEX_VERSION)
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view', 'index')")}
        assert {"files", "requests", "meta", "claimed", "requests_ts", "requests_key",
                "requests_won_ts", "files_session"} <= names
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.INDEX_VERSION
        assert store.get_meta(conn, store.META_INDEX_VERSION) == str(store.INDEX_VERSION)
    finally:
        conn.close()


def test_migrating_an_existing_store_keeps_its_rows(tmp_path):
    path = tmp_path / "index.db"
    conn = store.open_index(path=path)
    file_id = add_file(conn)
    store.set_meta(conn, store.META_LAST_PASS, "123.5")
    conn.close()
    conn = store.open_index(path=path)
    try:
        assert store.migrate(conn) == store.Migration(store.INDEX_VERSION, store.INDEX_VERSION)
        assert store.get_file(conn, lane="claude", path="/p/a.jsonl")["id"] == file_id
        assert store.get_meta(conn, store.META_LAST_PASS) == "123.5"
    finally:
        conn.close()


def test_a_later_step_runs_on_an_existing_store_and_bumps_index_version(conn):
    add_file(conn)
    steps = (*store.STEPS, store.Step("ALTER TABLE files ADD COLUMN extra_fact TEXT"))
    result = store.migrate(conn, steps=steps)
    assert (result.before, result.after) == (store.INDEX_VERSION, store.INDEX_VERSION + 1)
    assert store.get_meta(conn, store.META_INDEX_VERSION) == str(store.INDEX_VERSION + 1)
    assert "extra_fact" in {row[1] for row in conn.execute("PRAGMA table_info(files)")}
    assert len(store.files(conn)) == 1


def test_version_2_keeps_every_row_and_settles_only_the_excluded_ones(tmp_path):
    conn = store.connect(path=tmp_path / "index.db")
    try:
        store.migrate(conn, steps=store.STEPS[:1])
        kept = add_file(conn, "/c/kept.jsonl", lane="codex")
        skipped = add_file(conn, "/c/skipped.jsonl", lane="codex", excluded="imported")
        with moxi.store.transaction(conn):
            conn.execute("INSERT INTO requests (file_id, key, seq, sidechain, synthetic, won)"
                         " VALUES (?, 1, 1, 0, 0, 1)", (kept,))
        # a step that drops no row runs while an archive lane is offline
        result = store.migrate(conn, offline=["archive:old"])
        assert (result.before, result.after) == (1, 2) and result.dropped == ()
        flags = {f["path"]: f["marks_read"] for f in store.files(conn)}
        assert flags == {"/c/kept.jsonl": 0, "/c/skipped.jsonl": 1}
        assert [tuple(r) for r in conn.execute("SELECT key, context_window FROM requests")] == [
            (1, None)]
        assert conn.execute("SELECT count(*) FROM marks").fetchone()[0] == 0
        assert store.check(conn) == [] and skipped
    finally:
        conn.close()


def test_a_marker_read_twice_is_stored_once_and_goes_with_its_file(conn):
    file_id = add_file(conn)
    first = store.MarkRow(seq=7, kind="compaction", ts=10.0, trigger=None, pre_tokens=None)
    again = first._replace(trigger="auto", pre_tokens=150_000)
    with moxi.store.transaction(conn):
        store.upsert_marks(conn, file_id, [first])
        store.upsert_marks(conn, file_id, [again])
    assert [tuple(r) for r in conn.execute(
        "SELECT seq, kind, ts, trigger, pre_tokens FROM marks")] == [
        (7, "compaction", 10.0, "auto", 150_000)]
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        store.delete_files(conn, [file_id], touched=touched)
    assert conn.execute("SELECT count(*) FROM marks").fetchone()[0] == 0


def test_a_step_that_drops_rows_refuses_while_an_archive_lane_holding_rows_is_offline(conn):
    add_file(conn, "/arch/a.jsonl", lane="archive:old", rank=3)
    add_file(conn, "/live/a.jsonl")
    steps = (*store.STEPS, store.Step("DELETE FROM requests; DELETE FROM files", drops=True))
    with pytest.raises(store.MigrationRefused) as caught:
        store.migrate(conn, offline=["archive:old", "archive:empty"], steps=steps)
    assert caught.value.lanes == ("archive:old",) and caught.value.exit_code == 3
    assert len(store.files(conn)) == 2
    assert store.get_meta(conn, "index_version") == str(store.INDEX_VERSION)
    # an offline lane with no rows refuses nothing
    assert store.migrate(conn, offline=["archive:empty"],
                         steps=(*store.STEPS, store.Step("SELECT 1", drops=True))).after == (
        store.INDEX_VERSION + 1)


def test_force_runs_a_dropping_step_and_names_the_lanes_it_dropped(conn):
    add_file(conn, "/arch/a.jsonl", lane="archive:old", rank=3)
    steps = (*store.STEPS, store.Step("DELETE FROM requests; DELETE FROM files", drops=True))
    result = store.migrate(conn, offline=["archive:old"], force=True, steps=steps)
    assert result == store.Migration(store.INDEX_VERSION, store.INDEX_VERSION + 1,
                                     ("archive:old",))
    assert store.files(conn) == []


def test_a_store_from_a_newer_version_is_refused(conn):
    store.migrate(conn, steps=(*store.STEPS, store.Step("SELECT 1")))
    with pytest.raises(moxi.store.StoreTooNew):
        store.migrate(conn)


def test_the_view_orders_the_claim_as_the_claim_step_does(conn):
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'claimed'").fetchone()[0]
    assert f"ORDER BY {store.CLAIM_ORDER})" in " ".join(sql.split())


# --- meta -------------------------------------------------------------------------------

def test_meta_values_set_replace_and_remove(conn):
    store.set_meta(conn, store.META_LANES, '["claude"]')
    store.set_meta(conn, store.META_LANES, '["claude", "codex"]')
    key = store.cursor_meta_key("opencode", "/oc/opencode.db")
    store.set_meta(conn, key, "c1")
    assert store.all_meta(conn) == {"index_version": str(store.INDEX_VERSION), "lanes": '["claude", "codex"]',
                                    key: "c1"}
    store.set_meta(conn, key, None)
    assert store.get_meta(conn, key, "none") == "none"


# --- files rows -------------------------------------------------------------------------

def test_upsert_file_inserts_then_updates_only_what_it_is_given(conn):
    file_id = add_file(conn, cwd="/w", title="first", size=10)
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        again = store.upsert_file(conn, lane="claude", path="/p/a.jsonl", touched=touched,
                                  title="second", held="root offline")
    row = store.get_file(conn, lane="claude", path="/p/a.jsonl")
    assert again == file_id
    assert (row["cwd"], row["title"], row["size"], row["held"]) == ("/w", "second", 10,
                                                                   "root offline")
    assert row["part"] == "" and row["excluded"] is None


def test_files_rows_share_a_path_by_part(conn):
    for part in ("ses_1", "ses_2"):
        add_file(conn, "/oc/opencode.db", lane="opencode", part=part, harness="opencode",
                 rank=2)
    add_file(conn)
    rows = store.files(conn, path="/oc/opencode.db")
    assert [row["part"] for row in rows] == ["ses_1", "ses_2"]
    assert len(store.files(conn, lane="claude")) == 1 and len(store.files(conn)) == 3


def test_an_unknown_column_is_refused(conn):
    with moxi.store.transaction(conn), pytest.raises(ValueError, match="sizes"):
        store.upsert_file(conn, lane="claude", path="/p", touched=set(), sizes=1)


def test_a_write_outside_a_transaction_is_refused(conn):
    with pytest.raises(moxi.store.StoreError, match="transaction"):
        store.upsert_file(conn, lane="claude", path="/p", touched=set(), harness="claude",
                          rank=0, kind="main", read_at=1.0)
    with pytest.raises(moxi.store.StoreError):
        store.claim(conn, {1})


def test_deleting_a_file_deletes_its_requests(conn):
    file_id = add_file(conn)
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        store.upsert_requests(conn, file_id, [row(5), row(6)], touched=touched)
    touched.clear()
    with moxi.store.transaction(conn):
        assert store.delete_files(conn, [file_id], touched=touched) == 1
    assert touched == {5, 6}
    assert conn.execute("SELECT count(*) FROM requests").fetchone()[0] == 0


# --- request rows -----------------------------------------------------------------------

def row(key, *, seq=1, ts=100.0, **fields) -> store.RequestRow:
    base = dict(key=key, seq=seq, ts=ts, model="m", response_model=None, provider=None,
                input=10, cache_read=0, cache_write=0, cache_write_1h=0, output=5,
                reasoning=None, service_tier=None, speed=None, harness_cost=None,
                sidechain=0, synthetic=0, turn_id=None, agent_id=None, unknown=None)
    return store.RequestRow(**{**base, **fields})


def test_a_request_becomes_a_row_with_unknown_counts_null_and_named():
    request = Request(harness="codex", request_id="tc:main:12", at={"line": 12}, time=50.5,
                      model="gpt-x", provider="openai", input_tokens=900,
                      cache_read_input_tokens=0, cache_creation_input_tokens=UNKNOWN,
                      output_tokens=40, reasoning_output_tokens=None,
                      service_tier="priority", turn_id="t1")
    got = store.request_row(request, lane="codex", path="/r.jsonl")
    assert got.key == keys.request_key("codex", "tc:main:12", lane="codex", path="/r.jsonl")
    assert (got.seq, got.ts, got.model, got.provider) == (12, 50.5, "gpt-x", "openai")
    assert (got.input, got.cache_read, got.cache_write, got.cache_write_1h) == (900, 0, None,
                                                                                None)
    assert (got.output, got.reasoning) == (40, None)
    assert got.unknown == "cache_write,cache_write_1h,reasoning"
    assert store.unknown_columns(got.unknown) == ["cache_write", "cache_write_1h",
                                                  "reasoning"]
    assert (got.service_tier, got.speed, got.turn_id, got.harness_cost) == ("priority", None,
                                                                           "t1", None)
    assert (got.sidechain, got.synthetic, got.response_model) == (0, 0, None)
    assert got.context_window is None
    reported = Request(harness="codex", request_id="r", context_window=258_400)
    assert store.request_row(reported, lane="codex", path="/r").context_window == 258_400


def test_an_opencode_request_is_ordered_by_its_start_in_milliseconds():
    request = Request(harness="opencode", request_id="prt_01", at={"table": "part",
                                                                  "id": "prt_01"},
                      time=20.0, start_time=12.3456, input_tokens=1, output_tokens=1,
                      cache_read_input_tokens=0, cache_creation_input_tokens=0,
                      cache_creation_1h_input_tokens=UNKNOWN, reasoning_output_tokens=0,
                      cost_usd=0.25, sidechain=False, synthetic=False)
    got = store.request_row(request, lane="opencode", path="/oc/opencode.db")
    assert got.seq == 12346 and got.harness_cost == 0.25 and got.unknown == "cache_write_1h"
    no_start = Request(harness="opencode", request_id="prt_02", time=20.0)
    assert store.request_row(no_start, lane="opencode", path="/oc").seq == 20000


def test_a_claude_transcript_read_by_sessions_gives_rows_keyed_and_ordered(tmp_path):
    t = ClaudeTranscript(tmp_path / "projects")
    t.user("hello")
    t.assistant("one", usage=usage(input=3, cache_read=900, cache_write=50,
                                   cache_write_1h=20, output=40))
    t.user("again")
    t.assistant("two", request_id=None, message_id=None, usage=usage(output=7))
    path = str(t.write())
    found = list(sessions.records(sessions.Source("claude", path, t.session_id),
                                  kinds={"request"}))
    rows = [store.request_row(r, lane="claude", path=path) for r in found]
    assert [r.seq for r in rows] == [r.at["line"] for r in found]
    assert rows[0].seq < rows[1].seq
    assert rows[0].key == keys.request_key("claude", found[0].request_id, lane="claude",
                                           path=path)
    assert (rows[0].cache_read, rows[0].cache_write, rows[0].cache_write_1h,
            rows[0].output) == (900, 50, 20, 40)
    assert rows[0].sidechain == 0 and rows[0].synthetic == 0


def test_a_request_that_comes_again_replaces_its_copy(conn):
    file_id = add_file(conn)
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        store.upsert_requests(conn, file_id, [row(7, output=5)], touched=touched)
        store.claim(conn, touched)
    with moxi.store.transaction(conn):
        store.upsert_requests(conn, file_id, [row(7, seq=3, output=9)], touched=touched)
        store.claim(conn, touched)
    rows = conn.execute("SELECT key, seq, output, won FROM requests").fetchall()
    assert [tuple(r) for r in rows] == [(7, 3, 9, 1)]


def test_a_reset_deletes_a_files_requests_and_keeps_the_row(conn):
    file_id = add_file(conn)
    touched: set[int] = set()
    with moxi.store.transaction(conn):
        store.upsert_requests(conn, file_id, [row(1), row(2)], touched=touched)
    touched.clear()
    with moxi.store.transaction(conn):
        assert store.delete_requests(conn, [file_id], touched=touched) == 2
    assert touched == {1, 2} and len(store.files(conn)) == 1
    assert store.file_keys(conn, file_id) == []


def test_a_rollback_keeps_nothing_of_the_transaction(conn):
    file_id = add_file(conn)
    touched: set[int] = set()
    with pytest.raises(RuntimeError), moxi.store.transaction(conn):
        store.upsert_requests(conn, file_id, [row(1)], touched=touched)
        store.update_file(conn, file_id, touched=touched, cursor="c2")
        raise RuntimeError("crash mid-source")
    assert store.file_keys(conn, file_id) == []
    assert store.get_file(conn, lane="claude", path="/p/a.jsonl")["cursor"] is None


def test_rows_read_by_name_or_place(conn):
    add_file(conn)
    found = conn.execute("SELECT lane, path FROM files").fetchone()
    assert isinstance(found, sqlite3.Row)
    assert found["lane"] == found[0] == "claude"
