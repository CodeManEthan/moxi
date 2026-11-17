"""`moxi-dev timed schedule` and `moxi-dev timed unschedule`: the machine's timer for the
scheduled timed run, as systemd user units (Linux only).

`moxi-dev timed schedule --machine NAME [--slack F] [--idle-line CPUS] [--record-dir DIR]
[--stamp] [--take NAME=DIR]...` runs once from the tree the machine measures, the source,
which is a uv workspace root:

- a git clone: the run tree is a worktree of it, `git worktree add --detach <tree> main`,
  and the record goes to the clone's git directory unless `--record-dir` names another;
- a tree with no git (a synced copy of a clone): the run tree is the tick's verified copy
  (`moxi_dev.tick.copy_tree`), `--record-dir` is required and `--stamp` refused.

The run directory is `<user cache>/moxi-dev/timed/<12 hex of the source's path>` (the
user cache is `$XDG_CACHE_HOME`, else `~/.cache`). It holds the run tree, `tree/`; the
settings, `schedule.json`, in the fields `moxi_dev.tick` reads; and the tick script,
`tick.py`, a copy of `moxi_dev/tick.py`. Both files are written to a part file, then
renamed into place.

The units go in the user unit directory (`$XDG_CONFIG_HOME/systemd/user`, else
`~/.config/systemd/user`), each starting with the mark `# moxi-dev timed: <run
directory>`:

- `moxi-dev-timed.timer`: `OnCalendar=*-*-* 08..11:00:00`, `Persistent=false`;
- `moxi-dev-timed.service`: `Type=oneshot` in the run directory, `SuccessExitStatus=3`,
  `TimeoutStartSec=40min`, running the tick script with `-I` under the uv-managed Python
  3.12 through `/bin/sh -c`, which first tests that the interpreter is there and, when it
  isn't, prints `moxi-dev timed tick: <machine>: interpreter missing: <python> (Python
  <version>); run schedule again` and exits 2;
- with `--stamp`, `moxi-dev-timed-stamp.path`, `PathChanged=` main's reflog, and
  `moxi-dev-timed-stamp.service`, which runs `tick.py --stamp-only` the same way.

`schedule`, in order; each step that fails exits 2 with its reason and leaves no unit
it wrote:

1. The source (the current directory) is a uv workspace root, and on a clone `git
   rev-parse --show-toplevel` prints it.
2. `uv`, Python 3.12 (`uv python find --system 3.12`, and its version) and, on a clone,
   `git` resolve to absolute paths.
3. With `--stamp`, main's reflog exists; each `--take` folder exists.
4. Linger: anything but `Linger=yes` from `loginctl show-user` prints that the timer fires
   only while the user is logged in, and goes on.
5. The run tree: when it already exists, the run lock is taken first without waiting
   ("a timed run is measuring here" when it is held) and held until `schedule` ends, so
   a refused `schedule` writes nothing. A new run tree is made (the worktree, or the
   first verified copy, which stops at a tree not settled); then `uv sync --locked` in it.
6. The units are written and each checked with `systemd-analyze --user verify`. A unit
   that fails puts back what each file held before (removing a new one); a run directory
   this `schedule` made is removed. Then `tick.py` and `schedule.json`, the user manager's
   reload, and `systemctl --user enable --now` of the timer and, with `--stamp`, the path
   unit. Stamp units an earlier `schedule --stamp` wrote are stopped and removed when
   `--stamp` is not given.
7. The last lines name the one-tick test.

A unit directory's drop-ins (`<unit>.d/`) are never touched. When the units serve
another source's run directory, `schedule` takes them over and says so; that source's run
directory stays until `unschedule` runs there.

`moxi-dev timed unschedule`, from the source: takes the run lock without waiting when the
run tree exists (refused, it changes nothing), stops and disables the timer and the path
unit, removes the units that carry this run directory's mark, reloads the user manager,
and removes the run directory (on a clone with `git worktree remove --force`). It never
touches the record, the source, a drop-in or units another source's `schedule` wrote.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import check, tick, timed, timed_record

TIMER = "moxi-dev-timed.timer"
SERVICE = "moxi-dev-timed.service"
STAMP_PATH = "moxi-dev-timed-stamp.path"
STAMP_SERVICE = "moxi-dev-timed-stamp.service"
UNITS = (TIMER, SERVICE, STAMP_PATH, STAMP_SERVICE)
#: The first line of every unit `schedule` writes, followed by the run directory.
MARK = "# moxi-dev timed: "
TREE = "tree"
TICK_SCRIPT = "tick.py"
CALENDAR = "*-*-* 08..11:00:00"
SERVICE_TIMEOUT = "40min"
#: What `/bin/sh -c` runs for a unit, given the interpreter, the tick script, the machine
#: and the interpreter's version as `$0` to `$3`.
GUARD = ('test -x "$0" || { printf "%s\\n" "moxi-dev timed tick: $2: interpreter missing: '
         '$0 (Python $3); run schedule again"; exit 2; }; exec "$0" -I "$1"')
LINGER_LINE = ("the timer fires only while you are logged in; `loginctl enable-linger` keeps "
               "it firing")
MEASURING = "a timed run is measuring here"


class ScheduleError(Exception):
    """`schedule` or `unschedule` stops; the message says why (exit 2)."""


def _system() -> str:
    return sys.platform


def _refuse_platform() -> None:
    system = _system()
    if system == "darwin":
        raise ScheduleError("the timer is Linux only; on macOS run `moxi-dev timed` by hand")
    if system.startswith(("win", "cygwin", "msys")):
        raise ScheduleError("the timer is Linux only; a Windows machine's scheduled run is "
                            "not built yet")
    if not system.startswith("linux"):
        raise ScheduleError(f"the timer is Linux only; this system is {system}")


def _home(env: Mapping[str, str]) -> Path:
    return Path(env["HOME"]) if env.get("HOME") else Path.home()


def _xdg(env: Mapping[str, str], name: str, default: str) -> Path:
    value = env.get(name, "")
    return Path(value) if value and os.path.isabs(value) else _home(env) / default


def run_dir_of(source: Path, env: Mapping[str, str]) -> Path:
    """The run directory of a source (module doc)."""
    digest = hashlib.sha256(str(source).encode("utf-8", "surrogateescape")).hexdigest()
    return _xdg(env, "XDG_CACHE_HOME", ".cache") / "moxi-dev" / "timed" / digest[:12]


def unit_dir(env: Mapping[str, str]) -> Path:
    """The user unit directory (module doc)."""
    return _xdg(env, "XDG_CONFIG_HOME", ".config") / "systemd" / "user"


# --- unit text -------------------------------------------------------------------------

def _escape(text: str) -> str:
    """A value with systemd's specifier and variable marks doubled, as it reads it back."""
    return text.replace("%", "%%").replace("$", "$$")


def _word(text: str) -> str:
    """One quoted word of a unit's command line."""
    return '"' + _escape(text.replace("\\", "\\\\").replace('"', '\\"')) + '"'


def exec_line(python: str, script: Path, machine: str, version: str,
              extra: Sequence[str] = ()) -> str:
    """The `ExecStart=` value: the interpreter guard (`GUARD`) running the tick script."""
    guard = GUARD + "".join(f" {arg}" for arg in extra)
    words = [_word(guard), _word(python), _word(str(script)), _word(machine), _word(version)]
    return "/bin/sh -c " + " ".join(words)


@dataclass(frozen=True)
class Plan:
    """What `schedule` writes for one source."""

    machine: str
    source: Path
    has_git: bool
    run_dir: Path
    record_dir: Path | None
    slack: float
    idle_line: float | None
    takes: dict[str, Path]
    stamp: bool
    uv: str
    git: str | None
    python: str
    python_version: str
    common_dir: Path | None = None

    @property
    def tree(self) -> Path:
        return self.run_dir / TREE

    def settings(self) -> dict:
        """`schedule.json`, in the fields `moxi_dev.tick` reads."""
        return {"format": tick.SCHEDULE_FORMAT, "machine": self.machine,
                "source": str(self.source), "has_git": self.has_git,
                "record_dir": str(self.record_dir) if self.record_dir else None,
                "slack": self.slack, "idle_line": self.idle_line,
                "takes": {name: str(where) for name, where in self.takes.items()},
                "stamp": self.stamp, "uv": self.uv, "git": self.git,
                "python": self.python, "python_version": self.python_version}

    def units(self) -> dict[str, str]:
        """Each unit's name and text."""
        mark = f"{MARK}{self.run_dir}\n"
        script = self.run_dir / TICK_SCRIPT
        work = _escape(str(self.run_dir))
        out = {
            TIMER: (f"{mark}[Unit]\nDescription=moxi-dev timed: the scheduled timed run's "
                    f"ticks, hourly from 08:00 to 11:00\n\n[Timer]\nOnCalendar={CALENDAR}\n"
                    f"Persistent=false\nUnit={SERVICE}\n\n[Install]\n"
                    f"WantedBy=timers.target\n"),
            SERVICE: (f"{mark}[Unit]\nDescription=moxi-dev timed: one tick of the scheduled "
                      f"timed run\n\n[Service]\nType=oneshot\nWorkingDirectory={work}\n"
                      f"ExecStart={exec_line(self.python, script, self.machine, self.python_version)}\n"
                      f"SuccessExitStatus=3\nTimeoutStartSec={SERVICE_TIMEOUT}\n"),
        }
        if self.stamp and self.common_dir is not None:
            reflog = _escape(str(self.common_dir / "logs" / "refs" / "heads" / "main"))
            out[STAMP_PATH] = (f"{mark}[Unit]\nDescription=moxi-dev timed: stamp the source "
                               f"when main moves\n\n[Path]\nPathChanged={reflog}\n"
                               f"Unit={STAMP_SERVICE}\n\n[Install]\n"
                               f"WantedBy=default.target\n")
            stamp_exec = exec_line(self.python, script, self.machine, self.python_version,
                                   ("--stamp-only",))
            out[STAMP_SERVICE] = (f"{mark}[Unit]\nDescription=moxi-dev timed: write the "
                                  f"source's stamp\n\n[Service]\nType=oneshot\n"
                                  f"WorkingDirectory={work}\nExecStart={stamp_exec}\n")
        return out


# --- running tools ---------------------------------------------------------------------

def _call(argv: Sequence[str], env: Mapping[str, str], cwd: Path | None = None
          ) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(argv), cwd=cwd, env=dict(env), capture_output=True,
                              text=True, check=False)
    except OSError as exc:
        raise ScheduleError(f"could not run {argv[0]}: {exc}") from exc


def _why(proc: subprocess.CompletedProcess) -> str:
    lines = (proc.stderr or proc.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"exit {proc.returncode}"


def _need(argv: Sequence[str], env: Mapping[str, str], cwd: Path | None = None) -> str:
    """The command's stdout; ScheduleError naming it when it fails."""
    proc = _call(argv, env, cwd)
    if proc.returncode != 0:
        raise ScheduleError(f"{' '.join(argv[:3])}: {_why(proc)}")
    return proc.stdout


def _which(name: str, env: Mapping[str, str]) -> str:
    found = shutil.which(name, path=env.get("PATH"))
    if not found:
        raise ScheduleError(f"{name} is not on PATH")
    return os.path.abspath(found)


def _systemctl(env: Mapping[str, str], *args: str) -> None:
    _need([_which("systemctl", env), "--user", *args], env)


def _put(target: Path, data: bytes, mode: int = 0o644) -> None:
    """Write `target` to a part file beside it, then rename it into place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f"{target.name}.{os.getpid()}.part")
    try:
        part.write_bytes(data)
        os.chmod(part, mode)
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)


def _lock(source: Path, has_git: bool, tree: Path) -> int:
    """The run lock of a run tree, taken without waiting; ScheduleError when it is held."""
    if has_git:
        record_dir = timed.common_record_dir(source)
        if record_dir is None:
            raise ScheduleError(f"{source}: git names no common directory")
        lock_path = record_dir / timed.RUN_LOCK
    else:
        lock_path = tree / timed.TREE_TIMED_DIR / timed.RUN_LOCK
    try:
        return timed.lock_run(lock_path, {})
    except timed.TimedError:
        raise ScheduleError(MEASURING) from None
    except OSError as exc:
        raise ScheduleError(f"the run lock {lock_path}: {exc}") from exc


def find_clone(source: Path, env: Mapping[str, str]) -> tuple[bool, str | None, Path | None]:
    """Whether the source is in a git checkout, git's path (None when not on PATH) and the
    checkout's top level (None outside one)."""
    found = shutil.which("git", path=env.get("PATH"))
    git = os.path.abspath(found) if found else None
    top = None
    if git is not None:
        proc = _call([git, "rev-parse", "--show-toplevel"], env, source)
        top = Path(proc.stdout.strip()) if proc.returncode == 0 else None
    return top is not None or (source / ".git").exists(), git, top


def _unit_mark(path: Path) -> str | None:
    """The run directory a unit file's mark names, or None for a file without one."""
    try:
        with path.open(encoding="utf-8") as fh:
            first = fh.readline().rstrip("\n")
    except OSError:
        return None
    return first[len(MARK):] if first.startswith(MARK) else None


# --- schedule --------------------------------------------------------------------------

def _schedule_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moxi-dev timed schedule",
        description="Write this machine's timer for the scheduled timed run, measuring the "
                    "tree this runs from (Linux only).")
    parser.add_argument("--machine", metavar="NAME", required=True,
                        help="the machine the record files the runs under")
    parser.add_argument("--slack", type=float, default=1.0, metavar="F",
                        help="the machine's set factor, which every scheduled run passes "
                             "as MOXI_TEST_TIMED_SLACK (default 1)")
    parser.add_argument("--idle-line",  # noqa: MX1 -- a count of CPUs, not a duration
                        type=float, default=None, metavar="CPUS",
                        help="the run's --idle-line (default: the verb's)")
    parser.add_argument("--record-dir", metavar="DIR",
                        help="keep the record here (required for a tree with no git)")
    parser.add_argument("--stamp", action="store_true",
                        help="write the stamp a synced copy of this clone is verified "
                             "against, whenever main moves and at each tick")
    parser.add_argument("--take", metavar="NAME=DIR", action="append", default=[],
                        help="after each run, merge host NAME's results from DIR/NAME; may "
                             "be given more than once")
    return parser


def _absolute(raw: str) -> Path:
    return Path(os.path.abspath(Path(raw).expanduser()))


@dataclass
class _Schedule:
    args: argparse.Namespace
    source: Path
    env: Mapping[str, str]
    say: Callable[[str], None] = print
    wrote: dict[str, bytes | None] = field(default_factory=dict)

    def plan(self) -> Plan:
        """Checks 1 to 3, and the plan they give."""
        args, source, env = self.args, self.source, self.env
        if not timed_record.MACHINE_NAME.fullmatch(args.machine):
            raise ScheduleError(f"--machine takes letters, digits and ._-; {args.machine!r} "
                                f"is not one")
        if not (math.isfinite(args.slack) and args.slack >= 1):
            raise ScheduleError(f"--slack is a finite number of at least 1; it is "
                                f"{args.slack!r}")
        if args.idle_line is not None and not (math.isfinite(args.idle_line)
                                               and args.idle_line > 0):
            raise ScheduleError(f"--idle-line is a number of CPUs above 0; it is "
                                f"{args.idle_line!r}")
        takes: dict[str, Path] = {}
        for raw in args.take:
            name, sep, where = raw.partition("=")
            if not sep or not where or not timed_record.MACHINE_NAME.fullmatch(name):
                raise ScheduleError(f"--take is NAME=DIR, NAME of letters, digits and ._-; "
                                    f"{raw!r} is not")
            if name == args.machine:
                raise ScheduleError(f"--take {name} names this machine")
            takes[name] = _absolute(where)
        has_git, git, top = find_clone(source, env)
        # 1. the source
        if timed.workspace_root(source) != source:
            raise ScheduleError(f"{source} is not a uv workspace root; run schedule from it")
        if top is not None and top != source:
            raise ScheduleError(f"{source} is not its clone's top level ({top})")
        # 2. the tools
        uv = _which("uv", env)
        common_dir = None
        if has_git:
            if git is None:
                raise ScheduleError("git is not on PATH, and the source is a clone")
            common_dir = Path(_need([git, "rev-parse", "--path-format=absolute",
                                     "--git-common-dir"], env, source).strip())
        base = check._base_env(env)
        python = _need([uv, "python", "find", "--system", check.PYTHON], base, source).strip()
        if not python or not os.path.isabs(python):
            raise ScheduleError(f"uv python find names no absolute path for Python "
                                f"{check.PYTHON} ({python!r})")
        said = _need([python, "--version"], base).strip()
        found = re.fullmatch(r"Python (\S+)", said)
        if not found or not (found[1] + ".").startswith(check.PYTHON + "."):
            raise ScheduleError(f"{python} is not Python {check.PYTHON} ({said!r})")
        # 3. the stamp's reflog, the takes' folders, and what a tree with no git needs
        if not has_git:
            if args.stamp:
                raise ScheduleError("--stamp needs a git clone; this source has no git")
            if not args.record_dir:
                raise ScheduleError("a tree with no git keeps its record where --record-dir "
                                    "says; pass it")
        if args.stamp and _call([git, "reflog", "exists", "refs/heads/main"], env,
                                source).returncode != 0:
            raise ScheduleError("--stamp watches main's reflog, and this clone has none")
        for name, where in takes.items():
            if not where.is_dir():
                raise ScheduleError(f"--take {name}: {where} is not a folder")
        return Plan(machine=args.machine, source=source, has_git=has_git,
                    run_dir=run_dir_of(source, env),
                    record_dir=_absolute(args.record_dir) if args.record_dir else None,
                    slack=args.slack, idle_line=args.idle_line, takes=takes,
                    stamp=args.stamp, uv=uv, git=git, python=python,
                    python_version=found[1], common_dir=common_dir)

    def linger(self) -> None:
        """Check 4: say when linger is off; never stops."""
        user = self.env.get("USER") or self.env.get("LOGNAME") or str(os.getuid())
        try:
            proc = _call([_which("loginctl", self.env), "show-user", user,
                          "--property=Linger"], self.env)
            held = proc.stdout.strip() if proc.returncode == 0 else ""
        except ScheduleError:
            held = ""
        if held != "Linger=yes":
            self.say(f"moxi-dev timed schedule: {LINGER_LINE}")

    def make_tree(self, plan: Plan) -> None:
        """Check 5, after the lock: the run tree, then its environment."""
        if plan.has_git and not plan.tree.exists():
            _need([plan.git, "worktree", "add", "--detach", str(plan.tree), "main"],
                  self.env, plan.source)
        elif not plan.has_git:
            try:
                tick.copy_tree(plan.source, plan.tree)
            except tick.NotSettled as exc:
                raise ScheduleError(f"the synced tree doesn't match its stamp yet ({exc}); "
                                    f"run schedule again once the sync is idle") from None
            except tick.TickError as exc:
                raise ScheduleError(f"copy failed: {exc}") from None
        _need([plan.uv, "sync", "--locked"], check._base_env(self.env), plan.tree)

    def write_units(self, plan: Plan) -> list[str]:
        """Check 6: the units, written and verified; the names written."""
        where = unit_dir(self.env)
        units = plan.units()
        for name, text in units.items():
            path = where / name
            self.wrote[name] = path.read_bytes() if path.exists() else None
            _put(path, text.encode("utf-8"))
        analyze = _which("systemd-analyze", self.env)
        for name in units:
            proc = _call([analyze, "--user", "verify", str(where / name)], self.env)
            if proc.returncode != 0:
                raise ScheduleError(f"systemd-analyze --user verify {name}: {_why(proc)}")
        return list(units)

    def put_back(self) -> None:
        where = unit_dir(self.env)
        for name, before in self.wrote.items():
            with contextlib.suppress(OSError):
                if before is None:
                    (where / name).unlink(missing_ok=True)
                else:
                    _put(where / name, before)

    def go(self) -> int:
        plan = self.plan()
        self.linger()
        fresh = not plan.run_dir.exists()
        fd = None if fresh or not plan.tree.exists() else _lock(plan.source, plan.has_git,
                                                                plan.tree)
        try:
            try:
                self.make_tree(plan)
                written = self.write_units(plan)
            except BaseException:
                self.put_back()
                if fresh:
                    self.remove_run_dir(plan)
                raise
            self.say_takeover(plan)
            _put(plan.run_dir / TICK_SCRIPT, Path(tick.__file__).read_bytes())
            _put(plan.run_dir / tick.SCHEDULE_JSON,
                 (json.dumps(plan.settings(), indent=1) + "\n").encode("utf-8"))
            started = [TIMER] + ([STAMP_PATH] if STAMP_PATH in written else [])
            self.drop_stamp_units(plan, written)
            _systemctl(self.env, "daemon-reload")
            _systemctl(self.env, "enable", "--now", *started)
        finally:
            if fd is not None:
                os.close(fd)
        self.say(f"moxi-dev timed schedule: {plan.machine}: {', '.join(started)} started; "
                 f"ticks at 08:00, 09:00, 10:00 and 11:00 local time, in {plan.run_dir}")
        self.say("moxi-dev timed schedule: one tick now: systemctl --user start --no-block "
                 f"{SERVICE}")
        self.say(f"moxi-dev timed schedule: once `systemctl --user is-active {SERVICE}` no "
                 f"longer prints activating, its summary line: journalctl --user -u "
                 f"moxi-dev-timed -n 5")
        return 0

    def say_takeover(self, plan: Plan) -> None:
        """Say when the units served another source's run directory."""
        before = self.wrote.get(SERVICE)
        first = (before or b"").decode("utf-8", "replace").split("\n", 1)[0]
        held = first[len(MARK):] if first.startswith(MARK) else None
        if held and held != str(plan.run_dir):
            self.say(f"moxi-dev timed schedule: the timer ran from {held}; it now measures "
                     f"{plan.source}, and that run directory stays until unschedule runs "
                     f"from its source")

    def drop_stamp_units(self, plan: Plan, written: Sequence[str]) -> None:
        """Stop and remove the stamp units an earlier `schedule --stamp` from this source
        wrote, when this one has no `--stamp`."""
        if STAMP_PATH in written:
            return
        where = unit_dir(self.env)
        ours = [name for name in (STAMP_PATH, STAMP_SERVICE)
                if _unit_mark(where / name) == str(plan.run_dir)]
        if not ours:
            return
        if STAMP_PATH in ours:
            _systemctl(self.env, "disable", "--now", STAMP_PATH)
        for name in ours:
            (where / name).unlink(missing_ok=True)

    def remove_run_dir(self, plan: Plan) -> None:
        with contextlib.suppress(ScheduleError):
            if plan.has_git and plan.tree.exists():
                _need([plan.git, "worktree", "remove", "--force", str(plan.tree)], self.env,
                      plan.source)
        shutil.rmtree(plan.run_dir, ignore_errors=True)


def schedule_main(argv: Sequence[str], *, env: Mapping[str, str] | None = None,
                  start: Path | None = None) -> int:
    """`moxi-dev timed schedule` (module doc); its exit code."""
    args = _schedule_parser().parse_args(list(argv))
    env = os.environ if env is None else env
    try:
        _refuse_platform()
        source = (start or Path.cwd()).resolve()
        return _Schedule(args, source, env).go()
    except ScheduleError as exc:
        print(f"moxi-dev timed schedule: {exc}", file=sys.stderr, flush=True)
        return 2


# --- unschedule ------------------------------------------------------------------------

def unschedule(source: Path, env: Mapping[str, str],
               say: Callable[[str], None] = print) -> int:
    """`moxi-dev timed unschedule` from `source` (module doc); its exit code."""
    run_dir = run_dir_of(source, env)
    tree = run_dir / TREE
    has_git, git, _ = find_clone(source, env)
    where = unit_dir(env)
    ours = [name for name in UNITS if _unit_mark(where / name) == str(run_dir)]
    others = [name for name in UNITS
              if name not in ours and _unit_mark(where / name) is not None]
    if not ours and not run_dir.exists():
        say(f"moxi-dev timed unschedule: nothing is scheduled from {source}")
        return 0
    fd = _lock(source, has_git, tree) if tree.exists() else None
    try:
        started = [name for name in (TIMER, STAMP_PATH) if name in ours]
        if started:
            _systemctl(env, "disable", "--now", *started)
        for name in ours:
            (where / name).unlink(missing_ok=True)
        if ours:
            _systemctl(env, "daemon-reload")
        if has_git and tree.exists():
            if git is None:
                raise ScheduleError("git is not on PATH, and the run tree is a worktree")
            _need([git, "worktree", "remove", "--force", str(tree)], env, source)
        shutil.rmtree(run_dir, ignore_errors=True)
    finally:
        if fd is not None:
            os.close(fd)
    if others:
        say(f"moxi-dev timed unschedule: {', '.join(others)} serve another source's run "
            f"directory and stay")
    say(f"moxi-dev timed unschedule: removed {', '.join(ours) or 'no unit'} and {run_dir}")
    return 0


def unschedule_main(argv: Sequence[str], *, env: Mapping[str, str] | None = None,
                    start: Path | None = None) -> int:
    """`moxi-dev timed unschedule` (module doc); its exit code."""
    argparse.ArgumentParser(
        prog="moxi-dev timed unschedule",
        description="Stop and remove this machine's timer for the tree this runs from, and "
                    "its run directory; the record stays.").parse_args(list(argv))
    env = os.environ if env is None else env
    try:
        _refuse_platform()
        return unschedule((start or Path.cwd()).resolve(), env)
    except ScheduleError as exc:
        print(f"moxi-dev timed unschedule: {exc}", file=sys.stderr, flush=True)
        return 2
