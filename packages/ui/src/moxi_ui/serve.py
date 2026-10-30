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
   a worker's update check under a full cookie jar included, reaches the worker layer.

A port another program holds exits 1, naming the fix. At a clean stop it closes the
listeners and removes `page.json` before it lets go of the lock.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
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
    guard,
    prove,
    remove_runtime,
    serve,
    write_runtime,
)

from . import cli, config, listen
from .listen import Listeners
from .proxy import Proxy
from .router import ASGIApp, Router, shell_profile, shell_table, worker_layer
from .session import TICKETS_DIR, OwnerCheck, Sessions, Tickets, open_store
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
    Without a `proxy`, `/m/<module>/` goes to the proxy over those starters.
    """

    def __init__(self, listeners: Listeners, *, epoch: str | None,
                 layout: Layout | None = None, owner: OwnerCheck | None = None,
                 modules: Sequence[str] = (), mounts: Mapping[str, Mapping[str, Any]] | None = None,
                 page: Page | None = None, push: ASGIApp | None = None,
                 proxy: ASGIApp | None = None, starters: Starters | None = None,
                 child_args: Sequence[str] = ()):
        self.listeners = listeners
        self.epoch = epoch
        self.layout = layout or Layout(MODULE)
        self.info: RuntimeInfo | None = None
        store = open_store(self.layout.state_dir(), epoch)
        tickets = Tickets(self.layout.runtime_dir() / TICKETS_DIR)
        self.sessions = Sessions(store, tickets, owner=owner or OwnerCheck.here(),
                                 token=lambda: self.info.token if self.info else None)
        self._modules = tuple(modules)
        self._mounts = dict(mounts or {})
        self._page = page or Page(routes=())
        self.starters = starters or Starters(
            runtime_dir=self.layout.runtime_dir(), state_dir=self.layout.state_dir(),
            log=cli.log_path(), child_args=child_args)
        self._push = push
        self._proxy = proxy if proxy is not None else Proxy(
            self.starters, table=lambda: self.profile.table)
        self.router: Router | None = None
        self.asgi: ASGIApp | None = None
        self.profile = None
        self._should_exit: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> RuntimeInfo:
        """Write the runtime file and build the request path. Returns the runtime info."""
        self.info = write_runtime(MODULE, transport="tcp", address="127.0.0.1",
                                  port=self.listeners.port, version=version())
        page = app(self._page, module=MODULE, server_id=self.info.server_id, prefix="/m/ui",
                   version=self.info.version)
        self.router = Router(self.sessions, page=page, push=self._push, proxy=self._proxy)
        self.set_table(shell_table(self._modules, self._mounts))
        self.asgi = worker_layer(guard(
            prove(self.router, token=self.info.token, server_id=self.info.server_id),
            lambda: self.profile))
        return self.info

    def set_table(self, table: Table) -> None:
        """Check every request from the next one on against `table`, which this freezes."""
        self.profile = shell_profile(table, self.sessions, self.listeners.names)

    async def run(self) -> None:
        """Serve until `stop()`, a signal, or the server's own end."""
        self._loop = asyncio.get_running_loop()
        self._should_exit = asyncio.Event()
        _log.info("serving on 127.0.0.1:%d (%s)", self.listeners.port,
                  ", ".join(sorted(self.listeners.names)))
        try:
            await serve(self.asgi, self.listeners.sockets, should_exit=self._should_exit,
                        head_limit=HEAD_LIMIT)
        finally:
            await self.starters.aclose()

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
    shell = Shell(listeners, epoch=epoch_for(listeners), layout=layout)
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
