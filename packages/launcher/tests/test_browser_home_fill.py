"""The fill from the last launch on Home in a browser, through a real shell that starts the
launcher's real page server: each last launch is a launch made from Home itself, then Home
is opened again, but for the two tests that answer `/api/last` themselves (below). A value
it typed fills the row, is named on `From last launch` on the line above the box with
`Clear`, and under its row in the panel; it is no change, and the request names it in
`filled`. An edit makes it the user's change, `Clear` puts the form's value
back. Two seats with one home fill nothing from each other. A value that can do harm is
filled in the warning colour, asks nothing and launches with its flag. Another harness fills
first, and the form is read again for it. A harness picked by hand in the chip lets go of the
launch's values and fills that harness's comms identity, with comms' refusal and a live
namesake's note beside it, and does so too when the answer filled nothing for the form Home
opened on; the shell runs no comms, so those tests answer `/api/last` themselves and show the
identity rows `/api/catalog` hides without comms.

The shell runs the fake slots in slots' place and fake `claude` and `codex` first on its
`PATH`, so no launch starts a real session.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from moxi_dev.testing import PROC_WAIT_S, wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit
from moxi_launcher import presets

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' picker is checked in a browser on Linux")]

PREVIEW = "/m/launcher/api/preview"
CATALOG = "/m/launcher/api/catalog"
LAST = "/m/launcher/api/last"

#: Two seats whose home is the same project.
PRESETS = """\
[seat.planner]
harness = "claude"
home = "proj-b"

[seat.scout]
harness = "claude"
home = "proj-b"
"""


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


def add_project(shell, key: str) -> Path:
    """The project `key` in the shell's own store, its root under the shell's home."""
    where = Path(shell.env["HOME"]) / "work" / key
    where.mkdir(parents=True, exist_ok=True)
    done = shell.moxi("projects", "add", key, "--root", str(where))
    assert done.returncode == 0, done.stdout + done.stderr
    return where


@pytest.fixture(scope="module")
def shell(ui_shells, module_fake, on_path):
    """A shell with the projects `proj-a` and `proj-b`, `proj-a` among the places, and the
    two seats above."""
    module_fake.script(placed=2, slots=kit.free(2, 3))
    shell = ui_shells(**module_fake.options())
    root = add_project(shell, "proj-a")
    add_project(shell, "proj-b")
    file = presets.path(env=shell.env)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(PRESETS)
    kit.place(shell, "place-project", cwd=str(root), project_key="proj-a", at="2001-01-01T09:00:00.000000Z")
    return shell


@pytest.fixture
def fake(module_fake):
    module_fake.script(placed=2, slots=kit.free(2, 3))
    return module_fake


@pytest.fixture(name="work_dir")
def work_dir_fixture(shell):
    """Home opens on a folder of the test's own, which no launch has run in."""
    return kit.fresh_place(shell)


@pytest.fixture
def page(shell, fake, work_dir, ui_contexts):
    return ui_contexts(shell).pages[0]


def item(piece):
    """The From-last-launch item's face on the line above the box."""
    return piece.locator('.where [data-item="fill"] > button')


def open_list(piece):
    item(piece).click()
    listed = piece.locator('[data-menu="fill"]')
    expect(listed).to_be_visible()
    return listed


def note(whose: str, *, them: bool = False) -> re.Pattern:
    """The sentence under the item's list."""
    return re.compile(rf"^Filled from {re.escape(whose)} last launch, today .+\. "
                      rf"The preset sets nothing for {'them' if them else 'it'}\.$")


def open_options(piece):
    piece.get_by_role("button", name=re.compile(r"^Options")).click()
    panel = piece.get_by_role("complementary", name="Options")
    expect(panel).to_be_visible()
    return panel


def open_sub(panel, group: str, sub: str) -> None:
    panel.locator(f'button[data-group="{group}"]').click()
    panel.locator(f'button[data-sub="{group}/{sub}"]').click()


def control(panel, name: str):
    return panel.locator(f'[data-row="{name}"] [data-control="{name}"]')


def add_dir(panel, path: str) -> None:
    control(panel, "add_dir").fill(path)
    panel.locator('[data-row="add_dir"]').get_by_role("button", name="Add").click()


def headless(piece) -> None:
    piece.locator(".more").click()
    piece.locator('[data-menu="run"] button[data-run="Headless"]').click()
    expect(piece.locator(".go")).to_have_text("Launch headless")


def press(page, piece) -> dict:
    """Press the button once it can be pressed; returns the body the piece previews."""
    expect(piece.locator(".go")).to_be_enabled()
    with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    return json.loads(sent.value.post_data)


def launch(page, piece, brief: str) -> dict:
    """A headless launch of `brief`, followed until it starts; returns its request."""
    headless(piece)
    piece.locator("textarea").fill(brief)
    body = press(page, piece)
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    return body


def argv_of(harness, brief: str) -> list[str]:
    [run] = wait_for(lambda: [run for run in harness.runs() if brief in " ".join(run["argv"])],
                     PROC_WAIT_S, what="the fake harness's run with the brief")
    return run["argv"]


def flagged(argv: list[str], flag: str) -> list[str]:
    return [argv[at + 1] for at, word in enumerate(argv[:-1]) if word == flag]


def pick_project(piece, key: str) -> None:
    piece.locator(".where .face").click()
    piece.locator('[data-menu="where"] .places .row').filter(has_text=key).click()
    expect(piece.locator(".where .face")).to_have_text(f"{key}▾")


def pick_seat(piece, name: str) -> None:
    piece.locator('.where [data-item="preset"] > button').click()
    piece.locator(f'[data-menu="preset"] button[data-preset="seat:{name}"]').click()
    expect(piece.locator('.where [data-item="preset"] > button')).to_have_text(f"{name}▾")


def test_a_value_the_last_launch_typed_fills_its_row_and_clear_puts_the_forms_back(page, harness, shell):
    piece = kit.open_home(page)
    pick_project(piece, "proj-a")
    extra = str(Path(shell.env["HOME"]) / "work" / "proj-b")
    panel = open_options(piece)
    open_sub(panel, "access", "Reach")
    add_dir(panel, extra)
    open_sub(panel, "seat", "Name & notes")
    control(panel, "native_name").fill("helper-1")
    control(panel, "native_name").blur()
    expect(piece.locator('[data-line="changed"] b')).to_have_text(["Extra folders", "Native name"])
    body = launch(page, piece, "say the word first")
    assert "filled" not in body, body
    # Opened again on the project: what that launch typed fills, and is no change.
    piece = kit.open_home(page)
    expect(item(piece)).to_have_count(0)
    pick_project(piece, "proj-a")
    expect(item(piece)).to_have_text("2 settings▾")
    expect(piece.locator(".where .k")).to_have_text(["Project", "Preset", "From last launch"])
    kit.assert_colour(piece.locator(".where .k").last, "text-dim")
    kit.assert_colour(item(piece).locator("span").first, "text")
    expect(piece.locator(".opt")).to_have_text("Options")
    expect(piece.locator('[data-line="changed"]')).to_have_count(0)
    listed = open_list(piece)
    # A folder under the home folder is written from `~`.
    expect(listed.locator("[data-fill]")).to_have_text(["Extra folders~/work/proj-bClear", "Native namehelper-1Clear"])
    expect(listed.locator(".fnote")).to_have_text(note("this project's", them=True))
    kit.assert_colour(listed.locator(".fnote"), "text-dim")
    item(piece).click()
    expect(listed).to_have_count(0)
    # In the panel each value is under its row with where it came from and Clear.
    panel = open_options(piece)
    open_sub(panel, "access", "Reach")
    expect(panel.locator('[data-row="add_dir"] li')).to_have_text([f"{extra}Remove"])
    came = panel.locator('[data-from="add_dir"]')
    expect(came).to_have_text(re.compile(r"^from the last launch, today .+Clear$"))
    kit.assert_colour(came.locator("span"), "text-dim")
    expect(panel.locator('button[data-group="seat"] .sum')).to_have_text("Native name helper-1")
    # Edited, a filled value is the user's change and leaves the item.
    open_sub(panel, "seat", "Name & notes")
    control(panel, "native_name").fill("helper-2")
    control(panel, "native_name").blur()
    expect(piece.locator('[data-line="changed"] b')).to_have_text(["Native name"])
    expect(piece.locator(".opt")).to_have_text("Options · 1")
    expect(item(piece)).to_have_text("Extra folders▾")
    expect(panel.locator('[data-from="native_name"]')).to_have_count(0)
    body = launch(page, piece, "say the word second")
    assert body["filled"] == ["add_dir"], body
    assert body["values"]["add_dir"] == [extra] and body["values"]["native_name"] == "helper-2", body
    assert flagged(argv_of(harness, "say the word second"), "--add-dir") == [extra]
    # A filled value stays after a launch; Clear puts the form's value back.
    expect(item(piece)).to_have_text("Extra folders▾")
    came.get_by_role("button", name="Clear Extra folders").click()
    expect(item(piece)).to_have_count(0)
    expect(panel.locator('[data-row="add_dir"] li')).to_have_count(0)
    expect(piece.locator('[data-line="changed"] b')).to_have_text(["Native name"])
    piece.locator("textarea").fill("say the word third")
    body = press(page, piece)
    assert "filled" not in body and "add_dir" not in body["values"], body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()


def test_two_seats_with_one_home_do_not_fill_from_each_other(page, shell):
    piece = kit.open_home(page)
    pick_seat(piece, "planner")
    expect(piece.locator(".where .face")).to_have_text("proj-b▾")
    expect(item(piece)).to_have_count(0)
    extra = str(Path(shell.env["HOME"]) / "work" / "proj-a")
    panel = open_options(piece)
    open_sub(panel, "access", "Reach")
    add_dir(panel, extra)
    body = launch(page, piece, "say the word planner")
    assert body["seat"] == "planner" and body["values"]["add_dir"] == [extra], body
    piece = kit.open_home(page)
    # The other seat, in the same project, fills nothing from the planner's launch.
    pick_seat(piece, "scout")
    expect(piece.locator(".where .face")).to_have_text("proj-b▾")
    expect(piece.locator(".go")).to_be_enabled()
    expect(item(piece)).to_have_count(0)
    # Neither does the project the seats share: a seat's launch is the seat's alone.
    piece.locator('.where [data-item="preset"] > button').click()
    piece.locator('[data-menu="preset"] button[data-preset=""]').click()
    expect(piece.locator(".go")).to_be_enabled()
    expect(item(piece)).to_have_count(0)
    # The planner's own.
    pick_seat(piece, "planner")
    expect(item(piece)).to_have_text("Extra folders▾")
    listed = open_list(piece)
    expect(listed.locator(".fnote")).to_have_text(note("this agent's"))
    body = press(page, piece)
    assert body["seat"] == "planner" and body["filled"] == ["add_dir"], body


def test_a_value_that_can_do_harm_filled_from_the_last_launch_is_in_the_warning_colour_and_launches_with_its_flag(
        page, harness):
    piece = kit.open_home(page)
    panel = open_options(piece)
    open_sub(panel, "access", "Mode")
    control(panel, "permission_mode").select_option("bypassPermissions")
    panel.locator('[data-ask="permission_mode"]').get_by_role("button", name="Turn on").click()
    body = launch(page, piece, "say the word harm")
    assert body["off_posture"] is True and "filled" not in body, body
    # Opened again on the same folder: filled, in the warning colour, and nothing asked.
    piece = kit.open_home(page)
    expect(item(piece)).to_have_text("Permission mode▾")
    kit.assert_colour(item(piece).locator("span").first, "danger")
    listed = open_list(piece)
    kit.assert_colour(listed.locator('[data-fill="permission_mode"] .d'), "danger")
    expect(listed.locator(".fnote")).to_have_text(note("this folder's"))
    item(piece).click()
    expect(piece.locator('[data-line="changed"]')).to_have_count(0)
    expect(piece.locator(".opt")).to_have_text("Options")
    panel = open_options(piece)
    open_sub(panel, "access", "Mode")
    mode = control(panel, "permission_mode")
    expect(mode).to_have_value("bypassPermissions")
    kit.assert_colour(mode, "danger")
    kit.assert_colour(panel.locator('[data-from="permission_mode"] span'), "danger")
    expect(panel.locator('[data-ask="permission_mode"]')).to_have_count(0)
    brief = "say the word filled harm"
    body = launch(page, piece, brief)
    assert body["values"]["permission_mode"] == "bypassPermissions", body
    assert body["off_posture"] is True and body["filled"] == ["permission_mode"], body
    expect(piece.locator('.said [data-line="refused"]')).to_have_count(0)
    assert flagged(argv_of(harness, brief), "--permission-mode") == ["bypassPermissions"]
    # A filled value is not one turned on by hand: it stays after the launch.
    expect(item(piece)).to_have_text("Permission mode▾")
    expect(mode).to_have_value("bypassPermissions")


def test_another_harness_the_last_launch_ran_fills_first_and_clear_takes_the_form_back(page, harness):
    piece = kit.open_home(page)
    chip = piece.locator(".chip")
    chip.click()
    piece.locator('[data-menu="chip"] [data-list="harness"] .row', has_text="Codex").click()
    expect(chip).to_have_text(re.compile(r"^codex · default model\s*▾$"))
    page.keyboard.press("Escape")
    body = launch(page, piece, "say the word codex")
    assert body["values"]["harness"] == "codex", body
    piece = kit.open_home(page)
    expect(chip).to_have_text(re.compile(r"^codex · default model\s*▾$"))
    expect(item(piece)).to_have_text("Harness▾")
    listed = open_list(piece)
    expect(listed.locator("[data-fill]")).to_have_text(["HarnesscodexClear"])
    body = press(page, piece)
    assert body["values"]["harness"] == "codex" and body["filled"] == ["harness"], body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    open_list(piece).get_by_role("button", name="Clear Harness").click()
    expect(item(piece)).to_have_count(0)
    expect(chip).to_have_text(re.compile(r"^claude · default model\s*▾$"))


def pick_harness(piece, label: str) -> None:
    chip = piece.locator(".chip")
    chip.click()
    piece.locator('[data-menu="chip"] [data-list="harness"] .row', has_text=label).click()
    expect(chip).to_have_text(re.compile(rf"^{label.lower()} · default model\s*▾$"))
    piece.page.keyboard.press("Escape")


def comms_shown(route) -> None:
    """`/api/catalog`'s answer with the comms identity and scope rows shown, as with comms."""
    answer = route.fetch()
    data = answer.json()
    for harness in data["harnesses"]:
        for row in harness["fields"]:
            if row["name"] in ("identity", "scope"):
                row["shown"] = True
    route.fulfill(response=answer, json=data)


def test_a_harness_picked_by_hand_fills_its_comms_identity_with_comms_refusal_and_a_namesakes_note(page):
    at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    held = {"code": "identity-taken", "row": "identity", "problem": "identity cdx is held by slot 1",
            "hint": "pick another identity, or wait until that session ends"}
    namesake = ("cdx@proj-a is live in its project scope; the global name is the one filled. "
                "Pick that scope only if you mean that binding")
    last = {"state": "up", "detail": None, "at": at, "harness": "claude",
            "values": [{"name": "native_name", "value": "helper-1", "items": None}],
            "identities": [{"harness": "claude", "identity": "scout", "at": at, "refused": None, "note": None},
                           {"harness": "codex", "identity": "cdx", "at": at, "refused": held, "note": namesake}]}
    page.route("**" + CATALOG, comms_shown)
    page.route(re.compile(re.escape(LAST) + r"\?"), lambda route: route.fulfill(json=last))
    piece = kit.open_home(page)
    # The Claude launch's own value and Claude's identity fill.
    expect(item(piece)).to_have_text("2 settings▾")
    listed = open_list(piece)
    expect(listed.locator("[data-fill]")).to_have_text(["Comms identityscoutClear", "Native namehelper-1Clear"])
    item(piece).click()
    # Codex picked by hand: the Claude launch's value goes, Codex's identity fills, and
    # comms' refusal and the live namesake's note go beside it.
    pick_harness(piece, "Codex")
    expect(item(piece)).to_have_text("Comms identity▾")
    expect(piece.locator(".where .k").last).to_have_text("From last launch")
    listed = open_list(piece)
    expect(listed.locator("[data-fill]")).to_have_count(1)
    filled = listed.locator('[data-fill="identity"]')
    expect(filled).to_contain_text("Comms identitycdxClear")
    expect(filled.locator('[data-refused="identity-taken"]')).to_have_text(
        "identity cdx is held by slot 1. pick another identity, or wait until that session ends.")
    expect(filled.locator("[data-namesake]")).to_have_text(namesake + ".")
    item(piece).click()
    panel = open_options(piece)
    open_sub(panel, "seat", "Comms")
    expect(control(panel, "identity")).to_have_value("cdx")
    expect(panel.locator('[data-from="identity"]')).to_have_text(re.compile(r"^from the last launch, today .+Clear$"))
    expect(panel.locator('[data-refused="identity-taken"]')).to_have_count(1)
    expect(panel.locator("[data-namesake]")).to_have_text(namesake + ".")
    # Back to Claude: Claude's identity, still none of the launch's values, nothing beside it.
    pick_harness(piece, "Claude")
    expect(item(piece)).to_have_text("Comms identity▾")
    expect(control(panel, "identity")).to_have_value("scout")
    expect(panel.locator("[data-refused], [data-namesake]")).to_have_count(0)
    # An identity set by hand stays the user's across a harness picked.
    control(panel, "identity").fill("mine")
    control(panel, "identity").blur()
    expect(item(piece)).to_have_count(0)
    pick_harness(piece, "Codex")
    expect(control(panel, "identity")).to_have_value("mine")
    expect(item(piece)).to_have_count(0)
    expect(panel.locator("[data-refused], [data-namesake]")).to_have_count(0)


def test_a_harness_picked_by_hand_fills_its_comms_identity_when_the_opening_form_filled_nothing(page):
    at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    last = {"state": "up", "detail": None, "at": at, "harness": "codex",
            "values": [{"name": "native_name", "value": "helper-1", "items": None}],
            "identities": [{"harness": "codex", "identity": "cdx", "at": at, "refused": None, "note": None}]}
    page.route("**" + CATALOG, comms_shown)
    page.route(re.compile(re.escape(LAST) + r"\?seat=planner"), lambda route: route.fulfill(json=last))
    piece = kit.open_home(page)
    # The seat sets Claude, which the Codex launch gives no identity: nothing fills.
    pick_seat(piece, "planner")
    expect(piece.locator(".go")).to_be_enabled()
    expect(item(piece)).to_have_count(0)
    # Codex picked by hand: its identity fills all the same.
    pick_harness(piece, "Codex")
    expect(item(piece)).to_have_text("Comms identity▾")
    listed = open_list(piece)
    expect(listed.locator("[data-fill]")).to_have_text(["Comms identitycdxClear"])
