"""notes' settings schema with its page fields, as `moxi config notes show --json`
prints it."""

from __future__ import annotations

import json

from moxi import cli
from moxi.config import Schema


def show_json(capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["config", "notes", "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    Schema.from_resource("moxi_notes", "settings.toml")


def test_show_json_gives_every_key_a_label(capsys):
    keys = show_json(capsys)
    assert len(keys) == 12
    assert all(key["label"] for key in keys.values())
    assert keys["snapshot.push"]["label"] == "Push snapshots to"
    assert keys["snapshot.timer"]["label"] == "Snapshot on a timer"
    assert keys["snapshot.push"]["line"] == "A git remote. Not set: no push."
    assert {name: key["unit"] for name, key in keys.items() if key["unit"]} == {
        "max_mib": "MiB", "snapshot.interval_minutes": "minutes"}
    for key in keys.values():
        assert key["common"] is False and key["group"] is None and key["hurts"] is None
        assert key["pick"] is False and key["elsewhere"] is None


def test_show_json_words_each_way_a_commit_handles_a_secret(capsys):
    keys = show_json(capsys)
    assert keys["action"]["choices"] == [
        {"value": "leave-out", "words": "leave it out"},
        {"value": "redact", "words": "redact it"}]
    assert [name for name, key in keys.items() if key["choices"]] == ["action"]
