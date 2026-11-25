"""Codex's host fork, memory's side, against a stand-in for turns' Codex `fork`: the
rounds asked of the Codex session as `fork` and `fork.continue` with turns' largest
bound, the child waiting each round's bound itself; the answers sent by comms, a late
one too; the route a record names and where a taker reads a round's end; no fork of its
own to exit; and a Codex session's recall still off unless its launch picks the slot
route, a turns that offers `fork` included."""

from __future__ import annotations

import os
import types

import moxi_turns
import pytest
from moxi_turns import Ended, Started

from moxi_memory import (recall_child, recall_discard, recall_route, recall_send,
                         recall_start, recall_stream, recall_take, recalls, records)

SESSION = "0190b000-0000-7000-8000-000000000041"
THREAD = "0190b000-0000-7000-8000-0000000000c1"
DEAD = {"pid": os.getpid(), "started": 1.0}


class Delivery:
    """A stand-in for a turns delivery of a fork round: its start names the forked
    thread; its end is the next of `ends` (an exception is raised)."""

    def __init__(self, number, ref, ends):
        self.id, self.ref, self.ends, self.waits = f"d{number}", ref, ends, []

    def wait_started(self, timeout_s):
        return Started(turn_id=None, delivery=self.id, ref=self.ref, agent_id=THREAD)

    def wait_ended(self, timeout_s):
        self.waits.append(timeout_s)
        end = self.ends.pop(0)
        if isinstance(end, BaseException):
            raise end
        return end


class CodexSession:
    """A stand-in for turns' Codex session once its host offers `fork`."""

    capabilities = frozenset({"fork", "keep"})

    def __init__(self, ends):
        self.ends, self.asked, self.deliveries, self.closed = list(ends), [], [], False

    def _round(self, op, text, kw):
        self.asked.append((op, text, kw))
        delivery = Delivery(len(self.asked), kw.get("ref"), self.ends)
        self.deliveries.append(delivery)
        return delivery

    def fork(self, text, **kw):
        return self._round("fork", text, kw)

    def fork_continue(self, delivery, text, **kw):
        return self._round(f"fork.continue {delivery}", text, kw)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def codex(monkeypatch):
    found = {}

    def find(*, session_id):
        found.setdefault("ids", []).append(session_id)
        return found.get("session")

    monkeypatch.setattr(moxi_turns, "find", find)
    return found


def ended(answer):
    return Ended(turn_id=None, outcome="completed", answer=answer)


def taken(sent):
    def send(session_id, text, *, comms_name=None):
        sent.append((session_id, text))
        return recall_send.Sent(taken=True, message_id=f"m-{len(sent)}")

    return send


def a_recall(settings=None, **fields):
    record = recall_start.begin(SESSION, "codex", start="prompt", text="ingest lock",
                                senders=(), settings=dict(recall_start.default_settings(),
                                                          **(settings or {})),
                                cwd=None, notes_dir=None,
                                route_name=recall_start.HOST_FORK, spawn=lambda s, r: None)
    recall = recall_child.Recall(records.read(SESSION, record["recall"]))
    recall_child.open_line(recall)
    if fields:
        recall.put(**fields)
    return recall


def test_the_host_forks_bound_is_turns_largest_in_whole_seconds():
    # turns exports no maximum; its own tests pin the value this copies.
    assert recall_route.HOST_FORK_TURNS_BOUND_S == (2**31 - 1) // 1000


def test_rounds_are_turns_forks_of_the_codex_session_with_turns_largest_bound(codex):
    codex["session"] = session = CodexSession([ended("- a")])
    route = recall_route.HostFork(SESSION)
    handle = route.first("the brief", ref="r-1:1", round_timeout_s=90.0)
    route.next(handle, "go on", ref="r-1:2", round_timeout_s=90.0)
    big = float(recall_route.HOST_FORK_TURNS_BOUND_S)
    assert session.asked == [
        ("fork", "the brief", {"by": "memory", "ref": "r-1:1", "keep": True,
                               "round_timeout_s": big}),
        ("fork.continue d1", "go on", {"ref": "r-1:2", "keep": True,
                                       "round_timeout_s": big})]
    assert big * 1000 <= 2**31 - 1
    assert route.started(handle) == THREAD and route.turns_session == SESSION
    assert codex["ids"] == [SESSION]
    route.close()
    assert session.closed


def test_a_codex_session_with_no_live_endpoint_is_a_lost_round(codex):
    route = recall_route.HostFork(SESSION)
    end = route.ended(route.first("b", ref="r-1:1", round_timeout_s=9.0), 9.0)
    assert type(end).__name__ == "Lost" and "no live turns endpoint" in str(end.reason)


def test_a_record_on_the_host_fork_gets_it_and_it_answers_by_comms():
    recall = a_recall()
    route = recall_route.for_recall(recall)
    assert type(route) is recall_route.HostFork and route.name == "host-fork"
    assert recall_stream.by_comms(route) and recall_stream.by_comms("host-fork")
    assert recall_take._turns_session(route, SESSION) == SESSION
    assert not hasattr(route, "exit")
    assert recall_route.end_fork(recall, route) is None
    assert "fork_exit" not in recalls.find_line(SESSION, recall.recall_id)


def test_the_child_waits_the_bound_itself_and_sends_each_answer_by_comms(codex):
    recall = a_recall({"max_rounds": 2})
    codex["session"] = session = CodexSession([ended("- a"), ended("- b\nEnd of recall.")])
    sent = []
    route = recall_route.HostFork(SESSION)
    assert recall_stream.run(recall, route, "the brief", send=taken(sent),
                             sleep=lambda s: None) == "ended"
    bound = recall_stream.round_bound_s(recall.settings)
    assert [d.waits for d in session.deliveries] == [[bound], [bound]]
    assert sent == [(SESSION, "- a"), (SESSION, "- b\nEnd of recall.")]
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "back" and line["kept"] is False and line["fork"] == THREAD
    assert line["route"] == "host-fork"
    record = records.read(SESSION, recall.recall_id)
    assert record["state"] == "ended" and record["fork"] == THREAD


def test_nothing_from_the_host_fork_sends_nothing(codex):
    recall = a_recall()
    codex["session"] = CodexSession([ended("NOTHING")])
    sent = []
    recall_stream.run(recall, recall_route.HostFork(SESSION), "the brief",
                      send=taken(sent), sleep=lambda s: None)
    assert sent == []
    assert records.read(SESSION, recall.recall_id)["state"] == "ended"


def test_a_round_past_its_bound_still_sends_its_late_answer_record_left_lost(codex):
    recall = a_recall({"max_rounds": 1})
    codex["session"] = session = CodexSession([TimeoutError(), ended("- late")])
    sent = []
    assert recall_stream.run(recall, recall_route.HostFork(SESSION), "the brief",
                             send=taken(sent), sleep=lambda s: None) == "lost"
    bound = recall_stream.round_bound_s(recall.settings)
    assert session.deliveries[0].waits == [bound, recall_stream.LATE_WAIT_S]
    assert sent == [(SESSION, "- late")]
    line = recalls.find_line(SESSION, recall.recall_id)
    assert line["state"] == "back" and line["rounds"][-1]["late"] is True
    assert records.read(SESSION, recall.recall_id)["state"] == "lost"


def test_a_taker_reads_a_host_fork_rounds_end_in_the_session_and_sends_it():
    recall = a_recall()
    recall.put(child=DEAD, state=recalls.OPEN, kept=True, ref=f"{recall.recall_id}:1")
    read, sent = [], []
    acked = []
    route = recall_route.HostFork(SESSION)
    route.ack = acked.append

    def read_end(ref):
        read.append(ref)
        return ended("- a")

    assert recall_take.take(SESSION, recall.recall_id, identity=DEAD, route=route,
                            read_end=read_end, read_start=lambda ref: None,
                            send=taken(sent), sleep=lambda s: None) == "settled"
    assert read == [f"{recall.recall_id}:1"] and acked == [f"{recall.recall_id}:1"]
    assert sent == [(SESSION, "- a")]
    assert recalls.find_line(SESSION, recall.recall_id)["state"] == "back"


def test_a_discard_of_a_host_fork_line_ends_no_fork():
    recall = a_recall()
    recall.put(child=DEAD, fork=THREAD, state=recalls.SENDING, kept=False)

    class Launcher:
        def exit_session(self, session_id):  # pragma: no cover - must not be called
            raise AssertionError("a host fork has no exit")

    line = recall_discard.discard(recall.recall_id, launcher=Launcher())
    assert line["state"] == "discarded" and "fork_exit" not in line


def test_a_codex_session_stays_off_on_the_default_route_where_turns_offers_fork(
        monkeypatch):
    monkeypatch.setattr(recall_start, "offers_fork", lambda session_id: True)
    monkeypatch.setattr(moxi_turns, "find",
                        lambda *, session_id: CodexSession([]))
    assert recall_start.route("codex", SESSION) == (None, recall_start.CODEX)
    stamped = {recall_start.SUBCONSCIOUS_ENV: "automatic",
               recall_start.SUBCONSCIOUS_ROUTE_ENV: "subagent"}
    assert recall_start.route("codex", SESSION, stamped) == (None, recall_start.CODEX)
    assert recall_start.HOST_FORK not in recall_start.ROUTES
