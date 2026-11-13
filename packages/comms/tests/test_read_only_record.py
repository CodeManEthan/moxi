"""The read-only record: a record a long-lived reader opens without ever writing.

`LocalRecord(read_only=True)` and `open_record(read_only=True)` open each connection
through `store.open_read` with the store's schema step checked: no gate, no lock, no
move of comms 0.2's files, no migration. A write, a trim or a subscription on it raises
`ReadOnly`. `open_record()` with no keyword opens the writer it always did.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import pytest
from conftest import files_under

from moxi import exit as moxi_exit
from moxi import files as moxi_files
from moxi import store as moxi_store
from moxi_comms import local_record, paths, store
from moxi_comms import record as record_mod
from moxi_comms.local_record import LocalRecord, ReadOnly
from moxi_comms.mock_record import MockRecord
from moxi_comms.store import schema

SIGIL_LINE = "::to @reviewer"


def seeded() -> tuple[str, list[str]]:
    """comms.db at its current step, written by a writer and closed: one room, two
    posts. The room's id and the posts' ids."""
    writer = LocalRecord()
    try:
        room = writer.create_channel("general")
        ids = [writer.post_as_identity("planner@proj", room, f"{SIGIL_LINE}\nnote {n}").id
               for n in (1, 2)]
    finally:
        writer.close()
    return room, ids


def at_step(step: int) -> Path:
    """comms.db made by running only the first `step` of the schema's steps, then
    `user_version` set to `step` (past `len(STEPS)` for a store a newer comms wrote)."""
    target = store.path()
    conn = moxi_store.connect(target)
    try:
        moxi_store.migrate(conn, schema.STEPS[:min(step, len(schema.STEPS))])
        conn.execute(f"PRAGMA user_version = {step}")
    finally:
        conn.close()
    return target


def store_files(root: Path) -> dict:
    """Every file under `root` with its size and modification time, but SQLite's own
    `-wal` and `-shm` beside a store, which any connection to a store in WAL may make or
    remove as it opens and closes."""
    return {path: seen for path, seen in files_under(root).items()
            if not path.name.endswith(("-wal", "-shm"))}


def user_version(target: Path) -> int:
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def no_gate(monkeypatch):
    """Called once the store is set up: from then on the writers' open and the gate's
    lock fail the test if anything takes them."""
    def refused(*args, **kwargs):
        raise AssertionError("a read took a writer's open or the gate's lock")

    def arm():
        monkeypatch.setattr(local_record, "open_write", refused)
        monkeypatch.setattr(moxi_files, "lock", refused)

    return arm


# --------------------------------------------------------------------------- opening

def test_a_read_only_record_opens_through_the_reader_and_reads(no_gate):
    room, ids = seeded()
    no_gate()
    reader = LocalRecord(read_only=True)
    try:
        conn = reader._conn()
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert [m.id for m in reader.history(room)] == ids
        assert reader.get_message(ids[0]).content.endswith("note 1")
        assert reader.max_message_id(room) == ids[-1]
        assert [m.id for m in reader.since(room, "1970-01-01T00:00:00Z")] == ids
        assert reader.channel_by_name("general") == room
        assert reader.meta_get(schema.META_NEXT_ID) == ids[-1]
    finally:
        reader.close()


def test_open_record_read_only_opens_the_configured_store_read_only(no_gate):
    seeded()
    no_gate()
    opened = record_mod.open_record(read_only=True)
    try:
        assert isinstance(opened, LocalRecord)
        assert opened.read_only and opened.path == store.path()
        assert opened._conn().execute("PRAGMA query_only").fetchone()[0] == 1
    finally:
        opened.close()


def test_open_record_with_no_keyword_opens_the_writer_as_before(tmp_path):
    """A first open makes the store, at its current step, and writes through it."""
    assert not store.path().exists()
    opened = record_mod.open_record()
    try:
        assert isinstance(opened, LocalRecord) and not opened.read_only
        assert user_version(store.path()) == len(schema.STEPS)
        assert opened._conn().execute("PRAGMA query_only").fetchone()[0] == 0
        room = opened.create_channel("general")
        assert opened.post_as_identity("planner@proj", room, "hello").id
    finally:
        opened.close()


def test_the_mock_opens_its_file_whatever_read_only_says(monkeypatch, tmp_path):
    monkeypatch.setenv("MOXI_COMMS_BACKEND", "mock")
    monkeypatch.setenv("MOXI_COMMS_MOCK_BACKEND", str(tmp_path / "mock.json"))
    opened = record_mod.open_record(read_only=True)
    try:
        assert isinstance(opened, MockRecord)
    finally:
        opened.close()
    assert not store.path().exists()


# --------------------------------------------------------------------------- writing

WRITES = {
    "create_channel": lambda r, room, mid: r.create_channel("another"),
    "delete_channel": lambda r, room, mid: r.delete_channel(room),
    "register_principal": lambda r, room, mid: r.register_principal("someone"),
    "deny_send": lambda r, room, mid: r.deny_send("pr-1", room),
    "post_as_user": lambda r, room, mid: r.post_as_user("pr-1", room, "hi"),
    "post_as_identity": lambda r, room, mid: r.post_as_identity("planner@proj", room, "hi"),
    "post_as_reserved": lambda r, room, mid: r.post_as_reserved("console", room, "hi"),
    "withdraw": lambda r, room, mid: r.withdraw(mid, "2026-11-12T00:00:00Z"),
    "meta_set": lambda r, room, mid: r.meta_set("x", "y"),
    "trim": lambda r, room, mid: r.trim(1, keep=()),
    "subscribe": lambda r, room, mid: r.subscribe(),
}


@pytest.mark.parametrize("write", sorted(WRITES))
def test_every_write_on_a_read_only_record_raises_and_writes_nothing(write):
    room, ids = seeded()
    before = store_files(store.path().parent)
    threads = set(threading.enumerate())
    reader = LocalRecord(read_only=True)
    try:
        with pytest.raises(ReadOnly, match="read-only"):
            WRITES[write](reader, room, ids[0])
        assert reader._pump is None and reader._subs == []
        assert set(threading.enumerate()) == threads
        assert [m.id for m in reader.history(room)] == ids
        assert reader.get_message(ids[0]).withdrawn_at is None
    finally:
        reader.close()
    assert store_files(store.path().parent) == before


def test_the_reader_connection_itself_refuses_a_write():
    seeded()
    reader = LocalRecord(read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            reader._conn().execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")
    finally:
        reader.close()


# --------------------------------------------------------------------------- refusals

def test_a_missing_store_is_refused_and_no_file_is_made(no_gate):
    root = store.path().parent
    assert not root.exists()
    no_gate()
    with pytest.raises(sqlite3.OperationalError):
        record_mod.open_record(read_only=True)
    assert not store.path().exists()
    assert not root.exists() or store_files(root) == {}


@pytest.mark.parametrize("store_there", [True, False], ids=["store", "no-store"])
def test_comms_0_2_files_unmoved_are_refused_and_nothing_moves(store_there, no_gate):
    if store_there:
        seeded()
    old = paths.record_db()
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"")
    root = store.path().parent
    before = store_files(root)
    no_gate()
    with pytest.raises(store.Unmoved):
        LocalRecord(read_only=True)
    assert store_files(root) == before
    assert old.exists()
    assert store.path().exists() is store_there


def test_an_older_step_is_refused_and_not_migrated(no_gate):
    target = at_step(len(schema.STEPS) - 1)
    before = store_files(target.parent)
    no_gate()
    with pytest.raises(store.StepMismatch) as caught:
        record_mod.open_record(read_only=True)
    assert (caught.value.version, caught.value.known) == (len(schema.STEPS) - 1,
                                                          len(schema.STEPS))
    assert caught.value.exit_code == moxi_exit.TEMPFAIL
    assert user_version(target) == len(schema.STEPS) - 1
    assert store_files(target.parent) == before
    assert not (target.parent / store.MIGRATE_LOCK).exists()


def test_a_newer_step_is_refused(no_gate):
    target = at_step(len(schema.STEPS) + 1)
    no_gate()
    with pytest.raises(store.StepMismatch) as caught:
        LocalRecord(read_only=True)
    assert caught.value.version == len(schema.STEPS) + 1
    assert caught.value.exit_code == moxi_exit.REFUSED
    assert "newer comms" in str(caught.value)
    assert user_version(target) == len(schema.STEPS) + 1


def test_the_step_is_read_once_per_connection():
    """A connection's step is read as it opens: one already open keeps reading, and the
    next connection, another thread's, reads the step again."""
    room, ids = seeded()
    reader = LocalRecord(read_only=True)
    try:
        conn = moxi_store.connect(store.path(), create=False)
        try:
            conn.execute(f"PRAGMA user_version = {len(schema.STEPS) + 1}")
        finally:
            conn.close()
        assert [m.id for m in reader.history(room)] == ids
        caught: list[BaseException] = []

        def other_thread():
            try:
                reader.history(room)
            except BaseException as e:  # noqa: BLE001 - the test reads it
                caught.append(e)

        t = threading.Thread(target=other_thread)
        t.start()
        t.join()
        assert [type(e) for e in caught] == [store.StepMismatch]
    finally:
        reader.close()


# --------------------------------------------------------------------------- lifetime

def _open_on(target: Path) -> int:
    """How many descriptors this process holds on `target` (Linux's /proc)."""
    fds = Path("/proc/self/fd")
    count = 0
    for fd in fds.iterdir():
        try:
            if fd.resolve() == target.resolve():
                count += 1
        except OSError:
            continue
    return count


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc/self/fd")
def test_close_leaves_no_connection_open():
    room, _ = seeded()
    target = store.path()
    assert _open_on(target) == 0
    reader = LocalRecord(read_only=True)
    reader.history(room)
    assert _open_on(target) >= 1
    conns = list(reader._conns)
    reader.close()
    assert _open_on(target) == 0 and reader._conns == []
    for conn in conns:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
