"""The Projects page's list in a browser, on the two installs of projects_page_kit.py: which
projects there are and where each is (each a row with its key and its folder in `~`
form), which was last worked in (by the fixture's times, bad news first, and by key
without them), and the folder that doesn't exist, with `Change folder`. The page places
the fixture's `project-facts` in a hidden host with every project and the opened one's
key, and draws its report; a failed read draws the list without the fixture's parts.
"""

from __future__ import annotations

import json
import re
import sys
import time

import pytest
from playwright.sync_api import expect

import projects_page_kit as kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: The seven by key, as the list stands with no activity source.
BY_KEY = [row.key for row in kit.SEVEN]
#: The seven as the fixture's full report orders them: the rows with bad news first, then
#: the latest activity, newest first.
BY_ACTIVITY = ["atlas-api", "lumen-docs", "quill-mobile", "kestrel-web", "orbit-cli",
               "harbor-infra", "reviewer-home"]
#: Each project's folder as line one shows it.
WHERE = {row.key: f"~/{row.root}" for row in kit.SEVEN} | {
    "reviewer-home": "~/agents/reviewer · agent's home"}
#: Line two's words for the folder that doesn't exist.
MISSING = "Its folder doesn't exist"
MISSING_FIX = "point it at where the folder went, or remove it"


def open_list(page):
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    return panel


def rows(panel):
    return panel.locator('section[data-part="projects"] moxi-ui-row')


def row(panel, key: str):
    return panel.locator(f'moxi-ui-row[data-key="{key}"]')


def keys_in_order(panel) -> list[str]:
    return rows(panel).evaluate_all("rows => rows.map(row => row.dataset.key)")


def expect_order(panel, keys: list[str]) -> None:
    """Wait until the list's rows are `keys`, in that order."""
    deadline = time.monotonic() + 10
    while True:
        seen = keys_in_order(panel)
        if seen == keys or time.monotonic() > deadline:
            break
        panel.page.wait_for_timeout(50)
    assert seen == keys


def line_two(item):
    return item.locator(".l2")


@pytest.fixture
def fixture_page(fixture_shell, ui_contexts):
    kit.reset(fixture_shell)
    yield ui_contexts(fixture_shell).pages[0]
    kit.reset(fixture_shell)


# --- the small install -------------------------------------------------------------------

def test_the_small_install_lists_each_project_where_it_is_by_key(small_shell, ui_contexts):
    panel = open_list(ui_contexts(small_shell).pages[0])
    expect(rows(panel)).to_have_count(7)
    expect_order(panel, BY_KEY)
    expect(panel.locator('[data-line="count"]')).to_have_text("7 projects")
    expect(panel.get_by_role("button", name="Add a project")).to_be_visible()
    # Seven projects: no find field, no filter, no fold.
    expect(panel.locator('[data-part="find"]')).to_have_count(0)
    expect(panel.locator('[data-part="earlier"]')).to_have_count(0)
    # Nothing places facts: no module of this install declares them.
    expect(panel.locator('[data-part="facts"] > *')).to_have_count(0)
    for key in BY_KEY:
        item = row(panel, key)
        expect(item).to_have_attribute("name", key)
        expect(item).to_have_attribute("where", WHERE[key])
        # No activity source: no time at the right, and no claim that it went unused.
        assert item.get_attribute("fact") is None
        act = item.locator('[slot="acts-one"]')
        expect(act).to_have_text("Open")
        expect(act).to_be_visible()
        if key == kit.MISSING:
            continue
        # A quiet row is one line.
        expect(line_two(item)).to_be_hidden()
        expect(item.locator('[slot="acts"]')).to_have_count(0)
        for name in ("lead", "rest", "tail"):
            assert item.get_attribute(name) is None, (key, name)


def test_a_missing_root_says_so_on_line_two_and_keeps_its_place_by_key(
        small_shell, ui_contexts):
    panel = open_list(ui_contexts(small_shell).pages[0])
    item = row(panel, kit.MISSING)
    expect(item).to_have_attribute("lead", MISSING)
    expect(item).to_have_attribute("lead-heavy", "")
    expect(item).to_have_attribute("lead-more", MISSING_FIX)
    expect(line_two(item)).to_be_visible()
    expect(line_two(item)).to_contain_text(re.compile(f"{MISSING}\\s*·\\s*{MISSING_FIX}"))
    fix = item.locator('[slot="acts"]')
    expect(fix).to_have_text("Change folder")
    expect(fix).to_be_visible()
    # With no activity source the list stays by key: bad news doesn't move a row.
    expect_order(panel, BY_KEY)
    assert keys_in_order(panel).index(kit.MISSING) == BY_KEY.index(kit.MISSING)


def test_open_opens_the_row_once_and_close_closes_it(small_shell, ui_contexts):
    panel = open_list(ui_contexts(small_shell).pages[0])
    item = row(panel, "orbit-cli")
    act = item.locator('[slot="acts-one"]')
    toggles = item.evaluate("""row => {
      window.__toggles = 0;
      row.addEventListener("moxi-row-toggle", () => { window.__toggles += 1; });
    }""")
    assert toggles is None
    act.click()
    expect(item).to_have_attribute("open", "")
    expect(act).to_have_text("Close")
    expect(act).to_have_attribute("aria-expanded", "true")
    # A press of Open is not the row's: it sends no toggle of its own.
    assert panel.page.evaluate("() => window.__toggles") == 0
    act.click()
    expect(item).not_to_have_attribute("open", "")
    expect(act).to_have_text("Open")
    # The row's own line opens it too.
    item.get_by_role("button", name="orbit-cli").click()
    expect(item).to_have_attribute("open", "")
    assert panel.page.evaluate("() => window.__toggles") == 1


# --- the fixture install -----------------------------------------------------------------

def test_the_page_places_the_fixtures_facts_hidden_with_every_project_and_the_open_one(
        fixture_shell, fixture_page):
    panel = open_list(fixture_page)
    host = panel.locator('[data-part="facts"]')
    expect(host).to_be_hidden()
    facts = host.locator("moxi-fixture-project-facts")
    expect(facts).to_have_count(1)
    given = json.loads(facts.get_attribute("projects"))
    assert sorted(given, key=lambda item: item["key"]) == kit.given(fixture_shell)
    assert facts.get_attribute("open") is None
    # The fixture says nothing: no activity source, so the list is by key.
    expect_order(panel, BY_KEY)
    row(panel, "kestrel-web").locator('[slot="acts-one"]').click()
    expect(facts).to_have_attribute("open", "kestrel-web")
    row(panel, "kestrel-web").locator('[slot="acts-one"]').click()
    expect(row(panel, "kestrel-web")).not_to_have_attribute("open", "")
    expect(facts).not_to_have_attribute("open", "kestrel-web")
    assert facts.get_attribute("open") is None


def test_the_fixtures_times_order_the_list_with_bad_news_first(fixture_shell, fixture_page):
    now = time.time()
    report = kit.full_report(fixture_shell, now)
    kit.say(fixture_shell, report)
    panel = open_list(fixture_page)
    expect_order(panel, BY_ACTIVITY)
    for key, ago in kit.AGO_S.items():
        when = fixture_page.evaluate("""at => new Date(at * 1000).toLocaleTimeString(
          [], {hour: "2-digit", minute: "2-digit"})""", report["projects"][key]["at"])
        expect(row(panel, key)).to_have_attribute("fact", f"last active {when}")
    # The fixture is a source of all activity and gave quill-mobile no time.
    expect(row(panel, kit.MISSING)).to_have_attribute("fact", "not used yet")

    atlas = row(panel, "atlas-api")
    expect(atlas).to_have_attribute("lead", "queue-worker failed at 18:31")
    expect(atlas).to_have_attribute("lead-heavy", "")
    expect(atlas).to_have_attribute("tail", "2 running · $8.49 today")
    lumen = row(panel, "lumen-docs")
    expect(lumen).to_have_attribute("lead", "a git hook broke at 17:55")
    expect(lumen).to_have_attribute("rest", "docs-preview is stopped")
    expect(lumen).to_have_attribute("rest-strong", "")
    expect(lumen).to_have_attribute("tail", "1 active recently · $0.36 today")
    orbit = row(panel, "orbit-cli")
    expect(orbit).to_have_attribute("rest", "dep-audit's last run failed at 18:00")
    expect(orbit).to_have_attribute("tail", "1 active recently · $1.55 today")
    assert orbit.get_attribute("lead") is None
    expect(row(panel, "harbor-infra")).to_have_attribute("tail", "1 running · $3.30 today")
    quill = row(panel, kit.MISSING)
    expect(quill).to_have_attribute("lead", MISSING)
    expect(quill.locator('[slot="acts"]')).to_have_text("Change folder")
    for item in (atlas, lumen, orbit, quill):
        expect(line_two(item)).to_be_visible()


@pytest.mark.parametrize("route", ["open/kestrel-web", "edit/kestrel-web", "remove/harbor-infra"])
def test_a_row_opened_by_its_address_takes_its_place_once_the_first_order_applies(
        fixture_shell, fixture_page, route):
    now = time.time()
    kit.say(fixture_shell, kit.full_report(fixture_shell, now))
    # The page's clock stands still, so its first order is let go by the fixture's report
    # and never by the hold's time running out, however slow the machine.
    fixture_page.clock.set_fixed_time(now)
    fixture_page.goto(f"/m/projects/{route}")
    panel = fixture_page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    expect(row(panel, route.split("/")[1])).to_have_attribute("open", "")
    expect_order(panel, BY_ACTIVITY)


def test_a_failed_read_draws_the_list_without_the_fixtures_parts(fixture_shell, fixture_page):
    kit.say(fixture_shell, kit.full_report(fixture_shell))
    panel = open_list(fixture_page)
    expect_order(panel, BY_ACTIVITY)
    kit.fail(fixture_shell)
    # The fixture reports null: no activity source, so the list is by key again, and only
    # projects' own words are left.
    expect_order(panel, BY_KEY)
    for key in BY_KEY:
        item = row(panel, key)
        expect(item).not_to_have_attribute("fact", "not used yet")
        assert item.get_attribute("fact") is None, key
        if key != kit.MISSING:
            assert item.get_attribute("tail") is None, key
            expect(line_two(item)).to_be_hidden()
    expect(row(panel, kit.MISSING)).to_have_attribute("lead", MISSING)
    kit.fail(fixture_shell, enabled=False)
    expect_order(panel, BY_ACTIVITY)
    expect(row(panel, "atlas-api")).to_have_attribute("tail", "2 running · $8.49 today")
