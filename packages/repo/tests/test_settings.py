"""repo's settings schema with its page fields, as `moxi config repo show --json`
prints it."""

from __future__ import annotations

import json

from moxi import cli
from moxi.config import Schema


def show_json(capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["config", "repo", "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    Schema.from_resource("moxi_repo", "settings.toml")


def test_show_json_gives_every_key_a_label_and_a_line(capsys):
    keys = show_json(capsys)
    assert {name: key["label"] for name, key in keys.items()} == {
        "git_hooks.mode": "How git reaches the hook runner",
        "commit_rules.everywhere": "Commit rules in every repo",
        "commit_rules.tag_patterns": "Tag patterns for a new repo",
        "commit_proof.everywhere": "Commit proof in every repo"}
    assert all(key["line"] for key in keys.values())
    for key in keys.values():
        assert key["common"] is False and key["group"] is None and key["hurts"] is None
        assert key["unit"] is None and key["elsewhere"] is None


def test_show_json_words_each_git_hook_mode(capsys):
    choices = show_json(capsys)["git_hooks.mode"]["choices"]
    assert [(choice["value"], choice["words"]) for choice in choices] == [
        ("auto", "auto"), ("config", "config entries"), ("shim", "shims")]
