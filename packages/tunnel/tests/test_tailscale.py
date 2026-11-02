"""The Tailscale provider against the fake CLI: the reads, the host, the match rule, the
prerequisite checks and the commands it runs."""

from __future__ import annotations

import pytest

from fake_tailscale import FUNNEL_CAP, HOST, status
from moxi_tunnel import tailscale as ts
from moxi_tunnel.errors import ApplyError, Refused
from moxi_tunnel.record import Entry, State


def entry(**kw) -> Entry:
    base = dict(reach="own-devices", provider="tailscale", host=HOST, port=8461,
                target=8461, state=State.ON)
    return Entry(**{**base, **kw})


def serve_of(port=8461, target=8461, host=HOST, *, funnel=False, extra=None) -> dict:
    out = {"TCP": {str(port): {"HTTPS": True}},
           "Web": {f"{host}:{port}": {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{target}"},
                                                   **(extra or {})}}}}
    if funnel:
        out["AllowFunnel"] = {f"{host}:{port}": True}
    return out


def test_the_host_is_the_dns_name_lower_case_with_no_trailing_dot(tailscale):
    tailscale.rename("Box.Tail1234.TS.net")
    assert ts.Tailscale().host() == "box.tail1234.ts.net"


def test_a_match_needs_the_whole_handler_set_to_be_moxis():
    assert ts.matches(serve_of(), entry())
    assert ts.matches(serve_of(host="BOX.tail1234.ts.net."), entry())
    assert not ts.matches(serve_of(extra={"/admin": {"Proxy": "http://127.0.0.1:1"}}),
                          entry())
    assert not ts.matches(serve_of(target=9999), entry())
    assert not ts.matches(serve_of(funnel=True), entry())
    assert ts.matches(serve_of(funnel=True), entry(reach="public"))
    assert not ts.matches(serve_of(), entry(reach="public"))
    assert not ts.matches(serve_of(), entry(form="tcp"))
    no_https = serve_of()
    no_https["TCP"]["8461"] = {"TCPForward": "127.0.0.1:8461"}
    assert not ts.matches(no_https, entry())


def test_live_states():
    assert ts.live_state(serve_of(), entry(), HOST) is None
    assert ts.live_state({}, entry(), HOST) == "missing"
    assert ts.live_state(serve_of(target=1), entry(), HOST) == "changed"
    assert ts.live_state({}, entry(), "other.tail1234.ts.net") == "renamed"


def test_a_copy_holds_every_part_of_the_port_and_how_to_remove_and_restore_it():
    serve = serve_of(port=443, target=3000, funnel=True,
                     extra={"/docs": {"Path": "/srv/docs"}})
    copy = ts.copy_of(serve, 443, HOST)
    assert copy["parts"] == ts.parts(serve, 443)
    assert copy["remove"] == "tailscale funnel --https=443 off"
    assert copy["form"] == "funnel"
    assert "tailscale funnel --bg --https=443 http://127.0.0.1:3000" in copy["restore"]
    assert "tailscale funnel --bg --https=443 --set-path=/docs /srv/docs" in copy["restore"]


def test_ready_when_everything_is_on(tailscale):
    assert ts.Tailscale().ready("own-devices") == []
    assert ts.Tailscale().ready("public") == []


def test_a_missing_cli_is_the_first_and_only_problem(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(ts, "PLATFORM_PATHS", {})
    problems = ts.Tailscale().ready("public")
    assert len(problems) == 1 and "not installed" in problems[0].reason
    assert "tailscale.binary" in problems[0].fix


def test_the_binary_setting_wins_over_path(tailscale, config_file, tmp_path):
    config_file("tunnel", f"[tailscale]\nbinary = '{tailscale.bin / 'tailscale'}'\n")
    assert ts.find_binary() == str(tailscale.bin / "tailscale")
    config_file("tunnel", f"[tailscale]\nbinary = '{tmp_path / 'nope'}'\n")
    assert ts.find_binary() is None
    assert "tailscale.binary setting" in ts.Tailscale().ready("own-devices")[0].reason


@pytest.mark.parametrize("change, reach, fix", [
    (lambda s: s["status"].update(BackendState="NeedsLogin"), "own-devices", "tailscale up"),
    (lambda s: s["status"]["CurrentTailnet"].update(MagicDNSEnabled=False), "own-devices",
     "MagicDNS"),
    (lambda s: s["status"].update(CertDomains=[]), "own-devices", "HTTPS certificates"),
    (lambda s: s["status"]["Self"]["CapMap"].pop("https"), "own-devices",
     "HTTPS certificates"),
    (lambda s: s["prefs"].update(OperatorUser=""), "own-devices", "--operator=$USER"),
    (lambda s: s["status"]["Self"]["CapMap"].pop("funnel"), "public", "funnel node attribute"),
    (lambda s: s["status"]["Self"]["CapMap"].pop(FUNNEL_CAP), "public",
     "funnel node attribute"),
    (lambda s: s["prefs"].update(ShieldsUp=True), "public", "--shields-up=false"),
])
def test_each_missing_prerequisite_names_its_fix(tailscale, change, reach, fix, monkeypatch):
    monkeypatch.setattr(ts.sys, "platform", "linux")
    monkeypatch.setattr(ts, "_is_root", lambda: False)
    tailscale.edit(change)
    problems = ts.Tailscale().ready(reach)
    assert len(problems) == 1 and fix in problems[0].fix, problems


def test_the_operator_check_is_skipped_when_prefs_lack_the_key(tailscale, monkeypatch):
    monkeypatch.setattr(ts.sys, "platform", "linux")
    tailscale.edit(lambda s: s["prefs"].pop("OperatorUser"))
    assert ts.Tailscale().ready("own-devices") == []


def test_own_devices_needs_no_funnel(tailscale):
    tailscale.edit(lambda s: s["status"]["Self"]["CapMap"].pop("funnel"))
    assert ts.Tailscale().ready("own-devices") == []


def test_the_funnel_ports_grant(tailscale):
    assert ts.Tailscale().funnel_ports() == (443, 8443, 10000)
    tailscale.edit(lambda s: s["status"]["Self"].update(CapMap={
        "https": None, "funnel": None,
        "https://tailscale.com/cap/funnel-ports?ports=8443": None}))
    assert ts.Tailscale().funnel_ports() == (8443,)


def test_apply_and_remove_run_serve_and_funnel_with_bg_and_yes(tailscale):
    t = ts.Tailscale()
    t.apply(entry())
    t.apply(entry(reach="public", port=443, target=9000))
    assert tailscale.changes() == [
        ["serve", "--bg", "--yes", "--https=8461", "http://127.0.0.1:8461"],
        ["funnel", "--bg", "--yes", "--https=443", "http://127.0.0.1:9000"]]
    assert ts.matches(t.serve_config(), entry())
    assert ts.matches(t.serve_config(), entry(reach="public", port=443, target=9000))
    t.remove(entry(reach="public", port=443, target=9000))
    assert tailscale.changes()[-1] == ["funnel", "--https=443", "off"]
    assert set(t.live()) == {8461}


def test_remove_refuses_config_that_no_longer_matches(tailscale):
    tailscale.set_serve(serve_of(target=1))
    with pytest.raises(Refused):
        ts.Tailscale().remove(entry())
    assert tailscale.changes() == []


def test_a_failed_command_raises_its_stderr_unchanged(tailscale):
    tailscale.fail("apply", "error: serve config denied\n")
    with pytest.raises(ApplyError) as caught:
        ts.Tailscale().apply(entry())
    assert caught.value.reason == "error: serve config denied\n"
    assert caught.value.exit_code == 1


def test_reads_go_by_the_deadline(tailscale):
    import time

    from moxi_tunnel.errors import ReadError

    with pytest.raises(ReadError) as caught:
        ts.Tailscale(deadline=time.monotonic() - 1).status()
    assert caught.value.timeout


def test_status_reads_are_made_once(tailscale):
    t = ts.Tailscale()
    t.status(), t.status(), t.host(), t.serve_config(), t.serve_config()
    assert [c[0] for c in tailscale.calls] == ["status", "serve"]
    assert status()["Self"]["DNSName"].endswith(".")
