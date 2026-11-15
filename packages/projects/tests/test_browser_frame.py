"""The Projects page's frame in a browser, on the two installs of projects_page_kit.py: each
address opens what it names, a key matched ignoring case, and one that is no project
opens the list with a line saying so; opening and closing rewrite the address without a
new history entry, so Back leaves the page; the page draws no heading of its own, since
the shell's top line names it; the list's states (nothing, then `Reading…`; a page
server that isn't answering, with the last list kept dim and no acts; a store that
refuses, with its reason and fix and no rows); and the fixture's reads, which stop when
another page is placed and start again with a placement anew, which opens what the
address names.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

import projects_page_kit as kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: The event projects' page and components read their routes again on.
CHANGED = "moxi-projects-changed"
NOT_ANSWERING = "Projects' page server isn't answering."


def panel_of(page):
    return page.locator("#moxi-panel > moxi-projects-page")


def list_part(page):
    return panel_of(page).locator('section[data-part="list"]')


def row(page, key: str):
    return panel_of(page).locator(f'moxi-ui-row[data-key="{key}"]')


def open_rows(page):
    return panel_of(page).locator("moxi-ui-row[open]")


def path_of(page) -> str:
    return page.evaluate("() => location.pathname")


def expect_path(page, path: str) -> None:
    deadline = time.monotonic() + 10
    while path_of(page) != path and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert path_of(page) == path


def changed(page) -> None:
    """Sends the event after which every projects component reads again."""
    page.evaluate("name => document.dispatchEvent(new Event(name))", CHANGED)


def shell_navigate(page, module: str, route: str = "") -> None:
    """Shows `module`'s page in the shell's document, as a link on a page does."""
    page.evaluate("""async ([module, route]) => {
      const client = await import("/m/ui/client.js");
      client.navigate(module, route);
    }""", [module, route])


def count_writes(page) -> None:
    """From now on, `window.__writes` counts the address writes the page makes."""
    page.evaluate("""() => {
      window.__writes = 0;
      const replace = history.replaceState.bind(history);
      history.replaceState = (...args) => { window.__writes += 1; return replace(...args); };
    }""")


@pytest.fixture
def small_page(small_shell, ui_contexts):
    return ui_contexts(small_shell).pages[0]


@pytest.fixture
def fixture_page(fixture_shell, ui_contexts):
    kit.reset(fixture_shell)
    yield ui_contexts(fixture_shell).pages[0]
    kit.reset(fixture_shell)


# --- addresses -----------------------------------------------------------------------------

@pytest.mark.parametrize(("route", "opened", "path"), [
    ("", None, "/m/projects/"),
    ("open/reviewer-home", "reviewer-home", "/m/projects/open/reviewer-home"),
    ("open/ORBIT-CLI", "orbit-cli", "/m/projects/open/orbit-cli"),
    ("edit/quill-mobile", "quill-mobile", "/m/projects/edit/quill-mobile"),
    ("remove/harbor-infra", "harbor-infra", "/m/projects/remove/harbor-infra"),
    ("add", None, "/m/projects/add"),
])
def test_each_address_opens_what_it_names(small_page, route, opened, path):
    small_page.goto(f"/m/projects/{route}")
    expect(list_part(small_page)).to_have_attribute("data-state", "up")
    if opened is None:
        expect(open_rows(small_page)).to_have_count(0)
    else:
        item = row(small_page, opened)
        expect(item).to_have_attribute("open", "")
        expect(open_rows(small_page)).to_have_count(1)
        expect(item.locator('[slot="acts-one"]')).to_have_text("Close")
        expect(item).to_be_in_viewport()
    # The address says what is open, the stored key's case included.
    expect_path(small_page, path)
    expect(list_part(small_page).locator('[data-line="unknown"]')).to_have_count(0)
    if route.startswith("edit/"):
        # Until the Edit panel, the opened project's own folder field takes the focus.
        focused = panel_of(small_page).evaluate("page => page.shadowRoot.activeElement?.name ?? null")
        assert focused == "root"
    aside = panel_of(small_page).locator('moxi-projects-panel [data-part="panel"]')
    if route == "add":
        # The Add panel, empty, its folder field focused.
        expect(aside).to_be_visible()
        expect(aside.get_by_label("Folder", exact=True)).to_be_focused()
        expect(aside.get_by_label("Folder", exact=True)).to_have_value("")
    else:
        expect(aside).to_have_count(0)


def test_an_address_that_names_no_project_opens_the_list_and_says_so(small_page):
    small_page.goto("/m/projects/open/nope")
    expect(list_part(small_page)).to_have_attribute("data-state", "up")
    line = list_part(small_page).locator('[data-line="unknown"]')
    expect(line).to_have_text("No project is named nope.")
    expect(open_rows(small_page)).to_have_count(0)
    expect_path(small_page, "/m/projects/")
    # Opening a row takes the line away.
    row(small_page, "orbit-cli").locator('[slot="acts-one"]').click()
    expect(line).to_have_count(0)


def test_open_and_close_rewrite_the_address_and_back_leaves_the_page(small_page):
    # The context opens on Home; the shell shows the page in the same document.
    assert path_of(small_page) == "/m/ui/"
    shell_navigate(small_page, "projects")
    expect(list_part(small_page)).to_have_attribute("data-state", "up")
    entries = small_page.evaluate("() => history.length")
    count_writes(small_page)

    act = row(small_page, "orbit-cli").locator('[slot="acts-one"]')
    act.click()
    expect(row(small_page, "orbit-cli")).to_have_attribute("open", "")
    expect_path(small_page, "/m/projects/open/orbit-cli")
    # One press, one address write.
    assert small_page.evaluate("() => window.__writes") == 1
    act.click()
    expect_path(small_page, "/m/projects/")
    assert small_page.evaluate("() => window.__writes") == 2

    # `Change folder` opens the project for its edit; `Add a project` closes it.
    row(small_page, kit.MISSING).locator('[slot="acts"]').click()
    expect(row(small_page, kit.MISSING)).to_have_attribute("open", "")
    expect_path(small_page, f"/m/projects/edit/{kit.MISSING}")
    panel_of(small_page).get_by_role("button", name="Add a project").click()
    expect(open_rows(small_page)).to_have_count(0)
    expect_path(small_page, "/m/projects/add")
    # The panel's `Close` closes the add; Escape too.
    panel_of(small_page).locator('moxi-projects-panel [data-action="close"]').click()
    expect_path(small_page, "/m/projects/")
    expect(panel_of(small_page).locator("moxi-projects-panel")).to_have_count(0)
    panel_of(small_page).get_by_role("button", name="Add a project").click()
    expect_path(small_page, "/m/projects/add")
    small_page.keyboard.press("Escape")
    expect_path(small_page, "/m/projects/")
    panel_of(small_page).get_by_role("button", name="Add a project").click()
    expect_path(small_page, "/m/projects/add")
    row(small_page, "kestrel-web").get_by_role("button", name="kestrel-web").click()
    expect_path(small_page, "/m/projects/open/kestrel-web")

    # No entry was added, so Back leaves the page.
    assert small_page.evaluate("() => history.length") == entries
    small_page.go_back()
    expect_path(small_page, "/m/ui/")
    expect(panel_of(small_page)).to_have_count(0)


def test_the_page_draws_no_heading_of_its_own(small_page):
    small_page.goto("/m/projects/")
    expect(list_part(small_page)).to_have_attribute("data-state", "up")
    panel = panel_of(small_page)
    for gone in ("h1", "h2", ".lede", 'section[data-part="where"]', '[data-action="read"]'):
        expect(panel.locator(gone)).to_have_count(0)
    expect(panel.get_by_role("button", name="Read again")).to_have_count(0)
    # The shell's top line names the page.
    expect(small_page.locator("#moxi-pages-button")).to_contain_text("Projects")


# --- states --------------------------------------------------------------------------------

def test_the_first_read_shows_reading_until_it_answers(small_page):
    held: list = []
    small_page.route("**/m/projects/api/projects", lambda route: held.append(route))
    small_page.goto("/m/projects/")
    expect(list_part(small_page)).to_have_attribute("data-state", "loading")
    reading = list_part(small_page).locator('[data-line="reading"]')
    expect(reading).to_have_text("Reading…")
    expect(panel_of(small_page).locator("moxi-ui-row")).to_have_count(0)
    expect(panel_of(small_page).get_by_role("button", name="Add a project")).to_have_count(0)
    for route in held:
        route.continue_()
    small_page.unroute("**/m/projects/api/projects")
    expect(list_part(small_page)).to_have_attribute("data-state", "up")
    expect(reading).to_have_count(0)
    expect(panel_of(small_page).locator("moxi-ui-row")).to_have_count(7)


def test_a_page_server_that_isnt_answering_keeps_the_last_list_dim_with_no_acts(small_page):
    small_page.goto("/m/projects/open/orbit-cli")
    expect(row(small_page, "orbit-cli")).to_have_attribute("open", "")
    small_page.route("**/m/projects/api/projects", lambda route: route.fulfill(status=503, body=""))
    changed(small_page)
    # A 503 is read again three times, 1 s apart, before the page says so.
    expect(list_part(small_page)).to_have_attribute("data-state", "down", timeout=15_000)
    first = list_part(small_page).locator('[data-line="failed"]')
    expect(first).to_have_text(NOT_ANSWERING)
    panel = panel_of(small_page)
    expect(panel.locator("moxi-ui-row")).to_have_count(7)
    expect(panel.locator('.grps.kept')).to_have_count(1)
    for gone in ('[slot="acts-one"]', '[slot="acts"]', '[data-action="add"]', "moxi-ui-row[open]",
                 "moxi-ui-row[toggles]"):
        expect(panel.locator(gone)).to_have_count(0)
    expect(panel.locator('moxi-projects-panel [data-part="panel"]')).to_have_count(0)
    # Every word is dim, the rows' own included.
    dim = panel.evaluate("""page => {
      const kept = page.shadowRoot.querySelector(".grps.kept");
      const style = getComputedStyle(kept);
      return [style.getPropertyValue("--moxi-text").trim(), style.getPropertyValue("--moxi-text-dim").trim()];
    }""")
    assert dim[0] == dim[1] and dim[0] != "", dim

    # Answering again, the list is up with its acts, and the project opened again.
    small_page.unroute("**/m/projects/api/projects")
    changed(small_page)
    expect(list_part(small_page)).to_have_attribute("data-state", "up")
    expect(first).to_have_count(0)
    expect(row(small_page, "orbit-cli")).to_have_attribute("open", "")
    expect(panel.locator('[slot="acts-one"]')).to_have_count(7)


def test_a_store_that_refuses_shows_its_reason_and_fix_and_no_rows(small_shell, small_page):
    from moxi_projects import store

    where = Path(store.path(small_shell.env))
    kept = {path: path.read_bytes() for path in where.parent.glob(f"{where.name}*")}
    try:
        for path in kept:
            path.unlink()
        where.write_bytes(b"this is not a projects store\n" * 64)
        small_page.goto("/m/projects/")
        expect(list_part(small_page)).to_have_attribute("data-state", "refused")
        first = list_part(small_page).locator('[data-line="failed"]')
        expect(first).to_contain_text(str(where))
        expect(first).to_contain_text("fix:")
        panel = panel_of(small_page)
        expect(panel.locator("moxi-ui-row")).to_have_count(0)
        expect(panel.locator('[data-action="add"]')).to_have_count(0)
        expect(panel.locator("moxi-projects-panel")).to_have_count(0)
    finally:
        for path in where.parent.glob(f"{where.name}*"):
            path.unlink()
        kit.seed(small_shell, notes=False)


# --- the facts components on another page -----------------------------------------------------

def test_the_fixtures_reads_stop_on_another_page_and_a_placement_anew_opens_the_address(
        fixture_shell, fixture_page):
    shell_navigate(fixture_page, "projects")
    expect(list_part(fixture_page)).to_have_attribute("data-state", "up")
    facts = panel_of(fixture_page).locator('[data-part="facts"] moxi-fixture-project-facts')
    expect(facts).to_have_count(1)
    row(fixture_page, "kestrel-web").locator('[slot="acts-one"]').click()
    expect_path(fixture_page, "/m/projects/open/kestrel-web")
    # The fixture reads every 300 ms while it is placed.
    start = kit.reads(fixture_shell)
    deadline = time.monotonic() + 10
    while kit.reads(fixture_shell) < start + 2 and time.monotonic() < deadline:
        fixture_page.wait_for_timeout(100)
    assert kit.reads(fixture_shell) >= start + 2

    shell_navigate(fixture_page, "fixture", "elsewhere")
    expect(fixture_page.locator("#moxi-panel > moxi-fixture-page").get_by_test_id("route")).to_be_visible()
    expect(panel_of(fixture_page)).to_have_count(0)
    fixture_page.wait_for_timeout(400)      # a read already on its way lands
    stopped = kit.reads(fixture_shell)
    fixture_page.wait_for_timeout(1500)     # five of the fixture's beats
    assert kit.reads(fixture_shell) == stopped

    # Back places the page anew: it reads everything again and opens what the address says.
    lists = []
    fixture_page.on("request", lambda request: lists.append(request.url)
                    if request.url.endswith("/m/projects/api/projects") else None)
    fixture_page.go_back()
    expect_path(fixture_page, "/m/projects/open/kestrel-web")
    expect(row(fixture_page, "kestrel-web")).to_have_attribute("open", "")
    expect(facts).to_have_attribute("open", "kestrel-web")
    assert lists, "the list was not read again"
    deadline = time.monotonic() + 10
    while kit.reads(fixture_shell) <= stopped and time.monotonic() < deadline:
        fixture_page.wait_for_timeout(100)
    assert kit.reads(fixture_shell) > stopped
