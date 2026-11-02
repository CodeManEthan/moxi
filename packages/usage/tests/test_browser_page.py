"""usage's page in a browser, through a real shell that starts usage's real page server:
the contract check on the page and its two blocks (the cost block saying how old the index
is), each view drawn over a small index and fitting a phone, the daily view's figures by
token class, a session opened under its row's harness, the strip with no index, a read over a stale index that runs no pass, and "Read new sessions" bringing
new sessions in.

The transcripts are written, and the index built with `python -m moxi usage index`, in
the shell's own environment, so they sit under the shell's home where its page servers
read them. A test that changes what the index holds starts a shell of its own, so the
tests hold under pytest-xdist in any order.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="usage's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

MODEL = "claude-sonnet-4-5"
#: Two sessions in two projects; the first has a subagent.
FIRST = "0190b000-0000-7000-8000-0000000000e1"
SECOND = "0190b000-0000-7000-8000-0000000000e2"
LATER = "0190b000-0000-7000-8000-0000000000e3"
AGENT = "a0190b00000000e1"
SESSIONS = [
    {"session": FIRST, "project": "alpha", "hours_ago": 2, "agent": AGENT},
    {"session": SECOND, "project": "beta", "hours_ago": 30, "agent": None},
]

#: Writes Claude transcripts under the harness root of the environment it runs in: each
#: entry of the JSON list in argv[1] is one session of three requests, with an optional
#: subagent of two.
SEED = """
import json, sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for n, spec in enumerate(json.loads(sys.argv[1])):
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=spec["session"],
                           project="-work-" + spec["project"],
                           cwd="/work/" + spec["project"],
                           start=now - spec["hours_ago"] * 3600)
    for m in range(3):
        t.user("Fix the failing test.")
        t.assistant("Done.", usage=T.usage(input=200, output=1000 + m * 500),
                    request_id=f"req_{spec['session'][-2:]}{m}", model=sys.argv[2])
    if spec["agent"]:
        child = t.subagent(spec["agent"])
        for m in range(2):
            child.user("Look into it.")
            child.assistant("Found it.", usage=T.usage(input=100, output=400),
                            request_id=f"req_{spec['agent'][-4:]}{m}", model=sys.argv[2])
    t.write()
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


def run(shell, *argv: str) -> str:
    done = subprocess.run(list(argv), env=shell.env, capture_output=True, text=True,
                          timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done.stdout


def seed(shell, *sessions: dict) -> None:
    run(shell, sys.executable, "-c", SEED, json.dumps(list(sessions)), MODEL)


def build_index(shell) -> None:
    run(shell, sys.executable, "-m", "moxi", "usage", "index")


@pytest.fixture(scope="module")
def indexed(ui_shell):
    """The module's shell, its index built over the two sessions."""
    seed(ui_shell, *SESSIONS)
    build_index(ui_shell)
    return ui_shell


@pytest.fixture
def own_shell(ui_shells, ui_shell_options, ui_contexts):
    """A shell of the test's own, with no index yet, and a signed-in page on it."""
    shell = ui_shells(**ui_shell_options)
    return shell, ui_contexts(shell).pages[0]


def panel_of(page):
    return page.locator("#moxi-panel > moxi-usage-page")


def open_view(page, route: str = ""):
    page.goto(f"/m/usage/{route}")
    return panel_of(page)


def strip_of(panel):
    return panel.locator("section[data-part=strip] .strip")


def session_rows(panel):
    return panel.locator("section[data-part=view] tr[data-session]")


# --- the contract --------------------------------------------------------------------------

def test_the_page_keeps_the_contract(indexed, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "usage", "page", {"route": ""})
    assert check.element == "moxi-usage-page"
    assert any("/m/usage/api/status" in url for url in check.requests), check.requests


def test_the_limits_block_keeps_the_contract(indexed, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "usage", "limits", tokens=("accent", "warn"))
    assert check.element == "moxi-usage-limits"
    assert any("/m/usage/api/limits" in url for url in check.requests), check.requests


def test_the_cost_block_keeps_the_contract_and_says_how_old_the_index_is(
        indexed, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "usage", "cost", keep=True)
    assert check.element == "moxi-usage-cost"
    assert any("/m/usage/api/cost" in url for url in check.requests), check.requests
    age = check.locator.locator("[data-age]")
    expect(age).to_have_attribute("data-age", "fresh")
    expect(age).to_have_text(re.compile(r"^as of "))


# --- the views, at a phone's width ---------------------------------------------------------

#: Each view's route and what shows once its report has drawn.
VIEWS = {
    "": "section[data-part=view] svg g[data-period]",
    "sessions": f'section[data-part=view] tr[data-session="{FIRST}"]',
    "projects": "section[data-part=view] tr[data-project]",
    "models": f'section[data-part=view] tr[data-model="{MODEL}"]',
    f"session/{FIRST}": f'section[data-part=view] tr[data-agent="{AGENT}"]',
}


@pytest.mark.parametrize("route", list(VIEWS), ids=["daily", "sessions", "projects", "models",
                                                    "session"])
def test_each_view_draws_the_index_and_fits_a_phone(indexed, ui_page, ui_width_check, route):
    ui_width_check(ui_page, module="usage", route=route)
    panel = panel_of(ui_page)
    expect(panel.locator(VIEWS[route]).first).to_be_visible()
    expect(strip_of(panel)).to_have_attribute("data-state", "up")
    ui_width_check(ui_page)


def test_the_daily_view_gives_each_token_class_as_a_figure_and_each_segment_a_title(
        indexed, ui_page):
    panel = open_view(ui_page)
    view = panel.locator("section[data-part=view]")
    expect(view.locator("[data-part=classes] [data-class]")).to_have_count(5)
    # Two sessions of three requests (output 1000, 1500, 2000) and a subagent of two (400).
    expect(view.locator("[data-part=classes] [data-class=output] b")).to_have_text("9.80k")
    segments = view.locator("svg g[data-period] rect[data-key]")
    expect(segments.first).to_be_attached()
    assert segments.count() == view.locator("svg g[data-period] rect[data-key] > title").count()
    expect(view.locator("svg g[data-period] rect[data-key=output] > title").first).to_contain_text(
        "output: ")


def test_the_sessions_view_lists_both_and_links_to_one(indexed, ui_page):
    panel = open_view(ui_page, "sessions")
    expect(session_rows(panel)).to_have_count(2)
    session_rows(panel).locator(f'a[href="/m/usage/session/{FIRST}"]').click()
    expect(ui_page).to_have_url(f"{indexed.url}/m/usage/session/{FIRST}")
    agents = panel.locator("section[data-part=view] tr[data-agent]")
    expect(agents).to_have_count(2)
    expect(panel.locator(f'section[data-part=view] svg g[data-agent="{AGENT}"]')).to_have_count(1)


def test_a_session_opens_under_its_rows_harness_and_sums_to_the_row(indexed, ui_page):
    panel = open_view(ui_page, "sessions")
    harness = panel.locator("select[aria-label=Harness]")
    row = panel.locator(f'section[data-part=view] tr[data-session="{FIRST}"]')
    expect(row).to_be_visible()
    expect(harness).to_have_value("")
    tokens = row.locator("td.num").nth(-2).inner_text()
    with ui_page.expect_request(lambda request: "/m/usage/api/session?" in request.url) as sent:
        row.locator("a").click()
    assert "harness=claude" in sent.value.url, sent.value.url
    expect(harness).to_have_value("claude")
    totals = panel.locator("section[data-part=view] [data-part=totals]")
    expect(totals.locator("span", has_text="Tokens").locator("b")).to_have_text(tokens)


# --- the index's states --------------------------------------------------------------------

def test_with_no_index_the_strip_gives_the_command_that_builds_it(own_shell):
    _shell, page = own_shell
    panel = open_view(page)
    strip = strip_of(panel)
    expect(strip).to_have_attribute("data-state", "none")
    expect(strip.locator("code")).to_have_text("moxi usage index")
    expect(strip.get_by_role("button", name="Read new sessions")).to_have_count(0)


def test_a_read_over_a_stale_index_runs_no_pass(own_shell):
    shell, page = own_shell
    seed(shell, SESSIONS[0])
    build_index(shell)
    seed(shell, {"session": LATER, "project": "gamma", "hours_ago": 1, "agent": None})
    holder = subprocess.Popen([sys.executable, "-c", HOLD], env=shell.env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        no_wait = subprocess.run([sys.executable, "-m", "moxi", "usage", "index", "--no-wait"],
                                 env=shell.env, capture_output=True, text=True, timeout=60)
        assert no_wait.returncode == 75, no_wait.stderr     # the lock a pass takes is held
        http = shell.session()
        assert http.get("/m/usage/api/status")[0] == 200     # starts the page server
        started = time.monotonic()
        status, body = http.get("/m/usage/api/sessions")
        took = time.monotonic() - started
        # A read that ran a pass first would wait on the held lock for 30 s.
        assert status == 200 and took < 5, (status, took)
        assert [row["session_id"] for row in body["rows"]] == [FIRST]
        assert body["index"]["passed"] is False
        panel = open_view(page, "sessions")
        expect(strip_of(panel)).to_have_attribute("data-state", "up")
        expect(session_rows(panel)).to_have_count(1)
    finally:
        holder.stdin.close()
        holder.wait(timeout=30)


def test_read_new_sessions_moves_the_last_pass_and_brings_them_in(own_shell):
    shell, page = own_shell
    seed(shell, SESSIONS[0])
    build_index(shell)
    seed(shell, {"session": LATER, "project": "gamma", "hours_ago": 1, "agent": None})
    panel = open_view(page, "sessions")
    strip = strip_of(panel)
    expect(strip).to_have_attribute("data-state", "up")
    expect(session_rows(panel)).to_have_count(1)
    last = strip.locator(".last")
    before = last.get_attribute("data-last-pass")
    assert before
    strip.get_by_role("button", name="Read new sessions").click()
    expect(last).not_to_have_attribute("data-last-pass", before)
    expect(strip).to_have_attribute("data-state", "up")
    expect(session_rows(panel)).to_have_count(2)
    expect(panel.locator(f'section[data-part=view] tr[data-session="{LATER}"]')).to_have_count(1)
