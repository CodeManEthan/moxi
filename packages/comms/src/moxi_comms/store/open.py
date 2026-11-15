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
Asked to check the step, it also refuses a store whose schema is at another step than
this comms knows: an older one the daemon hasn't brought up to date, which a read must
not migrate, or a newer one a newer comms wrote.
"""

from __future__ import annotations

import json
import logging
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

log = logging.getLogger("comms.store")

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


class StepMismatch(StoreRefused):
    """A read found the store at another schema step than this comms knows: `version`
    is the store's `user_version`, `known` the number of steps (`len(STEPS)`). An older
    store waits for the next writer's open to migrate it, so a command that stops on it
    exits TEMPFAIL (try again later); a newer one was written by a newer comms, and a
    command exits REFUSED, as on `moxi.store.StoreTooNew`."""

    def __init__(self, path, version: int, known: int):
        self.version = version
        self.known = known
        self.exit_code = _exit.TEMPFAIL if version < known else _exit.REFUSED
        if version < known:
            reason = (f"its schema is at step {version} of {known}; comms' next write "
                      "brings it up to date")
        else:
            reason = (f"its schema is at step {version}, past the {known} this comms "
                      "knows; a newer comms wrote it")
        super().__init__(path, reason)


def path(env=None) -> Path:
    """comms.db: STATE/comms/comms.db."""
    return _paths.comms_db(env)


def zero_two_files(env=None, target=None) -> list[Path]:
    """comms 0.2's files still under their 0.2 names, in the roots `env` names.

    The `cursors/` folder counts unless the store (`target`, default `path(env)`) is
    already beside it and its move didn't fingerprint the folder. Then an earlier build
    of this version, whose adapters still wrote cursor files after the store was made,
    left it there; it says nothing about a 0.2 install, and the gate leaves it where it
    is."""
    found = [p for p in (_paths.record_db(env), _paths.directory_db(env),
                         _paths.bindings_db(env)) if p.exists()]
    cursors = _paths.cursors_dir(env)
    if cursors.is_dir() and _cursors_are_0_2(
            Path(target) if target is not None else path(env)):
        found.append(cursors)
    return found


def _cursors_are_0_2(target: Path) -> bool:
    """Whether a `cursors/` folder beside `target` is comms 0.2's: no store yet, or the
    store's move fingerprinted the folder (a move stopped before its renames). A store
    that can't be read answers yes, so the gate's own refusal names it."""
    if not target.exists():
        return True
    from .upgrade_0_2 import META_MIGRATED_FILES
    try:
        conn = _store.connect(target, create=False)
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND "
                                "name = 'meta'").fetchone():
                return False
            row = conn.execute("SELECT value FROM meta WHERE key = ?",
                               (META_MIGRATED_FILES,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return True
    try:
        return row is not None and "cursors" in json.loads(row[0])
    except (TypeError, ValueError):
        return True


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


def _log_report(lines: list[str]) -> None:
    for line in lines:
        log.info("%s", line)


def open_write(target=None, *, env=None, timeout: float = 5.0,
               check_same_thread: bool = False, report=None) -> sqlite3.Connection:
    """Open comms.db for writing, through the gate, with its schema brought up to date.

    `target` is the store's file (default `path(env)`); `env` names the roots the 0.2
    files are looked for in. `timeout` is the connection's wait on another writer, in
    seconds. The connection is `moxi.store.connect`'s (no implicit transactions): write
    through `moxi.store.transaction`. Raises `StoreRefused` with nothing written,
    `moxi.files.LockTimeout` when another open holds the gate past `LOCK_WAIT`, and
    `moxi.store.StoreTooNew` for a store a newer comms wrote.

    When the open moves comms 0.2's files, or finishes a move, `report` is called once
    with the report's lines (default: each is logged).
    """
    target = Path(target) if target is not None else path(env)
    report = report if report is not None else _log_report
    # The lock makes each missing directory down to the store's private to its owner,
    # and leaves one already there (a shared directory, or a link) as it is.
    with _gate, _files.lock(target.parent / MIGRATE_LOCK, timeout_s=LOCK_WAIT):
        found = zero_two_files(env, target)
        if found:
            try:
                state = state_of(target)
            except sqlite3.Error as exc:
                raise StoreRefused(target, f"can't be read ({exc}); nothing was written") \
                    from None
            if state == ABSENT:
                report(_move_0_2(found, target, env))
            elif state == MARKED:
                report(_finish_0_2(found, target, env))
            else:
                raise StoreRefused(target, (
                    "it exists beside comms 0.2's files "
                    f"({', '.join(str(p) for p in found)}) and wasn't made by moving "
                    "them; nothing was written"))
        conn = _store.connect(target, timeout_s=timeout, check_same_thread=check_same_thread)
        try:
            _store.migrate(conn, STEPS)
        except BaseException:
            conn.close()
            raise
        return conn


def open_read(target=None, *, env=None, timeout: float = 5.0,
              check_same_thread: bool = False,
              check_step: bool = False) -> sqlite3.Connection:
    """Open comms.db read-only: an existing store, opened as it is, whose connection
    refuses every write (`query_only`) and never checkpoints the WAL as it closes, so a
    read that is the last connection on the store leaves `comms.db` and its `-wal` as a
    writer left them. Takes no lock.

    Raises `Unmoved` while comms 0.2's files wait to be moved, and
    `sqlite3.OperationalError` for a store that is missing or can't be opened. With
    `check_step`, the store's `user_version` is read once, before the connection is
    handed back, and a store at another step than `len(STEPS)` raises `StepMismatch`.
    """
    target = Path(target) if target is not None else path(env)
    found = zero_two_files(env, target)
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
    conn = _store.connect(target, create=False, timeout_s=timeout,
                          check_same_thread=check_same_thread)
    try:
        conn.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        conn.execute("PRAGMA query_only = ON")
        if check_step:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version != len(STEPS):
                raise StepMismatch(target, version, len(STEPS))
    except BaseException:
        conn.close()
        raise
    return conn


# -- the move of comms 0.2's files -------------------------------------------------------
# Each runs under the gate's lock, and each either leaves `target` a store holding the
# marker or raises `StoreRefused` with nothing written. `upgrade_0_2` holds the move.

def _move_0_2(found: list[Path], target: Path, env) -> list[str]:
    """Move comms 0.2's files into a new store at `target`; the report's lines."""
    from . import upgrade_0_2

    return upgrade_0_2.move(target, env).lines(target)


def _finish_0_2(found: list[Path], target: Path, env) -> list[str]:
    """Finish a move that stopped after its commit: rename the 0.2 files that are left,
    when they still match the fingerprint the marker keeps; the report's lines."""
    from . import upgrade_0_2

    done = upgrade_0_2.finish(target, env)
    return [f"finished the move of comms 0.2's files into {target}"] + [
        f"renamed {a} to {b}" for a, b in done]


__all__ = ["MIGRATE_LOCK", "LOCK_WAIT", "ABSENT", "MARKED", "UNMARKED",
           "StoreRefused", "Unmoved", "StepMismatch", "path", "zero_two_files",
           "state_of", "open_write", "open_read"]
