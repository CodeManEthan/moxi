"""Credentials minted for live bindings and protocol-two registration."""

from __future__ import annotations

import base64
import hashlib
import logging

import pytest
from conftest import wait_until

from moxi_comms import directory, kinds
from moxi_comms.claimhold import ClaimServer
from moxi_comms.client import (DaemonClient, DaemonError, DaemonNoAnswer,
                               DaemonOffline)
from moxi_comms.daemon import Daemon
from moxi_comms.paths import daemon_socket, directory_db, state_dir
from moxi_comms.protocol import PROTOCOL_VERSION, ProtocolError


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

    db = directory.connect(directory_db())
    try:
        with db:
            db.execute("INSERT INTO agents(name, scope, status, created_at, kind) "
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


class RecordingConnection:
    def __init__(self, client=None, *, close_after_send=False, fail_send=False):
        self.client = client
        self.close_after_send = close_after_send
        self.fail_send = fail_send
        self.frames = []

    def send(self, frame):
        if self.fail_send:
            raise ProtocolError("write failed")
        self.frames.append(frame)
        if self.close_after_send:
            self.client._closed = True

    def close(self):
        pass


def test_post_distinguishes_a_frame_that_never_left_from_a_lost_answer():
    disconnected = DaemonClient()
    with pytest.raises(DaemonOffline):
        disconnected.post("planner@proj-a", "token", "body")

    failed = DaemonClient()
    failed._conn = RecordingConnection(fail_send=True)
    with pytest.raises(DaemonOffline):
        failed.post("planner@proj-a", "token", "body")

    closed = DaemonClient()
    closed._conn = RecordingConnection(closed, close_after_send=True)
    with pytest.raises(DaemonNoAnswer, match="closed"):
        closed.post("planner@proj-a", "token", "body")
    assert closed._conn.frames[0]["op"] == "post"

    timed_out = DaemonClient(timeout=0.001)
    timed_out._conn = RecordingConnection()
    with pytest.raises(DaemonNoAnswer, match="timeout"):
        timed_out.post("planner@proj-a", "token", "body")
    assert timed_out._conn.frames[0]["op"] == "post"


def test_register_frames_name_protocol_two_and_never_carry_a_slot(monkeypatch):
    seen = []
    daemon = DaemonClient()
    monkeypatch.setattr(daemon, "request",
                        lambda op, **fields: seen.append((op, fields)) or {})
    daemon.register(name="planner", scope="proj-a",
                    report={"protocol": 1, "slot": 4})
    daemon.register(name="reviewer", scope="proj-a")
    assert all(fields["protocol"] == PROTOCOL_VERSION == 2 for _, fields in seen)
    assert all("slot" not in fields for _, fields in seen)


def test_more_than_six_names_bind_and_a_protocol_two_slot_is_ignored(
        rig, bind_answered):
    for name in ("planner", "reviewer", "curator", "coder", "critic", "scout",
                 "pilot"):
        bind_answered(name=name, scope="proj-a")
    assert len(rig.daemon.bindings) == 7
    assert all(not hasattr(binding, "slot_id") for binding in rig.daemon.bindings.values())
    explicit = client()
    explicit_answer = explicit.request("register", slot=4, name="helper",
                                       scope="proj-a", adapter="codex", protocol=2)
    try:
        assert "slot" not in explicit_answer
        assert "helper@proj-a" in rig.daemon.bindings
    finally:
        explicit.close()


@pytest.mark.parametrize("slot", ["next-free", 3])
def test_an_old_register_is_refused_before_it_changes_any_binding_state(rig, slot):
    message = rig.post_as("reviewer@proj-a", ["planner@proj-a"], "waiting")
    assert wait_until(lambda: str(message.id) in rig.daemon.queue.get("planner@proj-a", {}))
    before = list(rig.daemon.binding_history.intervals())
    old = client()
    try:
        with pytest.raises(DaemonError) as raised:
            old.request("register", slot=slot, name="planner", scope="proj-a",
                        adapter="claude-shim")
        answer = raised.value.response
        assert answer["refused"] is True
        assert "holder" not in answer and "token" not in answer
        assert "claims protocol 1" in answer["error"]
        assert "Relaunch the session" in answer["error"]
    finally:
        old.close()
    assert "planner@proj-a" not in rig.daemon.bindings
    assert list(rig.daemon.binding_history.intervals()) == before
    assert str(message.id) in rig.daemon.queue["planner@proj-a"]


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
