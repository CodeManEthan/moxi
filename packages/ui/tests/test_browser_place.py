"""The shell's one more try at a component whose script did not load, in a browser: the
module's panel, when a fetch of the script then answers fine.

A script's first fetch is failed on purpose, by a route on the browser context that
answers it 503 and lets every later fetch through, so the path runs in every engine and
on every load, whatever the machine's timing."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(90)]

PANEL = "#moxi-panel > moxi-fixture-page"
MESSAGE = "#moxi-panel .moxi-message"


def _kind(request) -> str:
    """`import` for the script's load as a module, `check` for the shell's fetch of it."""
    return "import" if request.resource_type == "script" else "check"


def _fail_first(context, path: str) -> list[tuple[str, str]]:
    """Answers the next fetch of `path` in `context` with a 503 and lets every later one
    through. Gives what each fetch was and got, `(import or check, failed or passed)`, in
    order."""
    seen: list[tuple[str, str]] = []

    def answer(route):
        if not seen:
            seen.append((_kind(route.request), "failed"))
            route.fulfill(status=503, body="")
        else:
            seen.append((_kind(route.request), "passed"))
            route.continue_()

    context.route(re.compile(r"^[^?#]*" + re.escape(path) + r"(\?.*)?$"), answer)
    return seen


def _errors(page) -> list[str]:
    said: list[str] = []
    page.on("console", lambda msg: said.append(msg.text) if msg.type == "error" else None)
    return said


def test_a_panel_whose_first_script_fetch_fails_is_placed_on_one_more_try(ui_page):
    fetched = _fail_first(ui_page.context, "/m/fixture/page.js")
    said = _errors(ui_page)
    ui_page.locator("#moxi-menu a[data-module=fixture]").click()
    panel = ui_page.locator(PANEL)
    expect(panel.get_by_test_id("route")).to_have_text("Route: ")
    expect(ui_page.locator(MESSAGE)).to_have_count(0)
    # The import that failed, the shell's check of the script, and the one more import.
    assert fetched == [("import", "failed"), ("check", "passed"), ("import", "passed")]
    assert any("moxi: fixture's page could not load" in text for text in said), said
