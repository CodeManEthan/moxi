"""What comms' tests through a test shell share: a daemon the shell's comms page server
asks, the roster's value as the shell hands it on, cache's clock records for the chips the
roster places, and a short wait.

A test shell keeps its files under a home of its own, and its comms page server asks the
daemon at `moxi.ipc.address("comms", "daemon")` under that home. `point_at` moves this
process's comms runtime there, so the `rig` fixture's daemon, its shims and the CLI verbs
the rig runs all use that socket, and the page server reads their claim files.
"""

from __future__ import annotations

import json
import os
import time
import weakref
from collections.abc import Callable, Iterable
from typing import Any

from moxi import paths as moxi_paths

from moxi_comms.paths import daemon_socket

ROSTER = "/m/comms/api/roster"
EVENTS = "/m/comms/api/events?topics=roster"
#: The shell's push channel carrying comms' topic.
PUSH = "/m/ui/api/push?topics=comms.roster"
#: The standing names the rig provisions, in the roster's order.
STANDING = ["herald", "main@proj-a", "main@proj-b"]

#: The core's root for a socket path too long for its directory, as every process but
#: this one has it: read before the `env` fixture moves this process's under the test's
#: tmp.
_CORE_SHORT_ROOT = moxi_paths._SHORT_ROOT


def point_at(shell: Any, monkeypatch: Any) -> None:
    """Make this process's comms runtime the shell's, so the daemon the rig starts is the
    one the shell's comms page server asks."""
    runtime = shell.runtime_dir("comms")
    monkeypatch.setenv("MOXI_COMMS_RUNTIME_DIR", str(runtime.parent))
    if len(os.fsencode(runtime / "daemon.sock")) >= moxi_paths.SOCKET_MAX:
        # The page server then asks the core's short path; so must this process.
        monkeypatch.setattr(moxi_paths, "_SHORT_ROOT", _CORE_SHORT_ROOT)
    else:
        assert daemon_socket() == runtime / "daemon.sock", daemon_socket()


def server_gone(shell: Any, timeout: float = 10.0) -> None:
    """Wait until the shell runs no comms page server: the next request starts one, whose
    poller gathers at once. A test that sets its daemon up first and then waits here sees
    its own roster at once, not after the running server's next period."""
    wait_for(lambda: (row := shell.server("comms")) is None or row["pid"] is None, timeout,
             what="comms' page server to stop once idle")


#: One signed-in session per shell for the reads a test makes without a browser. Each
#: sign-in is a session, and the shell keeps at most 20, dropping the oldest first: a new
#: one per read would soon drop the browser's own.
_SESSIONS: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()


def session(shell: Any) -> Any:
    """The shell's one signed-in session for reads without a browser."""
    found = _SESSIONS.get(shell)
    if found is None or found.get("/m/ui/api/modules")[0] != 200:
        found = _SESSIONS[shell] = shell.session()
    return found


def published(shell: Any, check: Callable[[dict], bool], *, timeout: float = 15.0) -> dict:
    """Wait until comms' topic, as the shell's push channel hands it on, has a value that
    passes `check`, and return it. The shell keeps a topic's last value for the next
    subscriber, so after this a browser that subscribes gets no older value first."""
    with session(shell).stream(PUSH, timeout=timeout) as events:
        assert events.status == 200, f"the push channel answered {events.status}"
        return events.until(lambda name, data: name == "comms.roster" and check(data))[1]


def holding(live: Iterable[str], standing: Iterable[str] = STANDING) -> Callable[[dict], bool]:
    """A `published` check: the value is `up`, with exactly the `live` names, each with a
    session, and the `standing` ones."""
    wanted = (sorted(live), sorted(standing))

    def check(data: dict) -> bool:
        if data["state"] != "up":
            return False
        rows = data["rows"]
        if not all(row["session_id"] for row in rows if row["live"]):
            return False
        return (sorted(row["qualified"] for row in rows if row["live"]),
                sorted(row["qualified"] for row in rows if not row["live"])) == wanted

    return check


def current(shell: Any, check: Callable[[dict], bool]) -> dict:
    """Let the running comms page server stop, then wait for a new one's first gather to
    pass `check`."""
    server_gone(shell)
    return published(shell, check)


def clock_dir(shell: Any) -> Any:
    """The directory the shell's cache page server lists clock records from."""
    return shell.runtime_dir("cache") / "clock"


def put_clock(shell: Any, session_id: str, *, age: float, ttl: float = 3600,
              warn_before: float = 900) -> None:
    """Write a Claude clock record for `session_id` as cache's watcher does, its last
    request `age` seconds ago: whole, by a rename."""
    now = time.time()
    record = {"v": 1, "session": session_id, "harness": "claude", "pid": 4242,
              "kind": "warm", "last_request": now - age, "ttl": ttl, "read_at": now - 1,
              "seen_at": now - 1, "code": None, "warn_before": warn_before,
              "stale_after": 30}
    directory = clock_dir(shell)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record))
    os.replace(tmp, path)


def show_tab(page: Any) -> None:
    """Tell the document its tab was hidden and shown again, as switching tabs does."""
    page.evaluate("""() => {
      for (const hidden of [true, false]) {
        Object.defineProperty(document, "visibilityState", {configurable: true,
                              get: () => hidden ? "hidden" : "visible"});
        Object.defineProperty(document, "hidden", {configurable: true, get: () => hidden});
        document.dispatchEvent(new Event("visibilitychange"));
      }
    }""")


def wait_for(check: Callable[[], Any], timeout: float, *, what: str,
             interval: float = 0.05) -> Any:
    """Call `check` until it gives a true value, and return that; fail after `timeout`
    seconds saying `what` did not happen."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"{what} did not happen in {timeout:g}s")
        time.sleep(interval)
