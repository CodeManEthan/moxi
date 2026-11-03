"""`TEARDOWN` and `on_uninstall`, called in process under a held core lock, against the fake
`tailscale` and tunnel's own fake units, so no case reaches a real unit manager. Every
Cloudflare case (the switch, the unit, the file) is here; the real `moxi uninstall` runs in
`test_uninstall_build.py`.

The core runs `TEARDOWN` in a child that holds the uninstall's claim on the core lock, then
every `on_uninstall` in its own process, which holds the core lock itself; holding it here
is the same for tunnel, whose take of it yields to the holder. The reinstall cases stand in
for the core's steps around them: its unit removal (the fake unit is taken away), `--purge`
(the stores deleted, the config file kept) and a reinstall's unit apply."""

from __future__ import annotations

import json
import shutil
import threading

import pytest

import moxi.modules
from fake_tailscale import HOST
from moxi import files, paths
from moxi_tunnel import acts, cloudflare, doctor, locks, record, uninstall
from moxi_tunnel.errors import Refused
from moxi_tunnel.record import State

A = "a.example.com"
NEW = "laptop.tail1234.ts.net"


class Crash(BaseException):
    pass


def crash_at(monkeypatch, *names) -> set:
    """Crash at each of the act steps `names`; returns the set, to change or clear."""
    steps = set(names)

    def hit(name):
        if name in steps:
            raise Crash

    monkeypatch.setattr(acts, "_step", hit)
    return steps


def teardown():
    with files.lock(paths.core_lock()):
        uninstall.teardown()


def on_uninstall(module):
    with files.lock(paths.core_lock()):
        uninstall.on_uninstall(module)


def entries() -> dict:
    return dict(record.read().entries)


def live_ports(tailscale) -> list[str]:
    return sorted(tailscale.serve["TCP"])


def events() -> list[dict]:
    path = record.events_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def three(alpha, cf, tailscale, yes, config_file):
    """alpha/gateway on Serve, alpha/connector on Funnel, beta/page on Cloudflare."""
    alpha.module("beta", {"beta.page": "PAGE"},
                 values='PAGE = {"reach": "own-devices", "port_setting": "port"}\n')
    config_file("beta", "port = 8500\n")
    moxi.modules.reset()
    acts.add("alpha/gateway", ask=yes)
    acts.add("alpha/connector", ask=yes)
    acts.add("beta/page", provider_name="cloudflare", hostname=A, ask=yes)
    assert len(entries()) == 3 and cloudflare.switch_on()
    return alpha


def assert_cloudflare_gone(units):
    assert not cloudflare.switch_on()
    assert cloudflare.read_file().text is None
    assert units.unit is None
    assert record.read().cloudflared_started is None


# -- TEARDOWN -------------------------------------------------------------------------------

def test_teardown_removes_every_address_and_turns_cloudflare_off(three, tailscale, units,
                                                                 capsys):
    teardown()
    assert entries() == {}
    assert live_ports(tailscale) == []
    assert_cloudflare_gone(units)
    # The switch went off before the file went.
    assert units.switched[-1][0] is False and units.switched[-1][1] is not None
    out = capsys.readouterr().out
    for key in ("alpha/gateway", "alpha/connector", "beta/page"):
        assert f"tunnel: removed {key}" in out
    assert [(e["event"], e.get("by")) for e in events()][-3:] == [("remove", "teardown")] * 3
    assert all(held for _, held in units.locked)


def test_teardown_with_nothing_recorded_runs_nothing(alpha, tailscale, units, capsys):
    teardown()
    assert tailscale.calls == [] and units.acts() == []
    assert capsys.readouterr().out == ""


def test_teardown_refuses_on_a_changed_entry_and_changes_nothing(three, tailscale, units):
    tailscale.foreign(443, "http://127.0.0.1:9999", funnel=True)
    before = record.record_path().read_text()
    serve = tailscale.serve
    with pytest.raises(moxi.modules.Refused) as caught:
        teardown()
    reason = caught.value.reason
    assert "alpha/connector: port 443 no longer holds what Moxi applied" in reason
    assert "tailscale funnel --https=443 off" in reason
    assert "moxi tunnel remove alpha/connector --forget" in reason
    assert reason.endswith("moxi uninstall tunnel --skip-teardown")
    assert "alpha/gateway" not in reason
    # Nothing changed: the record, the live config, the switch, the file and the unit.
    assert record.record_path().read_text() == before
    assert tailscale.serve == serve
    assert cloudflare.switch_on() and cloudflare.read_file().text is not None
    assert units.unit == "active"


def test_teardown_refuses_on_an_entry_a_newer_tunnel_wrote_pointing_past_it(alpha,
                                                                            tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    raw = json.loads(record.record_path().read_text())
    raw["entries"]["alpha/gateway"]["form"] = "tcp"
    record.record_path().write_text(json.dumps(raw))
    with pytest.raises(moxi.modules.Refused) as caught:
        teardown()
    reason = caught.value.reason
    assert f"alpha/gateway: it was {record.NEWER}" in reason
    assert "--forget" not in reason
    assert "moxi uninstall tunnel --skip-teardown" in reason
    assert live_ports(tailscale) == ["8461"]


def test_teardown_refuses_a_record_of_another_format(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    raw = json.loads(record.record_path().read_text())
    raw["format"] = 2
    record.record_path().write_text(json.dumps(raw))
    with pytest.raises(moxi.modules.Refused) as caught:
        teardown()
    assert "format 2" in caught.value.reason
    assert "moxi uninstall tunnel --skip-teardown" in caught.value.reason
    assert live_ports(tailscale) == ["8461"]


def test_teardown_refuses_when_tailscale_cannot_be_read(three, tailscale, units):
    tailscale.fail("serve-status", "failed to connect to local tailscaled\n")
    with pytest.raises(moxi.modules.Refused) as caught:
        teardown()
    reason = caught.value.reason
    assert "tunnel could not remove 2 addresses" in reason
    assert "failed to connect to local tailscaled" in reason
    assert "tailscale serve --https=8461 off" in reason
    assert "beta/page" not in reason
    assert len(entries()) == 3 and cloudflare.switch_on()


def test_a_removal_that_fails_leaves_its_entry_and_refuses_naming_it(three, tailscale, units):
    tailscale.fail("off", "error: busy\n")
    with pytest.raises(moxi.modules.Refused) as caught:
        teardown()
    reason = caught.value.reason
    assert "tailscale did not remove it: error: busy" in reason
    assert "alpha/gateway" in reason and "alpha/connector" in reason
    left = entries()
    assert set(left) == {"alpha/gateway", "alpha/connector"}
    assert {e.state for e in left.values()} == {State.REMOVING}
    assert all("error: busy" in e.detail for e in left.values())
    # The Cloudflare side went: the record holds no Cloudflare entry.
    assert_cloudflare_gone(units)
    # The doctor still shows both, and repair finishes them once tailscale answers.
    rows = {r.name: r.state for r in doctor.rows(read_wait=1.0)}
    assert rows["alpha/gateway"] == rows["alpha/connector"] == "fail"
    tailscale.ok()
    assert acts.repair(ask=lambda lines: True).code == 0
    assert entries() == {} and live_ports(tailscale) == []


def test_a_failed_unit_act_does_not_stop_the_teardown(three, tailscale, units, capsys):
    units.fail.add("remove")
    teardown()
    assert entries() == {} and live_ports(tailscale) == []
    assert not cloudflare.switch_on() and cloudflare.read_file().text is None
    assert units.unit == "active"   # the uninstall's own unit removal comes next
    assert "cloudflared did not follow" in capsys.readouterr().out


def test_a_held_lock_refuses_naming_its_holder(alpha, tailscale, yes, monkeypatch):
    import os

    acts.add("alpha/gateway", ask=yes)
    monkeypatch.setattr(locks, "WAIT", 0.2)
    taken, done = threading.Event(), threading.Event()

    def hold():
        with locks.tunnel():
            taken.set()
            done.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    taken.wait(5)
    try:
        with pytest.raises(moxi.modules.Refused) as caught:
            teardown()
    finally:
        done.set()
        holder.join()
    assert f"held by process {os.getpid()}" in caught.value.reason
    assert "--skip-teardown" in caught.value.reason
    assert live_ports(tailscale) == ["8461"]


def test_a_replace_that_had_not_finished_is_undone_and_counts_as_removed(alpha, tailscale,
                                                                         yes, monkeypatch,
                                                                         capsys):
    tailscale.foreign(8461, "http://127.0.0.1:3000")
    foreign = tailscale.serve
    steps = crash_at(monkeypatch, "replace-applying")
    with pytest.raises(Crash):
        acts.add("alpha/gateway", replace_=True, ask=yes)
    steps.clear()
    assert record.names_pending(entries()["alpha/gateway"])
    teardown()
    assert entries() == {}
    # Nothing of Moxi's was live; the foreign config stays, and its copy is gone with it.
    assert tailscale.serve == foreign
    assert record.listing() == []
    assert "the replace that had not finished is undone (deleted)" in capsys.readouterr().out


def test_a_teardown_stopped_after_its_first_write_is_finished_by_repair(three, tailscale,
                                                                       units, monkeypatch):
    steps = crash_at(monkeypatch, "uninstall-removing")
    with pytest.raises(Crash):
        teardown()
    steps.clear()
    assert {e.state for e in entries().values()} == {State.REMOVING}
    assert_cloudflare_gone(units)
    assert acts.repair(ask=lambda lines: True).code == 0
    assert entries() == {} and live_ports(tailscale) == []


def test_a_removing_entry_that_names_a_pending_copy_is_undone_by_repair(alpha, tailscale,
                                                                        yes, monkeypatch):
    tailscale.foreign(8461, "http://127.0.0.1:3000")
    foreign = tailscale.serve
    steps = crash_at(monkeypatch, "replace-applying")
    with pytest.raises(Crash):
        acts.add("alpha/gateway", replace_=True, ask=yes)
    steps.clear()
    steps.add("uninstall-removing")
    with pytest.raises(Crash):
        teardown()
    steps.clear()
    entry = entries()["alpha/gateway"]
    assert entry.state == State.REMOVING and record.names_pending(entry)
    acts.repair(ask=lambda lines: True)
    assert entries() == {} and record.listing() == []
    assert tailscale.serve == foreign


def test_an_entry_under_an_old_name_is_dropped_and_its_stale_key_named(alpha, tailscale,
                                                                       yes, capsys):
    acts.add("alpha/gateway", ask=yes)
    tailscale.rename(NEW)
    teardown()
    assert entries() == {}
    out = capsys.readouterr().out
    assert f"tailscale still lists {HOST}:8461 under a name this machine no longer has" in out


def test_the_listeners_are_called_after_the_locks_are_let_go(three, monkeypatch):
    seen = []

    def notify(before, after):
        locks.none_held()
        seen.append((sorted(before.entries), sorted(after.entries)))

    monkeypatch.setattr(uninstall.changes, "notify", notify)
    teardown()
    assert seen == [(["alpha/connector", "alpha/gateway", "beta/page"], [])]


# -- on_uninstall ---------------------------------------------------------------------------

def test_another_modules_uninstall_removes_its_addresses_alone(three, tailscale, units):
    on_uninstall("beta")
    assert set(entries()) == {"alpha/gateway", "alpha/connector"}
    assert_cloudflare_gone(units)
    on_uninstall("alpha")
    assert entries() == {} and live_ports(tailscale) == []


def test_another_modules_uninstall_with_no_addresses_takes_no_lock(alpha, tailscale, yes,
                                                                   monkeypatch, capsys):
    acts.add("alpha/gateway", ask=yes)
    monkeypatch.setattr(locks, "held", None)
    uninstall.on_uninstall("gamma")
    assert capsys.readouterr().out == ""


def test_an_address_it_could_not_remove_stays_as_an_orphan(three, tailscale, units, capsys):
    tailscale.foreign(8461, "http://127.0.0.1:9999")
    on_uninstall("alpha")
    out = capsys.readouterr().out
    assert "tunnel: removed alpha/connector" in out
    assert ("tunnel: alpha/gateway stays in tunnel's record and may stay live: port 8461 "
            "no longer holds what Moxi applied") in out
    assert "moxi tunnel remove alpha/gateway" in out
    assert set(entries()) == {"alpha/gateway", "beta/page"}
    # With alpha gone, the doctor shows it as an orphan.
    shutil.rmtree(three.root / "moxi_alpha-0.1.0.dist-info")
    moxi.modules.reset()
    assert {s.target: s.check for s in acts.check()}["alpha/gateway"] == "orphan"


def test_a_failed_removal_leaves_an_orphan_with_the_providers_text(three, tailscale, units,
                                                                   capsys):
    tailscale.fail("off", "error: busy\n")
    on_uninstall("alpha")
    out = capsys.readouterr().out
    assert out.count("stays in tunnel's record") == 2
    assert {e.state for k, e in entries().items() if k.startswith("alpha/")} == \
        {State.REMOVING}
    shutil.rmtree(three.root / "moxi_alpha-0.1.0.dist-info")
    moxi.modules.reset()
    checks = {s.target: s.check for s in acts.check()}
    assert checks["alpha/gateway"] == checks["alpha/connector"] == "orphan"
    tailscale.ok()
    acts.remove("alpha/gateway", ask=lambda lines: True)
    assert "alpha/gateway" not in entries()


def test_tunnels_own_listener_names_every_address_and_removes_nothing(three, tailscale,
                                                                      units, capsys):
    serve = tailscale.serve
    on_uninstall("tunnel")
    out = capsys.readouterr().out
    assert (f"tunnel: alpha/gateway stays live at https://{HOST}:8461/; remove it by hand "
            "with: tailscale serve --https=8461 off") in out
    assert (f"tunnel: alpha/connector stays live at https://{HOST}/; remove it by hand "
            "with: tailscale funnel --https=443 off") in out
    assert f"tunnel: beta/page (https://{A}/) stays in tunnel's record" in out
    assert tailscale.serve == serve and len(entries()) == 3
    assert units.unit == "active"


def test_tunnels_own_listener_after_a_teardown_that_accepted_says_nothing(three, capsys):
    teardown()
    capsys.readouterr()
    on_uninstall("tunnel")
    assert capsys.readouterr().out == ""


# -- the reinstall claims -------------------------------------------------------------------

def test_after_skip_teardown_a_reinstall_finds_every_address_as_it_was(three, tailscale,
                                                                       units):
    tokens = {k: record.read().token(k) for k in entries()}
    on_uninstall("tunnel")
    # The uninstall removes the unit; the switch and cloudflared.yml stay.
    units.unit, units.started_on = None, None
    assert cloudflare.switch_on() and cloudflare.read_file().text is not None
    # A reinstall: the core applies tunnel's units, and the switch is still on.
    units.apply(units.plan("tunnel", only=["cloudflared"]))
    assert units.unit == "active"
    assert units.started_on == cloudflare.read_file().text
    assert {s.target: s.check for s in acts.check()} == dict.fromkeys(tokens, "ok")
    assert {k: record.read().token(k) for k in entries()} == tokens
    assert cloudflare.agreement()[0] == "ok"


def test_after_purge_the_live_entries_read_as_foreign_and_repair_turns_the_switch_off(
        three, tailscale, units, yes):
    on_uninstall("tunnel")
    units.unit, units.started_on = None, None
    # --purge: every store goes once the user says yes; the config file is kept.
    for path in (record.record_path(), record.events_path(), cloudflare.config_path()):
        path.unlink(missing_ok=True)
    shutil.rmtree(record.displaced_dir(), ignore_errors=True)
    units.apply(units.plan("tunnel", only=["cloudflared"]))
    assert units.unit == "active"   # the switch put the unit back, on no file
    assert entries() == {}
    with pytest.raises(Refused) as caught:
        acts.add("alpha/gateway", port=8461, ask=yes)
    assert "--replace" in f"{caught.value.reason} {caught.value.fix}"
    row = {r.name: r for r in doctor.rows()}["cloudflare in step"]
    assert (row.state, row.fix) == ("warn", "moxi tunnel repair")
    assert acts.repair(ask=lambda lines: True).code == 0
    assert not cloudflare.switch_on() and units.unit is None
    assert cloudflare.agreement()[0] == "ok"


# -- the step with nothing recorded, and a file the step can't write ------------------------

def test_teardown_with_nothing_recorded_turns_a_switch_left_on_off(alpha, cf, units):
    import moxi.config

    moxi.config.set("tunnel", "cloudflare.enabled", True, by="test")
    cloudflare.write_file("# left behind\n")
    assert cloudflare.switch_on()
    teardown()
    assert_cloudflare_gone(units)


def test_another_modules_uninstall_keeps_its_cloudflare_entry_while_the_file_cant_be_written(
        three, tailscale, units, yes, capsys):
    acts.add("alpha/gateway", provider_name="cloudflare", hostname="g.example.com", ask=yes)
    cloudflare.config_path().chmod(0o644)
    capsys.readouterr()
    on_uninstall("alpha")
    out = capsys.readouterr().out
    assert "tunnel: alpha/gateway stays in tunnel's record" in out
    assert "removed alpha/gateway" not in out
    assert "tunnel: removed alpha/connector" in out
    assert entries()["alpha/gateway"].state == State.ON
    assert "g.example.com" in cloudflare.read_file().rules()
    assert "alpha/connector" not in entries()
    assert ("rollback", "alpha/gateway") in [(e["event"], e.get("target")) for e in events()]
    cloudflare.config_path().chmod(0o600)
    assert acts.remove("alpha/gateway", ask=yes).code == 0
