"""The box on Home in a browser, through a real shell that starts the launcher's real page
server: the chip that says which harness and model run the session, the split button that
says where it runs, its menu, and a launch from the press to each of its ends, with the
line each end leaves under the box; then the piece's colours in light and dark, and the
piece on a narrow page.

The shell runs the fake slots in slots' place and a fake `claude` first on its `PATH`, so
a launch from Home starts no real session: a slot launch ends in the fake's `start`, a
headless one runs the fake harness, and a terminal launch is only ever copied. Each test
writes the fake's script before it opens Home. A test that holds a launch in the fake's
`start` waits for that start to end before it returns, since the start saves the script
it read and would write over the next test's.
"""

from __future__ import annotations

import json
import re
import sys

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit
from moxi_launcher import presets, store

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(sys.platform == "win32",
                                 reason="the fake harness is a script run by its #! line")]

BRIEF = "say the word ready"
PREVIEW = "/m/launcher/api/preview"
LAUNCH = "/m/launcher/api/launch"
LATE = "Not started yet, and it should have been by now. The Launcher page lists it."
STOPPED = "Stopped watching this launch. The Launcher page lists it."
COPIED = "Copied. Paste it in your terminal."
#: A time of day as a browser's locale writes it.
HOUR = r"\d{1,2}[:.]\d{2}.*"


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


def said(piece, line: str):
    """The line `line` under the box."""
    return piece.locator(f'.said [data-line="{line}"]')


def open_run(piece):
    """The button's menu of where the launch runs, opened."""
    piece.locator(".more").click()
    menu = piece.locator('[data-menu="run"]')
    expect(menu).to_be_visible()
    return menu


def pick_run(piece, lead: str) -> None:
    open_run(piece).locator(f'button[data-run="{lead}"]').click()
    expect(piece.locator('[data-menu="run"]')).to_have_count(0)


def press(page, piece) -> dict:
    """Press the button, and return the body the piece sends to be previewed."""
    with page.expect_request(lambda request: request.url.endswith(PREVIEW)) as sent:
        piece.locator(".go").click()
    return json.loads(sent.value.post_data)


def placed(fake, number: int) -> bool:
    """Whether the fake's `start` has placed a session in slot `number`."""
    script = json.loads((fake.dir / "script.json").read_text())
    return script.get("slots", {}).get(str(number), {}).get("state") == "occupied"


# --- the chip -------------------------------------------------------------------------------

def test_the_chip_says_the_harness_the_model_and_the_effort_as_its_menu_sets_them(
        fake, ui_page, ui_shell):
    piece = kit.open_home(ui_page)
    chip = piece.locator(".chip")
    expect(chip).to_have_text(re.compile(r"^claude · default model\s*▾$"))
    expect(chip).to_have_css("font-family", re.compile("mono", re.I))
    chip.click()
    menu = piece.locator('[data-menu="chip"]')
    lists = menu.locator("[data-list]")
    assert lists.evaluate_all("lists => lists.map((list) => list.dataset.list)") == [
        "harness", "model", "effort"]
    # The three lists stand side by side.
    tops = [found.bounding_box()["y"] for found in lists.all()]
    lefts = [found.bounding_box()["x"] for found in lists.all()]
    assert len(set(tops)) == 1 and lefts == sorted(set(lefts)), (tops, lefts)

    def rows(name: str) -> list[str]:
        return menu.locator(f'[data-list="{name}"] .row .name').all_inner_texts()

    assert rows("harness") == ["Claude Code", "Codex"]
    assert rows("model") == ["Default", "fable", "opus", "sonnet", "older…"]
    assert rows("effort") == ["Default", "low", "medium", "high", "xhigh", "max"]
    current = menu.locator('.row[data-current="true"] .name')
    assert current.all_inner_texts() == ["Claude Code", "Default", "Default"]

    def pick(name: str, label: str) -> None:
        menu.locator(f'[data-list="{name}"] .row', has_text=re.compile(f"^\\W*{label}$")).click()

    pick("model", "sonnet")
    expect(chip).to_have_text(re.compile(r"^claude · sonnet\s*▾$"))
    pick("effort", "medium")
    expect(chip).to_have_text(re.compile(r"^claude · sonnet · medium\s*▾$"))
    pick("model", "Default")
    expect(chip).to_have_text(re.compile(r"^claude · default model · medium\s*▾$"))
    # The menu stays open through the picks; the launch carries them.
    expect(menu).to_be_visible()
    pick("model", "opus")
    ui_page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    body = press(ui_page, piece)
    assert (body["values"]["model"], body["values"]["effort"]) == ("opus", "medium"), body
    expect(said(piece, "started")).to_be_visible()
    row = kit.newest_launch(ui_shell)
    assert "--model" in row.argv and "opus" in row.argv and "medium" in row.argv, row.argv


def test_a_typed_model_name_and_another_harness_show_on_the_chip(fake, ui_page):
    piece = kit.open_home(ui_page)
    chip = piece.locator(".chip")
    chip.click()
    menu = piece.locator('[data-menu="chip"]')
    menu.locator('[data-list="model"] .row', has_text="older…").click()
    field = menu.get_by_label("A model's full name")
    expect(field).to_be_focused()
    field.fill("claude-test-model-1")
    field.press("Enter")
    expect(chip).to_have_text(re.compile(r"^claude · claude-test-model-1\s*▾$"))
    # Codex has no effort and offers no model names: `Default`, and the field.
    menu.locator('[data-list="harness"] .row', has_text="Codex").click()
    expect(chip).to_have_text(re.compile(r"^codex · default model\s*▾$"))
    expect(menu.locator('[data-list="effort"]')).to_have_count(0)
    assert menu.locator('[data-list="model"] .row .name').all_inner_texts() == ["Default", "older…"]
    expect(menu.locator('[data-list="harness"] .row[data-current="true"] .name')).to_have_text("Codex")
    ui_page.keyboard.press("Escape")
    body = press(ui_page, piece)
    assert body["values"]["harness"] == "codex" and "effort" not in body["values"], body
    expect(said(piece, "started")).to_be_visible()


# --- the button's words and its menu --------------------------------------------------------

@pytest.mark.parametrize(("script", "words", "target"), [
    ({"slots": kit.free(2, 3)}, "Launch in slot 2", "slot"),
    ({"slots": {"1": {"state": "occupied", "launchable": False}, **kit.free(4)}},
     "Launch in slot 4", "slot"),
    ({"slots": {"1": {"state": "occupied", "launchable": False}}}, "Launch in a new slot", "slot"),
    ({"slots": {}}, "Launch in a new slot", "slot"),
    ({"supported": {"ok": False, "why": "tmux is not on PATH"}}, "Copy command", "terminal"),
    ({"list": "error", "slots": kit.free(2)}, "Copy command", "terminal"),
], ids=["lowest-free", "first-free-after-a-taken", "none-free", "no-slot-yet", "no-slots",
        "unreadable"])
def test_the_button_says_where_the_default_launch_runs(fake, ui_page, script, words, target):
    fake.script(**script)
    piece = kit.open_home(ui_page)
    go = piece.locator(".go")
    expect(go).to_have_text(words)
    expect(go).to_have_attribute("data-target", target)
    expect(piece.get_by_role("button", name="Where it runs")).to_have_attribute(
        "aria-expanded", "false")


def test_the_menu_lists_each_slot_with_its_state_and_only_a_free_one_can_be_picked(
        fake, ui_page, ui_shell):
    # The launch in slot 1 is another user's, so it is no place of this shell's user.
    with kit.launch_log(ui_shell) as conn:
        store.insert(conn, request_id="taken-by-planner", actor="alice",
                     harness="codex", target="slot", slot="1", cwd="/work/one", values={},
                     argv=["codex"], env={}, posture="operator", identity="planner")
    fake.script(placed=2, slots={
        "1": {"state": "occupied", "launchable": False, "label": "launcher:taken-by-planner",
              "meta": {"request_id": "taken-by-planner"}},
        "2": {"state": "free", "launchable": True},
        "3": {"state": "ended", "launchable": True},
        "4": {"state": "occupied", "launchable": False},
        "5": {"state": "free", "launchable": False,
              "hold": {"holder": "reviewer", "until": 1900000000.0}},
        "6": {"state": "missing", "launchable": False}})
    piece = kit.open_home(ui_page)
    more = piece.get_by_role("button", name="Where it runs")
    menu = open_run(piece)
    expect(more).to_have_attribute("aria-expanded", "true")
    rows = menu.locator(".row")
    assert rows.evaluate_all("rows => rows.map((row) => row.dataset.run)") == [
        "Slot 1", "Slot 2", "Slot 3", "Slot 4", "Slot 5", "Slot 6", "Your terminal", "Headless"]

    def words(lead: str):
        return menu.locator(f'[data-run="{lead}"] .d')

    expect(words("Slot 1")).to_have_text("taken · planner")
    expect(words("Slot 2")).to_have_text("free")
    expect(words("Slot 3")).to_have_text("free")
    expect(words("Slot 4")).to_have_text("taken")
    expect(words("Slot 5")).to_have_text(re.compile(f"^held for reviewer until {HOUR}$"))
    expect(words("Slot 6")).to_have_text("not running")
    expect(words("Your terminal")).to_have_text("copies the command for you to paste")
    expect(words("Headless")).to_have_text("no terminal, runs in the background")
    for lead in ("Slot 1", "Slot 4", "Slot 5", "Slot 6"):
        assert menu.locator(f'[data-run="{lead}"]').evaluate("row => row.localName") == "div"
    for lead in ("Slot 2", "Slot 3", "Your terminal", "Headless"):
        expect(menu.locator(f'button[data-run="{lead}"]')).to_be_enabled()
    # The tick is on the default, and a hairline parts the slots from the rest.
    current = menu.locator('.row[data-current="true"]')
    expect(current).to_have_count(1)
    expect(current).to_have_attribute("data-run", "Slot 2")
    expect(current).to_contain_text("✓")
    expect(menu.locator("hr")).to_have_count(1)
    line = menu.locator("hr").bounding_box()["y"]
    assert (menu.locator('[data-run="Slot 6"]').bounding_box()["y"] < line
            < menu.locator('[data-run="Your terminal"]').bounding_box()["y"])
    # A new slot is not offered while a slot is free: a launch that names none takes it.
    expect(menu.locator('[data-run="A new slot"]')).to_have_count(0)
    ui_page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    expect(more).to_be_focused()


def test_a_new_slot_is_offered_and_ticked_when_no_slot_is_free(fake, ui_page):
    fake.script(placed=5, slots={"1": {"state": "occupied", "launchable": False}})
    piece = kit.open_home(ui_page)
    menu = open_run(piece)
    new = menu.locator('button[data-run="A new slot"]')
    expect(new).to_have_attribute("data-current", "true")
    assert menu.locator(".row").evaluate_all("rows => rows.map((row) => row.dataset.run)") == [
        "Slot 1", "A new slot", "Your terminal", "Headless"]


def test_a_picked_slot_is_named_by_the_button_and_the_launch_and_is_gone_at_the_next_load(
        fake, ui_page):
    piece = kit.open_home(ui_page)
    go = piece.locator(".go")
    expect(go).to_have_text("Launch in slot 2")
    pick_run(piece, "Slot 3")
    expect(go).to_have_text("Launch in slot 3")
    expect(go).to_be_focused()
    expect(open_run(piece).locator('.row[data-current="true"]')).to_have_attribute(
        "data-run", "Slot 3")
    ui_page.keyboard.press("Escape")
    body = press(ui_page, piece)
    assert (body["values"]["target"], body["values"]["slot"]) == ("slot", "3"), body
    expect(said(piece, "started")).to_have_text(re.compile(f"^Started in slot 3 · {HOUR}$"))
    assert fake.starts()[-1]["slot"] == 3, fake.starts()[-1]
    # The choice is not kept: the next load starts at the default again.
    fake.script(placed=2, slots=kit.free(2, 3))
    piece = kit.open_home(ui_page)
    expect(piece.locator(".go")).to_have_text("Launch in slot 2")


def test_headless_is_a_choice_and_never_where_a_load_starts(fake, ui_page, ui_shell, harness):
    piece = kit.open_home(ui_page)
    pick_run(piece, "Headless")
    go = piece.locator(".go")
    expect(go).to_have_text("Launch headless")
    expect(go).to_have_attribute("data-target", "headless")
    # With nothing to do, a session in the background is refused, in the launcher's words.
    go.click()
    refused = said(piece, "refused")
    expect(refused).to_have_text(re.compile(r"\w.*\. \w"))
    expect(go).to_be_enabled()
    expect(go).to_have_text("Launch headless")
    before = len(fake.starts())
    piece.locator("textarea").fill(BRIEF)
    expect(refused).to_have_count(0)
    body = press(ui_page, piece)
    assert body["values"]["target"] == "headless" and "slot" not in body["values"], body
    expect(said(piece, "started")).to_have_text(re.compile(f"^Started headless · {HOUR}$"))
    expect(piece.locator("textarea")).to_have_value("")
    row = kit.newest_launch(ui_shell)
    assert (row.target, row.slot) == ("headless", None), row
    [run] = wait_for(lambda: [run for run in harness.runs() if BRIEF in " ".join(run["argv"])],
                     10, what="the fake harness's run with the brief")
    assert run["argv"][1] == "-p", run["argv"]
    assert len(fake.starts()) == before, "a headless launch asks slots for nothing"
    kit.open_home(ui_page)
    expect(kit.home_prompt(ui_page).locator(".go")).to_have_text("Launch in slot 2")


# --- a launch, and what is under the box ----------------------------------------------------

def test_a_launch_that_starts_says_so_empties_the_box_and_gives_the_button_back(
        fake, ui_page, ui_shell):
    piece = kit.open_home(ui_page)
    box = piece.locator("textarea")
    box.fill(BRIEF)
    body = press(ui_page, piece)
    # The default launch names its target and no slot: the launcher picks at the start.
    assert body["values"]["target"] == "slot" and "slot" not in body["values"], body
    assert body["values"]["brief"] == BRIEF and "off_posture" not in body, body
    expect(said(piece, "started")).to_have_text(re.compile(f"^Started in slot 2 · {HOUR}$"))
    expect(box).to_have_value("")
    go = piece.locator(".go")
    expect(go).to_be_enabled()
    expect(piece.locator(".said p")).to_have_count(1)
    row = kit.newest_launch(ui_shell)
    assert (row.target, row.slot, row.brief) == ("slot", "2", BRIEF), row
    assert fake.starts()[-1]["slot"] is None, fake.starts()[-1]
    # The launch wrote the log, so where a launch may run is read again: slot 2 is taken
    # by this launch, and the button moves to the next free slot with no reload.
    expect(go).to_have_text("Launch in slot 3")
    expect(open_run(piece).locator('[data-run="Slot 2"] .d')).to_have_text(
        re.compile(r"^taken · \S"))
    ui_page.keyboard.press("Escape")
    # The line stays until the box is edited.
    box.press_sequentially("a")
    expect(piece.locator(".said p")).to_have_count(0)


def test_an_empty_box_starts_a_session_with_no_first_turn(fake, ui_page, ui_shell):
    piece = kit.open_home(ui_page)
    expect(piece.locator("textarea")).to_have_value("")
    body = press(ui_page, piece)
    assert "brief" not in body["values"] and "brief" in body["leave_out"], body
    expect(said(piece, "started")).to_have_text(re.compile(r"^Started in slot 2 · "))
    assert kit.newest_launch(ui_shell).brief is None


#: A brief the user's defaults set, which the launch form does not show.
DEFAULT_BRIEF = "tidy every folder"


@pytest.fixture
def default_brief(ui_shell):
    """The shell's presets file with the user's defaults setting a brief; removed when the
    test ends."""
    file = presets.path(env=ui_shell.env)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f'[defaults]\nbrief = "{DEFAULT_BRIEF}"\n')
    yield
    file.unlink()


def test_an_empty_box_sends_no_brief_though_the_defaults_set_one(
        fake, ui_page, ui_shell, harness, default_brief):
    piece = kit.open_home(ui_page)
    box = piece.locator("textarea")
    expect(box).to_have_value("")
    # In the background: with no brief there is nothing to run, and the launcher says so.
    pick_run(piece, "Headless")
    body = press(ui_page, piece)
    assert "brief" not in body["values"] and "brief" in body["leave_out"], body
    expect(said(piece, "refused")).to_contain_text("needs a brief")
    expect(said(piece, "started")).to_have_count(0)
    # In a slot: the session starts with no first turn.
    pick_run(piece, "Slot 2")
    piece.locator(".go").click()
    expect(said(piece, "started")).to_have_text(re.compile(r"^Started in slot 2 · "))
    row = kit.newest_launch(ui_shell)
    assert row.brief is None and DEFAULT_BRIEF not in " ".join(row.argv), row
    assert not [run for run in harness.runs() if DEFAULT_BRIEF in " ".join(run["argv"])]


def test_the_text_in_the_box_is_the_brief_sent_whatever_the_defaults_set(
        fake, ui_page, ui_shell, harness, default_brief):
    typed = "count the open doors"
    piece = kit.open_home(ui_page)
    piece.locator("textarea").fill(typed)
    pick_run(piece, "Headless")
    body = press(ui_page, piece)
    assert body["values"]["brief"] == typed, body
    expect(said(piece, "started")).to_have_text(re.compile(r"^Started headless · "))
    [run] = wait_for(lambda: [run for run in harness.runs() if typed in " ".join(run["argv"])],
                     10, what="the fake harness's run with the typed brief")
    assert DEFAULT_BRIEF not in " ".join(run["argv"]), run["argv"]
    assert kit.newest_launch(ui_shell).brief == typed


def test_a_launch_that_fails_says_why_keeps_the_box_and_gives_the_button_back(
        fake, ui_page, ui_shell):
    fake.script(placed=2, slots=kit.free(2, 3), start="refused",
                refused={"reason": "occupied", "words": "slot 2 was taken a moment ago"})
    piece = kit.open_home(ui_page)
    box = piece.locator("textarea")
    box.fill(BRIEF)
    go = piece.locator(".go")
    go.click()
    expect(said(piece, "failed")).to_have_text("The launch failed: occupied")
    expect(box).to_have_value(BRIEF)
    expect(go).to_be_enabled()
    expect(go).to_have_text("Launch in slot 2")
    row = kit.newest_launch(ui_shell)
    assert (row.state, row.reason) == ("failed", "occupied"), row
    # The next press takes the line away.
    fake.script(placed=2, slots=kit.free(2, 3))
    go.click()
    expect(said(piece, "started")).to_be_visible()
    expect(said(piece, "failed")).to_have_count(0)


def test_while_a_launch_is_under_way_the_button_is_off_and_a_second_click_sends_nothing(
        fake, ui_page):
    piece = kit.open_home(ui_page)
    piece.locator("textarea").fill(BRIEF)
    held, asked = [], []
    ui_page.route(f"**{PREVIEW}", lambda route: (asked.append(route.request.url), held.append(route)))
    go = piece.locator(".go")
    before = len(fake.starts())
    go.click()
    expect(go).to_be_disabled()
    expect(go).to_have_text("Starting…")
    expect(piece.get_by_role("button", name="Where it runs")).to_be_disabled()
    expect(piece.locator(".said p")).to_have_count(0)
    go.click(force=True)
    go.evaluate("button => button.click()")
    wait_for(lambda: ui_page.wait_for_timeout(20) or len(held) == 1, 5, what="the held preview")
    held[0].continue_()
    expect(said(piece, "started")).to_be_visible()
    expect(go).to_be_enabled()
    assert len(asked) == 1, asked
    assert len(fake.starts()) == before + 1, fake.starts()


def test_a_ticket_used_already_shows_the_answers_words_and_keeps_the_box(fake, ui_page):
    piece = kit.open_home(ui_page)
    box = piece.locator("textarea")
    box.fill(BRIEF)
    answers: list[int] = []

    def twice(route) -> None:
        """Send the launch, then send it again and give the page the second answer."""
        answers.append(route.fetch().status)
        again = route.fetch()
        answers.append(again.status)
        route.fulfill(response=again)

    ui_page.route(f"**{LAUNCH}", twice)
    go = piece.locator(".go")
    go.click()
    used = said(piece, "used")
    expect(used).to_be_visible()
    assert answers == [202, 409], answers
    expect(used).not_to_be_empty()
    expect(used).not_to_contain_text("The launch failed")
    expect(box).to_have_value(BRIEF)
    expect(go).to_be_enabled()
    expect(go).to_have_text(re.compile("^Launch in slot"))


def test_a_launch_followed_to_the_end_of_its_watch_says_it_stopped_watching(fake, ui_page):
    fake.script(placed=2, slots=kit.free(2, 3), start_wait_s=3)
    ui_page.clock.install()
    piece = kit.open_home(ui_page)
    box = piece.locator("textarea")
    box.fill(BRIEF)
    go = piece.locator(".go")
    go.click()
    expect(said(piece, "starting")).to_have_text("Starting in slot 2…")
    expect(go).to_be_disabled()
    expect(go).to_have_text("Starting…")
    # Eleven minutes pass on the page's clock, and the row is still not started.
    ui_page.clock.fast_forward("11:01")
    expect(said(piece, "stopped")).to_have_text(STOPPED)
    expect(said(piece, "starting")).to_have_count(0)
    expect(box).to_have_value(BRIEF)
    expect(go).to_be_enabled()
    expect(go).to_have_text("Launch in slot 2")
    wait_for(lambda: placed(fake, 2), 15, what="the held start to end")
    # The watch is over: the row starting later changes nothing on Home.
    ui_page.clock.fast_forward("00:10")
    expect(said(piece, "stopped")).to_have_text(STOPPED)
    expect(said(piece, "started")).to_have_count(0)


# --- the terminal ---------------------------------------------------------------------------

def test_your_terminal_copies_the_command_and_starts_nothing(fake, ui_context, ui_shell):
    ui_context.grant_permissions(["clipboard-read", "clipboard-write"])
    page = ui_context.pages[0]
    piece = kit.open_home(page)
    box = piece.locator("textarea")
    box.fill(BRIEF)
    pick_run(piece, "Your terminal")
    go = piece.locator(".go")
    expect(go).to_have_text("Copy command")
    before, rows = len(fake.starts()), _count(ui_shell)
    go.click()
    expect(said(piece, "copied")).to_have_text(COPIED)
    expect(box).to_have_value(BRIEF)
    expect(go).to_be_enabled()
    expect(go).to_have_text("Copy command")
    command = page.evaluate("navigator.clipboard.readText()")
    assert command.startswith("moxi launcher run ") and BRIEF in command, command
    # The page ran nothing: no start, and no row until the user runs the command.
    assert (len(fake.starts()), _count(ui_shell)) == (before, rows)


def _count(shell) -> int:
    with kit.launch_log(shell) as conn:
        return len(store.log(conn))


def test_a_browser_that_refuses_the_clipboard_shows_the_command_to_copy_by_hand(
        fake, ui_page):
    ui_page.add_init_script(
        "navigator.clipboard.writeText = () => Promise.reject(new DOMException('no', 'NotAllowedError'))")
    piece = kit.open_home(ui_page)
    piece.locator("textarea").fill(BRIEF)
    pick_run(piece, "Your terminal")
    go = piece.locator(".go")
    go.click()
    expect(said(piece, "not-copied")).to_contain_text("The browser did not let this page copy")
    command = piece.locator('.said code[data-line="command"]')
    expect(command).to_have_text(re.compile(r"^moxi launcher run .*say the word ready"))
    expect(said(piece, "copied")).to_have_count(0)
    expect(go).to_be_enabled()
    expect(piece.locator("textarea")).to_have_value(BRIEF)


# --- when where a launch may run is read ----------------------------------------------------

def test_the_button_follows_the_slots_when_the_tab_is_shown_again_and_when_the_menu_opens(
        fake, ui_page):
    piece = kit.open_home(ui_page)
    go = piece.locator(".go")
    expect(go).to_have_text("Launch in slot 2")
    fake.script(placed=3, slots={"2": {"state": "occupied", "launchable": False}, **kit.free(3)})
    ui_page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(go).to_have_text("Launch in slot 3")
    fake.script(placed=6, slots=kit.free(6))
    menu = open_run(piece)
    expect(menu.locator('[data-run="Slot 6"]')).to_have_attribute("data-current", "true")
    expect(go).to_have_text("Launch in slot 6")


def test_a_press_on_an_old_reading_reads_again_and_names_the_slot_the_launch_takes(
        ui_shells, ui_contexts, tmp_path_factory, on_path):
    # A shell of its own: a launch another test left under way would write the log, and
    # the page would read the slots again for that, not for the press.
    fake = kit.FakeSlots(tmp_path_factory.mktemp("fake-slots"))
    fake.script(placed=2, slots=kit.free(2, 3))
    ui_shell = ui_shells(**fake.options())
    ui_page = ui_contexts(ui_shell).pages[0]
    ui_page.clock.install()
    piece = kit.open_home(ui_page)
    go = piece.locator(".go")
    expect(go).to_have_text("Launch in slot 2")
    # Slot 2 is taken behind the page's back, and its reading grows old.
    fake.script(placed=3, start_wait_s=2,
                slots={"2": {"state": "occupied", "launchable": False}, **kit.free(3)})
    ui_page.clock.fast_forward("00:06")
    expect(go).to_have_text("Launch in slot 2")
    body = press(ui_page, piece)
    assert body["values"]["target"] == "slot" and "slot" not in body["values"], body
    expect(said(piece, "starting")).to_have_text("Starting in slot 3…")
    wait_for(lambda: placed(fake, 3), 15, what="the held start to end")
    ui_page.clock.fast_forward("00:03")
    expect(said(piece, "started")).to_have_text(re.compile(r"^Started in slot 3 · "))
    assert kit.newest_launch(ui_shell).slot == "3"


TARGETS = "/m/launcher/api/targets"


def test_a_reading_is_as_old_as_its_asking_so_a_press_after_a_slow_answer_reads_again(
        ui_shells, ui_contexts, tmp_path_factory, on_path):
    fake = kit.FakeSlots(tmp_path_factory.mktemp("fake-slots"))
    fake.script(placed=2, slots=kit.free(2, 3, 4))
    ui_shell = ui_shells(**fake.options())
    ui_page = ui_contexts(ui_shell).pages[0]
    ui_page.clock.install()
    piece = kit.open_home(ui_page)
    go = piece.locator(".go")
    expect(go).to_have_text("Launch in slot 2")
    taken = {"state": "occupied", "launchable": False}
    fake.script(placed=3, slots={"2": taken, **kit.free(3, 4)})
    held = []

    def hold_first(route) -> None:
        """Keep the first answer back; let every later read through."""
        if held:
            route.continue_()
        else:
            held.append((route, route.fetch()))

    ui_page.route(f"**{TARGETS}", hold_first)
    ui_page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    wait_for(lambda: ui_page.wait_for_timeout(20) or len(held) == 1, 5, what="the held read")
    # Slot 3 is taken while the answer is on its way, and the answer is slow.
    fake.script(placed=4, start_wait_s=2, slots={"2": taken, "3": taken, **kit.free(4)})
    ui_page.clock.fast_forward("00:06")
    route, answer = held[0]
    route.fulfill(response=answer)
    expect(go).to_have_text("Launch in slot 3")
    # The answer has only just landed, but it was asked over five seconds ago.
    body = press(ui_page, piece)
    assert body["values"]["target"] == "slot" and "slot" not in body["values"], body
    expect(said(piece, "starting")).to_have_text("Starting in slot 4…")
    wait_for(lambda: placed(fake, 4), 15, what="the held start to end")
    ui_page.clock.fast_forward("00:03")
    expect(said(piece, "started")).to_have_text(re.compile(r"^Started in slot 4 · "))


def test_a_read_that_failed_is_not_a_fresh_reading_so_the_next_press_reads_again(
        ui_shells, ui_contexts, tmp_path_factory, on_path):
    # A shell of its own, where no other test's launch makes the page read.
    fake = kit.FakeSlots(tmp_path_factory.mktemp("fake-slots"))
    fake.script(placed=2, slots=kit.free(2, 3))
    ui_page = ui_contexts(ui_shells(**fake.options())).pages[0]
    piece = kit.open_home(ui_page)
    go = piece.locator(".go")
    expect(go).to_have_text("Launch in slot 2")
    failed = []

    def fail_first(route) -> None:
        if failed:
            route.continue_()
        else:
            failed.append(route.request.url)
            route.fulfill(status=500, body="")

    ui_page.route(f"**{TARGETS}", fail_first)
    ui_page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(go).to_have_text("Copy command")
    # The press reads where a launch may run before it asks for the preview, and sends
    # what the button said.
    asked = []
    ui_page.on("request", lambda request: asked.append(request.url.rsplit("/", 1)[-1]))
    body = press(ui_page, piece)
    assert body["values"]["target"] == "terminal", body
    assert "targets" in asked[:asked.index("preview")], asked
    expect(go).to_have_text("Launch in slot 2")


# --- a slot the user's defaults pin ---------------------------------------------------------

@pytest.fixture
def pinned(ui_shell):
    """The shell's presets file with the user's defaults pinning slot 7 and a headless
    target; removed when the test ends."""
    file = presets.path(env=ui_shell.env)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text('[defaults]\nslot = "7"\ntarget = "headless"\n')
    yield
    file.unlink()


def test_a_slot_and_target_the_defaults_pin_never_place_a_launch_from_home(
        fake, ui_page, ui_shell, pinned):
    piece = kit.open_home(ui_page)
    go = piece.locator(".go")
    expect(go).to_have_text("Launch in slot 2")
    piece.locator("textarea").fill(BRIEF)
    body = press(ui_page, piece)
    assert body["values"]["target"] == "slot" and "slot" in body["leave_out"], body
    expect(said(piece, "started")).to_have_text(re.compile(r"^Started in slot 2 · "))
    expect(said(piece, "left-out")).to_have_count(0)
    expect(piece.locator(".said")).not_to_contain_text("Left out")
    row = kit.newest_launch(ui_shell)
    assert (row.target, row.slot) == ("slot", "2"), row
    assert fake.starts()[-1]["slot"] is None, fake.starts()[-1]


# --- the stale row --------------------------------------------------------------------------

def test_a_launch_that_stays_starting_gives_the_button_back_and_is_followed_until_it_starts(
        ui_shells, ui_contexts, tmp_path_factory, on_path):
    # A row reads as late once it has been starting for the two waits together: none here.
    # The shell keeps its Moxi files under `moxi` in its home.
    home = tmp_path_factory.mktemp("late-shell")
    config = home / "moxi" / "config" / "launcher" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[ready]\nwait_s = 0\n\n[first]\nwait_s = 0\n")
    fake = kit.FakeSlots(tmp_path_factory.mktemp("fake-slots"))
    fake.script(placed=2, slots=kit.free(2), start_wait_s=4)
    shell = ui_shells(home=home, **fake.options())
    assert shell.config_file("launcher") == config
    page = ui_contexts(shell).pages[0]
    piece = kit.open_home(page)
    go = piece.locator(".go")
    go.click()
    late = said(piece, "late")
    expect(late).to_have_text(LATE)
    expect(said(piece, "starting")).to_have_count(0)
    expect(go).to_be_enabled()
    expect(go).to_have_text("Launch in slot 2")
    # The watch goes on, and the start replaces the line.
    expect(said(piece, "started")).to_have_text(re.compile(r"^Started in slot 2 · "),
                                                timeout=15_000)
    expect(late).to_have_count(0)
    expect(go).to_be_enabled()


# --- colours and width ----------------------------------------------------------------------

_COLOURS = """(piece) => {
  const root = piece.shadowRoot;
  const probe = document.createElement("span");
  const token = (name) => {
    probe.style.color = `var(--moxi-${name})`;
    root.append(probe);
    const seen = getComputedStyle(probe).color;
    probe.remove();
    return seen;
  };
  const of = (selector) => getComputedStyle(root.querySelector(selector));
  return {
    go: of(".go").backgroundColor, more: of(".more").backgroundColor, accent: token("accent"),
    goText: of(".go").color, onAccent: token("on-accent"),
    box: of("textarea").color, said: of(".said p").color, text: token("text"),
    word: of(".where .k").color, dim: token("text-dim"),
    edge: of(".box").borderTopColor, chip: of(".chip").backgroundColor, border: token("border"),
    page: getComputedStyle(document.body).backgroundColor,
  };
}"""


def test_the_button_alone_is_coloured_in_light_and_in_dark(fake, ui_contexts):
    seen = {}
    for scheme in ("light", "dark"):
        page = ui_contexts(color_scheme=scheme).pages[0]
        fake.script(placed=2, slots=kit.free(2, 3))
        piece = kit.open_home(page)
        piece.locator(".go").click()
        expect(said(piece, "started")).to_be_visible()
        got = seen[scheme] = piece.evaluate(_COLOURS)
        assert got["go"] == got["more"] == got["accent"], got
        assert got["goText"] == got["onAccent"], got
        # Everything else is the text colour, the dim one, or a hairline on the page.
        assert got["box"] == got["said"] == got["text"], got
        assert got["word"] == got["dim"] != got["text"], got
        assert got["edge"] == got["border"], got
        assert got["chip"] == "rgba(0, 0, 0, 0)", got
    light, dark = seen["light"], seen["dark"]
    assert (light["text"], light["page"]) != (dark["text"], dark["page"]), seen


def test_on_a_narrow_page_the_line_sits_over_the_box_and_nothing_scrolls_sideways(
        fake, ui_page, ui_width_check):
    ui_page.set_viewport_size({"width": 400, "height": 800})
    piece = kit.open_home(ui_page)
    ui_width_check(ui_page, width=400)
    # The line is in the flow, right over the box, and the piece is the page less its margins.
    line, box = piece.locator(".where").bounding_box(), piece.locator(".box").bounding_box()
    held = piece.bounding_box()
    assert held["x"] == 16 and held["width"] == 400 - 32, held
    assert held["y"] <= line["y"] and line["y"] + line["height"] + 10 == box["y"], (line, box)
    # The chip has a line of its own, and the button sits under it at the right.
    chip, go = piece.locator(".chip").bounding_box(), piece.locator(".go").bounding_box()
    more = piece.locator(".more").bounding_box()
    assert go["y"] >= chip["y"] + chip["height"], (chip, go)
    assert round(more["x"] + more["width"]) <= round(box["x"] + box["width"]), (more, box)
    # Each menu opens inside the page.
    for face, name in ((".where .face", "where"), (".chip", "chip"), (".more", "run")):
        piece.locator(face).click()
        menu = piece.locator(f'[data-menu="{name}"]')
        expect(menu).to_be_visible()
        found = menu.bounding_box()
        assert found["x"] >= 0 and found["x"] + found["width"] <= 400, (name, found)
        ui_width_check(ui_page, width=400)
        ui_page.keyboard.press("Escape")
        expect(menu).to_have_count(0)
    # A launch's line wraps under the box, and Home's rows stay under the piece.
    piece.locator(".go").click()
    expect(said(piece, "started")).to_be_visible()
    ui_width_check(ui_page, width=400)
    ui_width_check(ui_page, width=360)
    rows = ui_page.locator("#moxi-panel > moxi-ui-page moxi-ui-home .rows").bounding_box()
    held = piece.bounding_box()
    assert rows["y"] >= held["y"] + held["height"], (rows, held)
