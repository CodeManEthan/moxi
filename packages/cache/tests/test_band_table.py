"""The band table's segment half: every case against `segment.from_record`. The chip's
browser test runs the same cases."""

from __future__ import annotations

import pytest
from band_cases import CASES, NOW, shifted

from moxi_cache import segment

KINDS_WITH_NO_BAND = ("fresh", "no-cache", "unknown")


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_segment_shows_what_the_table_says(case):
    record, shows = case["record"], case["shows"]
    found = segment.from_record(record, NOW)
    if shows in segment.BANDS:
        assert (found.band, found.dim) == (shows, case["dim"])
    else:
        assert found is None
        word = record["kind"] if record["kind"] in KINDS_WITH_NO_BAND else "no reading"
        assert shows == word and case["dim"] is False


def test_the_table_holds_every_branch():
    shows = {case["shows"] for case in CASES}
    assert shows == {"warm", "near", "cold", "no reading", *KINDS_WITH_NO_BAND}
    assert any(case["dim"] for case in CASES) and not all(case["dim"] for case in CASES)
    records = [case["record"] for case in CASES]
    assert any(r["last_request"] is not None and r["last_request"] > NOW for r in records)
    # Only a case whose ttl is under its warn_before fails when the age isn't held at 0.
    assert any(r["last_request"] is not None and r["last_request"] > NOW
               and r["warn_before"] > r["ttl"] for r in records)
    assert any(r["kind"] not in ("warm", "cold", *KINDS_WITH_NO_BAND) for r in records)
    assert any(r["last_request"] is None and r["kind"] in ("warm", "cold") for r in records)
    assert any(r["ttl"] is None and r["kind"] in ("warm", "cold") for r in records)
    for key in ("warn_before", "stale_after"):
        assert any(key not in r for r in records)


def test_the_sessions_are_distinct():
    sessions = [case["record"]["session"] for case in CASES]
    assert len(set(sessions)) == len(sessions)


def test_a_shifted_case_shows_the_same_at_its_own_time():
    later = NOW + 12_345.5
    for case in CASES:
        moved = segment.from_record(shifted(case["record"], later), later)
        here = segment.from_record(case["record"], NOW)
        assert (moved and (moved.band, moved.dim)) == (here and (here.band, here.dim))
