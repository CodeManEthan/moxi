"""The launcher's piece on Home in a browser, through a real shell that starts the
launcher's real page server: the piece in Home's main spot with Home's rows under it, and
the line above the box that says where the session starts: the newest place when Home
opens, the menu of places, a typed folder, a project picked from projects' own picker, a
Home that never launched with no project, one and several, and an install without
projects and without slots, where every place is a folder and the button copies.

Every shell here runs the fake slots in slots' place and a fake `claude` first on its
`PATH`, so no launch starts a real session. A shell's launch log is seeded once, before a
page opens, with times far ahead of the clock, so a launch a test makes is never the
newest place.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

import launcher_page_kit as kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' picker is checked in a browser on Linux")]

SENTENCE = "Nothing needs you."
PREVIEW = "/m/launcher/api/preview"


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    return kit.Harness(tmp_path_factory.mktemp("fake-harness"))


@pytest.fixture(scope="module")
def on_path(harness):
    """The fake harness first on `PATH` for every shell this module starts."""
    with kit.first_on_path(harness.bin):
        yield


def started(ui_shells, tmp_path_factory, **options):
    """One more shell on the fake slots, with one slot free; returns it and its fake."""
    fake = kit.FakeSlots(tmp_path_factory.mktemp("fake-slots"))
    fake.script(placed=2, slots=kit.free(2))
    return ui_shells(**fake.options(), **options), fake


def root(shell, name: str) -> Path:
    """A folder under the shell's home, made."""
    found = Path(shell.env["HOME"]) / "work" / name
    found.mkdir(parents=True, exist_ok=True)
    return found


def add_project(shell, key: str) -> Path:
    """The project `key` in the shell's own store, its root under the shell's home."""
    where = root(shell, key)
    done = shell.moxi("projects", "add", key, "--root", str(where))
    assert done.returncode == 0, done.stdout + done.stderr
    return where


@pytest.fixture(scope="module")
def shell(ui_shells, tmp_path_factory, on_path):
    """A shell with two projects whose user launched in three places: a folder under his
    home last, the project `proj-a` before it, from a folder inside the project, and a
    folder outside his home first."""
    shell, _ = started(ui_shells, tmp_path_factory)
    add_project(shell, "proj-a")
    inside = root(shell, "proj-a/docs")
    add_project(shell, "proj-b")
    kit.place(shell, "place-outside", cwd="/srv/work/elsewhere", at="2098-01-01T08:00:00.000000Z")
    kit.place(shell, "place-project", cwd=str(inside), project_key="proj-a",
              at="2098-01-01T09:00:00.000000Z")
    kit.place(shell, "place-folder", cwd=str(root(shell, "notes")),
              at="2098-01-01T10:00:00.000000Z")
    return shell


@pytest.fixture
def page(shell, ui_contexts):
    return ui_contexts(shell).pages[0]


def where(piece):
    """The line above the box."""
    return piece.locator(".where")


def open_where(piece):
    """The line's menu, opened."""
    where(piece).locator(".face").click()
    menu = piece.locator('[data-menu="where"]')
    expect(menu).to_be_visible()
    return menu


def names(menu) -> list[str]:
    """The menu's places, top first."""
    return menu.locator(".places .row .name").all_inner_texts()


def previewed(page, piece) -> dict:
    """Press the button, and return the body the piece sends to be previewed."""
    with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    return json.loads(sent.value.post_data)


# --- the spot -------------------------------------------------------------------------------

def test_the_piece_fills_homes_main_spot_and_homes_rows_keep_their_places(page):
    piece = kit.open_home(page)
    home = page.locator("#moxi-panel > moxi-ui-page moxi-ui-home")
    expect(home.get_by_text(SENTENCE)).to_have_count(0)
    box = piece.locator("textarea")
    expect(box).to_be_focused()
    expect(box).to_have_attribute("placeholder", "What should the agent do?")
    # The piece is the spot's whole column, and the rows start 40 px under the spot.
    spot = home.locator(".spot").bounding_box()
    held = piece.bounding_box()
    assert spot["width"] == 680 and held["width"] == 680 and held["x"] == spot["x"]
    assert spot["height"] == 150 == piece.locator(".box").bounding_box()["height"]
    assert home.locator(".rows").bounding_box()["y"] == spot["y"] + spot["height"] + 40
    health = home.locator(".entry[data-row='ui.health'] .row")
    expect(health).to_be_visible()
    expect(health.locator(".label")).to_have_text("Health")
    # The line sits above the box, and nothing of the piece covers a row.
    line = where(piece).bounding_box()
    assert line["y"] + line["height"] <= spot["y"]
    assert health.bounding_box()["y"] >= held["y"] + held["height"]


# --- the line at load, and its menu ---------------------------------------------------------

def test_home_opens_on_the_newest_place_with_the_home_folder_written_short(page):
    piece = kit.open_home(page)
    expect(where(piece)).to_have_attribute("data-word", "Folder")
    expect(where(piece).locator(".k")).to_have_text("Folder")
    face = where(piece).locator(".face")
    expect(face).to_contain_text("~/work/notes")
    # The folder's own name is in the text colour, what leads to it is dim.
    expect(face.locator(".p")).to_have_text("~/work/")
    kit.assert_colour(face.locator(".p"), "text-dim")
    kit.assert_colour(face, "text")


def test_the_menu_lists_the_places_newest_first_with_a_tick_on_the_current_one(page):
    piece = kit.open_home(page)
    menu = open_where(piece)
    expect(menu.locator(".find")).to_be_focused()
    expect(menu.locator(".find")).to_have_attribute("placeholder", "Type a folder path")
    expect(menu.locator(".places .row").nth(2)).to_be_visible()
    # A project's place is its key alone: the folder of its last launch is not drawn.
    assert names(menu)[:3] == ["~/work/notes", "proj-a", "/srv/work/elsewhere"]
    current = menu.locator('.places .row[data-current="true"]')
    expect(current).to_have_count(1)
    expect(current.locator(".name")).to_have_text("~/work/notes")
    expect(current).to_contain_text("✓")
    for row in menu.locator(".places .row").all()[:3]:
        expect(row.locator(".t")).not_to_be_empty()
        name, when = row.locator(".name").bounding_box(), row.locator(".t").bounding_box()
        assert when["x"] >= name["x"] + name["width"], "the time is at the right"
    expect(menu.locator(".all")).to_have_text("All projects")
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    expect(where(piece).locator(".face")).to_be_focused()


def test_a_project_picked_from_the_places_is_the_line_and_the_launch_names_it(page, shell):
    piece = kit.open_home(page)
    menu = open_where(piece)
    menu.locator(".places .row", has_text="proj-a").click()
    expect(menu).to_have_count(0)
    expect(where(piece)).to_have_attribute("data-word", "Project")
    expect(where(piece).locator(".face")).to_contain_text("proj-a")
    expect(piece.locator("textarea")).to_be_focused()
    body = previewed(page, piece)
    assert body["values"]["project"] == "proj-a" and "cwd" not in body["values"], body
    # The launch starts at the project's root, not in the folder of its last launch.
    expect(piece.locator('[data-line="started"]')).to_be_visible()
    row = kit.newest_launch(shell)
    assert (row.project_key, row.cwd) == ("proj-a", str(root(shell, "proj-a"))), row
    # The tick follows the pick.
    menu = open_where(piece)
    expect(menu.locator('.places .row[data-current="true"] .name')).to_have_text("proj-a")


def test_a_project_picked_from_all_projects_is_the_line_and_the_launch_names_it(page, shell):
    piece = kit.open_home(page)
    menu = open_where(piece)
    picker = menu.locator(".picker moxi-projects-picker")
    expect(picker.locator("button.choice")).to_have_count(2)
    picker.locator('button.choice[data-key="proj-b"]').click()
    expect(menu).to_have_count(0)
    expect(where(piece)).to_have_attribute("data-word", "Project")
    expect(where(piece).locator(".face")).to_contain_text("proj-b")
    expect(piece.locator("textarea")).to_be_focused()
    body = previewed(page, piece)
    assert body["values"]["project"] == "proj-b" and "cwd" not in body["values"], body
    expect(piece.locator('[data-line="started"]')).to_be_visible()
    row = kit.newest_launch(shell)
    assert (row.project_key, row.cwd) == ("proj-b", str(root(shell, "proj-b"))), row
    # The launch made it a place, and the places are read again as the menu opens.
    menu = open_where(piece)
    expect(menu.locator('.places .row[data-current="true"] .name')).to_have_text("proj-b")


def test_a_typed_folder_is_taken_only_as_an_absolute_path_or_one_from_home(page, shell):
    piece = kit.open_home(page)
    menu = open_where(piece)
    find = menu.locator(".find")
    find.fill("work/notes")
    find.press("Enter")
    expect(menu.locator(".hint")).to_have_text("Give an absolute path, or one starting with ~")
    expect(where(piece).locator(".face")).to_contain_text("~/work/notes")
    find.fill("~/work/proj-b")
    find.press("Enter")
    expect(menu).to_have_count(0)
    expect(where(piece)).to_have_attribute("data-word", "Folder")
    expect(where(piece).locator(".face")).to_contain_text("~/work/proj-b")
    body = previewed(page, piece)
    assert body["values"]["cwd"] == "~/work/proj-b" and "project" not in body["values"], body
    expect(piece.locator('[data-line="started"]')).to_be_visible()
    assert kit.newest_launch(shell).cwd == str(root(shell, "proj-b"))


def test_a_long_folder_is_cut_in_its_dim_middle_and_its_name_is_whole(page):
    piece = kit.open_home(page)
    deep = "/srv/" + "/".join(["a-folder-with-a-long-name"] * 4) + "/the-last-folder-of-them-all"
    menu = open_where(piece)
    menu.locator(".find").fill(deep)
    menu.locator(".find").press("Enter")
    face = where(piece).locator(".face")
    expect(face).to_contain_text("the-last-folder-of-them-all")
    expect(face.locator(".p")).to_have_text(re.compile(r"^/srv/…/"))
    assert len(face.locator(".p").inner_text()) < len(deep) - len("the-last-folder-of-them-all")
    kit.assert_colour(face.locator(".p"), "text-dim")


# --- a Home that never launched -------------------------------------------------------------

def test_a_home_that_never_launched_opens_on_home_then_the_one_project_then_asks(
        ui_shells, ui_contexts, tmp_path_factory, on_path):
    shell, _ = started(ui_shells, tmp_path_factory)
    page = ui_contexts(shell).pages[0]
    # No project: the home folder.
    piece = kit.open_home(page)
    expect(where(piece)).to_have_attribute("data-word", "Folder")
    expect(where(piece).locator(".face")).to_have_text(re.compile(r"^~\s*▾$"))
    menu = open_where(piece)
    expect(menu.locator(".places .row")).to_have_count(0)
    # One project: it is picked.
    add_project(shell, "proj-a")
    piece = kit.open_home(page)
    expect(where(piece)).to_have_attribute("data-word", "Project")
    expect(where(piece).locator(".face")).to_contain_text("proj-a")
    # Several: the line asks, and the button is off until one is picked.
    add_project(shell, "proj-b")
    page.goto("/m/ui/")
    piece = kit.home_prompt(page)
    expect(where(piece)).to_have_attribute("data-word", "Project")
    expect(where(piece).locator(".face")).to_have_text(re.compile(r"^choose\s*▾$"))
    go = piece.locator(".go")
    expect(go).to_have_text("Launch in slot 2")
    expect(go).to_be_disabled()
    menu = open_where(piece)
    menu.locator('.picker button.choice[data-key="proj-b"]').click()
    expect(where(piece).locator(".face")).to_contain_text("proj-b")
    expect(go).to_be_enabled()


# --- without projects and without slots -----------------------------------------------------

@pytest.fixture(scope="module")
def small(ui_shells, tmp_path_factory, on_path):
    """A shell that lists no projects module and whose slots can't be used, with one
    place, launched in a project from a folder inside it."""
    fake = kit.FakeSlots(tmp_path_factory.mktemp("fake-slots"))
    fake.script(supported={"ok": False, "why": "tmux is not on PATH"})
    shell = ui_shells(leave_out=["projects"], **fake.options())
    kit.place(shell, "small-place", cwd=str(root(shell, "proj-a/docs")),
              project_key="proj-a", at="2098-01-01T09:00:00.000000Z")
    return shell


def test_without_projects_a_place_is_a_folder_and_a_pick_of_it_launches_in_that_folder(
        small, ui_contexts):
    context = ui_contexts(small)
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    page = context.pages[0]
    piece = kit.open_home(page)
    expect(where(piece)).to_have_attribute("data-word", "Folder")
    expect(where(piece).locator(".face")).to_contain_text("~/work/proj-a/docs")
    menu = open_where(piece)
    assert names(menu) == ["~/work/proj-a/docs"]
    expect(menu.locator(".all")).to_have_count(0)
    expect(menu.locator(".picker")).to_have_count(0)
    expect(menu.locator("moxi-projects-picker")).to_have_count(0)
    menu.locator(".places .row").click()
    body = previewed(page, piece)
    assert body["values"]["cwd"] == str(root(small, "proj-a/docs")), body
    assert "project" not in body["values"], body
    expect(piece.locator('[data-line="copied"]')).to_be_visible()


def test_without_slots_the_button_copies_and_offers_the_terminal_and_headless_alone(
        small, ui_contexts):
    page = ui_contexts(small).pages[0]
    piece = kit.open_home(page)
    go = piece.locator(".go")
    expect(go).to_have_text("Copy command")
    expect(go).to_have_attribute("data-target", "terminal")
    piece.locator(".more").click()
    menu = piece.locator('[data-menu="run"]')
    expect(menu.locator(".row")).to_have_count(2)
    assert menu.locator(".row").evaluate_all("rows => rows.map((row) => row.dataset.run)") == [
        "Your terminal", "Headless"]
    expect(menu.locator("hr")).to_have_count(0)
    expect(menu.locator('[data-run="Your terminal"]')).to_have_attribute("data-current", "true")
    # Nothing on the piece names a module this install lacks.
    said = piece.inner_text().lower()
    assert "slot" not in said and "project" not in said, said


def test_without_projects_a_home_that_never_launched_opens_on_the_home_folder(
        ui_shells, ui_contexts, tmp_path_factory, on_path):
    fake = kit.FakeSlots(tmp_path_factory.mktemp("fake-slots"))
    fake.script(supported={"ok": False, "why": "tmux is not on PATH"})
    shell = ui_shells(leave_out=["projects"], **fake.options())
    piece = kit.open_home(ui_contexts(shell).pages[0])
    expect(where(piece)).to_have_attribute("data-word", "Folder")
    expect(where(piece).locator(".face")).to_have_text(re.compile(r"^~\s*▾$"))
    expect(piece.locator(".go")).to_have_text("Copy command")
