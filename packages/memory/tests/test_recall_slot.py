"""The slot route: a recall's fork launched into a slot through the launcher's interface
3, stamped with the recall's id, its rounds delivered to the fork's own session and
acked there; the route a record names, and where a taker reads a round's end; the fork
ended by its owner once the recall's last round is settled, by a taker that settles the
line, or by a discard whose line's owner is gone, and never while its round is open."""

from __future__ import annotations

import os
import time
import types

import moxi_turns
import pytest
from moxi_turns import Refused

from moxi_memory import (doctor, recall_child, recall_discard, recall_route, recall_start,
                         recall_stream, recall_take, recalls, records)

SESSION = "0190b000-0000-7000-8000-000000000031"
FORK = "0190b000-0000-7000-8000-0000000000f1"


DEAD = {"pid": os.getpid(), "started": 1.0}


class Launcher:
    def __init__(self, session_id=FORK, error=None, exits=("exited",)):
        self.session_id, self.error, self.forks = session_id, error, []
        self.exits, self.exited = list(exits), []

    def fork(self, session_id, **kw):
        self.forks.append((session_id, kw))
        if self.error is not None:
            raise self.error
        return types.SimpleNamespace(session_id=self.session_id)

    def exit_session(self, session_id):
        self.exited.append(session_id)
        verdict = self.exits.pop(0) if len(self.exits) > 1 else self.exits[0]
        if isinstance(verdict, BaseException):
            raise verdict
        word = "turn" if verdict == "held" else None
        return types.SimpleNamespace(exit_code=0, lines=(),
                                     data={"outcome": verdict, "word": word})


class Endpoint:
    def __init__(self):
        self.delivered, self.closed = [], False

    def deliver(self, text, **kw):
        self.delivered.append((text, kw))
        return types.SimpleNamespace(id=f"d{len(self.delivered)}", ref=kw.get("ref"))

    def close(self):
        self.closed = True


@pytest.fixture
def turns(monkeypatch):
    endpoint, readied, acked = Endpoint(), [], []

    def wait_ready(*, session_id, timeout_s):
        readied.append((session_id, timeout_s))
        return endpoint

    monkeypatch.setattr(moxi_turns, "wait_ready", wait_ready)
    monkeypatch.setattr(moxi_turns, "ack", lambda ref, *, session_id: acked.append(
        (ref, session_id)))
    return types.SimpleNamespace(endpoint=endpoint, readied=readied, acked=acked)


def test_the_first_round_forks_the_session_into_a_slot_and_delivers_the_brief(turns):
    launcher = Launcher()
    route = recall_route.Slot(SESSION, "r-1", launcher=launcher)
    handle = route.first("the brief", ref="r-1:1", round_timeout_s=90.0)
    assert launcher.forks == [(SESSION, {"target": "slot", "stamps": {
        recall_start.SUBCONSCIOUS_FORK_ENV: "r-1"}})]
    assert turns.readied == [(FORK, 90.0)]
    assert turns.endpoint.delivered == [("the brief", {"by": "memory", "ref": "r-1:1",
                                                       "keep": True})]
    assert route.started(handle) == FORK and route.turns_session == FORK
    route.next(handle, "go on", ref="r-1:2", round_timeout_s=90.0)
    assert turns.readied == [(FORK, 90.0)]
    assert turns.endpoint.delivered[1] == ("go on", {"by": "memory", "ref": "r-1:2",
                                                     "keep": True})
    route.ack("r-1:1")
    assert turns.acked == [("r-1:1", FORK)]
    route.close()
    assert turns.endpoint.closed


def test_a_fork_the_launcher_refuses_is_a_refused_round(turns):
    route = recall_route.Slot(SESSION, "r-1", launcher=Launcher(error=RuntimeError("no")))
    end = route.ended(route.first("b", ref="r-1:1", round_timeout_s=9.0), 9.0)
    assert isinstance(end, Refused) and "the launcher: no" in str(end.reason)
    assert route.started(None) is None and turns.endpoint.delivered == []


def test_a_fork_whose_endpoint_never_readies_is_a_lost_round(monkeypatch):
    def never(**kw):
        raise TimeoutError("not ready")

    monkeypatch.setattr(moxi_turns, "wait_ready", never)
    route = recall_route.Slot(SESSION, "r-1", launcher=Launcher())
    end = route.ended(route.first("b", ref="r-1:1", round_timeout_s=9.0), 9.0)
    assert type(end).__name__ == "Lost" and "not ready in time" in str(end.reason)
    assert route.started(None) == FORK


def test_a_later_round_with_no_fork_is_lost():
    route = recall_route.Slot(SESSION, "r-1", launcher=Launcher())
    end = route.ended(route.next(None, "b", ref="r-1:2", round_timeout_s=9.0), 9.0)
    assert type(end).__name__ == "Lost"


def a_recall(route_name, **fields):
    record = recall_start.begin(SESSION, "claude", start="prompt", text="ingest lock",
                                senders=(), settings=recall_start.default_settings(),
                                cwd=None, notes_dir=None, route_name=route_name,
                                spawn=lambda s, r: None)
    if fields:
        records.update(SESSION, record["recall"], **fields)
    return recall_child.Recall(records.read(SESSION, record["recall"]))


def test_a_record_on_the_slot_route_gets_the_slot_route_with_its_fork():
    route = recall_route.for_recall(a_recall("slot", fork=FORK))
    assert isinstance(route, recall_route.Slot) and route.fork == FORK
    assert route.recall_id and route.turns_session == FORK
    assert isinstance(recall_route.for_recall(a_recall("subagent")), recall_route.Subagent)
    assert recall_start.SLOT in recall_start.BY_COMMS


def test_a_taker_reads_a_slot_rounds_end_in_the_forks_session():
    slot = recall_route.for_recall(a_recall("slot", fork=FORK))
    assert recall_take._turns_session(slot, SESSION) == FORK
    sub = recall_route.for_recall(a_recall("subagent"))
    assert recall_take._turns_session(sub, SESSION) == SESSION


# -- the slot fork's exit ----------------------------------------------------------------

class Refusal(Exception):
    code = "not-live"

    def __init__(self, problem):
        super().__init__(problem)
        self.problem = problem


def test_exit_ends_the_fork_through_the_launcher_and_says_how():
    launcher = Launcher()
    assert recall_route.Slot(SESSION, "r-1", fork=FORK, launcher=launcher).exit() == "exited"
    assert launcher.exited == [FORK]
    assert recall_route.Slot(SESSION, "r-1", launcher=launcher).exit() is None
    refused = Launcher(exits=(Refusal(f"session {FORK} is not live"),))
    said = recall_route.Slot(SESSION, "r-1", fork=FORK, launcher=refused).exit()
    assert said == f"refused (not-live): session {FORK} is not live"


def test_an_exit_the_launcher_holds_is_asked_again_for_a_bounded_time():
    launcher = Launcher(exits=("held", "held", "exited"))
    sleeps = []
    route = recall_route.Slot(SESSION, "r-1", fork=FORK, launcher=launcher)
    assert route.exit(sleep=sleeps.append) == "exited"
    assert sleeps == [recall_route.EXIT_RETRY_S] * 2
    held = Launcher(exits=("held",))
    clock = iter(range(0, 1000, recall_route.EXIT_RETRY_S))
    route = recall_route.Slot(SESSION, "r-1", fork=FORK, launcher=held)
    assert route.exit(sleep=lambda s: None, clock=lambda: next(clock)) == "held: turn"
    assert len(held.exited) == recall_route.EXIT_HELD_S // recall_route.EXIT_RETRY_S
    once = Launcher(exits=("held",))
    route = recall_route.Slot(SESSION, "r-1", fork=FORK, launcher=once)
    assert route.exit(held_s=0) == "held: turn" and once.exited == [FORK]


def test_end_fork_notes_the_exit_on_the_line_and_skips_a_route_with_no_fork_to_end():
    recall = a_recall("slot", fork=FORK)
    recall_child.open_line(recall)
    launcher = Launcher()
    route = recall_route.Slot(SESSION, recall.recall_id, fork=FORK, launcher=launcher)
    assert recall_route.end_fork(recall, route) == "exited"
    assert recalls.find_line(SESSION, recall.recall_id)["fork_exit"] == "exited"
    assert recall_route.end_fork(recall, recall_route.Subagent(SESSION)) is None


@pytest.mark.parametrize(("ended", "exits"), [
    ("ended", True), ("lost", True), ("discarded", True),
    (recall_stream.UNSETTLED, False)])
def test_the_child_ends_its_slot_fork_after_the_stream_unless_a_round_is_still_open(
        monkeypatch, ended, exits):
    recall = a_recall("slot", fork=FORK)
    launcher = Launcher()
    route = recall_route.Slot(SESSION, recall.recall_id, fork=FORK, launcher=launcher)
    monkeypatch.setattr(recall_child, "read_config", lambda: None)
    monkeypatch.setattr(recall_child, "write_brief", lambda recall: "the brief")
    monkeypatch.setattr(recall_child, "take_others", lambda *a, **kw: None)
    monkeypatch.setattr(recall_stream, "run", lambda recall, route, brief: ended)
    assert recall_child.run(SESSION, recall.recall_id, route=route) == 0
    assert launcher.exited == ([FORK] if exits else [])
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line.get("fork_exit") == ("exited" if exits else None)


def a_dead_line(**fields):
    recall = a_recall("slot", fork=FORK)
    recall_child.open_line(recall)
    recall.put(child=DEAD, fork=FORK, **fields)
    return recall


def test_a_taker_that_sends_a_slot_lines_text_ends_the_fork():
    recall = a_dead_line(state=recalls.SENDING, kept=False,
                         rounds=[{"round": 1, "text": "- a"}])
    launcher = Launcher()
    route = recall_route.Slot(SESSION, recall.recall_id, fork=FORK, launcher=launcher)
    sent = recall_send_taken()
    assert recall_take.take(SESSION, recall.recall_id, identity=DEAD, route=route,
                            send=sent, sleep=lambda s: None) == "settled"
    assert launcher.exited == [FORK]
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "back" and line["fork_exit"] == "exited"


def recall_send_taken():
    from moxi_memory import recall_send

    return lambda session_id, text: recall_send.Sent(taken=True, message_id="m-1")


def test_a_taker_leaves_the_fork_of_a_round_turns_has_no_end_for():
    launcher = Launcher()

    def no_end(ref):
        raise TimeoutError

    opened = a_dead_line(state=recalls.OPEN, kept=True)
    opened.put(ref=f"{opened.recall_id}:1")
    route = recall_route.Slot(SESSION, opened.recall_id, fork=FORK, launcher=launcher)
    assert recall_take.take(SESSION, opened.recall_id, identity=DEAD, route=route,
                            read_end=no_end) == "left"
    at_limit = a_dead_line(state=recalls.LOST, reason="gone", past_bound=True, kept=True)
    at_limit.put(ref=f"{at_limit.recall_id}:1", record={"state": records.LOST})
    route = recall_route.Slot(SESSION, at_limit.recall_id, fork=FORK, launcher=launcher)
    assert recall_take.take(SESSION, at_limit.recall_id, identity=DEAD, route=route,
                            read_end=no_end) == "left"
    assert launcher.exited == []


def test_a_taker_settling_a_later_round_past_its_bound_leaves_the_record_ended_and_ends_the_fork():
    recall = a_dead_line(state=recalls.BACK, past_bound=True, kept=True,
                         rounds=[{"round": 1, "text": "- a"},
                                 {"round": 2, "lost": "gone", "error": "bound"}])
    rid = recall.recall_id
    recall.put(ref=f"{rid}:2", record={"state": records.ENDED, "answered": True})
    launcher = Launcher()
    route = recall_route.Slot(SESSION, rid, fork=FORK, launcher=launcher)
    acked = []
    route.ack = acked.append
    late = moxi_turns.Ended(turn_id=None, outcome="completed", answer="- b")
    assert recall_take.take(SESSION, rid, identity=DEAD, route=route,
                            read_end=lambda ref: late, send=recall_send_taken(),
                            sleep=lambda s: None) == "settled"
    line = recalls.find_line(SESSION, rid)
    assert acked == [f"{rid}:2"] and line["kept"] is False
    assert line["state"] == "back" and line["rounds"][-1]["late"] is True
    assert records.read(SESSION, rid)["state"] == "ended"
    assert launcher.exited == [FORK]


def test_a_discard_ends_the_slot_fork_of_a_line_whose_owner_is_gone():
    gone = a_dead_line(state=recalls.SENDING, kept=False)
    launcher = Launcher(exits=("held",))
    line = recall_discard.discard(gone.recall_id, launcher=launcher)
    assert line["state"] == "discarded" and line["fork_exit"] == "held: turn"
    assert launcher.exited == [FORK]
    alive = a_recall("slot", fork=FORK)
    recall_child.open_line(alive)
    alive.put(fork=FORK, state=recalls.SENDING)
    launcher = Launcher()
    line = recall_discard.discard(alive.recall_id, launcher=launcher)
    assert line["state"] == "discarded" and "fork_exit" not in line
    assert launcher.exited == []


def test_the_doctor_lists_a_line_left_at_the_late_waits_last_limit_with_its_age(
        monkeypatch):
    monkeypatch.setattr(doctor, "live_subconscious", lambda: [])
    recall = a_dead_line(state=recalls.LOST, reason="gone", past_bound=True, kept=True)
    recall.put(ref=f"{recall.recall_id}:1")
    row = doctor._subconscious(config_in_error=False, now=time.time() + 600)
    assert row.state == "warn"
    assert ("1 recall line left at the late wait's last limit, its fork's round still "
            f"open: {recall.recall_id} (lost, fork {FORK}, 10m old)") in row.detail
    assert "not yet taken" not in row.detail


def test_the_doctor_lists_a_slot_fork_whose_exit_is_still_held_and_never_ends_it(
        monkeypatch):
    monkeypatch.setattr(doctor, "live_subconscious", lambda: [])
    launcher = Launcher(exits=("held",))
    recall = a_dead_line(state=recalls.BACK, kept=False)
    route = recall_route.Slot(SESSION, recall.recall_id, fork=FORK, launcher=launcher)
    assert recall_route.end_fork(recall, route, held_s=0) == "held: turn"
    asked = []

    def slots_running(slots, why=None):
        def read():
            asked.append(1)
            return slots, why
        return read

    later = time.time() + 600
    monkeypatch.setattr(doctor, "running_slots", slots_running({FORK: "3"}))
    row = doctor._subconscious(config_in_error=False, now=later)
    assert row.state == "warn"
    assert ("1 slot fork whose exit was still held after the exit's wait, holding its "
            f"slot: {recall.recall_id} (session {FORK}, slot 3, held: turn, 10m old); "
            "memory leaves it running, and moxi launcher exit-session SESSION_ID ends it "
            "once it is quiet") in row.detail
    monkeypatch.setattr(doctor, "running_slots", slots_running({}))
    assert "slot fork" not in doctor._subconscious(config_in_error=False, now=later).detail
    monkeypatch.setattr(doctor, "running_slots",
                        slots_running(None, "launcher is not installed"))
    row = doctor._subconscious(config_in_error=False, now=later)
    assert ("1 slot fork whose exit was still held after the exit's wait, holding its "
            "slot (the launcher can't say which are still running: launcher is not "
            f"installed): {recall.recall_id} (session {FORK}, held: turn, 10m old)"
            ) in row.detail
    assert len(asked) == 3 and launcher.exited == [FORK]


def test_running_slots_reads_the_launchers_running_launches_by_session(monkeypatch):
    from moxi_memory import links

    launches = (types.SimpleNamespace(session_id=FORK, slot="2"),
                types.SimpleNamespace(session_id=None, slot="4"))
    api = types.SimpleNamespace(running=lambda *, live_only: launches if live_only else ())
    monkeypatch.setattr(links, "launcher_forks", lambda: links.Link(links.READY, api=api))
    assert doctor.running_slots() == ({FORK: "2"}, None)

    def busy(*, live_only):
        raise RuntimeError("held")

    monkeypatch.setattr(links, "launcher_forks", lambda: links.Link(
        links.READY, api=types.SimpleNamespace(running=busy)))
    assert doctor.running_slots() == (None, "the launcher's launch log can't be read (held)")
    monkeypatch.setattr(links, "launcher_forks", lambda: links.Link(
        links.NONE, reason="launcher is not installed"))
    assert doctor.running_slots() == (None, "launcher is not installed")
