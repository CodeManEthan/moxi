"""The session name in Home's Options panel in a browser, through a real shell that starts
the launcher's real page server: the name the template gives, following the model; a name
typed over it, a change that reaches the launch; the template's editor rendering each edit
unsaved, refusing a template the page server would refuse, and saving through the shell's
own settings route, after which the next read names the saved template; and the save
refused on a shell started with the template's key locked.

The shell's clock is real, so a name holding the date is matched by its pattern. The shell
runs the fake slots in slots' place and a fake `claude` first on its `PATH`, so a launch
from Home starts no real session.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

import launcher_page_kit as kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' picker is checked in a browser on Linux")]

PREVIEW = "/m/launcher/api/preview"
SAVE = "/m/ui/api/settings/set"
UNSET = "/m/ui/api/settings/unset"
#: The shipped template's date, as the browser shell's real clock renders it.
DATE = r"[A-Z][a-z]{2}-\d{2} \d{2}:\d{2}"
SHIPPED = "<project>-<model>-<date:mmm-dd hh:mm>"
TYPE_OVER = "type over it to name this launch yourself."
LOCKED = "launcher name.template is locked for this run of the shell, so it can't be changed from a page"


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    return kit.Harness(tmp_path_factory.mktemp("fake-harness"))


@pytest.fixture(scope="module")
def on_path(harness):
    """The fake harnesses first on `PATH` for every shell this module starts."""
    with kit.first_on_path(harness.bin):
        yield


@pytest.fixture(scope="module")
def module_fake(tmp_path_factory):
    return kit.FakeSlots(tmp_path_factory.mktemp("fake-slots"))


@pytest.fixture(scope="module")
def shell(ui_shells, module_fake, on_path):
    """A shell with the project `proj-a` among the places."""
    module_fake.script(placed=2, slots=kit.free(2, 3))
    shell = ui_shells(**module_fake.options())
    root = Path(shell.env["HOME"]) / "work" / "proj-a"
    root.mkdir(parents=True, exist_ok=True)
    done = shell.moxi("projects", "add", "proj-a", "--root", str(root))
    assert done.returncode == 0, done.stdout + done.stderr
    kit.place(shell, "place-project", cwd=str(root), project_key="proj-a", at="2001-01-01T09:00:00.000000Z")
    return shell


@pytest.fixture(scope="module")
def locked_shell(ui_shells, module_fake, on_path):
    """A shell started with the name template's key locked for its run."""
    options = module_fake.options()
    return ui_shells(**{**options, "env": {**options["env"], "MOXI_UI_LOCKED_SETTINGS": "launcher.name.template"}})


@pytest.fixture
def fake(module_fake):
    module_fake.script(placed=2, slots=kit.free(2, 3))
    return module_fake


@pytest.fixture
def folder(shell):
    """Home opens on a folder of the test's own, which no launch has run in."""
    return kit.fresh_place(shell)


@pytest.fixture
def page(shell, fake, folder, ui_contexts):
    return ui_contexts(shell).pages[0]


@pytest.fixture
def unsaved(shell):
    """Both template settings back at their shipped values after the test."""
    yield
    for key in ("name.template", "name.templates"):
        status, answer = kit.session(shell).post(UNSET, {"module": "launcher", "key": key})
        assert status == 200, (status, answer)


def name_row(piece):
    """Options opened, with Seat > Name & notes; returns the panel."""
    piece.get_by_role("button", name=re.compile(r"^Options")).click()
    panel = piece.get_by_role("complementary", name="Options")
    expect(panel).to_be_visible()
    panel.locator('button[data-group="seat"]').click()
    panel.locator('button[data-sub="seat/Name & notes"]').click()
    return panel


def field(panel):
    return panel.get_by_role("textbox", name="Session name")


def note(panel):
    return panel.locator('[data-row="name"] .tnote')


def editor(panel):
    """The template's editor, opened by the caret in the name's field."""
    panel.get_by_role("button", name="Edit the name template").click()
    found = panel.locator("[data-editor]")
    expect(found).to_be_visible()
    return found


def written(found):
    return found.get_by_role("textbox", name="The template as written")


def pick_model(page, piece, model: str) -> None:
    piece.locator(".chip").click()
    menu = piece.locator('[data-menu="chip"]')
    menu.locator('[data-list="model"] .row', has_text=re.compile(f"^\\W*{model}$")).click()
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)


def launch(page, piece, brief: str) -> dict:
    """A headless launch of `brief`, followed until it starts; returns the previewed body."""
    piece.locator(".more").click()
    piece.locator('[data-menu="run"] button[data-run="Headless"]').click()
    piece.locator("textarea").fill(brief)
    expect(piece.locator(".go")).to_be_enabled()
    with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    return json.loads(sent.value.post_data)


def saved(page, found) -> dict:
    """`Save template` pressed; returns the body sent to the shell's settings route."""
    with page.expect_request(lambda request: request.url.endswith(SAVE)) as sent:
        found.get_by_role("button", name="Save template").click()
    return json.loads(sent.value.post_data)


def test_the_name_follows_the_model_and_a_typed_name_is_a_change_until_the_template_is_taken(
        page, shell, folder):
    piece = kit.open_home(page)
    panel = name_row(piece)
    name = field(panel)
    expect(name).to_have_value("")
    expect(name).to_have_attribute("placeholder", re.compile(rf"^{folder.name}-{DATE}$"))
    expect(note(panel)).to_have_text(
        f"From the template for every launch. It follows the project, the model and the clock; {TYPE_OVER}")
    kit.assert_colour(note(panel), "text-dim")
    pick_model(page, piece, "opus")
    expect(name).to_have_attribute("placeholder", re.compile(rf"^{folder.name}-opus-{DATE}$"))
    # A launch with no name typed leaves it out, and takes the template's.
    body = launch(page, piece, "name this launch from its template")
    assert "name" in body["leave_out"] and "name" not in body["values"], body
    assert re.fullmatch(rf"{folder.name}-opus-{DATE}", kit.newest_launch(shell).values["name"])
    # Typed over, it is a change like any row, and the launch takes it.
    name.fill("orders-skeleton")
    expect(note(panel)).to_have_text("Typed for this launch. Use the template")
    expect(piece.locator('[data-line="changed"] [data-change="name"]')).to_have_text("Session name")
    expect(piece.get_by_role("button", name="Options · 1")).to_be_visible()
    body = launch(page, piece, "name this launch as typed")
    assert body["values"]["name"] == "orders-skeleton", body
    assert kit.newest_launch(shell).values["name"] == "orders-skeleton"
    # `Use the template` lets go of it.
    note(panel).get_by_role("button", name="Use the template").click()
    expect(name).to_have_value("")
    expect(name).to_have_attribute("placeholder", re.compile(rf"^{folder.name}-opus-{DATE}$"))
    expect(piece.locator('[data-line="changed"]')).to_have_count(0)
    expect(note(panel)).to_contain_text("From the template for every launch.")


def test_an_edited_template_renders_unsaved_is_refused_then_saved_and_the_next_read_names_it(
        page, folder, unsaved):
    piece = kit.open_home(page)
    panel = name_row(piece)
    found = editor(panel)
    expect(found.locator(".tok")).to_have_text(["project", "model", "date"])
    expect(written(found)).to_have_value(SHIPPED)
    expect(found.get_by_role("button", name=re.compile(r"^Date "))).to_have_text(re.compile(rf"^{DATE}▾$"))
    # The line names a folder, so there is no project to keep it for.
    expect(found.get_by_role("group", name="Keep for")).to_have_count(0)
    found.get_by_role("button", name="add", exact=True).click()
    expect(found.locator('[data-menu="name-add"] [data-piece]')).to_have_text(
        ["agent", "identity", "scope", "instance", "time"])
    found.locator('[data-menu="name-add"] [data-piece="agent"]').click()
    expect(written(found)).to_have_value(f"{SHIPPED}-<agent>")
    # Each edit is rendered unsaved.
    found.get_by_role("button", name="Take out date").click()
    expect(written(found)).to_have_value("<project>-<model>-<agent>")
    expect(field(panel)).to_have_attribute("placeholder", folder.name)
    # A template the page server would refuse says why, and can't be saved.
    written(found).fill("<project>-<n>")
    expect(found.locator("[data-refused]")).to_have_text(re.compile(
        r"^the name template holds <n>, which is not a piece a name is made of\. use <project>, "))
    expect(found.get_by_role("button", name="Save template")).to_be_disabled()
    written(found).fill("<project>-kept")
    expect(field(panel)).to_have_attribute("placeholder", f"{folder.name}-kept")
    expect(found.locator("[data-refused]")).to_have_count(0)
    assert saved(page, found) == {"module": "launcher", "key": "name.template", "value": "<project>-kept"}
    expect(panel.locator("[data-editor]")).to_have_count(0)
    expect(note(panel)).to_have_text(f"From the template for every launch. It follows the project; {TYPE_OVER}")
    expect(field(panel)).to_have_attribute("placeholder", f"{folder.name}-kept")
    # A fresh Home reads the saved template.
    piece = kit.open_home(page)
    panel = name_row(piece)
    expect(field(panel)).to_have_attribute("placeholder", f"{folder.name}-kept")
    expect(written(editor(panel))).to_have_value("<project>-kept")


def test_a_template_kept_for_the_project_names_its_launches_and_says_whose_it_is(page, unsaved):
    piece = kit.open_home(page)
    piece.locator(".where .face").click()
    piece.locator('[data-menu="where"] .places .row').filter(has_text="proj-a").click()
    expect(piece.locator(".where .face")).to_have_text("proj-a▾")
    panel = name_row(piece)
    found = editor(panel)
    keep = found.get_by_role("group", name="Keep for")
    expect(keep.get_by_role("button")).to_have_text(["proj-a", "every project"])
    expect(keep.get_by_role("button", name="every project")).to_have_attribute("aria-pressed", "true")
    keep.get_by_role("button", name="proj-a").click()
    written(found).fill("<project>-own")
    expect(field(panel)).to_have_attribute("placeholder", "proj-a-own")
    body = saved(page, found)
    assert body == {"module": "launcher", "key": "name.templates", "value": '{proj-a = "<project>-own"}'}, body
    expect(note(panel)).to_have_text(f"From proj-a's template. It follows the project; {TYPE_OVER}")
    expect(field(panel)).to_have_attribute("placeholder", "proj-a-own")


def test_on_a_shell_started_with_the_template_locked_the_save_is_refused_in_the_shells_words(
        locked_shell, fake, ui_contexts):
    folder = kit.fresh_place(locked_shell)
    page = ui_contexts(locked_shell).pages[0]
    piece = kit.open_home(page)
    panel = name_row(piece)
    found = editor(panel)
    written(found).fill("<project>-locked")
    expect(field(panel)).to_have_attribute("placeholder", f"{folder.name}-locked")
    with page.expect_response(lambda answer: answer.url.endswith(SAVE)) as answered:
        found.get_by_role("button", name="Save template").click()
    assert answered.value.status == 422
    expect(found.locator("[data-saved]")).to_have_text(f"Not saved: {LOCKED}")
    expect(written(found)).to_have_value("<project>-locked")
    # Nothing was written: the name is still the shipped template's.
    found.get_by_role("button", name="Cancel").click()
    expect(panel.locator("[data-editor]")).to_have_count(0)
    expect(field(panel)).to_have_attribute("placeholder", re.compile(rf"^{folder.name}-{DATE}$"))
    written_file = locked_shell.config_file("launcher")
    assert not written_file.exists() or "<project>-locked" not in written_file.read_text()
