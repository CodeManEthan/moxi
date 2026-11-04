"""The call log: one JSON object per line, one file per UTC day.

    STATE/connector/calls/<YYYY-MM-DD>.jsonl     the directory 0700, each file 0600

The door builds its rows (`moxi_connector.app`) and gives each one to a `Writer`:

    write(row, *, durable, collapse)

- `row` is a flat mapping of plain values with `at` (UTC, ISO 8601, to the millisecond)
  and `kind` (`request`, a sign-in event's name, `tool-error`, `origin-changed`, or
  `check`, the last `moxi connector check`);
- `durable` is True for rows of tool calls with a valid sign-in and for events: they are
  on disk when `write` returns. Request rows are not, so a power loss can lose the last
  second of those;
- `collapse` is None, or the class name of a row a caller without a token can cause.
  Every request row without a valid grant is collapsed, whatever its status, in the class
  `<route>-<status>` (`prm-200`, `token-400`, `mcp-401`); so are registrations
  (`register`). Rows of calls with a valid grant and the other events are never
  collapsed.

`flush()` is called every 30 s and `close()` at the process's stop.

`CallLog` is the process's writer, built on the core's `moxi.files.append_record`:

- **No secrets.** A row holding one of the names in `FORBIDDEN` as a key, at any depth
  and in any case (`Authorization` as well as `authorization`), raises `ValueError` and
  writes nothing. `check` applies that rule before the write, since the core's `refuse`
  option, which every row also goes through, matches names exactly. A provider's
  `ctx.log` fields pass the same refusal, and `MemoryLog` applies the same `check`.
- **Nothing always-written is lost.** `max_bytes=None`, so a file never rolls. The only
  thing that removes a row is the deletion of files older than `RETAIN_DAYS`, at start
  (`prune()`, which the daemon calls before it serves) and at each UTC midnight (the first
  write or flush of a new day).
- **The day is bounded.** A collapsed row goes through `append_record`'s `collapse`
  option with the writer's one `moxi.files.Collapse`: past `COLLAPSE_AFTER` rows of one
  class in a minute the rest of that minute become one `summary` row with their count, and
  once the day's file holds `DAY_CAP` bytes those classes write one summary an hour.

`MemoryLog` keeps the rows in memory, for tests. `read_rows` reads the day files back, for
`moxi connector log`, `status` and the doctor.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol

from .provider import FORBIDDEN, refused_field

log = logging.getLogger("moxi.connector.calllog")

MODULE = "connector"
#: The store's directory, under the module's state directory.
CALLS_DIR = "calls"
#: Days of files kept; older ones are deleted at start and at each UTC midnight.
RETAIN_DAYS = 30
#: Rows of one collapsed class written in a minute before the rest are counted.
COLLAPSE_AFTER = 10
#: Past this many bytes in a day's file, collapsed classes write one summary an hour.
DAY_CAP = 64 * 1024 * 1024


class Writer(Protocol):
    def write(self, row: Mapping[str, Any], *, durable: bool,
              collapse: str | None) -> None: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


def now() -> str:
    """The time for a row's `at`: UTC, to the millisecond."""
    return stamp(time.time())


def stamp(t: float) -> str:
    """`t` as a row writes it: `2026-11-04T10:44:00.250Z`."""
    return _utc(t).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _utc(t: float) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(t, _dt.UTC)


def day(t: float) -> str:
    """The UTC date of `t`, the name of its day file without `.jsonl`."""
    return _utc(t).strftime("%Y-%m-%d")


def calls_dir(env: Mapping[str, str] | None = None) -> Path:
    """The call log's directory, `STATE/connector/calls`."""
    from moxi.paths import Layout

    return Layout(MODULE).state_dir(env) / CALLS_DIR


def check(row: Mapping[str, Any]) -> None:
    """Raise `ValueError` when `row` holds a field the log refuses, at any depth and in
    any case."""
    bad = refused_field(row, FORBIDDEN)
    if bad is not None:
        raise ValueError(f"the call log refuses the field {bad!r}")


class CallLog:
    """The day-file writer, one per process. Thread-safe: `append_record` writes each
    line with one call, and the collapse keeps its own lock.

    `directory` defaults to `calls_dir()`; `clock` gives seconds since the epoch and
    names the day file a row goes to."""

    def __init__(self, directory: str | os.PathLike | None = None, *,
                 clock: Callable[[], float] = time.time):
        from moxi import files

        self.dir = Path(directory) if directory is not None else calls_dir()
        self._clock = clock
        self.window = files.Collapse(COLLAPSE_AFTER, DAY_CAP, clock=clock)
        self._made = False
        self._lock = threading.Lock()
        self._pruned: str | None = None

    def path(self, t: float | None = None) -> Path:
        """The day file of `t` (now, with none)."""
        return self.dir / f"{day(self._clock() if t is None else t)}.jsonl"

    def write(self, row: Mapping[str, Any], *, durable: bool, collapse: str | None) -> None:
        from moxi import files

        check(row)
        t = self._clock()
        self._new_day(t)
        if not self._made:
            files.private_dir(self.dir)
            self._made = True
        files.append_record(self.path(t), dict(row), max_bytes=None, durable=durable,
                            refuse=FORBIDDEN,
                            collapse=(self.window, collapse) if collapse else None)

    def flush(self) -> None:
        """Write the summaries of the windows that have ended, of every day's file; at
        the first flush of a UTC day, delete the files past `RETAIN_DAYS`."""
        self.window.flush()
        self._new_day(self._clock())

    def close(self) -> None:
        """Write every pending summary."""
        self.window.close()

    def _new_day(self, t: float) -> None:
        today = day(t)
        if self._pruned == today:
            return
        with self._lock:
            if self._pruned == today:
                return
            self._pruned = today
        self.prune(t)

    def prune(self, t: float | None = None) -> list[str]:
        """Delete the day files older than `RETAIN_DAYS` days before `t` (now), and
        return their names. Today's file and the 29 before it are kept."""
        if t is None:
            t = self._clock()
            self._pruned = day(t)
        oldest = (_utc(t).date() - _dt.timedelta(days=RETAIN_DAYS - 1)).isoformat()
        gone: list[str] = []
        try:
            names = os.listdir(self.dir)
        except OSError:
            return gone
        for name in sorted(names):
            stem, dot, suffix = name.partition(".")
            if suffix == "jsonl" and dot and is_day(stem) and stem < oldest:
                try:
                    os.unlink(self.dir / name)
                    gone.append(name)
                except OSError:
                    log.warning("the call log file %s can't be deleted", name,
                                exc_info=True)
        return gone


class MemoryLog:
    """A `Writer` that keeps every row in memory, with what it was written with."""

    def __init__(self):
        self._lock = threading.Lock()
        #: (row, durable, collapse) for each row written, in order.
        self.written: list[tuple[dict[str, Any], bool, str | None]] = []
        self.flushes = 0
        self.closed = False

    @property
    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return [row for row, _, _ in self.written]

    def write(self, row: Mapping[str, Any], *, durable: bool, collapse: str | None) -> None:
        check(row)
        with self._lock:
            self.written.append((dict(row), durable, collapse))

    def flush(self) -> None:
        self.flushes += 1

    def close(self) -> None:
        self.closed = True


def event(log_: Writer, kind: str, **fields: Any) -> None:
    """Write one event row, durable and never collapsed."""
    log_.write({"at": now(), "kind": kind, **fields}, durable=True, collapse=None)


def is_day(text: str) -> bool:
    """Whether `text` is a `YYYY-MM-DD` date."""
    try:
        _dt.date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


def read_rows(*, days: int = 1, now: float | None = None,
              directory: str | os.PathLike | None = None) -> Iterator[dict[str, Any]]:
    """The rows of today's file and the `days - 1` days before it, oldest first. A line
    that isn't a JSON object is skipped."""
    when = _utc(time.time() if now is None else now).date()
    folder = Path(directory) if directory is not None else calls_dir()
    for back in range(max(days, 1) - 1, -1, -1):
        path = folder / f"{(when - _dt.timedelta(days=back)).isoformat()}.jsonl"
        try:
            handle = path.open("rb")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row


def last(kinds: Iterable[str], *, days: int = 2, now: float | None = None,
         directory: str | os.PathLike | None = None) -> dict[str, Any] | None:
    """The latest row of one of `kinds` in `days` days, or None."""
    wanted = frozenset(kinds)
    found = None
    for row in read_rows(days=days, now=now, directory=directory):
        if row.get("kind") in wanted:
            found = row
    return found


def detail(row: Mapping[str, Any]) -> str:
    """A row's fields but `at` and `kind`, as `key=value` in key order, None left out."""
    return " ".join(f"{key}={row[key]}" for key in sorted(row)
                    if key not in ("at", "kind") and row[key] is not None)


__all__ = ["CALLS_DIR", "COLLAPSE_AFTER", "CallLog", "DAY_CAP", "MemoryLog", "RETAIN_DAYS",
           "Writer", "calls_dir", "check", "day", "detail", "event", "is_day", "last", "now",
           "read_rows", "stamp"]
