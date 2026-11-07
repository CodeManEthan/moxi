"""The Claude half of the endpoint, in process: the leg (lines out, HTTP in on `mod.sock`)
driven by a stand-in runner and gate, and the endpoint's socket driven through the public
API. The tie's window and its three ends, holding, the context asks, `/clear` and
`/exit`, the live facts and the report queue's order."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time

import pytest

import moxi_turns as turns
from claude_runner import FakeRunner, pipe_pair, post
from conftest import bare, end_endpoint, opener, write_config
from moxi_turns import claude_leg as leg
from moxi_turns import client, listeners, protocol
from moxi_turns.claude_endpoint import ClaudeHarness
from moxi_turns.endpoint import Endpoint
from moxi_turns.listeners import Listener, Registry


@pytest.fixture
def claude(turns_env):
    made = []

    def make(*, registry=None, session="s-1", pid=None):
        r, w = pipe_pair()
        harness = ClaudeHarness(w, pid=os.getpid() if pid is None else pid)
        ep = Endpoint(harness, registry=registry or Registry())
        ep.prepare()
        harness.serve(str(ep.dir / protocol.MOD_SOCK))
        runner = FakeRunner(r)
        harness.first_line()
        runner.wait_first()
        if session:
            runner.session(session)
            ep.open()
        made.append((ep, w))
        return ep, runner

    yield make
    for ep, w in made:
        end_endpoint(ep)
        w.close()


def watcher(s):
    events: queue.Queue = queue.Queue()

    def run():
        for ev in s.watch():
            events.put(ev)

    threading.Thread(target=run, daemon=True).start()
    time.sleep(0.1)
    return events


def drain(events, timeout=0.3):
    out = []
    while True:
        try:
            out.append(events.get(timeout=timeout))
        except queue.Empty:
            return out


def test_the_first_line_and_no_hello_before_the_session_id(claude):
    ep, runner = claude(session=None)
    first = runner.first
    assert first["op"] == "hello" and first["leg"] == leg.LEG
    assert first["stop_wait_ms"] == 2000 and first["listen_wait_ms"] == 2000
    assert first["stop_after_clear"] is leg.STOP_AFTER_CLEAR
    assert first["mod_sock"] == str(ep.dir / protocol.MOD_SOCK)
    assert turns.sessions() == []                # no socket until the id comes
    runner.session("s-9", mod_version="0.1.0")
    ep.open()
    s = turns.find(session_id="s-9")
    assert s is not None and s.harness == "claude"
    assert s.capabilities == frozenset({"deliver", "only-if-idle", "steer", "stop-handler",
                                        "clear", "exit", "compact", "agents",
                                        "stop-shells", "guards", "after-tool", "usage",
                                        "environ"})
    assert s.info["mod_version"] == "0.1.0"


def test_an_idle_start_is_tied_at_the_resolve_and_its_events_wait(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    events = watcher(s)
    d = s.deliver("hello", only_if_idle=True, refuse_draft=True)
    line = runner.next_line()
    assert line == {"op": "submit", "delivery": d.id, "kind": "start", "text": "hello",
                    "nonce": line["nonce"]}
    assert runner.enter(line["nonce"], draft=False, agents=None)["verdict"] == "pass"
    runner.turn_start("T1")
    assert s.facts().turn_open                   # the ledger opened at the report
    assert drain(events) == []                   # its TurnStarted waits for the close
    with pytest.raises(TimeoutError):
        d.wait_started(0.1)
    runner.close(d.id, leg.END_RESOLVE)
    assert bare(d.wait_started(2)) == turns.Started("T1")
    runner.turn_complete("T1")
    assert bare(d.wait_ended(2)) == turns.Ended("T1", "answer")
    got = drain(events)
    assert [type(e).__name__ for e in got] == ["TurnStarted", "TurnEnded"]
    assert opener(got[0]) == d.id and opener(got[1]) == d.id


def test_a_turn_that_ends_before_the_close_raises_both_events_at_the_close(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    events = watcher(s)
    d = s.deliver("quick")
    runner.next_line()
    runner.turn_start("T1")
    runner.turn_complete("T1", reason="aborted", is_aborted=True)
    assert drain(events) == []
    runner.close(d.id, leg.END_RESOLVE)
    assert bare(d.wait_ended(2)) == turns.Ended("T1", "aborted")
    assert [(type(e).__name__, opener(e)) for e in drain(events)] == [
        ("TurnStarted", d.id), ("TurnEnded", d.id)]


def test_a_person_turn_in_the_window_is_released_by_the_later_turn_start(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    events = watcher(s)
    d = s.deliver("mail")
    runner.next_line()
    runner.turn_start("P")
    runner.turn_complete("P")
    runner.turn_start("D")
    runner.close(d.id, leg.END_RESOLVE)
    assert bare(d.wait_started(2)) == turns.Started("D")
    got = [(type(e).__name__, e.turn_id, opener(e)) for e in drain(events)]
    assert got == [("TurnStarted", "P", None), ("TurnEnded", "P", None),
                   ("TurnStarted", "D", d.id)]


def test_a_resolve_with_no_turn_held_reads_lost_untied(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("x")
    runner.next_line()
    runner.close(d.id, leg.END_RESOLVE)
    result = d.wait_started(2)
    assert isinstance(result, turns.Lost) and result.untied
    assert result.reason.startswith("untied")
    assert d.wait_ended(1) == result


def test_a_fail_releases_the_held_turn_and_reads_lost_untied(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    events = watcher(s)
    d = s.deliver("x")
    runner.next_line()
    runner.turn_start("T")
    runner.close(d.id, leg.END_FAIL, "unsettled after 5 s idle")
    result = d.wait_ended(2)
    assert isinstance(result, turns.Lost) and result.untied
    assert "unsettled after 5 s idle" in result.reason
    assert [(type(e).__name__, opener(e)) for e in drain(events)] == [
        ("TurnStarted", None)]


def test_another_plugin_drop_reads_dropped_and_releases_the_held_turn(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    events = watcher(s)
    d = s.deliver("x")
    runner.next_line()
    runner.turn_start("T")
    runner.close(d.id, leg.END_DROP, "some-plugin: no")
    assert bare(d.wait_started(2)) == turns.Refused("dropped")
    assert bare(d.wait_ended(1)) == turns.Refused("dropped")
    assert [(type(e).__name__, opener(e)) for e in drain(events)] == [
        ("TurnStarted", None)]


@pytest.mark.parametrize("code", ["gone", "unsupported"])
def test_a_drop_the_endpoint_did_not_record_reads_unsupported(claude, code):
    # The gate's 1 s timeout or a fault (`gone`), a missing gate (`unsupported`): the
    # runner reads the code from the drop's line. The text never ran, and `gone` is no
    # refusal a delivery reads, so either reads `unsupported`.
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("x", only_if_idle=True)
    runner.next_line()
    runner.close(d.id, leg.END_DROP, code)
    assert bare(d.wait_started(2)) == turns.Refused("unsupported")
    assert bare(d.wait_ended(1)) == turns.Refused("unsupported")


def test_the_gate_refusal_and_the_next_submit_waits_for_the_close(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("x", only_if_idle=True, refuse_draft=True)
    line = runner.next_line()
    answer = runner.enter(line["nonce"], draft=True, agents=None)
    assert answer["verdict"] == "drop" and answer["reason"] == "draft"
    assert "draft" in answer["text"]
    assert bare(d.wait_started(2)) == turns.Refused("draft")
    # The core's slot is free, but the window is not closed yet: no second submit.
    d2 = s.deliver("next")
    assert runner.no_line()
    runner.close(d.id, leg.END_DROP, answer["text"])
    line = runner.next_line()
    assert line["delivery"] == d2.id and line["kind"] == "deliver"
    assert bare(d.wait_ended(1)) == turns.Refused("draft")      # the gate's reason stands


def test_the_gate_refuses_a_start_when_a_turn_opened_since_the_read(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("x", only_if_idle=True, if_turn_number=0)
    line = runner.next_line()
    runner.turn_start("P")                       # a person's turn in the window
    answer = runner.enter(line["nonce"], draft=None, agents=None)
    assert answer == {"ok": True, "verdict": "drop", "reason": "busy-turn",
                      "text": answer["text"]}
    runner.close(d.id, leg.END_DROP, answer["text"])
    assert bare(d.wait_ended(2)) == turns.Refused("busy-turn")


def test_reports_apply_once_in_seq_order_and_a_gap_fails_the_window(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("x")
    runner.next_line()
    runner.report(leg.R_TURN_START, seq=2, turn_id="T")
    runner.report(leg.R_TURN_START, seq=2, turn_id="T")    # a resend: no second turn
    assert s.facts().turn_number == 1
    # Report 3 (say, the turn's end) never lands; 4 arrives: the window fails.
    runner.report(leg.R_SUBMIT_CLOSE, seq=4, delivery=d.id, end=leg.END_RESOLVE)
    result = d.wait_started(2)
    assert isinstance(result, turns.Lost) and result.untied


def test_a_close_after_the_window_failed_changes_nothing(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("x")
    runner.next_line()
    runner.close(d.id, leg.END_FAIL, "bound")
    runner.turn_start("late")
    runner.close(d.id, leg.END_RESOLVE)
    assert d.wait_started(1).untied
    events = watcher(s)
    runner.turn_complete("late")
    assert [(type(e).__name__, opener(e)) for e in drain(events)] == [
        ("TurnEnded", None)]


def test_reports_before_the_session_id_keep_the_turn_count(claude):
    ep, runner = claude(session=None)
    runner.turn_start("T0")                      # `claude "prompt"`: a turn before the id
    runner.session("s-5")
    ep.open()
    f = turns.find(session_id="s-5").facts()
    assert f.turn_number == 1 and f.turn_open and f.session_id == "s-5"


def test_live_facts_come_from_the_runner_and_an_unanswered_read_holds(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    runner.agents, runner.draft = 2, True
    f = s.facts()
    assert (f.agents, f.draft) == (2, True)
    runner.agents = None                         # $.agent.list() could not be read
    assert s.facts().agents is None
    runner.agents = 0
    runner.answer_live = False
    t0 = time.monotonic()
    f = s.facts()
    assert f.draft is True and f.agents is None and time.monotonic() - t0 < 1.8
    # A start's own checks are the gate's, from what it reads at entry: an unreadable
    # subagent list counts as busy and an unreadable box holds.
    for checks, reason in (({"refuse_agents": True}, "busy-agents"),
                           ({"refuse_draft": True}, "draft")):
        d = s.deliver("x", only_if_idle=True, **checks)
        line = runner.next_line()
        answer = runner.enter(line["nonce"], draft=None, agents=None)
        assert answer["verdict"] == "drop" and answer["reason"] == reason
        assert bare(d.wait_started(1)) == turns.Refused(reason)
        runner.close(d.id, leg.END_DROP, answer["text"])


def test_agents_maps_the_runners_list(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    runner.agent_list = [
        {"id": "a1", "type": "Explore", "description": "find it", "status": "running"},
        {"id": "a2", "type": "general-purpose", "description": "d", "status": "pending",
         "parentId": "a1"},
        {"id": "a3", "type": "x", "description": "d", "status": "waiting"},
        {"id": "t1", "type": "teammate", "description": "d", "status": "idle",
         "teammateId": "ann@crew"},
        {"id": "a4", "type": "x", "description": "d", "status": "completed"},
        {"id": "a5", "type": "x", "description": "d", "status": "failed"},
        {"id": "a6", "type": "x", "description": "d", "status": "killed"},
        {"id": "a7", "type": "x", "description": "d", "status": "hibernating"},
    ]
    a = s.agents()
    assert a.complete
    assert [(e.id, e.kind, e.state, e.final) for e in a.entries] == [
        ("a1", "subagent", "running", False), ("a2", "subagent", "pending", False),
        ("a3", "subagent", "waiting", False), ("t1", "teammate", "idle", False),
        ("a4", "subagent", "done", True), ("a5", "subagent", "failed", True),
        ("a6", "subagent", "stopped", True), ("a7", "subagent", "unknown", None)]
    assert a.entries[0].description == "find it" and a.entries[1].parent_id == "a1"
    assert a.busy() == 3


def test_an_unread_or_late_agent_list_is_not_complete(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    runner.agent_list = None                     # $.agent.list() could not be read
    assert s.agents().complete is False
    runner.agent_list = [{"id": "a1", "status": "running"}, {"status": "running"}]
    a = s.agents()                               # an entry with no id: not whole
    assert a.complete is False and [e.id for e in a.entries] == ["a1"]
    runner.agent_list = []
    assert s.agents().complete is True
    runner.answer_live = False
    t0 = time.monotonic()
    assert s.agents().complete is False and time.monotonic() - t0 < 1.8


def test_a_final_notice_in_an_open_turn_reads_final_at_the_next_read(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    runner.turn_start("T1")
    runner.agent_list = [{"id": "bg", "type": "x", "description": "d", "status": "running"}]
    assert s.agents().entries[0].final is False
    # The task's notification: the engine records it completed; the turn stays open.
    runner.agent_list = [{"id": "bg", "type": "x", "description": "d",
                          "status": "completed"}]
    a = s.agents()
    assert a.entries[0].final is True and a.entries[0].state == "done"
    assert s.facts().turn_open


def test_holding_answers_the_stop_ask_within_hold_wait(claude, turns_env):
    write_config(turns_env, "[stop_handler]\nwait_ms = 300\n")
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    answers = iter(["reply first", None])
    h = s.handle_stops(lambda facts: next(answers))
    time.sleep(0.1)
    stop = {"turn_id": "T", "session_id": "s-1", "stop_hook_active": False,
            "task_types": ["bash"], "crons": 0}
    assert runner.post(leg.PATH_STOP, stop)["reason"] == "reply first"
    assert runner.post(leg.PATH_STOP, {**stop, "stop_hook_active": True})["reason"] is None
    h.close()
    slow = s.handle_stops(lambda facts: time.sleep(2) or "too late")
    time.sleep(0.1)
    t0 = time.monotonic()
    assert runner.post(leg.PATH_STOP, stop)["reason"] is None
    assert time.monotonic() - t0 < 1.0               # the endpoint kept stop_handler.wait_ms
    slow.close()


def _ctx(event):
    return f"{event.event}:{event.source}:{event.session_id}"


def test_context_asks_reach_the_listeners_and_a_clear_follows_the_id(claude):
    registry = Registry((Listener("comms", frozenset({"start", "prompt"}),
                                  f"{__name__}:_ctx"),))
    ep, runner = claude(registry=registry)
    s = turns.find(session_id="s-1")
    events = watcher(s)
    assert runner.post(leg.PATH_CONTEXT, {"event": "ready", "source": "startup",
                                          "session_id": "s-1"})["context"] \
        == "start:startup:s-1"
    assert runner.post(leg.PATH_CONTEXT, {"event": "prompt"})["context"] \
        == "prompt:None:s-1"
    assert runner.post(leg.PATH_CONTEXT, {"event": "ready", "source": "clear",
                                          "session_id": "s-2"})["context"] \
        == "start:clear:s-2"
    assert turns.find(session_id="s-2").dir == ep.dir
    assert [(type(e).__name__, e.source) for e in drain(events)] == [
        ("Ready", "startup"), ("Ready", "clear")]


def _command(s, name, out):
    out.put(s.command(name, timeout=5))


def test_clear_runs_through_the_gate_and_reads_ran_with_the_new_id(claude, monkeypatch):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "clear")
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    out: queue.Queue = queue.Queue()
    threading.Thread(target=_command, args=(s, "clear", out), daemon=True).start()
    line = runner.next_line()
    assert line["op"] == "command" and line["name"] == "clear"
    assert runner.post(leg.PATH_GATE_COMMAND, {"request": line["request"], "agents": 0,
                                               "draft": False})["verdict"] == "pass"
    runner.post(leg.PATH_CONTEXT, {"event": "ready", "source": "clear",
                                   "session_id": "s-2"})
    assert out.get(timeout=3) == turns.Ran("s-2")


def test_the_gate_refuses_a_command_on_a_draft(claude, monkeypatch):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "clear")
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    out: queue.Queue = queue.Queue()
    threading.Thread(target=_command, args=(s, "clear", out), daemon=True).start()
    line = runner.next_line()
    answer = runner.post(leg.PATH_GATE_COMMAND, {"request": line["request"], "agents": 0,
                                                 "draft": True})
    assert answer["verdict"] == "drop" and answer["reason"] == "draft"
    assert out.get(timeout=3) == turns.Refused("draft")


@pytest.mark.parametrize("exits", [True, False])
def test_exit_reads_ran_only_once_the_harness_process_is_gone(claude, monkeypatch, exits):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "exit")
    monkeypatch.setattr(client, "EXIT_BOUND_S", 0.5)
    harness_proc = subprocess.Popen(["sleep", "30"])
    try:
        ep, runner = claude(pid=harness_proc.pid)
        s = turns.find(session_id="s-1")
        out: queue.Queue = queue.Queue()
        threading.Thread(target=_command, args=(s, "exit", out), daemon=True).start()
        line = runner.next_line()
        assert runner.post(leg.PATH_GATE_COMMAND, {"request": line["request"],
                                                   "agents": 0,
                                                   "draft": False})["verdict"] == "pass"
        if exits:
            harness_proc.kill()
            harness_proc.wait()
            assert out.get(timeout=3) == turns.Ran(None)
        else:
            # A reload's SIGTERM, or an exit held: the process lives on.
            assert out.get(timeout=3) == turns.Failed("not-exited")
    finally:
        harness_proc.kill()
        harness_proc.wait()


def test_steer_appends_and_a_failed_append_refuses_idle(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    runner.turn_start("T")
    d = s.steer("also this")
    assert runner.next_line() == {"op": "append", "delivery": d.id, "text": "also this"}
    with pytest.raises(TimeoutError):
        d.wait_started(0.2)                      # started only at the runner's word
    runner.report(leg.R_APPEND_DONE, delivery=d.id)
    assert bare(d.wait_started(1)) == turns.Started("T")
    d = s.steer("and this")
    assert runner.next_line()["delivery"] == d.id
    runner.report(leg.R_APPEND_FAILED, delivery=d.id, reason="no turn running")
    assert bare(d.wait_ended(1)) == turns.Refused("idle")


def test_the_endpoint_end_releases_a_held_turn_then_answers_lost(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    events = watcher(s)
    d = s.deliver("x")
    runner.next_line()
    runner.turn_start("T")
    ep.stop("SIGTERM: Claude Code ended the endpoint")
    got = [(type(e).__name__, opener(e)) for e in drain(events)]
    assert got[:2] == [("TurnStarted", None), ("Gone", None)]
    lost = ep.wait(d.id, "ended", 0)
    assert isinstance(lost, turns.Lost) and not lost.untied


def test_a_bad_request_is_answered_400_and_the_leg_lives(claude):
    ep, runner = claude()
    status, answer = post(runner.mod_sock, leg.PATH_REPORT, {"seq": 1, "kind": "nonsense"})
    assert status == 400 and answer["ok"] is False
    status, _ = post(runner.mod_sock, "/nowhere", {})
    assert status == 404
    assert turns.find(session_id="s-1").facts().session_id == "s-1"


def test_a_window_the_runner_never_closes_expires_and_frees_the_next(claude):
    ep, runner = claude()
    ep.pending_ttl_s = 0.3
    s = turns.find(session_id="s-1")
    d1 = s.deliver("one")
    runner.next_line()
    result = d1.wait_started(3)
    assert isinstance(result, turns.Lost) and result.reason.startswith("expired")
    d2 = s.deliver("two")
    assert runner.next_line()["delivery"] == d2.id


# -- entry (a runner prompt enters only as the open window's, admitted once) -----------


def test_each_window_has_a_random_nonce(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    nonces = []
    for _ in range(3):
        d = s.deliver("x")
        line = runner.next_line()
        nonces.append(line["nonce"])
        runner.close(d.id, leg.END_FAIL, "test")
    assert len(set(nonces)) == 3
    assert all(len(n) == 32 and int(n, 16) >= 0 for n in nonces)      # 128 bits, hex


def test_a_deliver_is_admitted_once_with_the_open_windows_nonce(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("mail")
    line = runner.next_line()
    assert runner.enter(line["nonce"]) == {"ok": True, "verdict": "pass"}
    again = runner.enter(line["nonce"])          # the same window cannot admit twice
    assert again["verdict"] == "drop" and again["reason"] == leg.LATE
    runner.turn_start("T")
    runner.close(d.id, leg.END_RESOLVE)
    assert bare(d.wait_started(2)) == turns.Started("T")


def test_a_nonce_no_window_of_this_endpoint_names_is_dropped_late(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("mail")
    runner.next_line()
    answer = runner.enter("0" * 32)              # an earlier endpoint's window, say
    assert answer["verdict"] == "drop" and answer["reason"] == leg.LATE
    assert answer["text"] == "moxi-turns-gate: a late prompt was dropped (late)"
    with pytest.raises(TimeoutError):
        d.wait_started(0.1)                      # the open window is untouched


def test_a_prompt_entering_after_the_bound_failed_its_window_is_dropped(claude):
    # A /config dialog holds the submit; the bound reports at 5 s, the window
    # fails and reads Lost; when the dialog closes the prompt enters with the closed
    # window's nonce and is dropped, so its text never runs.
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    events = watcher(s)
    d = s.deliver("mail")
    line = runner.next_line()
    runner.bound(d.id)
    result = d.wait_started(2)
    assert isinstance(result, turns.Lost) and result.untied
    assert "unsettled after 5000 ms" in result.reason
    answer = runner.enter(line["nonce"])
    assert answer["verdict"] == "drop" and answer["reason"] == leg.LATE
    runner.close(d.id, leg.END_DROP, leg.LATE)   # its settle, after the window closed
    assert d.wait_ended(1) == result             # the answer given stands
    assert drain(events) == []                   # no turn ran


def test_a_start_entering_after_its_window_failed_never_runs_unchecked(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("go", only_if_idle=True, if_turn_number=0)
    line = runner.next_line()
    runner.bound(d.id)
    assert d.wait_started(2).untied
    answer = runner.enter(line["nonce"])
    assert answer["verdict"] == "drop" and answer["reason"] == leg.LATE


def test_the_rehanded_text_runs_once(claude):
    # Window A fails behind the dialog; the caller hands the text again as B. When the
    # dialog closes A's prompt names a closed window and is dropped, B's is admitted.
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    a = s.deliver("same text")
    line_a = runner.next_line()
    runner.bound(a.id)
    assert a.wait_started(2).untied
    b = s.deliver("same text")
    line_b = runner.next_line()
    assert line_b["text"] == line_a["text"] and line_b["nonce"] != line_a["nonce"]
    assert runner.enter(line_a["nonce"])["reason"] == leg.LATE
    runner.close(a.id, leg.END_DROP, leg.LATE)   # A's settle while B is open: keyed to A
    assert runner.enter(line_b["nonce"])["verdict"] == "pass"
    runner.turn_start("TB")
    runner.close(b.id, leg.END_RESOLVE)
    assert bare(b.wait_started(2)) == turns.Started("TB")


def test_the_bound_after_admission_changes_nothing(claude):
    # Admission and the bound are decided in arrival order: ask first, the bound's report
    # is ignored and the window waits for the settle (settings hooks after the gate).
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("mail")
    line = runner.next_line()
    assert runner.enter(line["nonce"])["verdict"] == "pass"
    runner.bound(d.id)
    with pytest.raises(TimeoutError):
        d.wait_started(0.2)
    d2 = s.deliver("next")
    assert runner.no_line()                      # the window is still open
    runner.turn_start("T")
    runner.close(d.id, leg.END_RESOLVE)
    assert bare(d.wait_started(2)) == turns.Started("T")
    runner.turn_complete("T")
    assert runner.next_line()["delivery"] == d2.id


def test_the_gate_dropping_an_admitted_prompt_reads_unsupported(claude):
    # The endpoint admitted it, but the answer missed the gate's 1 s: the gate drops it
    # `gone`, and the admitted window closes by that drop. The text never ran.
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("mail")
    line = runner.next_line()
    assert runner.enter(line["nonce"])["verdict"] == "pass"
    runner.close(d.id, leg.END_DROP, "gone")
    assert bare(d.wait_ended(2)) == turns.Refused("unsupported")


def test_a_lost_report_fails_even_an_admitted_window(claude):
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    d = s.deliver("mail")
    line = runner.next_line()
    assert runner.enter(line["nonce"])["verdict"] == "pass"
    runner.report(leg.R_TURN_START, seq=9, turn_id="T")   # reports 2-8 never landed
    assert d.wait_started(2).untied


def test_an_admitted_window_closes_at_the_core_expiry_and_frees_the_slot(claude):
    # The liveness bound of an admitted window is the core's
    # PENDING_TTL_S. At it the window closes, its held turn goes out as the person's,
    # and the next submit is handed over.
    ep, runner = claude()
    ep.pending_ttl_s = 0.4
    s = turns.find(session_id="s-1")
    events = watcher(s)
    d1 = s.deliver("one")
    line = runner.next_line()
    assert runner.enter(line["nonce"])["verdict"] == "pass"
    runner.turn_start("P")                       # a turn held in the window
    runner.turn_complete("P")
    result = d1.wait_started(3)
    assert isinstance(result, turns.Lost) and result.reason.startswith("expired")
    got = [(type(e).__name__, e.turn_id, opener(e)) for e in drain(events)]
    assert got == [("TurnStarted", "P", None), ("TurnEnded", "P", None)]
    d2 = s.deliver("two")
    assert runner.next_line()["delivery"] == d2.id
    assert runner.enter(line["nonce"])["reason"] == leg.LATE


def _gate_pass(runner, line):
    assert runner.post(leg.PATH_GATE_COMMAND, {"request": line["request"], "agents": 0,
                                               "draft": False})["verdict"] == "pass"


def _done(runner, line, **fields):
    runner.report(leg.R_COMMAND_DONE, **{"request": line["request"], "state": "ran",
                                         "reason": None, "new_session_id": None, **fields})


def test_compact_runs_through_the_gate_and_settles_at_its_session_start(claude,
                                                                        monkeypatch):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "compact")
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    out: queue.Queue = queue.Queue()
    threading.Thread(target=_command, args=(s, "compact", out), daemon=True).start()
    line = runner.next_line()
    assert line == {"op": "command", "request": line["request"], "name": "compact",
                    "stop_shells": False}
    _gate_pass(runner, line)
    _done(runner, line, new_session_id="s-1")
    assert out.empty() and not _settles(out)   # the call's return alone settles nothing
    runner.post(leg.PATH_CONTEXT, {"event": "ready", "source": "compact",
                                   "session_id": "s-1"})
    assert out.get(timeout=3) == turns.Ran(None)


def _settles(out, timeout=0.3):
    try:
        out.put(out.get(timeout=timeout))
        return True
    except queue.Empty:
        return False


def test_a_clear_with_stop_shells_settles_at_the_stop_report_not_its_session_start(
        claude, monkeypatch):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "clear")
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    out: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: out.put(s.command("clear", stop_shells=True,
                                                      timeout=5)), daemon=True).start()
    line = runner.next_line()
    assert line["name"] == "clear" and line["stop_shells"] is True
    _gate_pass(runner, line)
    runner.post(leg.PATH_CONTEXT, {"event": "ready", "source": "clear",
                                   "session_id": "s-2"})
    assert not _settles(out)
    _done(runner, line, new_session_id="s-2", shells={
        "stopped": ["b1"], "left": [{"id": "m1", "why": "timeout"}], "complete": False})
    assert out.get(timeout=3) == turns.Ran("s-2", turns.Shells(
        ("b1",), (turns.LeftShell("m1", "timeout"), turns.LeftShell(None, "unlisted")),
        False))


@pytest.mark.parametrize("shells,expected", [
    ({"stopped": [], "left": [], "complete": True}, turns.Shells((), (), True)),
    ("garbage", turns.Shells((), (turns.LeftShell(None, "unlisted"),), False)),
    (None, turns.Shells((), (turns.LeftShell(None, "unlisted"),), False)),
])
def test_a_compact_with_stop_shells_settles_at_the_later_of_ready_and_report(
        claude, monkeypatch, shells, expected):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "compact")
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    out: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: out.put(s.command("compact", stop_shells=True,
                                                      timeout=5)), daemon=True).start()
    line = runner.next_line()
    _gate_pass(runner, line)
    runner.post(leg.PATH_CONTEXT, {"event": "ready", "source": "compact",
                                   "session_id": "s-1"})
    assert not _settles(out)
    _done(runner, line, **({} if shells is None else {"shells": shells}))
    assert out.get(timeout=3) == turns.Ran(None, expected)


def test_a_stop_the_runner_turned_down_reads_unsupported(claude, monkeypatch):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "clear")
    ep, runner = claude()
    s = turns.find(session_id="s-1")
    out: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: out.put(s.command("clear", stop_shells=True,
                                                      timeout=5)), daemon=True).start()
    line = runner.next_line()
    runner.report(leg.R_COMMAND_DONE, request=line["request"], state="refused",
                  reason="unsupported", new_session_id=None)
    assert out.get(timeout=3) == turns.Refused("unsupported")


# -- mod.sock's path ------------------------------------------------------------------

def test_mod_sock_sits_in_the_endpoint_directory_when_its_path_is_short(
        turns_env, short_runtime):
    from moxi_turns.claude_endpoint import mod_sock_path

    ep = Endpoint(ClaudeHarness(open(os.devnull, "wb"), pid=os.getpid()), registry=Registry())
    assert mod_sock_path(ep) == str(ep.dir / protocol.MOD_SOCK)


@pytest.mark.skipif(sys.platform == "win32", reason="no Unix socket paths on Windows")
def test_a_long_runtime_root_puts_mod_sock_on_the_short_path_and_the_endpoint_starts(
        turns_env, monkeypatch):
    from moxi import paths

    from moxi_turns.claude_endpoint import mod_sock_path

    long_root = turns_env / ("r" * 60) / ("u" * 60)
    monkeypatch.setenv("MOXI_TURNS_RUNTIME_DIR", str(long_root))
    short_root = turns_env / "s"           # short, and the test's own, never /tmp itself
    short_root.mkdir()
    monkeypatch.setattr(paths, "_SHORT_ROOT", str(short_root))
    r, w = pipe_pair()
    harness = ClaudeHarness(w, pid=os.getpid())
    ep = Endpoint(harness, registry=Registry())
    assert len(os.fsencode(str(ep.dir / protocol.MOD_SOCK))) >= paths.SOCKET_MAX
    ep.prepare()
    sock = mod_sock_path(ep)
    assert sock.startswith(str(short_root / f"moxi-{os.getuid()}") + os.sep)
    assert len(os.fsencode(sock)) < paths.SOCKET_MAX
    harness.serve(sock)
    try:
        runner = FakeRunner(r)
        harness.first_line()
        assert runner.wait_first()["mod_sock"] == sock
        runner.session("s-long")
        ep.open()
        assert [s.session_id for s in turns.sessions()] == ["s-long"]
    finally:
        ep.stop("the test ended")
        w.close()
    assert not os.path.lexists(sock)
    assert not ep.dir.exists()


def test_after_tool_floors_usage_and_the_prompt_through_the_leg(claude):
    import listener_targets

    listener_targets.CALLS.clear()
    listener_targets.FLOOR["value"] = 30_000
    registry = Registry((
        Listener("usage", frozenset({"after-tool"}), "listener_targets:after",
                 "listener_targets:floor"),
        Listener("usage", frozenset({"prompt", "usage"}), "listener_targets:seen")))
    ep, runner = claude(registry=registry)
    first = runner.first
    assert first["floors"] == {"listener_targets:after": 0}
    assert first["after_tool_wait_ms"] == 300
    runner.post(leg.PATH_CONTEXT, {"event": "ready", "source": "startup",
                                   "session_id": "s-1", "transcript_path": "/p/s-1.jsonl"})
    # The floors line went out before the ready's answer.
    assert runner.next_line(timeout=0.1) == {"op": "floors",
                                             "rows": {"listener_targets:after": 30_000}}
    assert ep.harness.record_path == "/p/s-1.jsonl"
    answer = runner.post(leg.PATH_AFTER_TOOL, {
        "call": {"tool": "Bash", "tool_use_id": "u1", "agentId": None, "is_error": False},
        "rows": ["listener_targets:after"]})
    assert answer == {"ok": True, "context": "after Bash"}
    assert ("after", "Bash", None, False, "after", "/p/s-1.jsonl", ep.harness.cwd) \
        in listener_targets.CALLS
    assert runner.post(leg.PATH_AFTER_TOOL, {"call": {"tool": "Bash"}, "rows": []}) \
        == {"ok": True, "context": None}
    # An agent's call: the floor read after it drops to 0, and the runner is told.
    listener_targets.FLOOR["value"] = 0
    runner.post(leg.PATH_AFTER_TOOL, {"call": {"tool": "Read", "agentId": "a1"}})
    assert runner.next_line() == {"op": "floors", "rows": {"listener_targets:after": 0}}
    for bad in ({"rows": []}, {"call": {"tool": "Bash"}, "rows": "x"},
                {"call": {"tool": ""}}):
        status, _ = post(runner.mod_sock, leg.PATH_AFTER_TOOL, bad)
        assert status == 400
    assert post(runner.mod_sock, leg.PATH_USAGE, {"usage": [1]})[0] == 400
    reading = {"startedAt": 1, "context": {"tokens": 5, "window": 9}, "rateLimits": []}
    assert runner.post(leg.PATH_USAGE, {"usage": reading}) == {"ok": True}
    runner.post(leg.PATH_CONTEXT, {"event": "prompt", "session_id": "s-1",
                                   "text": "hi there", "origin": "composer"})
    time.sleep(0.2)
    seen = [c for c in listener_targets.CALLS if c[0] in ("usage", "prompt")]
    assert [(c[0], c[2], c[3], c[4]) for c in seen] == [
        ("usage", None, None, reading), ("prompt", "hi there", "composer", None)]
    assert all(c[1] == ep.harness.cwd for c in seen)


def test_an_after_tool_events_cwd_is_the_launch_directory_not_the_calls(claude):
    import listener_targets

    listener_targets.CALLS.clear()
    registry = Registry((Listener("usage", frozenset({"after-tool"}),
                                  "listener_targets:after"),))
    ep, runner = claude(registry=registry)
    assert os.path.isabs(ep.harness.cwd) and ep.harness.cwd != "/elsewhere"
    # The guard's ask names the directory a shell cd moved to; the after-tool ask that
    # follows, even one that names it too, still reaches its listener with the launch
    # directory, which a cd does not move.
    runner.post(leg.PATH_GUARD, {"call": {"tool": "Bash", "tool_use_id": "u1",
                                          "input": {}, "cwd": "/elsewhere"},
                                 "guards": []})
    runner.post(leg.PATH_AFTER_TOOL, {
        "call": {"tool": "Bash", "tool_use_id": "u1", "cwd": "/elsewhere"},
        "rows": ["listener_targets:after"]})
    (after,) = [c for c in listener_targets.CALLS if c[0] == "after"]
    assert after[6] == ep.harness.cwd
