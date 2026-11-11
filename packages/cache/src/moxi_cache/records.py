"""The clock record: what the watcher last read of one live session's clock.

The watcher keeps one file per live Claude session, `RUNTIME/cache/clock/<session>.json`,
replaced whole at every tick that finds the session live:

    {"v": 1, "session": ..., "harness": "claude", "pid": ..., "kind": ...,
     "last_request": <epoch seconds or null>, "ttl": <seconds or null>,
     "read_at": <epoch seconds>, "seen_at": <epoch seconds>, "code": <code or null>,
     "warn_before": <seconds>, "stale_after": <seconds>}

`kind`, `ttl` and `code` are the clock's read (`api.read`) at `read_at`, and
`last_request` is when the session's last request ended by that read. `seen_at` is the
newest tick that found the session live: it moves at every tick, read or not, so a
record whose `seen_at` stands still means the watcher has stopped, not that the session
is idle. The file goes when the session leaves Claude Code's live list.

**A clock record carries every value a reader needs to render or judge it, so
no reader takes a record's band or staleness from cache's config.** `warn_before` is the
effective `segment.warn_before_s`: the band turns from warm to near at `ttl - warn_before`.
`stale_after` is three times the watcher's effective tick (`watch.tick_s`, at least one
second): a record whose `seen_at` is older than that shows dim. The watcher writes both at
every tick from its config as it read it at that tick, so a config change reaches every
record within one tick. A record without them, one an older watcher wrote, renders
nothing until the next tick rewrites it (`thresholds` gives None); it is still a record,
so the timer and `moxi cache timers` read it.

The format is a contract: a shell status line reads it with one `jq` and no config. A
record is for display and for the timer; it can lag the transcript by a tick, so nothing
decides about a cache from it. A caller that decides calls `api.read`.

The watcher's reading is one more file beside the records' directory,
`RUNTIME/cache/watch.json`, replaced whole after every tick:

    {"v": 1, "pid": ..., "tick_at": <epoch seconds>, "listed_at": <epoch seconds or null>,
     "stale_after": <seconds>}

`tick_at` is the newest tick. `listed_at` is the newest tick that held Claude Code's live
list and was not halted on a shape the watcher does not know: the last time the records
matched the live sessions, none live included. It is null until the first such tick, and
it is not a record's `read_at`, which is when one session's clock was read. `stale_after`
is the one the watcher writes into its records. The file stays when the watcher stops, so
its `tick_at` is then when it stopped. A watcher older than this file writes none.
`reading` loads it. It is not under `clock/`, so the listing of records never sees it.

`watching` judges a watcher from the reading, the records and the time, the one rule by
which `moxi cache doctor` and cache's page say how the watcher stands.

This module imports only the standard library and the core, so the status line segment
can read records without loading the clock.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from moxi.paths import Layout

#: The record format's version.
VERSION = 1
#: The two fields a reader renders and judges a record by.
THRESHOLDS = ("warn_before", "stale_after")
#: Every field a record holds, in the order the watcher writes them.
FIELDS = ("v", "session", "harness", "pid", "kind", "last_request", "ttl", "read_at",
          "seen_at", "code") + THRESHOLDS
#: The fields every record holds; a record an older watcher wrote, before records
#: carried them, lacks `THRESHOLDS`, and is still a record.
REQUIRED = FIELDS[:-len(THRESHOLDS)]
#: `stale_after` is this many of the watcher's ticks.
STALE_TICKS = 3
#: The directory under cache's runtime directory that holds the records.
DIR_NAME = "clock"

#: The watcher's reading: its file under cache's runtime directory, its format's version
#: and its fields, in the order the watcher writes them.
READING_NAME = "watch.json"
READING_VERSION = 1
READING_FIELDS = ("v", "pid", "tick_at", "listed_at", "stale_after")

#: How a watcher stands, `watching`'s four outcomes, in the order they are judged.
FRESH = "fresh"
NOT_READING = "not-reading"
STOPPED = "stopped"
NO_READING = "no-reading"

#: A session id that can name a record file: no separator, no leading dot.
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def record_dir(env=None) -> Path:
    """`RUNTIME/cache/clock`, where the records are."""
    return Layout("cache").runtime_dir(env) / DIR_NAME


def reading_path(env=None) -> Path:
    """`RUNTIME/cache/watch.json`, the watcher's reading."""
    return Layout("cache").runtime_dir(env) / READING_NAME


def record_path(session_id, directory: Path | None = None) -> Path | None:
    """The record file of one session, or None when the id cannot name a file."""
    if not isinstance(session_id, str) or not SAFE_ID_RE.fullmatch(session_id):
        return None
    return (record_dir() if directory is None else Path(directory)) / f"{session_id}.json"


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value) -> bool:
    if not _number(value):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def valid(record) -> bool:
    """Whether `record` holds to the format. The two `THRESHOLDS` are not asked for here:
    `thresholds` reads them."""
    if not isinstance(record, dict) or record.get("v") != VERSION:
        return False
    if any(name not in record for name in REQUIRED):
        return False
    return (isinstance(record["session"], str) and isinstance(record["harness"], str)
            and isinstance(record["pid"], int) and not isinstance(record["pid"], bool)
            and isinstance(record["kind"], str)
            and (record["last_request"] is None or _number(record["last_request"]))
            and (record["ttl"] is None or _number(record["ttl"]))
            and _number(record["read_at"]) and _number(record["seen_at"])
            and (record["code"] is None or isinstance(record["code"], str)))


def thresholds(record: dict) -> tuple[float, float] | None:
    """The record's own `(warn_before, stale_after)`, or None when it lacks either or
    holds one that is not a finite number. A reader renders nothing for a record without
    them, as for a missing record."""
    found = tuple(record.get(name) for name in THRESHOLDS)
    if all(_number(v) and math.isfinite(v) for v in found):
        return found
    return None


def load(path: Path) -> dict | None:
    """One record, or None when it is absent, unreadable or not in the format."""
    try:
        with open(path, "rb") as fh:
            record = json.loads(fh.read())
    except (OSError, ValueError):
        return None
    return record if valid(record) else None


def reading(path: Path | None = None) -> dict | None:
    """The watcher's reading, its `READING_FIELDS` alone, or None when the file is absent,
    unreadable or not in the format: `pid` an integer, `tick_at` and `stale_after` finite
    numbers, `listed_at` a finite number or null."""
    try:
        with open(reading_path() if path is None else path, "rb") as fh:
            doc = json.loads(fh.read())
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("v") != READING_VERSION:
        return None
    if any(name not in doc for name in READING_FIELDS):
        return None
    pid = doc["pid"]
    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    if not (_finite(doc["tick_at"]) and _finite(doc["stale_after"])
            and (doc["listed_at"] is None or _finite(doc["listed_at"]))):
        return None
    return {name: doc[name] for name in READING_FIELDS}


@dataclass(frozen=True)
class Watching:
    """How a watcher stands (`watching`): one of `FRESH`, `NOT_READING`, `STOPPED` and
    `NO_READING`, with `at`, the newest moment the records were known to match the live
    sessions, and `last`, the newest moment there is evidence a watcher ran. Each is None
    when nothing carries it."""

    outcome: str
    at: float | None
    last: float | None


def watching(found: dict | None, records: Iterable[dict], now: float) -> Watching:
    """Judge a watcher by the newest evidence, never by one record.

    `found` is the reading (`reading`) or None; `records` the clock records. `at` is the
    newer of the reading's `listed_at` and the newest `seen_at` among the records with a
    finite `stale_after`; its `stale` is the `stale_after` that came with the one taken (on
    equal times, the larger). A record with no finite `stale_after` cannot say whether it
    is old and takes no part. The watcher is ticking when there is a reading and
    `now - tick_at <= stale_after`, both the reading's. `last` is the newer of the
    reading's `tick_at` and `at`. The first that fits is the outcome:

    1. `FRESH`: `at` is not None and `now - at <= stale`;
    2. `NOT_READING`: it is ticking, so it runs and cannot list Claude's sessions;
    3. `STOPPED`: `last` is not None, the time it stopped;
    4. `NO_READING`: no reading, and no record that takes part.
    """
    newest: tuple[float, float] | None = None
    if found is not None and found["listed_at"] is not None:
        newest = (found["listed_at"], found["stale_after"])
    for record in records:
        seen, stale = record.get("seen_at"), record.get("stale_after")
        if _finite(seen) and _finite(stale) and (newest is None or (seen, stale) > newest):
            newest = (seen, stale)
    at, stale = newest if newest is not None else (None, None)
    ticking = found is not None and now - found["tick_at"] <= found["stale_after"]
    times = [t for t in (found["tick_at"] if found is not None else None, at)
             if t is not None]
    last = max(times) if times else None
    if at is not None and now - at <= stale:
        outcome = FRESH
    elif ticking:
        outcome = NOT_READING
    elif last is not None:
        outcome = STOPPED
    else:
        outcome = NO_READING
    return Watching(outcome, at, last)


__all__ = ["VERSION", "FIELDS", "REQUIRED", "THRESHOLDS", "STALE_TICKS", "DIR_NAME",
           "SAFE_ID_RE", "READING_NAME", "READING_VERSION", "READING_FIELDS", "FRESH",
           "NOT_READING", "STOPPED", "NO_READING", "Watching", "record_dir", "record_path",
           "reading_path", "valid", "thresholds", "load", "reading", "watching"]
