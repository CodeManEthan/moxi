"""moxi_ui.testing.days: one anchored today, and the wait past a midnight close by."""

from __future__ import annotations

from datetime import date, datetime

from moxi_ui.testing.days import MARGIN_S, PAST_MIDNIGHT_S, Days, wait_out_midnight


class Clock:
    """A clock that moves only when it is slept on."""

    def __init__(self, at: datetime):
        self.at = at.timestamp()
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.at += seconds


def test_the_anchor_is_read_once_and_every_day_comes_from_it():
    clock = Clock(datetime(2026, 11, 22, 14, 30))
    days = Days(clock=clock, sleep=clock.sleep)
    assert days.key(2) == "2026-11-20"
    clock.at += 86400  # a day later, the anchor stays where it was read
    assert days.today() == date(2026, 11, 22)
    assert days.noon(2) == datetime(2026, 11, 20, 12, 0)
    assert days.day(30) == date(2026, 10, 23)
    assert clock.slept == []


def test_close_before_midnight_the_anchor_waits_for_the_next_day():
    clock = Clock(datetime(2026, 11, 22, 23, 59, 0))
    days = Days(clock=clock, sleep=clock.sleep)
    assert days.today() == date(2026, 11, 23)
    assert clock.slept == [60 + PAST_MIDNIGHT_S]
    assert days.key(1) == "2026-11-22"


def test_further_from_midnight_than_the_margin_it_does_not_wait():
    clock = Clock(datetime(2026, 11, 22, 23, 50, 0))
    days = Days(clock=clock, sleep=clock.sleep)
    assert MARGIN_S < 600
    assert days.today() == date(2026, 11, 22) and clock.slept == []


def test_just_after_midnight_the_new_day_is_taken_at_once():
    clock = Clock(datetime(2026, 11, 23, 0, 0, 5))
    days = Days(clock=clock, sleep=clock.sleep, margin_s=600)
    assert days.today() == date(2026, 11, 23) and clock.slept == []


def test_a_step_waits_out_a_close_midnight_and_leaves_the_anchor_where_it_was():
    clock = Clock(datetime(2026, 11, 22, 23, 0, 0))
    days = Days(clock=clock, sleep=clock.sleep)
    assert days.today() == date(2026, 11, 22)
    clock.at = datetime(2026, 11, 22, 23, 59, 30).timestamp()
    after = days.wait_out_midnight()
    assert clock.slept == [30 + PAST_MIDNIGHT_S]
    assert datetime.fromtimestamp(after).date() == date(2026, 11, 23)
    assert days.today() == date(2026, 11, 22)
    assert wait_out_midnight(60, clock=clock, sleep=clock.sleep) == after
    assert len(clock.slept) == 1


def test_the_fixture_gives_one_days_to_the_module(ui_days, request):
    assert isinstance(ui_days, Days)
    assert request.getfixturevalue("ui_days") is ui_days
