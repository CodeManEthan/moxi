"""usage's page and its two blocks in a browser, through a real shell that starts usage's
real page server: the contract check on the page and its two blocks (the cost block saying
how old the index is), the chart, the Sessions section and an opened session drawn over a
small index and fitting a phone, a session opened under its row's harness with its
subagents shown, the command with no index, a read over a stale index that runs no pass,
and "Read new sessions" bringing new sessions in. Over a session of two turns with a
compaction and a dated `[[model]]` row: the opened session's lines, its chart with its
turns, compaction, context and each request's readout, its table, its bill, its restart
point and its reads still out, its subagent opened with its own requests and table,
and the Token kinds and Prices sections: the classes with a tier's part and what caching saved,
the price table, `Check against a bill` with its Total against its group lines, `Details`
and `Find a model`, the two tables keeping every column on a phone, scrolling in their
wrappers with the Model column in view.

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
    """The frame's foot: when the sessions were last read, and "Read new sessions"."""
    return panel.locator("[data-part=foot]")


def session_rows(panel):
    return panel.locator("section[data-group=sessions] moxi-ui-row")


def session_row(panel, session: str):
    """A row of the Sessions section, by the harness and session it opens."""
    return panel.locator(f'section[data-group=sessions] moxi-ui-row[data-key="claude/{session}"]')


def show_subagents(opened):
    """Presses `Show` at the end of the opened session's `Subagents` line; gives the rows."""
    opened.locator("[data-line=subagents]").get_by_role("button", name="Show").click()
    return opened.locator("[data-part=agents] moxi-ui-row[data-agent]")


# --- the contract --------------------------------------------------------------------------

def test_the_page_keeps_the_contract(indexed, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "usage", "page", {"route": ""}, tokens=("text",))
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


# --- the page's pieces, at a phone's width -------------------------------------------------

#: Each address and what shows once its reads have drawn.
VIEWS = {
    "": "[data-group=chart] svg g[data-period]",
    "sessions": f'section[data-group=sessions] moxi-ui-row[data-key="claude/{FIRST}"]',
    f"sessions/claude/{FIRST}": "[data-part=session] [data-line=subagents]",
    f"sessions/claude/{FIRST}/{AGENT}": "[data-part=agent] svg[data-part=session-chart] rect.bar",
}


@pytest.mark.parametrize("route", list(VIEWS), ids=["daily", "sessions", "session", "subagent"])
def test_each_address_draws_the_index_and_fits_a_phone(indexed, ui_page, ui_width_check, route):
    ui_width_check(ui_page, module="usage", route=route)
    panel = panel_of(ui_page)
    expect(panel.locator(VIEWS[route]).first).to_be_visible()
    expect(strip_of(panel)).to_have_attribute("data-state", "up")
    ui_width_check(ui_page)


def test_an_opened_session_shows_its_subagents_as_rows_under_a_map_of_its_agents(indexed, ui_page):
    panel = open_view(ui_page, "sessions")
    expect(session_rows(panel)).to_have_count(2)
    session_row(panel, FIRST).get_by_role("link", name="Open").click()
    expect(ui_page).to_have_url(f"{indexed.url}/m/usage/sessions/claude/{FIRST}")
    opened = session_row(panel, FIRST).locator("[data-part=session]")
    rows = show_subagents(opened)
    # A row a subagent, main left out; the map holds every agent, main among them.
    expect(rows).to_have_count(1)
    row = rows.first
    expect(row).to_have_attribute("name", AGENT)
    expect(row).to_have_attribute("where", "subagent")
    expect(row).to_have_attribute("fact", re.compile(r"^\$\d+\.\d\d$"))
    expect(row).to_have_attribute("lead", MODEL)
    expect(row).to_have_attribute("lead-more", re.compile(r"^first "))
    expect(row).to_have_attribute("rest", re.compile(r"^2 requests, [\d.]+k tokens$"))
    expect(row).to_have_attribute("tail", re.compile(r"^(under 1|\d+)% of the session$"))
    cells = opened.locator("svg[data-part=agent-map] g[data-agent]")
    expect(cells).to_have_count(2)
    expect(opened.locator('svg[data-part=agent-map] g[data-agent="main"] title')).to_contain_text("main: $")
    expect(opened.locator("[data-part=agents] [data-more=agents]")).to_have_count(0)
    # Shown, the line's act takes the rows away again; the address does not change.
    line = opened.locator("[data-line=subagents]")
    expect(line.get_by_role("button", name="Show")).to_have_count(0)
    line.get_by_role("button", name="Hide").click()
    expect(opened.locator("[data-part=agents]")).to_have_count(0)
    expect(ui_page).to_have_url(f"{indexed.url}/m/usage/sessions/claude/{FIRST}")


def test_a_session_opens_under_its_rows_harness_and_sums_to_the_row(indexed, ui_page):
    panel = open_view(ui_page, "sessions")
    harness = panel.locator("[data-part=filters] [data-filter=harness] button.pick")
    row = session_row(panel, FIRST)
    expect(row).to_have_attribute("lead", re.compile(r"^\d+ requests?$"))
    requests = row.get_attribute("lead").split()[0]
    expect(row).to_have_attribute("fact", re.compile(r"^\$\d+\.\d\d$"))
    cost = row.get_attribute("fact")
    with ui_page.expect_request(lambda request: "/m/usage/api/session?" in request.url) as sent:
        row.get_by_role("link", name="Open").click()
    # Read with its list's days and its row's harness, so it adds up to the row: the main
    # agent's requests and the subagent's, and the subagent's cost a part of the row's.
    assert "harness=claude" in sent.value.url and "since=" in sent.value.url, sent.value.url
    opened = row.locator("[data-part=session]")
    expect(opened.locator("[data-line=requests]")).to_have_text(re.compile(r"^Requests\s*3 requests by the main agent (at|from) "))
    expect(opened.locator("[data-line=subagents]")).to_have_text(
        re.compile(rf"^Subagents\s*1 subagent · \$\d+\.\d\d of the {re.escape(cost)}\s*Show$"))
    assert int(requests) == 3 + 2
    # The harness is the address's, not a filter of the page: with one harness holding
    # files the filter has no place, before and after.
    expect(harness).to_have_count(0)


# --- the index's states --------------------------------------------------------------------

def test_with_no_index_the_page_gives_the_command_that_builds_it(own_shell):
    _shell, page = own_shell
    panel = open_view(page)
    block = panel.locator("[data-part=nothing]")
    expect(block).to_have_attribute("data-state", "none")
    expect(block.locator("code")).to_have_text("moxi usage index")
    expect(panel.get_by_role("button", name="Read new sessions")).to_have_count(0)


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
    expect(session_row(panel, LATER)).to_have_count(1)


# --- a session's requests, its cost terms and the rates ------------------------------------

#: A session of two turns and six requests, with a compaction after the fourth, in its own
#: shell: requests 1-3 on the shipped rate, 4-6 under a dated `[[model]]` row that starts
#: between them (so the model gets two price groups and a subtotal line), the sixth on the
#: row's fast tier; and a subagent of two requests on another model.
CHARTED = "0190b000-0000-7000-8000-0000000000e4"
CHARTED_AGENT = "a0190b00000000e4"
SUBAGENT_MODEL = "claude-haiku-4-5"
#: A `[[model]]` row for an id the shipped table doesn't hold, which only a search finds.
SEEDED_ID = "seeded-model-7"

#: Writes the charted session under the harness root of the environment it runs in and
#: prints, as JSON, the moment between its third and fourth request.
CHARTED_SEED = """
import json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
session, agent, model, other = sys.argv[1:5]
t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=session,
                       project="-work-delta", cwd="/work/delta", start=time.time() - 3600)

def ask(n, **usage):
    t.assistant("Done.", usage=T.usage(input=100 * n, cache_read=30000 * n, output=500 * n,
                                       **usage), request_id=f"req_e4{n}", model=model)

t.user("Fix the failing test.")
for n in (1, 2, 3):
    ask(n)
between = math.ceil(t.clock)
t.user("Now the docs.")
ask(4)
t.system("compact_boundary", content="Conversation compacted", level="info", isMeta=False,
         compactMetadata={"trigger": "auto", "preTokens": 150000})
t.user("This session is being continued from an earlier one.", new_prompt=False,
       isCompactSummary=True, isVisibleInTranscriptOnly=True)
ask(5)
ask(6, speed="fast")
child = t.subagent(agent)
child.user("Look into it.")
for n in (1, 2):
    child.assistant("Found it.", usage=T.usage(input=100, cache_read=5000, output=400),
                    request_id=f"req_e4a{n}", model=other)
t.write()
print(json.dumps(datetime.fromtimestamp(between, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
"""

#: Writes usage's config file, argv[1], where the environment it runs in reads it.
WRITE_CONFIG = """
import sys
from moxi.paths import Layout
path = Layout("usage").config_file()
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(sys.argv[1], encoding="utf-8")
"""

CONFIG = """\
[[model]]
id = "{model}"
input = 4
output = 20
provider = "anthropic"
window = 1000000
from = "{start}"
note = "the new rate"
tiers.fast = {{input = 8, output = 40, speed = "fast"}}

[[model]]
id = "{seeded}"
input = 1
output = 2
note = "a row of my own"
"""


@pytest.fixture(scope="module")
def charted(ui_shells, ui_shell_options):
    """A shell of the module's own over the charted session and its `[[model]]` rows."""
    shell = ui_shells(**ui_shell_options)
    start = json.loads(run(shell, sys.executable, "-c", CHARTED_SEED, CHARTED, CHARTED_AGENT,
                           MODEL, SUBAGENT_MODEL))
    run(shell, sys.executable, "-c", WRITE_CONFIG,
        CONFIG.format(model=MODEL, start=start, seeded=SEEDED_ID))
    assert SEEDED_ID in run(shell, sys.executable, "-m", "moxi", "usage", "prices", SEEDED_ID)
    build_index(shell)
    return shell


@pytest.fixture
def charted_page(charted, ui_contexts):
    return ui_contexts(charted).pages[0]


def prices_of(panel):
    return panel.locator("section[data-group=prices]")


def open_prices(page, part: str | None = None):
    """The page at Prices, opened, with `part` (an act's words) opened under the table."""
    panel = open_view(page, "prices")
    prices = prices_of(panel)
    expect(prices.locator("[data-part=price-table]")).to_be_visible()
    if part is not None:
        prices.get_by_role("button", name=part).click()
    return prices


def bill(prices):
    return prices.locator("table[data-part=bill-lines]")


def test_the_page_keeps_the_contract_on_the_prices_section(charted, charted_page,
                                                            ui_component_check):
    check = ui_component_check(charted_page, "usage", "page", {"route": "prices"},
                               tokens=("text",))
    assert check.element == "moxi-usage-page"
    assert any("/m/usage/api/terms?" in url for url in check.requests), check.requests


#: What shows on the prices section and the session view once their reads have drawn.
CHARTED_VIEWS = {
    "prices": "section[data-group=prices] [data-part=price-table]",
    f"sessions/claude/{CHARTED}": "[data-part=session] svg[data-part=session-chart] rect.bar",
}


@pytest.mark.parametrize("route", list(CHARTED_VIEWS), ids=["prices", "session"])
def test_the_prices_section_and_session_view_fit_a_phone(charted, charted_page, ui_width_check,
                                                         route):
    ui_width_check(charted_page, module="usage", route=route)
    panel = panel_of(charted_page)
    expect(panel.locator(CHARTED_VIEWS[route]).first).to_be_visible()
    ui_width_check(charted_page)


@pytest.mark.parametrize("part", ["Details", "Check against a bill", "Find a model"])
def test_each_act_under_the_price_table_fits_a_phone_when_open(charted, charted_page,
                                                               ui_width_check, part):
    ui_width_check(charted_page, module="usage", route="prices")
    prices = prices_of(panel_of(charted_page))
    prices.get_by_role("button", name=part).click()
    expect(prices.get_by_role("button", name=part)).to_have_attribute("aria-expanded", "true")
    ui_width_check(charted_page)


#: Over a table in its scrolling wrapper: the headers it doesn't draw, whether the wrapper
#: scrolls, and, scrolled to its right end, the rows whose first cell has left the wrapper
#: and the colours behind each first cell.
SCROLLED = """(table) => {
  const wrapper = table.parentElement;
  const hidden = [...table.tHead.rows[0].cells]
    .filter((cell) => getComputedStyle(cell).display === "none" || cell.getBoundingClientRect().width === 0)
    .map((cell) => cell.textContent.trim());
  const scrolls = wrapper.scrollWidth > wrapper.clientWidth;
  wrapper.scrollLeft = wrapper.scrollWidth;
  const edge = wrapper.getBoundingClientRect().left;
  const firsts = [...table.rows].map((row) => row.cells[0]);
  return {
    headers: [...table.tHead.rows[0].cells].map((cell) => cell.textContent.trim()),
    hidden, scrolls, scrolled: wrapper.scrollLeft,
    page: document.documentElement.scrollWidth - window.innerWidth,
    out: firsts.filter((cell) => cell.getBoundingClientRect().left < edge - 0.5)
      .map((cell) => cell.textContent.trim()),
    backgrounds: [...new Set(firsts.map((cell) => getComputedStyle(cell).backgroundColor))],
  };
}"""

BILL_HEADERS = ["Model", "Priced by", "fresh input", "cache read", "cache write 5m",
                "cache write 1h", "output", "Cost"]
RATE_HEADERS = ["Model", "Input", "Output", "Cache read", "Write 5m", "Write 1h", "Window",
                "Tiers", "Period", "Where from"]


def scrolled_on_a_phone(table, headers: list[str]) -> None:
    """At phone width `table` draws every header in `headers`, scrolls inside its wrapper
    while the page doesn't, and keeps its Model column in view, on an opaque background,
    when scrolled to its right end."""
    seen = table.evaluate(SCROLLED)
    assert seen["headers"] == headers, seen
    assert seen["hidden"] == [], seen
    assert seen["scrolls"] and seen["scrolled"] > 0, seen
    assert seen["page"] <= 0, seen
    assert seen["out"] == [], seen
    assert seen["backgrounds"], seen
    for colour in seen["backgrounds"]:
        # rgb() is opaque; rgba() and color() carry an alpha, which must be 1.
        alpha = re.fullmatch(r"rgb\(\d+, \d+, \d+\)|rgba\(.*, (?P<a>[\d.]+)\)", colour)
        assert alpha and float(alpha.group("a") or 1) == 1, seen


def test_the_bill_and_the_rates_keep_every_column_on_a_phone_and_scroll_with_model_in_view(
        charted, charted_page, ui_width_check):
    ui_width_check(charted_page, module="usage", route="prices")
    prices = prices_of(panel_of(charted_page))
    prices.get_by_role("button", name="Check against a bill").click()
    table = bill(prices)
    expect(table.locator("tr[data-line=total]")).to_be_visible()
    for header in BILL_HEADERS:
        expect(table.locator("thead th", has_text=header).first).to_be_visible()
    scrolled_on_a_phone(table, BILL_HEADERS)

    prices.get_by_role("button", name="Details").click()
    rates = prices.locator("[data-part=details] table[data-part=rate-lines]")
    expect(rates.locator("tr[data-rate]")).to_have_count(2)
    scrolled_on_a_phone(rates, RATE_HEADERS)
    ui_width_check(charted_page)


def opened_of(panel):
    """The opened session, under its row in the Sessions section."""
    return panel.locator("section[data-group=sessions] [data-part=session]")


def session_chart(panel):
    """The opened session's own chart, not its opened subagent's."""
    return panel.locator(
        "section[data-group=sessions] [data-part=session] > [data-line=chart] svg[data-part=session-chart]")


def test_the_opened_session_charts_each_request_its_turns_compaction_and_context(
        charted, charted_page):
    panel = open_view(charted_page, f"sessions/claude/{CHARTED}")
    chart = session_chart(panel)
    expect(chart.locator("rect.bar")).to_have_count(6)
    expect(chart.locator("rect.bar.now")).to_have_count(1)
    expect(chart.locator("[data-mark=turn]")).to_have_count(1)
    compaction = chart.locator("[data-mark=compaction]")
    expect(compaction).to_have_count(1)
    expect(compaction.locator("text")).to_have_text("compaction (auto)")
    expect(compaction.locator("title")).to_contain_text("150,000 tokens before")
    # One line through the six requests' context, under a window that steps up once: the
    # shipped window for the first three, the `[[model]]` row's from the fourth on.
    expect(chart.locator("path[data-part=context]")).to_have_attribute(
        "d", re.compile(r"^M[\d.,]+(L[\d.,]+){5}$"))
    expect(chart.locator("path[data-part=window]")).to_have_attribute(
        "d", re.compile(r"^M[\d.,]+H[\d.]+(H[\d.]+)*V[\d.]+(H[\d.]+)+$"))
    expect(chart.locator("[data-part=last-context]")).to_have_text(re.compile(r"^\d+k$"))
    # Each request's readout is on hover over its column.
    readout = chart.locator('rect.hit[data-n="5"] title')
    expect(readout).to_contain_text("#5")
    expect(readout).to_contain_text("turn 2")
    expect(readout).to_contain_text(MODEL)


def test_the_opened_session_says_its_requests_context_cost_agents_and_start(
        charted, charted_page):
    panel = open_view(charted_page, f"sessions/claude/{CHARTED}")
    opened = opened_of(panel)
    expect(opened.locator("[data-line=requests]")).to_have_text(re.compile(
        r"^Requests\s*6 requests by the main agent (at .+|from .+ to .+) · the dearest \$\d+\.\d\d at .+$"))
    expect(opened.locator("[data-line=context]")).to_have_text(re.compile(
        r"^Context\s*\d+k of 1,000k tokens on the last request, \d+% full$"))
    expect(opened.locator("[data-line=went]")).to_have_text(re.compile(
        r"^Went to\s*\w[\w ]+ \$\d+\.\d\d( · .+)?$"))
    expect(opened.locator("[data-line=went]")).to_contain_text("cache reads $")
    expect(opened.locator("[data-line=subagents]")).to_have_text(re.compile(
        r"^Subagents\s*1 subagent · <?\$\d+\.\d\d of the \$\d+\.\d\d\s*Show$"))
    expect(opened.locator("[data-line=session]")).to_have_text(re.compile(
        rf"^Session\s*claude · {MODEL} · started "))
    # With no measure the restart point has no line, once its read has answered.
    expect(opened.locator("[data-part=reading]")).to_have_count(0)
    expect(opened.locator("[data-line=restart]")).to_have_count(0)
    acts = opened.locator("[data-part=session-acts]")
    expect(acts.get_by_role("link", name="Open in Sessions")).to_have_attribute(
        "href", f"/m/sessions/claude/{CHARTED}")
    for name in ("Table", "Check against a bill", "Copy id"):
        expect(acts.get_by_role("button", name=name)).to_be_visible()
    # The session is never shown by its id.
    assert CHARTED not in opened.inner_text()


def test_an_addressed_session_is_first_in_the_section_read_over_its_whole_life(
        charted, charted_page):
    with charted_page.expect_request(
            lambda request: "/m/usage/api/session?" in request.url) as sent:
        panel = open_view(charted_page, f"sessions/claude/{CHARTED}")
    assert "since=" not in sent.value.url and "harness=claude" in sent.value.url, sent.value.url
    section = panel.locator("section[data-group=sessions]")
    placed = section.locator("[data-list=placed] moxi-ui-row")
    expect(placed).to_have_attribute("data-key", f"claude/{CHARTED}")
    expect(placed).to_have_attribute("open", "")
    expect(section.locator("[data-list=first] moxi-ui-row")).to_have_count(0)
    # Closed, its row stays first and the address goes back to the section.
    placed.get_by_role("link", name="Close").click()
    expect(placed).not_to_have_attribute("open", "")
    assert charted_page.evaluate("location.pathname") == "/m/usage/sessions"


def test_open_in_sessions_goes_to_the_session_on_the_sessions_page(charted, charted_page):
    panel = open_view(charted_page, f"sessions/claude/{CHARTED}")
    opened_of(panel).get_by_role("link", name="Open in Sessions").click()
    expect(charted_page).to_have_url(f"{charted.url}/m/sessions/claude/{CHARTED}")


def test_the_opened_sessions_table_has_a_row_a_request_and_its_compaction(
        charted, charted_page):
    opened = opened_of(open_view(charted_page, f"sessions/claude/{CHARTED}"))
    opened.get_by_role("button", name="Table").click()
    table = opened.locator("[data-part=session-table] table")
    expect(table.locator("tbody tr")).to_have_count(7)
    expect(table.locator('tr[data-row="c:4"]')).to_contain_text("compaction (auto)")
    expect(table.locator("thead th")).to_have_text(
        ["#", "Time", "Turn", "Model", "Context", "Fresh input", "Cache read", "Cache write 5m",
         "Cache write 1h", "Output", "Cost"])


def test_check_against_a_bill_in_the_opened_session_lines_up_each_agents_models(
        charted, charted_page):
    opened = opened_of(open_view(charted_page, f"sessions/claude/{CHARTED}"))
    opened.get_by_role("button", name="Check against a bill").click()
    lines = opened.locator("table[data-part=bill-lines]")
    expect(lines.locator(f'tr[data-model="{CHARTED_AGENT} · {SUBAGENT_MODEL}"]')).to_have_count(1)
    expect(lines.locator("tr[data-line=total]")).to_have_count(1)
    opened.get_by_role("button", name="Table").click()
    expect(lines).to_have_count(0)


def test_the_restart_point_says_when_the_session_is_past_it(charted, charted_page):
    charted_page.route("**/m/usage/api/restart-point?*", lambda route: route.fulfill(
        json={"model": MODEL, "tokens": 1000, "reason": None}))
    opened = opened_of(open_view(charted_page, f"sessions/claude/{CHARTED}"))
    restart = opened.locator("[data-line=restart]")
    expect(restart).to_have_text(re.compile(
        rf"^Restart point\s*1k tokens for {MODEL} · this session is past it$"))
    expect(restart.locator(".d")).to_have_text(f"1k tokens for {MODEL}")


def test_a_read_still_out_says_reading_in_its_place(charted, charted_page):
    held = []
    charted_page.route("**/m/usage/api/terms?by=agent*", lambda route: held.append(route))
    opened = opened_of(open_view(charted_page, f"sessions/claude/{CHARTED}"))
    reading = opened.locator("[data-part=reading][data-reading=terms]")
    expect(reading).to_have_text("Reading…")
    expect(opened.locator("[data-line=went]")).to_have_count(0)
    expect(opened.locator("[data-line=session]")).to_be_visible()
    assert held
    for route in held:
        route.continue_()
    expect(opened.locator("[data-line=went]")).to_be_visible()
    expect(reading).to_have_count(0)


#: The words the opened session's chart draws, each with whether its box lies inside the
#: chart's viewBox.
CHART_WORDS = """svg => [...svg.querySelectorAll("text")].map((text) => {
    const box = text.getBBox(), view = svg.viewBox.baseVal;
    return [text.textContent, box.x >= view.x && box.y >= view.y
            && box.x + box.width <= view.x + view.width && box.y + box.height <= view.y + view.height];
  })"""


def test_the_opened_sessions_chart_keeps_its_words_inside_wide_and_on_a_phone(
        charted, charted_page):
    panel = open_view(charted_page, f"sessions/claude/{CHARTED}")
    chart = session_chart(panel)
    expect(chart.locator("rect.bar")).to_have_count(6)
    wide = chart.evaluate(CHART_WORDS)
    assert [text for text, inside in wide if not inside] == [], wide
    charted_page.set_viewport_size({"width": 360, "height": 800})
    expect(chart).to_have_attribute("viewBox", re.compile(r"^0 0 2\d\d "))
    phone = chart.evaluate(CHART_WORDS)
    assert [text for text, inside in phone if not inside] == [], phone


def test_a_subagents_open_shows_its_requests_under_its_row_with_its_address(charted, charted_page):
    panel = open_view(charted_page, f"sessions/claude/{CHARTED}")
    expect(session_chart(panel).locator("rect.bar")).to_have_count(6)
    opened = opened_of(panel)
    row = show_subagents(opened).first
    expect(row).to_have_attribute("lead", SUBAGENT_MODEL)
    row.get_by_role("link", name="Open").click()
    expect(charted_page).to_have_url(
        f"{charted.url}/m/usage/sessions/claude/{CHARTED}/{CHARTED_AGENT}")
    agent = row.locator("[data-part=agent]")
    # Its own requests, as a session's are shown: the line, its context, the chart.
    expect(agent.locator("[data-line=requests]")).to_have_text(
        re.compile(r"^Requests\s*2 requests (at|from) "))
    expect(agent.locator("[data-line=context]")).to_have_text(
        re.compile(r"^Context\s*\d+k? (of [\d,]+k )?tokens on the last request"))
    chart = agent.locator("svg[data-part=session-chart]")
    expect(chart.locator("rect.bar")).to_have_count(2)
    expect(chart.locator("[data-mark]")).to_have_count(0)
    expect(chart.locator('rect.hit[data-n="2"] title')).to_contain_text(SUBAGENT_MODEL)
    # The session's own chart stays the main agent's.
    expect(session_chart(panel).locator("rect.bar")).to_have_count(6)
    agent.get_by_role("button", name="Table").click()
    expect(agent.locator("[data-part=agent-table] tbody tr")).to_have_count(2)
    # Its act closes it back to the session, the rows still shown.
    row.get_by_role("link", name="Close").click()
    expect(charted_page).to_have_url(f"{charted.url}/m/usage/sessions/claude/{CHARTED}")
    expect(opened.locator("[data-part=agent]")).to_have_count(0)
    expect(opened.locator("[data-part=agents] moxi-ui-row")).to_have_count(1)


def test_the_bills_total_is_its_group_lines(charted, charted_page):
    table = bill(open_prices(charted_page, "Check against a bill"))
    total = table.locator("tr[data-line=total] td[data-cost]")
    expect(total).to_have_count(1)
    expect(table.locator("tr[data-line=subtotal]")).to_have_count(1)
    expect(table.locator(f'tr[data-line=subtotal][data-model="{MODEL} total"]')).to_have_count(1)
    groups = [float(cost) for cost in
              table.locator("tr[data-line=group] td[data-cost]").evaluate_all(
                  "cells => cells.map((cell) => cell.dataset.cost)")]
    subtotal = float(table.locator("tr[data-line=subtotal] td[data-cost]")
                     .get_attribute("data-cost"))
    shown = float(total.get_attribute("data-cost"))
    assert len(groups) >= 3 and all(cost > 0 for cost in groups), groups
    # The subtotal line repeats its groups: the total is the group lines alone.
    assert shown == pytest.approx(sum(groups), abs=1e-9)
    assert sum(groups) + subtotal != pytest.approx(shown, abs=1e-9)


def test_details_list_every_model_priced_and_where_its_prices_came_from(charted, charted_page):
    prices = open_prices(charted_page)
    details = prices.locator("[data-part=details]")
    expect(details).to_have_count(0)
    prices.get_by_role("button", name="Details").click()
    lines = details.locator("tr[data-rate]")
    expect(lines).to_have_count(2)
    assert sorted(lines.evaluate_all("rows => rows.map((row) => row.dataset.rate)")) == sorted(
        [MODEL, SUBAGENT_MODEL])
    sonnet = details.locator(f'tr[data-rate="{MODEL}"]')
    expect(sonnet).to_contain_text("your config")
    expect(sonnet).to_contain_text("fast (speed fast)")
    overrides = details.locator("[data-part=overrides] li")
    expect(overrides).to_have_count(2)
    expect(overrides.filter(has_text=SEEDED_ID)).to_contain_text("a row of my own")
    expect(details.locator("[data-part=layers]")).to_contain_text("model table in effect")
    # One act is open at a time, and a second press closes it.
    prices.get_by_role("button", name="Check against a bill").click()
    expect(details).to_have_count(0)
    expect(bill(prices).locator("tr[data-line=total]")).to_be_visible()
    prices.get_by_role("button", name="Check against a bill").click()
    expect(bill(prices)).to_have_count(0)


def test_find_a_model_searches_the_whole_table_and_finds_a_seeded_row(charted, charted_page):
    prices = open_prices(charted_page, "Find a model")
    found = prices.locator("[data-part=find]")
    found.get_by_role("searchbox", name="Find a model").fill("seeded-model")
    expect(found.locator(f'tr[data-rate="{SEEDED_ID}"]')).to_be_visible()
    expect(found.locator("tr[data-rate]")).to_have_count(1)
    expect(found.locator("[data-part=more]")).to_have_count(0)
    found.get_by_role("searchbox", name="Find a model").fill("no-such-model-anywhere")
    expect(found.locator("[data-part=no-match]")).to_have_text("No model matches.")


def test_prices_closed_counts_the_priced_models_and_dates_the_table(charted, charted_page):
    prices = prices_of(open_view(charted_page))
    expect(prices.locator("[data-part=closed]")).to_have_text(
        "2 models, all priced · a local price table, not billing data")
    count = prices.locator("[data-part=count]")
    expect(count).to_have_text(re.compile(r"^updated [A-Z][a-z]{2} \d{1,2}(, \d{4})?$"))
    count.click()
    expect(charted_page).to_have_url(f"{charted.url}/m/usage/prices")
    expect(count).to_have_text("Close")


def test_prices_opened_lists_each_models_price_per_million_and_three_acts(charted, charted_page):
    prices = open_prices(charted_page)
    table = prices.locator("[data-part=price-table]")
    expect(table.get_by_role("columnheader")).to_have_text(
        ["$ per million tokens", "input", "output", "cache write", "cache read"])
    rows = table.locator("[data-price]")
    expect(rows).to_have_count(2)
    # The `[[model]]` row priced input at 4 and output at 20 per million.
    sonnet = table.locator(f'[data-price^="{MODEL}|"]').get_by_role("cell")
    expect(sonnet.nth(1)).to_have_text("4.00")
    expect(sonnet.nth(2)).to_have_text("20.00")
    expect(prices.locator("[data-part=paragraph]")).to_contain_text(
        "Every figure on this page is tokens from your transcripts times these prices.")
    for act in ("Find a model", "Check against a bill", "Details"):
        expect(prices.get_by_role("button", name=act)).to_have_attribute("aria-expanded", "false")


def kinds_of(panel):
    return panel.locator("section[data-group=kinds]")


def test_token_kinds_closed_says_the_input_read_from_cache_and_opens_its_classes(
        charted, charted_page):
    kinds = kinds_of(open_view(charted_page))
    expect(kinds.locator("[data-part=closed]")).to_have_text(
        re.compile(r"^\d+% of input read from cache · cache reads \$\d+\.\d\d, output \$\d+\.\d\d$"))
    count = kinds.locator("[data-part=count]")
    expect(count).to_have_text(re.compile(r"tokens$"))
    count.click()
    expect(charted_page).to_have_url(f"{charted.url}/m/usage/kinds")
    rows = kinds.locator("[data-part=rows] .tr2")
    expect(rows).to_have_count(4)
    names = rows.locator(".nm").evaluate_all("cells => cells.map((cell) => cell.textContent.split('·')[0].trim())")
    assert sorted(names) == ["Cache reads", "Cache writes", "Fresh input", "Output"], names
    for row in rows.all():
        expect(row).to_contain_text(re.compile(r"tokens\s*(at least )?\$\d"))
    # The sixth request ran on the `[[model]]` row's fast tier.
    expect(kinds.locator("[data-row=output]")).to_contain_text(re.compile(r"· \$\d+\.\d\d at the fast price"))


def test_token_kinds_says_what_caching_saved_as_an_estimate(charted, charted_page):
    kinds = kinds_of(open_view(charted_page, "kinds"))
    expect(kinds.locator("[data-part=saving]")).to_have_text(re.compile(
        r"^[\d.]+[kM]? of [\d.]+[kM]? input tokens were read from cache\. With no cache, at the same prices, "
        r"those reads would have cost \$[\d.]+ as fresh input; they cost \$[\d.]+(, and cache writes .*)?, "
        r"so caching (saved about \$[\d.]+|cost about \$[\d.]+ more than it saved)\. An estimate\.$"))

