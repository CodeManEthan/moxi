"""The test shell: a real shell in a process of its own, over this environment's modules.

`TestShell(home, ...)` starts `python -P -m moxi_ui.testing.run` with an environment of
its own: `HOME`, the XDG homes, `XDG_RUNTIME_DIR`, `TMPDIR` and `MOXI_HOME` all under
`home`, `PYTHONPATH` the `site` directories given (and nothing of this process's), and
`PATH` and the locale kept. So the shell, its page servers and the `moxi` commands it
runs keep every file under `home`, and this process's own environment never reaches
them. The modules it serves are this environment's installed ones, plus any installed
as a distribution in a `site` directory.

A test reaches it three ways:

- a browser: `sign_in(page)` takes a Playwright page through a one-use ticket to the
  shell's document at `/m/ui/`;
- a signed-in session with no browser: `session()`, for routes and streams;
- the token routes the shell gives only the runtime file's holder: `call()`,
  `servers()`, `server()`, `wait_server()` and `kill()`.
"""

from __future__ import annotations

import collections
import http.client
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: Seconds a test shell has to print its ready line.
START_TIMEOUT = 30.0
#: Seconds a stopping test shell has before it is killed.
STOP_TIMEOUT = 15.0
#: Lines of the shell process's stderr kept for a failure's message.
STDERR_LINES = 200

_KEPT = ("PATH", "LANG", "LANGUAGE", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
_BROWSER = (("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Mode", "cors"),
            ("Sec-Fetch-Dest", "empty"))


class ShellError(RuntimeError):
    """A test shell that did not start, or answered what a test helper can't use."""


def shell_env(home: str | os.PathLike, site: Sequence[str | os.PathLike] = ()) -> dict[str, str]:
    """The whole environment a test shell under `home` runs with (see the module doc)."""
    home = Path(home)
    env = {key: value for key, value in os.environ.items()
           if key in _KEPT or key.startswith("LC_")}
    env["HOME"] = str(home)
    for key, rel in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                     ("XDG_STATE_HOME", ".local/state"), ("XDG_CACHE_HOME", ".cache")):
        env[key] = str(home / rel)
    env["XDG_RUNTIME_DIR"] = str(home / "run")
    env["MOXI_HOME"] = str(home / "moxi")
    for key in ("TMPDIR", "TEMP", "TMP"):
        env[key] = str(home / "tmp")
    if sys.platform == "win32":
        env["USERPROFILE"] = str(home)
        env["APPDATA"] = str(home / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    if site:
        env["PYTHONPATH"] = os.pathsep.join(str(Path(path)) for path in site)
    return env


class TestShell:
    """One test shell under `home` (see the module doc). Use it as a context manager, or
    call `start()` and `stop()`.

    `site` are directories put on the shell's `PYTHONPATH`, where a test installs modules
    of its own. `child_args` go to every page server the shell starts (for example
    `("--idle-s", "0.5")`), and `leave_out` names installed modules the shell's snapshot
    leaves out. Both reach only this shell: no setting or variable carries them. `port`
    is the port it binds on 127.0.0.1, 0 (the default) for one the system picks.
    """

    __test__ = False  # pytest: not a test class

    def __init__(self, home: str | os.PathLike, *, site: Sequence[str | os.PathLike] = (),
                 child_args: Sequence[str] = (), leave_out: Sequence[str] = (),
                 port: int = 0, start_timeout: float = START_TIMEOUT):
        self.home = Path(home)
        self.site = tuple(Path(path) for path in site)
        self.child_args = tuple(str(arg) for arg in child_args)
        self.leave_out = tuple(leave_out)
        self.bind_port = int(port)
        self.start_timeout = start_timeout
        self.env = shell_env(self.home, self.site)
        self.port: int | None = None
        self.info: Any = None
        self._process: subprocess.Popen | None = None
        self._stderr: collections.deque[str] = collections.deque(maxlen=STDERR_LINES)

    # -- the process ----------------------------------------------------------------------

    def __enter__(self) -> TestShell:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def url(self) -> str:
        """The shell's origin, `http://127.0.0.1:<port>`."""
        if self.port is None:
            raise ShellError("the test shell is not running")
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> TestShell:
        """Start the shell and wait for its first module snapshot. Raises `ShellError`,
        with the shell's stderr, when it doesn't start in `start_timeout` seconds."""
        from moxi import proc
        from moxi.paths import Layout, python
        from moxi_loopback.api import RuntimeInfo

        if self._process is not None:
            raise ShellError("the test shell is already started")
        for sub in ("tmp", "run"):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        os.chmod(self.home / "run", 0o700)
        cwd = Layout("ui").runtime_dir(self.env, create=True)
        options = json.dumps({"child_args": list(self.child_args),
                              "leave_out": list(self.leave_out), "port": self.bind_port})
        self._process = proc.spawn([python(), "-P", "-m", "moxi_ui.testing.run", options],
                                   lifeline="fd", env=self.env, cwd=cwd,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._keep_stderr, name="moxi-ui-test-shell-stderr",
                         daemon=True).start()
        line = self._ready_line()
        ready = json.loads(line)
        self.port = ready["port"]
        self.info = RuntimeInfo(module="ui", server_id=ready["server_id"], pid=ready["pid"],
                                transport="tcp", address="127.0.0.1", port=ready["port"],
                                token=ready["token"], version=ready["version"],
                                started_at=ready["started_at"])
        threading.Thread(target=self._drain_stdout, name="moxi-ui-test-shell-stdout",
                         daemon=True).start()
        # The first request under /m/ waits for the shell's first snapshot; take that
        # wait here, so no test's first page pays for it.
        status, _ = self.session(timeout=self.start_timeout).get("/m/ui/api/modules")
        if status != 200:
            raise ShellError(f"the test shell's modules view answered {status}"
                             + self._stderr_text())
        return self

    def _ready_line(self) -> str:
        assert self._process is not None and self._process.stdout is not None
        found: list[bytes] = []
        reader = threading.Thread(target=lambda: found.append(self._process.stdout.readline()),
                                  daemon=True)
        reader.start()
        reader.join(self.start_timeout)
        line = found[0].decode("utf-8", "replace").strip() if found else ""
        if not line.startswith("ready "):
            code = self._process.poll()
            self._kill()
            said = f"exited {code}" if code is not None else \
                f"printed no ready line in {self.start_timeout:g}s"
            raise ShellError(f"the test shell {said}" + self._stderr_text())
        return line[len("ready "):]

    def _keep_stderr(self) -> None:
        stream = self._process.stderr if self._process else None
        if stream is None:
            return
        for raw in stream:
            self._stderr.append(raw.decode("utf-8", "replace").rstrip("\n"))

    def _drain_stdout(self) -> None:
        stream = self._process.stdout if self._process else None
        if stream is not None:
            for _ in stream:
                pass

    def _stderr_text(self) -> str:
        time.sleep(0.2)  # let the reader take the last lines of an exited shell
        lines = list(self._stderr)[-40:]
        return (":\n" + "\n".join(lines)) if lines else " (its stderr was empty)"

    def stderr(self) -> list[str]:
        """The last lines the shell process wrote to its stderr."""
        return list(self._stderr)

    def stop(self) -> int | None:
        """Stop the shell, and so every page server it started: close its lifeline and
        wait, then kill it after `STOP_TIMEOUT` seconds. Returns its exit code."""
        process, self._process = self._process, None
        if process is None:
            return None
        try:
            process.lifeline.close()
        except OSError:
            pass
        try:
            return process.wait(STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait(STOP_TIMEOUT)
        finally:
            self.port = None

    def _kill(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.kill()
            process.wait(STOP_TIMEOUT)

    @property
    def running(self) -> bool:
        """Whether the shell process is still running."""
        return self._process is not None and self._process.poll() is None

    # -- where its files are --------------------------------------------------------------

    def runtime_dir(self, module: str) -> Path:
        """`module`'s runtime directory as the shell and its page servers see it."""
        from moxi.paths import Layout

        return Layout(module).runtime_dir(self.env)

    def state_dir(self, module: str) -> Path:
        """`module`'s state directory as the shell and its page servers see it."""
        from moxi.paths import Layout

        return Layout(module).state_dir(self.env)

    def config_file(self, module: str) -> Path:
        """`module`'s config file as `moxi config` in the shell's children sees it."""
        from moxi.paths import Layout

        return Layout(module).config_file(self.env)

    def log_path(self) -> Path:
        """The shell's log file, where each page server's stderr goes."""
        return self.state_dir("ui") / "logs" / "shell.log"

    # -- signing in -----------------------------------------------------------------------

    def ticket(self) -> str:
        """A fresh one-use sign-in ticket, as `moxi ui open` mints one."""
        from moxi_ui.session import TICKETS_DIR, Tickets

        return Tickets(self.runtime_dir("ui") / TICKETS_DIR).mint()

    def signin_url(self) -> str:
        """A fresh sign-in URL, its ticket in the fragment."""
        from moxi_ui.session import signin_url

        if self.port is None:
            raise ShellError("the test shell is not running")
        return signin_url(self.port, self.ticket())

    def sign_in(self, page: Any) -> Any:
        """Sign the Playwright `page`'s browser context in, and leave the page on the
        shell's document at `/m/ui/` with the modules view placed. Returns the page."""
        page.goto(self.signin_url())
        page.wait_for_url(self.url + "/m/ui/")
        page.locator("#moxi-panel > moxi-ui-page").wait_for(state="attached")
        return page

    def session(self, *, timeout: float = 10.0) -> Session:
        """A signed-in session with no browser: the cookie and the key a sign-in gives,
        sent the way the shell's document sends them."""
        if self.port is None:
            raise ShellError("the test shell is not running")
        return Session.sign_in(self.port, self.ticket(), timeout=timeout)

    # -- the token routes -----------------------------------------------------------------

    def call(self, method: str, path: str, body: Any = None, *,
             timeout: float = 10.0) -> tuple[int, Any]:
        """One request to a shell route with the runtime file's token, as `moxi ui status`
        sends it: `(status, data)`."""
        from moxi_loopback.api import call

        if self.info is None:
            raise ShellError("the test shell is not running")
        return call(self.info, method, path, body, timeout=timeout)

    def servers(self) -> list[dict[str, Any]]:
        """The shell's page-server rows, `/m/ui/api/servers`'s `servers`."""
        status, data = self.call("GET", "/m/ui/api/servers")
        if status != 200:
            raise ShellError(f"/m/ui/api/servers answered {status}")
        return list(data["servers"])

    def server(self, module: str) -> dict[str, Any] | None:
        """`module`'s page-server row, or None when the shell has none for it."""
        return next((row for row in self.servers() if row["module"] == module), None)

    def wait_server(self, module: str, check: str | Callable[[dict[str, Any]], bool], *,
                    timeout: float = 10.0) -> dict[str, Any]:
        """Wait for `module`'s row to be in state `check` (or for `check(row)` to be
        true), and return it. Raises `AssertionError` after `timeout` seconds."""
        wanted = (lambda row: row["state"] == check) if isinstance(check, str) else check
        deadline = time.monotonic() + timeout
        row = None
        while True:
            row = self.server(module)
            if row is not None and wanted(row):
                return row
            if time.monotonic() >= deadline:
                raise AssertionError(f"{module}'s page server did not reach {check!r} in "
                                     f"{timeout:g}s; its row: {row}")
            time.sleep(0.05)

    def kill(self, module: str) -> int:
        """Kill `module`'s running page server outright, as a crash would end it, and
        return its pid. Raises `ShellError` when none is running."""
        import psutil

        row = self.server(module)
        if row is None or row["state"] not in ("running", "draining") or not row["pid"]:
            raise ShellError(f"{module} has no running page server: {row}")
        process = psutil.Process(row["pid"])
        process.kill()
        process.wait(STOP_TIMEOUT)
        return row["pid"]


class Session:
    """A signed-in session with no browser, over plain HTTP to the shell's listener."""

    def __init__(self, port: int, cookie: str, key: str, *, timeout: float = 10.0):
        self.port = port
        self.cookie = cookie
        self.key = key
        self.timeout = timeout

    @classmethod
    def sign_in(cls, port: int, ticket: str, *, timeout: float = 10.0) -> Session:
        """Redeem `ticket` at the shell's sign-in route, as the sign-in page does."""
        body = json.dumps({"ticket": ticket}).encode()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("POST", "/m/ui/api/signin", body=body, headers={
                "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
                "Content-Type": "application/json"})
            answer = conn.getresponse()
            data = answer.read()
            cookie = answer.getheader("Set-Cookie") or ""
        finally:
            conn.close()
        if answer.status != 200 or not cookie:
            raise ShellError(f"the sign-in answered {answer.status}")
        return cls(port, cookie.split(";", 1)[0], json.loads(data)["key"], timeout=timeout)

    def headers(self, *, act: bool = False) -> dict[str, str]:
        """The headers the shell's document sends on a route: the cookie, the key and
        Fetch Metadata, and for an act its `Origin` and JSON type."""
        headers = {"Cookie": self.cookie, "X-Moxi-Key": self.key, **dict(_BROWSER)}
        if act:
            headers["Origin"] = f"http://127.0.0.1:{self.port}"
            headers["Content-Type"] = "application/json"
        return headers

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """One request: `(status, data)`, `data` the parsed JSON or None."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        try:
            payload = None if body is None else json.dumps(body).encode()
            conn.request(method, path, body=payload, headers=self.headers(act=method == "POST"))
            answer = conn.getresponse()
            raw = answer.read()
        finally:
            conn.close()
        kind = answer.getheader("Content-Type") or ""
        return answer.status, (json.loads(raw) if raw and "json" in kind else None)

    def get(self, path: str) -> tuple[int, Any]:
        return self.request("GET", path)

    def post(self, path: str, body: Mapping[str, Any] | None = None) -> tuple[int, Any]:
        return self.request("POST", path, dict(body or {}))

    @contextmanager
    def stream(self, path: str, *, timeout: float | None = None) -> Iterator[Stream]:
        """A server-sent event stream at `path` (a module's stream route through the
        proxy, or `/m/ui/api/push?topics=...`), open for the `with` block."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=self.timeout if timeout is None else timeout)
        try:
            headers = {**self.headers(), "Accept": "text/event-stream"}
            conn.request("GET", path, headers=headers)
            yield Stream(conn.getresponse())
        finally:
            conn.close()


class Stream:
    """One open server-sent event stream. `status` is its answer's status."""

    def __init__(self, response: http.client.HTTPResponse):
        self.response = response
        self.status = response.status

    def event(self) -> tuple[str, Any]:
        """The next event, `(name, data)` with `data` parsed as JSON; comments are
        skipped. Raises `EOFError` when the stream ends, and the socket's timeout when
        nothing comes in time."""
        name, data = "message", []
        while True:
            raw = self.response.readline()
            if not raw:
                raise EOFError("the stream ended")
            line = raw.decode("utf-8").rstrip("\r\n")
            if line == "":
                if data:
                    return name, json.loads("\n".join(data))
                name = "message"
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                name = value
            elif field == "data":
                data.append(value)

    def until(self, check: Callable[[str, Any], bool]) -> tuple[str, Any]:
        """Read events until `check(name, data)` is true; return that event."""
        while True:
            name, data = self.event()
            if check(name, data):
                return name, data
