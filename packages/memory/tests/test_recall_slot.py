"""The slot route: a recall's fork launched into a slot through the launcher's interface
3, stamped with the recall's id, its rounds delivered to the fork's own session and
acked there; the route a record names, and where a taker reads a round's end."""

from __future__ import annotations

import types

import moxi_turns
import pytest
from moxi_turns import Refused

from moxi_memory import recall_child, recall_route, recall_start, recall_take, records

SESSION = "0190b000-0000-7000-8000-000000000031"
FORK = "0190b000-0000-7000-8000-0000000000f1"


class Launcher:
    def __init__(self, session_id=FORK, error=None):
        self.session_id, self.error, self.forks = session_id, error, []

    def fork(self, session_id, **kw):
        self.forks.append((session_id, kw))
        if self.error is not None:
            raise self.error
        return types.SimpleNamespace(session_id=self.session_id)


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
