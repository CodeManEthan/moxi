"""Conversion of old authors and removal of retired posting files."""

from __future__ import annotations

import logging
import sqlite3

import pytest

from moxi_comms import attribution, directory, migration, tokens
from moxi_comms.daemon import Daemon
from moxi_comms.local_record import LocalRecord
from moxi_comms.mock_record import MockRecord
from moxi_comms.provision import provision


OLD_DIRECTORY = """
CREATE TABLE slots (slot_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE webhooks (
    webhook_id TEXT PRIMARY KEY,
    slot_id INTEGER,
    agent_name TEXT,
    agent_scope TEXT,
    channel_id TEXT NOT NULL,
    secret_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

OLD_BINDINGS = """
CREATE TABLE bindings (
    id INTEGER PRIMARY KEY,
    qualified TEXT NOT NULL,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    tier TEXT NOT NULL,
    slot_id INTEGER,
    adapter TEXT NOT NULL,
    bound_at TEXT NOT NULL,
    released_at TEXT,
    fenced INTEGER NOT NULL DEFAULT 0
);
"""


def _old_state(tmp_path):
    record = LocalRecord(tmp_path / "record.db", clock=lambda: 1_700_000_000)
    directory_path = tmp_path / "directory.db"
    made = provision(record, directory_path, operator=("900001", "operator"))
    conn = directory.connect(directory_path)
    conn.executescript(OLD_DIRECTORY)
    conn.execute("INSERT INTO agents(name, scope, status, created_at, kind) "
                 "VALUES ('herald', 'global', 'active', '2026-01-01', 'agent')")
    for number in (1, 2, 3):
        conn.execute("INSERT INTO slots VALUES (?, '2026-01-01')", (number,))
    rows = [
        ("standing", None, "herald", "global"),
        ("matched", 1, None, None),
        ("mismatch", 2, None, None),
        ("outside", 3, None, None),
    ]
    for webhook_id, number, name, scope in rows:
        conn.execute("INSERT INTO webhooks VALUES (?,?,?,?,?,?,?)",
                     (webhook_id, number, name, scope, made.rooms["#names"],
                      f"old/{webhook_id}", "2026-01-01"))
    conn.commit()
    conn.close()

    history_path = tmp_path / "bindings.db"
    history = sqlite3.connect(history_path)
    history.executescript(OLD_BINDINGS)
    history.executemany(
        "INSERT INTO bindings(qualified,name,scope,tier,slot_id,adapter,bound_at,released_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            ("planner@proj-a", "planner", "proj-a", "chosen", 1, "claude-shim",
             "2023-11-14T22:00:00Z", "2023-11-14T23:00:00Z"),
            ("critic@proj-a", "critic", "proj-a", "chosen", 2, "claude-shim",
             "2023-11-14T22:00:00Z", "2023-11-14T23:00:00Z"),
            ("curator@proj-a", "curator", "proj-a", "chosen", 3, "claude-shim",
             "2023-11-14T20:00:00Z", "2023-11-14T21:00:00Z"),
        ])
    history.commit()
    history.close()

    with record._write() as db:
        for author_id, display in (
                ("standing", "herald"),
                ("matched", "planner@proj-a"),
                ("mismatch", "reviewer@proj-a"),
                ("outside", "curator@proj-a"),
                ("gone", "scout@proj-a")):
            record._insert(db, channel_id=made.rooms["#names"], author_id=author_id,
                           author_kind="webhook", author_display=display, content="old")
    record.post_as_user(made.operator_user_id, made.rooms["#names"], "operator")
    return record, directory_path, history_path, made.rooms["#names"]


def _answers(record, directory_path, history_path, channel):
    db = directory.connect(directory_path, read_only=True)
    try:
        return [(a.qualified, a.attribution) for a in (
            attribution.from_record(db, msg, record=record, history_path=history_path)
            for msg in record.history(channel))]
    finally:
        db.close()


def test_old_messages_read_the_same_before_and_after_the_one_time_conversion(tmp_path):
    record, directory_path, history_path, channel = _old_state(tmp_path)
    try:
        before = _answers(record, directory_path, history_path, channel)
        assert before == [
            ("herald", "verified"),
            ("planner@proj-a", "verified"),
            ("reviewer@proj-a", "labeled"),
            ("curator@proj-a", "labeled"),
            ("unknown", "labeled"),
            ("operator", "verified"),
        ]

        assert migration.stamp_record(
            record, directory_path, history_path=history_path) == (3, True)
        assert _answers(record, directory_path, history_path, channel) == before
        messages = record.history(channel)
        assert [m.author_kind for m in messages] == [
            "identity", "identity", "webhook", "webhook", "unknown", "user"]
        snapshot = [m.to_dict() for m in messages]

        assert migration.stamp_record(
            record, directory_path, history_path=history_path) == (0, False)
        assert [m.to_dict() for m in record.history(channel)] == snapshot
    finally:
        record.close()


def test_an_unreadable_binding_history_leaves_the_record_unchanged(tmp_path):
    record, directory_path, history_path, channel = _old_state(tmp_path)
    record.close()
    history_path.write_text("not a database")
    record = LocalRecord(tmp_path / "record.db")
    try:
        before = [m.to_dict() for m in record.history(channel)]
        with pytest.raises(migration.MigrationError, match="left unchanged"):
            migration.stamp_record(record, directory_path, history_path=history_path)
        assert record.meta_get(migration.META_AUTHOR_STAMPS) is None
        assert [m.to_dict() for m in record.history(channel)] == before
    finally:
        record.close()


def test_store_cleanup_is_idempotent_and_matches_only_retired_posting_files(
        tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="comms.migration")
    root = tmp_path / "tokens"
    store = tokens.TokenStore(root)
    retired = tokens.TokenOwner(("slot-2",))
    retired_mixed = tokens.TokenOwner(("slot-3",))
    standing = tokens.TokenOwner(("proj-a", "planner"))
    mixed = tokens.TokenOwner(("proj-a", "reviewer"))
    operator = tokens.owner_for_role(tokens.ROLE_OPERATOR)
    daemon = tokens.owner_for_role(tokens.ROLE_DAEMON)
    store.write_secret(retired, "comms/slot-2/names", "old")
    store.write_secret(retired_mixed, "ANTHROPIC_API_KEY", "discarded")
    store.write_secret(standing, "comms/proj-a/planner/names", "old")
    store.write_secret(mixed, "comms/proj-a/reviewer/names", "old")
    store.write_secret(mixed, "ANTHROPIC_API_KEY", "keep")
    store.write_secret(operator, tokens.OPERATOR_SECRET_ID, "keep")
    store.write_secret(daemon, "daemon-key", "keep")
    keep = tmp_path / "slot-keeps.json"
    keep.write_text("{}")

    result = migration.prune_store(store, keep_path=keep, own_store=True)
    assert (result.removed_keys, result.removed_files) == (2, 4)
    assert not keep.exists()
    assert not store.path_for(retired).exists()
    assert not store.path_for(retired_mixed).exists()
    assert not store.path_for(standing).exists()
    assert store.path_for(mixed).exists()
    assert any("contains a key" in record.getMessage() for record in caplog.records)
    assert store.path_for(operator).exists() and store.path_for(daemon).exists()
    assert migration.prune_store(store, keep_path=keep, own_store=True) == migration.Result()


def test_startup_finishes_cleanup_after_the_record_marker_was_committed(tmp_path):
    root = tmp_path / "tokens"
    store = tokens.TokenStore(root)
    record = LocalRecord(tmp_path / "record.db")
    directory_path = tmp_path / "directory.db"
    made = provision(record, directory_path, operator=("900001", "operator"), store=store)
    operator_token = store.read_secret(tokens.owner_for_role(tokens.ROLE_OPERATOR),
                                       tokens.OPERATOR_SECRET_ID)
    record.post_as_user(made.operator_user_id, made.rooms["#names"], "unchanged",
                        token=operator_token)
    record.meta_set(migration.META_AUTHOR_STAMPS, "1")
    before = [message.to_dict() for message in record.history(made.rooms["#names"])]

    retired = tokens.TokenOwner(("slot-2",))
    standing = tokens.TokenOwner(("proj-a", "planner"))
    store.write_secret(retired, "comms/slot-2/names", "old")
    store.write_secret(standing, "comms/proj-a/planner/names", "old")
    keep = tmp_path / "slot-keeps.json"
    keep.write_text("{}")
    daemon = Daemon(
        record, db_path=directory_path, socket_path=tmp_path / "daemon.sock",
        history_path=tmp_path / "bindings.db", start_trim_thread=False,
        migrate=migration.startup(store, keep_path=keep, own_store=True),
        cache_store=None)

    try:
        daemon.start()
        assert not keep.exists()
        assert not store.path_for(retired).exists()
        assert not store.path_for(standing).exists()
        assert record.meta_get(migration.META_AUTHOR_STAMPS) == "1"
        assert [message.to_dict() for message in record.history(made.rooms["#names"])] == before
    finally:
        daemon.stop()
        record.close()


@pytest.mark.parametrize("body", [b"\xff", b"broken = [\n"])
def test_startup_keeps_an_unreadable_root_retired_file_and_removes_its_neighbors(
        tmp_path, caplog, body):
    caplog.set_level(logging.INFO, logger="comms.migration")
    root = tmp_path / "tokens"
    store = tokens.TokenStore(root)
    record = LocalRecord(tmp_path / "record.db")
    directory_path = tmp_path / "directory.db"
    provision(record, directory_path, operator=("900001", "operator"), store=store)

    unreadable = store.path_for(tokens.TokenOwner(("slot-2",)))
    unreadable.write_bytes(body)
    unreadable.chmod(0o600)
    readable = tokens.TokenOwner(("slot-3",))
    standing = tokens.TokenOwner(("proj-a", "planner"))
    store.write_secret(readable, "comms/slot-3/names", "old")
    store.write_secret(standing, "comms/proj-a/planner/names", "old")
    daemon = Daemon(
        record, db_path=directory_path, socket_path=tmp_path / "daemon.sock",
        history_path=tmp_path / "bindings.db", start_trim_thread=False,
        migrate=migration.startup(store, own_store=True), cache_store=None)

    try:
        daemon.start()
        assert unreadable.read_bytes() == body
        assert not store.path_for(readable).exists()
        assert not store.path_for(standing).exists()
        assert record.meta_get(migration.META_AUTHOR_STAMPS) == "1"
        assert any(str(unreadable) in item.getMessage()
                   and "not a readable flat" in item.getMessage()
                   for item in caplog.records)
    finally:
        daemon.stop()
        record.close()


@pytest.mark.parametrize("body", [b"\xff", b"{broken"])
def test_startup_keeps_an_unreadable_keep_file_and_continues_cleanup(
        tmp_path, caplog, body):
    caplog.set_level(logging.INFO, logger="comms.migration")
    root = tmp_path / "tokens"
    store = tokens.TokenStore(root)
    record = LocalRecord(tmp_path / "record.db")
    directory_path = tmp_path / "directory.db"
    provision(record, directory_path, operator=("900001", "operator"), store=store)

    keep = tmp_path / "slot-keeps.json"
    keep.write_bytes(body)
    retired = tokens.TokenOwner(("slot-2",))
    store.write_secret(retired, "comms/slot-2/names", "old")
    daemon = Daemon(
        record, db_path=directory_path, socket_path=tmp_path / "daemon.sock",
        history_path=tmp_path / "bindings.db", start_trim_thread=False,
        migrate=migration.startup(store, keep_path=keep, own_store=True),
        cache_store=None)

    try:
        daemon.start()
        assert keep.read_bytes() == body
        assert not store.path_for(retired).exists()
        assert record.meta_get(migration.META_AUTHOR_STAMPS) == "1"
        assert any(str(keep) in item.getMessage() and "not readable JSON" in item.getMessage()
                   for item in caplog.records)
    finally:
        daemon.stop()
        record.close()


@pytest.mark.parametrize("body", [b"\xff", b"broken = [\n"])
def test_startup_keeps_unreadable_legacy_holder_files(tmp_path, caplog, body):
    caplog.set_level(logging.INFO, logger="comms.migration")
    root = tmp_path / "tokens"
    store = tokens.TokenStore(root)
    record = LocalRecord(tmp_path / "record.db")
    directory_path = tmp_path / "directory.db"
    provision(record, directory_path, operator=("900001", "operator"), store=store)

    holder = store.path_for(tokens.TokenOwner(("proj-a", "planner")))
    holder.parent.mkdir(parents=True, exist_ok=True)
    holder.write_bytes(body)
    holder.chmod(0o600)
    unrelated = store.path_for(tokens.owner_for_role(tokens.ROLE_DAEMON))
    store.write_secret(tokens.owner_for_role(tokens.ROLE_DAEMON), "daemon-key", "keep")
    unrelated_before = unrelated.read_bytes()
    daemon = Daemon(
        record, db_path=directory_path, socket_path=tmp_path / "daemon.sock",
        history_path=tmp_path / "bindings.db", start_trim_thread=False,
        migrate=migration.startup(store, own_store=True), cache_store=None)

    try:
        daemon.start()
        assert record.meta_get(migration.META_AUTHOR_STAMPS) == "1"
        assert holder.read_bytes() == body
        assert unrelated.read_bytes() == unrelated_before
        assert any(str(holder) in item.getMessage() and "not a readable flat" in item.getMessage()
                   for item in caplog.records)
    finally:
        daemon.stop()
        record.close()


def test_an_external_store_is_counted_once_and_never_changed(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="comms.migration")
    store = tokens.TokenStore(tmp_path / "external")
    old = tokens.TokenOwner(("slot-4",))
    store.write_secret(old, "comms/slot-4/names", "old")
    path = store.path_for(old)

    record = LocalRecord(tmp_path / "record.db")
    directory_path = tmp_path / "directory.db"
    provision(record, directory_path)
    run = migration.startup(store, own_store=False)
    try:
        result = run(record, directory_path, tmp_path / "missing-bindings.db")
        assert result.left_keys == 1 and path.exists()
        run(record, directory_path, tmp_path / "missing-bindings.db")
    finally:
        record.close()
    messages = [record.getMessage() for record in caplog.records
                if "externally configured store" in record.getMessage()]
    assert len(messages) == 1 and "left 1 retired posting key(s)" in messages[0]


def test_daemon_runs_conversion_after_provision_and_the_boot_fence(tmp_path):
    order = []
    daemon = Daemon(
        MockRecord(), db_path=tmp_path / "directory.db",
        socket_path=tmp_path / "daemon.sock", start_trim_thread=False,
        first_start=lambda *_: order.append("provision"),
        migrate=lambda *_: (order.append("convert"), (_ for _ in ()).throw(RuntimeError("stop"))))
    daemon._probe_or_refuse = lambda: order.append("probe")
    daemon.binding_history.open = lambda: order.append("open") or True
    daemon.binding_history.fence = lambda: order.append("fence") or 0

    with pytest.raises(RuntimeError, match="stop"):
        daemon.start()
    assert order == ["probe", "provision", "open", "fence", "convert"]
    assert daemon._server is None
