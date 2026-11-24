"""The directed join (`waits.directed`): each fact it reports, with the agents found
before it stopped; a `Reads` made for one launch, its deadline and comms' offline stop;
and the joiner's answer to each fact, as the row read it before the join was its own."""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import pytest
from test_keepwarm import agent
from test_page_joiner import Skewed
from test_page_waits import (CAPTAIN, Comms, Turns, agents_case, directed, fresh, joined,
                             live, one_pass, seeded, wired)

from moxi_launcher import joiner, waits

__all__ = ["fresh", "wired"]

class Clock:
    """A monotonic clock a test sets."""

    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def logged_anew(monkeypatch):
    monkeypatch.setattr(waits, "_logged", set())


def world(*workers, owner=CAPTAIN):
    """A director `boss` with the agents it directs, each `(name, session)`, and one bound
    agent it does not direct; its comms and turns."""
    sessions, bound, ids = {}, [], {}
    for index, (name, session) in enumerate(workers):
        wsid = f"{index:08x}-0000-4000-8000-{index:012x}"
        session.session_id = wsid
        sessions[wsid] = session
        found, _ = directed(name, wsid)
        bound.append(found)
        ids[name] = wsid
    bound.append(SimpleNamespace(qualified="scout@proj-b", name="scout",
                                 directors=("someone@proj-b",)))
    return Comms({"boss": owner}, bound, ids), Turns(sessions)


def reads_for(comms, turns, *, clock=None, budget_s=20.0):
    clock = clock or Clock()
    return waits.Reads(deadline=clock() + budget_s, clock=clock, comms=comms, turns=turns)


def failing_on(comms, name, at, err):
    """`comms`' read `name` raising `err` from its `at`th call on."""
    real = getattr(comms, name)
    count = [0]

    def read(*args, **kwargs):
        count[0] += 1
        if count[0] >= at:
            comms.calls.append((name, kwargs.get("timeout_s")))
            raise err
        return real(*args, **kwargs)

    setattr(comms, name, read)


# -- the facts ---------------------------------------------------------------------------------


def test_the_agents_at_work_are_named_in_roster_order_and_the_read_is_sure():
    comms, turns = world(("coder@proj-a", live("x", turn_open=True)),
                         ("critic@proj-a", live("x")),
                         ("pilot@proj-a", live("x", agents=2)))
    found = waits.directed(reads_for(comms, turns), "boss")
    assert found == waits.Directed(("coder@proj-a", "pilot@proj-a"))
    assert found.sure


def test_without_comms_or_an_owner_nobody_is_directed_and_the_read_is_sure():
    assert waits.directed(reads_for(None, Turns({})), "boss") == waits.Directed()
    comms, turns = world(("coder@proj-a", live("x", turn_open=True)), owner=None)
    found = waits.directed(reads_for(comms, turns), "boss")
    assert found == waits.Directed() and found.sure
    assert [name for name, _ in comms.calls] == ["owner"]


def test_a_comms_that_does_not_load_is_a_read_that_raised():
    found = waits.directed(reads_for(waits.UNLOADED, Turns({})), "boss")
    assert found == waits.Directed(raised=True) and not found.sure


@pytest.mark.parametrize("kind", ["DaemonOffline", "DaemonError"])
def test_comms_raising_is_raised_with_the_names_found_before_it_and_not_logged(kind,
                                                                               caplog):
    comms, turns = world(("coder@proj-a", live("x", turn_open=True)),
                         ("critic@proj-a", live("x", turn_open=True)))
    failing_on(comms, "session_of", 2, getattr(comms, kind)("comms failed"))
    with caplog.at_level(logging.ERROR, logger="moxi.launcher.waits"):
        found = waits.directed(reads_for(comms, turns), "boss")
    assert found == waits.Directed(("coder@proj-a",), raised=True)
    assert not found.sure
    assert caplog.records == []


def test_a_failed_turns_read_is_raised_with_the_names_found_before_it_and_logged_once(
        caplog):
    comms, turns = world(("coder@proj-a", live("x", turn_open=True)),
                         ("critic@proj-a", live("x", turn_open=True)))
    turns.sessions[comms.sessions["critic@proj-a"]] = RuntimeError("the endpoint went away")
    with caplog.at_level(logging.ERROR, logger="moxi.launcher.waits"):
        first = waits.directed(reads_for(comms, turns), "boss")
        again = waits.directed(reads_for(comms, turns), "boss")
    assert first == again == waits.Directed(("coder@proj-a",), raised=True)
    assert [r.exc_info[0] for r in caplog.records] == [RuntimeError]


def test_a_call_at_the_deadline_is_late_with_the_names_found_before_it():
    clock = Clock()
    comms, turns = world(("coder@proj-a", live("x", turn_open=True)),
                         ("critic@proj-a", live("x", turn_open=True)))
    reads = reads_for(comms, turns, clock=clock, budget_s=5.0)
    coder = turns.sessions[comms.sessions["coder@proj-a"]]
    real_facts = coder.facts

    def facts():
        clock.now = reads.deadline
        return real_facts()

    coder.facts = facts
    found = waits.directed(reads, "boss")
    assert found == waits.Directed(("coder@proj-a",), late=True)
    assert not found.sure
    # The next agent's `session_of` was the call that met the deadline, and it was not made.
    assert [name for name, _ in comms.calls] == ["owner", "roster", "session_of"]
    assert coder.closed == 1


def test_a_closed_claude_turn_whose_subagents_turns_can_t_list_is_unlisted():
    comms, turns = world(("coder@proj-a", live("x", turn_open=True)),
                         ("critic@proj-a", live("x", agents=None)))
    found = waits.directed(reads_for(comms, turns), "boss")
    assert found == waits.Directed(("coder@proj-a",), unlisted=True)
    assert not found.sure


def test_a_codex_session_never_makes_the_read_unlisted():
    comms, turns = world(("coder@proj-a", live("x", agents=None, harness="codex")))
    found = waits.directed(reads_for(comms, turns), "boss")
    assert found == waits.Directed() and found.sure


def test_an_open_turn_with_no_subagent_list_is_at_work_and_not_unlisted():
    comms, turns = world(("coder@proj-a", live("x", turn_open=True, agents=None)))
    assert waits.directed(reads_for(comms, turns), "boss") == waits.Directed(
        ("coder@proj-a",))


def test_live_subagents_counts_busy_subagents_and_teammates_but_no_fork_or_shell():
    fork = SimpleNamespace(id="f1", kind="subagent", state="running", final=False,
                           is_fork=True)
    listed = SimpleNamespace(complete=True, entries=[
        agent("a1"), agent("a2", state="waiting"), agent("a3", state="pending"),
        agent("t1", kind="teammate"), agent("a4", state="idle"),
        agent("a5", state="done", final=True), agent("a6", final=True),
        agent("s1", kind="shell"), fork])
    assert [a.id for a in waits.live_subagents(listed)] == ["a1", "a2", "a3", "t1"]
    assert waits.live_subagents(None) == ()


def test_a_closed_turn_counts_by_its_listed_subagents_not_turns_busy_count():
    # turns' busy count says 1 (a fork), the list holds only the fork: not at work.
    fork = SimpleNamespace(id="f1", kind="subagent", state="running", final=False,
                           is_fork=True)
    comms, turns = world(("coder@proj-a", live("x", entries=[fork], agents=1)),
                         ("critic@proj-a", live("x", entries=[agent("a1", state="waiting")],
                                                agents=0)))
    found = waits.directed(reads_for(comms, turns), "boss")
    assert found == waits.Directed(("critic@proj-a",)) and found.sure


def test_a_directed_agent_whose_only_subagent_turns_judged_stale_is_not_at_work():
    # An endpoint that judges stale entries lists a stale `waiting` subagent `idle`.
    comms, turns = world(("coder@proj-a", live("x", entries=[agent("a1", state="idle")])))
    found = waits.directed(reads_for(comms, turns), "boss")
    assert found == waits.Directed() and found.sure


def test_an_open_turn_reads_no_agent_list_and_a_codex_closed_turn_none_either():
    comms, turns = world(("coder@proj-a", live("x", turn_open=True)),
                         ("pilot@proj-a", live("x", entries=[agent("a1")], harness="codex")))
    for session in turns.sessions.values():
        session.agents = lambda: pytest.fail("the agent list was read")
    assert waits.directed(reads_for(comms, turns), "boss") == waits.Directed(
        ("coder@proj-a",))


def test_an_agent_list_that_raises_is_unlisted_and_not_logged(caplog):
    comms, turns = world(("coder@proj-a", live("x", turn_open=True)),
                         ("critic@proj-a", live("x")))
    turns.sessions[comms.sessions["critic@proj-a"]].entries = RuntimeError("gone")
    with caplog.at_level(logging.ERROR, logger="moxi.launcher.waits"):
        found = waits.directed(reads_for(comms, turns), "boss")
    assert found == waits.Directed(("coder@proj-a",), unlisted=True)
    assert caplog.records == []
    assert turns.sessions[comms.sessions["critic@proj-a"]].closed == 1


def test_the_agent_list_is_a_call_the_deadline_stops():
    clock = Clock()
    comms, turns = world(("coder@proj-a", live("x", entries=[agent("a1")])))
    reads = reads_for(comms, turns, clock=clock, budget_s=5.0)
    coder = turns.sessions[comms.sessions["coder@proj-a"]]
    real_facts = coder.facts

    def facts():
        clock.now = reads.deadline
        return real_facts()

    coder.facts = facts
    assert waits.directed(reads, "boss") == waits.Directed(late=True)
    assert coder.closed == 1


# -- one launch's Reads ------------------------------------------------------------------------


def test_a_reads_starts_no_call_at_or_past_its_deadline():
    clock = Clock()
    reads = waits.Reads(deadline=clock() + 20.0, clock=clock)
    made = []
    clock.now = reads.deadline - 0.001
    assert reads.call(made.append, "before") is None
    for now in (reads.deadline, reads.deadline + 1.0):
        clock.now = now
        with pytest.raises(waits.Late):
            reads.call(made.append, "after")
    assert made == ["before"]


def test_a_reads_deadline_is_on_the_monotonic_clock_by_default():
    reads = waits.Reads(deadline=time.monotonic() + 20.0)
    assert reads.call(lambda: "read") == "read"
    with pytest.raises(waits.Late):
        waits.Reads(deadline=time.monotonic() - 1.0).call(lambda: "read")


def test_the_first_daemon_offline_stops_every_later_comms_call_but_not_turns():
    comms = Comms(offline=True)
    reads = reads_for(comms, Turns({}))
    with pytest.raises(comms.DaemonOffline):
        reads.comms_call(comms.owner, "boss", timeout_s=waits.COMMS_TIMEOUT_S)
    assert reads.offline
    comms.offline = False
    with pytest.raises(comms.DaemonOffline):
        reads.comms_call(comms.roster, timeout_s=waits.COMMS_TIMEOUT_S)
    assert [name for name, _ in comms.calls] == ["owner"]
    assert reads.call(lambda: "turns") == "turns"


def test_one_reads_reads_one_roster_and_after_comms_is_down_asks_it_nothing():
    comms, turns = world(("coder@proj-a", live("x", turn_open=True)))
    comms.owners["other"] = CAPTAIN
    reads = reads_for(comms, turns)
    assert waits.directed(reads, "boss").names == ("coder@proj-a",)
    assert waits.directed(reads, "other").names == ("coder@proj-a",)
    assert [name for name, _ in comms.calls].count("roster") == 1
    comms.offline = True
    assert waits.directed(reads, "boss") == waits.Directed(raised=True)
    calls = len(comms.calls)
    assert waits.directed(reads, "boss") == waits.Directed(raised=True)
    assert len(comms.calls) == calls


# -- the joiner's answer -----------------------------------------------------------------------


def test_the_joiner_gives_no_waits_when_the_join_raised_after_finding_agents(wired):
    agents_case(wired, ("coder@proj-a", live("x", turn_open=True)),
                ("critic@proj-a", live("x", turn_open=True)))
    failing_on(wired.comms, "session_of", 2, wired.comms.DaemonError("comms failed"))
    got = joined()
    assert got["waits"] is None and got["covered"] is False


def test_the_joiner_counts_the_others_when_one_agent_is_unlisted(wired):
    agents_case(wired, ("coder@proj-a", live("x", turn_open=True)),
                ("critic@proj-a", live("x", agents=None)),
                ("pilot@proj-a", live("x", agents=1)))
    assert joined()["waits"] == {"kind": "agents", "count": 2}


def test_the_joiner_publishes_nothing_for_a_launch_whose_join_was_late(wired):
    # The pass's deadline passes during the first agent's facts, so the join stops at the
    # next agent's `session_of` with one agent found.
    _, ids = agents_case(wired, ("coder@proj-a", live("x", turn_open=True)),
                         ("critic@proj-a", live("x", turn_open=True)))
    clock = Skewed()
    coder = wired.turns.sessions[ids["coder@proj-a"]]
    real_facts = coder.facts

    def facts():
        clock.skew += joiner.JOIN_BUDGET_S + 1.0
        return real_facts()

    coder.facts = facts
    join = one_pass(joiner.Joiner(clock=clock))
    assert join.kept == {}
    assert [name for name, _ in wired.comms.calls] == ["owner", "roster", "session_of"]


def test_a_launch_with_subagents_makes_no_join(wired):
    sid = seeded()
    wired.turns = Turns({sid: live(sid, entries=[agent("a1")])})
    wired.comms = Comms({sid: CAPTAIN})
    assert joined()["waits"] == {"kind": "subagents", "count": 1}
    assert wired.comms.calls == []
