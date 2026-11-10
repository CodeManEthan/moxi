"""The Options panel of the box on Home in a browser, through a real shell that starts the
launcher's real page server: the word that opens it, the panel at the window's right edge
with Home's column moved aside, its groups and their subgroups, the find field, a row
changed there reaching the harness's command line, and the panel on a narrow page.

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
    # A list row is the plain list control.
    group(panel, "tools").click()
    panel.locator('button[data-sub="tools/Built-in"]').click()
    built_in = panel.locator('[data-body="tools"] [data-row]')
    assert built_in.evaluate_all("found => found.map((one) => one.dataset.row)") == [
        "tools", "allowed_tools", "disallowed_tools"]
    expect(panel.locator('[data-row="tools"]').get_by_role("button", name="Add")).to_be_visible()
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
