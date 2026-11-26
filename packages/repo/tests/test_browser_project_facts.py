"""repo's facts on the Projects page in a browser, through a real shell that starts the real
page servers, on two installs:

- The full one: the core, ui, projects, sessions, usage, comms and repo. atlas-api's repo
  runs repo's `commit-rules` and holds a saved spec whose push failed: its closed row says,
  heavy, `a git hook broke at <time>`, and opened, its `Git hooks` row says that with the
  push's words, then its pieces, linking to repo's page for the project, which lands on
  atlas-api's repo opened. kestrel-web's repo refused a commit: a refusal is a gate doing
  its job, so its closed row says nothing and its opened row says the refusal, not heavy.
  A read that fails after a good one leaves no piece of repo's, and the next good read puts
  them back.
- The same without repo: no `Git hooks` row, nothing on a closed row, and nothing of
  repo's is read.

The repos are made with the shell's own environment, so repo's records are under the
shell's home where its page server reads them. git runs with the test's own global config
and no system config, in the test's commands and the page servers alike.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="the Projects page is checked in a browser on Linux; "
                                        "the Windows and macOS runs are owed")]

#: The modules the full install holds besides the core and ui.
FULL = ("projects", "sessions", "usage", "comms", "repo")
REPOS = re.compile(r"/m/repo/api/repos$")
SPEC = "notes/snapshot"
BROKE = re.compile(r"^a git hook broke at \S")
SAVE = """import sys, moxi_repo
moxi_repo.save_spec(moxi_repo.Spec(root=sys.argv[1], name=sys.argv[2], paths=("notes",),
                                   author=("snap-bot", "snap@example.invalid"),
                                   push=sys.argv[3] or None))
"""
#: A git hook of the test's own that refuses every commit, as a gate would.
REFUSE = "#!/bin/sh\necho 'the gate says no' >&2\nexit 1\n"

#: Each module's distribution that declares no module, so a page server finds none.
_EMPTY = "Metadata-Version: 2.1\nName: moxi-{name}\nVersion: 0.0.0\n"


def install(tmp_path_factory, holds: tuple[str, ...]) -> dict:
    """`ui_shells` options for an install of the core, ui and `holds` only: the shell's
    snapshot leaves every other module out, and a site first on its path holds an empty
    distribution of each, so no process the shell starts finds them. git reads the test's
    own global config and no system config."""
    from moxi import modules

    out = [found.name for found in modules.scan() if found.name not in ("ui", *holds)]
    site = tmp_path_factory.mktemp("site")
    for name in out:
        info = site / f"moxi_{name}-0.0.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(_EMPTY.format(name=name), encoding="utf-8")
    gitconfig = tmp_path_factory.mktemp("git") / "gitconfig"
    gitconfig.write_text("[user]\n\tname = sample-bot\n\temail = bot@example.invalid\n"
                         "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    return {"site": [str(site)], "leave_out": out,
            "env": {"GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}}


def run(shell, *argv: str, cwd: Path | None = None, check: bool = True):
    assert shell.env["GIT_CONFIG_NOSYSTEM"] == "1" and "GIT_CONFIG_GLOBAL" in shell.env
    done = subprocess.run(list(argv), env=shell.env, cwd=cwd or shell.env["HOME"],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
    if check and done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done


def project_repo(shell, key: str) -> Path:
    """Project `key` at `~/code/<key>`, a git repo with one commit."""
    root = Path(shell.env["HOME"]) / "code" / key
    root.mkdir(parents=True)
    run(shell, sys.executable, "-m", "moxi", "projects", "add", key, "--root", str(root))
    run(shell, "git", "init", "-q", str(root))
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    run(shell, "git", "add", "README.md", cwd=root)
    run(shell, "git", "commit", "-q", "-m", "start", cwd=root)
    return root


def spec_commit(shell, root: Path, *, push: str) -> int:
    """The saved spec's pass over a new note in `root`; its exit code."""
    run(shell, sys.executable, "-c", SAVE, str(root), SPEC, push)
    (root / "notes").mkdir()
    (root / "notes" / "a.md").write_text("a note\n", encoding="utf-8")
    return run(shell, sys.executable, "-m", "moxi", "repo", "commit", str(root), "--spec", SPEC,
               check=False).returncode


def seed(shell, *, hooks: bool) -> dict[str, Path]:
    """atlas-api, whose repo runs `commit-rules` and whose spec's push failed (no remote is
    set), and kestrel-web, whose repo refused the spec's commit; both made through repo
    only when `hooks`."""
    atlas = project_repo(shell, "atlas-api")
    kestrel = project_repo(shell, "kestrel-web")
    if hooks:
        run(shell, sys.executable, "-m", "moxi", "repo", "git-hooks", "add", str(atlas), "repo/commit-rules")
        assert spec_commit(shell, atlas, push="origin") == 1
        hook = kestrel / ".git" / "hooks" / "pre-commit"
        hook.write_text(REFUSE, encoding="utf-8")
        hook.chmod(0o755)
        assert spec_commit(shell, kestrel, push="") == 3
    return {"atlas-api": atlas, "kestrel-web": kestrel}


@pytest.fixture(scope="module")
def full(ui_shells, tmp_path_factory):
    shell = ui_shells(**install(tmp_path_factory, FULL))
    shell.roots = seed(shell, hooks=True)
    return shell


@pytest.fixture(scope="module")
def without_repo(ui_shells, tmp_path_factory):
    shell = ui_shells(**install(tmp_path_factory, tuple(name for name in FULL if name != "repo")))
    shell.roots = seed(shell, hooks=False)
    return shell


def page_on(ui_contexts, shell):
    """A fresh page on `shell`, keeping the path of every request it makes in `page.asked`."""
    page = ui_contexts(shell).pages[0]
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url.split("://", 1)[1].split("/", 1)[1]))
    page.asked = asked
    return page


def row(page, key: str):
    return page.locator(f'#moxi-panel > moxi-projects-page moxi-ui-row[data-key="{key}"]')


def opened(page, key: str):
    page.goto(f"/m/projects/open/{key}")
    item = row(page, key)
    expect(item).to_have_attribute("open", "")
    return item


def hooks_row(item):
    return item.locator('[data-part="opened"] [data-label="Git hooks"]')


def shown_again(page) -> None:
    """The document hidden and shown again, which makes each placed component read once."""
    page.evaluate("""() => {
      for (const state of ["hidden", "visible"]) {
        Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
        document.dispatchEvent(new Event("visibilitychange"));
      }
    }""")


def repo_reads(page) -> list[str]:
    return ["/" + path.split("?", 1)[0] for path in page.asked if path.startswith("m/repo/")]


def test_a_broken_hook_is_heavy_on_the_closed_row_and_opened_with_its_pieces_and_link(full, ui_contexts):
    page = page_on(ui_contexts, full)
    page.goto("/m/projects/")
    expect(row(page, "atlas-api")).to_have_attribute("lead", BROKE)
    expect(row(page, "atlas-api")).to_have_attribute("lead-heavy", "")
    item = opened(page, "atlas-api")
    lines = hooks_row(item).locator(".ol p")
    expect(lines).to_have_count(2)
    expect(lines.nth(0).locator("strong")).to_have_text(BROKE)
    expect(lines.nth(1)).to_have_text("commit-rules")
    expect(lines.nth(1).locator("strong")).to_have_count(0)
    # Of repo's, the page reads only the component's script and /api/repos.
    assert set(repo_reads(page)) <= {"/m/repo/project-facts.js", "/m/repo/api/repos"}, page.asked

    link = hooks_row(item).locator("a")
    expect(link).to_have_text("Git hooks")
    expect(link).to_have_attribute("href", "/m/repo/project/atlas-api")
    link.click()
    panel = page.locator("#moxi-panel > moxi-repo-page")
    expect(panel.locator(f'[data-group=repos] moxi-ui-row[data-key="{full.roots["atlas-api"]}"]')) \
        .to_have_attribute("open", "")
    expect(page).to_have_url(re.compile(r"/m/repo/repo/atlas-api$"))


def test_a_refusal_is_said_opened_but_is_not_breakage(full, ui_contexts):
    page = page_on(ui_contexts, full)
    item = opened(page, "kestrel-web")
    lines = hooks_row(item).locator(".ol p")
    expect(lines.first).to_contain_text("refused")
    expect(hooks_row(item).locator("strong")).to_have_count(0)
    expect(item).not_to_have_attribute("lead", re.compile("broke"))
    expect(item).not_to_have_attribute("lead-heavy", "")


def test_a_read_failing_after_a_good_one_leaves_no_piece_and_the_next_puts_them_back(full, ui_contexts):
    page = page_on(ui_contexts, full)
    item = opened(page, "atlas-api")
    expect(hooks_row(item)).to_have_count(1)
    expect(item).to_have_attribute("lead", BROKE)
    page.route(REPOS, lambda route: route.fulfill(status=500, body="no"))
    shown_again(page)
    expect(hooks_row(item)).to_have_count(0)
    expect(item).not_to_have_attribute("lead", BROKE)
    page.unroute(REPOS)
    shown_again(page)
    expect(hooks_row(item)).to_have_count(1)
    expect(item).to_have_attribute("lead", BROKE)


def test_without_repo_nothing_is_drawn_and_nothing_of_repo_is_read(without_repo, ui_contexts):
    page = page_on(ui_contexts, without_repo)
    item = opened(page, "atlas-api")
    expect(item.locator('[data-label="Folder"]')).to_be_visible()
    page.wait_for_timeout(1_000)
    expect(hooks_row(item)).to_have_count(0)
    expect(item).not_to_have_attribute("lead", BROKE)
    assert repo_reads(page) == [], page.asked
