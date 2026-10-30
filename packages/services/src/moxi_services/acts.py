"""The acts on rows: start, stop, restart, enable, disable, add and remove.

Each act takes one row id (`<module>/<name>`, `services/<name>` or a bare `<name>`), or
for `add` the new row, and returns one `Outcome`: the exit code (0 done; 1 an act or a
write failed, or the state read after it does not match; 2 asked wrongly; 3 refused; 75
the store or the core lock stayed busy), the lines to print and the fix. It never prints.

What each act calls, by the row's form (services builds no supervisor command; the core's
writer, `moxi.units`, does the rest):

- a module row M/N: `units.<act>(M, [N])` to start, stop or restart it;
  `moxi.config.set(M, <its switch>, ...)` to enable or disable it (refused without a
  switch); add and remove are refused, since it comes and goes with its module.
- a written row N: `units.<act>("services", [N])`; enable and disable set the store's
  flag, then `apply(plan("services", only=[N]))`; add writes the row, then its unit;
  remove takes the unit, then the row.
- an adopted row N of unit REF: `units.<act>("services", [REF], foreign=True)`, enable
  and disable included, after the flag; an adopted Linux timer's on/off is its key
  `adopted.N` in services' config instead. add records the row and makes the unit match
  it; remove forgets the row and leaves the unit as it was.

Every act on a row terminates in every state. The rules that make it so:

- An act that changes the store takes the core lock first (`moxi.files.lock(
  moxi.paths.core_lock(), nested=True)`, so the core's own calls inside it yield to it),
  and holds it around the write and the supervisor call that goes with it. Past its
  timeout nothing is written and the act exits 75. No store transaction is held across a
  supervisor call: each write is one short transaction.
- `add` deletes its own row whenever the core will not write the unit, so no row
  outlives a refused add. `remove` deletes the row whenever no unit or drop-in of it
  remains, or none can exist here (a row of another system, no supervisor, an editable
  install); the row stays, marked as being removed, only while a unit or drop-in the
  supervisor failed to take away remains, and `remove` again retries.
- A row of another system, a row being removed, and every row while there is no
  supervisor allow `remove` and the reads, nothing else.
- After each act the supervisor is read again: a daemon's start and restart must read
  `active` and its stop must not; enable and disable must read the schedule or login
  start matching the flag (a daemon also running or not, an adopted Linux timer its
  key). A timer's start, stop and restart are judged by what the core's act returned,
  since a run may end before any read. A value the supervisor can't give is printed
  with its reason and does not fail the act.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

import moxi.config
import moxi.exit
import moxi.files
import moxi.paths
from moxi import units

from . import projects_link, rows, state, store, verdict
from .errors import Busy, Refused, ServicesError, Usage
from .rows import MODULE, ModuleRow, UserRow

#: Who a change is recorded as made by, unless the caller says otherwise.
BY = "moxi services"
#: How long an act waits for the core lock (an install, or another act), in seconds.
LOCK_TIMEOUT = 30.0
#: The kinds of plan the core may make for a unit that it will write.
_WRITES = ("add", "change", "keep")

Row = UserRow | ModuleRow


@dataclass(frozen=True)
class Outcome:
    """What one act did on one row: `code` is the exit code, `lines` what to print (the
    core's and the supervisor's lines verbatim among them), `fix` the one command or step
    that fixes a failure, or None."""

    id: str
    act: str
    code: int
    lines: tuple[str, ...] = ()
    fix: str | None = None
    #: Lines the core printed itself while the act ran (a module daemon switch's units
    #: applied by `moxi.config.set`), kept apart so a verb does not print them twice.
    said: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.code == moxi.exit.OK


class _Lines(list):
    """An act's lines, and apart from them (`said`) the lines the core printed itself."""

    def __init__(self):
        super().__init__()
        self.said: list[str] = []


class _Done(Exception):
    """Ends an act early with its outcome's code, lines and fix."""

    def __init__(self, code: int, lines: Sequence[str], fix: str | None = None):
        super().__init__(lines[0] if lines else "")
        self.code = code
        self.lines = tuple(lines)
        self.fix = fix


def _outcome(act: str) -> Callable:
    """Wrap an act so that every way it ends is an `Outcome`: a `ServicesError` gives
    its own exit code, reason and fix; a `moxi.config.ConfigError` is a refusal (exit 3)
    with its text, or 75 when the config file stayed locked."""

    def decorate(fn: Callable) -> Callable:
        @wraps(fn)
        def run(target, *args, **kwargs) -> Outcome:
            ident = _ident(target)
            lines = _Lines()
            try:
                code, fix = fn(target, *args, lines=lines, **kwargs)
            except _Done as done:
                return Outcome(ident, act, done.code, tuple(lines) + done.lines, done.fix,
                               tuple(lines.said))
            except ServicesError as exc:
                return Outcome(ident, act, exc.exit_code, (*lines, exc.reason), exc.fix,
                               tuple(lines.said))
            except moxi.config.ConfigError as exc:
                code = moxi.exit.TEMPFAIL if exc.exit_code == moxi.exit.TEMPFAIL \
                    else moxi.exit.REFUSED
                return Outcome(ident, act, code, (*lines, str(exc)), exc.fix,
                               tuple(lines.said))
            return Outcome(ident, act, code, tuple(lines), fix, tuple(lines.said))
        return run
    return decorate


def _ident(target) -> str:
    if isinstance(target, str):
        return target if "/" in target else rows.row_id(MODULE, target)
    return getattr(target, "id", str(target))


# --- finding a row ----------------------------------------------------------------------

def resolve(ident: str) -> Row:
    """The row an id names: a user row from the store, or a module row from the
    installed modules' declarations. An unknown id is `Refused` (exit 3)."""
    module, name = rows.parse_id(ident)
    if rows.is_user(module):
        row = store.get(name)
        if row is None:
            raise Refused(f"no service is named {name}",
                          "`moxi services list` shows every row's id")
        return row
    found, _ = rows.module_rows([module])
    for row in found:
        if row.name == name:
            return row
    raise Refused(f"{module}/{name} is no daemon or timer of an installed module",
                  "`moxi services list` shows every row's id")


def _refuse_unless_actable(row: Row) -> None:
    """Refuse (exit 3) every act but `remove` and the reads on a row being removed, a
    row of another system, and any row while there is no supervisor."""
    if isinstance(row, UserRow):
        if not row.here:
            raise Refused(f"{row.id} was added on {row.platform}; on this system only "
                          "the reads and remove work",
                          f"moxi services remove {row.name}")
        if row.removing:
            raise Refused(f"{row.id}: remove not finished",
                          f"moxi services remove {row.name}")
    if units.backend() is None:
        raise Refused(f"{row.id}: no supervisor on this system, so there is nothing to "
                      "act on; only the reads and remove work", None)


# --- the core lock ----------------------------------------------------------------------

@contextmanager
def _locked() -> Iterator[None]:
    """The core lock, waited for `LOCK_TIMEOUT` seconds; then `Busy` (exit 75), with
    nothing written."""
    stack = ExitStack()
    try:
        stack.enter_context(moxi.files.lock(moxi.paths.core_lock(), timeout=LOCK_TIMEOUT,
                                            nested=True))
    except moxi.files.LockTimeout as exc:
        who = f"pid {exc.holder}" if exc.holder else "another process"
        raise Busy(f"the core lock stayed held by {who} (an install, or another act) "
                   f"past {LOCK_TIMEOUT:g} s; nothing was changed",
                   str(exc.path)) from None
    with stack:
        yield


# --- reading again after an act ---------------------------------------------------------

def _unknown(st: units.UnitState | None) -> str | None:
    """The reason a state can't be judged, or None when it can."""
    if st is None:
        return "the supervisor read did not cover it"
    if st.state in ("unknown", "nobackend"):
        return st.detail or st.state
    return None


def _log_fix(st: units.UnitState | None) -> str | None:
    return f"its log: {st.log}" if st is not None and st.log else None


def _check_running(row: Row, want: bool, lines: list[str]) -> int:
    """A daemon's re-read after start, restart or stop: `active` exactly when `want`."""
    st = state.read_one(row)
    why = _unknown(st)
    if why is not None:
        lines.append(f"{row.id}: unknown after the act: {why}")
        return moxi.exit.OK
    running = st.state == "active"
    if running == want:
        return moxi.exit.OK
    detail = f" ({st.detail})" if st.detail else ""
    lines.append(f"{row.id} reads {st.state}{detail}, not "
                 f"{'running' if want else 'stopped'}")
    raise _Done(moxi.exit.ERROR, (), _log_fix(st))


def _check_enabled(row: Row, on: bool, lines: list[str], *, wrapped: bool = False) -> int:
    """The re-read after enable or disable (and after add): the schedule or the start at
    login matches the flag, a daemon is running or not to match, an adopted Linux timer
    is wrapped and its key matches."""
    st = state.read_one(row)
    why = _unknown(st)
    if why is not None:
        lines.append(f"{row.id}: unknown after the act: {why}")
        return moxi.exit.OK
    wrong: list[str] = []
    if st.state == "missing":
        wrong.append("has no unit")
    elif wrapped:
        if st.wrapped is None:
            wrong.append("is not wrapped")
        key_on = state.flag(row)
        if key_on is None:
            lines.append(f"{row.id}: its key {state.key(row.name)} can't be read")
        elif key_on != on:
            wrong.append(f"its key {state.key(row.name)} is not {_word(on)}")
        if on and st.enabled is False:
            wrong.append("its schedule is not armed")
    else:
        if st.enabled is None:
            lines.append(f"{row.id}: whether it is enabled is unknown"
                         + (f": {st.detail}" if st.detail else ""))
        elif st.enabled != on:
            wrong.append(f"reads {'disabled' if on else 'enabled'}")
        if row.kind == "daemon" and (st.state == "active") != on:
            wrong.append(f"reads {st.state}, not {'running' if on else 'stopped'}")
    if not wrong:
        return moxi.exit.OK
    lines.append(f"{row.id} {', and '.join(wrong)}")
    raise _Done(moxi.exit.ERROR, (), _log_fix(st))


def _word(on: bool) -> str:
    return "on" if on else "off"


def _acted(results: Sequence[units.Acted], lines: list[str]) -> bool:
    ok = True
    for acted in results:
        lines.extend(acted.lines)
        ok = ok and acted.ok
    return ok


def _applied(results: Sequence[units.Applied], lines: list[str]) -> bool:
    ok = True
    for applied in results:
        lines.extend(applied.lines)
        ok = ok and applied.ok
    return ok


def _quiet(line: str) -> None:
    """The core's `say`: its lines are in each `Applied` already."""


# --- start, stop, restart ---------------------------------------------------------------

def start(ident: str, *, by: str = BY) -> Outcome:
    """Start a row: a daemon now, a timer's run now."""
    return _run("start", ident, by=by)


def stop(ident: str, *, by: str = BY) -> Outcome:
    """Stop a row: a daemon until the next login or start, a timer's run in progress. No
    flag changes; a user daemon's stop is recorded."""
    return _run("stop", ident, by=by)


def restart(ident: str, *, by: str = BY) -> Outcome:
    """Restart a row: stop, then start; a timer's run ended, then run once."""
    return _run("restart", ident, by=by)


def _run(act: str, ident: str, *, by: str) -> Outcome:
    return _outcome(act)(_run_body)(ident, act, by=by)


def _run_body(ident: str, act: str, *, by: str, lines: list[str]):
    row = resolve(ident)
    _refuse_unless_actable(row)
    call = getattr(units, act)
    if isinstance(row, ModuleRow):
        ok = _acted(call(row.module, [row.name]), lines)
    else:
        with _locked():
            if row.kind == "daemon":
                # A stop by hand is recorded; start and restart clear it.
                when = store.now() if act == "stop" else None
                if row.stopped != when:
                    store.write(lambda c: store.set_stopped(c, row.name, when, by=by))
            if row.form == "written":
                ok = _acted(call(MODULE, [row.name]), lines)
            else:
                ok = _acted(call(MODULE, [row.unit], foreign=True), lines)
    if not ok:
        return moxi.exit.ERROR, None
    if row.kind == "daemon":
        return _check_running(row, act != "stop", lines), None
    if act != "stop":
        _note_gate_off(row, lines)
    return moxi.exit.OK, None


def _note_gate_off(row: Row, lines: list[str]) -> None:
    """A module or adopted Linux timer runs now through its gate; with its switch or key
    off the gate records the run `off` and runs nothing. Say so."""
    if isinstance(row, ModuleRow):
        if row.switch is not None and state.switch_on(row.module, row.switch) is False:
            lines.append(f"{row.id}: its switch {row.switch} is off, so the run is "
                         "recorded off and nothing ran; turn it on with: "
                         f"moxi services enable {row.id}")
    elif state.is_wrapped(row) and state.flag(row) is False:
        lines.append(f"{row.id}: its key {state.key(row.name)} is off, so the run is "
                     "recorded off and nothing ran; turn it on with: "
                     f"moxi services enable {row.name}")


# --- enable, disable --------------------------------------------------------------------

def enable(ident: str, *, by: str = BY) -> Outcome:
    """Turn a row on: a daemon enabled and running, a timer's schedule armed, when it
    returns. A module row through its declared switch."""
    return _outcome("enable")(_switch_body)(ident, True, by=by)


def disable(ident: str, *, by: str = BY) -> Outcome:
    """Turn a row off: a daemon disabled and stopped, a timer's schedule off (a run in
    progress finishes) or its runs held by the gate. A module row through its switch."""
    return _outcome("disable")(_switch_body)(ident, False, by=by)


def _switch_body(ident: str, on: bool, *, by: str, lines: list[str]):
    row = resolve(ident)
    _refuse_unless_actable(row)
    if isinstance(row, ModuleRow):
        return _switch_module(row, on, by, lines)
    if row.form == "written":
        return _switch_written(row, on, by, lines)
    if state.is_wrapped(row):
        return _switch_wrapped(row, on, by, lines)
    return _switch_adopted(row, on, by, lines)


def _switch_module(row: ModuleRow, on: bool, by: str, lines: list[str]):
    """A module unit is turned on and off only through the switch its module declares;
    one that declares none is refused, never forced."""
    if row.switch is None:
        raise Refused(f"{row.id} declares no switch, so it has no on and off of its own",
                      f"stop it with `moxi services stop {row.id}`, or remove its module "
                      f"with `moxi uninstall {row.module}`")
    applied = moxi.config.set(row.module, row.switch, on, by=by)
    lines.append(f"{row.module} {row.switch} set to {str(on).lower()}")
    # `moxi.config.set` prints the lines of a daemon switch's units as it applies them.
    if not _applied(applied, lines.said):
        return moxi.exit.ERROR, f"moxi install {row.module}"
    return moxi.exit.OK, None


def _set_flag(row: UserRow, on: bool, by: str) -> None:
    def change(conn):
        store.set_enabled(conn, row.name, on, by=by)
        if on and row.stopped is not None:
            store.set_stopped(conn, row.name, None, by=by)
    store.write(change)


def _switch_written(row: UserRow, on: bool, by: str, lines: list[str]):
    with _locked():
        _set_flag(row, on, by)
        changes = units.plan(MODULE, only=[row.name])
        code = _plan_refused(changes, row.name, lines)
        if code is not None:
            return code, None
        if not _applied(units.apply(changes, say=_quiet), lines):
            return moxi.exit.ERROR, f"moxi services {'enable' if on else 'disable'} {row.name}"
    return _check_enabled(row, on, lines), None


def _switch_adopted(row: UserRow, on: bool, by: str, lines: list[str]):
    with _locked():
        _set_flag(row, on, by)
        call = units.enable if on else units.disable
        ok = _acted(call(MODULE, [row.unit], foreign=True), lines)
    if not ok:
        return moxi.exit.ERROR, f"moxi services {'enable' if on else 'disable'} {row.name}"
    return _check_enabled(row, on, lines), None


def _switch_wrapped(row: UserRow, on: bool, by: str, lines: list[str]):
    """An adopted Linux timer: wrapped again when its drop-in is gone, then its key set;
    `enable` also arms the schedule when it isn't."""
    retry = f"moxi services {'enable' if on else 'disable'} {row.name}"
    with _locked():
        _set_flag(row, on, by)
        if row.unit not in units.wraps(MODULE):
            if not _wrap(row, lines):
                return moxi.exit.ERROR, retry
        if not _set_key(row, on, by, lines):
            return moxi.exit.ERROR, retry
        if on:
            st = state.read_one(row)
            if st is not None and st.enabled is False:
                if not _acted(units.enable(MODULE, [row.unit], foreign=True), lines):
                    return moxi.exit.ERROR, retry
    return _check_enabled(row, on, lines, wrapped=True), None


def _set_key(row: UserRow, on: bool, by: str, lines: list[str]) -> bool:
    """Set an adopted Linux timer's key; a config file that refuses it is a failure of
    the act (exit 1), since the row is already written."""
    try:
        moxi.config.set(MODULE, state.key(row.name), on, by=by)
    except moxi.config.ConfigError as exc:
        lines.append(str(exc))
        return False
    lines.append(f"{row.unit}: runs {_word(on)} ({MODULE} {state.key(row.name)} = "
                 f"{str(on).lower()})")
    return True


def _wrap(row: UserRow, lines: list[str]) -> bool:
    """Wrap an adopted Linux timer. Every caller sets its key right after, and says how
    the key now stands, so the core's last line on a success (how the key stood before
    it was set) is left out."""
    acted = units.wrap(row.unit, module=MODULE, key=state.key(row.name), name=row.name)
    lines.extend(acted.lines[:-1] if acted.ok and acted.lines else acted.lines)
    return acted.ok


def _plan_refused(changes: Sequence[units.UnitChange], name: str,
                  lines: list[str]) -> int | None:
    """None when the core plans to write `name`'s unit; else its reason in `lines` and
    the exit code: 3 for a refusal, 1 for a skip or no plan at all."""
    mine = [c for c in changes if c.name == name and c.kind != "socket"]
    if mine and all(c.action in _WRITES for c in mine):
        return None
    if not mine:
        lines.append(f"{rows.row_id(MODULE, name)}: the core planned no unit for it")
        return moxi.exit.ERROR
    for change in mine:
        if change.action not in _WRITES:
            lines.append(f"{change.unit}: {change.reason}")
    if any(c.action == "refuse" for c in mine):
        return moxi.exit.REFUSED
    return moxi.exit.ERROR


# --- add --------------------------------------------------------------------------------

def add_written(name: str, argv: Sequence[str], *, timer: bool = False,
                every: str | None = None, at: Sequence[str] | None = None,
                days: Sequence[str] | None = None, cwd: str | None = None,
                health: Sequence[str] | None = None, project: str | None = None,
                launches_session: bool = False, doc: str = "", enable: bool = False,
                resolve_argv: bool = True, by: str = BY) -> Outcome:
    """Add a written row, then its unit: `argv` run as a daemon, or with `timer` on the
    schedule `every` or `at` (and `days`). It starts off unless `enable`.

    `resolve_argv` finds `argv[0]` on PATH (`.exe` on Windows) and stores its absolute
    path, refused (exit 3) when it is not found; without it `argv[0]` must already be
    absolute. A project key is checked through projects and stored as projects spells it.
    Whenever the core will not write the unit, the row is deleted again."""
    return _outcome("add")(_add_written)(
        name, argv, timer=timer, every=every, at=at, days=days, cwd=cwd, health=health,
        project=project, launches_session=launches_session, doc=doc, enable=enable,
        resolve_argv=resolve_argv, by=by)


def _add_written(name, argv, *, timer, every, at, days, cwd, health, project,
                 launches_session, doc, enable, resolve_argv, by, lines):
    rows.check_name(name)
    argv = list(argv)
    if not argv or not isinstance(argv[0], str) or not argv[0]:
        raise Usage("give the command to run after --", None)
    if resolve_argv:
        found = shutil.which(argv[0])
        if found is None:
            where = "" if os.path.dirname(argv[0]) else " on PATH"
            raise Refused(f"{argv[0]} is not found{where}, or is not executable",
                          "give the absolute path of a program that runs")
        argv[0] = os.path.abspath(found)
    fields = dict(name=name, form="written", kind="timer" if timer else "daemon",
                  argv=argv, cwd=cwd, every=every, at=_list(at), days=_list(days),
                  health=_list(health), project=project, platform=sys.platform, doc=doc,
                  launches_session=launches_session)
    rows.check_fields(fields)
    if project is not None:
        fields["project"] = projects_link.lookup(project)
    if units.backend() is None:
        raise Refused("no supervisor on this system, so a service can't be added here",
                      None)
    with _locked():
        store.write(lambda c: store.insert(c, enabled=enable, by=by, **fields))
        try:
            changes = units.plan(MODULE, only=[name])
        except Exception as exc:  # noqa: BLE001 - any failure to plan deletes the row
            _forget(name)
            lines.append(f"{rows.row_id(MODULE, name)}: the core could not plan its unit: "
                         f"{exc}")
            return moxi.exit.ERROR, None
        code = _plan_refused(changes, name, lines)
        if code is not None:
            _forget(name)
            lines.append(f"{rows.row_id(MODULE, name)} not added")
            return code, None
        if not _applied(units.apply(changes, say=_quiet), lines):
            # Its unit may be partly written: the row stays, for remove, enable or
            # disable to finish.
            return moxi.exit.ERROR, f"moxi services remove {name}"
    lines.append(f"{rows.row_id(MODULE, name)} added, {_word(enable)}")
    row = store.get(name)
    return _check_enabled(row, enable, lines), None


def _list(value) -> list | None:
    return None if value is None else list(value)


def _forget(name: str) -> None:
    store.write(lambda c: store.delete(c, name))


def add_adopted(name: str, ref: str, *, enable: bool | None, timer: bool = False,
                health: Sequence[str] | None = None, project: str | None = None,
                launches_session: bool = False, doc: str = "", by: str = BY) -> Outcome:
    """Adopt foreign unit `ref` as row `name`, recording `enable` as what is true at the
    supervisor and making the unit match it. Its own file is never written; a Linux timer
    is wrapped by the core's drop-in, or not adopted.

    `enable` None is asked wrongly (exit 2), with what the supervisor reads now. A unit
    that is missing, whose on/off can't be read, in moxi's own namespace, or already held
    by another row is refused (exit 3)."""
    return _outcome("add")(_add_adopted)(
        name, ref, enable=enable, timer=timer, health=health, project=project,
        launches_session=launches_session, doc=doc, by=by)


def _add_adopted(name, ref, *, enable, timer, health, project, launches_session, doc,
                 by, lines):
    rows.check_name(name)
    backend = units.backend()
    if backend is None:
        raise Refused("no supervisor on this system, so there is no unit to adopt", None)
    kind = rows.check_ref(ref, backend, timer=timer)
    fields = dict(name=name, form="adopted", kind=kind, unit=ref, health=_list(health),
                  project=project, platform=sys.platform, doc=doc,
                  launches_session=launches_session)
    rows.check_fields(fields)
    _held(name, ref)
    if backend == "launchd":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{ref}.plist"
        if not plist.is_file():
            raise Refused(f"{ref} has no plist in ~/Library/LaunchAgents; services adopts "
                          "only the user's own agents", None)
    st = units.status([], foreign=[ref])
    st = st[0] if st else None
    _adoptable(ref, kind, st, enable)
    if project is not None:
        fields["project"] = projects_link.lookup(project)
    probe = UserRow(**{**dict.fromkeys(UserRow._fields), **fields, "enabled": enable,
                       "removing": False})
    wrapped = state.is_wrapped(probe) and backend == "systemd"
    retry = f"moxi services {'enable' if enable else 'disable'} {name}"
    with _locked():
        store.write(lambda c: store.insert(c, enabled=enable, by=by, **fields))
        if wrapped:
            if not _wrap(probe, lines):
                return _wrap_refused(probe, lines), None
            if not _set_key(probe, enable, by, lines):
                return moxi.exit.ERROR, retry
            if enable and st.enabled is False:
                if not _acted(units.enable(MODULE, [ref], foreign=True), lines):
                    return moxi.exit.ERROR, retry
        else:
            call = units.enable if enable else units.disable
            if not _acted(call(MODULE, [ref], foreign=True), lines):
                return moxi.exit.ERROR, retry
    lines.append(f"{rows.row_id(MODULE, name)} adopts {ref}, {_word(enable)}")
    return _check_enabled(store.get(name), enable, lines, wrapped=wrapped), None


def _held(name: str, ref: str) -> None:
    """Refuse a unit another row already holds: one adopted by another row, or a Linux
    timer another module or row has wrapped."""
    for other in store.read_rows():
        if other.form == "adopted" and other.unit == ref and other.name != name:
            raise Refused(f"{ref} is already adopted as {other.id}",
                          f"`moxi services show {other.name}` shows it")
    wrapped = units.wraps(MODULE).get(ref)
    if wrapped is not None:
        raise Refused(f"{ref} is already wrapped for services/{wrapped.name}",
                      f"moxi services remove {wrapped.name}")


def _adoptable(ref: str, kind: str, st: units.UnitState | None,
               enable: bool | None) -> None:
    """Refuse a unit services can't record truly; without `--enable` or `--disable`,
    say what the supervisor reads now."""
    if st is None or st.state in ("missing", "nobackend"):
        why = f": {st.detail}" if st is not None and st.detail else ""
        raise Refused(f"{ref} is not a unit the supervisor knows{why}", None)
    if st.state == "unknown":
        raise Refused(f"{ref} can't be read: {st.detail or 'the supervisor did not say'}",
                      None)
    if st.wrapped is not None:
        raise Refused(f"{ref} is already wrapped by {st.wrapped.module} for "
                      f"{st.wrapped.module}/{st.wrapped.name}", None)
    if st.enabled is None:
        raise Refused(f"{ref}: whether it is on can't be read"
                      + (f" ({st.detail})" if st.detail else "")
                      + ", so a row could not record it", None)
    if enable is None:
        raise Usage(f"{ref}: {_reads(kind, st)}; adopt it with --enable to keep it on, "
                    "or --disable to turn it off", None)


def _reads(kind: str, st: units.UnitState) -> str:
    """What the supervisor reads of a unit, in a few words."""
    if kind == "timer":
        said = "schedule armed" if st.enabled else "schedule off"
        last = verdict.last_run(st)
        if last is None:
            return f"{said}, no run known"
        return f"{said}, last run {'failed, ' if last.failed else ''}{last.text}"
    said = "starts at login" if st.enabled else "does not start at login"
    return f"{said}, {'running' if st.state == 'active' else st.state}"


def _wrap_refused(row: UserRow, lines: list[str]) -> int:
    """After a wrap that did not succeed: the row is deleted. When no drop-in was left,
    the core refused it (exit 3); when one was, it is taken away again (exit 1)."""
    if row.unit in units.wraps(MODULE):
        _acted([units.unwrap(row.unit, module=MODULE)], lines)
        if row.unit in units.wraps(MODULE):
            # A drop-in that can't be taken away keeps the row, so remove can retry.
            store.write(lambda c: store.set_removing(c, row.name, by=BY))
            lines.append(f"{row.id}: its drop-in is still there")
            raise _Done(moxi.exit.ERROR, (), f"moxi services remove {row.name}")
        _forget(row.name)
        lines.append(f"{row.id} not added")
        return moxi.exit.ERROR
    _forget(row.name)
    lines.append(f"{row.id} not added")
    return moxi.exit.REFUSED


# --- remove -----------------------------------------------------------------------------

def remove(ident: str, *, by: str = BY) -> Outcome:
    """Remove a user row: a written row's unit, then the row; an adopted row with its unit
    left as it was (a Linux timer unwrapped, and disabled first when its key was off,
    since without the gate it would run). A module row is refused: it goes with its
    module."""
    return _outcome("remove")(_remove_body)(ident, by=by)


def _remove_body(ident: str, *, by: str, lines: list[str]):
    row = resolve(ident)
    if isinstance(row, ModuleRow):
        raise Refused(f"{row.id} comes with its module", f"moxi uninstall {row.module}")
    if row.form == "written":
        return _remove_written(row, by, lines)
    if state.is_wrapped(row):
        return _remove_wrapped(row, by, lines)
    _forget(row.name)
    lines.append(f"{row.id} removed; {row.unit} is left as it is")
    return moxi.exit.OK, None


def _remove_written(row: UserRow, by: str, lines: list[str]):
    with _locked():
        store.write(lambda c: store.set_removing(c, row.name, by=by))
        why = _no_unit_here(row)
        if why is not None:
            _forget(row.name)
            lines.append(f"{row.id} removed: {why}")
            return moxi.exit.OK, None
        try:
            changes = units.plan(MODULE, only=[row.name])
        except Exception as exc:  # noqa: BLE001 - the row stays, marked, for a retry
            lines.append(f"{row.id}: the core could not plan its removal: {exc}")
            return moxi.exit.ERROR, f"moxi services remove {row.name}"
        mine = [c for c in changes if c.name == row.name]
        if any(c.action == "refuse" and c.cause == "editable" for c in mine):
            _forget(row.name)
            lines.append(f"{row.id} removed: this is an editable install, where no unit "
                         "is written")
            return moxi.exit.OK, None
        _applied(units.apply(changes, say=_quiet), lines)
    st = state.read_one(row)
    if st is None or st.state == "missing":
        _forget(row.name)
        lines.append(f"{row.id} removed")
        return moxi.exit.OK, None
    detail = f" ({st.detail})" if st.detail else ""
    lines.append(f"{row.id}: its unit is still there, {st.state}{detail}; the row stays "
                 "until it goes")
    return moxi.exit.ERROR, f"moxi services remove {row.name}"


def _no_unit_here(row: UserRow) -> str | None:
    """Why a row can have no unit here, or None."""
    if not row.here:
        return (f"it was added on {row.platform}; a unit left there is removed by the "
                "next install there")
    if units.backend() is None:
        return "there is no supervisor on this system, so it has no unit here"
    return None


def _remove_wrapped(row: UserRow, by: str, lines: list[str]):
    """An adopted Linux timer: its key off means its schedule is disabled first, since
    without the gate it would run; then the drop-in goes, then the key, then the row."""
    if not row.here or units.backend() != "systemd":
        # No systemd here to reload a drop-in. With its key gone, the gate holds the
        # timer's runs off wherever the drop-in is left.
        moxi.config.unset(MODULE, state.key(row.name), by=by)
        _forget(row.name)
        where = row.platform if not row.here else "this system, which has no systemd"
        lines.append(f"{row.id} removed; its drop-in on {row.unit} stays on {where}, and "
                     f"with its key gone the gate holds its runs off there")
        return moxi.exit.OK, None
    retry = f"moxi services remove {row.name}"
    with _locked():
        store.write(lambda c: store.set_removing(c, row.name, by=by))
        # From here the row is marked: a config file that can't be read or written is
        # a failure of the act (exit 1), and remove again retries.
        try:
            key_on = state.key_on(row.name)
        except moxi.config.ConfigError as exc:
            lines.append(str(exc))
            return moxi.exit.ERROR, retry
        if not key_on:
            st = state.read_one(row)
            if st is not None and st.state == "missing":
                # Gone from the supervisor: nothing can run, so there is no schedule to
                # disable, and only the drop-in is left to take away.
                lines.append(f"{row.unit} is gone from the supervisor, so there is no "
                             "schedule to disable")
            else:
                acted = units.disable(MODULE, [row.unit], foreign=True)
                lines.append(f"{row.unit}: its key was off, so its schedule is disabled "
                             "first; without the gate it would run")
                if not _acted(acted, lines):
                    return moxi.exit.ERROR, retry
        _acted([units.unwrap(row.unit, module=MODULE)], lines)
    if row.unit in units.wraps(MODULE):
        lines.append(f"{row.id}: its drop-in on {row.unit} is still there; the row stays "
                     "until it goes")
        return moxi.exit.ERROR, retry
    try:
        moxi.config.unset(MODULE, state.key(row.name), by=by)
    except moxi.config.ConfigError as exc:
        lines.append(str(exc))
        return moxi.exit.ERROR, retry
    _forget(row.name)
    lines.append(f"{row.id} removed; {row.unit} runs as it did before it was adopted")
    return moxi.exit.OK, None


__all__ = [
    "BY", "LOCK_TIMEOUT", "Outcome", "resolve", "start", "stop", "restart", "enable",
    "disable", "add_written", "add_adopted", "remove"]
