"""repo's page in a browser, through a real shell that starts repo's real page server: the
contract check, a repo with notes' git hook pieces and a saved spec whose push failed,
the empty state, a failed read, "Read again", copying a command, and the 360 px width
check.

The repos are made with the shell's own environment, so repo's index and records are
under the shell's home. Git there reads a global config of the test's own and no system
config.
"""

from __future__ import annotations

import importlib.util
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
EMPTY = ("No repo has git hook pieces yet. See the pieces installed modules declare with "
         "moxi repo git-hooks available, and add one with moxi repo git-hooks add PATH PIECE.")


def env_of(shell) -> dict[str, str]:
    """The shell's environment, with git's global config in its home and no system one."""
    home = Path(shell.env["HOME"])
    gitconfig = home / "gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text("[user]\n\tname = sample-bot\n\temail = bot@example.invalid\n"
                             "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    return {**shell.env, "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}


def run(shell, *argv: str, cwd: Path | None = None, check: bool = True):
    done = subprocess.run(list(argv), cwd=cwd, env=env_of(shell), capture_output=True,
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


def section(panel, repo: Path):
    return panel.locator(f'section[data-repo="{repo}"]')


def test_the_page_keeps_the_contract(notes_repo, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "repo", "page")
    assert check.element == "moxi-repo-page"
    assert any(REPOS in url for url in check.requests), check.requests


def test_the_page_shows_the_repos_events_pieces_spec_and_record(notes_repo, ui_page):
    panel = open_panel(ui_page)
    expect(panel.locator("h1")).to_have_text("Git hooks")
    expect(panel.locator("div[data-state]")).to_have_attribute("data-state", "up")
    repo = section(panel, notes_repo)
    expect(repo.locator("h2")).to_have_text(str(notes_repo))
    expect(repo.locator('[data-line="facts"]')).to_contain_text("config mode")
    pre_commit = repo.locator('li[data-event="pre-commit"]')
    if NOTES:
        expect(pre_commit.locator("[data-piece] > .first > code")).to_have_text(
            ["notes/secret-gate"])
        expect(pre_commit).to_contain_text("run by git from a config entry")
        expect(pre_commit.locator('[data-line="existing"]')).to_have_text(
            "No existing git hook.")
        expect(repo.locator('li[data-event="pre-push"] [data-piece]')).to_have_count(1)
    expect(repo.locator('li[data-event="commit-msg"] [data-piece="repo/commit-rules"]')
           ).to_contain_text("fail closed")
    spec = repo.locator(f'li[data-spec="{SPEC}"]')
    expect(spec.locator("[data-status]")).to_have_text("committed")
    expect(spec).to_contain_text("Last commit: ")
    expect(spec).to_contain_text("Push of main failed")
    expect(spec).to_contain_text("remote origin is not configured")
    record = repo.locator('[data-part="records"] li[data-record="push"]')
    expect(record).to_contain_text(f"spec {SPEC}, push of main")
    expect(repo.locator('[data-part="problems"]')).to_have_count(0)


def test_a_command_is_copied_not_run(notes_repo, ui_contexts):
    context = ui_contexts(permissions=["clipboard-read", "clipboard-write"])
    page = context.pages[0]
    panel = open_panel(page)
    spec = section(panel, notes_repo).locator(f'li[data-spec="{SPEC}"]')
    spec.locator("details > summary").click()
    run_line = spec.locator("code[data-copy$=':Run']")
    expect(run_line).to_have_text(f"moxi repo commit {notes_repo} --spec {SPEC}")
    spec.locator("button[data-action=copy]").first.click()
    expect(spec.get_by_text("Copied.")).to_be_visible()
    assert page.evaluate("navigator.clipboard.readText()") == (
        f"moxi repo commit {notes_repo} --spec {SPEC}")


def test_read_again_shows_a_piece_disabled_from_the_command_line(notes_repo, ui_shell, ui_page):
    panel = open_panel(ui_page)
    piece = section(panel, notes_repo).locator('[data-piece="repo/commit-rules"]')
    expect(piece).not_to_contain_text("disabled")
    moxi(ui_shell, "git-hooks", "disable", str(notes_repo), "repo/commit-rules")
    try:
        panel.get_by_role("button", name="Read again").click()
        expect(piece).to_contain_text("disabled")
        expect(section(panel, notes_repo).locator('li[data-event="commit-msg"]')).to_contain_text(
            "every piece disabled")
    finally:
        moxi(ui_shell, "git-hooks", "enable", str(notes_repo), "repo/commit-rules")


@pytest.mark.parametrize("status", [500, 401])
def test_a_failed_read_shows_the_failure_line(notes_repo, ui_page, status):
    ui_page.route("**" + REPOS, lambda route: route.fulfill(status=status, json={"error": "no"}))
    panel = open_panel(ui_page)
    state = panel.locator("div[data-state]")
    expect(state).to_have_attribute("data-state", "failed")
    expect(state).to_have_text(f"The repos can't be read: repo's page server answered {status}.")
    expect(panel.locator("section[data-repo]")).to_have_count(0)


def test_with_no_repo_the_page_says_how_to_add_one(ui_shells, ui_contexts):
    shell = ui_shells()
    page = ui_contexts(shell).pages[0]
    panel = open_panel(page)
    state = panel.locator("div[data-state]")
    expect(state).to_have_attribute("data-state", "empty")
    expect(state).to_have_text(EMPTY)


def test_the_page_fits_a_phone(notes_repo, ui_page, ui_width_check):
    ui_width_check(ui_page, module="repo")
    panel = ui_page.locator("#moxi-panel > moxi-repo-page")
    expect(panel.locator("section[data-repo]")).to_have_count(1)
    for summary in panel.locator("details > summary").all():
        summary.click()
    ui_width_check(ui_page)
