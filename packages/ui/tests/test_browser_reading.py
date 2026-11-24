"""The page root's `data-reading` mark in a browser, and the kit's press that waits for it.

The test shell serves `ui` and the `fixture` module (tests/conftest.py). The fixture's page
reads `api/summary` when its `Read` is clicked."""

from __future__ import annotations

import re

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

from moxi_ui.testing import WAIT_S
from moxi_ui.testing.browser import READING_MARK, settled

pytestmark = [pytest.mark.browser, pytest.mark.timeout(60, method="thread")]

SUMMARY = re.compile(r"/m/fixture/api/summary(\?.*)?$")

# Holds the first `api/summary` fetch 1.5 s before sending it, and notes, at every click
# in the document, the page root's mark and the summary line as they stood.
_SLOW_FIRST_READ = """(() => {
  const fetched = window.fetch.bind(window);
  let first = true;
  window.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if (first && url.includes("/m/fixture/api/summary")) {
      first = false;
      await new Promise((done) => setTimeout(done, 1500));
    }
    return fetched(input, init);
  };
  window.clicks = [];
  document.addEventListener("click", () => {
    const page = document.querySelector("moxi-fixture-page");
    window.clicks.push({
      mark: document.getElementById("moxi-panel").getAttribute("data-reading"),
      summary: page?.shadowRoot?.querySelector("[data-testid=summary]")?.textContent ?? "",
    });
  }, true);
})()"""


def _fixture_page(page):
    page.goto("/m/fixture/")
    view = page.locator("#moxi-panel > moxi-fixture-page")
    expect(view.get_by_role("button", name="Read")).to_be_visible()
    settled(page)
    return view


def test_the_page_root_counts_the_reads_out_and_loses_the_mark_at_none(ui_page):
    view = _fixture_page(ui_page)
    panel = ui_page.locator("#moxi-panel")
    held = []
    ui_page.route(SUMMARY, lambda route: held.append(route))
    read = view.get_by_role("button", name="Read")
    read.click()
    expect(panel).to_have_attribute(READING_MARK, "1")
    read.click()
    expect(panel).to_have_attribute(READING_MARK, "2")
    ui_page.wait_for_function("() => true")
    assert len(held) == 2
    held.pop(0).continue_()
    expect(panel).to_have_attribute(READING_MARK, "1")
    held.pop(0).continue_()
    expect(view.get_by_test_id("summary")).to_have_text(re.compile(r"^Summary: \d+"))
    settled(ui_page, wait_ms=2000)


def test_the_kits_press_clicks_only_once_the_read_out_has_been_drawn(ui_page, ui_press):
    ui_page.add_init_script(_SLOW_FIRST_READ)
    view = _fixture_page(ui_page)
    read = view.get_by_role("button", name="Read")
    read.click()
    ui_press(read)
    first, second = ui_page.evaluate("() => window.clicks")
    assert first["mark"] is None
    assert second["mark"] is None
    assert second["summary"].startswith("Summary: "), second


def test_a_press_on_a_page_that_never_settles_fails_naming_the_mark(ui_page, ui_press):
    view = _fixture_page(ui_page)
    held = []
    ui_page.route(SUMMARY, lambda route: held.append(route))
    view.get_by_role("button", name="Read").click()
    expect(ui_page.locator("#moxi-panel")).to_have_attribute(READING_MARK, "1")
    with pytest.raises(AssertionError, match=r'data-reading="1" after 1500 ms'):
        ui_press(view.get_by_role("button", name="Modules"), wait_ms=1500)
    assert ui_page.url.endswith("/m/fixture/")
    for route in held:
        route.continue_()


def test_the_modules_view_counts_a_block_still_being_placed(ui_page):
    held = []
    ui_page.route(re.compile(r"/m/fixture/[^?]*block\.js(\?.*)?$"), lambda route: held.append(route))
    ui_page.goto("/m/ui/modules")
    panel = ui_page.locator("#moxi-panel")
    view = panel.locator("moxi-ui-page")
    expect(view.locator(".list .card[data-module='fixture']")).to_be_visible()
    # The card shows before the block's script is asked for; the route's handler runs as
    # Playwright's events are read, which the short waits let happen.
    wait_for(lambda: ui_page.wait_for_timeout(20) or held, WAIT_S,
             what="the fixture block's script to be asked for")
    expect(panel).to_have_attribute(READING_MARK, re.compile(r"^[1-9]\d*$"))
    held.pop(0).continue_()
    expect(view.locator("moxi-fixture-block")).to_be_attached()
    settled(ui_page)
