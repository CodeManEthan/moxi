"""turns' SETUP in its two steps, the plugin folders for one launch (which writes
nothing), and the Claude doctor rows.
`claude` is a stand-in on PATH; no test runs the real one, and none writes the
package's own `endpoint.json` (conftest's `claude_root`)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import moxi_turns as turns
from conftest import write_config
from moxi import paths
from moxi_turns import claude_setup

FAKE_CLAUDE = """#!/bin/sh
echo "$*" >> "$FAKE_CLAUDE_DIR/log"
case "$*" in
  "--version") echo "$FAKE_CLAUDE_VERSION (Claude Code)" ;;
  "plugin marketplace list --json") cat "$FAKE_CLAUDE_DIR/markets.json" ;;
  "plugin list --json") cat "$FAKE_CLAUDE_DIR/plugins.json" ;;
esac
exit 0
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "claude"
    exe.write_text(FAKE_CLAUDE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    state = tmp_path / "state"
    state.mkdir()
    (state / "markets.json").write_text("[]")
    (state / "plugins.json").write_text("[]")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_DIR", str(state))
    monkeypatch.setenv("FAKE_CLAUDE_VERSION", "2.1.296")
    return state


def _log(state: Path) -> list[str]:
    try:
        return (state / "log").read_text().splitlines()
    except OSError:
        return []


def test_no_test_sees_the_package_folder(claude_root):
    package = Path(claude_setup.__file__).resolve().parent / "claude"
    assert claude_setup.claude_root() == claude_root != package


def test_endpoint_json_names_this_environment_script(turns_env, claude_root):
    path = claude_setup.write_endpoint_file()
    assert path == claude_root / "moxi-turns" / "endpoint.json"
    data = json.loads(path.read_text())
    assert data["command"] == str(paths.script("moxi-turns-endpoint"))
    assert Path(data["command"]).is_absolute()


def test_the_config_key_wins_and_must_be_absolute(turns_env, claude_root):
    write_config(turns_env, '[claude]\nendpoint = "/opt/elsewhere/moxi-turns-endpoint"\n')
    claude_setup.write_endpoint_file()
    assert claude_setup.read_endpoint_file()["command"] == "/opt/elsewhere/moxi-turns-endpoint"
    write_config(turns_env, '[claude]\nendpoint = "relative/endpoint"\n')
    with pytest.raises(claude_setup.SetupError):
        claude_setup.write_endpoint_file()


def test_claude_plugin_dirs_returns_both_folders_and_writes_nothing(turns_env, claude_root):
    before = sorted(claude_root.rglob("*"))
    runner, gate = claude_setup.claude_plugin_dirs()
    assert (runner, gate) == ((claude_root / "moxi-turns").resolve(),
                              (claude_root / "moxi-turns-gate").resolve())
    assert sorted(claude_root.rglob("*")) == before


def test_two_setups_at_once_both_write_and_leave_no_temp_file(turns_env, claude_root):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        written = list(pool.map(lambda _: claude_setup.write_endpoint_file(), range(32)))
    runner = claude_root / "moxi-turns"
    assert set(written) == {runner / "endpoint.json"}
    assert claude_setup.read_endpoint_file()["command"] == str(paths.script("moxi-turns-endpoint"))
    assert [p.name for p in runner.iterdir() if "endpoint" in p.name] == ["endpoint.json"]
    assert (runner / "endpoint.json").stat().st_mode & 0o777 == 0o644


def test_a_failed_write_leaves_no_temp_file(turns_env, claude_root, monkeypatch):
    def refuse(src, dst):
        raise OSError("no room")

    monkeypatch.setattr(claude_setup.os, "replace", refuse)
    with pytest.raises(claude_setup.SetupError, match="no room"):
        claude_setup.write_endpoint_file()
    assert [p.name for p in (claude_root / "moxi-turns").iterdir()] == [".claude-plugin"]


def test_a_read_only_runner_folder_is_a_setup_error(turns_env, claude_root):
    runner = claude_root / "moxi-turns"
    runner.chmod(0o555)
    try:
        with pytest.raises(claude_setup.SetupError, match="cannot write"):
            claude_setup.write_endpoint_file()
    finally:
        runner.chmod(0o755)


def test_claude_plugin_dirs_raises_when_it_cannot_set_up(turns_env, claude_root):
    import shutil

    shutil.rmtree(claude_root / "moxi-turns")
    with pytest.raises(turns.TurnsError):
        claude_setup.claude_plugin_dirs()


def test_setup_adds_the_marketplace_and_both_plugins_then_skips(turns_env, claude_root,
                                                                 fake_claude, capsys):
    assert claude_setup.setup() == 0
    log = _log(fake_claude)
    assert f"plugin marketplace add {claude_root.resolve()}" in log
    assert "plugin install moxi-turns@moxi-turns" in log
    assert "plugin install moxi-turns-gate@moxi-turns" in log
    # Done once: the second run skips each step but still rewrites endpoint.json.
    (fake_claude / "markets.json").write_text(json.dumps(
        [{"name": "moxi-turns", "source": "directory", "path": str(claude_root)}]))
    (fake_claude / "plugins.json").write_text(json.dumps(
        [{"id": "moxi-turns@moxi-turns", "scope": "user"},
         {"id": "moxi-turns-gate@moxi-turns", "scope": "user"}]))
    (fake_claude / "log").unlink()
    (claude_root / "moxi-turns" / "endpoint.json").unlink()
    assert claude_setup.setup() == 0
    assert not [line for line in _log(fake_claude) if "add" in line or "install" in line]
    assert (claude_root / "moxi-turns" / "endpoint.json").is_file()


def test_setup_refuses_a_marketplace_of_the_same_name_elsewhere(turns_env, claude_root,
                                                                fake_claude, capsys):
    (fake_claude / "markets.json").write_text(json.dumps(
        [{"name": "moxi-turns", "source": "directory", "path": "/somewhere/else"}]))
    assert claude_setup.setup() == 1
    assert "/somewhere/else" in capsys.readouterr().err
    assert (claude_root / "moxi-turns" / "endpoint.json").is_file()   # step one stands


def test_setup_without_claude_still_writes_endpoint_json(turns_env, claude_root,
                                                         monkeypatch, capsys):
    monkeypatch.setattr(claude_setup, "_claude", lambda: None)
    assert claude_setup.setup() == 1
    assert (claude_root / "moxi-turns" / "endpoint.json").is_file()
    assert "--plugin-dir" in capsys.readouterr().err


def _rows():
    return {row.name: row for row in claude_setup.rows()}


def test_rows_name_the_plugins_endpoint_json_and_the_version(turns_env, claude_root,
                                                             fake_claude):
    rows = _rows()
    assert rows["claude plugin moxi-turns"].state == "ok"
    assert rows["claude plugin moxi-turns-gate"].state == "ok"
    row = rows["claude endpoint.json"]
    # No user-scope install reads it, so a missing file only warns.
    assert row.state == "warn" and "missing" in row.detail
    assert "MOXI_TURNS_ENDPOINT" in row.detail and "user-scope" in row.detail
    assert row.fix == "moxi install turns"
    assert rows["claude code"].state == "ok"
    claude_setup.write_endpoint_file()
    assert _rows()["claude endpoint.json"].state == "ok"
    write_config(turns_env, '[claude]\nendpoint = "/opt/moved/moxi-turns-endpoint"\n')
    assert _rows()["claude endpoint.json"].state == "warn"      # names a command no longer given
    os.environ["FAKE_CLAUDE_VERSION"] = "9.9.9"
    row = _rows()["claude code"]
    assert row.state == "warn" and "9.9.9" in row.detail


def _install_runner_at(fake_claude, scope):
    (fake_claude / "plugins.json").write_text(json.dumps(
        [{"id": "moxi-turns@moxi-turns", "scope": scope},
         {"id": "moxi-turns-gate@moxi-turns", "scope": scope}]))


def test_a_missing_or_stale_endpoint_json_fails_with_a_user_scope_install(
        turns_env, claude_root, fake_claude):
    _install_runner_at(fake_claude, "user")
    row = _rows()["claude endpoint.json"]
    assert row.state == "fail" and "missing" in row.detail and "user scope" in row.detail
    assert row.fix == "moxi install turns"
    claude_setup.write_endpoint_file()
    assert _rows()["claude endpoint.json"].state == "ok"
    write_config(turns_env, '[claude]\nendpoint = "/opt/moved/moxi-turns-endpoint"\n')
    row = _rows()["claude endpoint.json"]
    assert row.state == "fail" and "/opt/moved/moxi-turns-endpoint" in row.detail


def test_a_project_scope_install_only_warns(turns_env, claude_root, fake_claude):
    _install_runner_at(fake_claude, "project")
    assert _rows()["claude endpoint.json"].state == "warn"


def test_a_missing_endpoint_json_warns_when_claude_cannot_say(turns_env, claude_root,
                                                              monkeypatch):
    monkeypatch.setattr(claude_setup, "_claude", lambda: None)
    row = _rows()["claude endpoint.json"]
    assert row.state == "warn" and "could not say" in row.detail


def test_a_missing_endpoint_json_warns_when_the_plugin_list_fails(turns_env, claude_root,
                                                                   fake_claude):
    (fake_claude / "plugins.json").write_text("not json")
    row = _rows()["claude endpoint.json"]
    assert row.state == "warn" and "could not say" in row.detail


def test_no_claude_on_path_is_an_off_row(turns_env, monkeypatch):
    monkeypatch.setattr(claude_setup, "_claude", lambda: None)
    assert _rows()["claude code"].state == "off"
    assert all(row.module == "turns" for row in claude_setup.rows())


def test_check_carries_the_claude_rows(turns_env, fake_claude):
    from moxi_turns import doctor

    names = [row.name for row in doctor.check()]
    assert "claude endpoint.json" in names and "claude code" in names
