"""Clock times memory prints for a person: one function, so every line shows them alike."""

import time


def show(ts: float) -> str:
    """`ts` (seconds since the epoch) in local time, to the minute: `YYYY-MM-DD HH:MM`."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
