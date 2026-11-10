"""The list of sessions on sessions' page, in a browser, through a real shell that starts
sessions' real page server: the groups and their count lines; a two-line and a one-line
row, and a one-line row opened from the keyboard; a day's `Show` for the rows past its
first eight; the line for the sessions a program started, shown, hidden and absent;
`Earlier`; each filter and `Clear`; the find field by folder and by id; a list read again
when its tab is shown; the line for compressed Codex sessions; the empty and the
filtered-empty state; the line for a harness whose sessions could not be read; a route
the page has no view for; the 360 px width check, and the filter line wrapped at 360 px
with no line starting with a separator.

Three shells, each over roots of its own written with `moxi_sessions.testing`: one with
sessions of every kind, one with none, and one whose Codex root cannot be listed. Every
session is timed from the moment the test runs; a past day's sessions are set at that
day's local noon, so no test depends on the hour it runs at.
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

LIST = "#moxi-panel > moxi-sessions-page moxi-sessions-list"
#: How many sessions the seeded roots hold, and how many of them are Claude Code's.
ALL = 26
CLAUDE = 24
PROMPT = "Run the suite and report every diff."


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


def tid(n: int) -> str:
    return f"0190c000-0000-7000-8000-{n:012x}"


def noon(days_ago: int) -> datetime:
    """Local noon of the day `days_ago` days before today."""
    return datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)


def day_key(days_ago: int) -> str:
    return noon(days_ago).strftime("%Y-%m-%d")


def date_words(days_ago: int) -> str:
    """A day as the page words a date: `Nov 6`, with the year when it is not this one."""
    day = noon(days_ago)
    words = f"{day.strftime('%b')} {day.day}"
    return words if day.year == datetime.now().year else f"{words}, {day.year}"


def day_label(days_ago: int) -> str:
    return f"{noon(days_ago).strftime('%a')} {date_words(days_ago)}"


def changed_at(path: Path, at: float) -> None:
    stamp = int(at * 1_000_000_000)
    os.utime(path, ns=(stamp, stamp))


def claude(root: Path, n: int, at: float, *, program: bool = False, typed: str | None = None,
           folder: str = "proj-a", name: str | None = None, subagents: int = 0) -> None:
    """A Claude Code session last changed at `at`: started in a terminal, or by a program;
    with a typed message only when `typed` gives one."""
    t = ClaudeTranscript(root, session_id=sid(n), cwd=f"/work/{folder}", project=f"-work-{folder}",
                         entrypoint="sdk-cli" if program else "cli", start=at - 600)
    if name is not None:
        t.title(name)
    if typed is not None:
        t.user(typed, origin="human")
    else:
        t.user("A message no person typed.")
    for k in range(subagents):
        sub = t.subagent(f"a0190b000000000{k + 1}", description=f"look at part {k + 1}")
        sub.user("look")
        sub.assistant("looked")
    t.assistant("Done.")
    changed_at(t.write(), at)


def env_of(base: Path) -> dict:
    return {"env": {"CLAUDE_CONFIG_DIR": str(base / "claude-home"), "CODEX_HOME": str(base / "codex-home")}}


def seed(env: dict[str, str]) -> None:
    """Sessions of every kind the list meets:

    * two changed in the last hour: one named, with a typed message and two subagents;
      one with no typed message;
    * two days back: ten started in a terminal and three by a program;
    * three days back: two started by a program and none in a terminal;
    * four days back a Codex session, and a compressed rollout beside it; five days back
      an OpenCode session; six, seven and eight days back one Claude Code session each;
    * older than those seven days: one a day from nine to eleven days back, and one forty
      days back.
    """
    root = Path(env["CLAUDE_CONFIG_DIR"]) / "projects"
    now = time.time()
    claude(root, 1, now - 600, typed=PROMPT, name="fix-tooltips", subagents=2)
    claude(root, 2, now - 1200, folder="proj-b")
    for k in range(10):
        claude(root, 10 + k, noon(2).timestamp() + 60 * k, typed=f"Task {k}.")
    for k in range(3):
        claude(root, 20 + k, noon(2).timestamp() - 60 * (k + 1), program=True)
    for k in range(2):
        claude(root, 30 + k, noon(3).timestamp() + k, program=True)
    for k, days in enumerate((6, 7, 8, 9, 10, 11, 40)):
        claude(root, 40 + k, noon(days).timestamp())

    rollout = CodexRollout(Path(env["CODEX_HOME"]) / "sessions", thread_id=tid(1), cwd="/work/proj-b",
                           start=noon(4).timestamp() - 600)
    rollout.turn()
    rollout.user("Do the thing.")
    rollout.assistant("Done.")
    changed_at(rollout.write(), noon(4).timestamp())
    rollout.path.with_name(f"rollout-2026-10-30T10-00-00-{tid(2)}.jsonl.zst").write_bytes(b"not read")

    db = OpenCodeDb(Path(env["XDG_DATA_HOME"]) / "opencode")
    db.session(title="a title the harness made", directory="/work/proj-c", at=noon(5).timestamp())
    db.write()


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return env_of(tmp_path_factory.mktemp("harnesses"))


@pytest.fixture(scope="module")
def seeded(ui_shell) -> None:
    seed(ui_shell.env)


@pytest.fixture(scope="module")
def bare_shell(ui_shells, tmp_path_factory):
    """A shell on a machine with no session: the harnesses' roots do not exist."""
    return ui_shells(**env_of(tmp_path_factory.mktemp("no-harness")))


@pytest.fixture(scope="module")
def broken_shell(ui_shells, tmp_path_factory):
    """A shell with one Claude Code session and a Codex root that cannot be listed."""
    shell = ui_shells(**env_of(tmp_path_factory.mktemp("broken")))
    claude(Path(shell.env["CLAUDE_CONFIG_DIR"]) / "projects", 1, time.time() - 600, typed=PROMPT)
    codex = Path(shell.env["CODEX_HOME"])
    codex.mkdir(parents=True)
    (codex / "sessions").write_text("not a folder")
    return shell


def open_list(page, route: str = ""):
    page.goto(f"/m/sessions/{route}")
    return page.locator(LIST)


def group(view, key: str):
    return view.locator(f'section[data-group="{key}"]')


def row(view, harness: str, session: str):
    return view.locator(f'moxi-ui-row[data-key="{harness}/{session}"]')


def keys(rows, count: int) -> list[str]:
    """The sessions `rows` are of, in order, once there are `count` of them."""
    expect(rows).to_have_count(count)
    return rows.evaluate_all("rows => rows.map((row) => row.dataset.key)")


def count_line(view):
    return view.locator('[data-line="count"]')


def filter_button(view, key: str):
    return view.locator(f'[data-filter="{key}"] > button')


def test_the_page_keeps_the_contract_and_has_no_heading(seeded, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "sessions", "page", keep=True)
    assert check.element == "moxi-sessions-page"
    view = check.locator.locator("moxi-sessions-list")
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    expect(check.locator.locator("h1, h2")).to_have_count(0)


def test_the_groups_are_active_recently_then_the_days_then_earlier(seeded, ui_page):
    view = open_list(ui_page)
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    labels = ["Active recently"] + [day_label(n) for n in range(2, 9)] + ["Earlier"]
    expect(view.locator('section [data-line="label"]')).to_have_text(labels)
    expect(view.locator("section")).to_have_count(9)
    assert keys(group(view, "recent").locator("moxi-ui-row"), 2) == [f"claude/{sid(1)}", f"claude/{sid(2)}"]
    expect(group(view, day_key(4)).locator("moxi-ui-row")).to_have_attribute(
        "name", re.compile(rf"^proj-b · started {re.escape(date_words(4))} "))
    expect(group(view, day_key(5)).locator("moxi-ui-row")).to_have_attribute(
        "name", re.compile(rf"^proj-c · started {re.escape(date_words(5))} "))
    expect(view.locator("moxi-ui-row[open]")).to_have_count(0)


def test_a_row_with_a_typed_message_has_two_lines_and_one_without_has_one(seeded, ui_page):
    view = open_list(ui_page)
    two = row(view, "claude", sid(1))
    expect(two.locator(".name")).to_have_text("fix-tooltips")
    expect(two.locator(".where")).to_have_text("proj-a")
    expect(two.locator(".l2")).to_contain_text(re.compile(r"2 subagents\s*·.*You:\s*" + re.escape(PROMPT)))
    expect(two).to_have_attribute("said-by", "you")
    expect(two).to_have_attribute("time", re.compile(r"^\d"))
    expect(two.get_by_role("button", name="Open")).to_be_visible()

    one = row(view, "claude", sid(2))
    expect(one.locator(".name")).to_have_text(re.compile(r"^proj-b · started today "))
    expect(one.locator(".l2")).to_be_hidden()
    expect(one.locator('[slot="acts"]')).to_have_count(0)
    for absent in ("where", "lead", "time", "said-by", "rest", "fact"):
        expect(one).not_to_have_attribute(absent, re.compile(""))
    assert one.bounding_box()["height"] < two.bounding_box()["height"]


def test_the_open_act_opens_and_closes_a_row_in_place(seeded, ui_page):
    view = open_list(ui_page)
    two = row(view, "claude", sid(1))
    two.get_by_role("button", name="Open").click()
    expect(two).to_have_attribute("open", "")
    two.get_by_role("button", name="Close").click()
    expect(two).not_to_have_attribute("open", "")
    two.locator(".l2 .said").click()
    expect(two).to_have_attribute("open", "")
    expect(view.locator("moxi-ui-row[open]")).to_have_count(1)


def test_a_one_line_row_opens_from_the_keyboard_by_its_line(seeded, ui_page):
    view = open_list(ui_page)
    one = row(view, "claude", sid(2))
    line = one.get_by_role("button", name=re.compile(r"^proj-b · started today "))
    expect(line).to_have_attribute("aria-expanded", "false")
    # Tab from the row before reaches the line: its first line, then its Open act.
    row(view, "claude", sid(1)).get_by_role("button", name="Open").focus()
    ui_page.keyboard.press("Tab")
    expect(line).to_be_focused()
    ring = line.evaluate("line => { const s = getComputedStyle(line); return [line.matches(':focus-visible'),"
                         " s.outlineStyle, s.outlineWidth]; }")
    assert ring == [True, "solid", "2px"]
    ui_page.keyboard.press("Enter")
    expect(one).to_have_attribute("open", "")
    expect(line).to_have_attribute("aria-expanded", "true")
    expect(line).to_be_focused()
    ui_page.keyboard.press("Enter")
    expect(one).not_to_have_attribute("open", "")
    expect(line).to_have_attribute("aria-expanded", "false")
    ui_page.keyboard.press("Space")
    expect(one).to_have_attribute("open", "")
    expect(line).to_have_attribute("aria-expanded", "true")
    ui_page.keyboard.press("Space")
    expect(line).to_have_attribute("aria-expanded", "false")


def test_a_day_shows_its_first_eight_rows_and_show_adds_the_rest(seeded, ui_page):
    view = open_list(ui_page)
    day = group(view, day_key(2))
    more = day.locator('[data-line="more"]')
    assert keys(day.locator("moxi-ui-row"), 8) == [f"claude/{sid(n)}" for n in range(19, 11, -1)]
    expect(more.locator("span")).to_have_text("2 more")
    more.get_by_role("button", name="Show").click()
    assert keys(day.locator("moxi-ui-row"), 10) == [f"claude/{sid(n)}" for n in range(19, 9, -1)]
    expect(more).to_have_count(0)


def test_the_program_line_shows_and_hides_its_rows_and_is_absent_without_any(seeded, ui_page):
    view = open_list(ui_page)
    day = group(view, day_key(2))
    line = day.locator('[data-line="program"]')
    expect(line.locator("span")).to_have_text("3 started by a program")
    show = line.get_by_role("button", name="Show")
    expect(show).to_have_attribute("aria-expanded", "false")
    show.click()
    expect(line.get_by_role("button", name="Hide")).to_have_attribute("aria-expanded", "true")
    assert keys(day.locator("moxi-ui-row"), 11)[8:] == [f"claude/{sid(n)}" for n in (20, 21, 22)]
    program = row(view, "claude", sid(20))
    expect(program).not_to_have_attribute("said-by", re.compile(""))
    expect(program.locator(".l2")).to_be_hidden()
    # The rows a program started are no part of the day's own count.
    expect(day.locator('[data-line="more"] span')).to_have_text("2 more")
    line.get_by_role("button", name="Hide").click()
    expect(day.locator("moxi-ui-row")).to_have_count(8)
    expect(line.get_by_role("button", name="Show")).to_be_visible()

    only = group(view, day_key(3))
    expect(only.locator('[data-line="label"]')).to_have_text(day_label(3))
    expect(only.locator('[data-line="program"] span')).to_have_text("2 started by a program")
    expect(only.locator("moxi-ui-row")).to_have_count(0)
    for days in (4, 5, 6):
        expect(group(view, day_key(days)).locator('[data-line="program"]')).to_have_count(0)
    expect(group(view, "recent").locator('[data-line="program"]')).to_have_count(0)


def test_earlier_says_how_many_and_how_far_back_and_show_brings_its_days(seeded, ui_page):
    view = open_list(ui_page)
    earlier = group(view, "earlier")
    expect(earlier.locator('[data-line="earlier"] span')).to_have_text(f"4 sessions, back to {date_words(40)}")
    expect(earlier.locator("moxi-ui-row")).to_have_count(0)
    earlier.get_by_role("button", name="Show").click()
    labels = ["Active recently"] + [day_label(n) for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 40)]
    expect(view.locator('section [data-line="label"]')).to_have_text(labels)
    expect(group(view, "earlier")).to_have_count(0)
    expect(group(view, day_key(40)).locator("moxi-ui-row")).to_have_attribute("data-key", f"claude/{sid(46)}")


def test_the_harness_filter_lists_the_harnesses_with_sessions_and_narrows_the_list(seeded, ui_page):
    view = open_list(ui_page)
    button = filter_button(view, "harness")
    expect(button).to_have_text(re.compile("^all"))
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    menu = view.locator('[data-menu="harness"]')
    expect(menu.get_by_role("menuitemradio")).to_have_text([
        re.compile("All harnesses"), re.compile(rf"Claude Code\s*{CLAUDE}"), re.compile(r"Codex\s*1"),
        re.compile(r"OpenCode\s*1")])
    expect(menu.get_by_role("menuitemradio", name="All harnesses")).to_have_attribute("aria-checked", "true")
    menu.get_by_role("menuitemradio", name="Codex").click()
    expect(menu).to_have_count(0)
    expect(count_line(view)).to_have_text(f"1 of {ALL} sessions")
    expect(button).to_have_text(re.compile("^Codex"))
    expect(button).to_have_class("set")
    assert keys(view.locator("moxi-ui-row"), 1) == [f"codex/{tid(1)}"]
    expect(view.locator('section [data-line="label"]')).to_have_text([day_label(4)])
    view.get_by_role("button", name="Clear").click()
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    expect(view.get_by_role("button", name="Clear")).to_have_count(0)
    expect(button).not_to_have_class("set")


def test_the_when_filter_takes_today_and_a_range_of_days(seeded, ui_page):
    view = open_list(ui_page)
    button = filter_button(view, "when")
    expect(button).to_have_text(re.compile("^any time"))
    button.click()
    menu = view.locator('[data-menu="when"]')
    expect(menu.get_by_role("menuitemradio")).to_have_text([
        re.compile("Any time"), re.compile("Today"), re.compile("Last 7 days"), re.compile("Last 30 days")])
    menu.get_by_role("menuitemradio", name="Last 30 days").click()
    expect(count_line(view)).to_have_text(f"{ALL - 1} of {ALL} sessions")
    expect(button).to_have_text(re.compile("^last 30 days"))

    button.click()
    menu.get_by_label("From").fill(day_key(5))
    menu.get_by_label("To").fill(day_key(4))
    expect(button).to_have_text(re.compile(rf"^{re.escape(date_words(5))} to {re.escape(date_words(4))}"))
    expect(count_line(view)).to_have_text(f"2 of {ALL} sessions")
    assert sorted(keys(view.locator("moxi-ui-row"), 2))[0] == f"codex/{tid(1)}"
    ui_page.keyboard.press("Escape")
    expect(menu).to_have_count(0)

    button.click()
    menu.get_by_role("menuitemradio", name="Any time").click()
    expect(count_line(view)).to_have_text(f"{ALL} sessions")


def test_the_folder_filter_lists_the_folders_narrows_them_and_narrows_the_list(seeded, ui_page):
    view = open_list(ui_page)
    button = filter_button(view, "folder")
    button.click()
    menu = view.locator('[data-menu="folder"]')
    items = menu.get_by_role("menuitemradio")
    expect(items).to_have_text([re.compile("All folders"), re.compile(r"proj-a\s*/work"), re.compile(r"proj-b\s*/work"),
                                re.compile(r"proj-c\s*/work")])
    menu.get_by_label("Narrow the folders").fill("j-C")
    expect(items).to_have_text([re.compile("All folders"), re.compile("proj-c")])
    menu.get_by_label("Narrow the folders").fill("nowhere")
    expect(menu.locator('[data-line="folders"]')).to_have_text("No folder matches.")
    menu.get_by_label("Narrow the folders").fill("")
    menu.get_by_role("menuitemradio", name="proj-b").click()
    expect(count_line(view)).to_have_text(f"2 of {ALL} sessions")
    expect(button).to_have_text(re.compile("^proj-b"))
    assert keys(view.locator("moxi-ui-row"), 2) == [f"claude/{sid(2)}", f"codex/{tid(1)}"]
    # A press outside an open menu closes it.
    button.click()
    expect(menu).to_be_visible()
    count_line(view).click()
    expect(menu).to_have_count(0)


def test_the_find_field_finds_by_folder_and_by_id_a_program_session_too(seeded, ui_page):
    view = open_list(ui_page)
    field = view.get_by_label("Find a session")
    expect(field).to_have_attribute("placeholder", "Find a session by its id or folder")
    field.fill("p")
    ui_page.wait_for_timeout(400)
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    field.fill("ROJ-C")
    expect(count_line(view)).to_have_text(f"1 of {ALL} sessions")
    expect(view.locator("section")).to_have_count(1)
    expect(view.locator('section [data-line="label"]')).to_have_count(0)
    found = view.locator("moxi-ui-row")
    expect(found).to_have_attribute("data-key", re.compile("^opencode/"))
    expect(found).to_have_attribute("name", re.compile(rf"^proj-c · started {re.escape(date_words(5))} "))

    field.fill(sid(20))
    expect(found).to_have_attribute("data-key", f"claude/{sid(20)}")
    expect(count_line(view)).to_have_text(f"1 of {ALL} sessions")
    view.locator('[data-action="clear-find"]').click()
    expect(field).to_have_value("")
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    expect(view.locator("section")).to_have_count(9)


def test_a_find_that_matches_nothing_says_no_sessions_match(seeded, ui_page):
    view = open_list(ui_page)
    view.get_by_label("Find a session").fill("zzzz")
    expect(view.locator('[data-line="empty"]')).to_have_text("No sessions match.")
    expect(count_line(view)).to_have_text(f"0 of {ALL} sessions")
    expect(view.locator("moxi-ui-row")).to_have_count(0)
    expect(view.get_by_label("Find a session")).to_be_visible()
    expect(filter_button(view, "harness")).to_be_visible()


def test_compressed_codex_sessions_get_one_line_at_the_foot(seeded, ui_page):
    view = open_list(ui_page)
    expect(view.locator('[data-line="unlisted"]')).to_have_text("1 Codex session is compressed and not listed.")
    expect(view.locator('[data-line="unread"]')).to_have_count(0)
    below = view.evaluate("""list => {
      const line = list.shadowRoot.querySelector('[data-line="unlisted"]');
      const groups = list.shadowRoot.querySelector('[data-part="groups"]');
      return line.getBoundingClientRect().top >= groups.getBoundingClientRect().bottom;
    }""")
    assert below


def test_the_list_is_read_again_when_its_tab_is_shown(seeded, ui_page, ui_shell):
    view = open_list(ui_page)
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    open_row = row(view, "claude", sid(2))
    open_row.locator(".l1").click()
    expect(open_row).to_have_attribute("open", "")
    root = Path(ui_shell.env["CLAUDE_CONFIG_DIR"]) / "projects"
    path = root / "-work-proj-a" / f"{sid(99)}.jsonl"
    try:
        claude(root, 99, time.time() - 5, typed="A new one.")
        # The page server reuses a listing for two seconds.
        ui_page.wait_for_timeout(2100)
        ui_page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(count_line(view)).to_have_text(f"{ALL + 1} sessions")
        assert keys(group(view, "recent").locator("moxi-ui-row"), 3)[0] == f"claude/{sid(99)}"
        expect(open_row).to_have_attribute("open", "")
    finally:
        path.unlink(missing_ok=True)
        time.sleep(2.1)


def test_a_route_the_page_has_no_view_for_says_so_over_the_list(seeded, ui_page):
    view = open_list(ui_page, "nowhere")
    expect(view.locator('[data-line="unknown"]')).to_contain_text("There is no such view here.")
    expect(count_line(view)).to_have_text(f"{ALL} sessions")


def test_the_list_fits_a_phone(seeded, ui_page, ui_width_check):
    ui_width_check(ui_page, module="sessions")
    view = ui_page.locator(LIST)
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    ui_width_check(ui_page)
    group(view, day_key(2)).locator('[data-line="program"]').get_by_role("button", name="Show").click()
    expect(group(view, day_key(2)).locator("moxi-ui-row")).to_have_count(11)
    row(view, "claude", sid(1)).get_by_role("button", name="Open").click()
    ui_width_check(ui_page)
    for key in ("folder", "harness", "when"):
        filter_button(view, key).click()
        expect(view.locator(f'[data-menu="{key}"]')).to_be_visible()
        if key == "folder":
            expect(view.locator('[data-menu="folder"]').get_by_role("menuitemradio")).to_have_count(4)
        ui_width_check(ui_page)
    filter_button(view, "when").click()
    filter_button(view, "harness").click()
    view.locator('[data-menu="harness"]').get_by_role("menuitemradio", name="Claude Code").click()
    expect(count_line(view)).to_have_text(f"{CLAUDE} of {ALL} sessions")
    ui_width_check(ui_page)


#: The filter line's pieces as they are drawn, each `[line top, left, text]` with the
#: separators' text `·`, for the ones that are shown.
FILTER_PIECES = """(line) => [...line.querySelectorAll(
    ':scope .fs > .f > .k, :scope .fs > .f > .anchor > button, :scope .fs > .f > button, :scope .fs > .f > .sep')]
  .filter((piece) => piece.checkVisibility({ visibilityProperty: true }))
  .map((piece) => { const box = piece.getBoundingClientRect();
                    return [Math.round(box.top + box.height / 2), box.left, piece.textContent.trim()]; })"""


def filter_lines(view) -> list[list[str]]:
    """The filter line's shown pieces, one list per drawn line, left to right."""
    lines: dict[int, list[tuple[float, str]]] = {}
    for middle, left, text in view.locator('[data-part="filters"]').evaluate(FILTER_PIECES):
        at = next((top for top in lines if abs(top - middle) < 8), middle)
        lines.setdefault(at, []).append((left, text))
    return [[text for _, text in sorted(pieces)] for _, pieces in sorted(lines.items())]


def test_a_wrapped_filter_line_starts_no_line_with_a_separator(seeded, ui_page, ui_width_check):
    ui_width_check(ui_page, module="sessions")
    view = ui_page.locator(LIST)
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    for setting in ("none", "Codex"):
        if setting != "none":
            filter_button(view, "harness").click()
            view.locator('[data-menu="harness"]').get_by_role("menuitemradio", name=setting).click()
            expect(view.get_by_role("button", name="Clear")).to_be_visible()
        lines = filter_lines(view)
        assert len(lines) > 1, lines
        for line in lines:
            assert line[0] != "·" and line[-1] != "·", lines
        # Every filter still has its separator between it and the next on its line.
        assert [piece for line in lines for piece in line].count("·") >= 1, lines
    ui_width_check(ui_page)


def test_a_machine_with_no_session_says_so_and_draws_no_field_or_filter(bare_shell, ui_contexts):
    page = ui_contexts(bare_shell).pages[0]
    view = open_list(page)
    expect(view.locator('[data-line="empty"]')).to_have_text("No sessions yet.")
    expect(view.locator('[data-line="empty-sentence"]')).to_have_text(
        "Moxi lists every Claude Code, Codex and OpenCode session on this machine, whether it was started "
        "here or in a terminal. It looked and found none.")
    for absent in ('[data-part="find"]', '[data-part="filters"]', '[data-line="count"]', "section", "moxi-ui-row",
                   '[data-line="unread"]', '[data-line="unlisted"]'):
        expect(view.locator(absent)).to_have_count(0)
    launcher = page.evaluate("async () => (await import('/m/ui/client.js')).installed('launcher')")
    home = view.get_by_role("link", name="Start one from Home")
    expect(home).to_have_count(1 if launcher else 0)


def test_a_harness_whose_sessions_cannot_be_read_gets_a_line_under_the_list(broken_shell, ui_contexts, ui_width_check):
    page = ui_contexts(broken_shell).pages[0]
    view = open_list(page)
    expect(count_line(view)).to_have_text("1 session")
    expect(view.locator("moxi-ui-row")).to_have_count(1)
    line = view.locator('[data-line="unread"]')
    expect(line).to_contain_text("Moxi couldn't read Codex's sessions: ")
    expect(line.locator('[data-line="reason"]')).not_to_be_empty()
    expect(view.locator('[data-line="empty"]')).to_have_count(0)
    ui_width_check(page)
