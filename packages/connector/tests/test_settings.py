"""The connector's settings schema with its page fields, as `moxi config connector show
--json` prints it."""

from __future__ import annotations

import json

from moxi import cli
from moxi.config import Schema


def show_json(capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["config", "connector", "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    Schema.from_resource("moxi_connector", "settings.toml")


def test_show_json_gives_every_key_a_label(capsys):
    keys = show_json(capsys)
    assert {name: key["label"] for name, key in keys.items()} == {
        "enabled": "The public door", "port": "Port", "public_url": "Public address",
        "redirect_uris": "Sign-in callbacks", "refresh_days": "A sign-in lasts for",
        "tools.withheld": "Tools withheld from Claude"}
    assert keys["public_url"]["line"] == (
        "The public origin, https://name:port with no path, when no tunnel gives one.")
    assert {name: key["unit"] for name, key in keys.items() if key["unit"]} == {
        "refresh_days": "days"}
    for key in keys.values():
        assert key["common"] is False and key["group"] is None and key["hurts"] is None
        assert key["choices"] is None and key["elsewhere"] is None
