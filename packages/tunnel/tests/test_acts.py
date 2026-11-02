"""The acts against the fake `tailscale`: add, the no-op add, remove, forget, foreign ports,
failures rolled back or kept, the check states and repair, and the warnings."""

from __future__ import annotations

import json

import pytest

from fake_tailscale import HOST
from moxi_tunnel import acts, api, locks, record
from moxi_tunnel import confirm as warn
from moxi_tunnel.errors import ApplyError, Busy, Refused, UsageError
from moxi_tunnel.record import State

NEW = "laptop.tail1234.ts.net"


def states() -> dict[str, str]:
    return {s.target: s.check for s in acts.check()}


def events() -> list[str]:
    path = record.events_path()
    if not path.exists():
        return []
    return [json.loads(line)["event"] for line in path.read_text().splitlines()]


def test_an_add_on_serve_takes_the_targets_own_port(alpha, tailscale, yes):
    got = acts.add("alpha/gateway", ask=yes)
    assert got.address.url == f"https://{HOST}:8461/"
    assert (got.address.exposure, got.address.state, got.address.target) == \
        ("tailnet", "on", 8461)
    assert tailscale.changes() == [["serve", "--bg", "--yes", "--https=8461",
                                    "http://127.0.0.1:8461"]]
    assert states() == {"alpha/gateway": "ok"}
    assert record.read().warned_ct == (HOST,)
    assert events() == ["add"]
    lines = yes.asked[0]
    assert warn.ct_line(HOST) in lines and warn.PAIR_LINE in lines
    assert not any("Anyone on the internet" in line for line in lines)


def test_a_bare_name_works_when_one_module_declares_it(alpha, tailscale, yes):
    assert acts.add("gateway", ask=yes).address.name == "gateway"


def test_a_public_add_takes_funnel_443_and_says_so(alpha, tailscale, yes):
    got = acts.add("alpha/connector", ask=yes)
    assert got.address.url == f"https://{HOST}/" and got.address.exposure == "internet"
    assert tailscale.changes()[0][:4] == ["funnel", "--bg", "--yes", "--https=443"]
    lines = yes.asked[0]
    assert warn.internet_line(got.address.url, "alpha") in lines
    assert warn.funnel_line(HOST) in lines


def test_an_add_already_on_changes_nothing(alpha, tailscale, yes):
    first = acts.add("alpha/gateway", ask=yes)
    again = acts.add("alpha/gateway", ask=yes)
    assert again.address == first.address and "already on" in again.notes[0]
    assert len(yes.asked) == 1 and len(tailscale.changes()) == 1


def test_the_ct_line_shows_once_per_host(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    acts.add("alpha/connector", ask=yes)
    assert warn.ct_line(HOST) not in yes.asked[1]


def test_no_confirmation_refuses_and_changes_nothing(alpha, tailscale):
    with pytest.raises(Refused) as caught:
        acts.add("alpha/gateway", ask=lambda lines: False)
    assert caught.value.exit_code == 3
    assert tailscale.changes() == [] and not record.record_path().exists()


def test_no_lock_is_held_while_asking(alpha, tailscale):
    seen = []
    acts.add("alpha/gateway",
             ask=lambda lines: seen.append((locks.holds_core(), locks.holds_tunnel())) or True)
    assert seen == [(False, False)]


def test_a_plan_that_changed_under_the_lock_asks_again(alpha, tailscale):
    asked = []

    def ask(lines):
        asked.append(lines)
        if len(asked) == 1:
            tailscale.foreign(8461)
        return True

    got = acts.add("alpha/gateway", ask=ask)
    assert len(asked) == 2 and got.address.port == 8462


def test_a_plan_that_changes_twice_exits_75(alpha, tailscale):
    ports = iter([8461, 8462])

    def ask(lines):
        tailscale.foreign(next(ports, 8463))
        return True

    with pytest.raises(Busy) as caught:
        acts.add("alpha/gateway", ask=ask)
    assert caught.value.exit_code == 75 and tailscale.changes() == []


def test_an_add_refuses_when_tailscale_is_not_ready_naming_each_fix(alpha, tailscale, yes):
    tailscale.edit(lambda s: s["status"].update(BackendState="Stopped"))
    tailscale.edit(lambda s: s["status"]["CurrentTailnet"].update(MagicDNSEnabled=False))
    with pytest.raises(Refused) as caught:
        acts.add("alpha/gateway", ask=yes)
    assert "tailscale up" in caught.value.reason and "MagicDNS" in caught.value.reason
    assert yes.asked == [] and tailscale.changes() == []


def test_an_add_with_no_tailscale_names_the_missing_cli_first(alpha, tmp_path, monkeypatch,
                                                              yes):
    from moxi_tunnel import tailscale as ts

    monkeypatch.setenv("PATH", str(tmp_path / "none"))
    monkeypatch.setattr(ts, "PLATFORM_PATHS", {})
    with pytest.raises(Refused) as caught:
        acts.add("alpha/gateway", ask=yes)
    assert "not installed" in caught.value.reason


def test_a_bad_declaration_is_refused(site, tailscale, yes):
    site.module("alpha", {"alpha.gateway": "GATEWAY"},
                values="GATEWAY = {'reach': 'everyone', 'port_setting': 'port'}")
    with pytest.raises(Refused) as caught:
        acts.add("alpha/gateway", ask=yes)
    assert "everyone" in caught.value.reason


def test_two_targets_on_one_loopback_port_are_refused(alpha, tailscale, yes, config_file):
    acts.add("alpha/gateway", ask=yes)
    config_file("alpha", "[other]\nport = 8461\n")
    with pytest.raises(Refused) as caught:
        acts.add("alpha/connector", ask=yes)
    assert "also alpha/gateway's" in caught.value.reason


def test_a_foreign_port_is_refused_and_serve_falls_back_above_it(alpha, tailscale, yes):
    tailscale.foreign(8461)
    got = acts.add("alpha/gateway", ask=yes)
    assert got.address.port == 8462
    with pytest.raises(Refused):
        acts.add("alpha/connector", port=8461, ask=yes)


def test_all_three_funnel_ports_taken_names_their_holders(alpha, tailscale, yes):
    for port in (443, 8443, 10000):
        tailscale.foreign(port, funnel=True)
    with pytest.raises(Refused) as caught:
        acts.add("alpha/connector", ask=yes)
    assert "443" in caught.value.reason and "10000" in caught.value.reason


def test_the_funnel_ports_grant_narrows_the_choice(alpha, tailscale, yes):
    tailscale.edit(lambda s: s["status"]["Self"].update(CapMap={
        "https": None, "funnel": None,
        "https://tailscale.com/cap/funnel-ports?ports=8443": None}))
    assert acts.add("alpha/connector", ask=yes).address.port == 8443


def test_a_trailing_dot_and_case_never_make_a_second_host(alpha, tailscale, yes):
    tailscale.rename("BOX.tail1234.ts.net")
    acts.add("alpha/gateway", ask=yes)
    assert record.read().entries["alpha/gateway"].host == HOST
    assert states() == {"alpha/gateway": "ok"}


def test_cloudflare_is_refused_until_it_is_set_up_and_its_options_are_checked(alpha, yes):
    with pytest.raises(Refused) as caught:
        acts.add("alpha/gateway", provider_name="cloudflare", hostname="a.example.com",
                 ask=yes)
    assert "Cloudflare is not ready" in caught.value.reason
    assert "tunnel id" in caught.value.reason and "credentials" in caught.value.reason
    assert yes.asked == [] and record.read().entries == {}
    with pytest.raises(UsageError):
        acts.add("alpha/gateway", provider_name="cloudflare", replace_=True, ask=yes)
    with pytest.raises(UsageError):
        acts.add("alpha/gateway", hostname="a.example.com", ask=yes)


# -- failures ------------------------------------------------------------------------------

def test_a_refused_apply_rolls_back_whole(alpha, tailscale, yes):
    tailscale.fail("apply", "error: not allowed\n")
    before = tailscale.serve
    with pytest.raises(ApplyError) as caught:
        acts.add("alpha/gateway", ask=yes)
    assert caught.value.reason == "error: not allowed\n" and caught.value.exit_code == 1
    assert record.read().entries == {} and tailscale.serve == before
    assert events() == ["rollback"]


def test_an_apply_that_changed_the_live_config_and_failed_is_left_half(alpha, tailscale, yes):
    tailscale.fail("apply", "error: half done\n", after=True)
    with pytest.raises(ApplyError):
        acts.add("alpha/gateway", ask=yes)
    entry = record.read().entries["alpha/gateway"]
    assert entry.state == State.APPLYING and entry.detail == "error: half done\n"
    assert states() == {"alpha/gateway": "half"}
    tailscale.ok()
    assert acts.repair(ask=yes).code == 0
    assert states() == {"alpha/gateway": "ok"}


def test_a_refused_remove_rolls_back_to_on(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    token = api.address("alpha", "gateway").change
    tailscale.fail("off")
    with pytest.raises(ApplyError):
        acts.remove("alpha/gateway", ask=yes)
    got = api.address("alpha", "gateway")
    assert got.state == "on" and got.change != token
    assert states() == {"alpha/gateway": "ok"}


@pytest.mark.parametrize("step", ["add-applying", "add-applied"])
def test_a_crash_inside_an_add_is_finished_by_repair(alpha, tailscale, yes, monkeypatch, step):
    class Crash(BaseException):
        pass

    def crash(name):
        if name == step:
            raise Crash

    monkeypatch.setattr(acts, "_step", crash)
    with pytest.raises(Crash):
        acts.add("alpha/gateway", ask=yes)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    assert states() == {"alpha/gateway": "half"}
    assert acts.repair(ask=yes).code == 0
    assert states() == {"alpha/gateway": "ok"}
    assert len(tailscale.changes()) == 1


def test_a_crash_inside_a_remove_is_finished_by_repair(alpha, tailscale, yes, monkeypatch):
    acts.add("alpha/gateway", ask=yes)

    class Crash(BaseException):
        pass

    def crash(name):
        if name == "remove-removed":
            raise Crash

    monkeypatch.setattr(acts, "_step", crash)
    with pytest.raises(Crash):
        acts.remove("alpha/gateway", ask=yes)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    assert states() == {"alpha/gateway": "half"}
    acts.repair(ask=yes)
    assert record.read().entries == {}


# -- remove and forget ---------------------------------------------------------------------

def test_remove_takes_out_only_moxis_entry(alpha, tailscale, yes):
    tailscale.foreign(443, funnel=True)
    acts.add("alpha/gateway", ask=yes)
    acts.remove("alpha/gateway", ask=yes)
    assert record.read().entries == {}
    assert list(tailscale.serve["TCP"]) == ["443"]
    assert tailscale.changes()[-1] == ["serve", "--https=8461", "off"]


def test_remove_refuses_a_changed_entry_and_touches_nothing(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    tailscale.edit(lambda s: s["serve"]["Web"][f"{HOST}:8461"]["Handlers"].update(
        {"/admin": {"Proxy": "http://127.0.0.1:1"}}))
    assert states() == {"alpha/gateway": "changed"}
    with pytest.raises(Refused) as caught:
        acts.remove("alpha/gateway", ask=yes)
    assert "tailscale serve --https=8461 off" in caught.value.fix
    assert "--forget" in caught.value.fix
    assert record.read().entries["alpha/gateway"].state == State.ON
    assert len(tailscale.changes()) == 1


def test_forget_drops_the_entry_and_leaves_the_live_config(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    live = tailscale.serve
    got = acts.remove("alpha/gateway", ask=yes, forget=True)
    assert record.read().entries == {} and tailscale.serve == live
    assert any("tailscale serve --https=8461 off" in n for n in got.notes)
    assert events() == ["add", "forget"]


def test_remove_of_an_orphan(alpha, tailscale, yes, site):
    acts.add("alpha/gateway", ask=yes)
    (site.root / "moxi_alpha-0.1.0.dist-info" / "entry_points.txt").write_text(
        "[moxi.modules]\nalpha = moxi_alpha.module\n")
    assert states() == {"alpha/gateway": "orphan"}
    assert acts.repair(ask=yes).code == 3
    acts.remove("alpha/gateway", ask=yes)
    assert record.read().entries == {}


def test_remove_of_a_target_whose_live_config_is_gone_drops_it(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    tailscale.set_serve({})
    acts.remove("alpha/gateway", ask=yes)
    assert record.read().entries == {} and len(tailscale.changes()) == 1


# -- check and repair ----------------------------------------------------------------------

def test_missing_is_reapplied_on_the_same_address(alpha, tailscale, yes):
    first = acts.add("alpha/gateway", ask=yes).address
    tailscale.set_serve({})
    assert states() == {"alpha/gateway": "missing"}
    assert acts.repair(ask=yes).code == 0
    got = api.address("alpha", "gateway")
    assert (got.url, got.state) == (first.url, "on") and states() == {"alpha/gateway": "ok"}


def test_moved_is_followed_on_the_same_port_and_host(alpha, tailscale, yes, config_file):
    first = acts.add("alpha/gateway", ask=yes).address
    config_file("alpha", "port = 8500\n")
    assert states() == {"alpha/gateway": "moved"}
    acts.repair(ask=yes)
    got = api.address("alpha", "gateway")
    assert (got.url, got.target) == (first.url, 8500) and got.change != first.change
    assert states() == {"alpha/gateway": "ok"}


def test_renamed_comes_before_missing_and_one_repair_moves_it(alpha, tailscale, yes):
    first = acts.add("alpha/gateway", ask=yes).address
    tailscale.rename(NEW)
    tailscale.set_serve({})
    assert states() == {"alpha/gateway": "renamed"}
    yes.asked.clear()
    acts.repair(ask=yes)
    got = api.address("alpha", "gateway")
    assert got.host == NEW and got.port == first.port and got.change != first.change
    assert states() == {"alpha/gateway": "ok"}
    lines = yes.asked[0]
    assert warn.ct_line(NEW) in lines and warn.REPAIR_LINE in lines
    assert NEW in record.read().warned_ct
    assert events()[-1] == "rename"


def test_a_rename_reports_a_key_left_under_the_old_name(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    tailscale.rename(NEW)
    got = acts.repair(ask=yes)
    assert any(f"{HOST}:8461" in n for n in got.notes)
    assert states() == {"alpha/gateway": "ok"}


def test_renamed_and_moved_is_ok_after_one_repair(alpha, tailscale, yes, config_file):
    acts.add("alpha/gateway", ask=yes)
    tailscale.rename(NEW)
    config_file("alpha", "port = 8500\n")
    acts.repair(ask=yes)
    got = api.address("alpha", "gateway")
    assert (got.host, got.target, got.port) == (NEW, 8500, 8461)
    assert states() == {"alpha/gateway": "ok"} and len(yes.asked) == 2


def test_missing_and_moved_is_ok_after_one_repair(alpha, tailscale, yes, config_file):
    acts.add("alpha/gateway", ask=yes)
    tailscale.set_serve({})
    config_file("alpha", "port = 8500\n")
    acts.repair(ask=yes)
    assert states() == {"alpha/gateway": "ok"}
    assert api.address("alpha", "gateway").target == 8500


def test_a_half_entry_that_matches_while_its_setting_moved(alpha, tailscale, yes,
                                                           config_file, monkeypatch):
    class Crash(BaseException):
        pass

    def crash(name):
        if name == "add-applied":
            raise Crash

    monkeypatch.setattr(acts, "_step", crash)
    with pytest.raises(Crash):
        acts.add("alpha/gateway", ask=yes)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    config_file("alpha", "port = 8500\n")
    acts.repair(ask=yes)
    got = api.address("alpha", "gateway")
    assert (got.state, got.target, got.port, got.host) == ("on", 8500, 8461, HOST)
    assert states() == {"alpha/gateway": "ok"}


def test_a_half_entry_whose_host_also_changed_is_ok_after_one_repair(alpha, tailscale, yes,
                                                                     monkeypatch):
    class Crash(BaseException):
        pass

    def crash(name):
        if name == "add-applying":
            raise Crash

    monkeypatch.setattr(acts, "_step", crash)
    with pytest.raises(Crash):
        acts.add("alpha/gateway", ask=yes)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    tailscale.rename(NEW)
    yes.asked.clear()
    acts.repair(ask=yes)
    assert api.address("alpha", "gateway").host == NEW
    assert states() == {"alpha/gateway": "ok"}
    assert warn.ct_line(NEW) in yes.asked[0] and events()[-1] == "rename"


def test_a_half_add_over_config_added_since_is_held_not_overwritten(alpha, tailscale, yes,
                                                                    monkeypatch):
    class Crash(BaseException):
        pass

    def crash(name):
        if name == "add-applying":
            raise Crash

    monkeypatch.setattr(acts, "_step", crash)
    with pytest.raises(Crash):
        acts.add("alpha/gateway", ask=yes)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    tailscale.foreign(8461, "http://127.0.0.1:4000")
    live = tailscale.serve
    got = acts.repair(ask=yes)
    assert got.code == 3 and tailscale.serve == live
    entry = record.read().entries["alpha/gateway"]
    assert entry.state == State.APPLYING and "port 8461" in entry.detail


def test_clash_is_left_and_names_both_settings(alpha, tailscale, yes, config_file):
    acts.add("alpha/gateway", ask=yes)
    acts.add("alpha/connector", ask=yes)
    config_file("alpha", "[other]\nport = 8461\n")
    assert states() == {"alpha/gateway": "clash", "alpha/connector": "clash"}
    got = acts.repair(ask=yes)
    assert got.code == 3 and any("alpha other.port" in n for n in got.notes)


def test_an_extra_mount_beside_moxis_handler_reads_changed_and_repair_leaves_it(
        alpha, tailscale, yes):
    acts.add("alpha/connector", ask=yes)
    tailscale.edit(lambda s: s["serve"]["Web"][f"{HOST}:443"]["Handlers"].update(
        {"/x": {"Text": "hi"}}))
    assert states() == {"alpha/connector": "changed"}
    live = tailscale.serve
    assert acts.repair(ask=yes).code == 3 and tailscale.serve == live


def test_nothing_to_repair_asks_nothing(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    yes.asked.clear()
    assert acts.repair(ask=yes).notes == ["nothing to repair"] and yes.asked == []


def test_check_of_one_target(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    acts.add("alpha/connector", ask=yes)
    got = acts.check("alpha", "gateway")
    assert [s.target for s in got] == ["alpha/gateway"]
    assert acts.check("alpha", "nosuch") == []


def test_two_concurrent_adds_of_one_port_one_wins_and_the_other_is_refused(alpha, tailscale):
    import threading

    import moxi.modules

    alpha.module("beta", {"beta.page": "PAGE"},
                 values='PAGE = {"reach": "own-devices", "port_setting": "other.port"}\n')
    moxi.modules.reset()
    planned = threading.Barrier(2, timeout=10)

    def ask(lines):
        locks.none_held()
        planned.wait()  # both have planned, with no lock, before either goes on
        return True

    out: dict[str, object] = {}

    def run(target):
        try:
            out[target] = acts.add(target, port=9500, ask=ask)
        except Refused as exc:
            out[target] = exc

    workers = [threading.Thread(target=run, args=(t,)) for t in ("alpha/gateway", "beta/page")]
    for w in workers:
        w.start()
    for w in workers:
        w.join(30)
    won = [t for t, got in out.items() if isinstance(got, acts.Outcome)]
    lost = [t for t, got in out.items() if isinstance(got, Refused)]
    assert len(won) == 1 and len(lost) == 1
    assert out[won[0]].address.port == 9500
    assert out[lost[0]].exit_code == 3
    assert out[lost[0]].reason == f"port 9500 is held by {won[0]}"
    assert list(record.read().entries) == won
    assert len(tailscale.changes()) == 1
    assert states() == {won[0]: "ok"}
