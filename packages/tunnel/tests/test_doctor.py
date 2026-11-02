"""The doctor's rows against the fake `tailscale`: every row in each state it can take, the
Tailscale rows off while no Tailscale target is recorded, a bad declaration and a record of
another format as `fail` rows, and the deadline rule with short waits."""

from __future__ import annotations

import json
import shutil
import socket
import sys
import time
import types

import pytest

from fake_tailscale import HOST, status
from moxi_tunnel import acts, doctor, record
from moxi_tunnel import tailscale as ts_
from moxi_tunnel.doctor import TAILSCALE_ROWS


def by_name(rows) -> dict:
    return {r.name: r for r in rows}


def states(rows) -> dict[str, str]:
    return {r.name: r.state for r in rows}


def quick(**kw):
    """One run with short waits: a hung read costs 0.2 s, not 2 s."""
    return doctor.rows(**{"budget": 3.0, "read_wait": 0.2, "connect_wait": 0.05, **kw})


@pytest.fixture
def listening():
    """A socket listening on a free loopback port; yields the port."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    yield sock.getsockname()[1]
    sock.close()


def test_with_nothing_recorded_the_tailscale_rows_are_off_and_tailscale_is_not_run(
        alpha, tailscale):
    got = by_name(quick())
    assert [got[n].state for n in TAILSCALE_ROWS] == ["off"] * len(TAILSCALE_ROWS)
    assert got["tailscale CLI"].detail == "no Tailscale target is recorded"
    assert tailscale.calls == []
    assert got["record"].state == "ok" and got["record"].detail == "nothing recorded yet"
    assert got["cloudflare"].state == "off"
    assert got["cloudflare in step"].state == "ok"


def test_a_recorded_tailscale_target_turns_its_rows_on(alpha, tailscale, yes, config_file,
                                                       listening):
    config_file("alpha", f"port = {listening}\n")
    acts.add("alpha/gateway", ask=yes)
    tailscale.edit(lambda s: s.update(calls=[]))
    rows = quick()
    got = by_name(rows)
    assert states(rows) == {
        "record": "ok", "tailscale CLI": "ok", "certificate renewal": "ok",
        "logged in": "ok", "HTTPS certificates": "ok",
        "Funnel allowed": "off", "may change serve config": "ok", "shields up": "off",
        "alpha/gateway": "ok", "alpha/gateway answers": "ok", "cloudflare": "off",
        "cloudflare in step": "ok"}
    assert got["tailscale CLI"].detail.startswith("tailscale 1.104.1 at ")
    assert got["alpha/gateway"].detail == f"https://{HOST}:{listening}/ (tailscale) is " \
                                          "live as recorded"
    # Each read ran once, shared by every row and by the entry's comparison.
    assert sorted(c[0] for c in tailscale.calls) == ["debug", "serve", "status", "version"]


def test_a_target_that_does_not_answer_is_a_warning_naming_its_module(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    row = by_name(quick())["alpha/gateway answers"]
    assert row.state == "warn" and "alpha is not serving it" in row.detail
    assert row.fix == "moxi doctor alpha"


def test_a_public_target_checks_funnel_and_shields(alpha, tailscale, yes):
    acts.add("alpha/connector", ask=yes)
    got = by_name(quick())
    assert (got["Funnel allowed"].state, got["shields up"].state) == ("ok", "ok")
    assert "443, 8443, 10000" in got["Funnel allowed"].detail

    tailscale.edit(lambda s: s["prefs"].update(ShieldsUp=True))
    tailscale.edit(lambda s: s["status"]["Self"].update(CapMap={"https": None}))
    got = by_name(quick())
    assert got["shields up"].state == "fail"
    assert got["shields up"].fix == "tailscale set --shields-up=false"
    assert got["Funnel allowed"].state == "fail"
    assert "funnel node attribute" in got["Funnel allowed"].fix


@pytest.mark.parametrize("version, cli, renewal", [
    ("1.104.1", "ok", "ok"), ("1.102.5", "ok", "ok"), ("1.100.0", "ok", "warn"),
    ("1.50.2", "fail", "warn"), ("v-unknown", "fail", "warn")])
def test_the_version_floor_and_certificate_renewal(alpha, tailscale, yes, version, cli,
                                                   renewal):
    acts.add("alpha/gateway", ask=yes)
    tailscale.edit(lambda s: s.update(version=version))
    got = by_name(quick())
    assert (got["tailscale CLI"].state, got["certificate renewal"].state) == (cli, renewal)


def test_logged_out_and_no_https_are_failures_with_their_fixes(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    tailscale.edit(lambda s: s.update(status=status(
        BackendState="NeedsLogin", CertDomains=[],
        CurrentTailnet={"MagicDNSEnabled": False})))
    got = by_name(quick())
    assert (got["logged in"].state, got["logged in"].fix) == ("fail", "tailscale up")
    assert got["HTTPS certificates"].state == "fail"
    assert "MagicDNS is off" in got["HTTPS certificates"].detail
    assert "admin console" in got["HTTPS certificates"].fix


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux keeps an operator")
def test_another_operator_is_a_failure_and_no_operator_key_skips_the_row(alpha, tailscale,
                                                                          yes):
    acts.add("alpha/gateway", ask=yes)
    tailscale.edit(lambda s: s["prefs"].update(OperatorUser="someone-else"))
    row = by_name(quick())["may change serve config"]
    assert (row.state, row.fix) == ("fail", "sudo tailscale set --operator=$USER")
    tailscale.edit(lambda s: s["prefs"].pop("OperatorUser"))
    assert by_name(quick())["may change serve config"].state == "off"


def test_an_entry_that_changed_by_hand_is_a_failure_naming_the_way_out(alpha, tailscale,
                                                                       yes):
    acts.add("alpha/gateway", ask=yes)
    tailscale.foreign(8461, "http://127.0.0.1:9999")
    row = by_name(quick())["alpha/gateway"]
    assert row.state == "fail" and row.detail.startswith("changed: ")
    assert row.fix == ("tailscale serve --https=8461 off, then moxi tunnel remove "
                       "alpha/gateway --forget")


def test_a_renamed_machine_shows_its_entries_renamed(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    tailscale.rename("laptop.tail1234.ts.net")
    row = by_name(quick())["alpha/gateway"]
    assert (row.state, row.fix) == ("fail", "moxi tunnel repair")
    assert row.detail.startswith("renamed")


def test_a_bad_declaration_is_a_failure(site, tailscale):
    site.module("alpha", {"gateway": "GATEWAY", "alpha.ok": "GATEWAY"},
                values='GATEWAY = {"reach": "own-devices", "port_setting": "port"}\n')
    rows = quick()
    got = by_name(rows)
    row = got["declaration gateway"]
    assert row.state == "fail" and "not <module>.<name>" in row.detail
    assert "moxi-alpha" in row.fix
    assert "declaration alpha.ok" not in got


def test_a_record_of_another_format_is_a_failure(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    raw = json.loads(record.record_path().read_text())
    raw["format"] = 2
    record.record_path().write_text(json.dumps(raw))
    got = by_name(quick())
    assert got["record"].state == "fail" and "format 2" in got["record"].detail
    assert got["record"].fix == "upgrade moxi-tunnel"


def test_a_record_that_is_not_one_is_a_failure_and_the_rest_still_runs(alpha, tailscale):
    record.record_path().parent.mkdir(parents=True, exist_ok=True)
    record.record_path().write_text("not json")
    got = by_name(quick())
    assert got["record"].state == "fail" and "not JSON" in got["record"].detail
    assert got["cloudflare in step"].state in ("ok", "warn", "fail")


def test_an_entry_a_newer_tunnel_wrote_is_a_failure(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    raw = json.loads(record.record_path().read_text())
    raw["entries"]["alpha/gateway"]["form"] = "unix"
    record.record_path().write_text(json.dumps(raw))
    row = by_name(quick())["alpha/gateway"]
    assert row.state == "fail" and record.NEWER in row.detail


def test_no_tailscale_cli_fails_its_row_and_checks_nothing_else(alpha, tailscale, yes,
                                                                config_file, tmp_path):
    acts.add("alpha/gateway", ask=yes)
    config_file("tunnel", f"[tailscale]\nbinary = '{tmp_path / 'gone'}'\n")
    rows = quick()
    got = by_name(rows)
    assert got["tailscale CLI"].state == "fail"
    assert "tailscale.binary" in got["tailscale CLI"].detail
    for name in ("certificate renewal", "logged in", "HTTPS certificates",
                 "may change serve config", "alpha/gateway"):
        assert (got[name].state, got[name].detail) == ("warn", "not checked: no tailscale CLI")


def test_a_read_that_fails_at_once_fails_its_row_and_the_others_go_on(alpha, tailscale,
                                                                       yes):
    acts.add("alpha/gateway", ask=yes)
    tailscale.fail("status", "failed to connect to local tailscaled\n")
    got = by_name(quick())
    assert got["logged in"].state == "fail"
    assert "failed to connect to local tailscaled" in got["logged in"].detail
    assert got["HTTPS certificates"].state == "warn"
    assert got["tailscale CLI"].state == "ok"
    assert got["alpha/gateway"].state == "warn"
    assert ["serve", "status", "--json"] in tailscale.calls


# -- the deadline ---------------------------------------------------------------------------

@pytest.mark.timed
def test_a_hung_tailscale_fails_the_first_read_and_leaves_the_rest_not_checked(
        alpha, tailscale, yes):
    acts.add("alpha/connector", ask=yes)
    tailscale.edit(lambda s: s.update(hang=["version"], calls=[]))
    start = time.monotonic()
    rows = quick()
    took = time.monotonic() - start
    # Only the one read's wait: it fired, and nothing waited after it.
    assert took < 2.0, took
    got = by_name(rows)
    assert got["tailscale CLI"].state == "fail"
    assert got["tailscale CLI"].detail == "tailscale did not answer in 0.2 s"
    assert got["tailscale CLI"].fix == "tailscale status"
    for name in ("certificate renewal", "logged in", "HTTPS certificates", "Funnel allowed",
                 "may change serve config", "shields up", "alpha/connector"):
        assert (got[name].state, got[name].detail) == \
            ("warn", "not checked: tailscale did not answer"), name
    assert tailscale.calls == [["version"]]
    assert got["cloudflare in step"].state == "ok" and got["record"].state == "ok"


def test_a_hung_serve_status_fails_the_first_entry_and_skips_the_prefs(alpha, tailscale,
                                                                       yes):
    acts.add("alpha/gateway", ask=yes)
    acts.add("alpha/connector", ask=yes)
    tailscale.edit(lambda s: s.update(hang=["serve-status"], calls=[]))
    got = by_name(quick())
    assert (got["logged in"].state, got["tailscale CLI"].state) == ("ok", "ok")
    assert got["alpha/connector"].state == "fail"
    assert got["alpha/connector"].detail == "tailscale did not answer in 0.2 s"
    assert got["alpha/gateway"].state == "warn"
    assert got["may change serve config"].detail == "not checked: tailscale did not answer"
    assert not any(c[0] == "debug" for c in tailscale.calls)


def test_reads_the_deadline_has_passed_are_not_checked_and_never_run(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    tailscale.edit(lambda s: s.update(calls=[]))

    def clock():
        # Time runs out once tailscale has answered its first read.
        return 100.0 if tailscale.calls else 0.0

    got = by_name(doctor.rows(clock=clock))
    assert got["tailscale CLI"].state == "ok"
    for name in ("logged in", "HTTPS certificates", "may change serve config",
                 "alpha/gateway"):
        assert (got[name].state, got[name].detail) == \
            ("warn", "not checked: the doctor's 8 s ran out"), name
    assert tailscale.calls == [["version"]]


def test_a_read_cut_short_by_the_deadline_is_not_checked_not_failed(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    tailscale.edit(lambda s: s.update(hang=["version"]))
    got = by_name(doctor.rows(budget=0.2, read_wait=2.0, connect_wait=0.05))
    assert got["tailscale CLI"].state == "warn"
    assert got["tailscale CLI"].detail == "not checked: the doctor's 0.2 s ran out"


def test_connects_after_the_deadline_are_not_checked(alpha, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    row = by_name(doctor.rows(clock=iter([0.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0,
                                          9.0, 9.0]).__next__))["alpha/gateway answers"]
    assert (row.state, row.detail) == ("warn", "not checked: the doctor's 8 s ran out")


@pytest.mark.slow
@pytest.mark.timed
def test_with_the_real_waits_a_hung_tailscale_still_returns_inside_the_cores_ten_seconds(
        alpha, tailscale, yes):
    acts.add("alpha/connector", ask=yes)
    tailscale.edit(lambda s: s.update(hang=["status"]))
    start = time.monotonic()
    got = by_name(doctor.rows())
    assert time.monotonic() - start < 10.0
    assert got["logged in"].detail == "tailscale did not answer in 2 s"


# -- Cloudflare -----------------------------------------------------------------------------

def test_a_recorded_cloudflare_target_turns_its_row_on(alpha, cf, tailscale, yes):
    acts.add("alpha/gateway", provider_name="cloudflare", hostname="a.example.com", ask=yes)
    rows = quick()
    got = by_name(rows)
    assert got["cloudflare"].state == "ok"
    assert got["cloudflare in step"].state == "ok"
    assert got["alpha/gateway"].state == "ok"
    assert [got[n].state for n in TAILSCALE_ROWS] == ["off"] * len(TAILSCALE_ROWS)
    assert tailscale.calls == []


def test_the_cloudflare_row_fails_when_its_setup_is_gone(alpha, cf, tailscale, yes,
                                                         config_file):
    acts.add("alpha/gateway", provider_name="cloudflare", hostname="a.example.com", ask=yes)
    config_file("tunnel", "[cloudflare]\nenabled = true\ntunnel_id = 'not-a-uuid'\n")
    row = by_name(quick())["cloudflare"]
    assert row.state == "fail"


# -- the platforms, as far as Linux can show them ------------------------------------------

@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_off_linux_the_cli_is_found_in_the_platforms_place_and_no_operator_is_asked(
        alpha, tailscale, yes, monkeypatch, tmp_path, platform):
    acts.add("alpha/gateway", ask=yes)
    app = tmp_path / "app" / "Tailscale"
    app.parent.mkdir()
    shutil.copy(tailscale.bin / "tailscale", app)
    tailscale.edit(lambda st: st.update(calls=[]))
    shutil.copy(tailscale.path, app.parent / tailscale.path.name)
    monkeypatch.setenv("PATH", "/nowhere")
    monkeypatch.setitem(ts_.PLATFORM_PATHS, platform, str(app))
    # Only tunnel's reading of the platform changes; Python's own stays Linux's.
    seen = types.SimpleNamespace(platform=platform)
    monkeypatch.setattr(ts_, "sys", seen)
    monkeypatch.setattr(doctor, "sys", seen)
    got = by_name(quick())
    assert got["tailscale CLI"].state == "ok" and str(app) in got["tailscale CLI"].detail
    assert got["may change serve config"].state == "off"
    # An own-devices target there needs no prefs: debug prefs is not run.
    calls = json.loads((app.parent / tailscale.path.name).read_text())["calls"]
    assert ["debug", "prefs"] not in calls


def test_a_unit_state_that_does_not_come_back_leaves_its_row_not_checked(alpha, cf, units,
                                                                        yes, monkeypatch):
    import threading

    acts.add("alpha/gateway", provider_name="cloudflare", hostname="a.example.com",
             ask=yes)
    release = threading.Event()
    real = units.status

    def hung(*args, **kwargs):
        release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(units, "status", hung)
    try:
        got = by_name(quick())
    finally:
        release.set()
    row = got["cloudflare in step"]
    assert row.state == "warn" and row.detail.startswith("not checked")
    assert got["alpha/gateway"].state == "ok"
