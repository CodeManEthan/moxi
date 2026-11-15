"""The Projects page's panel in a browser, on the two installs of projects_page_kit.py: the
Add panel opened by `Add a project`, at the window's right edge with the column moved
aside; a folder typed with `~` and in full, what is there said under the field (a git
repository, nothing yet with the box that makes it, a project's root, a folder inside
one), an answer for a value the field no longer holds dropped, the name filled from the
folder and typed over, `More`'s kind, lead and notes folder, and the add through to the
stored project. The Edit panel opened by the foot's `Edit`: an agent taken off and the
lead moved onto it, sent in the order that keeps the lead rule; a save refused part way,
which says what was saved and sends only the rest next time; and its close back to the
project open.
"""

from __future__ import annotations

import json
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


def posts_to(page, *paths: str) -> list:
    """The requests the page posts to any of `paths` from now on, in the order sent."""
    seen: list = []
    page.on("request", lambda request: seen.append(request)
            if request.method == "POST" and request.url.rsplit("/m/projects/", 1)[-1] in paths else None)
    return seen


def sent(posts) -> list[tuple[str, dict]]:
    """Each request's route and body, in the order sent."""
    return [(request.url.rsplit("/", 1)[1], json.loads(request.post_data)) for request in posts]


def agents_of(shell, key: str) -> list[str]:
    """Who the shell's store says also works on `key`, read without writing."""
    from moxi_projects import store

    conn = sqlite3.connect(f"file:{store.path(shell.env)}?mode=ro", uri=True)
    try:
        found = conn.execute("SELECT agent FROM project_agents WHERE project_key = ? ORDER BY agent",
                             (key,)).fetchall()
    finally:
        conn.close()
    return [name for (name,) in found]


def also_here(aside) -> list[str]:
    """The names the Edit panel lists under `Also works here`."""
    return aside.locator("[data-agent]").evaluate_all("rows => rows.map(row => row.dataset.agent)")


def edit_panel(page, key: str):
    """Opens `key`'s project, presses `Edit` in its foot, and gives the panel."""
    page.goto(f"/m/projects/open/{key}")
    item = row(page, key)
    expect(item).to_have_attribute("open", "")
    item.locator('[data-part="foot"]').get_by_role("button", name="Edit").click()
    aside = panel_of(page).locator('moxi-projects-panel [data-part="panel"]')
    expect(aside).to_have_attribute("data-panel", "edit")
    return aside


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


# --- Edit -------------------------------------------------------------------------------------

def test_an_agent_is_taken_off_and_the_lead_moved_onto_it_in_an_order_the_lead_rule_takes(
        small_shell, small_page):
    try:
        small_page.set_viewport_size({"width": 1280, "height": 800})
        posts = posts_to(small_page, "api/agents-remove", "api/set", "api/agents-add")
        aside = edit_panel(small_page, "atlas-api")
        expect_path(small_page, "/m/projects/edit/atlas-api")
        expect(aside.locator(".ph b")).to_have_text("Edit atlas-api")
        field = aside.get_by_label("Folder", exact=True)
        expect(field).to_be_focused()
        expect(field).to_have_value("~/code/atlas-api")
        expect(aside.get_by_label("Kind", exact=True)).to_have_value("project")
        expect(aside.get_by_label("Lead", exact=True)).to_have_value("builder-1")
        assert also_here(aside) == ["planner", "reviewer"]
        expect(aside.locator('[data-line="key"]')).to_have_text("atlas-api")
        expect(aside.locator('[data-line="name"]')).to_have_text(
            "The name stays as it is: everything filed under this project is filed by its name.")
        # No notes on the small install, so no notes folder.
        expect(aside.get_by_label("Notes folder")).to_have_count(0)
        save = aside.get_by_role("button", name="Save")
        expect(save).to_be_disabled()

        # planner is taken off and leads; builder-1, the lead until now, works there, typed
        # and added with Enter, which sends nothing.
        aside.get_by_role("button", name="Take off planner").click()
        aside.get_by_label("Lead", exact=True).fill("planner")
        agent = aside.get_by_label("Add an agent by name")
        agent.fill("builder-1")
        agent.press("Enter")
        expect(agent).to_have_value("")
        expect(aside.locator('[data-agent="builder-1"]')).to_have_count(1)
        assert also_here(aside) == ["reviewer", "builder-1"]
        assert posts == []
        save.click()

        # A full save closes the panel, the project still open, the focus on its `Edit`.
        expect(panel_of(small_page).locator("moxi-projects-panel")).to_have_count(0)
        expect_path(small_page, "/m/projects/open/atlas-api")
        item = row(small_page, "atlas-api")
        expect(item.locator('[data-part="foot"]').get_by_role("button", name="Edit")).to_be_focused()
        expect(item.locator('[data-part="opened"] [data-label="Agents"] .ol')).to_contain_text("planner leads")
        assert sent(posts) == [
            ("agents-remove", {"key": "atlas-api", "name": "planner"}),
            ("set", {"key": "atlas-api", "lead": "planner"}),
            ("agents-add", {"key": "atlas-api", "name": "builder-1"}),
        ]
        assert saved(small_shell, "atlas-api")["lead"] == "planner"
        assert agents_of(small_shell, "atlas-api") == ["builder-1", "reviewer"]
    finally:
        kit.seed(small_shell, notes=False)


def test_a_save_refused_part_way_says_what_was_saved_and_sends_the_rest_after(small_shell, small_page):
    moved = kit.home(small_shell) / "code" / "kestrel-moved"
    moved.mkdir(parents=True, exist_ok=True)
    try:
        aside = edit_panel(small_page, "kestrel-web")
        aside.get_by_role("button", name="Take off reviewer").click()
        field = aside.get_by_label("Folder", exact=True)
        field.fill("code/relative")
        aside.get_by_role("button", name="Save").click()
        said = aside.locator('[data-line="said"]')
        expect(said).to_have_attribute("data-outcome", "refused")
        expect(said).to_contain_text("Saved reviewer taken off. Not saved: ")
        expect(said).to_contain_text("fix:")
        # The panel stays, with what he typed; the name taken off is off for good.
        expect(field).to_have_value("code/relative")
        expect(aside.locator("[data-agent]")).to_have_count(0)
        assert agents_of(small_shell, "kestrel-web") == []
        assert saved(small_shell, "kestrel-web")["root"] == kit.root(small_shell, kit.SEVEN[2])

        # Only the rest is sent next time.
        posts = posts_to(small_page, "api/agents-remove", "api/set", "api/agents-add")
        field.fill("~/code/kestrel-moved")
        aside.get_by_role("button", name="Save").click()
        expect(panel_of(small_page).locator("moxi-projects-panel")).to_have_count(0)
        assert sent(posts) == [("set", {"key": "kestrel-web", "root": "~/code/kestrel-moved"})]
        assert saved(small_shell, "kestrel-web")["root"] == str(moved)
    finally:
        kit.seed(small_shell, notes=False)
        shutil.rmtree(moved, ignore_errors=True)


def test_the_edit_panel_closes_back_to_its_project_open(small_page):
    aside = edit_panel(small_page, "orbit-cli")
    aside.get_by_label("Lead", exact=True).fill("someone-else")
    aside.get_by_role("button", name="Cancel").click()
    expect_path(small_page, "/m/projects/open/orbit-cli")
    expect(panel_of(small_page).locator("moxi-projects-panel")).to_have_count(0)
    item = row(small_page, "orbit-cli")
    expect(item).to_have_attribute("open", "")
    edit = item.locator('[data-part="foot"]').get_by_role("button", name="Edit")
    expect(edit).to_be_focused()
    # Opened again, it starts from the project: nothing typed before was kept.
    edit.click()
    aside = panel_of(small_page).locator('moxi-projects-panel [data-part="panel"]')
    expect(aside.get_by_label("Lead", exact=True)).to_have_value("planner")
    small_page.keyboard.press("Escape")
    expect_path(small_page, "/m/projects/open/orbit-cli")
    expect(panel_of(small_page).locator("moxi-projects-panel")).to_have_count(0)
