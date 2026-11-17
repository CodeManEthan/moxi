"""The idle gate, its gauge and the `timed` entries in `durations.json`.

Each test runs a small package of timed tests in a pytest of its own, in process, against
a stand-in machine: its clock, `/proc/stat`'s counters, this process's CPU time and the
load average are the stand-in's, and they move only when the gate pauses or a test burns
time through it. Nothing waits for real and nothing reads how busy this machine is, but
the one test under workers, whose line no machine reaches.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable

import pytest

from moxi_dev import testing
from moxi_dev.testing import Gauge, GaugeReading, stat_counts

#: Clock ticks a second on the stand-in machine.
HZ = 100


class Machine:
    """A stand-in machine for the gauge.

    `load` gives other work, in CPUs, at a time (seconds since the machine started);
    `advance` spends time at that load, or at the `other` work, this process's `own` CPU
    time and the `iowait` it is given. The gate's pauses go through it and are kept in
    `pauses`. With `has_stat` false it has no `/proc/stat`, and with `has_load` false no
    load average either."""

    def __init__(self, load: Callable[[float], float] = lambda at: 0.0, *, cpus: int = 16,
                 has_stat: bool = True, has_load: bool = True) -> None:
        self.load = load
        self.cpus = cpus
        self.has_stat = has_stat
        self.has_load = has_load
        self.started = self.at = 1_800_000_000.0
        self.busy = self.idle = self.iowait = 0
        self.own_s = 0.0
        self.pauses: list[float] = []
        self.ran: list[str] = []

    def since(self) -> float:
        return self.at - self.started

    def advance(self, seconds: float, *, other: float | None = None, own: float = 0.0,
                iowait: float = 0.0) -> None:
        other = self.load(self.since()) if other is None else other
        ticks = seconds * HZ
        self.busy += round((other + own) * ticks)
        self.iowait += round(iowait * ticks)
        self.idle += round((self.cpus - other - own - iowait) * ticks)
        self.own_s += own * seconds
        self.at += seconds

    def pause(self, seconds: float) -> None:
        self.pauses.append(round(seconds, 6))
        self.advance(seconds)

    def stat(self) -> str | None:
        if not self.has_stat:
            return None
        # Guest time is inside user's, so its fields must not count twice.
        lines = [f"cpu  {self.busy} 0 0 {self.idle} {self.iowait} 0 0 0 {self.busy} 0"]
        lines += [f"cpu{n} 0 0 0 0 0 0 0 0 0 0" for n in range(self.cpus)]
        return "\n".join([*lines, "intr 1 2 3", "ctxt 4", ""])

    def load_average(self) -> float | None:
        return self.load(self.since()) if self.has_load else None


@pytest.fixture
def machine(monkeypatch) -> Machine:
    """A quiet stand-in machine in place of the gauge's sources; a test changes its
    load or takes its sources away before the inner run."""
    stand_in = Machine()
    monkeypatch.setattr(testing, "_read_proc_stat", lambda: stand_in.stat())
    monkeypatch.setattr(testing, "_load_average", lambda: stand_in.load_average())
    monkeypatch.setattr(testing, "_own_cpu_s", lambda: stand_in.own_s)
    monkeypatch.setattr(testing, "_ticks_per_s", lambda: HZ)
    monkeypatch.setattr(testing, "_now", lambda: stand_in.at)
    monkeypatch.setattr(testing, "_pause", stand_in.pause)
    # The inner tests reach it by importing it.
    monkeypatch.setitem(sys.modules, "fake_machine", stand_in)
    return stand_in


@pytest.fixture
def gated(pytester, machine, monkeypatch):
    """Run a package of tests under the gate: `gated(source, line=2.0, wait_s=300.0)`
    returns pytest's result and the run's `durations.json`. The deadline is the
    machine's now plus `wait_s`, set on the first run and kept for the next."""
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    runs = []

    def run(source: str, *, line: float = 2.0, wait_s: float = 300.0):
        if not runs:
            monkeypatch.setenv(testing.IDLE_ENV, f"{line}@{machine.at + wait_s}")
        runs.append(source)
        name = f"test_run{len(runs)}"
        pytester.makepyfile(**{name: source})
        out = pytester.path / f"{name}.json"
        monkeypatch.setenv(testing.DURATIONS_ENV, str(out))
        result = pytester.runpytest("-p", "no:cacheprovider", "-rs", f"{name}.py")
        return result, json.loads(out.read_text())

    return run


def timed(data: dict) -> dict[str, dict]:
    """Each test's `timed` entry, by the test's name."""
    return {nodeid.rsplit("::", 1)[1]: entry["timed"]
            for nodeid, entry in data["tests"].items() if "timed" in entry}


# --- the gauge -------------------------------------------------------------------------

def test_stat_counts_leave_idle_iowait_and_guest_time_out_of_busy():
    text = ("cpu  100 20 30 400 50 6 7 8 90 10\n"
            "cpu0 50 10 15 200 25 3 3 4 45 5\n"
            "cpu1 50 10 15 200 25 3 4 4 45 5\n"
            "intr 1 2 3\n")
    assert stat_counts(text) == (100 + 20 + 30 + 6 + 7 + 8, 171 + 400 + 50, 2)
    with pytest.raises(ValueError, match="no line for all CPUs"):
        stat_counts("intr 1 2 3\n")


def test_other_work_is_the_busy_rise_less_this_process_per_cpu(monkeypatch):
    monkeypatch.setattr(testing, "_ticks_per_s", lambda: HZ)
    gauge = Gauge("stat")
    start = GaugeReading(at=0.0, busy=1_000, total=10_000, cpus=16, own_s=1.0)
    # One second on 16 CPUs: 1600 ticks, 400 of them busy, 100 of those this process's.
    end = GaugeReading(at=1.0, busy=1_400, total=11_600, cpus=16, own_s=2.0)
    assert gauge.other(start, end) == pytest.approx(3.0)
    # This process alone, or no tick at all, is no other work.
    assert gauge.other(start, GaugeReading(at=1.0, busy=1_100, total=11_600, cpus=16,
                                           own_s=2.5)) == 0.0
    assert gauge.other(start, start) == 0.0


def test_the_load_average_gauge_judges_the_higher_reading():
    gauge = Gauge("loadavg")
    assert gauge.other(GaugeReading(at=0.0, load=1.5), GaugeReading(at=9.0, load=2.5)) == 2.5
    assert gauge.describe(2.5) == "load average 2.50"
    assert Gauge("stat").describe(2.5) == "other work 2.50 CPUs"


# --- the gate --------------------------------------------------------------------------

QUIET = """
import pytest
import fake_machine

@pytest.mark.timed
def test_quiet():
    # This process's own work and I/O waits are not other work.
    fake_machine.advance(1.0, other=0.0, own=4.0, iowait=5.0)
"""


def test_a_quiet_machine_opens_the_gate_after_one_sample(gated, machine):
    result, data = gated(QUIET)
    result.assert_outcomes(passed=1)
    assert machine.pauses == [0.5]
    assert timed(data)["test_quiet"] == {
        "outcome": "passed", "clock": "wall", "gauge": "stat", "other": 0.0, "line": 2.0,
        "reading": None, "budget": None, "unit": None, "failure": None, "waited_s": 0.0}
    assert data["gate"] == {"gauge": "stat", "line": 2.0, "deadline": machine.started + 300,
                            "first_sample": 0.0, "waited_s": 0.0}


def test_over_the_line_it_samples_every_10_s_and_the_window_starts_at_the_last_sample(
        gated, machine):
    machine.load = lambda at: 3.0 if at < 20 else 0.0
    result, data = gated(QUIET)
    result.assert_outcomes(passed=1)
    # Samples at 0 s and 10 s read 3 CPUs; the one at 20 s reads none.
    assert machine.pauses == [0.5, 9.5, 0.5, 9.5, 0.5]
    entry = timed(data)["test_quiet"]
    assert entry["outcome"] == "passed"
    assert entry["other"] == 0.0 and entry["waited_s"] == 20.0
    assert data["gate"]["first_sample"] == 3.0 and data["gate"]["waited_s"] == 20.0


THREE = """
import pytest
import fake_machine

@pytest.mark.timed
def test_one():
    fake_machine.ran.append("one")

@pytest.mark.timed
def test_two():
    fake_machine.ran.append("two")

@pytest.mark.timed
def test_three():
    fake_machine.ran.append("three")
"""


def test_past_the_deadline_every_later_test_is_marked_after_its_one_sample(gated, machine):
    machine.load = lambda at: 3.0
    result, data = gated(THREE, wait_s=30.0)
    result.assert_outcomes(skipped=3)
    assert machine.ran == []
    # The first test samples at 0, 10, 20 and 30 s; the next would start past the
    # deadline. Each later test takes one sample and waits for no other.
    assert machine.pauses == [0.5, 9.5, 0.5, 9.5, 0.5, 9.5, 0.5, 0.5, 0.5]
    entries = timed(data)
    assert {entry["outcome"] for entry in entries.values()} == {"not measured"}
    assert entries["test_one"]["waited_s"] == 30.0 and entries["test_one"]["other"] == 3.0
    assert entries["test_two"]["waited_s"] == 0.0
    out = result.stdout.str()
    assert ("not measured: machine busy, other work 3.00 CPUs at or over the line 2, "
            "waited 30 s") in out
    # pytest folds the two later tests' one reason into one line.
    assert "SKIPPED [2]" in out and "waited 0 s" in out
    assert data["gate"]["waited_s"] == 30.0


ONE = """
import pytest
import fake_machine

@pytest.mark.timed
def test_alone():
    fake_machine.ran.append("alone")
"""


def test_one_deadline_spans_two_sessions(gated, machine):
    machine.load = lambda at: 3.0
    first, _ = gated(ONE, wait_s=15.0)
    first.assert_outcomes(skipped=1)
    assert machine.pauses == [0.5, 9.5, 0.5]
    machine.pauses.clear()
    second, data = gated(ONE)
    second.assert_outcomes(skipped=1)
    assert machine.pauses == [0.5]
    assert timed(data)["test_alone"]["waited_s"] == 0.0
    assert machine.ran == []


PAIR = """
import pytest
import fake_machine

@pytest.fixture
def gap():
    fake_machine.advance(GAP_S)

@pytest.mark.timed
def test_first():
    fake_machine.advance(1.0, other=BURN)

@pytest.mark.timed
def test_second(gap):
    fake_machine.advance(1.0)
"""


@pytest.mark.parametrize(("gap_s", "burn", "pauses"), [
    (0.0, 0.0, [0.5]),
    (1.0, 0.0, [0.5]),
    (1.5, 0.0, [0.5, 0.5]),
    (0.0, 3.0, [0.5, 0.5])],
    ids=["right-after", "a-second-after", "later", "after-a-busy-window"])
def test_a_test_right_after_one_under_the_line_carries_on_its_window(
        gated, machine, gap_s, burn, pauses):
    _, data = gated(PAIR.replace("GAP_S", repr(gap_s)).replace("BURN", repr(burn)))
    assert machine.pauses == pauses
    second = timed(data)["test_second"]
    assert second["outcome"] == "passed" and second["other"] == 0.0


def test_a_carried_window_starts_where_the_last_one_ended(gated, machine):
    # The first test's window reads 1.6 CPUs, under the line; the second's starts at its
    # end, so the burn inside the first is not in it.
    _, data = gated(PAIR.replace("GAP_S", "0.0").replace("BURN", "2.4"))
    assert machine.pauses == [0.5]
    entries = timed(data)
    assert entries["test_first"]["other"] == pytest.approx(1.6)
    assert entries["test_second"]["other"] == 0.0


BUSY_INSIDE = """
import pytest
import fake_machine

@pytest.mark.timed
def test_passes_busy():
    fake_machine.advance(2.0, other=2.5)

@pytest.mark.timed
def test_fails_busy():
    fake_machine.advance(2.0, other=2.5)
    assert False, "over its budget"

@pytest.mark.timed
def test_after():
    fake_machine.advance(1.0)
"""


def test_other_work_at_the_line_over_a_window_voids_a_pass_and_a_failure_alike(gated,
                                                                             machine):
    result, data = gated(BUSY_INSIDE)
    # Each gate sample reads 0 CPUs, before and after; inside, the window of 2.5 s holds
    # 5 CPU-seconds of other work: 2.0 CPUs, at the line.
    result.assert_outcomes(passed=1, skipped=2)
    assert machine.pauses == [0.5, 0.5, 0.5]
    entries = timed(data)
    assert entries["test_passes_busy"]["outcome"] == "not measured"
    assert entries["test_passes_busy"]["other"] == 2.0
    assert entries["test_passes_busy"]["failure"] is None
    assert entries["test_fails_busy"]["outcome"] == "not measured"
    assert "over its budget" in entries["test_fails_busy"]["failure"]
    assert entries["test_after"]["outcome"] == "passed"
    assert {name: data["tests"][f"test_run1.py::{name}"]["outcome"]
            for name in entries} == {"test_passes_busy": "skipped",
                                     "test_fails_busy": "skipped", "test_after": "passed"}
    out = result.stdout.str()
    assert out.count("not measured: machine busy, other work 2.00 CPUs over the test's "
                     "window, at or over the line 2, waited 0 s") == 2
    assert "over its budget" not in out


FAILS = """
import pytest
import fake_machine

@pytest.mark.timed
def test_fails_quiet():
    fake_machine.advance(1.0, other=1.0)
    assert False, "over its budget"
"""


def test_a_failure_under_the_line_counts(gated, machine):
    result, data = gated(FAILS)
    result.assert_outcomes(failed=1)
    entry = timed(data)["test_fails_quiet"]
    assert entry["outcome"] == "failed" and "over its budget" in entry["failure"]
    assert entry["other"] == pytest.approx(1.0 / 1.5, abs=1e-3)


def test_with_no_proc_stat_the_gauge_is_the_load_average(gated, machine):
    machine.has_stat = False
    machine.load = lambda at: 3.0 if at < 10 else 0.5
    result, data = gated(THREE)
    result.assert_outcomes(passed=3)
    # The load average is read, not sampled over half a second: one wait of 10 s, then
    # each later test carries on the window before it.
    assert machine.pauses == [10.0]
    entries = timed(data)
    assert {entry["gauge"] for entry in entries.values()} == {"loadavg"}
    assert entries["test_one"]["other"] == 0.5 and entries["test_one"]["waited_s"] == 10.0
    assert data["gate"]["gauge"] == "loadavg" and data["gate"]["first_sample"] == 3.0


def test_the_load_average_gauge_names_its_reading_when_busy(gated, machine):
    machine.has_stat = False
    machine.load = lambda at: 3.0
    result, _ = gated(ONE, wait_s=0.0)
    result.assert_outcomes(skipped=1)
    assert machine.pauses == []
    assert ("not measured: machine busy, load average 3.00 at or over the line 2, "
            "waited 0 s") in result.stdout.str()


def test_with_neither_gauge_a_timed_test_is_not_measured(gated, machine):
    machine.has_stat = machine.has_load = False
    result, data = gated(ONE)
    result.assert_outcomes(skipped=1)
    assert machine.ran == [] and machine.pauses == []
    assert "not measured: no gauge on this system" in result.stdout.str()
    entry = timed(data)["test_alone"]
    assert entry["outcome"] == "not measured"
    assert entry["gauge"] == "none" and entry["other"] is None
    assert data["gate"]["gauge"] == "none" and data["gate"]["first_sample"] is None


# --- durations.json and timed_reading --------------------------------------------------

REPORTED = """
import pytest
import fake_machine

@pytest.fixture
def unavailable():
    pytest.skip("needs a tool this machine lacks")

@pytest.fixture
def breaks_after():
    yield
    raise RuntimeError("teardown broke")

@pytest.mark.timed
def test_in_process(timed_reading):
    timed_reading(4.2, 9, "ms")

@pytest.mark.timed
def test_in_a_child(timed_reading):
    timed_reading(10.5, 10.0, "ms", clock="child")

@pytest.mark.timed
def test_says_nothing():
    pass

@pytest.mark.timed
def test_skips_itself():
    pytest.skip("not on this platform")

@pytest.mark.timed
def test_skipped_at_setup(unavailable):
    pass

@pytest.mark.timed
def test_teardown_breaks(breaks_after):
    pass

def test_not_timed():
    pass
"""


def test_durations_carry_each_timed_entry_and_timed_reading_s_values(gated, machine):
    result, data = gated(REPORTED)
    result.assert_outcomes(passed=5, skipped=2, errors=1)
    entries = timed(data)
    assert set(entries) == {"test_in_process", "test_in_a_child", "test_says_nothing",
                            "test_skips_itself", "test_skipped_at_setup",
                            "test_teardown_breaks"}
    assert entries["test_in_process"] == {
        "outcome": "passed", "clock": "wall", "gauge": "stat", "other": 0.0, "line": 2.0,
        "reading": 4.2, "budget": 9.0, "unit": "ms", "failure": None, "waited_s": 0.0}
    child = entries["test_in_a_child"]
    assert (child["clock"], child["reading"], child["budget"]) == ("child", 10.5, 10.0)
    assert entries["test_says_nothing"]["reading"] is None
    assert entries["test_says_nothing"]["outcome"] == "passed"
    assert entries["test_skips_itself"]["outcome"] == "skipped"
    assert entries["test_skipped_at_setup"]["outcome"] == "skipped"
    assert entries["test_skipped_at_setup"]["other"] is None
    broke = entries["test_teardown_breaks"]
    assert broke["outcome"] == "failed" and "teardown broke" in broke["failure"]


MISUSED = """
import math
import pytest

def test_not_timed(timed_reading):
    pass

@pytest.mark.timed
def test_reports_twice(timed_reading):
    timed_reading(1, 2, "ms")
    with pytest.raises(ValueError, match="once"):
        timed_reading(1, 2, "ms")

@pytest.mark.timed
@pytest.mark.parametrize("value, budget, unit, clock", [
    (math.nan, 1, "ms", "wall"), (True, 1, "ms", "wall"), ("1", 1, "ms", "wall"),
    (1, math.inf, "ms", "wall"), (1, 1, "", "wall"), (1, 1, "ms", "cpu")])
def test_refuses(timed_reading, value, budget, unit, clock):
    with pytest.raises(ValueError, match="timed_reading's"):
        timed_reading(value, budget, unit, clock=clock)
"""


def test_timed_reading_is_for_a_timed_test_called_once_with_numbers(gated, machine):
    result, _ = gated(MISUSED)
    result.assert_outcomes(passed=7, errors=1)
    assert "timed_reading is for a test marked timed" in result.stdout.str()


def test_without_the_variable_nothing_is_gated(pytester, machine, monkeypatch):
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_plain=REPORTED)
    out = pytester.path / "durations.json"
    monkeypatch.setenv(testing.DURATIONS_ENV, str(out))
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=5, skipped=2, errors=1)
    assert machine.pauses == []
    data = json.loads(out.read_text())
    assert "gate" not in data and timed(data) == {}


@pytest.mark.parametrize("value", ["2", "x@5", "0@5", "-1@5", "2@nan", "2@", "inf@5"])
def test_a_gate_that_is_not_a_line_and_a_deadline_stops_the_run(pytester, monkeypatch,
                                                                 value):
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_one=ONE)
    monkeypatch.setenv(testing.IDLE_ENV, value)
    result = pytester.runpytest("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert testing.IDLE_ENV in result.stderr.str()


def test_the_gate_reads_a_line_and_a_deadline():
    assert testing.idle({}) is None
    assert testing.idle({testing.IDLE_ENV: " 2.5@1800000300 "}) == testing.Idle(
        line=2.5, deadline=1_800_000_300.0)


REAL = """
import pytest

@pytest.mark.timed
def test_one():
    pass

@pytest.mark.timed
def test_two():
    pass
"""


def test_under_workers_the_entries_reach_the_controller(pytester, monkeypatch):
    """On this machine's own gauge, at a line no machine reaches."""
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_real=REAL)
    out = pytester.path / "durations.json"
    monkeypatch.setenv(testing.DURATIONS_ENV, str(out))
    monkeypatch.setenv(testing.IDLE_ENV, f"100000@{testing._now() + 60}")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-n", "2")
    gauge = Gauge.find().kind
    data = json.loads(out.read_text())
    entries = timed(data)
    assert set(entries) == {"test_one", "test_two"}
    if gauge == "none":
        result.assert_outcomes(skipped=2)
        assert {entry["outcome"] for entry in entries.values()} == {"not measured"}
    else:
        result.assert_outcomes(passed=2)
        assert {entry["outcome"] for entry in entries.values()} == {"passed"}
        assert all(math.isfinite(entry["other"]) for entry in entries.values())
        assert data["gate"]["first_sample"] is not None
    assert {entry["gauge"] for entry in entries.values()} == {gauge}
    assert data["gate"]["gauge"] == gauge
