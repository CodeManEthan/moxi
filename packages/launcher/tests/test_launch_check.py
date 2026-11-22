"""The launch check a relaunch runs before it touches its session (`job.launch_check`):
the launch's live checks, its composition and its harness on the child's PATH, with the
session's own comms name and slot not counted against it."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, identity, job, slots, store

TESTS = Path(__file__).resolve().parent
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
OTHER = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    fake_turns.hide(monkeypatch)


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def rig(tmp_path, monkeypatch):
    slots_dir = tmp_path / "fake-slots"
    slots_dir.mkdir()
    monkeypatch.setenv(slots.SLOTS_ENV, "launcher_fake_slots")
    monkeypatch.setenv(fake.DIR, str(slots_dir))
    monkeypatch.syspath_prepend(str(TESTS))
    fake.reset()

    def plan(**keys):
        (slots_dir / "script.json").write_text(json.dumps(keys))

    return SimpleNamespace(plan=plan)


def comms_holding(monkeypatch, holder_sid, state="held"):
    def identity_state(name, scope=None):
        return SimpleNamespace(state=state, qualified=name,
                               holder=SimpleNamespace(adapter="claude",
                                                      session_id=holder_sid))

    monkeypatch.setattr(api, "_comms_api", lambda: SimpleNamespace(
        identity_state=identity_state))


def occupied(pane="%9", hold=None):
    return {"3": {"state": "occupied", "launchable": False, "pane_id": pane, "hold": hold,
                  "last": {"at": 1.0, "label": "planner", "meta": {"request_id": "r1"}}}}


def launched(work: Path, *, values=None) -> store.LaunchRow:
    conn = store.connect()
    try:
        store.insert(conn, request_id="r1", by="ann", harness="claude", target="slot",
                     cwd=str(work), values={"cwd": str(work), "target": "slot",
                                            "slot": "3", **(values or {})},
                     argv=["claude"], env={}, posture="operator", session_id=SID, slot="3",
                     identity="planner")
        store.set_facts(conn, "r1", pane_id="%9")
        return store.get(conn, "r1")
    finally:
        conn.close()


def check_request(row, *, replacing=SID, hold=None, model_pin=None):
    request = job.replay_request(row, "run1", mode="director", handoff=None, by="boss",
                                 slot="3", hold=hold, respawn=True, director=True,
                                 model_pin=model_pin)
    return dataclasses.replace(request, replacing=replacing)


def test_the_sessions_own_name_and_slot_pass(work, harness, rig, monkeypatch):
    rig.plan(slots=occupied())
    comms_holding(monkeypatch, SID)
    assert job.launch_check(check_request(launched(work))) is None


def test_the_sessions_own_slot_under_the_runs_own_hold_passes(work, harness, rig,
                                                              monkeypatch):
    rig.plan(slots=occupied(hold={"holder": "run1", "until": 9e9}))
    comms_holding(monkeypatch, SID)
    assert job.launch_check(check_request(launched(work), hold="run1")) is None


def test_without_replacing_the_sessions_own_name_and_slot_are_refused(work, harness, rig,
                                                                     monkeypatch):
    rig.plan(slots=occupied())
    comms_holding(monkeypatch, SID)
    refused = job.launch_check(check_request(launched(work), replacing=None))
    assert refused is not None and refused.code == "identity-taken"
    comms_holding(monkeypatch, None, state="free")
    refused = job.launch_check(check_request(launched(work), replacing=None))
    assert refused is not None and refused.code == "slot-taken"


def test_a_name_held_by_another_session_is_refused(work, harness, rig, monkeypatch):
    rig.plan(slots=occupied())
    comms_holding(monkeypatch, OTHER)
    refused = job.launch_check(check_request(launched(work)))
    assert refused.code == "identity-taken" and OTHER in refused.problem


def test_a_holder_that_reported_no_session_is_refused_in_words_that_say_so(
        work, harness, rig, monkeypatch):
    rig.plan(slots=occupied())
    comms_holding(monkeypatch, None)
    refused = job.launch_check(check_request(launched(work)))
    assert refused.code == "identity-taken"
    assert identity.NO_SESSION_YET in refused.problem
    assert "reported its session" in refused.hint


@pytest.mark.parametrize("slot", [occupied(pane="%4"),
                                  occupied(hold={"holder": "someone", "until": 9e9})])
def test_a_slot_another_pane_or_hold_has_is_refused(work, harness, rig, monkeypatch, slot):
    rig.plan(slots=slot)
    comms_holding(monkeypatch, SID)
    refused = job.launch_check(check_request(launched(work)))
    assert refused is not None and refused.code == "slot-taken"


def test_a_cwd_gone_is_refused(work, harness, rig, monkeypatch):
    rig.plan(slots=occupied())
    comms_holding(monkeypatch, SID)
    row = launched(work)
    work.rmdir()
    refused = job.launch_check(check_request(row))
    assert refused is not None and refused.code == "no-cwd"


def test_a_harness_not_on_the_path_is_refused(work, rig, monkeypatch, tmp_path):
    rig.plan(slots=occupied())
    comms_holding(monkeypatch, SID)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    refused = job.launch_check(check_request(launched(work)))
    assert refused is not None and refused.code == "not-found"


def test_the_check_composes_the_launch_on_its_pin(work, harness, rig, monkeypatch):
    rig.plan(slots=occupied())
    comms_holding(monkeypatch, SID)
    request = check_request(launched(work), model_pin="claude-fable-5-2")
    assert job.launch_check(request) is None
    argv = api.preview(request).argv
    assert argv[argv.index("--model") + 1] == "claude-fable-5-2"


def test_a_check_that_breaks_refuses(work, harness, rig, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(api, "preview", broken)
    rig.plan(slots=occupied())
    comms_holding(monkeypatch, SID)
    refused = job.launch_check(check_request(launched(work)))
    assert refused.code == "launch-check" and "boom" in refused.problem
