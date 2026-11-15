"""secrets' settings schema with its page fields, as `moxi config secrets show --json`
prints it."""

from __future__ import annotations

import json

from moxi import cli
from moxi.config import Schema

#: The lists whose names the scan guards: taking one away can hurt.
GUARDED = ["private_names.words", "private_names.anywhere", "private_names.emails"]
#: The list of text that is public on purpose: adding to it lets text through.
ALLOWED = "private_names.allowed"


def show_json(capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["config", "secrets", "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    Schema.from_resource("moxi_secrets", "settings.toml")


def test_show_json_gives_every_key_a_label_and_a_line(capsys):
    keys = show_json(capsys)
    assert len(keys) == 6
    assert all(key["label"] and key["line"] for key in keys.values())
    assert keys["private_names.words"]["label"] == "Private names"
    for key in keys.values():
        assert key["common"] is False and key["group"] is None and key["unit"] is None
        assert key["elsewhere"] is None


def test_show_json_marks_taking_a_private_name_away_as_able_to_hurt(capsys):
    keys = show_json(capsys)
    assert [name for name, key in keys.items() if key["hurts"]] == [*GUARDED, ALLOWED]
    for name in GUARDED:
        hurt = keys[name]["hurts"]
        assert hurt["when"] == "remove" and hurt["stays"] is False
        assert hurt["says"].startswith("stops guarding this ") and hurt["confirm"]


def test_show_json_marks_adding_text_that_is_public_on_purpose_as_able_to_hurt(capsys):
    hurt = show_json(capsys)[ALLOWED]["hurts"]
    assert hurt["when"] == "add" and hurt["stays"] is False
    assert hurt["says"] == "lets this text through" and hurt["confirm"]
