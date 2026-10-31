"""The directory in comms.db, and provisioning."""

import os

import pytest

from moxi_comms import directory
from moxi_comms.mock_record import MockRecord
from moxi_comms.paths import comms_db
from moxi_comms.provision import (NAMES, ROOM_KINDS, ProvisionError, add_standing,
                                  provision)


def table_names(conn) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_schema_and_file_posture():
    gw = MockRecord()
    provision(gw, comms_db(), standing=[("planner", "proj-a")])
    assert oct(os.stat(comms_db()).st_mode & 0o777) == "0o600"
    conn = directory.connect(comms_db())
    try:
        tables = table_names(conn)
        assert {"identities", "rooms", "meta", "operators"} <= tables
        assert {"slots", "webhooks"}.isdisjoint(tables)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_provisioning_creates_one_room_without_agent_webhooks():
    gw = MockRecord()
    p = provision(gw, comms_db())
    assert list(p.rooms) == [NAMES]
    conn = directory.connect(comms_db(), read_only=True)
    try:
        assert {r["kind"] for r in conn.execute("SELECT kind FROM rooms")} == {"names"}
        assert directory.meta_get(conn, "reader_principal") is None, "no reader principal"
        assert {"slots", "webhooks"}.isdisjoint(table_names(conn))
    finally:
        conn.close()


@pytest.mark.parametrize("room, kind", [("#conference", "conference"),
                                        ("#notices", "notices")])
def test_a_new_directory_refuses_a_freed_room_row(room, kind):
    """The schema's CHECK names the one room provision makes, for a new file."""
    import sqlite3

    conn = directory.init(comms_db())
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO rooms(room_id, name, kind, created_at) "
                         "VALUES ('c', ?, ?, 't')", (room, kind))
    finally:
        conn.close()


def test_standing_identity_gets_no_stored_credential():
    gw = MockRecord()
    provision(gw, comms_db(), standing=[("planner", "proj-a")])
    conn = directory.connect(comms_db(), read_only=True)
    try:
        assert "webhooks" not in table_names(conn)
    finally:
        conn.close()


def test_provisioning_is_idempotent():
    gw = MockRecord()
    provision(gw, comms_db(), standing=[("planner", "proj-a")])
    provision(gw, comms_db(), standing=[("planner", "proj-a")])
    conn = directory.connect(comms_db(), read_only=True)
    try:
        assert "webhooks" not in table_names(conn)
        assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == len(ROOM_KINDS)
    finally:
        conn.close()


def test_agent_n_namespace_is_refused_as_a_standing_row():
    gw = MockRecord()
    provision(gw, comms_db())
    conn = directory.connect(comms_db())
    try:
        with pytest.raises(ProvisionError, match="reserved"):
            add_standing(gw, conn, "agent-4", "proj-a")
    finally:
        conn.close()


def test_standing_row_refused_while_the_name_is_live_as_chosen(rig):
    from moxi_comms.client import DaemonClient
    from moxi_comms.paths import daemon_socket

    rig.shim(name="planner", scope="proj-a")
    client = DaemonClient(daemon_socket()).connect()
    conn = directory.connect(comms_db())
    try:
        with pytest.raises(ProvisionError, match="release it first"):
            add_standing(rig.record, conn, "planner", "proj-a", daemon_client=client)
    finally:
        conn.close()
        client.close()


# ------------------------------------- provision as the token WRITER (the local backend)
#
# One token per ROW, written into the holder's store file under the row's own
# `secret_id` in the same call that created the row -- which is what makes a first run
# against `local` need no portal, no pasting and no operator beyond running the verb.

def local_rig(tmp_path):
    """A local record and its store, the pair `moxi-comms-provision --backend local` builds."""
    from moxi_comms.local_record import LocalRecord
    from moxi_comms.tokens import TokenStore

    return LocalRecord(tmp_path / "record.db"), TokenStore(tmp_path / "store")


def test_only_the_operator_token_lands_in_the_store(tmp_path):
    from moxi_comms import tokens

    gw, store = local_rig(tmp_path)
    try:
        provision(gw, comms_db(), standing=[("planner", "proj-a")],
                  operator=("900001", "operator"), store=store)
    finally:
        gw.close()

    assert set(store.owners_present()) == {"operator"}
    assert store.secret_ids(tokens.owner_for_role(tokens.ROLE_OPERATOR)) == \
        [tokens.OPERATOR_SECRET_ID]


def test_the_record_keeps_only_the_operator_hash(tmp_path):
    from moxi_comms import tokens

    gw, store = local_rig(tmp_path)
    try:
        provision(gw, comms_db(), standing=[("planner", "proj-a")],
                  operator=("900001", "operator"), store=store)
    finally:
        gw.close()

    owner = tokens.owner_for_role(tokens.ROLE_OPERATOR)
    minted = [store.read_secret(owner, tokens.OPERATOR_SECRET_ID)]
    assert all(minted)

    for db in ((tmp_path / "record.db"), comms_db()):
        for path in db.parent.glob(db.name + "*"):        # the WAL and the shm too
            blob = path.read_bytes()
            for token in minted:
                assert token.encode() not in blob, f"{token[:4]}... is in the clear in {path.name}"

    conn = directory.connect(comms_db(), read_only=True)
    try:
        tables = table_names(conn)
    finally:
        conn.close()
    assert "webhooks" not in tables


def test_a_second_run_converges_and_rotates_nothing(tmp_path):
    """Re-provisioning is the same promise the directory rows have always made: what is
    there is found, only what is missing is made, and no holder loses the credential it
    is already spending."""
    from moxi_comms import tokens

    gw, store = local_rig(tmp_path)
    try:
        first = provision(gw, comms_db(), standing=[("planner", "proj-a")],
                          operator=("900001", "operator"), store=store)
        before = {(owner.key, secret_id): store.read_secret(owner, secret_id)
                  for owner in (tokens.owner_for_role(tokens.ROLE_OPERATOR),)
                  for secret_id in store.secret_ids(owner)}

        second = provision(gw, comms_db(), standing=[("planner", "proj-a")],
                           operator=("900001", "operator"), store=store)
        after = {(owner.key, secret_id): store.read_secret(owner, secret_id)
                 for owner in (tokens.owner_for_role(tokens.ROLE_OPERATOR),)
                 for secret_id in store.secret_ids(owner)}
    finally:
        gw.close()

    assert after == before
    assert first.operator_token_written is True
    assert second.operator_token_written is False, "the second run rotated the operator"


def test_half_an_operator_pair_is_replaced_rather_than_left_unusable(tmp_path):
    """A stored token whose hash is not in the record is a lane nobody can use, so the
    converging run mints -- and the two halves agree again afterwards."""
    from moxi_comms import tokens
    from moxi_comms.local_record import META_OPERATOR_TOKEN_HASH, token_hash

    gw, store = local_rig(tmp_path)
    owner = tokens.owner_for_role(tokens.ROLE_OPERATOR)
    try:
        provision(gw, comms_db(), operator=("900001", "operator"), store=store)
        store.write_secret(owner, tokens.OPERATOR_SECRET_ID, "stale-and-unmatched")

        again = provision(gw, comms_db(), operator=("900001", "operator"),
                          store=store)
        token = store.read_secret(owner, tokens.OPERATOR_SECRET_ID)
        stored = gw.meta_get(META_OPERATOR_TOKEN_HASH)
    finally:
        gw.close()

    assert again.operator_token_written is True
    assert token != "stale-and-unmatched"
    assert stored == token_hash(token)


def test_the_mock_is_not_given_a_store(tmp_path):
    """The mock keeps the token in its own record, so there is nothing here to keep and
    provision writes nothing."""
    from moxi_comms.tokens import TokenStore

    store = TokenStore(tmp_path / "store")
    provision(MockRecord(), comms_db(), standing=[("planner", "proj-a")])

    assert store.owners_present() == []


# ------------------------------------------------- the first start and `[provision]`

def test_the_first_start_makes_what_provision_in_config_says(tmp_path):
    """The operator is read when the step runs; with no file it is `operator`."""
    from conftest import write_config
    from moxi_comms import provision as prov
    from moxi_comms.local_record import LocalRecord
    from moxi_comms.tokens import TokenStore

    assert prov.configured() == prov.DEFAULT_OPERATOR == ("operator", "operator")
    write_config(None, '[provision]\noperator = "alice"\n')
    assert prov.configured() == ("operator", "alice")

    gw = LocalRecord(tmp_path / "record.db")
    try:
        prov.first_start(TokenStore(tmp_path / "store"))(gw, comms_db())
        conn = directory.connect(comms_db(), read_only=True)
        try:
            assert {"slots", "webhooks"}.isdisjoint(table_names(conn))
            assert [r["name"] for r in conn.execute("SELECT name FROM operators")] == \
                ["alice"]
        finally:
            conn.close()
        # A provisioned directory is left alone, whatever the config says now.
        write_config(None, '[provision]\noperator = "bob"\n')
        prov.first_start(TokenStore(tmp_path / "store"))(gw, comms_db())
        conn = directory.connect(comms_db(), read_only=True)
        try:
            assert {"slots", "webhooks"}.isdisjoint(table_names(conn))
            assert [r["name"] for r in conn.execute("SELECT name FROM operators")] == \
                ["alice"]
        finally:
            conn.close()
    finally:
        gw.close()
