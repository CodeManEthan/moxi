"""moxi.ipc's blocking client: connect, calls, hello, notifications, NotRpc, and its safety
across threads, against the real server and against a fake one on a socket pair."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import threading
import time

import pytest

import moxi.ipc as ipc
from moxi.ipc import _client

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="Unix sockets; the Windows pipe client is built apart")


async def handler(conn, method, params):
    if method == "fail":
        raise ipc.RpcError(1002, "refused", ["why"])
    if method == "sleep":
        await asyncio.sleep(params[0])
        return "slept"
    if method == "push":
        for i in range(params[0]):
            conn.notify("pushed", [i])
        return "pushed"
    if method == "bye":
        conn.close()
        return None
    return params


def bounded(fn, timeout: float = 20.0):
    """Run `fn` on a thread and return what it returns, failing past `timeout`."""
    out: dict = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            out["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), f"no result in {timeout} s"
    if "error" in out:
        raise out["error"]
    return out["value"]


class Fake:
    """A client on one end of a socket pair, and the test as its server on the other."""

    def __init__(self, **options):
        self.server, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.settimeout(10)
        self.client = _client.Client(theirs, "pair", **options)
        self.buffer = b""

    def request(self) -> dict:
        while b"\n" not in self.buffer:
            chunk = self.server.recv(65536)
            assert chunk, "the client closed"
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        return json.loads(line)

    def send(self, data: bytes) -> None:
        self.server.sendall(data)

    def answer(self, id, result) -> None:
        self.send(json.dumps({"jsonrpc": "2.0", "id": id, "result": result}).encode()
                  + b"\n")

    def close(self) -> None:
        self.client.close()
        self.server.close()


@pytest.fixture
def fake():
    made = []

    def make(**options):
        made.append(Fake(**options))
        return made[-1]

    yield make
    for f in made:
        f.close()


# -- connect --------------------------------------------------------------------------------

def test_nothing_at_the_address_is_not_serving(sock_dir):
    with pytest.raises(ipc.NotServing) as caught:
        ipc.connect(str(sock_dir / "none.sock"))
    assert caught.value.exit_code == 75
    assert caught.value.address == str(sock_dir / "none.sock")


def test_a_socket_nobody_listens_on_is_not_serving(sock_dir):
    address = str(sock_dir / "left.sock")
    left = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    left.bind(address)
    left.close()
    with pytest.raises(ipc.NotServing):
        ipc.connect(address)


def test_a_closed_server_is_not_serving(serving):
    srv = serving(handler)
    address = srv.address
    srv.close()
    with pytest.raises(ipc.NotServing):
        ipc.connect(address)


def full_listener(address: str) -> tuple[socket.socket, list[socket.socket]]:
    """A listener that never accepts, with its backlog filled by connects already made."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(address)
    listener.listen(1)
    waiting = []
    for _ in range(64):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            sock.connect(address)
        except BlockingIOError:
            sock.close()
            return listener, waiting
        waiting.append(sock)
    raise AssertionError("the backlog never filled")


linux_backlog = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="a full Unix socket backlog answers EAGAIN on Linux; macOS is checked on a Mac")


@linux_backlog
def test_a_full_backlog_is_busy_until_the_timeout(sock_dir):
    listener, waiting = full_listener(str(sock_dir / "full.sock"))
    try:
        start = time.monotonic()
        with pytest.raises(ipc.Timeout) as caught:
            ipc.connect(listener.getsockname(), timeout=0.3)
        assert 0.3 <= time.monotonic() - start < 3
        assert "busy" in str(caught.value)
    finally:
        for sock in [*waiting, listener]:
            sock.close()


@linux_backlog
def test_a_busy_server_is_tried_again_until_it_accepts(sock_dir):
    listener, waiting = full_listener(str(sock_dir / "full.sock"))
    accepted = []

    def accept_later():
        time.sleep(0.3)
        for _ in range(len(waiting) + 1):
            accepted.append(listener.accept()[0])

    thread = threading.Thread(target=accept_later, daemon=True)
    thread.start()
    try:
        client = bounded(lambda: ipc.connect(listener.getsockname(), timeout=5.0))
        client.close()
        thread.join(10)
    finally:
        for sock in [*waiting, *accepted, listener]:
            sock.close()


def test_connect_passes_its_line_cap_to_the_client(serving):
    srv = serving(handler)
    with ipc.connect(srv.address, max_line=100) as client:
        assert client.call("echo", ["x" * 40], timeout=10) == ["x" * 40]
        with pytest.raises(ipc.IpcError, match="longer than 100 bytes"):
            client.call("echo", ["x" * 120], timeout=10)
    with ipc.connect(srv.address) as client:
        assert client.call("echo", ["x" * 120], timeout=10) == ["x" * 120]
    with pytest.raises(ValueError):
        ipc.connect(srv.address, max_line=0)


# -- calls ----------------------------------------------------------------------------------

def test_call_returns_the_result_and_raises_the_error(serving):
    srv = serving(handler)
    with ipc.connect(srv.address) as client:
        assert client.call("echo", {"a": [1, 2]}) == {"a": [1, 2]}
        assert client.call("echo", (1, "x")) == [1, "x"]
        with pytest.raises(ipc.RpcError) as caught:
            client.call("fail")
        assert (caught.value.code, caught.value.message, caught.value.data) == (
            1002, "refused", ["why"])
        assert client.call("echo", [3]) == [3]


def test_notify_sends_and_gets_nothing_back(serving):
    seen = []

    async def recording(conn, method, params):
        seen.append((method, params))
        return "ignored"

    srv = serving(recording)
    with ipc.connect(srv.address) as client:
        client.notify("tick", {"n": 1})
        assert client.call("go") == "ignored"
    assert seen == [("tick", {"n": 1}), ("go", None)]


def test_a_call_past_its_timeout_raises_and_its_late_answer_is_dropped(serving):
    srv = serving(handler)
    with ipc.connect(srv.address) as client:
        start = time.monotonic()
        with pytest.raises(ipc.Timeout) as caught:
            client.call("sleep", [0.5], timeout=0.1)
        assert time.monotonic() - start < 0.5
        assert isinstance(caught.value, TimeoutError)
        # The server answers the slow call first, and the client drops that answer.
        assert client.call("echo", ["next"], timeout=10) == ["next"]


#: Far more than a socket pair buffers, so a write of it blocks while nobody reads.
BIG = "x" * (16 << 20)


def test_a_call_whose_request_cannot_be_written_times_out_and_ends_the_connection(fake):
    f = fake()
    start = time.monotonic()
    with pytest.raises(ipc.Timeout):
        bounded(lambda: f.client.call("echo", [BIG], timeout=0.3))
    assert 0.3 <= time.monotonic() - start < 3
    # Part of the request was written, so the connection can't carry another frame.
    with pytest.raises(ipc.IpcError) as caught:
        bounded(lambda: f.client.call("echo", [1], timeout=1))
    assert not isinstance(caught.value, ipc.Timeout)


def test_a_call_waiting_on_another_calls_blocked_write_times_out(fake):
    f = fake()
    stuck = threading.Thread(target=lambda: pytest.raises(ipc.IpcError, f.client.call,
                                                          "echo", [BIG]), daemon=True)
    stuck.start()
    time.sleep(0.2)  # the untimed call holds the write lock, blocked in its write
    start = time.monotonic()
    with pytest.raises(ipc.Timeout):
        bounded(lambda: f.client.call("echo", [1], timeout=0.3))
    assert 0.3 <= time.monotonic() - start < 3
    f.client.close()
    stuck.join(10)
    assert not stuck.is_alive()


def test_a_timed_call_writes_a_large_request_the_server_reads(fake):
    f = fake()
    payload = "y" * (4 << 20)
    got = {}
    reader = threading.Thread(target=lambda: got.update(f.request()), daemon=True)
    reader.start()
    answer = threading.Thread(target=lambda: (reader.join(20), f.answer(got["id"], "ok")),
                              daemon=True)
    answer.start()
    assert bounded(lambda: f.client.call("echo", [payload], timeout=20)) == "ok"
    assert got["params"] == [payload]


def test_hello_agrees_a_version(serving):
    srv = serving(handler, hello=ipc.Hello((1, 3), {"watch", "steer"}))
    with ipc.connect(srv.address) as client:
        agreed = client.hello((2, 5), ["watch", "other"])
        assert agreed == ipc.Agreed(3, {"watch", "steer"}) and client.agreed == agreed
        assert client.call("echo", [1]) == [1]


def test_hello_tells_a_client_that_offers_nothing_the_servers_capabilities(serving):
    seen = []

    async def keep(conn, method, params):
        seen.append(conn.agreed)
        return None

    srv = serving(keep, hello=ipc.Hello((2, 2), {"deliver", "steer"}))
    with ipc.connect(srv.address) as client:
        assert client.hello((2, 2)) == ipc.Agreed(2, {"deliver", "steer"})
        client.call("go")
    with ipc.connect(srv.address) as client:
        client.hello((1, 2), ["guards"])
        client.call("go")
    assert seen == [ipc.Agreed(2), ipc.Agreed(2, {"guards"})]


def test_hello_refused_raises_rpc_error(serving):
    srv = serving(handler, hello=ipc.Hello((4, 5)))
    with ipc.connect(srv.address) as client:
        with pytest.raises(ipc.RpcError) as caught:
            client.hello((1, 2))
        assert caught.value.code == -32001
        assert caught.value.data == {"client": [1, 2], "server": [4, 5]}


def test_notifications_and_answers_share_the_connection(serving):
    srv = serving(handler)
    with ipc.connect(srv.address) as client:
        assert client.call("push", [5]) == "pushed"
        notes = client.notifications()
        got = bounded(lambda: [next(notes) for _ in range(5)])
        assert got == [("pushed", [i]) for i in range(5)]


def test_notifications_end_when_the_server_closes(serving):
    srv = serving(handler)
    client = ipc.connect(srv.address)
    try:
        client.call("push", [2])
        client.notify("bye")
        assert bounded(lambda: list(client.notifications())) == [
            ("pushed", [0]), ("pushed", [1])]
        with pytest.raises(ipc.IpcError, match="closed the connection"):
            client.call("echo")
    finally:
        client.close()


def test_a_waiting_call_fails_when_the_server_goes(serving):
    srv = serving(handler)
    client = ipc.connect(srv.address)
    try:
        def slow():
            return client.call("sleep", [30])

        started = time.monotonic()
        threading.Timer(0.1, srv.call, (srv.server.close,)).start()
        with pytest.raises(ipc.IpcError) as caught:
            bounded(slow)
        assert not isinstance(caught.value, ipc.Timeout)
        assert time.monotonic() - started < 15
    finally:
        client.close()


def test_close_wakes_a_waiting_call_and_ends_notifications(fake):
    f = fake()
    errors = []

    def wait():
        try:
            f.client.call("never")
        except ipc.IpcError as exc:
            errors.append(exc)

    thread = threading.Thread(target=wait)
    thread.start()
    assert f.request()["method"] == "never"
    f.client.close()
    thread.join(10)
    assert not thread.is_alive() and "is closed" in str(errors[0])
    assert bounded(lambda: list(f.client.notifications())) == []
    with pytest.raises(ipc.IpcError):
        f.client.call("again")


# -- the fake server: lines the client must sort ----------------------------------------------

def test_answers_out_of_order_reach_their_calls(fake):
    f = fake()
    out = {}

    def call(name):
        out[name] = f.client.call(name, timeout=10)

    threads = [threading.Thread(target=call, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    asked = {}
    for _ in range(2):
        message = f.request()
        asked[message["method"]] = message["id"]
    f.send(b"\n")
    f.send(b'{"jsonrpc":"2.0","method":"between","params":[1]}\n')
    f.answer(asked["b"], "for b")
    f.answer(asked["a"], "for a")
    for t in threads:
        t.join(10)
    assert out == {"a": "for a", "b": "for b"}
    assert next(f.client.notifications()) == ("between", [1])


def test_an_answer_that_is_not_json_rpc_raises_not_rpc(fake):
    f = fake()
    errors = []

    def call():
        try:
            f.client.call("register")
        except ipc.IpcError as exc:
            errors.append(exc)

    thread = threading.Thread(target=call)
    thread.start()
    f.request()
    old = b'{"ok": true, "op": "registered", "pad": "' + b"x" * 300 + b'"}'
    f.send(old + b"\n")
    thread.join(10)
    assert isinstance(errors[0], ipc.NotRpc) and not isinstance(errors[0], ipc.RpcError)
    assert errors[0].line == old[:200]
    with pytest.raises(ipc.NotRpc):
        f.client.call("again")


def test_each_call_after_a_failure_raises_a_fresh_exception(fake):
    import traceback

    f = fake()
    f.server.close()
    f.client._reader.join(10)
    raised, depths = [], []
    for _ in range(4):
        for fn in (lambda: f.client.call("again", timeout=1), lambda: f.client.notify("n")):
            with pytest.raises(ipc.IpcError) as caught:
                fn()
            raised.append(caught.value)
            depths.append(len(traceback.extract_tb(caught.value.__traceback__)))
    # the traceback of each is as deep as the first: none grows from call to call
    assert depths[::2] == depths[:1] * 4 and depths[1::2] == depths[1:2] * 4
    assert len({id(exc) for exc in raised}) == len(raised)
    assert len({str(exc) for exc in raised}) == 1


def test_a_fresh_not_rpc_keeps_the_line(fake):
    f = fake()
    thread = threading.Thread(target=lambda: pytest.raises(ipc.NotRpc, f.client.call, "x"))
    thread.start()
    f.request()
    f.send(b'{"ok": true}\n')
    thread.join(10)
    first = pytest.raises(ipc.NotRpc, f.client.call, "again").value
    second = pytest.raises(ipc.NotRpc, f.client.call, "again").value
    assert first is not second
    assert first.line == second.line == b'{"ok": true}'
    assert first.reason == second.reason


def test_hello_raises_not_rpc_from_an_old_daemon(fake):
    f = fake()
    errors = []

    def hello():
        try:
            f.client.hello((1, 1))
        except ipc.NotRpc as exc:
            errors.append(exc)

    thread = threading.Thread(target=hello)
    thread.start()
    assert f.request()["method"] == "rpc.hello"
    f.send(b"unknown op\n")
    thread.join(10)
    assert errors and errors[0].line == b"unknown op"


def test_an_error_with_no_id_fails_every_waiting_call(fake):
    f = fake()
    errors = []

    def call():
        try:
            f.client.call("x", timeout=10)
        except ipc.RpcError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=call) for _ in range(3)]
    for t in threads:
        t.start()
    for _ in range(3):
        f.request()
    f.send(b'{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"line longer '
           b'than 4194304 bytes"}}\n')
    for t in threads:
        t.join(10)
    assert errors == [-32600] * 3


def test_a_line_over_the_clients_cap_ends_the_connection(fake):
    f = fake(max_line=100)
    errors = []

    def call():
        try:
            f.client.call("x", timeout=10)
        except ipc.IpcError as exc:
            errors.append(exc)

    thread = threading.Thread(target=call)
    thread.start()
    f.request()
    f.send(b'{"jsonrpc":"2.0","method":"first"}\n' + b"x" * 101)
    thread.join(10)
    assert "line longer than 100 bytes" in str(errors[0])
    assert bounded(lambda: list(f.client.notifications())) == [("first", None)]


# -- threads ---------------------------------------------------------------------------------

def test_one_client_shared_by_many_threads(serving):
    srv = serving(handler, queue=4096)
    with ipc.connect(srv.address) as client:
        results: dict[int, list] = {}
        errors = []

        def work(k):
            try:
                results[k] = [client.call("echo", [k, i], timeout=20) for i in range(50)]
                client.notify("tick", [k])
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(k,)) for k in range(8)]
        for t in threads:
            t.start()
        # Pushed notifications interleave with the threads' answers on the one connection.
        assert client.call("push", [100], timeout=20) == "pushed"
        for t in threads:
            t.join(30)
        assert not errors and not any(t.is_alive() for t in threads)
        assert results == {k: [[k, i] for i in range(50)] for k in range(8)}
        notes = client.notifications()
        assert bounded(lambda: [next(notes) for _ in range(100)]) == [
            ("pushed", [i]) for i in range(100)]


def test_the_reader_is_one_daemon_thread_that_ends_with_the_client(serving):
    srv = serving(handler)
    before = {t.ident for t in threading.enumerate()}
    client = ipc.connect(srv.address)
    readers = [t for t in threading.enumerate() if t.ident not in before]
    assert [t.name for t in readers] == ["moxi.ipc reader"] and readers[0].daemon
    client.close()
    readers[0].join(10)
    assert not readers[0].is_alive()


def test_connect_timeout_bounds_the_wait_for_the_hello(fake):
    f = fake(timeout=0.2)
    start = time.monotonic()
    with pytest.raises(ipc.Timeout):
        f.client.hello((1, 1))
    assert time.monotonic() - start < 5


def test_importing_sweep_and_the_client_loads_no_asyncio():
    code = ("import sys, moxi.ipc\nmoxi.ipc.sweep; moxi.ipc.connect\n"
            "print(' '.join(m for m in ('asyncio', 'moxi.ipc._server', 'psutil')"
            " if m in sys.modules))\n")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                          text=True, check=True, timeout=60)
    assert done.stdout.strip() == ""
