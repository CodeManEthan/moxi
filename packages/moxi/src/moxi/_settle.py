"""The settle step and the teardown, each in a child process of `moxi install` or
`moxi uninstall`, and how the parent starts and waits for that child.

    python -m moxi._settle MODULE [--replace] [--skills | --no-skills]
                                                   the settle step of one module
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
hand, left as a doctor record, and the install exits 1. That record clears when a later
start of the unit succeeds, or when a later settle step ends clean and the unit then runs
or was removed or disabled by that step; a clean step with the unit still down keeps it.

Skills, install questions and the `moxi.installs` listeners come after the `finally`
(`_later_steps`), in that order, so a listener sees the run's units and placements.
Skills are placed by `moxi.place` in this process, its `apply` in a held span; the
module's skills are asked about only while it has no placement, and a placement refused
or failed makes the step exit 1, as does a skill whose folder name is not a skill name
(`moxi-<name>`), which is not placed while the module's others are. They
run unless the move refused (other than for an old directory that is a link to a
relative path), `SETUP` failed, or steps 1 to 3 raised; a unit the plan refused, or a
start the `finally` couldn't make, leaves them to run and the step exits 1. Each install
question (`QUESTIONS`) is asked once per module and recorded in `questions.json`
(`moxi._questions`): a key the config file already sets is recorded without a question
or a write, and with nobody at the keyboard a key is set off with the line that turns it
on. Every listener's `on_install(module, found)` is called, the module's own included; one
that fails is printed, the rest still run, and the step exits 1.

Signals. The child takes SIGINT, SIGTERM, SIGHUP and SIGQUIT (on Windows SIGINT for
Ctrl-C, and SIGBREAK, which Python raises for Ctrl-Break and for a console close) as one
request to stop. A signal that was ignored when the process started stays ignored, so a
`nohup` install survives its terminal. The handlers are Python handlers, never `SIG_IGN`,
so a program the child starts gets default dispositions. In the steps, a signal raises
`Interrupted` at once, except inside the core's own writes (the move, a timer switch with
its entry, `apply`, the placer's `apply`, a question's setting with its record), which
are marked `held()`: there it only sets the request, and the
write finishes before the request is obeyed. In the `finally` it only sets the request,
so `resume` and its reports always run. Then the child prints how many units it started
again and exits 130, the pair's private "interrupted" code. Once the step or the teardown
has returned, the child ignores these signals until it exits: a terminal's signal reaches
the child twice, from the terminal and forwarded by the parent, and the forwarded copy can
come late, after Python has put the default dispositions back while it shuts down, where
it would kill the child and the parent would read the signal in place of the exit code.
Nothing is started after that point, so the ignored dispositions reach no program.

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
#: The teardown child's exit code when the module's `TEARDOWN` could not be loaded, so it
#: never ran and removed nothing; private to the parent and child.
NOT_RUN = 4
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
               teardown: bool = False, skills: bool | None = None) -> list[str]:
    """The settle child's command line (or the teardown child's). `skills` is the
    install's answer ahead to the skills question: True for `--skills`, False for
    `--no-skills`, None to ask."""
    argv = [str(python), "-m", "moxi._settle"]
    if teardown:
        return [*argv, "--teardown", module]
    argv += [module] + (["--replace"] if replace else [])
    if skills is not None:
        argv.append("--skills" if skills else "--no-skills")
    return argv


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
    """`setup.json`'s path; the settle step and the doctor both read it here."""
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
    #: The backend ids of the units the unit step removed or disabled: their `resume:`
    #: records clear at a clean end, since the declaration now sets their state.
    gone: set = field(default_factory=set)
    #: The move refused in a way that leaves the rest of the step to run (an old
    #: directory that is a link to a relative path): the step goes on, and exits 1.
    moved: bool = True
    #: The install's answer ahead to the skills question (`--skills` True,
    #: `--no-skills` False), or None to ask.
    skills: bool | None = None
    #: Whether steps 4 to 6 run after the `finally`: not after a move that refused
    #: (other than the relative link above), a failed `SETUP`, or an exception in steps 1
    #: to 3.
    later: bool = True

    @property
    def restart(self) -> bool:
        """Whether the installed version differs from the one `SETUP` last ran at."""
        return self.found.version != self.before


def settle(module: str, *, replace: bool = False, skills: bool | None = None) -> int:
    """Run `module`'s settle step in this process; returns the exit code: 0, 1 on a
    failure, `INTERRUPTED` on a stop request."""
    from . import modules

    found = modules.load(module)
    if found.state != modules.PRESENT:
        say(f"{module}: not set up, the module is {found.state}"
            + (f": {found.reason}" if found.reason else ""), err=True)
        return 1
    step = Settle(module, found, replace, setup_version(module), skills=skills)
    try:
        try:
            try:
                step.failed = not _steps(step)
            except Interrupted:
                step.failed = True
            except Exception as exc:
                _state.raising = False
                step.failed = True
                step.later = False
                say(f"{module}: the settle step failed: {_one_line(exc)}", err=True)
        except Interrupted:  # a signal in an except clause above
            step.failed = True
            step.later = False
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
    code = 1 if step.failed or problems or not step.moved else 0
    if step.later:
        later = _later_steps(step)
        if later == INTERRUPTED:
            return INTERRUPTED
        code = code or later
    if code == 0:
        _clear_resume_records(step)
    return code


def _steps(step: Settle) -> bool:
    """Steps 1 to 3; True when each went through. Sets the steps flag first and clears
    it as its last act."""
    _state.raising = True
    ok = _move(step) and _setup(step)
    if not ok:
        step.later = False
    ok = ok and _units(step)
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
        if exc.only_move:
            # Nothing was stopped or moved, and the old directory is still read in
            # place, so setup and the units go on; the install still exits 1.
            step.moved = False
            return True
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
    step.gone = _gone_ids(module, applied)
    return ok and all(a.ok for a in applied)


def _switches(step: Settle, changes) -> bool:
    """Each switched timer `plan` will write or keep: a new one is asked about (off with
    nobody at the keyboard), a changed one is set off, an unchanged one is left. The
    switch is written first and its `units.json` entry second.

    A switch key another daemon or timer of the module also uses is never written here:
    writing it would turn that unit on or off too. The key is left as the file has it.
    While it reads off, the timer's entry is written and the timer installed, off; while
    it reads on, no entry is written, so `apply` holds the timer and says how it gets
    installed. One line names the key either way."""
    from . import config
    from .units import _apply
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
        others = _apply.sharers(change, changes)
        if others:
            ok = _shared(step, change, others) and ok
            continue
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


def _shared(step: Settle, change, others: list[str]) -> bool:
    """A new or changed timer whose switch other units also use: the key is read, never
    written. Off: the entry is written, so the timer is installed off. On: nothing is
    written, and `apply` holds the timer (it prints the line for an `add` or `change`)."""
    from . import config
    from .units import _apply
    from .units import _state as recorded

    module, switch = step.module, change.switch
    try:
        on = bool(config.load(module, config.schema_for(step.found))[switch])
    except (config.ConfigError, KeyError, TypeError) as exc:
        say(f"{change.unit}: its switch {switch} cannot be read: {_one_line(exc)}", err=True)
        return False
    if not on:
        with held():
            recorded.record(module, change.name, change.digest)
        say(_apply.shared_line(change, others, held=False))
    elif change.action == "keep":
        say(_apply.shared_line(change, others, held=True))
    return True


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
    declaration turned `enabled = false`: the unit step has set their state. The record
    holds daemons only, so a timer's change never drops an entry, even a daemon's of the
    same name; a socket unit drops the entry of its own id, and so does a socket unit a
    daemon's set dropped, which has no file left to start."""
    gone_names: set[str] = set()
    gone_ids: set[str] = set()
    for result in applied:
        change = result.change
        if not result.ok:
            continue
        gone_ids |= {path.name for path in change.dropped}
        disabled = (change.action == "change" and not change.enabled
                    and change.enabled_before)
        if change.action != "remove" and not disabled:
            continue
        if change.kind == "socket":
            gone_ids |= {path.name for path in change.paths}
        elif change.kind == "daemon":
            gone_names.add(change.name)
    record[:] = [entry for entry in record
                 if entry.name not in gone_names
                 and not (entry.unit in gone_ids)]


def _gone_ids(module: str, applied) -> set[str]:
    """The backend ids of the daemons and sockets an `ok` `Applied` removed, or disabled
    because their declaration turned `enabled = false`, and the socket units a daemon's
    set dropped."""
    from .units import _backend

    try:
        impl = _backend.implementation()
    except NotImplementedError:
        return set()
    if impl is None:
        return set()
    gone: set[str] = set()
    for result in applied:
        change = result.change
        disabled = (change.action == "change" and not change.enabled
                    and change.enabled_before)
        if result.ok:
            gone |= {path.name for path in change.dropped}
        if not result.ok or (change.action != "remove" and not disabled):
            continue
        if change.kind == "socket":
            gone |= {path.name for path in change.paths}
        elif change.kind == "daemon":
            gone |= set(impl.ids(module, change.name, "daemon"))
    return gone


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
        detail = f"{module}: {'; '.join(lines)}"
        # The record's own `fix` holds the by-hand command, so its text doesn't repeat it.
        line = f"{detail}; start it by hand: {by_hand}" if by_hand else detail
        say(line, err=True)
        try:
            doctor.record(module, detail, key=RESUME_KEY.format(unit=entry.unit), fix=by_hand)
        except (OSError, ValueError):
            pass
        problems.append(line)
    return started, problems


def _clear_resume_records(step: Settle) -> None:
    """The settle step ended clean: clear each `resume:<unit>` record of the module whose
    unit now runs (one `status` read shows it `active`), or that the unit step removed or
    disabled. A record whose unit is still down keeps its by-hand command, and when the
    `status` read fails only the removed and disabled units' records clear."""
    import json

    from . import doctor
    from .units._act import RESUME_KEY

    module = step.module
    prefix = RESUME_KEY.format(unit="")
    try:
        held = json.loads(doctor._path(module).read_bytes())
    except (OSError, ValueError):
        return
    if not isinstance(held, dict):
        return
    waiting = {key[len(prefix):] for key in held if str(key).startswith(prefix)}
    if not waiting:
        return
    clear = waiting & step.gone
    if waiting - clear:
        clear |= waiting & _running_ids(module)
    if not clear:
        return

    def drop(records: dict) -> dict:
        return {k: v for k, v in records.items()
                if not (str(k).startswith(prefix) and str(k)[len(prefix):] in clear)}

    try:
        doctor._change(module, drop)
    except (OSError, ValueError):
        pass


def _running_ids(module: str) -> set[str]:
    """The backend ids of the module's daemons and sockets that one `status` read shows
    `active`; empty when the read fails."""
    from . import units
    from .units import _backend

    try:
        impl = _backend.implementation()
        states = units.status([module])
        sockets = {} if impl is None else {ref.name: ref.ids for ref in impl.marked(module)
                                           if ref.kind == "socket"}
    except Exception:  # noqa: BLE001 - a failed read clears nothing
        return set()
    if impl is None:
        return set()
    out: set[str] = set()
    for state in states:
        if state.module != module or state.state != "active":
            continue
        if state.kind == "daemon":
            # A daemon's own id is its last.
            out.add(impl.ids(module, state.name, "daemon")[-1])
        elif state.kind == "socket":
            # An active socket state means every unit of its set is active.
            out |= set(sockets.get(state.name, ()))
    return out


def _later_steps(step: Settle) -> int:
    """Steps 4 to 6, after the `finally`: skills, install questions, then every
    `moxi.installs` listener. Each runs even when an earlier one failed. Returns 0, 1
    when one failed, or `INTERRUPTED`: a signal here raises at once outside a held span,
    as in steps 1 to 3, and there is no stopped unit left to start again."""
    module = step.module
    try:
        try:
            _state.raising = True
            ok = _skills(step)
            ok = _ask_questions(step) and ok
            ok = _listeners(step) and ok
        finally:
            _state.raising = False
    except Interrupted:
        say(f"{module}: interrupted; no later step ran", err=True)
        return INTERRUPTED
    except Exception as exc:
        say(f"{module}: the settle step failed: {_one_line(exc)}", err=True)
        return 1
    if _state.request is not None:
        say(f"{module}: interrupted; no later step ran", err=True)
        return INTERRUPTED
    return 0 if ok else 1


def _skills(step: Settle) -> bool:
    """Step 4: the module's skills (`SKILLS`, one subfolder per skill), placed by the
    placer (`moxi.place`) for each harness in `skills.harnesses`, with the module as
    owner. The placer is called in this process, where its lock yields on the install's
    claim; a `moxi` command started from here would wait for that lock instead.

    The step manages only the placements it makes: those whose source lies in the
    module's own package (`moxi.place.from_package`). The owner's other placements (one
    another module handed it, a project's skill it placed for a user) are left exactly as
    they are: the plan treats `items` as the owner's whole set, so their `remove` steps
    are dropped before `apply`. When the module has none of its own placements (none
    placed, and none handed over unless seeded), `skills_wanted` asks first, naming only
    the skills the plan would place, and a no places nothing. When it has some, there is
    no question: the plan updates, adds and removes them as the package's `SKILLS` say now,
    so a module that drops a skill, or `SKILLS` altogether, has it removed. One handed
    to another owner is skipped while any other owner places it; once none does, the
    skill is placed again with no question. A seeded hand-over (`moxi.place.adopt(...,
    seeded=True)`) is the user's choice for that one skill and never stands in for a yes
    here: it is skipped while it stands, and counts as none of the module's own, so with
    only seeded hand-overs the question is asked for the rest; once it has lapsed, its
    skill is named in the question like any other it would place. A name in
    `skills.harnesses` whose harness row is skipped or missing still makes its items:
    the placer refuses each (cause `harness`) with a line and keeps what was placed
    there, so a broken row never removes skills and never fails the step (the doctor
    warns about it); a name taken out of the setting removes them. `apply` runs in a
    held span. Returns False when the skills can't be read or a placement was refused
    for another cause or failed, or when a skill's folder name is not a skill name
    (`moxi.names.is_skill_name`): that skill alone is not placed, and its line says so."""
    from . import place

    module, found = step.module, step.found
    try:
        placed = place.status(module)
        if found.skills is None and not placed:
            return True
        skills, source = _skill_folders(found)
        if skills is None:
            say(f"{module}: skills not placed: {source}", err=True)
            return False
        misnamed = _misnamed(module, skills)
        skills = [name for name in skills if name not in misnamed]
        # Every name, with a row or not: see above.
        harnesses = list(dict.fromkeys(place._core_settings()["skills.harnesses"]))
        usable = place.chosen()[0] or harnesses  # the question names only working rows
        items = [place.Item(source / name, harness, name)
                 for name in skills for harness in harnesses]
        planned = place.plan(module, items)
        handed = place._read_ledger().get(module, {}).get("handed", {})
    except Exception as exc:  # noqa: BLE001 - a broken core config or ledger
        say(f"{module}: skills not placed: {_one_line(exc)}", err=True)
        return False
    steps = planned[:len(items)] + [
        s for s in planned[len(items):]
        if s.action == "remove" and place.from_package(found, s.item.source)]
    held_now = {str(p.path) for p in placed}
    labels = {(p.harness, p.kind, p.name) for p in placed}
    # A hand-over counts as the module's own only when it was not seeded, whether it
    # stands or has lapsed: the flag is read, never membership of `handed` alone.
    given = {key for key, entry in handed.items()
             if not (isinstance(entry, dict) and entry.get("seeded"))}
    own = len(steps) > len(items) or any(
        str(s.target) in held_now or str(s.target) in given
        or (s.cause == "harness" and (s.item.harness, s.item.kind, s.item.name) in labels)
        for s in steps)
    # The question names only the skills it would place: not one whose every target is
    # skipped as handed over (a standing seeded hand-over, when nothing is the module's).
    wanted = [name for name in skills
              if any(s.action != "skip" for s in steps[:len(items)] if s.item.name == name)]
    if not own and (not wanted or not skills_wanted(step, wanted, usable)):
        return not misnamed
    with held():
        done = place.apply(steps, replace=step.replace,
                           say=lambda line: say(f"{module}: {line}"))
    return _skills_report(module, steps, done, step.replace) and not misnamed


def _misnamed(module: str, skills: list[str]) -> set[str]:
    """The skills in `skills` whose folder name is not a skill name, each said in one
    line. They are not placed and make no item, so a placement of one an earlier install
    made is removed as that of a dropped skill is, and the step fails. Only the folder
    name is read: the contract check holds a skill's `SKILL.md` before release."""
    from . import names

    misnamed = set()
    for name in skills:
        try:
            names.check_skill_name(name)
        except ValueError as exc:
            say(f"{module}: skill {name} not placed: {exc}", err=True)
            misnamed.add(name)
    return misnamed


def _skill_folders(found) -> tuple[list[str] | None, object]:
    """The skill names in `found`'s `SKILLS` folder (its subfolders `moxi.names.
    is_skill_folder` counts, sorted) and the folder, read through
    `importlib.resources`; or None and the line that says why they can't be read. A
    package not on disk (in a zip, say) is refused, since a harness reads a skill from a
    folder."""
    import importlib.resources
    from pathlib import Path

    from . import names

    if found.skills is None:
        return [], None
    try:
        folder = importlib.resources.files(found.package).joinpath(found.skills)
    except (ImportError, TypeError, ValueError) as exc:
        return None, f"package {found.package} can't be read ({_one_line(exc)})"
    if not isinstance(folder, Path):
        return None, (f"package {found.package} is not a folder on disk, so its skills "
                      f"can't be placed")
    try:
        skills = sorted(entry.name for entry in folder.iterdir()
                        if names.is_skill_folder(entry.name) and entry.is_dir())
    except OSError as exc:
        return None, f"SKILLS folder {folder} can't be read ({exc.strerror or exc})"
    return skills, folder


def _skills_report(module: str, steps, done, replace: bool) -> bool:
    """One line per placement the plan added, updated or removed; True when every step
    went through (a skip counts, and so does a refusal for a broken harness row, which
    `apply` said), False on another refusal `replace` didn't place over or a step
    `apply` couldn't carry out (it said why)."""
    from . import place

    left = {str(p.path) for p in done}
    still = {str(p.path) for p in place.status(module)}
    ok = True
    for s in steps:
        item, target = s.item, str(s.target)
        if s.action == "remove":
            if target in still:
                ok = False
            else:
                say(f"{module}: removed skill {item.name} for {item.harness} ({target})")
        elif s.action in ("add", "update") or (
                s.action == "refuse" and replace and s.cause in place._OCCUPANTS):
            if target in left:
                verb = "updated" if s.action == "update" else "placed"
                say(f"{module}: {verb} skill {item.name} for {item.harness} ({target})")
            else:
                ok = False
        elif s.action == "refuse" and s.cause != "harness":
            ok = False
        elif s.action == "keep" and target not in left:
            ok = False
    return ok


def skills_wanted(step: Settle, skills: list[str], harnesses: list[str]) -> bool:
    """Whether to place the module's skills `skills` for `harnesses`, asked when the
    ledger holds none of its placements. `--skills` or `--no-skills` answers ahead; at
    a terminal the question is asked, default no; with nobody at the keyboard the answer
    is no, and one line says how to place them later. The install never waits here."""
    module = step.module
    if step.skills is not None:
        return step.skills
    stdin = sys.stdin
    on = False
    if stdin is not None and stdin.isatty():
        count = f"{len(skills)} skill" + ("" if len(skills) == 1 else "s")
        on = _ask(f"{module} ships {count} ({', '.join(skills)}). Place them for "
                  f"{_and(harnesses)}? [y/N] ")
    if not on:
        say(f"{module}: skills not placed; place them with: moxi install {module} "
            f"--skills")
    return on


def _and(items: list[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _ask_questions(step: Settle) -> bool:
    """Step 5: the module's install questions (`QUESTIONS`), in the order declared.

    A key that isn't switch-shaped (a bool read at use, no `env`, default false; or a
    switch table's entry) is not asked: one line says why, and the step exits 1. A key
    recorded for the module is not asked again. A key the config file already sets is
    recorded without a question or a write. Otherwise, at a terminal it is asked,
    default no; with nobody at the keyboard it is no. The answer is set with
    `moxi.config.set` and then recorded, both in one held span; an answer of no prints
    the line that turns the key on."""
    from . import _questions, config
    from .units import switch_problem

    module, found = step.module, step.found
    if not found.questions:
        return True
    try:
        schema = config.schema_for(found)
    except config.ConfigError as exc:
        say(f"{module}: install questions not asked: {_one_line(exc)}", err=True)
        return False
    done = _questions.recorded(module)
    ok = True
    for key, text in found.questions.items():
        problem = switch_problem(schema, key)
        if problem is not None:
            say(f"{module}: install question {key} not asked: {problem}", err=True)
            ok = False
            continue
        if key in done:
            continue
        try:
            already = config.load(module, schema).source(key) == "file"
        except config.ConfigError as exc:
            say(f"{module}: install question {key} not asked: {exc}", err=True)
            ok = False
            continue
        if already:
            with held():
                _questions.record(module, key)
            continue
        on = _ask(f"{module}: {text}? [y/N] ")
        try:
            with held():
                config.set(module, key, on, by="moxi install")
                _questions.record(module, key)
        except config.ConfigError as exc:
            say(f"{module}: install question {key} not set: {exc}", err=True)
            ok = False
            continue
        if not on:
            say(f"{module}: {text} is off; turn it on with: moxi config set {module} "
                f"{key} true")
    return ok


#: The entry-point group of the install listeners.
INSTALLS = "moxi.installs"


def _listeners(step: Settle) -> bool:
    """Step 6: every `moxi.installs` listener's `on_install(module, found)`, the
    module's own included, in the group's order. One that fails (it can't be loaded,
    lacks `on_install`, or raises) is printed and the rest still run; the step then
    exits 1. The stop request is checked as each returns."""
    from . import modules

    module, ok = step.module, True
    for ext in modules.extensions(INSTALLS):
        try:
            fn = getattr(ext.load(), "on_install")
            fn(module, step.found)
        except Interrupted:
            raise
        except (Exception, SystemExit) as exc:
            check()
            reason = (exc.reason if isinstance(exc, modules.ExtensionError)
                      else _one_line(exc))
            say(f"{module}: the moxi.installs listener {ext.name} failed: {reason}",
                err=True)
            ok = False
            continue
        check()
    return ok


# -- the teardown ----------------------------------------------------------------------------

def teardown(module: str) -> int:
    """Run `module`'s `TEARDOWN` in this process. 0 when it accepts (or there is none), 3
    when it raises `moxi.modules.Refused`, `NOT_RUN` when its target can't be loaded, 1 on
    any other failure, `INTERRUPTED` on a stop request: an interrupt abandons it at once,
    as a failed one, and its own `finally` blocks run as `Interrupted` passes through them.
    A failure's text is the module's own: a returned text is said as it is."""
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
                fn = _target(found.teardown)
            except modules.Refused:
                raise
            except Exception as exc:
                fn = None
                _state.raising = False
                say(f"teardown {module}: {found.teardown} can't be loaded "
                    f"({_one_line(exc)})", err=True)
                code = NOT_RUN
            if fn is not None:
                try:
                    result = fn()
                except SystemExit as exc:
                    result = exc.code
                check()
                code = 1 if _exit_code(result) else 0
                if code and isinstance(result, str):
                    say(result, err=True)
                elif code:
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
    return _target(target)()


def _target(target: str):
    """The callable a `"pkg.mod:fn"` target names, imported."""
    import importlib

    module_name, _, attr = target.partition(":")
    fn = importlib.import_module(module_name)
    for part in attr.split("."):
        fn = getattr(fn, part)
    return fn


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
    answer = parser.add_mutually_exclusive_group()
    answer.add_argument("--skills", dest="skills", action="store_true", default=None)
    answer.add_argument("--no-skills", dest="skills", action="store_false")
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
    try:
        if args.teardown:
            return teardown(args.module)
        return settle(args.module, replace=args.replace, skills=args.skills)
    finally:
        _ignore_signals()


def _ignore_signals() -> None:
    """Ignore `signals()` from here to the process's end: the child's last act, once its
    exit code is decided, so a late copy of a signal it already counted can't kill it as
    the interpreter shuts down."""
    for signum in signals():
        signal.signal(signum, signal.SIG_IGN)


if __name__ == "__main__":
    sys.exit(main())
