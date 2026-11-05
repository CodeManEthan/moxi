"""moxi.ipc's blocking client: connect, calls, hello, notifications, NotRpc, and its safety
across threads, against the real server and against a fake one on a socket pair (a pipe
pair on Windows).

The tests run on Unix sockets on Linux and macOS and on named pipes on Windows; the few
that only one of them can run say so. The Windows connect logic (which open errors mean
cold and which busy) also runs anywhere with the pipe open patched."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid

import pytest
from conftest import ServerThread

import moxi.ipc as ipc
from moxi.ipc import _client, _pipe, _users

WINDOWS = sys.platform == "win32"
posix = pytest.mark.skipif(WINDOWS, reason="Unix sockets")
windows = pytest.mark.skipif(not WINDOWS, reason="Windows named pipes")
#: How early a wait may seem to end by the clock: Windows' monotonic clock moves in
#: steps of about 15.6 ms.
EARLY = 0.032 if WINDOWS else 0.0


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
    """A client on one end of a socket pair (a pipe pair on Windows), and the test as its
    server on the other."""

    def __init__(self, **options):
        if WINDOWS:
            self.server, theirs = pipe_pair()
        else:
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


class PipeEnd:
    """The server's end of a pipe pair, read and written like a socket with a timeout."""

    def __init__(self, handle: int):
        self.pipe = _pipe.Pipe(handle)
        self.timeout = 10.0

    def settimeout(self, seconds: float) -> None:
        self.timeout = seconds

    def recv(self, size: int) -> bytes:
        """The next bytes; b"" at the end, or once `timeout` passes with none."""
        timer = threading.Timer(self.timeout, self.pipe.close)
        timer.start()
        try:
            return self.pipe.recv(size)
        finally:
            timer.cancel()

    def sendall(self, data: bytes) -> None:
        sent = self.pipe.send_by(data, time.monotonic() + self.timeout)
        assert sent == len(data), f"wrote {sent} of {len(data)} bytes in time"

    def close(self) -> None:
        self.pipe.close()


#: The size of each buffer of a test pipe pair's pipe.
PAIR_BUFFER = 64 * 1024


def pipe_instance(name: str, *, first: bool = True, most: int = 1) -> int:
    """A new overlapped server instance of byte pipe `name`, of at most `most` instances,
    not yet connected."""
    import _winapi

    return _winapi.CreateNamedPipe(
        name, _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
        | (_winapi.FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0),
        _winapi.PIPE_WAIT,  # PIPE_TYPE_BYTE and PIPE_READMODE_BYTE are 0
        most, PAIR_BUFFER, PAIR_BUFFER, 0, _winapi.NULL)


def accept(server: int, open_client):
    """Connect server instance `server` to the client `open_client()` opens, and return
    the client."""
    import _winapi

    ov = _winapi.ConnectNamedPipe(server, overlapped=True)
    client = open_client()
    ov.GetOverlappedResult(True)
    return client


def pipe_name() -> str:
    return rf"\\.\pipe\moxi-test-pair-{uuid.uuid4().hex}"


def pipe_pair() -> tuple[PipeEnd, _pipe.Pipe]:
    """The two ends of a new pipe of one instance, both overlapped: the server's and the
    client's."""
    name = pipe_name()
    server = pipe_instance(name)
    return PipeEnd(server), accept(server, lambda: _pipe.open_pipe(name))


@pytest.fixture
def fake():
    made = []

    def make(**options):
        made.append(Fake(**options))
        return made[-1]

    yield make
    for f in made:
        f.close()


@pytest.fixture
def serving(request, tmp_path, monkeypatch):
    """`serving(handler, **options)` starts a server on a loop thread and closes it after
    the test: at `<sock_dir>/s/a.sock`, or on Windows at a fresh pipe of the demo module,
    since pipe names are machine-wide."""
    if WINDOWS:
        monkeypatch.setenv("MOXI_DEMO_RUNTIME_DIR", str(tmp_path / "rt"))
        where = ipc.address("demo", f"t{uuid.uuid4().hex[:12]}")
    else:
        where = request.getfixturevalue("sock_dir") / "s" / "a.sock"
    started = []

    def start(handler, *, address=None, **options):
        thread = ServerThread(address or where, handler, **options)
        started.append(thread)
        return thread

    yield start
    for thread in reversed(started):
        thread.close()


# -- connect --------------------------------------------------------------------------------

@posix
def test_nothing_at_the_address_is_not_serving(sock_dir):
    with pytest.raises(ipc.NotServing) as caught:
        ipc.connect(str(sock_dir / "none.sock"))
    assert caught.value.exit_code == 75
    assert caught.value.address == str(sock_dir / "none.sock")


@posix
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
        assert 0.3 - EARLY <= time.monotonic() - start < 3
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


# -- connect on Windows: what each open error means (any system) ----------------------------

def win_error(code: int) -> OSError:
    """An `OSError` as `CreateFile` raises it, with Windows error `code`."""
    if WINDOWS:
        return OSError(None, f"error {code}", None, code)
    exc = OSError(None, f"error {code}")
    exc.winerror = code
    return exc


@pytest.fixture
def opens(monkeypatch):
    """`connect` taken down its Windows path on any system, with each pipe open answered
    by the next of `opens.answers`: an exception raised, else the connection returned.
    `opens.tried` lists the addresses opened."""
    import types

    def open_pipe(address):
        opens.tried.append(address)
        answer = opens.answers.pop(0) if opens.answers else win_error(231)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    opens = types.SimpleNamespace(answers=[], tried=[])
    monkeypatch.setattr(_client, "_WINDOWS", True)
    monkeypatch.setattr(_pipe, "open_pipe", open_pipe)
    return opens


class Opened:
    """A stand-in for an opened pipe: nothing comes from it until it is closed. Its
    server is `pid` (this process unless given), or `server_pid()` raises `pid` when it
    is an exception."""

    def __init__(self, pid: int | BaseException | None = None):
        self.gone = threading.Event()
        self.pid = os.getpid() if pid is None else pid
        self.asked = 0

    def server_pid(self) -> int:
        self.asked += 1
        if isinstance(self.pid, BaseException):
            raise self.pid
        return self.pid

    def recv(self, size: int) -> bytes:
        self.gone.wait(20)
        return b""

    def shutdown(self, how=None) -> None:
        self.gone.set()

    close = shutdown


PIPE = r"\\.\pipe\moxi-demo-a-0123abcd"


@pytest.mark.parametrize("code", [2, 3])
def test_on_windows_a_pipe_that_isnt_there_is_not_serving(opens, code):
    opens.answers = [win_error(code)]
    with pytest.raises(ipc.NotServing) as caught:
        ipc.connect(PIPE)
    assert caught.value.exit_code == 75 and caught.value.address == PIPE
    assert opens.tried == [PIPE]


def test_on_windows_a_busy_pipe_is_tried_every_50_ms_until_the_timeout(opens):
    start = time.monotonic()
    with pytest.raises(ipc.Timeout) as caught:
        ipc.connect(PIPE, timeout=0.3)
    assert 0.3 - EARLY <= time.monotonic() - start < 3
    assert "busy" in str(caught.value) and isinstance(caught.value, TimeoutError)
    # every 50 ms, and in the clock's last step before the deadline a few more times
    assert 2 <= len(opens.tried) <= 15


def test_on_windows_a_busy_pipe_is_tried_again_until_it_opens(opens):
    end = Opened()
    opens.answers = [win_error(231), win_error(231), win_error(231), end]
    start = time.monotonic()
    with ipc.connect(PIPE, timeout=5.0) as client:
        assert client._sock is end and client.address == PIPE
    assert 0.15 - EARLY <= time.monotonic() - start < 3
    assert len(opens.tried) == 4


def test_on_windows_a_pipe_whose_list_refuses_this_user_is_an_ipc_error(opens):
    opens.answers = [win_error(5)]  # ERROR_ACCESS_DENIED: another user's pipe
    with pytest.raises(ipc.IpcError) as caught:
        ipc.connect(PIPE)
    assert type(caught.value) is ipc.IpcError
    assert f"refused the pipe at {PIPE}" in str(caught.value)
    assert "access denied" in str(caught.value)
    assert opens.tried == [PIPE]  # not tried again, as busy is


def test_on_windows_any_other_open_error_is_raised_as_it_is(opens):
    opens.answers = [win_error(1450)]
    with pytest.raises(OSError) as caught:
        ipc.connect(PIPE)
    assert caught.value.winerror == 1450 and not isinstance(caught.value, ipc.IpcError)


@pytest.mark.parametrize("address", ["/run/user/1000/moxi/demo/a.sock",
                                     r"C:\Users\x\AppData\a.sock", r"\\server\pipe\x", ""])
def test_on_windows_an_address_outside_the_pipe_namespace_is_never_opened(opens, address):
    with pytest.raises(ipc.NotServing, match="not a named pipe"):
        ipc.connect(address)
    assert opens.tried == []


@pytest.mark.parametrize("address", [PIPE, r"\\?\pipe\x", r"\\.\PIPE\x", r"\\.\Pipe\x"])
def test_both_spellings_of_the_pipe_namespace_are_pipes(address):
    assert _pipe.is_pipe(address)


def test_the_pipe_is_opened_overlapped_and_never_lets_the_server_act_as_the_client(
        monkeypatch):
    import types

    seen = {}
    fake = types.SimpleNamespace(GENERIC_READ=0x80000000, GENERIC_WRITE=0x40000000,
                                 NULL=0, OPEN_EXISTING=3, FILE_FLAG_OVERLAPPED=0x40000000)

    def create_file(name, access, share, security, disposition, flags, template):
        seen.update(name=name, access=access, disposition=disposition, flags=flags)
        return 7

    fake.CreateFile = create_file
    monkeypatch.setitem(sys.modules, "_winapi", fake)
    monkeypatch.setitem(sys.modules, "_overlapped", types.SimpleNamespace(
        CreateEvent=lambda *args: 8))
    pipe = _pipe.open_pipe(PIPE)
    assert pipe._handle == 7 and pipe._stop == 8
    assert seen["access"] == 0x80000000 | 0x40000000 and seen["disposition"] == 3
    # FILE_FLAG_OVERLAPPED, and SECURITY_SQOS_PRESENT with SECURITY_IDENTIFICATION
    assert seen["flags"] == 0x40000000 | 0x00100000 | 0x00010000


# -- connect on Windows: who serves the pipe (any system) ----------------------------------

def test_on_windows_a_pipe_served_by_this_user_is_connected(opens):
    end = Opened()
    opens.answers = [end]
    with ipc.connect(PIPE) as client:
        assert client._sock is end and end.asked == 1


@pytest.fixture
def server_user(monkeypatch):
    """`server_user(user)` makes the user read of any process but this one `user`."""
    _users.own_user()  # read now, before the patch, as a real server's process would be

    def patch(user):
        mine = os.getpid()
        real = _users.user_of
        monkeypatch.setattr(_users, "user_of",
                            lambda pid: real(pid) if pid == mine else user)

    return patch


def test_on_windows_a_pipe_served_by_another_user_is_refused_and_closed(opens,
                                                                        server_user):
    server_user("ELSEWHERE\\someone")
    end = Opened(pid=4321)
    opens.answers = [end]
    with pytest.raises(ipc.IpcError) as caught:
        ipc.connect(PIPE)
    assert type(caught.value) is ipc.IpcError
    assert "(pid 4321) runs as ELSEWHERE\\someone, not as this user" in str(caught.value)
    assert PIPE in str(caught.value)
    assert end.gone.is_set() and opens.tried == [PIPE]


def test_on_windows_a_pipe_whose_server_user_cant_be_read_is_refused_and_closed(
        opens, server_user):
    server_user(None)
    end = Opened(pid=4321)
    opens.answers = [end]
    with pytest.raises(ipc.IpcError) as caught:
        ipc.connect(PIPE)
    assert type(caught.value) is ipc.IpcError
    assert "(pid 4321) can't be read" in str(caught.value)
    assert end.gone.is_set() and opens.tried == [PIPE]


def test_on_windows_a_pipe_whose_server_pid_cant_be_read_is_refused_and_closed(opens):
    end = Opened(pid=OSError(0, "GetNamedPipeServerProcessId failed"))
    opens.answers = [end]
    with pytest.raises(ipc.IpcError) as caught:
        ipc.connect(PIPE)
    assert type(caught.value) is ipc.IpcError
    assert "can't be read (GetNamedPipeServerProcessId failed)" in str(caught.value)
    assert end.gone.is_set()


@pytest.mark.parametrize("address", [r"\\.\pipe\other", r"\\.\pipe\moxi-demo-a",
                                     rf"\\.\pipe\moxi-test-pair-{'0' * 32}"])
def test_on_windows_a_pipe_name_moxi_doesnt_make_is_not_checked(opens, address):
    end = Opened(pid=AssertionError("the server was checked"))
    opens.answers = [end]
    with ipc.connect(address) as client:
        assert client._sock is end and end.asked == 0


# -- connect on Windows, on real pipes -----------------------------------------------------

@windows
def test_a_real_pipe_of_this_users_server_is_connected(serving):
    srv = serving(handler)
    with ipc.connect(srv.address) as client:
        assert client._sock.server_pid() == os.getpid()
        assert client.call("echo", [1], timeout=10) == [1]


@windows
def test_a_real_pipe_whose_server_runs_as_another_user_is_refused(serving, monkeypatch):
    srv = serving(handler)
    _users.own_user()
    real = _users.user_of
    asked = []

    def user_of(pid):
        # The client's read, on this thread, sees another user; the server's read of its
        # peer, on its loop's thread, is left real.
        if threading.current_thread() is not threading.main_thread():
            return real(pid)
        asked.append(pid)
        return "ELSEWHERE\\someone"

    monkeypatch.setattr(_users, "user_of", user_of)
    with pytest.raises(ipc.IpcError) as caught:
        ipc.connect(srv.address)
    assert type(caught.value) is ipc.IpcError
    assert f"(pid {os.getpid()}) runs as ELSEWHERE\\someone" in str(caught.value)
    assert asked == [os.getpid()]
    monkeypatch.setattr(_users, "user_of", real)
    with ipc.connect(srv.address) as client:  # the server serves on
        assert client.call("echo", [2], timeout=10) == [2]


@windows
def test_nothing_at_the_pipe_is_not_serving():
    address = ipc.address("demo", f"none{uuid.uuid4().hex[:12]}")
    with pytest.raises(ipc.NotServing) as caught:
        ipc.connect(address)
    assert caught.value.exit_code == 75 and caught.value.address == address


def busy_pipe() -> tuple[str, int, _pipe.Pipe]:
    """A pipe of at most two instances whose one instance is taken: its name, its server
    instance and the client that took it."""
    name = pipe_name()
    server = pipe_instance(name, most=2)
    return name, server, accept(server, lambda: _pipe.open_pipe(name))


def close_handles(*handles: int) -> None:
    import _winapi

    for handle in handles:
        _winapi.CloseHandle(handle)


@windows
def test_a_pipe_with_every_instance_taken_is_busy_until_the_timeout():
    name, server, taken = busy_pipe()
    try:
        start = time.monotonic()
        with pytest.raises(ipc.Timeout) as caught:
            ipc.connect(name, timeout=0.3)
        assert 0.3 - EARLY <= time.monotonic() - start < 3
        assert "busy" in str(caught.value)
    finally:
        taken.close()
        close_handles(server)


@windows
def test_a_busy_pipe_is_tried_again_until_an_instance_is_free():
    name, server, taken = busy_pipe()
    made = []

    def free_one_later():
        time.sleep(0.3)
        made.append(pipe_instance(name, first=False, most=2))

    thread = threading.Thread(target=free_one_later, daemon=True)
    thread.start()
    try:
        start = time.monotonic()
        client = bounded(lambda: ipc.connect(name, timeout=5.0))
        assert time.monotonic() - start >= 0.3 - EARLY
        client.close()
        thread.join(10)
    finally:
        taken.close()
        close_handles(server, *made)


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
    assert 0.3 - EARLY <= time.monotonic() - start < 3
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
    assert 0.3 - EARLY <= time.monotonic() - start < 3
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


# -- closed ----------------------------------------------------------------------------------

def _raise_at_once(client) -> None:
    """Once closed, `call` and `notify` raise without waiting on anything."""
    start = time.monotonic()
    with pytest.raises(ipc.IpcError):
        client.call("echo", [1])
    with pytest.raises(ipc.IpcError):
        client.notify("echo", [1])
    assert time.monotonic() - start < 1


def test_closed_is_false_after_connect_and_true_after_close(serving):
    srv = serving(handler)
    client = ipc.connect(srv.address)
    assert client.closed is False
    assert client.call("echo", [1]) == [1]
    assert client.closed is False
    client.close()
    assert client.closed is True
    _raise_at_once(client)


def test_closed_once_the_server_closes_the_connection(serving):
    srv = serving(handler)
    client = ipc.connect(srv.address)
    try:
        client.notify("bye")
        assert bounded(lambda: list(client.notifications())) == []
        assert client.closed is True
        _raise_at_once(client)
    finally:
        client.close()


def test_closed_after_an_answer_line_over_the_cap(fake):
    f = fake(max_line=100)
    f.send(b"x" * 101)
    assert bounded(lambda: list(f.client.notifications())) == []
    assert f.client.closed is True
    _raise_at_once(f.client)


def test_closed_after_a_line_that_is_not_json_rpc(fake):
    f = fake()
    assert f.client.closed is False
    f.send(b'{"ok": true}\n')
    assert bounded(lambda: list(f.client.notifications())) == []
    assert f.client.closed is True
    with pytest.raises(ipc.NotRpc):
        f.client.call("again")


@posix
def test_closed_after_a_write_that_failed(fake):
    f = fake()
    # The client's side can no longer write, while its reader still waits on the server.
    f.client._sock.shutdown(socket.SHUT_WR)
    assert f.client.closed is False
    with pytest.raises(ipc.IpcError, match="broke"):
        f.client.notify("echo", [1])
    assert f.client.closed is True
    _raise_at_once(f.client)


def test_closed_after_a_request_cut_off_by_its_timeout(fake):
    f = fake()
    with pytest.raises(ipc.Timeout):
        bounded(lambda: f.client.call("echo", [BIG], timeout=0.3))
    assert f.client.closed is True
    _raise_at_once(f.client)


def test_a_request_the_server_read_in_part_before_its_timeout_ends_the_connection(fake):
    f = fake()
    read = []
    reader = threading.Thread(target=lambda: read.append(f.server.recv(65536)), daemon=True)
    reader.start()
    with pytest.raises(ipc.Timeout):
        bounded(lambda: f.client.call("echo", [BIG], timeout=0.5))
    reader.join(10)
    assert read and read[0].startswith(b'{"jsonrpc"')
    # The server holds the start of a frame, so the next can't follow it.
    assert f.client.closed is True
    _raise_at_once(f.client)


def test_a_call_that_only_timed_out_leaves_closed_false(fake):
    f = fake()
    with pytest.raises(ipc.Timeout):
        bounded(lambda: f.client.call("echo", [1], timeout=0.2))
    assert f.client.closed is False
    request = f.request()
    f.answer(request["id"], [1])  # late: dropped, and the connection still carries calls
    thread = threading.Thread(target=lambda: f.answer(f.request()["id"], "ok"))
    thread.start()
    assert bounded(lambda: f.client.call("echo", [2], timeout=10)) == "ok"
    thread.join(10)
    assert f.client.closed is False


def test_closed_read_from_another_thread_never_raises_or_goes_back(serving):
    srv = serving(handler)
    client = ipc.connect(srv.address)
    seen: list[bool] = []
    errors: list[BaseException] = []
    stop = threading.Event()

    def watch():
        try:
            while not stop.is_set():
                seen.append(client.closed)
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            errors.append(exc)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    time.sleep(0.05)
    client.close()
    time.sleep(0.05)
    stop.set()
    thread.join(10)
    assert errors == []
    assert seen and seen[0] is False and seen[-1] is True
    first = seen.index(True)
    assert all(seen[first:])


@windows
def test_closed_after_a_write_that_failed_on_a_pipe(fake, monkeypatch):
    f = fake()

    def broken(data, deadline):
        raise BrokenPipeError(32, "the pipe broke")

    # The client's writes fail, while its reader still waits on the server.
    monkeypatch.setattr(f.client._sock, "send_by", broken)
    assert f.client.closed is False
    with pytest.raises(ipc.IpcError, match="broke"):
        f.client.notify("echo", [1])
    assert f.client.closed is True
    _raise_at_once(f.client)


def test_closed_is_read_only(fake):
    f = fake()
    with pytest.raises(AttributeError):
        f.client.closed = True


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


def test_a_client_in_another_process(serving):
    async def answer(conn, method, params):
        if method == "peer":
            return conn.peer.pid
        if method == "push":
            conn.notify("pushed", [1])
            return "pushed"
        return params

    srv = serving(answer, hello=ipc.Hello((1, 2), {"x"}))
    code = ("import json, os, sys\nimport moxi.ipc as ipc\n"
            "with ipc.connect(sys.argv[1]) as c:\n"
            "    agreed = c.hello((1, 1))\n"
            "    out = {'me': os.getpid(),"
            " 'agreed': [agreed.version, sorted(agreed.capabilities)],"
            " 'peer': c.call('peer', timeout=10), 'push': c.call('push', timeout=10),"
            " 'note': next(c.notifications())}\n"
            "print(json.dumps(out))\n")
    done = subprocess.run([sys.executable, "-c", code, srv.address], capture_output=True,
                          text=True, timeout=60, check=True)
    got = json.loads(done.stdout)
    assert got == {"me": got["me"], "agreed": [1, ["x"]], "peer": got["me"],
                   "push": "pushed", "note": ["pushed", [1]]}


def test_the_reader_is_one_daemon_thread_that_ends_with_the_client(serving):
    srv = serving(handler)
    before = {t.ident for t in threading.enumerate()}
    client = ipc.connect(srv.address)
    readers = [t for t in threading.enumerate() if t.ident not in before]
    assert [t.name for t in readers] == ["moxi.ipc reader"] and readers[0].daemon
    client.close()
    readers[0].join(10)
    assert not readers[0].is_alive()


def test_a_read_in_progress_holds_no_write(fake):
    f = fake()
    time.sleep(0.1)  # the reader thread waits in its read: the server has sent nothing
    bounded(lambda: f.client.notify("tick", [1]), timeout=5)
    assert f.request()["method"] == "tick"
    thread = threading.Thread(target=lambda: f.answer(f.request()["id"], "ok"))
    thread.start()
    assert bounded(lambda: f.client.call("echo", [2], timeout=10)) == "ok"
    thread.join(10)


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
