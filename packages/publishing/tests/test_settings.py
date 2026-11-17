"""publishing's config: the defaults, what the loader refuses, and the page fields
`moxi config publishing show --json` prints."""

from __future__ import annotations

import json

import pytest
from moxi import cli
from moxi.config import ConfigError, Schema

from moxi_publishing import rules, settings


def test_the_defaults():
    values = settings.load()
    assert tuple(values[settings.RULES]) == rules.NAMES
    assert values[settings.WORD_PATTERN] == r"^\S+$"
    assert list(values[settings.DROP_TRAILERS]) == ["Claude-Session", "Commit-Check"]
    assert values[settings.MAIN] == "main"
    assert values[settings.WORD_REQUIRED] is False


@pytest.mark.parametrize("text", [
    'provenance.rules = ["no-such-rule"]\n',
    'push.word_pattern = "("\n',
])
def test_the_loader_refuses(text):
    with pytest.raises(ConfigError):
        settings.check_text(text)


# -- page fields: how the settings page shows each key --------------------------------------

def show_json(capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["config", "publishing", "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    Schema.from_resource("moxi_publishing", "settings.toml")


def test_show_json_gives_every_key_a_label_and_a_line(capsys):
    keys = show_json(capsys)
    assert {name: key["label"] for name, key in keys.items()} == {
        "provenance.rules": "Provenance rules that run",
        "push.word_pattern": "What a push word must look like",
        "defaults.drop_trailers": "Trailers to drop, for a new repo",
        "defaults.main": "The main branch, for a new repo",
        "defaults.word_required": "Push word needed, for a new repo"}
    assert all(key["line"] for key in keys.values())
    for key in keys.values():
        assert key["common"] is False and key["group"] is None and key["unit"] is None
        assert key["choices"] is None and key["terminal"] is None
        assert key["elsewhere"] is None


def test_show_json_marks_taking_a_provenance_rule_away_as_able_to_hurt(capsys):
    keys = show_json(capsys)
    assert [name for name, key in keys.items() if key["hurts"]] == ["provenance.rules"]
    hurt = keys["provenance.rules"]["hurts"]
    assert (hurt["when"], hurt["stays"]) == ("remove", False)
    assert hurt["says"] == "switches this rule off" and hurt["confirm"]
