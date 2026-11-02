"""The Cloudflare step against tunnel's own fake `moxi.units`: after each path that changes
the Cloudflare set, the file, the switch, the record and the unit agree. Also the
Cloudflare side of add, remove, forget, repair and the provider switches, the failed unit
acts, crashes inside the step, hand edits, and a file tunnel can't write."""

from __future__ import annotations

import json

import pytest

from fake_tailscale import HOST
from moxi_tunnel import acts, api, cloudflare, confirm as warn, locks, record
from moxi_tunnel.errors import ApplyError, Refused, UsageError
from moxi_tunnel.record import Entry, State

A = "a.example.com"
B = "b.example.com"


class Crash(BaseException):
    """A crash: nothing in tunnel catches it."""


def events() -> list[str]:
    path = record.events_path()
    if not path.exists():
        return []
    return [json.loads(line)["event"] for line in path.read_text().splitlines()]


def states() -> dict[str, str]:
    return {s.target: s.check for s in acts.check()}


def add(target, host, yes, **kw):
    return acts.add(target, provider_name="cloudflare", hostname=host, ask=yes, **kw)


def assert_agree(units):
    """The step's rule, every clause."""
    rec = record.read()
    on_disk = cloudflare.read_file()
    on = cloudflare.switch_on()
    if rec.cloudflare_set():
        assert on_disk.text == cloudflare.render_record(rec)
        assert on and units.unit == "active"
        assert units.started_on == on_disk.text
        assert rec.cloudflared_started == on_disk.digest
    else:
        assert on_disk.text is None and not on and units.unit is None
        assert rec.cloudflared_started is None
    assert cloudflare.agreement()[0] == "ok"


@pytest.fixture
def setup(alpha, cf, units):
    return units


# -- the paths that change the set ---------------------------------------------------------

def test_the_first_add_writes_the_file_before_the_switch_turns_the_unit_on(setup, yes):
    got = add("alpha/gateway", A, yes)
    assert (got.address.url, got.address.exposure, got.address.provider) == \
        (f"https://{A}/", "internet", "cloudflare")
    assert setup.switched[0][0] is True and f'"{A}"' in setup.switched[0][1]
    assert "restart" not in setup.acts()  # the install started it on this text
    assert all(held for _, held in setup.locked)
    assert states() == {"alpha/gateway": "ok"}
    assert events() == ["add"]
    assert_agree(setup)


def test_the_first_add_while_the_switch_is_on_and_the_unit_idle_restarts_it(setup, yes):
    from moxi import config

    config.set("tunnel", "cloudflare.enabled", True, by="test")
    assert setup.unit == "active" and setup.started_on is None  # the file wasn't there
    setup.unit = "inactive"  # it exited idle
    add("alpha/gateway", A, yes)
    assert setup.acts()[-1] == "restart"
    assert_agree(setup)


def test_a_change_while_it_runs_restarts_it_on_the_new_text(setup, yes):
    add("alpha/gateway", A, yes)
    starts = setup.starts
    add("alpha/connector", B, yes)
    assert setup.starts == starts + 1 and setup.acts()[-1] == "restart"
    assert set(cloudflare.read_file().rules()) == {A, B}
    assert_agree(setup)


def test_the_switch_on_and_the_unit_file_deleted_installs_it(setup, yes):
    add("alpha/gateway", A, yes)
    setup.unit = None
    assert cloudflare.agreement()[0] == "warn"
    assert acts.repair(ask=yes).code == 0
    assert "apply" in setup.acts() and setup.acts()[-1] != "restart"
    assert_agree(setup)


def test_the_last_removal_turns_the_switch_off_before_the_file_goes(setup, yes):
    add("alpha/gateway", A, yes)
    add("alpha/connector", B, yes)
    assert acts.remove("alpha/gateway", ask=yes).code == 0
    assert list(cloudflare.read_file().rules()) == [B]
    assert_agree(setup)
    acts.remove("alpha/connector", ask=yes)
    off, text = setup.switched[-1]
    assert off is False and text is not None  # the file was still there
    assert events() == ["add", "add", "remove", "remove"]
    assert_agree(setup)


def test_forget_takes_the_rule_out_and_the_last_one_turns_the_switch_off(setup, yes):
    add("alpha/gateway", A, yes)
    add("alpha/connector", B, yes)
    got = acts.remove("alpha/gateway", ask=yes, forget=True)
    assert got.code == 0 and "left cloudflared.yml" in got.notes[0]
    assert list(cloudflare.read_file().rules()) == [B]
    assert_agree(setup)
    acts.remove("alpha/connector", ask=yes, forget=True)
    assert events()[-1] == "forget"
    assert_agree(setup)


def test_a_commit_that_changes_the_set_without_the_core_lock_is_refused(setup):
    entry = Entry(reach="own-devices", provider="cloudflare", host=A, port=443,
                  target=8461, state=State.ON)
    with locks.tunnel(), pytest.raises(Refused, match="core lock"):
        record.commit({"alpha/gateway": entry})
    with pytest.raises(RuntimeError):
        cloudflare.step()
    assert cloudflare.read_file().text is None and setup.calls == []


# -- the add's checks and lines ------------------------------------------------------------

def test_an_own_devices_add_over_cloudflare_warns_it_is_on_the_internet(setup, yes):
    add("alpha/gateway", A, yes)
    lines = yes.asked[0]
    assert warn.cloudflare_line(A) in lines and warn.OWN_DEVICES_LINE in lines
    assert warn.internet_line(f"https://{A}/", "alpha") in lines
    assert not any("Certificate Transparency" in line for line in lines)


def test_an_add_is_a_no_op_once_on_and_its_options_are_checked(setup, yes):
    add("alpha/gateway", A, yes)
    calls = len(setup.calls)
    assert "already on" in add("alpha/gateway", A, yes).notes[0]
    assert len(yes.asked) == 1 and len(setup.calls) - calls < 5
    with pytest.raises(Refused, match="already on"):
        add("alpha/gateway", B, yes)
    with pytest.raises(Refused, match="already alpha/gateway's"):
        add("alpha/connector", "A.example.com.", yes)
    with pytest.raises(UsageError, match="--hostname"):
        acts.add("alpha/connector", provider_name="cloudflare", ask=yes)
    with pytest.raises(Refused):
        add("alpha/connector", "not a host", yes)


def test_no_supervisor_is_refused_at_ready_and_its_remove_completes_with_exit_1(setup, yes):
    setup.supervisor = None
    with pytest.raises(Refused, match="no supervisor"):
        add("alpha/gateway", A, yes)
    assert record.read().entries == {} and setup.acts() == []
    setup.supervisor = "systemd"
    add("alpha/gateway", A, yes)
    setup.supervisor = None
    got = acts.remove("alpha/gateway", ask=yes)
    assert got.code == 1 and "did not follow" in got.notes[-1]
    assert record.read().entries == {} and cloudflare.read_file().text is None
    assert not cloudflare.switch_on()


def test_an_editable_install_and_missing_settings_are_refused_at_ready(alpha, units, yes,
                                                                      config_file):
    units.editable = True
    with pytest.raises(Refused) as caught:
        add("alpha/gateway", A, yes)
    assert "editable" in caught.value.reason and "tunnel_id" in caught.value.reason
    assert caught.value.fix == "moxi install tunnel"
    assert record.read().entries == {}


# -- failed unit acts ----------------------------------------------------------------------

def test_a_failed_unit_act_on_an_add_rolls_back(setup, yes):
    setup.fail.add("install")
    with pytest.raises(ApplyError, match="Job failed"):
        add("alpha/gateway", A, yes)
    assert record.read().entries == {}
    assert events() == ["rollback"]
    assert cloudflare.read_file().text is None and not cloudflare.switch_on()
    setup.fail.clear()
    assert_agree(setup)


def test_a_failed_unit_removal_with_an_empty_set_completes_and_the_row_warns(setup, yes):
    add("alpha/gateway", A, yes)
    setup.fail.add("remove")
    got = acts.remove("alpha/gateway", ask=yes)
    assert got.code == 1 and "remove failed" in got.notes[-1]
    assert record.read().entries == {} and cloudflare.read_file().text is None
    assert not cloudflare.switch_on() and setup.unit == "active"
    row = cloudflare.in_step_row()
    assert (row.state, row.fix) == ("warn", "moxi tunnel repair")
    assert "still installed" in row.detail
    setup.fail.clear()
    assert acts.repair(ask=yes).code == 0
    assert_agree(setup)


def test_a_repair_of_a_half_add_whose_start_fails_leaves_it_applying_then_finishes(
        setup, yes, monkeypatch):
    monkeypatch.setattr(acts, "_step", _crash_at("cloudflare-applying"))
    with pytest.raises(Crash):
        add("alpha/gateway", A, yes)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    assert states() == {"alpha/gateway": "half"}
    setup.unit = "failed"
    setup.fail.add("restart")
    got = acts.repair(ask=yes)
    entry = record.read().entries["alpha/gateway"]
    assert got.code == 1 and entry.state == State.APPLYING
    assert "start failed" in entry.detail
    setup.fail.clear()
    assert acts.repair(ask=yes).code == 0
    assert record.read().entries["alpha/gateway"].state == State.ON
    assert states() == {"alpha/gateway": "ok"}
    assert "overwrite" not in events()
    assert_agree(setup)


def _crash_at(name):
    def crash(step):
        if step == name:
            raise Crash
    return crash


@pytest.mark.parametrize("call", ["switch", "restart", "commit"])
def test_a_crash_inside_the_step_is_finished_by_repair_with_no_overwrite(
        setup, yes, monkeypatch, call):
    """A crash at the switch (the first add), at the restart (a second add while the unit
    runs), or at the record's started digest."""
    if call != "switch":
        add("alpha/gateway", A, yes)
    if call == "commit":
        real = record.commit

        def commit(entries=None, **kw):
            if entries is None and kw.get("cloudflared_started"):
                raise Crash
            return real(entries, **kw)
        monkeypatch.setattr(record, "commit", commit)
    else:
        setup.crash[call] = Crash()
    with pytest.raises(Crash):
        add("alpha/connector", B, yes)
    if call == "commit":
        monkeypatch.setattr(record, "commit", real)
    assert states()["alpha/connector"] == "half"
    assert cloudflare.agreement()[0] == "warn"
    assert acts.repair(ask=yes).code == 0
    assert states()["alpha/connector"] == "ok"
    assert "overwrite" not in events()
    assert_agree(setup)


# -- hand edits and a file tunnel can't write ----------------------------------------------

def test_a_hand_edited_file_reads_changed_and_repair_overwrites_it_logged(setup, yes):
    add("alpha/gateway", A, yes)
    path = cloudflare.config_path()
    path.write_text(path.read_text().replace("8461", "9999"))
    assert states() == {"alpha/gateway": "changed"}
    assert cloudflare.agreement() == ("fail", "cloudflared.yml was edited by hand")
    assert acts.repair(ask=yes).code == 0
    assert "overwrite" in events()
    line = [json.loads(x) for x in record.events_path().read_text().splitlines()
            if '"overwrite"' in x][0]
    assert "9999" in line["replaced"]
    assert states() == {"alpha/gateway": "ok"}
    assert_agree(setup)


def test_a_file_left_behind_by_an_earlier_record_reads_missing_with_no_overwrite(setup, yes):
    add("alpha/gateway", A, yes)
    old = cloudflare.read_file().text
    add("alpha/connector", B, yes)
    cloudflare.write_file(old)  # what a crash before the file write would leave
    assert states() == {"alpha/gateway": "ok", "alpha/connector": "missing"}
    assert acts.repair(ask=yes).code == 0
    assert "overwrite" not in events()
    assert_agree(setup)


def test_a_file_tunnel_cant_write_rolls_the_remove_back_with_the_entry_kept(setup, yes):
    add("alpha/gateway", A, yes)
    add("alpha/connector", B, yes)
    cloudflare.config_path().chmod(0o644)
    with pytest.raises(ApplyError) as caught:
        acts.remove("alpha/gateway", ask=yes)
    assert str(cloudflare.config_path()) in caught.value.reason
    assert record.read().entries["alpha/gateway"].state == State.ON
    assert events()[-1] == "rollback"
    cloudflare.config_path().chmod(0o600)
    assert acts.remove("alpha/gateway", ask=yes).code == 0
    assert_agree(setup)


# -- moved and repair ----------------------------------------------------------------------

def test_a_moved_target_is_repaired_on_the_same_hostname(setup, yes, config_file):
    add("alpha/gateway", A, yes)
    config_file("alpha", "port = 8470\n")
    assert states() == {"alpha/gateway": "moved"}
    assert acts.repair(ask=yes).code == 0
    entry = record.read().entries["alpha/gateway"]
    assert (entry.host, entry.target, entry.state) == (A, 8470, State.ON)
    assert cloudflare.read_file().rules() == {A: "http://127.0.0.1:8470"}
    assert_agree(setup)


def test_repair_turns_a_switch_left_on_with_no_target_off(setup, yes):
    from moxi import config

    config.set("tunnel", "cloudflare.enabled", True, by="test")
    assert cloudflare.in_step_row().state == "warn"
    assert acts.repair(ask=yes).code == 0
    assert_agree(setup)


# -- provider switches ---------------------------------------------------------------------

def test_a_switch_from_tailscale_to_cloudflare(setup, tailscale, yes):
    first = acts.add("alpha/gateway", ask=yes).address
    got = add("alpha/gateway", A, yes).address
    assert (got.provider, got.host, got.port) == ("cloudflare", A, 443)
    assert got.change != first.change
    assert tailscale.serve["TCP"] == {}
    assert warn.SWITCH_LINE in yes.asked[-1]
    assert record.read().entries["alpha/gateway"].from_ is None
    assert events()[-1] == "switch"
    assert states() == {"alpha/gateway": "ok"}
    assert_agree(setup)


def test_a_switch_from_cloudflare_to_tailscale(setup, tailscale, yes):
    add("alpha/gateway", A, yes)
    got = acts.add("alpha/gateway", provider_name="tailscale", ask=yes).address
    assert (got.provider, got.host, got.port) == ("tailscale", HOST, 8461)
    assert any("Certificate Transparency" in line for line in yes.asked[-1])
    assert states() == {"alpha/gateway": "ok"}
    assert_agree(setup)


def test_a_switch_to_cloudflare_whose_unit_fails_goes_back_whole(setup, tailscale, yes):
    before = acts.add("alpha/gateway", ask=yes).address
    serve = tailscale.serve
    setup.fail.add("install")
    with pytest.raises(ApplyError):
        add("alpha/gateway", A, yes)
    entry = record.read().entries["alpha/gateway"]
    assert (entry.provider, entry.host, entry.state, entry.from_) == \
        ("tailscale", HOST, State.ON, None)
    assert api.address("alpha", "gateway").change != before.change
    assert tailscale.serve == serve
    setup.fail.clear()
    assert_agree(setup)


def test_a_switch_to_tailscale_refused_by_tailscale_goes_back_whole(setup, tailscale, yes):
    add("alpha/gateway", A, yes)
    tailscale.fail("apply")
    with pytest.raises(ApplyError):
        acts.add("alpha/gateway", provider_name="tailscale", ask=yes)
    entry = record.read().entries["alpha/gateway"]
    assert (entry.provider, entry.host, entry.state) == ("cloudflare", A, State.ON)
    assert cloudflare.read_file().rules() == {A: "http://127.0.0.1:8461"}
    assert events()[-1] == "rollback"
    assert_agree(setup)


@pytest.mark.parametrize("direction", ["to-cloudflare", "to-tailscale"])
def test_a_crash_mid_switch_is_finished_by_repair_from_from(setup, tailscale, yes,
                                                            monkeypatch, direction):
    if direction == "to-cloudflare":
        acts.add("alpha/gateway", ask=yes)
        do = lambda: add("alpha/gateway", A, yes)  # noqa: E731
    else:
        add("alpha/gateway", A, yes)
        do = lambda: acts.add("alpha/gateway", provider_name="tailscale", ask=yes)  # noqa: E731
    monkeypatch.setattr(acts, "_step", _crash_at("switch-applying"))
    with pytest.raises(Crash):
        do()
    monkeypatch.setattr(acts, "_step", lambda name: None)
    assert record.read().entries["alpha/gateway"].from_ is not None
    assert states() == {"alpha/gateway": "half"}
    assert acts.repair(ask=yes).code == 0
    entry = record.read().entries["alpha/gateway"]
    assert entry.from_ is None and entry.state == State.ON
    assert states() == {"alpha/gateway": "ok"}
    if direction == "to-cloudflare":
        assert tailscale.serve["TCP"] == {}
    assert_agree(setup)


def test_listeners_hear_a_switch_once(setup, tailscale, yes, alpha):
    alpha.write("moxi_alpha/listen.py", """
        HEARD = []
        def on_change(target, old, new):
            HEARD.append((target, old and old.provider, new and new.provider))
        """)
    info = [p for p in alpha.root.iterdir() if p.name.endswith(".dist-info")][0]
    with open(info / "entry_points.txt", "a") as out:
        out.write("\n[moxi.tunnel.changes]\nalpha = moxi_alpha.listen:on_change\n")
    import moxi.modules

    moxi.modules.reset()
    acts.add("alpha/gateway", ask=yes)
    add("alpha/gateway", A, yes)
    import importlib

    listen = importlib.import_module("moxi_alpha.listen")  # the site's fixture module
    assert listen.HEARD == [("alpha/gateway", None, "tailscale"),
                            ("alpha/gateway", "tailscale", "cloudflare")]
