"""`moxi ui serve`, also `python -P -m moxi_ui.serve` and `moxi-ui-serve`: the shell.

It runs under `moxi.aio.run(workers=4, slow=0.5)` and:

1. creates its runtime dir and holds `<runtime_dir>/page.lock` for its whole life, taken
   before anything else (a shell of this user is running exactly while the lock is
   held); a 3 s wait that runs out exits 75;
2. takes its listeners;
3. reads its epoch and opens its session store for it;
4. writes its runtime file, `page.json`, with loopback's `write_runtime` (format 1, TCP);
5. serves `worker_layer(guard(prove(router), profile))` on the listeners with loopback's
   `serve` and a 4 MiB head limit, so every request head Chromium or Firefox can send,
   a worker's update check under a full cookie jar included, reaches the worker layer;
6. reads its module snapshot (`moxi modules --json` and the page exports, in fresh
   processes on a worker thread) and serves the table it gives from the next request
   on, reading it again when a module directory changes.

The shell's own page (`moxi_ui.page`) is served in process at `/m/ui/`, its export
mounted there in the table like any module's.

A port another program holds exits 1, naming the fix. At a clean stop it closes the
listeners and removes `page.json` before it lets go of the lock.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from importlib import metadata
from typing import Any

from moxi import files
from moxi.paths import Layout
from moxi_loopback.api import (
    Page,
    RuntimeInfo,
    Table,
    app,
    export,
    guard,
    prove,
    remove_runtime,
    serve,
    write_runtime,
)

from . import cli, config, listen
from . import page as shell_page
from .listen import Listeners
from .proxy import Proxy
from .push import Push
from .router import ASGIApp, Router, shell_profile, shell_table, worker_layer
from .session import (
    HEALTH_PATH,
    SERVERS_PATH,
    SIGNIN_API,
    SIGNOUT_API,
    STOP_PATH,
    TICKETS_DIR,
    WORKER_PATH,
    OwnerCheck,
    Sessions,
    Tickets,
    open_store,
)
from .snapshot import ModuleSnapshot, View
from .starter import Starters

MODULE = "ui"
#: The most bytes of an unfinished request head the shell's listener buffers. Above the
#: largest head Chromium (about 2.8 MB) or Firefox (about 1.8 MB) sends, a worker's update
#: check from the longest script URL under a full cookie jar included. Each connection
#: holding an unfinished head may cost this much memory.
HEAD_LIMIT = 4 * 1024 * 1024
#: Seconds a starting shell waits for another shell's lock before it exits 75.
LOCK_WAIT = 3.0
LOCK_FILE = "page.lock"
#: The event loop's worker threads and slow-callback threshold.
WORKERS = 4
SLOW = 0.5

_log = logging.getLogger("moxi.ui")

#: The paths under `/m/` that never need the module snapshot: the token routes, the
#: worker answer and the sign-in.
_NO_SNAPSHOT = frozenset(path.encode("ascii") for path in (
    HEALTH_PATH, SERVERS_PATH, STOP_PATH, WORKER_PATH, SIGNIN_API, SIGNOUT_API))


def version() -> str:
    """moxi-ui's installed version."""
    try:
        return metadata.version("moxi-ui")
    except metadata.PackageNotFoundError:
        return "0+unknown"


def epoch_for(listeners: Listeners) -> str | None:
    """The epoch of the hold the shell serves under, or None.

    A shell that bound its own listener has none: its hold is its own process, so its
    sessions live in its memory only. A shell on sockets inherited from systemd socket
    units takes the units' activation ids, and only when every unit is active and the
    inherited names are exactly those units.
    """
    return None


def may_exit_idle(*, inherited: bool, epoch: str | None, live_sessions: int) -> bool:
    """Whether an idle shell may exit: only when exiting loses no session and leaves the
    port held. So only on an inherited listener (the socket keeps the port), and only
    with an epoch (its sessions are on disk) or with no live session."""
    return inherited and (epoch is not None or live_sessions == 0)


class Shell:
    """One shell process's serving state, on listeners it already holds.

    `start()` writes the runtime file and builds the request path; `run()` serves until
    `stop()`, then stops every page server the shell started; `close()` closes the
    listeners and removes the runtime file.

    `starters` is the shell's one set of page-server starters, built here unless given;
    `child_args` go to every page server it starts (tests pass `("--idle-s", "0.5")`).
    Without a `proxy`, `/m/<module>/` goes to the proxy over those starters, and without a
    `push`, `/m/ui/api/push` goes to a `Push` over them and `mounts` (`Shell.push`; the
    snapshot gives it new exports with `set_exports` and `ui.modules` with `publish`).

    `page` is the shell's own page, `moxi_ui.page.PAGE` unless given; its export is
    mounted at `/m/ui`. `modules` and `mounts` are the table before any snapshot. With
    `snapshot` True (or a `ModuleSnapshot`), the shell reads its module snapshot when
    `run()` starts and takes each refresh's table, versions, exports and modules view;
    `leave_out` names installed modules it leaves out (test shells only).
    """

    def __init__(self, listeners: Listeners, *, epoch: str | None,
                 layout: Layout | None = None, owner: OwnerCheck | None = None,
                 modules: Sequence[str] = (), mounts: Mapping[str, Mapping[str, Any]] | None = None,
                 page: Page | None = None, push: ASGIApp | None = None,
                 proxy: ASGIApp | None = None, starters: Starters | None = None,
                 child_args: Sequence[str] = (), snapshot: bool | ModuleSnapshot = False,
                 leave_out: Sequence[str] = ()):
        self.listeners = listeners
        self.epoch = epoch
        self.layout = layout or Layout(MODULE)
        self.info: RuntimeInfo | None = None
        store = open_store(self.layout.state_dir(), epoch)
        tickets = Tickets(self.layout.runtime_dir() / TICKETS_DIR)
        self.sessions = Sessions(store, tickets, owner=owner or OwnerCheck.here(),
                                 token=lambda: self.info.token if self.info else None)
        self._page = page or shell_page.PAGE
        self.own_export = export(self._page)
        self._modules = tuple(modules)
        self._mounts = {**(mounts or {}), MODULE: self.own_export}
        self.started = time.monotonic()
        self.starters = starters or Starters(
            runtime_dir=self.layout.runtime_dir(), state_dir=self.layout.state_dir(),
            log=cli.log_path(), child_args=child_args)
        self.push = push if push is not None else Push(self.starters, mounts=self._mounts)
        self._proxy = proxy if proxy is not None else Proxy(
            self.starters, table=lambda: self.profile.table)
        if snapshot is True:
            snapshot = ModuleSnapshot(cwd=self.layout.runtime_dir(), env=self.starters.child_env,
                                      leave_out=leave_out)
        self.snapshot: ModuleSnapshot | None = snapshot or None
        if self.snapshot is not None:
            if self.snapshot.own is None:
                self.snapshot.own = self.own_export
            self.snapshot.on_view = self.take_view
        self.router: Router | None = None
        self.asgi: ASGIApp | None = None
        self.profile = None
        self._should_exit: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> RuntimeInfo:
        """Write the runtime file and build the request path. Returns the runtime info."""
        self.info = write_runtime(MODULE, transport="tcp", address="127.0.0.1",
                                  port=self.listeners.port, version=version())
        self.started = time.monotonic()
        page = app(self._page, module=MODULE, server_id=self.info.server_id, prefix="/m/ui",
                   version=self.info.version)
        self.router = Router(self.sessions, page=shell_page.serving(self, page),
                             push=self.push, proxy=self._proxy)
        self.set_table(shell_table(self._modules, self._mounts))
        inner = guard(prove(self.router, token=self.info.token, server_id=self.info.server_id),
                      lambda: self.profile)
        if self.snapshot is not None:
            inner = _needing(self.snapshot, inner)
        self.asgi = worker_layer(inner)
        return self.info

    @property
    def mounts(self) -> dict[str, Mapping[str, Any]]:
        """The exports the table mounts now, module to export, the shell's own included."""
        return self._mounts

    def take_view(self, view: View) -> None:
        """Take a snapshot refresh: its table from the next request on, its versions (a
        running server of another version is drained), its exports for the push, and
        its modules view as `ui.modules`."""
        mounts = {**view.exports.mounts, MODULE: self.own_export}
        try:
            table = shell_table(view.pages, mounts)
        except ValueError:
            _log.exception("the module snapshot's table was refused; the last one stays")
            return
        self._modules, self._mounts = tuple(view.pages), mounts
        self.set_table(table)
        self.starters.set_versions(view.versions)
        if isinstance(self.push, Push):
            self.push.set_exports(mounts)
            self.push.publish("modules", view.value)

    def set_table(self, table: Table) -> None:
        """Check every request from the next one on against `table`, which this freezes."""
        self.profile = shell_profile(table, self.sessions, self.listeners.names)

    async def run(self) -> None:
        """Serve until `stop()`, a signal, or the server's own end."""
        self._loop = asyncio.get_running_loop()
        self._should_exit = asyncio.Event()
        _log.info("serving on 127.0.0.1:%d (%s)", self.listeners.port,
                  ", ".join(sorted(self.listeners.names)))
        # Push streams never end on their own, so the stop ends them itself rather than
        # leave them to the server's shutdown deadline.
        ending = asyncio.ensure_future(self._end_push_at_stop())
        if self.snapshot is not None:
            self.snapshot.start()
        try:
            await serve(self.asgi, self.listeners.sockets, should_exit=self._should_exit,
                        head_limit=HEAD_LIMIT)
        finally:
            ending.cancel()
            if self.snapshot is not None:
                await self.snapshot.aclose()
            if isinstance(self.push, Push):
                await self.push.aclose()
            await self.starters.aclose()

    async def _end_push_at_stop(self) -> None:
        assert self._should_exit is not None
        await self._should_exit.wait()
        if isinstance(self.push, Push):
            await self.push.aclose()

    def stop(self) -> None:
        """Ask a running shell to stop; safe from any thread."""
        if self._loop is not None and self._should_exit is not None:
            try:
                self._loop.call_soon_threadsafe(self._should_exit.set)
            except RuntimeError:  # The loop has closed.
                pass

    def close(self) -> None:
        """Close the listeners, then remove the runtime file if it is still ours."""
        self.listeners.close()
        if self.info is not None:
            try:
                remove_runtime(MODULE, self.info.server_id)
            except OSError:
                _log.exception("removing the runtime file failed")


def _needing(snapshot: ModuleSnapshot, inner: ASGIApp) -> ASGIApp:
    """`inner`, with each request that needs the module snapshot (any path under `/m/`
    but the token routes, the worker answer and the sign-in) giving it the chance to look
    for a changed module directory, and waiting for the first snapshot after the start."""

    async def layer(scope: dict[str, Any], receive: Callable[..., Any],
                    send: Callable[..., Any]) -> None:
        if scope.get("type") == "http":
            raw = scope.get("raw_path") or b""
            if raw.startswith(b"/m/") and raw not in _NO_SNAPSHOT:
                snapshot.touch()
                if snapshot.current is None:
                    await snapshot.ready()
        await inner(scope, receive, send)

    return layer


def run(*, bind: Callable[[int], Listeners] = listen.bind,
        say: Callable[[str], Any] | None = None) -> int:
    """Run the shell to its end and return its exit code: 0 for a stop, 1 for an error
    (the port taken included), 75 when another shell holds the lock."""
    say = say or (lambda text: print(text, file=sys.stderr, flush=True))
    layout = Layout(MODULE)
    try:
        runtime_dir = layout.runtime_dir(create=True)
    except OSError as err:
        say(f"moxi ui: the runtime directory cannot be made private: {err}")
        return 1
    try:
        with files.lock(runtime_dir / LOCK_FILE, timeout=LOCK_WAIT):
            return _run_held(layout, bind, say)
    except files.LockTimeout:
        say("moxi ui: another shell is running or starting (it holds page.lock)")
        return files.LockTimeout.exit_code


def _run_held(layout: Layout, bind: Callable[[int], Listeners], say: Callable[[str], Any]) -> int:
    try:
        port = config.port()
    except Exception as err:  # noqa: BLE001 - a bad setting is said and ends the start
        say(f"moxi ui: {err}")
        return 1
    try:
        listeners = bind(port)
    except listen.PortInUse as err:
        say(f"moxi ui: {err}")
        return 1
    except OSError as err:
        say(f"moxi ui: the listener on port {port} could not be bound: {err}")
        return 1
    shell = Shell(listeners, epoch=epoch_for(listeners), layout=layout, snapshot=True)
    try:
        shell.start()
        from moxi import aio

        aio.run(shell.run, workers=WORKERS, slow=SLOW)
    except KeyboardInterrupt:
        pass
    finally:
        shell.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="moxi-ui-serve", description="Run Moxi's browser shell in the foreground.")
    parser.parse_args(argv)
    from moxi import log

    log.setup(MODULE, "info")
    return run()


if __name__ == "__main__":
    sys.exit(main())
