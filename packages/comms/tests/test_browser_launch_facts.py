"""What is this agent running on, and when is it next kept warm? comms' page in a browser,
on a test shell over the rig, with launcher's `/api/running` answered in the browser.

An opened agent's `Session` line names the model and effort its launch was started with,
where launcher gives them; a launch that gives neither shows neither. Its `Cache` line ends
in the next keep-alive where launcher gives one: a time, or `due` once the time has come.
Without launcher, and once launcher's read fails after a good one, nothing of them shows
and nothing of the last good answer stays.

The shell starts after the rig, with the rig's roots in its environment (`page_shell`), as on
the rest of the page's tests; `LauncherStub` serves launcher's routes in launcher's declared
shape.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from typing import Any

import pytest
from playwright.sync_api import expect

import roster_kit
from roster_kit import holding
from test_browser_page import (  # noqa: F401 -- `page_shell` is a fixture the tests take
    _PUSHED, PAGE, T, LauncherStub, agent, bound, iso_in, needs_launcher, open_page, page_shell, running_entry,
)
from test_reply_enforcement import PLANNER, REVIEWER, P, R

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="comms runs on Linux only"),
              pytest.mark.parametrize("backend", ["local"], indirect=True)]


def opened_line(root: Any, to: str, line: str) -> Any:
    """The value of the opened `to` row's line `line`, its tail included."""
    return root.locator(f'[data-part="opened"][data-to="{to}"] [data-line="{line}"] > .f')


def open_watched(shell: Any, ui_contexts: Any, launches: list[dict] | None = None
                 ) -> tuple[Any, Any, "FailingLauncher | None", list[str]]:
    """A signed-in page on the shell once its modules came; with `launches`, a
    `FailingLauncher` serves them. The page, its root, the stub, and every URL under
    `/m/launcher/` the page asked."""
    page = ui_contexts(shell).pages[0]
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url) if "/m/launcher/" in request.url else None)
    page.add_init_script(_PUSHED)
    stub = FailingLauncher(page, launches) if launches is not None else None
    page.goto(PAGE)
    page.wait_for_function("() => window.pushed.includes('ui.modules')")
    return page, page.locator("#moxi-panel > moxi-comms-page"), stub, asked


class FailingLauncher(LauncherStub):
    """`LauncherStub` whose `running` answers 500 once `failing` is set."""

    def __init__(self, page: Any, sessions: list[dict]) -> None:
        self.failing = False
        super().__init__(page, sessions)

    def _running(self, route: Any) -> None:
        if not self.failing:
            super()._running(route)
            return
        route.fulfill(status=500, content_type="application/json",
                      body=json.dumps({"error": "launcher's read failed"}))


def open_agent(root: Any, name: str) -> None:
    agent(root, name).locator('[data-action="message"]').click()
    expect(root.locator(f'[data-part="opened"][data-to="{name}"] [data-part="box"]')).to_have_count(1)


@needs_launcher
def test_the_session_line_names_the_model_and_effort_the_launch_was_started_with(
        page_shell, rig, ui_contexts):
    planner = rig.shim(**PLANNER)
    reviewer = rig.shim(**REVIEWER)
    bound(rig, P, R)
    shell = page_shell()
    roster_kit.current(shell, holding([P, R]))
    _, root, _ = open_page(shell, ui_contexts, [
        running_entry("r-planner", planner.session_id, "planner's session", slot_number="3",
                      project="atlas-api", waits=1, covered=True, model="opus", effort="high"),
        running_entry("r-reviewer", reviewer.session_id, "review pass", slot_number="4",
                      project="kestrel-web"),
    ])
    expect(agent(root, P)).to_have_attribute("fact", "kept warm while 1 agent works")

    open_agent(root, P)
    expect(opened_line(root, P, "session")).to_have_text("planner's session · claude · opus · high")
    # The Cache line keeps launcher's words and nothing more.
    expect(opened_line(root, P, "cache")).to_have_text(
        re.compile(rf"^warm until {T} · kept warm while 1 agent works$"))

    # A launch that gives no model and no effort: neither shows.
    open_agent(root, R)
    expect(opened_line(root, R, "session")).to_have_text("review pass · claude")
    expect(opened_line(root, R, "cache")).to_have_text(re.compile(rf"^warm until {T}$"))


def test_without_launcher_nothing_of_the_launch_shows_and_nothing_asks_it(
        page_shell, rig, ui_contexts):
    rig.shim(**PLANNER)
    bound(rig, P)
    shell = page_shell(leave_out=["launcher"])
    roster_kit.current(shell, holding([P]))
    page, root, _, asked = open_watched(shell, ui_contexts)
    open_agent(root, P)
    opened = root.locator('[data-part="opened"][data-to="planner"]')
    # With sessions, its own Session line from the rig's transcript: it ends at the harness.
    session = opened.locator('[data-line="session"] > .f')
    if importlib.util.find_spec("moxi_sessions") is not None:
        expect(session).to_have_text(re.compile(r"· claude$"))
    else:
        expect(session).to_have_count(0)
    page.wait_for_timeout(300)
    assert asked == []


@needs_launcher
def test_a_read_of_launcher_failing_after_a_good_one_leaves_no_launch_fact(
        page_shell, rig, ui_contexts):
    planner = rig.shim(**PLANNER)
    bound(rig, P)
    shell = page_shell()
    roster_kit.current(shell, holding([P]))
    page, root, stub, _ = open_watched(shell, ui_contexts, [
        running_entry("r-planner", planner.session_id, "planner's session", slot_number="3",
                      project="atlas-api", waits=1, covered=True, model="opus", effort="high"),
    ])
    open_agent(root, P)
    expect(opened_line(root, P, "session")).to_have_text("planner's session · claude · opus · high")
    expect(opened_line(root, P, "cache")).to_contain_text("kept warm while 1 agent works")

    stub.failing = True
    roster_kit.show_tab(page)
    opened = root.locator('[data-part="opened"][data-to="planner"]')
    expect(opened).not_to_contain_text("opus")
    expect(opened).not_to_contain_text("kept warm")
    expect(opened).not_to_contain_text("planner's session")
    expect(agent(root, P)).not_to_have_attribute("where", "slot 3 · atlas-api")


def clock_of(page: Any, at: str) -> str:
    """The launcher time `at` as the page words a time of day, in the browser's zone."""
    return page.evaluate("""(at) => new Date(Date.parse(at))
      .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })""", at)


def read_again(page: Any, stub: "FailingLauncher", sessions: list[dict] | None = None, *,
               failing: bool = False) -> None:
    """Launcher answers `sessions` (or fails) from now on, and the page reads it again."""
    if sessions is not None:
        stub.sessions = sessions
    stub.failing = failing
    with page.expect_response(lambda answer: "/m/launcher/api/running" in answer.url
                              and answer.status == (500 if failing else 200)):
        roster_kit.show_tab(page)


@needs_launcher
def test_the_cache_line_ends_in_the_next_keep_alive_and_nothing_stale_stays(
        page_shell, rig, ui_contexts):
    planner = rig.shim(**PLANNER)
    reviewer = rig.shim(**REVIEWER)
    bound(rig, P, R)
    shell = page_shell()
    roster_kit.current(shell, holding([P, R]))

    def covered(**fields: Any) -> dict:
        entry = running_entry("r-planner", planner.session_id, "planner's session", slot_number="3",
                              project="atlas-api", waits=1, covered=True)
        entry.update(fields)
        return entry

    # Warm for 20 minutes; keep-warm's window opens 15 minutes before it goes cold.
    first = covered(next_keep_alive_at=iso_in(5 * 60))
    plain = running_entry("r-reviewer", reviewer.session_id, "review pass", slot_number="4",
                          project="kestrel-web")
    page, root, stub, _ = open_watched(shell, ui_contexts, [first, plain])
    # not covered: launcher sends null, and the line keeps its words alone
    open_agent(root, R)
    expect(opened_line(root, R, "cache")).to_have_text(re.compile(rf"^warm until (?:tomorrow )?{T}$"))
    open_agent(root, P)
    cache = opened_line(root, P, "cache")
    # a time past midnight carries its day word
    expect(cache).to_have_text(re.compile(
        rf"^warm until (?:tomorrow )?{T} · kept warm while 1 agent works, next keep-alive "
        rf"(?:tomorrow )?{re.escape(clock_of(page, first['next_keep_alive_at']))}$"))

    # a time already come: the nudge goes at keep-warm's next ask
    read_again(page, stub, [covered(next_keep_alive_at=iso_in(-60)), plain])
    expect(cache).to_have_text(re.compile(r" · kept warm while 1 agent works, next keep-alive due$"))

    # null after a good time: no piece, and no stale one
    read_again(page, stub, [covered(), plain])
    expect(cache).to_have_text(re.compile(r" · kept warm while 1 agent works$"))

    # an older launcher that sends no such key: the line as before
    read_again(page, stub, [covered(next_keep_alive_at=iso_in(5 * 60)), plain])
    expect(cache).to_contain_text("next keep-alive")
    older = covered()
    del older["next_keep_alive_at"]
    read_again(page, stub, [older, plain])
    expect(cache).to_have_text(re.compile(r" · kept warm while 1 agent works$"))

    # a read failing after a good one: nothing of it stays
    read_again(page, stub, [covered(next_keep_alive_at=iso_in(5 * 60)), plain])
    expect(cache).to_contain_text("next keep-alive")
    read_again(page, stub, failing=True)
    opened = root.locator('[data-part="opened"][data-to="planner"]')
    expect(opened).not_to_contain_text("kept warm")
    expect(root.get_by_text(re.compile("keep-alive"))).to_have_count(0)
