"""Steers and stuck hand-offs on the real adapters: the Claude half against a stand-in
runner, the Codex host against a stand-in app-server. A steer's answer never waits on the
harness; it reads `Started` only at the harness's word; hand-offs reach the harness in the
order they were recorded; and a hand-off the harness never takes settles on the endpoint's
bound without holding up anything else."""

from __future__ import annotations

import itertools
import os
import sys
import threading
import time

import pytest

from claude_runner import FakeRunner, pipe_pair
from moxi import ipc
from moxi_turns import claude_leg as leg
from moxi_turns import codex as codex_module
from moxi_turns import codex_hooks
from moxi_turns import codex_protocol as proto
from moxi_turns import protocol
from moxi_turns import results as R
from moxi_turns.claude_endpoint import ClaudeHarness
from moxi_turns.codex import CodexHarness
from moxi_turns.endpoint import Endpoint
from moxi_turns.listeners import Registry

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="moxi.ipc's named pipe server comes later")


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


def notifications(watch, count):
    out = []
    for note in watch.notifications():
        out.append(R.from_notification(*note))
        if len(out) == count:
            return out
    return out


# -- Claude: the leg, a stand-in runner --------------------------------------------------


@pytest.fixture
def claude(turns_env):
    made = []

    def make():
        r, w = pipe_pair()
        harness = ClaudeHarness(w, pid=os.getpid())
        ep = Endpoint(harness, registry=Registry())
        ep.prepare()
        harness.serve(str(ep.dir / protocol.MOD_SOCK))
        runner = FakeRunner(r)
        harness.first_line()
        runner.wait_first()
        runner.session("s-1")
        ep.open()
        made.append((ep, runner, w))
        return ep, runner, harness

    yield make
    for ep, runner, w in made:
        runner.resume()
        ep.stop("the test ended")
        w.close()


def test_a_stuck_submit_line_never_wedges_the_expiry_sweep(claude):
    ep, runner, harness = claude()
    ep.pending_ttl_s = 0.4
    runner.pause(harness._lines)                     # the runner stops reading
    text = "x" * (200 * 1024)                        # past the pipe's buffer: the write blocks
    stuck = ep.deliver(text)
    behind = ep.deliver("behind it")
    assert until(lambda: harness._window is not None)
    lost = ep.wait(stuck.id, "started", 5)
    assert isinstance(lost, R.Lost) and lost.reason.startswith("expired")
    # The sweep goes on while the write is stuck: the delivery queued behind it is handed
    # to the stuck worker and settles at its own bound.
    lost = ep.wait(behind.id, "started", 5)
    assert isinstance(lost, R.Lost) and lost.reason.startswith("expired")
    assert until(lambda: harness._window is None)    # the adapter closed the window
    facts, took = timed(ep.facts)
    assert took < 1.5 and facts.draft is True and facts.agents is None
    runner.resume()
    line = runner.next_line()
    assert (line["op"], line["delivery"], len(line["text"])) == (
        "submit", stuck.id, len(text))
    assert runner.no_line(0.5)                       # nothing for the skipped one
    # Its prompt is a late entry now, and the gate drops it.
    entry = runner.enter(line["nonce"])
    assert (entry["verdict"], entry["reason"]) == ("drop", leg.LATE)


def test_append_done_starts_the_steer_and_its_turn_names_it(claude, connect):
    ep, runner, harness = claude()
    watch = connect(ep)
    watch.call("watch", {}, timeout=5)
    runner.turn_start("T1")                          # the person's turn
    client = connect(ep)
    answer, took = timed(lambda: client.call("steer", {"text": "also", "ref": "s1"},
                                             timeout=5))
    assert took < 0.5 and "result" not in answer
    steer = answer["delivery"]
    assert runner.next_line() == {"op": leg.LINE_APPEND, "delivery": steer,
                                  "text": "also"}
    assert ep.wait(steer, "started", 0.2) is None    # handed, not yet taken
    runner.report(leg.R_APPEND_DONE, delivery=steer)
    assert ep.wait(steer, "started", 2) == R.Started("T1", steer, "s1")
    runner.turn_complete("T1")
    assert ep.wait(steer, "ended", 2) == R.Ended("T1", "answer", steer, "s1")
    started, steered, ended = notifications(watch, 3)
    assert isinstance(started, R.TurnStarted) and started.turn_id == "T1"
    assert steered == R.TurnSteered("T1", "s-1", R.DeliveryRef(steer, "s1"))
    assert isinstance(ended, R.TurnEnded)
    assert ended.deliveries == (R.DeliveryRef(steer, "s1", "steered"),)


def test_a_steers_line_comes_before_a_later_submit_and_append_failed_reads_idle(claude):
    ep, runner, harness = claude()
    runner.turn_start("T1")
    steer = ep.steer("into T1")
    runner.turn_complete("T1")
    later = ep.deliver("a new turn")                 # recorded after the steer
    first, second = runner.next_line(), runner.next_line()
    assert (first["op"], first["delivery"]) == (leg.LINE_APPEND, steer.id)
    assert (second["op"], second["delivery"]) == (leg.LINE_SUBMIT, later.id)
    runner.report(leg.R_APPEND_FAILED, delivery=steer.id)
    assert ep.wait(steer.id, "started", 2) == R.Refused("idle", steer.id)
    assert ep.wait(steer.id, "ended", 0) == R.Refused("idle", steer.id)


# -- Codex: the host, a stand-in app-server ----------------------------------------------


class FakeAppServer:
    def __init__(self, socket_path, **kwargs):
        self.socket_path = socket_path
        self.alive = False
        self.returncode = None

    def start(self):
        self.alive = True
        return self

    def wait_for_socket(self):
        return True

    def stop(self):
        self.alive = False


class FakeClient:
    """The app-server's answers. `turn/steer` waits `steer_delay` seconds and for
    `steer_gate`, then takes the text into the live turn, or answers "no active turn"
    while `steer_no_turn` is set (ending the turn first, as Codex does)."""

    def __init__(self, harness, *, on_notification, on_close):
        self.harness = harness
        self.on_notification = on_notification
        self.on_close = on_close
        self.connected = False
        self.calls: list = []
        self.turns = itertools.count(1)
        self.live_turn: str | None = None
        self.steer_delay = 0.0
        self.steer_gate = threading.Event()
        self.steer_gate.set()
        self.steer_no_turn = False
        self.steer_log: list[tuple[str, str]] = []
        self._log_lock = threading.Lock()

    def connect(self):
        self.connected = True
        return self

    def initialize(self):
        return {}

    def close(self):
        self.connected = False

    def notify(self, method, params=None):
        pass

    def _log(self, what, text):
        with self._log_lock:
            self.steer_log.append((what, text))

    def call(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        if method == "hooks/list":
            return {"data": [{"hooks": [
                {"key": key, "enabled": True, "trustStatus": codex_hooks.TRUSTED}
                for key in self.harness.hook_keys]}]}
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/resume":
            return {"thread": {"id": "thread-1", "status": {"type": "idle"}}}
        if method == "turn/start":
            priming = params["input"][0]["text"] == codex_module.PRIMING_TEXT
            turn_id = "prime" if priming else f"turn-{next(self.turns)}"
            self.live_turn = turn_id
            self.on_notification("turn/started", {"turn": {"id": turn_id}}, None)
            if priming:
                self.complete()
            return {"turn": {"id": turn_id}}
        if method == "turn/steer":
            text = params["input"][0]["text"]
            self._log("in", text)
            try:
                time.sleep(self.steer_delay)
                self.steer_gate.wait(30)
                if self.steer_no_turn:
                    if self.live_turn is not None:
                        self.complete()
                    raise proto.NoActiveTurn(-32600, "no active turn to steer")
                return {"turnId": self.live_turn}
            finally:
                self._log("out", text)
        if method == "thread/backgroundTerminals/list":
            return {"data": []}
        if method == "thread/turns/list":
            data = [] if self.live_turn is None else [
                {"id": self.live_turn, "status": "inProgress"}]
            return {"data": data}
        raise AssertionError(f"unexpected app-server call {method}")

    def complete(self, status="completed"):
        turn_id, self.live_turn = self.live_turn, None
        self.on_notification("turn/completed",
                             {"turn": {"id": turn_id, "status": status}}, None)

    def texts(self, method):
        """The texts of the app-server's `method` calls, the priming turn left out."""
        return [params["input"][0]["text"] for m, params in self.calls if m == method
                and params["input"][0]["text"] != codex_module.PRIMING_TEXT]


@pytest.fixture
def codex(turns_env):
    made = []

    def make():
        clients: list[FakeClient] = []
        harness = CodexHarness(
            turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
            client_factory=lambda **kw: clients.append(FakeClient(harness, **kw))
            or clients[-1],
            app_server_factory=FakeAppServer)
        ep = Endpoint(harness, session_id=None).open()
        harness.start()
        app = clients[0]
        made.append((ep, app))
        first = ep.deliver("first")
        assert ep.wait(first.id, "started", 2).turn_id == "turn-1"
        return ep, harness, app

    yield make
    for ep, app in made:
        app.steer_gate.set()
        ep.stop("the test ended")


def test_a_late_turn_steer_answers_at_once_and_starts_at_codexs_word(codex, connect):
    ep, harness, app = codex()
    watch = connect(ep)
    watch.call("watch", {}, timeout=5)
    app.steer_delay = 1.0
    client = connect(ep)
    answer, took = timed(lambda: client.call("steer", {"text": "also", "ref": "s1"},
                                             timeout=5))
    assert took < 0.5 and "result" not in answer
    steer = answer["delivery"]
    assert until(lambda: app.steer_log)              # turn/steer is out
    facts, took = timed(lambda: R.from_wire(client.call("facts", {}, timeout=5)))
    assert took < 0.5 and facts.turn_open
    assert ep.wait(steer, "started", 0) is None
    assert ep.wait(steer, "started", 5) == R.Started("turn-1", steer, "s1")
    app.complete()
    assert ep.wait(steer, "ended", 2) == R.Ended("turn-1", "answer", steer, "s1")
    steered, ended = notifications(watch, 2)         # turn-1 started before the watch
    assert steered == R.TurnSteered("turn-1", "thread-1", R.DeliveryRef(steer, "s1"))
    assert R.DeliveryRef(steer, "s1", "steered") in ended.deliveries
    assert app.texts("turn/steer") == ["also"]
    assert app.texts("turn/start") == ["first"]


def test_no_active_turn_answered_late_reads_idle_through_wait(codex, connect):
    ep, harness, app = codex()
    app.steer_delay, app.steer_no_turn = 1.0, True
    client = connect(ep)
    answer, took = timed(lambda: client.call("steer", {"text": "too late"}, timeout=5))
    assert took < 0.5 and "result" not in answer
    got = client.call("wait", {"delivery": answer["delivery"], "until": "started",
                               "timeout_ms": 5000}, timeout=10)
    assert R.from_wire(got["result"]) == R.Refused("idle", answer["delivery"])
    assert until(lambda: not ep.reading().turn_open)  # the held end goes out after
    assert app.texts("turn/start") == ["first"]      # never a turn of its own


def test_two_steers_reach_turn_steer_in_order_one_at_a_time(codex):
    ep, harness, app = codex()
    app.steer_delay = 0.3
    one, two = ep.steer("one"), ep.steer("two")
    assert ep.wait(one.id, "started", 5).turn_id == "turn-1"
    assert ep.wait(two.id, "started", 5).turn_id == "turn-1"
    assert app.steer_log == [("in", "one"), ("out", "one"), ("in", "two"), ("out", "two")]


def test_a_turn_completed_after_the_record_is_held_for_the_steer(codex, connect):
    ep, harness, app = codex()
    watch = connect(ep)
    watch.call("watch", {}, timeout=5)
    reached, go = threading.Event(), threading.Event()
    append = harness.append

    def held_append(delivery):                       # the worker holds before the host
        reached.set()
        go.wait(10)
        append(delivery)

    harness.append = held_append
    steer = ep.steer("held at record")
    assert reached.wait(5)
    assert steer.id in harness._steering             # the hold began at the record
    # Codex ends the turn before the host has sent turn/steer; the turn stays live in
    # the stand-in, so the steer is still taken.
    app.on_notification("turn/completed",
                        {"turn": {"id": "turn-1", "status": "completed"}}, None)
    time.sleep(0.2)
    assert ep.reading().turn_open                    # held
    go.set()
    assert ep.wait(steer.id, "started", 5) == R.Started("turn-1", steer.id)
    assert ep.wait(steer.id, "ended", 5) == R.Ended("turn-1", "answer", steer.id)
    notes = notifications(watch, 2)
    assert [type(n).__name__ for n in notes] == ["TurnSteered", "TurnEnded"]
    assert R.DeliveryRef(steer.id, None, "steered") in notes[1].deliveries


def test_a_steer_expired_by_the_bound_releases_the_held_end(codex):
    ep, harness, app = codex()
    ep.pending_ttl_s = 0.4
    app.steer_gate.clear()                           # turn/steer never answers
    steer = ep.steer("unanswered")
    assert until(lambda: app.steer_log)
    app.complete()                                   # held while the steer is out
    time.sleep(0.1)
    assert ep.reading().turn_open
    lost = ep.wait(steer.id, "started", 5)
    assert isinstance(lost, R.Lost) and lost.reason.startswith("expired")
    assert until(lambda: not ep.reading().turn_open, 2)
    assert steer.id not in harness._steering
    app.steer_gate.set()                             # Codex's late word changes nothing
    assert until(lambda: app.steer_log[-1:] == [("out", "unanswered")])
    time.sleep(0.1)
    assert ep.wait(steer.id, "ended", 0) == lost
