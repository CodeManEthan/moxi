"""slots' settings schema with its page fields, as `moxi config slots show --json`
prints it."""

from __future__ import annotations

import json

from moxi import cli
from moxi.config import Schema


def show_json(capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["config", "slots", "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    Schema.from_resource("moxi_slots", "settings.toml")


def test_show_json_gives_both_keys_a_label_and_a_line(capsys):
    keys = show_json(capsys)
    assert {name: key["label"] for name, key in keys.items()} == {
        "tmux": "tmux", "tmux_conf": "tmux config file"}
    assert all(key["line"] for key in keys.values())
    for key in keys.values():
        assert key["common"] is False and key["group"] is None and key["hurts"] is None
        assert key["unit"] is None and key["elsewhere"] is None
