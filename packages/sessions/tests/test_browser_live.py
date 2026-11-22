"""turns' live state on sessions' page, in a browser, through a real shell that starts
sessions' real page server: a running row's state word, what is busy and its trouble line;
the opened row's `Now`, `Agents` and `Prompt box`; the line each launch says when turns'
list can't be read; the band without `Live sessions`, and the list's link to turns' panel
in its place, beside the count or, with no filter line, alone. A small install without turns draws none of it, no link,
and never reads turns; one without sessions keeps `Live sessions` in the band.

Launcher's `running` and turns' `sessions` routes are answered in the browser, and the
shell's push stream is kept from bringing either module's real topic, so each test sets the
launches and the live state it needs. Every other route is the page servers' own.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

import pytest
from moxi import modules
from playwright.sync_api import expect

from test_browser_list import env_of, open_list, sid, tid
from test_browser_running import T, LauncherStub, launch, launched_row, seed

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed"),
              pytest.mark.skipif(not {"launcher", "turns"} <= {found.name for found in modules.scan()},
                                 reason="moxi-launcher and moxi-turns are not both installed")]

#: Drops each event of turns' topic `sessions` from the shell's push stream, so the page
#: knows only what the stubbed route answers.
_DROP_TURNS = r"""(() => {
  const fetched = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const answer = await fetched(input, init);
    const url = typeof input === "string" ? input : input.url;
    if (!url.includes("/m/ui/api/push") || answer.body === null) return answer;
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    let held = "";
    const body = answer.body.pipeThrough(new TransformStream({
      transform(chunk, out) {
        held += decoder.decode(chunk, { stream: true });
        const blocks = held.split("\n\n");
        held = blocks.pop();
        for (const block of blocks) {
          if (!/^event: ?turns\.sessions\s*$/m.test(block)) out.enqueue(encoder.encode(`${block}\n\n`));
        }
      },
    }));
    return new Response(body, { status: answer.status, statusText: answer.statusText, headers: answer.headers });
  };
})()"""


def agent(agent_id: str, kind: str, state: str = "running", description: str | None = None) -> dict:
    return {"id": agent_id, "kind": kind, "state": state, "final": None, "description": description, "parent_id": None}


def live(key: str, session_id: str, *, turn_open: bool = True, agents: list[dict] | None = None,
         trouble: str | None = None, harness: str = "claude") -> dict:
    """One session in turns' declared shape: on turn 9, opened five minutes ago, its agent
    list whole."""
    now = time.time()
    return {
        "key": key, "harness": harness, "pid": 4242, "session_id": session_id, "cwd": "/work/proj-a",
        "started": now - 3600, "capabilities": ["agents"], "endpoint_version": "1.0", "mod_version": "1.0",
        "trouble": trouble,
        "facts": {"session_id": session_id, "turn_number": 9, "turn_open": turn_open,
                  "turn_started_at": now - 300 if turn_open else None, "agents": 0, "draft": False, "stale": False,
                  "ready": None, "at": now},
        "agents": {"entries": agents or [], "complete": True}, "read_error": None, "ended_at": None,
        "has_box": True, "unread_since": None,
    }


class TurnsStub:
    """turns' `sessions` route, answered in the browser: `sessions` is what it lists, and
    `status`, when set, the status it answers with instead. `reads` counts its reads."""

    def __init__(self, page: Any, sessions: list[dict]) -> None:
        self.sessions = sessions
        self.status: int | None = None
        self.reads = 0
        page.add_init_script(_DROP_TURNS)
        page.route("**/m/turns/api/sessions", self._sessions)

    def _sessions(self, route: Any) -> None:
        self.reads += 1
        if self.status is not None:
            route.fulfill(status=self.status, content_type="text/plain", body="down")
            return
        body = {"now": time.time(), "listed": True, "detail": None, "home": "/home/you", "reach": None,
                "sessions": self.sessions}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


@pytest.fixture(scope="module")
def full_shell(ui_shells, tmp_path_factory):
    """A shell with every installed module, over the sessions `seed` writes."""
    shell = ui_shells(**env_of(tmp_path_factory.mktemp("live-full")))
    seed(shell.env)
    return shell


def band(page) -> list[str]:
    """The band's names, in order, once the shell has drawn it."""
    expect(page.locator("#moxi-pages a").first).to_be_attached()
    return page.locator("#moxi-pages a").evaluate_all("(links) => links.map((link) => link.textContent)")


LAUNCHES = [launch("r-1", sid(1), "planner"), launch("r-2", sid(2), "builder-1", slot="4"),
            launch("r-3", tid(1), "codex-run", harness="codex", slot="5"), launch("r-4", None, "fresh", slot="6", said=None)]


def test_running_rows_say_their_state_what_is_busy_and_their_trouble(full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    TurnsStub(page, [
        live("claude-a", sid(1), agents=[agent("a1", "subagent", description="Check the plan"),
                                         agent("a2", "subagent", "done")]),
        live("claude-b", sid(2), turn_open=False, agents=[agent("s1", "shell"), agent("s2", "shell")]),
        live("codex-c", tid(1), turn_open=False, harness="codex",
             trouble="Codex load verification did not finish within 30 s"),
    ])
    LauncherStub(page, LAUNCHES)
    view = open_list(page)
    working = launched_row(view, "r-1")
    expect(working).to_have_attribute("lead", "working · 1 subagent busy")
    expect(working).not_to_have_attribute("lead-heavy", re.compile(""))
    expect(working).not_to_have_attribute("note", re.compile(""))
    # Idle says no state word: only what is busy.
    expect(launched_row(view, "r-2")).to_have_attribute("lead", "2 shells running")
    trouble = launched_row(view, "r-3")
    expect(trouble).not_to_have_attribute("lead", re.compile(""))
    expect(trouble).to_have_attribute(
        "note", "Its link to Moxi has trouble: Codex load verification did not finish within 30 s")
    # A launch turns has no session for says nothing of it.
    fresh = launched_row(view, "r-4")
    expect(fresh).not_to_have_attribute("lead", re.compile(""))
    expect(fresh).not_to_have_attribute("note", re.compile(""))

    # Opened, the row adds turns' lines, each with its label.
    working.get_by_role("button", name="Open").click()
    opened = working.locator('[data-part="opened"]')
    expect(opened.locator('[data-line="now"]')).to_have_text(re.compile(rf"^Now\s*working on turn 9 since {T}$"))
    expect(opened.locator('[data-line="agent"]')).to_have_text(
        [re.compile(r"Check the plan · subagent ·\s*running"), re.compile(r"^subagent ·\s*done$")])
    expect(opened.locator('[data-line="prompt-box"]')).to_have_text(re.compile(r"^Prompt box\s*empty$"))


def test_after_a_heavy_lead_turns_words_come_second(full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    TurnsStub(page, [live("claude-a", sid(1), turn_open=False, agents=[agent("s1", "shell")])])
    LauncherStub(page, [launch("r-1", sid(1), "builder-1", waits=2, coverable=True)])
    row = launched_row(open_list(page), "r-1")
    expect(row).to_have_attribute("lead", "waiting on 2 agents")
    expect(row).to_have_attribute("lead-heavy", "")
    expect(row).to_have_attribute("lead-more", "1 shell running")


def test_when_turns_cant_be_read_each_launch_with_a_session_says_so(full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    stub = TurnsStub(page, [])
    stub.status = 502
    LauncherStub(page, LAUNCHES)
    view = open_list(page)
    expect(launched_row(view, "r-1")).to_have_attribute(
        "note", "Moxi can't read its live state: turns' page server answered 502")
    expect(launched_row(view, "r-4")).not_to_have_attribute("note", re.compile(""))


def test_live_sessions_leaves_the_band_and_the_list_links_it(full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    TurnsStub(page, [])
    LauncherStub(page, [])
    view = open_list(page)
    names = band(page)
    assert "Sessions" in names and "Live sessions" not in names, names
    filters = view.locator('[data-part="filters"]')
    link = filters.get_by_role("link", name="Live sessions")
    expect(link).to_have_attribute("href", "/m/turns/")
    expect(filters.locator(".n")).to_have_text(re.compile(r"^Live sessions\s*·\s*\d+ sessions$"))
    link.click()
    expect(page).to_have_url(re.compile(r"/m/turns/$"))
    expect(page.locator("#moxi-panel > moxi-turns-page")).to_be_attached()
    expect(page.locator("#moxi-pages-button")).to_have_accessible_name("Live sessions")
    expect(page).to_have_title("Live sessions - Moxi")


@pytest.fixture(scope="module")
def unlisted_shell(ui_shells, tmp_path_factory):
    """A shell with every installed module on a machine where no session is listed."""
    return ui_shells(**env_of(tmp_path_factory.mktemp("live-unlisted")))


def test_with_a_launch_running_and_no_session_listed_the_link_stands_alone(unlisted_shell, ui_contexts):
    page = ui_contexts(unlisted_shell).pages[0]
    TurnsStub(page, [])
    LauncherStub(page, [launch("r-1", sid(1), "planner")])
    view = open_list(page)
    expect(launched_row(view, "r-1")).to_have_attribute("name", "planner")
    expect(view.locator('[data-part="filters"]')).to_have_count(0)
    link = view.locator('[data-line="turns"]').get_by_role("link", name="Live sessions")
    expect(link).to_have_attribute("href", "/m/turns/")
    link.click()
    expect(page.locator("#moxi-panel > moxi-turns-page")).to_be_attached()


@pytest.fixture(scope="module")
def no_turns_shell(ui_shells, tmp_path_factory):
    """A shell with every installed module but turns."""
    shell = ui_shells(leave_out=["turns"], **env_of(tmp_path_factory.mktemp("live-no-turns")))
    seed(shell.env)
    return shell


def test_without_turns_nothing_of_it_is_drawn_and_turns_is_never_read(no_turns_shell, ui_contexts):
    page = ui_contexts(no_turns_shell).pages[0]
    assert page.evaluate("async () => (await import('/m/ui/client.js')).installed('turns')") is False
    reads: list[str] = []
    page.on("request", lambda request: reads.append(request.url) if "/m/turns/" in request.url else None)
    LauncherStub(page, [launch("r-1", sid(1), "planner")])
    view = open_list(page)
    row = launched_row(view, "r-1")
    expect(row).to_have_attribute("name", "planner")
    expect(view.locator('a[data-action="turns"]')).to_have_count(0)
    expect(view.locator('[data-part="filters"] .n')).to_have_text(re.compile(r"^\d+ sessions?$"))
    expect(row).not_to_have_attribute("lead", re.compile(""))
    expect(row).not_to_have_attribute("note", re.compile(""))
    row.get_by_role("button", name="Open").click()
    opened = row.locator('[data-part="opened"]')
    expect(opened.locator('[data-line="session"]')).to_be_visible()
    expect(opened.locator('[data-line="now"], [data-line="agents"], [data-line="prompt-box"]')).to_have_count(0)
    assert reads == [], reads


@pytest.fixture(scope="module")
def no_sessions_shell(ui_shells, tmp_path_factory):
    """A shell with every installed module but sessions."""
    return ui_shells(leave_out=["sessions"], **env_of(tmp_path_factory.mktemp("live-no-sessions")))


def test_without_sessions_live_sessions_stays_in_the_band(no_sessions_shell, ui_contexts):
    page = ui_contexts(no_sessions_shell).pages[0]
    page.goto("/m/ui/")
    names = band(page)
    assert "Live sessions" in names and "Sessions" not in names, names
