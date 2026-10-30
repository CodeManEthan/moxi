"""Delivery on the endpoint: the only-if-idle fold, answers that never wait on the harness,
the ordered worker and its skips, steers confirmed by the harness, and the endpoint's
behaviour the contract names. Driven on the fake harness, over `moxi.ipc` where the
answer's timing is the point."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from fake_harness import FakeHarness
from moxi import ipc
from moxi_turns import results as R
from moxi_turns.harness import Live

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="moxi.ipc's named pipe server comes later")


@pytest.fixture
def connect():
    clients = []

    def make(ep):
        client = ipc.connect(ep.address, timeout=5)
        clients.append(client)
        client.hello((2, 2))
        return client

    yield make
    for client in clients:
        client.close()


def until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def timed(fn):
    began = time.monotonic()
    out = fn()
    return out, time.monotonic() - began


class Gated(FakeHarness):
    """The fake, recording every hand-off in order; a hand-off whose kind is in `hold`
    blocks the worker until the test sets `release`."""

    def __init__(self, *a, hold=(), **kw):
        super().__init__(*a, **kw)
        self.order: list[tuple[str, str]] = []
        self.hold = set(hold)
        self.release = threading.Event()
        self.expired_ids: list[str] = []
        self.live_wait_s = 0.5

    def _gate(self, kind, ident):
        self.order.append((kind, ident))
        if kind in self.hold:
            self.release.wait(30)

    def submit(self, delivery):
        self._gate("submit", delivery.id)
        super().submit(delivery)

    def append(self, delivery):
        self._gate("append", delivery.id)
        super().append(delivery)

    def command(self, request):
        self._gate("command", request.id)
        super().command(request)

    def expired(self, delivery_id):
        self.expired_ids.append(delivery_id)


class SlowLive(FakeHarness):
    """A harness whose live read takes `delay` seconds; counts the reads."""

    def __init__(self, *a, delay=5.0, **kw):
        super().__init__(*a, **kw)
        self.delay = delay
        self.reads = 0
        self.live_wait_s = 0.5

    def live(self):
        self.reads += 1
        time.sleep(self.delay)
        return Live(self.agents, self.draft)


# -- only_if_idle: the ledger's checks in the answer, the harness's in the value ---------

def test_only_if_idle_refuses_from_the_ledger_in_the_answer(make_endpoint, connect):
    ep, fake = make_endpoint(FakeHarness(auto=False))
    client = connect(ep)
    first = client.call("deliver", {"text": "a", "only_if_idle": True}, timeout=5)
    assert "result" not in first                       # in flight now
    busy = client.call("deliver", {"text": "b", "only_if_idle": True, "ref": "r"},
                       timeout=5)
    assert R.from_wire(busy["result"]) == R.Refused("busy-turn", busy["delivery"], "r")
    fake.enter(fake.submitted[0])
    fake.end_turn()
    stale = client.call("deliver", {"text": "c", "only_if_idle": True,
                                    "if_turn_number": 0}, timeout=5)
    assert R.from_wire(stale["result"]).reason == "stale-turn"
    plain = client.call("deliver", {"text": "d", "if_turn_number": None}, timeout=5)
    assert "result" not in plain


def test_a_flagged_deliver_never_waits_on_a_slow_live_read(make_endpoint, connect):
    ep, fake = make_endpoint(SlowLive(delay=5.0))
    client = connect(ep)
    fake.draft = True
    answer, took = timed(lambda: client.call(
        "deliver", {"text": "x", "only_if_idle": True, "refuse_draft": True,
                    "refuse_agents": True}, timeout=5))
    assert took < 0.5 and "result" not in answer
    got = client.call("wait", {"delivery": answer["delivery"], "until": "started",
                               "timeout_ms": 5000}, timeout=10)
    assert R.from_wire(got["result"]).reason == "draft"
    fake.draft, fake.agents = False, 1
    answer = client.call("deliver", {"text": "x", "only_if_idle": True,
                                     "refuse_agents": True}, timeout=5)
    got = client.call("wait", {"delivery": answer["delivery"], "until": "started",
                               "timeout_ms": 5000}, timeout=10)
    assert R.from_wire(got["result"]).reason == "busy-agents"
    # facts answers within the live bound, its live part unreadable, and joins the read
    # still out rather than starting another.
    facts, took = timed(lambda: R.from_wire(client.call("facts", {}, timeout=5)))
    assert took < 2.0 and facts.draft is True and facts.agents is None
    reads = fake.reads
    timed(lambda: client.call("facts", {}, timeout=5))
    assert fake.reads == reads


def test_an_adapters_own_word_reads_unsupported(make_endpoint):
    class Raises(FakeHarness):
        def submit(self, delivery):
            raise OSError("the line is closed")

    ep, _ = make_endpoint(Raises())
    d = ep.deliver("x")
    assert ep.wait(d.id, "started", 5).reason == "unsupported"
    ep2, fake = make_endpoint(FakeHarness(auto=False), session_id="s-2")
    d = ep2.deliver("y")
    until(lambda: fake.submitted)
    ep2.refused(d.id, "gone")
    assert ep2.wait(d.id, "started", 5).reason == "unsupported"


# -- the worker: answers while it is held, its order, its skips -------------------------

def test_while_a_hand_off_holds_the_worker_every_short_call_answers(make_endpoint, connect):
    ep, fake = make_endpoint(Gated(hold={"submit"}))
    client = connect(ep)
    held = client.call("deliver", {"text": "a", "ref": "r1"}, timeout=5)
    assert until(lambda: fake.order)                  # the worker is in the submit now
    for method, params in (("session", {}), ("deliver", {"text": "b"}),
                           ("steer", {"text": "c"}),
                           ("wait", {"ref": "r1", "until": "started", "timeout_ms": 0})):
        _, took = timed(lambda: client.call(method, params, timeout=5))
        assert took < 0.5, method
    _, took = timed(lambda: client.call("facts", {}, timeout=5))
    assert took < 1.0
    fake.release.set()
    got = client.call("wait", {"delivery": held["delivery"], "until": "started",
                               "timeout_ms": 5000}, timeout=10)
    assert R.from_wire(got["result"]).turn_id == "t1"


@pytest.mark.parametrize("after", ["deliver", "command"])
def test_hand_offs_reach_the_harness_in_the_order_recorded(make_endpoint, after):
    env = dict(os.environ, MOXI_TURNS_COMMANDS="clear")
    ep, fake = make_endpoint(Gated(hold={"append"}, auto_steer=False), env=env)
    fake.start_turn()
    steer = ep.steer("into the turn")
    assert until(lambda: fake.order)                  # its append holds the worker
    fake.end_turn()
    result: list = []
    if after == "deliver":
        later = ep.deliver("a new turn")              # recorded after the steer
    else:
        later = threading.Thread(target=lambda: result.append(ep.command("clear")))
        later.start()
        time.sleep(0.3)
    fake.release.set()
    assert until(lambda: len(fake.order) == 2)
    assert fake.order[0] == ("append", steer.id)
    assert fake.order[1][0] == ("submit" if after == "deliver" else "command")
    if after == "command":
        later.join(10)
        assert isinstance(result[0], R.Ran)


def test_a_stalled_worker_settles_everything_queued_and_hands_none_of_it_on(
        make_endpoint):
    env = dict(os.environ, MOXI_TURNS_COMMANDS="clear")
    ep, fake = make_endpoint(Gated(hold={"submit"}, auto_steer=False), env=env)
    ep.pending_ttl_s = 0.4
    stuck = ep.deliver("stuck")
    queued = ep.deliver("behind it")
    assert until(lambda: fake.order)
    assert isinstance(ep.wait(stuck.id, "started", 5), R.Lost)
    assert ep.wait(queued.id, "started", 5).reason.startswith("expired")
    fake.start_turn()                                  # the person's turn: a steer goes in
    steer = ep.steer("late")
    assert ep.wait(steer.id, "started", 5).reason.startswith("expired")
    fake.end_turn()
    command = ep.command("clear", timeout=5)
    assert isinstance(command, R.Failed) and command.reason.startswith("expired")
    fake.release.set()
    assert until(lambda: steer.id in fake.expired_ids)  # the skipped steer is let go
    time.sleep(0.2)
    assert [kind for kind, _ in fake.order] == ["submit"]   # only the stuck one ran
    ep.steered(steer.id)                               # a late word changes nothing
    assert ep.wait(steer.id, "ended", 0).reason.startswith("expired")


# -- steers: started only at the harness's word ------------------------------------------

def test_a_steer_starts_at_the_harnesss_word_and_its_turn_names_it(make_endpoint, connect):
    ep, fake = make_endpoint(FakeHarness(auto_steer=False))
    watch = connect(ep)
    watch.call("watch", {}, timeout=5)
    fake.start_turn()
    client = connect(ep)
    answer = client.call("steer", {"text": "also", "ref": "s1"}, timeout=5)
    assert "result" not in answer
    assert until(lambda: fake.appended)
    pending = client.call("wait", {"delivery": answer["delivery"], "until": "started",
                                   "timeout_ms": 0}, timeout=5)
    assert pending == {"pending": True}
    fake.take(fake.appended[0])
    got = client.call("wait", {"ref": "s1", "until": "started", "timeout_ms": 0},
                      timeout=5)
    assert R.from_wire(got["result"]) == R.Started("t1", answer["delivery"], "s1")
    fake.end_turn()
    ref = R.DeliveryRef(answer["delivery"], "s1")
    notes = []
    for note in watch.notifications():
        notes.append(R.from_notification(*note))
        if len(notes) == 3:
            break
    assert notes == [R.TurnStarted("t1", "s-1", 1, ()), R.TurnSteered("t1", "s-1", ref),
                     R.TurnEnded("t1", "s-1", 1, "answer",
                                 (R.DeliveryRef(ref.id, "s1", "steered"),))]


def test_a_steer_the_harness_turns_down_reads_idle_through_wait(make_endpoint):
    ep, fake = make_endpoint(FakeHarness(auto_steer=False))
    fake.start_turn()
    d = ep.steer("too late")
    assert d.early is None
    until(lambda: fake.appended)
    ep.refused(d.id, "idle")
    assert ep.wait(d.id, "started", 1) == R.Refused("idle", d.id)


def test_a_steer_never_confirmed_settles_lost_expired(make_endpoint):
    ep, fake = make_endpoint(FakeHarness(auto_steer=False))
    ep.pending_ttl_s = 0.3
    fake.start_turn()
    d = ep.steer("unheard")
    lost = ep.wait(d.id, "ended", 5)
    assert isinstance(lost, R.Lost) and lost.reason.startswith("expired")


def test_a_steer_into_a_held_turn_is_named_after_its_turn_started(make_endpoint, connect):
    ep, fake = make_endpoint(FakeHarness(auto=False))
    watch = connect(ep)
    watch.call("watch", {}, timeout=5)
    d = ep.deliver("opening")
    ep.turn_started("t1", held=True)
    fake.open_turn = "t1"
    s = ep.steer("steered while held")
    assert until(lambda: ep.wait(s.id, "started", 0) is not None)
    ep.turn_tied("t1", d.id)
    ep.turn_ended("t1", "answer")
    notes = []
    for note in watch.notifications():
        notes.append(note[0])
        if len(notes) == 3:
            break
    assert notes == ["turn.started", "turn.steered", "turn.ended"]


# -- the endpoint's behaviour the contract names ---------------------------------------

def test_wait_by_ref_names_the_newest_delivery_carrying_it(make_endpoint, connect):
    ep, fake = make_endpoint(FakeHarness(auto=False))
    client = connect(ep)
    client.call("deliver", {"text": "a", "ref": "same"}, timeout=5)
    second = client.call("deliver", {"text": "b", "ref": "same"}, timeout=5)
    got = client.call("wait", {"ref": "same", "until": "started", "timeout_ms": 0},
                      timeout=5)
    assert got == {"delivery": second["delivery"], "pending": True}


def test_watch_has_no_gap_for_a_turn_raised_while_it_subscribes(make_endpoint, connect):
    for _ in range(20):
        ep, fake = make_endpoint(session_id=None)
        watch = connect(ep)
        raiser = threading.Thread(target=fake.start_turn)
        raiser.start()
        facts = R.from_wire(watch.call("watch", {}, timeout=5)["facts"])
        raiser.join()
        if not facts.turn_open:
            method, _ = next(iter(watch.notifications()))
            assert method == "turn.started"
        ep.stop("next round")


def test_facts_turn_follows_the_ledger_through_a_missed_end(make_endpoint, connect):
    ep, fake = make_endpoint()
    client = connect(ep)
    watch = connect(ep)
    watch.call("watch", {}, timeout=5)
    fake.start_turn()                                 # the person's: no opening delivery
    facts = R.from_wire(client.call("facts", {}, timeout=5))
    assert facts.turn.turn_id == "t1" and facts.turn.opening is None
    fake.start_turn()                                 # t1's end was missed
    facts = R.from_wire(client.call("facts", {}, timeout=5))
    assert facts.turn.turn_id == "t2"
    got = []
    for method, params in watch.notifications():
        got.append((method, params["turn_id"], params.get("outcome")))
        if len(got) == 3:
            break
    assert got == [("turn.started", "t1", None), ("turn.ended", "t1", "error"),
                   ("turn.started", "t2", None)]
    fake.end_turn()
    assert R.from_wire(client.call("facts", {}, timeout=5)).turn is None


def test_a_held_turn_keeps_its_start_time_across_the_tie(make_endpoint, connect):
    ep, fake = make_endpoint(FakeHarness(auto=False))
    client = connect(ep)
    d = client.call("deliver", {"text": "x"}, timeout=5)
    ep.turn_started("t1", held=True)
    opened_at = ep.ledger.open.started_at
    time.sleep(0.05)
    ep.turn_tied("t1", d["delivery"])
    facts = R.from_wire(client.call("facts", {}, timeout=5))
    assert facts.turn.started_at == opened_at


@pytest.mark.parametrize("reason", ["draft", "busy-agents", "dropped", "unsupported",
                                    "idle"])
def test_each_harness_refusal_is_a_value_through_wait(make_endpoint, connect, reason):
    ep, fake = make_endpoint(FakeHarness(auto=False))
    client = connect(ep)
    d = client.call("deliver", {"text": "x", "ref": "r"}, timeout=5)
    assert until(lambda: fake.submitted)
    ep.refused(d["delivery"], reason)
    got = client.call("wait", {"delivery": d["delivery"], "until": "ended",
                               "timeout_ms": 1000}, timeout=5)
    assert R.from_wire(got["result"]) == R.Refused(reason, d["delivery"], "r")


def test_loading_refuses_in_the_answer_and_a_plain_deliver_waits(make_endpoint, connect):
    class Loading(FakeHarness):
        loading_now = True

        def loading(self):
            return self.loading_now

    ep, fake = make_endpoint(Loading())
    client = connect(ep)
    for method, params in (("steer", {"text": "x"}),
                           ("deliver", {"text": "x", "only_if_idle": True})):
        answer = client.call(method, params, timeout=5)
        assert R.from_wire(answer["result"]).reason == "loading"
    held = client.call("deliver", {"text": "held"}, timeout=5)
    assert "result" not in held and fake.submitted == []
    fake.loading_now = False
    ep.load_changed()
    got = client.call("wait", {"delivery": held["delivery"], "until": "started",
                               "timeout_ms": 5000}, timeout=10)
    assert isinstance(R.from_wire(got["result"]), R.Started)
