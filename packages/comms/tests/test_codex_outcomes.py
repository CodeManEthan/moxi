"""The Codex adapter's hand-in outcomes and interrupt mail, over turns' real library.

The adapter's launch returns a `moxi_turns.Session` found at a stand-in endpoint that
speaks turns' socket protocol (`turns_endpoint.FakeEndpoint`, built as a Codex host), so
every hand-in, wait and look-up by ref goes through the library as it does in a launch.
The endpoint plays the thread: its turns, its steers taken late, its going away.

What is held here:
- interrupt mail goes into a running turn by `steer`, marked `[interrupt]`, while plain
  mail waits for the turn's end; to an idle thread it is delivered unmarked; a session
  without `steer` takes it at the boundary;
- a steer refused `idle` or `loading` falls back to one `deliver` with the same text and
  ref;
- each outcome of a hand-in, row by row: refused `gone`, never written, unknown
  (resolved on the same connection or a new one, or never), settled lost;
- the race: a steer the harness takes past the library's read bound is acked once and
  never requeued.
"""

from __future__ import annotations

import sys
import time

import pytest
from conftest import clock_reads_warm, frame_kinds, wait_until
from turns_endpoint import (CODEX, FakeEndpoint, drop, gone, hold, late, lose, refuse,
                            refuse_later)

import moxi_turns
from moxi_comms import codex_threads, paths
from moxi_comms.adapters.codex import CodexAdapter
from moxi_comms.adapters.codex_roster import CodexAgent

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="the test endpoint serves on moxi.ipc, whose named "
                                       "pipe server comes later")

IDENTITY = "planner@proj-a"
THREAD = "thread-1"
SENDER = dict(name="main", scope="proj-a")


@pytest.fixture(autouse=True)
def _warm(monkeypatch):
    clock_reads_warm(monkeypatch, "codex")


@pytest.fixture
def endpoints():
    made: list[FakeEndpoint] = []

    def make(**kw) -> FakeEndpoint:
        kw.setdefault("session_id", THREAD)
        kw.setdefault("harness", "codex")
        kw.setdefault("capabilities", CODEX)
        ep = FakeEndpoint(**kw).open()
        made.append(ep)
        return ep

    yield make
    for ep in made:
        ep.go_away("the test ended")


@pytest.fixture
def adapter(rig, tmp_path):
    """A started adapter that finds the endpoint the test made."""
    made: list[CodexAdapter] = []

    def make(ep: FakeEndpoint) -> CodexAdapter:
        def find(pid, timeout):
            session = moxi_turns.find(session_id=ep.session_id)
            assert session is not None
            return session

        row = CodexAgent(qualified=IDENTITY, name="planner", scope="proj-a",
                         cwd=str(tmp_path), thread_file=codex_threads.path(IDENTITY))
        exits: list = []
        a = CodexAdapter(row, find=find, autoreconnect=False, clock=rig.clock,
                         exit_hook=lambda: exits.append(True))
        a.exits = exits
        made.append(a)
        a.start()
        assert wait_until(lambda: ep.stop_handler is not None and bool(ep.watchers))
        return a

    yield make
    for a in made:
        a.stop()


def send(rig, text, *flags):
    code, out = rig.send(IDENTITY, text, *flags, sender=SENDER)
    assert code == 0, out


def interrupt(rig, text):
    send(rig, text, "--interrupt")


def turn_frames(frames, key):
    """The message ids every `turn_ended` so far named under `key` (`handled_ids` or
    `unhandled_ids`), in order."""
    return [m for f in frame_kinds(frames, "turn") for m in f.get(key, ())]


def started_at(digest):
    from moxi_comms.store import open_read
    conn = open_read()
    try:
        row = conn.execute("SELECT started_at FROM digests WHERE id = ?",
                           (int(digest),)).fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


def short_read_bound(monkeypatch) -> None:
    """The library's read bound at 0.3 s, so an answer 0.8 s late is an unknown
    outcome. It lives in the module that holds `Session`, loaded by reading that name."""
    monkeypatch.setattr(sys.modules[moxi_turns.Session.__module__], "READ_S", 0.3)


def running(a, ep) -> None:
    """The person's turn, running, as the adapter sees it."""
    ep.start_turn()
    assert wait_until(lambda: a.turn_open)


# --------------------------------------------------------------------------- interrupt

def test_interrupt_mail_is_steered_into_the_running_turn_and_acked_at_its_end(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter(ep)
    running(a, ep)
    send(rig, "plain, mid turn")
    interrupt(rig, "stop and look")
    assert wait_until(lambda: len(ep.calls) == 1)
    (method, did, ref, text) = ep.calls[0]
    assert method == "steer" and "stop and look" in text
    assert "plain, mid turn" not in text, "only the interrupt mail is steered"
    assert "[interrupt]" in text, "the frame line says why it came mid-turn"
    assert ep.turn["deliveries"] == [(did, "steered")], "the turn took it"
    assert wait_until(lambda: started_at(ref) is not None), "turn.steered marks it started"
    assert a.queue and not turn_frames(frames, "handled_ids"), "plain mail waits"
    ep.end_turn("answer")
    assert wait_until(lambda: turn_frames(frames, "handled_ids"))
    (steered,) = turn_frames(frames, "handled_ids")
    assert wait_until(lambda: len(ep.calls) == 2), "the turn's end hands the plain mail"
    assert ep.calls[1][0] == "deliver" and "plain, mid turn" in ep.calls[1][3]
    assert "[interrupt]" not in ep.calls[1][3]
    assert wait_until(lambda: started_at(ep.calls[1][2]) is not None), \
        "turn.started marks the opening digest started"
    assert steered not in a.held_ids
    assert not paths.cursors_dir().exists(), "the adapter writes no cursor file"


def test_interrupt_mail_to_an_idle_thread_is_delivered_unmarked(rig, adapter, endpoints):
    ep = endpoints()
    adapter(ep)
    interrupt(rig, "while idle")
    assert wait_until(lambda: len(ep.calls) == 1)
    assert ep.calls[0][0] == "deliver"
    assert "[interrupt]" not in ep.calls[0][3], "nothing came mid-turn"


def test_a_session_without_steer_takes_interrupt_mail_at_the_boundary(
        rig, adapter, endpoints):
    ep = endpoints(capabilities=CODEX - {"steer"})
    a = adapter(ep)
    running(a, ep)
    interrupt(rig, "no steer here")
    time.sleep(0.4)
    assert ep.calls == [], "it waits for the turn's end"
    ep.end_turn("answer")
    assert wait_until(lambda: len(ep.calls) == 1)
    assert ep.calls[0][0] == "deliver"
    assert not a.gone.is_set(), "no capability error reached the adapter"


@pytest.mark.parametrize("step", [refuse("idle"), refuse_later("idle"), refuse("loading")],
                         ids=["idle-from-the-answer", "idle-from-wait-started",
                              "loading"])
def test_a_steer_refused_idle_or_loading_falls_back_to_one_deliver_with_the_same_ref(
        rig, adapter, endpoints, frames, step):
    ep = endpoints()
    a = adapter(ep)
    running(a, ep)
    ep.script.append(step)
    interrupt(rig, "raced the turn's end")
    assert wait_until(lambda: len(ep.calls) == 2)
    (steer, deliver) = ep.calls
    assert (steer[0], deliver[0]) == ("steer", "deliver")
    assert steer[2] == deliver[2] and steer[3] == deliver[3], "same text, same ref"
    assert not turn_frames(frames, "unhandled_ids"), "the digest stays open"
    ep.end_turn("answer")                 # the delivery opens the next turn
    assert wait_until(lambda: ep.turn is not None and ep.turn["opening"] == deliver[1])
    ep.end_turn("answer")
    assert wait_until(lambda: turn_frames(frames, "handled_ids"))
    time.sleep(0.3)
    assert len(ep.calls) == 2, "the text ran once"
    assert len(turn_frames(frames, "handled_ids")) == 1


# --------------------------------------------------------------------------- outcomes

def test_a_refusal_gone_is_unhandled_at_once_and_replays_at_the_next_release(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter(ep)
    ep.script.append(gone())
    send(rig, "to an ending endpoint")
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert not a.hands and not a.queue, "not put back by the adapter"
    running(a, ep)
    ep.end_turn("answer")                 # the next release point
    assert wait_until(lambda: len(ep.texts) == 1), "the digest replays, there"
    assert not a.gone.is_set()


def test_a_hand_in_never_written_is_unhandled_at_once(rig, adapter, endpoints, frames):
    """The library reads a call it could not write as `Refused("gone")`: here the
    hand-in goes through a closed `Session`, while the endpoint serves."""
    ep = endpoints()
    a = adapter(ep)
    live = a.session
    dead = moxi_turns.find(session_id=THREAD)
    dead.close()
    a.session = dead
    send(rig, "never written")
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert ep.calls == [] and not a.hands
    a.session = live
    running(a, ep)
    ep.end_turn("answer")                 # the next release point
    assert wait_until(lambda: len(ep.texts) == 1), "the digest replays, there"


def test_a_settled_lost_delivery_is_unhandled(rig, adapter, endpoints, frames):
    ep = endpoints(auto=False)
    a = adapter(ep)
    ep.script.append(lose("expired: the harness neither started nor refused it"))
    send(rig, "settled lost")
    assert wait_until(lambda: len(ep.texts) == 1)
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert not turn_frames(frames, "handled_ids") and not a.hands
    assert not a.gone.is_set(), "an expired delivery keeps the session"


def test_an_unknown_steer_that_resolves_to_started_gets_no_fallback(
        rig, adapter, endpoints, frames, monkeypatch):
    short_read_bound(monkeypatch)
    ep = endpoints()
    a = adapter(ep)
    running(a, ep)
    ep.script.append(late(0.8))
    interrupt(rig, "answered late")
    assert wait_until(lambda: len(ep.calls) == 1)
    hand = wait_until(lambda: next(iter(a.hands.values()), None))
    assert hand.steer and not hand.fell_back
    assert wait_until(lambda: hand.delivery is not None
                      and hand.delivery.id == ep.calls[0][1]), \
        "the library resolved it by ref on the same connection"
    ep.end_turn("answer")
    assert wait_until(lambda: turn_frames(frames, "handled_ids"))
    assert len(ep.calls) == 1 and not turn_frames(frames, "unhandled_ids")


def test_an_unknown_steer_that_resolves_to_refused_idle_falls_back_once(
        rig, adapter, endpoints, frames, monkeypatch):
    short_read_bound(monkeypatch)
    ep = endpoints()
    a = adapter(ep)
    running(a, ep)
    ep.script.append(late(0.8, refuse_later("idle")))
    interrupt(rig, "late and refused")
    assert wait_until(lambda: len(ep.calls) == 2)
    assert [c[0] for c in ep.calls] == ["steer", "deliver"]
    ep.end_turn("answer")
    assert wait_until(lambda: ep.turn is not None)
    ep.end_turn("answer")
    assert wait_until(lambda: turn_frames(frames, "handled_ids"))
    time.sleep(0.3)
    assert len(ep.calls) == 2, "the text ran once"


def test_an_unknown_whose_connection_closed_resolves_on_a_new_one(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter(ep)
    running(a, ep)
    ep.script.append(drop())
    interrupt(rig, "the answer was dropped")
    assert wait_until(lambda: len(ep.calls) == 1)
    hand = wait_until(lambda: next(iter(a.hands.values()), None))
    assert wait_until(lambda: hand.delivery is not None
                      and hand.delivery.id == ep.calls[0][1]), "resolved by ref"
    assert not a.gone.is_set(), "the session was kept"
    ep.end_turn("answer")
    assert wait_until(lambda: turn_frames(frames, "handled_ids"))
    assert len(ep.calls) == 1, "nothing was requeued"


def test_an_unknown_whose_endpoint_no_longer_serves_is_unhandled_only_then(
        rig, adapter, endpoints, frames, monkeypatch):
    short_read_bound(monkeypatch)
    ep = endpoints()
    a = adapter(ep)
    running(a, ep)
    ep.script.append(late(30))
    interrupt(rig, "never answered")
    assert wait_until(lambda: len(ep.calls) == 1)
    time.sleep(1.0)
    assert not turn_frames(frames, "unhandled_ids"), "an unknown outcome is waited on"
    assert a.hands, "the digest stays open"
    ep.go_away("the endpoint ended")
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert len(ep.calls) == 1
    assert wait_until(lambda: a.exits), "the session's end ends the launch"


def test_a_steer_the_harness_takes_past_the_read_bound_is_acked_once(
        rig, adapter, endpoints, frames, monkeypatch):
    """The harness confirms the steer a second past the library's read bound. Nothing
    is requeued, the digest is acked once at that turn's end, and no later turn carries
    it."""
    short_read_bound(monkeypatch)
    ep = endpoints()
    a = adapter(ep)
    running(a, ep)
    ep.script.append(hold())
    interrupt(rig, "taken late")
    assert wait_until(lambda: len(ep.calls) == 1)
    time.sleep(1.3)
    assert not turn_frames(frames, "unhandled_ids") and len(ep.calls) == 1
    ep.take(ep.calls[0][1])
    ep.end_turn("answer")
    assert wait_until(lambda: turn_frames(frames, "handled_ids"))
    time.sleep(0.5)
    assert [c[0] for c in ep.calls] == ["steer"], "one steer, no turn of its own"
    assert len(turn_frames(frames, "handled_ids")) == 1
    assert not turn_frames(frames, "unhandled_ids")
    assert not paths.cursors_dir().exists(), "the adapter writes no cursor file"
