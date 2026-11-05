"""The walk: whether Moxi or an agent started the terminal a `moxi` run is typed in.

`walk(view, run)` goes up the run's process tree and returns the first thing it finds:

- `Evidence`: a process shows Moxi, so the run must not record. `kind` is one of
  - `"E1"`, a Moxi program: its command line runs the `moxi` script or a
    `moxi-<module>-<part>` script, or an interpreter run with `-m moxi`, `-m moxi.<name>`
    or `-m moxi_<module>[...]` (on Windows a script is its `.exe` launcher);
  - `"E2"`, an agent: its program, the base name of its first argument (which a harness
    may set as its process title), is `claude`, `codex` or `opencode`;
  - `"E3"`, a Moxi unit: on Linux its cgroup is a `moxi-<module>-<name>.service`, on
    macOS it is the running process of a `moxi.<module>.<name>` launchd job;
  - `"E4"`, the start mark: `MOXI_STARTED_BY` is in the environment it started with
    (the run's own, as noted when `moxi` was imported).
- `Undecided`: the walk can't decide, so the run must not record either. `kind` is
  `"N1"` (a process it has to judge can't be read: `reason` `"ended"` when a process
  ended during the walk, `"unread"` when a read failed, `"deep"` past `LIMIT`
  processes, `"loop"` round a loop) or `"N2"` (`reason` `"tmux"`: a tmux server nothing
  places, while slots' server can run outside a unit here, so it can't be told from the
  person's own).
- None: nothing of Moxi's or an agent's up to the first process with no visible parent,
  so the run may record.

The run's own entry chain is judged by E3 and E4 only, since it runs the `moxi` script
by definition: on Linux and macOS that is the run's process alone (a `#!` script runs in
the interpreter's own process). On Windows it also takes, from the run up and each the
direct parent of the one below it, at most one venv redirector (a parent whose image is
the run's `sys.executable`, when the run's own image is another file) and then at most
one script launcher (a parent whose image is the run's `sys.argv[0]`). Every process
above the chain is judged by E1 to E4. The walk never starts above any other Moxi
process, so a `moxi.exe` that isn't the run's own launcher shows E1.

A shielded process (another account's, or one the system keeps from its own account) is
judged by its command line alone, where the system shows one, and passed when it shows
nothing. A parent that started after its child is no parent: on Linux and macOS, where a
living process always has a living parent, that is a parent that ended during the walk,
an N1 `Undecided`; on Windows, which never reparents, it ends the walk, as does a parent
pid with no process. A parent the system hides (Linux `hidepid`) ends the walk too.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass

from . import _view
from ._view import Gone, Proc, Unread

#: The most processes a walk reads, the run's own included; one more is N1.
LIMIT = 64

#: The agents' programs.
HARNESSES = ("claude", "codex", "opencode")

E1, E2, E3, E4 = "E1", "E2", "E3", "E4"
N1, N2 = "N1", "N2"
ENDED, UNREAD, DEEP, LOOP, TMUX = "ended", "unread", "deep", "loop", "tmux"

_SCRIPT = re.compile(r"moxi|moxi-([a-z0-9]+)-[a-z0-9]+(-[a-z0-9]+)*")
_MODULE = re.compile(r"moxi(\.\w+)*|moxi_([a-z0-9]+)(\.\w+)*")
_INTERPRETER = re.compile(r"python[0-9.]*[tw]?|pypy[0-9.]*")
#: Interpreter options that take the next argument (or the rest of their own) as a value.
_VALUED = "WX"
_LONG_VALUED = ("--check-hash-based-pycs",)


@dataclass(frozen=True)
class Run:
    """The run the walk starts from: its pid, `sys.executable`, `sys.argv[0]`, and the
    mark's value in the environment it started with (None: it had none)."""

    pid: int
    executable: str
    argv0: str
    started_by: str | None

    @classmethod
    def here(cls) -> "Run":
        from moxi import _mark

        argv = getattr(sys, "argv", None) or [""]
        return cls(os.getpid(), sys.executable or "", argv[0] or "", _mark.started_by)


@dataclass(frozen=True)
class Evidence:
    """A process showed Moxi. `kind` is `E1` to `E4`; `pid` and `program` (the base name
    of its first argument, else the system's name for it) name the process; `detail` is
    what was found: for E1 the script or `-m` module it runs, for E2 the harness, for E3
    the unit, for E4 the mark's value. `module` is the Moxi module named, where one is
    (E1 by a module's script or package, E3 by its unit), else None."""

    kind: str
    pid: int
    program: str
    detail: str
    module: str | None = None


@dataclass(frozen=True)
class Undecided:
    """The walk couldn't decide. `kind` is `N1` or `N2`; `reason` is `ended`, `unread`,
    `deep` or `loop` for N1 and `tmux` for N2; `pid` and `program` name the process it
    stopped at (`program` may be empty when nothing of it could be read); `error` says
    what failed, for `unread`."""

    kind: str
    reason: str
    pid: int
    program: str = ""
    error: str = ""


Found = Evidence | Undecided | None


def walk(view=None, run: Run | None = None) -> Found:
    """Walk up from `run` (default: this process) through `view` (default: this
    system's reader) and return what was found (module doc)."""
    view = _view.default_view() if view is None else view
    run = Run.here() if run is None else run
    windows = bool(getattr(view, "windows", False))
    return _Walk(view, run, windows).go()


class _Walk:
    def __init__(self, view, run: Run, windows: bool):
        self.view, self.run, self.windows = view, run, windows
        self.seen: set[int] = set()

    def go(self) -> Found:
        try:
            own = self.view.read(self.run.pid)
        except (Gone, Unread) as error:
            return Undecided(N1, UNREAD, self.run.pid, getattr(error, "name", ""),
                             str(error))
        self.seen.add(own.pid)
        if (found := self.chain_judge(own, own=True)) is not None:
            return found
        above = self.up(own)
        if self.windows:
            for match in (self.redirector, self.launcher):
                if isinstance(above, Proc) and match(above, own):
                    if (found := self.chain_judge(above)) is not None:
                        return found
                    above = self.up(above)
        while isinstance(above, Proc):
            if (found := self.judge(above)) is not None:
                return found
            above = self.up(above)
        return above

    # -- the entry chain -----------------------------------------------------------------

    def redirector(self, parent: Proc, own: Proc) -> bool:
        exe = self.norm(self.run.executable)
        return (own.image is not None and self.norm(own.image) != exe
                and parent.image is not None and self.norm(parent.image) == exe)

    def launcher(self, parent: Proc, own: Proc) -> bool:
        return (bool(self.run.argv0) and parent.image is not None
                and self.norm(parent.image) == self.norm(self.run.argv0))

    def norm(self, path: str) -> str:
        if self.windows:
            return ntpath.normcase(ntpath.abspath(path))
        return os.path.normcase(os.path.abspath(path))

    def chain_judge(self, proc: Proc, own: bool = False) -> Evidence | None:
        """E3 and E4 for a process of the run's entry chain."""
        if (found := self.unit(proc)) is not None:
            return found
        mark = self.run.started_by if own else self.mark(proc)
        if mark is not None:
            return Evidence(E4, proc.pid, self.program(proc), mark)
        return None

    # -- above it ------------------------------------------------------------------------

    def up(self, child: Proc) -> Proc | Undecided | None:
        """`child`'s parent, or what ends the walk there: None (no visible parent) or an
        `Undecided`."""
        pid = child.parent
        if pid <= 0:
            return None
        if pid in self.seen:
            return Undecided(N1, LOOP, pid)
        if len(self.seen) >= LIMIT:
            return Undecided(N1, DEEP, pid)
        try:
            parent = self.view.read(pid)
        except Gone:
            if self.windows:
                return None
            now = self.view.parent(child.pid)
            if now == pid:
                return None  # hidden from this account: the walk ends there
            return Undecided(N1, ENDED, pid)
        except Unread as error:
            return Undecided(N1, UNREAD, pid, error.name, error.error)
        if parent.started > child.started:
            return None if self.windows else Undecided(N1, ENDED, pid, self.program(parent))
        self.seen.add(pid)
        return parent

    def judge(self, proc: Proc) -> Found:
        """E1 to E4, then N2, for a process above the entry chain."""
        program = self.program(proc)
        if proc.argv:
            detail, module = self.moxi_program(proc.argv)
            if detail is not None:
                return Evidence(E1, proc.pid, program, detail, module)
            if (harness := self.harness(proc.argv)) is not None:
                return Evidence(E2, proc.pid, program, harness)
        if proc.shielded:
            return None
        if (found := self.chain_judge(proc)) is not None:
            return found
        if proc.argv and self.tmux(proc.argv) and self.view.slots_loose():
            return Undecided(N2, TMUX, proc.pid, program)
        return None

    def unit(self, proc: Proc) -> Evidence | None:
        if proc.shielded or not proc.unit:
            return None
        found = (_view.SYSTEMD_UNIT.fullmatch(proc.unit)
                 or _view.LAUNCHD_UNIT.fullmatch(proc.unit))
        if found is None:
            return None
        return Evidence(E3, proc.pid, self.program(proc), proc.unit, found.group(1))

    def mark(self, proc: Proc) -> str | None:
        from moxi import _mark

        env: Mapping[str, str] | None = proc.env
        if not env:
            return None
        if self.windows:
            return next((v for k, v in env.items() if k.upper() == _mark.NAME), None)
        return env.get(_mark.NAME)

    # -- command lines -------------------------------------------------------------------

    def base(self, part: str) -> str:
        name = (ntpath if self.windows else posixpath).basename(part)
        if self.windows:
            name = name.casefold()
            for suffix in ("-script.pyw", "-script.py", ".exe"):
                if name.endswith(suffix):
                    name = name[:-len(suffix)]
                    break
        return name

    def names(self, argv) -> tuple[str, ...]:
        """The program's names: the base name of its first argument, and, when that
        argument holds spaces (a process title such as `tmux: server (...)`), the base
        name of its first word too."""
        first = argv[0] if argv else ""
        names = [self.base(first)]
        words = first.split()
        if len(words) > 1:
            names.append(self.base(words[0].rstrip(":")))
        return tuple(names)

    def program(self, proc: Proc) -> str:
        """The process's program, for the lines: the script an interpreter runs, else a
        title's first word, else the base name of its first argument, else the system's
        name for it."""
        first = proc.argv[0] if proc.argv else ""
        words = first.split()
        if len(words) > 1 and not any(sep in words[0] for sep in "/\\"):
            return self.base(words[0].rstrip(":"))
        name = self.base(first)
        if _INTERPRETER.fullmatch(name):
            kind, target = self.runs(proc.argv)
            if kind == "script":
                return self.base(target)
        return name or proc.name

    def moxi_program(self, argv) -> tuple[str | None, str | None]:
        """(what makes it a Moxi program, the module it names) or (None, None)."""
        name = self.base(argv[0])
        if (script := _SCRIPT.fullmatch(name)) is not None:
            return name, script.group(1)
        if not _INTERPRETER.fullmatch(name):
            return None, None
        kind, target = self.runs(argv)
        if kind == "module" and (module := _MODULE.fullmatch(target)) is not None:
            return f"-m {target}", module.group(2)
        if kind == "script":
            script_name = self.base(target)
            if (script := _SCRIPT.fullmatch(script_name)) is not None:
                return script_name, script.group(1)
        return None, None

    def runs(self, argv) -> tuple[str | None, str]:
        """What an interpreter's command line runs: ("module", name) for `-m`,
        ("script", path) for a script, else (None, "") (`-c`, stdin, nothing)."""
        args = list(argv[1:])
        at = 0
        while at < len(args):
            part = args[at]
            if part == "--":
                at += 1
                break
            if part.startswith("--"):
                at += 2 if part in _LONG_VALUED else 1
                continue
            if part.startswith("-") and part != "-":
                for index, flag in enumerate(part[1:], start=1):
                    rest = part[index + 1:]
                    if flag == "m":
                        return "module", rest or (args[at + 1] if at + 1 < len(args) else "")
                    if flag == "c":
                        return None, ""
                    if flag in _VALUED:
                        if not rest:
                            at += 1
                        break
                at += 1
                continue
            break
        if at >= len(args) or args[at] == "-":
            return None, ""
        return "script", args[at]

    def harness(self, argv) -> str | None:
        for name in self.names(argv):
            if name in HARNESSES:
                return name
        return None

    def tmux(self, argv) -> bool:
        return "tmux" in self.names(argv)
