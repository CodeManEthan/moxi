"""The gate, `moxi run`: what every timer the writer installs runs, and what a wrap calls.

    moxi run [--home PATH] [--if MODULE.KEY] --record MODULE/NAME [--log PATH] [--restart]
             [--runtime] [--stop group|process] -- ARGV...
    moxi run [--home PATH] --if MODULE.KEY --record MODULE/NAME --condition
    moxi run [--home PATH] --record MODULE/NAME --result

The first form runs `ARGV` behind a switch. In order:

1. The argument list is parsed strictly before anything else. A bad one exits 2 and runs
   nothing, so a typo can never read as on.
2. `--if MODULE.KEY` reads the module's config file with `tomllib` (not the schema
   reader) and looks the dotted key up. A missing file, key, table or switch-table entry
   is off: one run line, result `off`, exit 0. A file that won't parse or can't be read, a
   value that is not a bool, or a segment of the key that holds a non-table where a
   table is needed: one run line, result `unreadable`, exit 3, so the supervisor marks the
   run failed. Without `--if` (a run-time timer with no switch) this step is skipped.
3. The command runs with the gate's stdin, stdout and stderr. One that cannot start
   gives the run line `not-started` and exit 127.
4. One run line goes to `STATE/<module>/runs.jsonl` through `moxi.files.append_record`
   (a 1 MB cap and one rolled generation): `{"unit": "notes/snapshot", "at": "<UTC
   start>", "seconds": 1.42, "result": "ran" | "off" | "unreadable" | "not-started",
   "exit": 0}`. A line that can't be written is said on stderr and changes no exit code.
5. The gate exits with the command's exit code, or 128 + the signal number that ended it.

`--home PATH`, in every form, is the Moxi home the unit was written from: the gate sets
`MOXI_HOME` to it before it reads anything, so the switch, the run line and the command all
follow that home. A tagged home's Task Scheduler task carries it, since a task has no
environment of its own, and so does a tagged home's wrap drop-in, so the foreign service's
own command is not given the variable. The path must be absolute.

The Windows backend adds four flags; they work the same everywhere, and only the Job
Object is Windows's own:

- `--log PATH` pipes the command's stdout and stderr into `PATH`, which rolls to
  `PATH.1` past 5 MiB (Task Scheduler keeps no log).
- `--restart` starts the command again each time it exits non-zero, after 2 s, doubling
  to 60 s, and back to 2 s once a run has lasted ten minutes. Each exit writes its own
  run line before the wait; a command that cannot start counts as an exit 127. An exit
  0, or the gate being told to stop, ends the loop.
- `--runtime` keeps `RUNTIME/moxi/units/<module>-<name>.json`, `{"pid", "started"}` (the
  command's `moxi.proc.ProcId`), while the command runs, and only then: the status read
  takes a task with no live runtime file and a non-zero newest run line as failed.
- `--stop process` says the daemon was declared `stop = "process"`. On Windows the command
  runs in a Job Object that ends everything in it when the gate ends, so ending the task
  ends the daemon; `--stop process` lets a child the daemon starts with
  `moxi.proc.spawn(detached=True)` leave that job. The default, `group`, keeps every
  child in it.

The two wrap forms sit in a wrap's drop-in on a foreign systemd timer's service and run no
command: systemd runs the service's own `ExecStart` between them.

- `--condition` is the drop-in's `ExecCondition=`. It removes a start stamp a run whose
  `--result` never ran left behind, then reads `--if` as step 2 does. Off: one `off` run
  line and exit 1, which systemd takes as a skip, not a failure. On: it writes the start
  stamp `RUNTIME/moxi/units/<module>-<name>.start`, `{"at", "mono"}`, writes no run line
  and exits 0. Anything else exits 255, since an exit from 1 to 254 would read as a skip
  and hide the fault: a bad argument list (no run line), an unreadable config (an
  `unreadable` line), a stamp it can't write or a stale one it can't remove (a
  `not-started` line).
- `--result` is the drop-in's `ExecStopPost=`, which systemd runs after every run of the
  service, a skipped one too. With no stamp it writes nothing, so a run `--condition`
  turned away is counted once, by `--condition`. With a stamp it removes the stamp and
  writes one run line from systemd's variables: `$EXIT_CODE` `exited` is `ran` with
  `$EXIT_STATUS` as the exit, `killed` or `dumped` is `ran` with 128 + the signal
  `$EXIT_STATUS` names, and unset is `not-started` with 127. `$SERVICE_RESULT`
  `exec-condition` (a condition after the gate's skipped the run) writes nothing. Its line
  also carries `service_result`, the raw `$SERVICE_RESULT`. It always exits 0, a bad
  argument list included, so it never changes the run's own result.

A run line's `exit` is what the first form would exit with for that outcome (0 for `off`,
3 for `unreadable`, 127 for `not-started`), in the wrap forms too, so a reader of
`runs.jsonl` reads every line one way.

`runs.jsonl` (with its `.1`) and `logs/` in a module's state directory are the core's
stores, as `doctor.json` is: no module declares them in `STORES`.

The gate imports only `tomllib`, `moxi.paths`, `moxi.files` and `moxi.proc` (and psutil
inside `moxi.proc.identity` for `--runtime`), so a timer firing costs one interpreter
start.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from . import files, names, paths

#: The run record and the log directory in a module's state directory. Both are the
#: core's stores, as `moxi.doctor`'s `doctor.json` is.
RUNS_FILE = "runs.jsonl"
LOGS_DIR = "logs"
#: The run record's cap, past which it rolls to `runs.jsonl.1`.
RUNS_CAP = 1_000_000
#: The `--log` file's cap, past which it rolls to `<log>.1`.
LOG_CAP = 5 * 1024 * 1024
#: `--restart`'s waits: the first, the longest, and how long a run must last for the next
#: wait to start again from the first.
RESTART_FIRST = 2.0
RESTART_MAX = 60.0
RESTART_RESET = 600.0

#: The four results a run line holds.
RAN = "ran"
OFF = "off"
UNREADABLE = "unreadable"
NOT_STARTED = "not-started"

#: The first form's exit codes for a bad argument list, an unreadable switch and a
#: command that cannot start.
USAGE_EXIT = 2
UNREADABLE_EXIT = 3
NOT_STARTED_EXIT = 127
#: `--condition`'s exits: a skip, and a fault systemd must not read as a skip.
CONDITION_SKIP = 1
CONDITION_FAULT = 255

USAGE = """\
usage: moxi run [--home PATH] [--if MODULE.KEY] --record MODULE/NAME [--log PATH]
                [--restart] [--runtime] [--stop group|process] -- ARGV...
       moxi run [--home PATH] --if MODULE.KEY --record MODULE/NAME --condition
       moxi run [--home PATH] --record MODULE/NAME --result
"""

_UNIT_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_KEY_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")
_WITH_VALUE = ("--home", "--if", "--record", "--log", "--stop")
_FLAGS = ("--restart", "--runtime", "--condition", "--result")
_STOPS = ("group", "process")
_WINDOWS = sys.platform == "win32"


class UsageError(Exception):
    """A bad argument list."""


class Args:
    """A parsed argument list."""

    __slots__ = ("switch", "module", "name", "log", "restart", "runtime", "stop",
                 "condition", "result", "argv", "home")

    def __init__(self, *, switch: tuple[str, str] | None, module: str, name: str,
                 log: str | None = None, restart: bool = False, runtime: bool = False,
                 stop: str = "group", condition: bool = False, result: bool = False,
                 argv: tuple[str, ...] = (), home: str | None = None):
        self.switch = switch
        self.module = module
        self.name = name
        self.log = log
        self.restart = restart
        self.runtime = runtime
        self.stop = stop
        self.condition = condition
        self.result = result
        self.argv = argv
        self.home = home

    @property
    def unit(self) -> str:
        return f"{self.module}/{self.name}"


def parse(argv: list[str]) -> Args:
    """The argument list, checked whole. Raises `UsageError` naming what is wrong.

    Options come before `--` and each is given at most once; a value is the next word,
    never joined with `=`.
    """
    if "--" in argv:
        cut = argv.index("--")
        options, command = argv[:cut], tuple(argv[cut + 1:])
        dashes = True
    else:
        options, command, dashes = argv, (), False
    seen: dict[str, str | bool] = {}
    words = iter(options)
    for word in words:
        if word in seen:
            raise UsageError(f"{word} is given twice")
        if word in _WITH_VALUE:
            value = next(words, None)
            if value is None or value.startswith("--"):
                raise UsageError(f"{word} needs a value")
            seen[word] = value
        elif word in _FLAGS:
            seen[word] = True
        else:
            raise UsageError(f"unknown argument {word!r}")
    if "--record" not in seen:
        raise UsageError("--record MODULE/NAME is required")
    module, name = _record(str(seen["--record"]))
    home = seen.get("--home")
    if home is not None and not os.path.isabs(str(home)):
        raise UsageError(f"--home {home!r} is not an absolute path")
    home = None if home is None else str(home)
    switch = _switch_name(str(seen["--if"]), module) if "--if" in seen else None
    condition, result = "--condition" in seen, "--result" in seen
    if condition or result:
        form = "--condition" if condition else "--result"
        extra = [w for w in seen if w not in ("--home", "--record", "--if", form)]
        if result and "--if" in seen:
            extra.insert(0, "--if")
        if extra:
            raise UsageError(f"{form} takes no {', '.join(extra)}")
        if condition and switch is None:
            raise UsageError("--condition needs --if MODULE.KEY")
        if dashes:
            raise UsageError(f"{form} runs no command; drop the -- and what follows it")
        return Args(switch=switch, module=module, name=name, condition=condition,
                    result=result, home=home)
    if not dashes or not command:
        raise UsageError("the command to run follows --")
    log = seen.get("--log")
    if log is not None and not os.path.isabs(str(log)):
        raise UsageError(f"--log {log!r} is not an absolute path")
    stop = str(seen.get("--stop", "group"))
    if stop not in _STOPS:
        raise UsageError(f"--stop {stop!r} is neither group nor process")
    return Args(switch=switch, module=module, name=name,
                log=None if log is None else str(log), restart="--restart" in seen,
                runtime="--runtime" in seen, stop=stop, argv=command, home=home)


def _record(value: str) -> tuple[str, str]:
    module, slash, name = value.partition("/")
    if not slash or not names.is_module_name(module) or not _UNIT_NAME.fullmatch(name):
        raise UsageError(f"--record {value!r} is not MODULE/NAME (a module name, a slash, "
                         "and a unit name of lower-case letters and digits in words "
                         "joined by single hyphens)")
    return module, name


def _switch_name(value: str, module: str) -> tuple[str, str]:
    owner, dot, key = value.partition(".")
    if (not dot or not names.is_module_name(owner)
            or not all(_KEY_SEGMENT.fullmatch(s) for s in key.split("."))):
        raise UsageError(f"--if {value!r} is not MODULE.KEY (a module name, a dot, and a "
                         "dotted key)")
    if owner != module:
        raise UsageError(f"--if names module {owner!r} but --record names {module!r}; "
                         "a unit's switch is its own module's")
    return owner, key


# -- the switch --------------------------------------------------------------------------

def read_switch(module: str, key: str, *,
                env: Mapping[str, str] | None = None) -> tuple[bool | None, str]:
    """The switch `<module>.<key>` as the gate reads it: `(True|False, "")`, or
    `(None, why)` when it is unreadable.

    The module's config file is read with `tomllib` and the dotted key looked up. A
    missing file, a missing table on the way and a missing last key are all off.
    """
    import tomllib

    path = paths._layout(module).config_file(env)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        return False, ""
    except tomllib.TOMLDecodeError as exc:
        return None, f"{path} is not valid TOML ({exc})"
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{path} cannot be read ({exc})"
    *tables, last = key.split(".")
    where: dict = data
    for i, segment in enumerate(tables):
        value = where.get(segment)
        if value is None:
            return False, ""
        if not isinstance(value, dict):
            dotted = ".".join(tables[:i + 1])
            return None, f"{path}: {dotted} is {type(value).__name__}, not a table"
        where = value
    value = where.get(last)
    if value is None:
        return False, ""
    if not isinstance(value, bool):
        return None, f"{path}: {key} = {value!r} is not true or false"
    return value, ""


# -- the run line ------------------------------------------------------------------------

def runs_path(module: str, env: Mapping[str, str] | None = None) -> Path:
    """`STATE/<module>/runs.jsonl`, the module's run record."""
    return paths._layout(module).state_dir(env) / RUNS_FILE


def log_path(module: str, name: str, env: Mapping[str, str] | None = None) -> Path:
    """`STATE/<module>/logs/<name>.log`, the Windows backend's `--log` path for a unit."""
    return paths._layout(module).state_dir(env) / LOGS_DIR / f"{name}.log"


def runtime_path(module: str, name: str, env: Mapping[str, str] | None = None) -> Path:
    """`RUNTIME/moxi/units/<module>-<name>.json`, what `--runtime` keeps while the command
    runs."""
    return paths.core().runtime_dir(env) / "units" / f"{module}-{name}.json"


def stamp_path(module: str, name: str, env: Mapping[str, str] | None = None) -> Path:
    """`RUNTIME/moxi/units/<module>-<name>.start`, `--condition`'s start stamp."""
    return paths.core().runtime_dir(env) / "units" / f"{module}-{name}.start"


def _utc(wall: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(wall))


def _line(args: Args, *, at: float | str, seconds: float, result: str, code: int,
          **more) -> None:
    """Append one run line; a line that can't be written is said on stderr."""
    record = {"unit": args.unit, "at": at if isinstance(at, str) else _utc(at),
              "seconds": round(max(seconds, 0.0), 3), "result": result, "exit": code,
              **more}
    path = runs_path(args.module)
    try:
        files.append_record(path, record, max_bytes=RUNS_CAP)
    except (OSError, ValueError) as exc:
        _say(f"cannot write the run line to {path}: {exc}")


def _say(text: str) -> None:
    try:
        print(f"moxi run: {text}", file=sys.stderr, flush=True)
    except (OSError, ValueError, AttributeError):  # no stderr (pythonw), or a closed one
        pass


# -- main --------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """`moxi run`. Returns the exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (["-h"], ["--help"]):
        sys.stdout.write(USAGE)
        return 0
    try:
        args = parse(argv)
    except UsageError as exc:
        _say(str(exc))
        try:
            sys.stderr.write(USAGE)
        except (OSError, ValueError, AttributeError):
            pass
        return _usage_exit(argv)
    if args.home is not None:
        # Before anything reads a root: the switch, the run line and the command follow it.
        os.environ[paths.HOME_ENV] = args.home
    if args.condition:
        return condition(args)
    if args.result:
        return result(args)
    return gate(args)


def _usage_exit(argv: list[str]) -> int:
    """A bad argument list's exit: 255 when it asked for `--condition` (any exit from 1 to
    254 would read as a skip), 0 when it asked for `--result` (which never changes the
    run's result), else 2."""
    options = argv[:argv.index("--")] if "--" in argv else argv
    if "--condition" in options:
        return CONDITION_FAULT
    if "--result" in options:
        return 0
    return USAGE_EXIT


def gate(args: Args) -> int:
    """The first form: the switch, then the command (again and again with `--restart`)."""
    start, mono = time.time(), time.monotonic()
    if args.switch is not None:
        on, why = read_switch(*args.switch)
        if on is None:
            _say(why)
            _line(args, at=start, seconds=time.monotonic() - mono, result=UNREADABLE,
                  code=UNREADABLE_EXIT)
            return UNREADABLE_EXIT
        if not on:
            _line(args, at=start, seconds=time.monotonic() - mono, result=OFF, code=0)
            return 0
    stopper = _Stopper()
    with stopper:
        if not args.restart:
            return _once(args, stopper)[0]
        wait = RESTART_FIRST
        while True:
            code, lasted = _once(args, stopper)
            if code == 0 or stopper.signum is not None:
                return code
            if lasted >= RESTART_RESET:
                wait = RESTART_FIRST
            if stopper.sleep(wait):
                return code
            wait = min(wait * 2, RESTART_MAX)


def _once(args: Args, stopper: _Stopper) -> tuple[int, float]:
    """Run the command once and write its run line. Returns its exit code and how long it
    ran."""
    import subprocess

    start, mono = time.time(), time.monotonic()
    log = _Log(Path(args.log)) if args.log else None
    options: dict = {}
    if log is not None:
        options.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if _WINDOWS:
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        child = subprocess.Popen(list(args.argv), **options)
    except (OSError, ValueError) as exc:
        why = f"cannot start {args.argv[0]}: {exc}"
        _say(why)
        if log is not None:  # on Windows the log is the one place anyone reads it
            log.write(f"moxi run: {why}\n".encode("utf-8", "replace"))
            log.close()
        _line(args, at=start, seconds=time.monotonic() - mono, result=NOT_STARTED,
              code=NOT_STARTED_EXIT)
        return NOT_STARTED_EXIT, time.monotonic() - mono
    job = _job(child, args.stop) if _WINDOWS else None
    stopper.hold(child)
    reader = log.follow(child.stdout) if log is not None else None
    runtime = runtime_path(args.module, args.name) if args.runtime else None
    try:
        if runtime is not None:
            _write_runtime(runtime, child.pid)
        code = _exit_code(child.wait())
    finally:
        stopper.child = None
        if runtime is not None:
            _remove(runtime)
        if reader is not None:
            reader.join(timeout=2.0)  # a grandchild may still hold the pipe; leave it
        if job is not None:
            job.close()
    lasted = time.monotonic() - mono
    _line(args, at=start, seconds=lasted, result=RAN, code=code)
    return code, lasted


def _exit_code(returncode: int) -> int:
    """A child's exit as the gate exits with it: 128 + the signal number for a signal."""
    return 128 - returncode if returncode < 0 else returncode


def _job(child, stop: str):
    """Put the command in a kill-on-close Job Object (Windows), so ending the gate ends
    it and everything it started; `stop = "process"` lets a detached child break away.
    A job that can't be made is said on stderr and the command runs without one."""
    from . import _win

    try:
        job = _win.Job(kill_on_close=True, breakaway=stop == "process")
    except OSError as exc:
        _say(f"cannot make a job for the command: {exc}")
        return None
    try:
        job.assign(child.pid)
    except OSError as exc:
        _say(f"cannot put the command in its job: {exc}")
        job.close()
        return None
    return job


def _write_runtime(path: Path, pid: int) -> None:
    from . import proc

    ident = proc.identity(pid)
    if ident is None:  # it has ended already
        return
    try:
        paths.core().runtime_dir(create=True)
        files.atomic_write(path, json.dumps({"pid": ident.pid, "started": ident.started}))
    except OSError as exc:
        _say(f"cannot write {path}: {exc}")


def _remove(path: Path) -> bool:
    """Remove `path`; True when it is gone (or never was)."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        _say(f"cannot remove {path}: {exc}")
        return False
    return True


class _Stopper:
    """While the command runs, SIGTERM and SIGHUP are passed on to it and SIGINT (which
    a terminal sends the whole group) is left to reach it alone; any of the three also
    ends a `--restart` loop. POSIX only: on Windows ending the task ends the gate and its
    job with it. A signal ignored when the gate started stays ignored."""

    _SIGNALS = ("SIGTERM", "SIGHUP", "SIGINT")

    def __init__(self):
        self.signum: int | None = None
        self.child = None
        self._old: dict = {}

    def __enter__(self) -> _Stopper:
        if _WINDOWS:
            return self
        import signal

        for name in self._SIGNALS:
            signum = getattr(signal, name)
            old = signal.getsignal(signum)
            if old is signal.SIG_IGN:
                continue
            self._old[signum] = old
            signal.signal(signum, self._on_signal)
        return self

    def __exit__(self, *exc) -> None:
        import signal

        for signum, old in self._old.items():
            signal.signal(signum, old)

    def hold(self, child) -> None:
        """Pass stop signals on to `child` from now on, one that came while it started
        too."""
        self.child = child
        if self.signum is not None:
            self._forward()

    def _on_signal(self, signum, frame) -> None:
        self.signum = signum
        self._forward()

    def _forward(self) -> None:
        import signal

        signum, child = self.signum, self.child
        if signum != signal.SIGINT and child is not None and child.poll() is None:
            try:
                os.kill(child.pid, signum)
            except OSError:
                pass

    def sleep(self, seconds: float) -> bool:
        """Wait `seconds`, or less if a stop signal comes. True when one came."""
        end = time.monotonic() + seconds
        while self.signum is None:
            left = end - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(left, 0.2))
        return True


class _Log:
    """`--log`: the command's output appended to a file that rolls to `<file>.1` past
    `LOG_CAP` bytes."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def _open(self) -> int:
        if self._fd is None:
            files._make_dirs(self.path.parent)
            self._fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                               | getattr(os, "O_BINARY", 0), 0o600)
        return self._fd

    def write(self, chunk: bytes) -> None:
        """Append `chunk`, rolling the file each time it reaches `LOG_CAP`, so neither
        file ever holds more."""
        view = memoryview(chunk)
        try:
            while view:
                fd = self._open()
                size = os.fstat(fd).st_size
                if size >= LOG_CAP:
                    self.close()
                    try:
                        os.replace(self.path, self.path.with_name(self.path.name + ".1"))
                    except OSError as exc:
                        _say(f"cannot roll {self.path}: {exc}")
                        return
                    continue
                part = view[:LOG_CAP - size]
                while part:
                    done = os.write(fd, part)
                    part, view = part[done:], view[done:]
        except OSError as exc:
            _say(f"cannot write the log {self.path}: {exc}")

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def follow(self, pipe):
        """Copy `pipe` into the log on a thread until the pipe closes. Returns the
        thread."""
        import threading

        def copy() -> None:
            try:
                while chunk := pipe.read1(65536):
                    self.write(chunk)
            except (OSError, ValueError):
                pass
            finally:
                self.close()
                try:
                    pipe.close()
                except OSError:
                    pass

        thread = threading.Thread(target=copy, name="moxi-run-log", daemon=True)
        thread.start()
        return thread


# -- the wrap forms ----------------------------------------------------------------------

def condition(args: Args) -> int:
    """`--condition`, a wrap's `ExecCondition=`: 0 runs the service, 1 skips it, 255
    fails it."""
    start, mono = time.time(), time.monotonic()
    stamp = stamp_path(args.module, args.name)
    if not _remove(stamp):
        _line(args, at=start, seconds=time.monotonic() - mono, result=NOT_STARTED,
              code=NOT_STARTED_EXIT)
        return CONDITION_FAULT
    on, why = read_switch(*args.switch)
    if on is None:
        _say(why)
        _line(args, at=start, seconds=time.monotonic() - mono, result=UNREADABLE,
              code=UNREADABLE_EXIT)
        return CONDITION_FAULT
    if not on:
        _line(args, at=start, seconds=time.monotonic() - mono, result=OFF, code=0)
        return CONDITION_SKIP
    try:
        paths.core().runtime_dir(create=True)
        files.atomic_write(stamp, json.dumps({"at": _utc(start), "mono": mono}))
    except OSError as exc:
        _say(f"cannot write the start stamp {stamp}: {exc}")
        _line(args, at=start, seconds=time.monotonic() - mono, result=NOT_STARTED,
              code=NOT_STARTED_EXIT)
        return CONDITION_FAULT
    return 0


def result(args: Args, env: Mapping[str, str] | None = None) -> int:
    """`--result`, a wrap's `ExecStopPost=`: the run line from systemd's variables when
    `--condition` left a stamp. Always 0."""
    environ = os.environ if env is None else env
    stamp = stamp_path(args.module, args.name)
    try:
        text = stamp.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    except (OSError, UnicodeDecodeError) as exc:
        _say(f"cannot read the start stamp {stamp}: {exc}")
        _remove(stamp)
        return 0
    _remove(stamp)
    try:
        started = json.loads(text)
        at, mono = started["at"], float(started["mono"])
        if not isinstance(at, str):
            raise TypeError("at is not a string")
    except (ValueError, TypeError, KeyError) as exc:
        _say(f"the start stamp {stamp} is not one --condition wrote ({exc}); "
             "no run line")
        return 0
    service_result = environ.get("SERVICE_RESULT")
    if service_result == "exec-condition":
        return 0
    how = environ.get("EXIT_CODE") or None
    if how is None:
        outcome, code = NOT_STARTED, NOT_STARTED_EXIT
    else:
        outcome, code = RAN, _systemd_exit(how, environ.get("EXIT_STATUS", ""))
    _line(args, at=at, seconds=time.monotonic() - mono, result=outcome, code=code,
          service_result=service_result)
    return 0


def _systemd_exit(how: str, status: str) -> int:
    """An exit code from systemd's `$EXIT_CODE` and `$EXIT_STATUS`: the status itself for
    `exited`, 128 + the signal's number for `killed` and `dumped` (the status names the
    signal, `TERM`, or gives its number). A status that can't be read is 1."""
    status = status.strip()
    if how == "exited":
        return int(status) if status.isdigit() else 1
    if status.isdigit():
        return 128 + int(status)
    import signal

    try:
        return 128 + int(signal.Signals[f"SIG{status.upper().removeprefix('SIG')}"])
    except KeyError:
        return 1


__all__ = ["main", "parse", "Args", "UsageError", "read_switch", "gate", "condition",
           "result", "runs_path", "log_path", "runtime_path", "stamp_path", "RUNS_FILE",
           "LOGS_DIR", "RUNS_CAP", "LOG_CAP", "RAN", "OFF", "UNREADABLE", "NOT_STARTED"]
