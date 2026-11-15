"""The page's reads of comms' record and books, in the page server's process.

These reads ask the daemon nothing, so they answer with it down. Each opens comms.db
read-only when its handler starts and closes it before it answers; nothing is held
between reads:

- **The books and the directory** on one `store.open_read` connection with its schema
  step checked: no lock, no gate, no move of comms 0.2's files, no migration, and every
  write refused.
- **The record** through `open_record(read_only=True)`, which opens the local record the
  same way and the mock record's file as the CLI does.

Which store answers what:

- no `comms.db`, or no `#names` room in it: `up`, with no message, and no file made;
- comms 0.2's files waiting to be moved (`store.Unmoved`), a schema at another step than
  this comms knows (`store.StepMismatch`), a store that can't be opened or read, or any
  other failure: `error`, with a fixed sentence naming only the exception's class. Each
  class is logged once with its text and traceback; no exception's text reaches the
  browser.

A query value the page never sends (an id that is not all digits, a day that is not a
date, a limit under one) answers `error` with a fixed sentence, and opens nothing.

A message is listed only when its line zero is a machine line: a prose message, which
no list shows, is left out and not counted. Its rows come from one query of the books'
`queue` and one of `dropped` for every message an answer carries, and its author from
`attribution.from_record`, run only on the messages the answer carries.

Like `page`, this module is imported only by the page server and loopback's export.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from . import attribution, directory, machine_line
from .owed import epoch_of
from .paths import comms_db
from .provision import NAMES
from .record import open_record
from .store import open_read

#: `/api/messages`' `limit` when the query names none.
LIMIT = 12
#: How many messages each step back reads when an earlier day is read whole.
DAY_PAGE = 200

#: The record's one time spelling, UTC.
STAMP = "%Y-%m-%dT%H:%M:%SZ"

#: What the page shows for a read that failed, naming only the exception's class.
READ_ERROR = ("comms' page server couldn't read comms' record and books ({}); its log "
              "has the details")
#: What the page shows for a query value it never sends.
BAD_QUERY = "the read's query holds a value this page never sends"
#: What the page shows for an earlier day asked for with no message id to read back from.
NO_CURSOR = "a day before today is read back from a message id, and none was given"

_ID = re.compile(r"\A[0-9]{1,20}\Z")
_DAY = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")

log = logging.getLogger("moxi.comms.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


class _Refused(ValueError):
    """A query the handler refuses; its one argument is the detail the page shows."""


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


def _today() -> dt.date:
    """The page server's local date."""
    return dt.date.today()


# --- opening -----------------------------------------------------------------------------


@dataclass
class Store:
    """One read's open store: the books' and directory's connection, the record and
    the `#names` room."""

    conn: sqlite3.Connection
    record: Any
    room: str


@contextlib.contextmanager
def opened() -> Iterator[Store | None]:
    """The store, opened read-only for one read, or None when there is no `comms.db` or
    no `#names` room in it. Both connections are closed when the block ends."""
    target = comms_db()
    try:
        conn = open_read(target, check_step=True)
    except sqlite3.OperationalError:
        if not target.exists():
            yield None
            return
        raise
    try:
        # As the directory's own connections answer, so its reads and attribution's
        # take this one.
        conn.row_factory = sqlite3.Row
        found = directory.room_by_name(conn, NAMES)
        if found is None:
            yield None
            return
        record = open_record(read_only=True)
        try:
            yield Store(conn=conn, record=record, room=str(found["room_id"]))
        finally:
            record.close()
    finally:
        conn.close()


def answer(empty: dict[str, Any], read) -> dict[str, Any]:
    """`read()`'s keys with `state` `up`; `empty`'s keys with `state` `error` and a fixed
    `detail` when `read` refuses its query (`_Refused`) or fails."""
    try:
        return {"state": "up", "detail": None, **read()}
    except _Refused as exc:
        return {"state": "error", "detail": exc.args[0], **empty}
    except Exception as exc:
        _log_once(exc, "reading comms' record and books failed")
        return {"state": "error", "detail": READ_ERROR.format(type(exc).__name__),
                **empty}


# --- query values ------------------------------------------------------------------------


def query_id(raw: str | None) -> str | None:
    """A query's message id: a string of digits, or None when it is absent."""
    if raw is None:
        return None
    if not _ID.match(raw):
        raise _Refused(BAD_QUERY)
    return raw


def _day(raw: str | None, today: dt.date) -> dt.date:
    if raw is None:
        return today
    if not _DAY.match(raw):
        raise _Refused(BAD_QUERY)
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise _Refused(BAD_QUERY) from None


def _limit(raw: int | None, default: int) -> int:
    if raw is None:
        return default
    if raw < 1:
        raise _Refused(BAD_QUERY)
    return raw


# --- times -------------------------------------------------------------------------------


def stamp(epoch: float) -> str:
    """Epoch seconds in the record's time spelling."""
    return time.strftime(STAMP, time.gmtime(epoch))


def day_bounds(day: dt.date) -> tuple[str, str]:
    """The record's spellings of the start of `day` and of the next day, local time."""
    start = dt.datetime.combine(day, dt.time()).timestamp()
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time()).timestamp()
    return stamp(start), stamp(end)


def local_day(epoch: float) -> str:
    """The local date of an epoch second, `YYYY-MM-DD`."""
    return dt.datetime.fromtimestamp(epoch).date().isoformat()


# --- messages ----------------------------------------------------------------------------


@dataclass
class Read:
    """One message of the record that a list can show: line zero parsed, its time in
    epoch seconds."""

    msg: Any
    line: machine_line.MachineLine
    at: float

    @property
    def number(self) -> int:
        """The message id as a number, for order."""
        return int(self.msg.id)


def readable(found) -> list[Read]:
    """The messages of `found` whose line zero is a machine line and whose time reads,
    in the order given."""
    out = []
    for msg in found:
        line = machine_line.parse(msg.content)
        at = epoch_of(msg.timestamp)
        if line is not None and at is not None:
            out.append(Read(msg=msg, line=line, at=at))
    return out


def book_rows(conn: sqlite3.Connection, ids) -> dict[str, list[dict[str, Any]]]:
    """Every row the books hold for each of `ids`: {message id: [row]}, by qualified
    name, from one query of `queue` and one of `dropped`."""
    mids = sorted({int(i) for i in ids})
    if not mids:
        return {}
    marks = ", ".join("?" * len(mids))
    out: dict[str, list[dict[str, Any]]] = {}
    for mid, name, state, held, once in conn.execute(
            "SELECT message_id, name, state, held, digested_once FROM queue "
            f"WHERE message_id IN ({marks})", mids):
        out.setdefault(str(mid), []).append(
            {"qualified": name, "state": state, "held": bool(held),
             "takeable": not once})
    for mid, name, reason in conn.execute(
            f"SELECT message_id, name, reason FROM dropped WHERE message_id IN ({marks})",
            mids):
        out.setdefault(str(mid), []).append(
            {"qualified": name, "state": reason, "held": False, "takeable": False})
    for rows in out.values():
        rows.sort(key=lambda row: row["qualified"])
    return out


def shaped(store: Store, reads: list[Read]) -> list[dict[str, Any]]:
    """`reads` as `page.MESSAGE`s, in the order given: each attributed, and with its rows
    in the books."""
    rows = book_rows(store.conn, [r.msg.id for r in reads])
    out = []
    for r in reads:
        author = attribution.from_record(store.conn, r.msg)
        out.append({
            "message_id": str(r.msg.id),
            "at": float(r.at),
            "author": author.qualified,
            "author_tier": author.tier,
            "to": list(r.line.recipients),
            "posture": r.line.posture,
            "reply_to": r.line.reply_to,
            "text": r.line.text,
            "withdrawn": bool(r.msg.withdrawn_at),
            "rows": rows.get(str(r.msg.id), []),
        })
    return out


def _back_through_day(store: Store, before: str, start: str, end: str) -> list[Read]:
    # Newest first from `before`, a page at a time, until a message older than the day's
    # start: the messages in between that fall on the day.
    found: list[Read] = []
    cursor = before
    while True:
        page = store.record.history(store.room, before=cursor, limit=DAY_PAGE)
        for msg in reversed(page):
            if msg.timestamp < start:
                return found
            if msg.timestamp < end:
                found.extend(readable([msg]))
        if len(page) < DAY_PAGE:
            return found
        cursor = page[0].id


def messages(call: Any) -> dict[str, Any]:
    """`GET /api/messages?day=&before=&limit=`: the day's messages below `before`,
    newest first, the newest `limit` of them (12 by default); `more`, the day's messages
    below the last one sent; `earlier`, the messages before the day. Today is read
    whole from its start; a day before today is read back from `before`, which it needs.
    A failure is a 200 with `state` `error`, never a 500."""
    query = call.query
    today = _today()
    empty = {"today": today.isoformat(), "day": today.isoformat(), "messages": [],
             "more": 0, "earlier": None}

    def read() -> dict[str, Any]:
        day = _day(query.get("day"), today)
        before = query_id(query.get("before"))
        limit = _limit(query.get("limit"), LIMIT)
        if day != today and before is None:
            raise _Refused(NO_CURSOR)
        out = {**empty, "day": day.isoformat()}
        with opened() as store:
            if store is None:
                return out
            start, end = day_bounds(day)
            if day == today:
                found = readable(store.record.since(store.room, start))
                if before is not None:
                    found = [r for r in found if r.number < int(before)]
            else:
                found = _back_through_day(store, before, start, end)
            found.sort(key=lambda r: r.number, reverse=True)
            out["messages"] = shaped(store, found[:limit])
            out["more"] = max(0, len(found) - limit)
            out["earlier"] = earlier(store, start)
        return out

    return answer(empty, read)


def earlier(store: Store, start: str) -> dict[str, Any] | None:
    """The messages a list can show before `start`, from one `count_before`: how many,
    the newest one's local day, the oldest one's time, and the cursor that reads the
    newest one's day (its id plus one). None when there are none."""
    count, first, last, last_id = store.record.count_before(store.room, start)
    if not count:
        return None
    first_at, last_at = epoch_of(first), epoch_of(last)
    if first_at is None or last_at is None:
        return None
    return {"day": local_day(last_at), "count": int(count), "first_at": float(first_at),
            "before": str(int(last_id) + 1)}


__all__ = ["BAD_QUERY", "DAY_PAGE", "LIMIT", "NO_CURSOR", "READ_ERROR", "Read", "Store",
           "answer", "book_rows", "day_bounds", "earlier", "local_day",
           "messages", "opened", "query_id", "readable", "shaped", "stamp"]
