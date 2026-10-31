"""What the launcher's browser tests share: a fake harness on the test shell's `PATH`, the
launch log the shell's page server reads, seeded as a launch would leave it, the shell's
push channel, and the panel's parts.

A test shell keeps its files under a home of its own and hands its page servers only
`PATH` and the locale, so its launcher page server reads the launch log under that home
(`store.connect(env=shell.env)`), never the test's or the user's, and a launch it makes
finds its harness only on the `PATH` the shell was started with.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from moxi import proc

from moxi_launcher import store

#: The shell's push channel carrying both of the launcher's topics.
PUSH = "/m/ui/api/push?topics=launcher.launches,launcher.sessions"
#: The panel's launch log read: the route alone, not a status path's read of it.
LAUNCHES = "/m/launcher/api/launches"
#: The harnesses the fake stands in for.
HARNESSES = ("claude", "codex")

FAKE = """#!{python}
import json, os, sys
record = {{"argv": sys.argv, "cwd": os.getcwd()}}
tmp = os.path.join({seen!r}, ".%d.tmp" % os.getpid())
with open(tmp, "w") as f:
    json.dump(record, f)
os.replace(tmp, os.path.join({seen!r}, "%d.json" % os.getpid()))
print("the fake harness ran", flush=True)
sys.exit(0)
"""


class Harness:
    """A fake `claude` and `codex` under `root/bin`. Each run records its argv and
    directory as one JSON file under `root/seen` (a path written into the script, since a
    test shell hands its page servers no variable of the test's), prints one line and
    exits 0."""

    def __init__(self, root: Path):
        self.bin = root / "bin"
        self.seen = root / "seen"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.seen.mkdir(parents=True, exist_ok=True)
        for name in HARNESSES:
            path = self.bin / name
            path.write_text(FAKE.format(python=sys.executable, seen=str(self.seen)))
            path.chmod(0o755)

    def runs(self) -> list[dict]:
        """What each run recorded, in no set order."""
        return [json.loads(path.read_text()) for path in self.seen.glob("*.json")]


@contextmanager
def first_on_path(directory: Path) -> Iterator[None]:
    """`directory` first on this process's `PATH` for the block, undone after it. A test
    shell started in it copies that `PATH` and hands it to its page servers and their
    jobs. Used from a module-scoped fixture, so nothing of it outlives the module on its
    worker."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PATH", str(directory), prepend=os.pathsep)
        yield


# --- the launch log under the shell's home ------------------------------------------------


@contextmanager
def launch_log(shell: Any) -> Iterator[Any]:
    """The launch log the shell's launcher page server reads."""
    conn = store.connect(env=shell.env)
    try:
        yield conn
    finally:
        conn.close()


def seed(shell: Any, request_id: str, *, target: str = "slot", live: bool = False,
         session_id: str | None = None, cwd: str = "/work/page") -> str:
    """A Claude launch's row, started; its process this test's own when `live`, else one
    that has ended. Returns its session id."""
    sid = session_id or str(uuid.uuid4())
    me = proc.identity(os.getpid())
    slot = "3" if target == "slot" else None
    # The values a resume replays: where the session ran, and how.
    values = {"harness": "claude", "target": target, "cwd": cwd, "model": "opus",
              **({"slot": slot} if slot else {})}
    with launch_log(shell) as conn:
        store.insert(conn, request_id=request_id, actor="ann", harness="claude",
                     target=target, cwd=cwd, values=values, argv=["claude"], env={},
                     posture="operator", session_id=sid, slot=slot)
        # A start time no running process has, so an ended launch's process reads ended.
        store.set_state(conn, request_id, "started", pid=me.pid,
                        pid_started=me.started if live else 1.0)
    return sid


def learned(shell: Any, request_id: str, source: str = "clear") -> str:
    """A new session id `request_id`'s launch learns, as a clear or a compact gives one;
    returns it."""
    sid = str(uuid.uuid4())
    with launch_log(shell) as conn:
        assert store.learn_session(conn, request_id, sid, source)
    return sid


def failed_run(shell: Any, request_id: str, *, word: str = "relaunch-failed",
               detail: str = "the new session did not start") -> str:
    """A relaunch run on `request_id`'s launch that ended failed; returns its id."""
    me = proc.identity(os.getpid())
    with launch_log(shell) as conn:
        row = store.get(conn, request_id)
        run = store.insert_run(conn, kind="relaunch", mode="director", request_id=request_id,
                               session_id=row.session_id, actor="launcher", state="acting",
                               job=f"proc:{me.pid}@{me.started:.6f}")
        store.end_run(conn, run.run_id, "relaunch-failed", word=word, detail=detail)
    return run.run_id


# --- the shell's push channel -------------------------------------------------------------

#: One signed-in session per shell for the reads a test makes without a browser: the
#: shell keeps at most 20 sign-ins, dropping the oldest, so a new one per read would soon
#: drop the browser's own.
_SESSIONS: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()


def session(shell: Any) -> Any:
    """The shell's one signed-in session for reads without a browser."""
    found = _SESSIONS.get(shell)
    if found is None or found.get("/m/ui/api/modules")[0] != 200:
        found = _SESSIONS[shell] = shell.session()
    return found


def published(shell: Any, topic: str, check: Callable[[dict], bool]) -> dict:
    """Wait until the launcher's `topic`, as the shell's push channel hands it on, has a
    value that passes `check`, and return it. The shell keeps a topic's last value for the
    next subscriber, so after this a browser that subscribes gets no older value first."""
    with session(shell).stream(PUSH) as events:
        assert events.status == 200, f"the push channel answered {events.status}"
        name = f"launcher.{topic}"
        return events.until(lambda got, data: got == name and check(data))[1]


def holding_launch(request_id: str) -> Callable[[dict], bool]:
    """A `launches` check: the newest page holds `request_id`'s row."""
    return lambda data: any(row["request_id"] == request_id for row in data["launches"])


def holding_session(session_id: str, **want: Any) -> Callable[[dict], bool]:
    """A `sessions` check: `session_id` is live, with each of `want`'s keys as given."""
    return lambda data: any(
        row["session_id"] == session_id and all(row.get(k) == v for k, v in want.items())
        for row in data["sessions"])


# --- the panel ----------------------------------------------------------------------------


def open_panel(page: Any) -> Any:
    """The launcher's panel, opened on the shell's document in `page`."""
    page.goto("/m/launcher/")
    return page.locator("#moxi-panel > moxi-launcher-page")


#: The colour a node shows, and the colour a theme token gives, read from a probe placed
#: beside the node so it inherits what the node does.
_COLOUR = """(node, token) => {
  const probe = document.createElement("span");
  probe.style.color = `var(--moxi-${token})`;
  node.after(probe);
  const want = getComputedStyle(probe).color;
  probe.remove();
  return {seen: getComputedStyle(node).color, want};
}"""


def assert_colour(locator: Any, token: str, timeout: float = 5.0) -> None:
    """`locator`'s text shows in the theme token `token`'s colour within `timeout`
    seconds, read again until it does, as a web-first assertion would."""
    from moxi_dev.testing import wait_for

    seen: list[dict] = []

    def matches() -> bool:
        seen.append(locator.evaluate(_COLOUR, token))
        return seen[-1]["seen"] == seen[-1]["want"]

    wait_for(matches, timeout, interval=0.05, what=lambda: f"the {token} colour; last seen {seen[-1:]}")


def control(form: Any, name: str) -> Any:
    """The form's control for the field `name`, with the form's "More" opened when the
    field is one of the rows it folds away."""
    found = form.locator(f'[data-control="{name}"]')
    found.wait_for(state="attached")
    if not found.is_visible():
        form.locator("details > summary", has_text=re.compile(r"^More$")).click()
    return found
