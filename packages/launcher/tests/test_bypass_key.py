"""The bypass warning's skip key: a `bypassPermissions` Claude slot launch passes one
`--settings`, a run file of its own holding `skipDangerousModePermissionPrompt`, merged
with the launch's own `--settings` (a JSON string, or a file read and never changed); a
terminal launch, a headless one, or a slot launch in another mode passes no key; and the
sweep removes the run file with the launch's others."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, catalog, compose, store, targets
from moxi_launcher.api import LaunchRequest
from moxi_launcher.compose import Context, claude, common

TESTS = Path(__file__).resolve().parent
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
KEY = common.SKIP_BYPASS_WARNING
BYPASS = {"permission_mode": "bypassPermissions"}

pytestmark = pytest.mark.touches("packages/launcher/src/moxi_launcher/compose/claude.py",
                                 "packages/launcher/src/moxi_launcher/compose/common.py")


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
    return SimpleNamespace(calls=lambda name=None: fake.calls(slots_dir, name))


def ctx(**fields) -> Context:
    return Context(**{"request_id": "r1", "session_id": SID, "actor": "ann",
                      "runs_dir": "/state/runs", "moxi": "moxi", **fields})


def values(**more):
    return {"harness": "claude", "cwd": "/work", "target": "slot", **more}


def settings_args(argv) -> list[str]:
    """Every `--settings` value on `argv`."""
    argv = list(argv)
    return [argv[i + 1] for i, token in enumerate(argv) if token == catalog.SETTINGS_FLAG]


def launch(work: Path, *, extra=(), **typed) -> api.LaunchResult:
    return api.launch(LaunchRequest(typed={"target": "slot", **typed}, extra=list(extra),
                                    cwd=str(work), request_id="r1", session_id=SID,
                                    actor="ann"))


def run_file(rid: str = "r1") -> Path:
    return targets.runs_dir() / f"{rid}{common.SETTINGS_SUFFIX}"


# -- composed -----------------------------------------------------------------------------------


def test_a_bypass_slot_launch_passes_one_settings_naming_its_run_file():
    got = claude.compose(values(**BYPASS), ctx())
    assert settings_args(got.argv) == ["/state/runs/r1.settings.json"]
    assert got.settings_file.path == "/state/runs/r1.settings.json"
    assert json.loads(got.settings_file.text) == {KEY: True}


def test_the_users_settings_string_is_merged_into_the_run_file_in_its_place():
    own = '{"env": {"A": "1"}}'
    got = claude.compose(values(**BYPASS, extra_flags=["--settings", own, "--verbose"]),
                         ctx(user_settings={"env": {"A": "1"}}))
    assert settings_args(got.argv) == [got.settings_file.path]
    assert own not in got.argv and got.argv[-1] == "--verbose"
    assert json.loads(got.settings_file.text) == {"env": {"A": "1"}, KEY: True}


def test_an_attached_settings_flag_is_replaced_too():
    got = claude.compose(values(**BYPASS, extra_flags=["--settings=/u/s.json"]),
                         ctx(user_settings={"model": "opus"}))
    assert "--settings=/u/s.json" not in got.argv
    assert settings_args(got.argv) == [got.settings_file.path]


@pytest.mark.parametrize("more", [
    {"target": "terminal", **BYPASS},
    {"target": "headless", **BYPASS},
    {"target": "slot", "permission_mode": "default"},
    {"target": "slot", "permission_mode": "acceptEdits"},
    {"target": "slot"},
], ids=["terminal", "headless", "slot-default", "slot-accept-edits", "slot-no-mode"])
def test_no_other_launch_passes_the_key(more):
    got = claude.compose(values(**more, extra_flags=["--settings", "/u/s.json"]),
                         ctx(brief="fix the build"))
    assert got.settings_file is None
    assert settings_args(got.argv) == ["/u/s.json"]


def test_the_users_settings_are_read_only_for_a_launch_that_carries_the_key(tmp_path):
    missing = str(tmp_path / "nowhere.json")
    plain = compose.context(values(extra_flags=["--settings", missing], cwd=str(tmp_path)),
                            request_id="r1", session_id=SID, modules=frozenset())
    assert plain.user_settings is None
    with pytest.raises(common.Unreadable, match="nowhere.json"):
        compose.context(values(**BYPASS, extra_flags=["--settings", missing],
                               cwd=str(tmp_path)),
                        request_id="r1", session_id=SID, modules=frozenset())


@pytest.mark.parametrize("given", ["[1, 2]", "{not json"])
def test_settings_that_are_not_a_json_object_are_refused(tmp_path, given):
    path = tmp_path / "s.json"
    path.write_text(given)
    with pytest.raises(common.Unreadable):
        common.read_settings(str(path), str(tmp_path))
    if given.startswith("{"):
        with pytest.raises(common.Unreadable):
            common.read_settings(given, str(tmp_path))


def test_a_relative_settings_file_is_read_from_the_launch_cwd(tmp_path):
    (tmp_path / "s.json").write_text('{"model": "opus"}')
    assert common.read_settings("s.json", str(tmp_path)) == {"model": "opus"}


# -- launched -----------------------------------------------------------------------------------


def test_a_bypass_slot_launch_writes_its_run_file_with_the_key(work, harness, rig):
    got = launch(work, **BYPASS)
    assert got.state == "started"
    [start] = rig.calls("start")
    assert settings_args(start["argv"]) == [str(run_file())]
    assert json.loads(run_file().read_text()) == {KEY: True}
    assert run_file().stat().st_mode & 0o777 == 0o600


def test_a_users_settings_file_is_read_and_never_changed(work, harness, rig, tmp_path):
    own = tmp_path / "own-settings.json"
    own.write_text('{"env": {"A": "1"}, "model": "opus"}\n')
    before = hashlib.sha256(own.read_bytes()).hexdigest()
    launch(work, extra=["--settings", str(own)], **BYPASS)
    assert hashlib.sha256(own.read_bytes()).hexdigest() == before
    [start] = rig.calls("start")
    assert settings_args(start["argv"]) == [str(run_file())]
    assert json.loads(run_file().read_text()) == {"env": {"A": "1"}, "model": "opus",
                                                  KEY: True}


def test_a_users_settings_string_is_merged(work, harness, rig):
    launch(work, extra=["--settings", '{"model": "opus"}'], **BYPASS)
    [start] = rig.calls("start")
    assert settings_args(start["argv"]) == [str(run_file())]
    assert json.loads(run_file().read_text()) == {"model": "opus", KEY: True}


def test_a_slot_launch_in_another_mode_writes_no_run_file(work, harness, rig):
    launch(work, permission_mode="acceptEdits")
    [start] = rig.calls("start")
    assert settings_args(start["argv"]) == []
    assert not run_file().exists()


def test_the_sweep_removes_the_settings_run_file(work, harness, rig):
    launch(work, **BYPASS)
    old = time.time() - 40 * 86400
    os.utime(run_file(), (old, old))
    conn = store.connect()
    try:
        swept = targets.sweep(conn, keep_days=30)
    finally:
        conn.close()
    assert run_file().name in swept.removed and not run_file().exists()
