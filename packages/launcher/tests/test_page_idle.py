"""The launcher's page server stops when idle, though its poller runs on, and the next
request starts it again. Through a real test shell with a short idle time, and no browser.

The shell keeps its files under a home of its own, so its page server reads the launch
log from that home's state directory: the test writes it there, through the shell's
environment."""

from __future__ import annotations

import os
import time
import uuid

from moxi import proc

from moxi_launcher import store

#: The idle time every page server of the test's shell gets, in seconds.
IDLE_S = 0.5
#: How long after its last request the server may take to exit: the idle time, plus the
#: slack allowed for the server's idle look and its exit.
EXIT_WITHIN = IDLE_S + 2
EVENTS = "/m/launcher/api/events?topics=launches"
LAUNCHES = "/m/launcher/api/launches"


def alive(pid: int) -> bool:
    return proc.identity(pid) is not None


def wait_for(check, seconds: float, *, what: str) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.05)


def launch(shell, request_id: str) -> None:
    """A launch's row in the shell's launch log, its process this test's own."""
    conn = store.connect(env=shell.env)
    try:
        ident = proc.identity(os.getpid())
        store.insert(conn, request_id=request_id, actor="ann", harness="claude",
                     target="headless", cwd="/work", values={}, argv=["claude"], env={},
                     posture="operator", session_id=str(uuid.uuid4()))
        store.set_state(conn, request_id, "started", pid=ident.pid,
                        pid_started=ident.started)
    finally:
        conn.close()


def requests(data: dict) -> list[str]:
    return [row["request_id"] for row in data["launches"]]


def test_the_page_server_stops_when_idle_with_its_poller_running_and_starts_again(ui_shells):
    shell = ui_shells(child_args=["--idle-s", str(IDLE_S)])
    launch(shell, "a1")
    session = shell.session()
    with session.stream(EVENTS) as events:
        assert events.status == 200
        # The stream started the poller, whose first gather is the first event.
        assert requests(events.event()[1]) == ["a1"]
        row = shell.wait_server("launcher", "running")
        pid = row["pid"]
        # An open stream holds the server past its idle time, and the poller keeps
        # reading: a new launch comes as the next event.
        launch(shell, "a2")
        assert requests(events.until(lambda name, data: len(data["launches"]) == 2)[1]) \
            == ["a2", "a1"]
        time.sleep(IDLE_S * 3)
        assert shell.server("launcher")["pid"] == pid and alive(pid)
    closed = time.monotonic()
    # Closed, the stream no longer holds it; the poller, which still runs, doesn't either.
    wait_for(lambda: not alive(pid), EXIT_WITHIN + 1,
             what=f"the launcher's page server (pid {pid}) to exit once idle")
    assert time.monotonic() - closed <= EXIT_WITHIN
    after = shell.wait_server("launcher", lambda r: r["pid"] != pid)
    assert after["state"] == "idle" and after["crashes"] == 0, after

    # The next request starts a new server, which reads what changed while none ran.
    launch(shell, "a3")
    status, data = session.get(LAUNCHES)
    assert status == 200 and requests(data) == ["a3", "a2", "a1"]
    again = shell.wait_server("launcher", "running")
    assert again["pid"] != pid and again["starts"] == row["starts"] + 1, again
    with session.stream(EVENTS) as events:
        assert requests(events.event()[1]) == ["a3", "a2", "a1"]
