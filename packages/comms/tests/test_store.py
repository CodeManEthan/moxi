"""comms.db: its tables, the one opener and its gate, and the read-only opener."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path

import pytest
from moxi.store import transaction

from moxi_comms import bindings, directory, module, paths, provision, store
from moxi_comms.local_record import LocalRecord
from moxi_comms.store import schema

SRC = Path(__file__).resolve().parents[1] / "src" / "moxi_comms"

#: Each table of the store and its columns, in order.
COLUMNS = {
    "meta": ["key", "value"],
    "rooms": ["room_id", "name", "kind", "created_at"],
    "messages": ["id", "room_id", "author_id", "author_kind", "author_display", "content",
                 "timestamp", "interrupt", "expires_at", "withdrawn_at"],
    "queue": ["name", "message_id", "queued_at", "held", "state", "binding_id",
              "digest_id", "digested_once"],
    "digests": ["id", "name", "binding_id", "opened_at", "ids", "text", "started_at",
                "ended_at", "outcome"],
    "cursors": ["name", "message_id", "updated_at"],
    "bindings": ["id", "qualified", "name", "scope", "tier", "adapter", "bound_at",
                 "released_at", "fenced", "kind"],
    "identities": ["name", "scope", "status", "kind", "created_at"],
    "operators": ["user_id", "name", "created_at", "token_hash"],
    "reserved": ["name", "module", "token_hash", "state", "reserved_at", "freed_at",
                 "note"],
    "dropped": ["message_id", "name", "reason", "at"],
    "principals": ["principal_id", "name"],
    "denied": ["principal_id", "channel_id"],
}


def zero_two(*which):
    """Put comms 0.2's files (empty stand-ins) under their 0.2 names."""
    made = []
    for name in which:
        path = {"record": paths.record_db, "directory": paths.directory_db,
                "bindings": paths.bindings_db}[name]()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        made.append(path)
    return made


def mark(target):
    conn = store.open_write(target)
    try:
        conn.execute("INSERT INTO meta(key, value) VALUES (?, '0.2')",
                     (schema.META_MIGRATED_FROM,))
    finally:
        conn.close()


# --------------------------------------------------------------------------- the tables

def test_a_new_store_has_every_table_at_version_one():
    conn = store.open_write()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert tables == set(COLUMNS) == set(schema.TABLES)
        for table, columns in COLUMNS.items():
            assert [r[1] for r in conn.execute(f"PRAGMA table_info({table})")] == columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(schema.STEPS) == 1
        assert conn.execute("SELECT value FROM meta WHERE key = 'next_id'"
                            ).fetchone()[0] == str(2 ** 62)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()
    assert store.path() == paths.state_dir() / "comms.db"
    assert oct(os.stat(store.path()).st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("table, row", [
    ("queue", "INSERT INTO queue(name, message_id, queued_at, state) "
              "VALUES ('a', 1, 't', 'gone')"),
    ("bindings", "INSERT INTO bindings(qualified, name, scope, tier, adapter, bound_at, "
                 "kind) VALUES ('a', 'a', 'global', 'chosen', 'x', 't', 'other')"),
    ("reserved", "INSERT INTO reserved(name, state) VALUES ('console', 'held')"),
    ("dropped", "INSERT INTO dropped(message_id, name, reason, at) "
                "VALUES (1, 'a', 'lost', 't')"),
    ("messages", "INSERT INTO messages(id, room_id, author_id, author_kind, "
                 "author_display, content, timestamp, interrupt) "
                 "VALUES (1, 'r', 'a', 'identity', 'a', 'x', 't', 2)"),
])
def test_the_schema_refuses_a_value_outside_its_set(table, row):
    conn = store.open_write()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(row)
    finally:
        conn.close()


def test_a_send_shaped_write_lands_whole_or_not_at_all():
    """The message, the counter and one queue row per recipient share one transaction."""
    conn = store.open_write()

    def send(fail: bool):
        with transaction(conn):
            conn.execute("UPDATE meta SET value = value + 1 WHERE key = 'next_id'")
            mid = int(conn.execute("SELECT value FROM meta WHERE key = 'next_id'"
                                   ).fetchone()[0])
            conn.execute("INSERT INTO messages(id, room_id, author_id, author_kind, "
                         "author_display, content, timestamp, expires_at) "
                         "VALUES (?, 'r', 'a', 'identity', 'a', 'x', 't', 1900000000)",
                         (mid,))
            for name in ("planner", "reviewer"):
                conn.execute("INSERT INTO queue(name, message_id, queued_at, state, "
                             "binding_id) VALUES (?, ?, 't', 'handed', 7)", (name, mid))
                if fail:
                    raise RuntimeError("injected")

    try:
        with pytest.raises(RuntimeError):
            send(fail=True)
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 0
        assert conn.execute("SELECT value FROM meta WHERE key = 'next_id'"
                            ).fetchone()[0] == str(2 ** 62)
        send(fail=False)
        assert conn.execute("SELECT COUNT(*) FROM queue WHERE state = 'handed'"
                            ).fetchone()[0] == 2
    finally:
        conn.close()


def test_the_record_and_the_directory_share_one_store(tmp_path):
    record = LocalRecord(poll_interval=0.02)
    try:
        made = provision.provision(record, paths.comms_db())
        conn = directory.connect(read_only=True)
        try:
            room = directory.room_by_name(conn, provision.NAMES)
            assert room["room_id"] == made.rooms[provision.NAMES]
            assert room["kind"] == "names"
            assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 1
        finally:
            conn.close()
        assert record.path == paths.comms_db()
    finally:
        record.close()
    assert sorted(p.name for p in paths.state_dir().iterdir()
                  if p.suffix == ".db") == ["comms.db"]
    assert not paths.directory_db().exists() and not paths.bindings_db().exists()


def test_the_binding_history_is_kept_in_the_store():
    history = bindings.BindingHistory()
    assert history.open()
    try:
        row = history.bind(qualified="planner", name="planner", scope="global",
                           tier="chosen", adapter="claude-shim")
        history.release(row)
    finally:
        history.close()
    conn = bindings.connect(read_only=True)
    try:
        got = conn.execute("SELECT kind, released_at IS NOT NULL FROM bindings").fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in got] == [("session", 1)]


def test_comms_declares_its_stores():
    assert module.STORES["comms"] == {
        "root": "state", "path": "comms.db", "kind": "sqlite", "private": True,
        "retention": "keep", "rebuildable": False}
    assert set(module.STORES) == {"comms", "cards", "tokens", "codex", "opencode"}
    for name in ("cards", "tokens", "codex", "opencode"):
        assert module.STORES[name]["kind"] == "dir" and module.STORES[name]["lazy"]


# --------------------------------------------------------------------------- the gate

def test_with_no_0_2_files_the_opener_creates_then_reopens_the_store():
    assert not store.path().exists()
    store.open_write().close()
    assert store.state_of(store.path()) == store.UNMARKED
    conn = store.open_write()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        conn.close()
    assert (paths.state_dir() / store.MIGRATE_LOCK).exists()


@pytest.mark.parametrize("which", [("record",), ("directory",), ("bindings",),
                                   ("record", "directory", "bindings")])
def test_0_2_files_without_a_store_are_not_written_over(which):
    """An empty stand-in isn't a 0.2 database: the move refuses, and writes nothing."""
    made = zero_two(*which)
    with pytest.raises(store.StoreRefused) as e:
        store.open_write()
    assert e.value.path == made[0]
    assert "is empty" in str(e.value) and "nothing was moved" in str(e.value)
    assert not store.path().exists()
    assert all(p.read_bytes() == b"" for p in made)


def test_a_store_without_the_marker_beside_0_2_files_is_refused():
    store.open_write().close()
    before = store.path().read_bytes()
    made = zero_two("record", "directory")
    for opener in (store.open_write, directory.init, LocalRecord):
        with pytest.raises(store.StoreRefused) as e:
            opener()
        assert "wasn't made by moving them; nothing was written" in str(e.value)
        assert str(made[0]) in str(e.value)
    assert store.path().read_bytes() == before


def test_a_marked_store_beside_0_2_files_goes_to_the_finishing_step():
    mark(store.path())
    zero_two("bindings")
    with pytest.raises(store.StoreRefused, match="changed after they were moved"):
        store.open_write()


def test_a_marked_store_with_no_0_2_files_left_opens():
    mark(store.path())
    conn = store.open_write()
    conn.close()
    assert store.state_of(store.path()) == store.MARKED


def test_a_leftover_cursor_folder_does_not_trip_the_gate_yet():
    """The adapters still write cursor files until the daemon keeps the cursors."""
    (paths.cursors_dir() / "local").mkdir(parents=True)
    assert store.zero_two_files() == []
    store.open_write().close()


def test_the_binding_history_does_not_open_a_store_the_gate_refuses():
    store.open_write().close()
    zero_two("directory")
    with pytest.raises(store.StoreRefused):
        bindings.BindingHistory().open()


def test_threads_of_one_process_queue_for_the_gate():
    errors, conns = [], []

    def opener():
        try:
            conns.append(store.open_write())
        except BaseException as e:        # noqa: BLE001 - the test reports it
            errors.append(e)

    threads = [threading.Thread(target=opener) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for conn in conns:
        conn.close()
    assert errors == [] and len(conns) == 8


# --------------------------------------------------------------------------- readers

def test_the_reader_writes_nothing():
    store.open_write().close()
    conn = store.open_read()
    try:
        assert conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")
    finally:
        conn.close()


def test_the_reader_of_a_missing_store_gets_sqlites_error():
    with pytest.raises(sqlite3.OperationalError):
        store.open_read()
    assert not store.path().exists()


def test_the_reader_waits_while_0_2_files_are_unmoved():
    store.open_write().close()
    zero_two("record")
    with pytest.raises(store.Unmoved) as e:
        store.open_read()
    assert e.value.exit_code == 75
    assert "aren't moved yet" in str(e.value)


def test_the_reader_reads_a_marked_store_beside_0_2_files():
    mark(store.path())
    zero_two("record")
    store.open_read().close()


# --------------------------------------------------------------------------- the rule

def test_only_the_store_package_calls_moxi_store_connect():
    """Every other comms module opens comms.db through `store.open_write` or
    `store.open_read`."""
    pattern = re.compile(r"moxi\.store\.connect|from moxi\.store import[^\n]*\bconnect\b"
                         r"|from moxi import[^\n]*\bstore\b|import moxi\.store\b"
                         r"|_store\.connect\(")
    found = []
    for path in SRC.rglob("*.py"):
        if (SRC / "store") in path.parents:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                found.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    assert found == []
