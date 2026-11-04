"""services' page server stops when idle though its rows poller runs on, and a job it held
is gone with it. Through a real shell with a short idle time and services' page open in a
browser: the page's stream starts the poller, a stop goes through the confirm sheet to
done, the page closes, and the server exits within its idle time though the poller still
runs. The next server, started by the next request, holds no record of the stop's key,
so its status answers `unknown` and the row's state shows the outcome.
"""

from __future__ import annotations

import os
import sys
import time

import pytest
from playwright.sync_api import expect

from browser_kit import fake_site, get, seed, written

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="services' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: The idle time every page server of the test's shell gets, in seconds.
IDLE_S = 1.0
#: How long after the page closes the server may take to exit: the idle time, plus the
#: slack allowed for the server's idle look, its exit and the shell's own stream closing.
EXIT_WITHIN = IDLE_S + 4
UNKNOWN = "no record of this act; the row's state shows the outcome"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_for(check, seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.05)  # polling a process, not waiting in a page


def test_the_server_exits_when_idle_with_its_poller_running_and_forgets_its_jobs(
        ui_shells, ui_contexts, tmp_path):
    units = {"written": {"web": {"kind": "daemon", "enabled": True, "active": True}}}
    shell = ui_shells(site=[str(fake_site(tmp_path / "units", units))],
                      child_args=["--idle-s", str(IDLE_S)])
    seed(shell, written("web"))
    page = ui_contexts(shell).pages[0]
    page.goto("/m/services/")
    panel = page.locator("#moxi-panel > moxi-services-page")
    item = panel.locator('li.row[data-id="services/web"]')
    expect(item).to_have_attribute("data-verdict", "ok")
    # The page follows the topic: its stream started the poller.
    with page.expect_response(lambda r: "/m/services/api/status?job=" in r.url
                              and r.json().get("state") == "done"):
        with page.expect_request("**/m/services/api/stop") as sent:
            item.get_by_role("button", name="Stop web", exact=True).click()
            page.locator("moxi-ui-confirm").get_by_role("dialog").get_by_role(
                "button", name="Confirm", exact=True).click()
    key = sent.value.headers["idempotency-key"]
    expect(item.locator('[data-line="said"]')).to_contain_text("Stop web: Done.")
    row = shell.wait_server("services", "running")
    pid = row["pid"]
    # Open, the page's stream holds the server past its idle time.
    time.sleep(IDLE_S * 3)
    assert shell.server("services")["pid"] == pid and alive(pid)

    page.goto("about:blank")
    closed = time.monotonic()
    wait_for(lambda: not alive(pid), EXIT_WITHIN + 1,
             f"services' page server (pid {pid}) to exit once idle")
    assert time.monotonic() - closed <= EXIT_WITHIN
    after = shell.wait_server("services", lambda r: r["pid"] != pid)
    assert after["state"] == "idle" and after["crashes"] == 0, after

    # A new server holds no record of the stop, and the row's state shows what it did.
    status = get(shell, f"api/status?job={key}")
    assert status == {"job": key, "act": None, "id": None, "state": "unknown", "code": None,
                      "lines": [UNKNOWN], "fix": None}, status
    again = shell.wait_server("services", "running")
    assert again["pid"] != pid, again
    listed = {r["id"]: r for r in get(shell, "api/rows")["rows"]}
    assert listed["services/web"]["stopped"] is not None, listed["services/web"]
