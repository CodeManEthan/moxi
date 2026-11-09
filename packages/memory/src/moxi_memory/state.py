"""memory's state store: the session table, the rebuild entry, and time-bounded calls.

The store is `state.db` in memory's state directory, opened with `moxi.store.connect`.
It is not part of the index, so a rebuild keeps it and a session start never opens the
index. It holds two tables:

- `sessions`, one row per main session (`moxi_memory.table` writes and reads it);
- `rebuild`, at most one row: the start time and process id a rebuild child wrote when
  it took the rebuild lock. Whether a rebuild is running is never read from this row
  but from the lock (`moxi_memory.index.rebuild_running`); the row only says since when.

A call that must not wait long (a listener's write or query) opens the store with a short
`timeout`, which is SQLite's busy timeout, and runs inside `bounded(conn, seconds)`,
which also stops a statement that runs past the same deadline with a progress handler.
Either way the call raises `Late`, and the caller skips that step.

`sqlite3` is imported inside the calls, so importing this module is cheap.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

TYPE_CHECKING = False
if TYPE_CHECKING:
    import sqlite3

#: The store's file name in memory's state directory.
STATE_DB = "state.db"
#: How many SQLite virtual machine steps run between two checks of a bounded call's
#: deadline. A step is well under a microsecond, so the check costs nothing and a long
#: statement is stopped within a millisecond or so of its deadline.
PROGRESS_STEPS = 1000

#: The store's schema, one migration step per entry (`moxi.store.migrate`).
#:
#: `sessions`: `newest` is the time, in seconds since the epoch, of the newest record a
#: child read of the session, or before any read the time a listener first wrote the
#: row. `written` is the time of the row's last write of any kind; a rebuild keeps a
#: row written after it started. `settled` is 1 once a child has read a model answer and
#: asked the launcher for the session's kind (or found no launcher to ask). The two
#: partial indexes hold only the rows the start lists may show, ordered by `newest`.
SCHEMA = (
    """
    CREATE TABLE sessions (
        harness TEXT NOT NULL,
        session_id TEXT NOT NULL,
        identity TEXT,
        label TEXT,
        kind TEXT,
        settled INTEGER NOT NULL DEFAULT 0,
        newest REAL NOT NULL,
        written REAL NOT NULL,
        PRIMARY KEY (harness, session_id)
    ) WITHOUT ROWID;
    CREATE INDEX sessions_by_identity ON sessions (identity, newest)
        WHERE settled = 1 AND kind IS NOT 'scratch';
    CREATE INDEX sessions_by_label ON sessions (label, newest)
        WHERE settled = 1 AND kind IS NOT 'scratch';
    CREATE INDEX sessions_by_kind ON sessions (kind);
    CREATE TABLE rebuild (
        one INTEGER PRIMARY KEY CHECK (one = 1),
        started REAL NOT NULL,
        pid INTEGER NOT NULL
    );
    """,
)


class Late(Exception):
    """A bounded call didn't finish in its time: the store stayed locked, or a statement
    ran past its deadline. Nothing it would have written was kept."""


def path(env: Mapping[str, str] | None = None) -> Path:
    """Where the state store lives."""
    from .settings import state_dir

    return state_dir(env) / STATE_DB


def connect(env: Mapping[str, str] | None = None, *, timeout: float = 5.0,
            create: bool = True, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the state store, creating it and bringing its schema up to date.

    `timeout` is SQLite's busy timeout for every statement on this connection, the first
    open's schema step included. With `create=False` a missing store raises
    `moxi.store.StoreError` and nothing is written. A lock held past `timeout` while the
    store is opened raises `Late`.
    """
    import sqlite3

    from moxi import store

    try:
        conn = store.connect(path(env), timeout=timeout, create=create,
                             check_same_thread=check_same_thread)
    except sqlite3.OperationalError as exc:
        if _is_late(exc):
            raise Late(str(exc)) from None
        raise
    try:
        store.migrate(conn, SCHEMA)
    except sqlite3.OperationalError as exc:
        conn.close()
        if _is_late(exc):
            raise Late(str(exc)) from None
        raise
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def bounded(conn: sqlite3.Connection, seconds: float) -> Iterator[sqlite3.Connection]:
    """Run the block's statements on `conn` within `seconds`, or raise `Late`.

    For the block, the busy timeout is `seconds` and a progress handler interrupts any
    statement still running once `seconds` have passed since the block began. A
    transaction the block left open is rolled back before `Late` is raised. The
    connection's own busy timeout and handler are put back when the block ends.
    """
    import sqlite3

    seconds = max(0.0, seconds)
    deadline = time.monotonic() + seconds
    before = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    conn.execute(f"PRAGMA busy_timeout = {int(seconds * 1000)}")
    conn.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0,
                              PROGRESS_STEPS)
    try:
        yield conn
    except sqlite3.OperationalError as exc:
        if not _is_late(exc):
            raise
        if conn.in_transaction:
            conn.set_progress_handler(None, 0)
            conn.execute("ROLLBACK")
        raise Late(str(exc)) from None
    finally:
        conn.set_progress_handler(None, 0)
        conn.execute(f"PRAGMA busy_timeout = {before}")


def _is_late(exc: Exception) -> bool:
    text = str(exc).lower()
    return "interrupted" in text or "locked" in text or "busy" in text


# -- the rebuild entry ----------------------------------------------------------------

class Entry:
    """The rebuild entry as written: the start time (seconds since the epoch) and the
    process id of the child that wrote it."""

    __slots__ = ("started", "pid")

    def __init__(self, started: float, pid: int):
        self.started = started
        self.pid = pid

    def __repr__(self) -> str:
        return f"Entry(started={self.started!r}, pid={self.pid!r})"

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Entry)
                and (self.started, self.pid) == (other.started, other.pid))


def write_entry(conn: sqlite3.Connection, started: float, pid: int | None = None) -> None:
    """Record a rebuild that started at `started`, run by `pid` (this process's by
    default)."""
    conn.execute("INSERT INTO rebuild (one, started, pid) VALUES (1, ?, ?) "
                 "ON CONFLICT (one) DO UPDATE SET started = excluded.started, "
                 "pid = excluded.pid", (started, os.getpid() if pid is None else pid))


def clear_entry(conn: sqlite3.Connection) -> None:
    """Remove the rebuild entry, if there is one."""
    conn.execute("DELETE FROM rebuild")


def read_entry(conn: sqlite3.Connection) -> Entry | None:
    """The rebuild entry as written, or None. It says nothing about whether a rebuild is
    running; only the rebuild lock does."""
    row = conn.execute("SELECT started, pid FROM rebuild WHERE one = 1").fetchone()
    return None if row is None else Entry(float(row[0]), int(row[1]))


def attach(conn: sqlite3.Connection, alias: str = "state",
           env: Mapping[str, str] | None = None) -> bool:
    """Attach the state store to `conn` (an index reader) as `alias`, so a query can read
    the session table beside the rows. Returns False, attaching nothing, when there is no
    state store yet. Set `PRAGMA query_only` on `conn` to keep the attached store
    read-only; the index reader does."""
    where = path(env)
    if not where.is_file():
        return False
    if not alias.isidentifier():
        raise ValueError(f"{alias!r} is not a plain name")
    conn.execute(f"ATTACH DATABASE ? AS {alias}", (os.fspath(where),))
    return True


__all__ = ["STATE_DB", "SCHEMA", "Late", "Entry", "path", "connect", "bounded",
           "write_entry", "clear_entry", "read_entry", "attach"]
