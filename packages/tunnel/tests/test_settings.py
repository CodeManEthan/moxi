"""tunnel's settings schema with its page fields, as `moxi config tunnel show --json`
prints it."""

from __future__ import annotations

import json

from moxi import cli
from moxi.config import Schema


def show_json(capsys) -> dict:
    capsys.readouterr()
    assert cli.main(["config", "tunnel", "show", "--json"]) == 0
    return {key["name"]: key for key in json.loads(capsys.readouterr().out)}


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    Schema.from_resource("moxi_tunnel", "settings.toml")


def test_show_json_gives_every_key_a_label(capsys):
    keys = show_json(capsys)
    assert {name: key["label"] for name, key in keys.items()} == {
        "tailscale.binary": "Tailscale CLI", "cloudflare.enabled": "The cloudflared daemon",
        "cloudflare.tunnel_id": "Cloudflare tunnel id",
        "cloudflare.credentials": "Cloudflare credentials",
        "cloudflare.metrics_port": "Cloudflare metrics port",
        "cloudflare.binary": "cloudflared binary"}
    assert keys["cloudflare.metrics_port"]["line"] is None
    for key in keys.values():
        assert key["common"] is False and key["group"] is None and key["hurts"] is None
        assert key["unit"] is None and key["choices"] is None


def test_show_json_shows_the_cloudflared_switch_set_by_the_tunnel_verbs(capsys):
    keys = show_json(capsys)
    assert not any(key["elsewhere"] for key in keys.values())
    assert [name for name, key in keys.items() if key["terminal"]] == ["cloudflare.enabled"]
    assert keys["cloudflare.enabled"]["terminal"] == {
        "on": "moxi tunnel add <target> --provider cloudflare",
        "off": "moxi tunnel remove <target>"}
