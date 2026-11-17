"""The one place statusline turns a moment into clock text.

Every date or time of day the module writes or shows goes through `stamp`, so a later
time-zone setting changes them all here.
"""

import time

__all__ = ["stamp"]


def stamp(when: float | None = None) -> str:
    """`when` (default now), in seconds since the epoch, as UTC ISO 8601 to the second:
    `2026-11-08T14:03:09Z`."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if when is None
                                                           else when))
