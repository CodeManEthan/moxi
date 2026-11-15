"""The Projects page's opened project in a browser, on the two installs of
projects_page_kit.py: one labelled row each (who leads and who also works there, the
folder and its kind, the notes folder, and the fixture's sections, ranked ones first,
each with its link), the foot's `Edit` and `Remove`, each with its address, and
`Change folder` from a folder that doesn't exist through the Edit panel to a saved root.
"""

from __future__ import annotations

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

MISSING_SAYS = ("Moxi keeps the project's name and settings. Point it at where the folder "
                "went, or remove the project.")


def panel_of(page):
    return page.locator("#moxi-panel > moxi-projects-page")


def row(page, key: str):
    return panel_of(page).locator(f'moxi-ui-row[data-key="{key}"]')


def opened(page, key: str):
    """Opens the page at `open/<key>` and gives the row, once it is open."""
    page.goto(f"/m/projects/open/{key}")
    item = row(page, key)
    expect(item).to_have_attribute("open", "")
    return item


def labels(item) -> list[str]:
    return item.locator('[data-part="opened"] .orow').evaluate_all(
        "rows => rows.map(row => row.dataset.label)")


def words(item, label: str):
    return item.locator(f'[data-part="opened"] [data-label="{label}"] .ol')


def path_of(page) -> str:
    return page.evaluate("() => location.pathname")


def expect_path(page, path: str) -> None:
    deadline = time.monotonic() + 10
    while path_of(page) != path and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert path_of(page) == path


def saved_root(shell, key: str) -> str:
    """The root the shell's store holds for `key`, read without writing."""
    from moxi_projects import store

    conn = sqlite3.connect(f"file:{store.path(shell.env)}?mode=ro", uri=True)
    try:
        (found,) = conn.execute("SELECT root FROM projects WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return found


@pytest.fixture
def small_page(small_shell, ui_contexts):
    return ui_contexts(small_shell).pages[0]


@pytest.fixture
def fixture_page(fixture_shell, ui_contexts):
    kit.reset(fixture_shell)
    yield ui_contexts(fixture_shell).pages[0]
    kit.reset(fixture_shell)


# --- who leads, and where -------------------------------------------------------------------

@pytest.mark.parametrize(("key", "root_words", "agents"), [
    ("atlas-api", "~/code/atlas-api · a code project",
     "builder-1 leads · planner and reviewer also work here"),
    ("kestrel-web", "~/code/kestrel-web · a code project", "builder-2 leads · reviewer also works here"),
    ("reviewer-home", "~/agents/reviewer · an agent's home", "no lead"),
])
def test_the_small_install_opens_a_project_to_its_root_and_who_leads(small_page, key, root_words, agents):
    item = opened(small_page, key)
    # No notes and no facts module: the folder and the agents, nothing else.
    assert labels(item) == ["Folder", "Agents"]
    expect(words(item, "Folder")).to_have_text(root_words)
    expect(words(item, "Agents")).to_have_text(agents)
    foot = item.locator('[data-part="foot"]')
    expect(foot.get_by_role("button")).to_have_text(["Edit", "Remove"])
    expect(item.locator('[data-part="opened"] a')).to_have_count(0)


def test_the_fixtures_sections_join_the_opened_project_ranked_first_with_their_links(
        fixture_shell, fixture_page):
    kit.say(fixture_shell, kit.full_report(fixture_shell))
    item = opened(fixture_page, "atlas-api")
    deadline = time.monotonic() + 10
    while labels(item)[0] != "Services" and time.monotonic() < deadline:
        fixture_page.wait_for_timeout(50)
    # The ranked section first, then the folder and the agents, then the rest in the
    # order the report lists them, then the notes folder.
    assert labels(item) == ["Services", "Folder", "Agents", "Sessions", "Cost", "Notes"]
    services = words(item, "Services")
    expect(services).to_have_text("queue-worker failed at 18:31 · api-server running")
    expect(services.locator("strong")).to_have_text("queue-worker failed at 18:31")
    expect(words(item, "Folder")).to_have_text("~/code/atlas-api · a code project")
    expect(words(item, "Agents")).to_have_text(
        "builder-1 leads · running now in slot 4 · planner and reviewer also work here")
    expect(words(item, "Sessions")).to_contain_text("3 more this week")
    expect(words(item, "Cost")).to_contain_text("$8.49 today")
    expect(words(item, "Notes")).to_have_text("~/notes/code/atlas-api")
    expect(item.locator('[data-label="Cost"] a')).to_have_count(0)

    # A section's link shows the module's page at the route the report gave.
    link = item.locator('[data-label="Services"] a')
    expect(link).to_have_text("Fixture")
    link.click()
    expect_path(fixture_page, "/m/fixture/project/atlas-api")
    expect(fixture_page.locator("#moxi-panel > moxi-fixture-page").get_by_test_id("route")).to_contain_text(
        "project/atlas-api")


#: For each labelled row of the opened project: the left of its label and of its words, its
#: words' colour, the label's (dim), each dim phrase's text, and its words' height and line
#: height; and the foot's acts, each with its left and colour.
_LOOK = """item => {
  const colour = (el) => getComputedStyle(el).color;
  const rows = [...item.querySelectorAll('[data-part="opened"] .orow')].map((row) => {
    const words = row.querySelector('.ol');
    return {
      label: row.dataset.label, size: getComputedStyle(row).fontSize,
      labelX: row.querySelector('.ok').getBoundingClientRect().left, wordsX: words.getBoundingClientRect().left,
      text: colour(words), dim: colour(row.querySelector('.ok')),
      dims: [...words.querySelectorAll('.d')].map((el) => [el.textContent.trim(), colour(el)]),
      height: words.getBoundingClientRect().height, line: parseFloat(getComputedStyle(words).lineHeight),
    };
  });
  const acts = [...item.querySelectorAll('[data-part="foot"] button')].map((el) =>
    [el.textContent.trim(), el.getBoundingClientRect().left, colour(el)]);
  return { rows, acts };
}"""


def test_the_opened_project_draws_each_lines_tail_dim_in_a_76_px_column(fixture_shell, fixture_page):
    fixture_page.set_viewport_size({"width": 1280, "height": 800})
    kit.say(fixture_shell, kit.full_report(fixture_shell))
    item = opened(fixture_page, "atlas-api")
    expect(words(item, "Sessions")).to_contain_text("3 more this week")
    look = item.evaluate(_LOOK)
    by = {row["label"]: row for row in look["rows"]}
    for row in look["rows"]:
        assert row["size"] == "13px", row
        assert row["wordsX"] - row["labelX"] == 76, row
        assert row["text"] != row["dim"], row
        assert all(colour == row["dim"] for _, colour in row["dims"]), row
    # The first phrase in the text's colour, the rest dim.
    assert [text for text, _ in by["Folder"]["dims"]] == ["· a code project"]
    assert [text for text, _ in by["Agents"]["dims"]] == ["· running now in slot 4",
                                                         "· planner and reviewer also work here"]
    assert [text for text, _ in by["Services"]["dims"]] == ["· api-server running"]
    assert "3 more this week" in [text for text, _ in by["Sessions"]["dims"]]
    # At 1280 the Agents line fits on one line.
    assert by["Agents"]["height"] < 2 * by["Agents"]["line"], by["Agents"]
    # The foot under the words: Edit in the text's colour, Remove dim.
    (edit, edit_x, edit_colour), (remove, _, remove_colour) = look["acts"]
    assert (edit, remove) == ("Edit", "Remove")
    assert edit_x == by["Folder"]["wordsX"]
    assert (edit_colour, remove_colour) == (by["Folder"]["text"], by["Folder"]["dim"])
    # With the remove step open, Remove in the text's colour and Edit dim.
    item.locator('[data-part="foot"] [data-action="remove"]').click()
    expect(item.locator('[data-part="remove"]')).to_be_visible()
    look = item.evaluate(_LOOK)
    assert [colour for _, _, colour in look["acts"]] == [by["Folder"]["dim"], by["Folder"]["text"]]


def test_an_agent_that_isnt_running_says_so_after_its_name(fixture_shell, fixture_page):
    kit.say(fixture_shell, kit.full_report(fixture_shell))
    item = opened(fixture_page, "kestrel-web")
    expect(words(item, "Agents")).to_have_text(
        "builder-2 leads · not running now · reviewer also works here")


# --- the foot ------------------------------------------------------------------------------

def test_the_foots_edit_and_remove_each_have_their_address(small_page):
    item = opened(small_page, "orbit-cli")
    foot = item.locator('[data-part="foot"]')
    aside = panel_of(small_page).locator('moxi-projects-panel [data-part="panel"]')
    foot.get_by_role("button", name="Edit").click()
    expect_path(small_page, "/m/projects/edit/orbit-cli")
    expect(aside).to_have_attribute("data-panel", "edit")
    expect(aside.get_by_label("Folder", exact=True)).to_be_focused()
    expect(item).to_have_attribute("open", "")
    foot.get_by_role("button", name="Remove").click()
    expect_path(small_page, "/m/projects/remove/orbit-cli")
    expect(aside).to_have_count(0)
    expect(item.locator('[data-part="remove"]')).to_be_visible()
    item.locator('[slot="acts-one"]').click()
    expect_path(small_page, "/m/projects/")


# --- a folder that doesn't exist --------------------------------------------------------------

def test_the_change_act_takes_a_missing_root_through_to_a_saved_one(small_shell, small_page):
    moved = kit.home(small_shell) / "code" / "quill-moved"
    moved.mkdir(parents=True, exist_ok=True)
    try:
        small_page.goto("/m/projects/")
        item = row(small_page, kit.MISSING)
        expect(item).to_have_attribute("lead", "Its folder doesn't exist")
        item.locator('[slot="acts"]').click()
        expect(item).to_have_attribute("open", "")
        expect_path(small_page, f"/m/projects/edit/{kit.MISSING}")
        root_words = words(item, "Folder")
        expect(root_words.locator("strong")).to_have_text("~/code/quill-mobile doesn't exist")
        expect(root_words).to_contain_text(MISSING_SAYS)
        expect(item.locator('[data-label="Folder"] [data-action="change-folder"]')).to_have_text("Change folder")
        expect(words(item, "Agents")).to_have_text("builder-2 leads")

        # The Edit panel's folder field has the focus; a `~` path is taken as the home.
        aside = panel_of(small_page).locator('moxi-projects-panel [data-part="panel"]')
        field = aside.get_by_label("Folder", exact=True)
        expect(field).to_be_focused()
        expect(field).to_have_value("~/code/quill-mobile")
        field.fill("~/code/quill-moved")
        aside.get_by_role("button", name="Save").click()
        expect(aside).to_have_count(0)
        expect_path(small_page, f"/m/projects/open/{kit.MISSING}")
        expect(root_words).to_have_text("~/code/quill-moved · a code project")
        expect(item.locator('[data-label="Folder"] [data-action="change-folder"]')).to_have_count(0)
        assert item.get_attribute("lead") is None
        expect(item.locator('[slot="acts"]')).to_have_count(0)
        assert saved_root(small_shell, kit.MISSING) == str(moved)
    finally:
        kit.seed(small_shell, with_notes=False)
