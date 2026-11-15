"""The addresses of usage's page and its opened limit row in a browser, through a real
shell that starts usage's real page server: each address opened directly, the address
rewritten in place when a piece opens or closes (so Back leaves the page and Forward
comes back to the piece), a reload landing on the same piece, the range, the filters and
the measure kept from one placement of the page to the next and never written into the
address, an address the page does not have, and a limit row opened with every line, with
each line absent, while its read is out, when it fails and when it is refused, and at a
phone's width.

The transcripts and the readings are written, and the index built with `python -m moxi
usage index`, in the shell's own environment, so they sit under the shell's home where
its page servers read them.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="usage's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

MODEL = "claude-sonnet-4-5"
OTHER_MODEL = "claude-haiku-4-5"
FIRST = "0190b000-0000-7000-8000-0000000000a1"
SECOND = "0190b000-0000-7000-8000-0000000000a2"
AGENT = "a0190b00000000a1"
#: Two sessions of three requests each, on two models. The first began an hour ago,
#: inside Claude's 5-hour window, and has a subagent of two requests.
SESSIONS = [
    {"session": FIRST, "project": "proj-a", "hours_ago": 1, "agent": AGENT, "model": MODEL},
    {"session": SECOND, "project": "proj-b", "hours_ago": 30, "agent": None, "model": OTHER_MODEL},
]

SEED = """
import json, sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for spec in json.loads(sys.argv[1]):
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=spec["session"],
                           project="-work-" + spec["project"],
                           cwd="/work/" + spec["project"],
                           start=now - spec["hours_ago"] * 3600)
    for m in range(3):
        t.user("Fix the failing test.")
        t.assistant("Done.", usage=T.usage(input=200, output=1000 + m * 500),
                    request_id=f"req_{spec['session'][-2:]}{m}", model=spec["model"])
    if spec["agent"]:
        child = t.subagent(spec["agent"])
        for m in range(2):
            child.user("Look into it.")
            child.assistant("Found it.", usage=T.usage(input=100, output=400),
                            request_id=f"req_{spec['agent'][-4:]}{m}", model=spec["model"])
    t.write()
"""

#: Stores plan readings where the environment it runs in keeps them.
#: - Claude's 5-hour window opened two hours ago and has three readings: every line.
#: - Claude's weekly window has one reading at 0%: its start is not known, so it says
#:   only its reset, and nothing of who used it.
#: - Claude's weekly Opus window has one reading at 33%: a scoped limit, so its start is
#:   known and nothing says who used it.
READINGS = """
import time
from moxi_usage import readings
from moxi_usage.readings import Reading
now = time.time()
found = [
    Reading("claude", "plan", 300, "five_hour", used, now + 3 * 3600, now - ago, "session-usage", "s1")
    for used, ago in ((20.0, 3000), (35.0, 1500), (48.6, 240))
] + [
    Reading("claude", "plan", 10080, "seven_day", 0.0, now + 5 * 86400, now - 240, "session-usage", "s1"),
    Reading("claude", "opus", 10080, "seven_day_opus", 33.0, now + 5 * 86400, now - 240, "session-usage", "s1"),
]
conn = readings.open_store()
try:
    readings.store(conn, found, now=now, days=7)
finally:
    conn.close()
"""

TIME = r"\d{1,2}:\d\d(?: [AP]M)?"
#: A time with the day it may carry: `21:34`, `today 21:34`, `Wed 23:58`, `Nov 3 08:52`.
WHEN = rf"(?:\w+ (?:\d+ )?)?{TIME}"
FIVE = "claude/plan/300"
WEEK = "claude/plan/10080"
OPUS = "claude/opus/10080"
NO_SUCH_VIEW = "There is no such view here."
#: What ends the readings line: the session the readings came from, which the index does
#: not hold, so it has no name, folder or start to be told by.
LAST_FROM = " · the last from Claude Code session"


def run(shell, *argv: str) -> str:
    done = subprocess.run(list(argv), env=shell.env, capture_output=True, text=True,
                          timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done.stdout


@pytest.fixture(scope="module")
def seeded(ui_shells, ui_shell_options):
    """A shell of the module's own: two sessions indexed, and the plan readings stored."""
    shell = ui_shells(**ui_shell_options)
    run(shell, sys.executable, "-c", SEED, json.dumps(SESSIONS))
    run(shell, sys.executable, "-m", "moxi", "usage", "index")
    run(shell, sys.executable, "-c", READINGS)
    return shell


@pytest.fixture
def page(seeded, ui_contexts):
    return ui_contexts(seeded).pages[0]


def panel_of(page):
    return page.locator("#moxi-panel > moxi-usage-page")


def open_page(page, route: str = ""):
    page.goto(f"/m/usage/{route}")
    return panel_of(page)


def limit(panel, key: str):
    return panel.locator(f'[data-group=limits] [data-limit="{key}"]')


def opened(panel):
    return panel.locator("[data-part=opened]")


def lines_of(panel):
    return panel.locator("[data-part=lines] p")


def session_row(panel, session: str):
    """A row of the Sessions section, by the harness and session it opens."""
    return panel.locator(f'section[data-group=sessions] moxi-ui-row[data-key="claude/{session}"]')


def price_table(panel):
    return panel.locator("section[data-group=prices] [data-part=price-table]")


def path_of(page) -> str:
    return page.evaluate("location.pathname + location.search + location.hash")


# --- each address --------------------------------------------------------------------------

def test_the_page_alone_has_nothing_opened(seeded, page):
    panel = open_page(page)
    expect(limit(panel, FIVE)).to_have_attribute("aria-expanded", "false")
    expect(panel.locator("[data-group=chart]")).to_be_visible()
    expect(opened(panel)).to_have_count(0)
    expect(lines_of(panel)).to_have_count(0)
    assert path_of(page) == "/m/usage/"


#: Each section's address, the address the page keeps for it, and what it shows.
SECTIONS = {
    "sessions": ("sessions", f'section[data-group=sessions] moxi-ui-row[data-key="claude/{FIRST}"]'),
    f"sessions/claude/{FIRST}": (f"sessions/claude/{FIRST}", "[data-part=session] [data-line=subagents]"),
    f"sessions/claude/{FIRST}/{AGENT}": (f"sessions/claude/{FIRST}/{AGENT}",
                                         "[data-part=agent] svg[data-part=session-chart] rect.bar"),
    "projects": ("projects", "section[data-group=projects] [data-part=rows] .tr2"),
    # Projects is installed, so the section's other name is rewritten to this one.
    "folders": ("projects", "section[data-group=projects] [data-part=rows] .tr2"),
    "models": ("models", f'section[data-group=models] [data-part=rows] [data-row="{MODEL}"]'),
    "kinds": ("kinds", "section[data-group=kinds] [data-part=rows] [data-row=cache_read]"),
    "prices": ("prices", f'section[data-group=prices] [data-part=price-table] [data-price^="{MODEL}|"]'),
}


@pytest.mark.parametrize("route", list(SECTIONS), ids=["sessions", "session", "subagent", "projects",
                                                       "folders", "models", "kinds", "prices"])
def test_a_sections_address_opens_it_under_the_chart(seeded, page, route):
    kept, shown = SECTIONS[route]
    panel = open_page(page, route)
    expect(panel.locator(shown).first).to_be_visible()
    expect(panel.locator("[data-group=chart]")).to_be_visible()
    expect(opened(panel)).to_have_count(0)
    expect(lines_of(panel)).to_have_count(0)
    assert path_of(page) == f"/m/usage/{kept}"


def test_a_subagents_address_shows_the_rows_and_opens_its_requests_under_its_row(seeded, page):
    panel = open_page(page, f"sessions/claude/{FIRST}/{AGENT}")
    row = panel.locator(f'[data-part=agents] moxi-ui-row[data-agent="{AGENT}"]')
    expect(row).to_have_attribute("open", "")
    expect(row.locator("[data-part=agent] svg[data-part=session-chart] rect.bar")).to_have_count(2)
    session = panel.locator("[data-part=session] > [data-line=chart] svg[data-part=session-chart]")
    expect(session.locator("rect.bar")).to_have_count(3)
    expect(panel.locator("[data-line=subagents]").get_by_role("button", name="Hide")).to_be_visible()
    # The session's own address shows neither the rows nor an agent's requests.
    panel = open_page(page, f"sessions/claude/{FIRST}")
    expect(panel.locator("[data-part=session] svg[data-part=session-chart] rect.bar")).to_have_count(3)
    expect(panel.locator("[data-part=agents]")).to_have_count(0)
    expect(panel.locator("[data-part=agent]")).to_have_count(0)


def test_a_limits_address_opens_its_row(seeded, page):
    panel = open_page(page, "limit/claude/plan/300")
    row = limit(panel, FIVE)
    expect(row).to_have_attribute("aria-expanded", "true")
    expect(opened(panel)).to_have_count(1)
    expect(opened(panel).locator("[data-opened=window]")).to_be_visible()
    expect(limit(panel, WEEK)).to_have_attribute("aria-expanded", "false")
    assert path_of(page) == "/m/usage/limit/claude/plan/300"


@pytest.mark.parametrize("route", ["nowhere", "daily", "terms", f"session/{FIRST}", "limit/claude/plan",
                                   "limit/claude/plan/5h", "sessions/claude", "prices/more"])
def test_an_address_the_page_does_not_have_shows_its_top_and_says_so_at_the_foot(seeded, page, route):
    panel = open_page(page, route)
    expect(lines_of(panel)).to_have_text([NO_SUCH_VIEW])
    expect(panel.locator("[data-group=cost]")).to_be_visible()
    expect(panel.locator("[data-group=chart]")).to_be_visible()
    expect(opened(panel)).to_have_count(0)
    # Opening a piece takes the line away and names the piece in the address.
    limit(panel, FIVE).click()
    expect(opened(panel)).to_have_count(1)
    expect(lines_of(panel)).to_have_count(0)
    assert path_of(page) == "/m/usage/limit/claude/plan/300"


def test_a_limit_address_whose_window_has_no_fresh_row_closes_to_the_page(seeded, page):
    panel = open_page(page, "limit/claude/plan/999")
    expect(panel.locator("[data-group=limits]")).to_be_visible()
    expect(page).to_have_url(f"{seeded.url}/m/usage/")
    expect(opened(panel)).to_have_count(0)
    expect(lines_of(panel)).to_have_count(0)


# --- the address is rewritten in place -----------------------------------------------------

def test_opening_and_closing_rewrite_the_address_and_add_no_history_entry(seeded, page):
    panel = open_page(page)
    expect(limit(panel, FIVE)).to_be_visible()
    entries = page.evaluate("history.length")
    limit(panel, FIVE).click()
    expect(page).to_have_url(f"{seeded.url}/m/usage/limit/claude/plan/300")
    expect(opened(panel)).to_have_count(1)
    # One piece is open at a time: another row takes the first one's place.
    limit(panel, OPUS).click()
    expect(page).to_have_url(f"{seeded.url}/m/usage/limit/claude/opus/10080")
    expect(limit(panel, FIVE)).to_have_attribute("aria-expanded", "false")
    expect(opened(panel)).to_have_count(1)
    # A section takes the row's place.
    panel.locator("[data-group=cost]").get_by_role("link", name="Prices").click()
    expect(page).to_have_url(f"{seeded.url}/m/usage/prices")
    expect(price_table(panel)).to_be_visible()
    expect(opened(panel)).to_have_count(0)
    # And a row takes the section's.
    limit(panel, OPUS).click()
    expect(price_table(panel)).to_have_count(0)
    limit(panel, OPUS).get_by_text("Close").click()
    expect(page).to_have_url(f"{seeded.url}/m/usage/")
    expect(opened(panel)).to_have_count(0)
    assert page.evaluate("history.length") == entries
    # The page was not placed again for any of it.
    assert panel.evaluate("el => el.isConnected")


def test_back_leaves_the_page_and_forward_comes_back_to_the_opened_piece(seeded, page):
    page.goto("/m/ui/modules")
    panel = open_page(page)
    limit(panel, FIVE).click()
    expect(opened(panel)).to_have_count(1)
    page.go_back()
    expect(page).to_have_url(f"{seeded.url}/m/ui/modules")
    page.go_forward()
    expect(page).to_have_url(f"{seeded.url}/m/usage/limit/claude/plan/300")
    panel = panel_of(page)
    expect(limit(panel, FIVE)).to_have_attribute("aria-expanded", "true")
    expect(opened(panel).locator("[data-opened=window]")).to_be_visible()


def test_a_reload_lands_on_the_piece_that_was_open(seeded, page):
    panel = open_page(page)
    limit(panel, FIVE).click()
    expect(opened(panel)).to_have_count(1)
    page.reload()
    panel = panel_of(page)
    expect(limit(panel, FIVE)).to_have_attribute("aria-expanded", "true")
    expect(opened(panel).locator("[data-opened=window]")).to_be_visible()
    panel.locator("[data-group=cost]").get_by_role("link", name="Prices").click()
    expect(price_table(panel)).to_be_visible()
    page.reload()
    panel = panel_of(page)
    expect(price_table(panel)).to_be_visible()
    assert path_of(page) == "/m/usage/prices"


def test_a_link_inside_a_section_opens_its_piece_in_place(seeded, page):
    panel = open_page(page, "sessions")
    entries = page.evaluate("history.length")
    session_row(panel, FIRST).get_by_role("link", name="Open").click()
    expect(page).to_have_url(f"{seeded.url}/m/usage/sessions/claude/{FIRST}")
    panel.locator("[data-line=subagents]").get_by_role("button", name="Show").click()
    row = panel.locator(f'[data-part=agents] moxi-ui-row[data-agent="{AGENT}"]')
    row.get_by_role("link", name="Open").click()
    expect(page).to_have_url(f"{seeded.url}/m/usage/sessions/claude/{FIRST}/{AGENT}")
    expect(row.locator("[data-part=agent] svg[data-part=session-chart] rect.bar")).to_have_count(2)
    # The agent's act closes it back to the session, and the session's to the section.
    row.get_by_role("link", name="Close").click()
    expect(page).to_have_url(f"{seeded.url}/m/usage/sessions/claude/{FIRST}")
    session_row(panel, FIRST).locator(":scope > a[data-action=open]").click()
    expect(page).to_have_url(f"{seeded.url}/m/usage/sessions")
    assert page.evaluate("history.length") == entries


# --- the range, the filters and the measure ------------------------------------------------

def range_button(panel):
    return panel.locator("[data-part=filters] [data-filter=range] button.pick")


def settled(panel) -> None:
    """Waits until the filter line holds its three filters, Range, the place and Model
    (one harness gives no Harness filter): a filter is absent until its list answers,
    and a menu opened meanwhile closes when the line is drawn again."""
    expect(panel.locator("[data-part=filters] [data-filter]")).to_have_count(3)


def set_filters(panel) -> None:
    settled(panel)
    range_button(panel).click()
    panel.locator("[data-menu=range]").get_by_role("menuitemradio", name=re.compile("^Last 7 days")).click()
    expect(range_button(panel)).to_have_text("last 7 days▾")
    settled(panel)
    model = panel.locator("[data-part=filters] [data-filter=model]")
    model.locator("button.pick").click()
    model.get_by_role("menuitemradio", name=MODEL).click()
    expect(model.locator("button.pick")).to_have_text(f"{MODEL}▾")
    panel.locator("[data-part=measure]").get_by_role("button", name="Tokens").click()


def expect_filters_set(panel) -> None:
    expect(range_button(panel)).to_have_text("last 7 days▾")
    expect(panel.locator("[data-part=filters] [data-filter=model] button.pick")).to_have_text(f"{MODEL}▾")
    expect(panel.locator("[data-part=measure]").get_by_role("button", name="Tokens")).to_have_attribute(
        "aria-pressed", "true")


def test_the_range_and_filters_are_kept_across_a_new_placement_and_stay_out_of_the_address(seeded, page):
    panel = open_page(page, "sessions")
    expect(range_button(panel)).to_have_text("last 30 days▾")
    set_filters(panel)
    # They are not part of the address, which still names only the open piece.
    assert path_of(page) == "/m/usage/sessions"
    with page.expect_request(lambda request: "/m/usage/api/sessions?" in request.url) as sent:
        page.locator("#moxi-pages-button").click()
        page.locator('#moxi-pages a[data-module="ui"][data-route="modules"]').click()
        expect(page).to_have_url(f"{seeded.url}/m/ui/modules")
        expect(panel_of(page)).to_have_count(0)
        page.go_back()
    assert f"model={MODEL}" in sent.value.url and "since=" in sent.value.url, sent.value.url
    # A new element was placed at the same address, and reads under the kept choices.
    expect(page).to_have_url(f"{seeded.url}/m/usage/sessions")
    panel = panel_of(page)
    expect_filters_set(panel)
    expect(panel.locator("section[data-group=sessions] moxi-ui-row")).to_have_count(1)
    # Another address of the page keeps them too.
    page.evaluate("async () => (await import('/m/ui/client.js')).navigate('usage', 'models')")
    panel = panel_of(page)
    expect(panel.locator("section[data-group=models] [data-part=rows]")).to_be_visible()
    expect_filters_set(panel)
    assert path_of(page) == "/m/usage/models"


def test_a_new_document_opens_on_the_last_thirty_days_with_no_filter(seeded, page):
    panel = open_page(page, "sessions")
    set_filters(panel)
    page.reload()
    panel = panel_of(page)
    expect(panel.locator("section[data-group=sessions] moxi-ui-row")).to_have_count(2)
    expect(range_button(panel)).to_have_text("last 30 days▾")
    expect(panel.locator("[data-part=filters] [data-filter=model] button.pick")).to_have_text("all▾")
    expect(panel.locator("[data-part=measure]").get_by_role("button", name="Cost")).to_have_attribute(
        "aria-pressed", "true")
    assert path_of(page) == "/m/usage/sessions"


# --- the opened limit row ------------------------------------------------------------------

def test_an_opened_row_says_its_window_its_readings_and_who_used_it(seeded, page):
    panel = open_page(page)
    row = limit(panel, FIVE)
    expect(row.locator("[data-part=close]")).to_have_count(0)
    row.click()
    expect(row).to_have_class(re.compile(r"\bopen\b"))
    expect(row).to_have_attribute("href", "/m/usage/")
    expect(row.locator("[data-part=close]")).to_have_text("Close")
    lines = opened(panel)
    expect(lines.locator("[data-opened]")).to_have_count(3)
    window = lines.locator("[data-opened=window]")
    expect(window.locator(".k")).to_have_text("Window")
    expect(window.locator(".f")).to_have_text(re.compile(
        rf"^opened {WHEN}, resets {WHEN} · (39|4\d)% of it gone, 48% used$"))
    readings = lines.locator("[data-opened=readings]")
    expect(readings.locator(".k")).to_have_text("Readings")
    # The first reading of the period and the row's own, then the session it came from.
    expect(readings.locator(".f")).to_have_text(
        re.compile(rf"^20% at {WHEN}, 48% at {WHEN}{LAST_FROM}$"))
    used = lines.locator("[data-opened=used]")
    expect(used.locator(".k")).to_have_text("Used by")
    # The one session since the window opened, by its folder and start.
    expect(used.locator("[data-part=used-row]")).to_have_text(
        [re.compile(rf"^\s*proj-a · started {WHEN} · \d+ requests\s*(at least )?\$\d+\.\d\d$")])
    total = used.locator("[data-part=used-total]")
    expect(total.locator("span").first).to_have_text(re.compile(rf"^1 session, \d requests since {WHEN}$"))
    expect(total.locator("span").last).to_have_text(re.compile(r"^(at least )?\$\d+\.\d\d$"))
    # Nothing of the row shows a session's id.
    assert FIRST not in panel.locator("div[data-part=top]").inner_text()
    # The lines are dim or in the text colour, and sit between the row and the next one.
    dim = panel.locator("[data-group=cost] .gk").evaluate("el => getComputedStyle(el).color")
    assert window.locator(".k").evaluate("el => getComputedStyle(el).color") == dim
    assert lines.bounding_box()["y"] >= row.bounding_box()["y"] + row.bounding_box()["height"] - 1
    assert limit(panel, WEEK).bounding_box()["y"] >= lines.bounding_box()["y"] + lines.bounding_box()["height"] - 1


def test_a_window_whose_start_is_not_known_says_its_reset_and_no_who(seeded, page):
    panel = open_page(page, "limit/claude/plan/10080")
    lines = opened(panel)
    expect(lines.locator("[data-opened=window] .f")).to_have_text(re.compile(rf"^resets {WHEN} · 0% used$"))
    expect(lines.locator("[data-opened=readings] .f")).to_have_text(re.compile(rf"^0% at {WHEN}{LAST_FROM}$"))
    expect(lines.locator("[data-opened=used]")).to_have_count(0)
    # Why the row has no fill time is one dim line under the others.
    expect(lines.locator("[data-opened=note]")).to_have_text([re.compile(r"^No fill time: .+")])


def test_a_scoped_limit_says_its_window_and_one_reading_and_not_who_used_it(seeded, page):
    panel = open_page(page, "limit/claude/opus/10080")
    lines = opened(panel)
    expect(lines.locator("[data-opened=window] .f")).to_have_text(re.compile(
        rf"^opened {WHEN}, resets {WHEN} · \d+% of it gone, 33% used$"))
    expect(lines.locator("[data-opened=readings] .f")).to_have_text(re.compile(rf"^33% at {WHEN}{LAST_FROM}$"))
    expect(lines.locator("[data-opened=used]")).to_have_count(0)


def test_a_line_whose_fact_the_answer_does_not_carry_is_absent(seeded, page):
    def bare(route):
        answer = route.fetch()
        body = answer.json()
        body.update(opened=None, readings=None, used=None)
        route.fulfill(response=answer, json=body)
    page.route("**/m/usage/api/window?*", bare)
    panel = open_page(page, "limit/claude/opus/10080")
    lines = opened(panel)
    expect(lines.locator("[data-opened=window] .f")).to_have_text(re.compile(rf"^resets {WHEN} · 33% used$"))
    # Besides the row's own dim lines, only the window's line is left.
    assert lines.locator("[data-opened]:not([data-opened=note])").evaluate_all(
        "nodes => nodes.map((node) => node.dataset.opened)") == ["window"]


def test_an_opened_row_says_it_is_reading_until_its_read_answers(seeded, page):
    held, let_through = [], []
    page.route("**/m/usage/api/window?*",
               lambda route: route.continue_() if let_through else held.append(route))
    panel = open_page(page)
    limit(panel, FIVE).click()
    lines = opened(panel)
    expect(lines.locator("[data-opened=reading]")).to_have_text("Reading…")
    expect(lines.locator("[data-opened=window]")).to_have_count(0)
    expect(limit(panel, FIVE).locator("[data-part=close]")).to_have_text("Close")
    assert held, "the window was not asked for"
    # A read asked after these goes through, so the row is not left on one held.
    let_through.append(True)
    for route in held:
        route.continue_()
    expect(lines.locator("[data-opened=window]")).to_be_visible()
    expect(lines.locator("[data-opened=reading]")).to_have_count(0)


def test_a_window_read_that_fails_keeps_the_row_open_and_says_so_at_the_foot(seeded, page):
    page.route("**/m/usage/api/window?*", lambda route: route.fulfill(
        status=500, content_type="application/json", body="{}"))
    panel = open_page(page, "limit/claude/plan/300")
    expect(lines_of(panel)).to_have_text(["The opened limit can't be read: usage's page server answered 500."])
    expect(limit(panel, FIVE)).to_have_attribute("aria-expanded", "true")
    expect(opened(panel).locator("[data-opened=window], [data-opened=reading]")).to_have_count(0)
    assert path_of(page) == "/m/usage/limit/claude/plan/300"


def test_a_window_read_that_is_refused_closes_the_row_and_says_why_at_the_foot(seeded, page):
    page.route("**/m/usage/api/window?*", lambda route: route.fulfill(
        status=422, content_type="application/json",
        body=json.dumps({"refused": "there is no fresh reading of this window"})))
    panel = open_page(page, "limit/claude/plan/300")
    expect(page).to_have_url(f"{seeded.url}/m/usage/")
    expect(limit(panel, FIVE)).to_have_attribute("aria-expanded", "false")
    expect(opened(panel)).to_have_count(0)
    expect(lines_of(panel)).to_have_text([
        "The opened limit can't be read: there is no fresh reading of this window."])
    # The line outlives a new placement of the page, and goes when another piece opens.
    page.unroute("**/m/usage/api/window?*")
    limit(panel, OPUS).click()
    expect(opened(panel).locator("[data-opened=window]")).to_be_visible()
    expect(lines_of(panel)).to_have_count(0)


def test_the_window_is_read_again_when_the_tab_shows_again(seeded, page):
    panel = open_page(page, "limit/claude/plan/300")
    expect(opened(panel).locator("[data-opened=window]")).to_be_visible()
    with page.expect_request(lambda request: "/m/usage/api/window?" in request.url):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


# --- a phone's width -----------------------------------------------------------------------

def test_an_opened_limit_row_fits_a_phone(seeded, page, ui_width_check):
    ui_width_check(page, module="usage", route="limit/claude/plan/300")
    panel = panel_of(page)
    lines = opened(panel)
    expect(lines.locator("[data-opened=used] [data-part=used-total]")).to_be_visible()
    expect(limit(panel, FIVE).locator("[data-part=close]")).to_be_visible()
    ui_width_check(page)
    # The lines keep to the page's width and wrap inside it.
    box = lines.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 360


@pytest.mark.parametrize("route", ["sessions", f"sessions/claude/{FIRST}"], ids=["sessions", "session"])
def test_a_sections_address_fits_a_phone(seeded, page, ui_width_check, route):
    ui_width_check(page, module="usage", route=route)
    expect(session_row(panel_of(page), FIRST)).to_be_visible()
    if route != "sessions":
        expect(panel_of(page).locator("[data-part=session] [data-line=subagents]")).to_be_visible()
    ui_width_check(page)


def test_the_prices_address_fits_a_phone(seeded, page, ui_width_check):
    ui_width_check(page, module="usage", route="prices")
    expect(price_table(panel_of(page))).to_be_visible()
    ui_width_check(page)

