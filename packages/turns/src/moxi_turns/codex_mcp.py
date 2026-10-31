"""The Codex host's settle of the MCP servers its launch rendered.

Codex keeps an MCP server that failed at thread start `failed` for the thread's life, and
`config/mcpServer/reload` brings it back. So at each `ready` (start, resume, compaction)
and on each `mcp.reload` call, the host settles the servers the launch's thread config
names (`options.config.mcp_servers`): it reads their status, waits out those still
starting, and reloads once when one reads `failed`. A server's `tools` in the list come
from a fresh probe connection, not the thread's, so only `runtimeStatus` is read; the
list's other servers (the account's own `codex_apps`, the user's) are not counted.

The rule: a server counts as settled only when a list read shows it final before any
reload, or when a `mcpServer/startupStatus/updated` notification sent after the host's
last request about it (its list read, or its reload) reports it settled. The host never
polls the list, since each read opens a fresh MCP session to every configured server;
it reads the list only at the steps below, and never counts a status from before its
own reload. One deadline, `MCP_SETTLE_S` from the settle's start, bounds the whole
settle, and every request races it.

Final statuses are `connected`, `failed`, `cancelled`, `authenticationRequired` and
`disabled`, and a list read showing one is not waited on. `starting`, `notStarted` and
null (unavailable, or the server absent from the list) are waited on alike. A
notification settles a server when it reports `ready`, `failed` or `cancelled`. Only
`failed` leads to the reload.

The steps:

(a) Clear the notifications recorded for the rendered servers, then read
    `mcpServerStatus/list {threadId}`.
(b) For each server that read not final, wait for a settling notification recorded since
    that clear (so one that came during the read counts), then clear and read again.
    Repeat until each reads final: Codex runs a second startup round a second or so after
    thread start, so a re-read can still show `starting`.
(c) If any reads `failed`: clear their notifications, send `config/mcpServer/reload`
    once, wait for a settling notification for each recorded after that clear, then
    read again as in (b). The reload answers before its servers settle, and the clear
    drops the `failed` the thread's start left, so neither ends the wait. A stale
    `failed` from the startup round that lands after the clear ends the wait early, but
    the read after it shows the reload's `starting`, so (b)'s loop waits again.
(d) When the deadline passes the host stops waiting and sends nothing more: a request
    in flight is given up at the deadline, the answer holds the last statuses read, and
    a server never read is null.

The answer is `{reloaded, servers}`: whether the reload was sent, and each rendered
server's last read status. A server that ends reading `failed`, or not final at the
deadline, is a doctor record (`mcp:<thread>:<name>`, its status in the text), cleared
when a later settle reads it `connected`, when a `ready` notification arrives for it, or
when the host exits.

One settle runs at a time. A `ready` that comes during one joins it. An `mcp.reload` call
joins it only before its first list read goes out; after that, the settle may have read
or reloaded before what the caller just did (comms registering its token), so the call
waits for one more settle, queued behind it, which every later call joins until it
starts. A call therefore answers within one deadline, or within two when it queued.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from . import codex_protocol as proto
from .results import MCP_SETTLE_S

log = logging.getLogger("moxi.turns.codex.mcp")

LIST = "mcpServerStatus/list"
RELOAD = "config/mcpServer/reload"
STARTUP_STATUS = "mcpServer/startupStatus/updated"
#: The `runtimeStatus` values a list read is not waited on for.
FINAL = frozenset({"connected", "failed", "cancelled", "authenticationRequired",
                   "disabled"})
#: The notification statuses that settle a server.
SETTLING = frozenset({"ready", "failed", "cancelled"})
#: How often a wait looks at the clock again, in seconds. A wait is woken at once by a
#: notification; this only lets an injected clock's deadline be seen.
TICK = 0.05
#: The doctor record's module, and its fix.
RECORD_MODULE = "turns"
RECORD_FIX = "relaunch the session"

Call = Callable[[str, Any, float], Any]


class _Deadline(Exception):
    """The settle's deadline passed."""


class _Stopped(Exception):
    """The host is closing."""


class _Run:
    """One settle: its deadline once it starts, whether its first list read went out,
    and its answer once it ends."""

    def __init__(self) -> None:
        self.deadline: float | None = None
        self.read_sent = False
        self.done = threading.Event()
        self.answer: dict[str, Any] | None = None


class McpSettle:
    """The settle of `names`, the servers a Codex launch rendered, on one host.

    `call(method, params, timeout)` sends one app-server request (raising
    `codex_protocol.AppServerOffline` when it was never sent, `NoReply` when no answer came,
    `CodexError` for an error answer); `thread_id()` is the session's thread. `record` and
    `clear` leave and remove a doctor record (`(text, key, fix)` and `(key)`); `clock`,
    `deadline_s` and `tick` are a test's to set.
    """

    def __init__(self, names: Iterable[str], call: Call, thread_id: Callable[[], str | None],
                 *, record: Callable[[str, str, str], None] | None = None,
                 clear: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 deadline_s: float = MCP_SETTLE_S, tick: float = TICK):
        self.names = tuple(dict.fromkeys(names))
        self._call = call
        self._thread_id = thread_id
        self._record = record or _doctor_record
        self._clear = clear or _doctor_clear
        self._clock = clock
        self.deadline_s = deadline_s
        self._tick = tick
        self._cond = threading.Condition()
        #: The settling status each rendered server last reported since its last clear.
        self._notes: dict[str, str] = {}
        self._running: _Run | None = None
        self._queued: _Run | None = None
        self._stopped = False
        #: The record keys this host left, by server name.
        self._recorded: dict[str, str] = {}
        #: Before the first settle ends, a record an earlier host left for this thread may
        #: stand: the first settle clears each server it reads connected.
        self._fresh = True
        #: How many settles ran and how many reloads went out, for a test to read.
        self.settles = 0
        self.reloads = 0

    # -- the host's side --------------------------------------------------------------------

    def ready(self) -> None:
        """A `ready`: settle, on a thread of its own, unless one runs, which it joins."""
        if not self.names:
            return
        with self._cond:
            if self._stopped or self._running is not None:
                return
            self._start()

    def reload(self) -> dict[str, Any]:
        """`mcp.reload`: settle, joining a settle whose first read has not gone out, else
        the one queued behind it; the answer once that settle ends."""
        if not self.names:
            return {"reloaded": False, "servers": {}}
        with self._cond:
            if self._stopped:
                return self._unsettled()
            running = self._running
            if running is not None and not running.read_sent:
                run = running
            elif running is not None:
                if self._queued is None:
                    self._queued = _Run()
                run = self._queued
            else:
                run = self._start()
        # Every wait and request in a settle is bounded by its deadline; this bound is
        # only a guard against a fault in the host.
        if not run.done.wait(2 * self.deadline_s + 5.0):
            log.error("an MCP settle did not end within its deadlines")
            return self._unsettled()
        answer = run.answer or self._unsettled()
        return {"reloaded": answer["reloaded"], "servers": dict(answer["servers"])}

    def notified(self, params: dict) -> None:
        """A `mcpServer/startupStatus/updated` for the session's thread."""
        name, status = params.get("name"), params.get("status")
        if name not in self.names or not isinstance(status, str):
            return
        with self._cond:
            if status in SETTLING:
                self._notes[name] = status
                self._cond.notify_all()
            stale = status == "ready" and name in self._recorded
        if stale:
            self._drop(name)

    def close(self) -> None:
        """The host exits: stop any settle and clear every record this host left."""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
            recorded = list(self._recorded)
        for name in recorded:
            self._drop(name)

    # -- the settle -------------------------------------------------------------------------

    def _start(self) -> _Run:
        """Under the lock: a settle starts now."""
        run = self._running = _Run()
        run.deadline = self._clock() + self.deadline_s
        threading.Thread(target=self._loop, args=(run,), daemon=True,
                         name="turns-codex-mcp").start()
        return run

    def _loop(self, run: _Run) -> None:
        while run is not None:
            try:
                answer = self._settle(run)
            except BaseException:  # noqa: BLE001 - a waiting caller still gets an answer
                log.exception("the MCP settle failed")
                answer = self._unsettled()
            with self._cond:
                run.answer = answer
                run.done.set()
                run = self._queued
                self._queued = None
                self._running = run
                if run is not None:
                    run.deadline = self._clock() + self.deadline_s

    def _settle(self, run: _Run) -> dict[str, Any]:
        self.settles += 1
        statuses: dict[str, str | None] = dict.fromkeys(self.names)
        reloaded = False
        ended = "settled"
        try:
            self._read(run, statuses, first=True)                      # (a)
            self._wait_out(run, statuses)                               # (b)
            failed = [n for n in self.names if statuses[n] == "failed"]
            if failed:                                                  # (c)
                self._forget(failed)
                remaining = self._check(run)
                try:
                    self._call(RELOAD, None, remaining)
                except proto.NoReply:
                    # It went out: Codex may be reloading.
                    reloaded = True
                    self.reloads += 1
                    if self._clock() >= run.deadline:
                        raise _Deadline() from None
                    raise
                except proto.CodexError:
                    reloaded = True
                    self.reloads += 1
                    raise
                reloaded = True
                self.reloads += 1
                self._wait(run, failed)
                self._read(run, statuses)
                self._wait_out(run, statuses)
        except _Deadline:
            ended = "deadline"
            log.warning("the MCP servers did not settle within %g s: %s", self.deadline_s,
                        _statuses(statuses))
        except _Stopped:
            ended = "stopped"
        except Exception as exc:  # noqa: BLE001 - the app-server failed a request
            ended = "error"
            log.warning("the MCP settle stopped: %s", str(exc).strip() or type(exc).__name__)
        if ended in ("settled", "deadline"):
            with self._cond:
                late = {n for n, s in self._notes.items() if s == "ready"}
            self._mark(statuses, late)
        return {"reloaded": reloaded, "servers": dict(statuses)}

    def _wait_out(self, run: _Run, statuses: dict[str, str | None]) -> None:
        """(b): until each server reads final, wait for each that does not, and read
        again."""
        while True:
            waiting = [n for n in self.names if statuses[n] not in FINAL]
            if not waiting:
                return
            self._wait(run, waiting)
            self._read(run, statuses)

    def _wait(self, run: _Run, names: list[str]) -> None:
        """Until each of `names` has a settling notification since its last clear."""
        with self._cond:
            while not all(n in self._notes for n in names):
                if self._stopped:
                    raise _Stopped()
                remaining = run.deadline - self._clock()
                if remaining <= 0:
                    raise _Deadline()
                self._cond.wait(min(remaining, self._tick))

    def _read(self, run: _Run, statuses: dict[str, str | None], *,
              first: bool = False) -> None:
        """Clear every rendered server's notifications, then read the list. `statuses`
        changes only when the whole read answered."""
        thread_id = self._thread_id()
        if not thread_id:
            raise proto.AppServerOffline("the session has no thread yet")
        with self._cond:
            self._check(run)
            self._notes.clear()
            if first:
                run.read_sent = True
        read: dict[str, str | None] = dict.fromkeys(self.names)
        params: dict[str, Any] = {"threadId": thread_id}
        while True:
            answer = self._request(run, LIST, dict(params))
            data = answer.get("data") if isinstance(answer, dict) else None
            for entry in data if isinstance(data, list) else ():
                if isinstance(entry, dict) and entry.get("name") in read:
                    status = entry.get("runtimeStatus")
                    read[entry["name"]] = status if isinstance(status, str) else None
            cursor = answer.get("nextCursor") if isinstance(answer, dict) else None
            if not cursor or all(read[n] is not None for n in self.names):
                break
            params["cursor"] = cursor
        statuses.update(read)

    def _forget(self, names: list[str]) -> None:
        with self._cond:
            for name in names:
                self._notes.pop(name, None)

    def _request(self, run: _Run, method: str, params) -> Any:
        """One request, given up at the deadline."""
        remaining = self._check(run)
        try:
            return self._call(method, params, remaining)
        except proto.NoReply:
            if self._clock() >= run.deadline:
                raise _Deadline() from None
            raise

    def _check(self, run: _Run) -> float:
        """The time left; raises once the deadline passed or the host closes."""
        if self._stopped:
            raise _Stopped()
        remaining = run.deadline - self._clock()
        if remaining <= 0:
            raise _Deadline()
        return remaining

    # -- the records --------------------------------------------------------------------------

    def _mark(self, statuses: dict[str, str | None], late: set[str]) -> None:
        """Record each server that reads `failed` or not final; clear the record of each
        that reads `connected`, or that reported `ready` after the last read."""
        thread_id = self._thread_id()
        fresh, self._fresh = self._fresh, False
        for name in self.names:
            status = statuses[name]
            if status == "connected" or name in late:
                if fresh or name in self._recorded:
                    self._drop(name, thread_id)
            elif status == "failed" or status not in FINAL:
                if not thread_id:
                    continue
                key = f"mcp:{thread_id}:{name}"
                shown = status or "unsettled"
                try:
                    self._record(f"{thread_id}: MCP server {name} stays {shown}", key,
                                 RECORD_FIX)
                except Exception:  # noqa: BLE001 - a record is information
                    log.exception("the MCP server's doctor record could not be written")
                    continue
                with self._cond:
                    self._recorded[name] = key

    def _drop(self, name: str, thread_id: str | None = None) -> None:
        with self._cond:
            key = self._recorded.pop(name, None)
        if key is None:
            thread_id = thread_id or self._thread_id()
            if not thread_id:
                return
            key = f"mcp:{thread_id}:{name}"
        try:
            self._clear(key)
        except Exception:  # noqa: BLE001
            log.exception("the MCP server's doctor record could not be cleared")

    def _unsettled(self) -> dict[str, Any]:
        return {"reloaded": False, "servers": dict.fromkeys(self.names)}


def rendered(options) -> tuple[str, ...]:
    """The MCP servers a Codex launch's thread config names: `options.config.mcp_servers`'
    keys."""
    config = options.get("config") if isinstance(options, dict) else None
    servers = config.get("mcp_servers") if isinstance(config, dict) else None
    return tuple(str(name) for name in servers) if isinstance(servers, dict) else ()


def _statuses(statuses: dict[str, str | None]) -> str:
    return ", ".join(f"{name} {status or 'unread'}" for name, status in statuses.items())


def _doctor_record(text: str, key: str, fix: str) -> None:
    from moxi import doctor

    doctor.record(RECORD_MODULE, text, key=key, fix=fix)


def _doctor_clear(key: str) -> None:
    from moxi import doctor

    doctor.clear(RECORD_MODULE, key)


__all__ = ["FINAL", "LIST", "RELOAD", "SETTLING", "STARTUP_STATUS", "McpSettle", "rendered"]
