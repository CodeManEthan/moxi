"""Serving a page: `serve`, and the page-server runner `python -P -m moxi_loopback.serve`.

`serve(app, sockets, *, should_exit, ...)` runs uvicorn in h11 mode on sockets the caller
bound, inside the caller's `moxi.aio.run`, until `should_exit` is set. The UI shell serves
its own listener with it; the runner serves a module's page with it.

The runner, `python -P -m moxi_loopback.serve <module> [--tcp] [--idle-s SECONDS]
[--no-lifeline]`, is the one way a module's page server starts:

1. It refuses to run without `python -P`, before it imports anything outside the standard
   library, so a planted `uvicorn/` or `moxi/` in a shared directory is never loaded.
2. It creates the module's runtime directory, private, and makes it its working
   directory. It loads the module's `PAGE` and resolves its handlers; a page it can't
   serve exits 3 before anything is written.
3. It takes `<runtime_dir>/page.lock` for its whole life (a wait past 3 s exits 75: another
   server is live), binds its socket (a Unix socket, or TCP on `127.0.0.1` with `--tcp` and
   on Windows), writes `page.json` with a fresh token and `server_id`, prints
   `ready <server_id>` on stdout, and from then on sends stdout to stderr.
4. It serves `guard(prove(app(...)))` until it decides to stop: when the counted work
   has been idle for the page's `idle_s` (or `--idle-s`), when stdin, its lifeline, closes,
   or on SIGTERM, SIGINT or SIGBREAK.

The stop is bounded and nothing a client sends stretches it. From the decision, a proof
is answered 503 and the listener is closed. The only request still taken is the next one
on a connection whose proof was answered within the last 0.5 s, and it is answered with
`Connection: close`; any other connection with no request in the app is closed. Every
connection still open 1.5 s after the decision is cut and its handler cancelled. Then the
runner removes `page.json` (only if it is still its own) and the socket, lets go of the
lock and exits 0, without waiting for a handler thread it abandoned. A watchdog thread
ends the process with exit 1 by 2.5 s after the decision whatever the event loop is
doing, which is inside the 3 s a new server waits for the lock. Every window and deadline
is on `time.monotonic()`.

Exit codes: 0 for a stop; 1 for an error, or a stop the watchdog had to end; 2 for usage; 3
when refused (no `-P`, a runtime directory that is not private, a page that can't be
served); 75 when another server holds the lock.
"""

from __future__ import annotations

import sys

if __name__ == "__main__" and not sys.flags.safe_path:
    sys.stderr.write("moxi_loopback.serve must be started with python -P, which keeps the "
                     "working directory off the import path\n")
    sys.exit(3)

import asyncio  # noqa: E402 - imported after the check above, on purpose
import contextlib  # noqa: E402
import logging  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections.abc import Awaitable, Callable, Iterable  # noqa: E402
from typing import Any  # noqa: E402

# Only the names `moxi_loopback.api` re-exports are public; the rest are the package's own.
__all__ = [
    "serve",
]

#: Seconds after a proof's answer within which the next request on that connection is
#: served, even once the stop is decided.
PROMISE = 0.5
#: Seconds from the stop decision by which every connection is closed and every handler
#: task cancelled.
CLOSE_DEADLINE = 1.5
#: Seconds from the stop decision by which the process has ended.
EXIT_DEADLINE = 2.5
#: Seconds a starting runner waits for another server's lock before it exits 75.
LOCK_WAIT = 3.0
#: uvicorn's own connection cap, a backstop behind the base's request and stream caps.
CONNECTION_CAP = 128

#: Seconds uvicorn's own shutdown waits for open work; it bounds only `serve` used alone.
_GRACEFUL = 2
#: The watchdog fires this long before the exit deadline, which leaves it time to log.
_LAST_WORDS = 0.1
#: How often the stop looks at its connections while it drains.
_SWEEP = 0.01
#: The longest the idle timer sleeps between two looks.
_IDLE_LOOK = 0.5
#: The thread pool and slow-callback threshold the runner's loop runs with.
_WORKERS = 4
_SLOW = 0.5

_SIGNALS = ("SIGTERM", "SIGINT", "SIGBREAK")
_CLOSE = (b"connection", b"close")


# --- serve --------------------------------------------------------------------------


async def serve(app: Callable[..., Any], sockets: Iterable[Any], *,
                should_exit: asyncio.Event,
                drain: Callable[[], Awaitable[None]] | None = None,
                head_limit: int | None = None) -> None:
    """Serve the ASGI `app` on the bound, listening `sockets` until `should_exit` is set.

    It runs uvicorn in h11 mode (no WebSockets, no lifespan, no proxy headers, no
    `Server` or `Date` header, no access log, at most 128 connections) inside the running
    loop, which should be `moxi.aio.run`'s. SIGTERM, SIGINT and SIGBREAK (Windows) set
    `should_exit`, and the handlers that were installed before are put back when `serve`
    returns; no signal is raised again, so a signal never ends the process before the
    caller's own stop. Signals are left alone when `serve` runs off the main thread.

    Every TCP connection it takes has `TCP_NODELAY` on, whatever made its listener.

    Once `should_exit` is set, `serve` awaits `drain()` if given, then tells uvicorn to
    stop, and returns when it has. uvicorn's own shutdown waits up to 2 s for open
    requests and then cancels them.

    `head_limit` is the most bytes of an unfinished request head a connection buffers
    before the request is refused with a 400. None, the default, keeps h11's 16 KiB. A
    raised limit costs up to that many bytes of memory for every connection holding an
    unfinished head, and up to 128 connections may: 4 MiB is up to 512 MiB.
    """
    await _serve(app, sockets, should_exit=should_exit, drain=drain, head_limit=head_limit)


async def _serve(app: Callable[..., Any], sockets: Iterable[Any], *,
                 should_exit: asyncio.Event,
                 drain: Callable[[], Awaitable[None]] | None = None,
                 head_limit: int | None = None, http: Any = "h11",
                 on_signal: Callable[[str], None] | None = None,
                 on_server: Callable[[Any], None] | None = None) -> None:
    """`serve`, with what the runner adds: its own protocol class as `http`, its own
    signal callback, and `on_server(server)`, called with the uvicorn server before it
    starts."""
    if head_limit is not None and (isinstance(head_limit, bool) or not isinstance(head_limit, int)
                                   or head_limit < 1):
        raise ValueError(f"head_limit is a number of bytes above 0, not {head_limit!r}")
    if isinstance(http, str) and http != "h11":
        raise ValueError(f"http is \"h11\" or a protocol class, not {http!r}")
    import uvicorn

    if http == "h11":
        from uvicorn.protocols.http.h11_impl import H11Protocol

        http = H11Protocol
    options: dict[str, Any] = dict(
        http=_no_delay(http), ws="none", lifespan="off", loop="asyncio", interface="asgi3",
        proxy_headers=False, server_header=False, date_header=False, log_config=None,
        access_log=False, timeout_graceful_shutdown=_GRACEFUL,
        limit_concurrency=CONNECTION_CAP,
    )
    if head_limit is not None:
        options["h11_max_incomplete_event_size"] = head_limit
    server = _server_class()(uvicorn.Config(app, **options))
    if on_server is not None:
        on_server(server)
    loop = asyncio.get_running_loop()
    if on_signal is None:
        def on_signal(name: str) -> None:
            with contextlib.suppress(RuntimeError):  # The loop has closed.
                loop.call_soon_threadsafe(should_exit.set)
    saved = _install_signals(on_signal)
    try:
        serving = loop.create_task(server.serve(sockets=list(sockets)))
        waiting = loop.create_task(should_exit.wait())
        try:
            done, _ = await asyncio.wait({serving, waiting},
                                         return_when=asyncio.FIRST_COMPLETED)
            if serving not in done and drain is not None:
                await drain()
        except BaseException:
            server.should_exit = server.force_exit = True
            serving.cancel()
            raise
        finally:
            waiting.cancel()
            server.should_exit = True
        await serving
    finally:
        _restore_signals(saved)


def _no_delay(protocol: type) -> type:
    """`protocol`, turning `TCP_NODELAY` on for each TCP connection it takes.

    asyncio turns it on only when the listener was made with `IPPROTO_TCP`: a socket
    made with protocol 0, as an inherited socket may be on macOS, and every connection
    the Windows proactor accepts, keep Nagle's algorithm on. A response written in two
    pieces then waits on the client's delayed ACK, about 40 ms a request.
    """

    class NoDelay(protocol):  # type: ignore[misc, valid-type]
        def connection_made(self, transport: Any) -> None:
            sock = transport.get_extra_info("socket")
            if sock is not None and sock.family in (socket.AF_INET, socket.AF_INET6):
                # A connection already reset refuses the option; it fails its own way.
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            super().connection_made(transport)

    return NoDelay


def _server_class() -> type:
    import uvicorn

    class Server(uvicorn.Server):
        """uvicorn's server without its own signal handling.

        uvicorn's `capture_signals` raises a caught signal again once `serve()` ends,
        which would end the process before the caller's stop; `serve` installs its own.
        """

        @contextlib.contextmanager
        def capture_signals(self) -> Any:
            yield

    return Server


def _install_signals(callback: Callable[[str], None]) -> dict[int, Any]:
    """Point SIGTERM, SIGINT and SIGBREAK at `callback(name)`; what was there before."""
    if threading.current_thread() is not threading.main_thread():
        return {}
    saved = {}
    for name in _SIGNALS:
        number = getattr(signal, name, None)
        if number is not None:
            saved[number] = signal.signal(number, lambda signum, frame, name=name: callback(name))
    return saved


def _restore_signals(saved: dict[int, Any]) -> None:
    for number, handler in saved.items():
        signal.signal(number, handler)


# --- the stop -----------------------------------------------------------------------


class _Stop:
    """The runner's one stop.

    `request(why)` is the decision. It may come from the loop (the idle timer), the
    lifeline thread or a signal handler, and only the first call counts: it notes the
    time, sets the flag that makes `/api/prove` answer 503 and arms the watchdog, and
    schedules the rest on the loop with `call_soon_threadsafe`. Later calls change
    nothing, so a second trigger leaves the clock alone. The watchdog is a thread started
    with the runner; once armed it ends the process at the exit deadline whatever the
    loop is doing, so a blocked loop can't hold the stop, and a signal handler never has
    to start a thread.

    On the loop, `_decide` closes the listeners and every connection with no request in
    the app and no live promise, then sets `should_exit`. `drain` keeps doing that until
    no connection is left or the close deadline comes, then cuts what is left.
    """

    def __init__(self, log: logging.Logger, *, end: Callable[[int], Any] = os._exit,
                 exit_deadline: float = EXIT_DEADLINE) -> None:
        self.log = log
        self.flag = threading.Event()
        self.decided_at: float | None = None
        self.why = ""
        self.connections: set[Any] = set()
        self.activity: Any = None
        self.server: Any = None
        self._once = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._should_exit: asyncio.Event | None = None
        self._decided = False
        self._end = end
        self._exit_deadline = exit_deadline
        threading.Thread(target=self._watch, name="moxi-loopback-watchdog",
                         daemon=True).start()

    def decided(self) -> bool:
        return self.flag.is_set()

    def request(self, why: str) -> None:
        """Decide to stop, from any thread or a signal handler. Only the first call counts."""
        # Never released: a later call returns here, and a signal that lands while this
        # thread is inside the first call can't wait on it.
        if not self._once.acquire(blocking=False):
            return
        self.decided_at = time.monotonic()
        self.why = why
        self.flag.set()
        loop = self._loop
        if loop is not None:
            self._schedule(loop)

    def attach(self, loop: asyncio.AbstractEventLoop, should_exit: asyncio.Event) -> None:
        """Give the stop its loop, once the loop runs; a decision made before is taken now."""
        self._should_exit = should_exit
        self._loop = loop
        if self.flag.is_set():
            self._schedule(loop)

    def _schedule(self, loop: asyncio.AbstractEventLoop) -> None:
        with contextlib.suppress(RuntimeError):  # The loop has closed: the process is ending.
            loop.call_soon_threadsafe(self._decide)

    def _decide(self) -> None:
        if self._decided:
            return
        self._decided = True
        self.log.info("stopping: %s", self.why)
        for listener in getattr(self.server, "servers", ()):
            listener.close()
        self._sweep(time.monotonic())
        if self._should_exit is not None:
            self._should_exit.set()

    def _sweep(self, now: float) -> None:
        for connection in list(self.connections):
            if connection.in_app():
                # The response that is running closes its connection when it ends.
                if connection.cycle is not None:
                    connection.cycle.keep_alive = False
            elif not connection.promised(now):
                connection.close()

    async def drain(self) -> None:
        """Close connections as their promises lapse and their requests end, until none is
        left or the close deadline comes; then cut every connection and handler left."""
        assert self.decided_at is not None
        self._decide()
        deadline = self.decided_at + CLOSE_DEADLINE
        while self.connections:
            now = time.monotonic()
            if now >= deadline:
                break
            self._sweep(now)
            await asyncio.sleep(min(_SWEEP, deadline - now))
        for connection in list(self.connections):
            connection.abort()
        tasks = getattr(getattr(self.server, "server_state", None), "tasks", ())
        for task in list(tasks):
            task.cancel()

    def _watch(self) -> None:
        self.flag.wait()
        assert self.decided_at is not None
        left = self.decided_at + self._exit_deadline - _LAST_WORDS - time.monotonic()
        if left > 0:
            time.sleep(left)
        _last_words(self.log, "the stop passed its exit deadline; the process ends here")
        self._end(1)


def _last_words(log: logging.Logger, text: str) -> None:
    """Log `text`, waiting at most a moment: a log handler stuck on a full pipe must not
    hold the process past its exit deadline."""
    writer = threading.Thread(target=log.error, args=(text,), daemon=True)
    writer.start()
    writer.join(_LAST_WORDS / 2)


def _protocol_class(stop: _Stop) -> type:
    """uvicorn's h11 protocol, with the per-connection state the stop reads.

    Each connection knows when its proof was answered (cleared by its next request) and
    whether a request on it is in the app. It counts a request for the idle exit in the
    step that hands it to the app. Once the stop is decided, it hands the app only the
    first request after a proof answered within the last `PROMISE` seconds, and closes
    the connection of any other with no answer. This is uvicorn's internal class, not its
    API; the stop tests pin it.
    """
    import h11
    from uvicorn.protocols.http.flow_control import HIGH_WATER_LIMIT, service_unavailable
    from uvicorn.protocols.http.h11_impl import H11Protocol, RequestResponseCycle

    from moxi_loopback.routes import PROVE_PATH
    from moxi_loopback.server import settle

    prove_raw = PROVE_PATH.encode("ascii")

    class StopProtocol(H11Protocol):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.proved_at: float | None = None
            self.task: asyncio.Task[None] | None = None

        def connection_made(self, transport: Any) -> None:
            super().connection_made(transport)
            stop.connections.add(self)
            if stop.decided():
                # Accepted after the decision, so it holds no promise.
                self.close()

        def connection_lost(self, exc: Exception | None) -> None:
            stop.connections.discard(self)
            super().connection_lost(exc)

        def in_app(self) -> bool:
            return self.task is not None and not self.task.done()

        def promised(self, now: float) -> bool:
            return self.proved_at is not None and now - self.proved_at <= PROMISE

        def close(self) -> None:
            if not self.transport.is_closing():
                self.transport.close()

        def abort(self) -> None:
            self.transport.abort()

        def handle_events(self) -> None:
            # uvicorn's loop over h11's events, with the hand-off of a request replaced.
            while True:
                try:
                    event = self.conn.next_event()
                except h11.RemoteProtocolError:
                    message = "Invalid HTTP request received."
                    self.logger.warning(message)
                    self.send_400_response(message)
                    return
                if event is h11.NEED_DATA:
                    break
                if event is h11.PAUSED:
                    self.flow.pause_reading()
                    break
                if isinstance(event, h11.Request):
                    self._unset_keepalive_if_required()
                    if not self._admit():
                        self.close()
                        return
                    self._hand_off(event)
                elif isinstance(event, h11.Data):
                    if self.conn.our_state is h11.DONE:
                        continue
                    self.cycle.body += event.data
                    if len(self.cycle.body) > HIGH_WATER_LIMIT:
                        self.flow.pause_reading()
                    self.cycle.message_event.set()
                elif isinstance(event, h11.EndOfMessage):
                    if self.conn.our_state is h11.DONE:
                        self.transport.resume_reading()
                        self.conn.start_next_cycle()
                        continue
                    self.cycle.more_body = False
                    self.cycle.message_event.set()
                    if self.conn.their_state == h11.MUST_CLOSE:
                        break

        def _admit(self) -> bool:
            """Whether a request whose head is complete goes to the app. Before the
            decision every one does; after it, only the first since a live proof."""
            proved, self.proved_at = self.proved_at, None
            if not stop.decided():
                return True
            return proved is not None and time.monotonic() - proved <= PROMISE

        def _hand_off(self, event: Any) -> None:
            from urllib.parse import unquote

            self.headers = [(name.lower(), value) for name, value in event.headers]
            raw_path, _, query_string = event.target.partition(b"?")
            self.scope = {  # type: ignore[typeddict-item]
                "type": "http",
                "asgi": {"version": self.asgi_version, "spec_version": "2.3"},
                "http_version": event.http_version.decode("ascii"),
                "server": self.server,
                "client": self.client,
                "scheme": self.scheme,
                "method": event.method.decode("ascii"),
                "root_path": self.root_path,
                "path": self.root_path + unquote(raw_path.decode("ascii")),
                "raw_path": self.root_path.encode("ascii") + raw_path,
                "query_string": query_string,
                "headers": self.headers,
                "state": self.app_state.copy(),
            }
            if self.limit_concurrency is not None and (
                    len(self.connections) >= self.limit_concurrency
                    or len(self.tasks) >= self.limit_concurrency):
                self.logger.warning("Exceeded concurrency limit.")
                app: Any = service_unavailable
            else:
                app = self._run_app
            self.cycle = RequestResponseCycle(
                scope=self.scope, conn=self.conn, transport=self.transport, flow=self.flow,
                logger=self.logger, access_logger=self.access_logger,
                access_log=self.access_log, default_headers=self.server_state.default_headers,
                message_event=asyncio.Event(), on_response=self.on_response_complete,
            )
            if stop.decided():
                self.cycle.keep_alive = False
            scope = self.scope
            # Counted in this step, so an idle look never falls between the hand-off and
            # the count; settled by `guard` in the request's first step, or here if the
            # task ends without one.
            stop.activity.hand_off(scope)
            task = self.loop.create_task(self.cycle.run_asgi(app))
            task.add_done_callback(lambda _: settle(scope))
            task.add_done_callback(self.tasks.discard)
            self.tasks.add(task)
            self.task = task

        async def _run_app(self, scope: Any, receive: Any, send: Any) -> None:
            proof = scope["method"] == "GET" and scope["raw_path"] == prove_raw

            async def sending(message: Any) -> None:
                if message["type"] == "http.response.start":
                    if stop.decided():
                        headers = list(message.get("headers") or ())
                        if not any(name.lower() == b"connection" for name, _ in headers):
                            message = {**message, "headers": [*headers, _CLOSE]}
                    elif proof and message["status"] == 204:
                        self.proved_at = time.monotonic()
                await send(message)

            await self.app(scope, receive, sending)

    return StopProtocol


async def _idle(activity: Any, idle_s: float, stop: _Stop) -> None:
    """Decide to stop once the counted work has been idle for `idle_s` seconds."""
    look = min(_IDLE_LOOK, idle_s / 10)
    while not stop.decided():
        quiet = activity.quiet()
        if quiet is not None and quiet >= idle_s:
            stop.request(f"idle for {idle_s:g} s")
            return
        await asyncio.sleep(look if quiet is None else max(idle_s - quiet, 0.001))


# --- the runner ---------------------------------------------------------------------


def _seconds(text: str) -> float:
    import argparse

    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None
    if not (value > 0 and math.isfinite(value)):
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds above 0")
    return value


def _arguments(argv: list[str] | None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -P -m moxi_loopback.serve",
        description="Serve a module's page until it is idle, its lifeline closes or a "
                    "signal stops it.")
    parser.add_argument("module", help="the module whose PAGE to serve")
    parser.add_argument("--tcp", action="store_true",
                        help="listen on TCP 127.0.0.1 instead of a Unix socket")
    parser.add_argument("--idle-s", type=_seconds, metavar="SECONDS",
                        help="stop after this many idle seconds instead of the page's own")
    parser.add_argument("--no-lifeline", action="store_true",
                        help="don't stop when stdin closes (for a run by hand)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the page server for the arguments `argv` (the process's own without one).

    It returns the exit code of a start that was refused; a server that started ends the
    process itself, with `os._exit`, once its stop is done.
    """
    args = _arguments(argv)
    from moxi import exit as codes
    from moxi import names

    if not names.is_module_name(args.module):
        sys.stderr.write(f"{args.module!r} is not a module name\n")
        return codes.USAGE
    import moxi.log

    moxi.log.setup(args.module)
    log = logging.getLogger(f"moxi.{args.module}.page")
    stop = _Stop(log)
    _install_signals(stop.request)
    try:
        return _start(args, stop, log)
    except Exception:
        log.exception("the page server failed to start")
        return codes.ERROR


def _start(args: Any, stop: _Stop, log: logging.Logger) -> int:
    from moxi import exit as codes
    from moxi import files, modules, proc
    from moxi.paths import Layout

    from moxi_loopback.check import upstream
    from moxi_loopback.export import PageLoadError, load_page
    from moxi_loopback.routes import PageError
    from moxi_loopback.runtime import remove_runtime, write_runtime
    from moxi_loopback.server import app, guard, prove
    from moxi_loopback.transport import NotPrivate, bind_tcp, bind_unix

    module = args.module
    try:
        runtime = Layout(module).runtime_dir(create=True)
    except OSError as err:
        log.error("the runtime directory is refused: %s", err)
        return codes.REFUSED
    os.chdir(runtime)

    try:
        page = load_page(module)
        version = modules.load(module).version
        if not version:
            # The runtime file carries the version, and it is refused there too late:
            # after the lock and the bind.
            raise PageLoadError(f"{module}'s distribution has no version")
        # The server_id is minted with the runtime file, under the lock; the page is
        # loaded and its handlers resolved before that, so a refused page writes nothing.
        inner = app(page, module=module, server_id="starting", version=version)
    except (PageLoadError, PageError) as err:
        log.error("the page of %s is refused: %s", module, " ".join(str(err).split()))
        return codes.REFUSED
    idle_s = args.idle_s if args.idle_s is not None else float(page.idle_s)

    held = contextlib.ExitStack()
    try:
        held.enter_context(files.lock(runtime / "page.lock", timeout=LOCK_WAIT))
    except files.LockTimeout as err:
        log.warning("another page server holds the lock: %s", err)
        return codes.TEMPFAIL

    tcp = args.tcp or sys.platform == "win32"
    try:
        if tcp:
            sock = bind_tcp(0)
            transport, address, port = "tcp", "127.0.0.1", sock.getsockname()[1]
        else:
            from moxi.paths import socket_path

            address = socket_path(module, "page")
            sock = bind_unix(address)
            transport, port = "unix", None
    except NotPrivate as err:
        held.close()
        log.error("the socket's directory is refused: %s", err)
        return codes.REFUSED
    except BaseException:
        held.close()
        raise

    def finish(code: int) -> None:
        """The stop's last step: remove our files, let go of the lock and end."""
        try:
            sock.close()
            if remove_runtime(module, info.server_id) and transport == "unix":
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(address)
        except Exception:
            log.exception("removing the runtime file failed")
            code = codes.ERROR
        held.close()
        log.info("stopped")
        _flush()
        os._exit(code)

    try:
        info = write_runtime(module, transport=transport, address=address, port=port,
                             version=version)
    except BaseException:
        sock.close()
        if transport == "unix":
            with contextlib.suppress(OSError):
                os.unlink(address)
        held.close()
        raise

    inner.server_id = info.server_id
    profile = upstream(inner.table, token=info.token, port=port)
    asgi = guard(prove(inner, token=info.token, server_id=info.server_id,
                       stopping=stop.flag.is_set),
                 lambda: profile)

    try:
        sys.stdout.write(f"ready {info.server_id}\n")
        sys.stdout.flush()
        # From here on a print() anywhere goes to the log; no pipe nobody reads stays on
        # this process's output.
        os.dup2(2, 1)
        sys.stdout = sys.stderr
    except OSError as err:
        log.error("the ready line could not be written: %s", err)
        finish(codes.ERROR)

    if not args.no_lifeline:
        proc.lifeline(on_close=lambda: stop.request("the lifeline closed"))
    log.info("serving %s on %s, idle after %g s", module, transport, idle_s)

    from moxi import aio

    async def run() -> None:
        loop = asyncio.get_running_loop()
        should_exit = asyncio.Event()
        stop.activity = inner.activity
        stop.attach(loop, should_exit)
        inner.activity.restart()
        idle = loop.create_task(_idle(inner.activity, idle_s, stop))
        code = codes.OK
        try:
            await _serve(asgi, [sock], should_exit=should_exit, drain=stop.drain,
                         http=_protocol_class(stop), on_signal=stop.request,
                         on_server=lambda server: setattr(stop, "server", server))
            if not stop.decided():
                log.error("the server stopped on its own")
                code = codes.ERROR
        except Exception:
            log.exception("the page server failed")
            code = codes.ERROR
        finally:
            idle.cancel()
        # Ended here, inside the loop, so no executor shutdown waits on a handler thread
        # the stop abandoned.
        finish(code)

    aio.run(run, workers=_WORKERS, slow=_SLOW)
    finish(codes.ERROR)  # Not reached: run() always ends the process.
    return codes.ERROR


def _flush() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    logging.shutdown()


if __name__ == "__main__":
    sys.exit(main())
