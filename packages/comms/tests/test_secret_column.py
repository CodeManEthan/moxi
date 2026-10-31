"""A fresh comms.db carries no retired credential table."""

import pytest

from moxi_comms import directory, tokens
from moxi_comms.local_record import META_OPERATOR_TOKEN_HASH, LocalRecord
from moxi_comms.paths import comms_db, tokens_dir
from moxi_comms.provision import COMPLETE, provision, readiness
from moxi_comms.store import schema


@pytest.fixture
def rig():
    record = LocalRecord(poll_interval=0.02)
    try:
        yield record, tokens.TokenStore(tokens_dir())
    finally:
        record.close()


def _ready(record, store) -> str:
    return readiness(comms_db(), store=store,
                     operator_hash=record.meta_get(META_OPERATOR_TOKEN_HASH)).state


def test_a_fresh_directory_has_no_webhook_table(rig):
    record, store = rig
    provision(record, comms_db(), standing=[("planner", "proj-a")], store=store)
    conn = directory.connect(comms_db(), read_only=True)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"slots", "webhooks"}.isdisjoint(tables)
    finally:
        conn.close()
    assert _ready(record, store) == COMPLETE


def test_the_fresh_schema_declares_no_retired_credential_tables():
    assert "slots" not in schema.TABLES and "webhooks" not in schema.TABLES
    assert "CREATE TABLE slots" not in schema.V1
    assert "CREATE TABLE webhooks" not in schema.V1
    assert "bws_secret_id" not in schema.V1
