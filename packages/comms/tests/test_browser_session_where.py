"""Where is this agent working? comms' page in a browser, on a test shell over the rig, with
sessions' own page server reading the rig's Claude transcripts.

With sessions installed and launcher not, a bound name's row says where its session works
as sessions' header has it, `<folder> · started <time> · <harness>`, and its opened row's
`Session` line names the session and links to its transcript; a name typed in the harness
after the page read the session comes when the row is opened again. A session sessions
can't read shows nothing. With launcher installed, launcher's where wins for a session it runs,
and sessions' fills in for one it doesn't. Without sessions nothing of it shows, and
nothing asks sessions.

The shell starts after the rig, with the rig's roots in its environment (`rig_env`, through
`page_shell`), so its comms page server asks the rig's daemon and its sessions page server
reads the transcripts under the rig's `CLAUDE_CONFIG_DIR`. Launcher's `/api/running` is
answered in the browser (`LauncherStub`), as on the rest of the page's tests.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

import roster_kit
from conftest import assistant_line, cache_absent, projects_root, write_transcript
from roster_kit import holding, wait_for
from test_browser_page import (  # noqa: F401 -- `page_shell` is a fixture the tests take
    _PUSHED, PAGE, SMALL, T, LauncherStub, agent, bound, needs_launcher, page_shell, running_entry,
)
from test_reply_enforcement import PLANNER, REVIEWER, P, R

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="comms runs on Linux only"),
              pytest.mark.parametrize("backend", ["local"], indirect=True)]

needs_sessions = pytest.mark.skipif(importlib.util.find_spec("moxi_sessions") is None,
                                    reason="the sessions module is not installed here")

#: The small install with sessions: every module but the core, ui, comms and sessions left out.
SMALL_WITH_SESSIONS = [module for module in SMALL if module != "sessions"]
#: A session's where as sessions gives it, started a minute ago in a folder `atlas-api`.
STARTED = rf"started (?:today|yesterday) {T}"
SESSIONS_READ = "/m/sessions/api/session"
#: The name sessions gives a session, read in the page as the page reads it, with the key
#: the shell signed it in with.
NAME_READ = """(url) => fetch(url, {headers: {"X-Moxi-Key": localStorage.getItem("moxi-ui-key"),
                                          Accept: "application/json"}, cache: "no-store"})
  .then((answer) => answer.json()).then((value) => value.name)"""


def transcript(tmp_path: Path, work_dir_name: str) -> str:
    """A Claude session whose transcript says it started a minute ago in
    `<tmp>/<work_dir_name>`; its id."""
    session_id = str(uuid.uuid4())
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 60)) + ".000Z"
    write_transcript(session_id, lines=[
        {"type": "user", "timestamp": stamp, "sessionId": session_id,
         "cwd": str(tmp_path / work_dir_name), "message": {"role": "user", "content": "hello"}},
        assistant_line(30),
    ])
    return session_id


def open_page(shell: Any, ui_contexts: Any, launcher: list[dict] | None = None) -> tuple[Any, Any, list[str]]:
    """A signed-in page on the shell once its modules came; with `launcher`, the stub serves
    those launches. The page, its root, and every URL under `/m/sessions/` it asked, with
    the status sessions answered, as `<status> <url>`."""
    page = ui_contexts(shell).pages[0]
    asked: list[str] = []
    page.on("response", lambda answer: asked.append(f"{answer.status} {answer.url}")
            if "/m/sessions/" in answer.url else None)
    page.add_init_script(_PUSHED)
    if launcher is not None:
        LauncherStub(page, launcher)
    page.goto(PAGE)
    page.wait_for_function("() => window.pushed.includes('ui.modules')")
    return page, page.locator("#moxi-panel > moxi-comms-page"), asked


def answered(asked: list[str], session_id: str) -> None:
    """Wait until sessions answered the page's read of `session_id`."""
    wait_for(lambda: any(SESSIONS_READ in url and session_id in url for url in asked), 10,
             what=f"sessions to answer the read of {session_id}")


@needs_sessions
def test_on_the_small_install_sessions_says_where_each_agent_works(
        page_shell, rig, ui_contexts, monkeypatch, tmp_path):
    cache_absent(monkeypatch)
    planner = rig.shim(**PLANNER, session_id=transcript(tmp_path, "atlas-api"))
    # A session with no transcript, so sessions has no record of it: its row says nothing
    # of where.
    reviewer = rig.shim(**REVIEWER, session_id=str(uuid.uuid4()))
    bound(rig, P, R)
    shell = page_shell(leave_out=SMALL_WITH_SESSIONS)
    roster_kit.current(shell, holding([P, R]))
    page, root, asked = open_page(shell, ui_contexts)

    expect(agent(root, P)).to_have_attribute("where", re.compile(rf"^atlas-api · {STARTED} · claude$"))
    answered(asked, reviewer.session_id)
    page.wait_for_timeout(300)
    assert agent(root, R).get_attribute("where") is None
    assert all(answer.startswith("200 ") for answer in asked if planner.session_id in answer), asked

    agent(root, P).locator('[data-action="message"]').click()
    opened = root.locator('[data-part="opened"][data-to="planner"]')
    expect(opened.locator('[data-line="session"]')).to_contain_text(
        re.compile(rf"atlas-api · {STARTED} · claude"))
    link = opened.locator('[data-line="session"] a[data-action="sessions"]')
    expect(link).to_have_attribute("href", f"/m/sessions/claude/{planner.session_id}")

    agent(root, R).locator('[data-action="message"]').click()
    opened = root.locator('[data-part="opened"][data-to="reviewer"]')
    expect(opened.locator('[data-part="box"]')).to_have_count(1)
    expect(opened.locator('[data-line="session"]')).to_have_count(0)


@needs_sessions
def test_a_name_typed_since_the_page_read_the_session_comes_when_its_row_is_opened(
        page_shell, rig, ui_contexts, monkeypatch, tmp_path):
    cache_absent(monkeypatch)
    planner = rig.shim(**PLANNER, session_id=transcript(tmp_path, "atlas-api"))
    rig.shim(**REVIEWER, session_id=transcript(tmp_path, "kestrel-web"))
    bound(rig, P, R)
    shell = page_shell(leave_out=SMALL_WITH_SESSIONS)
    roster_kit.current(shell, holding([P, R]))
    page, root, _ = open_page(shell, ui_contexts)

    def session_line(to: str) -> Any:
        return root.locator(f'[data-part="opened"][data-to="{to}"] [data-line="session"]')

    agent(root, P).locator('[data-action="message"]').click()
    expect(session_line("planner")).to_contain_text(re.compile(rf"atlas-api · {STARTED} · claude"))
    # Planner's session is given a name in the harness after the page read it. Sessions
    # reuses what it gathered for a moment; the row is opened again once it gives the name.
    with (projects_root() / "rig" / f"{planner.session_id}.jsonl").open("a") as transcript_fh:
        transcript_fh.write(json.dumps({"type": "custom-title", "customTitle": "parser fix",
                                        "sessionId": planner.session_id}) + "\n")
    named = f"{SESSIONS_READ}?harness=claude&session={planner.session_id}"
    wait_for(lambda: page.evaluate(NAME_READ, named) == "parser fix", 10,
             what="sessions to give planner's session its name")
    agent(root, R).locator('[data-action="message"]').click()
    expect(session_line("reviewer")).to_contain_text(re.compile(rf"kestrel-web · {STARTED} · claude"))

    agent(root, P).locator('[data-action="message"]').click()
    expect(session_line("planner")).to_contain_text(re.compile(rf"parser fix · {STARTED} · claude"))
    expect(agent(root, P)).to_have_attribute("where", re.compile(rf"^atlas-api · {STARTED} · claude$"))


@needs_sessions
@needs_launcher
def test_launchers_where_wins_and_sessions_fills_in_where_launcher_gives_none(
        page_shell, rig, ui_contexts, tmp_path):
    planner = rig.shim(**PLANNER, session_id=transcript(tmp_path, "planner-home"))
    reviewer = rig.shim(**REVIEWER, session_id=transcript(tmp_path, "kestrel-web"))
    bound(rig, P, R)
    shell = page_shell()
    roster_kit.current(shell, holding([P, R]))
    _, root, asked = open_page(shell, ui_contexts, [
        running_entry("r-planner", planner.session_id, "planner's session", slot_number="3",
                      project="atlas-api"),
    ])
    # Launcher runs planner: its where, never sessions'. Reviewer was started by hand.
    expect(agent(root, P)).to_have_attribute("where", "slot 3 · atlas-api")
    expect(agent(root, R)).to_have_attribute("where", re.compile(rf"^kestrel-web · {STARTED} · claude$"))
    assert not any(planner.session_id in url for url in asked), asked

    agent(root, P).locator('[data-action="message"]').click()
    opened = root.locator('[data-part="opened"][data-to="planner"]')
    expect(opened.locator('[data-line="session"]')).to_contain_text("planner's session · claude")


def test_without_sessions_no_where_shows_and_nothing_asks_sessions(
        page_shell, rig, ui_contexts, monkeypatch, tmp_path):
    cache_absent(monkeypatch)
    rig.shim(**PLANNER, session_id=transcript(tmp_path, "atlas-api"))
    bound(rig, P)
    shell = page_shell(leave_out=SMALL)
    roster_kit.current(shell, holding([P]))
    page, root, asked = open_page(shell, ui_contexts)
    expect(agent(root, P)).to_have_attribute("lead", re.compile(r"."))
    page.wait_for_timeout(500)
    assert agent(root, P).get_attribute("where") is None
    agent(root, P).locator('[data-action="message"]').click()
    opened = root.locator('[data-part="opened"][data-to="planner"]')
    expect(opened.locator('[data-part="box"]')).to_have_count(1)
    expect(opened.locator('[data-line="session"]')).to_have_count(0)
    assert asked == []
