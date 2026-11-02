"""loadout's machine settings, its stores and the lock files it keeps in its state dir."""

from __future__ import annotations

import os
import stat

import pytest
from moxi import config
from moxi.config import ConfigError
from moxi.paths import Layout

from moxi_loadout import module, settings, stores


def write_config(text: str):
    path = Layout("loadout").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_defaults_put_the_folder_in_loadouts_data_directory():
    machine = settings.read()
    assert not machine.chosen
    assert machine.source == Layout("loadout").data_dir() / "source"
    assert machine.backups_keep == 10
    assert machine.project_folders == {
        "claude": {"skills": ".claude/skills", "agents": ".claude/agents"},
        "codex": {"skills": ".agents/skills", "agents": ".codex/agents"},
        "opencode": {"skills": ".opencode/skills", "agents": ".opencode/agents"},
    }


def test_a_configured_folder_is_read_with_its_home_expanded():
    write_config('source = "~/my-loadout"\n')
    machine = settings.read()
    assert machine.chosen and machine.source == type(machine.source)(
        os.path.expanduser("~/my-loadout"))
    assert stores.source_dir() == machine.source
    assert stores.manifest_path() == machine.source / "loadout.toml"


def test_project_folders_change_only_what_the_file_names():
    write_config('[project_folders.codex]\nagents = "codex/agents"\n'
                 '[project_folders.other]\nskills = "other/skills"\n')
    folders = settings.read().project_folders
    assert folders["codex"] == {"skills": ".agents/skills", "agents": "codex/agents"}
    assert folders["claude"] == settings.default_folders()["claude"]
    assert folders["other"] == {"skills": "other/skills"}


@pytest.mark.parametrize("text, part", [
    ('[project_folders.claude]\nskills = "/abs/skills"\n', "absolute path"),
    ('[project_folders.claude]\nskills = "~/skills"\n', "absolute path"),
    ('[project_folders.claude]\nskills = "C:/skills"\n', "absolute path"),
    ('[project_folders.claude]\nskills = "../skills"\n', "climbs out"),
    ('[project_folders.claude]\nskills = ""\n', "is empty"),
    ('[project_folders.claude]\nskills = 3\n', "not a string"),
    ('[project_folders.claude]\nprompts = "x"\n', "is not a folder kind"),
    ('[project_folders]\nclaude = ".claude"\n', "not a table"),
    ('[project_folders.Claude]\nskills = "x"\n', "names the harness"),
    ('[backups]\nkeep = 0\n', "is 0"),
])
def test_the_config_check_refuses_what_the_schema_cannot_say(text, part):
    with pytest.raises(ConfigError) as caught:
        settings.check_text(text)
    assert part in str(caught.value)
    write_config(text)
    with pytest.raises(ConfigError):
        settings.read()


def test_config_set_runs_the_modules_check():
    with pytest.raises(ConfigError) as caught:
        config.set("loadout", "backups.keep", 0, by="test")
    assert "keep at least 1" in str(caught.value)
    config.set("loadout", "backups.keep", 3, by="test")
    assert settings.read().backups_keep == 3


def test_the_stores_are_declared_as_loadout_keeps_them():
    assert module.STORES["source"] == {"root": "data", "path": "source", "kind": "dir",
                                       "private": False, "retention": "keep",
                                       "rebuildable": False, "lazy": True}
    assert module.STORES["ledger"]["root"] == "state"
    assert module.STORES["ledger"]["private"] and not module.STORES["ledger"]["rebuildable"]
    assert module.STORES["generated"]["rebuildable"]
    assert module.STORES["backups"]["private"] and module.STORES["backups"]["root"] == "data"
    layout = Layout("loadout")
    assert stores.default_source() == layout.data_dir() / "source"
    assert stores.ledger_path() == layout.state_dir() / "ledger.json"
    assert stores.generated_dir() == layout.state_dir() / "generated"
    assert stores.backups_dir() == layout.data_dir() / "backups"


def test_the_core_reads_the_announcement():
    import moxi.modules

    found = moxi.modules.load("loadout")
    assert set(found.stores) == {"source", "ledger", "generated", "backups"}
    assert "projects" in found.optional


def test_each_lock_path_makes_its_private_folder_first():
    state = Layout("loadout").state_dir()
    assert not state.exists()
    assert stores.manifest_lock() == state / "manifest.lock"
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stores.install_lock() == state / "install.lock"
    lock = stores.harness_lock("~/.codex/config.toml")
    assert lock.parent == state / "locks" and lock.parent.is_dir()
    assert lock.name.startswith("config.toml-") and lock.suffix == ".lock"


def test_two_harness_files_of_one_name_never_share_a_lock(tmp_path):
    a = stores.harness_lock(tmp_path / "a" / "config.toml")
    b = stores.harness_lock(tmp_path / "b" / "config.toml")
    assert a != b
    assert stores.harness_lock(tmp_path / "a" / "config.toml") == a
    home = os.path.expanduser("~")
    assert stores.harness_lock("~/x.toml") == stores.harness_lock(os.path.join(home, "x.toml"))
