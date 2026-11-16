"""comms' page in a browser, through a test shell whose comms page server reads the rig's
record and asks the rig's daemon: who can be messaged and in which session, when a
message to each would be read, which names need launching, who owes a reply, the thread
between two agents and whether comms is working, on the full install and on the small
one; and the acts: a send end to end with `Take back`, `Reply` with the ask quoted, and a
refused check with `Check again`. Then each address, `Earlier today` and `Before today`,
the empty state with launcher and without, the daemon's error, markup drawn as text, the
page at 1280 and 400 px in the light and the dark theme with colour from the text tokens
and the accent on Send alone, and what the small install leaves out.

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
from moxi.store import transaction
from moxi_comms import machine_line, store
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
    and `pressed`, when set, the sessions `running` lists from the next press on. Between
    `hold()` and `release()` a read of `running` waits unanswered."""

    def __init__(self, page: Any, sessions: list[dict]) -> None:
        self.sessions = sessions
        self.presses: list[dict] = []
        self.answer: dict = {"refused": None}
        self.pressed: list[dict] | None = None
        self.held: list[Any] | None = None
        page.route("**/m/launcher/api/running", self._running)
        page.route("**/m/launcher/api/keep-warm", self._keep_warm)

    def hold(self) -> None:
        """Answer no read of `running` until `release()`."""
        self.held = []

    def release(self) -> None:
        """Answer each read of `running` held since `hold()`, and every later one at once."""
        held, self.held = self.held or [], None
        for route in held:
            self._running(route)

    def _running(self, route: Any) -> None:
        if self.held is not None:
            self.held.append(route)
            return
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


def visit(page, route: str) -> Any:
    """The page at `route` after `/m/comms/`, placed anew, once the shell has its modules;
    its root."""
    page.goto(PAGE + route)
    page.wait_for_function("() => window.pushed.includes('ui.modules')")
    return page.locator("#moxi-panel > moxi-comms-page")


def no_standing_names() -> None:
    """Retire the rig's standing names, so the roster has no row."""
    conn = store.open_write()
    try:
        with transaction(conn):
            conn.execute("UPDATE identities SET status = 'retired'")
    finally:
        conn.close()


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
    # The page also reads `running` on its own: on the roster stream's first value, which
    # can come late, and on its timer. Any value after the answer takes the note, so no
    # read is answered until the note has been seen.
    stub.hold()
    press.click()
    expect(row).to_have_attribute("note", re.compile(r"^Not kept warm: the launch has ended"))
    expect(press).to_be_disabled()
    assert stub.presses == [{"request_id": "r-planner"}]
    # The next value of `running` takes the note away and lets the act be pressed again.
    stub.release()
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
    expect(down.locator('[data-line="down"]')).to_have_text("The comms daemon isn't answering")
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


def test_a_reply_to_an_asker_with_no_session_keeps_its_sent_line(page_shell, rig, ui_contexts):
    rig.shim(**PLANNER)
    bound(rig, P)
    asked = ask_him(rig, "critic", "Is the review done?")
    shell = page_shell()
    roster_kit.current(shell, holding([P]))
    page, root, _ = open_page(shell, ui_contexts, [])
    row = agent(root, "critic")
    expect(row).to_have_attribute("fact", "waiting for your reply")
    row.locator('[data-action="reply"]').click()
    assert address(page) == f"reply/{asked.id}"
    box = root.locator('[data-part="box"][data-to="critic"]')
    box.locator("textarea").fill("Done; two notes on the parser.")
    box.locator('[data-action="send"]').click()
    dry = root.locator('[data-line="dry"]')
    expect(dry.locator(".k")).to_have_text(re.compile(rf"^Sent {T}$"))
    assert machine_line.parse(last(rig).content).reply_to == str(asked.id)
    # The ask is answered and the reply waits for critic: whatever each read brings first,
    # the row stays open with the box that sent and its answer.
    expect(row).to_have_attribute("fact", "1 message waiting")
    expect(row).to_have_attribute("open", "")
    expect(dry.locator(".k")).to_have_text(re.compile(rf"^Sent {T}$"))
    expect(dry).to_contain_text("critic isn't running")
    assert address(page) == "agent/critic"
    expect(root.locator('[data-line="foot-address"]')).to_have_count(0)


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


# --- The addresses ------------------------------------------------------------------------------

def test_each_address_opens_its_piece_and_writing_one_adds_no_history(page_shell, rig, ui_contexts):
    rig.shim(**PLANNER)
    rig.shim(**REVIEWER)
    bound(rig, P, R)
    asked = ask_him(rig, "critic", "Which branch takes the fix?")
    between = send(rig, R, "Is the cursor fix safe to ship?", "--posture", "once", who=PLANNER)
    shell = page_shell()
    roster_kit.current(shell, holding([P, R]))
    page, root, _ = open_page(shell, ui_contexts, [])
    expect(agent(root, R)).to_have_count(1)
    expect(root.locator('[data-part="opened"]')).to_have_count(0)
    # Opening and closing writes the address in place: Back still leaves the page.
    entries = page.evaluate("history.length")
    agent(root, R).locator('[data-action="message"]').click()
    assert address(page) == "agent/reviewer"
    agent(root, R).locator('[data-action="close"]').click()
    assert address(page) == ""
    root.locator('[data-action="show-others"]').click()
    assert address(page) == "others"
    assert page.evaluate("history.length") == entries

    root = visit(page, "agent/reviewer")
    expect(root.locator('[data-part="opened"][data-to="reviewer"] [data-part="box"]')).to_have_count(1)
    root = visit(page, f"reply/{asked.id}")
    expect(root.locator('[data-part="box"][data-to="critic"] [data-line="quote"] .msg')).to_have_text(
        "Which branch takes the fix?")
    root = visit(page, "thread/planner/reviewer")
    expect(root.locator('[data-line="thread-header"]')).to_have_text(
        re.compile(r"^planner and reviewer · 1 message today"))
    root = visit(page, "mail/planner")
    expect(agent(root, P).locator('[data-line="thread-header"]')).to_have_text(
        re.compile(r"^All mail with planner · 1 message today"))
    root = visit(page, "others")
    expect(root.locator('[data-group="others"] moxi-ui-row')).to_have_count(3)
    expect(root.locator('[data-group="others"] moxi-ui-row').first).to_have_attribute("name", "herald")
    # A name the page doesn't know, a message no longer owed, and no view at all.
    root = visit(page, "agent/nobody")
    expect(root.locator('[data-line="foot-address"]')).to_have_text("nobody isn't on this page.")
    expect(root.locator('[data-part="opened"]')).to_have_count(0)
    root = visit(page, f"reply/{between.id}")
    expect(root.locator('[data-line="foot-address"]')).to_have_text(
        "That message no longer waits for your reply.")
    expect(root.locator('[data-part="box"][data-to="planner"] [data-line="quote"]')).to_have_count(0)
    expect(root.locator('[data-part="box"][data-to="planner"]')).to_have_count(1)
    root = visit(page, "nowhere/at-all")
    expect(root.locator('[data-line="foot-address"]')).to_have_text("There is no such view here.")


# --- Earlier today, Before today -------------------------------------------------------------

def test_earlier_today_and_before_today_add_their_messages(page_shell, rig, ui_contexts):
    rig.shim(**PLANNER)
    bound(rig, P)
    now = rig.clock.t
    rig.clock.t = now - 86400
    for n in range(2):
        rig.post_as("reviewer", [P], f"Yesterday's note {n}.")
    rig.clock.t = now
    for n in range(14):
        rig.post_as("reviewer", [P], f"Today's note {n}.")
    shell = page_shell()
    roster_kit.current(shell, holding([P]))
    _, root, _ = open_page(shell, ui_contexts, [])
    today = root.locator('[data-group="today"] moxi-ui-row')
    expect(today).to_have_count(12)
    expect(today.first).to_have_attribute("lead", "Today's note 13.")
    earlier = root.locator('[data-group="earlier-today"]')
    expect(earlier.locator('[data-line="more"] span')).to_have_text("2 more messages")
    earlier.locator("button").click()
    expect(today).to_have_count(14)
    expect(today.last).to_have_attribute("lead", "Today's note 0.")
    expect(root.locator('[data-group="earlier-today"]')).to_have_count(0)
    before = root.locator('[data-group="before"]')
    expect(before.locator(".gk")).to_have_text("Before today")
    expect(before.locator('[data-line="before"] span')).to_have_text(
        re.compile(r"^2 messages, back to \w{3} \w{3} \d{1,2}$"))
    before.locator("button").click()
    yesterday = root.locator('[data-part="messages"] section.grp').nth(1)
    expect(yesterday.locator(".gk")).to_have_text("Yesterday")
    expect(yesterday.locator("moxi-ui-row")).to_have_count(2)
    expect(yesterday.locator("moxi-ui-row").first).to_have_attribute("lead", "Yesterday's note 1.")
    expect(root.locator('[data-group="before"]')).to_have_count(0)


# --- The states --------------------------------------------------------------------------------

@needs_launcher
def test_the_empty_state_offers_a_launch_from_home_with_launcher(page_shell, rig, ui_contexts):
    no_standing_names()
    shell = page_shell()
    roster_kit.current(shell, holding([], standing=[]))
    _, root, _ = open_page(shell, ui_contexts, [])
    expect(root.locator('[data-line="empty"]')).to_have_text("No agents yet.")
    empty = root.locator('[data-part="empty"]')
    expect(empty).to_contain_text("An agent is a name that one session holds at a time")
    expect(empty.locator('[data-action="launch-home"]')).to_have_text("Launch one from Home")
    expect(empty.locator('[data-action="launch-home"]')).to_have_attribute("href", "/m/ui/")
    expect(empty.locator("code")).to_have_count(0)
    expect(root.locator('[data-part="agents"]')).to_have_count(0)


def test_the_empty_state_gives_the_command_without_launcher(page_shell, rig, ui_contexts,
                                                            monkeypatch):
    cache_absent(monkeypatch)
    no_standing_names()
    shell = page_shell(leave_out=SMALL)
    roster_kit.current(shell, holding([], standing=[]))
    _, root, _ = open_page(shell, ui_contexts)
    expect(root.locator('[data-line="empty"]')).to_have_text("No agents yet.")
    empty = root.locator('[data-part="empty"]')
    expect(empty.locator("code")).to_have_text("moxi comms claude --name NAME")
    expect(empty.locator('[data-action="copy-command"]')).to_have_text("Copy")
    expect(empty.locator('[data-action="launch-home"]')).to_have_count(0)


def test_an_error_from_the_daemon_takes_the_first_row_with_a_fixed_line(
        page_shell, rig, ui_contexts, monkeypatch):
    def fails(frame):
        raise OSError("/x/y can't be read")

    rig.shim(**PLANNER)
    bound(rig, P)
    monkeypatch.setattr(rig.daemon, "_agents_query", fails)
    shell = page_shell()
    roster_kit.current(shell, lambda data: data["state"] == "error")
    _, root, _ = open_page(shell, ui_contexts, [])
    down = root.locator('[data-part="down"] [data-line="down"]')
    expect(down).to_have_text(re.compile(r"\S"))
    expect(down).not_to_contain_text("/x/y")
    expect(root.locator('[data-part="box"]')).to_have_count(0)
    expect(root.locator('[data-line="answering"]')).to_have_text("")


# --- Markup as text ------------------------------------------------------------------------------

#: A message whose text is markup: the page draws it as text, everywhere it shows.
MARKUP = '<img src="x" onerror="window.markupRan = 1"><b>bold</b> & <script>window.markupRan = 2</script>'
#: Whether any element of these tags is under the page, in any shadow tree.
_TAGS_UNDER = """(names) => {
  const panel = document.querySelector("#moxi-panel > moxi-comms-page");
  const found = [];
  const walk = (root) => { for (const node of root.querySelectorAll("*")) {
    if (names.includes(node.localName)) found.push(node.localName);
    if (node.shadowRoot) walk(node.shadowRoot);
  } };
  walk(panel.shadowRoot);
  return found;
}"""


def test_markup_in_a_message_is_drawn_as_text(page_shell, rig, ui_contexts):
    rig.shim(**PLANNER)
    bound(rig, P)
    ask_him(rig, "critic", MARKUP)
    send(rig, "builder-2", MARKUP, "--posture", "none", who=PLANNER)
    shell = page_shell()
    roster_kit.current(shell, holding([P]))
    page, root, _ = open_page(shell, ui_contexts, [])
    today = root.locator('[data-group="today"] moxi-ui-row')
    expect(today).to_have_count(2)
    expect(today.first).to_have_attribute("lead", MARKUP)
    expect(today.first.locator("css=.lead, .said").first).to_contain_text("<img")
    # The ask quoted in the box, the queue of a name no session holds, and the thread.
    agent(root, "critic").locator('[data-action="reply"]').click()
    expect(root.locator('[data-line="quote"] .msg')).to_have_text(MARKUP)
    agent(root, "builder-2").locator('[data-action="message"]').click()
    expect(root.locator('[data-line="waiting"] .msg')).to_contain_text(MARKUP)
    today.first.locator('[data-action="open-thread"]').click()
    expect(root.locator('[data-part="thread"] .tm p.msg')).to_have_text([MARKUP])
    assert page.evaluate(_TAGS_UNDER, ["img", "b", "script"]) == []
    assert page.evaluate("() => window.markupRan") is None


# --- Widths, themes and colour ---------------------------------------------------------------

#: The tokens a piece of the page may take a colour from: the text tokens and the neutral
#: border and background ones; and the accent, on Send alone (with the colour set on it).
ALLOWED = {"text", "text-dim", "text-faint", "border", "border-strong", "bg", "bg-raised",
           "bg-sunken", "bg-hover"}
#: For each colour token the shell's style sheets define, the nodes under the page whose
#: colours change when that token alone changes on the page's element, and in which
#: property. The walk goes into every shadow tree under the page. Focus is taken off
#: first, and the outline's colour is left out: a focus ring takes the accent on every
#: element.
REACHED = """async () => {
  const panel = document.querySelector("#moxi-panel > moxi-comms-page");
  function* tree(root) {
    for (const node of root.querySelectorAll("*")) {
      yield node;
      if (node.shadowRoot) yield* tree(node.shadowRoot);
    }
  }
  let focused = document.activeElement;
  while (focused?.shadowRoot?.activeElement) focused = focused.shadowRoot.activeElement;
  focused?.blur?.();
  const nodes = [panel, ...tree(panel.shadowRoot), ...tree(panel)];
  const props = ["color", "background-color", "background-image", "border-top-color",
                 "border-right-color", "border-bottom-color", "border-left-color",
                 "text-decoration-color", "box-shadow", "fill", "stroke", "stop-color"];
  const frames = () => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
  const colours = () => nodes.map((node) => {
    const style = getComputedStyle(node);
    return props.map((name) => style.getPropertyValue(name));
  });
  const names = new Set();
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch { continue; }
    for (const rule of rules) {
      if (!rule.style) continue;
      for (const name of rule.style) if (name.startsWith("--moxi-")) names.add(name.slice(7));
    }
  }
  const what = (node) => node.localName + (node.dataset?.action ? `[${node.dataset.action}]` : "")
    + (node.className && typeof node.className === "string" ? `.${node.className.split(" ").join(".")}` : "");
  await frames();
  const before = colours();
  const reached = {};
  for (const token of [...names].sort()) {
    if (/^(font|radius|gap)/.test(token)) continue;
    panel.style.setProperty(`--moxi-${token}`, "rgb(1, 2, 3)");
    await frames();
    const after = colours();
    panel.style.removeProperty(`--moxi-${token}`);
    const changed = [];
    after.forEach((seen, n) => seen.forEach((value, i) => {
      if (value !== before[n][i]) changed.push(`${what(nodes[n])} ${props[i]}`);
    }));
    if (changed.length) reached[token] = [...new Set(changed)];
  }
  await frames();
  return {reached, text: getComputedStyle(panel).color};
}"""


#: At a width where a row keeps each line to one, the elements under the page that reach
#: past the viewport's right edge and are not inside a box that cuts them with an
#: ellipsis (the row's own cut), and the document's horizontal scroll.
PAST_THE_EDGE = """() => {
  const edge = document.documentElement.clientWidth;
  const panel = document.querySelector("#moxi-panel > moxi-comms-page");
  const up = (node) => node.parentElement ?? node.getRootNode().host ?? null;
  const cut = (node) => {
    for (let at = up(node); at !== null; at = up(at)) {
      const style = getComputedStyle(at);
      if (style.textOverflow === "ellipsis" && style.overflowX !== "visible")
        return at.getBoundingClientRect().right <= edge + 0.5;
    }
    return false;
  };
  const past = [];
  const walk = (root) => { for (const node of root.querySelectorAll("*")) {
    const box = node.getBoundingClientRect();
    if (box.width > 0 && box.right > edge + 0.5 && !cut(node)) past.push(`${node.localName}.${node.className}`);
    if (node.shadowRoot) walk(node.shadowRoot);
  } };
  walk(panel.shadowRoot);
  return {past, scroll: document.documentElement.scrollWidth - edge};
}"""


def test_the_page_fits_both_widths_in_both_themes_and_only_send_takes_the_accent(
        page_shell, rig, ui_contexts, ui_width_check):
    cold_shim(rig, PLANNER)
    rig.shim(**REVIEWER)
    bound(rig, P, R)
    ask_him(rig, "critic", "Which branch takes the fix? " * 12)
    send(rig, "builder-2@kestrel-web", "A long brief for the parser change. " * 10, "--posture", "none",
         who=PLANNER)
    shell = page_shell()
    roster_kit.current(shell, holding([P, R]))
    page, root, _ = open_page(shell, ui_contexts, [])
    agent(root, R).locator('[data-action="message"]').click()
    box = root.locator('[data-part="box"][data-to="reviewer"]')
    box.locator("textarea").fill("Is the cursor fix safe to ship?")
    expect(box.locator('[data-action="send"]')).to_have_class("go")
    expect(root.get_by_role("heading")).to_have_count(0)
    texts = set()
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        seen = page.evaluate(f"({REACHED})()")
        texts.add(seen["text"])
        reached = seen["reached"]
        refused = {token: where for token, where in reached.items()
                   if token not in ALLOWED | {"accent", "on-accent"}}
        assert not refused, f"{scheme}: colour from {refused}"
        for token in ("accent", "on-accent"):
            assert reached.get(token) and all(where.startswith("button[send].go ")
                                              for where in reached[token]), (scheme, reached.get(token))
        assert {"text", "text-dim"} <= set(reached), (scheme, reached)
        # At 1280 a line is cut by the row's ellipsis; at 400 line two wraps, and nothing
        # may pass the edge.
        page.set_viewport_size({"width": 1280, "height": 800})
        assert page.evaluate(PAST_THE_EDGE) == {"past": [], "scroll": 0}, scheme
        ui_width_check(page, width=400)
    assert len(texts) == 2, f"the text colour is the same in both themes: {texts}"


# --- The small install -----------------------------------------------------------------------

def test_on_the_small_install_mail_reads_not_read_yet_with_no_session_cache_or_links(
        page_shell, rig, ui_contexts, monkeypatch):
    cache_absent(monkeypatch)
    rig.shim(**PLANNER)
    bound(rig, P)
    send(rig, "builder-2", "Pick up the parser.", "--posture", "none", who=PLANNER)
    send(rig, P, "The cursor commit is in.", "--posture", "none", who=REVIEWER)
    shell = page_shell(leave_out=SMALL)
    roster_kit.current(shell, holding([P]))
    _, root, _ = open_page(shell, ui_contexts)
    today = root.locator('[data-group="today"] moxi-ui-row')
    expect(today.first).to_have_attribute("where", re.compile(r"^to planner · not read yet$"))
    expect(today.nth(1)).to_have_attribute("where", "to builder-2 · not read yet, builder-2 isn't running")
    agent(root, P).locator('[data-action="message"]').click()
    opened = root.locator('[data-part="opened"][data-to="planner"]')
    expect(opened.locator('[data-part="box"]')).to_have_count(1)
    for line in ("session", "cache"):
        expect(opened.locator(f'[data-line="{line}"]')).to_have_count(0)
    expect(opened.locator('[data-action="sessions"], [data-action="cache"]')).to_have_count(0)
    opened.locator('[data-action="details"]').click()
    expect(opened.locator('[data-line="address"]').last).to_contain_text("planner")
    expect(opened.locator('[data-action="sessions"], [data-action="cache"]')).to_have_count(0)
