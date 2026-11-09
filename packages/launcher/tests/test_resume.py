"""The past-sessions list and resume: liveness from turns, then the launch's own process,
a failed read marking only its row; each reason a session is not resumable; the replay a
resume asks for; the slot it goes to (its own, the lowest, one made again, one named);
and, through `api.launch`, a resume that keeps the session id and is found by it."""

from __future__ import annotations

import json
import threading
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import proc

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import (api, catalog, first, presets, resume, slots, store, target,
                           targets)

TESTS = Path(__file__).resolve().parent
SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
SID2 = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
THREAD = "7c6d5e4f-3a2b-4c1d-9e8f-0a1b2c3d4e5f"
#: A start time no running process has, so the row's process reads ended.
ENDED = 1.0


def me() -> proc.ProcId:
    return proc.identity(os.getpid())


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    fake_turns.hide(monkeypatch)


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "launcher.db")
    yield connection
    connection.close()


def launched(conn, *, request_id="r1", harness="claude", target_name="slot", session_id=SID,
             slot="3", identity=None, live=False, pid=None, at=None, values=None):
    store.insert(conn, request_id=request_id, actor="ann", harness=harness,
                 target=target_name, cwd="/work", values=values or {"model": "opus"},
                 argv=[harness], env={}, posture="operator",
                 session_id=session_id if harness == "claude" else None,
                 slot=slot if target_name == "slot" else None, identity=identity, at=at)
    if harness == "codex" and session_id:
        store.learn_session(conn, request_id, session_id, "sender")
    started = me().started if live else ENDED
    conn.execute("UPDATE launches SET pid = ?, pid_started = ? WHERE request_id = ?",
                 (os.getpid() if pid is None else pid, started, request_id))


class Session:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class Turns:
    """turns' `find`: live for the ids in `live`, raising for the ids in `failing`."""

    def __init__(self, live=(), failing=()):
        self.live, self.failing, self.asked = set(live), set(failing), []
        self.sessions = []

    def find(self, *, session_id=None, pid=None):
        self.asked.append(session_id)
        if session_id in self.failing:
            raise OSError("the endpoint did not answer")
        if session_id in self.live:
            self.sessions.append(Session())
            return self.sessions[-1]
        return None


class Slots:
    """slots' `supported`, `show` and `create`, from a table of slots by number."""

    class SlotsError(Exception):
        pass

    class TryAgain(SlotsError):
        pass

    class Refused(SlotsError):
        pass

    def __init__(self, table=None, *, ok=True, show="ok", create="ok"):
        self.table, self.ok, self.how, self.create_how = dict(table or {}), ok, show, create
        self.created = []

    def supported(self):
        return SimpleNamespace(ok=self.ok, why="")

    def show(self, slot):
        if self.how == "try-again":
            raise self.TryAgain("not answering")
        if self.how == "error":
            raise self.SlotsError("tmux failed")
        entry = self.table.get(str(slot))
        if entry is None:
            return None
        fields = {"number": int(slot) if str(slot).isdigit() else 9, "created": 1.0,
                  "hold": None, "launchable": False, **entry}
        return SimpleNamespace(**fields)

    def create(self, numbers=None, *, count=1, name=None):
        if self.create_how == "refused":
            raise self.Refused("in-use")
        if self.create_how == "try-again":
            raise self.TryAgain("lock")
        self.created.append(list(numbers))
        return list(numbers)


FREE = {"state": "free", "launchable": True}
ENDED_PANE = {"state": "ended", "launchable": True}
OCCUPIED = {"state": "occupied"}
HELD = {"state": "free", "hold": SimpleNamespace(holder="someone", until=1.9e9)}
MISSING = {"state": "missing"}


class Launch:
    """`api.launch` stood in for: records each request and answers started."""

    def __init__(self):
        self.requests = []

    def __call__(self, request, *, env=None, warn=None):
        self.requests.append(request)
        return SimpleNamespace(line=f"started {request.request_id}", outcome=None,
                               exit_code=0, state="started", reason=None,
                               request_id=request.request_id,
                               session_id=request.session_id, target=request.row.target,
                               slot=request.slot, duplicate=False)


def go(conn, which=SID, *, turns=None, slots_api=None, **kw):
    launch = Launch()
    result = resume.resume(which, conn=conn, turns=turns, launch=launch,
                           slots_api=slots_api if slots_api is not None else Slots(
                               {"3": FREE}), environ={first.COMMS_NAME: "ann"}, **kw)
    return result, launch


def launches(conn) -> int:
    return conn.execute("SELECT count(*) FROM launches").fetchone()[0]


# -- the list -----------------------------------------------------------------------------


def test_the_list_is_one_entry_per_launch_newest_first(conn):
    launched(conn, request_id="r1", at="2026-10-30T10:00:00.000000+00:00")
    launched(conn, request_id="r2", session_id=SID2, target_name="terminal",
             at="2026-10-31T10:00:00.000000+00:00")
    got = resume.sessions(conn=conn, turns=None)
    assert got.exit_code == 0
    assert [e["request_id"] for e in got.data["sessions"]] == ["r2", "r1"]
    first_entry = got.data["sessions"][0]
    assert (first_entry["session_id"], first_entry["live"], first_entry["resumable"]) == (
        SID2, False, True)
    assert got.lines[0].endswith("terminal, launch r2, session " + SID2 +
                                 ", ended: resumable")
    assert resume.sessions(conn=conn, turns=None, limit=1).data["sessions"][0][
        "request_id"] == "r2"
    older = resume.sessions(conn=conn, turns=None, before="2026-10-31T00:00:00Z")
    assert [e["request_id"] for e in older.data["sessions"]] == ["r1"]


def test_an_empty_log_lists_no_sessions(conn):
    got = resume.sessions(conn=conn, turns=None)
    assert got.lines == ("no sessions",) and got.data == {"sessions": []}


def test_no_launch_log_yet_lists_no_sessions():
    got = resume.sessions(turns=None)
    assert got.data == {"sessions": []}


def test_live_is_read_from_turns_by_the_newest_id(conn):
    launched(conn)
    store.learn_session(conn, "r1", SID2, "clear")
    turns = Turns(live={SID2})
    [past] = resume.sessions(conn=conn, turns=turns).data["sessions"]
    assert turns.asked == [SID2] and turns.sessions[0].closed == 1
    assert (past["session_id"], past["live"], past["resumable"]) == (SID2, True, False)
    assert past["why"] == f"session {SID2} is live"
    assert past["hint"] == "attach to it instead: moxi slots attach 3"


def test_live_is_read_from_the_launchs_process_when_turns_says_none(conn):
    launched(conn, live=True)
    [past] = resume.sessions(conn=conn, turns=Turns()).data["sessions"]
    assert past["live"] is True and not past["resumable"]


def test_live_is_read_from_the_process_without_turns(conn):
    launched(conn, live=True, target_name="terminal")
    [past] = resume.sessions(conn=conn, turns=None).data["sessions"]
    assert past["live"] is True
    assert past["hint"] == resume.ATTACH_TERMINAL


def test_a_process_with_no_start_time_is_checked_by_its_pid(conn):
    launched(conn)
    conn.execute("UPDATE launches SET pid_started = NULL")
    [past] = resume.sessions(conn=conn, turns=None).data["sessions"]
    assert past["live"] is True


def test_a_turns_read_that_fails_marks_only_its_row(conn):
    launched(conn, request_id="r1", at="2026-10-30T10:00:00.000000+00:00")
    launched(conn, request_id="r2", session_id=SID2, pid=0,
             at="2026-10-31T10:00:00.000000+00:00")
    got = resume.sessions(conn=conn, turns=Turns(failing={SID2}))
    unread, fine = got.data["sessions"]
    assert unread["live"] is None and not unread["resumable"]
    assert unread["unread"] == "turns could not be asked: the endpoint did not answer"
    assert "can't be read" in unread["why"]
    assert (fine["live"], fine["resumable"]) == (False, True)
    assert "liveness unread" in got.lines[0]


def test_a_failed_turns_read_with_a_process_to_check_reads_the_process(conn):
    launched(conn)
    [past] = resume.sessions(conn=conn, turns=Turns(failing={SID})).data["sessions"]
    assert (past["live"], past["unread"], past["resumable"]) == (False, None, True)


@pytest.mark.parametrize(("fields", "why"), [
    ({"target_name": "headless"}, "ran headless"),
    ({"harness": "codex", "target_name": "terminal", "session_id": None},
     "has no session id"),
])
def test_what_no_resume_can_take(conn, fields, why):
    launched(conn, **fields)
    [past] = resume.sessions(conn=conn, turns=None).data["sessions"]
    assert not past["resumable"] and why in past["why"]


def with_comms(monkeypatch, api_) -> list[int]:
    """comms linked, its API `api_`; the list counts each time the API is read."""
    reads = []
    monkeypatch.setattr(slots, "links", lambda name: name == "comms")
    monkeypatch.setattr(presets, "comms_api", lambda: reads.append(1) or api_)
    return reads


NEW_COMMS = SimpleNamespace(CODEX_RESUME=True)
#: A comms from before its Codex wrapper took --resume: no CODEX_RESUME at all.
OLD_COMMS = SimpleNamespace()


def test_a_codex_launch_with_a_comms_name_is_resumable_when_comms_can(conn, monkeypatch):
    with_comms(monkeypatch, NEW_COMMS)
    launched(conn, harness="codex", target_name="terminal", session_id=THREAD,
             identity="builder")
    [past] = resume.sessions(conn=conn, turns=None).data["sessions"]
    assert past["resumable"] and past["why"] is None
    assert past["session_id"] == THREAD


def test_a_codex_launch_with_a_comms_name_and_an_older_comms_is_not_yet(conn, monkeypatch):
    reads = with_comms(monkeypatch, OLD_COMMS)
    launched(conn, request_id="r1", harness="codex", target_name="terminal",
             session_id=THREAD, identity="builder", at="2026-10-30T10:00:00.000000+00:00")
    launched(conn, request_id="r2", harness="codex", target_name="terminal",
             session_id=SID2, identity="planner", at="2026-10-31T10:00:00.000000+00:00")
    launched(conn, request_id="r3", session_id=SID, at="2026-11-01T10:00:00.000000+00:00")
    claude_past, *codex_pasts = resume.sessions(conn=conn, turns=None).data["sessions"]
    assert claude_past["resumable"]
    for past in codex_pasts:
        assert not past["resumable"]
        assert "comms' Codex wrapper here can't resume a thread" in past["why"]
        assert past["hint"] == "upgrade moxi-comms"
    # comms is read once for the whole list, not once per entry.
    assert len(reads) == 1


def test_a_codex_launch_with_a_comms_name_and_no_comms_is_resumable(conn, monkeypatch):
    monkeypatch.setattr(slots, "links", lambda name: False)
    launched(conn, harness="codex", target_name="terminal", session_id=THREAD,
             identity="builder")
    [past] = resume.sessions(conn=conn, turns=None).data["sessions"]
    assert past["resumable"]


def test_a_launch_resumed_since_points_at_the_launch_that_holds_its_id(conn):
    launched(conn, request_id="r1", at="2026-10-30T10:00:00.000000+00:00")
    store.insert(conn, request_id="r2", actor="ann", harness="claude", target="slot",
                 cwd="/work", values={}, argv=["claude"], env={}, posture="operator",
                 session_id=SID, slot="3", resume_of="r1",
                 at="2026-10-31T10:00:00.000000+00:00")
    conn.execute("UPDATE launches SET state = 'ready', endpoint = 'x' WHERE request_id = "
                 "'r2'")
    turns = Turns(live={SID})
    new, old = resume.sessions(conn=conn, turns=turns).data["sessions"]
    assert (new["request_id"], new["live"], new["held_by"]) == ("r2", True, None)
    assert (old["request_id"], old["held_by"], old["live"]) == ("r1", "r2", False)
    assert not old["resumable"] and "launch r2 resumed session" in old["why"]
    # Only the newest holder's session is read from turns.
    assert turns.asked == [SID]


# -- resume: the refusals -------------------------------------------------------------------


def test_a_session_no_launch_holds_is_not_launched(conn):
    with pytest.raises(target.NotLaunched):
        go(conn, SID2)


def test_a_headless_launch_is_refused_and_nothing_is_written(conn):
    launched(conn, target_name="headless")
    with pytest.raises(resume.Refused) as refused:
        go(conn)
    assert refused.value.code == "headless" and refused.value.exit_code == 3
    assert launches(conn) == 1


def test_a_codex_launch_with_no_thread_id_is_refused(conn):
    launched(conn, harness="codex", target_name="terminal", session_id=None)
    # A Codex launch with no id is in no session_ids row: found by its request's id only
    # through the list; resume by any id finds nothing.
    with pytest.raises(target.NotLaunched):
        go(conn, THREAD)
    row = store.get(conn, "r1")
    refused = resume.static_refusal(row, None)
    assert refused.code == "no-session-id" and "codex resume" in refused.hint


def test_a_codex_resume_with_a_comms_name_and_an_older_comms_is_refused(conn,
                                                                         monkeypatch):
    with_comms(monkeypatch, OLD_COMMS)
    launched(conn, harness="codex", target_name="terminal", session_id=THREAD,
             identity="builder")
    with pytest.raises(resume.Refused) as refused:
        go(conn, THREAD)
    assert refused.value.code == "not-yet" and refused.value.exit_code == 3
    assert "comms' Codex wrapper here can't resume a thread" in refused.value.problem
    assert refused.value.hint == "upgrade moxi-comms"
    assert launches(conn) == 1


HOST = "/env/bin/moxi-turns-codex-host"


def test_a_codex_resume_with_a_comms_name_continues_its_thread(conn, monkeypatch):
    # The replay composes comms' Codex wrapper with its own flags, then the thread.
    with_comms(monkeypatch, NEW_COMMS)
    monkeypatch.setattr(slots, "links", lambda name: name in ("comms", "turns"))
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: SimpleNamespace())
    monkeypatch.setattr(presets, "codex_host", lambda: HOST)
    launched(conn, harness="codex", target_name="terminal", session_id=THREAD,
             identity="builder", values={"harness": "codex", "target": "terminal",
                                         "cwd": "/work", "identity": "builder",
                                         "model": "gpt-5"})
    composed = []

    def launch(request, *, env=None, warn=None):
        composed.append(api.preview(request, env=env))
        return Launch()(request, env=env, warn=warn)

    result = resume.resume(THREAD, conn=conn, turns=None, slots_api=None, launch=launch,
                           request_id="r2", environ={first.COMMS_NAME: "ann"})
    assert result.exit_code == 0 and result.data["resume_of"] == "r1"
    assert result.data["session_id"] == THREAD
    [got] = composed
    argv = list(got.argv)
    assert got.mode == "host" and argv[1:5] == ["comms", "codex", "--name", "builder"]
    assert argv[-2:] == ["--resume", THREAD] and argv.count("--resume") == 1
    assert "--sandbox" in argv[:-2]


def test_a_harness_no_longer_catalogued_is_refused(conn, monkeypatch):
    launched(conn)
    monkeypatch.setattr(catalog, "HARNESSES", ("codex",))
    with pytest.raises(resume.Refused) as refused:
        go(conn)
    assert refused.value.code == "not-catalogued"


def test_a_live_session_is_refused_at_the_call_with_where_to_attach(conn):
    launched(conn)
    with pytest.raises(resume.Refused) as refused:
        go(conn, turns=Turns(live={SID}))
    assert (refused.value.code, refused.value.exit_code) == ("live", 3)
    assert refused.value.hint == "attach to it instead: moxi slots attach 3"
    assert launches(conn) == 1


def test_a_live_process_is_refused_without_turns(conn):
    launched(conn, live=True, target_name="terminal")
    with pytest.raises(resume.Refused, match="is live"):
        go(conn)


def test_an_unread_liveness_is_refused_with_exit_1(conn):
    launched(conn, pid=0)
    with pytest.raises(resume.Refused) as refused:
        go(conn, turns=Turns(failing={SID}))
    assert (refused.value.code, refused.value.exit_code) == ("liveness-unread", 1)


# -- resume: the replay ---------------------------------------------------------------------


def open_relaunch(conn, *, job=None, at=None):
    mine = me()
    return store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                            session_id=SID, actor="ann",
                            job=job or f"proc:{mine.pid}@{mine.started:.6f}", at=at)


def test_a_launch_with_a_run_under_way_is_refused_run_open_and_nothing_launched(conn):
    launched(conn)
    run = open_relaunch(conn)
    with pytest.raises(store.RunOpen) as refused:
        go(conn)
    assert refused.value.run.run_id == run.run_id and refused.value.exit_code == 3
    assert launches(conn) == 1


def test_a_run_whose_job_is_gone_does_not_hold_a_resume(conn):
    launched(conn)
    open_relaunch(conn, job="proc:999999999@1.0", at="2020-01-01T00:00:00.000000Z")
    result, launch = go(conn)
    assert result.exit_code == 0 and len(launch.requests) == 1


def test_a_resume_replays_the_row_with_resume_of_and_its_newest_id(conn):
    launched(conn)
    store.learn_session(conn, "r1", SID2, "clear")
    result, launch = go(conn, SID, request_id="r9")
    [request] = launch.requests
    assert isinstance(request, api.ReplayRequest)
    assert (request.resume_of, request.relaunch_of, request.session_id) == ("r1", None,
                                                                            SID2)
    assert (request.request_id, request.actor, request.slot) == ("r9", "ann", "3")
    assert (request.posture, request.handoff, request.hold, request.respawn) == (
        None, None, None, False)
    assert result.exit_code == 0 and result.lines == ("started r9",)
    assert result.data["outcome"] == "resumed" and result.data["resume_of"] == "r1"
    assert result.data["session_id"] == SID2


def test_a_resume_mints_its_request_id(conn):
    launched(conn)
    _, launch = go(conn)
    assert launch.requests[0].request_id and launch.requests[0].request_id != "r1"


def test_a_terminal_resume_names_no_slot(conn):
    launched(conn, target_name="terminal")
    _, launch = go(conn)
    assert launch.requests[0].slot is None


def test_a_failed_launch_is_the_resumes_outcome(conn):
    launched(conn)

    def failing(request, *, env=None, warn=None):
        return SimpleNamespace(line="failed: no harness", outcome=None, exit_code=1,
                               state="failed", reason="not-on-path",
                               request_id=request.request_id, session_id=SID,
                               target="slot", slot=None, duplicate=False)

    got = resume.resume(SID, conn=conn, turns=None, launch=failing,
                        slots_api=Slots({"3": FREE}))
    assert got.exit_code == 1 and got.data["outcome"] == "failed"
    assert got.data["reason"] == "not-on-path"


# -- the slot -------------------------------------------------------------------------------


def row_in(conn, slot="3"):
    launched(conn, slot=slot)
    return store.get(conn, "r1")


@pytest.mark.parametrize(("entry", "chosen"), [
    (FREE, "3"), (ENDED_PANE, "3"), (OCCUPIED, ""), (HELD, ""),
    ({"state": "free", "launchable": False}, ""),
])
def test_the_recorded_slot_when_launchable_else_the_lowest(conn, entry, chosen):
    api_ = Slots({"3": entry})
    assert resume.choose_slot(api_, row_in(conn), None) == chosen
    assert api_.created == []


@pytest.mark.parametrize("entry", [MISSING, None])
def test_a_missing_recorded_slot_is_made_again_first(conn, entry):
    api_ = Slots({"3": entry} if entry is not None else {})
    assert resume.choose_slot(api_, row_in(conn), None) == "3"
    assert api_.created == [[3]]


def test_a_slot_session_with_no_registry_row_is_made_again_first(conn):
    api_ = Slots({"3": {"state": "occupied", "created": None}})
    assert resume.choose_slot(api_, row_in(conn), None) == "3"
    assert api_.created == [[3]]


def test_a_slot_slots_wont_make_again_falls_back_to_the_lowest(conn):
    api_ = Slots({"3": MISSING}, create="refused")
    assert resume.choose_slot(api_, row_in(conn), None) == ""


def test_a_named_recorded_slot_that_is_gone_falls_back_to_the_lowest(conn):
    api_ = Slots({})
    assert resume.choose_slot(api_, row_in(conn, slot="docs"), None) == ""
    assert api_.created == []


@pytest.mark.parametrize("entry", [FREE, OCCUPIED, HELD])
def test_a_slot_asked_for_is_used_as_it_is(conn, entry):
    api_ = Slots({"5": entry})
    assert resume.choose_slot(api_, row_in(conn), 5) == 5
    assert api_.created == []


def test_a_missing_slot_asked_for_is_made_first(conn):
    api_ = Slots({"5": MISSING})
    assert resume.choose_slot(api_, row_in(conn), 5) == 5
    assert api_.created == [[5]]


def test_a_missing_slot_asked_for_that_slots_wont_make_is_passed_on(conn):
    api_ = Slots({}, create="refused")
    assert resume.choose_slot(api_, row_in(conn), 5) == 5


def test_slots_not_answering_is_refused_try_again(conn):
    with pytest.raises(resume.Refused) as refused:
        resume.choose_slot(Slots(show="try-again"), row_in(conn), None)
    assert (refused.value.code, refused.value.exit_code) == ("slots-busy", 75)


def test_slots_failing_on_the_recorded_slot_is_refused(conn):
    with pytest.raises(resume.Refused) as refused:
        resume.choose_slot(Slots(show="error"), row_in(conn), None)
    assert (refused.value.code, refused.value.exit_code) == ("slots-failed", 1)


def test_a_create_not_answering_is_refused_try_again(conn):
    with pytest.raises(resume.Refused) as refused:
        resume.choose_slot(Slots({"3": MISSING}, create="try-again"), row_in(conn), None)
    assert refused.value.code == "slots-busy"


def test_without_slots_the_recorded_slot_goes_to_the_launchs_own_check(conn):
    row = row_in(conn)
    assert resume.choose_slot(None, row, None) == "3"
    assert resume.choose_slot(Slots(ok=False), row, 7) == 7


def test_a_slot_resume_is_refused_before_launching_when_slots_cant_answer(conn):
    launched(conn)
    with pytest.raises(resume.Refused, match="did not answer"):
        go(conn, slots_api=Slots(show="try-again"))


# -- through api.launch -----------------------------------------------------------------------


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def no_exec(monkeypatch):
    seen = []
    monkeypatch.setattr(targets, "_execve",
                        lambda command, argv, env: seen.append({"argv": argv, "env": env}))
    here = os.getcwd()
    yield seen
    os.chdir(here)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    directory = tmp_path / "fake-slots"
    directory.mkdir()
    monkeypatch.setenv(slots.SEAM, "launcher_fake_slots")
    monkeypatch.setenv(fake.DIR, str(directory))
    monkeypatch.syspath_prepend(str(TESTS))
    fake.reset()
    script = directory / "script.json"

    def plan(**keys):
        now = json.loads(script.read_text()) if script.exists() else {}
        script.write_text(json.dumps({**now, **keys}))

    return SimpleNamespace(plan=plan, calls=lambda name=None: fake.calls(directory, name))


def first_launch(work: Path, **typed) -> None:
    api.launch(api.LaunchRequest(typed=typed, cwd=str(work), request_id="r1",
                                 session_id=SID, actor="ann"))
    ended("r1")


def ended(request_id: str) -> None:
    """The launch's session has ended: its recorded process is not running."""
    with_store(lambda c: c.execute("UPDATE launches SET pid_started = ? WHERE "
                                   "request_id = ?", (ENDED, request_id)))


def with_store(run):
    conn = store.connect(create=False)
    try:
        return run(conn)
    finally:
        conn.close()


def test_a_terminal_resume_keeps_the_id_and_later_acts_find_the_new_launch(work, harness,
                                                                              no_exec):
    first_launch(work)
    resume.resume(SID, request_id="r2", turns=None, slots_api=None)
    argv = no_exec[-1]["argv"]
    assert argv[1:3] == ["--resume", SID] and "--session-id" not in argv
    new = with_store(lambda c: store.get(c, "r2"))
    assert (new.session_id, new.resume_of, new.target) == (SID, "r1", "terminal")
    assert with_store(lambda c: store.resolve(c, SID)).request_id == "r2"
    # The resumed session's endpoint claims the new launch, and an act on the id finds it.
    endpoint = f"{me().pid}@{me().started:.6f}"
    with_store(lambda c: store.claim(c, "r2", harness="claude", session_id=SID,
                                     endpoint=endpoint))
    session = SimpleNamespace(session_id=SID, endpoint_pid=os.getpid(), pid=os.getppid(),
                              close=lambda: None)
    found = target.find(SID, turns=SimpleNamespace(find=lambda **_: session),
                        slots_api=None)
    assert found.row.request_id == "r2" and found.live


def test_a_slot_resume_goes_back_to_its_slot(work, harness, rig):
    rig.plan(slots={"3": {"state": "free", "launchable": True}})
    first_launch(work, target="slot", slot="3")
    rig.plan(slots={"3": {"state": "ended", "launchable": True}})
    got = resume.resume(SID, request_id="r2", turns=None)
    start = rig.calls("start")[-1]
    assert (start["slot"], start["create"]) == (3, False)
    assert start["argv"][1:3] == ["--resume", SID]
    assert start["meta"] == {"request_id": "r2", "session_id": SID, "chain": "r2"}
    assert got.exit_code == 0 and got.data["slot"] == "3"


def test_a_slot_resume_takes_the_lowest_when_its_slot_is_occupied(work, harness, rig):
    rig.plan(slots={"3": {"state": "free", "launchable": True}})
    first_launch(work, target="slot", slot="3")
    rig.plan(slots={"3": {"state": "occupied", "launchable": False}}, placed=1)
    got = resume.resume(SID, request_id="r2", turns=None)
    start = rig.calls("start")[-1]
    assert (start["slot"], start["create"]) == (None, True)
    assert got.data["slot"] == "1"


def test_a_slot_resume_makes_its_missing_slot_first(work, harness, rig):
    rig.plan(slots={"3": {"state": "free", "launchable": True}})
    first_launch(work, target="slot", slot="3")
    rig.plan(slots={"3": {"state": "missing", "launchable": False}})
    resume.resume(SID, request_id="r2", turns=None)
    assert [c["numbers"] for c in rig.calls("create")] == [[3]]
    assert rig.calls("start")[-1]["slot"] == 3


def test_a_slot_named_for_a_terminal_resume_is_refused(work, harness, no_exec):
    first_launch(work)
    with pytest.raises(catalog.BadValue) as refused:
        resume.resume(SID, slot=4, request_id="r2", turns=None, slots_api=None)
    assert api.exit_code(refused.value) == 2
    assert with_store(lambda c: store.get(c, "r2")) is None


def test_a_resume_that_failed_before_its_claim_is_skipped(work, harness, rig):
    rig.plan(slots={"3": {"state": "free", "launchable": True}})
    first_launch(work, target="slot", slot="3")
    rig.plan(start="refused", refused={"reason": "occupied", "words": "slot 3 is taken"})
    got = resume.resume(SID, request_id="r2", turns=None)
    assert got.exit_code != 0 and got.data["state"] == "failed"
    assert with_store(lambda c: store.resolve(c, SID)).request_id == "r1"
    listed = resume.sessions(turns=None).data["sessions"]
    assert [(e["request_id"], e["held_by"]) for e in listed] == [("r2", "r1"), ("r1", None)]
    rig.plan(start="ok")
    resume.resume(SID, request_id="r3", turns=None)
    assert with_store(lambda c: store.get(c, "r3")).resume_of == "r1"


def test_two_resumes_of_one_session_at_once_launch_it_once(work, harness, no_exec,
                                                           monkeypatch):
    """Both resumes read the session ended before either writes its row. The first to
    write it launches; the second, finding that launch of the session still starting, is
    refused `live` with nothing written, and the session is launched once."""
    first_launch(work)
    both_read = threading.Barrier(2, timeout=10)
    real_liveness = resume.liveness

    def read_then_wait(*args, **kwargs):
        found = real_liveness(*args, **kwargs)
        both_read.wait()
        return found
    monkeypatch.setattr(resume, "liveness", read_then_wait)
    # The first launch to write its row waits, before it starts, until the other resume
    # has finished: so the other meets it while it is still starting.
    other_done = threading.Event()
    wrote = []
    real_files = api._make_files

    def files_then_wait(*args, **kwargs):
        wrote.append(threading.current_thread().name)
        if len(wrote) == 1:
            other_done.wait(10)
        return real_files(*args, **kwargs)
    monkeypatch.setattr(api, "_make_files", files_then_wait)
    outcomes = {}

    def one(rid):
        try:
            outcomes[rid] = resume.resume(SID, request_id=rid, turns=None, slots_api=None)
        except Exception as error:  # noqa: BLE001  (the refusal is the outcome)
            outcomes[rid] = error
        finally:
            if wrote and wrote[0] != rid:
                other_done.set()
    threads = [threading.Thread(target=one, args=(rid,), name=rid) for rid in ("r2", "r3")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert wrote and len(wrote) == 1, wrote
    winner = wrote[0]
    [loser] = {"r2", "r3"} - {winner}
    assert outcomes[winner].exit_code == 0, outcomes[winner]
    refused = outcomes[loser]
    assert isinstance(refused, resume.Refused), refused
    assert refused.code == "live" and api.exit_code(refused) == 3
    assert f"launch {winner}" in refused.problem
    assert with_store(lambda c: store.get(c, loser)) is None
    # The first launch and the winning resume became the harness; the loser did not.
    assert len(no_exec) == 2


def test_a_resume_while_the_last_resume_runs_is_refused_live(work, harness, no_exec):
    """A resume whose session a later launch now runs (its process live) is refused at
    the insert even when the check before it read the session ended."""
    first_launch(work)
    resume.resume(SID, request_id="r2", turns=None, slots_api=None)
    # The new launch runs (this process, as a terminal launch records it); the session's
    # original launch is ended. A resume through the original's row meets the new one.
    row = with_store(lambda c: store.get(c, "r1"))
    with pytest.raises(store.SessionLive) as refused:
        api.launch(api.ReplayRequest(row, resume_of="r1", request_id="r3",
                                     session_id=SID, actor="ann"))
    assert (refused.value.holder.request_id, refused.value.how) == ("r2", "live")
    assert with_store(lambda c: store.get(c, "r3")) is None


def test_a_launch_that_failed_or_was_left_starting_long_ago_does_not_block_a_resume(
        work, harness, no_exec):
    first_launch(work)
    with_store(lambda c: c.execute(
        "UPDATE launches SET state = 'spawning', pid = NULL, created_at = "
        "'2026-01-01T00:00:00.000000Z' WHERE request_id = 'r1'"))
    resume.resume(SID, request_id="r2", turns=None, slots_api=None)
    with_store(lambda c: store.set_state(c, "r2", "failed", reason="spawn-failed"))
    ended("r2")
    resume.resume(SID, request_id="r3", turns=None, slots_api=None)
    assert with_store(lambda c: store.get(c, "r3")).resume_of == "r1"
