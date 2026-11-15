"""comms' page in a browser, through a test shell whose comms page server reads the rig's
record and asks the rig's daemon: who can be messaged and in which session, when a
message to each would be read, and which names need launching, on the full install and
on the small one.

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
import uuid
from typing import Any

import pytest
from playwright.sync_api import expect

import roster_kit
from conftest import cache_absent, write_transcript
from moxi import paths as moxi_paths
from moxi_comms.paths import daemon_socket
from roster_kit import holding, wait_for
from test_reply_enforcement import PLANNER, REVIEWER, P, R, send

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


def open_page(shell, ui_contexts, launcher: list[dict] | None = None) -> tuple[Any, Any, Any]:
    """A signed-in page on the shell; with `launcher`, the stub serves those launches. The
    page, its root and the stub."""
    page = ui_contexts(shell).pages[0]
    stub = LauncherStub(page, launcher) if launcher is not None else None
    page.goto(PAGE)
    return page, page.locator("#moxi-panel > moxi-comms-page"), stub


def agent(root, name: str) -> Any:
    return root.locator(f'moxi-ui-row[data-name="{name}"]')


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
