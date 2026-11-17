"""The record seam's two reads for a reader that walks back through the record.

`history(channel, before=)` keeps the ids below an id, and with a limit still takes the
tail, so `before` with `limit` reads the newest `limit` below an id. `count_before(channel,
timestamp)` counts the messages before a time whose line zero leads with the machine
line's sigil, with the oldest one's time and the newest one's time and id. Both
backends answer both the same way; the local record answers them over a read-only
connection too.
"""

from __future__ import annotations

import time

import pytest
from conftest import build_record

from moxi_comms.local_record import LocalRecord
from moxi_comms.machine_line import leads_with_sigil, parse

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


# --------------------------------------------------------------------------- count_before

def test_count_before_in_an_empty_room(record):
    room = record.create_channel("general")
    assert record.count_before(room, stamp(DAY_ONE + DAY_S)) == (0, None, None, None)


def test_count_before_one_message(record, clock):
    room = record.create_channel("general")
    clock.at = DAY_ONE + 3600
    only = post(record, room)
    assert record.count_before(room, stamp(DAY_ONE + 3601)) == (
        1, stamp(DAY_ONE + 3600), stamp(DAY_ONE + 3600), only)
    assert record.count_before(room, stamp(DAY_ONE + 3600)) == (0, None, None, None), (
        "a message at the time itself is not before it")


def test_count_before_across_days_gives_the_oldest_and_the_newest(record, clock):
    room = record.create_channel("general")
    ids = {}
    for day in range(3):
        for hour in (9, 17):
            clock.at = DAY_ONE + day * DAY_S + hour * 3600
            ids[day, hour] = post(record, room)
    clock.at = DAY_ONE + 3 * DAY_S + 8 * 3600
    post(record, room)                                 # the day the count stops at

    third_day = stamp(DAY_ONE + 2 * DAY_S)
    count, first, last, last_id = record.count_before(room, third_day)
    assert count == 4
    assert first == stamp(DAY_ONE + 9 * 3600)
    assert last == stamp(DAY_ONE + DAY_S + 17 * 3600)
    assert last_id == ids[1, 17]

    fourth_day = stamp(DAY_ONE + 3 * DAY_S)
    assert record.count_before(room, fourth_day)[0] == 6
    assert record.count_before(room, fourth_day)[3] == ids[2, 17]


def test_count_before_counts_only_messages_whose_line_zero_leads_with_the_sigil(record):
    room = record.create_channel("general")
    counted = [post(record, room, text) for text in (
        f"{LINE}\nwell formed",
        f"  \t{LINE}\nspaces and a tab first",
        "::TO reviewer\nthe sigil in capitals",
        "::to\nthe sigil and nothing after: malformed, still counted",
    )]
    left_out = [post(record, room, text) for text in (
        "prose, with no machine line",
        f"prose first\n{LINE}\nthe sigil on line one",
        f"\n{LINE}\nan empty line zero",
        f"> {LINE}\nquoted",
    )]
    assert left_out
    count, _, _, last_id = record.count_before(room, stamp(DAY_ONE + 1))
    assert count == len(counted)
    assert last_id == counted[-1], "the newest counted message, not the newest prose"


def test_count_before_reads_its_own_room(record):
    room = record.create_channel("general")
    other = record.create_channel("elsewhere")
    post(record, room)
    post(record, other)
    post(record, other)
    assert record.count_before(room, stamp(DAY_ONE + 1))[0] == 1
    assert record.count_before(other, stamp(DAY_ONE + 1))[0] == 2


def test_the_newest_time_and_id_are_one_messages_when_the_clock_steps_back(record, clock):
    """Ids rise in post order and times follow the clock; when the clock steps back the
    two disagree, and the newest is the newest by id, with its own time."""
    room = record.create_channel("general")
    clock.at = DAY_ONE + 7200
    first = post(record, room)
    clock.at = DAY_ONE + 3600
    second = post(record, room)
    assert record.count_before(room, stamp(DAY_ONE + DAY_S)) == (
        2, stamp(DAY_ONE + 7200), stamp(DAY_ONE + 3600), second)
    assert int(second) > int(first)


SIGIL_CASES = (
    "::to reviewer\nbody",
    "::to reviewer",
    "  ::to reviewer",
    "\t::to reviewer",
    "::To reviewer",
    "::tox reviewer",
    "::to",
    "",
    "plain prose",
    "\n::to reviewer",
    " :: to reviewer",
    "x::to reviewer",
    " ::to reviewer",
    "::t",
)


def test_the_sigil_test_is_the_parsers_leading_one():
    """A body the parser reads as a machine line always leads with the sigil."""
    for body in SIGIL_CASES:
        if parse(body) is not None:
            assert leads_with_sigil(body), body


def test_the_local_count_and_the_python_test_agree_on_every_case(tmp_path):
    """The local record's SQL test and the mock's Python one, body by body."""
    local = LocalRecord(tmp_path / "record.db", clock=lambda: DAY_ONE)
    try:
        for n, body in enumerate(SIGIL_CASES):
            room = local.create_channel(f"room-{n}")
            local.post_as_identity("planner@proj", room, body or " ")
            counted = local.count_before(room, stamp(DAY_ONE + 1))[0]
            assert counted == int(leads_with_sigil(body or " ")), repr(body)
    finally:
        local.close()


# --------------------------------------------------------------------------- read-only

def test_both_reads_answer_over_a_read_only_connection(clock):
    writer = LocalRecord(clock=clock)
    try:
        room = writer.create_channel("general")
        ids = []
        for day in range(3):
            clock.at = DAY_ONE + day * DAY_S + 3600
            ids.append(post(writer, room))
            ids.append(post(writer, room, "prose"))
        wanted_history = ids_of(writer.history(room, before=ids[4], limit=2))
        wanted_count = writer.count_before(room, stamp(DAY_ONE + 2 * DAY_S))
    finally:
        writer.close()

    reader = LocalRecord(read_only=True)
    try:
        assert reader._conn().execute("PRAGMA query_only").fetchone()[0] == 1
        assert ids_of(reader.history(room, before=ids[4], limit=2)) == wanted_history
        assert wanted_history == ids[2:4]
        assert reader.count_before(room, stamp(DAY_ONE + 2 * DAY_S)) == wanted_count
        assert wanted_count == (2, stamp(DAY_ONE + 3600), stamp(DAY_ONE + DAY_S + 3600),
                                ids[2])
    finally:
        reader.close()
