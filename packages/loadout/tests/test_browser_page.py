"""loadout's page in a browser, through a real shell that starts loadout's real page server,
whose acts run as real page jobs: the contract check; a toggle off and on again; add and
remove; a page install refused while Moxi has no record of the user's terminal, and one
refused when the shell's `PATH` has no git and the terminal's has;
Replace and Turn off through the confirm sheet, where Cancel sends nothing and each
confirmation sends its own key; Replace over a project's copy that git tracks; every
control off while a job runs, and still off after a reload; an interrupted job; a job
whose read is refused; a refused act; the project picker; and the 360 px width check.

The loadout folder, its manifest and every `moxi` command here use the shell's own
environment, so loadout's config, ledger, jobs and every harness file are under the
shell's home, and after each act the test checks that every harness file loadout names
lies there. The shell has a fake `claude` and `codex` first on its `PATH`, which fail if
run (no item here needs them), and a global git config of the test's own with no system
config.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="loadout's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

JOB = "**/m/loadout/api/job?*"
#: The event loadout's page sends on the document when an act has ended.
CHANGED = "moxi-loadout-changed"
FAKE_CLI = "#!/bin/sh\necho \"fake $(basename \"$0\"): no test here runs it\" >&2\nexit 1\n"
KEY = re.compile(r"^[A-Za-z0-9_-]{43}$")
BASE = '[user]\nskills = ["tidy-notes"]\n'
OWN_TEXT = "My own instructions, which loadout leaves alone.\n"


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    """A fake `claude` and `codex` first on the shell's `PATH`, which its page servers and
    their jobs inherit, and git with the test's own global config and no system config."""
    bin_dir = tmp_path_factory.mktemp("fake-clis")
    for name in ("claude", "codex"):
        path = bin_dir / name
        path.write_text(FAKE_CLI, encoding="utf-8")
        path.chmod(0o755)
    gitconfig = tmp_path_factory.mktemp("git") / "gitconfig"
    gitconfig.write_text("[user]\n\tname = sample-bot\n\temail = bot@example.invalid\n",
                         encoding="utf-8")
    return {"env": {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}}


def moxi(shell, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """`moxi ARGS` as the user would run it in a terminal with the shell's environment."""
    done = subprocess.run([sys.executable, "-m", "moxi", *args], env=shell.env,
                          cwd=shell.env["HOME"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)
    if check and done.returncode != 0:
        raise AssertionError(f"moxi {' '.join(args)} failed ({done.returncode}):\n"
                             f"{done.stdout}{done.stderr}")
    return done


def typed(shell) -> None:
    """Moxi's record of the terminal, as a command typed in the user's own terminal with
    `shell`'s environment makes it (`moxi` run here has no keyboard, so it records nothing
    itself)."""
    from moxi import paths, terminal

    terminal.write(shell.env, path=paths.terminal_record(shell.env))


def skill(folder: Path, name: str) -> None:
    (folder / "skills" / name).mkdir(parents=True, exist_ok=True)
    (folder / "skills" / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\nDo the thing.\n", encoding="utf-8")


def make_folder(shell) -> Path:
    """The configured loadout folder under the shell's home: three skills and a global
    file, with only `tidy-notes` in the manifest. Nothing is installed."""
    folder = Path(shell.env["HOME"]) / "my-loadout"
    for name in ("tidy-notes", "review-diff", "fresh-skill"):
        skill(folder, name)
    (folder / "global.md").write_text("Be brief.\n", encoding="utf-8")
    (folder / "loadout.toml").write_text(BASE, encoding="utf-8")
    moxi(shell, "config", "set", "loadout", "source", str(folder))
    return folder


@pytest.fixture(scope="module")
def folder(ui_shell) -> Path:
    """The loadout folder, with the user's terminal recorded as having the shell's own
    environment, which the page's jobs compare with."""
    typed(ui_shell)
    return make_folder(ui_shell)


@pytest.fixture
def installed(folder, ui_shell) -> Path:
    """The folder with only `tidy-notes` listed and installed from a terminal."""
    (folder / "loadout.toml").write_text(BASE, encoding="utf-8")
    moxi(ui_shell, "loadout", "install")
    return folder


def status(shell) -> dict:
    return json.loads(moxi(shell, "loadout", "status", "--json", check=False).stdout)


def written_under_home(shell) -> None:
    """Every harness file loadout names, its placements and the global files, lies under
    the shell's home."""
    home = Path(shell.env["HOME"]).resolve()
    paths = [Path(row["target"]) for row in status(shell)["rows"] if row["target"]]
    shown = json.loads(moxi(shell, "loadout", "global", "show", "--json").stdout)
    paths += [Path(f["file"]) for f in shown["files"]]
    for path in paths:
        assert path.is_relative_to(home) or path.resolve().is_relative_to(home), path


def open_panel(page):
    page.goto("/m/loadout/")
    panel = page.locator("#moxi-panel > moxi-loadout-page")
    expect(panel.locator('section[data-part="items"]')).to_have_attribute("data-state", "up")
    return panel


def item(panel, name: str, section: str = "user"):
    return panel.locator(f'[data-section="{section}"] li[data-item="skill:{name}"]')


def cell(panel, name: str, harness: str, section: str = "user"):
    return item(panel, name, section).locator(f'.cell[data-harness="{harness}"]')


def job_done(panel, exit_code: int = 0):
    job = panel.locator('section[data-part="job"]')
    expect(job).to_have_attribute("data-state", "done", timeout=30_000)
    expect(job).to_have_attribute("data-exit", str(exit_code))
    return job


def count_changes(page) -> None:
    page.evaluate(f"""() => {{ window.__changed = 0;
      document.addEventListener('{CHANGED}', () => {{ window.__changed += 1; }}); }}""")


def posts_to(page, path: str) -> list:
    seen: list = []
    page.on("request", lambda request: seen.append(request)
            if request.method == "POST" and request.url.endswith(f"/m/loadout/{path}") else None)
    return seen


def test_the_page_keeps_the_contract(installed, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "loadout", "page", keep=True)
    assert check.element == "moxi-loadout-page"
    for route in ("status", "list", "global", "projects"):
        assert any(url.endswith(f"/m/loadout/api/{route}") for url in check.requests), check.requests
    expect(check.locator.locator('li[data-item="skill:tidy-notes"]')).to_have_count(1)


def test_a_toggle_turns_a_harness_off_and_on_again(installed, ui_shell, ui_page):
    panel = open_panel(ui_page)
    codex = cell(panel, "tidy-notes", "codex")
    expect(codex).to_have_attribute("data-state", "ok")
    target = next(Path(row["target"]) for row in status(ui_shell)["rows"]
                  if row["name"] == "tidy-notes" and row["harness"] == "codex")
    assert target.exists()
    count_changes(ui_page)

    codex.get_by_role("switch").click()
    job = job_done(panel)
    expect(job.locator('[data-line="command"]')).to_have_text(
        "moxi loadout disable skill:tidy-notes@codex --user")
    expect(codex).to_have_attribute("data-state", "off")
    expect(codex.get_by_role("switch")).not_to_be_checked()
    assert 'disabled = ["skill:tidy-notes@codex"]' in (installed / "loadout.toml").read_text()
    assert not target.exists()
    # The page told the drift block, which read again.
    assert ui_page.evaluate("window.__changed") >= 1
    expect(panel.locator("moxi-loadout-drift .drift")).to_have_attribute("data-count", "0")
    written_under_home(ui_shell)

    codex.get_by_role("switch").click()
    job_done(panel)
    expect(job.locator('[data-line="command"]')).to_have_text(
        "moxi loadout enable skill:tidy-notes@codex --user")
    expect(codex).to_have_attribute("data-state", "ok")
    assert target.exists()
    written_under_home(ui_shell)


def test_add_places_an_item_and_remove_takes_it_away(installed, ui_shell, ui_page):
    panel = open_panel(ui_page)
    add = panel.locator('[data-part="add"]')
    add.get_by_label("Kind").select_option("skill")
    add.get_by_label("Name").fill("fresh-skill")
    add.get_by_label("Section").select_option("user")
    add.get_by_role("button", name="Add").click()
    job = job_done(panel)
    expect(job.locator('[data-line="command"]')).to_have_text("moxi loadout add skill fresh-skill")
    expect(job.locator('[data-part="job-lines"]')).to_contain_text("placed skill fresh-skill")
    expect(cell(panel, "fresh-skill", "claude")).to_have_attribute("data-state", "ok")
    assert "fresh-skill" in (installed / "loadout.toml").read_text()
    written_under_home(ui_shell)

    item(panel, "fresh-skill").get_by_role("button", name="Remove").click()
    expect(job.locator('[data-line="command"]')).to_have_text(
        "moxi loadout remove skill fresh-skill")
    job_done(panel)
    expect(item(panel, "fresh-skill")).to_have_count(0)
    assert "fresh-skill" not in (installed / "loadout.toml").read_text()
    assert all(row["name"] != "fresh-skill" for row in status(ui_shell)["rows"])


def test_a_page_install_is_refused_while_no_terminal_is_recorded(
        ui_shells, ui_shell_options, ui_contexts):
    shell = ui_shells(**ui_shell_options)
    make_folder(shell)
    page = ui_contexts(shell).pages[0]
    panel = open_panel(page)
    panel.get_by_role("button", name="Install").click()
    job = job_done(panel, 3)
    expect(job.locator('[data-line="job"]')).to_have_text(re.compile(r"^Refused"))
    expect(job.locator('[data-part="job-lines"]')).to_contain_text(
        "Type moxi modules in a terminal of your own")
    # Nothing was written: no placement, and the manifest as it was.
    assert {row["state"] for row in status(shell)["rows"]} == {"missing"}
    assert not (Path(shell.env["HOME"]) / ".claude" / "skills").exists()
    assert (Path(shell.env["HOME"]) / "my-loadout" / "loadout.toml").read_text() == BASE


def test_a_page_install_whose_path_has_no_git_is_refused_where_the_terminal_refuses(
        ui_shells, ui_shell_options, ui_contexts, tmp_path_factory):
    # The shell's PATH holds the fake CLIs and a few shell tools, but no git; the
    # terminal's is the usual one, with git.
    fakes = ui_shell_options["env"]["PATH"].split(os.pathsep)[0]
    tools = tmp_path_factory.mktemp("no-git-bin")
    for name in ("sh", "env", "basename", "dirname", "uname", "cat"):
        found = shutil.which(name)
        if found is not None:
            (tools / name).symlink_to(found)
    shell = ui_shells(env={**ui_shell_options["env"], "PATH": f"{fakes}{os.pathsep}{tools}"})
    terminal = SimpleNamespace(env={**shell.env, "PATH": ui_shell_options["env"]["PATH"]})

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(project), *args], env=terminal.env,
                              capture_output=True, text=True, check=True).stdout

    home = Path(shell.env["HOME"])
    project = home / "proj"
    (project / ".claude" / "skills").mkdir(parents=True)
    git("init", "-q")
    # An ignore file of the project's own, so loadout can't write its own there.
    (project / ".claude" / "skills" / ".gitignore").write_text("/scratch\n", encoding="utf-8")
    (project / "README").write_text("A project.\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "first")
    folder = make_folder(terminal)
    manifest = (BASE + f'[project."{project}"]\nskills = ["review-diff"]\n'
                '[skill.review-diff]\nharnesses = ["claude"]\n')
    (folder / "loadout.toml").write_text(manifest, encoding="utf-8")
    target = project / ".claude" / "skills" / "review-diff"
    typed(terminal)
    done = moxi(terminal, "loadout", "install", check=False)
    assert done.returncode == 3 and "would-commit" in done.stdout + done.stderr, done
    assert not os.path.lexists(target)

    panel = open_panel(ui_contexts(shell).pages[0])
    panel.get_by_role("button", name="Install").click()
    job = job_done(panel, 3)
    expect(job.locator('[data-part="job-lines"]')).to_contain_text(
        "git isn't on PATH here, but the last run in a terminal found it")
    assert not os.path.lexists(target)
    assert git("status", "--porcelain", "--untracked-files=all") == ""
    assert (folder / "loadout.toml").read_text(encoding="utf-8") == manifest


def test_replace_goes_through_the_sheet_and_cancel_sends_nothing(installed, ui_shell, ui_page):
    home = Path(ui_shell.env["HOME"])
    occupant = home / ".claude" / "skills" / "review-diff"
    occupant.mkdir(parents=True)
    (occupant / "SKILL.md").write_text("the user's own review-diff\n", encoding="utf-8")
    (installed / "loadout.toml").write_text('[user]\nskills = ["tidy-notes", "review-diff"]\n',
                                            encoding="utf-8")
    assert moxi(ui_shell, "loadout", "install", check=False).returncode == 3
    panel = open_panel(ui_page)
    claude = cell(panel, "review-diff", "claude")
    expect(claude).to_have_attribute("data-state", "blocked")
    expect(panel.locator("moxi-loadout-drift .drift")).to_have_attribute("data-count", "1")
    posts = posts_to(ui_page, "api/replace")

    claude.get_by_role("button", name="Replace…").click()
    sheet = ui_page.locator("moxi-ui-confirm").get_by_role("dialog")
    expect(sheet).to_contain_text("moxi loadout install --replace skill:review-diff@claude")
    expect(sheet).to_contain_text(str(occupant))
    sheet.get_by_role("button", name="Cancel").click()
    expect(ui_page.locator("moxi-ui-confirm")).to_have_count(0)
    assert posts == []
    expect(claude).to_have_attribute("data-state", "blocked")

    # The first confirmation is answered with a refusal, so Replace is still there.
    refusal = "the page server is busy; try again"
    ui_page.route("**/m/loadout/api/replace",
                  lambda route: route.fulfill(status=409, json={"conflict": refusal}))
    claude.get_by_role("button", name="Replace…").click()
    ui_page.locator("moxi-ui-confirm").get_by_role("button", name="Confirm").click()
    expect(panel.locator('[data-line="said"]')).to_have_text(refusal)
    ui_page.unroute("**/m/loadout/api/replace")
    expect(claude).to_have_attribute("data-state", "blocked")

    claude.get_by_role("button", name="Replace…").click()
    ui_page.locator("moxi-ui-confirm").get_by_role("button", name="Confirm").click()
    job = job_done(panel)
    expect(job.locator('[data-line="command"]')).to_have_text(
        "moxi loadout install --replace=skill:review-diff@claude --user")
    expect(claude).to_have_attribute("data-state", "ok")
    expect(panel.locator("moxi-loadout-drift .drift")).to_have_attribute("data-count", "0")
    # Each confirmation sent once, with a key of its own.
    keys = [request.headers["idempotency-key"] for request in posts]
    assert len(keys) == 2 and keys[0] != keys[1] and all(KEY.match(k) for k in keys)
    # The user's own copy was kept, under the shell's home.
    kept = [p for p in home.rglob("SKILL.md") if "the user's own" in p.read_text()]
    assert kept and all(p.resolve().is_relative_to(home.resolve()) for p in kept)
    written_under_home(ui_shell)


def test_replace_over_a_copy_the_projects_git_tracks_says_git_will_show_it_deleted(
        ui_shells, ui_shell_options, ui_contexts):
    shell = ui_shells(**ui_shell_options)
    home = Path(shell.env["HOME"])
    project = home / "app"
    occupant = project / ".agents" / "skills" / "review-diff"
    occupant.mkdir(parents=True)
    (occupant / "SKILL.md").write_text("the project's own review-diff\n", encoding="utf-8")

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(project), *args], env=shell.env,
                              capture_output=True, text=True, check=True).stdout

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "a skill")
    typed(shell)
    folder = make_folder(shell)
    (folder / "loadout.toml").write_text(
        BASE + f'[project."{project}"]\nskills = ["review-diff"]\n'
        '[skill.review-diff]\nharnesses = ["codex"]\n', encoding="utf-8")
    assert moxi(shell, "loadout", "install", check=False).returncode == 3
    [row] = [r for r in status(shell)["rows"] if r["name"] == "review-diff"]
    assert (row["state"], row["replaceable"]) == ("would-commit", True), row

    page = ui_contexts(shell).pages[0]
    panel = open_panel(page)
    codex = cell(panel, "review-diff", "codex", section=str(project))
    expect(codex).to_have_attribute("data-state", "would-commit")
    codex.get_by_role("button", name="Replace…").click()
    sheet = page.locator("moxi-ui-confirm").get_by_role("dialog")
    expect(sheet).to_contain_text("moxi loadout install --replace skill:review-diff@codex")
    expect(sheet).to_contain_text(str(occupant))
    expect(sheet).to_contain_text("git tracks the project's copy there. It is moved aside like "
                                  "the rest, so git will show it deleted, and the row reads "
                                  "would-commit until you commit that.")
    sheet.get_by_role("button", name="Confirm").click()
    job = job_done(panel)
    expect(job.locator('[data-line="command"]')).to_have_text(
        f"moxi loadout install --replace=skill:review-diff@codex --project={project}")
    assert occupant.is_symlink()
    written_under_home(shell)
    # What the sheet said: git shows the project's copy deleted, and the row reads
    # would-commit, with no Replace now, until the deletion is committed.
    assert " D .agents/skills/review-diff/SKILL.md" in git("status", "--porcelain")
    expect(codex).to_have_attribute("data-state", "would-commit")
    expect(codex.get_by_role("button", name="Replace…")).to_have_count(0)
    git("rm", "-r", "-q", "--cached", ".agents/skills/review-diff")
    git("commit", "-qm", "the skill is loadout's now")
    panel.get_by_role("button", name="Read again").click()
    expect(codex).to_have_attribute("data-state", "ok")


def test_turn_off_goes_through_the_sheet_with_a_new_key_each_time(
        ui_shells, ui_shell_options, ui_contexts):
    shell = ui_shells(**ui_shell_options)
    typed(shell)
    folder = make_folder(shell)
    claude_md = Path(shell.env["HOME"]) / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    claude_md.write_text(OWN_TEXT, encoding="utf-8")
    moxi(shell, "loadout", "install")
    page = ui_contexts(shell).pages[0]
    panel = open_panel(page)
    glob = panel.locator('section[data-part="global"]')
    expect(glob).to_have_attribute("data-chosen", "false")
    posts = posts_to(page, "api/global-off")

    for _ in range(2):
        glob.get_by_role("button", name="Managed").click()
        job_done(panel)
        expect(glob).to_have_attribute("data-mode", "managed")
        expect(glob.locator(f'li[data-file="{claude_md}"]')).to_have_count(1)
        assert f"@{folder / 'global.md'}" in claude_md.read_text()
        written_under_home(shell)

        glob.get_by_role("button", name="Turn off…").click()
        sheet = page.locator("moxi-ui-confirm").get_by_role("dialog")
        expect(sheet).to_contain_text(f"{claude_md}: loadout's part comes out.")
        sheet.get_by_role("button", name="Cancel").click()
        expect(page.locator("moxi-ui-confirm")).to_have_count(0)
        expect(glob).to_have_attribute("data-mode", "managed")
        glob.get_by_role("button", name="Turn off…").click()
        page.locator("moxi-ui-confirm").get_by_role("button", name="Confirm").click()
        job = job_done(panel)
        expect(job.locator('[data-line="command"]')).to_have_text("moxi loadout global remove")
        expect(glob).to_have_attribute("data-mode", "off")
        expect(glob).to_have_attribute("data-chosen", "true")
        assert claude_md.read_text() == OWN_TEXT
    keys = [request.headers["idempotency-key"] for request in posts]
    assert len(keys) == 2 and keys[0] != keys[1] and all(KEY.match(k) for k in keys)
    expect(glob.get_by_role("button", name="Turn off…")).to_have_count(0)


#: Every act's control on the panel: each button that starts an act, and the form fields.
ACTS = ('button[data-action]:not([data-action="read"]), button.switch, '
        'form select, form input')


def controls(panel) -> dict:
    """Each act's control, by what it does: its action and mode, or its label."""
    found = {}
    for control in panel.locator(ACTS).all():
        name = (control.get_attribute("data-action") or control.get_attribute("aria-label")
                or control.get_attribute("title"))
        found[f"{name}:{control.get_attribute('data-mode') or ''}"] = control
    return found


def every_control(panel, enabled: bool) -> None:
    found = controls(panel)
    # Install, Remove, a switch, Add with its fields, both mode buttons, Turn off and
    # Replace are all on the panel.
    for name in ("install:", "remove:", "add:", "Kind:", "Name:", "Section:",
                 "Sections to install:", "mode:managed", "mode:if-absent", "global-off:",
                 "replace:"):
        assert name in found, (name, sorted(found))
    assert any(name.startswith("Turn ") for name in found), sorted(found)
    for control in found.values():
        if enabled:
            expect(control).to_be_enabled()
        else:
            expect(control).to_be_disabled()


@pytest.fixture
def with_replace(installed, ui_shell):
    """`installed`, with `review-diff` listed and a folder of the user's own in its way,
    so its row offers Replace. The folder goes when the test ends."""
    occupant = Path(ui_shell.env["HOME"]) / ".claude" / "skills" / "review-diff"
    occupant.mkdir(parents=True)
    (occupant / "SKILL.md").write_text("the user's own review-diff\n", encoding="utf-8")
    (installed / "loadout.toml").write_text(
        '[user]\nskills = ["tidy-notes", "review-diff"]\n', encoding="utf-8")
    assert moxi(ui_shell, "loadout", "install", check=False).returncode == 3
    yield installed
    shutil.rmtree(occupant)


def test_every_control_is_off_while_a_job_runs(with_replace, ui_page):
    panel = open_panel(ui_page)
    expect(cell(panel, "review-diff", "claude")).to_have_attribute("data-state", "blocked")
    panel.locator('[data-part="add"]').get_by_label("Name").fill("fresh-skill")
    every_control(panel, enabled=True)
    held = {"job": "held", "argv": ["install"], "state": "running", "exit": None,
            "outcome": None, "lines": []}
    ui_page.route(JOB, lambda route: route.fulfill(json=held))
    panel.get_by_role("button", name="Install").click()
    job = panel.locator('section[data-part="job"]')
    expect(job.locator('[data-line="job"]')).to_have_text("Running…")
    expect(panel.get_by_text("A change is running.")).to_be_visible()
    every_control(panel, enabled=False)
    ui_page.unroute(JOB)
    job_done(panel, 3)          # review-diff is still blocked
    every_control(panel, enabled=True)


def test_a_reload_while_a_job_runs_follows_it_again_with_every_control_off(
        with_replace, ui_page):
    panel = open_panel(ui_page)
    panel.locator('[data-part="add"]').get_by_label("Name").fill("fresh-skill")
    held = {"job": "held", "argv": ["install"], "state": "running", "exit": None,
            "outcome": None, "lines": []}
    ui_page.route(JOB, lambda route: route.fulfill(json=held))
    panel.get_by_role("button", name="Install").click()
    expect(panel.locator('[data-line="job"]')).to_have_text("Running…")

    def reload() -> None:
        ui_page.reload()

    def away_and_back() -> None:
        # The modules view, then back through the drift block's link, in the same document.
        ui_page.goto("/m/ui/modules")
        ui_page.locator("#moxi-panel > moxi-ui-page #home moxi-loadout-drift h2 a").click()
        expect(ui_page).to_have_url(re.compile(r"/m/loadout/$"))

    # Each builds a new panel.
    for again in (reload, away_and_back):
        again()
        panel = ui_page.locator("#moxi-panel > moxi-loadout-page")
        expect(panel.locator('section[data-part="items"]')).to_have_attribute("data-state", "up")
        job = panel.locator('section[data-part="job"]')
        expect(job).to_have_attribute("data-state", "running")
        expect(job.locator('[data-line="command"]')).to_have_text("moxi loadout install")
        expect(job).to_contain_text("Install:")
        every_control(panel, enabled=False)

    ui_page.unroute(JOB)
    job_done(panel, 3)
    panel.locator('[data-part="add"]').get_by_label("Name").fill("fresh-skill")
    every_control(panel, enabled=True)
    # Once the job has ended, a reload no longer shows it.
    ui_page.reload()
    panel = open_panel(ui_page)
    expect(panel.locator('section[data-part="job"]')).to_have_count(0)
    expect(panel.get_by_role("button", name="Install")).to_be_enabled()


def test_an_interrupted_job_says_to_run_it_again(installed, ui_page):
    panel = open_panel(ui_page)
    count_changes(ui_page)
    cut = {"job": "cut", "argv": ["install"], "state": "interrupted", "exit": None,
           "outcome": None, "lines": ["the job stopped before it ended; run it again"]}
    ui_page.route(JOB, lambda route: route.fulfill(json=cut))
    panel.get_by_role("button", name="Install").click()
    job = panel.locator('section[data-part="job"]')
    expect(job).to_have_attribute("data-state", "interrupted")
    expect(job.locator('[data-line="job"]')).to_have_text("Interrupted.")
    expect(job.locator('[data-part="job-lines"]')).to_have_text(
        "the job stopped before it ended; run it again")
    expect(panel.get_by_role("button", name="Install")).to_be_enabled()
    assert ui_page.evaluate("window.__changed") >= 1


def test_a_job_whose_read_is_refused_ends_with_the_refusals_line(installed, ui_page):
    panel = open_panel(ui_page)
    count_changes(ui_page)
    line = "there is no page job held; a job's record is kept for a day"
    ui_page.route(JOB, lambda route: route.fulfill(status=422, json={"refused": line}))
    panel.get_by_role("button", name="Install").click()
    job = panel.locator('section[data-part="job"]')
    expect(job).to_have_attribute("data-state", "failed")
    expect(job.locator('[data-line="job"]')).to_have_text(line)
    expect(panel.get_by_role("button", name="Install")).to_be_enabled()
    assert ui_page.evaluate("window.__changed") >= 1
    ui_page.unroute(JOB)


def test_a_refused_act_starts_no_job_and_says_why(installed, ui_page):
    panel = open_panel(ui_page)
    add = panel.locator('[data-part="add"]')
    add.get_by_label("Name").fill("not a name!")
    add.get_by_role("button", name="Add").click()
    job = panel.locator('section[data-part="job"]')
    expect(job).to_have_attribute("data-state", "refused")
    expect(job.locator('[data-line="said"]')).to_contain_text("loadout: the page names skill")
    assert "not a name" not in (installed / "loadout.toml").read_text()


def test_the_project_picker_offers_projects_by_key_or_says_why_not(installed, ui_page):
    ui_page.route("**/m/loadout/api/projects", lambda route: route.fulfill(json={
        "projects": [{"key": "sample-site", "root": "/w/sample-site"}], "reason": None}))
    panel = open_panel(ui_page)
    section = panel.locator('[data-part="add"]').get_by_label("Section")
    expect(section.locator("option")).to_have_text(
        ["Your user section", "Project sample-site (new section)"])
    ui_page.unroute("**/m/loadout/api/projects")
    ui_page.route("**/m/loadout/api/projects", lambda route: route.fulfill(json={
        "projects": [], "reason": "projects is busy; try again"}))
    panel = open_panel(ui_page)
    expect(panel.locator('[data-line="projects"]')).to_have_text(
        "Projects by key: projects is busy; try again")


def test_the_page_fits_a_phone(installed, ui_shell, ui_page, ui_width_check):
    (installed / "loadout.toml").write_text(
        '[user]\nskills = ["tidy-notes", "review-diff"]\ndisabled = ["skill:review-diff@codex"]\n'
        '\n[global]\nmode = "managed"\n', encoding="utf-8")
    moxi(ui_shell, "loadout", "install", check=False)
    try:
        ui_width_check(ui_page, module="loadout")
        panel = ui_page.locator("#moxi-panel > moxi-loadout-page")
        expect(panel.locator('section[data-part="items"]')).to_have_attribute("data-state", "up")
        expect(panel.locator('section[data-part="global"]')).to_have_attribute("data-mode", "managed")
        for summary in panel.locator("details > summary").all():
            summary.click()
        ui_width_check(ui_page)
    finally:
        (installed / "loadout.toml").write_text(BASE + '\n[global]\nmode = "off"\n',
                                                encoding="utf-8")
        moxi(ui_shell, "loadout", "install", check=False)
