"""`moxi ui serve`, also `python -P -m moxi_ui.serve` and `moxi-ui-serve`: the shell.

It runs under `moxi.aio.run(workers=4, slow=0.5)` and:

1. creates its runtime dir and holds `<runtime_dir>/page.lock` for its whole life, taken
   before anything else (a shell of this user is running exactly while the lock is
   held); a 3 s wait that runs out exits 75;
2. takes its listeners: the sockets a socket unit passed it (systemd's `LISTEN_FDS`, or
   launchd's `Listeners` keys), else its own, bound on its port;
3. reads its epoch and opens its session store for it: on sockets systemd passed, the
   socket units' activation ids from `units.status`, when every unit is active and the
   passed names are exactly those units; else none, and its sessions live in memory;
4. writes its runtime file, `page.json`, with loopback's `write_runtime` (format 1, TCP);
5. serves `worker_layer(guard(prove(router), profile))` on the listeners with loopback's
   `serve` and a 4 MiB head limit, so every request head Chromium or Firefox can send,
   a worker's update check under a full cookie jar included, reaches the worker layer;
6. reads its module snapshot (`moxi modules --json` and the page exports, in fresh
   processes on a worker thread) and serves the table it gives from the next request
   on, reading it again when a module directory changes.

The shell's own page (`moxi_ui.page`) is served in process at `/m/ui/`, its export
mounted there in the table like any module's.

It also runs the gateway (`moxi_ui.gateway`), the listener a tunnel forwards a phone to,
in the same process and on the same loop; the shell's stop closes it, and a shell that
holds the gateway's endpoint never exits idle.

A shell on inherited sockets exits 0 once it has been idle for `idle_s` (no open request
or stream and no page server running), when that loses no session: it has an epoch, so
its sessions are on disk, or it holds none. The socket keeps the port, and the next
connection starts a shell again. A shell that bound its own listener never exits idle,
since nothing would hold the port after it.

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
from .gateway import Gateway
from .gateway.access import Access
from .proxy import Proxy
from .push import Push
from .router import ASGIApp, Router, shell_profile, shell_table, worker_layer
from .session import (
    HEALTH_PATH,
    PROVE_PATH,
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
#: The longest the idle timer sleeps between two looks.
IDLE_LOOK = 1.0

_log = logging.getLogger("moxi.ui")

#: The probes the idle exit doesn't count.
_NOT_COUNTED = frozenset((HEALTH_PATH, PROVE_PATH))
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


def _units_status() -> list[Any]:
    from moxi import units

    try:
        return list(units.status([MODULE]))
    except Exception:  # noqa: BLE001 - an unread supervisor gives no epoch
        _log.warning("the units could not be read; serving with no epoch", exc_info=True)
        return []


def epoch_for(listeners: Listeners, *, rows: Sequence[Any] | None = None) -> str | None:
    """The epoch of the hold the shell serves under, or None.

    A shell that bound its own listener has none: its hold is its own process, so its
    sessions live in its memory only. Nor has one on launchd's sockets, which carry no
    activation id. A shell on sockets systemd passed takes the socket state's `epoch`
    from `units.status` (`rows`, read here unless given): each socket unit's
    `InvocationID`, which systemd changes whenever the unit starts again. It takes it
    only when the socket state is `active` and the names systemd passed with the sockets
    are exactly the units the epoch names, so the sessions it keeps are tied to the very
    sockets it serves on.
    """
    if not listeners.inherited or not listeners.fd_names:
        return None
    sock = cli.socket_row(_units_status() if rows is None else rows)
    epoch = getattr(sock, "epoch", None) if sock is not None else None
    if sock is None or sock.state != "active" or not isinstance(epoch, str) or not epoch:
        return None
    units = [part.partition("=")[0] for part in epoch.split(",")]
    if len(set(units)) != len(units) or sorted(units) != sorted(listeners.fd_names):
        return None
    return epoch


def may_exit_idle(*, inherited: bool, epoch: str | None, live_sessions: int) -> bool:
    """Whether an idle shell may exit: only when exiting loses no session and leaves the
    port held. So only on an inherited listener (the socket keeps the port), and only
    with an epoch (its sessions are on disk) or with no live session."""
    return inherited and (epoch is not None or live_sessions == 0)


class Activity:
    """The shell's open requests, streams among them, for the idle exit. Health probes
    and proofs are not counted, so a probe never keeps an idle shell running."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.open = 0
        self._clock = clock
        self._quiet_since = clock()

    def quiet(self) -> float | None:
        """Seconds with no open request, or None while one is open."""
        return None if self.open else max(0.0, self._clock() - self._quiet_since)

    def wrap(self, inner: ASGIApp) -> ASGIApp:
        async def counted(scope: dict[str, Any], receive: Callable[..., Any],
                          send: Callable[..., Any]) -> None:
            if scope.get("type") != "http" or scope.get("path") in _NOT_COUNTED:
                await inner(scope, receive, send)
                return
            self.open += 1
            try:
                await inner(scope, receive, send)
            finally:
                self.open -= 1
                if not self.open:
                    self._quiet_since = self._clock()

        return counted


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

    `idle_s` is the idle exit's wait, None for none. The shell exits idle only on
    inherited listeners and only as `may_exit_idle` allows, so a bound listener never
    exits idle whatever `idle_s` says, and never while the gateway holds its endpoint.
    `idled` is True once it has.

    `gateway` is the shell's `Gateway`, or True to build one, with the gateway's sign-in
    state as `phone`; without it (the default, which tests take) the shell runs no
    gateway. `phone` is that sign-in state when a test passes its own gateway.
    """

    def __init__(self, listeners: Listeners, *, epoch: str | None,
                 layout: Layout | None = None, owner: OwnerCheck | None = None,
                 modules: Sequence[str] = (), mounts: Mapping[str, Mapping[str, Any]] | None = None,
                 page: Page | None = None, push: ASGIApp | None = None,
                 proxy: ASGIApp | None = None, starters: Starters | None = None,
                 child_args: Sequence[str] = (), snapshot: bool | ModuleSnapshot = False,
                 leave_out: Sequence[str] = (), idle_s: float | None = None,
                 gateway: Gateway | bool = False, phone: Access | None = None):
        self.listeners = listeners
        self.epoch = epoch
        self.idle_s = idle_s
        self.idled = False
        self.activity = Activity()
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
        self.phone = phone
        if gateway is True:
            self.phone = Access(
                state_dir=self.layout.state_dir(), runtime_dir=self.layout.runtime_dir(),
                kept=lambda: self.gateway.kept if self.gateway is not None else None)
            gateway = Gateway(self.layout, parts=self.phone.parts(), head_limit=HEAD_LIMIT)
        self.gateway: Gateway | None = gateway or None

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
        self.asgi = self.activity.wrap(worker_layer(inner))
        if self.gateway is not None:
            self.gateway.start()
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
        _log.info("serving on 127.0.0.1:%d (%s), %s, %s", self.listeners.port,
                  ", ".join(sorted(self.listeners.names)),
                  "on inherited sockets" if self.listeners.inherited else "on its own listener",
                  "with an epoch" if self.epoch else "sessions in memory")
        # Push streams never end on their own, so the stop ends them itself rather than
        # leave them to the server's shutdown deadline.
        ending = asyncio.ensure_future(self._end_push_at_stop())
        idling = (asyncio.ensure_future(self._idle_exit())
                  if self.idle_s is not None and self.listeners.inherited else None)
        if self.snapshot is not None:
            self.snapshot.start()
        gateway = (asyncio.ensure_future(self.gateway.run(self._should_exit))
                   if self.gateway is not None else None)
        try:
            await serve(self.asgi, self.listeners.sockets, should_exit=self._should_exit,
                        head_limit=HEAD_LIMIT)
        finally:
            # The shell's stop closes the gateway's listener with it.
            self._should_exit.set()
            if gateway is not None:
                await gateway
            ending.cancel()
            if idling is not None:
                idling.cancel()
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

    def idle(self) -> bool:
        """Whether the shell may exit idle now: `idle_s` with no open request or stream,
        no page server running, and `may_exit_idle`."""
        quiet = self.activity.quiet()
        if self.idle_s is None or quiet is None or quiet < self.idle_s:
            return False
        if self.starters.children():
            return False
        if self.gateway is not None and self.gateway.holding:
            return False
        return may_exit_idle(inherited=self.listeners.inherited, epoch=self.epoch,
                             live_sessions=self.sessions.store.live())

    async def _idle_exit(self) -> None:
        assert self._should_exit is not None and self.idle_s is not None
        look = min(IDLE_LOOK, self.idle_s / 10)
        while not self._should_exit.is_set():
            if self.idle():
                _log.info("idle for %g s; exiting, and the socket starts a shell at the "
                          "next connection", self.idle_s)
                self.idled = True
                self._should_exit.set()
                return
            await asyncio.sleep(look)

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


def run(*, bind: Callable[[Callable[[], int]], Listeners] = listen.take,
        say: Callable[[str], Any] | None = None) -> int:
    """Run the shell to its end and return its exit code: 0 for a stop or an idle exit,
    1 for an error (the port taken, a port setting it can't read when it binds its own, or
    passed sockets it can't serve, included), 75 when another shell holds the lock.
    `bind(port)` takes the listeners, calling `port()` for the setting only when it binds
    them itself."""
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


def _run_held(layout: Layout, bind: Callable[[Callable[[], int]], Listeners],
              say: Callable[[str], Any]) -> int:
    asked: list[int] = []

    def port() -> int:
        asked.append(config.port())
        return asked[-1]

    try:
        listeners = bind(port)
    except listen.PortInUse as err:
        say(f"moxi ui: {err}")
        return 1
    except listen.NotLoopback as err:
        say(f"moxi ui: the sockets passed to the shell can't be served: {err}; "
            "`moxi install ui` writes its socket units again")
        return 1
    except OSError as err:
        where = f" on port {asked[-1]}" if asked else ""
        say(f"moxi ui: the listener{where} could not be bound: {err}")
        return 1
    except Exception as err:  # noqa: BLE001 - a bad port setting is said and ends the start
        say(f"moxi ui: {err}")
        return 1
    idle_s = None
    if listeners.inherited:
        try:
            idle_s = config.idle_s()
        except Exception as err:  # noqa: BLE001 - a bad setting keeps the shell running
            say(f"moxi ui: {err}; the shell won't exit idle")
    shell = Shell(listeners, epoch=epoch_for(listeners), layout=layout, snapshot=True,
                  idle_s=idle_s, gateway=True)
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
