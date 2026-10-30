"""The claim socket on the new wire, and what each send outcome exits.

The claim socket speaks JSON-RPC through `moxi.ipc` and serves nothing of the old line
protocol: a comms 0.2 adapter's claim holder is answered "relaunch the session", a 0.2
claim frame at a new claim socket is an error that ends the connection, and 0.2's sweep of
dead claim files leaves the live new ones alone. Every send outcome exits with
`moxi.exit`'s codes, and an outcome that may have happened never exits `TEMPFAIL`.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest
from claim_kit import World
from conftest import wait_until

from moxi import exit as moxi_exit
from moxi import ipc
from moxi_comms import api, claimfile, claimhold, client as client_mod, sending, wire
from moxi_comms.claimhold import CLAIM_SOCKET_ENV, OLD_ADAPTER, AdapterMismatch
from moxi_comms.client import (DaemonClient, DaemonError, DaemonNoAnswer, DaemonOffline,
                               VersionMismatch)
from moxi_comms.membership import TREE
from moxi_comms.paths import daemon_socket, runtime_root
from moxi_comms.sender import SenderIdentity


@pytest.fixture
def world():
    w = World()
    yield w
    w.stop()


# ------------------------------------------------------------------ a 0.2 claim holder

@pytest.fixture
def zero_two_holder():
    """A comms 0.2 adapter's claim socket: it reads lines and answers each with 0.2's
    `{"ok": ...}` claim answer, whatever was asked."""
    root = runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "comms-adapter-0-2.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(8)
    listener.settimeout(0.2)
    seen: list = []
    done = threading.Event()

    def answer(conn) -> None:
        with conn:
            conn.settimeout(5.0)
            buf = b""
            try:
                while not done.is_set():
                    data = conn.recv(4096)
                    if not data:
                        return
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        seen.append(line)
                        conn.sendall(json.dumps({
                            "ok": True, "protocol": 2, "token": "t",
                            "claim": {"name": "planner", "scope": "proj-a",
                                      "qualified": "planner@proj-a"}}).encode() + b"\n")
            except OSError:
                return

    def accept() -> None:
        while not done.is_set():
            try:
                conn, _ = listener.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=answer, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield path, seen
    done.set()
    thread.join(timeout=2)
    listener.close()
    path.unlink(missing_ok=True)


def test_a_0_2_claim_holder_is_answered_relaunch_the_session(rig, zero_two_holder):
    path, seen = zero_two_holder
    with pytest.raises(AdapterMismatch) as e:
        claimhold.call(path, "claim", timeout=5.0)
    assert str(e.value) == OLD_ADAPTER
    assert "relaunch the session" in OLD_ADAPTER


def test_a_send_through_a_0_2_claim_holder_exits_error_and_sends_nothing(
        rig, zero_two_holder):
    path, _seen = zero_two_holder
    rig.shim(name="reviewer", scope="proj-a")
    before = rig.record.max_message_id(rig.names_channel)
    code, out = rig.send("reviewer@proj-a", "from an old session",
                         env={CLAIM_SOCKET_ENV: str(path)})
    assert code == moxi_exit.ERROR, out
    assert OLD_ADAPTER in out
    assert rig.record.max_message_id(rig.names_channel) == before


# ------------------------------------------------------------- a 0.2 frame, new socket

@pytest.mark.parametrize("frame", [
    {"op": "claim", "protocol": 2},
    {"op": "state"},
], ids=["claim", "state"])
def test_a_0_2_claim_frame_at_a_new_claim_socket_is_an_error_that_closes(rig, world,
                                                                         frame):
    s = world.session("planner")
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(5.0)
    conn.connect(s.socket)
    try:
        conn.sendall(json.dumps(frame).encode() + b"\n")
        buf = b""
        while b"\n" not in buf:
            data = conn.recv(4096)
            assert data, "the claim socket closed without an answer"
            buf += data
        answer = json.loads(buf.split(b"\n", 1)[0])
        assert answer.get("jsonrpc") == "2.0" and answer["error"]["code"] == -32600
        assert "ok" not in answer and "claim" not in answer
        rest = buf.split(b"\n", 1)[1]
        while True:                    # the claim socket ends the connection
            data = conn.recv(4096)
            if not data:
                break
            rest += data
        assert rest == b""
    finally:
        conn.close()
    assert s.state == claimfile.BOUND, "the holder is untouched"
    assert claimhold.call(s.socket, "state", timeout=5.0)["state"] == claimfile.BOUND


# ----------------------------------------------------------------- 0.2's claim sweep

def zero_two_sweep(proc) -> list:
    """comms 0.2's `sweep_dead_harness_files`, as 0.2 shipped it: every
    `comms-claim-*.json` in the runtime directory keyed by pid whose roots are all dead
    goes. Kept here as it was, so a change to today's sweep can't hide a 0.2 one."""
    gone = []
    for p in sorted(runtime_root().glob("comms-claim-*.json")):
        try:
            record = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("keying") != "pid":
            continue
        roots = [r for r in (record.get("roots") or ()) if isinstance(r, dict)]
        if roots and not any(proc.alive(r.get("pid"), r.get("start")) for r in roots):
            p.unlink()
            gone.append(p.name)
    return gone


def test_0_2s_claim_sweep_removes_no_live_new_claim_file(rig, world):
    sessions = [world.session("planner"), world.session("reviewer"),
                world.session("worker", bind=False)]
    files = {s.key: claimfile.file_for(s.key) for s in sessions}
    assert all(f.exists() for f in files.values())
    assert all(r.get("reach", TREE) for s in sessions
               for r in (s.record() or {}).get("roots", ()))
    assert zero_two_sweep(world.proc) == []
    assert all(f.exists() for f in files.values())
    assert [s.state for s in sessions[:2]] == [claimfile.BOUND, claimfile.BOUND]


# ---------------------------------------------------------------------- exit codes

def _stub_send(monkeypatch, make):
    def send(self, *a, **kw):
        raise make()

    monkeypatch.setattr(DaemonClient, "send", send)


@pytest.mark.parametrize("error, code, says", [
    (lambda: DaemonError("that name is closed", wire.NAME_RETIRED, {}),
     moxi_exit.REFUSED, "nothing was sent"),
    (lambda: DaemonError("the store is busy", wire.STORE_BUSY, {}),
     moxi_exit.TEMPFAIL, "try again"),
    (lambda: DaemonError("internal error", wire.INTERNAL_ERROR, {}),
     moxi_exit.ERROR, "Nothing was sent"),
    (lambda: VersionMismatch("the daemon is newer; restart this side"),
     moxi_exit.ERROR, "restart this side"),
    (lambda: DaemonOffline("comms offline: gone"),
     moxi_exit.TEMPFAIL, "Nothing was sent"),
], ids=["refusal", "try-again", "other-error", "version", "offline"])
def test_a_send_the_daemon_answers_with_an_error_exits_by_its_range(
        rig, monkeypatch, error, code, says):
    rig.shim(name="reviewer", scope="proj-a")
    _stub_send(monkeypatch, error)
    got, out = rig.send("reviewer@proj-a", "hello",
                        sender={"name": "planner", "scope": "proj-a"})
    assert got == code, out
    assert says in out


def test_a_send_whose_answer_never_comes_after_the_commit_exits_error_never_75(
        rig, monkeypatch):
    """The daemon committed the post; the answer was lost. The message was sent, so the
    exit is `ERROR` with outcome `unknown` and never "try again"."""
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    real = DaemonClient.send

    def lost(self, *a, **kw):
        real(self, *a, **kw)
        raise DaemonNoAnswer("timeout waiting for send")

    monkeypatch.setattr(DaemonClient, "send", lost)
    code, out = rig.send("reviewer@proj-a", "it went",
                         sender={"name": "planner", "scope": "proj-a"})
    assert code == moxi_exit.ERROR != moxi_exit.TEMPFAIL, out
    assert sending.NO_ANSWER in out
    assert wait_until(lambda: any(p["text"] == "it went" for p in reviewer.queue))

    rig.shim(name="planner", scope="proj-a")
    sender = SenderIdentity.for_agent("planner", "proj-a")
    with rig.session_holding(sender) as path:
        got = api.send(api.Sender.socket(path), "reviewer@proj-a", "and again",
                       posture="none")
    assert got.outcome == "unknown" and got.exit_code == moxi_exit.ERROR, got


def test_the_send_verbs_usage_and_refusal_exits(rig):
    rig.shim(name="reviewer", scope="proj-a")
    planner = {"name": "planner", "scope": "proj-a"}
    code, out = rig.send("reviewer@proj-a", "no posture", posture=None, sender=planner)
    assert code == moxi_exit.USAGE, out
    code, out = rig.send("nobody-here@proj-a", "to nobody", sender=planner)
    assert code == moxi_exit.REFUSED, out


def test_owner_with_an_empty_id_exits_usage(rig):
    code, out = rig.cli(["owner", ""])
    assert code == moxi_exit.USAGE, out


def test_a_verb_with_no_daemon_exits_tempfail(rig):
    rig.daemon.stop()
    code, out = rig.cli(["owner", "some-session"])
    assert code == moxi_exit.TEMPFAIL, out


# ------------------------------------------------------------------ the busy answer

def _busy_once(monkeypatch, method: str) -> list:
    """The daemon's first answer to `method` is `busy`, as a daemon with no room for
    the connection answers its first method; later ones are real."""
    real = ipc.Client.call
    asked: list = []

    def call(self, m, params=None, **kw):
        asked.append((id(self), m))
        if m == method and sum(1 for _, x in asked if x == method) == 1:
            raise ipc.RpcError(wire.BUSY, "busy", {"reason": "busy"})
        return real(self, m, params, **kw)

    monkeypatch.setattr(ipc.Client, "call", call)
    return asked


def test_a_verb_client_asks_a_busy_daemon_once_more_on_a_new_connection(rig, monkeypatch):
    monkeypatch.setattr(client_mod, "BUSY_RETRY", 0.01)
    asked = _busy_once(monkeypatch, "max_id")
    answer = client_mod.ask("max_id", socket_path=daemon_socket())
    assert "message_id" in answer
    tries = [conn for conn, m in asked if m == "max_id"]
    assert len(tries) == 2 and tries[0] != tries[1], "the second try is a new connection"


def test_a_client_that_reads_pushes_is_not_moved_to_a_new_connection(rig, monkeypatch):
    monkeypatch.setattr(client_mod, "BUSY_RETRY", 0.01)
    _busy_once(monkeypatch, "max_id")
    c = DaemonClient(daemon_socket(), on_push=lambda m, p: None).connect()
    try:
        with pytest.raises(DaemonError) as e:
            c.call("max_id")
        assert e.value.code == wire.BUSY and e.value.try_again
    finally:
        c.close()
