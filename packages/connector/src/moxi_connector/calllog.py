"""The call log's writer interface: what the door hands each row to.

The door builds its rows (`moxi_connector.app`) and gives each one to a `CallLog`:

    write(row, *, durable, collapse)

- `row` is a flat mapping of plain values, already checked against `FORBIDDEN`, with
  `at` (UTC, ISO 8601) and `kind` (`request`, or a sign-in event's name, or
  `tool-error`, or `origin-changed`);
- `durable` is True for rows of tool calls with a valid sign-in and for events: they must
  be on disk when `write` returns;
- `collapse` is None, or the class name of a row a caller without a token can cause (a
  401, 404, 405, 421, 429 or 503 with no grant): the writer may fold those past a rate
  into one summary row with a count.

`flush()` is called every 30 s and `close()` at the process's stop.

`MemoryLog` keeps the rows in memory; it is the process's writer until the day files are
built on the core's `append_record` options, and the tests' writer after. A writer
refuses a row holding a `FORBIDDEN` name, at any depth, with `ValueError`, and writes
nothing of it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any, Protocol

from .provider import FORBIDDEN, refused_field

#: Status codes a caller without a token can cause; their rows are collapsed by class.
COLLAPSED_STATUSES = frozenset({401, 404, 405, 421, 429, 503})


class CallLog(Protocol):
    def write(self, row: Mapping[str, Any], *, durable: bool,
              collapse: str | None) -> None: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


def now() -> str:
    """The time for a row's `at`: UTC, to the millisecond."""
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1000):03d}Z"


def check(row: Mapping[str, Any]) -> None:
    """Raise `ValueError` when `row` holds a field the log refuses."""
    bad = refused_field(row, FORBIDDEN)
    if bad is not None:
        raise ValueError(f"the call log refuses the field {bad!r}")


class MemoryLog:
    """A `CallLog` that keeps every row in memory, with what it was written with."""

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


def event(log: CallLog, kind: str, **fields: Any) -> None:
    """Write one event row, durable and never collapsed."""
    log.write({"at": now(), "kind": kind, **fields}, durable=True, collapse=None)


__all__ = ["COLLAPSED_STATUSES", "CallLog", "MemoryLog", "check", "event", "now"]
