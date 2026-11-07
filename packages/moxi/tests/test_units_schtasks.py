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
from datetime import datetime, timezone
from pathlib import Path

import pytest

import moxi.modules
import moxi.paths
from moxi import files, proc, units
from moxi.units import _backend, _state
from moxi.units._backend import Backend, RenderError, Schedule, Spec, UnitRef
from moxi.units._fence import Fence
from moxi.units._schtasks import BACKEND, NS, SchtasksBackend
from moxi_dev.testing import console_script

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
    `{"state", "last_run", "last_result"[, "enabled"]}`. `/end` of a task that is not
    running fails, as it may on Windows. `fail` maps `(verb, task)` to the stderr that
    command fails with; `powershell=False` makes `powershell.exe` missing. `calls` lists
    each command as `(verb, task[, arg])`, the read as `("tasks",)`.

    A foreign task takes `/run`, `/end` and `/change` too. As on Windows, `State` follows
    `/run` and `/end` late: the read shows the change only after `lag[task]` reads (0 by
    default) have still shown the task as it was. `/run` makes it `Running` with
    `LastRunTime` `RUN_AT` and result 0x41301; `/end` makes it `Ready` with 0x41306. A
    task in `stuck` never changes after `/run` or `/end`. `after_run[task]`, when set, is
    the item the read shows once the lag has passed after `/run`, in place of `Running`.
    """

    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.foreign: dict[str, dict] = {}
        self.fail: dict[tuple[str, str | None], str] = {}
        self.calls: list[tuple[str, ...]] = []
        self.envs: list[dict] = []
        self.powershell = True
        self.lag: dict[str, int] = {}
        self.stuck: set[str] = set()
        self.after_run: dict[str, dict] = {}
        self._pending: dict[str, list] = {}

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
        if task in self.foreign:
            return self._foreign_act(cmd, verb, task)
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

    def _foreign_act(self, cmd, verb, task):
        info = self.foreign[task]
        if verb == "/change":
            on = cmd[4] == "/enable"
            info["enabled"] = on
            if info["state"] != "Running":
                info["state"] = "Ready" if on else "Disabled"
            return self._done(cmd, 0, "SUCCESS")
        if verb == "/run":
            if info.get("enabled") is False or info["state"] == "Disabled":
                return self._done(cmd, 1, "", "ERROR: The task is disabled.")
            after = self.after_run.get(task) or {"state": "Running", "last_run": RUN_AT,
                                                 "last_result": 0x41301}
        elif verb == "/end":
            if info["state"] != "Running":
                return self._done(cmd, 1, "", "ERROR: The task is not running.")
            after = {"state": "Ready", "last_result": 0x41306}
        else:
            return self._done(cmd, 1, "", f"ERROR: {verb} of a foreign task")
        if task not in self.stuck:
            self._pending[task] = [self.lag.get(task, 0), after]
        return self._done(cmd, 0, "SUCCESS")

    def _read(self, cmd, env):
        self.calls.append(("tasks",))
        for task, pending in list(self._pending.items()):
            if pending[0] > 0:
                pending[0] -= 1
            else:
                self.foreign[task].update(pending[1])
                del self._pending[task]
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


#: The `LastRunTime` a foreign task's `/run` gives in the fake, and an earlier one.
RUN_AT = "2026-11-07T09:30:00.1234567+00:00"
EARLIER = "2026-11-07T08:00:00.1234567+00:00"


class Clock:
    """A clock and a sleep for the backend that never wait: `sleep` moves `now` on."""

    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def w(site, tmp_path, monkeypatch):
    """A fake module `demo`, a scripts directory with `pythonw.exe`, the backend as
    `PC\\ana` on a fake clock, and the fake `schtasks.exe`. The scripts directory holds
    `python` under both systems' names, so `moxi.paths.python` finds it on either."""
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    for name in ("python", "python.exe", "pythonw.exe"):
        path = scripts / name
        path.write_text("")
        path.chmod(0o755)
    for name in ("moxi-demo-relay", "moxi-demo-snap"):
        console_script(scripts, name)
    clock = Clock()
    backend = SchtasksBackend(user="PC\\ana", clock=clock, sleep=clock.sleep)
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
                                    fence=Fence("schtasks", sup), tmp=tmp_path,
                                    clock=clock)


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


def test_without_powershell_ending_a_stopped_task_still_fails_and_says_why(w):
    """The residual: with no PowerShell read, a failed `/end` cannot be told from an idle
    task, so it stands, with a note that the check could not run."""
    unit = registered(w, running=False)
    w.sup.powershell = False
    acted = w.backend.act("stop", unit, w.fence)
    assert not acted.ok
    assert "The task is not running." in acted.lines[0]
    assert "could not be checked" in acted.lines[0]


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
    # A timer reads its schedule: armed is `active` whatever its last run did; the run line
    # is the core's to add to `last`, and the PowerShell read gives a marked task none.
    assert s.state == "active" and s.last is None and s.enabled is True
    g = states["gone"]
    assert (g.state, g.enabled, g.log) == ("missing", None, None)


def test_a_listed_daemon_without_a_live_runtime_file_is_inactive(w):
    relay = registered(w)
    run_line("demo", "relay", 0)
    [state] = w.backend.status([relay], w.fence)
    assert state.state == "inactive" and state.pid is None and state.last is None


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


@pytest.mark.parametrize("enabled, running, code, state", [
    (True, False, 0, "active"),
    (True, False, 2, "active"),  # a failed last run: the run line says it, not the state
    (True, True, None, "active"),
    (False, False, 0, "inactive"),
])
def test_a_marked_timer_reads_its_schedule_never_its_runs(w, enabled, running, code, state):
    snap = registered(w, "timer", enabled=enabled, running=running)
    if code is not None:
        run_line("demo", "snap", code)
    [read] = w.backend.status([snap], w.fence)
    assert (read.state, read.enabled, read.pid, read.last) == (state, enabled, None, None)


def test_no_task_names_a_triggered_unit(w):
    """A task is its own job: a marked timer and a foreign task carry no `triggers`."""
    snap = registered(w, "timer")
    w.sup.foreign["\\Backups\\Daily"] = {"state": "Ready", "last_run": None,
                                         "last_result": 0}
    states = w.backend.status([snap, w.backend.foreign("\\Backups\\Daily", w.fence)],
                              w.fence)
    assert [(s.kind, s.state, s.triggers) for s in states] == [("timer", "active", None)] * 2


def test_a_marked_timer_without_powershell_reads_active_and_says_why(w):
    snap = registered(w, "timer", enabled=False)
    run_line("demo", "snap", 2)
    w.sup.powershell = False
    [read] = w.backend.status([snap], w.fence)
    assert (read.state, read.enabled) == ("active", None)
    assert "whether it is disabled could not be checked" in read.detail
    assert "did not run" in read.detail  # the reason the read gave
    w.sup.powershell = True
    w.sup.fail[("tasks", None)] = "Get-ScheduledTask : Access is denied."
    [read] = w.backend.status([snap], w.fence)
    assert (read.state, read.enabled) == ("active", None) and "Access is denied" in read.detail


def test_a_failed_query_reads_unknown(w):
    relay = registered(w)
    w.sup.fail[("/query", None)] = "ERROR: The RPC server is unavailable."
    [state] = w.backend.status([relay], w.fence)
    assert state.state == "unknown" and "RPC server" in state.detail


def test_a_failed_query_makes_every_marked_unit_it_covers_unknown(w):
    """Daemon and timer alike; a foreign task, read by the PowerShell call instead, is
    not covered by the query and reads as ever."""
    relay = registered(w, running=True)
    snap = registered(w, "timer")
    live_runtime("demo", "relay")
    w.sup.foreign["\\Backups\\Daily"] = {"state": "Ready", "last_run": None,
                                         "last_result": 0}
    w.sup.fail[("/query", None)] = "ERROR: The RPC server is unavailable."
    states = w.backend.status([relay, snap, w.backend.foreign("\\Backups\\Daily", None)],
                              w.fence)
    assert [s.state for s in states] == ["unknown", "unknown", "active"]
    assert all("RPC server" in s.detail for s in states[:2])


@pytest.mark.parametrize("info, state, enabled", [
    ({"state": "Running", "last_run": "2026-01-01T00:00:00", "last_result": 0x41301},
     "active", True),
    ({"state": "Queued", "last_run": None, "last_result": 0x41303}, "active", True),
    ({"state": "Ready", "last_run": None, "last_result": 0x41303}, "active", True),
    ({"state": "Ready", "last_run": "2026-01-01T00:00:00", "last_result": 0},
     "active", True),
    # A failed last run leaves the schedule armed: still `active`, the result in `last`.
    ({"state": "Ready", "last_run": "2026-01-01T00:00:00", "last_result": 1}, "active", True),
    ({"state": "Ready", "last_run": "x", "last_result": 0x80070002}, "active", True),
    ({"state": "Disabled", "last_run": None, "last_result": 0x41306}, "inactive", False),
    ({"state": "Disabled", "last_run": "x", "last_result": 1}, "inactive", False),
])
def test_a_foreign_task_is_read_as_a_timer_from_the_powershell_call(w, info, state,
                                                                     enabled):
    w.sup.foreign["\\Backups\\Daily (home)"] = info
    ref = w.backend.foreign("\\Backups\\Daily (home)", w.fence)
    [read] = w.backend.status([ref], w.fence)
    assert w.sup.calls == [("tasks",)]
    assert (read.module, read.kind, read.state, read.enabled) == (None, "timer", state,
                                                                  enabled)
    assert read.detail.startswith(info["state"])
    assert read.last == {"last_run": info["last_run"], "last_result": info["last_result"]}
    assert read.since is None and read.log is None


def test_foreign_refs_reach_powershell_only_in_its_environment(w):
    name = "a b 'c\" ;$d"
    w.sup.foreign[f"\\MoxiTest\\{name}"] = {"state": "Ready", "last_run": None,
                                            "last_result": 0}
    ref = w.backend.foreign(f"\\MoxiTest\\{name}", w.fence)
    missing = w.backend.foreign("\\Backups\\Gone", w.fence)
    read, gone = w.backend.status([ref, missing], w.fence)
    assert read.state == "active" and gone.state == "missing" and gone.enabled is None
    [env] = w.sup.envs
    assert json.loads(env["MOXI_TASK_REFS"]) == [
        {"path": "\\MoxiTest\\", "name": name}, {"path": "\\Backups\\", "name": "Gone"}]


def test_a_foreign_ref_matches_regardless_of_case(w):
    w.sup.foreign["\\Backups\\Daily"] = {"state": "Ready", "last_run": None,
                                         "last_result": 0}
    [read] = w.backend.status([w.backend.foreign("\\backups\\daily", w.fence)], w.fence)
    assert read.state == "active"


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


# -- foreign with no fence, ids, wraps, and a read that fails --------------------------------

def test_foreign_with_no_fence_is_the_same_ref_and_makes_no_call(w):
    for ref in ("\\Backups\\Daily", "Daily"):
        assert w.backend.foreign(ref, None) == w.backend.foreign(ref, w.fence)
    assert w.sup.calls == []


def test_ids_is_the_task_path_whether_or_not_it_is_registered(w):
    assert w.backend.ids("demo", "relay", "daemon") == ("\\Moxi\\demo-relay",)
    assert w.backend.ids("demo", "snap", "timer") == ("\\Moxi\\demo-snap",)
    w.module(DAEMONS=DAEMON)
    w.apply(w.plan().values())
    [ref] = w.backend.marked("demo")
    assert ref.ids == w.backend.ids("demo", "relay", "daemon")
    assert SchtasksBackend(folder="\\MoxiTest\\").ids("demo", "relay", "daemon") == (
        "\\MoxiTest\\demo-relay",)


def test_wraps_is_empty_on_task_scheduler(w):
    assert w.backend.wraps("demo") == () and w.sup.calls == []


@pytest.mark.parametrize("bad", ["folder", "not json", "no pid"])
def test_running_raises_on_a_runtime_file_it_cannot_read(w, bad):
    path = SchtasksBackend.runtime_file("demo", "relay")
    path.parent.mkdir(parents=True, exist_ok=True)
    if bad == "folder":
        path.mkdir()
    else:
        path.write_text("{not json" if bad == "not json" else json.dumps({"started": 1.0}))
    with pytest.raises(_backend.ReadError) as raised:
        w.backend.running(["\\Moxi\\demo-relay"], w.fence)
    assert raised.value.ran.argv == ("read", str(path)) and not raised.value.ran.ok
    assert str(path) in str(raised.value)
    # A task with no runtime file, or outside the folder, is simply not running.
    assert w.backend.running(["\\Moxi\\demo-none", "\\Other\\x"], w.fence) == frozenset()
    assert w.sup.calls == []


def test_status_reads_an_unreadable_runtime_file_as_not_running(w):
    relay = registered(w)
    path = SchtasksBackend.runtime_file("demo", "relay")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    [state] = w.backend.status([relay], w.fence)
    assert state.state == "inactive" and state.pid is None


def test_restart_after_upgrade_fails_its_daemons_when_a_runtime_file_cannot_be_read(w):
    w.module(DAEMONS=DAEMON)
    w.apply(w.plan().values())
    path = SchtasksBackend.runtime_file("demo", "relay")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    [keep] = w.plan().values()
    [done] = w.apply([keep], restart=True)
    assert not done.ok and w.sup.calls == []
    assert any(str(path) in line for line in done.lines)


def test_a_failed_runtime_read_stops_nothing_and_records_nothing(w):
    w.module(DAEMONS=DAEMON)
    w.apply(w.plan().values())
    w.sup.tasks["\\Moxi\\demo-relay"]["running"] = True
    path = SchtasksBackend.runtime_file("demo", "relay")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    w.sup.calls.clear()
    record: list = []
    [done] = units.stop("demo", record=record, runner=w.sup)
    assert not done.ok and done.lines[0].startswith("cannot tell which units of demo run")
    assert record == [] and w.sup.calls == []


def test_stop_with_replace_ends_an_unmarked_task_by_its_path_and_resume_runs_it(w):
    w.module(DAEMONS=DAEMON)
    task = "\\Moxi\\demo-relay"
    w.sup.tasks[task] = {"enabled": True, "running": True}
    live_runtime("demo", "relay")
    record: list = []
    [done] = units.stop("demo", replace=True, record=record, runner=w.sup)
    assert done.ok and done.lines == (f"{task} stopped",)
    assert [(e.unit, e.marked, e.backend) for e in record] == [(task, False, "schtasks")]
    assert w.sup.calls == [("/end", task)] and not w.sup.tasks[task]["running"]
    [started] = units.resume(record, runner=w.sup)
    assert started.ok and w.sup.tasks[task]["running"]


def test_status_gives_one_state_per_unit_in_order_with_only_the_supervisors_result(w):
    relay = registered(w, running=True)
    snap = registered(w, "timer")
    info = {"state": "Ready", "last_run": "2026-01-01T00:00:00", "last_result": 1}
    w.sup.foreign["\\Backups\\Daily"] = info
    run_line("demo", "snap", 2)
    refs = [w.backend.foreign("\\Backups\\Daily", None), snap, relay,
            w.backend.foreign("\\Backups\\Gone", None)]
    states = w.backend.status(refs, w.fence)
    assert w.sup.calls == [("/query",), ("tasks",)]
    assert [(s.module, s.name, s.kind) for s in states] == [
        (r.module, r.name, r.kind) for r in refs]
    assert [s.state for s in states] == ["active", "active", "inactive", "missing"]
    assert states[0].last == {"last_run": info["last_run"], "last_result": 1}
    assert [s.last for s in states[1:]] == [None, None, None]


def test_status_through_the_core_adds_the_run_line(w):
    w.module(DAEMONS=DAEMON)
    w.apply(w.plan().values())
    run_line("demo", "relay", 3)
    w.sup.foreign["\\Backups\\Daily"] = {"state": "Ready", "last_run": None,
                                             "last_result": 0}
    w.sup.calls.clear()
    rows = units.status("demo", foreign=["\\Backups\\Daily"], runner=w.sup)
    assert w.sup.calls == [("/query",), ("tasks",)]
    assert [(r.module, r.name) for r in rows] == [("demo", "relay"),
                                                  (None, "\\Backups\\Daily")]
    relay, daily = rows
    assert relay.state == "failed" and relay.last["exit"] == 3
    assert "supervisor" not in relay.last
    assert daily.last == {"supervisor": {"last_run": None, "last_result": 0}}


# -- a foreign task read and acted on as a daemon --------------------------------------------

DAILY = "\\Backups\\Daily"
AGENT = "\\Vendor\\Agent"


def test_foreign_takes_the_callers_kind_and_makes_no_call(w):
    assert w.backend.foreign(AGENT, None, kind="daemon") == UnitRef(None, AGENT, "daemon",
                                                                    (AGENT,))
    assert w.backend.foreign(AGENT, w.fence, kind="timer").kind == "timer"
    assert w.backend.foreign(AGENT, None).kind == "timer"
    with pytest.raises(ValueError):
        w.backend.foreign(AGENT, None, kind="service")
    assert w.sup.calls == []


def test_status_and_the_acts_without_kinds_call_an_old_foreign_without_the_keyword(w):
    """A backend whose `foreign` has the signature from before the hint still serves every
    caller that passes no `kinds`."""

    class Old(SchtasksBackend):
        def foreign(self, ref, fence):
            return super().foreign(ref, fence)

    w.sup.foreign[DAILY] = {"state": "Ready", "last_run": None, "last_result": 0}
    with _backend.use(Old(user="PC\\ana")):
        [row] = units.status([], foreign=[DAILY], runner=w.sup)
        [acted] = units.enable("services", [DAILY], foreign=True, runner=w.sup)
    assert (row.kind, row.state) == ("timer", "active")
    assert acted.ok and ("/change", DAILY, "/enable") in w.sup.calls


def daemon_read(w, info):
    w.sup.foreign[AGENT] = info
    [read] = w.backend.status([w.backend.foreign(AGENT, None, kind="daemon")], w.fence)
    assert w.sup.calls == [("tasks",)]
    assert (read.module, read.name, read.kind, read.pid) == (None, AGENT, "daemon", None)
    assert read.last == {"last_run": info["last_run"], "last_result": info["last_result"]}
    return read


@pytest.mark.parametrize("state, code, read_as", [
    ("Ready", 0, "inactive"),
    ("Ready", None, "inactive"),
    ("Ready", 1, "failed"),
    ("Ready", 0x41303, "inactive"),       # has not yet run
    ("Ready", 0x41306, "inactive"),       # terminated by /end
    ("Ready", 0x41325, "inactive"),       # queued: outside the old 0x4131F bound
    ("Ready", 0x80041320, "failed"),      # SCHED_E_TASK_NOT_READY
    ("Ready", 0x80070002, "failed"),      # the action's file is not found
    ("Ready", -2147024894, "failed"),     # the same code read as a signed int
    ("Queued", 0x41325, "inactive"),
    ("Queued", None, "inactive"),
    ("Disabled", 1, "inactive"),
])
def test_a_foreign_task_read_as_a_daemon_runs_only_while_running(w, state, code, read_as):
    info = {"state": state, "last_run": EARLIER, "last_result": code}
    read = daemon_read(w, info)
    assert read.state == read_as and read.since is None
    assert read.enabled is (state != "Disabled")
    if code is None:
        assert read.detail == state
    else:
        assert read.detail == f"{state}, last result 0x{code & 0xFFFFFFFF:08X}"


def test_a_running_foreign_daemon_task_is_active_since_its_last_run(w):
    read = daemon_read(w, {"state": "Running", "last_run": RUN_AT, "last_result": 0x41301})
    assert (read.state, read.enabled) == ("active", True)
    assert read.detail == "Running, last result 0x00041301"
    assert read.since == datetime(2026, 11, 7, 9, 30, 0, 123456, timezone.utc).timestamp()


def test_a_running_daemon_task_with_an_unparseable_last_run_has_no_since(w):
    read = daemon_read(w, {"state": "Running", "last_run": "soon", "last_result": 0})
    assert read.state == "active" and read.since is None


def test_a_daemon_tasks_unknown_state_is_unknown(w):
    read = daemon_read(w, {"state": "Unknown", "last_run": None, "last_result": 0})
    assert (read.state, read.detail) == ("unknown", "Task Scheduler reads Unknown")


@pytest.mark.parametrize("state, enabled, read_as", [
    ("Running", False, "active"),    # disabled outside moxi while it runs
    ("Ready", False, "inactive"),
    ("Disabled", True, "inactive"),  # the setting wins over State
])
def test_a_daemon_tasks_enabled_is_its_own_setting_apart_from_state(w, state, enabled,
                                                                   read_as):
    read = daemon_read(w, {"state": state, "last_run": EARLIER, "last_result": 0,
                           "enabled": enabled})
    assert (read.state, read.enabled) == (read_as, enabled)


def test_a_timer_ignores_the_tasks_enabled_setting(w):
    w.sup.foreign[DAILY] = {"state": "Ready", "last_run": None, "last_result": 0,
                            "enabled": False}
    [read] = w.backend.status([w.backend.foreign(DAILY, None)], w.fence)
    assert (read.kind, read.state, read.enabled) == ("timer", "active", True)


def test_task_status_codes_is_task_schedulers_success_band():
    assert units.TASK_STATUS_CODES == range(0x41300, 0x41400)
    for code in (0x41300, 0x41303, 0x41325, 0x413FF):
        assert code in units.TASK_STATUS_CODES
    for code in (0, 1, 0x412FF, 0x41400, 0x80041320):
        assert code not in units.TASK_STATUS_CODES


def test_the_task_read_script_reads_the_tasks_own_enabled_setting():
    from moxi.units._fence import TASKS_SCRIPT

    assert "enabled = [bool]$t.Settings.Enabled; " in TASKS_SCRIPT


def agent(w, state="Ready", code=0x41303, last_run=EARLIER, **more):
    w.sup.foreign[AGENT] = {"state": state, "last_run": last_run, "last_result": code,
                            **more}
    w.sup.calls.clear()
    return w.backend.foreign(AGENT, None, kind="daemon")


def test_start_of_a_running_daemon_task_only_reads_it(w):
    acted = w.backend.act("start", agent(w, "Running", 0x41301), w.fence)
    assert acted.ok and acted.lines == ()
    assert w.sup.calls == [("tasks",)]


def test_start_runs_the_task_and_reads_until_it_runs(w):
    unit = agent(w)
    w.sup.lag[AGENT] = 1
    acted = w.backend.act("start", unit, w.fence)
    assert acted.ok and acted.lines == ()
    assert w.sup.calls == [("tasks",), ("/run", AGENT), ("tasks",), ("tasks",)]
    assert w.sup.foreign[AGENT]["state"] == "Running"
    assert w.clock.slept == [0.25]


def test_start_of_a_crashed_task_waits_through_reads_of_the_old_crash(w):
    """The two reads after `/run` still show the crash from before the act, `failed`
    when read as a status; neither ends the wait."""
    unit = agent(w, "Ready", 1)
    w.sup.lag[AGENT] = 2
    acted = w.backend.act("start", unit, w.fence)
    assert acted.ok
    assert w.sup.calls == [("tasks",), ("/run", AGENT)] + [("tasks",)] * 3
    assert w.sup.foreign[AGENT]["state"] == "Running"


def test_a_run_that_started_and_ended_since_the_act_ends_the_wait(w):
    """A newer `LastRunTime` ends it, though the task never read `Running`: the run
    crashed at once."""
    unit = agent(w, "Ready", 1)
    w.sup.after_run[AGENT] = {"state": "Ready", "last_run": RUN_AT, "last_result": 1}
    acted = w.backend.act("start", unit, w.fence)
    assert acted.ok and w.sup.calls == [("tasks",), ("/run", AGENT), ("tasks",)]


@pytest.mark.parametrize("mark", [None, "not a time"])
def test_with_no_mark_any_last_run_that_parses_ends_the_wait(w, mark):
    """A task that never ran has no `LastRunTime`, and one may not parse: then any
    `LastRunTime` that parses shows a run since the act."""
    unit = agent(w, "Ready", 0x41303, last_run=mark)
    w.sup.after_run[AGENT] = {"state": "Ready", "last_run": RUN_AT, "last_result": 1}
    acted = w.backend.act("start", unit, w.fence)
    assert acted.ok and w.sup.calls == [("tasks",), ("/run", AGENT), ("tasks",)]


def test_with_no_mark_a_last_run_that_still_doesnt_parse_doesnt_end_the_wait(w):
    unit = agent(w, "Ready", 0x41303, last_run="not a time")
    w.sup.stuck.add(AGENT)
    acted = w.backend.act("start", unit, w.fence)
    assert acted.ok and w.clock.now >= 5.0


def test_start_of_a_task_that_never_reads_running_is_ok_after_five_seconds(w):
    unit = agent(w)
    w.sup.stuck.add(AGENT)
    acted = w.backend.act("start", unit, w.fence)
    assert acted.ok and acted.lines == ()
    assert w.clock.now == pytest.approx(5.0)
    reads = [c for c in w.sup.calls if c == ("tasks",)]
    assert len(reads) == 1 + 21  # the one-copy read, then one each 0.25 s up to 5 s


def test_restart_ends_waits_for_the_end_then_runs(w):
    unit = agent(w, "Running", 0x41301)
    w.sup.lag[AGENT] = 1
    acted = w.backend.act("restart", unit, w.fence)
    assert acted.ok
    assert w.sup.calls == [("tasks",), ("/end", AGENT), ("tasks",), ("tasks",),
                           ("/run", AGENT), ("tasks",), ("tasks",)]
    assert w.sup.foreign[AGENT]["state"] == "Running"


def test_restart_whose_end_doesnt_settle_fails_and_never_runs(w):
    unit = agent(w, "Running", 0x41301)
    w.sup.stuck.add(AGENT)
    acted = w.backend.act("restart", unit, w.fence)
    assert not acted.ok
    assert acted.lines == (f"{AGENT} still runs 5 s after /end; not started again",)
    assert ("/run", AGENT) not in w.sup.calls


def test_stop_whose_end_doesnt_settle_is_still_ok(w):
    unit = agent(w, "Running", 0x41301)
    w.sup.stuck.add(AGENT)
    acted = w.backend.act("stop", unit, w.fence)
    assert acted.ok and w.clock.now >= 5.0


def test_stop_of_an_idle_daemon_task_is_done(w):
    acted = w.backend.act("stop", agent(w), w.fence)
    assert acted.ok
    assert w.sup.calls == [("tasks",), ("/end", AGENT), ("tasks",), ("tasks",)]


def test_enable_turns_the_task_on_then_runs_it(w):
    unit = agent(w, "Disabled", 0x41306, enabled=False)
    acted = w.backend.act("enable", unit, w.fence)
    assert acted.ok
    assert w.sup.calls == [("tasks",), ("/change", AGENT, "/enable"), ("/run", AGENT),
                           ("tasks",)]
    assert w.sup.foreign[AGENT]["state"] == "Running"


def test_enable_of_a_task_running_while_disabled_doesnt_start_a_second_copy(w):
    unit = agent(w, "Running", 0x41301, enabled=False)
    acted = w.backend.act("enable", unit, w.fence)
    assert acted.ok
    assert w.sup.calls == [("tasks",), ("/change", AGENT, "/enable")]


def test_disable_turns_the_task_off_then_ends_it(w):
    unit = agent(w, "Running", 0x41301)
    acted = w.backend.act("disable", unit, w.fence)
    assert acted.ok
    assert w.sup.calls == [("tasks",), ("/change", AGENT, "/disable"), ("/end", AGENT),
                           ("tasks",)]
    assert w.sup.foreign[AGENT]["state"] == "Ready" and not w.sup.foreign[AGENT]["enabled"]


@pytest.mark.parametrize("missing_powershell", [True, False])
def test_start_when_the_read_fails_runs_unchecked_and_says_so(w, missing_powershell):
    unit = agent(w)
    if missing_powershell:
        w.sup.powershell = False
    else:
        w.sup.fail[("tasks", None)] = "Get-ScheduledTask : Access is denied."
    acted = w.backend.act("start", unit, w.fence)
    assert acted.ok and w.sup.calls == [("tasks",), ("/run", AGENT)]
    [line] = acted.lines
    assert line.startswith(f"{AGENT}'s state couldn't be read (")
    assert ("did not run" if missing_powershell else "Access is denied") in line
    assert line.endswith("; ran /run unchecked, so a task that allows parallel instances "
                         "may now run a second copy")


def test_restart_when_the_read_fails_runs_both_steps_unchecked(w):
    unit = agent(w, "Running", 0x41301)
    w.sup.fail[("tasks", None)] = "Get-ScheduledTask : Access is denied."
    acted = w.backend.act("restart", unit, w.fence)
    assert acted.ok
    assert w.sup.calls == [("tasks",), ("/end", AGENT), ("/run", AGENT)]
    assert "ran /end /run unchecked" in acted.lines[0]


def test_a_timers_acts_on_a_foreign_task_are_as_before(w):
    w.sup.foreign[DAILY] = {"state": "Running", "last_run": None, "last_result": 0}
    w.sup.calls.clear()
    acted = w.backend.act("start", w.backend.foreign(DAILY, None), w.fence)
    assert acted.ok and w.sup.calls == [("/run", DAILY)]


# -- kinds through the public API ------------------------------------------------------------

def test_status_reads_a_task_named_a_daemon_by_kinds_and_others_as_timers(w):
    w.sup.foreign[AGENT] = {"state": "Ready", "last_run": EARLIER, "last_result": 1}
    w.sup.foreign[DAILY] = {"state": "Ready", "last_run": EARLIER, "last_result": 1}
    rows = units.status([], foreign=[AGENT, DAILY], kinds={AGENT: "daemon", "x": "timer"},
                        runner=w.sup)
    assert [(r.name, r.kind, r.state) for r in rows] == [
        (AGENT, "daemon", "failed"), (DAILY, "timer", "active")]
    assert rows[0].last == {"supervisor": {"last_run": EARLIER, "last_result": 1}}
    [same] = units.status([], foreign=[AGENT], runner=w.sup)
    assert (same.kind, same.state) == ("timer", "active")


def test_a_row_decided_without_a_read_takes_its_kind_from_kinds(w):
    [own] = units.status([], foreign=["\\Moxi\\demo-x"], kinds={"\\Moxi\\demo-x": "daemon"},
                         runner=w.sup)
    assert (own.kind, own.state) == ("daemon", "unknown")
    [own] = units.status([], foreign=["\\Moxi\\demo-x.timer"], runner=w.sup)
    assert own.kind == "timer"


def test_the_acts_act_on_a_task_named_a_daemon_by_kinds(w):
    w.sup.foreign[AGENT] = {"state": "Running", "last_run": EARLIER, "last_result": 0x41301}
    [acted] = units.start("services", [AGENT], foreign=True, kinds={AGENT: "daemon"},
                          runner=w.sup)
    assert acted.ok and w.sup.calls == [("tasks",)]  # running: one copy, no /run
    [acted] = units.start("services", [AGENT], foreign=True, runner=w.sup)
    assert acted.ok and w.sup.calls[-1] == ("/run", AGENT)  # a timer's run, as before


@pytest.mark.parametrize("bad", [{AGENT: "service"}, {AGENT: None}, [AGENT]])
def test_kinds_with_a_bad_value_raises(w, bad):
    with pytest.raises(ValueError):
        units.status([], foreign=[AGENT], kinds=bad, runner=w.sup)
    with pytest.raises(ValueError):
        units.start("services", [AGENT], foreign=True, kinds=bad, runner=w.sup)
    assert w.sup.calls == []


@pytest.mark.parametrize("act", ["start", "stop", "restart", "enable", "disable"])
def test_kinds_on_an_act_without_foreign_raises(w, act):
    with pytest.raises(ValueError, match="foreign=True"):
        getattr(units, act)("demo", ["relay"], kinds={"relay": "daemon"}, runner=w.sup)
    assert w.sup.calls == []


# -- a socket daemon whose start follows a setting -------------------------------------------

def test_a_switch_form_start_changes_no_task_and_its_key_writes_and_restarts_nothing(
        w, monkeypatch):
    """A daemon's logon task starts it at every logon whatever its `start` says, so the
    task text is the same with the key true and false, and `moxi config set` of the key
    writes nothing and runs nothing."""
    from moxi import config

    w.module(ANNOUNCES="2", DAEMONS=(
        "{'relay': {'command': 'moxi-demo-relay', 'socket': {'setting': 'port', "
        "'addresses': ['127.0.0.1'], 'start': {'login': 'snap.enabled'}}}}"))
    off = w.plan()["relay"]
    assert off.action == "add"
    assert all(a.ok for a in w.apply([off]))
    monkeypatch.setattr(config, "apply_units", lambda *a, **k: [])
    for value in (True, None):
        if value is None:
            config.unset("demo", "snap.enabled", by="test")
        else:
            config.set("demo", "snap.enabled", value, by="test")
        on = w.plan()["relay"]
        assert (on.action, on.text) == ("keep", off.text)
        w.sup.calls.clear()
        applied = units.apply_switch("demo", "snap.enabled", runner=w.sup, say=w.lines.append)
        assert [(a.change.action, a.ok) for a in applied] == [("keep", True)]
        assert w.sup.calls == []


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


# -- Windows rows, run on a Windows host -------------------------------------------------

windows = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("MOXI_TEST_REAL_SUPERVISOR") != "1",
    reason="a real Task Scheduler row: Windows with MOXI_TEST_REAL_SUPERVISOR=1, as a "
           "non-admin account")


@windows
def test_a_real_task_under_a_test_folder_is_created_run_read_ended_and_deleted(
        tmp_path, record_property):
    """Registers `\\MoxiTest\\<hex>-probe` from a rendered XML whose gate is replaced by a
    short sleep, and walks it through the verbs and the reads: armed and disabled, as a
    marked timer and as a foreign task. It records what `/end` of an idle task exits
    with (printed, and in the JUnit report as `end_idle_exit`), which decides whether
    stopping a stopped task can be read as done without PowerShell."""
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
        other = SchtasksBackend()  # reads the same task as a foreign one
        foreign = other.foreign(task, None)
        [state] = backend.status([unit], fence)
        assert (state.state, state.enabled) == ("active", True)  # armed, never run
        [state] = other.status([foreign], fence)
        assert (state.state, state.enabled) == ("active", True)
        assert backend.act("start", unit, fence).ok
        assert backend.act("start", unit, fence).ok  # already running: done
        [state] = backend.status([unit], fence)
        assert (state.state, state.enabled) == ("active", True)
        assert backend.act("disable", unit, fence).ok
        assert backend.act("stop", unit, fence).ok
        [state] = backend.status([unit], fence)
        assert (state.state, state.enabled) == ("inactive", False)
        [state] = other.status([foreign], fence)
        assert (state.state, state.enabled) == ("inactive", False)
        assert state.detail.startswith("Disabled")
        idle = fence.run("/end", task)
        record_property("end_idle_exit", idle.returncode)
        print(f"/end of an idle task: exit {idle.returncode}, {idle.stderr.strip()!r}")
        assert backend.act("stop", unit, fence).ok  # not running: done
    finally:
        fence.run("/end", task)
        fence.run("/delete", task)


def _logged_on(account: str) -> bool | None:
    """Whether `query user` lists `account` (its name without the domain) as logged on;
    None when `query` can't run here."""
    name = account.rpartition("\\")[2].casefold()
    try:
        done = subprocess.run(["query", "user"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in done.stdout.splitlines()[1:]:
        words = line.lstrip(" >").split()
        if words and words[0].casefold() == name:
            return True
    return False


def _sleepers(tag: str) -> list[int]:
    """The pids of the task's `Start-Sleep` processes, found by `tag` in their command
    line (the PowerShell that looks is left out)."""
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and "
              f"$_.CommandLine -like '*Start-Sleep 3600 # {tag}*' }} | "
              "ForEach-Object { $_.ProcessId }")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                           script], capture_output=True, text=True, timeout=60)
    return [int(word) for word in done.stdout.split() if word.isdigit()]


@windows
def test_a_real_foreign_task_read_as_a_daemon_tracks_its_process(tmp_path, record_property):
    """Registers `\\\\MoxiTest\\\\<hex>-daemon`, an InteractiveToken task with policy
    Parallel whose action is one `Start-Sleep` carrying the hex, and reads and acts on it
    as a foreign daemon from a second backend. It records what Windows does where the
    source can't settle it (a killed process's result, `State` of a running task disabled,
    how long `State` takes to follow `/run` and `/end`, whether `Queued` can be made),
    each in the JUnit report, and fails at the end naming every value that contradicts a
    rule of the daemon read or its acts.

    A non-admin account can register only an InteractiveToken task, which runs only while
    that account is logged on interactively; an ssh session isn't one. So the row skips
    unless `query user` lists the account, or when its first `/run` doesn't read
    `Running` within 10 s."""
    import secrets
    import time

    from moxi.units._fence import Fence as RealFence
    from moxi.units._schtasks import SETTLE_SECONDS

    owner = SchtasksBackend(folder="\\MoxiTest\\")
    account = owner.user()
    needs = (f"needs {account} logged on at the console: an InteractiveToken task runs "
             "only in an interactive session")
    if not _logged_on(account):
        pytest.skip(needs)
    fence = RealFence("schtasks")
    tag = secrets.token_hex(4)
    task = f"\\MoxiTest\\{tag}-daemon"
    other = SchtasksBackend()
    unit = other.foreign(task, None, kind="daemon")
    found: dict[str, object] = {}
    wrong: list[str] = []

    def keep(key, value):
        found[key] = value
        record_property(key, value)
        print(f"{key}: {value!r}")

    def read():
        [state] = other.status([unit], fence)
        return state

    def code(state):
        return (state.last or {}).get("last_result")

    def until(want, seconds):
        """Read until `want(state)` or `seconds` pass: `(state, seconds taken)`."""
        start = time.monotonic()
        while True:
            state = read()
            if want(state) or time.monotonic() - start >= seconds:
                return state, time.monotonic() - start
            time.sleep(0.1)

    def register(policy):
        xml = tmp_path / f"{policy}.xml"
        text = ('<?xml version="1.0" encoding="UTF-16"?>\n'
                f'<Task version="1.2" xmlns="{NS}"><Principals><Principal id="Author">'
                f"<UserId>{account}</UserId><LogonType>InteractiveToken</LogonType>"
                "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings>"
                f"<MultipleInstancesPolicy>{policy}</MultipleInstancesPolicy>"
                "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
                "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
                "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>"
                "<Enabled>true</Enabled></Settings><Actions Context=\"Author\"><Exec>"
                "<Command>powershell.exe</Command><Arguments>-NoProfile -Command "
                f"\"Start-Sleep 3600 # {tag}\"</Arguments></Exec></Actions></Task>\n")
        xml.write_bytes(text.encode("utf-16"))
        assert fence.run("/create", task, str(xml)).ok

    try:
        register("Parallel")
        # 1. Never run: inactive, "has not yet run".
        state = read()
        if (state.state, code(state)) != ("inactive", 0x41303):
            wrong.append(f"before any run it read {state.state} ({state.detail})")
        # 2. start runs it; a second start leaves one copy.
        assert other.act("start", unit, fence).ok
        state, _ = until(lambda s: s.state == "active", 10.0)
        if state.state != "active":
            pytest.skip(needs)
        if state.since is None:
            wrong.append("a running task read no since")
        assert other.act("start", unit, fence).ok
        copies = _sleepers(tag)
        if len(copies) != 1:
            wrong.append(f"a second start left {len(copies)} copies")
        # 3. A killed process reads failed.
        for pid in copies:
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            f"Stop-Process -Id {pid} -Force"], capture_output=True,
                           timeout=60)
        state, _ = until(lambda s: s.state != "active", 10.0)
        keep("killed_result", code(state))
        if state.state != "failed":
            wrong.append(f"a killed process read {state.state} ({state.detail})")
        # 4. start again, through the stale crash read.
        assert other.act("start", unit, fence).ok
        if read().state != "active":
            wrong.append("start after a crash did not read active")
        # 5. stop: inactive with a code in the success band.
        assert other.act("stop", unit, fence).ok
        state = read()
        keep("ended_result", code(state))
        if state.state != "inactive" or code(state) not in units.TASK_STATUS_CODES:
            wrong.append(f"stop read {state.state} ({state.detail})")
        # 6. Disabled while it runs: what State reads.
        assert other.act("start", unit, fence).ok
        assert fence.run("/change", task, "/disable").ok
        state = read()
        keep("disabled_running_state", state.detail)
        if (state.state, state.enabled) != ("active", False):
            wrong.append(f"a running task disabled read {state.state}, enabled "
                         f"{state.enabled} ({state.detail})")
        assert fence.run("/change", task, "/enable").ok
        assert other.act("stop", unit, fence).ok
        # 7. How long State takes to follow /run and /end.
        assert fence.run("/run", task).ok
        _, took = until(lambda s: s.state == "active", 15.0)
        keep("run_to_running_s", round(took, 2))
        assert fence.run("/end", task).ok
        _, took = until(lambda s: s.state != "active", 15.0)
        keep("end_to_ready_s", round(took, 2))
        for key in ("run_to_running_s", "end_to_ready_s"):
            if found[key] > SETTLE_SECONDS:
                wrong.append(f"{key} {found[key]} is over {SETTLE_SECONDS:g} s")
        # 8. Whether a second /run under policy Queue reads Queued.
        register("Queue")
        fence.run("/run", task)
        fence.run("/run", task)
        seen, _ = until(lambda s: (s.detail or "").startswith("Queued"), 3.0)
        keep("queued_seen", (seen.detail or "").startswith("Queued"))
    finally:
        fence.run("/end", task)
        fence.run("/delete", task)
        # Ending or deleting the task leaves a running or queued copy alive, so stop
        # every Start-Sleep this run's tag names.
        for pid in _sleepers(tag):
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            f"Stop-Process -Id {pid} -Force"], capture_output=True,
                           timeout=60)
    assert not wrong, wrong
