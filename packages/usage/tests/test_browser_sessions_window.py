"""The Sessions table of usage's page with more sessions than one page of the table's
reads, through a real shell, under the shell's own style policy (`style-src 'self'`):
the box scrolls to the last session of the second page, which opens; and the page
writes nothing the policy refuses while the table scrolls.

The transcripts are written and the index built in the shell's own environment. No test
changes what the index holds, so the tests hold under pytest-xdist in any order.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="usage's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

MODEL = "claude-sonnet-4-5"
#: More sessions than one page of the table's reads (500).
COUNT = 520

#: Writes argv[1] sessions of one request each under the harness roots of the
#: environment it runs in, an hour apart and the newest half an hour old, with model
#: argv[2]: all of them inside the last thirty days.
SEED = """
import sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for n in range(int(sys.argv[1])):
    sid = f"0190c000-0000-7000-8000-{n:012d}"
    cwd = f"/work/proj-{n % 7}"
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=sid,
                           project=cwd.replace("/", "-"), cwd=cwd, start=now - 1800 - 3600 * n)
    t.user("Fix the failing test.")
    t.assistant("Done.", usage=T.usage(input_tokens=200 + n, output=1000),
                request_id=f"req_{n}", model=sys.argv[2])
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
    run(shell, sys.executable, "-c", SEED, str(COUNT), MODEL)
    run(shell, sys.executable, "-m", "moxi", "usage", "index")
    return shell


@pytest.fixture
def page(shell, ui_contexts):
    return ui_contexts(shell).pages[0]


def opened_table(page):
    """The Sessions section opened by its address, its first page drawn."""
    page.goto("/m/usage/sessions")
    panel = page.locator("#moxi-panel > moxi-usage-page")
    section = panel.locator("[data-part=below] section[data-group=sessions]")
    expect(section.locator("table[data-table=sessions] tbody tr[data-row]").first).to_be_visible()
    return section


#: Twenty frames of the page, long enough for the table to draw what a scroll moved.
FRAMES = """new Promise((done) => {
  let n = 0;
  const step = () => (++n < 20 ? requestAnimationFrame(step) : done());
  requestAnimationFrame(step);
})"""


def scroll_to_end(section) -> None:
    """Scrolls the table's box to its bottom, as far as the box itself lets it go."""
    section.locator("[data-part=board]").evaluate("(box) => { box.scrollTop = box.scrollHeight; }")


def test_the_whole_policy_holds_on_the_page(page):
    response = page.goto("/m/usage/sessions")
    assert "style-src 'self'" in response.headers["content-security-policy"]


def test_the_box_scrolls_to_the_last_session_past_the_first_page_and_it_opens(page):
    section = opened_table(page)
    table = section.locator("table[data-table=sessions]")
    more = section.locator("[data-more=table]")
    expect(more).to_have_text(f"{COUNT} sessions · showing 500Load more")
    # The box is as tall as every row read, so its bottom is the last of them; near it
    # the next page is read.
    scroll_to_end(section)
    expect(more).to_have_text(f"{COUNT} sessions · showing {COUNT}")
    expect(table).to_have_attribute("aria-rowcount", str(COUNT + 1))
    box = section.locator("[data-part=board]")
    assert box.evaluate("(box) => box.scrollHeight") >= 40 * COUNT
    scroll_to_end(section)
    last = table.locator(f"tbody tr[data-index='{COUNT - 1}']")
    expect(last).to_be_visible()
    expect(last).to_have_attribute("aria-rowindex", str(COUNT + 1))
    # It shows at the box's bottom, not under it.
    shown = box.bounding_box()
    row = last.bounding_box()
    assert shown["y"] <= row["y"] and row["y"] + row["height"] <= shown["y"] + shown["height"] + 0.5, (shown, row)
    key = last.get_attribute("data-row")
    with page.expect_request(lambda request: "/m/usage/api/session?" in request.url):
        last.locator("td").last.click()
    assert page.evaluate("location.pathname") == f"/m/usage/sessions/{key}"
    expect(section.locator(f'[data-list=placed] moxi-ui-row[data-key="{key}"]')).to_have_attribute("open", "")


def test_the_table_scrolls_with_no_console_error(page):
    section = opened_table(page)
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    box = section.locator("[data-part=board]")
    box.hover()
    for _ in range(12):
        page.mouse.wheel(0, 2000)
    scroll_to_end(section)
    page.evaluate(FRAMES)
    box.evaluate("(box) => { box.scrollTop = 0; }")
    page.evaluate(FRAMES)
    assert errors == []
