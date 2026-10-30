"""`moxi usage limits [--harness H] [--no-pull] [--json]`, over readings put in the store
and a fake turns pull."""

import json
import time
from types import SimpleNamespace

import pytest

from moxi_usage import cli, limits, readings
from moxi_usage.readings import Reading

from conftest import write_config

MIN = 60.0
HOUR = 3600.0


@pytest.fixture(autouse=True)
def turns(monkeypatch):
    state = SimpleNamespace(available=True, calls=[])

    def pull(*, timeout):
        state.calls.append(timeout)
        return None

    monkeypatch.setattr(limits, "_turns_available", lambda: state.available)
    monkeypatch.setattr(limits, "_puller", lambda: (pull, None))
    return state


def put(*found):
    conn = readings.open_store()
    try:
        readings.store(conn, found, now=time.time(), days=8)
    finally:
        conn.close()


def fill():
    now = time.time()
    put(Reading("claude", "plan", 300, "five_hour", 61.0, now + 2 * HOUR, now - 2 * MIN,
                "session-usage", "s1"),
        Reading("claude", "plan", 10080, "seven_day", 46.0, now + 50 * HOUR, now - 5 * MIN,
                "session-usage", "s1"),
        *(Reading("codex", "codex", 300, "primary", p, now + 3 * HOUR, now - m * MIN,
                  "rollout", "t1") for p, m in ((40.0, 30), (45.0, 20), (50.0, 15))),
        Reading("codex", "codex", 10080, "secondary", 12.0, now - HOUR, now - 15 * MIN,
                "rollout", "t1"))


def test_limits_prints_a_line_per_harness_with_the_oldest_age(capsys):
    fill()
    assert cli.main(["limits"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("claude ")
    assert "5h 61% used, resets " in lines[0] and "7d 46% used, resets " in lines[0]
    assert "   as of 5m0" in lines[0]
    assert lines[1].startswith("codex ")
    assert "5h 50% used, resets " in lines[1] and ", full by " in lines[1]
    assert "7d reset since the last reading" in lines[1]
    assert lines[2].strip() == "no newer reading: turns found no Codex host or app-server " \
                               "to ask, or it did not answer in time"


def test_no_pull_and_one_harness(capsys, turns):
    fill()
    assert cli.main(["limits", "--harness", "codex", "--no-pull"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("codex ")
    assert turns.calls == []


def test_turns_absent_says_so_on_claudes_line(capsys, turns):
    turns.available = False
    assert cli.main(["limits", "--harness", "claude"]) == 0
    out = capsys.readouterr().out
    assert out.count("unavailable (turns is not installed)") == 2


def test_limits_json_is_one_object(capsys):
    fill()
    assert cli.main(["limits", "--json", "--no-pull"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [(r["harness"], r["label"], r["state"]) for r in data["rows"]] == [
        ("claude", "5h", "fresh"), ("claude", "7d", "fresh"),
        ("codex", "5h", "fresh"), ("codex", "7d", "reset")]
    assert data["rows"][2]["fit"] == 3 and data["rows"][2]["full_at"] is not None
    assert data["pull"] is None


def test_a_bad_config_exits_2(capsys):
    write_config("[limits]\nhistory_days = 'eight'\n")
    assert cli.main(["limits"]) == 2
    assert "history_days" in capsys.readouterr().err


def test_an_unknown_harness_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as raised:
        cli.main(["limits", "--harness", "opencode"])
    assert raised.value.code == 2


@pytest.mark.parametrize("seconds, text", [(0, "0s"), (59.9, "59s"), (245, "4m05s"),
                                           (3840, "1h04m"), (273600, "3d04h"), (-3, "0s")])
def test_ages_round_down(seconds, text):
    assert cli.format_age(seconds) == text
