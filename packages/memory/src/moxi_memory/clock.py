"""How memory shows a clock time to a person: `show(ts)`.

Every time memory prints for a person goes through `show`, never formatted inline, so the
whole package changes format in one place. Machine-readable output (`--json`, stored
times, the `--around` time handed to `moxi sessions read`) is ISO 8601 in UTC instead and
does not come through here.
"""

from __future__ import annotations

import time

#: Shown for a time that isn't known.
NONE = "-"


def show(ts: float | None) -> str:
    """Seconds since the epoch as local time to the minute, `YYYY-MM-DD HH:MM`; `-` for
    None."""
    if ts is None:
        return NONE
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


__all__ = ["show"]
