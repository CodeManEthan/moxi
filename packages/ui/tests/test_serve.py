"""The shell on its real listener: the 4 MiB head limit, the port taken, the lock held."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest
from moxi.paths import Layout
from moxi_ui import listen, serve
from moxi_ui.serve import HEAD_LIMIT, Shell


@pytest.fixture
def shell():
    Layout("ui").runtime_dir(create=True)
    running = Shell(listen.bind(0), epoch=None)
    running.start()
    thread = threading.Thread(target=lambda: asyncio.run(running.run()), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while running._should_exit is None and time.monotonic() < deadline:
        time.sleep(0.01)
    yield running
    running.stop()
    thread.join(10)
    running.close()


def _request(port: int, head: bytes, body: bytes = b"") -> tuple[int, bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        try:
            sock.sendall(head + body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        data = b""
        while b"\r\n\r\n" not in data:
            try:
                chunk = sock.recv(65536)
            except ConnectionResetError:
                break
            if not chunk:
                break
            data += chunk
        head_end = data.find(b"\r\n\r\n")
        status = int(data.split(b" ", 2)[1]) if data else 0
        rest = data[head_end + 4:]
        length = [int(line.split(b":")[1]) for line in data[:head_end].split(b"\r\n")
                  if line.lower().startswith(b"content-length:")]
        while length and len(rest) < length[0]:
            chunk = sock.recv(65536)
            if not chunk:
                break
            rest += chunk
        return status, rest


def _head(port, method, path, lines):
    text = [f"{method} {path} HTTP/1.1".encode(), b"Host: 127.0.0.1:%d" % port, *lines]
    return b"\r\n".join(text) + b"\r\n\r\n"


WORKER = [b"Service-Worker: script", b"Sec-Fetch-Site: same-origin",
          b"Sec-Fetch-Mode: same-origin", b"Sec-Fetch-Dest: serviceworker"]


def _jar(session_id: str) -> bytes:
    cookies = [b"c%d=%s" % (n, b"v" * 4090) for n in range(179)]
    cookies.insert(90, b"moxi-ui=" + session_id.encode())
    return b"Cookie: " + b"; ".join(cookies)


def test_a_worker_check_just_under_the_limit_is_answered(shell):
    port = shell.listeners.port
    session_id, _ = shell.sessions.store.mint()
    cookie = b"Cookie: moxi-ui=" + session_id.encode()
    bare = _head(port, "GET", "/m/ui/", [cookie, *WORKER])
    path = "/m/ui/" + "a" * (HEAD_LIMIT - 1024 - len(bare) - 3) + ".js"
    head = _head(port, "GET", path, [cookie, *WORKER])
    assert HEAD_LIMIT - 1100 < len(head) <= HEAD_LIMIT - 1024
    status, body = _request(port, head)
    assert status == 200 and b"unregister" in body
    assert shell.sessions.store.live() == 0


def test_a_long_url_under_a_full_cookie_jar_is_answered(shell):
    port = shell.listeners.port
    session_id, _ = shell.sessions.store.mint()
    path = "/m/ui/" + "b" * (2 * 1024 * 1024) + "/sw.js"
    head = _head(port, "GET", path, [_jar(session_id), *WORKER])
    assert len(head) > 2_800_000
    status, _ = _request(port, head)
    assert status == 200
    assert shell.sessions.store.live() == 0


def test_a_shell_route_with_the_key_is_answered_under_a_full_cookie_jar(shell):
    port = shell.listeners.port
    session_id, key = shell.sessions.store.mint()
    head = _head(port, "GET", "/m/ui/api/modules", [
        _jar(session_id), b"X-Moxi-Key: " + key.encode(), b"Sec-Fetch-Site: same-origin",
        b"Sec-Fetch-Mode: cors", b"Sec-Fetch-Dest: empty"])
    assert len(head) > 700_000
    status, body = _request(port, head)
    assert status == 200 and "modules" in json.loads(body)


def test_a_head_over_the_limit_is_refused_before_any_layer(shell):
    port = shell.listeners.port
    session_id, _ = shell.sessions.store.mint()
    path = "/m/ui/" + "c" * (5 * 1024 * 1024) + ".js"
    status, _ = _request(port, _head(port, "GET", path,
                                     [b"Cookie: moxi-ui=" + session_id.encode(), *WORKER]))
    assert status == 400
    assert shell.sessions.store.live() == 1


def test_sign_in_and_a_navigation_pass_under_a_full_cookie_jar(shell):
    port = shell.listeners.port
    jar = _jar("A" * 43)
    ticket = shell.sessions.tickets.mint()
    body = json.dumps({"ticket": ticket}).encode()
    head = _head(port, "POST", "/m/ui/api/signin", [
        jar, b"Origin: http://127.0.0.1:%d" % port, b"Sec-Fetch-Site: same-origin",
        b"Content-Type: application/json", b"Content-Length: %d" % len(body)])
    status, answer = _request(port, head, body)
    assert status == 200 and "key" in json.loads(answer)
    nav = [jar, b"Sec-Fetch-Site: none", b"Sec-Fetch-Mode: navigate", b"Sec-Fetch-Dest: document"]
    status, page = _request(port, _head(port, "GET", "/m/fixture/", nav))
    assert status == 200 and b"not signed in" in page


def _read_until(sock, marker, wait=5.0):
    sock.settimeout(wait)
    data = b""
    try:
        while marker not in data:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    except TimeoutError:
        pass
    return data


def test_a_push_stream_ends_when_every_browser_is_signed_out(shell):
    port = shell.listeners.port
    session_id, key = shell.sessions.store.mint()
    shell._loop.call_soon_threadsafe(shell.push.publish, "modules", {"v": 1})
    head = _head(port, "GET", "/m/ui/api/push?topics=ui.modules", [
        b"Cookie: moxi-ui=" + session_id.encode(), b"X-Moxi-Key: " + key.encode(),
        b"Sec-Fetch-Site: same-origin", b"Sec-Fetch-Mode: cors", b"Sec-Fetch-Dest: empty"])
    with socket.create_connection(("127.0.0.1", port), timeout=10) as stream:
        stream.sendall(head)
        first = _read_until(stream, b'{"v":1}')
        assert first.startswith(b"HTTP/1.1 200") and b'{"v":1}' in first
        shell.sessions.store.clear()
        rest = _read_until(stream, b"0\r\n\r\n")
        assert rest.endswith(b"0\r\n\r\n")
    deadline = time.monotonic() + 5
    while shell.push.streams and time.monotonic() < deadline:
        time.sleep(0.01)
    assert shell.push.streams == 0


def test_the_runtime_file_names_the_listener(shell):
    from moxi_ui.loopback.api import call, read_runtime

    info = read_runtime("ui")
    assert info.transport == "tcp" and info.port == shell.listeners.port
    status, data = call(info, "GET", "/m/ui/api/health")
    assert status == 200 and data["server_id"] == info.server_id


def _write_port(port: int) -> None:
    path = Layout("ui").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"port = {port}\n")


def test_a_port_another_program_holds_exits_1(capsys):
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    port = taken.getsockname()[1]
    _write_port(port)
    said = []
    try:
        assert serve.run(say=said.append) == 1
    finally:
        taken.close()
    assert said == [f"moxi ui: port {port} is in use by another program; choose another "
                    "with `moxi config set ui port N`"]


def test_the_port_setting_is_read_only_when_the_shell_binds_its_own():
    _write_port(0)

    def own(port):
        return listen.bind(port())

    said = []
    assert serve.run(bind=own, say=said.append) == 1
    assert said and "ui's port is 0" in said[0]

    def passed(port):
        raise listen.NotLoopback("not the shell's sockets")

    said = []
    assert serve.run(bind=passed, say=said.append) == 1
    assert said and "can't be served" in said[0]


def test_another_shells_lock_exits_75(monkeypatch):
    lock = Layout("ui").runtime_dir(create=True) / "page.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys, time; from moxi import files\n"
         f"with files.lock({str(lock)!r}):\n print('held', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE, env=os.environ.copy())
    try:
        assert holder.stdout.readline() == b"held\n"
        monkeypatch.setattr(serve, "LOCK_WAIT", 0.2)
        said = []
        assert serve.run(say=said.append) == 75
        assert "another shell" in said[0]
    finally:
        holder.kill()
        holder.wait()


# --- the epoch, the idle exit and inherited sockets ---------------------------------------

V4, V6 = "moxi-ui-shell.socket", "moxi-ui-shell-1.socket"
EPOCH = f"{V4}=1f0c2e,{V6}=9ab3d4"


def _socket_state(state="active", epoch=EPOCH, backend="systemd"):
    from moxi.units import UnitState

    return [UnitState("ui", "shell", "daemon", backend, "inactive"),
            UnitState("ui", "shell", "socket", backend, state, epoch=epoch)]


def _inherited(fd_names=(V4, V6), inherited=True):
    return listen.Listeners((), 8460, frozenset({"127.0.0.1"}), inherited=inherited,
                            fd_names=tuple(fd_names))


def test_the_epoch_is_taken_only_when_every_socket_unit_is_active_and_named():
    assert serve.epoch_for(_inherited(), rows=_socket_state()) == EPOCH
    assert serve.epoch_for(_inherited((V6, V4)), rows=_socket_state()) == EPOCH
    # The passed names are not exactly the units the epoch names.
    assert serve.epoch_for(_inherited((V4,)), rows=_socket_state()) is None
    assert serve.epoch_for(_inherited((V4, V6, "other.socket")), rows=_socket_state()) is None
    assert serve.epoch_for(_inherited((V4, V4)), rows=_socket_state()) is None
    # A socket state that is not active carries no epoch, and none is taken from one.
    assert serve.epoch_for(_inherited(), rows=_socket_state("failed")) is None
    assert serve.epoch_for(_inherited(), rows=_socket_state("active", epoch=None)) is None
    assert serve.epoch_for(_inherited(), rows=[]) is None
    # Its own listener, or launchd's sockets (no names), never have one.
    assert serve.epoch_for(_inherited(inherited=False), rows=_socket_state()) is None
    assert serve.epoch_for(_inherited(()), rows=_socket_state()) is None


def test_an_unreadable_supervisor_gives_no_epoch(monkeypatch):
    from moxi import units

    def broken(modules):
        raise RuntimeError("no bus")

    monkeypatch.setattr(units, "status", broken)
    assert serve.epoch_for(_inherited()) is None


def _held(inherited, epoch=None, idle_s=0.3):
    bound = listen.bind(0)
    held = listen.Listeners(bound.sockets, bound.port, bound.names, inherited=inherited)
    Layout("ui").runtime_dir(create=True)
    return Shell(held, epoch=epoch, idle_s=idle_s)


def _serving(shell):
    shell.start()
    thread = threading.Thread(target=lambda: asyncio.run(shell.run()), daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize("epoch", [None, "e1"])
def test_a_shell_on_inherited_sockets_exits_idle_when_it_loses_no_session(epoch):
    shell = _held(True, epoch)
    if epoch is not None:
        shell.sessions.store.mint()  # on disk under its epoch, so exiting loses nothing
    thread = _serving(shell)
    try:
        thread.join(10)
        assert not thread.is_alive() and shell.idled
    finally:
        shell.stop()
        thread.join(10)
        shell.close()


@pytest.mark.parametrize("inherited", [False, True])
def test_no_idle_exit_on_its_own_listener_or_with_a_session_only_in_memory(inherited):
    shell = _held(inherited)
    if inherited:
        shell.sessions.store.mint()  # no epoch: in memory, so exiting would lose it
    thread = _serving(shell)
    try:
        thread.join(1.2)
        assert thread.is_alive() and not shell.idled
    finally:
        shell.stop()
        thread.join(10)
        shell.close()


def test_an_open_request_or_a_page_server_holds_the_idle_exit(monkeypatch):
    now = [100.0]
    shell = _held(True, idle_s=10)
    shell.activity = serve.Activity(clock=lambda: now[0])
    assert not shell.idle()
    now[0] += 10
    assert shell.idle()
    monkeypatch.setattr(shell.starters, "children", lambda: 1)
    assert not shell.idle()
    shell.listeners.close()

    release = asyncio.Event()

    async def inner(scope, receive, send):
        await release.wait()

    async def drive():
        counted = shell.activity.wrap(inner)
        request = asyncio.ensure_future(counted({"type": "http", "path": "/m/fixture/"},
                                                None, None))
        probe = asyncio.ensure_future(counted({"type": "http", "path": "/m/ui/api/health"},
                                              None, None))
        await asyncio.sleep(0)
        assert shell.activity.open == 1 and shell.activity.quiet() is None
        now[0] += 5
        release.set()
        await asyncio.gather(request, probe)
        assert shell.activity.open == 0 and shell.activity.quiet() == 0.0

    asyncio.run(drive())


@pytest.mark.skipif(shutil.which("systemd-socket-activate") is None,
                    reason="needs systemd-socket-activate")
def test_a_shell_started_on_a_passed_socket_serves_it_then_exits_idle(tmp_path):
    from moxi_ui.loopback.api import call, read_runtime

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    # The setting names another port: a socket-started shell serves the one it was given.
    path = Layout("ui").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"port = {port + 1 if port < 65535 else port - 1}\nidle_s = 1\n")
    argv = ["systemd-socket-activate", "-l", f"127.0.0.1:{port}", f"--fdname={V4}"]
    for name in os.environ:
        argv += ["-E", name]
    argv += [sys.executable, "-P", "-m", "moxi_ui.serve"]
    activator = subprocess.Popen(argv, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                conn = socket.create_connection(("127.0.0.1", port), timeout=10)
                break
            except ConnectionRefusedError:
                assert time.monotonic() < deadline, "the activator never listened"
                time.sleep(0.05)
        with conn:
            conn.sendall(_head(port, "GET", "/m/ui/api/health", []))
            assert conn.recv(12).startswith(b"HTTP/1.1 ")
        info = read_runtime("ui")
        assert info is not None and info.port == port
        status, data = call(info, "GET", "/m/ui/api/health")
        assert status == 200 and data["server_id"] == info.server_id
        assert activator.wait(30) == 0
        log = activator.stderr.read()
        assert "on inherited sockets" in log and "idle for 1 s" in log
        assert read_runtime("ui") is None
    finally:
        if activator.poll() is None:
            activator.kill()
            activator.wait()
