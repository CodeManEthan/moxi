"""The runner, spawned as `python -P -m moxi_loopback.serve <module>` with a stdin
lifeline, over a fake module installed in a site directory of the test's own.

Each stop case times the decision (a lifeline close or a signal, so the moment is known),
the connections' close and the process's exit against the stop's deadlines. `SLACK` is
the measuring side's allowance: the lifeline thread's read, the wakeups and the reap.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import stat
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from moxi import proc
from moxi.paths import Layout
from moxi_dev.testing import short_dir, wait_for

from moxi_loopback.check import proof
from moxi_loopback.runtime import read_runtime, runtime_path
from moxi_loopback.serve import CLOSE_DEADLINE, EXIT_DEADLINE, LOCK_WAIT, PROMISE
from moxi_loopback.transport import call

posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals or Unix sockets")

SLACK = 0.25
MODULE = "prun"

PAGE = """
from moxi_loopback.routes import Field, Out, Page, Route

H = "moxi_prun.handlers"
PAGE = Page(
    routes=(
        Route("/api/echo", "read", H + ":echo", query={"n": Field("int", required=False)},
              returns={"n": Out("int")}),
        Route("/api/where", "read", H + ":where",
              returns={"cwd": Out("string"), "package": Out("string")}),
        Route("/api/print", "read", H + ":printing", returns={}),
        Route("/api/sleep", "act", H + ":sleep", returns={}),
        Route("/api/block", "read", H + ":block", returns={}),
        Route("/api/slow", "read", H + ":slow", query={"ms": Field("int")}, returns={}),
        Route("/api/events", "read", H + ":events", stream=True,
              topics={"tick": Out("table", items={"n": Out("int")})}),
    ),
    health=H + ":health",
)
"""

HANDLERS = """
import asyncio
import os
import time

import moxi_loopback
from moxi_loopback.server import Topics

TOPICS = Topics("tick")


def echo(call):
    return {"n": call.query.get("n", 0)}


def where(call):
    return {"cwd": os.getcwd(), "package": os.path.dirname(moxi_loopback.__file__)}


def printing(call):
    print("printed by a handler", flush=True)
    return {}


def sleep(call):
    time.sleep(10)
    return {}


async def block(call):
    time.sleep(10)
    return {}


async def slow(call):
    await asyncio.sleep(call.query["ms"] / 1000)
    return {}


def events(call):
    TOPICS.publish("tick", {"n": 1})
    return call.stream(TOPICS)


def health():
    return (True, "fine")
"""

ANNOUNCEMENT = {
    "INTERFACES": "frozenset({1})", "API": "None", "CLI": "None",
    "SUMMARY": "'A module made for a test.'", "OPTIONAL": "{}", "GATED": "{}",
    "INTERNAL_ENV": "{}", "SETTINGS": "None", "SETUP": "None", "SEGMENT": "None",
}


def _module(root: Path, name: str, files: dict[str, str], page: str | None, *,
            version: str | None = "0.1.0") -> None:
    package = root / f"moxi_{name}"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    fields = dict(ANNOUNCEMENT)
    if page is not None:
        fields["PAGE"] = repr(page)
    (package / "module.py").write_text("".join(f"{k} = {v}\n" for k, v in fields.items()))
    for relpath, text in files.items():
        (package / relpath).write_text(textwrap.dedent(text))
    info = root / (f"moxi_{name}-{version}.dist-info" if version else f"moxi_{name}.dist-info")
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: moxi-{name}\n"
                                   + (f"Version: {version}\n" if version else ""))
    (info / "entry_points.txt").write_text(f"[moxi.modules]\n{name} = moxi_{name}.module\n")


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("runner-site")
    _module(root, MODULE, {"page.py": PAGE, "handlers.py": HANDLERS}, "moxi_prun.page:PAGE")
    _module(root, "pnopage", {}, None)
    _module(root, "pnoversion",
            {"page.py": PAGE.replace('H = "moxi_prun.handlers"', 'H = "moxi_pnoversion.handlers"'),
             "handlers.py": HANDLERS},
            "moxi_pnoversion.page:PAGE", version=None)
    _module(root, "pbadhandler",
            {"page.py": PAGE.replace('H = "moxi_prun.handlers"', 'H = "moxi_pbadhandler.gone"')},
            "moxi_pbadhandler.page:PAGE")
    return root


@pytest.fixture
def cwd(tmp_path):
    """A working directory only this user can write, as every spawner passes."""
    path = tmp_path / "cwd"
    path.mkdir(mode=0o700)
    return path


class Runner:
    """One spawned runner, its stdout a pipe and its stderr a file."""

    def __init__(self, site, cwd, log, *args, module=MODULE, safe=True, env=None):
        argv = [sys.executable, *(["-P"] if safe else []), "-m", "moxi_loopback.serve",
                module, *args]
        environ = {**os.environ, "PYTHONPATH": str(site),
                   f"MOXI_{module.upper()}_LOG_LEVEL": "info", **(env or {})}
        self.log = log
        self._err = open(log, "ab")
        self.proc = proc.spawn(argv, lifeline="stdin", cwd=cwd, env=environ,
                               stdout=subprocess.PIPE, stderr=self._err)
        self.server_id = None

    def ready(self, timeout=10.0):
        """The server_id of the ready line, or None if stdout closed without one."""
        line = []
        reader = threading.Thread(target=lambda: line.append(self.proc.stdout.readline()),
                                  daemon=True)
        reader.start()
        reader.join(timeout)
        assert line, f"no ready line within {timeout} s:\n{self.stderr()}"
        text = line[0].decode()
        if not text:
            return None
        assert text.startswith("ready ") and text.endswith("\n"), text
        self.server_id = text.split()[1]
        return self.server_id

    def close_lifeline(self):
        self.proc.lifeline.close()
        return time.monotonic()

    def signal(self, number):
        self.proc.send_signal(number)
        return time.monotonic()

    def exit(self, timeout):
        """The exit code and the moment it was seen, waiting at most `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while (code := self.proc.poll()) is None:
            if time.monotonic() >= deadline:
                raise AssertionError(f"still running after {timeout} s:\n{self.stderr()}")
            time.sleep(0.005)
        return code, time.monotonic()

    def stderr(self):
        self._err.flush()
        return Path(self.log).read_text(errors="replace")

    def end(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)
        self.proc.stdout.close()
        self._err.close()


@pytest.fixture
def spawn(site, cwd, tmp_path):
    started = []

    def start(*args, **kwargs):
        log = tmp_path / f"runner-{len(started)}.log"
        runner = Runner(site, kwargs.pop("cwd", cwd), log, *args, **kwargs)
        started.append(runner)
        return runner

    yield start
    for runner in started:
        runner.end()


@pytest.fixture
def served(spawn):
    """A runner that has printed its ready line, and its runtime file."""
    runner = spawn()
    assert runner.ready()
    info = read_runtime(MODULE)
    assert info is not None and info.server_id == runner.server_id
    return runner, info


# --- a raw client ---------------------------------------------------------------------


def connect(info):
    if info.transport == "unix":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(info.address)
    else:
        sock = socket.create_connection((info.address, info.port))
    sock.settimeout(5)
    return sock


def host(info):
    return "localhost" if info.transport == "unix" else f"127.0.0.1:{info.port}"


def send(sock, info, method, path, *, token=True, body=None, headers=()):
    lines = [f"{method} {path} HTTP/1.1", f"Host: {host(info)}"]
    if token:
        lines.append(f"X-Moxi-Token: {info.token}")
    payload = b""
    if body is not None:
        payload = json.dumps(body).encode()
        lines += ["Content-Type: application/json", f"Content-Length: {len(payload)}"]
    lines += [f"{name}: {value}" for name, value in headers]
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + payload)


def response(sock):
    """(status, headers, body) of one response, or None when the connection closed
    before any byte. An event stream's body is what has arrived so far."""
    data = b""
    while b"\r\n\r\n" not in data:
        try:
            chunk = sock.recv(65536)
        except ConnectionResetError:
            chunk = b""
        if not chunk:
            assert not data, f"a response cut short: {data!r}"
            return None
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    while "content-length" in headers and len(body) < length:
        body += sock.recv(65536)
    return int(lines[0].split()[1]), headers, body


def prove(sock, info):
    """Prove the server on `sock`; the moment the answer was read."""
    nonce = secrets.token_urlsafe(32)
    send(sock, info, "GET", "/api/prove", token=False, headers=[("X-Moxi-Nonce", nonce)])
    status, headers, _ = response(sock)
    assert status == 204
    assert headers["x-moxi-proof"] == proof(info.token, info.server_id, nonce)
    return time.monotonic()


def closed_at(sock, timeout=5.0):
    """The moment the server closed `sock` with nothing more sent."""
    sock.settimeout(timeout)
    try:
        data = sock.recv(65536)
    except ConnectionResetError:
        data = b""
    assert data == b"", f"the server sent {data!r}"
    return time.monotonic()


def refused(info):
    try:
        connect(info).close()
    except OSError:
        return True
    return False


def wait_refused(info, timeout=0.08):
    """Wait until a new connect is refused: the decision has been taken on the loop."""
    wait_for(lambda: refused(info), timeout, interval=0.002, what="the listener's close")


def wait_open_requests(info, count):
    wait_for(lambda: call(info, "GET", "/api/health")[1]["open"]["requests"] >= count, 3,
             interval=0.02, what="the request in the app")


def files_gone(info):
    return not runtime_path(MODULE).exists() and not (
        info.transport == "unix" and os.path.exists(info.address))


# --- start ----------------------------------------------------------------------------


@posix
def test_the_ready_line_and_the_runtime_file(served):
    runner, info = served
    path = runtime_path(MODULE)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert info.pid == runner.proc.pid and info.transport == "unix"
    assert info.port is None and stat.S_ISSOCK(os.stat(info.address).st_mode)
    assert info.version == "0.1.0"
    status, health = call(info, "GET", "/api/health")
    assert status == 200
    assert (health["module"], health["server_id"], health["version"]) == (
        MODULE, info.server_id, "0.1.0")
    assert health["check"] == [True, "fine"]
    assert call(info, "GET", "/api/echo?n=4") == (200, {"n": 4})
    assert info.token not in runner.stderr()


@posix
def test_the_lifeline_closing_stops_the_server_with_exit_0_and_both_files_gone(served):
    runner, info = served
    t0 = runner.close_lifeline()
    code, t = runner.exit(EXIT_DEADLINE + 2)
    assert code == 0 and t - t0 <= EXIT_DEADLINE + SLACK
    assert files_gone(info)
    assert "stopping: the lifeline closed" in runner.stderr()


@posix
def test_sigterm_stops_the_server_with_exit_0_and_both_files_gone(served):
    runner, info = served
    t0 = runner.signal(signal.SIGTERM)
    code, t = runner.exit(EXIT_DEADLINE + 2)
    assert code == 0 and t - t0 <= EXIT_DEADLINE + SLACK
    assert files_gone(info)


def test_tcp_serves_on_127_0_0_1_and_stops_on_the_lifeline(spawn):
    runner = spawn("--tcp")
    assert runner.ready()
    info = read_runtime(MODULE)
    assert info.transport == "tcp" and info.address == "127.0.0.1"
    assert isinstance(info.port, int) and info.port > 0
    assert call(info, "GET", "/api/echo?n=2") == (200, {"n": 2})
    runner.close_lifeline()
    assert runner.exit(EXIT_DEADLINE + 2)[0] == 0
    assert files_gone(info)


@posix
def test_idle_exit_at_idle_s_unlinks_both_files(spawn):
    runner = spawn("--idle-s", "0.5")
    assert runner.ready()
    ready = time.monotonic()
    info = read_runtime(MODULE)
    code, t = runner.exit(5)
    assert code == 0 and t - ready <= 0.5 + 1.0
    assert files_gone(info)
    assert "stopping: idle for 0.5 s" in runner.stderr()


@posix
def test_the_idle_run_starts_when_serving_starts_not_while_the_lock_is_awaited(spawn):
    from moxi import files

    runtime = Layout(MODULE).runtime_dir(create=True)
    with files.lock(runtime / "page.lock"):
        runner = spawn("--idle-s", "0.5")
        time.sleep(2.0)
        freed = time.monotonic()
    assert runner.ready(LOCK_WAIT + 5)
    ready = time.monotonic()
    assert ready >= freed
    info = read_runtime(MODULE)
    # The 2 s spent waiting for the lock count for nothing: half the idle run after the
    # ready line, the server still takes a connection.
    time.sleep(max(0.0, ready + 0.25 - time.monotonic()))
    assert not refused(info), "the server stopped before its idle run was out"
    code, t = runner.exit(5)
    assert code == 0 and t - ready >= 0.5 - SLACK
    assert "stopping: idle for 0.5 s" in runner.stderr()


@posix
def test_a_print_from_a_handler_after_the_ready_line_goes_to_stderr(served):
    runner, info = served
    assert call(info, "GET", "/api/print") == (200, {})
    runner.close_lifeline()
    assert runner.exit(EXIT_DEADLINE + 2)[0] == 0
    assert runner.proc.stdout.read() == b""
    assert "printed by a handler" in runner.stderr()


@posix
def test_a_second_instance_exits_75(served, spawn):
    first, info = served
    second = spawn()
    assert second.ready(LOCK_WAIT + 5) is None
    assert second.exit(5)[0] == 75
    assert read_runtime(MODULE).server_id == info.server_id
    assert call(info, "GET", "/api/echo") == (200, {"n": 0})


@posix
def test_a_killed_servers_files_are_recovered_by_the_next_start(served, spawn):
    runner, info = served
    runner.proc.kill()
    runner.proc.wait(5)
    assert runtime_path(MODULE).exists() and os.path.exists(info.address)
    second = spawn()
    assert second.ready()
    fresh = read_runtime(MODULE)
    assert fresh.server_id == second.server_id != info.server_id
    assert fresh.token != info.token
    assert call(fresh, "GET", "/api/echo?n=1") == (200, {"n": 1})


# --- the stop -------------------------------------------------------------------------


@posix
def test_a_request_promised_by_a_proof_is_served_after_the_decision(served):
    runner, info = served
    sock = connect(info)
    proved = prove(sock, info)
    t0 = runner.close_lifeline()
    wait_refused(info)
    time.sleep(max(0.0, proved + 0.1 - time.monotonic()))
    send(sock, info, "GET", "/api/echo?n=7")
    status, headers, body = response(sock)
    assert (status, json.loads(body)) == (200, {"n": 7})
    assert headers["connection"] == "close"
    closed_at(sock)
    code, t = runner.exit(EXIT_DEADLINE + 2)
    assert code == 0 and t - t0 <= EXIT_DEADLINE + SLACK
    assert files_gone(info)


@posix
def test_a_request_sent_after_the_promise_window_gets_no_answer(served):
    runner, info = served
    sock = connect(info)
    proved = prove(sock, info)
    runner.close_lifeline()
    wait_refused(info)
    time.sleep(max(0.0, proved + PROMISE + 0.1 - time.monotonic()))
    try:
        send(sock, info, "GET", "/api/echo?n=7")
    except (BrokenPipeError, ConnectionResetError):
        pass
    assert response(sock) is None
    assert runner.exit(EXIT_DEADLINE + 2)[0] == 0


@posix
def test_a_proof_followed_by_nothing_or_half_a_head_is_closed_when_its_promise_lapses(served):
    runner, info = served
    bare, half = connect(info), connect(info)
    proved_bare = prove(bare, info)
    proved_half = prove(half, info)
    half.sendall(f"GET /api/echo HTTP/1.1\r\nHost: {host(info)}\r\n".encode())
    runner.close_lifeline()
    assert closed_at(bare) - proved_bare <= PROMISE + SLACK
    assert closed_at(half) - proved_half <= PROMISE + SLACK
    assert runner.exit(EXIT_DEADLINE + 2)[0] == 0


@posix
def test_streams_end_at_the_close_deadline_and_the_server_exits_0(served):
    runner, info = served
    held = connect(info)
    prove(held, info)
    send(held, info, "GET", "/api/events")
    status, headers, body = response(held)
    assert status == 200 and headers["content-type"].startswith("text/event-stream")
    late = connect(info)
    prove(late, info)

    t0 = runner.close_lifeline()
    wait_refused(info)
    # A stream opened on a just-proved connection after the decision is the promised
    # request: it is served, with Connection: close.
    send(late, info, "GET", "/api/events")
    status, headers, _ = response(late)
    assert status == 200 and headers["connection"] == "close"
    for sock in (held, late):
        sock.settimeout(5)
        while sock.recv(65536):
            pass
        assert time.monotonic() - t0 <= CLOSE_DEADLINE + SLACK
    code, t = runner.exit(EXIT_DEADLINE + 2)
    assert code == 0 and t - t0 <= EXIT_DEADLINE + SLACK
    assert files_gone(info)


@posix
def test_a_blocked_def_act_is_cut_at_the_close_deadline_and_a_start_takes_the_lock(
        served, spawn):
    runner, info = served
    sock = connect(info)
    prove(sock, info)
    send(sock, info, "POST", "/api/sleep", body={})
    wait_open_requests(info, 1)
    t0 = runner.signal(signal.SIGTERM)
    successor = spawn()
    assert closed_at(sock) - t0 <= CLOSE_DEADLINE + SLACK  # With no response byte.
    code, t = runner.exit(EXIT_DEADLINE + 2)
    assert code == 0 and t - t0 <= EXIT_DEADLINE + SLACK
    # The start made at the decision waited for the lock and did not exit 75.
    assert successor.ready(LOCK_WAIT + 5) is not None
    assert read_runtime(MODULE).server_id == successor.server_id


@posix
def test_a_loop_blocked_past_the_exit_deadline_meets_the_watchdog(served, spawn):
    runner, info = served
    sock = connect(info)
    prove(sock, info)
    send(sock, info, "GET", "/api/block")
    time.sleep(0.3)  # The handler now holds the loop for 10 s.
    t0 = runner.close_lifeline()
    time.sleep(1.0)
    runner.signal(signal.SIGTERM)  # A second trigger leaves the clock where it was.
    code, t = runner.exit(EXIT_DEADLINE + 2)
    assert code == 1 and t - t0 <= EXIT_DEADLINE + SLACK
    assert "passed its exit deadline" in runner.stderr()
    # Its files are a crash's, and the next start recovers them.
    assert runtime_path(MODULE).exists()
    successor = spawn()
    assert successor.ready() is not None
    assert read_runtime(MODULE).server_id == successor.server_id
    assert call(read_runtime(MODULE), "GET", "/api/echo") == (200, {"n": 0})


@posix
def test_a_pooled_kept_alive_connection_is_closed_at_the_decision(served):
    runner, info = served
    sock = connect(info)
    prove(sock, info)
    send(sock, info, "GET", "/api/echo?n=1")
    assert response(sock)[0] == 200
    t0 = runner.close_lifeline()
    assert closed_at(sock) - t0 <= SLACK
    try:
        send(sock, info, "GET", "/api/echo?n=2")
    except (BrokenPipeError, ConnectionResetError):
        pass
    assert response(sock) is None
    assert runner.exit(EXIT_DEADLINE + 2)[0] == 0


@posix
def test_after_the_decision_a_proof_gets_503_and_a_new_connect_is_refused(served):
    runner, info = served
    sock = connect(info)
    prove(sock, info)
    runner.close_lifeline()
    wait_refused(info)
    nonce = secrets.token_urlsafe(32)
    send(sock, info, "GET", "/api/prove", token=False, headers=[("X-Moxi-Nonce", nonce)])
    status, headers, _ = response(sock)
    assert status == 503 and headers["connection"] == "close"
    assert "x-moxi-proof" not in headers
    closed_at(sock)
    assert runner.exit(EXIT_DEADLINE + 2)[0] == 0


@posix
def test_a_respawn_while_a_stream_is_held_takes_the_lock(served, spawn):
    runner, info = served
    stream = connect(info)
    prove(stream, info)
    send(stream, info, "GET", "/api/events")
    assert response(stream)[0] == 200
    runner.close_lifeline()
    successor = spawn()
    assert successor.ready(LOCK_WAIT + 5) is not None
    assert runner.exit(EXIT_DEADLINE + 2)[0] == 0
    assert read_runtime(MODULE).server_id == successor.server_id


# --- the idle count -------------------------------------------------------------------


@posix
def test_probes_proofs_and_requests_without_the_token_do_not_keep_the_server_up(spawn):
    runner = spawn("--idle-s", "0.5")
    assert runner.ready()
    ready = time.monotonic()
    info = read_runtime(MODULE)
    done = threading.Event()

    def traffic():
        while not done.is_set():
            for kind in ("health", "proof", "tokenless"):
                try:
                    if kind == "health":
                        call(info, "GET", "/api/health", timeout=1)
                    else:
                        with connect(info) as sock:
                            if kind == "proof":
                                prove(sock, info)
                            else:
                                send(sock, info, "GET", "/api/echo", token=False)
                                response(sock)
                except (OSError, AssertionError):
                    return
                time.sleep(0.2 / 3)

    sender = threading.Thread(target=traffic, daemon=True)
    sender.start()
    try:
        code, t = runner.exit(5)
    finally:
        done.set()
        sender.join(5)
    assert code == 0 and t - ready <= 0.5 + 1.0


@posix
def test_a_counted_request_open_across_the_idle_look_holds_the_decision(spawn):
    runner = spawn("--idle-s", "0.5")
    assert runner.ready()
    info = read_runtime(MODULE)
    assert call(info, "GET", "/api/slow?ms=1200") == (200, {})
    ended = time.monotonic()
    code, t = runner.exit(5)
    assert code == 0 and t - ended >= 0.5 - 0.1


# --- where it runs, and what it imports -------------------------------------------------


@posix
def test_from_a_fresh_moxi_home_it_runs_in_its_own_runtime_dir(spawn, monkeypatch):
    with short_dir() as short:
        home = short / "home"
        shell = short / "shell"
        shell.mkdir(mode=0o700)
        monkeypatch.setenv("MOXI_HOME", str(home))
        runner = spawn(cwd=shell, env={"MOXI_HOME": str(home)})
        assert runner.ready()
        runtime = Layout(MODULE).runtime_dir()
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
        info = read_runtime(MODULE)
        assert info is not None and info.address.startswith(str(home))
        status, where = call(info, "GET", "/api/where")
        assert status == 200 and Path(where["cwd"]) == runtime
        runner.close_lifeline()
        assert runner.exit(EXIT_DEADLINE + 2)[0] == 0


def _plant(directory: Path, package: str, marker: Path) -> None:
    (directory / package).mkdir()
    (directory / package / "__init__.py").write_text(
        f"open({str(marker)!r}, 'a').write({package!r} + '\\n')\n")


@posix
def test_a_planted_moxi_loopback_in_the_working_directory_is_never_imported(spawn, cwd,
                                                                           tmp_path):
    marker = tmp_path / "imported"
    _plant(cwd, "moxi_loopback", marker)
    (cwd / "moxi_loopback" / "serve.py").write_text(
        f"open({str(marker)!r}, 'a').write('serve\\n')\n")
    runner = spawn()
    assert runner.ready()
    info = read_runtime(MODULE)
    status, where = call(info, "GET", "/api/where")
    assert status == 200 and not where["package"].startswith(str(cwd))
    assert not marker.exists()


def test_started_without_safe_path_it_exits_3_before_importing_anything(spawn, cwd,
                                                                       tmp_path):
    marker = tmp_path / "imported"
    for package in ("uvicorn", "starlette", "moxi"):
        _plant(cwd, package, marker)
    runner = spawn(safe=False)
    assert runner.ready() is None
    assert runner.exit(10)[0] == 3
    assert "python -P" in runner.stderr()
    assert not marker.exists(), marker.read_text()


# --- refusals -------------------------------------------------------------------------


@pytest.mark.parametrize("module, words", [
    ("pnopage", "declares no PAGE"),
    ("pbadhandler", "cannot be loaded"),
    ("pmissing", "absent"),
    ("pnoversion", "has no version"),
])
def test_a_page_it_cannot_serve_exits_3_and_writes_nothing(spawn, module, words):
    runner = spawn(module=module)
    assert runner.ready() is None
    assert runner.exit(10)[0] == 3
    assert words in runner.stderr()
    assert not runtime_path(module).exists()


@posix
def test_a_runtime_dir_that_is_a_symlink_exits_3(spawn, tmp_path):
    runtime = Layout(MODULE).runtime_dir()
    runtime.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "elsewhere"
    target.mkdir(mode=0o700)
    runtime.symlink_to(target)
    runner = spawn()
    assert runner.ready() is None
    assert runner.exit(10)[0] == 3
    assert not (target / "page.json").exists()


@pytest.mark.parametrize("argv", [[], ["prun", "--idle-s", "0"], ["prun", "--idle-s", "nan"],
                                  ["prun", "--bogus"]])
def test_usage_exits_2(argv, capsys):
    from moxi_loopback.serve import main

    with pytest.raises(SystemExit) as stopped:
        main(argv)
    assert stopped.value.code == 2


def test_a_name_that_is_not_a_module_name_is_usage(capsys):
    from moxi_loopback.serve import main

    assert main(["Not-A-Module"]) == 2
