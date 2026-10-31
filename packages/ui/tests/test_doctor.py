"""The ui module's doctor rows: the shell row as the lock, the units and the port give it,
and a row per page server from the shell's `/servers` data or, with the shell down, from
`servers.json`. A doctor run never connects to a port only a socket holds."""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from moxi import files, modules
from moxi.doctor import Row
from moxi.paths import Layout
from moxi_loopback.api import read_runtime

from moxi_ui import doctor, health
from moxi_ui.starter import SERVERS_FILE, SERVERS_FORMAT
from test_snapshot import site  # noqa: F401 - the fixture

NOW = 1_800_000_000.0


def _stamp(seconds_ago):
    return datetime.fromtimestamp(NOW - seconds_ago, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _server(module, state="running", **extra):
    row = {"module": module, "state": state, "pid": 100 if state == "running" else None,
           "uptime_s": 30.0, "log": "/logs/shell.log", "starts": 1, "failed_starts": 0,
           "failed_in_row": 0, "crashes": 0, "last_exit": None, "last_exit_at": None,
           "last_crash_at": None, "reason": None,
           "sample": {"pid": 100, "rss": 40 * 1024**2, "cpu_s": 0.2, "fds": 10}
           if state == "running" else None,
           "open": {"requests": 0, "streams": 1} if state == "running" else None,
           "budget": {"rss": "120M", "fds": 64}}
    row.update(extra)
    return row


def _running(servers, port=8460):
    shell = {"pid": 1, "port": port, "uptime_s": 60.0, "localhost": True,
             "sample": {"pid": 1, "rss": 30 * 1024**2, "cpu_s": 1.0, "fds": 20},
             "budget": {"rss": "80M", "fds": 64}}
    return health.Probe("running", servers={"shell": shell, "servers": servers}, port=port)


def _states(rows):
    return {row.name: row.state for row in rows}


@pytest.fixture
def runtime():
    return Layout("ui").runtime_dir(create=True)


def _set_port(port):
    path = Layout("ui").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"port = {port}\n")


# --- the rows from recorded data ----------------------------------------------------------


def test_the_rows_with_the_shell_up_come_from_its_servers_data():
    found = _running([
        _server("fine"),
        _server("heavy", sample={"pid": 101, "rss": 200 * 1024**2, "cpu_s": 0.1, "fds": 9}),
        _server("broken", state="failed", failed_in_row=3, reason="exited 3 at start"),
        _server("crashy", crashes=1, last_crash_at=_stamp(600), reason="the page server exited 1"),
        _server("old", state="idle", crashes=1, last_crash_at=_stamp(7200)),
    ])
    rows = doctor.shell_rows(found) + doctor.page_rows(found, now=NOW)
    assert all(isinstance(row, Row) and row.module == "ui" for row in rows)
    assert _states(rows) == {"shell": "ok", "page:broken": "fail", "page:crashy": "warn",
                             "page:fine": "ok", "page:heavy": "warn", "page:old": "off"}
    by = {row.name: row for row in rows}
    assert "/logs/shell.log" in by["page:broken"].detail and "exited 3" in by["page:broken"].detail
    assert "RSS 200 MB over its 120M" in by["page:heavy"].detail
    assert "crashed at" in by["page:crashy"].detail
    assert "1 streams" in by["page:fine"].detail


def test_a_shell_listening_off_its_port_setting_is_a_warn(runtime):
    _set_port(8471)
    row = doctor.shell_rows(_running([], port=8460))[0]
    assert row.state == "warn" and "8471" in row.detail and row.fix
    _set_port(8460)
    assert doctor.shell_rows(_running([], port=8460))[0].state == "ok"


def test_with_the_shell_down_the_page_rows_come_from_servers_json(runtime):
    path = Layout("ui").state_dir() / SERVERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": SERVERS_FORMAT, "modules": {
        "broken": {"starts": 0, "failed_starts": 4, "failed_in_row": 4, "reason": "exit 3"},
        "crashy": {"starts": 2, "crashes": 1, "last_exit": 1, "last_exit_at": _stamp(60),
                   "last_crash_at": _stamp(60)},
        "calm": {"starts": 5, "last_exit": 0, "last_exit_at": _stamp(60)},
    }}))
    rows = doctor.page_rows(health.Probe("down"), now=NOW)
    assert _states(rows) == {"page:broken": "fail", "page:calm": "off", "page:crashy": "warn"}
    assert "shell.log" in {row.name: row for row in rows}["page:broken"].detail
    # A missing file is no rows.
    path.unlink()
    assert doctor.page_rows(health.Probe("down"), now=NOW) == []


@pytest.mark.parametrize("task, state", [
    (None, "warn"),
    (SimpleNamespace(state="missing", enabled=None, last=None), "warn"),
    (SimpleNamespace(state="inactive", enabled=False, last=None), "warn"),
    (SimpleNamespace(state="failed", enabled=True, last={"exit": 1}), "warn"),
    (SimpleNamespace(state="unknown", enabled=None, last=None, detail="schtasks timed out"),
     "warn"),
    (SimpleNamespace(state="inactive", enabled=True, last=None), "off"),
])
def test_on_windows_the_task_row_stands_beside_a_held_port(monkeypatch, task, state):
    monkeypatch.setattr(health, "_platform", lambda: "win32")
    held = doctor.shell_rows(health.Probe("held", task=task, port=8460,
                                          detail="port 8460 is held by another program"))
    assert [(row.name, row.state) for row in held] == [("shell", "fail"), ("logon task", state)]
    if task is not None and task.state == "failed":
        assert "exited with 1" in held[1].detail and "within a minute" in held[1].detail
    if task is not None and task.state == "unknown":
        assert "could not be read: schtasks timed out" in held[1].detail
    # With the port free, the task's state is the shell row itself.
    down = doctor.shell_rows(health.Probe("down", task=task, port=8460))
    assert [(row.name, row.state) for row in down] == [("shell", state)]


# --- the probe the rows rest on -----------------------------------------------------------


class Listener:
    """A listener on a free port that counts the connections made to it."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.setblocking(False)
        self.port = self.sock.getsockname()[1]

    def connects(self):
        count = 0
        while True:
            try:
                conn, _ = self.sock.accept()
            except BlockingIOError:
                return count
            conn.close()
            count += 1

    def close(self):
        self.sock.close()


@pytest.fixture
def listener(runtime):
    held = Listener()
    _set_port(held.port)
    yield held
    held.close()


V4, V6 = "moxi-ui-shell.socket", "moxi-ui-shell-1.socket"


def _socket_unit(state="active", detail="listening", backend="systemd"):
    """The socket state as the core's `units.status` gives it."""
    from moxi.units import UnitState

    epoch = f"{V4}=a1,{V6}=b2" if state == "active" and backend == "systemd" else None
    return UnitState("ui", "shell", "socket", backend, state, detail=detail, epoch=epoch)


def test_idle_under_an_active_socket_the_doctor_makes_no_connect(monkeypatch, listener):
    monkeypatch.setattr(health, "_units_status", lambda: [_socket_unit()])
    rows = list(doctor.rows())
    assert rows[0].name == "shell" and rows[0].state == "off" and "socket" in rows[0].detail
    assert listener.connects() == 0


@pytest.mark.parametrize(("state", "detail", "row"), [
    ("failed", f"failed: {V6}", "warn"),
    ("inactive", f"not active: {V6}", "warn"),
    ("failed", f"failed: {V4}", "fail"),
    ("inactive", f"not active: {V4}, {V6}", "fail"),
])
def test_a_socket_not_all_listening_makes_no_connect(monkeypatch, listener, state, detail,
                                                      row):
    """[::1] alone not held: the shell still starts on 127.0.0.1 and refuses localhost, a
    warn; 127.0.0.1 not held is the fail row, naming the unit."""
    monkeypatch.setattr(health, "_units_status", lambda: [_socket_unit(state, detail)])
    found = next(doctor.rows())
    assert found.name == "shell" and found.state == row
    assert detail in found.detail and found.fix == "moxi install ui"
    if row == "warn":
        assert "localhost is refused" in found.detail
    assert listener.connects() == 0


def test_with_no_socket_the_doctor_connects_once_and_a_listener_is_another_program(
        monkeypatch, listener):
    monkeypatch.setattr(health, "_units_status", lambda: [])
    row = next(doctor.rows())
    assert row.state == "fail" and "another program" in row.detail
    assert listener.connects() == 1
    monkeypatch.setattr(health, "_units_status", lambda: [
        SimpleNamespace(module="ui", name="shell", kind="daemon", state="unknown",
                        detail="read failed", backend="systemd")])
    assert next(doctor.rows()).state == "warn" and listener.connects() == 0


@pytest.mark.parametrize("state", ["failed", "unknown"])
def test_on_windows_every_task_state_goes_on_to_the_connect(monkeypatch, listener, state):
    monkeypatch.setattr(health, "_platform", lambda: "win32")
    task = SimpleNamespace(module="ui", name="shell", kind="daemon", backend="schtasks",
                           state=state, enabled=True, last={"exit": 1}, detail="")
    # The core gives the skipped socket as `off` beside the task: no socket holds the port.
    off = _socket_unit("off", "the socket is skipped on Windows; the daemon binds its own",
                       backend="schtasks")
    monkeypatch.setattr(health, "_units_status", lambda: [task, off])
    rows = list(doctor.rows())
    assert [(r.name, r.state) for r in rows[:2]] == [("shell", "fail"), ("logon task", "warn")]
    assert listener.connects() == 1


@pytest.mark.parametrize("units", ["socket", "listening"])
def test_a_held_lock_with_no_file_is_starting_never_idle_or_another_program(
        monkeypatch, listener, units):
    monkeypatch.setattr(health, "WAIT", 0.4)
    monkeypatch.setattr(health, "_units_status",
                        lambda: [_socket_unit()] if units == "socket" else [])
    with files.lock(Layout("ui").runtime_dir() / "page.lock", timeout=0):
        row = next(doctor.rows())
    assert row.state == "warn" and "starting or stopping" in row.detail
    assert listener.connects() == 0


def test_a_failed_proof_then_no_file_is_this_users_shell_stopping(monkeypatch):
    """A 503 from this user's own shell at its connection cap fails the proof; when the
    shell then removes its file, the last round read no file, so it is not another
    program."""
    from moxi_loopback.api import NotOurServer

    info = SimpleNamespace(port=8460, server_id="a")
    files_read = [info]
    monkeypatch.setattr(health, "read_runtime",
                        lambda module: files_read.pop(0) if files_read else None)

    def refused(found, timeout):
        raise NotOurServer("503")

    monkeypatch.setattr(health, "_servers", refused)
    monkeypatch.setattr(health, "POLL", 0.01)
    found = health._held(0.1)
    assert found.state == "starting"
    # A proof that fails to the end is another program.
    monkeypatch.setattr(health, "read_runtime", lambda module: info)
    assert health._held(0.1).state == "held"


def test_a_runtime_file_that_appears_within_the_wait_gives_the_running_row(monkeypatch, site):
    from moxi_ui.snapshot import ModuleSnapshot
    from test_snapshot import _serve

    monkeypatch.setattr(health, "_units_status", lambda: [_socket_unit()])
    started = []

    def late():
        time.sleep(0.5)
        started.append(_serve(snapshot=ModuleSnapshot(cwd=Layout("ui").runtime_dir(),
                                                      dirs=[str(site)])))

    with files.lock(Layout("ui").runtime_dir() / "page.lock", timeout=0):
        assert read_runtime("ui") is None
        thread = threading.Thread(target=late)
        thread.start()
        try:
            rows = list(doctor.rows())
        finally:
            thread.join(15)
            for shell, serving in started:
                shell.stop()
                serving.join(15)
                shell.close()
    assert rows[0].name == "shell" and rows[0].state in ("ok", "warn")
    assert rows[0].detail.startswith("running: pid")


# --- the announcement ----------------------------------------------------------------------


def test_the_core_runs_the_modules_doctor(runtime):
    from moxi import proc
    from moxi.paths import python

    found = modules.load("ui")
    assert found.doctor == "moxi_ui.doctor:rows" and not found.budgets
    done = proc.run([str(python()), "-P", "-m", "moxi", "doctor", "ui", "--json"],
                    timeout=60, cwd=str(runtime))
    rows = [row for row in json.loads(done.stdout) if row["module"] == "ui"]
    names = [row["name"] for row in rows]
    assert "shell" in names and "DOCTOR" not in names
