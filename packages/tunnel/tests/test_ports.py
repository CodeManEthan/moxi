"""Choosing the outside port: Funnel's three, Serve's default and its fallbacks, the
funnel-ports grant, holders and `--replace`, and Cloudflare's hostnames."""

from __future__ import annotations

import pytest

from moxi_tunnel import ports
from moxi_tunnel.errors import Refused
from moxi_tunnel.record import Entry, Record, State


def choose(reach, target=8461, **kw):
    kw.setdefault("moxi", {})
    kw.setdefault("foreign", {})
    return ports.choose(reach, target=target, **kw)


def test_funnel_takes_the_first_free_of_its_three_ports():
    assert choose("public").port == 443
    assert choose("public", moxi={443: "a/x"}).port == 8443
    assert choose("public", moxi={443: "a/x"}, foreign={8443: "a mount"}).port == 10000


def test_all_three_funnel_ports_taken_lists_their_holders():
    with pytest.raises(Refused) as caught:
        choose("public", moxi={443: "a/x", 10000: "b/y"}, foreign={8443: "a web mount"})
    assert caught.value.exit_code == 3
    assert caught.value.reason == ("every Funnel port is taken (443: a/x; 8443: a web "
                                   "mount; 10000: b/y)")


def test_the_funnel_ports_grant_narrows_the_three():
    assert choose("public", funnel_ports=[10000, 8443]).port == 8443
    with pytest.raises(Refused, match="every Funnel port is taken"):
        choose("public", funnel_ports=[8443], moxi={8443: "a/x"})
    with pytest.raises(Refused, match="grant doesn't allow port 443") as caught:
        choose("public", port=443, funnel_ports=[8443])
    assert "give 8443" in caught.value.fix
    with pytest.raises(Refused, match="allows none"):
        choose("public", funnel_ports=[])


def test_a_public_port_must_be_a_funnel_port():
    assert choose("public", port=10000).port == 10000
    with pytest.raises(Refused, match="8461 is not a Funnel port"):
        choose("public", port=8461)


def test_serve_takes_the_targets_own_port_when_it_is_free():
    assert choose("own-devices", target=8461) == ports.Choice(8461)


def test_serve_falls_back_above_the_target_and_skips_funnel_ports():
    assert choose("own-devices", target=8461, moxi={8461: "a/x"},
                  foreign={8462: "x"}).port == 8463
    assert choose("own-devices", target=443).port == 444
    assert choose("own-devices", target=8442, moxi={8442: "a/x"}).port == 8444


def test_serve_falls_back_to_1024_up_past_65535():
    taken = {p: "a/x" for p in range(65000, 65536)}
    assert choose("own-devices", target=65000, moxi=taken).port == 1024
    assert choose("own-devices", target=65000, moxi={**taken, 1024: "b/y"}).port == 1025


def test_the_serve_default_is_total_and_refuses_only_with_every_port_taken():
    taken = {p: "a/x" for p in range(1, 65536)}
    with pytest.raises(Refused, match="every port is taken"):
        choose("own-devices", target=8461, moxi=taken)
    free = dict(taken)
    del free[2000]
    assert choose("own-devices", target=8461, moxi=free).port == 2000


def test_serve_on_a_named_funnel_port_says_so():
    choice = choose("own-devices", port=8443)
    assert choice.port == 8443 and len(choice.lines) == 1 and "8443" in choice.lines[0]
    assert choose("own-devices", port=9000).lines == ()
    with pytest.raises(Refused, match="not a port"):
        choose("own-devices", port=70000)


def test_a_port_another_moxi_target_holds_is_refused_naming_it():
    with pytest.raises(Refused, match="port 9000 is held by beta/web") as caught:
        choose("own-devices", port=9000, moxi={9000: "beta/web"}, replace=True)
    assert "moxi tunnel remove beta/web" in caught.value.fix


def test_foreign_config_is_refused_unless_replace_takes_it_over():
    with pytest.raises(Refused, match="Moxi did not apply: a proxy to :3000") as caught:
        choose("own-devices", port=9000, foreign={9000: "a proxy to :3000"})
    assert "--replace" in caught.value.fix
    assert choose("own-devices", port=9000, foreign={9000: "x"},
                  replace=True) == ports.Choice(9000, replace=True)
    # With no --port, --replace takes over the port the add would choose.
    assert choose("own-devices", target=8461, foreign={8461: "x"},
                  replace=True) == ports.Choice(8461, replace=True)
    assert choose("own-devices", target=8461, foreign={8461: "x"}) == ports.Choice(8462)
    assert choose("public", foreign={443: "x"}, replace=True) == ports.Choice(443, True)
    assert choose("public", foreign={443: "x"}, replace=False) == ports.Choice(8443)
    assert choose("own-devices", port=9000, replace=True) == ports.Choice(9000)


def _entry(provider, host, port):
    return Entry(reach="own-devices", provider=provider, host=host, port=port, target=1,
                 state=State.ON)


def test_moxi_ports_are_the_records_tailscale_entries():
    record = Record(entries={"a/x": _entry("tailscale", "box.ts.net", 8461),
                             "b/y": _entry("cloudflare", "y.example.com", 443),
                             "c/z": _entry("tailscale", "box.ts.net", 443)})
    assert ports.moxi_ports(record) == {8461: "a/x", 443: "c/z"}
    assert ports.moxi_ports(record, exclude="c/z") == {8461: "a/x"}


def test_an_entry_a_newer_tunnel_wrote_holds_its_port():
    from dataclasses import replace

    newer = replace(_entry("tailscale", "box.ts.net", 9100), form="tcp")
    record = Record(entries={"a/x": _entry("tailscale", "box.ts.net", 8461), "n/w": newer,
                             "o/v": replace(_entry("cloudflare", "v.example.com", 443),
                                            state="paused")})
    held = ports.moxi_ports(record)
    assert held == {8461: "a/x", 9100: "n/w (written by a newer tunnel)"}
    for kw in ({"replace": False}, {"replace": True}):
        with pytest.raises(Refused, match="held by n/w .written by a newer tunnel") as caught:
            choose("own-devices", port=9100, moxi=held, foreign={}, **kw)
        assert caught.value.fix.startswith("upgrade moxi-tunnel")
    assert choose("own-devices", target=9100, moxi=held).port == 9101


def test_two_cloudflare_targets_may_not_share_a_hostname():
    record = Record(entries={"b/y": _entry("cloudflare", "y.example.com", 443),
                             "a/x": _entry("tailscale", "z.example.com", 443)})
    assert ports.hostname_holder("Y.Example.com.", record) == "b/y"
    assert ports.hostname_holder("y.example.com", record, exclude="b/y") is None
    assert ports.hostname_holder("z.example.com", record) is None
