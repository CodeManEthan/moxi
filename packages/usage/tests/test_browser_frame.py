"""The frame of usage's page in a browser, through a real shell that starts usage's real
page server: the limit rows and the warning line from a seeded readings store, the Cost
rows, the foot and its lines, each state the page has (reading, no index, an index with no
request, a pass running), and the frame at a phone's width.

The transcripts and the readings are written, and the index built with `python -m moxi
usage index`, in the shell's own environment, so they sit under the shell's home where
its page servers read them. A test that changes what the index holds starts a shell of
its own, so the tests hold under pytest-xdist in any order.
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
FIRST = "0190b000-0000-7000-8000-0000000000f1"
SECOND = "0190b000-0000-7000-8000-0000000000f2"
LATER = "0190b000-0000-7000-8000-0000000000f3"
#: Two sessions of three requests each, in two projects, both inside the last seven days.
SESSIONS = [
    {"session": FIRST, "project": "proj-a", "hours_ago": 30},
    {"session": SECOND, "project": "proj-b", "hours_ago": 54},
]

#: Writes Claude transcripts under the harness root of the environment it runs in: each
#: entry of the JSON list in argv[1] is one session of three requests.
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
                    request_id=f"req_{spec['session'][-2:]}{m}", model=sys.argv[2])
    t.write()
"""

#: Stores plan readings where the environment it runs in keeps them. Codex's 5-hour
#: window resets in two hours and its three readings rise a percent a minute to 79%, so
#: it fills before it resets; Codex's weekly window and Claude's 5-hour window have one
#: reading each; Claude's weekly reading is of a period that ended an hour ago.
READINGS = """
import time
from moxi_usage import readings
from moxi_usage.readings import Reading
now = time.time()
found = [
    Reading("codex", "codex", 300, "primary", used, now + 7200, now - ago, "rollout", "thread-1")
    for used, ago in ((60.0, 1200), (70.0, 600), (79.0, 60))
] + [
    Reading("codex", "codex", 10080, "secondary", 41.0, now + 3 * 86400, now - 60, "rollout", "thread-1"),
    Reading("claude", "plan", 300, "five_hour", 48.6, now + 3 * 3600, now - 240, "session-usage", "s1"),
    Reading("claude", "plan", 10080, "seven_day", 60.0, now - 3600, now - 2 * 86400, "session-usage", "s1"),
]
conn = readings.open_store()
try:
    readings.store(conn, found, now=now, days=7)
finally:
    conn.close()
"""

#: Holds the index's lock, as a pass in another process would, until stdin closes.
HOLD = """
import sys
import moxi.files
from moxi_usage import store
with moxi.files.lock(store.lock_path()):
    print("held", flush=True)
    sys.stdin.readline()
"""

TIME = r"\d{1,2}:\d\d(?: [AP]M)?"
WHAT_USAGE_DOES = ("Usage adds up what your sessions cost from the transcripts on this "
                   "machine, and shows your plan limits once a session has reported them.")


def run(shell, *argv: str) -> str:
    done = subprocess.run(list(argv), env=shell.env, capture_output=True, text=True,
                          timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done.stdout


def seed(shell, *sessions: dict) -> None:
    run(shell, sys.executable, "-c", SEED, json.dumps(list(sessions)), MODEL)


def seed_readings(shell) -> None:
    run(shell, sys.executable, "-c", READINGS)


def build_index(shell) -> None:
    run(shell, sys.executable, "-m", "moxi", "usage", "index")


@pytest.fixture(scope="module")
def seeded(ui_shells, ui_shell_options):
    """A shell of the module's own: two sessions indexed, and the plan readings stored."""
    shell = ui_shells(**ui_shell_options)
    seed(shell, *SESSIONS)
    build_index(shell)
    seed_readings(shell)
    return shell


@pytest.fixture
def seeded_page(seeded, ui_contexts):
    return ui_contexts(seeded).pages[0]


@pytest.fixture
def own_shell(ui_shells, ui_shell_options, ui_contexts):
    """A shell of the test's own, with no index yet, and a signed-in page on it."""
    shell = ui_shells(**ui_shell_options)
    return shell, ui_contexts(shell).pages[0]


def open_page(page, route: str = ""):
    page.goto(f"/m/usage/{route}")
    return page.locator("#moxi-panel > moxi-usage-page")


def limit(panel, key: str):
    return panel.locator(f'[data-group=limits] [data-limit="{key}"]')


def foot_of(panel):
    return panel.locator("[data-part=foot]")


def lines_of(panel):
    return panel.locator("[data-part=lines] p")


# --- the contract --------------------------------------------------------------------------

def test_the_frame_keeps_the_contract_in_text_tokens(seeded, seeded_page, ui_component_check):
    check = ui_component_check(seeded_page, "usage", "page", {"route": ""},
                               tokens=("text", "text-dim"), keep=True)
    assert check.element == "moxi-usage-page"
    for read in ("limits", "status", "daily?"):
        assert any(f"/m/usage/api/{read}" in url for url in check.requests), check.requests
    expect(check.locator.locator("[data-group=cost]")).to_be_visible()
    # The frame has no heading of its own: the shell's top line names the page.
    assert check.locator.evaluate("el => el.shadowRoot.querySelectorAll('h1, h2, h3').length") == 0


# --- limits --------------------------------------------------------------------------------

def test_the_limits_lead_the_page_with_the_group_that_fills_soonest_first(seeded, seeded_page):
    panel = open_page(seeded_page)
    groups = panel.locator("[data-group=limits]")
    expect(groups).to_have_count(2)
    expect(groups.nth(0)).to_have_attribute("data-harness", "codex")
    expect(groups.nth(1)).to_have_attribute("data-harness", "claude")
    expect(groups.nth(0).locator(".gk")).to_have_text(re.compile(rf"^Codex\s*read {TIME}$"))
    expect(groups.nth(1).locator(".gk")).to_have_text(re.compile(rf"^Claude\s*read {TIME}$"))
    # The limits come before Cost, and nothing comes before them but the warning line.
    order = panel.evaluate("""el => [...el.shadowRoot.children]
        .filter((node) => node.dataset && node.dataset.part).map((node) => node.dataset.part)""")
    assert order[:2] == ["warning", "top"], order
    top = panel.locator("[data-part=top] > section")
    assert top.evaluate_all("nodes => nodes.map((node) => node.dataset.group)") == [
        "limits", "limits", "cost"]


def test_a_limit_row_says_its_percent_its_line_and_when_it_fills_and_resets(seeded, seeded_page):
    panel = open_page(seeded_page)
    five = limit(panel, "codex/codex/300")
    expect(five.locator("span").first).to_have_text("5-hour")
    expect(five.locator(".u")).to_have_text("79% used")
    expect(five.locator(".w b")).to_have_text(re.compile(rf"^fills {TIME}$"))
    expect(five.locator(".w")).to_have_text(re.compile(rf"^fills {TIME}, resets {TIME}$"))
    expect(five.locator("svg.m")).to_have_attribute(
        "aria-label", re.compile(r"^79% used, (5\d|60)% of the window gone$"))
    fill = float(five.locator("svg.m rect.fill").get_attribute("width"))
    assert fill == pytest.approx(96 * 0.79)
    expect(five.locator("svg.m rect.tick")).to_have_count(1)
    # The row that fills early is first in its group.
    rows = panel.locator('[data-group=limits][data-harness=codex] [data-limit]')
    assert rows.evaluate_all("rows => rows.map((row) => row.dataset.limit)") == [
        "codex/codex/300", "codex/codex/10080"]
    week = limit(panel, "codex/codex/10080")
    expect(week.locator(".u")).to_have_text("41% used")
    expect(week.locator(".w b")).to_have_count(0)
    expect(week.locator(".w")).to_have_text(re.compile(rf"^resets \w+ {TIME}$"))
    # A percent is rounded down, and a row read at another minute says when.
    claude = limit(panel, "claude/plan/300")
    expect(claude.locator(".u")).to_have_text("48% used")
    # A row that opens is a link to its address and has no word of its own; nothing is open.
    expect(five).to_have_attribute("href", "/m/usage/limit/codex/codex/300")
    expect(five).to_have_attribute("aria-expanded", "false")
    assert panel.locator("[data-group=limits] button, [data-group=limits] [data-part=close]").count() == 0
    expect(panel.locator("[data-part=opened]")).to_have_count(0)


def test_a_reading_of_an_ended_period_is_one_dim_row_in_words(seeded, seeded_page):
    panel = open_page(seeded_page)
    row = limit(panel, "claude/plan/10080")
    expect(row).to_have_attribute("data-state", "reset")
    expect(row).to_have_text(re.compile(rf"^Weekly\s*·\s*reset {TIME}$"))
    expect(row.locator("svg")).to_have_count(0)
    dim = panel.locator("[data-group=limits] .gk").first.evaluate("el => getComputedStyle(el).color")
    assert row.evaluate("el => getComputedStyle(el).color") == dim


def test_the_warning_line_names_the_limit_that_fills_before_it_resets(seeded, seeded_page):
    panel = open_page(seeded_page)
    warning = panel.locator("[data-part=warning]")
    expect(warning.locator("b")).to_have_text(
        re.compile(rf"^Codex's 5-hour limit fills at {TIME}$"))
    expect(warning).to_have_text(re.compile(
        rf"^Codex's 5-hour limit fills at {TIME}, an hour before it resets$"))
    weight = warning.locator("b").evaluate("el => getComputedStyle(el).fontWeight")
    assert weight == "600"


# --- Cost ----------------------------------------------------------------------------------

def test_cost_gives_today_seven_days_and_thirty_each_with_the_period_before(seeded, seeded_page):
    panel = open_page(seeded_page)
    cost = panel.locator("[data-group=cost]")
    expect(cost.locator(".gk")).to_have_text("Cost")
    rows = cost.locator("[data-cost]")
    assert rows.evaluate_all("rows => rows.map((row) => row.dataset.cost)") == ["today", "7d", "30d"]
    expect(rows.locator(".pk")).to_have_text(["Today", "7 days", "30 days"])
    expect(rows.nth(0).locator(".fig")).to_have_text(re.compile(r"^\$\d+\.\d\d over \d requests?$"))
    expect(rows.nth(0).locator(".t")).to_have_text(re.compile(r"^yesterday \$\d+\.\d\d$"))
    # Both sessions, three requests each, are inside the last seven days.
    expect(rows.nth(1).locator(".fig")).to_have_text(re.compile(r"^\$0\.\d\d over 6 requests$"))
    expect(rows.nth(1).locator(".t")).to_have_text("the week before $0.00")
    expect(rows.nth(2).locator(".fig")).to_have_text(re.compile(r"^\$0\.\d\d over 6 requests$"))
    expect(rows.nth(2).locator(".t")).to_have_text("the 30 days before $0.00")
    caption = cost.locator("[data-part=caption]")
    expect(caption).to_contain_text("Estimated from a local price table. Not billing data.")
    expect(caption.get_by_role("link", name="Prices")).to_be_visible()
    expect(caption.locator("[data-part=read]")).to_have_text(re.compile(rf"^sessions read {TIME}$"))


# --- the foot and its lines ----------------------------------------------------------------

def test_the_foot_says_when_the_sessions_were_read_and_the_files_by_harness(seeded, seeded_page):
    panel = open_page(seeded_page)
    foot = foot_of(panel)
    expect(foot).to_have_attribute("data-state", "up")
    expect(foot.locator(".last")).to_have_text(
        re.compile(rf"^Sessions read {TIME} · 2 files: Claude 2$"))
    expect(foot.get_by_role("button", name="Read new sessions")).to_be_visible()
    # The foot closes the page: the chart and the views the page had before sit between
    # Cost and it.
    order = panel.evaluate("""el => [...el.shadowRoot.children].map((node) =>
        node.dataset && node.dataset.part || node.localName)""")
    assert order[-5:] == ["top", "filters", "below", "moxi-usage-legacy", "foot"], order
    expect(lines_of(panel)).to_have_count(0)


def test_a_read_that_fails_and_an_unknown_address_each_leave_a_dim_line(seeded, seeded_page):
    seeded_page.route("**/m/usage/api/limits", lambda route: route.fulfill(
        status=500, content_type="application/json", body="{}"))
    panel = open_page(seeded_page, "nowhere")
    expect(lines_of(panel)).to_have_text([
        "The limits can't be read: usage's page server answered 500.",
        "There is no such view here."])
    expect(panel.locator("[data-group=limits]")).to_have_count(0)
    expect(panel.locator("[data-group=cost]")).to_be_visible()
    dim = panel.locator("[data-group=cost] .gk").evaluate("el => getComputedStyle(el).color")
    assert lines_of(panel).first.evaluate("el => getComputedStyle(el).color") == dim


# --- the states ----------------------------------------------------------------------------

def test_reading_shows_until_the_first_read_answers_then_goes(seeded, seeded_page):
    held = []
    seeded_page.route(re.compile(r"/m/usage/api/(limits|status|daily)"),
                      lambda route: held.append(route))
    panel = open_page(seeded_page)
    reading = panel.locator("[data-part=reading]")
    expect(reading).to_have_text("Reading…")
    assert panel.evaluate("el => el.shadowRoot.children.length") == 1
    assert len(held) == 3, [route.request.url for route in held]
    for route in list(held):
        route.continue_()
    seeded_page.unroute_all(behavior="wait")
    expect(reading).to_have_count(0)
    expect(panel.locator("[data-group=cost]")).to_be_visible()


def test_with_no_index_the_block_gives_the_command_to_copy(own_shell):
    shell, page = own_shell
    seed_readings(shell)
    panel = open_page(page)
    block = panel.locator("[data-part=nothing]")
    expect(block).to_have_attribute("data-state", "none")
    expect(block.locator(".quietline")).to_have_text("Nothing counted yet")
    expect(block.locator(".first p").first).to_have_text(WHAT_USAGE_DOES)
    expect(block.locator("code")).to_have_text("moxi usage index")
    expect(block.get_by_role("button", name="Copy")).to_be_visible()
    expect(panel.get_by_role("button", name="Read new sessions")).to_have_count(0)
    # The limits still lead; Cost, the views under it and the foot are absent.
    expect(panel.locator("[data-group=limits]")).to_have_count(2)
    for gone in ("[data-group=cost]", "moxi-usage-legacy", "[data-part=foot]", "[data-part=lines]"):
        expect(panel.locator(gone)).to_have_count(0)


def test_an_index_with_no_request_says_nothing_is_counted_and_reads_new_sessions(own_shell):
    shell, page = own_shell
    build_index(shell)
    panel = open_page(page)
    block = panel.locator("[data-part=nothing]")
    expect(block).to_have_attribute("data-state", "empty")
    expect(block.locator(".quietline")).to_have_text("Nothing counted yet")
    expect(block.locator(".first p").first).to_have_text(re.compile(
        "^" + re.escape(WHAT_USAGE_DOES)
        + rf" It found no sessions when it last looked, at {TIME}\.$"))
    for gone in ("[data-group=limits]", "[data-group=cost]", "moxi-usage-legacy",
                 "[data-part=foot]", "code"):
        expect(panel.locator(gone)).to_have_count(0)
    seed(shell, SESSIONS[0])
    block.get_by_role("button", name="Read new sessions").click()
    expect(panel.locator("[data-group=cost]")).to_be_visible()
    expect(block).to_have_count(0)
    expect(foot_of(panel).locator(".last")).to_have_text(
        re.compile(rf"^Sessions read {TIME} · 1 file: Claude 1$"))


def test_while_a_pass_runs_the_foot_leads_with_it_and_the_act_is_absent(own_shell):
    shell, page = own_shell
    seed(shell, SESSIONS[0])
    build_index(shell)
    seed(shell, {"session": LATER, "project": "proj-b", "hours_ago": 1})
    panel = open_page(page)
    foot = foot_of(panel)
    expect(foot).to_have_attribute("data-state", "up")
    holder = subprocess.Popen([sys.executable, "-c", HOLD], env=shell.env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        foot.get_by_role("button", name="Read new sessions").click()
        # The pass waits on the held lock, as it would on a pass in another process.
        expect(foot).to_have_attribute("data-state", "running")
        expect(foot.locator(".last")).to_have_text(
            re.compile(r"^Reading new sessions… · 1 file: Claude 1$"))
        expect(panel.get_by_role("button", name="Read new sessions")).to_have_count(0)
    finally:
        holder.stdin.close()
        holder.wait(timeout=30)
    expect(foot).to_have_attribute("data-state", "up", timeout=30_000)
    expect(foot.locator(".last")).to_have_text(
        re.compile(rf"^Sessions read {TIME} · 2 files: Claude 2$"))
    expect(foot.get_by_role("button", name="Read new sessions")).to_be_visible()


# --- a phone's width -----------------------------------------------------------------------

def test_the_frame_fits_a_phone(seeded, seeded_page, ui_width_check):
    ui_width_check(seeded_page, module="usage", route="")
    panel = seeded_page.locator("#moxi-panel > moxi-usage-page")
    expect(panel.locator("[data-part=warning]")).to_be_visible()
    expect(limit(panel, "codex/codex/300").locator(".w")).to_be_visible()
    expect(panel.locator("[data-group=cost] [data-part=read]")).to_be_visible()
    expect(foot_of(panel).get_by_role("button", name="Read new sessions")).to_be_visible()
    ui_width_check(seeded_page)
    # The meter keeps its length beside the percent, and the times take a line of their own.
    five = limit(panel, "codex/codex/300")
    meter = five.locator("svg.m").bounding_box()
    when = five.locator(".w").bounding_box()
    assert meter["width"] == pytest.approx(96, abs=1)
    assert when["y"] >= meter["y"] + meter["height"] - 1


def test_the_no_index_block_fits_a_phone(own_shell, ui_width_check):
    _shell, page = own_shell
    ui_width_check(page, module="usage", route="")
    panel = page.locator("#moxi-panel > moxi-usage-page")
    expect(panel.locator("[data-part=nothing] code")).to_be_visible()
    ui_width_check(page)
