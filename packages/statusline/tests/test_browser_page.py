"""statusline's page in a browser, through a real shell that starts its real page server:
the contract check, the 360 px width check, and a move that changes the preview and
leaves the saved config alone until Save."""

from __future__ import annotations

import re
import sys

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="the status line runs on Linux only here")]


@pytest.fixture
def shell(ui_shell):
    """The module's shell, its statusline config written fresh for each test."""
    path = ui_shell.config_file("statusline")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('show = ["model", "effort"]\nseparator = " / "\ncolor = false\n')
    return ui_shell


def open_page(page):
    page.goto("/m/statusline/")
    panel = page.locator("#moxi-panel > moxi-statusline-page")
    expect(panel.locator("div[data-state]")).to_have_attribute("data-state", "up")
    return panel


def test_the_page_keeps_the_contract(shell, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "statusline", "page")
    assert check.element == "moxi-statusline-page"
    assert any("/m/statusline/api/state" in url for url in check.requests), check.requests


def test_the_page_fits_a_phone(shell, ui_page, ui_width_check):
    ui_width_check(ui_page, module="statusline")
    panel = ui_page.locator("#moxi-panel > moxi-statusline-page")
    expect(panel.locator("pre.line")).to_have_text("Sample / high")
    # With unsaved changes the draft's bar shows, and the page still fits.
    panel.get_by_role("button", name="Move Effort level up").click()
    expect(panel.get_by_role("button", name="Save")).to_be_visible()
    ui_width_check(ui_page)


def test_a_move_changes_the_preview_and_the_config_only_on_save(shell, ui_page):
    path = shell.config_file("statusline")
    saved = path.read_bytes()
    panel = open_page(ui_page)
    line = panel.locator("pre.line")
    expect(line).to_have_text("Sample / high")
    expect(panel.get_by_role("button", name="Save")).to_have_count(0)
    expect(panel.locator("li[data-item]").first).to_have_attribute("data-item", "model")

    panel.get_by_role("button", name="Move Effort level up").click()
    expect(line).to_have_text("high / Sample")
    expect(panel.locator("[data-count]")).to_have_text("1 unsaved change")
    expect(panel.get_by_role("button", name="Use Moxi's line")).to_be_disabled()
    expect(panel.locator("[data-held]")).to_have_text("Save or discard first")
    assert path.read_bytes() == saved

    panel.get_by_role("button", name="Save").click()
    expect(panel.locator("[data-message=ok]")).to_contain_text("Saved")
    expect(panel.get_by_role("button", name="Save")).to_have_count(0)
    assert re.search(r'show = \["effort", "model"\]', path.read_text())
    expect(line).to_have_text("high / Sample")
    expect(panel.get_by_role("button", name="Use Moxi's line")).to_be_enabled()


def test_discard_gives_back_the_saved_line(shell, ui_page):
    panel = open_page(ui_page)
    line = panel.locator("pre.line")
    expect(line).to_have_text("Sample / high")
    panel.get_by_role("button", name="Move Model down").click()
    expect(line).to_have_text("high / Sample")
    panel.get_by_role("button", name="Discard").click()
    expect(line).to_have_text("Sample / high")
    expect(panel.get_by_role("button", name="Save")).to_have_count(0)


def test_a_refused_draft_says_why(shell, ui_page):
    panel = open_page(ui_page)
    expect(panel.locator("pre.line")).to_have_text("Sample / high")
    warn = panel.get_by_role("spinbutton", name="Context turns yellow at (%)")
    warn.fill("95")
    warn.dispatch_event("change")
    expect(panel.locator("[data-problem=draft]")).to_contain_text("context.warn_at")
    expect(panel.get_by_role("button", name="Save")).to_be_disabled()


def test_a_config_that_stops_reading_shows_the_line_claude_prints(shell, ui_page):
    panel = open_page(ui_page)
    line = panel.locator("pre.line")
    expect(line).to_have_text("Sample / high")
    shell.config_file("statusline").write_text('show = ["row", "model"]\n')
    ui_page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(panel.locator("[data-problem=config]")).to_contain_text("can't be read")
    expect(line).to_have_text("config?")
    expect(line).to_have_attribute("data-state", "unreadable")
    expect(panel.locator("[data-note]")).to_have_text(
        "Claude Code's line shows this until the saved config can be read.")
