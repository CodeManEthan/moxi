"""moxi.ipc's server and sweep on Windows named pipes.

The first tests check the Windows-only logic with the platform patched, so they run
anywhere: where the lock is, what a sweep reports, how a peer is read and judged, and
that the lock goes only with the last pipe instance. The rest run on real pipes on
Windows and skip elsewhere."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid

import pytest
from conftest import PipeRaw, ServerThread

import moxi.ipc as ipc
from moxi import _win, files, paths, proc
from moxi.ipc import _server, _sweep

WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipes")


def req(id, method, params=None) -> bytes:
    obj = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        obj["params"] = params
    return json.dumps(obj).encode() + b"\n"


async def echo(conn, method, params):
    if method == "peer":
        peer = conn.peer
        return {"pid": peer.pid, "uid": peer.uid,
                "started": peer.proc.started if peer.proc else None}
    return {"method": method, "params": params}


def wait_for(predicate, timeout: float = 10.0, what: str = "the condition"):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        assert time.monotonic() < deadline, f"{what} did not happen in {timeout} s"
        time.sleep(0.01)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """The demo module's runtime root, in the test's tmp."""
    root = tmp_path / "rt"
    monkeypatch.setenv("MOXI_DEMO_RUNTIME_DIR", str(root))
    return root


@pytest.fixture
def fake_windows(monkeypatch):
    """Pipe addresses and the Windows lock place on any system: the platform flag set
    and the user hash fixed."""
    monkeypatch.setattr(ipc, "_WINDOWS", True)
    monkeypatch.setattr(ipc, "_user_hash", lambda: "0123abcd")
    monkeypatch.setattr(_sweep, "_WINDOWS", True)


# -- the lock's place and the sweep (any system) ---------------------------------------------

def test_a_pipes_lock_is_its_name_in_the_modules_runtime_directory(fake_windows, runtime):
    address = ipc.address("demo", "a-b")
    assert address == r"\\.\pipe\moxi-demo-a-b-0123abcd"
    assert _sweep.lock_path(address) == str(runtime / "demo" / "a-b.lock")


def test_a_pipe_address_moxi_did_not_make_has_no_lock(fake_windows):
    with pytest.raises(ValueError):
        _sweep.lock_path(r"\\.\pipe\somebody-else")


def test_a_windows_sweep_with_no_lock_file_is_absent_and_makes_nothing(fake_windows,
                                                                       runtime):
    assert ipc.sweep(ipc.address("demo", "a")) == "absent"
    assert not runtime.exists()


def test_a_windows_sweep_of_a_free_lock_is_absent_and_removes_nothing(fake_windows,
                                                                      runtime):
    lock = runtime / "demo" / "a.lock"
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"")
    assert ipc.sweep(ipc.address("demo", "a")) == "absent"
    assert lock.exists()


def test_a_windows_sweep_of_a_held_lock_is_live(fake_windows, runtime):
    lock = runtime / "demo" / "a.lock"
    child, _pid = holder(lock)
    try:
        assert ipc.sweep(ipc.address("demo", "a")) == "live"
    finally:
        child.stdin.close()
        child.wait(30)
    with files.lock(lock, timeout=0):
        assert ipc.sweep(ipc.address("demo", "a")) == "live"


def holder(lock_path) -> tuple[subprocess.Popen, int]:
    """A process that holds lock file `lock_path` until its stdin closes, and the pid it
    holds it as. Returns once it says it holds the lock. (A virtual environment's
    `python.exe` on Windows starts the real interpreter as its own child, so that pid
    need not be the `Popen`'s.)"""
    code = ("import os, sys\nfrom moxi import files\n"
            "with files.lock(sys.argv[1]):\n"
            "    print('held', os.getpid(), flush=True)\n"
            "    sys.stdin.read()\n")
    child = subprocess.Popen([sys.executable, "-c", code, str(lock_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    said, _, pid = child.stdout.readline().strip().partition(b" ")
    assert said == b"held"
    return child, int(pid)


# -- reading and judging a peer (any system) -------------------------------------------------

class FakePipe:
    def fileno(self):
        return 1234


class FakeTransport:
    def __init__(self, pipe=FakePipe()):
        self.pipe = pipe
        self.aborted = False

    def get_extra_info(self, name, default=None):
        return self.pipe if name == "pipe" else default

    def abort(self):
        self.aborted = True


def test_a_pipe_peer_is_read_from_the_pipe_handle(monkeypatch):
    seen = []
    monkeypatch.setattr(_win, "pipe_client_pid", lambda h: seen.append(h) or os.getpid())
    peer, user = _server._pipe_peer(FakeTransport())
    assert seen == [1234]
    assert peer.pid == os.getpid() and peer.uid is None
    assert peer.proc == proc.identity(os.getpid())
    assert user is not None and user == _server._own_user()


def test_a_pipe_peer_that_cant_be_read_has_no_pid_and_no_user(monkeypatch):
    def fail(handle):
        raise OSError(0, "GetNamedPipeClientProcessId failed")

    monkeypatch.setattr(_win, "pipe_client_pid", fail)
    assert _server._pipe_peer(FakeTransport()) == (_server.Peer(None, None, None), None)
    assert _server._pipe_peer(FakeTransport(None)) == (_server.Peer(None, None, None), None)


def test_a_gone_process_has_no_user():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(30)
    assert _server._user_of(child.pid) is None


class FakeWriter:
    def __init__(self):
        self.transport = FakeTransport()


@pytest.mark.parametrize("user", ["ELSEWHERE\\someone", None],
                         ids=["another user", "unreadable"])
def test_a_pipe_peer_of_another_user_is_dropped_unread(monkeypatch, caplog, user):
    called = []

    async def handler(conn, method, params):
        called.append(method)

    monkeypatch.setattr(_server, "_pipe_peer",
                        lambda transport: (_server.Peer(4321, None, None), user))

    async def main():
        server = _server.Server("x", handler, hello=None, max_line=100,
                                write_deadline=1.0, queue=4, legacy=None, on_close=None)
        writer = FakeWriter()
        await server._accept_pipe(None, writer)
        return writer, server

    with caplog.at_level(logging.WARNING, "moxi.ipc"):
        writer, server = asyncio.run(main())
    assert writer.transport.aborted and not server.connections and called == []
    assert any(f"of user {user} (pid 4321): not this user" in r.getMessage()
               for r in caplog.records)


# -- the lock goes with the last pipe instance (any system) ----------------------------------

class FakePipeServer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_the_lock_is_let_go_only_once_the_last_pipe_instance_has_closed(tmp_path):
    lock = tmp_path / "a.lock"

    async def main():
        server = _server.Server("x", echo, hello=None, max_line=100, write_deadline=1.0,
                                queue=4, legacy=None, on_close=None)
        server._hold.enter_context(files.lock(lock, timeout=0))
        listener = FakePipeServer()
        server._pipes = [listener]
        one = _server._PipeProtocol(server, asyncio.StreamReader())
        two = _server._PipeProtocol(server, asyncio.StreamReader())
        assert server._pipes_open == 2
        server.close()
        assert listener.closed
        steps = [held(lock)]
        one.connection_lost(None)
        await asyncio.sleep(0)
        steps.append(held(lock))
        two.connection_lost(None)
        steps.append(held(lock))  # the count drops on the next callback, not this one
        await asyncio.wait_for(server.wait_closed(), 5)
        steps.append(held(lock))
        return steps

    assert asyncio.run(main()) == [True, True, True, False]


def test_a_closed_pipe_server_with_no_instance_open_lets_the_lock_go_at_once(tmp_path):
    lock = tmp_path / "a.lock"

    async def main():
        server = _server.Server("x", echo, hello=None, max_line=100, write_deadline=1.0,
                                queue=4, legacy=None, on_close=None)
        server._hold.enter_context(files.lock(lock, timeout=0))
        server._pipes = [FakePipeServer()]
        server.close()
        return held(lock)

    assert asyncio.run(main()) is False


def held(lock) -> bool:
    """Whether lock file `lock` is held, asked by a fresh process: within this one a
    second take raises instead of saying."""
    code = ("import sys\nfrom moxi import files\n"
            "try:\n    with files.lock(sys.argv[1], timeout=0): print('free')\n"
            "except files.LockTimeout: print('held')\n")
    out = subprocess.run([sys.executable, "-c", code, str(lock)], capture_output=True,
                         timeout=30, check=True).stdout
    return out.strip() == b"held"


# -- real pipes (Windows) --------------------------------------------------------------------

@pytest.fixture
def pipe(runtime):
    """A fresh pipe address for the demo module; pipe names are machine-wide, so each
    test takes its own."""
    return ipc.address("demo", f"t{uuid.uuid4().hex[:12]}")


@pytest.fixture
def pipe_serving(pipe):
    """`pipe_serving(handler, **options)` starts a server at the test's pipe on a loop
    thread and closes it after the test."""
    started = []

    def start(handler, **options):
        thread = ServerThread(options.pop("address", pipe), handler, **options)
        started.append(thread)
        return thread

    yield start
    for thread in reversed(started):
        thread.close()


async def _close_and_wait(server):
    server.close()
    await server.wait_closed()


def connect_fails(address) -> int:
    """The Windows error code opening `address` fails with."""
    import _winapi

    try:
        handle = _winapi.CreateFile(address, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                                    0, _winapi.NULL, _winapi.OPEN_EXISTING, 0, _winapi.NULL)
    except OSError as exc:
        return exc.winerror
    _winapi.CloseHandle(handle)
    raise AssertionError(f"{address} opened")


@WINDOWS
def test_serve_on_a_pipe_answers_and_keeps_its_lock_in_a_private_directory(pipe_serving,
                                                                           runtime):
    srv = pipe_serving(echo)
    raw = PipeRaw(srv.address)
    raw.send(req(1, "go"))
    assert raw.json()["result"] == {"method": "go", "params": None}
    directory = runtime / "demo"
    assert os.path.exists(_sweep.lock_path(srv.address))
    assert os.path.dirname(_sweep.lock_path(srv.address)) == str(directory)
    assert _win._only_user(directory, _win._token_sid(_win._TOKEN_USER))
    assert ipc.sweep(srv.address) == "live"


@WINDOWS
def test_serve_refuses_a_pipe_address_moxi_did_not_make(runtime):
    with pytest.raises(ValueError):
        asyncio.run(ipc.serve(r"\\.\pipe\somebody-else", echo))


@WINDOWS
def test_close_ends_open_connections_closes_the_pipe_then_lets_the_lock_go(pipe_serving):
    srv = pipe_serving(echo)
    raws = [PipeRaw(srv.address) for _ in range(3)]
    for i, raw in enumerate(raws):
        raw.send(req(i, "go"))
        raw.json()
    srv.run(_close_and_wait(srv.server))
    assert all(raw.closed() for raw in raws)
    assert not srv.server.is_serving()
    assert connect_fails(srv.address) == 2  # ERROR_FILE_NOT_FOUND
    assert not held(_sweep.lock_path(srv.address))
    assert ipc.sweep(srv.address) == "absent"


@WINDOWS
def test_a_server_started_right_after_one_closed_with_clients_attached_serves(pipe_serving):
    srv = pipe_serving(echo)
    raws = [PipeRaw(srv.address) for _ in range(3)]
    for raw in raws:
        raw.send(req(1, "go"))
        raw.json()
    srv.call(srv.server.close)  # not waiting: the next server starts at once
    again = pipe_serving(echo)
    raw = PipeRaw(again.address)
    raw.send(req(2, "go"))
    assert raw.json()["id"] == 2


@WINDOWS
def test_close_cancels_a_running_handler(pipe_serving):
    started, cancelled = [], []

    async def handler(conn, method, params):
        started.append(method)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(method)
            raise

    srv = pipe_serving(handler)
    raw = PipeRaw(srv.address)
    raw.send(req(1, "wait"))
    wait_for(lambda: started, what="the handler's start")
    srv.run(_close_and_wait(srv.server))
    assert cancelled == ["wait"]
    assert raw.closed()


@WINDOWS
def test_a_second_server_in_this_process_is_already_serving(pipe_serving):
    srv = pipe_serving(echo)
    with pytest.raises(ipc.AlreadyServing) as caught:
        ServerThread(srv.address, echo)
    assert caught.value.holder == os.getpid()
    raw = PipeRaw(srv.address)
    raw.send(req(1, "go"))
    assert raw.json()["id"] == 1


@WINDOWS
def test_a_live_holder_in_another_process_is_waited_on_then_refused(pipe):
    lock = _sweep.lock_path(pipe)
    os.makedirs(os.path.dirname(lock))
    child, pid = holder(lock)
    try:
        start = time.monotonic()
        with pytest.raises(ipc.AlreadyServing) as caught:
            ServerThread(pipe, echo)
        waited = time.monotonic() - start
    finally:
        child.stdin.close()
        child.wait(30)
    assert caught.value.holder == pid
    assert waited >= _server.LOCK_WAIT * 0.9


@WINDOWS
def test_a_pipe_another_process_made_without_the_lock_refuses_the_server(pipe):
    import _winapi

    squat = _winapi.CreateNamedPipe(
        pipe, _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED,
        _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT,
        _winapi.PIPE_UNLIMITED_INSTANCES,
        8192, 8192, 0, _winapi.NULL)
    try:
        with pytest.raises(ipc.AlreadyServing) as caught:
            ServerThread(pipe, echo)
        assert caught.value.holder is None
    finally:
        _winapi.CloseHandle(squat)
    assert not held(_sweep.lock_path(pipe))
    srv = ServerThread(pipe, echo)
    try:
        raw = PipeRaw(pipe)
        raw.send(req(1, "go"))
        assert raw.json()["id"] == 1
    finally:
        srv.close()


@WINDOWS
def test_serve_forever_closes_when_cancelled(pipe_serving):
    srv = pipe_serving(echo)

    async def cancel():
        task = asyncio.ensure_future(srv.server.serve_forever())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return srv.server.is_serving()

    assert srv.run(cancel()) is False
    assert connect_fails(srv.address) == 2


@WINDOWS
def test_the_peer_is_this_process(pipe_serving):
    srv = pipe_serving(echo)
    raw = PipeRaw(srv.address)
    raw.send(req(1, "peer"))
    peer = raw.json()["result"]
    assert peer["pid"] == os.getpid() and peer["uid"] is None
    assert peer["started"] == proc.identity(os.getpid()).started


@WINDOWS
def test_the_peer_in_another_process_is_that_process(pipe_serving):
    srv = pipe_serving(echo)
    code = ("import json, os, sys, _winapi, msvcrt\n"
            "h = _winapi.CreateFile(sys.argv[1], _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,"
            " 0, 0, _winapi.OPEN_EXISTING, 0, 0)\n"
            "f = os.fdopen(msvcrt.open_osfhandle(h, 0), 'r+b', buffering=0)\n"
            "f.write(b'{\"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"peer\"}\\n')\n"
            "line = b''\n"
            "while not line.endswith(b'\\n'):\n    line += f.read(4096)\n"
            "print(json.dumps({'me': os.getpid(), 'answer': json.loads(line)}))\n")
    out = subprocess.run([sys.executable, "-c", code, srv.address], capture_output=True,
                         timeout=60, check=True).stdout
    got = json.loads(out)
    assert got["answer"]["result"]["pid"] == got["me"]
    assert got["answer"]["result"]["started"] is not None


@WINDOWS
def test_a_peer_of_another_user_is_dropped_on_a_real_pipe(pipe_serving, monkeypatch,
                                                          caplog):
    called = []

    async def handler(conn, method, params):
        called.append(method)

    monkeypatch.setattr(_server, "_own_user", lambda: "ELSEWHERE\\someone")
    srv = pipe_serving(handler)
    raw = PipeRaw(srv.address)
    try:
        raw.send(req(1, "go"))
    except OSError:
        pass  # dropped before the write finished
    assert raw.closed()
    assert called == []
    assert any("not this user" in r.getMessage() for r in caplog.records)


@WINDOWS
def test_framing_errors_on_the_wire(pipe_serving):
    srv = pipe_serving(echo)
    raw = PipeRaw(srv.address)
    raw.send(b"\n  \nnot json\n")
    assert raw.json() == {"jsonrpc": "2.0", "id": None,
                          "error": {"code": -32700, "message": "parse error: not JSON"}}
    raw.send(b"42\n")
    assert raw.json()["error"]["code"] == -32600
    raw.send(b'[{"jsonrpc":"2.0","id":1,"method":"go"}]\n')
    assert raw.json()["error"] == {"code": -32600, "message": "batches are not supported"}
    raw.send(req(2, "go"))
    assert raw.json()["id"] == 2


@WINDOWS
def test_with_a_hello_the_agreed_set_is_on_the_connection(pipe_serving):
    async def handler(conn, method, params):
        return [conn.agreed.version, sorted(conn.agreed.capabilities)]

    srv = pipe_serving(handler, hello=ipc.Hello((1, 4), {"a", "b"}))
    raw = PipeRaw(srv.address)
    raw.send(req(0, "rpc.hello", {"versions": [2, 9], "capabilities": ["b", "c"]}))
    assert raw.json()["result"] == {"version": 4, "capabilities": ["a", "b"]}
    raw.send(req(1, "go"))
    assert raw.json()["result"] == [4, ["b", "c"]]


def sized(n: int) -> bytes:
    """A request line of exactly `n` bytes, without its newline."""
    base = b'{"jsonrpc":"2.0","id":1,"method":"go","params":[""]}'
    line = base.replace(b'[""]', b'["' + b"x" * (n - len(base)) + b'"]')
    assert len(line) == n
    return line


@WINDOWS
@pytest.mark.parametrize("max_line", [200, 65536])
def test_a_line_of_exactly_the_cap_passes_and_one_byte_more_closes(pipe_serving, max_line):
    srv = pipe_serving(echo, max_line=max_line)
    raw = PipeRaw(srv.address)
    raw.send(sized(max_line) + b"\n")
    assert raw.json()["id"] == 1
    raw.send(sized(max_line + 1) + b"\n")
    assert raw.json() == {"jsonrpc": "2.0", "id": None, "error": {
        "code": -32600, "message": f"line longer than {max_line} bytes"}}
    assert raw.closed()


@WINDOWS
def test_the_default_cap_is_four_mebibytes(pipe_serving):
    srv = pipe_serving(echo)
    raw = PipeRaw(srv.address, timeout=30)
    raw.send(sized(4 << 20) + b"\n")
    assert raw.json()["id"] == 1
    raw.send(sized((4 << 20) + 1) + b"\n")
    assert raw.json()["error"]["message"] == "line longer than 4194304 bytes"
    assert raw.closed()


@WINDOWS
def test_a_client_gone_mid_line_ends_its_connection_and_others_go_on(pipe_serving):
    closed = []
    srv = pipe_serving(echo, on_close=closed.append)
    raw = PipeRaw(srv.address)
    raw.send(b'{"jsonrpc": "2.0", "id": 1, "meth')
    raw.close()
    wait_for(lambda: closed, what="the cut connection's end")
    other = PipeRaw(srv.address)
    other.send(req(2, "go"))
    assert other.json()["id"] == 2


@WINDOWS
def test_many_clients_at_once_are_all_served(pipe_serving):
    async def handler(conn, method, params):
        await asyncio.sleep(0.2)
        return params

    srv = pipe_serving(handler)
    answers = {}

    def one(i):
        raw = PipeRaw(srv.address, timeout=30)
        raw.send(req(i, "go", [i]))
        answers[i] = raw.json()["result"]
        raw.close()

    threads = [threading.Thread(target=one, args=(i,)) for i in range(16)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert answers == {i: [i] for i in range(16)}
    assert time.monotonic() - start < 16 * 0.2  # served side by side, not in turn


@WINDOWS
def test_notify_threadsafe_from_executor_threads(pipe_serving):
    import concurrent.futures

    pool = concurrent.futures.ThreadPoolExecutor(4)

    async def handler(conn, method, params):
        def work(k):
            for i in range(50):
                conn.notify_threadsafe("t", [k, i])

        loop = asyncio.get_running_loop()
        await asyncio.gather(*(loop.run_in_executor(pool, work, k) for k in range(4)))
        return "done"

    try:
        srv = pipe_serving(handler, queue=1000)
        raw = PipeRaw(srv.address)
        raw.send(req(1, "spawn"))
        lines = [raw.json() for _ in range(201)]
    finally:
        pool.shutdown()
    notes = [m["params"] for m in lines if "id" not in m]
    assert len(notes) == 200
    for k in range(4):
        assert [i for kk, i in notes if kk == k] == list(range(50))


@WINDOWS
def test_a_full_queue_drops_the_peer(pipe_serving, caplog):
    results = []

    async def handler(conn, method, params):
        results.extend(conn.notify("n", [i]) for i in range(6))
        return "unsent"

    srv = pipe_serving(handler, queue=4)
    raw = PipeRaw(srv.address)
    raw.send(req(1, "go"))
    assert raw.closed()
    assert results == [True, True, True, True, False, False]
    assert any("queue of 4 frames is full" in r.getMessage() for r in caplog.records)


@WINDOWS
def test_a_stuck_peer_is_dropped_at_the_write_deadline_and_others_go_on(pipe_serving,
                                                                        caplog):
    deadline = 1.5
    pushed = []
    closed = []

    async def handler(conn, method, params):
        if method == "flood":
            for _ in range(64):
                conn.notify("big", ["x" * 65536])
            pushed.append(time.monotonic())
            return None
        return "fine"

    def on_close(conn):
        closed.append(time.monotonic())

    srv = pipe_serving(handler, write_deadline=deadline, queue=1000, on_close=on_close)
    stuck = PipeRaw(srv.address)
    stuck.send(req(1, "flood"))
    wait_for(lambda: pushed, what="the flood")
    other = PipeRaw(srv.address)
    other.send(req(2, "go"))
    assert other.json()["result"] == "fine"
    assert not closed
    wait_for(lambda: closed, timeout=deadline + 10, what="the stuck peer's drop")
    waited = closed[0] - pushed[0]
    assert deadline * 0.8 <= waited <= deadline + 5
    assert any(f"longer than {deadline} s" in r.getMessage() for r in caplog.records)
    other.send(req(3, "go"))
    assert other.json()["id"] == 3


@WINDOWS
def test_an_old_clients_first_line_goes_to_legacy(pipe_serving):
    got = []

    def legacy(first):
        got.append(first)
        return b'{"ok": false, "error": "upgrade"}\n'

    srv = pipe_serving(echo, legacy=legacy)
    raw = PipeRaw(srv.address)
    raw.send(b'{"id": 1, "op": "register"}\n')
    assert raw.line() == b'{"ok": false, "error": "upgrade"}'
    assert raw.closed()
    assert got == [b'{"id": 1, "op": "register"}']


@WINDOWS
def test_async_with_closes_the_server(pipe):
    async def main():
        async with await ipc.serve(pipe, echo) as server:
            assert server.is_serving()
        return connect_fails(pipe)

    assert asyncio.run(main()) == 2
