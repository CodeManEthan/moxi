"""A resume from the launcher's page: `/api/resume`'s refusals answered before anything
runs, a terminal launch answered with the command to copy, a slot launch resumed by the
detached launch job (a fake spawn) under a new request id, and the job's `resume` act,
with a stand-in launch and through `api.launch` into fake slots."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import proc
from test_page import get, post
from test_page_launch import Spawned, requested, row

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import (api, catalog, first, job, pagehandlers, presets, slots, store,
                           targets)

TESTS = Path(__file__).resolve().parent
SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
OTHER = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
#: A start time no running process has, so a row's process reads ended.
ENDED = 1.0
POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="the fake harness is a script run by its #! line")
SLOTS_API = slots.api


class Turns:
    """turns' `find`: live for the ids in `live`, raising for the ids in `failing`."""

    def __init__(self, live=(), failing=()):
        self.live, self.failing = set(live), set(failing)

    def find(self, *, session_id=None, pid=None):
        if session_id in self.failing:
            raise OSError("the endpoint did not answer")
        if session_id in self.live:
            return SimpleNamespace(close=lambda: None)
        return None


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew; only slots reads as installed, slots and
    turns are not reached unless a test says how, and nothing is spawned unless a test
    says how."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(slots, "links", lambda name: name == "slots")
    monkeypatch.setattr(slots, "api", lambda: None)
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: None)
    fake_turns.hide(monkeypatch)

    def no_spawn(argv, **kw):
        raise AssertionError(f"spawned {argv}")

    monkeypatch.setattr(pagehandlers, "_spawn", no_spawn)


def with_turns(monkeypatch, turns: Turns) -> None:
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: turns)


def with_store(run):
    conn = store.connect()
    try:
        return run(conn)
    finally:
        conn.close()


def seed(request_id="r1", *, target_name="slot", harness="claude", session_id=SID,
         slot="3", live=False, pid=True, identity=None, at=None) -> None:
    """A launch in the log, its process ended unless `live`; with no `pid`, none recorded."""
    def insert(conn):
        store.insert(conn, request_id=request_id, actor="ann", harness=harness,
                     target=target_name, cwd="/work", values={"model": "opus"},
                     argv=[harness], env={}, posture="operator",
                     session_id=session_id if harness == "claude" else None,
                     slot=slot if target_name == "slot" else None, identity=identity,
                     at=at)
        if harness == "codex" and session_id:
            store.learn_session(conn, request_id, session_id, "sender")
        if pid:
            mine = proc.identity(os.getpid())
            conn.execute("UPDATE launches SET pid = ?, pid_started = ? WHERE request_id = ?",
                         (mine.pid, mine.started if live else ENDED, request_id))
    with_store(insert)


def resumed(body: dict) -> dict:
    status, said = post("/api/resume", body)
    assert status == 200, said
    return said


def refused_code(body: dict) -> str:
    said = resumed(body)
    assert said["command"] is None and said["refused"]["problem"] and said["refused"]["hint"]
    return said["refused"]["code"]


def no_request_files() -> bool:
    runs = targets.request_file("x").parent
    return not runs.exists() or not list(runs.glob("*.request.json"))


# --- the refusals, answered in the route -------------------------------------------------------

def test_a_session_no_launch_holds_is_refused_not_launched():
    assert refused_code({"session": SID}) == "not-launched"
    seed()
    assert refused_code({"session": OTHER}) == "not-launched"
    assert no_request_files()


def test_a_headless_launch_is_refused_with_nothing_to_resume_it_into():
    seed(target_name="headless")
    said = resumed({"session": SID})
    assert said["refused"]["code"] == "headless" and "headless" in said["refused"]["problem"]
    assert said["command"] is None and no_request_files()


def test_a_harness_no_longer_launched_is_refused(monkeypatch):
    seed()
    monkeypatch.setattr(catalog, "HARNESSES", ("codex",))
    assert refused_code({"session": SID}) == "not-catalogued"


def test_a_live_session_is_refused_with_where_to_attach(monkeypatch):
    seed()
    with_turns(monkeypatch, Turns(live={SID}))
    said = resumed({"session": SID})
    assert said["refused"]["code"] == "live"
    assert said["refused"]["hint"] == "attach to it instead: moxi slots attach 3"
    assert no_request_files()


def test_a_live_process_is_refused_without_turns():
    seed(target_name="terminal", live=True)
    said = resumed({"session": SID})
    assert said["refused"]["code"] == "live" and said["command"] is None


def test_a_liveness_that_cant_be_read_is_refused(monkeypatch):
    seed(pid=False)
    with_turns(monkeypatch, Turns(failing={SID}))
    said = resumed({"session": SID})
    assert said["refused"]["code"] == "liveness-unread"
    assert "the endpoint did not answer" in said["refused"]["problem"]


def open_run(request_id="r1") -> None:
    """A relaunch run open on `request_id`'s launch, its job this test's own process."""
    mine = proc.identity(os.getpid())
    with_store(lambda c: store.insert_run(c, kind="relaunch", mode="self",
                                          request_id=request_id, session_id=SID, actor="ann",
                                          job=f"proc:{mine.pid}@{mine.started:.6f}"))


@pytest.mark.parametrize("target_name", ["slot", "terminal"])
def test_a_run_under_way_is_refused_run_open_in_the_route(target_name):
    # A relaunch, exit or recycle under way owns the session: the route says so before
    # anything is written or spawned, as the command line's resume does.
    seed(target_name=target_name)
    open_run()
    said = resumed({"session": SID})
    assert said["command"] is None and said["refused"]["code"] == "run-open"
    assert said["refused"]["hint"] == "that run must end first"
    assert no_request_files()


def test_a_run_whose_job_is_gone_does_not_hold_the_resume(monkeypatch):
    monkeypatch.setattr(pagehandlers, "_spawn", Spawned())
    seed()
    with_store(lambda c: store.insert_run(c, kind="relaunch", mode="self", request_id="r1",
                                          session_id=SID, actor="ann",
                                          job=f"proc:{os.getpid()}@{ENDED:.6f}",
                                          at="2020-01-01T00:00:00.000000Z"))
    status, _ = post("/api/resume", {"session": SID})
    assert status == 202


def test_an_unexpected_failure_names_only_its_class(monkeypatch):
    def broken(conn, session_id):
        raise RuntimeError("/home/someone/private")

    seed()
    monkeypatch.setattr(store, "resolve", broken)
    said = resumed({"session": SID})
    assert said["refused"]["code"] == "error"
    assert said["refused"]["problem"] == pagehandlers.UNEXPECTED.format("RuntimeError")


# --- a terminal launch: the command to copy ------------------------------------------------------

def test_a_terminal_launch_is_answered_with_the_command_to_copy():
    seed(target_name="terminal")
    said = resumed({"session": SID})
    assert said == {"refused": None, "command": f"moxi launcher resume {SID}"}
    assert no_request_files() and with_store(lambda c: len(store.log(c))) == 1


def test_a_slot_named_for_a_terminal_launch_is_refused():
    seed(target_name="terminal")
    said = resumed({"session": SID, "slot": "4"})
    assert said["refused"]["code"] == "bad-value" and said["refused"]["row"] == "slot"
    assert said["command"] is None and no_request_files()


# --- a Codex launch with a comms name -------------------------------------------------------------

def with_comms(monkeypatch, api_) -> list[int]:
    """comms linked beside slots, its API `api_`; each read of the API is counted."""
    reads = []
    monkeypatch.setattr(slots, "links", lambda name: name in ("slots", "comms"))
    monkeypatch.setattr(presets, "comms_api", lambda: reads.append(1) or api_)
    return reads


def test_a_codex_launch_with_a_comms_name_resumes_when_comms_can(monkeypatch):
    with_comms(monkeypatch, SimpleNamespace(CODEX_RESUME=True))
    seed(target_name="terminal", harness="codex", identity="builder")
    said = resumed({"session": SID})
    assert said == {"refused": None, "command": f"moxi launcher resume {SID}"}


def test_a_codex_launch_with_a_comms_name_and_an_older_comms_is_refused_not_yet(
        monkeypatch):
    with_comms(monkeypatch, SimpleNamespace())
    seed(target_name="terminal", harness="codex", identity="builder")
    said = resumed({"session": SID})
    assert said["refused"]["code"] == "not-yet" and said["command"] is None
    assert "comms' Codex wrapper here can't resume a thread" in said["refused"]["problem"]
    assert said["refused"]["hint"] == "upgrade moxi-comms"
    assert no_request_files()


def test_the_pages_past_sessions_read_comms_once_for_the_list(monkeypatch):
    reads = with_comms(monkeypatch, SimpleNamespace())
    seed("r1", target_name="terminal", harness="codex", identity="builder",
         at="2026-01-01T00:00:00.000000Z")
    seed("r2", target_name="terminal", harness="codex", identity="planner",
         session_id=OTHER, at="2026-01-02T00:00:00.000000Z")
    status, said, _ = get("/api/sessions")
    assert status == 200 and [r["request_id"] for r in said["sessions"]] == ["r2", "r1"]
    for entry in said["sessions"]:
        assert not entry["resumable"] and entry["hint"] == "upgrade moxi-comms"
    assert len(reads) == 1


def test_the_pages_past_sessions_name_each_launchs_project_and_actor():
    seed("r1", at="2026-01-01T00:00:00.000000Z")

    def in_project(conn):
        store.insert(conn, request_id="r2", actor="alice", harness="claude", target="slot",
                     cwd="/src/proj-a", values={}, argv=["claude"], env={},
                     posture="operator", session_id=OTHER, slot="2",
                     project_key="proj-a", at="2026-01-02T00:00:00.000000Z")
    with_store(in_project)
    status, said, _ = get("/api/sessions", "limit=20")
    assert status == 200
    assert [(e["request_id"], e["project_key"], e["actor"], e["cwd"])
            for e in said["sessions"]] == [("r2", "proj-a", "alice", "/src/proj-a"),
                                           ("r1", None, "ann", "/work")]


# --- a slot launch: the job ------------------------------------------------------------------------

def test_a_slot_launch_is_resumed_by_the_launch_job_202(monkeypatch):
    spawned = Spawned()
    monkeypatch.setattr(pagehandlers, "_spawn", spawned)
    monkeypatch.setattr(pagehandlers, "_new_request_id", lambda: "r9")
    seed()
    status, said = post("/api/resume", {"session": SID, "slot": " 5 "})
    assert (status, said) == (202, {"status": "/api/launches?request=r9"})
    path = targets.request_file("r9")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"act": "resume", "request_id": "r9",
                                            "session": SID, "slot": "5"}
    [call] = spawned.calls
    assert call["argv"][-2:] == [job.PAGE, "r9"] and call["detached"] is True
    assert Path(call["argv"][0]).name.startswith("moxi-launcher-job")
    assert call["output"] == targets.run_log("r9")


def test_a_slot_resume_with_no_slot_named_leaves_the_slot_to_the_job(monkeypatch):
    monkeypatch.setattr(pagehandlers, "_spawn", Spawned())
    seed()
    status, said = post("/api/resume", {"session": SID})
    assert status == 202
    request_id = said["status"].rsplit("=", 1)[-1]
    assert json.loads(targets.request_file(request_id).read_text())["slot"] is None


def test_a_second_use_of_the_request_is_409(monkeypatch):
    monkeypatch.setattr(pagehandlers, "_spawn", Spawned())
    monkeypatch.setattr(pagehandlers, "_new_request_id", lambda: "r9")
    seed()
    assert post("/api/resume", {"session": SID})[0] == 202
    # While its request file is there.
    status, said = post("/api/resume", {"session": SID})
    assert (status, said) == (409, {"conflict": pagehandlers.RESUME_USED.format("r9")})
    # And once the job has deleted it and the launch log knows the id.
    targets.request_file("r9").unlink()
    with_store(lambda c: store.add_event(c, store.LAUNCH_REFUSED, {"code": "live"},
                                         request_id="r9"))
    status, said = post("/api/resume", {"session": SID})
    assert status == 409 and not targets.request_file("r9").exists()


def test_a_job_that_did_not_start_leaves_no_request_file(monkeypatch):
    def failing(argv, **kw):
        raise OSError("no such program")

    monkeypatch.setattr(pagehandlers, "_spawn", failing)
    seed()
    said = resumed({"session": SID})
    assert said["refused"]["code"] == targets.SPAWN_FAILED
    assert "OSError" in said["refused"]["problem"] and no_request_files()


# --- the job's resume act ----------------------------------------------------------------------------

class Launch:
    """`api.launch` stood in for: records each request and answers started."""

    def __init__(self):
        self.requests = []

    def __call__(self, request, *, env=None, warn=None):
        self.requests.append(request)
        return SimpleNamespace(line=f"started {request.request_id}", outcome=None,
                               exit_code=0, state="started", reason=None,
                               request_id=request.request_id, session_id=request.session_id,
                               target=request.row.target, slot=request.slot, duplicate=False)


def resume_file(request_id="r9", **data) -> Path:
    return job.write_request(request_id, {"act": "resume", "request_id": request_id,
                                          "session": SID, "slot": None, **data})


def test_the_job_resumes_the_session_under_the_request_id():
    seed()
    path = resume_file(slot="4")
    launch = Launch()
    assert job.run_request("r9", launch=launch) == "started"
    [request] = launch.requests
    assert (request.request_id, request.resume_of, request.session_id) == ("r9", "r1", SID)
    assert request.row.request_id == "r1" and str(request.slot) == "4"
    assert not path.exists()


def test_the_job_without_a_slot_goes_back_to_the_launchs_own():
    seed()
    resume_file()
    launch = Launch()
    assert job.run_request("r9", launch=launch) == "started"
    assert launch.requests[0].slot == "3"


def test_a_session_live_by_the_time_the_job_runs_is_recorded(monkeypatch):
    seed()
    resume_file()
    with_turns(monkeypatch, Turns(live={SID}))
    launch = Launch()
    assert job.run_request("r9", launch=launch) == "refused"
    assert launch.requests == [] and row("r9") is None
    said = requested("r9")
    assert said["launches"] == [] and said["refused"]["code"] == "live"


def test_a_run_under_way_is_recorded_run_open():
    seed()
    open_run()
    resume_file()
    assert job.run_request("r9", launch=Launch()) == "refused"
    assert requested("r9")["refused"]["code"] == "run-open"


def test_the_job_never_resumes_a_terminal_launch():
    # The launch the session belongs to may be a terminal one by the time the job runs.
    seed(target_name="terminal")
    resume_file()
    launch = Launch()
    assert job.run_request("r9", launch=launch) == "refused"
    assert launch.requests == []
    refused = requested("r9")["refused"]
    assert refused["code"] == "not-run" and refused["row"] == "target"
    assert f"moxi launcher resume {SID}" in refused["hint"]


def test_a_resume_request_naming_no_session_is_recorded():
    resume_file(session="")
    assert job.run_request("r9", launch=Launch()) == "refused"
    assert requested("r9")["refused"]["code"] == "not-run"


def test_a_resume_past_its_wait_is_recorded_timed_out():
    release = threading.Event()

    def slow(request, **kw):
        release.wait(30)

    seed()
    path = resume_file()
    try:
        assert job.run_request("r9", wait_s=0.2, launch=slow) == "timed-out"
    finally:
        release.set()
    refused = requested("r9")["refused"]
    assert refused["code"] == "timed-out" and "resume" in refused["problem"]
    assert not path.exists()


def test_an_act_the_job_doesnt_run_is_recorded():
    job.write_request("r9", {"act": "fly", "request_id": "r9"})
    assert job.run_request("r9") == "refused"
    assert requested("r9")["refused"]["code"] == "not-run"


# --- through api.launch, into fake slots ----------------------------------------------------------

@pytest.fixture
def rig(tmp_path, monkeypatch):
    directory = tmp_path / "fake-slots"
    directory.mkdir()
    monkeypatch.setenv(slots.SEAM, "launcher_fake_slots")
    monkeypatch.setenv(fake.DIR, str(directory))
    monkeypatch.syspath_prepend(str(TESTS))
    monkeypatch.setattr(slots, "api", SLOTS_API)
    fake.reset()
    script = directory / "script.json"

    def plan(**keys):
        now = json.loads(script.read_text()) if script.exists() else {}
        script.write_text(json.dumps({**now, **keys}))

    return SimpleNamespace(plan=plan, calls=lambda name=None: fake.calls(directory, name))


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@POSIX
def test_a_slot_resume_from_the_page_goes_back_to_its_slot(tmp_path, harness, rig,
                                                           monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    rig.plan(slots={"3": {"state": "free", "launchable": True}})
    api.launch(api.LaunchRequest(typed={"target": "slot", "slot": "3"}, cwd=str(work),
                                 request_id="r1", session_id=SID, actor="ann"))
    with_store(lambda c: c.execute("UPDATE launches SET pid_started = ? WHERE "
                                   "request_id = 'r1'", (ENDED,)))
    rig.plan(slots={"3": {"state": "ended", "launchable": True}})
    spawned = Spawned(run=True)
    monkeypatch.setattr(pagehandlers, "_spawn", spawned)
    monkeypatch.setattr(pagehandlers, "_new_request_id", lambda: "r2")
    status, said = post("/api/resume", {"session": SID})
    assert (status, said) == (202, {"status": "/api/launches?request=r2"})
    spawned.join()
    [resumed_row] = requested("r2")["launches"]
    assert (resumed_row["resume_of"], resumed_row["session_id"]) == ("r1", SID)
    assert resumed_row["slot"] == "3" and resumed_row["target"] == "slot"
    start = rig.calls("start")[-1]
    assert start["slot"] == 3 and start["argv"][1:3] == ["--resume", SID]
    assert not targets.request_file("r2").exists()
    # The same request run again launches nothing more: the request id is the new launch's.
    resume_file("r2")
    job.run_request("r2")
    assert len(rig.calls("start")) == 2
    assert with_store(lambda c: len(store.log(c))) == 2
