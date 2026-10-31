"""The relaunch job against fakes of turns, slots, comms and the launch: each step's
outcome, every result of the exit and the dialog mapped, the deferred stop, the slot hold
released on every terminal path, a run ended under its job, the notices, and repair."""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest
from moxi import proc

from moxi_launcher import job, notices, store

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
SETTINGS = {"relaunch.wait_s": 900, "relaunch.person_s": 120, "relaunch.dialog_s": 60,
            "relaunch.name_wait_s": 15, "relaunch.follow_s": 300, "relaunch.fallback": "",
            "relaunch.notice_retry_s": 30, "records.keep_days": 90}


def me() -> str:
    ident = proc.identity(os.getpid())
    return f"{ident.pid}@{ident.started:.6f}"


class Result:
    def __init__(self, kind, reason=None):
        self.KIND, self.reason = kind, reason


class FakeWatch:
    def __init__(self, facts):
        self.facts = facts
        self._closed = threading.Event()

    def __iter__(self):
        self._closed.wait(5)
        return iter(())

    def close(self):
        self._closed.set()


def facts(turn_open=False, number=4, draft=False):
    return SimpleNamespace(turn_open=turn_open, turn=None, turn_number=number, draft=draft,
                           stale=False)


class FakeSession:
    def __init__(self, exits=(Result("ran"),), facts_seq=None, agents_complete=True,
                 delivered="started"):
        self.session_id, self.endpoint_pid, self.pid = SID, os.getpid(), os.getppid()
        self.capabilities = frozenset({"exit"})
        self.exits = list(exits)
        self.facts_seq = list(facts_seq or [facts()])
        self.agents_complete = agents_complete
        self.delivered = delivered
        self.commands, self.deliveries, self.closed = [], [], 0

    def facts(self):
        value = self.facts_seq[0] if len(self.facts_seq) == 1 else self.facts_seq.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def agents(self):
        return SimpleNamespace(complete=self.agents_complete, entries=[])

    def watch(self):
        return FakeWatch(self.facts())

    def command(self, name, *, if_turn_number=None):
        self.commands.append((name, if_turn_number))
        result = self.exits[0] if len(self.exits) == 1 else self.exits.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def deliver(self, text, *, ref=None):
        self.deliveries.append(text)
        kind = self.delivered
        return SimpleNamespace(wait_started=lambda timeout: Result(kind, "busy-turn"))

    def close(self):
        self.closed += 1


class FakeTurns:
    def __init__(self, session):
        self.session = session

    def find(self, *, session_id=None, pid=None):
        return self.session


class FakeSlots:
    class SlotsError(Exception):
        pass

    class TryAgain(SlotsError):
        pass

    class Refused(SlotsError):
        def __init__(self, reason, words="", holder=None):
            super().__init__(words)
            self.reason, self.words, self.holder = reason, words, holder

    CLAUDE_EXIT_DIALOG = SimpleNamespace(name="CLAUDE_EXIT_DIALOG")

    def __init__(self, *, hold=None, ended=False, screens=("dialog",), answer="ended",
                 why="", pane=None, shown=None):
        self.hold_error, self.ended, self.screens = hold, ended, list(screens)
        self.answer, self.why = answer, why
        self.pane = pane or SimpleNamespace(pane_id="%9", pane_in_mode=False,
                                            client_activity=None, pane_activity=None)
        self.shown = shown
        self.calls = []

    def supported(self):
        return SimpleNamespace(ok=True, why="")

    def show(self, slot):
        return self.shown

    def hold(self, slot, *, holder, seconds):
        self.calls.append(("hold", slot, holder))
        if self.hold_error is not None:
            raise self.hold_error

    def release(self, slot, *, holder):
        self.calls.append(("release", slot, holder))
        return True

    def wait_ended(self, slot, *, pane_id, timeout):
        self.calls.append(("wait_ended", slot, pane_id))
        return SimpleNamespace(ended=self.ended, gone=False, dead_status=None)

    def read_screen(self, slot, screens):
        name = self.screens[0] if len(self.screens) == 1 else self.screens.pop(0)
        screen = self.CLAUDE_EXIT_DIALOG if name == "dialog" else None
        if screen is not None:
            self.draw()
        return SimpleNamespace(screen=screen, full=True, dead=False)

    def draw(self):
        """The dialog drawing itself; real slots see it as the pane's output."""

    def pane_use(self, slot):
        return self.pane

    def answer_screen(self, slot, screen, wait=5):
        self.calls.append(("answer", slot, wait))
        return SimpleNamespace(outcome=self.answer, why=self.why, capture=None)

    def job(self, name):
        return None

    def names(self):
        return [c[0] for c in self.calls]


class FakeComms:
    GLOBAL = ""

    def __init__(self, outcomes=("sent",), state="free"):
        self.outcomes, self.state, self.sent = list(outcomes), state, []

    def send_as(self, module, name, to, text):
        self.sent.append((module, name, list(to), text))
        outcome = self.outcomes[0] if len(self.outcomes) == 1 else self.outcomes.pop(0)
        code = "name-reserved" if outcome == "reserved" else "nope"
        if outcome == "reserved":
            outcome = "refused"
        return SimpleNamespace(outcome=outcome, message_id="m1",
                               refusal=SimpleNamespace(code=code, text=code))

    def identity_state(self, name, scope=None):
        return SimpleNamespace(state=self.state)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        self.now += 1.0
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def launched_result(state="started", outcome="sent", reason=None):
    return SimpleNamespace(state=state, reason=reason, line=f"launch r2 {state}",
                           outcome=None if outcome is None else SimpleNamespace(
                               outcome=outcome), request_id="r2", base="row")


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    store.insert(connection, request_id="r1", actor="ann", harness="claude", target="slot",
                 cwd="/work", values={"model": "opus", "director": ["boss"]},
                 argv=["claude"], env={}, posture="operator", session_id=SID, slot="3",
                 identity="planner")
    store.set_facts(connection, "r1", pane_id="%9")
    connection.execute("UPDATE launches SET endpoint = ? WHERE request_id = 'r1'", (me(),))
    yield connection
    connection.close()


def arm(conn, mode="self", handoff="/work/h.md", turn_number=4):
    return store.insert_run(conn, kind="relaunch", mode=mode, request_id="r1",
                            session_id=SID, actor="planner" if mode == "self" else "boss",
                            handoff_path=handoff if mode == "self" else None, slot="3",
                            pane_id="%9", turn_number=turn_number, job="slots:relaunch-x")


def go(conn, run, *, session=None, slots_api=None, comms=None, launch=None, settings=None,
       cls=job.RelaunchJob, stop=None):
    session = session or FakeSession()
    slots_api = slots_api or FakeSlots(ended=True)
    seen = []

    def fake_launch(request):
        seen.append(request)
        return launch(request) if callable(launch) else (launch or launched_result())

    clock = Clock()
    runner = cls(conn, run.run_id, turns=FakeTurns(session), slots_api=slots_api,
                 comms=comms, launch=fake_launch, settings={**SETTINGS, **(settings or {})},
                 clock=clock, sleep=clock.sleep)
    if stop:
        runner.ask_stop(15)
    outcome = runner.go()
    return SimpleNamespace(outcome=outcome, run=store.get_run(conn, run.run_id),
                           events=[e.kind for e in store.run_events(conn, run.run_id)],
                           requests=seen, slots=slots_api, session=session, job=runner,
                           comms=comms)


# -- the steps ---------------------------------------------------------------------------


def test_a_self_run_relaunches_with_its_handoff_into_its_held_slot(conn):
    got = go(conn, arm(conn))
    assert got.outcome == "relaunched" and got.run.new_request_id == "r2"
    assert got.events[:4] == ["hold", "exit", "exit-result", "pane-ended"]
    assert got.session.commands == [("exit", 4)]
    request = got.requests[0]
    assert request.respawn and request.hold == got.run.run_id and request.slot == "3"
    assert request.relaunch_of == "r1" and request.handoff == "/work/h.md"
    assert request.posture == "relaunched (handoff: /work/h.md)"
    assert request.run == got.run.run_id and request.actor == "planner"


def test_a_director_run_carries_no_handoff_and_its_posture(conn):
    got = go(conn, arm(conn, mode="director"))
    assert got.outcome == "relaunched"
    request = got.requests[0]
    assert request.handoff is None and request.posture == "with:boss"
    assert "wait" not in got.events


def test_a_self_wait_that_never_quiets_expires_and_is_noted(conn):
    session = FakeSession(facts_seq=[facts(turn_open=True)])
    got = go(conn, arm(conn), session=session, settings={"relaunch.wait_s": 0})
    assert got.outcome == "expired" and got.run.word == "turn"
    assert "wait" in got.events and "note" in got.events
    assert session.deliveries[0].startswith("Relaunch note from launcher: run ")
    assert got.slots.calls == []


def test_a_note_not_taken_is_told_to_the_directors(conn):
    comms = FakeComms()
    session = FakeSession(facts_seq=[facts(turn_open=True)], delivered="refused")
    got = go(conn, arm(conn), session=session, comms=comms,
             settings={"relaunch.wait_s": 0})
    assert got.outcome == "expired"
    assert comms.sent and comms.sent[0][2] == ["boss"]
    assert "note-not-taken" in comms.sent[0][3]


def test_a_hold_refused_ends_not_held(conn):
    slots_api = FakeSlots(hold=FakeSlots.Refused("held", "held by someone"))
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "not-held" and got.run.word == "held"


@pytest.mark.parametrize("reason", job.TRANSIENT)
def test_a_transient_exit_refusal_ends_a_director_run_held(conn, reason):
    got = go(conn, arm(conn, mode="director"), session=FakeSession([Result("refused", reason)]))
    assert got.outcome == "held" and got.run.word == reason
    assert ("release", "3", got.run.run_id) in got.slots.calls


def test_a_transient_exit_refusal_sends_a_self_run_back_to_its_wait(conn):
    session = FakeSession([Result("refused", "busy-turn"), Result("ran")])
    got = go(conn, arm(conn), session=session)
    assert got.outcome == "relaunched"
    assert got.events.count("hold") == 2 and "again" in got.events
    assert got.slots.names().count("release") >= 1


@pytest.mark.parametrize("reason", job.TERMINAL)
def test_a_terminal_exit_refusal_ends_not_exited(conn, reason):
    got = go(conn, arm(conn), session=FakeSession([Result("refused", reason)]))
    assert got.outcome == "not-exited" and got.run.word == reason
    assert ("release", "3", got.run.run_id) in got.slots.calls


@pytest.mark.parametrize("result", [Result("ran"), Result("failed", "not-exited"),
                                    Result("failed", "timeout"), Result("refused", "gone"),
                                    RuntimeError("endpoint ending")])
def test_every_other_exit_result_goes_on_to_the_pane(conn, result):
    got = go(conn, arm(conn, mode="director"), session=FakeSession([result]))
    assert got.outcome == "relaunched"
    assert "pane-ended" in got.events


def test_the_dialog_is_answered_after_two_full_reads(conn):
    slots_api = FakeSlots(ended=False, screens=("dialog",))
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "relaunched"
    assert ("answer", "3", 0) in slots_api.calls
    assert slots_api.names().count("wait_ended") >= 2


def test_only_a_director_run_replays_with_the_directors_pass_list(conn):
    assert go(conn, arm(conn, mode="director")).requests[0].director is True
    assert go(conn, arm(conn)).requests[0].director is False


@pytest.mark.parametrize("base", ["no-credentials", "job"])
def test_a_replay_with_no_credentials_or_the_jobs_base_says_so(conn, base):
    result = launched_result()
    result.base = base
    got = go(conn, arm(conn, mode="director"), launch=lambda request: result)
    assert got.outcome == "relaunched"
    [event] = [e for e in store.run_events(conn, got.run.run_id) if e.kind == "base"]
    assert event.detail == {"base": base}


def test_a_dialog_with_a_new_turn_is_left_for_a_person(conn):
    session = FakeSession(facts_seq=[facts(number=5)])
    slots_api = FakeSlots(ended=False)
    got = go(conn, arm(conn, mode="director"), session=session, slots_api=slots_api)
    assert got.outcome == "exit-pending" and got.run.word == "turn"
    assert "answer" not in slots_api.names()


def test_a_person_at_the_dialog_presses_nothing(conn):
    import time

    pane = SimpleNamespace(pane_id="%9", pane_in_mode=False, client_activity=time.time(),
                           pane_activity=None)
    slots_api = FakeSlots(ended=False, pane=pane)
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "exit-pending" and got.run.word == "person"
    assert "answer" not in slots_api.names()


class DrawingSlots(FakeSlots):
    """Slots whose pane's activity moves each time the exit dialog draws, as tmux's
    window activity does; `then` changes the pane the first time it draws."""

    def __init__(self, then=None, **kw):
        super().__init__(ended=False, **kw)
        self.then = then

    def draw(self):
        if self.then is not None:
            self.pane, self.then = self.then, None
        self.pane.pane_activity = time.time()


def test_the_dialog_drawing_itself_is_not_a_person(conn):
    slots_api = DrawingSlots()
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "relaunched"
    assert ("answer", "3", 0) in slots_api.calls


def test_a_self_run_answers_the_dialog_that_drew_itself(conn):
    slots_api = DrawingSlots()
    got = go(conn, arm(conn), slots_api=slots_api)
    assert got.outcome == "relaunched" and "exit-pending" not in got.events


@pytest.mark.parametrize("then", [
    SimpleNamespace(pane_id="%9", pane_in_mode=True, client_activity=None,
                    pane_activity=None),
    SimpleNamespace(pane_id="%9", pane_in_mode=False, client_activity=time.time() + 3600,
                    pane_activity=None),
], ids=["copy-mode", "client"])
def test_copy_mode_or_a_client_at_the_dialog_still_holds(conn, then):
    slots_api = DrawingSlots(then=then)
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "exit-pending" and got.run.word == "person"
    assert "answer" not in slots_api.names()


def test_turns_gone_at_the_dialog_leaves_the_pane_check(conn):
    session = FakeSession(facts_seq=[RuntimeError("gone")])
    got = go(conn, arm(conn, mode="director"), session=session,
             slots_api=FakeSlots(ended=False))
    assert got.outcome == "relaunched"
    answer = [e for e in store.run_events(conn, got.run.run_id) if e.kind == "answer"][0]
    assert answer.detail["turns"] == "turns-gone"


@pytest.mark.parametrize("answer,outcome", [("ended-without-screen", "relaunched"),
                                            ("mismatch", "not-exited"),
                                            ("not-ended", "not-exited"),
                                            ("gone", "relaunch-failed"),
                                            ("no-pane", "relaunch-failed")])
def test_each_answer_maps_to_one_outcome(conn, answer, outcome):
    got = go(conn, arm(conn, mode="director"),
             slots_api=FakeSlots(ended=False, answer=answer, why="not-settled: moving"))
    assert got.outcome == outcome


def test_no_dialog_and_no_end_is_not_exited(conn):
    got = go(conn, arm(conn, mode="director"), slots_api=FakeSlots(ended=False,
                                                                    screens=(None,)))
    assert got.outcome == "not-exited" and got.run.word == "no-dialog"


def test_a_name_still_held_fails_the_relaunch(conn):
    got = go(conn, arm(conn, mode="director"), comms=FakeComms(state="held"))
    assert got.outcome == "relaunch-failed" and got.run.word == "name-held"
    assert got.requests == []


def test_a_launch_refused_after_the_exit_is_relaunch_failed(conn):
    from moxi_launcher import api

    def refuse(request):
        raise api.CommsOffline("identity", "comms' daemon is offline", "start it")

    got = go(conn, arm(conn, mode="director"), launch=refuse)
    assert got.outcome == "relaunch-failed"
    assert ("release", "3", got.run.run_id) in got.slots.calls


def test_a_respawn_that_failed_is_relaunch_failed(conn):
    got = go(conn, arm(conn, mode="director"),
             launch=launched_result(state="failed", outcome=None, reason="live-pane"))
    assert got.outcome == "relaunch-failed" and got.run.word == "live-pane"


def test_a_self_run_whose_first_turn_was_not_sent(conn):
    got = go(conn, arm(conn), launch=launched_result(state="failed", outcome="no-ready"))
    assert got.outcome == "first-turn-not-sent" and got.run.word == "no-ready"


def test_a_stop_before_the_exit_ends_the_run_stopped(conn):
    got = go(conn, arm(conn), stop=True)
    assert got.outcome == "stopped" and got.session.commands == []


def test_a_stop_from_the_exit_on_is_deferred_until_the_respawn(conn):
    run = arm(conn, mode="director")
    session = FakeSession()
    original = session.command

    def command(name, **kw):
        runner.ask_stop(15)
        return original(name, **kw)

    session.command = command
    clock = Clock()
    runner = job.RelaunchJob(conn, run.run_id, turns=FakeTurns(session),
                             slots_api=FakeSlots(ended=True), comms=None,
                             launch=lambda r: launched_result(), settings=SETTINGS,
                             clock=clock, sleep=clock.sleep)
    assert runner.go() == "relaunched"
    kinds = [e.kind for e in store.run_events(conn, run.run_id)]
    assert "stop-deferred" in kinds and kinds.index("stop-deferred") < kinds.index("ended")


def test_a_run_ended_under_its_job_does_not_act(conn):
    run = arm(conn, mode="director")
    store.end_run(conn, run.run_id, "error", word="job-gone")
    session = FakeSession()
    got = go(conn, run, session=session)
    assert session.commands == [] and got.requests == []


def test_a_run_ended_between_steps_stops_before_its_next_act(conn):
    run = arm(conn, mode="director")
    slots_api = FakeSlots(ended=True)
    original = slots_api.hold

    def hold(slot, **kw):
        original(slot, **kw)
        store.end_run(conn, run.run_id, "error", word="job-gone")

    slots_api.hold = hold
    session = FakeSession()
    got = go(conn, run, session=session, slots_api=slots_api)
    assert session.commands == [] and got.run.word == "job-gone"
    assert ("release", "3", run.run_id) in slots_api.calls


# -- notices -----------------------------------------------------------------------------


def test_a_failed_relaunch_notices_the_directors_with_the_repair_command(conn):
    comms = FakeComms()
    got = go(conn, arm(conn, mode="director"), comms=comms,
             launch=launched_result(state="failed", outcome=None, reason="spawn-failed"))
    module, name, to, text = comms.sent[0]
    assert (module, name, to) == ("launcher", "console", ["boss"])
    assert f"--from-record {got.run.run_id}" in text and "slot 3" in text


def test_a_notice_refused_for_its_place_is_retried_then_recorded(conn):
    comms = FakeComms(outcomes=("reserved",))
    got = go(conn, arm(conn, mode="director"), comms=comms,
             settings={"relaunch.notice_retry_s": 6},
             launch=launched_result(state="failed", outcome=None, reason="spawn-failed"))
    assert len(comms.sent) > 1
    assert "notice-refused" in got.events


def test_a_refused_notice_goes_once_to_the_fallback(conn):
    comms = FakeComms(outcomes=("refused", "sent"))
    go(conn, arm(conn, mode="director"), comms=comms,
       settings={"relaunch.fallback": "lead"},
       launch=launched_result(state="failed", outcome=None, reason="spawn-failed"))
    assert [s[2] for s in comms.sent] == [["boss"], ["lead"]]


def test_an_offline_daemon_is_tried_once_more_then_recorded(conn):
    comms = FakeComms(outcomes=("offline",))
    got = go(conn, arm(conn, mode="director"), comms=comms,
             launch=launched_result(state="failed", outcome=None, reason="spawn-failed"))
    assert len(comms.sent) == 2
    assert notices.OFFLINE_RETRY_S in got.job.sleep.__self__.slept


def test_without_comms_a_notice_is_recorded(conn):
    got = go(conn, arm(conn, mode="director"),
             launch=launched_result(state="failed", outcome=None, reason="spawn-failed"))
    notice = [e for e in store.run_events(conn, got.run.run_id) if e.kind == "notice"][0]
    assert notice.detail["verdict"] == "recorded"


# -- repair ------------------------------------------------------------------------------


def failed_run(conn, mode="self"):
    run = arm(conn, mode=mode)
    store.add_run_event(conn, run.run_id, "exit", {"turn_number": 4})
    store.end_run(conn, run.run_id, "relaunch-failed", word="spawn-failed")
    return store.get_run(conn, run.run_id)


def repair_run(conn, original):
    return store.insert_run(conn, kind="relaunch", mode="repair", request_id="r1",
                            session_id=SID, actor="ann", slot="3", repair_of=original.run_id,
                            handoff_path=original.handoff_path, job="slots:repair-y")


def shown(state, holder=None, chain=None):
    hold = SimpleNamespace(holder=holder, until=0) if holder else None
    last = SimpleNamespace(meta={"request_id": "r9", "chain": chain}) if chain else None
    return SimpleNamespace(state=state, hold=hold, last=last, created=1.0)


def test_a_repair_respawns_the_recorded_slot_under_the_original_holder(conn):
    original = failed_run(conn)
    slots_api = FakeSlots(shown=shown("ended", holder=original.run_id))
    got = go(conn, repair_run(conn, original), slots_api=slots_api, cls=job.RepairJob)
    assert got.outcome == "relaunched"
    request = got.requests[0]
    assert request.respawn and request.slot == "3" and request.hold == original.run_id
    assert request.handoff == original.handoff_path
    assert ("hold", "3", original.run_id) in slots_api.calls


def test_a_repair_of_a_directors_run_replays_with_the_plain_pass_list(conn):
    original = failed_run(conn, mode="director")
    slots_api = FakeSlots(shown=shown("ended", holder=original.run_id))
    got = go(conn, repair_run(conn, original), slots_api=slots_api, cls=job.RepairJob)
    assert got.outcome == "relaunched" and got.requests[0].director is False


def test_a_repair_refuses_a_slot_running_the_chains_session(conn):
    original = failed_run(conn)
    slots_api = FakeSlots(shown=shown("occupied", chain="r1"))
    got = go(conn, repair_run(conn, original), slots_api=slots_api, cls=job.RepairJob)
    assert got.outcome == "occupied-by-chain" and got.requests == []


@pytest.mark.parametrize("seen", [shown("occupied", chain="other"),
                                  shown("free", holder="someone"), None])
def test_a_repair_falls_back_only_when_the_slot_is_someone_elses(conn, seen):
    original = failed_run(conn)
    got = go(conn, repair_run(conn, original), slots_api=FakeSlots(shown=seen),
             cls=job.RepairJob)
    request = got.requests[0]
    assert request.slot == "" and request.hold is None and not request.respawn


def test_a_repair_of_a_director_run_keeps_its_posture(conn):
    original = failed_run(conn, mode="director")
    got = go(conn, repair_run(conn, original), slots_api=FakeSlots(shown=shown("free")),
             cls=job.RepairJob)
    assert got.requests[0].posture == "with:boss" and got.requests[0].handoff is None


def test_repairable_runs():
    def run(outcome, mode="self"):
        return SimpleNamespace(kind="relaunch", open=False, outcome=outcome, mode=mode)

    acted = [SimpleNamespace(kind="exit")]
    assert job.repairable(run("relaunch-failed"), [])
    assert job.repairable(run("gone"), [])
    assert not job.repairable(run("error"), [])
    assert job.repairable(run("error"), acted)
    assert job.repairable(run("error", "repair"), [])
    assert not job.repairable(run("first-turn-not-sent"), acted)
    assert not job.repairable(run("relaunched"), acted)
