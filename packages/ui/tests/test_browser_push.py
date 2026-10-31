"""The push channel and the page servers in a browser: a fixture act reaching its
components, one stream for seven tabs and its hand-over, the close after 10 s with no
visible tab, a crash, a start that fails, and a version change that drains."""

from __future__ import annotations

import re
import threading

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import fixture_kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(90)]

PUSH = "/m/ui/api/push"


def _streams(shell) -> int:
    """Browser push streams the shell holds open now."""
    status, data = shell.call("GET", "/m/ui/api/servers")
    assert status == 200
    return data["shell"]["streams"]


def _count(shell) -> int:
    status, data = shell.session().get("/m/fixture/api/summary")
    assert status == 200
    return data["count"]


def _panel(page):
    return page.locator("#moxi-panel > moxi-fixture-page")


def _servers_row(page, module="fixture"):
    return page.locator(f"#moxi-panel moxi-ui-servers li[data-module={module}]")


def _hide(page, hidden=True):
    """Make the tab report itself hidden (or shown again), as switching tabs would."""
    page.evaluate("""(hidden) => {
      Object.defineProperty(document, "visibilityState", {configurable: true,
                            get: () => hidden ? "hidden" : "visible"});
      Object.defineProperty(document, "hidden", {configurable: true, get: () => hidden});
      document.dispatchEvent(new Event("visibilitychange"));
    }""", hidden)


def _in_thread(fn):
    out: dict = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as err:  # handed back to the test
            out["error"] = err

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def result(timeout=20.0):
        thread.join(timeout)
        assert not thread.is_alive(), "the request did not finish"
        if "error" in out:
            raise out["error"]
        return out["value"]

    return result


def test_a_fixture_act_changes_its_topic_and_its_components_show_it(ui_shell, ui_page):
    n = _count(ui_shell)
    block = ui_page.locator("#home moxi-fixture-block").get_by_test_id("count")
    expect(block).to_have_text(f"Count: {n}")
    ui_page.goto("/m/fixture/")
    panel = _panel(ui_page)
    expect(panel.get_by_test_id("count")).to_have_text(f"Count: {n}")
    panel.get_by_role("button", name="Add one").click()
    expect(panel.get_by_test_id("status")).to_have_text("add: 200")
    expect(panel.get_by_test_id("count")).to_have_text(f"Count: {n + 1}")
    # An act from elsewhere reaches the panel through the same topic.
    status, _ = ui_shell.session().post("/m/fixture/api/add", {"by": 2, "note": "from afar"})
    assert status == 200
    expect(panel.get_by_test_id("count")).to_have_text(f"Count: {n + 3}")
    expect(panel.get_by_test_id("note")).to_have_text("Note: from afar")
    panel.get_by_role("button", name="Read").click()
    expect(panel.get_by_test_id("summary")).to_have_text(f"Summary: {n + 3} from afar")
    panel.get_by_role("button", name="Modules").click()
    expect(block).to_have_text(f"Count: {n + 3}")


def test_seven_tabs_share_one_stream_and_closing_the_leader_hands_it_on(ui_shell, ui_context):
    pushes: list = []
    ui_context.on("request", lambda r: pushes.append(r) if PUSH in r.url else None)
    pages = [ui_context.pages[0]] + [ui_context.new_page() for _ in range(6)]
    n = _count(ui_shell)
    for page in pages:
        page.goto("/m/fixture/")
        expect(_panel(page).get_by_test_id("count")).to_have_text(f"Count: {n}")
    wait_for(lambda: _streams(ui_shell) == 1, 5, what="one push stream at the shell")
    held = [lock for lock in pages[0].evaluate("navigator.locks.query()")["held"]
            if lock["name"] == "moxi-ui-push"]
    assert len(held) == 1, held
    leader = pushes[-1].frame.page
    assert leader in pages
    assert all(p.frame.page is leader for p in pushes[-3:])

    status, _ = ui_shell.session().post("/m/fixture/api/add", {"by": 1})
    assert status == 200
    for page in pages:
        expect(_panel(page).get_by_test_id("count")).to_have_text(f"Count: {n + 1}")
    assert _streams(ui_shell) == 1

    before = len(pushes)
    leader.close()
    rest = [page for page in pages if page is not leader]
    # The sync API hands over the context's request events only inside a call to it.
    wait_for(lambda: rest[0].evaluate("true") and len(pushes) > before
             and _streams(ui_shell) == 1, 5,
             what=lambda: f"another tab to open the one stream: {len(pushes) - before} new "
                          f"push requests, {_streams(ui_shell)} streams at the shell")
    successor = pushes[-1].frame.page
    assert successor in rest
    held = [lock for lock in rest[0].evaluate("navigator.locks.query()")["held"]
            if lock["name"] == "moxi-ui-push"]
    assert len(held) == 1, held
    # The value is kept across the hand-over, and the next act reaches every tab left.
    for page in rest:
        expect(_panel(page).get_by_test_id("count")).to_have_text(f"Count: {n + 1}")
    ui_shell.session().post("/m/fixture/api/add", {"by": 1})
    for page in rest:
        expect(_panel(page).get_by_test_id("count")).to_have_text(f"Count: {n + 2}")
    # A tab that joins now gets the current value at once, from the new leader.
    late = ui_context.new_page()
    late.goto("/m/fixture/")
    expect(_panel(late).get_by_test_id("count")).to_have_text(f"Count: {n + 2}")
    assert _streams(ui_shell) == 1


def test_the_stream_closes_after_10_s_with_no_visible_tab_and_reopens_when_one_is_shown(
        ui_shell, ui_contexts):
    context = ui_contexts(sign_in=False)
    context.clock.install()
    first = ui_shell.sign_in(context.new_page())
    second = context.new_page()
    n = _count(ui_shell)
    for page in (first, second):
        page.goto("/m/fixture/")
        expect(_panel(page).get_by_test_id("count")).to_have_text(f"Count: {n}")
    wait_for(lambda: _streams(ui_shell) == 1, 5, what="one push stream at the shell")
    _hide(second)
    context.clock.fast_forward(11_000)
    assert _streams(ui_shell) == 1, "the stream closed while one tab was still shown"
    _hide(first)
    context.clock.fast_forward(9_000)
    assert _streams(ui_shell) == 1, "the stream closed before 10 s"

    def closed() -> bool:
        context.clock.fast_forward(500)
        return _streams(ui_shell) == 0

    wait_for(closed, 5, interval=0.1, what="the stream to close with no tab shown")
    # Acts while no stream is open; showing a tab reopens it with the current value.
    ui_shell.session().post("/m/fixture/api/add", {"by": 5})
    _hide(second, False)
    wait_for(lambda: _streams(ui_shell) == 1, 5, what="the stream to reopen")
    for page in (first, second):
        expect(_panel(page).get_by_test_id("count")).to_have_text(f"Count: {n + 5}")


def test_a_crash_shows_down_in_the_status_line_and_the_panel_comes_back(ui_shell, ui_page):
    ui_page.goto("/m/fixture/")
    panel = _panel(ui_page)
    n = _count(ui_shell)
    expect(panel.get_by_test_id("count")).to_have_text(f"Count: {n}")
    ui_page.evaluate("""() => {
      const status = document.getElementById("moxi-status");
      window.__statusSeen = [];
      new MutationObserver(() => {
        if (!status.hidden) window.__statusSeen.push(status.textContent);
      }).observe(status, {attributes: true, childList: true, characterData: true, subtree: true});
    }""")
    crashes = ui_shell.server("fixture")["crashes"]
    ui_shell.kill("fixture")
    ui_page.wait_for_function("window.__statusSeen.some((t) => t.startsWith(\"fixture's page server is down\"))")
    expect(ui_page.locator("#moxi-status")).to_be_hidden()
    row = ui_shell.wait_server("fixture", lambda r: r["state"] == "running" and r["crashes"] > crashes)
    assert row["crashes"] == crashes + 1
    panel.get_by_role("button", name="Add one").click()
    expect(panel.get_by_test_id("count")).to_have_text(f"Count: {n + 1}")


def test_the_servers_block_follows_a_running_server_a_crash_and_a_failed_start(
        ui_shells, fixture_site, ui_contexts):
    shell = ui_shells(site=[fixture_site])
    page = ui_contexts(shell).pages[0]
    row = _servers_row(page)
    # The home's fixture block subscribes to a fixture topic, which starts its server.
    expect(row.locator(".state")).to_have_text("running")
    expect(row).to_have_class("ok")
    expect(row).to_contain_text(re.compile(r"pid \d+, version 0\.1\.0, over (a private socket|TCP)"))
    shell.kill("fixture")
    expect(row).to_contain_text("crashed just now")
    expect(row).to_contain_text("1 crash")
    expect(row).to_have_class("warn")
    expect(row.locator(".state")).to_have_text("running")
    # The next start fails: the push upstream's reconnect meets a page server that exits 3.
    fixture_kit.fail_start(shell)
    shell.kill("fixture")
    expect(row.locator(".state")).to_have_text("failed to start", timeout=10_000)
    expect(row).to_have_class("fail")
    expect(row).to_contain_text("The next request after 10 s tries again.")
    expect(row).to_contain_text("last exited with code 3")
    expect(row).to_contain_text("log: ")
    fixture_kit.fail_start(shell, False)


def test_the_servers_block_says_when_no_page_server_has_run(ui_shells, fixture_site, ui_contexts):
    shell = ui_shells(site=[fixture_site], leave_out=["fixture"])
    page = ui_contexts(shell).pages[0]
    block = page.locator("#moxi-panel moxi-ui-servers")
    expect(block).to_contain_text("No page server has run since the shell started.")


def test_a_start_that_fails_shows_the_error_panel_and_a_second_look_starts_nothing(
        ui_shells, fixture_site, ui_contexts):
    shell = ui_shells(site=[fixture_site])
    fixture_kit.fail_start(shell)
    page = ui_contexts(shell).pages[0]
    page.goto("/m/fixture/")
    box = page.locator("#moxi-panel .moxi-message")
    expect(box.locator("h1")).to_have_text("fixture's page server did not start")
    expect(box).to_contain_text("It exited with code 3.")
    expect(box).to_contain_text("The next try is at the first request after 10 s.")
    expect(box).to_contain_text("Its log: ")
    row = shell.wait_server("fixture", "failed")
    page.goto("/m/fixture/")
    expect(box.locator("h1")).to_have_text("fixture's page server did not start")
    again = shell.server("fixture")
    assert again["failed_starts"] == row["failed_starts"] == 1, again
    assert again["starts"] == row["starts"]


#: The failed window the three-failed-starts test gives its shell, so it waits out two
#: windows in about a second rather than 20 s.
SHORT_WINDOW = 0.5


def test_three_failed_starts_in_a_row_turn_the_servers_row_to_fail(ui_shells, fixture_site,
                                                                  ui_contexts):
    shell = ui_shells(site=[fixture_site], failed_for=SHORT_WINDOW)
    fixture_kit.fail_start(shell)
    page = ui_contexts(shell).pages[0]
    row = _servers_row(page)
    session = shell.session()
    # The row shows each failed start only for the short window, between samples of the
    # servers topic, so the starts are counted at the shell; the block's "failed to start"
    # is the servers block test's.
    for n in (1, 2, 3):
        # One start each window at most: a request inside the window gets the same 502 at
        # once, and the first one after it starts the server again.
        def failed_again(n=n) -> bool:
            session.get("/m/fixture/api/summary")
            return shell.server("fixture")["failed_in_row"] >= n

        wait_for(failed_again, 5, interval=0.1, what=f"failed start {n}")
    expect(row).to_contain_text("the last 3 starts failed", timeout=5_000)
    expect(row).to_have_class("fail")
    expect(row).to_contain_text("log: ")


def test_a_version_change_drains_the_server_and_the_next_request_starts_the_new_one(
        ui_shells, ui_contexts, tmp_path_factory):
    site = fixture_kit.install(tmp_path_factory.mktemp("drain-site"))
    shell = ui_shells(site=[site])
    page = ui_contexts(shell).pages[0]
    row = _servers_row(page)
    expect(row.locator(".state")).to_have_text("running")
    old = shell.wait_server("fixture", "running")
    assert old["version"] == "0.1.0"
    fixture_kit.hold(shell)
    held = _in_thread(lambda: shell.session(timeout=30).get("/m/fixture/api/wait"))
    wait_for(lambda: fixture_kit.waiting(shell) == 1, 5, what="the held read to reach the server")

    fixture_kit.set_version(site, "0.2.0")

    def seen_new() -> bool:
        status, view = shell.session().get("/m/ui/api/modules")
        return any(m["name"] == "fixture" and m.get("version") == "0.2.0" for m in view["modules"])

    wait_for(seen_new, 10, interval=0.2, what="the snapshot to see version 0.2.0")
    expect(row.locator(".state")).to_have_text("restarting")
    expect(row).to_have_class("warn")
    assert shell.server("fixture")["state"] == "draining"
    # A new request waits for the drain; the open one finishes on the old server.
    waiting = _in_thread(lambda: shell.session(timeout=30).get("/m/fixture/api/summary"))
    fixture_kit.hold(shell, False)
    status, data = held()
    assert status == 200 and data["pid"] == old["pid"]
    status, data = waiting()
    assert status == 200 and data["pid"] != old["pid"]
    new = shell.wait_server("fixture", lambda r: r["state"] == "running" and r["version"] == "0.2.0")
    assert new["pid"] == data["pid"]
    expect(row.locator(".state")).to_have_text("running")
    expect(row).to_contain_text("version 0.2.0")
    card = page.locator("#moxi-panel > moxi-ui-page .card[data-module=fixture]")
    expect(card).to_contain_text("0.2.0")
    # The page's stream came back on the new server.
    n = _count(shell)
    shell.session().post("/m/fixture/api/add", {"by": 1})
    expect(page.locator("#home moxi-fixture-block").get_by_test_id("count")).to_have_text(
        f"Count: {n + 1}")
