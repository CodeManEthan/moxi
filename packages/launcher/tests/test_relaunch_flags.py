"""A relaunch run through the launch itself: the job's respawn launches the new session
into a fake slots with a fake turns, and what the launch finds at the session's startup
screens or at ready flags the relaunch and never fails it. A self run held on a screen or
never ready ends `first-turn-not-sent` with the row left `started`; a director's run waits
for ready and ends `relaunched`, flagged; a session that exited during the watch is still
`relaunch-failed`. The new session's pane is never sent a key it wasn't set to send."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi.paths import Layout

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, first, job, startup, store, targets
from moxi_launcher.api import LaunchRequest
from test_relaunch_job import SETTINGS, Clock, FakeSession, FakeSlots, FakeTurns, me

TESTS = Path(__file__).resolve().parent
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
#: The ready wait the launches here are given, in seconds.
READY_WAIT_S = 0.2

pytestmark = pytest.mark.touches("packages/launcher/src/moxi_launcher/job.py",
                                 "packages/launcher/src/moxi_launcher/notices.py",
                                 "packages/launcher/src/moxi_launcher/targets.py")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A fake slots and a fake turns for the launch, the fake harness on PATH, and the
    launcher's config: a short watch and ready wait."""
    Harness.on_path(tmp_path, monkeypatch)
    slots_dir = tmp_path / "fake-slots"
    turns_dir = tmp_path / "fake-turns"
    slots_dir.mkdir()
    turns_dir.mkdir()
    monkeypatch.setenv("MOXI_LAUNCHER_SLOTS", "launcher_fake_slots")
    monkeypatch.setenv(fake.DIR, str(slots_dir))
    monkeypatch.setenv(first.TURNS_ENV, "launcher_fake_turns")
    monkeypatch.setenv(fake_turns.DIR, str(turns_dir))
    monkeypatch.syspath_prepend(str(TESTS))
    fake.reset()
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[startup]\nwait_s = 2\n\n[ready]\nwait_s = {READY_WAIT_S}\n",
                    encoding="utf-8")
    script = slots_dir / "script.json"

    def plan(**keys):
        now = json.loads(script.read_text()) if script.exists() else {}
        script.write_text(json.dumps({**now, **keys}))

    def forget():
        """Drop the calls made so far, the first launch's."""
        for calls_dir in (slots_dir, turns_dir):
            (calls_dir / "calls.jsonl").unlink(missing_ok=True)

    return SimpleNamespace(
        plan=plan, forget=forget, plan_turns=lambda **keys: (turns_dir / "script.json").write_text(
            json.dumps(keys)),
        calls=lambda name=None: fake.calls(slots_dir, name),
        turns_calls=lambda name=None: [c for c in fake_turns.calls(turns_dir)
                                       if name is None or c["call"] == name])


@pytest.fixture
def conn(rig, tmp_path):
    """The launch log, holding r1, a Claude launch started in slot 3, ended since and held
    by the run the test arms."""
    work = tmp_path / "work"
    work.mkdir()
    rig.plan(slots={"3": {"state": "free", "launchable": True}})
    api.launch(LaunchRequest(typed={"target": "slot", "slot": "3"}, cwd=str(work),
                             request_id="r1", session_id=SID, actor="ann"))
    conn = store.connect()
    store.set_facts(conn, "r1", pane_id="%9")
    # The session turns finds is the launch's: its claim names this test's process.
    conn.execute("UPDATE launches SET endpoint = ? WHERE request_id = 'r1'", (me(),))
    yield conn
    conn.close()


def arm(conn, tmp_path, mode):
    handoff = tmp_path / "handoff.md"
    handoff.write_text("where the work stands\n")
    return store.insert_run(conn, kind="relaunch", mode=mode, request_id="r1",
                            session_id=SID, by="planner" if mode == "self" else "boss",
                            handoff_path=str(handoff) if mode == "self" else None,
                            slot="3", pane_id="%9", turn_number=4, job="slots:relaunch-x")


def relaunch(conn, rig, run):
    """Take the run's steps with the launch itself as its respawn; the new launch's row."""
    rig.plan(slots={"3": {"state": "ended", "launchable": False,
                          "hold": {"holder": run.run_id, "until": 1.9e9}}})
    rig.forget()
    clock = Clock()
    outcome = job.RelaunchJob(conn, run.run_id, turns=FakeTurns(FakeSession()),
                              slots_api=FakeSlots(ended=True), comms=None,
                              settings=SETTINGS, clock=clock, sleep=clock.sleep).go()
    ended = store.get_run(conn, run.run_id)
    new = store.get(conn, ended.new_request_id) if ended.new_request_id else None
    return outcome, ended, new


def flags(conn, request_id):
    return [e for e in store.events(conn, request_id) if e.kind == targets.FLAGGED]


def notice(conn, run):
    [told] = [e for e in store.run_events(conn, run.run_id) if e.kind == "notice"]
    return told.detail


def test_a_self_relaunch_held_on_a_screen_ends_first_turn_not_sent_at_once(conn, rig,
                                                                         tmp_path):
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_DIR", "rows": ["  Do you trust ..."]}])
    run = arm(conn, tmp_path, "self")
    outcome, ended, new = relaunch(conn, rig, run)
    assert outcome == "first-turn-not-sent" and ended.word == startup.SCREEN_NOT_ANSWERED
    assert "slot 3" in ended.detail and "startup.trust_dialog" in ended.detail
    assert new.state == "started"
    [flag] = flags(conn, new.request_id)
    assert flag.detail["code"] == startup.SCREEN_NOT_ANSWERED
    assert rig.turns_calls("deliver") == [] and rig.calls("answer_screen") == []
    assert "first-turn-not-sent" in notice(conn, ended)["text"]


def test_a_self_relaunch_never_ready_ends_first_turn_not_sent_and_stays_started(
        conn, rig, tmp_path):
    rig.plan_turns(ready="timeout")
    run = arm(conn, tmp_path, "self")
    outcome, ended, new = relaunch(conn, rig, run)
    assert outcome == "first-turn-not-sent" and ended.word == first.NO_READY
    assert new.state == "started"
    assert [f.detail["code"] for f in flags(conn, new.request_id)] == [first.NO_READY]
    assert rig.turns_calls("deliver") == []
    assert notice(conn, ended)["text"]


def test_a_directors_relaunch_waits_for_ready_and_ends_relaunched_flagged(conn, rig,
                                                                       tmp_path):
    rig.plan_turns(ready="timeout")
    run = arm(conn, tmp_path, "director")
    outcome, ended, new = relaunch(conn, rig, run)
    assert outcome == "relaunched" and ended.word == first.NO_READY
    assert new.state == "started"
    [wait] = rig.turns_calls("wait_ready")
    assert wait["timeout"] == READY_WAIT_S
    assert "ended relaunched: no-ready" in notice(conn, ended)["text"]


def test_a_directors_relaunch_that_gets_ready_is_not_flagged(conn, rig, tmp_path):
    run = arm(conn, tmp_path, "director")
    outcome, ended, new = relaunch(conn, rig, run)
    assert outcome == "relaunched" and ended.word is None
    assert flags(conn, new.request_id) == []
    assert rig.turns_calls("wait_ready")
    assert [e for e in store.run_events(conn, run.run_id) if e.kind == "notice"] == []


def test_a_relaunch_whose_session_exits_during_the_watch_is_relaunch_failed(conn, rig,
                                                                          tmp_path):
    rig.plan_turns(ready="timeout")
    rig.plan(frames=[{"screen": None, "dead": True}], dead_status=1)
    run = arm(conn, tmp_path, "self")
    outcome, ended, new = relaunch(conn, rig, run)
    assert outcome == "relaunch-failed" and ended.word == startup.EXITED
    assert new.state == "failed" and flags(conn, new.request_id) == []
