"""sessions' settings schema with its page fields, as `moxi config sessions show --json`
prints it."""

from __future__ import annotations

import json

from moxi import cli
from moxi.config import COMMON_MAX, Schema

#: The keys the settings page shows before More, in schema order.
COMMON = ["claude.projects", "codex.sessions", "opencode.database", "guard.transcript_read"]


def show_json(capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["config", "sessions", "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    parsed = Schema.from_resource("moxi_sessions", "settings.toml")
    assert sum(1 for key in parsed.live() if key.common) <= COMMON_MAX


def test_show_json_gives_every_key_a_label_and_four_first(capsys):
    keys = show_json(capsys)
    assert all(key["label"] for key in keys.values())
    assert [name for name, key in keys.items() if key["common"]] == COMMON
    assert keys["claude.projects"]["label"] == "Claude Code transcripts"
    assert keys["claude.projects"]["line"] == "Not set: the folder Claude Code itself uses."
    assert keys["guard.transcript_read_escapes"]["label"] == "Commands the guard lets through"
    assert {name: key["unit"] for name, key in keys.items() if key["unit"]} == {
        "list.recent_s": "seconds"}
    for key in keys.values():
        assert key["group"] is None and key["choices"] is None and key["pick"] is False
        assert key["terminal"] is None and key["elsewhere"] is None


def test_show_json_marks_turning_the_transcript_read_guard_off_as_able_to_hurt(capsys):
    keys = show_json(capsys)
    assert [name for name, key in keys.items() if key["hurts"]] == ["guard.transcript_read"]
    hurt = keys["guard.transcript_read"]["hurts"]
    assert (hurt["when"], hurt["says"], hurt["stays"]) == ("off", "turns this guard off", False)
    assert hurt["confirm"]
