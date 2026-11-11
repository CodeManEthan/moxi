"""What the launcher's browser tests share: a fake harness on the test shell's `PATH`, the
launch log the shell's page server reads, seeded as a launch would leave it, the shell's
push channel, the panel's parts, slots for a shell (the fake, scripted, or slots' own
server on the shell's socket), and the launcher's piece on Home.

A test shell keeps its files under a home of its own and hands its page servers only
`PATH` and the locale, so its launcher page server reads the launch log under that home
(`store.connect(env=shell.env)`), never the test's or the user's, and a launch it makes
finds its harness only on the `PATH` the shell was started with.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import signal
import subprocess
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
         session_id: str | None = None, cwd: str = "/work/page", actor: str = "ann") -> str:
    """A Claude launch's row made by `actor`, started; its process this test's own when
    `live`, else one that has ended. Returns its session id."""
    sid = session_id or str(uuid.uuid4())
    me = proc.identity(os.getpid())
    slot = "3" if target == "slot" else None
    # The values a resume replays: where the session ran, and how.
    values = {"harness": "claude", "target": target, "cwd": cwd, "model": "opus",
              **({"slot": slot} if slot else {})}
    with launch_log(shell) as conn:
        store.insert(conn, request_id=request_id, actor=actor, harness="claude",
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


# --- slots for a shell --------------------------------------------------------------------

#: The launcher's tests folder: on a shell's `site`, its page servers can import the fakes.
TESTS = Path(__file__).resolve().parent


class FakeSlots:
    """The fake slots for one test shell: a directory holding the script the fake reads at
    each call and the calls it got. Start the shell with `options()`, so its launcher page
    server and the jobs it starts use the fake and never slots' own API."""

    def __init__(self, root: Path):
        self.dir = Path(root)
        self.dir.mkdir(parents=True, exist_ok=True)

    def options(self) -> dict[str, Any]:
        """The shell options that put the fake in slots' place."""
        return {"site": [str(TESTS)],
                "env": {"MOXI_LAUNCHER_SLOTS": "launcher_fake_slots",
                        "LAUNCHER_FAKE_SLOTS": str(self.dir)}}

    def script(self, **keys: Any) -> None:
        """Write the fake's script anew (see `launcher_fake_slots`), written whole and then
        moved into place, since the page server may read it at any moment."""
        tmp = self.dir / "script.json.tmp"
        tmp.write_text(json.dumps(keys))
        os.replace(tmp, self.dir / "script.json")

    def starts(self) -> list[dict]:
        """Every `start` the fake got so far, oldest first."""
        import launcher_fake_slots

        return launcher_fake_slots.calls(self.dir, "start")


def free(*numbers: int) -> dict[str, dict]:
    """A script's `slots` with each of `numbers` free to launch in."""
    return {str(number): {"state": "free", "launchable": True} for number in numbers}


#: The oldest tmux slots runs on.
TMUX_FLOOR = (3, 2)
#: What the shell's environment runs to set slots up and say where its socket is.
_SLOTS_SETUP = """
import sys
from moxi import config, paths
config.set("slots", "tmux", sys.argv[1], by="launcher browser test")
config.set("slots", "tmux_conf", sys.argv[2], by="launcher browser test")
print(paths.socket_path("slots", "tmux"))
"""


@contextmanager
def own_slots(shell: Any) -> Iterator[str]:
    """slots' server on `shell`'s own socket, started as the supervisor would start it
    (tmux's absolute path recorded, `tmux_conf` the null device), and stopped after with
    its tmux server. The shell's `MOXI_HOME` is under its own home and slots' socket
    follows it, so the default tmux server, and any other, is never reached. Yields the
    socket; skips the test where tmux on `PATH` is older than slots runs on."""
    from moxi import paths
    from moxi_dev.testing import wait_for

    found = shutil.which("tmux")
    said = subprocess.run([found, "-V"], capture_output=True, text=True, timeout=5).stdout
    version = re.search(r"(\d+)\.(\d+)", said)
    if version is None or tuple(map(int, version.groups())) < TMUX_FLOOR:
        pytest.skip(f"tmux on PATH is {said.strip()!r}, below 3.2")
    env = dict(shell.env)
    tmux = os.path.abspath(found)
    socket = subprocess.run([sys.executable, "-c", _SLOTS_SETUP, tmux, os.devnull], env=env,
                            capture_output=True, text=True, check=True,
                            timeout=30).stdout.strip()
    assert socket.startswith(env["MOXI_HOME"]) or "/moxi-" in socket, socket
    log_path = shell.home / "slots-server.log"
    with open(log_path, "wb") as log:
        server = subprocess.Popen([str(paths.script("moxi-slots-server"))], env=env,
                                  stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=subprocess.STDOUT, start_new_session=True)
    try:
        wait_for(lambda: subprocess.run([tmux, "-S", socket, "display-message", "-p",
                                         "#{pid}"], capture_output=True, env=env,
                                        timeout=5).returncode == 0,
                 20, interval=0.2,
                 what=lambda: f"slots' own server; its log: "
                              f"{log_path.read_text(errors='replace')}")
        yield socket
    finally:
        subprocess.run([tmux, "-S", socket, "kill-server"], capture_output=True, env=env,
                       timeout=10)
        if server.poll() is None:
            try:
                os.killpg(server.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=10)


# --- the piece on Home --------------------------------------------------------------------

PLACES = "/m/launcher/api/places"


def actor(shell: Any) -> str:
    """Who the shell's launcher page server launches as. A launch row counts as a place
    only when its `actor` is this."""
    status, answer = session(shell).get(PLACES)
    assert status == 200 and answer["actor"], (status, answer)
    return answer["actor"]


def place(shell: Any, request_id: str, *, cwd: str, at: str,
          project_key: str | None = None) -> None:
    """A launch's row as the shell's own user would have left it in `cwd` at `at` (UTC,
    `YYYY-MM-DDTHH:MM:SS.ffffffZ`), in the project `project_key` when one is given: a place
    the line above Home's box offers."""
    who = actor(shell)
    with launch_log(shell) as conn:
        store.insert(conn, request_id=request_id, actor=who, harness="codex",
                     target="terminal", cwd=cwd, project_key=project_key, values={},
                     argv=["codex"], env={}, posture="operator", at=at)


#: Before this, a launch is one a test made from a page; a place seeded to stay the newest
#: is dated after it.
AHEAD = "2090-01-01T00:00:00.000000Z"

_fresh = itertools.count(1)


def fresh_place(shell: Any) -> Path:
    """A folder under the shell's home that no launch has run in, seeded as the newest
    place, so Home opens there and fills nothing from a launch an earlier test made."""
    number = next(_fresh)
    folder = Path(shell.env["HOME"]) / "work" / f"fresh-{number}"
    folder.mkdir(parents=True, exist_ok=True)
    at = f"2099-01-01T{number // 3600:02d}:{number // 60 % 60:02d}:{number % 60:02d}.000000Z"
    place(shell, f"fresh-place-{number}", cwd=str(folder), at=at)
    return folder


def newest_launch(shell: Any) -> Any:
    """The newest row of the shell's launch log, places seeded ahead of the clock aside."""
    with launch_log(shell) as conn:
        return store.log(conn, limit=1, before=AHEAD)[0]


def home_prompt(page: Any) -> Any:
    """The launcher's piece in Home's main spot, on the shell's document in `page`."""
    return page.locator("#moxi-panel > moxi-ui-page moxi-ui-home .spot moxi-launcher-prompt")


def open_home(page: Any) -> Any:
    """Home opened anew in `page`; returns the launcher's piece once its button can be
    pressed, so the places, the launch form and where a launch may run are all read."""
    page.goto("/m/ui/")
    piece = home_prompt(page)
    piece.locator(".go:not([disabled])").wait_for()
    return piece
