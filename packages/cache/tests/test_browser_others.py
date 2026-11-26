"""What launcher and usage say of the sessions on cache's page, in a browser, through real
shells that start cache's, launcher's and usage's real page servers: what keeps a session
warm, what goes cold next, and what it costs if it does.

On a full install a session a running launch holds is named by the launch where no name
was typed and says where it runs (`slot 4 · atlas-api`, `terminal · kestrel-web`); its
opened `Session` line carries the effort it was launched with and its `Keep warm` line
launcher's state of it. Every timed row's line two ends in what going cold costs, from
usage's context of the session and its price for the record's lifetime, and its opened row
says it in full with a `Usage` link. A session waiting on agents with nothing to keep it
warm is loud and first, and carries `Keep warm`, whose press posts the launch's request id;
a covered one says it is kept warm. Without launcher, or without usage, or with either's
read failing after a first good answer, that module's pieces are absent and nothing stands
in their place.

The transcripts are written and usage's index built in each shell's own environment, and
the launches are rows of the shell's own launch log whose process is this test's own, so
launcher lists them while the tests run. The keep-warm cases are answered in the browser
(`LauncherStub`), with the push stream kept from bringing the real launcher's topics, since
no shell runs a session that waits on agents. Clock records are written whole into each
shell's runtime directory, as the watcher writes them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Any

import pytest
from moxi import modules
from playwright.sync_api import expect

import page_kit
from page_kit import put, record

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"), reason="cache runs on Linux only"),
              pytest.mark.skipif(not {"launcher", "usage"} <= {found.name for found in modules.scan()},
                                 reason="moxi-launcher and moxi-usage are not both installed")]

PANEL = "#moxi-panel > moxi-cache-page"
MODEL = "claude-sonnet-4-5"
#: A long reading, so no record or reading grows old while a test runs.
STALE = 3600


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


#: A session launched in slot 4 at high effort, one launched in a terminal with no effort,
#: one started by hand, a cold one started by hand, and two the keep-warm cases play.
SLOT, TERMINAL, BY_HAND, COLD, LOUD, COVERED = (sid(n) for n in range(0x401, 0x407))
#: Each session's folder and the age of its record's last request, in seconds.
SESSIONS = {SLOT: ("atlas-api", 600), TERMINAL: ("kestrel-web", 900), BY_HAND: ("orbit-cli", 300),
            COLD: ("reviewer-home", 4000), LOUD: ("atlas-api", 1200), COVERED: ("harbor-infra", 1500)}
#: The prompt of every session's newest request: 2 fresh, 150,000 read, 2,000 written.
CONTEXT = 152_002
LAUNCHES = [{"request_id": "r-slot", "session": SLOT, "target": "slot", "slot": "4", "folder": "atlas-api",
             "values": {"name": "builder-1", "model": "sonnet", "effort": "high"}},
            {"request_id": "r-terminal", "session": TERMINAL, "target": "terminal", "slot": None,
             "folder": "kestrel-web", "values": {"name": "planner", "model": "sonnet"}}]

#: Writes one Claude Code session per item of the JSON list in argv[1], `[id, folder]`,
#: under `~/code/<folder>`, its requests by model argv[2].
SEED = """
import json, os, sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for session, folder in json.loads(sys.argv[1]):
    cwd = os.path.join(os.environ["HOME"], "code", folder)
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=session,
                           project=cwd.replace("/", "-"), cwd=cwd, start=now - 7200)
    for n in range(2):
        t.user("Fix the failing test.")
        t.assistant("Done.", usage=T.usage(input_tokens=2, cache_read=150_000, cache_write=2_000,
                                           cache_write_1h=2_000, output=500),
                    request_id=f"req_{session[-3:]}{n}", model=sys.argv[2])
    t.write()
"""

#: Writes the launches of the JSON list in argv[1] to the shell's launch log, each started
#: by the process whose pid is argv[2], so each reads running while that process lives.
LAUNCH = """
import json, os, sys
from moxi import proc
from moxi_launcher import store
me = proc.identity(int(sys.argv[2]))
conn = store.connect()
for spec in json.loads(sys.argv[1]):
    cwd = os.path.join(os.environ["HOME"], "code", spec["folder"])
    values = {"harness": "claude", "target": spec["target"], "cwd": cwd, **spec["values"]}
    store.insert(conn, request_id=spec["request_id"], by="ann", harness="claude",
                 target=spec["target"], cwd=cwd, values=values, argv=["claude"], env={},
                 posture="operator", session_id=spec["session"], slot=spec["slot"])
    store.set_state(conn, spec["request_id"], "started", pid=me.pid, pid_started=me.started)
conn.close()
"""


def run(shell, *argv: str) -> None:
    done = subprocess.run(list(argv), env=shell.env, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")


def made(ui_shells, tmp_path_factory, name: str, leave_out: list[str]):
    """A shell leaving out `leave_out`, over the six sessions: usage's index built where
    usage is there, the two launches written where launcher is, and every record laid."""
    base = tmp_path_factory.mktemp(name)
    shell = ui_shells(leave_out=leave_out, env={"CLAUDE_CONFIG_DIR": str(base / "claude-home")})
    run(shell, sys.executable, "-c", SEED, json.dumps([[one, project] for one, (project, _) in SESSIONS.items()]), MODEL)
    if "usage" not in leave_out:
        run(shell, sys.executable, "-m", "moxi", "usage", "index")
    if "launcher" not in leave_out:
        run(shell, sys.executable, "-c", LAUNCH, json.dumps(LAUNCHES), str(os.getpid()))
    page_kit.put_reading(shell, stale_after=STALE)
    for one, (_, age) in SESSIONS.items():
        put(shell, record(one, age=age, stale_after=STALE, kind="cold" if age > 3600 else "warm"))
    page_kit.published(shell, page_kit.holding(SESSIONS))
    return shell


@pytest.fixture(scope="module")
def full(ui_shells, tmp_path_factory):
    return made(ui_shells, tmp_path_factory, "full", [])


@pytest.fixture(scope="module")
def no_launcher(ui_shells, tmp_path_factory):
    return made(ui_shells, tmp_path_factory, "no-launcher", ["launcher"])


@pytest.fixture(scope="module")
def no_usage(ui_shells, tmp_path_factory):
    return made(ui_shells, tmp_path_factory, "no-usage", ["usage"])


# --- what the page should say, from the shell's own routes -----------------------------------

def money(dollars: float) -> str:
    return "under $0.01" if dollars < 0.005 else f"${dollars:.2f}"


def costs(shell) -> tuple[str, str, str]:
    """The cost of going cold and of a warm turn for a session of `CONTEXT` tokens on an
    hour's cache, from usage's own prices, and the tokens as the page shortens them."""
    status, answer = page_kit.session(shell).get(f"/m/usage/api/prices?models={MODEL}")
    assert status == 200, answer
    rate = answer["models"][0]
    assert rate["cache_write_1h"] is not None and rate["cache_read"] is not None, rate
    return (money(CONTEXT * rate["cache_write_1h"] / 1e6), money(CONTEXT * rate["cache_read"] / 1e6),
            f"{round(CONTEXT / 1000)}k")


# --- launcher's routes answered in the browser -------------------------------------------------

#: Drops each event of launcher's topics `running` and `sessions` from the shell's push
#: stream, so the page knows only what the stubbed routes answer.
DROP_TOPICS = r"""(() => {
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
          if (!/^event: ?launcher\.(running|sessions)\s*$/m.test(block)) out.enqueue(encoder.encode(`${block}\n\n`));
        }
      },
    }));
    return new Response(body, { status: answer.status, statusText: answer.statusText, headers: answer.headers });
  };
})()"""


def iso_in(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


def launch(request_id: str, session: str, name: str, *, waits: int = 0, covered: bool = False,
           coverable: bool = False, effort: str | None = "high") -> dict:
    """One running launch in launcher's declared shape, warm until its record goes cold."""
    project, age = SESSIONS[session]
    return {
        "request_id": request_id, "harness": "claude", "session_id": session, "name": name, "identity": None,
        "where": {"target": "slot", "slot": "2", "project_key": project, "folder": project},
        "started_at": iso_in(-7200), "model": "sonnet", "effort": effort,
        "cache": {"state": "warm", "until": iso_in(3600 - age), "estimated": False},
        "waits": {"kind": "agents", "count": waits} if waits else None,
        "covered": covered, "coverable": {"cap": 3} if coverable else None, "activity": None,
    }


def state(request_id: str, session: str, *, on: bool, holds: tuple[str, ...] = ()) -> dict:
    return {"session_id": session, "request_id": request_id, "relaunch": None, "recycle": None,
            "keep_warm": {"on": on, "holds": list(holds), "nudges": 0, "last": None, "window": None},
            "red": False, "since": None, "why": None, "word": None, "run_id": None, "unreadable": None}


class LauncherStub:
    """Launcher's `running`, `state` and `keep-warm` routes answered in the browser:
    `sessions` is what `running` lists and `states` what `state` does; `status`, when set,
    is what both answer with instead. `presses` holds each body `keep-warm` was posted,
    `answer` its reply, and `pressed`, when set, the sessions `running` lists from the next
    press on. While `held` is a list, a press waits unanswered in it until `release()`."""

    def __init__(self, page: Any, sessions: list[dict], states: list[dict]) -> None:
        self.sessions = sessions
        self.states = states
        self.status: int | None = None
        self.presses: list[dict] = []
        self.answer: dict = {"refused": None}
        self.pressed: list[dict] | None = None
        self.held: list[Any] | None = None
        page.add_init_script(DROP_TOPICS)
        page.route("**/m/launcher/api/running", self._running)
        page.route("**/m/launcher/api/state", self._state)
        page.route("**/m/launcher/api/keep-warm", self._keep_warm)

    def _fulfill(self, route: Any, body: dict) -> None:
        if self.status is not None:
            route.fulfill(status=self.status, content_type="text/plain", body="down")
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def _running(self, route: Any) -> None:
        self._fulfill(route, {"state": "up", "detail": None, "count": len(self.sessions), "sessions": self.sessions})

    def _state(self, route: Any) -> None:
        self._fulfill(route, {"state": "up", "detail": None, "refused": None, "sessions": self.states})

    def _keep_warm(self, route: Any) -> None:
        self.presses.append(route.request.post_data_json)
        if self.held is not None:
            self.held.append(route)
            return
        self._answer(route)

    def release(self) -> None:
        held, self.held = self.held or [], None
        for route in held:
            self._answer(route)

    def _answer(self, route: Any) -> None:
        if self.pressed is not None:
            self.sessions = self.pressed
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.answer))


# --- the page --------------------------------------------------------------------------------

def open_page(page):
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto("/m/cache/")
    panel = page.locator(PANEL)
    expect(panel.locator("moxi-ui-row")).to_have_count(len(SESSIONS))
    return panel, requests


def row(panel, session: str):
    return panel.locator(f'moxi-ui-row[data-key="claude/{session}"]')


def opened(panel, session: str):
    one = row(panel, session)
    one.locator('[data-action="open"]').click()
    expect(one).to_have_attribute("open", "")
    return one.locator('[data-part="opened"]')


def line(det, label: str):
    return det.locator(f'[data-line="{label}"]')


def shown_again(page) -> None:
    """The tab is shown again: the page reads launcher's and usage's routes again."""
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


def no_attribute(locator, name: str) -> None:
    expect(locator).not_to_have_attribute(name, re.compile(r".*"))


def colour(locator) -> str:
    return locator.evaluate("(element) => getComputedStyle(element).color")


def text_colour(page) -> str:
    return page.evaluate("""() => {
      const probe = document.createElement("span");
      probe.style.color = "var(--moxi-text)";
      document.body.append(probe);
      const colour = getComputedStyle(probe).color;
      probe.remove();
      return colour;
    }""")


# --- a full install ------------------------------------------------------------------------------

def test_a_launched_session_is_named_and_placed_by_its_launch_and_says_what_going_cold_costs(full, ui_contexts):
    cold, warm, tokens = costs(full)
    page = ui_contexts(full).pages[0]
    panel, _ = open_page(page)
    slot, terminal, by_hand = row(panel, SLOT), row(panel, TERMINAL), row(panel, BY_HAND)
    expect(slot).to_have_attribute("name", "builder-1")
    expect(slot).to_have_attribute("where", "slot 4 · atlas-api")
    expect(terminal).to_have_attribute("name", "planner")
    expect(terminal).to_have_attribute("where", "terminal · kestrel-web")
    expect(by_hand).to_have_attribute("name", re.compile(r"^orbit-cli · started "))
    no_attribute(by_hand, "where")
    for one in (slot, terminal, by_hand):
        expect(one).to_have_attribute("fact", re.compile(r"^warm until "))
        expect(one).to_have_attribute("rest", re.compile(r"^last request "))
        expect(one).to_have_attribute("tail", f"about {cold} to re-read if cold")
        no_attribute(one, "fact-heavy")
    expect(row(panel, COLD)).to_have_attribute("tail", f"about {cold} to re-read on its next turn")
    expect(panel.locator('[data-action="keep-warm"]')).to_have_count(0)

    det = opened(panel, SLOT)
    expect(line(det, "session")).to_have_text(re.compile(r"^Session\s*Claude Code · .+ · high · started .+ · ~/code/atlas-api\s*Sessions$"))
    expect(line(det, "If cold")).to_have_text(re.compile(
        rf"^If cold\s*Next turn re-reads {tokens} tokens: about {re.escape(cold)}, or {re.escape(warm)} warm · estimate\s*Usage$"))
    expect(line(det, "Keep warm")).to_have_text(
        re.compile(r"^Keep warm\s*off · set at launch; it applies while a session waits on its agents$"))
    labels = det.locator(".r").evaluate_all("all => all.map((one) => one.dataset.line)")
    assert labels == ["Last request", "Goes cold", "If cold", "Keep warm", "session"], labels
    # a terminal launch set no effort; a session started by hand has no Keep warm line
    det = opened(panel, TERMINAL)
    expect(line(det, "session")).not_to_have_text(re.compile(r"· high ·"))
    det = opened(panel, BY_HAND)
    expect(line(det, "Keep warm")).to_have_count(0)
    expect(line(det, "If cold")).to_be_visible()
    line(det, "If cold").get_by_role("link", name="Usage").click()
    expect(page).to_have_url(re.compile(rf"/m/usage/sessions/claude/{BY_HAND}$"))


def test_the_session_waiting_uncovered_is_loud_and_first_and_a_covered_one_is_kept_warm(full, ui_contexts):
    cold, warm, tokens = costs(full)
    page = ui_contexts(full).pages[0]
    stub = LauncherStub(page, [launch("r-loud", LOUD, "director", waits=2, coverable=True),
                               launch("r-covered", COVERED, "planner", waits=1, covered=True)],
                        [state("r-loud", LOUD, on=False), state("r-covered", COVERED, on=True, holds=("builder-1: orders pagination",))])
    panel, _ = open_page(page)
    warm_rows = panel.locator('section[data-group="warm"] moxi-ui-row')
    expect(warm_rows.first).to_have_attribute("data-key", f"claude/{LOUD}")
    loud, covered = row(panel, LOUD), row(panel, COVERED)
    expect(loud).to_have_attribute("fact", re.compile(r"^goes cold at \S"))
    expect(loud).to_have_attribute("fact-heavy", "")
    expect(loud).to_have_attribute("lead", "waiting on 2 agents")
    expect(loud).to_have_attribute("lead-heavy", "")
    no_attribute(loud, "rest")
    expect(loud).to_have_attribute("tail", f"about {cold} to re-read if cold")
    keep = loud.locator('[data-action="keep-warm"]')
    expect(keep).to_have_text("Keep warm")
    assert colour(keep) == text_colour(page)
    expect(covered).to_have_attribute("fact", "kept warm while 1 agent works")
    expect(covered).to_have_attribute("rest", re.compile(r"^last request "))
    no_attribute(covered, "tail")
    no_attribute(covered, "fact-heavy")
    expect(covered.locator('[data-action="keep-warm"]')).to_have_count(0)

    det = opened(panel, COVERED)
    expect(line(det, "Goes cold")).to_have_text(re.compile(r"^Goes cold\s*\S.* if nothing keeps it warm · in .+ · one hour after the last request$"))
    expect(line(det, "Keep warm")).to_have_text(re.compile(r"^Keep warm\s*on · holding for “builder-1: orders pagination”$"))
    labels = det.locator(".r").evaluate_all("all => all.map((one) => one.dataset.line)")
    assert labels == ["Last request", "Goes cold", "Keep warm", "If cold", "session"], labels
    det = opened(panel, LOUD)
    expect(line(det, "Keep warm")).to_have_text(
        re.compile(r"^Keep warm\s*off, and it waits on 2 agents · Keep warm holds the cache until they finish$"))
    expect(line(det, "Goes cold")).not_to_have_text(re.compile("if nothing keeps it warm"))

    # A press posts the launch's request id and is disabled until the answer and the next value.
    stub.held = []
    keep.click()
    expect(keep).to_be_disabled()
    assert stub.presses == [{"request_id": "r-loud"}]
    stub.pressed = [launch("r-loud", LOUD, "director", waits=2, covered=True),
                    launch("r-covered", COVERED, "planner", waits=1, covered=True)]
    stub.release()
    expect(loud).to_have_attribute("fact", "kept warm while 2 agents work")
    expect(loud.locator('[data-action="keep-warm"]')).to_have_count(0)
    no_attribute(loud, "fact-heavy")


def test_a_refused_press_says_why_on_its_row(full, ui_contexts):
    page = ui_contexts(full).pages[0]
    stub = LauncherStub(page, [launch("r-loud", LOUD, "director", waits=2, coverable=True)], [state("r-loud", LOUD, on=False)])
    stub.answer = {"refused": {"code": "unsupported", "problem": "turns can't nudge this session"}}
    panel, _ = open_page(page)
    loud = row(panel, LOUD)
    loud.locator('[data-action="keep-warm"]').click()
    expect(loud).to_have_attribute("note", "Not kept warm: turns can't nudge this session")


def test_a_later_failed_read_of_launchers_leaves_no_piece_of_it(full, ui_contexts):
    page = ui_contexts(full).pages[0]
    stub = LauncherStub(page, [launch("r-slot", SLOT, "builder-1"),
                               launch("r-loud", LOUD, "director", waits=2, coverable=True)],
                        [state("r-slot", SLOT, on=False), state("r-loud", LOUD, on=False)])
    panel, _ = open_page(page)
    slot, loud = row(panel, SLOT), row(panel, LOUD)
    expect(slot).to_have_attribute("name", "builder-1")
    expect(loud).to_have_attribute("lead", "waiting on 2 agents")
    det = opened(panel, SLOT)
    expect(line(det, "Keep warm")).to_be_visible()
    expect(line(det, "session")).to_have_text(re.compile(r"· high ·"))
    stub.status = 500
    with page.expect_response(lambda answer: "/m/launcher/api/running" in answer.url and answer.status == 500):
        shown_again(page)
    expect(slot).to_have_attribute("name", re.compile(r"^atlas-api · started "))
    no_attribute(slot, "where")
    expect(line(det, "Keep warm")).to_have_count(0)
    expect(line(det, "session")).not_to_have_text(re.compile(r"· high ·"))
    expect(loud).to_have_attribute("fact", re.compile(r"^warm until "))
    for name in ("lead", "fact-heavy", "where"):
        no_attribute(loud, name)
    expect(panel.locator('[data-action="keep-warm"]')).to_have_count(0)
    expect(panel.get_by_text(re.compile("launcher", re.I))).to_have_count(0)


def test_a_later_failed_read_of_usages_leaves_no_cost(full, ui_contexts):
    cold, _, _ = costs(full)
    page = ui_contexts(full).pages[0]
    failing = {"on": False}

    def session(route: Any) -> None:
        if failing["on"]:
            route.fulfill(status=500, content_type="text/plain", body="down")
        else:
            route.fallback()

    page.route("**/m/usage/api/session?*", session)
    panel, _ = open_page(page)
    slot = row(panel, SLOT)
    expect(slot).to_have_attribute("tail", f"about {cold} to re-read if cold")
    det = opened(panel, SLOT)
    expect(line(det, "If cold")).to_be_visible()
    failing["on"] = True
    with page.expect_response(lambda answer: "/m/usage/api/session" in answer.url and answer.status == 500):
        shown_again(page)
    expect(panel.locator("moxi-ui-row[tail]")).to_have_count(0)
    expect(line(det, "If cold")).to_have_count(0)
    expect(panel.get_by_text(re.compile(r"\$|re-read|usage", re.I))).to_have_count(0)


# --- small installs ---------------------------------------------------------------------------

def test_without_launcher_nothing_of_a_launch_shows_and_the_cost_still_does(no_launcher, ui_contexts):
    cold, _, _ = costs(no_launcher)
    page = ui_contexts(no_launcher).pages[0]
    panel, requests = open_page(page)
    slot = row(panel, SLOT)
    expect(slot).to_have_attribute("name", re.compile(r"^atlas-api · started "))
    no_attribute(slot, "where")
    expect(slot).to_have_attribute("tail", f"about {cold} to re-read if cold")
    det = opened(panel, SLOT)
    expect(line(det, "If cold")).to_be_visible()
    expect(line(det, "Keep warm")).to_have_count(0)
    expect(line(det, "session")).not_to_have_text(re.compile(r"· high ·"))
    expect(panel.get_by_text(re.compile(r"\bslot\b|keep warm|kept warm|keep-alive", re.I))).to_have_count(0)
    for attribute in ("lead", "fact-heavy", "note"):
        expect(panel.locator(f"moxi-ui-row[{attribute}]")).to_have_count(0)
    assert not [url for url in requests if "/m/launcher/" in url], requests


def test_without_usage_nothing_says_what_going_cold_costs(no_usage, ui_contexts):
    page = ui_contexts(no_usage).pages[0]
    panel, requests = open_page(page)
    slot = row(panel, SLOT)
    expect(slot).to_have_attribute("name", "builder-1")
    expect(slot).to_have_attribute("where", "slot 4 · atlas-api")
    expect(panel.locator("moxi-ui-row[tail]")).to_have_count(0)
    det = opened(panel, SLOT)
    expect(line(det, "Keep warm")).to_be_visible()
    expect(line(det, "If cold")).to_have_count(0)
    expect(panel.get_by_text(re.compile(r"\$|re-read|\bUsage\b"))).to_have_count(0)
    assert not [url for url in requests if "/m/usage/" in url], requests


def test_the_contract_admits_launcher_and_usage_only_where_installed(no_launcher, no_usage, ui_contexts,
                                                                     ui_component_check):
    for shell, allow, reads in ((no_launcher, ("usage",), "/m/usage/api/session"),
                                (no_usage, ("launcher",), "/m/launcher/api/running")):
        page = ui_contexts(shell).pages[0]
        page.goto("/m/ui/")
        check = ui_component_check(page, "cache", "page", allow=allow)
        assert check.element == "moxi-cache-page"
        assert any(reads in url for url in check.requests), check.requests
