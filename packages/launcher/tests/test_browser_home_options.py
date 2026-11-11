"""The Options panel of the box on Home in a browser, through a real shell that starts the
launcher's real page server: the word that opens it, the panel at the window's right edge
with Home's column moved aside, its groups and their subgroups, the find field, a row
changed there reaching the harness's command line, the line under the box that names the
changed rows with its `Reset`, a value that can do harm turned on and launched once, the
rows the button's place draws, a tool denied in Tools > Built-in reaching the command line
beside the rules the defaults hold, and the panel on a narrow page.

The shell runs the fake slots in slots' place and a fake `claude` first on its `PATH`, so
a launch from Home starts no real session.
"""

from __future__ import annotations

import json
import sys

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit
from moxi_launcher import presets

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(sys.platform == "win32",
                                 reason="the fake harness is a script run by its #! line")]

BRIEF = "say the word options"
PREVIEW = "/m/launcher/api/preview"
CATALOG = "**/m/launcher/api/catalog"
GROUPS = ["access", "tools", "seat", "runs-in", "lifecycle", "config"]
#: The panel's width, and Home's column's.
PANEL, COLUMN = 400, 680


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


@pytest.fixture(scope="module")
def ui_shell_options(module_fake, on_path):
    return module_fake.options()


@pytest.fixture
def fake(module_fake):
    """The shell's fake slots, with slots 2 and 3 free and a start placed in slot 2."""
    module_fake.script(placed=2, slots=kit.free(2, 3))
    return module_fake


def home_at(page, width: int, height: int = 900):
    """Home opened in a window `width` px wide; returns the launcher's piece."""
    page.set_viewport_size({"width": width, "height": height})
    return kit.open_home(page)


def open_options(piece):
    """The panel, opened by the word beside the chip."""
    piece.get_by_role("button", name="Options", exact=True).click()
    panel = piece.get_by_role("complementary", name="Options")
    expect(panel).to_be_visible()
    return panel


def expect_box_at(piece, left: int) -> None:
    """The box's left edge comes to `left`, read again until it does."""
    seen: list[float] = []

    def there() -> bool:
        seen.append(piece.locator(".box").bounding_box()["x"])
        return round(seen[-1]) == left

    wait_for(there, 5, interval=0.05, what=lambda: f"the box's left edge at {left}; last seen {seen[-1:]}")


def group(panel, key: str):
    return panel.locator(f'button[data-group="{key}"]')


def row(panel, name: str):
    """The panel's row `name`: the form renderer's control for it."""
    return panel.locator(f'[data-row="{name}"] [data-control="{name}"]')


def test_options_opens_a_panel_at_the_right_edge_and_the_column_moves_aside(fake, ui_page):
    piece = home_at(ui_page, 1400)
    centred = (1400 - COLUMN) // 2
    expect_box_at(piece, centred)
    word = piece.get_by_role("button", name="Options", exact=True)
    expect(word).to_have_attribute("aria-expanded", "false")
    kit.assert_colour(word, "text-dim")
    panel = open_options(piece)
    expect(word).to_have_attribute("aria-expanded", "true")
    # The column is centred in what the panel leaves of the window.
    expect_box_at(piece, (1400 - PANEL - COLUMN) // 2)
    found = panel.bounding_box()
    assert (found["x"], found["y"], found["width"], found["height"]) == (1400 - PANEL, 0, PANEL, 900), found
    assert found["x"] + found["width"] == 1400, found
    box = piece.locator(".box").bounding_box()
    assert box["x"] + box["width"] <= found["x"], (box, found)
    # The six groups, closed, each saying its rows are at their defaults.
    groups = panel.locator("button[data-group]")
    assert groups.evaluate_all("found => found.map((one) => one.dataset.group)") == GROUPS
    assert groups.locator("b").all_inner_texts() == ["Access", "Tools", "Seat", "Runs in", "Lifecycle", "Config"]
    assert set(groups.locator(".sum").all_inner_texts()) == {"defaults"}
    expect(panel.locator("[data-row]")).to_have_count(0)
    # The box and the button are still in use beside it.
    piece.locator("textarea").fill(BRIEF)
    expect(piece.locator("textarea")).to_have_value(BRIEF)
    expect(piece.locator(".go")).to_be_enabled()
    piece.locator(".chip").click()
    expect(piece.locator('[data-menu="chip"]')).to_be_visible()
    ui_page.keyboard.press("Escape")
    expect(piece.locator('[data-menu="chip"]')).to_have_count(0)
    expect(panel).to_be_visible()
    # Closing puts the column back.
    panel.get_by_role("button", name="Close").click()
    expect(panel).to_have_count(0)
    expect_box_at(piece, centred)
    expect(word).to_be_focused()
    expect(piece.locator("textarea")).to_have_value(BRIEF)
    # So does Escape, and the word closes the panel it opened.
    panel = open_options(piece)
    expect_box_at(piece, (1400 - PANEL - COLUMN) // 2)
    panel.get_by_label("Find an option").press("Escape")
    expect(panel).to_have_count(0)
    expect_box_at(piece, centred)
    open_options(piece)
    word.click()
    expect(piece.get_by_role("complementary", name="Options")).to_have_count(0)


def test_a_group_opens_in_place_with_its_subgroups_and_their_rows(fake, ui_page):
    piece = home_at(ui_page, 1400)
    panel = open_options(piece)
    access = group(panel, "access")
    access.click()
    expect(access).to_have_attribute("aria-expanded", "true")
    subs = panel.locator('[data-body="access"] button[data-sub]')
    assert subs.evaluate_all("found => found.map((one) => one.dataset.sub)") == ["access/Mode", "access/Reach"]
    assert subs.locator(".sum").all_inner_texts() == ["defaults", "defaults"]
    # The body sits between its group's line and the next group's.
    body = panel.locator('[data-body="access"]').bounding_box()
    assert access.bounding_box()["y"] < body["y"] < group(panel, "tools").bounding_box()["y"], body
    expect(panel.locator("[data-row]")).to_have_count(0)
    panel.locator('button[data-sub="access/Mode"]').click()
    mode = row(panel, "permission_mode")
    expect(mode).to_have_value("default")
    expect(panel.locator('[data-row="permission_mode"] label')).to_have_text("Permission mode")
    # A list row is the plain list control; the rules of Tools > Built-in are a line per tool.
    group(panel, "tools").click()
    panel.locator('button[data-sub="tools/Built-in"]').click()
    built_in = panel.locator('[data-body="tools"] [data-row]')
    assert built_in.evaluate_all("found => found.map((one) => one.dataset.row)")[:1] == ["tools"]
    expect(panel.locator('[data-row="tools"]').get_by_role("button", name="Add")).to_be_visible()
    expect(panel.locator('[data-view="tools"] [data-tool]')).to_have_count(9)
    # The rows the box, the line above it, the chip and the button hold are not here.
    for key in GROUPS:
        if group(panel, key).get_attribute("aria-expanded") == "false":
            group(panel, key).click()
    for key in panel.locator("button[data-sub]").evaluate_all("found => found.map((one) => one.dataset.sub)"):
        sub = panel.locator(f'button[data-sub="{key}"]')
        if sub.get_attribute("aria-expanded") == "false":
            sub.click()
    drawn = panel.locator("[data-row]").evaluate_all("found => found.map((one) => one.dataset.row)")
    assert len(drawn) == len(set(drawn)) > 10, drawn
    assert not {"brief", "brief_file", "handoff", "cwd", "project", "target", "slot", "harness", "model",
                "effort"} & set(drawn), drawn
    # Closing a group folds its rows away.
    access.click()
    expect(panel.locator('[data-row="permission_mode"]')).to_have_count(0)


def test_a_row_found_and_changed_is_in_the_launch_and_on_its_groups_line(fake, ui_page, ui_shell, harness):
    piece = home_at(ui_page, 1400)
    panel = open_options(piece)
    find = panel.get_by_label("Find an option")
    find.fill("no such option")
    expect(panel.get_by_role("status")).to_have_text("No option matches.")
    expect(panel.locator("button[data-group]")).to_have_count(0)
    # The help text is read too, whatever the case.
    find.fill("DISPLAY NAME")
    expect(panel.locator("[data-row]")).to_have_count(1)
    expect(panel.locator('[data-row="native_name"] .at')).to_have_text("Seat › Name & notes")
    name = row(panel, "native_name")
    name.fill("helper-1")
    name.blur()
    find.fill("extra folders")
    expect(panel.locator("[data-row]")).to_have_count(1)
    folder = str(ui_shell.env["HOME"])
    row(panel, "add_dir").fill(folder)
    panel.locator('[data-row="add_dir"]').get_by_role("button", name="Add").click()
    expect(panel.locator('[data-row="add_dir"] li')).to_have_text([f"{folder}Remove"])
    # With the field emptied the groups are back, and say what was changed.
    find.fill("")
    expect(group(panel, "seat").locator(".sum")).to_have_text("Native name helper-1")
    expect(group(panel, "access").locator(".sum")).to_have_text("Extra folders 1")
    expect(group(panel, "tools").locator(".sum")).to_have_text("defaults")
    group(panel, "seat").click()
    expect(panel.locator('button[data-sub="seat/Name & notes"] .sum')).to_have_text("Native name helper-1")
    panel.locator('button[data-sub="seat/Name & notes"]').click()
    expect(row(panel, "native_name")).to_have_value("helper-1")
    # The launch carries both, beside what the box and the button set.
    piece.locator(".more").click()
    piece.locator('[data-menu="run"] button[data-run="Headless"]').click()
    piece.locator("textarea").fill(BRIEF)
    with ui_page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    body = json.loads(sent.value.post_data)
    assert body["values"]["native_name"] == "helper-1" and body["values"]["add_dir"] == [folder], body
    assert body["values"]["brief"] == BRIEF and body["values"]["target"] == "headless", body
    assert "off_posture" not in body and "handoff" not in body["values"], body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    [run] = wait_for(lambda: [run for run in harness.runs() if BRIEF in " ".join(run["argv"])],
                     10, what="the fake harness's run with the brief")
    argv = run["argv"]
    assert argv[argv.index("-n") + 1] == "helper-1", argv
    assert argv[argv.index("--add-dir") + 1] == folder, argv
    # The panel stays open through the launch, with its values.
    expect(row(panel, "native_name")).to_have_value("helper-1")


def changed_line(piece):
    """The line under the box that names the changed rows."""
    return piece.locator('[data-line="changed"]')


def open_mode(panel):
    """Access > Mode opened; returns the permission mode's control."""
    group(panel, "access").click()
    panel.locator('button[data-sub="access/Mode"]').click()
    return row(panel, "permission_mode")


def test_a_changed_row_is_named_by_its_label_under_the_box_and_reset_puts_it_back(fake, ui_page):
    piece = home_at(ui_page, 1400)
    word = piece.locator(".opt")
    expect(word).to_have_text("Options")
    expect(changed_line(piece)).to_have_count(0)
    piece.locator("textarea").fill(BRIEF)
    piece.locator(".more").click()
    piece.locator('[data-menu="run"] button[data-run="Headless"]').click()
    panel = open_options(piece)
    mode = open_mode(panel)
    mode.select_option("plan")
    line = changed_line(piece)
    expect(line.locator("b")).to_have_text(["Permission mode"])
    expect(line).to_have_text("ChangedPermission modeReset")
    expect(word).to_have_text("Options · 1")
    kit.assert_colour(word, "text")
    kit.assert_colour(line, "text-dim")
    kit.assert_colour(line.locator("b"), "text")
    # The line sits under the box, in its column.
    box = piece.locator(".box").bounding_box()
    found = line.bounding_box()
    assert found["y"] >= box["y"] + box["height"] and found["x"] >= box["x"], (box, found)
    assert found["x"] + found["width"] <= box["x"] + box["width"], (box, found)
    # Home's next row moves down to make room for it.
    below = ui_page.get_by_text("Health", exact=True).bounding_box()
    assert below["y"] >= found["y"] + found["height"] + 10, (found, below)
    # A second row joins it in the panel's order, whichever was changed first.
    group(panel, "seat").click()
    panel.locator('button[data-sub="seat/Name & notes"]').click()
    name = row(panel, "native_name")
    name.fill("helper-1")
    name.blur()
    expect(line.locator("b")).to_have_text(["Permission mode", "Native name"])
    expect(word).to_have_text("Options · 2")
    # A row set back by hand leaves the line.
    mode.select_option("default")
    expect(line.locator("b")).to_have_text(["Native name"])
    expect(word).to_have_text("Options · 1")
    mode.select_option("plan")
    expect(word).to_have_text("Options · 2")
    # Reset puts every changed row back and leaves the box, the button and the panel.
    line.get_by_role("button", name="Reset").click()
    expect(line).to_have_count(0)
    expect(word).to_have_text("Options")
    kit.assert_colour(word, "text-dim")
    expect(mode).to_have_value("default")
    expect(name).to_have_value("")
    expect(piece.locator("textarea")).to_have_value(BRIEF)
    expect(piece.locator(".go")).to_have_text("Launch headless")
    expect(panel).to_be_visible()
    with ui_page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    body = json.loads(sent.value.post_data)
    assert body["values"]["permission_mode"] == "default" and "native_name" in body["leave_out"], body
    assert "off_posture" not in body, body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()


def headless(piece) -> None:
    """The button's menu set to launch in the background."""
    piece.locator(".more").click()
    piece.locator('[data-menu="run"] button[data-run="Headless"]').click()
    expect(piece.locator(".go")).to_have_text("Launch headless")


def argv_of(harness, brief: str) -> list[str]:
    """The command line of the fake harness's run with `brief`, once there is one."""
    [run] = wait_for(lambda: [run for run in harness.runs() if brief in " ".join(run["argv"])],
                     10, what="the fake harness's run with the brief")
    return run["argv"]


def press(page, piece) -> dict:
    """Press the button, and return the body the piece sends to be previewed."""
    with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    return json.loads(sent.value.post_data)


def test_a_value_that_can_do_harm_is_on_after_turn_on_and_launches_with_its_flag_once(fake, ui_page, harness):
    piece = home_at(ui_page, 1400)
    panel = open_options(piece)
    mode = open_mode(panel)
    asked = panel.locator('[data-ask="permission_mode"]')
    expect(asked).to_have_count(0)
    mode.select_option("bypassPermissions")
    # Picked, it is not set: two dim sentences and the two acts under it.
    expect(asked.locator("p")).to_have_text(
        "Every tool runs without asking, whatever the mode and the rules say. Confirmed for this launch only.")
    kit.assert_colour(asked.locator("p"), "text-dim")
    expect(changed_line(piece)).to_have_count(0)
    expect(piece.locator(".opt")).to_have_text("Options")
    kit.assert_colour(panel.locator('[data-row="permission_mode"] label'), "text")
    asked.get_by_role("button", name="Cancel").click()
    expect(asked).to_have_count(0)
    expect(mode).to_have_value("default")
    expect(changed_line(piece)).to_have_count(0)
    # Turned on, it is in the warning colour wherever it is drawn.
    mode.select_option("bypassPermissions")
    asked.get_by_role("button", name="Turn on").click()
    expect(mode).to_have_value("bypassPermissions")
    expect(asked.get_by_role("button")).to_have_count(0)
    expect(asked.locator("p")).to_contain_text("Confirmed for this launch only.")
    kit.assert_colour(panel.locator('[data-row="permission_mode"] label'), "danger")
    kit.assert_colour(mode, "danger")
    # A second row, changed after it, is named after it; a row holding no such value is plain.
    panel.locator('button[data-sub="access/Reach"]').click()
    kit.assert_colour(panel.locator('[data-row="add_dir"] .label'), "text")
    folder = "/srv/proj-a"
    row(panel, "add_dir").fill(folder)
    panel.locator('[data-row="add_dir"]').get_by_role("button", name="Add").click()
    marks = changed_line(piece).locator("b")
    expect(marks).to_have_text(["Permission mode", "Extra folders"])
    kit.assert_colour(marks.nth(0), "danger")
    kit.assert_colour(marks.nth(1), "text")
    expect(piece.locator(".opt")).to_have_text("Options · 2")
    kit.assert_colour(piece.locator(".opt"), "text")
    # The closed subgroup and group say the value in the same colour.
    panel.locator('button[data-sub="access/Mode"]').click()
    said = panel.locator('button[data-sub="access/Mode"] .sum')
    expect(said).to_have_text("bypassPermissions")
    kit.assert_colour(said.locator("span"), "danger")
    group(panel, "access").click()
    words = group(panel, "access").locator(".sum span")
    expect(words).to_have_text(["bypassPermissions", "Extra folders 1"])
    kit.assert_colour(words.nth(0), "danger")
    kit.assert_colour(words.nth(1), "text-dim")
    # The launch says it is off the usual permissions and is not refused.
    piece.locator(".more").click()
    piece.locator('[data-menu="run"] button[data-run="Headless"]').click()
    brief = "say the word harm"
    piece.locator("textarea").fill(brief)
    with ui_page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    body = json.loads(sent.value.post_data)
    assert body["values"]["permission_mode"] == "bypassPermissions" and body["off_posture"] is True, body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    expect(piece.locator('.said [data-line="refused"]')).to_have_count(0)
    [run] = wait_for(lambda: [run for run in harness.runs() if brief in " ".join(run["argv"])],
                     10, what="the fake harness's run with the brief")
    argv = run["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions", argv
    # It held for that launch only: the row is back at the form's value, the line names the
    # other row alone, and nothing is in the warning colour.
    expect(marks).to_have_text(["Extra folders"])
    kit.assert_colour(marks, "text")
    expect(piece.locator(".opt")).to_have_text("Options · 1")
    group(panel, "access").click()
    panel.locator('button[data-sub="access/Mode"]').click()
    expect(mode).to_have_value("default")
    kit.assert_colour(panel.locator('[data-row="permission_mode"] label'), "text")
    expect(asked).to_have_count(0)
    # Picked again, it asks again, and is not set until `Turn on`.
    mode.select_option("bypassPermissions")
    expect(asked.get_by_role("button", name="Turn on")).to_be_visible()
    expect(marks).to_have_text(["Extra folders"])
    body = press(ui_page, piece)
    assert body["values"]["permission_mode"] == "default" and "off_posture" not in body, body
    expect(piece.locator('.said [data-line="refused"]')).to_be_visible()


def test_a_value_turned_on_stays_on_through_a_launch_that_fails_or_is_refused(fake, ui_page):
    fake.script(placed=2, slots=kit.free(2, 3), start="refused",
                refused={"reason": "occupied", "words": "slot 2 was taken a moment ago"})
    piece = home_at(ui_page, 1400)
    panel = open_options(piece)
    mode = open_mode(panel)
    asked = panel.locator('[data-ask="permission_mode"]')
    mode.select_option("bypassPermissions")
    asked.get_by_role("button", name="Turn on").click()
    marks = changed_line(piece).locator("b")
    expect(marks).to_have_text(["Permission mode"])

    def still_on() -> None:
        expect(marks).to_have_text(["Permission mode"])
        kit.assert_colour(marks, "danger")
        expect(piece.locator(".opt")).to_have_text("Options · 1")
        expect(mode).to_have_value("bypassPermissions")
        kit.assert_colour(mode, "danger")
        expect(asked.get_by_role("button")).to_have_count(0)

    # The slot is taken as the launch starts, and the launch fails.
    piece.locator("textarea").fill(BRIEF)
    body = press(ui_page, piece)
    assert body["values"]["permission_mode"] == "bypassPermissions" and body["off_posture"] is True, body
    expect(piece.locator('.said [data-line="failed"]')).to_be_visible()
    still_on()
    # In the background with nothing to do, the launch is refused.
    piece.locator("textarea").fill("")
    headless(piece)
    body = press(ui_page, piece)
    assert body["values"]["permission_mode"] == "bypassPermissions", body
    expect(piece.locator('.said [data-line="refused"]')).to_be_visible()
    still_on()
    # The next launch that starts takes it with nothing asked, and then lets go of it.
    piece.locator("textarea").fill("say the word again")
    body = press(ui_page, piece)
    assert body["values"]["permission_mode"] == "bypassPermissions" and body["off_posture"] is True, body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    expect(changed_line(piece)).to_have_count(0)
    expect(mode).to_have_value("default")


def test_a_value_that_can_do_harm_the_form_is_filled_with_stays_after_a_launch(fake, ui_page, ui_shell, harness):
    file = presets.path(env=ui_shell.env)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text('[defaults]\npermission_mode = "bypassPermissions"\n')
    try:
        piece = home_at(ui_page, 1400)
        panel = open_options(piece)
        mode = open_mode(panel)
        expect(mode).to_have_value("bypassPermissions")
        kit.assert_colour(mode, "danger")
        expect(panel.locator('[data-ask="permission_mode"]')).to_have_count(0)
        expect(changed_line(piece)).to_have_count(0)
        headless(piece)
        for brief in ("say the word filled", "say the word filled again"):
            piece.locator("textarea").fill(brief)
            body = press(ui_page, piece)
            assert body["values"]["permission_mode"] == "bypassPermissions" and "off_posture" not in body, body
            expect(piece.locator('.said [data-line="started"]')).to_be_visible()
            argv = argv_of(harness, brief)
            assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions", argv
            # The user's own default is not his pick for one launch: it stays, unnamed.
            expect(mode).to_have_value("bypassPermissions")
            kit.assert_colour(mode, "danger")
            expect(changed_line(piece)).to_have_count(0)
            expect(piece.locator(".opt")).to_have_text("Options")
    finally:
        file.unlink()


def test_the_warning_colour_is_the_themes_in_light_and_in_dark(fake, ui_contexts):
    seen = {}
    for scheme in ("light", "dark"):
        page = ui_contexts(color_scheme=scheme).pages[0]
        fake.script(placed=2, slots=kit.free(2, 3))
        piece = home_at(page, 1400)
        panel = open_options(piece)
        mode = open_mode(panel)
        mode.select_option("bypassPermissions")
        panel.locator('[data-ask="permission_mode"]').get_by_role("button", name="Turn on").click()
        mark = changed_line(piece).locator("b")
        kit.assert_colour(mark, "danger")
        kit.assert_colour(mode, "danger")
        seen[scheme] = mark.evaluate("(node) => getComputedStyle(node).color")
    assert seen["light"] != seen["dark"], seen


def test_a_row_whose_flag_the_harness_hides_carries_a_dim_mark(fake, ui_page):
    def hide(route) -> None:
        answer = route.fetch()
        data = answer.json()
        for one in data["harnesses"]:
            for field in one["fields"]:
                if field["name"] == "native_name":
                    field["hidden"] = True
        route.fulfill(response=answer, json=data)

    ui_page.route(CATALOG, hide)
    piece = home_at(ui_page, 1400)
    panel = open_options(piece)
    group(panel, "seat").click()
    panel.locator('button[data-sub="seat/Name & notes"]').click()
    mark = panel.locator('[data-row="native_name"] [data-mark="hidden"]')
    expect(mark).to_have_text("hidden flag")
    kit.assert_colour(mark, "text-dim")
    # The row is drawn in place with the others, with its control.
    expect(row(panel, "native_name")).to_be_visible()
    expect(panel.locator('[data-row="notes_dir"] [data-mark="hidden"]')).to_have_count(0)
    expect(panel.locator("details:visible")).to_have_count(0)


def test_on_a_narrow_page_the_panel_lies_over_the_page(fake, ui_page, ui_width_check):
    piece = home_at(ui_page, 400, 800)
    before = piece.locator(".box").bounding_box()
    panel = open_options(piece)
    found = panel.bounding_box()
    assert (found["x"], found["y"], found["width"], found["height"]) == (0, 0, 400, 800), found
    # The page under it has not moved.
    assert piece.locator(".box").bounding_box() == before
    ui_width_check(ui_page, width=400)
    group(panel, "seat").click()
    panel.locator('button[data-sub="seat/Name & notes"]').click()
    expect(row(panel, "native_name")).to_be_visible()
    ui_width_check(ui_page, width=400)
    ui_width_check(ui_page, width=360)
    ui_page.set_viewport_size({"width": 400, "height": 800})
    panel.get_by_role("button", name="Close").click()
    expect(panel).to_have_count(0)
    assert piece.locator(".box").bounding_box() == before


def test_the_panel_is_the_pages_colours_in_light_and_in_dark(fake, ui_contexts):
    seen = {}
    for scheme in ("light", "dark"):
        page = ui_contexts(color_scheme=scheme).pages[0]
        fake.script(placed=2, slots=kit.free(2, 3))
        piece = home_at(page, 1400)
        panel = open_options(piece)
        kit.assert_colour(group(panel, "access").locator("b"), "text")
        kit.assert_colour(group(panel, "access").locator(".sum"), "text-dim")
        seen[scheme] = panel.evaluate("""(panel) => ({
            panel: getComputedStyle(panel).backgroundColor,
            page: getComputedStyle(document.body).backgroundColor,
        })""")
        assert seen[scheme]["panel"] == seen[scheme]["page"], seen
    assert seen["light"] != seen["dark"], seen


def tools_view(panel):
    """Tools > Built-in opened; returns its lines' frame."""
    group(panel, "tools").click()
    panel.locator('button[data-sub="tools/Built-in"]').click()
    view = panel.locator('[data-view="tools"]')
    expect(view.locator("[data-tool]")).to_have_count(9)
    return view


def flagged(argv: list[str], flag: str) -> list[str]:
    """The items a command line gives `flag`, each after one of its own."""
    return [argv[at + 1] for at, word in enumerate(argv[:-1]) if word == flag]


def test_a_row_drawn_for_one_places_mode_is_gone_once_the_button_launches_in_another(fake, ui_page):
    piece = home_at(ui_page, 1400)
    expect(piece.locator(".go")).to_have_text("Launch in slot 2")
    panel = open_options(piece)
    # In a slot the session is interactive, and Remote Control is passed to it.
    group(panel, "runs-in").click()
    panel.locator('button[data-sub="runs-in/Remote"]').click()
    remote = panel.get_by_label("Remote Control", exact=True)
    remote.check()
    expect(changed_line(piece).locator("b")).to_have_text(["Remote Control"])
    # A headless session is not; the row, its group and its value go.
    headless(piece)
    expect(panel.locator('[data-row="remote_control"]')).to_have_count(0)
    expect(group(panel, "runs-in")).to_have_count(0)
    expect(changed_line(piece)).to_have_count(0)
    expect(piece.locator(".opt")).to_have_text("Options")
    find = panel.get_by_label("Find an option")
    find.fill("remote")
    expect(panel.get_by_role("status")).to_have_text("No option matches.")
    find.fill("")
    piece.locator("textarea").fill("say the word modes")
    body = press(ui_page, piece)
    assert "remote_control" not in body["values"] and "remote_control" in body["leave_out"], body
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    # Back in a slot it is drawn again, open as it was left, at what the form is filled with.
    piece.locator(".more").click()
    piece.locator('[data-menu="run"] button[data-run="Slot 2"]').click()
    expect(group(panel, "runs-in")).to_have_attribute("aria-expanded", "true")
    expect(panel.get_by_label("Remote Control", exact=True)).not_to_be_checked()


def test_a_tool_denied_launches_with_the_defaults_scoped_rule_beside_it(fake, ui_page, ui_shell, harness):
    file = presets.path(env=ui_shell.env)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text('[defaults]\ndisallowed_tools = ["Bash(git push:*)"]\n')
    try:
        piece = home_at(ui_page, 1400)
        panel = open_options(piece)
        view = tools_view(panel)
        expect(panel.locator('[data-row="tools"]')).to_contain_text("every built-in tool")
        # The scoped rule is counted on its tool's line and opens to its row and Remove.
        bash = view.locator('[data-tool="Bash"]')
        bash.get_by_role("button", name="1 rule").click()
        rule = panel.locator('[data-rules="Bash"] [data-rule]')
        expect(rule).to_have_count(1)
        expect(rule.locator(".re")).to_have_text("Bash(git push:*)")
        expect(rule.locator(".rl")).to_have_text("Disallowed tools")
        expect(rule.get_by_role("button", name="Remove Bash(git push:*)")).to_be_visible()
        expect(panel.locator('[data-rules="other"]')).to_have_count(0)
        for word in ("allow", "deny"):
            expect(bash.get_by_role("button", name=word, exact=True)).to_have_attribute("aria-pressed", "false")
        # Deny one tool: the word is boxed, and the row is changed and off the usual.
        search = view.locator('[data-tool="WebSearch"]')
        deny = search.get_by_role("button", name="deny", exact=True)
        deny.click()
        expect(deny).to_have_attribute("aria-pressed", "true")
        kit.assert_colour(deny, "text")
        kit.assert_colour(search.get_by_role("button", name="allow", exact=True), "text-dim")
        expect(changed_line(piece).locator("b")).to_have_text(["Disallowed tools"])
        expect(piece.locator(".opt")).to_have_text("Options · 1")
        headless(piece)
        brief = "say the word tools"
        piece.locator("textarea").fill(brief)
        body = press(ui_page, piece)
        assert body["values"]["disallowed_tools"] == ["Bash(git push:*)", "WebSearch"], body
        assert body.get("off_posture") is True and "tools" in body["leave_out"], body
        expect(piece.locator('.said [data-line="started"]')).to_be_visible()
        argv = argv_of(harness, brief)
        assert flagged(argv, "--disallowedTools") == ["Bash(git push:*)", "WebSearch"], argv
        assert "--tools" not in argv and "--allowedTools" not in argv, argv
    finally:
        file.unlink()


def test_a_set_of_built_in_tools_dims_the_lines_it_lacks_and_no_line_writes_it(fake, ui_page):
    piece = home_at(ui_page, 1400)
    panel = open_options(piece)
    view = tools_view(panel)
    tools = panel.locator('[data-row="tools"]')
    tools.locator('[data-control="tools"]').fill("Read")
    tools.get_by_role("button", name="Add").click()
    expect(tools.locator("li")).to_have_text(["ReadRemove"])
    expect(view.locator(".tool.dim")).to_have_count(8)
    edit = view.locator('[data-tool="Edit"]')
    expect(edit).to_contain_text("not in this launch's tools")
    kit.assert_colour(edit.locator(".nm"), "text-dim")
    expect(view.locator('[data-tool="Read"]')).not_to_contain_text("not in this launch's tools")
    # A press on a dim line writes the rule, and the set stays as it was.
    edit.get_by_role("button", name="allow", exact=True).click()
    expect(changed_line(piece).locator("b")).to_have_text(["Built-in tool set", "Allowed tools"])
    expect(tools.locator("li")).to_have_text(["ReadRemove"])
    # A rule no line draws is listed under Other rules, with Remove.
    find = panel.get_by_label("Find an option")
    find.fill("allowed tools")
    allowed = panel.locator('[data-row="allowed_tools"]')
    allowed.locator('[data-control="allowed_tools"]').fill("mcp__proj-a__search")
    allowed.get_by_role("button", name="Add").click()
    find.fill("")
    other = panel.locator('[data-rules="other"]')
    expect(other.locator(".oh")).to_have_text("Other rules")
    expect(other.locator("[data-rule] .re")).to_have_text(["mcp__proj-a__search"])
    other.get_by_role("button", name="Remove mcp__proj-a__search").click()
    expect(other).to_have_count(0)
    expect(edit.get_by_role("button", name="allow", exact=True)).to_have_attribute("aria-pressed", "true")
