"""What went wrong in a session's turns, on sessions' page in a browser, through a real
shell that starts sessions' real page server: a tool result its harness marked as failed
starts with `Failed` and the message Claude Code writes for a request that failed starts
with `Error`, each in the heavier weight and in the text's own colour, in a session's
turns and in a subagent's; a result that did not fail and a line Claude Code made up for
another reason start with no word.

One shell over a root of its own, written with `moxi_sessions.testing`.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from moxi_sessions.testing import ClaudeTranscript
from test_browser_list import changed_at, env_of, sid

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

PANEL = "#moxi-panel > moxi-sessions-page"
AGENT = "a0190b0000000001"
WORD = '[data-line="word"]'


def turns(t: ClaudeTranscript) -> None:
    """A tool call that worked, one that failed, a line the harness made up, and the
    message it wrote for a request that failed."""
    t.user("Read the notes.", origin="human")
    request = t.request()
    worked = request.tool_use("Read", {"file_path": "/work/proj-a/notes.md"})
    t.tool_result(worked, "notes")
    failed = request.tool_use("Read", {"file_path": "/work/proj-a/none.md"})
    t.tool_result(failed, "no such file", is_error=True)
    t.synthetic("No response requested.")
    made_up = t.request(request_id=None, model="<synthetic>", requested_model=None, usage_block=None)
    made_up.text("API Error: the request could not be served", isApiErrorMessage=True)


def seed(env: dict[str, str]) -> None:
    root = Path(env["CLAUDE_CONFIG_DIR"]) / "projects"
    now = time.time()
    t = ClaudeTranscript(root, session_id=sid(1), cwd="/work/proj-a", project="-work-proj-a", start=now - 600)
    turns(t)
    turns(t.subagent(AGENT, agent_type="Explore", description="Read the notes"))
    changed_at(t.write(), now - 300)


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return env_of(tmp_path_factory.mktemp("harnesses"))


@pytest.fixture(scope="module")
def seeded(ui_shell) -> None:
    seed(ui_shell.env)


def style(locator, name: str) -> str:
    return locator.evaluate("(node, name) => getComputedStyle(node).getPropertyValue(name)", name)


def check_words(page, route: str) -> None:
    """The turns at `route` say which result failed and which message is an error, in
    words that are heavier than, and the colour of, an ordinary message's text."""
    page.goto(f"/m/sessions/{route}")
    panel = page.locator(PANEL)
    records = panel.locator('ol[data-part="records"]')
    expect(panel.locator('section[data-part="window"]')).to_have_attribute("data-state", "up")
    expect(records.locator(WORD)).to_have_text(["Failed", "Error"])

    failed = records.locator('li[data-kind="tool_result"][data-error="true"]')
    expect(failed).to_have_text("Failed Result: error, 12 B")
    expect(records.locator('li[data-kind="tool_result"][data-error="false"]')).to_have_text("Result: 5 B")

    error = records.locator('li[data-marker="synthetic"]').filter(has=page.locator(WORD))
    expect(error).to_have_text("Error Synthetic: API Error: the request could not be served")
    expect(records.locator('li[data-marker="synthetic"]').filter(has_not=page.locator(WORD))).to_have_text(
        "Synthetic: No response requested.")

    plain = records.locator('li.msg[data-role="user"] [data-line="text"]').first
    for line in (failed, error):
        word = line.locator(WORD)
        expect(word).to_have_css("font-weight", "600")
        assert style(word, "color") == style(plain, "color")
        assert style(plain, "font-weight") == "400"
    # The line after the word keeps the weight it had.
    assert style(failed, "font-weight") == "400"
    assert style(failed, "color") == style(plain, "color")


def test_a_failed_result_and_an_error_message_each_start_with_a_heavier_word(seeded, ui_page):
    check_words(ui_page, f"claude/{sid(1)}")


def test_a_subagents_turns_say_the_same_words(seeded, ui_page):
    check_words(ui_page, f"claude/{sid(1)}/{AGENT}")
