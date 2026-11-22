"""turns' settings schema with its page fields, as `moxi config turns show --json` prints it."""

from __future__ import annotations

import itertools
import json

from moxi import cli
from moxi.config import COMMON_MAX, Schema

#: The keys the settings page shows before More, in schema order.
COMMON = ["guards.removal", "guards.no_nested_agents", "guards.question_mode", "guards.run",
          "start.order", "commands.allow"]
#: The sub-heads and the keys under each, in schema order: Guards and Sessions hold the
#: keys shown first, in that order; Waits is behind More.
GROUPS = {
    "Guards": ["guards.removal", "guards.no_nested_agents", "guards.question_mode",
               "guards.run"],
    "Sessions": ["start.order", "commands.allow"],
    "Waits": ["guards.max_wait_ms", "stop_handler.wait_ms", "listen.wait_ms",
              "after_tool.wait_ms"],
}
#: The keys with no sub-head, in the order More names them.
LOOSE = ["start.limit_chars", "claude.endpoint", "prompt.own_origins",
         "question_mode.prose_words"]


def show_json(capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["config", "turns", "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    parsed = Schema.from_resource("moxi_turns", "settings.toml")
    assert sum(1 for key in parsed.live() if key.common) <= COMMON_MAX


def test_show_json_gives_every_key_a_label(capsys):
    keys = show_json(capsys)
    assert all(key["label"] for key in keys.values())
    assert keys["guards.no_nested_agents"]["label"] == "No nested agents"
    assert keys["guards.run"]["line"] == "Guard names, and paths to guard files of your own."
    assert keys["stop_handler.wait_ms"]["label"] == "For a session's stop handler"
    for key in keys.values():
        assert key["choices"] is None and key["pick"] is False and key["terminal"] is None
        assert key["elsewhere"] is None


def test_show_json_shows_the_guards_and_sessions_first_and_groups_the_waits(capsys):
    keys = show_json(capsys)
    assert [name for name, key in keys.items() if key["common"]] == COMMON
    first = [key["group"] for key in keys.values() if key["common"]]
    assert list(dict.fromkeys(first)) == ["Guards", "Sessions"]
    assert {key["group"] for key in keys.values() if not key["common"] and key["group"]} == {
        "Waits"}
    for group, names in GROUPS.items():
        assert [name for name, key in keys.items() if key["group"] == group] == names
    assert [name for name, key in keys.items() if not key["group"]] == LOOSE


def test_show_json_gives_each_wait_and_the_limit_a_unit(capsys):
    keys = show_json(capsys)
    assert {name: key["unit"] for name, key in keys.items() if key["unit"]} == {
        **dict.fromkeys(GROUPS["Waits"], "ms"), "start.limit_chars": "characters"}


def test_show_json_marks_turning_a_guard_off_and_removing_one_as_able_to_hurt(capsys):
    keys = show_json(capsys)
    hurts = {name: key["hurts"] for name, key in keys.items() if key["hurts"]}
    assert {name: hurt["when"] for name, hurt in hurts.items()} == {
        "guards.removal": "off", "guards.no_nested_agents": "off",
        "guards.question_mode": "off", "guards.run": "remove"}
    assert hurts["guards.no_nested_agents"]["says"] == "turns this guard off"
    assert hurts["guards.no_nested_agents"]["confirm"] == (
        "From its next tool call, a subagent in any session can start subagents of its own.")
    assert not any(hurt["stays"] for hurt in hurts.values())


def test_show_json_keeps_each_groups_keys_together_behind_more(capsys):
    # The page starts a sub-head wherever the group changes in schema order, so a group
    # whose keys were split would be drawn twice.
    keys = show_json(capsys)
    more = [key["group"] for key in keys.values()
            if not key["common"] and not key["elsewhere"]]
    runs = [group for group, _ in itertools.groupby(more) if group]
    assert len(runs) == len(set(runs)), runs
