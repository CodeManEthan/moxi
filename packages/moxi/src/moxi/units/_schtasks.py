"""The Windows Task Scheduler backend: `schtasks.exe` with a task XML, no pywin32.

Each unit is one task, `\\Moxi\\<module>-<name>`, registered from an XML file the core
keeps as `STATE/moxi/units/<module>-<name>.xml` (UTF-16 with a byte-order mark). That
file is what `plan` compares, so `plan` reads no supervisor. A home given by `MOXI_HOME`
puts its tasks in a folder of its tag, `\\Moxi\\<tag>\\<module>-<name>`
(`moxi.units.home_tag`); its XML, runtime files and logs are under its own roots already.
The mark is the `\\Moxi\\` folder together with the XML's `<Source>moxi unit:
<module>/<name></Source>` line.

The action is the gate under a window-less interpreter, so no console window opens at
logon: `pythonw.exe -m moxi run --record <module>/<name> --log <log> ... -- <command>`.
A task has no environment of its own, so a tagged home's gate is given `--home <path>`, the
home's real path, and sets `MOXI_HOME` for itself and the command.
A daemon's task starts at logon and its gate runs with `--restart` and `--runtime`; a
timer's task fires on a time or calendar trigger and its gate runs with `--if` when the
timer has a switch. A daemon's `socket` is skipped here, with one line, and the daemon runs
without it.

Reading state: one `schtasks /query /fo csv /nh`, read by position (its status column is
translated, so its text is never read), plus the gate's runtime file
`RUNTIME/moxi/units/<module>-<name>.json` (`{"pid", "started"}`), which names the running
daemon. A task's `enabled`, and a foreign task's state and last result, come from the
fence's one PowerShell read (`Get-ScheduledTask` with `Get-ScheduledTaskInfo`, as JSON).
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import replace as _replaced
from pathlib import Path
from xml.sax.saxutils import escape

from ._backend import ApplyIO, ReadError, Rendered, RenderError, Spec, UnitRef
from ._fence import Fence, Ran, task_refs_env
from ._home import home_tag
from ._types import Acted, UnitChange, UnitState, Wrapped

#: The task folder that is moxi's namespace on this backend.
FOLDER = "\\Moxi\\"
#: The XML's namespace.
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
#: The schedule's anchor: a fixed day in the past, local time.
ANCHOR = "2000-01-01"

_MARK = re.compile(r"<Source>\s*moxi unit: ([^<\s]+)\s*</Source>")
_SETTINGS = re.compile(r"<Settings>(.*?)</Settings>", re.S)
_DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
              "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}


class SchtasksBackend:
    """The `Backend` protocol over Task Scheduler.

    `user` is the account the tasks run as and log on with, `DOMAIN\\user`; it is read
    from this process's token when first needed unless given. `folder` is the task
    folder (`\\Moxi\\`; a proof row uses another).
    """

    name = "schtasks"
    supervisor = "schtasks"
    encoding = "utf-16"
    sockets = "none"

    def __init__(self, *, user: str | None = None, folder: str = FOLDER):
        self._user = user
        self.folder = folder

    # -- where things are ------------------------------------------------------------------

    def task(self, module: str, name: str) -> str:
        """The task's path, `\\Moxi\\<module>-<name>`, or `\\Moxi\\<tag>\\<module>-<name>`
        for a tagged home."""
        return f"{self.home_folder()}{module}-{name}"

    def home_folder(self) -> str:
        """The folder this home's tasks are in: the folder itself, or its subfolder of
        this home's tag."""
        tag = home_tag()
        return self.folder if tag is None else f"{self.folder}{tag}\\"

    @staticmethod
    def xml_path(module: str, name: str) -> Path:
        """Where the task's XML is kept: `STATE/moxi/units/<module>-<name>.xml`."""
        from .. import paths

        return paths.core().state_dir() / "units" / f"{module}-{name}.xml"

    @staticmethod
    def runtime_file(module: str, name: str) -> Path:
        """The gate's runtime file for a daemon: `RUNTIME/moxi/units/<module>-<name>.json`."""
        from .. import paths

        return paths.core().runtime_dir() / "units" / f"{module}-{name}.json"

    @staticmethod
    def log_path(module: str, name: str) -> Path:
        """The gate's `--log` file: `STATE/<module>/logs/<name>.log`."""
        from .. import paths

        return paths.Layout(module).state_dir() / "logs" / f"{name}.log"

    def user(self) -> str:
        if self._user is None:
            self._user = _account()
        return self._user

    # -- writing ---------------------------------------------------------------------------

    def render(self, spec: Spec) -> tuple[Rendered, ...]:
        from .. import paths

        pythonw = paths.python().parent / "pythonw.exe"
        if not pythonw.is_file():
            raise RenderError("command", f"no pythonw.exe beside {paths.python()}; a task "
                                         "runs the gate under it so no window opens")
        daemon = spec.kind == "daemon"
        log = self.log_path(spec.module, spec.name)
        gate = ["-m", "moxi", "run"]
        home = dict(spec.environment).get("MOXI_HOME")
        if home:
            gate += ["--home", home]
        if not daemon and spec.switch:
            gate += ["--if", f"{spec.module}.{spec.switch}"]
        gate += ["--record", spec.unit, "--log", str(log)]
        if daemon:
            gate += ["--restart", "--runtime"]
            if spec.stop == "process":  # its detached children may leave the gate's job
                gate += ["--stop", "process"]
        gate += ["--", *spec.command]
        user = escape(self.user())
        triggers = ([f"    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId>"
                     "</LogonTrigger>"] if daemon else _triggers(spec))
        settings = [
            "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
            "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
            "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
            "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
        ]
        if daemon:
            settings.append("    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count>"
                            "</RestartOnFailure>")
        settings += ["    <StartWhenAvailable>false</StartWhenAvailable>",
                     f"    <Enabled>{'true' if spec.enabled else 'false'}</Enabled>"]
        exec_ = [f"      <Command>{escape(str(pythonw))}</Command>",
                 f"      <Arguments>{escape(subprocess.list2cmdline(gate))}</Arguments>"]
        if spec.cwd:
            exec_.append(f"      <WorkingDirectory>{escape(spec.cwd)}</WorkingDirectory>")
        lines = [
            '<?xml version="1.0" encoding="UTF-16"?>',
            f'<Task version="1.2" xmlns="{NS}">',
            "  <RegistrationInfo>",
            f"    <Source>moxi unit: {escape(spec.unit)}</Source>",
            f"    <Description>{escape(f'Moxi {spec.module}: {spec.doc}')}</Description>",
            "  </RegistrationInfo>",
            "  <Triggers>", *triggers, "  </Triggers>",
            "  <Principals>",
            '    <Principal id="Author">',
            f"      <UserId>{user}</UserId>",
            "      <LogonType>InteractiveToken</LogonType>",
            "      <RunLevel>LeastPrivilege</RunLevel>",
            "    </Principal>",
            "  </Principals>",
            "  <Settings>", *settings, "  </Settings>",
            '  <Actions Context="Author">',
            "    <Exec>", *exec_, "    </Exec>",
            "  </Actions>",
            "</Task>",
        ]
        text = "\n".join(lines) + "\n"
        return (Rendered(spec.kind, ((self.xml_path(spec.module, spec.name), text),)),)

    def mark(self, text: str) -> str | None:
        found = _MARK.search(text)
        return found.group(1) if found else None

    def enabled(self, text: str) -> bool:
        settings = _SETTINGS.search(text)
        return not (settings and re.search(r"<Enabled>\s*false\s*</Enabled>",
                                           settings.group(1)))

    def socket_files(self) -> tuple[tuple[Path, str, tuple[tuple[str, int], ...]], ...]:
        """None: Task Scheduler writes no socket, and each daemon binds its own."""
        return ()

    def marked(self, module: str) -> tuple[UnitRef, ...]:
        folder = self.xml_path(module, "x").parent
        out = []
        for path in sorted(folder.glob(f"{module}-*.xml")):
            try:
                text = path.read_bytes().decode(self.encoding, errors="replace")
            except OSError:
                continue
            mark = self.mark(text)
            if not mark or mark.partition("/")[0] != module:
                continue
            name = mark.partition("/")[2]
            if path.name != f"{module}-{name}.xml":
                continue
            kind = "daemon" if "<LogonTrigger>" in text else "timer"
            out.append(UnitRef(module, name, kind, (self.task(module, name),), (path,),
                               self.enabled(text)))
        return tuple(out)

    def apply(self, changes: Sequence[UnitChange], *, restart: bool, io: ApplyIO) -> None:
        fence = io.fence
        for change in changes:
            if change.action == "remove":
                self._remove(change, io)
        for change in changes:
            if change.action in ("add", "change"):
                self._write(change, io)
        if restart:
            daemons = [c for c in changes if c.action == "keep" and c.kind == "daemon"
                       and c.enabled and c.entry.get("upgrade", "restart") == "restart"]
            try:
                running = self.running([self.task(c.module, c.name) for c in daemons],
                                       fence)
            except ReadError as exc:
                for change in daemons:
                    io.fail(change, exc.ran)
                return
            for change in daemons:
                task = self.task(change.module, change.name)
                if task not in running:
                    continue
                ran = self._end(task, fence)
                if not ran.ok:
                    io.fail(change, ran)
                    continue
                ran = fence.run("/run", task)
                if not ran.ok:
                    io.fail(change, ran)

    def _remove(self, change: UnitChange, io: ApplyIO) -> None:
        task = self.task(change.module, change.name)
        io.fence.run("/end", task)  # a task that is not running is no failure here
        ran = io.fence.run("/delete", task)
        if not ran.ok and self._listed(task, io.fence) is not False:
            io.fail(change, ran, "the task is left in place; fix the cause and run "
                                 f"`moxi install {change.module}` again")
            return
        io.delete(change)

    def _write(self, change: UnitChange, io: ApplyIO) -> None:
        fence = io.fence
        task = self.task(change.module, change.name)
        daemon = change.kind == "daemon"
        changed = change.action == "change"
        disabling = changed and not change.enabled and change.enabled_before is not False
        keep = (daemon and changed and change.enabled and change.enabled_before is not False
                and change.entry.get("upgrade", "restart") == "keep")
        if changed and not keep and not disabling:
            fence.run("/end", task)  # ended so the new task starts clean; idle is fine
        log = self.log_path(change.module, change.name).parent
        try:
            from .. import files

            files.private_dir(log)
        except OSError as exc:
            io.say(change, f"{change.unit}: its log folder {log} could not be made: {exc}")
        io.write(change)
        if daemon and change.entry.get("socket"):
            io.say(change, f"{change.unit}: its socket is skipped on Windows; the daemon "
                           "runs without it")
        ran = fence.run("/create", task, str(change.paths[0]))
        if not ran.ok:
            io.fail(change, ran)
            return
        if not change.enabled:
            if disabling and daemon:
                ran = self._end(task, fence)
                if not ran.ok:
                    io.fail(change, ran)
            return
        if keep:
            io.say(change, f"{task} rewritten; the new code runs from its next start")
            return
        if daemon:
            ran = fence.run("/run", task)
            if not ran.ok:
                io.fail(change, ran)

    # -- acting and reading ----------------------------------------------------------------

    def own(self, ref: str) -> bool:
        path = _path(ref).casefold()
        return path.startswith(FOLDER.casefold()) or path.startswith(self.folder.casefold())

    def foreign(self, ref: str, fence: Fence | None) -> UnitRef:
        """The task `ref` (a leading `\\` added when it has none), read and acted on as a
        timer: services adopts scheduled tasks, and a timer's acts never end a run the user
        started. No supervisor call, with a fence or without one."""
        return UnitRef(None, ref, "timer", (_path(ref),))

    def ids(self, module: str, name: str, kind: str) -> tuple[str, ...]:
        """The task path `\\Moxi\\<module>-<name>` (with this home's tag folder), for a
        daemon and a timer alike, whether or not the task is registered. No supervisor
        call."""
        return (self.task(module, name),)

    def files(self, module: str, name: str, kind: str) -> tuple[Path, ...]:
        return (self.xml_path(module, name),)

    def act(self, act: str, unit: UnitRef, fence: Fence) -> Acted:
        task = unit.ids[0]
        daemon = unit.kind == "daemon"
        steps = {
            "start": [("/run",)],
            "stop": [("/end",)],
            "restart": [("/end",), ("/run",)],
            "enable": [("/change", "/enable")] + ([("/run",)] if daemon else []),
            "disable": [("/change", "/disable")] + ([("/end",)] if daemon else []),
        }.get(act)
        if steps is None:
            return Acted(unit.module, unit.name, act, False, (f"no such act {act!r}",))
        for verb, *rest in steps:
            ran = self._end(task, fence) if verb == "/end" else fence.run(verb, task, *rest)
            if not ran.ok:
                return Acted(unit.module, unit.name, act, False, (ran.failure(),))
        return Acted(unit.module, unit.name, act, True)

    def running(self, ids: Sequence[str], fence: Fence) -> frozenset[str]:
        """The tasks of `ids` whose gate's runtime file names a live process, read from the
        files (no supervisor call). A task outside the folder has no runtime file and is
        not running. A runtime file that is there and cannot be read, or holds no
        `{"pid", "started"}`, raises `ReadError`: it may name a daemon that runs."""
        out = set()
        folder = self.home_folder()
        for task in ids:
            path = _path(task)
            if not path.casefold().startswith(folder.casefold()):
                continue
            module, dash, name = path[len(folder):].partition("-")
            if not dash or "\\" in module:
                continue
            file = self.runtime_file(module, name)
            try:
                live = self._live_file(file, strict=True)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ReadError(Ran(False, None, "", "", ("read", str(file)),
                                    f"{type(exc).__name__}: {exc}")) from None
            if live is not None:
                out.add(task)
        return frozenset(out)

    def stop_id(self, unit: str, fence: Fence) -> Ran:
        return self._end(unit, fence)

    def start_id(self, unit: str, fence: Fence) -> Ran:
        return fence.run("/run", unit)

    def status(self, units: Sequence[UnitRef], fence: Fence) -> list[UnitState]:
        units = list(units)
        if not units:
            return []
        marked = [u for u in units if u.marked]
        listed: set[str] | None = set()
        query: Ran | None = None
        if marked:
            query = fence.run("/query")
            listed = _listed_tasks(query.stdout) if query.ok else None
        tasks, why = self._read_tasks([u.ids[0] for u in units], fence)
        out = []
        for unit in units:
            task = unit.ids[0]
            info = None if tasks is None else tasks.get(task.casefold())
            if unit.marked:
                out.append(self._marked_state(unit, listed, query, tasks, info, why))
            else:
                out.append(self._foreign_state(unit, tasks, info, why))
        return out

    def _marked_state(self, unit: UnitRef, listed, query, tasks, info, why) -> UnitState:
        """A task in moxi's folder. Not listed by `/query`, `missing`; the query failed,
        `unknown`. A timer reads its schedule: `active` unless the PowerShell read gives its
        `State` as `Disabled` (`inactive`); without that read, `active` with `enabled` None
        and a `detail` saying the disabled check could not run. A daemon reads its gate's
        runtime file: a live `ProcId` is `active`; none, `inactive`, or `failed` when its
        newest run line has a non-zero exit. `last` is None: the core adds the run line."""
        task = unit.ids[0]
        common = dict(module=unit.module, name=unit.name, kind=unit.kind, backend=self.name)
        if listed is None:
            return UnitState(state="unknown", detail=query.failure(), **common)
        if task.casefold() not in listed:
            return UnitState(state="missing", detail=f"{task} is not registered", **common)
        enabled = None if tasks is None else (
            None if info is None else info.get("state") != "Disabled")
        if unit.kind == "timer":
            if enabled is False:
                state, detail = "inactive", "disabled"
            elif enabled is True:
                state, detail = "active", info.get("state") or "listed"
            else:
                state = "active"
                detail = ("listed; whether it is disabled could not be checked: "
                          + (why if tasks is None else f"the task read did not find {task}"))
            return UnitState(state=state, detail=detail, enabled=enabled,
                             log=self.log(unit), **common)
        last = _newest_run(unit.module, unit.name)
        live = self._runtime(unit.module, unit.name)
        if live is not None:
            pid, since = live
            state, detail, more = "active", f"running (pid {pid})", dict(pid=pid, since=since)
        elif last is not None and last.get("exit") not in (0, None):
            state, detail, more = "failed", f"its last run exited {last.get('exit')}", {}
        else:
            state, detail, more = "inactive", "not running", {}
        return UnitState(state=state, detail=detail, enabled=enabled,
                         log=self.log(unit), **more, **common)

    def _foreign_state(self, unit: UnitRef, tasks, info, why) -> UnitState:
        """A task outside moxi's folder, read as a timer from the PowerShell read: `State`
        `Disabled` is `inactive`, any other `active` with the raw `State` in `detail` (so a
        run in progress still shows). `last` holds `LastRunTime` and `LastTaskResult` as
        they came; whether that result is a failure is the caller's to judge, and no state
        here is `failed`. A ref the read doesn't find is `missing`; no PowerShell, or a
        failed read, `unknown`."""
        common = dict(module=None, name=unit.name, kind=unit.kind, backend=self.name)
        if tasks is None:
            return UnitState(state="unknown", detail=why or "the task read failed", **common)
        if info is None:
            return UnitState(state="missing", detail=f"{unit.ids[0]} is not registered",
                             **common)
        raw = info.get("state") or ""
        code = info.get("last_result")
        last = {"last_run": info.get("last_run"), "last_result": code}
        state = "inactive" if raw == "Disabled" else "active"
        detail = raw if not isinstance(code, int) else f"{raw}, last result 0x{code:08X}"
        return UnitState(state=state, detail=detail, last=last, enabled=raw != "Disabled",
                         **common)

    def log(self, unit: UnitRef) -> str | None:
        if not unit.marked:
            return None
        return str(self.log_path(unit.module, unit.name))

    # -- a foreign timer's wrap: systemd only ------------------------------------------------

    def wrap(self, ref: str, *, module: str, key: str, name: str, fence: Fence) -> Acted:
        return Acted(None, ref, "wrap", False, (f"wrap is systemd only; {ref} runs as it is",))

    def wraps(self, module: str) -> tuple[tuple[str, str, Wrapped], ...]:
        """Empty: Task Scheduler has no wraps."""
        return ()

    def unwrap(self, ref: str, *, module: str, fence: Fence) -> Acted:
        return Acted(None, ref, "unwrap", True, (f"{ref} is not wrapped by {module}",))

    def unwrap_all(self, module: str, fence: Fence) -> list[Acted]:
        return []

    # -- helpers ---------------------------------------------------------------------------

    def _end(self, task: str, fence: Fence) -> Ran:
        """`schtasks /end`, where a task that was not running is no failure: when `/end`
        fails, the PowerShell read says whether the task is running, and a task it finds
        and that isn't `Running` has nothing left to end.

        Without PowerShell, or when that read fails, `/end`'s failure stands, with a note
        that the check could not run: so stopping an already stopped task may read as
        failed there. That stays until a Windows host records what `/end` of an idle task
        exits with; if that code names the idle state, it can be read as done here."""
        ran = fence.run("/end", task)
        if ran.ok:
            return ran
        tasks, why = self._read_tasks([task], fence)
        info = None if tasks is None else tasks.get(task.casefold())
        if info is not None and info.get("state") != "Running":
            return _replaced(ran, ok=True)
        if tasks is None:
            said = ran.stderr.strip() or ran.stdout.strip() or f"exit {ran.returncode}"
            return _replaced(ran, stderr=f"{said} (whether {task} was already stopped "
                                         f"could not be checked: {why})")
        return ran

    def _listed(self, task: str, fence: Fence) -> bool | None:
        """Whether `/query` lists `task`; None when the query fails."""
        ran = fence.run("/query")
        return task.casefold() in _listed_tasks(ran.stdout) if ran.ok else None

    def _read_tasks(self, refs: Sequence[str], fence: Fence
                    ) -> tuple[dict[str, dict] | None, str | None]:
        """The PowerShell read of `refs` (task paths): a map of each found task's path,
        case-folded, to `{path, name, state, last_run, last_result}`; or None and the
        reason when PowerShell is missing or the call fails."""
        found: dict[str, dict] = {}
        for env in task_refs_env([_split(r) for r in refs]):
            ran = fence.run("tasks", env=env)
            if not ran.ok:
                return None, ran.failure()
            try:
                data = json.loads(ran.stdout.lstrip("﻿").strip() or "[]")
            except ValueError as exc:
                return None, f"the task read printed no JSON: {exc}"
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                return None, "the task read printed no list"
            for item in data:
                if isinstance(item, dict):
                    path = f"{item.get('path') or ''}{item.get('name') or ''}"
                    found[path.casefold()] = item
        return found, None

    def _runtime(self, module: str, name: str) -> tuple[int, float] | None:
        return self._live_file(self.runtime_file(module, name))

    @staticmethod
    def _live_file(path: Path | None, *, strict: bool = False) -> tuple[int, float] | None:
        """`(pid, started)` from a runtime file whose process is still the one it names
        (same pid, same start time), else None. With `strict`, a file that is there and
        cannot be read or parsed raises instead of reading as None."""
        if path is None:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid, started = int(data["pid"]), float(data["started"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            if strict:
                raise
            return None
        from .. import proc

        return (pid, started) if proc.alive(proc.ProcId(pid, started)) else None


BACKEND = SchtasksBackend()


def _triggers(spec: Spec) -> list[str]:
    """A timer's triggers: one repeating `TimeTrigger` for `every`, else one
    `CalendarTrigger` per time of `at`, daily or on its weekdays."""
    schedule = spec.schedule
    if schedule is None:
        raise RenderError("declaration", f"{spec.unit} is a timer with no schedule")
    if schedule.every:
        hours, minutes = divmod(schedule.every, 60)
        interval = f"PT{hours}H" if hours and not minutes else f"PT{schedule.every}M"
        return ["    <TimeTrigger>",
                f"      <Repetition><Interval>{interval}</Interval></Repetition>",
                f"      <StartBoundary>{ANCHOR}T00:00:00</StartBoundary>",
                "      <Enabled>true</Enabled>",
                "    </TimeTrigger>"]
    if schedule.daily:
        when = "      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    else:
        days = "".join(f"<{_DAY_NAMES[d]} />" for d in schedule.days)
        when = (f"      <ScheduleByWeek><DaysOfWeek>{days}</DaysOfWeek>"
                "<WeeksInterval>1</WeeksInterval></ScheduleByWeek>")
    out = []
    for hour, minute in schedule.at:
        out += ["    <CalendarTrigger>",
                f"      <StartBoundary>{ANCHOR}T{hour:02d}:{minute:02d}:00</StartBoundary>",
                "      <Enabled>true</Enabled>",
                when,
                "    </CalendarTrigger>"]
    return out


def _path(ref: str) -> str:
    """A task ref as a full task path: a leading `\\` added when it has none."""
    return ref if ref.startswith("\\") else "\\" + ref


def _split(ref: str) -> tuple[str, str]:
    """A task path's folder (with its trailing `\\`) and name, as `Get-ScheduledTask`
    gives `TaskPath` and `TaskName`."""
    head, _, name = _path(ref).rpartition("\\")
    return head + "\\", name


def _listed_tasks(text: str) -> set[str]:
    """The task paths `schtasks /query /fo csv /nh` lists, case-folded, read from the
    first column only."""
    rows = csv.reader(line for line in text.splitlines() if line.strip())
    return {row[0].casefold() for row in rows if row and row[0].startswith("\\")}


def _newest_run(module: str, name: str) -> dict | None:
    """The newest run line of `<module>/<name>` in `STATE/<module>/runs.jsonl`."""
    from .. import files, paths

    unit = f"{module}/{name}"
    try:
        records = files.read_records(paths.Layout(module).state_dir() / "runs.jsonl",
                                     tail=500)
    except OSError:
        return None
    for record in reversed(records):
        if record.get("unit") == unit:
            return record
    return None


def _account() -> str:
    """This process's account as `DOMAIN\\user` (`GetUserNameExW`'s SAM-compatible name),
    or `USERDOMAIN\\USERNAME` where that call is not there."""
    try:
        import ctypes

        secur32 = ctypes.WinDLL("secur32")  # type: ignore[attr-defined]
        size = ctypes.c_ulong(0)
        secur32.GetUserNameExW(2, None, ctypes.byref(size))
        buffer = ctypes.create_unicode_buffer(size.value or 256)
        if secur32.GetUserNameExW(2, buffer, ctypes.byref(size)):
            return buffer.value
    except (AttributeError, OSError):
        pass
    domain = os.environ.get("USERDOMAIN") or ""
    user = os.environ.get("USERNAME") or ""
    if not user:
        import getpass

        user = getpass.getuser()
    return f"{domain}\\{user}" if domain else user
