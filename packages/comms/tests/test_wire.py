"""The daemon socket's wire, through a real socket: the hello, each method, the error
codes, the old wire, and what the daemon does with a stuck client, an over-long line,
too many connections, a long catch-up and its own stop.

Most tests speak JSON lines on a bare socket (`Line`), so they control exactly what is
read and when: a client that stops reading is one that never calls `read`.
"""

from __future__ import annotations

import itertools
import json
import logging
import socket
import threading
import time
import uuid

import pytest
from conftest import (projects_root, queued, record_rows, wait_for_queue, wait_until,
                      write_transcript)

from moxi import ipc

from moxi_comms import directory, machine_line, wire
from moxi_comms.daemon import WINDOW, Daemon
from moxi_comms.paths import comms_db, daemon_socket, old_daemon_socket

pytestmark = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"),
                                reason="the daemon socket is a unix socket here; the "
                                       "named pipe is tested on Windows")

PLANNER = "planner@proj-a"
MAIN = "main@proj-a"
#: Text long enough that a few dozen deliveries fill a socket's buffers.
BULKY = "x" * 15_000


class Line:
    """A bare connection to the daemon socket that reads only when asked to."""

    def __init__(self, path=None, *, rcvbuf: int | None = None):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if rcvbuf is not None:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.sock.settimeout(10.0)
        self.sock.connect(str(path or daemon_socket()))
        self.buf = b""
        self.notes: list[tuple[str, dict]] = []
        self._ids = itertools.count(1)
        self._seq = itertools.count(1)

    def close(self) -> None:
        self.sock.close()

    def write(self, data: bytes) -> None:
        self.sock.sendall(data)

    def send(self, message: dict) -> None:
        self.write(json.dumps({"jsonrpc": "2.0", **message}).encode() + b"\n")

    def notify(self, method: str, params: dict | None = None) -> None:
        self.send({"method": method, "params": params or {}})

    def call(self, method: str, params: dict | None = None, *,
             timeout: float = 10.0) -> dict:
        """The whole answer: `{"result": ...}` or `{"error": ...}`. Notifications read
        on the way are kept in `notes`."""
        id = next(self._ids)
        self.send({"id": id, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            message = self.read(max(deadline - time.monotonic(), 0.01))
            assert message is not None, f"no answer to {method}: the connection closed"
            if message.get("id") == id:
                return message

    def read(self, timeout: float = 10.0) -> dict | None:
        """The next answer, filing notifications in `notes` as it goes; None at the end
        of the connection."""
        self.sock.settimeout(timeout)
        while b"\n" not in self.buf:
            try:
                data = self.sock.recv(1 << 16)
            except ConnectionResetError:
                return None
            if not data:
                return None
            self.buf += data
        line, self.buf = self.buf.split(b"\n", 1)
        message = json.loads(line)
        if "id" not in message:
            self.notes.append((message["method"], message.get("params") or {}))
            return self.read(timeout)
        return message

    def ends(self, timeout: float = 10.0) -> bool:
        """Read until the daemon ends the connection: True when it did."""
        self.sock.settimeout(timeout)
        try:
            while True:
                data = self.sock.recv(1 << 16)
                if not data:
                    return True
        except ConnectionResetError:
            return True
        except TimeoutError:
            return False

    def hello(self, versions=wire.VERSIONS, capabilities=()) -> dict:
        return self.call("rpc.hello", {"versions": list(versions),
                                       "capabilities": sorted(capabilities)})

    def seq(self) -> int:
        return next(self._seq)

    def delivered(self) -> list[str]:
        return [p["payload"]["message_id"] for m, p in self.notes if m == "deliver"]


def result(answer: dict):
    assert "error" not in answer, answer
    return answer["result"]


def error(answer: dict) -> dict:
    assert "error" in answer, answer
    return answer["error"]


@pytest.fixture
def lines():
    """Bare connections past their hello, closed at the end of the test."""
    made: list[Line] = []

    def _open(path=None, *, hello=True, **kw) -> Line:
        line = Line(path, **kw)
        made.append(line)
        if hello:
            assert result(line.hello())["version"] == wire.VERSIONS[1]
        return line

    yield _open
    for line in made:
        line.close()


def warm_session() -> str:
    session_id = str(uuid.uuid4())
    write_transcript(session_id, age=0)
    return session_id


def bind_params(name="planner", scope="proj-a", **extra) -> dict:
    """A claude-shim's bind, with a warm session behind it so the cache gate lets mail
    through."""
    return {"name": name, "scope": scope, "adapter": "claude-shim",
            "session_id": warm_session(), "projects_root": str(projects_root()), **extra}


def bound(line: Line, **kw) -> dict:
    return result(line.call("bind", bind_params(**kw)))


def retire(name: str, scope: str) -> None:
    conn = directory.connect(comms_db())
    try:
        with conn:
            conn.execute("INSERT OR IGNORE INTO identities(name, scope, status, created_at, "
                         "kind) VALUES (?, ?, 'retired', ?, 'agent')",
                         (name, scope, directory.now()))
    finally:
        conn.close()


def stopping(rig) -> None:
    """The first step of the daemon's stop, on its own: what a client sees between the
    stop starting and its connection closing."""
    with rig.daemon._lock:
        rig.daemon._stopping = True


def post(rig, to: str, text: str = "hello", n: int = 1) -> list[str]:
    return [str(rig.post_as("agent-6", [to], text).id) for _ in range(n)]


# =========================================================================== the hello

def test_the_hello_agrees_the_version_and_answers_the_daemons_own_capabilities(rig, lines):
    line = lines(hello=False)
    answer = result(line.hello(capabilities={"x-not-comms"}))
    assert answer["version"] == wire.VERSIONS[1]
    assert set(answer["capabilities"]) == {"delete", "expiry", "interrupt", "reserved-names",
                                           "mcp", "connector"}
    # Each side holds the other's set: the daemon's connection holds what the client
    # offered, not an intersection.
    bound(line)
    assert rig.daemon.bindings[PLANNER].conn.agreed.capabilities == {"x-not-comms"}


def test_the_ipc_client_holds_the_daemons_capabilities(rig):
    with ipc.connect(str(daemon_socket()), timeout=5.0) as client:
        agreed = client.hello(wire.VERSIONS, ["x-not-comms"])
    assert agreed.version == 1 and set(agreed.capabilities) == set(wire.CAPABILITIES)


def test_a_method_before_the_hello_is_refused_and_the_connection_closed(rig, lines):
    line = lines(hello=False)
    assert error(line.call("max_id"))["code"] == -32002
    assert line.ends()


@pytest.mark.parametrize("older", ["daemon", "client"])
def test_no_common_version_is_refused_naming_both_ranges(rig, monkeypatch, lines, older):
    """[1,1] against [2,2] both ways: the core's -32001 with both ranges in its data,
    and the text the client prints names the older side and its cure."""
    if older == "client":
        rig.daemon.stop()
        monkeypatch.setattr(wire, "VERSIONS", (2, 2))
        rig.daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False).start()
        offer = (1, 1)
    else:
        offer = (2, 2)
    line = lines(hello=False)
    refused = error(line.hello(versions=offer))
    theirs = [2, 2] if older == "client" else [1, 1]
    assert refused["code"] == wire.VERSION_REFUSED == -32001
    assert refused["data"] == {"client": list(offer), "server": theirs}
    text = wire.version_text(refused["data"], ours=offer)
    if older == "daemon":
        assert text == ("the comms daemon running speaks comms wire 1-1 and this comms "
                        "speaks 2-2: the daemon is older. Restart it (`moxi comms service "
                        "restart`), then run this again. Nothing was sent.")
    else:
        assert text == ("this comms speaks comms wire 1-1 and its daemon speaks 2-2: this "
                        "program is older than the daemon. Relaunch the session (an "
                        "adapter) or update this install (a verb). Nothing was sent.")
    # Nothing past the refused hello is a comms method.
    assert error(line.call("max_id"))["code"] == -32002


# ======================================================================= the codes

def test_the_codes_are_the_tables_and_their_ranges():
    assert wire.CODE_NAMES == {
        1101: "name-held", 1102: "name-reserved", 1103: "name-retired",
        1104: "name-unknown", 1105: "not-mailable", 1106: "credential", 1107: "too-long",
        1108: "not-sender", 1109: "already-delivered", 1110: "unknown-message",
        1111: "unknown-adapter", 1112: "not-bound", 1113: "mcp-token-taken",
        1114: "mcp-token-revoked", 1150: "stopping", 1151: "failed", 1152: "busy",
        1153: "store-busy"}
    for code in wire.CODE_NAMES:
        assert wire.is_refusal(code) == (code < 1150)
        assert wire.is_try_again(code) == (code >= 1150)
    for code in (-32700, -32601, -32602, -32603, -32001, 1100, 1200):
        assert not wire.is_refusal(code) and not wire.is_try_again(code)


def test_an_unknown_method_and_params_that_are_not_an_object(rig, lines):
    line = lines()
    assert error(line.call("ping"))["code"] == -32601, "ping is retired"
    assert error(line.call("frobnicate"))["code"] == -32601
    line.send({"id": 99, "method": "max_id", "params": ["not", "an", "object"]})
    assert error(line.read())["code"] == -32602


def test_an_accident_in_the_daemon_is_failed_and_logged(rig, lines, monkeypatch, caplog):
    def broken(frame):
        raise KeyError("the rooms table")

    monkeypatch.setattr(rig.daemon, "_rooms_query", broken)
    with caplog.at_level(logging.ERROR, logger="comms.daemon"):
        failed = error(lines().call("rooms"))
    assert failed["code"] == wire.FAILED and wire.is_try_again(failed["code"])
    assert "the rooms table" in failed["data"]["reason"]
    assert any("rooms failed" in r.getMessage() for r in caplog.records)


# ========================================================================== bind

def test_bind_answers_identity_token_and_high(rig, lines):
    answer = bound(lines())
    assert set(answer) == {"identity", "token", "high"}
    assert answer["identity"] == {"name": "planner", "scope": "proj-a", "tier": "chosen",
                                  "qualified": PLANNER}
    assert answer["token"] and answer["high"].isdigit()
    assert rig.daemon.bindings[PLANNER].adapter == "claude-shim"


def test_bind_of_a_held_name_is_name_held_naming_the_holder(rig, lines):
    bound(lines())
    held = error(lines().call("bind", bind_params()))
    assert held["code"] == wire.NAME_HELD and wire.is_refusal(held["code"])
    assert held["data"]["name"] == PLANNER
    assert held["data"]["holder"] == {"qualified": PLANNER, "adapter": "claude-shim"}
    assert "already bound" in held["data"]["reason"]


@pytest.mark.parametrize("params, code, named", [
    ({"name": "agent-7"}, wire.NAME_RESERVED, "agent-7"),
    ({"name": "gone"}, wire.NAME_RETIRED, "gone@proj-a"),
    ({"adapter": "no-such-kind"}, wire.UNKNOWN_ADAPTER, None),
    ({"name": "Not A Name!"}, wire.INVALID_PARAMS, None),
], ids=["agent-n", "retired", "unknown-adapter", "invalid-name"])
def test_bind_refusals(rig, lines, params, code, named):
    retire("gone", "proj-a")
    refused = error(lines().call("bind", bind_params(**params)))
    assert refused["code"] == code
    assert refused["data"]["reason"] == refused["message"]
    if named is not None:
        assert refused["data"]["name"] == named
    assert not rig.daemon.bindings


def test_bind_and_send_while_stopping_are_stopping(rig, lines):
    line = lines()
    token = bound(line)["token"]
    stopping(rig)
    for method, params in (("bind", bind_params(name="main")),
                           ("send", {"as": PLANNER, "token": token, "content": "x"})):
        refused = error(lines().call(method, params))
        assert refused["code"] == wire.STOPPING and wire.is_try_again(refused["code"])


# ========================================================================== send

def test_send_as_a_bound_identity_answers_the_message_id(rig, lines):
    line = lines()
    token = bound(line)["token"]
    content = machine_line.compose(["main@proj-a"], "the parser diff")
    sent = result(line.call("send", {"as": PLANNER, "token": token, "content": content}))
    assert set(sent) == {"message_id"}
    message = rig.record.get_message(sent["message_id"])
    assert message.content == content


def test_send_from_another_connection_with_the_bindings_token(rig, lines):
    """The token proves the sender, not the connection: a verb sends on a connection of
    its own."""
    token = bound(lines())["token"]
    sent = result(lines().call("send", {"as": PLANNER, "token": token, "content": "hi"}))
    assert rig.record.get_message(sent["message_id"]) is not None


@pytest.mark.parametrize("params, code", [
    ({"token": "wrong"}, wire.CREDENTIAL),
    ({"as": "nobody@proj-a"}, wire.CREDENTIAL),
    ({"content": "x" * 16_001}, wire.TOO_LONG),
    ({"as": None}, wire.INVALID_PARAMS),
    ({"content": 7}, wire.INVALID_PARAMS),
], ids=["wrong-token", "unbound-as", "too-long", "no-as", "content-not-text"])
def test_send_refusals(rig, lines, params, code):
    line = lines()
    token = bound(line)["token"]
    before = rig.record.max_message_id(rig.names_channel)
    refused = error(line.call("send", {"as": PLANNER, "token": token, "content": "hi",
                                       **params}))
    assert refused["code"] == code and refused["data"]["reason"]
    if code == wire.CREDENTIAL:
        assert refused["data"]["credential"] is True
    assert rig.record.max_message_id(rig.names_channel) == before, "nothing was sent"


def test_send_under_a_name_retired_while_bound_is_name_retired(rig, lines):
    line = lines()
    token = bound(line)["token"]
    retire("planner", "proj-a")
    refused = error(line.call("send", {"as": PLANNER, "token": token, "content": "hi"}))
    assert refused["code"] == wire.NAME_RETIRED and refused["data"]["name"] == PLANNER


@pytest.mark.parametrize("backend", ["local"], indirect=True)
def test_send_as_the_operator_takes_the_operators_credential(rig, lines):
    line = lines()
    sent = result(line.call("send", {"as": "operator", "token": rig.operator_token,
                                     "content": "from the operator"}))
    assert rig.record.get_message(sent["message_id"]).content == "from the operator"
    refused = error(line.call("send", {"as": "operator", "token": "wrong",
                                       "content": "not from the operator"}))
    assert refused["code"] == wire.CREDENTIAL and refused["data"]["credential"] is True


# ========================================================================== reads

READS = [
    ("resolve", {"name": "main@proj-a"},
     {"tier", "route", "qualified", "bound", "deferred", "send_only", "standing_row",
      "ever_bound", "seen_as"}),
    ("agents", {}, {"bindings", "waiting", "pending", "unanswered",
                    "standing"}),
    ("session", {"identity": PLANNER},
     {"identity", "bound", "adapter", "session_id", "session_seen_at", "bound_at",
      "bound_for", "cache_kind", "age_seconds", "cache_reason", "cache_ttl", "cache_key",
      "cache_code", "cache_source", "endpoint", "endpoint_for"}),
    ("owner", {"session_id": "no-such-session"},
     {"session_id", "owner", "adapter", "awaiting"}),
    ("asks", {"identity": PLANNER}, None),
    ("owed", {"identity": PLANNER}, None),
    ("readable", {"identity": PLANNER}, None),
    ("explain", {"identity": PLANNER},
     {"identity", "bound", "binding", "delivery", "deliveries", "waiting",
      "injected_through", "digest", "activity_for", "cursor", "case", "case_error"}),
    ("binding_window", {"name": "agent-1"}, {"window"}),
    ("attributed_message", {"message_id": None},
     {"message_id", "room", "author", "recipients", "resolved", "text", "timestamp"}),
    ("rooms", {}, {"rooms"}),
    ("message", {"message_id": None}, {"message"}),
    ("history", {}, {"messages"}),
    ("max_id", {}, {"message_id"}),
    ("last_digest", {"identity": PLANNER}, {"identity", "bound", "through", "digest"}),
]


@pytest.mark.parametrize("method, params, keys", READS, ids=[r[0] for r in READS])
def test_each_read_answers_on_any_connection(rig, lines, method, params, keys):
    """Every read is its own method, answered on a connection that holds no binding.
    A read of one message asks for one the record holds."""
    if "message_id" in params and params["message_id"] is None:
        params = {"message_id": post(rig, PLANNER, "one message")[0]}
    answer = result(lines().call(method, params))
    assert isinstance(answer, dict)
    if keys is not None:
        assert set(answer) == keys


def test_the_reads_answer_what_the_daemon_holds(rig, lines):
    line = lines()
    bound(line)
    mid = post(rig, PLANNER, "for the planner")[0]
    assert wait_until(lambda: result(line.call("max_id")) and mid in line.delivered())
    reader = lines()
    assert result(reader.call("max_id"))["message_id"] == mid
    assert result(reader.call("message", {"message_id": mid}))["message"]["id"] == mid
    assert mid in [m["id"] for m in result(reader.call("history"))["messages"]]
    attributed = result(reader.call("attributed_message", {"message_id": mid}))
    assert "found" not in attributed and attributed["recipients"] == [PLANNER]
    assert result(reader.call("session", {"identity": PLANNER}))["bound"] is True
    assert result(reader.call("resolve", {"name": PLANNER}))["bound"] is True
    assert [b["qualified"] for b in result(reader.call("agents"))["bindings"]] == [PLANNER]
    assert "#names" in [r["name"] for r in result(reader.call("rooms"))["rooms"]]


@pytest.mark.parametrize("method, params, code", [
    ("resolve", {"name": "agent-99"}, wire.NAME_UNKNOWN),
    ("resolve", {"name": "gone@proj-a"}, wire.NAME_RETIRED),
    ("owner", {"session_id": ""}, wire.INVALID_PARAMS),
    ("attributed_message", {"message_id": "not-an-id"}, wire.INVALID_PARAMS),
], ids=["resolve-unknown", "resolve-retired", "owner-no-id", "attributed-not-an-id"])
def test_read_refusals(rig, lines, method, params, code):
    retire("gone", "proj-a")
    refused = error(lines().call(method, params))
    assert refused["code"] == code and refused["data"]["reason"]
    if code in (wire.NAME_UNKNOWN, wire.NAME_RETIRED):
        assert refused["data"]["name"] == params["name"]


@pytest.mark.parametrize("method", ["message", "attributed_message"])
def test_a_message_the_record_does_not_hold_is_refused_as_unknown_message(rig, lines,
                                                                          method):
    """An id the record doesn't hold is `unknown-message`, naming the id, and the
    connection stays open."""
    line = lines()
    refused = error(line.call(method, {"message_id": "999999"}))
    assert refused["code"] == wire.UNKNOWN_MESSAGE and wire.is_refusal(refused["code"])
    assert refused["data"]["message_id"] == "999999" and refused["data"]["reason"]
    assert result(line.call("max_id"))


def test_the_client_reads_an_unknown_message_as_none(rig):
    from moxi_comms import client
    assert client.attributed_message("999999", socket_path=daemon_socket()) is None


# =============================================================== the adapter's methods

@pytest.mark.parametrize("method", ["turn_ended", "injected", "ready", "look", "activity",
                                    "unanswered"])
def test_an_adapter_method_on_a_connection_with_no_binding_is_not_bound(rig, lines, method):
    bound(lines())          # the name is bound, on another connection
    refused = error(lines().call(method, {"identity": PLANNER, "message_id": "1",
                                          "author": "main@proj-a", "through": "1"}))
    assert refused["code"] == wire.NOT_BOUND and wire.is_refusal(refused["code"])


def test_injected_and_activity_on_the_bindings_own_connection(rig, lines):
    line = lines()
    bound(line)
    mid = rig.post_as("main@proj-a", [PLANNER], "for the planner").id
    assert wait_until(lambda: mid in rig.daemon.bindings[PLANNER].delivered)
    opened = result(line.call("open_digest", {"ids": [mid]}))["digest"]
    other = warm_session()
    line.notify("activity", {"identity": PLANNER, "session_id": other, "seq": line.seq()})
    line.notify("injected", {"identity": PLANNER, "digest": opened, "text": "the digest",
                             "seq": line.seq()})
    result(line.call("max_id"))
    assert rig.daemon.bindings[PLANNER].session_id == other
    digest = result(lines().call("last_digest", {"identity": PLANNER}))
    assert digest == {"identity": PLANNER, "bound": True, "through": mid,
                      "digest": "the digest"}


def test_unanswered_on_the_bindings_own_connection_is_kept(rig, lines):
    line = lines()
    bound(line)
    line.notify("unanswered", {"identity": PLANNER, "message_id": "12",
                               "author": "main@proj-a"})
    result(line.call("max_id"))
    rows = result(lines().call("agents"))["unanswered"]
    assert [(r["owing"], r["message_id"]) for r in rows] == [(PLANNER, "12")]


def test_ready_and_look_reach_the_binding(rig, lines, monkeypatch):
    seen = []
    monkeypatch.setattr(rig.daemon, "_on_ready", lambda b, source: seen.append(
        ("ready", b.qualified, source)))
    monkeypatch.setattr(rig.daemon, "_on_look", lambda b: seen.append(("look", b.qualified)))
    line = lines()
    bound(line)
    line.notify("ready", {"identity": PLANNER, "source": "startup", "seq": line.seq()})
    line.notify("look", {"identity": PLANNER, "seq": line.seq()})
    result(line.call("max_id"))
    assert seen == [("ready", PLANNER, "startup"), ("look", PLANNER)]


# ======================================================================== the old wire

@pytest.fixture
def zero_two_daemon():
    """Something listening at comms 0.2's daemon path, as a 0.2 daemon left running
    would: it counts the connections it is offered."""
    path = old_daemon_socket()
    assert str(path) != str(daemon_socket())
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(8)
    listener.settimeout(0.05)
    accepted: list = []
    done = threading.Event()

    def accept() -> None:
        while not done.is_set():
            try:
                conn, _ = listener.accept()
            except (TimeoutError, OSError):
                continue
            accepted.append(conn)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield path, accepted
    done.set()
    thread.join(timeout=2)
    listener.close()
    for conn in accepted:
        conn.close()


def test_the_daemon_never_binds_removes_or_connects_to_the_0_2_path(rig, zero_two_daemon):
    """A daemon's whole life, with a 0.2 daemon still listening at its old path: the new
    daemon serves only its own address, and a bind, a send and a stop never touch the
    old one."""
    path, accepted = zero_two_daemon
    inode = path.stat().st_ino
    rig.daemon.stop()
    rig.daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False).start()
    rig.shim(name="planner", scope="proj-a")
    code, out = rig.send("main@proj-a", "hello", sender={"name": "planner",
                                                         "scope": "proj-a"})
    assert code == 0, out
    rig.daemon.stop()
    assert path.stat().st_ino == inode, "the 0.2 socket file is where it was"
    assert accepted == [], "nothing of this build connected to it"


@pytest.mark.parametrize("frame", [
    {"op": "register", "name": "planner", "scope": "proj-a", "protocol": 2},
    {"op": "post", "identity": PLANNER, "token": "t", "content": "hi"},
    {"op": "ping", "protocol": 3},
], ids=["register", "post", "ping"])
def test_a_0_2_frame_at_the_new_socket_is_an_error_that_closes_and_does_nothing(rig, lines,
                                                                            frame):
    line = lines(hello=False)
    before = rig.record.max_message_id(rig.names_channel)
    line.write(json.dumps(frame).encode() + b"\n")
    refused = error(line.read())
    assert refused["code"] == -32600 and "ok" not in refused
    assert line.ends(), "the daemon closes the connection after the error"
    assert not rig.daemon.bindings
    assert rig.record.max_message_id(rig.names_channel) == before


# ================================================================== a client that stops

def stall(rig, lines, n: int = 32) -> tuple[Line, list[str]]:
    """`main@proj-a` bound on a connection that never reads again, and `n` bulky
    messages sent to it: more than the socket's buffers hold, so the daemon's writer
    waits on it."""
    stuck = lines(rcvbuf=4096)
    bound(stuck, name="main")
    ids = post(rig, MAIN, BULKY, n=n)
    assert n <= WINDOW
    assert wait_until(lambda: all((MAIN, mid) in rig.daemon.handed_at for mid in ids))
    return stuck, ids


def test_a_client_that_stops_reading_is_dropped_and_its_mail_waits_for_its_next_bind(
        rig, lines):
    other = rig.shim(name="planner", scope="proj-a")
    started = time.monotonic()
    _, ids = stall(rig, lines)
    # Other bindings keep receiving while the stuck one is still bound.
    meanwhile = post(rig, PLANNER, "meanwhile")[0]
    assert wait_until(lambda: meanwhile in [p["message_id"] for p in other.queue])
    assert MAIN in rig.daemon.bindings
    assert wait_until(lambda: MAIN not in rig.daemon.bindings,
                      timeout=wire.WRITE_DEADLINE + 5)
    assert time.monotonic() - started < wire.WRITE_DEADLINE + 2, "within the deadline"
    assert set(ids) <= queued(rig.daemon, MAIN), "its mail waits again"
    again = rig.shim(name="main", scope="proj-a")
    wait_for_queue(again, len(ids))
    assert set(ids) <= {p["message_id"] for p in again.queue}


def test_a_stop_with_a_stuck_client_ends_within_six_seconds(rig, lines):
    _, ids = stall(rig, lines)
    started = time.monotonic()
    rig.daemon.stop()
    assert time.monotonic() - started < 6.0
    assert set(ids) <= record_rows(rig.record, MAIN), "its mail is queued in the record"


# ============================================================== the line cap and blanks

#: The core's cap on one line, in bytes without its newline.
MAX_LINE = 4 << 20


def test_blank_lines_are_skipped_and_an_over_long_line_drops_only_its_connection(rig,
                                                                                 lines):
    other = rig.shim(name="planner", scope="proj-a")
    line = lines()
    line.write(b"\n" * 5000)
    assert set(result(line.call("max_id"))) == {"message_id"}
    long = lines()
    try:
        long.write(b"x" * (MAX_LINE + 1))
    except (BrokenPipeError, ConnectionResetError):
        pass
    refused = error(long.read())
    assert refused["code"] == -32600 and str(MAX_LINE) in refused["message"]
    assert long.ends()
    # Only that connection: the other line and the bound shim are as they were.
    assert set(result(line.call("max_id"))) == {"message_id"}
    mid = post(rig, PLANNER, "after the long line")[0]
    assert wait_until(lambda: mid in [p["message_id"] for p in other.queue])


# ================================================================= the connection cap

def test_the_connection_past_the_cap_is_busy_at_its_first_method(rig, lines):
    held = [lines() for _ in range(wire.MAX_CONNECTIONS)]
    for line in held:
        result(line.call("max_id"))
    assert rig.daemon.host.connections == wire.MAX_CONNECTIONS == 256
    extra = lines()             # its hello is answered: the hello never reaches comms
    busy = error(extra.call("max_id"))
    assert busy["code"] == wire.BUSY and wire.is_try_again(busy["code"])
    assert "256 connections" in busy["data"]["reason"]
    assert extra.ends(), "and the connection is closed after the answer"
    lines()                     # a connection that only says hello is never counted
    assert rig.daemon.host.connections == wire.MAX_CONNECTIONS
    held.pop().close()
    assert wait_until(lambda: rig.daemon.host.connections == wire.MAX_CONNECTIONS - 1)
    assert set(result(lines().call("max_id"))) == {"message_id"}


# =========================================================================== the window

def take(line: Line, taken: list[str]) -> list[str]:
    """What has arrived since `taken`, after a round trip; then those ids are put in a
    digest (`open_digest`, then `injected` naming it), which frees the window for the
    next."""
    result(line.call("max_id"))
    arrived = line.delivered()
    assert arrived[:len(taken)] == taken
    fresh = arrived[len(taken):]
    if fresh:
        digest = result(line.call("open_digest", {"ids": fresh}))["digest"]
        line.notify("injected", {"identity": MAIN, "digest": digest, "text": "digest",
                                 "seq": line.seq()})
    return fresh


def waiting(rig, ids: list[str]) -> None:
    assert wait_until(lambda: set(ids) <= queued(rig.daemon, MAIN), timeout=30)


def test_a_long_catch_up_reaches_a_slow_adapter_a_window_at_a_time(rig, lines):
    ids = post(rig, MAIN, "waiting", n=400)
    waiting(rig, ids)
    line = lines()
    bound(line, name="main")
    conn = rig.daemon.bindings[MAIN].conn
    taken: list[str] = []
    while len(taken) < len(ids):
        fresh = take(line, taken)
        assert len(fresh) == min(WINDOW, len(ids) - len(taken)), len(taken)
        taken += fresh
    assert take(line, taken) == []
    assert taken == sorted(ids, key=int), "oldest first, each once"
    assert rig.daemon.bindings[MAIN].conn is conn, "the connection was never dropped"


def test_a_live_send_behind_a_full_window_arrives_after_the_older_mail(rig, lines):
    older = post(rig, MAIN, "waiting", n=WINDOW + 10)
    waiting(rig, older)
    line = lines()
    bound(line, name="main")
    first = take(line, [])
    assert first == older[:WINDOW]
    # `take` freed the window just now; fill it again before the live send.
    rest = take(line, first)
    assert rest == older[WINDOW:]
    more = post(rig, MAIN, "waiting", n=WINDOW + 5)
    assert wait_until(lambda: len(rig.daemon.bindings[MAIN].overflow) == 5)
    live = post(rig, MAIN, "live")[0]
    assert wait_until(lambda: live in rig.daemon.bindings[MAIN].overflow)
    taken = first + rest
    batch = take(line, taken)
    assert batch == more[:WINDOW] and live not in batch
    taken += batch
    assert take(line, taken) == more[WINDOW:] + [live], "after the older rows"
