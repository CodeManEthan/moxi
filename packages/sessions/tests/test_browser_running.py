"""The launches running now on sessions' page, in a browser, through a real shell that
starts sessions' real page server: `Keep warm` in the text colour, its press posting the
launch's request id, disabled until the next value, and a refusal's note.

Launcher's `running` and `keep-warm` routes are answered in the browser (`LauncherStub`),
so each test sets the launches it needs, and the shell's push stream is kept from bringing
the real launcher's topic `running`; every other route is the page servers' own. The
sessions are written with `moxi_sessions.testing` and timed from the moment the test runs.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from moxi import modules
from playwright.sync_api import expect

from test_browser_list import claude, env_of, open_list, sid

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed"),
              pytest.mark.skipif(not any(found.name == "launcher" for found in modules.scan()),
                                 reason="moxi-launcher is not installed")]

PROMPT = "Run the suite and report every diff."


def iso_in(seconds: float) -> str:
    """A moment `seconds` from now, as launcher writes one."""
    return datetime.fromtimestamp(time.time() + seconds, timezone.utc).isoformat()


def launch(request_id: str, session_id: str | None, name: str, *, harness: str = "claude", slot: str = "3",
           project: str = "atlas-api", waits: int = 0, coverable: bool = False, covered: bool = False,
           said: str | None = "Done. I changed three files.") -> dict:
    """One running launch in launcher's declared shape, warm for 20 minutes, waiting on
    `waits` agents, its latest message `said` by the agent a minute ago."""
    return {
        "request_id": request_id, "harness": harness, "session_id": session_id, "name": name, "identity": None,
        "where": {"target": "slot", "slot": slot, "project_key": project, "folder": project},
        "started_at": iso_in(-3600),
        "cache": {"state": "warm", "until": iso_in(20 * 60), "estimated": False},
        "waits": {"kind": "agents", "count": waits} if waits else None,
        "covered": covered, "coverable": {"cap": 3} if coverable else None,
        "activity": {"at": iso_in(-60), "by": "agent", "text": said} if said is not None else None,
    }


#: Drops from the shell's push stream each event of launcher's topic `running`, whose value
#: is the real launcher's: the page then knows only what the stubbed route answers.
_NO_RUNNING_TOPIC = r"""(() => {
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
          if (!/^event: ?launcher\.running\s*$/m.test(block)) out.enqueue(encoder.encode(`${block}\n\n`));
        }
      },
    }));
    return new Response(body, { status: answer.status, statusText: answer.statusText, headers: answer.headers });
  };
})()"""


class LauncherStub:
    """Launcher's `running` and `keep-warm` routes, answered in the browser: `sessions` is
    what `running` lists, `presses` each body `keep-warm` was posted, `answer` its reply,
    and `pressed`, when set, the sessions `running` lists from the next press on. While
    `held` is a list, a press waits unanswered in it until `release()`. `status`, when
    set, is the status `running` answers with instead of a value."""

    def __init__(self, page: Any, sessions: list[dict]) -> None:
        self.sessions = sessions
        self.presses: list[dict] = []
        self.answer: dict = {"refused": None}
        self.pressed: list[dict] | None = None
        self.status: int | None = None
        self.held: list[Any] | None = None
        page.add_init_script(_NO_RUNNING_TOPIC)
        page.route("**/m/launcher/api/running", self._running)
        page.route("**/m/launcher/api/keep-warm", self._keep_warm)

    def _running(self, route: Any) -> None:
        if self.status is not None:
            route.fulfill(status=self.status, content_type="text/plain", body="down")
            return
        body = {"state": "up", "detail": None, "count": len(self.sessions), "sessions": self.sessions}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def release(self) -> None:
        """Answer each press held since `held` was set, and every later one at once."""
        held, self.held = self.held or [], None
        for route in held:
            self._answer(route)

    def _keep_warm(self, route: Any) -> None:
        self.presses.append(route.request.post_data_json)
        if self.held is not None:
            self.held.append(route)
            return
        self._answer(route)

    def _answer(self, route: Any) -> None:
        if self.pressed is not None:
            self.sessions = self.pressed
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.answer))


@pytest.fixture(scope="module")
def full_shell(ui_shells, tmp_path_factory):
    """A shell with every installed module, over two Claude Code sessions of the last hour."""
    shell = ui_shells(**env_of(tmp_path_factory.mktemp("full")))
    root = Path(shell.env["CLAUDE_CONFIG_DIR"]) / "projects"
    now = time.time()
    claude(root, 1, now - 600, typed=PROMPT, name="fix-tooltips")
    claude(root, 2, now - 1200, typed="Look at the cursor.", work_dir="proj-b")
    return shell


def open_with(shell, ui_contexts, sessions: list[dict], route: str = "") -> tuple[Any, Any, LauncherStub]:
    """A signed-in page on `shell` whose launcher runs `sessions`: the page, the list and
    the stub."""
    page = ui_contexts(shell).pages[0]
    stub = LauncherStub(page, sessions)
    return page, open_list(page, route), stub


def running(view):
    return view.locator('section[data-group="running"]')


def launched_row(view, request_id: str):
    return running(view).locator(f'moxi-ui-row[data-request="{request_id}"]')


def colour(locator) -> str:
    return locator.evaluate("(element) => getComputedStyle(element).color")


def test_keep_warm_is_in_the_text_colour_and_posts_the_request_id(full_shell, ui_contexts):
    page, view, stub = open_with(full_shell, ui_contexts, [
        launch("r-1", sid(1), "builder-1", waits=2, coverable=True)])
    row = launched_row(view, "r-1")
    keep = row.get_by_role("button", name="Keep warm")
    expect(keep).to_be_enabled()
    expect(keep).to_have_attribute("title", "Sends a short turn before each cold time while it waits, at most 3 in a row.")
    # The text colour, as the list's own text has it; Open stays dim.
    assert colour(keep) == colour(view)
    assert colour(keep) != colour(row.locator('[data-action="open"]'))

    # A refused press says why on the row's third line until launcher's next value, which
    # a read of the list brings when its tab is shown again; then the act is back.
    stub.answer = {"refused": {"code": "not-waiting", "row": None, "problem": "it is not waiting", "hint": ""}}
    keep.click()
    expect(row).to_have_attribute("note", "Not kept warm: it is not waiting")
    assert stub.presses == [{"request_id": "r-1"}]
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    expect(row).not_to_have_attribute("note", re.compile(""))
    expect(keep).to_be_enabled()

    # A press that goes through is disabled until launcher's next value, which covers it.
    stub.answer = {"refused": None}
    stub.pressed = [launch("r-1", sid(1), "builder-1", waits=2, covered=True)]
    stub.held = []
    keep.click()
    expect(keep).to_be_disabled()
    stub.release()
    expect(row.get_by_role("button", name="Keep warm")).to_have_count(0)
    assert stub.presses == [{"request_id": "r-1"}, {"request_id": "r-1"}]
