"""The handlers of cache's page, and the poller that feeds its stream.

Every answer comes from `api.clock_records()`, which `moxi cache timers` lists through,
and from the watcher's reading, `records.reading()`. A handler only cuts each record and
the reading to the route's declared keys (a key a record lacks is sent as null), adds the
server's local date as `today` and, on the read, the server's `now`, and turns any failure
into `state` `error` with a fixed `detail` naming only the exception's class and the
reading null, so no message text, which may hold a path, reaches the browser. The class
and its traceback are logged once.

The stream's topic is fed by one poller, an asyncio task the stream route starts on its
first call. It gathers at once and then every `PERIOD` seconds for the rest of the
process's life, and its `Snapshot` publishes only when the value changed: a record, the
reading (its `tick_at` moves at each of the watcher's ticks, records or none) or the
date. The poller is
not a request, a stream or a busy hold, so it never delays the server's idle exit. It
survives every exception: a failed gather is logged once per class and the next period
gathers again.

`names` and `session` read the clock records for their keys and ask sessions, which cache
requires, in this process. `names` asks `session_facts` for every record's
`(harness, session)`, at most `FACTS_MAX` keys a call, and answers one item per record in
the records' order; `session` asks `session_opened` for the one session's model and sends
nothing else of what it read. A raise in either is logged once per class: `names` then
answers `state` `error` with no sessions, and `session` a null model.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

from moxi import modules
from moxi_sessions import api as sessions

from . import api, records
from .page import READING, ROW

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"cache's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

#: Seconds between the poller's gathers. A record changes at each of the watcher's ticks
#: (10 s by default), so the topic publishes about once a tick.
PERIOD = 2.0
TOPIC = "clocks"
#: The keys of one row, in the order `page.ROW` declares them.
KEYS = tuple(ROW.items)
#: The keys of the reading, in the order `page.READING` declares them.
READING_KEYS = tuple(READING.items)
#: The most keys one call of sessions' `session_facts` takes.
FACTS_MAX = 200
#: The harnesses sessions names and opens; a record of any other is not asked about.
SESSION_HARNESSES = ("claude", "codex", "opencode")

log = logging.getLogger("moxi.cache.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str, route: str | None = None) -> None:
    # The clocks read and its poller share one set of classes; each other route has its own.
    key = type(err) if route is None else (route, type(err))
    with _logged_lock:
        first = key not in _logged
        _logged.add(key)
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


def _fits(value: Any, shape: Any) -> bool:
    # As the route's projection judges a value of `ROW`, so a record it would refuse is
    # caught here and the read ends in `state` `error` rather than a 500.
    if value is None:
        return shape.nullable
    if shape.type == "string":
        return isinstance(value, str)
    if shape.type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False
    return False


def _row(record: dict) -> dict:
    row = {key: record.get(key) for key in KEYS}
    for key, value in row.items():
        if not _fits(value, ROW.items[key]):
            raise ValueError(f"a clock record's {key} is not of its declared type")
    return row


def _reading() -> dict | None:
    found = records.reading()
    if found is None:
        return None
    reading = {key: found[key] for key in READING_KEYS}
    for key, value in reading.items():
        if not _fits(value, READING.items[key]):
            raise ValueError(f"the watcher's reading's {key} is not of its declared type")
    return reading


def _today() -> str:
    """The page server's local date, `YYYY-MM-DD`."""
    return dt.date.today().isoformat()


def gather() -> dict[str, Any]:
    """The `clocks` topic's value: `state`, `detail`, `today`, the reading and the rows.
    Never raises."""
    today = _today()
    try:
        rows = [_row(record) for record in api.clock_records()]
        reading = _reading()
    except Exception as err:
        _log_once(err, "listing the clock records or the reading failed")
        return {"state": "error",
                "detail": f"cache's page server hit an unexpected error "
                          f"({type(err).__name__}); its log has the details",
                "today": today, "reading": None, "clocks": []}
    return {"state": "up", "detail": None, "today": today, "reading": reading,
            "clocks": rows}


def clocks(call: Any) -> dict[str, Any]:
    """`GET /api/clocks`: the topic's value now, with the server's `now`. A failure is a
    200 with `state` `error`, never a 500."""
    value = gather()
    return {"now": time.time(), **value}


async def events(call: Any) -> Any:
    """`GET /api/events`: the stream over the `clocks` topic. The first call starts the
    poller."""
    return call.stream(_feed().topics)


class _Feed:
    """The topic, its Snapshot and the poller, on one event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.topics = lb.Topics(TOPIC)
        self.snapshot = lb.Snapshot(gather, self.topics, TOPIC)
        self.task = loop.create_task(self._poll(), name="cache clocks poller")

    async def _poll(self) -> None:
        while True:
            try:
                await self.snapshot.changed()
            except Exception as err:
                _log_once(err, "the clocks poller's gather failed")
            await asyncio.sleep(PERIOD)


_current: _Feed | None = None


def _feed() -> _Feed:
    # A page server runs one loop for its life; a loop of its own per in-process test
    # gets a fresh feed, since a task and its Snapshot belong to one loop.
    global _current
    loop = asyncio.get_running_loop()
    if _current is None or _current.loop is not loop:
        _current = _Feed(loop)
    return _current


def _home() -> str | None:
    """The account's home folder, or None when it cannot be found."""
    try:
        return str(Path.home())
    except (RuntimeError, KeyError, OSError):
        return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _when(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _facts(keys: list[tuple[str, str]]) -> tuple[dict, set[str]]:
    """What sessions names each key by, asked `FACTS_MAX` keys at a time, and the
    harnesses whose root was not listed whole."""
    found: dict[tuple[str, str], dict] = {}
    unlisted: set[str] = set()
    for start in range(0, len(keys), FACTS_MAX):
        answer = sessions.session_facts(keys[start:start + FACTS_MAX])
        for item in answer["sessions"]:
            found[(item["harness"], item["session"])] = item
        unlisted.update(root["harness"] for root in answer["roots"])
    return found, unlisted


def names(call: Any) -> dict[str, Any]:
    """`GET /api/names`: one item per clock record, in the records' order, with what
    sessions names it by. A failure is a 200 with `state` `error` and no sessions."""
    today, home = _today(), _home()
    try:
        found = [(record["harness"], record["session"]) for record in api.clock_records()]
        asked = [key for key in found if key[0] in SESSION_HARNESSES]
        facts, unlisted = _facts(asked)
    except Exception as err:
        _log_once(err, "naming the sessions with a clock record failed", "names")
        return {"state": "error", "today": today, "home": home, "sessions": []}
    out = []
    for harness, session in found:
        item = facts.get((harness, session))
        if item is not None:
            known: bool | None = True
        elif harness in SESSION_HARNESSES and harness not in unlisted:
            known = False
        else:
            known = None
        item = item or {}
        out.append({"session": session, "harness": harness,
                    "name": _text(item.get("name")), "folder": _text(item.get("folder")),
                    "started": _when(item.get("started")), "known": known})
    return {"state": "up", "today": today, "home": home, "sessions": out}


def session(call: Any) -> dict[str, Any]:
    """`GET /api/session?session=`: the model of the session with that clock record. Null
    with no such record, a session sessions does not hold, and a raise."""
    wanted = call.query["session"]
    try:
        harness = next((record["harness"] for record in api.clock_records()
                        if record["session"] == wanted), None)
        if harness not in SESSION_HARNESSES:
            return {"model": None}
        opened = sessions.session_opened(harness=harness, session=wanted)
    except Exception as err:
        _log_once(err, "reading the model of a session with a clock record failed",
                  "session")
        return {"model": None}
    model = opened.get("model") if isinstance(opened, dict) else None
    return {"model": model if isinstance(model, str) and model else None}


__all__ = ["FACTS_MAX", "KEYS", "PERIOD", "READING_KEYS", "SESSION_HARNESSES", "TOPIC",
           "clocks", "events", "gather", "names", "session"]
