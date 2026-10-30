"""One verdict per row: `ok`, `warn`, `fail` or `off`, with its reason and fix.

`judge` is the one function that turns what the supervisor says of a row, the row's
on/off and its health into a verdict. The list, `show`, `watch` and the doctor all show
what it returns, so a row reads the same everywhere.

The rule:

- A timer is judged by whether its schedule is armed against its on/off, and by its last
  result; never by whether a process runs. Idle between runs is `ok`; a last run that
  failed is `warn`; a gate run line `off` (the run held by its switch) is no failure.
- A state is `fail` only for a daemon that is on and not running with no stop recorded,
  a timer that is on whose schedule is not armed, and a unit that is missing while on.
- A value the supervisor can't give is `unknown`, with the reason, and shows as `warn`.
- A running daemon whose declared health check fails is `fail`, with the check's first
  line: a check the user declared and the doctor read, not inferred.

The first case that matches gives the verdict:

    a row of another system; no supervisor          off
    being removed                                   warn, remove not finished
    a module unit whose switch is off               off
    the state can't be read                         warn, unknown
    missing                                         on: fail; off: warn
    an adopted Linux timer whose drop-in is gone    warn, not wrapped
    a daemon that is on                             running: ok; else a recorded stop
                                                    warn, none fail "not running"
    a daemon that is off                            running or starting at login: warn;
                                                    else off
    a timer that is on                              not armed: fail; last run failed:
                                                    warn; else ok
    a timer that is off                             a module or adopted Linux timer: off
                                                    (its gate holds the runs); else armed:
                                                    warn, not armed: off

A user daemon's stop by hand is recorded; one older than the machine's last boot is
ignored, since the daemon started again at login after it. A module daemon's stop is not
recorded, so a module daemon that is on and not running is `fail`, "not running".
"""

from __future__ import annotations

import calendar
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from moxi import units

from . import state
from .rows import ModuleRow, UserRow

Row = UserRow | ModuleRow

#: The verdicts, best first.
STATES = ("ok", "off", "warn", "fail")
#: Task Scheduler's own status codes (0x41300 to 0x4131F, "ready", "running", "has not
#: yet run" and the like), which a task's last result may hold. None is a failure.
_SCHED_STATUS = range(0x41300, 0x41320)


@dataclass(frozen=True)
class Verdict:
    """A row's verdict. `state` is `ok`, `warn`, `fail` or `off`; `unknown` is True when
    the verdict is `warn` because a value could not be read; `reason` says why in a few
    words; `fix` is one command, or where the log is, or None."""

    state: str
    reason: str
    fix: str | None = None
    unknown: bool = False

    def as_dict(self) -> dict[str, object]:
        return {"state": self.state, "reason": self.reason, "fix": self.fix,
                "unknown": self.unknown}


@dataclass(frozen=True)
class LastRun:
    """A timer's last run: `failed`, a few words, and when (as given, or None)."""

    failed: bool
    text: str
    at: str | None


def worst(states: Iterable[str]) -> str:
    """The worst of several verdict states (`ok` when there are none)."""
    found = list(states)
    return max(found, key=STATES.index) if found else "ok"


# --- a timer's last run -----------------------------------------------------------------

def last_run(st: units.UnitState | None) -> LastRun | None:
    """How a timer's last run went: from the gate's newest run line when there is one
    (module, written and adopted Linux timers), else from the supervisor's own last
    result (an adopted timer on macOS or Windows, or a Linux timer whose drop-in is
    gone). None when nothing is known."""
    if st is None or not st.last:
        return None
    line = {k: v for k, v in st.last.items() if k != "supervisor"}
    if line:
        return _gate_line(line)
    supervisor = st.last.get("supervisor")
    if isinstance(supervisor, Mapping):
        return _supervisor_last(supervisor)
    return None


def _gate_line(line: Mapping) -> LastRun:
    at = line.get("at")
    result, code = line.get("result"), line.get("exit")
    if result == "off":
        return LastRun(False, "held off by its switch", at)
    if result == "ran" and code == 0:
        return LastRun(False, "ok", at)
    if result == "ran":
        return LastRun(True, f"exit {code}", at)
    if result == "unreadable":
        return LastRun(True, "its switch's config file could not be read", at)
    if result == "not-started":
        return LastRun(True, "the command did not start", at)
    return LastRun(code not in (0, None), f"{result}, exit {code}", at)


def _supervisor_last(found: Mapping) -> LastRun | None:
    if "last_result" in found:  # a Task Scheduler task
        code = _int(found.get("last_result"))
        at = found.get("last_run")
        if code is None:
            return None
        if code == 0 or code in _SCHED_STATUS:
            return LastRun(False, "ok" if code == 0 else f"result 0x{code:08X}", at)
        return LastRun(True, f"result 0x{code & 0xFFFFFFFF:08X}", at)
    if "result" in found:  # systemd: the timer's service
        result = str(found.get("result") or "")
        status = str(found.get("status") or "")
        if not result and not status:
            return None
        if result in ("", "success") and status in ("", "0"):
            return LastRun(False, "ok", None)
        said = f"exit {status}" if status not in ("", "0") else result
        return LastRun(True, said, None)
    if "status" in found:  # launchd: the last exit status
        code = _int(found.get("status"))
        if code is None:
            return None
        return LastRun(code != 0, "ok" if code == 0 else f"exit {code}", None)
    return None


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


# --- the stop recorded by hand ----------------------------------------------------------

_booted: list[float] = []


def boot_time() -> float | None:
    """When the machine last booted (seconds since the epoch), or None when it can't be
    read."""
    if not _booted:
        try:
            import psutil

            _booted.append(float(psutil.boot_time()))
        except Exception:  # noqa: BLE001 - an unreadable boot time ignores no stop
            return None
    return _booted[0]


def _seconds(utc: str) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(utc, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return None


def stop_recorded(row: Row, booted: float | None) -> str | None:
    """The time of a user daemon's stop by hand that still holds, or None. A stop from
    before the last boot no longer holds: the daemon started again at login since."""
    if not isinstance(row, UserRow) or row.stopped is None:
        return None
    when = _seconds(row.stopped)
    if booted is not None and when is not None and when < booted:
        return None
    return row.stopped


# --- the verdict ------------------------------------------------------------------------

def _cmd(verb: str, row: Row) -> str:
    """`moxi services <verb> <id>`: a user row by its name, a module row by its id."""
    return f"moxi services {verb} {row.name if isinstance(row, UserRow) else row.id}"


def _unknown(reason: str) -> Verdict:
    return Verdict("warn", f"unknown: {reason}", None, True)


def judge(row: Row, st: units.UnitState | None, flag: bool | None, *,
          health: tuple[str, str] | None = None, socket: units.UnitState | None = None,
          supervisor: bool = True, booted: float | None = None,
          unread: str | None = None) -> Verdict:
    """The verdict of one row.

    `st` is what the supervisor said of it (None when the read did not cover it, and
    then `unread` says why), `flag` its on/off (`state.flag`; None when unreadable),
    `health` its health check's `(verdict, line)` when one ran, `socket` a module
    daemon's listening-socket state, `supervisor` False when this system has none, and
    `booted` the last boot time, before which a recorded stop no longer holds."""
    found = _state_verdict(row, st, flag, socket=socket, supervisor=supervisor,
                           booted=booted, unread=unread)
    if health is None or found.state not in ("ok", "warn") or found.unknown:
        return found
    said, line = health
    if said == "fail":
        return Verdict("fail", f"health check failed: {line}",
                       st.log if st is not None else None)
    if said == "warn" and found.state == "ok":
        return Verdict("warn", f"health check: {line}", None)
    return found


def _state_verdict(row: Row, st, flag, *, socket, supervisor, booted, unread) -> Verdict:
    user = isinstance(row, UserRow)
    if user and not row.here:
        return Verdict("off", f"added on {row.platform}")
    if not supervisor or (st is not None and st.state == "nobackend"):
        return Verdict("off", "no supervisor")
    if user and row.removing:
        return Verdict("warn", "remove not finished", _cmd("remove", row))
    if not user and flag is False:
        return Verdict("off", f"its switch {row.switch} is off")
    if flag is None:
        what = (f"its switch {row.switch}" if not user
                else f"its key {state.key(row.name)}")
        return _unknown(f"{what} can't be read")
    if st is None:
        return _unknown(unread or "the supervisor read did not cover it")
    if st.state == "unknown":
        return _unknown(st.detail or "the supervisor did not say")
    if st.state == "off":
        return Verdict("off", st.detail or "not for this system")
    if st.state == "missing":
        return _missing(row, flag)
    if user and state.is_wrapped(row) and st.wrapped is None:
        return Verdict("warn", "not wrapped: runs are neither gated nor recorded",
                       _cmd("enable" if flag else "disable", row))
    if row.kind == "daemon":
        return _daemon(row, st, flag, socket, booted)
    return _timer(row, st, flag)


def _missing(row: Row, flag: bool) -> Verdict:
    if isinstance(row, ModuleRow):
        fix = f"moxi install {row.module}"
    elif row.form == "written":
        fix = _cmd("enable" if flag else "disable", row)
    else:
        fix = _cmd("remove", row)
    what = "its unit is missing" if row.form != "adopted" else f"{row.unit} is missing"
    return Verdict("fail" if flag else "warn", what, fix)


def _daemon(row: Row, st, flag: bool, socket, booted) -> Verdict:
    running = st.state == "active"
    listening = (socket is not None and socket.state == "active" and not running
                 and st.state == "inactive")
    enabled = st.enabled
    if enabled is None and socket is not None:
        enabled = socket.enabled
    if flag:
        if listening:
            return Verdict("ok", "idle, socket listening")
        if not running:
            stopped = stop_recorded(row, booted)
            if stopped is not None:
                return Verdict("warn", f"stopped by hand at {stopped}", _cmd("start", row))
            why = f" (failed: {st.detail})" if st.state == "failed" and st.detail else (
                " (failed)" if st.state == "failed" else "")
            return Verdict("fail", f"not running{why}", _cmd("start", row))
        if enabled is None:
            return _unknown("whether it starts at login can't be read"
                            + (f" ({st.detail})" if st.detail else ""))
        if enabled is False:
            fix = (f"moxi install {row.module}" if isinstance(row, ModuleRow)
                   else _cmd("enable", row))
            return Verdict("warn", "won't start at login", fix)
        return Verdict("ok", "running")
    if running:
        return Verdict("warn", "running, off at next login", _cmd("disable", row))
    if enabled is None:
        return _unknown("whether it starts at login can't be read"
                        + (f" ({st.detail})" if st.detail else ""))
    if enabled:
        return Verdict("warn", "starts at login, flag off", _cmd("disable", row))
    return Verdict("off", "off")


def _timer(row: Row, st, flag: bool) -> Verdict:
    module_or_wrapped = isinstance(row, ModuleRow) or state.is_wrapped(row)
    if not flag:
        if module_or_wrapped:
            return Verdict("off", "off; its gate holds the runs")
        if st.enabled is None:
            return _unknown("whether its schedule is armed can't be read"
                            + (f" ({st.detail})" if st.detail else ""))
        if st.enabled:
            return Verdict("warn", "schedule armed, flag off", _cmd("disable", row))
        return Verdict("off", "off")
    if st.enabled is None:
        return _unknown("whether its schedule is armed can't be read"
                        + (f" ({st.detail})" if st.detail else ""))
    fix = (f"moxi install {row.module}" if isinstance(row, ModuleRow)
           else _cmd("enable", row))
    if st.enabled is False:
        return Verdict("fail", "schedule not armed", fix)
    if st.state == "failed":
        return Verdict("fail", "its schedule failed"
                       + (f" ({st.detail})" if st.detail else ""), st.log or fix)
    last = last_run(st)
    if last is not None and last.failed:
        at = f" at {last.at}" if last.at else ""
        return Verdict("warn", f"last run failed ({last.text}{at})", st.log)
    if last is None:
        return Verdict("ok", "armed, no run yet")
    return Verdict("ok", "armed")


# --- every row at once ------------------------------------------------------------------

@dataclass(frozen=True)
class View:
    """One row as the reads show it: the row, what the supervisor said (`st`, None when
    not read), its on/off (`flag`), its health check's `(verdict, line)` when one ran,
    and its verdict."""

    row: Row
    st: units.UnitState | None
    flag: bool | None
    health: tuple[str, str] | None
    verdict: Verdict


def flags(rows: Sequence[Row]) -> dict[str, bool | None]:
    """Each row's on/off by id, reading each module's settings once."""
    switches: dict[tuple[str, str | None], bool | None] = {}
    out: dict[str, bool | None] = {}
    for row in rows:
        if isinstance(row, ModuleRow):
            key = (row.module, row.switch)
            if key not in switches:
                switches[key] = state.flag(row)
            out[row.id] = switches[key]
        else:
            out[row.id] = state.flag(row)
    return out


def healths(pairs: Sequence[tuple[Row, units.UnitState | None]],
            seconds: float | None = None) -> dict[str, tuple[str, str]]:
    """Every health check that applies, all started at once, each under its own limit;
    by row id. A check still running after `seconds` (default: the health limit and a
    second) is `fail`, "did not finish"."""
    limit = (state.HEALTH_SECONDS + 1.0) if seconds is None else seconds
    out: dict[str, tuple[str, str]] = {}
    threads: list[tuple[str, threading.Thread]] = []
    lock = threading.Lock()

    def run(row: Row, st) -> None:
        try:
            found = state.health(row, st)
        except Exception as exc:  # noqa: BLE001 - a check that breaks is a failing check
            found = ("fail", f"the check could not run: {exc}")
        if found is not None:
            with lock:
                out[row.id] = found

    for row, st in pairs:
        if row.kind != "daemon" or st is None or st.state != "active":
            continue
        if isinstance(row, UserRow) and not row.health:
            continue
        thread = threading.Thread(target=run, args=(row, st), daemon=True,
                                  name=f"services health {row.id}")
        thread.start()
        threads.append((row.id, thread))
    deadline = time.monotonic() + limit
    for ident, thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    with lock:
        for ident, thread in threads:
            if thread.is_alive() and ident not in out:
                out[ident] = ("fail", f"did not finish in {state.HEALTH_SECONDS:g} s")
        return dict(out)


def within(fn: Callable[[], object], seconds: float):
    """`fn()` in a worker thread, waited on for `seconds`: `(True, value)`, or
    `(False, None)` when it did not return in time. What it raises is raised here. A
    thread that overran is left to finish on its own."""
    box: list = []

    def work():
        try:
            box.append((True, fn()))
        except BaseException as exc:  # handed back to the caller
            box.append((False, exc))

    thread = threading.Thread(target=work, daemon=True, name="services status")
    thread.start()
    thread.join(seconds)
    if not box:
        return False, None
    ok, value = box[0]
    if not ok:
        raise value
    return True, value


def views(rows: Sequence[Row], *, health: bool = False,
          status_seconds: float | None = None,
          health_seconds: float | None = None,
          known_health: Mapping[str, tuple[str, str]] | None = None) -> list[View]:
    """Every row's view, from one supervisor read. With `health`, every running
    daemon's check runs, all at once, waited on for `health_seconds` (see `healths`);
    `known_health` gives results of an earlier run to judge with instead. With
    `status_seconds`, the read gets that long in a worker thread; past it every row read
    is `unknown` and no health runs."""
    rows = list(rows)
    supervisor = units.backend() is not None
    unread = None
    if status_seconds is None:
        states, sockets = state.read_with_sockets(rows)
    else:
        done, value = within(lambda: state.read_with_sockets(rows), status_seconds)
        if done:
            states, sockets = value
        else:
            states, sockets = {r.id: None for r in rows}, {}
            unread = f"the supervisor did not answer in {status_seconds:g} s"
            health = False
    on = flags(rows)
    if health:
        checked = healths([(r, states.get(r.id)) for r in rows], health_seconds)
    elif known_health is not None and unread is None:
        checked = {r.id: known_health[r.id] for r in rows if r.id in known_health
                   and (st := states.get(r.id)) is not None and st.state == "active"}
    else:
        checked = {}
    booted = boot_time()
    out = []
    for row in rows:
        st = states.get(row.id)
        verdict = judge(row, st, on[row.id], health=checked.get(row.id),
                        socket=sockets.get(row.id), supervisor=supervisor, booted=booted,
                        unread=unread)
        out.append(View(row, st, on[row.id], checked.get(row.id), verdict))
    return out


__all__ = ["STATES", "Verdict", "LastRun", "View", "worst", "last_run", "boot_time",
           "stop_recorded", "judge", "flags", "healths", "within", "views"]
