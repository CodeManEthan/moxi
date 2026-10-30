"""The endpoint core and its turn ledger, driven by the fake harness, through the public
API over the socket."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time

import pytest

import moxi_turns as turns
from conftest import bare, write_config
from fake_harness import FakeHarness
from moxi_turns import protocol

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="moxi.ipc's named pipe server comes later")


def idle_start(s, **checks):
    """How a delivery given only if the session is idle starts."""
    return bare(s.deliver("x", only_if_idle=True, **checks).wait_started(1))


def test_find_by_session_id_and_by_pid(make_endpoint):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    assert s is not None and s.harness == "fake" and s.pid == os.getpid()
    assert s.capabilities == frozenset({"deliver", "only-if-idle", "steer", "stop-handler",
                                        "clear", "exit", "compact", "agents",
                                            "stop-shells"})
    assert s.endpoint_pid == os.getpid() and s.address == ep.address
    assert turns.find(pid=os.getpid()).dir == ep.dir
    assert turns.find(session_id="nobody") is None
    assert [x.dir for x in turns.sessions()] == [ep.dir]


def test_find_by_pid_walks_up_from_a_process_under_the_harness(make_endpoint):
    class Tree:
        def ancestry(self, pid):
            yield from (pid, 4242, os.getpid(), 1)

    ep, _ = make_endpoint()
    assert turns.find(pid=999999, proc=Tree()).dir == ep.dir


def test_directory_and_hello(make_endpoint):
    ep, _ = make_endpoint()
    assert stat.S_IMODE(ep.dir.stat().st_mode) == 0o700
    assert ep.dir.name == f"fake-{ep.nonce}" and ep.dir.parent.name == "v2"
    hello = json.loads(ep.hello_path.read_text())
    assert hello["session_id"] == "s-1" and hello["protocol"] == 2
    assert hello["address"] == ep.address
    assert {"harness", "pid", "endpoint_pid", "earlier", "cwd", "endpoint_version",
            "mod_version"} <= set(hello)


def test_a_clear_keeps_the_endpoint_and_hello_follows_the_id(make_endpoint):
    ep, _ = make_endpoint()
    ep.ready("clear", "s-2")
    assert json.loads(ep.hello_path.read_text())["session_id"] == "s-2"
    assert turns.find(session_id="s-1").session_id == "s-2"     # an earlier id still finds it
    assert turns.find(session_id="s-2").dir == ep.dir


def test_a_dead_directory_is_swept_and_a_starting_one_is_not(make_endpoint, turns_env):
    ep, _ = make_endpoint()
    dead = ep.dir.parent / "fake-dead"
    dead.mkdir()
    (dead / "hello.json").write_text(json.dumps({
        "address": protocol.address("fake-dead"), "protocol": 2, "harness": "fake",
        "pid": os.getpid(), "session_id": "s-1", "started": 9e12}))
    starting = ep.dir.parent / "fake-starting"
    starting.mkdir()
    assert turns.find(session_id="s-1").dir == ep.dir
    assert not dead.exists() and starting.exists()


def test_start_runs_and_ends_with_the_outcome(make_endpoint):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    d = s.deliver("hello", only_if_idle=True)
    assert bare(d.wait_started(2)) == turns.Started("t1")
    assert fake.submitted[0].text == "hello"
    with pytest.raises(TimeoutError):
        d.wait_ended(0.05)
    fake.end_turn("answer")
    assert bare(d.wait_ended(2)) == turns.Ended("t1", "answer")
    f = s.facts()
    assert (f.session_id, f.turn_number, f.turn_open, f.stale) == ("s-1", 1, False, False)


@pytest.mark.parametrize("outcome", ["aborted", "error", "refusal"])
def test_esc_and_errors_are_outcomes(make_endpoint, outcome):
    ep, fake = make_endpoint()
    d = turns.find(session_id="s-1").deliver("x", only_if_idle=True)
    d.wait_started(1)                       # the worker hands it over after the answer
    fake.end_turn(outcome)
    assert bare(d.wait_ended(2)) == turns.Ended("t1", outcome)


def test_start_refusals(make_endpoint):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    fake.start_turn()
    assert idle_start(s) == turns.Refused("busy-turn")
    fake.end_turn()
    assert idle_start(s, if_turn_number=0) == turns.Refused("stale-turn")
    fake.draft = True
    assert idle_start(s, refuse_draft=True) == turns.Refused("draft")
    fake.draft, fake.live_agents = False, 1
    assert idle_start(s, refuse_agents=True) == turns.Refused("busy-agents")
    fake.live_fails = True
    assert idle_start(s, refuse_draft=True) == turns.Refused("draft")
    fake.live_fails, fake.live_agents = False, 0
    d = s.deliver("x", only_if_idle=True, if_turn_number=1, refuse_draft=True, refuse_agents=True)
    assert bare(d.wait_started(1)) == turns.Started("t2")


def test_an_unreadable_subagent_list_is_unknown_and_counts_as_busy(make_endpoint):
    # An unknown subagent count never reads as 0.
    env = dict(os.environ, MOXI_TURNS_COMMANDS="clear")
    ep, fake = make_endpoint(env=env)
    s = turns.find(session_id="s-1")
    fake.live_agents = None
    assert s.facts().agents is None
    assert idle_start(s, refuse_agents=True) == turns.Refused("busy-agents")
    assert s.command("clear") == turns.Refused("busy-agents")
    fake.live_fails = True
    f = s.facts()
    assert f.agents is None and f.draft is True


def test_a_turn_typed_between_the_submit_and_the_entry_refuses_the_start(make_endpoint):
    ep, fake = make_endpoint(FakeHarness(auto=False))
    s = turns.find(session_id="s-1")
    d = s.deliver("x", only_if_idle=True, if_turn_number=0)
    fake.start_turn()                       # the person types first
    fake.end_turn()
    assert fake.enter(fake.handed(1)[0]) == "stale-turn"
    assert bare(d.wait_started(1)) == turns.Refused("stale-turn")
    assert bare(d.wait_ended(1)) == turns.Refused("stale-turn")


def test_a_start_whose_checks_fail_where_it_takes_effect_is_refused(make_endpoint):
    ep, fake = make_endpoint(FakeHarness(auto=False))
    d = turns.find(session_id="s-1").deliver("x", only_if_idle=True, refuse_draft=True)
    fake.draft = True
    assert fake.enter(fake.handed(1)[0]) == "draft"
    assert bare(d.wait_started(1)) == turns.Refused("draft")


def test_deliver_waits_for_the_running_turn_and_two_are_two_turns(make_endpoint):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    fake.start_turn()                        # the person's turn
    a, b = s.deliver("a"), s.deliver("b")
    with pytest.raises(TimeoutError):
        a.wait_started(0.05)
    assert fake.submitted == []
    fake.end_turn()
    assert bare(a.wait_started(1)) == turns.Started("t2")
    assert [x.text for x in fake.submitted] == ["a"]
    fake.end_turn()
    assert bare(a.wait_ended(1)) == turns.Ended("t2", "answer")
    assert bare(b.wait_started(1)) == turns.Started("t3")
    fake.end_turn("aborted")
    assert bare(b.wait_ended(1)) == turns.Ended("t3", "aborted")
    # A start is refused while a delivery is held.
    fake.start_turn()
    s.deliver("c")
    assert idle_start(s) == turns.Refused("busy-turn")


def test_steer(make_endpoint):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    assert bare(s.steer("x").wait_started(1)) == turns.Refused("idle")
    turn = fake.start_turn()
    d = s.steer("now")
    assert bare(d.wait_started(1)) == turns.Started(turn)
    assert [x.text for x in fake.appended] == ["now"]
    fake.end_turn()
    assert bare(d.wait_ended(1)) == turns.Ended(turn, "answer")


def test_what_the_session_lacks_is_an_error_and_gone_is_refused(make_endpoint):
    ep, fake = make_endpoint(FakeHarness(can=("deliver", "only-if-idle")))
    s = turns.find(session_id="s-1")
    fake.start_turn()
    for call in (lambda: s.steer("x"), lambda: s.command("clear")):
        with pytest.raises(turns.TurnsError) as caught:
            call()
        assert caught.value.code == 1002
    assert caught.value.data == {"capability": "clear"}
    with pytest.raises(ValueError):
        s.command("reboot")
    d = s.deliver("held")                        # held to the turn's end
    ep.stop("the session ended")
    assert isinstance(d.wait_ended(1), turns.Lost)      # the endpoint is gone with it
    assert idle_start(s) == turns.Refused("gone")
    assert s.steer("x").wait_started(1) == turns.Refused("gone")
    assert s.command("clear") == turns.Refused("gone")
    for read in (s.facts, s.agents, s.refresh):
        with pytest.raises(turns.TurnsError):
            read()
    assert turns.find(session_id="s-1") is None


def test_an_open_delivery_answers_lost_when_the_endpoint_goes(make_endpoint):
    ep, fake = make_endpoint()
    d = turns.find(session_id="s-1").deliver("x", only_if_idle=True)
    assert bare(d.wait_started(1)) == turns.Started("t1")
    waiter = []
    t = threading.Thread(target=lambda: waiter.append(d.wait_ended(None)))
    t.start()
    time.sleep(0.1)
    ep.gone("a reload")
    t.join(3)
    # The endpoint's reason when its answer is out before the server closes; else the
    # wait's reconnect finds nothing serving, which reads the same `lost`.
    assert len(waiter) == 1 and isinstance(waiter[0], turns.Lost)
    assert waiter[0].reason in ("a reload", "unanswered")


def test_watch_yields_every_turn_event_then_gone(make_endpoint):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    events = []
    started = threading.Event()

    w = s.watch()
    assert w.facts.session_id == "s-1" and w.facts.turn is None

    def read():
        for ev in w:
            events.append(ev)
            started.set()

    t = threading.Thread(target=read)
    t.start()
    ep.ready("startup", "s-1")
    fake.start_turn()
    fake.end_turn()
    d = s.deliver("x", only_if_idle=True)
    d.wait_started(1)
    fake.end_turn("error")
    ep.gone("bye")
    t.join(3)
    opened = (turns.DeliveryRef(d.id, None, "opened"),)
    assert events == [
        turns.Ready("startup", "s-1"),
        turns.TurnStarted("t1", "s-1", 1),
        turns.TurnEnded("t1", "s-1", 1, "answer"),
        turns.TurnStarted("t2", "s-1", 2, opened),
        turns.TurnEnded("t2", "s-1", 2, "error", opened),
        turns.Gone("bye"),
    ]


def test_the_turn_number_counts_this_session_and_a_missed_end_closes_as_error(
        make_endpoint):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    d = s.deliver("x", only_if_idle=True)
    d.wait_started(1)
    fake.start_turn()                    # a turn start with the last one still open
    assert bare(d.wait_ended(1)) == turns.Ended("t1", "error")
    fake.end_turn()
    assert s.facts().turn_number == 2
    ep.ready("clear", "s-2")
    assert (s.facts().session_id, s.facts().turn_number) == ("s-2", 0)


# -- the stop handler ------------------------------------------------------------------


def test_the_stop_handler_holds_a_stop_open_or_lets_it_end(make_endpoint):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    assert ep.on_stop(turns.StopFacts(turn_id="t1")) is None     # no handler
    seen = []
    h = s.handle_stops(
        lambda facts: seen.append(facts) or ("reply first" if len(seen) == 1 else None))
    facts = turns.StopFacts(turn_id="t1", session_id="s-1", stop_hook_active=False,
                            task_types=("shell",), crons=0)
    assert ep.on_stop(facts) == "reply first"
    assert ep.on_stop(facts) is None
    assert seen == [facts, facts]
    with pytest.raises(turns.TurnsError) as caught:
        s.handle_stops(lambda f: None)
    assert caught.value.code == 1003
    h.close()
    assert h.closed
    time.sleep(0.1)
    assert ep.on_stop(facts) is None
    s.handle_stops(lambda f: "again").close()


def test_a_late_or_broken_stop_handler_lets_the_turn_end(make_endpoint, turns_env):
    write_config(turns_env, "[stop_handler]\nwait_ms = 100\n")
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    h = s.handle_stops(lambda f: time.sleep(0.5) or "too late")
    t0 = time.monotonic()
    assert ep.on_stop(turns.StopFacts()) is None
    assert time.monotonic() - t0 < 0.4
    h.close()
    time.sleep(0.6)

    def broken(facts):
        raise RuntimeError("bug")

    h = s.handle_stops(broken)
    assert ep.on_stop(turns.StopFacts()) is None
    h.close()


# -- session commands ----------------------------------------------------------------


def test_a_command_needs_consent(make_endpoint, turns_env):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    assert s.command("clear") == turns.Refused("not-allowed")
    write_config(turns_env, '[commands]\nallow = ["exit"]\n')
    assert s.command("clear") == turns.Refused("not-allowed")
    assert s.command("exit") == turns.Ran(None)


def test_a_launch_stamp_grants_commands(make_endpoint):
    env = dict(os.environ, MOXI_TURNS_COMMANDS="clear,exit")
    ep, fake = make_endpoint(env=env)
    s = turns.find(session_id="s-1")
    r = s.command("clear", if_turn_number=0)
    assert r == turns.Ran("s-2")
    assert s.refresh()["session_id"] == "s-2"
    assert s.command("exit") == turns.Refused("rate")        # one accepted per 60 s


def test_a_command_runs_only_from_an_idle_snapshot(make_endpoint):
    env = dict(os.environ, MOXI_TURNS_COMMANDS="clear")
    ep, fake = make_endpoint(env=env)
    s = turns.find(session_id="s-1")
    fake.start_turn()
    assert s.command("clear") == turns.Refused("busy-turn")
    fake.end_turn()
    assert s.command("clear", if_turn_number=0) == turns.Refused("stale-turn")
    fake.live_agents = 1
    assert s.command("clear") == turns.Refused("busy-agents")
    fake.live_agents, fake.draft = 0, True
    assert s.command("clear") == turns.Refused("draft")
    fake.draft = False
    fake.auto_commands = False
    results = []
    t = threading.Thread(target=lambda: results.append(s.command("clear", timeout=3)))
    t.start()
    time.sleep(0.2)
    assert s.command("clear") == turns.Refused("in-flight")
    fake.start_turn()                          # a turn opens before the command takes effect
    fake.run_command(fake.commands[0])
    t.join(3)
    assert results == [turns.Refused("busy-turn")]


def test_a_delivery_the_harness_never_settles_expires_and_frees_the_slot(
        make_endpoint, monkeypatch):
    """A delivery handed to the adapter that is never tied nor refused answers
    `Lost` past the bound, and the endpoint takes the next one."""
    from moxi_turns import endpoint

    monkeypatch.setattr(endpoint, "PENDING_TTL_S", 0.2)
    ep, fake = make_endpoint(FakeHarness(auto=False))
    s = turns.find(session_id="s-1")
    a = s.deliver("a")                               # in flight, never entered
    b = s.deliver("b")                               # held behind it
    assert idle_start(s) == turns.Refused("busy-turn")
    assert [x.text for x in fake.handed(1)] == ["a"]
    lost = a.wait_started(2)
    assert isinstance(lost, turns.Lost) and lost.reason.startswith("expired")
    assert a.wait_ended(1) == lost
    assert [x.text for x in fake.handed(2)] == ["a", "b"]   # the held one went in
    # The adapter's late word about the expired one is refused and changes nothing.
    assert fake.enter(fake.submitted[0]) == "expired"
    assert a.wait_started(1) == lost
    assert fake.enter(fake.submitted[1]) is None
    assert bare(b.wait_started(1)) == turns.Started("t1")
    fake.end_turn()
    assert bare(b.wait_ended(1)) == turns.Ended("t1", "answer")
    # An untied start expires the same way, and a start after it goes in.
    c = s.deliver("c", only_if_idle=True)
    assert isinstance(c.wait_started(2), turns.Lost)
    d = s.deliver("d", only_if_idle=True)
    assert fake.enter(fake.handed(4)[3]) is None
    assert bare(d.wait_started(1)) == turns.Started("t2")


def test_a_command_the_harness_never_settles_expires_and_frees_the_slot(
        make_endpoint, monkeypatch):
    """A command the adapter never settles fails past the bound and frees the
    one command slot; the caller who stopped waiting first got `Failed` already."""
    from moxi_turns import endpoint

    monkeypatch.setattr(endpoint, "COMMAND_RATE_S", 0.0)
    monkeypatch.setattr(endpoint, "PENDING_TTL_S", 0.4)
    env = dict(os.environ, MOXI_TURNS_COMMANDS="clear,exit")
    fake = FakeHarness()
    fake.auto_commands = False
    ep, fake = make_endpoint(fake, env=env)
    s = turns.find(session_id="s-1")
    first = s.command("clear", timeout=0.1)          # the caller stops waiting
    assert isinstance(first, turns.Failed)
    assert not first.reason.startswith("expired")
    assert s.command("exit", timeout=0.1) == turns.Refused("in-flight")
    pending = ep._commands[fake.commands[0].id]
    deadline = time.monotonic() + 3
    while pending.result is None and time.monotonic() < deadline:
        time.sleep(0.02)                             # past the bound: the slot frees
    assert pending.result.reason.startswith("expired")
    # An adapter that comes back late is told the command expired, so it does not run.
    fake.run_command(fake.commands[0])
    assert ep.ledger.session_id == "s-1"
    fake.auto_commands = True
    assert s.command("clear", timeout=1) == turns.Ran("s-2")
    # A caller who waits without a bound gets the expiry, not a hang.
    fake.auto_commands = False
    t0 = time.monotonic()
    late = ep.command("exit", timeout=None)
    assert isinstance(late, turns.Failed) and late.reason.startswith("expired")
    assert time.monotonic() - t0 < 2
