"""A switch onto Cloudflare that stopped half way: until it settles, the old Tailscale entry
its `from` names counts as a Tailscale entry for every reader that removes, counts or
reports those.

Each test starts from `alpha/connector`, public on a Funnel on 443, whose switch onto
Cloudflare crashed after its first commit: the record holds a Cloudflare entry, and the
Funnel is still live.
"""

from __future__ import annotations

import json

import pytest

import moxi.modules
from moxi import files, paths
from moxi_tunnel import acts, doctor, ports, record, uninstall
from moxi_tunnel.errors import ApplyError, Refused
from moxi_tunnel.record import State

A = "a.example.com"
KEY = "alpha/connector"
HAND = "tailscale funnel --https=443 off"


class Crash(BaseException):
    pass


def _crash_at(name):
    def crash(step):
        if step == name:
            raise Crash
    return crash


@pytest.fixture
def half(alpha, cf, tailscale, units, yes, monkeypatch):
    acts.add(KEY, ask=yes)
    monkeypatch.setattr(acts, "_step", _crash_at("switch-applying"))
    with pytest.raises(Crash):
        acts.add(KEY, provider_name="cloudflare", hostname=A, ask=yes)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    entry = record.read().entries[KEY]
    assert entry.provider == "cloudflare" and entry.state == State.APPLYING
    assert entry.from_["provider"] == "tailscale" and entry.from_["reach"] == "public"
    assert "443" in tailscale.serve["TCP"]
    return alpha


def funnel_live(tailscale) -> bool:
    return "443" in tailscale.serve["TCP"] or bool(tailscale.serve["AllowFunnel"])


def under_core(fn, *args):
    with files.lock(paths.core_lock()):
        return fn(*args)


def test_the_old_entry_reads_back_from_from(half):
    old = record.old_tailscale(record.read().entries[KEY])
    assert (old.provider, old.port, old.reach) == ("tailscale", 443, "public")
    assert record.old_tailscale(old) is None



@pytest.mark.parametrize("damage", [{"host": 5}, {"port": 0}, {"port": "443"}])
def test_a_from_naming_tailscale_that_cant_be_read_is_refused_not_hidden(half, tailscale,
                                                                          yes, damage):
    path = record.record_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["entries"][KEY]["from"].update(damage)
    path.write_text(json.dumps(raw), encoding="utf-8")
    entry = record.read().entries[KEY]
    assert not entry.known
    with pytest.raises(Refused, match="newer tunnel"):
        acts.remove(KEY, ask=yes)
    assert record.read().entries[KEY].from_ == raw["entries"][KEY]["from"]
    assert funnel_live(tailscale)

# -- uninstall ------------------------------------------------------------------------------

def test_teardown_removes_the_old_funnel_too(half, tailscale, capsys):
    under_core(uninstall.teardown)
    assert record.read().entries == {}
    assert not funnel_live(tailscale)
    out = capsys.readouterr().out
    assert f"tunnel: removed {KEY} (https://{A}/, and its old Tailscale entry" in out


def test_teardown_refuses_naming_an_old_funnel_that_changed(half, tailscale):
    tailscale.foreign(443, "http://127.0.0.1:9999", funnel=True)
    before = record.record_path().read_text()
    with pytest.raises(moxi.modules.Refused) as caught:
        under_core(uninstall.teardown)
    reason = caught.value.reason
    assert f"{KEY}: its switch from Tailscale has not finished" in reason
    assert HAND in reason and f"moxi tunnel remove {KEY} --forget" in reason
    assert record.record_path().read_text() == before


def test_teardown_refuses_while_tailscale_cant_be_read(half, tailscale):
    tailscale.fail("serve-status", "tailscaled is not running\n")
    with pytest.raises(moxi.modules.Refused) as caught:
        under_core(uninstall.teardown)
    assert "tailscale can't be read" in caught.value.reason
    assert HAND in caught.value.reason
    tailscale.ok()
    assert funnel_live(tailscale) and KEY in record.read().entries


def test_a_teardown_whose_old_funnel_removal_fails_is_finished_by_repair(half, tailscale,
                                                                          yes):
    tailscale.fail("off", "tailscaled: busy\n")
    with pytest.raises(moxi.modules.Refused) as caught:
        under_core(uninstall.teardown)
    assert HAND in caught.value.reason
    entry = record.read().entries[KEY]
    assert entry.state == State.REMOVING and entry.from_ is not None
    tailscale.ok()
    assert acts.repair(ask=yes).code == 0
    assert record.read().entries == {}
    assert not funnel_live(tailscale)


def test_another_modules_uninstall_removes_the_old_funnel_too(half, tailscale, capsys):
    under_core(uninstall.on_uninstall, "alpha")
    assert record.read().entries == {}
    assert not funnel_live(tailscale)


def test_tunnels_own_listener_names_the_old_funnel_with_its_hand_command(half, capsys):
    under_core(uninstall.on_uninstall, "tunnel")
    out = capsys.readouterr().out
    assert f"tunnel: {KEY}'s switch from Tailscale had not finished" in out
    assert HAND in out


# -- remove and forget ----------------------------------------------------------------------

def test_remove_takes_the_old_funnel_out_too(half, tailscale, yes):
    got = acts.remove(KEY, ask=yes)
    assert got.code == 0
    assert "and its old Tailscale entry" in got.notes[0]
    assert any("old Tailscale entry" in line for line in yes.asked[-1])
    assert record.read().entries == {}
    assert not funnel_live(tailscale)


def test_remove_refuses_while_the_old_funnel_changed_or_cant_be_read(half, tailscale, yes):
    tailscale.fail("serve-status", "tailscaled is not running\n")
    with pytest.raises(Refused, match="can.t be read") as caught:
        acts.remove(KEY, ask=yes)
    assert HAND in caught.value.fix
    tailscale.ok()
    tailscale.foreign(443, "http://127.0.0.1:9999", funnel=True)
    with pytest.raises(Refused, match="no longer matches") as caught:
        acts.remove(KEY, ask=yes)
    assert HAND in caught.value.fix
    assert record.read().entries[KEY].state == State.APPLYING


def test_a_remove_whose_old_funnel_removal_does_nothing_goes_back_whole(half, tailscale,
                                                                        yes):
    tailscale.fail("off", "tailscaled: refused\n")
    with pytest.raises(ApplyError, match="refused"):
        acts.remove(KEY, ask=yes)
    entry = record.read().entries[KEY]
    assert entry.provider == "cloudflare" and entry.from_ is not None
    assert funnel_live(tailscale)
    tailscale.ok()
    assert acts.remove(KEY, ask=yes).code == 0
    assert not funnel_live(tailscale)


def test_forget_names_the_old_funnel_with_its_hand_command(half, tailscale, yes):
    got = acts.remove(KEY, forget=True, ask=yes)
    assert any(HAND in line for line in yes.asked[-1])
    assert any(HAND in note for note in got.notes)
    assert record.read().entries == {}
    assert funnel_live(tailscale)


# -- the port bookkeeping and the doctor ----------------------------------------------------

def test_the_old_funnels_port_is_held_not_foreign(half, tailscale, yes):
    assert ports.moxi_ports(record.read()) == {443: KEY}
    with pytest.raises(Refused, match=f"port 443 is held by {KEY}"):
        acts.add("alpha/gateway", port=443, replace_=True, ask=yes)
    assert funnel_live(tailscale)


def test_the_doctor_checks_funnel_and_shields_for_the_old_funnel(half, tailscale):
    tailscale.edit(lambda s: s["prefs"].update(ShieldsUp=True))
    got = {r.name: r for r in doctor.rows(budget=3.0, read_wait=0.5, connect_wait=0.05)}
    assert got["Funnel allowed"].state == "ok"
    assert got["shields up"].state == "fail"
    assert got[KEY].state == "fail" and got[KEY].fix == "moxi tunnel repair"
