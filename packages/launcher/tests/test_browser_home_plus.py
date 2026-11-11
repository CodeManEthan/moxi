"""The `+` on Home in a browser, through a real shell that starts the launcher's real page
server. `Continue the last session` offers only a session the shell's own user made in the
place the line names, and resumes it in a slot with the lines under the box while the box
keeps its text; a brief from a file turns the box off and launches with that file as the
first turn.

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


@pytest.fixture
def folder(shell):
    """Home opens on a folder of the test's own, which no launch has run in."""
    return kit.fresh_place(shell)


@pytest.fixture
def context(shell, fake, folder, ui_contexts):
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


def test_continue_the_last_session_is_not_offered_for_another_actors_session(page, shell, folder):
    other = "planner"
    assert other != kit.actor(shell)
    kit.seed(shell, "worker-in-folder", target="slot", cwd=str(folder), actor=other)
    piece = kit.open_home(page)
    plus = piece.get_by_role("button", name="More ways to start")
    expect(plus).to_have_text("+")
    kit.assert_colour(plus, "text-dim")
    menu = plus_menu(page, piece)
    expect(entry(menu, "Brief from a file")).to_be_visible()
    expect(entry(menu, "Continue the last session")).to_have_count(0)


def test_his_own_last_session_is_offered_and_resumes_in_a_slot_keeping_the_box(page, shell, fake, folder):
    sid = kit.seed(shell, "his-own-in-folder", target="slot", cwd=str(folder), actor=kit.actor(shell))
    # A newer session of another actor in the same folder is passed over.
    kit.seed(shell, "worker-after-him", target="slot", cwd=str(folder), actor="planner")
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
    assert start["slot"] == 3 and start["cwd"] == str(folder), start
    assert "--resume" in start["argv"] and sid in start["argv"], start["argv"]
    expect(piece.locator("textarea")).to_have_value("a draft that stays")
    expect(piece.locator(".go")).to_be_enabled()


def test_a_brief_from_a_file_turns_the_box_off_and_launches_with_that_file(page, folder):
    brief = folder / "first-turn.md"
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


def test_clear_takes_the_brief_file_off_and_the_box_is_back(page, folder):
    piece = kit.open_home(page)
    menu = plus_menu(page, piece)
    entry(menu, "Brief from a file").click()
    field = menu.get_by_role("textbox", name="Path to a file holding the brief")
    field.fill(str(folder / "notes.md"))
    field.press("Enter")
    expect(piece.locator("textarea")).to_be_disabled()
    piece.get_by_role("button", name="Clear the brief file").click()
    expect(piece.locator("[data-brief-file]")).to_have_count(0)
    expect(piece.locator("textarea")).to_be_enabled()
    expect(piece.locator("textarea")).to_be_focused()
