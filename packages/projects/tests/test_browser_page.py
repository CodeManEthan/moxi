"""projects' page in a browser, through a real shell that starts projects' real page server:
the contract check; a shell whose PATH has no git, where a create names the lasting fix;
a terminal whose last run placed the store elsewhere than the shell does, after which the
Add panel shows the store line above its button and its add refuses with it; a
contribution that won't load, named under the panel's `Kind`, which an add goes ahead
without and whose warning the panel shows beside the success; an add with create in a real
git; an add whose answer is lost, after which the page reads its list again and says it
may have been added; the Edit panel, which changes the folder and the kind, refuses a
relative folder, and sends only what the user changed when a terminal has changed the
project since it was opened; the lead and the associated agents set, added, taken off and
cleared through it; and the 360 px width check.

Rows are seeded with `moxi projects add` run with the shell's own environment, so the
store is the one the page server reads, under the shell's home, and every check reads it
back the same way. Those runs have no keyboard, so they record nothing; each shell is
given the core's record of a terminal with its own environment before the page writes,
as a person's own terminal run would leave it. git runs with a global config of the
test's own and no system config.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: The event projects' page and components read their routes again on.
CHANGED = "moxi-projects-changed"
#: The paths a create from the page goes through, for a real-git row put back by a change.
MINT_GLOBS = ("packages/projects/src/moxi_projects/acts.py",
              "packages/projects/src/moxi_projects/pagehandlers.py",
              "packages/projects/web/src/panel.ts",
              "packages/projects/web/src/page.ts",
              "packages/projects/tests/test_browser_page.py")

BROKEN = "INTERFACE = 1\nimport fake_module_that_is_not_there\n"


def fake_site(base: Path, package: str, entry: str, source: str) -> Path:
    """A site holding one fake distribution whose `moxi.projects` entry `entry` is
    `package.projects_link`, with `source` as its text."""
    site = base / "site"
    (site / package).mkdir(parents=True)
    (site / package / "__init__.py").write_text("", encoding="utf-8")
    (site / package / "projects_link.py").write_text(source, encoding="utf-8")
    name = package.replace("_", "-")
    info = site / f"{package}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1.0\n",
                                   encoding="utf-8")
    (info / "entry_points.txt").write_text(
        f"[moxi.projects]\n{entry} = {package}.projects_link\n", encoding="utf-8")
    return site


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    """git with the test's own global config and no system config."""
    gitconfig = tmp_path_factory.mktemp("git") / "gitconfig"
    gitconfig.write_text("[user]\n\tname = sample-bot\n\temail = bot@example.invalid\n"
                         "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    return {"env": {"GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}}


def moxi(shell, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """`moxi ARGS` as the user would run it in a terminal with the shell's environment."""
    done = subprocess.run([sys.executable, "-m", "moxi", *args], env=shell.env,
                          cwd=shell.env["HOME"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)
    if check and done.returncode != 0:
        raise AssertionError(f"moxi {' '.join(args)} failed ({done.returncode}):\n"
                             f"{done.stdout}{done.stderr}")
    return done


def record_terminal(shell, **changes: str) -> None:
    """The core's record of a terminal whose environment is the shell's with `changes`,
    written where the shell's page server reads it. A module's shell keeps the anchor of
    the test that started it, so the record goes there and not to this test's anchor."""
    from moxi import paths, terminal

    terminal.write({**shell.env, **changes}, path=paths.terminal_record(shell.env))


def store_of(env: dict) -> str:
    """The store file `moxi projects` resolves with environment `env`."""
    return subprocess.run([sys.executable, "-c",
                           "from moxi_projects import store; print(store.path())"],
                          env=env, capture_output=True, text=True, check=True,
                          timeout=60).stdout.strip()


def root(shell, name: str) -> str:
    return str(Path(shell.env["HOME"]) / "work" / name)


def shown(shell, key: str) -> dict | None:
    done = moxi(shell, "projects", "show", key, "--json", check=False)
    return json.loads(done.stdout) if done.returncode == 0 else None


def add(shell, key: str, *args: str) -> str:
    """Adds project `key` with its root under the shell's home made, and returns the root."""
    where = root(shell, key)
    Path(where).mkdir(parents=True, exist_ok=True)
    moxi(shell, "projects", "add", key, "--root", where, *args)
    return where


@pytest.fixture(scope="module")
def seeded(ui_shell) -> dict[str, str]:
    """Two projects: `alpha`, with a lead and an associated agent, and `scout-home`, an
    agent's. Returns each key's root."""
    roots = {"alpha": add(ui_shell, "alpha", "--lead", "lead-a"),
             "scout-home": add(ui_shell, "scout-home", "--kind", "agent")}
    moxi(ui_shell, "projects", "agents", "add", "alpha", "helper")
    record_terminal(ui_shell)
    return roots


def open_panel(page):
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    return panel


def row(panel, key: str):
    return panel.locator(f'moxi-ui-row[data-key="{key}"]')


def open_row(panel, key: str):
    """Opens `key`'s row with its `Open`, and gives the row."""
    item = row(panel, key)
    item.locator('[data-action="open"]').click()
    expect(item).to_have_attribute("open", "")
    return item


def edit_panel(panel, key: str):
    """Opens `key`'s row unless it is open, presses `Edit` in its foot, and gives the Edit
    panel."""
    item = row(panel, key)
    if item.get_attribute("open") is None:
        open_row(panel, key)
    item.locator('[data-part="foot"] [data-action="edit"]').click()
    aside = panel.locator('moxi-projects-panel [data-part="panel"]')
    expect(aside).to_have_attribute("data-panel", "edit")
    return aside


def panel_closed(panel) -> None:
    expect(panel.locator("moxi-projects-panel")).to_have_count(0)


def root_row(item):
    """The opened project's `Folder` row's words."""
    return item.locator('[data-part="opened"] [data-label="Folder"] .ol')


def tilde(shell, path: str) -> str:
    """`path` as the page shows a folder under the shell's home."""
    return "~/" + Path(path).relative_to(shell.env["HOME"]).as_posix()


def add_panel(panel):
    """Presses `Add a project`, and gives the panel."""
    panel.get_by_role("button", name="Add a project").click()
    aside = panel.locator('moxi-projects-panel [data-part="panel"]')
    expect(aside).to_be_visible()
    return aside


def fill_root(aside, path: str) -> None:
    """Types `path` in the panel's folder field and waits for what is there to be read."""
    aside.get_by_label("Folder", exact=True).fill(path)
    expect(aside.get_by_role("button", name="Add project")).to_be_enabled()


def changed(page) -> None:
    """Sends the event after which every projects component reads again."""
    page.evaluate("name => document.dispatchEvent(new Event(name))", CHANGED)


def posts_to(page, path: str) -> list:
    seen: list = []
    page.on("request", lambda request: seen.append(request)
            if request.method == "POST" and request.url.endswith(f"/m/projects/{path}") else None)
    return seen


def reads_of(page, path: str) -> list[str]:
    seen: list[str] = []
    page.on("request", lambda request: seen.append(request.url)
            if request.method == "GET" and f"/m/projects/{path}" in request.url else None)
    return seen


# --- the contract, the store and the git -----------------------------------------------------

def test_the_page_keeps_the_contract(seeded, ui_page, ui_component_check):
    # With services installed, the page places its `project-facts` component, which reads
    # services' routes.
    check = ui_component_check(ui_page, "projects", "page", keep=True, allow=("services", "sessions", "loadout"))
    assert check.element == "moxi-projects-page"
    for route in ("projects", "kinds"):
        assert any(url.endswith(f"/m/projects/api/{route}") for url in check.requests), check.requests
    for key in seeded:
        expect(check.locator.locator(f'moxi-ui-row[data-key="{key}"]')).to_have_count(1)
    # The add is the panel's, opened by `Add a project`; no add form is placed.
    expect(check.locator.locator("moxi-projects-add")).to_have_count(0)


def test_a_terminal_whose_store_is_elsewhere_refuses_the_add_with_the_store_line(
        ui_shells, ui_shell_options, ui_contexts, tmp_path_factory):
    shell = ui_shells(**ui_shell_options)
    moved = str(tmp_path_factory.mktemp("terminal-data"))
    record_terminal(shell, MOXI_PROJECTS_DATA_HOME=moved)
    here = store_of(shell.env)
    there = store_of({**shell.env, "MOXI_PROJECTS_DATA_HOME": moved})
    assert here != there
    page = ui_contexts(shell).pages[0]
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    line = f"the projects store here is {here}, but the last run in a terminal used {there}"
    # The list still reads: the shell's store, which has no rows.
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "empty")

    aside = add_panel(panel)
    # The panel says above its button that a change would be refused, and why.
    expect(aside.locator('[data-line="note"]')).to_contain_text(line)
    where_to = root(shell, "pagerow")
    fill_root(aside, where_to)
    expect(aside.get_by_label("Name", exact=True)).to_have_value("pagerow")
    aside.get_by_role("button", name="Add project").click()
    said = aside.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-outcome", "refused")
    expect(said).to_contain_text(line)
    expect(said).to_contain_text(f"Set MOXI_PROJECTS_DATA_HOME to {moved}")
    expect(said).to_contain_text("moxi ui stop")
    # Neither store was made, and the panel keeps what was typed.
    assert not os.path.exists(here) and not os.path.exists(there)
    expect(aside.get_by_label("Folder", exact=True)).to_have_value(where_to)


def test_a_shell_with_no_git_refuses_a_create_with_the_lasting_fix(
        ui_shells, ui_shell_options, ui_contexts, tmp_path_factory):
    tools = tmp_path_factory.mktemp("no-git-bin")
    for name in ("sh", "env", "uname", "cat"):
        found = shutil.which(name)
        if found is not None:
            (tools / name).symlink_to(found)
    shell = ui_shells(**{**ui_shell_options,
                         "env": {**ui_shell_options["env"], "PATH": str(tools)}})
    assert shutil.which("git", path=shell.env["PATH"]) is None
    record_terminal(shell)
    page = ui_contexts(shell).pages[0]
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "empty")

    aside = add_panel(panel)
    where_to = str(Path(shell.env["HOME"]) / "work" / "nogit")
    fill_root(aside, where_to)
    expect(aside.locator('[data-line="folder"]')).to_have_text("No folder there yet.")
    aside.get_by_label("Create the folder and run git init").check()
    aside.get_by_role("button", name="Add project").click()
    said = aside.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-outcome", "refused")
    expect(said).to_contain_text("moxi ui stop")
    expect(said).to_contain_text(f"moxi projects add nogit --root {where_to} --create")
    assert not os.path.lexists(where_to)
    assert shown(shell, "nogit") is None


# --- the list ---------------------------------------------------------------------------------

def test_a_contribution_that_wont_load_is_named_and_an_add_goes_ahead_with_its_warning(
        ui_shells, ui_contexts, tmp_path_factory):
    site = fake_site(tmp_path_factory.mktemp("broken"), "fake_broken", "broken", BROKEN)
    shell = ui_shells(site=[str(site)])
    record_terminal(shell)
    page = ui_contexts(shell).pages[0]
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "empty")
    aside = add_panel(panel)
    # The broken contribution is one dim line under `Kind`, behind `More`.
    aside.get_by_role("button", name=re.compile(r"More")).click()
    expect(aside.locator('[data-line="broken"]')).to_contain_text("broken:")
    where = root(shell, "skipping")
    Path(where).mkdir(parents=True)
    fill_root(aside, where)
    aside.get_by_role("button", name="Add project").click()
    said = aside.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-outcome", "done")
    expect(said).to_contain_text("Added skipping at ~/work/skipping.")
    expect(said).to_contain_text("Warning: skipped broken, which won't load")
    expect(said).to_contain_text("defaults and checks were not applied")
    expect(said).to_contain_text("moxi uninstall broken")
    assert shown(shell, "skipping")["root"] == where


# --- add ----------------------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.touches(*MINT_GLOBS)
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_an_add_with_create_makes_a_git_repository_that_the_list_then_shows(
        seeded, ui_shell, ui_page):
    panel = open_panel(ui_page)
    aside = add_panel(panel)
    where = root(ui_shell, "minted")
    fill_root(aside, where)
    aside.get_by_label("Create the folder and run git init").check()
    aside.get_by_role("button", name="Add project").click()
    said = aside.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-outcome", "done", timeout=30_000)
    expect(said).to_contain_text(f"Added minted at {tilde(ui_shell, where)}.")
    done = subprocess.run(["git", "-C", where, "rev-parse", "--is-inside-work-tree"],
                          env=ui_shell.env, capture_output=True, text=True, check=False)
    assert done.stdout.strip() == "true", done
    assert shown(ui_shell, "minted")["root"] == where
    # The panel sent CHANGED, and the page read its list again.
    expect(row(panel, "minted")).to_have_count(1)


def test_a_lost_add_answer_reads_the_list_again_and_says_it_may_have_been_added(
        seeded, ui_shell, ui_page):
    panel = open_panel(ui_page)
    reads = reads_of(ui_page, "api/projects")

    def lost(route) -> None:
        route.fetch()               # the add is made, and its answer never arrives
        route.fulfill(status=504, json={"error": "timeout"})

    ui_page.route("**/m/projects/api/add", lost)
    aside = add_panel(panel)
    where = root(ui_shell, "lost-made")
    Path(where).mkdir(parents=True)
    fill_root(aside, where)
    aside.get_by_role("button", name="Add project").click()
    said = aside.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-outcome", "unknown")
    expect(said).to_have_text("It may have been added.")
    ui_page.unroute("**/m/projects/api/add")
    # The page read its list again, which now holds it.
    expect(row(panel, "lost-made")).to_have_count(1)
    assert len(reads) >= 1, reads
    made = shown(ui_shell, "lost-made")
    assert made is not None and made["root"] == where, made


# --- edit ---------------------------------------------------------------------------------------

def test_the_edit_panel_changes_the_root_and_kind_and_refuses_a_relative_root(
        seeded, ui_shell, ui_page):
    add(ui_shell, "editable")
    panel = open_panel(ui_page)
    aside = edit_panel(panel, "editable")
    save = aside.get_by_role("button", name="Save")
    expect(save).to_be_disabled()
    moved = root(ui_shell, "moved-away")
    aside.get_by_label("Folder", exact=True).fill(moved)
    aside.get_by_label("Kind", exact=True).select_option("agent")
    save.click()
    panel_closed(panel)
    # The new folder doesn't exist yet, so the Folder row says so rather than the kind.
    item = row(panel, "editable")
    expect(root_row(item)).to_contain_text(f"{tilde(ui_shell, moved)} doesn't exist")
    after = shown(ui_shell, "editable")
    assert (after["root"], after["kind"]) == (moved, "agent"), after

    aside = edit_panel(panel, "editable")
    aside.get_by_label("Folder", exact=True).fill("work/relative")
    aside.get_by_role("button", name="Save").click()
    said = aside.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-outcome", "refused")
    expect(said).to_contain_text("fix:")
    expect(aside.get_by_label("Folder", exact=True)).to_have_value("work/relative")
    assert shown(ui_shell, "editable")["root"] == moved


def test_an_edit_after_a_terminal_change_sends_only_what_the_user_changed(
        seeded, ui_shell, ui_page):
    first = add(ui_shell, "edited")
    second = root(ui_shell, "edited-moved")
    Path(second).mkdir(parents=True)
    panel = open_panel(ui_page)
    sets = posts_to(ui_page, "api/set")
    aside = edit_panel(panel, "edited")
    item = row(panel, "edited")
    save = aside.get_by_role("button", name="Save")
    field = aside.get_by_label("Folder", exact=True)
    stale = aside.locator('[data-line="stale"]')
    expect(field).to_have_value(tilde(ui_shell, first))

    # A terminal moves the folder while the panel is open, and the page reads again on the
    # changed event. Nothing was typed, so the panel starts again from what is stored, with
    # nothing to save.
    moxi(ui_shell, "projects", "set", "edited", "--root", second)
    changed(ui_page)
    expect(root_row(item)).to_have_text(f"{tilde(ui_shell, second)} · a code project")
    expect(field).to_have_value(tilde(ui_shell, second))
    expect(save).to_be_disabled()
    expect(stale).to_have_count(0)

    # Only the kind is changed, so only the kind is sent, and the terminal's folder stands.
    aside.get_by_label("Kind", exact=True).select_option("agent")
    save.click()
    panel_closed(panel)
    expect(root_row(item)).to_have_text(f"{tilde(ui_shell, second)} · an agent's home")
    assert [json.loads(request.post_data) for request in sets] == [
        {"key": "edited", "kind": "agent"}]
    after = shown(ui_shell, "edited")
    assert (after["root"], after["kind"]) == (second, "agent"), after

    # Typed in, the panel keeps his folder when the terminal changes the kind, and says
    # the project has changed; the save sends the folder alone.
    third = root(ui_shell, "edited-typed")
    aside = edit_panel(panel, "edited")
    field = aside.get_by_label("Folder", exact=True)
    field.fill(third)
    moxi(ui_shell, "projects", "set", "edited", "--kind", "project")
    changed(ui_page)
    expect(root_row(item)).to_contain_text("a code project")
    expect(aside.locator('[data-line="stale"]')).to_have_text("This project has changed since you opened it.")
    expect(field).to_have_value(third)
    aside.get_by_role("button", name="Save").click()
    panel_closed(panel)
    expect(root_row(item)).to_contain_text(tilde(ui_shell, third))
    assert json.loads(sets[-1].post_data) == {"key": "edited", "root": third}
    after = shown(ui_shell, "edited")
    assert (after["root"], after["kind"]) == (third, "project"), after


# --- lead and agents ----------------------------------------------------------------------------

def test_the_lead_and_the_agents_are_set_added_taken_off_and_cleared(seeded, ui_shell, ui_page):
    add(ui_shell, "crewed")
    panel = open_panel(ui_page)
    item = row(panel, "crewed")
    agents_row = item.locator('[data-part="opened"] [data-label="Agents"] .ol')
    aside = edit_panel(panel, "crewed")
    expect(aside.get_by_label("Lead", exact=True)).to_have_value("")
    expect(aside.locator("[data-agent]")).to_have_count(0)

    aside.get_by_label("Lead", exact=True).fill("captain")
    aside.get_by_label("Add an agent by name").fill("deckhand")
    aside.get_by_role("button", name="Save").click()
    panel_closed(panel)
    expect(agents_row).to_contain_text("captain leads")
    expect(agents_row).to_contain_text("deckhand also works here")

    # The lead can't also be one who works there: the verb's refusal, with its fix.
    aside = edit_panel(panel, "crewed")
    expect(aside.locator('[data-agent="deckhand"]')).to_have_count(1)
    agent = aside.get_by_label("Add an agent by name")
    agent.fill("captain")
    agent.press("Enter")
    aside.get_by_role("button", name="Save").click()
    said = aside.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-outcome", "refused")
    expect(said).to_contain_text("fix:")
    expect(said).not_to_contain_text("Saved")
    assert shown(ui_shell, "crewed")["associated"] == ["deckhand"]

    aside.get_by_role("button", name="Take off captain").click()
    aside.get_by_role("button", name="Take off deckhand").click()
    aside.get_by_label("Lead", exact=True).fill("")
    aside.get_by_role("button", name="Save").click()
    panel_closed(panel)
    expect(agents_row).to_have_text("no lead")
    after = shown(ui_shell, "crewed")
    assert (after["lead"], after["associated"]) == (None, []), after


# --- width --------------------------------------------------------------------------------------

def test_the_page_fits_a_phone(seeded, ui_shell, ui_page, ui_width_check):
    long_root = add(ui_shell, "a-project-with-a-rather-long-key-for-a-narrow-screen")
    assert len(long_root) > 60
    ui_width_check(ui_page, module="projects")
    panel = ui_page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    # A project opened, then one with a lead and an agent, and its Edit panel.
    for key in ("a-project-with-a-rather-long-key-for-a-narrow-screen", "alpha"):
        open_row(panel, key)
        ui_width_check(ui_page)
    row(panel, "alpha").locator('[data-part="foot"] [data-action="edit"]').click()
    expect(panel.locator('moxi-projects-panel [data-part="panel"]')).to_be_visible()
    ui_width_check(ui_page)
