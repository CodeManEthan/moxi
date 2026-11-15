"""`today_at`, a seed time that stays on today's local date.

A test that writes a record meant to be today usually times it `now - age`. In the first
minutes after local midnight that lands on yesterday. `today_at` keeps it on today: it is
`now - age_s` while the oldest of the test's today-records, `oldest_s` before `now`, is
still today; otherwise every age is scaled by one factor, so the oldest lands halfway
between local midnight and `now` and the records keep their order.
"""

from __future__ import annotations

from datetime import datetime


def today_at(age_s: float, *, oldest_s: float, now: float) -> float:
    """The time `age_s` seconds before `now`, kept on `now`'s local date. `oldest_s` is
    the largest age of the records the test means to be today; give every one of them the
    same `oldest_s` and `now`."""
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if now - oldest_s > midnight:
        return now - age_s
    return now - age_s * (now - midnight) / (2 * oldest_s)
