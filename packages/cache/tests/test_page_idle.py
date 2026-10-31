"""cache's page server stops when idle, though its poller runs on, and the next request
starts it again. Through a real test shell with a short idle time, and no browser."""

from __future__ import annotations

import os
import sys
import time

import pytest

import page_kit
from page_kit import put, record, wait_for

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="cache runs on Linux only")

#: The idle time every page server of the test's shell gets, in seconds.
IDLE_S = 0.5
#: How long after its last request the server may take to exit: the idle time, plus the
#: slack the design allows for its look and its exit.
EXIT_WITHIN = IDLE_S + 2
EVENTS = "/m/cache/api/events?topics=clocks"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def sessions(data: dict) -> list[str]:
    return [row["session"] for row in data["clocks"]]


def test_the_page_server_stops_when_idle_with_its_poller_running_and_starts_again(ui_shells):
    shell = ui_shells(child_args=["--idle-s", str(IDLE_S)])
    put(shell, record("a1"))
    session = shell.session()
    with session.stream(EVENTS) as events:
        assert events.status == 200
        # The stream started the poller, whose first gather is the first event.
        assert sessions(events.event()[1]) == ["a1"]
        row = shell.wait_server("cache", "running")
        pid = row["pid"]
        # An open stream holds the server past its idle time, and the poller keeps
        # gathering: a new record comes as the next event.
        put(shell, record("a2"))
        assert sessions(events.until(lambda name, data: len(data["clocks"]) == 2)[1]) \
            == ["a1", "a2"]
        time.sleep(IDLE_S * 3)
        assert shell.server("cache")["pid"] == pid and alive(pid)
    closed = time.monotonic()
    # Closed, the stream no longer holds it; the poller, which still runs, doesn't either.
    wait_for(lambda: not alive(pid), EXIT_WITHIN + 1,
             what=f"cache's page server (pid {pid}) to exit once idle")
    assert time.monotonic() - closed <= EXIT_WITHIN
    after = shell.wait_server("cache", lambda r: r["pid"] != pid)
    assert after["state"] == "idle" and after["crashes"] == 0, after

    # The next request starts a new server, whose first gather sees what changed while
    # none ran.
    put(shell, record("a3"))
    status, data = session.get(page_kit.CLOCKS)
    assert status == 200 and sessions(data) == ["a1", "a2", "a3"]
    again = shell.wait_server("cache", "running")
    assert again["pid"] != pid and again["starts"] == row["starts"] + 1, again
    with session.stream(EVENTS) as events:
        assert sessions(events.event()[1]) == ["a1", "a2", "a3"]
