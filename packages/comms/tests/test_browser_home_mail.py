"""comms' row on Home in a browser, through real shells that start comms' real page server,
asking the rig's daemon: mail for a standing name nobody runs shows as a count and the
name, never the message's text; the row rises above a filling Usage row and Health; a
click opens comms' page; and on a phone, through the gateway, the row shows from
`/api/mail`, the one comms route the gateway reaches.

Each test sends its mail after the rig's daemon starts, from a shim nobody waits on, to
`main@proj-b`, one of the standing names the rig provisions.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

import roster_kit
from moxi_comms import api, claimfile
from roster_kit import wait_for

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="comms runs on Linux only")]

COMMS, USAGE, HEALTH = "comms.mail", "usage.row", "ui.health"
TIME = r"\d{1,2}:\d\d(?: [AP]M)?"
TEXT = "the parser fix is ready"
TO = "main@proj-b"
WITH_USAGE = importlib.util.find_spec("moxi_usage") is not None

#: Claude's 5-hour window at 20, 35 and 48.6% over the last 50 minutes, resetting in three
#: hours, so it fills before it resets: usage's row needs the user.
FILLING = """
import time
from moxi_usage import readings
from moxi_usage.readings import Reading
now = time.time()
found = [
    Reading("claude", "plan", 300, "five_hour", used, now + 3 * 3600, now - ago, "session-usage", "s1")
    for used, ago in ((20.0, 3000), (35.0, 1500), (48.6, 240))
]
conn = readings.open_store()
try:
    readings.store(conn, found, now=now, days=7)
finally:
    conn.close()
"""

#: Keeps the last `needs` each row component reported to Home, by its element's name, in
#: `window.homeNeeds` (null for a report of nothing). Home stops the event at the row's
#: host, so it is heard on its way down, at the window.
NEEDS = """window.homeNeeds = {};
window.addEventListener("moxi-home-row", (event) => {
  const from = event.composedPath()[0];
  window.homeNeeds[from.localName] = event.detail === null ? null : (event.detail.needs ?? 0);
}, true);"""


def _clear_claims() -> None:
    """No claim file left by an earlier test: it names a claim socket in that test's tmp,
    and the daemon would try it."""
    for path in claimfile.claim_dir().glob(f"{claimfile.PREFIX}*{claimfile.SUFFIX}"):
        path.unlink()


@pytest.fixture(autouse=True)
def shell(ui_shell, monkeypatch):
    """The module's shell, the rig's daemon the one its comms page server asks."""
    roster_kit.point_at(ui_shell, monkeypatch)
    _clear_claims()
    return ui_shell


def send_mail(rig) -> None:
    """Mail from a running `planner` to `main@proj-b`, which no session runs, and wait
    until the daemon holds it."""
    rig.shim(name="planner", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", TO, TEXT], name="planner", scope="proj-a")
    assert code == 0, out
    wait_for(lambda: api.roster().waiting, 5, what="mail to wait")


def home_of(ui_contexts, shell=None):
    """A signed-in page on the shell's Home that keeps what each row reports."""
    context = ui_contexts(shell)
    context.add_init_script(NEEDS)
    page = context.pages[0]
    page.goto("/m/ui/")
    return page


def _home(page):
    return page.locator("#moxi-panel > moxi-ui-page moxi-ui-home")


def _line(page, key: str):
    return _home(page).locator(f".entry[data-row='{key}'] .row")


def _shown(page) -> list[str]:
    """The rows in sight, top first."""
    return _home(page).locator(".entry").evaluate_all(
        """entries => entries.filter((entry) => entry.getBoundingClientRect().height > 0)
             .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)
             .map((entry) => entry.dataset.row)""")


def _expect_shown(page, keys: list[str]) -> None:
    for key in keys:
        expect(_line(page, key)).to_be_visible()
    expect(_home(page).locator(".entry:not([hidden])")).to_have_count(len(keys))
    assert _shown(page) == keys


def _expect_mail_line(page) -> None:
    line = _line(page, COMMS)
    expect(line.locator(".label")).to_have_text("Comms")
    expect(line.locator(".fact")).to_have_text("1 message waiting · main isn't running")
    expect(line.locator(".fact .heavy")).to_have_text("1 message waiting")
    expect(line.locator(".time")).to_have_text(re.compile(rf"^since {TIME}$"))


# --- at the desk ---------------------------------------------------------------------------

def test_mail_for_a_name_nobody_runs_shows_its_count_and_name_and_never_its_text(
        shell, rig, ui_contexts):
    send_mail(rig)
    status, data = shell.session().get("/m/comms/api/mail")
    assert status == 200, data
    assert data["state"] == "up"
    assert [(row["qualified"], row["name"], row["count"]) for row in data["waiting"]] == [(TO, "main", 1)]
    assert TEXT not in json.dumps(data)
    page = home_of(ui_contexts)
    _expect_mail_line(page)
    assert TEXT not in _home(page).inner_text()
    assert page.evaluate("window.homeNeeds")["moxi-comms-mail"] == 20


def test_a_click_on_the_comms_row_opens_comms_page(shell, rig, ui_contexts):
    send_mail(rig)
    page = home_of(ui_contexts)
    _line(page, COMMS).click()
    expect(page).to_have_url(f"{shell.url}/m/comms/")
    expect(page.locator("#moxi-panel > moxi-comms-page [data-part=foot]")).to_be_visible()
    expect(page.locator("#moxi-panel > moxi-comms-page h1")).to_have_count(0)


@pytest.mark.skipif(not WITH_USAGE, reason="usage isn't installed here")
def test_waiting_mail_rises_above_a_filling_usage_row_and_health(shell, rig, ui_contexts):
    done = subprocess.run([sys.executable, "-c", FILLING], env=shell.env, capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    page = home_of(ui_contexts)
    # No mail waits: no Comms row, and the filling Usage row leads.
    _expect_shown(page, [USAGE, HEALTH])
    expect(_line(page, USAGE).locator(".fact .heavy")).to_have_text(re.compile(r"^Claude 5-hour limit fills "))
    send_mail(rig)
    roster_kit.show_tab(page)
    _expect_shown(page, [COMMS, USAGE, HEALTH])
    # Lower `needs` sits higher: mail waits for the user (20) before a limit runs out (40).
    needs = page.evaluate("window.homeNeeds")
    assert (needs["moxi-comms-mail"], needs["moxi-usage-row"]) == (20, 40), needs


# --- on a phone ----------------------------------------------------------------------------

def phone_kit():
    """ui's phone test kit. ui is an optional module of this one, so the kit is reached by
    name, and the phone tests skip where ui isn't installed."""
    return pytest.importorskip("moxi_ui.testing.phone")


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    """A gateway-mode shell holding its TCP endpoint, and the bridge a phone reaches it
    through."""
    kit = phone_kit()
    shell = kit.gateway_shell(tmp_path_factory.mktemp("gw"))
    bridge = None
    try:
        bridge = kit.PhoneBridge(shell.home, shell.gateway_port).start()
        yield shell, bridge
    finally:
        if bridge is not None:
            bridge.stop()
        shell.stop()


@pytest.fixture
def phone_rig(gw, shell, monkeypatch, request):
    """The rig, its daemon the one the gateway shell's comms page server asks, with mail
    waiting."""
    roster_kit.point_at(gw[0], monkeypatch)
    _clear_claims()
    rig = request.getfixturevalue("rig")
    send_mail(rig)
    return rig


def test_the_gateway_reaches_the_mail_read_and_no_other_comms_route(gw, phone_rig):
    shell, bridge = gw
    phone = bridge.pair(shell)
    status, data = phone.get("/m/comms/api/mail")
    assert status == 200, data
    assert data["state"] == "up"
    assert [(row["name"], row["count"]) for row in data["waiting"]] == [("main", 1)]
    assert TEXT not in json.dumps(data)
    desk = shell.session()
    assert phone.get("/m/comms/api/roster")[0] == 404
    # The desk reaches it, so the 404 is the gateway's.
    assert desk.get("/m/comms/api/roster")[0] == 200
    with phone.stream("/m/comms/api/events?topics=roster") as events:
        assert events.status == 404


def test_the_phone_shows_the_comms_row(gw, phone_rig, ui_browser):
    kit = phone_kit()
    shell, bridge = gw
    browser = bridge.launch(ui_browser.browser_type)
    try:
        context = browser.new_context(**kit.PhoneBridge.context_args(ui_browser.browser_type.name))
        context.set_default_timeout(pytest.importorskip("moxi_ui.testing").WAIT_MS)
        page = kit.pair_page(context, shell)
        _expect_mail_line(page)
        # Home draws it as the desk does, a link to comms' page.
        expect(_line(page, COMMS)).to_have_attribute("href", "/m/comms/")
        assert TEXT not in _home(page).inner_text()
    finally:
        browser.close()
        # Every connection through the bridge ends inside the test that opened it.
        bridge.cut()
