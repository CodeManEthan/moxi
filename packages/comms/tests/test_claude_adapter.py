"""comms' Claude adapter process as a turns caller.

The adapter runs a `ClaudeShim` on the rig's daemon and reaches a stand-in endpoint that
speaks turns' socket protocol (`turns_endpoint.FakeEndpoint`) through the real
`moxi_turns` library. The endpoint plays the session: its turns, its SessionStarts, its
Stops, and its going away.

What is held here:
- before the endpoint is found the adapter holds mail and the daemon reports the bound
  name as having no endpoint, which a send reports as `waiting`;
- the first look takes the stop handler and the watch, learns the session's id and
  `turned` from the watch's facts, and is a release point;
- plain mail goes one digest at a time, at turn boundaries only, named by its digest id
  as its `ref`, and settles from the turn that names that ref: `answer` handled;
  `aborted`, `error`, `refusal` unhandled;
- interrupt mail goes into a running turn by `steer`, marked `[interrupt]`; a steer
  refused `idle` falls back to one `deliver` with the same ref; a session without
  `steer` takes it at the boundary;
- each outcome of a hand-in, row by row: refused, gone, never written, unknown (resolved
  on the same connection or a new one, or never), lost;
- `Lost` that is untied keeps the endpoint; any other `Lost`, or the endpoint's end,
  drops it, tells the daemon, and the adapter looks again;
- the stop handler's answer at a Stop is the shim's reply check.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest
from conftest import clock_reads_warm, cursor_of, frame_kinds, wait_until
from turns_endpoint import (CLAUDE, FakeEndpoint, drop, gone, hold, late, lose, refuse,
                            refuse_later)

import moxi_turns
from moxi_comms import api, owed, paths
from moxi_comms.adapters.claude_adapter import ClaudeAdapter

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="the test endpoint serves on moxi.ipc, whose named "
                                       "pipe server comes later")

PLANNER = dict(name="planner", scope="proj-a")
REVIEWER = "reviewer@proj-a"
SESSION = "5e55a1d0-0000-4000-8000-000000000001"


@pytest.fixture
def backend():
    """The local record: the library opens the box's record by the config, as a session's
    send does."""
    return "local"


@pytest.fixture
def warm(monkeypatch):
    clock_reads_warm(monkeypatch, "claude-shim")


@pytest.fixture
def endpoints():
    made: list[FakeEndpoint] = []

    def make(**kw) -> FakeEndpoint:
        kw.setdefault("session_id", SESSION)
        ep = FakeEndpoint(**kw).open()
        made.append(ep)
        return ep

    yield make
    for ep in made:
        ep.go_away("the test ended")


@pytest.fixture
def bare_adapter(rig):
    """An adapter whose shim states `stated` from its bind, as `moxi comms claude` hands
    it the id the launch named (None for an unnamed resume). The cache read is
    the real one: no transcript is written for it."""
    made: list[ClaudeAdapter] = []

    def make(*, stated=SESSION, **kw) -> ClaudeAdapter:
        shim = rig.shim(name="reviewer", scope="proj-a", session_id=None, start=False)
        shim.session_id = stated
        a = ClaudeAdapter(shim, os.getpid(), look_interval=0.05, idle_poll=0.02,
                          idle_settle=0.05, **kw)
        a.start(tolerate_offline=False)
        made.append(a)
        return a

    yield make
    for a in made:
        a.stop()


@pytest.fixture
def adapter(bare_adapter, warm):
    """An adapter whose cache reads warm, for the tests of its delivery mechanics."""
    return bare_adapter


def send(rig, text, *flags):
    return rig.send(REVIEWER, text, *flags, sender=PLANNER)


def found(a, ep) -> bool:
    """The adapter has found `ep`, handles its stops and watches it."""
    return wait_until(lambda: a.session is not None and ep.stop_handler is not None
                      and bool(ep.watchers))


def awaiting(a) -> tuple:
    """The ids of the digest the adapter handed over, once its hand-over has returned."""
    assert wait_until(lambda: bool(a.shim.awaiting_ids))
    return a.shim.awaiting_ids


def binding(rig, qualified=REVIEWER):
    with rig.daemon._lock:
        return rig.daemon.bindings.get(qualified)


# --------------------------------------------------------------------------- waiting

def test_before_its_endpoint_the_adapter_holds_mail_and_a_send_reports_waiting(
        rig, bare_adapter, endpoints, frames):
    """On the real cache read: a fresh launch states its named id from its bind,
    with no transcript and no turn, so cache reads `fresh` and the send reads `waiting`."""
    a = bare_adapter()
    assert wait_until(lambda: binding(rig) is not None)
    assert binding(rig).endpoint is False, "registered as having no endpoint"
    assert binding(rig).session_id == SESSION and binding(rig).turned is False, \
        "the named id, stated from the bind"
    assert rig.daemon._read(binding(rig)).kind == "fresh"
    code, out = send(rig, "before the endpoint")
    assert code == 0, out
    assert "is bound, but its session has had no turns endpoint" in out, out
    assert wait_until(lambda: len(a.shim.queue) == 1), "the adapter holds it"
    reply = rig.daemon._session_query({"identity": REVIEWER})
    assert reply["endpoint"] is False and reply["endpoint_for"] is not None

    ep = endpoints()
    assert found(a, ep), "found within its look interval"
    assert wait_until(lambda: len(ep.texts) == 1), "the held mail goes in at the look"
    assert "before the endpoint" in ep.texts[0]
    assert binding(rig).endpoint is True
    assert binding(rig).session_id == SESSION, "the look learned the session id"
    assert ep.stop_handler is not None, "the adapter handles the session's stops"


def test_waiting_is_data_on_the_library_result(rig, bare_adapter):
    from conftest import _stamp_of
    rig.shim(name="planner", scope="proj-a")
    bare_adapter()
    assert wait_until(lambda: binding(rig) is not None)
    planner = _stamp_of({"MOXI_COMMS_PROJECT": "proj-a", "MOXI_COMMS_NAME": "planner",
                         "MOXI_COMMS_SCOPE": "proj-a"})
    with rig.session_holding(planner) as sock:
        result = api.send(api.Sender.socket(sock), [REVIEWER], "data", posture="none")
    assert result.outcome == "sent", result.lines()
    (r,) = result.recipients
    assert r.delivery == "waiting", r
    assert "no turns endpoint" in r.reason


def test_a_launch_resuming_an_expired_session_reads_its_clock_before_the_endpoint(
        rig, bare_adapter):
    """A resume names its id, so cache reads that transcript's clock at the
    bind, and a send to the expired session is held cold, not refused `cache-unread`."""
    from conftest import write_transcript
    write_transcript(SESSION, age=3 * 3600)
    bare_adapter()
    assert wait_until(lambda: binding(rig) is not None)
    assert rig.daemon._read(binding(rig)).kind == "cold"
    code, out = send(rig, "into a cold cache")
    assert code == 0 and "cache went cold" in out, out
    assert "no turns endpoint" not in out, "a cold hold reads before `waiting`"


def test_an_unnamed_resume_states_no_id_and_its_send_fails_closed(rig, bare_adapter):
    """An unnamed resume is the exception: until the look the binding has no id, so the
    hold refuses `cache-unread` (exit 6)."""
    bare_adapter(stated=None)
    assert wait_until(lambda: binding(rig) is not None)
    assert binding(rig).session_id is None
    assert rig.daemon._read(binding(rig)).kind == "unknown"
    code, out = send(rig, "to an unnamed resume")
    assert code == 75 and "no session id reported yet" in out, out


def test_a_bound_name_with_its_endpoint_reads_live(rig, adapter, endpoints):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    assert wait_until(lambda: binding(rig).endpoint is True)
    code, out = send(rig, "live")
    assert code == 0 and "no turns endpoint" not in out, out


# --------------------------------------------------------------------------- delivery

def test_an_answered_turn_settles_the_digest_handled_and_moves_the_cursor(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    code, out = send(rig, "one")
    assert code == 0, out
    assert wait_until(lambda: len(ep.texts) == 1)
    mid = awaiting(a)
    assert mid and ep.turn is not None and ep.turn["opening"] == "d1"
    (method, _, ref, _) = ep.calls[0]
    assert method == "deliver" and ref == str(a.shim.awaiting_digest), \
        "the digest's id is its ref"
    ep.end_turn("answer")
    assert wait_until(lambda: a.boundary is None and not a.shim.awaiting_ids)
    # The frame is recorded as it goes out; the daemon has acked once it answers.
    assert wait_until(lambda: any(mid[0] in f.get("handled_ids", ()) and "answer" in f
                                  for f in frame_kinds(frames, "turn")))
    assert cursor_of(REVIEWER) == mid[0]


@pytest.mark.parametrize("outcome", ["aborted", "error", "refusal"])
def test_a_turn_that_did_not_answer_settles_the_digest_unhandled(
        rig, adapter, endpoints, frames, outcome):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    send(rig, "interrupted")
    assert wait_until(lambda: len(ep.texts) == 1)
    (mid,) = awaiting(a)
    ep.end_turn(outcome)
    assert wait_until(lambda: any(mid in f.get("unhandled_ids", ()) and "answer" in f
                                  for f in frame_kinds(frames, "turn")))
    assert cursor_of(REVIEWER) is None, "the cursor does not pass an unhandled digest"
    assert a.session is not None, "the endpoint is kept"


def test_mail_that_arrives_mid_turn_waits_for_the_turn_end(rig, adapter, endpoints):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.start_turn()                       # the person's turn
    assert wait_until(lambda: a.turn_open)
    send(rig, "mid turn")
    assert wait_until(lambda: len(a.shim.queue) == 1)
    time.sleep(0.3)
    assert ep.texts == [], "nothing is handed while a turn runs"
    ep.end_turn("answer")
    assert wait_until(lambda: len(ep.texts) == 1), "the turn end is the release point"


def test_one_digest_at_a_time(rig, adapter, endpoints):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    send(rig, "first")
    assert wait_until(lambda: len(ep.texts) == 1)
    send(rig, "second")
    send(rig, "third")
    assert wait_until(lambda: len(a.shim.queue) == 2)
    time.sleep(0.3)
    assert len(ep.texts) == 1, "the next digest waits for the first one's turn"
    ep.end_turn("answer")
    assert wait_until(lambda: len(ep.texts) == 2)
    assert "second" in ep.texts[1] and "third" in ep.texts[1], "one digest, coalesced"


@pytest.mark.parametrize("step", [refuse("unsupported"), refuse_later("dropped"),
                                  refuse_later("unsupported")],
                         ids=["in-the-answer", "dropped-later", "unsupported-later"])
def test_a_refused_delivery_is_unhandled_at_once_and_the_endpoint_stays(
        rig, adapter, endpoints, frames, step):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.script.append(step)
    send(rig, "refused")
    assert wait_until(lambda: len(ep.texts) == 1)
    assert wait_until(lambda: any(f.get("unhandled") for f in frame_kinds(frames, "turn")))
    assert a.session is not None, "a refusal the endpoint made keeps the endpoint"
    assert a.looks == 1 and len(ep.texts) == 1, "nothing replays it at once"


def test_an_untied_loss_settles_one_digest_and_keeps_the_endpoint(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.script.append(lose("untied: the submit resolved with no turn started in its window"))
    send(rig, "untied")
    assert wait_until(lambda: len(ep.texts) == 1)
    assert wait_until(lambda: any(f.get("unhandled") for f in frame_kinds(frames, "turn")))
    assert a.session is not None and binding(rig).endpoint is True
    assert a.looks == 1


def test_a_loss_that_is_not_untied_means_the_endpoint_is_gone(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.script.append(lose("the endpoint ended"))
    send(rig, "gone")
    assert wait_until(lambda: a.looks >= 2 or a.session is None)
    assert wait_until(lambda: any(f.get("unhandled") for f in frame_kinds(frames, "turn")))
    assert wait_until(lambda: any(f.get("endpoint") is False for f in frames)), \
        "the daemon was told"


def test_the_endpoint_going_away_settles_the_open_digest_and_the_adapter_looks_again(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    send(rig, "in flight")
    assert wait_until(lambda: len(ep.texts) == 1)
    (mid,) = awaiting(a)
    ep.go_away("a plugin reload")
    assert wait_until(lambda: a.session is None)
    assert wait_until(lambda: binding(rig).endpoint is False)
    assert wait_until(lambda: any(mid in f.get("unhandled_ids", ())
                                  for f in frame_kinds(frames, "turn")))
    again = endpoints()
    assert wait_until(lambda: a.session is not None and a.looks == 2)
    assert wait_until(lambda: again.stop_handler is not None), \
        "the adapter handles the new endpoint's stops"
    assert wait_until(lambda: binding(rig).endpoint is True)


def test_a_look_after_a_reload_releases_no_held_mail(rig, adapter, endpoints, frames):
    """Only the first look stands in for a `ready`. A plugin reload raises no
    SessionStart, so the re-look after it is no release point: `--held` mail waits for
    the session's own next turn, a clear, a compaction or a restart."""
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    assert wait_until(lambda: len(frame_kinds(frames, "look")) == 1)
    time.sleep(0.3)
    code, out = send(rig, "held one", "--held")
    assert code == 0, out
    time.sleep(0.5)
    assert ep.texts == [], "held while idle"
    ep.go_away("a plugin reload")
    assert wait_until(lambda: a.session is None)
    again = endpoints()
    assert wait_until(lambda: a.session is not None and a.looks == 2)
    time.sleep(1.0)
    assert again.texts == [], "the re-look released the held message"
    assert len(frame_kinds(frames, "look")) == 1, "one look frame, at the first look"
    code, out = send(rig, "not held")
    assert code == 0, out
    assert wait_until(lambda: len(again.texts) == 1), "unheld mail still goes in"
    assert "not held" in again.texts[0] and "held one" not in again.texts[0]


def test_a_stop_while_the_endpoint_is_gone_is_not_held(rig, adapter, endpoints):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.go_away("crash")
    assert wait_until(lambda: a.session is None)
    assert a._stops is None


# --------------------------------------------------------------------------- stops

def test_the_stop_handler_answers_each_stop_with_the_shims_reply_check(
        rig, adapter, endpoints, monkeypatch):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    assert ep.stop() is None, "no debt: the turn ends"
    seen = []

    def on_stop(fields):
        seen.append(fields)
        return owed.Decision(block="reply to planner first", names=("planner@proj-a",))

    monkeypatch.setattr(a.shim, "on_stop", on_stop)
    ep.start_turn()
    assert ep.stop({"stop_hook_active": False, "task_types": ["shell"], "crons": 1}) \
        == "reply to planner first"
    assert seen[-1] == {"stop_hook_active": False, "task_types": ["shell"], "crons": 1}
    assert a.shim.blocked, "a held turn flushes nothing until it ends"
    ep.end_turn("answer")
    assert wait_until(lambda: not a.shim.blocked), "the turn end clears the block"


def test_a_missing_stop_fact_lets_the_turn_end(rig, adapter, endpoints):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    assert ep.stop({"task_types": None, "crons": None, "stop_hook_active": None}) is None


# --------------------------------------------------------------------------- evidence

def test_turned_follows_the_turn_events(rig, adapter, endpoints):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    assert a.shim.session_id == SESSION
    assert a.shim.turned is False, "a fresh session with no turn"
    ep.start_turn()
    assert wait_until(lambda: a.shim.turned)
    ep.end_turn()
    ep.ready("clear", "5e55a1d0-0000-4000-8000-000000000002")
    assert wait_until(lambda: a.shim.session_id.endswith("02"))
    assert a.shim.turned is False, "a clear lowers turned for the new id"
    ep.ready("resume", "5e55a1d0-0000-4000-8000-000000000003")
    assert wait_until(lambda: a.shim.session_id.endswith("03"))
    assert a.shim.turned is True, "another new id may hold a context"
    assert wait_until(lambda: binding(rig).session_id.endswith("03"))


def test_the_stated_id_at_the_look_keeps_turned_and_a_different_one_sets_it(
        rig, bare_adapter, endpoints):
    """At the look `facts()`'s id replaces the stated one. The same id is not a new
    session; a different one (a `/clear` or `/resume` no endpoint saw) has no source, so
    it sets `turned` and an empty transcript reads `unknown` until a turn."""
    ep = endpoints()
    same = bare_adapter()
    assert found(same, ep)
    assert same.shim.session_id == SESSION and same.shim.turned is False
    assert wait_until(lambda: binding(rig).endpoint is True)
    assert binding(rig).turned is False
    same.stop()
    assert wait_until(lambda: binding(rig) is None)

    other = bare_adapter(stated="5e55a1d0-0000-4000-8000-0000000000aa")
    assert wait_until(lambda: binding(rig) is not None)
    assert binding(rig).session_id.endswith("aa") and binding(rig).turned is False
    assert found(other, ep)
    assert other.shim.session_id == SESSION and other.shim.turned is True
    assert wait_until(lambda: binding(rig).session_id == SESSION
                      and binding(rig).turned is True)
    assert rig.daemon._read(binding(rig)).kind == "unknown"


def test_a_turn_number_above_zero_at_the_look_sets_turned(rig, adapter, endpoints):
    ep = endpoints(turn_number=3)
    a = adapter()
    assert found(a, ep)
    assert a.shim.turned is True
    assert wait_until(lambda: binding(rig).turned is True)


def test_the_first_look_is_a_release_point_when_idle_and_no_evidence_of_a_turn(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    assert wait_until(lambda: frame_kinds(frames, "look")), "a look frame at the look"
    assert not frame_kinds(frames, "turn"), "a look is not a turn"
    assert binding(rig).turned is False, "a fresh session stays fresh"


def test_no_release_at_the_look_while_a_turn_runs(rig, adapter, endpoints, frames):
    ep = endpoints()
    ep.start_turn()
    a = adapter()
    assert found(a, ep)
    time.sleep(0.2)
    assert not frame_kinds(frames, "look")
    assert a.turn_open


def test_a_session_whose_stops_another_caller_handles_is_not_taken(
        rig, adapter, endpoints):
    ep = endpoints()
    s = moxi_turns.find(pid=os.getpid())
    other = s.handle_stops(lambda facts: None)
    try:
        a = adapter()
        time.sleep(0.3)
        assert a.session is None, "a refused stop handler is no endpoint; it looks again"
    finally:
        other.close()
        s.close()
    assert found(a, ep)
    assert ep.handlers >= 2


# --------------------------------------------------------------------------- stopping

def test_stop_deregisters_and_removes_the_claim_file(rig, adapter, endpoints):
    from moxi_comms import claimfile
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    key = a.shim.claims.key
    a.stop()
    assert wait_until(lambda: binding(rig) is None), "the name deregisters"
    assert claimfile.read(key) is None


# --------------------------------------------------------------------------- hand-in outcomes

def turn_frames(frames, key):
    """The message ids every `turn_ended` so far named under `key` (`handled_ids` or
    `unhandled_ids`), in order."""
    return [m for f in frame_kinds(frames, "turn") for m in f.get(key, ())]


def test_a_refusal_gone_is_unhandled_at_once_and_the_endpoint_is_looked_for_again(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.script.append(gone())
    send(rig, "to an ending endpoint")
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert wait_until(lambda: a.looks >= 2 and a.session is not None), \
        "the same endpoint, found again"
    ep.start_turn()
    ep.end_turn("answer")                 # the next release point
    assert wait_until(lambda: len(ep.texts) == 1), "the digest replays, there"


def test_a_hand_in_never_written_is_unhandled_at_once(rig, adapter, endpoints, frames):
    """The library reads a call it could not write as `Refused("gone")`: here the
    adapter's own connections are closed under it, while the endpoint serves."""
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    a.session.close()
    send(rig, "never written")
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert wait_until(lambda: a.looks >= 2 and a.session is not None)
    assert found(a, ep)
    ep.start_turn()
    ep.end_turn("answer")                 # the next release point
    assert wait_until(lambda: len(ep.texts) == 1), "the digest replays, there"


def test_a_lost_delivery_is_unhandled_in_the_next_turn_ended(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.script.append(lose("expired: the harness neither started nor refused it"))
    send(rig, "settled lost")
    assert wait_until(lambda: len(ep.texts) == 1)
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert not turn_frames(frames, "handled_ids")
    assert a.looks == 1, "an expired delivery keeps the endpoint"


@pytest.mark.parametrize("outcome", ["answer", "aborted", "error"])
def test_only_a_turn_that_ended_with_an_answer_is_reported_answered(
        rig, adapter, endpoints, frames, outcome):
    """A turn that ended with the model's answer made a request just now: its
    `turn_ended` says `answered`, so the daemon releases held mail without waiting on
    the transcript. An interrupted or failed turn's does not, and its end reads the
    cache as before. The same holds for a delivered digest's own turn."""
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.start_turn()                       # the person's turn
    assert wait_until(lambda: a.turn_open)
    ep.end_turn(outcome)
    assert wait_until(lambda: frame_kinds(frames, "turn"))
    send(rig, "a digest of its own")
    assert wait_until(lambda: len(ep.texts) == 1)
    ep.start_turn(ep.calls[0][1])
    ep.end_turn(outcome)
    assert wait_until(lambda: len(frame_kinds(frames, "turn")) >= 2)
    marks = [f.get("answered") for f in frame_kinds(frames, "turn")[:2]]
    assert marks == ([True, True] if outcome == "answer" else [None, None])


# --------------------------------------------------------------------------- interrupt

def interrupt(rig, text):
    code, out = send(rig, text, "--interrupt")
    assert code == 0, out


def started_at(digest):
    from moxi_comms.store import open_read
    conn = open_read()
    try:
        row = conn.execute("SELECT started_at FROM digests WHERE id = ?",
                           (int(digest),)).fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


def test_interrupt_mail_is_steered_into_the_running_turn_and_acked_at_its_end(
        rig, adapter, endpoints, frames):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.start_turn()                       # the person's turn
    assert wait_until(lambda: a.turn_open)
    send(rig, "plain, mid turn")
    interrupt(rig, "stop and look")
    assert wait_until(lambda: len(ep.calls) == 1)
    (method, did, ref, text) = ep.calls[0]
    assert method == "steer" and "stop and look" in text
    assert "plain, mid turn" not in text, "only the interrupt mail is steered"
    assert "[interrupt]" in text, "the frame line says why it came mid-turn"
    assert ep.turn["deliveries"] == [(did, "steered")], "the turn took it"
    assert wait_until(lambda: started_at(ref) is not None), "turn.steered marks it started"
    assert a.shim.queue and not turn_frames(frames, "handled_ids")
    ep.end_turn("answer")
    assert wait_until(lambda: turn_frames(frames, "handled_ids"))
    (steered,) = turn_frames(frames, "handled_ids")
    assert wait_until(lambda: len(ep.calls) == 2), "the turn end hands the plain mail"
    assert ep.calls[1][0] == "deliver" and "plain, mid turn" in ep.calls[1][3]
    assert "[interrupt]" not in ep.calls[1][3]
    assert steered not in a.shim.held_ids
    assert not paths.cursors_dir().exists(), "the adapter writes no cursor file"


def test_interrupt_mail_to_an_idle_session_is_delivered_unmarked(
        rig, adapter, endpoints):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    interrupt(rig, "while idle")
    assert wait_until(lambda: len(ep.calls) == 1)
    assert ep.calls[0][0] == "deliver"
    assert "[interrupt]" not in ep.calls[0][3], "nothing came mid-turn"


def test_a_session_without_steer_takes_interrupt_mail_at_the_boundary(
        rig, adapter, endpoints, frames):
    ep = endpoints(capabilities=CLAUDE - {"steer"})
    a = adapter()
    assert found(a, ep)
    ep.start_turn()
    assert wait_until(lambda: a.turn_open)
    interrupt(rig, "no steer here")
    time.sleep(0.4)
    assert ep.calls == [], "it waits for the turn's end"
    ep.end_turn("answer")
    assert wait_until(lambda: len(ep.calls) == 1)
    assert ep.calls[0][0] == "deliver"
    assert a.session is not None, "no capability error reached the adapter"


@pytest.mark.parametrize("step", [refuse("idle"), refuse_later("idle")],
                         ids=["from-the-answer", "from-wait-started"])
def test_a_steer_refused_idle_falls_back_to_one_deliver_with_the_same_ref(
        rig, adapter, endpoints, frames, step):
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.start_turn()
    assert wait_until(lambda: a.turn_open)
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


def test_an_unknown_steer_that_resolves_to_started_gets_no_fallback(
        rig, adapter, endpoints, frames, monkeypatch):
    monkeypatch.setattr("moxi_turns.client.READ_S", 0.3)
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.start_turn()
    assert wait_until(lambda: a.turn_open)
    ep.script.append(late(0.8))
    interrupt(rig, "answered late")
    assert wait_until(lambda: len(ep.calls) == 1)
    hand = wait_until(lambda: next(iter(a.hands.values()), None))
    assert hand.steer and not hand.fell_back
    assert wait_until(lambda: hand.delivery.id == ep.calls[0][1]), \
        "the library resolved it by ref on the same connection"
    ep.end_turn("answer")
    assert wait_until(lambda: turn_frames(frames, "handled_ids"))
    assert len(ep.calls) == 1 and not turn_frames(frames, "unhandled_ids")


def test_an_unknown_steer_that_resolves_to_refused_idle_falls_back_once(
        rig, adapter, endpoints, frames, monkeypatch):
    monkeypatch.setattr("moxi_turns.client.READ_S", 0.3)
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.start_turn()
    assert wait_until(lambda: a.turn_open)
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
    a = adapter()
    assert found(a, ep)
    ep.start_turn()
    assert wait_until(lambda: a.turn_open)
    ep.script.append(drop())
    interrupt(rig, "the answer was dropped")
    assert wait_until(lambda: len(ep.calls) == 1)
    hand = wait_until(lambda: next(iter(a.hands.values()), None))
    assert wait_until(lambda: hand.delivery.id == ep.calls[0][1]), "resolved by ref"
    assert a.looks == 1, "the endpoint was kept"
    ep.end_turn("answer")
    assert wait_until(lambda: turn_frames(frames, "handled_ids"))
    assert len(ep.calls) == 1, "nothing was requeued"


def test_an_unknown_whose_endpoint_no_longer_serves_is_unhandled_only_then(
        rig, adapter, endpoints, frames, monkeypatch):
    monkeypatch.setattr("moxi_turns.client.READ_S", 0.3)
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.start_turn()
    assert wait_until(lambda: a.turn_open)
    ep.script.append(late(30))
    interrupt(rig, "never answered")
    assert wait_until(lambda: len(ep.calls) == 1)
    time.sleep(1.0)
    assert not turn_frames(frames, "unhandled_ids"), "an unknown outcome is waited on"
    assert a.hands, "the digest stays open"
    ep.go_away("the endpoint ended")
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert len(ep.calls) == 1


def test_a_wait_whose_connect_stalls_keeps_the_digest_open_and_hands_it_in_once(
        rig, adapter, endpoints, frames, monkeypatch):
    """The endpoint records the delivery, then stalls: it still listens, and each new
    connection's `session` goes unanswered past the library's read bound. The wait reads
    that as a timeout, never as lost, so the digest stays open, the endpoint is kept, and
    the text runs once when the stall clears."""
    monkeypatch.setattr("moxi_turns.client.READ_S", 0.3)
    ep = endpoints(auto=False)                   # the delivery stays pending
    a = adapter()
    assert found(a, ep)
    stall = threading.Event()
    ep.session_gate = stall
    try:
        send(rig, "run me once")
        assert wait_until(lambda: len(ep.calls) == 1)
        time.sleep(1.5)                          # several stalled waits, each paced
        assert not turn_frames(frames, "unhandled_ids"), "a stalled connect is not lost"
        assert a.hands, "the digest stays open"
        assert a.looks == 1, "the endpoint was kept"
    finally:
        stall.set()
    ep.start_turn(ep.calls[0][1])
    ep.end_turn("answer")
    assert wait_until(lambda: turn_frames(frames, "handled_ids"))
    time.sleep(0.5)
    assert len(ep.calls) == 1, "the text was handed in once"
    assert len([t for t in ep.texts if "run me once" in t]) == 1
    assert not turn_frames(frames, "unhandled_ids")


def test_a_steer_the_harness_takes_past_the_read_bound_is_acked_once(
        rig, adapter, endpoints, frames, monkeypatch):
    """The harness confirms the steer a second past the library's read bound. Nothing
    is requeued, the digest is acked once at that turn's end, and no later turn carries
    it."""
    monkeypatch.setattr("moxi_turns.client.READ_S", 0.3)
    ep = endpoints()
    a = adapter()
    assert found(a, ep)
    ep.start_turn()
    assert wait_until(lambda: a.turn_open)
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
