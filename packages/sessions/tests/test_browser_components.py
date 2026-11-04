"""sessions' window block in a browser, placed as another module's page places it, through
a real shell that starts sessions' real page server: the contract check, reading only
sessions' own routes; the session's view; a subagent opened in the block itself, with
Back; a Claude Code subagent named by the `agent` attribute; a session the page server
can't find, shown with its reason; the link that opens sessions' page at the session's
route; and the 360 px width check.

The records are written by `moxi_sessions.testing`'s builders under the roots the shell's
environment names (see `browser_seed`).
"""

from __future__ import annotations

import re
import sys

import pytest
from playwright.sync_api import expect

from browser_seed import AGENT, SESSION, THREAD, TURNS, seed, shell_options

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' block is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return shell_options(tmp_path_factory.mktemp("harnesses"))


@pytest.fixture(scope="module")
def seeded(ui_shell) -> dict[str, str]:
    return seed(ui_shell.env)


def texts(block, role: str):
    return block.locator(f'li.msg[data-role="{role}"][data-reasoning="false"] [data-line="text"]')


@pytest.mark.parametrize(("attrs", "route"), [
    ({"harness": "claude", "session": SESSION},
     f"/m/sessions/api/window?harness=claude&session={SESSION}"),
    ({"harness": "claude", "session": SESSION, "agent": AGENT},
     f"/m/sessions/api/window?harness=claude&session={SESSION}&agent={AGENT}"),
    ({"harness": "codex", "session": THREAD},
     f"/m/sessions/api/subagents?harness=codex&session={THREAD}"),
])
def test_the_block_keeps_the_contract(seeded, ui_page, ui_component_check, attrs, route):
    check = ui_component_check(ui_page, "sessions", "window", attrs)
    assert check.element == "moxi-sessions-window"
    assert any(url.endswith(route) for url in check.requests), check.requests


def test_the_block_shows_the_sessions_last_turns(seeded, ui_page, ui_component_check):
    block = ui_component_check(ui_page, "sessions", "window",
                               {"harness": "claude", "session": SESSION}, keep=True).locator
    view = block.locator('section[data-part="window"]')
    expect(view).to_have_attribute("data-state", "up")
    expect(view.locator("h2")).to_have_text("fix the tests")
    expect(texts(block, "user")).to_have_text([f"turn {n}" for n in range(TURNS - 5, TURNS)])
    expect(block.locator('section[data-part="subagents"]')).to_have_attribute("data-state",
                                                                            "listed")


def test_a_subagent_opens_in_the_block_and_back_returns(seeded, ui_page, ui_component_check):
    block = ui_component_check(ui_page, "sessions", "window",
                               {"harness": "claude", "session": SESSION}, keep=True).locator
    url = ui_page.url
    block.locator(f'li[data-agent="{AGENT}"]').get_by_role("button",
                                                           name="general-purpose").click()
    view = block.locator('section[data-part="window"]')
    expect(view.locator('[data-field="agent"]')).to_have_text(AGENT)
    expect(texts(block, "user")).to_have_text(["look into it"])
    assert ui_page.url == url
    block.get_by_role("button", name="Back").click()
    expect(view.locator("h2")).to_have_text("fix the tests")
    expect(block.get_by_role("button", name="Back")).to_have_count(0)


def test_the_agent_attribute_opens_a_claude_subagent(seeded, ui_page, ui_component_check):
    block = ui_component_check(ui_page, "sessions", "window",
                               {"harness": "claude", "session": SESSION, "agent": AGENT},
                               keep=True).locator
    expect(texts(block, "user")).to_have_text(["look into it"])
    expect(block.locator('section[data-part="subagents"]')).to_have_count(0)


def test_a_session_the_page_server_cannot_find_shows_why(seeded, ui_page, ui_component_check):
    block = ui_component_check(ui_page, "sessions", "window",
                               {"harness": "codex", "session": "nosuch"}, keep=True).locator
    view = block.locator('section[data-part="window"]')
    expect(view).to_have_attribute("data-state", "refused")
    expect(view.locator('[data-line="refused"]')).to_contain_text("no codex session nosuch")


def test_the_link_opens_the_sessions_page_at_the_route(seeded, ui_page, ui_component_check):
    block = ui_component_check(ui_page, "sessions", "window",
                               {"harness": "claude", "session": SESSION, "agent": AGENT},
                               keep=True).locator
    link = block.get_by_role("link", name="Open in Sessions")
    expect(link).to_have_attribute("href", f"/m/sessions/claude/{SESSION}/{AGENT}")
    link.click()
    expect(ui_page).to_have_url(re.compile(re.escape(f"/m/sessions/claude/{SESSION}/{AGENT}")
                                           + "$"))
    panel = ui_page.locator("#moxi-panel > moxi-sessions-page")
    expect(panel.locator('[data-field="agent"]')).to_have_text(AGENT)


def test_the_block_fits_a_phone(seeded, ui_page, ui_width_check):
    ui_width_check(ui_page, module="sessions", name="window",
                   attrs={"harness": "claude", "session": SESSION})
    block = ui_page.locator("#moxi-panel moxi-sessions-window").last
    expect(block.locator('section[data-part="window"]')).to_have_attribute("data-state", "up")
    ui_width_check(ui_page)
