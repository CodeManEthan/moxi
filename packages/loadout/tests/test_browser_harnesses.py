"""loadout's page in a browser on which harnesses it draws and which sections are a
project's, through a real shell that starts the real page servers, on the seeded home
`page_kit` describes:

- Add for one harness: `For` offers every harness and each harness on this machine;
  picking Codex lists the skill for Codex only, and its other cells are blank.
- A harness not on this machine, with nothing of loadout's left there, has no column,
  isn't offered in Add, and nothing in Placed names it.
- A project with a key section and a path section under its root: the filtered view
  shows both sections' items, and the Projects page's `Loadout` row names both.
- Without projects, a path section under that root maps to no project and reads as
  before: its own group under its `~` path, and no filtered view.
"""

from __future__ import annotations

import shutil
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import page_kit as kit
import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="loadout's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: The path section under atlas-api's root the project tests add, from the home.
WEB = ("code", "atlas-api", "web")


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return kit.shell_options(tmp_path_factory)


@pytest.fixture
def world(ui_shells, ui_shell_options, ui_contexts):
    """A shell of the test's own on the seeded home, and a page signed in on it."""
    shell = ui_shells(**ui_shell_options)
    loadout_dir = kit.seed(shell, projects=True)
    page = ui_contexts(shell).pages[0]
    yield SimpleNamespace(shell=shell, page=page, dir=loadout_dir, home=Path(shell.env["HOME"]))
    shell.stop()


def options(select) -> list[str]:
    return select.locator("option").evaluate_all("options => options.map(o => o.textContent.trim())")


def head(panel, group: str = "user") -> list[str]:
    """The harnesses a group's head row draws, by their `data-harness`."""
    return panel.locator(f'[data-group="{group}"] .ir.hd .c').evaluate_all(
        "cells => cells.map(c => c.dataset.harness)")


def with_web_section(home: Path, loadout_dir: Path) -> str:
    """A path section under atlas-api's root listing bump-version; returns its root."""
    root = home.joinpath(*WEB)
    root.mkdir(parents=True, exist_ok=True)
    manifest = loadout_dir / "loadout.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8")
                        + f'\n[project."{root}"]\nskills = ["bump-version"]\n', encoding="utf-8")
    return str(root)


def test_add_for_one_harness_lists_it_for_that_harness_only(world):
    panel = kit.open_page(world.page)
    panel.locator('[data-group="unlisted"] [data-action="add"]').click()
    add = panel.locator('[data-part="add"]')
    harness = add.get_by_label("For")
    expect(harness).to_have_value("")
    assert options(harness) == ["every harness", "Claude Code", "Codex", "OpenCode"]
    harness.select_option("codex")
    add.locator('[data-action="confirm-add"]').click()
    kit.job_line(panel, "moxi loadout add skill bump-version --harness=codex: done")
    expect(kit.cell(panel, "skill", "bump-version", "codex")).to_have_text("placed")
    for other in ("claude", "opencode"):
        expect(kit.cell(panel, "skill", "bump-version", other)).to_have_text("")
    toml = tomllib.loads((world.dir / "loadout.toml").read_text(encoding="utf-8"))
    assert toml["skill"]["bump-version"]["harnesses"] == ["codex"], toml


def test_a_harness_not_on_this_machine_has_no_column_and_isnt_offered(world):
    from moxi_loadout import harness

    # Codex goes from this machine: its home, and every placement of loadout's for it. The
    # hand edit is taken back first, since status keeps an edited file `modified` even
    # once it is gone, and a modified row keeps its harness drawn.
    kit.take_back_edit(world.shell)
    for row in kit.status(world.shell)["rows"]:
        target = Path(row["target"]) if row["harness"] == "codex" and row["target"] else None
        if target is None:
            continue
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
    shutil.rmtree(harness.home("codex", world.shell.env))
    panel = kit.open_page(world.page)
    assert head(panel) == ["claude", "opencode"]
    expect(kit.cell(panel, "skill", "tidy-notes", "codex")).to_have_count(0)
    expect(panel.locator('[data-group="placed"]')).not_to_contain_text("Codex")
    panel.locator('[data-group="unlisted"] [data-action="add"]').click()
    assert options(panel.locator('[data-part="add"]').get_by_label("For")) == [
        "every harness", "Claude Code", "OpenCode"]


def test_a_project_with_a_key_and_a_path_section_under_its_root_is_filtered_to_both(world):
    web = with_web_section(world.home, world.dir)
    panel = kit.open_page(world.page, "project/atlas-api")
    expect(panel.locator('[data-part="filter"] [data-action="project"]')).to_contain_text("atlas-api")
    key = panel.locator('[data-group="atlas-api"]')
    expect(key.locator('[data-line="label"]')).to_have_text("atlas-api")
    expect(key.locator('[data-line="count"]')).to_have_text("2 of its own")
    under = panel.locator(f'[data-group="{web}"]')
    expect(under.locator('[data-line="label"]')).to_have_text("~/code/atlas-api/web")
    expect(under.locator('[data-line="count"]')).to_have_text("1 of its own")
    expect(kit.item_row(panel, "skill", "bump-version", web)).to_be_visible()
    # The Projects page's row names the items of both, the key section's first.
    world.page.goto("/m/projects/open/atlas-api")
    row = world.page.locator('#moxi-panel > moxi-projects-page moxi-ui-row[data-key="atlas-api"]')
    expect(row.locator('[data-part="opened"] [data-label="Loadout"] .ol')).to_have_text(
        "skill release-checklist · agent api-lead · skill bump-version")


@pytest.fixture(scope="module")
def small(ui_shells, tmp_path_factory):
    """The small install (no projects), seeded, with the path section under atlas-api's
    root."""
    shell = ui_shells(**kit.small_options(tmp_path_factory))
    loadout_dir = kit.seed(shell, projects=False)
    web = with_web_section(Path(shell.env["HOME"]), loadout_dir)
    return SimpleNamespace(shell=shell, web=web)


def test_without_projects_a_path_section_reads_as_before(small, ui_contexts):
    page = ui_contexts(small.shell).pages[0]
    panel = kit.open_page(page)
    under = panel.locator(f'[data-group="{small.web}"]')
    expect(under.locator('[data-line="label"]')).to_have_text("~/code/atlas-api/web")
    expect(kit.item_row(panel, "skill", "bump-version", small.web)).to_be_visible()
    # No filtered view: the address opens the whole page.
    panel = kit.open_page(page, "project/atlas-api")
    expect(panel.locator('[data-part="filter"]')).to_have_count(0)
    expect(panel.locator(f'[data-group="{small.web}"]')).to_be_visible()
