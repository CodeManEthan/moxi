"""comms' roster and page in a browser, through a real shell that starts comms' and cache's
real page servers, comms' asking the rig's daemon: the contract check, the rows and the
states, cache's chip on each live row, push updates, every failure state, and the 360 px
width check.

The shell's page servers stop after a second idle. A test sets its daemon up, then waits
for the running comms page server to stop (`roster_kit.current`), so the next one's first
gather is the test's own roster and no test waits out the poller's period for it. Tests
of a change on an open roster wait for the period, as a user would.
"""

from __future__ import annotations

import itertools
import re
import sys
import time
import uuid

import pytest
from playwright.sync_api import expect

import roster_kit
import shim_drive
from moxi_comms.daemon import Daemon
from roster_kit import STANDING, holding, put_clock, wait_for
from test_reply_enforcement import PLANNER, REVIEWER, P, R, indexed, send

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="comms runs on Linux only")]

HELP = "An identity is a name only one session can hold at a time."
OFFLINE = "The comms daemon isn't answering. moxi doctor comms says why."
FAILED = "Can't reach the comms panel's server."
#: How long a change on the daemon may take to reach an open roster: the poller's period,
#: and the gather and the push after it.
PUSH_MS = 12_000

#: Places a roster in a host of its own in the shell's panel, and keeps every
#: `data-state` it shows in `window.rosterStates` ("none" while it draws nothing).
_PLACE = """async () => {
  const client = await import("/m/ui/client.js");
  const host = document.createElement("div");
  host.id = "placed";
  document.getElementById("moxi-panel").append(host);
  const element = await client.place(host, "comms", "roster");
  const states = window.rosterStates = [];
  if (element === null) return false;
  const note = () => {
    const state = element.shadowRoot?.querySelector(".roster")?.dataset.state ?? "none";
    if (states[states.length - 1] !== state) states.push(state);
  };
  note();
  new MutationObserver(note).observe(element.shadowRoot, {subtree: true, childList: true,
                                                        attributes: true});
  return true;
}"""


@pytest.fixture(scope="module")
def ui_shell_options():
    return {"child_args": ["--idle-s", "1"]}


@pytest.fixture(autouse=True)
def shell(ui_shell, monkeypatch):
    """The module's shell, the rig's daemon the one its comms page server asks, with no
    clock record left by an earlier test."""
    roster_kit.point_at(ui_shell, monkeypatch)
    directory = roster_kit.clock_dir(ui_shell)
    if directory.is_dir():
        for path in directory.iterdir():
            path.unlink()
    return ui_shell


def bound(rig, *names):
    wait_for(lambda: all(n in rig.daemon.bindings and rig.daemon.bindings[n].session_id
                         for n in names), 5, what=f"{names} to bind")


def awaits(data: dict, qualified: str) -> list[str]:
    return next(row["awaiting"] for row in data["rows"] if row["qualified"] == qualified)


def open_panel(ui_contexts):
    """A signed-in page on comms' panel, and the roster in it."""
    page = ui_contexts().pages[0]
    page.goto("/m/comms/")
    return page, page.locator("#moxi-panel > moxi-comms-page moxi-comms-roster div.roster")


def rows(roster):
    return roster.locator("li[data-address]")


def row(roster, qualified):
    return roster.locator(f'li[data-address="{qualified}"]')


def chip(roster, qualified, session_id):
    return row(roster, qualified).locator(
        f'span.cache[data-session="{session_id}"] moxi-cache-chip span.chip')


def place_roster(page) -> object:
    assert page.evaluate(_PLACE), "the shell placed no roster"
    return page.locator("#placed moxi-comms-roster div.roster")


def roster_states(page) -> list[str]:
    return page.evaluate("() => window.rosterStates")


# --- the contract --------------------------------------------------------------------------

def test_the_roster_keeps_the_contract(shell, rig, ui_contexts, ui_component_check):
    reviewer = rig.shim(**REVIEWER)
    bound(rig, R)
    put_clock(shell, reviewer.session_id, age=60)
    roster_kit.current(shell, holding([R]))
    page = ui_contexts().pages[0]
    # It places cache's chip on its live row, which reads cache's routes.
    check = ui_component_check(page, "comms", "roster", tokens=("accent", "bg-raised"),
                               allow=("cache",))
    assert check.element == "moxi-comms-roster"
    assert any(roster_kit.ROSTER in url for url in check.requests), check.requests


def test_the_page_keeps_the_contract(shell, rig, ui_contexts, ui_component_check):
    roster_kit.current(shell, holding([]))
    page = ui_contexts().pages[0]
    # The page's accent is in the roster it places, whose script the home view loaded.
    check = ui_component_check(page, "comms", "page", allow=("cache",))
    assert check.element == "moxi-comms-page"
    assert any(roster_kit.ROSTER in url for url in check.requests), check.requests


# --- rows and states -----------------------------------------------------------------------

def test_the_roster_lists_live_then_standing_names_with_a_chip_on_each_live_row(
        shell, rig, ui_contexts):
    reviewer = rig.shim(**REVIEWER, directors=("lead",))
    planner = rig.shim(**PLANNER)
    bound(rig, P, R)
    indexed(rig, send(rig, P, "do it", "--posture", "once", who=REVIEWER).id)
    put_clock(shell, reviewer.session_id, age=60)
    put_clock(shell, planner.session_id, age=4000)
    roster_kit.current(shell, lambda data: holding([P, R])(data) and awaits(data, R) == [P])
    page, roster = open_panel(ui_contexts)
    expect(page.locator("#moxi-panel > moxi-comms-page h1")).to_have_text("Comms")
    expect(roster).to_have_attribute("data-state", "up")
    expect(roster.locator("h2 a")).to_have_text("Agents")
    expect(roster.locator("p.help")).to_have_text(HELP)
    expect(roster.locator(".cols span")).to_have_text(["Address", "State"])
    expect(rows(roster)).to_have_count(5)
    assert rows(roster).evaluate_all("(rows) => rows.map((r) => r.dataset.address)") == [
        P, R, *STANDING]
    for qualified, shim, shows in ((P, planner, "cold"), (R, reviewer, "warm")):
        live = row(roster, qualified)
        expect(live).to_have_attribute("data-live", "true")
        expect(live.locator("code.address")).to_have_text(qualified)
        expect(live.locator("[data-tag]")).to_have_text(["live"])
        expect(live.locator('[data-field="adapter"]')).to_have_text("claude-shim")
        expect(live.locator('[data-field="bound"]')).to_have_text(
            re.compile(r"^bound for \d+s$"))
        expect(chip(roster, qualified, shim.session_id)).to_have_attribute("data-shows", shows)
    expect(row(roster, R).locator('[data-field="awaiting"]')).to_have_text("waiting on planner")
    expect(row(roster, R).locator('[data-field="directors"]')).to_have_text("directors: lead")
    expect(row(roster, P).locator('[data-field="awaiting"]')).to_have_count(0)
    for qualified in STANDING:
        standing = row(roster, qualified)
        expect(standing).to_have_attribute("data-live", "false")
        expect(standing.locator("[data-tag]")).to_have_text(["standing"])
        expect(standing.locator("[data-field], span.cache")).to_have_count(0)
    expect(roster.locator("p[data-line]")).to_have_count(0)


def test_a_new_binding_and_a_wait_reach_the_open_roster_by_push(shell, rig, ui_contexts):
    rig.shim(**REVIEWER)
    bound(rig, R)
    roster_kit.current(shell, holding([R]))
    page = ui_contexts().pages[0]
    reads: list[str] = []
    page.on("request", lambda request: reads.append(request.url)
            if roster_kit.ROSTER in request.url else None)
    page.goto("/m/comms/")
    roster = page.locator("#moxi-panel > moxi-comms-page moxi-comms-roster div.roster")
    expect(rows(roster)).to_have_count(4)
    rig.shim(**PLANNER)
    bound(rig, P)
    indexed(rig, send(rig, P, "do it", "--posture", "once", who=REVIEWER).id)
    expect(row(roster, R).locator('[data-field="awaiting"]')).to_have_text(
        "waiting on planner", timeout=PUSH_MS)
    expect(rows(roster)).to_have_count(5)
    expect(row(roster, P)).to_have_attribute("data-live", "true")
    assert len(reads) == 1, reads


def test_a_row_whose_session_changes_gets_a_chip_for_the_new_one(shell, rig, ui_contexts):
    reviewer = rig.shim(**REVIEWER)
    bound(rig, R)
    old, new = reviewer.session_id, str(uuid.uuid4())
    put_clock(shell, old, age=60)
    put_clock(shell, new, age=4000)
    roster_kit.current(shell, holding([R]))
    page, roster = open_panel(ui_contexts)
    expect(chip(roster, R, old)).to_have_attribute("data-shows", "warm")
    # A /clear: the same binding, now for a new session.
    shim_drive.ready(reviewer, "clear", new)
    expect(chip(roster, R, new)).to_have_attribute("data-shows", "cold", timeout=PUSH_MS)
    expect(row(roster, R).locator("moxi-cache-chip")).to_have_count(1)
    expect(roster.locator(f'moxi-cache-chip[session="{old}"]')).to_have_count(0)


def test_the_daemon_stopped_shows_the_offline_line_and_the_rows_return(shell, rig,
                                                                       ui_contexts):
    rig.shim(**REVIEWER, autoreconnect=True, backoff=itertools.repeat(0.2))
    bound(rig, R)
    roster_kit.current(shell, holding([R]))
    page, roster = open_panel(ui_contexts)
    expect(rows(roster)).to_have_count(4)
    rig.daemon.stop()
    expect(roster).to_have_attribute("data-state", "offline", timeout=PUSH_MS)
    expect(roster.locator('p[data-line="offline"]')).to_have_text(OFFLINE)
    expect(rows(roster)).to_have_count(0)
    rig.daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False).start()
    # The shim binds its name again; the rows come back with it.
    expect(roster).to_have_attribute("data-state", "up", timeout=PUSH_MS + 5_000)
    expect(rows(roster)).to_have_count(4, timeout=PUSH_MS)
    expect(row(roster, R)).to_have_attribute("data-live", "true")
    expect(roster.locator("p[data-line]")).to_have_count(0)


def test_an_error_from_the_daemon_shows_a_fixed_line_without_its_text(shell, rig,
                                                                      ui_contexts, monkeypatch):
    def fails(frame):
        raise OSError("/x/y can't be read")

    # The daemon answers the roster read with an error, so the route and the topic both
    # carry `state: error`.
    monkeypatch.setattr(rig.daemon, "_agents_query", fails)
    data = roster_kit.current(shell, lambda data: data["state"] == "error")
    detail = data["detail"]
    assert detail.startswith("the comms daemon answered the roster read with an error (")
    assert "/x/y" not in detail
    page, roster = open_panel(ui_contexts)
    expect(roster).to_have_attribute("data-state", "error")
    expect(roster.locator('p[data-line="error"]')).to_have_text(
        f"comms answered with an error: {detail}")
    expect(rows(roster)).to_have_count(0)


# --- failed requests -----------------------------------------------------------------------

@pytest.mark.parametrize("answer, why", [
    ({"status": 500, "json": {"error": "boom"}}, "comms' page server answered 500"),
    ({"status": 502, "json": {"error": "start"}}, "comms' page server answered 502"),
    ({"status": 504, "body": ""}, "comms' page server answered 504"),
    ({"status": 401, "json": {"error": "who"}}, "comms' page server answered 401"),
    ({"status": 200, "body": "<p>not JSON</p>", "content_type": "text/html"},
     "comms' page server sent an answer that can't be read"),
    ({"status": 200, "json": {"now": 1.0, "state": "maybe"}},
     "comms' page server sent an answer that can't be read"),
    ({"status": 200, "json": {"state": "up", "detail": None, "rows": []}},
     "comms' page server sent an answer that can't be read"),
    (None, "no answer from the shell"),
], ids=["500", "502", "504", "401", "not-json", "not-the-shape", "no-now", "no-answer"])
def test_a_failed_read_shows_the_failure_line(shell, ui_contexts, answer, why):
    page = ui_contexts().pages[0]
    page.route("**" + roster_kit.ROSTER,
               lambda route: route.abort() if answer is None else route.fulfill(**answer))
    roster = place_roster(page)
    expect(roster).to_have_attribute("data-state", "failed")
    line = roster.locator('p[data-line="failed"]')
    expect(line).to_have_text(FAILED)
    expect(line).to_have_attribute("title", why)
    expect(rows(roster)).to_have_count(0)


def test_a_failed_read_keeps_the_rows_and_reads_again_on_a_topic_event_or_a_shown_tab(
        shell, rig, ui_contexts):
    rig.shim(**REVIEWER)
    bound(rig, R)
    roster_kit.current(shell, holding([R]))
    page = ui_contexts().pages[0]
    roster = place_roster(page)
    expect(rows(roster)).to_have_count(4)
    page.route("**" + roster_kit.ROSTER,
               lambda route: route.fulfill(status=500, json={"error": "boom"}))
    roster_kit.show_tab(page)
    expect(roster).to_have_attribute("data-state", "failed")
    expect(roster.locator('p[data-line="failed"]')).to_have_text(FAILED)
    expect(rows(roster)).to_have_count(4)
    page.unroute("**" + roster_kit.ROSTER)
    # A new binding is the next topic event, which reads the route again.
    rig.shim(**PLANNER)
    bound(rig, P)
    expect(roster).to_have_attribute("data-state", "up", timeout=PUSH_MS)
    expect(rows(roster)).to_have_count(5)
    expect(roster.locator("p[data-line]")).to_have_count(0)
    page.route("**" + roster_kit.ROSTER,
               lambda route: route.fulfill(status=502, json={"error": "start"}))
    roster_kit.show_tab(page)
    expect(roster).to_have_attribute("data-state", "failed")
    page.unroute("**" + roster_kit.ROSTER)
    roster_kit.show_tab(page)
    expect(roster).to_have_attribute("data-state", "up")
    expect(rows(roster)).to_have_count(5)
    assert roster_states(page) == ["loading", "up", "failed", "up", "failed", "up"]


def test_a_503_is_read_again_and_the_next_answer_shows_with_no_failure(shell, rig,
                                                                         ui_contexts):
    roster_kit.current(shell, holding([]))
    page = ui_contexts().pages[0]
    hits: list[int] = []

    def busy_once(route):
        hits.append(1)
        if len(hits) == 1:
            route.fulfill(status=503, json={"error": "busy"}, headers={"Retry-After": "1"})
        else:
            route.fallback()

    page.route("**" + roster_kit.ROSTER, busy_once)
    roster = place_roster(page)
    expect(roster).to_have_attribute("data-state", "up")
    expect(rows(roster)).to_have_count(3)
    assert len(hits) == 2
    assert roster_states(page) == ["loading", "up"]


def test_four_503s_in_a_row_show_the_failure_line(shell, ui_contexts):
    page = ui_contexts().pages[0]
    hits: list[int] = []

    def busy(route):
        hits.append(1)
        route.fulfill(status=503, json={"error": "busy"}, headers={"Retry-After": "1"})

    page.route("**" + roster_kit.ROSTER, busy)
    began = time.monotonic()
    roster = place_roster(page)
    # Three reads again, 1 s apart, stay loading; the fourth 503 is the failure.
    expect(roster).to_have_attribute("data-state", "failed", timeout=10_000)
    assert time.monotonic() - began >= 2.9
    expect(roster.locator('p[data-line="failed"]')).to_have_attribute(
        "title", "comms' page server answered 503")
    assert len(hits) >= 4
    assert roster_states(page) == ["loading", "failed"]


def test_a_404_after_placement_renders_nothing(shell, ui_contexts):
    page = ui_contexts().pages[0]
    page.route("**" + roster_kit.ROSTER,
               lambda route: route.fulfill(status=404, json={"error": "no such module"}))
    place_roster(page)
    expect(page.locator("#placed moxi-comms-roster")).to_have_count(1)
    wait_for(lambda: roster_states(page)[-1] == "none", 5, what="the roster to empty itself")
    expect(page.locator("#placed moxi-comms-roster div.roster")).to_have_count(0)


def test_a_chip_cache_cannot_place_leaves_its_cell_empty(shell, rig, ui_contexts):
    reviewer = rig.shim(**REVIEWER)
    bound(rig, R)
    roster_kit.current(shell, holding([R]))
    page = ui_contexts().pages[0]
    # cache's script can't load, so place() gives null.
    page.route("**/m/cache/chip.js*", lambda route: route.fulfill(status=404, body=""))
    page.goto("/m/comms/")
    roster = page.locator("#moxi-panel > moxi-comms-page moxi-comms-roster div.roster")
    cell = row(roster, R).locator(f'span.cache[data-session="{reviewer.session_id}"]')
    expect(cell).to_have_attribute("data-placed", reviewer.session_id)
    page.wait_for_timeout(300)
    expect(cell.locator("*")).to_have_count(0)
    expect(roster).to_have_attribute("data-state", "up")
    expect(roster.locator("p[data-line]")).to_have_count(0)


# --- a phone's width -----------------------------------------------------------------------

def test_the_page_the_roster_and_the_home_view_fit_a_phone(shell, rig, ui_contexts,
                                                          ui_width_check):
    long = rig.shim(name="w" * 100)
    reviewer = rig.shim(**REVIEWER)
    qualified = "w" * 100
    bound(rig, qualified, R)
    indexed(rig, send(rig, qualified, "do it", "--posture", "once", who=REVIEWER).id)
    for shim in (long, reviewer):
        put_clock(shell, shim.session_id, age=60)
    roster_kit.current(shell, holding([qualified, R]))
    page = ui_contexts().pages[0]
    ui_width_check(page)
    ui_width_check(page, module="comms")
    roster = page.locator("#moxi-panel > moxi-comms-page moxi-comms-roster div.roster")
    expect(rows(roster)).to_have_count(5)
    expect(chip(roster, qualified, long.session_id)).to_have_attribute("data-shows", "warm")
    expect(row(roster, R).locator('[data-field="awaiting"]')).to_have_text(
        f"waiting on {qualified}")
    ui_width_check(page)
    ui_width_check(page, module="comms", name="roster")
