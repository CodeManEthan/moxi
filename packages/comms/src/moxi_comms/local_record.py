"""The LOCAL backend: the record's tables in comms.db, behind the record seam.

One of `record.py`'s two implementations, beside `MockRecord` (in-process, the test
double).

Why it exists: comms reads no platform. Every comms process on the box
writes this file directly and the daemon subscribes to it, so `moxi comms send` lands in
the record with the daemon down, `moxi comms history` reads with no network, and a box
needs no server and no account.

What is fixed here, and must not drift:

  * `STATE/comms/comms.db` (`paths.comms_db()`), one file per box, opened only
    through `store.open_write`. STATE and not DATA because a live DB is never
    continuous-file-synced. The record's tables are `rooms`, `messages`,
    `principals`, `denied` and `meta`; `store/schema.py` makes them.
  * `max_content_length` 16,000 (`record.MAX_CONTENT_LENGTH`), the one cap on one
    message.
  * `retain_days` 90 (config.toml's `[local] retain_days`, whose default this is),
    `0` disables.
  * No reply column: correlation rides the machine line's
    `re:` token, exactly as on the mock.
  * Ids from a PERSISTED counter, `meta('next_id')`, never `MAX(id)+1`.
    Retention trims messages; a high-water mark taken off the trimmed table resets the
    sequence after a trim, and every cursor on the box then compares as "after" every
    new id. The counter is the fix, and channels, principals and messages share it.

Fanout is three layers under one lock, and two of them live here:

  1. in-process: a post made through THIS instance fans out at once, as the mock does;
  2. the nudge: a CLI process tells the daemon over the socket it already owns. That op
     is the daemon's; `nudge()` is the entry point it calls;
  3. the slow poll: the pump thread wakes on the nudge event or every `poll_interval`
     (0.5 s), reads `id > last_id` once, and pushes each row to every subscription in id
     order. N subscriptions each see each id once because `last_id` moves under the same
     lock the in-process push holds.

There is no socket to drop, so `disconnect` / `reconnect` exist for the tests only.
"""

from __future__ import annotations

import contextlib
import json
import hashlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from moxi.store import transaction

from .config import LOCAL_RETAIN_DAYS
from .machine_line import parse as parse_machine_line
from .record import (
    AUTHOR_IDENTITY,
    AUTHOR_OPERATOR,
    MAX_CONTENT_LENGTH,
    Forbidden,
    RecordError,
    Message,
    Subscription,
)
from .store import open_write
from .store.schema import ID_BASE, META_NEXT_ID

#: Days of message bodies kept; `0` disables the trim. One owner: the schema's default
#: for `[local] retain_days` (settings.toml), read through `config`.
RETAIN_DAYS = LOCAL_RETAIN_DAYS

BUSY_TIMEOUT_MS = 5_000

META_OPERATOR_TOKEN_HASH = "operator_token_hash"


def token_hash(token: str) -> str:
    """One hashing rule for the whole backend: posting tokens and the operator's."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LocalRecord:
    """The box's own record. Every method here is one the seam's callers already use."""

    max_content_length = MAX_CONTENT_LENGTH
    #: `history(after=)` with no limit reads the whole range.
    history_bound = None

    def __init__(self, record=None, *, retain_days: int = RETAIN_DAYS,
                 poll_interval: float = 0.5, clock=time.time,
                 busy_timeout: float | None = None):
        from .paths import comms_db

        self.path = Path(record) if record else comms_db()
        self.retain_days = retain_days
        self.poll_interval = poll_interval
        #: How long a statement waits on another writer's lock, in milliseconds: a
        #: caller's deadline, else `BUSY_TIMEOUT_MS`.
        self.busy_timeout_ms = (BUSY_TIMEOUT_MS if busy_timeout is None
                                else max(0, int(busy_timeout * 1000)))
        self.clock = clock            # record timestamps; injectable so tests can age mail
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._lock = threading.RLock()
        self._subs: list[Subscription] = []
        self._connected = True
        self._pump: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        # The first connection opens the store through its gate now, so a store that
        # can't be opened refuses here and not at the first post.
        self._conn()

    # ------------------------------------------------------------------ connections
    def _conn(self) -> sqlite3.Connection:
        """One connection per thread. WAL lets them all read while one writes.

        Opened through `store.open_write`, which opens with the core's store connect: a
        store it creates is private to its owner (0600) from its first byte and starts
        in WAL, in a directory left as it was when it was there already (a missing one
        is made 0700); an existing store is opened as it is, its mode and journal mode
        untouched.
        A thread that is done with the record calls `release()`, or its connection stays
        open until `close()`.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # check_same_thread=False so `close()` may be called from any thread; the
            # connection is still only ever USED by the thread it was opened for.
            conn = open_write(self.path, timeout=self.busy_timeout_ms / 1000.0,
                              check_same_thread=False)
            self._local.conn = conn
            with self._lock:
                self._conns.append(conn)
        return conn

    def release(self) -> None:
        """Close the calling thread's connection, if it has one, and forget it.

        For a thread that is about to end while the record stays open: without this each
        such thread leaves one connection open until `close()`. The daemon serves its
        clients on a fixed pool whose threads live as long as its socket, so it needs no
        release per client. The thread's next call opens a new one.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._lock:
            self._conns = [c for c in self._conns if c is not conn]
        with contextlib.suppress(sqlite3.Error):
            conn.close()

    @contextlib.contextmanager
    def _write(self):
        """`BEGIN IMMEDIATE`, always (`moxi.store.transaction`). A DEFERRED transaction
        that reads the counter before it upgrades computes a stale id and two writers
        collide."""
        with transaction(self._conn()) as conn:
            yield conn

    def _next_id(self, conn: sqlite3.Connection) -> int:
        conn.execute("UPDATE meta SET value = value + 1 WHERE key = ?", (META_NEXT_ID,))
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_NEXT_ID,)).fetchone()
        return int(row[0])

    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))

    # ------------------------------------------------------------------ meta
    def meta_get(self, key: str) -> str | None:
        row = self._conn().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def meta_set(self, key: str, value: str) -> None:
        with self._write() as conn:
            conn.execute("INSERT INTO meta(key, value) VALUES (?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (key, str(value)))

    # ------------------------------------------------------------------ channels
    def create_channel(self, name: str) -> str:
        with self._write() as conn:
            row = conn.execute("SELECT room_id FROM rooms WHERE name = ?",
                               (name,)).fetchone()
            if row:
                return row[0]
            cid = f"ch-{self._next_id(conn)}"
            conn.execute("INSERT INTO rooms(room_id, name, created_at) VALUES (?,?,?)",
                         (cid, name, self._now()))
            return cid

    def channel_by_name(self, name: str) -> str | None:
        row = self._conn().execute("SELECT room_id FROM rooms WHERE name = ?",
                                   (name,)).fetchone()
        return row[0] if row else None

    def channel_name(self, channel_id: str) -> str | None:
        row = self._conn().execute("SELECT name FROM rooms WHERE room_id = ?",
                                   (str(channel_id),)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------ teardown
    def delete_channel(self, channel_id: str) -> None:
        channel_id = str(channel_id)
        with self._write() as conn:
            conn.execute("DELETE FROM messages WHERE room_id = ?", (channel_id,))
            conn.execute("DELETE FROM rooms WHERE room_id = ?", (channel_id,))

    # ------------------------------------------------------------------ permissions
    def register_principal(self, name: str) -> str:
        """A non-webhook principal (a bot user, the operator)."""
        with self._write() as conn:
            row = conn.execute("SELECT principal_id FROM principals WHERE name = ?",
                               (name,)).fetchone()
            if row:
                return row[0]
            pid = f"pr-{self._next_id(conn)}"
            conn.execute("INSERT INTO principals(principal_id, name) VALUES (?,?)",
                         (pid, name))
            return pid

    def deny_send(self, principal: str, channel_id: str) -> None:
        with self._write() as conn:
            conn.execute("INSERT OR IGNORE INTO denied(principal_id, channel_id) VALUES (?,?)",
                         (str(principal), str(channel_id)))

    def can_send(self, principal: str, channel_id: str) -> bool:
        row = self._conn().execute(
            "SELECT 1 FROM denied WHERE principal_id = ? AND channel_id = ?",
            (str(principal), str(channel_id))).fetchone()
        return row is None

    # ------------------------------------------------------------------ posting
    def post_as_user(self, user_id: str, channel_id: str, content: str, *,
                     token: str | None = None) -> Message:
        """The operator's lane, guarded by a credential like every other lane.

        The hash rests in `meta('operator_token_hash')`, written by provision. The
        SENDER side of the guard (`MOXI_COMMS_OPERATOR`, refused inside a claimed session) is
        the send verb's; what this method owns is the compare. Once a hash is stored,
        a post with no token or with the wrong one is refused; with no hash stored there
        is nothing to compare against and the lane is open, which is the state of a
        record that has not been provisioned for the operator yet.
        """
        if len(content) > self.max_content_length:
            raise RecordError(
                f"content is {len(content)} characters, "
                f"over the {self.max_content_length}-character cap on one message")
        stored = self.meta_get(META_OPERATOR_TOKEN_HASH)
        if stored and (token is None or not secrets.compare_digest(
                stored, token_hash(token))):
            # A stored hash makes the token REQUIRED: a call that presents none is the
            # unguarded lane this guard closes, not a call that gets a pass.
            raise Forbidden("bad operator token")
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM denied WHERE principal_id = ? AND channel_id = ?",
                            (str(user_id), str(channel_id))).fetchone():
                raise Forbidden(f"Send denied in {channel_id}")
            row = conn.execute("SELECT name FROM principals WHERE principal_id = ?",
                               (str(user_id),)).fetchone()
            msg = self._insert(conn, channel_id=str(channel_id), author_id=str(user_id),
                               author_kind=AUTHOR_OPERATOR,
                               author_display=row[0] if row else "operator",
                               content=content)
        self._fanout()
        self.nudge()
        return msg

    def post_as_identity(self, qualified: str, channel_id: str, content: str) -> Message:
        """Write an agent post after the daemon has checked its live binding."""
        if len(content) > self.max_content_length:
            raise RecordError(
                f"content is {len(content)} characters, "
                f"over the {self.max_content_length}-character cap on one message")
        with self._write() as conn:
            msg = self._insert(conn, channel_id=str(channel_id), author_id=str(qualified),
                               author_kind=AUTHOR_IDENTITY, author_display=str(qualified),
                               content=content)
        self._fanout()
        self.nudge()
        return msg

    def _insert(self, conn: sqlite3.Connection, *, channel_id: str, author_id: str,
                author_kind: str, author_display: str, content: str) -> Message:
        msg = Message(id=str(self._next_id(conn)), channel_id=channel_id,
                      author_id=author_id, author_kind=author_kind,
                      author_display=author_display, content=content,
                      timestamp=self._now())
        # Line zero is parsed once, here, so the store can ask about the interrupt mark
        # and the expiry without parsing: they are copies of what line zero says.
        ml = parse_machine_line(content)
        conn.execute(
            "INSERT INTO messages(id, room_id, author_id, author_kind, author_display, "
            "content, timestamp, interrupt, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (int(msg.id), msg.channel_id, msg.author_id, msg.author_kind,
             msg.author_display, msg.content, msg.timestamp,
             int(ml is not None and ml.interrupt),
             None if ml is None else ml.expires_at))
        return msg

    def withdraw(self, message_id: str, at: str) -> bool:
        """Mark a message withdrawn at `at`; a message already withdrawn keeps its first
        time. The daemon's delete writes the same mark in its own transaction; this is
        the record's half of the seam, so either backend answers `withdrawn_at`."""
        with self._write() as conn:
            return conn.execute("UPDATE messages SET withdrawn_at = ? WHERE id = ? AND "
                                "withdrawn_at IS NULL", (at, int(message_id))).rowcount > 0

    def operator_token_ok(self, token: str | None) -> bool:
        """The operator's credential check on its own, for a request that posts nothing
        (a delete): the same compare `post_as_user` makes."""
        stored = self.meta_get(META_OPERATOR_TOKEN_HASH)
        if not stored:
            return True
        return token is not None and secrets.compare_digest(stored, token_hash(token))

    # ------------------------------------------------------------------ the record
    _COLUMNS = ("id, room_id, author_id, author_kind, author_display, content, timestamp, "
                "withdrawn_at")

    @staticmethod
    def _message(row) -> Message:
        return Message(id=str(row[0]), channel_id=row[1], author_id=row[2],
                       author_kind=row[3], author_display=row[4], content=row[5],
                       timestamp=row[6], withdrawn_at=row[7])

    def history(self, channel_id: str, *, after: str | None = None,
                limit: int | None = None) -> list[Message]:
        """Ascending by id. With a limit this takes the TAIL.

        The boot rescan is `history(channel, limit=RESCAN_LIMIT)` with no `after`, and a
        literal "first 500 then take the tail" would scan the whole channel every boot.
        `ORDER BY id DESC LIMIT ?` walks the index backwards and stops at 500.
        """
        sql = f"SELECT {self._COLUMNS} FROM messages WHERE room_id = ?"
        params: list = [str(channel_id)]
        if after is not None:
            sql += " AND id > ?"
            params.append(int(after))
        if limit is None:
            rows = self._conn().execute(sql + " ORDER BY id", params).fetchall()
        else:
            rows = self._conn().execute(sql + " ORDER BY id DESC LIMIT ?",
                                        params + [int(limit)]).fetchall()
            rows.reverse()
        return [self._message(r) for r in rows]

    def get_message(self, message_id: str) -> Message | None:
        try:
            key = int(message_id)
        except (TypeError, ValueError):
            return None
        row = self._conn().execute(f"SELECT {self._COLUMNS} FROM messages WHERE id = ?",
                                   (key,)).fetchone()
        return self._message(row) if row else None

    def max_message_id(self, channel_id: str | None = None) -> str | None:
        """`MAX(id)` is O(log n) through the primary key (or `messages_channel`), so the
        burst of concurrent register boundary reads costs nothing."""
        if channel_id is None:
            row = self._conn().execute("SELECT MAX(id) FROM messages").fetchone()
        else:
            row = self._conn().execute("SELECT MAX(id) FROM messages WHERE room_id = ?",
                                       (str(channel_id),)).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def since(self, channel_id: str, timestamp: str) -> list[Message]:
        """Every message in `channel_id` at or after `timestamp`, ascending by id, with no
        count bound: the daemon's boot read of
        its sent index. Timestamps are the record's one fixed format, so the comparison
        is a string one."""
        rows = self._conn().execute(
            f"SELECT {self._COLUMNS} FROM messages WHERE room_id = ? AND timestamp >= ? "
            "ORDER BY id", (str(channel_id), str(timestamp))).fetchall()
        return [self._message(r) for r in rows]

    def trim(self, retain_days: int | None = None, *, keep=()) -> int:
        """Delete messages older than `retain_days`; `0` disables.

        The id counter is deliberately untouched: ids never go backwards, however much of
        the table goes away. The daemon calls this at boot and daily.
        `keep` are ids that stay whatever their age: every message with a queue row stays
        in the record until it is handled. A row's message is never trimmed while the
        row is there, so the trim touches no row.
        """
        days = self.retain_days if retain_days is None else retain_days
        if not days:
            return 0
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(self.clock() - days * 86400))
        kept = json.dumps(sorted({int(k) for k in keep if str(k).isdigit()}))
        with self._write() as conn:
            deleted = conn.execute(
                "DELETE FROM messages WHERE timestamp < ? "
                "AND id NOT IN (SELECT value FROM json_each(?)) "
                "AND id NOT IN (SELECT message_id FROM queue)",
                (cutoff, kept)).rowcount
            # A queued message stays, whatever the daemon's `keep` said.
        with contextlib.suppress(sqlite3.Error):
            self._conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return deleted or 0

    # ------------------------------------------------------------------ events
    def subscribe(self, *, from_id: str | None = None) -> Subscription:
        sub = Subscription(from_id if from_id is not None else (self.max_message_id() or "0"))
        with self._lock:
            self._subs.append(sub)
            if self._pump is None:
                self._pump = threading.Thread(target=self._pump_loop, daemon=True)
                self._pump.start()
        # The daemon's event loop sees the two shapes it sees today; a local subscription
        # loses nothing, so it says so.
        sub._push({"t": "READY", "resumed": True})
        self._fanout()
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)
        sub.close()

    def nudge(self) -> None:
        """Wake the pump now. Layer 2's daemon-socket `nudge` op calls this."""
        self._wake.set()

    def disconnect(self) -> None:
        """Tests only: there is no socket here to drop."""
        with self._lock:
            self._connected = False

    def reconnect(self, *, resume: bool = True) -> None:
        """Tests only, and the mock's meaning: a fresh IDENTIFY loses the gap."""
        with self._lock:
            self._connected = True
            subs = list(self._subs)
        newest = self.max_message_id() or "0"
        for sub in subs:
            if not resume:
                sub.last_id = newest
            sub._push({"t": "READY", "resumed": resume})
        self._fanout()

    def _pump_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.poll_interval)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self._fanout()
            except Exception:  # pragma: no cover - the pump outlives one bad pass
                pass

    def _fanout(self) -> None:
        """One query, one lock, N subscriptions, each id once.

        `last_id` moves under the same lock the in-process push takes, so a pump pass
        and a direct post can never both push the same row.
        """
        with self._lock:
            if not self._connected or not self._subs:
                return
            low = min(int(sub.last_id) for sub in self._subs)
            rows = self._conn().execute(
                f"SELECT {self._COLUMNS} FROM messages WHERE id > ? ORDER BY id",
                (low,)).fetchall()
            if not rows:
                return
            for sub in list(self._subs):
                floor = int(sub.last_id)
                for row in rows:
                    if row[0] > floor:
                        sub.last_id = str(row[0])
                        sub._push({"t": "MESSAGE_CREATE", "d": self._message(row).to_dict()})

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        for sub in list(self._subs):
            sub.close()
        with self._lock:
            for conn in self._conns:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
            self._conns.clear()
        self._local = threading.local()


__all__ = ["LocalRecord", "ID_BASE", "MAX_CONTENT_LENGTH", "RETAIN_DAYS",
           "META_NEXT_ID", "META_OPERATOR_TOKEN_HASH", "token_hash"]
