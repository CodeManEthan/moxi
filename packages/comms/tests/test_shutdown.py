"""A systemd stop is a clean stop, and a restart's replay is not shown twice.

Two failures this pins: the daemon dying on SIGTERM before `Daemon.stop()` ran, and the
shim queueing a replayed message it already held.
"""

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time

import psutil
import pytest
from conftest import wait_for_queue

from moxi_comms.paths import daemon_socket


#: Windows has no SIGHUP.
SIGHUP = getattr(signal, "SIGHUP", None)


@pytest.mark.skipif(sys.platform == "win32", reason="on Windows send_signal(SIGTERM) is "
                    "TerminateProcess, so no signal reaches the daemon to stop it")
@pytest.mark.parametrize("sig", [signal.SIGTERM, pytest.param(SIGHUP, marks=pytest.mark.skipif(
    SIGHUP is None, reason="this system has no SIGHUP"))])
def test_a_signal_runs_the_clean_shutdown(tmp_path, sig):
    proc = subprocess.Popen(
        [sys.executable, "-c", "from moxi_comms.daemon import main; raise SystemExit(main())",
         "--backend", "mock", "--backend-store", str(tmp_path / "store.json")],
        env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sock = daemon_socket()
    deadline = time.time() + 20
    while not sock.exists():
        assert proc.poll() is None, proc.stdout.read()
        assert time.time() < deadline, "the daemon never bound its socket"
        time.sleep(0.05)
    proc.send_signal(sig)
    out, _ = proc.communicate(timeout=20)
    assert proc.returncode == 0, out
    assert not sock.exists(), "stop() removes the socket it bound, so it ran"


@pytest.mark.skipif(sys.platform != "win32", reason="Ctrl+Break is Windows' console stop")
def test_ctrl_break_runs_the_clean_shutdown_on_windows(tmp_path):
    """Windows' stop that a handler sees: Ctrl+Break, sent to the daemon's own process
    group. Its handler stops the daemon as SIGTERM's does on POSIX, so it exits 0 and no
    longer answers at its pipe. The break is sent once the daemon has started and waits
    to be stopped: Windows runs a handler only while the main thread runs Python code, so
    a break that lands during the start is handled whatever the wait does."""
    from moxi_comms.client import DaemonClient

    proc = subprocess.Popen(
        [sys.executable, "-c", "from moxi_comms.daemon import main; raise SystemExit(main())",
         "--backend", "mock", "--backend-store", str(tmp_path / "store.json")],
        env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    address = daemon_socket()
    lines: list[str] = []
    started = threading.Event()

    def read() -> None:
        for line in proc.stdout:
            lines.append(line)
            if "comms daemon listening on" in line:
                started.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    def answers() -> bool:
        try:
            client = DaemonClient(address, timeout=1).connect()
        except Exception:                                             # noqa: BLE001
            return False
        try:
            client.call("agents")
            return True
        except Exception:                                             # noqa: BLE001
            return False
        finally:
            client.close()

    try:
        deadline = time.time() + 20
        while not (started.is_set() and answers()):
            assert proc.poll() is None, "".join(lines)
            assert time.time() < deadline, "the daemon never started: " + "".join(lines)
            time.sleep(0.05)
        time.sleep(0.5)     # past the log line, into the wait main() stops in
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        proc.wait(timeout=20)
        reader.join(5)
    finally:
        if proc.poll() is None:
            for child in psutil.Process(proc.pid).children(recursive=True):
                with contextlib.suppress(psutil.NoSuchProcess):
                    child.kill()
            proc.kill()
            proc.wait()
    out = "".join(lines)
    assert proc.returncode == 0, out
    assert not answers(), "stop() closed the pipe it served"


def _deliver(shim, message_id):
    shim._on_push("deliver", {"payload": {"message_id": message_id, "text": "x"}})


def test_a_replayed_message_already_queued_is_dropped(rig):
    shim = rig.shim(name="planner", scope="proj-a")
    _deliver(shim, "329")
    _deliver(shim, "329")
    assert [p["message_id"] for p in shim.queue] == ["329"]


def _one_real_delivery(rig):
    planner = rig.shim(name="planner", scope="proj-a")
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    code, _ = rig.cli(["send", "--posture", "none", "reviewer@proj-a", "the parser diff"],
                      name="planner", scope="proj-a")
    assert code == 0
    wait_for_queue(reviewer, 1)
    return reviewer, dict(reviewer.queue[0])


def test_a_replayed_message_injected_but_not_completed_is_dropped(rig):
    reviewer, payload = _one_real_delivery(rig)
    assert reviewer.on_turn_boundary() is not None      # injected; the turn is running
    reviewer._on_push("deliver", {"payload": payload})   # the restart's replay
    assert reviewer.queue == []
    reviewer.on_turn_boundary()                         # completes; nothing to inject
    assert len(reviewer.digests) == 1


def test_after_completion_the_same_id_is_no_longer_held(rig):
    reviewer, payload = _one_real_delivery(rig)
    reviewer.on_turn_boundary()
    reviewer.on_turn_boundary()
    assert reviewer.held_ids == set()
