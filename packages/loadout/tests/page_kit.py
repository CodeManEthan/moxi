"""Helpers loadout's page tests in a browser share: the seeded home they run on, `moxi`
as the user would type it, and the page's parts.

The seeded home holds the loadout the user keeps in `~/my-loadout`, at the moment the
page is read:

- Your items: the skills `tidy-notes` (every harness), `review-diff` (Claude Code and
  Codex), `write-changelog` (off for Codex) and `release-checklist` (listed since the
  last install, so not placed); the agent `code-reviewer`, whose Codex file was edited by
  hand; the hook `guard-rm` (Claude Code and Codex); the permission rule `git-reads`; and
  the global file, managed.
- The project `atlas-api`, its section named by key: the skill `release-checklist` and
  the agent `api-lead`. With projects installed (`projects=True`) it is a project of
  the shell's and its section is placed; without, its section is refused and was never
  placed.
- The folder is a git work tree with `loadout.toml` and `skills/tidy-notes/SKILL.md`
  changed and not committed, and the skill `bump-version`, which no section lists.

Every `moxi` command here runs with the shell's own environment, so loadout's config,
ledger and every harness file lie under the shell's home.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from moxi_dev.testing import console_script
from playwright.sync_api import expect

#: The event loadout's page sends on the document when an act has ended.
CHANGED = "moxi-loadout-changed"
FAKE_CLI = ("import os, sys\n"
            "name = os.path.splitext(os.path.basename(sys.argv[0]))[0]\n"
            "sys.exit(f'fake {name}: no test here runs it')\n")
#: The manifest as last committed and installed.
INSTALLED = """\
[global]
mode = "managed"

[user]
skills = ["tidy-notes", "review-diff", "write-changelog"]
agents = ["code-reviewer"]
hooks = ["guard-rm"]
permissions = ["git-reads"]
disabled = ["skill:write-changelog@codex"]

[project.atlas-api]
skills = ["release-checklist"]
agents = ["api-lead"]

[skill.review-diff]
harnesses = ["claude", "codex"]

[hook.guard-rm]
event = "PreToolUse"
matcher = "Bash"
command = "python3 {source}/hooks/guard_rm.py"
harnesses = ["claude", "codex"]

[permissions.git-reads]
allow = [{ shell = ["git", "status"] }, { shell = ["git", "diff"] }]
"""
#: The manifest now: `release-checklist` added to Your items, not committed or installed.
NOW = INSTALLED.replace('"write-changelog"]', '"write-changelog", "release-checklist"]', 1)
GLOBAL_TEXT = "Be brief.\n"
#: The modules beside loadout that the small install doesn't hold.
SMALL_LEAVES_OUT = ("projects", "repo")


def shell_options(tmp_path_factory) -> dict:
    """A fake `claude` and `codex` first on the shell's `PATH`, which its page servers and
    their jobs inherit, and git with the test's own global config and no system config."""
    bin_dir = tmp_path_factory.mktemp("fake-clis")
    for name in ("claude", "codex"):
        console_script(bin_dir, name, FAKE_CLI)
    gitconfig = tmp_path_factory.mktemp("git") / "gitconfig"
    gitconfig.write_text("[user]\n\tname = sample-bot\n\temail = bot@example.invalid\n"
                         "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    return {"env": {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}}


def small_options(tmp_path_factory) -> dict:
    """`shell_options` for the small install, the core, ui and loadout: the shell's snapshot
    leaves projects and repo out, and a site first on its `PYTHONPATH` holds a
    distribution of each name that declares no module, so loadout, its page server and
    every `moxi` command the shell runs find neither, as on a machine without them."""
    site = tmp_path_factory.mktemp("small-site")
    for module in SMALL_LEAVES_OUT:
        info = site / f"moxi_{module}-0.0.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: moxi-{module}\nVersion: 0.0.0\n", encoding="utf-8")
    return {**shell_options(tmp_path_factory), "site": [str(site)],
            "leave_out": list(SMALL_LEAVES_OUT)}


def moxi(shell, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """`moxi ARGS` as the user would run it in a terminal with the shell's environment."""
    done = subprocess.run([sys.executable, "-m", "moxi", *args], env=shell.env,
                          cwd=shell.env["HOME"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)
    if check and done.returncode != 0:
        raise AssertionError(f"moxi {' '.join(args)} failed ({done.returncode}):\n"
                             f"{done.stdout}{done.stderr}")
    return done


def git(shell, work_dir: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work_dir), *args], env=shell.env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          check=True, timeout=60).stdout


def typed(shell) -> None:
    """Moxi's record of the terminal, as a command typed in the user's own terminal with
    the shell's environment makes it (`moxi` run here has no keyboard, so it records
    nothing itself)."""
    from moxi import paths, terminal

    terminal.write(shell.env, path=paths.terminal_record(shell.env))


def record_path(shell) -> Path:
    from moxi import paths

    return Path(paths.terminal_record(shell.env))


def skill(loadout_dir: Path, name: str, body: str = "Do the thing.\n") -> None:
    (loadout_dir / "skills" / name).mkdir(parents=True, exist_ok=True)
    (loadout_dir / "skills" / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\n{body}", encoding="utf-8")


def agent(loadout_dir: Path, name: str) -> None:
    (loadout_dir / "agents").mkdir(parents=True, exist_ok=True)
    (loadout_dir / "agents" / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: An agent.\n---\nYou help.\n", encoding="utf-8")


def status(shell) -> dict:
    return json.loads(moxi(shell, "loadout", "status", "--json", check=False).stdout)


def target_of(shell, kind: str, name: str, harness: str, scope: str = "user") -> Path:
    return next(Path(row["target"]) for row in status(shell)["rows"]
                if (row["scope"], row["kind"], row["name"], row["harness"])
                == (scope, kind, name, harness))


def seed(shell, *, projects: bool) -> Path:
    """The seeded home on `shell` (see the module doc); returns the loadout folder."""
    home = Path(shell.env["HOME"])
    loadout_dir = home / "my-loadout"
    for name in ("tidy-notes", "review-diff", "write-changelog", "release-checklist",
                 "bump-version"):
        skill(loadout_dir, name)
    for name in ("code-reviewer", "api-lead"):
        agent(loadout_dir, name)
    (loadout_dir / "hooks").mkdir()
    (loadout_dir / "hooks" / "guard_rm.py").write_text("print('ok')\n", encoding="utf-8")
    (loadout_dir / "global.md").write_text(GLOBAL_TEXT, encoding="utf-8")
    (loadout_dir / "loadout.toml").write_text(INSTALLED, encoding="utf-8")
    (home / "code" / "atlas-api").mkdir(parents=True)
    git(shell, loadout_dir, "init", "-q")
    git(shell, loadout_dir, "add", "-A")
    git(shell, loadout_dir, "commit", "-qm", "my loadout")
    moxi(shell, "config", "set", "loadout", "source", str(loadout_dir))
    # The OpenCode row loadout's setup adds to the core's harness table, as `moxi install
    # loadout` makes it.
    subprocess.run([sys.executable, "-c", "from moxi_loadout import harness_row; harness_row.add()"],
                   env=shell.env, cwd=shell.env["HOME"], stdin=subprocess.DEVNULL,
                   capture_output=True, text=True, timeout=60, check=True)
    if projects:
        moxi(shell, "projects", "add", "atlas-api", "--root", str(home / "code" / "atlas-api"))
    typed(shell)
    done = moxi(shell, "loadout", "install", check=False)
    assert done.returncode == (0 if projects else 3), done.stdout + done.stderr
    # Since then: the Codex form of code-reviewer edited by hand, release-checklist
    # listed for the user and tidy-notes changed, neither committed.
    generated = target_of(shell, "agent", "code-reviewer", "codex")
    with generated.open("a", encoding="utf-8") as fh:
        fh.write("# my own change\n")
    (loadout_dir / "loadout.toml").write_text(NOW, encoding="utf-8")
    with (loadout_dir / "skills" / "tidy-notes" / "SKILL.md").open("a", encoding="utf-8") as fh:
        fh.write("Keep it short.\n")
    return loadout_dir


def take_back_edit(shell) -> None:
    """The hand edit to the Codex form of code-reviewer taken back, so an act over Your
    items leaves no row it refuses."""
    generated = target_of(shell, "agent", "code-reviewer", "codex")
    generated.write_text(generated.read_text(encoding="utf-8").replace("# my own change\n", ""),
                         encoding="utf-8")


def open_page(page, route: str = ""):
    """loadout's page at `/m/loadout/<route>`, once its view is drawn."""
    page.goto(f"/m/loadout/{route}")
    panel = page.locator("#moxi-panel > moxi-loadout-page")
    expect(panel.locator('[data-part="groups"]')).to_be_visible()
    return panel


def item_key(kind: str, name: str, scope: str = "user") -> str:
    """The page's key of an item, as its line carries it in `data-item`."""
    return json.dumps([scope, kind, name], separators=(",", ":"))


def item_row(panel, kind: str, name: str, scope: str = "user"):
    """An item's line in the grid."""
    return panel.locator(f".ir[data-item='{item_key(kind, name, scope)}']")


def cell(panel, kind: str, name: str, harness: str, scope: str = "user"):
    return item_row(panel, kind, name, scope).locator(f'.c[data-harness="{harness}"]')


def job_line(locator, text, timeout_ms: int = 60_000) -> None:
    """The line under an act, inside `locator`, reads `text` once its job has ended."""
    expect(locator.locator('[data-part="job"] [data-line="job"]')).to_have_text(text,
                                                                             timeout=timeout_ms)


def displaced(home: Path, name: str) -> list[Path]:
    """Every copy named `name` under the core's displaced folder in `home`."""
    return sorted(p for p in home.rglob(name) if "displaced" in p.parts)


#: The colour tokens a page drawn in text tokens only may reach.
TEXT_TOKENS = {"text", "text-dim", "text-faint", "border", "border-strong", "bg", "bg-raised",
               "bg-sunken", "bg-hover"}
#: For each colour token the shell's style sheets define, the nodes under the page whose
#: colours change when that token alone changes on the page's element, and in which
#: property. The walk goes into every shadow tree under the page. Focus is taken off
#: first, and the outline's colour is left out: a focus ring takes the accent on every
#: element.
REACHED = """async () => {
  const panel = document.querySelector("#moxi-panel > moxi-loadout-page");
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
  const what = (node) => node.localName + (node.dataset?.action ? `[${node.dataset.action}]` : "")
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
#: The elements under the page that reach past the viewport's right edge, and the
#: document's horizontal scroll.
PAST_THE_EDGE = """() => {
  const edge = document.documentElement.clientWidth;
  const panel = document.querySelector("#moxi-panel > moxi-loadout-page");
  const past = [];
  const walk = (root) => { for (const node of root.querySelectorAll("*")) {
    const box = node.getBoundingClientRect();
    if (box.width > 0 && box.right > edge + 0.5) past.push(`${node.localName}.${node.className}`);
    if (node.shadowRoot) walk(node.shadowRoot);
  } };
  walk(panel.shadowRoot);
  return {past, scroll: document.documentElement.scrollWidth - edge};
}"""
#: At the viewport's width: each word of an item's name line drawn across two lines, each
#: harness head cut short, the pitch between the first two item lines of Your items, and
#: how far the first harness column starts from the grid's right edge.
GRID = """() => {
  const root = document.querySelector("#moxi-panel > moxi-loadout-page").shadowRoot;
  const broken = [];
  for (const name of root.querySelectorAll(".grid .ir .nm")) {
    for (const node of [name.firstChild, name.querySelector(".k")?.firstChild]) {
      if (!node || node.nodeType !== Node.TEXT_NODE) continue;
      for (const word of node.textContent.matchAll(/\\S+/g)) {
        const range = document.createRange();
        range.setStart(node, word.index);
        range.setEnd(node, word.index + word[0].length);
        const tops = new Set([...range.getClientRects()].map((box) => Math.round(box.top)));
        if (tops.size > 1) broken.push(word[0]);
      }
    }
  }
  const cut = [...root.querySelectorAll(".grid .ir.hd .c")].filter((c) => c.scrollWidth > c.clientWidth)
    .map((c) => c.textContent);
  const lines = [...root.querySelectorAll('[data-group="user"] .grid .ir[data-item]')];
  const pitch = Math.round(lines[1].getBoundingClientRect().top - lines[0].getBoundingClientRect().top);
  const grid = lines[0].getBoundingClientRect();
  const first = lines[0].querySelector(".c").getBoundingClientRect();
  return {broken, cut, pitch, columns: Math.round(grid.right - first.left)};
}"""
