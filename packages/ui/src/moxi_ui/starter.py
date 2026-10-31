"""Starting page servers on demand: one starter per module, and the export run.

The shell holds one `Starters`. Each module with a page gets one `Starter`, in one of
five states: `idle`, `starting` (one start in flight, which every request that finds it
awaits, so two first requests make one process), `running` (a `Server`: the child, its
runtime info and its pooled connections), `draining`, or `failed` (for 10 s, with the
reason).

A start follows loopback's spawn contract:

1. `[python(), "-P", "-m", "moxi_loopback.serve", <module>, *child_args]`, spawned with a
   stdin lifeline, stdout and stderr pipes, and the shell's runtime dir as its working
   directory: a directory that exists before the spawn and only this user can write. (The
   module's own runtime dir is not used: only the child's runner creates it, so on a
   fresh install it does not exist yet.)
2. A thread per child copies its stderr into the shell's log as `moxi.ui.child.<module>`,
   keeping the last 50 lines; another reads stdout for the `ready <server_id>` line within
   10 s, then closes the shell's end of stdout.
3. `read_runtime(module)` must give the same `server_id`, tried every 20 ms until the same
   deadline.
4. Exit 75 means another server holds the module's lock: its runtime file is read and the
   server used once it passes the proof. If the proof fails that server was stopping, and
   the starter starts once more; a holder that is the shell's own child is stopped first,
   since it may be hung and would hold the lock until the shell stops.
5. Any other exit, or no ready line: the child and its descendants are killed and the
   starter is `failed` for 10 s, during which a request gets the same failure at once and
   nothing is started.

A child that exits 0 after `ready` stopped itself (its idle exit, or a lifeline close); one
that exits otherwise crashed. Either way the starter drops its runtime info and pooled
connections at once, so nothing dials a dead port, and goes `idle`: the next request
starts a new server. Every child exit is counted in `<state_dir>/servers.json`, which the
doctor reads while the shell is down.

No byte goes to a server, the token included, before the server proves on that same
connection that it holds the token of the runtime file the starter read: every
connection a `Server` hands out comes from loopback's `open_connection`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moxi import files, proc
from moxi.names import is_module_name
from moxi.paths import python
from moxi_loopback.api import NotOurServer, RuntimeInfo, Table, call, open_connection, read_runtime

#: Seconds from the spawn within which a child prints its ready line and its runtime file
#: reads with the same server id.
READY_WITHIN = 10.0
#: How often the runtime file is read again while it does not yet name the child.
RETRY_EVERY = 0.02
#: Seconds a failed start answers every request with its failure, starting nothing.
FAILED_FOR = 10.0
#: Lines of a child's stderr kept for `tail()`.
TAIL_LINES = 50
#: Idle kept-alive connections pooled per server.
POOL_MAX = 4
#: A proved connection takes its first request within this many seconds of the proof,
#: inside the half second a stopping server keeps for it, or is not used for an act.
SEND_WITHIN = 0.25
#: Seconds a drain waits for open requests to finish before it closes the lifeline.
DRAIN_WAIT = 30.0
#: Seconds the starter waits for a child to exit once its lifeline is closed, before it
#: kills it. A page server's own stop ends by 2.5 s.
STOP_WAIT = 3.0
#: Seconds at most the proof of a server that holds the lock may take.
HELD_PROOF_WAIT = 5.0
#: Seconds the `/servers` data waits for one child's health answer.
HEALTH_WAIT = 2.0
#: Threads that take the `/servers` data's samples and health calls. They are the
#: starters' own, not the shell's shared pool: the doctor calls `/servers` back while the
#: shell may be busy, and that call must not wait for the shell's other work.
SAMPLE_THREADS = 4
#: Seconds the export command may take.
EXPORT_TIMEOUT = 10.0
#: The page-server runner's exit code when another server holds the module's lock.
LOCK_HELD = 75
SERVERS_FILE = "servers.json"
SERVERS_FORMAT = 1

#: The variable that names the shell's log file. A child must not inherit it: its stderr
#: reaches the shell's log through the shell, and two processes never roll one file.
LOG_FILE_VARIABLE = "MOXI_LOG_FILE"

_READY = re.compile(rb"\Aready ([0-9a-f]{32})\n\Z")
_log = logging.getLogger("moxi.ui.starter")


class StartFailed(Exception):
    """A module's page server did not start.

    `exit` is the child's exit code, or None when it gave no ready line in time (it was
    killed). `log` names the log the child's stderr went to. `reason` is one line for the
    shell's own log and status; the browser's answer (`body()`) carries nothing from the
    child's stderr.
    """

    def __init__(self, module: str, reason: str, *, exit: int | None, log: str):
        super().__init__(f"{module}: {reason}")
        self.module = module
        self.reason = reason
        self.exit = exit
        self.log = log

    def body(self) -> dict[str, Any]:
        """The JSON body of the 502 the browser gets."""
        return {"error": "start", "module": self.module, "exit": self.exit, "log": self.log}


class ServerGone(ConnectionError):
    """The server is no longer the module's: its child exited, the shell dropped it, or it
    is draining. Nothing was sent to it."""


# --- connections ------------------------------------------------------------------------


@dataclass(eq=False)
class Conn:
    """One proved connection to a page server.

    `proved_at` is the `time.monotonic()` at which the proof matched. `state` is the
    caller's own (the proxy keeps its HTTP client state there across a pooled reuse).
    """

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    proved_at: float
    state: Any = None

    def fresh(self, now: float | None = None) -> bool:
        """Whether a request sent now falls inside the proof's promise, so an act may go
        on this connection."""
        now = time.monotonic() if now is None else now
        return now - self.proved_at <= SEND_WITHIN

    def closed(self) -> bool:
        return self.writer.is_closing() or self.reader.at_eof()

    def close(self) -> None:
        self.writer.close()


class Use:
    """One open request or stream on a server, counted while a drain waits.

    Made by `Server.use()`; ended by `end()` or by leaving its `with` block. A stream's
    `close` callback is called when the server drains, so the browser's stream ends and
    its client reconnects to the next server.
    """

    def __init__(self, server: Server, *, stream: bool, close: Callable[[], Any] | None):
        self.server = server
        self.stream = stream
        self._close = close
        self._ended = False

    def __enter__(self) -> Use:
        return self

    def __exit__(self, *exc: object) -> None:
        self.end()

    def end(self) -> None:
        if not self._ended:
            self._ended = True
            self.server._ended(self)

    def cut(self) -> None:
        if self._close is not None and not self._ended:
            try:
                self._close()
            except Exception:  # noqa: BLE001 - one stream's cut never stops a drain
                _log.exception("closing a stream to %s failed", self.server.module)


class Server:
    """A module's page server as the shell uses it: its runtime info, its child (None
    when the server is one the shell found running and did not start), and its pool of
    idle proved connections.

    `connect()` gives a newly proved connection; `take()` an idle pooled one, for a read;
    `give()` returns one to the pool. `use()` counts an open request or stream. Once the
    server is dropped (`gone`) or draining, `connect()` and `use()` raise `ServerGone`
    and the pool is empty.
    """

    def __init__(self, module: str, info: RuntimeInfo, child: Child | None, *,
                 started: float, handoff: Conn | None = None):
        self.module = module
        self.info = info
        self.child = child
        self.started = started
        self.gone = False
        self.draining = False
        self._handoff = handoff
        self._idle: deque[Conn] = deque()
        self._uses: set[Use] = set()
        self._quiet = asyncio.Event()
        self._quiet.set()

    def __repr__(self) -> str:
        state = "gone" if self.gone else "draining" if self.draining else "up"
        return f"<Server {self.module} {self.info.server_id} {state}>"

    @property
    def requests(self) -> int:
        return sum(1 for use in self._uses if not use.stream)

    @property
    def streams(self) -> int:
        return sum(1 for use in self._uses if use.stream)

    def _check(self) -> None:
        if self.gone or self.draining:
            raise ServerGone(f"{self.module}'s server {self.info.server_id} is "
                             f"{'gone' if self.gone else 'draining'}")

    def use(self, *, stream: bool = False, close: Callable[[], Any] | None = None) -> Use:
        """Count one open request (or, with `stream`, a stream) on this server until the
        returned `Use` ends. `close` is called if the server drains while it is open."""
        self._check()
        use = Use(self, stream=stream, close=close)
        self._uses.add(use)
        self._quiet.clear()
        return use

    def _ended(self, use: Use) -> None:
        self._uses.discard(use)
        if not self._uses:
            self._quiet.set()

    async def connect(self, *, timeout: float = 5.0) -> Conn:
        """A connection to this server, proved on itself before it is returned.

        Raises `ServerGone` when the server was dropped, and what loopback's
        `open_connection` raises: `NotOurServer` for a failed proof, `OSError` for a
        failed connect, `TimeoutError`. In each case nothing was sent but the proof's
        nonce, and the caller tells the starters with `lost(server)`.
        """
        self._check()
        handoff, self._handoff = self._handoff, None
        if handoff is not None:
            if handoff.fresh() and not handoff.closed():
                return handoff
            handoff.close()
        reader, writer = await open_connection(self.info, timeout=timeout)
        conn = Conn(reader, writer, time.monotonic())
        if self.gone:
            conn.close()
            raise ServerGone(f"{self.module}'s server {self.info.server_id} is gone")
        return conn

    def take(self) -> Conn | None:
        """An idle pooled connection, most recent first, or None. For a read only: an
        act always goes on a newly proved connection."""
        while self._idle:
            conn = self._idle.pop()
            if not conn.closed():
                return conn
            conn.close()
        return None

    def give(self, conn: Conn) -> None:
        """Keep `conn`, whose last answer ended and which stays open, for the next read;
        closed instead when the pool is full or the server is gone or draining."""
        if self.gone or self.draining or conn.closed() or len(self._idle) >= POOL_MAX:
            conn.close()
            return
        self._idle.append(conn)

    def _drop(self) -> None:
        self.gone = True
        while self._idle:
            self._idle.pop().close()
        if self._handoff is not None:
            self._handoff.close()
            self._handoff = None


# --- children ---------------------------------------------------------------------------


class Child:
    """One spawned page-server process and the threads that read it."""

    def __init__(self, module: str, popen: subprocess.Popen, loop: asyncio.AbstractEventLoop,
                 on_exit: Callable[[Child, int], None]):
        self.module = module
        self.popen = popen
        self.pid = popen.pid
        self.server_id: str | None = None
        self.server: Server | None = None
        self.stopping = False
        self.code: int | None = None
        self.tail: deque[str] = deque(maxlen=TAIL_LINES)
        self.ready: asyncio.Future[bytes] = loop.create_future()
        self.exited: asyncio.Future[int] = loop.create_future()
        self._loop = loop
        self._on_exit = on_exit
        self._log = logging.getLogger(f"moxi.ui.child.{module}")
        for target, name in ((self._read_stderr, "stderr"), (self._read_ready, "ready"),
                             (self._watch, "watch")):
            threading.Thread(target=target, daemon=True,
                             name=f"moxi-ui-{module}-{name}-{self.pid}").start()

    def _post(self, fn: Callable[..., Any], *args: Any) -> None:
        try:
            self._loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # The loop has closed: nobody is waiting any more.
            pass

    def _read_stderr(self) -> None:
        stream = self.popen.stderr
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                self.tail.append(line)
                self._log.info("%s", line)
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

    def _read_ready(self) -> None:
        stream = self.popen.stdout
        line = b""
        try:
            if stream is not None:
                line = stream.readline(256)
        except (OSError, ValueError):
            line = b""
        finally:
            if stream is not None:
                stream.close()
        self._post(_resolve, self.ready, line)

    def _watch(self) -> None:
        code = self.popen.wait()
        self._post(self._exited, code)

    def _exited(self, code: int) -> None:
        self.code = code
        _resolve(self.exited, code)
        self._on_exit(self, code)

    @property
    def alive(self) -> bool:
        return self.code is None

    def close_lifeline(self) -> None:
        """Tell the child to stop, as the shell's own end would."""
        self.stopping = True
        lifeline = getattr(self.popen, "lifeline", None)
        if lifeline is not None:
            try:
                lifeline.close()
            except OSError:
                pass

    def kill(self) -> None:
        """Kill the child and every process below it. (The child shares the shell's
        process group, so the group itself is never signalled.)"""
        self.stopping = True
        try:
            import psutil

            below = psutil.Process(self.pid).children(recursive=True)
        except Exception:  # noqa: BLE001 - psutil.Error, or the child is already gone
            below = []
        if self.popen.poll() is None:
            try:
                self.popen.kill()
            except OSError:
                pass
        for process in below:
            try:
                process.kill()  # psutil checks the pid still names the process it found
            except Exception:  # noqa: BLE001
                pass

    async def stop(self, wait: float = STOP_WAIT) -> None:
        """Close the lifeline and wait for the exit; kill the child past `wait`."""
        self.close_lifeline()
        try:
            await asyncio.wait_for(asyncio.shield(self.exited), wait)
        except TimeoutError:
            _log.warning("%s's page server did not stop within %g s; killing it",
                         self.module, wait)
            self.kill()
            await asyncio.shield(self.exited)


def _resolve(future: asyncio.Future, value: Any) -> None:
    if not future.done():
        future.set_result(value)


# --- the record -------------------------------------------------------------------------


def _now_text(wall: Callable[[], float]) -> str:
    return datetime.fromtimestamp(wall(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Record:
    """A module's counts, kept across shell restarts in `servers.json`."""

    starts: int = 0
    failed_starts: int = 0
    failed_in_row: int = 0
    crashes: int = 0
    last_exit: int | None = None
    last_exit_at: str | None = None
    last_crash_at: str | None = None
    reason: str | None = None

    FIELDS = ("starts", "failed_starts", "failed_in_row", "crashes", "last_exit",
              "last_exit_at", "last_crash_at", "reason")

    def data(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.FIELDS}

    @classmethod
    def from_data(cls, data: Any) -> Record:
        record = cls()
        if not isinstance(data, Mapping):
            return record
        for name in ("starts", "failed_starts", "failed_in_row", "crashes"):
            value = data.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                setattr(record, name, value)
        value = data.get("last_exit")
        if isinstance(value, int) and not isinstance(value, bool):
            record.last_exit = value
        for name in ("last_exit_at", "last_crash_at", "reason"):
            value = data.get(name)
            if isinstance(value, str):
                setattr(record, name, value[:500])
        return record


def read_servers(path: str | os.PathLike) -> dict[str, dict[str, Any]]:
    """The per-module records of a `servers.json`, as written; an empty mapping for a
    file that is missing or doesn't read. Never raises."""
    try:
        with open(path, "rb") as stream:
            data = json.loads(stream.read(1024 * 1024))
        modules = data.get("modules") if data.get("format") == SERVERS_FORMAT else None
        if not isinstance(modules, dict):
            return {}
        return {name: Record.from_data(value).data() for name, value in modules.items()
                if isinstance(name, str) and is_module_name(name)}
    except Exception:  # noqa: BLE001 - a missing or broken file reads as no record
        return {}


# --- one module's starter ---------------------------------------------------------------


class Starter:
    """The one starter of one module. See the module docstring for its states."""

    def __init__(self, module: str, pool: Starters, record: Record | None = None):
        self.module = module
        self.record = record or Record()
        self._pool = pool
        self._lock = asyncio.Lock()
        self._server: Server | None = None
        self._starting: asyncio.Task[Server] | None = None
        self._draining: asyncio.Task[None] | None = None
        self._failure: StartFailed | None = None
        self._failed_until = 0.0
        self._children: list[Child] = []

    @property
    def state(self) -> str:
        if self._draining is not None:
            return "draining"
        if self._starting is not None:
            return "starting"
        if self._server is not None:
            return "running"
        if self._failure is not None and self._pool.clock() < self._failed_until:
            return "failed"
        return "idle"

    @property
    def server(self) -> Server | None:
        """The running server, or None."""
        return self._server

    @property
    def children(self) -> list[Child]:
        """The children of this starter still running."""
        return [child for child in self._children if child.alive]

    def tail(self) -> list[str]:
        """The last lines of stderr of the most recent child."""
        return list(self._children[-1].tail) if self._children else []

    async def ensure(self) -> Server:
        """The module's running server, started first when there is none.

        A request that finds a start in flight awaits that start; one that finds a drain
        waits for it to end. Raises `StartFailed` for a failed start, and for 10 s after
        one without starting anything.
        """
        while True:
            async with self._lock:
                if self._draining is not None:
                    drain: asyncio.Task[None] | None = self._draining
                    start = None
                elif self._server is not None:
                    return self._server
                elif self._failure is not None and self._pool.clock() < self._failed_until:
                    raise self._failure
                else:
                    if self._starting is None:
                        if self._pool.closing:
                            raise ServerGone("the shell is stopping")
                        self._starting = asyncio.ensure_future(self._start())
                    drain, start = None, self._starting
            if start is not None:
                try:
                    return await asyncio.shield(start)
                except asyncio.CancelledError:
                    if start.cancelled():  # The shell stopped the start, not our caller.
                        raise ServerGone("the shell is stopping") from None
                    raise
            await asyncio.shield(drain)

    def lost(self, server: Server) -> None:
        """`server` failed a connect or a proof with nothing sent: forget it, so the next
        `ensure()` starts a new one. A child of it that still runs is left to stop on its
        own (a server that refuses connections is one that is stopping) and is still
        watched; if it turns out to hold the lock, the new start uses it again when it
        passes the proof, and stops it when it fails it."""
        if server is self._server:
            _log.info("%s's server %s is unreachable; the next request starts another",
                      self.module, server.info.server_id)
            self._drop()

    def drain(self) -> asyncio.Task[None] | None:
        """Drain the running server: new requests wait, its streams are closed, its open
        requests finish (for up to 30 s), then its lifeline is closed and the next request
        starts the new version. Returns the drain's task, or None when nothing runs."""
        if self._draining is not None:
            return self._draining
        server = self._server
        if server is None:
            return None
        server.draining = True
        _log.info("draining %s's server %s (version %s)", self.module,
                  server.info.server_id, server.info.version)
        for use in list(server._uses):
            if use.stream:
                use.cut()
        self._draining = asyncio.ensure_future(self._drain(server))
        return self._draining

    async def _drain(self, server: Server) -> None:
        try:
            try:
                await asyncio.wait_for(server._quiet.wait(), DRAIN_WAIT)
            except TimeoutError:
                _log.warning("%s: %d requests still open after %g s; stopping the server",
                             self.module, len(server._uses), DRAIN_WAIT)
            if server is self._server:
                self._drop()
            if server.child is not None and server.child.alive:
                await server.child.stop()
        finally:
            self._draining = None

    def _drop(self) -> None:
        if self._server is not None:
            self._server._drop()
            self._server = None

    # -- the start ---------------------------------------------------------------------

    async def _start(self) -> Server:
        try:
            for attempt in (1, 2):
                server = await self._one_start(retry=attempt == 1)
                if server is not None:
                    self._started(server)
                    return server
            raise AssertionError("not reached")  # pragma: no cover
        except StartFailed as failure:
            self._failed(failure)
            raise
        finally:
            self._starting = None

    async def _one_start(self, *, retry: bool) -> Server | None:
        """One spawn. Returns the server; None when another server held the lock and failed
        its proof and `retry` allows one more start; raises `StartFailed`."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + READY_WITHIN
        child = self._spawn(loop)
        try:
            line = await asyncio.wait_for(asyncio.shield(child.ready),
                                          max(deadline - loop.time(), 0))
        except TimeoutError:
            child.kill()
            raise self._failure_of(f"no ready line within {READY_WITHIN:g} s",
                                   await asyncio.shield(child.exited), killed=True) from None
        match = _READY.match(line)
        if match is None:
            if line:
                child.kill()
                raise self._failure_of("the ready line is malformed",
                                       await asyncio.shield(child.exited), killed=True)
            code = await self._exit_code(child, deadline)
            if code is None:
                raise self._failure_of("stdout closed with no ready line",
                                       await asyncio.shield(child.exited), killed=True)
            if code == LOCK_HELD:
                return await self._held(deadline, retry=retry)
            raise self._failure_of(f"the page server exited {code} at start", code)
        server_id = match.group(1).decode("ascii")
        child.server_id = server_id
        info = await self._runtime(server_id, deadline)
        if info is None:
            child.kill()
            raise self._failure_of("the runtime file does not name the server that "
                                   "said ready", await asyncio.shield(child.exited), killed=True)
        server = Server(self.module, info, child, started=self._pool.clock())
        child.server = server
        if not child.alive:
            # It printed ready and was gone before the shell looked: no server to use.
            server._drop()
            raise self._failure_of(f"the page server exited {child.code} after ready",
                                   child.code)
        return server

    def _spawn(self, loop: asyncio.AbstractEventLoop) -> Child:
        argv = self._pool.argv(self.module)
        self.record.starts += 1
        try:
            popen = self._pool.spawn(argv, lifeline="stdin", stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, cwd=str(self._pool.cwd),
                                     env=self._pool.child_env())
        except OSError as err:
            raise self._failure_of(f"the page server could not be started: {err}",
                                   None) from None
        child = Child(self.module, popen, loop, self._on_exit)
        # Kept: every child still running, and the newest, for its stderr's tail.
        self._children = [c for c in self._children if c.alive] + [child]
        return child

    async def _exit_code(self, child: Child, deadline: float) -> int | None:
        """The exit code of a child that closed stdout, or None (it was killed) when it
        doesn't exit by the deadline."""
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(asyncio.shield(child.exited),
                                          max(deadline - loop.time(), 0))
        except TimeoutError:
            child.kill()
            return None

    async def _runtime(self, server_id: str, deadline: float) -> RuntimeInfo | None:
        loop = asyncio.get_running_loop()
        while True:
            info = await asyncio.to_thread(read_runtime, self.module)
            if info is not None and info.server_id == server_id:
                return info
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(RETRY_EVERY)

    async def _held(self, deadline: float, *, retry: bool) -> Server | None:
        """Exit 75: use the server that holds the lock once it passes the proof. A child of
        the shell's own that holds it and fails the proof is stopped before the one more
        start."""
        loop = asyncio.get_running_loop()
        while True:
            info = await asyncio.to_thread(read_runtime, self.module)
            if info is not None or loop.time() >= deadline:
                break
            await asyncio.sleep(RETRY_EVERY)
        reason = "another server holds the lock and has no runtime file"
        if info is not None:
            mine = next((c for c in self._children
                         if c.alive and c.server_id == info.server_id), None)
            try:
                reader, writer = await open_connection(
                    info, timeout=max(min(HELD_PROOF_WAIT, deadline - loop.time()), 0.5))
            except (NotOurServer, OSError, TimeoutError) as err:
                reason = f"another server holds the lock and fails its proof ({err})"
                if mine is not None:
                    _log.warning("%s: %s, and it is the shell's own; stopping it",
                                 self.module, reason)
                    await mine.stop(STOP_WAIT)
            else:
                handoff = Conn(reader, writer, time.monotonic())
                server = Server(self.module, info, mine, started=self._pool.clock(),
                                handoff=handoff)
                if mine is not None:
                    mine.server = server
                _log.info("%s: using the server %s that holds the lock", self.module,
                          info.server_id)
                return server
        if retry:
            _log.info("%s: %s; starting once more", self.module, reason)
            return None
        raise StartFailed(self.module, reason, exit=LOCK_HELD, log=self._pool.log)

    def _failure_of(self, reason: str, code: int | None, *, killed: bool = False) -> StartFailed:
        # A child the starter killed reports no exit code of its own.
        return StartFailed(self.module, reason, exit=None if killed else code,
                           log=self._pool.log)

    def _started(self, server: Server) -> None:
        self._server = server
        self._failure = None
        self.record.failed_in_row = 0
        self.record.reason = None
        _log.info("%s's page server %s is up (version %s, %s)", self.module,
                  server.info.server_id, server.info.version, server.info.transport)

    def _failed(self, failure: StartFailed) -> None:
        self._failure = failure
        self._failed_until = self._pool.clock() + FAILED_FOR
        self.record.failed_starts += 1
        self.record.failed_in_row += 1
        self.record.reason = failure.reason
        _log.warning("%s's page server failed to start: %s", self.module, failure.reason)
        self._pool.save()

    def _on_exit(self, child: Child, code: int) -> None:
        record = self.record
        record.last_exit = code
        record.last_exit_at = _now_text(self._pool.wall)
        server = child.server
        if server is not None:
            if server is self._server:
                self._drop()
            else:
                server._drop()
            if code != 0 and not child.stopping and not self._pool.closing:
                record.crashes += 1
                record.last_crash_at = record.last_exit_at
                record.reason = f"the page server exited {code}"
                _log.warning("%s's page server %s crashed (exit %d)", self.module,
                             child.server_id, code)
            else:
                _log.info("%s's page server %s stopped (exit %d)", self.module,
                          child.server_id, code)
        self._pool.save()

    # -- the /servers data -------------------------------------------------------------

    def row(self) -> dict[str, Any]:
        """This module's row of the `/servers` data, without samples."""
        server = self._server
        failed = self.state == "failed"
        row: dict[str, Any] = {
            "module": self.module,
            "state": self.state,
            "server_id": server.info.server_id if server else None,
            "version": server.info.version if server else None,
            "transport": server.info.transport if server else None,
            "pid": server.info.pid if server else None,
            "child": bool(server and server.child is not None),
            "uptime_s": round(self._pool.clock() - server.started, 3) if server else None,
            "failed_for_s": (round(self._failed_until - self._pool.clock(), 3)
                             if failed else None),
            "log": self._pool.log,
            **self.record.data(),
        }
        return row


# --- every starter ----------------------------------------------------------------------


@dataclass
class Exports:
    """What the export command gave: each mountable export by module, and why each other
    module has none."""

    mounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class Starters:
    """The shell's starters, one per module, made on first use.

    `runtime_dir` is the shell's own runtime dir, every child's working directory;
    `state_dir` holds `servers.json`; `log` names the log a child's stderr goes to, as
    `moxi ui status` prints it. `child_args` are added to every page server's arguments
    (tests pass `("--idle-s", "0.5")` or `("--tcp",)`). `spawn`, `clock` and `wall` are
    seams for tests.
    """

    def __init__(self, *, runtime_dir: str | os.PathLike, state_dir: str | os.PathLike,
                 log: str | os.PathLike, child_args: Sequence[str] = (),
                 spawn: Callable[..., Any] = proc.spawn,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time):
        self.cwd = Path(runtime_dir)
        self.state_dir = Path(state_dir)
        self.log = str(log)
        self.child_args = tuple(child_args)
        self.spawn = spawn
        self.clock = clock
        self.wall = wall
        self.closing = False
        self._sampling: ThreadPoolExecutor | None = None
        self._starters: dict[str, Starter] = {}
        self._records = {name: Record.from_data(data)
                         for name, data in read_servers(self.path).items()}

    @property
    def path(self) -> Path:
        """`servers.json`."""
        return self.state_dir / SERVERS_FILE

    def argv(self, module: str) -> list[str]:
        """The page server's command line for `module`."""
        if not is_module_name(module):
            raise ValueError(f"{module!r} is not a module name")
        return [str(python()), "-P", "-m", "moxi_loopback.serve", module, *self.child_args]

    def child_env(self) -> dict[str, str]:
        """The shell's environment without the variable that names its log file."""
        return {key: value for key, value in os.environ.items() if key != LOG_FILE_VARIABLE}

    def get(self, module: str) -> Starter:
        """`module`'s starter, made on first use."""
        starter = self._starters.get(module)
        if starter is None:
            if not is_module_name(module):
                raise ValueError(f"{module!r} is not a module name")
            starter = Starter(module, self, self._records.pop(module, None))
            self._starters[module] = starter
        return starter

    async def ensure(self, module: str) -> Server:
        """`module`'s running server, started on demand (`Starter.ensure`)."""
        return await self.get(module).ensure()

    def lost(self, server: Server) -> None:
        """`server` failed a connect or proof with nothing sent (`Starter.lost`)."""
        starter = self._starters.get(server.module)
        if starter is not None:
            starter.lost(server)

    def set_versions(self, versions: Mapping[str, str]) -> list[asyncio.Task[None]]:
        """Take a new snapshot's versions, module to version: every running server whose
        runtime-file version differs from its module's is drained. Returns the drains."""
        drains = []
        for module, starter in self._starters.items():
            server = starter.server
            wanted = versions.get(module)
            if server is not None and wanted is not None and server.info.version != wanted:
                task = starter.drain()
                if task is not None:
                    drains.append(task)
        return drains

    def children(self) -> int:
        """How many page servers the shell started are still running."""
        return sum(len(starter.children) for starter in self._starters.values())

    def save(self) -> None:
        """Rewrite `servers.json` with every module's record."""
        modules = {name: record.data() for name, record in self._records.items()}
        modules.update((name, starter.record.data()) for name, starter in self._starters.items())
        data = json.dumps({"format": SERVERS_FORMAT, "modules": modules}, sort_keys=True,
                          separators=(",", ":"))
        try:
            files.atomic_write(self.path, data + "\n", mode=0o600)
        except OSError:
            _log.exception("writing %s failed", self.path)

    def rows(self) -> list[dict[str, Any]]:
        """Every module's row of the `/servers` data, without samples, by module name."""
        rows = [starter.row() for starter in self._starters.values()]
        for name, record in self._records.items():
            rows.append({"module": name, "state": "idle", "server_id": None, "version": None,
                         "transport": None, "pid": None, "child": False, "uptime_s": None,
                         "failed_for_s": None, "log": self.log, **record.data()})
        return sorted(rows, key=lambda row: row["module"])

    async def servers(self) -> dict[str, Any]:
        """The `/servers` data: one row per module the shell has started or recorded, and
        for each running server one sample (pid, RSS, CPU seconds, open fds or handles)
        and its open requests and streams from its health route. Sampled now, on this
        call only."""
        rows = self.rows()

        async def sampled(row: dict[str, Any]) -> None:
            starter = self._starters.get(row["module"])
            server = starter.server if starter is not None else None
            row["sample"] = None
            row["open"] = None
            if server is None:
                return
            row["sample"] = await self.sample(server.info.pid)
            row["open"] = await self._offload(_health_open, server.info)

        await asyncio.gather(*(sampled(row) for row in rows))
        return {"servers": rows}

    async def sample(self, pid: int) -> dict[str, Any] | None:
        """`sample(pid)`, taken on the starters' own threads."""
        return await self._offload(sample, pid)

    def _offload(self, fn: Callable[..., Any], *args: Any) -> asyncio.Future[Any]:
        if self._sampling is None:
            self._sampling = ThreadPoolExecutor(SAMPLE_THREADS,
                                                thread_name_prefix="moxi-ui-servers")
        return asyncio.get_running_loop().run_in_executor(self._sampling, fn, *args)

    async def aclose(self, wait: float = STOP_WAIT) -> None:
        """The shell is stopping: close every child's lifeline and wait for each to exit,
        killing any still running after `wait` seconds."""
        self.closing = True
        tasks = []
        for starter in self._starters.values():
            if starter._starting is not None:
                starter._starting.cancel()
            starter._drop()
            tasks.extend(child.stop(wait) for child in starter.children)
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._sampling is not None:
            self._sampling.shutdown(wait=False, cancel_futures=True)
            self._sampling = None
        self.save()


def _health_open(info: RuntimeInfo) -> dict[str, int] | None:
    try:
        status, data = call(info, "GET", "/api/health", timeout=HEALTH_WAIT)
    except Exception:  # noqa: BLE001 - no answer is reported as None
        return None
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("open"), dict):
        return None
    opened = data["open"]
    return {key: opened[key] for key in ("requests", "streams")
            if isinstance(opened.get(key), int)}


def sample(pid: int) -> dict[str, Any] | None:
    """One psutil sample of process `pid`: `{"pid", "rss", "cpu_s", "fds"}`, fds being
    open handles on Windows; None when the process can't be read."""
    try:
        import psutil

        process = psutil.Process(pid)
        with process.oneshot():
            times = process.cpu_times()
            return {
                "pid": pid,
                "rss": process.memory_info().rss,
                "cpu_s": round(times.user + times.system, 3),
                "fds": process.num_handles() if sys.platform == "win32" else process.num_fds(),
            }
    except Exception:  # noqa: BLE001 - psutil.Error, or a platform without the field
        return None


# --- the export run ---------------------------------------------------------------------


def export_argv(modules: Sequence[str]) -> list[str]:
    """The export command for `modules`."""
    for module in modules:
        if not is_module_name(module):
            raise ValueError(f"{module!r} is not a module name")
    return [str(python()), "-P", "-m", "moxi_loopback.export", *modules]


def export_tables(modules: Sequence[str], *, cwd: str | os.PathLike,
                  timeout: float = EXPORT_TIMEOUT,
                  run: Callable[..., subprocess.CompletedProcess] = proc.run) -> Exports:
    """Read `modules`' route tables with loopback's export command, in a fresh process run
    from `cwd` (the shell's runtime dir), so the shell imports none of their code.

    Each export the command gave and a route table takes goes in `mounts`, ready for
    `router.shell_table(modules, mounts)`; every other module is in `errors` with its
    reason. Blocking: the shell runs it on a worker thread.
    """
    found = Exports()
    if not modules:
        return found
    argv = export_argv(modules)
    try:
        done = run(argv, timeout=timeout, cwd=str(cwd))
    except subprocess.TimeoutExpired:
        return Exports(errors={m: f"the export command ran past {timeout:g} s" for m in modules})
    except OSError as err:
        return Exports(errors={m: f"the export command could not run: {err}" for m in modules})
    try:
        data = json.loads(done.stdout)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError:
        reason = f"the export command exited {done.returncode} with no answer"
        return Exports(errors={m: reason for m in modules})
    for module in modules:
        entry = data.get(module)
        if not isinstance(entry, dict):
            found.errors[module] = "the export command gave nothing for it"
        elif not entry.get("ok"):
            error = entry.get("error")
            found.errors[module] = error if isinstance(error, str) else "refused"
        else:
            export = entry.get("export")
            try:
                Table().mount(f"/m/{module}", export)
            except (ValueError, TypeError) as err:
                found.errors[module] = f"its export is refused: {err}"
            else:
                found.mounts[module] = export
    return found
