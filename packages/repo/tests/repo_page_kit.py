"""Helpers the Git hooks page's browser tests share: a test shell over the demo pieces,
repos made inside the folder the shell's `MOXI_ANCHOR_HOME` names, `moxi` and git as the
user would type them, and the page's parts.

Every repo here is made under the test's tmp, inside the shell's anchor folder, so the
page's acts may write to it; none is a real repo. git runs with the test's own global
config and no system config, in the test's commands and the shell's page servers alike.

A git hook written by `moxi` run with the shell's Python runs repo's own pieces only: the
hook starts `python -I`, which doesn't see the demo site. `hooked` writes a repo's entries
with the hook environment's Python instead (`conftest.hook_env`), so a commit there runs
the demo pieces, each doing what its `DEMO_<NAME>` variable says.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect

PANEL = "#moxi-panel > moxi-repo-page"
#: A module of the test's own whose one closed piece is registered in a repo while a site
#: holds it, then left with no module declaring it.
OLD_MODULE = "oldmod"
OLD_PIECE = "oldmod/prose-check"
OLD_INIT = '''
PROSE = {"event": "pre-commit", "target": "moxi_oldmod_pieces:check", "fail": "closed",
         "order": 30, "doc": "Checks prose."}


def check(ctx):
    return None
'''


def shell_options(tmp_path_factory, hook_env: dict) -> dict:
    """The demo pieces declared for the shell and every process it starts, and git with
    the test's own global config and no system config."""
    gitconfig = tmp_path_factory.mktemp("git") / "gitconfig"
    gitconfig.write_text("[user]\n\tname = sample-bot\n\temail = bot@example.invalid\n"
                         "[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n",
                         encoding="utf-8")
    return {"site": [str(hook_env["site"])],
            "env": {"GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}}


def old_site(tmp_path_factory) -> Path:
    """A site declaring `oldmod/prose-check`, put on `PYTHONPATH` only for the add that
    registers it."""
    site = tmp_path_factory.mktemp("old-site")
    (site / "moxi_oldmod_pieces.py").write_text(OLD_INIT, encoding="utf-8")
    info = site / "moxi_oldmod-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: moxi-oldmod\nVersion: 0.1.0\n",
                                   encoding="utf-8")
    (info / "entry_points.txt").write_text(
        "[moxi.repo.git_hooks]\noldmod.prose-check = moxi_oldmod_pieces:PROSE\n",
        encoding="utf-8")
    return site


def run(shell, *argv: str, cwd: Path | None = None, env: dict | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    """A command in the shell's environment, with `env` added."""
    assert shell.env["GIT_CONFIG_NOSYSTEM"] == "1" and "GIT_CONFIG_GLOBAL" in shell.env
    done = subprocess.run(list(argv), cwd=cwd or shell.env["HOME"],
                          env={**shell.env, **(env or {})}, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)
    if check and done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done


def moxi(shell, *args: str, python: str | Path | None = None, env: dict | None = None,
         check: bool = True) -> subprocess.CompletedProcess:
    """`moxi ARGS` as the user would type it beside the shell; `python` runs it with
    another environment's Python, whose path the hooks it writes then start."""
    return run(shell, str(python or sys.executable), "-m", "moxi", *args, env=env,
               check=check)


def git(shell, repo: Path, *args: str, env: dict | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    return run(shell, "git", *args, cwd=repo, env=env, check=check)


def anchor(shell) -> Path:
    """The folder the shell's `MOXI_ANCHOR_HOME` names: the page acts on a repo inside it."""
    return Path(shell.env["MOXI_ANCHOR_HOME"])


def make_repo(shell, rel: str) -> Path:
    """A repo with one commit at `rel` inside the anchor folder."""
    repo = anchor(shell) / rel
    repo.parent.mkdir(parents=True, exist_ok=True)
    git(shell, repo.parent, "init", "-q", repo.name)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    git(shell, repo, "add", "README.md")
    git(shell, repo, "commit", "-q", "-m", "start")
    return repo


def name_of(shell, path: Path) -> str:
    """`path` as the page names a repo with no project: in the `~` form under the home."""
    home = Path(shell.env["HOME"])
    return f"~/{path.relative_to(home).as_posix()}" if path.is_relative_to(home) else str(path)


def objects(repo: Path) -> list[str]:
    """Every file in the repo's object store, so a test can see that none was added."""
    store = repo / ".git" / "objects"
    return sorted(str(p.relative_to(store)) for p in store.rglob("*") if p.is_file())


def open_page(page, route: str = ""):
    """The page at `/m/repo/<route>`, read; returns its panel."""
    page.goto("/m/repo/" + route)
    panel = page.locator(PANEL)
    expect(panel.locator("section[data-part=page]")).to_have_attribute("data-state", "up")
    return panel


def row(panel, root: Path):
    return panel.locator(f'[data-group=repos] moxi-ui-row[data-key="{root}"]')


def opened(panel, root: Path):
    """The repo's row opened with its `Open`; returns its opened part."""
    one = row(panel, root)
    one.locator("button[data-action=open]").click()
    expect(one).to_have_attribute("open", "")
    return one.locator("[data-part=opened]")


def item(panel, title: str):
    """The `Hooks` item whose title is `title`."""
    return panel.locator("[data-group=hooks] .prob").filter(
        has=panel.page.locator("[data-line=title]", has_text=title))


# -- the cast ------------------------------------------------------------------------------

#: atlas-api's tag pattern, a setting of commit-rules there.
TAG_PATTERN = r"\bATL-[0-9]+\b"
#: harbor-infra's own pre-commit hook, which git runs after the pieces.
OWN_HOOK = "#!/bin/sh\n# lint terraform files\nexit 0\n"


def seed(shell, hook_env: dict, old: Path) -> dict[str, Path]:
    """Five repos inside the anchor folder, under `code/`:

    - atlas-api: commit-rules with a tag pattern, commit-proof, the gate and the push
      check;
    - harbor-infra: the gate and commit-rules, and its own pre-commit hook;
    - kestrel-web: the gate turned off, and commit-rules;
    - lumen-docs: the open lint check, which broke on a commit, and `oldmod/prose-check`,
      a closed piece no installed module declares, which refuses every commit;
    - my-loadout: commit-rules alone.
    """
    repos = {name: make_repo(shell, f"code/{name}") for name in
             ("atlas-api", "harbor-infra", "kestrel-web", "lumen-docs", "my-loadout")}
    add = ("repo", "git-hooks", "add")
    moxi(shell, *add, str(repos["atlas-api"]), "repo/commit-rules", "repo/commit-proof",
         "demo/gate", "demo/push-check", "--set", f"tag-pattern={TAG_PATTERN}")
    hook = repos["harbor-infra"] / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(OWN_HOOK, encoding="utf-8")
    hook.chmod(0o755)
    moxi(shell, *add, str(repos["harbor-infra"]), "demo/gate", "repo/commit-rules")
    moxi(shell, *add, str(repos["kestrel-web"]), "demo/gate", "repo/commit-rules")
    moxi(shell, "repo", "git-hooks", "disable", str(repos["kestrel-web"]), "demo/gate")
    lumen = repos["lumen-docs"]
    moxi(shell, *add, str(lumen), "demo/lint", python=hook_env["python"])
    (lumen / "guide.md").write_text("a guide\n", encoding="utf-8")
    git(shell, lumen, "add", "guide.md")
    git(shell, lumen, "commit", "-q", "-m", "add the guide", env={"DEMO_LINT": "raise"})
    sites = [p for p in shell.env.get("PYTHONPATH", "").split(":") if p]
    moxi(shell, *add, str(lumen), OLD_PIECE, env={"PYTHONPATH": ":".join([*sites, str(old)])})
    moxi(shell, *add, str(repos["my-loadout"]), "repo/commit-rules")
    return repos


# -- copying ---------------------------------------------------------------------------------

def clipboard_args(browser) -> dict:
    """The context options that let a test read the clipboard: Chromium's alone."""
    if browser.browser_type.name != "chromium":
        return {}
    return {"permissions": ["clipboard-read", "clipboard-write"]}


def copied(page, button, text: str) -> None:
    """`button` puts `text` on the clipboard and reads `Copied`, then `Copy` again."""
    button.click()
    expect(button).to_have_text("Copied")
    if page.context.browser.browser_type.name == "chromium":
        assert page.evaluate("navigator.clipboard.readText()") == text
    expect(button).to_have_text("Copy")
