"""The views usage's page had before its frame was redrawn, which stay mounted under the
frame until each has its place in the new page, and usage's two blocks, in a browser,
through a real shell that starts usage's real page server:
the contract check on the page and its two blocks (the cost block saying how old the index
is), each view drawn over a small index and fitting a phone, a session opened under its
row's harness, the command with no index, a read over a stale index that runs no pass,
and "Read new sessions" bringing new sessions in. Over a session of two turns with a compaction and a dated `[[model]]` row:
the session view's request and context charts and their readout, a subagent's charts, the
cost terms view's Total against its group lines and the models view, the Rates section and its search, and Reconcile and Rates keeping every
column on a phone, scrolling in their wrappers with the Model column in view.

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
    return panel.locator("section[data-part=view] tr[data-session]")


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


# --- the views, at a phone's width ---------------------------------------------------------

#: Each view's route and what shows once its report has drawn.
VIEWS = {
    "": "[data-group=chart] svg g[data-period]",
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
    harness = panel.locator("[data-part=filters] [data-filter=harness] button.pick")
    row = panel.locator(f'section[data-part=view] tr[data-session="{FIRST}"]')
    expect(row).to_be_visible()
    # One harness holds files, so its filter has no place until one is set.
    expect(harness).to_have_count(0)
    tokens = row.locator("td.num").nth(-2).inner_text()
    with ui_page.expect_request(lambda request: "/m/usage/api/session?" in request.url) as sent:
        row.locator("a").click()
    assert "harness=claude" in sent.value.url, sent.value.url
    expect(harness).to_have_text("Claude▾")
    totals = panel.locator("section[data-part=view] [data-part=totals]")
    expect(totals.locator("span", has_text="Tokens").locator("b")).to_have_text(tokens)


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
    expect(panel.locator(f'section[data-part=view] tr[data-session="{LATER}"]')).to_have_count(1)


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


def requests_chart(panel):
    return panel.locator("section[data-part=view] svg[data-chart=requests]")


def context_chart(panel):
    return panel.locator("section[data-part=view] svg[data-chart=context]")


def reconcile(panel):
    return panel.locator("section[data-part=view] table[data-part=reconcile]").first


def dollars(text: str) -> str:
    found = re.search(r"\$[\d,.]+", text)
    assert found, text
    return found.group(0)


def test_the_page_keeps_the_contract_on_the_cost_terms_view(charted, charted_page,
                                                             ui_component_check):
    check = ui_component_check(charted_page, "usage", "page", {"route": "terms"},
                               tokens=("text",))
    assert check.element == "moxi-usage-page"
    assert any("/m/usage/api/terms?" in url for url in check.requests), check.requests


#: What shows on the cost terms and session views once their reports have drawn.
CHARTED_VIEWS = {
    "terms": "section[data-part=view] tr[data-line=total]",
    f"session/{CHARTED}": "section[data-part=view] svg[data-chart=requests] g[data-n]",
}


@pytest.mark.parametrize("route", list(CHARTED_VIEWS), ids=["terms", "session"])
def test_the_cost_terms_and_session_views_fit_a_phone(charted, charted_page, ui_width_check,
                                                      route):
    ui_width_check(charted_page, module="usage", route=route)
    panel = panel_of(charted_page)
    expect(panel.locator(CHARTED_VIEWS[route]).first).to_be_visible()
    ui_width_check(charted_page)


def test_the_rates_section_fits_a_phone_when_open(charted, charted_page, ui_width_check):
    ui_width_check(charted_page, module="usage", route="terms")
    rates = panel_of(charted_page).locator("section[data-part=view] details[data-part=rates]")
    rates.locator("summary").click()
    expect(rates.locator("tr[data-rate]")).to_have_count(2)
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

RECONCILE_HEADERS = ["Model", "Priced by", "fresh input", "cache read", "cache write 5m",
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


def test_reconcile_and_rates_keep_every_column_on_a_phone_and_scroll_with_model_in_view(
        charted, charted_page, ui_width_check):
    ui_width_check(charted_page, module="usage", route="terms")
    panel = panel_of(charted_page)
    view = panel.locator("section[data-part=view]")
    table = reconcile(panel)
    expect(table.locator("tr[data-line=total]")).to_be_visible()
    for header in RECONCILE_HEADERS:
        expect(table.locator("thead th", has_text=header).first).to_be_visible()
    scrolled_on_a_phone(table, RECONCILE_HEADERS)

    rates = view.locator("details[data-part=rates]")
    rates.locator("summary").click()
    expect(rates.locator("tr[data-rate]")).to_have_count(2)
    scrolled_on_a_phone(rates.locator("table[data-part=rate-lines]"), RATE_HEADERS)
    ui_width_check(charted_page)


def test_the_session_view_charts_each_request_its_turns_compaction_and_context(
        charted, charted_page):
    panel = open_view(charted_page, f"session/{CHARTED}")
    bars = requests_chart(panel)
    curve = context_chart(panel)
    expect(bars.locator("g[data-n]")).to_have_count(6)
    expect(bars.locator("[data-rule=turn]")).to_have_count(1)
    compaction = bars.locator("[data-rule=compaction]")
    expect(compaction).to_have_count(1)
    expect(compaction).to_have_attribute("data-after", "4")
    expect(compaction.locator("text")).to_have_text("compaction (auto)")
    expect(curve.locator("path[data-part=context]")).to_have_attribute("data-points", "6")
    expect(curve.locator("circle.compaction-dot")).to_have_count(1)
    expect(curve.locator("path[data-part=window]")).to_have_count(1)
    # The shipped window for the first three, the `[[model]]` row's from the fourth on.
    expect(curve.locator("text.window-label")).to_have_text(["window 200,000",
                                                             "window 1,000,000"])

    readout = panel.locator("section[data-part=view] [data-part=readout]")
    expect(readout).not_to_contain_text("#5")
    bars.locator('rect.hit[data-n="5"]').hover()
    expect(readout).to_contain_text("#5")
    expect(readout).to_contain_text("turn 2")
    expect(readout).to_contain_text(MODEL)


#: The labels the request charts draw beside their lines, each with whether its box lies
#: inside its chart's viewBox, and its anchor.
CHART_LABELS = """svgs => svgs.flatMap((svg) => [
  ...svg.querySelectorAll("text.window-label, [data-rule=compaction] text")].map((text) => {
    const box = text.getBBox(), view = svg.viewBox.baseVal;
    return [text.textContent, text.getAttribute("text-anchor"),
            box.x >= view.x && box.y >= view.y && box.x + box.width <= view.x + view.width
            && box.y + box.height <= view.y + view.height];
  }))"""


def test_the_request_charts_keep_their_labels_inside_wide_and_on_a_phone(
        charted, charted_page):
    """The larger window is the top of its chart, so its label goes under its line; on a
    phone the compaction's rule is near enough the right edge that its label goes left of
    it."""
    panel = open_view(charted_page, f"session/{CHARTED}")
    charts = panel.locator("section[data-part=view] svg[data-chart]")
    expect(context_chart(panel).locator("text.window-label")).to_have_count(2)
    wide = charts.evaluate_all(CHART_LABELS)
    assert [label for label, _anchor, inside in wide if not inside] == [], wide
    charted_page.set_viewport_size({"width": 360, "height": 800})
    expect(context_chart(panel)).to_have_attribute("viewBox", re.compile(r"^0 0 [23]\d\d "))
    phone = charts.evaluate_all(CHART_LABELS)
    assert [label for label, _anchor, inside in phone if not inside] == [], phone
    assert ["compaction (auto)", "end", True] in phone, phone


def test_the_subagents_link_redraws_both_charts_for_its_requests(charted, charted_page):
    panel = open_view(charted_page, f"session/{CHARTED}")
    expect(requests_chart(panel).locator("g[data-n]")).to_have_count(6)
    panel.locator(f'section[data-part=view] tr[data-agent="{CHARTED_AGENT}"] a').click()
    expect(charted_page).to_have_url(
        f"{charted.url}/m/usage/session/{CHARTED}/{CHARTED_AGENT}")
    bars = requests_chart(panel)
    expect(bars.locator("g[data-n]")).to_have_count(2)
    expect(bars.locator("[data-rule]")).to_have_count(0)
    expect(context_chart(panel).locator("path[data-part=context]")).to_have_attribute(
        "data-points", "2")
    bars.locator('rect.hit[data-n="2"]').hover()
    expect(panel.locator("section[data-part=view] [data-part=readout]")).to_contain_text(
        SUBAGENT_MODEL)


def test_the_cost_terms_total_is_its_group_lines_and_the_models_views_total(
        charted, charted_page):
    panel = open_view(charted_page, "terms")
    table = reconcile(panel)
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
    terms_total = dollars(total.inner_text())

    panel = open_view(charted_page, "models")
    expect(panel.locator(f'section[data-part=view] tr[data-model="{MODEL}"]')).to_be_visible()
    cost = panel.locator("section[data-part=view] [data-part=totals]").locator(
        "span", has_text="Cost").locator("b")
    expect(cost).to_have_text(terms_total)


def test_rates_list_every_reconcile_model_and_a_search_finds_a_seeded_row(
        charted, charted_page):
    panel = open_view(charted_page, "terms")
    expect(reconcile(panel).locator("tr[data-line=total]")).to_be_visible()
    rates = panel.locator("section[data-part=view] details[data-part=rates]")
    expect(rates.locator("tr[data-rate]")).to_have_count(0)
    rates.locator("summary").click()
    lines = rates.locator("tr[data-rate]")
    expect(lines).to_have_count(2)
    assert sorted(lines.evaluate_all("rows => rows.map((row) => row.dataset.rate)")) == sorted(
        [MODEL, SUBAGENT_MODEL])
    sonnet = rates.locator(f'tr[data-rate="{MODEL}"]')
    expect(sonnet).to_contain_text("your config")
    expect(sonnet).to_contain_text("fast (speed fast)")
    overrides = rates.locator("[data-part=overrides] li")
    expect(overrides).to_have_count(2)
    expect(overrides.filter(has_text=SEEDED_ID)).to_contain_text("a row of my own")
    expect(rates.locator("[data-part=layers]")).to_contain_text("model table in effect")

    rates.get_by_role("searchbox", name="Search the model table").fill("seeded-model")
    expect(rates.locator(f'tr[data-rate="{SEEDED_ID}"]')).to_be_visible()
    expect(lines).to_have_count(1)
    expect(rates.locator("[data-part=more]")).to_have_count(0)
