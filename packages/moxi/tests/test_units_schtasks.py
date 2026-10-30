"""The Task Scheduler backend on Linux: the task XML it renders (parsed with
`xml.etree`), and apply, the acts and status driven through a fake `schtasks.exe` and a
fake PowerShell read. Nothing here runs a real supervisor; the rows that need Windows are
at the end and skip elsewhere."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import moxi.modules
import moxi.paths
from moxi import files, proc, units
from moxi.units import _backend, _state
from moxi.units._backend import Backend, RenderError, Schedule, Spec, UnitRef
from moxi.units._fence import Fence
from moxi.units._schtasks import BACKEND, NS, SchtasksBackend

N = {"t": NS}

SETTINGS = """
[snap.enabled]
type = "bool"
default = false
doc = "Take snapshots."
read = "use"

[port]
type = "int"
default = 8470
doc = "The port."
read = "start"
"""

DAEMON = "{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}}"
TIMER = ("{'snap': {'command': 'moxi-demo-snap', 'doc': 'take a snapshot', 'every': '5m', "
         "'switch': 'snap.enabled'}}")


class FakeSchtasks:
    """A runner for the Task Scheduler fence: `schtasks.exe` over registered tasks kept in
    memory, and the PowerShell read over them and over `foreign` tasks.

    `tasks` maps a task path to `{"enabled", "running"}`; `/create` reads `Enabled` from
    the XML it is given. `foreign` maps a task path to the read's
    `{"state", "last_run", "last_result"}`. `/end` of a task that is not running fails, as
    it may on Windows. `fail` maps `(verb, task)` to the stderr that command fails with;
    `powershell=False` makes `powershell.exe` missing. `calls` lists each command as
    `(verb, task[, arg])`, the read as `("tasks",)`.
    """

    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.foreign: dict[str, dict] = {}
        self.fail: dict[tuple[str, str | None], str] = {}
        self.calls: list[tuple[str, ...]] = []
        self.envs: list[dict] = []
        self.powershell = True

    def __call__(self, cmd, *, env=None, timeout=None):
        if cmd[0] == "powershell.exe":
            return self._read(cmd, env)
        verb = cmd[1]
        task = cmd[3] if verb != "/query" else None
        self.calls.append((verb, task, cmd[4]) if verb == "/change" else
                          (verb,) if verb == "/query" else (verb, task))
        if (verb, task) in self.fail:
            return self._done(cmd, 1, "", self.fail[(verb, task)])
        if verb == "/query":
            rows = [f'"{t}","N/A","Bereit"' for t in [*self.tasks, *self.foreign]]
            return self._done(cmd, 0, "\r\n" + "\r\n".join(rows) + "\r\n")
        if verb == "/create":
            root = ET.fromstring(Path(cmd[5]).read_bytes())
            enabled = root.find("t:Settings/t:Enabled", N).text == "true"
            old = self.tasks.get(task, {})
            self.tasks[task] = {"enabled": enabled, "running": old.get("running", False)}
            return self._done(cmd, 0, "SUCCESS")
        state = self.tasks.get(task)
        if state is None:
            return self._done(cmd, 1, "", "ERROR: The system cannot find the file specified.")
        if verb == "/delete":
            del self.tasks[task]
        elif verb == "/run":
            if not state["enabled"]:
                return self._done(cmd, 1, "", "ERROR: The task is disabled.")
            state["running"] = True
        elif verb == "/end":
            if not state["running"]:
                return self._done(cmd, 1, "", "ERROR: The task is not running.")
            state["running"] = False
        elif verb == "/change":
            state["enabled"] = cmd[4] == "/enable"
        return self._done(cmd, 0, "SUCCESS")

    def _read(self, cmd, env):
        self.calls.append(("tasks",))
        if not self.powershell:
            raise FileNotFoundError(2, "No such file or directory", "powershell.exe")
        if ("tasks", None) in self.fail:
            return self._done(cmd, 1, "", self.fail[("tasks", None)])
        self.envs.append(env)
        out = []
        for ref in json.loads(env["MOXI_TASK_REFS"]):
            want = (ref["path"] + ref["name"]).casefold()
            for path, info in self._known().items():
                if path.casefold() == want:
                    folder, _, name = path.rpartition("\\")
                    out.append({"path": folder + "\\", "name": name, **info})
        return self._done(cmd, 0, "\ufeff" + json.dumps(out))

    def _known(self) -> dict[str, dict]:
        known = {path: {"state": "Running" if s["running"] else
                        "Ready" if s["enabled"] else "Disabled",
                        "last_run": None, "last_result": 0x41303}
                 for path, s in self.tasks.items()}
        return {**known, **self.foreign}

    @staticmethod
    def _done(cmd, code, out, err=""):
        return subprocess.CompletedProcess(cmd, code, out, err)


@pytest.fixture
def w(site, tmp_path, monkeypatch):
    """A fake module `demo`, a scripts directory with `pythonw.exe`, the backend as
    `PC\\ana`, and the fake `schtasks.exe`."""
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    for name in ("python", "pythonw.exe", "moxi-demo-relay", "moxi-demo-snap"):
        path = scripts / name
        path.write_text("")
        path.chmod(0o755)
    backend = SchtasksBackend(user="PC\\ana")
    sup = FakeSchtasks()
    lines: list[str] = []

    def module(**names):
        for mod in [m for m in sys.modules if m.split(".")[0] == "moxi_demo"]:
            del sys.modules[mod]
        site.write("moxi_demo/settings.toml", SETTINGS)
        site.module("demo", SETTINGS="'settings.toml'", **names)
        moxi.modules.reset()

    def plan(**kw):
        return {c.name: c for c in units.plan("demo", **kw)}

    def apply(changes, **kw):
        lines.clear()
        sup.calls.clear()
        return units.apply(changes, say=lines.append, runner=sup, **kw)

    def ready(changes):
        """Record each switched timer's digest, as the settle step does first."""
        for change in changes:
            if change.digest:
                _state.record(change.module, change.name, change.digest)
        return changes

    with _backend.use(backend):
        yield types.SimpleNamespace(scripts=scripts, backend=backend, sup=sup, lines=lines,
                                    module=module, plan=plan, apply=apply, ready=ready,
                                    fence=Fence("schtasks", sup), tmp=tmp_path)


def spec(kind="daemon", **kw) -> Spec:
    entry = kw.pop("entry", {})
    base = dict(module="demo", name="relay" if kind == "daemon" else "snap", kind=kind,
                command=("C:\\env\\Scripts\\moxi-demo-relay.exe", "--port", "8 9"),
                doc="the relay", entry=entry)
    if kind == "timer":
        base["schedule"] = Schedule(every=5)
        base["switch"] = "snap.enabled"
    base.update(kw)
    return Spec(**base)


def render(w, s: Spec) -> tuple[Path, str, ET.Element]:
    [group] = w.backend.render(s)
    [(path, text)] = group.files
    return path, text, ET.fromstring(text.encode("utf-16"))


def args(root: ET.Element) -> list[str]:
    return root.find("t:Actions/t:Exec/t:Arguments", N).text


# -- rendering ---------------------------------------------------------------------------

def test_a_stop_process_daemon_tells_the_gate(w):
    _path, _text, root = render(w, spec(stop="process"))
    assert "--restart --runtime --stop process -- " in args(root)
    _path, _text, root = render(w, spec())
    assert "--stop" not in args(root)


def test_a_daemon_renders_the_designs_task(w):
    path, text, root = render(w, spec())
    pythonw = w.scripts / "pythonw.exe"
    log = moxi.paths.Layout("demo").state_dir() / "logs" / "relay.log"
    gate = subprocess.list2cmdline(
        ["-m", "moxi", "run", "--record", "demo/relay", "--log", str(log), "--restart",
         "--runtime", "--", "C:\\env\\Scripts\\moxi-demo-relay.exe", "--port", "8 9"])
    assert text == f"""\
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Source>moxi unit: demo/relay</Source>
    <Description>Moxi demo: the relay</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>PC\\ana</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>PC\\ana</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <StartWhenAvailable>false</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{pythonw}</Command>
      <Arguments>{gate}</Arguments>
    </Exec>
  </Actions>
</Task>
"""
    assert path == moxi.paths.core().state_dir() / "units" / "demo-relay.xml"
    assert root.find("t:RegistrationInfo/t:Source", N).text == "moxi unit: demo/relay"
    assert args(root).endswith('-- C:\\env\\Scripts\\moxi-demo-relay.exe --port "8 9"')
    assert w.backend.mark(text) == "demo/relay" and w.backend.enabled(text)


def test_a_timer_every_repeats_from_a_fixed_start_with_the_gates_if(w):
    _, text, root = render(w, spec("timer"))
    [trigger] = root.find("t:Triggers", N)
    assert trigger.tag == f"{{{NS}}}TimeTrigger"
    assert trigger.find("t:StartBoundary", N).text == "2000-01-01T00:00:00"
    assert trigger.find("t:Repetition/t:Interval", N).text == "PT5M"
    assert root.find("t:Settings/t:RestartOnFailure", N) is None
    line = args(root)
    assert "--if demo.snap.enabled --record demo/snap --log " in line
    assert "--restart" not in line and "--runtime" not in line
    assert "LogonTrigger" not in text


@pytest.mark.parametrize("every, interval", [(1, "PT1M"), (90, "PT90M"), (120, "PT2H"),
                                             (1440, "PT24H")])
def test_an_interval_is_written_in_minutes_or_whole_hours(w, every, interval):
    _, _, root = render(w, spec("timer", schedule=Schedule(every=every)))
    assert root.find(".//t:Repetition/t:Interval", N).text == interval


def test_a_switchless_timer_runs_the_gate_without_if(w):
    _, _, root = render(w, spec("timer", switch=None))
    assert "--if" not in args(root)


def test_a_timer_at_has_one_calendar_trigger_per_time(w):
    _, _, root = render(w, spec("timer", schedule=Schedule(at=((9, 0), (17, 30)))))
    triggers = list(root.find("t:Triggers", N))
    assert [t.tag for t in triggers] == [f"{{{NS}}}CalendarTrigger"] * 2
    assert [t.find("t:StartBoundary", N).text for t in triggers] == [
        "2000-01-01T09:00:00", "2000-01-01T17:30:00"]
    assert all(t.find("t:ScheduleByDay/t:DaysInterval", N).text == "1" for t in triggers)


def test_a_timer_at_on_some_days_is_weekly(w):
    _, _, root = render(w, spec("timer", schedule=Schedule(at=((3, 5),),
                                                           days=("mon", "fri"))))
    [trigger] = root.find("t:Triggers", N)
    week = trigger.find("t:ScheduleByWeek", N)
    assert [d.tag for d in week.find("t:DaysOfWeek", N)] == [
        f"{{{NS}}}Monday", f"{{{NS}}}Friday"]
    assert week.find("t:WeeksInterval", N).text == "1"
    assert trigger.find("t:ScheduleByDay", N) is None


def test_enabled_false_is_in_the_settings_and_read_back(w):
    _, text, root = render(w, spec(enabled=False))
    assert root.find("t:Settings/t:Enabled", N).text == "false"
    assert root.find(".//t:LogonTrigger/t:Enabled", N).text == "true"
    assert not w.backend.enabled(text)


def test_cwd_is_the_working_directory_and_text_is_escaped(w):
    _, _, root = render(w, spec(cwd="C:\\work & play", doc="a <b> & 'c'",
                                command=("C:\\x\\t.exe", "a&b", "<c>")))
    assert root.find("t:Actions/t:Exec/t:WorkingDirectory", N).text == "C:\\work & play"
    assert root.find("t:RegistrationInfo/t:Description", N).text == "Moxi demo: a <b> & 'c'"
    assert args(root).endswith("-- C:\\x\\t.exe a&b <c>")


def test_no_pythonw_refuses_the_unit(w):
    (w.scripts / "pythonw.exe").unlink()
    with pytest.raises(RenderError) as caught:
        w.backend.render(spec())
    assert caught.value.cause == "command" and "pythonw.exe" in caught.value.text


def test_the_backend_meets_the_protocol_and_is_the_one_found_on_windows(monkeypatch):
    assert isinstance(BACKEND, Backend)
    assert BACKEND.name == BACKEND.supervisor == "schtasks"
    monkeypatch.setattr(_backend, "backend", lambda: "schtasks")
    assert _backend.implementation() is BACKEND


# -- plan and apply ----------------------------------------------------------------------

def test_a_new_daemon_is_written_utf16_created_and_run(w):
    w.module(DAEMONS=DAEMON)
    change = w.plan()["relay"]
    assert change.action == "add"
    [done] = w.apply([change])
    assert done.ok, done.lines
    assert w.sup.calls == [("/create", "\\Moxi\\demo-relay"), ("/run", "\\Moxi\\demo-relay")]
    raw = change.paths[0].read_bytes()
    assert raw[:2] == b"\xff\xfe"
    assert ET.fromstring(raw).find("t:RegistrationInfo/t:Source", N).text == (
        "moxi unit: demo/relay")
    assert oct(change.paths[0].stat().st_mode & 0o777) == "0o644"
    assert (moxi.paths.Layout("demo").state_dir() / "logs").is_dir()
    assert w.plan()["relay"].action == "keep"
    [ref] = w.backend.marked("demo")
    assert ref == UnitRef("demo", "relay", "daemon", ("\\Moxi\\demo-relay",),
                          change.paths, True)


def test_a_new_timer_is_created_and_not_run(w):
    w.module(TIMERS=TIMER)
    [done] = w.apply(w.ready(list(w.plan().values())))
    assert done.ok
    assert w.sup.calls == [("/create", "\\Moxi\\demo-snap")]
    [ref] = w.backend.marked("demo")
    assert ref.kind == "timer" and ref.ids == ("\\Moxi\\demo-snap",)


def test_a_changed_daemon_is_ended_recreated_and_run(w):
    w.module(DAEMONS=DAEMON)
    w.apply(w.plan().values())
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'doc': 'the new relay'}}")
    change = w.plan()["relay"]
    assert change.action == "change"
    [done] = w.apply([change])
    assert done.ok
    task = "\\Moxi\\demo-relay"
    assert w.sup.calls == [("/end", task), ("/create", task), ("/run", task)]
    assert w.sup.tasks[task]["running"]


def test_a_changed_daemon_with_upgrade_keep_is_recreated_without_end_or_run(w):
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'upgrade': 'keep'}}")
    w.apply(w.plan().values())
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'upgrade': 'keep', "
                     "'doc': 'new'}}")
    [done] = w.apply(w.plan().values())
    assert done.ok
    assert w.sup.calls == [("/create", "\\Moxi\\demo-relay")]
    assert "\\Moxi\\demo-relay rewritten; the new code runs from its next start" in w.lines


def test_a_removed_unit_is_ended_deleted_and_its_xml_dropped(w):
    w.module(DAEMONS=DAEMON)
    [change] = w.plan().values()
    w.apply([change])
    w.module()
    [gone] = w.plan().values()
    assert gone.action == "remove"
    [done] = w.apply([gone])
    assert done.ok, done.lines
    task = "\\Moxi\\demo-relay"
    assert w.sup.calls == [("/end", task), ("/delete", task)]
    assert not change.paths[0].exists() and task not in w.sup.tasks


def test_a_remove_of_a_task_already_gone_drops_its_xml(w):
    w.module(DAEMONS=DAEMON)
    [change] = w.plan().values()
    w.apply([change])
    del w.sup.tasks["\\Moxi\\demo-relay"]
    w.module()
    [done] = w.apply(w.plan().values())
    assert done.ok and not change.paths[0].exists()


def test_a_failed_delete_leaves_the_task_and_its_xml(w):
    w.module(DAEMONS=DAEMON)
    [change] = w.plan().values()
    w.apply([change])
    w.sup.fail[("/delete", "\\Moxi\\demo-relay")] = "ERROR: Access is denied."
    w.module()
    [done] = w.apply(w.plan().values())
    assert not done.ok and change.paths[0].exists()
    assert any("ERROR: Access is denied." in line for line in done.lines)


def test_a_failed_create_prints_the_command_its_text_and_a_remedy(w):
    w.module(DAEMONS=DAEMON)
    w.sup.fail[("/create", "\\Moxi\\demo-relay")] = "ERROR: The task XML is malformed."
    [done] = w.apply(w.plan().values())
    assert not done.ok
    assert "/create /tn" in done.lines[0] and "ERROR: The task XML is malformed." in (
        done.lines[0])
    assert done.lines[1].startswith("the unit file is written; fix the cause and run "
                                    "`moxi install demo` again")
    assert ("/run", "\\Moxi\\demo-relay") not in w.sup.calls


def live_runtime(module: str, name: str) -> Path:
    path = SchtasksBackend.runtime_file(module, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    me = proc.identity(os.getpid())
    path.write_text(json.dumps({"pid": me.pid, "started": me.started}))
    return path


def test_restart_after_upgrade_ends_and_runs_a_running_unchanged_daemon(w):
    w.module(DAEMONS=DAEMON)
    w.apply(w.plan().values())
    live_runtime("demo", "relay")
    [keep] = w.plan().values()
    assert keep.action == "keep"
    [done] = w.apply([keep], restart=True)
    assert done.ok
    assert w.sup.calls == [("/end", "\\Moxi\\demo-relay"), ("/run", "\\Moxi\\demo-relay")]
    w.apply([keep], restart=False)
    assert w.sup.calls == []


def test_restart_after_upgrade_leaves_a_daemon_that_is_not_running(w):
    w.module(DAEMONS=DAEMON)
    w.apply(w.plan().values())
    w.apply(w.plan().values(), restart=True)
    assert w.sup.calls == []


RUNTIME_DAEMON = """
def units():
    return {{"daemons": {{"extra": {{"argv": [{tool!r}], "enabled": {enabled}}}}}}}
"""


def test_a_disabled_daemon_is_created_disabled_and_a_flip_takes_effect_now(w, site):
    tool = w.tmp / "tool.exe"
    tool.write_text("")

    def declare(enabled: bool):
        site.write("moxi_demo/runtime.py", RUNTIME_DAEMON.format(tool=str(tool),
                                                                 enabled=enabled))
        w.module(UNITS_FROM="'moxi_demo.runtime:units'")

    task = "\\Moxi\\demo-extra"
    declare(False)
    [done] = w.apply(w.plan().values())
    assert done.ok and w.sup.calls == [("/create", task)]
    assert w.sup.tasks[task] == {"enabled": False, "running": False}
    declare(True)
    [change] = w.plan().values()
    assert change.action == "change" and change.enabled_before is False
    [done] = w.apply([change])
    assert done.ok and w.sup.calls == [("/end", task), ("/create", task), ("/run", task)]
    assert w.sup.tasks[task] == {"enabled": True, "running": True}
    declare(False)
    [done] = w.apply(w.plan().values())
    assert done.ok, done.lines
    assert w.sup.calls == [("/create", task), ("/end", task)]
    assert w.sup.tasks[task] == {"enabled": False, "running": False}


def test_a_daemons_socket_is_skipped_with_one_line(w):
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', "
                     "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")
    changes = list(w.plan().values())
    assert [c.kind for c in changes] == ["daemon"]
    [done] = w.apply(changes)
    assert done.ok
    assert "demo/relay: its socket is skipped on Windows; the daemon runs without it" in (
        done.lines)


def test_an_unmarked_xml_in_the_way_is_refused_and_moved_aside_with_replace(w):
    w.module(DAEMONS=DAEMON)
    path = SchtasksBackend.xml_path("demo", "relay")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("<Task/>".encode("utf-16"))
    [change] = w.plan().values()
    assert change.action == "refuse" and change.cause == "foreign"
    [done] = w.apply([change], replace=True)
    assert done.ok
    assert w.backend.mark(path.read_bytes().decode("utf-16")) == "demo/relay"


# -- the acts ----------------------------------------------------------------------------

def registered(w, kind="daemon", *, enabled=True, running=False) -> UnitRef:
    name = "relay" if kind == "daemon" else "snap"
    task = f"\\Moxi\\demo-{name}"
    w.sup.tasks[task] = {"enabled": enabled, "running": running}
    return UnitRef("demo", name, kind, (task,))


@pytest.mark.parametrize("kind, act, before, calls, after", [
    ("daemon", "start", (True, False), [("/run",)], (True, True)),
    ("daemon", "stop", (True, True), [("/end",)], (True, False)),
    ("daemon", "restart", (True, True), [("/end",), ("/run",)], (True, True)),
    ("daemon", "enable", (False, False), [("/change", "/enable"), ("/run",)], (True, True)),
    ("daemon", "disable", (True, True), [("/change", "/disable"), ("/end",)],
     (False, False)),
    ("timer", "start", (True, False), [("/run",)], (True, True)),
    ("timer", "stop", (True, True), [("/end",)], (True, False)),
    ("timer", "enable", (False, False), [("/change", "/enable")], (True, False)),
    # A timer's disable leaves a run in progress; `stop` ends it.
    ("timer", "disable", (True, True), [("/change", "/disable")], (False, True)),
])
def test_each_act_runs_the_verb_table(w, kind, act, before, calls, after):
    unit = registered(w, kind, enabled=before[0], running=before[1])
    task = unit.ids[0]
    acted = w.backend.act(act, unit, w.fence)
    assert acted.ok, acted.lines
    assert acted.module == "demo" and acted.act == act
    assert w.sup.calls == [(c[0], task, *c[1:]) for c in calls]
    assert (w.sup.tasks[task]["enabled"], w.sup.tasks[task]["running"]) == after


def test_ending_a_task_that_is_not_running_is_no_failure(w):
    unit = registered(w, running=False)
    acted = w.backend.act("stop", unit, w.fence)
    assert acted.ok
    assert w.sup.calls == [("/end", unit.ids[0]), ("tasks",)]
    acted = w.backend.act("disable", unit, w.fence)
    assert acted.ok


def test_an_end_that_fails_on_a_running_task_is_a_failure(w):
    unit = registered(w, running=True)
    w.sup.fail[("/end", unit.ids[0])] = "ERROR: Access is denied."
    acted = w.backend.act("stop", unit, w.fence)
    assert not acted.ok and "ERROR: Access is denied." in acted.lines[0]


def test_a_failed_step_stops_the_act_with_the_supervisors_text(w):
    unit = registered(w, enabled=False)
    w.sup.fail[("/change", unit.ids[0])] = "ERROR: Access is denied."
    acted = w.backend.act("enable", unit, w.fence)
    assert not acted.ok and "ERROR: Access is denied." in acted.lines[0]
    assert ("/run", unit.ids[0]) not in w.sup.calls


def test_start_id_and_stop_id_use_run_and_end_only(w):
    unit = registered(w)
    assert w.backend.start_id(unit.ids[0], w.fence).ok
    assert w.backend.stop_id(unit.ids[0], w.fence).ok
    assert w.sup.calls == [("/run", unit.ids[0]), ("/end", unit.ids[0])]


def test_running_reads_the_live_runtime_files(w):
    live_runtime("demo", "relay")
    dead = SchtasksBackend.runtime_file("demo", "old")
    dead.write_text(json.dumps({"pid": os.getpid(), "started": 1.0}))
    ids = ["\\Moxi\\demo-relay", "\\Moxi\\demo-old", "\\Moxi\\demo-none", "\\Other\\x"]
    assert w.backend.running(ids, w.fence) == {"\\Moxi\\demo-relay"}
    assert w.sup.calls == []


def test_own_names_the_moxi_folder_and_foreign_refs_are_timers(w):
    assert w.backend.own("\\Moxi\\demo-relay") and w.backend.own("\\moxi\\Anything")
    assert not w.backend.own("\\Backups\\Daily") and not w.backend.own("Moxi")
    ref = w.backend.foreign("\\Backups\\Daily (home)", w.fence)
    assert ref == UnitRef(None, "\\Backups\\Daily (home)", "timer",
                          ("\\Backups\\Daily (home)",))
    assert w.backend.foreign("Daily", w.fence).ids == ("\\Daily",)
    assert w.sup.calls == []


def test_wrap_is_refused_and_there_is_nothing_to_unwrap(w):
    acted = w.backend.wrap("\\Backups\\Daily", module="demo", key="k", name="n",
                           fence=w.fence)
    assert not acted.ok and acted.lines == (
        "wrap is systemd only; \\Backups\\Daily runs as it is",)
    acted = w.backend.unwrap("\\Backups\\Daily", module="demo", fence=w.fence)
    assert acted.ok and acted.lines == ("\\Backups\\Daily is not wrapped by demo",)
    assert w.backend.unwrap_all("demo", w.fence) == []


# -- status ------------------------------------------------------------------------------

def run_line(module, name, code):
    files.append_record(moxi.paths.Layout(module).state_dir() / "runs.jsonl",
                        {"unit": f"{module}/{name}", "at": "2026-01-01T00:00:00Z",
                         "seconds": 0.5, "result": "ran", "exit": code})


def test_status_reads_marked_tasks_by_position_and_the_runtime_file(w):
    relay = registered(w, running=True)
    snap = registered(w, "timer")
    gone = UnitRef("demo", "gone", "daemon", ("\\Moxi\\demo-gone",))
    live_runtime("demo", "relay")
    run_line("demo", "snap", 0)
    run_line("demo", "snap", 2)
    states = {s.name: s for s in w.backend.status([relay, snap, gone], w.fence)}
    assert w.sup.calls == [("/query",), ("tasks",)]
    me = proc.identity(os.getpid())
    r = states["relay"]
    assert (r.state, r.pid, r.since, r.enabled, r.backend) == (
        "active", me.pid, me.started, True, "schtasks")
    assert r.log == str(moxi.paths.Layout("demo").state_dir() / "logs" / "relay.log")
    s = states["snap"]
    assert s.state == "failed" and s.last["exit"] == 2 and s.enabled is True
    g = states["gone"]
    assert (g.state, g.enabled, g.log) == ("missing", None, None)


def test_a_listed_daemon_without_a_live_runtime_file_is_inactive(w):
    relay = registered(w)
    run_line("demo", "relay", 0)
    [state] = w.backend.status([relay], w.fence)
    assert state.state == "inactive" and state.pid is None and state.last["exit"] == 0


def test_a_disabled_marked_task_reads_enabled_false(w):
    relay = registered(w, enabled=False)
    [state] = w.backend.status([relay], w.fence)
    assert state.enabled is False and state.state == "inactive"


def test_without_powershell_enabled_is_none_and_the_state_is_read_as_before(w):
    relay = registered(w)
    live_runtime("demo", "relay")
    w.sup.powershell = False
    [state] = w.backend.status([relay], w.fence)
    assert state.state == "active" and state.enabled is None


def test_a_failed_query_reads_unknown(w):
    relay = registered(w)
    w.sup.fail[("/query", None)] = "ERROR: The RPC server is unavailable."
    [state] = w.backend.status([relay], w.fence)
    assert state.state == "unknown" and "RPC server" in state.detail


@pytest.mark.parametrize("info, state, enabled", [
    ({"state": "Running", "last_run": "2026-01-01T00:00:00", "last_result": 0x41301},
     "active", True),
    ({"state": "Ready", "last_run": None, "last_result": 0x41303}, "inactive", True),
    ({"state": "Ready", "last_run": "2026-01-01T00:00:00", "last_result": 0},
     "inactive", True),
    ({"state": "Ready", "last_run": "2026-01-01T00:00:00", "last_result": 1}, "failed", True),
    ({"state": "Ready", "last_run": "x", "last_result": 0x80070002}, "failed", True),
    ({"state": "Disabled", "last_run": None, "last_result": 0x41306}, "inactive", False),
])
def test_a_foreign_task_is_read_from_the_powershell_call(w, info, state, enabled):
    w.sup.foreign["\\Backups\\Daily (home)"] = info
    ref = w.backend.foreign("\\Backups\\Daily (home)", w.fence)
    [read] = w.backend.status([ref], w.fence)
    assert w.sup.calls == [("tasks",)]
    assert (read.module, read.state, read.enabled) == (None, state, enabled)
    assert read.last == {"last_run": info["last_run"], "last_result": info["last_result"]}
    assert read.since is None and read.log is None


def test_foreign_refs_reach_powershell_only_in_its_environment(w):
    name = "a b 'c\" ;$d"
    w.sup.foreign[f"\\MoxiTest\\{name}"] = {"state": "Ready", "last_run": None,
                                            "last_result": 0}
    ref = w.backend.foreign(f"\\MoxiTest\\{name}", w.fence)
    missing = w.backend.foreign("\\Backups\\Gone", w.fence)
    read, gone = w.backend.status([ref, missing], w.fence)
    assert read.state == "inactive" and gone.state == "missing" and gone.enabled is None
    [env] = w.sup.envs
    assert json.loads(env["MOXI_TASK_REFS"]) == [
        {"path": "\\MoxiTest\\", "name": name}, {"path": "\\Backups\\", "name": "Gone"}]


def test_a_foreign_ref_matches_regardless_of_case(w):
    w.sup.foreign["\\Backups\\Daily"] = {"state": "Ready", "last_run": None,
                                         "last_result": 0}
    [read] = w.backend.status([w.backend.foreign("\\backups\\daily", w.fence)], w.fence)
    assert read.state == "inactive"


@pytest.mark.parametrize("missing_powershell", [True, False])
def test_a_foreign_task_reads_unknown_when_the_read_fails(w, missing_powershell):
    if missing_powershell:
        w.sup.powershell = False
    else:
        w.sup.fail[("tasks", None)] = "Get-ScheduledTask : Access is denied."
    ref = w.backend.foreign("\\Backups\\Daily", w.fence)
    [read] = w.backend.status([ref], w.fence)
    assert read.state == "unknown" and read.enabled is None
    assert ("did not run" if missing_powershell else "Access is denied") in read.detail


def test_marked_and_foreign_share_one_powershell_call(w):
    relay = registered(w)
    w.sup.foreign["\\Backups\\Daily"] = {"state": "Running", "last_run": None,
                                         "last_result": 0}
    states = w.backend.status([relay, w.backend.foreign("\\Backups\\Daily", w.fence)],
                              w.fence)
    assert [s.state for s in states] == ["inactive", "active"]
    assert w.sup.calls == [("/query",), ("tasks",)]


# -- python -m moxi ----------------------------------------------------------------------

def test_python_dash_m_moxi_is_the_moxi_command():
    done = subprocess.run([sys.executable, "-m", "moxi", "--version"], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.startswith("moxi ")


def test_python_dash_m_moxi_runs_with_no_standard_streams(tmp_path):
    """As under `pythonw.exe`, where the three streams are None."""
    code = ("import runpy, sys\n"
            "sys.stdin = sys.stdout = sys.stderr = None\n"
            "sys.argv = ['moxi', '--version']\n"
            "try:\n"
            "    runpy.run_module('moxi', run_name='__main__')\n"
            "except SystemExit as exc:\n"
            f"    open({str(tmp_path / 'code')!r}, 'w').write(str(exc.code))\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "code").read_text() == "0"


# -- Windows rows (Mini 2) ---------------------------------------------------------------

windows = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("MOXI_TEST_REAL_SUPERVISOR") != "1",
    reason="a real Task Scheduler row: Windows with MOXI_TEST_REAL_SUPERVISOR=1, as a "
           "non-admin account")


@windows
def test_a_real_task_under_a_test_folder_is_created_run_read_ended_and_deleted(tmp_path):
    """Registers `\\MoxiTest\\<hex>-probe` from a rendered XML whose gate is replaced by a
    short sleep, and walks it through the verbs and the reads."""
    import secrets

    from moxi.units._fence import Fence as RealFence

    backend = SchtasksBackend(folder="\\MoxiTest\\")
    fence = RealFence("schtasks")
    module = "t" + secrets.token_hex(4)
    task = backend.task(module, "probe")
    xml = tmp_path / "probe.xml"
    text = ('<?xml version="1.0" encoding="UTF-16"?>\n'
            f'<Task version="1.2" xmlns="{NS}"><RegistrationInfo><Source>moxi unit: '
            f'{module}/probe</Source></RegistrationInfo><Principals><Principal id="Author">'
            f"<UserId>{backend.user()}</UserId><LogonType>InteractiveToken</LogonType>"
            "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings>"
            "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
            "<Enabled>true</Enabled></Settings><Actions Context=\"Author\"><Exec>"
            "<Command>cmd.exe</Command><Arguments>/c ping -n 30 127.0.0.1</Arguments>"
            "</Exec></Actions></Task>\n")
    xml.write_bytes(text.encode("utf-16"))
    try:
        assert fence.run("/create", task, str(xml)).ok
        unit = UnitRef(module, "probe", "timer", (task,))
        assert backend.act("start", unit, fence).ok
        assert backend.act("start", unit, fence).ok  # already running: done
        [state] = backend.status([unit], fence)
        assert state.state in ("inactive", "failed") and state.enabled is True
        assert backend.act("disable", unit, fence).ok
        [state] = backend.status([unit], fence)
        assert state.enabled is False
        assert backend.act("stop", unit, fence).ok
        assert backend.act("stop", unit, fence).ok  # not running: done
    finally:
        fence.run("/end", task)
        fence.run("/delete", task)
