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

**A launch from the page** goes in two steps. `/api/preview` reads the form's values as
typed, as the command line reads them: each becomes its row's option and goes through
`catalog.parse` for the launch's harness, so a value the page sends overrides the preset
and `[defaults]`, as one typed does. It answers `api.preview`'s composition beside every
refusal `api.check` finds, and, for a launch not in a terminal, whether the command is on
the PATH the launch job would start it with, which is this server's (a refusal
`not-found` naming that PATH). A launch that names no directory, project or scratch is
refused `no-cwd`: the server's own directory means nothing to the user. When nothing
refuses it and its target isn't `terminal`, the preview issues a ticket: `<request
id>.<expiry>.<hmac>`, an HMAC-SHA256 over the request as sent, its request id and its
expiry, with a 32-byte key kept in `STATE/page.key` (0600, made at first use), living
`page.launch_wait_s` seconds. A terminal launch gets the `moxi launcher run` command to
copy instead: the page never runs one, since it would take the server's place.

`/api/launch` takes the request and its ticket. A ticket not issued for exactly this
request is refused `bad-ticket`, an old one `expired`. It writes the request file
(`STATE/runs/<request id>.request.json`, 0600, made only if it is not there) and spawns
`moxi-launcher-job launch <request id>` detached, then answers 202 with the status path
`/api/launches?request=<request id>`, which shows the launch's row once it is written, or
the refusal the job recorded before it. A ticket used a second time is a 409, whether its
request file is still there or the launch log already knows its id.

`/api/resume` takes a session id, and a slot in place of the launch's own. It answers the
refusals a resume meets before anything runs, as `moxi launcher resume` words them: no
launch holds the id, the launch ran headless (nothing to resume it into), it has no session
id, its harness is no longer launched, its session is live now (liveness read at the call),
or its liveness can't be read. A launch that ran in a terminal is not resumed here: the
answer is the `moxi launcher resume <id>` command to copy, since run here it would take the
server's place. A slot launch is resumed by the same detached job as a launch: a request
file whose act is `resume`, under a new request id that becomes the new launch's, and a 202
with the status path holding that id. What the resume meets later (an open run, a slot
slots won't give, another resume of the session starting first) the job records as it
records a launch's refusal.

A handler cuts each answer to its route's declared keys, and turns any failure it did not
expect into `state` `error` with a fixed `detail` naming only the exception's class, so no
message text, which may hold a path, reaches the browser. The class and its traceback are
logged once. A query the read can't take (a `before` that is not a time, a `limit` under
1, `request` with paging) is refused (422) with what to send instead.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moxi import aio, files, modules, paths, proc
from moxi import store as core_store
from moxi.config import ConfigError
from moxi.paths import Layout

from . import api, catalog, first, job, presets, settings, slots, state, store, target, targets
from . import resume as resuming
from .compose import common
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


# -- the ticket -----------------------------------------------------------------------------

#: The ticket key's file under the launcher's state root, and its length in bytes.
KEY_FILE = "page.key"
KEY_BYTES = 32
_key_lock = threading.Lock()


def page_key(env: Mapping[str, str] | None = None) -> bytes:
    """The key tickets are signed with: 32 random bytes in `STATE/page.key` (0600), made
    at first use. A file of any other length is replaced, which voids the tickets out."""
    path = Layout(store.MODULE).state_dir(env) / KEY_FILE
    with _key_lock:
        try:
            key = path.read_bytes()
        except FileNotFoundError:
            key = b""
        if len(key) != KEY_BYTES:
            key = secrets.token_bytes(KEY_BYTES)
            files.private_dir(path.parent)
            files.atomic_write(path, key, mode=0o600)
        return key


def canonical(body: Mapping[str, Any], request_id: str, expires: int) -> bytes:
    """The request a ticket is issued for, as the bytes it signs: the preset, the values
    and `off_posture` as the page sent them, the request id and the expiry."""
    asked = {"seat": body.get("seat"), "project_preset": body.get("project_preset"),
             "values": body.get("values") or {},
             "off_posture": bool(body.get("off_posture", False)),
             "request_id": request_id, "expires": expires}
    return json.dumps(asked, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def _signed(body: Mapping[str, Any], request_id: str, expires: int) -> str:
    return hmac.new(page_key(), canonical(body, request_id, expires),
                    hashlib.sha256).hexdigest()


def ticket(body: Mapping[str, Any], request_id: str,
           now: float | None = None) -> tuple[str, str]:
    """A ticket to launch `body` as `request_id`, and when it expires: it lives
    `page.launch_wait_s` seconds. It is `<request id>.<expiry>.<hmac>`."""
    wait = int(settings.current()["page.launch_wait_s"])
    expires = int(time.time() if now is None else now) + wait
    at = store.stamp(datetime.fromtimestamp(expires, timezone.utc))
    return f"{request_id}.{expires}.{_signed(body, request_id, expires)}", at


def verify(body: Mapping[str, Any],
           now: float | None = None) -> tuple[str | None, dict[str, Any] | None]:
    """`(request id, None)` when `body`'s ticket was issued for exactly this request and
    has not expired, else `(None, refusal)`."""
    parts = str(body.get("ticket", "")).rsplit(".", 2)
    if len(parts) == 3 and common.plain_name(parts[0]) and parts[1].isdigit():
        request_id, expires = parts[0], int(parts[1])
        if hmac.compare_digest(parts[2], _signed(body, request_id, expires)):
            if (time.time() if now is None else now) <= expires:
                return request_id, None
            return None, {"code": "expired", "row": None,
                          "problem": "the ticket for this launch has expired",
                          "hint": "preview the launch again"}
    return None, {"code": "bad-ticket", "row": None,
                  "problem": "the ticket was not issued for this launch as it is now",
                  "hint": "preview the launch again, and launch what it shows"}


# -- the form's values as a launch request ------------------------------------------------------


def _refusal(error: BaseException) -> dict[str, Any]:
    return job.refused_detail(error)


def _is_path(row: catalog.Row) -> bool:
    return row.type == "path" or (row.type == "list" and row.items == "path")


def tokens(values: Mapping[str, Any], harness: str,
           available: Callable[[str], bool]) -> tuple[list[str], dict[str, object]]:
    """The form's values as the words a `harness` launch's command line would type,
    each with its row's option, and the values the command line can't type (an optional
    switch set off, a list emptied), already typed. A value spelled as the command line
    spells it; a path must be absolute (or start with `~`), since the page server's own
    directory means nothing to the user. Raises a refusal for a row the harness doesn't
    have or this install doesn't offer, and for a bad spelling."""
    words: list[str] = []
    typed: dict[str, object] = {}
    extra: list[str] = []
    for name, value in values.items():
        row = catalog.row(harness, name)
        if row is None:
            raise catalog.BadValue(name, f"{name} is not a {harness} row or a launcher row",
                                   f"leave {name} out of a {harness} launch")
        if name == catalog.EXTRA:
            extra = [str(word) for word in value]
            continue
        items = value if isinstance(value, list) else [value]
        if _is_path(row):
            for item in items:
                if item and not Path(item).expanduser().is_absolute():
                    raise catalog.BadValue(name, f"{name} is the relative path {item!r}",
                                           "give an absolute path, or one starting with ~")
        option = row.option
        if isinstance(value, list):
            if value:
                words += [f"{option}={item}" for item in value]
                continue
            direct: object = []
        elif row.type == "bool":
            if value not in ("true", "false"):
                raise catalog.BadValue(name, f"{name} is {value!r}, not true or false",
                                       f"set {name} to true or false")
            words.append(option if value == "true" else f"--no-{option[2:]}")
            continue
        elif row.optional_value and value in ("true", "false"):
            if value == "true":
                words.append(option)
                continue
            direct = False
        else:
            words.append(f"{option}={value}")
            continue
        why = row.unoffered(available, harness)
        if why is not None:
            raise catalog.NeedsModule(row, *why)
        typed[name] = row.check(direct)
    return words + (["--", *extra] if extra else []), typed


def launch_request(body: Mapping[str, Any], request_id: str):
    """The `api.LaunchRequest` the page's `body` asks for, under `request_id`, and the
    words of its `moxi launcher run` command. Each value is read as typed, as the command
    line reads it: its words go through `catalog.parse` for `harness`'s rows (the value
    set, else the preset's, else `[defaults]`'s), so a value the page sends overrides the
    preset even when it is the shipped default. Raises a refusal."""
    seat = body.get("seat") or None
    project_preset = body.get("project_preset") or None
    values = dict(body.get("values") or {})
    available = _available()
    harness = values.get("harness") or presets.harness_of(
        presets.Request(seat=seat, project_preset=project_preset))
    if harness not in catalog.HARNESSES:
        raise catalog.BadValue("harness", f"harness cannot be {harness!r}",
                               f"set it to one of {', '.join(catalog.HARNESSES)}")
    words, direct = tokens(values, harness, available)
    parsed = catalog.parse(words, harness, available=available)
    off_posture = bool(body.get("off_posture", False))
    request = api.LaunchRequest(seat=seat, project_preset=project_preset,
                                typed={**parsed.values, **direct}, extra=parsed.extra,
                                off_posture=off_posture, request_id=request_id)
    command = ["moxi", "launcher", "run"]
    if seat:
        command += ["--seat", seat]
    if project_preset:
        command += ["--project-preset", project_preset]
    if off_posture:
        command.append("--off-posture")
    return request, command + words


def _command(words: list[str]) -> str:
    from .cli import command_line

    return command_line(words)


# -- /api/preview ---------------------------------------------------------------------------

#: The refusals a launch request meets in the launcher's own words.
REFUSALS = (catalog.Refused, ConfigError, core_store.StoreError)
#: What the page shows when the harness is not found where the launch would look.
NOT_ON_PATH = "{command} is not found on the PATH the launcher's page server searched: {path}"
NOT_ON_PATH_HINT = ("install it in a folder on that PATH, or give the UI shell a PATH that "
                    "holds it")


def _previewed(**answer: Any) -> dict[str, Any]:
    empty = {"refused": [], "harness": None, "mode": None, "target": None, "argv": [],
             "stamps": [], "first_turn": None, "not_applied": [], "warnings": [],
             "command": None, "request_id": None, "ticket": None, "expires_at": None}
    return {**empty, **answer}


def on_path(composition: Any) -> dict[str, Any] | None:
    """The refusal of a launch whose command is not found on the PATH the launch job
    would start it with, the page server's, naming that PATH; None when it is found. The
    check is the target's own, as `launch` makes it."""
    env = common.environment(os.environ, composition, modules.session_env())
    try:
        targets.get(composition.target).prepare(composition, env)
    except targets.NoCommand as error:
        return {"code": error.code, "row": None,
                "problem": NOT_ON_PATH.format(command=error.command,
                                              path=env.get("PATH", os.defpath)),
                "hint": NOT_ON_PATH_HINT}
    return None


def previewed(body: Mapping[str, Any]) -> dict[str, Any]:
    """What `/api/preview` answers for the page's `body`: what the launch would start,
    every refusal `check` finds, and, when there is none and the target isn't
    `terminal`, a ticket to launch it. A terminal launch is answered with the command to
    copy instead."""
    request_id = str(uuid.uuid4())
    try:
        request, words = launch_request(body, request_id)
        job.placed(request)
        composition = api.preview(request)
        checked = [_refusal(found) for found in api.check(request)]
    except REFUSALS as error:
        return _previewed(refused=[_refusal(error)])
    terminal = composition.target == "terminal"
    refused: list[dict[str, Any]] = []
    for found in [*checked, None if terminal else on_path(composition)]:
        if found is not None and found not in refused:
            refused.append(found)
    issued, expires_at = (None, None) if refused or terminal else ticket(body, request_id)
    return _previewed(
        refused=refused, harness=composition.harness, mode=composition.mode,
        target=composition.target, argv=list(composition.argv),
        stamps=[{"name": str(k), "value": str(v)} for k, v in composition.env.items()],
        first_turn=composition.first, not_applied=list(composition.not_applied),
        warnings=list(composition.warnings),
        command=_command(words) if terminal else None,
        request_id=None if issued is None else request_id, ticket=issued,
        expires_at=expires_at)


def _unexpected(err: BaseException, what: str) -> dict[str, Any]:
    _log_once(err, what)
    return {"code": "error", "row": None, "problem": UNEXPECTED.format(type(err).__name__),
            "hint": "try again; if it fails the same way, read the page server's log"}


def preview(call: Any) -> dict[str, Any]:
    """`POST /api/preview`: what the launch the form describes would start, its
    refusals, and a ticket to launch it."""
    with call.busy("preview"):
        try:
            return previewed(call.body)
        except Exception as err:
            return _previewed(refused=[_unexpected(err, "previewing a launch failed")])


# -- /api/launch ----------------------------------------------------------------------------

#: The 409 of a ticket used a second time.
USED = "the ticket for launch {} was used already; preview the launch again to launch it again"
#: The 409 of a resume's request id used a second time.
RESUME_USED = ("the request for launch {} was used already; resume the session from the page "
               "again")
#: The status path a page launch's 202 points to.
STATUS = "/api/launches?request={}"

# The call the launch job is started through, named here so a test can stand in for it.
_spawn = proc.spawn


def _new_request_id() -> str:
    return str(uuid.uuid4())


def _known(request_id: str) -> bool:
    """Whether the launch log has `request_id`'s row, or its refusal."""
    with _store() as conn:
        if conn is None:
            return False
        rows, refused = _requested(conn, request_id)
        return bool(rows) or refused is not None


def _start_job(request_id: str) -> None:
    env = common.scrub(os.environ, modules.session_env())
    argv = [str(paths.script("moxi-launcher-job")), job.PAGE, request_id]
    _spawn(argv, detached=True, env=env, cwd=str(Layout(store.MODULE).state_dir()),
           output=targets.run_log(request_id))


def launched(body: Mapping[str, Any]) -> Any:
    """What `/api/launch` answers for the page's `body`: 202 with the status path once the
    launch job is started, a refusal before that, or a 409 for a ticket used already."""
    request_id, refused = verify(body)
    if refused is not None:
        return {"refused": refused, "command": None}
    try:
        request, words = launch_request(body, request_id)
        resolved = job.placed(request)
    except REFUSALS as error:
        return {"refused": _refusal(error), "command": None}
    if resolved.target == "terminal":
        return {"refused": {"code": "terminal", "row": "target",
                            "problem": "a launch in a terminal is not run by the page",
                            "hint": "run its command in a terminal of your own"},
                "command": _command(words)}
    return _run_job(request_id, {
        "act": "launch", "request_id": request_id, "seat": request.seat,
        "project_preset": request.project_preset, "typed": dict(request.typed),
        "extra": list(request.extra), "off_posture": request.off_posture})


def _run_job(request_id: str, data: Mapping[str, Any], used: str = USED) -> Any:
    """Write `data` as `request_id`'s request file and start the launch job on it: 202
    with the status path, a refusal when the job did not start, or a 409 when the request
    id was used already (its file is there, or the launch log knows it)."""
    try:
        path = job.write_request(request_id, data)
    except FileExistsError:
        raise lb.Conflict(used.format(request_id)) from None
    if _known(request_id):
        path.unlink(missing_ok=True)
        raise lb.Conflict(used.format(request_id))
    try:
        _start_job(request_id)
    except Exception as err:
        path.unlink(missing_ok=True)
        _log_once(err, "the launch job did not start")
        return {"refused": {"code": targets.SPAWN_FAILED, "row": None,
                            "problem": f"the launch job did not start ({type(err).__name__})",
                            "hint": "try again; the page server's log has the details"},
                "command": None}
    return lb.Accepted(STATUS.format(request_id))


def launch(call: Any) -> Any:
    """`POST /api/launch`: the launch a ticket was issued for, written as a request file
    and run by a detached launch job (202). Never run here, and never for a terminal."""
    with call.busy("launch"):
        try:
            return launched(call.body)
        except (lb.Conflict, lb.Refused):
            raise
        except Exception as err:
            return {"refused": _unexpected(err, "a launch from the page failed"),
                    "command": None}


# -- /api/resume ----------------------------------------------------------------------------


def resumable(session: str) -> store.LaunchRow:
    """The launch the session id `session` belongs to, when a resume of it may go ahead.
    Raises the refusal it meets before anything runs: `not-launched`, or `resume`'s own,
    whatever the session's state (`headless` among them) and then its liveness, read now."""
    with _store() as conn:
        row = None if conn is None else store.resolve(conn, session)
    if row is None:
        raise target.NotLaunched()
    newest = row.newest_session_id or row.session_id
    refused = resuming.static_refusal(row, newest)
    if refused is None:
        live, unread = resuming.liveness(row, newest, first.turns_api(quiet=True))
        refused = resuming.live_refusal(row, newest, live, unread)
    if refused is not None:
        raise refused
    return row


def resumed(body: Mapping[str, Any]) -> Any:
    """What `/api/resume` answers for the page's `body`: a refusal, the command to copy
    for a launch that ran in a terminal, or, for a slot launch, 202 with the status path
    once the job that resumes it is started."""
    session = str(body.get("session") or "").strip()
    slot = str(body.get("slot") or "").strip() or None
    try:
        row = resumable(session)
    except REFUSALS as error:
        return {"refused": _refusal(error), "command": None}
    if row.target != "slot":
        if slot is not None:
            return {"refused": {"code": "bad-value", "row": "slot",
                                "problem": f"launch {row.request_id} ran in its {row.target}, "
                                           "and only a slot launch resumes in a slot",
                                "hint": "resume it without a slot"},
                    "command": None}
        return {"refused": None,
                "command": _command(["moxi", "launcher", "resume", session])}
    request_id = _new_request_id()
    return _run_job(request_id, {"act": "resume", "request_id": request_id,
                                 "session": session, "slot": slot}, used=RESUME_USED)


def resume(call: Any) -> Any:
    """`POST /api/resume`: a past session resumed. A slot launch's resume runs in a
    detached launch job (202); a terminal launch's is answered with the command to copy."""
    with call.busy("resume"):
        try:
            return resumed(call.body)
        except (lb.Conflict, lb.Refused):
            raise
        except Exception as err:
            return {"refused": _unexpected(err, "a resume from the page failed"),
                    "command": None}

__all__ = ["EVENT_TEXT", "KEY_BYTES", "KEY_FILE", "NOT_ON_PATH", "REFUSALS", "REGATHER",
           "RESUME_USED", "STATUS", "TICK", "TOPICS", "UNEXPECTED", "UNREADABLE", "USED",
           "WITHHELD", "alive", "canonical", "events", "field", "fields", "gather_launches",
           "gather_sessions", "launch", "launch_request", "launch_row", "launched", "launches",
           "live", "live_states", "on_path", "page_key", "preview", "previewed",
           "read_catalog", "read_launches", "read_presets", "read_runs", "read_sessions",
           "read_state", "refusal", "resumable", "resume", "resumed", "run_entry",
           "session_state", "spelled", "ticket", "tokens", "unreadable", "verify"]
