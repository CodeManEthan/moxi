"""The binding history: comms.db's `bindings` table.

The daemon's routing table is memory only and dies with the socket. This table keeps
the binding history used by the never-bound check and its scope hint, and the intervals
attribution reads to verify an author.

What it is NOT: it is never consulted for routing or addressability. A binding
history row is history, not a live binding, and nothing here can make a dead agent
addressable.

The daemon writes it, through `store.open_write`; every other consumer opens it
read-only (`store.open_read`). The store is in WAL, which is why a reader can read while
the daemon holds it open: an attribution read that fails is an attribution read that lies
by omission.

Best-effort by construction: every live operation here swallows its own failures and
says so in the log.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from moxi.store import transaction

from .paths import comms_db
from .store import open_read, open_write

log = logging.getLogger("comms.bindings")

#: The record's timestamp format, the one the record normalizes timestamps into.
#: Containment is a string comparison because this format sorts chronologically.
TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def stamp(t: float) -> str:
    """Epoch seconds -> the record's own timestamp spelling."""
    return time.strftime(TIMESTAMP_FMT, time.gmtime(t))


def connect(path: str | Path | None = None, *, read_only: bool = False,
            env=None) -> sqlite3.Connection:
    """Open the store that holds the binding history (`path`, else comms.db), rows as
    `sqlite3.Row`, usable from any thread."""
    opener = open_read if read_only else open_write
    conn = opener(path, env=env, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init(path: str | Path | None = None, *, env=None) -> sqlite3.Connection:
    """The daemon's connection, through the store's gate. Only the daemon's."""
    return connect(path, env=env)


# ------------------------------------------------------------------------- the writer

class BindingHistory:
    """The daemon's custody writer: bind, release, and the boot fence. Nothing else.

    Every method is failure-swallowing on purpose (see the module docstring): a write
    that fails is logged and the binding proceeds. The alternative -- refusing to bind
    an agent because a history file would not open -- trades the product for its
    evidence.
    """

    def __init__(self, path: str | Path | None = None, *, clock=time.time):
        self.path = Path(path) if path is not None else None
        self.clock = clock
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _resolved(self) -> Path:
        return self.path if self.path is not None else comms_db()

    # -- lifecycle ----------------------------------------------------------
    def open(self) -> bool:
        """Open (creating) the binding history. False means custody goes unrecorded this run.
        A store its gate refuses (`store.StoreRefused`) raises: the daemon doesn't start
        on it."""
        with self._lock:
            if self._conn is not None:
                return True
            try:
                self._conn = init(self._resolved())
            except (sqlite3.Error, OSError) as e:
                log.warning("binding history unavailable (%s: %s); bindings will not be "
                            "recorded and replay attribution stays unverified",
                            type(e).__name__, e)
                self._conn = None
                return False
            return True

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None

    # -- the boot fence -----------------------------------------------------
    def fence(self, at: float | None = None) -> int:
        """Close every interval left open by a daemon that did not shut down cleanly.

        This is SAFE rather than approximate. While the daemon is down no new binding
        can exist and no moxi comms send can resolve, so nothing competes for the seat in the
        gap: the holder at the moment of the crash is the last holder there was, and the
        fence records that the interval ENDED without witnessing the release (`fenced=1`)
        rather than pretending the binding ran to now.

        A fenced row records only that the daemon did not witness a clean release. A
        reserved name's interval is no binding a daemon held, and stays open.
        """
        with self._lock:
            if self._conn is None:
                return 0
            marker = stamp(self.clock() if at is None else at)
            try:
                with transaction(self._conn) as conn:
                    cur = conn.execute(
                        "UPDATE bindings SET released_at = ?, fenced = 1 "
                        "WHERE released_at IS NULL AND kind = 'session'", (marker,))
            except sqlite3.Error:
                log.exception("boot fence failed; dangling custody intervals remain open")
                return 0
            return cur.rowcount or 0

    # -- the two moments only the daemon witnesses --------------------------
    def bind(self, *, qualified: str, name: str, scope: str, tier: str,
             adapter: str, at: float | None = None) -> int | None:
        """Open an interval. Returns its row id, or None if unrecorded."""
        with self._lock:
            if self._conn is None:
                return None
            try:
                with transaction(self._conn) as conn:
                    cur = conn.execute(
                        "INSERT INTO bindings(qualified, name, scope, tier, adapter, "
                        "bound_at) VALUES (?,?,?,?,?,?)",
                        (qualified, name, scope, tier, adapter,
                         stamp(self.clock() if at is None else at)))
            except sqlite3.Error:
                # logged, never fatal: the binding is real whether or not it is recorded
                log.exception("binding history: failed to record the bind of %s", qualified)
                return None
            return cur.lastrowid

    def release(self, row_id: int | None, at: float | None = None) -> None:
        """Close an interval at the connection-close deregistration."""
        if row_id is None:
            return
        with self._lock:
            if self._conn is None:
                return
            try:
                with transaction(self._conn) as conn:
                    conn.execute(
                        "UPDATE bindings SET released_at = ? WHERE id = ? "
                        "AND released_at IS NULL",
                        (stamp(self.clock() if at is None else at), int(row_id)))
            except sqlite3.Error:
                log.exception("binding history: failed to record the release of row %s",
                              row_id)

    # -- read-back for the daemon's never-bound checks -----------------------
    def ever_bound(self, name: str, scope: str) -> bool | None:
        """Has the identity (name, scope) ever been bound on this box? None when the
        binding history is not open or the read fails: the caller then does not know, and the
        send's never-bound check does not run."""
        with self._lock:
            if self._conn is None:
                return None
            try:
                row = self._conn.execute(
                    "SELECT 1 FROM bindings WHERE name = ? AND scope = ? LIMIT 1",
                    (name, scope)).fetchone()
            except sqlite3.Error:
                return None
            return row is not None

    def known_scopes(self, name: str) -> list:
        """Every scope this NAME has ever been bound in, most recent first. Read only for
        the never-bound refusal's hint (the identities of that name that did exist);
        never to decide which identity a name means."""
        with self._lock:
            if self._conn is None:
                return []
            try:
                rows = self._conn.execute(
                    "SELECT scope, MAX(id) AS last FROM bindings WHERE name = ? "
                    "GROUP BY scope ORDER BY last DESC", (name,)).fetchall()
            except sqlite3.Error:
                return []
            return [str(r["scope"]) for r in rows]

    def intervals(self) -> list:
        with self._lock:
            if self._conn is None:
                return []
            try:
                return self._conn.execute(
                    "SELECT * FROM bindings ORDER BY id").fetchall()
            except sqlite3.Error:
                return []


__all__ = ["BindingHistory", "TIMESTAMP_FMT", "connect", "init", "stamp"]
