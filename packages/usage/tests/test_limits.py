"""`limits()`: windows by duration, picking per window, states, the projection, the
history cap, the Codex rollout read between passes and the pull through turns (a fake:
no test starts Codex)."""

import json
import os
import time
from types import SimpleNamespace

import pytest

from moxi_sessions.api import testing as builders
from moxi_usage import api, limits, readings
from moxi_usage.readings import Reading

from conftest import write_config

REAL_PULLER = limits._puller

NOW = 1793400000.0
MIN = 60.0
HOUR = 3600.0
DAY = 86400.0


@pytest.fixture(autouse=True)
def turns(monkeypatch):
    """turns installed, with a pull that must not be reached unless a test says so."""
    state = SimpleNamespace(available=True, calls=[], answer=None, why=None)

    def pull(*, timeout_s):
        state.calls.append(timeout_s)
        if isinstance(state.answer, BaseException):
            raise state.answer
        return state.answer

    def puller():
        if state.why is not None:
            return None, state.why
        return pull, None

    monkeypatch.setattr(limits, "_turns_available", lambda: state.available)
    monkeypatch.setattr(limits, "_puller", puller)
    return state


def codex(used, minutes, resets, observed, *, slot="primary", limit_id="codex",
          source="rollout", session_id="thread-1"):
    return Reading("codex", limit_id, minutes, slot, used, resets, observed, source,
                   session_id)


def claude(used, minutes, resets, observed, *, kind=None, limit_id="plan", session_id="s1"):
    slot = kind or {300: "five_hour", 10080: "seven_day"}.get(minutes)
    return Reading("claude", limit_id, minutes, slot, used, resets, observed,
                   "session-usage", session_id)


def put(*found, now=NOW):
    conn = readings.open_store()
    try:
        return readings.store(conn, found, now=now, days=8)
    finally:
        conn.close()


def row(result, harness, label):
    (found,) = [r for r in result.rows if r.harness == harness and r.label == label]
    return found


# --- windows and labels ---------------------------------------------------------------

@pytest.mark.parametrize("minutes, limit_id, slot, text", [
    (300, "plan", "five_hour", "5h"), (10080, "plan", "seven_day", "7d"),
    (10080, "opus", "seven_day_opus", "7d opus"), (300, "codex", "primary", "5h"),
    (60, "codex", "primary", "60m"), (1440, "codex_other", "primary", "1440m codex_other"),
    (None, "spend_limit", "spend_limit", "spend_limit")])
def test_a_rows_label_comes_from_its_minutes(minutes, limit_id, slot, text):
    assert limits.label(minutes, limit_id, slot) == text


def test_a_weekly_reading_in_primary_newer_than_a_five_hour_one_leaves_the_5h_row():
    put(codex(10.0, 300, NOW + 2 * HOUR, NOW - 10 * MIN),
        codex(40.0, 10080, NOW + 3 * DAY, NOW - 10 * MIN, slot="secondary"),
        codex(42.0, 10080, NOW + 3 * DAY, NOW - 1 * MIN, slot="primary"))
    result = limits.limits(harness="codex", now=NOW, pull=False)
    five, week = row(result, "codex", "5h"), row(result, "codex", "7d")
    assert (five.used_percent, five.observed_at, five.state) == (10.0, NOW - 10 * MIN,
                                                                 "fresh")
    assert (week.used_percent, week.slot, week.observed_at) == (42.0, "primary", NOW - MIN)
    assert [r.label for r in result.rows] == ["5h", "7d"]


# --- picking --------------------------------------------------------------------------

def test_codex_picks_the_newest_reading_from_either_source():
    put(codex(30.0, 300, NOW + HOUR, NOW - 5 * MIN, source="app-server", session_id=None),
        codex(25.0, 300, NOW + HOUR, NOW - 2 * MIN))
    five = row(limits.limits(harness="codex", now=NOW, pull=False), "codex", "5h")
    assert (five.used_percent, five.source, five.age_s) == (25.0, "rollout", 2 * MIN)


def test_claude_picks_the_latest_future_reset_then_the_highest_percent():
    put(claude(70.0, 300, NOW - HOUR, NOW - 2 * HOUR),               # its window is over
        claude(20.0, 300, NOW + 3 * HOUR, NOW - 30 * MIN, session_id="a"),
        claude(25.0, 300, NOW + 3 * HOUR, NOW - 40 * MIN, session_id="b"),
        claude(5.0, 300, NOW + 3 * HOUR - 30, NOW - MIN, session_id="c"))
    five = row(limits.limits(harness="claude", now=NOW, pull=False), "claude", "5h")
    assert (five.used_percent, five.session_id, five.state) == (25.0, "b", "fresh")
    assert five.age_s == 40 * MIN


def test_an_idle_sessions_old_number_cannot_pass_for_fresh():
    put(claude(80.0, 300, NOW - 10 * MIN, NOW - MIN, session_id="idle"),
        claude(12.0, 300, NOW + 4 * HOUR, NOW - 20 * MIN, session_id="busy"))
    five = row(limits.limits(harness="claude", now=NOW, pull=False), "claude", "5h")
    assert (five.used_percent, five.session_id) == (12.0, "busy")


def test_with_no_future_reading_claude_shows_the_newest_readings_state():
    put(claude(80.0, 300, NOW - 10 * MIN, NOW - HOUR),
        claude(81.0, 300, NOW - 5 * MIN, NOW - 30 * MIN))
    five = row(limits.limits(harness="claude", now=NOW, pull=False), "claude", "5h")
    assert (five.state, five.used_percent) == ("reset", 81.0)


# --- states ---------------------------------------------------------------------------

@pytest.mark.parametrize("used, resets, state, reason", [
    (10.0, NOW + 1, "fresh", None),
    (10.0, None, "reset", "the reading has no reset time"),
    (10.0, NOW, "reset", "the window has reset since the reading"),
    (None, NOW + 1, "unavailable", "the reading has no percent for this window"),
    (None, None, "unavailable", "the reading has no percent for this window")])
def test_row_states(used, resets, state, reason):
    assert limits.row_state(used, resets, NOW) == (state, reason)


def test_turns_absent_makes_claudes_rows_unavailable(turns):
    put(claude(20.0, 300, NOW + HOUR, NOW - MIN))
    turns.available = False
    result = limits.limits(harness="claude", now=NOW, pull=False)
    assert [(r.label, r.state, r.reason) for r in result.rows] == [
        ("5h", "unavailable", "turns is not installed"),
        ("7d", "unavailable", "turns is not installed")]


def test_the_main_windows_are_always_rows_and_say_why_they_are_empty():
    put(claude(3.0, 10080, NOW + DAY, NOW - MIN, kind="seven_day_opus", limit_id="opus"))
    result = limits.limits(now=NOW, pull=False)
    assert [(r.harness, r.label, r.state) for r in result.rows] == [
        ("claude", "5h", "unavailable"), ("claude", "7d", "unavailable"),
        ("claude", "7d opus", "fresh"),
        ("codex", "5h", "unavailable"), ("codex", "7d", "unavailable")]
    assert row(result, "codex", "5h").reason == ("no Codex reading of this window in the "
                                                 "last 8 days")


def test_a_kind_with_no_duration_is_its_own_row_and_keeps_its_dollars():
    readings.add_claude([{"kind": "spend_limit", "usedDollars": 4.5}], observed_at=NOW - 5,
                        session_id="s", now=NOW)
    spend = row(limits.limits(harness="claude", now=NOW, pull=False), "claude",
                "spend_limit")
    assert (spend.window_minutes, spend.state, dict(spend.extra)) == (
        api.UNKNOWN, "unavailable", {"usedDollars": 4.5})


# --- projection -----------------------------------------------------------------------

def test_a_rising_window_projects_when_it_fills():
    put(*(codex(p, 300, NOW + 4 * HOUR, NOW - m * MIN)
          for p, m in ((40.0, 30), (45.0, 20), (50.0, 10))))
    five = row(limits.limits(harness="codex", now=NOW, pull=False), "codex", "5h")
    # 0.5% a minute from 50% ten minutes ago: 100% a hundred minutes later
    assert five.full_at == pytest.approx(NOW + 90 * MIN)
    assert (five.fit, five.projection) == (3, None)


def test_a_projection_with_readings_on_both_sides_of_a_reset_fits_only_the_winners():
    old_period = NOW + 20 * MIN - 5 * HOUR
    put(codex(90.0, 300, old_period, NOW - 50 * MIN),
        codex(95.0, 300, old_period, NOW - 40 * MIN),
        codex(1.0, 300, NOW + 4 * HOUR + 20 * MIN, NOW - 30 * MIN),
        codex(6.0, 300, NOW + 4 * HOUR + 20 * MIN + 2, NOW - 20 * MIN),
        codex(11.0, 300, NOW + 4 * HOUR + 20 * MIN - 1, NOW - 10 * MIN))
    five = row(limits.limits(harness="codex", now=NOW, pull=False), "codex", "5h")
    assert five.fit == 3
    assert five.full_at == pytest.approx(NOW - 10 * MIN + 89 * 2 * MIN)


def test_too_few_readings_give_no_projection_and_say_so():
    put(codex(40.0, 300, NOW + HOUR, NOW - 2 * HOUR),          # before the hour fitted
        codex(45.0, 300, NOW + HOUR, NOW - 20 * MIN),
        codex(50.0, 300, NOW + HOUR, NOW - 10 * MIN))
    five = row(limits.limits(harness="codex", now=NOW, pull=False), "codex", "5h")
    assert five.full_at is None
    assert five.projection == ("2 readings of this period in the last 60 minutes; a "
                               "projection needs 3")
    write_config("[limits]\nprojection_minutes = 180\n")
    assert row(limits.limits(harness="codex", now=NOW, pull=False), "codex",
               "5h").fit == 3


def test_a_window_that_resets_before_it_fills_says_resets_first():
    put(*(codex(p, 300, NOW + 30 * MIN, NOW - m * MIN)
          for p, m in ((10.0, 30), (11.0, 20), (12.0, 10))))
    five = row(limits.limits(harness="codex", now=NOW, pull=False), "codex", "5h")
    assert (five.full_at, five.projection) == (None, "resets first")


def test_a_fit_that_reaches_100_percent_before_now_gives_no_full_at():
    # 1.5% a minute through readings 45 to 55 minutes old reaches 100% 42 minutes ago,
    # while the newest reading says 95%
    put(*(codex(p, 300, NOW + 4 * HOUR, NOW - m * MIN)
          for p, m in ((80.0, 55), (90.0, 50), (95.0, 45))))
    five = row(limits.limits(harness="codex", now=NOW, pull=False), "codex", "5h")
    assert (five.state, five.used_percent) == ("fresh", 95.0)
    assert (five.full_at, five.projection, five.fit) == (
        None, "the fit reaches 100% before now", 3)


def test_a_flat_window_does_not_project():
    put(*(codex(10.0, 300, NOW + HOUR, NOW - m * MIN) for m in (30, 20, 10)))
    five = row(limits.limits(harness="codex", now=NOW, pull=False), "codex", "5h")
    assert (five.full_at, five.projection) == (None, "not rising")


def test_a_window_with_no_duration_does_not_project():
    winner = claude(10.0, None, NOW + HOUR, NOW, kind="other", limit_id="other")
    assert limits.project([winner], winner, now=NOW, minutes=60) == (
        None, "the window's duration is unknown", 0)


# --- the history cap ------------------------------------------------------------------

def test_limits_prunes_when_the_last_prune_is_over_an_hour_old():
    put(codex(1.0, 300, NOW, NOW - 7.97 * DAY), now=NOW)
    limits.limits(harness="codex", now=NOW, pull=False)              # first prune
    conn = readings.open_store()
    try:
        assert readings.last_prune(conn) == NOW
    finally:
        conn.close()
    limits.limits(harness="codex", now=NOW + 0.5 * HOUR, pull=False)
    assert count() == 1                                              # not due yet
    limits.limits(harness="codex", now=NOW + 1.1 * HOUR, pull=False)
    assert count() == 0


def count():
    conn = readings.open_store()
    try:
        return conn.execute("SELECT count(*) FROM readings").fetchone()[0]
    finally:
        conn.close()


# --- Codex between passes: the newest rollout ------------------------------------------

def sessions_root(fence):
    return fence.home / ".codex" / "sessions"


def rollout(root, thread, start, readings_at):
    built = builders.CodexRollout(root, thread_id=thread, start=start, step=MIN)
    for used in readings_at:
        built.request(limits=builders.rate_limits(primary=(used, 300, NOW + HOUR),
                                                  secondary=(30.0, 10080, NOW + DAY)))
    return built


def test_limits_reads_the_newest_rollout_by_mtime(fence):
    root = sessions_root(fence)
    newer = rollout(root, "0190c000-0000-7000-8000-00000000aaaa", NOW - 30 * MIN,
                    [5.0, 6.0])
    older = rollout(root, "0190c000-0000-7000-8000-00000000bbbb", NOW - 20 * MIN, [9.0])
    newer.write()
    older.write()
    os.utime(older.path, (NOW - 10 * MIN, NOW - 10 * MIN))
    os.utime(newer.path, (NOW - 2 * MIN, NOW - 2 * MIN))
    result = limits.limits(harness="codex", now=NOW, pull=False)
    five = row(result, "codex", "5h")
    assert (five.used_percent, five.session_id) == (6.0, newer.thread_id)
    assert count() == 4                                       # two readings of two windows
    assert result.notes == ()


def test_a_second_call_reads_only_what_the_rollout_added(fence, monkeypatch):
    root = sessions_root(fence)
    built = rollout(root, "0190c000-0000-7000-8000-00000000aaaa", NOW - 30 * MIN, [5.0])
    built.write()
    limits.limits(harness="codex", now=NOW, pull=False)
    seen = []
    real = readings.codex_readings
    monkeypatch.setattr(readings, "codex_readings",
                        lambda record: seen.append(record) or real(record))
    built.request(limits=builders.rate_limits(primary=(8.0, 300, NOW + HOUR)))
    built.flush()
    os.utime(built.path, (NOW - MIN, NOW - MIN))
    five = row(limits.limits(harness="codex", now=NOW, pull=False), "codex", "5h")
    assert five.used_percent == 8.0
    assert len(seen) == 2              # the new reading, then the stored one it stops at


def test_no_codex_sessions_root_is_no_reading_and_no_note():
    result = limits.limits(harness="codex", now=NOW, pull=False)
    assert result.notes == ()
    assert row(result, "codex", "5h").state == "unavailable"


def test_a_sessions_root_that_cannot_be_listed_is_a_note_not_a_failure(fence):
    root = sessions_root(fence)
    root.parent.mkdir(parents=True)
    root.write_text("not a directory")
    result = limits.limits(harness="codex", now=NOW, pull=False)
    assert len(result.notes) == 1
    assert result.notes[0].startswith("Codex rollouts not listed: ")
    assert row(result, "codex", "5h").state == "unavailable"


# --- the pull through turns -----------------------------------------------------------

def pulled(used=12.0, received=NOW - 1):
    payload = {"rateLimits": {"limitId": "codex", "planType": "pro",
                              "primary": {"usedPercent": used, "windowDurationMins": 300,
                                          "resetsAt": NOW + HOUR},
                              "secondary": {"usedPercent": 33.0,
                                            "windowDurationMins": 10080,
                                            "resetsAt": NOW + DAY}}}
    return SimpleNamespace(payload=payload, received_at=received, via="host")


def test_a_stale_codex_reading_is_pulled_through_turns(turns):
    put(codex(5.0, 300, NOW + HOUR, NOW - 20 * MIN))
    turns.answer = pulled()
    result = limits.limits(harness="codex", now=NOW)
    assert turns.calls == [3.0]
    assert result.pull.as_dict() == {"tried": True, "ok": True, "reason": None,
                                     "via": "host", "added": 2}
    five = row(result, "codex", "5h")
    assert (five.used_percent, five.source, five.note) == (12.0, "app-server", None)


def test_a_fresh_codex_reading_is_not_pulled(turns):
    put(codex(5.0, 300, NOW + HOUR, NOW - 5 * MIN))
    result = limits.limits(harness="codex", now=NOW)
    assert turns.calls == [] and result.pull is None
    write_config("[limits]\ncodex_pull_after_s = 120\n")
    turns.answer = pulled()
    limits.limits(harness="codex", now=NOW)
    assert turns.calls == [3.0]


def test_no_pull_never_pulls(turns):
    result = limits.limits(harness="codex", now=NOW, pull=False)
    assert turns.calls == [] and result.pull is None
    assert row(result, "codex", "5h").note is None


@pytest.mark.parametrize("answer, why", [
    (None, "turns found no Codex host or app-server to ask, or it did not answer in "
           "time"),
    (TimeoutError("no answer in 3 s"), "the pull failed: TimeoutError: no answer in 3 s"),
    (SimpleNamespace(payload={"nothing": 1}, received_at=NOW, via="app-server"),
     "the pulled reading could not be read: ValueError: a rate-limit snapshot is a JSON "
     "object")])
def test_a_failed_pull_keeps_the_stored_readings_and_the_row_says_why(turns, answer, why):
    put(codex(5.0, 300, NOW + HOUR, NOW - 20 * MIN),
        codex(30.0, 10080, NOW + DAY, NOW - 20 * MIN, slot="secondary"))
    turns.answer = answer
    result = limits.limits(harness="codex", now=NOW)
    assert (result.pull.tried, result.pull.ok, result.pull.reason) == (True, False, why)
    five = row(result, "codex", "5h")
    assert (five.used_percent, five.state) == (5.0, "fresh")
    assert five.note == f"no newer reading: {why}"
    assert row(result, "codex", "7d").note == five.note


def test_a_failed_pull_is_not_tried_again_until_the_pull_threshold_has_passed(turns):
    put(codex(5.0, 300, NOW + HOUR, NOW - 20 * MIN))
    turns.answer = None
    assert limits.limits(harness="codex", now=NOW).pull.tried
    assert turns.calls == [3.0]
    again = limits.limits(harness="codex", now=NOW + 5 * MIN)
    assert turns.calls == [3.0]
    assert (again.pull.tried, again.pull.ok) == (False, False)
    assert "the last pull, 5 min ago, brought no reading" in again.pull.reason
    assert row(again, "codex", "5h").note == f"no newer reading: {again.pull.reason}"

    turns.answer = pulled(received=NOW + 10 * MIN)
    assert limits.limits(harness="codex", now=NOW + 10 * MIN).pull.ok
    assert turns.calls == [3.0, 3.0]
    turns.answer = None                    # a success clears the failure it followed
    assert limits.limits(harness="codex", now=NOW + 21 * MIN).pull.tried
    assert turns.calls == [3.0, 3.0, 3.0]


def test_turns_absent_means_no_pull_and_the_row_says_so(turns):
    put(codex(5.0, 300, NOW + HOUR, NOW - 20 * MIN))
    turns.why = "turns is not installed"
    result = limits.limits(harness="codex", now=NOW)
    assert (result.pull.tried, result.pull.reason) == (False, "turns is not installed")
    assert row(result, "codex", "5h").note == "no newer reading: turns is not installed"
    assert turns.calls == []


def test_the_puller_asks_turns_only_when_it_is_installed(monkeypatch):
    from moxi import modules

    monkeypatch.setattr(modules, "available", lambda name: False)
    assert REAL_PULLER() == (None, "turns is not installed")
    monkeypatch.setattr(modules, "available", lambda name: True)
    api_module = SimpleNamespace()
    monkeypatch.setattr(modules, "load",
                        lambda name: SimpleNamespace(ok=True, api=api_module, reason="",
                                                     state="present"))
    assert REAL_PULLER() == (None, "the installed turns offers no Codex limits pull")
    api_module.codex_rate_limits = lambda *, timeout: None
    assert REAL_PULLER() == (api_module.codex_rate_limits, None)
    monkeypatch.setattr(modules, "load",
                        lambda name: SimpleNamespace(ok=False, api=None, reason="boom",
                                                     state="broken"))
    assert REAL_PULLER() == (None, "turns could not be loaded: boom")


# --- the API ----------------------------------------------------------------------------

def test_api_limits_answers_with_as_dict_for_json():
    put(codex(10.0, 300, NOW + HOUR, NOW - MIN))
    result = api.limits(harness="codex", now=NOW, pull=False)
    data = json.loads(json.dumps(result.as_dict()))
    assert data["now"] == "2026-10-30T22:40:00Z"
    five = data["rows"][0]
    assert (five["label"], five["state"], five["used_percent"], five["age_s"]) == (
        "5h", "fresh", 10.0, 60.0)
    assert five["resets_at"] == "2026-10-30T23:40:00Z"
    assert data["pull"] is None


def test_what_a_reading_did_not_say_is_unknown_and_named_in_json():
    put(codex(10.0, 300, None, NOW - MIN, session_id=None))
    result = api.limits(harness="codex", now=NOW, pull=False)
    five, seven = row(result, "codex", "5h"), row(result, "codex", "7d")
    assert (five.state, five.used_percent, five.observed_at) == ("reset", 10.0, NOW - MIN)
    assert five.resets_at is api.UNKNOWN and five.session_id is api.UNKNOWN
    assert five.full_at is None                    # usage's own answer: no projection
    data = five.as_dict()
    assert "resets_at" not in data and "session_id" not in data
    assert set(data["unknown"]) == {"resets_at", "session_id", "plan_type"}
    # no reading at all: every fact a reading gives is unknown, the reason says why
    assert seven.state == "unavailable" and "no Codex reading" in seven.reason
    for name in ("used_percent", "resets_at", "observed_at", "age_s", "source",
                 "session_id", "plan_type", "slot"):
        assert getattr(seven, name) is api.UNKNOWN, name
    data = json.loads(json.dumps(result.as_dict()))
    seven = [r for r in data["rows"] if r["label"] == "7d"][0]
    assert "used_percent" not in seven and "used_percent" in seven["unknown"]
    assert seven["window_minutes"] == 10080 and seven["reason"]


def test_api_limits_refuses_an_unknown_harness():
    with pytest.raises(ValueError, match="opencode"):
        api.limits(harness="opencode", now=NOW)


def test_limits_with_no_now_reads_the_clock():
    put(codex(10.0, 300, time.time() + HOUR, time.time() - MIN))
    result = api.limits(harness="codex", pull=False)
    assert abs(result.now - time.time()) < 60
    assert row(result, "codex", "5h").state == "fresh"


@pytest.mark.slow
@pytest.mark.timed
def test_limits_with_no_pull_answers_in_under_100_ms(fence):
    root = sessions_root(fence)
    for n in range(200):
        thread = f"0190c000-0000-7000-8000-{n:012d}"
        rollout(root, thread, NOW - DAY + n * MIN, [float(n % 50)]).write()
    found = [codex(float(n % 90), 300, NOW + HOUR, NOW - n * MIN) for n in range(2000)]
    put(*found)
    limits.limits(now=NOW, pull=False)
    best = min(_timed(lambda: limits.limits(now=NOW, pull=False)) for _ in range(5))
    assert best < 0.1


def _timed(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start
