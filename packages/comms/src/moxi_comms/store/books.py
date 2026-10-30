"""The delivery books: each name's queue rows, the digests its adapter opens, and its cursor.

Every routed message holds one `queue` row per recipient name until the name acks it or
it is dropped. A row is in one of three states:

  * `waiting`: nothing holds it; the name's next release point hands it.
  * `handed`: pushed to the binding `binding_id` names, not yet in a digest.
  * `in_digest`: in the digest `digest_id` names, waiting on that digest's turn.

The daemon is the only writer. Each method below is one `BEGIN IMMEDIATE` transaction,
so a failure inside one lands none of it. The connection is one per daemon, used from
any of its threads under this object's lock.

The cursor is the daemon's: at each turn end it moves to the lower of the highest id the
name has ever acked and one below the lowest id that still has a row for the name, and
it never goes back. The highest acked id is kept across acks, so acking H while a lower
L is held leaves the cursor below L, and acking L later carries it up to H. After an ack no row means "not to be delivered again": catch-up is the
name's rows, never a replay from the record.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from moxi.store import transaction

from .open import open_read, open_write

log = logging.getLogger("comms.books")

WAITING = "waiting"
HANDED = "handed"
IN_DIGEST = "in_digest"

#: How a digest ended. `answer`: its turn ended answered and its rows are acked.
#: `unhandled`: its turn ended any other way, or the text never went in; its rows wait
#: again. `released`: its binding ended first. `fenced`: a daemon stopped without closing
#: it, and the next one's boot closed it.
ANSWER = "answer"
UNHANDLED = "unhandled"
RELEASED = "released"
FENCED = "fenced"

#: Why `open_digest` left an id out.
WITHDRAWN = "withdrawn"
EXPIRED = "expired"

TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"

#: The `meta` key that holds the highest message id the daemon has routed. Every id at
#: or below it was given its rows when it was routed, so a boot rescan routes only what
#: is above it: a message acked above a lower held one has no row and sits above the
#: clamped cursor, and routing it again would deliver it twice.
META_ROUTED_THROUGH = "routed_through"

#: The prefix of the `meta` key that holds a name's highest acked id, the clamp's
#: high-water: `acked_through:<name>`. Kept across acks, so the cursor reaches it once
#: the lower rows that held it back are acked or gone.
META_ACKED_THROUGH = "acked_through:"

# --------------------------------------------------------------- the cursor's reasons
#: Why a name's cursor stands where it does, for `moxi comms explain`. A name, not an
#: error code: the first two are the commonest states in a healthy home.
NO_CURSOR_YET = "no-cursor-yet"
ALREADY_PAST = "cursor-already-past-it"
BEHIND = "cursor-behind-it"
#: Not the cursor's own: the message has a row for the name. `explain` checks it before
#: the cursor, so a message with a row never reads `cursor-behind-it`.
WAITING = "waiting"

#: One sentence per reason, in the terms of the person reading it at a terminal because
#: an agent went quiet. `moxi comms explain` prints these verbatim.
REASONS = {
    NO_CURSOR_YET: (
        "No turn of this name has acked a digest yet, so it has no cursor. Its mail "
        "waits in its rows until a turn answers it."),
    ALREADY_PAST: (
        "The cursor is at or past this message. This is the handled case: a turn "
        "answered the digest that carried it."),
    BEHIND: (
        "The cursor has not reached this message. Nothing declined anything: the turn "
        "that answers its digest has not ended, or a lower message still waits."),
    WAITING: (
        "This message waits in the name's rows for its next release point: its next "
        "session start, /clear, compaction, or a turn of its own on a warm cache."),
}


def stamp(t: float) -> str:
    return time.strftime(TIMESTAMP_FMT, time.gmtime(t))


def _ids(values) -> list[int]:
    """Message ids as integers; anything that isn't one is left out."""
    out = []
    for v in values or ():
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


@dataclass(frozen=True)
class Row:
    name: str
    message_id: str
    state: str
    held: bool
    binding_id: int | None
    digest_id: int | None
    queued_at: str


@dataclass(frozen=True)
class Opened:
    """`open_digest`'s answer: the digest's id, the ids it holds, and the ids left out
    with why (`[{id, reason}]`)."""

    digest: int
    ids: list
    dropped: list


@dataclass(frozen=True)
class Withdrawn:
    """`withdraw`'s answer: the names the message was withdrawn from, the names that
    had it in a digest, the names whose row expired first, and whether anything was
    written."""

    withdrawn: list
    delivered: list
    expired: list
    written: bool


@dataclass(frozen=True)
class Ended:
    """`turn_ended`'s answer: the ids acked, the ids back to waiting, and the cursor."""

    handled: list
    unhandled: list
    cursor: str | None


class Books:
    """comms.db's delivery tables, for the daemon."""

    def __init__(self, path=None, *, clock=time.time, env=None):
        self._path = path
        self._env = env
        self.clock = clock
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ open, close
    def open(self) -> None:
        """Open comms.db for writing, through the one opener. Its refusals raise."""
        with self._lock:
            if self._conn is None:
                self._conn = open_write(self._path, env=self._env, check_same_thread=False)

    def close(self) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            conn.close()

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    def _write(self):
        if self._conn is None:
            raise sqlite3.ProgrammingError("the delivery books are not open")
        return transaction(self._conn)

    def _now(self) -> str:
        return stamp(self.clock())

    # ------------------------------------------------------------------ reads
    def rows(self, name: str | None = None, *, states=None) -> list[Row]:
        """Rows, by message id: one name's, or every name's; of `states` or any."""
        sql = ("SELECT name, message_id, state, held, binding_id, digest_id, queued_at "
               "FROM queue")
        where, args = [], []
        if name is not None:
            where.append("name = ?")
            args.append(name)
        if states:
            where.append(f"state IN ({','.join('?' * len(states))})")
            args.extend(states)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY message_id, name"
        with self._lock:
            got = self._conn.execute(sql, args).fetchall()
        return [Row(r[0], str(r[1]), r[2], bool(r[3]), r[4], r[5], r[6]) for r in got]

    def cursor(self, name: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT message_id FROM cursors WHERE name = ?",
                                     (name,)).fetchone()
        return None if row is None else str(row[0])

    def cursor_row(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT message_id, updated_at FROM cursors WHERE name = ?",
                (name,)).fetchone()
        return None if row is None else {"cursor": str(row[0]), "updated_at": row[1]}

    def digest(self, digest_id) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, binding_id, opened_at, ids, text, started_at, ended_at, "
                "outcome FROM digests WHERE id = ?", (int(digest_id),)).fetchone()
        return None if row is None else _digest(row)

    def last_digest(self, name: str) -> dict | None:
        """The newest digest the name has had text for."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, binding_id, opened_at, ids, text, started_at, ended_at, "
                "outcome FROM digests WHERE name = ? AND text != '' ORDER BY id DESC "
                "LIMIT 1", (name,)).fetchone()
        return None if row is None else _digest(row)

    def open_digests(self, name: str) -> list[dict]:
        with self._lock:
            got = self._conn.execute(
                "SELECT id, name, binding_id, opened_at, ids, text, started_at, ended_at, "
                "outcome FROM digests WHERE name = ? AND ended_at IS NULL ORDER BY id",
                (name,)).fetchall()
        return [_digest(r) for r in got]

    def routed_through(self) -> int | None:
        """The highest message id routed, or None when nothing has been."""
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?",
                                     (META_ROUTED_THROUGH,)).fetchone()
        try:
            return None if row is None else int(row[0])
        except (TypeError, ValueError):
            return None

    def routed(self, message_id) -> None:
        """A message was routed: the high mark rises to it, and never falls."""
        mid = int(message_id)
        with self._lock, self._write() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?",
                               (META_ROUTED_THROUGH,)).fetchone()
            try:
                if row is not None and int(row[0]) >= mid:
                    return
            except (TypeError, ValueError):
                pass
            conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO "
                         "UPDATE SET value = excluded.value",
                         (META_ROUTED_THROUGH, str(mid)))

    def held_ids(self) -> set:
        """Every message id with a row for any name: what retention keeps."""
        with self._lock:
            got = self._conn.execute("SELECT DISTINCT message_id FROM queue").fetchall()
        return {str(r[0]) for r in got}

    # ------------------------------------------------------------------ routing
    def add(self, name: str, message_id, *, held: bool = False, binding_id=None) -> bool:
        """One row: `waiting`, or `handed` to `binding_id`. A row the name already has
        becomes `waiting` again when `binding_id` is None (a push that failed), and is
        otherwise left as it is. True when the row is new or changed."""
        mid = int(message_id)
        state = WAITING if binding_id is None else HANDED
        with self._lock, self._write() as conn:
            row = conn.execute("SELECT state FROM queue WHERE name = ? AND message_id = ?",
                               (name, mid)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO queue(name, message_id, queued_at, held, state, "
                    "binding_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, mid, self._now(), int(bool(held)), state, binding_id))
                return True
            if binding_id is None and row[0] != WAITING:
                conn.execute("UPDATE queue SET state = 'waiting', binding_id = NULL, "
                             "digest_id = NULL WHERE name = ? AND message_id = ?",
                             (name, mid))
                return True
        return False

    def hand(self, name: str, message_id, binding_id) -> None:
        """A row handed to a binding: `handed` to it, made if the name had none."""
        mid = int(message_id)
        with self._lock, self._write() as conn:
            cur = conn.execute("UPDATE queue SET state = 'handed', binding_id = ?, "
                               "digest_id = NULL WHERE name = ? AND message_id = ? "
                               "AND state = 'waiting'", (binding_id, name, mid))
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT OR IGNORE INTO queue(name, message_id, queued_at, state, "
                    "binding_id) VALUES (?, ?, ?, 'handed', ?)",
                    (name, mid, self._now(), binding_id))

    def drop(self, name: str, ids) -> int:
        """Delete rows nobody can take (an `agent-N`'s, a send-only name's)."""
        mids = _ids(ids)
        if not mids:
            return 0
        with self._lock, self._write() as conn:
            return sum(conn.execute("DELETE FROM queue WHERE name = ? AND message_id = ?",
                                    (name, mid)).rowcount for mid in mids)

    # ------------------------------------------------------------------ dropping
    def drop_gone(self, name: str, ids, reason: str) -> list[str]:
        """`ids` may no longer go to `name`'s model (`reason`, `withdrawn` or `expired`):
        each row not in a digest is deleted, and a `dropped` row says why. An id with no
        row gets its `dropped` row too (a message dropped at routing). A row in a digest
        is left: what a model may have read isn't pulled back. Returns the ids dropped."""
        at = self._now()
        out = []
        with self._lock, self._write() as conn:
            for mid in _ids(ids):
                row = conn.execute("SELECT state FROM queue WHERE name = ? AND "
                                   "message_id = ?", (name, mid)).fetchone()
                if row is not None and row[0] == IN_DIGEST:
                    continue
                conn.execute("DELETE FROM queue WHERE name = ? AND message_id = ?",
                             (name, mid))
                conn.execute("INSERT OR IGNORE INTO dropped(message_id, name, reason, at) "
                             "VALUES (?, ?, ?, ?)", (mid, name, reason, at))
                out.append(str(mid))
        return out

    def sweep(self, now: float, expires=None) -> list[tuple[str, str]]:
        """The expiry sweep: every `waiting` row whose message is past its expiry is
        deleted, with a `dropped` row. The expiry is the store's `messages.expires_at`,
        or `expires` (message id -> Unix second) for a message another backend keeps.
        Rows handed or in a digest are left: `open_digest` drops a handed one, and a
        digest's rows go back to waiting at a release. Returns (name, id) pairs."""
        at = stamp(now)
        given = {}
        for mid, when in dict(expires or {}).items():
            if when is not None:
                try:
                    given[int(mid)] = when
                except (TypeError, ValueError):
                    continue
        with self._lock, self._write() as conn:
            rows = conn.execute(
                "SELECT q.name, q.message_id, m.expires_at FROM queue q LEFT JOIN "
                "messages m ON m.id = q.message_id WHERE q.state = 'waiting'").fetchall()
            out = []
            for name, mid, stored in rows:
                when = stored if stored is not None else given.get(int(mid))
                if when is None or when > now:
                    continue
                conn.execute("DELETE FROM queue WHERE name = ? AND message_id = ?",
                             (name, mid))
                conn.execute("INSERT OR IGNORE INTO dropped(message_id, name, reason, at) "
                             "VALUES (?, ?, ?, ?)", (mid, name, EXPIRED, at))
                out.append((name, str(mid)))
        return out

    def withdraw(self, message_id, *, unrouted=(), at: str | None = None) -> "Withdrawn":
        """The author's delete, in one transaction ordered against `open_digest` by
        `BEGIN IMMEDIATE`: every row of the message that was never in a digest is
        deleted with a `withdrawn` row in `dropped`, each name in `unrouted` (routing
        hasn't reached it yet) gets its `withdrawn` row, and the message is marked
        withdrawn. Names whose row has been in a digest had it delivered. When nothing
        can be withdrawn (no undelivered row and no unrouted name, and the message was
        not withdrawn before), nothing is written."""
        mid = int(message_id)
        at = at or self._now()
        with self._lock, self._write() as conn:
            rows = conn.execute("SELECT name, digested_once FROM queue WHERE "
                                "message_id = ? ORDER BY name", (mid,)).fetchall()
            before = {r[0]: r[1] for r in conn.execute(
                "SELECT name, reason FROM dropped WHERE message_id = ?", (mid,))}
            withdrawn = [n for n, once in rows if not once]
            delivered = [n for n, once in rows if once]
            fresh = [n for n in unrouted if n not in before and n not in withdrawn
                     and n not in delivered]
            again = sorted(n for n, why in before.items() if why == WITHDRAWN)
            if not withdrawn and not fresh and not again:
                return Withdrawn(withdrawn=[], delivered=delivered,
                                 expired=sorted(n for n, why in before.items()
                                                if why == EXPIRED), written=False)
            for n in withdrawn + fresh:
                conn.execute("INSERT OR REPLACE INTO dropped(message_id, name, reason, at) "
                             "VALUES (?, ?, ?, ?)", (mid, n, WITHDRAWN, at))
            conn.execute("DELETE FROM queue WHERE message_id = ? AND digested_once = 0",
                         (mid,))
            conn.execute("UPDATE messages SET withdrawn_at = ? WHERE id = ? AND "
                         "withdrawn_at IS NULL", (at, mid))
        return Withdrawn(withdrawn=sorted(set(withdrawn + fresh + again)),
                         delivered=delivered,
                         expired=sorted(n for n, why in before.items() if why == EXPIRED),
                         written=True)

    def dropped_reason(self, name: str, message_id) -> str | None:
        """Why `message_id` left `name`'s queue undelivered, or None."""
        with self._lock:
            row = self._conn.execute("SELECT reason FROM dropped WHERE name = ? AND "
                                     "message_id = ?", (name, int(message_id))).fetchone()
        return None if row is None else row[0]

    # ------------------------------------------------------------------ digests
    def open_digest(self, name: str, binding_id, ids, *, expires=None) -> Opened:
        """The adapter is about to put `ids` in one digest. Rows of withdrawn or expired
        messages are deleted, with their `dropped` rows, and answered in `dropped`. The
        rest of the name's rows asked for go `in_digest` under a new digest. An id with
        no row is left out: it was acked or dropped already. `expires` (message id ->
        Unix second) is the expiry of a message the store's `messages` doesn't hold."""
        mids = _ids(ids)
        given = {str(k): v for k, v in dict(expires or {}).items()}
        now = self.clock()
        at = stamp(now)
        with self._lock, self._write() as conn:
            kept, dropped = [], []
            for mid in mids:
                row = conn.execute("SELECT state FROM queue WHERE name = ? AND "
                                   "message_id = ?", (name, mid)).fetchone()
                if row is None:
                    gone = conn.execute("SELECT reason FROM dropped WHERE name = ? AND "
                                        "message_id = ?", (name, mid)).fetchone()
                    if gone is not None:
                        dropped.append({"id": str(mid), "reason": gone[0]})
                    continue
                why = _why_dropped(conn, mid, now, given.get(str(mid)))
                if why is not None:
                    conn.execute("DELETE FROM queue WHERE name = ? AND message_id = ?",
                                 (name, mid))
                    conn.execute("INSERT OR REPLACE INTO dropped(message_id, name, reason, "
                                 "at) VALUES (?, ?, ?, ?)", (mid, name, why, at))
                    dropped.append({"id": str(mid), "reason": why})
                    continue
                kept.append(mid)
            cur = conn.execute(
                "INSERT INTO digests(name, binding_id, opened_at, ids, text) "
                "VALUES (?, ?, ?, ?, '')",
                (name, binding_id, at, json.dumps([str(m) for m in kept])))
            digest = cur.lastrowid
            for mid in kept:
                conn.execute("UPDATE queue SET state = 'in_digest', digest_id = ?, "
                             "binding_id = ?, digested_once = 1 WHERE name = ? AND "
                             "message_id = ?", (digest, binding_id, name, mid))
        return Opened(digest=digest, ids=[str(m) for m in kept], dropped=dropped)

    def injected(self, name: str, digest_id, text: str) -> bool:
        """The digest's text, as the adapter put it in."""
        with self._lock, self._write() as conn:
            return conn.execute("UPDATE digests SET text = ? WHERE id = ? AND name = ?",
                                (text, int(digest_id), name)).rowcount > 0

    def started(self, name: str, digest_ids) -> int:
        """A turn took these digests (it started, or a steer went in)."""
        at = self._now()
        with self._lock, self._write() as conn:
            return sum(conn.execute(
                "UPDATE digests SET started_at = COALESCE(started_at, ?) WHERE id = ? "
                "AND name = ?", (at, did, name)).rowcount for did in _ids(digest_ids))

    def turn_ended(self, name: str, handled, unhandled) -> Ended:
        """The ack. For each handled digest its rows are deleted, it ends `answer`, and
        the cursor advances; each unhandled digest's rows go back to `waiting` and it
        ends `unhandled`. A digest that isn't the name's, or has ended, is ignored."""
        at = self._now()
        acked, back = [], []
        with self._lock, self._write() as conn:
            for did, outcome in ([(d, ANSWER) for d in _ids(handled)]
                                 + [(d, UNHANDLED) for d in _ids(unhandled)]):
                row = conn.execute("SELECT ended_at FROM digests WHERE id = ? AND "
                                   "name = ?", (did, name)).fetchone()
                if row is None or row[0] is not None:
                    log.info("turn end for %s names digest %s, which is not open for it; "
                             "ignored", name, did)
                    continue
                mids = [r[0] for r in conn.execute(
                    "SELECT message_id FROM queue WHERE name = ? AND digest_id = ?",
                    (name, did))]
                if outcome == ANSWER:
                    conn.execute("DELETE FROM queue WHERE name = ? AND digest_id = ?",
                                 (name, did))
                    acked.extend(mids)
                else:
                    conn.execute("UPDATE queue SET state = 'waiting', digest_id = NULL, "
                                 "binding_id = NULL WHERE name = ? AND digest_id = ?",
                                 (name, did))
                    back.extend(mids)
                conn.execute("UPDATE digests SET ended_at = ?, outcome = ? WHERE id = ?",
                             (at, outcome, did))
            cursor = _advance(conn, name, acked, at)
        return Ended(handled=[str(m) for m in sorted(acked)],
                     unhandled=[str(m) for m in sorted(back)], cursor=cursor)

    # ------------------------------------------------------------------ release, fence
    def release(self, name: str, *, ephemeral: bool = False) -> list[str]:
        """A binding of `name` ended: its `handed` and `in_digest` rows wait again (an
        `agent-N`'s are deleted instead) and its open digests end `released`. Returns
        the ids now waiting (none for an `agent-N`)."""
        at = self._now()
        with self._lock, self._write() as conn:
            mids = [str(r[0]) for r in conn.execute(
                "SELECT message_id FROM queue WHERE name = ? AND state != 'waiting' "
                "ORDER BY message_id", (name,))]
            if ephemeral:
                conn.execute("DELETE FROM queue WHERE name = ?", (name,))
                mids = []
            else:
                conn.execute("UPDATE queue SET state = 'waiting', binding_id = NULL, "
                             "digest_id = NULL WHERE name = ? AND state != 'waiting'",
                             (name,))
            conn.execute("UPDATE digests SET ended_at = ?, outcome = ? WHERE name = ? "
                         "AND ended_at IS NULL", (at, RELEASED, name))
        return mids

    def fence(self) -> tuple[int, int]:
        """Boot, before any method is answered: every `handed` and `in_digest` row waits
        again and every open digest ends `fenced`. Returns (rows, digests)."""
        at = self._now()
        with self._lock, self._write() as conn:
            rows = conn.execute("UPDATE queue SET state = 'waiting', binding_id = NULL, "
                                "digest_id = NULL WHERE state != 'waiting'").rowcount
            digests = conn.execute("UPDATE digests SET ended_at = ?, outcome = ? WHERE "
                                   "ended_at IS NULL", (at, FENCED)).rowcount
        return rows, digests


def _digest(row) -> dict:
    try:
        ids = json.loads(row[4])
    except (TypeError, ValueError):
        ids = []
    return {"id": row[0], "name": row[1], "binding_id": row[2], "opened_at": row[3],
            "ids": ids, "text": row[5], "started_at": row[6], "ended_at": row[7],
            "outcome": row[8]}


def _why_dropped(conn, mid: int, now: float, expires=None) -> str | None:
    """`withdrawn` or `expired` for a message that may no longer be delivered; None
    otherwise. A message another backend keeps has no `messages` row here: `expires` is
    its expiry, and its withdrawal already deleted its undelivered rows."""
    row = conn.execute("SELECT withdrawn_at, expires_at FROM messages WHERE id = ?",
                       (mid,)).fetchone()
    if row is None:
        return EXPIRED if expires is not None and expires <= now else None
    if row[0] is not None:
        return WITHDRAWN
    if row[1] is not None and row[1] <= now:
        return EXPIRED
    return None


def _advance(conn, name: str, acked, at: str) -> str | None:
    """The clamp, in the turn end's transaction: the lower of the highest id the name has
    ever acked (kept in `meta`, raised by this ack) and one below the lowest id the name
    still has a row for; never back. A turn end that acks nothing still moves it when
    the rows that held it back have gone."""
    row = conn.execute("SELECT message_id FROM cursors WHERE name = ?", (name,)).fetchone()
    old = None if row is None else int(row[0])
    key = META_ACKED_THROUGH + name
    got = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    try:
        high = None if got is None else int(got[0])
    except (TypeError, ValueError):
        high = None
    if acked and (high is None or max(acked) > high):
        high = max(acked)
        conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO "
                     "UPDATE SET value = excluded.value", (key, str(high)))
    if high is None:
        return None if old is None else str(old)
    target = high
    low = conn.execute("SELECT MIN(message_id) FROM queue WHERE name = ?",
                       (name,)).fetchone()[0]
    if low is not None:
        target = min(target, int(low) - 1)
    if old is not None and target <= old:
        return str(old)
    conn.execute("INSERT INTO cursors(name, message_id, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(name) DO UPDATE SET message_id = excluded.message_id, "
                 "updated_at = excluded.updated_at", (name, target, at))
    return str(target)


def explain_cursor(row: dict | None, message_id=None) -> dict:
    """A name's cursor row (`cursor_row`, `read_cursor`) with the reason it stands where
    it does. With `message_id`, the reason compares against it."""
    cursor = None if row is None else row.get("cursor")
    reason = NO_CURSOR_YET if cursor is None else None
    if cursor is not None and message_id is not None:
        try:
            reason = ALREADY_PAST if int(cursor) >= int(message_id) else BEHIND
        except (TypeError, ValueError):
            reason = None
    return {"cursor": cursor, "updated_at": None if row is None else row.get("updated_at"),
            "reason": reason, "sentence": REASONS.get(reason)}


def read_dropped(name: str, message_id, *, path=None, env=None) -> str | None:
    """Why `message_id` left `name`'s queue undelivered (`withdrawn` or `expired`), from
    a read-only open, for a process that isn't the daemon; None when it didn't, or the
    store isn't there."""
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return None
    try:
        conn = open_read(Path(path) if path else None, env=env)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT reason FROM dropped WHERE name = ? AND message_id = ?",
                           (name, mid)).fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


def dropped_by_message(conn, ids) -> dict[str, dict[str, str]]:
    """For each of `ids` that left a queue undelivered: {message id: {name: reason}},
    read on `conn`, any connection to comms.db."""
    mids = _ids(ids)
    if not mids:
        return {}
    out: dict[str, dict[str, str]] = {}
    marks = ", ".join("?" * len(mids))
    for mid, name, reason in conn.execute(
            f"SELECT message_id, name, reason FROM dropped WHERE message_id IN ({marks}) "
            "ORDER BY message_id, name", mids):
        out.setdefault(str(mid), {})[name] = reason
    return out


def read_cursor(name: str, *, path=None, env=None) -> dict | None:
    """One name's cursor from a read-only open, for a process that isn't the daemon:
    `{"cursor", "updated_at"}`, or None when it has none or the store isn't there."""
    try:
        conn = open_read(Path(path) if path else None, env=env)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT message_id, updated_at FROM cursors WHERE name = ?",
                           (name,)).fetchone()
    finally:
        conn.close()
    return None if row is None else {"cursor": str(row[0]), "updated_at": row[1]}
