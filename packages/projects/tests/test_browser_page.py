"""projects' page in a browser, through a real shell that starts projects' real page
server: the contract check; the store file and the git the page acts with, and a shell
whose PATH has no git; the list and its kind filter; a contribution that won't load,
named beside the filter and in the add form, which an add goes ahead without and whose
warning the form shows beside the success; an add with create in a
real git; a topic added with no root, whose answer is lost, after which the page reads its
list again and says the directory may exist; the edit form, which sends only what the
user changed when a terminal has changed the project since it was opened; the lead and
the associated agents; remove through the confirm sheet, where Cancel sends nothing, a
refusal gives each sentence, a removal takes the row off, and each confirmation sends its
own key; a remove whose answer is lost, which says projects may have acted; and the 360
px width check.

Rows are seeded with `moxi projects add` run with the shell's own environment, so the
store is the one the page server reads, under the shell's home, and every check reads it
back the same way. A fake module on the shell's site refuses to remove a project while a
file named `HOLD` is in its root. git runs with a global config of the test's own and no
system config.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: The event projects' page and components read their routes again on.
CHANGED = "moxi-projects-changed"
#: An `Idempotency-Key` as the shell's confirm sheet mints one.
KEY = re.compile(r"^[A-Za-z0-9_-]{43}$")
#: The paths a create from the page goes through, for a real-git row put back by a change.
MINT_GLOBS = ("packages/projects/src/moxi_projects/acts.py",
              "packages/projects/src/moxi_projects/pagehandlers.py",
              "packages/projects/web/src/add.ts",
              "packages/projects/web/src/page.ts",
              "packages/projects/tests/test_browser_page.py")

HOLDS = textwrap.dedent('''\
    import os

    INTERFACE = 1


    def refuse_remove(project):
        hold = os.path.join(project.root, "HOLD")
        if os.path.exists(hold):
            return f"{hold} holds it; delete that file to free it"
        return None
''')

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
    """A site holding the fake module `holds`, and git with the test's own global config and
    no system config."""
    site = fake_site(tmp_path_factory.mktemp("holds"), "fake_holds", "holds", HOLDS)
    gitconfig = tmp_path_factory.mktemp("git") / "gitconfig"
    gitconfig.write_text("[user]\n\tname = sample-bot\n\temail = bot@example.invalid\n"
                         "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    return {"site": [str(site)],
            "env": {"GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}}


def moxi(shell, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """`moxi ARGS` as the user would run it in a terminal with the shell's environment."""
    done = subprocess.run([sys.executable, "-m", "moxi", *args], env=shell.env,
                          cwd=shell.env["HOME"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)
    if check and done.returncode != 0:
        raise AssertionError(f"moxi {' '.join(args)} failed ({done.returncode}):\n"
                             f"{done.stdout}{done.stderr}")
    return done


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
    return roots


def open_panel(page):
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    return panel


def row(panel, key: str):
    return panel.locator(f'li.project[data-key="{key}"]')


def open_row(panel, key: str):
    item = row(panel, key)
    item.locator('[data-action="open"]').click()
    expect(item).to_have_attribute("data-open", "true")
    return item


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
    check = ui_component_check(ui_page, "projects", "page", keep=True)
    assert check.element == "moxi-projects-page"
    for route in ("projects", "kinds", "where"):
        assert any(url.endswith(f"/m/projects/api/{route}") for url in check.requests), check.requests
    for key in seeded:
        expect(check.locator.locator(f'li.project[data-key="{key}"]')).to_have_count(1)
    expect(check.locator.locator("moxi-projects-add form")).to_have_attribute("data-state", "up")


def test_the_page_shows_the_store_and_the_git_it_acts_with(seeded, ui_shell, ui_page):
    store = subprocess.run([sys.executable, "-c", "from moxi_projects import store; print(store.path())"],
                           env=ui_shell.env, capture_output=True, text=True, check=True).stdout.strip()
    git = shutil.which("git", path=ui_shell.env["PATH"])
    assert git is not None
    panel = open_panel(ui_page)
    where = panel.locator('section[data-part="where"]')
    expect(where).to_have_attribute("data-state", "up")
    expect(where.locator('[data-line="store"]')).to_have_text(store)
    expect(where.locator('[data-line="git"]')).to_have_text(git)
    assert Path(store).is_relative_to(Path(ui_shell.env["MOXI_HOME"]))


def test_a_shell_with_no_git_says_so_and_a_create_names_the_lasting_fix(
        ui_shells, ui_shell_options, ui_contexts, tmp_path_factory):
    tools = tmp_path_factory.mktemp("no-git-bin")
    for name in ("sh", "env", "uname", "cat"):
        found = shutil.which(name)
        if found is not None:
            (tools / name).symlink_to(found)
    shell = ui_shells(**{**ui_shell_options,
                         "env": {**ui_shell_options["env"], "PATH": str(tools)}})
    assert shutil.which("git", path=shell.env["PATH"]) is None
    page = ui_contexts(shell).pages[0]
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "empty")
    where = panel.locator('section[data-part="where"]')
    expect(where.locator('[data-line="git"]')).to_contain_text("none on this server's PATH")

    form = panel.locator("moxi-projects-add")
    where_to = str(Path(shell.env["HOME"]) / "work" / "nogit")
    form.get_by_label("Key", exact=True).fill("nogit")
    form.get_by_label(re.compile(r"^Root")).fill(where_to)
    form.get_by_label("Create the root").check()
    form.get_by_role("button", name="Add").click()
    said = form.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "error")
    expect(said).to_contain_text("moxi ui stop")
    expect(said).to_contain_text(f"moxi projects add nogit --root {where_to} --create")
    assert not os.path.lexists(where_to)
    assert shown(shell, "nogit") is None


# --- the list ---------------------------------------------------------------------------------

def test_the_kind_filter_lists_only_that_kind(seeded, ui_page):
    panel = open_panel(ui_page)
    reads = reads_of(ui_page, "api/projects")
    expect(row(panel, "alpha")).to_have_count(1)
    expect(row(panel, "scout-home")).to_have_count(1)
    expect(panel.locator('[data-part="broken"]')).to_have_count(0)
    choose = panel.get_by_role("combobox", name="Show the kind")
    choose.select_option("agent")
    expect(row(panel, "alpha")).to_have_count(0)
    expect(row(panel, "scout-home")).to_have_count(1)
    assert any(url.endswith("/m/projects/api/projects?kind=agent") for url in reads), reads
    choose.select_option("topic")
    expect(panel.locator('[data-line="empty"]')).to_have_text("No project has the kind topic.")
    choose.select_option("")
    expect(row(panel, "alpha")).to_have_count(1)
    expect(row(panel, "scout-home")).to_have_count(1)


def test_a_contribution_that_wont_load_is_named_and_an_add_goes_ahead_with_its_warning(
        ui_shells, ui_contexts, tmp_path_factory):
    site = fake_site(tmp_path_factory.mktemp("broken"), "fake_broken", "broken", BROKEN)
    shell = ui_shells(site=[str(site)])
    page = ui_contexts(shell).pages[0]
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    broken = panel.locator('section[data-part="list"] [data-part="broken"]')
    expect(broken.locator('[data-line="broken"]')).to_contain_text("broken:")
    expect(broken).to_contain_text("a remove is refused, as is an add or set of a kind no "
                                   "loaded module declares")
    expect(broken).to_contain_text("any other add goes ahead without it, with a warning")
    form = panel.locator("moxi-projects-add")
    expect(form.locator('[data-line="broken"]')).to_contain_text("broken:")
    where = root(shell, "skipping")
    form.get_by_label("Key", exact=True).fill("skipping")
    form.get_by_label(re.compile(r"^Root")).fill(where)
    form.get_by_role("button", name="Add").click()
    said = form.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "ok")
    expect(said).to_contain_text(f"Added skipping at {where}.")
    warning = form.locator('[data-line="warning"]')
    expect(warning).to_be_visible()
    expect(warning).to_contain_text("Warning: skipped broken, which won't load")
    expect(warning).to_contain_text("defaults and checks were not applied")
    expect(warning).to_contain_text("moxi uninstall broken")
    assert shown(shell, "skipping")["root"] == where


# --- add ----------------------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.touches(*MINT_GLOBS)
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_an_add_with_create_makes_a_git_repository_that_the_list_then_shows(
        seeded, ui_shell, ui_page):
    panel = open_panel(ui_page)
    form = panel.locator("moxi-projects-add")
    where = root(ui_shell, "minted")
    form.get_by_label("Key", exact=True).fill("minted")
    form.get_by_label(re.compile(r"^Root")).fill(where)
    form.get_by_label("Create the root").check()
    form.get_by_role("button", name="Add").click()
    said = form.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "ok", timeout=30_000)
    expect(said).to_contain_text(f"Added minted at {where}.")
    done = subprocess.run(["git", "-C", where, "rev-parse", "--is-inside-work-tree"],
                          env=ui_shell.env, capture_output=True, text=True, check=False)
    assert done.stdout.strip() == "true", done
    assert shown(ui_shell, "minted")["root"] == where
    # The form sent CHANGED, and the page read its list again.
    expect(row(panel, "minted")).to_have_count(1)


def test_a_lost_add_answer_reads_the_list_again_and_says_the_directory_may_exist(
        seeded, ui_shell, ui_page):
    panel = open_panel(ui_page)
    reads = reads_of(ui_page, "api/projects")

    def lost(route) -> None:
        route.fetch()               # the add is made, and its answer never arrives
        route.fulfill(status=504, json={"error": "timeout"})

    ui_page.route("**/m/projects/api/add", lost)
    form = panel.locator("moxi-projects-add")
    form.get_by_label("Key", exact=True).fill("lost-topic")
    form.get_by_role("combobox", name=re.compile(r"^Kind")).select_option("topic")
    # The root is left empty: notes gives a topic's.
    form.get_by_label("Create the root").check()
    form.get_by_role("button", name="Add").click()
    said = form.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "warn")
    expect(said).to_contain_text("may have acted")
    expect(said).to_contain_text("The directory may exist now.")
    ui_page.unroute("**/m/projects/api/add")
    # The page read its list again, which now holds the topic.
    expect(row(panel, "lost-topic")).to_have_count(1)
    assert len(reads) >= 1, reads
    made = shown(ui_shell, "lost-topic")
    assert made is not None and made["kind"] == "topic" and Path(made["root"]).is_dir(), made


# --- edit ---------------------------------------------------------------------------------------

def test_the_edit_form_changes_the_root_and_kind_and_refuses_a_relative_root(
        seeded, ui_shell, ui_page):
    add(ui_shell, "editable")
    panel = open_panel(ui_page)
    item = open_row(panel, "editable")
    edit = item.locator('form[data-part="edit"]')
    save = edit.get_by_role("button", name="Save")
    expect(save).to_be_disabled()
    moved = root(ui_shell, "moved-away")
    edit.get_by_label(re.compile(r"^Root")).fill(moved)
    edit.get_by_role("combobox", name="Kind").select_option("agent")
    save.click()
    said = item.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "ok")
    expect(said).to_contain_text("Saved editable.")
    expect(said).to_contain_text("doesn't exist yet")
    expect(item.locator('[data-field="root"]')).to_have_text(moved)
    expect(item.locator('[data-field="kind"]')).to_have_text("agent")
    after = shown(ui_shell, "editable")
    assert (after["root"], after["kind"]) == (moved, "agent"), after

    edit.get_by_label(re.compile(r"^Root")).fill("work/relative")
    save.click()
    expect(said).to_have_attribute("data-tone", "error")
    expect(said).to_contain_text("fix:")
    assert shown(ui_shell, "editable")["root"] == moved


def test_an_edit_after_a_terminal_change_sends_only_what_the_user_changed(
        seeded, ui_shell, ui_page):
    first = add(ui_shell, "edited")
    second = root(ui_shell, "edited-moved")
    Path(second).mkdir(parents=True)
    panel = open_panel(ui_page)
    sets = posts_to(ui_page, "api/set")
    item = open_row(panel, "edited")
    edit = item.locator('form[data-part="edit"]')
    save = edit.get_by_role("button", name="Save")
    root_field = edit.get_by_label(re.compile(r"^Root"))
    stale = edit.locator('[data-line="stale"]')
    expect(root_field).to_have_value(first)

    # A terminal moves the root while the form is open, and the page reads again. The form
    # wasn't typed in, so it starts again from what is stored, with nothing to save.
    moxi(ui_shell, "projects", "set", "edited", "--root", second)
    panel.get_by_role("button", name="Read again").click()
    expect(item.locator('[data-field="root"]')).to_have_text(second)
    expect(root_field).to_have_value(second)
    expect(save).to_be_disabled()
    expect(stale).to_have_count(0)

    # Only the kind is changed, so only the kind is sent, and the terminal's root stands.
    edit.get_by_role("combobox", name="Kind").select_option("agent")
    save.click()
    expect(item.locator('[data-field="kind"]')).to_have_text("agent")
    assert [json.loads(request.post_data) for request in sets] == [
        {"key": "edited", "kind": "agent"}]
    after = shown(ui_shell, "edited")
    assert (after["root"], after["kind"]) == (second, "agent"), after

    # Typed in, the form keeps the user's root when the terminal changes the kind, and
    # says the project has changed; the save sends the root alone.
    third = root(ui_shell, "edited-typed")
    root_field.fill(third)
    moxi(ui_shell, "projects", "set", "edited", "--kind", "project")
    panel.get_by_role("button", name="Read again").click()
    expect(item.locator('[data-field="kind"]')).to_have_text("project")
    expect(stale).to_contain_text("This project has changed since you opened the form.")
    expect(root_field).to_have_value(third)
    save.click()
    expect(item.locator('[data-field="root"]')).to_have_text(third)
    expect(stale).to_have_count(0)
    assert json.loads(sets[-1].post_data) == {"key": "edited", "root": third}
    after = shown(ui_shell, "edited")
    assert (after["root"], after["kind"]) == (third, "project"), after


# --- lead and agents ----------------------------------------------------------------------------

def test_the_lead_and_the_agents_are_set_added_removed_and_cleared(seeded, ui_shell, ui_page):
    add(ui_shell, "crewed")
    panel = open_panel(ui_page)
    item = open_row(panel, "crewed")
    agents = item.locator('[data-part="agents"]')
    said = item.locator('[data-line="said"]')
    expect(agents.locator('[data-line="lead"]')).to_have_text("none")
    expect(agents.locator('[data-line="no-agents"]')).to_be_visible()

    agents.get_by_label("Lead agent").fill("captain")
    agents.get_by_role("button", name="Set lead").click()
    expect(said).to_contain_text("captain leads crewed now.")
    expect(agents.locator('[data-line="lead"]')).to_have_text("captain")
    expect(agents.get_by_label("Lead agent")).to_have_value("")

    agents.get_by_label("Agent name").fill("deckhand")
    agents.get_by_role("button", name="Add agent").click()
    expect(said).to_contain_text("Added deckhand to crewed.")
    expect(agents.locator('li[data-agent="deckhand"]')).to_have_count(1)

    # The lead can't also be in the associated set: the verb's refusal, with its fix.
    agents.get_by_label("Agent name").fill("captain")
    agents.get_by_role("button", name="Add agent").click()
    expect(said).to_have_attribute("data-tone", "error")
    expect(said).to_contain_text("fix:")
    assert shown(ui_shell, "crewed")["associated"] == ["deckhand"]

    agents.get_by_role("button", name="Remove deckhand").click()
    expect(said).to_contain_text("Took deckhand out of crewed.")
    expect(agents.locator('li[data-agent="deckhand"]')).to_have_count(0)

    agents.get_by_role("button", name="Clear lead").click()
    expect(said).to_contain_text("crewed has no lead now.")
    expect(agents.locator('[data-line="lead"]')).to_have_text("none")
    after = shown(ui_shell, "crewed")
    assert (after["lead"], after["associated"]) == (None, []), after


# --- remove -------------------------------------------------------------------------------------

def test_remove_goes_through_the_sheet_refuses_with_its_sentences_then_removes(
        seeded, ui_shell, ui_page):
    where = add(ui_shell, "held")
    moxi(ui_shell, "projects", "agents", "add", "held", "keeper")
    hold = Path(where) / "HOLD"
    hold.write_text("kept\n", encoding="utf-8")
    panel = open_panel(ui_page)
    posts = posts_to(ui_page, "api/remove")
    statuses = reads_of(ui_page, "api/remove-status")
    item = open_row(panel, "held")
    sheet = ui_page.locator("moxi-ui-confirm").get_by_role("dialog")

    # Cancel sends nothing.
    item.get_by_role("button", name="Remove…").click()
    expect(sheet).to_contain_text("moxi projects remove held")
    expect(sheet).to_contain_text(where)
    sheet.get_by_role("button", name="Cancel").click()
    expect(ui_page.locator("moxi-ui-confirm")).to_have_count(0)
    assert posts == []
    expect(row(panel, "held")).to_have_count(1)

    # Its associated agent holds it.
    item.get_by_role("button", name="Remove…").click()
    sheet.get_by_role("button", name="Confirm").click()
    said = item.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "error")
    expect(said).to_contain_text("keeper")
    expect(said).to_contain_text("fix:")
    assert shown(ui_shell, "held") is not None

    # Then the fake module's sentence, named by its module.
    item.get_by_role("button", name="Remove keeper").click()
    expect(item.locator('li[data-agent="keeper"]')).to_have_count(0)
    item.get_by_role("button", name="Remove…").click()
    sheet.get_by_role("button", name="Confirm").click()
    expect(said).to_have_attribute("data-tone", "error")
    expect(said).to_contain_text(f"holds: {hold} holds it; delete that file to free it")
    expect(said).to_contain_text("moxi projects remove held")
    assert shown(ui_shell, "held") is not None

    # Freed, it is removed; its root and files stay.
    hold.unlink()
    item.get_by_role("button", name="Remove…").click()
    sheet.get_by_role("button", name="Confirm").click()
    expect(row(panel, "held")).to_have_count(0)
    told = panel.locator('section[data-part="list"] > [data-line="said"]')
    expect(told).to_have_attribute("data-tone", "ok")
    expect(told).to_contain_text("Removed held.")
    assert shown(ui_shell, "held") is None
    assert Path(where).is_dir()

    # Each confirmation sent once, with a key of its own, and its status link was read.
    keys = [request.headers["idempotency-key"] for request in posts]
    assert len(keys) == 3 and len(set(keys)) == 3 and all(KEY.match(k) for k in keys), keys
    for key in keys:
        assert any(url.endswith(f"/m/projects/api/remove-status?job={key}") for url in statuses), statuses


def test_a_lost_remove_answer_says_projects_may_have_acted(seeded, ui_shell, ui_page):
    add(ui_shell, "lost-remove")
    panel = open_panel(ui_page)

    def lost(route) -> None:
        route.fetch()               # the remove is made, and its answer never arrives
        route.fulfill(status=504, json={"error": "timeout"})

    ui_page.route("**/m/projects/api/remove", lost)
    item = open_row(panel, "lost-remove")
    item.get_by_role("button", name="Remove…").click()
    ui_page.locator("moxi-ui-confirm").get_by_role("dialog").get_by_role(
        "button", name="Confirm").click()
    # The page read its list again, which no longer holds it, and says so in its own words.
    expect(row(panel, "lost-remove")).to_have_count(0)
    told = panel.locator('section[data-part="list"] > [data-line="said"]')
    expect(told).to_have_attribute("data-tone", "warn")
    expect(told).to_have_text(
        "No answer came back, so projects may have acted; the list has been read again.")
    ui_page.unroute("**/m/projects/api/remove")
    assert shown(ui_shell, "lost-remove") is None


# --- width --------------------------------------------------------------------------------------

def test_the_page_fits_a_phone(seeded, ui_shell, ui_page, ui_width_check):
    long_root = add(ui_shell, "a-project-with-a-rather-long-key-for-a-narrow-screen")
    assert len(long_root) > 60
    ui_width_check(ui_page, module="projects")
    panel = ui_page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    # A project opened, with its edit form, and then one with a lead and an agent.
    for key in ("a-project-with-a-rather-long-key-for-a-narrow-screen", "alpha"):
        open_row(panel, key)
        ui_width_check(ui_page)
