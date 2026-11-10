"""The shell's one more try at a component whose script did not load, in a browser: the
module's panel and a home block, each when a fetch of the script then answers fine and
when it names a failure.

A script's first fetch is failed on purpose, by a route on the browser context that
answers it 503 and lets every later fetch through, so the path runs in every engine and
on every load, whatever the machine's timing. A named failure is a real one: the fixture's
page server set to exit at its start, so the shell's proxy answers each script fetch with
what went wrong."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

import fixture_kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(90)]

PANEL = "#moxi-panel > moxi-fixture-page"
MESSAGE = "#moxi-panel .moxi-message"
BLOCK = "#moxi-panel > moxi-ui-page #home section[data-block='fixture.block']"
#: How long a script fetch the proxy answers with a page server's failed start may take:
#: the start itself, up to its exit.
START_MS = 20_000


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


def _fetches(page, path: str) -> list[str]:
    """What each request `page` sends for `path` from now on was: `import` or `check`."""
    seen: list[str] = []
    page.on("request", lambda request: seen.append(_kind(request))
            if request.url.split("?")[0].endswith(path) else None)
    return seen


def _errors(page) -> list[str]:
    said: list[str] = []
    page.on("console", lambda msg: said.append(msg.text) if msg.type == "error" else None)
    return said


def test_a_panel_whose_first_script_fetch_fails_is_placed_on_one_more_try(ui_page):
    fetched = _fail_first(ui_page.context, "/m/fixture/page.js")
    said = _errors(ui_page)
    ui_page.locator("#moxi-pages-button").click()
    ui_page.locator("#moxi-pages a[data-module=fixture]").click()
    panel = ui_page.locator(PANEL)
    expect(panel.get_by_test_id("route")).to_have_text("Route: ")
    expect(ui_page.locator(MESSAGE)).to_have_count(0)
    # The import that failed, the shell's check of the script, and the one more import.
    assert fetched == [("import", "failed"), ("check", "passed"), ("import", "passed")]
    assert any("moxi: fixture's page could not load" in text for text in said), said


def test_a_home_block_whose_first_script_fetch_fails_is_placed_on_one_more_try(ui_page):
    fetched = _fail_first(ui_page.context, "/m/fixture/block.js")
    said = _errors(ui_page)
    ui_page.goto("/m/ui/modules")
    block = ui_page.locator(BLOCK)
    expect(block.locator("moxi-fixture-block").get_by_test_id("count")).to_have_text(
        re.compile(r"^Count: \d+$"))
    expect(block.locator(".warn")).to_have_count(0)
    assert fetched == [("import", "failed"), ("check", "passed"), ("import", "passed")]
    assert any("moxi: fixture's block could not load" in text for text in said), said


@pytest.fixture(scope="module")
def failing_shell(ui_shells, ui_shell_options):
    """A shell of this module's own whose fixture page server exits at its start, set
    before anything has started it."""
    shell = ui_shells(**ui_shell_options)
    fixture_kit.fail_start(shell)
    return shell


def test_a_home_block_whose_script_names_a_failure_shows_its_state_with_no_retry(
        failing_shell, ui_contexts):
    page = ui_contexts(failing_shell).pages[0]
    fetches = _fetches(page, "/m/fixture/block.js")
    page.goto("/m/ui/modules")
    block = page.locator(BLOCK)
    expect(block.locator(".warn")).to_have_text("fixture's block could not be shown.",
                                                timeout=START_MS)
    expect(block.locator("moxi-fixture-block")).to_have_count(0)
    # The import and the check that named the failure; no second import.
    page.evaluate("1")
    assert fetches == ["import", "check"]
    # The servers block beside it is placed as usual.
    expect(page.locator("#home section[data-block='ui.servers'] moxi-ui-servers")).to_be_attached()


def test_a_panel_whose_script_names_a_failure_shows_it_with_no_retry(failing_shell,
                                                                     ui_contexts):
    page = ui_contexts(failing_shell).pages[0]
    fetches = _fetches(page, "/m/fixture/page.js")
    page.locator("#moxi-pages-button").click()
    page.locator("#moxi-pages a[data-module=fixture]").click()
    message = page.locator(MESSAGE)
    expect(message.locator("h1")).to_have_text("fixture's page server did not start",
                                               timeout=START_MS)
    expect(message).to_contain_text("It exited with code 3.")
    expect(page.locator(PANEL)).to_have_count(0)
    page.evaluate("1")
    assert fetches == ["import", "check"]
