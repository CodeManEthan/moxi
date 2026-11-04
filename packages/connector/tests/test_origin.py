"""The origin: where O comes from, every value derived from it, the swap rule's reads,
the port row, and the settings check over `public_url`."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moxi.config import ConfigError

from moxi_connector import origin, settings
from moxi_connector.origin import BadOrigin, Origin, Watch

A = Origin("door.example.net")
B = Origin("other.example.net", 8443)


def test_every_value_derives_from_the_origin_alone():
    assert A.url == A.issuer == A.page_origin == "https://door.example.net"
    assert A.resource == "https://door.example.net/mcp"
    assert A.resource_metadata_url == ("https://door.example.net/.well-known/"
                                       "oauth-protected-resource/mcp")
    assert A.authorization_endpoint == "https://door.example.net/authorize"
    assert A.token_endpoint == "https://door.example.net/token"
    assert A.registration_endpoint == "https://door.example.net/register"
    assert A.revocation_endpoint == "https://door.example.net/revoke"
    assert A.allowed_hosts == ("door.example.net", "door.example.net:443")
    assert A.challenge() == ('Bearer resource_metadata="https://door.example.net/'
                             '.well-known/oauth-protected-resource/mcp"')
    assert B.url == "https://other.example.net:8443"
    assert B.resource == "https://other.example.net:8443/mcp"
    assert B.allowed_hosts == ("other.example.net", "other.example.net:8443")


def test_form_action_allows_itself_and_each_callback_origin_once():
    uris = ["https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback",
            "https://claude.ai/other"]
    assert A.form_action(uris) == "'self' https://claude.ai https://claude.com"


@pytest.mark.parametrize("value", ["door.example.net", "DOOR.example.NET",
                                   "door.example.net:443", " door.example.net "])
def test_the_host_gate_admits_the_host_in_any_case_with_or_without_its_port(value):
    assert A.admits_host(value)


@pytest.mark.parametrize("value", [None, "", "evil.example.net", "door.example.net:8443",
                                   "door.example.net.evil.net", "127.0.0.1:8452",
                                   "localhost", "door.example.net:443:443"])
def test_the_host_gate_refuses_anything_else(value):
    assert not A.admits_host(value)


@pytest.mark.parametrize(("text", "want"), [
    ("https://door.example.net", A),
    ("https://door.example.net/", A),
    ("https://Door.Example.Net:443", A),
    ("https://other.example.net:8443", B),
])
def test_a_public_url_names_its_origin(text, want):
    assert origin.parse(text) == want


@pytest.mark.parametrize("text", [
    "http://door.example.net", "https://door.example.net/mcp", "https://door.example.net?x=1",
    "https://door.example.net#f", "https://user@door.example.net", "https://192.0.2.1",
    "https://[::1]", "https://localhost", "https://door.example.net:0",
    "https://door.example.net:99999", "door.example.net", "https://-bad-.example.net",
])
def test_a_public_url_that_is_not_an_https_dns_origin_is_refused(text):
    with pytest.raises(BadOrigin):
        origin.parse(text)


def _address(state="on", exposure="internet", host="door.example.net", port=443):
    return SimpleNamespace(state=state, exposure=exposure, host=host, port=port)


def test_public_url_wins_over_tunnel_and_is_read_each_time():
    assert origin.read("https://other.example.net:8443", lambda: _address()) == B
    assert origin.read("", lambda: _address()) == A
    assert origin.read("  ", lambda: _address(port=8443)) == Origin("door.example.net", 8443)


@pytest.mark.parametrize("address", [None, _address(state="pending"),
                                     _address(state="off"), _address(exposure="tailnet")])
def test_tunnel_gives_an_origin_only_while_on_and_reaching_the_internet(address):
    assert origin.read("", lambda: address) is None


def test_a_hand_edited_bad_public_url_reads_as_no_origin():
    assert origin.read("http://door.example.net", lambda: _address()) is None


def test_without_tunnel_installed_there_is_no_tunnel_address(monkeypatch):
    from moxi import modules

    monkeypatch.setattr(modules, "load", lambda name, interface=None: SimpleNamespace(
        state="absent", api=None))
    assert origin.tunnel_address() is None


def test_a_tunnel_record_that_cannot_be_read_is_no_address(monkeypatch):
    from moxi import modules

    def broken(*args):
        raise OSError("record unreadable")

    monkeypatch.setattr(modules, "load", lambda name, interface=None: SimpleNamespace(
        state=modules.PRESENT, api=SimpleNamespace(address=broken)))
    assert origin.tunnel_address() is None


def test_a_different_origin_swaps_at_once():
    w = Watch(A)
    assert w.see(A) is None
    assert w.see(B) == B
    assert w.current == B


def test_one_read_with_no_origin_swaps_nothing_and_two_in_a_row_swap_to_none():
    w = Watch(A)
    assert w.see(None) is None
    assert w.current == A
    assert w.see(A) is None
    assert w.see(None) is None
    assert w.see(None) == Watch.NONE
    assert w.current is None
    assert w.see(None) is None
    assert w.see(A) == A


def test_a_start_with_no_origin_takes_the_first_one_at_once():
    w = Watch(None)
    assert w.see(None) is None
    assert w.see(A) == A


def test_the_port_row_warns_only_off_the_ports_claude_is_known_to_reach():
    assert origin.REACHED_PORTS == frozenset({443})
    assert A.port_warning() is None
    assert B.port_warning() == ("Claude's connector service is known to reach only ports "
                                "443. Claude may say it couldn't reach the server.")
    assert origin.port_warning(8443, {443, 8443}) is None


# -- the settings check ----------------------------------------------------------------

def test_the_defaults_pass_and_read_as_designed():
    values = settings.load()
    assert values["enabled"] is False
    assert values["port"] == 8452
    assert values["public_url"] == ""
    assert values["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback",
                                       "https://claude.com/api/mcp/auth_callback"]
    assert values["refresh_days"] == 30
    settings.check("")


@pytest.mark.parametrize(("text", "key"), [
    ('public_url = "http://door.example.net"', "public_url"),
    ('public_url = "https://door.example.net/mcp"', "public_url"),
    ("port = 0", "port"),
    ("port = 70000", "port"),
    ('redirect_uris = ["http://claude.ai/cb"]', "redirect_uris"),
    ("refresh_days = 0", "refresh_days"),
])
def test_the_check_refuses_what_the_connector_would_not_take(text, key):
    with pytest.raises(ConfigError) as e:
        settings.check(text)
    assert key in str(e.value)


def test_the_check_takes_a_good_public_url():
    settings.check('public_url = "https://door.example.net:8443"\nenabled = true\n')


def test_the_switch_is_read_at_each_use(config_file):
    assert settings.enabled() is False
    config_file("enabled = true\n")
    assert settings.enabled() is True
