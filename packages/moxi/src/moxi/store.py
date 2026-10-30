"""A module's SQLite stores: one way to open them, change them and move their schema forward.

- `connect` opens a store with the same settings everywhere: write-ahead logging on a
  store it creates, `synchronous=NORMAL`, a busy timeout, foreign keys on, and no implicit
  transactions. A store it creates is private to its owner from its first byte, and so
  is each directory it creates on the way; a directory that is already there is left as
  it is.
- `transaction` runs a block in one `BEGIN IMMEDIATE` transaction.
- `migrate` walks the store's `user_version` forward through a list of steps, each step
  and its version bump in one transaction, so two processes never run a step twice.
- `update` opens a store, runs one change in one transaction and closes it.

The caller owns a connection's lifetime: a daemon keeps one, a command opens and closes
one. `sqlite3` is imported only inside the functions, so importing this module is cheap.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

TYPE_CHECKING = False
if TYPE_CHECKING:
    import sqlite3
    from typing import TypeVar

    T = TypeVar("T")

_BINARY = getattr(os, "O_BINARY", 0)
_WINDOWS = sys.platform == "win32"


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

    A missing file raises `StoreError` with `create=False`. With `create=True` each
    missing directory on the way to it is made, one level at a time, the file is created
    0600 before SQLite opens it (on Windows with an access list that grants only the
    current user), and the store is switched to write-ahead logging.

    `private` covers only what this call creates: with `private=True` each directory it
    makes is 0700 (on Windows given the owner-only access list), and with `private=False`
    it gets the default mode. A directory that already exists is never changed, its mode,
    owner and access list kept, and a symbolic link on the way is followed as any open
    follows it. So a store placed in a shared or linked directory the user chose opens
    there, private as a file, and the directory stays as the user set it.

    An existing store is opened as it is: nothing is written to it. Its journal mode is
    read, and one warning is logged when it is not `wal`. With `create=True`, an empty
    file (another process created it a moment ago and has not yet opened it) counts as
    one this call creates; with `create=False` it is opened as it is, like any other, so
    a call that may not create writes nothing.

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
        _make_parents(path.parent, private)
        try:
            _create_file(path)
            fresh = True
        except FileExistsError:
            pass  # another process created it first; it is opened below like any other
    if not fresh and create:
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


def _make_parents(directory: Path, private: bool) -> None:
    """Make each missing directory down to `directory`, outermost first. One that exists
    (a link to a directory counts) is left as it is."""
    missing = []
    while not os.path.isdir(directory):
        missing.append(directory)
        if directory.parent == directory:
            break
        directory = directory.parent
    for each in reversed(missing):
        _make_dir(each, private)


def _make_dir(directory: Path, private: bool) -> None:
    """Make one directory: 0700 (the owner-only access list on Windows) when `private`,
    else the default mode. One another process made a moment ago is left as it is."""
    if private and _WINDOWS:
        from . import _win

        _win.make_private_dir(directory)
        return
    try:
        os.mkdir(directory, 0o700 if private else 0o777)
    except FileExistsError:
        pass  # what is there is not this call's to change; the file's open says if it fits


def _create_file(path: Path) -> None:
    """Create the empty file `path`, readable and writable only by its owner. Raises
    `FileExistsError` when something is there already."""
    if _WINDOWS:
        from . import _win

        _win.private_file(path)
        return
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600))


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
