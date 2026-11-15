"""comms' page in a browser, through a test shell whose comms page server reads the rig's
record and asks the rig's daemon: who can be messaged and in which session, when a
message to each would be read, which names need launching, who owes a reply, the thread
between two agents and whether comms is working, on the full install and on the small
one; and the acts: a send end to end with `Take back`, `Reply` with the ask quoted, and a
refused check with `Check again`.

Each test starts its own shell after its rig, with the rig's roots in the shell's
environment (`rig_env`), so the shell's comms page server opens the rig's `comms.db`, its
token store and its daemon's socket, all under the test's tmp. The first test proves the
page server reads the rig's record: a message the rig posted is on the page.

Launcher's `/api/running` and `/api/keep-warm` are answered by a route stub in the browser
(`LauncherStub`) in launcher's declared shape, its entries joined to the rig's session
ids. The small install leaves out every module but the core, ui and comms, and comms'
seam finds no cache.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
import urllib.parse
import uuid
from typing import Any

import pytest
from playwright.sync_api import expect

import roster_kit
import shim_drive
from conftest import cache_absent, write_transcript
from moxi import paths as moxi_paths
from moxi_comms import machine_line
from moxi_comms.paths import daemon_socket
from roster_kit import holding, wait_for
from test_reply_enforcement import PLANNER, REVIEWER, P, R, indexed, last, reply, send

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="comms runs on Linux only"),
              pytest.mark.parametrize("backend", ["local"], indirect=True)]

#: The modules the small install leaves out: every one but the core, ui and comms.
SMALL = ["launcher", "cache", "sessions", "usage", "turns", "projects"]
#: The variables of this process the shell's page servers need to work on the rig: comms'
#: roots and device, turns' roots, the Claude transcripts the cache clock reads, sends with
#: no expiry, and the keychain step that is never the user's own.
RIG_ENV = ("MOXI_COMMS_HOME", "MOXI_COMMS_DEVICE", "MOXI_COMMS_RUNTIME_DIR",
           "MOXI_COMMS_DELIVERY_EXPIRY", "MOXI_TURNS_RUNTIME_DIR", "MOXI_TURNS_CONFIG_HOME",
           "CLAUDE_CONFIG_DIR", "PYTHON_KEYRING_BACKEND")
BUILDER = dict(name="builder-1")
B = "builder-1"
#: A time as the page words one today: ui's hour and minute in the browser's locale.
T = r"\d{1,2}:\d\d(?: [AP]M)?"
#: A cold Claude session: its last request two hours ago, past the hour's lifetime.
COLD_AGE_S = 2 * 3600
PAGE = "/m/comms/"
needs_launcher = pytest.mark.skipif(importlib.util.find_spec("moxi_launcher") is None,
                                    reason="the launcher module is not installed here")


def rig_env() -> dict[str, str]:
    """The rig's roots, for the shell's environment. The daemon's socket must fit its
    directory, so the page server never falls back to the core's shared short path."""
    assert len(os.fsencode(daemon_socket())) < moxi_paths.SOCKET_MAX, daemon_socket()
    return {key: os.environ[key] for key in RIG_ENV}


@pytest.fixture
def page_shell(ui_shells, rig):
    """`page_shell(**options)`: a test shell over the rig, stopped when the test ends."""
    started = []

    def start(**options: Any) -> Any:
        shell = ui_shells(env=rig_env(), child_args=["--idle-s", "1"], **options)
        started.append(shell)
        return shell

    yield start
    for shell in started:
        shell.stop()


def bound(rig, *names: str) -> None:
    wait_for(lambda: all(n in rig.daemon.bindings and rig.daemon.bindings[n].session_id
                         for n in names), 5, what=f"{names} to bind")


def cold_shim(rig, who: dict) -> Any:
    """A shim whose session's cache went cold: its last request two hours ago."""
    session_id = str(uuid.uuid4())
    write_transcript(session_id, COLD_AGE_S)
    return rig.shim(**who, session_id=session_id)


def iso_in(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


def running_entry(request_id: str, session_id: str, name: str, *, slot_number: str, project: str,
                  waits: int = 0, covered: bool = False, coverable: bool = False) -> dict:
    """One running launch in launcher's declared shape (`RUNNING_LAUNCH`), warm for 20
    minutes, waiting on `waits` agents."""
    return {
        "request_id": request_id, "harness": "claude", "session_id": session_id, "name": name,
        "identity": None, "where": {"target": "slot", "slot": slot_number, "project_key": project,
                                    "folder": project},
        "started_at": iso_in(-3600),
        "cache": {"state": "warm", "until": iso_in(20 * 60), "estimated": False},
        "waits": {"kind": "agents", "count": waits} if waits else None,
        "covered": covered, "coverable": {"cap": 3} if coverable else None, "activity": None,
    }


class LauncherStub:
    """Launcher's `running` and `keep-warm` routes, answered in the browser: `sessions` is
    what `running` lists, `presses` each body `keep-warm` was posted, `answer` its reply,
    and `pressed`, when set, the sessions `running` lists from the next press on."""

    def __init__(self, page: Any, sessions: list[dict]) -> None:
        self.sessions = sessions
        self.presses: list[dict] = []
        self.answer: dict = {"refused": None}
        self.pressed: list[dict] | None = None
        page.route("**/m/launcher/api/running", self._running)
        page.route("**/m/launcher/api/keep-warm", self._keep_warm)

    def _running(self, route: Any) -> None:
        body = {"state": "up", "detail": None, "count": len(self.sessions), "sessions": self.sessions}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def _keep_warm(self, route: Any) -> None:
        self.presses.append(route.request.post_data_json)
        if self.pressed is not None:
            self.sessions = self.pressed
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.answer))


#: Keeps in `window.pushed` the name of each event the shell's push stream brings.
_PUSHED = """(() => {
  const pushed = window.pushed = [];
  const fetched = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const answer = await fetched(input, init);
    const url = typeof input === "string" ? input : input.url;
    if (!url.includes("/m/ui/api/push") || answer.body === null) return answer;
    const [kept, read] = answer.body.tee();
    (async () => {
      const reader = read.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) return;
        for (const found of value.matchAll(/^event: ?(.+)$/gm)) pushed.push(found[1].trim());
      }
    })().catch(() => {});
    return new Response(kept, { status: answer.status, statusText: answer.statusText,
                                headers: answer.headers });
  };
})()"""


def open_page(shell, ui_contexts, launcher: list[dict] | None = None) -> tuple[Any, Any, Any]:
    """A signed-in page on the shell, once the shell's push stream has brought the modules;
    with `launcher`, the stub serves those launches. The page, its root and the stub.

    The shell places the page again on a modules event when the address differs from the
    one it placed, and the page writes its address as things open; the stream's first
    value of the modules would place a page a test had already acted on anew."""
    page = ui_contexts(shell).pages[0]
    page.add_init_script(_PUSHED)
    stub = LauncherStub(page, launcher) if launcher is not None else None
    page.goto(PAGE)
    page.wait_for_function("() => window.pushed.includes('ui.modules')")
    return page, page.locator("#moxi-panel > moxi-comms-page"), stub


def agent(root, name: str) -> Any:
    return root.locator(f'moxi-ui-row[data-name="{name}"]')


def awaits(data: dict, qualified: str) -> list[str]:
    """The names a roster value says `qualified` waits on."""
    return next(row["awaiting"] for row in data["rows"] if row["qualified"] == qualified)


def ask_him(rig, by: str, text: str) -> Any:
    """`by`'s ask to the operator, one reply wanted, posted under its verified identity."""
    return rig.record.post_as_identity(by, rig.names_channel,
                                       machine_line.compose(["operator"], text, posture="once"))


def address(page) -> str:
    """The page's address after `/m/comms/`."""
    return urllib.parse.unquote(page.evaluate("() => location.pathname").removeprefix(PAGE))


# --- M1: who can I message, and in which session ------------------------------------------------

@needs_launcher
def test_who_can_be_messaged_and_in_which_session(page_shell, rig, ui_contexts):
    planner = rig.shim(**PLANNER)
    reviewer = rig.shim(**REVIEWER)
    bound(rig, P, R)
    send(rig, R, "The cursor commit is in.", "--posture", "none", who=PLANNER)
    shell = page_shell()
    roster_kit.current(shell, holding([P, R]))
    page, root, _ = open_page(shell, ui_contexts, [
        running_entry("r-planner", planner.session_id, "planner's session", slot_number="3", project="atlas-api"),
        running_entry("r-reviewer", reviewer.session_id, "review pass", slot_number="4", project="kestrel-web"),
    ])
    # The page server reads the rig's record: the message the rig posted is today's.
    today = root.locator('[data-group="today"] moxi-ui-row')
    expect(today).to_have_count(1)
    expect(today.first).to_have_attribute("lead", "The cursor commit is in.")
    expect(today.first).to_have_attribute("name", re.compile(r"planner"))

    expect(root.locator('[data-group="agents"] [data-line="label"]')).to_have_text("2 agents")
    expect(agent(root, P)).to_have_attribute("where", "slot 3 · atlas-api")
    expect(agent(root, R)).to_have_attribute("where", "slot 4 · kestrel-web")
    expect(agent(root, R)).to_have_attribute("fact", re.compile(r"^warm until \S+"))

    agent(root, R).locator('[data-action="message"]').click()
    opened = root.locator('[data-part="opened"][data-to="reviewer"]')
    expect(opened.locator('[data-line="session"]')).to_contain_text("review pass · claude")
    link = opened.locator('[data-line="session"] a[data-action="sessions"]')
    expect(link).to_have_attribute("href", f"/m/sessions/claude/{reviewer.session_id}")
    expect(opened.locator('[data-part="box"] textarea')).to_be_focused()


# --- M2: if I message one now, when does it read it -----------------------------------------------

def test_when_a_message_would_be_read_held_for_a_cold_cache_live_for_a_warm_one(
        page_shell, rig, ui_contexts):
    cold_shim(rig, PLANNER)
    rig.shim(**REVIEWER)
    bound(rig, P, R)
    shell = page_shell()
    roster_kit.current(shell, holding([P, R]))
    _, root, _ = open_page(shell, ui_contexts, [])
    expect(agent(root, P)).to_have_attribute("fact", "cold")
    agent(root, R).locator('[data-action="message"]').click()
    expect(root.locator('[data-line="dry"]')).to_contain_text(
        "Delivered. reviewer reads it at its next turn boundary.")
    agent(root, P).locator('[data-action="message"]').click()
    expect(root.locator('[data-part="box"][data-to="planner"]')).to_have_count(1)
    expect(root.locator('[data-line="dry"]')).to_contain_text("Held until planner's next turn.")
    expect(root.locator('[data-part="box"] [data-line="deliver"]')).to_have_text("at its next turn")


def test_on_the_small_install_every_message_is_read_live(page_shell, rig, ui_contexts,
                                                          monkeypatch):
    cache_absent(monkeypatch)
    cold_shim(rig, PLANNER)
    rig.shim(**REVIEWER)
    bound(rig, P, R)
    shell = page_shell(leave_out=SMALL)
    roster_kit.current(shell, holding([P, R]))
    _, root, _ = open_page(shell, ui_contexts)
    # No cache: no fact, no where, and no Deliver.
    for name in (P, R):
        expect(agent(root, name)).not_to_have_attribute("fact", re.compile(r"."))
        expect(agent(root, name)).not_to_have_attribute("where", re.compile(r"."))
    agent(root, P).locator('[data-action="message"]').click()
    expect(root.locator('[data-line="dry"]')).to_contain_text(
        "Delivered. planner reads it at its next turn boundary.")
    expect(root.locator('[data-part="box"] [data-action="deliver"]')).to_have_count(0)
    expect(root.locator('[data-part="box"] [data-line="deliver"]')).to_have_count(0)


# --- M3: which names need launching -------------------------------------------------------------

def test_which_names_need_launching_one_row_per_name_with_its_address_line(
        page_shell, rig, ui_contexts):
    rig.shim(**PLANNER)
    bound(rig, P)
    send(rig, "builder-2", "Pick up the parser.", "--posture", "none", who=PLANNER)
    send(rig, "builder-2@kestrel-web", "And the web build.", "--posture", "none", who=PLANNER)
    shell = page_shell()
    roster_kit.current(shell, holding([P]))
    _, root, _ = open_page(shell, ui_contexts, [])
    rows = root.locator('[data-group="agents"] moxi-ui-row')
    # The name that needs launching comes first, one row for both its addresses.
    expect(rows).to_have_count(2)
    expect(rows.first).to_have_attribute("data-name", "builder-2")
    builder = agent(root, "builder-2")
    expect(builder).to_have_attribute("fact", "2 messages waiting")
    expect(builder).to_have_attribute("lead", "not running")
    # Home names both addresses in full, so each has its own line, the lead's too.
    expect(builder).to_have_attribute("note", re.compile(
        r"^builder-2 · 1 message since .+, from planner; builder-2@kestrel-web · 1 message since .+, from planner$"))
    builder.locator('[data-action="message"]').click()
    opened = root.locator('[data-part="opened"]')
    expect(opened.locator('[data-action="address"]')).to_have_text(["builder-2", "builder-2@kestrel-web"])
    expect(opened.locator('[data-line="waiting"]')).to_contain_text("Pick up the parser.")


# --- Keep warm, through the stub --------------------------------------------------------------

@needs_launcher
def test_keep_warm_presses_launcher_and_a_refusal_leads_line_three(page_shell, rig, ui_contexts):
    planner = rig.shim(**PLANNER)
    bound(rig, P)
    shell = page_shell()
    roster_kit.current(shell, holding([P]))
    loud = running_entry("r-planner", planner.session_id, "planner's session", slot_number="3",
                         project="atlas-api", waits=2, coverable=True)
    page, root, stub = open_page(shell, ui_contexts, [loud])
    row = agent(root, P)
    expect(row).to_have_attribute("fact", re.compile(r"^goes cold at \S+"))
    expect(row).to_have_attribute("lead", "waiting on 2 agents")
    press = row.locator('[data-action="keep-warm"]')
    expect(press).to_have_attribute("title", re.compile(r"at most 3 in a row"))
    stub.answer = {"refused": {"code": "gone", "row": None, "problem": "the launch has ended",
                               "hint": "Launch it again."}}
    press.click()
    expect(row).to_have_attribute("note", re.compile(r"^Not kept warm: the launch has ended"))
    expect(press).to_be_disabled()
    assert stub.presses == [{"request_id": "r-planner"}]
    # The next value of `running` takes the note away and lets the act be pressed again.
    roster_kit.show_tab(page)
    expect(row).not_to_have_attribute("note", re.compile(r"Not kept warm"))
    expect(press).to_be_enabled()
    # A press that goes reads the next value at once; covered, the act is gone.
    stub.answer = {"refused": None}
    stub.pressed = [{**loud, "covered": True}]
    press.click()
    expect(row).to_have_attribute("fact", "kept warm while 2 agents work")
    expect(row.locator('[data-action="keep-warm"]')).to_have_count(0)
    assert stub.presses == [{"request_id": "r-planner"}] * 2


# --- M4: who owes a reply, to him and between agents -----------------------------------------

def test_who_owes_a_reply_to_him_and_between_agents(page_shell, rig, ui_contexts):
    rig.shim(**PLANNER)
    rig.shim(**REVIEWER)
    bound(rig, P, R)
    # critic asked him and its session ended; planner asked reviewer.
    ask_him(rig, "critic", "Which branch takes the fix?")
    indexed(rig, send(rig, R, "Is the cursor fix safe to ship?", "--posture", "once", who=PLANNER).id)
    shell = page_shell()
    roster_kit.current(shell, lambda data: holding([P, R])(data) and awaits(data, P) == [R])
    page, root, _ = open_page(shell, ui_contexts, [])
    rows = root.locator('[data-group="agents"] moxi-ui-row')
    # A message waiting for his reply comes first, on a row of its own though no session holds it.
    expect(rows).to_have_count(3)
    expect(rows.first).to_have_attribute("data-name", "critic")
    critic = agent(root, "critic")
    expect(critic).to_have_attribute("fact", "waiting for your reply")
    expect(critic).to_have_attribute("lead", re.compile(rf"^wrote to you {T}, wants one reply$"))
    expect(critic.locator("button.do")).to_have_text(["Reply", "Message"])
    # Between agents: the asker's row says whom it waits on, and its opened row the thread.
    planner = agent(root, P)
    expect(planner).to_have_attribute("lead", re.compile(rf"^waiting on reviewer's reply since {T}$"))
    planner.locator('[data-action="message"]').click()
    waits = root.locator('[data-part="opened"][data-to="planner"] [data-line="waits:reviewer"]')
    expect(waits).to_contain_text(re.compile(rf"Waits onreviewer · for one reply to its {T} message"))
    waits.locator('[data-action="thread"]').click()
    header = root.locator('[data-part="thread"] [data-line="thread-header"]')
    expect(header).to_have_text("planner and reviewer · 1 message today · reviewer owes one reply")
    assert address(page) == "thread/planner/reviewer"


# --- M5: the thread between planner and builder-1 ----------------------------------------------

def test_the_thread_between_planner_and_builder_1_opens_in_place(page_shell, rig, ui_contexts):
    rig.shim(**PLANNER)
    rig.shim(**BUILDER)
    bound(rig, P, B)
    first = send(rig, B, "Take the parser change.", "--posture", "once", who=PLANNER)
    indexed(rig, first.id)
    indexed(rig, reply(rig, first.id, "On it; the cursor tests first.", who=BUILDER).id)
    indexed(rig, send(rig, B, "And the web build after?", "--posture", "once", who=PLANNER).id)
    shell = page_shell()
    roster_kit.current(shell, lambda data: holding([P, B])(data) and awaits(data, P) == [B])
    page, root, _ = open_page(shell, ui_contexts, [])
    today = root.locator('[data-group="today"] moxi-ui-row')
    expect(today).to_have_count(3)
    expect(today.first).to_have_attribute("lead", "And the web build after?")
    today.first.locator('[data-action="open-thread"]').click()
    # It opens under the newest message between the two, the pair in name order.
    thread = today.first.locator('[data-part="thread"]')
    expect(thread.locator('[data-line="thread-header"]')).to_have_text(
        "builder-1 and planner · 3 messages today · builder-1 owes one reply")
    assert address(page) == "thread/builder-1/planner"
    expect(thread.locator(".tm .h")).to_have_text([
        re.compile(rf"^planner · {T} · wants one reply"),
        re.compile(rf"^builder-1 · {T} · replies · wants no reply"),
        re.compile(rf"^planner · {T} · wants one reply"),
    ])
    expect(thread.locator(".tm p.msg")).to_have_text([
        "Take the parser change.", "On it; the cursor tests first.", "And the web build after?"])
    expect(thread.locator('[data-action="show-earlier"]')).to_have_count(0)
    expect(thread.locator(".foot button")).to_have_text(
        ["Message builder-1", "Message planner", "Copy the last message's id"])
    thread.locator('[data-action="message-side"][data-address="builder-1"]').click()
    expect(root.locator('[data-part="box"][data-to="builder-1"] textarea')).to_be_focused()
    assert address(page) == "agent/builder-1"


# --- M6: is comms working, with the daemon stopped mid-page ----------------------------------

def test_with_the_daemon_stopped_the_page_says_so_and_the_messages_still_read(
        page_shell, rig, ui_contexts):
    rig.shim(**PLANNER)
    bound(rig, P)
    send(rig, "builder-2", "Pick up the parser.", "--posture", "none", who=PLANNER)
    shell = page_shell()
    roster_kit.current(shell, holding([P]))
    page, root, _ = open_page(shell, ui_contexts, [])
    expect(root.locator('[data-line="answering"]')).to_have_text(
        re.compile(rf"^Comms is answering · read {T}"))
    expect(agent(root, P).locator('[data-action="message"]')).to_have_count(1)
    rig.daemon.stop()
    down = root.locator('[data-part="down"]')
    expect(down.locator('[data-line="down"]')).to_have_text("The comms daemon isn't answering",
                                                            timeout=15_000)
    expect(down).to_contain_text("moxi doctor comms")
    expect(down.locator('[data-action="copy-doctor"]')).to_have_count(1)
    expect(root.locator('[data-line="last-read"]')).to_have_text(
        re.compile(rf"^Last read {T}\. It may have changed since\.$"))
    # The last rows stay, dim, with no acts and no box; the messages stay with no Open.
    expect(root.locator('[data-part="agents"]')).to_have_class(re.compile(r"\bstale\b"))
    expect(agent(root, P)).to_have_count(1)
    expect(root.locator('[data-part="agents"] button.do')).to_have_count(0)
    expect(root.locator('[data-part="box"]')).to_have_count(0)
    expect(root.locator('[data-part="messages"] [data-action="open-thread"]')).to_have_count(0)
    # The message reads need no daemon: a message the record gains shows on the next read.
    rig.post_as("reviewer", ["planner"], "Read while the daemon is down.")
    roster_kit.show_tab(page)
    expect(root.locator('[data-group="today"] moxi-ui-row').first).to_have_attribute(
        "lead", "Read while the daemon is down.")


# --- Send, Take back, Reply and Check again -------------------------------------------------

def test_a_send_end_to_end_then_take_back(page_shell, rig, ui_contexts):
    cold_shim(rig, PLANNER)
    bound(rig, P)
    shell = page_shell()
    roster_kit.current(shell, holding([P]))
    page, root, _ = open_page(shell, ui_contexts, [])
    agent(root, P).locator('[data-action="message"]').click()
    box = root.locator('[data-part="box"][data-to="planner"]')
    send_button = box.locator('[data-action="send"]')
    expect(root.locator('[data-line="dry"]')).to_contain_text("Held until planner's next turn.")
    # An outline until the box has text and the check answered that it would send.
    expect(send_button).to_be_disabled()
    box.locator("textarea").fill("The parser change is yours.")
    expect(send_button).to_be_enabled()
    expect(send_button).to_have_class("go")
    send_button.click()
    dry = root.locator('[data-line="dry"]')
    expect(dry.locator(".k")).to_have_text(re.compile(rf"^Sent {T}$"))
    expect(dry).to_contain_text("Held until planner's next turn. Its cache went cold at")
    expect(dry.locator(".d")).to_have_text(
        "1 message now waits for it. You can take a message back until planner has read it.")
    expect(box.locator("textarea")).to_have_value("")
    # Posted as him, to planner.
    posted = last(rig)
    assert machine_line.parse(posted.content).text == "The parser change is yours."
    assert machine_line.parse(posted.content).recipients == ("planner",)
    held = root.locator('[data-line="held"]')
    expect(held).to_contain_text(re.compile(rf"{T} from you · The parser change is yours\."))
    today = root.locator('[data-group="today"] moxi-ui-row').first
    expect(today).to_have_attribute("name", "you")
    expect(today).to_have_attribute("where", "to planner · not read yet, held until its next turn")
    held.locator('[data-action="take-back"]').click()
    expect(root.locator('[data-line="taken"]')).to_contain_text("Taken back.")
    expect(held).to_have_count(0)
    expect(today).to_have_attribute("where", "to planner · taken back")


def test_reply_answers_the_address_that_asked_with_the_ask_quoted(page_shell, rig, ui_contexts):
    rig.shim(**PLANNER)
    bound(rig, P)
    # The asker is planner@proj-b, not the row's lead, the bound planner.
    asked = ask_him(rig, "planner@proj-b", "Ship the cursor fix tonight?")
    shell = page_shell()
    roster_kit.current(shell, holding([P]))
    page, root, _ = open_page(shell, ui_contexts, [])
    row = agent(root, P)
    expect(row).to_have_attribute("fact", "waiting for your reply")
    row.locator('[data-action="reply"]').click()
    assert address(page) == f"reply/{asked.id}"
    box = root.locator('[data-part="box"][data-to="planner@proj-b"]')
    expect(box.locator('[data-line="quote"] .ql')).to_have_text(
        re.compile(rf"^Answering planner's {T} message:$"))
    expect(box.locator('[data-line="quote"] .msg')).to_have_text("Ship the cursor fix tonight?")
    expect(box.locator('[data-action="posture"]')).to_have_text(re.compile(r"^no reply"))
    expect(box.locator("textarea")).to_be_focused()
    box.locator("textarea").fill("Yes, tonight.")
    box.locator('[data-action="send"]').click()
    expect(root.locator('[data-line="dry"] .k')).to_have_text(re.compile(r"^Sent "))
    posted = machine_line.parse(last(rig).content)
    assert (posted.recipients, posted.reply_to, posted.posture) == (
        ("planner@proj-b",), str(asked.id), "none")
    # Answered: the row's case goes, and the address says the row alone.
    expect(row).not_to_have_attribute("fact", "waiting for your reply")
    expect(box.locator('[data-line="quote"]')).to_have_count(0)
    assert address(page) == "agent/planner@proj-b"


def test_a_cache_comms_cannot_read_refuses_the_check_until_check_again(
        page_shell, rig, ui_contexts):
    # A session that has had a turn but has no transcript: comms can't tell whether a
    # message would wake it cold.
    session_id = str(uuid.uuid4())
    reviewer = rig.shim(**REVIEWER, session_id=session_id)
    bound(rig, R)
    shim_drive.turn_started(reviewer)
    shim_drive.turn_ended(reviewer)
    wait_for(lambda: rig.daemon.bindings[R].turned, 5, what="reviewer's turn to reach the daemon")
    shell = page_shell()
    roster_kit.current(shell, holding([R]))
    _, root, _ = open_page(shell, ui_contexts, [])
    agent(root, R).locator('[data-action="message"]').click()
    dry = root.locator('[data-line="dry"]')
    expect(dry.locator("b")).to_have_text("Refused: nothing would be sent.")
    expect(dry).to_contain_text("Comms can't read reviewer's cache right now, so it can't tell "
                                "whether this would wake it cold. Check again in a moment.")
    box = root.locator('[data-part="box"][data-to="reviewer"]')
    box.locator("textarea").fill("Is the cursor fix safe?")
    expect(box.locator('[data-action="send"]')).to_be_disabled()
    write_transcript(session_id, 0)
    dry.locator('[data-action="check-again"]').click()
    expect(dry).to_contain_text("Delivered. reviewer reads it at its next turn boundary.")
    expect(dry.locator('[data-action="check-again"]')).to_have_count(0)
    expect(box.locator('[data-action="send"]')).to_be_enabled()
