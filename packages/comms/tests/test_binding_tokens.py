"""Credentials minted for live bindings, and what a bind carries."""

from __future__ import annotations

import base64
import hashlib
import logging

import pytest
from conftest import wait_until

from moxi import ipc
from moxi_comms import directory, kinds
from moxi_comms.claimhold import ClaimServer
from moxi_comms.client import (DaemonClient, DaemonError, DaemonNoAnswer,
                               DaemonOffline)
from moxi_comms.daemon import Daemon
from moxi_comms.paths import daemon_socket, comms_db, state_dir


def client(**kwargs):
    return DaemonClient(daemon_socket(), **kwargs).connect()


def test_each_binding_gets_a_fresh_token_and_only_its_hash_stays_in_the_daemon(
        rig, bind_answered):
    first, answer = bind_answered(name="planner", scope="proj-a")
    token = answer["token"]
    assert len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) == 32
    binding = rig.daemon.bindings["planner@proj-a"]
    assert binding.token_hash == hashlib.sha256(token.encode()).digest()
    assert not hasattr(binding, "token")

    first.close()
    assert wait_until(lambda: "planner@proj-a" not in rig.daemon.bindings)
    _, rebound = bind_answered(name="planner", scope="proj-a")
    assert rebound["token"] != token

    refused = client()
    try:
        with pytest.raises(DaemonError) as raised:
            refused.register(name="reviewer", scope="proj-a", adapter="stranger")
        assert "token" not in raised.value.response
    finally:
        refused.close()


@pytest.mark.parametrize("backend", ["mock", "local"], indirect=True)
def test_a_live_token_posts_one_stamped_row_in_the_names_room(
        rig, bind_answered, backend):
    holder, answer = bind_answered(name="planner", scope="proj-a")
    posted = holder.post("planner@proj-a", answer["token"], "hello")
    rows = rig.record.history(rig.names_channel)
    assert [row.id for row in rows] == [posted["message_id"]]
    assert (rows[0].channel_id, rows[0].author_kind, rows[0].author_id,
            rows[0].author_display, rows[0].content) == (
                rig.names_channel, "identity", "planner@proj-a", "planner@proj-a",
                "hello")


def test_wrong_missing_released_and_unbound_credentials_write_nothing(
        rig, bind_answered):
    planner, own = bind_answered(name="planner", scope="proj-a")
    reviewer, other = bind_answered(name="reviewer", scope="proj-a")
    old, released = bind_answered(name="curator", scope="proj-a")
    old.close()
    assert wait_until(lambda: "curator@proj-a" not in rig.daemon.bindings)
    caller = client()
    attempts = [
        {"identity": "planner@proj-a", "token": other["token"], "content": "wrong"},
        {"identity": "planner@proj-a", "content": "missing"},
        {"identity": "curator@proj-a", "token": released["token"],
         "content": "released"},
        {"identity": "ghost@proj-a", "token": own["token"], "content": "unbound"},
    ]
    try:
        for fields in attempts:
            with pytest.raises(DaemonError) as raised:
                caller.request("post", **fields)
            assert raised.value.response["credential"] is True
    finally:
        caller.close()
    assert rig.record.history(rig.names_channel) == []


def test_a_token_from_before_a_daemon_restart_is_dead(rig, bind_answered):
    old, answer = bind_answered(name="planner", scope="proj-a")
    rig.daemon.stop()
    rig.daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False).start()
    caller = client()
    try:
        with pytest.raises(DaemonError) as raised:
            caller.post("planner@proj-a", answer["token"], "stale")
        assert raised.value.response["credential"] is True
    finally:
        caller.close()
        old.close()
    assert rig.record.history(rig.names_channel) == []


def test_retired_overlong_and_stopping_posts_write_nothing(rig, bind_answered):
    holder, answer = bind_answered(name="planner", scope="proj-a")
    with pytest.raises(DaemonError, match="over the") as too_long:
        holder.post("planner@proj-a", answer["token"],
                    "x" * (rig.record.max_content_length + 1))
    assert "credential" not in too_long.value.response

    db = directory.connect(comms_db())
    try:
        with db:
            db.execute("INSERT INTO identities(name, scope, status, created_at, kind) "
                       "VALUES ('planner', 'proj-a', 'retired', ?, 'agent')",
                       (directory.now(),))
    finally:
        db.close()
    with pytest.raises(DaemonError, match="retired"):
        holder.post("planner@proj-a", answer["token"], "too late")

    with rig.daemon._lock:
        rig.daemon._stopping = True
    try:
        with pytest.raises(DaemonError, match="stopping"):
            holder.post("planner@proj-a", answer["token"], "stopping")
    finally:
        with rig.daemon._lock:
            rig.daemon._stopping = False
    assert rig.record.history(rig.names_channel) == []


def test_a_declared_kind_gets_a_token_and_posts_the_same_way(rig):
    class Entry:
        name = "helper-kind"

        @staticmethod
        def load():
            return {"cache": "person"}

    rig.daemon.kinds = kinds.load([Entry()])
    holder = client()
    try:
        answer = holder.register(name="helper", scope="proj-a", adapter="helper-kind")
        holder.post("helper@proj-a", answer["token"], "declared")
    finally:
        holder.close()
    (row,) = rig.record.history(rig.names_channel)
    assert (row.author_kind, row.author_id, row.content) == (
        "identity", "helper@proj-a", "declared")


@pytest.mark.parametrize("backend", ["mock", "local"], indirect=True)
def test_a_binding_token_is_absent_from_state_files_and_logs(
        rig, bind_answered, caplog, backend):
    caplog.set_level(logging.DEBUG)
    holder, answer = bind_answered(name="planner", scope="proj-a")
    holder.post("planner@proj-a", answer["token"], "quiet")
    token = answer["token"]
    assert token not in caplog.text
    for path in state_dir().rglob("*"):
        if path.is_file():
            assert token.encode() not in path.read_bytes(), path.name


class FakeClient:
    """Stands in for the `moxi.ipc` client: records each call, then fails it as told."""

    def __init__(self, failure):
        self.failure = failure
        self.calls = []

    def call(self, method, params=None, *, timeout=None):
        self.calls.append(method)
        raise self.failure

    def close(self):
        pass


def test_post_distinguishes_a_frame_that_never_left_from_a_lost_answer():
    """Offline only when nothing went out; once `send` was handed to the connection, a
    failed write, a closed connection and a timeout alike may have landed."""
    disconnected = DaemonClient()
    with pytest.raises(DaemonOffline):
        disconnected.post("planner@proj-a", "token", "body")

    for failure, said in ((ipc.IpcError("the connection broke: EPIPE"), "closed"),
                          (ipc.IpcError("the server closed the connection"), "closed"),
                          (ipc.Timeout("no answer to send"), "timeout")):
        lost = DaemonClient(timeout=0.001)
        lost._client = FakeClient(failure)
        with pytest.raises(DaemonNoAnswer, match=said):
            lost.post("planner@proj-a", "token", "body")
        assert lost._client.calls == ["send"]


def test_register_frames_carry_no_protocol_and_never_a_slot(monkeypatch):
    """The wire version rides the hello, so a bind names no protocol."""
    seen = []
    daemon = DaemonClient()
    monkeypatch.setattr(daemon, "request",
                        lambda op, **fields: seen.append((op, fields)) or {})
    daemon.register(name="planner", scope="proj-a",
                    report={"protocol": 1, "slot": 4})
    daemon.register(name="reviewer", scope="proj-a")
    assert all("protocol" not in fields for _, fields in seen)
    assert all("slot" not in fields for _, fields in seen)


def test_more_than_six_names_bind_and_a_slot_is_ignored(
        rig, bind_answered):
    for name in ("planner", "reviewer", "curator", "coder", "critic", "scout",
                 "pilot"):
        bind_answered(name=name, scope="proj-a")
    assert len(rig.daemon.bindings) == 7
    assert all(not hasattr(binding, "slot_id") for binding in rig.daemon.bindings.values())
    explicit = client()
    explicit_answer = explicit.request("register", slot=4, name="helper",
                                       scope="proj-a", adapter="codex")
    try:
        assert "slot" not in explicit_answer
        assert "helper@proj-a" in rig.daemon.bindings
    finally:
        explicit.close()


def test_a_restart_writes_no_keep_file_and_the_name_rebinds_with_a_new_token(
        rig, bind_answered):
    old, answer = bind_answered(name="planner", scope="proj-a")
    old_token = answer["token"]
    rows = [row for row in rig.daemon.binding_history.intervals()
            if row["qualified"] == "planner@proj-a"]
    assert len(rows) == 1 and "slot_id" not in rows[0].keys()
    rig.daemon.stop()
    assert not (state_dir() / "slot-keeps.json").exists()
    rig.daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False).start()
    fresh = client()
    try:
        rebound = fresh.register(name="planner", scope="proj-a")
        assert rebound["token"] != old_token
        assert "slot" not in rebound
    finally:
        fresh.close()
        old.close()


def test_a_claim_server_binds_without_slot_state(rig):
    server = ClaimServer("relay", "proj-a", kind="codex", arm=True,
                         daemon_socket=daemon_socket()).start()
    try:
        assert server.holder.state == "bound"
        assert "slot" not in server.holder.claim
        assert not hasattr(rig.daemon.bindings["relay@proj-a"], "slot_id")
    finally:
        server.stop()


def test_a_claim_server_reports_an_unrelated_refusal_without_retrying_another_frame(rig):
    server = ClaimServer("relay", "proj-a", kind="stranger",
                         daemon_socket=daemon_socket()).start()
    try:
        assert server._register_once() is False
        assert server.holder.state == "waiting"
    finally:
        server.stop()
