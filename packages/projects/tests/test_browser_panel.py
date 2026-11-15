"""The Projects page's panel in a browser, on the two installs of projects_page_kit.py: the
Add panel opened by `Add a project`, at the window's right edge with the column moved
aside; a folder typed with `~` and in full, what is there said under the field (a git
repository, nothing yet with the box that makes it, a project's root, a folder inside
one), an answer for a value the field no longer holds dropped, the name filled from the
folder and typed over, `More`'s kind, lead and notes folder, and the add through to the
stored project.
"""

from __future__ import annotations

import shutil
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

NAME_FILLED = "Filled from the folder. Change it now if you like: it can't be changed later."


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


def add_panel(page):
    """Opens the page, presses `Add a project`, and gives the panel."""
    page.goto("/m/projects/")
    expect(panel_of(page).locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    panel_of(page).get_by_role("button", name="Add a project").click()
    aside = panel_of(page).locator('moxi-projects-panel [data-part="panel"]')
    expect(aside).to_be_visible()
    return aside


def said_under_field(aside):
    return aside.locator('[data-line="folder"]')


def saved(shell, key: str) -> dict | None:
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
    return ui_contexts(small_shell).pages[0]


@pytest.fixture
def fixture_page(fixture_shell, ui_contexts):
    kit.reset(fixture_shell)
    yield ui_contexts(fixture_shell).pages[0]
    kit.reset(fixture_shell)


# --- Add --------------------------------------------------------------------------------------

def test_a_path_typed_with_a_tilde_is_added_under_its_own_name(small_shell, small_page):
    where = kit.home(small_shell) / "code" / "wren-tools"
    (where / ".git").mkdir(parents=True, exist_ok=True)
    try:
        small_page.set_viewport_size({"width": 1280, "height": 800})
        aside = add_panel(small_page)
        expect_path(small_page, "/m/projects/add")
        # At the window's right edge, 400 px wide, with the column moved aside.
        box = aside.bounding_box()
        edge = small_page.evaluate("() => document.documentElement.clientWidth")
        assert (round(box["x"] + box["width"]), round(box["width"])) == (edge, 400), box
        page_box = panel_of(small_page).bounding_box()
        assert page_box["x"] + page_box["width"] <= box["x"], (page_box, box)

        field = aside.get_by_label("Folder", exact=True)
        expect(field).to_be_focused()
        expect(aside.locator('[data-line="more"]')).to_have_text("a code project · no lead")
        field.fill("~/code/wren-tools")
        expect(said_under_field(aside)).to_have_text("The folder exists. It is a git repository.")
        expect(aside.get_by_label("Name", exact=True)).to_have_value("wren-tools")
        expect(aside.locator('[data-line="name"]')).to_have_text(NAME_FILLED)
        expect(aside.locator('[data-part="create"]')).to_have_count(0)
        # The small install has no notes, so `More` holds no notes folder.
        aside.get_by_role("button", name="More").click()
        expect(aside.get_by_label("Kind", exact=True)).to_have_value("project")
        expect(aside.get_by_label("Notes folder")).to_have_count(0)

        aside.get_by_role("button", name="Add project").click()
        said = aside.locator('[data-line="said"]')
        expect(said).to_have_attribute("data-outcome", "done")
        expect(said).to_have_text("Added wren-tools at ~/code/wren-tools.")
        # The panel is empty again, and the list behind it read again.
        expect(field).to_have_value("")
        expect(row(small_page, "wren-tools")).to_have_attribute("where", "~/code/wren-tools")
        assert saved(small_shell, "wren-tools") == {"key": "wren-tools", "root": str(where), "kind": "project",
                                                    "lead": None, "notes_root": None}
        aside.get_by_role("button", name="Cancel").click()
        expect_path(small_page, "/m/projects/")
        expect(panel_of(small_page).locator("moxi-projects-panel")).to_have_count(0)
        assert panel_of(small_page).get_attribute("data-side") is None
    finally:
        kit.seed(small_shell, notes=False)
        shutil.rmtree(where, ignore_errors=True)


def test_a_full_path_with_nothing_there_is_made_with_more_set_and_its_name_typed_over(
        small_shell, small_page):
    where = kit.home(small_shell) / "agents" / "wren"
    try:
        aside = add_panel(small_page)
        aside.get_by_label("Folder", exact=True).fill(str(where))
        expect(said_under_field(aside)).to_have_text("No folder there yet.")
        expect(aside.get_by_label("Name", exact=True)).to_have_value("wren")
        create = aside.get_by_label("Create the folder and run git init")
        expect(create).not_to_be_checked()
        create.check()

        aside.get_by_role("button", name="More").click()
        aside.get_by_label("Kind", exact=True).select_option("agent")
        aside.get_by_label("Lead", exact=True).fill("builder-3")
        expect(aside.locator('[data-line="more"]')).to_have_text("an agent's home · builder-3 leads")
        name = aside.get_by_label("Name", exact=True)
        name.fill("wren-home")
        # Typed in, the name is his: no line says it was filled.
        expect(aside.locator('[data-line="name"]')).to_have_count(0)

        aside.get_by_role("button", name="Add project").click()
        said = aside.locator('[data-line="said"]')
        expect(said).to_have_attribute("data-outcome", "done", timeout=30_000)
        expect(said).to_contain_text("Added wren-home at ~/agents/wren.")
        assert (where / ".git").is_dir()
        assert saved(small_shell, "wren-home") == {"key": "wren-home", "root": str(where), "kind": "agent",
                                                   "lead": "builder-3", "notes_root": None}
        expect(row(small_page, "wren-home")).to_have_attribute("where", "~/agents/wren · agent's home")
    finally:
        kit.seed(small_shell, notes=False)
        shutil.rmtree(where, ignore_errors=True)


def test_the_field_says_how_a_path_stands_to_a_project_and_a_taken_name_is_refused(
        small_shell, small_page):
    aside = add_panel(small_page)
    field = aside.get_by_label("Folder", exact=True)
    name = aside.get_by_label("Name", exact=True)
    field.fill("~/code/atlas-api")
    expect(said_under_field(aside)).to_have_text("The folder exists. That folder is atlas-api's already.")
    expect(name).to_have_value("atlas-api")
    expect(aside.locator('[data-line="name"]')).to_have_text("That name is taken.")
    aside.get_by_role("button", name="Add project").click()
    said = aside.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-outcome", "refused")
    expect(said).to_contain_text("fix:")
    # A refusal keeps what was typed.
    expect(field).to_have_value("~/code/atlas-api")

    field.fill("~/code/atlas-api/deep")
    expect(said_under_field(aside)).to_have_text("No folder there yet. Inside atlas-api, a project already.")
    expect(name).to_have_value("deep")
    expect(aside.locator('[data-line="name"]')).to_have_text(NAME_FILLED)

    # A relative path says nothing, fills no name, and can't be added.
    field.fill("code/elsewhere")
    expect(name).to_have_value("")
    expect(said_under_field(aside)).to_have_count(0)
    expect(aside.get_by_role("button", name="Add project")).to_be_disabled()
    assert saved(small_shell, "deep") is None


def test_an_answer_for_a_value_the_field_no_longer_holds_is_dropped(small_shell, small_page):
    held: list = []

    def hold(route) -> None:
        if route.request.url.endswith("path=~%2Fcode%2Fatl"):
            held.append(route)
        else:
            route.continue_()

    small_page.route("**/m/projects/api/folder?*", hold)
    aside = add_panel(small_page)
    field = aside.get_by_label("Folder", exact=True)
    field.fill("~/code/atl")
    deadline = time.monotonic() + 10
    while not held and time.monotonic() < deadline:
        small_page.wait_for_timeout(50)
    assert held
    # Until what is typed has been read, the name it would fill is not sent.
    expect(aside.get_by_role("button", name="Add project")).to_be_disabled()
    field.fill("~/code/orbit-cli")
    expect(said_under_field(aside)).to_have_text("The folder exists. That folder is orbit-cli's already.")
    held[0].continue_()
    small_page.wait_for_timeout(300)
    expect(said_under_field(aside)).to_have_text("The folder exists. That folder is orbit-cli's already.")
    expect(aside.get_by_label("Name", exact=True)).to_have_value("orbit-cli")
    small_page.unroute("**/m/projects/api/folder?*")


def test_more_holds_the_notes_root_where_notes_is_installed(fixture_shell, fixture_page):
    where = kit.home(fixture_shell) / "code" / "wren-notes"
    where.mkdir(parents=True, exist_ok=True)
    try:
        aside = add_panel(fixture_page)
        aside.get_by_label("Folder", exact=True).fill("~/code/wren-notes")
        expect(said_under_field(aside)).to_have_text("The folder exists.")
        aside.get_by_role("button", name="More").click()
        aside.get_by_label("Notes folder", exact=True).fill("~/notes/code/wren-notes")
        expect(aside.locator('[data-line="more"]')).to_have_text(
            "a code project · no lead · notes in ~/notes/code/wren-notes")
        aside.get_by_role("button", name="Add project").click()
        expect(aside.locator('[data-line="said"]')).to_have_attribute("data-outcome", "done")
        found = saved(fixture_shell, "wren-notes")
        assert found is not None and found["root"] == str(where), found
        assert found["notes_root"] == str(kit.home(fixture_shell) / "notes" / "code" / "wren-notes"), found
    finally:
        kit.seed(fixture_shell)
        shutil.rmtree(where, ignore_errors=True)
