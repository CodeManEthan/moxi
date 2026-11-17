"""The status line segment: Claude's plan limits, from the status line's own input.

**The segment shows status and triggers nothing.** It reads the input it is handed and
returns text. It reads no file, records nothing, starts no process and asks nothing.

`from_input(payload, now=None)` takes Claude's status line input as parsed JSON and reads
its `rate_limits`, a table of kinds (`five_hour`, `seven_day`, `spend_limit`), each
`{used_percentage, resets_at}` with `resets_at` in epoch seconds. usage's own rules turn
each kind into a row: `claude_window` gives its window, `label` its label (`5h`, `7d`,
`7d <scope>` for `seven_day_<scope>`, the kind itself when the window's length is not
known), and `row_state` its state at `now`:

* `fresh` shows the percent, `5h:42%`, rounded to a whole number;
* `reset` shows `5h:reset`;
* `unavailable` (no percent) is left out.

Rows join with one space, in the order the input gives them: `5h:42% 7d:61%`. A spend
limit's percent can pass 100 and is shown as given. Each row's band is `low` under 50%,
`warn` from 50% and `alert` from 80%, judged on the percent as shown; a reset row is
`low`. The result is None when the input has no `rate_limits` or no row is left.

`segment(session_id, now=None)` is None: usage keeps no record per session for the line,
so with no input there is nothing to show. It does not fall back to stored readings,
which would mean opening the readings store on every render.

This module imports only the standard library's `math` and `time` and usage's `windows`,
so a renderer can load it alone and quickly.
"""

from __future__ import annotations

import math
import time

from .windows import FRESH, RESET, _finite, claude_window, label, percent, row_state

LOW = "low"
WARN = "warn"
ALERT = "alert"
BANDS = (LOW, WARN, ALERT)

#: The percent, as shown, from which a row is `warn` and from which it is `alert`.
WARN_AT = 50
ALERT_AT = 80

#: The status line's colours: green, yellow, red.
COLOURS = {LOW: "\033[32m", WARN: "\033[33m", ALERT: "\033[31m"}
_END = "\033[0m"


class Segment:
    """What the segment shows: `rows`, each `(text, band)`; `text`, the rows joined by
    one space; `band`, the highest band of any row; `dim`, always False, since the input
    is Claude's own at this render."""

    __slots__ = ("rows", "text", "band", "dim")

    def __init__(self, rows: tuple[tuple[str, str], ...]) -> None:
        self.rows = rows
        self.text = " ".join(text for text, _ in rows)
        self.band = max((band for _, band in rows), key=BANDS.index)
        self.dim = False

    def __repr__(self) -> str:
        return f"Segment({self.text!r}, band={self.band!r})"

    def coloured(self) -> str:
        """Each row in its band's colour, joined by one space."""
        return " ".join(f"{COLOURS[band]}{text}{_END}" for text, band in self.rows)


def band(shown: int) -> str:
    """The band of a percent as shown."""
    if shown >= ALERT_AT:
        return ALERT
    if shown >= WARN_AT:
        return WARN
    return LOW


def whole(value: float) -> int:
    """A percent rounded to a whole number, halves up."""
    return math.floor(value + 0.5)


def row(kind: str, entry: object, now: float) -> tuple[str, str] | None:
    """One kind's row, `(text, band)`, or None when it is left out."""
    if not isinstance(entry, dict):
        return None
    used = percent(entry.get("used_percentage"))
    resets_at = _finite(entry.get("resets_at"))
    limit_id, minutes = claude_window(kind)
    name = label(minutes, limit_id, kind)
    state, _ = row_state(used, resets_at, now)
    if state == FRESH:
        shown = whole(used)
        return f"{name}:{shown}%", band(shown)
    if state == RESET:
        return f"{name}:reset", LOW
    return None


def from_input(payload: object, now: float | None = None) -> Segment | None:
    """The segment Claude's status line input shows at `now` (module doc)."""
    if not isinstance(payload, dict):
        return None
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return None
    at = time.time() if now is None else now
    rows = tuple(found for kind, entry in limits.items()
                 if isinstance(kind, str) and kind
                 and (found := row(kind, entry, at)) is not None)
    return Segment(rows) if rows else None


def segment(session_id: object, now: float | None = None) -> None:
    """None: the limits come only from the status line's input (`from_input`)."""
    return None


__all__ = ["Segment", "from_input", "segment", "row", "band", "whole", "BANDS", "LOW",
           "WARN", "ALERT", "WARN_AT", "ALERT_AT"]
