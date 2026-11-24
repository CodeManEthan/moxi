"""loadout's page in a browser on the full install (projects and repo beside loadout), one
test per question the page answers the user, through a real shell that starts loadout's
real page server, on the seeded home `page_kit` describes:

1. Does anything need me? Placed: the changed agent, then the skill not placed.
2. What does each harness get from me? The grid's cells and the counts.
3. What does atlas-api get beyond mine? Its section, and the page filtered to it.
4. What did I switch off on purpose? The skill off for Codex, and one not listed for
   OpenCode.
5. What does loadout do to my global instruction file? The Global file, managed, opened.
6. Did I change something in my folder that isn't saved? Folder and Not listed.
7. Is anything waiting on me inside a harness? The hook's trust note, and the
   experimental lines.
8. Will a change from this page land where my terminal's would? With no record of the
   terminal, the page shows every act as its command; with one, the acts are back.

Nothing here acts: the seeded home is read, and the one test that changes it (the
terminal's record) puts it back.
"""

from __future__ import annotations

import sys

import page_kit as kit
import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="loadout's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

HARNESSES = ("claude", "codex", "opencode")


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return kit.shell_options(tmp_path_factory)


@pytest.fixture(scope="module", name="loadout_dir")
def loadout_dir_fixture(ui_shell):
    return kit.seed(ui_shell, projects=True)


def cells(panel, kind: str, name: str, scope: str = "user") -> list[str]:
    """The words in an item's cells, Claude Code's, Codex's and OpenCode's."""
    return [kit.cell(panel, kind, name, harness, scope).inner_text().strip()
            for harness in HARNESSES]


def test_anything_that_needs_the_user_is_in_placed_changed_first(loadout_dir, ui_page):
    panel = kit.open_page(ui_page)
    placed = panel.locator('[data-group="placed"]')
    expect(placed.locator('[data-line="verdict"]')).to_have_text("2 items need you")
    problems = placed.locator(".prob")
    expect(problems).to_have_count(2)
    changed, missing = problems.nth(0), problems.nth(1)
    expect(changed.locator('[data-line="sentence"]')).to_have_text(
        "The agent code-reviewer was changed by hand")
    expect(changed.locator('[data-line="word"]')).to_have_text("changed")
    expect(changed.locator('[data-line="where"]')).to_have_text(
        "Your items · Codex · the file Codex reads, made from the source, was edited")
    expect(changed.locator('[data-line="fix"]')).to_have_text(
        "Make the change in the source instead, or put loadout's version back")
    expect(changed.locator('[data-action="replace"]')).to_have_text("Replace")
    expect(missing.locator('[data-line="sentence"]')).to_have_text(
        "The skill release-checklist is listed and not placed")
    expect(missing.locator('[data-line="word"]')).to_have_text("not placed")
    expect(missing.locator('[data-line="fix"]')).to_have_text("Install places it for all three")
    expect(missing.locator('[data-action="install"]')).to_have_text("Install")
    # One Install among the fixes: Install all sits dim on the counts line.
    expect(placed.locator('.counts [data-action="install"]')).to_have_text("Install all")


def test_each_harness_shows_what_it_gets_from_the_users_items(loadout_dir, ui_page):
    panel = kit.open_page(ui_page)
    group = panel.locator('[data-group="user"]')
    expect(group.locator(".hd .c")).to_have_text(["Claude Code", "Codex", "OpenCode"])
    expect(group.locator('[data-line="label"]')).to_have_text("Your items")
    want = {
        ("skill", "tidy-notes"): ["placed", "placed", "placed"],
        ("skill", "review-diff"): ["placed", "placed", ""],
        ("skill", "write-changelog"): ["placed", "off", "placed"],
        ("skill", "release-checklist"): ["not placed", "not placed", "not placed"],
        ("agent", "code-reviewer"): ["placed", "changed", "placed"],
        ("hook", "guard-rm"): ["placed", "placed", ""],
        ("permissions", "git-reads"): ["placed", "placed", "placed"],
    }
    for (kind, name), words in want.items():
        expect(kit.item_row(panel, kind, name)).to_be_visible()
        assert cells(panel, kind, name) == words, (kind, name)
    glob = panel.locator('.ir[data-item="global"]')
    expect(glob.locator('[data-line="mode"]')).to_have_text("managed")
    expect(glob.locator(".c")).to_have_text(["placed", "placed", "placed"])
    expect(panel.locator('[data-group="placed"] [data-line="counts"]')).to_have_text(
        "Claude Code 9 of 10 placed · Codex 7 of 9 · OpenCode 7 of 8")


def test_the_projects_section_shows_what_it_adds_and_the_page_filters_to_it(loadout_dir,
                                                                            ui_page):
    panel = kit.open_page(ui_page)
    group = panel.locator('[data-group="atlas-api"]')
    expect(group.locator('[data-line="label"]')).to_have_text("atlas-api")
    expect(group.locator('[data-line="count"]')).to_have_text("2 items")
    for kind, name in (("skill", "release-checklist"), ("agent", "api-lead")):
        expect(kit.item_row(panel, kind, name, "atlas-api")).to_be_visible()
        assert cells(panel, kind, name, "atlas-api") == ["placed"] * 3, name

    panel = kit.open_page(ui_page, "project/atlas-api")
    line = panel.locator('[data-part="filter"]')
    expect(line.locator('[data-action="project"]')).to_contain_text("atlas-api")
    expect(line.locator('[data-line="filter-where"]')).to_have_text(
        "what sessions in ~/code/atlas-api load")
    own = panel.locator('[data-group="atlas-api"]')
    expect(own.locator('[data-line="count"]')).to_have_text("2 of its own")
    expect(own.locator('[data-action="project-page"]')).to_have_text("Project")
    expect(panel.locator('[data-group="user"] [data-line="label"]')).to_have_text("Yours too")
    expect(panel.locator('[data-group="folder"]')).to_have_count(0)
    expect(panel.locator('[data-group="unlisted"]')).to_have_count(0)


def test_what_the_user_switched_off_reads_off_and_says_so_when_opened(loadout_dir, ui_page):
    panel = kit.open_page(ui_page)
    expect(kit.cell(panel, "skill", "write-changelog", "codex")).to_have_text("off")
    # review-diff is listed for Claude Code and Codex only: OpenCode's cell is blank.
    expect(kit.cell(panel, "skill", "review-diff", "opencode")).to_have_text("")
    kit.item_row(panel, "skill", "write-changelog").locator('[data-action="open"]').click()
    opened = panel.locator('[data-part="opened"]')
    codex = opened.locator('.r[data-harness="codex"]')
    expect(codex.locator('[data-line="word"]')).to_have_text("off")
    expect(codex.locator('[data-line="why"]')).to_have_text("you switched it off for Codex")
    expect(codex.locator('[data-action="turn-on"]')).to_have_text("Turn on")
    expect(opened.locator('.r[data-harness="claude"] [data-action="turn-off"]')).to_have_text(
        "Turn off")
    assert ui_page.evaluate("location.pathname") == "/m/loadout/item/user/skill/write-changelog"


def test_the_global_file_is_managed_and_the_next_install_changes_nothing(loadout_dir,
                                                                          ui_page):
    panel = kit.open_page(ui_page, "global")
    opened = panel.locator('[data-part="opened"][data-item="global"]')
    expect(opened.locator('[data-line="from"]')).to_have_text("~/my-loadout/global.md")
    expect(opened.locator('[data-mode="managed"]')).to_have_attribute("aria-current", "true")
    expect(opened.locator('[data-line="mode-line"]')).to_have_text(
        "Managed: your own text stays, and loadout adds its part to each file.")
    for harness in HARNESSES:
        expect(opened.locator(f'.r[data-harness="{harness}"]')).to_contain_text(
            "the next install changes nothing")
    expect(opened.locator('[data-action="diff"]')).to_have_count(0)


def test_uncommitted_files_and_what_no_section_lists_are_named(loadout_dir,
                                                                         ui_page):
    panel = kit.open_page(ui_page)
    source = panel.locator('[data-group="folder"]')
    expect(source.locator('[data-line="folder"]')).to_have_text("~/my-loadout")
    expect(source.locator('[data-line="uncommitted"]')).to_have_text(
        "2 files changed, not committed")
    expect(source.locator('[data-line="names"]')).to_have_text(
        "loadout.toml, skills/tidy-notes/SKILL.md · a commit places them again")
    unlisted = panel.locator('[data-group="unlisted"]')
    expect(unlisted.locator(".ir")).to_have_count(1)
    expect(unlisted.locator(".ir")).to_contain_text("bump-version")
    expect(unlisted.locator(".ir")).to_contain_text("skill · in your folder, in no section")


def test_the_hook_says_codex_waits_for_the_users_trust(loadout_dir, ui_page):
    panel = kit.open_page(ui_page)
    expect(kit.item_row(panel, "hook", "guard-rm").locator(
        "xpath=following-sibling::*[1][@data-line='second']")).to_have_text(
        "Codex runs a new hook only once you trust it in Codex")
    kit.item_row(panel, "hook", "guard-rm").locator('[data-action="open"]').click()
    opened = panel.locator('[data-part="opened"]')
    expect(opened.locator('[data-line="from"]')).to_have_text(
        "loadout.toml · runs python3 ~/my-loadout/hooks/guard_rm.py before each Bash call")
    codex = opened.locator('.r[data-harness="codex"] [data-line="why"]')
    expect(codex).to_contain_text(
        "Codex runs a new hook only once you trust it in Codex, and loadout can't tell "
        "whether you have.")
    expect(codex).to_contain_text(
        "Experimental: no run has confirmed yet that Codex reads the file as loadout writes it.")


def test_with_no_record_of_the_terminal_every_act_is_its_command(loadout_dir, ui_shell,
                                                                 ui_page):
    record = kit.record_path(ui_shell)
    kept = record.read_bytes()
    record.unlink()
    try:
        panel = kit.open_page(ui_page)
        note = panel.locator('[data-group="note"]')
        expect(note.locator('[data-line="note-heading"]')).to_have_text(
            "Changes from this page are off")
        expect(note.locator('[data-line="note"]')).to_contain_text("moxi modules")
        expect(note.locator('[data-line="note-last"]')).to_have_text(
            "Or run each change in a terminal.")
        placed = panel.locator('[data-group="placed"]')
        expect(placed.locator('[data-line="command"]')).to_have_text([
            "moxi loadout install --replace=agent:code-reviewer@codex --user",
            "moxi loadout install --user",
            "moxi loadout install"])
        expect(panel.locator('button[data-action="install"], button[data-action="replace"]')
               ).to_have_count(0)
    finally:
        record.write_bytes(kept)
    panel = kit.open_page(ui_page)
    expect(panel.locator('[data-group="note"]')).to_have_count(0)
    expect(panel.locator('[data-group="placed"] button[data-action="replace"]')).to_have_count(1)
