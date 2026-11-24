"""loadout's page in a browser on the small install (the core, ui and loadout, with neither
projects nor repo), through a real shell that starts loadout's real page server, on the
seeded home `page_kit` describes. atlas-api's section names its project by key, which
needs projects, so it is refused and was never placed:

- Placed counts it as a section that needs the user, says to name it by its path, and
  offers no `Copy`, since loadout recorded no root for it; nothing says to install
  projects.
- Its items read not placed in the grid and in the counts.
- The folder says nothing about a commit placing the folder again, which needs repo.
- A `project/<key>` address, or one whose item is gone, opens the whole page with a line
  at its foot, which stays when the shell places the page again for the address the page
  wrote in its place.
- Add offers no project without a section, and says nothing of projects.
- Install all ends `done, but the section atlas-api still can't be placed`, and its rows
  say why in the page's words.
"""

from __future__ import annotations

import re
import sys

import page_kit as kit
import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="loadout's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: Words of the projects link's refusal that the page never shows.
INSTALL_PROJECTS = "moxi install projects"


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return kit.small_options(tmp_path_factory)


@pytest.fixture(scope="module", name="loadout_dir")
def loadout_dir_fixture(ui_shell):
    return kit.seed(ui_shell, projects=False)


def test_the_refused_section_is_a_problem_with_the_path_free_fix(loadout_dir, ui_page):
    panel = kit.open_page(ui_page)
    placed = panel.locator('[data-group="placed"]')
    expect(placed.locator('[data-line="verdict"]')).to_have_text("2 items and 1 section need you")
    problems = placed.locator(".prob")
    expect(problems.locator('[data-line="sentence"]')).to_have_text([
        "The agent code-reviewer was changed by hand",
        "The skill release-checklist is listed and not placed",
        "The section atlas-api is named by a project key, so nothing in it is placed"])
    section = problems.nth(2)
    expect(section.locator('[data-line="word"]')).to_have_text("not placed")
    expect(section.locator('[data-line="where"]')).to_have_text(
        "loadout.toml · release-checklist and api-lead · Claude Code, Codex and OpenCode")
    expect(section.locator('[data-line="fix"]')).to_have_text("Name the section by its path")
    expect(section.locator("button")).to_have_count(0)
    expect(panel).not_to_contain_text(INSTALL_PROJECTS)


def test_the_sections_items_read_not_placed_and_count_as_not_placed(loadout_dir, ui_page):
    panel = kit.open_page(ui_page)
    for kind, name in (("skill", "release-checklist"), ("agent", "api-lead")):
        for harness in ("claude", "codex", "opencode"):
            expect(kit.cell(panel, kind, name, harness, "atlas-api")).to_have_text("not placed")
    expect(panel.locator('[data-group="placed"] [data-line="counts"]')).to_have_text(
        "Claude Code 7 of 10 placed · Codex 5 of 9 · OpenCode 5 of 8")


def test_without_repo_no_line_says_a_commit_places_them_again(loadout_dir, ui_page):
    panel = kit.open_page(ui_page)
    source = panel.locator('[data-group="folder"]')
    expect(source.locator('[data-line="uncommitted"]')).to_have_text(
        "2 files changed, not committed")
    expect(source.locator('[data-line="names"]')).to_have_text(
        "loadout.toml, skills/tidy-notes/SKILL.md")


@pytest.mark.parametrize(("route", "line"), [
    ("project/atlas-api", "No project is named atlas-api."),
    ("item/user/skill/no-such", "Your items no longer lists the skill no-such."),
])
def test_an_address_whose_thing_is_gone_opens_the_whole_page_with_a_line_that_stays(
        loadout_dir, ui_page, route, line):
    panel = kit.open_page(ui_page, route)
    expect(panel.locator('[data-line="foot"]', has_text=line)).to_be_visible()
    expect(panel.locator('[data-part="filter"]')).to_have_count(0)
    expect(panel.locator('[data-group="folder"]')).to_be_visible()
    expect(ui_page).to_have_url(re.compile(r"/m/loadout/$"))
    # The shell places the page again for the address the page wrote, as it does when
    # the modules it reads change: the new page keeps the line.
    panel.evaluate("element => { element.dataset.before = 'yes'; }")
    ui_page.evaluate("dispatchEvent(new PopStateEvent('popstate'))")
    again = ui_page.locator("#moxi-panel > moxi-loadout-page:not([data-before])")
    expect(again.locator('[data-group="folder"]')).to_be_visible()
    expect(again.locator('[data-line="foot"]', has_text=line)).to_be_visible()
    # Opening something takes it away.
    kit.item_row(again, "skill", "tidy-notes").locator('[data-action="open"]').click()
    expect(again.locator('[data-line="foot"]', has_text=line)).to_have_count(0)


def test_add_offers_no_project_and_says_nothing_of_projects(loadout_dir, ui_page):
    panel = kit.open_page(ui_page)
    panel.locator('[data-group="unlisted"] [data-action="add"]').click()
    add = panel.locator('[data-part="add"]')
    expect(add.get_by_label("Add to").locator("option")).to_have_text(
        ["Your items", "atlas-api"])
    expect(add.locator('[data-line="picker"]')).to_have_count(0)


def test_install_all_ends_done_but_the_section_still_cant_be_placed(ui_shells, ui_shell_options,
                                                                    ui_contexts):
    # A shell of its own, since the install changes what the other tests read.
    shell = ui_shells(**ui_shell_options)
    kit.seed(shell, projects=False)
    # The hand edit taken back, so the refused section is the only row the install leaves.
    kit.take_back_edit(shell)
    panel = kit.open_page(ui_contexts(shell).pages[0])
    placed = panel.locator('[data-group="placed"]')
    placed.locator('[data-action="install"]', has_text="Install all").click()
    job = placed.locator('[data-part="job"]')
    expect(job.locator('[data-line="job"]')).to_have_text(
        "moxi loadout install: done, but the section atlas-api still can't be placed",
        timeout=30_000)
    job.locator('[data-action="show"]').click()
    show = job.locator('[data-part="show"]')
    expect(show).to_contain_text(
        "The section atlas-api is named by a project key, so nothing in it is placed")
    expect(show).not_to_contain_text(INSTALL_PROJECTS)
    # release-checklist is placed now; the section is still refused.
    expect(kit.cell(panel, "skill", "release-checklist", "claude")).to_have_text("placed")
    expect(placed.locator('.prob [data-line="sentence"]')).to_have_text(
        ["The section atlas-api is named by a project key, so nothing in it is placed"])
