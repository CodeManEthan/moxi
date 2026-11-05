"""The walk: whether Moxi or an agent started the terminal a `moxi` run is typed in.

`walk(view, run)` first reads the run's own start environment, as noted when `moxi` was
imported: the start mark there is E4 and a harness's tool mark there is E2, E4 first when
both are there, and either stops the walk before any process is read. Then it goes up the
run's process tree and returns the first thing it finds:

- `Evidence`: a process shows Moxi, so the run must not record. `kind` is one of
  - `"E1"`, a Moxi program: its command line runs the `moxi` script or a
    `moxi-<module>-<part>` script, or an interpreter run with `-m moxi`, `-m moxi.<name>`
    or `-m moxi_<module>[...]` (on Windows a script is its `.exe` launcher, and the
    Python launchers `py` and `pyw` count as interpreters);
  - `"E2"`, an agent: its program, the base name of its first argument (which a harness
    may set as its process title), is `claude`, `codex` or `opencode`, alone or followed
    by `-` and more, since a harness names its own helpers after itself (Codex's Linux
    tool sandbox runs under `codex-linux-sandbox`); on Windows the name is matched after
    dropping a trailing `.exe` and without case. Two more signs name a harness whose
    sandbox's first process carries no such name: that first process (no visible parent)
    is a program run from a file descriptor, its first argument `/proc/self/fd/<n>` (as
    Claude Code's Linux sandbox runs it), or the run's own start environment holds a
    harness's tool mark (`CLAUDECODE`). E2 can only stop a record, so it errs wide: a
    person's own `codex-x` stops it too, and so does a terminal of his that inherited
    `CLAUDECODE`;
  - `"E3"`, a Moxi unit: on Linux its cgroup is a `moxi-<module>-<name>.service`, on
    macOS it is the running process of a `moxi.<module>.<name>` launchd job;
  - `"E4"`, the start mark: `MOXI_STARTED_BY` is in the environment it started with
    (the run's own, as noted when `moxi` was imported). A variable counts whatever its
    value, an empty one included, and on Windows its name matches without case.
- `Undecided`: the walk can't decide, so the run must not record either. `kind` is
  `"N1"` (a process it has to judge can't be read: `reason` `"ended"` when a process
  ended during the walk, `"unread"` when a read failed, `"deep"` past `LIMIT`
  processes, `"loop"` round a loop) or `"N2"` (`reason` `"tmux"`: a tmux server nothing
  places, while slots' server can run outside a unit here, so it can't be told from the
  person's own).
- None: nothing of Moxi's or an agent's up to and including the first process with no
  visible parent (a pid namespace's first process, say), so the run may record.

The run's own entry chain is judged by E3 and E4 only (the run's own E4 is the read
above), since it runs the `moxi` script by definition: on Linux and macOS that is the run's process alone (a `#!` script runs in
the interpreter's own process). On Windows it also takes, from the run up and each the
direct parent of the one below it, at most one venv redirector (a parent whose image is
the run's `sys.executable`, when the run's own image is another file) and then at most
one script launcher (a parent whose image is the run's `sys.argv[0]`, or that with `.exe`
put back when it doesn't end in `.exe`, since the uv and pip launchers strip it), and then
at most one Python launcher for `py -m moxi` (a parent whose image's base name is `py.exe`
and whose command line, after py's version options, is the run's own `sys.orig_argv`
from `-m` on). Every process above the chain is judged by E1 to E4. The walk never
starts above any other Moxi process, so a `moxi.exe` that isn't the run's own launcher
shows E1.

A shielded process (another account's, or one the system keeps from its own account) is
judged by its command line alone, where the system shows one, and passed when it shows
nothing. A parent that started after its child is no parent: on Linux and macOS, where a
living process always has a living parent, that is a parent that ended during the walk,
an N1 `Undecided`; on Windows, which never reparents, it ends the walk, as does a parent
pid with no process. A parent the system hides (Linux `hidepid`) ends the walk too. A process
whose parent ends the walk in any of these ways is judged first, a shielded one by its
command line, so a pid namespace's first process is judged before the walk ends there.
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

#: The agents' programs. A program named one of these, alone or followed by `-` and more,
#: is that agent or a helper it names after itself.
HARNESSES = ("claude", "codex", "opencode")

E1, E2, E3, E4 = "E1", "E2", "E3", "E4"
N1, N2 = "N1", "N2"
ENDED, UNREAD, DEEP, LOOP, TMUX = "ended", "unread", "deep", "loop", "tmux"

_SCRIPT = re.compile(r"moxi|moxi-([a-z0-9]+)-[a-z0-9]+(-[a-z0-9]+)*")
_MODULE = re.compile(r"moxi(\.\w+)*|moxi_([a-z0-9]+)(\.\w+)*")
_INTERPRETER = re.compile(r"python[0-9.]*[tw]?|pypy[0-9.]*")
#: The Python launcher's programs on Windows, and its version options: `-3`, `-3.12`,
#: `-V:<tag>`.
_PY_LAUNCHERS = ("py", "pyw")
_PY_VERSION = re.compile(r"-[0-9]+(\.[0-9]+)?|-V:\S+")
#: A program run from a file descriptor, as a harness's sandbox may run its own binary.
FD_PROGRAM = re.compile(r"/proc/self/fd/[0-9]+")
#: Interpreter options that take the next argument (or the rest of their own) as a value.
_VALUED = "WX"
_LONG_VALUED = ("--check-hash-based-pycs",)


@dataclass(frozen=True)
class Run:
    """The run the walk starts from: its pid, `sys.executable`, `sys.argv[0]`, the
    mark's value in the environment it started with (None: it had none),
    `sys.orig_argv`, the interpreter's own command line, and the harness tool marks the
    environment it started with holds."""

    pid: int
    executable: str
    argv0: str
    started_by: str | None
    orig_argv: tuple[str, ...] = ()
    tool_marks: tuple[str, ...] = ()

    @classmethod
    def here(cls) -> "Run":
        from moxi import _mark

        argv = getattr(sys, "argv", None) or [""]
        return cls(os.getpid(), sys.executable or "", argv[0] or "", _mark.started_by,
                   tuple(getattr(sys, "orig_argv", None) or ()), _mark.tool_marks)


@dataclass(frozen=True)
class Evidence:
    """A process showed Moxi. `kind` is `E1` to `E4`; `pid` and `program` (the base name
    of its first argument, else the system's name for it) name the process; `detail` is
    what was found: for E1 the script or `-m` module it runs; for E2 the harness its name
    shows, else the `/proc/self/fd/<n>` its top runs, else the tool mark the run's own
    environment holds; for E3 the unit; for E4 the mark's value. `module` is the Moxi module named, where one is
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
        if (found := self.own_environment()) is not None:
            return found
        try:
            own = self.view.read(self.run.pid)
        except (Gone, Unread) as error:
            return Undecided(N1, UNREAD, self.run.pid, getattr(error, "name", ""),
                             str(error))
        self.seen.add(own.pid)
        if (found := self.unit(own)) is not None:
            return found
        above = self.up(own)
        if self.windows:
            for match in (self.redirector, self.launcher, self.python_launcher):
                if isinstance(above, Proc) and match(above, own):
                    if (found := self.chain_judge(above)) is not None:
                        return found
                    above = self.up(above)
        while isinstance(above, Proc):
            if (found := self.judge(above)) is not None:
                return found
            top, above = above, self.up(above)
            if above is None and (found := self.fd_top(top)) is not None:
                return found
        return above

    def own_environment(self) -> Evidence | None:
        """E4's mark, then E2's tool marks, in the run's own start environment as noted at
        import; read before any process is."""
        from moxi import _mark

        program = self.own_program()
        if self.run.started_by is not None:
            return Evidence(E4, self.run.pid, program, self.run.started_by)
        for mark in _mark.TOOL_MARKS:
            if mark in self.run.tool_marks:
                return Evidence(E2, self.run.pid, program, mark)
        return None

    def own_program(self) -> str:
        argv = self.run.orig_argv or (self.run.executable, self.run.argv0)
        name = self.base(argv[0]) if argv else ""
        return self.program(Proc(self.run.pid, 0, 0.0, tuple(argv), None, name=name))

    def fd_top(self, top: Proc) -> Evidence | None:
        """E2 for a process with no visible parent that runs a program from a file
        descriptor: a harness's sandbox may run its own binary so, under no name."""
        if top.argv and FD_PROGRAM.fullmatch(top.argv[0]):
            return Evidence(E2, top.pid, self.program(top), top.argv[0])
        return None

    # -- the entry chain -----------------------------------------------------------------

    def redirector(self, parent: Proc, own: Proc) -> bool:
        exe = self.norm(self.run.executable)
        return (own.image is not None and self.norm(own.image) != exe
                and parent.image is not None and self.norm(parent.image) == exe)

    def launcher(self, parent: Proc, own: Proc) -> bool:
        # The uv and pip launchers strip a trailing `.exe` from `sys.argv[0]` (case
        # sensitively) before `cli.main` runs, so the launcher's image is `argv0` itself or
        # `argv0` with `.exe` put back. Both name the one file the launcher ran from.
        argv0 = self.run.argv0
        if not argv0 or parent.image is None:
            return False
        names = [argv0]
        if not argv0.lower().endswith(".exe"):
            names.append(argv0 + ".exe")
        image = self.norm(parent.image)
        return any(image == self.norm(name) for name in names)

    def python_launcher(self, parent: Proc, own: Proc) -> bool:
        # `py -m moxi ...` starts the interpreter as a child and waits. Its `py.exe` is the
        # run's own when, after py's version options, it runs exactly what the interpreter
        # was given from `-m` on: the run's own module and arguments.
        if parent.image is None or ntpath.basename(parent.image).lower() != "py.exe":
            return False
        orig = list(self.run.orig_argv[1:])
        if not parent.argv or "-m" not in orig:
            return False
        rest = list(parent.argv[1:])
        while rest and _PY_VERSION.fullmatch(rest[0]):
            rest.pop(0)
        return rest == orig[orig.index("-m"):]

    def norm(self, path: str) -> str:
        if self.windows:
            return ntpath.normcase(ntpath.abspath(path))
        return os.path.normcase(os.path.abspath(path))

    def chain_judge(self, proc: Proc) -> Evidence | None:
        """E3 and E4 for a process of the run's entry chain above the run's own process,
        whose own E4 is read before the walk climbs."""
        if (found := self.unit(proc)) is not None:
            return found
        if (mark := self.mark(proc)) is not None:
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
        if self.interpreter(name):
            kind, target = self.runs(proc.argv)
            if kind == "script":
                return self.base(target)
        return name or proc.name

    def moxi_program(self, argv) -> tuple[str | None, str | None]:
        """(what makes it a Moxi program, the module it names) or (None, None)."""
        name = self.base(argv[0])
        if (script := _SCRIPT.fullmatch(name)) is not None:
            return name, script.group(1)
        if not self.interpreter(name):
            return None, None
        kind, target = self.runs(argv)
        if kind == "module" and (module := _MODULE.fullmatch(target)) is not None:
            return f"-m {target}", module.group(2)
        if kind == "script":
            script_name = self.base(target)
            if (script := _SCRIPT.fullmatch(script_name)) is not None:
                return script_name, script.group(1)
        return None, None

    def interpreter(self, name: str) -> bool:
        """Whether a program's base name is a Python interpreter's, or on Windows the
        Python launcher's (`py`, `pyw`), which hands its command line to one."""
        return (_INTERPRETER.fullmatch(name) is not None
                or (self.windows and name in _PY_LAUNCHERS))

    def runs(self, argv) -> tuple[str | None, str]:
        """What an interpreter's command line runs: ("module", name) for `-m`,
        ("script", path) for a script, else (None, "") (`-c`, stdin, nothing). The Python
        launcher's own version options come first and are passed over."""
        args = list(argv[1:])
        at = 0
        if self.windows and self.base(argv[0]) in _PY_LAUNCHERS:
            while at < len(args) and _PY_VERSION.fullmatch(args[at]):
                at += 1
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
        """The harness a program is, or is a helper of: its name is the harness's alone
        or followed by `-` and more. On Windows `base` has already dropped `.exe` and
        folded case."""
        for name in self.names(argv):
            for harness in HARNESSES:
                if name == harness or name.startswith(harness + "-"):
                    return harness
        return None

    def tmux(self, argv) -> bool:
        return "tmux" in self.names(argv)
