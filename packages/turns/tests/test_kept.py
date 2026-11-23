"""Kept ends: a delivery asked with `keep` has its end written to turns' state folder as it
settles, served to `wait` by its ref past the endpoint's settled list and past the
endpoint's own end, and removed only by an `ack` of its ref. Driven on the fake harness
through the client."""

from __future__ import annotations

import json
import sys
import time

import pytest

from fake_harness import FakeHarness
from moxi_turns import client as turns_client
from moxi_turns import doctor, endpoint, kept, results
from moxi_turns.client import Session, TurnsError

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="moxi.ipc's named pipe server comes later")

CAN = ("deliver", "only-if-idle", "steer", "fork", "keep")


def until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


@pytest.fixture
def keeping(make_endpoint):
    """An endpoint on the fake harness that offers `fork` and `keep`, and a client
    session on it."""
    sessions = []

    def make(can=CAN, *, session_id="s-1"):
        ep, h = make_endpoint(FakeHarness(can=can), session_id=session_id)
        s = Session(ep.address, ep.session(), endpoint_dir=ep.dir)
        sessions.append(s)
        return ep, h, s

    yield make
    for s in sessions:
        s.close()


def files():
    return sorted(kept.kept_dir().glob("*.json"))


def ran(s, h, text, ref, *, keep=False):
    """A delivery that went in as a turn, which then ended."""
    d = s.deliver(text, ref=ref, keep=keep)
    assert isinstance(d.wait_started(5), results.Started)
    h.end_turn()
    assert isinstance(d.wait_ended(5), results.Ended)
    return d


def answered_fork(ep, h, s, ref, answer="two points"):
    d = s.fork("recall", by="memory", ref=ref, keep=True)
    assert until(lambda: len(h.forked) >= 1)
    spec = h.forked[-1]
    ep.fork_started(spec.id, "agent-7")
    ep.fork_ended(spec.id, answer)
    return d, spec


def test_a_kept_forks_end_is_written_as_it_settles_and_removed_by_its_ack(keeping):
    ep, h, s = keeping()
    d, spec = answered_fork(ep, h, s, "r-1")
    ended = d.wait_ended(5)
    assert isinstance(ended, results.Ended) and ended.answer == "two points"
    [path] = files()
    record = json.loads(path.read_text())
    assert (record["ref"], record["delivery"], record["kind"], record["by"]) == (
        "r-1", spec.id, "fork", "memory")
    assert record["session_ids"] == ["s-1"]
    assert results.from_wire(record["ended"]) == ended
    assert results.from_wire(record["started"]).agent_id == "agent-7"
    assert s.ack("r-1") is True
    assert files() == []
    assert s.ack("r-1") is False                 # a second ack does nothing


def test_a_delivery_without_keep_writes_nothing(keeping):
    ep, h, s = keeping()
    d = s.fork("recall", by="memory", ref="r-1")
    assert until(lambda: len(h.forked) == 1)
    ep.fork_ended(h.forked[0].id, "x")
    assert isinstance(d.wait_ended(5), results.Ended)
    assert files() == []


def test_a_kept_end_outlives_the_settled_list(keeping, monkeypatch):
    monkeypatch.setattr(endpoint, "SETTLED_KEPT", 2)
    ep, h, s = keeping()
    first = ran(s, h, "one", "kept-1", keep=True)
    others = [ran(s, h, f"n{n}", f"plain-{n}") for n in range(4)]
    # The oldest plain one has left the record; the kept one has not.
    assert ep.wait(others[0].id, "ended", 0).reason.startswith(results.LOST_UNKNOWN)
    assert isinstance(ep.wait(first.id, "ended", 0), results.Ended)
    assert isinstance(turns_client.wait_ref("kept-1", session_id="s-1", timeout_s=5),
                      results.Ended)
    assert s.ack("kept-1") is True
    # Acked, it is an ordinary settled delivery again.
    for n in range(2):
        ran(s, h, f"m{n}", f"later-{n}")
    assert ep.wait(first.id, "ended", 0).reason.startswith(results.LOST_UNKNOWN)


def test_an_ack_before_the_end_does_nothing_and_the_end_is_kept_when_it_settles(keeping):
    ep, h, s = keeping()
    d = s.fork("recall", by="memory", ref="r-open", keep=True)
    assert until(lambda: len(h.forked) == 1)
    assert s.ack("r-open") is False
    ep.fork_ended(h.forked[0].id, "late answer")
    assert d.wait_ended(5).answer == "late answer"
    assert len(files()) == 1


def test_a_fork_open_at_the_endpoints_end_is_kept_lost_and_read_after_it(keeping):
    ep, h, s = keeping()
    d = s.fork("recall", by="memory", ref="r-gone", keep=True)
    assert until(lambda: len(h.forked) == 1)
    ep.fork_started(h.forked[0].id, "agent-9")
    ep.stop("the session ended")
    assert until(lambda: ep.wait_closed(0))
    # The endpoint no longer serves: the wait reads the kept end, the same at every read.
    for _ in range(2):
        ended = d.wait_ended(5)
        assert isinstance(ended, results.Lost)
        assert ended.reason == f"{results.LOST_GONE}: the session ended"
        assert (ended.delivery, ended.ref) == (h.forked[0].id, "r-gone")
    assert d.wait_started(5).agent_id == "agent-9"
    assert turns_client.wait_ref("r-gone", session_id="s-1") == ended
    # The client acks on the state folder itself.
    assert s.ack("r-gone") is True
    assert files() == []
    gone = turns_client.wait_ref("r-gone", session_id="s-1")
    assert isinstance(gone, results.Lost) and gone.reason.startswith(results.LOST_UNKNOWN)


def test_a_caller_with_no_delivery_reads_and_acks_by_session_id_after_the_end(keeping):
    ep, h, s = keeping()
    d, _ = answered_fork(ep, h, s, "r-2", answer="kept answer")
    assert d.wait_ended(5).answer == "kept answer"
    ep.stop()
    assert until(lambda: ep.wait_closed(0))
    assert turns_client.wait_ref("r-2", session_id="s-1").answer == "kept answer"
    assert turns_client.wait_ref("r-2", session_id="s-other").reason.startswith(
        results.LOST_UNKNOWN)
    assert turns_client.ack("r-2", session_id="s-other") is False
    assert turns_client.ack("r-2", session_id="s-1") is True
    assert files() == []


def test_a_later_endpoint_of_the_session_serves_and_acks_an_earlier_ones_end(keeping):
    ep, h, s = keeping()
    answered_fork(ep, h, s, "r-3", answer="from the first launch")[0].wait_ended(5)
    ep.stop()
    ep2, _, s2 = keeping()                          # the same session, launched again
    value = turns_client.wait_ref("r-3", session_id="s-1", timeout_s=5)
    assert value.answer == "from the first launch"
    assert ep2.wait_ref("r-3", "ended", 0)[1] == value
    assert s2.ack("r-3") is True
    assert files() == []


def test_another_sessions_end_is_not_served(keeping):
    ep, h, s = keeping()
    answered_fork(ep, h, s, "r-4")[0].wait_ended(5)
    _, _, other = keeping(session_id="s-2")
    assert turns_client.wait_ref("r-4", session_id="s-2", timeout_s=5).reason.startswith(
        results.LOST_UNKNOWN)
    assert other.ack("r-4") is False
    assert len(files()) == 1


def test_a_fork_continue_lost_at_once_is_kept_too(keeping):
    ep, h, s = keeping()
    d = s.fork_continue("no-such-delivery", "next", ref="r-5", keep=True)
    lost = d.wait_ended(5)
    assert isinstance(lost, results.Lost) and lost.reason.startswith(results.LOST_GONE)
    [path] = files()
    assert json.loads(path.read_text())["ref"] == "r-5"


def test_keep_needs_a_ref_and_a_session_that_offers_it(keeping):
    ep, h, s = keeping()
    with pytest.raises(ValueError, match="keep needs a ref"):
        s.fork("x", by="memory", keep=True)
    with pytest.raises(ValueError, match="keep needs a ref"):
        s.deliver("x", keep=True)
    _, _, old = keeping(can=("deliver", "fork"), session_id="s-old")
    with pytest.raises(TurnsError) as caught:
        old.fork("x", by="memory", ref="r", keep=True)
    assert caught.value.code == results.ERROR_CAPABILITY
    assert old.ack("r") is False                    # nothing kept: the state folder only


def test_the_doctor_lists_kept_ends_older_than_a_day(keeping):
    ep, h, s = keeping()
    answered_fork(ep, h, s, "fresh")[0].wait_ended(5)
    old = kept.entry(endpoint="claude-abc", session_ids=["s-9"], delivery="d-1",
                     ref="stale-ref", kind="fork", by="memory", started={}, ended={})
    old["settled_at"] = time.time() - 2 * kept.OLD_S
    kept.write(old)
    rows = {row.name: row for row in doctor._kept_rows()}
    assert rows["kept ends"].state == "ok"
    assert rows["kept ends"].detail.startswith("2 kept under")
    stale = rows["kept end stale-ref"]
    assert stale.state == "warn"
    assert "memory" in stale.detail and "s-9" in stale.detail and "48 h" in stale.detail
    assert "kept end fresh" not in rows


def refused_keep(call):
    """`call` is refused invalid params, naming the ref's unacked kept end."""
    with pytest.raises(TurnsError) as caught:
        call()
    assert caught.value.code == -32602
    assert "still holds a kept end" in str(caught.value)


def test_a_second_keep_under_an_open_kept_ref_is_refused_and_the_first_end_survives(keeping):
    ep, h, s = keeping()
    first = s.fork("recall", by="memory", ref="r-twice", keep=True)
    assert until(lambda: len(h.forked) == 1)
    refused_keep(lambda: s.fork("again", by="memory", ref="r-twice", keep=True))
    refused_keep(lambda: s.deliver("again", ref="r-twice", keep=True))
    assert len(h.forked) == 1 and h.submitted == []        # nothing was handed in
    ep.fork_started(h.forked[0].id, "agent-7")
    ep.fork_ended(h.forked[0].id, "first answer")
    assert first.wait_ended(5).answer == "first answer"
    ep.stop()
    assert until(lambda: ep.wait_closed(0))
    # Past the endpoint's end the first end is still on disk, under its own ref.
    assert first.wait_ended(5).answer == "first answer"
    assert len(files()) == 1


def test_a_settled_kept_ref_is_refused_until_its_ack_then_taken(keeping):
    ep, h, s = keeping()
    first = ran(s, h, "one", "r-again", keep=True)
    refused_keep(lambda: s.deliver("two", ref="r-again", keep=True))
    # Without keep the ref is free to use, as it always was.
    plain = s.deliver("plain", ref="r-again")
    assert isinstance(plain.wait_started(5), results.Started)
    h.end_turn()
    assert isinstance(plain.wait_ended(5), results.Ended)
    assert s.ack("r-again") is True
    second = ran(s, h, "two", "r-again", keep=True)
    [path] = files()
    assert json.loads(path.read_text())["delivery"] == second.id != first.id


def test_an_earlier_endpoints_kept_end_holds_its_ref_for_the_same_session_only(keeping):
    ep, h, s = keeping()
    answered_fork(ep, h, s, "r-held")[0].wait_ended(5)
    ep.stop()
    ep2, h2, s2 = keeping()                      # the same session, launched again
    refused_keep(lambda: s2.fork("recall", by="memory", ref="r-held", keep=True))
    assert h2.forked == []
    _, h3, other = keeping(session_id="s-2")     # another session's ref is its own
    other.fork("recall", by="memory", ref="r-held", keep=True)
    assert until(lambda: len(h3.forked) == 1)
    assert s2.ack("r-held") is True
    s2.fork("recall", by="memory", ref="r-held", keep=True)
    assert until(lambda: len(h2.forked) == 1)
