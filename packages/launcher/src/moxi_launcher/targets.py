"""Where a launch starts its session: this terminal, headless, or a tmux slot.

A target is looked up by the name the launch's `target` row holds (`get`), and is asked
three things, in this order:

1. `check(resolved)`: the live state it needs, before anything is composed or written. A
   refusal here writes nothing.
2. `prepare(composition, env)`: the command it will run, found before the launch row is
   written, so a harness that is not installed is refused with nothing written.
3. `start(launch)`: after the row is written (`spawning`), start the session, record the
   process and move the row to `started`, or end it `failed` with a reason.

**This terminal** (`terminal`, the default). The launcher becomes the harness. It writes
its own pid to the row (exec keeps it) and `started`, starts the detached deliverer when a
first turn is owed, then execs the harness in the launch's directory. On Windows, where
there is no exec, the harness starts as a child with this terminal, its pid is recorded,
the deliverer is given that pid, and the launcher waits and exits with the child's code.

**Headless** (`headless`). The harness's non-interactive mode, with the brief as its
prompt, started detached and left: a session of its own (a detached process group on
Windows), stdin closed, its output appended to the run log `STATE/runs/<request id>.log`.
The pid and its start time are recorded, and the launch returns at once.

**A tmux slot** (`slot`; Linux, macOS and WSL2, with slots installed). A launch that names
a slot (a number or a name) goes only there, and `check` refuses it, naming who has it,
when slots says it is not launchable; one that names none takes the lowest launchable slot
or a new one. slots claims the slot and opens the window in one step (`start`), with the
launch's environment on that command only. The row records the slot, the pane and the
pane's pid (the harness's, since slots' trampoline execs it), and `started` once slots says
the command started (or could not confirm it either way), or `failed` with slots' exec line.
A slot taken since `check` ends the row `failed` with slots' reason, naming the holder
(exit 3); slots' lock held too long ends it `try-again` (exit 75). Nothing retries. The
launch then watches for the startup screens it is set to answer (`startup`), and sends the
first turn itself before it returns. Native Windows, and an install without slots, are
refused with a hint naming the other two targets.

**The sweep.** A launch's run files (its run log and its system prompt file) are kept for
`runs.keep_days` and then removed, and a run log past `LOG_CAP` is cut from the front to
fit, by the sweep each launch runs before it starts its session. A file whose launch is
still running is left for a later sweep, since its process is still writing to it.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moxi import exit as exits
from moxi import files, proc
from moxi.config import ConfigError
from moxi.paths import Layout

from . import catalog, first, slots, startup, store
from .compose import Composition

log = logging.getLogger("moxi.launcher.targets")

MODULE = "launcher"
#: The folder under the launcher's state root that holds every launch's run files.
RUNS = "runs"
#: The largest a run log is kept, in bytes; the sweep cuts a longer one from the front.
LOG_CAP = 8 * 1024 * 1024
#: The line a cut run log starts with.
CUT_LINE = "[the launcher cut {size} bytes from the front of this log]\n"
#: The suffixes of a launch's run files, after its request id.
RUN_FILES = (".log", ".prompt.md")
#: Reasons a row ends `failed` with when a start fails outright (exit 1).
SPAWN_FAILED = "spawn-failed"
NO_DELIVERER = "no-deliverer"
#: Reasons a failed row exits 1 with; any other failed reason is a refusal (3), except
#: `TRY_AGAIN` (75).
FAILED_ERRORS = frozenset({SPAWN_FAILED, NO_DELIVERER, first.NO_READY,
                           first.HANDOFF_UNREADABLE, first.NO_TURNS, startup.EXITED,
                           startup.SCREEN_MISMATCH})
TRY_AGAIN = "try-again"

WINDOWS = sys.platform == "win32"

# The calls a target starts processes through, named here so a test can stand in for
# them.
_execve = os.execve
_popen = subprocess.Popen
_spawn = proc.spawn


# -- refusals ---------------------------------------------------------------------------

class NoCommand(catalog.Refused):
    """The command a launch runs is not found. Nothing is written."""

    code = "not-found"
    exit_code = exits.ERROR

    def __init__(self, command: str, what: str = "the harness"):
        self.command = command
        super().__init__(None, f"{command} is not found on PATH; {what} can't be started",
                         f"install {command}, or put it on PATH")


#: The hint every refusal of the slot target itself gives.
OTHER_TARGETS = "launch with --target terminal or --target headless"


class NoSlots(catalog.Refused):
    """A slot launch without slots installed. Nothing is written."""

    code = "no-slots"

    def __init__(self):
        super().__init__("target", "the slot target needs slots, which is not installed",
                         f"install moxi-slots, or {OTHER_TARGETS}")


class SlotsUnsupported(catalog.Refused):
    """A slot launch where tmux slots can't run: native Windows, or what slots'
    `supported()` says. Nothing is written."""

    code = "unsupported"

    def __init__(self, why: str):
        super().__init__("target", f"tmux slots can't run here: {why}", OTHER_TARGETS)


class SlotTaken(catalog.Refused):
    """A named slot that is not launchable (held, occupied or missing), or one that is
    not there; `holder` names who has it, when someone does. Nothing is written."""

    code = "slot-taken"

    def __init__(self, slot: object, problem: str, hint: str, holder: str | None = None):
        self.slot, self.holder = slot, holder
        super().__init__("slot", problem, hint)


class SlotsBusy(catalog.Refused):
    """slots' lock, or its server, did not answer in its bound. Nothing is written."""

    code = "try-again"
    exit_code = exits.TEMPFAIL


class SlotsFailed(catalog.Refused):
    """slots could not be asked about the slot. Nothing is written."""

    code = "slots-failed"
    exit_code = exits.ERROR


# -- the shapes -------------------------------------------------------------------------

@dataclass(frozen=True)
class Launch:
    """What a target is handed to start: one launch, composed and in the launch log.

    `argv` is the composition's, as `preview` shows it; `command` the executable found for
    its first word, which the target runs it as. `env` is the child's whole environment,
    `cwd` its directory. `deliverer_env` is the environment the detached deliverer runs
    in, the launcher's own."""

    conn: sqlite3.Connection
    request_id: str
    composition: Composition
    argv: tuple[str, ...]
    command: str
    env: Mapping[str, str]
    cwd: str
    deliverer_env: Mapping[str, str] | None = None

    @property
    def first_owed(self) -> bool:
        """Whether the session is owed a first turn that the sender delivers."""
        return self.composition.first is not None


@dataclass(frozen=True)
class Started:
    """What a target did: the state it left the row in, the reason with a `failed`, the
    process it started, the code the launch exits with, a line for the caller, and the
    first turn's outcome when the target sent it itself."""

    state: str
    exit_code: int
    reason: str | None = None
    pid: int | None = None
    pane_id: str | None = None
    slot: str | None = None
    line: str = ""
    outcome: first.Outcome | None = None


class Target:
    """One place a session starts. A subclass sets `name` and overrides what it needs."""

    name = ""

    def check(self, resolved: Any) -> list[catalog.Refused]:
        """The refusals of the live state this target needs; writes nothing."""
        return []

    def prepare(self, composition: Composition, env: Mapping[str, str]) -> str:
        """The command the launch's argv runs as. Raises a refusal (`NoCommand`) before
        anything is written."""
        return composition.argv[0]

    def start(self, launch: Launch) -> Started:
        raise NotImplementedError


# -- the targets ------------------------------------------------------------------------

class Terminal(Target):
    """This terminal: exec on Linux and macOS, a waited-for child on Windows."""

    name = "terminal"

    def prepare(self, composition: Composition, env: Mapping[str, str]) -> str:
        command = find(composition.argv[0], env)
        if composition.first is not None:
            try:
                first.deliverer_argv("-", 0)
            except FileNotFoundError:
                raise NoCommand("moxi", "the first turn's deliverer") from None
        return command

    def start(self, launch: Launch) -> Started:
        if WINDOWS:
            return self._start_child(launch)
        rid = launch.request_id
        pid = os.getpid()
        _started(launch.conn, rid, pid)
        if launch.first_owed:
            failed = _start_deliverer(launch, pid)
            if failed is not None:
                return failed
        here = os.getcwd()
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            os.chdir(launch.cwd)
            _execve(launch.command, list(launch.argv), dict(launch.env))
        except OSError as error:
            os.chdir(here)
            return fail(launch.conn, rid, SPAWN_FAILED,
                           f"{launch.argv[0]} could not be started: {error}")
        # Only a stand-in for exec returns.
        return Started("started", exits.OK, pid=pid)

    def _start_child(self, launch: Launch) -> Started:
        rid = launch.request_id
        try:
            child = _popen([launch.command, *launch.argv[1:]], env=dict(launch.env),
                           cwd=launch.cwd)
        except OSError as error:
            return fail(launch.conn, rid, SPAWN_FAILED,
                           f"{launch.argv[0]} could not be started: {error}")
        _started(launch.conn, rid, child.pid)
        if launch.first_owed:
            _start_deliverer(launch, child.pid)
        while True:
            try:
                code = child.wait()
                break
            except KeyboardInterrupt:
                # The harness has the terminal and takes its own Ctrl-C.
                continue
        return Started("started", code, pid=child.pid)


class Headless(Target):
    """Headless: started detached, its output in the run log, and left."""

    name = "headless"

    def prepare(self, composition: Composition, env: Mapping[str, str]) -> str:
        return find(composition.argv[0], env)

    def start(self, launch: Launch) -> Started:
        rid = launch.request_id
        output = run_log(rid)
        argv = [launch.command, *launch.argv[1:]] if WINDOWS else list(launch.argv)
        try:
            pid = _spawn(argv, detached=True, output=output, env=dict(launch.env),
                         cwd=launch.cwd)
        except OSError as error:
            return fail(launch.conn, rid, SPAWN_FAILED,
                           f"{launch.argv[0]} could not be started: {error}")
        _started(launch.conn, rid, pid)
        return Started("started", exits.OK, pid=pid,
                       line=f"launch {rid} started headless, pid {pid}; its output goes "
                            f"to {output}")


class Slot(Target):
    """A tmux slot, through slots: the launch claims a slot and starts there, the
    startup screens it is set to answer are answered, and the first turn is sent before
    the launch returns."""

    name = "slot"

    def check(self, resolved: Any) -> list[catalog.Refused]:
        if WINDOWS:
            return [SlotsUnsupported("native Windows has no tmux; slots runs on Linux, "
                                     "macOS and WSL2")]
        api = slots.api()
        if api is None:
            return [NoSlots()]
        support = api.supported()
        if not support.ok:
            return [SlotsUnsupported(support.why or "slots says so")]
        named = named_slot(getattr(resolved, "values", {}) or {})
        if named is None:
            return []
        try:
            found = api.show(named)
        except api.TryAgain as error:
            return [SlotsBusy("slot", f"slots did not answer about slot {named}: {error}",
                              "try again in a moment")]
        except api.TmuxError as error:
            return [SlotsFailed("slot", f"slots could not show slot {named}: {error}",
                                "moxi slots list shows the slots")]
        if found is None:
            return [SlotTaken(named, f"there is no slot {named}",
                              f"make it with moxi slots create, or leave out the slot to "
                              "take the lowest free one")]
        if found.launchable:
            return []
        state = found.state or "not launchable"
        holder = _slot_holder(found)
        by = f" by {holder}" if holder else ""
        if state == "missing":
            return [SlotTaken(named, f"slot {named} is missing: its tmux session is gone",
                              f"recreate it with moxi slots create {named}")]
        if found.hold is not None:
            state = "held"
        return [SlotTaken(named, f"slot {named} is {state}{by}",
                          "name another slot, or leave out the slot to take the lowest "
                          "free one", holder)]

    def prepare(self, composition: Composition, env: Mapping[str, str]) -> str:
        # slots' trampoline finds the command on the child's PATH; a harness that is not
        # there is refused now, before anything is written.
        find(composition.argv[0], env)
        return composition.argv[0]

    def start(self, launch: Launch) -> Started:
        conn, rid = launch.conn, launch.request_id
        api = slots.api()
        if api is None:
            return fail(conn, rid, SPAWN_FAILED, "slots can't be loaded")
        named = named_slot(launch.composition.values)
        row = store.get(conn, rid)
        meta = {"request_id": rid, "session_id": row.session_id if row else None}
        try:
            placed = api.start(named, list(launch.argv), env=dict(launch.env),
                               cwd=launch.cwd, label=f"launcher:{rid}", meta=meta,
                               create=named is None)
        except api.Refused as refused:
            holder = _refused_holder(refused.reason, refused.holder)
            words = refused.words or f"slots refused the slot: {refused.reason}"
            if holder and holder not in words:
                words += f" ({refused.reason} by {holder})"
            return fail(conn, rid, refused.reason, words)
        except api.TryAgain as error:
            return fail(conn, rid, TRY_AGAIN, f"slots' lock was not had in time: {error}")
        except api.TmuxError as error:
            return fail(conn, rid, SPAWN_FAILED,
                        f"{launch.argv[0]} could not be started in a slot: {error}")

        slot, pane_id = placed.slot, placed.pane_id
        where = {"slot": str(slot), "pane_id": pane_id}
        verdict = placed.exec
        if verdict not in ("started", "unconfirmed"):
            line = placed.exec_line or verdict or "no verdict"
            status = placed.dead_status
            store.set_facts(conn, rid, **where)
            return fail(conn, rid, SPAWN_FAILED,
                        f"{launch.argv[0]} did not start in slot {slot}: {line}"
                        + (f" (pane status {status})" if status is not None else ""))
        pid = _pane_pid(api, slot)
        ident = proc.identity(pid) if pid else None
        store.set_state(conn, rid, "started", leaving=("spawning",), pid=pid,
                        pid_started=ident.started if ident is not None else None, **where)
        here = f"launch {rid} started in slot {slot}"
        if verdict == "unconfirmed":
            here += " (slots could not confirm that the command started)"

        turns = first.turns_api(quiet=True)
        try:
            enabled = startup.screens(launch.composition.harness, api)
        except ConfigError as error:
            log.warning("startup screens of %s not watched: %s", rid, error)
            enabled = []
        if enabled:
            watched = startup.watch(conn, rid, slot, enabled, api=api,
                                    ready=_ready(turns, launch, row, pid))
            if watched.failed:
                done = fail(conn, rid, watched.reason or startup.EXITED, watched.why,
                            capture=watched.capture)
                capture = f"; the screen is saved at {watched.capture}" \
                    if watched.capture else ""
                return Started(done.state, done.exit_code, done.reason, pid, pane_id,
                               str(slot), done.line + capture)

        if not launch.first_owed:
            return Started("started", exits.OK, pid=pid, pane_id=pane_id, slot=str(slot),
                           line=here)
        outcome = first.send(conn, rid, pid=pid, turns=turns)
        line = f"{here}; {outcome.line()}"
        if outcome.outcome == "no-ready" and launch.composition.harness == "codex":
            line += (f". Codex may be waiting on its folder-trust prompt: attach to slot "
                     f"{slot} to answer it, or launch with trust_cwd on")
        now = store.get(conn, rid)
        return Started(now.state if now else "started", outcome.exit_code,
                       now.reason if now else outcome.reason, pid, pane_id, str(slot),
                       line, outcome)


#: The targets by the `target` row's values.
TARGETS: dict[str, Target] = {
    "terminal": Terminal(),
    "headless": Headless(),
    "slot": Slot(),
}


def get(name: str) -> Target:
    """The target `name`; a `catalog.BadValue` for a name no target has."""
    try:
        return TARGETS[name]
    except KeyError:
        raise catalog.BadValue("target", f"there is no target {name!r}",
                               "use one of " + ", ".join(TARGETS)) from None


# -- what the targets share -------------------------------------------------------------

def find(command: str, env: Mapping[str, str]) -> str:
    """`command` as the child's PATH finds it; `NoCommand` when it is not there."""
    found = shutil.which(command, path=env.get("PATH", os.defpath))
    if found is None:
        raise NoCommand(command)
    return found


def run_log(request_id: str, env: Mapping[str, str] | None = None) -> Path:
    """A launch's run log, `STATE/runs/<request id>.log`: a headless session's output,
    and the deliverer's line for a launch in this terminal."""
    return first.run_log(request_id, env)


def runs_dir(env: Mapping[str, str] | None = None) -> Path:
    return Layout(MODULE).state_dir(env) / RUNS


def exit_of(row: store.LaunchRow) -> int:
    """The code a launch exits with for its stored row: 0 unless it ended `failed`; then
    1 for a start or boot that failed, 75 for a lock held too long, and 3 for a refusal."""
    return exits.OK if row.state != "failed" else failed_exit(row.reason)


def failed_exit(reason: str | None) -> int:
    """The code a launch that ended `failed` with `reason` exits with."""
    if reason == TRY_AGAIN:
        return exits.TEMPFAIL
    return exits.ERROR if reason in FAILED_ERRORS else exits.REFUSED


def _started(conn: sqlite3.Connection, request_id: str, pid: int) -> None:
    ident = proc.identity(pid)
    store.set_state(conn, request_id, "started", leaving=("spawning",), pid=pid,
                    pid_started=ident.started if ident is not None else None)


def fail(conn: sqlite3.Connection, request_id: str, reason: str, why: str,
         **more: Any) -> Started:
    """End a launch's row `failed` with `reason` from before or just after its start (or
    before its session is ready), and record why, with anything in `more`, as a
    `start-failed` event. The exit code is the reason's (`failed_exit`)."""
    store.set_state(conn, request_id, "failed", reason=reason,
                    leaving=("spawning", "started", "ready"))
    detail = {"reason": reason, "why": why,
              **{k: v for k, v in more.items() if v is not None}}
    store.add_event(conn, "start-failed", detail, request_id=request_id)
    return Started("failed", failed_exit(reason), reason=reason,
                   line=f"launch {request_id} failed: {why}")


def named_slot(values: Mapping[str, object]) -> int | str | None:
    """The slot a launch's values name, a number or a name, or None when they name none."""
    value = values.get("slot")
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def _slot_holder(found: Any) -> str | None:
    """Who has a slot that `show` read: the hold's holder and until, else the last
    launch's label and meta."""
    if found.hold is not None:
        return _hold_words(found.hold)
    return _last_words(found.last)


def _refused_holder(reason: str, holder: Any) -> str | None:
    """Who has the slot a `start` was refused, in a few words: slots names the hold for
    `held` and the last launch for `occupied`, and nobody for its other refusals."""
    if holder is None:
        return None
    if reason == "held":
        return _hold_words(holder)
    if reason == "occupied":
        return _last_words(holder)
    return None


def _hold_words(hold: Any) -> str | None:
    """A hold (`holder`, `until` in epoch seconds) in a few words."""
    if hold is None or not hold.holder:
        return None
    until = hold.until
    when = (f" until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(until))}"
            if isinstance(until, (int, float)) else "")
    return f"{hold.holder}{when}"


def _last_words(last: Any) -> str | None:
    """A last launch (`label`, and the launcher's own `meta`) in a few words."""
    if last is None:
        return None
    words = str(last.label or "")
    meta = last.meta
    if isinstance(meta, Mapping) and meta:
        facts = ", ".join(f"{k} {v}" for k, v in meta.items() if v is not None)
        words = f"{words} ({facts})" if words else facts
    return words or None


def _pane_pid(api: Any, slot: Any) -> int | None:
    """The pid of slot `slot`'s run pane, as slots shows it, or None."""
    try:
        found = api.show(slot)
    except (api.TryAgain, api.TmuxError):  # the pid is a fact the row can do without
        return None
    pid: int | None = found.pane_pid if found is not None else None
    return pid if pid and pid > 0 else None


def _ready(turns: Any, launch: Launch, row: store.LaunchRow | None,
           pid: int | None):
    """How the screens loop asks turns whether the session is ready, or None without
    turns: by the minted session id for Claude, by the pane's pid for Codex's host."""
    if turns is None:
        return None
    if launch.composition.harness == "codex":
        if launch.composition.mode != "host" or not pid:
            return None
        where = {"pid": pid}
    else:
        if row is None or not row.session_id:
            return None
        where = {"session_id": row.session_id}
    return lambda timeout: turns.wait_ready(**where, timeout=timeout)


def _start_deliverer(launch: Launch, pid: int) -> Started | None:
    try:
        first.start_deliverer(launch.request_id, pid=pid, env=launch.deliverer_env)
    except OSError as error:
        return fail(launch.conn, launch.request_id, NO_DELIVERER,
                       f"the first turn's deliverer could not be started: {error}")
    return None


# -- the sweep --------------------------------------------------------------------------

@dataclass(frozen=True)
class Swept:
    """What one sweep did: the run files it removed and the run logs it cut."""

    removed: tuple[str, ...] = ()
    cut: tuple[str, ...] = ()


def sweep(conn: sqlite3.Connection | None = None, *, env: Mapping[str, str] | None = None,
          keep_days: float | None = None, cap: int = LOG_CAP,
          now: float | None = None) -> Swept:
    """Remove the run files older than `keep_days` (`runs.keep_days`) and cut each run log
    past `cap` bytes from the front, keeping its newest lines. A file whose launch is
    still running (its row's process alive, by pid and start time) is left alone. A file
    that can't be read or removed is left for the next sweep."""
    folder = runs_dir(env)
    if not folder.is_dir():
        return Swept()
    if keep_days is None:
        from . import settings

        keep_days = float(settings.current()["runs.keep_days"])
    now = time.time() if now is None else now
    removed: list[str] = []
    cut: list[str] = []
    for path in sorted(folder.iterdir()):
        request_id = _request_of(path.name)
        if request_id is None or not path.is_file():
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        old = now - info.st_mtime > keep_days * 86400
        if not old and not (path.name.endswith(".log") and info.st_size > cap):
            continue
        if _running(conn, request_id):
            continue
        try:
            if old:
                path.unlink()
                removed.append(path.name)
            else:
                _cut(path, info.st_size, cap)
                cut.append(path.name)
        except OSError:
            continue
    return Swept(tuple(removed), tuple(cut))


def _request_of(name: str) -> str | None:
    for suffix in RUN_FILES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)]
    return None


def _running(conn: sqlite3.Connection | None, request_id: str) -> bool:
    if conn is None:
        return False
    row = store.get(conn, request_id)
    if row is None or row.pid is None or row.pid_started is None:
        return False
    return proc.alive(proc.ProcId(row.pid, row.pid_started))


def _cut(path: Path, size: int, cap: int) -> None:
    """Keep the newest part of the log at `path`, from a line's start, with the cut line
    first, so the whole fits in `cap` bytes."""
    head = CUT_LINE.format(size=size).encode("utf-8")
    keep = max(cap - len(head) - 32, 0)
    with open(path, "rb") as log:
        log.seek(max(size - keep, 0))
        tail = log.read(keep)
    newline = tail.find(b"\n")
    if 0 <= newline < len(tail) - 1:
        tail = tail[newline + 1:]
    head = CUT_LINE.format(size=size - len(tail)).encode("utf-8")
    files.atomic_write(path, head + tail, mode=0o600)


__all__ = ["CUT_LINE", "FAILED_ERRORS", "LOG_CAP", "NO_DELIVERER", "OTHER_TARGETS", "RUNS",
           "RUN_FILES", "SPAWN_FAILED", "TARGETS", "TRY_AGAIN", "Headless", "Launch",
           "NoCommand", "NoSlots", "Slot", "SlotTaken", "SlotsBusy",
           "SlotsFailed", "SlotsUnsupported", "Started", "Swept", "Target", "Terminal",
           "exit_of", "fail", "failed_exit", "find", "get", "named_slot",
           "run_log", "runs_dir", "sweep"]
