"""The Sessions section of usage's page in a browser, through a real shell that starts
usage's real page server: the closed row naming the range's dearest session with the
count that opens it; opened, the table of every session of the range at once, dearest
first, sorted by a column, with its turns, its token classes each with a bar, its
subagent share and its cost, its rows in a box with a row cursor on the arrow keys, a
next page that failed read again only on `Load more`; a table row opening its session
first in the section, and the opened row's `Close` and the address it writes; a range of seven days or one holding only its own sessions; Cost's
`most to` naming today's two dearest; and the section at a phone's width.
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
#: more twelve and twenty days ago. Each costs less than the one before it. The two
#: dearest are today's.
WEEK = [{"session": session_id(n), "folder": "proj-a" if n % 2 == 0 else "proj-b",
         "hours_ago": 0.02 * (n + 1) if n < 2 else 2 + 5 * n, "today": n < 2, "requests": 16 - n,
         "title": "fix the tests" if n == 0 else None} for n in range(14)]
EARLIER = [{"session": session_id(20 + n), "folder": "proj-b", "hours_ago": 24 * days, "today": False,
            "requests": 3 - n, "title": None} for n, days in enumerate((12, 20))]
#: The oldest of today's sessions, in hours before the moment.
TODAY_HOURS = max(spec["hours_ago"] for spec in WEEK if spec["today"])

#: Writes the sessions of the JSON list in argv[1] under the harness roots of the
#: environment it runs in, each of its own number of requests, with model argv[2]; those
#: marked today are kept on today, the oldest of them argv[3] hours old.
SEED = """
import json, sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for spec in json.loads(sys.argv[1]):
    cwd = "/work/" + spec["folder"]
    age_s = spec["hours_ago"] * 3600
    start = T.today_at(age_s, oldest_s=float(sys.argv[3]) * 3600, now=now) if spec["today"] else now - age_s
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=spec["session"],
                           project=cwd.replace("/", "-"), cwd=cwd, start=start)
    if spec["title"]:
        t.title(spec["title"])
    for m in range(spec["requests"]):
        t.user("Fix the failing test.")
        t.assistant("Done.", usage=T.usage(input_tokens=200, output=1000),
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
    run(shell, sys.executable, "-c", SEED, json.dumps(WEEK + EARLIER), MODEL, repr(TODAY_HOURS))
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


def rows_of(section):
    """The Sessions table's rows."""
    return section.locator("table[data-table=sessions] tbody tr[data-row]")


def keys_of(rows) -> list[str]:
    return rows.evaluate_all("nodes => nodes.map((node) => node.dataset.row)")


def placed_of(section, session: str):
    """The row of the session opened in the section, first in it."""
    return section.locator(f'[data-list=placed] moxi-ui-row[data-key="claude/{session}"]')


def opened_sessions(page):
    """Opens the Sessions section by its address, waiting for the table's first read."""
    with page.expect_request(lambda request: "sort=cost" in request.url) as sent:
        panel = open_page(page, "sessions")
    assert "order=desc" in sent.value.url and "top=500" in sent.value.url, sent.value.url
    return panel


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


def test_opened_it_shows_the_table_of_every_session_at_once_dearest_first(page):
    section = sessions_of(opened_sessions(page))
    rows = rows_of(section)
    expect(rows).to_have_count(16)
    expected = [f"claude/{session_id(n)}" for n in range(14)] + [f"claude/{session_id(20)}", f"claude/{session_id(21)}"]
    assert keys_of(rows) == expected
    # A row is named as the session was typed, or by its folder and start.
    expect(rows.first.get_by_role("link")).to_have_text("fix the tests")
    expect(rows.nth(1).get_by_role("link")).to_have_text(re.compile(rf"^proj-b · started (today|yesterday) {TIME}$"))
    # The table is the section's view: no toggle hides it and no list stands above it.
    expect(section.locator("[data-part=table-toggle]")).to_have_count(0)
    expect(section.locator("moxi-ui-row")).to_have_count(0)


def test_a_range_of_seven_days_or_one_holds_only_its_own_sessions(page):
    panel = opened_sessions(page)
    section = sessions_of(panel)
    expect(rows_of(section)).to_have_count(16)
    set_range(panel, "Last 7 days")
    expect(section.get_by_role("link", name="Close")).to_be_visible()
    expect(rows_of(section)).to_have_count(14)
    set_range(panel, "Today")
    # Today's two, and none of the days before; a session of earlier this morning may be
    # today's too, by the hour the run starts.
    for n in (0, 1):
        expect(section.locator(f'tr[data-row="claude/{session_id(n)}"]')).to_have_count(1)
    expect(section.locator(f'tr[data-row="claude/{session_id(13)}"]')).to_have_count(0)
    set_range(panel, "Last 30 days")


def test_the_table_holds_every_session_of_the_range_and_sorts_by_a_column(page):
    section = sessions_of(opened_sessions(page))
    table = section.locator("table[data-part=table]")
    # Every session of the 30 days.
    expect(table.locator("tbody tr")).to_have_count(16)
    expect(section.locator("[data-more=table]")).to_have_text("16 sessions · showing 16")
    expect(section.locator("[data-more=table]").get_by_role("button", name="Load more")).to_have_count(0)
    heads = table.locator("th").all_inner_texts()
    assert heads == ["Project", "Session", "Started", "Active", "Turns", "Input", "Cache create", "Cache read",
                     "Output", "Subagent share", "Est. cost ↓"], heads
    column = {name: i for i, name in enumerate(head.removesuffix(" ↓") for head in heads)}
    expect(table.locator("th[aria-sort=descending]")).to_have_text("Est. cost ↓")
    expect(table.locator("th[aria-sort=none]")).to_have_count(9)
    first = table.locator("tbody tr").first.locator("td")
    expect(first.nth(column["Session"])).to_have_text("fix the tests")
    # Each token class cell has a bar against its column's most: the dearest session's
    # output is the most, so its bar is whole.
    expect(table.locator("tbody tr").first.locator("svg.sb [data-part=sparkbar]")).to_have_count(4)
    output_bar = first.nth(column["Output"]).locator("[data-part=sparkbar]")
    assert float(output_bar.get_attribute("width")) == pytest.approx(44, abs=0.5)
    last_bar = table.locator("tbody tr").last.locator("td").nth(column["Output"]).locator("[data-part=sparkbar]")
    assert float(last_bar.get_attribute("width")) < 44

    # A new column starts highest first; a second click turns it around.
    output = table.get_by_role("button", name="Output")
    with page.expect_request(lambda request: "sort=output" in request.url) as sent:
        output.click()
    assert "order=desc" in sent.value.url, sent.value.url
    expect(table.locator("th[aria-sort=descending]")).to_have_text("Output ↓")
    expect(table.locator("th[aria-sort=none]").filter(has_text="Est. cost")).to_have_count(1)
    expect(table.locator("tbody tr").first).to_have_attribute("data-row", f"claude/{session_id(0)}")
    with page.expect_request(lambda request: "sort=output&order=asc" in request.url):
        output.click()
    expect(table.locator("th[aria-sort=ascending]")).to_have_text("Output ↑")
    expect(table.locator("tbody tr").first).to_have_attribute("data-row", f"claude/{session_id(21)}")
    expect(table.locator("tbody tr").last).to_have_attribute("data-row", f"claude/{session_id(0)}")


def test_the_table_counts_turns_and_the_subagent_share_and_sorts_by_both(page):
    section = sessions_of(opened_sessions(page))
    table = section.locator("table[data-part=table]")
    expect(table.locator("tbody tr")).to_have_count(16)
    heads = [head.removesuffix(" ↓") for head in table.locator("th").all_inner_texts()]
    column = {name: i for i, name in enumerate(heads)}
    assert heads.index("Active") + 1 == column["Turns"] == column["Input"] - 1, heads
    assert column["Output"] + 1 == column["Subagent share"] == column["Est. cost"] - 1, heads
    expect(table.locator("th", has_text="Turns")).to_have_attribute("title", re.compile("never summed"))
    first = table.locator("tbody tr").first.locator("td")
    # Each request of the seed is a prompt of its own, and no session has a subagent.
    expect(first.nth(column["Turns"])).to_have_text("16")
    # The share is a meter, named for a reader that cannot see it.
    expect(first.nth(column["Subagent share"]).get_by_role("img")).to_have_attribute(
        "aria-label", "Subagent share 0%")

    turns = table.get_by_role("button", name="Turns")
    with page.expect_request(lambda request: "sort=turns&order=desc" in request.url):
        turns.click()
    expect(table.locator("th[aria-sort=descending]")).to_have_text("Turns ↓")
    with page.expect_request(lambda request: "sort=turns&order=asc" in request.url):
        turns.click()
    expect(table.locator("th[aria-sort=ascending]")).to_have_text("Turns ↑")
    expect(first.nth(column["Turns"])).to_have_text("2")
    with page.expect_request(lambda request: "sort=subagent_share&order=desc" in request.url):
        table.get_by_role("button", name="Subagent share").click()
    expect(table.locator("th[aria-sort=descending]")).to_have_text("Subagent share ↓")
    expect(table.locator("tbody tr")).to_have_count(16)


def test_a_table_row_opens_its_session_first_in_the_section(page):
    section = sessions_of(opened_sessions(page))
    table = section.locator("table[data-part=table]")
    with page.expect_request(lambda request: "sort=turns&order=desc" in request.url):
        table.get_by_role("button", name="Turns").click()
    with page.expect_request(lambda request: "sort=turns&order=asc" in request.url):
        table.get_by_role("button", name="Turns").click()
    # The fewest turns: a session of twenty days ago.
    earliest = table.locator(f'tr[data-row="claude/{session_id(21)}"]')
    expect(table.locator("tbody tr").first).to_have_attribute("data-row", f"claude/{session_id(21)}")
    with page.expect_request(lambda request: "/m/usage/api/session?" in request.url
                             and session_id(21) in request.url):
        earliest.locator("td").last.click()
    assert path_of(page) == f"/m/usage/sessions/claude/{session_id(21)}"
    placed = placed_of(section, session_id(21))
    expect(placed).to_have_attribute("open", "")
    expect(placed.get_by_role("link", name="Close")).to_have_attribute("aria-expanded", "true")
    # The table stays under it.
    expect(table.locator("tbody tr")).to_have_count(16)

    # By the keyboard: the session's name is a link to its address, and Enter opens it.
    name = table.locator(f'tr[data-row="claude/{session_id(1)}"]').get_by_role("link")
    expect(name).to_have_attribute("href", f"/m/usage/sessions/claude/{session_id(1)}")
    name.focus()
    page.keyboard.press("Enter")
    expect(placed_of(section, session_id(1))).to_have_attribute("open", "")
    assert path_of(page) == f"/m/usage/sessions/claude/{session_id(1)}"
    expect(table).to_be_visible()



def test_the_tables_rows_sit_in_a_box_and_the_arrow_keys_move_a_row_cursor_enter_opens(page):
    section = sessions_of(opened_sessions(page))
    table = section.locator("table[data-part=table]")
    rows = table.locator("tbody tr")
    expect(rows).to_have_count(16)
    expect(table).to_have_attribute("aria-rowcount", "17")
    # Each row is 40 px tall, in a box of its own that scrolls under the head.
    assert rows.first.bounding_box()["height"] == pytest.approx(40, abs=0.5)
    box = section.locator("[data-part=board]").bounding_box()
    assert box["height"] <= 480.5, box
    # One row at a time takes the focus: the cursor starts on the first.
    expect(table.locator("tbody tr[tabindex='0']")).to_have_count(1)
    rows.first.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowDown")
    third = rows.nth(2)
    expect(third).to_be_focused()
    expect(third).to_have_attribute("tabindex", "0")
    key = third.get_attribute("data-row")
    # Enter on the row opens its session first in the section.
    with page.expect_request(lambda request: "/m/usage/api/session?" in request.url):
        page.keyboard.press("Enter")
    assert path_of(page) == f"/m/usage/sessions/{key}"
    expect(section.locator(f'[data-list=placed] moxi-ui-row[data-key="{key}"]')).to_have_attribute("open", "")
    expect(table).to_be_visible()


#: Twenty frames of the page, long enough for a read asked on every draw to go out many times.
FRAMES = """new Promise((done) => {
  let n = 0;
  const step = () => (++n < 20 ? requestAnimationFrame(step) : done());
  requestAnimationFrame(step);
})"""


def test_a_next_page_that_failed_is_read_again_only_on_load_more(page):
    failed = []

    def answer(route):
        if "skip=" in route.request.url:
            failed.append(route.request.url)
            route.fulfill(status=500, json={"error": "down"})
            return
        # The first page says 600 more sessions wait, so the rows drawn are near the end.
        response = route.fetch()
        body = response.json()
        body.update(count=body["count"] + 600, more=600)
        route.fulfill(response=response, json=body)

    page.route("**/m/usage/api/sessions?*", answer)
    with page.expect_request(lambda request: "skip=16" in request.url):
        section = sessions_of(opened_sessions(page))
    more = section.locator("[data-more=table]")
    expect(more).to_contain_text("616 sessions · showing 16")
    page.evaluate(FRAMES)
    assert len(failed) == 1, failed
    # A press reads the page again; once the server answers, the line counts what it holds.
    page.unroute("**/m/usage/api/sessions?*")
    with page.expect_request(lambda request: "skip=16" in request.url):
        more.get_by_role("button", name="Load more").click()
    expect(more).to_have_text("16 sessions · showing 16")
    expect(rows_of(section)).to_have_count(16)


def test_a_rows_open_writes_its_address_and_reads_as_close(page):
    section = sessions_of(opened_sessions(page))
    row = placed_of(section, session_id(1))
    with page.expect_request(lambda request: "/m/usage/api/session?" in request.url) as sent:
        section.locator(f'tr[data-row="claude/{session_id(1)}"]').get_by_role("link").click()
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
    # The table scrolls inside its box.
    expect(rows_of(section)).to_have_count(16)
    ui_width_check(page)
