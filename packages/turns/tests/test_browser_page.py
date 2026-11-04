"""turns' panel in a browser, through a real shell that starts turns' real page server: the
contract check; a seeded fake session shown with its facts, agents and trouble, its
harness-written text shown as text; a session appearing, its turn opening and closing, and
the session going, all live; a failed read; and the 360 px width check.

The sessions are the fake harness's endpoints (`make_endpoint`), run in the test's own
process with turns' runtime root pointed at the shell's, so the page server the shell
starts finds them where it looks. No live session is read.
"""

from __future__ import annotations

import re
import sys

import pytest
from playwright.sync_api import expect

from fake_harness import FakeHarness
from moxi_dev.testing import short_dir
from moxi_turns.results import Agent

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="turns' panel is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

SID = "aaaaaaaa-1111-4111-8111-111111111111"
CWD = "/srv/work/sample-project"
#: Harness-written text that would be markup if it went in as HTML.
MARKUP = '<img src="x" onerror="window.__hit = 1">look <b>here</b>'
EMPTY = "No session is reachable. A session shows here while it runs with turns' endpoint."
SESSIONS = "/m/turns/api/sessions"
#: How long a new endpoint may take to show: one rescan, its first read, the push.
APPEAR_MS = 10_000


@pytest.fixture(scope="module")
def ui_shell(ui_shell_options):
    """The test shell, rooted in a short directory rather than pytest's tmp. The
    endpoints the tests open put their sockets under the shell's runtime root, and under
    pytest's root (a numbered directory per run, a worker's directory, a counted one per
    shell) a socket's path can pass the core's limit, which moves it to a shared directory
    outside the test."""
    from moxi_ui.testing.shell import TestShell

    with short_dir("tb-") as root:
        shell = TestShell(root, **ui_shell_options).start()
        try:
            yield shell
        finally:
            shell.stop()


@pytest.fixture
def shell(ui_shell, turns_env, monkeypatch):
    """The module's shell, with this process's turns runtime root set to the shell's, so
    every endpoint `make_endpoint` opens is one the shell's page server finds."""
    monkeypatch.setenv("MOXI_TURNS_RUNTIME_DIR", str(ui_shell.runtime_dir("turns").parent))
    return ui_shell


def harness(**over) -> FakeHarness:
    fake = FakeHarness()
    fake.cwd = CWD
    for name, value in over.items():
        setattr(fake, name, value)
    return fake


def open_panel(page):
    page.goto("/m/turns/")
    return page.locator("#moxi-panel > moxi-turns-page")


def rows(panel):
    return panel.locator("li.session")


def row(panel, key: str):
    return panel.locator(f'li.session[data-key="{key}"]')


def test_the_page_keeps_the_contract(shell, make_endpoint, ui_page, ui_component_check):
    make_endpoint(harness(), session_id=SID)
    check = ui_component_check(ui_page, "turns", "page")
    assert check.element == "moxi-turns-page"
    assert any(SESSIONS in url for url in check.requests), check.requests


def test_a_seeded_session_shows_its_facts_agents_and_trouble(shell, make_endpoint, ui_page):
    fake = harness(draft=True, trouble="the host is loading the thread", agents_complete=False,
                   entries=[Agent("a1", "subagent", "running", False, MARKUP),
                            Agent("sh1", "shell", "done", True, None, "a1")])
    ep, _ = make_endpoint(fake, session_id=SID)
    panel = open_panel(ui_page)
    found = row(panel, ep.dir.name)
    expect(found).to_have_count(1, timeout=APPEAR_MS)
    expect(found.locator(".harness")).to_have_text("fake")
    expect(found.locator("code.id")).to_have_text(SID[:8])
    expect(found.locator("code.id")).to_have_attribute("title", SID)
    expect(found.locator("code.cwd")).to_have_text(CWD)
    expect(found.locator(".turn")).to_have_text("turn 0, idle")
    expect(found.locator(".turn")).to_have_attribute("data-open", "false")
    # An incomplete agent list leaves the busy count unknown.
    expect(found.locator(".facts")).to_have_text(
        re.compile(r"^started \d+s ago · pid \d+ · agents unknown · draft in the prompt box$"))
    expect(found.locator(".versions")).to_contain_text("endpoint ")
    expect(found.locator(".trouble")).to_have_text("the host is loading the thread")
    agent = found.locator('li.agent[data-agent="a1"]')
    expect(agent.locator(".kind")).to_have_text("subagent")
    expect(agent.locator(".state")).to_have_text("running")
    expect(agent.locator(".final")).to_have_text("not final")
    # The description goes in as text: its markup is shown, never made into elements.
    expect(agent.locator(".desc")).to_have_text(MARKUP)
    expect(found.locator("img, b")).to_have_count(0)
    assert ui_page.evaluate("window.__hit") is None
    expect(found.locator('li.agent[data-agent="sh1"] .final')).to_have_text("final")
    expect(found.locator(".incomplete")).to_have_count(1)
    # Read-only: nothing on the panel acts on a session.
    expect(panel.locator("button, form, input, a[href]")).to_have_count(0)


def test_a_session_appears_its_turn_moves_and_it_goes_live(shell, make_endpoint, ui_page):
    panel = open_panel(ui_page)
    state = panel.locator("div[data-state]")
    expect(state).to_have_attribute("data-state", "empty", timeout=APPEAR_MS)
    expect(state).to_have_text(EMPTY)
    ep, fake = make_endpoint(harness(), session_id=SID)
    found = row(panel, ep.dir.name)
    expect(found).to_have_count(1, timeout=APPEAR_MS)
    expect(state).to_have_attribute("data-state", "up")
    turn = found.locator(".turn")
    expect(turn).to_have_text("turn 0, idle")
    fake.start_turn()
    expect(turn).to_have_text("turn 1, open")
    expect(turn).to_have_attribute("data-open", "true")
    fake.end_turn()
    expect(turn).to_have_text("turn 1, idle")
    ep.stop("the test is done with it")
    expect(rows(panel)).to_have_count(0)
    expect(state).to_have_attribute("data-state", "empty")


@pytest.mark.parametrize("status", [500, 401])
def test_a_failed_read_shows_the_failure_line(shell, ui_page, status):
    ui_page.route("**" + SESSIONS,
                  lambda route: route.fulfill(status=status, json={"error": "no"}))
    panel = open_panel(ui_page)
    state = panel.locator("div[data-state]")
    expect(state).to_have_attribute("data-state", "failed")
    expect(state).to_have_text(f"The sessions can't be read: turns' page server answered {status}.")


def test_the_page_fits_a_phone(shell, make_endpoint, ui_page, ui_width_check):
    long = "a-very-long-description-with-no-spaces-" * 6
    fake = harness(trouble="t" * 200, entries=[Agent("a" * 80, "subagent", "running", False, long)])
    fake.cwd = "/srv/" + "deep/" * 40 + "project"
    ep, _ = make_endpoint(fake, session_id="b" * 120)
    make_endpoint(harness(), session_id=SID)
    panel = ui_page.locator("#moxi-panel > moxi-turns-page")
    ui_width_check(ui_page, module="turns")
    expect(rows(panel)).to_have_count(2, timeout=APPEAR_MS)
    expect(row(panel, ep.dir.name).locator(".desc")).to_have_text(long)
    ui_width_check(ui_page)
