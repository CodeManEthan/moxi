"""A declaration's upstream, the unix form, retired origins, httpHostHeader and the group
only this version reads, against the fake `tailscale` and tunnel's fake units."""

from __future__ import annotations

import json
import socket
from dataclasses import replace

import pytest

import moxi_tunnel
from fake_tailscale import HOST
from moxi import files, paths
from moxi_tunnel import acts, api, cloudflare, confirm as warn, declare, doctor, module
from moxi_tunnel import record, uninstall
from moxi_tunnel.errors import NotReady, Refused

DECL = """
import json, pathlib
from moxi_tunnel import NotReady
STATE = pathlib.Path(__file__).with_name("up.json")
def upstream():
    got = json.loads(STATE.read_text())
    if "not_ready" in got:
        raise NotReady(got["not_ready"])
    return got["value"]
def retired():
    return json.loads(STATE.read_text()).get("retired", [])
GATEWAY = {"reach": "own-devices", "port_setting": "port", "doc": "the page",
           "upstream": "moxi_beta.tunnel_decl:upstream",
           "retired": "moxi_beta.tunnel_decl:retired"}
PUBLIC = {"reach": "public", "port_setting": "other.port",
          "upstream": "moxi_beta.tunnel_decl:upstream"}
"""
SOCK = "/run/user/1000/moxi/ui/gateway.sock"
GW = "beta/gateway"


class Crash(BaseException):
    """A crash: nothing in tunnel catches it."""


@pytest.fixture
def beta(site):
    """Module `beta`: `beta/gateway` in `moxi.tunnel.targets`, its upstream and retired
    read from a file the test sets with `up(...)`."""
    pkg = site.module("beta", targets_group={"beta.gateway": "GATEWAY",
                                             "beta.public": "PUBLIC"}, values=DECL)
    state = site.root / pkg / "up.json"

    def up(value=f"unix:{SOCK}", **more):
        state.write_text(json.dumps({"value": value, **more}))
    up()
    return up


def states():
    return {s.target: s.check for s in acts.check()}


def retired():
    return record.read().retired


def crash_at(monkeypatch, step):
    def hit(name):
        if name == step:
            raise Crash
    monkeypatch.setattr(acts, "_step", hit)


# -- the group and the version --------------------------------------------------------------

def test_tunnel_offers_interface_2_and_exports_not_ready():
    assert module.INTERFACES == {1, 2}
    assert moxi_tunnel.NotReady is NotReady and issubclass(NotReady, Refused)


@pytest.mark.parametrize("value, reason", [
    ('{"reach": "own-devices", "port_setting": "port", "upstream": "m:f"}', "names upstream"),
    ('{"reach": "own-devices", "port_setting": "port", "retired": "m:f"}', "names retired"),
    ('{"reach": "own-devices", "port_setting": "port", "lifetime": "process"}', "lifetime"),
])
def test_the_old_group_refuses_the_new_keys(site, value, reason):
    site.module("alpha", {"alpha.gateway": "G"}, values=f"G = {value}")
    (bad,) = declare.read().bad
    assert reason in bad.reason and bad.group == declare.GROUP


def test_a_target_in_both_groups_is_bad_and_an_add_finds_none(site, tailscale, yes):
    site.module("alpha", {"alpha.gateway": "G"}, targets_group={"alpha.gateway": "G"},
                values='G = {"reach": "own-devices", "port_setting": "port"}')
    got = declare.read()
    assert got.targets == {} and {b.group for b in got.bad} == set(declare.GROUPS)
    with pytest.raises(Refused, match="declared in both"):
        acts.add("alpha/gateway", ask=yes)
    assert tailscale.changes() == []


# -- upstream -------------------------------------------------------------------------------

def test_not_ready_refuses_an_add_and_applies_nothing(beta, tailscale, yes):
    beta(not_ready="the gateway's shell doesn't hold its port")
    with pytest.raises(NotReady, match="doesn't hold its port") as caught:
        acts.add(GW, ask=yes)
    assert caught.value.exit_code == 3 and tailscale.changes() == []
    assert not record.record_path().exists()


def test_not_ready_holds_a_repairs_reapply_and_applies_nothing(beta, tailscale, yes):
    beta("http://127.0.0.1:8461")
    acts.add(GW, ask=yes)
    tailscale.set_serve({})
    beta(not_ready="not yet")
    assert states() == {GW: "missing"}
    got = acts.repair(ask=yes)
    assert got.code == 3 and any("not yet" in n for n in got.notes)
    assert len(tailscale.changes()) == 1


def test_a_unix_entry_is_applied_matched_checked_and_answers(beta, tailscale, yes,
                                                             tmp_path):
    got = acts.add(GW, ask=yes).address
    assert tailscale.changes() == [["serve", "--bg", "--yes", "--https=8461",
                                    f"unix:{SOCK}"]]
    assert (got.form, got.target, got.port, got.retired) == ("unix", SOCK, 8461, False)
    assert states() == {GW: "ok"} and acts.add(GW, ask=yes).address == got
    path = str(tmp_path / "g.sock")
    entries = {GW: replace(record.read().entries[GW], target=path)}

    def answers():
        (row,) = doctor._answer_rows(entries, connect_wait=1, deadline=1e18, budget=1,
                                     clock=lambda: 0)
        return row
    assert answers().state == "warn" and path in answers().detail
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(path)
        sock.listen()
        assert answers().state == "ok"


def test_a_changed_path_reads_moved_and_repair_follows_it_on_its_port(beta, tailscale,
                                                                      yes):
    acts.add(GW, ask=yes)
    beta("unix:/run/other.sock")
    assert states() == {GW: "moved"}
    assert acts.repair(ask=yes).code == 0
    assert tailscale.changes()[-1] == ["serve", "--bg", "--yes", "--https=8461",
                                       "unix:/run/other.sock"]
    assert states() == {GW: "ok"} and retired() == frozenset()
    beta(not_ready="down")
    assert states() == {GW: "ok"}


@pytest.mark.parametrize("value", ["tcp://x", "unix:relative", "http://127.0.0.1:0"])
def test_an_upstream_tunnel_cant_apply_is_refused(beta, tailscale, yes, value):
    beta(value)
    with pytest.raises(Refused, match="upstream gave"):
        acts.add(GW, ask=yes)


def test_funnel_refuses_a_socket(beta, tailscale, yes):
    with pytest.raises(Refused, match="Funnel"):
        acts.add("beta/public", ask=yes)
    assert tailscale.changes() == []


def test_every_cloudflare_rule_sends_its_hostname_as_the_host(beta, alpha, cf, units,
                                                              yes):
    acts.add(GW, provider_name="cloudflare", hostname="gw.example.com", ask=yes)
    acts.add("alpha/gateway", provider_name="cloudflare", hostname="a.example.com",
             ask=yes)
    text = cloudflare.read_file().text
    assert ('    service: "unix:' + SOCK + '"\n    originRequest:\n'
            '      httpHostHeader: "gw.example.com"\n') in text
    assert ('    service: "http://127.0.0.1:8461"\n    originRequest:\n'
            '      httpHostHeader: "a.example.com"\n') in text
    assert states() == {GW: "ok", "alpha/gateway": "ok"}


# -- retired origins ------------------------------------------------------------------------

def test_retire_first_with_a_crash_before_an_adds_apply(alpha, tailscale, yes, monkeypatch):
    crash_at(monkeypatch, "add-applying")
    with pytest.raises(Crash):
        acts.add("alpha/gateway", ask=yes)
    assert tailscale.changes() == [] and retired() == {(HOST, 8461)}


@pytest.mark.parametrize("rename", [False, True])
def test_retire_first_with_a_crash_before_a_reapply(alpha, tailscale, yes, monkeypatch,
                                                    rename):
    acts.add("alpha/gateway", ask=yes)
    raw = json.loads(record.record_path().read_text())
    raw["retired"] = []
    record.record_path().write_text(json.dumps(raw))
    if rename:
        tailscale.rename("laptop.tail1234.ts.net")
    else:
        tailscale.set_serve({})
    crash_at(monkeypatch, "repair-applying")
    with pytest.raises(Crash):
        acts.repair(ask=yes)
    want = ("laptop.tail1234.ts.net" if rename else HOST, 8461)
    assert retired() == {want, (HOST, 8461)} and len(tailscale.changes()) == 1


def test_retire_first_with_a_crash_in_a_switch(alpha, cf, units, tailscale, yes,
                                               monkeypatch):
    acts.add("alpha/gateway", provider_name="cloudflare", hostname="a.example.com",
             ask=yes)
    crash_at(monkeypatch, "switch-applying")
    with pytest.raises(Crash):
        acts.add("alpha/gateway", provider_name="tailscale", ask=yes)
    assert retired() == {("a.example.com", 443), (HOST, 8461)}
    assert tailscale.changes() == []


def test_a_replace_retires_the_foreign_origin_and_says_so(beta, tailscale, yes):
    tailscale.foreign(8470)
    got = acts.add(GW, port=8470, replace_=True, ask=yes).address
    assert (got.port, got.form, got.retired) == (8470, "unix", True)
    assert (HOST, 8470) in retired() and warn.RETIRED_LINE in yes.asked[0]


def test_a_record_without_the_list_reads_every_http_origin_and_stores_it(alpha, tailscale,
                                                                         yes):
    acts.add("alpha/gateway", ask=yes)
    raw = json.loads(record.record_path().read_text())
    del raw["retired"]
    record.record_path().write_text(json.dumps(raw))
    assert retired() == {(HOST, 8461)} and api.address("alpha", "gateway").retired
    acts.add("alpha/connector", ask=yes)
    stored = json.loads(record.record_path().read_text())["retired"]
    assert {(o["host"], o["port"]) for o in stored} == {(HOST, 8461), (HOST, 443)}


def test_an_http_entry_missing_from_the_list_is_retired_and_stored_at_the_next_write(
        alpha, tailscale, yes):
    """An older tunnel keeps the list as it is and adds its own entries to none, so an
    `http` entry it made live is read as retired anyway, and stays so once removed."""
    acts.add("alpha/gateway", ask=yes)
    raw = json.loads(record.record_path().read_text())
    raw["retired"] = [{"host": "a.example.com", "port": 443}]
    record.record_path().write_text(json.dumps(raw))
    assert retired() == {("a.example.com", 443), (HOST, 8461)}
    assert record.read().is_retired(HOST, 8461)
    acts.remove("alpha/gateway", ask=yes)
    assert record.read().entries == {}
    stored = json.loads(record.record_path().read_text())["retired"]
    assert [(o["host"], o["port"]) for o in stored] == [("a.example.com", 443), (HOST, 8461)]


def test_the_list_survives_every_act_and_moves_no_token(alpha, tailscale, units, yes):
    acts.add("alpha/gateway", ask=yes)
    token = api.address("alpha", "gateway").change
    with files.lock(paths.core_lock()), record.locks.held():
        record.commit(retire=[("old.example.com", 9999)])
    assert api.address("alpha", "gateway").change == token
    want = {(HOST, 8461), (HOST, 8462), ("old.example.com", 9999)}
    acts.remove("alpha/gateway", ask=yes)
    acts.add("alpha/gateway", ask=yes)
    acts.remove("alpha/gateway", ask=yes, forget=True)
    tailscale.fail("apply")
    with pytest.raises(Exception):
        acts.add("alpha/gateway", ask=yes)
    tailscale.ok()
    acts.add("alpha/gateway", ask=yes)
    with files.lock(paths.core_lock()):
        uninstall.teardown()
    assert record.read().entries == {} and retired() == want


def test_fresh_placement_for_a_socket(beta, alpha, tailscale, yes, config_file):
    assert acts.add(GW, ask=yes).address.port == 8461
    acts.remove(GW, ask=yes)
    assert acts.add(GW, ask=yes).address.port == 8461  # a socket retires nothing
    acts.remove(GW, ask=yes)
    beta("http://127.0.0.1:8461")
    assert acts.add(GW, ask=yes).address.port == 8461
    config_file("alpha", "port = 8462\n")
    acts.add("alpha/gateway", ask=yes)
    beta()
    assert states()[GW] == "moved"
    assert acts.repair(ask=yes).code == 0
    got = api.address("beta", "gateway")
    assert (got.port, got.form, got.retired) == (8463, "unix", False)
    assert str(8461) not in tailscale.serve["TCP"] and states()[GW] == "ok"


def test_a_retired_port_or_hostname_the_user_names_is_taken_with_a_line(beta, tailscale,
                                                                        cf, units, yes):
    beta("http://127.0.0.1:8461")
    acts.add(GW, ask=yes)
    acts.remove(GW, ask=yes)
    assert acts.add(GW, ask=yes).address.port == 8461  # a persistent add: the default
    acts.remove(GW, ask=yes)
    beta()
    got = acts.add(GW, port=8461, ask=yes).address
    assert got.retired and warn.RETIRED_LINE in yes.asked[-1]
    acts.remove(GW, ask=yes)
    beta("http://127.0.0.1:8461")
    acts.add(GW, provider_name="cloudflare", hostname="gw.example.com", ask=yes)
    acts.remove(GW, ask=yes)
    beta()
    got = acts.add(GW, provider_name="cloudflare", hostname="gw.example.com",
                   ask=yes).address
    assert got.retired and warn.RETIRED_LINE in yes.asked[-1]


def test_the_declarations_copy_refills_a_purged_record(beta, tailscale, yes):
    beta(retired=[{"host": HOST, "port": 8461}])
    got = acts.add(GW, ask=yes).address
    assert got.port == 8462 and (HOST, 8461) in retired()
