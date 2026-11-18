"""How a slot launch ends at its startup screens and at ready, through a fake slots and a
fake turns: a screen on the startup list the launcher won't answer, the bypass warning,
the ready read of every slot launch, and the findings that fail a new session's launch and
only flag a relaunch. The session's pane is never sent a key it wasn't set to send, and
never stopped."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import exit as exits
from moxi.paths import Layout

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, compose, first, seed, startup, store, targets
from moxi_launcher.api import LaunchRequest

TESTS = Path(__file__).resolve().parent
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
BRIEF = "Read the design and start the build."
ROWS = ["  WARNING: Claude Code running in Bypass Permissions mode", "  ❯ No, exit",
        "    Yes, I accept"]

pytestmark = pytest.mark.touches("packages/launcher/src/moxi_launcher/startup.py",
                                 "packages/launcher/src/moxi_launcher/targets.py",
                                 "packages/launcher/src/moxi_launcher/seed.py")


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
    monkeypatch.setenv("MOXI_LAUNCHER_SLOTS", "launcher_fake_slots")
    monkeypatch.setenv(fake.DIR, str(slots_dir))
    monkeypatch.syspath_prepend(str(TESTS))
    fake.reset()
    script = slots_dir / "script.json"

    def plan(**keys):
        now = json.loads(script.read_text()) if script.exists() else {}
        script.write_text(json.dumps({**now, **keys}))

    return SimpleNamespace(dir=slots_dir, plan=plan,
                           calls=lambda name=None: fake.calls(slots_dir, name))


@pytest.fixture
def turns(tmp_path, monkeypatch):
    turns_dir = tmp_path / "fake-turns"
    turns_dir.mkdir()
    monkeypatch.setenv(first.TURNS_ENV, "launcher_fake_turns")
    monkeypatch.setenv(fake_turns.DIR, str(turns_dir))
    monkeypatch.syspath_prepend(str(TESTS))
    script = turns_dir / "script.json"
    return SimpleNamespace(
        dir=turns_dir, plan=lambda **keys: script.write_text(json.dumps(keys)),
        calls=lambda name=None: [c for c in fake_turns.calls(turns_dir)
                                 if name is None or c["call"] == name])


def settings_file(**startup_keys) -> None:
    lines = ["[startup]", "wait_s = 3"]
    lines += [f"{key} = {str(value).lower()}" for key, value in startup_keys.items()]
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def request(work: Path, *, rid: str = "r1", **typed) -> LaunchRequest:
    return LaunchRequest(typed={"target": "slot", **typed}, cwd=str(work), request_id=rid,
                         session_id=SID, actor="ann")


def events(rid: str = "r1", kind: str | None = None) -> list:
    conn = store.connect(create=False)
    try:
        return [e for e in store.events(conn, rid) if kind is None or e.kind == kind]
    finally:
        conn.close()


def row(rid: str = "r1") -> store.LaunchRow:
    conn = store.connect(create=False)
    try:
        return store.get(conn, rid)
    finally:
        conn.close()


def no_key_sent(rig) -> bool:
    """Nothing was pressed, and nothing respawned or started the pane again."""
    return rig.calls("answer_screen") == [] and rig.calls("respawn") == [] \
        and len(rig.calls("start")) <= 1


def direct(work: Path, *, rid: str = "r1", relaunch_of: str | None = None,
           resume_of: str | None = None, brief: str | None = None,
           respawn: bool = False) -> tuple[targets.Started, store.LaunchRow]:
    """A Claude slot launch started on the target itself, its row inserted as a fresh
    launch, a resume or a relaunch would have it."""
    conn = store.connect()
    try:
        for parent in (relaunch_of, resume_of):
            if parent and store.get(conn, parent) is None:
                store.insert(conn, request_id=parent, by="ann", harness="claude",
                             target="slot", cwd=str(work), values={}, argv=["claude"],
                             env={}, posture="",
                             session_id="0b0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e60")
        store.insert(conn, request_id=rid, by="ann", harness="claude", target="slot",
                     cwd=str(work), values={"slot": "1"} if respawn else {},
                     argv=["claude"], env={}, posture="", session_id=SID, brief=brief,
                     relaunch_of=relaunch_of, resume_of=resume_of)
        composition = compose.Composition(
            harness="claude", mode="interactive", target="slot", argv=("claude",), env={},
            values={"cwd": str(work), **({"slot": "1"} if respawn else {})}, first=brief)
        launch = targets.Launch(conn=conn, request_id=rid, composition=composition,
                                argv=composition.argv, command="claude",
                                env=dict(os.environ), cwd=str(work),
                                respawn=respawn)
        started = targets.Slot().start(launch)
        return started, store.get(conn, rid)
    finally:
        conn.close()


# -- a screen the launcher won't answer -----------------------------------------------------


@pytest.mark.parametrize("name, setting", [
    ("CLAUDE_TRUST_DIR", "startup.trust_dialog"),
    ("CLAUDE_DEV_CHANNELS", "startup.dev_channels_dialog"),
    ("CLAUDE_BYPASS_WARNING", "startup.bypass_warning")])
def test_a_screen_whose_setting_is_off_fails_a_fresh_launch_at_once(work, harness, rig,
                                                                    turns, name, setting):
    settings_file()
    rig.plan(frames=[{"screen": name, "rows": ROWS}])
    got = api.launch(request(work, brief=BRIEF))
    assert (got.state, got.reason, got.exit_code) == (
        "failed", startup.SCREEN_NOT_ANSWERED, exits.ERROR)
    assert name in got.line and setting in got.line and "slot 1" in got.line
    assert f"moxi config set launcher {setting} true" in got.line
    # One read saw it; nothing was pressed and no first turn went.
    assert len(rig.calls("read_screen")) == 1 and no_key_sent(rig)
    assert turns.calls("deliver") == []
    [failed] = events(kind="start-failed")
    assert failed.detail["rows"] == ROWS and failed.detail["screen"] == name
    [seen] = events(kind=startup.EVENT)
    assert (seen.detail["screen"], seen.detail["outcome"], seen.detail["setting"]) == (
        name, startup.NOT_ANSWERED, setting)
    assert targets.exit_of(row()) == exits.ERROR


def test_the_bypass_warnings_hint_names_the_terminal_answer(work, harness, rig):
    settings_file()
    rig.plan(frames=[{"screen": "CLAUDE_BYPASS_WARNING"}])
    got = api.launch(request(work))
    assert "in a terminal" in got.line and "~/.claude/settings.json" in got.line


def test_the_bypass_warning_is_answered_only_with_its_setting_on(work, harness, rig):
    settings_file(bypass_warning=True)
    rig.plan(frames=[{"screen": "CLAUDE_BYPASS_WARNING"}, {"screen": None}],
             answers=[{"outcome": "gone", "keys": ["Down", "Enter"]}])
    got = api.launch(request(work))
    assert got.state == "started" and got.exit_code == 0
    [answer] = rig.calls("answer_screen")
    assert answer["screen"] == "CLAUDE_BYPASS_WARNING"
    [seen] = events(kind=startup.EVENT)
    assert seen.detail["outcome"] == "gone"
    # The event names the file Claude Code writes its consent to.
    assert seen.detail["file"] == str(seed.settings_file(os.environ))
    assert seen.detail["file"].endswith("settings.json")


def test_a_screen_whose_setting_is_on_is_answered_and_the_others_stay_unanswered(
        work, harness, rig):
    settings_file(trust_dialog=True)
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_DIR"}, {"screen": "CLAUDE_DEV_CHANNELS"}])
    got = api.launch(request(work))
    assert [a["screen"] for a in rig.calls("answer_screen")] == ["CLAUDE_TRUST_DIR"]
    assert (got.state, got.reason) == ("failed", startup.SCREEN_NOT_ANSWERED)
    assert "CLAUDE_DEV_CHANNELS" in got.line


def test_the_watch_runs_with_every_setting_off(work, harness, rig):
    settings_file()
    got = api.launch(request(work))
    assert rig.calls("read_screen") and rig.calls("answer_screen") == []
    assert got.state == "started"


def test_a_resume_held_on_a_screen_fails_as_a_fresh_launch(work, rig):
    settings_file()
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_DIR"}])
    started, now = direct(work, rid="r2", resume_of="r1")
    assert (started.state, started.reason, started.exit_code) == (
        "failed", startup.SCREEN_NOT_ANSWERED, exits.ERROR)
    assert (now.state, now.reason) == ("failed", startup.SCREEN_NOT_ANSWERED)
    assert no_key_sent(rig)


# -- Claude's first-run screens and the Chrome dialog -----------------------------------------


#: The three screens on the startup list with no setting, with rows from their 2.1.326
#: captures.
NEVER_ANSWERED = {
    "CLAUDE_THEME_PICKER": [" Let's get started.",
                            " Choose the text style that looks best with your terminal",
                            " ❯ ✔ Auto (match terminal)", "     Dark mode"],
    "CLAUDE_WELCOME": [" Welcome to Claude Code v2.1.326", " Security notes:",
                       " Press Enter to continue…"],
    "CLAUDE_CHROME_DIALOG": ["  Claude in Chrome extension detected",
                             "  ❯ No, keep browser tools off", "    Yes, use my browser",
                             "  Enter to confirm · Esc to keep browser tools off"],
}
EVERY_SETTING_ON = {"trust_dialog": True, "dev_channels_dialog": True,
                    "bypass_warning": True}
FIRST_RUN_HINT = ("Claude Code's first-run setup is not done for this config folder: run "
                  "`claude` once in a terminal with the same `CLAUDE_CONFIG_DIR` and "
                  "finish it")
CHROME_HINT = ("answer Claude Code's Claude-in-Chrome question once in a terminal session; "
               "it is not asked again")


def test_the_three_screens_are_on_the_startup_list_with_no_setting():
    assert [name for key, name in startup.SCREENS["claude"] if key is None] == \
        list(NEVER_ANSWERED)
    on = {key: True for key, _ in startup.SCREENS["claude"] if key is not None}
    for entry in startup.listed("claude", fake, on):
        assert entry.answer is (entry.setting is not None), entry.name


def test_the_hints_of_the_three_screens():
    assert startup.HINTS["CLAUDE_THEME_PICKER"] == FIRST_RUN_HINT
    assert startup.HINTS["CLAUDE_WELCOME"] == FIRST_RUN_HINT
    assert startup.HINTS["CLAUDE_CHROME_DIALOG"] == CHROME_HINT


@pytest.mark.parametrize("mode", ["default", "manual", "acceptEdits", "plan", "dontAsk",
                                  "auto", "bypassPermissions"])
@pytest.mark.parametrize("name", list(NEVER_ANSWERED))
def test_a_first_run_screen_or_the_chrome_dialog_fails_the_launch_at_once(
        work, harness, rig, turns, name, mode):
    settings_file(**EVERY_SETTING_ON)
    rows = NEVER_ANSWERED[name]
    rig.plan(frames=[{"screen": name, "rows": rows}])
    asked = LaunchRequest(typed={"target": "slot", "permission_mode": mode},
                          cwd=str(work), request_id="r1", session_id=SID, actor="ann",
                          off_posture=True)
    got = api.launch(asked)
    assert (got.state, got.reason, got.exit_code) == (
        "failed", startup.SCREEN_NOT_ANSWERED, exits.ERROR)
    assert f"the {name} screen showed and the launcher never answers it" in got.line
    assert "slot 1" in got.line
    assert (FIRST_RUN_HINT if name != "CLAUDE_CHROME_DIALOG" else CHROME_HINT) in got.line
    # One read of the watch saw it; nothing was pressed and no first turn went.
    assert len(rig.calls("read_screen")) == 1 and no_key_sent(rig)
    assert turns.calls("deliver") == []
    [seen] = events(kind=startup.EVENT)
    assert (seen.detail["screen"], seen.detail["outcome"], seen.detail["setting"]) == (
        name, startup.NOT_ANSWERED, None)
    assert seen.detail["rows"] == rows
    [failed] = events(kind="start-failed")
    assert failed.detail["screen"] == name


def test_a_relaunch_held_on_the_chrome_dialog_is_flagged(work, rig, turns):
    settings_file(**EVERY_SETTING_ON)
    rig.plan(frames=[{"screen": "CLAUDE_CHROME_DIALOG"}])
    started, now = direct(work, rid="r2", relaunch_of="r1", brief=BRIEF)
    assert (started.state, started.exit_code, started.finding) == (
        "started", 0, startup.SCREEN_NOT_ANSWERED)
    assert now.state == "started" and no_key_sent(rig)
    assert turns.calls("deliver") == []


# -- the ready read ---------------------------------------------------------------------------


def test_a_fresh_launch_owing_no_first_turn_that_never_gets_ready_fails_no_ready(
        work, harness, rig, turns):
    settings_file()
    turns.plan(ready="timeout")
    rig.plan(frames=[{"screen": None, "rows": ["  Pick a theme", "  ❯ Dark"]}])
    got = api.launch(request(work))
    assert (got.state, got.reason, got.exit_code) == ("failed", first.NO_READY,
                                                      exits.ERROR)
    # Both causes a launch can't tell apart: a screen it doesn't know, and no turns.
    assert ("not ready: turns never reported the session ready; a screen the launcher "
            "does not know may be waiting, or turns may not be running in the session; "
            "attach to slot 1") in got.line
    [failed] = events(kind="start-failed")
    assert failed.detail["rows"] == ["  Pick a theme", "  ❯ Dark"]
    assert no_key_sent(rig)
    # The watch asked, and the ready wait asked once more, up to ready.wait_s.
    assert len(turns.calls("wait_ready")) >= 2


def test_a_resume_that_never_gets_ready_fails_no_ready(work, rig, turns):
    settings_file()
    turns.plan(ready="timeout")
    started, now = direct(work, rid="r2", resume_of="r1")
    assert (started.state, started.reason, started.exit_code) == (
        "failed", first.NO_READY, exits.ERROR)
    assert now.state == "failed" and no_key_sent(rig)


def test_a_launch_owing_no_first_turn_that_gets_ready_ends_started(work, harness, rig,
                                                                  turns):
    settings_file()
    got = api.launch(request(work))
    assert (got.state, got.reason, got.exit_code) == ("started", None, 0)
    assert turns.calls("wait_ready") and turns.calls("deliver") == []


def test_without_turns_a_launch_ends_started_ready_unknown(work, harness, rig):
    settings_file()
    got = api.launch(request(work))
    assert (got.state, got.reason, got.exit_code) == ("started", targets.READY_UNKNOWN, 0)
    assert "nothing could confirm that the session is ready" in got.line
    assert "slot 1" in got.line
    assert (row().state, row().reason) == ("started", targets.READY_UNKNOWN)


# -- a relaunch is flagged, never failed, by a finding ----------------------------------------


def test_a_relaunch_held_on_a_screen_is_flagged_and_sends_no_first_turn(work, rig, turns):
    settings_file()
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_DIR", "rows": ROWS}])
    started, now = direct(work, rid="r2", relaunch_of="r1", brief=BRIEF)
    assert (started.state, started.exit_code, started.finding) == (
        "started", 0, startup.SCREEN_NOT_ANSWERED)
    assert now.state == "started"
    assert "flagged screen-not-answered" in started.line and "slot 1" in started.line
    [flag] = events("r2", targets.FLAGGED)
    assert flag.detail["code"] == startup.SCREEN_NOT_ANSWERED
    assert flag.detail["rows"] == ROWS and "startup.trust_dialog" in flag.detail["line"]
    assert events("r2", "start-failed") == []
    assert turns.calls("deliver") == [] and started.outcome is None
    assert no_key_sent(rig)


def test_a_relaunch_held_on_a_screen_slots_could_not_answer_is_flagged(work, rig, turns):
    settings_file(trust_dialog=True)
    turns.plan(ready="timeout")
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_DIR"}],
             answers=[{"outcome": "mismatch", "why": "the option row is missing"}])
    started, now = direct(work, rid="r2", relaunch_of="r1", brief=BRIEF)
    assert (started.state, started.finding) == ("started", startup.SCREEN_MISMATCH)
    assert now.state == "started"
    assert turns.calls("deliver") == []


def test_a_relaunch_that_never_gets_ready_is_flagged_no_ready_and_stays_started(
        work, rig, turns):
    settings_file()
    turns.plan(ready="timeout")
    started, now = direct(work, rid="r2", relaunch_of="r1", brief=BRIEF)
    assert (started.state, started.exit_code, started.finding) == (
        "started", 0, first.NO_READY)
    assert now.state == "started"
    [flag] = events("r2", targets.FLAGGED)
    assert flag.detail["code"] == first.NO_READY and "attach to slot 1" in flag.detail["line"]
    assert turns.calls("deliver") == []


def test_a_relaunch_whose_session_exited_during_the_watch_still_fails(work, rig, turns):
    settings_file()
    turns.plan(ready="timeout")
    rig.plan(frames=[{"screen": None, "dead": True}], dead_status=1)
    started, now = direct(work, rid="r2", relaunch_of="r1", brief=BRIEF)
    assert (started.state, started.reason) == ("failed", startup.EXITED)
    assert started.finding is None and now.state == "failed"


def test_a_relaunch_that_gets_ready_sends_its_first_turn_unflagged(work, rig, turns):
    settings_file()
    started, now = direct(work, rid="r2", relaunch_of="r1", brief=BRIEF)
    assert started.finding is None and started.outcome.outcome == "sent"
    assert events("r2", targets.FLAGGED) == []


# -- every mode launches ----------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["default", "manual", "acceptEdits", "plan", "dontAsk",
                                  "auto", "bypassPermissions"])
@pytest.mark.parametrize("target", ["slot", "terminal"])
def test_no_claude_mode_is_refused(work, harness, rig, mode, target):
    asked = LaunchRequest(typed={"target": target, "permission_mode": mode},
                          cwd=str(work), request_id="r1", session_id=SID, actor="ann",
                          off_posture=True)
    assert api.check(asked) == []
    assert "attended" not in api.preview(asked).values


@pytest.mark.parametrize("approval", ["on-request", "never"])
@pytest.mark.parametrize("target", ["slot", "terminal"])
def test_no_codex_approval_is_refused(work, harness, rig, approval, target):
    asked = LaunchRequest(typed={"harness": "codex", "target": target,
                                 "approval": approval},
                          cwd=str(work), request_id="r1", actor="ann", off_posture=True)
    assert api.check(asked) == []
    assert api.preview(asked).mode == "interactive"
