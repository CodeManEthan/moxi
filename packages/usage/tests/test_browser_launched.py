"""What launcher says of the sessions it runs, on usage's page in a browser, through a real
shell that starts usage's and launcher's real page servers: a session a running launch
holds is named by the launch where no name was typed, its where leads with `slot 4` or
`terminal`, and its opened `Session` line carries the effort it was launched with. A
session no launch holds reads as before; with launcher's read failing, and on an install
without launcher, nothing of launcher's shows and nothing stands in its place.

The transcripts are written and the index built in the shell's own environment. The
launches are rows of the shell's own launch log whose process is this test's own, so
launcher lists them as running while the tests run. No test changes what either holds.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

pytest.importorskip("moxi_launcher.api", reason="launcher is not installed")

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="usage's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

MODEL = "claude-sonnet-4-5"
MONEY = r"(at least )?\$\d[\d,]*\.\d\d"
#: A time as the browser's locale writes it.
TIME = r"\d\d?:\d\d(\s?[AP]M)?"
#: A day as the page words a start this week.
DAY = r"(today|yesterday|Mon|Tue|Wed|Thu|Fri|Sat|Sun|[A-Z][a-z]{2} \d\d?)"

SLOT = "0190b000-0000-7000-8000-000000000301"
TERMINAL = "0190b000-0000-7000-8000-000000000302"
BY_HAND = "0190b000-0000-7000-8000-000000000303"
#: Three sessions of the last hours, dearest first: one launched in slot 4 at high effort,
#: one launched in a terminal with no effort set, one started by hand.
SESSIONS = [{"session": SLOT, "folder": "atlas-api", "hours_ago": 0.3, "requests": 12},
            {"session": TERMINAL, "folder": "kestrel-web", "hours_ago": 0.2, "requests": 6},
            {"session": BY_HAND, "folder": "harbor-infra", "hours_ago": 0.1, "requests": 3}]
LAUNCHES = [{"request_id": "r-slot", "session": SLOT, "target": "slot", "slot": "4",
             "cwd": "/work/atlas-api",
             "values": {"name": "builder-1", "model": "opus", "effort": "high"}},
            {"request_id": "r-terminal", "session": TERMINAL, "target": "terminal", "slot": None,
             "cwd": "/work/kestrel-web", "values": {"name": "planner", "model": "sonnet"}}]

#: Writes the sessions of the JSON list in argv[1] under the harness roots of the
#: environment it runs in, with model argv[2].
SEED = """
import json, sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for spec in json.loads(sys.argv[1]):
    cwd = "/work/" + spec["folder"]
    start = now - spec["hours_ago"] * 3600
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=spec["session"],
                           project=cwd.replace("/", "-"), cwd=cwd, start=start)
    for m in range(spec["requests"]):
        t.user("Fix the failing test.")
        t.assistant("Done.", usage=T.usage(input_tokens=200, output=1000),
                    request_id=f"req_{spec['session'][-3:]}{m}", model=sys.argv[2])
    t.write()
"""

#: Writes the launches of the JSON list in argv[1] to the launch log of the environment it
#: runs in, each started by the process whose pid is argv[2], so each reads running while
#: that process lives.
LAUNCH = """
import json, sys
from moxi import proc
from moxi_launcher import store
me = proc.identity(int(sys.argv[2]))
conn = store.connect()
for spec in json.loads(sys.argv[1]):
    values = {"harness": "claude", "target": spec["target"], "cwd": spec["cwd"], **spec["values"]}
    store.insert(conn, request_id=spec["request_id"], by="ann", harness="claude",
                 target=spec["target"], cwd=spec["cwd"], values=values, argv=["claude"], env={},
                 posture="operator", session_id=spec["session"], slot=spec["slot"])
    store.set_state(conn, spec["request_id"], "started", pid=me.pid, pid_started=me.started)
conn.close()
"""


def run(shell, *argv: str) -> str:
    done = subprocess.run(list(argv), env=shell.env, capture_output=True, text=True,
                          timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done.stdout


def seeded(shell, *, launches: bool):
    run(shell, sys.executable, "-c", SEED, json.dumps(SESSIONS), MODEL)
    run(shell, sys.executable, "-m", "moxi", "usage", "index")
    if launches:
        run(shell, sys.executable, "-c", LAUNCH, json.dumps(LAUNCHES), str(os.getpid()))
    return shell


@pytest.fixture(scope="module")
def shell(ui_shells, ui_shell_options):
    return seeded(ui_shells(**ui_shell_options), launches=True)


@pytest.fixture(scope="module")
def small(ui_shells, ui_shell_options):
    """The same sessions on an install without launcher."""
    return seeded(ui_shells(**{**ui_shell_options, "leave_out": ["launcher"]}), launches=False)


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


def placed_of(panel, session: str):
    return sessions_of(panel).locator(f'[data-list=placed] moxi-ui-row[data-key="claude/{session}"]')


def session_line(panel):
    return panel.locator("[data-part=session] [data-line=session] .f")


def by_hand(folder: str) -> re.Pattern:
    """The name of a session no launch holds: its folder and start."""
    return re.compile(rf"^{folder} · started {DAY} {TIME}$")


def test_a_launched_session_is_named_by_its_launch_and_one_started_by_hand_as_before(page):
    panel = open_page(page, "sessions")
    section = sessions_of(panel)
    expect(section.locator("[data-part=closed]")).to_have_text(re.compile(rf"^most to builder-1, {MONEY}$"))
    rows = section.locator("table[data-part=table] tbody tr")
    expect(rows).to_have_count(3)
    expect(section.locator(f'tr[data-row="claude/{SLOT}"]').get_by_role("link")).to_have_text("builder-1")
    expect(section.locator(f'tr[data-row="claude/{TERMINAL}"]').get_by_role("link")).to_have_text("planner")
    expect(section.locator(f'tr[data-row="claude/{BY_HAND}"]').get_by_role("link")).to_have_text(
        by_hand("harbor-infra"))
    # No launch is shown by its request id.
    assert "r-slot" not in section.inner_text()


def test_a_session_launched_in_a_slot_says_its_slot_and_its_effort_when_opened(page):
    panel = open_page(page, f"sessions/claude/{SLOT}")
    row = placed_of(panel, SLOT)
    expect(row).to_have_attribute("open", "")
    expect(row).to_have_attribute("name", "builder-1")
    expect(row).to_have_attribute("where", "slot 4 · atlas-api")
    expect(session_line(panel)).to_have_text(re.compile(rf"^claude · {MODEL} · high · started {DAY} {TIME}$"))


def test_a_terminal_launch_with_no_effort_says_terminal_and_no_effort(page):
    panel = open_page(page, f"sessions/claude/{TERMINAL}")
    row = placed_of(panel, TERMINAL)
    expect(row).to_have_attribute("name", "planner")
    expect(row).to_have_attribute("where", "terminal · kestrel-web")
    expect(session_line(panel)).to_have_text(re.compile(rf"^claude · {MODEL} · started {DAY} {TIME}$"))


def test_a_session_no_launch_holds_reads_as_before(page):
    panel = open_page(page, f"sessions/claude/{BY_HAND}")
    row = placed_of(panel, BY_HAND)
    expect(row).to_have_attribute("name", by_hand("harbor-infra"))
    expect(row).to_have_attribute("where", "")
    expect(session_line(panel)).to_have_text(re.compile(rf"^claude · {MODEL} · started {DAY} {TIME}$"))


def test_with_launchers_read_failing_nothing_of_launchers_shows(page):
    page.route("**/m/launcher/api/running", lambda route: route.fulfill(status=500, json={"error": "down"}))
    with page.expect_response("**/m/launcher/api/running"):
        panel = open_page(page, f"sessions/claude/{SLOT}")
    row = placed_of(panel, SLOT)
    expect(row).to_have_attribute("open", "")
    expect(session_line(panel)).to_have_text(re.compile(rf"^claude · {MODEL} · started {DAY} {TIME}$"))
    expect(row).to_have_attribute("name", by_hand("atlas-api"))
    expect(row).to_have_attribute("where", "")
    expect(sessions_of(panel).locator("[data-part=closed]")).to_have_text(
        re.compile(rf"^most to atlas-api · started {DAY} {TIME}, {MONEY}$"))
    # The failed read is not one of the page's own: no line names it.
    expect(panel.locator("[data-part=lines]")).not_to_contain_text(re.compile("aunch"))


def test_without_launcher_nothing_of_launchers_shows_and_nothing_is_asked(small, ui_contexts):
    page = ui_contexts(small).pages[0]
    asked = []
    page.on("request", lambda request: asked.append(request.url) if "/m/launcher/" in request.url else None)
    panel = open_page(page, f"sessions/claude/{SLOT}")
    row = placed_of(panel, SLOT)
    expect(row).to_have_attribute("open", "")
    expect(session_line(panel)).to_have_text(re.compile(rf"^claude · {MODEL} · started {DAY} {TIME}$"))
    expect(row).to_have_attribute("name", by_hand("atlas-api"))
    expect(row).to_have_attribute("where", "")
    assert asked == []
