"""The filter line and the chart of usage's page in a browser, through a real shell that
starts usage's real page server: the Range menu's entries and what each choice does to
the chart and leaves alone in Cost, the two days of a range of one's own, the Harness
and Model filters, the folder filter on a shell without projects, the chart's bars,
sentence, today's value, dashed outline and table by cost and by tokens, a range that
holds nothing, a range whose total is not whole with its line at the foot, today's views
reading under the same range, and all of it at a phone's width.

The transcripts are written, and the index built with `python -m moxi usage index`, in
the shell's own environment, so they sit under the shell's home where its page servers
read them. No test changes what the index holds, so the tests hold under pytest-xdist in
any order.
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

SONNET = "claude-sonnet-4-5"
HAIKU = "claude-haiku-4-5"
#: A model no price table holds, so its tokens count and its cost does not.
UNPRICED = "mystery-9"
THREAD = "0190c000-0000-7000-8000-0000000c0da1"
#: Four Claude sessions and one Codex thread. Inside the last 7 days: two sessions on two
#: models in two folders. Inside the last 30: one more. Inside the last 90: the Codex
#: thread, on the model with no price. Before that: one session, 200 days ago.
SESSIONS = [
    {"harness": "claude", "session": "0190b000-0000-7000-8000-0000000000a1", "project": "proj-a",
     "days_ago": 0, "model": SONNET},
    {"harness": "claude", "session": "0190b000-0000-7000-8000-0000000000a2", "project": "proj-b",
     "days_ago": 2, "model": HAIKU},
    {"harness": "claude", "session": "0190b000-0000-7000-8000-0000000000a3", "project": "proj-a",
     "days_ago": 20, "model": SONNET},
    {"harness": "codex", "session": THREAD, "project": "proj-b", "days_ago": 50, "model": UNPRICED},
    {"harness": "claude", "session": "0190b000-0000-7000-8000-0000000000a5", "project": "proj-a",
     "days_ago": 200, "model": SONNET},
]

#: Writes the sessions of the JSON list in argv[1] under the harness roots of the
#: environment it runs in: three requests each, the newest a minute old.
SEED = """
import json, sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for spec in json.loads(sys.argv[1]):
    start = now - spec["days_ago"] * 86400 - 60
    cwd = "/work/" + spec["project"]
    if spec["harness"] == "codex":
        r = T.CodexRollout(Path(sessions.root("codex")), thread_id=spec["session"], cwd=cwd,
                           start=start)
        r.turn(spec["model"])
        r.user("Fix the failing test.")
        for m in range(3):
            r.request(T.token_usage(output=1000 + m * 500), response_id=f"resp_c{m}")
        r.turn_end()
        r.write()
        continue
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=spec["session"],
                           project="-work-" + spec["project"], cwd=cwd, start=start)
    for m in range(3):
        t.user("Fix the failing test.")
        t.assistant("Done.", usage=T.usage(input=200, output=1000 + m * 500),
                    request_id=f"req_{spec['session'][-2:]}{m}", model=spec["model"])
    t.write()
"""

MONEY = r"\$\d[\d,]*\.\d\d"


def run(shell, *argv: str) -> str:
    done = subprocess.run(list(argv), env=shell.env, capture_output=True, text=True,
                          timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done.stdout


def seeded_shell(ui_shells, **options):
    shell = ui_shells(**options)
    run(shell, sys.executable, "-c", SEED, json.dumps(SESSIONS))
    run(shell, sys.executable, "-m", "moxi", "usage", "index")
    return shell


@pytest.fixture(scope="module")
def seeded(ui_shells, ui_shell_options):
    """A shell of the module's own over the five sessions."""
    return seeded_shell(ui_shells, **ui_shell_options)


@pytest.fixture(scope="module")
def small(ui_shells, ui_shell_options):
    """The same sessions on a shell that leaves projects out."""
    return seeded_shell(ui_shells, **{**ui_shell_options, "leave_out": ["projects"]})


@pytest.fixture
def page(seeded, ui_contexts):
    return ui_contexts(seeded).pages[0]


def open_page(page, route: str = ""):
    page.goto(f"/m/usage/{route}")
    panel = page.locator("#moxi-panel > moxi-usage-page")
    expect(panel.locator("[data-part=filters]")).to_be_visible()
    return panel


def filter_of(panel, key: str):
    return panel.locator(f'[data-part=filters] [data-filter="{key}"]')


def pick(panel, key: str, entry: str) -> None:
    """Opens a filter's menu and picks the entry of that name."""
    filter_of(panel, key).locator("button.pick").click()
    filter_of(panel, key).get_by_role("menuitemradio", name=re.compile(rf"^{re.escape(entry)}\b")).click()
    expect(filter_of(panel, key).get_by_role("menu")).to_have_count(0)


def chart_of(panel):
    return panel.locator("[data-group=chart] svg[data-part=chart]")


def bars_of(panel):
    return chart_of(panel).locator("g[data-period]")


def sentence_of(panel):
    return panel.locator("[data-group=chart] [data-part=sentence]")


def cost_figures(panel) -> list[str]:
    return panel.locator("[data-group=cost] [data-cost] .fig").all_inner_texts()


# --- the filter line -----------------------------------------------------------------------

def test_the_filter_line_sits_under_cost_and_opens_on_the_last_30_days_by_cost(page):
    panel = open_page(page)
    order = panel.evaluate("""el => [...el.shadowRoot.children]
        .filter((node) => node.dataset && node.dataset.part).map((node) => node.dataset.part)""")
    assert order[:3] == ["top", "filters", "below"], order
    assert order.index("below") < order.index("foot"), order
    expect(filter_of(panel, "range").locator(".k")).to_have_text("Range")
    expect(filter_of(panel, "range").locator("button.pick")).to_have_text("last 30 days▾")
    expect(panel.get_by_role("button", name="Cost", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(panel.get_by_role("button", name="Tokens", exact=True)).to_have_attribute("aria-pressed", "false")
    # Nothing here is a heading: the shell's top line names the page.
    assert panel.evaluate("el => el.shadowRoot.querySelectorAll('h1, h2, h3').length") == 0


def test_the_range_menu_lists_each_range_with_its_figure_and_checks_the_one_in_force(page):
    panel = open_page(page)
    expect(panel.locator("[data-group=cost] [data-cost]")).to_have_count(3)
    today, week, month = (re.search(MONEY, text).group(0) for text in cost_figures(panel))
    button = filter_of(panel, "range").locator("button.pick")
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    menu = filter_of(panel, "range").get_by_role("menu")
    entries = menu.get_by_role("menuitemradio")
    expect(entries).to_have_text([
        re.compile(rf"^Today\s*{re.escape(today)}$"), re.compile(rf"^Last 7 days\s*{re.escape(week)}$"),
        re.compile(rf"^✓\s*Last 30 days\s*{re.escape(month)}$"),
        re.compile(rf"^Last 90 days\s*at least {MONEY}$"), re.compile(r"^From … to …$"),
        re.compile(rf"^All time\s*at least {MONEY}$")])
    expect(menu.get_by_role("menuitemradio", checked=True)).to_have_count(1)
    # A rule sets the two entries with no preset apart.
    expect(menu.locator("hr")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    expect(button).to_be_focused()
    button.click()
    panel.locator("[data-group=cost]").click()
    expect(menu).to_have_count(0)


def test_each_range_redraws_the_chart_and_leaves_cost_as_it_was(page):
    panel = open_page(page)
    expect(bars_of(panel)).to_have_count(3)
    expect(panel.locator("[data-group=chart] .gk")).to_have_text("Each day")
    before = cost_figures(panel)

    with page.expect_request(lambda request: "/m/usage/api/daily?by=day" in request.url) as sent:
        pick(panel, "range", "Last 7 days")
    assert "since=" in sent.value.url and "until=" in sent.value.url, sent.value.url
    expect(filter_of(panel, "range").locator("button.pick")).to_have_text("last 7 days▾")
    expect(bars_of(panel)).to_have_count(2)
    expect(chart_of(panel)).to_have_attribute("aria-label", "Estimated cost each day, last 7 days")
    expect(sentence_of(panel)).to_have_text(
        re.compile(rf"^The most in these 7 days was (today|Wed|Thu|Fri|Sat|Sun|Mon|Tue), {MONEY}\. 5 days had no sessions\.$"))

    pick(panel, "range", "Today")
    expect(bars_of(panel)).to_have_count(1)
    # One bar says its own value, so there is no sentence to compare it in.
    expect(sentence_of(panel)).to_have_text("")
    expect(chart_of(panel).locator("[data-part=value]")).to_have_text(re.compile(rf"^{MONEY}$"))

    pick(panel, "range", "Last 90 days")
    expect(bars_of(panel)).to_have_count(4)

    with page.expect_request(lambda request: request.url.endswith("/m/usage/api/daily?by=month")):
        pick(panel, "range", "All time")
    expect(panel.locator("[data-group=chart] .gk")).to_have_text("Each month")
    expect(chart_of(panel)).to_have_attribute("aria-label", "Estimated cost each month, all time")
    assert 3 <= bars_of(panel).count() <= 5
    expect(filter_of(panel, "range").locator("button.pick")).to_have_class(re.compile(r"\bset\b"))
    assert cost_figures(panel) == before


def test_a_range_of_two_typed_days_is_charted_and_named_by_its_days(page):
    panel = open_page(page)
    box = filter_of(panel, "range")
    box.locator("button.pick").click()
    box.get_by_role("menuitemradio", name="From … to …").click()
    form = box.locator("[data-part=custom]")
    expect(form.get_by_label("From")).not_to_have_value("")
    form.get_by_label("From").fill("2001-02-03")
    form.get_by_label("to").fill("2001-02-01")
    form.get_by_role("button", name="Set").click()
    # Days out of order set nothing.
    expect(form).to_be_visible()
    form.get_by_label("to").fill("2001-02-05")
    form.get_by_role("button", name="Set").click()
    expect(box.get_by_role("menu")).to_have_count(0)
    expect(box.locator("button.pick")).to_have_text("Feb 3 to Feb 5 2001▾")


# --- the chart -----------------------------------------------------------------------------

def test_the_chart_draws_a_bar_a_day_with_todays_value_in_words_and_each_value_on_hover(page):
    panel = open_page(page)
    chart = chart_of(panel)
    expect(chart).to_have_attribute("aria-label", "Estimated cost each day, last 30 days")
    expect(bars_of(panel)).to_have_count(3)
    expect(sentence_of(panel)).to_have_text(
        re.compile(rf"^The most in these 30 days was \w+( \d+)?, {MONEY}\. 27 days had no sessions\.$"))
    now = chart.locator("rect.bar.now")
    expect(now).to_have_count(1)
    value = chart.locator("[data-part=value]")
    expect(value).to_have_text(re.compile(rf"^{MONEY}$"))
    expect(chart.locator("g[data-period]").last.locator("title")).to_have_text(f"today: {value.text_content()}")
    assert bars_of(panel).locator("title").count() == 3
    expect(chart.locator("[data-part=top]")).to_have_text(re.compile(r"^\$[\d.]+$"))
    labels = chart.locator("text:not([data-part])").all_text_contents()
    assert labels[-1] == "today" and len(labels) >= 2, labels
    # Today's bar is told from the others by its weight, with no colour of its own.
    fills = chart.locator("rect.bar").evaluate_all(
        "bars => bars.map((bar) => [getComputedStyle(bar).fill, getComputedStyle(bar).fillOpacity])")
    assert len({fill for fill, _ in fills}) == 1, fills
    assert [opacity for _, opacity in fills] == ["0.36", "0.36", "1"], fills
    # Every period of the range is whole, so nothing is outlined and the foot says nothing.
    expect(chart.locator("rect.dash")).to_have_count(0)
    expect(panel.locator("[data-line=range]")).to_have_count(0)


def test_tokens_switches_the_bars_and_the_sentence(page):
    panel = open_page(page)
    panel.get_by_role("button", name="Tokens", exact=True).click()
    expect(panel.get_by_role("button", name="Tokens", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(chart_of(panel)).to_have_attribute("aria-label", "Tokens each day, last 30 days")
    expect(sentence_of(panel)).to_have_text(
        re.compile(r"^[\d.]+k tokens in these 30 days\. The most was \w+( \d+)?, [\d.]+k\.$"))
    expect(chart_of(panel).locator("[data-part=value]")).to_have_text(re.compile(r"^[\d.]+k$"))
    expect(bars_of(panel)).to_have_count(3)


def test_table_shows_every_figure_of_the_chart_in_its_place(page):
    panel = open_page(page)
    group = panel.locator("[data-group=chart]")
    group.get_by_role("button", name="Table").click()
    expect(chart_of(panel)).to_have_count(0)
    table = group.get_by_role("table")
    expect(table.locator("thead th")).to_have_text(["Day", SONNET, HAIKU, "Total"])
    expect(table.locator("tbody tr")).to_have_count(3)
    # A column per model, each day's total at its end, newest first.
    first = table.locator("tbody tr").first.locator("td").all_inner_texts()
    assert re.fullmatch(r"\w{3} \d{1,2}", first[0]) and first[2] == "", first
    assert first[1] == first[3] and re.fullmatch(MONEY, first[1]), first
    expect(sentence_of(panel)).to_have_text(re.compile(r"^The most in these 30 days"))

    panel.get_by_role("button", name="Tokens", exact=True).click()
    expect(table.locator("thead th")).to_have_text(
        ["Day", "Fresh input", "Cache read", "Cache write", "Output", "Total"])
    # Three requests of 1000, 1500 and 2000 output tokens.
    assert table.locator("tbody tr").first.locator("td").nth(4).inner_text() == "4.50k"

    group.get_by_role("button", name="Chart").click()
    expect(group.get_by_role("table")).to_have_count(0)
    expect(bars_of(panel)).to_have_count(3)


def test_a_range_whose_total_is_not_whole_outlines_its_day_and_says_why_at_the_foot(page):
    panel = open_page(page)
    pick(panel, "range", "Last 90 days")
    expect(bars_of(panel)).to_have_count(4)
    outlined = chart_of(panel).locator('g[data-whole="false"]')
    expect(outlined).to_have_count(1)
    expect(outlined.locator("title")).to_have_text(re.compile(r"^\w{3} \d{1,2}: no price, not whole$"))
    dash = outlined.locator("rect.dash")
    assert dash.evaluate("el => getComputedStyle(el).strokeDasharray") == "3px, 3px"
    assert dash.bounding_box()["height"] >= 4
    line = panel.locator("[data-part=lines] [data-line=range]")
    expect(line).to_have_text(re.compile(
        rf"^This range's total is not whole: (.+; )?[\d.]+k tokens have no price \({UNPRICED}\)$"))
    # The table says the same of the day.
    panel.locator("[data-group=chart]").get_by_role("button", name="Table").click()
    table = panel.locator("[data-group=chart]").get_by_role("table")
    expect(table.locator("thead th").last).to_have_text("Not whole")
    expect(table.locator("tbody tr").last.locator("td").last).to_have_text(
        re.compile(r"nothing here could be priced$"))
    # Back on a whole range the line goes.
    pick(panel, "range", "Last 7 days")
    expect(line).to_have_count(0)


def test_a_range_that_holds_nothing_says_so_in_the_charts_place(page):
    panel = open_page(page, "sessions")
    expect(panel.locator("section[data-part=view] tr[data-session]")).to_have_count(3)
    box = filter_of(panel, "range")
    box.locator("button.pick").click()
    box.get_by_role("menuitemradio", name="From … to …").click()
    box.get_by_label("From").fill("2001-02-01")
    box.get_by_label("to").fill("2001-02-02")
    box.get_by_role("button", name="Set").click()
    group = panel.locator("[data-group=chart]")
    expect(group.locator("[data-part=none]")).to_have_text("Nothing was used in this range.")
    expect(chart_of(panel)).to_have_count(0)
    expect(group.get_by_role("button")).to_have_count(0)
    # Cost, the filter line and the foot stay; nothing under the chart repeats the sentence.
    expect(panel.locator("[data-group=cost] [data-cost]")).to_have_count(3)
    expect(panel.locator("[data-part=foot]")).to_be_visible()
    expect(panel.get_by_text("Nothing was used in this range.")).to_have_count(1)
    expect(panel.locator("section[data-part=view] tr[data-session]")).to_have_count(0)


# --- the filters ---------------------------------------------------------------------------

def test_harness_filters_the_chart_and_the_menus_figures_and_not_cost(page):
    panel = open_page(page)
    before = cost_figures(panel)
    box = filter_of(panel, "harness")
    expect(box.locator(".k")).to_have_text("Harness")
    box.locator("button.pick").click()
    expect(box.get_by_role("menuitemradio")).to_have_text([re.compile(r"^✓\s*all$"), "Claude", "Codex"])
    with page.expect_request(lambda request: "/m/usage/api/daily?by=day" in request.url) as sent:
        box.get_by_role("menuitemradio", name="Codex").click()
    assert "harness=codex" in sent.value.url, sent.value.url
    expect(box.locator("button.pick")).to_have_text("Codex▾")
    expect(box.locator("button.pick")).to_have_class(re.compile(r"\bset\b"))
    # Codex's one thread is 50 days old.
    expect(panel.locator("[data-group=chart] [data-part=none]")).to_be_visible()
    filter_of(panel, "range").locator("button.pick").click()
    entries = filter_of(panel, "range").get_by_role("menuitemradio")
    expect(entries.nth(0)).to_have_text(re.compile(r"^Today\s*\$0\.00$"))
    expect(entries.nth(3)).to_have_text(re.compile(r"^Last 90 days\s*no price$"))
    entries.nth(3).click()
    expect(bars_of(panel)).to_have_count(1)
    assert cost_figures(panel) == before
    pick(panel, "harness", "all")
    expect(bars_of(panel)).to_have_count(4)


def test_model_lists_the_ranges_models_and_filters_to_one(page):
    panel = open_page(page)
    box = filter_of(panel, "model")
    expect(box.locator(".k")).to_have_text("Model")
    box.locator("button.pick").click()
    expect(box.get_by_role("menuitemradio")).to_have_text([re.compile(r"^✓\s*all$"), SONNET, HAIKU])
    with page.expect_request(lambda request: "/m/usage/api/daily?by=day" in request.url) as sent:
        box.get_by_role("menuitemradio", name=HAIKU).click()
    assert f"model={HAIKU}" in sent.value.url, sent.value.url
    expect(bars_of(panel)).to_have_count(1)
    expect(box.locator("button.pick")).to_have_text(f"{HAIKU}▾")
    # A range with one model has nothing to choose, so the filter shows only while set.
    pick(panel, "model", "all")
    pick(panel, "range", "Today")
    expect(box).to_have_count(0)


def test_project_offers_all_alone_when_no_session_has_a_project(page):
    panel = open_page(page)
    box = filter_of(panel, "place")
    expect(box.locator(".k")).to_have_text("Project")
    box.locator("button.pick").click()
    expect(box.get_by_role("menuitemradio")).to_have_text([re.compile(r"^✓\s*all$")])


def test_without_projects_the_filter_is_folder_and_filters_by_the_whole_path(small, ui_contexts):
    page = ui_contexts(small).pages[0]
    panel = open_page(page)
    box = filter_of(panel, "place")
    expect(box.locator(".k")).to_have_text("Folder")
    expect(bars_of(panel)).to_have_count(3)
    box.locator("button.pick").click()
    expect(box.get_by_role("menuitemradio")).to_have_text(
        [re.compile(r"^✓\s*all$"), "/work/proj-a", "/work/proj-b"])
    with page.expect_request(lambda request: "/m/usage/api/daily?by=day" in request.url) as sent:
        box.get_by_role("menuitemradio", name="/work/proj-b").click()
    assert "folder=%2Fwork%2Fproj-b" in sent.value.url, sent.value.url
    expect(box.locator("button.pick")).to_have_text("/work/proj-b▾")
    expect(bars_of(panel)).to_have_count(1)


def test_todays_views_read_under_the_pages_range_and_filters(page):
    panel = open_page(page, "sessions")
    rows = panel.locator("section[data-part=view] tr[data-session]")
    expect(rows).to_have_count(3)
    pick(panel, "range", "Last 7 days")
    expect(rows).to_have_count(2)
    pick(panel, "model", HAIKU)
    expect(rows).to_have_count(1)
    # The range and the filters outlive a placement of the page.
    panel.get_by_role("link", name="Models").click()
    expect(page).to_have_url(re.compile(r"/m/usage/models$"))
    expect(filter_of(panel, "range").locator("button.pick")).to_have_text("last 7 days▾")
    expect(filter_of(panel, "model").locator("button.pick")).to_have_text(f"{HAIKU}▾")
    expect(panel.locator("section[data-part=view] tr[data-model]")).to_have_count(1)


# --- the contract and a phone's width ------------------------------------------------------

def test_the_page_with_its_chart_keeps_the_contract_in_text_tokens(seeded, page, ui_component_check):
    check = ui_component_check(page, "usage", "page", {"route": ""}, tokens=("text", "text-dim"),
                               keep=True)
    expect(check.locator.locator("[data-group=chart] svg[data-part=chart]")).to_be_visible()
    for read in ("daily?by=day", "projects?", "models?"):
        assert any(f"/m/usage/api/{read}" in url for url in check.requests), check.requests


def test_the_filter_line_its_menu_and_the_chart_fit_a_phone(seeded, page, ui_width_check):
    ui_width_check(page, module="usage", route="")
    panel = page.locator("#moxi-panel > moxi-usage-page")
    expect(bars_of(panel)).to_have_count(3)
    for key in ("range", "place", "harness", "model"):
        expect(filter_of(panel, key)).to_be_visible()
    ui_width_check(page)
    # The chart takes the page's width and its last label ends inside it.
    chart = chart_of(panel).bounding_box()
    host = panel.bounding_box()
    assert chart["width"] == pytest.approx(host["width"], abs=1)
    filter_of(panel, "range").locator("button.pick").click()
    expect(filter_of(panel, "range").get_by_role("menuitemradio")).to_have_count(6)
    filter_of(panel, "range").get_by_role("menuitemradio", name="From … to …").click()
    expect(filter_of(panel, "range").get_by_role("button", name="Set")).to_be_visible()
    ui_width_check(page)
    page.keyboard.press("Escape")
    filter_of(panel, "model").locator("button.pick").click()
    expect(filter_of(panel, "model").get_by_role("menuitemradio")).to_have_count(3)
    ui_width_check(page)
    page.keyboard.press("Escape")
    panel.locator("[data-group=chart]").get_by_role("button", name="Table").click()
    expect(panel.locator("[data-group=chart]").get_by_role("table")).to_be_visible()
    ui_width_check(page)
