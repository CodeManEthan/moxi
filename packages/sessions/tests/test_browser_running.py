"""The launches running now on sessions' page, in a browser, through a real shell that
starts sessions' real page server: the running group first, its rows named and placed as
Home words them, the session it holds drawn nowhere else, a row opened in place; no group
when nothing runs, and a failed read's line; a filter keeping the launches its list holds,
and no group under the find field; `Keep warm` in the text colour, its press posting the
launch's request id, disabled until the next value, and a refusal's note; the line at a
transcript's start that says a Claude Code session was started by hand, from its head and
launcher's one answer, and a Codex session's plain start line; and a small install, with ui and sessions only, which draws none of it and leaves no gap.

Launcher's `running` and `keep-warm` routes are answered in the browser (`LauncherStub`),
so each test sets the launches it needs, and the shell's push stream is kept from bringing
the real launcher's topic `running`; one test instead puts a launch in each of the topic's
events and leaves the route unanswered, so the group comes from the topic alone. Every
other route is the page servers' own. The
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

from moxi_sessions.testing import CodexRollout
from test_browser_list import changed_at, claude, count_line, env_of, filter_button, group, keys, open_list, sid, tid

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed"),
              pytest.mark.skipif(not any(found.name == "launcher" for found in modules.scan()),
                                 reason="moxi-launcher is not installed")]

PROMPT = "Run the suite and report every diff."
PANEL = "#moxi-panel > moxi-sessions-page"
#: A time as the page words one today: the hour and minute in the browser's locale.
T = r"\d{1,2}:\d\d(?: [AP]M)?"
START_LINE = "This is the start of the session."
#: The modules a small install holds beside the core.
SMALL = ("sessions", "ui")


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


#: Rewrites each event of launcher's topic `running` in the shell's push stream, whose value
#: is the real launcher's: called with null it drops the event, so the page knows only what
#: the stubbed route answers; called with a value it puts that value in the event's place.
#: `window.runningEvents` counts the events it met.
_RUNNING_TOPIC = r"""(value) => {
  const fetched = window.fetch.bind(window);
  window.runningEvents = 0;
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
          if (!/^event: ?launcher\.running\s*$/m.test(block)) {
            out.enqueue(encoder.encode(`${block}\n\n`));
            continue;
          }
          window.runningEvents += 1;
          if (value !== null) out.enqueue(encoder.encode(`event: launcher.running\ndata: ${JSON.stringify(value)}\n\n`));
        }
      },
    }));
    return new Response(body, { status: answer.status, statusText: answer.statusText, headers: answer.headers });
  };
}"""


def running_topic(value: dict | None) -> str:
    """The init script that puts `value` in place of each event of the topic `running`,
    or drops each one when `value` is None."""
    return f"({_RUNNING_TOPIC})({json.dumps(value)})"


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
        page.add_init_script(running_topic(None))
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
    """A shell with every installed module, over the sessions `seed` writes."""
    shell = ui_shells(**env_of(tmp_path_factory.mktemp("full")))
    seed(shell.env)
    return shell


def seed(env: dict[str, str]) -> None:
    """Two Claude Code sessions and a Codex one, all of the last hour."""
    root = Path(env["CLAUDE_CONFIG_DIR"]) / "projects"
    now = time.time()
    claude(root, 1, now - 600, typed=PROMPT, name="fix-tooltips")
    claude(root, 2, now - 1200, typed="Look at the cursor.", work_dir="proj-b")
    rollout = CodexRollout(Path(env["CODEX_HOME"]) / "sessions", thread_id=tid(1), cwd="/work/proj-c", start=now - 2400)
    rollout.turn()
    rollout.user("Do the thing.")
    rollout.assistant("Done.")
    changed_at(rollout.write(), now - 1800)


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


def state_reads(page) -> list[str]:
    """The address of each read of launcher's `/api/state` the page makes from now on."""
    reads: list[str] = []
    page.on("request", lambda request: reads.append(request.url) if "/m/launcher/api/state" in request.url else None)
    return reads


def start_line(page, route: str):
    """The start line of `route`'s transcript, once the page has launcher's answer."""
    with page.expect_response(lambda response: "/m/launcher/api/state" in response.url):
        page.goto(f"/m/sessions/{route}")
    line = page.locator(PANEL).locator('[data-line="start"]')
    expect(line).to_be_visible()
    return line


def test_a_session_started_by_hand_says_so_at_its_start_from_one_read_of_launcher(full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    reads = state_reads(page)
    # The real launcher, whose log holds no launch, refuses the session as not launched.
    line = start_line(page, f"claude/{sid(2)}")
    expect(line).to_have_text(re.compile(rf"^Started (?:today|yesterday) {T} in proj-b, by hand$"))
    assert len(reads) == 1 and reads[0].endswith(f"/m/launcher/api/state?session={sid(2)}"), reads
    # Refresh reads the turns again, not launcher.
    page.locator(PANEL).locator('[data-action="refresh"]').click()
    expect(line).to_contain_text("by hand")
    assert len(reads) == 1, reads


def test_a_session_a_launch_holds_keeps_the_plain_start_line(full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    held = {"state": "up", "detail": None, "refused": None,
            "sessions": [{"session_id": sid(1), "request_id": "r-1", "harness": "claude"}]}
    page.route("**/m/launcher/api/state?*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(held)))
    expect(start_line(page, f"claude/{sid(1)}")).to_have_text(START_LINE)
    # A read that fails says nothing either.
    page.unroute("**/m/launcher/api/state?*")
    page.route("**/m/launcher/api/state?*", lambda route: route.fulfill(status=502, body="down"))
    expect(start_line(page, f"claude/{sid(2)}")).to_have_text(START_LINE)


def test_a_codex_session_launcher_refuses_keeps_the_plain_start_line(full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    reads = state_reads(page)
    # The real launcher, whose log holds no launch, refuses every id as not launched; a
    # Codex record says nothing of who started it, so the page doesn't ask.
    page.goto(f"/m/sessions/codex/{tid(1)}")
    line = page.locator(PANEL).locator('[data-line="start"]')
    expect(line).to_have_text(START_LINE)
    assert reads == [], reads


@pytest.fixture(scope="module")
def small_shell(ui_shells, tmp_path_factory):
    """A shell with ui and sessions only, over the same two sessions."""
    shell = ui_shells(leave_out=[found.name for found in modules.scan() if found.name not in SMALL],
                      **env_of(tmp_path_factory.mktemp("small")))
    seed(shell.env)
    return shell


def labels(view):
    return view.locator('section [data-line="label"]')


def test_the_running_group_comes_first_and_holds_its_sessions_alone(full_shell, ui_contexts):
    _, view, _ = open_with(full_shell, ui_contexts, [
        launch("r-1", sid(1), "builder-1", waits=2, coverable=True),
        launch("r-9", None, "planner", slot="4", project="kestrel-web", said=None)])
    expect(labels(view)).to_have_text(["2 running", "Active recently"])
    rows = running(view).locator("moxi-ui-row")
    expect(rows).to_have_count(2)
    # The uncovered waiting launch leads, named and placed as Home words it.
    first = rows.first
    expect(first).to_have_attribute("data-request", "r-1")
    expect(first).to_have_attribute("data-key", f"claude/{sid(1)}")
    expect(first).to_have_attribute("name", "builder-1")
    expect(first).to_have_attribute("where", "slot 3 · atlas-api")
    expect(first).to_have_attribute("fact", re.compile(rf"^goes cold at {T}$"))
    expect(first).to_have_attribute("fact-heavy", "")
    expect(first).to_have_attribute("lead", "waiting on 2 agents")
    expect(first).to_have_attribute("rest", "Done. I changed three files.")
    # A launch with no session yet: `launched`, and nothing to open.
    second = rows.nth(1)
    expect(second).to_have_attribute("where", "slot 4 · kestrel-web")
    expect(second).to_have_attribute("rest", "launched")
    expect(second.locator('[slot="acts"]')).to_have_count(0)
    # The running session is not drawn again under Active recently.
    assert keys(group(view, "recent").locator("moxi-ui-row"), 2) == [f"claude/{sid(2)}", f"codex/{tid(1)}"]
    # A running row opens in place, as the list's rows do.
    first.get_by_role("button", name="Open").click()
    expect(first).to_have_attribute("open", "")
    expect(first.get_by_role("button", name="Close")).to_be_visible()


def test_a_value_of_launchers_topic_alone_draws_the_group_with_keep_warm(full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    value = {"state": "up", "detail": None, "count": 1,
             "sessions": [launch("r-t", sid(1), "from-topic", waits=2, coverable=True)]}
    page.add_init_script(running_topic(value))
    # The route is never answered, so only the topic can bring the launch.
    held: list[Any] = []
    page.route("**/m/launcher/api/running", lambda route: held.append(route))
    view = open_list(page)
    row = launched_row(view, "r-t")
    expect(row).to_have_attribute("name", "from-topic", timeout=30_000)
    expect(labels(view).first).to_have_text("1 running")
    expect(row.get_by_role("button", name="Keep warm")).to_be_enabled()
    assert page.evaluate("() => window.runningEvents") >= 1
    assert len(held) >= 1


def test_nothing_running_draws_no_group_and_a_failed_read_says_why(full_shell, ui_contexts):
    page, view, stub = open_with(full_shell, ui_contexts, [])
    expect(labels(view)).to_have_text(["Active recently"])
    expect(running(view)).to_have_count(0)
    expect(view.get_by_text("Nothing is running")).to_have_count(0)
    assert keys(group(view, "recent").locator("moxi-ui-row"), 3) == [
        f"claude/{sid(1)}", f"claude/{sid(2)}", f"codex/{tid(1)}"]

    stub.status = 502
    failed = open_list(page)
    expect(labels(failed)).to_have_text(["Running", "Active recently"])
    line = running(failed).locator('[data-line="running-unread"]')
    expect(line).to_contain_text("Moxi couldn't read the running sessions: ")
    expect(line.locator('[data-line="reason"]')).to_have_text("launcher's page server answered 502")


def test_a_filter_keeps_the_launches_its_list_holds_and_find_draws_no_group(full_shell, ui_contexts):
    _, view, _ = open_with(full_shell, ui_contexts, [
        launch("r-1", sid(1), "builder-1"), launch("r-c", tid(1), "codex-run", harness="codex", slot="5")])
    expect(labels(view)).to_have_text(["2 running", "Active recently"])
    button = filter_button(view, "harness")
    button.click()
    view.locator('[data-menu="harness"]').get_by_role("menuitemradio", name="Claude Code").click()
    expect(button).to_have_class("set")
    expect(labels(view)).to_have_text(["1 running", "Active recently"])
    expect(running(view).locator("moxi-ui-row")).to_have_attribute("data-request", "r-1")
    view.get_by_role("button", name="Clear").click()
    expect(labels(view)).to_have_text(["2 running", "Active recently"])

    # The find field's results replace every group, the running one too, and keep its session.
    view.get_by_label("Find a session").fill(sid(1))
    expect(count_line(view)).to_have_text("1 of 3 sessions")
    expect(running(view)).to_have_count(0)
    expect(view.locator("moxi-ui-row")).to_have_attribute("data-key", f"claude/{sid(1)}")


def test_a_small_install_draws_no_running_group_and_no_gap(small_shell, ui_contexts):
    page = ui_contexts(small_shell).pages[0]
    assert page.evaluate("async () => (await import('/m/ui/client.js')).installed('launcher')") is False
    view = open_list(page)
    expect(labels(view)).to_have_text(["Active recently"])
    expect(running(view)).to_have_count(0)
    expect(view.get_by_text("running", exact=False)).to_have_count(0)
    assert keys(group(view, "recent").locator("moxi-ui-row"), 3) == [
        f"claude/{sid(1)}", f"claude/{sid(2)}", f"codex/{tid(1)}"]
    # Active recently is the groups' first element: nothing stands above it.
    groups = view.locator('[data-part="groups"]')
    assert groups.evaluate("(element) => element.firstElementChild.dataset.group") == "recent"
    # A transcript asks launcher nothing and keeps the plain start line.
    reads = state_reads(page)
    page.goto(f"/m/sessions/claude/{sid(2)}")
    expect(page.locator(PANEL).locator('[data-line="start"]')).to_have_text(START_LINE)
    assert reads == []
