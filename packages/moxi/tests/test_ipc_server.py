"""moxi.ipc's server and sweep on real Unix sockets: the lock, the socket's mode, peers,
framing on the wire, the line cap, pushed notifications, dropped peers and legacy=."""

from __future__ import annotations

import asyncio
import json
import os
import select
import socket
import stat
import struct
import subprocess
import sys
import threading
import time

import pytest
from conftest import Raw, ServerThread

import moxi.ipc as ipc
from moxi import files, proc
from moxi.ipc import _server

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="Unix sockets; the Windows pipe server is built apart")


def req(id, method, params=None, **extra) -> bytes:
    obj = {"jsonrpc": "2.0", "id": id, "method": method, **extra}
    if params is not None:
        obj["params"] = params
    return json.dumps(obj).encode() + b"\n"


def note(method, params=None) -> bytes:
    obj = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        obj["params"] = params
    return json.dumps(obj).encode() + b"\n"


async def echo(conn, method, params):
    if method == "fail":
        raise ipc.RpcError(1001, "no such thing", {"x": 1})
    if method == "boom":
        raise ValueError("a secret detail")
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


def holder(lock_path, hold_for: float | None = None) -> subprocess.Popen:
    """A process that holds lock file `lock_path`, until its stdin closes or for
    `hold_for` seconds. Returns once it says it holds the lock."""
    code = ("import sys, time\nfrom moxi import files\n"
            "with files.lock(sys.argv[1]):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(float(sys.argv[2])) if sys.argv[2] != '-' else sys.stdin.read()\n")
    child = subprocess.Popen([sys.executable, "-c", code, str(lock_path),
                              "-" if hold_for is None else str(hold_for)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    ready, _, _ = select.select([child.stdout], [], [], 30)
    assert ready and child.stdout.readline() == b"held\n"
    return child


# -- the socket, the lock and closing ---------------------------------------------------------

def test_serve_makes_a_private_directory_and_a_0600_socket(serving, sock_dir):
    address = sock_dir / "new" / "a.sock"
    srv = serving(echo, address=str(address))
    assert stat.S_ISSOCK(os.lstat(address).st_mode)
    assert stat.S_IMODE(os.lstat(address).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(address.parent).st_mode) == 0o700
    assert os.path.exists(f"{address}.lock")
    raw = Raw(srv.address)
    raw.send(req(1, "go"))
    assert raw.json()["result"] == {"method": "go", "params": None}


def mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_serve_refuses_an_open_directory_of_the_users_and_leaves_it(serving, sock_dir):
    (sock_dir / "s").mkdir()
    os.chmod(sock_dir / "s", 0o775)
    with pytest.raises(OSError, match=str(sock_dir / "s")):
        serving(echo)
    assert mode(sock_dir / "s") == 0o775
    assert os.listdir(sock_dir / "s") == []


def test_serve_uses_a_closed_directory_of_the_users_as_it_is(serving, sock_dir):
    (sock_dir / "s").mkdir()
    os.chmod(sock_dir / "s", 0o755)
    srv = serving(echo)
    assert mode(sock_dir / "s") == 0o755
    raw = Raw(srv.address)
    raw.send(req(1, "go"))
    assert raw.json()["result"] == {"method": "go", "params": None}


def test_serve_refuses_a_linked_directory_and_leaves_it(serving, sock_dir):
    (sock_dir / "real").mkdir(mode=0o700)
    os.symlink(sock_dir / "real", sock_dir / "s")
    with pytest.raises(OSError, match="symbolic link"):
        serving(echo)
    assert os.listdir(sock_dir / "real") == []


def test_serve_refuses_a_directory_another_user_owns(serving):
    if os.lstat("/").st_uid == os.getuid():
        pytest.skip("the root directory is this user's")
    with pytest.raises(OSError, match="not this user"):
        serving(echo, address="/x.sock")


def test_serve_makes_a_modules_runtime_directory_private(serving, sock_dir, monkeypatch):
    from moxi import paths

    monkeypatch.setenv("MOXI_DEMO_RUNTIME_DIR", str(sock_dir / "rt"))
    runtime = paths.Layout("demo").runtime_dir()
    runtime.mkdir(parents=True)
    os.chmod(runtime, 0o755)
    serving(echo, address=str(runtime / "a.sock"))
    assert mode(runtime) == 0o700


def test_serve_makes_the_short_hashed_directory_private(serving, sock_dir, monkeypatch):
    from moxi import paths

    monkeypatch.setattr(paths, "_SHORT_ROOT", str(sock_dir))
    short = sock_dir / f"moxi-{os.getuid()}"
    short.mkdir()
    os.chmod(short, 0o755)
    serving(echo, address=str(short / "0123456789abcdef.sock"))
    assert mode(short) == 0o700


def test_a_directory_named_like_a_module_is_not_its_runtime_directory(serving, sock_dir):
    (sock_dir / "demo").mkdir()
    os.chmod(sock_dir / "demo", 0o775)
    with pytest.raises(OSError):
        serving(echo, address=str(sock_dir / "demo" / "a.sock"))
    assert mode(sock_dir / "demo") == 0o775


def test_close_removes_the_socket_before_it_lets_the_lock_go(serving):
    srv = serving(echo)
    address = srv.address
    seen = []
    hold = srv.server._hold
    release = hold.close
    hold.close = lambda: (seen.append(os.path.lexists(address)), release())
    srv.call(srv.server.close)
    assert seen == [False]
    assert not os.path.lexists(address)
    with files.lock(f"{address}.lock", timeout_s=0):
        pass
    assert ipc.sweep(address) == "absent"


def test_close_ends_open_connections(serving):
    srv = serving(echo)
    raw = Raw(srv.address)
    raw.send(req(1, "go"))
    raw.json()
    srv.run(_close_and_wait(srv.server))
    assert raw.closed()
    assert not srv.server.is_serving()


async def _close_and_wait(server):
    server.close()
    await server.wait_closed()


def test_close_cancels_a_running_handler(serving):
    started, cancelled = [], []

    async def handler(conn, method, params):
        started.append(method)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(method)
            raise

    srv = serving(handler)
    raw = Raw(srv.address)
    raw.send(req(1, "wait"))
    wait_for(lambda: started, what="the handler's start")
    srv.run(_close_and_wait(srv.server))
    assert cancelled == ["wait"]
    assert raw.closed()


def test_a_socket_left_by_a_dead_server_is_replaced(serving, sock_dir):
    address = sock_dir / "s" / "a.sock"
    address.parent.mkdir(mode=0o700)
    left = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    left.bind(str(address))
    left.close()
    assert os.path.lexists(address)
    srv = serving(echo)
    raw = Raw(srv.address)
    raw.send(req(1, "go"))
    assert raw.json()["id"] == 1


def test_a_file_that_is_not_a_socket_is_left_alone(sock_dir):
    address = sock_dir / "s" / "a.sock"
    address.parent.mkdir(mode=0o700)
    address.write_text("mine")
    with pytest.raises(FileExistsError):
        ServerThread(address, echo)
    assert address.read_text() == "mine"
    with files.lock(f"{address}.lock", timeout_s=0):
        pass


def test_a_second_server_in_this_process_is_already_serving(serving, sock_dir):
    srv = serving(echo)
    with pytest.raises(ipc.AlreadyServing) as caught:
        ServerThread(srv.address, echo)
    assert caught.value.holder == os.getpid() and caught.value.address == srv.address
    raw = Raw(srv.address)
    raw.send(req(1, "go"))
    assert raw.json()["id"] == 1


def test_a_live_holder_in_another_process_is_waited_on_then_refused(sock_dir):
    address = sock_dir / "s" / "a.sock"
    address.parent.mkdir(mode=0o700)
    child = holder(f"{address}.lock")
    try:
        start = time.monotonic()
        with pytest.raises(ipc.AlreadyServing) as caught:
            ServerThread(address, echo)
        waited = time.monotonic() - start
    finally:
        child.stdin.close()
        child.wait(30)
    assert caught.value.holder == child.pid
    assert waited >= _server.LOCK_WAIT * 0.9


def test_a_server_starting_while_a_sweep_holds_the_lock_serves(serving, sock_dir):
    address = sock_dir / "s" / "a.sock"
    address.parent.mkdir(mode=0o700)
    child = holder(f"{address}.lock", hold_for=0.3)
    try:
        srv = serving(echo)
    finally:
        child.wait(30)
    raw = Raw(srv.address)
    raw.send(req(1, "go"))
    assert raw.json()["id"] == 1


def test_serve_forever_closes_when_cancelled(serving):
    srv = serving(echo)

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
    assert not os.path.lexists(srv.address)


def test_async_with_closes_the_server(sock_dir):
    address = str(sock_dir / "a.sock")

    async def main():
        async with await ipc.serve(address, echo) as server:
            assert server.is_serving() and os.path.lexists(address)
        return os.path.lexists(address)

    assert asyncio.run(main()) is False


@pytest.mark.parametrize("options", [
    {"max_line": 0}, {"queue": 0}, {"queue": True}, {"write_deadline": 0},
])
def test_serve_refuses_bad_options(sock_dir, options):
    with pytest.raises(ValueError):
        asyncio.run(ipc.serve(str(sock_dir / "a.sock"), echo, **options))
    assert not os.path.lexists(sock_dir / "a.sock")


# -- peers ----------------------------------------------------------------------------------

def test_the_peer_is_read_from_the_kernel(serving):
    srv = serving(echo)
    raw = Raw(srv.address)
    raw.send(req(1, "peer"))
    peer = raw.json()["result"]
    assert peer["pid"] == os.getpid() and peer["uid"] == os.getuid()
    assert peer["started"] == proc.identity(os.getpid()).started


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="SO_PEERCRED is Linux's")
def test_credentials_on_linux_come_from_so_peercred():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b:
        assert _server._credentials(a) == (os.getpid(), os.getuid())


@pytest.mark.skipif(sys.platform != "darwin", reason="LOCAL_PEERPID is macOS's")
def test_credentials_on_macos_come_from_local_peerpid_and_local_peercred():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with a, b:
        assert _server._credentials(a) == (os.getpid(), os.getuid())


def test_the_macos_reading_unpacks_xucred_and_the_pid(monkeypatch):
    class Sock:
        def getsockopt(self, level, option, size=None):
            assert level == _server._SOL_LOCAL
            if option == _server._LOCAL_PEERCRED:
                assert size == _server._XUCRED_SIZE
                return struct.pack("=IIh", 0, 501, 1).ljust(size, b"\0")
            assert option == _server._LOCAL_PEERPID and size is None
            return 4242

    monkeypatch.setattr(_server, "_PLATFORM", "darwin")
    assert _server._credentials(Sock()) == (4242, 501)


@pytest.mark.parametrize("credentials", [
    lambda sock: (os.getpid(), os.getuid() + 1),
    lambda sock: (None, None),
], ids=["another uid", "unreadable"])
def test_a_peer_of_another_user_is_dropped_unread(serving, monkeypatch, caplog, credentials):
    called = []

    async def handler(conn, method, params):
        called.append(method)
        return None

    monkeypatch.setattr(_server, "_credentials", credentials)
    srv = serving(handler)
    raw = Raw(srv.address)
    try:
        raw.send(req(1, "go"))
    except OSError:
        pass  # dropped before the send finished
    assert raw.closed()
    assert called == []
    assert any("not this user" in r.getMessage() for r in caplog.records)


# -- framing on the wire ----------------------------------------------------------------------

def test_framing_errors_on_the_wire(serving):
    srv = serving(echo)
    raw = Raw(srv.address)
    raw.send(b"\n  \nnot json\n")
    assert raw.json() == {"jsonrpc": "2.0", "id": None,
                          "error": {"code": -32700, "message": "parse error: not JSON"}}
    raw.send(b"42\n")
    assert raw.json()["error"]["code"] == -32600
    raw.send(b'[{"jsonrpc":"2.0","id":1,"method":"go"}]\n')
    assert raw.json()["error"] == {"code": -32600, "message": "batches are not supported"}
    raw.send(req(2, "go"))
    assert raw.json()["id"] == 2


def test_handler_errors_are_answered(serving, caplog):
    srv = serving(echo)
    raw = Raw(srv.address)
    raw.send(req(1, "fail") + req(2, "boom"))
    assert raw.json()["error"] == {"code": 1001, "message": "no such thing", "data": {"x": 1}}
    answer = raw.json()
    assert answer["id"] == 2 and answer["error"] == {"code": -32603,
                                                     "message": "internal error"}
    assert any(r.exc_info and "a secret detail" in str(r.exc_info[1])
               for r in caplog.records)


def test_a_notification_gets_no_answer(serving):
    seen = []

    async def handler(conn, method, params):
        seen.append(method)
        return "dropped"

    srv = serving(handler)
    raw = Raw(srv.address)
    raw.send(note("tick") + req(5, "go"))
    assert raw.json() == {"jsonrpc": "2.0", "id": 5, "result": "dropped"}
    assert seen == ["tick", "go"]


def test_one_connections_messages_reach_the_handler_one_at_a_time(serving):
    running = []
    most = []

    async def handler(conn, method, params):
        running.append(1)
        most.append(len(running))
        await asyncio.sleep(0.02)
        running.pop()
        return params

    srv = serving(handler)
    raw = Raw(srv.address)
    raw.send(b"".join(req(i, "go", [i]) for i in range(1, 6)))
    assert [raw.json()["id"] for _ in range(5)] == [1, 2, 3, 4, 5]
    assert max(most) == 1


def test_with_a_hello_any_other_first_request_closes(serving):
    srv = serving(echo, hello=ipc.Hello((1, 1)))
    raw = Raw(srv.address)
    raw.send(req(1, "go"))
    assert raw.json()["error"]["code"] == -32002
    assert raw.closed()


def test_the_agreed_hello_is_on_the_connection(serving):
    async def handler(conn, method, params):
        return [conn.agreed.version, sorted(conn.agreed.capabilities)]

    srv = serving(handler, hello=ipc.Hello((1, 4), {"a", "b"}))
    raw = Raw(srv.address)
    raw.send(req(0, "rpc.hello", {"versions": [2, 9], "capabilities": ["b", "c"]}))
    assert raw.json()["result"] == {"version": 4, "capabilities": ["a", "b"]}
    raw.send(req(1, "go"))
    assert raw.json()["result"] == [4, ["b", "c"]]


# -- the line cap ---------------------------------------------------------------------------

def sized(n: int) -> bytes:
    """A request line of exactly `n` bytes, without its newline."""
    base = b'{"jsonrpc":"2.0","id":1,"method":"go","params":[""]}'
    line = base.replace(b'[""]', b'["' + b"x" * (n - len(base)) + b'"]')
    assert len(line) == n
    return line


@pytest.mark.parametrize("max_line", [200, 65536])
def test_a_line_of_exactly_the_cap_passes(serving, max_line):
    srv = serving(echo, max_line=max_line)
    raw = Raw(srv.address)
    raw.send(sized(max_line) + b"\n")
    assert raw.json()["id"] == 1
    raw.send(req(2, "go"))
    assert raw.json()["id"] == 2


@pytest.mark.parametrize("max_line", [200, 65536])
def test_a_line_one_byte_over_the_cap_is_refused_and_closes(serving, max_line):
    srv = serving(echo, max_line=max_line)
    raw = Raw(srv.address)
    raw.send(sized(max_line + 1) + b"\n")
    assert raw.json() == {"jsonrpc": "2.0", "id": None, "error": {
        "code": -32600, "message": f"line longer than {max_line} bytes"}}
    assert raw.closed()


def test_an_unfinished_line_over_the_cap_is_refused_before_its_newline(serving):
    srv = serving(echo, max_line=200)
    raw = Raw(srv.address)
    raw.send(sized(201))
    assert raw.json()["error"]["message"] == "line longer than 200 bytes"
    assert raw.closed()


def test_the_default_cap_is_four_mebibytes(serving):
    srv = serving(echo)
    raw = Raw(srv.address)
    raw.send(sized(4 << 20) + b"\n")
    assert raw.json()["id"] == 1
    raw.send(sized((4 << 20) + 1) + b"\n")
    assert raw.json()["error"]["message"] == "line longer than 4194304 bytes"
    assert raw.closed()


# -- pushed notifications ---------------------------------------------------------------------

def test_notify_from_another_task_while_a_handler_waits(serving):
    async def handler(conn, method, params):
        if method == "watch":
            done = asyncio.Event()

            async def push():
                for i in range(5):
                    assert conn.notify("tick", [i])
                    await asyncio.sleep(0.005)
                done.set()

            asyncio.ensure_future(push())
            await done.wait()
            return "watched"
        return None

    srv = serving(handler)
    raw = Raw(srv.address)
    raw.send(req(1, "watch"))
    lines = [raw.json() for _ in range(6)]
    assert [m["params"] for m in lines[:5]] == [[i] for i in range(5)]
    assert all(m["method"] == "tick" and "id" not in m for m in lines[:5])
    assert lines[5] == {"jsonrpc": "2.0", "id": 1, "result": "watched"}


def test_notify_threadsafe_from_executor_threads(serving):
    import concurrent.futures

    pool = concurrent.futures.ThreadPoolExecutor(4)

    async def handler(conn, method, params):
        def work(k):
            for i in range(50):
                conn.notify_threadsafe("t", [k, i])
            return threading.current_thread().name

        loop = asyncio.get_running_loop()
        names = await asyncio.gather(*(loop.run_in_executor(pool, work, k)
                                       for k in range(4)))
        return len(set(names))

    try:
        srv = serving(handler, queue=1000)
        raw = Raw(srv.address)
        raw.send(req(1, "spawn"))
        lines = [raw.json() for _ in range(201)]
    finally:
        pool.shutdown()
    answer = [m for m in lines if "id" in m]
    notes = [m["params"] for m in lines if "id" not in m]
    assert len(answer) == 1 and answer[0]["result"] >= 1
    assert len(notes) == 200
    for k in range(4):
        assert [i for kk, i in notes if kk == k] == list(range(50))


def test_notify_after_the_connection_closed_is_false_and_threadsafe_is_a_no_op(serving):
    conns = []

    async def handler(conn, method, params):
        conns.append(conn)
        return None

    closed = []
    srv = serving(handler, on_close=closed.append)
    raw = Raw(srv.address)
    raw.send(req(1, "go"))
    raw.json()
    raw.close()
    wait_for(lambda: closed, what="on_close")
    conn = conns[0]
    assert conn.closed
    assert srv.call(conn.notify, "late") is False
    assert conn.notify_threadsafe("late") is None


def test_on_close_is_called_once_per_connection(serving):
    closed = []

    async def on_close(conn):
        closed.append(conn.peer.pid)

    srv = serving(echo, on_close=on_close)
    for _ in range(3):
        raw = Raw(srv.address)
        raw.send(req(1, "go"))
        raw.json()
        raw.close()
    wait_for(lambda: len(closed) == 3, what="three on_close calls")
    assert closed == [os.getpid()] * 3


def test_a_full_queue_drops_the_peer(serving, caplog):
    results = []

    async def handler(conn, method, params):
        results.extend(conn.notify("n", [i]) for i in range(6))
        results.append(conn.notify("after"))
        return "unsent"

    srv = serving(handler, queue=4)
    raw = Raw(srv.address)
    raw.send(req(1, "go"))
    assert raw.closed()
    assert results == [True, True, True, True, False, False, False]
    assert any("queue of 4 frames is full" in r.getMessage() for r in caplog.records)


def test_a_stuck_peer_is_dropped_at_the_write_deadline_and_others_go_on(serving, caplog):
    deadline = 1.5
    pushed = []
    closed = {}

    async def handler(conn, method, params):
        if method == "flood":
            for _ in range(64):
                conn.notify("big", ["x" * 65536])
            pushed.append(time.monotonic())
            return None
        return "fine"

    def on_close(conn):
        closed[conn.peer.pid] = time.monotonic()

    srv = serving(handler, write_timeout_s=deadline, queue=1000, on_close=on_close)
    stuck = Raw(srv.address)
    stuck.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    stuck.send(req(1, "flood"))
    wait_for(lambda: pushed, what="the flood")
    # While the stuck peer's write waits, another peer is served at once.
    other = Raw(srv.address)
    other.send(req(2, "go"))
    assert other.json()["result"] == "fine"
    assert not closed
    wait_for(lambda: closed, timeout=deadline + 10, what="the stuck peer's drop")
    waited = closed[os.getpid()] - pushed[0]
    assert deadline * 0.8 <= waited <= deadline + 5
    assert any(f"longer than {deadline} s" in r.getMessage() for r in caplog.records)
    other.send(req(3, "go"))
    assert other.json()["id"] == 3


# -- legacy= ----------------------------------------------------------------------------------

OLD_LINES = [
    pytest.param(b"register planner", -32700, "parse error: not JSON", id="not JSON"),
    pytest.param(b"42", -32600, "a message is a JSON object", id="a value"),
    pytest.param(b'[{"jsonrpc": "2.0", "id": 1, "method": "go"}]', -32600,
                 "batches are not supported", id="a batch"),
    pytest.param(b'{"id": 1, "op": "register", "name": "planner"}', -32600,
                 'a request carries "jsonrpc": "2.0"', id="an object, no jsonrpc"),
    pytest.param(b'{"jsonrpc": "1.0", "id": 1, "method": "go"}', -32600,
                 'a request carries "jsonrpc": "2.0"', id="jsonrpc 1.0"),
]


@pytest.mark.parametrize(("line", "code", "message"), OLD_LINES)
def test_an_old_clients_first_line_goes_to_legacy(serving, line, code, message):
    got = []

    def legacy(first):
        got.append(first)
        return b'{"ok": false, "error": "upgrade"}\n'

    srv = serving(echo, legacy=legacy)
    raw = Raw(srv.address)
    raw.send(line + b"\n")
    assert raw.line() == b'{"ok": false, "error": "upgrade"}'
    assert raw.closed()
    assert got == [line]


@pytest.mark.parametrize(("line", "code", "message"), OLD_LINES)
def test_legacy_returning_none_sends_the_usual_refusal_and_closes(serving, line, code,
                                                                   message):
    srv = serving(echo, legacy=lambda first: None)
    raw = Raw(srv.address)
    raw.send(line + b"\n")
    answer = raw.json()
    assert answer["error"] == {"code": code, "message": message}
    if code == -32700 or message == "batches are not supported" or line == b"42":
        assert answer["id"] is None
    assert raw.closed()


def test_an_async_legacy_hook_is_awaited(serving):
    async def legacy(first):
        await asyncio.sleep(0)
        return b"old\n"

    srv = serving(echo, legacy=legacy)
    raw = Raw(srv.address)
    raw.send(b'{"op": "ping"}\n')
    assert raw.line() == b"old"
    assert raw.closed()


def test_a_failing_legacy_hook_sends_the_usual_refusal(serving, caplog):
    def legacy(first):
        raise RuntimeError("hook broke")

    srv = serving(echo, legacy=legacy)
    raw = Raw(srv.address)
    raw.send(b"nope\n")
    assert raw.json()["error"]["code"] == -32700
    assert raw.closed()
    assert any("legacy hook failed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("first", [
    req(1, "go"),
    note("tick"),
    b'{"jsonrpc": "2.0", "id": 1.5, "method": "go"}\n',
    b'{"jsonrpc": "2.0", "id": 1}\n',
], ids=["request", "notification", "bad id", "no method"])
def test_a_json_rpc_2_first_message_never_reaches_legacy(serving, first):
    got = []
    srv = serving(echo, legacy=got.append)
    raw = Raw(srv.address)
    raw.send(first + req(9, "go"))
    answers = [raw.json() for _ in range(1 if b'"tick"' in first else 2)]
    assert answers[-1]["id"] == 9
    assert got == []


def test_blank_lines_before_the_first_message_do_not_count(serving):
    got = []
    srv = serving(echo, legacy=lambda first: got.append(first) or b"old\n")
    raw = Raw(srv.address)
    raw.send(b"\n \n\t\nregister\n")
    assert raw.line() == b"old"
    assert got == [b"register"]


def test_an_over_long_first_line_is_refused_whatever_legacy_says(serving):
    got = []
    srv = serving(echo, max_line=200, legacy=got.append)
    raw = Raw(srv.address)
    raw.send(b"x" * 201 + b"\n")
    assert raw.json()["error"]["message"] == "line longer than 200 bytes"
    assert raw.closed()
    assert got == []


def test_only_the_first_message_is_judged(serving):
    got = []
    srv = serving(echo, legacy=got.append)
    raw = Raw(srv.address)
    raw.send(req(1, "go") + b'{"id": 2, "op": "register"}\n' + b"junk\n" + req(3, "go"))
    assert raw.json()["id"] == 1
    assert raw.json()["error"]["code"] == -32600
    assert raw.json()["error"]["code"] == -32700
    assert raw.json()["id"] == 3
    assert got == []


def test_legacy_with_a_hello_takes_an_old_client(serving):
    got = []
    srv = serving(echo, hello=ipc.Hello((1, 1)),
                  legacy=lambda first: got.append(first) or b"old\n")
    raw = Raw(srv.address)
    raw.send(b'{"id": 1, "op": "register"}\n')
    assert raw.line() == b"old"
    assert raw.closed()
    new = Raw(srv.address)
    new.send(req(1, "go"))
    assert new.json()["error"]["code"] == -32002
    assert len(got) == 1


# -- sweep ----------------------------------------------------------------------------------

def test_sweep_of_no_socket_is_absent(sock_dir):
    assert ipc.sweep(str(sock_dir / "none.sock")) == "absent"
    assert not os.path.exists(sock_dir / "none.sock.lock")


def test_sweep_removes_a_socket_no_server_holds(sock_dir):
    address = sock_dir / "a.sock"
    left = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    left.bind(str(address))
    left.close()
    assert ipc.sweep(str(address)) == "removed"
    assert not os.path.lexists(address)
    assert ipc.sweep(str(address)) == "absent"


def test_sweep_leaves_a_live_servers_socket(serving):
    srv = serving(echo)
    assert ipc.sweep(srv.address) == "live"
    code = "import sys, moxi.ipc; print(moxi.ipc.sweep(sys.argv[1]))"
    done = subprocess.run([sys.executable, "-c", code, srv.address], capture_output=True,
                          text=True, timeout=60, check=True)
    assert done.stdout.strip() == "live"
    raw = Raw(srv.address)
    raw.send(req(1, "go"))
    assert raw.json()["id"] == 1


def test_sweep_says_live_while_another_process_holds_the_lock(sock_dir):
    address = sock_dir / "a.sock"
    left = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    left.bind(str(address))
    left.close()
    child = holder(f"{address}.lock")
    try:
        assert ipc.sweep(str(address)) == "live"
        assert os.path.lexists(address)
    finally:
        child.stdin.close()
        child.wait(30)
    assert ipc.sweep(str(address)) == "removed"

