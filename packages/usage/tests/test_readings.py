"""The readings store and its one write path: Codex records from a rollout and from the
app-server, Claude's `$.session.usage()` results, the history cap and the prune."""

import json
import os
import stat

import pytest

from moxi.paths import Layout
from moxi_sessions.api import UNKNOWN, Limits, LimitWindow, codex, records
from moxi_sessions.api import testing as builders
from moxi_usage import readings

from conftest import write_config

NOW = 1793400000.0
HOUR = 3600.0
DAY = 86400.0


def rows(*, order="id"):
    conn = readings.open_store()
    try:
        return conn.execute(f"SELECT {', '.join(readings.Reading._fields)} FROM readings "
                            f"ORDER BY {order}").fetchall()
    finally:
        conn.close()


def as_readings():
    return [readings.Reading(*row) for row in rows()]


def limits_record(*windows, observed_at=NOW - 60, source="rollout", limit_id="codex",
                  session_id="thread-1", **fields):
    return Limits(harness="codex", session_id=session_id, observed_at=observed_at,
                  time=observed_at, source=source, limit_id=limit_id, plan_type="plus",
                  windows=tuple(LimitWindow(name=name, used_percent=used,
                                            window_minutes=minutes, resets_at=resets)
                                for name, used, minutes, resets in windows), **fields)


def app_server(primary=(12.0, 300, NOW + HOUR), secondary=(30.0, 10080, NOW + 3 * DAY)):
    def window(value):
        if value is None:
            return None
        used, minutes, resets = value
        return {"usedPercent": used, "windowDurationMins": minutes, "resetsAt": resets}

    return {"rateLimits": {"limitId": "codex", "limitName": None, "primary": window(primary),
                           "secondary": window(secondary), "planType": "pro",
                           "credits": None}}


# --- the store ------------------------------------------------------------------------

def test_the_store_is_created_private_under_usage_state():
    conn = readings.open_store()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    path = Layout("usage").state_dir() / "readings.db"
    assert readings.path() == path and path.exists()
    assert version == len(readings.STEPS) and mode == "wal"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# --- Codex ----------------------------------------------------------------------------

def test_rollout_records_store_one_row_per_window_and_repeats_are_ignored(tmp_path):
    rollout = builders.CodexRollout(tmp_path / "sessions", start=NOW - 600)
    rollout.request(limits=builders.rate_limits(primary=(5.0, 300, NOW + HOUR),
                                                secondary=(40.0, 10080, NOW + DAY)))
    rollout.request(limits=builders.rate_limits(primary=(7.0, 300, NOW + HOUR),
                                                secondary=(41.0, 10080, NOW + DAY)))
    rollout.write()
    source = builders_source(rollout)
    found = list(records(source, kinds={"limits"}))
    assert len(found) == 2
    assert readings.add_codex(found, now=NOW) == 4
    assert readings.add_codex(found, now=NOW) == 0
    stored = as_readings()
    assert {(r.limit_id, r.window_minutes, r.slot, r.used_percent) for r in stored} == {
        ("codex", 300, "primary", 5.0), ("codex", 10080, "secondary", 40.0),
        ("codex", 300, "primary", 7.0), ("codex", 10080, "secondary", 41.0)}
    assert {r.source for r in stored} == {"rollout"}
    assert {r.session_id for r in stored} == {rollout.thread_id}
    assert {r.plan_type for r in stored} == {"plus"}


def builders_source(rollout):
    from moxi_sessions.api import Source

    return Source("codex", str(rollout.path), rollout.thread_id)


def test_two_slots_of_one_duration_are_one_window_at_the_higher_percent():
    record = limits_record(("primary", 20.0, 10080, NOW + DAY),
                           ("secondary", 35.0, 10080, NOW + DAY))
    assert readings.add_codex([record], now=NOW) == 1
    (stored,) = as_readings()
    assert (stored.window_minutes, stored.used_percent, stored.slot) == (10080, 35.0,
                                                                         "secondary")


def test_a_window_with_no_duration_is_not_stored():
    record = limits_record(("primary", 20.0, UNKNOWN, NOW + DAY),
                           ("secondary", 35.0, None, NOW + DAY),
                           ("tertiary", 1.0, 60, NOW + HOUR))
    assert readings.add_codex([record], now=NOW) == 1
    assert [r.window_minutes for r in as_readings()] == [60]


def test_readings_past_the_history_cap_are_skipped():
    old = limits_record(("primary", 1.0, 300, NOW), observed_at=NOW - 9 * DAY)
    inside = limits_record(("primary", 2.0, 300, NOW), observed_at=NOW - 7 * DAY)
    assert readings.add_codex([old, inside], now=NOW) == 1
    write_config("[limits]\nhistory_days = 10\n")
    assert readings.add_codex([old], now=NOW) == 1


def test_a_record_with_no_limit_id_is_the_main_codex_limit_and_others_are_skipped():
    record = limits_record(("primary", 3.0, 300, NOW + HOUR), limit_id=UNKNOWN)
    not_limits = object()
    no_time = limits_record(("primary", 3.0, 300, NOW + HOUR), observed_at=UNKNOWN)
    no_time = Limits(**{**{f: getattr(no_time, f) for f in
                           ("harness", "session_id", "source", "limit_id", "windows")},
                        "observed_at": UNKNOWN})
    assert readings.add_codex([record, not_limits, no_time], now=NOW) == 1
    assert as_readings()[0].limit_id == "codex"


def test_app_server_readings_keep_their_source_and_no_session():
    found = codex.limits_from_app_server(app_server(), NOW - 5)
    assert readings.add_codex(found, now=NOW) == 2
    stored = as_readings()
    assert {r.source for r in stored} == {"app-server"}
    assert {r.session_id for r in stored} == {None}
    assert {r.plan_type for r in stored} == {"pro"}
    assert readings.add_codex(found, now=NOW) == 0


def test_an_app_server_reading_with_no_reset_time_takes_the_one_before_it():
    readings.add_codex(codex.limits_from_app_server(app_server(), NOW - 100), now=NOW)
    push = app_server(primary=(15.0, 300, None), secondary=None)
    assert readings.add_codex(codex.limits_from_app_server(push, NOW - 10), now=NOW) == 1
    newest = max((r for r in as_readings() if r.window_minutes == 300),
                 key=lambda r: r.observed_at)
    assert (newest.used_percent, newest.resets_at) == (15.0, NOW + HOUR)


def test_a_rollout_reading_with_no_reset_time_keeps_it_null():
    readings.add_codex([limits_record(("primary", 3.0, 300, NOW + HOUR),
                                      observed_at=NOW - 100)], now=NOW)
    readings.add_codex([limits_record(("primary", 0.0, 300, None),
                                      observed_at=NOW - 10)], now=NOW)
    newest = max(as_readings(), key=lambda r: r.observed_at)
    assert newest.resets_at is None


def test_extra_keeps_what_else_the_reading_said():
    record = limits_record(("primary", 3.0, 300, NOW + HOUR), limit_name="Codex",
                           reached_type="primary", extra={"credits": {"balance": "4"}})
    readings.add_codex([record], now=NOW)
    assert json.loads(as_readings()[0].extra) == {"credits": {"balance": "4"},
                                                  "limit_name": "Codex",
                                                  "reached_type": "primary"}


# --- Claude ---------------------------------------------------------------------------

CLAUDE_RESULT = [
    {"kind": "five_hour", "percentUsed": 61, "resetsAt": "2026-10-30T12:00:00.000Z"},
    {"kind": "seven_day", "percentUsed": 46, "resetsAt": "2026-10-31T05:00:00.000Z"},
    {"kind": "seven_day_opus", "percentUsed": 12.5, "resetsAt": 1793500000},
    {"kind": "spend_limit", "usedDollars": 4.5, "limitDollars": 50},
]


def test_claude_kinds_map_to_limit_and_duration():
    assert readings.claude_window("five_hour") == ("plan", 300)
    assert readings.claude_window("seven_day") == ("plan", 10080)
    assert readings.claude_window("seven_day_opus") == ("opus", 10080)
    assert readings.claude_window("seven_day_") == ("seven_day_", None)
    assert readings.claude_window("spend_limit") == ("spend_limit", None)


def test_a_claude_result_is_stored_with_its_receive_time_and_session():
    added = readings.add_claude(CLAUDE_RESULT, observed_at=NOW - 30, session_id="s1",
                                now=NOW)
    assert added == 4
    stored = {r.slot: r for r in as_readings()}
    five = stored["five_hour"]
    assert (five.harness, five.limit_id, five.window_minutes, five.used_percent) == (
        "claude", "plan", 300, 61.0)
    assert five.resets_at == 1793361600.0          # 2026-10-30T12:00Z
    assert (five.observed_at, five.source, five.session_id) == (NOW - 30, "session-usage",
                                                                "s1")
    assert (stored["seven_day_opus"].limit_id, stored["seven_day_opus"].resets_at) == (
        "opus", 1793500000.0)
    spend = stored["spend_limit"]
    assert (spend.window_minutes, spend.used_percent) == (None, None)
    assert json.loads(spend.extra) == {"usedDollars": 4.5, "limitDollars": 50}


def test_a_claude_result_identical_to_the_sessions_previous_one_is_not_stored():
    assert readings.add_claude(CLAUDE_RESULT, observed_at=NOW - 30, session_id="s1",
                               now=NOW) == 4
    assert readings.add_claude(CLAUDE_RESULT, observed_at=NOW - 20, session_id="s1",
                               now=NOW) == 0
    # another session reporting the same numbers is its own report
    assert readings.add_claude(CLAUDE_RESULT, observed_at=NOW - 20, session_id="s2",
                               now=NOW) == 4
    moved = [dict(CLAUDE_RESULT[0], percentUsed=62), *CLAUDE_RESULT[1:]]
    assert readings.add_claude(moved, observed_at=NOW - 10, session_id="s1", now=NOW) == 4
    # back to what it said two reports ago is a change from the previous one
    assert readings.add_claude(CLAUDE_RESULT, observed_at=NOW - 5, session_id="s1",
                               now=NOW) == 4


def test_an_empty_or_unreadable_claude_result_stores_nothing():
    assert readings.add_claude([], observed_at=NOW, session_id="s1", now=NOW) == 0
    assert readings.add_claude(None, observed_at=NOW, session_id="s1", now=NOW) == 0
    assert readings.add_claude([{"percentUsed": 3}, "x"], observed_at=NOW,
                               session_id="s1", now=NOW) == 0
    assert readings.add_claude(CLAUDE_RESULT, observed_at=NOW - 9 * DAY, session_id="s1",
                               now=NOW) == 0
    assert not readings.path().exists()


def test_the_percent_is_read_on_its_scale():
    readings.add_claude([{"kind": "five_hour", "percentUsed": 0.5, "resetsAt": NOW + 10}],
                        observed_at=NOW, session_id="s", now=NOW)
    assert as_readings()[0].used_percent == 0.5 * readings.PERCENT_SCALE


# --- prune ----------------------------------------------------------------------------

def test_prune_deletes_readings_past_the_cap_and_notes_the_time():
    readings.add_codex([limits_record(("primary", 1.0, 300, NOW), observed_at=t)
                        for t in (NOW - 7.5 * DAY, NOW - 2 * DAY, NOW - 60)], now=NOW)
    assert readings.prune(now=NOW + DAY) == 1
    conn = readings.open_store()
    try:
        assert readings.last_prune(conn) == NOW + DAY
    finally:
        conn.close()
    assert len(as_readings()) == 2
    assert readings.prune(now=NOW + DAY) == 0


@pytest.mark.parametrize("value, seconds", [
    (1793361600, 1793361600.0), ("1793361600", 1793361600.0),
    ("2026-10-30T12:00:00Z", 1793361600.0), ("2026-10-30T12:00:00", 1793361600.0),
    ("soon", None), (True, None), (None, None), (float("nan"), None)])
def test_epoch_reads_numbers_and_iso_text(value, seconds):
    assert readings.epoch(value) == seconds
