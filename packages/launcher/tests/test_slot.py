"""The slot target and the startup screens: a slot launch through a fake slots, with a slot
named and with none, every refusal before and after the row, the first turn sent before the
launch returns, the screens loop's outcomes, and the install questions."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import config, modules
from moxi import exit as exits
from moxi.paths import Layout

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import (api, catalog, compose, first, module, slots, startup, store,
                           targets)
from moxi_launcher.api import LaunchRequest

TESTS = Path(__file__).resolve().parent
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
BRIEF = "Read the design and start the build."


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    """The installed turns hidden in every test here: a test that wants turns fakes it on
    the launcher's seam."""
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
    """The fake slots behind the launcher's seam; `rig.plan(...)` scripts it."""
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

    return SimpleNamespace(dir=directory, plan=plan,
                           calls=lambda name=None: fake.calls(directory, name))


@pytest.fixture
def turns(tmp_path, monkeypatch):
    directory = tmp_path / "fake-turns"
    directory.mkdir()
    monkeypatch.setenv(first.TURNS_SEAM, "launcher_fake_turns")
    monkeypatch.setenv(fake_turns.DIR, str(directory))
    monkeypatch.syspath_prepend(str(TESTS))
    script = directory / "script.json"
    return SimpleNamespace(
        dir=directory, plan=lambda **keys: script.write_text(json.dumps(keys)),
        calls=lambda name=None: [c for c in fake_turns.calls(directory)
                                 if name is None or c["call"] == name])


@pytest.fixture
def no_slots(monkeypatch):
    """slots not installed, whatever this environment holds."""
    monkeypatch.delenv(slots.SEAM, raising=False)
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "slots" and real(name))


def settings_file(text: str) -> None:
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def screens_on(wait_s: int = 2, *, trust: bool = True, dev: bool = False) -> None:
    settings_file(f"[startup]\nwait_s = {wait_s}\ntrust_dialog = {str(trust).lower()}\n"
                  f"dev_channels_dialog = {str(dev).lower()}\n")


def request(work: Path, *, rid: str = "r1", sid: str | None = SID,
            **typed) -> LaunchRequest:
    return LaunchRequest(typed={"target": "slot", **typed}, cwd=str(work), request_id=rid,
                         session_id=sid, actor="ann")


def row(rid: str = "r1") -> store.LaunchRow | None:
    if not store.path().exists():
        return None
    conn = store.connect(create=False)
    try:
        return store.get(conn, rid)
    finally:
        conn.close()


def events(rid: str = "r1", kind: str | None = None) -> list:
    conn = store.connect(create=False)
    try:
        return [e for e in store.events(conn, rid) if kind is None or e.kind == kind]
    finally:
        conn.close()


def nothing_written(rid: str = "r1") -> bool:
    return row(rid) is None and not targets.runs_dir().exists()


# -- a slot launch ----------------------------------------------------------------------


def test_a_launch_naming_no_slot_takes_the_lowest_free_one(work, harness, rig,
                                                           monkeypatch):
    rig.plan(placed=4, pane_pid=5151)
    at_start = []
    real = fake.start

    def start(*args, **kwargs):
        at_start.append(row())
        return real(*args, **kwargs)

    monkeypatch.setattr(fake, "start", start)
    got = api.launch(request(work, model="opus"))
    [call] = rig.calls("start")
    # No slot named: slots takes the lowest launchable one, or makes the next.
    assert call["slot"] is None and call["create"] is True
    assert call["label"] == "launcher:r1"
    assert call["meta"] == {"request_id": "r1", "session_id": SID}
    assert call["cwd"] == str(work)
    # The whole child environment goes to slots, scrubbed and stamped.
    assert call["env"]["MOXI_LAUNCHER_REQUEST"] == "r1"
    assert call["env"]["PATH"].startswith(str(harness.bin))
    # The argv slots runs is preview's.
    assert call["argv"] == list(api.preview(request(work, model="opus")).argv)
    assert call["argv"][0] == "claude"
    # The row was in before the start, with no slot yet; then it records where it went.
    [before] = at_start
    assert (before.state, before.slot, before.target) == ("spawning", None, "slot")
    after = row()
    assert (after.state, after.slot, after.pane_id, after.pid) == ("started", "4", "%7",
                                                                  5151)
    assert (got.state, got.exit_code, got.slot, got.target) == ("started", 0, "4", "slot")
    assert "slot 4" in got.line
    # A launch that names no slot reads none with show before it starts.
    assert [c["slot"] for c in rig.calls("show")] == [4]


@pytest.mark.parametrize("given, asked", [("3", 3), ("review", "review")])
def test_a_launch_naming_a_slot_goes_only_there(work, harness, rig, given, asked):
    rig.plan(slots={str(asked): {"state": "free", "launchable": True}})
    got = api.launch(request(work, slot=given))
    [call] = rig.calls("start")
    assert call["slot"] == asked and call["create"] is False
    # check read the slot before anything was written.
    assert rig.calls("show")[0]["slot"] == asked
    assert row().slot == str(asked) and got.slot == str(asked)
    assert got.state == "started" and got.exit_code == 0


def test_a_retried_launch_into_the_slot_its_first_run_took_gets_the_stored_outcome(
        work, harness, rig):
    rig.plan(slots={"3": {"state": "free", "launchable": True}})
    first_ = api.launch(request(work, slot="3"))
    calls = len(rig.calls())
    # The first run's session now occupies the slot the retry names.
    rig.plan(slots={"3": OCCUPIED})
    again = api.launch(request(work, slot="3"))
    assert not first_.duplicate and again.duplicate
    assert (again.state, again.exit_code, again.slot) == ("started", 0, "3")
    # slots is not asked again, and nothing more is started.
    assert len(rig.calls()) == calls and len(rig.calls("start")) == 1


def test_the_named_slot_is_on_the_row_from_its_insert(work, harness, rig, monkeypatch):
    rig.plan(slots={"3": {"state": "ended", "launchable": True}})
    seen = []
    real = fake.start
    monkeypatch.setattr(fake, "start", lambda *a, **kw: (seen.append(row().slot),
                                                         real(*a, **kw))[1])
    api.launch(request(work, slot="3"))
    assert seen == ["3"]


def test_the_harness_runs_in_the_slot_with_the_launch_env(work, harness, rig):
    rig.plan(run=True)
    got = api.launch(request(work))
    run = harness.one()
    assert run["cwd"] == str(work)
    assert [Path(run["argv"][0]).name, *run["argv"][1:]] == list(
        api.preview(request(work)).argv)
    assert run["env"]["MOXI_LAUNCHER_REQUEST"] == "r1"
    assert got.pid == run["pid"] == row().pid
    assert row().pid_started is not None


def test_an_unconfirmed_start_is_started_and_says_so(work, harness, rig):
    rig.plan(exec="unconfirmed", exec_line="taken")
    got = api.launch(request(work))
    assert (got.state, got.exit_code, got.reason) == ("started", 0, targets.UNCONFIRMED)
    assert "could not confirm" in got.line and "taken" in got.line
    assert (row().state, row().reason) == ("started", targets.UNCONFIRMED)
    assert events(kind="start-failed") == []


def test_an_unconfirmed_start_waits_for_ready_and_sends_the_first_turn(work, harness, rig,
                                                                      turns):
    """`unconfirmed` is never `spawn-failed`: the ready wait decides, as for `started`."""
    rig.plan(exec="unconfirmed", exec_line="taken")
    got = api.launch(request(work, brief=BRIEF))
    assert [c["session_id"] for c in turns.calls("wait_ready")] == [SID]
    assert len(turns.calls("deliver")) == 1
    assert (got.state, got.exit_code) == ("first-sent", 0)
    assert "could not confirm" in got.line


def test_an_unconfirmed_start_whose_session_never_gets_ready_ends_no_ready(work, harness,
                                                                          rig, turns):
    rig.plan(exec="unconfirmed", exec_line="taken")
    turns.plan(ready="timeout")
    got = api.launch(request(work, brief=BRIEF))
    assert (got.state, got.reason) == ("failed", first.NO_READY)


def test_a_scratch_launch_gives_slots_an_absolute_cwd(harness, rig, tmp_path,
                                                      monkeypatch):
    monkeypatch.chdir(tmp_path)
    got = api.launch(LaunchRequest(typed={"target": "slot", "scratch": True},
                                   request_id="r1", session_id=SID, actor="ann"))
    [call] = rig.calls("start")
    assert os.path.isabs(call["cwd"])
    assert call["cwd"] == str(compose.common.scratch_dir("r1"))
    assert got.state == "started"


def common_root_name() -> str:
    return compose.common.scratch_root().name


def test_a_relative_temp_folder_still_gives_an_absolute_scratch_dir(tmp_path,
                                                                   monkeypatch):
    import tempfile

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: ".")
    path = compose.common.scratch_dir("r9")
    assert path.is_absolute() and path == tmp_path / common_root_name() / "r9"


def test_a_command_that_did_not_start_fails_the_row_with_slots_exec_line(work, harness,
                                                                         rig):
    rig.plan(exec="failed", exec_line="failed: execve: permission denied", dead_status=127)
    got = api.launch(request(work))
    assert (got.state, got.reason, got.exit_code) == ("failed", targets.SPAWN_FAILED,
                                                      exits.ERROR)
    assert "permission denied" in got.line and "127" in got.line
    stored = row()
    assert (stored.state, stored.slot, stored.pane_id) == ("failed", "1", "%7")
    [failed] = events(kind="start-failed")
    assert "execve" in failed.detail["why"]


# -- refusals before the row ------------------------------------------------------------


def test_without_slots_a_slot_launch_is_refused_naming_the_other_targets(work, harness,
                                                                          no_slots):
    with pytest.raises(targets.NoSlots) as raised:
        api.launch(request(work, brief=BRIEF))
    refused = raised.value
    assert api.exit_code(refused) == exits.REFUSED and refused.code == "no-slots"
    assert "terminal" in refused.hint and "headless" in refused.hint
    assert nothing_written() and harness.runs() == []
    [listed] = api.check(request(work))
    assert type(listed) is targets.NoSlots and nothing_written()


def test_on_native_windows_a_slot_launch_is_refused(work, harness, rig, monkeypatch):
    monkeypatch.setattr(targets, "WINDOWS", True)
    with pytest.raises(targets.SlotsUnsupported) as raised:
        api.launch(request(work))
    assert api.exit_code(raised.value) == exits.REFUSED
    assert "Windows" in raised.value.problem
    assert "terminal" in raised.value.hint and "headless" in raised.value.hint
    assert nothing_written() and rig.calls("start") == []


def test_where_slots_is_not_supported_a_slot_launch_is_refused(work, harness, rig):
    rig.plan(supported={"ok": False, "why": "tmux 3.0 is older than 3.2"})
    with pytest.raises(targets.SlotsUnsupported) as raised:
        api.launch(request(work))
    assert api.exit_code(raised.value) == exits.REFUSED
    assert "tmux 3.0" in raised.value.problem
    assert "terminal" in raised.value.hint and "headless" in raised.value.hint
    assert nothing_written()


HELD = {"state": "free", "launchable": False,
        "hold": {"holder": "relaunch-77", "until": 1900000000}}
OCCUPIED = {"state": "occupied", "launchable": False, "label": "launcher:r0",
            "last": {"at": 1890000000.0, "label": "launcher:r0",
                     "meta": {"request_id": "r0", "session_id": "s-0"}}}
OCCUPIED_AND_HELD = {**OCCUPIED, "hold": {"holder": "relaunch-77", "until": 1900000000}}


@pytest.mark.parametrize("entry, words", [
    (HELD, ["slot 3 is held by relaunch-77 until"]),
    (OCCUPIED, ["slot 3 is occupied by launcher:r0", "request_id r0", "session_id s-0"]),
    # As slots' own refusal at start: occupied wins, naming the last launch, not the hold.
    (OCCUPIED_AND_HELD, ["slot 3 is occupied by launcher:r0", "request_id r0"]),
])
def test_a_named_slot_that_is_not_launchable_is_refused_naming_its_holder(
        work, harness, rig, entry, words):
    rig.plan(slots={"3": entry} if entry else {})
    with pytest.raises(targets.SlotTaken) as raised:
        api.launch(request(work, slot="3"))
    refused = raised.value
    assert api.exit_code(refused) == exits.REFUSED
    for word in words:
        assert word in refused.problem
    if entry is OCCUPIED_AND_HELD:
        assert "relaunch-77" not in refused.problem
    assert refused.hint
    assert nothing_written() and rig.calls("start") == []
    [listed] = api.check(request(work, slot="3"))
    assert type(listed) is targets.SlotTaken


@pytest.mark.parametrize("entry, words, hint", [
    ({"state": "missing", "launchable": False}, "slot 3 is missing: its tmux session is gone",
     "moxi slots create 3"),
    (None, "there is no slot 3", "moxi slots create"),
    # A live session named like the slot but with no registry row: slots reads it not
    # launchable, whatever its state, and its start refuses it no-slot. Not taken.
    ({"state": "free", "launchable": False, "created": None},
     "there is no slot 3 (a session of that name runs with no registry row)",
     "moxi slots create 3 makes it"),
    ({"state": "occupied", "launchable": False, "created": None, "label": "vim"},
     "there is no slot 3 (a session of that name runs with no registry row)",
     "moxi slots create 3 makes it"),
])
def test_a_named_slot_slots_has_no_slot_for_is_refused_as_missing(work, harness, rig,
                                                                   entry, words, hint):
    rig.plan(slots={"3": entry} if entry else {})
    with pytest.raises(targets.SlotMissing) as raised:
        api.launch(request(work, slot="3"))
    refused = raised.value
    assert refused.code == "slot-missing" and api.exit_code(refused) == exits.REFUSED
    assert words in refused.problem and hint in refused.hint
    assert "taken" not in refused.problem and "occupied" not in refused.problem
    assert nothing_written() and rig.calls("start") == []
    [listed] = api.check(request(work, slot="3"))
    assert type(listed) is targets.SlotMissing


@pytest.mark.parametrize("how, kind, code, words", [
    ("try-again", targets.SlotsBusy, exits.TEMPFAIL, "not answering"),
    ("error", targets.SlotsFailed, exits.ERROR, "TmuxError: tmux list-panes failed"),
    ("registry", targets.SlotsFailed, exits.ERROR, "RegistryError: the slots registry"),
    ("usage", catalog.BadValue, exits.USAGE, "lower-case letters")])
def test_slots_that_cant_show_the_named_slot_refuse_the_launch(work, harness, rig, how,
                                                               kind, code, words):
    rig.plan(show=how)
    with pytest.raises(kind) as raised:
        api.launch(request(work, slot="3"))
    assert api.exit_code(raised.value) == code and nothing_written()
    assert words in raised.value.problem
    [listed] = api.check(request(work, slot="3"))
    assert type(listed) is kind


def test_a_harness_missing_from_path_is_refused_before_the_row(work, rig, monkeypatch):
    monkeypatch.setenv("PATH", str(work))
    with pytest.raises(targets.NoCommand):
        api.launch(request(work))
    assert nothing_written() and rig.calls("start") == []


# -- refusals at the start, after the row -------------------------------------------------


def test_a_slot_taken_since_check_ends_the_row_failed_naming_the_holder(work, harness,
                                                                        rig):
    rig.plan(start="refused", refused={
        "reason": "occupied", "words": "slot 1 is occupied",
        "holder": {"label": "launcher:r0", "meta": {"request_id": "r0"}}})
    got = api.launch(request(work))
    assert (got.state, got.reason, got.exit_code) == ("failed", "occupied", exits.REFUSED)
    assert "launcher:r0" in got.line and "request_id r0" in got.line
    assert (row().state, row().reason) == ("failed", "occupied")
    # The same request again starts nothing and gives the stored outcome.
    again = api.launch(request(work))
    assert again.duplicate and again.exit_code == exits.REFUSED
    assert len(rig.calls("start")) == 1


def test_an_occupied_slot_that_is_also_held_names_the_last_launch(work, harness, rig):
    """slots refuses it `occupied`, and its holder is the last launch's label and meta."""
    rig.plan(start="refused", refused={
        "reason": "occupied", "words": "slot 1 runs a launch (launcher:r0); it ends "
                                       "through its own session",
        "holder": {"label": "launcher:r0", "meta": {"request_id": "r0",
                                                    "session_id": "s-0"}}})
    got = api.launch(request(work))
    assert (got.reason, got.exit_code) == ("occupied", exits.REFUSED)
    assert "slot 1 runs a launch" in got.line
    assert "(occupied by launcher:r0 (request_id r0, session_id s-0))" in got.line


def test_a_held_slot_at_start_names_the_hold(work, harness, rig):
    rig.plan(start="refused", refused={
        "reason": "held", "words": "slot 1 is held",
        "holder": {"holder": "relaunch-77", "until": 1900000000}})
    got = api.launch(request(work))
    assert (got.reason, got.exit_code) == ("held", exits.REFUSED)
    assert "relaunch-77" in got.line


def test_a_refusal_naming_nobody_gives_slots_words(work, harness, rig):
    rig.plan(start="refused", refused={"reason": "no-free-slot",
                                       "words": "every slot is taken"})
    got = api.launch(request(work))
    assert (got.state, got.reason, got.exit_code) == ("failed", "no-free-slot",
                                                      exits.REFUSED)
    assert "every slot is taken" in got.line and " by " not in got.line


def test_slots_lock_held_too_long_ends_the_row_try_again(work, harness, rig):
    rig.plan(start="try-again")
    got = api.launch(request(work))
    assert (got.state, got.reason, got.exit_code) == ("failed", targets.TRY_AGAIN,
                                                      exits.TEMPFAIL)
    assert targets.exit_of(row()) == exits.TEMPFAIL
    assert len(rig.calls("start")) == 1  # nothing retries


@pytest.mark.parametrize("how, words", [
    ("error", "TmuxError: tmux new-window failed"),
    ("registry", "RegistryError: the slots registry can't be read")])
def test_a_slots_failure_at_start_ends_the_row_spawn_failed_with_slots_words(
        work, harness, rig, how, words):
    rig.plan(start=how)
    got = api.launch(request(work))
    assert (got.state, got.reason, got.exit_code) == ("failed", targets.SPAWN_FAILED,
                                                      exits.ERROR)
    assert words in got.line
    [failed] = events(kind="start-failed")
    assert words in failed.detail["why"]
    assert (row().state, row().reason) == ("failed", targets.SPAWN_FAILED)


# -- the first turn -----------------------------------------------------------------------


def test_a_slot_launch_sends_the_first_turn_before_it_returns(work, harness, rig, turns):
    got = api.launch(request(work, brief=BRIEF))
    [ready] = turns.calls("wait_ready")
    assert ready["session_id"] == SID
    [given] = turns.calls("deliver")
    assert given["ref"] == "launcher:r1" and BRIEF in given["text"]
    # The brief is the first turn, not on the slot's argv.
    [call] = rig.calls("start")
    assert not any(BRIEF in word for word in call["argv"])
    assert (got.state, got.exit_code) == ("first-sent", 0)
    assert got.outcome is not None and got.outcome.outcome == "sent"
    assert "slot 1" in got.line and "delivered" in got.line
    assert row().state == "first-sent"


def test_a_slot_launch_that_never_gets_ready_ends_no_ready(work, harness, rig, turns):
    turns.plan(ready="timeout")
    got = api.launch(request(work, brief=BRIEF))
    assert (got.state, got.reason, got.exit_code) == ("failed", first.NO_READY,
                                                      exits.ERROR)
    assert turns.calls("deliver") == []


def test_without_turns_the_brief_is_on_the_slots_argv(work, harness, rig):
    got = api.launch(request(work, brief=BRIEF))
    [call] = rig.calls("start")
    assert call["argv"][1].startswith("Brief from launcher, launched by ann")
    assert got.outcome is None and got.state == "started"


def test_a_codex_slot_launch_answers_no_screen_and_says_what_may_be_waiting(work, rig,
                                                                          monkeypatch):
    """No Codex screen is declared yet: a Codex slot launch reads no screen, even with the
    startup settings on, and one whose session never got ready says Codex's trust prompt
    may be waiting for a person."""
    screens_on()
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_FOLDER"}])
    conn = store.connect()
    store.insert(conn, request_id="r1", actor="ann", harness="codex", target="slot",
                 cwd=str(work), values={}, argv=["moxi-turns-codex-host"], env={},
                 posture="", brief=BRIEF)
    sent = []

    def send(conn, rid, *, pid=None, turns=None):
        sent.append(pid)
        return first.Outcome(rid, "no-ready", "failed", first.NO_READY, "not ready")

    composition = compose.Composition(
        harness="codex", mode="host", target="slot", argv=("moxi-turns-codex-host",),
        env={}, values={"cwd": str(work)}, first=BRIEF)
    launch = targets.Launch(conn=conn, request_id="r1", composition=composition,
                            argv=composition.argv, command="moxi-turns-codex-host",
                            env={}, cwd=str(work))
    monkeypatch.setattr(first, "send", send)
    try:
        started = targets.Slot().start(launch)
    finally:
        conn.close()
    assert rig.calls("read_screen") == [] and rig.calls("answer_screen") == []
    assert sent == [4343]  # the pane's pid finds Codex's host
    assert started.exit_code == exits.ERROR
    assert "folder-trust prompt" in started.line and "slot 1" in started.line
    assert startup.screens("codex", fake, {"startup.trust_dialog": True,
                                           "startup.dev_channels_dialog": True}) == []


# -- startup screens, through a launch ------------------------------------------------------


def test_with_the_settings_off_no_screen_is_read(work, harness, rig):
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_FOLDER"}])
    got = api.launch(request(work))
    assert rig.calls("read_screen") == [] and got.state == "started"


def test_a_trust_screen_that_shows_is_answered_and_recorded(work, harness, rig):
    screens_on(2)
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_FOLDER"}, {"screen": None}],
             answers=[{"outcome": "gone"}])
    got = api.launch(request(work))
    reads = rig.calls("read_screen")
    assert reads[0]["screens"] == ["CLAUDE_TRUST_FOLDER"] and reads[0]["slot"] == 1
    [answer] = rig.calls("answer_screen")
    assert answer["screen"] == "CLAUDE_TRUST_FOLDER"
    # The answer is bounded by what remains of startup.wait_s, never more than 5 s.
    assert 1 <= answer["wait"] <= 2
    [seen] = events(kind=startup.EVENT)
    assert (seen.detail["screen"], seen.detail["outcome"]) == ("CLAUDE_TRUST_FOLDER",
                                                              "gone")
    # Without turns the loop runs to its bound, and the launch goes on.
    assert len(reads) > 2
    assert (got.state, got.exit_code) == ("started", 0)


def test_both_screens_are_watched_when_both_settings_are_on(work, harness, rig):
    screens_on(1, dev=True)
    api.launch(request(work))
    assert rig.calls("read_screen")[0]["screens"] == ["CLAUDE_TRUST_FOLDER",
                                                      "CLAUDE_DEV_CHANNELS"]


def test_a_screen_that_is_not_the_one_declared_fails_screen_mismatch_at_once(
        work, harness, rig, turns):
    screens_on(3)
    turns.plan(ready="timeout")
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_FOLDER"}],
             answers=[{"outcome": "mismatch", "why": "the option row is missing",
                       "capture": "/state/slots/mismatches/1.txt"}])
    got = api.launch(request(work, brief=BRIEF))
    assert (got.state, got.reason, got.exit_code) == ("failed", startup.SCREEN_MISMATCH,
                                                      exits.ERROR)
    assert "/state/slots/mismatches/1.txt" in got.line
    assert "the option row is missing" in got.line
    [failed] = events(kind="start-failed")
    assert failed.detail["capture"] == "/state/slots/mismatches/1.txt"
    # Asked once and not again: nothing more is sent, no further read, no first turn.
    assert len(rig.calls("answer_screen")) == 1 and len(rig.calls("read_screen")) == 1
    [seen] = events(kind=startup.EVENT)
    assert seen.detail["outcome"] == "mismatch"
    assert turns.calls("deliver") == []
    assert targets.exit_of(row()) == exits.ERROR


def test_a_screen_not_settled_until_the_bound_runs_out_lets_the_launch_go_on(
        work, harness, rig):
    screens_on(2)
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_FOLDER"}],
             answers=[{"outcome": "mismatch", "why": "not-settled: the marker moved",
                       "capture": "/state/slots/mismatches/2.txt"}])
    got = api.launch(request(work))
    # The watch ends at its bound with not-settled recorded, and the launch goes on.
    assert (got.state, got.exit_code, got.reason) == ("started", 0, None)
    assert len(rig.calls("answer_screen")) > 1
    seen = events(kind=startup.EVENT)
    assert {e.detail["outcome"] for e in seen} == {startup.NOT_SETTLED}
    assert {e.detail["answer"] for e in seen} == {"mismatch"}
    assert events(kind="start-failed") == []


def test_a_screen_not_settled_whose_screen_left_is_not_a_failure(work, harness, rig):
    screens_on(2)
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_FOLDER"}, {"screen": None}],
             answers=[{"outcome": "mismatch", "why": "not-settled"}])
    got = api.launch(request(work))
    assert got.state == "started"
    [seen] = events(kind=startup.EVENT)
    assert seen.detail["outcome"] == startup.NOT_SETTLED
    assert len(rig.calls("answer_screen")) == 1


def test_a_screen_not_settled_then_answered_lets_the_launch_go_on(work, harness, rig):
    screens_on(3)
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_FOLDER"}, {"screen": "CLAUDE_TRUST_FOLDER"},
                     {"screen": None}],
             answers=[{"outcome": "mismatch",
                       "why": "not-settled: the marker stayed on No, exit for 2 s"},
                      {"outcome": "gone"}])
    got = api.launch(request(work))
    assert got.state == "started" and got.exit_code == 0
    first_answer, second = events(kind=startup.EVENT)
    assert first_answer.detail["outcome"] == startup.NOT_SETTLED
    assert "stayed on" in first_answer.detail["why"]
    assert second.detail["outcome"] == "gone"
    assert row().reason is None


def test_a_screen_not_settled_then_an_ended_pane_fails_exited(work, harness, rig, turns):
    screens_on(3)
    turns.plan(ready="timeout")
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_FOLDER"}], dead_status=1,
             answers=[{"outcome": "mismatch", "why": "not-settled: x"},
                      {"outcome": "ended"}])
    got = api.launch(request(work, brief=BRIEF))
    assert (got.state, got.reason) == ("failed", startup.EXITED)
    assert "pane status 1" in got.line
    assert [e.detail["outcome"] for e in events(kind=startup.EVENT)] == [
        startup.NOT_SETTLED, "ended"]
    assert turns.calls("deliver") == []


@pytest.mark.parametrize("why", ["the screen was still there 5 s after Enter",
                                 "not-settled: the marker did not move"])
def test_a_mismatch_whose_pane_died_fails_exited(work, harness, rig, turns, why):
    """A session that died after Enter on the trust screen comes back `mismatch` with
    the last read dead: it exited, it did not show another screen."""
    screens_on(3)
    turns.plan(ready="timeout")
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_FOLDER"}], dead_status=1,
             answers=[{"outcome": "mismatch", "why": why, "dead": True,
                       "keys": ["Down", "Enter"]}])
    got = api.launch(request(work, brief=BRIEF))
    assert (got.state, got.reason, got.exit_code) == ("failed", startup.EXITED,
                                                      exits.ERROR)
    assert "pane status 1" in got.line
    assert len(rig.calls("answer_screen")) == 1
    [seen] = events(kind=startup.EVENT)
    assert seen.detail["keys"] == ["Down", "Enter"]
    assert turns.calls("deliver") == []


def test_a_pane_that_died_at_startup_fails_exited(work, harness, rig, turns):
    screens_on(2)
    turns.plan(ready="timeout")
    rig.plan(frames=[{"screen": None, "dead": True}], dead_status=1)
    got = api.launch(request(work, brief=BRIEF))
    assert (got.state, got.reason, got.exit_code) == ("failed", startup.EXITED,
                                                      exits.ERROR)
    assert "pane status 1" in got.line
    assert turns.calls("deliver") == []


def test_with_turns_the_loop_ends_when_the_session_is_ready(work, harness, rig, turns):
    screens_on(20)
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_FOLDER"}, {"screen": None}])
    got = api.launch(request(work, brief=BRIEF))
    assert len(rig.calls("read_screen")) == 2
    assert got.state == "first-sent"
    # Asked by the minted id, then again by the sender.
    assert [c["session_id"] for c in turns.calls("wait_ready")] == [SID, SID]


# -- the loop itself, on a clock of its own -------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def loop_api(clock: Clock, frames: list, answers: list, *, answer_takes: float = 0.0,
             fail_read: Exception | None = None, status: int | None = None,
             overrun: float = 0.0):
    """slots' screen calls on `clock`: each read gives the next of `frames` (`screen`, a
    screen constant's name or None, and `dead`), each answer the next of `answers`, and
    `show` the pane's exit `status`. An answer takes `answer_takes` seconds, at most its
    `wait`, plus `overrun` (slots' wait for a screen to leave after Enter)."""
    seen = SimpleNamespace(reads=[], answers=[])

    def read_screen(slot, screens):
        if fail_read is not None:
            raise fail_read
        seen.reads.append(clock.now)
        frame = frames[min(len(seen.reads) - 1, len(frames) - 1)]
        name = frame.get("screen")
        return fake.Seen(fake.SCREENS[name] if name else None, True, "",
                         dead=frame.get("dead", False), pane_id="%7")

    def answer_screen(slot, screen, wait=5):
        seen.answers.append(wait)
        answer = answers[min(len(seen.answers) - 1, len(answers) - 1)]
        clock.now += min(answer_takes, wait) + overrun
        last = fake.Seen(screen, False, answer.get("why", ""),
                         dead=answer.get("dead", False), pane_id="%7")
        return fake.Answer(answer["outcome"], screen, answer.get("why", ""), last,
                           answer.get("capture"), tuple(answer.get("keys", ())))

    def show(slot):
        return fake.make_slot(slot, "occupied", dead_status=status)

    seen.api = SimpleNamespace(
        read_screen=read_screen, answer_screen=answer_screen, show=show,
        SlotsError=fake.SlotsError, Refused=fake.Refused, TryAgain=fake.TryAgain,
        TmuxError=fake.TmuxError, RegistryError=fake.RegistryError,
        UsageError=fake.UsageError)
    return seen


@pytest.fixture
def conn():
    connection = store.connect()
    store.insert(connection, request_id="r1", actor="ann", harness="claude",
                 target="slot", cwd="/w", values={}, argv=["claude"], env={}, posture="",
                 session_id=SID)
    yield connection
    connection.close()


TRUST = [("CLAUDE_TRUST_FOLDER", fake.CLAUDE_TRUST_FOLDER)]
SHOWING = {"screen": "CLAUDE_TRUST_FOLDER"}
NONE = {"screen": None}


def watch(conn, clock, seen, **kw):
    return startup.watch(conn, "r1", 1, TRUST, api=seen.api, clock=clock,
                         sleep=clock.sleep, **kw)


def test_a_screen_that_never_shows_runs_the_loop_to_its_bound(conn):
    clock = Clock()
    seen = loop_api(clock, [NONE], [])
    got = watch(conn, clock, seen, wait_s=20)
    assert got.ended == "bound" and not got.failed
    # Every 250 ms, sending nothing.
    assert len(seen.reads) == 80 and seen.answers == []
    assert seen.reads[1] - seen.reads[0] == pytest.approx(0.25)


def test_the_loop_ends_once_the_session_is_ready(conn):
    clock = Clock()
    seen = loop_api(clock, [NONE], [])
    asked = []

    def ready(timeout):
        asked.append(timeout)
        clock.now += timeout
        if len(asked) < 3:
            raise TimeoutError("not yet")
        return SimpleNamespace(session_id=SID)

    got = watch(conn, clock, seen, wait_s=20, ready=ready)
    assert got.ended == "ready" and len(seen.reads) == 3
    assert asked == [0.25, 0.25, 0.25]


def test_each_answer_gets_what_remains_of_the_bound_at_most_5_s(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING], [{"outcome": "gone"}], answer_takes=4.0)
    got = watch(conn, clock, seen, wait_s=12)
    # 12 s: answers at 0 (wait 5), 4 (5), 8 (4); at 12 the bound is spent.
    assert seen.answers == [5.0, 5.0, 4.0]
    assert got.ended == "bound"
    assert [a for _, a in got.answered] == ["gone", "gone", "gone"]


def test_an_answer_that_runs_past_its_wait_is_measured_on_the_clock(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING], [{"outcome": "gone"}], answer_takes=5.0,
                    overrun=5.0)
    got = watch(conn, clock, seen, wait_s=12)
    # Asked at 0 (wait 5), back at 10 with 2 s left: asked again (wait 2), back at 17,
    # 5 s past the bound, and nothing more is asked.
    assert seen.answers == [5.0, 2.0] and got.ended == "bound"
    assert clock.now == pytest.approx(17.0)


def test_no_answer_is_started_with_less_than_a_second_left(conn):
    clock = Clock()
    seen = loop_api(clock, [NONE, SHOWING], [{"outcome": "gone"}])
    got = watch(conn, clock, seen, wait_s=1, interval=0.25)
    # The screen shows at 0.25 s, with 0.75 s left: too little to answer, and since slots
    # never said it could not answer it, the loop just ends at its bound.
    assert seen.answers == [] and got.ended == "bound" and not got.failed


NOT_SETTLED = {"outcome": "mismatch", "why": "not-settled: two rows marked",
               "capture": "/c"}


def test_a_screen_not_settled_is_asked_again_until_the_bound_runs_out(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING], [NOT_SETTLED], answer_takes=2.0)
    got = watch(conn, clock, seen, wait_s=6)
    # Asked at 0 (wait 5), at 2.25 (3.75) and at 4.5 (1.5), one interval after each
    # answer; at 6 the bound is spent. The watch ends there, not failed: the ready wait
    # decides.
    assert seen.answers == [5.0, 3.75, 1.5]
    assert (got.ended, got.reason, got.capture) == ("bound", None, None)
    assert [a for _, a in got.answered] == [startup.NOT_SETTLED] * 3
    detail = [e.detail for e in store.events(conn, "r1")]
    assert [d["outcome"] for d in detail] == [startup.NOT_SETTLED] * 3
    assert {d["answer"] for d in detail} == {"mismatch"}
    assert {d["capture"] for d in detail} == {"/c"}


def test_a_screen_not_settled_with_under_a_second_left_is_not_asked_again(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING], [NOT_SETTLED], answer_takes=2.5)
    got = watch(conn, clock, seen, wait_s=3)
    # Asked at 0 (wait 3); at 2.75 only 0.25 s is left, and the screen still shows.
    assert seen.answers == [3.0]
    assert got.ended == "bound" and not got.failed


def test_a_screen_not_settled_then_answered_goes_on(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING, SHOWING, NONE],
                    [{"outcome": "mismatch", "why": "not-settled: the marker did not move"},
                     {"outcome": "gone"}], answer_takes=2.0)
    got = watch(conn, clock, seen, wait_s=6)
    assert got.ended == "bound" and not got.failed
    assert got.answered == (("CLAUDE_TRUST_FOLDER", startup.NOT_SETTLED),
                            ("CLAUDE_TRUST_FOLDER", "gone"))
    detail = [e.detail for e in store.events(conn, "r1")]
    assert [d["outcome"] for d in detail] == [startup.NOT_SETTLED, "gone"]
    assert detail[0]["why"] == "not-settled: the marker did not move"


def test_a_screen_not_settled_whose_screen_left_ends_at_the_bound(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING, NONE], [NOT_SETTLED], answer_takes=1.0)
    got = watch(conn, clock, seen, wait_s=3)
    assert seen.answers == [3.0]
    assert got.ended == "bound" and not got.failed and got.capture is None


def test_a_screen_not_settled_then_an_ended_pane_fails_exited_with_its_status(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING], [NOT_SETTLED, {"outcome": "ended"}],
                    answer_takes=1.0, status=0)
    got = watch(conn, clock, seen, wait_s=10)
    assert (got.ended, got.reason) == ("failed", startup.EXITED)
    assert "pane status 0" in got.why and len(seen.answers) == 2
    assert [a for _, a in got.answered] == [startup.NOT_SETTLED, "ended"]


@pytest.mark.parametrize("why", [
    "the option row is missing",
    "",
    "the screen was not-settled",  # the word must lead
    "not-settledness",             # and be the whole word
    "Not-settled: two rows",
])
def test_any_other_mismatch_fails_screen_mismatch_at_once(conn, why):
    clock = Clock()
    seen = loop_api(clock, [SHOWING], [{"outcome": "mismatch", "why": why,
                                        "capture": "/c"}], answer_takes=1.0)
    got = watch(conn, clock, seen, wait_s=20)
    # One read, one answer, then nothing more: no pause, no read, no second answer.
    assert seen.answers == [5.0] and len(seen.reads) == 1
    assert (got.ended, got.reason, got.capture) == ("failed", startup.SCREEN_MISMATCH,
                                                     "/c")
    assert "CLAUDE_TRUST_FOLDER" in got.why and why in got.why
    assert got.answered == (("CLAUDE_TRUST_FOLDER", "mismatch"),)
    [event] = store.events(conn, "r1")
    assert event.detail["outcome"] == "mismatch" and "answer" not in event.detail


@pytest.mark.parametrize("why", ["not-settled", "not-settled: two rows marked",
                                 "not-settled (2 s)", "not-settled. the marker moved"])
def test_a_why_led_by_the_word_not_settled_is_not_settled(conn, why):
    clock = Clock()
    seen = loop_api(clock, [SHOWING, NONE], [{"outcome": "mismatch", "why": why}])
    got = watch(conn, clock, seen, wait_s=2)
    assert got.ended == "bound" and not got.failed
    assert got.answered == (("CLAUDE_TRUST_FOLDER", startup.NOT_SETTLED),)


def test_not_ended_goes_on_watching(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING, NONE], [{"outcome": "not-ended"}])
    got = watch(conn, clock, seen, wait_s=2)
    assert got.ended == "bound" and not got.failed
    assert got.answered == (("CLAUDE_TRUST_FOLDER", "not-ended"),)


def test_an_answer_word_the_loop_does_not_know_stops_the_watch(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING], [{"outcome": "sideways"}])
    got = watch(conn, clock, seen, wait_s=20)
    assert got.ended == "stopped" and not got.failed and "sideways" in got.why


@pytest.mark.parametrize("frames, answers, status, why", [
    ([{"screen": None, "dead": True}], [], None, "exited before it was ready"),
    ([{"screen": None, "dead": True}], [], 137, "pane status 137"),
    ([{"screen": None, "dead": True}], [], 0, "pane status 0"),
    ([SHOWING], [{"outcome": "ended"}], 1, "pane status 1"),
    ([SHOWING], [{"outcome": "ended-without-screen"}], 1, "pane status 1"),
    ([SHOWING], [{"outcome": "no-pane"}], None, "exited before it was ready"),
    ([SHOWING, {"screen": None, "dead": True}], [NOT_SETTLED], 0, "pane status 0"),
    ([SHOWING], [{"outcome": "mismatch", "why": "the screen was still there", "dead": True}],
     1, "pane status 1"),
    ([SHOWING], [{**NOT_SETTLED, "dead": True}], None, "exited before it was ready"),
])
def test_a_dead_or_gone_pane_fails_exited(conn, frames, answers, status, why):
    clock = Clock()
    seen = loop_api(clock, frames, answers, status=status)
    got = watch(conn, clock, seen, wait_s=20)
    assert (got.ended, got.reason) == ("failed", startup.EXITED) and why in got.why


def test_a_pane_that_is_alive_is_not_dead_whatever_its_last_status(conn):
    clock = Clock()
    seen = loop_api(clock, [{"screen": None, "dead": False}], [], status=0)
    assert watch(conn, clock, seen, wait_s=1).ended == "bound"


def test_a_read_matches_the_screen_object_slots_gives(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING, NONE], [{"outcome": "gone"}])
    got = watch(conn, clock, seen, wait_s=2)
    assert got.answered == (("CLAUDE_TRUST_FOLDER", "gone"),)
    # Another screen than the ones enabled is not answered.
    clock = Clock()
    other = loop_api(clock, [{"screen": "CLAUDE_EXIT_DIALOG"}], [{"outcome": "gone"}])
    assert watch(conn, clock, other, wait_s=1).ended == "bound"
    assert other.answers == []


@pytest.mark.parametrize("error", [fake.TmuxError("tmux capture-pane failed"),
                                   fake.TryAgain("the server is not answering yet"),
                                   fake.RegistryError("the slots registry can't be read"),
                                   fake.Refused("no-slot", "there is no slot 1")])
def test_slots_that_cant_be_read_stop_the_loop_without_failing(conn, error):
    clock = Clock()
    seen = loop_api(clock, [], [], fail_read=error)
    got = watch(conn, clock, seen, wait_s=20)
    assert got.ended == "stopped" and not got.failed and str(error) in got.why
    [event] = store.events(conn, "r1")
    assert event.kind == startup.EVENT and event.detail["outcome"] == "stopped"


@pytest.mark.parametrize("error", [fake.TmuxError("tmux send-keys failed"),
                                   fake.RegistryError("the slots registry can't be read")])
def test_slots_that_cant_answer_stop_the_loop_without_failing(conn, error):
    clock = Clock()
    seen = loop_api(clock, [SHOWING], [])

    def answer_screen(slot, screen, wait=5):
        raise error

    seen.api.answer_screen = answer_screen
    got = watch(conn, clock, seen, wait_s=20)
    assert got.ended == "stopped" and not got.failed and str(error) in got.why


def test_the_keys_slots_sent_are_recorded(conn):
    clock = Clock()
    seen = loop_api(clock, [SHOWING, NONE], [{"outcome": "gone", "keys": ["Down", "Enter"]}])
    watch(conn, clock, seen, wait_s=2)
    [event] = store.events(conn, "r1")
    assert event.detail["keys"] == ["Down", "Enter"]


def test_the_enabled_screens_follow_the_settings():
    on = {"startup.trust_dialog": True, "startup.dev_channels_dialog": False}
    assert startup.screens("claude", fake, on) == [
        ("CLAUDE_TRUST_FOLDER", fake.CLAUDE_TRUST_FOLDER)]
    both = {"startup.trust_dialog": True, "startup.dev_channels_dialog": True}
    assert startup.screens("claude", fake, both) == [
        ("CLAUDE_TRUST_FOLDER", fake.CLAUDE_TRUST_FOLDER),
        ("CLAUDE_DEV_CHANNELS", fake.CLAUDE_DEV_CHANNELS)]
    off = {"startup.trust_dialog": False, "startup.dev_channels_dialog": False}
    assert startup.screens("claude", fake, off) == []


def test_the_screens_are_read_from_slots_api_module_only():
    both = {"startup.trust_dialog": True, "startup.dev_channels_dialog": True}
    elsewhere = SimpleNamespace(screens=SimpleNamespace(
        CLAUDE_TRUST_FOLDER=fake.CLAUDE_TRUST_FOLDER,
        CLAUDE_DEV_CHANNELS=fake.CLAUDE_DEV_CHANNELS))
    with pytest.raises(AttributeError):
        startup.screens("claude", elsewhere, both)


# -- the install questions ------------------------------------------------------------------


def test_the_announcement_names_the_setup_and_the_seam():
    assert module.SETUP == "moxi_launcher.startup:setup"
    assert slots.SEAM in module.INTERNAL_ENV


def test_without_slots_setup_asks_nothing(no_slots):
    asked, out = [], io.StringIO()
    assert startup.setup(ask=lambda q: asked.append(q) or True, out=out) == 0
    assert asked == [] and out.getvalue() == ""
    assert not Layout("launcher").config_file().exists()


def test_with_slots_setup_asks_the_two_questions_once(rig):
    asked, out = [], io.StringIO()
    answers = iter([True, False])
    assert startup.setup(ask=lambda q: asked.append(q) or next(answers), out=out) == 0
    assert len(asked) == 2
    assert "folder-trust" in asked[0] and "development-channels" in asked[1]
    from moxi_launcher import settings

    now = settings.current()
    assert now["startup.trust_dialog"] is True
    assert now["startup.dev_channels_dialog"] is False
    assert now.source("startup.dev_channels_dialog") == config.FILE
    assert "moxi config set launcher startup.dev_channels_dialog true" in out.getvalue()
    # An install again asks nothing: both were answered, and nothing is reset.
    again = []
    assert startup.setup(ask=lambda q: again.append(q) or False, out=io.StringIO()) == 0
    assert again == [] and settings.current()["startup.trust_dialog"] is True


def test_with_nobody_at_the_keyboard_both_stay_off_and_the_lines_are_printed(
        rig, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    out = io.StringIO()
    assert startup.setup(out=out) == 0
    text = out.getvalue()
    assert "moxi config set launcher startup.trust_dialog true" in text
    assert "moxi config set launcher startup.dev_channels_dialog true" in text
    assert not Layout("launcher").config_file().exists()


#: How the core's installer runs a `SETUP` target: import it, call it, exit with its code.
RUN_SETUP = """\
import importlib, sys
module, _, attr = sys.argv[1].partition(":")
result = getattr(importlib.import_module(module), attr)()
sys.exit(result if isinstance(result, int) and not isinstance(result, bool) else 0)
"""


def test_the_core_runs_setup_in_a_process_of_its_own(rig, tmp_path):
    """As `moxi install launcher` runs a module's `SETUP`: its target, in a fresh
    process, with nobody at the keyboard."""
    done = subprocess.run(
        [sys.executable, "-c", RUN_SETUP, module.SETUP], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(TESTS)})
    assert done.returncode == 0, done.stderr
    assert "moxi config set launcher startup.trust_dialog true" in done.stdout


# -- the fake against slots as built ----------------------------------------------------------

#: Every name the launcher reads from slots' API module.
USED = ("supported", "show", "start", "read_screen", "answer_screen", "SlotsError",
        "Refused", "TryAgain", "TmuxError", "RegistryError", "UsageError",
        "CLAUDE_TRUST_FOLDER", "CLAUDE_DEV_CHANNELS")
#: The shapes the launcher reads, as the fake gives them.
SHAPES = ("Support", "Hold", "Last", "Slot", "Placed", "Screen", "Seen", "Answer")


@pytest.fixture
def real_slots(monkeypatch):
    """slots' own API module; skips when slots is not installed."""
    monkeypatch.delenv(slots.SEAM, raising=False)
    if not modules.available("slots"):
        pytest.skip("slots is not installed")
    found = slots.api()
    assert found is not None, "slots is installed but its API did not load"
    return found


def _model(api_module):
    """The module slots' API takes its shapes and words from, reached through the API,
    since the launcher reads slots only through its API module."""
    return sys.modules[api_module.Answer.__module__]


def _fields(cls) -> list[tuple[str, object]]:
    import dataclasses

    return [(f.name, f.default) for f in dataclasses.fields(cls)]


def test_the_launcher_reads_only_names_slots_api_has(real_slots):
    assert real_slots.__name__ == "moxi_slots.api"
    missing = [name for name in USED if not hasattr(real_slots, name)]
    assert missing == []
    assert startup.screens("claude", real_slots, {"startup.trust_dialog": True,
                                                  "startup.dev_channels_dialog": True}) == [
        ("CLAUDE_TRUST_FOLDER", real_slots.CLAUDE_TRUST_FOLDER),
        ("CLAUDE_DEV_CHANNELS", real_slots.CLAUDE_DEV_CHANNELS)]


def test_the_fakes_shapes_are_slots_own(real_slots):
    for name in SHAPES:
        assert _fields(getattr(fake, name)) == _fields(getattr(real_slots, name)), name
    assert _fields(fake.Items) == _fields(type(real_slots.CLAUDE_EXIT_DIALOG.items))
    assert fake.REASONS == real_slots.REASONS


def test_the_fakes_errors_are_slots_own(real_slots):
    for name in ("Refused", "TryAgain", "TmuxError", "RegistryError", "UsageError"):
        assert issubclass(getattr(real_slots, name), real_slots.SlotsError), name
        assert issubclass(getattr(fake, name), fake.SlotsError), name
    assert issubclass(real_slots.UsageError, ValueError)
    assert issubclass(fake.UsageError, ValueError)
    refused = real_slots.Refused("held", "slot 2 is held", {"holder": "x", "until": 1.0})
    assert (refused.reason, refused.words, refused.holder) == (
        "held", "slot 2 is held", {"holder": "x", "until": 1.0})


def test_the_fakes_calls_take_slots_own_arguments(real_slots):
    import inspect

    for name in ("supported", "show", "start", "read_screen", "answer_screen"):
        mine = inspect.signature(getattr(fake, name)).parameters
        theirs = inspect.signature(getattr(real_slots, name)).parameters
        assert [(p.name, p.kind, p.default) for p in mine.values()] == [
            (p.name, p.kind, p.default) for p in theirs.values()], name


def test_the_fakes_screens_are_slots_own(real_slots):
    for name in fake.SCREENS:
        mine, theirs = getattr(fake, name), getattr(real_slots, name)
        assert mine.name == theirs.name == name
        assert (mine.phrases, mine.option, mine.move, mine.then, mine.marker) == (
            theirs.phrases, theirs.option, theirs.move, theirs.then, theirs.marker)
        assert (mine.items is None) == (theirs.items is None)
        if mine.items is not None:
            assert (mine.items.after, mine.items.kinds) == (theirs.items.after,
                                                            theirs.items.kinds)


def test_not_settled_is_slots_word(real_slots):
    model = _model(real_slots)
    assert model.NOT_SETTLED == startup.NOT_SETTLED
    assert not hasattr(real_slots, "NOT_SETTLED") or (
        real_slots.NOT_SETTLED == startup.NOT_SETTLED)
    assert startup.GO_ON | startup.PANE_ENDED | {startup.MISMATCH} == set(
        model.ANSWER_OUTCOMES)
    assert {targets.STARTED, targets.UNCONFIRMED} < set(model.EXEC_OUTCOMES)
