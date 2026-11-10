"""The announcement, as `moxi.modules` reads it, and the `moxi rounds` command."""

from __future__ import annotations

import pytest
from moxi import cli, modules

import moxi_rounds.api
from moxi_rounds import module, paths


def test_the_module_loads_at_interface_one_with_its_read_api():
    found = modules.load("rounds", interface=1)
    assert found.state == modules.PRESENT, found.reason
    assert found.api is moxi_rounds.api
    assert modules.load("rounds", interface=2).state != modules.PRESENT


def test_the_announcement_names_the_command_the_settings_and_their_check():
    found = modules.load("rounds")
    assert found.cli == "moxi_rounds.cli:main"
    assert found.settings == "settings.toml"
    assert found.config_check == "moxi_rounds.settings:check_text"
    assert found.summary and "\n" not in found.summary


def test_notes_is_the_one_optional_module():
    found = modules.load("rounds")
    assert dict(found.optional) == {
        "notes": "Gives the default notes folder of a project that names none, for a "
                 "relative rounds folder."}
    assert dict(found.gated) == {}


def test_the_announcement_declares_no_variable_of_its_own():
    found = modules.load("rounds")
    assert dict(found.internal_env) == {} and dict(found.session_env) == {}


def test_the_index_is_the_one_store_and_can_be_rebuilt():
    found = modules.load("rounds")
    assert set(found.stores) == {"index"}
    index = found.stores["index"]
    assert (index["root"], index["path"], index["kind"]) == ("data", "index.db", "sqlite")
    assert index["rebuildable"] is True and index["lazy"] is True
    assert index["path"] == paths.INDEX_FILE


def test_the_announcement_defines_only_names_the_core_knows():
    defined = {name for name in vars(module) if name.isupper()}
    assert defined <= set(modules.KNOWN)


def test_moxi_rounds_runs_the_module_command(capsys):
    assert cli.main(["rounds", "--help"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("usage: moxi rounds") and err == ""


def test_the_command_with_no_verb_is_a_usage_error(capsys):
    from moxi_rounds.cli import main

    assert main([]) == 2
    out, err = capsys.readouterr()
    assert out == "" and "moxi rounds: name a verb" in err


@pytest.mark.parametrize("argv", [["no-such-verb"], ["--no-such-flag"]])
def test_the_command_refuses_what_it_does_not_know(capsys, argv):
    from moxi_rounds.cli import main

    assert main(argv, prog="rounds") == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("usage: rounds")
