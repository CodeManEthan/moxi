"""Park entries kept true to slots: the park lock and reconcile.

**The park lock** is `park.lock` in the launcher's state directory. Its file names the
holder: the pid on the first line (as `moxi.files.lock` writes it), the holder's kind on
the second, and `done` on a third once the holder's reconcile and crash discovery are
done. `park`, `restore`, `unpark` and a plain resume of a parked session (`resume`) hold
it for their whole run; a launch's own crash discovery (`discovery`) holds it only around
discovery. A second whole-run holder waits `LOCK_WAIT_S`, then is refused `park or restore
is running`. A launch that meets a whole-run holder whose `done` mark is written proceeds
without discovery, since that holder ran it; before the mark, and behind another launch's
discovery, it waits the same `LOCK_WAIT_S` and is then refused, never proceeding
unchecked.

**Reconcile** keeps the rule that an open entry always has its reservation, or is closed.
An open entry whose slot does not carry its holder's hold takes it again, with `expect`
set to the entry's launch; a refusal closes the entry `dropped` and names the session's
`moxi launcher resume` command. A `park-*` hold with no open entry behind it is released.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moxi import files
from moxi.paths import Layout

from . import catalog, store

#: The park lock's file, in the launcher's state directory.
LOCK_FILE = "park.lock"
#: Who may hold the park lock: the verbs that hold it for their whole run, then a
#: launch's crash discovery.
LOCK_KINDS = ("park", "restore", "unpark", "resume", "discovery")
#: The kinds that hold the lock for their whole run, reconcile and discovery first.
WHOLE_KINDS = ("park", "restore", "unpark", "resume")
#: How long a holder waits for the park lock before it is refused, in seconds.
LOCK_WAIT_S = 10.0
#: The mark a whole-run holder writes once its reconcile and discovery are done.
DONE = "done"
#: How often a launch's discovery looks at the lock again while it waits, in seconds.
POLL_S = 0.2

# The kind this process holds the park lock as, or None. A launch inside a process that
# holds it (restore's own resumes, a parked session's resume) runs no discovery of its
# own: the holder ran it.
_mine: str | None = None
_mine_guard = threading.Lock()


class ParkRunning(catalog.Refused):
    """The park lock stayed held past the wait."""

    code = "park-running"

    def __init__(self, holder: LockHolder | None):
        who = holder.words() if holder is not None else "another process"
        super().__init__(None, "park or restore is running",
                         f"it is held by {who}; try again once it is done")
        self.holder = holder


@dataclass(frozen=True)
class LockHolder:
    """What the park lock's file says of its holder."""

    pid: int | None
    kind: str | None
    done: bool

    def words(self) -> str:
        kind = self.kind or "a holder"
        return f"{kind} (pid {self.pid})" if self.pid else kind


def lock_path(env: Mapping[str, str] | None = None) -> Path:
    return Layout("launcher").state_dir(env) / LOCK_FILE


def read_holder(path: Path) -> LockHolder | None:
    """The holder the lock file names, or None for an empty or unreadable file."""
    try:
        lines = path.read_text(encoding="ascii", errors="replace").splitlines()
    except OSError:
        return None
    if not lines or not lines[0].strip():
        return None
    pid = int(lines[0]) if lines[0].strip().isdigit() else None
    kind = lines[1].strip() if len(lines) > 1 and lines[1].strip() in LOCK_KINDS else None
    return LockHolder(pid, kind, len(lines) > 2 and lines[2].strip() == DONE)


def _write_holder(path: Path, kind: str, *, done: bool = False) -> None:
    text = f"{os.getpid()}\n{kind}\n" + (f"{DONE}\n" if done else "")
    with open(path, "r+", encoding="ascii") as fh:
        fh.write(text)
        fh.truncate()


def held_here() -> str | None:
    """The kind this process holds the park lock as, or None."""
    return _mine


@contextmanager
def lock(kind: str, *, env: Mapping[str, str] | None = None,
         wait_s: float = LOCK_WAIT_S) -> Iterator[Path]:
    """Hold the park lock as `kind` for the block, its file naming the holder. Waits
    `wait_s` for it, then raises `ParkRunning`. Yields the lock file's path, for
    `mark_done`."""
    global _mine
    if kind not in LOCK_KINDS:
        raise ValueError(f"not a park lock holder: {kind!r}")
    path = lock_path(env)
    with ExitStack() as stack:
        try:
            stack.enter_context(files.lock(path, timeout_s=wait_s))
        except files.LockTimeout as exc:
            raise ParkRunning(read_holder(path)) from exc
        _write_holder(path, kind)
        with _mine_guard:
            _mine = kind
        try:
            yield path
        finally:
            with _mine_guard:
                _mine = None


def mark_done(path: Path) -> None:
    """Write the mark that this whole-run holder's reconcile and discovery are done."""
    if _mine not in WHOLE_KINDS:
        raise RuntimeError("only a whole-run holder of the park lock marks it done")
    _write_holder(path, _mine, done=True)


@contextmanager
def discovery(*, env: Mapping[str, str] | None = None,
              wait_s: float = LOCK_WAIT_S) -> Iterator[bool]:
    """The park lock as a launch's crash discovery takes it. Yields True with the lock
    held, for the launch to run discovery; or False, holding nothing, when a whole-run
    holder that has done its discovery holds it (in this process or another). Waits up
    to `wait_s` for either, then raises `ParkRunning`."""
    if held_here() in WHOLE_KINDS:
        yield False
        return
    path = lock_path(env)
    deadline = time.monotonic() + wait_s
    with ExitStack() as stack:
        taken = False
        while True:
            try:
                stack.enter_context(lock("discovery", env=env, wait_s=0))
                taken = True
                break
            except ParkRunning as exc:
                found = exc.holder or read_holder(path)
            except RuntimeError:
                found = None  # another thread of this process runs its discovery
            if found is not None and found.kind in WHOLE_KINDS and found.done:
                break
            if time.monotonic() >= deadline:
                raise ParkRunning(found)
            time.sleep(POLL_S)
        yield taken


# -- reconcile -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    """One thing reconcile did or could not do: `retaken` (a lost reservation taken
    again), `dropped` (the entry closed; `resume` is the command that brings the session
    back), `released` (a park hold with no entry) or `unread` (slots could not say)."""

    what: str
    slot: int | None
    why: str
    session_id: str | None = None
    resume: str | None = None

    def line(self) -> str:
        where = f"slot {self.slot}" if self.slot is not None else "slots"
        text = f"{where}: {self.what}: {self.why}"
        return f"{text}; resume it with {self.resume}" if self.resume else text


def resume_command(session_id: str) -> str:
    return f"moxi launcher resume {session_id}"


def _holder_of(slot: Any) -> str | None:
    hold = getattr(slot, "hold", None)
    return hold.holder if hold is not None else None


def reconcile(conn: sqlite3.Connection, api: Any) -> list[Note]:
    """Bring the open entries and slots' `park-*` holds into line, under the park lock.
    Nothing to do without slots."""
    if api is None:
        return []
    listing = api.list_slots()
    if not listing.ok:
        return [Note("unread", None, f"slots can't be read: {listing.why}")]
    by_number = {slot.number: slot for slot in listing.slots}
    notes: list[Note] = []
    entries = store.open_entries(conn)
    for entry in entries:
        slot = by_number.get(entry.slot)
        if slot is not None and _holder_of(slot) == entry.holder:
            continue
        try:
            api.hold(entry.slot, entry.holder, None, expect=entry.request_id)
        except api.Refused as exc:
            why = f"its reservation is gone and can't be taken again: {exc.words}"
            store.close_entry(conn, entry.entry_id, "dropped", detail=why)
            notes.append(Note("dropped", entry.slot, why, entry.session_id,
                              resume_command(entry.session_id)))
            continue
        except (api.TryAgain, api.TmuxError, api.RegistryError) as exc:
            notes.append(Note("unread", entry.slot, f"its reservation can't be checked: "
                                                     f"{exc}", entry.session_id))
            continue
        notes.append(Note("retaken", entry.slot, f"its reservation {entry.holder} was "
                                                  "taken again", entry.session_id))
    kept = {(entry.slot, entry.holder) for entry in entries}
    for slot in listing.slots:
        holder = _holder_of(slot)
        if holder is None or not holder.startswith(store.HOLDER_PREFIX):
            continue
        if (slot.number, holder) in kept:
            continue
        try:
            if api.release(slot.number, holder):
                notes.append(Note("released", slot.number,
                                  f"the hold {holder} had no park entry"))
        except (api.TryAgain, api.TmuxError, api.RegistryError) as exc:
            notes.append(Note("unread", slot.number, f"the hold {holder} has no park "
                                                      f"entry and can't be released: {exc}"))
    return notes


__all__ = ["DONE", "LOCK_FILE", "LOCK_KINDS", "LOCK_WAIT_S", "WHOLE_KINDS", "LockHolder",
           "Note", "ParkRunning", "discovery", "held_here", "lock", "lock_path", "mark_done",
           "read_holder", "reconcile", "resume_command"]
