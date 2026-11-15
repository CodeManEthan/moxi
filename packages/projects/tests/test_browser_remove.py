"""The Projects page's remove step in a browser, on the two installs of
projects_page_kit.py: what goes and what stays, the fixture's lines among them; the press,
after which the row is one line in its place, and `Add it back`, which puts the project
back as it was; the step refused before a press, by the project's agents (with `Edit`)
and by the fixture's link (with its page); refused after a press, when the fixture holds
the project between the check and the press; the where note above the button, with the
refusal under it; and a lost answer, read again under its `job`, or said to be a remove
that may have been made.
"""

from __future__ import annotations

import json
import re
import sqlite3
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

JOB = re.compile(r"^[0-9a-f]{32}$")
GOES = "the project's entry in Moxi: its name and its kind. Other pages stop grouping by it."


def panel_of(page):
    return page.locator("#moxi-panel > moxi-projects-page")


def row(page, key: str):
    return panel_of(page).locator(f'moxi-ui-row[data-key="{key}"]')


def path_of(page) -> str:
    return page.evaluate("() => location.pathname")


def expect_path(page, path: str) -> None:
    deadline = time.monotonic() + 10
    while path_of(page) != path and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert path_of(page) == path


def step_of(page, key: str):
    """Opens the page at `remove/<key>` and gives the step, once its check is read."""
    page.goto(f"/m/projects/remove/{key}")
    step = row(page, key).locator('[data-part="remove"]')
    expect(step).to_have_attribute("data-state", re.compile("ask|refused"))
    return step


def lines(item) -> list[str]:
    return [text.strip() for text in item.locator(".ol p").all_inner_texts()]


def posts_to(page, path: str) -> list:
    seen: list = []
    page.on("request", lambda request: seen.append(request)
            if request.method == "POST" and request.url.endswith(f"/m/projects/{path}") else None)
    return seen


def stored(shell, key: str) -> dict | None:
    """What the shell's store holds for `key`, read without writing."""
    from moxi_projects import store

    conn = sqlite3.connect(f"file:{store.path(shell.env)}?mode=ro", uri=True)
    try:
        found = conn.execute("SELECT key, root, kind, lead, notes_root FROM projects WHERE key = ?",
                             (key,)).fetchone()
    finally:
        conn.close()
    return None if found is None else dict(zip(("key", "root", "kind", "lead", "notes_root"), found))


@pytest.fixture
def small_page(small_shell, ui_contexts):
    yield ui_contexts(small_shell).pages[0]
    kit.seed(small_shell, notes=False)


@pytest.fixture
def fixture_page(fixture_shell, ui_contexts):
    kit.reset(fixture_shell)
    yield ui_contexts(fixture_shell).pages[0]
    kit.reset(fixture_shell)
    kit.seed(fixture_shell)


# --- what goes, what stays, and back --------------------------------------------------------

def test_a_removed_row_is_one_line_in_its_place_and_is_added_back_as_it_was(fixture_shell, fixture_page):
    report = kit.full_report(fixture_shell)
    report["projects"]["harbor-infra"]["stays"] = ["the fixture's notes on it"]
    kit.say(fixture_shell, report)
    before = stored(fixture_shell, "harbor-infra")
    step = step_of(fixture_page, "harbor-infra")
    expect(step).to_have_attribute("data-state", "ask")
    expect(step.locator('[data-line="title"]')).to_have_text("Remove harbor-infra from Moxi?")
    assert lines(step.locator('[data-label="Goes"]')) == [GOES]
    expect(step.locator('[data-label="Stays"] .ol p')).to_have_text([
        "the folder ~/code/harbor-infra and every file in it", "the notes in ~/notes/code/harbor-infra",
        "the fixture's notes on it"])
    expect(step.locator('[data-line="foot"]')).to_have_text(
        "Nothing on disk is deleted. Adding ~/code/harbor-infra again under the same name puts it back.")
    # Nothing would refuse a change here, so no note.
    expect(step.locator('[data-line="note"]')).to_have_count(0)
    # The row drawn before it, which its line follows once it is removed.
    after = row(fixture_page, "harbor-infra").evaluate("item => item.previousElementSibling?.dataset.key ?? null")
    count = panel_of(fixture_page).locator('[data-line="count"]')
    expect(count).to_have_text("7 projects")

    posts = posts_to(fixture_page, "api/remove")
    step.get_by_role("button", name="Remove from Moxi").click()
    gone = panel_of(fixture_page).locator('[data-part="removed"]')
    expect(gone.locator('[data-line="removed"]')).to_have_text(
        "harbor-infra was removed from Moxi. Nothing on disk was deleted.")
    expect(row(fixture_page, "harbor-infra")).to_have_count(0)
    expect(count).to_have_text("6 projects")
    expect_path(fixture_page, "/m/projects/")
    assert gone.evaluate("item => item.previousElementSibling?.dataset.key ?? null") == after
    (body,) = [json.loads(request.post_data) for request in posts]
    assert body["key"] == "harbor-infra" and JOB.match(body["job"]), body
    assert stored(fixture_shell, "harbor-infra") is None
    assert Path(before["root"]).is_dir()

    gone.get_by_role("button", name="Add it back").click()
    expect(gone).to_have_count(0)
    expect(row(fixture_page, "harbor-infra")).to_have_count(1)
    expect(count).to_have_text("7 projects")
    assert stored(fixture_shell, "harbor-infra") == before


# --- refused --------------------------------------------------------------------------------

def test_the_step_refused_by_agents_and_a_module_names_each_with_edit_or_its_page(fixture_shell, fixture_page):
    kit.hold(fixture_shell)
    step = step_of(fixture_page, "atlas-api")
    expect(step).to_have_attribute("data-state", "refused")
    expect(step.locator('[data-line="title"]')).to_have_text("atlas-api can't be removed yet")
    agents = step.locator('[data-label="Agents"]')
    assert lines(agents) == ["planner and reviewer also work here. Take them off the project first."]
    fixture = step.locator('[data-label="Fixture"]')
    assert lines(fixture) == [kit.held_sentence("atlas-api")]
    expect(step.locator('[data-line="unchanged"]')).to_have_text("Nothing was changed.")
    expect(step.get_by_role("button", name="Remove from Moxi")).to_be_disabled()
    expect(step.get_by_role("button", name="Close")).to_be_visible()

    # The agents' fix is the Edit panel.
    agents.get_by_role("button", name="Edit").click()
    expect_path(fixture_page, "/m/projects/edit/atlas-api")
    expect(panel_of(fixture_page).locator('moxi-projects-panel [data-part="panel"]')).to_have_attribute(
        "data-panel", "edit")

    # Close leaves the project open; the fixture's row links to its page.
    step = step_of(fixture_page, "atlas-api")
    step.get_by_role("button", name="Close").click()
    expect_path(fixture_page, "/m/projects/open/atlas-api")
    expect(row(fixture_page, "atlas-api").locator('[data-part="remove"]')).to_have_count(0)
    step = step_of(fixture_page, "atlas-api")
    step.locator('[data-label="Fixture"]').get_by_role("link", name="Fixture").click()
    expect_path(fixture_page, "/m/fixture/")
    assert stored(fixture_shell, "atlas-api") is not None


def test_a_press_refused_since_the_check_draws_the_step_again_from_a_fresh_check(fixture_shell, fixture_page):
    step = step_of(fixture_page, "lumen-docs")
    expect(step).to_have_attribute("data-state", "ask")
    checks: list[str] = []
    fixture_page.on("request", lambda request: checks.append(request.url)
                    if "/m/projects/api/remove-check" in request.url else None)
    # The fixture takes hold of it between the check and the press.
    kit.hold(fixture_shell)
    step.get_by_role("button", name="Remove from Moxi").click()
    expect(step).to_have_attribute("data-state", "refused")
    assert lines(step.locator('[data-label="Fixture"]')) == [kit.held_sentence("lumen-docs")]
    expect(step.get_by_role("button", name="Remove from Moxi")).to_be_disabled()
    assert len(checks) == 1
    assert stored(fixture_shell, "lumen-docs") is not None


def test_a_held_press_whose_fresh_check_finds_nothing_keeps_the_step_with_the_reason(
        fixture_shell, fixture_page):
    # A check that finds nothing to refuse, while the fixture holds every remove.
    fixture_page.route("**/m/projects/api/remove-check?*",
                       lambda route: route.fulfill(json={"key": "orbit-cli", "refusals": []}))
    kit.hold(fixture_shell)
    step = step_of(fixture_page, "orbit-cli")
    expect(step).to_have_attribute("data-state", "ask")
    press = step.get_by_role("button", name="Remove from Moxi")
    press.click()
    said = step.locator('[data-line="said"]')
    expect(said).to_contain_text("orbit-cli")
    expect(step).to_have_attribute("data-state", "ask")
    expect(press).to_be_enabled()
    assert stored(fixture_shell, "orbit-cli") is not None
    fixture_page.unroute("**/m/projects/api/remove-check?*")


# --- the where note -------------------------------------------------------------------------

def test_the_where_note_shows_above_the_button_and_the_refusal_under_it(
        small_shell, small_page, tmp_path_factory):
    from moxi import paths, terminal

    from moxi_projects import store

    moved = str(tmp_path_factory.mktemp("terminal-data"))
    terminal.write({**small_shell.env, "MOXI_PROJECTS_DATA_HOME": moved},
                   path=paths.terminal_record(small_shell.env))
    try:
        here = store.path(small_shell.env)
        line = f"the projects store here is {here}, but the last run in a terminal used"
        step = step_of(small_page, "orbit-cli")
        expect(step.locator('[data-line="note"]')).to_contain_text(line)
        press = step.get_by_role("button", name="Remove from Moxi")
        press.click()
        said = step.locator('[data-line="said"]')
        expect(said).to_contain_text(line)
        expect(said).to_contain_text("MOXI_PROJECTS_DATA_HOME")
        # The step is kept, its button live.
        expect(step).to_have_attribute("data-state", "ask")
        expect(press).to_be_enabled()
        assert stored(small_shell, "orbit-cli") is not None
    finally:
        kit.record_terminal(small_shell)


# --- a lost answer --------------------------------------------------------------------------

def test_a_lost_answer_is_read_again_under_its_job(small_shell, small_page):
    def lost(route) -> None:
        route.fetch()               # the remove is made, and its answer never arrives
        route.fulfill(status=504, json={"error": "timeout"})

    statuses: list[str] = []
    small_page.on("request", lambda request: statuses.append(request.url)
                  if "/m/projects/api/remove-status" in request.url else None)
    posts = posts_to(small_page, "api/remove")
    small_page.route("**/m/projects/api/remove", lost)
    step = step_of(small_page, "orbit-cli")
    step.get_by_role("button", name="Remove from Moxi").click()
    expect(panel_of(small_page).locator('[data-part="removed"] [data-line="removed"]')).to_have_text(
        "orbit-cli was removed from Moxi. Nothing on disk was deleted.")
    (body,) = [json.loads(request.post_data) for request in posts]
    assert statuses == [f"{small_page.url.split('/m/', 1)[0]}/m/projects/api/remove-status?job={body['job']}"], statuses
    assert stored(small_shell, "orbit-cli") is None
    small_page.unroute("**/m/projects/api/remove")


def test_a_lost_answer_with_no_outcome_to_read_says_it_may_have_been_removed(small_shell, small_page):
    def lost(route) -> None:
        route.fetch()
        route.fulfill(status=504, json={"error": "timeout"})

    small_page.route("**/m/projects/api/remove", lost)
    small_page.route("**/m/projects/api/remove-status?*",
                     lambda route: route.fulfill(status=504, json={"error": "timeout"}))
    step = step_of(small_page, "orbit-cli")
    step.get_by_role("button", name="Remove from Moxi").click()
    # The list was read again, which no longer holds it, so the line is the list's.
    expect(row(small_page, "orbit-cli")).to_have_count(0)
    said = panel_of(small_page).locator('section[data-part="list"] > [data-line="said"]')
    expect(said).to_have_text("It may have been removed.")
    expect(panel_of(small_page).locator('[data-part="removed"]')).to_have_count(0)
    assert stored(small_shell, "orbit-cli") is None
    small_page.unroute("**/m/projects/api/remove-status?*")
    small_page.unroute("**/m/projects/api/remove")
