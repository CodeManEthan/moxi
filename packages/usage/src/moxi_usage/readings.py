"""The limits readings store, `STATE/usage/readings.db`, and its one write path.

Every limits reading usage sees is kept here for `limits.history_days` (8 by default: a
7-day window plus a day), so `limits()` answers with no live session and a window's
history can project when it fills. The store is apart from the token index, so a
listener's write never waits on a pass.

A reading is one window of one harness's report, one row:

* `harness`: `claude` or `codex`.
* `limit_id`: Codex's limit id (`codex` when the reading names none); for Claude `plan`,
  or the scope of a per-model weekly cap.
* `window_minutes`: the window's duration. NULL only for a Claude limit of a kind whose
  duration is not known; a Codex window with no duration is not stored.
* `slot`: the harness's own name for the slot (`primary`, `five_hour`, ...), kept for
  display only.
* `used_percent` (0-100), `resets_at` and `observed_at` (epoch seconds).
* `source`: `rollout`, `app-server` or `session-usage`.
* `session_id`, `plan_type`, and `extra` (JSON text of whatever else the reading said).

The rule: a window is `(harness, limit_id, window_minutes)`, whatever slot the harness
put it in. Codex moves its weekly window between slots, so a slot name never identifies
one. When one reading names two slots with the same duration, they are one window and the
higher percent is kept.

The write path:

* `add_codex(records, *, now, env=None)` stores sessions' Codex `Limits` records, from a
  rollout or from the app-server (each record's `source` says which); the pass and
  `limits()` call it.
* `add_claude(rate_limits, *, observed_at, session_id, now, env=None)` stores one
  `$.session.usage()` result's `rateLimits`, unless it is identical to the session's
  previous one.
* `prune(*, now, env=None)` deletes readings older than the history cap.

A reading older than the history cap is never stored, and a reading stored once is never
stored again: each write returns the number of new rows.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    import sqlite3

    from moxi_sessions.api import Limits

#: The store's file name, under usage's state directory.
FILE = "readings.db"

CLAUDE = "claude"
CODEX = "codex"

ROLLOUT = "rollout"
APP_SERVER = "app-server"
SESSION_USAGE = "session-usage"

#: A Codex reading that names no limit id is the account's main limit, `codex`; Claude's
#: plan windows are limit `plan`.
CODEX_LIMIT = "codex"
PLAN_LIMIT = "plan"

FIVE_HOUR = 300
SEVEN_DAY = 10_080

#: What `percentUsed` in a `$.session.usage()` result is multiplied by to make 0-100.
#: turns' one live check found it already on 0-100, so it is read as given.
PERCENT_SCALE = 1.0

#: Claude's rate-limit kinds whose window is known: kind -> (limit id, minutes).
#: `seven_day_<scope>` is limit `<scope>`, 7 days: a per-model weekly cap is a limit of
#: its own, not a second slot of the plan's.
CLAUDE_KINDS = {"five_hour": (PLAN_LIMIT, FIVE_HOUR), "seven_day": (PLAN_LIMIT, SEVEN_DAY)}
_SCOPED_WEEK = "seven_day_"
_CLAUDE_NAMED = frozenset({"kind", "percentUsed", "resetsAt"})

DAY = 86_400.0

#: Where the time of the last prune is kept, in the `meta` table.
LAST_PRUNE = "last_prune"

STEPS: Sequence[str] = (
    """
    CREATE TABLE readings (
      id INTEGER PRIMARY KEY,
      harness TEXT NOT NULL,
      limit_id TEXT NOT NULL,
      window_minutes INTEGER,
      slot TEXT,
      used_percent REAL,
      resets_at REAL,
      observed_at REAL NOT NULL,
      source TEXT NOT NULL,
      session_id TEXT,
      plan_type TEXT,
      extra TEXT);
    CREATE INDEX readings_window
      ON readings(harness, limit_id, window_minutes, observed_at);
    CREATE UNIQUE INDEX readings_once
      ON readings(harness, limit_id, coalesce(window_minutes, -1), observed_at, source,
                  coalesce(session_id, ''));
    CREATE TABLE meta (key TEXT PRIMARY KEY, value REAL);
    """,
)

_COLUMNS = ("harness", "limit_id", "window_minutes", "slot", "used_percent", "resets_at",
            "observed_at", "source", "session_id", "plan_type", "extra")
_INSERT = (f"INSERT OR IGNORE INTO readings ({', '.join(_COLUMNS)}) "
           f"VALUES ({', '.join('?' * len(_COLUMNS))})")


class Reading(NamedTuple):
    """One stored reading: one window of one report (the module doc names each field)."""

    harness: str
    limit_id: str
    window_minutes: int | None
    slot: str | None
    used_percent: float | None
    resets_at: float | None
    observed_at: float
    source: str
    session_id: str | None = None
    plan_type: str | None = None
    extra: str | None = None

    @property
    def window(self) -> tuple[str, str, int | None]:
        """The window this reading is of: `(harness, limit_id, window_minutes)`."""
        return (self.harness, self.limit_id, self.window_minutes)


# --- the store ------------------------------------------------------------------------

def path(env: Mapping[str, str] | None = None) -> Path:
    """Where the store lives: usage's state directory, `readings.db`."""
    from moxi.paths import Layout

    return Layout("usage").state_dir(env) / FILE


def open_store(env: Mapping[str, str] | None = None) -> sqlite3.Connection:
    """Open the store, creating it and bringing its schema up to date. The caller closes
    the connection. Raises `moxi.store.StoreTooNew` for a store a newer usage wrote."""
    from moxi.store import connect, migrate

    conn = connect(path(env))
    try:
        migrate(conn, STEPS)
    except BaseException:
        conn.close()
        raise
    return conn


def history_days(env: Mapping[str, str] | None = None) -> int:
    """`limits.history_days` from usage's config. Raises `moxi.config.ConfigError`."""
    from .settings import current

    return int(current(env)["limits.history_days"])


def cutoff(now: float, days: float) -> float:
    """The oldest `observed_at` the history cap keeps at `now`."""
    return now - days * DAY


# --- the write path -------------------------------------------------------------------

def add_codex(records: Iterable[Limits], *, now: float,
              env: Mapping[str, str] | None = None) -> int:
    """Store sessions' Codex limits records, one row per window with a known
    `window_minutes`, and return how many rows are new.

    Each record's `source` (`rollout` or `app-server`) is kept. Two slots of one duration
    in one record are one window, kept at the higher percent. A reading older than the
    history cap at `now` is skipped, and one already stored is ignored. A record that is
    not a `limits` record is skipped.
    """
    found = [reading for record in records for reading in codex_readings(record)]
    if not found:
        return 0
    days = history_days(env)
    conn = open_store(env)
    try:
        return store(conn, found, now=now, days=days)
    finally:
        conn.close()


def add_claude(rate_limits: Any, *, observed_at: float, session_id: str | None,
               now: float, env: Mapping[str, str] | None = None) -> int:
    """Store one `$.session.usage()` result's `rateLimits` list, as read at `observed_at`
    by session `session_id`, and return how many rows are new.

    A result identical to the session's previous stored one is not stored again, so a
    session that reports the same numbers each turn adds nothing.
    """
    found = claude_readings(rate_limits, observed_at=observed_at, session_id=session_id)
    if not found:
        return 0
    days = history_days(env)
    if observed_at < cutoff(now, days):
        return 0
    from moxi.store import transaction

    conn = open_store(env)
    try:
        with transaction(conn):
            if _same_as_before(conn, found, session_id):
                return 0
            return _insert(conn, found)
    finally:
        conn.close()


def prune(*, now: float, env: Mapping[str, str] | None = None) -> int:
    """Delete the readings older than `limits.history_days` at `now`, note the time, and
    return how many were deleted."""
    days = history_days(env)
    conn = open_store(env)
    try:
        return prune_store(conn, now=now, days=days)
    finally:
        conn.close()


def store(conn: sqlite3.Connection, found: Iterable[Reading], *, now: float,
          days: float) -> int:
    """Store `found` through an open store, in one transaction, under the history cap.
    Returns the count of new rows."""
    from moxi.store import transaction

    oldest = cutoff(now, days)
    kept = [reading for reading in found if reading.observed_at >= oldest]
    if not kept:
        return 0
    with transaction(conn):
        kept = [_fill_reset(conn, reading) for reading in kept]
        return _insert(conn, kept)


def prune_store(conn: sqlite3.Connection, *, now: float, days: float) -> int:
    """`prune` through an open store."""
    from moxi.store import transaction

    with transaction(conn):
        deleted = conn.execute("DELETE FROM readings WHERE observed_at < ?",
                               (cutoff(now, days),)).rowcount
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (LAST_PRUNE, now))
    return deleted


def last_prune(conn: sqlite3.Connection) -> float | None:
    """When the store was last pruned, or None when it never was."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (LAST_PRUNE,)).fetchone()
    return None if row is None else row[0]


def _insert(conn: sqlite3.Connection, found: Iterable[Reading]) -> int:
    added = 0
    for reading in found:
        added += conn.execute(_INSERT, tuple(reading)).rowcount
    return added


def _fill_reset(conn: sqlite3.Connection, reading: Reading) -> Reading:
    """An app-server reading with no reset time takes the one the newest earlier reading
    of its window gave, while that time is still ahead of this reading.

    Codex says a null in an app-server update means the value was not sent this time,
    not that it was cleared, so the reading is merged with the one before it. A rollout's
    null is kept as written: there it means the window has just reset.
    """
    if reading.source != APP_SERVER or reading.resets_at is not None:
        return reading
    row = conn.execute(
        "SELECT resets_at FROM readings WHERE harness = ? AND limit_id = ? "
        "AND window_minutes IS ? AND observed_at <= ? AND resets_at > ? "
        "ORDER BY observed_at DESC LIMIT 1",
        (reading.harness, reading.limit_id, reading.window_minutes, reading.observed_at,
         reading.observed_at)).fetchone()
    return reading if row is None else reading._replace(resets_at=row[0])


def _same_as_before(conn: sqlite3.Connection, found: list[Reading],
                    session_id: str | None) -> bool:
    """True when the session's newest stored Claude result says exactly what `found`
    says, time aside."""
    previous = conn.execute(
        "SELECT limit_id, window_minutes, slot, used_percent, resets_at, plan_type, extra "
        "FROM readings WHERE harness = ? AND session_id IS ? AND observed_at = "
        "(SELECT max(observed_at) FROM readings WHERE harness = ? AND session_id IS ?)",
        (CLAUDE, session_id, CLAUDE, session_id)).fetchall()
    if not previous:
        return False
    now = {(r.limit_id, r.window_minutes, r.slot, r.used_percent, r.resets_at, r.plan_type,
            r.extra) for r in found}
    return now == set(previous)


# --- mapping reports to readings ------------------------------------------------------

def codex_readings(record: Any) -> list[Reading]:
    """One sessions `Limits` record as readings: one per window with a known duration,
    two slots of one duration kept once at the higher percent."""
    if getattr(record, "kind", None) != "limits":
        return []
    observed = epoch(_said(getattr(record, "observed_at", None)))
    if observed is None:
        observed = epoch(_said(getattr(record, "time", None)))
    source = _said(getattr(record, "source", None))
    if observed is None or not isinstance(source, str) or not source:
        return []
    limit_id = _said(getattr(record, "limit_id", None))
    limit_id = limit_id if isinstance(limit_id, str) and limit_id else CODEX_LIMIT
    plan_type = _said(getattr(record, "plan_type", None))
    session_id = _said(getattr(record, "session_id", None))
    extra = {k: v for k, v in (("limit_name", _said(getattr(record, "limit_name", None))),
                               ("reached_type", _said(getattr(record, "reached_type", None))))
             if v is not None}
    extra.update(getattr(record, "extra", None) or {})
    out = []
    for window in getattr(record, "windows", ()) or ():
        minutes = _minutes(_said(getattr(window, "window_minutes", None)))
        if minutes is None:
            continue                      # a window with no duration has no identity
        out.append(Reading(
            harness=CODEX, limit_id=limit_id, window_minutes=minutes,
            slot=str(getattr(window, "name", "")) or None,
            used_percent=percent(_said(getattr(window, "used_percent", None))),
            resets_at=epoch(_said(getattr(window, "resets_at", None))),
            observed_at=observed, source=source,
            session_id=session_id if isinstance(session_id, str) else None,
            plan_type=plan_type if isinstance(plan_type, str) else None,
            extra=_extra(extra)))
    return one_per_window(out)


def claude_readings(rate_limits: Any, *, observed_at: float,
                    session_id: str | None) -> list[Reading]:
    """A `$.session.usage()` result's `rateLimits` entries as readings.

    `five_hour` is limit `plan`, 300 minutes; `seven_day` is `plan`, 10,080;
    `seven_day_<scope>` is limit `<scope>`, 10,080. Any other kind is a limit of its own,
    named by the kind, with no known duration. `percentUsed` is scaled to 0-100 by
    `PERCENT_SCALE`; `resetsAt` may be ISO 8601 text or epoch seconds. An entry's other
    fields (a spend limit's dollars, say) are kept in `extra`.
    """
    if not isinstance(rate_limits, (list, tuple)):
        return []
    out = []
    for entry in rate_limits:
        if not isinstance(entry, Mapping):
            continue
        kind = entry.get("kind")
        if not isinstance(kind, str) or not kind:
            continue
        limit_id, minutes = claude_window(kind)
        used = percent(entry.get("percentUsed"))
        out.append(Reading(
            harness=CLAUDE, limit_id=limit_id, window_minutes=minutes, slot=kind,
            used_percent=None if used is None else used * PERCENT_SCALE,
            resets_at=epoch(entry.get("resetsAt")), observed_at=float(observed_at),
            source=SESSION_USAGE, session_id=session_id,
            extra=_extra({k: v for k, v in entry.items() if k not in _CLAUDE_NAMED})))
    return one_per_window(out)


def claude_window(kind: str) -> tuple[str, int | None]:
    """A Claude rate-limit kind's `(limit_id, window_minutes)`."""
    if kind in CLAUDE_KINDS:
        return CLAUDE_KINDS[kind]
    if kind.startswith(_SCOPED_WEEK) and len(kind) > len(_SCOPED_WEEK):
        return kind[len(_SCOPED_WEEK):], SEVEN_DAY
    return kind, None


def one_per_window(found: list[Reading]) -> list[Reading]:
    """Readings of one report with one per window: of two with the same window, the one
    with the higher percent (a known percent above an unknown one), else the first."""
    best: dict[tuple, Reading] = {}
    for reading in found:
        held = best.get(reading.window)
        if held is None or _rank(reading) > _rank(held):
            best[reading.window] = reading
    return list(best.values())


def _rank(reading: Reading) -> float:
    return -1.0 if reading.used_percent is None else reading.used_percent


# --- small conversions ------------------------------------------------------------------

def _said(value: Any) -> Any:
    """`value`, or None for sessions' `UNKNOWN`."""
    from moxi_sessions.api import UNKNOWN

    return None if value is UNKNOWN else value


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def percent(value: Any) -> float | None:
    """A percentage as a float, or None. A bool is not a number."""
    return _finite(value)


def _minutes(value: Any) -> int | None:
    number = _finite(value)
    if number is None or number <= 0 or number != int(number):
        return None
    return int(number)


def epoch(value: Any) -> float | None:
    """Epoch seconds from a number or ISO 8601 text, or None."""
    number = _finite(value)
    if number is not None:
        return number
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            pass
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    return None


def _extra(values: Mapping[str, Any]) -> str | None:
    if not values:
        return None
    return json.dumps(values, sort_keys=True, default=str)


__all__ = ["Reading", "add_codex", "add_claude", "prune", "codex_readings",
           "claude_readings", "claude_window", "path", "open_store", "STEPS"]
