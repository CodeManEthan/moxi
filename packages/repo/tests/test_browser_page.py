"""repo's page in a browser, through a real shell that starts repo's real page server: the
contract check, a repo with notes' git hook pieces and a saved spec whose push failed drawn
as a row, the empty page, a failed read, and the 360 px width check.

The repos are made with the shell's own environment, so repo's index and records are
under the shell's home. The shell is given a global git config of the test's own and no
system config, so the test's git commands and the page server's read the same config, and
none of the machine's.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="repo's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

REPOS = "/m/repo/api/repos"
SPEC = "notes/snapshot"
NOTES = importlib.util.find_spec("moxi_notes") is not None
#: notes' two gates when notes is installed beside repo, and repo's own piece always.
PIECES = (["notes/secret-gate", "notes/push-gate"] if NOTES else []) + ["repo/commit-rules"]
SAVE = """import sys, moxi_repo
moxi_repo.save_spec(moxi_repo.Spec(root=sys.argv[1], name=sys.argv[2], paths=("notes",),
                                   author=("snap-bot", "snap@example.invalid"), push="origin"))
"""


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    """Every shell here runs git, its page servers included, with the test's own global
    config and no system config."""
    gitconfig = tmp_path_factory.mktemp("git") / "gitconfig"
    gitconfig.write_text("[user]\n\tname = sample-bot\n\temail = bot@example.invalid\n"
                         "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    return {"env": {"GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}}


def run(shell, *argv: str, cwd: Path | None = None, check: bool = True):
    assert shell.env["GIT_CONFIG_NOSYSTEM"] == "1" and "GIT_CONFIG_GLOBAL" in shell.env
    done = subprocess.run(list(argv), cwd=cwd, env=shell.env, capture_output=True,
                          text=True, timeout=120)
    if check and done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done


def moxi(shell, *args: str, check: bool = True):
    return run(shell, sys.executable, "-m", "moxi", "repo", *args, check=check)


@pytest.fixture(scope="module")
def notes_repo(ui_shell) -> Path:
    """A repo with notes' pieces and repo's own registered, and the saved spec
    `notes/snapshot`, which has committed once and failed to push: no remote is set."""
    repo = Path(ui_shell.env["HOME"]) / "alpha-notes"
    run(ui_shell, "git", "init", "-q", str(repo))
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    run(ui_shell, "git", "add", "README.md", cwd=repo)
    run(ui_shell, "git", "commit", "-q", "-m", "start", cwd=repo)
    moxi(ui_shell, "git-hooks", "add", str(repo), *PIECES)
    run(ui_shell, sys.executable, "-c", SAVE, str(repo), SPEC)
    (repo / "notes").mkdir()
    (repo / "notes" / "a.md").write_text("a note\n", encoding="utf-8")
    assert moxi(ui_shell, "commit", str(repo), "--spec", SPEC, check=False).returncode == 1
    return repo


def open_panel(page):
    page.goto("/m/repo/")
    return page.locator("#moxi-panel > moxi-repo-page")


def test_the_page_keeps_the_contract(notes_repo, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "repo", "page")
    assert check.element == "moxi-repo-page"
    assert any(REPOS in url for url in check.requests), check.requests


def test_a_repo_is_a_row_named_by_its_home_folder(notes_repo, ui_page):
    panel = open_panel(ui_page)
    expect(panel.locator("section[data-part=page]")).to_have_attribute("data-state", "up")
    row = panel.locator(f'[data-group=repos] moxi-ui-row[data-key="{notes_repo}"]')
    expect(row).to_have_attribute("name", "~/alpha-notes")
    expect(row).to_have_attribute("where", re.compile("commit-rules"))
    row.locator("button[data-action=open]").click()
    expect(row).to_have_attribute("open", "")
    ui_page.wait_for_url("**/m/repo/repo/~%2Falpha-notes")


@pytest.mark.parametrize("status", [500, 401])
def test_a_failed_read_says_git_hooks_cant_be_read(notes_repo, ui_page, status):
    ui_page.route("**" + REPOS, lambda route: route.fulfill(status=status, json={"error": "no"}))
    panel = open_panel(ui_page)
    page = panel.locator("section[data-part=page]")
    expect(page).to_have_attribute("data-state", "down")
    expect(page.locator("[data-line=failed]")).to_have_text("Git hooks can't be read right now.")
    expect(panel.locator("[data-group]")).to_have_count(0)


def test_with_no_repo_the_verdict_says_so(ui_shells, ui_shell_options, ui_contexts):
    shell = ui_shells(**ui_shell_options)
    page = ui_contexts(shell).pages[0]
    panel = open_panel(page)
    expect(panel.locator("[data-line=verdict]")).to_have_text("No repo has a hook yet.")
    expect(panel.locator("[data-group=repos] moxi-ui-row")).to_have_count(0)


def test_the_page_fits_a_phone(notes_repo, ui_page, ui_width_check):
    ui_width_check(ui_page, module="repo")
    panel = ui_page.locator("#moxi-panel > moxi-repo-page")
    expect(panel.locator("[data-group=repos] moxi-ui-row")).to_have_count(1)
    for show in panel.locator("button[data-action^=show]").all():
        show.click()
    ui_width_check(ui_page)
