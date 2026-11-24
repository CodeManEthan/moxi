"""usage's row on Home in a browser, through real shells that start usage's real page
server: a limit filling before its reset leads the row heavy and needs the user, a limit
that is not says its percents and needs nothing, a click opens usage's page, a shell
without usage and comms draws neither row and names neither, and on a phone, through the
gateway, the row shows from `/api/summary`, the one usage route the gateway reaches.

The plan readings, the transcripts and the index are written in each shell's own
environment, so they sit under its home where its page servers read them.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="usage's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

USAGE, COMMS, HEALTH = "usage.row", "comms.mail", "ui.health"
SENTENCE = "Nothing needs you."
TIME = r"\d{1,2}:\d\d(?: [AP]M)?"
#: A time with the day it may carry: `21:34`, `tomorrow 21:34`, `Wed 23:58`, `Nov 3 08:52`.
WHEN = rf"(?:\w+ (?:\d+ )?)?{TIME}"
COST = r"today \$\d+\.\d\d\+?"
SESSION = "0190b000-0000-7000-8000-0000000000c1"

#: One Claude session of three requests, begun a minute ago and kept on today: today's cost.
SEED = """
import sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=sys.argv[1],
                       project="-work-proj-a", cwd="/work/proj-a",
                       start=T.today_at(60, oldest_s=60, now=time.time()))
for m in range(3):
    t.user("Fix the failing test.")
    t.assistant("Done.", usage=T.usage(input_tokens=200, output=1000),
                request_id=f"req_c{m}", model="claude-sonnet-4-5")
t.write()
"""

#: Claude's 5-hour window at 20, 35 and 48.6% over the last 50 minutes, resetting in three
#: hours, so it fills before it resets; its weekly window at 0%, read once.
FILLING = """
import time
from moxi_usage import readings
from moxi_usage.readings import Reading
now = time.time()
found = [
    Reading("claude", "plan", 300, "five_hour", used, now + 3 * 3600, now - ago, "session-usage", "s1")
    for used, ago in ((20.0, 3000), (35.0, 1500), (48.6, 240))
] + [Reading("claude", "plan", 10080, "seven_day", 0.0, now + 5 * 86400, now - 240, "session-usage", "s1")]
conn = readings.open_store()
try:
    readings.store(conn, found, now=now, days=7)
finally:
    conn.close()
"""

#: Each of Claude's main windows read once: no pace, so neither fills before its reset.
STEADY = """
import time
from moxi_usage import readings
from moxi_usage.readings import Reading
now = time.time()
found = [Reading("claude", "plan", 300, "five_hour", 30.0, now + 3 * 3600, now - 240, "session-usage", "s1"),
         Reading("claude", "plan", 10080, "seven_day", 60.0, now + 5 * 86400, now - 240, "session-usage", "s1")]
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


def run(shell, *argv: str) -> None:
    done = subprocess.run(list(argv), env=shell.env, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")


def seed(shell, plan: str, *, cost: bool) -> None:
    """Store `plan`'s readings under the shell's home and, with `cost`, today's session and
    its index."""
    if cost:
        run(shell, sys.executable, "-c", SEED, SESSION)
        run(shell, sys.executable, "-m", "moxi", "usage", "index")
    run(shell, sys.executable, "-c", plan)


@pytest.fixture(scope="module")
def filling(ui_shells):
    """A shell whose 5-hour window fills before it resets, with today's cost. The
    launcher is left out, so Home's spot holds the sentence and shows when it goes."""
    shell = ui_shells(leave_out=["launcher"])
    seed(shell, FILLING, cost=True)
    return shell


@pytest.fixture(scope="module")
def steady(ui_shells):
    """A shell whose windows don't fill before they reset, with no index."""
    shell = ui_shells(leave_out=["launcher"])
    seed(shell, STEADY, cost=False)
    return shell


def home_of(ui_contexts, shell):
    """A signed-in page on `shell`'s Home that keeps what each row reports."""
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


# --- at the desk ---------------------------------------------------------------------------

def test_a_limit_filling_before_its_reset_leads_the_row_and_needs_the_user(filling, ui_contexts):
    page = home_of(ui_contexts, filling)
    line = _line(page, USAGE)
    expect(line.locator(".label")).to_have_text("Usage")
    expect(line.locator(".fact")).to_have_text(
        re.compile(rf"^Claude 5-hour limit fills {WHEN}, resets {WHEN} · {COST}$"))
    expect(line.locator(".fact .heavy")).to_have_text(re.compile(rf"^Claude 5-hour limit fills {WHEN}$"))
    expect(line.locator(".time")).to_have_text(re.compile(rf"^read {WHEN}$"))
    # It needs the user: the sentence goes, and the row sits above Health, which does not.
    # The comms daemon is not running, so comms reports no row.
    expect(_home(page).get_by_text(SENTENCE)).to_have_count(0)
    _expect_shown(page, [USAGE, HEALTH])
    assert page.evaluate("window.homeNeeds")["moxi-usage-row"] == 40


def test_a_limit_that_is_not_filling_says_its_percents_and_needs_nothing(steady, ui_contexts):
    page = home_of(ui_contexts, steady)
    line = _line(page, USAGE)
    expect(line.locator(".fact")).to_have_text(re.compile(rf"^Claude 5-hour 30%, resets {WHEN} · weekly 60%$"))
    expect(line.locator(".fact .heavy")).to_have_count(0)
    expect(line.locator(".time")).to_have_text(re.compile(rf"^read {WHEN}$"))
    expect(_home(page).get_by_text(SENTENCE)).to_be_visible()
    _expect_shown(page, [USAGE, HEALTH])
    assert page.evaluate("window.homeNeeds")["moxi-usage-row"] == 0


def test_a_click_on_the_usage_row_opens_usages_page(filling, ui_contexts):
    page = home_of(ui_contexts, filling)
    _line(page, USAGE).click()
    expect(page).to_have_url(f"{filling.url}/m/usage/")
    expect(page.locator("#moxi-panel > moxi-usage-page [data-group=limits]")).to_be_visible()


# --- without usage and comms ---------------------------------------------------------------

def test_without_usage_and_comms_home_draws_neither_row_and_names_neither(ui_shells, ui_contexts):
    shell = ui_shells(leave_out=["usage", "comms"])
    asked: list[str] = []
    context = ui_contexts(shell)
    page = context.pages[0]
    page.on("request", lambda request: asked.append(request.url))
    page.goto("/m/ui/")
    _expect_shown(page, [HEALTH])
    expect(_home(page).locator(f".entry[data-row='{USAGE}'], .entry[data-row='{COMMS}']")).to_have_count(0)
    expect(page.locator("moxi-usage-row, moxi-comms-mail")).to_have_count(0)
    text = _home(page).inner_text()
    assert "Usage" not in text and "Comms" not in text, text
    page.locator("#moxi-pages-button").click()
    expect(page.locator("#moxi-pages a[data-module=ui]").first).to_be_visible()
    expect(page.locator("#moxi-pages a[data-module=usage], #moxi-pages a[data-module=comms]")).to_have_count(0)
    status, data = shell.session().get("/m/ui/api/modules")
    assert status == 200
    names = {module["name"] for module in data["modules"]}
    assert not names & {"usage", "comms"}, names
    assert not [url for url in asked if "/m/usage/" in url or "/m/comms/" in url], asked


# --- on a phone ----------------------------------------------------------------------------

def phone_kit():
    """ui's phone test kit. ui is an optional module of this one, so the kit is reached by
    name, and the phone tests skip where ui isn't installed."""
    return pytest.importorskip("moxi_ui.testing.phone")


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    """A gateway-mode shell holding its TCP endpoint, its 5-hour window filling, and the
    bridge a phone reaches it through."""
    kit = phone_kit()
    shell = kit.gateway_shell(tmp_path_factory.mktemp("gw"))
    bridge = None
    try:
        seed(shell, FILLING, cost=False)
        bridge = kit.PhoneBridge(shell.home, shell.gateway_port).start()
        yield shell, bridge
    finally:
        if bridge is not None:
            bridge.stop()
        shell.stop()


def test_the_gateway_reaches_the_summary_and_no_other_usage_route(gw):
    shell, bridge = gw
    phone = bridge.pair(shell)
    status, data = phone.get("/m/usage/api/summary")
    assert status == 200, data
    assert data["limits"]["harness"] == "claude"
    assert [window["window"] for window in data["limits"]["windows"]] == ["5h", "7d"]
    assert data["limits"]["windows"][0]["full_at"] is not None
    desk = shell.session()
    for path in ("/m/usage/api/limits", "/m/usage/api/cost", "/m/usage/api/status",
                 "/m/usage/api/daily", "/m/usage/api/window?harness=claude&limit_id=five_hour&minutes=300"):
        assert phone.get(path)[0] == 404, path
        # The desk reaches it, so the 404 is the gateway's.
        assert desk.get(path)[0] in (200, 422), path
    for path in ("/m/usage/api/events?topics=limits", "/m/usage/api/events?topics=index"):
        with phone.stream(path) as events:
            assert events.status == 404, path
    assert phone.post("/m/usage/api/pass", {})[0] == 404


def test_the_phone_shows_the_usage_row(gw, ui_browser):
    kit = phone_kit()
    shell, bridge = gw
    browser = bridge.launch(ui_browser.browser_type)
    try:
        context = browser.new_context(**kit.PhoneBridge.context_args(ui_browser.browser_type.name))
        context.set_default_timeout(pytest.importorskip("moxi_ui.testing").WAIT_MS)
        page = kit.pair_page(context, shell)
        line = _line(page, USAGE)
        expect(line.locator(".label")).to_have_text("Usage")
        expect(line.locator(".fact .heavy")).to_have_text(re.compile(rf"^Claude 5-hour limit fills {WHEN}$"))
        expect(line.locator(".fact")).to_have_text(re.compile(rf"^Claude 5-hour limit fills {WHEN}, resets {WHEN}$"))
        expect(line.locator(".time")).to_have_text(re.compile(rf"^read {WHEN}$"))
        # Home draws it as the desk does, a link to usage's page.
        expect(line).to_have_attribute("href", "/m/usage/")
        _expect_shown(page, [USAGE, HEALTH])
    finally:
        browser.close()
