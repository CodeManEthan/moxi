"""`moxi ui open` and the other verbs: the Windows logon task path, the locator check,
the spawn rule, the opener, and the waits, from recorded `units.status` rows."""

from __future__ import annotations

import json
import os
import stat
import time

import pytest
from moxi import units
from moxi.paths import Layout, python
from moxi.units import Acted, UnitState
from moxi_ui import cli
from moxi_ui.cli import Refused


def task(state="inactive", enabled=True, last=None):
    return UnitState("ui", "shell", "daemon", "schtasks", state, last=last, enabled=enabled)


V4, V6 = "moxi-ui-shell.socket", "moxi-ui-shell-1.socket"
#: A tagged home's names for them, as the core gives them.
TAGGED = ("moxi-h3f9a2c1d-ui-shell.socket", "moxi-h3f9a2c1d-ui-shell-1.socket")


def ids_of(*sockets):
    """A stand-in for `moxi.units.ids` on systemd that gives the shell `sockets` and its
    service; the core's own tests hold the names it builds."""
    def ids(module, name, kind="daemon"):
        assert (module, name, kind) == ("ui", "shell", "daemon")
        return (*sockets, sockets[0].replace(".socket", ".service"))
    return ids


@pytest.fixture(autouse=True)
def default_ids(monkeypatch):
    """The default home's socket units, unless a test names others."""
    monkeypatch.setattr(units, "ids", ids_of(V4, V6))


def socket_unit(state="active", detail="", backend="systemd"):
    """The socket state as the core reads it: `detail` names the units not active."""
    return UnitState("ui", "shell", "socket", backend, state, detail=detail)


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
        monkeypatch.setattr(cli, "_written_port", lambda port: None)
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
    world = World(monkeypatch, [socket_unit("failed", f"failed: {V4}")], probes=("down", "up"))
    world.ensure()
    assert "moxi install ui" in world.said[0] and world.spawned == [True]


def test_a_socket_that_holds_only_127_0_0_1_needs_no_start_and_no_connect(monkeypatch):
    world = World(monkeypatch, [socket_unit("failed", f"failed: {V6}")], probes=("down",))
    dialled = []
    monkeypatch.setattr(cli, "_port_taken", lambda port: dialled.append(port) or True)
    assert world.ensure() is None
    assert world.spawned == [] and dialled == []
    assert "localhost" in world.said[0] and "moxi install ui" in world.said[0]


@pytest.mark.parametrize("read", [None, [socket_unit("unknown", "systemctl timed out")]])
def test_a_supervisor_that_cannot_be_read_is_refused_with_no_connect(monkeypatch, read):
    world = World(monkeypatch, probes=("down",))
    dialled = []
    monkeypatch.setattr(cli, "_units_status", lambda: read)
    monkeypatch.setattr(cli, "_port_taken", lambda port: dialled.append(port) or False)
    with pytest.raises(Refused, match="supervisor could not be read") as refused:
        world.ensure()
    assert world.spawned == [] and dialled == []
    assert ("systemctl timed out" in str(refused.value)) is (read is not None)
    # Windows has no socket: the start goes on.
    world = World(monkeypatch, platform="win32", probes=("down", "up"))
    monkeypatch.setattr(cli, "_units_status", lambda: read)
    world.ensure()
    assert world.spawned == [True]


@pytest.mark.parametrize("row", [socket_unit("active"), socket_unit("failed", f"failed: {V6}")])
def test_a_socket_written_with_another_port_is_refused_with_no_connect(monkeypatch, row):
    world = World(monkeypatch, [row], probes=("down",))
    dialled = []
    monkeypatch.setattr(cli, "_port_taken", lambda port: dialled.append(port) or True)
    monkeypatch.setattr(cli, "_written_port", lambda port: 8459 if port == 8460 else None)
    with pytest.raises(Refused, match=r"^the socket listens on 8459; run `moxi install ui`$"):
        world.ensure()
    assert world.spawned == [] and dialled == []


def test_the_written_port_is_read_from_the_units_the_plan_would_change(monkeypatch, tmp_path):
    from moxi import units

    written = tmp_path / V4
    written.write_text("[Socket]\nListenStream=127.0.0.1:8460\n")
    entry = {"socket": {"setting": "port", "addresses": ["127.0.0.1"]}}

    def plan(port, action="change"):
        text = f"[Socket]\nListenStream=127.0.0.1:{port}\n"
        return [units.UnitChange("ui", "shell", "socket", action, None, (written,), text,
                                 "", texts=(text,), entry=entry)]

    monkeypatch.setattr(units, "plan", lambda module, only=None: plan(8461))
    assert cli._written_port(8461) == 8460
    monkeypatch.setattr(units, "plan", lambda module, only=None: plan(8460, "keep"))
    assert cli._written_port(8460) is None
    # A plan that can't be read refuses nothing.
    monkeypatch.setattr(units, "plan", lambda module, only=None: 1 / 0)
    assert cli._written_port(8461) is None


#: The core's detail when the first unit is inactive or missing and the second has
#: failed: "not active" covers both, so the two cases read the same.
V4_DOWN_V6_FAILED = f"failed: {V6}; not active: {V4}"


@pytest.mark.parametrize(("row", "holds"), [
    pytest.param(socket_unit("active", "listening"), True, id="both active"),
    pytest.param(socket_unit("failed", f"failed: {V6}"), True, id="v6 failed"),
    pytest.param(socket_unit("inactive", f"not active: {V6}"), True, id="v6 inactive"),
    pytest.param(socket_unit("failed", f"failed: {V4}"), False, id="v4 failed"),
    pytest.param(socket_unit("failed", f"failed: {V4}, {V6}"), False, id="both failed"),
    pytest.param(socket_unit("failed", f"failed: {V4}; not active: {V6}"), False,
                 id="v4 failed, v6 inactive"),
    pytest.param(socket_unit("failed", V4_DOWN_V6_FAILED), False, id="v4 inactive, v6 failed"),
    pytest.param(socket_unit("failed", V4_DOWN_V6_FAILED), False, id="v4 missing, v6 failed"),
    pytest.param(socket_unit("inactive", f"not active: {V4}"), False, id="v4 inactive"),
    pytest.param(socket_unit("inactive", f"not active: {V4}, {V6}"), False, id="both inactive"),
    # A detail not in the core's form for the state is never read as the first unit active.
    pytest.param(socket_unit("failed", ""), False, id="no detail"),
    pytest.param(socket_unit("inactive", f"failed: {V6}"), False, id="detail of another state"),
    pytest.param(socket_unit("failed", f"failed: {V6} (start limit)"), False, id="other words"),
    pytest.param(socket_unit("inactive", "not loaded", backend="launchd"), False, id="launchd"),
    pytest.param(None, False, id="no socket"),
])
def test_whether_the_socket_holds_127_0_0_1(row, holds):
    assert cli.socket_holds_v4(row) is holds


def test_a_socket_whose_first_unit_is_down_beside_a_failed_one_makes_no_url_for_it(monkeypatch):
    """127.0.0.1's unit stopped or missing and ::1's failed: nothing holds 127.0.0.1, so
    `open` says so and starts a shell of its own in place of resting on the socket."""
    world = World(monkeypatch, [socket_unit("failed", V4_DOWN_V6_FAILED)], probes=("down", "up"))
    # The shell it started gives the URL, not the socket (which would return None).
    assert world.ensure() is UP and world.spawned == [True]
    assert V4_DOWN_V6_FAILED in world.said[0] and "localhost" not in world.said[0]


def test_on_windows_the_skipped_socket_is_no_socket(monkeypatch):
    off = socket_unit("off", "the socket is skipped on Windows; the daemon binds its own",
                      backend="schtasks")
    assert cli.socket_row([off]) is None
    world = World(monkeypatch, [task("inactive"), off], platform="win32",
                  probes=("down", "up"))
    world.ensure()
    assert world.started == [True] and not any("socket" in line for line in world.said)


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


@pytest.mark.parametrize("home", [False, True], ids=["default home", "tagged home"])
def test_a_locator_that_moves_the_runtime_dir_is_refused_under_a_socket(monkeypatch, tmp_path,
                                                                        home):
    sockets = TAGGED if home else (V4, V6)
    monkeypatch.setattr(units, "ids", ids_of(*sockets))
    base = {**_plain(), "MOXI_HOME": str(tmp_path / "h")} if home else _plain()
    world = World(monkeypatch, [socket_unit("active")])
    with pytest.raises(Refused, match="MOXI_UI_HOME") as caught:
        world.ensure({**base, "MOXI_UI_HOME": str(tmp_path / "elsewhere")})
    assert f"systemctl --user stop {' '.join(sockets)}`" in str(caught.value)
    World(monkeypatch, [socket_unit("active")]).ensure(base)


def test_a_tagged_home_opens_through_its_own_socket(monkeypatch, tmp_path):
    """The unit a tagged home wrote runs with that `MOXI_HOME`, so the shell it starts reads
    the runtime dir this shell does, under any spelling of the home."""
    monkeypatch.setattr(units, "ids", ids_of(*TAGGED))
    home = tmp_path / "h"
    home.mkdir()
    (tmp_path / "link").symlink_to(home)
    monkeypatch.chdir(tmp_path)
    for spelling in (str(home), str(tmp_path / "link"), "h"):
        environ = {**_plain(), "MOXI_HOME": spelling}
        assert units.environment("ui", environ) == {"MOXI_HOME": os.path.realpath(home)}
        world = World(monkeypatch, [socket_unit("active")], probes=("down",))
        assert world.ensure(environ) is None and world.spawned == []


def test_the_fix_line_names_only_the_socket_units_the_core_gives(monkeypatch, tmp_path):
    monkeypatch.setattr(units, "ids", ids_of(V4))
    world = World(monkeypatch, [socket_unit("active")])
    with pytest.raises(Refused) as caught:
        world.ensure({**_plain(), "MOXI_UI_HOME": str(tmp_path / "elsewhere")})
    assert f"systemctl --user stop {V4}`" in str(caught.value)
    monkeypatch.setattr(units, "ids", lambda module, name, kind="daemon": ())
    world = World(monkeypatch, [socket_unit("active")])
    with pytest.raises(Refused) as caught:
        world.ensure({**_plain(), "MOXI_UI_HOME": str(tmp_path / "elsewhere")})
    assert "systemctl" not in str(caught.value)
    assert "after stopping the shell's socket units" in str(caught.value)


def test_on_windows_the_stored_environment_counts(monkeypatch, tmp_path):
    moved = str(tmp_path / "elsewhere")
    environ = {**_plain(), "MOXI_UI_HOME": moved}
    world = World(monkeypatch, [task("active")], platform="win32")
    with pytest.raises(Refused, match="MOXI_UI_HOME") as caught:
        world.ensure(environ)
    assert "moxi ui stop" in str(caught.value)
    world = World(monkeypatch, [task("active")], platform="win32")
    monkeypatch.setattr(cli, "_stored_environment", lambda: {"Moxi_Ui_Home": moved})
    world.ensure(environ)


def test_on_windows_a_tagged_homes_task_runs_with_its_home(monkeypatch, tmp_path):
    """The task's gate sets the home it was written from over the stored environment's."""
    environ = {**_plain(), "MOXI_HOME": str(tmp_path / "h")}
    world = World(monkeypatch, [task("active")], platform="win32")
    monkeypatch.setattr(cli, "_stored_environment",
                        lambda: {"MOXI_HOME": str(tmp_path / "other")})
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


# --- status -------------------------------------------------------------------------------


def test_status_lists_each_page_server_from_the_servers_rows(monkeypatch, capsys):
    from types import SimpleNamespace

    info = SimpleNamespace(pid=10, port=8460, server_id="s", version="1", started_at="t")
    data = {"shell": {"pid": 10}, "servers": [
        {"module": "notes", "state": "running", "pid": 42},
        {"module": "cache", "state": "idle", "pid": None}]}
    monkeypatch.setattr(cli, "lock_held", lambda: True)
    monkeypatch.setattr(cli, "_running", lambda: info)
    monkeypatch.setattr(cli, "call", lambda *a, **k: (200, data))
    monkeypatch.setattr(cli, "_units_status", lambda: [socket_unit()])
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("shell: running, pid 10")
    assert out[-2:] == ["page notes: running, pid 42", "page cache: idle"]
    assert cli.main(["status", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["servers"] == data and report["units"][0]["kind"] == "socket"


def test_signout_with_no_shell_clears_the_store_holding_page_lock(monkeypatch, capsys):
    from moxi import files
    from moxi_ui import session

    monkeypatch.setattr(cli, "_running", lambda: None)
    seen = []
    monkeypatch.setattr(session, "clear_stored", lambda state_dir: seen.append(cli.lock_held()))
    assert cli.main(["signout"]) == 0
    assert seen == [True] and "signed out" in capsys.readouterr().out
    # A shell starting holds the lock: nothing is cleared, and the exit is 75.
    with files.lock(Layout("ui").runtime_dir(create=True) / "page.lock", timeout=0):
        assert cli.main(["signout"]) == 75
    assert seen == [True] and "starting or stopping" in capsys.readouterr().err


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
