"""The quiet read: its words and their order, a shell entry that holds nothing, a person
turn from a delivery-less `turn.started` and from the open turn at a watch's start, the
held state of a turn not yet tied, reads that fail, the pane re-read between events, and
the watch's own thread."""

from __future__ import annotations

import queue
from dataclasses import dataclass, field

import pytest

from moxi_launcher import quiet

NOW = 1_800_000_000.0


# Stand-ins for turns' and slots' values, field for field as the read uses them
# (`test_the_fields_the_read_uses_are_turns_and_slots_own` holds them against the real ones).


@dataclass(frozen=True)
class DeliveryRef:
    id: str
    ref: str | None = None


@dataclass(frozen=True)
class OpenTurn:
    turn_id: str | None
    opening: DeliveryRef | None
    command: str | None
    started_at: float


@dataclass(frozen=True)
class Facts:
    session_id: str | None = "s"
    turn_number: int = 3
    turn_open: bool = False
    turn: OpenTurn | None = None
    agents: int | None = 0
    draft: bool = False
    at: float = NOW
    stale: bool = False


@dataclass(frozen=True)
class Agent:
    id: str
    kind: str
    state: str
    final: bool | None


@dataclass(frozen=True)
class Agents:
    entries: tuple = ()
    complete: bool = True
    at: float = NOW


@dataclass(frozen=True)
class PaneUse:
    slot: int = 3
    pane_id: str | None = "%9"
    pane_in_mode: bool = False
    attached: int = 0
    client_activity: float | None = None
    pane_activity: float | None = None


@dataclass(frozen=True)
class TurnStarted:
    turn_id: str | None
    session_id: str | None
    turn_number: int
    deliveries: tuple = ()
    command: str | None = None


@dataclass(frozen=True)
class TurnEnded:
    turn_id: str | None
    session_id: str | None
    turn_number: int
    outcome: str = "completed"


@dataclass(frozen=True)
class Gone:
    reason: str


def person(started_at, turn_id="t"):
    return OpenTurn(turn_id, None, None, started_at)


def delivered(started_at):
    return OpenTurn("t", DeliveryRef("d-1", "launcher:r1"), None, started_at)


class FakeSession:
    def __init__(self, facts=None, agents=None, *, facts_error=None, agents_error=None):
        self._facts, self._agents = facts or Facts(), agents or Agents()
        self.facts_error, self.agents_error = facts_error, agents_error
        self.watches = []

    def facts(self):
        if self.facts_error:
            raise self.facts_error
        return self._facts

    def agents(self):
        if self.agents_error:
            raise self.agents_error
        return self._agents

    def watch(self):
        made = FakeWatch(self._facts)
        self.watches.append(made)
        return made


class FakeWatch:
    """turns' watch: `facts`, then events as the test sends them; iteration blocks."""

    def __init__(self, facts):
        self.facts = facts
        self.events: queue.Queue = queue.Queue()
        self.closed = False

    def __iter__(self):
        while True:
            event = self.events.get()
            if event is None:
                return
            if isinstance(event, Exception):
                raise event
            yield event
            if isinstance(event, Gone):
                return

    def close(self):
        self.closed = True
        self.events.put(None)


@dataclass
class FakeSlots:
    use: PaneUse = field(default_factory=PaneUse)
    error: Exception | None = None
    asked: list = field(default_factory=list)

    def pane_use(self, slot):
        self.asked.append(slot)
        if self.error is not None:
            raise self.error
        return self.use


def judge(facts=None, agents=None, pane=None, **kwargs):
    kwargs.setdefault("now", NOW)
    return quiet.judge(facts or Facts(), agents or Agents(), pane, **kwargs)


# -- the words ----------------------------------------------------------------------------


def test_nothing_holds_a_quiet_session():
    reading = judge(pane=PaneUse())
    assert reading.quiet and reading.first is None and reading.words == ()
    assert reading.turn_number == 3


def test_every_word_holds_in_its_order():
    facts = Facts(turn_open=True, turn=person(NOW - 10), draft=True, stale=True)
    agents = Agents((Agent("a", "subagent", "running", False),))
    reading = judge(facts, agents, PaneUse(pane_in_mode=True))
    assert reading.words == quiet.WORDS
    assert reading.first.word == "turn"
    assert str(reading.hold("draft")) == "draft: a draft is in the prompt box"


def test_an_open_turn_holds():
    reading = judge(Facts(turn_open=True, turn=delivered(NOW - 500)))
    assert reading.words == ("turn",)
    assert reading.first.detail == "turn 3 is open"


def test_a_turn_not_yet_tied_holds_until_it_is():
    reading = judge(Facts(turn_open=True, turn=None))
    assert reading.words == ("turn",)
    assert "not yet tied" in reading.first.detail


@pytest.mark.parametrize("entry, holds", [
    (Agent("a", "subagent", "running", False), True),
    (Agent("a", "subagent", "pending", None), True),
    (Agent("a", "teammate", "waiting", False), True),
    (Agent("a", "teammate", "unknown", None), True),
    (Agent("a", "subagent", "running", True), False),  # its final notice came
    (Agent("a", "subagent", "done", False), False),
    (Agent("a", "subagent", "idle", None), False),
    (Agent("a", "shell", "running", False), False),  # a shell never holds
    (Agent("a", "shell", "unknown", None), False),
])
def test_which_agents_hold(entry, holds):
    reading = judge(agents=Agents((entry,)))
    assert reading.words == (("subagents",) if holds else ())


def test_an_incomplete_agent_list_holds():
    reading = judge(agents=Agents((), complete=False))
    assert reading.words == ("subagents",)
    assert "could not be read whole" in reading.first.detail


def test_the_subagent_words_count_the_live_ones():
    agents = Agents((Agent("a", "subagent", "running", False),
                     Agent("b", "teammate", "waiting", None),
                     Agent("c", "shell", "running", False)))
    assert judge(agents=agents).first.detail == "2 still working (subagent, teammate)"


# -- a person -----------------------------------------------------------------------------


@pytest.mark.parametrize("use, words", [
    (PaneUse(pane_in_mode=True), "the pane is in copy mode"),
    (PaneUse(attached=1, client_activity=NOW - 40), "a client was active 40 s ago"),
    (PaneUse(pane_activity=NOW - 5), "the pane was active 5 s ago"),
])
def test_a_person_at_the_pane_holds(use, words):
    reading = judge(pane=use)
    assert reading.words == ("person",)
    assert str(reading.first) == f"person: {words}"


def test_activity_older_than_the_window_holds_nothing():
    use = PaneUse(attached=1, client_activity=NOW - 200, pane_activity=NOW - 121)
    assert judge(pane=use).quiet
    assert judge(pane=use, person_s=300).words == ("person",)


def test_pane_output_from_the_harness_own_time_holds_nothing():
    own = NOW - 10
    assert judge(pane=PaneUse(pane_activity=own), own_output_from=own).quiet
    assert judge(pane=PaneUse(pane_activity=NOW - 1), own_output_from=own).quiet
    held = judge(pane=PaneUse(pane_activity=own - 1), own_output_from=own)
    assert str(held.first) == "person: the pane was active 11 s ago"


@pytest.mark.parametrize("use", [PaneUse(pane_in_mode=True, pane_activity=NOW - 1),
                                 PaneUse(attached=1, client_activity=NOW - 1,
                                         pane_activity=NOW - 1)])
def test_copy_mode_and_a_client_hold_whatever_the_harness_own_time(use):
    assert judge(pane=use, own_output_from=NOW - 10).words == ("person",)


def test_a_pane_that_is_gone_holds_nothing():
    assert judge(pane=PaneUse(pane_id=None, pane_in_mode=True)).quiet


def test_an_open_turn_no_delivery_opened_holds_as_a_person():
    reading = judge(Facts(turn_open=True, turn=person(NOW - 30)))
    assert reading.words == ("turn", "person")
    assert reading.hold("person").detail == "a turn no delivery opened, 30 s ago"
    assert reading.person_turn_at == NOW - 30


def test_a_delivered_or_command_turn_is_no_person():
    assert judge(Facts(turn_open=True, turn=delivered(NOW - 30))).words == ("turn",)
    command = OpenTurn("t", None, "compact", NOW - 30)
    assert judge(Facts(turn_open=True, turn=command)).words == ("turn",)


def test_a_person_turn_the_watch_saw_holds_after_it_ended():
    assert judge(person_turn_at=NOW - 50).hold("person").detail == (
        "a turn no delivery opened, 50 s ago")
    assert judge(person_turn_at=NOW - 130).quiet


# -- reads that fail ----------------------------------------------------------------------


def test_facts_that_fail_hold_unreadable_and_nothing_else_from_them():
    session = FakeSession(facts_error=RuntimeError("the endpoint did not answer facts"))
    reading = quiet.read(session, clock=lambda: NOW)
    assert reading.words == ("unreadable",)
    assert reading.first.detail == "facts: the endpoint did not answer facts"
    assert reading.turn_number is None


def test_stale_facts_hold_unreadable():
    reading = quiet.read(FakeSession(Facts(stale=True)), clock=lambda: NOW)
    assert reading.words == ("unreadable",)
    assert "stale" in reading.first.detail


def test_every_failed_read_is_named():
    session = FakeSession(agents_error=RuntimeError("no list"))
    slots = FakeSlots(error=RuntimeError("tmux display-message failed"))
    reading = quiet.read(session, slot=3, slots_api=slots, clock=lambda: NOW)
    assert reading.words == ("unreadable",)
    assert reading.first.detail == "agents: no list; pane: tmux display-message failed"


def test_without_slots_a_slot_launch_is_unreadable(monkeypatch):
    monkeypatch.setattr(quiet.slots, "api", lambda: None)
    reading = quiet.read(FakeSession(), slot=3, clock=lambda: NOW)
    assert reading.first.detail == "pane: slots is not installed"


def test_a_launch_with_no_slot_reads_no_pane():
    slots = FakeSlots(use=PaneUse(pane_in_mode=True))
    assert quiet.read(FakeSession(), slots_api=slots, clock=lambda: NOW).quiet
    assert slots.asked == []


def test_the_whole_read_asks_turns_and_the_pane():
    slots = FakeSlots(use=PaneUse(attached=1, client_activity=NOW - 1))
    session = FakeSession(Facts(draft=True))
    reading = quiet.read(session, slot="3", slots_api=slots, clock=lambda: NOW)
    assert reading.words == ("draft", "person") and slots.asked == ["3"]


# -- the pane re-read between events ------------------------------------------------------


def test_the_person_re_read_keeps_the_turns_reads():
    slots = FakeSlots(error=RuntimeError("tmux gone"))
    session = FakeSession(Facts(turn_open=True, turn=delivered(NOW - 1)))
    last = quiet.read(session, slot=3, slots_api=slots, clock=lambda: NOW)
    assert last.words == ("turn", "unreadable")
    slots.error, slots.use = None, PaneUse(pane_in_mode=True)
    session.facts_error = RuntimeError("never asked")
    again = quiet.reread_person(last, slot=3, slots_api=slots, clock=lambda: NOW + 2)
    assert again.words == ("turn", "person")
    assert again.facts is last.facts and again.at == NOW + 2


def test_the_person_re_read_lets_a_window_run_out():
    last = judge(pane=PaneUse(), person_turn_at=NOW - 110)
    assert last.words == ("person",)
    again = quiet.reread_person(last, slot=3, slots_api=FakeSlots(), clock=lambda: NOW + 15)
    assert again.quiet


# -- the watch ----------------------------------------------------------------------------


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def test_the_turn_open_when_the_watch_began_counts_from_its_start():
    session = FakeSession(Facts(turn_open=True, turn=person(NOW - 20)))
    with quiet.Watch(session, clock=Clock()) as watch:
        assert watch.person_turn_at == NOW - 20
        assert watch.read().hold("person").detail == "a turn no delivery opened, 20 s ago"


def test_a_delivery_less_turn_started_is_a_person_turn_from_when_it_came():
    clock = Clock()
    session = FakeSession()
    with quiet.Watch(session, clock=clock) as watch:
        fake = session.watches[0]
        assert watch.person_turn_at is None
        clock.now = NOW + 5
        fake.events.put(TurnStarted("t1", "s", 4, deliveries=(DeliveryRef("d", "r"),)))
        seen = watch.wait(0, 2)
        assert seen == 1 and watch.person_turn_at is None and watch.turn_number == 4
        fake.events.put(TurnEnded("t1", "s", 4))
        seen = watch.wait(seen, 2)
        clock.now = NOW + 10
        fake.events.put(TurnStarted("t2", "s", 5))
        seen = watch.wait(seen, 2)
        assert watch.person_turn_at == NOW + 10 and watch.turn_number == 5
        fake.events.put(TurnEnded("t2", "s", 5))
        watch.wait(seen, 2)
        clock.now = NOW + 40
        reading = watch.read()
        assert reading.words == ("person",)
        assert reading.first.detail == "a turn no delivery opened, 30 s ago"
        clock.now = NOW + 200
        assert watch.reread_person(reading).quiet


def test_a_turn_held_for_its_tie_is_learned_from_its_turn_started():
    clock = Clock()
    session = FakeSession(Facts(turn_open=True, turn=None))
    with quiet.Watch(session, clock=clock) as watch:
        assert watch.person_turn_at is None
        assert watch.read().words == ("turn",)
        session.watches[0].events.put(TurnStarted("t", "s", 3))
        watch.wait(0, 2)
        assert watch.person_turn_at == NOW


def test_a_command_turn_is_no_person():
    session = FakeSession()
    with quiet.Watch(session, clock=Clock()) as watch:
        session.watches[0].events.put(TurnStarted("t", "s", 4, command="compact"))
        watch.wait(0, 2)
        assert watch.person_turn_at is None


def test_wait_times_out_with_no_event():
    with quiet.Watch(FakeSession(), clock=Clock()) as watch:
        assert watch.wait(0, 0.05) == 0 and watch.gone is None


def test_the_watch_ends_on_gone_and_on_a_broken_connection():
    session = FakeSession()
    watch = quiet.Watch(session, clock=Clock())
    session.watches[0].events.put(Gone("the session ended"))
    watch.wait(0, 2)
    assert watch.gone == "the session ended"
    watch.close()

    session = FakeSession()
    watch = quiet.Watch(session, clock=Clock())
    session.watches[0].events.put(RuntimeError("connection reset"))
    watch.wait(0, 2)
    assert watch.gone == "the watch broke: connection reset"
    watch.close()


def test_close_stops_the_thread():
    session = FakeSession()
    watch = quiet.Watch(session, clock=Clock())
    watch.close()
    assert session.watches[0].closed
    assert not watch._thread.is_alive()
    assert watch.gone == "the watch ended"


# -- the shapes against turns and slots ---------------------------------------------------


def test_the_fields_the_read_uses_are_turns_and_slots_own():
    results = pytest.importorskip("moxi_turns.results")
    names = {cls: {f.name for f in cls.__dataclass_fields__.values()}
             for cls in (results.Facts, results.OpenTurn, results.Agent, results.Agents,
                         results.TurnStarted, results.TurnEnded, results.Gone)}
    assert {"turn_number", "turn_open", "turn", "draft", "stale"} <= names[results.Facts]
    assert {"opening", "command", "started_at"} <= names[results.OpenTurn]
    assert {"kind", "state", "final"} <= names[results.Agent]
    assert {"entries", "complete"} <= names[results.Agents]
    assert {"turn_number", "deliveries", "command"} <= names[results.TurnStarted]
    assert {"turn_number"} <= names[results.TurnEnded]
    assert {"reason"} <= names[results.Gone]
    assert set(quiet.LIVE_KINDS) | {"shell"} == set(results.AGENT_KINDS)
    assert set(quiet.LIVE_STATES) <= set(results.AGENT_STATES)
    model = pytest.importorskip("moxi_slots.model")
    assert {"pane_id", "pane_in_mode", "client_activity", "pane_activity"} <= {
        f.name for f in model.PaneUse.__dataclass_fields__.values()}
