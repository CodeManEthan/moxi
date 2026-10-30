"""`moxi ui open` and the other verbs: the Windows logon task path, the locator check,
the spawn rule, the opener, and the waits, from recorded `units.status` rows."""

from __future__ import annotations

import os
import stat
import time

import pytest
from moxi.paths import Layout, python
from moxi.units import Acted, UnitState
from moxi_ui import cli
from moxi_ui.cli import Refused


def task(state="inactive", enabled=True, last=None):
    return UnitState("ui", "shell", "daemon", "schtasks", state, last=last, enabled=enabled)


def socket_unit(state="active"):
    return UnitState("ui", "shell", "socket", "systemd", state)


#: What a probe that found the shell up returns.
UP = object()


class World:
    """The seams `open` reads, scripted."""

    def __init__(self, monkeypatch, rows=(), *, platform="linux", probes=("up",), start_ok=True):
        self.rows = [list(rows)] if not rows or not isinstance(rows[0], list) else list(rows)
        self.probes = list(probes)
        self.started = []
        self.spawned = []
        self.opened = []
        self.said = []
        self.start_ok = start_ok
        monkeypatch.setattr(cli, "_platform", lambda: platform)
        monkeypatch.setattr(cli, "_wsl", lambda: False)
        monkeypatch.setattr(cli, "_units_status", self.status)
        monkeypatch.setattr(cli, "_units_start", self.start)
        monkeypatch.setattr(cli, "spawn_shell", lambda: self.spawned.append(True))
        monkeypatch.setattr(cli, "probe", self.probe)
        monkeypatch.setattr(cli, "lock_held", lambda: False)
        monkeypatch.setattr(cli, "_port_taken", lambda port: False)
        monkeypatch.setattr(cli, "_stored_environment", lambda: {})
        monkeypatch.setattr(cli, "_open_browser", lambda url: self.opened.append(url) or True)
        monkeypatch.setattr(cli, "POLL", 0.001)
        monkeypatch.setattr(cli, "STATUS_POLL", 0.0)
        monkeypatch.setattr(cli, "UP_WAIT", 0.2)
        monkeypatch.setattr(cli, "RESTART_WAIT", 0.5)

    def status(self):
        return self.rows.pop(0) if len(self.rows) > 1 else self.rows[0]

    def start(self):
        self.started.append(True)
        return [Acted("ui", "shell", "start", self.start_ok, ("refused",) if not self.start_ok else ())]

    def probe(self):
        state = self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]
        return state, (UP if state == "up" else None)

    def ensure(self, environ=None):
        return cli.ensure_shell(8460, environ=environ or {}, say=self.said.append)


# --- the Windows logon task ---------------------------------------------------------------


def test_a_disabled_task_takes_the_detached_spawn(monkeypatch):
    world = World(monkeypatch, [task(enabled=False)], platform="win32", probes=("down", "up"))
    world.ensure()
    assert world.started == [] and world.spawned == [True]
    assert "disabled" in world.said[0]


def test_a_failed_task_is_started_and_waited_for(monkeypatch):
    world = World(monkeypatch, [task("failed", last={"at": "2026-01-01T00:00:00Z", "exit": 1})],
                  platform="win32", probes=("down", "down", "down", "up"))
    world.ensure()
    assert world.started == [True] and world.spawned == []
    assert world.said == ["the logon task restarts the shell within a minute"]


def test_a_failed_task_that_fails_again_stops_the_wait_at_once(monkeypatch):
    old = {"at": "2026-01-01T00:00:00Z", "exit": 1, "result": "ran"}
    new = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 1)),
           "exit": 1, "result": "ran"}
    world = World(monkeypatch, [[task("failed", last=old)], [task("failed", last=new)]],
                  platform="win32", probes=("down",))
    monkeypatch.setattr(cli, "RESTART_WAIT", 30.0)
    began = time.monotonic()
    with pytest.raises(Refused, match="failed again"):
        world.ensure()
    assert time.monotonic() - began < 5


def test_an_inactive_task_is_started_and_waited_for_five_seconds(monkeypatch):
    world = World(monkeypatch, [task("inactive")], platform="win32", probes=("down",))
    with pytest.raises(Refused, match="did not answer"):
        world.ensure()
    assert world.started == [True] and world.spawned == []


@pytest.mark.parametrize("enabled", [True, None])
def test_a_start_that_fails_takes_the_detached_spawn(monkeypatch, enabled):
    world = World(monkeypatch, [task("inactive", enabled=enabled)], platform="win32",
                  probes=("down", "up"), start_ok=False)
    world.ensure()
    assert world.started == [True] and world.spawned == [True]
    assert "did not start" in world.said[0]


# --- the other steps -------------------------------------------------------------------


def test_a_listening_socket_needs_no_start(monkeypatch):
    world = World(monkeypatch, [socket_unit("active")], probes=("down",))
    assert world.ensure() is None
    assert world.spawned == []


def test_a_failed_socket_is_said_and_a_shell_started(monkeypatch):
    world = World(monkeypatch, [socket_unit("failed")], probes=("down", "up"))
    world.ensure()
    assert "moxi install ui" in world.said[0] and world.spawned == [True]


def test_a_port_held_by_another_program_is_refused(monkeypatch):
    world = World(monkeypatch, probes=("down",))
    monkeypatch.setattr(cli, "_port_taken", lambda port: True)
    with pytest.raises(Refused, match="held by another program"):
        world.ensure()
    assert world.spawned == []


def test_a_held_lock_waits_and_starts_no_second_shell(monkeypatch):
    world = World(monkeypatch, probes=("down", "down", "up"))
    monkeypatch.setattr(cli, "lock_held", lambda: True)
    world.ensure()
    assert world.spawned == []
    world = World(monkeypatch, probes=("down",))
    monkeypatch.setattr(cli, "lock_held", lambda: True)
    with pytest.raises(Refused, match="page.lock"):
        world.ensure()
    assert world.spawned == []


def test_a_proof_that_fails_twice_is_refused(monkeypatch):
    class Info:
        def __init__(self, server_id):
            self.server_id, self.port = server_id, 8460

    world = World(monkeypatch)
    states = [("foreign", Info("a")), ("foreign", Info("b"))]
    monkeypatch.setattr(cli, "probe", lambda: states.pop(0))
    monkeypatch.setattr(cli, "read_runtime", lambda module: Info("b"))
    with pytest.raises(Refused, match="held by another program"):
        world.ensure()
    # No new file within the wait is refused too.
    monkeypatch.setattr(cli, "probe", lambda: ("foreign", Info("a")))
    monkeypatch.setattr(cli, "read_runtime", lambda module: Info("a"))
    monkeypatch.setattr(cli, "STALE_WAIT", 0.05)
    with pytest.raises(Refused, match="held by another program"):
        world.ensure()


# --- the locator check -----------------------------------------------------------------


def _plain():
    """This environment without the locators the test fence sets."""
    names = Layout("ui").locators()
    return {key: value for key, value in os.environ.items() if key not in names}


def test_a_locator_that_moves_the_runtime_dir_is_refused_under_a_socket(monkeypatch, tmp_path):
    world = World(monkeypatch, [socket_unit("active")])
    environ = {**_plain(), "MOXI_HOME": str(tmp_path / "elsewhere")}
    with pytest.raises(Refused, match="MOXI_HOME") as caught:
        world.ensure(environ)
    assert "systemctl --user stop moxi-ui-shell.socket" in str(caught.value)
    World(monkeypatch, [socket_unit("active")]).ensure(_plain())


def test_on_windows_the_stored_environment_counts(monkeypatch, tmp_path):
    moved = str(tmp_path / "elsewhere")
    environ = {**_plain(), "MOXI_HOME": moved}
    world = World(monkeypatch, [task("active")], platform="win32")
    with pytest.raises(Refused, match="MOXI_HOME") as caught:
        world.ensure(environ)
    assert "moxi ui stop" in str(caught.value)
    world = World(monkeypatch, [task("active")], platform="win32")
    monkeypatch.setattr(cli, "_stored_environment", lambda: {"Moxi_Home": moved})
    world.ensure(environ)


# --- the spawn rule and the opener -------------------------------------------------------


def test_the_shell_is_spawned_as_python_dash_p_from_its_runtime_dir(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_spawn", lambda argv, **kw: seen.append((argv, kw)))
    log = cli.log_path()
    cli.spawn_shell()
    ((argv, kw),) = seen
    assert argv == [str(python()), "-P", "-m", "moxi_ui.serve"]
    assert kw["cwd"] == Layout("ui").runtime_dir()
    assert stat.S_IMODE(kw["cwd"].stat().st_mode) == 0o700
    assert kw["output"] == log and kw["env"]["MOXI_LOG_FILE"] == str(log)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_off_linux_the_browser_gets_a_redirect_file_with_no_ticket(monkeypatch, platform):
    world = World(monkeypatch, platform=platform)
    directory = Layout("ui").state_dir() / "open"
    directory.mkdir(parents=True)
    stale = directory / "0000000000000000.html"
    stale.write_text("old")
    os.utime(stale, (time.time() - 120, time.time() - 120))
    url = "http://127.0.0.1:8460/signin?n=0123456789abcdef#t=" + "s" * 43
    cli.hand_over(url, printed=False, say=world.said.append)
    (opened,) = world.opened
    assert opened.startswith("file://") and "s" * 43 not in opened
    assert not stale.exists()
    (page,) = directory.iterdir()
    assert stat.S_IMODE(page.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert url in page.read_text()


def test_under_wsl_open_prints_the_url_and_starts_no_browser(monkeypatch):
    world = World(monkeypatch)
    monkeypatch.setattr(cli, "_wsl", lambda: True)
    cli.hand_over("http://127.0.0.1:8460/signin?n=1#t=x", printed=False, say=world.said.append)
    assert world.opened == [] and world.said[-1] == "http://127.0.0.1:8460/signin?n=1#t=x"


def test_on_linux_the_url_goes_to_the_browser(monkeypatch):
    world = World(monkeypatch)
    cli.hand_over("http://127.0.0.1:8460/signin?n=1#t=x", printed=False, say=world.said.append)
    assert world.opened == ["http://127.0.0.1:8460/signin?n=1#t=x"]


# --- end to end ----------------------------------------------------------------------------


def test_open_print_starts_a_shell_and_its_ticket_signs_in(monkeypatch, capsys):
    import json
    import signal
    import socket

    from moxi_loopback.api import read_runtime

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    config = Layout("ui").config_file()
    config.parent.mkdir(parents=True)
    config.write_text(f"port = {port}\n")
    monkeypatch.setattr(cli, "_units_status", lambda: [])
    try:
        assert cli.main(["open", "--print"]) == 0
        url = capsys.readouterr().out.strip().splitlines()[-1]
        assert url.startswith(f"http://127.0.0.1:{port}/signin?n=")
        ticket = url.split("#t=", 1)[1]
        body = json.dumps({"ticket": ticket}).encode()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(b"POST /m/ui/api/signin HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                         b"Origin: http://127.0.0.1:%d\r\nSec-Fetch-Site: same-origin\r\n"
                         b"Content-Type: application/json\r\nContent-Length: %d\r\n"
                         b"Connection: close\r\n\r\n%s" % (port, port, len(body), body))
            answer = b""
            while chunk := sock.recv(65536):
                answer += chunk
        assert answer.startswith(b"HTTP/1.1 200") and b"set-cookie: moxi-ui=" in answer.lower()
        # A second open finds the shell up and starts no other.
        assert cli.main(["open", "--no-browser"]) == 0
        assert cli.main(["status"]) == 0
        assert "running" in capsys.readouterr().out
    finally:
        info = read_runtime("ui")
        if info is not None:
            os.kill(info.pid, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while cli.lock_held() and time.monotonic() < deadline:
                time.sleep(0.05)
    assert read_runtime("ui") is None
