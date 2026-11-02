"""The `usage` and `codex_limits` listeners, called with plain event objects shaped as
turns hands them: `usage` with `usage` (the `$.session.usage()` value) and
`received_at`, `codex_limits` with `payload`, `received_at` and `kind`."""

import json
import subprocess
import sys
from types import SimpleNamespace

from moxi_usage import listeners, readings

NOW = 1793400000.0
HOUR = 3600.0

SESSION_USAGE = {
    "startedAt": 1793355388172, "context": {"tokens": 18850, "window": 1000000, "percent": 2},
    "rateLimits": [
        {"kind": "five_hour", "percentUsed": 61, "resetsAt": "2026-10-31T00:00:00.000Z"},
        {"kind": "seven_day", "percentUsed": 46, "resetsAt": "2026-11-02T05:00:00.000Z"}],
    "cost": {"usd": 0.00164772}}


def usage_event(usage=SESSION_USAGE, received_at=NOW - 5, session_id="s1", harness="claude"):
    return SimpleNamespace(event="usage", harness=harness, session_id=session_id, env={},
                           usage=usage, received_at=received_at)


def codex_event(payload, received_at=NOW - 5, kind="updated"):
    return SimpleNamespace(event="codex_limits", harness="codex", session_id="thread-1",
                           env={}, payload=payload, received_at=received_at, kind=kind)


def stored():
    if not readings.path().exists():
        return []
    conn = readings.open_store()
    try:
        return [readings.Reading(*row) for row in conn.execute(
            f"SELECT {', '.join(readings.Reading._fields)} FROM readings ORDER BY id")]
    finally:
        conn.close()


def test_a_usage_event_stores_claudes_windows_at_its_receive_time():
    assert listeners.usage(usage_event()) is None
    found = {r.slot: r for r in stored()}
    assert set(found) == {"five_hour", "seven_day"}
    five = found["five_hour"]
    assert (five.limit_id, five.window_minutes, five.used_percent, five.observed_at) == (
        "plan", 300, 61.0, NOW - 5)
    assert (five.source, five.session_id) == ("session-usage", "s1")


def test_the_same_result_again_is_not_stored_again():
    listeners.usage(usage_event())
    listeners.usage(usage_event(received_at=NOW))
    assert len(stored()) == 2


def test_an_empty_or_strange_usage_event_stores_nothing():
    listeners.usage(usage_event(usage={**SESSION_USAGE, "rateLimits": []}))
    listeners.usage(usage_event(usage={"context": {}}))
    listeners.usage(usage_event(usage="not a dict"))
    listeners.usage(usage_event(received_at=None))
    listeners.usage(usage_event(harness="codex"))
    listeners.usage(SimpleNamespace())
    assert stored() == []


def test_a_usage_listener_that_cannot_store_fails_open(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(readings, "add_claude", broken)
    assert listeners.usage(usage_event()) is None
    assert "not stored" in caplog.text


def app_server_payload(used=12.0):
    return {"rateLimits": {"limitId": "codex", "planType": "pro",
                           "primary": {"usedPercent": used, "windowDurationMins": 300,
                                       "resetsAt": NOW + HOUR},
                           "secondary": {"usedPercent": 30.0, "windowDurationMins": 10080,
                                         "resetsAt": NOW + 3 * 86400}}}


def test_a_codex_limits_event_stores_app_server_readings():
    assert listeners.codex_limits(codex_event(app_server_payload())) is None
    found = stored()
    assert {(r.window_minutes, r.used_percent, r.source, r.observed_at) for r in found} == {
        (300, 12.0, "app-server", NOW - 5), (10080, 30.0, "app-server", NOW - 5)}
    listeners.codex_limits(codex_event(app_server_payload(), kind="read"))
    assert len(stored()) == 2


def test_a_codex_limits_event_sessions_cannot_read_fails_open(caplog):
    assert listeners.codex_limits(codex_event({"rateLimits": None})) is None
    listeners.codex_limits(codex_event("nope"))
    listeners.codex_limits(codex_event(app_server_payload(), received_at="later"))
    assert stored() == []
    assert "not stored" in caplog.text


def test_importing_the_listeners_loads_neither_sessions_nor_sqlite_nor_logging(fence):
    code = ("import sys, moxi_usage.listeners; "
            "print(sorted(m for m in ('sqlite3', 'moxi_sessions', 'moxi_usage.readings', "
            "'json', 'logging') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=dict(fence.env), check=True).stdout
    assert json.loads(out.replace("'", '"')) == []



def test_usages_rows_for_turns_name_each_event_and_the_marks_floor():
    import importlib

    assert listeners.LISTENERS == (
        (("after-tool",), "moxi_usage.mark:on_tool", {"floor": "moxi_usage.mark:floor"}),
        (("gone",), "moxi_usage.mark:gone"),
        (("usage",), "moxi_usage.listeners:usage"),
        (("codex_limits",), "moxi_usage.listeners:codex_limits"))
    for row in listeners.LISTENERS:
        for target in (row[1], *(row[2].values() if len(row) > 2 else ())):
            name, _, attr = target.partition(":")
            assert callable(getattr(importlib.import_module(name), attr))
