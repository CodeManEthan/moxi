"""Limits: how much of each plan window is used, when it resets, and when it fills.

`limits(*, harness=None, now=None, pull=True)` answers from the readings store
(`moxi_usage.readings`), after bringing it up to date:

* **Codex, between passes**: the newest rollout by mtime is read backward for the
  readings written since the store's newest one from it.
* **Codex, a pull**: when the newest Codex reading is older than `limits.codex_pull_after`
  seconds and turns is installed, turns' `codex_rate_limits(timeout=3)` asks a live Codex
  host or a short-lived app-server for one more. A pull that fails, or turns absent,
  leaves the stored readings in place, and each Codex row older than the threshold says
  why no newer reading came. A failed pull's time is kept in the store's `meta`, and no
  pull is tried again until `limits.codex_pull_after` has passed since it.
* **Claude** readings come only from turns' `usage` event (`moxi_usage.listeners`), so
  with turns absent Claude's rows are `unavailable`, "turns is not installed".

Then one row per window, a window being `(harness, limit_id, window_minutes)`:

* **Picking.** Claude: among readings with a future `resets_at` and a known percent, the
  latest `resets_at` wins, on a tie the highest percent, so an idle session's old number
  never passes for fresh; with none, the newest reading's state. Codex: the newest
  `observed_at`, from either source. A reading in another slot never moves a window's
  row: a 7-day reading in `primary` is the 7-day row's, and the 5-hour row keeps its own.
* **States.** `fresh`, `reset` or `unavailable`, the last two with a reason: no
  `resets_at` is `reset` (the harness reports none just after a five-hour reset), `now`
  at or past `resets_at` is `reset`, no percent is `unavailable` (a reading with neither,
  such as a spend limit in dollars, too).
* **Projection.** For a `fresh` row with at least 3 readings of its window's current
  period in the last `limits.projection_minutes`, a least-squares line of percent over
  time gives `full_at`, the time the window reaches 100%. A projection never fits across
  a reset: a reading is in the winner's period when its `resets_at` lies within half the
  window's duration of the winner's. `full_at` is given only when it falls before the
  reset, else None with "resets first".

Readings older than `limits.history_days` are pruned here when the last prune is over an
hour old. Nothing here reads the clock but `limits()` with no `now`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from moxi_sessions.api import UNKNOWN

from . import readings as store
from .readings import CLAUDE, CODEX, FIVE_HOUR, SEVEN_DAY, Reading

if TYPE_CHECKING:
    import sqlite3

log = logging.getLogger("moxi.usage")

FRESH = "fresh"
RESET = "reset"
UNAVAILABLE = "unavailable"
ROW_STATES = (FRESH, RESET, UNAVAILABLE)

HARNESSES = (CLAUDE, CODEX)
#: Each harness's main limit, whose 5h and 7d rows are always shown.
MAIN_LIMIT = {CLAUDE: store.PLAN_LIMIT, CODEX: store.CODEX_LIMIT}
#: Labels by duration; any other duration is labelled by its minutes.
LABELS = {FIVE_HOUR: "5h", SEVEN_DAY: "7d"}

#: Seconds turns' Codex pull may take.
PULL_TIMEOUT = 3.0
#: Seconds between prunes made by `limits()`.
PRUNE_EVERY = 3600.0
#: Readings a projection needs.
MIN_FIT = 3
#: Where a key splits into words: an underscore, a hyphen, or a lower-case letter
#: followed by a capital.
_WORDS = re.compile(r"[_-]|(?<=[a-z])(?=[A-Z])")
#: Slack, in seconds, between a rollout's mtime and the time on its newest reading.
_MTIME_SLACK = 60.0

NO_TURNS = "turns is not installed"
NO_PERCENT = "the reading has no percent for this window"


# --- results --------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True, slots=True)
class Row:
    """One window, in one state (`fresh`, `reset` or `unavailable`, with `reason` for the
    last two), from its winning reading.

    What the reading gives (`window_minutes`, `slot`, `used_percent`, `resets_at`,
    `observed_at` in epoch seconds, `age_seconds`, `source`, `session_id`, `plan_type`)
    is `UNKNOWN` where the reading did not say, and all of it where no reading was found;
    the state and `reason` say which. `age_seconds` is `now - observed_at`. `full_at` is
    the projection's time the window reaches 100%, None with `projection` saying why
    there is none; `fit` counts the readings it fitted. `reason` and `note` are None when
    there is nothing to say; `note` says why no newer reading came when one was wanted.
    `extra` holds what else the reading said (a spend limit's dollars, say);
    `dollars(extra)` picks the dollar figures out of it.
    """

    harness: str
    limit_id: str
    window_minutes: Any = UNKNOWN
    label: str
    state: str
    reason: str | None = None
    slot: Any = UNKNOWN
    used_percent: Any = UNKNOWN
    resets_at: Any = UNKNOWN
    observed_at: Any = UNKNOWN
    age_seconds: Any = UNKNOWN
    source: Any = UNKNOWN
    session_id: Any = UNKNOWN
    plan_type: Any = UNKNOWN
    full_at: float | None = None
    projection: str | None = None
    fit: int = 0
    note: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def window(self) -> tuple[str, str, Any]:
        return (self.harness, self.limit_id, self.window_minutes)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready: times as ISO 8601 UTC, and an `UNKNOWN` value left out and named
        in `"unknown"`."""
        out: dict[str, Any] = {
            "harness": self.harness, "limit_id": self.limit_id,
            "window_minutes": self.window_minutes, "label": self.label,
            "state": self.state, "reason": self.reason, "slot": self.slot,
            "used_percent": self.used_percent, "resets_at": iso(self.resets_at),
            "observed_at": iso(self.observed_at), "age_seconds": self.age_seconds,
            "source": self.source, "session_id": self.session_id,
            "plan_type": self.plan_type, "full_at": iso(self.full_at),
            "projection": self.projection, "fit": self.fit, "note": self.note,
            "extra": dict(self.extra),
        }
        unknown = [name for name, value in out.items() if value is UNKNOWN]
        for name in unknown:
            del out[name]
        if unknown:
            out["unknown"] = unknown
        return out


@dataclass(frozen=True, kw_only=True, slots=True)
class Pull:
    """What `limits()` did about a stale Codex reading: `tried` the pull or not (turns
    absent), `ok` when it brought a reading, `added` new rows, `via` turns' route
    (`host` or `app-server`), and `reason` when it brought none."""

    tried: bool
    ok: bool
    reason: str | None = None
    via: str | None = None
    added: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"tried": self.tried, "ok": self.ok, "reason": self.reason, "via": self.via,
                "added": self.added}


@dataclass(frozen=True, kw_only=True, slots=True)
class Limits:
    """`limits()`'s answer: the rows at `now`, Claude's then Codex's; the Codex pull
    when one was wanted; and `notes`, the problems met on the way."""

    now: float
    rows: tuple[Row, ...]
    pull: Pull | None = None
    notes: tuple[str, ...] = ()

    def of(self, harness: str) -> list[Row]:
        """The rows of one harness."""
        return [row for row in self.rows if row.harness == harness]

    def as_dict(self) -> dict[str, Any]:
        return {"now": iso(self.now), "rows": [row.as_dict() for row in self.rows],
                "pull": None if self.pull is None else self.pull.as_dict(),
                "notes": list(self.notes)}


# --- the entry point ------------------------------------------------------------------

def limits(*, harness: str | None = None, now: float | None = None, pull: bool = True,
           env: Mapping[str, str] | None = None) -> Limits:
    """The limit rows of `harness` (`claude`, `codex`, or None for both) at `now`.

    Reads the newest Codex rollout, pulls through turns when `pull` and the newest Codex
    reading is stale, prunes when due, and picks a row per window (module doc). Raises
    ValueError for another harness, and `moxi.config.ConfigError` when usage's config
    cannot be read.
    """
    if harness is not None and harness not in HARNESSES:
        raise ValueError(f"{harness!r} is not a harness with limits; use claude or codex")
    from .settings import current

    now = time.time() if now is None else float(now)
    settings = current(env)
    days = float(settings["limits.history_days"])
    wanted = HARNESSES if harness is None else (harness,)
    notes: list[str] = []
    pulled: Pull | None = None
    conn = store.open_store(env)
    try:
        done = store.last_prune(conn)
        if done is None or now - done >= PRUNE_EVERY:
            store.prune_store(conn, now=now, days=days)
        if CODEX in wanted:
            notes.extend(_read_rollout(conn, now=now, days=days, env=env))
            if pull:
                pulled = _maybe_pull(conn, now=now, days=days,
                                     after=float(settings["limits.codex_pull_after"]))
        found = _stored(conn, wanted, store.cutoff(now, days))
    finally:
        conn.close()
    turns = _turns_available()
    rows: list[Row] = []
    for name in wanted:
        if name == CLAUDE and not turns:
            rows.extend(_missing(CLAUDE, minutes, NO_TURNS)
                        for minutes in (FIVE_HOUR, SEVEN_DAY))
            continue
        rows.extend(harness_rows(name, found.get(name, []), now=now, days=days,
                                 projection_minutes=float(
                                     settings["limits.projection_minutes"])))
    if pulled is not None and not pulled.ok:
        after = float(settings["limits.codex_pull_after"])
        rows = [_with_note(row, now, after, pulled.reason) for row in rows]
    return Limits(now=now, rows=tuple(rows), pull=pulled, notes=tuple(notes))


def harness_rows(harness: str, found: list[Reading], *, now: float, days: float,
                 projection_minutes: float) -> list[Row]:
    """One harness's rows from its stored readings: one per window, the main limit's 5h
    and 7d always among them, ordered by limit (the main one first) then duration."""
    by_window: dict[tuple, list[Reading]] = {}
    for reading in found:
        by_window.setdefault(reading.window, []).append(reading)
    rows = [window_row(harness, group, now=now, projection_minutes=projection_minutes)
            for group in by_window.values()]
    main = MAIN_LIMIT[harness]
    for minutes in (FIVE_HOUR, SEVEN_DAY):
        if (harness, main, minutes) not in by_window:
            rows.append(_missing(harness, minutes, _no_reading(harness, days)))
    rows.sort(key=lambda r: (r.limit_id != main, r.limit_id, r.window_minutes is UNKNOWN,
                             0 if r.window_minutes is UNKNOWN else r.window_minutes))
    return rows


def window_row(harness: str, found: list[Reading], *, now: float,
               projection_minutes: float) -> Row:
    """The row of one window from its readings (all of one window, at least one)."""
    winner = pick(harness, found, now)
    state, reason = row_state(winner.used_percent, winner.resets_at, now)
    full_at = None
    projection = None
    fit = 0
    if state == FRESH:
        full_at, projection, fit = project(found, winner, now=now,
                                           minutes=projection_minutes)
    return Row(harness=harness, limit_id=winner.limit_id,
               window_minutes=_said(winner.window_minutes),
               label=label(winner.window_minutes, winner.limit_id, winner.slot),
               state=state, reason=reason, slot=_said(winner.slot),
               used_percent=_said(winner.used_percent), resets_at=_said(winner.resets_at),
               observed_at=winner.observed_at, age_seconds=now - winner.observed_at,
               source=winner.source, session_id=_said(winner.session_id),
               plan_type=_said(winner.plan_type), full_at=full_at, projection=projection,
               fit=fit, extra=_load_extra(winner.extra))


def _said(value: Any) -> Any:
    """A stored reading's value, `UNKNOWN` where the store holds NULL (not said)."""
    return UNKNOWN if value is None else value


# --- picking, states, projection ------------------------------------------------------

def pick(harness: str, found: list[Reading], now: float) -> Reading:
    """The winning reading of one window.

    Claude: among readings with a future `resets_at` and a known percent, the latest
    `resets_at`, then the highest percent; with none, the newest reading. Codex: the
    newest `observed_at`, then the highest percent.
    """
    if harness == CLAUDE:
        candidates = [r for r in found if r.resets_at is not None and r.resets_at > now
                      and r.used_percent is not None]
        if candidates:
            return max(candidates, key=lambda r: (r.resets_at, r.used_percent,
                                                  r.observed_at))
    return max(found, key=lambda r: (r.observed_at, -1.0 if r.used_percent is None
                                     else r.used_percent))


def row_state(used_percent: float | None, resets_at: float | None,
              now: float) -> tuple[str, str | None]:
    """A window's state and its reason.

    No `resets_at` is `reset`: a harness reports none for a while after a five-hour
    reset. `now` at or past `resets_at` is `reset`. No percent is `unavailable`, and so
    is a reading with neither, such as a spend limit that reports only dollars.
    """
    if resets_at is None and used_percent is None:
        return UNAVAILABLE, NO_PERCENT
    if resets_at is None:
        return RESET, "the reading has no reset time"
    if now >= resets_at:
        return RESET, "the window has reset since the reading"
    if used_percent is None:
        return UNAVAILABLE, NO_PERCENT
    return FRESH, None


def project(found: list[Reading], winner: Reading, *, now: float,
            minutes: float) -> tuple[float | None, str | None, int]:
    """`(full_at, why_none, readings_fitted)` for one window's winning reading.

    Fits the readings of the winner's period (`resets_at` within half the window of the
    winner's) observed in the last `minutes` minutes, by least squares of percent over
    time. Needs at least `MIN_FIT` of them, at two times or more, and a rising line.
    `full_at` is given only when it falls after `now` and before the winner's reset.
    """
    if winner.window_minutes is None:
        return None, "the window's duration is unknown", 0
    if winner.resets_at is None:
        return None, "the reading has no reset time", 0
    half = winner.window_minutes * 60 / 2
    since = now - minutes * 60
    points = sorted({(r.observed_at, r.used_percent) for r in found
                     if r.observed_at >= since and r.used_percent is not None
                     and r.resets_at is not None
                     and abs(r.resets_at - winner.resets_at) <= half})
    count = len(points)
    if count < MIN_FIT:
        return None, (f"{count} reading{'s' if count != 1 else ''} of this period in the "
                      f"last {minutes:g} minutes; a projection needs {MIN_FIT}"), count
    mean_t = sum(t for t, _ in points) / count
    mean_p = sum(p for _, p in points) / count
    spread = sum((t - mean_t) ** 2 for t, _ in points)
    if spread == 0:
        return None, "the readings are all from one moment", count
    slope = sum((t - mean_t) * (p - mean_p) for t, p in points) / spread
    if slope <= 0:
        return None, "not rising", count
    full_at = mean_t + (100.0 - mean_p) / slope
    if full_at <= now:
        # The line through older readings crossed 100% already while the window has not
        # filled: they no longer say when it will.
        return None, "the fit reaches 100% before now", count
    if full_at >= winner.resets_at:
        return None, "resets first", count
    return full_at, None, count


def label(minutes: int | None, limit_id: str, slot: str | None = None) -> str:
    """A row's label: `5h`, `7d` or `<n>m` from its duration, with the limit beside it
    when it is not a main one; the kind when the duration is unknown."""
    if minutes is None:
        return slot or limit_id
    text = LABELS.get(minutes, f"{minutes}m")
    if limit_id not in MAIN_LIMIT.values():
        text = f"{text} {limit_id}"
    return text


# --- bringing Codex up to date --------------------------------------------------------

def _read_rollout(conn: sqlite3.Connection, *, now: float, days: float,
                  env: Mapping[str, str] | None) -> list[str]:
    """Store the readings of the newest rollout by mtime written since the store's newest
    reading from it. Returns the problems met, in words."""
    from moxi_sessions import api as sessions

    oldest = store.cutoff(now, days)
    newest = conn.execute("SELECT max(observed_at) FROM readings WHERE harness = ? AND "
                          "source = ?", (CODEX, store.ROLLOUT)).fetchone()[0]
    since = oldest if newest is None else max(oldest, newest - _MTIME_SLACK)
    root = None if env is None else sessions.root(CODEX, env)
    try:
        listing = sessions.sources(CODEX, root=root, modified_since=since, heads=False)
    except Exception as exc:  # noqa: BLE001 - a listing problem is a note, not a failure
        return [f"Codex rollouts not listed: {exc}"]
    if not listing.ok:
        if not os.path.lexists(listing.root):
            return []                     # no Codex here: its rows say there is no reading
        return [f"Codex rollouts not listed: {listing.reason}"]
    entries = [entry for entry in listing if not entry.compressed]
    if not entries:
        return []
    entry = max(entries, key=lambda e: (e.mtime_ns or 0, e.rel_path or ""))
    held = conn.execute("SELECT max(observed_at) FROM readings WHERE harness = ? AND "
                        "source = ? AND session_id = ?",
                        (CODEX, store.ROLLOUT, entry.source.session_id)).fetchone()[0]
    stop = oldest if held is None else max(oldest, held)
    found: list[Reading] = []
    try:
        with sessions.records_back(entry, kinds={"limits"}) as back:
            for record in back:
                got = store.codex_readings(record)
                if got and got[0].observed_at <= stop:
                    break
                found.extend(got)
    except sessions.Unreadable as exc:
        return [f"the newest Codex rollout could not be read: {exc.reason}"]
    store.store(conn, found, now=now, days=days)
    return []


def _maybe_pull(conn: sqlite3.Connection, *, now: float, days: float,
                after: float) -> Pull | None:
    """Pull a Codex reading through turns when the newest stored one is older than
    `after` seconds, or there is none. None when no pull was wanted. A pull that was
    tried and brought no reading is remembered, and the next waits `after` seconds from
    it, so a call never waits out a pull that just failed."""
    newest = conn.execute("SELECT max(observed_at) FROM readings WHERE harness = ?",
                          (CODEX,)).fetchone()[0]
    if newest is not None and now - newest < after:
        return None
    failed = store.last_failed_pull(conn)
    if failed is not None and 0 <= now - failed < after:
        return Pull(tried=False, ok=False,
                    reason=f"the last pull, {_ago(now - failed)} ago, brought no reading; "
                           "the next waits for limits.codex_pull_after")
    pulled = _pull(conn, now=now, days=days)
    if pulled.tried:
        store.note_pull(conn, now=now, failed=not pulled.ok)
    return pulled


def _ago(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds} s" if seconds < 120 else f"{seconds // 60} min"


def _pull(conn: sqlite3.Connection, *, now: float, days: float) -> Pull:
    fn, why = _puller()
    if fn is None:
        return Pull(tried=False, ok=False, reason=why)
    try:
        got = fn(timeout=PULL_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - a failed pull leaves the stored readings
        return Pull(tried=True, ok=False, reason=f"the pull failed: {_describe(exc)}")
    if got is None:
        return Pull(tried=True, ok=False,
                    reason="turns found no Codex host or app-server to ask, or it did "
                           "not answer in time")
    from moxi_sessions.api import codex

    via = getattr(got, "via", None)
    try:
        records = codex.limits_from_app_server(got.payload, float(got.received_at))
    except (AttributeError, TypeError, ValueError) as exc:
        return Pull(tried=True, ok=False, via=via,
                    reason=f"the pulled reading could not be read: {_describe(exc)}")
    found = [r for record in records for r in store.codex_readings(record)]
    if not found:
        return Pull(tried=True, ok=False, via=via,
                    reason="the pulled reading held no window with a duration")
    added = store.store(conn, found, now=now, days=days)
    return Pull(tried=True, ok=True, via=via, added=added)


def _puller() -> tuple[Callable[..., Any] | None, str | None]:
    """turns' Codex pull, or None with the reason it cannot be had."""
    from moxi import modules

    if not modules.available("turns"):
        return None, NO_TURNS
    found = modules.load("turns")
    if not found.ok or found.api is None:
        return None, f"turns could not be loaded: {found.reason or found.state}"
    fn = getattr(found.api, "codex_rate_limits", None)
    if not callable(fn):
        return None, "the installed turns offers no Codex limits pull"
    return fn, None


def _turns_available() -> bool:
    from moxi import modules

    return modules.available("turns")


# --- small pieces ---------------------------------------------------------------------

def _stored(conn: sqlite3.Connection, wanted: tuple[str, ...],
            oldest: float) -> dict[str, list[Reading]]:
    marks = ", ".join("?" * len(wanted))
    out: dict[str, list[Reading]] = {}
    for row in conn.execute(
            f"SELECT {', '.join(Reading._fields)} FROM readings "
            f"WHERE harness IN ({marks}) AND observed_at >= ?", (*wanted, oldest)):
        reading = Reading(*row)
        out.setdefault(reading.harness, []).append(reading)
    return out


def _missing(harness: str, minutes: int, reason: str) -> Row:
    limit_id = MAIN_LIMIT[harness]
    return Row(harness=harness, limit_id=limit_id, window_minutes=minutes,
               label=label(minutes, limit_id), state=UNAVAILABLE, reason=reason)


def _no_reading(harness: str, days: float) -> str:
    if harness == CLAUDE:
        return f"no Claude session has reported this window in the last {days:g} days"
    return f"no Codex reading of this window in the last {days:g} days"


def _with_note(row: Row, now: float, after: float, why: str | None) -> Row:
    from dataclasses import replace

    if row.harness != CODEX:
        return row
    if row.observed_at is not UNKNOWN and now - row.observed_at < after:
        return row
    return replace(row, note=f"no newer reading: {why}")


def dollars(extra: Mapping[str, Any]) -> list[tuple[str, float]]:
    """The dollar fields of a reading's `extra`, in the order written: each number whose
    key names dollars (`usedDollars`, `limit_usd`), at the top or one table down (a
    nested key is named `outer inner`). Everything else a harness sends beside a window
    (Codex's `credits`, an empty `individual_limit`) is kept in `extra` but is not a
    dollar figure."""
    found: list[tuple[str, float]] = []

    def take(name: str, key: str, value: Any) -> None:
        if (_names_dollars(key) and isinstance(value, (int, float))
                and not isinstance(value, bool)):
            found.append((name, float(value)))

    for key, value in extra.items():
        if isinstance(value, Mapping):
            for inner, deeper in value.items():
                take(f"{key} {inner}", str(inner), deeper)
        else:
            take(str(key), str(key), value)
    return found


def _names_dollars(key: str) -> bool:
    """True when `key` names a dollar figure: a word of it is dollar(s) or usd."""
    return any(word.lower() in ("dollar", "dollars", "usd") for word in _WORDS.split(key))


def _load_extra(text: str | None) -> Mapping[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def iso(epoch: Any) -> Any:
    """Epoch seconds as ISO 8601 UTC; None and `UNKNOWN` as they are."""
    if epoch is None or epoch is UNKNOWN:
        return epoch
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["limits", "Limits", "Row", "Pull", "pick", "row_state", "project", "label",
           "dollars", "harness_rows", "window_row", "FRESH", "RESET", "UNAVAILABLE"]
