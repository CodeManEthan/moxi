"""Home's Sessions row on the phone, through a gateway-mode test shell reached by
`PhoneBridge`: the row the phone sees, opened with no click, with the count beside its
label, each session's where on its second line and no act, time or message; the one
launcher route the phone reaches, which carries no message text; the row reading that
route again every 30 s while the page is shown, and when it is shown again; and a session
a press would keep warm, whose `coverable` the phone reads and draws no act for, the press
itself refused by the gateway.

The shell runs the fake turns in turns' place and reads the real cache and sessions over
transcripts the tests write; its launches are slot launches whose process is this test's
own, as in `test_browser_home_running.py`. The first test opens no desk page and no stream,
so the phone's own reads start the page server's feed and its joiner.
"""

from __future__ import annotations

import re
import sys
import uuid

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit

from moxi_launcher import cachetimers, keepwarm, store

pytest.importorskip("moxi_cache.api", reason="cache is not installed")
pytest.importorskip("moxi_sessions.api", reason="sessions is not installed")
# ui is the launcher's optional module: its test kit is taken only where it is installed.
WAIT_MS = pytest.importorskip("moxi_ui.testing", reason="ui is not installed").WAIT_MS
_phone = pytest.importorskip("moxi_ui.testing.phone", reason="ui is not installed")
PhoneBridge, gateway_shell, pair_page = _phone.PhoneBridge, _phone.gateway_shell, _phone.pair_page

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(sys.platform == "win32",
                                 reason="a launch's process is read through the process table")]

ROUTE = "/api/running"
RUNNING = "/m/launcher" + ROUTE
ANSWER = "The login form is fixed."
HOUR = r"\d{1,2}[:.]\d{2}(\s?[AaPp][Mm])?"
#: Seconds within which the phone's first reads carry what a joiner pass read.
PASS_MS = 30_000
#: The press of `Keep warm`, as the desk's component posts it through the shell.
PRESS = "/m/launcher/api/keep-warm"
KEEP_WARM = cachetimers.KEEP_WARM


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    """A gateway-mode shell with the fake turns, the bridge to it, and the fake."""
    turns = kit.FakeTurns(tmp_path_factory.mktemp("fake-turns"))
    options = turns.options()
    shell = gateway_shell(tmp_path_factory.mktemp("gw"), options["site"], env=options["env"])
    bridge = PhoneBridge(shell.home, shell.gateway_port).start()
    try:
        yield shell, bridge, turns
    finally:
        bridge.stop()
        shell.stop()


@pytest.fixture
def phones(ui_browser):
    """`phones(bridge)`: a fresh phone context through `bridge`; closed at the test's end.
    Chromium is launched per bridge, since its SPKI flag is a launch argument."""
    opened: list = []

    def open_phone(bridge: PhoneBridge):
        browser = bridge.launch(ui_browser.browser_type)
        opened.append(browser)
        context = browser.new_context(**PhoneBridge.context_args(ui_browser.browser_type.name))
        context.set_default_timeout(WAIT_MS)
        return context

    try:
        yield open_phone
    finally:
        for browser in opened:
            browser.close()


@pytest.fixture
def seeded(gw, tmp_path):
    """`seeded(name, waits=False, covered=False, coverable=False)`: a live launch with a
    transcript, as `test_browser_home_running.Launches.seed` makes one; every one is ended
    when the test ends, and the fake's script cleared. `coverable` gives it an hour
    lifetime and a clock record whose watcher asks keep-warm about it, so a press would
    cover it while it waits."""
    shell, _, turns = gw
    ids: list[str] = []
    sessions: dict[str, dict] = {}

    def seed(name: str, *, waits: bool = False, covered: bool = False,
             coverable: bool = False) -> str:
        request_id = f"{name}-{uuid.uuid4().hex[:8]}"
        config = tmp_path / request_id
        sid = kit.seed(shell, request_id, live=True, claimed=True, config_dir=config,
                       values={"name": name, **({"keep_warm": "on"} if covered else {})})
        kit.transcript(config, sid, answered=ANSWER, hour=coverable)
        if covered:
            kit.timed(shell, sid)
        if coverable:
            kit.timed(shell, sid, asks=[KEEP_WARM])
        if waits:
            sessions[sid] = kit.waiting()
            turns.script(sessions=sessions)
        ids.append(request_id)
        return request_id

    try:
        yield seed
    finally:
        turns.script()
        kit.end(shell, *ids)


def _home(page):
    return page.locator("#moxi-panel > moxi-ui-page moxi-ui-home")


def _entry(page):
    return _home(page).locator(".entry[data-row='launcher.running']")


def _session(page, request_id: str):
    return _entry(page).locator(f"moxi-launcher-running moxi-ui-row[data-request='{request_id}']")


def test_the_phone_alone_shows_the_row_open_with_the_count_by_its_label_and_where_on_line_two(
        gw, phones, seeded):
    shell, bridge, _ = gw
    loud = seeded("planner", waits=True)
    kept = seeded("reviewer", waits=True, covered=True)
    page = pair_page(phones(bridge), shell)

    line = _entry(page).locator(".row")
    expect(line.locator(".label")).to_have_text("Sessions")
    expect(line.locator(".time")).to_have_text("2 running")
    expect(line.locator(".fact")).to_have_text("")
    # Open with no click, and nothing to close it with.
    expect(line).to_have_count(1)
    expect(_entry(page).get_by_role("button")).to_have_count(0)

    # The phone's reads alone start the feed and its joiner: the loud session is the third
    # case, heavy, and the covered one says only that it is kept warm.
    row = _session(page, loud)
    expect(row.locator(".fact.strong")).to_have_text(
        re.compile(f"^goes cold at {HOUR}, est\\.$"), timeout=PASS_MS)
    expect(row.locator(".lead.strong")).to_have_text("waiting on 1 subagent")
    expect(row.locator(".name")).to_have_text("planner")
    expect(row.locator(".rest")).to_have_text("slot 3 · page")
    other = _session(page, kept)
    expect(other.locator(".fact")).to_have_text("kept warm", timeout=PASS_MS)
    expect(other.locator(".strong")).to_have_count(0)
    expect(other.locator(".rest")).to_have_text("slot 3 · page")
    # The loud one comes first.
    rows = _entry(page).locator("moxi-ui-row")
    assert rows.evaluate_all("rows => rows.map((row) => row.dataset.request)") == [loud, kept]

    # No act, no time and no message.
    for found in (row, other):
        expect(found.locator("[slot='acts']")).to_have_count(0)
        expect(found.locator(".time")).to_have_count(0)
        expect(found.locator(".where")).to_have_count(0)
        expect(found.locator(".you")).to_have_count(0)
    expect(_entry(page).get_by_role("link", name="Open")).to_have_count(0)
    expect(page.get_by_text(ANSWER)).to_have_count(0)
    expect(_entry(page).get_by_role("link", name="All sessions (2)")).to_be_visible()


def test_the_running_route_answers_the_phone_without_the_message_and_the_stream_does_not(
        gw, seeded):
    shell, bridge, _ = gw
    request_id = seeded("planner")
    desk = kit.session(shell)
    found: list = []

    def said() -> bool:
        found[:] = [desk.get(RUNNING)]
        return any(entry["request_id"] == request_id and entry["activity"]
                   for entry in found[0][1]["sessions"])

    wait_for(said, PASS_MS / 1000, interval=0.2, what=lambda: f"the desk's answer; last {found}")
    phone = bridge.pair(shell)
    status, data = phone.get(RUNNING)
    assert status == 200, (status, data)
    (entry,) = [entry for entry in data["sessions"] if entry["request_id"] == request_id]
    assert entry["activity"]["text"] is None and entry["activity"]["by"] == "agent"
    assert entry["activity"]["at"] and entry["name"] == "planner"
    assert entry["where"] == {"target": "slot", "slot": "3", "project_key": None,
                              "folder": "page"}
    assert entry["cache"]["state"] == "warm" and entry["started_at"]
    (on_desk,) = [e for e in found[0][1]["sessions"] if e["request_id"] == request_id]
    assert on_desk["activity"]["text"] == ANSWER

    status, _ = phone.get("/m/launcher/api/events?topics=running")
    assert status in (403, 404), status
    # The shell holds the topic's value for the desk; the phone's push stream opens, and
    # what it gets for the launcher is that the topic is not the phone's, never a value.
    kit.published(shell, "running", lambda data: data["count"] >= 1)
    with phone.stream("/m/ui/api/push?topics=launcher.running") as events:
        assert events.status == 200, events.status
        assert events.until(lambda name, data: True) == (
            "moxi.state", {"module": "launcher", "state": "down",
                           "detail": "not exported: running"})


#: Counts in the page each fetch of the launcher's running route, as the page makes it.
_COUNT_READS = """(() => {
  const real = window.fetch;
  window.__runningReads = 0;
  window.fetch = function (target, init) {
    if (String(target).endsWith("/m/launcher/api/running")) window.__runningReads += 1;
    return real.call(this, target, init);
  };
})()"""


def _reads(page) -> int:
    return page.evaluate("() => window.__runningReads")


def _seen(page, *, visible: bool) -> None:
    """The document reads as shown or hidden, and says so as a browser does."""
    page.evaluate("""(visible) => {
      Object.defineProperty(document, "visibilityState",
                            {configurable: true, get: () => (visible ? "visible" : "hidden")});
      document.dispatchEvent(new Event("visibilitychange"));
    }""", visible)


def test_on_the_phone_the_row_reads_again_every_30_s_while_shown_and_when_shown_again(
        gw, phones, seeded):
    shell, bridge, _ = gw
    seeded("planner")
    context = phones(bridge)
    context.clock.install()
    context.add_init_script(script=_COUNT_READS)
    page = pair_page(context, shell)
    expect(_entry(page).locator(".row .time")).to_have_text("1 running")
    first = _reads(page)
    assert first >= 1

    # The timers run as the clock moves on: one read 30 s after the row was placed, none
    # before. The clock also runs on its own, a little since the row was placed.
    page.clock.fast_forward("00:25")
    assert _reads(page) == first
    page.clock.fast_forward("00:06")
    assert _reads(page) == first + 1

    # Hidden, the page reads nothing however long it waits.
    _seen(page, visible=False)
    page.clock.fast_forward("02:00")
    assert _reads(page) == first + 1

    # Shown again, it reads at once, and every 30 s from then on.
    _seen(page, visible=True)
    assert _reads(page) == first + 2
    page.clock.fast_forward("00:30")
    assert _reads(page) == first + 3


def test_on_the_phone_a_session_a_press_would_cover_has_no_act_and_the_press_is_refused(
        gw, phones, seeded):
    shell, bridge, _ = gw
    request_id = seeded("planner", waits=True, coverable=True)
    phone = bridge.pair(shell)
    found: list = []

    def arrived() -> bool:
        found[:] = [phone.get(RUNNING)]
        return any(entry["request_id"] == request_id and entry["coverable"]
                   for entry in found[0][1]["sessions"])

    # The phone's read carries `coverable`, as the desk's does.
    wait_for(arrived, PASS_MS / 1000, interval=0.2, what=lambda: f"coverable; last {found}")
    (entry,) = [e for e in found[0][1]["sessions"] if e["request_id"] == request_id]
    assert entry["coverable"] == {"cap": 3} and entry["covered"] is False
    assert entry["waits"] == {"kind": "subagents", "count": 1}

    # The phone draws the uncovered row, loud, with no act.
    page = pair_page(phones(bridge), shell)
    row = _session(page, request_id)
    expect(row.locator(".lead.strong")).to_have_text("waiting on 1 subagent", timeout=PASS_MS)
    expect(row.locator(".fact.strong")).to_have_text(re.compile(f"^goes cold at {HOUR}"))
    expect(row.locator("[slot='acts']")).to_have_count(0)
    expect(_entry(page).locator("button[data-act='keep-warm']")).to_have_count(0)
    expect(_entry(page).get_by_role("button", name="Keep warm")).to_have_count(0)

    # The press is not the gateway's: the phone's post is refused and covers nothing.
    status, _ = phone.post(PRESS, {"request_id": request_id})
    assert status == 404, status
    with kit.launch_log(shell) as conn:
        assert [e for e in store.events(conn, request_id) if e.kind == keepwarm.COVER] == []
    # The same post from the desk is the route's, and covers the session.
    status, answer = kit.session(shell).post(PRESS, {"request_id": request_id})
    assert (status, answer) == (200, {"refused": None})
    with kit.launch_log(shell) as conn:
        assert [e.detail["state"] for e in store.events(conn, request_id)
                if e.kind == keepwarm.COVER] == ["on"]
