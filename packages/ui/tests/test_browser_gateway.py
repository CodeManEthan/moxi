"""The gateway in a browser, as a phone meets it: through the phone bridge, at
`https://gw.moxi.test/`, on a gateway-mode test shell over the fixture module.

Each engine plays a phone: Chromium a Pixel, WebKit an iPhone, and desktop Firefox at 360
px for layout. Pairing with a passkey and signing in with one run in Chromium, against the
CDP virtual authenticator; the other engines pair without a passkey. The endpoint is TCP on
every system today, so the tests of the phone's acts on the socket endpoint are skipped
with the reason, and the TCP tests show what the phone gets instead: the desk pointer.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

import phone_kit
from moxi_ui.checks import CHECKS_FILE
from moxi_ui.loopback.api import response_headers
from moxi_ui.testing.phone import HOST, ORIGIN, PhoneBridge, set_gateway
from moxi_ui.testing.shell import TestShell
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
#: Waits until the gateway's worker answers this page: only it answers `/offline` with the
#: page made from its own bytes, whose root says the case it saw. It asks once, then waits
#: for the claim (`controllerchange`) and asks again; it allows more than the other polls,
#: since it waits on the update check, the install and the claim.
#:
#: It sends nothing through the planted worker while the swap is pending. In Chromium, a
#: fetch that lands while the planted worker is being stopped for the swap starts it again,
#: and the swap then waits until that worker has had no event for 30 s, which a page
#: fetching every 100 ms never allows.
OWN_OFFLINE = """async () => {
  const claimed = new Promise((done) => navigator.serviceWorker.addEventListener(
    "controllerchange", () => done(true), {once: true}));
  const own = async () => {
    try {
      return (await (await fetch("/offline")).text()).includes("data-reach=");
    } catch {
      return false;
    }
  };
  if (await own()) return true;
  const late = new Promise((done) => setTimeout(() => done(false), 30000));
  return (await Promise.race([claimed, late])) && await own();
}"""
#: Takes the real worker and its cache off the phone, as a phone that never had them.
UNREGISTER = """async () => {
  for (const name of await caches.keys()) await caches.delete(name);
  const reg = await navigator.serviceWorker.getRegistration();
  return reg ? await reg.unregister() : false;
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
#: Registers the gateway's worker at its own URL, as the shell's client.js does on each
#: load, and waits until it controls this page with no other version left.
OWN_WORKER = """async () => {
  const own = location.origin + "/sw.js";
  await navigator.serviceWorker.register("/sw.js", {scope: "/"});
  const until = Date.now() + 15000;
  while (Date.now() < until) {
    const reg = await navigator.serviceWorker.getRegistration();
    const controller = navigator.serviceWorker.controller;
    if (reg && reg.active && reg.active.scriptURL === own && !reg.installing && !reg.waiting
        && controller && controller.scriptURL === own) return true;
    await new Promise((done) => setTimeout(done, 100));
  }
  return false;
}"""
#: Registers the gateway's worker from another script URL (the gateway answers every
#: worker script request with the same bytes), through a Trusted Types policy of the
#: test's own, and waits until that version controls this page with no other left.
WORKER_AT = """async (url) => {
  const own = location.origin + url;
  const types = globalThis.trustedTypes;
  const script = types === undefined ? url
    : types.createPolicy("test-worker", {createScriptURL: (given) => given}).createScriptURL(url);
  await navigator.serviceWorker.register(script, {scope: "/"});
  const until = Date.now() + 15000;
  while (Date.now() < until) {
    const reg = await navigator.serviceWorker.getRegistration();
    const controller = navigator.serviceWorker.controller;
    if (reg && reg.active && reg.active.scriptURL === own && !reg.installing && !reg.waiting
        && controller && controller.scriptURL === own) return true;
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


#: A year, the longest a squatter's answer could ask to be cached.
YEAR = "Cache-Control: max-age=31536000"
PLANTED_START = ('<!doctype html><title>planted start</title>'
                 '<script type="module" src="/m/ui/client.js"></script>')
PLANTED_CLIENT = ('window.__planted = true; document.title = "planted client";'
                  'export const gateway = () => "planted";')


class CachingSquatter(Squatter):
    """The squatter, also serving a start document and a `client.js` of its own, each
    cached for a year, at the shell's URLs."""

    def _answer(self, target: str, headers: dict) -> tuple[str, str, bytes, list[str]]:
        page = urlsplit(target).path
        if headers.get("service-worker") != "script":
            if page == "/m/ui/":
                return "200 OK", "text/html; charset=utf-8", PLANTED_START.encode(), [YEAR]
            if page == "/m/ui/client.js":
                return "200 OK", "text/javascript", PLANTED_CLIENT.encode(), [YEAR]
        return super()._answer(target, headers)


#: A squatter's page that writes its own offline page, offline script and `client.js` into
#: Cache Storage, under the worker's cache name and another. Each would set the title and
#: `window.__planted`, and the page would read the phone's stored key.
STORE_IN_CACHES = """<!doctype html><title>storing</title><script>
const page = '<!doctype html><title>planted offline</title><main id="moxi-offline"></main>'
  + '<script>window.__planted = true; document.title = "planted offline";<' + '/script>';
const script = 'window.__planted = true; document.title = "planted script";';
const html = {"Content-Type": "text/html"};
const js = {"Content-Type": "text/javascript"};
(async () => {
  for (const name of ["moxi-gateway", "planted"]) {
    const cache = await caches.open(name);
    await cache.put("/offline", new Response(page, {headers: html}));
    await cache.put("/offline.js", new Response(script, {headers: js}));
    await cache.put("/m/ui/client.js", new Response(script, {headers: js}));
  }
  document.title = "stored in caches";
})();
</script>"""
NOT_PLANTED = "() => window.__planted === undefined && !document.title.startsWith('planted')"
#: What `client.js` gets now: the network's status, 0 when nothing answers.
CLIENT_STATUS = """async () => {
  try {
    return (await fetch("/m/ui/client.js")).status;
  } catch {
    return 0;
  }
}"""
#: The headers the worker sends with the offline page's script.
OWN_HEADERS = """async () => Object.fromEntries((await fetch("/offline.js")).headers)"""
STILL_PLANTED = """async () => {
  for (const name of ["moxi-gateway", "planted"]) {
    for (const path of ["/offline", "/offline.js", "/m/ui/client.js"]) {
      const kept = await caches.match(path, {cacheName: name});
      if (!kept || !(await kept.text()).includes("planted")) return false;
    }
  }
  return true;
}"""


class StoringSquatter(Squatter):
    """The squatter, serving at the shell's URL a page that writes into Cache Storage."""

    def _answer(self, target: str, headers: dict) -> tuple[str, str, bytes, list[str]]:
        if headers.get("service-worker") != "script" and urlsplit(target).path == "/m/ui/":
            return "200 OK", "text/html; charset=utf-8", STORE_IN_CACHES.encode(), []
        return super()._answer(target, headers)


#: The longest the relay holds an answer.
HOLD_S = 10


class HoldingRelay:
    """A relay between the bridge and the gateway that copies bytes both ways. After
    `hold(path, until, delay)`, the answer to the next GET of `path` waits until a GET of
    `until` has gone on and `delay` seconds more, or `HOLD_S` pass."""

    def __init__(self, target: int):
        self.target = target
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(64)
        self.port = self._listener.getsockname()[1]
        self._open: list[socket.socket] = [self._listener]
        self._lock = threading.Lock()
        self._path = self._until = b""
        self._asked = threading.Event()
        self._delay = 0.0

    def start(self) -> HoldingRelay:
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self) -> None:
        with self._lock:
            held, self._open = self._open, []
        for sock in held:
            _shut(sock)

    def hold(self, path: str, until: str, delay: float) -> None:
        with self._lock:
            self._path, self._until = f"GET {path} ".encode(), f"GET {until} ".encode()
            self._delay = delay
            self._asked.clear()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._listener.accept()
                upstream = socket.create_connection(("127.0.0.1", self.target))
            except OSError:
                return
            with self._lock:
                self._open += [client, upstream]
            held = threading.Event()
            threading.Thread(target=self._ask, args=(client, upstream, held), daemon=True).start()
            threading.Thread(target=self._answer, args=(upstream, client, held),
                             daemon=True).start()

    def _ask(self, client: socket.socket, upstream: socket.socket,
             held: threading.Event) -> None:
        try:
            while data := client.recv(65536):
                with self._lock:
                    if self._path and data.startswith(self._path):
                        self._path = b""
                        held.set()
                    if self._until and data.startswith(self._until):
                        self._until = b""
                        self._asked.set()
                upstream.sendall(data)
        except OSError:
            pass
        finally:
            _shut(client)
            _shut(upstream)

    def _answer(self, upstream: socket.socket, client: socket.socket,
                held: threading.Event) -> None:
        try:
            while data := upstream.recv(65536):
                if held.is_set():
                    held.clear()
                    if self._asked.wait(HOLD_S):
                        time.sleep(self._delay)  # the swap's moment, not a wait for a result
                client.sendall(data)
        except OSError:
            pass
        finally:
            _shut(upstream)
            _shut(client)


def _shut(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


def _engine(ui_browser) -> str:
    return ui_browser.browser_type.name


#: The doctor run the gateway shell kept: one failing check, one warning, one that
#: passes. Their details and fixes hold what the phone must not get.
KEPT_ROWS = [
    {"module": "ui", "name": "shell", "state": "ok", "detail": "The shell answers", "fix": None},
    {"module": "projects", "name": "roots", "state": "warn",
     "detail": "The project proj-a points at /srv/proj-a, which doesn't exist",
     "fix": "moxi projects set proj-a --root DIR"},
    {"module": "turns", "name": "guard", "state": "fail",
     "detail": "A guard could not check a call", "fix": "moxi config set turns guard.strict false"},
]


@pytest.fixture(scope="module")
def gw(tmp_path_factory, fixture_site):
    """A gateway-mode shell holding its TCP endpoint, and the bridge to it. It kept a
    doctor run (`KEPT_ROWS`) and runs none."""
    home = tmp_path_factory.mktemp("gw")
    state = TestShell(home, gateway=True).state_dir("ui")
    state.mkdir(parents=True, exist_ok=True)
    (state / CHECKS_FILE).write_text(json.dumps({
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": KEPT_ROWS, "error": None}))
    shell = phone_kit.gateway_shell(home, fixture_site)
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


def test_on_the_phone_home_is_the_rows_with_no_spot_and_no_act_under_the_reads_only_line(
        gw, phones, ui_width_check):
    shell, bridge = gw
    page = _pair(phones(bridge), shell)
    page.set_viewport_size({"width": 360, "height": 740})
    home = page.locator("#moxi-panel > moxi-ui-page moxi-ui-home")
    row = home.get_by_role("button", name=re.compile("Health"))
    # The counts alone: the worst check's sentence can hold a path.
    expect(row.locator(".fact")).to_have_text("1 check failing, 1 warning")
    expect(row.locator(".time")).to_have_text(re.compile(r"^checked \d\d?:\d\d"))
    # No spot and no sentence: the rows start under the shell's line, which says the
    # phone only reads and sits first under the top line.
    expect(home.locator(".spot")).to_have_count(0)
    expect(home.get_by_text("Nothing needs you.")).to_have_count(0)
    reads = page.locator("#moxi-reads")
    expect(reads).to_have_text("This phone reads only here.")
    expect(home).not_to_contain_text("This phone reads only")
    top, line = page.locator(".moxi-top").bounding_box(), reads.bounding_box()
    rows = home.locator(".rows").bounding_box()
    assert top["y"] + top["height"] <= line["y"] and line["y"] + line["height"] <= rows["y"] < 100
    assert line["x"] + 18 == rows["x"] + 2, "the line starts where the rows' labels do"
    expect(page.locator("#moxi-settings")).to_be_hidden()
    row.click()
    health = home.locator("moxi-ui-health")
    checks = health.locator(".check")
    expect(checks).to_have_text([re.compile(r"^\s*turns · guard\s*failing\s*$"),
                                 re.compile(r"^\s*projects · roots\s*warning\s*$")])
    for row_ in KEPT_ROWS:
        expect(home).not_to_contain_text(row_["detail"])
        if row_["fix"]:
            expect(home).not_to_contain_text(row_["fix"])
    expect(health.locator(".foot")).to_contain_text("Every other check passes")
    expect(health.get_by_role("link", name="All checks on Modules")).to_be_visible()
    expect(health.get_by_role("button")).to_have_count(0)
    ui_width_check(page)
    # A fixture row that reports shows here as at the desk.
    expect(home.locator("moxi-fixture-row-one")).to_be_attached()
    page.evaluate("""() => window.dispatchEvent(new CustomEvent("moxi-fixture-row", {detail: {
      row: "row-one", report: {parts: [{text: "2 running"}], time: "read 19:08"}}}))""")
    expect(home.locator(".entry[data-row='fixture.row-one'] .fact")).to_have_text("2 running")
    ui_width_check(page)
    # The line stays on every page the phone opens.
    page.locator("#moxi-pages-button").click()
    page.locator("#moxi-pages a[data-module=fixture]").click()
    expect(page.locator("#moxi-panel > moxi-fixture-page")).to_be_attached()
    expect(reads).to_be_visible()
    assert reads.bounding_box() == line


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
        set_gateway(shell, enabled=False)  # the entry stays: the off answer
        assert page.evaluate(OFF) == 503  # the shell's next tick turns the gateway off
        page.goto("/m/ui/")
        expect(page.locator("main#moxi-offline")).to_contain_text(DOWN)
        shell.stop()
        page.goto("/m/ui/")
        expect(page.locator("main#moxi-offline")).to_contain_text(DOWN)
        # Nothing answers: the bridge, the phone's way to the computer, is gone. Firefox's
        # offline mode leaves a worker's own fetches on the network, so it can't stand in.
        bridge.stop()
        page.goto("/m/ui/")
        expect(page.locator("main#moxi-offline")).to_contain_text(AWAY)
    finally:
        bridge.stop()
        shell.stop()


def test_a_planted_worker_is_replaced_at_the_next_update_check(
        tmp_path_factory, fixture_site, phones):
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
        # The update check installs the gateway's worker, with no session on the phone, and
        # it takes the registration.
        assert page.evaluate(OWN_OFFLINE) is True
        assert page.evaluate(SETTLED) is True
        page.goto("/m/ui/")
        expect(page).to_have_title("Moxi: sign in")
    finally:
        bridge.stop()
        shell.stop()


def test_a_cached_start_page_and_client_js_planted_before_the_worker_never_run_after(
        tmp_path_factory, fixture_site, phones):
    """A squatter's start document and `client.js`, cached for a year while no worker
    controls the phone, run from the cache until the real worker takes the origin, and never
    after. The phone keeps a live session throughout, so no sign-in page clears the cache:
    the bridge sends the phone to the squatter while the shell runs, which the phone can't
    tell from a squatter holding the port."""
    shell = phone_kit.gateway_shell(tmp_path_factory.mktemp("gw"), fixture_site)
    bridge = PhoneBridge(shell.home, shell.gateway_port).start()
    squat = CachingSquatter().start()

    def answer(target):
        bridge.target = target
        bridge.cut()

    try:
        context = phones(bridge)
        page = _pair(context, shell)
        assert page.evaluate(UNREGISTER) is True
        page.goto("about:blank")
        answer(squat.port)
        page.goto("/m/ui/")
        expect(page).to_have_title("planted client")
        answer(None)  # no gateway, only the bridge's 502: the copies run from the cache
        page.goto("about:blank")
        page.goto("/m/ui/")
        expect(page).to_have_title("planted client")
        answer(squat.port)
        page.goto("/plant?kind=deep")
        expect(page).to_have_title("planted", timeout=30_000)
        answer(shell.gateway_port)
        page.goto("/m/ui/")
        expect(page).to_have_title("squatter")  # the planted worker takes this one
        # The update check installs the real worker, which sends client.js on past the cache.
        assert page.evaluate(OWN_OFFLINE) is True
        assert page.evaluate(SETTLED) is True
        # That worker runs at the planted script's URL, so the shell's next load would
        # register `/sw.js`, which takes over while that load goes on: Firefox then drops
        # the fetches the replaced worker has open, the panel's script among them, and
        # Chromium may hold the next navigation until the replaced worker goes idle. So
        # `/sw.js` takes over here, from this page, which fetches nothing, before the load.
        assert page.evaluate(OWN_WORKER) is True
        page.goto("/m/ui/")
        page.locator("#moxi-panel > moxi-ui-page").wait_for(state="attached")
        assert page.title() != "planted client"
        assert page.evaluate("() => window.__planted === undefined") is True
        assert page.evaluate("async () => (await import('/m/ui/client.js')).gateway()") is True
    finally:
        squat.stop()
        bridge.stop()
        shell.stop()


#: The most loads the worker-swap test makes for one whose panel script is dropped.
SWAP_LOADS = 40
#: How long after `/sw.js` is asked for each load's modules list goes on, in turn: the
#: swap's moment moves with the machine's load, so the loads try a spread of them.
SWAP_DELAYS = (0.0, 0.01, 0.02, 0.04, 0.08, 0.16)


def test_a_panel_script_dropped_while_the_worker_is_replaced_is_placed_on_one_more_try(
        ui_browser, tmp_path_factory, fixture_site, phones):
    """The gateway's worker, registered from another script URL, controls the phone, so
    the shell's next load registers `/sw.js`, which replaces it while the load goes on.
    Firefox ends the replaced worker at once and drops a fetch it is sent then. The modules
    list is held until `/sw.js` is asked for, and a little more, so the panel's script is
    fetched near that moment; loads go on until one has it dropped. The shell finds the script fine and tries
    once more, and every load's panel is placed.

    The swap's moment moves with the machine's load, so a busy machine may see no drop in
    all the loads. That run skips, saying so: it never took the retry, and the rows in
    test_browser_place.py take the same path on every run, in every engine."""
    if _engine(ui_browser) != "firefox":
        pytest.skip("only Firefox ends a replaced worker at once and drops what it is sent; "
                    "Chromium keeps it until its fetches finish")
    shell = phone_kit.gateway_shell(tmp_path_factory.mktemp("gw"), fixture_site)
    relay = HoldingRelay(shell.gateway_port).start()
    bridge = PhoneBridge(shell.home, relay.port).start()
    try:
        page = _pair(phones(bridge), shell)
        dropped: list[str] = []
        page.on("console", lambda msg: dropped.append(msg.text)
                if "moxi: ui's page could not load" in msg.text else None)
        panel = page.locator("#moxi-panel > moxi-ui-page")
        message = page.locator("#moxi-panel .moxi-message")
        for load in range(SWAP_LOADS):
            assert page.evaluate(WORKER_AT, f"/m/ui/swap/{load}/sw.js") is True
            relay.hold("/m/ui/api/modules", until="/sw.js",
                       delay=SWAP_DELAYS[load % len(SWAP_DELAYS)])
            page.goto("/m/ui/")
            expect(panel.or_(message)).to_be_attached()
            assert message.count() == 0, message.inner_text()
            if dropped:
                break
        if not dropped:
            pytest.skip(f"none of {SWAP_LOADS} loads had its panel script dropped, so this "
                        "run never took the retry: the worker swap's moment depends on the "
                        "machine's timing")
    finally:
        bridge.stop()
        relay.stop()
        shell.stop()


def test_an_offline_page_and_client_js_planted_in_cache_storage_never_run(
        tmp_path_factory, fixture_site, phones):
    """A squatter's page, served while the real worker controls the phone, writes its own
    `/offline`, `/offline.js` and `client.js` into Cache Storage, under the worker's cache
    name and another. None of them runs on the real gateway, on its off answer, or with
    nothing answering; the offline page and its script come from the worker's own bytes,
    with the gateway's headers."""
    shell = phone_kit.gateway_shell(tmp_path_factory.mktemp("gw"), fixture_site)
    bridge = PhoneBridge(shell.home, shell.gateway_port).start()
    squat = StoringSquatter().start()

    def answer(target):
        bridge.target = target
        bridge.cut()

    try:
        page = _pair(phones(bridge), shell)
        assert page.evaluate(WORKER) == ORIGIN + "/sw.js"
        answer(squat.port)
        page.goto("/m/ui/")
        expect(page).to_have_title("stored in caches")
        answer(shell.gateway_port)
        page.goto("/m/ui/")
        page.locator("#moxi-panel > moxi-ui-page").wait_for(state="attached")
        assert page.evaluate(NOT_PLANTED) is True
        set_gateway(shell, enabled=False)
        assert page.evaluate(OFF) == 503
        page.goto("/m/ui/")
        expect(page.locator("main#moxi-offline")).to_contain_text(DOWN)
        expect(page).to_have_title("Moxi: offline")
        assert page.evaluate(NOT_PLANTED) is True
        assert page.evaluate(CLIENT_STATUS) == 503
        assert page.evaluate(OWN_HEADERS) == {
            **{name.decode(): value.decode()
               for name, value in response_headers(policy="gateway")},
            "content-type": "text/javascript; charset=utf-8"}
        bridge.stop()  # nothing answers
        page.goto("/m/ui/")
        expect(page.locator("main#moxi-offline")).to_contain_text(AWAY)
        expect(page).to_have_title("Moxi: offline")
        assert page.evaluate(NOT_PLANTED) is True
        assert page.evaluate(CLIENT_STATUS) == 0
        assert page.evaluate(STILL_PLANTED) is True  # the copies were there all along
    finally:
        squat.stop()
        bridge.stop()
        shell.stop()


@pytest.mark.skip(reason=SOCKET_SKIP)
def test_on_the_socket_endpoint_the_sheet_acts_once_and_a_lapsed_mount_gives_the_desk():
    """Waits for a system whose socket probe passes; see SOCKET_TESTS."""
