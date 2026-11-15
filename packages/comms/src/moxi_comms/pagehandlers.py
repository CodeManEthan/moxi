"""The handlers of comms' page, and the poller that feeds its stream.

The roster and its stream answer from one `api.roster_rows(with_roster=True)`, the rows
`moxi comms agents --json` prints and the `Roster` they were joined from, so a gather asks
the daemon for `agents` once; such a handler cuts each row to the route's declared keys,
adds every name in the roster's `waiting`, and on the read adds the server's `now` and
local date. The mail read, Home's Comms row, answers from one `api.roster()`: the names
in its `waiting` that no live session holds. Each turns a failure into a `state`:

- `DaemonOffline` is `offline` with no detail, since its text names the daemon's socket;
- `DaemonError` is `error` with a fixed sentence naming its class;
- anything else is `error` with a fixed sentence naming only the exception's class.

No exception's text, which may hold a path, reaches the browser. A `DaemonError` and an
unexpected exception are logged once per class with their text and traceback.

The stream's topic is fed by one poller, an asyncio task the stream route starts on its
first call. It gathers at once and then every `PERIOD` seconds for the rest of the
process's life, and its `Snapshot` publishes only when the value changed. The rows carry
`bound_at`, not an elapsed time, and a cache's `until` in whole seconds, not its age, so
the value changes only when a binding, a standing name, whom a session awaits, a cache's
kind or lifetime, or the mail waiting does (or when two reads round either side of a
second, which publishes once more). The poller is not a request, a stream or a busy hold, so it
never delays the server's idle exit. It survives every exception: a failed gather is
logged once per class and the next period gathers again.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import threading
import time
from typing import Any

from moxi import modules

from . import api
from .page import MAIL, ROSTER, ROW

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"comms' page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

#: Seconds between the poller's gathers. Each gather asks the daemon twice per live name.
PERIOD = 10.0
#: Seconds each of a gather's daemon calls may wait.
DEADLINE = 2.0
TOPIC = "roster"
#: The keys of one row, in the order `page.ROW` declares them.
KEYS = tuple(ROW.items)
#: One name of the mail read, as `page.MAIL` declares it.
WAITING = MAIL["waiting"].items
#: One name with mail queued on the roster, as `page.ROSTER` declares it.
QUEUED = ROSTER["waiting"].items

#: What the page shows for a daemon that answered with an error, and for any other
#: failure. Each names only the exception's class.
DAEMON_ERROR = ("the comms daemon answered the roster read with an error ({}); "
                "the page server's log has the details")
UNEXPECTED = "comms' page server hit an unexpected error ({}); its log has the details"

log = logging.getLogger("moxi.comms.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


def _fits(value: Any, shape: Any) -> bool:
    # As the route's projection judges a value of `ROW`, so a row it would refuse is
    # caught here and the read ends in `state` `error` rather than a 500.
    if value is None:
        return shape.nullable
    if shape.type == "string":
        return isinstance(value, str)
    if shape.type == "bool":
        return isinstance(value, bool)
    if shape.type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if shape.type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False
    if shape.type == "list":
        return (isinstance(value, (list, tuple))
                and all(_fits(item, shape.items) for item in value))
    if shape.type == "table":
        return (isinstance(value, dict) and set(value) == set(shape.items)
                and all(_fits(value[key], item) for key, item in shape.items.items()))
    return False


def _row(row: api.RosterRow) -> dict:
    out = {key: getattr(row, key, None) for key in KEYS}
    for key in ("directors", "awaiting"):
        out[key] = list(out[key] or ())
    if out["cache"] is not None:
        out["cache"] = {key: getattr(out["cache"], key, None)
                        for key in ROW.items["cache"].items}
    for key, value in out.items():
        if not _fits(value, ROW.items[key]):
            raise ValueError(f"a roster row's {key} is not of its declared type")
    return out


def _today() -> str:
    """The page server's local date, `YYYY-MM-DD`."""
    return dt.date.today().isoformat()


def gather() -> dict[str, Any]:
    """The `roster` topic's value: `state`, `detail`, the rows and every name with mail
    waiting. Never raises."""
    try:
        found_rows, found = api.roster_rows(timeout_s=DEADLINE, with_roster=True)
        rows = [_row(row) for row in found_rows]
        waiting = _queued(found.waiting)
    except api.DaemonOffline:
        return {"state": "offline", "detail": None, "rows": [], "waiting": []}
    except api.DaemonError as exc:
        _log_once(exc, "the comms daemon answered the roster read with an error")
        return {"state": "error", "detail": DAEMON_ERROR.format(type(exc).__name__),
                "rows": [], "waiting": []}
    except Exception as exc:
        _log_once(exc, "reading the roster failed")
        return {"state": "error", "detail": UNEXPECTED.format(type(exc).__name__),
                "rows": [], "waiting": []}
    return {"state": "up", "detail": None, "rows": rows, "waiting": waiting}


def _queued(found: Any) -> list[dict[str, Any]]:
    # Each `api.Waiting` as `{qualified, count, since}`, oldest `since` first, `since` a
    # float; a value its declared type can't carry raises `ValueError`.
    rows = []
    for waiting in found:
        out = {"qualified": waiting.qualified, "count": waiting.count,
               "since": waiting.oldest}
        for key, value in out.items():
            if not _fits(value, QUEUED.items[key]):
                raise ValueError(f"a waiting name's {key} is not of its declared type")
        out["since"] = float(out["since"])
        rows.append(out)
    rows.sort(key=lambda row: (row["since"], row["qualified"]))
    return rows


def _waiting(found: api.Roster) -> list[dict[str, Any]]:
    # A bound name is left out: its mail waits on a session that runs, not on someone to
    # start one. A name is shown by the part before `@`, unless another name here
    # shares it.
    bound = {agent.qualified for agent in found.bound}
    rows = [{"qualified": row["qualified"], "name": None, "count": row["count"],
             "since": row["since"]}
            for row in _queued(w for w in found.waiting if w.qualified not in bound)]
    short = [api.split_tag(row["qualified"])[0] for row in rows]
    for row, name in zip(rows, short, strict=True):
        row["name"] = name if short.count(name) == 1 else row["qualified"]
    return rows


def mail(call: Any) -> dict[str, Any]:
    """`GET /api/mail`: `state`, `detail`, and the names with mail waiting that no live
    session holds, oldest `since` first, from one daemon call. A failure is a 200 with
    `state` `offline` or `error`, never a 500."""
    try:
        waiting = _waiting(api.roster(timeout_s=DEADLINE))
    except api.DaemonOffline:
        return {"state": "offline", "detail": None, "waiting": []}
    except api.DaemonError as exc:
        _log_once(exc, "the comms daemon answered the mail read with an error")
        return {"state": "error", "detail": DAEMON_ERROR.format(type(exc).__name__),
                "waiting": []}
    except Exception as exc:
        _log_once(exc, "reading the mail failed")
        return {"state": "error", "detail": UNEXPECTED.format(type(exc).__name__),
                "waiting": []}
    return {"state": "up", "detail": None, "waiting": waiting}


def roster(call: Any) -> dict[str, Any]:
    """`GET /api/roster`: the topic's value now, with the server's `now` and local date
    (`today`). A failure is a 200 with `state` `offline` or `error`, never a 500."""
    value = gather()
    return {"now": time.time(), "today": _today(), **value}


async def events(call: Any) -> Any:
    """`GET /api/events`: the stream over the `roster` topic. The first call starts the
    poller."""
    return call.stream(_feed().topics)


class _Feed:
    """The topic, its Snapshot and the poller, on one event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.topics = lb.Topics(TOPIC)
        self.snapshot = lb.Snapshot(gather, self.topics, TOPIC)
        self.task = loop.create_task(self._poll(), name="comms roster poller")

    async def _poll(self) -> None:
        while True:
            try:
                await self.snapshot.on_changed()
            except Exception as exc:
                _log_once(exc, "the roster poller's gather failed")
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


__all__ = ["DAEMON_ERROR", "DEADLINE", "KEYS", "PERIOD", "QUEUED", "TOPIC", "UNEXPECTED",
           "WAITING", "events", "gather", "mail", "roster"]
