"""The handlers of cache's page, and the poller that feeds its stream.

Every answer comes from `api.clock_records()`, which `moxi cache timers` lists through.
A handler only cuts each record to the route's declared keys (a key the record lacks is
sent as null), adds the server's `now` to the read, and turns any failure into `state`
`error` with a fixed `detail` naming only the exception's class, so no message text, which
may hold a path, reaches the browser. The class and its traceback are logged once.

The stream's topic is fed by one poller, an asyncio task the stream route starts on its
first call. It gathers at once and then every `PERIOD` seconds for the rest of the
process's life, and its `Snapshot` publishes only when the list changed. The poller is
not a request, a stream or a busy hold, so it never delays the server's idle exit. It
survives every exception: a failed gather is logged once per class and the next period
gathers again.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from typing import Any

from moxi import modules

from . import api
from .page import ROW

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

log = logging.getLogger("moxi.cache.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
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


def gather() -> dict[str, Any]:
    """The `clocks` topic's value: `state`, `detail` and the rows. Never raises."""
    try:
        rows = [_row(record) for record in api.clock_records()]
    except Exception as err:
        _log_once(err, "listing the clock records failed")
        return {"state": "error",
                "detail": f"cache's page server hit an unexpected error "
                          f"({type(err).__name__}); its log has the details",
                "clocks": []}
    return {"state": "up", "detail": None, "clocks": rows}


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


__all__ = ["KEYS", "PERIOD", "TOPIC", "clocks", "events", "gather"]
