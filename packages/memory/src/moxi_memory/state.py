"""memory's state store: the session table, the rebuild entry, the waiting request, and
time-bounded calls.

The store is `state.db` in memory's state directory, opened with `moxi.store.connect`.
It is not part of the index, so a rebuild keeps it and a session start never opens the
index. It holds these tables:

- `sessions`, one row per main session (`moxi_memory.table` writes and reads it);
- `rebuild`, at most one row: the start time and process id a rebuild child wrote when
  it took the rebuild lock. Whether a rebuild is running is never read from this row
  but from the lock (`moxi_memory.index.rebuild_running`); the row only says since when.
- `request`, at most one row: the work someone asked for that hasn't been taken yet, and
  the time it was first asked (`ask`, `take_request`, `read_request`);
- `unfinished`, at most one row: the start time of a rebuild that ended without
  finishing (killed), written by the tidy that clears its entry and cleared by the next
  rebuild that finishes;
- `waiting`, one row per source an ingest couldn't read because its folder raised (an
  archive folder offline, a permission or read error): every later walk reads it again
  from its start, whatever its time, until a read reaches its end or the source is gone
  (`read_waiting`, `write_waiting`, `waiting_sessions`). A compressed Codex rollout,
  which sessions never reads, is never one.

**Requests.** A walk, a full read, a rebuild or a relabel someone asked for is never lost
to a held writer lock. The child started for it records it here before it tries the
writer lock; the child that takes the lock to run it reads and clears it in one
transaction; every child that frees the writer lock reads it afterwards and starts a child
to run it when one waits. The store holds one request at most: a new one merges into the
one waiting, keeping the higher kind (`KINDS`, lowest first, each doing all the work of the
ones below it) and the earlier time, in one statement.

A call that must not wait long (a listener's write or query) opens the store with a short
`timeout`, which is SQLite's busy timeout, and runs inside `bounded(conn, seconds)`,
which also stops a statement that runs past the same deadline with a progress handler.
Either way the call raises `Late`, and the caller skips that step.

`sqlite3` is imported inside the calls, so importing this module is cheap.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

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
#: partial indexes hold only the rows the start lists may show, ordered by `newest`;
#: the fourth step remakes them to leave out the `fork` kind as well as `scratch`.
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
    """
    CREATE TABLE request (
        one INTEGER PRIMARY KEY CHECK (one = 1),
        kind INTEGER NOT NULL,
        asked REAL NOT NULL
    );
    CREATE TABLE unfinished (
        one INTEGER PRIMARY KEY CHECK (one = 1),
        started REAL NOT NULL
    );
    """,
    """
    CREATE TABLE waiting (
        harness TEXT NOT NULL,
        source TEXT NOT NULL,
        session_id TEXT NOT NULL,
        lookup TEXT NOT NULL,
        root_dir TEXT NOT NULL,
        why TEXT NOT NULL,
        since REAL NOT NULL,
        PRIMARY KEY (harness, source)
    ) WITHOUT ROWID;
    """,    """
    DROP INDEX sessions_by_identity;
    DROP INDEX sessions_by_label;
    CREATE INDEX sessions_by_identity ON sessions (identity, newest)
        WHERE settled = 1 AND kind IS NOT 'scratch' AND kind IS NOT 'fork';
    CREATE INDEX sessions_by_label ON sessions (label, newest)
        WHERE settled = 1 AND kind IS NOT 'scratch' AND kind IS NOT 'fork';
    """,
)

#: The kinds of request, lowest first. Each does all the work of the kinds below it: a
#: full read reads every source that grew, as a walk does, and more; a rebuild reads
#: every source from its start into a new generation; a relabel is a rebuild that also
#: derives labels again.
KINDS = ("walk", "all", "rebuild", "relabel")


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
        conn = store.connect(path(env), timeout_s=timeout, create=create,
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


# -- the waiting request ---------------------------------------------------------------

class Request:
    """A request waiting in the store: its kind (one of `KINDS`) and when it was first
    asked (seconds since the epoch)."""

    __slots__ = ("kind", "asked")

    def __init__(self, kind: str, asked: float):
        self.kind = kind
        self.asked = asked

    def __repr__(self) -> str:
        return f"Request(kind={self.kind!r}, asked={self.asked!r})"

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Request)
                and (self.kind, self.asked) == (other.kind, other.asked))


def _request(row) -> Request | None:
    if row is None or not 0 <= int(row[0]) < len(KINDS):
        return None
    return Request(KINDS[int(row[0])], float(row[1]))


def ask(conn: sqlite3.Connection, kind: str, asked: float | None = None) -> None:
    """Record a request of `kind`, asked at `asked` (now by default). One waiting already
    takes it in: the higher kind is kept, and the earlier time. It is one statement, so
    two children asking at once can't leave the lower kind."""
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a kind of request")
    conn.execute("INSERT INTO request (one, kind, asked) VALUES (1, ?, ?) "
                 "ON CONFLICT (one) DO UPDATE SET kind = max(kind, excluded.kind), "
                 "asked = min(asked, excluded.asked)",
                 (KINDS.index(kind), time.time() if asked is None else asked))


def read_request(conn: sqlite3.Connection, schema: str = "main") -> Request | None:
    """The request waiting, or None. Reads only."""
    return _request(conn.execute(
        f"SELECT kind, asked FROM {schema}.request WHERE one = 1").fetchone())


def take_request(conn: sqlite3.Connection) -> Request | None:
    """Read the waiting request and clear it, in one transaction; None when none waits.
    Only the child holding the writer lock to run it calls this."""
    from moxi import store

    with store.transaction(conn):
        row = conn.execute("SELECT kind, asked FROM request WHERE one = 1").fetchone()
        conn.execute("DELETE FROM request")
    return _request(row)


def write_unfinished(conn: sqlite3.Connection, started: float) -> None:
    """Record that the rebuild that started at `started` ended without finishing."""
    conn.execute("INSERT INTO unfinished (one, started) VALUES (1, ?) ON CONFLICT (one) "
                 "DO UPDATE SET started = excluded.started", (started,))


def clear_unfinished(conn: sqlite3.Connection) -> None:
    """Forget an unfinished rebuild: a rebuild has finished since."""
    conn.execute("DELETE FROM unfinished")


def read_unfinished(conn: sqlite3.Connection) -> float | None:
    """The start time of the last rebuild that ended without finishing, or None."""
    row = conn.execute("SELECT started FROM unfinished WHERE one = 1").fetchone()
    return None if row is None else float(row[0])


# -- the sources waiting on a folder ---------------------------------------------------

class Waiting(NamedTuple):
    """A source an ingest couldn't read because its folder raised: its harness, its name
    in the index's `sources` (`source`), the main session its rows go under, the id
    sessions' `find` takes for it (`lookup`), the folder that holds it (`root_dir`), why
    it wasn't read, and since when it has waited (seconds since the epoch)."""

    harness: str
    source: str
    session_id: str
    lookup: str
    root_dir: str
    why: str
    since: float


def read_waiting(conn: sqlite3.Connection) -> list[Waiting]:
    """Every source waiting on a folder. Reads only."""
    return [Waiting(*row) for row in conn.execute(
        "SELECT harness, source, session_id, lookup, root_dir, why, since FROM waiting "
        "ORDER BY harness, root_dir, lookup, source")]


def write_waiting(conn: sqlite3.Connection, add: Iterable[Waiting],
                  cleared: Iterable[tuple[str, str]], *, replace: bool = False) -> None:
    """In one transaction: with `replace`, forget every source waiting first; record each
    of `add` (one already waiting keeps its `since`); and forget each `(harness, source)`
    of `cleared`."""
    from moxi import store

    with store.transaction(conn):
        if replace:
            conn.execute("DELETE FROM waiting")
        conn.executemany(
            "INSERT INTO waiting (harness, source, session_id, lookup, root_dir, why, since) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (harness, source) DO UPDATE SET "
            "session_id = excluded.session_id, lookup = excluded.lookup, "
            "root_dir = excluded.root_dir, why = excluded.why", list(add))
        conn.executemany("DELETE FROM waiting WHERE harness = ? AND source = ?",
                         list(cleared))


def has_waiting(conn: sqlite3.Connection, schema: str = "main") -> bool:
    """Whether the store `schema` names (one attached to an index reader, say) holds the
    `waiting` table: a store from before it doesn't until memory next opens it to write."""
    return conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' "
                        "AND name = 'waiting'").fetchone() is not None


def waiting_sessions(conn: sqlite3.Connection, schema: str = "main") -> int:
    """How many main sessions have a source waiting on a folder; 0 for a store from
    before the table."""
    if not has_waiting(conn, schema):
        return 0
    return conn.execute(f"SELECT count(*) FROM (SELECT DISTINCT harness, session_id "
                        f"FROM {schema}.waiting)").fetchone()[0]


def peek(env: Mapping[str, str] | None = None) -> tuple[Request | None, float | None]:
    """The waiting request and the start of the last unfinished rebuild, read without
    creating the store, migrating it or taking a lock; None for each it can't read."""
    import sqlite3

    from moxi import store

    try:
        conn = store.connect(path(env), create=False, timeout_s=0.25)
    except (store.StoreError, sqlite3.Error):
        return None, None
    try:
        conn.execute("PRAGMA query_only = 1")
        out = []
        for read in (read_request, read_unfinished):
            try:
                out.append(read(conn))
            except sqlite3.Error:
                out.append(None)
        return out[0], out[1]
    finally:
        conn.close()


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


__all__ = ["STATE_DB", "SCHEMA", "KINDS", "Late", "Entry", "Request", "path", "connect",
           "bounded", "write_entry", "clear_entry", "read_entry", "ask", "read_request",
           "take_request", "peek", "write_unfinished", "clear_unfinished",
           "read_unfinished", "attach", "Waiting", "read_waiting", "write_waiting",
           "has_waiting", "waiting_sessions"]
