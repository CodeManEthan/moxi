"""cache's page in a browser, through a real shell that starts cache's real page server:
the contract check, the groups of rows with no id on them and the foot, the empty state
and the Watcher row, rows that follow the topic, the failure and error lines, and the
360 px width check on the page and the chip."""

from __future__ import annotations

import re
import sys

import pytest
from playwright.sync_api import expect

import page_kit
from page_kit import put, record

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="cache runs on Linux only")]

A = "aaaaaaaa-1111-4111-8111-111111111111"
B = "bbbbbbbb-2222-4222-8222-222222222222"
EMPTY = "No Claude Code session has a cache yet."
UNEXPECTED = "cache's page server hit an unexpected error (ValueError); its log has the details"


@pytest.fixture
def shell(ui_shell):
    """The module's shell, with no clock record or reading left by an earlier test."""
    page_kit.clear(ui_shell)
    page_kit.clear_reading(ui_shell)
    return ui_shell


def write(shell, *records):
    """Write `records` and wait until the topic holds exactly them."""
    for rec in records:
        put(shell, rec)
    page_kit.published(shell, page_kit.holding([rec["session"] for rec in records]))


def open_panel(page):
    page.goto("/m/cache/")
    return page.locator("#moxi-panel > moxi-cache-page")


def rows(panel):
    return panel.locator("moxi-ui-row")


def group(panel, kind):
    return panel.locator(f'section[data-group="{kind}"]')


def test_the_shell_hands_on_the_watchers_reading_and_the_date(shell):
    sent = page_kit.put_reading(shell, listed=None)
    data = page_kit.published(shell, lambda d: d["state"] == "up" and d["reading"] == sent)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["today"])
    page_kit.clear_reading(shell)
    page_kit.published(shell, lambda d: d["state"] == "up" and d["reading"] is None)


def test_the_page_keeps_the_contract(shell, ui_page, ui_component_check):
    write(shell, record(A), record(B, age=4000))
    check = ui_component_check(ui_page, "cache", "page")
    assert check.element == "moxi-cache-page"
    assert any(page_kit.CLOCKS in url for url in check.requests), check.requests


def test_the_page_draws_its_groups_rows_and_foot_and_no_id(shell, ui_page):
    page_kit.put_reading(shell)
    write(shell, record(A), record(B, age=4000))
    panel = open_panel(ui_page)
    expect(group(panel, "warm").get_by_text("1 warm", exact=True)).to_be_visible()
    expect(group(panel, "cold").get_by_text("1 cold", exact=True)).to_be_visible()
    expect(rows(panel)).to_have_count(2)
    expect(group(panel, "warm").locator("moxi-ui-row")).to_have_attribute("fact", re.compile(r"^warm until "))
    expect(group(panel, "cold").locator("moxi-ui-row")).to_have_attribute("fact", re.compile(r"^cold since "))
    for one in (group(panel, "warm"), group(panel, "cold")):
        expect(one.locator("moxi-ui-row")).to_have_attribute("rest", re.compile(r"^last request "))
        expect(one.get_by_role("button", name="Open")).to_be_visible()
    expect(panel.get_by_text(re.compile(r"^Last reading "))).to_be_visible()
    expect(panel.get_by_role("link", name="Cache settings")).to_be_visible()
    # no second heading, no chip, and no session's id anywhere on the page
    expect(panel.locator("h1")).to_have_count(0)
    expect(panel.locator("moxi-cache-chip")).to_have_count(0)
    for session in (A, B):
        expect(panel.get_by_text(session)).to_have_count(0)
        expect(panel.locator(f'moxi-ui-row[name*="{session}"]')).to_have_count(0)
    # a row opens and closes in place
    row = group(panel, "warm").locator("moxi-ui-row")
    row.get_by_role("button", name="Open").click()
    expect(row).to_have_attribute("open", "")
    row.get_by_role("button", name="Close").click()
    expect(row).not_to_have_attribute("open", "")


def test_a_fresh_reading_and_no_record_is_the_empty_state(shell, ui_page):
    page_kit.put_reading(shell)
    page_kit.published(shell, lambda d: d["state"] == "up" and d["reading"] is not None
                       and d["clocks"] == [])
    panel = open_panel(ui_page)
    expect(panel.get_by_text(EMPTY, exact=True)).to_be_visible()
    expect(panel.get_by_text(re.compile(r"^A session shows here"))).to_be_visible()
    expect(panel.get_by_text("Cache settings")).to_have_count(0)
    expect(panel.get_by_text("Watcher")).to_have_count(0)


def test_with_no_reading_and_no_record_the_watcher_row_says_how_to_start_it(shell, ui_page):
    page_kit.published(shell, page_kit.holding([]))
    panel = open_panel(ui_page)
    watcher = panel.locator('section[data-part="watcher"]')
    expect(watcher.get_by_text("No reading yet", exact=True)).to_be_visible()
    expect(watcher.get_by_role("button", name="Copy")).to_be_visible()
    expect(watcher.locator("code")).to_have_text(["moxi cache watch", "moxi install cache"])
    expect(panel.get_by_text(EMPTY)).to_have_count(0)
    expect(panel.get_by_text(re.compile(r"^A session shows here"))).to_be_visible()
    expect(rows(panel)).to_have_count(0)


def test_the_rows_follow_the_topic_and_a_stopped_watcher_is_said_once(shell, ui_page):
    write(shell, record(A, seen=120))
    panel = open_panel(ui_page)
    expect(rows(panel)).to_have_count(1)
    watcher = panel.locator('section[data-part="watcher"]')
    expect(watcher.get_by_text(re.compile(r"^Stopped at "))).to_be_visible()
    # A second session's record shows up as a row with no reload, and as its reading is
    # current, the watcher is no longer said to have stopped.
    put(shell, record(B))
    expect(rows(panel)).to_have_count(2, timeout=10_000)
    expect(group(panel, "warm").get_by_text("2 warm", exact=True)).to_be_visible()
    expect(watcher).to_have_count(0)
    # Its record removed, its row goes.
    page_kit.record_dir(shell).joinpath(f"{B}.json").unlink()
    expect(rows(panel)).to_have_count(1, timeout=10_000)
    expect(group(panel, "warm").get_by_text("1 warm", exact=True)).to_be_visible()


def test_an_opened_row_has_its_times_to_the_second_and_copies_its_id(shell, ui_contexts,
                                                                       ui_browser):
    clipboard = ui_browser.browser_type.name == "chromium"
    page = ui_contexts(**({"permissions": ["clipboard-read", "clipboard-write"]}
                          if clipboard else {})).pages[0]
    page_kit.put_reading(shell)
    write(shell, record(A, age=600), record(B, age=4000))
    panel = open_panel(page)
    warm = group(panel, "warm").locator("moxi-ui-row")
    warm.get_by_role("button", name="Open").click()
    det = warm.locator('[data-part="opened"]')
    expect(det.locator('[data-line="Last request"]')).to_have_text(
        re.compile(r"^Last request\s*\S*\d\d:\d\d:\d\d"))
    expect(det.locator('[data-line="Goes cold"]')).to_have_text(re.compile(
        r"^Goes cold\s*\S*\d\d:\d\d:\d\d.* · in (49|50) minutes · one hour after the last request$"))
    # sessions holds no transcript for it: the harness alone, and no link
    expect(det.locator('[data-line="session"]')).to_have_text(re.compile(r"^Session\s*Claude Code$"))
    expect(det.get_by_role("link", name="Sessions")).to_have_count(0)
    expect(det.locator('[data-line="Read"]')).to_have_count(0)
    cold = group(panel, "cold").locator("moxi-ui-row")
    cold.get_by_role("button", name="Open").click()
    expect(cold.locator('[data-line="Went cold"]')).to_have_text(
        re.compile(r" · 6 minutes ago · one hour after the last request$"))
    # both stay open, and the first closes on its own
    expect(warm).to_have_attribute("open", "")
    warm.get_by_role("button", name="Close").click()
    expect(warm.locator('[data-part="opened"]')).to_have_count(0)
    expect(cold).to_have_attribute("open", "")
    if clipboard:
        cold.get_by_role("button", name="Copy id").click()
        expect(cold.locator('[data-action="copy-id"]')).to_have_text("Copied")
        assert page.evaluate("navigator.clipboard.readText()") == B


@pytest.mark.parametrize("status", [500, 401])
def test_a_failed_read_shows_the_failure_line(shell, ui_page, status):
    ui_page.route("**" + page_kit.CLOCKS,
                  lambda route: route.fulfill(status=status, json={"error": "no"}))
    panel = open_panel(ui_page)
    expect(panel.get_by_text(
        f"The clock records can't be read: cache's page server answered {status}.",
        exact=True)).to_be_visible()
    expect(rows(panel)).to_have_count(0)
    expect(panel.get_by_text("Cache settings")).to_have_count(0)


def test_an_error_from_the_page_server_shows_its_detail(shell, ui_page):
    # A clock record holding a number JSON can't carry makes the page server's read fail.
    put(shell, record(A, ttl=float("inf")))
    page_kit.published(shell, lambda data: data["state"] == "error")
    panel = open_panel(ui_page)
    expect(panel.get_by_text(f"cache's page server hit an error: {UNEXPECTED}",
                             exact=True)).to_be_visible()
    expect(rows(panel)).to_have_count(0)


def test_the_page_and_the_chip_fit_a_phone(shell, ui_page, ui_width_check):
    page_kit.put_reading(shell)
    write(shell, record(A), record(B, age=4000), record("c" * 128, seen=120))
    panel = ui_page.locator("#moxi-panel > moxi-cache-page")
    ui_width_check(ui_page, module="cache")
    expect(rows(panel)).to_have_count(3)
    expect(group(panel, "cold").get_by_text("1 cold", exact=True)).to_be_visible()
    ui_width_check(ui_page)
    ui_width_check(ui_page, module="cache", name="chip", attrs={"session": A})
