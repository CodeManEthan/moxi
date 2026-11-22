"""The `fork` request on the endpoint: handed on at once beside the main loop, its start
naming the fork, its end carrying the fork's answer, and every way it can fail to.
Driven on the fake harness through the client."""

from __future__ import annotations

import sys
import time

import pytest

from fake_harness import FakeHarness
from moxi_turns import results
from moxi_turns.client import Session, TurnsError

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="moxi.ipc's named pipe server comes later")

CAN = ("deliver", "only-if-idle", "steer", "fork")


def until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


@pytest.fixture
def forking(make_endpoint):
    """An endpoint on the fake harness that offers `fork`, and a client session on it."""
    sessions = []

    def make(can=CAN):
        ep, h = make_endpoint(FakeHarness(can=can))
        s = Session(ep.address, ep.session(), endpoint_dir=ep.dir)
        sessions.append(s)
        return ep, h, s

    yield make
    for s in sessions:
        s.close()


def test_a_fork_starts_names_its_agent_and_ends_with_its_answer(forking):
    ep, h, s = forking()
    d = s.fork("recall what we said", by="memory", ref="r-1")
    assert until(lambda: len(h.forked) == 1)
    spec = h.forked[0]
    assert (spec.kind, spec.text, spec.by, spec.ref) == ("fork", "recall what we said",
                                                         "memory", "r-1")
    ep.fork_started(spec.id, "agent-7")
    started = d.wait_started(5)
    assert isinstance(started, results.Started)
    assert (started.agent_id, started.turn_id, started.delivery, started.ref) == (
        "agent-7", None, spec.id, "r-1")
    ep.fork_ended(spec.id, "two points")
    ended = d.wait_ended(5)
    assert isinstance(ended, results.Ended)
    assert (ended.answer, ended.outcome, ended.turn_id, ended.ref) == (
        "two points", "answer", None, "r-1")


def test_a_fork_goes_in_while_a_turn_runs_and_holds_no_slot(forking):
    ep, h, s = forking()
    h.start_turn()
    d = s.fork("look", by="memory")
    assert until(lambda: len(h.forked) == 1)
    later = s.deliver("next")
    assert until(lambda: ep.facts().turn_open)
    assert h.submitted == []          # held behind the running turn, not behind the fork
    h.end_turn()
    assert until(lambda: len(h.submitted) == 1) and h.submitted[0].id == later.id
    ep.fork_ended(h.forked[0].id, None)
    ended = d.wait_ended(5)
    assert isinstance(ended, results.Ended) and ended.answer is None


def test_a_session_that_cannot_fork_answers_the_capability_error(forking):
    _, _, s = forking(can=("deliver",))
    with pytest.raises(TurnsError) as caught:
        s.fork("look", by="memory")
    assert caught.value.code == results.ERROR_CAPABILITY


def test_a_fork_needs_its_asker(forking):
    _, _, s = forking()
    with pytest.raises(ValueError):
        s.fork("look", by="")


@pytest.mark.parametrize("reason", ["denied: the harness refused the spawn",
                                    "gone: the fork's warm-up never answered"])
def test_a_fork_the_harness_could_not_run_reads_lost_with_its_reason(forking, reason):
    ep, h, s = forking()
    d = s.fork("look", by="memory")
    assert until(lambda: len(h.forked) == 1)
    ep.lost(h.forked[0].id, reason)
    for value in (d.wait_started(5), d.wait_ended(5)):
        assert isinstance(value, results.Lost) and value.reason == reason


def test_a_fork_lost_after_it_started_keeps_its_start(forking):
    ep, h, s = forking()
    d = s.fork("look", by="memory")
    assert until(lambda: len(h.forked) == 1)
    ep.fork_started(h.forked[0].id, "agent-1")
    ep.lost(h.forked[0].id, "gone: the harness did not take the task")
    assert d.wait_started(5).agent_id == "agent-1"
    assert d.wait_ended(5).reason.startswith(results.LOST_GONE)


def test_the_endpoints_end_settles_an_open_fork_lost(forking):
    ep, h, s = forking()
    d = s.fork("look", by="memory", ref="r-9")
    assert until(lambda: len(h.forked) == 1)
    ep.fork_started(h.forked[0].id, "agent-1")
    ep.stop("the session ended")
    ended = d.wait_ended(5)
    assert isinstance(ended, results.Lost) and ended.ref == "r-9"


def test_a_late_word_on_a_settled_fork_changes_nothing(forking):
    ep, h, s = forking()
    d = s.fork("look", by="memory")
    assert until(lambda: len(h.forked) == 1)
    fork_id = h.forked[0].id
    ep.lost(fork_id, "denied: no")
    ep.fork_started(fork_id, "agent-late")
    ep.fork_ended(fork_id, "late answer")
    assert isinstance(d.wait_ended(5), results.Lost)


def test_a_fork_the_adapter_could_not_take_reads_unsupported(make_endpoint):
    class Broken(FakeHarness):
        def fork(self, delivery):
            raise OSError("the runner's line is closed")

    ep, _ = make_endpoint(Broken(can=CAN))
    d = ep.fork("look", by="memory")
    assert until(lambda: ep.wait(d.id, "ended", 0) is not None)
    value = ep.wait(d.id, "ended", 0)
    assert isinstance(value, results.Refused) and value.reason == "unsupported"
