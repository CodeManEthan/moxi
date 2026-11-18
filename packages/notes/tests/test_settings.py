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
    assert len(keys) == 13
    assert all(key["label"] for key in keys.values())
    assert keys["snapshot.push"]["label"] == "Push snapshots to"
    assert keys["snapshot.timer"]["label"] == "Snapshot on a timer"
    assert keys["snapshot.push"]["line"] == "A git remote. Not set: no push."
    assert {name: key["unit"] for name, key in keys.items() if key["unit"]} == {
        "max_mib": "MiB", "snapshot.interval_minutes": "minutes"}
    guard = keys.pop("guard.home_paths")
    assert guard["common"] is True and guard["hurts"]["when"] == "off"
    assert guard["label"] == "Home-paths guard" and "nothing warns" in guard["line"]
    for key in keys.values():
        assert key["common"] is False and key["group"] is None and key["hurts"] is None
        assert key["pick"] is False and key["elsewhere"] is None


def test_show_json_words_each_way_a_commit_handles_a_secret(capsys):
    keys = show_json(capsys)
    assert keys["action"]["choices"] == [
        {"value": "leave-out", "words": "leave it out"},
        {"value": "redact", "words": "redact it"}]
    assert [name for name, key in keys.items() if key["choices"]] == ["action"]


def test_no_prefix_text_offers_the_user_name_or_login_as_an_example():
    import re
    from pathlib import Path

    package = Path(__file__).resolve().parents[1]
    texts = {
        "settings.toml": (package / "src/moxi_notes/settings.toml").read_text("utf-8"),
        "settings.py": (package / "src/moxi_notes/settings.py").read_text("utf-8"),
        "README.md": (package / "README.md").read_text("utf-8"),
    }
    offers = re.compile(r"such as (?:your |the )?(?:user ?name|login)", re.IGNORECASE)
    for name, text in texts.items():
        assert not offers.search(text), name
    table = next(line for line in texts["README.md"].splitlines()
                 if line.startswith("| `prefix` |"))
    assert "such as `work`" in table
    assert texts["settings.py"].count("give a folder name under the root, such as work") == 2
    prefix = Schema.from_resource("moxi_notes", "settings.toml")
    doc = re.search(r"\[prefix\]\n(?:.*\n)*?doc = \"(.*)\"", texts["settings.toml"]).group(1)
    assert "such as `work`" in doc and "not your login" in doc
    assert prefix is not None
