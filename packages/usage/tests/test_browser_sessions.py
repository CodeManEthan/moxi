"""The Sessions section of usage's page in a browser, through a real shell that starts
usage's real page server: the closed row naming the range's dearest session with the
count that opens it; opened, the week's ten dearest as two-line rows, the line for the
rest of the week and its `Show`, the earlier days' line and its `Show`, the table, a
row's `Open` and the address it writes; a range of seven days or fewer with no earlier
line; Cost's `most to` naming today's two dearest; and the section at a phone's width.

The transcripts are written and the index built in the shell's own environment. No test
changes what the index holds, so the tests hold under pytest-xdist in any order.
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
MONEY = r"(at least )?\$\d[\d,]*\.\d\d"
#: A time as the browser's locale writes it.
TIME = r"\d\d?:\d\d(\s?[AP]M)?"


def session_id(n: int) -> str:
    return f"0190b000-0000-7000-8000-0000000001{n:02d}"


#: Fourteen sessions this week, dearest first, the dearest with a typed name; then two
#: more twelve and twenty days ago. Each costs less than the one before it.
WEEK = [{"session": session_id(n), "folder": "proj-a" if n % 2 == 0 else "proj-b",
         "hours_ago": 0.02 * (n + 1) if n < 2 else 2 + 5 * n, "requests": 16 - n,
         "title": "fix the tests" if n == 0 else None} for n in range(14)]
EARLIER = [{"session": session_id(20 + n), "folder": "proj-b", "hours_ago": 24 * days,
            "requests": 3 - n, "title": None} for n, days in enumerate((12, 20))]

#: Writes the sessions of the JSON list in argv[1] under the harness roots of the
#: environment it runs in, each of its own number of requests.
SEED = """
import json, sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for spec in json.loads(sys.argv[1]):
    cwd = "/work/" + spec["folder"]
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=spec["session"],
                           project=cwd.replace("/", "-"), cwd=cwd,
                           start=now - spec["hours_ago"] * 3600)
    if spec["title"]:
        t.title(spec["title"])
    for m in range(spec["requests"]):
        t.user("Fix the failing test.")
        t.assistant("Done.", usage=T.usage(input=200, output=1000),
                    request_id=f"req_{spec['session'][-3:]}{m}", model=sys.argv[2])
    t.write()
"""


def run(shell, *argv: str) -> str:
    done = subprocess.run(list(argv), env=shell.env, capture_output=True, text=True,
                          timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done.stdout


@pytest.fixture(scope="module")
def shell(ui_shells, ui_shell_options):
    shell = ui_shells(**ui_shell_options)
    run(shell, sys.executable, "-c", SEED, json.dumps(WEEK + EARLIER), MODEL)
    run(shell, sys.executable, "-m", "moxi", "usage", "index")
    return shell


@pytest.fixture
def page(shell, ui_contexts):
    return ui_contexts(shell).pages[0]


def open_page(page, route: str = ""):
    page.goto(f"/m/usage/{route}")
    panel = page.locator("#moxi-panel > moxi-usage-page")
    expect(panel.locator("[data-part=filters]")).to_be_visible()
    return panel


def sessions_of(panel):
    return panel.locator("[data-part=below] section[data-group=sessions]")


def rows_of(section, which: str = "first"):
    return section.locator(f"[data-list={which}] moxi-ui-row")


def keys_of(rows) -> list[str]:
    return rows.evaluate_all("nodes => nodes.map((node) => node.dataset.key)")


def path_of(page) -> str:
    return page.evaluate("location.pathname")


def set_range(panel, name: str) -> None:
    panel.locator("[data-part=filters] [data-filter=range] button.pick").click()
    panel.locator("[data-menu=range]").get_by_role("menuitemradio", name=re.compile(f"^{name}")).click()


def test_closed_it_names_the_dearest_session_and_its_count_opens_it(page):
    panel = open_page(page)
    section = sessions_of(panel)
    expect(section.locator(".gk")).to_have_text("Sessions")
    expect(section.locator("[data-part=closed]")).to_have_text(re.compile(rf"^most to fix the tests, {MONEY}$"))
    count = section.get_by_role("link", name="16 sessions")
    expect(count).to_have_attribute("aria-expanded", "false")
    count.click()
    expect(section.get_by_role("link", name="Close")).to_have_attribute("aria-expanded", "true")
    assert path_of(page) == "/m/usage/sessions"
    # No session is shown by its id.
    assert session_id(0) not in section.inner_text()


def test_opened_it_lists_the_weeks_ten_dearest_as_two_line_rows(page):
    section = sessions_of(open_page(page, "sessions"))
    expect(section.locator("[data-part=heading]")).to_have_text("This week, dearest first")
    rows = rows_of(section)
    expect(rows).to_have_count(10)
    assert keys_of(rows) == [f"claude/{session_id(n)}" for n in range(10)]
    typed = rows.first
    expect(typed).to_have_attribute("name", "fix the tests")
    expect(typed).to_have_attribute("where", "proj-a")
    expect(typed).to_have_attribute("fact", re.compile(rf"^{MONEY}$"))
    expect(typed).to_have_attribute("lead", "16 requests")
    expect(typed).to_have_attribute("lead-more", re.compile(rf"^(today|yesterday) {TIME}( to .*{TIME})?$"))
    expect(typed).to_have_attribute("rest", MODEL)
    expect(typed).to_have_attribute("tail", re.compile(r"^context (under 1|\d+)% full$"))
    # With no typed name a row is named by its folder and start, and has no `where`.
    other = rows.nth(1)
    expect(other).to_have_attribute("name", re.compile(rf"^proj-b · started (today|yesterday) {TIME}$"))
    expect(other).to_have_attribute("where", "")
    expect(typed.get_by_role("link", name="Open")).to_be_visible()


def test_show_adds_the_rest_of_the_week_below_the_rows_shown(page):
    section = sessions_of(open_page(page, "sessions"))
    more = section.locator("[data-more=first]")
    expect(more).to_have_text(re.compile(rf"^4 more this week, {MONEY} between them\s*Show$"))
    before = keys_of(rows_of(section))
    more.get_by_role("button", name="Show").click()
    rows = rows_of(section)
    expect(rows).to_have_count(14)
    assert keys_of(rows)[:10] == before
    assert keys_of(rows)[10:] == [f"claude/{session_id(n)}" for n in range(10, 14)]
    expect(more).to_have_count(0)


def test_earlier_says_the_days_before_the_week_and_shows_their_rows(page):
    section = sessions_of(open_page(page, "sessions"))
    earlier = section.locator("[data-more=earlier]")
    expect(earlier).to_have_text(re.compile(rf"^Earlier · 2 sessions, \w+ \d+ to \w+ \d+, {MONEY}\s*Show$"))
    expect(rows_of(section, "earlier")).to_have_count(0)
    earlier.get_by_role("button", name="Show").click()
    expect(rows_of(section, "earlier")).to_have_count(2)
    assert keys_of(rows_of(section, "earlier")) == [f"claude/{session_id(20)}", f"claude/{session_id(21)}"]
    # Once shown, the line stands over its rows with no act.
    expect(earlier.get_by_role("button", name="Show")).to_have_count(0)
    expect(section.locator("[data-more=earlier-more]")).to_have_count(0)


def test_a_range_of_seven_days_or_fewer_has_no_earlier_line(page):
    panel = open_page(page, "sessions")
    section = sessions_of(panel)
    expect(section.locator("[data-more=earlier]")).to_be_visible()
    set_range(panel, "Last 7 days")
    expect(section.get_by_role("link", name="Close")).to_be_visible()
    expect(section.locator("[data-part=heading]")).to_have_text("This week, dearest first")
    expect(rows_of(section)).to_have_count(10)
    expect(section.locator("[data-more=earlier]")).to_have_count(0)
    set_range(panel, "Today")
    expect(section.locator("[data-part=heading]")).to_have_text("Today, dearest first")
    expect(section.locator("[data-more=earlier]")).to_have_count(0)
    expect(section.locator("[data-more=first]")).to_have_count(0)
    set_range(panel, "Last 30 days")


def test_the_table_holds_every_figure_of_the_rows_shown(page):
    section = sessions_of(open_page(page, "sessions"))
    expect(rows_of(section)).to_have_count(10)
    section.locator("[data-more=earlier]").get_by_role("button", name="Show").click()
    expect(rows_of(section, "earlier")).to_have_count(2)
    section.get_by_role("button", name="Table").click()
    table = section.locator("table[data-part=table]")
    heads = table.locator("th").all_inner_texts()
    assert heads[:3] == ["List", "Session", "Project"], heads
    assert {"Folder", "Harness", "Models", "First request", "Last request", "Context", "Requests", "Tokens",
            "Cost"} <= set(heads), heads
    expect(table.locator("tbody tr")).to_have_count(12)
    expect(table.locator("tbody tr").first.locator("td").nth(1)).to_have_text("fix the tests")
    section.get_by_role("button", name="Hide table").click()
    expect(table).to_have_count(0)


def test_a_rows_open_writes_its_address_and_reads_as_close(page):
    panel = open_page(page, "sessions")
    section = sessions_of(panel)
    row = section.locator(f'moxi-ui-row[data-key="claude/{session_id(1)}"]')
    with page.expect_request(lambda request: "/m/usage/api/session?" in request.url) as sent:
        row.get_by_role("link", name="Open").click()
    assert "harness=claude" in sent.value.url, sent.value.url
    assert path_of(page) == f"/m/usage/sessions/claude/{session_id(1)}"
    expect(row).to_have_attribute("open", "")
    close = row.get_by_role("link", name="Close")
    expect(close).to_have_attribute("aria-expanded", "true")
    close.click()
    assert path_of(page) == "/m/usage/sessions"
    expect(row).not_to_have_attribute("open", "")


def test_cost_names_todays_two_dearest_sessions(page):
    panel = open_page(page)
    expect(panel.locator("[data-group=cost] [data-part=most]")).to_have_text(
        re.compile(rf"^most to fix the tests {MONEY}, proj-b · started today {TIME} {MONEY}$"))


def test_the_opened_section_fits_a_phone(page, ui_width_check):
    ui_width_check(page, module="usage", route="sessions")
    section = sessions_of(page.locator("#moxi-panel > moxi-usage-page"))
    expect(rows_of(section)).to_have_count(10)
    ui_width_check(page)
