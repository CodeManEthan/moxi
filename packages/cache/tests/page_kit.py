"""What cache's tests through a test shell share: the clock records and the watcher's
reading the shell's page server reads, the chip placed on the shell's document, and a
short wait.

A test shell keeps its files under a home of its own, so its page server reads its records
from that home's runtime directory, never from the test's (`own_roots`) or the user's.
"""

from __future__ import annotations

import json
import os
import time
import weakref
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

CLOCKS = "/m/cache/api/clocks"
#: The shell's push channel carrying cache's topic.
PUSH = "/m/ui/api/push?topics=cache.clocks"


def record_dir(shell: Any) -> Path:
    """The directory the shell's cache page server lists the clock records from."""
    return shell.runtime_dir("cache") / "clock"


def put(shell: Any, record: dict, text: str | None = None) -> None:
    """Write `record` (or `text` in its place) as its session's record, as the watcher
    does: whole, by a rename."""
    directory = record_dir(shell)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record['session']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record) if text is None else text)
    os.replace(tmp, path)


def clear(shell: Any) -> None:
    """Remove every record, so a test starts from none."""
    directory = record_dir(shell)
    if directory.is_dir():
        for path in directory.iterdir():
            path.unlink()


def put_reading(shell: Any, *, now: float | None = None, tick: float = 1,
                listed: float | None = 1, stale_after: float = 30,
                text: str | None = None) -> dict:
    """Write the watcher's reading (or `text` in its place) as the watcher does: whole, by
    a rename. It ticked `tick` seconds before `now` and listed Claude's sessions `listed`
    seconds before it (never, with None). Returns the reading as the page server sends
    it."""
    now = time.time() if now is None else now
    sent = {"tick_at": now - tick, "listed_at": None if listed is None else now - listed,
            "stale_after": stale_after}
    path = shell.runtime_dir("cache") / "watch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".watch.json.tmp")
    tmp.write_text(json.dumps({"v": 1, "pid": 4242, **sent}) if text is None else text)
    os.replace(tmp, path)
    return sent


def clear_reading(shell: Any) -> None:
    """Remove the watcher's reading, as if no watcher of this build had run."""
    (shell.runtime_dir("cache") / "watch.json").unlink(missing_ok=True)


def record(session: str, *, now: float | None = None, age: float = 60, ttl: float = 3600,
           warn_before: float = 900, seen: float = 1, stale_after: float = 30,
           **extra: Any) -> dict:
    """A Claude record for `session`, its last request `age` seconds before `now` and seen
    `seen` seconds before it."""
    now = time.time() if now is None else now
    out = {"v": 1, "session": session, "harness": "claude", "pid": 4242, "kind": "warm",
           "last_request": now - age, "ttl": ttl, "read_at": now - seen, "seen_at": now - seen,
           "code": None, "warn_before": warn_before, "stale_after": stale_after}
    out.update(extra)
    return out


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


def published(shell: Any, check: Callable[[dict], bool]) -> dict:
    """Wait until cache's topic, as the shell's push channel hands it on, has a value that
    passes `check`, and return it. The shell keeps a topic's last value for the next
    subscriber, so after this a browser that subscribes gets no older value first."""
    with session(shell).stream(PUSH) as events:
        assert events.status == 200, f"the push channel answered {events.status}"
        return events.until(lambda name, data: name == "cache.clocks" and check(data))[1]


def holding(sessions: Iterable[str]) -> Callable[[dict], bool]:
    """A `published` check: the value is `up` and lists exactly `sessions`."""
    wanted = sorted(sessions)
    return lambda data: (data["state"] == "up"
                         and sorted(row["session"] for row in data["clocks"]) == wanted)


#: Places one chip per session, each in a cell `[data-cell=<session>]` of one host in the
#: shell's panel, and keeps each chip's every `data-state` in `window.chipStates`.
_PLACE = """async (sessions) => {
  const client = await import("/m/ui/client.js");
  let host = document.getElementById("chips");
  if (host === null) {
    host = document.createElement("div");
    host.id = "chips";
    document.getElementById("moxi-panel").append(host);
  }
  window.chipStates = window.chipStates ?? {};
  const placed = [];
  for (const session of sessions) {
    const cell = document.createElement("div");
    cell.dataset.cell = session;
    host.append(cell);
    const element = await client.place(cell, "cache", "chip", {session});
    placed.push(element !== null);
    if (element === null) continue;
    const states = window.chipStates[session] = [];
    const note = () => {
      const state = element.shadowRoot?.querySelector(".chip")?.dataset.state ?? "none";
      if (states[states.length - 1] !== state) states.push(state);
    };
    note();
    new MutationObserver(note).observe(element.shadowRoot, {subtree: true, childList: true,
                                                          attributes: true});
  }
  return placed;
}"""


def place_chips(page: Any, *sessions: str, placed: bool = True) -> list[bool]:
    """Place a chip for each session on the shell's document in `page`, and return whether
    each was placed. Unless `placed` is false, fail when one wasn't."""
    found = page.evaluate(_PLACE, list(sessions))
    if placed:
        assert all(found), f"the shell placed no chip for some of {sessions}: {found}"
    return found


def chip(page: Any, session: str) -> Any:
    """The span of the chip placed for `session`: it carries `data-state`, `data-shows`,
    `data-dim` and the title."""
    return page.locator(f'[data-cell="{session}"] moxi-cache-chip span.chip')


def chip_states(page: Any, session: str) -> list[str]:
    """Each `data-state` the chip for `session` has shown, in order."""
    return page.evaluate("(session) => window.chipStates[session]", session)


def pin_now(page: Any, now: float) -> list[dict]:
    """Answer each read of `/api/clocks` from `page` with the real page server's answer,
    its `now` set to `now`: the components count on the server's clock, so this sets that
    clock without touching the records. Returns the list each real answer is added to."""
    seen: list[dict] = []

    def handle(route: Any) -> None:
        response = route.fetch()
        body = response.json()
        seen.append(dict(body))
        body["now"] = now
        route.fulfill(response=response, json=body)

    page.route("**" + CLOCKS, handle)
    return seen


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
