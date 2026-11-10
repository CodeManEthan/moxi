"""One session on sessions' page, at its own address, in a browser, through a real shell
that starts sessions' real page server: a Claude Code session's header, its last five
turns and "Earlier"; a subagent opened by its route, with the browser's back returning to
the parent; an OpenCode child and a Codex subagent thread each opened as a session of
their own; a session the page server can't find, shown with its reason; a long message cut
and expanded, and one the page server cut at 64 KiB; a route naming an agent under Codex;
and the 360 px width check. The list the page shows at its own address is checked in
`test_browser_list`.

The records are written by `moxi_sessions.testing`'s builders under the roots the shell's
environment names (see `browser_seed`).
"""

from __future__ import annotations

import re
import sys

import pytest
from playwright.sync_api import expect

from browser_seed import (AGENT, CHILD_THREAD, CUT_BYTES, LONG, LONG_CHARS, SESSION, THREAD,
                          TURNS, seed, shell_options)

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

PANEL = "#moxi-panel > moxi-sessions-page"


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return shell_options(tmp_path_factory.mktemp("harnesses"))


@pytest.fixture(scope="module")
def seeded(ui_shell) -> dict[str, str]:
    return seed(ui_shell.env)


def at(route: str) -> re.Pattern:
    """The shell's URL for sessions' panel at `route`."""
    return re.compile(re.escape(f"/m/sessions/{route}") + "$")


def open_route(page, route: str):
    page.goto(f"/m/sessions/{route}")
    return page.locator(PANEL)


def window(panel):
    return panel.locator('section[data-part="window"]')


def texts(panel, role: str):
    return panel.locator(f'li.msg[data-role="{role}"][data-reasoning="false"] [data-line="text"]')


def test_a_claude_session_shows_its_header_and_last_turns_and_pages_back(seeded, ui_page):
    panel = open_route(ui_page, f"claude/{SESSION}")
    view = window(panel)
    expect(view).to_have_attribute("data-state", "up")
    expect(view.locator('[data-field="harness"]')).to_have_text("Claude Code")
    expect(view.locator('[data-field="session"]')).to_have_text(SESSION)
    expect(view.locator('[data-field="cwd"]')).to_have_text("/work/project")
    expect(view.locator('[data-field="title"]')).to_have_text("fix the tests")
    expect(view.locator('[data-field="last_activity"]')).not_to_be_empty()
    expect(texts(panel, "user")).to_have_text([f"turn {n}" for n in range(TURNS - 5, TURNS)])
    expect(view.locator('li[data-kind="tool_call"]').first).to_contain_text("notes.md")
    expect(view.locator('li[data-kind="tool_result"]').first).to_have_text("Result: 37 B")
    expect(view.locator('li[data-kind="request"]').first).to_contain_text("claude-model-x")
    expect(view.locator('li[data-marker="turn_end"]').first).to_have_text("Turn ended after 1.2 s")
    expect(view).not_to_contain_text("never sends")
    view.get_by_role("button", name="Earlier turns").click()
    expect(texts(panel, "user")).to_have_text([f"turn {n}" for n in range(TURNS)])
    expect(view.locator('[data-line="start"]')).to_have_text("This is the start of the session.")
    expect(view.get_by_role("button", name="Earlier turns")).to_have_count(0)


def test_a_subagent_opens_by_its_route_and_back_returns(seeded, ui_page):
    panel = open_route(ui_page, f"claude/{SESSION}")
    listing = panel.locator('section[data-part="subagents"]')
    expect(listing).to_have_attribute("data-state", "listed")
    entry = listing.locator(f'li[data-agent="{AGENT}"]')
    expect(entry.locator('[data-line="description"]')).to_have_text("Look into the failing test")
    entry.get_by_role("button", name="general-purpose").click()
    expect(ui_page).to_have_url(at(f"claude/{SESSION}/{AGENT}"))
    expect(window(panel).locator('[data-field="agent"]')).to_have_text(AGENT)
    expect(texts(panel, "user")).to_have_text(["look into it"])
    expect(panel.locator('section[data-part="subagents"]')).to_have_count(0)
    ui_page.go_back()
    expect(ui_page).to_have_url(at(f"claude/{SESSION}"))
    expect(window(panel).locator("h2")).to_have_text("fix the tests")


def test_an_opencode_child_opens_as_a_session_of_its_own(seeded, ui_page):
    panel = open_route(ui_page, f"opencode/{seeded['opencode']}")
    expect(window(panel).locator("h2")).to_have_text("opencode work")
    expect(texts(panel, "user")).to_have_text(["opencode turn"])
    panel.locator(f'li[data-agent="{seeded["opencode_child"]}"] button').click()
    expect(ui_page).to_have_url(at(f"opencode/{seeded['opencode_child']}"))
    expect(window(panel).locator('[data-field="parent"]')).to_have_text(seeded["opencode"])
    expect(texts(panel, "user")).to_have_text(["look"])
    panel.get_by_role("button", name="Open the parent session").click()
    expect(ui_page).to_have_url(at(f"opencode/{seeded['opencode']}"))


def test_a_codex_subagent_thread_opens_as_a_session_of_its_own(seeded, ui_page):
    panel = open_route(ui_page, f"codex/{THREAD}")
    expect(texts(panel, "user")).to_have_text(["codex turn"])
    panel.locator(f'li[data-agent="{CHILD_THREAD}"] button').click()
    expect(ui_page).to_have_url(at(f"codex/{CHILD_THREAD}"))
    expect(window(panel).locator('[data-field="parent"]')).to_have_text(THREAD)
    expect(texts(panel, "user").last).to_have_text("explore")


def test_a_session_the_page_server_cannot_find_shows_why(seeded, ui_page):
    panel = open_route(ui_page, "claude/nosuch")
    view = window(panel)
    expect(view).to_have_attribute("data-state", "refused")
    expect(view.locator('[data-line="refused"]')).to_contain_text("no claude session nosuch was found")
    expect(panel.locator('section[data-part="subagents"]')).to_have_attribute("data-state", "refused")


def test_a_long_message_is_cut_and_expands_and_a_server_cut_says_so(seeded, ui_page):
    panel = open_route(ui_page, f"claude/{LONG}")
    user = panel.locator('li.msg[data-role="user"]')
    text = user.locator('[data-line="text"]')
    expect(text).to_have_text("x" * 2000)
    user.get_by_role("button", name=f"Show {LONG_CHARS - 2000} more characters").click()
    expect(text).to_have_text("x" * LONG_CHARS)
    user.get_by_role("button", name="Show less").click()
    expect(text).to_have_text("x" * 2000)
    reply = panel.locator('li.msg[data-role="assistant"][data-reasoning="false"]')
    expect(reply.locator('[data-line="sent"]')).to_have_text(
        f"The page server sent only the first 64 KiB of this message's {CUT_BYTES:,} bytes.")
    reply.get_by_role("button", name="Show", exact=False).first.click()
    expect(reply.locator('[data-line="text"]')).to_have_text("é" * (65536 // 2))


def test_an_agent_under_codex_names_how_to_open_it(seeded, ui_page):
    panel = open_route(ui_page, f"codex/{THREAD}/{CHILD_THREAD}")
    expect(panel.locator('[data-line="unknown"]')).to_contain_text("a session of its own")
    expect(panel.get_by_label("Find a session")).to_be_visible()


def test_the_page_fits_a_phone(seeded, ui_page, ui_width_check):
    ui_width_check(ui_page, module="sessions")
    ui_width_check(ui_page, module="sessions", route=f"claude/{SESSION}")
    panel = ui_page.locator(PANEL)
    expect(window(panel)).to_have_attribute("data-state", "up")
    ui_width_check(ui_page)
    ui_width_check(ui_page, module="sessions", route=f"claude/{LONG}")
    expect(window(panel)).to_have_attribute("data-state", "up")
    ui_width_check(ui_page)
