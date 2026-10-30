"""The launchd backend: a module's units as launch agents in the user's `gui/<uid>` domain.

Each unit is one plist, `~/Library/LaunchAgents/moxi.<module>.<name>.plist`, with the label
`moxi.<module>.<name>`, written 0644 with `atomic_write`. The mark is the XML comment
`<!-- moxi unit: <module>/<name> -->` before the `<plist>` element, and a unit declared
`enabled = false` carries `<!-- moxi: enabled = false -->` under it.

A daemon:

    Label, ProgramArguments (the absolute command), WorkingDirectory (a run-time `cwd`),
    RunAtLoad, KeepAlive {SuccessfulExit: false}, ThrottleInterval 2,
    ProcessType Background, StandardOutPath and StandardErrorPath (the unit's log file,
    `STATE/<module>/logs/<name>.log`), EnvironmentVariables {MOXI_LOG_FILE: that file},
    AbandonProcessGroup (only with `stop = "process"`), and a `socket` as
    Sockets {Listeners: [{SockNodeName, SockServiceName, SockType: stream}, ...]}, one
    listener per address, which the daemon takes with `launch_activate_socket`.

A timer has no RunAtLoad and no KeepAlive. Its ProgramArguments is the gate's argv,
`<scripts>/moxi run [--if <module>.<switch>] --record <module>/<name> -- <command>`;
`every` is StartInterval in seconds; `at` is a StartCalendarInterval array of {Hour,
Minute} dicts, crossed with Weekday (0 is Sunday) when `days` is not all seven. It keeps
the daemon's log file, ProcessType and MOXI_LOG_FILE.

Every supervisor call goes through the fence's launchd verbs: `bootstrap gui/<uid> <plist>`,
`bootout`, `enable`, `disable`, `kickstart -k` and `kill SIGTERM` of `gui/<uid>/<label>`,
and `print gui/<uid>`, the one read of the domain. "Not loaded" for a bootout and "already
loaded" for a bootstrap are not failures; each is told apart from a real refusal by the
domain's own `services` block, read only when the call failed, so no message text of
launchctl's is relied on.

The shapes of `launchctl print gui/<uid>` this reads, a `services = {` block of
`<pid> <last exit status> <label>` lines and a `disabled services = {` block of
`"<label>" => disabled` lines, are tested here against recorded text; their proof on a
real Mac is owed.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace as _replaced
from pathlib import Path

from ._backend import ApplyIO, ReadError, Rendered, RenderError, Spec, UnitRef
from ._fence import Fence, Ran
from ._fence import argv as _argv
from ._types import Acted, UnitChange, UnitState, Wrapped

#: The mark's comment, around `<module>/<name>`.
MARK = "<!-- moxi unit: {} -->"
#: The comment under the mark of a unit declared `enabled = false`.
DISABLED = "<!-- moxi: enabled = false -->"

_HEADER = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
           '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n')
_MARK = re.compile(r"<!-- moxi unit: ([a-z0-9-]+/[a-z0-9-]+) -->")
_TIMER_KEY = re.compile(r"<key>Start(Interval|CalendarInterval)</key>")
_WEEKDAY = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
# XML 1.0 has no form for these characters, escaped or not.
_UNWRITABLE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\ud800-\udfff]")
# The bootout of a label that is not loaded: ESRCH, and "could not find service".
_NOT_LOADED = (3, 113)


class LaunchdBackend:
    """The `Backend` protocol on launchd.

    `agents` is the LaunchAgents directory (default `~/Library/LaunchAgents`, read when
    used), `uid` the domain's user (default this process's), `gate` the `moxi` script a
    timer runs (default `moxi.paths.script("moxi")`) and `runner` the fence's runner when
    a call passes none. Tests set all four; `BACKEND` sets none.
    """

    name = "launchd"
    supervisor = "launchd"
    encoding = "utf-8"
    sockets = "job"

    def __init__(self, *, agents: Path | None = None, uid: int | None = None,
                 gate: str | None = None, runner=None):
        self._agents = Path(agents) if agents is not None else None
        self._uid = uid
        self._gate = gate
        if runner is not None:
            self.runner = runner

    # -- names -------------------------------------------------------------------------------

    @property
    def agents(self) -> Path:
        if self._agents is not None:
            return self._agents
        return Path(os.path.expanduser("~")) / "Library" / "LaunchAgents"

    @property
    def domain(self) -> str:
        return f"gui/{self._uid if self._uid is not None else os.getuid()}"

    def target(self, label: str) -> str:
        return f"{self.domain}/{label}"

    @staticmethod
    def label(module: str, name: str) -> str:
        return f"moxi.{module}.{name}"

    def plist(self, label: str) -> Path:
        return self.agents / f"{label}.plist"

    @staticmethod
    def log_file(module: str, name: str) -> Path:
        """The unit's log file, `STATE/<module>/logs/<name>.log`."""
        from .. import paths

        return paths.Layout(module).state_dir() / "logs" / f"{name}.log"

    # -- writing -----------------------------------------------------------------------------

    def render(self, spec: Spec) -> tuple[Rendered, ...]:
        label = self.label(spec.module, spec.name)
        log = str(self.log_file(spec.module, spec.name))
        body: dict = {"Label": label}
        if spec.kind == "daemon":
            body["ProgramArguments"] = list(spec.command)
            if spec.cwd:
                body["WorkingDirectory"] = spec.cwd
            body["RunAtLoad"] = True
            body["KeepAlive"] = {"SuccessfulExit": False}
            body["ThrottleInterval"] = 2
        else:
            gate = [self.gate(), "run"]
            if spec.switch:
                gate += ["--if", f"{spec.module}.{spec.switch}"]
            body["ProgramArguments"] = gate + ["--record", spec.unit, "--", *spec.command]
            if spec.cwd:
                body["WorkingDirectory"] = spec.cwd
            body.update(_schedule(spec))
        body["ProcessType"] = "Background"
        body["StandardOutPath"] = log
        body["StandardErrorPath"] = log
        body["EnvironmentVariables"] = {"MOXI_LOG_FILE": log}
        if spec.kind == "daemon" and spec.stop == "process":
            body["AbandonProcessGroup"] = True
        if spec.kind == "daemon" and spec.socket is not None:
            port = str(spec.socket.port)
            body["Sockets"] = {"Listeners": [
                {"SockNodeName": address, "SockServiceName": port, "SockType": "stream"}
                for address in spec.socket.addresses]}
        head = [MARK.format(spec.unit)] + ([DISABLED] if not spec.enabled else [])
        try:
            text = _document(head, body)
        except ValueError as exc:
            raise RenderError("command", f"{spec.unit}: {exc}") from None
        kind = "daemon" if spec.kind == "daemon" else "timer"
        return (Rendered(kind, ((self.plist(label), text),)),)

    def gate(self) -> str:
        """The `moxi` script a timer's plist runs; `RenderError` when there is none."""
        if self._gate is not None:
            return self._gate
        from .. import paths

        try:
            return str(paths.script("moxi"))
        except FileNotFoundError:
            raise RenderError("command", "no moxi in this environment; a timer runs "
                                         "through `moxi run`") from None

    def mark(self, text: str) -> str | None:
        found = _MARK.search(_prologue(text))
        return found.group(1) if found else None

    def enabled(self, text: str) -> bool:
        return DISABLED not in _prologue(text)

    def marked(self, module: str) -> tuple[UnitRef, ...]:
        out = []
        for path in sorted(self.agents.glob(f"moxi.{module}.*.plist")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            mark = self.mark(text)
            if mark is None or mark.partition("/")[0] != module:
                continue
            kind = "timer" if _TIMER_KEY.search(text) else "daemon"
            out.append(UnitRef(module, mark.partition("/")[2], kind,
                               (path.name.removesuffix(".plist"),), (path,),
                               self.enabled(text)))
        return tuple(out)

    def apply(self, changes: Sequence[UnitChange], *, restart: bool, io: ApplyIO) -> None:
        """Each `remove`: `bootout`, then the plist deleted. Each `add` or `change`: a
        change is booted out first; the plist is written; then `enable` and `bootstrap`,
        or, for a unit declared `enabled = false`, `disable` only. A changed daemon with
        `upgrade = "keep"` whose job is loaded is only rewritten. With `restart=True`, an
        unchanged daemon that has a pid and `upgrade = "restart"` is `kickstart -k`ed."""
        fence = io.fence
        removes = [c for c in changes if c.action == "remove"]
        writes = [c for c in changes if c.action in ("add", "change")]
        kept = [c for c in writes if c.action == "change" and _keeps(c)]
        restarts = [c for c in changes if restart and c.action == "keep"
                    and c.kind == "daemon" and c.enabled
                    and c.entry.get("upgrade", "restart") == "restart"]
        # One read of the domain serves every unit that needs one.
        read = self._services(fence) if kept or restarts else None
        for change in removes:
            if self._bootout(self._label_of(change), fence, io, change,
                             "the unit is left in place; fix the cause and run "
                             f"`moxi install {change.module}` again"):
                io.delete(change)
        for change in writes:
            self._write(change, io, read)
        for change in restarts:
            label = self._label_of(change)
            if isinstance(read, Ran):
                io.fail(change, read, "the daemon runs its old code until it restarts: "
                                      f"`launchctl kickstart -k {self.target(label)}`")
                continue
            if read.get(label, (None, None))[0]:
                self._run(io, change, "kickstart", self.target(label))

    def _write(self, change: UnitChange, io: ApplyIO, read) -> None:
        fence = io.fence
        label = self._label_of(change)
        if change.action == "change" and _keeps(change) and (
                isinstance(read, Ran) or label in read):
            io.write(change)
            io.say(change, f"{label} rewritten; the new code runs from its next start")
            return
        if change.action == "change" and not self._bootout(
                label, fence, io, change, "the unit is left as it was; fix the cause and "
                                          f"run `moxi install {change.module}` again"):
            return
        io.write(change)
        if not change.enabled:
            self._run(io, change, "disable", self.target(label))
            return
        problem = self._logs(change.module, change.name)
        if problem is not None:
            io.fail(change, problem)
            return
        if self._run(io, change, "enable", self.target(label)):
            self._run(io, change, "bootstrap", self.domain, str(change.paths[0]))

    def _logs(self, module: str, name: str) -> Ran | None:
        """Make the unit's log directory private before its plist is loaded; the failure
        as a `Ran` for the line, or None."""
        from .. import files

        logs = self.log_file(module, name).parent
        try:
            files.private_dir(logs)
        except OSError as exc:
            return Ran(False, None, "", "", ("mkdir", "-m", "700", str(logs)), str(exc))
        return None

    def _label_of(self, change: UnitChange) -> str:
        if change.paths:
            return change.paths[0].name.removesuffix(".plist")
        return self.label(change.module, change.name)

    def _run(self, io: ApplyIO, change: UnitChange, verb: str, *args: str) -> bool:
        ran = io.fence.run(verb, *args)
        if not ran.ok:
            io.fail(change, ran)
        return ran.ok

    def _bootout(self, label: str, fence: Fence, io: ApplyIO, change: UnitChange,
                 remedy: str) -> bool:
        ran = self._unload(label, fence)
        if not ran.ok:
            io.fail(change, ran, remedy)
        return ran.ok

    # -- acting and reading ----------------------------------------------------------------

    def own(self, ref: str) -> bool:
        return ref.startswith("moxi.")

    def foreign(self, ref: str, fence: Fence | None) -> UnitRef:
        """A foreign label, its plist read from `~/Library/LaunchAgents/<label>.plist`
        when there is one (a timer when it has a StartInterval or StartCalendarInterval).
        No supervisor call, with a fence or without one."""
        path = self.plist(ref)
        body = _load(path)
        kind = "timer" if body and ("StartInterval" in body
                                    or "StartCalendarInterval" in body) else "daemon"
        return UnitRef(None, ref, kind, (ref,), (path,) if path.is_file() else ())

    def ids(self, module: str, name: str, kind: str) -> tuple[str, ...]:
        """The label `moxi.<module>.<name>`, for a daemon and a timer alike, whether or
        not its plist is there. No supervisor call."""
        return (self.label(module, name),)

    def files(self, module: str, name: str, kind: str) -> tuple[Path, ...]:
        return (self.plist(self.label(module, name)),)

    def act(self, act: str, unit: UnitRef, fence: Fence) -> Acted:
        label = unit.ids[0]
        lines: list[str] = []

        def done(ran: Ran) -> bool:
            if not ran.ok:
                lines.append(ran.failure())
            return ran.ok

        if act in ("start", "enable") and (unit.kind == "daemon" or act == "enable"):
            path = unit.paths[0] if unit.paths else self.plist(label)
            if not path.is_file():
                return Acted(unit.module, unit.name, act, False,
                             (f"{label} has no plist at {path}",))
        if unit.marked and act in ("start", "enable", "restart"):
            problem = self._logs(unit.module, unit.name)
            if problem is not None:
                return Acted(unit.module, unit.name, act, False, (problem.failure(),))
        if act == "enable":
            ok = done(fence.run("enable", self.target(label))) and done(
                self._load(label, fence, path))
        elif act == "disable":
            ok = done(self._unload(label, fence)) and done(
                fence.run("disable", self.target(label)))
        elif unit.kind == "daemon":
            if act == "start":
                ok = done(self._load(label, fence, path))
            elif act == "stop":
                ok = done(self._unload(label, fence))
            elif act == "restart":
                ok = done(self._restart(label, fence, unit))
            else:
                raise ValueError(f"no such act {act!r}")
        elif act == "start":
            ok = done(fence.run("kickstart", self.target(label)))
        elif act == "stop":
            ok = done(self._end_run(label, fence))
        elif act == "restart":
            ok = done(self._end_run(label, fence)) and done(
                fence.run("kickstart", self.target(label)))
        else:
            raise ValueError(f"no such act {act!r}")
        return Acted(unit.module, unit.name, act, ok, tuple(lines))

    def running(self, ids: Sequence[str], fence: Fence) -> frozenset[str]:
        """The labels of `ids` loaded in the domain, with a pid or not: a loaded job with
        `Sockets` or `KeepAlive` can start on its own. One `print gui/<uid>`; `ReadError`
        when it fails."""
        if not ids:
            return frozenset()
        read = self._services(fence)
        if isinstance(read, Ran):
            raise ReadError(read)
        return frozenset(label for label in ids if label in read)

    def stop_id(self, unit: str, fence: Fence) -> Ran:
        return self._unload(unit, fence)

    def start_id(self, unit: str, fence: Fence) -> Ran:
        return self._load(unit, fence, self.plist(unit))

    def status(self, units: Sequence[UnitRef], fence: Fence) -> list[UnitState]:
        """One `launchctl print gui/<uid>` for all of `units`. A loaded daemon with a pid
        is `active`, without one `failed` when its last exit status is not zero, else
        `inactive`. A timer reads its schedule, never its runs: loaded and not listed
        disabled is `active` (with a pid or not), loaded and listed disabled `inactive`;
        its last exit status goes in `last`, so a timer whose last run failed still reads
        `active`. A label that is not loaded is `inactive` ("not loaded") when its plist is
        there, else `missing`. `enabled` is False for a label the `disabled services` block
        lists disabled, True otherwise, None when `missing`. When the print fails, every
        unit is `unknown` with the failure in `detail`.

        One state per unit, in the order of `units`. `last` is launchd's own result only,
        `{"status": <last exit status>}` for a timer (None for a daemon); the core adds the
        gate's run line. A foreign label from `foreign(ref, None)` is read in the same
        call."""
        if not units:
            return []
        ran = fence.run("print", self.domain)
        services, disabled = _domain(ran.stdout) if ran.ok else ({}, {})
        out = []
        for unit in units:
            label = unit.ids[0]
            path = unit.paths[0] if unit.paths else self.plist(label)
            pid = since = last = None
            if not ran.ok:
                state, detail = "unknown", ran.failure()
            elif label in services:
                pid, code = services[label]
                if unit.kind == "timer":
                    state = "inactive" if disabled.get(label) is False else "active"
                    last = {"status": code}
                elif pid:
                    state = "active"
                else:
                    state = "failed" if code not in (None, 0) else "inactive"
                detail = (f"loaded, pid {pid}" if pid else "loaded") + (
                    ", disabled" if unit.kind == "timer" and state == "inactive" else "") + (
                    f", last exit {code}" if code is not None else "")
                since = _started(pid) if pid else None
            elif path.is_file():
                state, detail = "inactive", "not loaded"
            else:
                state, detail = "missing", f"no plist at {path}"
            enabled = None if state in ("missing", "unknown") else disabled.get(label, True)
            out.append(UnitState(unit.module, unit.name, unit.kind, self.name, state,
                                 pid=pid, since=since, last=last, detail=detail,
                                 log=None if state == "missing" else self.log(unit),
                                 enabled=enabled))
        return out

    def log(self, unit: UnitRef) -> str | None:
        """The plist's StandardOutPath (or StandardErrorPath), None when it has neither."""
        body = _load(unit.paths[0] if unit.paths else self.plist(unit.ids[0]))
        if not body:
            return None
        found = body.get("StandardOutPath") or body.get("StandardErrorPath")
        return found if isinstance(found, str) else None

    # -- a foreign timer's wrap: systemd only --------------------------------------------------

    def wrap(self, ref: str, *, module: str, key: str, name: str, fence: Fence) -> Acted:
        return Acted(None, ref, "wrap", False, (f"wrap is systemd only; {ref} runs as it is",))

    def wraps(self, module: str) -> tuple[tuple[str, str, Wrapped], ...]:
        """Empty: launchd has no wraps."""
        return ()

    def unwrap(self, ref: str, *, module: str, fence: Fence) -> Acted:
        return Acted(None, ref, "unwrap", True, (f"{ref} is not wrapped by {module}",))

    def unwrap_all(self, module: str, fence: Fence) -> list[Acted]:
        return []

    # -- the verbs, with "not loaded" and "already loaded" read as done ---------------------------

    def _services(self, fence: Fence) -> dict[str, tuple[int | None, int | str | None]] | Ran:
        """The domain's loaded labels, each `(pid, last exit status)`, or the failed
        `Ran` of the read."""
        ran = fence.run("print", self.domain)
        return _domain(ran.stdout)[0] if ran.ok else ran

    def _unload(self, label: str, fence: Fence) -> Ran:
        """`bootout`; a label that was not loaded is done."""
        ran = fence.run("bootout", self.target(label))
        if ran.ok:
            return ran
        if ran.reason is None and ran.returncode in _NOT_LOADED:
            return _replaced(ran, ok=True)
        read = self._services(fence)
        if not isinstance(read, Ran) and label not in read:
            return _replaced(ran, ok=True)
        return ran

    def _load(self, label: str, fence: Fence, path: Path) -> Ran:
        """`bootstrap` of the plist at `path`; a label already loaded is done."""
        if not path.is_file():
            return Ran(False, None, "", "", tuple(_argv("launchd", "bootstrap", self.domain,
                                                        str(path))),
                       f"no plist at {path}")
        ran = fence.run("bootstrap", self.domain, str(path))
        if ran.ok or ran.reason is not None:
            return ran
        read = self._services(fence)
        if not isinstance(read, Ran) and label in read:
            return _replaced(ran, ok=True)
        return ran

    def _restart(self, label: str, fence: Fence, unit: UnitRef) -> Ran:
        """`kickstart -k`; a daemon that is not loaded is bootstrapped instead."""
        ran = fence.run("kickstart", self.target(label))
        if ran.ok or ran.reason is not None:
            return ran
        read = self._services(fence)
        if not isinstance(read, Ran) and label not in read:
            return self._load(label, fence, unit.paths[0] if unit.paths else self.plist(label))
        return ran

    def _end_run(self, label: str, fence: Fence) -> Ran:
        """`kill SIGTERM` of a timer's run; a timer with no run in progress is done."""
        ran = fence.run("kill", self.target(label))
        if ran.ok or ran.reason is not None:
            return ran
        read = self._services(fence)
        if not isinstance(read, Ran) and not read.get(label, (None, None))[0]:
            return _replaced(ran, ok=True)
        return ran


# -- the plist text ---------------------------------------------------------------------------

def _schedule(spec: Spec) -> dict:
    schedule = spec.schedule
    if schedule is None:
        raise RenderError("declaration", f"{spec.unit}: a timer with no schedule")
    if schedule.every:
        return {"StartInterval": schedule.every * 60}
    if schedule.daily:
        return {"StartCalendarInterval": [{"Hour": h, "Minute": m}
                                          for h, m in schedule.at]}
    return {"StartCalendarInterval": [{"Weekday": _WEEKDAY[day], "Hour": h, "Minute": m}
                                      for h, m in schedule.at for day in schedule.days]}


def _document(head: list[str], body: Mapping) -> str:
    """The plist text: the XML declaration and doctype, the comments in `head`, then the
    top-level dict with one key per line, its value beside it (a container on the next
    line)."""
    lines = [_HEADER.rstrip("\n"), *head, '<plist version="1.0">', "<dict>"]
    for key, value in body.items():
        if isinstance(value, (dict, list)):
            lines += [f"  <key>{_escape(key)}</key>", f"  {_value(value)}"]
        else:
            lines.append(f"  <key>{_escape(key)}</key>{_value(value)}")
    lines += ["</dict>", "</plist>"]
    return "\n".join(lines) + "\n"


def _value(value) -> str:
    if value is True:
        return "<true/>"
    if value is False:
        return "<false/>"
    if isinstance(value, int):
        return f"<integer>{value}</integer>"
    if isinstance(value, str):
        return f"<string>{_escape(value)}</string>"
    if isinstance(value, list):
        return "<array>" + "".join(_value(v) for v in value) + "</array>"
    if isinstance(value, dict):
        return "<dict>" + "".join(f"<key>{_escape(k)}</key>{_value(v)}"
                                  for k, v in value.items()) + "</dict>"
    raise ValueError(f"{value!r} has no plist form")


def _escape(text: str) -> str:
    if _UNWRITABLE.search(text):
        raise ValueError(f"{text!r} holds a character a plist cannot carry")
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\r", "&#13;"))


def _prologue(text: str) -> str:
    """The text before the `<plist` element, where the mark comments are."""
    return text.split("<plist", 1)[0]


def _load(path: Path) -> dict | None:
    """A plist file's top-level dict (XML or binary), or None."""
    import plistlib

    try:
        with open(path, "rb") as handle:
            body = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return body if isinstance(body, dict) else None


# -- the domain read ----------------------------------------------------------------------------

_DISABLED_LINE = re.compile(r'"(?P<label>[^"]+)"\s*=>\s*(?P<value>\w+)')


def _domain(text: str) -> tuple[dict[str, tuple[int | None, int | str | None]],
                                dict[str, bool]]:
    """The `services` and `disabled services` blocks of `launchctl print gui/<uid>`.

    `services` maps each loaded label to `(pid, last exit status)`: the pid None when
    the line has `-` or 0, the status None for `-`, an int when it reads as one, else
    its text. `disabled` maps each listed label to whether it is enabled: `disabled` (and
    the older `true`) is False, `enabled` (and `false`) True. Only blocks directly inside
    the domain's own block are read.
    """
    services: dict[str, tuple[int | None, int | str | None]] = {}
    disabled: dict[str, bool] = {}
    depth = 0
    block = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.endswith("{"):
            if depth == 1 and line in ("services = {", "disabled services = {"):
                block = line.split(" = ")[0]
            depth += 1
            continue
        if line.startswith("}"):
            depth -= 1
            if depth <= 1:
                block = None
            continue
        if depth != 2 or block is None:
            continue
        if block == "services":
            parts = line.split(None, 2)
            if len(parts) != 3 or not (parts[0] == "-" or parts[0].isdigit()):
                continue
            pid = int(parts[0]) if parts[0].isdigit() and int(parts[0]) > 0 else None
            status: int | str | None = None
            if parts[1] != "-":
                try:
                    status = int(parts[1])
                except ValueError:
                    status = parts[1]
            services[parts[2].strip()] = (pid, status)
        else:
            found = _DISABLED_LINE.fullmatch(line)
            if found and found["value"] in ("disabled", "enabled", "true", "false"):
                disabled[found["label"]] = found["value"] in ("enabled", "false")
    return services, disabled


def _started(pid: int) -> float | None:
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def _keeps(change: UnitChange) -> bool:
    """A daemon with `upgrade = "keep"` that stays enabled: rewritten without a restart."""
    return (change.kind == "daemon" and change.entry.get("upgrade") == "keep"
            and change.enabled and change.enabled_before is not False)


BACKEND = LaunchdBackend()
