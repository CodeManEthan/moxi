"""The folder-trust seed: the one key it writes and where, what it leaves untouched, how it
keeps the file's mode and backs off from a file another writer changed, and which launches
seed at all."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi.paths import Layout

import launcher_starts as starts
import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, seed, startup, store, targets
from moxi_launcher.api import LaunchRequest

TESTS = Path(__file__).resolve().parent
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
#: A Claude Code config with more in it than projects, as a real one has.
CONFIG = {"numStartups": 41, "theme": "dark", "oauthAccount": {"emailAddress": "a@b.c"},
          "projects": {"/elsewhere": {"hasTrustDialogAccepted": True, "allowedTools": []}},
          "tipsHistory": {"x": 3}}

pytestmark = pytest.mark.touches("packages/launcher/src/moxi_launcher/seed.py",
                                 "packages/launcher/src/moxi_launcher/targets.py")


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path.resolve()


def write_config(path: Path, doc=CONFIG, *, mode: int = 0o600, text: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n" if text is None else text,
                    encoding="utf-8")
    path.chmod(mode)
    return path


@pytest.fixture
def env(tmp_path) -> dict:
    """A launch's environment: its own home, and Claude Code's config folder in it."""
    home = tmp_path / "h"
    home.mkdir()
    return {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / "cc"), "PATH":
            os.environ["PATH"]}


def config_of(env) -> Path:
    return Path(env["CLAUDE_CONFIG_DIR"]) / ".claude.json"


# -- the seed itself ----------------------------------------------------------------------


def test_the_seed_writes_exactly_the_one_key_for_the_git_root(tmp_path, env):
    repo = git_repo(tmp_path / "repo")
    path = write_config(config_of(env))
    got = seed.seed(repo, env)
    assert (got.outcome, got.root_dir, got.file) == (seed.SEEDED, str(repo), str(path))
    after = json.loads(path.read_text())
    want = json.loads(json.dumps(CONFIG))
    want["projects"][str(repo)] = {"hasTrustDialogAccepted": True}
    assert after == want


def test_a_subfolder_is_keyed_by_its_git_root_never_a_parent(tmp_path, env):
    repo = git_repo(tmp_path / "repo")
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    write_config(config_of(env))
    got = seed.seed(sub, env)
    assert got.root_dir == str(repo)
    keys = set(json.loads(config_of(env).read_text())["projects"])
    assert keys == {"/elsewhere", str(repo)}


def test_outside_a_repo_the_launch_dir_itself_is_the_key(tmp_path, env):
    plain = tmp_path / "plain" / "inner"
    plain.mkdir(parents=True)
    write_config(config_of(env))
    got = seed.seed(plain, env)
    assert got.root_dir == str(plain.resolve())
    assert str(tmp_path / "plain") not in json.loads(config_of(env).read_text())["projects"]


def test_a_root_already_trusted_is_left_as_it_is(tmp_path, env):
    repo = git_repo(tmp_path / "repo")
    doc = {**CONFIG, "projects": {str(repo): {"hasTrustDialogAccepted": True, "x": 1}}}
    path = write_config(config_of(env), doc)
    before = path.read_bytes()
    assert seed.seed(repo, env).outcome == seed.ALREADY
    assert path.read_bytes() == before


def test_an_entry_with_other_keys_keeps_them(tmp_path, env):
    repo = git_repo(tmp_path / "repo")
    doc = {**CONFIG, "projects": {str(repo): {"hasTrustDialogAccepted": False,
                                              "allowedTools": ["Read"]}}}
    write_config(config_of(env), doc)
    assert seed.seed(repo, env).outcome == seed.SEEDED
    entry = json.loads(config_of(env).read_text())["projects"][str(repo)]
    assert entry == {"hasTrustDialogAccepted": True, "allowedTools": ["Read"]}


def test_the_home_dir_is_never_written(env):
    path = write_config(config_of(env))
    before = path.read_bytes()
    got = seed.seed(env["HOME"], env)
    assert got.outcome == seed.HOME_DIR
    assert path.read_bytes() == before


@pytest.mark.parametrize("text, why", [
    (None, "no such file"),
    ("{not json", "not JSON"),
    ("[1, 2]", "top level is not an object"),
    ('{"projects": []}', "projects is not an object"),
])
def test_a_file_missing_unparseable_or_wrongly_shaped_is_not_touched(tmp_path, env, text,
                                                                     why):
    repo = git_repo(tmp_path / "repo")
    path = config_of(env)
    if text is not None:
        write_config(path, text=text)
    before = path.read_bytes() if text is not None else None
    got = seed.seed(repo, env)
    assert got.outcome == f"seed-refused({why})"
    assert (path.read_bytes() if path.exists() else None) == before
    left = sorted(p.name for p in path.parent.iterdir()) if path.parent.exists() else []
    assert left == ([path.name] if text is not None else [])


def test_an_entry_that_is_not_an_object_is_not_touched(tmp_path, env):
    repo = git_repo(tmp_path / "repo")
    path = write_config(config_of(env), {"projects": {str(repo): "yes"}})
    before = path.read_bytes()
    assert seed.seed(repo, env).outcome.startswith("seed-refused(")
    assert path.read_bytes() == before


def test_without_claude_config_dir_the_file_is_in_home(tmp_path, env):
    del env["CLAUDE_CONFIG_DIR"]
    repo = git_repo(tmp_path / "repo")
    path = write_config(Path(env["HOME"]) / ".claude.json")
    got = seed.seed(repo, env)
    assert (got.outcome, got.file) == (seed.SEEDED, str(path))
    assert seed.settings_file(env) == Path(env["HOME"]) / ".claude" / "settings.json"


def test_the_settings_file_follows_claude_config_dir(env):
    assert seed.settings_file(env) == Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json"


@pytest.mark.parametrize("mode", [0o644, 0o600])
def test_the_write_is_a_rename_beside_the_file_and_keeps_the_mode(tmp_path, env,
                                                                      monkeypatch, mode):
    repo = git_repo(tmp_path / "repo")
    path = write_config(config_of(env), mode=mode)
    renames = []
    real = os.replace

    def replace(src, dst, *args, **kwargs):
        renames.append((Path(src), Path(dst)))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    assert seed.seed(repo, env).outcome == seed.SEEDED
    [(src, dst)] = renames
    assert dst == path and src.parent == path.parent and src != path
    if os.name == "posix":
        assert path.stat().st_mode & 0o7777 == mode
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_a_file_changed_once_between_read_and_rename_is_read_again(tmp_path, env):
    repo = git_repo(tmp_path / "repo")
    path = write_config(config_of(env))
    calls = []

    def writer(p: Path):
        calls.append(p)
        if len(calls) == 1:
            doc = json.loads(p.read_text())
            doc["numStartups"] = 4200
            p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    got = seed.seed(repo, env, between=writer)
    assert got.outcome == seed.SEEDED and len(calls) == 2
    after = json.loads(path.read_text())
    # The other writer's change is kept, and the key added on top of it.
    assert after["numStartups"] == 4200
    assert after["projects"][str(repo)] == {"hasTrustDialogAccepted": True}


def test_a_same_size_write_in_the_same_clock_step_is_seen_as_a_change(tmp_path, env):
    repo = git_repo(tmp_path / "repo")
    path = write_config(config_of(env))
    calls = []

    def writer(p: Path):
        # A write of the same size that leaves the time as it was: what two writes in one
        # step of Windows' clock look like.
        calls.append(p)
        if len(calls) == 1:
            info = p.stat()
            text = p.read_text(encoding="utf-8").replace('"dark"', '"mist"')
            p.write_text(text, encoding="utf-8")
            os.utime(p, ns=(info.st_atime_ns, info.st_mtime_ns))
            assert p.stat().st_size == info.st_size

    got = seed.seed(repo, env, between=writer)
    assert got.outcome == seed.SEEDED and len(calls) == 2
    after = json.loads(path.read_text())
    assert after["theme"] == "mist"
    assert after["projects"][str(repo)] == {"hasTrustDialogAccepted": True}


def test_a_file_changed_twice_is_not_written_and_keeps_the_other_writers_bytes(tmp_path,
                                                                               env):
    repo = git_repo(tmp_path / "repo")
    path = write_config(config_of(env))
    written = []

    def writer(p: Path):
        text = json.dumps({**CONFIG, "numStartups": 500 * 10 ** len(written)},
                          indent=2) + "\n"
        p.write_text(text, encoding="utf-8")
        written.append(text)

    got = seed.seed(repo, env, between=writer)
    assert got.outcome == "seed-refused(changed)"
    assert path.read_text() == written[-1] and len(written) == 2
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


# -- which launches seed --------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    fake_turns.hide(monkeypatch)


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


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

    return SimpleNamespace(plan=plan, calls=lambda name=None: fake.calls(slots_dir, name))


@pytest.fixture
def launch_config() -> Path:
    """The fenced Claude Code config the launch's environment points at."""
    return write_config(Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json")


def trust_setting(*, on: bool) -> None:
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[startup]\nwait_s = 3\ntrust_dialog = {str(on).lower()}\n",
                    encoding="utf-8")


def request(work: Path, **typed) -> LaunchRequest:
    return LaunchRequest(typed={"target": "slot", **typed}, cwd=str(work), request_id="r1",
                         session_id=SID, actor="ann")


def seed_events() -> list:
    conn = store.connect(create=False)
    try:
        return [e for e in store.events(conn, "r1") if e.kind == seed.EVENT]
    finally:
        conn.close()


@pytest.mark.slot_target
def test_a_slot_launch_with_the_setting_on_seeds_before_slots_starts(tmp_path, harness, rig,
                                                                     launch_config,
                                                                     monkeypatch):
    repo = git_repo(tmp_path / "repo")
    trust_setting(on=True)
    seen_at_start = []
    real = fake.start

    def start(*args, **kwargs):
        seen_at_start.append(json.loads(launch_config.read_text())["projects"])
        return real(*args, **kwargs)

    monkeypatch.setattr(fake, "start", start)
    got = api.launch(request(repo))
    assert got.state == "started"
    [projects] = seen_at_start
    assert projects[str(repo)] == {"hasTrustDialogAccepted": True}
    [event] = seed_events()
    assert event.detail == {"folder": str(repo), "file": str(launch_config),
                            "outcome": seed.SEEDED}


@pytest.mark.slot_target
def test_a_trust_screen_after_a_seed_is_answered_and_says_the_seed_missed(
        tmp_path, harness, rig, launch_config):
    repo = git_repo(tmp_path / "repo")
    trust_setting(on=True)
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_DIR"}, {"screen": None}],
             answers=[{"outcome": "gone"}])
    api.launch(request(repo))
    conn = store.connect(create=False)
    try:
        [screen] = [e for e in store.events(conn, "r1") if e.kind == startup.EVENT]
    finally:
        conn.close()
    assert screen.detail["outcome"] == "gone" and screen.detail["seed_missed"] is True
    assert (screen.detail["folder"], screen.detail["file"]) == (str(repo),
                                                                str(launch_config))


@pytest.mark.slot_target
def test_with_the_setting_off_nothing_is_written(tmp_path, harness, rig, launch_config):
    repo = git_repo(tmp_path / "repo")
    trust_setting(on=False)
    before = launch_config.read_bytes()
    rig.plan(frames=[{"screen": "CLAUDE_TRUST_DIR"}])
    got = api.launch(request(repo))
    assert launch_config.read_bytes() == before and seed_events() == []
    assert got.reason == startup.SCREEN_NOT_ANSWERED
    # The event names the folder and the file all the same.
    conn = store.connect(create=False)
    try:
        [screen] = [e for e in store.events(conn, "r1") if e.kind == startup.EVENT]
    finally:
        conn.close()
    assert (screen.detail["folder"], screen.detail["file"]) == (str(repo),
                                                                str(launch_config))


@pytest.mark.slot_target
def test_a_trusted_root_with_the_setting_off_shows_no_screen_and_goes_on(
        tmp_path, harness, rig):
    repo = git_repo(tmp_path / "repo")
    path = write_config(Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json",
                        {"projects": {str(repo): {"hasTrustDialogAccepted": True}}})
    before = path.read_bytes()
    trust_setting(on=False)
    got = api.launch(request(repo))
    assert (got.state, got.exit_code) == ("started", 0)
    assert path.read_bytes() == before and rig.calls("answer_screen") == []


@pytest.mark.parametrize("target", ["terminal", "headless"])
def test_a_terminal_or_headless_launch_writes_nothing(tmp_path, harness, rig, launch_config,
                                                     monkeypatch, target):
    starts.stand_in(monkeypatch, starts.ignored)
    monkeypatch.setattr(targets, "_spawn", lambda *a, **kw: 99)
    repo = git_repo(tmp_path / "repo")
    trust_setting(on=True)
    before = launch_config.read_bytes()
    typed = {"target": target, **({"brief": "go"} if target == "headless" else {})}
    api.launch(LaunchRequest(typed=typed, cwd=str(repo), request_id="r1", session_id=SID,
                             actor="ann"))
    assert launch_config.read_bytes() == before and seed_events() == []


def test_a_refused_launch_writes_nothing(tmp_path, harness, rig, launch_config):
    repo = git_repo(tmp_path / "repo")
    trust_setting(on=True)
    rig.plan(slots={"3": {"state": "occupied", "launchable": False}})
    before = launch_config.read_bytes()
    with pytest.raises(Exception):
        api.launch(request(repo, slot="3"))
    assert launch_config.read_bytes() == before


# -- what a launch changes in harness config ------------------------------------------------


def digest(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


@pytest.mark.parametrize("harness_name", ["claude", "codex"])
@pytest.mark.parametrize("target", ["terminal", "headless",
                                    pytest.param("slot", marks=pytest.mark.slot_target)])
def test_a_launch_changes_only_the_trust_key_and_only_in_a_claude_slot_launch(
        tmp_path, harness, rig, launch_config, monkeypatch, target, harness_name):
    starts.stand_in(monkeypatch, starts.ignored)
    monkeypatch.setattr(targets, "_spawn", lambda *a, **kw: 99)
    repo = git_repo(tmp_path / "repo")
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[startup]\nwait_s = 3\ntrust_dialog = true\nbypass_warning = true\n",
                    encoding="utf-8")
    claude_settings = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
    claude_settings.write_text('{"theme": "dark"}\n')
    codex_config = Path(os.environ["CODEX_HOME"]) / "config.toml"
    codex_config.parent.mkdir(parents=True, exist_ok=True)
    codex_config.write_text('model = "gpt-5"\n')
    kept = {p: digest(p) for p in (claude_settings, codex_config)}
    before = json.loads(launch_config.read_text())
    # The fake slots shows the bypass warning and presses through it, writing nothing.
    rig.plan(frames=[{"screen": "CLAUDE_BYPASS_WARNING"}, {"screen": None}],
             answers=[{"outcome": "gone"}])
    typed = {"target": target, "harness": harness_name,
             **({"brief": "go"} if target == "headless" else {}),
             **({"permission_mode": "bypassPermissions"} if harness_name == "claude"
                else {})}
    api.launch(LaunchRequest(typed=typed, cwd=str(repo), request_id="r1", actor="ann",
                             session_id=SID if harness_name == "claude" else None,
                             off_posture=True))
    assert {p: digest(p) for p in kept} == kept
    after = json.loads(launch_config.read_text())
    if harness_name == "claude" and target == "slot":
        assert after.pop("projects")[str(repo)] == {"hasTrustDialogAccepted": True}
        assert after == {k: v for k, v in before.items() if k != "projects"}
    else:
        assert after == before
