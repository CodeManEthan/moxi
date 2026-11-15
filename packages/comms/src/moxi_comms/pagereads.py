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
date, a limit under one, an empty address) answers `error` with a fixed sentence, and
opens nothing.

A message is listed only when its line zero is a machine line: a prose message, which
no list shows, is left out and not counted. Its rows come from one query of the books'
`queue` and one of `dropped` for every message an answer carries, and its author from
`attribution.from_record`, run only on the messages the answer carries. A read that
weighs every message of a window (`/api/mail-state`) names an agent's or a module's
post by its `author_id`, with no directory query (`authored`).

`/api/mail-state` decides whether an ask was answered by `replies`, the rule the
daemon's `awaiting` uses, applied over the record: with the operator as the answerer for
the asks to him (`owed`), and with each awaited agent for the asks between agents.

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

from . import attribution, constants, directory, machine_line, replies
from .owed import epoch_of
from .paths import comms_db
from .provision import NAMES
from .record import AUTHOR_IDENTITY, AUTHOR_RESERVED, open_record
from .replies import SentEntry
from .scoping import GLOBAL, OPERATOR_NAME, identity_of, qualify, split_tag, tag_matches
from .store import open_read

#: `/api/messages`' `limit` when the query names none.
LIMIT = 12
#: How many messages each step back reads when an earlier day is read whole.
DAY_PAGE = 200
#: How many of one address's rows `/api/queue` sends.
QUEUE_CAP = 50

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


def _address(raw: str | None) -> str:
    """A query's address: any string but the empty one, which the page never sends."""
    if not raw:
        raise _Refused(BAD_QUERY)
    return raw


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


# --- who wrote it ------------------------------------------------------------------------


@dataclass(frozen=True)
class Authored:
    """What the reply rule needs of a message's author: its qualified name, whether its
    attribution is `verified`, and whether it is the operator (tier `operator`, whatever
    name the directory gives him)."""

    qualified: str
    verified: bool
    is_operator: bool


def authored(conn: sqlite3.Connection, msg) -> Authored:
    """`attribution.from_record`'s answer in short. An agent's or a module's post is
    named by its `author_id` and is always verified, so it needs no directory query;
    any other kind is attributed in full."""
    if msg.author_kind in (AUTHOR_IDENTITY, AUTHOR_RESERVED):
        name, scope = split_tag(msg.author_id)
        return Authored(qualify(name, scope or GLOBAL), verified=True, is_operator=False)
    author = attribution.from_record(conn, msg)
    return Authored(author.qualified, verified=author.attribution == attribution.VERIFIED,
                    is_operator=author.tier == attribution.TIER_OPERATOR)


def sent_entry(r: Read, who: Authored) -> SentEntry:
    """One read message as the reply rule reads it (`replies`)."""
    return SentEntry(id=str(r.msg.id), author=who.qualified, tags=tuple(r.line.recipients),
                     posture=r.line.posture, reply_to=r.line.reply_to, at=r.at,
                     verified=who.verified)


# --- /api/mail-state ---------------------------------------------------------------------


def mail_state(call: Any) -> dict[str, Any]:
    """`GET /api/mail-state`: `queued`, `owed`, `asks` and `last` (`page.MAIL_STATE`),
    from the books and one read of the record's last `ASK_TTL`. It asks the daemon
    nothing. A failure is a 200 with `state` `error`, never a 500."""
    empty = {"today": _today().isoformat(), "queued": [], "owed": [], "asks": [],
             "last": []}

    def read() -> dict[str, Any]:
        out = dict(empty)
        with opened() as store:
            if store is None:
                return out
            now = time.time()
            found = readable(store.record.since(store.room,
                                                stamp(now - constants.ASK_TTL)))
            found.sort(key=lambda r: r.number)
            items = []
            for r in found:
                who = authored(store.conn, r.msg)
                items.append((r, who, sent_entry(r, who)))
            rows = store.conn.execute(
                "SELECT name, message_id, state FROM queue ORDER BY name, message_id"
            ).fetchall()
            out["queued"] = _queued(store, rows, {e.id: who for _, who, e in items})
            out["owed"] = _owed(items, now)
            out["asks"] = _asks(items, now, {(name, str(mid)) for name, mid, _ in rows})
            out["last"] = _last(items)
        return out

    return answer(empty, read)


def _queued(store: Store, rows, known: dict[str, Authored]) -> list[dict[str, Any]]:
    # Per address, its waiting and in-session rows, and the waiting rows' authors in
    # message order, each once. An author outside the read is looked up by id.
    out: dict[str, dict[str, Any]] = {}
    for name, mid, state in rows:
        entry = out.setdefault(name, {"qualified": name, "waiting": 0, "handed": 0,
                                      "from": []})
        if state != "waiting":
            entry["handed"] += 1
            continue
        entry["waiting"] += 1
        who = known.get(str(mid))
        if who is None:
            msg = store.record.get_message(str(mid))
            if msg is None:
                continue
            who = known[str(mid)] = authored(store.conn, msg)
        if who.qualified not in entry["from"]:
            entry["from"].append(who.qualified)
    return [out[name] for name in sorted(out)]


def _owed(items, now: float) -> list[dict[str, Any]]:
    # The asks to him, oldest first, that no message of his answers by the shared rule.
    his = [e for _, who, e in items if who.is_operator]
    out = []
    for r, who, ask in items:
        if who.is_operator or not replies.is_ask(ask, now):
            continue
        if not any(tag_matches(tag, OPERATOR_NAME) for tag in ask.tags):
            continue
        if replies.answered(ask.id, ask.author, his):
            continue
        out.append({"message_id": ask.id, "author": ask.author, "at": float(ask.at),
                    "text": r.line.text})
    return out


def _asks(items, now: float, held: set[tuple[str, str]]) -> list[dict[str, Any]]:
    # Each agent's open asks to another agent, the newest per pair. `held` is every
    # (name, message id) the books hold a row for.
    by_author: dict[str, list[SentEntry]] = {}
    for _, _, e in items:
        by_author.setdefault(e.author, []).append(e)
    pairs: dict[tuple[str, str], SentEntry] = {}
    for _, who, ask in items:
        if who.is_operator or not replies.is_ask(ask, now):
            continue
        for tag in ask.tags:
            awaited = identity_of(tag)
            if awaited in (ask.author, OPERATOR_NAME):
                continue
            if not replies.answered(ask.id, ask.author, by_author.get(awaited, ())):
                pairs[(ask.author, awaited)] = ask
    return [{"asker": asker, "awaited": awaited, "message_id": ask.id,
             "at": float(ask.at), "read": False if (awaited, ask.id) in held else None}
            for (asker, awaited), ask in sorted(pairs.items())]


def _last(items) -> list[dict[str, Any]]:
    # Each address's newest message by id, by it or to it.
    last: dict[str, float] = {}
    for _, _, e in items:
        for qualified in (e.author, *(identity_of(tag) for tag in e.tags)):
            last[qualified] = float(e.at)
    return [{"qualified": q, "at": last[q]} for q in sorted(last)]


# --- /api/queue ---------------------------------------------------------------------------


def queue(call: Any) -> dict[str, Any]:
    """`GET /api/queue?name=`: the messages one address has a row for in the books'
    `queue`, oldest first, at most `QUEUE_CAP`, each with that address's row first in
    its rows; `more`, how many rows are past the last one sent. A failure is a 200 with
    `state` `error`, never a 500."""
    query = call.query
    empty = {"messages": [], "more": 0}

    def read() -> dict[str, Any]:
        name = _address(query.get("name"))
        out = dict(empty)
        with opened() as store:
            if store is None:
                return out
            mids = [str(mid) for (mid,) in store.conn.execute(
                "SELECT message_id FROM queue WHERE name = ? ORDER BY message_id",
                (name,))]
            found: list[Read] = []
            taken = 0
            for mid in mids:
                if len(found) == QUEUE_CAP:
                    break
                taken += 1
                msg = store.record.get_message(mid)
                if msg is not None:
                    found.extend(readable([msg]))
            sent = shaped(store, found)
            for m in sent:
                m["rows"].sort(key=lambda row: row["qualified"] != name)
            out["messages"] = sent
            out["more"] = len(mids) - taken
        return out

    return answer(empty, read)


__all__ = ["BAD_QUERY", "DAY_PAGE", "LIMIT", "NO_CURSOR", "QUEUE_CAP", "READ_ERROR",
           "Authored", "Read", "Store", "answer", "authored", "book_rows", "day_bounds",
           "earlier", "local_day", "mail_state", "messages", "opened", "query_id", "queue",
           "readable", "sent_entry", "shaped", "stamp"]
