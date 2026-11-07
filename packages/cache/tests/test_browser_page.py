"""cache's page in a browser, through a real shell that starts cache's real page server:
the contract check, the list of sessions with a chip each, the empty state, the stopped
watcher, rows that follow the topic, the failure and error lines, and the 360 px width
check on the page and the chip."""

from __future__ import annotations

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
EMPTY = ("No live Claude Code session has a cache record. The watcher writes one for each: "
         "moxi cache watch, or moxi install cache.")
STOPPED = "The cache watcher has stopped, so these readings may be old."
UNEXPECTED = "cache's page server hit an unexpected error (ValueError); its log has the details"


@pytest.fixture
def shell(ui_shell):
    """The module's shell, with no clock record left by an earlier test."""
    page_kit.clear(ui_shell)
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
    return panel.locator("li[data-session]")


def row_chip(panel, session):
    return panel.locator(f'li[data-session="{session}"] span.cell moxi-cache-chip span.chip')


def test_the_page_keeps_the_contract(shell, ui_page, ui_component_check):
    write(shell, record(A), record(B, age=4000))
    check = ui_component_check(ui_page, "cache", "page")
    assert check.element == "moxi-cache-page"
    assert any(page_kit.CLOCKS in url for url in check.requests), check.requests


def test_the_page_lists_each_record_with_its_harness_and_a_chip(shell, ui_page):
    write(shell, record(A), record(B, age=4000, harness="claude"))
    panel = open_panel(ui_page)
    expect(panel.locator("h1")).to_have_text("Cache")
    expect(panel.locator("div[data-state]")).to_have_attribute("data-state", "up")
    expect(rows(panel)).to_have_count(2)
    for session, shows in ((A, "warm"), (B, "cold")):
        row = panel.locator(f'li[data-session="{session}"]')
        expect(row.locator("code")).to_have_text(session)
        expect(row.locator(".harness")).to_have_text("claude")
        expect(row_chip(panel, session)).to_have_attribute("data-shows", shows)
    expect(panel.locator("p.warn")).to_have_count(0)


def test_with_no_record_the_page_says_how_to_get_one(shell, ui_page):
    page_kit.published(shell, page_kit.holding([]))
    panel = open_panel(ui_page)
    state = panel.locator("div[data-state]")
    expect(state).to_have_attribute("data-state", "empty")
    expect(state).to_have_text(EMPTY)
    expect(rows(panel)).to_have_count(0)


def test_the_rows_follow_the_topic_and_a_stopped_watcher_is_said_once(shell, ui_page):
    write(shell, record(A, seen=120))
    panel = open_panel(ui_page)
    expect(rows(panel)).to_have_count(1)
    expect(row_chip(panel, A)).to_have_attribute("data-dim", "true")
    expect(panel.locator("p.warn")).to_have_text(STOPPED)
    # A second session's record shows up as a row with no reload, and as its reading is
    # current, the watcher is no longer said to have stopped.
    put(shell, record(B))
    expect(rows(panel)).to_have_count(2, timeout=10_000)
    expect(row_chip(panel, B)).to_have_attribute("data-dim", "false")
    expect(panel.locator("p.warn")).to_have_count(0)
    # Its record removed, its row goes.
    page_kit.record_dir(shell).joinpath(f"{B}.json").unlink()
    expect(rows(panel)).to_have_count(1, timeout=10_000)
    expect(panel.locator(f'li[data-session="{A}"]')).to_have_count(1)


@pytest.mark.parametrize("status", [500, 401])
def test_a_failed_read_shows_the_failure_line(shell, ui_page, status):
    ui_page.route("**" + page_kit.CLOCKS,
                  lambda route: route.fulfill(status=status, json={"error": "no"}))
    panel = open_panel(ui_page)
    state = panel.locator("div[data-state]")
    expect(state).to_have_attribute("data-state", "failed")
    expect(state).to_have_text(
        f"The clock records can't be read: cache's page server answered {status}.")
    expect(rows(panel)).to_have_count(0)


def test_an_error_from_the_page_server_shows_its_detail(shell, ui_page):
    # A clock record holding a number JSON can't carry makes the page server's read fail.
    put(shell, record(A, ttl=float("inf")))
    page_kit.published(shell, lambda data: data["state"] == "error")
    panel = open_panel(ui_page)
    state = panel.locator("div[data-state]")
    expect(state).to_have_attribute("data-state", "error")
    expect(state).to_have_text(f"cache's page server hit an error: {UNEXPECTED}")
    expect(rows(panel)).to_have_count(0)


def test_the_page_and_the_chip_fit_a_phone(shell, ui_page, ui_width_check):
    write(shell, record(A), record(B, age=4000), record("c" * 128, seen=120))
    panel = ui_page.locator("#moxi-panel > moxi-cache-page")
    ui_width_check(ui_page, module="cache")
    expect(rows(panel)).to_have_count(3)
    expect(row_chip(panel, B)).to_have_attribute("data-shows", "cold")
    ui_width_check(ui_page)
    ui_width_check(ui_page, module="cache", name="chip", attrs={"session": A})
