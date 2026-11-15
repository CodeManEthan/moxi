"""The handlers of comms' page, and the poller that feeds its stream.

The roster and its stream answer from `api.roster_rows()`, which `moxi comms agents
--json` prints; such a handler only cuts each row to the route's declared keys and adds
the server's `now` to the read. The mail read, Home's Comms row, answers from one
`api.roster()`: the names in its `waiting` that no live session holds. Each turns a
failure into a `state`:

- `DaemonOffline` is `offline` with no detail, since its text names the daemon's socket;
- `DaemonError` is `error` with a fixed sentence naming its class;
- anything else is `error` with a fixed sentence naming only the exception's class.

No exception's text, which may hold a path, reaches the browser. A `DaemonError` and an
unexpected exception are logged once per class with their text and traceback.

The stream's topic is fed by one poller, an asyncio task the stream route starts on its
first call. It gathers at once and then every `PERIOD` seconds for the rest of the
process's life, and its `Snapshot` publishes only when the rows changed. The rows carry
`bound_at`, not an elapsed time, so they change only when a binding, a standing name or
whom a session awaits does. The poller is not a request, a stream or a busy hold, so it
never delays the server's idle exit. It survives every exception: a failed gather is
logged once per class and the next period gathers again.

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
from .page import MAIL, ROW

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
    return False


def _row(row: api.RosterRow) -> dict:
    out = {key: getattr(row, key, None) for key in KEYS}
    for key in ("directors", "awaiting"):
        out[key] = list(out[key] or ())
    for key, value in out.items():
        if not _fits(value, ROW.items[key]):
            raise ValueError(f"a roster row's {key} is not of its declared type")
    return out


def gather() -> dict[str, Any]:
    """The `roster` topic's value: `state`, `detail` and the rows. Never raises."""
    try:
        rows = [_row(row) for row in api.roster_rows(timeout_s=DEADLINE)]
    except api.DaemonOffline:
        return {"state": "offline", "detail": None, "rows": []}
    except api.DaemonError as exc:
        _log_once(exc, "the comms daemon answered the roster read with an error")
        return {"state": "error", "detail": DAEMON_ERROR.format(type(exc).__name__),
                "rows": []}
    except Exception as exc:
        _log_once(exc, "reading the roster failed")
        return {"state": "error", "detail": UNEXPECTED.format(type(exc).__name__),
                "rows": []}
    return {"state": "up", "detail": None, "rows": rows}


def _waiting(found: api.Roster) -> list[dict[str, Any]]:
    # A bound name is left out: its mail waits on a session that runs, not on someone to
    # start one. A name is shown by the part before `@`, unless another name here
    # shares it.
    bound = {agent.qualified for agent in found.bound}
    rows = []
    for waiting in found.waiting:
        if waiting.qualified in bound:
            continue
        out = {"qualified": waiting.qualified, "name": None, "count": waiting.count,
               "since": waiting.oldest}
        for key in ("qualified", "count", "since"):
            if not _fits(out[key], WAITING.items[key]):
                raise ValueError(f"a waiting name's {key} is not of its declared type")
        out["since"] = float(out["since"])
        rows.append(out)
    rows.sort(key=lambda row: (row["since"], row["qualified"]))
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
    except api.DaemonError as err:
        _log_once(err, "the comms daemon answered the mail read with an error")
        return {"state": "error", "detail": DAEMON_ERROR.format(type(err).__name__),
                "waiting": []}
    except Exception as err:
        _log_once(err, "reading the mail failed")
        return {"state": "error", "detail": UNEXPECTED.format(type(err).__name__),
                "waiting": []}
    return {"state": "up", "detail": None, "waiting": waiting}


def roster(call: Any) -> dict[str, Any]:
    """`GET /api/roster`: the topic's value now, with the server's `now`. A failure is a
    200 with `state` `offline` or `error`, never a 500."""
    value = gather()
    return {"now": time.time(), **value}


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


__all__ = ["DAEMON_ERROR", "DEADLINE", "KEYS", "PERIOD", "TOPIC", "UNEXPECTED", "WAITING",
           "events", "gather", "mail", "roster"]
