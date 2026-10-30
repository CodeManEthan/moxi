"""The one way comms opens comms.db: `open_write` for every writer, `open_read` for a
reader that must not write.

`open_write` is the gate in front of the store. Under `migrate.lock` beside the store it
looks at two facts, whether comms 0.2's files are still in place under their 0.2 names
and whether the store exists and carries the `migrated_from` marker, and acts on them:

    0.2 files   comms.db             open_write
    none        any or none          opens it, creating it on a fresh install
    present     none                 moves the 0.2 files in, then opens it
    present     with the marker      finishes an interrupted move, then opens it
    present     without the marker   refuses; nothing is written

The trigger is the marker, not the file: on an upgrade, provisioning and the install
listeners can reach the store before the daemon does, and each of them passes this gate.
A refusal names the file and the reason and writes nothing.

`open_read` writes nothing, takes no lock, and refuses (try again later) while comms
0.2's files wait to be moved, since the store beside them doesn't hold their data yet.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from moxi import exit as _exit
from moxi import files as _files
from moxi import store as _store

from .. import paths as _paths
from .schema import META_MIGRATED_FROM, STEPS

#: The lock file every writer's open takes, beside the store.
MIGRATE_LOCK = "migrate.lock"
#: How long an open waits for another process's open, in seconds.
LOCK_WAIT = 60.0

#: What `state_of` answers about the store's file.
ABSENT = "absent"
MARKED = "marked"
UNMARKED = "unmarked"

#: Whether a leftover `cursors/` folder counts as comms 0.2's. The adapters and the
#: daemon of this version still write cursor files there until the daemon keeps the
#: cursors in the store; until then the folder says nothing about a 0.2 install.
CURSORS_ARE_0_2 = False

# `moxi.files.lock` refuses a second holder in one process, so this process's threads
# queue for the gate here first.
_gate = threading.Lock()


class StoreRefused(_store.StoreError):
    """comms.db can't be opened as asked; nothing was written. `path` is the file the
    refusal is about, `reason` the line that says why."""

    exit_code = _exit.ERROR

    def __init__(self, path, reason: str):
        self.path = Path(path)
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class Unmoved(StoreRefused):
    """A read found comms 0.2's files not yet moved into comms.db. The daemon's next
    start moves them; a command that stops on this exits `exit_code` (try again later)."""

    exit_code = _exit.TEMPFAIL


def path(env=None) -> Path:
    """comms.db: STATE/comms/comms.db."""
    return _paths.comms_db(env)


def zero_two_files(env=None) -> list[Path]:
    """comms 0.2's files still under their 0.2 names, in the roots `env` names."""
    found = [p for p in (_paths.record_db(env), _paths.directory_db(env),
                         _paths.bindings_db(env)) if p.exists()]
    cursors = _paths.cursors_dir(env)
    if CURSORS_ARE_0_2 and cursors.is_dir():
        found.append(cursors)
    return found


def state_of(target) -> str:
    """`ABSENT`, `MARKED` (the store holds the move's marker) or `UNMARKED`. Reads only.
    A file that isn't a store raises `sqlite3.Error`."""
    target = Path(target)
    if not target.exists():
        return ABSENT
    conn = _store.connect(target, create=False)
    try:
        has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                                "AND name = 'meta'").fetchone()
        if not has_meta:
            return UNMARKED
        row = conn.execute("SELECT 1 FROM meta WHERE key = ?",
                           (META_MIGRATED_FROM,)).fetchone()
        return MARKED if row else UNMARKED
    finally:
        conn.close()


def open_write(target=None, *, env=None, timeout: float = 5.0,
               check_same_thread: bool = False) -> sqlite3.Connection:
    """Open comms.db for writing, through the gate, with its schema brought up to date.

    `target` is the store's file (default `path(env)`); `env` names the roots the 0.2
    files are looked for in. `timeout` is the connection's wait on another writer, in
    seconds. The connection is `moxi.store.connect`'s (no implicit transactions): write
    through `moxi.store.transaction`. Raises `StoreRefused` with nothing written,
    `moxi.files.LockTimeout` when another open holds the gate past `LOCK_WAIT`, and
    `moxi.store.StoreTooNew` for a store a newer comms wrote.
    """
    target = Path(target) if target is not None else path(env)
    with _gate, _files.lock(target.parent / MIGRATE_LOCK, timeout=LOCK_WAIT):
        found = zero_two_files(env)
        if found:
            try:
                state = state_of(target)
            except sqlite3.Error as e:
                raise StoreRefused(target, f"can't be read ({e}); nothing was written") \
                    from None
            if state == ABSENT:
                _move_0_2(found, target, env)
            elif state == MARKED:
                _finish_0_2(found, target, env)
            else:
                raise StoreRefused(target, (
                    "it exists beside comms 0.2's files "
                    f"({', '.join(str(p) for p in found)}) and wasn't made by moving "
                    "them; nothing was written"))
        conn = _store.connect(target, timeout=timeout, check_same_thread=check_same_thread)
        try:
            _store.migrate(conn, STEPS)
        except BaseException:
            conn.close()
            raise
        return conn


def open_read(target=None, *, env=None, timeout: float = 5.0,
              check_same_thread: bool = False) -> sqlite3.Connection:
    """Open comms.db read-only: an existing store, opened as it is, whose connection
    refuses every write (`query_only`). Takes no lock.

    Raises `Unmoved` while comms 0.2's files wait to be moved, and
    `sqlite3.OperationalError` for a store that is missing or can't be opened.
    """
    target = Path(target) if target is not None else path(env)
    found = zero_two_files(env)
    if found:
        try:
            state = state_of(target)
        except sqlite3.Error:
            state = UNMARKED
        if state != MARKED:
            raise Unmoved(target, "comms' 0.2 files aren't moved yet; they move at the "
                                  "daemon's next start")
    if not target.exists():
        raise sqlite3.OperationalError(f"unable to open database file: {target} does not "
                                       "exist")
    conn = _store.connect(target, create=False, timeout=timeout,
                          check_same_thread=check_same_thread)
    try:
        conn.execute("PRAGMA query_only = ON")
    except BaseException:
        conn.close()
        raise
    return conn


# -- the move of comms 0.2's files -------------------------------------------------------
# The two steps below are where the move plugs in. Each runs under the gate's lock, and
# each either leaves `target` a store holding the marker or raises `StoreRefused` with
# nothing written. Until the move is built, both refuse.

def _move_0_2(found: list[Path], target: Path, env) -> None:
    """Move comms 0.2's `found` files into a new store at `target`."""
    raise StoreRefused(found[0], (
        "comms 0.2's files are still here and this version of comms can't move them yet; "
        "nothing was written"))


def _finish_0_2(found: list[Path], target: Path, env) -> None:
    """Finish a move that stopped after its commit: rename the 0.2 files that are left,
    when they still match the fingerprint the marker keeps."""
    raise StoreRefused(found[0], (
        f"{target} was moved from comms 0.2, but these files are still under their 0.2 "
        "names and this version of comms can't finish the move; nothing was written"))


__all__ = ["MIGRATE_LOCK", "LOCK_WAIT", "ABSENT", "MARKED", "UNMARKED",
           "CURSORS_ARE_0_2", "StoreRefused", "Unmoved", "path", "zero_two_files",
           "state_of", "open_write", "open_read"]
