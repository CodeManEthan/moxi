"""The record seam's reads for a reader that walks back through the record.

`history(channel, before=)` keeps the ids below an id, and with a limit still takes the
tail, so `before` with `limit` reads the newest `limit` below an id. Both backends answer
the same way; the local record answers over a read-only connection too.
"""

from __future__ import annotations

import time

import pytest
from conftest import build_record

from moxi_comms.local_record import LocalRecord

DAY_S = 86_400
#: Midnight UTC, 2026-03-02: the first test day.
DAY_ONE = 1_772_409_600

LINE = "::to reviewer"


def stamp(at: float) -> str:
    """A time as the record writes it."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


class Clock:
    """The record's clock, set by the test."""

    def __init__(self, at: float = DAY_ONE):
        self.at = at

    def __call__(self) -> float:
        return self.at


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def record(backend, clock):
    opened = build_record(backend, clock)
    try:
        yield opened
    finally:
        if hasattr(opened, "close"):
            opened.close()


def post(record, room, text=f"{LINE}\nhello"):
    return record.post_as_identity("planner@proj", room, text).id


def ids_of(messages) -> list[str]:
    return [m.id for m in messages]


# --------------------------------------------------------------------------- history

def test_history_before_keeps_the_ids_below_it(record):
    room = record.create_channel("general")
    ids = [post(record, room) for _ in range(8)]
    assert ids_of(record.history(room, before=ids[5])) == ids[:5]
    assert ids_of(record.history(room, before=ids[0])) == []
    above_all = str(int(ids[-1]) + 1)
    assert ids_of(record.history(room, before=above_all)) == ids


def test_history_before_with_a_limit_reads_the_newest_below_it(record):
    room = record.create_channel("general")
    ids = [post(record, room) for _ in range(8)]
    assert ids_of(record.history(room, before=ids[5], limit=2)) == ids[3:5]
    assert ids_of(record.history(room, before=ids[2], limit=5)) == ids[:2]
    assert ids_of(record.history(room, before=ids[5], limit=0)) == []


def test_history_between_after_and_before(record):
    room = record.create_channel("general")
    ids = [post(record, room) for _ in range(8)]
    assert ids_of(record.history(room, after=ids[1], before=ids[6])) == ids[2:6]
    assert ids_of(record.history(room, after=ids[1], before=ids[6], limit=2)) == ids[4:6]
    assert ids_of(record.history(room, after=ids[4], before=ids[5])) == []


def test_history_before_reads_its_own_room(record):
    room = record.create_channel("general")
    other = record.create_channel("elsewhere")
    mine = post(record, room)
    theirs = post(record, other)
    last = post(record, room)
    assert ids_of(record.history(room, before=last)) == [mine]
    assert ids_of(record.history(other, before=last)) == [theirs]


def test_history_with_no_before_is_unchanged(record):
    room = record.create_channel("general")
    ids = [post(record, room) for _ in range(5)]
    assert ids_of(record.history(room)) == ids
    assert ids_of(record.history(room, limit=2)) == ids[-2:]
    assert ids_of(record.history(room, after=ids[2])) == ids[3:]


# --------------------------------------------------------------------------- read-only

def test_history_before_answers_over_a_read_only_connection(clock):
    writer = LocalRecord(clock=clock)
    try:
        room = writer.create_channel("general")
        ids = []
        for day in range(3):
            clock.at = DAY_ONE + day * DAY_S + 3600
            ids.append(post(writer, room))
            ids.append(post(writer, room, "prose"))
        wanted_history = ids_of(writer.history(room, before=ids[4], limit=2))
    finally:
        writer.close()

    reader = LocalRecord(read_only=True)
    try:
        assert reader._conn().execute("PRAGMA query_only").fetchone()[0] == 1
        assert ids_of(reader.history(room, before=ids[4], limit=2)) == wanted_history
        assert wanted_history == ids[2:4]
    finally:
        reader.close()
