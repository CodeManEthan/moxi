"""The announcement, the settings and their check, and the install listener."""

from __future__ import annotations

from importlib.metadata import entry_points

import pytest

import moxi.modules
from moxi.config import ConfigError
from moxi_tunnel import installs, settings


def test_the_module_loads_with_its_stores_and_settings():
    found = moxi.modules.load("tunnel")
    assert found.state == "present", found.reason
    assert set(found.stores) == {"applied", "events", "displaced", "cloudflared"}
    assert found.stores["applied"]["lazy"] is True
    assert found.config_check == "moxi_tunnel.settings:check"
    values = settings.load()
    assert values["cloudflare.enabled"] is False
    assert values["cloudflare.metrics_port"] == 20241
    assert values["tailscale.binary"] is None and values["cloudflare.tunnel_id"] == ""


def test_the_daemon_switch_is_declared_as_the_core_checks_one():
    key = settings.schema().keys["cloudflare.enabled"]
    assert (key.type, key.read, key.default, key.env) == ("bool", "use", False, None)


@pytest.mark.parametrize("text", [
    "", "[cloudflare]\ntunnel_id = '6ff42ae2-765d-4adf-8112-31c55c1551ef'\n",
    "[cloudflare]\nmetrics_port = 1\ncredentials = '/home/me/a #b: c.json'\n",
])
def test_the_check_passes_what_tunnel_takes(text):
    settings.check(text)


@pytest.mark.parametrize("text, key", [
    ("[cloudflare]\ntunnel_id = 'not-a-uuid'\n", "cloudflare.tunnel_id"),
    ("[cloudflare]\nmetrics_port = 0\n", "cloudflare.metrics_port"),
    ("[cloudflare]\nmetrics_port = 65536\n", "cloudflare.metrics_port"),
    ('[cloudflare]\ncredentials = "/home/me/a\\u0007.json"\n', "cloudflare.credentials"),
    ('[tailscale]\nbinary = "/usr/bin/tail\\u009bscale"\n', "tailscale.binary"),
    ("[cloudflare]\nenabled = 'yes'\n", "cloudflare.enabled"),
])
def test_the_check_refuses_what_the_schema_passes_and_tunnel_would_not(text, key):
    with pytest.raises(ConfigError) as caught:
        settings.check(text)
    assert caught.value.key == key


def test_the_install_listener_has_both_hooks_and_does_nothing_yet():
    (ep,) = [e for e in entry_points(group="moxi.installs") if e.name == "tunnel"]
    assert ep.value == "moxi_tunnel.installs"
    assert installs.on_install("ui", None) is None
    assert installs.on_uninstall("ui") is None
    assert installs.on_uninstall("tunnel") is None
