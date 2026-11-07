"""The test kit's gateway mode and the phone bridge, with no browser: a gateway-mode shell
takes its hold where `moxi ui gateway pair` and `on` and the tunnel's `upstream()` find
it, the usual test environment takes none, and a phone paired over the bridge reaches the
fixture module's gateway routes and nothing else of it."""

from __future__ import annotations

import json
import socket
import subprocess
import time

import pytest

import phone_kit
from moxi_ui.testing.phone import HOST, PhoneBridge
from moxi_ui.testing.shell import shell_env

pytestmark = pytest.mark.timeout(120)


def _python(shell, code: str) -> str:
    """Run `code` in a process with the shell's environment; its stdout."""
    from moxi.paths import python

    done = subprocess.run([python(), "-P", "-c", code], env=shell.env, capture_output=True,
                          text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _supervisor(shell) -> str:
    return _python(shell, "from moxi.units import backend; print(backend())")


def test_the_mode_sets_no_locator_and_keeps_every_root_under_the_home(tmp_path):
    env = shell_env(tmp_path, gateway=True)
    assert not [key for key in env if key.startswith("MOXI_")]
    assert env["PYTEST_CURRENT_TEST"]
    assert env["PATH"].split(__import__("os").pathsep)[0] == str(tmp_path / "bin")
    assert "MOXI_HOME" in shell_env(tmp_path)
    for name in ("MOXI_UI_HOME", "MOXI_TUNNEL_STATE_HOME", "MOXI_UI_RUNTIME_DIR"):
        with pytest.raises(ValueError, match="locator"):
            shell_env(tmp_path, extra={name: "x"}, gateway=True)
        assert shell_env(tmp_path, extra={name: "x"})[name] == "x"
    for name in ("MOXI_HOME", "PYTEST_CURRENT_TEST"):
        with pytest.raises(ValueError, match="can't set"):
            shell_env(tmp_path, extra={name: "x"}, gateway=True)


def test_in_gateway_mode_the_shell_takes_its_hold_where_pair_and_upstream_look(
        tmp_path_factory, fixture_site):
    shell = phone_kit.gateway_shell(tmp_path_factory.mktemp("gw"), fixture_site)
    try:
        port = shell.gateway_port
        hold = json.loads((shell.runtime_dir("ui") / "gateway.hold").read_text())
        assert hold == {"pid": shell.info.pid, "port": port}
        # Every root is under the shell's home, and no module has a locator variable.
        assert shell.runtime_dir("ui").is_relative_to(shell.home)
        assert shell.state_dir("tunnel").is_relative_to(shell.home)
        done = shell.moxi("ui", "gateway", "pair")
        assert done.returncode == 0, done.stdout + done.stderr
        assert f"https://{HOST}/pair?n=" in done.stdout
        said = _python(shell, "from moxi_ui.tunnel import upstream; print(upstream())")
        assert said == f"http://127.0.0.1:{port}"
        # The stand-ins come first: no real provider runs under the shell.
        found = _python(shell, "import shutil; print(shutil.which('cloudflared'))")
        assert found.startswith(str(shell.home / "bin"))
    finally:
        shell.stop()


def test_on_in_gateway_mode_passes_and_writes_no_unit(tmp_path_factory, fixture_site):
    shell = phone_kit.gateway_shell(tmp_path_factory.mktemp("gw"), fixture_site, enabled=False)
    try:
        found = _supervisor(shell)
        if found != "None":
            pytest.skip(f"the core finds {found} under the kit's environment, so `on`'s "
                        "`moxi config set` would apply the shell's unit there; the kit "
                        "refuses that call, so `on` isn't run on this system")
        done = shell.moxi("ui", "gateway", "on")
        assert done.returncode == 0, done.stdout + done.stderr
        assert f"the gateway is on at https://{HOST}" in done.stdout
        assert "no supervisor on this system" in done.stdout
        units = [path for path in shell.home.rglob("*")
                 if path.suffix in (".service", ".socket", ".plist")]
        assert units == []
        done = shell.moxi("ui", "gateway", "off")
        assert done.returncode == 0, done.stdout + done.stderr
        assert "moxi tunnel remove ui/gateway" in done.stdout
    finally:
        shell.stop()


def test_a_port_another_program_takes_before_the_shell_binds_it_gets_another(
        tmp_path_factory, fixture_site, monkeypatch):
    """Another program binds the port the kit picked before the shell does: the shell
    removes its tunnel entry, so the kit starts it again on another port, where it holds
    with its address and a phone can pair."""
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        first = [taken.getsockname()[1]]
        pick = phone_kit.free_port
        monkeypatch.setattr(phone_kit, "free_port", lambda: first.pop() if first else pick())
        shell = phone_kit.gateway_shell(tmp_path_factory.mktemp("gw"), fixture_site)
        try:
            assert shell.gateway_port != taken.getsockname()[1]
            status, data = shell.session().post("/m/ui/api/gateway/pair")
            assert status == 200, data
        finally:
            shell.stop()


def test_the_usual_test_environment_takes_no_hold(tmp_path_factory, fixture_site):
    home = tmp_path_factory.mktemp("usual")
    shell = phone_kit.gateway_shell(home, fixture_site, start=False)
    shell.env = shell_env(home, [fixture_site])  # TestShell's usual environment
    shell.start()
    try:
        time.sleep(3)  # past the gateway's first tick
        assert phone_kit.hold_port(shell) is None
        with __import__("socket").socket() as free:
            free.bind(("127.0.0.1", shell.gateway_port))  # the port stays free
        # Its config lives under its own MOXI_HOME, where the gateway's switch is off.
        assert not any("takes no hold" in line for line in shell.stderr())
    finally:
        shell.stop()


@pytest.fixture(scope="module")
def phone(tmp_path_factory, fixture_site):
    shell = phone_kit.gateway_shell(tmp_path_factory.mktemp("gw"), fixture_site)
    bridge = PhoneBridge(shell.home, shell.gateway_port).start()
    try:
        yield shell, bridge.pair(shell)
    finally:
        bridge.stop()
        shell.stop()


def test_the_phone_reaches_the_fixtures_gateway_read_with_its_marks_changed(phone):
    shell, session = phone
    status, data = session.get("/m/fixture/api/phone")
    assert status == 200
    assert data["secret"] is None and data["rows"][0]["token"] is None
    assert data["file"].startswith("@h/") and data["file"].endswith("/phone.txt")
    assert data["rows"][0]["where"].startswith("@h/") and data["rows"][0]["name"] == "one"
    status, desk = shell.session().get("/m/fixture/api/phone")
    assert status == 200 and desk["secret"] == "the fixture's secret"
    assert desk["count"] == data["count"]


def test_the_phone_reaches_nothing_else_of_the_fixture(phone):
    _, session = phone
    for path in ("/m/fixture/api/summary", "/m/fixture/api/events",
                 "/m/fixture/api/job?job=j1"):
        assert session.get(path)[0] == 404, path
    for path in ("/m/fixture/api/add", "/m/fixture/api/confirm", "/m/fixture/api/phone-act"):
        assert session.post(path, {"by": 1})[0] == 404, path
    assert session.get("/m/fixture/api/phone-job?job=p1") == (200, {"job": "p1",
                                                                    "state": "unknown"})
    # The endpoint is TCP, so the phone's acts aren't mounted: every act path is a 404.
    for path, body in (("/gw/act/prepare", {"module": "fixture", "path": "/api/phone-act",
                                            "body": {}}),
                       ("/gw/act/commit", {"act": "a" * 43})):
        assert session.post(path, body)[0] == 404, path
    assert session.get("/gw/act/state?act=" + "a" * 43)[0] == 404


def test_the_document_says_the_acts_are_off_on_tcp(phone):
    _, session = phone
    status, headers, body = session.request("GET", "/", headers={
        "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"})
    assert status == 200, body
    assert b'data-gateway-acts="off"' in body and b'data-gateway-reason="tcp"' in body


def test_a_desk_and_a_phone_stream_share_one_upstream_and_only_the_phones_is_redacted(phone):
    shell, session = phone
    streams = shell.state_dir("fixture") / "streams"
    path = "/m/ui/api/push?topics=fixture.phone"
    with shell.session().stream(path) as desk, session.stream(path) as mine:
        assert desk.status == 200 and mine.status == 200
        _, seen = desk.until(lambda name, data: name == "fixture.phone")
        _, got = mine.until(lambda name, data: name == "fixture.phone")
        assert seen["secret"] == "the fixture's secret" and seen["file"].endswith("phone.txt")
        assert got["secret"] is None and got["file"].startswith("@h/")
        assert len(streams.read_text().splitlines()) == 1
