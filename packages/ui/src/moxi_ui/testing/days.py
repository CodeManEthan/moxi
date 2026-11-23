"""Days a page words against today, all taken from one anchor.

A page groups and words dates by the local day it is when it draws them, while a test
seeds its data and writes the words it expects from its own reading of the clock. When
two of those readings fall on either side of midnight (a module constant taken at import
and a fixture's data seeded later, say), they disagree by a day. `Days` reads the clock
once, the first time it is asked, and gives every date from that anchor, so a module's
data and its expected words agree on which day is "two days ago" whenever each is made.

When that first reading falls within `margin_s` seconds before local midnight, it waits
midnight out first, so a module that takes less time than that sees the same today as
its page from start to end.

    DAYS = Days()

    def seed(root):
        write(root, at=DAYS.noon(2).timestamp())

    def test_the_day_is_a_group(page):
        expect(page.locator(f'section[data-group="{DAYS.key(2)}"]')).to_have_count(1)

The `ui_days` fixture gives each test module one.
"""

from __future__ import annotations

import threading
import time as clock_time
from collections.abc import Callable
from datetime import date, datetime, time, timedelta

#: Seconds before local midnight within which the anchor waits for the next day.
MARGIN_S = 120.0
#: Seconds the anchor waits past midnight, so the new day has surely begun.
PAST_MIDNIGHT_S = 1.0


class Days:
    """Dates `days_ago` days before one anchored today (see the module doc). `clock` and
    `sleep` stand in for `time.time` and `time.sleep`."""

    def __init__(self, *, margin_s: float = MARGIN_S,
                 clock: Callable[[], float] = clock_time.time,
                 sleep: Callable[[float], None] = clock_time.sleep):
        self.margin_s = margin_s
        self._clock = clock
        self._sleep = sleep
        self._now: datetime | None = None
        self._lock = threading.Lock()

    @property
    def now(self) -> datetime:
        """The anchor, local time without a zone: the clock at the first read, after
        waiting out a midnight less than `margin_s` seconds away."""
        with self._lock:
            if self._now is None:
                self._now = self._take()
            return self._now

    def _take(self) -> datetime:
        at = self._clock()
        today = datetime.fromtimestamp(at).date()
        midnight = datetime.combine(today + timedelta(days=1), time()).timestamp()
        if midnight - at <= self.margin_s:
            self._sleep(midnight - at + PAST_MIDNIGHT_S)
            at = self._clock()
        return datetime.fromtimestamp(at)

    def today(self) -> date:
        """The anchored local date."""
        return self.now.date()

    def day(self, days_ago: int) -> date:
        """The local date `days_ago` days before the anchored one."""
        return self.today() - timedelta(days=days_ago)

    def noon(self, days_ago: int) -> datetime:
        """Local noon of `day(days_ago)`, without a zone, as `datetime.now()` gives one."""
        return datetime.combine(self.day(days_ago), time(12))

    def key(self, days_ago: int) -> str:
        """`day(days_ago)` as `YYYY-MM-DD`."""
        return self.day(days_ago).isoformat()
