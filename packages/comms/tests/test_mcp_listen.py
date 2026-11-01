"""The port of comms' tools: the socket the supervisor holds (systemd's `LISTEN_FDS`,
launchd's `Listeners` key through a fake `launch_activate_socket`), else the daemon's own
bind; port-taken only on a live listener, `bind-failed` after the retries, no false
port-taken, the settle before any bind is answered, the stop order, the doctor's rows and
the idle cost. Loopback on ephemeral ports only; never the real `[mcp] port`, user
manager or launchd."""

from __future__ import annotations

import errno
import functools
import http.client
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from conftest import write_config
from test_mcp_endpoint import (  # noqa: F401  (fixtures)
    INITIALIZE,
    endpoint,
    post,
    result_of,
    rows,
    serving,
    sessions,
    view,
)

from moxi_comms import checks
from moxi_comms.client import DaemonClient
from moxi_comms.mcp import listen
from moxi_comms.paths import daemon_socket

LINUX = sys.platform.startswith("linux")
#: The real read of comms' socket unit, kept before the rig's stub replaces it.
REAL_UNIT_READ = listen.socket_unit_state
#: Where the tests put a passed descriptor, as systemd puts its first at 3.
FIRST = 200


def listener(port: int = 0) -> socket.socket:
    """A listening socket on 127.0.0.1, as the supervisor would hold one."""
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(16)
    return sock


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def blocker() -> socket.socket:
    """A socket bound to a port but not listening, without `SO_REUSEADDR`: a bind beside
    it fails, and a connect to it is refused."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock


@pytest.fixture
def passed(monkeypatch):
    """Put sockets where systemd would pass them, and give the environment that names
    them to this process."""
    placed: list[int] = []
    monkeypatch.setattr(listen, "_FIRST_FD", FIRST)

    def place(*socks: socket.socket) -> dict:
        for k, sock in enumerate(socks):
            os.dup2(sock.fileno(), FIRST + k)
            placed.append(FIRST + k)
        return {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": str(len(socks)),
                "LISTEN_FDNAMES": ":".join("moxi-comms-daemon.socket" for _ in socks),
                "OTHER": "kept"}

    yield place
    for fd in placed:
        try:
            os.close(fd)
        except OSError:
            pass


def fake_launchd(table: dict):
    """A fake `launch_activate_socket`: `table` maps a key to its descriptors, or to an
    errno; a key not in it is ENOENT."""
    asked = []

    def activate(key):
        asked.append(key)
        found = table.get(key, errno.ENOENT)
        return (found, []) if isinstance(found, int) else (0, list(found))

    activate.asked = asked
    return activate


class Clock:
    """A clock that `sleep` moves."""

    def __init__(self):
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def ask_daemon(method: str, params=None):
    client = DaemonClient(daemon_socket()).connect()
    try:
        return client.call(method, params or {})
    finally:
        client.close()


# ==================================================================== the bind

def test_the_own_bind_is_loopback_non_blocking_and_reuses_addresses():
    sock = listen.bind(0)
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
        assert sock.getblocking() is False
        if os.name != "nt":
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
    finally:
        sock.close()


def _time_wait_on(port: int) -> bool:
    want = f"0100007F:{port:04X}"
    with open("/proc/net/tcp") as f:
        return any(line.split()[1] == want and line.split()[3] == "06"
                   for line in f.read().splitlines()[1:])


@pytest.mark.skipif(not LINUX, reason="reads the kernel's TCP table for TIME_WAIT")
def test_time_wait_sockets_left_by_an_earlier_listener_dont_refuse_the_bind():
    first = listen.bind(0)
    first.setblocking(True)
    port = first.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = first.accept()
    conn.close()                                       # the server closes first
    client.recv(1)
    client.close()
    first.close()
    assert _time_wait_on(port)
    again = listen.take(port)
    try:
        assert again.why is None and again.sock.getsockname()[1] == port
    finally:
        again.sock.close()


# ==================================================================== a failed bind

def test_a_live_listener_on_the_port_is_port_taken_with_its_uid(unit_read):
    unit_read.state = "missing"
    with listener() as holder:
        port = holder.getsockname()[1]
        clock = Clock()
        got = listen.take(port, environ={}, sleep=clock.sleep, clock=clock)
    assert got.sock is None and got.why == "port-taken"
    assert clock.slept == [] and unit_read.asked == 1
    assert f"127.0.0.1:{port}" in got.detail
    if LINUX:
        assert got.holder_uid == os.getuid()
        assert f"uid {os.getuid()}" in got.detail


def test_a_port_bound_but_not_listening_retries_every_half_second_for_five_seconds():
    with blocker() as held:
        port = held.getsockname()[1]
        clock = Clock()
        got = listen.take(port, environ={}, sleep=clock.sleep, clock=clock)
    assert got.sock is None and got.why == "bind-failed"
    assert clock.slept == [0.5] * 10 and clock.now == 105.0
    assert f"127.0.0.1:{port}" in got.detail and got.holder_uid is None


def test_a_listener_that_appears_while_the_binds_retry_is_port_taken(unit_read):
    unit_read.state = "inactive"
    held = blocker()
    port = held.getsockname()[1]
    clock = Clock()
    late = []

    def sleep(seconds):
        clock.sleep(seconds)
        if len(clock.slept) == 2:
            held.close()
            late.append(socket.socket())
            late[0].bind(("127.0.0.1", port))
            late[0].listen(1)

    try:
        got = listen.take(port, environ={}, sleep=sleep, clock=clock)
    finally:
        held.close()
        for s in late:
            s.close()
    assert got.why == "port-taken" and len(clock.slept) == 2
    assert unit_read.asked == 1                     # read once, before the first connect


def test_a_port_freed_while_the_binds_retry_is_bound():
    held = blocker()
    port = held.getsockname()[1]
    clock = Clock()

    def sleep(seconds):
        clock.sleep(seconds)
        held.close()

    got = listen.take(port, environ={}, sleep=sleep, clock=clock)
    try:
        assert got.why is None and got.sock.getsockname()[1] == port
        assert not got.inherited and clock.slept == [0.5]
    finally:
        got.sock.close()


def test_a_bind_that_succeeds_reads_no_unit(unit_read):
    got = listen.take(0, environ={})
    try:
        assert got.why is None and unit_read.asked == 0
    finally:
        got.sock.close()


def test_an_active_socket_unit_is_supervised_and_nothing_connects(unit_read, monkeypatch):
    unit_read.state = "active"
    probed = []
    monkeypatch.setattr(listen, "answers", lambda *a, **k: probed.append(a) or True)
    with listener() as unit:
        unit.setblocking(False)
        port = unit.getsockname()[1]
        clock = Clock()
        got = listen.take(port, environ={}, sleep=clock.sleep, clock=clock)
        # Nothing connected to the unit's socket: a connect would start its service.
        with pytest.raises(BlockingIOError):
            unit.accept()
    assert got.sock is None and got.why == "supervised" and got.holder_uid is None
    assert probed == [] and clock.slept == [] and unit_read.asked == 1
    assert f"127.0.0.1:{port}" in got.detail and "moxi install comms" in got.detail


def test_a_listener_with_the_unit_unread_and_this_users_uid_is_bind_failed(unit_read):
    unit_read.state = "unknown"
    with listener() as holder:
        port = holder.getsockname()[1]
        clock = Clock()
        got = listen.take(port, environ={}, sleep=clock.sleep, clock=clock)
    assert got.sock is None and got.why == "bind-failed" and clock.slept == []
    assert "whether it is comms' own socket unit couldn't be read" in got.detail
    assert f"127.0.0.1:{port}" in got.detail


def test_a_listener_of_another_user_is_port_taken_with_the_unit_unread(unit_read,
                                                                        monkeypatch):
    unit_read.state = "unknown"
    other = (os.getuid() + 1) if hasattr(os, "getuid") else 1
    monkeypatch.setattr(listen, "holder_uid", lambda port, *a: other)
    monkeypatch.setattr(listen, "_another_user", lambda uid: uid == other)
    with listener() as holder:
        got = listen.take(holder.getsockname()[1], environ={})
    assert got.why == "port-taken" and got.holder_uid == other
    assert f"uid {other}" in got.detail


@pytest.mark.parametrize("state", sorted(listen.NOT_HOLDING))
def test_each_state_that_shows_the_unit_not_holding_lets_a_listener_be_port_taken(
        unit_read, state):
    unit_read.state = state
    with listener() as holder:
        got = listen.take(holder.getsockname()[1], environ={})
    assert got.why == "port-taken"


def row(name, kind, state):
    return SimpleNamespace(module="comms", name=name, kind=kind, state=state)


def test_the_unit_read_is_the_daemons_socket_row():
    asked = []

    def status(module):
        asked.append(module)
        return [row("daemon", "daemon", "inactive"), row("daemon", "socket", "active")]

    assert REAL_UNIT_READ(status=status) == "active"
    assert asked == ["comms"]
    assert REAL_UNIT_READ(status=lambda m: [row("daemon", "daemon", "active")]) == "unknown"
    assert REAL_UNIT_READ(status=lambda m: [row("daemon", "socket", "missing")]) == "missing"


def test_a_unit_read_that_raises_or_hangs_is_unknown():
    def raises(module):
        raise RuntimeError("systemctl failed")

    assert REAL_UNIT_READ(status=raises) == "unknown"
    release = threading.Event()

    def hangs(module):
        release.wait(10)
        return [row("daemon", "socket", "active")]

    began = time.monotonic()
    try:
        assert REAL_UNIT_READ(0.2, status=hangs) == "unknown"
        assert time.monotonic() - began < 2.0
    finally:
        release.set()


def test_the_holders_uid_is_read_from_the_kernels_table(tmp_path):
    table = tmp_path / "tcp"
    head = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
            "   uid  timeout inode\n")
    table.write_text(
        head
        + "   0: 0100007F:2104 00000000:0000 06 00000000:00000000 00:00000000 00000000"
          "  1000        0 1 1\n"
        + "   1: 0200007F:2102 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
          "  1001        0 2 1\n"
        + "   2: 00000000:2102 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
          "  1002        0 3 1\n")
    assert listen.holder_uid(0x2102, [str(table)]) == 1002
    assert listen.holder_uid(0x2104, [str(table)]) is None      # TIME_WAIT, not a listener
    assert listen.holder_uid(0x2102, [str(tmp_path / "none")]) is None


# ==================================================================== inherited

def test_systemds_socket_is_taken_and_its_variables_removed(passed):
    with listener() as unit:
        environ = passed(unit)
        sock = listen.inherited(environ=environ, platform="linux")
        try:
            assert sock.getsockname() == unit.getsockname()
            assert sock.getblocking() is False and not sock.get_inheritable()
        finally:
            sock.close()
    assert environ == {"OTHER": "kept"}


def test_systemds_variables_for_another_process_pass_nothing_and_are_removed(passed):
    with listener() as unit:
        environ = passed(unit)
        environ["LISTEN_PID"] = str(os.getpid() + 1)
        assert listen.inherited(environ=environ, platform="linux") is None
    assert environ == {"OTHER": "kept"}


def test_take_serves_an_inherited_socket_on_another_port_than_the_setting(passed):
    with listener() as unit:
        other = free_port()
        got = listen.take(other, environ=passed(unit), platform="linux")
        try:
            assert got.why is None and got.inherited
            assert got.sock.getsockname()[1] == unit.getsockname()[1] != other
            assert "supervisor" in got.detail
        finally:
            got.sock.close()


@pytest.mark.parametrize("kind", ["udp", "not-listening", "two"])
def test_a_passed_socket_that_isnt_one_loopback_listener_is_failed(passed, kind):
    socks = {"udp": lambda: [socket.socket(socket.AF_INET, socket.SOCK_DGRAM)],
             "not-listening": lambda: [blocker()],
             "two": lambda: [listener(), listener()]}[kind]()
    if kind == "udp":
        socks[0].bind(("127.0.0.1", 0))
    try:
        got = listen.take(1, environ=passed(*socks), platform="linux")
    finally:
        for s in socks:
            s.close()
    assert got.sock is None and got.why == "failed" and "supervisor" in got.detail
    for k in range(len(socks)):
        with pytest.raises(OSError):
            os.fstat(FIRST + k)                        # every passed descriptor closed


def test_launchds_listeners_key_is_taken_on_macos():
    with listener() as job:
        fd = os.dup(job.fileno())
        activate = fake_launchd({"Listeners": [fd]})
        got = listen.take(1, environ={}, activate=activate, platform="darwin")
        try:
            assert got.inherited and got.sock.getsockname() == job.getsockname()
            assert activate.asked == ["Listeners"]
        finally:
            got.sock.close()


@pytest.mark.parametrize("code", [errno.ESRCH, errno.ENOENT])
def test_a_process_launchd_didnt_start_binds_its_own(code):
    activate = fake_launchd({"Listeners": code})
    got = listen.take(0, environ={}, activate=activate, platform="darwin")
    try:
        assert got.why is None and not got.inherited
    finally:
        got.sock.close()


def test_a_launchd_socket_that_cant_be_taken_is_failed():
    activate = fake_launchd({"Listeners": errno.EALREADY})
    got = listen.take(0, environ={}, activate=activate, platform="darwin")
    assert got.why == "failed" and "Listeners" in got.detail


def test_launchd_is_never_asked_off_macos():
    activate = fake_launchd({})
    got = listen.take(0, environ={}, activate=activate, platform="linux")
    got.sock.close()
    assert activate.asked == []


def test_the_listen_module_imports_no_mcp_library():
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys, moxi_comms.mcp.listen; "
         "print(sorted(m for m in ('mcp', 'uvicorn', 'starlette') if m in sys.modules))"],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# ==================================================================== the daemon

def test_the_daemon_serves_an_inherited_socket_on_another_port_and_revokes_nothing(
        serving, sessions, passed):
    serving()
    s = sessions()
    s.bind()
    unit = listener()
    configured = free_port()
    write_config(None, f"[mcp]\nport = {configured}\n")
    environ = passed(unit)
    daemon = serving(port=None, mcp_listen=functools.partial(
        listen.take, environ=environ, platform="linux"))
    port = unit.getsockname()[1]
    try:
        answer = ask_daemon("mcp_endpoint")
        assert answer["served"] and answer["held"]
        assert answer["url"] == f"http://127.0.0.1:{port}/mcp"
        assert daemon.mcp_endpoint.port == port != configured
        assert [r["revoked_at"] for r in rows()] == [None]
        assert result_of(post(port, INITIALIZE, token=s.token))
        config = type("Mcp", (), {"enabled": True, "port": configured})()
        assert checks.endpoint_row(answer, config).state == "ok"
        port_row = checks.port_row(answer, config)
        assert port_row.state == "warn" and str(port) in port_row.detail
    finally:
        daemon.stop()
        unit.close()


def test_the_supervisors_socket_holds_the_port_across_a_restart(serving, sessions, passed):
    """While the daemon is down the supervisor's copy keeps listening: a client that
    connects then waits in the backlog, and the next daemon answers it."""
    unit = listener()
    port = unit.getsockname()[1]
    take = functools.partial(listen.take, platform="linux")
    daemon = serving(mcp_listen=lambda p: take(p, environ=passed(unit)))
    s = sessions()
    s.bind()
    s.close()
    daemon.stop()
    waiting = socket.create_connection(("127.0.0.1", port), timeout=5)   # not refused
    try:
        daemon = serving(mcp_listen=lambda p: take(p, environ=passed(unit)))
        assert s.bind() == {"served": True}
        body = json.dumps(INITIALIZE).encode()
        waiting.sendall(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Accept: application/json, text/event-stream\r\n"
                        b"Authorization: Bearer %s\r\nContent-Length: %d\r\n"
                        b"Connection: close\r\n\r\n%s"
                        % (port, s.token.encode(), len(body), body))
        reply = b""
        while chunk := waiting.recv(65536):
            reply += chunk
        assert reply.startswith(b"HTTP/1.1 200")
    finally:
        waiting.close()
        daemon.stop()
        unit.close()


def test_port_taken_logs_one_line_naming_the_port_and_the_doctor_fails_it(
        serving, sessions, caplog, unit_read):
    unit_read.state = "missing"
    serving()
    first, second = sessions(), sessions(name="main")
    first.bind()
    second.bind()
    with listener() as holder:
        port = holder.getsockname()[1]
        with caplog.at_level(logging.ERROR, logger="comms.daemon"):
            serving(port=port)
        answer = ask_daemon("mcp_endpoint")
    assert answer["served"] is False and answer["why"] == "port-taken"
    assert sorted(r["revoked_why"] for r in rows()) == ["port-taken", "port-taken"]
    lines = [r.getMessage() for r in caplog.records if r.name == "comms.daemon"
             and r.levelno >= logging.ERROR]
    assert len(lines) == 1 and f"127.0.0.1:{port}" in lines[0] and "2 live" in lines[0]
    row = checks.endpoint_row(answer)
    assert row.state == "fail" and "revoked" in row.detail
    if LINUX:
        assert f"uid {os.getuid()}" in row.detail
    assert "mcp.port" in row.fix


def test_bind_failed_revokes_nothing_and_the_doctor_fails_it(serving, sessions):
    serving()
    s = sessions()
    s.bind()
    with blocker() as held:
        port = held.getsockname()[1]
        daemon = serving(port=port, mcp_listen=functools.partial(
            listen.take, environ={}, retry=0.05, retry_for=0.2))
        answer = ask_daemon("mcp_endpoint")
    assert {k: answer[k] for k in ("served", "url", "why")} == {
        "served": False, "url": None, "why": "bind-failed"}
    assert [r["revoked_at"] for r in rows()] == [None]
    assert len(daemon.mcp_tokens.active()) == 1
    row = checks.endpoint_row(answer)
    assert row.state == "fail" and "no token was revoked" in row.detail
    assert row.fix == checks.RESTART


def test_a_daemon_beside_its_active_socket_unit_is_supervised_and_revokes_nothing(
        serving, sessions, unit_read):
    serving()
    s = sessions()
    s.bind()
    unit_read.state = "active"
    with listener() as unit:
        unit.setblocking(False)
        port = unit.getsockname()[1]
        daemon = serving(port=port)
        answer = ask_daemon("mcp_endpoint")
        # The unit's service was not started: nothing connected to its socket.
        with pytest.raises(BlockingIOError):
            unit.accept()
    assert {k: answer[k] for k in ("served", "url", "why")} == {
        "served": False, "url": None, "why": "supervised"}
    assert [r["revoked_at"] for r in rows()] == [None]
    assert len(daemon.mcp_tokens.active()) == 1
    row = checks.endpoint_row(answer)
    assert row.state == "fail" and "no token was revoked" in row.detail
    assert "socket unit" in row.detail
    assert row.fix == checks.SUPERVISED


def test_a_listener_that_may_be_the_unit_is_bind_failed_and_revokes_nothing(
        serving, sessions, unit_read):
    serving()
    s = sessions()
    s.bind()
    unit_read.state = "unknown"
    with listener() as holder:
        daemon = serving(port=holder.getsockname()[1])
        answer = ask_daemon("mcp_endpoint")
    assert answer["served"] is False and answer["why"] == "bind-failed"
    assert "couldn't be read" in answer["detail"]
    assert [r["revoked_at"] for r in rows()] == [None]
    assert len(daemon.mcp_tokens.active()) == 1
    row = checks.endpoint_row(answer)
    assert row.state == "fail" and "no token was revoked" in row.detail


@pytest.mark.timed
def test_the_daemon_tries_the_bind_for_five_seconds_before_bind_failed(serving, sessions):
    serving()
    s = sessions()
    s.bind()
    with blocker() as held:
        began = time.monotonic()
        serving(port=held.getsockname()[1])
        took = time.monotonic() - began
        assert endpoint()["why"] == "bind-failed"
    assert 5.0 <= took < 7.0
    assert [r["revoked_at"] for r in rows()] == [None]


@pytest.mark.skipif(not LINUX, reason="reads the kernel's TCP table for TIME_WAIT")
def test_a_restart_beside_its_predecessors_time_wait_sockets_serves_and_revokes_nothing(
        serving, sessions):
    daemon = serving()
    port = daemon.mcp_endpoint.port
    s = sessions()
    s.bind()
    # A keep-alive connection the first daemon's stop closes from its side.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/mcp", body=json.dumps(INITIALIZE),
                 headers={"Content-Type": "application/json",
                          "Accept": "application/json, text/event-stream",
                          "Authorization": f"Bearer {s.token}"})
    assert conn.getresponse().read()
    s.close()
    daemon.stop()
    conn.close()
    assert _time_wait_on(port)
    daemon = serving(port=port)
    assert endpoint() == {"served": True, "url": f"http://127.0.0.1:{port}/mcp",
                          "why": None}
    assert [r["revoked_at"] for r in rows()] == [None]


def test_a_bind_sent_while_the_endpoint_settles_is_answered_after_it(serving, sessions):
    gate, asked = threading.Event(), threading.Event()

    def slow(port):
        asked.set()
        assert gate.wait(10)
        return listen.take(port)

    serving()
    s = sessions()
    starting = threading.Thread(target=serving, kwargs={"mcp_listen": slow})
    starting.start()
    try:
        assert asked.wait(10)
        answers = []
        binding = threading.Thread(target=lambda: answers.append(s.bind()))
        binding.start()
        binding.join(0.5)
        assert binding.is_alive() and answers == []      # waits for the settle
        gate.set()
        binding.join(10)
        assert answers == [{"served": True}]
    finally:
        gate.set()
        starting.join(10)


def test_the_listener_is_closed_before_the_daemon_socket_and_its_lock_go(serving):
    daemon = serving()
    port = daemon.mcp_endpoint.port
    seen = []
    real_stop = daemon.mcp_endpoint.stop

    async def stop():
        await real_stop()
        # The HTTP listener is closed here, while the daemon socket still serves and
        # its lock is held.
        with socket.socket() as probe:
            probe.settimeout(1)
            seen.append((probe.connect_ex(("127.0.0.1", port)) != 0,
                         daemon.host._server.is_serving()))

    daemon.host.stop_hook = stop
    daemon.stop()
    assert seen == [(True, True)]


# ==================================================================== the doctor

def test_the_endpoint_rows_follow_the_daemons_answer():
    url = "http://127.0.0.1:8450/mcp"
    on = type("Mcp", (), {"enabled": True, "port": 8450})()
    off = type("Mcp", (), {"enabled": False, "port": 8450})()
    held = checks.endpoint_row({"served": True, "url": url, "held": True}, on)
    assert held.state == "ok" and "supervisor holds" in held.detail
    own = checks.endpoint_row({"served": True, "url": url, "held": False}, on)
    assert own.state == "warn" and "another local user could take the port" in own.detail
    assert checks.endpoint_row({"served": False, "why": "disabled"}, off).state == "off"
    failed = checks.endpoint_row({"served": False, "why": "failed",
                                  "detail": "the endpoint didn't start: x\ny"}, on)
    assert failed.state == "fail" and failed.detail == "the endpoint didn't start: x"
    assert checks.port_row({"served": True, "url": url, "held": True}, on) is None
    moved = checks.port_row({"served": True, "url": url, "held": False},
                            type("Mcp", (), {"enabled": True, "port": 8451})())
    assert moved.state == "warn" and moved.fix == checks.RESTART


def test_the_tokens_row_counts_live_sessions_and_the_days_revocations(serving, sessions,
                                                                      unit_read):
    unit_read.state = "missing"
    serving()
    for name in ("planner", "main"):
        sessions(name=name).bind()
    with listener() as holder:
        serving(port=holder.getsockname()[1])
    s = sessions(name="reviewer")
    s.bind()
    row = checks.tokens_row()
    assert row.state == "ok"
    assert row.detail == ("1 session holds a live tool token; revoked in the last 24 "
                          "hours: 2 port-taken")
    later = checks.tokens_row(now=lambda: time.time() + 2 * checks.REVOKED_WINDOW)
    assert later.detail == "1 session holds a live tool token"


def test_a_live_session_whose_token_was_revoked_gets_its_own_warn_row(serving, sessions):
    from moxi_comms.mcp import tokens

    daemon = serving()
    lost, kept = sessions(name="planner"), sessions(name="main")
    lost.bind()
    kept.bind()
    # A session bound under its claim that never registered a token: no row of its own.
    bare = sessions(name="reviewer")
    bare.client = DaemonClient(daemon_socket()).connect()
    bare.client.bind(name="reviewer", scope="proj-a", adapter="codex", claim=bare.key)
    assert ask_daemon("mcp_endpoint")["revoked"] == []
    daemon.mcp_tokens.revoke_all(tokens.PORT_TAKEN)
    # `main` registers a new token, so only `planner` is left without one.
    kept.token = tokens.mint()
    kept.client.mcp_token(tokens.digest(kept.token))
    answer = ask_daemon("mcp_endpoint")
    assert [(e["identity"].split("@")[0], e["why"]) for e in answer["revoked"]] == [
        ("planner", "port-taken")]
    (row,) = checks.revoked_rows(answer)
    assert row.name == f"mcp tokens: {answer['revoked'][0]['identity']}"
    assert row.state == "warn" and "port-taken" in row.detail
    assert row.fix == "relaunch that session to give it comms' tools"
    listed = checks.mcp_rows(ask=lambda: answer, configured=None)
    assert [r.name for r in listed][-1] == row.name
    # Once the session's binding ends, it isn't listed.
    lost.close()
    deadline = time.monotonic() + 5
    while ask_daemon("mcp_endpoint")["revoked"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ask_daemon("mcp_endpoint")["revoked"] == []


def test_doctor_rows_ask_the_running_daemon(serving):
    daemon = serving()
    names = {r.name: r for r in checks.mcp_rows()}
    assert names["mcp endpoint"].state == "warn"            # bound itself in a test
    assert str(daemon.mcp_endpoint.port) in names["mcp endpoint"].detail
    assert "mcp tokens" in names
    assert checks.health().state == "ok"
    daemon.stop()
    assert "mcp endpoint" not in {r.name for r in checks.mcp_rows()}
    health = checks.health()
    assert health.state == "fail" and health.fix == checks.RESTART


# ==================================================================== idle cost

def _switches() -> int:
    total = 0
    for tid in os.listdir("/proc/self/task"):
        try:
            with open(f"/proc/self/task/{tid}/status") as f:
                for line in f:
                    if line.startswith("voluntary_ctxt_switches:"):
                        total += int(line.split()[1])
        except OSError:
            pass
    return total


def _idle(seconds: float) -> int:
    before = _switches()
    time.sleep(seconds)
    return _switches() - before


@pytest.mark.skipif(not LINUX, reason="counts context switches in /proc")
def test_the_endpoint_adds_no_idle_wakeups(serving, rig):
    """With the endpoint on and no clients, the process's voluntary context switches
    over 5 s match a daemon with it off; uvicorn's tick alone would add 50."""
    off = _idle(5.0)
    serving()
    on = _idle(5.0)
    assert on <= off + 15, (off, on)
