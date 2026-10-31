"""A launch from the launcher's page: the form's values read as typed, `/api/preview` with
its refusals, the PATH check and the ticket, `/api/launch` with its request file and its
detached job (a fake spawn), and the job's `launch` act against a fake harness on PATH."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from test_page import get, post, write_presets

import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import catalog, first, job, pagehandlers, slots, store, targets

BRIEF = "Read the design and start the build."
POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="the fake harness is a script run by its #! line")


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew; no other module reads as installed, turns
    is hidden, and nothing is spawned unless a test says how."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(slots, "links", lambda name: False)
    monkeypatch.setattr(slots, "api", lambda: None)
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: None)
    fake_turns.hide(monkeypatch)

    def no_spawn(argv, **kw):
        raise AssertionError(f"spawned {argv}")

    monkeypatch.setattr(pagehandlers, "_spawn", no_spawn)


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def headless(work: Path, **more) -> dict:
    """A request body for a headless Claude launch in `work`."""
    values = {"harness": "claude", "target": "headless", "cwd": str(work), "brief": BRIEF,
              **more}
    return {"values": values}


def previewed(body: dict) -> dict:
    status, said = post("/api/preview", body)
    assert status == 200, said
    return said


def codes(said: dict) -> list[str]:
    return [r["code"] for r in said["refused"]]


def row(request_id: str) -> store.LaunchRow | None:
    if not store.path().exists():
        return None
    conn = store.connect(create=False)
    try:
        return store.get(conn, request_id)
    finally:
        conn.close()


def requested(request_id: str) -> dict:
    status, said, _ = get("/api/launches", f"request={request_id}")
    assert status == 200 and said["state"] == "up", said
    return said


class Spawned:
    """A stand-in for `proc.spawn` that records each call, and runs the launch job in
    this process when `run` is set."""

    def __init__(self, run: bool = False):
        self.calls: list[dict] = []
        self.run = run
        self.threads: list[threading.Thread] = []

    def __call__(self, argv, **kw):
        self.calls.append({"argv": list(argv), **kw})
        if self.run:
            worker = threading.Thread(target=job.run_request, args=(argv[-1],))
            worker.start()
            self.threads.append(worker)
        return 4242

    def join(self) -> None:
        for worker in self.threads:
            worker.join(60)


# --- the form's values as typed -------------------------------------------------------------

def test_each_value_becomes_its_rows_option_as_the_command_line_spells_it():
    words, direct = pagehandlers.tokens(
        {"harness": "claude", "target": "headless", "scratch": "true",
         "model": "opus", "remote_control": "true", "add_dir": ["/a", "~/b"],
         "extra_flags": ["--verbose", "-x"]}, "claude", lambda name: False)
    assert words == ["--harness=claude", "--target=headless", "--scratch", "--model=opus",
                     "--remote-control", "--add-dir=/a", "--add-dir=~/b",
                     "--", "--verbose", "-x"]
    assert direct == {}
    words, _ = pagehandlers.tokens({"scratch": "false", "remote_control": "mine"}, "claude",
                                   lambda name: False)
    assert words == ["--no-scratch", "--remote-control=mine"]


def test_what_the_command_line_cant_type_is_typed_directly():
    words, direct = pagehandlers.tokens({"remote_control": "false", "add_dir": []},
                                        "claude", lambda name: False)
    assert words == [] and direct == {"remote_control": False, "add_dir": []}
    # A row this install does not offer is refused there too, as the parser refuses it.
    with pytest.raises(catalog.NeedsModule):
        pagehandlers.tokens({"director": []}, "claude", lambda name: False)


@pytest.mark.parametrize("values,row,words", [
    ({"search": "true"}, "search", "not a claude row"),
    ({"scratch": "yes"}, "scratch", "not true or false"),
    ({"cwd": "work"}, "cwd", "relative path"),
    ({"add_dir": ["/a", "b"]}, "add_dir", "relative path"),
])
def test_a_value_the_launch_cant_take_is_refused(values, row, words):
    with pytest.raises(catalog.BadValue) as refused:
        pagehandlers.tokens(values, "claude", lambda name: False)
    assert refused.value.row == row and words in refused.value.problem


def test_the_values_go_through_the_command_lines_parser(work):
    request, command = pagehandlers.launch_request(
        {"seat": "", "values": {"harness": "codex", "cwd": str(work), "search": "true",
                                "add_dir": ["~/x"]}, "off_posture": True}, "r1")
    assert request.typed["search"] is True and request.typed["harness"] == "codex"
    assert request.typed["add_dir"] == [str(Path("~/x").expanduser())]
    assert request.seat is None and request.off_posture and request.request_id == "r1"
    assert request.cwd is None
    assert command[:4] == ["moxi", "launcher", "run", "--off-posture"]
    with pytest.raises(catalog.BadValue):
        pagehandlers.launch_request({"values": {"model": "opus", "harness": "claude",
                                                "target": "nowhere"}}, "r1")


# --- /api/preview ----------------------------------------------------------------------------

@POSIX
def test_a_preview_shows_the_launch_and_issues_a_ticket(work, harness):
    said = previewed(headless(work, model="opus"))
    assert said["refused"] == []
    assert (said["harness"], said["mode"], said["target"]) == ("claude", "print", "headless")
    assert said["argv"][:2] == ["claude", "-p"] and "--model" in said["argv"]
    stamps = {s["name"]: s["value"] for s in said["stamps"]}
    assert stamps["MOXI_LAUNCHER_REQUEST"] == said["request_id"]
    assert said["command"] is None
    rid, expires, mac = said["ticket"].split(".")
    assert rid == said["request_id"] and len(mac) == 64
    assert int(expires) - time.time() == pytest.approx(600, abs=5)
    assert said["expires_at"].endswith("Z")
    # The preview starts and writes nothing of the launch.
    assert harness.runs() == [] and row(rid) is None


def test_a_value_the_page_sends_overrides_the_preset_even_the_shipped_default(work, harness):
    write_presets(f'[project.here]\ncwd = "{work}"\nmodel = "opus"\ntarget = "headless"\n'
                  f'brief = "{BRIEF}"\n')
    shown = previewed({"project_preset": "here"})
    assert shown["refused"] == [] and "opus" in shown["argv"]
    shown = previewed({"project_preset": "here", "values": {"model": ""}})
    assert shown["refused"] == [] and "--model" not in shown["argv"]


def test_a_launch_that_names_no_place_is_refused_no_cwd(harness):
    said = previewed({"values": {"target": "headless", "brief": BRIEF}})
    assert codes(said) == ["no-cwd"] and said["refused"][0]["row"] == "cwd"
    assert said["ticket"] is None and said["request_id"] is None


def test_a_scratch_or_a_project_preset_is_a_place(work, harness):
    assert previewed({"values": {"target": "headless", "brief": BRIEF,
                                 "scratch": "true"}})["refused"] == []
    write_presets(f'[project.here]\ncwd = "{work}"\n')
    said = previewed({"project_preset": "here",
                      "values": {"target": "headless", "brief": BRIEF}})
    assert said["refused"] == []


def test_a_harness_not_on_the_servers_path_is_refused_naming_that_path(work, tmp_path,
                                                                       monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    said = previewed(headless(work))
    [refused] = said["refused"]
    assert refused["code"] == "not-found"
    assert refused["problem"] == pagehandlers.NOT_ON_PATH.format(command="claude",
                                                                 path=str(empty))
    assert said["argv"][:2] == ["claude", "-p"] and said["ticket"] is None


def test_the_preview_shows_every_refusal_check_finds(tmp_path, harness):
    said = previewed({"values": {"harness": "claude", "target": "slot",
                                 "cwd": str(tmp_path / "gone"), "brief": BRIEF}})
    assert set(codes(said)) >= {"no-cwd"} and len(said["refused"]) >= 2
    assert any("slots" in r["problem"] for r in said["refused"])
    assert said["ticket"] is None


def test_a_refusal_before_the_composition_is_answered_alone(work):
    said = previewed({"values": {"harness": "claude", "cwd": str(work), "model": "opus",
                                 "search": "true"}})
    assert codes(said) == ["bad-value"] and said["refused"][0]["row"] == "search"
    assert said["argv"] == [] and said["harness"] is None


def test_a_preview_that_fails_unexpectedly_names_only_the_class(work, monkeypatch):
    def broken(request, **kw):
        raise RuntimeError("/home/someone/private")

    monkeypatch.setattr(pagehandlers.api, "preview", broken)
    status, said = post("/api/preview", headless(work))
    assert status == 200 and codes(said) == ["error"]
    assert said["refused"][0]["problem"] == pagehandlers.UNEXPECTED.format("RuntimeError")
    assert said["ticket"] is None


def test_a_terminal_launch_is_answered_with_the_command_to_copy(work, tmp_path,
                                                               monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # the terminal's PATH is the user's, not ours
    said = previewed({"seat": "", "values": {"cwd": str(work), "model": "opus"},
                      "off_posture": False})
    assert said["refused"] == [] and said["target"] == "terminal"
    assert said["ticket"] is None and said["request_id"] is None
    assert said["command"] == f"moxi launcher run --cwd={work} --model=opus"


def test_the_ticket_key_is_made_once_private_and_replaced_when_bad(work, harness):
    previewed(headless(work))
    key = Path(store.path()).parent / pagehandlers.KEY_FILE
    first_key = key.read_bytes()
    assert len(first_key) == 32 and stat.S_IMODE(key.stat().st_mode) == 0o600
    previewed(headless(work))
    assert key.read_bytes() == first_key
    key.write_bytes(b"short")
    assert len(pagehandlers.page_key()) == 32 and key.read_bytes() != first_key


def test_a_ticket_holds_only_for_its_request_until_it_expires(work):
    body = headless(work)
    issued, _ = pagehandlers.ticket(body, "r1", now=1000.0)
    assert pagehandlers.verify({**body, "ticket": issued}, now=1000.0) == ("r1", None)
    assert pagehandlers.verify({**body, "ticket": issued}, now=1600.0) == ("r1", None)
    _, late = pagehandlers.verify({**body, "ticket": issued}, now=1601.0)
    assert late["code"] == "expired"
    changed = headless(work, model="opus")
    # Each part of the request the ticket was issued for binds it: the values, the seat,
    # the project preset, the posture allowance and the request id.
    for bad in ({**changed, "ticket": issued}, {**body, "seat": "other", "ticket": issued},
                {**body, "project_preset": "other", "ticket": issued},
                {**body, "off_posture": True, "ticket": issued},
                {**body, "ticket": "r2" + issued[2:]},
                {**body, "ticket": "nonsense"}, {**body, "ticket": "../x.1.ab"}):
        rid, refused = pagehandlers.verify(bad, now=1000.0)
        assert rid is None and refused["code"] == "bad-ticket"


# --- /api/launch -----------------------------------------------------------------------------

def ticketed(body: dict, request_id: str = "r1") -> dict:
    return {**body, "ticket": pagehandlers.ticket(body, request_id)[0]}


def test_a_launch_writes_its_request_file_and_spawns_its_job_detached(work, harness,
                                                                      monkeypatch):
    spawned = Spawned()
    monkeypatch.setattr(pagehandlers, "_spawn", spawned)
    said = previewed(headless(work, model="opus"))
    status, answer = post("/api/launch", {**headless(work, model="opus"),
                                          "ticket": said["ticket"]})
    rid = said["request_id"]
    assert (status, answer) == (202, {"status": f"/api/launches?request={rid}"})
    [call] = spawned.calls
    assert Path(call["argv"][0]).name.startswith("moxi-launcher-job")
    assert call["argv"][1:] == ["launch", rid] and call["detached"] is True
    assert call["output"] == targets.run_log(rid)
    assert "MOXI_LAUNCHER_REQUEST" not in call["env"]
    path = targets.request_file(rid)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = json.loads(path.read_text())
    assert data["act"] == "launch" and data["request_id"] == rid
    assert data["typed"]["model"] == "opus" and data["typed"]["cwd"] == str(work)
    # A second use of the ticket is a 409, and spawns nothing.
    status, answer = post("/api/launch", {**headless(work, model="opus"),
                                          "ticket": said["ticket"]})
    assert status == 409 and rid in answer["conflict"]
    assert len(spawned.calls) == 1


def test_a_ticket_used_after_its_job_ended_is_a_409_too(work, harness, monkeypatch):
    spawned = Spawned()
    monkeypatch.setattr(pagehandlers, "_spawn", spawned)
    body = ticketed(headless(work))
    conn = store.connect()
    store.add_event(conn, store.LAUNCH_REFUSED, {"code": "no-cwd"}, request_id="r1")
    conn.close()
    status, answer = post("/api/launch", body)
    assert status == 409 and spawned.calls == []
    assert not targets.request_file("r1").exists()


def test_a_bad_or_expired_ticket_is_refused_and_starts_nothing(work):
    body = headless(work)
    status, said = post("/api/launch", {**body, "ticket": "r1.1.ab"})
    assert status == 200 and said["refused"]["code"] == "bad-ticket"
    issued = pagehandlers.ticket(body, "r1", now=0.0)[0]
    status, said = post("/api/launch", {**body, "ticket": issued})
    assert status == 200 and said["refused"]["code"] == "expired"
    assert not targets.runs_dir().exists() or not list(targets.runs_dir().iterdir())


def test_a_terminal_launch_is_never_run_by_the_page(work):
    body = ticketed({"values": {"cwd": str(work), "model": "opus"}})
    status, said = post("/api/launch", body)
    assert status == 200 and said["refused"]["code"] == "terminal"
    assert said["command"] == f"moxi launcher run --cwd={work} --model=opus"
    assert not targets.request_file("r1").exists()


def test_a_launch_refused_again_at_launch_writes_nothing(harness):
    body = ticketed({"values": {"target": "headless", "brief": BRIEF}})
    status, said = post("/api/launch", body)
    assert status == 200 and said["refused"]["code"] == "no-cwd"
    assert not targets.request_file("r1").exists()


def test_a_job_that_does_not_start_leaves_no_request_file(work, harness, monkeypatch):
    def broken(argv, **kw):
        raise OSError(2, "No such file: '/home/someone/bin/moxi-launcher-job'")

    monkeypatch.setattr(pagehandlers, "_spawn", broken)
    status, said = post("/api/launch", ticketed(headless(work)))
    assert status == 200 and said["refused"]["code"] == targets.SPAWN_FAILED
    assert "someone" not in said["refused"]["problem"]
    assert not targets.request_file("r1").exists()


@POSIX
def test_preview_then_launch_then_the_row_answers_the_status_path(work, harness,
                                                                  monkeypatch):
    spawned = Spawned(run=True)
    monkeypatch.setattr(pagehandlers, "_spawn", spawned)
    body = headless(work)
    said = previewed(body)
    status, answer = post("/api/launch", {**body, "ticket": said["ticket"]})
    assert status == 202
    spawned.join()
    rid = said["request_id"]
    [launched] = requested(rid)["launches"]
    assert launched["request_id"] == rid and launched["state"] == "started"
    assert launched["cwd"] == str(work) and launched["target"] == "headless"
    run = harness.one()
    # What ran is what the preview showed, but the Claude session id each one mints.
    assert run["cwd"] == str(work) and len(run["argv"]) == len(said["argv"])
    assert launched["session_id"] in run["argv"]
    assert [w for w in run["argv"][1:] if not _uuid(w)] == [
        w for w in said["argv"][1:] if not _uuid(w)]
    assert not targets.request_file(rid).exists()


def _uuid(word: str) -> bool:
    try:
        uuid.UUID(word)
    except ValueError:
        return False
    return True


# --- the launch job --------------------------------------------------------------------------

def request_file(request_id: str = "r1", **data) -> Path:
    return job.write_request(request_id, {"act": "launch", "request_id": request_id,
                                          "typed": {}, "extra": [], **data})


@POSIX
def test_the_job_launches_the_request_and_deletes_its_file(work, harness):
    path = request_file(typed={"target": "headless", "cwd": str(work), "brief": BRIEF})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert job.main(["launch", "r1"]) == 0
    got = row("r1")
    assert got.state == "started" and got.cwd == str(work)
    assert harness.one()["cwd"] == str(work)
    assert not path.exists()


def test_a_second_request_file_for_one_id_is_refused():
    request_file()
    with pytest.raises(FileExistsError):
        request_file()


@pytest.mark.parametrize("typed,code", [
    ({"target": "headless", "brief": BRIEF}, "no-cwd"),
    ({"target": "headless", "brief": BRIEF, "cwd": "/nowhere/at/all"}, "no-cwd"),
    ({"cwd": "WORK", "brief": BRIEF}, "not-run"),
    ({"target": "headless", "cwd": "WORK"}, "no-brief"),
    ({"target": "headless", "cwd": "WORK", "brief": BRIEF, "slot": "3"}, "needs-module"),
])
def test_a_refusal_before_the_row_is_recorded_under_the_request_id(typed, code, harness,
                                                                   work):
    typed = {k: str(work) if v == "WORK" else v for k, v in typed.items()}
    path = request_file(typed=typed)
    assert job.run_request("r1") == "refused"
    assert row("r1") is None and not path.exists()
    said = requested("r1")
    assert said["launches"] == [] and said["refused"]["code"] == code
    assert said["refused"]["problem"] and said["refused"]["hint"]
    assert harness.runs() == []


def test_a_launch_past_its_wait_is_recorded_timed_out(work):
    release = threading.Event()

    def slow(request):
        release.wait(30)

    path = request_file(typed={"target": "headless", "cwd": str(work), "brief": BRIEF})
    try:
        assert job.run_request("r1", wait_s=0.2, launch=slow) == "timed-out"
    finally:
        release.set()
    assert requested("r1")["refused"]["code"] == "timed-out"
    assert not path.exists()


def test_a_request_file_that_cant_be_read_is_recorded(monkeypatch):
    path = targets.request_file("r1")
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    assert job.run_request("r1") == "refused"
    assert requested("r1")["refused"]["code"] == "not-run"
    assert not path.exists()
    assert job.run_request("r2") == "refused"  # no file at all


def test_an_unexpected_failure_names_only_its_class(work):
    def broken(request):
        raise RuntimeError("/home/someone/private")

    request_file(typed={"target": "headless", "cwd": str(work), "brief": BRIEF})
    assert job.run_request("r1", launch=broken) == "refused"
    refused = requested("r1")["refused"]
    assert refused["code"] == "error"
    assert refused["problem"] == job.UNEXPECTED_JOB.format("RuntimeError")


def test_the_sweep_takes_an_old_request_file():
    path = request_file()
    old = time.time() - 100 * 86400
    os.utime(path, (old, old))
    swept = targets.sweep(keep_days=90)
    assert path.name in swept.removed and not path.exists()
