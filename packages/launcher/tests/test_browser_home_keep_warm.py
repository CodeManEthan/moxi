"""`Keep warm` on Home's Sessions row in a browser, through a real shell that starts the
launcher's real page server: of three waiting sessions only the one a press would cover
has the button; a press turns its row quiet, moves it down the list and hands the focus to
its `Open`, while Home's row still names the session that goes cold soonest; and a press
the launcher refuses leaves its reason on the row's third line.

The shell runs the fake turns in turns' place and reads the real cache and sessions over
transcripts the tests write, as `test_browser_home_running.py` does. Each launch is a
slot launch whose process is this test's own, waiting on one subagent, with an hour cache
lifetime; the cache watcher's clock record says whether its timer asks keep-warm about
the session. No test starts a harness session or a keep-warm ask, and the shell's page
server reaches no comms daemon: its homes are its own.
"""

from __future__ import annotations

import re
import sys
import uuid

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_fake_turns
import launcher_page_kit as kit

from moxi_launcher import cachetimers, keepwarm, store

pytest.importorskip("moxi_cache.api", reason="cache is not installed")
pytest.importorskip("moxi_sessions.api", reason="sessions is not installed")

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(sys.platform == "win32",
                                 reason="a launch's process is read through the process table")]

ROW, PEER, HEALTH = "launcher.running", "peer.row", "ui.health"
HOUR = r"\d{1,2}[:.]\d{2}(\s?[AaPp][Mm])?"
#: Within this many milliseconds a joiner pass reads a change in a session.
PASS_MS = 30_000
#: How long a pressed button waits for a value before it is enabled again, with a margin.
PRESS_WAIT_MS = 25_000
COST = "Sends a short turn before each cold time while it waits, at most 3 in a row."
DRAFT = "there is text typed in the session's prompt box"


@pytest.fixture(scope="module")
def turns(tmp_path_factory):
    return kit.FakeTurns(tmp_path_factory.mktemp("fake-turns"))


@pytest.fixture(scope="module")
def ui_shell_options(turns, tmp_path_factory):
    peer = kit.install_peer(tmp_path_factory.mktemp("peer-site"))
    options = turns.options()
    return {**options, "site": [*options["site"], str(peer)]}


class Launches:
    """The waiting launches one test seeds in a shell, ended when the test ends."""

    def __init__(self, shell, turns, root):
        self.shell, self.turns, self.root = shell, turns, root
        self.ids: list[str] = []
        self.sessions: dict[str, dict] = {}

    def seed(self, name: str, *, ago_s: float = 600.0, asks: bool = True) -> tuple[str, str]:
        """A live launch named `name` with keep-warm off, waiting on one subagent, its last
        request `ago_s` seconds ago on an hour lifetime; its clock record says the timer
        asks keep-warm about it when `asks`, and says nothing of it otherwise. Returns its
        request and session ids."""
        request_id = f"{name}-{uuid.uuid4().hex[:8]}"
        config = self.root / request_id
        sid = kit.seed(self.shell, request_id, live=True, claimed=True, config_dir=config,
                       values={"name": name})
        kit.transcript(config, sid, ago_s=ago_s, hour=True)
        kit.timed(self.shell, sid, asks=[cachetimers.KEEP_WARM] if asks else None)
        self.ids.append(request_id)
        self.script(sid, draft=False)
        return request_id, sid

    def script(self, sid: str, *, draft: bool) -> None:
        """`sid` waits on one subagent from now on, with a draft in its prompt box when
        `draft`."""
        self.sessions[sid] = kit.waiting(draft=draft)
        self.turns.script(sessions=self.sessions)

    def close(self) -> None:
        self.turns.script()
        kit.end(self.shell, *self.ids)
        kit.published(self.shell, "running", lambda data: data["count"] == 0)


@pytest.fixture
def launches(ui_shell, turns, tmp_path):
    seeded = Launches(ui_shell, turns, tmp_path)
    try:
        yield seeded
    finally:
        seeded.close()


def _home(page):
    return page.locator("#moxi-panel > moxi-ui-page moxi-ui-home")


def _entry(page, key: str = ROW):
    return _home(page).locator(f".entry[data-row='{key}']")


def _line(page, key: str = ROW):
    return _entry(page, key).locator(".row")


def _component(page):
    return _entry(page).locator("moxi-launcher-running")


def _session(page, request_id: str):
    return _component(page).locator(f"moxi-ui-row[data-request='{request_id}']")


def _shown(page) -> list[str]:
    """The rows in sight, top first."""
    return _home(page).locator(".entry").evaluate_all(
        """entries => entries.filter((entry) => entry.getBoundingClientRect().height > 0)
             .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)
             .map((entry) => entry.dataset.row)""")


def _listed(page) -> list[str]:
    """The request ids of the opened list's rows, top first."""
    return _component(page).locator("moxi-ui-row").evaluate_all(
        "rows => rows.map((row) => row.dataset.request)")


def _joined(shell, request_id: str, **want) -> dict:
    """The running entry of `request_id` once each key of `want` holds its value."""
    def matches(data: dict) -> bool:
        return any(entry["request_id"] == request_id
                   and all(entry.get(k) == v for k, v in want.items())
                   for entry in data["sessions"])

    data = kit.published(shell, "running", matches)
    return next(entry for entry in data["sessions"] if entry["request_id"] == request_id)


def _opened(page):
    """Home opened, with the Sessions row's list opened in place."""
    page.goto("/m/ui/")
    _line(page).click()
    expect(_line(page)).to_have_attribute("aria-expanded", "true")
    return _component(page)


def _covers(shell, request_id: str) -> list[str]:
    """The states of the keep-warm cover events of `request_id`, oldest first."""
    with kit.launch_log(shell) as conn:
        return [e.detail["state"] for e in store.events(conn, request_id)
                if e.kind == keepwarm.COVER]


WAITS = {"kind": "subagents", "count": 1}


def test_only_a_session_a_press_would_cover_offers_keep_warm_and_a_press_turns_its_row_quiet(
        ui_page, ui_shell, turns, launches):
    coverable, _ = launches.seed("planner", ago_s=600)
    late, _ = launches.seed("reviewer", ago_s=3200)
    unasked, _ = launches.seed("curator", ago_s=600, asks=False)
    assert _joined(ui_shell, coverable, waits=WAITS)["coverable"] == {"cap": 3}
    # Too close to cold for an ask to come, and a watcher that says nothing of asking.
    assert _joined(ui_shell, late, waits=WAITS, covered=False)["coverable"] is None
    assert _joined(ui_shell, unasked, waits=WAITS, covered=False)["coverable"] is None

    ui_page.goto("/m/ui/")
    expect(_line(ui_page).locator(".fact .heavy")).to_have_text(
        re.compile(f"^reviewer goes cold at {HOUR}$"))
    wait_for(lambda: _shown(ui_page) == [ROW, PEER, HEALTH], 5, interval=0.05,
             what=lambda: f"the Sessions row first; seen {_shown(ui_page)}")

    component = _opened(ui_page)
    wait_for(lambda: _listed(ui_page)[:1] == [late], 5, interval=0.05,
             what=lambda: f"the soonest cold first; seen {_listed(ui_page)}")
    # All three are loud; only the first has `Keep warm`, before its `Open`, with the cost.
    for request_id in (coverable, late, unasked):
        row = _session(ui_page, request_id)
        expect(row.locator(".fact.strong")).to_have_text(
            re.compile(f"^goes cold at {HOUR}$"))
        expect(row.locator(".lead.strong")).to_have_text("waiting on 1 subagent")
    expect(component.get_by_role("button", name="Keep warm")).to_have_count(1)
    row = _session(ui_page, coverable)
    press = row.get_by_role("button", name="Keep warm")
    expect(press).to_have_attribute("title", COST)
    expect(press).to_have_attribute("aria-description", COST)
    assert row.locator("[slot='acts']").evaluate_all(
        "acts => acts.map((act) => act.textContent.trim())") == ["Keep warm", "Open"]

    press.click()
    expect(row.locator(".fact")).to_have_text("kept warm while 1 subagent runs",
                                              timeout=PASS_MS)
    expect(row.locator(".strong")).to_have_count(0)
    expect(row.get_by_role("button")).to_have_count(0)
    expect(row.get_by_role("link", name="Open")).to_have_count(1)
    # The pressed button has left: the focus is on the same session's `Open`.
    expect(row.get_by_role("link", name="Open")).to_be_focused()
    # It leaves the top for its place by activity, below the two still loud.
    wait_for(lambda: _listed(ui_page) == [late, unasked, coverable], 5, interval=0.05,
             what=lambda: f"the quiet session last; seen {_listed(ui_page)}")
    assert _covers(ui_shell, coverable) == ["on"]
    assert not [c for c in launcher_fake_turns.calls(turns.dir) if c.get("call") == "deliver"]

    # Closed, the row stays heavy, names the session that goes cold soonest and stays first.
    _line(ui_page).click()
    expect(_line(ui_page)).to_have_attribute("aria-expanded", "false")
    expect(_line(ui_page).locator(".fact .heavy")).to_have_text(
        re.compile(f"^reviewer goes cold at {HOUR}$"))
    wait_for(lambda: _shown(ui_page) == [ROW, PEER, HEALTH], 5, interval=0.05,
             what=lambda: f"the Sessions row first; seen {_shown(ui_page)}")


def test_a_refused_press_shows_its_reason_on_the_row_which_stays_loud(
        ui_page, ui_shell, launches):
    request_id, sid = launches.seed("planner", ago_s=600)
    _joined(ui_shell, request_id, waits=WAITS, coverable={"cap": 3})
    _opened(ui_page)
    row = _session(ui_page, request_id)
    press = row.get_by_role("button", name="Keep warm")
    expect(press).to_be_enabled()
    looks = "b => [getComputedStyle(b).color, getComputedStyle(b).opacity].join(' ')"
    enabled_look = press.evaluate(looks)

    # A launch that starts wakes the joiner; once that pass has published, the next comes
    # no sooner than its interval, so the draft below is typed and cleared again before
    # any pass reads it, and the row's value does not change.
    other, _ = launches.seed("scout", ago_s=600)
    _joined(ui_shell, other, waits=WAITS)

    launches.script(sid, draft=True)
    press.click()
    expect(row).to_have_attribute("note", DRAFT)
    expect(row.locator(".l3")).to_have_text(DRAFT)
    launches.script(sid, draft=False)
    expect(row.locator(".fact.strong")).to_have_text(
        re.compile(f"^goes cold at {HOUR}$"))
    expect(row.locator(".lead.strong")).to_have_text("waiting on 1 subagent")
    assert _covers(ui_shell, request_id) == []
    # No value follows a refusal, so the button comes back after its wait and the note
    # stays until the row's value next changes.
    expect(press).to_be_disabled()
    # A disabled button looks it, though the theme may give its two colours one value.
    assert press.evaluate(looks) != enabled_look
    expect(press).to_be_enabled(timeout=PRESS_WAIT_MS)
    assert press.evaluate(looks) == enabled_look
    expect(row.locator(".l3")).to_have_text(DRAFT)
