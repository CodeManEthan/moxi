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
from moxi.config import Settings
from moxi.paths import Layout

from moxi_launcher import job, notices, settings, store
from moxi_launcher import job as job_module

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


def facts(*, turn_open=False, number=4, draft=False, session_id=None):
    return SimpleNamespace(turn_open=turn_open, turn=None, turn_number=number, draft=draft,
                           stale=False, session_id=session_id)


class FakeSession:
    def __init__(self, exits=(Result("ran"),), facts_seq=None, *, agents_complete=True,
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

    def wait_ended(self, slot, *, pane_id, timeout_s):
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

    def answer_screen(self, slot, screen, wait_s=5):
        self.calls.append(("answer", slot, wait_s))
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


def launched_result(state="started", outcome="sent", reason=None, finding=None,
                    line=None, session_id=None):
    return SimpleNamespace(state=state, reason=reason, line=line or f"launch r2 {state}",
                           outcome=None if outcome is None else SimpleNamespace(
                               outcome=outcome), request_id="r2", base="row",
                           finding=finding, session_id=session_id)


#: A respawned session the launch flagged: held on a screen it won't answer, or not ready.
HELD_LINE = ("launch r2 started in slot 3; flagged screen-not-answered: held on Claude "
             "Code's folder-trust screen")
NOT_READY_LINE = "launch r2 started in slot 3; flagged no-ready: not ready: attach to slot 3"


class Checks:
    """The launch check stood in for: every request it is asked about, and the refusal
    it answers (None: the launch would be accepted)."""

    def __init__(self):
        self.asked, self.refusal = [], None

    def __call__(self, request, env=None):
        self.asked.append(request)
        return self.refusal


@pytest.fixture(autouse=True)
def checks(monkeypatch):
    """The launch check passes, unless a test says otherwise."""
    found = Checks()
    monkeypatch.setattr(job_module, "launch_check", found)
    return found


@pytest.fixture
def conn(tmp_path):
    conn = store.connect(tmp_path / "launcher.db")
    store.insert(conn, request_id="r1", by="ann", harness="claude", target="slot",
                 cwd="/work", values={"model": "opus", "director": ["boss"]},
                 argv=["claude"], env={}, posture="operator", session_id=SID, slot="3",
                 identity="planner")
    store.set_facts(conn, "r1", pane_id="%9")
    conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = 'r1'", (me(),))
    yield conn
    conn.close()


def arm(conn, mode="self", handoff="/work/h.md", turn_number=4):
    return store.insert_run(conn, kind="relaunch", mode=mode, request_id="r1",
                            session_id=SID, by="planner" if mode == "self" else "boss",
                            handoff_path=handoff if mode == "self" else None, slot="3",
                            pane_id="%9", turn_number=turn_number, job="slots:relaunch-x")


def go(conn, run, *, session=None, slots_api=None, comms=None, launch=None, settings=None,
       cls=job.RelaunchJob, stop=None, sessions=None, cache=None):
    session = session or FakeSession()
    slots_api = slots_api or FakeSlots(ended=True)
    seen = []

    def fake_launch(request):
        seen.append(request)
        return launch(request) if callable(launch) else (launch or launched_result())

    clock = Clock()
    if not isinstance(settings, Settings):
        settings = {**SETTINGS, **(settings or {})}
    runner = cls(conn, run.run_id, turns=FakeTurns(session), slots_api=slots_api,
                 comms=comms, sessions=sessions, cache=cache, launch=fake_launch,
                 settings=settings, clock=clock, sleep=clock.sleep)
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
    assert got.events[:5] == ["model", "hold", "exit", "exit-result", "pane-ended"]
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


class CountingSession(FakeSession):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.reads = 0

    def facts(self):
        self.reads += 1
        return super().facts()


def test_an_open_turn_is_not_read_on_the_clock_while_the_run_waits(conn, monkeypatch):
    monkeypatch.setattr(job, "POLL_S", 0.01)
    session = CountingSession(facts_seq=[facts(turn_open=True)])
    got = go(conn, arm(conn), session=session, settings={"relaunch.wait_s": 1})
    assert got.outcome == "expired" and got.run.word == "turn"
    # The watch's own facts and the read when it opened; nothing on the clock.
    assert session.reads <= 3


def test_a_draft_is_read_again_on_the_clock_while_the_run_waits(conn, monkeypatch):
    monkeypatch.setattr(job, "POLL_S", 0.01)
    session = CountingSession(facts_seq=[facts(draft=True)])
    got = go(conn, arm(conn), session=session, settings={"relaunch.wait_s": 1})
    assert got.outcome == "expired" and got.run.word == "draft"
    assert session.reads > 10


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
    comms = FakeComms()
    got = go(conn, arm(conn, mode="director"), session=session, slots_api=slots_api,
             comms=comms)
    assert got.outcome == "exit-pending" and got.run.word == "turn"
    assert "answer" not in slots_api.names()
    # The notice names the repair, for once a person has answered the dialog.
    [(_, _, _, text)] = comms.sent
    assert ("Once a person has answered the dialog and the session has exited, to launch "
            f"it again from the run's record: moxi launcher relaunch --from-record "
            f"{got.run.run_id}") in text


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


class RedrawingSlots(FakeSlots):
    """Slots whose pane shows output at every read, as tmux's window activity does while
    Claude Code redraws its spinner and status line; `client_activity` is the attached
    client's, None when no client is attached."""

    def __init__(self, client_activity=None, **kw):
        super().__init__(**kw)
        self.client_activity = client_activity
        self.uses = 0

    def pane_use(self, slot):
        self.uses += 1
        return SimpleNamespace(pane_id="%9", pane_in_mode=False,
                               client_activity=self.client_activity,
                               pane_activity=time.time())


def test_a_self_run_fires_while_the_pane_redraws(conn, monkeypatch):
    monkeypatch.setattr(job, "POLL_S", 0.01)
    slots_api = RedrawingSlots(ended=True)
    got = go(conn, arm(conn), slots_api=slots_api, settings={"relaunch.wait_s": 2})
    assert got.outcome == "relaunched" and got.session.commands == [("exit", 4)]
    assert "wait" not in got.events and "exit" in got.events
    assert slots_api.uses >= 1


def test_output_before_the_exit_is_not_a_person_at_the_dialog(conn):
    pane = SimpleNamespace(pane_id="%9", pane_in_mode=False, client_activity=None,
                           pane_activity=time.time() - 5)
    slots_api = FakeSlots(ended=False, pane=pane)
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "relaunched"
    assert ("answer", "3", 0) in slots_api.calls


def test_a_client_at_a_redrawing_dialog_holds(conn):
    slots_api = RedrawingSlots(client_activity=time.time() + 3600, ended=False)
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "exit-pending" and got.run.word == "person"
    assert "a client was active" in got.run.detail
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


def test_a_self_run_held_on_a_screen_ends_first_turn_not_sent_naming_it(conn):
    comms = FakeComms()
    got = go(conn, arm(conn), comms=comms,
             launch=launched_result(outcome=None, finding="screen-not-answered",
                                    line=HELD_LINE))
    assert got.outcome == "first-turn-not-sent" and got.run.word == "screen-not-answered"
    assert got.run.detail == HELD_LINE and got.run.new_request_id == "r2"
    assert "flagged" in got.events
    [(_module, _name, to, text)] = comms.sent
    assert to == ["boss"] and "folder-trust screen" in text and "slot 3" in text
    assert "--from-record" not in text


def test_a_self_run_whose_new_session_is_not_ready_ends_first_turn_not_sent(conn):
    comms = FakeComms()
    got = go(conn, arm(conn), comms=comms,
             launch=launched_result(outcome=None, finding="no-ready", line=NOT_READY_LINE))
    assert got.outcome == "first-turn-not-sent" and got.run.word == "no-ready"
    assert len(comms.sent) == 1


def test_a_directors_relaunch_flagged_ends_relaunched_and_tells_the_directors(conn):
    comms = FakeComms()
    got = go(conn, arm(conn, mode="director"), comms=comms,
             launch=launched_result(outcome=None, finding="no-ready", line=NOT_READY_LINE))
    assert got.outcome == "relaunched" and got.run.word == "no-ready"
    assert got.run.new_request_id == "r2" and got.run.detail == NOT_READY_LINE
    assert notices.is_flagged(got.run)
    [(_module, _name, to, text)] = comms.sent
    assert to == ["boss"] and "ended relaunched: no-ready" in text
    assert "attach to its slot" in text and "--from-record" not in text


def test_a_relaunch_with_no_finding_tells_nobody(conn):
    comms = FakeComms()
    got = go(conn, arm(conn, mode="director"), comms=comms)
    assert got.outcome == "relaunched" and got.run.word is None
    assert not notices.is_flagged(got.run) and comms.sent == []


def test_a_relaunch_whose_new_session_exited_is_still_relaunch_failed(conn):
    got = go(conn, arm(conn, mode="director"),
             launch=launched_result(state="failed", outcome=None, reason="exited"))
    assert got.outcome == "relaunch-failed" and got.run.word == "exited"


def test_a_stop_before_the_exit_ends_the_run_stopped(conn):
    got = go(conn, arm(conn), stop=True)
    assert got.outcome == "stopped" and got.session.commands == []
    assert got.session.deliveries[0].startswith(
        f"Relaunch note from launcher: run {got.run.run_id} did not run (stopped: ")


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


def test_a_run_ended_after_the_exit_does_not_respawn(conn):
    # Ended under the job once the pane has ended: the respawn, the act that matters most,
    # waits on its own check that the run is still open.
    run = arm(conn, mode="director")
    slots_api = FakeSlots(ended=True)
    original = slots_api.wait_ended

    def wait_ended(slot, **kw):
        store.end_run(conn, run.run_id, "error", word="job-gone")
        return original(slot, **kw)

    slots_api.wait_ended = wait_ended
    session = FakeSession()
    got = go(conn, run, session=session, slots_api=slots_api)
    assert session.commands == [("exit", 4)] and got.requests == []
    assert got.run.word == "job-gone" and "respawn" not in got.events
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


def real_settings(text: str = "") -> Settings:
    """The launcher's settings as the job reads them in use: `settings.current()`, which
    reads by key and has no `get`, over a config file holding `text`."""
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return settings.current()


def test_a_directors_notice_reads_the_real_settings(conn):
    comms = FakeComms(outcomes=("refused", "sent"))
    got = go(conn, arm(conn, mode="director"), comms=comms,
             settings=real_settings('[relaunch]\nfallback = "lead"\n'),
             launch=launched_result(state="failed", outcome=None, reason="spawn-failed"))
    assert got.outcome == "relaunch-failed"
    assert [s[2] for s in comms.sent] == [["boss"], ["lead"]]
    assert f"--from-record {got.run.run_id}" in comms.sent[1][3]


def test_a_self_runs_note_reads_the_real_settings(conn, checks):
    refusing(checks)
    comms = FakeComms()
    got = go(conn, arm(conn), comms=comms, settings=real_settings())
    assert got.outcome == "refused" and comms.sent == []
    assert got.session.deliveries[0].startswith("Relaunch note from launcher: run ")
    unread = FakeSession(delivered="refused")
    missed = go(conn, arm(conn), comms=comms, session=unread,
                settings=real_settings('[relaunch]\nfallback = "lead"\n'))
    assert missed.outcome == "refused" and unread.deliveries
    assert [s[2] for s in comms.sent] == [["boss"]]
    assert "note-not-taken (the run ended refused: no-cwd)" in comms.sent[0][3]


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


# -- a session left in place ------------------------------------------------------------

CLEARED = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


def notes_of(got):
    return [e.detail for e in store.run_events(got.job.conn, got.run.run_id)
            if e.kind == "note"]


@pytest.mark.parametrize("delivered", ["started", "refused"])
def test_a_self_run_not_exited_tells_its_session_and_past_it_only_when_not_taken(
        conn, delivered):
    comms = FakeComms()
    session = FakeSession([Result("refused", "not-allowed")], delivered=delivered)
    got = go(conn, arm(conn), session=session, comms=comms)
    assert got.outcome == "not-exited" and got.run.word == "not-allowed"
    assert len(session.deliveries) == 1
    assert ("The relaunch did not exit this session (not-allowed): carry on with your "
            "work") in session.deliveries[0]
    if delivered == "started":
        assert comms.sent == []
    else:
        assert [s[2] for s in comms.sent] == [["boss"]]
        assert "note-not-taken (the run ended not-exited: not-allowed)" in comms.sent[0][3]


def test_a_self_run_with_no_dialog_tells_its_session(conn):
    comms = FakeComms()
    session = FakeSession()
    got = go(conn, arm(conn), session=session, comms=comms,
             slots_api=FakeSlots(screens=(None,)), settings={"relaunch.dialog_s": 0})
    assert got.outcome == "not-exited" and got.run.word == "no-dialog"
    assert "did not exit this session (no-dialog)" in session.deliveries[0]
    assert comms.sent == []


def test_a_director_run_not_exited_still_tells_its_directors(conn):
    comms = FakeComms()
    session = FakeSession([Result("refused", "not-allowed")])
    got = go(conn, arm(conn, mode="director"), session=session, comms=comms)
    assert got.outcome == "not-exited"
    assert session.deliveries == [] and [s[2] for s in comms.sent] == [["boss"]]


@pytest.mark.parametrize("delivered", ["started", "refused"])
def test_a_self_run_error_before_the_exit_tells_its_session(conn, delivered):
    comms = FakeComms()
    session = FakeSession(delivered=delivered)
    got = go(conn, arm(conn), session=session, comms=comms,
             slots_api=FakeSlots(hold=RuntimeError("slots broke")))
    assert got.outcome == "error" and "exit" not in got.events
    assert session.commands == []
    assert ("The relaunch stopped with an error before the exit: carry on with your work."
            in session.deliveries[0])
    if delivered == "started":
        assert comms.sent == []
    else:
        assert "note-not-taken (the run ended error: RuntimeError)" in comms.sent[0][3]


def test_a_self_run_error_after_the_exit_tells_its_directors_and_no_note(conn):
    comms = FakeComms()
    slots_api = FakeSlots()

    def wait_ended(slot, **kw):
        raise RuntimeError("slots broke")

    slots_api.wait_ended = wait_ended
    session = FakeSession()
    got = go(conn, arm(conn), session=session, comms=comms, slots_api=slots_api)
    assert got.outcome == "error" and "exit" in got.events
    assert session.deliveries == []
    assert [s[2] for s in comms.sent] == [["boss"]]
    assert "ended error: RuntimeError" in comms.sent[0][3]


def test_a_self_run_after_a_clear_ends_changed_with_a_note_and_nothing_touched(conn):
    comms = FakeComms()
    session = FakeSession(facts_seq=[facts(session_id=CLEARED)])
    got = go(conn, arm(conn), session=session, comms=comms)
    assert got.outcome == "changed" and got.run.word == "changed"
    assert CLEARED in got.run.detail
    assert "the conversation that armed the run is gone" in got.run.detail
    assert session.commands == [] and got.slots.calls == [] and got.requests == []
    assert "Nothing was exited" in session.deliveries[0]
    assert notes_of(got)[0]["verdict"] == "taken" and comms.sent == []


def test_a_self_run_after_a_clear_whose_note_is_not_taken_tells_the_directors(conn):
    comms = FakeComms()
    session = FakeSession(facts_seq=[facts(session_id=CLEARED)], delivered="refused")
    got = go(conn, arm(conn), session=session, comms=comms)
    assert got.outcome == "changed"
    assert "note-not-taken (the run ended changed: changed)" in comms.sent[0][3]


def test_a_director_run_after_a_clear_ends_changed_and_tells_nobody(conn):
    comms = FakeComms()
    session = FakeSession(facts_seq=[facts(session_id=CLEARED)])
    got = go(conn, arm(conn, mode="director"), session=session, comms=comms)
    assert got.outcome == "changed" and session.commands == []
    assert session.deliveries == [] and comms.sent == []


def test_the_same_conversation_goes_on(conn):
    got = go(conn, arm(conn), session=FakeSession(facts_seq=[facts(session_id=SID)]))
    assert got.outcome == "relaunched"


def test_only_a_self_run_owes_a_note(conn):
    run = store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                           session_id=SID, by="planner", handoff_path="/work/h.md",
                           slot="3", pane_id="%9", turn_number=4, job="slots:relaunch-x")
    store.end_run(conn, run.run_id, "error", word="boom")
    assert notices.owes_note(store.get_run(conn, run.run_id), "error", [])
    repair = SimpleNamespace(mode="repair")
    assert not notices.owes_note(repair, "error", [])
    assert not notices.owes_note(repair, "not-exited", [])


# -- repair ------------------------------------------------------------------------------


def failed_run(conn, mode="self"):
    run = arm(conn, mode=mode)
    store.add_run_event(conn, run.run_id, "exit", {"turn_number": 4})
    store.end_run(conn, run.run_id, "relaunch-failed", word="spawn-failed")
    return store.get_run(conn, run.run_id)


def repair_run(conn, original):
    return store.insert_run(conn, kind="relaunch", mode="repair", request_id="r1",
                            session_id=SID, by="ann", slot="3", repair_of=original.run_id,
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


def test_a_repair_asks_again_when_slots_dont_answer_about_the_hold(conn):
    original = failed_run(conn)
    slots_api = FakeSlots(shown=shown("ended", holder=original.run_id))
    plain, tries = slots_api.hold, []

    def hold(slot, **kw):
        tries.append(slot)
        if len(tries) < 3:
            raise FakeSlots.TryAgain("locked")
        return plain(slot, **kw)

    slots_api.hold = hold
    got = go(conn, repair_run(conn, original), slots_api=slots_api, cls=job.RepairJob)
    assert got.outcome == "relaunched" and got.requests[0].slot == "3"
    assert got.requests[0].hold == original.run_id and len(tries) == 3


def test_a_repair_slots_never_answer_about_ends_with_nothing_launched(conn):
    original = failed_run(conn)
    slots_api = FakeSlots(shown=shown("ended", holder=original.run_id),
                          hold=FakeSlots.TryAgain("locked"))
    got = go(conn, repair_run(conn, original), slots_api=slots_api, cls=job.RepairJob)
    assert (got.outcome, got.run.word) == ("relaunch-failed", "slots-busy")
    assert got.requests == []
    assert slots_api.names().count("hold") == job.HOLD_TRIES
    assert job.repairable(got.run, store.run_events(conn, got.run.run_id))


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


def test_a_repair_flagged_by_its_launch_tells_the_directors(conn):
    original = failed_run(conn, mode="director")
    comms = FakeComms()
    got = go(conn, repair_run(conn, original), slots_api=FakeSlots(shown=shown("free")),
             cls=job.RepairJob, comms=comms,
             launch=launched_result(outcome=None, finding="screen-not-answered",
                                    line=HELD_LINE))
    assert got.outcome == "relaunched" and got.run.word == "screen-not-answered"
    assert [s[2] for s in comms.sent] == [["boss"]]


# -- the model pin ------------------------------------------------------------------------


class PinSessions:
    """sessions' `find` and `records_back`, the newest request naming each model of
    `models` in turn, the last one from then on."""

    def __init__(self, *models):
        self.models, self.reads = list(models), 0

    def find(self, harness, session_id, *, root=None):
        return SimpleNamespace(hits=(SimpleNamespace(source="s0", compressed=False),))

    def records_back(self, source, *, kinds=None):
        self.reads += 1
        model = self.models[0] if len(self.models) == 1 else self.models.pop(0)
        return iter([SimpleNamespace(model=model, response_model=None, time=1.0)])


def no_model(conn):
    conn.execute("UPDATE launches SET values_json = ? WHERE request_id = 'r1'",
                 ('{"director": ["boss"]}',))


def model_events(conn, run):
    return [e.detail for e in store.run_events(conn, run.run_id) if e.kind == "model"]


def test_a_row_naming_a_model_relaunches_on_it_with_no_pin(conn):
    run = arm(conn)
    got = go(conn, run, sessions=PinSessions("claude-opus-5-5"))
    assert model_events(conn, run) == [{"model": "opus", "how": "row", "read": None}]
    assert got.requests[0].model_pin is None


def test_a_row_naming_no_model_relaunches_on_the_pin(conn):
    no_model(conn)
    run = arm(conn, mode="director")
    got = go(conn, run, sessions=PinSessions("claude-fable-5-2"))
    assert got.requests[0].model_pin == "claude-fable-5-2"
    assert model_events(conn, run) == [{"model": "claude-fable-5-2", "how": "pinned",
                                        "read": "claude-fable-5-2"}]


def test_the_pin_is_read_on_each_pass_and_written_once_per_change(conn):
    no_model(conn)
    run = arm(conn)
    session = FakeSession([Result("refused", "busy-turn"), Result("refused", "busy-turn"),
                           Result("ran")])
    seen = PinSessions("claude-opus-5-5", "claude-opus-5-5", "claude-fable-5-2")
    got = go(conn, run, session=session, sessions=seen)
    assert got.outcome == "relaunched" and seen.reads == 3
    assert [d["model"] for d in model_events(conn, run)] == ["claude-opus-5-5",
                                                             "claude-fable-5-2"]
    assert got.requests[0].model_pin == "claude-fable-5-2"


def test_a_default_pin_replays_with_no_model(conn):
    no_model(conn)
    run = arm(conn, mode="director")
    got = go(conn, run, sessions=None)
    assert got.requests[0].model_pin is None
    assert model_events(conn, run)[0]["how"] == "default:no-sessions"


def test_a_repair_reuses_the_root_runs_pin(conn):
    no_model(conn)
    original = failed_run(conn)
    store.add_run_event(conn, original.run_id, "model",
                        {"model": "claude-fable-5-2", "how": "pinned",
                         "read": "claude-fable-5-2"})
    seen = PinSessions("claude-opus-5-5")
    repair = repair_run(conn, original)
    got = go(conn, repair, slots_api=FakeSlots(shown=shown("free")), cls=job.RepairJob,
             sessions=seen)
    assert got.requests[0].model_pin == "claude-fable-5-2" and seen.reads == 0
    assert model_events(conn, repair)[0]["model"] == "claude-fable-5-2"


def test_a_repair_whose_root_run_wrote_no_pin_reads_the_transcript(conn):
    no_model(conn)
    original = failed_run(conn)
    got = go(conn, repair_run(conn, original), slots_api=FakeSlots(shown=shown("free")),
             cls=job.RepairJob, sessions=PinSessions("claude-opus-5-5"))
    assert got.requests[0].model_pin == "claude-opus-5-5"


# -- the launch check ---------------------------------------------------------------------


def refusing(checks):
    checks.refusal = SimpleNamespace(code="no-cwd", problem="the directory /work does not "
                                     "exist", hint="make it")


def test_the_job_checks_its_launch_under_its_hold_before_the_exit(conn, checks):
    run = arm(conn)
    got = go(conn, run)
    assert got.outcome == "relaunched"
    asked = checks.asked[0]
    assert asked.replacing == SID and asked.hold == run.run_id and asked.respawn
    assert got.events.index("hold") < got.events.index("exit")


def test_a_self_run_whose_launch_would_be_refused_ends_refused_and_is_noted(conn, checks):
    refusing(checks)
    comms = FakeComms()
    got = go(conn, arm(conn), comms=comms)
    assert (got.outcome, got.run.word) == ("refused", "no-cwd")
    assert "exit" not in got.events and got.session.commands == []
    assert got.session.deliveries[0].startswith("Relaunch note from launcher: run ")
    assert comms.sent == [] and "release" in got.slots.names()


def test_a_directors_run_whose_launch_would_be_refused_tells_the_directors(conn, checks):
    refusing(checks)
    comms = FakeComms()
    got = go(conn, arm(conn, mode="director"), comms=comms)
    assert (got.outcome, got.run.word) == ("refused", "no-cwd")
    assert "exit" not in got.events and got.session.deliveries == []
    assert [s[2] for s in comms.sent] == [["boss"]]
    assert "ended refused: no-cwd" in comms.sent[0][3]


class Warm:
    def __init__(self, kind):
        self.kind = kind

    def read(self, harness, session_id, **kw):
        return SimpleNamespace(kind=self.kind)


def test_a_directors_run_reads_the_cache_again_after_its_hold(conn):
    got = go(conn, arm(conn, mode="director"), cache=Warm("warm"))
    assert (got.outcome, got.run.word) == ("held", "warm")
    assert "exit" not in got.events and "hold" in got.events
    cold = go(conn, arm(conn, mode="director"), cache=Warm("cold"))
    assert cold.outcome == "relaunched"


def test_a_self_run_does_not_read_the_cache(conn):
    got = go(conn, arm(conn), cache=Warm("warm"))
    assert got.outcome == "relaunched"


def test_a_repair_whose_launch_would_be_refused_launches_nothing(conn, checks):
    original = failed_run(conn)
    refusing(checks)
    comms = FakeComms()
    got = go(conn, repair_run(conn, original), slots_api=FakeSlots(shown=shown("free")),
             cls=job.RepairJob, comms=comms)
    assert (got.outcome, got.run.word) == ("refused", "no-cwd")
    assert got.requests == [] and checks.asked[0].replacing is None
    assert [s[2] for s in comms.sent] == [["boss"]]
    said = comms.sent[0][3]
    assert "Nothing was exited" not in said
    assert ("The session is down and nothing was launched: the repair's launch would have "
            "been refused.") in said
    assert said.endswith("Once the launch's problem is fixed, to try the repair again: moxi "
                         f"launcher relaunch --from-record {original.run_id}")


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


# -- turns' pace, the dead hold and what came back ---------------------------------------


def again_events(conn, run):
    return [e.detail for e in store.run_events(conn, run.run_id) if e.kind == "again"]


@pytest.mark.parametrize("reason", job.PACED)
def test_a_refusal_of_turns_pace_holds_the_self_run_out_of_the_exit(conn, monkeypatch,
                                                                   reason):
    monkeypatch.setattr(job, "POLL_S", 0.01)
    monkeypatch.setattr(job, "PACE_HOLD_OFF_S", 0.3)
    session = FakeSession([Result("refused", reason), Result("ran")])
    started = time.time()
    run = arm(conn)
    got = go(conn, run, session=session)
    assert got.outcome == "relaunched" and session.commands == [("exit", 4), ("exit", 4)]
    [again] = again_events(conn, run)
    assert again["word"] == reason and "act_after" in again
    act_after = store.parse_time(again["act_after"]).timestamp()
    assert started < act_after
    # The wait held as `rate` until then, and the second exit came after it.
    waits = [e.detail for e in store.run_events(conn, run.run_id) if e.kind == "wait"]
    assert waits and waits[-1]["word"] == "rate"
    assert "turns' pace holds the exit until" in waits[-1]["detail"]
    assert time.time() >= act_after
    # And the run paused before its wait.
    assert 0.01 in got.job.sleep.__self__.slept


def test_any_other_refusal_pauses_and_holds_nothing(conn, monkeypatch):
    monkeypatch.setattr(job, "POLL_S", 0.01)
    run = arm(conn)
    got = go(conn, run, session=FakeSession([Result("refused", "busy-turn"), Result("ran")]))
    assert got.outcome == "relaunched"
    [again] = again_events(conn, run)
    assert "act_after" not in again and got.job.act_after is None
    assert 0.01 in got.job.sleep.__self__.slept


def test_a_wait_that_ends_inside_turns_pace_expires_as_rate(conn, monkeypatch):
    monkeypatch.setattr(job, "POLL_S", 0.01)
    session = FakeSession([Result("refused", "rate"), Result("ran")])
    got = go(conn, arm(conn), session=session, settings={"relaunch.wait_s": 1})
    assert got.outcome == "expired" and got.run.word == "rate"
    assert session.commands == [("exit", 4)]


def hold_events(conn, run):
    return [e.detail for e in store.run_events(conn, run.run_id) if e.kind == "hold"]


def test_a_run_holds_its_slot_for_core_s_510_s(conn):
    run = arm(conn)
    go(conn, run)
    assert [h["seconds"] for h in hold_events(conn, run)] == [510.0]


class OnceHeldSlots(FakeSlots):
    """A slot held by `holder` until that hold is released (or refused `times` times)."""

    def __init__(self, holder, *, times=None, **kw):
        super().__init__(ended=True, **kw)
        self.holder, self.times = holder, times

    def hold(self, slot, *, holder, seconds):
        self.calls.append(("hold", slot, holder))
        if self.holder is not None and (self.times is None or self.times > 0):
            if self.times is not None:
                self.times -= 1
            raise self.Refused("held", f"slot {slot} is held by {self.holder}",
                               holder={"holder": self.holder, "until": 0})

    def release(self, slot, *, holder):
        self.calls.append(("release", slot, holder))
        if holder == self.holder:
            self.holder = None
            return True
        return False


def dead_run(conn, request_id="r1", *, ended=True):
    old = store.insert_run(conn, kind="relaunch", mode="self", request_id=request_id,
                           session_id=SID, by="planner", slot="3", job="slots:relaunch-old",
                           at="2026-01-01T00:00:00.000000Z")
    if ended:
        store.end_run(conn, old.run_id, *store.JOB_GONE[:1], word=store.JOB_GONE[1])
    return old


def test_a_hold_left_by_a_dead_run_of_the_launch_is_released_and_taken_again(conn):
    old = dead_run(conn)
    slots_api = OnceHeldSlots(old.run_id)
    run = arm(conn)
    got = go(conn, run, slots_api=slots_api)
    assert got.outcome == "relaunched"
    assert ("release", "3", old.run_id) in slots_api.calls
    assert slots_api.names()[:3] == ["hold", "release", "hold"]
    [released] = [e.detail for e in store.run_events(conn, run.run_id)
                  if e.kind == "released-dead"]
    assert released == {"slot": "3", "holder": old.run_id, "released": True}


def test_a_dead_hold_is_released_once_only(conn):
    old = dead_run(conn)

    class Stubborn(OnceHeldSlots):
        def release(self, slot, *, holder):
            self.calls.append(("release", slot, holder))
            return False

    slots_api = Stubborn(old.run_id)
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "not-held" and got.run.word == "held"
    assert slots_api.names().count("hold") == 2


def test_a_hold_by_a_run_of_another_launch_is_not_released(conn):
    store.insert(conn, request_id="r9", by="ann", harness="claude", target="slot",
                 cwd="/work", values={}, argv=["claude"], env={}, posture="operator",
                 session_id="other-sid", slot="3", identity="other")
    old = dead_run(conn, "r9")
    slots_api = OnceHeldSlots(old.run_id)
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "not-held" and "release" not in slots_api.names()[:2]
    assert ("release", "3", old.run_id) not in slots_api.calls


def test_a_hold_by_a_run_whose_job_still_runs_is_not_released(conn):
    old = dead_run(conn)

    class Running(OnceHeldSlots):
        def job(self, name):
            return SimpleNamespace(name=name, running=True)

    slots_api = Running(old.run_id)
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "not-held"
    assert ("release", "3", old.run_id) not in slots_api.calls


def test_a_hold_by_no_run_is_not_released(conn):
    slots_api = OnceHeldSlots("someone-else")
    got = go(conn, arm(conn, mode="director"), slots_api=slots_api)
    assert got.outcome == "not-held"
    assert ("release", "3", "someone-else") not in slots_api.calls


def ended_detail(conn, run):
    [ended] = [e.detail for e in store.run_events(conn, run.run_id) if e.kind == "ended"]
    return ended


class BindingComms(FakeComms):
    """comms whose name is free at the name step, then `after` once the launch ran."""

    def __init__(self, after):
        super().__init__()
        self.after, self.launched = after, False

    def identity_state(self, name, scope=None):
        return SimpleNamespace(state=self.after if self.launched else "free")


@pytest.mark.parametrize("state", ["held", "free"])
def test_a_relaunch_records_what_came_back(conn, state):
    run = arm(conn, mode="director")
    comms = BindingComms(state)

    def launch(request):
        comms.launched = True
        return launched_result(session_id="new-sid")

    got = go(conn, run, comms=comms, launch=launch)
    assert got.outcome == "relaunched"
    detail = ended_detail(conn, run)
    assert {k: detail[k] for k in ("session_id", "identity", "posture", "model",
                                    "model_how", "identity_state")} == {
        "session_id": "new-sid", "identity": "planner", "posture": "with:boss",
        "model": "opus", "model_how": "row", "identity_state": state}


def test_a_relaunch_without_comms_records_no_identity_state(conn):
    run = arm(conn)
    got = go(conn, run)
    assert got.outcome == "relaunched"
    detail = ended_detail(conn, run)
    assert detail["identity_state"] is None
    assert detail["posture"] == "relaunched (handoff: /work/h.md)"
