"""The fence: every supervisor call `moxi.units` makes, built by one function.

`argv(backend, verb, *args)` builds a command from a closed set of verbs per backend and
refuses anything else, so this is never a general wrapper of `systemctl`, `launchctl` or
`schtasks`. `Fence(backend, runner).run(verb, *args)` runs one and returns a `Ran`, whose
`stderr` is the supervisor's own text, kept as it is: its refusals say more than any
paraphrase.

The closed sets:

- systemd: `systemctl --user` (always `--user`) with `daemon-reload`; `enable` and
  `disable`, each with or without `--now`; `start`, `stop`, `restart`; and `show` with
  `--property=<names>` (with no unit, only `--property=Version`: the manager's version). Also `loginctl show-user <user> -p Linger`, read only.
- launchd: `launchctl` with `bootstrap gui/<uid> <plist>`, `bootout gui/<uid>/<label>`,
  `enable` and `disable` of `gui/<uid>/<label>`, `kickstart -k gui/<uid>/<label>`,
  `kill SIGTERM gui/<uid>/<label>` and `print gui/<uid>`.
- Task Scheduler: `schtasks` with `/create /tn <task> /xml <file> /f`, `/delete /tn <task>
  /f`, `/run /tn <task>`, `/end /tn <task>`, `/query /fo csv /nh`, and `/change /tn <task>`
  with only `/enable` or `/disable`; and one read-only PowerShell call, `powershell.exe
  -NoProfile -NonInteractive -Command <TASKS_SCRIPT>`. No task name ever reaches that
  command's text: the refs travel in `MOXI_TASK_REFS`, set only in the environment of that
  one call, as a JSON array of `{"path", "name"}` objects.

Every call has a 30 s limit. A runner is injectable (`runner(argv, *, env, timeout)`,
returning an object with `returncode`, `stdout` and `stderr` as text), so tests never
reach a real supervisor. The default runner refuses to run under pytest unless
`MOXI_TEST_REAL_SUPERVISOR=1` is set, so a test that forgot its fake fails loudly
instead of acting on the user's own units.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

#: Seconds one supervisor call may take.
TIMEOUT = 30.0
#: The variable that lets a test run a real supervisor.
REAL_SUPERVISOR = "MOXI_TEST_REAL_SUPERVISOR"
#: The variable that carries task refs to the PowerShell read.
TASK_REFS = "MOXI_TASK_REFS"
#: The most characters one environment variable holds on Windows; a longer ref list is
#: split over more than one call.
TASK_REFS_MAX = 32_767

SYSTEMCTL = "systemctl"
LOGINCTL = "loginctl"
LAUNCHCTL = "launchctl"
SCHTASKS = "schtasks"
POWERSHELL = "powershell.exe"

#: The fixed PowerShell script of the foreign task read. It reads its refs from
#: `$env:MOXI_TASK_REFS`, lists the tasks once, keeps those whose folder and name equal a
#: ref's (`-eq`, never a wildcard or a parsed string) and prints one JSON array, in UTF-8
#: without a byte-order mark whatever the console's code page. The refs are parsed with
#: `-InputObject` and enumerated by `foreach`: Windows PowerShell 5.1 emits a parsed JSON
#: array as one object, so wrapping it in `@(...)` would give a list of one list.
TASKS_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
    "$refs = ConvertFrom-Json -InputObject $env:MOXI_TASK_REFS; "
    "$out = @(); "
    "foreach ($t in @(Get-ScheduledTask)) { "
    "foreach ($r in $refs) { "
    "if ($t.TaskPath -eq $r.path -and $t.TaskName -eq $r.name) { "
    "$i = $t | Get-ScheduledTaskInfo; "
    "$out += [pscustomobject]@{ path = $t.TaskPath; name = $t.TaskName; "
    "state = [string]$t.State; "
    "last_run = $(if ($i.LastRunTime) { $i.LastRunTime.ToString('o') } else { $null }); "
    "last_result = $i.LastTaskResult } } } }; "
    "ConvertTo-Json -InputObject @($out) -Compress -Depth 3")

BACKENDS = ("systemd", "launchd", "schtasks")

# A unit id, a label or a task name: no leading dash (never read as a flag), no control
# characters, nothing empty.
_UNIT = re.compile(r"[^\s\-][^\x00-\x1f]*")
_SYSTEMD_UNIT = re.compile(r"[A-Za-z0-9:_.\\@-]+\.(service|timer|socket)")
_PROPERTIES = re.compile(r"[A-Za-z]+(,[A-Za-z]+)*")
_DOMAIN = re.compile(r"gui/[0-9]+")
_TARGET = re.compile(r"gui/[0-9]+/[A-Za-z0-9._-]+")
_USER = re.compile(r"[A-Za-z0-9._-]+")


class FenceError(ValueError):
    """A supervisor call the fence does not allow."""


class SupervisorRefused(RuntimeError):
    """A real supervisor call asked for under pytest without `MOXI_TEST_REAL_SUPERVISOR=1`."""


@dataclass(frozen=True)
class Ran:
    """What one supervisor call gave. `stderr` is the supervisor's own text. `reason` is
    set only when the call itself failed (no such program, the time limit), not the unit."""

    ok: bool
    returncode: int | None
    stdout: str
    stderr: str
    argv: tuple[str, ...]
    reason: str | None = None

    @property
    def command(self) -> str:
        """The command as one line, for a person."""
        return subprocess.list2cmdline(self.argv) if sys.platform == "win32" else " ".join(
            _quote(a) for a in self.argv)

    def failure(self) -> str:
        """One line: the command and what the supervisor said, as it said it."""
        said = self.reason or self.stderr.strip() or self.stdout.strip() or (
            f"exit {self.returncode}")
        return f"`{self.command}` failed: {said}"


Runner = Callable[..., object]


def argv(backend: str, verb: str, *args: str) -> list[str]:
    """The command for `verb` on `backend`, or `FenceError` for anything outside the set."""
    build = _BUILDERS.get(backend, {}).get(verb)
    if build is None:
        raise FenceError(f"{verb!r} is not a {backend} verb this core runs")
    for arg in args:
        if not isinstance(arg, str):
            raise FenceError(f"{verb}: argument {arg!r} is not a string")
    return build(list(args))


def task_refs_env(refs: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    """The `MOXI_TASK_REFS` values for `refs` (each `(folder, name)`), split so no one
    value passes `TASK_REFS_MAX` characters. Each becomes one PowerShell call."""
    chunks: list[list[dict]] = [[]]
    for folder, name in refs:
        item = {"path": folder, "name": name}
        if chunks[-1] and len(json.dumps(chunks[-1] + [item])) > TASK_REFS_MAX:
            chunks.append([])
        chunks[-1].append(item)
    return [{TASK_REFS: json.dumps(chunk)} for chunk in chunks if chunk]


class Fence:
    """The supervisor calls of one backend, through one runner."""

    def __init__(self, backend: str, runner: Runner | None = None):
        self.backend = backend
        self.runner = runner

    def run(self, verb: str, *args: str, env: Mapping[str, str] | None = None) -> Ran:
        """Run one fenced call; never raises for the call's own failure (see `Ran`).

        `env` adds variables to the call's environment; only the PowerShell read takes
        it (its `MOXI_TASK_REFS`)."""
        cmd = argv(self.backend, verb, *args)
        if env is not None and verb != "tasks":
            raise FenceError(f"{verb}: only the task read takes variables of its own")
        runner = self.runner or default_runner()
        try:
            proc = runner(cmd, env=dict(env) if env else None, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return Ran(False, None, "", "", tuple(cmd), f"timed out after {TIMEOUT:g} s")
        except OSError as exc:
            return Ran(False, None, "", "", tuple(cmd), f"did not run: {exc}")
        out, err = _text(getattr(proc, "stdout", "")), _text(getattr(proc, "stderr", ""))
        code = proc.returncode
        return Ran(code == 0, code, out, err, tuple(cmd))


def under_pytest() -> bool:
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def default_runner() -> Runner:
    """The runner that starts the real program, through `moxi.proc.run` (its own process
    group, stdin closed, the whole group ended at the time limit)."""
    if under_pytest() and os.environ.get(REAL_SUPERVISOR) != "1":
        raise SupervisorRefused(
            "a real supervisor call under pytest; pass a fake runner, or set "
            f"{REAL_SUPERVISOR}=1 for a proof row")

    def run(cmd: list[str], *, env: Mapping[str, str] | None, timeout: float):
        from .. import proc

        full = {**os.environ, "SYSTEMD_COLORS": "0", "NO_COLOR": "1", **(env or {})}
        done = proc.run(cmd, timeout=timeout, env=full)
        return subprocess.CompletedProcess(done.args, done.returncode,
                                           _text(done.stdout), _text(done.stderr))
    return run


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _quote(arg: str) -> str:
    import shlex

    return shlex.quote(arg)


# -- the builders ---------------------------------------------------------------------------

def _refuse(verb: str, args: list[str], shape: str) -> FenceError:
    return FenceError(f"{verb} {' '.join(map(repr, args))}: the fence takes {shape}")


def _units(verb: str, args: list[str]) -> list[str]:
    if not args or not all(_SYSTEMD_UNIT.fullmatch(a) for a in args):
        raise _refuse(verb, args, "one or more unit names (name.service, .timer, .socket)")
    return args


def _systemctl(verb: str, *, now: bool = False, properties: bool = False):
    def build(args: list[str]) -> list[str]:
        head: list[str] = []
        if now and args and args[0] == "--now":
            head, args = ["--now"], args[1:]
        if properties and args and args[0].startswith("--property="):
            if not _PROPERTIES.fullmatch(args[0].removeprefix("--property=")):
                raise _refuse(verb, args, "--property=<Name,Name...>")
            head, args = [args[0]], args[1:]
            if not args and head == ["--property=Version"]:
                # The manager's own version, read before a wrap.
                return [SYSTEMCTL, "--user", verb, *head]
        return [SYSTEMCTL, "--user", verb, *head, *_units(verb, args)]
    return build


def _daemon_reload(args: list[str]) -> list[str]:
    if args:
        raise _refuse("daemon-reload", args, "no arguments")
    return [SYSTEMCTL, "--user", "daemon-reload"]


def _linger(args: list[str]) -> list[str]:
    if len(args) != 1 or not _USER.fullmatch(args[0]):
        raise _refuse("linger", args, "one user name")
    return [LOGINCTL, "show-user", args[0], "-p", "Linger"]


def _launchctl(verb: str, pattern: re.Pattern, *, head: tuple[str, ...] = (),
               plist: bool = False):
    def build(args: list[str]) -> list[str]:
        want = 2 if plist else 1
        if len(args) != want or not pattern.fullmatch(args[0]) or (
                plist and not (os.path.isabs(args[1]) and args[1].endswith(".plist"))):
            raise _refuse(verb, args, "gui/<uid> and a plist's absolute path" if plist
                          else f"one {pattern.pattern}")
        return [LAUNCHCTL, verb, *head, *args]
    return build


def _task(args: list[str], verb: str) -> str:
    if not args or not _UNIT.fullmatch(args[0]) or not args[0].startswith("\\"):
        raise _refuse(verb, args, "a task path starting with \\")
    return args[0]


def _schtasks(verb: str):
    def build(args: list[str]) -> list[str]:
        if verb == "/create":
            if len(args) != 2 or not os.path.isabs(args[1]):
                raise _refuse(verb, args, "a task path and the task XML's absolute path")
            return [SCHTASKS, "/create", "/tn", _task(args, verb), "/xml", args[1], "/f"]
        if verb == "/delete":
            if len(args) != 1:
                raise _refuse(verb, args, "one task path")
            return [SCHTASKS, "/delete", "/tn", _task(args, verb), "/f"]
        if verb in ("/run", "/end"):
            if len(args) != 1:
                raise _refuse(verb, args, "one task path")
            return [SCHTASKS, verb, "/tn", _task(args, verb)]
        if verb == "/change":
            if len(args) != 2 or args[1] not in ("/enable", "/disable"):
                raise _refuse(verb, args, "a task path and /enable or /disable")
            return [SCHTASKS, "/change", "/tn", _task(args, verb), args[1]]
        if args:
            raise _refuse(verb, args, "no arguments")
        return [SCHTASKS, "/query", "/fo", "csv", "/nh"]
    return build


def _tasks(args: list[str]) -> list[str]:
    if args:
        raise _refuse("tasks", args, "no arguments; the refs go in MOXI_TASK_REFS")
    return [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", TASKS_SCRIPT]


_BUILDERS: dict[str, dict[str, Callable[[list[str]], list[str]]]] = {
    "systemd": {
        "daemon-reload": _daemon_reload,
        "enable": _systemctl("enable", now=True),
        "disable": _systemctl("disable", now=True),
        "start": _systemctl("start"),
        "stop": _systemctl("stop"),
        "restart": _systemctl("restart"),
        "show": _systemctl("show", properties=True),
        "linger": _linger,
    },
    "launchd": {
        "bootstrap": _launchctl("bootstrap", _DOMAIN, plist=True),
        "bootout": _launchctl("bootout", _TARGET),
        "enable": _launchctl("enable", _TARGET),
        "disable": _launchctl("disable", _TARGET),
        "kickstart": _launchctl("kickstart", _TARGET, head=("-k",)),
        "kill": _launchctl("kill", _TARGET, head=("SIGTERM",)),
        "print": _launchctl("print", _DOMAIN),
    },
    "schtasks": {
        "/create": _schtasks("/create"),
        "/delete": _schtasks("/delete"),
        "/run": _schtasks("/run"),
        "/end": _schtasks("/end"),
        "/query": _schtasks("/query"),
        "/change": _schtasks("/change"),
        "tasks": _tasks,
    },
}
