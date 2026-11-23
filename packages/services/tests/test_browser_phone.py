"""services' page on the phone, through a gateway-mode test shell reached by `PhoneBridge`,
its page server acting on a fake supervisor (`browser_kit`): with the phone's acts off, the
rows with no act, no `Open` and no command; with them on, `Open` on every row, each foot
act of his daemon and his timer through the shell's sheet to its kept confirmed route, a
Stop and a Run now followed to their ends, slots' server's Stop with the count it ends,
no `Turn on` or `Turn off` on a Moxi row, no Remove; and no route of services' reached but
the gateway's reads and the confirmed acts.

The gateway mounts the phone's acts only on its socket endpoint, which no system takes
yet, so for the acts-on tests the shell's document is served with its acts flag on, and
the gateway's own prepare and commit are stood in for: the prepare echoes what the page
sent, and the commit sends it to the act's confirmed route through the desk, as the
gateway does on the socket endpoint. What reaches services is then real.
"""

from __future__ import annotations

import http.client
import json
import re
import secrets
import sys

import pytest
from playwright.sync_api import expect

from browser_kit import fake_site, seed, slots_said, written

WAIT_MS = pytest.importorskip("moxi_ui.testing", reason="ui is not installed").WAIT_MS
_phone = pytest.importorskip("moxi_ui.testing.phone", reason="ui is not installed")
PhoneBridge, gateway_shell, pair_page, ORIGIN = (_phone.PhoneBridge, _phone.gateway_shell,
                                                 _phone.pair_page, _phone.ORIGIN)

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="services' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: His daemon `web` and timer `backup`, his `paused` daemon left stopped, and Moxi's rows:
#: a daemon of cache's, slots' server and ui's shell, each running.
UNITS = {"written": {"web": {"kind": "daemon", "enabled": True, "active": True},
                     "backup": {"kind": "timer", "enabled": True},
                     "paused": {"kind": "daemon", "enabled": True, "active": True}},
         "modules": {"cache/watch": {"kind": "daemon", "enabled": True, "active": True},
                     "slots/server": {"kind": "daemon", "enabled": True, "active": True},
                     "ui/shell": {"kind": "daemon", "enabled": True, "active": True}},
         "health": {"slots/server": ["ok", "tmux answers"], "ui/shell": ["ok", "the shell answers"]},
         "slots": True}
WEB, BACKUP, PAUSED = "services/web", "services/backup", "services/paused"
SLOTS, CACHE = "slots/server", "cache/watch"
COMMAND = "/usr/bin/python3"
#: The routes of services' the phone may reach: the gateway's reads and the confirmed acts.
REACHED = {"/api/rows", "/api/row", "/api/ends", "/api/status", "/api/events", "/api/start",
           "/api/stop", "/api/restart", "/api/run", "/api/enable", "/api/disable"}
THREE = tuple({"number": n, "state": "occupied", "idle": False, "label": label}
              for n, label in ((1, "kestrel-web"), (3, "planner"), (4, "builder-1")))


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    """A gateway-mode shell whose services page server acts on the fake supervisor, his
    rows seeded, the bridge to it, and where the fake slots API's answer is kept."""
    base = tmp_path_factory.mktemp("services-units")
    shell = gateway_shell(tmp_path_factory.mktemp("gw"), [fake_site(base, UNITS)])
    try:
        seed(shell, written("web"), written("paused"),
             written("backup", kind="timer", every="5m", argv=["/usr/bin/true"]))
        bridge = PhoneBridge(shell.home, shell.gateway_port).start()
    except BaseException:
        shell.stop()
        raise
    try:
        yield shell, bridge, base
    finally:
        bridge.stop()
        shell.stop()


@pytest.fixture
def phone(gw, ui_browser):
    """A phone paired with the gateway's shell; its browser closed at the test's end.
    Chromium is launched per bridge, since its SPKI flag is a launch argument. Its
    service worker is blocked, so the shell's document comes from the network, where
    `acts_on` serves it; `acts_on` fetches it with Playwright's own client, which takes
    no SPKI flag, so the context ignores certificate errors on every engine."""
    shell, bridge, _ = gw
    browser = bridge.launch(ui_browser.browser_type)
    try:
        context = browser.new_context(**PhoneBridge.context_args(ui_browser.browser_type.name,
                                                                 ignore_https_errors=True),
                                      service_workers="block")
        context.set_default_timeout(WAIT_MS)
        yield pair_page(context, shell)
    finally:
        browser.close()


class StandIn:
    """The gateway's prepare and commit, stood in for on `page`: each prepare is kept as
    `(path, body)` and echoed; a commit sends the last prepared act to its confirmed route
    on the desk, with a fresh idempotency key, and answers as the gateway does."""

    def __init__(self, page, shell):
        self.shell = shell
        self.prepared: list[tuple[str, dict]] = []
        self.committed: list[tuple[str, dict]] = []
        page.route(f"{ORIGIN}/gw/act/prepare", self._prepare)
        page.route(f"{ORIGIN}/gw/act/commit", self._commit)

    def _prepare(self, route):
        sent = route.request.post_data_json
        assert sent["module"] == "services", sent
        self.prepared.append((sent["path"], sent["body"]))
        route.fulfill(json={"act": f"a{len(self.prepared)}", "module": "services", "path": sent["path"],
                            "body": sent["body"]})

    def _commit(self, route):
        path, body = self.prepared[-1]
        self.committed.append((path, body))
        session = self.shell.session()
        conn = http.client.HTTPConnection("127.0.0.1", session.port, timeout=30)
        try:
            conn.request("POST", f"/m/services{path}", body=json.dumps(body).encode(),
                         headers={**session.headers(act=True), "Idempotency-Key": secrets.token_urlsafe(32)})
            answer = conn.getresponse()
            data = json.loads(answer.read() or b"null")
        finally:
            conn.close()
        act = f"a{len(self.prepared)}"
        if answer.status == 202:
            route.fulfill(status=202, json={"act": act, "outcome": "accepted", "status": data["status"]})
        else:
            route.fulfill(status=202, json={"act": act, "outcome": "refused", "code": answer.status,
                                            "text": (data or {}).get("refused") or f"answered {answer.status}"})


def acts_on(page) -> None:
    """Serves the shell's document with the phone's acts on, as the gateway does on its
    socket endpoint. The fetch carries the mark the gateway's worker puts on a navigation
    it sends on, since Playwright's own client sends no `Sec-Fetch-*` headers."""
    def serve(route):
        response = route.fetch(headers={**route.request.headers, "x-moxi-navigation": "1"})
        text = re.sub(r' data-gateway-reason="[^"]*"', "", response.text())
        route.fulfill(response=response, body=text.replace('data-gateway-acts="off"', 'data-gateway-acts="on"'))

    page.route(re.compile(re.escape(ORIGIN) + r"/m/services/(\?.*)?$"), serve)


def acts_shown(page) -> None:
    expect(page.locator("html")).to_have_attribute("data-gateway-acts", "on")


def reached(page) -> list[str]:
    """Every route of services' the page requests, by its page path, as it is requested."""
    found: list[str] = []
    page.on("request", lambda request: found.append(request.url.split("/m/services", 1)[1].split("?", 1)[0])
            if request.url.startswith(f"{ORIGIN}/m/services/api/") else None)
    return found


def services_on(page):
    page.goto(f"{ORIGIN}/m/services/")
    panel = page.locator("#moxi-panel > moxi-services-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    return panel


def row(panel, ident: str):
    return panel.locator(f'.item[data-id="{ident}"]')


def opened(panel, ident: str, name: str):
    item = row(panel, ident)
    item.get_by_role("button", name=f"Open {name}", exact=True).click()
    part = item.locator('[data-part="opened"]')
    expect(part).to_be_visible()
    return part


def foot(part):
    return part.locator('[data-part="foot"] button')


def sheet(page):
    return page.locator("moxi-ui-confirm dialog")


def test_with_the_phones_acts_off_no_row_offers_an_act_open_or_command(gw, phone):
    seen = reached(phone)
    panel = services_on(phone)
    for ident in (WEB, BACKUP, SLOTS, CACHE):
        expect(row(panel, ident)).to_be_visible()
    expect(panel.locator("button")).to_have_count(0)
    expect(panel.locator('[data-part="panel"]')).to_have_count(0)
    expect(panel).not_to_contain_text(COMMAND)
    expect(panel.locator('[data-line="desk"]')).to_have_count(0)
    assert set(seen) <= REACHED, seen


def test_his_daemons_foot_acts_go_through_the_sheet_and_stop_is_followed_to_its_end(gw, phone):
    shell, _, _ = gw
    acts_on(phone)
    stand_in = StandIn(phone, shell)
    seen = reached(phone)
    panel = services_on(phone)
    acts_shown(phone)
    for ident in (WEB, BACKUP, SLOTS, CACHE):
        expect(row(panel, ident).get_by_role("button", name=re.compile(r"^Open "))).to_have_count(1)
    expect(panel.locator('[data-action="add"]')).to_have_count(0)
    part = opened(panel, WEB, "web")
    expect(foot(part)).to_have_text(["Restart", "Stop", "Turn off"])
    expect(part).not_to_contain_text(COMMAND)
    # Each act's sheet says what it does to which row; Cancel sends nothing.
    for label, path in (("Restart", "/api/restart"), ("Turn off", "/api/disable")):
        foot(part).filter(has_text=label).click()
        expect(sheet(phone).locator("p").first).to_have_text(f"{label} web.")
        assert stand_in.prepared[-1] == (path, {"id": WEB}), stand_in.prepared
        sheet(phone).get_by_role("button", name="Cancel").click()
        expect(sheet(phone)).to_have_count(0)
    foot(part).filter(has_text="Stop").click()
    expect(sheet(phone).locator("p").first).to_have_text("Stop web.")
    sheet(phone).get_by_role("button", name="Confirm").click()
    expect(row(panel, WEB)).to_have_attribute("data-cause", "stopped")
    assert stand_in.committed == [("/api/stop", {"id": WEB})], stand_in.committed
    assert set(seen) <= REACHED, seen


def test_his_timers_foot_acts_go_through_the_sheet_and_run_now_is_followed_to_its_end(gw, phone):
    shell, _, _ = gw
    acts_on(phone)
    stand_in = StandIn(phone, shell)
    panel = services_on(phone)
    part = opened(panel, BACKUP, "backup")
    expect(foot(part)).to_have_text(["Run now", "Turn off", "Run again", "Stop run"])
    for label, path, body in (("Run again", "/api/run", {"id": BACKUP, "restart": True}),
                              ("Stop run", "/api/stop", {"id": BACKUP}),
                              ("Turn off", "/api/disable", {"id": BACKUP})):
        foot(part).filter(has_text=label).click()
        expect(sheet(phone).locator("p").first).to_have_text(f"{label} backup.")
        assert stand_in.prepared[-1] == (path, body), stand_in.prepared
        sheet(phone).get_by_role("button", name="Cancel").click()
        expect(sheet(phone)).to_have_count(0)
    with phone.expect_response(lambda r: "/m/services/api/status?job=" in r.url
                               and r.json().get("state") != "running") as ended:
        foot(part).filter(has_text="Run now").click()
        sheet(phone).get_by_role("button", name="Confirm").click()
    assert ended.value.json()["state"] == "done", ended.value.json()
    assert stand_in.committed == [("/api/run", {"id": BACKUP})], stand_in.committed


def test_a_stopped_daemons_closed_row_offers_start_and_a_moxi_row_no_turn_on_or_remove(gw, phone):
    shell, _, _ = gw
    acts_on(phone)
    StandIn(phone, shell)
    panel = services_on(phone)
    # `paused`, stopped by hand: Start on its closed row.
    _phone_stop(phone, panel, PAUSED, "paused")
    expect(row(panel, PAUSED).locator('button[data-action="start"]')).to_have_text("Start")
    item = row(panel, CACHE)
    item.get_by_role("button", name=re.compile(r"^Open ")).click()
    part = item.locator('[data-part="opened"]')
    expect(foot(part)).to_have_text(["Restart", "Stop"])
    expect(part.locator('[data-action="enable"], [data-action="disable"], [data-action="remove"]')).to_have_count(0)
    expect(panel.locator('[data-action="remove"]')).to_have_count(0)


def _phone_stop(page, panel, ident: str, name: str) -> None:
    part = opened(panel, ident, name)
    foot(part).filter(has_text="Stop").click()
    sheet(page).get_by_role("button", name="Confirm").click()
    expect(row(panel, ident)).to_have_attribute("data-cause", "stopped")
    row(panel, ident).get_by_role("button", name=f"Close {name}", exact=True).click()


def test_the_slots_servers_stop_says_the_count_it_ends_and_sends_it(gw, phone):
    shell, _, base = gw
    slots_said(base, slots=THREE)
    acts_on(phone)
    stand_in = StandIn(phone, shell)
    seen = reached(phone)
    panel = services_on(phone)
    item = row(panel, SLOTS)
    item.get_by_role("button", name=re.compile(r"^Open ")).click()
    part = item.locator('[data-part="opened"]')
    stop = foot(part).filter(has_text=re.compile(r"^Stop$"))
    stop.click()
    lines = sheet(phone).locator("p")
    expect(lines.first).to_have_text("Stop the slots server and end 3 sessions?")
    expect(sheet(phone)).to_contain_text("Ends now: 3 sessions")
    expect(sheet(phone)).not_to_contain_text("kestrel-web")
    expect(sheet(phone)).to_contain_text("A working session is cut off mid-turn. This cannot be taken back.")
    assert stand_in.prepared[-1] == ("/api/stop", {"id": SLOTS, "ends": 3}), stand_in.prepared
    sheet(phone).get_by_role("button", name="Cancel").click()
    expect(sheet(phone)).to_have_count(0)
    # Read before the sheet, and drawn in the warning colour once read.
    expect(part.locator('[data-part="foot"] button[data-warn="true"]')).to_have_text(["Restart", "Stop"])
    assert "/api/ends" in seen and set(seen) <= REACHED, seen
    assert stand_in.committed == []
