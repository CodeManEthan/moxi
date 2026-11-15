"""The Projects page's folders that are not projects in a browser, on the fixture install of
projects_page_kit.py: the fixture's loose folder under the list as one line, `Not a
project`, with its parts, its time and `Add`, which opens the Add panel with the folder
and the name filled and adds it; `Worked in this week` under the empty list; five rows,
then how many more with `Show`, which shows every row held; and a folder the fixture
dates without being an activity source, which shows nothing.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import time

import pytest
from playwright.sync_api import expect

import projects_page_kit as kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

LAST_ACTIVE = re.compile(r"^last active \w+ \d")


def open_list(page, state: str = "up"):
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", state)
    return panel


def loose_part(panel):
    return panel.locator('section[data-part="folders"]')


def loose_rows(panel):
    return loose_part(panel).locator("moxi-ui-row")


def shown_loose(panel) -> list[str]:
    return loose_rows(panel).evaluate_all("rows => rows.map(row => row.dataset.folder)")


def expect_loose(panel, paths: list[str]) -> None:
    """Wait until the folder rows are those of `paths`, in that order."""
    deadline = time.monotonic() + 10
    while True:
        seen = shown_loose(panel)
        if seen == paths or time.monotonic() > deadline:
            break
        panel.page.wait_for_timeout(50)
    assert seen == paths


def path_of(page) -> str:
    return page.evaluate("() => location.pathname")


def expect_path(page, path: str) -> None:
    deadline = time.monotonic() + 10
    while path_of(page) != path and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert path_of(page) == path


def saved(shell, key: str) -> dict | None:
    """What the shell's store holds for `key`, read without writing."""
    from moxi_projects import store

    conn = sqlite3.connect(f"file:{store.path(shell.env)}?mode=ro", uri=True)
    try:
        found = conn.execute("SELECT key, root, kind FROM projects WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return None if found is None else dict(zip(("key", "root", "kind"), found))


def loose(shell) -> str:
    return str(kit.home(shell) / kit.LOOSE)


@pytest.fixture
def fixture_page(fixture_shell, ui_contexts):
    kit.reset(fixture_shell)
    yield ui_contexts(fixture_shell).pages[0]
    kit.reset(fixture_shell)
    kit.seed(fixture_shell)


def test_a_place_worked_in_is_one_line_under_the_list_and_its_add_makes_it_a_project(
        fixture_shell, fixture_page):
    kit.say(fixture_shell, kit.full_report(fixture_shell))
    panel = open_list(fixture_page)
    group = loose_part(panel)
    expect(group.locator('[data-line="folders"]')).to_have_text("Not a project")
    expect_loose(panel, [loose(fixture_shell)])
    assert panel.locator(".grps > section").evaluate_all(
        "parts => parts.map(part => part.dataset.part)") == ["projects", "folders"]
    item = loose_rows(panel).first
    expect(item).to_have_attribute("name", "~/code/finch-scripts")
    expect(item).to_have_attribute("where", "2 sessions this week · $0.82")
    expect(item).to_have_attribute("fact", LAST_ACTIVE)
    # One line, which opens nothing; `Add` at its right end.
    expect(item.locator(".l2")).to_be_hidden()
    assert item.get_attribute("toggles") is None
    act = item.locator('[slot="acts-one"]')
    expect(act).to_have_text("Add")
    expect(act).to_be_visible()
    expect(group.locator('[data-line="more-folders"]')).to_have_count(0)

    act.click()
    # The address is the add's: what is filled lives only in the panel.
    expect_path(fixture_page, "/m/projects/add")
    aside = panel.locator('moxi-projects-panel [data-part="panel"]')
    expect(aside).to_be_visible()
    expect(aside.get_by_label("Folder", exact=True)).to_have_value("~/code/finch-scripts")
    expect(aside.locator('[data-line="folder"]')).to_have_text("The folder exists.")
    expect(aside.get_by_label("Name", exact=True)).to_have_value("finch-scripts")
    aside.get_by_role("button", name="Add project").click()
    said = aside.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-outcome", "done")
    expect(said).to_have_text("Added finch-scripts at ~/code/finch-scripts.")
    assert saved(fixture_shell, "finch-scripts") == {"key": "finch-scripts", "root": loose(fixture_shell),
                                                      "kind": "project"}
    expect(panel.locator('moxi-ui-row[data-key="finch-scripts"]')).to_have_count(1)


def test_with_no_project_the_places_worked_in_follow_the_empty_sentence(fixture_shell, fixture_page):
    kit.seed(fixture_shell, ())
    report = kit.full_report(fixture_shell)
    report["projects"] = {}
    kit.say(fixture_shell, report)
    panel = open_list(fixture_page, "empty")
    expect(panel.locator('[data-line="sentence"]')).to_have_text(
        "A project is a folder with a short name. Its sessions are then gathered under that name.")
    expect(loose_part(panel).locator('[data-line="folders"]')).to_have_text("Worked in this week")
    expect_loose(panel, [loose(fixture_shell)])
    assert panel.locator('[data-part="empty"] ~ .grps [data-part="folders"]').count() == 1

    loose_rows(panel).first.get_by_role("button", name="Add").click()
    aside = panel.locator('moxi-projects-panel [data-part="panel"]')
    expect(aside.get_by_label("Folder", exact=True)).to_have_value("~/code/finch-scripts")
    expect(aside.get_by_label("Name", exact=True)).to_have_value("finch-scripts")


def test_five_places_worked_in_then_how_many_more_and_show_shows_every_one_held(fixture_shell, fixture_page):
    now = int(time.time())
    held = [str(kit.home(fixture_shell) / "work" / f"tool-{n}") for n in range(7)]
    report = kit.full_report(fixture_shell)
    # Newest first: tool-0 an hour ago, each next an hour before; tool-5 and tool-6 at the
    # same time, so by folder.
    ago = [3600, 7200, 10800, 14400, 18000, 21600, 21600]
    report["folders"] = [{"folder": path, "at": now - seconds, "parts": [{"text": "1 session this week"}]}
                         for path, seconds in zip(reversed(held), reversed(ago))]
    report["more_folders"] = 3
    kit.say(fixture_shell, report)
    panel = open_list(fixture_page)
    expect_loose(panel, held[:5])
    more = loose_part(panel).locator('[data-line="more-folders"]')
    expect(more).to_have_text("5 more folders")
    show = loose_part(panel).get_by_role("button", name="Show")
    show.click()
    expect_loose(panel, held)
    # Those no report listed are counted, with nothing more to show.
    expect(more).to_have_text("3 more folders")
    expect(show).to_have_count(0)


def test_a_place_dated_by_a_report_that_is_no_activity_source_shows_nothing(fixture_shell, fixture_page):
    report = kit.full_report(fixture_shell)
    del report["activity"]
    kit.say(fixture_shell, report)
    panel = open_list(fixture_page)
    # The fixture's parts are drawn, so its report was read.
    expect(panel.locator('moxi-ui-row[data-key="atlas-api"]')).to_have_attribute("tail", re.compile(r"\$8\.49 today"))
    expect(loose_part(panel)).to_have_count(0)
