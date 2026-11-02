"""The targets modules declare in `moxi.tunnel`: names, the dict, the port setting rule,
bad declarations, and the clash rule."""

from __future__ import annotations

import pytest

from moxi_tunnel import declare
from moxi_tunnel.errors import Refused

GOOD = """
GATEWAY = {"reach": "own-devices", "port_setting": "port", "doc": "the page",
           "asks": ["later"], "upstream": "x"}
CONNECTOR = {"reach": "public", "port_setting": "other.port"}
"""


def test_a_target_is_named_module_slash_name_and_unknown_keys_are_ignored(site):
    site.module("alpha", {"alpha.gateway": "GATEWAY", "alpha.connector": "CONNECTOR"},
                values=GOOD)
    got = declare.read()
    assert got.bad == ()
    assert list(got.targets) == ["alpha/connector", "alpha/gateway"]
    gateway = got.targets["alpha/gateway"]
    assert (gateway.module, gateway.name, gateway.reach, gateway.port_setting,
            gateway.doc) == ("alpha", "gateway", "own-devices", "port", "the page")
    assert got.targets["alpha/connector"].doc == ""


@pytest.mark.parametrize("entry, values, reason", [
    ("gateway", GOOD, "not <module>.<name>"),
    ("alpha.", GOOD, "not <module>.<name>"),
    ("beta.gateway", GOOD, "module part 'beta' is not the module of moxi-alpha"),
    ("alpha.gateway", "GATEWAY = ['own-devices']", "a list, not a dict"),
    ("alpha.gateway", "GATEWAY = {'reach': 'everyone', 'port_setting': 'port'}",
     "its reach is 'everyone'"),
    ("alpha.gateway", "GATEWAY = {'reach': 'public', 'port_setting': 'name'}",
     "port_setting name is not an int setting of alpha"),
    ("alpha.gateway", "GATEWAY = {'reach': 'public', 'port_setting': 'nosuch'}",
     "port_setting nosuch is not an int setting"),
    ("alpha.gateway", "GATEWAY = {'reach': 'public'}", "port_setting is not the name"),
    ("alpha.gateway", "GATEWAY = {'reach': 'public', 'port_setting': 'port', 'doc': 3}",
     "doc is not a string"),
    ("alpha.gateway", "", "loading"),
])
def test_a_bad_declaration_is_named_with_its_reason(site, entry, values, reason):
    site.module("alpha", {entry: "GATEWAY"}, values=values)
    got = declare.read()
    assert got.targets == {}
    (bad,) = got.bad
    assert bad.entry == entry and bad.distribution == "moxi-alpha"
    assert reason in bad.reason
    assert "moxi-alpha" in bad.fix


def test_a_module_without_settings_declares_a_bad_target(site):
    site.module("alpha", {"alpha.gateway": "GATEWAY"}, values=GOOD, settings=None)
    (bad,) = declare.read().bad
    assert "declares no settings" in bad.reason


def test_one_bad_declaration_leaves_the_others(site):
    site.module("alpha", {"alpha.gateway": "GATEWAY", "alpha.broken": "NOSUCH"},
                values=GOOD)
    got = declare.read()
    assert list(got.targets) == ["alpha/gateway"]
    assert [b.entry for b in got.bad] == ["alpha.broken"]


def test_the_loopback_port_is_read_from_its_setting_at_each_use(site, config_file):
    site.module("alpha", {"alpha.gateway": "GATEWAY"}, values=GOOD)
    target = declare.read().targets["alpha/gateway"]
    assert declare.loopback_port(target) == 8461
    config_file("alpha", "port = 8470\n")
    assert declare.loopback_port(target) == 8470


@pytest.mark.parametrize("text, problem", [("port = 0\n", "not a port"),
                                           ("port = 70000\n", "not a port"),
                                           ("port = 'x'\n", "can't be read")])
def test_a_setting_that_holds_no_port_is_refused(site, config_file, text, problem):
    site.module("alpha", {"alpha.gateway": "GATEWAY"}, values=GOOD)
    target = declare.read().targets["alpha/gateway"]
    config_file("alpha", text)
    with pytest.raises(Refused) as caught:
        declare.loopback_port(target)
    assert caught.value.exit_code == 3 and problem in caught.value.reason


def test_a_bare_name_resolves_when_one_module_declares_it():
    keys = ["alpha/gateway", "beta/gateway", "beta/connector"]
    assert declare.resolve("beta/gateway", keys) == "beta/gateway"
    assert declare.resolve("connector", keys) == "beta/connector"
    with pytest.raises(Refused, match="2 modules declare a target gateway"):
        declare.resolve("gateway", keys)
    with pytest.raises(Refused, match="no target nosuch") as caught:
        declare.resolve("nosuch", keys)
    assert "alpha/gateway" in caught.value.fix
    with pytest.raises(Refused, match="no target gamma/gateway"):
        declare.resolve("gamma/gateway", keys)


def test_two_targets_on_one_loopback_port_clash():
    ports = {"alpha/gateway": 8461, "beta/connector": 8461, "beta/web": 9000}
    assert declare.same_port(8461, ports, exclude="alpha/gateway") == ["beta/connector"]
    assert declare.same_port(9001, ports) == []
    assert declare.clashes(ports) == {"alpha/gateway": ["beta/connector"],
                                      "beta/connector": ["alpha/gateway"]}
    assert declare.clashes({"alpha/gateway": 1, "beta/web": 2}) == {}
