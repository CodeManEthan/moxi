"""comms' Codex adapter over turns: real daemon, real sockets, a stand-in turns session.

The daemon and its socket are the ordinary rig; the session is `fake_turns.FakeSession`,
shaped as turns' public `Session`, whose turns the test starts and ends as turns' Codex
host would report them. `test_codex_turns_host.py` runs the wrapper over turns' real
host, and `test_codex_wrapper.py` the wrapper's own steps. Nothing here spawns codex and nothing here touches the network.
"""

from __future__ import annotations

import itertools
import time

import pytest

from conftest import clock_reads_warm, cursor_of, frame_kinds, wait_until
from moxi_comms import codex_threads, owed, paths
from moxi_comms.adapters.codex import CodexAdapter, HostGone, layer_one_hash
from moxi_comms.adapters.codex_roster import CodexAgent

from fake_turns import FakeSession, fake_find


@pytest.fixture(autouse=True)
def _gate_reads_warm(monkeypatch):
    """Past the daemon's cold-cache gate, on a clock that reads warm: this suite tests
    the adapter's own mechanics."""
    clock_reads_warm(monkeypatch, "codex")


IDENTITY = "planner@proj-a"


@pytest.fixture
def launch(rig, tmp_path):
    """One wrapper for `planner@proj-a` over a fresh `FakeSession`."""
    made: list = []

    def build(*, name="planner", scope="proj-a", directors=(), start=True,
              session=None, autoreconnect=False, backoff=None, **kwargs):
        qualified = f"{name}@{scope}" if scope != "global" else name
        agent = CodexAgent(qualified=qualified, name=name, scope=scope,
                           cwd=str(tmp_path), thread_file=codex_threads.path(qualified),
                           directors=list(directors))
        session = session or FakeSession()
        exits: list = []
        adapter = CodexAdapter(agent, find=fake_find(session), harness_pid=4242,
                               autoreconnect=autoreconnect, backoff=backoff,
                               clock=rig.clock,
                               status_sink=lambda text: adapter.statuses.append(text),
                               exit_hook=lambda: exits.append(True), **kwargs)
        adapter.statuses = []
        adapter.exits = exits
        adapter.fake = session
        made.append(adapter)
        if start:
            adapter.start()
        return adapter

    yield build
    for adapter in made:
        adapter.stop()


def send(rig, text, *, to=IDENTITY,  name="main", scope="proj-a"):
    code, _ = rig.send(to, text, sender=dict(name=name, scope=scope))
    assert code == 0


def delivered(session, count=1):
    return wait_until(lambda: len(session.delivered) >= count and session.delivered)


def turn_frames(frames, key):
    """The message ids every `turn_ended` so far named under `key` (`handled_ids` or
    `unhandled_ids`), in order."""
    return [m for f in frame_kinds(frames, "turn") for m in f.get(key, ())]


# --------------------------------------------------------------------------- the launch

def test_it_registers_with_the_codex_kind(rig, launch):
    adapter = launch()
    binding = rig.daemon.bindings[IDENTITY]
    assert (binding.adapter, binding.tier) == ("codex", "chosen")
    assert adapter.identity == IDENTITY


def test_the_wrappers_death_is_its_deregistration(rig, launch):
    adapter = launch()
    adapter.stop()
    assert wait_until(lambda: IDENTITY not in rig.daemon.bindings)


def test_the_session_is_found_by_the_hosts_pid_once_bound(rig, launch):
    """The adapter binds, says so, then finds the host's session by the host's pid."""
    order = []
    adapter = launch(start=False, on_bound=lambda: order.append(
        ("bound", IDENTITY in rig.daemon.bindings)))
    adapter._find = lambda pid, timeout: order.append(("find", pid)) or adapter.fake
    adapter.start()
    assert order == [("bound", True), ("find", 4242)]
    assert adapter.session is adapter.fake


def test_the_look_for_the_session_waits_while_the_host_runs(rig, launch):
    looks = []

    def find(pid, timeout):
        looks.append(timeout)
        if len(looks) < 3:
            raise TimeoutError("not yet")
        return adapter.fake

    adapter = launch(start=False, find_step=0.01)
    adapter._find = find
    adapter.start()
    assert looks == [0.01, 0.01, 0.01] and adapter.thread_id == "thread-1"


def test_a_host_that_ends_before_its_session_is_ready_ends_the_look(rig, launch):
    adapter = launch(start=False, alive=lambda: False)
    adapter._find = lambda pid, timeout: pytest.fail("no look once the host has ended")
    with pytest.raises(HostGone):
        adapter.start()


def test_the_thread_record_names_the_launch_and_its_layer_one(rig, launch):
    adapter = launch()
    record = codex_threads.read_meta(IDENTITY)
    assert record["thread_id"] == "thread-1" == adapter.thread_id
    assert record["launch"] == adapter.launch
    assert record["layer_one"] == layer_one_hash(adapter.comms_path)


def test_a_thread_named_after_the_launch_is_adopted_from_the_first_event(rig, launch):
    """A no-window launch whose first load failed returns with no thread id. The first
    event that names the thread gives comms the thread record and the daemon its id."""
    session = FakeSession(session_id=None)
    adapter = launch(session=session)
    assert adapter.thread_id is None and codex_threads.read(IDENTITY) is None
    assert "session_id" not in adapter.session_report(register=False)
    assert rig.daemon.bindings[IDENTITY].session_id is None
    session.session_id = "thread-9"
    session.ready("startup")
    session.start_turn()
    session.end_turn("answer")
    assert wait_until(lambda: adapter.session_report(register=False).get("turned"))
    assert adapter.thread_id == "thread-9"
    assert codex_threads.read(IDENTITY) == "thread-9"
    record = codex_threads.read_meta(IDENTITY)
    assert record["launch"] == adapter.launch
    assert record["layer_one"] == layer_one_hash(adapter.comms_path)
    assert adapter.session_report(register=False)["session_id"] == "thread-9"
    assert wait_until(lambda: rig.daemon.bindings[IDENTITY].session_id == "thread-9")


def test_a_later_event_naming_another_thread_does_not_replace_the_launchs(rig, launch):
    adapter = launch()
    adapter.fake.session_id = "thread-other"
    adapter.fake.ready("compact")
    adapter.fake.start_turn()
    adapter.fake.end_turn("answer")
    assert wait_until(lambda: adapter.session_report(register=False)["turned"])
    assert adapter.thread_id == "thread-1" == codex_threads.read(IDENTITY)


def test_the_launch_no_longer_warns_that_roster_options_are_not_applied(rig, launch):
    adapter = launch()
    assert not any("NOT applied" in s for s in adapter.statuses), adapter.statuses


def test_the_stop_handler_is_taken_at_the_launch(rig, launch):
    adapter = launch()
    assert adapter.fake.holder is not None
    adapter.stop()
    assert adapter.fake.holder.closed


# --------------------------------------------------------------------------- delivery

def test_a_message_to_an_idle_thread_is_delivered_named_by_its_digest(rig, launch):
    adapter = launch()
    send(rig, "hello planner")
    first = delivered(adapter.fake)[0]
    assert first.steer is False and "hello planner" in first.text
    assert "[interrupt]" not in first.text
    assert first.ref is not None and first.ref.isdigit(), "the ref is the digest's id"
    assert wait_until(lambda: adapter.held_ids)
    adapter.fake.end_turn("answer")
    assert wait_until(lambda: not adapter.held_ids)
    assert wait_until(lambda: cursor_of(IDENTITY) is not None)
    assert not adapter.hands
    assert not paths.cursors_dir().exists(), "the adapter writes no cursor file"


def test_plain_mail_waits_for_the_running_turns_end(rig, launch):
    """A running turn no longer takes plain mail: it goes as the next turn's digest."""
    session = FakeSession(turn_open=True)
    adapter = launch(session=session)
    send(rig, "while you work")
    send(rig, "and this too")
    assert wait_until(lambda: len(adapter.queue) == 2)
    time.sleep(0.3)
    assert session.delivered == [], "nothing goes into the running turn"
    session.end_turn("answer")
    (row,) = delivered(session)
    assert row.steer is False
    assert "while you work" in row.text and "and this too" in row.text, "one digest"
    assert wait_until(lambda: session.open_turn == "turn-2")
    session.end_turn("answer")
    assert wait_until(lambda: not adapter.held_ids)


def test_one_plain_digest_at_a_time(rig, launch):
    """A plain digest handed and not yet in its turn holds the next one back."""
    session = FakeSession()
    session.answer = "hang"
    adapter = launch(session=session)
    send(rig, "first")
    delivered(session)
    send(rig, "second")
    assert wait_until(lambda: len(adapter.queue) == 1)
    time.sleep(0.3)
    assert len(session.delivered) == 1


def test_an_aborted_turn_leaves_its_digest_unhandled_and_the_cursor_unmoved(rig, launch):
    adapter = launch()
    send(rig, "will be interrupted")
    delivered(adapter.fake)
    assert wait_until(lambda: adapter.held_ids)
    adapter.fake.end_turn("aborted")
    assert wait_until(lambda: not adapter.held_ids)
    assert cursor_of(IDENTITY) is None


def test_a_refused_delivery_is_unhandled_at_once_and_not_put_back(rig, launch, frames):
    """A refusal says the text never went in: the digest is unhandled at once, and the
    daemon hands it again at the next release point. The adapter does not requeue it."""
    adapter = launch()
    adapter.fake.answer = "refuse"
    send(rig, "try again")
    delivered(adapter.fake)
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert not adapter.queue and not adapter.hands
    time.sleep(0.2)
    assert len(adapter.fake.delivered) == 1, "not handed again at once"
    adapter.fake.answer = "start"
    adapter.fake.start_turn()
    adapter.fake.end_turn("answer")              # the next release point
    rows = delivered(adapter.fake, 2)
    assert "try again" in rows[1].text and rows[1].steer is False
    adapter.fake.end_turn("answer")
    assert wait_until(lambda: not adapter.held_ids)


def test_a_lost_delivery_is_unhandled_at_once(rig, launch, frames):
    adapter = launch()
    adapter.fake.answer = "lose"
    send(rig, "into the void")
    delivered(adapter.fake)
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert not adapter.hands and not adapter.held_ids
    assert not turn_frames(frames, "handled_ids")


def test_a_delivery_with_no_answer_is_waited_on_not_handed_again(rig, launch, frames):
    """An unanswered hand-in may be in a turn already: it stays open, is never
    requeued, and settles only when its outcome comes or the session ends."""
    adapter = launch()
    adapter.fake.answer = "hang"
    send(rig, "no answer")
    delivered(adapter.fake)
    time.sleep(0.5)
    assert adapter.hands and adapter.held_ids, "the digest stays open"
    assert not turn_frames(frames, "unhandled_ids")
    assert len(adapter.fake.delivered) == 1 and not adapter.queue
    adapter.fake.gone("the host failed")
    assert wait_until(lambda: turn_frames(frames, "unhandled_ids"))
    assert len(adapter.fake.delivered) == 1


class _FastSession(FakeSession):
    """A session whose turn starts and ends before the hand-in call returns."""

    def deliver(self, text, **kw):
        d = super().deliver(text, **kw)
        self.end_turn("answer")
        time.sleep(0.2)
        return d


def test_a_turn_that_ends_before_the_hand_in_returns_still_settles(rig, launch):
    session = _FastSession()
    adapter = launch(session=session)
    send(rig, "fast turn")
    delivered(session)
    assert wait_until(lambda: cursor_of(IDENTITY) is not None)
    assert wait_until(lambda: not adapter.held_ids and not adapter.hands)


def test_held_mail_waits_for_the_running_turns_end(rig, launch):
    session = FakeSession(turn_open=True)
    adapter = launch(session=session)
    with adapter._lock:
        adapter.queue.append({"message_id": "999", "held": True})
    assert not adapter.may_take_held()
    session.end_turn("answer")
    assert wait_until(lambda: adapter.may_take_held())


# --------------------------------------------------------------------------- the session's events

def test_a_resume_and_a_compaction_are_reported_ready(rig, launch, monkeypatch):
    adapter = launch()
    seen: list = []
    monkeypatch.setattr(adapter, "report_ready", seen.append)
    adapter.fake.ready("resume")
    adapter.fake.ready("compact")
    adapter.fake.ready("startup")
    assert wait_until(lambda: seen == ["resume", "compact"])


def test_the_first_turn_end_sets_turned(rig, launch):
    adapter = launch()
    assert adapter.session_report(register=False)["turned"] is False
    adapter.fake.start_turn()
    adapter.fake.end_turn("answer")
    assert wait_until(lambda: adapter.session_report(register=False)["turned"])


def test_a_register_before_the_launch_reports_a_new_session(rig, launch):
    adapter = launch(start=False)
    assert adapter.session_report(register=True)["new_session"] is True


def test_the_session_going_away_ends_the_launch(rig, launch):
    adapter = launch()
    send(rig, "open when it went")
    delivered(adapter.fake)
    adapter.fake.gone()
    assert wait_until(lambda: adapter.exits)
    assert adapter.gone_reason.startswith("the Codex window closed")
    assert wait_until(lambda: not adapter.held_ids)
    assert not adapter.can_inject()


# --------------------------------------------------------------------------- the holder

def test_a_stop_with_nothing_owed_lets_the_turn_end(rig, launch):
    adapter = launch()
    adapter.fake.start_turn()
    assert adapter.fake.stop(stop_hook_active=False, task_types=(), crons=0) is None


def test_a_stop_that_owes_a_reply_holds_the_turn_with_the_reminder(rig, launch,
                                                                    monkeypatch):
    adapter = launch()
    asked: list = []

    def check(fields):
        asked.append(fields)
        return owed.Decision(block="reply to main", names=("main",))

    monkeypatch.setattr(adapter, "reply_check", check)
    turn = adapter.fake.start_turn()
    assert adapter.fake.stop(stop_hook_active=False, task_types=("background-terminal",),
                             crons=0) == "reply to main"
    assert asked == [{"stop_hook_active": False, "task_types": ["background-terminal"],
                      "crons": 0}]
    assert adapter.blocked and adapter._blocked_turn == turn
    adapter.fake.end_turn("answer")
    assert wait_until(lambda: not adapter.blocked)


def test_a_new_turn_resolves_the_reminder(rig, launch, monkeypatch):
    adapter = launch()
    monkeypatch.setattr(adapter, "reply_check",
                        lambda fields: owed.Decision(block="reply", names=("main",)))
    adapter.fake.start_turn()
    adapter.fake.stop(stop_hook_active=False, task_types=(), crons=0)
    assert adapter.blocked
    adapter.fake.start_turn()
    assert wait_until(lambda: not adapter.blocked)


def test_a_launch_into_a_held_name_waits_for_it(rig, launch):
    first = launch()
    second = launch(autoreconnect=True, backoff=itertools.repeat(0.05))
    assert second.identity is None and second.claims.state == "waiting"
    assert first.identity == IDENTITY




def test_a_wrappers_launch_into_a_held_name_is_refused_before_the_look(rig, launch):
    """The wrapper's adapter does not wait for a held name: the bind's refusal ends the
    launch before the host starts."""
    from moxi_comms.client import DaemonError

    launch()
    second = launch(start=False, autoreconnect=True, backoff=itertools.repeat(0.05),
                    wait_when_held=False)
    second._find = lambda pid, timeout: pytest.fail("no look after a refused bind")
    with pytest.raises(DaemonError) as refused:
        second.start()
    assert refused.value.refused
