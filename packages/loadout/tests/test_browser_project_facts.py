"""loadout's facts on the Projects page in a browser, through a real shell that starts the
real page servers of projects and loadout, on two installs:

- The full one, the core, ui, projects and loadout, on the seeded home `page_kit`
  describes, with kestrel-web a project too: the opened atlas-api has a `Loadout` row of
  its section's items, `skill release-checklist · agent api-lead`, whose link opens the
  Loadout page filtered to atlas-api; kestrel-web, which loadout lists nothing for, has
  none, and no closed row says anything of loadout's; the page reads only loadout's
  `/api/list`; a manifest loadout can't read leaves the row out, and the next good read
  puts it back.
- The small one, the core, ui and projects: the opened project has no `Loadout` row and
  no room for one, no loadout component is placed, and nothing of loadout's is read.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import page_kit as kit
import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="loadout's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: atlas-api's `Loadout` row, as the drawing words it.
LINE = "skill release-checklist · agent api-lead"
#: How long after loadout's list is read a test waits for a report that leaves a row out,
#: in milliseconds: well past the page's one frame.
DRAWN_MS = 1_000


@pytest.fixture(scope="module")
def full(ui_shells, tmp_path_factory):
    """The full install, seeded: atlas-api with its section, and kestrel-web with none."""
    shell = ui_shells(**kit.only_options(tmp_path_factory, ("projects", "loadout")))
    kit.seed(shell, projects=True)
    (Path(shell.env["HOME"]) / "code" / "kestrel-web").mkdir(parents=True)
    kit.moxi(shell, "projects", "add", "kestrel-web", "--root",
             str(Path(shell.env["HOME"]) / "code" / "kestrel-web"))
    return shell


@pytest.fixture(scope="module")
def small(ui_shells, tmp_path_factory):
    """The small install, with atlas-api a project."""
    shell = ui_shells(**kit.only_options(tmp_path_factory, ("projects",)))
    root = Path(shell.env["HOME"]) / "code" / "atlas-api"
    root.mkdir(parents=True)
    kit.moxi(shell, "projects", "add", "atlas-api", "--root", str(root))
    return shell


def page_on(ui_contexts, shell):
    """A fresh page on `shell`, keeping the path of every request it makes in `page.asked`."""
    page = ui_contexts(shell).pages[0]
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url.split("?", 1)[0]))
    page.asked = asked
    return page


def row(page, key: str):
    return page.locator(f'#moxi-panel > moxi-projects-page moxi-ui-row[data-key="{key}"]')


def opened(page, key: str):
    """The Projects page opened at `key`, once the row is open."""
    page.goto(f"/m/projects/open/{key}")
    item = row(page, key)
    expect(item).to_have_attribute("open", "")
    return item


def labels(item) -> list[str]:
    return item.locator('[data-part="opened"] .orow').evaluate_all("rows => rows.map(row => row.dataset.label)")


def loadout_reads(page) -> list[str]:
    return [url[url.index("/m/loadout/"):] for url in page.asked if "/m/loadout/" in url]


def wait_for_list(page) -> None:
    """Wait until loadout's list has been read, then for the page to draw its report."""
    deadline = time.monotonic() + 30
    while "/m/loadout/api/list" not in loadout_reads(page) and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert "/m/loadout/api/list" in loadout_reads(page), page.asked
    page.wait_for_timeout(DRAWN_MS)


def test_the_opened_project_shows_its_items_and_links_to_its_filtered_loadout(full, ui_contexts):
    page = page_on(ui_contexts, full)
    item = opened(page, "atlas-api")
    words = item.locator('[data-part="opened"] [data-label="Loadout"] .ol')
    expect(words).to_have_text(LINE)
    link = item.locator('[data-label="Loadout"] a')
    expect(link).to_have_text("Loadout")
    expect(link).to_have_attribute("href", "/m/loadout/project/atlas-api")
    # The page reads loadout's list and nothing else of loadout's.
    assert set(loadout_reads(page)) <= {"/m/loadout/project-facts.js", "/m/loadout/api/list"}, page.asked
    assert "/m/loadout/api/list" in loadout_reads(page)

    link.click()
    expect(page).to_have_url(f"{full.url}/m/loadout/project/atlas-api")
    panel = page.locator("#moxi-panel > moxi-loadout-page")
    expect(panel.locator('[data-part="filter"] [data-action="project"]')).to_contain_text("atlas-api")


def test_a_project_loadout_lists_nothing_for_has_no_row_and_no_closed_row_names_loadout(full, ui_contexts):
    page = page_on(ui_contexts, full)
    item = opened(page, "kestrel-web")
    wait_for_list(page)
    assert "Loadout" not in labels(item)
    page.goto("/m/projects/")
    for key in ("atlas-api", "kestrel-web"):
        expect(row(page, key)).to_be_visible()
    wait_for_list(page)
    for key in ("atlas-api", "kestrel-web"):
        expect(row(page, key)).not_to_contain_text("release-checklist")


def test_a_manifest_loadout_cant_read_leaves_the_row_out_until_it_reads_again(full, ui_contexts):
    manifest = Path(full.env["HOME"]) / "my-loadout" / "loadout.toml"
    good = manifest.read_text(encoding="utf-8")
    page = page_on(ui_contexts, full)
    item = opened(page, "atlas-api")
    expect(item.locator('[data-label="Loadout"] .ol')).to_have_text(LINE)
    manifest.write_text("[project.atlas-api\n", encoding="utf-8")
    try:
        page.asked.clear()
        item = opened(page, "atlas-api")
        wait_for_list(page)
        expect(item.locator('[data-label="Loadout"]')).to_have_count(0)
    finally:
        manifest.write_text(good, encoding="utf-8")
    item = opened(page, "atlas-api")
    expect(item.locator('[data-label="Loadout"] .ol')).to_have_text(LINE)


def test_without_loadout_nothing_is_drawn_placed_or_read(small, ui_contexts):
    page = page_on(ui_contexts, small)
    item = opened(page, "atlas-api")
    expect(item.locator('[data-label="Folder"]')).to_be_visible()
    page.wait_for_timeout(DRAWN_MS)
    # Only the rows projects draws itself, each with its words.
    assert labels(item) == ["Folder", "Agents"]
    for label in labels(item):
        assert item.locator(f'[data-label="{label}"] .ol').inner_text().strip(), label
    expect(item.locator('[data-part="opened"]')).not_to_contain_text("Loadout")
    host = page.locator('#moxi-panel > moxi-projects-page [data-part="facts"]')
    expect(host.locator("moxi-loadout-project-facts")).to_have_count(0)
    assert loadout_reads(page) == [], page.asked
