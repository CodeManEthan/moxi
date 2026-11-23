"""usage's rules for naming a limit window and saying what state its row is in.

These are the pieces the status line segment needs, kept apart from the readings store and
the limits picker so the segment loads them alone: this module imports only `math`.
`readings` and `limits` import every name here back, so callers find them where they
always have.

* A Claude rate-limit kind names a window: `claude_window(kind)` gives its
  `(limit_id, window_minutes)`.
* `label(minutes, limit_id, slot)` gives a row's label: `5h`, `7d` or `<n>m`, with the
  limit beside it when it is not a harness's main one.
* `row_state(used_percent, resets_at, now)` gives `fresh`, `reset` or `unavailable`, with
  a reason for the last two. Times are epoch seconds.
"""

from __future__ import annotations

import math

CLAUDE = "claude"
CODEX = "codex"

#: A Codex reading that names no limit id is the account's main limit, `codex`; Claude's
#: plan windows are limit `plan`.
CODEX_LIMIT = "codex"
PLAN_LIMIT = "plan"

FIVE_HOUR = 300
SEVEN_DAY = 10_080

#: Claude's rate-limit kinds whose window is known: kind -> (limit id, minutes).
#: `seven_day_<scope>` is limit `<scope>`, 7 days: a per-model weekly cap is a limit of
#: its own, not a second slot of the plan's.
CLAUDE_KINDS = {"five_hour": (PLAN_LIMIT, FIVE_HOUR), "seven_day": (PLAN_LIMIT, SEVEN_DAY)}
_SCOPED_WEEK = "seven_day_"

FRESH = "fresh"
RESET = "reset"
UNAVAILABLE = "unavailable"
ROW_STATES = (FRESH, RESET, UNAVAILABLE)

#: Each harness's main limit, whose 5h and 7d rows are always shown.
MAIN_LIMIT = {CLAUDE: PLAN_LIMIT, CODEX: CODEX_LIMIT}
#: Labels by duration; any other duration is labelled by its minutes.
LABELS = {FIVE_HOUR: "5h", SEVEN_DAY: "7d"}

NO_PERCENT = "the reading has no percent for this window"


def claude_window(kind: str) -> tuple[str, int | None]:
    """A Claude rate-limit kind's `(limit_id, window_minutes)`."""
    if kind in CLAUDE_KINDS:
        return CLAUDE_KINDS[kind]
    if kind.startswith(_SCOPED_WEEK) and len(kind) > len(_SCOPED_WEEK):
        return kind[len(_SCOPED_WEEK):], SEVEN_DAY
    return kind, None


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def percent(value: object) -> float | None:
    """A percentage as a float, or None. A bool is not a number."""
    return _finite(value)


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


def label(minutes: int | None, limit_id: str, slot: str | None = None) -> str:
    """A row's label: `5h`, `7d` or `<n>m` from its duration, with the limit beside it
    when it is not a main one; the kind when the duration is unknown."""
    if minutes is None:
        return slot or limit_id
    text = LABELS.get(minutes, f"{minutes}m")
    if limit_id not in MAIN_LIMIT.values():
        text = f"{text} {limit_id}"
    return text


__all__ = ["CLAUDE", "CODEX", "PLAN_LIMIT", "CODEX_LIMIT", "FIVE_HOUR", "SEVEN_DAY",
           "CLAUDE_KINDS", "FRESH", "RESET", "UNAVAILABLE", "ROW_STATES", "NO_PERCENT",
           "MAIN_LIMIT", "LABELS", "claude_window", "percent", "row_state", "label"]
