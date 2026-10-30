"""The writer's fence, `backend()`, and the fake backend's side of the protocol."""

from __future__ import annotations

import json
import subprocess

import pytest

from moxi import units
from moxi.units import _backend, _fence
from moxi.units._backend import Backend, UnitRef
from moxi.units._fake import FakeBackend, FakeSupervisor
from moxi.units._fence import Fence, FenceError, argv


@pytest.mark.parametrize("backend, verb, args, expect", [
    ("systemd", "daemon-reload", (), ["systemctl", "--user", "daemon-reload"]),
    ("systemd", "enable", ("--now", "a.service", "a.socket"),
     ["systemctl", "--user", "enable", "--now", "a.service", "a.socket"]),
    ("systemd", "disable", ("a.timer",), ["systemctl", "--user", "disable", "a.timer"]),
    ("systemd", "show", ("--property=Id,Triggers", "a.timer"),
     ["systemctl", "--user", "show", "--property=Id,Triggers", "a.timer"]),
    ("systemd", "show", ("--property=Version",),
     ["systemctl", "--user", "show", "--property=Version"]),
    ("systemd", "linger", ("ana",), ["loginctl", "show-user", "ana", "-p", "Linger"]),
    ("launchd", "bootstrap", ("gui/501", "/Users/ana/Library/LaunchAgents/moxi.a.b.plist"),
     ["launchctl", "bootstrap", "gui/501", "/Users/ana/Library/LaunchAgents/moxi.a.b.plist"]),
    ("launchd", "kickstart", ("gui/501/moxi.a.b",),
     ["launchctl", "kickstart", "-k", "gui/501/moxi.a.b"]),
    ("launchd", "kill", ("gui/501/moxi.a.b",), ["launchctl", "kill", "SIGTERM",
                                                "gui/501/moxi.a.b"]),
    ("launchd", "print", ("gui/501",), ["launchctl", "print", "gui/501"]),
    ("schtasks", "/create", ("\\Moxi\\a-b", "/s/a-b.xml"),
     ["schtasks", "/create", "/tn", "\\Moxi\\a-b", "/xml", "/s/a-b.xml", "/f"]),
    ("schtasks", "/change", ("\\Moxi\\a-b", "/disable"),
     ["schtasks", "/change", "/tn", "\\Moxi\\a-b", "/disable"]),
    ("schtasks", "/query", (), ["schtasks", "/query", "/fo", "csv", "/nh"]),
])
def test_the_fence_builds_each_allowed_command(backend, verb, args, expect):
    assert argv(backend, verb, *args) == expect


@pytest.mark.parametrize("backend, verb, args", [
    ("systemd", "kill", ("a.service",)),
    ("systemd", "start", ()),
    ("systemd", "start", ("--force",)),
    ("systemd", "start", ("a.service; rm",)),
    ("systemd", "daemon-reload", ("a.service",)),
    ("systemd", "show", ("--property=Id;x", "a.service")),
    ("systemd", "show", ("--property=Id",)),
    ("systemd", "show", ("--property=Version,Id",)),
    ("systemd", "enable", ("--now",)),
    ("launchd", "bootout", ("system/moxi.a.b",)),
    ("launchd", "bootstrap", ("gui/501", "relative.plist")),
    ("schtasks", "/change", ("\\Moxi\\a", "/delete")),
    ("schtasks", "/run", ("Moxi\\a",)),
    ("schtasks", "tasks", ("\\Moxi\\a",)),
    ("nobody", "start", ()),
])
def test_the_fence_refuses_anything_else(backend, verb, args):
    with pytest.raises(FenceError):
        argv(backend, verb, *args)


def test_the_task_read_carries_refs_only_in_its_environment():
    cmd = argv("schtasks", "tasks")
    assert cmd[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
    assert "$env:MOXI_TASK_REFS" in cmd[4]
    seen = {}

    def runner(cmd, *, env, timeout):
        seen.update(env=env, timeout=timeout)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    [refs] = _fence.task_refs_env([("\\Backups\\", "Daily (home) 'x\";$y")])
    ran = Fence("schtasks", runner).run("tasks", env=refs)
    assert ran.ok and seen["timeout"] == 30
    assert json.loads(seen["env"]["MOXI_TASK_REFS"]) == [
        {"path": "\\Backups\\", "name": "Daily (home) 'x\";$y"}]
    assert "Daily" not in " ".join(ran.argv)
    with pytest.raises(FenceError):
        Fence("systemd", runner).run("start", "a.service", env=refs)


def test_a_long_ref_list_is_split_over_calls():
    refs = [("\\F\\", "t" * 1000 + str(i)) for i in range(70)]
    chunks = _fence.task_refs_env(refs)
    assert len(chunks) > 1
    assert all(len(c["MOXI_TASK_REFS"]) <= _fence.TASK_REFS_MAX for c in chunks)
    assert sum(len(json.loads(c["MOXI_TASK_REFS"])) for c in chunks) == 70


def test_a_call_keeps_stderr_as_it_is_and_names_a_failed_start():
    def fails(cmd, *, env, timeout):
        return subprocess.CompletedProcess(cmd, 1, "", "Unit a.service not found.\n")

    ran = Fence("systemd", fails).run("start", "a.service")
    assert not ran.ok and ran.stderr == "Unit a.service not found.\n"
    assert ran.failure() == ("`systemctl --user start a.service` failed: Unit a.service "
                             "not found.")

    def slow(cmd, *, env, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    ran = Fence("systemd", slow).run("start", "a.service")
    assert not ran.ok and ran.reason == "timed out after 30 s"

    def missing(cmd, *, env, timeout):
        raise FileNotFoundError(2, "No such file", "systemctl")

    assert "did not run" in Fence("systemd", missing).run("daemon-reload").failure()


def test_the_real_runner_is_refused_under_pytest(monkeypatch):
    monkeypatch.delenv(_fence.REAL_SUPERVISOR, raising=False)
    with pytest.raises(_fence.SupervisorRefused):
        Fence("systemd").run("daemon-reload")


# -- backend() -----------------------------------------------------------------------------

def test_backend_reads_the_platform_and_the_file_system(tmp_path, monkeypatch):
    init, runtime = tmp_path / "init", tmp_path / "runtime"
    monkeypatch.setattr(_backend, "_SYSTEMD_INIT", str(init))
    monkeypatch.setattr(_backend.sys, "platform", "linux")
    monkeypatch.setattr(_backend.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    assert units.backend() is None
    init.mkdir()
    assert units.backend() is None
    (runtime / "systemd").mkdir(parents=True)
    (runtime / "systemd" / "private").write_text("")
    assert units.backend() == "systemd"
    monkeypatch.setattr(_backend.shutil, "which", lambda name: None)
    assert units.backend() is None

    monkeypatch.setattr(_backend.sys, "platform", "darwin")
    monkeypatch.setattr(_backend, "_LAUNCHCTL", str(tmp_path / "launchctl"))
    assert units.backend() is None
    (tmp_path / "launchctl").write_text("")
    assert units.backend() == "launchd"

    monkeypatch.setattr(_backend.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", str(tmp_path / "win"))
    assert units.backend() is None
    (tmp_path / "win" / "System32").mkdir(parents=True)
    (tmp_path / "win" / "System32" / "schtasks.exe").write_text("")
    assert units.backend() == "schtasks"
    monkeypatch.setattr(_backend.sys, "platform", "freebsd14")
    assert units.backend() is None


def test_a_supervisor_with_no_writer_yet_says_so(monkeypatch):
    monkeypatch.setattr(_backend, "backend", lambda: "launchd")
    import importlib

    try:
        importlib.import_module("moxi.units._launchd")
    except ModuleNotFoundError:
        with pytest.raises(NotImplementedError, match="no launchd writer"):
            _backend.implementation()
    else:
        assert _backend.implementation().name == "launchd"


# -- the fake backend's acts, reads and wraps ------------------------------------------------

def test_the_fake_backend_meets_the_protocol(tmp_path):
    assert isinstance(FakeBackend(tmp_path), Backend)


def test_the_fake_backend_acts_and_reads_marked_and_foreign_units(tmp_path):
    root = tmp_path
    sup = FakeSupervisor(root)
    fake = FakeBackend(root, runner=sup)
    fence = Fence(fake.supervisor, sup)
    (root / "moxi-demo-shell.service").write_text("# moxi unit: demo/shell\n")
    (root / "moxi-demo-shell.socket").write_text("# moxi unit: demo/shell\n")
    sup.extra |= {"backup.timer", "backup-run.service"}
    sup.triggers["backup.timer"] = "backup-run.service"
    sup.reload()
    refs = {(r.name, r.kind): r for r in fake.marked("demo")}
    shell = refs[("shell", "daemon")]
    assert shell.ids == ("moxi-demo-shell.service",) and shell.marked
    assert fake.act("enable", shell, fence).ok
    assert sup.calls[-1] == ("enable", "--now", "moxi-demo-shell.socket",
                             "moxi-demo-shell.service")
    assert fake.running(["moxi-demo-shell.service", "x.service"], fence) == {
        "moxi-demo-shell.service"}
    backup = fake.foreign("backup.timer", fence)
    assert backup.ids == ("backup.timer", "backup-run.service") and not backup.marked
    assert fake.act("start", backup, fence).ok and sup.calls[-1] == ("start",
                                                                      "backup-run.service")
    assert fake.own("moxi-x.service") and not fake.own("backup.timer")
    assert fake.wrap("backup.timer", module="demo", key="rows.backup", name="backup",
                     fence=fence).ok
    states = {s.name: s for s in fake.status([shell, backup], fence)}
    assert states["shell"].state == "active"
    assert states["shell"].enabled is True
    assert states["shell"].log == "journalctl --user -u moxi-demo-shell.service"
    assert states["backup.timer"].wrapped == units.Wrapped("demo", "rows.backup", "backup")
    assert fake.unwrap("backup.timer", module="demo", fence=fence).ok
    assert fake.status([backup], fence)[0].wrapped is None
    assert fake.stop_id("moxi-demo-shell.service", fence).ok
    assert fake.start_id("moxi-demo-shell.service", fence).ok
    missing = UnitRef("demo", "gone", "daemon", ("moxi-demo-gone.service",))
    [state] = fake.status([missing], fence)
    assert (state.state, state.enabled, state.log) == ("missing", None, None)
