"""A module's SQLite stores: one way to open them, change them and move their schema forward.

- `connect` opens a store with the same settings everywhere: write-ahead logging on a
  store it creates, `synchronous=NORMAL`, a busy timeout, foreign keys on, and no implicit
  transactions. A store it creates is private to its owner from its first byte.
- `transaction` runs a block in one `BEGIN IMMEDIATE` transaction.
- `migrate` walks the store's `user_version` forward through a list of steps, each step
  and its version bump in one transaction, so two processes never run a step twice.
- `update` opens a store, runs one change in one transaction and closes it.

The caller owns a connection's lifetime: a daemon keeps one, a command opens and closes
one. `sqlite3` is imported only inside the functions, so importing this module is cheap.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    import sqlite3

T = TypeVar("T")

_BINARY = getattr(os, "O_BINARY", 0)


class StoreError(Exception):
    """A store can't be opened or changed as asked."""


class StoreTooNew(StoreError):
    """The store was written by a newer version of its module than the one running.

    Its schema version is past every step this version knows, so this version must not
    write to it. A command that stops on this exits `exit_code` (refused).
    """

    exit_code = 3

    def __init__(self, path: str, version: int, known: int):
        self.version = version
        self.known = known
        where = path or "the store"
        super().__init__(f"{where} is at schema version {version}, newer than the {known} "
                         "this version knows; upgrade the module that owns it")


def connect(path: str | os.PathLike, *, private: bool = True, create: bool = True,
            timeout: float = 5.0, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the SQLite store at `path` and return its connection.

    A missing file raises `StoreError` with `create=False`. With `create=True` its parent
    directory is made (only its owner may enter it when `private`), the file is created
    0600 before SQLite opens it, and the store is switched to write-ahead logging.

    An existing store is opened as it is: nothing is written to it. Its journal mode is
    read, and one warning is logged when it is not `wal`. An empty file (another process
    created it a moment ago and has not yet opened it) counts as one this call creates.

    Every connection then gets `synchronous=NORMAL`, a busy timeout of `timeout` seconds
    and foreign keys on, and runs with no implicit transactions (`isolation_level=None`):
    each statement commits by itself unless a `transaction` holds it.
    `check_same_thread` is passed to `sqlite3.connect` as given.

    `path` names a file: `":memory:"`, SQLite's name for a database held in memory, raises
    `StoreError`, since a store is a file.
    """
    import sqlite3

    path = Path(path)
    if str(path) == ":memory:":
        raise StoreError("':memory:' is SQLite's in-memory database, not a store file; "
                         "give the path of the store's file")
    fresh = False
    if not path.exists():
        if not create:
            raise StoreError(f"{path} does not exist")
        _make_parent(path.parent, private)
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600))
            fresh = True
        except FileExistsError:
            pass  # another process created it first; it is opened below like any other
    if not fresh:
        try:
            fresh = path.stat().st_size == 0
        except FileNotFoundError:
            raise StoreError(f"{path} was removed while it was being opened") from None
    conn = sqlite3.connect(path, timeout=timeout, isolation_level=None,
                           check_same_thread=check_same_thread)
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        if fresh:
            conn.execute("PRAGMA journal_mode = WAL")
        else:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() != "wal":
                import logging

                logging.getLogger("moxi.store").warning(
                    "%s uses journal mode %s, not wal; it is opened as it is", path, mode)
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
    except BaseException:
        conn.close()
        raise
    return conn


def _make_parent(directory: Path, private: bool) -> None:
    if private:
        from .files import private_dir

        private_dir(directory)
    else:
        directory.mkdir(parents=True, exist_ok=True)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run the block in one transaction that holds the store's write lock from its start.

    `BEGIN IMMEDIATE` on entry; `COMMIT` when the block ends, or `ROLLBACK` and the
    exception raised again when it raises. A `COMMIT` that fails (the store busy past the
    timeout) is rolled back too and its error raised, so the connection is left out of a
    transaction and the block's changes are not kept. The connection must be one `connect`
    opened (no implicit transactions) and must not be in a transaction already.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    if not conn.in_transaction:
        raise StoreError("the transaction was ended inside its block")
    try:
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def migrate(conn: sqlite3.Connection,
            steps: Sequence[str | Callable[[sqlite3.Connection], None]]) -> int:
    """Bring the store's schema up to `len(steps)` and return the version it ends at.

    The store's `PRAGMA user_version` counts the steps already run. Each step past it runs
    in its own `BEGIN IMMEDIATE` transaction, with the version read again under that lock
    and bumped before the commit, so two processes migrating one store run each step once.
    A step is SQL text (one or more statements, run one at a time inside the transaction)
    or a callable given the connection; a callable must not commit or roll back. A step
    that raises rolls back its own changes and leaves the store at the version before it.

    A store whose version is past `len(steps)` was written by a newer version of its
    module: `StoreTooNew`.
    """
    known = len(steps)
    while True:
        if _version(conn) == known:
            return known
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = _version(conn)
            if version > known:
                raise StoreTooNew(_db_path(conn), version, known)
            if version == known:
                conn.execute("COMMIT")
                return known
            step = steps[version]
            if callable(step):
                step(conn)
            else:
                for statement in _statements(step):
                    conn.execute(statement)
            if not conn.in_transaction:
                raise StoreError(f"migration step {version + 1} ended its own transaction")
            conn.execute(f"PRAGMA user_version = {version + 1}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _db_path(conn: sqlite3.Connection) -> str:
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return file or ""
    return ""


def _statements(script: str) -> Iterator[str]:
    """The statements of `script`, one at a time.

    `executescript` would commit the transaction first, so a step's text is split here
    with SQLite's own test for a complete statement, which knows about quoted text and
    trigger bodies. Text that holds no statement (blank, or only comments) is skipped.
    """
    import sqlite3

    pieces = script.split(";")
    buffer = ""
    for index, piece in enumerate(pieces):
        buffer += piece
        if index < len(pieces) - 1:
            buffer += ";"
        if sqlite3.complete_statement(buffer):
            if _has_sql(buffer):
                yield buffer
            buffer = ""
    if _has_sql(buffer):
        yield buffer + ";"


def _has_sql(text: str) -> bool:
    """Whether `text` holds anything but blanks, semicolons and comments."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n\f;":
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            return True
    return False


def update(path: str | os.PathLike, fn: Callable[[sqlite3.Connection], T],
           **connect_args) -> T:
    """Open the store at `path`, run `fn(conn)` in one transaction, close it, and return
    what `fn` returned. `connect_args` go to `connect`. If `fn` raises, nothing it did is
    kept."""
    conn = connect(path, **connect_args)
    try:
        with transaction(conn):
            return fn(conn)
    finally:
        conn.close()


__all__ = ["StoreError", "StoreTooNew", "connect", "transaction", "migrate", "update"]
