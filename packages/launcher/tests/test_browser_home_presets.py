"""The Preset item on the line above Home's box in a browser, through a real shell that
starts the launcher's real page server: Home opening on a project with that project's
preset applied, the menu of project presets and seats with what each sets and where it
puts the launch, a preset picked and named in the launch with its values on the harness's
command line, a place picked while a seat is applied, a seat that pins a slot launching in
the slot the button names, the line under the box for the rows a preset sets off the
user's usual values, and a tool denied under a preset beside the preset's own rule.

The shell runs the fake slots in slots' place and a fake `claude` first on its `PATH`, so
no launch starts a real session. Its log is seeded with one place ahead of the clock, the
project `proj-a`, so Home always opens there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit
from moxi_launcher import presets

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' picker is checked in a browser on Linux")]

PREVIEW = "/m/launcher/api/preview"

PRESETS = """\
[seat.planner]
harness = "claude"
model = "opus"
effort = "high"
home = "proj-b"

[seat.pilot]
harness = "claude"
model = "sonnet"
slot = "3"

[seat.critic]
harness = "claude"
permission_mode = "bypassPermissions"

[seat.reviewer]
harness = "claude"
disallowed_tools = ["Bash(git push:*)"]

[project.proj-a]
project = "proj-a"
harness = "claude"
model = "sonnet"
effort = "low"
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    return kit.Harness(tmp_path_factory.mktemp("fake-harness"))


@pytest.fixture(scope="module")
def on_path(harness):
    """The fake harness first on `PATH` for every shell this module starts."""
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
    """A shell with the projects `proj-a` and `proj-b`, the presets above, and `proj-a` the
    newest place."""
    module_fake.script(placed=2, slots=kit.free(2, 3))
    shell = ui_shells(**module_fake.options())
    root = add_project(shell, "proj-a")
    add_project(shell, "proj-b")
    file = presets.path(env=shell.env)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(PRESETS)
    kit.place(shell, "place-project", cwd=str(root), project_key="proj-a", at="2098-01-01T09:00:00.000000Z")
    return shell


@pytest.fixture
def fake(module_fake):
    """Slots 2 and 3 free, and a start placed in slot 2."""
    module_fake.script(placed=2, slots=kit.free(2, 3))
    return module_fake


@pytest.fixture
def page(shell, fake, ui_contexts):
    return ui_contexts(shell).pages[0]


def item(piece):
    """The Preset item's face on the line above the box."""
    return piece.locator('.where [data-item="preset"] > button')


def expect_line(piece, place: str, preset: str) -> None:
    """The line above the box reads `Project <place> ▾ · Preset <preset> ▾`."""
    expect(piece.locator(".where .k")).to_have_text(["Project", "Preset"])
    expect(piece.locator(".where .face")).to_have_text(f"{place}▾")
    expect(item(piece)).to_have_text(f"{preset}▾")


def open_presets(piece):
    """The Preset menu, opened."""
    item(piece).click()
    menu = piece.locator('[data-menu="preset"]')
    expect(menu).to_be_visible()
    return menu


def pick_preset(piece, key: str) -> None:
    """The preset `key` (`seat:<name>`, `project:<name>`, "" for none) picked in the menu."""
    open_presets(piece).locator(f'button[data-preset="{key}"]').click()
    expect(piece.locator('[data-menu="preset"]')).to_have_count(0)


def pick_place(piece, key: str) -> None:
    """The project `key` picked from the places on the line above the box."""
    piece.locator(".where .face").click()
    piece.locator('[data-menu="where"] .places .row').filter(has_text=key).click()


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


def argv_of(harness, brief: str) -> list[str]:
    [run] = wait_for(lambda: [run for run in harness.runs() if brief in " ".join(run["argv"])],
                     10, what="the fake harness's run with the brief")
    return run["argv"]


def flagged(argv: list[str], flag: str) -> list[str]:
    return [argv[at + 1] for at, word in enumerate(argv[:-1]) if word == flag]


def test_home_opens_on_a_project_with_its_preset_and_the_menu_lists_every_preset(page):
    piece = kit.open_home(page)
    expect_line(piece, "proj-a", "proj-a")
    # The project's preset is the form's fill, so the chip shows its model and nothing is changed.
    expect(piece.locator(".chip")).to_have_text("claude · sonnet · low▾")
    expect(piece.locator(".opt")).to_have_text("Options")
    expect(piece.locator('[data-line="changed"]')).to_have_count(0)
    menu = open_presets(piece)
    rows = menu.locator("button.row")
    expect(rows.locator(".name")).to_have_text(["No preset", "proj-a", "critic", "pilot", "planner", "reviewer"])
    expect(menu.locator(".hd")).to_have_text(["Project presets", "Agent presets"])
    kit.assert_colour(menu.locator(".hd").first, "text-dim")
    expect(rows.locator(".d")).to_have_text(["", "claude · sonnet · low", "claude · bypassPermissions", "claude · sonnet",
                                             "claude · opus · high", "claude"])
    expect(rows.locator(".t")).to_have_text(["", "this project", "", "", "in proj-b", ""])
    expect(menu.locator('button.row[data-current="true"] .name')).to_have_text(["proj-a"])
    # A value that can do harm is in the warning colour in the row that says what it sets.
    danger = menu.locator('button[data-preset="seat:critic"] .d .dg')
    expect(danger).to_have_text("bypassPermissions")
    kit.assert_colour(danger, "danger")
    kit.assert_colour(menu.locator('button[data-preset="seat:pilot"] .d span').first, "text-dim")
    # No preset takes it off: the item reads none, dim, and the request names no preset.
    rows.filter(has_text="No preset").click()
    expect(item(piece)).to_have_text("none▾")
    kit.assert_colour(item(piece).locator(".p"), "text-dim")
    expect(piece.locator(".chip")).to_have_text("claude · default model▾")
    expect_line(piece, "proj-a", "none")
    piece.locator("textarea").fill("say the word none")
    body = press(page, piece)
    assert "seat" not in body and "project_preset" not in body, body
    assert body["values"]["project"] == "proj-a", body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()


def test_a_preset_picked_is_named_in_the_launch_and_its_values_reach_the_command_line(page, harness):
    piece = kit.open_home(page)
    pick_preset(piece, "seat:planner")
    # The seat's home is the line's place now.
    expect_line(piece, "proj-b", "planner")
    expect(piece.locator(".chip")).to_have_text("claude · opus · high▾")
    expect(piece.locator(".opt")).to_have_text("Options")
    expect(piece.locator('[data-line="changed"]')).to_have_count(0)
    expect(piece.locator('[data-line="preset"]')).to_have_count(0)
    headless(piece)
    brief = "say the word planner"
    piece.locator("textarea").fill(brief)
    body = press(page, piece)
    assert body["seat"] == "planner" and "project_preset" not in body, body
    assert body["values"]["project"] == "proj-b" and body["values"]["model"] == "opus", body
    assert "off_posture" not in body, body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    argv = argv_of(harness, brief)
    assert flagged(argv, "--model") == ["opus"] and flagged(argv, "--effort") == ["high"], argv
    # A project picked while the seat is applied is only the place: the seat stays.
    pick_place(piece, "proj-a")
    expect_line(piece, "proj-a", "planner")
    expect(piece.locator(".chip")).to_have_text("claude · opus · high▾")
    piece.locator("textarea").fill("say the word planner again")
    body = press(page, piece)
    assert body["seat"] == "planner" and body["values"]["project"] == "proj-a", body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    # With none applied, a project picked applies its own preset again.
    pick_preset(piece, "")
    pick_place(piece, "proj-b")
    pick_place(piece, "proj-a")
    expect_line(piece, "proj-a", "proj-a")


def test_a_seat_whose_preset_pins_a_slot_launches_in_the_slot_the_button_names(page, fake, shell):
    piece = kit.open_home(page)
    pick_preset(piece, "seat:pilot")
    expect(item(piece)).to_have_text("pilot▾")
    go = piece.locator(".go")
    expect(go).to_have_text("Launch in slot 2")
    piece.locator("textarea").fill("say the word pilot")
    body = press(page, piece)
    assert body["seat"] == "pilot" and "slot" not in body["values"] and "slot" in body["leave_out"], body
    expect(piece.locator('.said [data-line="started"]')).to_contain_text("Started in slot 2")
    assert fake.starts()[-1]["slot"] is None, fake.starts()[-1]
    row = kit.newest_launch(shell)
    assert (row.target, row.slot, row.preset_kind, row.preset_name) == ("slot", "2", "seat", "pilot"), row


def test_a_preset_off_the_usual_permissions_says_so_under_the_box_and_launches_with_them(page, harness):
    piece = kit.open_home(page)
    pick_preset(piece, "seat:critic")
    line = piece.locator('[data-line="preset"]')
    expect(line).to_have_text("Preset critic sets Permission mode off your defaults")
    kit.assert_colour(line, "text-dim")
    kit.assert_colour(line.locator("b"), "danger")
    # The preset's value is the baseline: no Changed line, no count, nothing to turn on.
    expect(piece.locator('[data-line="changed"]')).to_have_count(0)
    expect(piece.locator(".opt")).to_have_text("Options")
    headless(piece)
    brief = "say the word critic"
    piece.locator("textarea").fill(brief)
    body = press(page, piece)
    assert body["seat"] == "critic" and body.get("off_posture") is True, body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    argv = argv_of(harness, brief)
    assert flagged(argv, "--permission-mode") == ["bypassPermissions"], argv
    # Taken off, the line goes with it.
    pick_preset(piece, "")
    expect(line).to_have_count(0)


def test_a_tool_denied_under_a_preset_launches_with_the_presets_scoped_rule_beside_it(page, harness):
    piece = kit.open_home(page)
    pick_preset(piece, "seat:reviewer")
    expect(piece.locator('[data-line="preset"]')).to_have_text(
        "Preset reviewer sets Disallowed tools off your defaults")
    piece.get_by_role("button", name="Options", exact=True).click()
    panel = piece.get_by_role("complementary", name="Options")
    panel.locator('button[data-group="tools"]').click()
    panel.locator('button[data-sub="tools/Built-in"]').click()
    view = panel.locator('[data-view="tools"]')
    bash = view.locator('[data-tool="Bash"]')
    bash.get_by_role("button", name="1 rule").click()
    expect(panel.locator('[data-rules="Bash"] [data-rule] .re')).to_have_text("Bash(git push:*)")
    search = view.locator('[data-tool="WebSearch"]')
    search.get_by_role("button", name="deny", exact=True).click()
    expect(piece.locator('[data-line="changed"] b')).to_have_text(["Disallowed tools"])
    expect(piece.locator(".opt")).to_have_text("Options · 1")
    # Typed now, the row is the Changed line's, not the preset's.
    expect(piece.locator('[data-line="preset"]')).to_have_count(0)
    headless(piece)
    brief = "say the word reviewer"
    piece.locator("textarea").fill(brief)
    body = press(page, piece)
    assert body["seat"] == "reviewer" and body.get("off_posture") is True, body
    assert body["values"]["disallowed_tools"] == ["Bash(git push:*)", "WebSearch"], body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    argv = argv_of(harness, brief)
    assert flagged(argv, "--disallowedTools") == ["Bash(git push:*)", "WebSearch"], argv
