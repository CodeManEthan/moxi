"""usage's status line segment: Claude's plan limits from the status line's own input,
labelled and stated by usage's window rules, and a module a renderer can load alone."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from moxi import modules
from moxi_usage import limits, readings, segment, windows
from moxi_usage.segment import from_input

NOW = 1793400000.0
LATER = NOW + 3600.0
EARLIER = NOW - 60.0

GREEN, YELLOW, RED, END = "\033[32m", "\033[33m", "\033[31m", "\033[0m"


def payload(**kinds):
    """A status line input whose `rate_limits` holds `kinds`, each `(percent, resets_at)`."""
    return {"session_id": "s-1", "cwd": "/w",
            "rate_limits": {kind: {"used_percentage": used, "resets_at": resets}
                            for kind, (used, resets) in kinds.items()}}


def child(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          capture_output=True, text=True, timeout=60, env=dict(os.environ))


# --- rows -------------------------------------------------------------------------------

def test_the_five_hour_and_seven_day_windows_show_their_percents():
    found = from_input(payload(five_hour=(42, LATER), seven_day=(61, LATER + 86400)), NOW)
    assert found.text == "5h:42% 7d:61%"
    assert found.rows == (("5h:42%", "low"), ("7d:61%", "warn"))
    assert found.band == "warn"
    assert found.dim is False


def test_a_percent_is_shown_as_a_whole_number_halves_up():
    assert from_input(payload(five_hour=(41.5, LATER)), NOW).text == "5h:42%"
    assert from_input(payload(five_hour=(42.4, LATER)), NOW).text == "5h:42%"
    assert from_input(payload(five_hour=(0, LATER)), NOW).text == "5h:0%"


def test_a_spend_limit_is_labelled_as_given_and_may_pass_100():
    found = from_input(payload(five_hour=(10, LATER), spend_limit=(64, LATER)), NOW)
    assert found.text == "5h:10% spend_limit:64%"
    over = from_input(payload(spend_limit=(130.2, LATER)), NOW)
    assert over.text == "spend_limit:130%"
    assert over.band == "alert"


def test_a_resets_at_in_the_past_shows_reset():
    found = from_input(payload(five_hour=(97, EARLIER), seven_day=(55, LATER)), NOW)
    assert found.text == "5h:reset 7d:55%"
    assert found.rows[0] == ("5h:reset", "low")


def test_a_resets_at_of_now_is_reset():
    assert from_input(payload(five_hour=(30, NOW)), NOW).text == "5h:reset"


def test_no_resets_at_shows_reset():
    assert from_input(payload(five_hour=(12, None)), NOW).text == "5h:reset"


def test_a_null_percent_is_left_out():
    found = from_input(payload(five_hour=(None, LATER), seven_day=(20, LATER)), NOW)
    assert found.text == "7d:20%"


def test_a_row_with_neither_percent_nor_reset_is_left_out():
    assert from_input(payload(five_hour=(None, None)), NOW) is None


@pytest.mark.parametrize("used", [True, "42", [42], float("nan"), float("inf")])
def test_a_percent_that_is_not_a_finite_number_is_left_out(used):
    assert from_input(payload(five_hour=(used, LATER)), NOW) is None


def test_a_scoped_week_is_labelled_by_usage_rule():
    # No such kind arrives from Claude today; the case holds usage's labelling rule.
    found = from_input(payload(seven_day_opus=(33, LATER)), NOW)
    assert found.text == "7d opus:33%"


def test_rows_keep_the_order_the_input_gives():
    found = from_input(payload(seven_day=(1, LATER), five_hour=(2, LATER)), NOW)
    assert found.text == "7d:1% 5h:2%"


# --- nothing to show ------------------------------------------------------------------

def test_no_rate_limits_shows_nothing():
    assert from_input({"session_id": "s-1", "cwd": "/w"}, NOW) is None


@pytest.mark.parametrize("value", [None, [], "five_hour", 3, {}])
def test_rate_limits_that_are_not_a_table_or_are_empty_show_nothing(value):
    assert from_input({"rate_limits": value}, NOW) is None


@pytest.mark.parametrize("value", [None, [], "x", 3])
def test_an_input_that_is_not_a_table_shows_nothing(value):
    assert from_input(value, NOW) is None


def test_an_entry_that_is_not_a_table_is_left_out():
    found = from_input({"rate_limits": {"five_hour": 42, "seven_day": {
        "used_percentage": 5, "resets_at": LATER}}}, NOW)
    assert found.text == "7d:5%"


def test_segment_by_session_id_shows_nothing():
    assert segment.segment("s-1", NOW) is None
    assert segment.segment("s-1") is None


def test_now_defaults_to_the_clock():
    far = 4_000_000_000.0
    assert from_input(payload(five_hour=(7, far))).text == "5h:7%"
    assert from_input(payload(five_hour=(7, 1.0))).text == "5h:reset"


# --- bands ----------------------------------------------------------------------------

@pytest.mark.parametrize(("used", "band", "colour"), [
    (49, "low", GREEN), (50, "warn", YELLOW), (79, "warn", YELLOW), (80, "alert", RED),
    (49.4, "low", GREEN), (49.5, "warn", YELLOW)])
def test_bands_turn_yellow_at_50_and_red_at_80(used, band, colour):
    found = from_input(payload(five_hour=(used, LATER)), NOW)
    assert found.band == band
    assert found.coloured() == f"{colour}{found.text}{END}"


def test_each_row_is_coloured_by_its_own_band():
    found = from_input(payload(five_hour=(85, LATER), seven_day=(20, LATER),
                               spend_limit=(60, EARLIER)), NOW)
    assert found.text == "5h:85% 7d:20% spend_limit:reset"
    assert found.coloured() == (f"{RED}5h:85%{END} {GREEN}7d:20%{END} "
                                f"{GREEN}spend_limit:reset{END}")
    assert found.band == "alert"


# --- the module -----------------------------------------------------------------------

def test_the_announcement_names_the_segment():
    from moxi_usage import module

    assert module.SEGMENT == "moxi_usage.segment"


def test_the_window_rules_are_the_ones_readings_and_limits_use():
    assert readings.claude_window is windows.claude_window
    assert readings.percent is windows.percent
    assert readings.PLAN_LIMIT is windows.PLAN_LIMIT
    assert limits.row_state is windows.row_state
    assert limits.label is windows.label
    assert limits.MAIN_LIMIT is windows.MAIN_LIMIT


def test_importing_the_segment_leaves_heavy_modules_out():
    proc = child('''
        import json, sys
        import moxi_usage.segment
        print(json.dumps(sorted(name for name in sys.modules if name.split(".")[0] in (
            "datetime", "logging", "dataclasses", "moxi_sessions", "sqlite3"))))
    ''')
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == []


def test_the_segment_works_under_the_render_hook():
    given = json.dumps(payload(five_hour=(42, LATER)))
    proc = child(f'''
        import json
        from moxi import render
        mod = render.load_segment("moxi_usage.segment")
        assert render.installed()
        print(mod.from_input(json.loads({given!r}), {NOW!r}).text)
    ''')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "5h:42%"


def test_the_core_reads_the_segment_from_the_announcement():
    found = modules.announced("usage")
    assert found.segment == "moxi_usage.segment"
