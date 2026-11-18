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

The page's acts write only once the shell has a record of the user's terminal (`typed`).
"""

from __future__ import annotations

import os
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


#: What the small install holds beside the core: ui and repo, and the demo pieces.
SMALL_INSTALL = ("ui", "repo", "demo")


def small_options(tmp_path_factory, hook_env: dict) -> dict:
    """`shell_options` for the small install, the core, ui and repo: the shell's snapshot
    leaves every other module of this environment out, and a site first on its
    `PYTHONPATH` holds a distribution of each such name that declares no module, so repo,
    its page server and every `moxi` command the shell runs find none of them, as on a
    machine without them."""
    from moxi import modules

    gone = sorted(found.name for found in modules.scan() if found.name not in SMALL_INSTALL)
    site = tmp_path_factory.mktemp("small-site")
    for name in gone:
        info = site / f"moxi_{name}-0.0.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: moxi-{name}\nVersion: 0.0.0\n", encoding="utf-8")
    options = shell_options(tmp_path_factory, hook_env)
    return {**options, "site": [str(site), *options["site"]], "leave_out": gone}


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


def own_shell(ui_shells, options: dict):
    """A shell of a test's own, started in the test, whose home is the folder the test's
    fence anchors: its repos then lie under its home and are named in the `~` form."""
    return ui_shells(home=Path(os.environ["MOXI_ANCHOR_HOME"]), **options)


def hooked(shell, hook_env: dict, repo: Path, *pieces: str) -> None:
    """Adds `pieces` to `repo` with the hook environment's Python, so its commits run the
    demo pieces."""
    moxi(shell, "repo", "git-hooks", "add", str(repo), *pieces, python=hook_env["python"])


def typed(shell) -> None:
    """Moxi's record of the terminal, as a command typed in the user's own terminal makes
    it: the page's acts refuse to write without one."""
    from moxi import paths, terminal

    terminal.write(shell.env, path=paths.terminal_record(shell.env))


def commit(shell, repo: Path, name: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Writes `name` in `repo` and commits it; the commit may be refused."""
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    git(shell, repo, "add", name)
    return git(shell, repo, "commit", "-q", "-m", f"add {name}", env=env, check=False)


def git_version() -> str:
    """The version of the git on PATH, as the page writes it."""
    return subprocess.run(["git", "version"], capture_output=True, text=True,
                          check=True).stdout.split()[2]


def records_file(shell) -> Path:
    """repo's doctor records, under the shell's Moxi home."""
    return Path(shell.env["MOXI_HOME"]) / "state" / "repo" / "doctor.json"


def at_of(page, at: str) -> str:
    """A record's `at` as the page writes a time today."""
    return page.evaluate("at => new Date(Date.parse(at)).toLocaleTimeString([], "
                         "{hour: '2-digit', minute: '2-digit'})", at)


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
    - my-loadout: commit-rules, and its own pre-commit hook with no piece before it.
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
    own = repos["my-loadout"] / ".git" / "hooks" / "pre-commit"
    own.write_text(OWN_HOOK, encoding="utf-8")
    own.chmod(0o755)
    return repos


# -- colours ---------------------------------------------------------------------------------

#: The colour tokens the page may draw with anywhere: the text's and the surface's.
TEXT_TOKENS = {"text", "text-dim", "text-faint", "border", "border-strong", "bg", "bg-raised",
               "bg-sunken", "bg-hover"}
#: For each colour token the shell's style sheets define, the nodes under the page whose
#: colours change when that token alone changes on the page's element, and in which
#: property; a node inside a `Turn off` block is marked `off:`. The walk goes into every
#: shadow tree under the page. Focus is taken off first, and the outline's colour is left
#: out: a focus ring takes the accent on every element.
REACHED = """async () => {
  const panel = document.querySelector("#moxi-panel > moxi-repo-page");
  function* tree(root) {
    for (const node of root.querySelectorAll("*")) {
      yield node;
      if (node.shadowRoot) yield* tree(node.shadowRoot);
    }
  }
  let focused = document.activeElement;
  while (focused?.shadowRoot?.activeElement) focused = focused.shadowRoot.activeElement;
  focused?.blur?.();
  const nodes = [panel, ...tree(panel.shadowRoot), ...tree(panel)];
  const props = ["color", "background-color", "background-image", "border-top-color",
                 "border-right-color", "border-bottom-color", "border-left-color",
                 "text-decoration-color", "box-shadow", "fill", "stroke", "stop-color"];
  const frames = () => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
  const colours = () => nodes.map((node) => {
    const style = getComputedStyle(node);
    return props.map((name) => style.getPropertyValue(name));
  });
  const names = new Set();
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch { continue; }
    for (const rule of rules) {
      if (!rule.style) continue;
      for (const name of rule.style) if (name.startsWith("--moxi-")) names.add(name.slice(7));
    }
  }
  const what = (node) => (node.closest?.("[data-part=turn-off]") ? "off:" : "") + node.localName
    + (node.dataset?.action ? `[${node.dataset.action}]` : "")
    + (node.className && typeof node.className === "string" ? `.${node.className.split(" ").join(".")}` : "");
  await frames();
  const before = colours();
  const reached = {};
  for (const token of [...names].sort()) {
    if (/^(font|radius|gap)/.test(token)) continue;
    panel.style.setProperty(`--moxi-${token}`, "rgb(1, 2, 3)");
    await frames();
    const after = colours();
    panel.style.removeProperty(`--moxi-${token}`);
    const changed = [];
    after.forEach((seen, n) => seen.forEach((value, i) => {
      if (value !== before[n][i]) changed.push(`${what(nodes[n])} ${props[i]}`);
    }));
    if (changed.length) reached[token] = [...new Set(changed)];
  }
  await frames();
  return {reached, text: getComputedStyle(panel).color};
}"""


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
