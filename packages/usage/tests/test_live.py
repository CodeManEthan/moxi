"""Live parity: this machine's per-day tokens from usage's `daily`, against a reference.

A `live` test, run by hand: `MOXI_TEST_LIVE=1` and `MOXI_USAGE_PARITY_REFERENCE=<file>`
in the environment pytest starts in, then `pytest -m live -s`. Without the reference it
is skipped. It reads the real harness roots, the ones the harness rules give in that
environment (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `XDG_DATA_HOME`, `HOME`), and nothing
else of the machine: usage's index and every other write stay in the test's tmp, under
the fence.

The reference is a JSON file made outside this repository by another counter of the same
corpus:

    {"tz": "UTC",                  # the zone its days are cut in
     "harnesses": ["claude", "codex"],
     "through": 1791318903.7,      # its newest request; that day and later are not compared
     "days": {"2026-07-01": {"claude": {"requests": 812, "fresh_input": 9120,
                                        "cache_read": 41000000, "cache_write": 1900000,
                                        "output": 310000},
                             "codex": {...}}}}

`fresh_input` is the prompt less what was read from or written to the cache. Every day
from the reference's first up to the day of `through` is compared, by harness and token
class. `harnesses` (optional; every harness when absent) names the ones the reference
counted: a harness it never read is shown, not compared. The test prints each day that
differs, with the difference (usage less the reference), and fails when any does.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from moxi.paths import Layout
from moxi_sessions import api as sessions
from moxi_usage import index, reports, store

pytestmark = pytest.mark.live

#: The variable naming the reference file.
REFERENCE_ENV = "MOXI_USAGE_PARITY_REFERENCE"
CLASSES = ("requests", "fresh_input", "cache_read", "cache_write", "output")
HARNESSES = ("claude", "codex", "opencode")

#: The environment pytest started in, read at import, before any test's fence empties it.
_STARTED_IN = dict(os.environ)


def _day(epoch: float, zone: dt.tzinfo) -> str:
    return dt.datetime.fromtimestamp(epoch, zone).strftime("%Y-%m-%d")


def _days(first: str, stop: str) -> list[str]:
    day, end, out = dt.date.fromisoformat(first), dt.date.fromisoformat(stop), []
    while day < end:
        out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def test_per_day_tokens_match_the_reference_on_this_machine(fence):
    reference_path = _STARTED_IN.get(REFERENCE_ENV)
    if not reference_path:
        pytest.skip(f"{REFERENCE_ENV} names no reference file")
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    tz = reference.get("tz", "UTC")
    zone = dt.timezone.utc if tz.upper() == "UTC" else ZoneInfo(tz)
    roots = {h: sessions.root(h, _STARTED_IN) for h in HARNESSES}
    if not any(path.exists() for path in roots.values()):
        pytest.skip("no harness root on this machine")
    config = Layout("sessions").config_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("".join(f'[{h}]\n{key} = {json.dumps(str(roots[h]))}\n'
                              for h, key in (("claude", "projects"), ("codex", "sessions"),
                                             ("opencode", "database"))), encoding="utf-8")

    started = time.perf_counter()
    result = index.pass_(projects=None)
    took = time.perf_counter() - started
    print(f"\npass over the real roots: {took:.1f} s, read {result.read}, excluded "
          f"{result.excluded}, held {result.held}, unreadable {len(result.unreadable)}")
    assert store.index_path().is_relative_to(fence.short)

    first = min(reference["days"])
    stop = _day(float(reference["through"]), zone)
    days = _days(first, stop)
    conn = store.connect(create=False)
    try:
        ours: dict[str, dict[str, dict[str, int]]] = {}
        for harness in HARNESSES:
            report = reports.daily(conn, since=first, until=days[-1], harness=harness, tz=tz)
            for row in report.rows:
                cell = ours.setdefault(row["day"], {}).setdefault(
                    harness, dict.fromkeys(CLASSES, 0))
                for name in CLASSES:
                    cell[name] += row[name] or 0
    finally:
        conn.close()

    counted = tuple(reference.get("harnesses", HARNESSES))
    differ: list[str] = []
    totals = {h: dict.fromkeys(CLASSES, 0) for h in HARNESSES}
    deltas = {h: dict.fromkeys(CLASSES, 0) for h in HARNESSES}
    for day in days:
        theirs = reference["days"].get(day, {})
        for harness in HARNESSES:
            mine = ours.get(day, {}).get(harness, dict.fromkeys(CLASSES, 0))
            other = theirs.get(harness, {})
            delta = {name: mine[name] - other.get(name, 0) for name in CLASSES}
            for name in CLASSES:
                totals[harness][name] += mine[name]
                deltas[harness][name] += delta[name]
            if harness in counted and any(delta.values()):
                differ.append(f"{day} {harness:<8} " + " ".join(
                    f"{name} {delta[name]:+,} ({other.get(name, 0):,})" for name in CLASSES))
    print(f"compared {len(days)} days, {days[0]} to {days[-1]} ({tz}); "
          f"{len(differ)} day-harness cells differ")
    for harness in HARNESSES:
        what = "less the reference" if harness in counted else "(not in the reference)"
        mine = " ".join(f"{n} {v:,}" for n, v in totals[harness].items())
        delta = " ".join(f"{n} {v:+,}" for n, v in deltas[harness].items())
        print(f"{harness:<8} usage {mine}; {what} {delta}")
    print("\n".join(differ))
    assert not differ
