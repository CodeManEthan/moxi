"""Park entries kept true to slots: the park lock, reconcile and crash discovery.

**The park lock** is `park.lock` in the launcher's state directory, taken with
`moxi.files.lock`, which writes the holder's pid there. Beside it, `park.holder` names
the holder: its pid on the first line, its kind on the second, and `done` on a third once
the holder's reconcile and crash discovery are done. It counts only while its pid is the
lock file's. `park`, `restore`, `unpark` and a plain resume of a parked session (`resume`) hold
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

**Crash discovery** turns crashed slots (slots' `Slot.crashed`) into park entries before
the launcher places a session in a slot, and at the start of each whole-run holder. A
crashed slot with no open entry is a candidate when its last launch is a launcher slot
launch, not failed, with a session id, whose session is not live and that no later launch
holds (or whose liveness can't be read). A candidate is reserved (an open-ended hold
`park-<id>`, `expect` set to its launch), given a `crash` entry, then settled, since the
reservation now keeps it. A crashed slot with nothing to restore is settled with the
reason; one another holder holds is left crashed, with no entry, for a later discovery.
Discovery writes only entries, reservations and settles: it never starts a session.
`discover(..., write=False)` is its read-only form, for dry runs and the page.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moxi import exit as codes
from moxi import files
from moxi.paths import Layout

from . import catalog, first, slots, store

log = logging.getLogger("moxi.launcher.parks")

#: The park lock's file, in the launcher's state directory.
LOCK_FILE = "park.lock"
#: The file beside it that names the holder's kind and whether it is done.
HOLDER_FILE = "park.holder"
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
#: The default `turns`: turns' API looked up when discovery runs.
LOOK = object()

# The kind this process holds the park lock as, or None. A launch inside a process that
# holds it (restore's own resumes, a parked session's resume) runs no discovery of its
# own: the holder ran it.
_mine: str | None = None
_mine_guard = threading.Lock()


class ParkRunning(catalog.Refused):
    """The park lock stayed held past the wait."""

    code = "park-running"
    exit_code = codes.TEMPFAIL

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


def _holder_path(path: Path) -> Path:
    return path.with_name(HOLDER_FILE)


def _lock_pid(path: Path) -> int | None:
    try:
        with open(path, "rb") as fh:
            first_line = fh.readline(32).strip()
    except OSError:
        return None
    return int(first_line) if first_line.isdigit() else None


def read_holder(path: Path) -> LockHolder | None:
    """The holder of lock file `path`: its pid, and the kind and done mark its holder
    file gives when that file names the same pid. None when the lock file names none."""
    pid = _lock_pid(path)
    if pid is None:
        return None
    try:
        lines = _holder_path(path).read_text(encoding="ascii", errors="replace").splitlines()
    except OSError:
        lines = []
    if not lines or lines[0].strip() != str(pid):
        return LockHolder(pid, None, done=False)
    kind = lines[1].strip() if len(lines) > 1 and lines[1].strip() in LOCK_KINDS else None
    return LockHolder(pid, kind, done=len(lines) > 2 and lines[2].strip() == DONE)


def _write_holder(path: Path, kind: str, *, done: bool = False) -> None:
    text = f"{os.getpid()}\n{kind}\n" + (f"{DONE}\n" if done else "")
    files.atomic_write(_holder_path(path), text)


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


# -- crash discovery -----------------------------------------------------------------------

#: What discovery does with a crashed slot: `reserve` it for its session, `settle` it
#: (nothing to restore), or leave it (`held` by another holder; `failed` when slots
#: refused or could not answer).
ACTIONS = ("reserve", "settle", "held", "failed")


@dataclass(frozen=True)
class Found:
    """One crashed slot discovery met, what it does (or, read-only, would do) with it, and
    why. `entry` is the crash entry written for a reserved slot."""

    slot: int
    action: str
    why: str
    request_id: str | None = None
    session_id: str | None = None
    harness: str | None = None
    unread: str | None = None
    entry: store.ParkEntry | None = None

    def line(self) -> str:
        who = f"session {self.session_id}" if self.session_id else "no session"
        return f"slot {self.slot}: crashed, {who}: {self.action}: {self.why}"


def _session_of(conn: sqlite3.Connection, slot: Any,
                turns: Any | None) -> tuple[str, str, Any, str | None, str | None]:
    """What a crashed slot's last launch says: `(action, why, row, session id, unread)`,
    the action `reserve` or `settle`."""
    from . import resume

    meta = slot.last.meta if slot.last is not None else None
    rid = meta.get("request_id") if isinstance(meta, dict) else None
    row = store.get(conn, rid) if isinstance(rid, str) and rid else None
    if row is None:
        return "settle", "its last launch is not the launcher's", None, None, None
    if row.target != "slot":
        return "settle", f"launch {row.request_id} was not a slot launch", row, None, None
    if row.state == "failed":
        return "settle", f"launch {row.request_id} failed", row, None, None
    known = store.session_ids(conn, row.request_id)
    newest = known[-1][0] if known else row.session_id
    if not newest:
        return "settle", f"launch {row.request_id} has no session id", row, None, None
    holder = store.resolve(conn, newest)
    if holder is not None and holder.request_id != row.request_id:
        return ("settle", f"launch {holder.request_id} took up session {newest} since",
                row, newest, None)
    live, unread = resume.liveness(row, newest, turns)
    if live:
        return "settle", f"session {newest} is live", row, newest, None
    if live is None:
        return "reserve", f"session {newest}'s liveness can't be read", row, newest, unread
    return "reserve", f"session {newest} was running when its server went down", row, \
        newest, None


def discover(conn: sqlite3.Connection, api: Any, *, by: str, write: bool = True,
             turns: Any = LOOK, listing: Any = None) -> list[Found]:
    """Crash discovery over every registered slot slots reads crashed. With
    `write=False` it writes nothing and says what it would do. Without slots, or with no
    tmux server answering (nothing reads crashed), it finds nothing."""
    if api is None:
        return []
    listing = listing if listing is not None else api.list_slots()
    if not listing.ok:
        return []
    park_id: str | None = None
    found: list[Found] = []
    for slot in listing.slots:
        if not getattr(slot, "crashed", False) or store.open_entry(conn, slot.number):
            continue
        if turns is LOOK:
            turns = first.turns_api(quiet=True)
        action, why, row, session_id, unread = _session_of(conn, slot, turns)
        held = slot.hold
        if action == "reserve" and held is not None:
            action, why = "held", f"held by {held.holder}; discovery tries again once it ends"
        seen = Found(slot.number, action, why, row.request_id if row else None, session_id,
                     row.harness if row else None, unread)
        if write:
            if park_id is None and action == "reserve":
                park_id = store.new_park_id()
            seen = _act(conn, api, seen, park_id, by)
        found.append(seen)
    return found


def _act(conn: sqlite3.Connection, api: Any, seen: Found, park_id: str | None,
         by: str) -> Found:
    """Write what discovery found for one slot: the reservation, the entry and the settle
    for `reserve`, the settle for `settle`."""
    from dataclasses import replace

    try:
        if seen.action == "settle":
            api.settle(slot=seen.slot, why=_settled_words(seen.why))
            return seen
        if seen.action != "reserve" or park_id is None:
            return seen
        holder = store.park_holder(park_id)
        api.hold(seen.slot, holder, None, expect=seen.request_id)
    except api.Refused as exc:
        action = "held" if exc.reason == "held" else "failed"
        return replace(seen, action=action, why=f"slots refused: {exc.words}")
    except (api.TryAgain, api.TmuxError, api.RegistryError) as exc:
        return replace(seen, action="failed", why=f"slots could not answer: {exc}")
    made = store.add_entry(conn, park_id=park_id, kind="crash", by=by, slot=seen.slot,
                           request_id=seen.request_id, session_id=seen.session_id,
                           harness=seen.harness, outcome="crashed",
                           detail=seen.unread or seen.why)
    try:
        api.settle(slot=seen.slot, why=f"reserved by {holder}")
    except (api.Refused, api.TryAgain, api.TmuxError, api.RegistryError) as exc:
        log.warning("slot %s was reserved but not settled: %s", seen.slot, exc)
    return replace(seen, entry=made)


def _settled_words(why: str) -> str:
    text = "".join(ch if ch.isprintable() else " " for ch in why)
    return text[:200] or "nothing to restore"


@dataclass(frozen=True)
class Prepared:
    """What a whole-run holder's reconcile and discovery did."""

    notes: tuple[Note, ...]
    found: tuple[Found, ...]

    def lines(self) -> list[str]:
        return [n.line() for n in self.notes] + [f.line() for f in self.found]


@contextmanager
def whole(kind: str, conn: sqlite3.Connection, api: Any, *, by: str,
          env: Mapping[str, str] | None = None, turns: Any = LOOK,
          wait_s: float = LOCK_WAIT_S) -> Iterator[Prepared]:
    """Hold the park lock as whole-run holder `kind`, run reconcile and crash discovery,
    mark the lock done, and hold it for the rest of the block."""
    if kind not in WHOLE_KINDS:
        raise ValueError(f"not a whole-run holder: {kind!r}")
    with lock(kind, env=env, wait_s=wait_s) as path:
        notes = reconcile(conn, api)
        found = discover(conn, api, by=by, turns=turns)
        mark_done(path)
        yield Prepared(tuple(notes), tuple(found))


def discover_before_launch(*, by: str, env: Mapping[str, str] | None = None,
                           turns: Any = LOOK, wait_s: float = LOCK_WAIT_S) -> list[Found]:
    """A slot launch's own crash discovery, under the park lock as `discovery`. Nothing
    when a whole-run holder that has run discovery holds the lock; `ParkRunning` when it
    stays held past the wait otherwise. Without slots it does nothing."""
    api = slots.api()
    if api is None:
        return []
    with discovery(env=env, wait_s=wait_s) as run_it:
        if not run_it:
            return []
        conn = store.connect(env=env)
        try:
            return discover(conn, api, by=by, turns=turns)
        finally:
            conn.close()


__all__ = ["ACTIONS", "DONE", "HOLDER_FILE", "LOOK", "Found", "Prepared", "discover", "discover_before_launch",
           "whole", "LOCK_FILE", "LOCK_KINDS", "LOCK_WAIT_S", "WHOLE_KINDS", "LockHolder",
           "Note", "ParkRunning", "discovery", "held_here", "lock", "lock_path", "mark_done",
           "read_holder", "reconcile", "resume_command"]
