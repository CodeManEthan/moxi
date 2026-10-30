"""What a squatter leaves in the browser, and what the shell does about it.

A squatter holds the port first: it fills the cookie jar for 127.0.0.1, plants a service
worker with scope `/`, caches a page for a year and writes the origin's storage. Then it
lets the port go and the shell takes it. A planted worker takes the first navigation
still, its update check gets the shell's worker answer, and the second navigation is the
shell's. The sign-in completes with the full jar and clears the cached page and the
storage.

Each test runs in the browser this run uses (`--ui-browser`), and is listed once per
engine, so the engines this run doesn't use show as skipped. The longest script URL is the
longest each engine registers: Chromium 2 MiB, Firefox 1 MiB, less the origin, and a
4,000,000-byte path in WebKit.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

from squatter import Squatter

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120)]

ENGINES = ("chromium", "firefox", "webkit")
#: The `a`s in `/m/ui/<a's>/sw.js` for the longest script URL each engine registers.
LONGEST = {"chromium": 2_097_100, "firefox": 1_048_000, "webkit": 4_000_000}
#: 180 cookies of 4 KiB: the most Chromium and Firefox keep for one host.
JAR = "/cookies?k=180&s=4096"
#: A worker planted from a long URL takes a while to register.
PLANT_MS = 30_000

# The engine sends the update check a moment after the navigation the worker took, not
# during it, so the test waits for the registration to go.
GONE_S = 10
WORKERS = "async () => (await navigator.serviceWorker.getRegistrations()).length"


def _this_run(engine: str, ui_browser) -> None:
    running = ui_browser.browser_type.name
    if engine != running:
        pytest.skip(f"this run uses {running}; run with --ui-browser {engine} for {engine}")


def _plant(contexts, squat, kind: str, n: int = 0):
    page = contexts(SimpleNamespace(url=squat.url), sign_in=False).new_page()
    page.goto(JAR)
    expect(page).to_have_title("filled")
    page.goto(f"/plant?kind={kind}&n={n}")
    expect(page).to_have_title("planted", timeout=PLANT_MS)
    return page


@pytest.mark.parametrize("engine", ENGINES)
def test_a_planted_worker_takes_one_navigation_and_is_gone_by_the_second(
        engine, ui_browser, ui_contexts, ui_shells, fixture_site):
    _this_run(engine, ui_browser)
    squat = Squatter().start()
    try:
        planted = {kind: _plant(ui_contexts, squat, kind, LONGEST[engine])
                   for kind in ("deep", "percent", "long")}
    finally:
        squat.stop()
    shell = ui_shells(site=[fixture_site], port=squat.port)
    for kind, page in planted.items():
        assert page.evaluate(WORKERS) == 1, kind
        page.goto("/m/ui/")
        expect(page, f"the {kind} worker's navigation").to_have_title("squatter")
        wait_for(lambda: page.evaluate(WORKERS) == 0, GONE_S, interval=0.1,
                 what=f"the {kind} worker to unregister")
        page.goto("/m/ui/")
        expect(page, f"after the {kind} worker").to_have_title("Moxi: signed out")
        assert page.evaluate(WORKERS) == 0, kind
        shell.sign_in(page)
        expect(page.locator("#moxi-panel > moxi-ui-page")).to_be_attached()


@pytest.mark.parametrize("engine", ENGINES)
def test_a_full_jar_still_signs_in_and_the_sign_in_clears_the_cached_page_and_storage(
        engine, ui_browser, ui_contexts, ui_shells, fixture_site):
    _this_run(engine, ui_browser)
    squat = Squatter().start()
    try:
        context = ui_contexts(SimpleNamespace(url=squat.url), sign_in=False)
        page = context.new_page()
        for path, title in ((JAR, "filled"), ("/storage", "stored"), ("/cached", "squatter cached")):
            page.goto(path)
            expect(page).to_have_title(title)
    finally:
        squat.stop()
    shell = ui_shells(site=[fixture_site], port=squat.port)
    jar = [c for c in context.cookies() if c["name"].startswith("q")]
    assert len(jar) >= 150, len(jar)
    # Before a sign-in the cached page is served without asking the shell.
    page.goto("/cached")
    cached = page.title() == "squatter cached"
    assert cached or engine == "webkit", "the squatter's page was not kept in the cache"
    shell.sign_in(page)
    assert page.evaluate("Object.keys(localStorage)") == ["moxi-ui-key"]
    page.goto("/cached")
    expect(page.locator("#moxi-shell")).to_be_attached()
    expect(page).not_to_have_title("squatter cached")
    # The full jar rides on every request, and the routes still answer.
    page.goto("/m/ui/")
    expect(page.locator("#moxi-panel > moxi-ui-page .card[data-module=fixture]")).to_be_visible()
    if not cached:
        pytest.skip("the sign-in completed with the full jar and cleared the storage, but this "
                    "WebKit fetched the year-cached page again before the sign-in, so the "
                    "cache part shows nothing here")


@pytest.mark.parametrize("engine", ENGINES)
def test_a_worker_webkit_registered_from_a_5_000_000_byte_path_stays(
        engine, ui_browser, ui_contexts, ui_shells, fixture_site):
    """The residue the design states for WebKit: its update check is over the shell's head
    limit, so the shell never reaches that worker. A WebKit that changes shows here."""
    if engine != "webkit":
        pytest.skip(f"{engine} caps the script URL far under the head limit; only WebKit "
                    "registers one this long")
    _this_run(engine, ui_browser)
    squat = Squatter().start()
    try:
        page = _plant(ui_contexts, squat, "long", 5_000_000)
    finally:
        squat.stop()
    ui_shells(site=[fixture_site], port=squat.port)
    for _ in range(3):
        page.goto("/m/ui/")
        expect(page).to_have_title("squatter")
    assert page.evaluate(WORKERS) == 1
