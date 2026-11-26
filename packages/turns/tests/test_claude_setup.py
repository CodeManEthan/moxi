"""turns' SETUP and TEARDOWN, the placed copy of the marketplace, the plugin folders for
one launch (which writes nothing), and the Claude doctor rows.
`claude` is a stand-in on PATH; no test runs the real one, and none reads or writes the
package's own folder (conftest's `claude_root`). The placed copy goes to turns' data dir
in the test's own home."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

import moxi_turns as turns
from conftest import write_config
from moxi import paths
from moxi_dev.testing import PROC_WAIT_S, toml_string
from moxi_turns import claude_setup

@pytest.fixture
def fake_claude(stub_claude):
    """The stand-in `claude` every test has on PATH (conftest's `stub_claude`): the folder
    of its files and log."""
    return stub_claude


def _log(state: Path) -> list[str]:
    try:
        return (state / "log").read_text().splitlines()
    except OSError:
        return []


#: The mark of a reach that asks `claude`: it runs the plugin list only where it can kill
#: the command's whole process group at the bound, so on Windows the reach reads None.
ASKS_CLAUDE = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows: no POSIX process groups (os.killpg), so the reach never runs claude "
           "there and reads None, which test_the_reach_is_none_without_posix_process_groups "
           "covers")

@pytest.fixture
def placed(turns_env, claude_root):
    """The marketplace placed at turns' data dir, as SETUP's first part places it."""
    root, _ = claude_setup.place_copy()
    return root


def _status():
    from moxi import place

    return [(p.path, p.state) for p in place.status("turns")]


def _read_only(root: Path) -> list[tuple[Path, int]]:
    """Make every folder and file under `root` read-only; returns what to put back."""
    saved = []
    for path in [root, *root.rglob("*")]:
        saved.append((path, path.stat().st_mode))
        path.chmod(0o555 if path.is_dir() else 0o444)
    return saved


def _put_back(saved: list[tuple[Path, int]]) -> None:
    for path, mode in saved:
        path.chmod(mode)


def test_no_test_sees_the_package_dir(claude_root):
    from moxi.paths import Layout

    package = Path(claude_setup.__file__).resolve().parent / "claude"
    assert claude_setup.claude_root() == claude_root != package
    assert claude_setup.placed_root() == Layout("turns").data_dir() / "claude"
    assert not claude_setup.placed_root().is_relative_to(package.parent)


def test_the_volatile_paths_are_each_plugins_loader_files_and_endpoint_json(claude_root):
    assert claude_setup.volatile_paths() == (
        "moxi-turns/endpoint.json", "moxi-turns/tsconfig.json",
        "moxi-turns/.claude-plugin/types", "moxi-turns-gate/tsconfig.json",
        "moxi-turns-gate/.claude-plugin/types")


def test_endpoint_json_names_this_env_script(placed):
    from moxi_turns.endpoint import package_version

    path = claude_setup.write_endpoint_file()
    assert path == placed / "moxi-turns" / "endpoint.json"
    data = json.loads(path.read_text())
    assert data["command"] == str(paths.script("moxi-turns-endpoint"))
    assert Path(data["command"]).is_absolute()
    assert data["version"] == package_version()


def test_the_config_key_wins_and_must_be_absolute(turns_env, placed):
    elsewhere = os.path.abspath("/opt/elsewhere/moxi-turns-endpoint")
    write_config(turns_env, f'[claude]\nendpoint = {toml_string(elsewhere)}\n')
    claude_setup.write_endpoint_file()
    assert claude_setup.read_endpoint_file()["command"] == elsewhere
    write_config(turns_env, '[claude]\nendpoint = "relative/endpoint"\n')
    with pytest.raises(claude_setup.SetupError):
        claude_setup.write_endpoint_file()


def test_claude_plugin_dirs_returns_both_dirs_and_writes_nothing(turns_env, claude_root):
    before = sorted(claude_root.rglob("*"))
    runner, gate = claude_setup.claude_plugin_dirs()
    assert (runner, gate) == ((claude_root / "moxi-turns").resolve(),
                              (claude_root / "moxi-turns-gate").resolve())
    assert sorted(claude_root.rglob("*")) == before
    assert not claude_setup.placed_root().exists()


def _temps(placed: Path) -> list[str]:
    return [p.name for p in placed.parent.iterdir() if p.name.startswith(".")]


def test_two_setups_at_once_both_write_and_leave_no_temp_file(placed):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        written = list(pool.map(lambda _: claude_setup.write_endpoint_file(), range(32)))
    runner = placed / "moxi-turns"
    assert set(written) == {runner / "endpoint.json"}
    assert claude_setup.read_endpoint_file()["command"] == str(paths.script("moxi-turns-endpoint"))
    assert [p.name for p in runner.iterdir() if "endpoint" in p.name] == ["endpoint.json"]
    if os.name != "nt":                 # mode bits are POSIX's
        assert (runner / "endpoint.json").stat().st_mode & 0o777 == 0o644
    assert _temps(placed) == []
    assert _status() == [(placed, "ok")]


def test_a_failed_write_leaves_no_temp_file(placed, monkeypatch):
    def refuse(src, dst):
        raise OSError("no room")

    monkeypatch.setattr(claude_setup.os, "replace", refuse)
    with pytest.raises(claude_setup.SetupError, match="no room"):
        claude_setup.write_endpoint_file()
    assert [p.name for p in (placed / "moxi-turns").iterdir()] == [".claude-plugin"]
    assert _temps(placed) == []


@pytest.mark.skipif(os.name == "nt", reason="a read-only folder is made by mode bits, "
                    "which Windows doesn't read")
def test_a_read_only_runner_dir_is_a_setup_error(placed):
    runner = placed / "moxi-turns"
    runner.chmod(0o555)
    try:
        with pytest.raises(claude_setup.SetupError, match="cannot write"):
            claude_setup.write_endpoint_file()
    finally:
        runner.chmod(0o755)


def test_claude_plugin_dirs_raises_when_it_cannot_set_up(turns_env, claude_root):
    shutil.rmtree(claude_root / "moxi-turns")
    with pytest.raises(turns.TurnsError):
        claude_setup.claude_plugin_dirs()


# -- SETUP -------------------------------------------------------------------------------


def test_setup_places_the_copy_and_writes_endpoint_json_there_never_into_the_package(
        turns_env, claude_root, fake_claude, capsys):
    saved = _read_only(claude_root)
    try:
        assert claude_setup.setup() == 0, capsys.readouterr()
    finally:
        _put_back(saved)
    placed = claude_setup.placed_root()
    assert (placed / "moxi-turns" / "endpoint.json").is_file()
    assert not list(claude_root.rglob("endpoint.json"))
    assert _status() == [(placed, "ok")]
    mark = json.loads((placed / ".moxi-placed").read_text())
    assert mark["volatile"] == list(claude_setup.volatile_paths())
    ledger = json.loads((paths.core().state_dir() / "placed.json").read_text())
    assert ledger["turns"]["placed"][str(placed)]["dest"] is True
    out = capsys.readouterr().out
    assert f"turns: placed the Claude plugin at {placed}" in out
    assert f"plugin marketplace add {placed.resolve()}" in _log(fake_claude)


def test_setup_adds_the_marketplace_and_both_plugins_then_skips(turns_env, claude_root,
                                                                 fake_claude, capsys):
    assert claude_setup.setup() == 0
    placed = claude_setup.placed_root()
    log = _log(fake_claude)
    assert f"plugin marketplace add {placed.resolve()}" in log
    assert "plugin install moxi-turns@moxi-turns" in log
    assert "plugin install moxi-turns-gate@moxi-turns" in log
    # Done once: the second run skips each step but still rewrites endpoint.json.
    (fake_claude / "markets.json").write_text(json.dumps(
        [{"name": "moxi-turns", "source": "directory", "path": str(placed)}]))
    (fake_claude / "plugins.json").write_text(json.dumps(
        [{"id": "moxi-turns@moxi-turns", "scope": "user"},
         {"id": "moxi-turns-gate@moxi-turns", "scope": "user"}]))
    (fake_claude / "log").unlink()
    (placed / "moxi-turns" / "endpoint.json").unlink()
    capsys.readouterr()
    assert claude_setup.setup() == 0
    assert not [line for line in _log(fake_claude) if "add" in line or "install" in line]
    assert (placed / "moxi-turns" / "endpoint.json").is_file()
    assert "placed the Claude plugin" not in capsys.readouterr().out
    assert _status() == [(placed, "ok")]


def test_setup_refuses_a_marketplace_of_the_same_name_elsewhere(turns_env, claude_root,
                                                                fake_claude, capsys):
    (fake_claude / "markets.json").write_text(json.dumps(
        [{"name": "moxi-turns", "source": "directory", "path": "/somewhere/else"}]))
    assert claude_setup.setup() == 1
    assert "/somewhere/else" in capsys.readouterr().err
    placed = claude_setup.placed_root()
    assert (placed / "moxi-turns" / "endpoint.json").is_file()   # the first parts stand
    assert not [line for line in _log(fake_claude) if "remove" in line]


def test_setup_moves_a_marketplace_added_from_a_package_dir(turns_env, claude_root,
                                                               fake_claude, capsys):
    old = "/opt/env/lib/python3.12/site-packages/moxi_turns/claude"
    (fake_claude / "markets.json").write_text(json.dumps(
        [{"name": "moxi-turns", "source": "directory", "path": old}]))
    _install_runner_at(fake_claude, "user")
    assert claude_setup.setup() == 0, capsys.readouterr()
    placed = claude_setup.placed_root().resolve()
    calls = [line for line in _log(fake_claude) if "--json" not in line]
    assert calls == ["plugin marketplace remove moxi-turns",
                     f"plugin marketplace add {placed}",
                     "plugin install moxi-turns@moxi-turns",
                     "plugin install moxi-turns-gate@moxi-turns"]
    assert f"turns: moved the marketplace moxi-turns from {old} to {placed}" \
        in capsys.readouterr().out


def test_setup_updates_a_plugin_installed_at_another_version(turns_env, claude_root,
                                                             fake_claude, capsys):
    assert claude_setup.setup() == 0
    placed = claude_setup.placed_root()
    (fake_claude / "markets.json").write_text(json.dumps(
        [{"name": "moxi-turns", "source": "directory", "path": str(placed)}]))
    # The runner's cache is behind the placed manifest (0.1.0 in the test marketplace);
    # the gate's is current, and an entry with no version is left as it is.
    (fake_claude / "plugins.json").write_text(json.dumps(
        [{"id": "moxi-turns@moxi-turns", "scope": "user", "version": "0.0.9"},
         {"id": "moxi-turns-gate@moxi-turns", "scope": "user", "version": "0.1.0"}]))
    (fake_claude / "log").unlink()
    capsys.readouterr()
    assert claude_setup.setup() == 0
    calls = [line for line in _log(fake_claude) if "--json" not in line]
    assert calls == ["plugin update moxi-turns@moxi-turns --scope user"]
    out = capsys.readouterr().out
    assert "turns: updated moxi-turns@moxi-turns from 0.0.9 to 0.1.0" in out
    assert "turns: moxi-turns-gate@moxi-turns is already installed" in out
    (fake_claude / "plugins.json").write_text(json.dumps(
        [{"id": "moxi-turns@moxi-turns", "scope": "user"},
         {"id": "moxi-turns-gate@moxi-turns", "scope": "user", "version": "0.1.0"}]))
    (fake_claude / "log").unlink()
    assert claude_setup.setup() == 0
    assert [line for line in _log(fake_claude) if "--json" not in line] == []


SKIPPED_LINE = ("turns: Claude Code not found; skipped its plugin. Run moxi install turns "
                "again after installing it.")


def test_setup_without_claude_skips_the_step_and_succeeds(turns_env, claude_root,
                                                          monkeypatch, capsys):
    monkeypatch.setattr(claude_setup, "_claude", lambda: None)
    assert claude_setup.setup() == 0
    assert capsys.readouterr().out.splitlines() == [SKIPPED_LINE]
    assert _status() == []
    assert not claude_setup.placed_root().exists()
    assert not list(claude_root.rglob("endpoint.json"))
    assert claude_setup.install_plugins() == [SKIPPED_LINE.removeprefix("turns: ")]


def test_setup_run_again_once_claude_is_on_path_places_writes_and_registers(
        turns_env, claude_root, fake_claude, monkeypatch, capsys):
    found = claude_setup._claude
    monkeypatch.setattr(claude_setup, "_claude", lambda: None)
    assert claude_setup.setup() == 0
    assert _status() == [] and _log(fake_claude) == []
    monkeypatch.setattr(claude_setup, "_claude", found)
    assert claude_setup.setup() == 0
    placed = claude_setup.placed_root()
    assert _status() == [(placed, "ok")]
    assert (placed / "moxi-turns" / "endpoint.json").is_file()
    assert f"plugin marketplace add {placed.resolve()}" in _log(fake_claude)


def test_a_failed_registration_still_fails_setup(turns_env, claude_root, fake_claude,
                                                 capsys):
    (fake_claude / "plugins.json").write_text("not json")
    assert claude_setup.setup() == 1
    assert "not installed at user scope" in capsys.readouterr().err


# -- the placed copy and launches --------------------------------------------------------


def test_the_volatile_paths_never_count_as_a_change(placed, claude_root, capsys):
    claude_setup.write_endpoint_file()
    for root in (placed, claude_root):
        for plugin in ("moxi-turns", "moxi-turns-gate"):
            (root / plugin / "tsconfig.json").write_text("{}")
            (root / plugin / ".claude-plugin" / "types").mkdir()
            (root / plugin / ".claude-plugin" / "types" / "index.d.ts").write_text("x")
    assert _status() == [(placed, "ok")]
    assert claude_setup.placed_copy()[0] == "current"
    root, lines = claude_setup.place_copy()
    assert root == placed and lines == []          # kept, not placed again
    assert (placed / "moxi-turns" / "tsconfig.json").is_file()
    assert claude_setup.claude_plugin_dirs()[0] == (placed / "moxi-turns").resolve()


def test_launch_uses_the_current_copy_and_falls_back_to_the_package(placed, claude_root):
    package = ((claude_root / "moxi-turns").resolve(),
               (claude_root / "moxi-turns-gate").resolve())
    # Placed, but no endpoint.json says which turns placed it.
    assert claude_setup.claude_plugin_dirs() == package
    claude_setup.write_endpoint_file()
    assert claude_setup.claude_plugin_dirs() == ((placed / "moxi-turns").resolve(),
                                                 (placed / "moxi-turns-gate").resolve())
    # The package changed under the copy: stale.
    (claude_root / "moxi-turns" / "hooks.json").write_text("{}")
    assert _status() == [(placed, "stale")]
    assert claude_setup.claude_plugin_dirs() == package
    (claude_root / "moxi-turns" / "hooks.json").unlink()
    assert claude_setup.placed_copy()[0] == "current"
    # Placed by another version of turns.
    endpoint = placed / "moxi-turns" / "endpoint.json"
    data = json.loads(endpoint.read_text())
    endpoint.write_text(json.dumps({**data, "version": "0.0.1"}))
    state, why = claude_setup.placed_copy()
    assert state == "behind" and "0.0.1" in why
    assert claude_setup.claude_plugin_dirs() == package


def test_a_launch_through_launch_additions_names_the_placed_dirs(placed):
    claude_setup.write_endpoint_file()
    adds = turns.launch_additions("claude", cwd=placed.parent, env={})
    assert str((placed / "moxi-turns").resolve()) in adds.argv
    assert str((placed / "moxi-turns-gate").resolve()) in adds.argv


def test_a_changed_copy_is_moved_aside_and_placed_again(placed, fake_claude, capsys):
    claude_setup.write_endpoint_file()
    (placed / "moxi-turns" / "notes.txt").write_text("mine")
    assert _status() == [(placed, "modified")]
    assert claude_setup.placed_copy()[0] == "behind"
    assert claude_setup.setup() == 0, capsys.readouterr()
    assert _status() == [(placed, "ok")]
    assert not (placed / "moxi-turns" / "notes.txt").exists()
    kept = list((paths.core().data_dir() / "displaced").rglob("notes.txt"))
    assert [p.read_text() for p in kept] == ["mine"]


# -- a whole settle ----------------------------------------------------------------------


def test_a_whole_settle_of_turns_keeps_the_copy_and_its_endpoint_json(
        turns_env, fake_claude):
    """The settle step runs turns' SETUP, its units (none) and its skills (none): the
    skills step finds the copy in turns' ledger and leaves it, the first time and the
    next. It runs in the child `moxi install` starts, in this test's home, so its copy's
    source is the package's own folder, which it only reads."""
    import subprocess
    import sys

    from moxi import modules

    placed = claude_setup.placed_root()
    package = Path(claude_setup.__file__).resolve().parent / "claude"
    for _ in range(2):
        done = subprocess.run([sys.executable, "-m", "moxi._settle", "turns"],
                              check=False, capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "setup turns: done" in done.stdout
        assert "removed" not in done.stdout + done.stderr
        assert _status() == [(placed, "ok")]
        assert claude_setup.read_endpoint_file()["command"] == \
            str(paths.script("moxi-turns-endpoint"))
        [(key, entry)] = _ledger_entries()
        assert key == str(placed) and entry["source"] == str(package)
    assert modules.setup_ran("turns")


def _ledger_entries():
    ledger = json.loads((paths.core().state_dir() / "placed.json").read_text())
    return list(ledger["turns"]["placed"].items())


# -- TEARDOWN ----------------------------------------------------------------------------


def test_the_announcement_names_the_teardown():
    from moxi_turns import module

    assert module.TEARDOWN == "moxi_turns.claude_setup:teardown"


def test_teardown_removes_the_marketplace_and_the_copy(turns_env, claude_root,
                                                       fake_claude, capsys):
    assert claude_setup.setup() == 0
    placed = claude_setup.placed_root()
    (fake_claude / "markets.json").write_text(json.dumps(
        [{"name": "moxi-turns", "source": "directory", "path": str(placed)}]))
    (fake_claude / "log").unlink()
    assert claude_setup.teardown() == 0
    assert "plugin marketplace remove moxi-turns" in _log(fake_claude)
    assert not placed.exists() and _status() == []


def test_teardown_leaves_a_marketplace_added_from_elsewhere(placed, fake_claude, capsys):
    (fake_claude / "markets.json").write_text(json.dumps(
        [{"name": "moxi-turns", "source": "directory", "path": "/somewhere/else"}]))
    assert claude_setup.teardown() == 0
    assert not [line for line in _log(fake_claude) if "remove" in line]
    assert not placed.exists() and _status() == []


def test_teardown_that_cannot_read_claude_code_stops_and_keeps_the_copy(
        placed, fake_claude, capsys):
    (fake_claude / "markets.json").write_text("not json")
    assert claude_setup.teardown() == 1
    assert "--skip-teardown" in capsys.readouterr().err
    assert _status() == [(placed, "ok")]


def test_teardown_without_claude_removes_the_copy(placed, monkeypatch, capsys):
    monkeypatch.setattr(claude_setup, "_claude", lambda: None)
    assert claude_setup.teardown() == 0
    assert not placed.exists() and _status() == []
    assert "claude plugin marketplace remove moxi-turns" in capsys.readouterr().out


# -- doctor rows -------------------------------------------------------------------------


def _rows():
    return {row.name: row for row in claude_setup.rows()}


def test_rows_name_the_plugins_endpoint_json_and_the_version(turns_env, placed,
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
    moved = os.path.abspath("/opt/moved/moxi-turns-endpoint")
    write_config(turns_env, f'[claude]\nendpoint = {toml_string(moved)}\n')
    assert _rows()["claude endpoint.json"].state == "warn"      # names a command no longer given
    os.environ["FAKE_CLAUDE_VERSION"] = "9.9.9"
    row = _rows()["claude code"]
    assert row.state == "warn" and "9.9.9" in row.detail


def _install_runner_at(fake_claude, scope):
    (fake_claude / "plugins.json").write_text(json.dumps(
        [{"id": "moxi-turns@moxi-turns", "scope": scope},
         {"id": "moxi-turns-gate@moxi-turns", "scope": scope}]))


def test_a_missing_or_stale_endpoint_json_fails_with_a_user_scope_install(
        turns_env, placed, fake_claude):
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


@pytest.mark.parametrize("claude", [True, False], ids=["claude", "no-claude"])
def test_with_no_placed_copy_no_row_fails(turns_env, claude_root, fake_claude, monkeypatch,
                                          claude):
    """An install whose Claude step was skipped, or never ran, reads its plugin rows from
    the package and fails none."""
    if not claude:
        monkeypatch.setattr(claude_setup, "_claude", lambda: None)
    rows = _rows()
    assert [r.name for r in rows.values() if r.state == "fail"] == []
    for plugin in ("moxi-turns", "moxi-turns-gate"):
        assert str(claude_root.resolve() / plugin) in rows[f"claude plugin {plugin}"].detail
    row = rows["claude placed copy"]
    assert (row.state, row.detail, row.fix) == (
        "off", "no placed copy; launches use the package's folders", None)
    assert rows["claude endpoint.json"].state == "warn"
    assert rows["claude code"].state == ("ok" if claude else "off")


def test_the_placed_copy_row_compares_versions(placed, fake_claude):
    claude_setup.write_endpoint_file()
    rows = _rows()
    assert rows["claude placed copy"].state == "ok"
    assert str((placed / "moxi-turns").resolve()) in rows["claude plugin moxi-turns"].detail
    endpoint = placed / "moxi-turns" / "endpoint.json"
    endpoint.write_text(json.dumps({**json.loads(endpoint.read_text()), "version": "0.0.1"}))
    rows = _rows()
    row = rows["claude placed copy"]
    assert row.state == "warn" and "0.0.1" in row.detail and row.fix == "moxi install turns"
    assert str(placed) not in rows["claude plugin moxi-turns"].detail


def _cached_runner(tmp_path, fake_claude, leg_line):
    """A user-scope runner in Claude Code's cache whose source has `leg_line`."""
    cache = tmp_path / "cache" / "moxi-turns" / "moxi-turns" / "0.1.0"
    (cache / "hooks").mkdir(parents=True, exist_ok=True)
    (cache / "hooks" / "register.ts").write_text(f"const MOD_VERSION = '0.1.0'\n{leg_line}\n")
    (fake_claude / "plugins.json").write_text(json.dumps(
        [{"id": "moxi-turns@moxi-turns", "scope": "user", "version": "0.1.0",
          "installPath": str(cache)}]))
    return cache


def test_the_installed_runner_row_compares_the_cached_leg_with_the_endpoints(
        turns_env, claude_root, fake_claude, tmp_path):
    from moxi_turns.claude_leg import LEG

    cache = _cached_runner(tmp_path, fake_claude, f"const LEG = {LEG}")
    row = _rows()["claude installed runner"]
    assert row.state == "ok" and str(cache) in row.detail and f"leg {LEG}" in row.detail
    cache = _cached_runner(tmp_path, fake_claude, f"const LEG = {LEG - 1}")
    row = _rows()["claude installed runner"]
    assert row.state == "fail" and row.fix == "moxi install turns"
    assert f"speaks leg {LEG - 1}, the endpoint leg {LEG}" in row.detail
    _cached_runner(tmp_path, fake_claude, "// no leg here")
    row = _rows()["claude installed runner"]
    assert row.state == "warn" and "names no leg" in row.detail
    (fake_claude / "plugins.json").write_text(json.dumps(
        [{"id": "moxi-turns@moxi-turns", "scope": "user"}]))
    row = _rows()["claude installed runner"]
    assert row.state == "warn" and "names no folder" in row.detail


@pytest.mark.parametrize("listed", ["[]", "not json", "project"])
def test_no_installed_runner_row_without_a_user_scope_install(turns_env, claude_root,
                                                              fake_claude, listed):
    if listed == "project":
        _install_runner_at(fake_claude, "project")
    else:
        (fake_claude / "plugins.json").write_text(listed)
    assert "claude installed runner" not in _rows()


def test_check_carries_the_claude_rows(turns_env, fake_claude):
    from moxi_turns import doctor

    names = [row.name for row in doctor.check()]
    assert "claude endpoint.json" in names and "claude code" in names
    assert "claude placed copy" in names


# -- the reach ---------------------------------------------------------------------------


def _runner_listed(fake_claude, scope="user", **extra):
    (fake_claude / "plugins.json").write_text(json.dumps(
        [{"id": "moxi-turns@moxi-turns", "scope": scope, **extra},
         {"id": "moxi-turns-gate@moxi-turns", "scope": scope, **extra}]))


def _name_in_endpoint_file(placed: Path, command: str) -> None:
    (placed / "moxi-turns" / "endpoint.json").write_text(json.dumps({"command": command}))


@ASKS_CLAUDE
def test_the_reach_is_start_with_the_runner_and_a_current_endpoint_json(placed, fake_claude):
    _runner_listed(fake_claude, enabled=True)
    claude_setup.write_endpoint_file()
    assert claude_setup.reach() == "start"
    assert "plugin list --json" in _log(fake_claude)
    # An entry that says nothing of `enabled` is enabled.
    _runner_listed(fake_claude)
    assert claude_setup.reach() == "start"


@ASKS_CLAUDE
def test_the_reach_is_setup_when_the_install_would_fix_it(placed, fake_claude):
    claude_setup.write_endpoint_file()
    _runner_listed(fake_claude, scope="project")
    assert claude_setup.reach() == "setup"          # the runner not at user scope
    _runner_listed(fake_claude)
    (placed / "moxi-turns" / "endpoint.json").unlink()
    assert claude_setup.reach() == "setup"          # endpoint.json missing
    _name_in_endpoint_file(placed, str(placed / "no-such-endpoint"))
    assert claude_setup.reach() == "setup"          # names no executable file


def test_the_reach_is_absent_with_no_claude_and_nothing_placed(turns_env, claude_root,
                                                               monkeypatch):
    monkeypatch.setattr(claude_setup, "_claude", lambda: None)
    assert claude_setup.reach() == "absent"


def test_the_reach_is_none_with_no_claude_and_a_copy_placed(placed, monkeypatch):
    monkeypatch.setattr(claude_setup, "_claude", lambda: None)
    assert claude_setup.reach() is None


@pytest.mark.parametrize("listed", ["not json", '{"a": 1}'])
def test_the_reach_is_none_when_the_plugin_list_fails(placed, fake_claude, listed):
    claude_setup.write_endpoint_file()
    (fake_claude / "plugins.json").write_text(listed)
    assert claude_setup.reach() is None


HELD_CLAUDE = """#!/bin/sh
sleep 30 &
echo $! > "$FAKE_CLAUDE_DIR/child"
echo '[]'
exit 0
"""


@ASKS_CLAUDE
def test_the_reach_is_none_past_its_bound_and_kills_a_child_holding_the_pipes(
        placed, fake_claude):
    import time

    exe = Path(shutil.which("claude"))
    exe.write_text(HELD_CLAUDE)
    claude_setup.write_endpoint_file()
    began = time.monotonic()
    assert claude_setup.reach(timeout_s=0.5) is None
    assert time.monotonic() - began < 5.0
    child = int((fake_claude / "child").read_text())
    deadline = time.monotonic() + PROC_WAIT_S
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(child)


def _alive(pid: int) -> bool:
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat_line.rsplit(")", 1)[1].split()[0] != "Z"


def test_the_reach_is_none_with_no_endpoint_command(turns_env, placed, fake_claude):
    _runner_listed(fake_claude)
    claude_setup.write_endpoint_file()
    write_config(turns_env, '[claude]\nendpoint = "relative/endpoint"\n')
    assert claude_setup.reach() is None


def test_the_reach_is_none_for_a_runner_listed_disabled(placed, fake_claude):
    _runner_listed(fake_claude, enabled=False)
    claude_setup.write_endpoint_file()
    assert claude_setup.reach() is None


def test_the_reach_is_none_when_endpoint_json_names_another_executable(placed, fake_claude):
    import sys

    _runner_listed(fake_claude)
    _name_in_endpoint_file(placed, sys.executable)
    assert claude_setup.reach() is None


def test_the_reach_is_none_without_posix_process_groups(placed, fake_claude, monkeypatch):
    _runner_listed(fake_claude)
    claude_setup.write_endpoint_file()
    monkeypatch.delattr(os, "killpg", raising=False)            # none on Windows
    assert claude_setup.reach() is None
    assert "plugin list --json" not in _log(fake_claude)


def _tree(*roots: Path) -> dict:
    out = {}
    for root in roots:
        for path in [root, *root.rglob("*")] if root.exists() else []:
            info = path.lstat()
            out[path] = (info.st_mtime_ns, info.st_size, info.st_mode)
    return out


@ASKS_CLAUDE
def test_the_reach_writes_nothing(turns_env, placed, fake_claude, claude_root):
    from moxi.paths import Layout

    _runner_listed(fake_claude)
    claude_setup.write_endpoint_file()
    layout = Layout("turns")
    roots = (turns_env, claude_root, layout.data_dir(), layout.state_dir(),
             layout.config_dir())
    before = _tree(*roots)
    assert claude_setup.reach() == "start"
    assert _tree(*roots) == before
