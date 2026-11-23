"""The line of runs that wait for the machine's check lock, and the record of who holds it.

`files.lock` alone lets whichever waiter tries first after a release win. So a run that
must wait takes a ticket in the line (`take`), a file `<n>.json` in
`STATE/rounds/locks/check.line/` (`line_dir`), `n` one above the highest there and
zero-padded to twelve digits, holding the waiting process's `pid` and `created` (its
start time, so a pid given to another process since never matches), its `round`,
`stage`, `step` and `repo`, and `since`, when it was taken. Every change to the line is
made under `check.line.lock` beside it, held only while files are listed, written or
removed.

Each poll (`LINE_POLL_S`) a waiter removes every dead ticket (no live process has its
`pid` and `created`, as `moxi.proc.alive` reads it on every system) and touches its own
(`remove_dead`). It is first when no ticket ahead of it is live and fresh: a ticket not
touched for `TICKET_STALE_S` belongs to a waiter that lives but does not run, and is
passed over, not removed, until it is touched again. Only the first tries the check
lock, which stays what keeps two runs apart and still dies with its process.

The run that holds the lock writes `check.holder.json` beside it (`write_holder`): the
ticket's fields less `since`, then `output` (the run's output file) and `since`, the
time it took the lock; it removes the record when it releases. A reader trusts it only
while its `pid` and `created` name a live process (`holder`). The doctor's check lock
row reads all of this without taking any lock (`tickets`, `holder`, `lock_pid`).
"""

from __future__ import annotations

import calendar
import json
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from moxi import files, proc

from . import log

#: The line's folder, the lock its changes are made under, and the holder record, all
#: beside the check lock in rounds' folder of lock files.
LINE_DIR = "check.line"
LINE_LOCK = "check.line.lock"
HOLDER_FILE = "check.holder.json"
#: How often a waiter reads the line, in seconds.
LINE_POLL_S = 0.5
#: How long a ticket may go untouched before it is passed over, in seconds.
TICKET_STALE_S = 60.0
#: How long the holder's output may go unwritten before the doctor warns it may be hung,
#: in seconds.
QUIET_S = 1800.0
#: How many digits a ticket's number is padded to.
_DIGITS = 12


@dataclass(frozen=True)
class Ticket:
    """A ticket in the line: its number `n`, its file `path`, what it holds (`fields`)
    and when it was last touched (`touched_s`, seconds since the epoch)."""

    n: int
    path: Path
    fields: Mapping
    touched_s: float

    @property
    def dead(self) -> bool:
        """Whether no live process has the ticket's pid and start time."""
        return not _alive(self.fields)

    def stale(self, now_s: float) -> bool:
        """Whether the ticket has gone untouched for `TICKET_STALE_S` at `now_s`."""
        return now_s - self.touched_s >= TICKET_STALE_S


def line_dir(lock_path: Path) -> Path:
    """The line's folder beside the check lock `lock_path`."""
    return lock_path.with_name(LINE_DIR)


def holder_file(lock_path: Path) -> Path:
    """The holder record beside the check lock `lock_path`."""
    return lock_path.with_name(HOLDER_FILE)


def _alive(fields: Mapping) -> bool:
    try:
        found = proc.ProcId(int(fields["pid"]), float(fields["created"]))
    except (KeyError, TypeError, ValueError):
        return False
    return proc.alive(found)


def me() -> dict:
    """This process's `pid` and `created`, as a ticket or the holder record holds them."""
    found = proc.identity(os.getpid())
    return {"pid": os.getpid(), "created": None if found is None else found.started}


@contextmanager
def changing(lock_path: Path) -> Iterator[None]:
    """Hold the line's own lock for a change to it."""
    line_dir(lock_path).mkdir(parents=True, exist_ok=True)
    with files.lock(lock_path.with_name(LINE_LOCK)):
        yield


def tickets(lock_path: Path) -> list[Ticket]:
    """Every ticket in the line, in order; one that can't be read holds no fields, so
    it reads dead."""
    found = []
    try:
        entries = list(line_dir(lock_path).iterdir())
    except FileNotFoundError:
        return []
    for path in entries:
        if path.suffix != ".json" or not path.stem.isdigit():
            continue
        try:
            fields = json.loads(path.read_text(encoding="utf-8"))
            touched_s = path.stat().st_mtime
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            fields, touched_s = {}, 0.0
        found.append(Ticket(int(path.stem), path, fields if isinstance(fields, dict)
                            else {}, touched_s))
    return sorted(found, key=lambda one: one.n)


def take(lock_path: Path, fields: Mapping) -> Path:
    """Take a ticket at the end of the line, holding `fields` and `since`; its file."""
    with changing(lock_path):
        n = max((one.n for one in tickets(lock_path)), default=0) + 1
        path = line_dir(lock_path) / f"{n:0{_DIGITS}d}.json"
        files.atomic_write(path, json.dumps({**fields, "since": log.stamp()}))
    return path


def remove(path: Path) -> None:
    """Remove a ticket; one already gone is no matter."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def remove_dead(lock_path: Path, *, own: Path | None = None) -> list[Ticket]:
    """Under the line's lock: remove each dead ticket, touch `own`, and give the live,
    fresh tickets ahead of `own` (every live, fresh one, with `own` None)."""
    with changing(lock_path):
        if own is not None:
            try:
                os.utime(own)
            except FileNotFoundError:
                pass
        now_s = time.time()
        found = []
        mine = None if own is None else int(own.stem)
        for one in tickets(lock_path):
            if one.path == own:
                continue
            if one.dead:
                remove(one.path)
            elif (mine is None or one.n < mine) and not one.stale(now_s):
                found.append(one)
        return found


def write_holder(lock_path: Path, fields: Mapping) -> None:
    """Record that this process holds the check lock, with `fields` and `since`."""
    files.atomic_write(holder_file(lock_path),
                       json.dumps({**fields, "since": log.stamp()}))


def update_holder(lock_path: Path, **fields) -> None:
    """Add `fields` to this process's holder record."""
    found = holder(lock_path)
    if found is not None and found.get("pid") == os.getpid():
        files.atomic_write(holder_file(lock_path), json.dumps({**found, **fields}))


def remove_holder(lock_path: Path) -> None:
    """Remove the holder record, when it is this process's."""
    found = holder(lock_path)
    if found is not None and found.get("pid") == os.getpid():
        remove(holder_file(lock_path))


def holder(lock_path: Path) -> dict | None:
    """The holder record, while a live process has its pid and start time; else None."""
    try:
        found = json.loads(holder_file(lock_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) and _alive(found) else None


def lock_pid(lock_path: Path) -> int | None:
    """The pid the check lock's file holds, written by the process that holds it or
    last died holding it; None when it holds none."""
    try:
        with open(lock_path, "rb") as fh:
            first = fh.readline(32).strip()
    except OSError:
        return None
    return int(first) if first.isdigit() else None


def seconds(since: object) -> int | None:
    """The stamp `since` as seconds since the epoch; None for one that doesn't read."""
    try:
        return calendar.timegm(time.strptime(str(since), "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def age(since: object, now_s: float | None = None) -> str:
    """How long before `now_s` (now, with none) the stamp `since` was, in whole minutes,
    as words."""
    then = seconds(since)
    if then is None:
        return "an unknown time"
    now_s = time.time() if now_s is None else now_s
    return f"{max(int((now_s - then) // 60), 0)} min"


def held_by(found: Mapping | None, pid: int | None) -> str:
    """Who holds the check lock, in words: from its holder record `found`, else the
    `pid` its file holds, else "no one"."""
    if found is not None:
        return (f"pid {found.get('pid')} ({found.get('round')} {found.get('stage')}, for "
                f"{age(found.get('since'))})")
    return f"pid {pid}" if pid else "no one"


__all__ = ["LINE_DIR", "LINE_LOCK", "HOLDER_FILE", "LINE_POLL_S", "TICKET_STALE_S",
           "QUIET_S", "Ticket", "line_dir", "holder_file", "me", "changing", "tickets",
           "take", "remove", "remove_dead", "write_holder", "update_holder",
           "remove_holder", "holder", "lock_pid", "seconds", "age", "held_by"]
