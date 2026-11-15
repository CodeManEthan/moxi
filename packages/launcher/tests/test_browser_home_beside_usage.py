"""Home's Sessions row beside usage's real Usage row, in a browser, through real shells that
start the launcher's and usage's real page servers: Home puts a row that needs the user
above one that needs nothing, whichever of the two is ordered first.

The Sessions row is ordered above the Usage row. In one shell a limit filling before its
reset makes the Usage row need the user while the one session running is ordinary, so
the Usage row rises above it. In another the session waits on a subagent while its cache
is warm and nothing keeps it warm, and the limits are steady, so the Sessions row needs
the user and the Usage row does not.

Each shell runs the fake turns in turns' place, as `test_browser_home_running.py` does,
and holds usage's plan readings under its own home. usage is an optional module of the
launcher, so the test skips where it isn't installed.
"""

from __future__ import annotations

import re
import subprocess
import sys
import uuid

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit

pytest.importorskip("moxi_cache.api", reason="cache is not installed")
pytest.importorskip("moxi_sessions.api", reason="sessions is not installed")
pytest.importorskip("moxi_usage.api", reason="usage is not installed")

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="a launch's process is read through the process "
                                        "table, and usage's row is checked on Linux")]

SESSIONS, USAGE, HEALTH = "launcher.running", "usage.row", "ui.health"
#: A time of day as a browser's locale writes it.
HOUR = r"\d{1,2}[:.]\d{2}(\s?[AaPp][Mm])?"
#: Seconds within which a joiner pass reads a change in a session (`JOIN_EVERY` and a pass).
PASS_MS = 30_000

#: Claude's 5-hour window at 20, 35 and 48.6% over the last 50 minutes, resetting in three
#: hours, so it fills before it resets; its weekly window at 0%, read once.
FILLING = """
import time
from moxi_usage import readings
from moxi_usage.readings import Reading
now = time.time()
found = [
    Reading("claude", "plan", 300, "five_hour", used, now + 3 * 3600, now - ago, "session-usage", "s1")
    for used, ago in ((20.0, 3000), (35.0, 1500), (48.6, 240))
] + [Reading("claude", "plan", 10080, "seven_day", 0.0, now + 5 * 86400, now - 240, "session-usage", "s1")]
conn = readings.open_store()
try:
    readings.store(conn, found, now=now, days=7)
finally:
    conn.close()
"""

#: Each of Claude's main windows read once: no pace, so neither fills before its reset.
STEADY = """
import time
from moxi_usage import readings
from moxi_usage.readings import Reading
now = time.time()
found = [Reading("claude", "plan", 300, "five_hour", 30.0, now + 3 * 3600, now - 240, "session-usage", "s1"),
         Reading("claude", "plan", 10080, "seven_day", 60.0, now + 5 * 86400, now - 240, "session-usage", "s1")]
conn = readings.open_store()
try:
    readings.store(conn, found, now=now, days=7)
finally:
    conn.close()
"""

#: Keeps the last `needs` each row component reported to Home, by its element's name, in
#: `window.homeNeeds` (null for a report of nothing). Home stops the event at the row's
#: host, so it is heard on its way down, at the window.
NEEDS = """window.homeNeeds = {};
window.addEventListener("moxi-home-row", (event) => {
  const from = event.composedPath()[0];
  window.homeNeeds[from.localName] = event.detail === null ? null : (event.detail.needs ?? 0);
}, true);"""


class Shell:
    """A test shell with the fake turns in turns' place and `plan`'s readings stored under
    its home; the launches a test seeds in it are ended when it is done with them."""

    def __init__(self, ui_shells, root, plan: str):
        self.turns = kit.FakeTurns(root / "fake-turns")
        self.root = root
        self.shell = ui_shells(**self.turns.options())
        done = subprocess.run([sys.executable, "-c", plan], env=self.shell.env,
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr
        self.ids: list[str] = []

    def seed(self, name: str, *, waits: bool = False) -> str:
        """A live launch named `name` whose agent answered a minute ago, waiting on one
        subagent with nothing keeping it warm when `waits`. Returns its request id."""
        request_id = f"{name}-{uuid.uuid4().hex[:8]}"
        config = self.root / request_id
        sid = kit.seed(self.shell, request_id, live=True, claimed=True, config_dir=config,
                       values={"name": name})
        kit.transcript(config, sid, ago_s=60)
        if waits:
            self.turns.script(sessions={sid: kit.waiting()})
        self.ids.append(request_id)
        return request_id

    def close(self) -> None:
        self.turns.script()
        kit.end(self.shell, *self.ids)
        kit.published(self.shell, "running", lambda data: data["count"] == 0)


@pytest.fixture(scope="module")
def shells(ui_shells, tmp_path_factory):
    """`shells(plan)`: one more shell holding `plan`'s readings."""
    made: list[Shell] = []

    def make(plan: str) -> Shell:
        made.append(Shell(ui_shells, tmp_path_factory.mktemp("shell"), plan))
        return made[-1]

    try:
        yield make
    finally:
        for shell in made:
            shell.close()


def _home(page):
    return page.locator("#moxi-panel > moxi-ui-page moxi-ui-home")


def _line(page, key: str):
    return _home(page).locator(f".entry[data-row='{key}'] .row")


def _shown(page) -> list[str]:
    """The rows in sight, top first."""
    return _home(page).locator(".entry").evaluate_all(
        """entries => entries.filter((entry) => entry.getBoundingClientRect().height > 0)
             .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)
             .map((entry) => entry.dataset.row)""")


def _expect_order(page, keys: list[str]) -> None:
    wait_for(lambda: _shown(page) == keys, 5, interval=0.05,
             what=lambda: f"the rows {keys}; seen {_shown(page)}")


def _expect_needs(page, want: dict[str, int]) -> None:
    """Each row component's last report to Home carries the `needs` in `want`."""
    def needs() -> dict:
        return page.evaluate("window.homeNeeds")

    wait_for(lambda: {name: needs().get(name) for name in want} == want, 5, interval=0.05,
             what=lambda: f"the needs {want}; seen {needs()}")


def _home_of(ui_contexts, shell: Shell):
    """A signed-in page on the shell's Home that keeps what each row reports."""
    context = ui_contexts(shell.shell)
    context.add_init_script(NEEDS)
    page = context.pages[0]
    page.goto("/m/ui/")
    return page


def test_a_row_that_needs_the_user_rises_above_the_row_ordered_first_usage_or_sessions(
        shells, ui_contexts):
    # A limit fills before its reset; the session running is ordinary. The Sessions row is
    # ordered first, and the Usage row rises above it because it needs the user.
    filling = shells(FILLING)
    request_id = filling.seed("planner")
    kit.published(filling.shell, "running", lambda data: any(
        entry["request_id"] == request_id and entry["activity"] and entry["waits"] is None
        for entry in data["sessions"]))
    page = _home_of(ui_contexts, filling)
    expect(_line(page, USAGE).locator(".fact .heavy")).to_have_text(
        re.compile(r"^Claude 5-hour limit fills "))
    expect(_line(page, SESSIONS).locator(".fact")).to_have_text("1 running")
    expect(_line(page, SESSIONS).locator(".fact .heavy")).to_have_count(0)
    _expect_needs(page, {"moxi-usage-row": 40, "moxi-launcher-running": 0})
    _expect_order(page, [USAGE, SESSIONS, HEALTH])

    # The other way round: the limits are steady, and the session waits on a subagent
    # while its cache is warm and nothing keeps it warm. The Sessions row needs the user
    # and stays above the Usage row, which needs nothing.
    steady = shells(STEADY)
    request_id = steady.seed("reviewer", waits=True)
    kit.published(steady.shell, "running", lambda data: any(
        entry["request_id"] == request_id and entry["covered"] is False
        and entry["waits"] == {"kind": "subagents", "count": 1}
        for entry in data["sessions"]))
    page = _home_of(ui_contexts, steady)
    expect(_line(page, SESSIONS).locator(".fact .heavy")).to_have_text(
        re.compile(f"^reviewer goes cold at {HOUR}$"), timeout=PASS_MS)
    expect(_line(page, USAGE).locator(".fact")).to_have_text(
        re.compile(r"^Claude 5-hour 30%, resets .+ · weekly 60%$"))
    expect(_line(page, USAGE).locator(".fact .heavy")).to_have_count(0)
    _expect_needs(page, {"moxi-usage-row": 0, "moxi-launcher-running": 20})
    _expect_order(page, [SESSIONS, USAGE, HEALTH])
