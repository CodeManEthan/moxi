"""Session commands on the endpoint core, on the fake harness: compact beside clear and
exit, and stopping background shells with a command (`stop_shells`)."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

import moxi_turns as turns
from fake_harness import FakeHarness
from moxi_turns import endpoint
from moxi_turns.results import LeftShell, Shells

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no pipe server yet")

UNVOUCHED = Shells((), (LeftShell(None, "unlisted"),), False)


def granted(make_endpoint, fake=None, names="clear,exit,compact"):
    ep, fake = make_endpoint(fake or FakeHarness(), env=dict(os.environ,
                                                              MOXI_TURNS_COMMANDS=names))
    return ep, fake, turns.find(session_id="s-1")


def in_thread(fn):
    out: list = []
    t = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    t.start()
    return t, out


def until(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)
    return cond()


def test_compact_needs_its_own_consent(make_endpoint):
    ep, fake, s = granted(make_endpoint, names="clear")
    assert s.command("compact") == turns.Refused("not-allowed")
    assert fake.commands == []


def test_compact_settles_ran_at_its_ready_and_keeps_the_session(make_endpoint):
    ep, fake, s = granted(make_endpoint)
    fake.compact_ready = False
    t, out = in_thread(lambda: s.command("compact", timeout=3))
    assert until(lambda: fake.commands)
    time.sleep(0.2)
    assert out == []                           # the call's return alone settles nothing
    ep.ready("compact", "s-1")
    t.join(3)
    assert out == [turns.Ran(None)]
    assert ep.ledger.session_id == "s-1"


def test_compact_refusals_slot_and_rate(make_endpoint, monkeypatch):
    ep, fake, s = granted(make_endpoint)
    fake.start_turn()
    assert s.command("compact") == turns.Refused("busy-turn")
    fake.end_turn()
    fake.agents = 1
    assert s.command("compact") == turns.Refused("busy-agents")
    fake.agents, fake.draft = 0, True
    assert s.command("compact") == turns.Refused("draft")
    fake.draft = False
    fake.auto_commands = False
    t, out = in_thread(lambda: s.command("compact", timeout=3))
    assert until(lambda: fake.commands)
    assert s.command("clear") == turns.Refused("in-flight")
    fake.run_command(fake.commands[0])
    t.join(3)
    assert out == [turns.Ran(None)]
    assert s.command("compact") == turns.Refused("rate")


def test_a_compact_with_no_ready_expires_failed(make_endpoint, monkeypatch):
    monkeypatch.setattr(endpoint, "PENDING_TTL_S", 0.3)
    ep, fake, s = granted(make_endpoint)
    fake.compact_ready = False
    r = s.command("compact", timeout=3)
    assert isinstance(r, turns.Failed) and r.reason.startswith("expired")


def test_a_ready_before_the_hand_off_settles_nothing(make_endpoint):
    ep, fake, s = granted(make_endpoint)
    fake.auto_commands = False
    fake.compact_ready = False
    ep._worker.put(lambda: time.sleep(0.4))     # hold the worker before the hand-off
    t, out = in_thread(lambda: s.command("compact", timeout=3))
    time.sleep(0.1)
    ep.ready("compact", "s-1")                  # an automatic compaction, not this one
    assert until(lambda: fake.commands)
    assert out == []
    ep.ready("compact", "s-1")
    t.join(3)
    assert out == [turns.Ran(None)]


@pytest.mark.parametrize("name", ["clear", "compact"])
def test_stop_shells_runs_once_and_ran_carries_the_report(make_endpoint, name):
    ep, fake, s = granted(make_endpoint)
    fake.agents = fake.shells = 2              # only shells live: they leave the check
    assert s.command(name, stop_shells=False) == turns.Refused("busy-agents")
    r = s.command(name, stop_shells=True)
    assert isinstance(r, turns.Ran) and r.shells == Shells(("sh1",), (), True)
    assert (r.new_session_id is not None) == (name == "clear")
    assert [c.stop_shells for c in fake.commands] == [True]
    assert s.command(name, stop_shells=True) == turns.Refused("rate")   # one count


def test_a_subagent_still_holds_a_command_with_stop_shells(make_endpoint):
    ep, fake, s = granted(make_endpoint)
    fake.agents, fake.shells = 2, 1
    assert s.command("clear", stop_shells=True) == turns.Refused("busy-agents")
    assert fake.stops == []


def test_nothing_is_stopped_when_the_command_is_refused(make_endpoint):
    ep, fake, s = granted(make_endpoint)
    fake.auto_commands = False
    t, out = in_thread(lambda: s.command("compact", stop_shells=True, timeout=3))
    assert until(lambda: fake.commands)
    fake.start_turn()                          # a turn opens before it takes effect
    fake.run_command(fake.commands[0])
    t.join(3)
    assert out == [turns.Refused("busy-turn")] and fake.stops == []


def test_a_compaction_with_stop_shells_settles_at_the_later_of_ready_and_the_report(
        make_endpoint):
    ep, fake, s = granted(make_endpoint)
    fake.compact_ready = False
    t, out = in_thread(lambda: s.command("compact", stop_shells=True, timeout=3))
    assert until(lambda: fake.stops)           # the report came first
    time.sleep(0.2)
    assert out == []
    ep.ready("compact", "s-1")
    t.join(3)
    assert out == [turns.Ran(None, Shells(("sh1",), (), True))]


def test_a_stop_that_never_reports_reads_ran_unvouched_not_expired(make_endpoint,
                                                                    monkeypatch):
    """The 300 s clock stops at the moment of effect; past the stop's own bound the
    command still ran, and the shells read as a list turns cannot vouch for."""
    monkeypatch.setattr(endpoint, "PENDING_TTL_S", 0.2)
    monkeypatch.setattr(endpoint, "SHELL_STOP_S", 0.3)
    monkeypatch.setattr(endpoint, "SHELL_REPORT_MARGIN_S", 0.2)
    ep, fake, s = granted(make_endpoint)
    fake.auto_commands = False
    t, out = in_thread(lambda: s.command("clear", stop_shells=True, timeout=5))
    assert until(lambda: fake.commands)
    request = fake.commands[0]
    assert ep.command_entering(request.id) is None
    ep.ready("clear", "s-2")
    ep.command_took_effect(request.id)
    time.sleep(0.3)                            # past PENDING_TTL_S, inside the stop bound
    assert out == []
    t.join(3)
    assert out == [turns.Ran("s-2", UNVOUCHED)]


def test_stop_shells_with_exit_and_without_the_capability(make_endpoint):
    ep, fake, s = granted(make_endpoint, FakeHarness(can=("deliver", "clear", "exit",
                                                          "compact")))
    with pytest.raises(ValueError):
        s.command("exit", stop_shells=True)
    with pytest.raises(turns.TurnsError) as err:
        s.command("clear", stop_shells=True)
    assert "stop-shells" in str(err.value)
    assert ep.command("clear", stop_shells=True) == turns.Refused("unsupported")
    assert fake.commands == []
