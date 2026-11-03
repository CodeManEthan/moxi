"""projects' three components and its page in a browser, through a real shell that starts
projects' real page server: the contract check on all four (the page placing the add
form; the page in full is test_browser_page.py's); the picker listing every
project with its root, or only those of a kind, and sending its host a bubbling, composed
`moxi-change` whose detail is the key and nothing else; the card for a key and for a key
that names no project; and the add form hiding "create" for a kind whose mint is
`refuse`, then adding a project, which a placed picker then lists.

Rows are seeded with `moxi projects add` run with the shell's own environment, so the
store is the one the page server reads, under the shell's home. A fake module on the
shell's site contributes the kind `shelf`, whose mint is `refuse`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' components are checked in a browser on "
                                        "Linux; the Windows and macOS runs are owed")]

#: The event the picker sends its host on a choice.
PICKED = "moxi-change"
#: The event projects' components read their routes again on.
CHANGED = "moxi-projects-changed"

SHELF = textwrap.dedent('''\
    INTERFACE = 2
    KINDS = {"shelf": ("a place made somewhere else, never by create", lambda fields: None,
                       "refuse")}
''')

# Each placed picker records every event it sends to the document, so a test reads
# what a host would have got.
_LISTEN = """(name) => {
  window.__picked = [];
  document.addEventListener(name, (event) => {
    window.__picked.push({detail: event.detail, bubbles: event.bubbles,
                          composed: event.composed, from: event.target.localName});
  });
}"""


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    """A site holding a fake module that contributes the `refuse` kind `shelf`."""
    site = tmp_path_factory.mktemp("site")
    (site / "fake_shelves").mkdir()
    (site / "fake_shelves" / "__init__.py").write_text("", encoding="utf-8")
    (site / "fake_shelves" / "projects_link.py").write_text(SHELF, encoding="utf-8")
    info = site / "fake_shelves-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: fake-shelves\nVersion: 0.1.0\n",
                                   encoding="utf-8")
    (info / "entry_points.txt").write_text(
        "[moxi.projects]\nshelves = fake_shelves.projects_link\n", encoding="utf-8")
    return {"site": [str(site)]}


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


@pytest.fixture(scope="module")
def seeded(ui_shell) -> dict[str, str]:
    """Two projects, each with a root under the shell's home: `alpha`, a project with a
    lead and an associated agent, and `scout-home`, an agent's. Returns each key's root."""
    roots = {"alpha": root(ui_shell, "alpha"), "scout-home": root(ui_shell, "scout")}
    moxi(ui_shell, "projects", "add", "alpha", "--root", roots["alpha"], "--lead", "lead-a")
    moxi(ui_shell, "projects", "agents", "add", "alpha", "helper")
    moxi(ui_shell, "projects", "add", "scout-home", "--root", roots["scout-home"],
         "--kind", "agent")
    return roots


def shown(shell, key: str) -> dict | None:
    done = moxi(shell, "projects", "show", key, "--json", check=False)
    return json.loads(done.stdout) if done.returncode == 0 else None


# --- the contract --------------------------------------------------------------------------

def test_the_page_keeps_the_contract_and_holds_the_add_form(seeded, ui_page,
                                                            ui_component_check):
    check = ui_component_check(ui_page, "projects", "page", keep=True)
    assert check.element == "moxi-projects-page"
    expect(check.locator.locator("h1")).to_have_text("Projects")
    expect(check.locator.locator("moxi-projects-add form")).to_have_attribute("data-state", "up")


@pytest.mark.parametrize(("name", "attrs", "route"), [
    ("picker", {}, "/m/projects/api/projects"),
    ("picker", {"kind": "agent"}, "/m/projects/api/projects?kind=agent"),
    ("card", {"key": "alpha"}, "/m/projects/api/project?key=alpha"),
    ("add", {}, "/m/projects/api/kinds"),
])
def test_each_component_keeps_the_contract(seeded, ui_page, ui_component_check, name, attrs,
                                           route):
    check = ui_component_check(ui_page, "projects", name, attrs)
    assert check.element == f"moxi-projects-{name}"
    assert any(url.endswith(route) for url in check.requests), check.requests


# --- the picker ----------------------------------------------------------------------------

def test_the_picker_shows_each_key_and_root_and_sends_the_key_only(seeded, ui_page,
                                                                     ui_component_check):
    ui_page.evaluate(_LISTEN, PICKED)
    picker = ui_component_check(ui_page, "projects", "picker", keep=True).locator
    for key, where in seeded.items():
        choice = picker.locator(f'button.choice[data-key="{key}"]')
        expect(choice).to_contain_text(key)
        expect(choice.locator("code")).to_have_text(where)
    choice = picker.get_by_role("button", name="alpha")
    choice.click()
    expect(choice).to_have_attribute("aria-pressed", "true")
    picked = ui_page.evaluate("() => window.__picked")
    assert picked == [{"detail": {"key": "alpha"}, "bubbles": True, "composed": True,
                       "from": "moxi-projects-picker"}]


def test_the_picker_by_kind_lists_only_that_kind(seeded, ui_page, ui_component_check):
    ui_page.evaluate(_LISTEN, PICKED)
    picker = ui_component_check(ui_page, "projects", "picker", {"kind": "agent"},
                                keep=True).locator
    choices = picker.locator("button.choice")
    expect(choices).to_have_count(1)
    expect(choices).to_have_attribute("data-key", "scout-home")
    choices.click()
    assert ui_page.evaluate("() => window.__picked.map((event) => event.detail)") == [
        {"key": "scout-home"}]


def test_a_picker_for_a_kind_no_project_has_says_so(seeded, ui_page, ui_component_check):
    picker = ui_component_check(ui_page, "projects", "picker", {"kind": "topic"},
                                keep=True).locator
    expect(picker.locator(".picker")).to_have_attribute("data-state", "empty")
    expect(picker.locator('[data-line="empty"]')).to_have_text("No project has the kind topic.")


# --- the card ------------------------------------------------------------------------------

def test_the_card_shows_a_project(seeded, ui_page, ui_component_check):
    card = ui_component_check(ui_page, "projects", "card", {"key": "ALPHA"}, keep=True).locator
    expect(card.locator(".card")).to_have_attribute("data-state", "up")
    expect(card.locator("h3")).to_have_text("alpha")
    expect(card.locator('[data-field="kind"]')).to_have_text("project")
    expect(card.locator('[data-field="root"]')).to_have_text(seeded["alpha"])
    expect(card.locator('[data-field="lead"]')).to_have_text("lead-a")
    expect(card.locator('[data-field="agents"]')).to_have_text("helper")


def test_the_card_for_a_missing_key_shows_the_refusal(seeded, ui_page, ui_component_check):
    card = ui_component_check(ui_page, "projects", "card", {"key": "nosuch"}, keep=True).locator
    expect(card.locator(".card")).to_have_attribute("data-state", "refused")
    expect(card.locator("h3")).to_have_text("nosuch")
    expect(card.locator('[data-line="refused"]')).to_contain_text("nosuch")
    expect(card.locator("dl")).to_have_count(0)


# --- the add form --------------------------------------------------------------------------

def test_the_add_form_hides_create_for_a_refuse_kind(seeded, ui_page, ui_component_check):
    add = ui_component_check(ui_page, "projects", "add", {"kind": "shelf"}, keep=True).locator
    expect(add.locator("form")).to_have_attribute("data-state", "up")
    kind = add.get_by_role("combobox", name=re.compile(r"^Kind"))
    expect(kind).to_have_value("shelf")
    expect(add.locator('[data-part="create"]')).to_have_count(0)
    kind.select_option("project")
    expect(add.get_by_label("Create the root")).to_be_visible()
    kind.select_option("shelf")
    expect(add.locator('[data-part="create"]')).to_have_count(0)


def test_the_add_form_makes_a_row_that_a_placed_picker_then_lists(seeded, ui_shell, ui_page,
                                                                   ui_component_check):
    picker = ui_component_check(ui_page, "projects", "picker", keep=True).locator
    expect(picker.locator(".picker")).to_have_attribute("data-state", "up")
    expect(picker.locator('button.choice[data-key="fresh"]')).to_have_count(0)
    add = ui_component_check(ui_page, "projects", "add", keep=True).locator
    where = root(ui_shell, "fresh")
    add.get_by_label("Key", exact=True).fill("fresh")
    add.get_by_label(re.compile(r"^Root")).fill(where)
    expect(add.get_by_role("combobox", name=re.compile(r"^Kind"))).to_have_value("project")
    add.get_by_role("button", name="Add").click()
    said = add.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "ok")
    expect(said).to_contain_text(f"Added fresh at {where}.")
    expect(add.get_by_label("Key", exact=True)).to_have_value("")
    row = shown(ui_shell, "fresh")
    assert row is not None and row["root"] == where and row["kind"] == "project", row
    # The add sent CHANGED, so the picker read its list again.
    expect(picker.locator('button.choice[data-key="fresh"]')).to_have_count(1)


def test_a_refused_add_shows_the_reason_and_fix_and_makes_nothing(seeded, ui_shell, ui_page,
                                                                    ui_component_check):
    add = ui_component_check(ui_page, "projects", "add", keep=True).locator
    add.get_by_label("Key", exact=True).fill("relative")
    add.get_by_label(re.compile(r"^Root")).fill("work/relative")
    add.get_by_role("button", name="Add").click()
    said = add.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "error")
    expect(said).to_contain_text("fix:")
    assert shown(ui_shell, "relative") is None
