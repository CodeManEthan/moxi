"""`serve` on real sockets in this process, the stop's one decision, and the runner's h11
protocol: what it hands to the app once the stop is decided."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import signal
import socket
import sys
import threading
import time

import pytest
from moxi_dev.testing import short_dir, wait_for

import asgi_kit
from moxi_ui.loopback import serve as serve_mod
from moxi_ui.loopback.check import proof, upstream
from moxi_ui.loopback.runtime import RuntimeInfo
from moxi_ui.loopback.serve import serve
from moxi_ui.loopback.server import app, guard, prove
from moxi_ui.loopback.transport import bind_browser, bind_tcp, bind_unix, call

posix = pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets")

MiB = 1024 * 1024
TOKEN = asgi_kit.TOKEN
SERVER_ID = asgi_kit.SERVER_ID


# --- a server on a loop of its own ----------------------------------------------------


class Served:
    """`serve` (or the runner's `_serve`) running on a loop in a thread of its own."""

    def __init__(self, asgi, sockets, *, before=None, inner=False, **kwargs):
        self.ready = threading.Event()
        self.error = None
        self.should_exit = None
        self.loop = None

        async def main():
            self.loop = asyncio.get_running_loop()
            self.should_exit = asyncio.Event()
            if before is not None:
                before(self.loop, self.should_exit)
            self.ready.set()
            run = serve_mod._serve if inner else serve
            await run(asgi, sockets, should_exit=self.should_exit, **kwargs)

        def thread():
            try:
                asyncio.run(main())
            except BaseException as err:  # noqa: BLE001 - reported by the test
                self.error = err
                self.ready.set()

        self.thread = threading.Thread(target=thread, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)

    def stop(self, timeout=10.0):
        if self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.should_exit.set)
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "serve did not return"
        if self.error is not None:
            raise self.error


def unix_listener(directory):
    path = os.path.join(directory, "page.sock")
    return bind_unix(path), path


def raw_connect(address):
    if isinstance(address, str):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(address)
    else:
        sock = socket.create_connection(address)
    sock.settimeout(5)
    return sock


def read_head(sock):
    """The status and lower-cased headers of one response, or None for a connection
    closed before any byte."""
    data = b""
    while b"\r\n\r\n" not in data:
        try:
            chunk = sock.recv(65536)
        except ConnectionResetError:
            chunk = b""
        if not chunk:
            return None if not data else (int(data.split()[1]), {})
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        headers[name.strip().lower().decode()] = value.strip().decode()
    # The body is read too, so what follows on the connection is the next response.
    length = int(headers.get("content-length", "0"))
    while len(rest) < length:
        chunk = sock.recv(65536)
        if not chunk:
            break
        rest += chunk
    return int(lines[0].split()[1]), headers


def closed(sock, timeout=5.0):
    """Whether the server closed `sock` with nothing more sent."""
    sock.settimeout(timeout)
    try:
        return sock.recv(1) == b""
    except ConnectionResetError:
        return True


# --- serve: the real chain on both transports ------------------------------------------


def page_chain(transport, port=None):
    inner = app(asgi_kit.make_page(), module="lbtest", server_id=SERVER_ID, version="9.9")
    profile = upstream(inner.table, token=TOKEN, port=port)
    return inner, guard(prove(inner, token=TOKEN, server_id=SERVER_ID), lambda: profile)


def info_for(transport, address, port=None):
    return RuntimeInfo(module="lbtest", server_id=SERVER_ID, pid=os.getpid(),
                       transport=transport, address=address, port=port, token=TOKEN,
                       version="9.9", started_at="2026-10-30T12:00:00Z")


@posix
def test_serve_answers_through_guard_and_prove_on_a_unix_socket(pages):
    with short_dir() as directory:
        sock, path = unix_listener(directory)
        _, asgi = page_chain("unix")
        served = Served(asgi, [sock])
        try:
            info = info_for("unix", path)
            assert call(info, "GET", "/api/summary?days=3") == (
                200, {"total": 3, "log": "/var/log/x"})
            status, health = call(info, "GET", "/api/health")
            assert status == 200 and health["server_id"] == SERVER_ID
            assert call(info, "POST", "/api/refresh", {"force": True}) == (
                200, {"ok": True, "module": "lbtest"})
        finally:
            served.stop()


def test_serve_answers_through_guard_and_prove_on_tcp(pages):
    sock = bind_tcp(0)
    port = sock.getsockname()[1]
    _, asgi = page_chain("tcp", port)
    served = Served(asgi, [sock])
    try:
        info = info_for("tcp", "127.0.0.1", port)
        assert call(info, "GET", "/api/summary") == (200, {"total": 0, "log": "/var/log/x"})
        # The upstream profile's Host gate runs on TCP: another port is refused.
        conn = raw_connect(("127.0.0.1", port))
        conn.sendall(b"GET /api/summary HTTP/1.1\r\nHost: 127.0.0.1:1\r\n"
                     b"X-Moxi-Token: " + TOKEN.encode() + b"\r\n\r\n")
        assert read_head(conn)[0] == 403
        conn.close()
    finally:
        served.stop()


@posix
def test_serve_awaits_drain_before_uvicorn_stops(pages):
    with short_dir() as directory:
        sock, path = unix_listener(directory)
        _, asgi = page_chain("unix")
        seen = []

        async def drain():
            # The server still answers while the drain runs.
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(b"GET /api/summary HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
                         b"X-Moxi-Token: " + TOKEN.encode() + b"\r\n\r\n")
            seen.append((await reader.readline()).split()[1])
            writer.close()

        served = Served(asgi, [sock], drain=drain)
        served.stop()
        assert seen == [b"200"]


@posix
def test_a_signal_sets_should_exit_and_serve_puts_the_old_handlers_back(pages):
    with short_dir() as directory:
        sock, _ = unix_listener(directory)
        _, asgi = page_chain("unix")
        before = signal.getsignal(signal.SIGTERM)

        async def main():
            should_exit = asyncio.Event()
            asyncio.get_running_loop().call_later(0.2, os.kill, os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(serve(asgi, [sock], should_exit=should_exit), 5)
            return should_exit.is_set()

        # The process is still here: serve neither lets the signal end it nor raises it
        # again when it returns.
        assert asyncio.run(main()) is True
        assert signal.getsignal(signal.SIGTERM) is before


# --- serve's head limit ---------------------------------------------------------------


def reaching_app(seen):
    async def asgi(scope, receive, send):
        seen.append(sum(len(k) + len(v) for k, v in scope["headers"]))
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})
    return asgi


def send_head(address, size):
    """Send a GET whose head is `size` bytes in 64 KiB writes; the status, or None."""
    start = b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Big: "
    end = b"\r\n\r\n"
    head = start + b"a" * (size - len(start) - len(end)) + end
    assert len(head) == size
    conn = raw_connect(address)
    try:
        for at in range(0, len(head), 64 * 1024):
            try:
                conn.sendall(head[at:at + 64 * 1024])
            except (BrokenPipeError, ConnectionResetError):
                break
        answer = read_head(conn)
        return None if answer is None else answer[0]
    finally:
        conn.close()


@posix
def test_a_raised_head_limit_lets_a_big_head_reach_the_app_and_refuses_a_bigger_one():
    with short_dir() as directory:
        sock, path = unix_listener(directory)
        seen = []
        served = Served(reaching_app(seen), [sock], head_limit=4 * MiB)
        try:
            assert send_head(path, 4 * MiB - 1024) == 200
            assert len(seen) == 1 and seen[0] > 4 * MiB - 2048
            assert send_head(path, 5 * MiB) == 400
            assert len(seen) == 1, "the 5 MiB head reached the app"
        finally:
            served.stop()


@posix
def test_the_default_head_limit_refuses_300000_bytes():
    with short_dir() as directory:
        sock, path = unix_listener(directory)
        seen = []
        served = Served(reaching_app(seen), [sock])
        try:
            assert send_head(path, 300_000) == 400
            assert seen == []
            assert send_head(path, 8_000) == 200
        finally:
            served.stop()


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "4M"])
def test_a_head_limit_that_is_not_a_number_of_bytes_is_refused(value):
    with pytest.raises(ValueError):
        asyncio.run(serve(reaching_app([]), [], should_exit=asyncio.Event(), head_limit=value))


def test_the_config_passes_the_head_limit_only_when_it_is_set(monkeypatch):
    import uvicorn

    configs = []

    class Recording(uvicorn.Config):
        def __init__(self, *args, **kwargs):
            configs.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(uvicorn, "Config", Recording)

    async def once(**kwargs):
        should_exit = asyncio.Event()
        should_exit.set()
        await serve(reaching_app([]), [], should_exit=should_exit, **kwargs)

    asyncio.run(once())
    asyncio.run(once(head_limit=4 * MiB))
    assert "h11_max_incomplete_event_size" not in configs[0]
    assert configs[1]["h11_max_incomplete_event_size"] == 4 * MiB
    from uvicorn.protocols.http.h11_impl import H11Protocol

    for config in configs:
        assert issubclass(config["http"], H11Protocol) and config["ws"] == "none"
        assert config["lifespan"] == "off" and config["proxy_headers"] is False
        assert config["server_header"] is False and config["date_header"] is False
        assert config["log_config"] is None and config["access_log"] is False
        assert config["limit_concurrency"] == 128
        assert config["timeout_graceful_shutdown"] == 2


# --- TCP_NODELAY on every accepted TCP connection --------------------------------------


def _has_ipv6_loopback():
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


ipv6 = pytest.mark.skipif(not _has_ipv6_loopback(), reason="this system has no IPv6 loopback")


def bound_v4():
    return bind_tcp(0)


def bound_v6():
    v4, v6 = bind_browser(0).sockets
    v4.close()
    return v6


def inherited(proto=None):
    """A listener bound outside loopback and passed in by descriptor, wrapped as the ui
    shell wraps systemd's and launchd's: `socket.socket(fileno=fd)`. Linux reads the
    protocol back from the descriptor; `proto=0` gives the socket a system that can't,
    as macOS may, hands back."""
    outside = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    outside.bind(("127.0.0.1", 0))
    outside.listen()
    fd = outside.detach()
    if proto is None:
        return socket.socket(fileno=fd)
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM, proto, fileno=fd)


def accepted_nodelay(served, server):
    """`TCP_NODELAY` on each connection the server holds, read on its own loop."""
    async def read():
        return [connection.transport.get_extra_info("socket").getsockopt(
                    socket.IPPROTO_TCP, socket.TCP_NODELAY)
                for connection in server.server_state.connections]
    return asyncio.run_coroutine_threadsafe(read(), served.loop).result(5)


def assert_nodelay(sock, **kwargs):
    address = sock.getsockname()[:2]
    servers = []
    served = Served(reaching_app([]), [sock], inner=True, on_server=servers.append, **kwargs)
    try:
        conn = raw_connect(address)
        try:
            conn.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            assert read_head(conn)[0] == 200
            flags = accepted_nodelay(served, servers[0])
            assert len(flags) == 1 and flags[0], "the accepted connection keeps Nagle on"
        finally:
            conn.close()
    finally:
        served.stop()


@pytest.mark.parametrize("listener", [
    pytest.param(bound_v4, id="bound-127.0.0.1"),
    pytest.param(bound_v6, id="bound-::1", marks=ipv6),
    pytest.param(inherited, id="inherited"),
    pytest.param(lambda: inherited(proto=0), id="inherited-protocol-0"),
])
def test_every_accepted_tcp_connection_has_nodelay_on(listener):
    assert_nodelay(listener())


def test_the_runners_protocol_turns_nodelay_on_too(pages):
    stop = serve_mod._Stop(logging.getLogger("test.stop"), end=lambda code: None,
                           exit_deadline=60)
    stop.activity = app(asgi_kit.make_page(), module="lbtest", server_id=SERVER_ID).activity
    assert_nodelay(inherited(proto=0), http=serve_mod._protocol_class(stop))


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="no Unix sockets")
def test_a_unix_connection_is_left_as_it_is():
    seen = []

    class Base:
        def connection_made(self, transport):
            seen.append("made")

    class Sock:
        family = socket.AF_UNIX

        def setsockopt(self, *args):
            seen.append(args)

    class Transport:
        def get_extra_info(self, name):
            return Sock() if name == "socket" else None

    serve_mod._no_delay(Base)().connection_made(Transport())
    assert seen == ["made"]


def test_an_http_name_other_than_h11_is_refused():
    with pytest.raises(ValueError, match="httptools"):
        asyncio.run(serve_mod._serve(reaching_app([]), [], should_exit=asyncio.Event(),
                                     http="httptools"))


# --- the one decision -----------------------------------------------------------------


def test_only_the_first_request_decides_and_a_second_leaves_the_clock_alone():
    ended = []
    stop = serve_mod._Stop(logging.getLogger("test.stop"), end=ended.append,
                           exit_deadline=0.4)
    assert not stop.decided()
    stop.request("first")
    first = stop.decided_at
    assert stop.decided() and stop.why == "first"
    time.sleep(0.05)
    threading.Thread(target=stop.request, args=("second",)).start()
    stop.request("third")
    assert stop.decided_at == first and stop.why == "first"
    wait_for(lambda: ended, 2, what="the watchdog")
    assert ended == [1]
    assert time.monotonic() - first < 0.4 + 0.2


def test_a_decision_before_the_loop_runs_is_taken_when_the_stop_attaches():
    stop = serve_mod._Stop(logging.getLogger("test.stop"), end=lambda code: None,
                           exit_deadline=60)
    stop.request("early")

    async def main():
        should_exit = asyncio.Event()
        stop.attach(asyncio.get_running_loop(), should_exit)
        await asyncio.wait_for(should_exit.wait(), 2)

    asyncio.run(main())


def test_the_watchdog_ends_the_process_even_while_the_loop_is_blocked():
    ended = []
    stop = serve_mod._Stop(logging.getLogger("test.stop"), end=ended.append,
                           exit_deadline=0.3)

    async def main():
        stop.attach(asyncio.get_running_loop(), asyncio.Event())
        threading.Timer(0.05, stop.request, args=("lifeline",)).start()
        time.sleep(0.6)  # Holds the loop, as a blocking async def handler would.

    asyncio.run(main())
    assert ended == [1]


# --- the runner's protocol --------------------------------------------------------------


class Proto:
    """The runner's chain behind its own protocol class, in this process."""

    def __init__(self, directory):
        self.sock, self.path = unix_listener(directory)
        self.stop = serve_mod._Stop(logging.getLogger("test.stop"), end=lambda code: None,
                                    exit_deadline=60)
        self.inner = app(asgi_kit.make_page(), module="lbtest", server_id=SERVER_ID)
        self.handed = []
        real = self.inner.activity.hand_off

        def hand_off(scope):
            # The protocol counts in its own callback, before the request's task runs.
            self.handed.append((scope["raw_path"], asyncio.current_task(),
                                self.inner.activity.pending))
            real(scope)

        self.inner.activity.hand_off = hand_off
        profile = upstream(self.inner.table, token=TOKEN)
        asgi = guard(prove(self.inner, token=TOKEN, server_id=SERVER_ID,
                           stopping=self.stop.flag.is_set), lambda: profile)

        def before(loop, should_exit):
            self.stop.activity = self.inner.activity
            self.stop.attach(loop, should_exit)

        self.served = Served(asgi, [self.sock], before=before, inner=True,
                             drain=self.stop.drain, http=serve_mod._protocol_class(self.stop),
                             on_server=lambda server: setattr(self.stop, "server", server))

    def connect(self):
        return raw_connect(self.path)

    def prove(self, conn):
        nonce = secrets.token_urlsafe(32)
        conn.sendall(b"GET /api/prove HTTP/1.1\r\nHost: localhost\r\nX-Moxi-Nonce: "
                     + nonce.encode() + b"\r\n\r\n")
        status, headers = read_head(conn)
        assert status == 204
        assert headers["x-moxi-proof"] == proof(TOKEN, SERVER_ID, nonce)

    @staticmethod
    def get(conn, path="/api/summary"):
        conn.sendall(b"GET " + path.encode() + b" HTTP/1.1\r\nHost: localhost\r\n"
                     b"X-Moxi-Token: " + TOKEN.encode() + b"\r\n\r\n")


@posix
def test_the_protocol_counts_each_request_in_the_step_that_hands_it_off(pages):
    with short_dir() as directory:
        proto = Proto(directory)
        try:
            conn = proto.connect()
            proto.prove(conn)
            Proto.get(conn)
            status, _ = read_head(conn)
            assert status == 200
            assert [raw for raw, _, _ in proto.handed] == [b"/api/prove", b"/api/summary"]
            assert all(task is None for _, task, _ in proto.handed)
            wait_for(lambda: proto.inner.activity.pending == 0, 2, what="the hand-offs settled")
            wait_for(lambda: proto.inner.activity.quiet() is not None, 2,
                     what="the quiet run restarted")
            conn.close()
        finally:
            proto.stop.request("the test ended")
            proto.served.thread.join(5)


@posix
def test_after_the_decision_only_the_promised_request_is_handed_off(pages):
    with short_dir() as directory:
        proto = Proto(directory)
        promised = proto.connect()
        proto.prove(promised)
        idle = proto.connect()
        Proto.get(idle)
        assert read_head(idle)[0] == 200
        handed = len(proto.handed)

        proto.stop.request("a test")
        # A kept-alive connection with nothing in the app is closed at once, unanswered.
        assert closed(idle, 1)
        # The promised request is served, and closes its connection.
        Proto.get(promised)
        status, headers = read_head(promised)
        assert status == 200 and headers["connection"] == "close"
        assert closed(promised, 1)
        assert [raw for raw, _, _ in proto.handed[handed:]] == [b"/api/summary"]
        # The listener is closed: a new connect is refused.
        with pytest.raises(OSError):
            proto.connect().sendall(b"GET / HTTP/1.1\r\n\r\n")
        proto.served.thread.join(5)
        assert not proto.served.thread.is_alive()
        assert proto.served.error is None


@posix
def test_after_the_decision_a_proof_is_answered_503_with_connection_close(pages):
    with short_dir() as directory:
        proto = Proto(directory)
        conn = proto.connect()
        proto.prove(conn)
        proto.stop.request("a test")
        wait_for(lambda: proto.stop._decided, 2, what="the decision on the loop")
        conn.sendall(b"GET /api/prove HTTP/1.1\r\nHost: localhost\r\nX-Moxi-Nonce: "
                     + secrets.token_urlsafe(32).encode() + b"\r\n\r\n")
        status, headers = read_head(conn)
        assert status == 503 and headers["connection"] == "close"
        assert "x-moxi-proof" not in headers
        assert closed(conn, 1)
        proto.served.thread.join(5)
        assert not proto.served.thread.is_alive()


# --- serve's signals=False: a second listener in a process that has one --------------------


@posix
def test_serve_with_signals_off_installs_no_handler_and_puts_none_back():
    seen = []

    def mine(signum, frame):
        seen.append(signum)

    before = signal.signal(signal.SIGTERM, mine)
    try:
        with short_dir() as directory:
            sock, _ = unix_listener(directory)

            async def main():
                should_exit = asyncio.Event()
                serving = asyncio.create_task(
                    serve(reaching_app([]), [sock], should_exit=should_exit, signals=False))
                await asyncio.sleep(0.2)
                during = signal.getsignal(signal.SIGTERM)
                should_exit.set()
                await asyncio.wait_for(serving, 5)
                return during

            assert asyncio.run(main()) is mine
            assert signal.getsignal(signal.SIGTERM) is mine
            assert seen == []
    finally:
        signal.signal(signal.SIGTERM, before)


@posix
def test_a_second_serve_with_signals_off_leaves_the_first_ones_signal_handling():
    with short_dir() as directory:
        first, _ = unix_listener(directory)
        second = bind_unix(os.path.join(directory, "gateway.sock"))
        before = signal.getsignal(signal.SIGTERM)

        async def main():
            first_exit, second_exit = asyncio.Event(), asyncio.Event()
            one = asyncio.create_task(serve(reaching_app([]), [first], should_exit=first_exit))
            await asyncio.sleep(0.2)
            installed = signal.getsignal(signal.SIGTERM)
            two = asyncio.create_task(serve(reaching_app([]), [second],
                                            should_exit=second_exit, signals=False))
            await asyncio.sleep(0.2)
            assert signal.getsignal(signal.SIGTERM) is installed
            # The signal reaches the first serve's handler, not the second's.
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(first_exit.wait(), 5)
            assert not second_exit.is_set()
            await asyncio.wait_for(one, 5)
            second_exit.set()
            await asyncio.wait_for(two, 5)
            # Closing the second listener changed nothing either.
            return installed

        installed = asyncio.run(main())
        assert installed is not before
        assert signal.getsignal(signal.SIGTERM) is before


@pytest.mark.parametrize(("signals", "peer"), [(None, False), (1, False), (True, "yes")])
def test_signals_and_peer_take_only_true_or_false(signals, peer):
    async def main():
        await serve(reaching_app([]), [], should_exit=asyncio.Event(), signals=signals,
                    peer=peer)

    with pytest.raises(ValueError, match="signals and peer"):
        asyncio.run(main())


# --- serve's peer=True: each connection's peer in its requests' scope -----------------------


def peer_app(states=None):
    """Answers each request with its scope's peer, or `"absent"` when it has none."""
    import json

    async def asgi(scope, receive, send):
        if states is not None:
            states.append(dict(scope.get("state") or {}))
        body = json.dumps(scope.get("moxi.peer", "absent")).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
    return asgi


def ask(sock, extra=b""):
    """One keep-alive GET on `sock`; the JSON body of its answer."""
    import json

    sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n" + extra + b"\r\n")
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        assert chunk, "the server closed the connection"
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    length = int(next(line.split(b":", 1)[1] for line in head.split(b"\r\n")
                      if line.lower().startswith(b"content-length:")))
    while len(body) < length:
        body += sock.recv(65536)
    return json.loads(body)


def counted_reads(monkeypatch, answers=None):
    """Replace the peer read with one that counts its calls, and answers from `answers`
    in turn when given (the real read otherwise)."""
    real = serve_mod._peer_uid
    reads = []

    def read(sock):
        reads.append(sock)
        return real(sock) if answers is None else answers[len(reads) - 1]

    monkeypatch.setattr(serve_mod, "_peer_uid", read)
    return reads


@pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason="a peer uid on Unix sockets")
def test_peer_gives_every_request_on_a_unix_connection_this_processs_uid(monkeypatch):
    reads = counted_reads(monkeypatch)
    with short_dir() as directory:
        sock, path = unix_listener(directory)
        served = Served(peer_app(), [sock], peer=True)
        try:
            conn = raw_connect(path)
            try:
                assert ask(conn) == {"uid": os.getuid()}
                # A header of the same name is a header, never the scope key.
                assert ask(conn, b"Moxi-Peer: {\"uid\": 0}\r\n") == {"uid": os.getuid()}
                assert ask(conn) == {"uid": os.getuid()}
            finally:
                conn.close()
        finally:
            served.stop()
    assert len(reads) == 1, "the peer is read once, when the connection is made"


# --- platform's uname, without a WMI query ----------------------------------------------


def wmi_recorder(monkeypatch):
    """`platform` with its cached uname cleared and its WMI query replaced by one that
    records each call and fails as a query that timed out does."""
    import platform

    asked = []

    def query(*keys):
        asked.append(keys)
        raise OSError("timed out")

    monkeypatch.setattr(platform, "_uname_cache", None)
    monkeypatch.setattr(platform, "_wmi_query", query, raising=False)
    return asked, query


def test_the_uname_is_filled_without_a_wmi_query_and_the_query_is_put_back(monkeypatch):
    import platform

    asked, query = wmi_recorder(monkeypatch)
    serve_mod._uname_without_wmi()
    assert asked == [] and platform._wmi_query is query
    assert platform._uname_cache is not None and platform.system()


@pytest.mark.skipif(sys.platform != "win32", reason="platform makes a WMI query on Windows only")
def test_on_windows_platform_still_asks_wmi_through_the_name_the_helper_replaces(monkeypatch):
    """The two tests beside this one pass when no query is recorded, and they would pass
    as well if `platform` stopped asking through `_wmi_query` but still made the query:
    `_uname_without_wmi` would then refuse nothing, and the query thread it exists to
    keep from starting would start. Here a plain `uname()` must ask through that name."""
    import platform

    asked, _ = wmi_recorder(monkeypatch)
    platform.uname()
    assert asked, "platform.uname() made no query through platform._wmi_query"


def test_serve_starts_on_a_thread_without_a_wmi_query(monkeypatch):
    asked, _ = wmi_recorder(monkeypatch)
    sock = bind_tcp(0)
    served = Served(peer_app(), [sock])
    try:
        conn = raw_connect(sock.getsockname())
        try:
            assert ask(conn) == "absent"
        finally:
            conn.close()
    finally:
        served.stop()
    assert asked == [], "uvicorn's startup asked platform for a WMI query"


def test_peer_gives_none_on_tcp():
    sock = bind_tcp(0)
    served = Served(peer_app(), [sock], peer=True)
    try:
        conn = raw_connect(sock.getsockname())
        try:
            assert ask(conn) == {"uid": None}
        finally:
            conn.close()
    finally:
        served.stop()


@pytest.mark.parametrize("transport", [
    pytest.param("unix", marks=posix),
    "tcp",
])
def test_without_peer_the_scope_has_no_peer_key(transport, monkeypatch):
    reads = counted_reads(monkeypatch)
    with short_dir() as directory:
        if transport == "unix":
            sock, address = unix_listener(directory)
        else:
            sock = bind_tcp(0)
            address = sock.getsockname()
        served = Served(peer_app(), [sock])
        try:
            conn = raw_connect(address)
            try:
                assert ask(conn) == "absent"
            finally:
                conn.close()
        finally:
            served.stop()
    assert reads == []


@posix
def test_two_connections_at_once_each_see_only_their_own_peer(monkeypatch):
    # This process can't connect as two users; the read is stubbed to give the first
    # connection one uid and the second another, and the requests interleave.
    counted_reads(monkeypatch, answers=[1001, 1002])
    servers = []
    states = []
    with short_dir() as directory:
        sock, path = unix_listener(directory)
        served = Served(peer_app(states), [sock], inner=True, peer=True,
                        on_server=servers.append)
        try:
            one = raw_connect(path)
            assert ask(one) == {"uid": 1001}
            two = raw_connect(path)
            try:
                assert ask(two) == {"uid": 1002}
                assert ask(one) == {"uid": 1001}
                assert ask(two) == {"uid": 1002}
                assert ask(one) == {"uid": 1001}
            finally:
                one.close()
                two.close()
            shared = servers[0].lifespan.state
            assert "moxi.peer" not in shared
        finally:
            served.stop()
    assert "moxi.peer" not in servers[0].lifespan.state
    # The peer is lifted out of each request's state, so the app reads it in one place.
    assert len(states) == 5 and all("moxi.peer" not in state for state in states)


def test_the_peer_read_gives_none_for_tcp_no_socket_and_a_failed_read():
    assert serve_mod._peer_uid(None) is None
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        assert serve_mod._peer_uid(tcp) is None
    finally:
        tcp.close()

    if hasattr(socket, "AF_UNIX"):
        class Failing:
            family = socket.AF_UNIX

            def getsockopt(self, *args):
                raise OSError("no peer")

        assert serve_mod._peer_uid(Failing()) is None


@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED")
def test_the_peer_read_is_the_connected_processs_uid_on_linux():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert serve_mod._peer_uid(left) == os.getuid()
    finally:
        left.close()
        right.close()
