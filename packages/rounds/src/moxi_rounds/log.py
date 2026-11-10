"""A log: the append-only file of events that is the one source of a round's state.

A round's log is `round.jsonl` in its folder and a project's is `project.jsonl` in the
project's rounds folder. Both hold one JSON object per line and are read and appended by
the functions here.

`read(path)` gives every event, oldest first. It answers in one of three ways and never
mixes them: the events (an empty list for an empty file), `NoLog` when no file is there,
or `Unreadable` when any line can't be read as the next event. A line is the next event
when it ends in a newline, parses as a JSON object, holds the five envelope keys in their
types, and its `n` is the line before's plus one (1 for the first). Nothing is skipped and
nothing is read up to the bad line: a log is read whole or not at all.

`append(path, name, fields, actor=...)` writes one event with the next `n`. The file is
only ever appended to, as one file of any size. The caller holds the log's lock: two
appends that both read the log before either writes would give one `n` twice.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path

from moxi import files

from . import events
from .errors import NoLog, TooNew, Unreadable

#: The name of a round's log in the round's folder.
ROUND_LOG = "round.jsonl"
#: The name of a project's log in the project's rounds folder.
PROJECT_LOG = "project.jsonl"


def round_log(folder: Path) -> Path:
    """The log of the round whose folder is `folder`."""
    return Path(folder) / ROUND_LOG


def project_log(folder: Path) -> Path:
    """The log of the project whose rounds folder is `folder`."""
    return Path(folder) / PROJECT_LOG


def exists(path: Path) -> bool:
    """Whether a log file is at `path`, readable or not."""
    return Path(path).is_file()


def stamp(when_s: float | None = None) -> str:
    """`when_s` (seconds since the epoch; now, with none) as an event's `at`: UTC to the
    second, `2026-11-04T10:44:00Z`."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when_s))


def read(path: Path) -> list[dict]:
    """Every event of the log `path`, oldest first. Raises `NoLog` when the file does not
    exist and `Unreadable` when a line is not the next event."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise NoLog(path) from None
    return parse(path, data)


def parse(path: Path, data: bytes) -> list[dict]:
    """The events in `data`, the whole content of the log `path`, oldest first. For a
    caller that needs the bytes it read and the events they hold to be one reading of
    the file. Raises `Unreadable` as `read` does."""
    path = Path(path)
    if not data:
        return []
    lines = data.split(b"\n")
    # What follows the last newline: empty in a whole file, a line cut short otherwise.
    tail = lines.pop()
    found = [_event(path, number, raw) for number, raw in enumerate(lines, start=1)]
    if tail:
        raise Unreadable(path, len(lines) + 1,
                         "ends without a newline (a line that was cut short)")
    return found


def _event(path: Path, number: int, raw: bytes) -> dict:
    """Line `number` of `path` as an event."""
    try:
        event = json.loads(raw)
    except ValueError:  # covers a line that is not UTF-8 too
        raise Unreadable(path, number, "does not parse as JSON") from None
    if not isinstance(event, dict):
        raise Unreadable(path, number, "is not a JSON object")
    n, version = event.get("n"), event.get("v")
    if not _whole(n) or not _whole(version):
        raise Unreadable(path, number, "has no whole number for n or for v")
    if n != number:
        raise Unreadable(path, number,
                         f"holds n {n} where {number} comes next (a gap or a repeat, "
                         "which is what two machines appending to one log leave)")
    if version != events.VERSION:
        raise TooNew(path, number, f"is in log format {version}, and this code reads "
                                   f"format {events.VERSION}")
    for key in ("at", "actor", "type"):
        if not isinstance(event.get(key), str) or not event[key]:
            raise Unreadable(path, number, f"has no text for {key}")
    return event


def _whole(value: object) -> bool:
    # JSON's true and false are numbers to Python and never a line's number.
    return isinstance(value, int) and not isinstance(value, bool)


def append(path: Path, name: str, fields: Mapping[str, object], *, actor: str,
           at: str | None = None) -> dict:
    """Append one event of type `name` to the log `path` and return it as written.

    The event takes the next `n`: 1 in a file that does not exist yet or is empty. `at` is
    the event's time as `stamp` writes one, now when left out. A log that can't be read
    is not appended to (`Unreadable`), and an event `events.check` refuses is not written
    (`ValueError`). The line is flushed to disk before this returns. The folder of `path`
    exists already; the caller holds the log's lock.
    """
    path = Path(path)
    events.check(name, fields)
    if not actor:
        raise ValueError("an event names who ran the verb")
    try:
        last = len(read(path))
    except NoLog:
        last = 0
    event = {"v": events.VERSION, "n": last + 1, "at": stamp() if at is None else at,
             "actor": actor, "type": name, **fields}
    # No size limit: the helper then never moves the file aside, so a log stays one file.
    files.append_record(path, event, max_bytes=None, durable=True)
    return event


__all__ = ["ROUND_LOG", "PROJECT_LOG", "round_log", "project_log", "exists", "stamp",
           "read", "parse", "append"]
