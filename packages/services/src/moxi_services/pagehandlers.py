"""The handlers of services' page (`moxi_services.page` declares the routes), and the
poller that feeds its stream.

Each read and act is a plain function, which loopback's base runs off the event loop on
its threads. None calls `moxi.aio.offload` itself: a worker thread has no loop to offload
from.

**Reads** list every row the way `moxi services list --json` does: the user's rows from
the store, then every installed module's daemons and timers, one supervisor read, and each
row's verdict. A read never runs a health check. A running daemon's last health result
comes from the poller's last health pass, the same way `moxi services watch` carries one
between its passes. The supervisor read gets `STATUS_SECONDS`, past which every row is
`unknown`, "the supervisor did not answer". A store that can't be read is a 200 with
`state` `error` and the reason, and any other failure is `error` with a fixed sentence
naming only the exception's class, logged once.

**Acts** each check the request, then hand the act to services' one job thread
(`moxi_services.jobs`) and answer 202 with `/api/status?job=<id>` at once. The thread
runs the act the verb runs (`moxi_services.acts`), recorded as made by the page, and the
status route gives its state, lines and fix.

- A confirmed act's job id is its idempotency key. Within one server's life loopback
  answers a repeat of the key itself. A repeat it has dropped (it keeps the last 1,024)
  finds the job here, for the same act on the same row, and gets the same link, acting
  nothing; for another act or row it is a 422. After a restart, a key is new: stop,
  disable and remove end where the first did (a removed row's id is refused here), and a
  start, run or restart runs again.
- Before the 202, a handler checks only what needs no lock and no supervisor: the row
  exists (an unknown id is a 422 with the fix), a timer's start and restart go through
  `/api/run` and a daemon's never do (422), and an add's fields are in shape. Everything
  else is the act's own refusal, in the job's record.
- `/api/act` is the desk's one plain act: the page mints its job id per press, and a
  repeat of it is answered as a confirmed act's key is. The confirmed routes stay for
  the phone. Every route refuses what the verb refuses, and also an enable or disable
  of a Moxi row (`ON_OFF_IN_SETTINGS`): the page never turns one on or off; its on and
  off is in Settings.
- A stop or restart of slots' server (`slots/server`, by id: no other module declares
  what its daemon's stop ends) is guarded: before the 202 the handler reads through
  slots' API what the stop would end, and goes ahead only when nothing is in use, or
  when the request's `ends`, the count the person was shown, covers what is in use now
  (or the read did not answer and the person confirmed that). Else it is a 422,
  `SLOTS_IN_USE`, or `SLOTS_UNREAD` for a plain press the read couldn't answer: a count
  Moxi couldn't read means the stop may end sessions, never that nothing is in use.
  `/api/ends` answers the same read for the page's warning, its counts null when unread.
- `add` is a plain act: no key, so its handler mints the job id. A repeated add POST is a
  second job, which the thread refuses as the name being taken. A command's first word
  must be a full path, since this server's PATH is the service manager's, not the user's
  shell's. The command line and the health command are split as this system's shell
  splits them.
- A refusal is answered as the verb's exit code says: exit 2 and 3 are a 422 whose text is
  the reason and the fix, and a store locked past the wait (exit 75) is a 409, try again.
- A store that can't be read, or that a newer services wrote, is a 422 with a fixed
  sentence that names no file: the gateway passes a refusal's text to the phone as it
  is, and the store's reason carries its full path. The full text is logged once.

**The status route** reads the job's record; for a job this server holds no record of
(after a restart, or one never sent) it answers `unknown`, "no record of this act; the
row's state shows the outcome".

**The topic.** The stream route's first call starts one poller, an asyncio task, which
runs for the rest of the process's life. Every `PERIOD` seconds it starts a gather: the
supervisor read, and every `HEALTH_PERIOD` seconds a health pass too: every running
daemon's check, `HEALTH_AT_ONCE` at a time, each under services' own health limit. The
bound keeps the server's open files within its budget however many rows have a check;
each running check holds its pipes. A gather runs in a thread
of its own, never on the base's threads, and only one runs at a time: a tick that finds
the last one still running is skipped, and a skipped health pass runs at the next tick
that gathers. The topic is published only when its value changed. The poller is not a
request, a stream or a busy hold, so it never delays the server's idle exit, and it
survives every exception, logged once per class.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ntpath
import posixpath
import secrets
import sys
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from moxi import modules

from . import acts, jobs, rows, state, store, verdict
from .cli import split_command
from .errors import Busy, ServicesError, TooNew, Unreadable
from .page import STATUS_ROUTE, TOPIC, lb
from .rows import MODULE, ModuleRow, UserRow

# The poller's cost, measured against the real systemd user manager on Linux (Fedora 43,
# systemd 258, Python 3.14, a 16-thread i7-13620H), CPU time from /proc over 3-minute
# windows. With no stream open the server used 0.08 CPU s a minute (0.13% of a core) at
# every size. With one stream open:
#   12 rows, 5 read at the supervisor, 3 with a health check: 0.28 s/min of its own,
#   0.38 with its children (each `systemctl show` and check), 0.64% of a core;
#   20 rows, 13 read, 6 with a check: 0.29 s/min of its own, 0.45 with its children,
#   0.75% of a core.
# The user manager answering the reads added 0.09 and 0.17 s/min. After the stream
# closed, the poller ran on at about the same rate (0.57% and 0.71% with children) until
# the idle exit. A row whose unit is missing costs no supervisor read. So loopback's
# default `idle_s` stays: the poller's run after the last stream closes costs about
# 3 CPU seconds, the user manager's share included.

#: Seconds between the poller's ticks: a supervisor read at each.
PERIOD = 5.0
#: Seconds between the poller's health passes.
HEALTH_PERIOD = 30.0
#: The most health checks the poller runs at once.
HEALTH_AT_ONCE = 8
#: Seconds after which the poller publishes the topic again though nothing but its
#: `at` changed, so a page's `read <time>` is never older than this.
REPUBLISH_S = 60.0
#: The most run lines a failing timer's streak counts back through.
STREAK_LINES = 50
#: The most run lines one row's read takes for its count of today's runs.
TODAY_LINES = 500
#: The newest runs one row's read shows.
RUNS_SHOWN = 5
#: Seconds a page read, or the poller, waits for the supervisor.
STATUS_SECONDS = 10.0
#: Who a change from the page is recorded as made by.
BY = "services page"
#: What a status read says of a job this server holds no record of.
UNKNOWN = "no record of this act; the row's state shows the outcome"
#: What a key used for another act or row is told, as loopback says it.
KEY_REUSED = "this Idempotency-Key was used for another request"
#: A timer's start and restart, and a daemon's run.
TIMER_RUNS = "a timer runs through /api/run"
DAEMON_STARTS = "/api/run runs a timer; a daemon starts through /api/start"
#: What a read of one row or an act is told when services' store can't be read, or a
#: newer services wrote it. Neither names the file.
STORE_UNREADABLE = ("services' store can't be read; fix: `moxi services list` on this "
                    "machine names the file and how to fix it")
STORE_TOO_NEW = ("services' store was written by a newer moxi-services; fix: upgrade it "
                 "with `moxi install services`")
#: The one row whose stop and restart end what runs in the slots: slots' server, by its
#: id. No other module declares what its daemon's stop ends.
SLOTS_SERVER = "slots/server"
#: What a Stop or Restart of slots' server is told when more runs in its slots than the
#: person was shown.
SLOTS_IN_USE = "Something is running in its slots now."
#: What a plain stop or restart of slots' server is told when its slots couldn't be read.
SLOTS_UNREAD = "Moxi can't read the slots, so stopping it may end sessions in them."
#: What `/api/ends` is told of any row but slots' server.
ENDS_SLOTS_ONLY = "only slots' server ends what runs in the slots"
#: What an act is told when its `ends` is below 0.
ENDS_NEGATIVE = "ends is how many the person was shown in use: 0 or more"
#: What an enable or disable of a Moxi row is told: the page never turns one on or off.
ON_OFF_IN_SETTINGS = "Its on and off is in Settings › {module}."
#: What `/api/act` is told of a job id that isn't 32 hex characters.
JOB_SHAPE = "job is 32 hex characters the page mints for each press"
#: What `/api/act` is told of a `restart` or an `ends` its act doesn't take.
RESTART_RUN_ONLY = "restart goes with run only"
ENDS_STOP_ONLY = "ends goes with stop and restart only"

log = logging.getLogger("moxi.services.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


def _text(exc: ServicesError) -> str:
    return f"{exc.reason}; fix: {exc.fix}" if exc.fix else exc.reason


def _answer(exc: ServicesError) -> Exception:
    """The page's answer to a refusal: 409 for a lock or store busy past the wait (exit
    75), else 422, with the reason and the fix. A store that can't be read or is too new
    gets a fixed sentence naming no file, its full text logged once."""
    if isinstance(exc, Busy):
        return lb.Conflict(_text(exc))
    if isinstance(exc, Unreadable):
        _log_once(exc, f"services' store couldn't be read: {_text(exc)}")
        return lb.Refused(STORE_TOO_NEW if isinstance(exc, TooNew) else STORE_UNREADABLE)
    return lb.Refused(_text(exc))


def _unexpected(err: Exception) -> str:
    return (f"services' page server hit an unexpected error ({type(err).__name__}); its "
            f"log has the details")


# -- one row as the page shows it -----------------------------------------------------

def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _switch(row) -> str | None:
    if isinstance(row, ModuleRow):
        return row.switch
    if state.is_wrapped(row):
        return state.key(row.name)
    return None


def _schedule(row: UserRow) -> str | None:
    if row.every:
        return f"every {row.every}"
    if row.at:
        days = f" on {','.join(row.days)}" if row.days else ""
        return f"at {','.join(row.at)}{days}"
    return None


def _list(value) -> list[str] | None:
    return None if value is None else list(value)


def _timing(row) -> dict[str, Any] | None:
    """A timer's schedule as declared: a written row's from the store, a module timer's
    from its declaration. None for a daemon and an adopted timer."""
    if row.kind != "timer" or (isinstance(row, UserRow) and row.form != "written"):
        return None
    return {"every": row.every, "at": _list(row.at), "days": _list(row.days)}


def ends_slots(row) -> bool:
    """True for slots' server, whose stop and restart end what runs in the slots."""
    return isinstance(row, ModuleRow) and row.id == SLOTS_SERVER and row.kind == "daemon"


def with_settings(found: list) -> frozenset[str]:
    """The modules among `found`'s module rows that have settings, so a row's
    `Settings` link leads somewhere. A module whose schema can't be read has none."""
    import moxi.config

    names = {r.module for r in found if isinstance(r, ModuleRow)}
    out = set()
    for name in names:
        loaded = modules.load(name)
        try:
            if loaded.ok and moxi.config.schema_for(loaded) is not None:
                out.add(name)
        except Exception as exc:  # noqa: BLE001 - an unreadable schema is no link
            _log_once(exc, f"{name}'s settings schema couldn't be read")
    return frozenset(out)


def row_value(view: verdict.View, *, settings: frozenset[str] = frozenset(),
              checked_at: str | None = None,
              streak: dict[str, Any] | None = None) -> dict[str, Any]:
    """One `page.ROW`. `settings` holds the modules that have settings; `checked_at` is
    when the health pass that gave the row's health result began; `streak` a failing
    timer's streak (`count_streak`)."""
    row, st = view.row, view.st
    user = row if isinstance(row, UserRow) else None
    last = verdict.last_run(st) if row.kind == "timer" else None
    return {
        "id": row.id, "module": row.module, "name": row.name, "form": row.form,
        "kind": row.kind, "doc": row.doc or "",
        "project": user.project if user else None,
        "launches_session": bool(user and user.launches_session),
        "flag": view.flag, "switch": _switch(row),
        "has_switch": not (isinstance(row, ModuleRow) and row.switch is None),
        "verdict": view.verdict.as_dict(),
        "state": None if st is None else st.state,
        "enabled": None if st is None else st.enabled,
        "pid": None if st is None else st.pid,
        "since": None if st is None else _iso(st.since),
        "detail": (st.detail or None) if st is not None else None,
        "last_run": None if last is None else {"failed": last.failed, "text": last.text,
                                               "at": last.at, "exit": last.exit},
        "checked": None if view.health is None else {"state": view.health[0],
                                                     "line": view.health[1],
                                                     "at": checked_at},
        "streak": streak,
        "timing": _timing(row),
        "ends_slots": ends_slots(row),
        "has_settings": user is None and row.module in settings,
        "argv": _list(user.argv) if user else None,
        "cwd": user.cwd if user else None,
        "unit": user.unit if user else None,
        "schedule": _schedule(user) if user else None,
        "health": _list(user.health) if user else None,
        "stopped": view.stopped,
        "removing": bool(user and user.removing),
        "platform": user.platform if user else None,
    }


# -- a failing timer's streak -----------------------------------------------------------

#: Each failing timer's streak, by row id, with the `at` of the newest run line it was
#: counted from: the runs file is read again only when a newer line arrives.
_streaks: dict[str, tuple[str | None, dict[str, Any] | None]] = {}
_streaks_lock = threading.Lock()


def count_streak(lines: list[dict]) -> dict[str, Any] | None:
    """How many of `lines`' newest runs failed in a row (lines oldest first), when the
    oldest of them ran, and whether they share one exit code; None when the newest run
    did not fail."""
    failed = []
    for line in reversed(lines):
        if not verdict.run_of(line).failed:
            break
        failed.append(line)
    if not failed:
        return None
    exits = {line.get("exit") for line in failed}
    return {"count": len(failed), "since": failed[-1].get("at"),
            "same_exit": len(exits) == 1}


def _streak(view: verdict.View, kept: dict[str, tuple[str | None, dict | None]]
            ) -> dict[str, Any] | None:
    """A timer's streak when its verdict is a failed last run, else None. Counted from
    its newest `STREAK_LINES` run lines once per newest line, then taken from `kept`,
    which this call updates."""
    if view.verdict.cause != "last-run":
        return None
    last = verdict.last_run(view.st)
    newest = None if last is None else last.at
    held = kept.get(view.row.id)
    if held is not None and held[0] == newest:
        return held[1]
    found = count_streak(state.last_runs(view.row, STREAK_LINES))
    kept[view.row.id] = (newest, found)
    return found


def _streaks_of(seen: list[verdict.View], *, is_every_row: bool
                ) -> dict[str, dict[str, Any] | None]:
    """Each of `seen`'s streaks, by id. With `is_every_row`, the kept streaks of rows no
    longer failing, or gone, are dropped."""
    with _streaks_lock:
        kept = dict(_streaks)
    out = {v.row.id: _streak(v, kept) for v in seen}
    with _streaks_lock:
        if is_every_row:
            _streaks.clear()
        _streaks.update({i: kept[i] for i in out if out[i] is not None})
    return out


# -- the health results the poller last read -----------------------------------------

#: Each row's last health result, by id, with when the pass that ran it began.
_known: dict[str, tuple[str, str, str]] = {}
_known_lock = threading.Lock()


def _remember(seen: list[verdict.View], at: str) -> None:
    with _known_lock:
        _known.clear()
        _known.update({v.row.id: (*v.health, at) for v in seen if v.health is not None})


def _known_health() -> dict[str, tuple[str, str]]:
    with _known_lock:
        return {ident: (said, line) for ident, (said, line, _) in _known.items()}


def _checked_at(view: verdict.View, at: str | None = None) -> str | None:
    """When the pass that gave `view` its health result began: `at` for this gather's own
    pass, else the kept pass's."""
    if view.health is None:
        return None
    if at is not None:
        return at
    with _known_lock:
        kept = _known.get(view.row.id)
    return None if kept is None else kept[2]


# -- the reads -------------------------------------------------------------------------

def every_row() -> tuple[list, list[tuple[str, str]]]:
    """The user's rows, then every installed module's daemons and timers; and the
    modules whose run-time declarations failed."""
    user = store.read_rows()
    found, problems = rows.module_rows()
    return [*user, *found], problems


def gather(*, check_health: bool = False) -> dict[str, Any]:
    """The `rows` topic's value and `/api/rows`' answer. With `check_health`, every running
    daemon's check runs and its result is kept for the reads after. Never raises."""
    try:
        found, problems = every_row()
        pass_at = None
        read_at = _iso(time.time())
        if check_health:
            pass_at = _iso(time.time())
            seen = verdict.views(found, check_health=True, status_seconds=STATUS_SECONDS,
                                 health_seconds=state.HEALTH_SECONDS,
                                 health_at_once=HEALTH_AT_ONCE)
            _remember(seen, pass_at)
        else:
            seen = verdict.views(found, status_seconds=STATUS_SECONDS,
                                 known_health=_known_health())
        settings = with_settings(found)
        streaks = _streaks_of(seen, is_every_row=True)
        listed = [row_value(v, settings=settings, checked_at=_checked_at(v, pass_at),
                            streak=streaks[v.row.id])
                  for v in seen]
    except ServicesError as exc:
        return {"state": "error", "detail": _text(exc), "at": None, "rows": [],
                "problems": []}
    except Exception as unexpected_exc:  # noqa: BLE001 - the page says so, never a 500
        _log_once(unexpected_exc, "listing the rows failed")
        return {"state": "error", "detail": _unexpected(unexpected_exc), "at": None,
                "rows": [], "problems": []}
    return {"state": "up", "detail": None, "at": read_at, "rows": listed,
            "problems": [{"module": m, "reason": r} for m, r in problems]}


def listing(call: Any) -> dict[str, Any]:
    """`GET /api/rows`: every row now, with the last health results."""
    return gather()


def _local_day(utc: Any) -> tuple[int, int, int] | None:
    seconds = verdict.utc_seconds(utc) if isinstance(utc, str) else None
    return None if seconds is None else time.localtime(seconds)[:3]


def count_today(lines: list[dict], *, limit: int = TODAY_LINES) -> dict[str, Any]:
    """`lines`' runs on this machine's local day, of the newest `limit` read: how many,
    how many failed, and `more` when every line read is today's, so there may be more."""
    today = time.localtime()[:3]
    ran = [line for line in lines if _local_day(line.get("at")) == today]
    return {"runs": len(ran), "failed": sum(verdict.run_of(line).failed for line in ran),
            "more": len(lines) >= limit and len(ran) == len(lines)}


def row(call: Any) -> dict[str, Any]:
    """`GET /api/row?id=ID`: one row, with a timer's newest runs, the count of its runs
    today, the units the supervisor knows it by and its log hint; 422 for an id that
    names no row."""
    try:
        found = acts.resolve(call.query["id"])
        view = verdict.views([found], status_seconds=STATUS_SECONDS,
                             known_health=_known_health())[0]
        lines = state.last_runs(found, TODAY_LINES) if found.kind == "timer" else []
        ids = list(state.unit_ids(found))
    except ServicesError as exc:
        raise _answer(exc) from None
    runs = lines[-RUNS_SHOWN:]
    return {"row": row_value(view, settings=with_settings([found]),
                             checked_at=_checked_at(view),
                             streak=_streaks_of([view], is_every_row=False)[found.id]),
            "runs": [{"at": r.get("at"), "result": r.get("result"), "exit": r.get("exit"),
                      "seconds": r.get("seconds")} for r in runs],
            "log": None if view.st is None else view.st.log,
            "unit": state.unit_of(found),
            "units": ids,
            "today": count_today(lines) if state.keeps_runs(found) else None}


def unit(call: Any) -> dict[str, Any]:
    """`GET /api/unit?ref=REF&kind=KIND`: what the supervisor says of a unit not yet
    adopted, for the adopt panel. `kind` is the person's choice where the supervisor
    can't tell a unit's kind (launchd, Task Scheduler); on systemd the suffix decides
    and a `kind` that disagrees is refused. A refusal is in `refused`, never a 422."""
    ref, asked = call.query["ref"], call.query.get("kind")
    backend = state.units.backend()
    answer: dict[str, Any] = {"found": None, "kind": asked, "asks_kind": False,
                              "enabled": None, "active": None, "wraps": False,
                              "refused": None}
    try:
        kind = rows.check_ref(ref, backend, is_timer=asked == "timer")
    except ServicesError as exc:
        answer["refused"] = _text(exc)
        return answer
    answer.update(kind=kind, asks_kind=backend != "systemd")
    if asked is not None and kind != asked:
        answer["refused"] = f"{ref} is a {_KIND_WORDS[kind]}, not a {_KIND_WORDS[asked]}"
        return answer
    done, st = verdict.within(lambda: state.read_foreign(ref, kind), STATUS_SECONDS)
    if not done or st is None or st.state in ("unknown", "nobackend"):
        return answer
    answer["found"] = st.state != "missing"
    if answer["found"]:
        answer.update(enabled=st.enabled, active=st.state == "active",
                      wraps=backend == "systemd" and kind == "timer")
    return answer


_KIND_WORDS = {"daemon": "service", "timer": "timer"}


# -- what a stop of slots' server ends -------------------------------------------------

def _unread(why: str) -> dict[str, Any]:
    """A read that didn't answer: its counts are null, never 0, since what is in use is
    not known."""
    return {"read": False, "why": why, "sessions": None, "open": None, "jobs": None}


def _count_ends(api: Any) -> dict[str, Any]:
    listing = api.list_slots()
    if not listing.ok:
        return _unread(f"slots couldn't be read: {listing.why}")
    try:
        running = sum(1 for found in api.jobs() if found.running)
    except Exception as exc:  # noqa: BLE001 - an unread count is said, never raised
        return _unread(f"slots' jobs couldn't be read ({type(exc).__name__})")
    sessions, open_slots = [], 0
    for found in (*listing.slots, *listing.unregistered):
        if found.state == "missing":
            continue
        if found.state == "occupied":
            sessions.append({"slot": found.number, "label": found.label or None})
        elif not found.idle:
            open_slots += 1
    return {"read": True, "why": None, "sessions": sessions, "open": open_slots,
            "jobs": running}


def read_ends() -> dict[str, Any]:
    """What a stop of slots' server would end now, from slots' own API, counted as slots'
    uninstall check counts it: each occupied slot a session, with its number and label;
    each other slot not missing and not idle an open slot; each running job. `read` is
    False, with `why` and null counts, when slots' API does not load, its listing could
    not be read, its jobs could not be read, or it did not answer within
    `STATUS_SECONDS`."""
    found = modules.load("slots", interface=1)
    if not found.ok:
        return _unread(f"slots' API couldn't be loaded: {found.reason}")
    try:
        done, value = verdict.within(lambda: _count_ends(found.api), STATUS_SECONDS)
    except Exception as exc:  # noqa: BLE001 - an unread count is said, never raised
        _log_once(exc, "reading slots' listing failed")
        return _unread(f"slots couldn't be read ({type(exc).__name__})")
    if not done:
        return _unread(f"slots did not answer within {STATUS_SECONDS:g} s")
    return value


def in_use(read: Mapping[str, Any]) -> int | None:
    """How many things `read_ends` found in use: sessions, open slots and jobs; None when
    it couldn't read them, which may be any number."""
    if not read["read"]:
        return None
    return len(read["sessions"]) + read["open"] + read["jobs"]


def guard_slots(found, ends: int | None) -> None:
    """Refuse a stop or restart of slots' server unless what it ends now is no more than
    the person was shown. `ends` is the count the warning showed, 0 when it said the
    slots couldn't be read, None for a plain press. A read that didn't answer counts as
    "may end sessions", never as nothing in use: only a confirmed press (`ends` given)
    goes past it, and a plain one is told the slots couldn't be read. It goes ahead when
    the read finds nothing in use, or when `ends` is given and the read did not answer
    or finds at most `ends`. Any other row passes."""
    if not ends_slots(found):
        return
    if ends is not None and ends < 0:
        raise lb.Refused(ENDS_NEGATIVE)
    count = in_use(read_ends())
    if count is None:
        if ends is None:
            raise lb.Refused(SLOTS_UNREAD)
        return
    if count == 0 or (ends is not None and count <= ends):
        return
    raise lb.Refused(SLOTS_IN_USE)


def ends(call: Any) -> dict[str, Any]:
    """`GET /api/ends?id=ID`: what a stop or restart of slots' server would end now (see
    `read_ends`); 422 for any other row."""
    try:
        found = acts.resolve(call.query["id"])
    except ServicesError as exc:
        raise _answer(exc) from None
    if not ends_slots(found):
        raise lb.Refused(ENDS_SLOTS_ONLY)
    return read_ends()


def projects(call: Any) -> dict[str, Any]:
    """`GET /api/projects`: every project by key, for the add form's picker; `reason`
    says why there is none (projects not installed, busy or unreadable)."""
    found = modules.load("projects", interface=1)
    if not found.ok:
        return {"projects": [], "reason": "projects is not installed; `moxi install "
                                          "services --with projects` adds it"}
    try:
        listed = found.api.list_projects()
    except Exception as exc:  # noqa: BLE001 - the picker says why, it never fails
        if type(exc).__name__ == "Busy":
            return {"projects": [], "reason": "projects is busy; try again"}
        _log_once(exc, "services' page couldn't list the projects")
        return {"projects": [], "reason": f"projects couldn't be listed "
                                          f"({type(exc).__name__})"}
    return {"projects": [{"key": str(p.key), "root": str(p.root)} for p in listed],
            "reason": None}


def status(call: Any) -> dict[str, Any]:
    """`GET /api/status?job=ID`: the job's record, or `unknown` when there is none."""
    job = call.query["job"]
    found = JOBS.get(job)
    if found is None:
        return {"job": job, "act": None, "id": None, "state": "unknown", "code": None,
                "lines": [UNKNOWN], "fix": None}
    return found.as_dict()


# -- the acts --------------------------------------------------------------------------

#: The job thread and its records, for this server's life.
JOBS = jobs.Jobs(lb.busy)


def _link(job: str) -> Any:
    return lb.Accepted(f"{STATUS_ROUTE}?job={job}")


def _canonical(ident: str) -> str:
    return ident if "/" in ident else rows.row_id(MODULE, ident)


def _confirmed(call: Any, act: str, run: Callable[[str], acts.Outcome], *,
               is_timer: bool | None = None) -> Any:
    """A confirmed act on one row, queued under its idempotency key (`_queue`)."""
    return _queue(call.key, act, call.body["id"], run, is_timer=is_timer,
                  ends=call.body.get("ends"))


def _queue(job: str, act: str, ident: str, run: Callable[[str], acts.Outcome], *,
           is_timer: bool | None = None, ends: int | None = None) -> Any:
    """An act on one row, queued as `job`: `is_timer` True for an act only a timer takes,
    False for one a timer takes through `/api/run`, None for either. An enable or
    disable of a Moxi row is refused, and a stop or restart of slots' server goes only
    as `guard_slots` lets it, with `ends`."""
    held = JOBS.get(job)
    if held is not None:
        if held.act == act and held.id == _canonical(ident):
            return _link(job)
        raise lb.Refused(KEY_REUSED)
    try:
        found = acts.resolve(ident)
    except ServicesError as exc:
        raise _answer(exc) from None
    if is_timer is False and found.kind == "timer":
        raise lb.Refused(TIMER_RUNS)
    if is_timer is True and found.kind != "timer":
        raise lb.Refused(DAEMON_STARTS)
    if act in ("enable", "disable") and isinstance(found, ModuleRow):
        raise lb.Refused(ON_OFF_IN_SETTINGS.format(module=found.module))
    if act in ("stop", "restart"):
        guard_slots(found, ends)
    record, _ = JOBS.submit(job, act, found.id, lambda: run(found.id))
    if record.act != act or record.id != found.id:
        raise lb.Refused(KEY_REUSED)
    return _link(job)


def start(call: Any) -> Any:
    """`POST /api/start`, confirmed: `moxi services start ID`, a daemon."""
    return _confirmed(call, "start", lambda i: acts.start(i, by=BY), is_timer=False)


def stop(call: Any) -> Any:
    """`POST /api/stop`, confirmed: `moxi services stop ID`, a daemon or a timer's run;
    slots' server's under the guard, with `ends`."""
    return _confirmed(call, "stop", lambda i: acts.stop(i, by=BY))


def restart(call: Any) -> Any:
    """`POST /api/restart`, confirmed: `moxi services restart ID`, a daemon; slots'
    server's under the guard, with `ends`."""
    return _confirmed(call, "restart", lambda i: acts.restart(i, by=BY), is_timer=False)


def run(call: Any) -> Any:
    """`POST /api/run`, confirmed: a timer's run now, its `start`; with `restart`, a run
    in progress ended first."""
    act = acts.restart if call.body.get("restart") else acts.start
    return _confirmed(call, "run", lambda i: act(i, by=BY), is_timer=True)


def enable(call: Any) -> Any:
    """`POST /api/enable`, confirmed: `moxi services enable ID`; a Moxi row's is
    refused, its on and off being in Settings."""
    return _confirmed(call, "enable", lambda i: acts.enable(i, by=BY))


def disable(call: Any) -> Any:
    """`POST /api/disable`, confirmed: `moxi services disable ID`; a Moxi row's is
    refused, as enable's."""
    return _confirmed(call, "disable", lambda i: acts.disable(i, by=BY))


def remove(call: Any) -> Any:
    """`POST /api/remove`, confirmed and desk-only: `moxi services remove ID`."""
    return _confirmed(call, "remove", lambda i: acts.remove(i, by=BY))


def _run_act(i: str, *, restart: bool) -> acts.Outcome:
    return (acts.restart if restart else acts.start)(i, by=BY)


#: Each act `/api/act` takes: what it runs (given the row's id and the body), and
#: `is_timer` as `_queue` reads it.
_ACTS: dict[str, tuple[Callable[[str, Mapping[str, Any]], acts.Outcome], bool | None]] = {
    "start": (lambda i, body: acts.start(i, by=BY), False),
    "stop": (lambda i, body: acts.stop(i, by=BY), None),
    "restart": (lambda i, body: acts.restart(i, by=BY), False),
    "run": (lambda i, body: _run_act(i, restart=bool(body.get("restart"))), True),
    "enable": (lambda i, body: acts.enable(i, by=BY), None),
    "disable": (lambda i, body: acts.disable(i, by=BY), None),
    "remove": (lambda i, body: acts.remove(i, by=BY), None),
}


def act(call: Any) -> Any:
    """`POST /api/act`, the desk's one plain act: `act` on the row `id`, queued as `job`,
    32 hex characters the page mints per press. A repeat of a `job` for the same act and
    row gets the same link; for another, it is refused. `restart` goes with `run` only,
    `ends` with `stop` and `restart` only. Each act refuses what its confirmed route
    refuses."""
    body = call.body
    job, name = body["job"], body["act"]
    if not _is_job(job):
        raise lb.Refused(JOB_SHAPE)
    if "restart" in body and name != "run":
        raise lb.Refused(RESTART_RUN_ONLY)
    if "ends" in body and name not in ("stop", "restart"):
        raise lb.Refused(ENDS_STOP_ONLY)
    work, is_timer = _ACTS[name]
    return _queue(job, name, body["id"], lambda i: work(i, body), is_timer=is_timer,
                  ends=body.get("ends"))


def _is_job(job: str) -> bool:
    return len(job) == 32 and all(c in "0123456789abcdefABCDEF" for c in job)


def _absolute(path: str) -> bool:
    return posixpath.isabs(path) or (ntpath.isabs(path) and bool(ntpath.splitdrive(path)[0]))


def _split(text: str, what: str) -> list[str]:
    try:
        return split_command(text)
    except ServicesError:
        raise lb.Refused(f"the {what} is empty or its quotes don't balance; give it as "
                         "you would type it in your shell") from None


def not_a_full_path(word: str) -> str:
    """What an add whose command doesn't start with a full path is told."""
    return (f"{word} is not a full path: this page's server doesn't search your shell's "
            f"PATH; give the full path; `command -v {word}` in your shell prints it")


def add(call: Any) -> Any:
    """`POST /api/add`: `moxi services add`, queued under a job id this handler mints.
    `command` adds a written row, `unit` adopts one; the fields are checked here, and
    the name, the project and the unit by the act."""
    body = call.body
    name, timer = body["name"], body["kind"] == "timer"
    command, unit = body.get("command"), body.get("unit")
    if (command is None) == (unit is None):
        raise lb.Refused("give a command line to run, or the unit to adopt, not both")
    health = None if body.get("health") is None else _split(body["health"],
                                                            "health command")
    common = dict(health=health, project=body.get("project"),
                  launches_session=bool(body.get("launches_session", False)),
                  doc=body.get("doc", ""))
    fields = dict(name=name, kind="timer" if timer else "daemon", platform=sys.platform,
                  every=body.get("every"), at=body.get("at"), days=body.get("days"),
                  cwd=body.get("cwd"), **common)
    if command is not None:
        argv = _split(command, "command line")
        if not _absolute(argv[0]):
            raise lb.Refused(not_a_full_path(argv[0]))
        fields.update(form="written", argv=argv)

        def work() -> acts.Outcome:
            return acts.add_written(name, argv, is_timer=timer, every=fields["every"],
                                    at=fields["at"], days=fields["days"],
                                    cwd=fields["cwd"], enable=bool(body.get("enable")),
                                    resolve_argv=False, by=BY, **common)
    else:
        fields.update(form="adopted", unit=unit)

        def work() -> acts.Outcome:
            return acts.add_adopted(name, unit, enable=body.get("enable"), is_timer=timer,
                                    by=BY, **common)
    try:
        rows.check_fields(fields)
    except ServicesError as exc:
        raise _answer(exc) from None
    job = secrets.token_urlsafe(32)
    JOBS.submit(job, "add", rows.row_id(MODULE, name), work)
    return _link(job)


# -- the stream and its poller -------------------------------------------------------

async def events(call: Any) -> Any:
    """`GET /api/events`: the stream over the `rows` topic. The first call starts the
    poller."""
    return call.stream(_feed().topics)


def _digest(value: Mapping[str, Any]) -> str:
    """The topic's value's digest, its `at` left out: a read that changed nothing else
    is not a change."""
    rest = {k: v for k, v in value.items() if k != "at"}
    return hashlib.sha256(json.dumps(rest, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


class _Feed:
    """The topic and the poller, on one event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.topics = lb.Topics(TOPIC)
        self.thread: threading.Thread | None = None
        self.skipped = 0
        self._health_at: float | None = None
        self._last: str | None = None
        self._published_at: float | None = None
        self.task = loop.create_task(self._poll(), name="services rows poller")

    async def _poll(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - the poller outlives every failure
                _log_once(exc, "the rows poller's tick failed")
            await asyncio.sleep(PERIOD)

    def tick(self) -> bool:
        """Start a gather, with a health pass when one is due; or, while the last gather
        still runs, skip this tick. True when a gather started."""
        if self.thread is not None and self.thread.is_alive():
            self.skipped += 1
            return False
        now = time.monotonic()
        check_health = self._health_at is None or now - self._health_at >= HEALTH_PERIOD
        if check_health:
            self._health_at = now
        self.thread = threading.Thread(target=self._gather, kwargs={"check_health": check_health}, daemon=True,
                                       name="services rows gather")
        self.thread.start()
        return True

    def _gather(self, *, check_health: bool) -> None:
        try:
            value = gather(check_health=check_health)
            digest = _digest(value)
            now = time.monotonic()
            stale = (self._published_at is None
                     or now - self._published_at >= REPUBLISH_S)
            if digest != self._last or stale:
                self.topics.publish(TOPIC, value)
                self._last, self._published_at = digest, now
        except Exception as exc:  # noqa: BLE001 - the next tick gathers again
            _log_once(exc, "the rows poller's gather failed")


_current: _Feed | None = None


def _feed() -> _Feed:
    # A page server runs one loop for its life; a loop of its own per in-process test
    # gets a fresh feed, since a task belongs to one loop.
    global _current
    loop = asyncio.get_running_loop()
    if _current is None or _current.loop is not loop:
        _current = _Feed(loop)
    return _current


__all__ = ["BY", "DAEMON_STARTS", "ENDS_NEGATIVE", "ENDS_SLOTS_ONLY", "ENDS_STOP_ONLY",
           "HEALTH_PERIOD", "JOBS", "JOB_SHAPE", "KEY_REUSED", "ON_OFF_IN_SETTINGS",
           "PERIOD", "REPUBLISH_S", "RESTART_RUN_ONLY", "RUNS_SHOWN", "SLOTS_IN_USE",
           "SLOTS_SERVER", "SLOTS_UNREAD", "STATUS_SECONDS", "STREAK_LINES", "TIMER_RUNS",
           "TODAY_LINES", "UNKNOWN", "act", "add", "count_streak", "count_today", "disable", "enable",
           "ends", "ends_slots", "events", "every_row", "gather", "guard_slots", "in_use",
           "listing", "not_a_full_path", "projects", "read_ends", "remove", "restart",
           "row", "row_value", "run", "start", "status", "stop", "unit", "with_settings"]
