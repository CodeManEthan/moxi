"""The handlers of the launcher's page.

The reads answer from the launcher's own calls: the catalog (`catalog.offered`'s rows, as
the UI shell's form reads them), the presets file checked (`presets.check`), the launch
log (`store.log`), the runs with their events (`store.runs`, `store.run_events`), the
past-sessions list (`resume.sessions`) and a session's state (`state.state`, or
`state.of_launch` for every live launch). Each holds `busy("read")` for its own length
only.

**A live launch** is one not `failed` whose process runs now: `moxi.proc.alive` on the pid
and start time its row recorded, or, with no pid, on the endpoint that claimed it. That
costs one store read and one look at a process per row, and asks turns nothing, so the
`sessions` topic can gather it every few seconds; the past-sessions list, which asks turns
about each entry, is read only when the page asks for it. A live launch whose state can't
be read (a keep-warm launch's cache read failing, say) is marked `unreadable` in its own
entry, naming only the exception's class, and the other entries still stand.

**The stream** carries two topics, `sessions` (every live launch's state) and `launches`
(the newest page of the launch log), each built by a `Snapshot` of its own, which
publishes only when its value changed. One poller feeds both: an asyncio task the stream
route starts on its first call, living for the rest of the process. Every `TICK` seconds
it reads the launch log's data version, and asks both topics for a rebuild when it moved,
or when `REGATHER` seconds have passed without one: a session's process ending writes
nothing to the log, nor does a spawning row turning stale. The poller is not a request, a
stream or a busy hold, so it never delays the server's idle exit. It survives every
exception: a failure is logged once per class and the next tick reads again.

A handler cuts each answer to its route's declared keys, and turns any failure it did not
expect into `state` `error` with a fixed `detail` naming only the exception's class, so no
message text, which may hold a path, reaches the browser. The class and its traceback are
logged once. A query the read can't take (a `before` that is not a time, a `limit` under
1, `request` with paging) is refused (422) with what to send instead.

A route the page declares whose handler this version does not have yet answers 422, saying
so, and runs nothing.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from moxi import aio, modules, proc
from moxi import store as core_store
from moxi.config import ConfigError

from . import catalog, presets, slots, state, store, target
from .page import LAUNCH, LIMIT, RUN
from .resume import sessions as past_sessions

_loopback = modules.load("loopback", interface=1)
if not _loopback.ok:
    raise ImportError(f"the launcher's page needs the loopback module: {_loopback.reason}")
lb = _loopback.api

#: The detail of a read that failed in a way it did not expect.
UNEXPECTED = "the launcher's page server hit an unexpected error ({}); its log has the details"
#: What a live launch's entry says when its state could not be read.
UNREADABLE = ("the launcher could not read this session's state ({}); the page server's log "
              "has the details")
#: The catalog rows whose text a preset's values never send: a brief or standing
#: instructions written inline.
WITHHELD = ("brief", "instructions")
#: The run event detail key never sent: a notice's whole text, which the run's other events
#: already describe.
EVENT_TEXT = "text"
#: The stream's topics.
TOPICS = ("sessions", "launches")
#: Seconds between the poller's reads of the launch log's data version, which moves at
#: every write to the log.
TICK = 1.0
#: Seconds after which the poller gathers both topics again though the log has not moved:
#: a session's process ending, or a run's job going, writes nothing to it.
REGATHER = 5.0

log = logging.getLogger("moxi.launcher.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


def _failed(err: BaseException, what: str, **empty: Any) -> dict[str, Any]:
    _log_once(err, what)
    return {"state": "error", "detail": UNEXPECTED.format(type(err).__name__), **empty}


def _up(**answer: Any) -> dict[str, Any]:
    return {"state": "up", "detail": None, **answer}


# -- spelling values as the command line does ---------------------------------------------


def spelled(value: object) -> str | None:
    """A scalar value as the command line spells it: `true`/`false`, a number's digits,
    a string as it is; None for a value that sets nothing (None or "")."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _spelled_items(value: object) -> list[str] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    return [str(item) for item in value]


def _value_of(row: catalog.Row | None, value: object) -> tuple[str | None, list[str] | None]:
    """`(value, items)` for one value of `row`: a list row's items, any other's spelling.
    A switch with an optional value that is off spells nothing."""
    if isinstance(value, (list, tuple)):
        return None, _spelled_items(value)
    if row is not None and row.optional_value and value is False:
        return None, None
    return spelled(value), None


# -- /api/catalog ---------------------------------------------------------------------------


def _available() -> Callable[[str], bool]:
    # Looked up at each call, so it answers for the modules installed now.
    return slots.links


def _offered_values(row: catalog.Row, available: Callable[[str], bool],
                    harness: str) -> tuple[list[str] | None, list[str]]:
    """The words of `row` this install offers, and a note for each it doesn't: an `enum`
    value whose own modules are absent, or whose condition does not hold here. A value
    that needs only another row's value (a target) stays; the launch's check refuses it
    with its own words."""
    if not row.values:
        return None, []
    words, notes = [], []
    for word in row.values:
        need = row.when.get(word)
        missing = need is not None and (
            not all(available(module) for module in need.requires)
            or (need.condition is not None
                and catalog.CONDITIONS[need.condition](harness) is not None))
        if missing:
            notes.append(f"{word} is not offered here: {need.refused}")
        else:
            words.append(str(word))
    return words, notes


def field(row: catalog.Row, available: Callable[[str], bool], harness: str) -> dict[str, Any]:
    """One catalog row as the UI shell's form reads it, for a `harness` launch."""
    why = row.unoffered(available, harness)
    values, notes = _offered_values(row, available, harness)
    default, items = _value_of(row, row.default)
    return {
        "name": row.name, "type": row.type, "doc": row.doc,
        "default": default, "value_items": items,
        "values": values, "items": row.items,
        "flag": row.option, "since": row.since,
        "repeatable": row.repeatable, "optional_value": row.optional_value,
        "hidden": row.hidden, "posture": row.posture,
        # The form reads one module's name; a row needing several says so in `note`.
        "requires": row.requires if isinstance(row.requires, str) else None,
        "needs_absent": why is not None,
        "note": why[0] if why is not None else ("; ".join(notes) or None),
    }


def fields(harness: str, available: Callable[[str], bool]) -> list[dict[str, Any]]:
    """Every row a user may set for a `harness` launch, the launcher's then the harness's,
    in catalog order, with those this install doesn't offer marked so."""
    rows = [*catalog.launcher(), *catalog.load(harness)]
    return [field(row, available, harness) for row in rows if row.settable]


def read_catalog(call: Any) -> dict[str, Any]:
    """`GET /api/catalog`: per harness, the rows a launch may set."""
    with call.busy("read"):
        try:
            available = _available()
            harnesses = [{"name": harness, "fields": fields(harness, available)}
                         for harness in catalog.HARNESSES]
        except Exception as err:
            return _failed(err, "reading the catalog failed", harnesses=[])
        return _up(harnesses=harnesses)


# -- /api/presets ---------------------------------------------------------------------------


def _preset(preset: presets.Preset) -> dict[str, Any]:
    harness = preset.harness if isinstance(preset.harness, str) else None
    rows = []
    for name, value in preset.values.items():
        found = catalog.row(harness, name) if harness in catalog.HARNESSES else (
            catalog.launcher().rows.get(name))
        if name in WITHHELD:
            shown, items = None, None
        else:
            shown, items = _value_of(found, value)
        rows.append({"name": name, "value": shown, "items": items})
    return {"kind": preset.kind, "name": preset.name, "harness": harness, "values": rows}


def _file_problem(err: ConfigError) -> dict[str, Any]:
    return {"where": "file", "key": err.key if isinstance(err.key, str) else None,
            "problem": str(err.problem), "hint": str(err.fix), "code": "bad-file",
            "level": presets.ERROR}


def read_presets(call: Any) -> dict[str, Any]:
    """`GET /api/presets`: every seat and project preset, and what is wrong with each. A
    presets file that can't be read is one problem, `where` `file`."""
    with call.busy("read"):
        try:
            try:
                found = presets.read()
            except ConfigError as err:
                return _up(presets=[], problems=[_file_problem(err)])
            problems = presets.check(available=_available(), presets=found)
            return _up(presets=[_preset(p) for p in found.all()],
                       problems=[p.as_json() for p in problems])
        except Exception as err:
            return _failed(err, "reading the presets failed", presets=[], problems=[])


# -- the launch log -------------------------------------------------------------------------


@contextmanager
def _store() -> Iterator[sqlite3.Connection | None]:
    """The launch log, or None when it was never made; nothing is made to say so."""
    try:
        conn = store.connect(create=False)
    except core_store.StoreError as err:
        if isinstance(err, core_store.StoreTooNew):
            raise
        yield None
        return
    try:
        yield conn
    finally:
        conn.close()


def _cut(data: Mapping[str, Any], shape: Any) -> dict[str, Any]:
    """`data` cut to the keys `shape` declares; a key it lacks is null."""
    return {key: data.get(key) for key in shape.items}


def _paging(query: Mapping[str, Any]) -> tuple[int, str | None]:
    limit = query.get("limit", LIMIT)
    if limit < 1:
        raise lb.Refused("limit is 1 or more")
    before = query.get("before")
    if before is not None:
        try:
            store.parse_time(before)
        except ValueError:
            raise lb.Refused("before is a time as the launch log writes it, such as a row's "
                             "created_at") from None
    return limit, before


def _alone(query: Mapping[str, Any], key: str) -> None:
    if key in query and len(query) > 1:
        raise lb.Refused(f"{key} is asked alone, without {', '.join(sorted(set(query) - {key}))}")


def launch_row(row: store.LaunchRow, stale_after: float, now: datetime) -> dict[str, Any]:
    """One launch log row as the page shows it."""
    data = {key: getattr(row, key, None) for key in LAUNCH.items}
    return {**data, "stale": row.stale(stale_after, now)}


def refusal(detail: Mapping[str, Any]) -> dict[str, Any]:
    """A refusal recorded in an event's detail, as the page sends it."""
    def text(key: str) -> str | None:
        value = detail.get(key)
        return value if isinstance(value, str) else None

    return {"code": text("code") or "refused", "row": text("row"),
            "problem": text("problem") or "", "hint": text("hint") or ""}


def _requested(conn: sqlite3.Connection, request_id: str
               ) -> tuple[list[store.LaunchRow], dict[str, Any] | None]:
    row = store.get(conn, request_id)
    if row is not None:
        return [row], None
    refused = [e for e in store.events(conn, request_id) if e.kind == store.LAUNCH_REFUSED]
    return [], refusal(refused[-1].detail) if refused else None


def launches(*, limit: int = LIMIT, before: str | None = None,
             request: str | None = None) -> dict[str, Any]:
    """The launch log page the route and the `launches` topic send: newest first, or the
    one launch `request` names, with the refusal of a page launch refused before its row
    was written."""
    rows: list[store.LaunchRow] = []
    refused = None
    with _store() as conn:
        if conn is not None and request is not None:
            rows, refused = _requested(conn, request)
        elif conn is not None:
            rows = store.log(conn, limit=limit, before=before)
    after, now = store.stale_after_s(), datetime.now(timezone.utc)
    return _up(launches=[launch_row(row, after, now) for row in rows], refused=refused)


def read_launches(call: Any) -> dict[str, Any]:
    """`GET /api/launches`: the launch log, paged; with `request`, one launch."""
    query = call.query
    _alone(query, "request")
    limit, before = _paging(query)
    with call.busy("read"):
        try:
            return launches(limit=limit, before=before, request=query.get("request"))
        except Exception as err:
            return _failed(err, "reading the launch log failed", launches=[], refused=None)


# -- /api/runs ------------------------------------------------------------------------------


def _event(event: store.RunEvent) -> dict[str, Any]:
    detail = {k: v for k, v in event.detail.items() if k != EVENT_TEXT}
    return {"at": event.at, "kind": event.kind,
            "detail": json.dumps(detail, sort_keys=True, ensure_ascii=False, default=str)}


def run_entry(conn: sqlite3.Connection, run: store.Run) -> dict[str, Any]:
    """One run as the page shows it, with its events, oldest first."""
    return {**_cut(run.as_json(), RUN),
            "events": [_event(e) for e in store.run_events(conn, run.run_id)]}


def read_runs(call: Any) -> dict[str, Any]:
    """`GET /api/runs`: one run (`run`), one launch's runs (`request`, paged), or the
    newest runs (paged), each with its events."""
    query = call.query
    _alone(query, "run")
    limit, before = _paging(query)
    with call.busy("read"):
        try:
            with _store() as conn:
                if conn is None:
                    found: list[store.Run] = []
                elif "run" in query:
                    one = store.get_run(conn, query["run"])
                    found = [] if one is None else [one]
                else:
                    found = store.runs(conn, request_id=query.get("request"), limit=limit,
                                       before=before)
                return _up(runs=[run_entry(conn, run) for run in found])
        except Exception as err:
            return _failed(err, "reading the runs failed", runs=[])


# -- live launches and their state ------------------------------------------------------------


def alive(row: store.LaunchRow) -> bool:
    """Whether the process of the launch `row` runs now: `moxi.proc.alive` on the pid and
    start time it recorded (with no start time, whether any process has that pid), or,
    for a row that recorded no pid, on the endpoint that claimed it. An endpoint that
    can't be read is not alive."""
    if row.pid:
        if row.pid_started is None:
            return proc.identity(row.pid) is not None
        return proc.alive(proc.ProcId(row.pid, row.pid_started))
    parsed = store.parse_endpoint(row.endpoint) if row.endpoint else None
    return parsed is not None and not store.endpoint_gone(row.endpoint or "")


def live(conn: sqlite3.Connection) -> list[store.LaunchRow]:
    """The live launches, newest first: each not `failed` whose process runs now, with
    `newest_session_id` the id it was last known by. One store read and one look at a
    process per row; turns is not asked."""
    found = []
    for row in store.running(conn):
        if alive(row):
            known = store.session_ids(conn, row.request_id)
            found.append(replace(row, newest_session_id=known[-1][0] if known
                                 else row.session_id))
    return found


def _readable(found: state.SessionState) -> dict[str, Any]:
    return {**found.as_json(), "unreadable": None}


def unreadable(row: store.LaunchRow, err: BaseException) -> dict[str, Any]:
    """The entry of a live launch whose state could not be read: the launch, and why,
    naming only the exception's class."""
    _log_once(err, f"reading the state of launch {row.request_id} failed")
    return {"session_id": row.newest_session_id or row.session_id or "",
            "request_id": row.request_id, "relaunch": None, "recycle": None,
            "keep_warm": None, "red": False, "since": None, "why": None, "word": None,
            "run_id": None, "unreadable": UNREADABLE.format(type(err).__name__)}


def live_states(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The state of every live launch, newest first. A launch whose state can't be read
    is marked `unreadable` in its own entry, and the others still stand."""
    api = slots.api()
    found = []
    for row in live(conn):
        try:
            found.append(_readable(state.of_launch(conn, row, slots_api=api)))
        except Exception as err:
            found.append(unreadable(row, err))
    return found


def _refused(err: catalog.Refused) -> dict[str, Any]:
    return {"code": err.code, "row": err.row, "problem": err.problem, "hint": err.hint}


def session_state(session: str | None) -> dict[str, Any]:
    """`/api/state`'s answer: the state of the launched session `session`, or `refused`
    when no launch holds it; with no session, the state of every live launch."""
    with _store() as conn:
        if session is None:
            return _up(sessions=[] if conn is None else live_states(conn), refused=None)
        try:
            if conn is None:
                raise target.NotLaunched()
            got = state.state(session, conn=conn, slots_api=slots.api())
        except target.NotLaunched as err:
            return _up(sessions=[], refused=_refused(err))
        return _up(sessions=[_readable(got)], refused=None)


def read_state(call: Any) -> dict[str, Any]:
    """`GET /api/state`: one session's state (`session`), or every live launch's. A
    session no launch holds is answered `refused` (`not-launched`), not an error."""
    with call.busy("read"):
        try:
            return session_state(call.query.get("session"))
        except Exception as err:
            return _failed(err, "reading the state failed", sessions=[], refused=None)


def read_sessions(call: Any) -> dict[str, Any]:
    """`GET /api/sessions`: the past-sessions list, newest first, paged, each entry's
    liveness read now. A read of one entry that fails marks only that entry."""
    limit, before = _paging(call.query)
    with call.busy("read"):
        try:
            with _store() as conn:
                found = [] if conn is None else past_sessions(
                    limit=limit, before=before, conn=conn).data["sessions"]
            return _up(sessions=found)
        except Exception as err:
            return _failed(err, "reading the past sessions failed", sessions=[])


# -- /api/events and its poller -------------------------------------------------------------


def gather_sessions() -> dict[str, Any]:
    """The `sessions` topic's value: every live launch's state. Never raises."""
    try:
        with _store() as conn:
            found = [] if conn is None else live_states(conn)
    except Exception as err:
        return _failed(err, "gathering the live launches' state failed", sessions=[])
    return _up(sessions=found)


def gather_launches() -> dict[str, Any]:
    """The `launches` topic's value: the newest page of the launch log. Never raises."""
    try:
        found = launches(limit=LIMIT)
    except Exception as err:
        return _failed(err, "gathering the launch log failed", launches=[])
    return {key: found[key] for key in ("state", "detail", "launches")}


class _Version:
    """The store's data version, read on one connection kept open: SQLite moves it each
    time another connection commits. Read from the poller's worker thread, one read at a
    time."""

    def __init__(self) -> None:
        self.conn: sqlite3.Connection | None = None
        self.opened = 0

    def read(self) -> tuple[int, int] | None:
        """`(which connection, its data version)`, or None while the store doesn't exist
        or can't be read. A connection opened again counts as a move, since the version
        is a count kept per connection."""
        try:
            if self.conn is None:
                self.conn = core_store.connect(store.path(), create=False, timeout=1.0,
                                               check_same_thread=False)
                self.opened += 1
            return self.opened, self.conn.execute("PRAGMA data_version").fetchone()[0]
        except Exception as err:
            if not isinstance(err, core_store.StoreError) or isinstance(
                    err, core_store.StoreTooNew):
                _log_once(err, "reading the launch log's data version failed")
            self.close()
            return None

    def close(self) -> None:
        if self.conn is not None:
            with suppress(Exception):
                self.conn.close()
            self.conn = None


class _Feed:
    """The two topics, a Snapshot for each, and the poller, on one event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.topics = lb.Topics(*TOPICS)
        self.snapshots = (lb.Snapshot(gather_sessions, self.topics, "sessions"),
                          lb.Snapshot(gather_launches, self.topics, "launches"))
        self.version = _Version()
        self.task = loop.create_task(self._poll(), name="launcher state poller")

    async def _poll(self) -> None:
        seen: object = _NEVER
        due = 0.0
        while True:
            try:
                version = await aio.offload(self.version.read)
                now = time.monotonic()
                if version != seen or now >= due:
                    seen, due = version, now + REGATHER
                    await self.changed()
            except Exception as err:
                _log_once(err, "the state poller failed")
            await asyncio.sleep(TICK)

    async def changed(self) -> None:
        """Ask each topic for one rebuild; a gather that raises is logged and the other
        still runs."""
        done = await asyncio.gather(*(s.changed() for s in self.snapshots),
                                    return_exceptions=True)
        for result in done:
            if isinstance(result, Exception):
                _log_once(result, "a topic's gather failed")


_NEVER = object()
_current: _Feed | None = None


def _feed() -> _Feed:
    # A page server runs one loop for its life; a loop of its own per in-process test
    # gets a fresh feed, since a task and its Snapshots belong to one loop.
    global _current
    loop = asyncio.get_running_loop()
    if _current is None or _current.loop is not loop:
        _current = _Feed(loop)
    return _current


async def events(call: Any) -> Any:
    """`GET /api/events`: the stream over the `sessions` and `launches` topics. The first
    call starts the poller."""
    return call.stream(_feed().topics)


# -- the routes still to come ----------------------------------------------------------------


def _not_yet(route: str) -> Callable[[Any], Any]:
    def handler(call: Any) -> Any:
        raise lb.Refused(f"this version of the launcher's page does not answer {route} yet")

    handler.__name__ = handler.__qualname__ = route.rsplit("/", 1)[-1]
    return handler


preview = _not_yet("/api/preview")
launch = _not_yet("/api/launch")
resume = _not_yet("/api/resume")

__all__ = ["EVENT_TEXT", "REGATHER", "TICK", "TOPICS", "UNEXPECTED", "UNREADABLE", "WITHHELD",
           "alive", "events", "field", "fields", "gather_launches", "gather_sessions", "launch",
           "launch_row", "launches", "live", "live_states", "preview", "read_catalog",
           "read_launches", "read_presets", "read_runs", "read_sessions", "read_state",
           "refusal", "resume", "run_entry", "session_state", "spelled", "unreadable"]
