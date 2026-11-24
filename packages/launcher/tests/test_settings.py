"""The launcher's settings: the schema reads, and every key has its default with no file."""

from __future__ import annotations

import itertools
import json

from moxi import cli
from moxi.config import COMMON_MAX, DEFAULT, FILE, Schema
from moxi.paths import Layout

from moxi_launcher import settings

DEFAULTS = {
    "ready.wait_s": 60,
    "first.wait_s": 120,
    "startup.wait_s": 20,
    "startup.trust_dialog": False,
    "startup.dev_channels_dialog": False,
    "startup.bypass_warning": False,
    "runs.keep_days": 30,
    "relaunch.wait_s": 900,
    "relaunch.person_s": 120,
    "relaunch.dialog_s": 60,
    "relaunch.name_wait_s": 15,
    "relaunch.follow_s": 300,
    "relaunch.fallback": "",
    "relaunch.pass_env": [],
    "relaunch.director_pass_env": [],
    "relaunch.notice_retry_s": 30,
    "wake_guard.enabled": True,
    "recycle.limit_wait_s": 1800,
    "keep_warm.subagent_cap": 3,
    "keep_warm.director_cap": 10,
    "name.template": "<date>-<time>-<project>-<agent>",
    "name.templates": {},
    "page.launch_wait_s": 600,
    "park.wait_s": 300,
    "records.keep_days": 90,
}
#: The list settings whose defaults are texts: checked for their count only.
TEXTS = {"keep_warm.subagent_texts": 3, "keep_warm.director_texts": 2,
         "keep_warm.directing_texts": 1}


def test_with_no_file_every_key_has_its_default():
    current = settings.current()
    for key, value in DEFAULTS.items():
        assert current[key] == value
        assert current.source(key) == DEFAULT


def test_the_schema_declares_exactly_these_keys():
    assert set(settings.schema().keys) == set(DEFAULTS) | set(TEXTS)
    current = settings.current()
    for key, count in TEXTS.items():
        assert len(current[key]) == count and all(isinstance(t, str) for t in current[key])


def test_the_startup_keys_need_slots():
    schema = settings.schema()
    for key in ("startup.wait_s", "startup.trust_dialog", "startup.dev_channels_dialog",
                "startup.bypass_warning"):
        assert schema.keys[key].needs == "slots"


def test_a_key_set_in_the_file_wins():
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[ready]\nwait_s = 5\n\n[startup]\ntrust_dialog = true\n",
                    encoding="utf-8")
    current = settings.current()
    assert current["ready.wait_s"] == 5
    assert current.source("ready.wait_s") == FILE
    assert current["startup.trust_dialog"] is True
    assert current["first.wait_s"] == 120


# -- page fields: how the settings page shows each key --------------------------------------

#: The keys the settings page shows before More, in schema order.
COMMON = ["startup.trust_dialog", "startup.dev_channels_dialog", "startup.bypass_warning",
          "runs.keep_days", "relaunch.fallback", "relaunch.pass_env", "records.keep_days"]
#: The sub-heads behind More, in the order they first appear, and the keys under each.
GROUPS = {
    "Waits": ["ready.wait_s", "first.wait_s", "startup.wait_s", "recycle.limit_wait_s",
              "page.launch_wait_s", "park.wait_s"],
    "Relaunch": ["relaunch.wait_s", "relaunch.person_s", "relaunch.dialog_s",
                 "relaunch.name_wait_s", "relaunch.follow_s", "relaunch.director_pass_env",
                 "relaunch.notice_retry_s", "wake_guard.enabled"],
    "Keep warm": ["keep_warm.subagent_cap", "keep_warm.director_cap",
                  "keep_warm.subagent_texts", "keep_warm.director_texts",
                  "keep_warm.directing_texts"],
}
#: The keys edited on another page, which carry no label here.
TEMPLATES = {"name.template", "name.templates"}


def show_json(capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["config", "launcher", "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    parsed = Schema.from_resource("moxi_launcher", "settings.toml")
    assert sum(1 for key in parsed.live() if key.common) <= COMMON_MAX


def test_show_json_gives_every_key_but_the_templates_a_label(capsys):
    keys = show_json(capsys)
    assert {name for name, key in keys.items() if not key["label"]} == TEMPLATES
    assert keys["runs.keep_days"]["label"] == "Keep run files for"
    assert keys["runs.keep_days"]["line"] == ("A launch's prompt file, settings file and "
                                                 "headless log.")
    assert keys["ready.wait_s"]["label"] == "For a started session to be ready"
    assert keys["keep_warm.subagent_cap"]["label"] == "Nudges in a row, waiting on subagents"
    for key in keys.values():
        assert key["choices"] is None and key["pick"] is False and key["terminal"] is None


def test_show_json_marks_seven_keys_common_and_groups_the_rest(capsys):
    keys = show_json(capsys)
    assert [name for name, key in keys.items() if key["common"]] == COMMON
    assert all(keys[name]["group"] is None for name in COMMON)
    first_seen = list(dict.fromkeys(key["group"] for key in keys.values() if key["group"]))
    assert first_seen == list(GROUPS)
    for group, names in GROUPS.items():
        assert [name for name, key in keys.items() if key["group"] == group] == names


def test_show_json_gives_each_wait_and_count_its_unit(capsys):
    keys = show_json(capsys)
    for name, key in keys.items():
        if name.endswith("_s"):
            assert key["unit"] == "seconds", name
        elif name.endswith("_days"):
            assert key["unit"] == "days", name
        elif name.endswith("_cap"):
            assert key["unit"] == "nudges", name
        else:
            assert key["unit"] is None, name


def test_show_json_marks_the_six_changes_that_can_hurt(capsys):
    keys = show_json(capsys)
    hurts = {name: key["hurts"] for name, key in keys.items() if key["hurts"]}
    assert set(hurts) == {"startup.trust_dialog", "startup.dev_channels_dialog",
                          "startup.bypass_warning", "relaunch.pass_env",
                          "relaunch.director_pass_env", "wake_guard.enabled"}
    trust = hurts["startup.trust_dialog"]
    assert (trust["when"], trust["stays"]) == ("on", True)
    assert trust["says"] == "trusts the folder of every slot launch"
    assert "~/.claude.json" in trust["confirm"]
    bypass = hurts["startup.bypass_warning"]
    assert (bypass["when"], bypass["stays"]) == ("on", True)
    assert "~/.claude/settings.json" in bypass["confirm"]
    assert (hurts["startup.dev_channels_dialog"]["when"],
            hurts["startup.dev_channels_dialog"]["stays"]) == ("on", False)
    assert hurts["relaunch.pass_env"]["when"] == "add"
    assert hurts["relaunch.director_pass_env"]["when"] == "add"
    assert hurts["wake_guard.enabled"]["when"] == "off"
    assert all(hurt["confirm"] for hurt in hurts.values())


def test_show_json_keeps_each_groups_keys_together_behind_more(capsys):
    # The page starts a sub-head wherever the group changes in schema order, so a group
    # whose keys were split would be drawn twice.
    keys = show_json(capsys)
    more = [key["group"] for key in keys.values()
            if not key["common"] and not key["elsewhere"]]
    runs = [group for group, _ in itertools.groupby(more) if group]
    assert len(runs) == len(set(runs)), runs
