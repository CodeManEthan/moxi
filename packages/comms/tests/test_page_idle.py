"""comms' page server stops when idle, though its poller runs on, and the next request
starts it again. Through a real test shell with a short idle time, its comms page server
asking the rig's daemon, and no browser."""

from __future__ import annotations

import os
import sys
import time

import pytest

import roster_kit
from roster_kit import wait_for
from test_reply_enforcement import PLANNER, REVIEWER, P, R

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="comms runs on Linux only")

#: The idle time every page server of the test's shell gets, in seconds.
IDLE_S = 0.5
#: How long after its last request the server may take to exit: the idle time, plus the
#: slack the design allows for its look and its exit.
EXIT_WITHIN = IDLE_S + 2


@pytest.fixture(scope="module")
def ui_shell_options():
    return {"child_args": ["--idle-s", str(IDLE_S)]}


@pytest.fixture(autouse=True)
def at_shell(ui_shell, monkeypatch):
    """The rig's daemon is the one the shell's comms page server asks."""
    roster_kit.point_at(ui_shell, monkeypatch)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def live(data: dict) -> list[str]:
    return [row["qualified"] for row in data["rows"] if row["live"]]


def bound(rig, *names):
    wait_for(lambda: all(n in rig.daemon.bindings and rig.daemon.bindings[n].session_id
                         for n in names), 5, what=f"{names} to bind")


def test_the_page_server_stops_when_idle_with_its_poller_running_and_starts_again(ui_shell,
                                                                                   rig):
    shell = ui_shell
    rig.shim(**REVIEWER)
    bound(rig, R)
    session = shell.session()
    with session.stream(roster_kit.EVENTS) as events:
        assert events.status == 200
        # The stream started the poller, whose first gather is the first event.
        name, data = events.event()
        assert (name, data["state"], live(data)) == ("roster", "up", [R])
        row = shell.wait_server("comms", "running")
        pid = row["pid"]
        # An open stream holds the server well past its idle time.
        time.sleep(IDLE_S * 3)
        assert shell.server("comms")["pid"] == pid and alive(pid)
    closed = time.monotonic()
    # Closed, the stream no longer holds it; the poller, which still runs, doesn't either.
    wait_for(lambda: not alive(pid), EXIT_WITHIN + 1,
             what=f"comms' page server (pid {pid}) to exit once idle")
    assert time.monotonic() - closed <= EXIT_WITHIN
    after = shell.wait_server("comms", lambda r: r["pid"] != pid)
    assert after["state"] == "idle" and after["crashes"] == 0, after

    # The next request starts a new server, whose first gather sees the name bound while
    # none ran.
    rig.shim(**PLANNER)
    bound(rig, P)
    status, data = session.get(roster_kit.ROSTER)
    assert status == 200 and (data["state"], live(data)) == ("up", [P, R]), data
    again = shell.wait_server("comms", "running")
    assert again["pid"] != pid and again["starts"] == row["starts"] + 1, again
    with session.stream(roster_kit.EVENTS) as events:
        assert live(events.event()[1]) == [P, R]
