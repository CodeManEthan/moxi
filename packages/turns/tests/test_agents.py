"""Subagent liveness through the endpoint core, on the fake harness: the list as the
harness records it, the count the busy checks read from it, an unknown list counted busy,
and a final notice read inside an open turn."""

from __future__ import annotations

import os
import sys
import time

import pytest

import moxi_turns as turns
from fake_harness import FakeHarness
from moxi import ipc
from moxi_turns.results import Agent, Agents, from_wire

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="moxi.ipc's named pipe server comes later")


def consent():
    """The environment with consent for `clear`, read after the test's roots are set."""
    return dict(os.environ, MOXI_TURNS_COMMANDS="clear")


@pytest.fixture
def connect():
    clients = []

    def make(ep):
        client = ipc.connect(ep.address, timeout_s=5)
        clients.append(client)
        client.hello((2, 2))
        return client

    yield make
    for client in clients:
        client.close()


def entry(id, state="running", final=False, kind="subagent", **rest):
    return Agent(id, kind, state, final, **rest)


def test_agents_reads_the_harness_record_and_facts_count_it(make_endpoint):
    ep, fake = make_endpoint()
    fake.entries = [
        entry("a1", description="look around", parent_id=None),
        entry("a2", "waiting"),
        entry("a3", "pending", final=None),
        entry("a4", "idle"),                       # a teammate waiting: not busy
        entry("a5", "done", final=True),
        entry("a6", "running", final=True),        # its final notice came: not busy
        entry("a7", "unknown", final=None),
        entry("sh1", kind="shell"),
    ]
    s = turns.find(session_id="s-1")
    a = s.agents()
    assert isinstance(a, turns.Agents) and a.complete
    assert [e.id for e in a.entries] == ["a1", "a2", "a3", "a4", "a5", "a6", "a7", "sh1"]
    assert a.entries[0] == entry("a1", description="look around")
    assert s.facts().agents == 4                    # a1, a2, a3 and the shell


def test_the_agents_answer_carries_its_kind_on_the_wire(make_endpoint, connect):
    ep, fake = make_endpoint()
    fake.entries = [entry("a1", parent_id="p")]
    answer = connect(ep).call("agents", {}, timeout_s=5)
    assert answer["kind"] == "agents" and answer["complete"] is True
    assert answer["entries"] == [{"id": "a1", "kind": "subagent", "state": "running",
                                  "final": False, "description": None,
                                  "parent_id": "p"}]
    assert from_wire(answer) == Agents((entry("a1", parent_id="p"),), True,
                                                     answer["at"])


@pytest.mark.parametrize("how", ["incomplete", "raises", "not-a-list"])
def test_an_unknown_list_counts_busy_and_never_as_empty(make_endpoint, how):
    ep, fake = make_endpoint(env=consent())
    fake.entries = []
    if how == "incomplete":
        fake.agents_complete = False
    elif how == "raises":
        fake.agents_fail = True
    else:
        fake.agents = lambda: [entry("a1")]       # its live count fails to derive too
    s = turns.find(session_id="s-1")
    a = s.agents()
    assert a.complete is False and a.busy() is None
    assert s.facts().agents is None
    d = s.deliver("x", only_if_idle=True, refuse_agents=True)
    assert d.wait_started(1).reason == "busy-agents"
    assert s.command("clear") == turns.Refused("busy-agents")


def test_an_empty_list_read_whole_is_idle(make_endpoint):
    ep, fake = make_endpoint(env=consent())
    fake.entries = [entry("a1", "done", final=True)]
    s = turns.find(session_id="s-1")
    assert s.facts().agents == 0
    assert s.agents().busy() == 0
    assert s.command("clear") == turns.Ran("s-2")


def test_a_final_notice_inside_an_open_turn_reads_final_before_the_turn_ends(
        make_endpoint):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    turn = fake.start_turn()
    fake.entries = [entry("bg", "running", description="a background task")]
    assert s.agents().entries[0].final is False and s.facts().agents == 1
    # The harness records the subagent's final notice; the main-loop turn stays open.
    fake.entries = [entry("bg", "done", final=True, description="a background task")]
    f, a = s.facts(), s.agents()
    assert f.turn_open and f.turn is not None and f.turn.turn_id == turn
    assert a.entries[0].final is True and a.entries[0].state == "done"
    assert f.agents == 0


def test_a_slow_list_answers_within_the_live_bound_as_not_complete(make_endpoint):
    class Slow(FakeHarness):
        live_wait_s = 0.3

        def agents(self):
            time.sleep(3)
            return super().agents()

    ep, fake = make_endpoint(Slow())
    s = turns.find(session_id="s-1")
    t0 = time.monotonic()
    a = s.agents()
    assert a.complete is False and time.monotonic() - t0 < 1.5


def test_a_session_without_the_capability_refuses_agents(make_endpoint):
    can = ("deliver", "only-if-idle", "steer", "stop-handler", "clear", "exit")
    ep, fake = make_endpoint(FakeHarness(can=can))
    s = turns.find(session_id="s-1")
    assert "agents" not in s.capabilities
    with pytest.raises(turns.TurnsError):
        s.agents()
