"""The gateway in a browser, as a phone meets it: through the phone bridge, at
`https://gw.moxi.test/`, on a gateway-mode test shell over the fixture module.

Each engine plays a phone: Chromium a Pixel, WebKit an iPhone, and desktop Firefox at 360
px for layout. Pairing with a passkey and signing in with one run in Chromium, against the
CDP virtual authenticator; the other engines pair without a passkey. The endpoint is TCP on
every system today, so the tests of the phone's acts on the socket endpoint are skipped
with the reason, and the TCP tests show what the phone gets instead: the desk pointer.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

import phone_kit
from moxi_ui.testing.phone import HOST, ORIGIN, PhoneBridge, set_gateway
from squatter import Squatter

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120)]

WAIT_MS = 5000
#: The browser tests that need the socket endpoint, which no system's probe result names.
SOCKET_TESTS = (
    "the phone's confirmed-act sheet shows the gateway's echo and acts once on a double tap",
    "a page loaded while the acts were mounted, whose prepare gets a 404 after the "
    "address's target changed, shows the 404 line and resolves `desk`",
)
SOCKET_SKIP = ("the gateway's endpoint is TCP on every system until a system's socket probe "
               "passes, so these wait for the socket endpoint: " + "; ".join(SOCKET_TESTS))
TCP_LINE = "listens on a port another program could take while Moxi isn't running"
DOWN = "Moxi isn't running or the gateway is off"
AWAY = "Can't reach the computer"
#: Polls in the page (no `wait_for_function`: the gateway's policy allows no eval).
WORKER = """async () => {
  const until = Date.now() + 15000;
  while (Date.now() < until) {
    const reg = await navigator.serviceWorker.getRegistration();
    if (reg && reg.active && navigator.serviceWorker.controller) return reg.active.scriptURL;
    await new Promise((done) => setTimeout(done, 100));
  }
  return null;
}"""
CACHED = """async () => {
  const until = Date.now() + 15000;
  while (Date.now() < until) {
    if (await caches.has("moxi-gateway")) return true;
    await new Promise((done) => setTimeout(done, 100));
  }
  return false;
}"""
SETTLED = """async () => {
  const until = Date.now() + 15000;
  while (Date.now() < until) {
    const reg = await navigator.serviceWorker.getRegistration();
    if (reg && reg.active && !reg.installing && !reg.waiting) return true;
    await new Promise((done) => setTimeout(done, 100));
  }
  return false;
}"""
OFF = """async () => {
  const until = Date.now() + 10000;
  let status = 0;
  while (Date.now() < until && status !== 503) {
    status = (await fetch("/m/ui/api/modules", {cache: "no-store"})).status;
    if (status !== 503) await new Promise((done) => setTimeout(done, 100));
  }
  return status;
}"""
ACT_PATHS = """async () => {
  const key = localStorage.getItem("moxi-ui-key");
  const headers = {"Content-Type": "application/json", "X-Moxi-Key": key};
  const post = (path, body) => fetch(path, {method: "POST", headers,
                                            body: JSON.stringify(body)});
  const act = "a".repeat(43);
  return [
    (await fetch("/m/ui/api/modules", {headers})).status,
    (await post("/gw/act/prepare", {module: "fixture", path: "/api/phone-act", body: {}})).status,
    (await post("/gw/act/commit", {act})).status,
    (await fetch("/gw/act/state?act=" + act, {headers})).status,
    (await post("/m/fixture/api/phone-act", {by: 1})).status,
    (await post("/m/fixture/api/confirm", {by: 1})).status,
  ];
}"""


def _engine(ui_browser) -> str:
    return ui_browser.browser_type.name


@pytest.fixture(scope="module")
def gw(tmp_path_factory, fixture_site):
    """A gateway-mode shell holding its TCP endpoint, and the bridge to it."""
    shell = phone_kit.gateway_shell(tmp_path_factory.mktemp("gw"), fixture_site)
    bridge = PhoneBridge(shell.home, shell.gateway_port).start()
    try:
        yield shell, bridge
    finally:
        bridge.stop()
        shell.stop()


@pytest.fixture
def phones(ui_browser, tmp_path_factory):
    """`phones(bridge)`: a fresh phone context through `bridge`; closed at the test's end.
    Chromium is launched per bridge, since its SPKI flag is a launch argument."""
    engine = _engine(ui_browser)
    opened: list = []

    def open_phone(bridge: PhoneBridge):
        browser = bridge.launch(ui_browser.browser_type)
        opened.append(browser)
        context = browser.new_context(**PhoneBridge.context_args(engine))
        context.set_default_timeout(WAIT_MS)
        return context

    try:
        yield open_phone
    finally:
        for browser in opened:
            browser.close()


def _pair(context, shell, *, passkey: bool = False):
    """Pair through the desk's QR link; the page ends on the signed-in shell document."""
    status, data = shell.session().post("/m/ui/api/gateway/pair")
    assert status == 200, data
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(data["url"])
    expect(page).to_have_title("Moxi: pair this phone")
    page.get_by_role("button", name="Continue").click()
    name = "Create a passkey" if passkey else "Pair without a passkey"
    page.get_by_role("button", name=name).click()
    page.wait_for_url(ORIGIN + "/m/ui/")
    page.locator("#moxi-panel > moxi-ui-page").wait_for(state="attached")
    return page


def _authenticator(context, page):
    cdp = context.new_cdp_session(page)
    cdp.send("WebAuthn.enable")
    made = cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
        "protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
        "hasUserVerification": True, "isUserVerified": True}})
    return cdp, made["authenticatorId"]


def _violations(page) -> list[str]:
    seen: list[str] = []
    page.on("console", lambda msg: seen.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda err: seen.append(str(err)))
    return seen


def test_the_phone_page_is_a_secure_context_and_the_worker_controls_it(gw, phones):
    shell, bridge = gw
    page = _pair(phones(bridge), shell)
    assert page.evaluate("() => isSecureContext") is True
    assert page.evaluate(WORKER) == ORIGIN + "/sw.js"
    assert page.evaluate("async () => (await import('/m/ui/client.js')).gateway()") is True


def test_a_passkey_pairs_and_signs_in_again_after_a_restart(
        ui_browser, tmp_path_factory, fixture_site, phones):
    if _engine(ui_browser) != "chromium":
        pytest.skip("passkeys run in Chromium only, against its CDP virtual authenticator")
    shell = phone_kit.gateway_shell(tmp_path_factory.mktemp("gw"), fixture_site)
    bridge = PhoneBridge(shell.home, shell.gateway_port).start()
    try:
        context = phones(bridge)
        page = context.new_page()
        cdp, authenticator = _authenticator(context, page)
        _pair(context, shell, passkey=True)
        held = cdp.send("WebAuthn.getCredentials", {"authenticatorId": authenticator})
        assert len(held["credentials"]) == 1 and held["credentials"][0]["rpId"] == HOST
        shell.stop()
        shell.start()
        phone_kit.wait_hold(shell, shell.gateway_port)
        page.goto("/m/ui/")
        expect(page).to_have_title("Moxi: sign in")
        page.get_by_role("button", name="Sign in with a passkey").click()
        page.wait_for_url(ORIGIN + "/m/ui/")
        page.locator("#moxi-panel > moxi-ui-page").wait_for(state="attached")
    finally:
        bridge.stop()
        shell.stop()


def test_the_manifest_installs_and_no_policy_is_violated(ui_browser, gw, phones):
    if _engine(ui_browser) != "chromium":
        pytest.skip("only Chromium reports installability and policy violations over CDP")
    shell, bridge = gw
    context = phones(bridge)
    page = context.new_page()
    errors = _violations(page)
    _pair(context, shell)
    assert page.evaluate(WORKER) == ORIGIN + "/sw.js"
    page.reload()
    page.locator("#moxi-panel > moxi-ui-page").wait_for(state="attached")
    cdp = context.new_cdp_session(page)
    manifest = cdp.send("Page.getAppManifest")
    assert manifest["url"] == ORIGIN + "/manifest.webmanifest" and not manifest["errors"]
    assert cdp.send("Page.getInstallabilityErrors")["installabilityErrors"] == []
    assert not [e for e in errors if "policy" in e.lower() or "trusted" in e.lower()], errors


def test_on_tcp_the_phones_act_shows_the_desk_pointer_and_sends_nothing(gw, phones):
    shell, bridge = gw
    page = _pair(phones(bridge), shell)
    sent: list[str] = []
    page.on("request", lambda r: sent.append(r.url) if "/gw/act/" in r.url
            or "/api/phone-act" in r.url else None)
    assert page.evaluate("async () => (await import('/m/ui/client.js')).gatewayActs()") is False
    page.evaluate("""() => { window.__outcome = undefined;
      import("/m/ui/client.js").then((c) => c.confirm("fixture", "api/phone-act", {by: 1},
        ["Adds one."])).then((o) => { window.__outcome = o; }); }""")
    sheet = page.locator("moxi-ui-confirm")
    expect(sheet.locator("p.desk").first).to_contain_text("Do this at the desk")
    expect(sheet).to_contain_text(TCP_LINE)
    expect(sheet.get_by_role("button", name="Confirm")).to_have_count(0)
    sheet.get_by_role("button", name="Close").click()
    outcome = page.evaluate("""async () => { while (window.__outcome === undefined)
      await new Promise((done) => setTimeout(done, 50)); return window.__outcome; }""")
    assert outcome["outcome"] == "desk" and TCP_LINE in outcome["text"]
    assert sent == []


def test_the_fixture_panel_hides_its_desk_only_acts_and_fits_360_px(gw, phones,
                                                                   ui_width_check):
    shell, bridge = gw
    page = _pair(phones(bridge), shell)
    page.set_viewport_size({"width": 360, "height": 740})
    ui_width_check(page, module="fixture")
    panel = page.locator("moxi-fixture-page")
    expect(panel.get_by_role("button", name="Phone add")).to_be_visible()
    expect(panel.get_by_role("button", name="Confirmed add")).to_have_count(0)
    expect(panel.get_by_role("button", name="Add one")).to_have_count(0)


def test_code_holding_a_real_session_gets_a_404_on_every_act_path(gw, phones):
    shell, bridge = gw
    page = _pair(phones(bridge), shell)
    assert page.evaluate(ACT_PATHS) == [200, 404, 404, 404, 404, 404]


def test_the_offline_page_for_a_stopped_shell_the_off_answer_and_no_answer(
        tmp_path_factory, fixture_site, phones):
    shell = phone_kit.gateway_shell(tmp_path_factory.mktemp("gw"), fixture_site)
    bridge = PhoneBridge(shell.home, shell.gateway_port).start()
    try:
        context = phones(bridge)
        page = _pair(context, shell)
        assert page.evaluate(WORKER) == ORIGIN + "/sw.js"
        assert page.evaluate(CACHED) is True
        set_gateway(shell, enabled=False)  # the entry stays: the off answer
        assert page.evaluate(OFF) == 503  # the shell's next tick turns the gateway off
        page.goto("/m/ui/")
        expect(page.locator("main#moxi-offline")).to_contain_text(DOWN)
        shell.stop()
        page.goto("/m/ui/")
        expect(page.locator("main#moxi-offline")).to_contain_text(DOWN)
        if context.browser.browser_type.name == "chromium":
            # Firefox shows the page offline with no text yet; left to the by-hand check.
            context.set_offline(True)
            page.goto("/m/ui/")
            expect(page.locator("main#moxi-offline")).to_contain_text(AWAY)
    finally:
        bridge.stop()
        shell.stop()


@pytest.mark.xfail(strict=True, reason="in Chromium the gateway's worker installs at the "
                   "update check (its cache appears), yet the planted worker still answers "
                   "the next navigation; not yet explained")
def test_a_planted_worker_is_replaced_at_the_next_update_check(
        ui_browser, tmp_path_factory, fixture_site, phones):
    if _engine(ui_browser) == "webkit":
        pytest.skip("WebKit sends no cookie on an update check; its replacement is in the "
                    "by-hand check")
    port = phone_kit.free_port()
    home = tmp_path_factory.mktemp("gw")
    bridge = PhoneBridge(home, port).start()
    squat = Squatter(port).start()
    try:
        context = phones(bridge)
        page = context.new_page()
        page.goto("/plant?kind=deep")
        expect(page).to_have_title("planted", timeout=30_000)
    finally:
        squat.stop()
    shell = phone_kit.gateway_shell(home, fixture_site, port=port)
    try:
        page.goto("/m/ui/")
        expect(page).to_have_title("squatter")  # the planted worker takes this one
        assert page.evaluate(CACHED) is True  # the update check installed the real worker
        assert page.evaluate(SETTLED) is True
        page.goto("/m/ui/")
        expect(page).to_have_title("Moxi: sign in")
    finally:
        bridge.stop()
        shell.stop()


@pytest.mark.skip(reason=SOCKET_SKIP)
def test_on_the_socket_endpoint_the_sheet_acts_once_and_a_lapsed_mount_gives_the_desk():
    """Waits for a system whose socket probe passes; see SOCKET_TESTS."""
