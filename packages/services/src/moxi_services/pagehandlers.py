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

#: Seconds between the poller's ticks: a supervisor read at each.
PERIOD = 5.0
#: Seconds between the poller's health passes.
HEALTH_PERIOD = 30.0
#: The most health checks the poller runs at once.
HEALTH_AT_ONCE = 8
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


def row_value(view: verdict.View) -> dict[str, Any]:
    """One `page.ROW`."""
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
                                               "at": last.at},
        "checked": None if view.health is None else {"state": view.health[0],
                                                     "line": view.health[1]},
        "argv": _list(user.argv) if user else None,
        "cwd": user.cwd if user else None,
        "unit": user.unit if user else None,
        "schedule": _schedule(user) if user else None,
        "health": _list(user.health) if user else None,
        "stopped": user.stopped if user else None,
        "removing": bool(user and user.removing),
        "platform": user.platform if user else None,
    }


# -- the health results the poller last read -----------------------------------------

_known: dict[str, tuple[str, str]] = {}
_known_lock = threading.Lock()


def _remember(seen: list[verdict.View]) -> None:
    with _known_lock:
        _known.clear()
        _known.update({v.row.id: v.health for v in seen if v.health is not None})


def _known_health() -> dict[str, tuple[str, str]]:
    with _known_lock:
        return dict(_known)


# -- the reads -------------------------------------------------------------------------

def every_row() -> tuple[list, list[tuple[str, str]]]:
    """The user's rows, then every installed module's daemons and timers; and the
    modules whose run-time declarations failed."""
    user = store.read_rows()
    found, problems = rows.module_rows()
    return [*user, *found], problems


def gather(*, health: bool = False) -> dict[str, Any]:
    """The `rows` topic's value and `/api/rows`' answer. With `health`, every running
    daemon's check runs and its result is kept for the reads after. Never raises."""
    try:
        found, problems = every_row()
        if health:
            seen = verdict.views(found, health=True, status_seconds=STATUS_SECONDS,
                                 health_seconds=state.HEALTH_SECONDS,
                                 health_at_once=HEALTH_AT_ONCE)
            _remember(seen)
        else:
            seen = verdict.views(found, status_seconds=STATUS_SECONDS,
                                 known_health=_known_health())
        listed = [row_value(v) for v in seen]
    except ServicesError as exc:
        return {"state": "error", "detail": _text(exc), "rows": [], "problems": []}
    except Exception as err:  # noqa: BLE001 - the page says so, never a 500
        _log_once(err, "listing the rows failed")
        return {"state": "error", "detail": _unexpected(err), "rows": [], "problems": []}
    return {"state": "up", "detail": None, "rows": listed,
            "problems": [{"module": m, "reason": r} for m, r in problems]}


def listing(call: Any) -> dict[str, Any]:
    """`GET /api/rows`: every row now, with the last health results."""
    return gather()


def row(call: Any) -> dict[str, Any]:
    """`GET /api/row?id=ID`: one row, with a timer's newest runs and its log hint; 422
    for an id that names no row."""
    try:
        found = acts.resolve(call.query["id"])
        view = verdict.views([found], status_seconds=STATUS_SECONDS,
                             known_health=_known_health())[0]
        runs = state.last_runs(found, 5) if found.kind == "timer" else []
    except ServicesError as exc:
        raise _answer(exc) from None
    return {"row": row_value(view),
            "runs": [{"at": r.get("at"), "result": r.get("result"), "exit": r.get("exit"),
                      "seconds": r.get("seconds")} for r in runs],
            "log": None if view.st is None else view.st.log}


def projects(call: Any) -> dict[str, Any]:
    """`GET /api/projects`: every project by key, for the add form's picker; `reason`
    says why there is none (projects not installed, busy or unreadable)."""
    found = modules.load("projects", interface=1)
    if not found.ok:
        return {"projects": [], "reason": "projects is not installed; `moxi install "
                                          "services --with projects` adds it"}
    try:
        listed = found.api.list_projects()
    except Exception as err:  # noqa: BLE001 - the picker says why, it never fails
        if type(err).__name__ == "Busy":
            return {"projects": [], "reason": "projects is busy; try again"}
        _log_once(err, "services' page couldn't list the projects")
        return {"projects": [], "reason": f"projects couldn't be listed "
                                          f"({type(err).__name__})"}
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
               timer: bool | None = None) -> Any:
    """A confirmed act on one row, queued: `timer` True for an act only a timer takes,
    False for one a timer takes through `/api/run`, None for either."""
    job, ident = call.key, call.body["id"]
    held = JOBS.get(job)
    if held is not None:
        if held.act == act and held.id == _canonical(ident):
            return _link(job)
        raise lb.Refused(KEY_REUSED)
    try:
        found = acts.resolve(ident)
    except ServicesError as exc:
        raise _answer(exc) from None
    if timer is False and found.kind == "timer":
        raise lb.Refused(TIMER_RUNS)
    if timer is True and found.kind != "timer":
        raise lb.Refused(DAEMON_STARTS)
    record, _ = JOBS.submit(job, act, found.id, lambda: run(found.id))
    if record.act != act or record.id != found.id:
        raise lb.Refused(KEY_REUSED)
    return _link(job)


def start(call: Any) -> Any:
    """`POST /api/start`, confirmed: `moxi services start ID`, a daemon."""
    return _confirmed(call, "start", lambda i: acts.start(i, by=BY), timer=False)


def stop(call: Any) -> Any:
    """`POST /api/stop`, confirmed: `moxi services stop ID`, a daemon or a timer's run."""
    return _confirmed(call, "stop", lambda i: acts.stop(i, by=BY))


def restart(call: Any) -> Any:
    """`POST /api/restart`, confirmed: `moxi services restart ID`, a daemon."""
    return _confirmed(call, "restart", lambda i: acts.restart(i, by=BY), timer=False)


def run(call: Any) -> Any:
    """`POST /api/run`, confirmed: a timer's run now, its `start`; with `restart`, a run
    in progress ended first."""
    act = acts.restart if call.body.get("restart") else acts.start
    return _confirmed(call, "run", lambda i: act(i, by=BY), timer=True)


def enable(call: Any) -> Any:
    """`POST /api/enable`, confirmed: `moxi services enable ID`."""
    return _confirmed(call, "enable", lambda i: acts.enable(i, by=BY))


def disable(call: Any) -> Any:
    """`POST /api/disable`, confirmed: `moxi services disable ID`."""
    return _confirmed(call, "disable", lambda i: acts.disable(i, by=BY))


def remove(call: Any) -> Any:
    """`POST /api/remove`, confirmed and desk-only: `moxi services remove ID`."""
    return _confirmed(call, "remove", lambda i: acts.remove(i, by=BY))


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
            return acts.add_written(name, argv, timer=timer, every=fields["every"],
                                    at=fields["at"], days=fields["days"],
                                    cwd=fields["cwd"], enable=bool(body.get("enable")),
                                    resolve_argv=False, by=BY, **common)
    else:
        fields.update(form="adopted", unit=unit)

        def work() -> acts.Outcome:
            return acts.add_adopted(name, unit, enable=body.get("enable"), timer=timer,
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
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
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
        self.task = loop.create_task(self._poll(), name="services rows poller")

    async def _poll(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as err:  # noqa: BLE001 - the poller outlives every failure
                _log_once(err, "the rows poller's tick failed")
            await asyncio.sleep(PERIOD)

    def tick(self) -> bool:
        """Start a gather, with a health pass when one is due; or, while the last gather
        still runs, skip this tick. True when a gather started."""
        if self.thread is not None and self.thread.is_alive():
            self.skipped += 1
            return False
        now = time.monotonic()
        health = self._health_at is None or now - self._health_at >= HEALTH_PERIOD
        if health:
            self._health_at = now
        self.thread = threading.Thread(target=self._gather, args=(health,), daemon=True,
                                       name="services rows gather")
        self.thread.start()
        return True

    def _gather(self, health: bool) -> None:
        try:
            value = gather(health=health)
            digest = _digest(value)
            if digest != self._last:
                self.topics.publish(TOPIC, value)
                self._last = digest
        except Exception as err:  # noqa: BLE001 - the next tick gathers again
            _log_once(err, "the rows poller's gather failed")


_current: _Feed | None = None


def _feed() -> _Feed:
    # A page server runs one loop for its life; a loop of its own per in-process test
    # gets a fresh feed, since a task belongs to one loop.
    global _current
    loop = asyncio.get_running_loop()
    if _current is None or _current.loop is not loop:
        _current = _Feed(loop)
    return _current


__all__ = ["BY", "DAEMON_STARTS", "HEALTH_PERIOD", "JOBS", "KEY_REUSED", "PERIOD",
           "STATUS_SECONDS", "TIMER_RUNS", "UNKNOWN", "add", "disable", "enable",
           "events", "every_row", "gather", "listing", "not_a_full_path", "projects",
           "remove", "restart", "row", "row_value", "run", "start", "status", "stop"]
