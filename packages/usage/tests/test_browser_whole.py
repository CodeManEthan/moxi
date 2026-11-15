"""usage's whole page in a browser, through a real shell that starts usage's real page
server, on the two installs the question walks use (`test_browser_questions.py`): a small
one whose shell leaves out every module but ui, usage and sessions, and a full one.

- Each address of the page opens its piece on both installs, and `projects` and
  `folders` each land on the section the install has.
- With every piece opened in turn (a limit row, each section, a session, a subagent,
  each `Table`, the Range menu, `Tokens`, `Details`, `Find a model` and both `Check
  against a bill`), the page has no heading of its own and no colour but the text, border
  and background tokens: no accent, no warning or error colour, no series colour.
- The range, the filters and the measure are kept across a trip to the Sessions page and
  Back, which places the page again at the session's address with it opened.
- A range that holds nothing under the filters set says so in the chart's place.
- Without projects, at a phone's width: Folders opened with its `Table`, and the Folder
  menu open.
- On shells of their own: an index another version of usage wrote, which only `moxi usage
  index` can bring up, so the page gives that command and no act; and a harness the last
  pass could not read, said in a dim line at the foot.

Each address on its own, the states with no index, an empty index, a pass running and a
range holding nothing, `All time`, `Tokens`, each `Table`, `Details`, both `Check against
a bill` and the phone width of the full install's pieces are held by the other browser
files; this one holds what they leave.
"""

from __future__ import annotations

import re
import sys
import time

import pytest
from playwright.sync_api import expect

from test_browser_questions import (AGENTS, CODEX, PANEL, READINGS, SONNET, THREAD, WEEK, filter_of,
                                    installs, key, limit, on_both, open_page, opened, pick, place_words,
                                    rows_of, run, section, session_id, settle, write)

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="usage's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

__all__ = ["installs", "on_both"]

TYPED_ID = WEEK[0]["session"]
#: The tokens a piece of the page may take a colour from: the text tokens, and the
#: neutral border and background ones. Every other colour token is refused.
ALLOWED = {"text", "text-dim", "text-faint", "border", "border-strong", "bg", "bg-raised",
           "bg-sunken", "bg-hover"}
WHAT_USAGE_DOES = ("Usage adds up what your sessions cost from the transcripts on this "
                   "machine, and shows your plan limits once a session has reported them.")

#: For each colour token the shell's style sheets define (fonts, radii and gaps are not
#: colours), how many nodes of the page change colour when that token alone changes on
#: the page's element. The walk goes into every shadow tree under the page, `moxi-ui-row`'s
#: too. Focus is taken off first: a focus ring is the browser's or the shell's, not the
#: page's.
REACHED = """async () => {
  const panel = document.querySelector("#moxi-panel > moxi-usage-page");
  function* tree(root) {
    for (const node of root.querySelectorAll("*")) {
      yield node;
      if (node.shadowRoot) yield* tree(node.shadowRoot);
    }
  }
  let focused = document.activeElement;
  while (focused?.shadowRoot?.activeElement) focused = focused.shadowRoot.activeElement;
  focused?.blur?.();
  const nodes = [panel, ...tree(panel.shadowRoot), ...tree(panel)];
  const props = ["color", "background-color", "background-image", "border-top-color",
                 "border-right-color", "border-bottom-color", "border-left-color", "outline-color",
                 "text-decoration-color", "box-shadow", "fill", "stroke", "stop-color"];
  const frames = () => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
  const colours = () => nodes.map((node) => {
    const style = getComputedStyle(node);
    return props.map((name) => style.getPropertyValue(name)).join("|");
  });
  const names = new Set();
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch { continue; }
    for (const rule of rules) {
      if (!rule.style) continue;
      for (const name of rule.style) if (name.startsWith("--moxi-")) names.add(name.slice(7));
    }
  }
  await frames();
  const before = colours();
  const reached = {};
  for (const token of [...names].sort()) {
    if (/^(font|radius|gap)/.test(token)) continue;
    panel.style.setProperty(`--moxi-${token}`, "rgb(1, 2, 3)");
    await frames();
    const after = colours();
    panel.style.removeProperty(`--moxi-${token}`);
    const changed = after.filter((seen, n) => seen !== before[n]).length;
    if (changed) reached[token] = changed;
  }
  await frames();
  return {reached, tokens: names.size, nodes: nodes.length};
}"""


def check_colours_and_headings(page, what: str) -> None:
    """The page as it stands has no heading and takes colour from the allowed tokens
    alone, and from the text tokens at least."""
    panel = page.locator(PANEL)
    expect(panel.get_by_role("heading")).to_have_count(0)
    seen = panel.evaluate(f"(el) => ({REACHED})()")
    assert seen["tokens"] > len(ALLOWED), seen
    refused = sorted(set(seen["reached"]) - ALLOWED)
    assert not refused, f"{what}: colour from {refused} ({seen['reached']})"
    assert {"text", "text-dim"} <= set(seen["reached"]), f"{what}: {seen['reached']}"


def addresses_of(shell) -> list[tuple[str, str, str]]:
    """Each address, the address the page keeps for it, and a piece only it shows."""
    place = place_words(shell)[1]
    return [
        ("", "", "[data-group=chart] svg[data-part=chart]"),
        ("limit/claude/plan/300", "limit/claude/plan/300", "[data-part=opened] [data-opened=window]"),
        ("limit/codex/codex/300", "limit/codex/codex/300", "[data-part=opened] [data-opened=window]"),
        ("sessions", "sessions", f'section[data-group=sessions] [data-list=first] moxi-ui-row[data-key="{key(WEEK[0])}"]'),
        (f"sessions/codex/{THREAD}", f"sessions/codex/{THREAD}",
         f'[data-list=placed] moxi-ui-row[open][data-key="{key(CODEX)}"] [data-part=session]'),
        (f"sessions/claude/{TYPED_ID}/{AGENTS[1]}", f"sessions/claude/{TYPED_ID}/{AGENTS[1]}",
         f'moxi-ui-row[data-agent="{AGENTS[1]}"] [data-part=agent] svg[data-part=session-chart]'),
        ("projects", place, f"section[data-group={place}] [data-part=rows] .tr2"),
        ("folders", place, f"section[data-group={place}] [data-part=rows] .tr2"),
        ("models", "models", f'section[data-group=models] [data-part=rows] [data-row="{SONNET}"]'),
        ("kinds", "kinds", "section[data-group=kinds] [data-part=rows] [data-row=cache_read]"),
        ("prices", "prices", f'section[data-group=prices] [data-part=price-table] [data-price^="{SONNET}|"]'),
    ]


def test_each_address_opens_its_piece_on_either_install(installs, on_both):
    def walk(page, shell) -> list:
        kept = []
        for route, keeps, shown in addresses_of(shell):
            panel = open_page(page, route)
            expect(panel.locator(shown).first).to_be_visible()
            expect(page).to_have_url(re.compile(rf"/m/usage/{re.escape(keeps)}$"))
            expect(panel.locator("[data-part=lines] p")).to_have_count(0)
            # One piece is open at a time.
            assert panel.locator("[data-part=opened]").count() == (1 if route.startswith("limit/") else 0)
            # What the dumps compare: the address kept as it was, or the install's place.
            kept.append("<place>" if keeps in ("projects", "folders") else "kept" if keeps == route else keeps)
        # The other install's name for the section is not drawn.
        other = "projects" if shell.small else "folders"
        expect(section(open_page(page, other), other)).to_have_count(0)
        return [kept]

    on_both(installs, walk)


def settled_page(page, route: str = ""):
    """The page at `route` once its filter line holds all four filters: a filter is
    absent until its list answers, and a menu opened meanwhile closes when it arrives."""
    panel = open_page(page, route)
    expect(panel.locator("[data-part=filters] [data-filter]")).to_have_count(4)
    return panel


def open_every_piece(page, shell):
    """Opens each piece of the page in turn, and after each yields what is open."""
    panel = settled_page(page)
    yield "the page"
    filter_of(panel, "range").locator("button.pick").click()
    expect(filter_of(panel, "range").get_by_role("menuitemradio", name=re.compile("^All time"))).to_be_visible()
    yield "the Range menu"
    page.keyboard.press("Escape")
    chart = section(panel, "chart")
    panel.locator("[data-part=measure]").get_by_role("button", name="Tokens").click()
    chart.get_by_role("button", name="Table").click()
    expect(chart.locator("table")).to_be_visible()
    yield "the chart's Table under Tokens"
    panel.locator("[data-part=measure]").get_by_role("button", name="Cost").click()
    for which in ("claude/plan/300", "codex/codex/300"):
        panel = open_page(page, f"limit/{which}")
        expect(opened(panel).locator("[data-opened=window]")).to_be_visible()
        yield f"the limit {which}"
    panel = open_page(page, "sessions")
    sessions = section(panel, "sessions")
    expect(rows_of(sessions)).to_have_count(10)
    sessions.locator("[data-more=first]").get_by_role("button", name="Show").click()
    sessions.locator("[data-more=earlier]").get_by_role("button", name="Show").click()
    expect(rows_of(sessions, "earlier")).to_have_count(2)
    sessions.get_by_role("button", name="Table").last.click()
    expect(sessions.locator("table[data-part=table]")).to_be_visible()
    yield "Sessions shown whole with its Table"
    panel = open_page(page, f"sessions/claude/{TYPED_ID}")
    body = panel.locator("[data-part=session]")
    acts = body.locator("[data-part=session-acts]")
    acts.get_by_role("button", name="Table").click()
    expect(body.locator("[data-part=session-table] table")).to_be_visible()
    yield "a session's Table"
    acts.get_by_role("button", name="Check against a bill").click()
    expect(body.locator("table[data-part=bill-lines]")).to_be_visible()
    yield "a session's bill"
    panel = open_page(page, f"sessions/claude/{TYPED_ID}/{AGENTS[1]}")
    agent = panel.locator("[data-part=agent]")
    expect(panel.locator("svg[data-part=agent-map]")).to_be_visible()
    agent.get_by_role("button", name="Table").click()
    expect(agent.locator("[data-part=agent-table] table")).to_be_visible()
    yield "a subagent's requests with their Table"
    for name in (place_words(shell)[1], "models"):
        panel = open_page(page, name)
        part = section(panel, name)
        part.locator("[data-part=table-toggle]").click()
        expect(part.locator("table[data-part=table]")).to_be_visible()
        yield f"{name} with its Table"
    panel = open_page(page, "kinds")
    expect(section(panel, "kinds").locator("[data-part=saving]")).to_be_visible()
    yield "Token kinds"
    for act, shows in (("Details", "[data-part=details]"), ("Find a model", "[data-part=find]"),
                       ("Check against a bill", "table[data-part=bill-lines]")):
        panel = open_page(page, "prices")
        prices = section(panel, "prices")
        prices.get_by_role("button", name=act).click()
        expect(prices.locator(shows)).to_be_visible()
        yield f"Prices with {act}"


def test_no_piece_of_the_page_draws_a_heading_or_a_colour_but_text_tokens(installs, ui_contexts):
    for shell in installs.values():
        page = ui_contexts(shell).pages[0]
        seen = 0
        for what in open_every_piece(page, shell):
            check_colours_and_headings(page, what)
            seen += 1
        assert seen == 15


def test_the_filters_are_kept_across_a_trip_to_sessions_and_back(installs, on_both):
    def walk(page, shell) -> list:
        panel = settled_page(page, "sessions")
        pick(panel, "range", "Last 7 days")
        expect(filter_of(panel, "range").locator("button.pick")).to_have_text("last 7 days▾")
        # The model list is read again under the new range; it is absent until it answers.
        expect(panel.locator("[data-part=filters] [data-filter]")).to_have_count(4)
        settle(panel, "model", SONNET)
        panel.locator("[data-part=measure]").get_by_role("button", name="Tokens").click()
        sessions = section(panel, "sessions")
        row = sessions.locator(f'moxi-ui-row[data-key="{key(WEEK[0])}"]')
        row.get_by_role("link", name="Open").click()
        body = row.locator("[data-part=session]")
        body.get_by_role("link", name="Open in Sessions").click()
        expect(page).to_have_url(re.compile(rf"/m/sessions/claude/{TYPED_ID}$"))
        expect(page.locator("#moxi-panel > moxi-sessions-page")).to_have_count(1)
        with page.expect_request(lambda request: "/m/usage/api/sessions?" in request.url) as sent:
            page.go_back()
        assert f"model={SONNET}" in sent.value.url and "since=" in sent.value.url, sent.value.url
        # Placed again at the session's address: the session opened, first in the
        # section, under the range, the filter and the measure that were set.
        expect(page).to_have_url(re.compile(rf"/m/usage/sessions/claude/{TYPED_ID}$"))
        panel = page.locator(PANEL)
        placed = section(panel, "sessions").locator("[data-list=placed] moxi-ui-row")
        expect(placed).to_have_attribute("data-key", key(WEEK[0]))
        expect(placed).to_have_attribute("open", "")
        expect(filter_of(panel, "range").locator("button.pick")).to_have_text("last 7 days▾")
        expect(filter_of(panel, "model").locator("button.pick")).to_have_text(f"{SONNET}▾")
        expect(panel.locator("[data-part=measure]").get_by_role("button", name="Tokens")).to_have_attribute(
            "aria-pressed", "true")
        return []

    on_both(installs, walk)


def test_a_range_with_nothing_under_the_filters_says_so_in_the_charts_place(installs, ui_contexts):
    page = ui_contexts(installs["full"]).pages[0]
    panel = settled_page(page)
    pick(panel, "harness", "Codex")
    expect(filter_of(panel, "harness").locator("button.pick")).to_have_text("Codex▾")
    pick(panel, "range", "Today")
    chart = section(panel, "chart")
    # The thread is three days old: nothing of Codex today.
    expect(chart.locator("[data-part=none]")).to_have_text("Nothing was used in this range.")
    expect(chart.locator("svg[data-part=chart]")).to_have_count(0)
    expect(panel.locator("[data-part=below] section[data-group]")).to_have_count(1)
    expect(panel.locator("[data-group=cost] [data-cost]")).to_have_count(3)
    expect(filter_of(panel, "harness").locator("button.pick")).to_have_text("Codex▾")
    expect(panel.locator("[data-part=foot]")).to_be_visible()


def test_without_projects_folders_and_the_folder_menu_fit_a_phone(installs, ui_contexts, ui_width_check):
    shell = installs["small"]
    page = ui_contexts(shell).pages[0]
    ui_width_check(page, module="usage", route="folders")
    panel = page.locator(PANEL)
    folders = section(panel, "folders")
    expect(folders.locator("[data-part=rows] .tr2 .nm").first).to_have_text("~/work/proj-a")
    folders.locator("[data-part=table-toggle]").click()
    expect(folders.locator("table[data-part=table]")).to_be_visible()
    ui_width_check(page)
    filter_of(panel, "place").locator("button.pick").click()
    expect(filter_of(panel, "place").get_by_role("menuitemradio", name="~/work/scratch")).to_be_visible()
    ui_width_check(page)


# --- shells of their own -------------------------------------------------------------------

#: Lowers the index's schema version by one, as if an older usage had written it.
OLDER = """
import sqlite3
from moxi_usage import store
conn = sqlite3.connect(store.index_path())
conn.execute(f"PRAGMA user_version = {store.INDEX_VERSION - 1}")
conn.commit()
conn.close()
"""

#: Puts a file where Codex's sessions folder belongs, so a pass cannot list it.
NOT_A_FOLDER = """
from pathlib import Path
from moxi_sessions import api as sessions
root = Path(sessions.root("codex"))
root.parent.mkdir(parents=True, exist_ok=True)
root.write_text("")
"""


def test_an_index_another_version_wrote_gives_the_command_and_keeps_the_limits(ui_shells, ui_contexts):
    shell = ui_shells()
    moment = time.time()
    write(shell, [WEEK[0]], moment)
    run(shell, sys.executable, "-m", "moxi", "usage", "index")
    run(shell, sys.executable, "-c", OLDER)
    run(shell, sys.executable, "-c", READINGS, repr(moment), session_id(1), THREAD)
    page = ui_contexts(shell).pages[0]
    page.goto("/m/usage/")
    panel = page.locator(PANEL)
    block = panel.locator("[data-part=nothing]")
    expect(block).to_have_attribute("data-state", "none")
    expect(block.locator(".quietline")).to_have_text("Nothing counted yet")
    # The block without its last sentence, and the command in place of the act.
    expect(block.locator(".first p").first).to_have_text(WHAT_USAGE_DOES)
    expect(block.locator("code")).to_have_text("moxi usage index")
    expect(block.get_by_role("button", name="Copy")).to_be_visible()
    expect(panel.get_by_role("button", name="Read new sessions")).to_have_count(0)
    # The limits still lead; Cost, the filter line, the sections and the foot are absent.
    expect(limit(panel, "claude/plan/300")).to_be_visible()
    for gone in ("[data-group=cost]", "[data-part=filters]", "[data-part=below]", "[data-part=foot]"):
        expect(panel.locator(gone)).to_have_count(0)


def test_a_harness_the_last_pass_could_not_read_is_said_at_the_foot(ui_shells, ui_contexts):
    shell = ui_shells()
    write(shell, [WEEK[0]], time.time())
    run(shell, sys.executable, "-c", NOT_A_FOLDER)
    run(shell, sys.executable, "-m", "moxi", "usage", "index")
    page = ui_contexts(shell).pages[0]
    page.goto("/m/usage/")
    panel = page.locator(PANEL)
    lines = panel.locator("[data-part=lines] p")
    expect(lines.filter(has_text=re.compile(r"^Codex not read: "))).to_have_text(
        [re.compile(r"^Codex not read: \w+, sessions root is not a directory \(unmounted\?\)$")])
    # The figures read stay, and the foot counts the files of the harness read: the
    # session and its two subagents.
    expect(panel.locator("[data-group=cost] [data-cost]")).to_have_count(3)
    expect(panel.locator("[data-part=foot] .last")).to_have_text(re.compile(r"· 3 files: Claude 3$"))
