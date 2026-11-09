"""services' store: the user's rows, written and adopted, in one SQLite file.

The store is `services.db` in services' data directory. Module rows are never stored:
they come from the modules' declarations.

Reads never write: they never create the store, never migrate it and never change a byte
of it. A missing store is an empty list. A store this code can't read raises
`Unreadable`; one written by a newer services raises `TooNew` (exit 3); one locked past
the wait raises `Busy` (exit 75).

Every write goes through `writing()`, one sequence in this order:

1. `moxi.store.connect(path)`, which creates the file private to its owner and in WAL
   mode on the first write;
2. `moxi.store.migrate(conn, STEPS)`, each step with its version bump in its own
   transaction, before and outside the write's transaction;
3. one `moxi.store.transaction(conn)` holding the write, which reads the schema version
   again under its lock and writes nothing when another release migrated the store past
   this code in between;
4. close.

The row writes below run inside that transaction, given its connection. A caller that
also acts on the supervisor takes the core lock before the write and holds no
transaction across a supervisor call: each write here is one short transaction.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

import moxi.paths
import moxi.store

from . import rows as _rows
from .errors import Busy, Refused, TooNew, Unreadable
from .rows import COLUMNS, MODULE, UserRow

T = TypeVar("T")

#: The store's file name in services' data directory.
FILE = "services.db"

#: The schema, one step per version: step n takes the store from version n-1 to n.
STEPS: tuple[str, ...] = (
    """
    CREATE TABLE services (
        name      TEXT PRIMARY KEY,
        form      TEXT NOT NULL CHECK (form IN ('written', 'adopted')),
        kind      TEXT NOT NULL CHECK (kind IN ('daemon', 'timer')),
        argv      TEXT,
        cwd       TEXT,
        unit      TEXT,
        every     TEXT,
        at        TEXT,
        days      TEXT,
        enabled   INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
        removing  INTEGER NOT NULL DEFAULT 0 CHECK (removing IN (0, 1)),
        stopped   TEXT,
        health    TEXT,
        project   TEXT,
        launches_session INTEGER NOT NULL DEFAULT 0 CHECK (launches_session IN (0, 1)),
        platform  TEXT NOT NULL CHECK (platform IN ('linux', 'darwin', 'win32')),
        doc       TEXT NOT NULL DEFAULT '',
        created   TEXT NOT NULL,
        changed   TEXT NOT NULL,
        changed_by TEXT NOT NULL,
        CHECK ((form = 'written') = (argv IS NOT NULL)),
        CHECK ((form = 'adopted') = (unit IS NOT NULL)),
        CHECK ((form = 'written' AND kind = 'timer') = (every IS NOT NULL OR at IS NOT NULL)),
        CHECK (every IS NULL OR at IS NULL),
        CHECK (days IS NULL OR at IS NOT NULL)
    );
    """,
)

#: The schema version this code writes.
VERSION = len(STEPS)
#: The schema versions this code reads as they are.
READS = frozenset({1})
#: How long a read or a write waits for another writer's lock, in seconds, before `Busy`.
TIMEOUT = 5.0

_SELECT = f"SELECT {', '.join(COLUMNS)} FROM services"
_INSTALL = "run `moxi install services`, which migrates the store"


def path() -> str:
    """The store's file."""
    return os.path.join(str(moxi.paths.Layout(MODULE).data_dir()), FILE)


def now() -> str:
    """The time now, UTC, ISO 8601 with `Z`."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- errors -----------------------------------------------------------------------------

def _is_busy(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (5, 6)  # SQLITE_BUSY, SQLITE_LOCKED
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def unreadable(where: str, exc: BaseException) -> Unreadable:
    """The error for a store at `where` that failed with `exc`."""
    if isinstance(exc, sqlite3.Error) and _is_busy(exc):
        return Busy(path=where)
    if isinstance(exc, PermissionError) or "permission" in str(exc).lower():
        return Unreadable(f"the services store {where} can't be read ({exc})",
                          "give your user read and write access to it", where)
    return Unreadable(f"the services store {where} can't be read ({exc})",
                      "move the file aside and add the services again, or restore it "
                      "from a backup", where)


# --- reading ----------------------------------------------------------------------------

def read_rows(where: str | None = None, *, strict: bool = False) -> list[UserRow]:
    """Every user row, by name. Never writes.

    A missing store has no rows. So does one a first write has created and not yet set
    up (an empty file, or schema version 0), unless `strict`: then that store raises
    `Unreadable`, so that only a missing file reads as no rows at all. Raises
    `Unreadable`, `TooNew` or `Busy` when the store can't be read."""
    where = where or path()
    try:
        empty = os.path.getsize(where) == 0
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise unreadable(where, exc) from exc
    if empty:
        # Created a moment ago by a write that hasn't set it up yet. `connect` would
        # switch an empty file to WAL, which is a write.
        return _not_set_up(where, strict)
    try:
        conn = moxi.store.connect(where, create=False, timeout_s=TIMEOUT)
    except moxi.store.StoreError as exc:
        if not os.path.exists(where):
            return []
        raise unreadable(where, exc) from exc
    except (sqlite3.Error, OSError) as exc:
        raise unreadable(where, exc) from exc
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            return _not_set_up(where, strict)
        if version > VERSION:
            raise TooNew(where, version, VERSION)
        if version not in READS:
            raise Unreadable(f"the services store {where} is at schema version {version}, "
                             "older than this version of moxi-services reads", _INSTALL,
                             where)
        return [_rows.from_sql(row)
                for row in conn.execute(f"{_SELECT} ORDER BY name").fetchall()]
    except Unreadable:
        raise
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise unreadable(where, exc) from exc
    finally:
        conn.close()


def _not_set_up(where: str, strict: bool) -> list[UserRow]:
    if strict:
        raise Unreadable(f"the services store {where} is not set up yet: a write made it "
                         "and has not finished", "try again in a moment; if it stays, "
                         f"{_INSTALL}", where)
    return []


def get(name: str, where: str | None = None) -> UserRow | None:
    """The user row named `name`, or None."""
    for row in read_rows(where):
        if row.name == name:
            return row
    return None


def of_project(key: str, where: str | None = None) -> list[UserRow]:
    """The user rows whose project is `key`, ignoring case, by name."""
    wanted = key.casefold()
    return [row for row in read_rows(where)
            if row.project is not None and row.project.casefold() == wanted]


# --- writing ----------------------------------------------------------------------------

def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


@contextmanager
def writing(where: str | None = None) -> Iterator[sqlite3.Connection]:
    """The write sequence around a block: connect, migrate, one transaction, close.

    The block gets the connection inside the transaction; what it raises rolls the
    transaction back and is raised again (a SQLite error as `Busy` or `Unreadable`, except
    an `sqlite3.IntegrityError`, a store check the write broke, which is raised as it is)."""
    where = where or path()
    try:
        conn = moxi.store.connect(where, timeout_s=TIMEOUT)
    except (moxi.store.StoreError, sqlite3.Error, OSError) as exc:
        raise unreadable(where, exc) from exc
    try:
        try:
            moxi.store.migrate(conn, STEPS)
        except moxi.store.StoreTooNew as exc:
            raise TooNew(where, exc.version, exc.known) from None
        with moxi.store.transaction(conn):
            version = _version(conn)
            if version != VERSION:
                # Another release migrated it between this code's migration and its lock.
                raise TooNew(where, version, VERSION)
            yield conn
    except (Unreadable, sqlite3.IntegrityError):
        # A broken store check is a bug in the write, not a store that can't be read.
        raise
    except (sqlite3.Error, OSError) as exc:
        raise unreadable(where, exc) from exc
    finally:
        conn.close()


def write(fn: Callable[[sqlite3.Connection], T], where: str | None = None) -> T:
    """Run `fn(conn)` as one write (see `writing`) and return what it returned."""
    with writing(where) as conn:
        return fn(conn)


def migrate_existing(where: str | None = None) -> bool:
    """Migrate an existing store to this code's schema; make none. True when there was
    a store to migrate. `SETUP` calls it: the first write makes a missing store.
    Raises `Unreadable`, `TooNew` or `Busy`."""
    where = where or path()
    try:
        if os.path.getsize(where) == 0:
            return False  # made a moment ago by a write that will migrate it
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise unreadable(where, exc) from exc
    try:
        conn = moxi.store.connect(where, create=False, timeout_s=TIMEOUT)
    except (moxi.store.StoreError, sqlite3.Error, OSError) as exc:
        raise unreadable(where, exc) from exc
    try:
        moxi.store.migrate(conn, STEPS)
    except moxi.store.StoreTooNew as exc:
        raise TooNew(where, exc.version, exc.known) from None
    except (sqlite3.Error, OSError) as exc:
        raise unreadable(where, exc) from exc
    finally:
        conn.close()
    return True


def fetch(conn: sqlite3.Connection, name: str) -> UserRow | None:
    """The row named `name` as this transaction sees it, or None."""
    row = conn.execute(f"{_SELECT} WHERE name = ?", (name,)).fetchone()
    return None if row is None else _rows.from_sql(row)


def _json(value) -> str | None:
    return None if value is None else json.dumps(list(value), ensure_ascii=False)


def _suggest(name: str, project: str | None) -> str:
    if project:
        stem = re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-")
        candidate = f"{stem}-{name}"
        if stem and _rows.is_name(candidate) and not name.startswith(stem + "-"):
            return f"try {candidate}"
    return "pick another name"


def insert(conn: sqlite3.Connection, *, name: str, form: str, kind: str, by: str,
           argv=None, cwd: str | None = None, unit: str | None = None,
           every: str | None = None, at=None, days=None, enabled: bool = False,
           health=None, project: str | None = None, launches_session: bool = False,
           platform: str | None = None, doc: str = "") -> UserRow:
    """Insert a user row and return it. `by` is who wrote it (`moxi services`, the page).

    Raises `Usage` (exit 2) for fields out of shape (see `moxi_services.rows`) and
    `Refused` (exit 3) when the name is taken: names are unique across projects."""
    platform = platform or sys.platform
    fields: dict[str, object] = dict(
        name=name, form=form, kind=kind, argv=argv, cwd=cwd, unit=unit, every=every,
        at=at, days=days, health=health, project=project, platform=platform, doc=doc)
    _rows.check_fields(fields)
    taken = fetch(conn, name)
    if taken is not None:
        where = f" (project {taken.project})" if taken.project else ""
        raise Refused(f"{name} is taken{where}; names are unique across projects",
                      _suggest(name, project))
    stamp = now()
    conn.execute(
        f"INSERT INTO services ({', '.join(COLUMNS)}) VALUES "
        f"({', '.join('?' for _ in COLUMNS)})",
        (name, form, kind, _json(argv), cwd, unit, every, _json(at), _json(days),
         int(bool(enabled)), 0, None, _json(health), project, int(bool(launches_session)),
         platform, doc, stamp, stamp, by))
    return fetch(conn, name)


def _set(conn: sqlite3.Connection, name: str, by: str, column: str, value) -> UserRow:
    cursor = conn.execute(
        f"UPDATE services SET {column} = ?, changed = ?, changed_by = ? WHERE name = ?",
        (value, now(), by, name))
    if cursor.rowcount == 0:
        raise Refused(f"no service is named {name!r}",
                      "`moxi services list` shows every row's id")
    return fetch(conn, name)


def set_enabled(conn: sqlite3.Connection, name: str, enabled: bool, *, by: str) -> UserRow:
    """Set a row's flag. Raises `Refused` when there is no such row."""
    return _set(conn, name, by, "enabled", int(bool(enabled)))


def set_removing(conn: sqlite3.Connection, name: str, removing: bool = True, *,
                 by: str) -> UserRow:
    """Mark a row as being removed (its written unit is then no longer declared), or
    clear the mark. Raises `Refused` when there is no such row."""
    return _set(conn, name, by, "removing", int(bool(removing)))


def set_stopped(conn: sqlite3.Connection, name: str, when: str | None, *,
                by: str) -> UserRow:
    """Record a stop by hand at `when` (UTC, as `now()` gives it), or clear it with
    None. Raises `Refused` when there is no such row."""
    return _set(conn, name, by, "stopped", when)


def delete(conn: sqlite3.Connection, name: str) -> bool:
    """Delete the row named `name`; True when there was one."""
    return conn.execute("DELETE FROM services WHERE name = ?", (name,)).rowcount > 0


__all__ = [
    "FILE", "STEPS", "VERSION", "READS", "TIMEOUT", "path", "now", "unreadable",
    "read_rows", "get", "of_project", "writing", "write", "migrate_existing", "fetch",
    "insert", "set_enabled", "set_removing", "set_stopped", "delete"]
