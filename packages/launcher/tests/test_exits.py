"""exit-session against fakes of turns and slots: each refusal (logged once the launch is
found, with no run), the run that holds the launch, the quiet read's hold, every result of
the exit and the dialog mapped to a verdict, the dialog outside a slot, one exit-log row
per exit, and the exit log."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from moxi import proc

from moxi_launcher import exits, first, job, store, target

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
OTHER = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
SETTINGS = {"relaunch.person_s": 120, "relaunch.dialog_s": 60, "records.keep_days": 90}


def me() -> str:
    ident = proc.identity(os.getpid())
    return f"{ident.pid}@{ident.started:.6f}"


class Result:
    def __init__(self, kind, reason=None):
        self.KIND, self.reason = kind, reason


def facts(turn_open=False, number=4, draft=False, stale=False):
    return SimpleNamespace(turn_open=turn_open, turn=None, turn_number=number, draft=draft,
                           stale=stale)


class FakeSession:
    def __init__(self, *, exits=(Result("ran"),), facts_seq=None, capabilities=("exit",),
                 agents=()):
        self.session_id, self.endpoint_pid, self.pid = SID, os.getpid(), os.getppid()
        self.capabilities = frozenset(capabilities)
        self.exits = list(exits)
        self.facts_seq = list(facts_seq or [facts()])
        self.entries = list(agents)
        self.commands, self.closed = [], 0

    def facts(self):
        value = self.facts_seq[0] if len(self.facts_seq) == 1 else self.facts_seq.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def agents(self):
        return SimpleNamespace(complete=True, entries=self.entries)

    def command(self, name, *, if_turn_number=None):
        self.commands.append((name, if_turn_number))
        result = self.exits[0] if len(self.exits) == 1 else self.exits.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

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

    CLAUDE_EXIT_DIALOG = SimpleNamespace(name="CLAUDE_EXIT_DIALOG")

    def __init__(self, *, ok=True, ended=False, screens=("dialog",), answer="ended", why="",
                 pane=None):
        self.ok, self.ended, self.screens = ok, ended, list(screens)
        self.answer, self.why = answer, why
        self.pane = pane or SimpleNamespace(pane_id="%9", pane_in_mode=False,
                                            client_activity=None, pane_activity=None)
        self.calls = []

    def supported(self):
        return SimpleNamespace(ok=self.ok, why="")

    def show(self, slot):
        return SimpleNamespace(state="occupied", hold=None, created=1.0)

    def wait_ended(self, slot, *, pane_id, timeout):
        self.calls.append("wait_ended")
        return SimpleNamespace(ended=self.ended, gone=False, dead_status=None)

    def read_screen(self, slot, screens):
        name = self.screens[0] if len(self.screens) == 1 else self.screens.pop(0)
        screen = self.CLAUDE_EXIT_DIALOG if name == "dialog" else None
        return SimpleNamespace(screen=screen, full=True, dead=False)

    def pane_use(self, slot):
        return self.pane

    def answer_screen(self, slot, screen, wait=5):
        self.calls.append(("answer", wait))
        return SimpleNamespace(outcome=self.answer, why=self.why, capture=None)

    def job(self, name):
        return None


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1.0
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    launched(connection)
    yield connection
    connection.close()


def launched(conn, *, request_id="r1", target_name="slot", session_id=SID, claimed=True,
             harness="claude"):
    store.insert(conn, request_id=request_id, actor="ann", harness=harness,
                 target=target_name, cwd="/work", values={"model": "opus"}, argv=["claude"],
                 env={}, posture="operator", session_id=session_id,
                 slot="3" if target_name == "slot" else None)
    store.set_facts(conn, request_id, pane_id="%9")
    if claimed:
        conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = ?",
                     (me(), request_id))


def go(conn, which=SID, *, session=None, slots_api=None, turns=None, **kw):
    clock = Clock()
    return exits.exit_session(
        which, conn=conn, turns=turns or FakeTurns(session or FakeSession()),
        slots_api=slots_api if slots_api is not None else FakeSlots(ended=True),
        settings=SETTINGS, environ={**os.environ, first.COMMS_NAME: "ann"}, clock=clock,
        wall=lambda: 1e9, sleep=clock.sleep, **kw)


def logged(conn):
    return store.exits_log(conn)


# -- exited -------------------------------------------------------------------------------


def test_a_slot_session_exits_through_turns_and_its_pane_ending(conn):
    session, slots_api = FakeSession(), FakeSlots(ended=True)
    result = go(conn, session=session, slots_api=slots_api)
    assert result.exit_code == 0 and result.data["outcome"] == "exited"
    assert session.commands == [("exit", 4)]
    run = result.run
    assert (run.kind, run.mode, run.state, run.outcome) == ("exit", "verb", "ended", "exited")
    assert run.turn_number == 4 and run.actor == "ann" and run.slot == "3"
    assert run.job == f"proc:{me()}"
    [row] = logged(conn)
    assert (row.run_id, row.verdict, row.slot, row.pane_id) == (run.run_id, "exited", "3",
                                                                "%9")
    kinds = [e.kind for e in store.run_events(conn, run.run_id)]
    assert kinds[:3] == ["exit", "exit-result", "pane-ended"] and kinds[-1] == "ended"


def test_the_exit_dialog_is_answered_after_two_full_reads(conn):
    slots_api = FakeSlots(ended=False, answer="ended")
    result = go(conn, session=FakeSession(exits=(Result("failed", "not-exited"),)),
                slots_api=slots_api)
    assert result.data["outcome"] == "exited"
    assert ("answer", 0) in slots_api.calls


@pytest.mark.parametrize("answer", ["ended-without-screen"])
def test_an_answer_that_ended_the_pane_is_an_exit(conn, answer):
    assert go(conn, slots_api=FakeSlots(answer=answer)).data["outcome"] == "exited"


# -- held -----------------------------------------------------------------------------------


@pytest.mark.parametrize("session, word", [
    (FakeSession(facts_seq=[facts(turn_open=True)]), "turn"),
    (FakeSession(facts_seq=[facts(draft=True)]), "draft"),
    (FakeSession(agents=[SimpleNamespace(kind="subagent", state="running", final=False)]),
     "subagents"),
    (FakeSession(facts_seq=[RuntimeError("no answer")]), "unreadable"),
])
def test_a_session_not_quiet_is_held_and_not_asked_to_exit(conn, session, word):
    result = go(conn, session=session)
    assert result.exit_code == 75 and result.data["outcome"] == "held"
    assert result.run.word == word and session.commands == []
    [row] = logged(conn)
    assert (row.verdict, row.word) == ("held", word)


def test_a_person_at_the_pane_holds(conn):
    pane = SimpleNamespace(pane_id="%9", pane_in_mode=True, client_activity=None,
                           pane_activity=None)
    result = go(conn, slots_api=FakeSlots(pane=pane))
    assert (result.data["outcome"], result.run.word) == ("held", "person")


@pytest.mark.parametrize("reason", ["busy-turn", "draft", "busy-agents", "stale-turn",
                                    "rate", "in-flight", "loading"])
def test_a_refusal_of_the_exit_that_clears_by_itself_is_held(conn, reason):
    result = go(conn, session=FakeSession(exits=(Result("refused", reason),)))
    assert result.exit_code == 75 and (result.data["outcome"], result.run.word) == (
        "held", reason)
    assert logged(conn)[0].verdict == "held"


# -- not exited, at the dialog --------------------------------------------------------------


@pytest.mark.parametrize("reason", ["not-allowed", "unsupported"])
def test_a_terminal_refusal_of_the_exit_is_not_exited(conn, reason):
    result = go(conn, session=FakeSession(exits=(Result("refused", reason),)))
    assert result.exit_code == 1 and (result.data["outcome"], result.run.word) == (
        "not-exited", reason)


@pytest.mark.parametrize("answer, word", [("mismatch", "mismatch"),
                                          ("not-ended", "not-ended"),
                                          ("gone", "gone"), ("no-pane", "no-pane")])
def test_an_answer_that_did_not_end_the_pane_is_not_exited(conn, answer, word):
    result = go(conn, slots_api=FakeSlots(answer=answer, why="the screen changed"))
    assert result.exit_code == 1 and result.data["outcome"] == "not-exited"
    assert result.run.word == word
    assert logged(conn)[0].verdict == "not-exited"


def test_no_dialog_and_no_end_by_the_bound_is_not_exited(conn):
    result = go(conn, slots_api=FakeSlots(screens=("none",)))
    assert (result.data["outcome"], result.run.word) == ("not-exited", "no-dialog")


@pytest.mark.parametrize("seq, word", [
    ([facts(), facts(turn_open=True)], "turn"),
    ([facts(), facts(number=5)], "turn"),
])
def test_a_check_that_fails_at_the_dialog_leaves_it_for_a_person(conn, seq, word):
    slots_api = FakeSlots()
    result = go(conn, session=FakeSession(facts_seq=seq), slots_api=slots_api)
    assert result.exit_code == 1 and result.data["outcome"] == "at-dialog"
    assert result.run.word == word
    assert not [c for c in slots_api.calls if c != "wait_ended"]
    assert logged(conn)[0].verdict == "at-dialog"


@pytest.mark.parametrize("harness_result", [Result("failed", "not-exited"),
                                            Result("refused", "gone"),
                                            RuntimeError("endpoint gone")])
def test_outside_a_slot_an_exit_turns_did_not_see_through_is_at_the_dialog(
        tmp_path, harness_result):
    connection = store.connect(tmp_path / "launcher.db")
    launched(connection, target_name="terminal")
    try:
        slots_api = FakeSlots()
        result = go(connection, session=FakeSession(exits=(harness_result,)),
                    slots_api=slots_api)
        assert result.exit_code == 1 and result.data["outcome"] == "at-dialog"
        assert "a person answers it" in result.run.detail
        assert slots_api.calls == []
        assert logged(connection)[0].verdict == "at-dialog"
    finally:
        connection.close()


def test_outside_a_slot_an_exit_turns_saw_through_is_exited(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    launched(connection, target_name="terminal")
    try:
        assert go(connection).data["outcome"] == "exited"
    finally:
        connection.close()


def test_a_slot_launch_where_slots_does_not_run_is_treated_as_outside_a_slot(conn):
    slots_api = FakeSlots(ok=False)
    result = go(conn, session=FakeSession(exits=(Result("failed", "not-exited"),)),
                slots_api=slots_api)
    assert result.data["outcome"] == "at-dialog" and slots_api.calls == []


def test_a_fault_ends_the_run_and_logs_it_not_exited(conn):
    class Broken(FakeSlots):
        def wait_ended(self, slot, *, pane_id, timeout):
            raise ZeroDivisionError("bug")

    result = go(conn, slots_api=Broken())
    assert result.exit_code == 1 and result.run.outcome == "error"
    assert result.data["outcome"] == "not-exited"
    [row] = logged(conn)
    assert (row.verdict, row.word) == ("not-exited", "ZeroDivisionError")


def test_a_ctrl_c_ends_the_run_logs_it_and_goes_on(conn):
    with pytest.raises(KeyboardInterrupt):
        go(conn, session=FakeSession(exits=(KeyboardInterrupt(),)))
    [run] = store.runs(conn)
    assert (run.state, run.outcome) == ("ended", "error")
    assert logged(conn)[0].verdict == "not-exited"


# -- refused --------------------------------------------------------------------------------


def test_a_session_no_launch_holds_is_not_launched_and_logged_nowhere(conn):
    with pytest.raises(target.NotLaunched):
        go(conn, OTHER)
    assert logged(conn) == [] and store.runs(conn) == []


def test_an_unclaimed_launch_is_refused_and_logged_without_a_run(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    launched(connection, claimed=False)
    try:
        with pytest.raises(target.Unclaimed):
            go(connection)
        [row] = logged(connection)
        assert (row.verdict, row.word, row.run_id) == ("refused", "unclaimed", None)
        assert store.runs(connection) == []
    finally:
        connection.close()


def test_a_session_not_live_is_refused(conn):
    with pytest.raises(exits.Refused) as caught:
        go(conn, turns=SimpleNamespace(find=lambda **kw: None))
    assert caught.value.code == "not-live" and caught.value.exit_code == 3
    assert [(r.verdict, r.word) for r in logged(conn)] == [("refused", "not-live")]


def test_a_harness_that_offers_no_exit_is_refused(conn):
    session = FakeSession(capabilities=("compact",))
    with pytest.raises(exits.Refused) as caught:
        go(conn, session=session)
    assert caught.value.code == "no-exit" and session.commands == []
    assert logged(conn)[0].word == "no-exit"


def test_an_exit_loses_at_insert_to_an_open_run_whose_job_lives(conn):
    other = store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1",
                             session_id=SID, actor="cache", job=f"proc:{me()}")
    session = FakeSession()
    with pytest.raises(store.RunOpen):
        go(conn, session=session)
    assert session.commands == []
    assert [(r.verdict, r.word, r.run_id) for r in logged(conn)] == [
        ("refused", "run-open", None)]
    assert store.get_run(conn, other.run_id).open


def test_an_exits_run_makes_every_other_act_lose_at_insert(conn):
    """While an exit runs, a keep-warm ask, an expiry clear or a relaunch arm can't start."""
    seen = {}

    class Watching(FakeSession):
        def command(self, name, *, if_turn_number=None):
            for kind, mode in (("keep-warm", "ask"), ("clear", "expiry"),
                               ("relaunch", "self")):
                try:
                    store.insert_run(conn, kind=kind, mode=mode, request_id="r1",
                                     session_id=SID, actor="x", job="proc:1@1.0",
                                     gone=lambda j: True)
                except store.RunOpen as error:
                    seen[kind] = error.run.kind
            return super().command(name, if_turn_number=if_turn_number)

    assert go(conn, session=Watching()).data["outcome"] == "exited"
    assert seen == {"keep-warm": "exit", "clear": "exit", "relaunch": "exit"}


def test_an_open_run_whose_job_is_gone_is_ended_and_the_exit_goes_on(conn):
    stale = store.insert_run(conn, kind="keep-warm", mode="ask", request_id="r1",
                             session_id=SID, actor="cache", job="proc:999999@1.0",
                             at="2026-01-01T00:00:00.000000Z")
    assert go(conn).data["outcome"] == "exited"
    assert store.get_run(conn, stale.run_id).outcome == "error"


def test_a_dry_run_reads_and_writes_nothing(conn):
    session = FakeSession()
    result = go(conn, session=session, dry_run=True)
    assert result.exit_code == 0 and result.data["outcome"] == "dry-run"
    assert "exit dialog in slot 3" in result.lines[0]
    assert session.commands == [] and store.runs(conn) == [] and logged(conn) == []


def test_a_dry_run_says_what_holds_and_writes_nothing(conn):
    result = go(conn, session=FakeSession(facts_seq=[facts(turn_open=True)]), dry_run=True)
    assert result.exit_code == 75 and result.data["word"] == "turn"
    assert store.runs(conn) == [] and logged(conn) == []


def test_a_dry_run_refusal_is_logged_nowhere(conn):
    with pytest.raises(exits.Refused):
        go(conn, turns=SimpleNamespace(find=lambda **kw: None), dry_run=True)
    assert logged(conn) == []


def test_the_newest_id_after_a_clear_is_the_one_exited(conn):
    store.learn_session(conn, "r1", OTHER, "clear")
    seen = []

    class Turns(FakeTurns):
        def find(self, *, session_id=None, pid=None):
            seen.append(session_id)
            return self.session

    result = go(conn, SID, turns=Turns(FakeSession()))
    assert seen == [OTHER] and result.run.session_id == OTHER
    assert logged(conn)[0].session_id == OTHER


# -- the exit log ---------------------------------------------------------------------------


def test_the_exit_log_lists_every_verdict_newest_first(conn):
    store.add_exit(conn, request_id="r1", session_id=SID, verdict="refused", word="changed",
                   detail="another endpoint", at="2026-10-01T00:00:00.000000Z")
    store.add_exit(conn, request_id="r1", session_id=SID, verdict="exited", slot="3",
                   at="2026-10-02T00:00:00.000000Z")
    result = exits.exits(conn=conn)
    assert result.exit_code == 0
    assert result.lines[0].startswith("2026-10-02T00:00:00.000000Z exited session")
    assert "refused (changed)" in result.lines[1] and "another endpoint" in result.lines[1]
    assert [e["verdict"] for e in result.data["exits"]] == ["exited", "refused"]
    assert len(exits.exits(conn=conn, limit=1).data["exits"]) == 1
    before = exits.exits(conn=conn, before="2026-10-02T00:00:00Z").data["exits"]
    assert [e["verdict"] for e in before] == ["refused"]


def test_an_empty_exit_log(conn):
    assert exits.exits(conn=conn).lines == ("no exits",)


def test_an_exit_row_takes_only_a_verdict(conn):
    with pytest.raises(ValueError):
        store.add_exit(conn, request_id="r1", session_id=SID, verdict="maybe")


def test_retention_ages_out_exits_with_their_runs_and_those_with_none(conn):
    old = "2026-01-01T00:00:00.000000Z"
    run = store.insert_run(conn, kind="exit", mode="verb", request_id="r1", session_id=SID,
                           actor="ann", job="proc:1", at=old)
    store.end_run(conn, run.run_id, "exited", at=old)
    store.add_exit(conn, run_id=run.run_id, request_id="r1", session_id=SID,
                   verdict="exited", at=old)
    store.add_exit(conn, request_id="r1", session_id=SID, verdict="refused", at=old)
    store.add_exit(conn, request_id="r1", session_id=SID, verdict="refused")
    gone = store.prune(conn, 90, at="2026-10-31T00:00:00.000000Z")
    assert gone["exits"] == 2 and gone["runs"] == 1
    assert [r.verdict for r in logged(conn)] == ["refused"]


def test_the_exit_run_is_a_relaunch_job_whose_outcomes_are_verdicts():
    assert issubclass(exits.ExitRun, job.RelaunchJob)
    assert set(exits.EXIT_CODES) == set(store.VERDICTS)
