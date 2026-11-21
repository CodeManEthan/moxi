"""The index: its schema, its generation files, and the locks that keep readers safe.

The index is derived: it can be deleted and rebuilt from the transcripts, and holds
nothing they can't give back. It lives in the `index` folder of memory's cache directory
(a store the announcement declares rebuildable, so an uninstall's purge removes it) as a
series of generation files, `index-<n>.db`, each with a lock file `index-<n>.lock`
beside it. A small pointer file, `index.current`, names the current generation and is replaced in one
step (`moxi.files.atomic_write`). No file is ever renamed over, or removed from under, a
database a process may hold open.

Readers (search, status) call `open_read()`. It reads the pointer, takes a shared
`fcntl.flock` on the generation's lock file, and opens the generation without creating
it, read-only. It holds the lock until it closes. When the generation is gone, removed
between the pointer read and the open, it reads the pointer once more. A reader never
takes the writer lock, even for a moment.

Writers (the ingest child, the rebuild) work inside `writer()`, which holds the writer
lock (`moxi.files.lock` on `writer.lock` in memory's state directory) for the length of
the block. A child that finds it held gets `WriterBusy` and exits (what it was asked to
do waits in the state store when someone asked for it; see `moxi_memory.ingest`). On
entry the writer tidies:

- it clears a rebuild entry whose rebuild lock is free (a crashed rebuild's), and records
  that rebuild's start as unfinished, for the doctor; it leaves a waiting request alone;
- it removes the file of a generation past the pointer, which a crashed rebuild left
  half built;
- it removes each generation older than the current one whose lock file it can take
  exclusively without waiting; one a reader still holds is left for the next child;
- on a machine with no generation it makes `index-1.db` and points at it before
  anything is read, so a search during the first full read finds a filling index, not a
  missing one.

A rebuild holds the writer lock for its whole run, and inside it `Writer.rebuilding()`,
which takes `rebuild.lock` in the state directory (exclusive, waiting out any reader's
moment on it) and writes the rebuild entry. A rebuild is running exactly while that
lock is held, so `rebuild_running()` tests the lock with a shared, non-waiting `flock`
and lets it go at once: refused means a rebuild is running, and the entry says since
when; granted means none is, whatever the entry says. The entry's process id is shown
for information only. The rebuild builds its file with `Writer.new_generation()` and
ends with `Writer.switch()`.

Every generation stems the same way: the tokenizer `TOKENIZER`, shared by the text and
the query, with no extra token characters. Changing it means a rebuild.

The shared lock is `fcntl.flock`, since `moxi.files.lock` is exclusive only, so this
module runs on Linux and other POSIX systems only, as turns does.
"""

from __future__ import annotations

import errno
import os
import re
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

TYPE_CHECKING = False
if TYPE_CHECKING:
    import sqlite3

#: The folder in memory's cache directory that holds the generations, their locks and the
#: pointer.
INDEX_DIR = "index"
#: The pointer file's name in the index folder.
POINTER = "index.current"
#: The writer lock's file name in memory's state directory.
WRITER_LOCK = "writer.lock"
#: The rebuild lock's file name in memory's state directory.
REBUILD_LOCK = "rebuild.lock"
#: The tokenizer every generation's text tables use, and so every query.
TOKENIZER = "porter unicode61 remove_diacritics 2"
#: The `meta` key that says which secrets `redact` stored a generation's rows.
REDACT_VERSION_KEY = "redact_version"
#: How long a reader waits for a generation's shared lock. Only a child removing that
#: generation holds it exclusively, for the moment the removal takes.
READ_LOCK_WAIT = 2.0
#: How long a rebuild waits for the rebuild lock, which a reader holds only for the
#: moment its test takes.
REBUILD_LOCK_WAIT = 10.0
#: How often a waiting lock take tries again, in seconds.
POLL = 0.02

_GENERATION = re.compile(r"index-([1-9][0-9]{0,17})\.(db|lock)")
_POINTER_TEXT = re.compile(r"index-([1-9][0-9]{0,17})\.db")
#: The files SQLite may keep beside a database.
_DB_SUFFIXES = ("", "-wal", "-shm", "-journal")

#: Each generation's schema, one migration step per entry (`moxi.store.migrate`).
#:
#: `rows` holds tier 1 and subagent rows. `session_id` is always the main session's id,
#: so a subagent's rows take their main session's label and launch kind; `agent_id` is
#: the subagent's own session id, and is NULL on a main session's row. `kind` is the
#: row's kind (a message's role, or a tool call). `key` names the one record the row
#: came from. `time` is seconds since the epoch.
#:
#: `tails` holds tier 2: the end of each main-session tool result, one row per record
#: under that record's key, with whether the tool failed.
#:
#: `sources` holds each source's read cursor and reuse keys, so a cursor goes when the
#: index goes. `secret_calls` holds the call ids of each source's secret-path calls, whose
#: results give no tail. `meta` holds the schema version, the tokenizer, whether the first full
#: read has finished (`first_read`, "0" or "1"), which secrets `redact` stored the rows
#: (`redact_version`, written when the generation is made; one without it counts as 1),
#: and whatever else a writer records.
SCHEMA = (
    f"""
    CREATE TABLE rows (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,
        harness TEXT NOT NULL,
        session_id TEXT NOT NULL,
        agent_id TEXT,
        label TEXT,
        time REAL,
        turn_id TEXT,
        kind TEXT NOT NULL,
        text TEXT NOT NULL
    );
    CREATE INDEX rows_by_session ON rows (harness, session_id);
    CREATE INDEX rows_by_time ON rows (time);
    CREATE VIRTUAL TABLE rows_fts USING fts5(
        text, content = 'rows', content_rowid = 'id', tokenize = '{TOKENIZER}');
    CREATE TRIGGER rows_ai AFTER INSERT ON rows BEGIN
        INSERT INTO rows_fts (rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER rows_ad AFTER DELETE ON rows BEGIN
        INSERT INTO rows_fts (rows_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    CREATE TRIGGER rows_au AFTER UPDATE ON rows BEGIN
        INSERT INTO rows_fts (rows_fts, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO rows_fts (rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TABLE tails (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,
        harness TEXT NOT NULL,
        session_id TEXT NOT NULL,
        label TEXT,
        time REAL,
        turn_id TEXT,
        failed INTEGER NOT NULL DEFAULT 0,
        text TEXT NOT NULL
    );
    CREATE INDEX tails_by_session ON tails (harness, session_id);
    CREATE INDEX tails_by_time ON tails (time);
    CREATE VIRTUAL TABLE tails_fts USING fts5(
        text, content = 'tails', content_rowid = 'id', tokenize = '{TOKENIZER}');
    CREATE TRIGGER tails_ai AFTER INSERT ON tails BEGIN
        INSERT INTO tails_fts (rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER tails_ad AFTER DELETE ON tails BEGIN
        INSERT INTO tails_fts (tails_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    CREATE TRIGGER tails_au AFTER UPDATE ON tails BEGIN
        INSERT INTO tails_fts (tails_fts, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO tails_fts (rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TABLE sources (
        harness TEXT NOT NULL,
        source TEXT NOT NULL,
        session_id TEXT,
        cursor TEXT,
        reuse TEXT,
        read REAL,
        PRIMARY KEY (harness, source)
    );
    CREATE TABLE secret_calls (
        harness TEXT NOT NULL,
        source TEXT NOT NULL,
        call_id TEXT NOT NULL,
        PRIMARY KEY (harness, source, call_id)
    ) WITHOUT ROWID;
    CREATE TABLE meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    INSERT INTO meta (key, value) VALUES ('schema', '1'), ('tokenizer', '{TOKENIZER}'),
        ('first_read', '0');
    """,
)


class IndexMissing(Exception):
    """There is no index to read: no pointer, or a pointer to a generation that isn't
    there."""


class IndexUnreadable(Exception):
    """The index is there but can't be read: a damaged file, one written by a newer
    version of memory, or a generation lock that stayed held."""


class StateUnreadable(IndexUnreadable):
    """The index can be read but memory's state store, which holds the session table a
    search reads beside it, can't. A rebuild doesn't repair it: the rebuild opens the
    same store."""


class WriterBusy(Exception):
    """Another child holds the writer lock."""


class Rebuilding:
    """A rebuild that is running: when it started (seconds since the epoch) and the
    process id it wrote, each None when the entry isn't written yet or can't be read.
    The process id is for information only."""

    __slots__ = ("started", "pid")

    def __init__(self, started: float | None, pid: int | None):
        self.started = started
        self.pid = pid

    def __repr__(self) -> str:
        return f"Rebuilding(started={self.started!r}, pid={self.pid!r})"


# -- places ---------------------------------------------------------------------------

def index_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where the generations, their locks and the pointer live: the `index` folder of
    memory's cache directory."""
    from .settings import cache_dir

    return cache_dir(env) / INDEX_DIR


def db_path(n: int, env: Mapping[str, str] | None = None) -> Path:
    """Generation `n`'s database file."""
    return index_dir(env) / f"index-{n}.db"


def lock_path(n: int, env: Mapping[str, str] | None = None) -> Path:
    """Generation `n`'s lock file."""
    return index_dir(env) / f"index-{n}.lock"


def pointer_path(env: Mapping[str, str] | None = None) -> Path:
    return index_dir(env) / POINTER


def writer_lock_path(env: Mapping[str, str] | None = None) -> Path:
    from .settings import state_dir

    return state_dir(env) / WRITER_LOCK


def rebuild_lock_path(env: Mapping[str, str] | None = None) -> Path:
    from .settings import state_dir

    return state_dir(env) / REBUILD_LOCK


def current(env: Mapping[str, str] | None = None) -> int | None:
    """The generation the pointer names, or None when there is no pointer or it names
    nothing this module wrote."""
    try:
        text = pointer_path(env).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    match = _POINTER_TEXT.fullmatch(text)
    return int(match.group(1)) if match else None


def generations(env: Mapping[str, str] | None = None) -> list[int]:
    """Every generation number with a database or lock file on disk, in order."""
    try:
        names = os.listdir(index_dir(env))
    except OSError:
        return []
    found = set()
    for name in names:
        base = name
        for suffix in _DB_SUFFIXES[1:]:
            base = base.removesuffix(suffix)
        if match := _GENERATION.fullmatch(base):
            found.add(int(match.group(1)))
    return sorted(found)


# -- meta -----------------------------------------------------------------------------

def meta_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """A generation's meta value, or `default`."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return default if row is None else row[0]


def meta_set(conn: sqlite3.Connection, key: str, value: object) -> None:
    """Set a generation's meta value (stored as text)."""
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, str(value)))


def stored_redact_version(conn: sqlite3.Connection) -> int:
    """Which secrets `redact` stored a generation's rows: its `redact_version`, or 1 for
    a generation without one (made before memory recorded it)."""
    value = meta_get(conn, REDACT_VERSION_KEY)
    try:
        return int(value) if value is not None else 1
    except ValueError:
        return 1


# -- readers --------------------------------------------------------------------------

class Reader:
    """An open generation, read-only, with its shared lock held until `close`."""

    __slots__ = ("conn", "generation", "path", "_fd", "_env")

    def __init__(self, conn, generation: int, path: Path, fd: int, env):
        self.conn = conn
        self.generation = generation
        self.path = path
        self._fd = fd
        self._env = env

    def attach_state(self, alias: str = "state") -> bool:
        """Attach memory's state store (the session table) to this reader's connection
        as `alias`, read-only. False when there is no state store yet."""
        from . import state

        return state.attach(self.conn, alias, self._env)

    def close(self) -> None:
        conn, fd = self.conn, self._fd
        self.conn, self._fd = None, None
        try:
            if conn is not None:
                conn.close()
        finally:
            if fd is not None:
                _unlock(fd)

    def __enter__(self) -> Reader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_read(env: Mapping[str, str] | None = None, *,
              lock_wait: float = READ_LOCK_WAIT) -> Reader:
    """Open the current generation to read. Use it as a context manager, or `close` it.

    Raises `IndexMissing` when there is no pointer, or when the generation it names is
    gone at the second reading too, and `IndexUnreadable` when the file can't be read as
    an index or its lock stayed held past `lock_wait` seconds.
    """
    import sqlite3

    from moxi import store

    from . import settings

    seen = None
    for _ in range(2):
        n = current(env)
        if n is None:
            raise IndexMissing(f"no index in {index_dir(env)}")
        seen = n
        fd = _shared(lock_path(n, env), lock_wait)
        if fd is None:
            continue
        path = db_path(n, env)
        try:
            conn = store.connect(path, create=False, timeout_s=lock_wait)
        except store.StoreError:
            _unlock(fd)
            continue
        except sqlite3.Error as exc:
            _unlock(fd)
            raise IndexUnreadable(f"{path}: {exc}") from None
        try:
            conn.execute("PRAGMA query_only = 1")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > len(SCHEMA):
                raise IndexUnreadable(f"{path} was written by a newer version of "
                                      f"{settings.MODULE} (schema {version})")
            if version < 1:
                raise IndexUnreadable(f"{path} holds no index")
            conn.execute("SELECT 1 FROM meta LIMIT 1").fetchall()
        except sqlite3.Error as exc:
            conn.close()
            _unlock(fd)
            raise IndexUnreadable(f"{path}: {exc}") from None
        except BaseException:
            conn.close()
            _unlock(fd)
            raise
        return Reader(conn, n, path, fd, env)
    raise IndexMissing(f"generation {seen} named by {pointer_path(env)} is gone")


def _shared(path: Path, wait: float) -> int | None:
    """A descriptor holding a shared `flock` on lock file `path`, or None when the file
    is gone (it was removed with its generation). Raises `IndexUnreadable` when the lock
    stays held exclusively past `wait` seconds."""
    import fcntl

    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise IndexUnreadable(f"{path} stayed locked for {wait:g} s") from None
                time.sleep(POLL)
        if os.fstat(fd).st_nlink == 0:  # removed while this reader waited for it
            os.close(fd)
            return None
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return fd


def _unlock(fd: int) -> None:
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def rebuild_running(env: Mapping[str, str] | None = None) -> Rebuilding | None:
    """The rebuild that is running, or None.

    It tests the rebuild lock with a shared, non-waiting `flock` and lets it go at once:
    a refusal means a rebuild holds it, and the entry gives its start time; a grant means
    none is running, whatever the entry says. It never takes the writer lock.
    """
    import fcntl

    try:
        fd = os.open(rebuild_lock_path(env), os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            held = True
        else:
            held = False
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    if not held:
        return None
    entry = _read_entry(env)
    return Rebuilding(entry.started if entry else None, entry.pid if entry else None)


def _read_entry(env):
    """The rebuild entry, or None when the store or the row isn't there or the store is
    busy. Opens the store without creating it."""
    import sqlite3

    from moxi import store

    from . import state

    try:
        conn = store.connect(state.path(env), create=False, timeout_s=0.25)
    except (store.StoreError, sqlite3.Error):
        return None
    try:
        conn.execute("PRAGMA query_only = 1")
        return state.read_entry(conn)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# -- writers --------------------------------------------------------------------------

class Writer:
    """The writer lock's holder. `generation` is the current generation after the tidy
    on entry; `connect()` opens it to write."""

    def __init__(self, env: Mapping[str, str] | None):
        self.env = env
        self.generation: int = 0
        self.made_first = False

    # The writes.

    def connect(self, *, timeout: float = 5.0) -> sqlite3.Connection:
        """Open the current generation to write, its schema brought up to date."""
        return self._open(self.generation, create=False, timeout=timeout)

    def new_generation(self, *, timeout: float = 5.0) -> tuple[int, sqlite3.Connection]:
        """Make the next generation's file, empty but for its schema, and open it to
        write. The pointer still names the current one until `switch`."""
        n = max([self.generation, *generations(self.env)]) + 1
        self._make_lock(n)
        return n, self._open(n, create=True, timeout=timeout)

    def switch(self, n: int) -> list[int]:
        """Point `index.current` at generation `n` in one step, then remove each older
        generation no reader holds. Returns the generations removed."""
        from moxi import files

        if not db_path(n, self.env).is_file():
            raise FileNotFoundError(errno.ENOENT, "no such generation", str(db_path(n, self.env)))
        files.atomic_write(pointer_path(self.env), f"index-{n}.db\n", mode=0o600)
        self.generation = n
        return self.remove_old()

    def remove_old(self) -> list[int]:
        """Remove each generation other than the current one whose lock this child takes
        exclusively without waiting. Returns the ones removed; one a reader holds is left
        for the next child."""
        return [n for n in generations(self.env)
                if n != self.generation and _remove(n, self.env)]

    @contextmanager
    def rebuilding(self, *, wait: float = REBUILD_LOCK_WAIT) -> Iterator[float]:
        """Hold the rebuild lock for the block, with the rebuild entry written, and yield
        the rebuild's start time. The entry is cleared when the block ends; a crash
        leaves it, and the lock is freed with the process."""
        from moxi import files

        from . import state

        with files.lock(rebuild_lock_path(self.env), timeout_s=wait):
            started = time.time()
            conn = state.connect(self.env)
            try:
                state.write_entry(conn, started)
                try:
                    yield started
                finally:
                    state.clear_entry(conn)
            finally:
                conn.close()

    # The tidy on entry.

    def _tidy(self) -> None:
        self._clear_stale_entry()
        named = current(self.env)
        n = named if named is not None and db_path(named, self.env).is_file() else None
        if n is None:
            # No usable generation: whatever is on disk is left over. The new one takes
            # a number past every one seen, so no reader can mistake one for the other.
            seen = generations(self.env)
            for old in seen:
                _remove(old, self.env)
            first = max([named or 0, *seen, *generations(self.env)]) + 1
            self._make_lock(first)
            conn = self._open(first, create=True, timeout=5.0)
            conn.close()
            self.switch(first)
            self.made_first = True
            return
        self.generation = n
        for half_built in (m for m in generations(self.env) if m > n):
            _remove(half_built, self.env)
        self.remove_old()

    def _clear_stale_entry(self) -> None:
        import sqlite3

        from moxi import store

        from . import state

        if not state.path(self.env).is_file() or rebuild_running(self.env) is not None:
            return
        try:
            conn = store.connect(state.path(self.env), create=False)
        except (store.StoreError, sqlite3.Error):
            return
        try:
            entry = state.read_entry(conn) if _has_table(conn, "rebuild") else None
            if entry is not None:
                # The rebuild was killed: say so for the doctor. Its request was cleared
                # when it took it, so nothing runs it again. A request written while it
                # ran is left waiting, for this child to start when it frees the lock.
                with store.transaction(conn):
                    if _has_table(conn, "unfinished"):
                        state.write_unfinished(conn, entry.started)
                    state.clear_entry(conn)
        finally:
            conn.close()

    def _make_lock(self, n: int) -> None:
        """Create generation `n`'s lock file before its database, so a reader that finds
        the database always finds its lock."""
        from moxi import files

        with files.lock(lock_path(n, self.env), timeout_s=0):
            pass

    def _open(self, n: int, *, create: bool, timeout: float) -> sqlite3.Connection:
        from moxi import store

        conn = store.connect(db_path(n, self.env), create=create, timeout_s=timeout)
        try:
            store.migrate(conn, SCHEMA)
            if create and meta_get(conn, REDACT_VERSION_KEY) is None:
                from .rows import redact_version

                meta_set(conn, REDACT_VERSION_KEY, redact_version())
        except BaseException:
            conn.close()
            raise
        return conn


@contextmanager
def writer(env: Mapping[str, str] | None = None, *, wait: float = 0) -> Iterator[Writer]:
    """Hold the writer lock for the block and yield a `Writer`, after the tidy.

    `wait=0` tries once: a held lock raises `WriterBusy` at once, and the child exits.
    """
    from moxi import files

    held = files.lock(writer_lock_path(env), timeout_s=wait)
    try:
        held.__enter__()
    except files.LockTimeout as exc:
        raise WriterBusy(str(exc)) from None
    try:
        w = Writer(env)
        w._tidy()
        yield w
    finally:
        held.__exit__(None, None, None)


def _remove(n: int, env) -> bool:
    """Remove generation `n`'s files if its lock can be taken exclusively at once."""
    from moxi import files

    lock = lock_path(n, env)
    try:
        with files.lock(lock, timeout_s=0):
            db = db_path(n, env)
            for suffix in _DB_SUFFIXES:
                Path(f"{db}{suffix}").unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
    except files.LockTimeout:
        return False
    return True


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (name,)).fetchone() is not None


__all__ = ["INDEX_DIR", "POINTER", "WRITER_LOCK", "REBUILD_LOCK", "TOKENIZER", "SCHEMA",
           "IndexMissing",
           "IndexUnreadable", "StateUnreadable", "WriterBusy", "Rebuilding", "Reader",
           "Writer", "index_dir",
           "db_path", "lock_path", "pointer_path", "writer_lock_path", "rebuild_lock_path",
           "current", "generations", "meta_get", "meta_set", "stored_redact_version",
           "REDACT_VERSION_KEY", "open_read",
           "rebuild_running", "writer"]
