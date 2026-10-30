"""Retired credential columns remain untouched in an older directory."""

import sqlite3

import pytest

from moxi_comms import directory, tokens
from moxi_comms.local_gateway import META_OPERATOR_TOKEN_HASH, LocalGateway
from moxi_comms.paths import directory_db, tokens_dir
from moxi_comms.provision import COMPLETE, add_standing, provision, readiness

#: The directory schema of comms 0.1.0, as that release wrote it. Only this test holds it.
SCHEMA_0_1_0 = """
CREATE TABLE IF NOT EXISTS agents (   -- standing identities, and retired ones
    name        TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'global',
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'agent' CHECK (kind IN ('agent','send-only')),
    PRIMARY KEY (name, scope)
);
CREATE TABLE IF NOT EXISTS slots (    -- claim containers (webhook bundles); not addresses
    slot_id     INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rooms (    -- no 'inbox' kind; rooms have no owner
    channel_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL CHECK (kind IN ('names')),
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhooks ( -- posting credentials; THE provenance anchor
    webhook_id    TEXT PRIMARY KEY,
    slot_id       INTEGER REFERENCES slots(slot_id),
    agent_name    TEXT,
    agent_scope   TEXT,
    channel_id    TEXT NOT NULL REFERENCES rooms(channel_id),
    bws_secret_id TEXT NOT NULL,      -- pointer only; URLs never touch disk
    created_at    TEXT NOT NULL,
    FOREIGN KEY (agent_name, agent_scope) REFERENCES agents(name, scope),
    CHECK ((slot_id IS NULL) <> (agent_name IS NULL)),
    CHECK ((agent_name IS NULL) = (agent_scope IS NULL)),
    UNIQUE (slot_id, channel_id),
    UNIQUE (agent_name, agent_scope, channel_id)
);
CREATE TABLE IF NOT EXISTS meta (     -- e.g. 'writer' -> the one named directory writer
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operators (
    user_id     TEXT PRIMARY KEY,     -- the operator's user id
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def _webhook_columns(path) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    try:
        return [tuple(row) for row in conn.execute("PRAGMA table_info(webhooks)")]
    finally:
        conn.close()


def _old_directory():
    """An empty directory as 0.1.0 created it."""
    path = directory_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA_0_1_0)
        conn.commit()
    finally:
        conn.close()
    path.chmod(0o600)
    return path


@pytest.fixture
def rig():
    gateway = LocalGateway(poll_interval=0.02)
    try:
        yield gateway, tokens.TokenStore(tokens_dir())
    finally:
        gateway.close()


def _ready(gateway, store) -> str:
    return readiness(directory_db(), store=store,
                     operator_hash=gateway.meta_get(META_OPERATOR_TOKEN_HASH)).state


def test_a_fresh_directory_has_no_webhook_table(rig):
    gateway, store = rig
    provision(gateway, directory_db(), standing=[("planner", "proj-a")], store=store)
    conn = directory.connect(directory_db(), read_only=True)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"slots", "webhooks"}.isdisjoint(tables)
    finally:
        conn.close()
    assert _ready(gateway, store) == COMPLETE


def test_a_directory_from_0_1_0_keeps_its_columns_through_every_writer(rig):
    gateway, store = rig
    path = _old_directory()
    before = _webhook_columns(path)
    assert "bws_secret_id" in [column[1] for column in before]
    assert "secret_id" not in [column[1] for column in before]

    # First provisioning, then a re-run that adds standing identities.
    provision(gateway, path, standing=[("planner", "proj-a")], store=store)
    assert _webhook_columns(path) == before
    provision(gateway, path, standing=[("planner", "proj-a"), ("archivist", "global")],
              store=store)
    assert _webhook_columns(path) == before
    conn = directory.init(path)
    try:
        add_standing(gateway, conn, "reviewer", "proj-b", store=store)
    finally:
        conn.close()
    assert _webhook_columns(path) == before

    conn = directory.connect(path, read_only=True)
    try:
        rows = conn.execute("SELECT * FROM webhooks").fetchall()
    finally:
        conn.close()
    assert rows == []

    assert _ready(gateway, store) == COMPLETE
    assert _webhook_columns(path) == before


def test_readiness_ignores_missing_slot_tokens_in_an_old_directory(rig):
    """Retired slot credentials do not make an otherwise complete directory lost."""
    gateway, store = rig
    path = _old_directory()
    result = provision(gateway, path, store=store)
    conn = directory.connect(path)
    try:
        for slot_id in (1, 2):
            conn.execute("INSERT INTO slots(slot_id, created_at) VALUES (?, ?)",
                         (slot_id, directory.now()))
            conn.execute(
                "INSERT INTO webhooks(webhook_id, slot_id, agent_name, agent_scope, "
                "channel_id, bws_secret_id, created_at) VALUES (?,?,NULL,NULL,?,?,?)",
                (f"legacy-{slot_id}", slot_id, result.rooms["#names"],
                 f"comms/slot-{slot_id}/names", directory.now()))
        conn.commit()
    finally:
        conn.close()
    found = readiness(path, store=store,
                      operator_hash=gateway.meta_get(META_OPERATOR_TOKEN_HASH))
    assert found.state == COMPLETE and found.detail == ""
    assert _webhook_columns(path)[5][1] == "bws_secret_id"


def test_the_fresh_schema_declares_no_retired_credential_tables():
    assert "CREATE TABLE IF NOT EXISTS slots" not in directory.SCHEMA
    assert "CREATE TABLE IF NOT EXISTS webhooks" not in directory.SCHEMA
    assert "bws_secret_id" not in directory.SCHEMA
