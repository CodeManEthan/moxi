"""`api.clock_records()`, the listing of the watcher's clock records that `moxi cache
timers` and cache's page both read, and the timers output it feeds."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from conftest import write_cache_config

from moxi_cache import api, cli, records

NOW = 1_800_000_000.0
A = "aaaaaaaa-1111-4111-8111-111111111111"
B = "bbbbbbbb-2222-4222-8222-222222222222"
C = "cccccccc-3333-4333-8333-333333333333"


def record(session, *, kind="warm", last_request=NOW - 100, ttl=3600, code=None,
           thresholds=True, **extra) -> dict:
    out = {"v": 1, "session": session, "harness": "claude", "pid": 4242, "kind": kind,
           "last_request": last_request, "ttl": ttl, "read_at": NOW - 5, "seen_at": NOW - 2,
           "code": code}
    if thresholds:
        out.update(warn_before=900, stale_after=30)
    out.update(extra)
    return out


def put(name: str, content) -> Path:
    directory = records.record_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


@pytest.fixture
def fixture_records() -> list[dict]:
    """Three valid records, written out of order, beside files that are not records."""
    written = [
        record(C, kind="unknown", last_request=None, ttl=None, code="no-transcript"),
        record(A),
        record(B, kind="cold", last_request=NOW - 4000, thresholds=False),
    ]
    for rec in written:
        put(f"{rec['session']}.json", rec)
    put("dddddddd-4444-4444-8444-444444444444.json", record("d", v=2))
    put("eeeeeeee-5555-4555-8555-555555555555.json", "{not json")
    put("ffffffff-6666-4666-8666-666666666666.txt", record("f"))
    return sorted(written, key=lambda rec: rec["session"])


# --- clock_records ----------------------------------------------------------------------

def test_clock_records_lists_the_valid_records_as_written_sorted_by_session(fixture_records):
    assert api.clock_records() == fixture_records


def test_a_record_without_thresholds_is_still_listed(fixture_records):
    found = {rec["session"]: rec for rec in api.clock_records()}
    assert "warn_before" not in found[B] and "stale_after" not in found[B]


def test_clock_records_sorts_by_session_not_by_file_name():
    put("z.json", record("a"))
    put("a.json", record("z"))
    assert [rec["session"] for rec in api.clock_records()] == ["a", "z"]


def test_no_directory_gives_no_records():
    assert not records.record_dir().exists()
    assert api.clock_records() == []


def test_an_unreadable_directory_gives_no_records(monkeypatch):
    def refuse(self):
        raise PermissionError(13, "Permission denied")

    put(f"{A}.json", record(A))
    monkeypatch.setattr(Path, "iterdir", refuse)
    assert api.clock_records() == []


def test_clock_records_is_in_interface_1():
    assert "clock_records" in api.__all__


# --- moxi cache timers, unchanged ---------------------------------------------------------
#
# The text and JSON below are what `moxi cache timers` printed over these records before
# its listing moved into the API.

TIMERS_TEXT = """\
subscriptions: 1
  before (config): at -900s from expiry, until +0s, every 120s
      run <exe> --session {session}
live sessions: 3
  aaaaaaaa-1111-4111-8111-111111111111 (pid 4242): warm, last request 1m40s ago, lifetime 1h0m
      before: asks when the window opens, in 43m20s (08:43:20)
  bbbbbbbb-2222-4222-8222-222222222222 (pid 4242): cold, last request 1h6m ago, lifetime 1h0m
      before: window closed 6m40s ago (07:53:20)
  cccccccc-3333-4333-8333-333333333333 (pid 4242): unknown
      before: no clock to count from (no request read yet)
log: <log>
"""


def _ask(state, next_at, opens_at, closes_at):
    return {"subscriber": "before", "state": state, "next_at": next_at, "opens_at": opens_at,
            "closes_at": closes_at, "last_ask": None, "missing_env": []}


TIMERS_JSON = {
    "now": NOW,
    "config_error": None,
    "subscriptions": [{"name": "before", "source": "config", "at": -900.0, "until": 0.0,
                       "every": 120.0, "requires_env": {},
                       "run": ["<exe>", "--session", "{session}"], "argv0": "<exe>"}],
    "refused": [],
    "sessions": [
        {"session": A, "pid": 4242, "kind": "warm", "age": 100.0, "ttl": 3600,
         "seen_at": NOW - 2, "asks": [_ask("waiting", NOW + 2600, NOW + 2600, NOW + 3500)]},
        {"session": B, "pid": 4242, "kind": "cold", "age": 4000.0, "ttl": 3600,
         "seen_at": NOW - 2, "asks": [_ask("closed", None, NOW - 1300, NOW - 400)]},
        {"session": C, "pid": 4242, "kind": "unknown", "age": None, "ttl": None,
         "seen_at": NOW - 2, "asks": [_ask("no-clock", None, None, None)]},
    ],
    "log": "<log>",
}


@pytest.fixture
def frozen(monkeypatch, own_roots, tmp_path):
    """The clock stopped at NOW in UTC, and one subscription in cache's config."""
    exe = tmp_path / "bin" / "verb"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    write_cache_config(own_roots, f"""
[[timer.subscriber]]
name = "before"
at = -900
until = 0
every = 120
run = ["{exe}", "--session", "{{session}}"]
""")
    zone = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    monkeypatch.setattr(cli.time, "time", lambda: NOW)
    try:
        yield exe
    finally:
        if zone is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = zone
        time.tzset()


def run_timers(capsys, frozen, *argv) -> str:
    assert cli.main(["timers", *argv], prog="moxi cache") == 0
    out = capsys.readouterr().out
    log = str(cli._timer.log_path())
    return out.replace(log, "<log>").replace(str(frozen), "<exe>")


def test_timers_text_is_unchanged(fixture_records, frozen, capsys):
    assert run_timers(capsys, frozen) == TIMERS_TEXT


def test_timers_json_is_unchanged(fixture_records, frozen, capsys):
    assert run_timers(capsys, frozen, "--json") == json.dumps(TIMERS_JSON, indent=2) + "\n"


def test_timers_lists_what_clock_records_gives(monkeypatch, frozen, capsys):
    given = [record(B, kind="cold", last_request=NOW - 4000)]
    monkeypatch.setattr(api, "clock_records", lambda: given)
    doc = json.loads(run_timers(capsys, frozen, "--json"))
    assert [s["session"] for s in doc["sessions"]] == [B]

