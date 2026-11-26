"""What turns' browser tests share: a test shell over the full install or the small one,
the stand-in `claude` its page server finds, the reach it reads, and the panel opened in a
browser.

The sessions are the fake harness's endpoints (`make_endpoint`), run in the test's own
process with turns' runtime root pointed at the shell's (`on`), so the page server the
shell starts finds them where it looks. No live session is read.

The fence. A shell's page server reads the reach through `claude_setup.reach`, which runs
the `claude` its PATH finds. Each shell's PATH holds the stand-in's folder first and none
of this process's folders that hold a `claude`, so the page server runs the stand-in or no
`claude` at all, never a real one. The stand-in answers `plugin list --json` from a file
the test writes; its placed copy is in the shell's own data folder. The page server's
`CLAUDE_CONFIG_DIR` is a folder in the shell's home, so the Claude Code records it reads for
typed names are the ones a test writes (`Panel.name_session`), never a real one.

Launcher's `running` is answered in the browser by `LauncherStub`, in launcher's declared
shape, its entries joined to the endpoints' session ids: its route, and its topic on the
shell's push stream, so a value the full install's real launcher publishes never replaces
the stub's. These tests import no other package's kit.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from collections.abc import Iterator
from typing import Any

import pytest
from moxi import modules
from moxi.paths import Layout

from conftest import FAKE_CLAUDE
from fake_harness import FakeHarness
from moxi_dev.testing import short_dir

#: The modules the small install keeps: the core's own, ui and turns.
SMALL = ("moxi", "ui", "turns")
PAGE = "/m/turns/"
SESSIONS = "/m/turns/api/sessions"
#: A time of day as the page words one: ui's hour and minute in the browser's locale.
T = r"\d{1,2}:\d\d(?: [AP]M)?"
#: Seconds a wait on the page server's list may take: a scan, then a first read.
LISTED_S = 20.0
#: Seconds a browser test may take, its setup and teardown included.
TEST_S = 120

#: Every browser test module's marks. Its bound is kept by a thread that, past `TEST_S`,
#: prints every thread's stack and ends the test's process: under xdist the run reports
#: the worker crashed while running that test, and goes on in a new worker. The default
#: way, a signal, raises once inside the test, and that is not enough here: Playwright's
#: sync calls that close a context or a browser, stop a trace or run a script take no
#: timeout of their own, so once the browser stops answering, the teardown after that one
#: raise waits on it with no end. A raise that lands in one of asyncio's callbacks is
#: also caught there, and the test then waits on.
MARKS = [pytest.mark.browser, pytest.mark.timeout(TEST_S, method="thread"),
         pytest.mark.skipif(not sys.platform.startswith("linux"),
                            reason="turns' panel is checked in a browser on Linux; the Windows "
                                   "and macOS runs are owed")]


def others() -> list[str]:
    """Every installed module the small install leaves out."""
    return [found.name for found in modules.scan() if found.name not in SMALL]


class Stub:
    """The stand-in `claude` in `root/bin`, answering from `root/state`: `plugins.json` is
    what `plugin list --json` prints, `log` each call's arguments."""

    def __init__(self, root: Path) -> None:
        self.bin = root / "bin"
        self.state = root / "state"
        self.bin.mkdir(parents=True)
        self.state.mkdir()
        (self.state / "markets.json").write_text("[]")
        self.plugins([])

    @property
    def exe(self) -> Path:
        return self.bin / "claude"

    def put(self) -> None:
        """Puts the stand-in on the shell's PATH."""
        self.exe.write_text(FAKE_CLAUDE)
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IXUSR)

    def plugins(self, listed: list[dict]) -> None:
        """What `plugin list --json` prints from now on."""
        tmp = self.state / "plugins.json.tmp"
        tmp.write_text(json.dumps(listed))
        os.replace(tmp, self.state / "plugins.json")

    def runner_listed(self) -> None:
        """The runner installed at user scope and enabled, as Claude Code lists it."""
        from moxi_turns import claude_setup

        self.plugins([{"id": f"{claude_setup.RUNNER}@{claude_setup.MARKETPLACE}",
                       "version": "0.1.0", "scope": "user", "enabled": True,
                       "installPath": "/nowhere"}])

    def calls(self) -> list[str]:
        try:
            return (self.state / "log").read_text().splitlines()
        except FileNotFoundError:
            return []


def shell_path(stub: Stub) -> str:
    """The stand-in's folder, then this process's PATH without any folder that holds a
    `claude`."""
    kept = [part for part in os.environ.get("PATH", "").split(os.pathsep)
            if part and not (Path(part) / "claude").exists()]
    return os.pathsep.join([str(stub.bin), *kept])


class Panel:
    """One test shell with its stand-in `claude`; `is_small` when it is the small install."""

    def __init__(self, shell: Any, stub: Stub, runtime_root: Path, *, is_small: bool,
                 claude_dir: Path | None = None) -> None:
        self.shell = shell
        self.runtime_root = runtime_root
        self.claude_dir = claude_dir
        self.stub = stub
        self.is_small = is_small
        self._http: Any = None

    def endpoint_file(self) -> Path:
        """The placed copy's `endpoint.json`, in the shell's data folder."""
        from moxi_turns import claude_setup

        return (Layout("turns").data_dir(self.shell.env) / claude_setup.PLACED_NAME
                / claude_setup.RUNNER / claude_setup.ENDPOINT_FILE)

    def write_endpoint_file(self) -> None:
        """`endpoint.json` naming the endpoint command the shell's page server gives, as
        an install writes it: a whole file moved into place."""
        from moxi_turns import claude_setup

        target = self.endpoint_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(".endpoint.json.tmp")
        tmp.write_text(json.dumps({"command": str(claude_setup.endpoint_command(self.shell.env))}))
        os.replace(tmp, target)

    def name_session(self, pid: int, session_id: str, name: str, *,
                     source: str | None = "user") -> None:
        """Claude Code's record of process `pid` in the shell's Claude Code folder, as
        Claude Code writes it: session `session_id` named `name`, the name from `source`
        (None leaves `nameSource` out, as `--name` does)."""
        record: dict[str, Any] = {"pid": pid, "sessionId": session_id, "name": name,
                                  "status": "idle"}
        if source is not None:
            record["nameSource"] = source
        sessions_dir = self.claude_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        tmp = sessions_dir / f".{pid}.json.tmp"
        tmp.write_text(json.dumps(record))
        os.replace(tmp, sessions_dir / f"{pid}.json")

    def list(self) -> dict:
        """The page server's list now, through the shell."""
        if self._http is None:
            self._http = self.shell.session()
        status, data = self._http.get(SESSIONS)
        assert status == 200, (status, data)
        return data

    def listed(self, keys: set[str], *, reach: str | None = None) -> dict:
        """The list once it holds exactly the sessions `keys`, each read (or in trouble),
        and with `reach`, once its `claude` reads so."""
        deadline = time.monotonic() + LISTED_S
        while True:
            data = self.list()
            read = {row["key"] for row in data["sessions"]
                    if row["facts"] is not None or row["trouble"] is not None
                    or row["unread_since"] is not None}
            seen = {row["key"] for row in data["sessions"]}
            reached = reach is None or (data["reach"] or {}).get("claude") == reach
            if data["listed"] and seen == keys and read == keys and reached:
                return data
            if time.monotonic() >= deadline:
                found = sorted(p.parent.name for p in (self.runtime_root / "turns").rglob("hello.json"))
                raise AssertionError(f"the list never held {keys}: {data}; hello files: {found}")
            time.sleep(0.1)


@contextlib.contextmanager
def panel_shell(ui_shells, reach: str = "start", *, small: bool = False) -> Iterator[Panel]:
    """A new test shell with its stand-in `claude`, the small install with `small`, and the
    reach set: `start` (the stand-in lists the runner, `endpoint.json` names the command),
    `setup` (the stand-in lists nothing, no `endpoint.json`) or `absent` (no `claude` on
    PATH, nothing placed). Its home is a short directory, and turns' runtime root `rt` in
    it, since the endpoints the tests open put their sockets there; the shell is stopped
    before its home is removed."""
    with short_dir("tb-") as home:
        stub = Stub(home / "stub")
        if reach != "absent":
            stub.put()
        runtime_root = home / "rt"
        claude_dir = home / "cc"
        env = {"PATH": shell_path(stub), "FAKE_CLAUDE_DIR": str(stub.state),
               "FAKE_CLAUDE_VERSION": "2.1.296", "MOXI_TURNS_RUNTIME_DIR": str(runtime_root),
               "CLAUDE_CONFIG_DIR": str(claude_dir)}
        shell = ui_shells(home=home, env=env, leave_out=others() if small else [])
        try:
            panel = Panel(shell, stub, runtime_root, is_small=small, claude_dir=claude_dir)
            if reach == "start":
                stub.runner_listed()
                panel.write_endpoint_file()
            yield panel
        finally:
            shell.stop()


@pytest.fixture(scope="module")
def full(ui_shells):
    """The full install's shell, the reach `start`, for the module's tests."""
    with panel_shell(ui_shells) as panel:
        yield panel


@pytest.fixture(scope="module")
def small(ui_shells):
    """The small install's shell, the reach `start`, for the module's tests."""
    with panel_shell(ui_shells, small=True) as panel:
        yield panel


@pytest.fixture
def own_panel(ui_shells, turns_env, monkeypatch):
    """`own_panel(reach, *, small=False)`: a shell of the test's own (`panel_shell`),
    stopped when the test ends, with this process's runtime root set to it (`on`)."""
    with contextlib.ExitStack() as stack:
        def start(reach: str = "start", *, small: bool = False) -> Panel:
            return on(stack.enter_context(panel_shell(ui_shells, reach, small=small)), monkeypatch)

        yield start


def on(panel: Panel, monkeypatch) -> Panel:
    """`panel`, with this process's turns runtime root the one its shell's page server
    reads, so every endpoint `make_endpoint` opens is one it finds. A fixture that calls it
    takes `turns_env` first, which sets the root of its own."""
    monkeypatch.setenv("MOXI_TURNS_RUNTIME_DIR", str(panel.runtime_root))
    return panel


def harness(cwd: str = "/srv/work/sample-project", **over) -> FakeHarness:
    """A fake harness in `cwd`; `name="claude"` plays Claude Code, whose box turns reads."""
    fake = FakeHarness()
    fake.cwd = cwd
    for name, value in over.items():
        setattr(fake, name, value)
    return fake


def key_of(ep) -> str:
    """The row's key: the endpoint's directory name."""
    return ep.dir.name


class LauncherStub:
    """Launcher's `running`, answered in the browser in launcher's declared shape:
    `sessions` is what it lists. Its route answers so, and every value of its topic the
    push stream brings is replaced by the same, before the page reads it."""

    def __init__(self, page: Any, sessions: list[dict]) -> None:
        self.sessions = sessions
        page.route("**/m/launcher/api/running", self._running)
        page.add_init_script(f"window.heldTopics = Object.assign(window.heldTopics || {{}}, "
                             f"{{'launcher.running': {json.dumps(json.dumps(self.body()))}}});")

    def body(self) -> dict:
        return {"state": "up", "detail": None, "count": len(self.sessions), "sessions": self.sessions}

    def _running(self, route: Any) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.body()))


def launch(session_id: str | None, name: str, *, target: str = "slot", slot: str | None = "2",
           project: str | None = "kestrel-web", segment: str = "kestrel-web") -> dict:
    """One running launch in launcher's declared shape (`RUNNING_LAUNCH`)."""
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    return {
        "request_id": f"r-{session_id}", "harness": "claude", "session_id": session_id, "name": name,
        "identity": None, "where": {"target": target, "slot": slot, "project_key": project, "folder": segment},
        "started_at": at, "cache": None, "waits": None, "covered": False, "coverable": None,
        "activity": None,
    }


#: Keeps in `window.pushed` the name of each event the shell's push stream brings, and
#: gives the page, for each event named in `window.heldTopics`, that entry's data in place
#: of the stream's.
_PUSHED = r"""(() => {
  const pushed = window.pushed = [];
  const held = window.heldTopics = window.heldTopics || {};
  const fetched = window.fetch.bind(window);
  const frame = (text) => {
    const found = text.match(/^event: ?(.+)$/m);
    if (found === null) return text;
    const name = found[1].trim();
    pushed.push(name);
    return Object.hasOwn(held, name) ? `event: ${name}\ndata: ${held[name]}\n\n` : text;
  };
  window.fetch = async (input, init) => {
    const answer = await fetched(input, init);
    const url = typeof input === "string" ? input : input.url;
    if (!url.includes("/m/ui/api/push") || answer.body === null) return answer;
    let rest = "";
    const frames = new TransformStream({
      transform(chunk, out) {
        rest += chunk;
        for (let end = rest.indexOf("\n\n"); end !== -1; end = rest.indexOf("\n\n")) {
          out.enqueue(frame(rest.slice(0, end + 2)));
          rest = rest.slice(end + 2);
        }
      },
      flush(out) {
        if (rest !== "") out.enqueue(rest);
      },
    });
    const body = answer.body.pipeThrough(new TextDecoderStream()).pipeThrough(frames)
      .pipeThrough(new TextEncoderStream());
    return new Response(body, { status: answer.status, statusText: answer.statusText,
                                headers: answer.headers });
  };
})()"""


# The page's copies, kept where a test can read them back. Chromium is granted the
# clipboard; Firefox refuses a `clipboard-read` grant, so there each document wraps
# `navigator.clipboard.writeText` to keep the last text in `window.__copied`, then calls
# the real one. Guarded, so a second run in the same document wraps nothing.
_KEEP_COPIED = """(() => {
  const clipboard = navigator.clipboard;
  if (!clipboard || clipboard.__keepsCopied) return;
  const write = clipboard.writeText.bind(clipboard);
  clipboard.writeText = (text) => { window.__copied = text; return write(text); };
  clipboard.__keepsCopied = true;
})()"""


def _on_chromium(context: Any) -> bool:
    return context.browser.browser_type.name == "chromium"


def ready_copying(context: Any) -> None:
    """Ready `context` to read back what its page copies: grant the clipboard on
    Chromium, keep each `writeText` argument elsewhere. Read it with `copied`."""
    if _on_chromium(context):
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        return
    context.add_init_script(_KEEP_COPIED)
    for page in context.pages:
        page.evaluate(_KEEP_COPIED)


def copied(page: Any) -> str | None:
    """The text `page` last copied: the clipboard's on Chromium, the last `writeText`
    argument elsewhere (None when it copied nothing)."""
    if _on_chromium(page.context):
        return page.evaluate("navigator.clipboard.readText()")
    return page.evaluate("window.__copied ?? null")


def open_panel(panel: Panel, ui_contexts, *, route: str = "", launches: list[dict] | None = None,
               before=None, copying: bool = False, **context_args) -> tuple[Any, Any]:
    """A signed-in page on the shell's turns panel at `route`, once the shell's push stream
    has brought the modules; with `launches`, the launcher stub serves them. `before(page)`
    runs before the page opens (a route of its own). With `copying`, `copied(page)` reads
    back what a Copy wrote, in any browser (`ready_copying`, below). The page and the panel's
    root.

    The shell places the page again on a modules event when the address differs from the
    one it placed, and the page writes its address as a row opens; waiting for the stream's
    first value keeps a test from acting on a page about to be placed anew."""
    page = ui_contexts(panel.shell, **context_args).pages[0]
    if copying:
        ready_copying(page.context)
    page.add_init_script(_PUSHED)
    if launches is not None:
        LauncherStub(page, launches)
    if before is not None:
        before(page)
    page.goto(PAGE + route)
    page.wait_for_function("() => window.pushed.includes('ui.modules')")
    return page, page.locator("#moxi-panel > moxi-turns-page")


def row(root, key: str):
    return root.locator(f'moxi-ui-row[data-key="{key}"]')


def rows(root):
    return root.locator("moxi-ui-row[data-key]")


def group(root, name: str):
    return root.locator(f'section.grp[data-group="{name}"]')


def line(opened, name: str):
    """The words of the opened row's line `name` (`now`, `agents`, `prompt-box`, `session`,
    `details`), its label left out."""
    return opened.locator(f'[data-line="{name}"] .f')


def keys(root) -> list[str]:
    """The rows' keys, in the page's order."""
    return rows(root).evaluate_all("(rows) => rows.map((r) => r.dataset.key)")


def started(prefix: str) -> re.Pattern:
    """A row's name by its folder: `<prefix> · started today <time>`."""
    return re.compile(rf"^{re.escape(prefix)} · started today {T}$")


def address(page) -> str:
    """The page's address after `/m/turns/`."""
    return page.evaluate("() => decodeURIComponent(location.pathname)").removeprefix(PAGE)
