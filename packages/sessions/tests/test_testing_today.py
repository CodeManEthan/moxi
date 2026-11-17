"""`today_at` keeps a test's today-records on today's local date and in their order."""

from __future__ import annotations

from datetime import datetime

from moxi_sessions.testing import today_at


def at(hour: int, minute: int, second: int = 0) -> float:
    return datetime(2026, 11, 15, hour, minute, second).timestamp()


def test_well_after_midnight_a_record_is_its_age_before_now():
    now = at(14, 30)
    assert today_at(600, oldest_s=4800, now=now) == now - 600
    assert today_at(4800, oldest_s=4800, now=now) == now - 4800


def test_just_after_midnight_every_record_is_today_in_its_order():
    now = at(0, 0, 20)
    times = [today_at(age, oldest_s=4800, now=now) for age in (4800, 2400, 600, 0)]
    assert times == sorted(times) and times[-1] == now
    assert all(datetime.fromtimestamp(t).date() == datetime.fromtimestamp(now).date() for t in times)
    # The oldest sits halfway between midnight and now.
    assert times[0] == at(0, 0, 10)


def test_an_oldest_record_that_would_be_yesterday_compresses_them_all():
    now = at(1, 0)
    assert today_at(3600, oldest_s=3600, now=now) == at(0, 30)
    assert today_at(1800, oldest_s=3600, now=now) == at(0, 45)
