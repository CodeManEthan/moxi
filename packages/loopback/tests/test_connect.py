"""The server proves itself: `call` and `open_connection` against a small stdlib server
that answers `/api/prove` through `proof()`, and against stand-ins on a freed port."""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest
from moxi.paths import Layout, socket_path
from moxi_dev.testing import wait_for

from moxi_loopback.check import (
    RequestView,
    browser,
    check,
    proof,
    prove_nonce,
    token_matches,
    upstream,
)
from moxi_loopback.routes import Table
from moxi_loopback.runtime import RuntimeInfo, read_runtime, write_runtime
from moxi_loopback.transport import (
    NotOurServer,
    bind_tcp,
    bind_unix,
    call,
    open_connection,
)

posix = pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets")


# --- a small page server ------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def address_string(self):
        return "client"

    def _answer(self, status, headers=(), body=b""):
        self.send_response_only(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        stub = self.server.stub
        raw_path = self.path.partition("?")[0].encode("latin-1")
        headers = [(k.lower().encode("latin-1"), v.encode("latin-1"))
                   for k, v in self.headers.items()]
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        stub.seen.append((self.command, self.path, headers, body))
        view = RequestView(self.command, raw_path, headers, stub.transport, stub.port)
        refusal = check(view, stub.profile)
        if refusal is not None:
            return self._answer(refusal.status)
        if raw_path == b"/api/prove":
            nonce = prove_nonce(view)
            if nonce is None:
                return self._answer(400)
            return self._answer(204, [("X-Moxi-Proof",
                                       proof(stub.token, stub.server_id, nonce))])
        answer = {"method": self.command, "path": self.path,
                  "body": json.loads(body) if body else None}
        return self._answer(200, [("Content-Type", "application/json")],
                            json.dumps(answer).encode())

    do_GET = do_POST = _handle


class _TCP(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True


#: Windows' socketserver has no Unix server; the tests that use one skip there.
class _Unix(socketserver.ThreadingMixIn, getattr(socketserver, "UnixStreamServer", object)):
    daemon_threads = True


def _table() -> Table:
    table = Table()
    table.add("GET", "/api/prove")
    table.add("GET", "/api/echo", "read")
    table.add("POST", "/api/act", "act")
    return table


class Stub:
    """A page server on a pre-bound socket: the check, `/api/prove` and an echo route."""

    def __init__(self, sock, *, token, server_id, shell=False):
        self.token, self.server_id, self.seen = token, server_id, []
        if sock.family == socket.AF_INET:
            self.transport, self.port = "tcp", sock.getsockname()[1]
            server_class = _TCP
        else:
            self.transport, self.port = "unix", None
            server_class = _Unix
        if shell:
            self.profile = browser(
                _table(), names={"127.0.0.1"},
                session=lambda view, entry: entry is not None and (
                    entry.raw_path == "/api/prove" or token_matches(view, token)))
        else:
            self.profile = upstream(_table(), token=token, port=self.port)
        self.server = server_class(sock.getsockname(), _Handler, bind_and_activate=False)
        self.server.socket.close()
        self.server.socket = sock
        self.server.stub = self
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


@pytest.fixture
def stubs():
    running: list[Stub] = []

    def start(sock, **kwargs):
        kwargs.setdefault("token", secrets.token_urlsafe(32))
        kwargs.setdefault("server_id", secrets.token_hex(16))
        stub = Stub(sock, **kwargs)
        running.append(stub)
        return stub

    yield start
    for stub in running:
        stub.stop()


def info_for(stub, **changes) -> RuntimeInfo:
    values = dict(module="demo", server_id=stub.server_id, pid=1, transport=stub.transport,
                  address="127.0.0.1" if stub.transport == "tcp" else stub.address,
                  port=stub.port, token=stub.token, version="0.1.0",
                  started_at="2026-10-30T12:00:00Z")
    values.update(changes)
    return RuntimeInfo(**values)


def tcp_stub(stubs, **kwargs):
    return stubs(bind_tcp(), **kwargs)


def header(seen_headers, name):
    return [value for key, value in seen_headers if key == name]


# --- a stand-in on a freed port -----------------------------------------------------


class StandIn:
    """Whatever took a freed port: it accepts, records every byte it is sent, and answers
    the first request head with `answer(received)`. None never answers; an empty answer
    closes the connection."""

    def __init__(self, port, answer):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen()
        self.answer = answer
        self.received = b""
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        conn, _ = self.sock.accept()
        conn.settimeout(5)
        answered = False
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                self.received += data
                if not answered and b"\r\n\r\n" in self.received and self.answer is not None:
                    answered = True
                    reply = self.answer(self.received)
                    if not reply:
                        break  # an empty answer: close the connection unanswered
                    conn.sendall(reply)
        except OSError:
            pass
        finally:
            conn.close()
            self.done.set()

    def close(self):
        self.sock.close()


@pytest.fixture
def stand_ins():
    made: list[StandIn] = []

    def make(port, answer):
        stand_in = StandIn(port, answer)
        made.append(stand_in)
        return stand_in

    yield make
    for stand_in in made:
        stand_in.close()


def _nonce(received: bytes) -> str:
    for line in received.split(b"\r\n"):
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"x-moxi-nonce":
            return value.strip().decode()
    raise AssertionError("no nonce sent")


def _proof_answer(value: str) -> bytes:
    return (f"HTTP/1.1 204 No Content\r\nX-Moxi-Proof: {value}\r\n\r\n").encode()


def freed_port(stubs):
    """A server that ran and was killed: its runtime info, and its port now free."""
    stub = tcp_stub(stubs)
    info = info_for(stub)
    stub.stop()
    return stub, info


def assert_only_the_proof_was_sent(stand_in):
    wait_for(stand_in.done.is_set, what="the client to close")
    received = stand_in.received
    assert received.startswith(b"GET /api/prove HTTP/1.1\r\n")
    assert received.count(b"HTTP/1.1") == 1
    assert received.endswith(b"\r\n\r\n")
    assert b"x-moxi-token" not in received.lower()


# --- call ---------------------------------------------------------------------------


def test_call_proves_then_sends_the_request_with_the_token(stubs):
    stub = tcp_stub(stubs)
    status, data = call(info_for(stub), "GET", "/api/echo?days=3")
    assert (status, data) == (200, {"method": "GET", "path": "/api/echo?days=3",
                                    "body": None})
    (prove, request) = stub.seen
    assert prove[1] == "/api/prove"
    assert header(prove[2], b"x-moxi-token") == []
    assert len(header(prove[2], b"x-moxi-nonce")) == 1
    assert header(request[2], b"x-moxi-token") == [stub.token.encode()]
    assert header(request[2], b"host") == [f"127.0.0.1:{stub.port}".encode()]


def test_call_sends_a_json_body_on_an_act(stubs):
    stub = tcp_stub(stubs)
    status, data = call(info_for(stub), "POST", "/api/act", {"force": True})
    assert status == 200 and data["body"] == {"force": True}
    request = stub.seen[-1]
    assert header(request[2], b"content-type") == [b"application/json"]


def test_call_returns_none_for_an_empty_answer(stubs):
    stub = tcp_stub(stubs)
    assert call(info_for(stub), "GET", "/api/missing") == (404, None)
    # An act without JSON is refused by the check; the call still reads the answer.
    assert call(info_for(stub), "POST", "/api/act") == (403, None)


def test_call_uses_a_fresh_nonce_each_time(stubs):
    stub = tcp_stub(stubs)
    call(info_for(stub), "GET", "/api/echo")
    call(info_for(stub), "GET", "/api/echo")
    nonces = [header(seen[2], b"x-moxi-nonce")[0] for seen in stub.seen
              if seen[1] == "/api/prove"]
    assert len(nonces) == 2 and nonces[0] != nonces[1]


@posix
def test_call_over_a_unix_socket_sends_host_localhost(stubs):
    Layout("demo").runtime_dir(create=True)
    path = socket_path("demo", "page")
    stub = stubs(bind_unix(path))
    stub.address = path
    assert call(info_for(stub), "GET", "/api/echo")[0] == 200
    assert all(header(seen[2], b"host") == [b"localhost"] for seen in stub.seen)


def test_call_through_the_runtime_file(stubs):
    Layout("demo").runtime_dir(create=True)
    sock = bind_tcp()
    written = write_runtime("demo", transport="tcp", address="127.0.0.1",
                            port=sock.getsockname()[1], version="0.1.0")
    stubs(sock, token=written.token, server_id=written.server_id)
    assert call(read_runtime("demo"), "GET", "/api/echo")[0] == 200


def test_call_proves_a_shell_style_listener_on_a_browser_profile(stubs):
    stub = tcp_stub(stubs, shell=True)
    status, data = call(info_for(stub), "POST", "/api/act", {"stop": True})
    assert status == 200 and data["body"] == {"stop": True}
    request = stub.seen[-1]
    assert header(request[2], b"origin") == []


def test_a_server_with_another_server_id_fails_the_proof(stubs):
    stub = tcp_stub(stubs)
    with pytest.raises(NotOurServer):
        call(info_for(stub, server_id=secrets.token_hex(16)), "GET", "/api/echo")
    assert [seen[1] for seen in stub.seen] == ["/api/prove"]


def test_a_server_with_another_token_fails_the_proof(stubs):
    stub = tcp_stub(stubs)
    with pytest.raises(NotOurServer):
        call(info_for(stub, token=secrets.token_urlsafe(32)), "GET", "/api/echo")
    assert [seen[1] for seen in stub.seen] == ["/api/prove"]


@pytest.mark.parametrize("answer", [
    lambda got: _proof_answer("0" * 64),
    lambda got: b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
    lambda got: b"HTTP/1.1 204 No Content\r\n\r\n",
    lambda got: b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
                b"Content-Length: 0\r\n\r\n",
    lambda got: b"not http at all\r\n\r\n",
    lambda got: b"",
], ids=["wrong-proof", "200", "no-proof", "503", "garbage", "silent-close"])
def test_a_stand_in_on_a_freed_port_gets_only_the_proof(stubs, stand_ins, answer):
    stub, info = freed_port(stubs)
    stand_in = stand_ins(stub.port, answer)
    start = time.monotonic()
    with pytest.raises(NotOurServer):
        call(info, "GET", "/api/echo", timeout=3)
    assert time.monotonic() - start < 0.9
    assert_only_the_proof_was_sent(stand_in)


def test_a_replayed_proof_fails_against_a_new_nonce(stubs, stand_ins):
    # Record a real proof for one nonce, then let a stand-in answer every nonce with it.
    stub = tcp_stub(stubs)
    info = info_for(stub)
    old_nonce = secrets.token_urlsafe(32)
    recorded = proof(stub.token, stub.server_id, old_nonce)
    stub.stop()
    stand_in = stand_ins(stub.port, lambda got: _proof_answer(recorded))
    with pytest.raises(NotOurServer):
        call(info, "GET", "/api/echo")
    assert _nonce(stand_in.received) != old_nonce
    assert_only_the_proof_was_sent(stand_in)


def test_a_stand_in_that_never_answers_times_out_at_the_callers_deadline(stubs, stand_ins):
    stub, info = freed_port(stubs)
    stand_in = stand_ins(stub.port, None)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        call(info, "GET", "/api/echo", timeout=0.3)
    elapsed = time.monotonic() - start
    assert 0.25 <= elapsed < 0.9
    assert_only_the_proof_was_sent(stand_in)


def test_a_proof_with_no_answer_fails_after_a_second(stubs, stand_ins):
    stub, info = freed_port(stubs)
    stand_in = stand_ins(stub.port, None)
    start = time.monotonic()
    with pytest.raises(NotOurServer):
        call(info, "GET", "/api/echo", timeout=5)
    elapsed = time.monotonic() - start
    assert 0.9 <= elapsed < 2.0
    assert_only_the_proof_was_sent(stand_in)


def test_a_slow_answer_after_the_proof_times_out_at_the_deadline(stubs, monkeypatch):
    stub = tcp_stub(stubs)
    handle = _Handler._handle

    def slow(self):
        if self.path != "/api/prove":
            time.sleep(1.0)
        return handle(self)

    monkeypatch.setattr(_Handler, "do_GET", slow)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        call(info_for(stub), "GET", "/api/echo", timeout=0.4)
    assert time.monotonic() - start < 0.9


def test_a_refused_connect_raises_its_error(stubs):
    stub, info = freed_port(stubs)
    with pytest.raises(ConnectionRefusedError):
        call(info, "GET", "/api/echo")


def test_an_answer_that_is_not_json_raises(stubs, monkeypatch):
    stub = tcp_stub(stubs)

    def text(self):
        if self.path == "/api/prove":
            return _Handler._handle(self)
        return self._answer(200, [("Content-Type", "text/html")], b"<p>hi</p>")

    monkeypatch.setattr(_Handler, "do_GET", text)
    with pytest.raises(ValueError):
        call(info_for(stub), "GET", "/api/echo")


# --- open_connection ----------------------------------------------------------------


async def _request(reader, writer, info, path):
    host = f"127.0.0.1:{info.port}" if info.transport == "tcp" else "localhost"
    writer.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nX-Moxi-Token: {info.token}\r\n"
                 f"Connection: close\r\n\r\n".encode())
    await writer.drain()
    answer = await reader.read()
    writer.close()
    return answer


def test_open_connection_proves_and_leaves_the_connection_to_the_caller(stubs):
    stub = tcp_stub(stubs)
    info = info_for(stub)

    async def go():
        reader, writer = await open_connection(info)
        return await _request(reader, writer, info, "/api/echo")

    answer = asyncio.run(go())
    assert answer.startswith(b"HTTP/1.1 200")
    assert [seen[1] for seen in stub.seen] == ["/api/prove", "/api/echo"]
    assert header(stub.seen[0][2], b"x-moxi-token") == []


@posix
def test_open_connection_over_a_unix_socket(stubs):
    Layout("demo").runtime_dir(create=True)
    path = socket_path("demo", "page")
    stub = stubs(bind_unix(path))
    stub.address = path
    info = info_for(stub)

    async def go():
        reader, writer = await open_connection(info)
        return await _request(reader, writer, info, "/api/echo")

    assert asyncio.run(go()).startswith(b"HTTP/1.1 200")
    assert header(stub.seen[1][2], b"host") == [b"localhost"]


def test_open_connection_to_a_stand_in_raises_and_sends_nothing_more(stubs, stand_ins):
    stub, info = freed_port(stubs)
    stand_in = stand_ins(stub.port, lambda got: _proof_answer("f" * 64))
    with pytest.raises(NotOurServer):
        asyncio.run(open_connection(info))
    assert_only_the_proof_was_sent(stand_in)


def test_open_connection_refuses_a_server_with_another_server_id(stubs):
    stub = tcp_stub(stubs)
    with pytest.raises(NotOurServer):
        asyncio.run(open_connection(info_for(stub, server_id=secrets.token_hex(16))))


def test_open_connection_refuses_a_proof_that_closes_the_connection(stubs, stand_ins):
    stub, info = freed_port(stubs)

    def closing(got):
        value = proof(info.token, info.server_id, _nonce(got))
        return (f"HTTP/1.1 204 No Content\r\nX-Moxi-Proof: {value}\r\n"
                f"Connection: close\r\n\r\n").encode()

    stand_in = stand_ins(stub.port, closing)
    with pytest.raises(NotOurServer):
        asyncio.run(open_connection(info))
    assert_only_the_proof_was_sent(stand_in)


def test_open_connection_with_no_answer_fails_after_a_second(stubs, stand_ins):
    stub, info = freed_port(stubs)
    stand_ins(stub.port, None)
    start = time.monotonic()
    with pytest.raises(NotOurServer):
        asyncio.run(open_connection(info))
    assert 0.9 <= time.monotonic() - start < 2.0


def test_open_connection_times_out_at_the_callers_deadline(stubs, stand_ins):
    stub, info = freed_port(stubs)
    stand_ins(stub.port, None)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(open_connection(info, timeout=0.3))
    assert time.monotonic() - start < 0.9
