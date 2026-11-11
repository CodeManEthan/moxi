"""`Run again` on Home in a browser, through a real shell that starts the launcher's real
page server: each recent is a launch made from Home itself. Pointing at a recent shows its
card, what a click runs and where, a value that can do harm in the warning colour, and what
the click does with the button's default; the click runs it again there, in the same folder,
with the same flags, or copies its command where the default is the user's terminal. A
recent that can't run again says why, and its click does nothing.

The shell runs the fake slots in slots' place and fake `claude` and `codex` first on its
`PATH`, so no launch starts a real session: a slot launch ends in the fake's `start`, and a
headless one runs the fake harness.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' picker is checked in a browser on Linux")]

PREVIEW = "/m/launcher/api/preview"
RECENT = "/m/launcher/api/recent?"
COPIED = "Copied. Paste it in your terminal."
#: When a launch was made, as the card says it: today and a time of day in the browser's locale.
TODAY = r"today \d{1,2}[:.]\d{2}.*"


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


@pytest.fixture
def fake(module_fake):
    """The shell's fake slots, with slots 2 and 3 free and a start placed in slot 2."""
    module_fake.script(placed=2, slots=kit.free(2, 3))
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


def recents(piece):
    """The recents under the box, each the button a click runs again."""
    return piece.locator("[data-recents] [data-recent] > button")


def card(piece):
    return piece.get_by_role("tooltip")


def point(page, piece, at: int = 0):
    """Point at the recent `at` and wait for its card, read with the button's default."""
    with page.expect_response(lambda answer: RECENT in answer.url):
        recents(piece).nth(at).hover()
    expect(card(piece)).to_be_visible()
    return card(piece)


def headless(piece) -> None:
    piece.locator(".more").click()
    piece.locator('[data-menu="run"] button[data-run="Headless"]').click()
    expect(piece.locator(".go")).to_have_text("Launch headless")


def launch(page, piece, brief: str) -> dict:
    """A headless launch of `brief`, followed until it starts; returns its request."""
    headless(piece)
    piece.locator("textarea").fill(brief)
    expect(piece.locator(".go")).to_be_enabled()
    with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    expect(piece.locator('.said [data-line="started"]')).to_be_visible()
    return json.loads(sent.value.post_data)


def run_again(page, piece, at: int = 0) -> dict:
    """Click the recent `at`; returns the body the piece previews."""
    with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        recents(piece).nth(at).click()
    return json.loads(sent.value.post_data)


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


def pick_project(piece, key: str) -> None:
    piece.locator(".where .face").click()
    piece.locator('[data-menu="where"] .places .row').filter(has_text=key).click()
    expect(piece.locator(".where .face")).to_have_text(f"{key}▾")


def started(fake, count: int) -> dict:
    """The fake's start once it has had `count` of them."""
    wait_for(lambda: len(fake.starts()) >= count, 10, what=f"start {count} in the fake slots")
    return fake.starts()[count - 1]


def test_a_launch_in_a_project_runs_again_in_the_same_folder_from_one_click(page, fake, shell):
    piece = kit.open_home(page)
    pick_project(piece, "proj-a")
    expect(piece.locator("[data-recents]")).to_have_count(0)
    root = str(Path(shell.env["HOME"]) / "work" / "proj-a")
    brief = "Read the design and start the build of the recents"
    body = launch(page, piece, brief)
    assert body["values"]["project"] == "proj-a" and body["values"]["target"] == "headless", body
    # The launch's event lists it under the box, by the first words of its brief and a time.
    expect(piece.locator("[data-recents] > .k")).to_have_text("Run again")
    expect(recents(piece)).to_have_count(1)
    expect(recents(piece).first.locator("b")).to_have_text("Read the design and start")
    expect(recents(piece).first.locator(".t")).to_have_text(re.compile(r"^\d{1,2}[:.]\d{2}"))
    kit.assert_colour(piece.locator("[data-recents] > .k"), "text-dim")
    kit.assert_colour(recents(piece).first.locator("b"), "text")
    # The card: the prompt whole, what runs, where and when it ran, and what a click does.
    shown = point(page, piece)
    expect(shown.locator("[data-prompt]")).to_have_text(brief)
    expect(shown.locator("[data-runs]")).to_have_text("claude · default model")
    expect(shown.locator("[data-ran]")).to_have_text("Ran headless")
    expect(shown).to_contain_text(re.compile(TODAY))
    expect(shown.locator("[data-place]")).to_have_count(0)
    expect(shown.locator("[data-posture]")).to_have_count(0)
    expect(shown.locator("[data-act]")).to_have_text("Click to run again in slot 2")
    expect(shown.locator("[data-beside]")).to_have_count(0)
    assert "text-decoration-line: underline" in recents(piece).first.locator("b").evaluate(
        "b => `text-decoration-line: ${getComputedStyle(b).textDecorationLine}`")
    # The click runs it again in the slot the card named, in the project's folder.
    piece.locator("textarea").fill("a draft that stays")
    before = len(fake.starts())
    body = run_again(page, piece)
    assert body["values"]["target"] == "slot" and body["values"]["slot"] == "2", body
    assert body["values"]["brief"] == brief and "filled" not in body, body
    expect(piece.locator('.said [data-line="started"]')).to_have_text(re.compile(r"^Started in slot 2 · "))
    start = started(fake, before + 1)
    assert start["slot"] == 2 and start["cwd"] == root, start
    expect(piece.locator("textarea")).to_have_value("a draft that stays")


def test_the_card_names_the_slot_it_ran_in_when_that_slot_is_in_use(page, fake):
    piece = kit.open_home(page)
    piece.locator("textarea").fill("say the word slot")
    piece.locator(".go").click()
    expect(piece.locator('.said [data-line="started"]')).to_have_text(re.compile(r"^Started in slot 2 · "))
    # The fake left slot 2 taken: the button's default is slot 3 now.
    expect(piece.locator(".go")).to_have_text("Launch in slot 3")
    shown = point(page, piece)
    expect(shown.locator("[data-ran]")).to_have_text("Ran in slot 2")
    expect(shown.locator("[data-act]")).to_have_text("Click to run again in slot 3")
    expect(shown.locator("[data-beside]")).to_have_text("slot 2 is in use")
    kit.assert_colour(shown.locator("[data-beside]"), "text-dim")
    kit.assert_colour(shown.locator("[data-act]"), "text")


def test_a_recent_with_a_value_that_can_do_harm_shows_it_in_the_warning_colour_and_runs_with_its_flag(
        page, fake):
    piece = kit.open_home(page)
    panel = open_options(piece)
    open_sub(panel, "access", "Mode")
    control(panel, "permission_mode").select_option("bypassPermissions")
    panel.locator('[data-ask="permission_mode"]').get_by_role("button", name="Turn on").click()
    panel.get_by_role("button", name="Close").click()
    body = launch(page, piece, "say the word harm again")
    assert body["off_posture"] is True, body
    # The value turned on is let go of once the launch started.
    expect(piece.locator('[data-line="changed"]')).to_have_count(0)
    shown = point(page, piece)
    word = shown.locator('[data-posture="off"] b')
    expect(shown.locator('[data-posture="off"]')).to_have_text("Off your defaultsPermission mode bypassPermissions")
    kit.assert_colour(word, "danger")
    kit.assert_colour(shown.locator('[data-posture="off"] .k'), "text-dim")
    expect(shown.locator("[data-act]")).to_have_text("Click to run again in slot 2")
    # One click runs it, with nothing asked, and the flag.
    before = len(fake.starts())
    body = run_again(page, piece)
    assert body["values"]["permission_mode"] == "bypassPermissions" and body["off_posture"] is True, body
    expect(piece.locator('.said [data-line="started"]')).to_have_text(re.compile(r"^Started in slot 2 · "))
    argv = started(fake, before + 1)["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions", argv
    expect(piece.locator(".opt")).to_have_text("Options")


def test_the_changed_line_sits_above_the_recents(page):
    piece = kit.open_home(page)
    launch(page, piece, "say the word order")
    expect(recents(piece)).to_have_count(1)
    panel = open_options(piece)
    open_sub(panel, "access", "Mode")
    control(panel, "permission_mode").select_option("plan")
    panel.get_by_role("button", name="Close").click()
    changed = piece.locator('[data-line="changed"]')
    expect(changed).to_be_visible()
    assert changed.bounding_box()["y"] < piece.locator("[data-recents]").bounding_box()["y"]


def test_where_the_default_is_the_terminal_a_click_copies_the_recents_command(context, fake, shell):
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    page = context.pages[0]
    fake.script(supported={"ok": False, "why": "tmux is not on PATH"})
    piece = kit.open_home(page)
    expect(piece.locator(".go")).to_have_text("Copy command")
    brief = "say the word copied"
    launch(page, piece, brief)
    shown = point(page, piece)
    expect(shown.locator("[data-act]")).to_have_text("Click to copy its command")
    before = len(fake.starts())
    body = run_again(page, piece)
    assert body["values"]["target"] == "terminal" and "slot" in body["leave_out"], body
    expect(piece.locator('.said [data-line="copied"]')).to_have_text(COPIED)
    command = page.evaluate("navigator.clipboard.readText()")
    assert command.startswith("moxi launcher run ") and brief in command, command
    assert len(fake.starts()) == before


def test_a_recent_that_cant_run_again_says_why_and_its_click_does_nothing(page, folder):
    piece = kit.open_home(page)
    launch(page, piece, "say the word gone")
    launch(page, piece, "say the word gone twice")
    shutil.rmtree(folder)
    piece = kit.open_home(page)
    expect(recents(piece)).to_have_count(2)
    shown = point(page, piece, 1)
    expect(shown.locator("[data-act]")).to_have_text(re.compile(r"^Can't run again: .+"))
    kit.assert_colour(shown.locator("[data-act]"), "text")
    expect(shown.locator("[data-prompt]")).to_have_count(0)
    expect(shown.locator("[data-runs]")).to_have_count(0)
    expect(shown).to_contain_text("say the word gone")
    expect(shown).to_contain_text(re.compile(TODAY))
    previews = []
    page.on("request", lambda request: previews.append(request) if request.url.endswith(PREVIEW) else None)
    recents(piece).nth(1).click()
    # The other recent's card is read after the click had its turn: nothing was previewed.
    point(page, piece, 0)
    expect(card(piece).locator("[data-act]")).to_have_text(re.compile(r"^Can't run again: "))
    assert previews == []
    expect(piece.locator(".said p")).to_have_count(0)
