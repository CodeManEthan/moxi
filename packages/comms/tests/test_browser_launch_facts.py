"""What is this agent running on? comms' page in a browser, on a test shell over the rig,
with launcher's `/api/running` answered in the browser.

An opened agent's `Session` line names the model and effort its launch was started with,
where launcher gives them; a launch that gives neither shows neither. Without launcher, and
once launcher's read fails after a good one, nothing of them shows and nothing of the last
good answer stays.

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
    _PUSHED, PAGE, T, LauncherStub, agent, bound, needs_launcher, open_page, page_shell, running_entry,
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
