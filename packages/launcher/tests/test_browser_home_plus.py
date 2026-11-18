"""The `+` on Home in a browser, through a real shell that starts the launcher's real page
server. `Continue the last session` offers only a session the shell's own user made in the
place the line names, and resumes it in a slot with the lines under the box while the box
keeps its text; a brief from a file turns the box off and launches with that file as the
first turn; a handoff from a file, or one a preset names, is on its line under the box,
goes in the launch beside the box's text, and `Clear` takes it out until a preset is picked
again, when that preset's handoff is back on the line and in the launch.

The shell runs the fake slots in slots' place and fake `claude` and `codex` first on its
`PATH`, so no launch or resume starts a real session.
"""

from __future__ import annotations

import json
import re
import sys

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit
from moxi_launcher import presets

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="the fake harness is a script run by its #! line")]

PREVIEW = "/m/launcher/api/preview"
SESSIONS = "/m/launcher/api/sessions?limit=20"
RESUME = "/m/launcher/api/resume"


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
    module_fake.script(placed=3, slots=kit.free(3))
    return ui_shells(**module_fake.options())


@pytest.fixture
def fake(module_fake):
    """The shell's fake slots, with slot 3 free."""
    module_fake.script(placed=3, slots=kit.free(3))
    return module_fake


@pytest.fixture(name="work_dir")
def work_dir_fixture(shell):
    """Home opens on a folder of the test's own, which no launch has run in."""
    return kit.fresh_place(shell)


@pytest.fixture
def context(shell, fake, work_dir, ui_contexts):
    return ui_contexts(shell)


@pytest.fixture
def page(context):
    return context.pages[0]


def plus_menu(page, piece):
    """Open the `+` menu and wait for its read of the past sessions; returns the menu."""
    with page.expect_response(lambda answer: answer.url.endswith(SESSIONS)):
        piece.get_by_role("button", name="More ways to start").click()
    menu = piece.locator('[data-menu="plus"]')
    expect(menu).to_be_visible()
    return menu


def entry(menu, name: str):
    return menu.get_by_role("button", name=re.compile(f"^{name}"))


def started(fake, count: int) -> dict:
    """The fake's start once it has had `count` of them."""
    wait_for(lambda: len(fake.starts()) >= count, 10, what=f"start {count} in the fake slots")
    return fake.starts()[count - 1]


def test_continue_the_last_session_is_not_offered_for_another_actors_session(page, shell, work_dir):
    other = "planner"
    assert other != kit.actor(shell)
    kit.seed(shell, "worker-in-folder", target="slot", cwd=str(work_dir), by=other)
    piece = kit.open_home(page)
    plus = piece.get_by_role("button", name="More ways to start")
    expect(plus).to_have_text("+")
    kit.assert_colour(plus, "text-dim")
    menu = plus_menu(page, piece)
    expect(entry(menu, "Brief from a file")).to_be_visible()
    expect(entry(menu, "Continue the last session")).to_have_count(0)


def test_his_own_last_session_is_offered_and_resumes_in_a_slot_keeping_the_box(page, shell, fake, work_dir):
    sid = kit.seed(shell, "his-own-in-folder", target="slot", cwd=str(work_dir), by=kit.actor(shell))
    # A newer session of another actor in the same folder is passed over.
    kit.seed(shell, "worker-after-him", target="slot", cwd=str(work_dir), by="planner")
    piece = kit.open_home(page)
    piece.locator("textarea").fill("a draft that stays")
    menu = plus_menu(page, piece)
    found = entry(menu, "Continue the last session")
    expect(found).to_be_visible()
    expect(found.locator(".d")).to_have_text("resumes in a slot")
    kit.assert_colour(found.locator(".d"), "text-dim")
    before = len(fake.starts())
    with page.expect_request(lambda request: request.url.endswith(RESUME)) as sent:
        found.click()
    assert json.loads(sent.value.post_data) == {"session": sid}
    expect(menu).to_have_count(0)
    expect(piece.locator('.said [data-line="started"]')).to_have_text(re.compile(r"^Resumed in slot 3 · "),
                                                                    timeout=20_000)
    start = started(fake, before + 1)
    assert start["slot"] == 3 and start["cwd"] == str(work_dir), start
    assert "--resume" in start["argv"] and sid in start["argv"], start["argv"]
    expect(piece.locator("textarea")).to_have_value("a draft that stays")
    expect(piece.locator(".go")).to_be_enabled()


def test_a_brief_from_a_file_turns_the_box_off_and_launches_with_that_file(page, work_dir):
    brief = work_dir / "first-turn.md"
    brief.write_text("Read the notes and start.\n", encoding="utf-8")
    piece = kit.open_home(page)
    piece.locator(".more").click()
    piece.locator('[data-menu="run"] button[data-run="Headless"]').click()
    piece.locator("textarea").fill("text the file stands in for")
    menu = plus_menu(page, piece)
    entry(menu, "Brief from a file").click()
    field = menu.get_by_role("textbox", name="Path to a file holding the brief")
    field.fill(str(brief))
    field.press("Enter")
    expect(piece.locator("textarea")).to_be_disabled()
    line = piece.locator("[data-brief-file]")
    expect(line).to_contain_text(str(brief))
    with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    body = json.loads(sent.value.post_data)
    assert body["values"]["brief_file"] == str(brief), body
    assert "brief" not in body["values"] and "brief" in body["leave_out"], body
    assert "handoff" not in body["values"], body
    expect(piece.locator('.said [data-line="started"]')).to_have_text(re.compile(r"^Started headless · "))
    # The launch that started with the file lets go of it; the box is back with its text.
    expect(line).to_have_count(0)
    expect(piece.locator("textarea")).to_be_enabled()
    expect(piece.locator("textarea")).to_have_value("text the file stands in for")


def test_clear_takes_the_brief_file_off_and_the_box_is_back(page, work_dir):
    piece = kit.open_home(page)
    menu = plus_menu(page, piece)
    entry(menu, "Brief from a file").click()
    field = menu.get_by_role("textbox", name="Path to a file holding the brief")
    field.fill(str(work_dir / "notes.md"))
    field.press("Enter")
    expect(piece.locator("textarea")).to_be_disabled()
    piece.get_by_role("button", name="Clear the brief file").click()
    expect(piece.locator("[data-brief-file]")).to_have_count(0)
    expect(piece.locator("textarea")).to_be_enabled()
    expect(piece.locator("textarea")).to_be_focused()


def test_a_handoff_from_a_file_is_on_its_line_under_the_box_and_goes_in_the_launch(page, shell, work_dir):
    handoff = work_dir / "handoff.md"
    handoff.write_text("Where the last session stopped.\n", encoding="utf-8")
    piece = kit.open_home(page)
    piece.locator(".more").click()
    piece.locator('[data-menu="run"] button[data-run="Headless"]').click()
    piece.locator("textarea").fill("carry on from the handoff")
    menu = plus_menu(page, piece)
    found = entry(menu, "Handoff from a file")
    expect(found).to_have_attribute("data-current", "false")
    found.click()
    field = menu.get_by_role("textbox", name="Path to a handoff file")
    field.fill(str(handoff))
    field.press("Enter")
    expect(menu).to_have_count(0)
    # The box stays on, and the line names the file with home written ~.
    expect(piece.locator("textarea")).to_be_enabled()
    line = piece.locator("[data-handoff]")
    expect(line.locator(".k")).to_have_text("Handoff from a file")
    expect(line.locator(".file")).to_have_text(f"~/work/{work_dir.name}/handoff.md")
    expect(entry(plus_menu(page, piece), "Handoff from a file")).to_have_attribute("data-current", "true")
    page.keyboard.press("Escape")
    with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    body = json.loads(sent.value.post_data)
    assert body["values"]["handoff"] == str(handoff), body
    assert body["values"]["brief"] == "carry on from the handoff", body
    expect(piece.locator('.said [data-line="started"]')).to_have_text(re.compile(r"^Started headless · "))
    row = kit.newest_launch(shell)
    assert (row.brief, row.handoff_path) == ("carry on from the handoff", str(handoff)), row
    # The launch that started with it lets go of it.
    expect(line).to_have_count(0)


def test_a_presets_handoff_shows_as_its_line_and_clear_takes_it_out_of_the_launch(page, shell, work_dir):
    handoff = work_dir / "seat-handoff.md"
    handoff.write_text("The seat's own notes.\n", encoding="utf-8")
    file = presets.path(env=shell.env)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f'[seat.relief]\nharness = "claude"\nhandoff = "{handoff}"\n')
    try:
        piece = kit.open_home(page)
        line = piece.locator("[data-handoff]")
        expect(line).to_have_count(0)
        piece.locator('.where [data-item="preset"] > button').click()
        piece.locator('[data-menu="preset"] button[data-preset="seat:relief"]').click()
        expect(line.locator(".file")).to_have_text(f"~/work/{work_dir.name}/seat-handoff.md")
        piece.locator(".go:not([disabled])").wait_for()
        line.get_by_role("button", name="Clear the handoff").click()
        expect(line).to_have_count(0)
        expect(piece.locator("textarea")).to_be_focused()
        piece.locator("textarea").fill("start fresh")
        with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
            piece.locator(".go").click()
        body = json.loads(sent.value.post_data)
        assert body["seat"] == "relief", body
        assert "handoff" not in body["values"] and "handoff" in body["leave_out"], body
        expect(piece.locator('.said [data-line="started"]')).to_have_text(re.compile(r"^Started in slot 3 · "))
        row = kit.newest_launch(shell)
        assert row.handoff_path is None, row
    finally:
        file.unlink()


def pick_seat(piece, name: str):
    piece.locator('.where [data-item="preset"] > button').click()
    piece.locator(f'[data-menu="preset"] button[data-preset="seat:{name}"]').click()


def launch(page, piece, shell, brief: str) -> tuple[dict, object]:
    """Presses the button with `brief` in the box; returns the request it sent and the launch
    row it started."""
    piece.locator(".go:not([disabled])").wait_for()
    piece.locator("textarea").fill(brief)
    with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    expect(piece.locator('.said [data-line="started"]')).to_have_text(re.compile(r"^Started "))
    wait_for(lambda: kit.newest_launch(shell).brief == brief, 10, what=f"the launch row of {brief!r}")
    return json.loads(sent.value.post_data), kit.newest_launch(shell)


def test_a_clear_covers_one_launch_and_the_next_launch_or_preset_shows_and_sends_its_handoff(
        page, shell, work_dir):
    alpha, beta, mine = (work_dir / f"{name}-handoff.md" for name in ("alpha", "beta", "mine"))
    for path in (alpha, beta, mine):
        path.write_text(f"{path.name}\n", encoding="utf-8")
    file = presets.path(env=shell.env)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f'[seat.alpha]\nharness = "claude"\nhandoff = "{alpha}"\n'
                    f'[seat.beta]\nharness = "claude"\nhandoff = "{beta}"\n')
    try:
        piece = kit.open_home(page)
        line = piece.locator("[data-handoff]")
        pick_seat(piece, "alpha")
        expect(line.locator(".file")).to_have_text(f"~/work/{work_dir.name}/alpha-handoff.md")
        piece.locator(".go:not([disabled])").wait_for()
        line.get_by_role("button", name="Clear the handoff").click()
        expect(line).to_have_count(0)
        # Cleared, then launched: that launch sends none.
        body, row = launch(page, piece, shell, "first")
        assert body["seat"] == "alpha", body
        assert "handoff" not in body["values"] and "handoff" in body["leave_out"], body
        assert row.handoff_path is None, row
        # The Clear went with that launch: alpha's handoff shows again and the next launch sends it.
        expect(line.locator(".file")).to_have_text(f"~/work/{work_dir.name}/alpha-handoff.md")
        body, row = launch(page, piece, shell, "second")
        assert (body["seat"], body["values"]["handoff"]) == ("alpha", str(alpha)), body
        assert row.handoff_path == str(alpha), row
        # Cleared, then another preset picked: its handoff shows and is sent.
        line.get_by_role("button", name="Clear the handoff").click()
        expect(line).to_have_count(0)
        pick_seat(piece, "beta")
        expect(line.locator(".file")).to_have_text(f"~/work/{work_dir.name}/beta-handoff.md")
        body, row = launch(page, piece, shell, "third")
        assert (body["seat"], body["values"]["handoff"]) == ("beta", str(beta)), body
        assert row.handoff_path == str(beta), row
        # The same preset picked again brings its handoff back after a Clear.
        line.get_by_role("button", name="Clear the handoff").click()
        expect(line).to_have_count(0)
        pick_seat(piece, "beta")
        expect(line.locator(".file")).to_have_text(f"~/work/{work_dir.name}/beta-handoff.md")
        # One set through the + stays over a preset change, as a brief file does.
        menu = plus_menu(page, piece)
        entry(menu, "Handoff from a file").click()
        field = menu.get_by_role("textbox", name="Path to a handoff file")
        field.fill(str(mine))
        field.press("Enter")
        expect(line.locator(".file")).to_have_text(f"~/work/{work_dir.name}/mine-handoff.md")
        pick_seat(piece, "alpha")
        expect(piece.locator('.where [data-item="preset"] > button')).to_contain_text("alpha")
        expect(line.locator(".file")).to_have_text(f"~/work/{work_dir.name}/mine-handoff.md")
        # It goes with the next launch and is then let go, so the preset's shows again.
        body, row = launch(page, piece, shell, "fourth")
        assert (body["seat"], body["values"]["handoff"]) == ("alpha", str(mine)), body
        assert row.handoff_path == str(mine), row
        expect(line.locator(".file")).to_have_text(f"~/work/{work_dir.name}/alpha-handoff.md")
    finally:
        file.unlink()
