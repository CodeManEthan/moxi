"""The shell on its real listener: the 4 MiB head limit, the port taken, the lock held."""

from __future__ import annotations

import asyncio
import json
import os
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


def test_the_runtime_file_names_the_listener(shell):
    from moxi_loopback.api import call, read_runtime

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
