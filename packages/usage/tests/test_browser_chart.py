"""The filter line and the chart of usage's page in a browser, through a real shell that
starts usage's real page server: the Range menu's entries and what each choice does to
the chart and leaves alone in Cost, the two days of a range of one's own, the Harness
and Model filters, the folder filter on a shell without projects, the chart's bars,
sentence, today's value, dashed outline and table by cost and by tokens, its bars by
cost stacked by token class or by model with the table following the split, the two
charts by tokens, the 53 weeks' heat map under the place filter alone, a range that
holds nothing, a range whose total is not whole with its line at the foot, a cost not
whole marked in its own cell in each table, the sections reading under the same range,
and all of it at a phone's width.

The transcripts are written, and the index built with `python -m moxi usage index`, in
the shell's own environment, so they sit under the shell's home where its page servers
read them. No test changes what the index holds, so the tests hold under pytest-xdist in
any order.
"""

from __future__ import annotations

import datetime
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
#: environment it runs in: three requests each, the newest a minute old and kept on today.
SEED = """
import json, sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for spec in json.loads(sys.argv[1]):
    start = (T.today_at(60, oldest_s=60, now=now) if spec["days_ago"] == 0
             else now - spec["days_ago"] * 86400 - 60)
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
        t.assistant("Done.", usage=T.usage(input_tokens=200, output=1000 + m * 500),
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
    # The part under the filter line is drawn once the chart's or a section's read answers.
    expect(panel.locator("[data-part=below]")).to_have_count(1)
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
    expect(sentence_of(panel)).to_have_count(0)
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
    value = chart.locator("[data-part=value]")
    expect(value).to_have_text(re.compile(rf"^{MONEY}$"))
    expect(chart.locator("g[data-period]").last.locator("title")).to_have_text(
        re.compile(rf"^today: {re.escape(value.text_content())}\n"))
    assert bars_of(panel).locator("title").count() == 3
    expect(chart.locator("[data-part=top]")).to_have_text(re.compile(r"^\$[\d.]+$"))
    labels = chart.locator("text:not([data-part])").all_text_contents()
    assert labels[-1] == "today" and len(labels) >= 2, labels
    # Today's bar is told from the others by its weight, with no colour of its own: each
    # class's part of it is heavier than the same class's part of the other days.
    expect(chart.locator("rect.seg.now")).not_to_have_count(0)
    fills = chart.locator("rect.seg").evaluate_all(
        """parts => parts.map((part) => [part.dataset.key, part.classList.contains("now"),
                                          getComputedStyle(part).fill, Number(getComputedStyle(part).fillOpacity)])""")
    assert len({fill for _, _, fill, _ in fills}) == 1, fills
    weight = {(key, now): opacity for key, now, _, opacity in fills}
    for key, now in weight:
        if now:
            assert weight[key, True] > weight.get((key, False), 0), fills
    # Every period of the range is whole, so nothing is outlined and the foot says nothing.
    expect(chart.locator("rect.dash")).to_have_count(0)
    expect(panel.locator("[data-line=range]")).to_have_count(0)


def test_by_cost_each_bar_is_stacked_by_token_class_and_its_parts_add_up_to_it(page):
    panel = open_page(page)
    chart = chart_of(panel)
    today = chart.locator("g[data-period]").last
    # Each request read 200 fresh input and 10 cache read tokens and wrote output; it
    # wrote no cache, so that class has no part.
    expect(today.locator("rect.seg")).to_have_count(3)
    keys = today.locator("rect.seg").evaluate_all("parts => parts.map((part) => part.dataset.key)")
    assert keys == ["input", "cache_read", "output"], keys
    legend = panel.locator("[data-group=chart] [data-part=legend] li")
    expect(legend).to_have_text(["Input", "Cache create", "Cache read", "Output"])
    # The hover gives each class's dollars, and they add up to the day's own figure.
    lines = today.locator("title").text_content().split("\n")
    assert re.fullmatch(rf"today: {MONEY}", lines[0]), lines
    assert [line.rsplit(" ", 1)[0] for line in lines[1:]] == [
        "Input", "Cache create", "Cache read", "Output"], lines

    def dollars(text: str) -> float:
        return 0.0 if text.endswith("<$0.01") else float(text.rsplit("$", 1)[1].replace(",", ""))
    assert abs(sum(dollars(line) for line in lines[1:]) - dollars(lines[0])) <= 0.02, lines
    # The parts fill the bar, the lowest on the axis line.
    tops = today.locator("rect.seg").evaluate_all(
        "parts => parts.map((part) => [part.y.baseVal.value, part.height.baseVal.value])")
    assert tops[-1][0] < tops[0][0] and tops[0][0] + tops[0][1] == pytest.approx(84, abs=0.2), tops


def test_by_model_each_bar_is_stacked_by_model_and_the_table_follows_the_split(page):
    panel = open_page(page)
    split = panel.locator("[data-part=split]")
    by_class = split.get_by_role("button", name="By class")
    by_model = split.get_by_role("button", name="By model")
    expect(by_class).to_have_attribute("aria-pressed", "true")
    by_model.click()
    expect(by_model).to_have_attribute("aria-pressed", "true")
    expect(by_class).to_have_attribute("aria-pressed", "false")
    chart = chart_of(panel)
    expect(chart).to_have_attribute("aria-label", "Estimated cost by model each day, last 30 days")
    group = panel.locator("[data-group=chart]")
    expect(group.locator("[data-part=legend] li")).to_have_text([SONNET, HAIKU])
    expect(group.locator("[data-part=chart-name]")).to_have_text("Daily estimated cost — by model")
    expect(group.locator("[data-part=caption]")).to_have_text("Per-model dollars, straight from the index.")
    # Today's session ran on Sonnet alone; the hover names every model of the range.
    today = chart.locator("g[data-period]").last
    expect(today.locator("rect.seg")).to_have_count(1)
    assert today.locator("rect.seg").get_attribute("data-key") == SONNET
    lines = today.locator("title").text_content().split("\n")
    assert re.fullmatch(rf"today: ({MONEY})", lines[0]), lines
    assert lines[1:] == [f"{SONNET} {lines[0].split(': ')[1]}", f"{HAIKU} $0.00"], lines

    group.get_by_role("button", name="Table").click()
    table = group.get_by_role("table")
    expect(table.locator("thead th")).to_have_text(["Day", SONNET, HAIKU])
    # Today ran on Sonnet alone: its Haiku cell reads $0.00.
    expect(table.locator("tbody tr").last.locator("td")).to_have_text(
        [re.compile(r"^\d{4}-\d{2}-\d{2}$"), re.compile(rf"^{MONEY}$"), "$0.00"])
    # The split draws the chart again, its chart and not its table; its table follows it.
    by_class.click()
    expect(table).to_have_count(0)
    group.get_by_role("button", name="Table").click()
    expect(table.locator("thead th")).to_have_text(["Day", "Input", "Cache create", "Cache read", "Output"])

    # Under Tokens the split is by class, and both buttons are off.
    panel.get_by_role("button", name="Tokens", exact=True).click()
    for button in (by_class, by_model):
        expect(button).to_be_disabled()
        expect(button).to_have_attribute("title", "Token mode always splits by class")


def test_tokens_draws_the_cache_reads_alone_and_the_billable_work_stacked(page):
    panel = open_page(page)
    panel.get_by_role("button", name="Tokens", exact=True).click()
    expect(panel.get_by_role("button", name="Tokens", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(sentence_of(panel)).to_have_text(
        re.compile(r"^[\d.]+k tokens in these 30 days\. The most was \w+( \d+)?, [\d.]+k\.$"))
    group = panel.locator("[data-group=chart]")
    expect(group.locator("[data-part=chart-name]")).to_have_text(["Cache reads", "Billable work"])
    expect(chart_of(panel)).to_have_count(2)
    cache, billable = chart_of(panel).nth(0), chart_of(panel).nth(1)
    expect(cache).to_have_attribute("aria-label", "Cache read tokens each day, last 30 days")
    expect(billable).to_have_attribute("aria-label", "Billable tokens each day, last 30 days")
    expect(group.locator("[data-part=caption]")).to_have_text([
        re.compile(r"^[\d.]+% of all tokens in this range\. Shown alone because a single stack would be one shade\.$"),
        re.compile(r"^Input \+ cache writes \+ output: the [\d.]+% of tokens that is not a cache read\.$")])
    # Ten cache-read tokens a request: today's three requests read 30.
    expect(cache.locator("g[data-period]")).to_have_count(3)
    expect(cache.locator("rect.seg")).to_have_count(0)
    expect(cache.locator("[data-part=value]")).to_have_text("30")
    # The billable work: 200 fresh input and 1000 to 2000 output a request, stacked.
    expect(billable.locator("g[data-period]")).to_have_count(3)
    today = billable.locator("g[data-period]").last
    expect(today.locator("rect.seg")).to_have_count(2)
    expect(today.locator("title")).to_have_text("today: 5.10k\nInput 600\nCache create 0\nOutput 4.50k")
    expect(billable.locator("[data-part=value]")).to_have_text("5.10k")
    expect(group.locator("[data-part=legend] li")).to_have_text(["Input", "Cache create", "Output"])


def test_each_chart_has_its_own_table_with_every_figure_in_its_place(page):
    panel = open_page(page)
    group = panel.locator("[data-group=chart]")
    expect(group.locator("[data-part=chart-name]")).to_have_text("Daily estimated cost — by class")
    expect(group.locator("[data-part=caption]")).to_have_count(0)
    group.get_by_role("button", name="Table").click()
    expect(chart_of(panel)).to_have_count(0)
    table = group.get_by_role("table")
    expect(table.locator("thead th")).to_have_text(["Day", "Input", "Cache create", "Cache read", "Output"])
    expect(table.locator("tbody tr")).to_have_count(3)
    # The day, then a column per token class, and no total; oldest first.
    keys = [row.get_attribute("data-row") for row in table.locator("tbody tr").all()]
    assert keys == sorted(keys), keys
    last = table.locator("tbody tr").last.locator("td")
    expect(last).to_have_count(5)
    expect(last.first).to_have_text(re.compile(r"^\d{4}-\d{2}-\d{2}$"))
    expect(last.nth(1)).to_have_text(re.compile(MONEY))
    expect(sentence_of(panel)).to_have_text(re.compile(r"^The most in these 30 days"))

    # The cost chart's table keeps its name.
    expect(group.locator("[data-part=chart-name]")).to_have_text("Daily estimated cost — by class")

    panel.get_by_role("button", name="Tokens", exact=True).click()
    # Each chart has its own Table under its name, and the charts by tokens are drawn
    # whatever the chart by cost showed.
    expect(group.locator("[data-part=chart-name]")).to_have_text(["Cache reads", "Billable work"])
    expect(chart_of(panel)).to_have_count(2)
    expect(group.get_by_role("table")).to_have_count(0)
    reads_head, billable_head = group.locator('[data-chart="Cache reads"]'), group.locator('[data-chart="Billable work"]')
    expect(reads_head.get_by_role("button")).to_have_text("Table")
    expect(billable_head.get_by_role("button")).to_have_text("Table")
    # The cache reads' Table switches it alone.
    reads_head.get_by_role("button", name="Table").click()
    tables = group.get_by_role("table")
    expect(tables).to_have_count(1)
    expect(chart_of(panel)).to_have_count(1)
    expect(chart_of(panel)).to_have_attribute("aria-label", "Billable tokens each day, last 30 days")
    expect(tables.locator("thead th")).to_have_text(["Day", "Cache read"])
    expect(billable_head.get_by_role("button")).to_have_text("Table")
    billable_head.get_by_role("button", name="Table").click()
    expect(tables).to_have_count(2)
    expect(chart_of(panel)).to_have_count(0)
    reads, billable = tables.nth(0), tables.nth(1)
    expect(reads.locator("thead th")).to_have_text(["Day", "Cache read"])
    expect(billable.locator("thead th")).to_have_text(["Day", "Input", "Cache create", "Output"])
    expect(reads.locator("tbody tr")).to_have_count(3)
    expect(billable.locator("tbody tr")).to_have_count(3)
    # Today's three requests read ten cache tokens each and wrote 1000, 1500 and 2000
    # output tokens, in the last row.
    expect(reads.locator("tbody tr").last.locator("td")).to_have_text([re.compile(r"^\d{4}-\d{2}-\d{2}$"), "30"])
    expect(billable.locator("tbody tr").last.locator("td").nth(3)).to_have_text("4.50k")

    # Chart brings the cache reads back and leaves the billable work's table.
    reads_head.get_by_role("button", name="Chart").click()
    expect(tables).to_have_count(1)
    expect(tables.locator("thead th")).to_have_text(["Day", "Input", "Cache create", "Output"])
    expect(chart_of(panel)).to_have_attribute("aria-label", "Cache read tokens each day, last 30 days")
    expect(chart_of(panel).locator("g[data-period]")).to_have_count(3)
    # Back by cost the chart is drawn again, its chart and not its table.
    panel.get_by_role("button", name="Cost", exact=True).click()
    expect(chart_of(panel)).to_have_count(1)
    expect(group.get_by_role("table")).to_have_count(0)


def test_every_chart_shows_its_chart_again_each_time_it_is_drawn_again(page):
    panel = open_page(page)
    group, heat = panel.locator("[data-group=chart]"), heat_of(panel)
    expect(bars_of(panel)).to_have_count(3)

    def tables_shown() -> None:
        group.get_by_role("button", name="Table").click()
        heat.get_by_role("button", name="Table").click()
        expect(chart_of(panel)).to_have_count(0)
        expect(heat.locator("svg[data-part=heat]")).to_have_count(0)

    def charts_shown() -> None:
        expect(chart_of(panel)).to_have_count(1)
        expect(group.get_by_role("table")).to_have_count(0)
        expect(heat.locator("svg[data-part=heat]")).to_be_visible()
        expect(heat.get_by_role("table")).to_have_count(0)

    # A new range.
    tables_shown()
    pick(panel, "range", "Last 7 days")
    charts_shown()
    # A new split.
    tables_shown()
    panel.locator("[data-part=split]").get_by_role("button", name="By model").click()
    charts_shown()
    # A new address: a section opened.
    tables_shown()
    panel.locator("[data-group=models] [data-part=count]").click()
    expect(page).to_have_url(re.compile(r"/m/usage/models$"))
    charts_shown()
    # A new measure: each chart by tokens shows its chart, whatever the others showed.
    panel.get_by_role("button", name="Tokens", exact=True).click()
    group.locator('[data-chart="Cache reads"]').get_by_role("button", name="Table").click()
    expect(chart_of(panel)).to_have_count(1)
    panel.get_by_role("button", name="Cost", exact=True).click()
    charts_shown()
    panel.get_by_role("button", name="Tokens", exact=True).click()
    expect(chart_of(panel)).to_have_count(2)
    expect(group.get_by_role("table")).to_have_count(0)


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
    # The table marks the day, the oldest, in its first row's Day cell, with no column
    # of its own.
    panel.locator("[data-group=chart]").get_by_role("button", name="Table").click()
    table = panel.locator("[data-group=chart]").get_by_role("table")
    expect(table.locator("thead th")).to_have_text(["Day", "Input", "Cache create", "Cache read", "Output"])
    rows = table.locator("tbody tr")
    expect_marked(rows.first.locator("td").first, "nothing here could be priced")
    expect(rows.locator("[data-part=not-whole]")).to_have_count(1)
    # Back on a whole range the line goes.
    pick(panel, "range", "Last 7 days")
    expect(line).to_have_count(0)


def expect_marked(cell, why: str) -> None:
    """A figure not whole: `*` on screen after it, `why` (its last reason, a pattern) on hover
    and in the cell's name."""
    mark = cell.locator("[data-part=not-whole]")
    expect(mark.locator("[aria-hidden=true]")).to_have_text("*")
    expect(mark).to_have_attribute("title", re.compile(rf"^Not whole: (.+; )?{why}$"))
    expect(cell).to_have_accessible_name(re.compile(rf"^\S.*, not whole: (.+; )?{why}$"))
    # The words are for a screen reader only: on screen they take no room.
    box = mark.locator(".vh").bounding_box()
    assert box is not None and box["width"] <= 1 and box["height"] <= 1, box


def test_a_cost_not_whole_is_marked_in_its_cost_cell_in_every_table(page):
    unpriced = rf"[\d.]+k? tokens have no price \({UNPRICED}\)"
    panel = open_page(page, "sessions")
    pick(panel, "range", "Last 90 days")
    sessions = panel.locator("table[data-table=sessions]")
    expect(sessions.locator("tbody tr[data-row]")).to_have_count(4)
    expect(sessions.locator("thead th").last).to_have_text(re.compile(r"^Est\. cost\b"))
    thread = sessions.locator(f'tbody tr[data-row="codex/{THREAD}"]')
    expect_marked(thread.locator("td").last, "nothing here could be priced")
    expect(sessions.locator("[data-part=not-whole]")).to_have_count(1)

    panel = open_page(page, "projects")
    pick(panel, "range", "Last 90 days")
    projects = panel.locator("section[data-group=projects]")
    projects.locator("[data-part=table-toggle]").click()
    table = projects.locator("table[data-part=table]")
    expect(table.locator("thead th")).to_have_text(["Project", "Folder", "Models", "Requests", "Tokens", "Est. cost"])
    expect_marked(table.locator("tbody tr").filter(has_text="proj-b").locator("td").last, unpriced)
    expect(table.locator("tbody tr").filter(has_text="proj-a").locator("[data-part=not-whole]")).to_have_count(0)

    panel = open_page(page, "models")
    pick(panel, "range", "Last 90 days")
    models = panel.locator("section[data-group=models]")
    models.locator("[data-part=table-toggle]").click()
    table = models.locator("table[data-part=table]")
    expect(table.locator("thead th")).to_have_text(["Model", "Priced by", "Requests", "Tokens", "Est. cost"])
    expect_marked(table.locator("tbody tr").filter(has_text=UNPRICED).locator("td").last,
                  "nothing here could be priced")
    expect(table.locator("[data-part=not-whole]")).to_have_count(1)

    panel = open_page(page, "prices")
    pick(panel, "range", "Last 90 days")
    panel.get_by_role("button", name="Check against a bill").click()
    bill = panel.locator("table[data-part=bill-lines]")
    expect(bill.locator("thead th").last).to_have_text("Est. cost")
    expect_marked(bill.locator(f'tr[data-model="{UNPRICED}"] td').last, "not priced: .+")
    expect_marked(bill.locator("tr[data-line=total] td").last, f"{unpriced}, so the cost is the priced part")
    expect(bill.locator(f'tr[data-model="{SONNET}"] [data-part=not-whole]')).to_have_count(0)


def test_a_range_that_holds_nothing_says_so_in_the_charts_place(page):
    panel = open_page(page, "sessions")
    expect(panel.locator("section[data-group=sessions] table[data-table=sessions] tbody tr[data-row]")).to_have_count(3)
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
    expect(panel.locator("section[data-group=sessions]")).to_have_count(0)


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


def test_project_offers_all_projects_alone_when_no_session_has_a_project(page):
    panel = open_page(page)
    box = filter_of(panel, "place")
    expect(box.locator(".k")).to_have_text("Project")
    expect(box.locator("button.pick")).to_have_text("All projects▾")
    box.locator("button.pick").click()
    expect(box.get_by_role("menuitemradio")).to_have_text([re.compile(r"^✓\s*All projects$")])


def test_without_projects_the_filter_is_dir_and_filters_by_the_whole_path(small, ui_contexts):
    page = ui_contexts(small).pages[0]
    panel = open_page(page)
    box = filter_of(panel, "place")
    expect(box.locator(".k")).to_have_text("Folder")
    expect(bars_of(panel)).to_have_count(3)
    expect(box.locator("button.pick")).to_have_text("All folders▾")
    box.locator("button.pick").click()
    expect(box.get_by_role("menuitemradio")).to_have_text([re.compile(r"^✓\s*All folders$")])
    expect(box.get_by_role("menuitemcheckbox")).to_have_text(["/work/proj-a", "/work/proj-b"])
    with page.expect_request(lambda request: "/m/usage/api/daily?by=day" in request.url) as sent:
        box.get_by_role("menuitemcheckbox", name="/work/proj-b").click()
    assert "folder=%2Fwork%2Fproj-b" in sent.value.url, sent.value.url
    expect(box.locator("button.pick")).to_have_text("1 folder▾")
    expect(bars_of(panel)).to_have_count(1)


def test_the_place_filter_picks_several_and_reads_them_one_to_a_line(small, ui_contexts):
    page = ui_contexts(small).pages[0]
    panel = open_page(page)
    box = filter_of(panel, "place")
    expect(bars_of(panel)).to_have_count(3)
    box.locator("button.pick").click()
    entries = box.get_by_role("menuitemcheckbox")
    expect(entries).to_have_text(["/work/proj-a", "/work/proj-b"])
    with page.expect_request(lambda request: "/m/usage/api/daily?by=day" in request.url):
        entries.filter(has_text="/work/proj-b").click()
    # The menu stays open to pick another.
    expect(entries.filter(has_text="/work/proj-b")).to_have_attribute("aria-checked", "true")
    expect(bars_of(panel)).to_have_count(1)
    with page.expect_request(lambda request: "/m/usage/api/daily?by=day" in request.url) as sent:
        entries.filter(has_text="/work/proj-a").click()
    assert "folder=%2Fwork%2Fproj-b%0A%2Fwork%2Fproj-a" in sent.value.url, sent.value.url
    expect(box.locator("button.pick")).to_have_text("2 folders▾")
    expect(entries.and_(page.locator("[aria-checked=true]"))).to_have_count(2)
    expect(bars_of(panel)).to_have_count(3)
    # Picking one again takes it away; `All folders` clears them and closes the menu.
    entries.filter(has_text="/work/proj-a").click()
    expect(box.locator("button.pick")).to_have_text("1 folder▾")
    box.get_by_role("menuitemradio", name=re.compile(r"All folders$")).click()
    expect(box.get_by_role("menu")).to_have_count(0)
    expect(box.locator("button.pick")).to_have_text("All folders▾")
    expect(bars_of(panel)).to_have_count(3)


def test_the_sections_read_under_the_pages_range_and_filters(page):
    panel = open_page(page, "sessions")
    rows = panel.locator("section[data-group=sessions] table[data-table=sessions] tbody tr[data-row]")
    # Over 30 days the table holds all three; over 7, the week's two.
    expect(rows).to_have_count(3)
    pick(panel, "range", "Last 7 days")
    expect(rows).to_have_count(2)
    pick(panel, "model", HAIKU)
    expect(rows).to_have_count(1)
    # The range and the filters outlive a placement of the page.
    page.evaluate("async () => (await import('/m/ui/client.js')).navigate('usage', 'models')")
    expect(page).to_have_url(re.compile(r"/m/usage/models$"))
    expect(filter_of(panel, "range").locator("button.pick")).to_have_text("last 7 days▾")
    expect(filter_of(panel, "model").locator("button.pick")).to_have_text(f"{HAIKU}▾")
    expect(panel.locator("section[data-group=models] [data-part=rows] .tr2")).to_have_count(1)


# --- the heat map ----------------------------------------------------------------------------

def heat_of(panel):
    return panel.locator("[data-group=heat]")


def test_the_heat_map_holds_53_weeks_whatever_the_range_and_a_day_picks_its_range(page):
    panel = open_page(page)
    heat = heat_of(panel)
    expect(heat.locator(".gk")).to_have_text("53 weeks")
    expect(heat.locator("[data-part=chart-name]")).to_have_text("Daily estimated cost — trailing 53 weeks")
    expect(heat.locator("[data-part=sentence]")).to_have_text(re.compile(
        r"^Trailing 53 weeks, independent of the range filter · days in \S+\. Click a day to filter to it\.$"))
    grid = heat.locator("svg[data-part=heat]")
    expect(grid).to_have_attribute("aria-label", "Estimated cost per day, trailing 53 weeks")
    # Every one of the 371 days is a button, 11 px cells 13 px apart whatever the width;
    # the five sessions fall on five of them.
    days = grid.get_by_role("button")
    expect(days).to_have_count(371)
    assert {days.first.get_attribute("width"), days.last.get_attribute("width")} == {"11"}
    assert grid.get_attribute("width") in {str(22 + 53 * 13), str(22 + 54 * 13)}
    used = grid.locator("rect[data-used=true]")
    expect(used).to_have_count(5)
    expect(grid.locator("text")).to_contain_text(["M", "W", "F"])
    expect(heat.locator("[data-part=scale]")).to_have_text("lessmore")
    # It does not follow the range.
    pick(panel, "range", "Last 7 days")
    expect(chart_of(panel).locator("g[data-period]")).to_have_count(2)
    expect(used).to_have_count(5)
    # A day in the range names its classes on hover; one outside it, its cost and tokens.
    expect(used.last.locator("title")).to_have_text(
        re.compile(r"^\d{4}-\d{2}-\d{2}: \d+\.\d{2} USD, [\d.]+k tokens\nInput 600\nCache create 0\n"
                   r"Cache read 30\nOutput 4\.50k$"))
    oldest = used.first
    day = oldest.get_attribute("data-day")
    expect(oldest).to_have_attribute("aria-label", re.compile(rf"^{day}: \d+\.\d{{2}} USD$"))
    expect(oldest.locator("title")).to_have_text(re.compile(rf"^{day}: \d+\.\d{{2}} USD, [\d.]+k tokens$"))
    expect(days.first).to_have_attribute("aria-label", f"{days.first.get_attribute('data-day')}: 0.00 USD")
    # A day nothing was used on says its cost and tokens, and in the range its classes at 0.
    expect(days.first.locator("title")).to_have_text(f"{days.first.get_attribute('data-day')}: 0.00 USD, 0 tokens")
    idle = grid.locator("rect[data-used=false]").last
    expect(idle.locator("title")).to_have_text(
        f"{idle.get_attribute('data-day')}: 0.00 USD, 0 tokens\nInput 0\nCache create 0\nCache read 0\nOutput 0")
    # Picking a day sets the range to it.
    when = datetime.date.fromisoformat(day)
    date, year = f"{when:%b} {when.day}", when.year
    oldest.click()
    # The range names the day's year only when it is not this year.
    expect(filter_of(panel, "range").locator("button.pick")).to_have_text(
        re.compile(rf"^{date}( {year})?▾$"))
    expect(chart_of(panel).locator("g[data-period]")).to_have_count(1)
    expect(chart_of(panel).locator("g[data-period]")).to_have_attribute(
        "data-period", oldest.get_attribute("data-day"))
    # A day is reached by the keyboard too: Enter on the newest sets the range to it.
    newest = used.last.get_attribute("data-day")
    used.last.focus()
    page.keyboard.press("Enter")
    expect(chart_of(panel).locator("g[data-period]")).to_have_attribute("data-period", newest)
    # A day nothing was used on is a button as well.
    days.first.click()
    expect(panel.locator("[data-group=chart] [data-part=none]")).to_have_text("Nothing was used in this range.")
    expect(used).to_have_count(5)


class Held:
    """Reads whose address matches `pattern`, held back until `release()`; later ones go
    through."""

    def __init__(self, page, pattern: str):
        self.routes = []
        self.released = False
        page.route(re.compile(pattern), self._hold)

    def _hold(self, route) -> None:
        if self.released:
            route.continue_()
        else:
            self.routes.append(route)

    def release(self) -> None:
        self.released = True
        for route in self.routes:
            route.continue_()


def test_a_picked_day_keeps_all_under_the_filter_line_drawn_and_faded_until_its_reads_answer(page):
    panel = open_page(page)
    below = panel.locator("[data-part=below]")
    grid = heat_of(panel).locator("svg[data-part=heat]")
    newest = grid.locator("rect[data-used=true]").last
    expect(newest).to_be_visible()
    expect(bars_of(panel)).to_have_count(3)
    expect(below).to_have_css("opacity", "1")
    day = newest.get_attribute("data-day")
    # The chart's read for the day, and the map's own, which starts on its first day, are
    # held back after the pick.
    first = grid.get_by_role("button").first.get_attribute("data-day")
    chart_read = Held(page, rf"/m/usage/api/daily\?by=day&since={day}&until={day}")
    heat_read = Held(page, rf"/m/usage/api/daily\?by=day&since={first}&")
    newest.focus()
    page.keyboard.press("Enter")
    expect(filter_of(panel, "range").locator("button.pick")).not_to_have_text("last 30 days▾")
    # While the reads are out everything under the filter line stays drawn, faded as one,
    # the last chart and map with it, and the day keeps the focus.
    expect(below).to_have_css("opacity", "0.6")
    expect(heat_of(panel).locator(".gl")).to_have_css("opacity", "1")
    expect(bars_of(panel)).to_have_count(3)
    expect(grid.locator("rect[data-used=true]")).to_have_count(5)
    expect(panel.locator("[data-group=sessions] [data-part=count]")).to_be_visible()
    expect(newest).to_be_focused()
    assert chart_read.routes and heat_read.routes
    chart_read.release()
    # The chart is drawn from its answer; the map's read is still out.
    expect(bars_of(panel)).to_have_count(1)
    expect(bars_of(panel)).to_have_attribute("data-period", day)
    expect(below).to_have_css("opacity", "0.6")
    heat_read.release()
    # Then the map is drawn anew from its read, and the day's new cell does not hold the
    # focus the old one had.
    expect(below).to_have_css("opacity", "1")
    cell = grid.locator(f'rect[data-day="{day}"]')
    expect(cell).to_be_visible()
    expect(cell).not_to_be_focused()


def test_the_heat_map_follows_only_the_place_filter_and_its_table_lists_the_used_days(small, ui_contexts):
    page = ui_contexts(small).pages[0]
    panel = open_page(page)
    heat = heat_of(panel)
    used = heat.locator("svg[data-part=heat] rect[data-used=true]")
    expect(used).to_have_count(5)
    # Harness and Model leave it as it is.
    pick(panel, "harness", "Codex")
    expect(panel.locator("[data-group=chart] [data-part=none]")).to_have_text("Nothing was used in this range.")
    expect(used).to_have_count(5)
    pick(panel, "harness", "all")
    # A folder filters it: proj-b's two sessions, two days and fifty days ago.
    box = filter_of(panel, "place")
    box.locator("button.pick").click()
    box.get_by_role("menuitemcheckbox", name="/work/proj-b").click()
    expect(box.locator("button.pick")).to_have_text("1 folder▾")
    expect(used).to_have_count(2)
    page.keyboard.press("Escape")
    heat.get_by_role("button", name="Table").click()
    expect(heat.locator("svg[data-part=heat]")).to_have_count(0)
    table = heat.get_by_role("table")
    expect(table.locator("thead th")).to_have_text(["Day", "Est. cost", "Tokens"])
    expect(table.locator("tbody tr")).to_have_count(2)
    # Oldest first: the Codex thread, whose model has no price, then the Haiku session.
    expect(table.locator("tbody tr").locator("td:first-child")).to_have_text(
        [re.compile(r"^\d{4}-\d{2}-\d{2}$")] * 2)
    expect(table.locator("tbody tr").first.locator("td").nth(1)).to_have_text("no price")
    expect(table.locator("tbody tr").last.locator("td").nth(1)).to_have_text(re.compile(rf"^{MONEY}$"))
    heat.get_by_role("button", name="Chart").click()
    expect(heat.locator("svg[data-part=heat]")).to_be_visible()


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
