"""The Git hooks page in a browser on the small install, the core, ui and repo (with the
demo pieces), through a real shell that starts repo's real page server: no other module
is installed, so

- each repo is named by its `~` folder, and no line names projects with no hooks;
- an opened repo has no `Project` link, and its foot still offers `Add a piece` and
  `Refresh`;
- a `project/<key>` address names no repo, so the page opens with the line at its foot;
- the page fits 1280 and 400 px with a repo opened.
"""

from __future__ import annotations

import re
import sys

import pytest
import repo_page_kit as kit
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="repo's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory, hook_env) -> dict:
    return kit.small_options(tmp_path_factory, hook_env)


@pytest.fixture(scope="module")
def repos(ui_shell) -> dict:
    made = {name: kit.make_repo(ui_shell, f"code/{name}") for name in ("atlas-api", "quill-mobile")}
    kit.moxi(ui_shell, "repo", "git-hooks", "add", str(made["atlas-api"]), "repo/commit-rules",
             "demo/gate")
    # No projects module: the command isn't there.
    assert kit.moxi(ui_shell, "projects", "list", check=False).returncode != 0
    return made


@pytest.fixture
def page(repos, ui_contexts):
    return ui_contexts().pages[0]


def test_repos_take_the_home_form_with_no_project_line_or_link(repos, page):
    panel = kit.open_page(page)
    atlas = repos["atlas-api"]
    expect(panel.locator("[data-group=repos] moxi-ui-row")).to_have_count(1)
    expect(kit.row(panel, atlas)).to_have_attribute("name", "~/code/atlas-api")
    expect(kit.row(panel, atlas)).to_have_attribute("where", "gate · commit-rules")
    expect(panel.locator("[data-line=no-hooks]")).to_have_count(0)
    expect(panel.locator("[data-line=verdict]")).to_have_text(re.compile(r"^Every repo runs its hooks\."))
    opened = kit.opened(panel, atlas)
    foot = opened.locator("[data-part=foot]")
    expect(foot.locator("[data-action=add-piece]")).to_be_visible()
    expect(foot.locator("[data-action=refresh]")).to_be_visible()
    expect(foot.locator("[data-action=project]")).to_have_count(0)


def test_a_project_address_names_no_repo(repos, page):
    panel = kit.open_page(page, "project/atlas-api")
    expect(panel.locator("[data-line=unknown]")).to_have_text("No repo is named atlas-api.")
    expect(panel.locator("[data-group=repos] moxi-ui-row[open]")).to_have_count(0)


@pytest.mark.parametrize("width", [1280, 400])
def test_the_small_install_fits_the_width(repos, page, ui_width_check, width):
    page.set_viewport_size({"width": width, "height": 900})
    panel = kit.open_page(page, "repo/~!2Fcode!2Fatlas-api")
    expect(kit.row(panel, repos["atlas-api"])).to_have_attribute("open", "")
    ui_width_check(page, width=width)
    panel = kit.open_page(page, "pieces")
    ui_width_check(page, width=width)
