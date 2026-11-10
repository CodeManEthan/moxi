"""The list over roots the test kit writes: filters and their counts, the groups, the next
rows of each, the folders, one session opened, and the resume commands."""

from __future__ import annotations

import calendar
import dataclasses
import os
import time

import pytest

from moxi_sessions import resume as resume_module
from moxi_sessions.catalog import Catalog, Gathered
from moxi_sessions.lists import (DAY_ROWS, EARLIER, FOUND, MORE_ROWS, RECENT, RECENT_ROWS,
                                 SUBAGENTS_MAX, UNDATED, list_folders, list_sessions,
                                 local_day, more_sessions, newest_sessions, open_session,
                                 parse_position, position)
from moxi_sessions.resume import resume
from moxi_sessions.shape import CLAUDE, CODEX, OPENCODE
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb

HOUR = 3600.0
DAY = 24 * HOUR


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    return float(calendar.timegm((year, month, day, hour, minute, 0)))


#: The time every list here is cut at: a Tuesday afternoon.
NOW = utc(2026, 11, 10, 15)


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


def tid(n: int) -> str:
    return f"0190c000-0000-7000-8000-{n:012x}"


def set_zone(zone: str | None) -> None:
    if zone is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = zone
    time.tzset()


@pytest.fixture(autouse=True)
def zone():
    """Every test runs in UTC unless it sets a zone; the process's own comes back
    after."""
    before = os.environ.get("TZ")
    set_zone("UTC")
    yield set_zone
    set_zone(before)


@pytest.fixture
def roots(tmp_path):
    """A root per harness, each empty: a projects root, a sessions root and a database."""
    projects = tmp_path / "projects"
    rollouts = tmp_path / "codex" / "sessions"
    projects.mkdir()
    rollouts.mkdir(parents=True)
    database = OpenCodeDb(tmp_path / "opencode").write()
    return {CLAUDE: projects, CODEX: rollouts, OPENCODE: database}


def changed_at(path, at: float) -> None:
    stamp = int(at * 1_000_000_000)
    os.utime(path, ns=(stamp, stamp))


def claude(roots, n: int, at: float, *, program: bool = False, cwd: str = "/work/proj-a",
           project: str = "-work-proj-a", session: str | None = None) -> ClaudeTranscript:
    """A Claude session that last changed at `at`: a person's, or one a program ran."""
    t = ClaudeTranscript(roots[CLAUDE], session_id=session or sid(n), cwd=cwd,
                         project=project, entrypoint="sdk-cli" if program else "cli")
    t.user("Do the thing.", origin=None if program else "human")
    t.assistant("Done.")
    changed_at(t.write(), at)
    return t


def codex(roots, n: int, at: float, *, cwd: str = "/work/proj-a") -> CodexRollout:
    r = CodexRollout(roots[CODEX], thread_id=tid(n), cwd=cwd)
    r.turn()
    r.user("Do the thing.")
    r.assistant("Done.")
    changed_at(r.write(), at)
    return r


def catalog(roots) -> Catalog:
    return Catalog(roots, reuse_s=0)


def listed(roots, **kw):
    return list_sessions(catalog(roots), recent_s=HOUR, now=NOW, **kw)


def group(answer, key):
    return next(g for g in answer["groups"] if g["key"] == key)


def keys(answer) -> list[str]:
    return [g["key"] for g in answer["groups"]]


def ids(rows) -> list[str]:
    return [row["session"] for row in rows]


# --- filters and counts --------------------------------------------------------------------

def shared_directory(roots):
    """Two folders whose sessions sit in one Claude project directory, and a Codex
    session in the first folder."""
    claude(roots, 1, NOW - 10, cwd="/work/proj-a", project="-work-proj")
    claude(roots, 2, NOW - 2 * DAY, cwd="/work/proj-a", project="-work-proj")
    claude(roots, 3, NOW - 20, cwd="/work/proj-b", project="-work-proj")
    codex(roots, 4, NOW - 3 * DAY, cwd="/work/proj-a")


def test_a_folder_counts_its_own_sessions_where_two_folders_share_a_directory(roots):
    shared_directory(roots)

    everything = listed(roots)
    one = listed(roots, folder="/work/proj-a")
    other = listed(roots, folder="/work/proj-b")

    assert (everything["all"], everything["total"]) == (4, 4)
    assert (one["all"], one["total"]) == (4, 3)
    assert one["harnesses"] == [{"harness": CLAUDE, "count": 2},
                                {"harness": CODEX, "count": 1},
                                {"harness": OPENCODE, "count": 0}]
    assert (other["all"], other["total"]) == (4, 1)
    assert ids(group(other, RECENT)["rows"]) == [sid(3)]
    assert listed(roots, folder="/work/proj")["total"] == 0


def test_a_harness_filter_leaves_the_harness_counts_as_they_were(roots):
    shared_directory(roots)

    answer = listed(roots, folder="/work/proj-a", harness=CLAUDE)

    assert answer["total"] == 2
    assert [h["count"] for h in answer["harnesses"]] == [2, 1, 0]
    assert sum(g["count"] for g in answer["groups"]) == 2


def test_since_and_until_cut_on_the_last_change(roots):
    shared_directory(roots)

    assert listed(roots, since=NOW - DAY)["total"] == 2
    assert listed(roots, until=NOW - DAY)["total"] == 2
    answer = listed(roots, since=NOW - 2.5 * DAY, until=NOW - DAY)
    assert answer["total"] == 1 and answer["all"] == 4
    assert [h["count"] for h in answer["harnesses"]] == [1, 0, 0]


def test_entry_level_filters_read_no_head_but_the_ones_the_groups_show(roots):
    claude(roots, 1, NOW - 10)
    claude(roots, 2, NOW - 2 * DAY)
    for n in range(3, 12):                      # nine older days: two of them are earlier
        claude(roots, n, NOW - n * DAY)
    cat = catalog(roots)

    answer = list_sessions(cat, recent_s=HOUR, now=NOW, harness=CLAUDE, since=0)

    assert answer["total"] == 11 and group(answer, EARLIER)["count"] == 3
    assert cat.head_reads == 8                  # `recent` and the seven days
    list_sessions(cat, recent_s=HOUR, now=NOW, folder="/work/proj-a")
    assert cat.head_reads == 11                 # a folder asks for every head, once
    list_sessions(cat, recent_s=HOUR, now=NOW, find="proj")
    list_folders(cat)
    assert cat.head_reads == 11


def test_folders_are_counted_under_the_other_filters_newest_first(roots):
    shared_directory(roots)
    cat = catalog(roots)

    answer = list_folders(cat)

    assert answer == {"folders": [
        {"folder": "/work/proj-a", "count": 3, "changed": NOW - 10},
        {"folder": "/work/proj-b", "count": 1, "changed": NOW - 20}], "more": 0}
    assert list_folders(cat, harness=CODEX) == {"folders": [
        {"folder": "/work/proj-a", "count": 1, "changed": NOW - 3 * DAY}], "more": 0}
    assert list_folders(cat, since=NOW - HOUR, limit=1) == {"folders": [
        {"folder": "/work/proj-a", "count": 1, "changed": NOW - 10}], "more": 1}
    assert list_folders(cat, find="proj-b")["folders"][0]["folder"] == "/work/proj-b"


def test_a_filter_that_is_not_one_is_refused(roots):
    cat = catalog(roots)
    for bad in ({"harness": "other"}, {"folder": "proj-a"}, {"find": "a"},
                {"find": " a "}, {"since": "today"}):
        with pytest.raises(ValueError):
            list_sessions(cat, recent_s=HOUR, now=NOW, **bad)


# --- find -------------------------------------------------------------------------------------

def test_find_matches_an_id_start_or_the_folder_last_segment_and_nothing_else(roots):
    claude(roots, 1, NOW - 10, session="abc10000-0000-7000-8000-000000000001",
           cwd="/work/other")
    claude(roots, 2, NOW - 2 * DAY, program=True, cwd="/work/Atlas-Api",
           project="-work-atlas")
    codex(roots, 3, NOW - 3 * DAY, cwd="/atlas/elsewhere")
    claude(roots, 4, NOW - 20 * DAY, session="ffabc000-0000-7000-8000-000000000004",
           cwd="/work/third", project="-work-third")
    cat = catalog(roots)

    def find(text):
        return list_sessions(cat, recent_s=HOUR, now=NOW, find=text)

    by_id = find("ABC1")
    assert keys(by_id) == [FOUND]
    assert ids(group(by_id, FOUND)["rows"]) == ["abc10000-0000-7000-8000-000000000001"]
    assert (by_id["total"], by_id["all"]) == (1, 4)
    by_folder = find("las-a")
    assert ids(group(by_folder, FOUND)["rows"]) == [sid(2)]
    assert group(by_folder, FOUND)["rows"][0]["by_program"] is True
    assert find("atlas")["total"] == 1          # a segment that is not the last is not read
    assert find("abc")["total"] == 1            # nor the middle of an id
    assert find("Done")["total"] == 0           # nor what was said


def test_find_lists_both_kinds_in_one_group_and_marks_the_recent_rows(roots):
    claude(roots, 1, NOW - 10)
    claude(roots, 2, NOW - 20, program=True)
    claude(roots, 3, NOW - 30 * DAY)
    codex(roots, 4, NOW - 40 * DAY)

    found = group(listed(roots, find="proj-a"), FOUND)

    assert found["count"] == 4 and found["before"] is None
    assert ids(found["rows"]) == [sid(1), sid(2), sid(3), tid(4)]
    assert [row["recent"] for row in found["rows"]] == [True, False, False, False]


# --- the groups -------------------------------------------------------------------------------

def test_a_day_counts_a_persons_sessions_and_a_programs_apart(roots):
    day = utc(2026, 11, 8, 12)
    for n in (1, 2, 3):
        claude(roots, n, day + n)
    for n in (4, 5):
        claude(roots, n, day + n, program=True)
    codex(roots, 6, day + 6)

    answer = listed(roots)

    assert keys(answer) == [RECENT, "2026-11-08"]
    assert group(answer, RECENT) == {"key": RECENT, "count": 0, "rows": [],
                                     "before": None}
    a_day = group(answer, "2026-11-08")
    assert a_day["count"] == 4 and a_day["program"] == {"count": 2}
    assert ids(a_day["rows"]) == [tid(6), sid(3), sid(2), sid(1)]
    assert a_day["before"] is None
    assert all(row["by_program"] is None and not row["recent"] for row in a_day["rows"])
    assert answer["total"] == 6 and answer["today"] == "2026-11-10"


def test_a_day_with_only_program_sessions_has_no_rows(roots):
    claude(roots, 1, utc(2026, 11, 7, 9), program=True)
    claude(roots, 2, utc(2026, 11, 7, 10), program=True)

    answer = listed(roots)

    assert group(answer, "2026-11-07") == {"key": "2026-11-07", "count": 0, "rows": [],
                                           "before": None, "program": {"count": 2}}


def test_a_program_session_changed_in_the_last_hour_is_in_its_day_not_in_recent(roots):
    claude(roots, 1, NOW - 60)
    claude(roots, 2, NOW - 30, program=True)
    cat = catalog(roots)

    answer = list_sessions(cat, recent_s=HOUR, now=NOW)

    assert ids(group(answer, RECENT)["rows"]) == [sid(1)]
    assert group(answer, RECENT)["rows"][0]["recent"] is True
    today = group(answer, "2026-11-10")
    assert today["count"] == 0 and today["program"] == {"count": 1}
    shown = more_sessions(cat, group="2026-11-10", kind="program", recent_s=HOUR, now=NOW)
    assert ids(shown["rows"]) == [sid(2)] and shown["kind"] == "program"
    assert shown["rows"][0]["recent"] is False
    assert shown["rows"][0]["activity"]["who"] is None


def test_recent_ends_where_the_setting_says(roots):
    claude(roots, 1, NOW - 50 * 60)
    claude(roots, 2, NOW - 70 * 60)
    cat = catalog(roots)

    assert group(list_sessions(cat, recent_s=HOUR, now=NOW), RECENT)["count"] == 1
    assert group(list_sessions(cat, recent_s=2 * HOUR, now=NOW), RECENT)["count"] == 2
    assert group(list_sessions(cat, recent_s=60, now=NOW), RECENT)["count"] == 0


def test_seven_days_are_shown_and_the_rest_is_earlier_and_each_session_is_in_one(roots):
    claude(roots, 1, NOW - 10)
    for n in range(2, 12):                      # one session a day, ten days back
        claude(roots, n, NOW - (n - 1) * DAY, program=n % 2 == 0)
    codex(roots, 12, NOW - 30 * DAY)

    answer = listed(roots)

    assert keys(answer) == [RECENT, "2026-11-09", "2026-11-08", "2026-11-07",
                            "2026-11-06", "2026-11-05", "2026-11-04", "2026-11-03",
                            EARLIER]
    earlier = group(answer, EARLIER)
    assert earlier == {"key": EARLIER, "count": 4, "oldest": NOW - 30 * DAY,
                       "before": earlier["before"]}
    assert parse_position(earlier["before"])[2] == sid(8)
    counted = sum(g["count"] + g.get("program", {}).get("count", 0)
                  for g in answer["groups"])
    assert counted == answer["total"] == answer["all"] == 12


def test_recent_past_thirty_rows_gives_the_place_to_go_on_from(roots):
    for n in range(1, 36):
        claude(roots, n, NOW - n)
    cat = catalog(roots)

    recent = group(list_sessions(cat, recent_s=HOUR, now=NOW), RECENT)

    assert recent["count"] == 35 and len(recent["rows"]) == RECENT_ROWS
    assert ids(recent["rows"]) == [sid(n) for n in range(1, 31)]
    rest = more_sessions(cat, group=RECENT, before=recent["before"], recent_s=HOUR,
                         now=NOW)
    assert ids(rest["rows"]) == [sid(n) for n in range(31, 36)]
    assert rest == {"group": RECENT, "kind": "person", "rows": rest["rows"],
                    "before": None}


def test_a_day_the_clocks_change_on_is_one_day(roots, zone):
    zone("America/New_York")
    # The clocks went back on 1 November 2026: that local day ran 25 hours.
    claude(roots, 1, utc(2026, 11, 1, 4, 30))   # 00:30 on the 1st, before the change
    claude(roots, 2, utc(2026, 11, 2, 4, 30))   # 23:30 on the 1st, after it
    claude(roots, 3, utc(2026, 11, 2, 5, 10))   # 00:10 on the 2nd
    # They went forward on 8 March 2026: that local day ran 23 hours.
    claude(roots, 4, utc(2026, 3, 8, 5, 30))    # 00:30 on the 8th
    claude(roots, 5, utc(2026, 3, 9, 3, 30))    # 23:30 on the 8th
    claude(roots, 6, utc(2026, 3, 9, 4, 10))    # 00:10 on the 9th

    answer = list_sessions(catalog(roots), recent_s=HOUR, now=utc(2026, 11, 3, 3))

    assert answer["today"] == "2026-11-02"      # 22:00 the evening before, there
    assert keys(answer) == [RECENT, "2026-11-02", "2026-11-01", "2026-03-09",
                            "2026-03-08"]
    assert ids(group(answer, "2026-11-01")["rows"]) == [sid(2), sid(1)]
    assert ids(group(answer, "2026-11-02")["rows"]) == [sid(3)]
    assert ids(group(answer, "2026-03-08")["rows"]) == [sid(5), sid(4)]
    assert local_day(utc(2026, 11, 2, 4, 59)) == "2026-11-01"


def test_a_session_with_no_change_time_sorts_last_and_is_earlier(roots):
    db = OpenCodeDb(roots[OPENCODE].parent)
    session = db.session(at=NOW - 2 * DAY)
    session.user("Do the thing.")
    db.write()
    claude(roots, 1, NOW - 3 * DAY)
    cat = catalog(roots)
    real = cat.gather()
    timeless = [dataclasses.replace(e, changed_ns=None) if e.harness == OPENCODE else e
                for e in real.entries]
    timeless.sort(key=lambda e: e.order)
    cat.gather = lambda: Gathered(tuple(timeless), at=real.at)

    answer = list_sessions(cat, recent_s=HOUR, now=NOW)

    assert keys(answer) == [RECENT, "2026-11-07", EARLIER]
    assert group(answer, EARLIER)["count"] == 1
    assert group(answer, EARLIER)["oldest"] is None
    older = more_sessions(cat, group=EARLIER, before=group(answer, EARLIER)["before"],
                          recent_s=HOUR, now=NOW)
    assert [g["key"] for g in older["groups"]] == [UNDATED] and older["left"] == 0
    assert ids(older["groups"][0]["rows"]) == [session.id]
    assert older["groups"][0]["rows"][0]["changed"] is None


# --- more -------------------------------------------------------------------------------------

def page_through(cat, first, before, **ask) -> list[str]:
    """The ids of `first` and of every further page of the group, asked for in turn."""
    seen = ids(first)
    while before is not None:
        answer = more_sessions(cat, before=before, recent_s=HOUR, now=NOW, **ask)
        assert 0 < len(answer["rows"]) <= MORE_ROWS
        seen += ids(answer["rows"])
        before = answer["before"]
    return seen


def test_paging_a_day_gives_each_session_of_each_kind_once(roots):
    day = utc(2026, 11, 8, 12)
    people = [sid(n) for n in range(1, 46)]
    programs = [sid(n) for n in range(101, 136)]
    for n in range(1, 46):
        claude(roots, n, day - n // 3)           # several share one change time
    for n in range(101, 136):
        claude(roots, n, day - n // 3, program=True)
    cat = catalog(roots)

    a_day = group(list_sessions(cat, recent_s=HOUR, now=NOW), "2026-11-08")

    assert a_day["count"] == 45 and a_day["program"] == {"count": 35}
    assert len(a_day["rows"]) == DAY_ROWS
    seen = page_through(cat, a_day["rows"], a_day["before"], group="2026-11-08")
    assert seen == people
    first = more_sessions(cat, group="2026-11-08", kind="program", recent_s=HOUR, now=NOW)
    assert len(first["rows"]) == MORE_ROWS
    seen = page_through(cat, first["rows"], first["before"], group="2026-11-08",
                        kind="program")
    assert seen == programs


def test_paging_found_gives_each_match_of_either_kind_once(roots):
    for n in range(1, 41):
        claude(roots, n, NOW - n * HOUR, program=n % 2 == 0)
    for n in range(41, 71):
        codex(roots, n, NOW - n * HOUR)
    claude(roots, 99, NOW - 5, cwd="/work/proj-b", project="-work-proj-b")
    cat = catalog(roots)

    found = group(list_sessions(cat, recent_s=HOUR, now=NOW, find="proj-a"), FOUND)

    assert found["count"] == 70 and len(found["rows"]) == 30
    seen = page_through(cat, found["rows"], found["before"], group=FOUND, find="proj-a")
    assert seen == [sid(n) for n in range(1, 41)] + [tid(n) for n in range(41, 71)]


def test_earlier_answers_whole_days_until_they_hold_thirty_sessions(roots):
    n = 0
    for back in range(1, 8):                    # the seven days a list shows
        n += 1
        claude(roots, n, NOW - back * DAY)
    sizes = {8: 20, 9: 15, 10: 5, 11: 3}        # days back: sessions that day
    for back, size in sizes.items():
        for i in range(size):
            n += 1
            claude(roots, n, NOW - back * DAY - i, program=i % 5 == 0)
    cat = catalog(roots)

    earlier = group(list_sessions(cat, recent_s=HOUR, now=NOW), EARLIER)
    first = more_sessions(cat, group=EARLIER, before=earlier["before"], recent_s=HOUR,
                          now=NOW)
    second = more_sessions(cat, group=EARLIER, before=first["before"], recent_s=HOUR,
                           now=NOW)

    assert earlier["count"] == 43
    assert [g["key"] for g in first["groups"]] == ["2026-11-02", "2026-11-01"]
    assert [(g["count"], g["program"]["count"]) for g in first["groups"]] == [
        (16, 4), (12, 3)]
    assert len(first["groups"][0]["rows"]) == DAY_ROWS
    assert first["groups"][0]["before"] is not None
    assert first["left"] == 8 and first["before"] is not None
    assert [g["key"] for g in second["groups"]] == ["2026-10-31", "2026-10-30"]
    assert second["left"] == 0 and second["before"] is None
    assert set(second["groups"][0]) == {"key", "count", "rows", "before", "program"}


def test_a_session_that_changes_between_two_calls_is_not_answered_twice(roots):
    files = {n: claude(roots, n, NOW - n) for n in range(1, 51)}
    cat = catalog(roots)

    recent = group(list_sessions(cat, recent_s=HOUR, now=NOW), RECENT)
    changed_at(files[5].path, NOW + 1)           # one already shown
    changed_at(files[40].path, NOW + 2)          # one not shown yet
    rest = more_sessions(cat, group=RECENT, before=recent["before"], recent_s=HOUR,
                         now=NOW + 3)

    assert ids(recent["rows"]) == [sid(n) for n in range(1, 31)]
    assert ids(rest["rows"]) == [sid(n) for n in range(31, 51) if n != 40]
    assert rest["before"] is None
    again = group(list_sessions(cat, recent_s=HOUR, now=NOW + 3), RECENT)
    assert ids(again["rows"])[:3] == [sid(40), sid(5), sid(1)]
    assert again["count"] == 50


def test_a_new_session_between_two_calls_moves_no_row(roots):
    for n in range(1, 41):
        claude(roots, n, NOW - n)
    cat = catalog(roots)

    recent = group(list_sessions(cat, recent_s=HOUR, now=NOW), RECENT)
    claude(roots, 99, NOW + 1)
    rest = more_sessions(cat, group=RECENT, before=recent["before"], recent_s=HOUR,
                         now=NOW + 2)

    assert ids(recent["rows"]) + ids(rest["rows"]) == [sid(n) for n in range(1, 41)]


def test_a_before_that_does_not_parse_is_refused_with_what_one_is(roots):
    claude(roots, 1, NOW - 10)
    cat = catalog(roots)

    for bad in ("", "soon", "12:other:abc", "x:claude:abc", "12:claude:", "-5:claude:a",
                17):
        with pytest.raises(ValueError, match="nanoseconds, its harness and its id"):
            more_sessions(cat, group=RECENT, before=bad, recent_s=HOUR, now=NOW)
    entry = cat.gather().entries[0]
    assert parse_position(position(entry)) == entry.order


def test_a_group_or_kind_that_is_not_one_is_refused(roots):
    cat = catalog(roots)

    def ask(**kw):
        return more_sessions(cat, recent_s=HOUR, now=NOW, **kw)

    for bad in ({"group": "tomorrow"}, {"group": "2026-13-01"},
                {"group": RECENT, "kind": "program"}, {"group": RECENT, "kind": "both"},
                {"group": FOUND}, {"group": RECENT, "find": "proj"},
                {"group": EARLIER, "kind": "program"}):
        with pytest.raises(ValueError):
            ask(**bad)
    assert ask(group="2026-11-08") == {"group": "2026-11-08", "kind": "person",
                                       "rows": [], "before": None}


# --- one session, opened --------------------------------------------------------------------

def opened(roots, session: str, harness: str = CLAUDE):
    return open_session(catalog(roots), harness=harness, session=session, recent_s=HOUR,
                        now=NOW)


def test_an_opened_session_carries_its_row_and_the_reply_to_the_last_prompt(roots):
    t = ClaudeTranscript(roots[CLAUDE], session_id=sid(1), cwd="/work/proj-a")
    t.user("First.", origin="human")
    t.assistant("An old answer.")
    asked = t.clock
    t.user("Second.", origin="human")
    t.assistant("Halfway.")
    t.assistant("The new answer.")
    changed_at(t.write(), NOW - 10)

    row = opened(roots, sid(1))

    assert row["reply"] == {"at": asked + 2, "text": "The new answer."}
    assert row["prompt"] == {"at": asked, "text": "Second."}
    assert row["harness"] == CLAUDE and row["session"] == sid(1)
    assert row["folder"] == "/work/proj-a" and row["recent"] is True
    assert row["subagents"] == [] and row["more"] == 0
    assert row["resume"] == {"command": f"claude --resume {sid(1)}",
                             "folder": "/work/proj-a"}


def test_there_is_no_reply_when_a_user_message_is_the_newest(roots):
    unanswered = ClaudeTranscript(roots[CLAUDE], session_id=sid(1))
    unanswered.user("First.", origin="human")
    unanswered.assistant("An old answer.")
    unanswered.user("Second.", origin="human")
    unanswered.write()
    injected = ClaudeTranscript(roots[CLAUDE], session_id=sid(2))
    injected.user("First.", origin="human")
    injected.assistant("An answer.")
    injected.user("A task finished.", new_prompt=False, origin="task-notification")
    injected.write()
    made_up = ClaudeTranscript(roots[CLAUDE], session_id=sid(3))
    made_up.user("First.", origin="human")
    made_up.synthetic()
    made_up.write()

    assert opened(roots, sid(1))["reply"] is None
    assert opened(roots, sid(2))["reply"] is None
    assert opened(roots, sid(3))["reply"] is None
    assert opened(roots, sid(9)) is None


def test_an_opened_session_lists_eight_subagents_and_counts_the_rest(roots):
    t = ClaudeTranscript(roots[CLAUDE], session_id=sid(1))
    t.user("Look into it.", origin="human")
    starts = {}
    for n in range(1, 12):
        child = t.subagent(f"a{n:02d}", agent_type="scout", description=f"Look at {n}")
        starts[n] = child.clock
        child.user("look")
        child.assistant(f"Found {n}.")
        if n == 2:
            child.tool_result("call_1", "ok")   # its newest record is not a message
    quiet = t.subagent("a00", agent_type=None, description=None)
    quiet.add_raw("not a line")
    t.assistant("Done.")
    t.write()

    row = opened(roots, sid(1))

    assert len(row["subagents"]) == SUBAGENTS_MAX and row["more"] == 4
    assert row["subagents"][0] == {"description": None, "agent_type": None,
                                   "started": None, "last_at": None, "last": None}
    assert row["subagents"][1] == {"description": "Look at 1", "agent_type": "scout",
                                   "started": starts[1], "last_at": starts[1] + 1,
                                   "last": "Found 1."}
    assert row["subagents"][2]["last_at"] == starts[2] + 2
    assert row["subagents"][2]["last"] == "Found 2."
    assert row["subagents"] and row["subagents"][7]["description"] == "Look at 7"


def test_a_codex_and_an_opencode_session_open_with_their_resume_commands(roots):
    codex(roots, 1, NOW - 10, cwd="/work/proj-b")
    db = OpenCodeDb(roots[OPENCODE].parent)
    session = db.session(directory="/work/proj-a", at=NOW - 20)
    session.user("Do the thing.")
    session.child()
    db.write()

    thread = opened(roots, tid(1), CODEX)
    row = opened(roots, session.id, OPENCODE)

    assert thread["reply"]["text"] == "Done."
    assert thread["resume"] == {"command": f"codex resume {tid(1)}", "folder": None}
    assert thread["folder"] == "/work/proj-b"
    assert row["resume"] == {"command": f"opencode --session {session.id}",
                             "folder": "/work/proj-a"}
    assert row["reply"] is None and len(row["subagents"]) == 1 and row["more"] == 0


# --- the resume commands ----------------------------------------------------------------------

def test_each_harness_has_its_resume_command():
    assert resume(CLAUDE, "abc", "/work/proj-a") == {"command": "claude --resume abc",
                                                     "folder": "/work/proj-a"}
    assert resume(CLAUDE, "abc") == {"command": "claude --resume abc", "folder": None}
    assert resume(CODEX, "abc", "/work/proj-a") == {"command": "codex resume abc",
                                                    "folder": None}
    assert resume(OPENCODE, "ses_1", "/work/proj-a") == {
        "command": "opencode --session ses_1", "folder": "/work/proj-a"}


def test_an_id_a_shell_would_read_is_quoted():
    assert resume(CODEX, "a b; rm x")["command"] == "codex resume 'a b; rm x'"
    assert resume(CLAUDE, "it's")["command"] == "claude --resume 'it'\"'\"'s'"
    assert resume(CODEX, "") is None and resume(CODEX, None) is None


def test_a_harness_left_out_of_the_table_has_no_resume_command(roots, monkeypatch):
    monkeypatch.delitem(resume_module.LINES, CODEX)
    codex(roots, 1, NOW - 10)

    assert resume(CODEX, "abc") is None
    assert resume("other", "abc") is None
    assert opened(roots, tid(1), CODEX)["resume"] is None


# --- the sessions as one run ----------------------------------------------------------------

def test_the_newest_sessions_are_one_run_of_both_kinds_with_the_lists_counts(roots):
    claude(roots, 1, NOW - 10 * 60)
    claude(roots, 2, NOW - 20 * 60, program=True)
    codex(roots, 3, NOW - 3 * DAY, cwd="/work/proj-b")
    claude(roots, 4, NOW - 40 * DAY)
    cat = catalog(roots)

    answer = newest_sessions(cat, recent_s=HOUR, now=NOW, limit=3)

    assert [r["session"] for r in answer["rows"]] == [sid(1), sid(2), tid(3)]
    assert [r["recent"] for r in answer["rows"]] == [True, False, False]
    assert (answer["all"], answer["total"]) == (4, 4)
    assert answer["before"] == position(cat.gather().entries[2])
    whole = list_sessions(cat, recent_s=HOUR, now=NOW)
    for key in ("now", "today", "all", "total", "unreadable", "roots", "harnesses"):
        assert answer[key] == whole[key], key
    assert cat.head_reads == 4 and cat.tail_reads == 4

    some = newest_sessions(cat, recent_s=HOUR, now=NOW, harness=CLAUDE,
                           folder="/work/proj-a", since=NOW - DAY)
    assert [r["session"] for r in some["rows"]] == [sid(1), sid(2)]
    assert (some["total"], some["before"]) == (2, None)
    for limit in (0, -1, True, "3"):
        with pytest.raises(ValueError, match="limit is a number of sessions"):
            newest_sessions(cat, recent_s=HOUR, now=NOW, limit=limit)


# -- a harness that is not there, and a root that was not read ---------------------------------

@pytest.fixture
def bare_home(tmp_path, monkeypatch):
    """A home with no harness in it, and an environment that names no root."""
    home = tmp_path / "bare-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    return home


def test_a_machine_with_no_harness_answers_an_empty_list_and_no_failure(bare_home):
    answer = list_sessions(Catalog(), recent_s=HOUR, now=NOW)

    assert (answer["all"], answer["total"]) == (0, 0)
    assert answer["roots"] == [] and answer["harnesses"] == []
    assert answer["unreadable"] is None
    assert answer["groups"] == [{"key": RECENT, "count": 0, "rows": [], "before": None}]


def test_a_harness_that_is_not_there_has_no_row_and_no_failure(bare_home):
    claude({CLAUDE: bare_home / ".claude" / "projects"}, 1, NOW - 60)

    answer = list_sessions(Catalog(), recent_s=HOUR, now=NOW)

    assert answer["all"] == 1 and answer["roots"] == []
    assert answer["harnesses"] == [{"harness": CLAUDE, "count": 1}]
    assert answer["unreadable"] is None


def test_a_root_that_failed_has_no_count_and_no_zero(roots, tmp_path):
    claude(roots, 1, NOW - 60)
    roots[CODEX] = tmp_path / "nowhere"

    answer = list_sessions(Catalog(roots), recent_s=HOUR, now=NOW)

    assert [r["harness"] for r in answer["roots"]] == [CODEX]
    assert answer["harnesses"] == [{"harness": CLAUDE, "count": 1},
                                   {"harness": OPENCODE, "count": 0}]
    assert answer["unreadable"] is None
