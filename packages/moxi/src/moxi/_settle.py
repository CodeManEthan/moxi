"""The settle step and the teardown, each in a child process of `moxi install` or
`moxi uninstall`, and how the parent starts and waits for that child.

    python -m moxi._settle MODULE [--replace]      the settle step of one module
    python -m moxi._settle --teardown MODULE       a module's TEARDOWN

The parent holds the core lock for the whole install (`moxi.files.lock(core_lock())`)
and hands its claim to the one child it starts: the lock's descriptor is inherited by
that child alone (`pass_fds`; on Windows the handle, named in the `Popen`'s handle list)
and its number is in `MOXI_CORE_LOCK_FD`, set only in the child's environment. The
child's first act, before it imports the module or runs any step, is
`moxi.files.adopt_core_claim`: the claim is then this process's for its whole life, so
every core call it makes under the lock yields, and nothing it spawns inherits the claim.
A child given the variable whose claim does not check prints one line and exits 1 before
any step. Run by hand, with no variable, it holds no claim and each core call takes the
lock itself.

On POSIX a parent killed outright leaves the child running with the lock's descriptor,
so the lock stays held until the child exits; on Windows the lock is freed when the
parent dies.

The settle step, in order: the home move (`moxi.home.move`) into the step's own stop
record; the module's `SETUP`; then the units: `plan`, each timer switch with its
`units.json` entry, `apply`. Those three run in a `try` whose `finally` starts again
exactly the units the move stopped (`moxi.units.resume`), on every path: a refusal, a
failed stop or rename, a failed `SETUP`, a plan that refuses or keeps the unit, an
interrupt. A start the supervisor refuses is printed with the command that starts it by
hand, left as a doctor record, and the install exits 1. Skills, install questions and the
`moxi.installs` listeners come after the `finally` (`_later_steps`).

Signals. The child takes SIGINT, SIGTERM, SIGHUP and SIGQUIT (on Windows SIGINT for
Ctrl-C, and SIGBREAK, which Python raises for Ctrl-Break and for a console close) as one
request to stop. A signal that was ignored when the process started stays ignored, so a
`nohup` install survives its terminal. The handlers are Python handlers, never `SIG_IGN`,
so a program the child starts gets default dispositions. In the steps, a signal raises
`Interrupted` at once, except inside the core's own writes (the move, a timer switch with
its entry, `apply`), which are marked `held()`: there it only sets the request, and the
write finishes before the request is obeyed. In the `finally` it only sets the request,
so `resume` and its reports always run. Then the child prints how many units it started
again and exits 130, the pair's private "interrupted" code.

The parent never kills the child. It waits with `Popen.wait()` and no timeout, under
handlers that record each signal and, on POSIX, forward it to the child; on Windows the
console delivers Ctrl-C, Ctrl-Break and a close to the child itself. When the child
exits interrupted, the parent runs nothing more and exits 1.

What stays the residual: SIGKILL, power loss, and on Windows a console close, after which
Windows ends the process within about five seconds of the handler returning, whatever
Python does. A child ended so runs no `finally`: what the move stopped stays stopped until
it is started by hand.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field

_WINDOWS = sys.platform == "win32"

#: The child's exit code for "interrupted", private to the parent and child.
INTERRUPTED = 130
#: The file in the core's state directory that holds the version each module's `SETUP`
#: last ran at: `{"<module>": {"version": <str or null>, "at": <UTC>}}`.
SETUP_FILE = "setup.json"


class Interrupted(BaseException):
    """Raised in the child's steps when a stop request arrives outside a held span. It is
    a `BaseException`, so code that catches `Exception` doesn't swallow it."""

    def __init__(self, signum: int | None = None):
        super().__init__(signum)
        self.signum = signum


# -- one writer for every line -----------------------------------------------------------------

class _Guarded:
    """A text stream that, once a write fails with `OSError` (the terminal gone after an
    SSH drop), drops that write and every later one instead of raising."""

    def __init__(self, inner):
        self._inner = inner

    def write(self, text: str) -> int:
        if not _lines.gone:
            try:
                return self._inner.write(text)
            except OSError:
                _lines.gone = True
        return len(text)

    def flush(self) -> None:
        if not _lines.gone:
            try:
                self._inner.flush()
            except OSError:
                _lines.gone = True

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _Lines:
    gone = False


_lines = _Lines()


def say(line: str, *, err: bool = False) -> None:
    """Print one line to stdout (or stderr with `err=True`). After a write fails with
    `OSError`, this line and every later one are dropped, never raised."""
    if _lines.gone:
        return
    stream = sys.stderr if err else sys.stdout
    if stream is None:
        return
    try:
        stream.write(f"{line}\n")
        stream.flush()
    except OSError:
        _lines.gone = True


def guard_streams() -> None:
    """Make `sys.stdout` and `sys.stderr` drop writes once the terminal is gone, so a
    print anywhere in the process can't raise after a hang-up."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is not None and not isinstance(stream, _Guarded):
            setattr(sys, name, _Guarded(stream))


# -- the child's signals -----------------------------------------------------------------------

STEPS = "steps"
RESTART = "restart"


class _Signals:
    """The child's stop request and phase. `raising` is the steps flag: the handler
    raises only while it is set, and clears it when it does; the body clears it as its
    last act, so a signal at the end of the `try` can't skip the restart."""

    def __init__(self):
        self.request: int | None = None
        self.phase = STEPS
        self.raising = False
        self.depth = 0


_state = _Signals()


def signals() -> tuple[int, ...]:
    """The signals the child and the parent handle on this system."""
    if _WINDOWS:
        return (signal.SIGINT, signal.SIGBREAK)
    return (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)


def _on_signal(signum, frame) -> None:
    if _state.request is None:
        _state.request = signum
    if _state.phase == STEPS and _state.raising and _state.depth == 0:
        _state.raising = False
        raise Interrupted(signum)


def install_handlers() -> None:
    """The child's handlers for `signals()`. One the process started with ignored is left
    ignored."""
    for signum in signals():
        if signal.getsignal(signum) == signal.SIG_IGN:
            continue
        signal.signal(signum, _on_signal)


@contextmanager
def held():
    """A span of the core's own writes: a signal inside only sets the stop request, and
    the request is obeyed when the outermost span ends."""
    _state.depth += 1
    try:
        yield
    finally:
        _state.depth -= 1
    if _state.depth == 0:
        check()


def check() -> None:
    """Raise `Interrupted` when a stop request is set (in the steps phase): after a held
    span, and when `SETUP` or a listener returns, so code that caught `Interrupted`
    still stops there."""
    if _state.request is not None and _state.phase == STEPS:
        _state.raising = False
        raise Interrupted(_state.request)


def interrupted() -> bool:
    """Whether a stop request is set."""
    return _state.request is not None


# -- the parent ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    """How a child ended: its exit code, and the signals the parent caught meanwhile."""

    code: int
    signals: tuple[int, ...] = field(default=())

    @property
    def interrupted(self) -> bool:
        return bool(self.signals) or self.code == INTERRUPTED


def child_argv(python: str, module: str, *, replace: bool = False,
               teardown: bool = False) -> list[str]:
    """The settle child's command line (or the teardown child's)."""
    argv = [str(python), "-m", "moxi._settle"]
    if teardown:
        return [*argv, "--teardown", module]
    return [*argv, module] + (["--replace"] if replace else [])


def run_child(argv: list[str], *, env: dict[str, str] | None = None) -> Outcome:
    """Start a settle or teardown child with this process's claim on the core lock, and
    wait for it to exit, however long it takes.

    The caller holds the core lock (`moxi.files.lock(core_lock())`). While the child
    runs, this process's handlers for `signals()` record each signal and, on POSIX,
    forward it to the child; they never raise, so the wait is never cut short, and the
    child is never killed from here. A signal ignored here stays ignored. The handlers
    it replaced are put back when the child has exited.
    """
    from . import files, paths

    fd = files._descriptor(paths.core_lock())
    environ = dict(os.environ if env is None else env)
    seen: list[int] = []
    box: list[subprocess.Popen] = []

    def forward(signum, frame) -> None:
        seen.append(signum)
        if box and not _WINDOWS:
            try:
                box[0].send_signal(signum)
            except OSError:
                pass

    replaced = {}
    for signum in signals():
        current = signal.getsignal(signum)
        if current == signal.SIG_IGN:
            continue
        replaced[signum] = current
        signal.signal(signum, forward)
    try:
        try:
            proc = _start(argv, environ, fd)
        except OSError as exc:
            say(f"moxi: cannot run {argv[0]}: {exc}", err=True)
            return Outcome(127, tuple(seen))
        box.append(proc)
        if seen and not _WINDOWS:
            # A signal that came before the child existed: it is told now.
            try:
                proc.send_signal(seen[0])
            except OSError:
                pass
        code = proc.wait()
    finally:
        for signum, handler in replaced.items():
            signal.signal(signum, handler if handler is not None else signal.SIG_DFL)
    return Outcome(code, tuple(seen))


def _start(argv: list[str], environ: dict[str, str], fd: int) -> subprocess.Popen:
    from .files import CLAIM_VARIABLE

    if not _WINDOWS:
        environ[CLAIM_VARIABLE] = str(fd)
        return subprocess.Popen(argv, env=environ, pass_fds=(fd,))
    import msvcrt

    handle = msvcrt.get_osfhandle(fd)
    os.set_handle_inheritable(handle, True)
    try:
        info = subprocess.STARTUPINFO()
        info.lpAttributeList = {"handle_list": [handle]}
        environ[CLAIM_VARIABLE] = str(handle)
        return subprocess.Popen(argv, env=environ, startupinfo=info, close_fds=True)
    finally:
        os.set_handle_inheritable(handle, False)


# -- setup.json ------------------------------------------------------------------------------------

def setup_path():
    from . import paths

    return paths.core().state_dir() / SETUP_FILE


def setup_versions() -> dict[str, dict]:
    """Every module's entry in `setup.json`; empty when the file is missing or unreadable."""
    import json

    try:
        data = json.loads(setup_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def setup_version(module: str) -> str | None:
    """The version `module`'s `SETUP` last ran at, or None when none is recorded."""
    entry = setup_versions().get(module)
    version = entry.get("version") if isinstance(entry, dict) else None
    return version if isinstance(version, str) else None


def record_setup(module: str, version: str | None) -> None:
    """Record that `module`'s `SETUP` ran (or that it declares none) at `version`."""
    import time

    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _change_setup(lambda data: data.__setitem__(module, {"version": version, "at": at}))


def forget_setup(module: str) -> None:
    """Drop `module`'s entry (uninstall)."""
    if setup_path().exists():
        _change_setup(lambda data: data.pop(module, None))


def _change_setup(fn) -> None:
    import json

    from . import files

    def edit(old: bytes | None) -> bytes:
        try:
            data = json.loads(old) if old else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        fn(data)
        return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")

    files.update(setup_path(), edit)


# -- the settle step ---------------------------------------------------------------------------

@dataclass
class Settle:
    """One module's settle step: what the steps share."""

    module: str
    found: object
    replace: bool = False
    #: The version `SETUP` last ran at, read before anything writes it.
    before: str | None = None
    #: The stop record the move fills and the `finally` starts again.
    record: list = field(default_factory=list)
    failed: bool = False

    @property
    def restart(self) -> bool:
        """Whether the installed version differs from the one `SETUP` last ran at."""
        return self.found.version != self.before


def settle(module: str, *, replace: bool = False) -> int:
    """Run `module`'s settle step in this process; returns the exit code: 0, 1 on a
    failure, `INTERRUPTED` on a stop request."""
    from . import modules

    found = modules.load(module)
    if found.state != modules.PRESENT:
        say(f"{module}: not set up, the module is {found.state}"
            + (f": {found.reason}" if found.reason else ""), err=True)
        return 1
    step = Settle(module, found, replace, setup_version(module))
    try:
        try:
            try:
                step.failed = not _steps(step)
            except Interrupted:
                step.failed = True
            except Exception as exc:
                _state.raising = False
                step.failed = True
                say(f"{module}: the settle step failed: {_one_line(exc)}", err=True)
        except Interrupted:  # a signal in an except clause above
            step.failed = True
    finally:
        # A plain store first: the handler is called only between bytecodes that check
        # for it, and none does before this one, so nothing can raise from here on.
        _state.raising = False
        _state.phase = RESTART
        started, problems = _restart(step)
        _state.phase = STEPS
    if _state.request is not None:
        say(f"{module}: interrupted; started again {started} of the {len(step.record)} "
            f"units the move stopped", err=True)
        return INTERRUPTED
    if step.failed or problems:
        return 1
    code = _later_steps(step)
    if code == 0:
        _clear_resume_records(module)
    return code


def _steps(step: Settle) -> bool:
    """Steps 1 to 3; True when each went through. Sets the steps flag first and clears
    it as its last act."""
    _state.raising = True
    ok = _move(step) and _setup(step) and _units(step)
    _state.raising = False
    _after_steps()
    return ok


def _after_steps() -> None:
    """Nothing; a test delivers a signal here, between the steps and the `finally`."""


def _move(step: Settle) -> bool:
    """Step 1: the home move."""
    from . import home

    try:
        with held():
            home.move(step.module, step.record, replace=step.replace, say=say)
    except home.MoveError as exc:
        say(exc.line, err=True)
        return False
    return True


def _setup(step: Settle) -> bool:
    """Step 2: `SETUP`, imported and called, then `setup.json`."""
    module, target = step.module, step.found.setup
    if target:
        say(f"setup {module}: running {target}")
        try:
            result = _call(target)
        except Interrupted:
            say(f"setup {module}: interrupted", err=True)
            raise
        except SystemExit as exc:
            result = exc.code
        except Exception as exc:
            check()
            say(f"setup {module}: failed ({_one_line(exc)}). The install stays; run "
                f"`moxi install {module}` again once the cause is fixed.", err=True)
            return False
        check()
        code = _exit_code(result)
        if code != 0:
            if isinstance(result, str):
                say(result, err=True)
            say(f"setup {module}: failed (exit {code}). The install stays; run "
                f"`moxi install {module}` again once the cause is fixed.", err=True)
            return False
        say(f"setup {module}: done")
    with held():
        record_setup(module, step.found.version)
    return True


def _units(step: Settle) -> bool:
    """Step 3: plan, the timer switches with their `units.json` entries, apply, and the
    record's entries for units the unit step removed or disabled taken out."""
    from . import units
    from .units import _backend

    module, found = step.module, step.found
    try:
        _backend.implementation()
    except NotImplementedError as exc:
        if not (found.daemons or found.timers or found.units_from):
            return True  # no writer here, so no unit of moxi's can be here either
        say(f"{module}: units not written: {exc}", err=True)
        return False
    try:
        changes = units.plan(module)
    except ValueError as exc:
        say(f"{module}: units not planned: {exc}", err=True)
        return False
    ok = _switches(step, changes)
    with held():
        applied = units.apply(changes, replace=step.replace, restart=step.restart, say=say)
        _take_out(step.record, applied)
    return ok and all(a.ok for a in applied)


def _switches(step: Settle, changes) -> bool:
    """Each switched timer `plan` will write or keep: a new one is asked about (off with
    nobody at the keyboard), a changed one is set off, an unchanged one is left. The
    switch is written first and its `units.json` entry second."""
    from . import config
    from .units import _state as recorded

    module, ok = step.module, True
    for change in changes:
        if (change.kind != "timer" or change.switch is None or change.digest is None
                or change.action not in ("add", "change", "keep")):
            continue
        before = recorded.recorded(module, change.name)
        if before == change.digest:
            continue
        switch = change.switch
        turn_on = f"moxi config set {module} {switch} true"
        on = False
        if before is None:
            on = _ask(f"{change.unit}: {_describe(change)}. Turn it on? [y/N] ")
            line = None if on else f"{change.unit} is off; turn it on with: {turn_on}"
        else:
            line = (f"{change.unit} changed (its schedule or command), so it is off "
                    f"again: {turn_on}")
        try:
            with held():
                config.set(module, switch, on, by="moxi install")
                recorded.record(module, change.name, change.digest)
        except config.ConfigError as exc:
            say(f"{change.unit}: its switch {switch} was not written: {exc}", err=True)
            ok = False
            continue
        if line:
            say(line)
    return ok


def _ask(question: str) -> bool:
    stdin = sys.stdin
    if stdin is None or not stdin.isatty():
        return False
    try:
        answer = input(question)
    except EOFError:
        say("")
        return False
    return answer.strip().lower() in ("y", "yes")


def _describe(change) -> str:
    """`<doc>, every 5 minutes`, from a timer's entry."""
    entry = change.entry
    doc = entry.get("doc") or change.name
    if "every" in entry:
        every = str(entry["every"])
        count, unit = int(every[:-1]), "hour" if every.endswith("h") else "minute"
        when = f"every {unit}" if count == 1 else f"every {count} {unit}s"
    else:
        when = "at " + ", ".join(sorted(entry.get("at", ())))
        days = entry.get("days")
        if days:
            when += " on " + ", ".join(days)
    return f"{doc}, {when}"


def _take_out(record: list, applied) -> None:
    """Drop the entries of units an `ok` `Applied` removed, or disabled because their
    declaration turned `enabled = false`: the unit step has set their state."""
    gone_names: set[str] = set()
    gone_ids: set[str] = set()
    for result in applied:
        change = result.change
        if not result.ok:
            continue
        disabled = (change.action == "change" and not change.enabled
                    and change.enabled_before)
        if change.action != "remove" and not disabled:
            continue
        if change.kind == "socket":
            gone_ids |= {path.name for path in change.paths}
        else:
            gone_names.add(change.name)
    record[:] = [entry for entry in record
                 if entry.name not in gone_names
                 and not (entry.unit in gone_ids)]


def _restart(step: Settle) -> tuple[int, list[str]]:
    """The `finally`: start the record again; print and record each start that failed.
    Returns how many started, and the lines of those that didn't."""
    if not step.record:
        return 0, []
    from . import doctor, units
    from .units._act import RESUME_KEY

    module = step.module
    try:
        acted = units.resume(step.record)
    except Exception as exc:  # resume catches each entry's error; this is a last guard
        acted = [units.Acted(module, entry.name, "start", False,
                             (f"{entry.unit}: {_one_line(exc)}",))
                 for entry in step.record]
    started, problems = 0, []
    for entry, result in zip(step.record, acted):
        if result.ok:
            started += 1
            continue
        lines = list(result.lines) or [f"{entry.unit}: not started"]
        by_hand = None
        if lines[-1].startswith("start it by hand: "):
            by_hand = lines.pop()[len("start it by hand: "):]
        line = f"{module}: {'; '.join(lines)}"
        if by_hand:
            line += f"; start it by hand: {by_hand}"
        say(line, err=True)
        try:
            doctor.record(module, line, key=RESUME_KEY.format(unit=entry.unit), fix=by_hand)
        except (OSError, ValueError):
            pass
        problems.append(line)
    return started, problems


def _clear_resume_records(module: str) -> None:
    """The settle step ended clean: no `resume:` record of the module stands."""
    from . import doctor

    if not doctor._path(module).exists():
        return

    def drop(records: dict) -> dict:
        return {k: v for k, v in records.items() if not str(k).startswith("resume:")}

    try:
        doctor._change(module, drop)
    except (OSError, ValueError):
        pass


def _later_steps(step: Settle) -> int:
    """Steps 4 to 6 (skills, install questions, the `moxi.installs` listeners), which
    run after the `finally`; 0 until they are built."""
    return 0


# -- the teardown ----------------------------------------------------------------------------

def teardown(module: str) -> int:
    """Run `module`'s `TEARDOWN` in this process. 0 when it accepts (or there is none), 3
    when it raises `moxi.modules.Refused`, 1 on any other failure, `INTERRUPTED` on a stop
    request: an interrupt abandons it at once, as a failed one, and its own `finally`
    blocks run as `Interrupted` passes through them."""
    from . import modules

    found = modules.load(module)
    if found.state != modules.PRESENT:
        say(f"{module} is {found.state}, so it has no teardown to run")
        return 0
    if not found.teardown:
        return 0
    code = 1
    try:
        try:
            _state.raising = True
            try:
                result = _call(found.teardown)
            except SystemExit as exc:
                result = exc.code
            check()
            code = 1 if _exit_code(result) else 0
            if code:
                say(f"teardown {module}: failed (exit {result})", err=True)
        except modules.Refused as exc:
            say(f"{module}: {exc.reason}", err=True)
            code = 3
        except Exception as exc:
            _state.raising = False
            say(f"teardown {module}: failed ({_one_line(exc)})", err=True)
            code = 1
    except Interrupted:
        code = INTERRUPTED
    finally:
        _state.raising = False
    if _state.request is not None:
        say(f"teardown {module}: interrupted; nothing was removed", err=True)
        return INTERRUPTED
    return code


# -- the child's entry --------------------------------------------------------------------------

def _call(target: str):
    import importlib

    module_name, _, attr = target.partition(":")
    fn = importlib.import_module(module_name)
    for part in attr.split("."):
        fn = getattr(fn, part)
    return fn()


def _exit_code(result) -> int:
    """A target's result as an exit code: an int is one (a bool is not), a text is 1,
    anything else 0."""
    if isinstance(result, int) and not isinstance(result, bool):
        return result
    return 1 if isinstance(result, str) else 0


def _one_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {text[0]}" if text else type(exc).__name__


def _parse(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(prog="python -m moxi._settle")
    parser.add_argument("module")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--teardown", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None,
         before: Callable[[], object] | None = None) -> int:
    """The child: adopt the parent's claim, take the signals, then run the step. `before`
    runs after the handlers are in place and before the step (tests)."""
    from . import files, paths

    handed = "MOXI_CORE_LOCK_FD" in os.environ  # moxi.files.CLAIM_VARIABLE
    if not files.adopt_core_claim(paths.core_lock()) and handed:
        say("the install's lock claim did not check; nothing done", err=True)
        return 1
    guard_streams()
    install_handlers()
    args = _parse(sys.argv[1:] if argv is None else argv)
    if before is not None:
        before()
    if args.teardown:
        return teardown(args.module)
    return settle(args.module, replace=args.replace)


if __name__ == "__main__":
    sys.exit(main())
