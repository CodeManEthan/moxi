"""The caller's side: finding a session's endpoint and speaking turns' socket protocol to it.

A caller names a session by what it already knows (the harness session id, or a process
id), and turns resolves that to a live endpoint by connecting to it, not by trusting a
file. The wire is `moxi.ipc` (JSON-RPC 2.0, one message per line); every connection
offers versions 2 to 2 in `rpc.hello` and then asks `session` before any other line.

Connections. `moxi.ipc` serves one connection's requests one at a time, in order, so a
method that can block without a bound gets a connection of its own. A `Session` keeps
two shared connections, each opened on first use and closed with the `Session` (its
`close()`, or its collection):

- the first carries the calls whose answer never waits on the harness: `session`,
  `deliver`, `steer`, and the look-up by `ref` of a hand-in whose answer never came;
- the second carries `facts` and `agents`, which wait on the harness's live read for up
  to its live bound, so a `deliver` never queues behind one.

Every other `wait`, and each `command`, `watch` and `handle_stops`, opens a connection of
its own and closes it when done. Before a call writes on a shared connection it checks
the connection is still open, and opens a new one first if not, so a request is never
lost to a connection that had already closed.

Read bounds. A short call (`session`, `deliver`, `steer`, the look-up, `stops.answer`)
waits `READ_S` for its answer. `facts`, `agents` and `watch`'s first answer wait
`LIVE_READ_S`, which is longer than any harness's live bound (1 s on Claude, 2 s on Codex
and OpenCode), so a slow live read answers with its live part unreadable instead of
running past the caller's bound. `connect` and the hello wait `CONNECT_S`.

When an answer does not come. The library reports a refusal only when it received one,
or when it never wrote the request:

- Never written (nothing serves the address, or the connection failed during its
  `rpc.hello` or `session`): `deliver`, `steer` and `command` read `Refused("gone")`;
  `wait` reads `Lost` `unanswered`; `session`, `facts` and `agents` raise `TurnsError`.
- The endpoint answered the `gone` error: `Refused("gone")` on `deliver`, `steer` and
  `command`, `Lost` on `wait`, `TurnsError` on the reads.
- Written and not answered (the bound passed, or the connection closed): the outcome is
  unknown. `deliver` and `steer` give a `Delivery` whose `id` is None; its waits first
  look the delivery up by its `ref` on the connection the request went on (the endpoint
  serves that connection in order, so the answer follows the request's), or, when that
  connection has closed, on a new one. `command` reads `Failed("unanswered")`, and the
  command may still run. `wait` raises `TimeoutError`, as when the endpoint answers
  `pending`. The reads raise `TurnsError`; asking again is safe.

A call whose bound passed leaves its connection open: `moxi.ipc` drops the late answer.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from moxi import ipc
from moxi.paths import Layout

from . import protocol
from .procs import DEFAULT as DEFAULT_PROC, ProcView
from .results import (COMMAND_WAIT_S, COMMANDS, ERROR_GONE, FAILED_UNANSWERED,
                      LOST_UNANSWERED, REFUSED_GONE, STOP_SHELLS_WAIT_S, Agents, Ended,
                      Facts, Failed, Gone, Lost, Passed, Ran, Refused, Started, StopFacts,
                      check_ref, from_notification, from_wire)

log = logging.getLogger("moxi.turns.client")

#: How long a connect, and the hello after it, may take, in seconds.
CONNECT_S = 2.0
#: The library's read bound for a short call, in seconds: `session`, `deliver`, `steer`,
#: the look-up by `ref`, `stops.answer`. None of these waits on the harness.
READ_S = 2.0
#: The read bound for `facts`, `agents` and `watch`'s first answer, in seconds. Each
#: waits on the harness's live read for at most its live bound (1 s on Claude, 2 s on
#: Codex and OpenCode), so this is set well past the longest.
LIVE_READ_S = 5.0
#: How long a gate-passed `exit` may take to end the harness process before it reads
#: `Failed("not-exited")`.
EXIT_BOUND_S = 5.0
#: The reason of a gate-passed `exit` whose harness process still runs after the bound
#: (a reload's SIGTERM met it, or the exit was held).
NOT_EXITED = "not-exited"

#: `moxi.ipc`'s hello codes.
_HELLO_REFUSED = -32001


class TurnsError(Exception):
    """An endpoint that cannot be reached, refused a request with an error, or answered
    something turns cannot read. `code` and `data` are the JSON-RPC error's, when the
    endpoint answered with one."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class VersionError(TurnsError):
    """The endpoint speaks a turns protocol version this library does not. `client` and
    `server` are the two version ranges, `(lo, hi)`; the message says which side to
    upgrade."""

    def __init__(self, message: str, *, client: tuple[int, int],
                 server: tuple[int, int] | None):
        super().__init__(message, code=_HELLO_REFUSED,
                         data={"client": list(client),
                               "server": None if server is None else list(server)})
        self.client = client
        self.server = server


def _version_error(address: str, data: Any) -> VersionError:
    ours = protocol.VERSIONS
    server = None
    if isinstance(data, dict):
        found = data.get("server")
        if (isinstance(found, list) and len(found) == 2
                and all(isinstance(v, int) for v in found)):
            server = (found[0], found[1])
    if server is None:
        return VersionError(f"the endpoint at {address} refused turns protocol "
                            f"{ours[0]} to {ours[1]} and did not say what it speaks",
                            client=ours, server=None)
    if server[1] < ours[0]:
        fix = "restart the session, or reload its plugins, so its endpoint is upgraded"
    else:
        fix = "upgrade moxi-turns in this program"
    return VersionError(f"the endpoint at {address} speaks turns protocol {server[0]} to "
                        f"{server[1]}; this library speaks {ours[0]} to {ours[1]}; {fix}",
                        client=ours, server=server)


def _v1_error(directory: Path) -> VersionError:
    ours = protocol.VERSIONS
    return VersionError(f"this session's endpoint ({directory}) speaks turns protocol 1; "
                        f"this library speaks {ours[0]} to {ours[1]}; reload the "
                        f"session's plugins or restart it", client=ours, server=(1, 1))


def _rpc_error(exc: ipc.RpcError, address: str, method: str) -> TurnsError:
    """The `TurnsError` for an error the endpoint answered."""
    if exc.code == _HELLO_REFUSED:
        return _version_error(address, exc.data)
    if exc.code == ERROR_GONE:
        return TurnsError(f"the endpoint at {address} is ending: {exc.message}",
                          code=exc.code, data=exc.data)
    return TurnsError(f"the endpoint at {address} refused {method}: {exc}",
                      code=exc.code, data=exc.data)


class _NotWritten(Exception):
    """A request that never went out: no connection could be made, or the connection
    failed during its hello or its `session`. `error` is the `TurnsError` for the reads;
    `cause` is what failed; `connect` is True when the connect itself failed."""

    def __init__(self, error: TurnsError, cause: BaseException, connect: bool):
        super().__init__(str(error))
        self.error = error
        self.cause = cause
        self.connect = connect

    @property
    def not_serving(self) -> bool:
        return isinstance(self.cause, ipc.NotServing)


class _Unanswered(Exception):
    """A request written, or handed to the connection to write, with no answer: its bound
    passed or the connection closed. `client` is the connection it went on."""

    def __init__(self, cause: BaseException, client: ipc.Client):
        super().__init__(str(cause))
        self.cause = cause
        self.client = client


def _is_open(client: ipc.Client) -> bool:
    """Whether `client` can still carry a call: it has not closed or failed."""
    return not client.closed


def runtime_dir() -> Path:
    """turns' runtime directory, `RUNTIME/turns/`. Version 2's endpoints keep their
    directories under its `v2/`."""
    return Layout("turns").runtime_dir()


def _connect(address: str) -> tuple[ipc.Client, ipc.Agreed, dict[str, Any]]:
    """A new connection to `address`, past its `rpc.hello` and `session`: the client, what
    the hello agreed and the session answer. Raises `_NotWritten`."""
    try:
        client = ipc.connect(address, timeout=CONNECT_S)
    except (ipc.IpcError, OSError) as exc:
        raise _NotWritten(TurnsError(f"the endpoint at {address} cannot be reached: "
                                     f"{exc}"), exc, True) from None
    try:
        agreed = client.hello(protocol.VERSIONS)
        info = client.call("session", timeout=READ_S)
    except ipc.RpcError as exc:
        client.close()
        raise _NotWritten(_rpc_error(exc, address, "the hello"), exc, False) from None
    except ipc.NotRpc as exc:
        client.close()
        raise _NotWritten(TurnsError(f"the endpoint at {address} does not speak "
                                     f"JSON-RPC: {exc}"), exc, False) from None
    except (ipc.IpcError, OSError) as exc:
        client.close()
        raise _NotWritten(TurnsError(f"the endpoint at {address} did not finish its "
                                     f"hello: {exc}"), exc, False) from None
    if not isinstance(info, dict):
        client.close()
        error = TurnsError(f"the endpoint at {address} answered session with {info!r}")
        raise _NotWritten(error, error, False)
    return client, agreed, info


class _Link:
    """One of a `Session`'s two shared connections: opened on first use, opened again
    when the last one has closed, closed with the `Session`."""

    def __init__(self, session: Session):
        self._session = weakref.ref(session)
        self._lock = threading.Lock()
        self._client: ipc.Client | None = None
        self._closed = False

    def get(self) -> ipc.Client:
        """The connection, open. Raises `_NotWritten` when none can be made."""
        with self._lock:
            if self._closed:
                error = TurnsError("this Session is closed")
                raise _NotWritten(error, error, False)
            client = self._client
            if client is not None and _is_open(client):
                return client
            self._client = None
            if client is not None:
                client.close()
            session = self._session()
            if session is None:
                error = TurnsError("this Session is closed")
                raise _NotWritten(error, error, False)
            self._client = session._open()
            return self._client

    def close(self) -> None:
        with self._lock:
            self._closed = True
            client, self._client = self._client, None
        if client is not None:
            client.close()


def _close_links(links: tuple[_Link, ...]) -> None:
    for link in links:
        link.close()


def _call(client: ipc.Client, method: str, params: Any, timeout: float | None) -> Any:
    """`client.call`, with a failure after the request was handed to the connection
    raised as `_Unanswered`. An error the endpoint answered is raised as given."""
    try:
        return client.call(method, params, timeout=timeout)
    except ipc.RpcError:
        raise
    except (ipc.IpcError, OSError) as exc:
        raise _Unanswered(exc, client) from None


def _plus(timeout: float | None, extra: float) -> float | None:
    return None if timeout is None else timeout + extra


def _ms(timeout: float | None) -> int | None:
    return None if timeout is None else max(0, int(timeout * 1000))


class Session:
    """One live endpoint, as `find`, `sessions` or `wait_ready` found it.

    `info` is the last `session` answer: `harness`, `pid` (the harness process, the
    root of the session's process tree), `endpoint_pid` (the process serving the
    endpoint), `session_id`, `earlier`, `cwd`, `nonce`, `started`, `endpoint_version`,
    `mod_version` and `trouble` when the endpoint has some. `refresh()` asks again.
    `capabilities` is what the session offers, as its hello answered. Close it with
    `close()`, or use it as a context manager; collecting it closes it too.
    """

    def __init__(self, address: str, info: dict[str, Any] | None = None, *,
                 directory: Path | None = None):
        self.address = address
        self.dir = directory
        self._info: dict[str, Any] = dict(info or {})
        self._capabilities: frozenset[str] | None = None
        self._short = _Link(self)
        self._reads = _Link(self)
        self._finalizer = weakref.finalize(self, _close_links, (self._short, self._reads))

    def __repr__(self) -> str:
        return (f"Session(harness={self.harness!r}, pid={self.pid}, "
                f"session_id={self.session_id!r})")

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the shared connections. A later call on this `Session` raises
        `TurnsError` or reads as never written."""
        self._finalizer()

    # -- what the endpoint said ----------------------------------------------------------

    @property
    def info(self) -> dict[str, Any]:
        return dict(self._info)

    @property
    def harness(self) -> str:
        return self._info.get("harness", "")

    @property
    def pid(self) -> int | None:
        return self._info.get("pid")

    @property
    def endpoint_pid(self) -> int | None:
        return self._info.get("endpoint_pid")

    @property
    def session_id(self) -> str | None:
        return self._info.get("session_id")

    @property
    def capabilities(self) -> frozenset[str]:
        """What the session offers. Known from the first connection's hello; asking
        before any connection opens the first shared one. Raises `TurnsError` when the
        endpoint cannot be reached then."""
        if self._capabilities is None:
            try:
                self._short.get()
            except _NotWritten as exc:
                raise exc.error from None
        return self._capabilities or frozenset()

    def _open(self) -> ipc.Client:
        """A new connection past its hello and `session`; what they said is kept."""
        client, agreed, info = _connect(self.address)
        self._info = info
        self._capabilities = frozenset(agreed.capabilities)
        return client

    # -- reads --------------------------------------------------------------------------

    def _read(self, link: _Link, method: str, timeout: float) -> Any:
        try:
            return _call(link.get(), method, None, timeout)
        except _NotWritten as exc:
            raise exc.error from None
        except _Unanswered as exc:
            raise TurnsError(f"the endpoint at {self.address} did not answer {method}: "
                             f"{exc.cause}") from None
        except ipc.RpcError as exc:
            raise _rpc_error(exc, self.address, method) from None

    def _value(self, raw: Any, method: str, kind: type) -> Any:
        try:
            value = from_wire(raw)
        except ValueError as exc:
            raise TurnsError(f"the endpoint at {self.address} answered {method} with "
                             f"something turns cannot read: {exc}") from None
        if not isinstance(value, kind):
            raise TurnsError(f"the endpoint at {self.address} answered {method} with a "
                             f"{type(value).__name__}")
        return value

    def refresh(self) -> dict[str, Any]:
        """Ask the endpoint for its `session` answer now (the session id follows a
        `/clear`). Raises `TurnsError`."""
        info = self._read(self._short, "session", READ_S)
        if not isinstance(info, dict):
            raise TurnsError(f"the endpoint at {self.address} answered session with "
                             f"{info!r}")
        self._info = info
        return self.info

    def facts(self) -> Facts:
        """One observation of the session. Raises `TurnsError`."""
        return self._value(self._read(self._reads, "facts", LIVE_READ_S), "facts", Facts)

    def agents(self) -> Agents:
        """The session's subagents as the harness records them. Raises `TurnsError`."""
        return self._value(self._read(self._reads, "agents", LIVE_READ_S), "agents",
                           Agents)

    # -- deliveries ---------------------------------------------------------------------

    def deliver(self, text: str, *, only_if_idle: bool = False,
                if_turn_number: int | None = None, refuse_draft: bool = False,
                refuse_agents: bool = False, ref: str | None = None) -> Delivery:
        """Hand `text` to the session as the next input. With `only_if_idle` it starts a
        turn only when none is open, and `if_turn_number`, `refuse_draft` and
        `refuse_agents` add their checks; without it those are a `ValueError`. `ref` is
        the caller's own name for the delivery (at most 256 bytes as UTF-8), echoed on
        its values and the turn notifications."""
        _check_text(text)
        ref = check_ref(ref)
        if not only_if_idle and (if_turn_number is not None or refuse_draft
                                 or refuse_agents):
            raise ValueError("if_turn_number, refuse_draft and refuse_agents are valid "
                             "only with only_if_idle=True")
        params: dict[str, Any] = {"text": text}
        if ref is not None:
            params["ref"] = ref
        if only_if_idle:
            params.update(only_if_idle=True, if_turn_number=if_turn_number,
                          refuse_draft=refuse_draft, refuse_agents=refuse_agents)
        return self._hand("deliver", params, ref)

    def steer(self, text: str, *, ref: str | None = None) -> Delivery:
        """Hand `text` into the turn now running. A session with no turn running refuses
        it `idle`."""
        _check_text(text)
        ref = check_ref(ref)
        params: dict[str, Any] = {"text": text}
        if ref is not None:
            params["ref"] = ref
        return self._hand("steer", params, ref)

    def _hand(self, method: str, params: dict[str, Any], ref: str | None) -> Delivery:
        try:
            answer = _call(self._short.get(), method, params, READ_S)
        except _NotWritten as exc:
            log.info("%s was never written: %s", method, exc.error)
            return Delivery(self, None, ref, known=Refused(REFUSED_GONE))
        except _Unanswered as exc:
            log.info("%s was written and not answered: %s; its outcome is unknown", method,
                     exc.cause)
            return Delivery(self, None, ref, via=exc.client)
        except ipc.RpcError as exc:
            if exc.code == ERROR_GONE:
                return Delivery(self, None, ref, known=Refused(REFUSED_GONE))
            raise _rpc_error(exc, self.address, method) from None
        if not isinstance(answer, dict) or not isinstance(answer.get("delivery"), str):
            raise TurnsError(f"the endpoint at {self.address} answered {method} with "
                             f"{answer!r}")
        known = None
        if answer.get("result") is not None:
            known = self._value(answer["result"], method, (Refused, Lost, Started, Ended))
        return Delivery(self, answer["delivery"], answer.get("ref", ref), known=known)

    # -- commands -----------------------------------------------------------------------

    def command(self, name: str, *, if_turn_number: int | None = None,
                timeout: float | None = None,
                stop_shells: bool = False) -> Ran | Refused | Failed:
        """Run `clear`, `exit` or `compact`; `timeout` bounds this call's wait for the
        harness to settle it (None: 30 s for `clear` and `exit`, 330 s for `compact`,
        plus 10 s with `stop_shells`). A call that stops waiting first reads `Failed`,
        and the command may still run.

        `stop_shells` (with `clear` or `compact` only) stops the session's background
        shells once the command has taken effect; `Ran.shells` reports them.

        `exit` reads `Ran` only once the harness process is gone: when the harness's
        gate passes it the endpoint answers `passed`, and this watches the process
        `pid` names (its identity taken before the request, so a reused pid cannot stand
        in for it) and answers `Ran` once it has ended, or `Failed("not-exited")` when
        it still runs after `EXIT_BOUND_S`."""
        if name not in COMMANDS:
            raise ValueError("a command is one of " + ", ".join(COMMANDS))
        if stop_shells and name == "exit":
            raise ValueError("stop_shells is valid with clear and compact only")
        if timeout is None:
            timeout = COMMAND_WAIT_S[name] + (STOP_SHELLS_WAIT_S if stop_shells else 0)
        params: dict[str, Any] = {"name": name, "if_turn_number": if_turn_number,
                                  "timeout_ms": _ms(timeout)}
        if stop_shells:
            params["stop_shells"] = True
        proc_id, watched = None, False
        if name == "exit" and isinstance(self.pid, int) and self.pid > 0:
            from moxi import proc

            proc_id, watched = proc.identity(self.pid), True
        try:
            client, _, _ = _connect(self.address)
        except _NotWritten as exc:
            log.info("command %s was never written: %s", name, exc.error)
            return Refused(REFUSED_GONE)
        try:
            answer = _call(client, "command", params, timeout + READ_S)
        except _Unanswered as exc:
            log.info("command %s was written and not answered: %s; it may still run",
                     name, exc.cause)
            return Failed(FAILED_UNANSWERED)
        except ipc.RpcError as exc:
            if exc.code == ERROR_GONE:
                return Refused(REFUSED_GONE)
            raise _rpc_error(exc, self.address, "command") from None
        finally:
            client.close()
        if not isinstance(answer, dict):
            raise TurnsError(f"the endpoint at {self.address} answered command with "
                             f"{answer!r}")
        result = self._value(answer.get("result"), "command", (Ran, Refused, Failed, Passed))
        if not isinstance(result, Passed):
            return result
        if watched and _exited(proc_id, EXIT_BOUND_S):
            return Ran(None)
        return Failed(NOT_EXITED)

    # -- watching and the stop handler --------------------------------------------------

    def watch(self) -> Watch:
        """Watch the session: `.facts` as of the subscription, then iterate every
        `Ready`, `TurnStarted`, `TurnSteered` and `TurnEnded` as it happens, and one
        `Gone` at the end. No event falls between the facts and the first one. Raises
        `TurnsError` when the watch cannot start."""
        try:
            client, _, _ = _connect(self.address)
        except _NotWritten as exc:
            raise exc.error from None
        try:
            answer = _call(client, "watch", None, LIVE_READ_S)
            if not isinstance(answer, dict):
                raise TurnsError(f"the endpoint at {self.address} answered watch with "
                                 f"{answer!r}")
            facts = self._value(answer.get("facts"), "watch", Facts)
        except _Unanswered as exc:
            client.close()
            raise TurnsError(f"the endpoint at {self.address} did not answer watch: "
                             f"{exc.cause}") from None
        except ipc.RpcError as exc:
            client.close()
            raise _rpc_error(exc, self.address, "watch") from None
        except BaseException:
            client.close()
            raise
        return Watch(client, facts)

    def handle_stops(self, on_stop: Callable[[StopFacts], str | None]) -> StopHandler:
        """Become the session's one stop handler: `on_stop` is asked at each Stop of the
        main loop, and a non-empty string it returns holds the turn open with that text
        as the reason. Raises `TurnsError` when the endpoint cannot be reached, does not
        offer `stop-handler`, or another connection already handles its stops (`code`
        1003)."""
        try:
            client, _, _ = _connect(self.address)
        except _NotWritten as exc:
            raise exc.error from None
        try:
            _call(client, "stops.handle", None, READ_S)
        except _Unanswered as exc:
            client.close()
            raise TurnsError(f"the endpoint at {self.address} did not answer "
                             f"stops.handle: {exc.cause}") from None
        except ipc.RpcError as exc:
            client.close()
            raise _rpc_error(exc, self.address, "stops.handle") from None
        return StopHandler(client, on_stop)


def _check_text(text: Any) -> None:
    if not isinstance(text, str) or not text:
        raise ValueError("text is a non-empty string")


def _exited(proc_id: Any, bound: float) -> bool:
    """True once the process `proc_id` names has ended, polled through `moxi.proc` for
    up to `bound` seconds. A process already gone when its identity was taken (None)
    has ended."""
    if proc_id is None:
        return True
    from moxi import proc

    deadline = time.monotonic() + bound
    while proc.alive(proc_id):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


class Delivery:
    """One text handed to turns.

    `id` is the id turns minted. It is None when the endpoint could not be reached (both
    waits then read `Refused("gone")`) and when the hand-in was written and its answer
    never came: the outcome is unknown, and the first wait looks the delivery up by its
    `ref` and sets `id` when the endpoint has it. `ref` is the caller's ref.
    """

    def __init__(self, session: Session, delivery_id: str | None, ref: str | None = None,
                 *, known: Any = None, via: ipc.Client | None = None):
        self.session = session
        self.id = delivery_id
        self.ref = ref
        self._known = known
        self._via = via
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"Delivery({self.id!r}, ref={self.ref!r})"

    def wait_started(self, timeout: float | None) -> Started | Refused | Lost:
        """The delivery's start: `Started`, or why it did not start. Raises
        `TimeoutError` when `timeout` passes first; a later wait asks again."""
        return self._wait("started", timeout)

    def wait_ended(self, timeout: float | None) -> Ended | Refused | Lost:
        """The end of the turn that took the delivery: `Ended`, or why there is none.
        Raises `TimeoutError` when `timeout` passes first."""
        return self._wait("ended", timeout)

    def _wait(self, until: str, timeout: float | None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            if self._known is not None:
                return self._known
            if self.id is None:
                value = self._resolve(until, deadline)
                if value is not None:
                    return value
        return self._wait_id(until, deadline)

    def _left(self, deadline: float | None) -> float | None:
        return None if deadline is None else max(deadline - time.monotonic(), 0.0)

    def _timed_out(self, until: str, deadline: float | None) -> TimeoutError:
        what = self.id if self.id is not None else f"with ref {self.ref!r}"
        return TimeoutError(f"delivery {what} has not {until} in the time given")

    def _resolve(self, until: str, deadline: float | None):
        """Look an unanswered hand-in up by its `ref`. Sets `id` when the endpoint has the
        delivery, and returns its value when settled, None when it is still pending (the
        wait then goes on by id). Settles the delivery as `known` when there is nothing to
        wait for. Raises `TimeoutError` when the deadline passes with the outcome still
        unknown, and at once with no deadline when a connection cannot be made."""
        if self.ref is None:
            self._known = Lost(LOST_UNANSWERED)
            return self._known
        params = {"ref": self.ref, "until": until, "timeout_ms": 0}
        client = self._via
        while True:
            if client is None or not _is_open(client):
                # The connection the request went on has closed: the endpoint has
                # recorded the delivery or never will, so a new one may ask.
                try:
                    client = self.session._short.get()
                except _NotWritten as exc:
                    if exc.not_serving:
                        self._known = Lost(LOST_UNANSWERED, ref=self.ref)
                        return self._known
                    # With no deadline a wait that cannot connect would ask again
                    # forever; it hands the caller a TimeoutError to decide on instead.
                    if deadline is None or time.monotonic() >= deadline:
                        raise self._timed_out(until, deadline) from None
                    time.sleep(0.05)
                    continue
                self._via = client
            left = self._left(deadline)
            try:
                answer = _call(client, "wait", params,
                               READ_S if left is None else min(READ_S, max(left, 0.05)))
            except _Unanswered:
                if deadline is not None and time.monotonic() >= deadline:
                    raise self._timed_out(until, deadline) from None
                continue
            except ipc.RpcError as exc:
                if exc.code == ERROR_GONE:
                    self._known = Lost(f"{REFUSED_GONE}: {exc.message}", ref=self.ref)
                    return self._known
                raise _rpc_error(exc, self.session.address, "wait") from None
            if not isinstance(answer, dict):
                raise TurnsError(f"the endpoint at {self.session.address} answered wait "
                                 f"with {answer!r}")
            found = answer.get("delivery")
            if isinstance(found, str):
                self.id = found
                self._via = None
            if answer.get("pending"):
                if self.id is None:
                    raise TurnsError(f"the endpoint at {self.session.address} answered a "
                                     f"look-up by ref pending with no delivery")
                return None
            value = self.session._value(answer.get("result"), "wait",
                                        (Started, Ended, Refused, Lost))
            if self.id is None:
                # No delivery carries the ref: the request never reached the record.
                self._known = value
            return value

    def _wait_id(self, until: str, deadline: float | None):
        """Wait by id on a connection of its own. A connection that closes while the
        endpoint lives is replaced once. `Lost` `unanswered` only when nothing listens at
        the address; any other connect that fails raises `TimeoutError`."""
        reconnected = False
        while True:
            try:
                client, _, _ = _connect(self.session.address)
            except _NotWritten as exc:
                if exc.not_serving:
                    return Lost(LOST_UNANSWERED, delivery=self.id, ref=self.ref)
                # The endpoint listens but did not get through the hello or `session`:
                # that says nothing of a delivery an earlier call recorded.
                raise self._timed_out(until, deadline) from None
            left = self._left(deadline)
            try:
                answer = _call(client, "wait", {"delivery": self.id, "until": until,
                                                "timeout_ms": _ms(left)},
                               _plus(left, READ_S))
            except _Unanswered as exc:
                if isinstance(exc.cause, ipc.Timeout) or reconnected:
                    raise self._timed_out(until, deadline) from None
                reconnected = True
                continue
            except ipc.RpcError as exc:
                if exc.code == ERROR_GONE:
                    return Lost(f"{REFUSED_GONE}: {exc.message}", delivery=self.id,
                                ref=self.ref)
                raise _rpc_error(exc, self.session.address, "wait") from None
            finally:
                client.close()
            if not isinstance(answer, dict):
                raise TurnsError(f"the endpoint at {self.session.address} answered wait "
                                 f"with {answer!r}")
            if answer.get("pending"):
                raise self._timed_out(until, deadline)
            return self.session._value(answer.get("result"), "wait",
                                       (Started, Ended, Refused, Lost))


class Watch:
    """A session watched: `facts` as of the subscription, then iterate the events. The
    iteration ends after one `Gone`: the endpoint's own `session.gone`, or one this
    makes when the connection ends without it. `close()` stops watching."""

    def __init__(self, client: ipc.Client, facts: Facts):
        self.facts = facts
        self._client = client
        self._closed = False

    def __iter__(self) -> Iterator:
        try:
            for method, params in self._client.notifications():
                try:
                    event = from_notification(method, params)
                except ValueError as exc:
                    log.info("a watch skipped a notification it cannot read: %s", exc)
                    continue
                yield event
                if isinstance(event, Gone):
                    return
            if not self._closed:
                yield Gone("the endpoint went away")
        finally:
            self._client.close()

    def __enter__(self) -> Watch:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True
        self._client.close()


class StopHandler:
    """The session's stop handler. `close()` gives the slot up."""

    def __init__(self, client: ipc.Client, on_stop: Callable[[StopFacts], str | None]):
        self._client = client
        self._on_stop = on_stop
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="turns-stop-handler")
        self._thread.start()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def _run(self) -> None:
        try:
            for method, params in self._client.notifications():
                if method != protocol.STOP_ASKED or not isinstance(params, dict):
                    continue
                self._answer(params.get("ask"), params.get("facts"))
        finally:
            self._closed.set()
            self._client.close()

    def _answer(self, ask: Any, raw: Any) -> None:
        try:
            facts = from_wire(raw)
            reason = self._on_stop(facts)
        except Exception as exc:  # noqa: BLE001 - a broken handler lets the turn end
            log.warning("on_stop raised %s: %s; the turn ends", type(exc).__name__, exc)
            reason = None
        if not isinstance(reason, str) or not reason:
            reason = None
        try:
            answer = self._client.call("stops.answer", {"ask": ask, "reason": reason},
                                       timeout=READ_S)
        except ipc.RpcError as exc:
            log.warning("the endpoint refused the answer to Stop %s: %s", ask, exc)
            return
        except (ipc.IpcError, OSError) as exc:
            log.warning("the answer to Stop %s went unanswered (%s): whether it was taken "
                        "is unknown", ask, exc)
            return
        if isinstance(answer, dict) and answer.get("taken") is False:
            log.info("the answer to Stop %s came too late: the turn ended", ask)

    def close(self) -> None:
        self._client.close()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)


# -- finding ------------------------------------------------------------------------------


def _candidates() -> list[tuple[Path, dict[str, Any], int]]:
    """Every endpoint directory with a readable `hello.json`, newest first: version 2's
    under `v2/`, and version 1's directly under the runtime directory, each with its
    version. A directory with no readable `hello.json` is an endpoint starting."""
    root = runtime_dir()
    found = []
    for directory in protocol.v2_directories(root):
        hello = protocol.read_hello(directory)
        if hello is not None:
            found.append((directory, hello, 2))
    for directory in protocol.v1_directories(root):
        hello = protocol.read_hello(directory)
        if hello is not None:
            found.append((directory, hello, 1))

    def started(item) -> float:
        value = item[1].get("started")
        return value if isinstance(value, (int, float)) else 0

    found.sort(key=started, reverse=True)
    return found


def _probe(directory: Path, hello: dict[str, Any]) -> Session | None:
    """The version 2 endpoint in `directory` as a `Session`, when a connection to its
    address passes the hello and `session`. When the connect fails, the directory is
    cleared on `moxi.ipc.sweep`'s word. Raises `VersionError` when the endpoint refuses
    this library's versions."""
    address = hello.get("address")
    if not isinstance(address, str) or not address:
        return None
    try:
        client, agreed, info = _connect(address)
    except _NotWritten as exc:
        if isinstance(exc.error, VersionError):
            raise exc.error from None
        if exc.connect:
            verdict = protocol.sweep(directory, address)
            if verdict != protocol.SWEPT_LIVE:
                log.info("swept the dead endpoint directory %s", directory)
        return None
    client.close()
    session = Session(address, info, directory=directory)
    session._capabilities = frozenset(agreed.capabilities)
    return session


def _names(info: dict[str, Any], session_id: str) -> bool:
    return (info.get("session_id") == session_id
            or session_id in (info.get("earlier") or ()))


def sessions() -> list[Session]:
    """Every live version 2 endpoint, newest first. Dead ones are swept; version 1
    directories are left alone and not listed."""
    out = []
    for directory, hello, version in _candidates():
        if version != 2:
            continue
        try:
            session = _probe(directory, hello)
        except VersionError as exc:
            log.info("%s", exc)
            continue
        if session is not None:
            out.append(session)
    return out


def find(*, session_id: str | None = None, pid: int | None = None,
         proc: ProcView | None = None) -> Session | None:
    """The live endpoint of a session, or None.

    `session_id` matches an endpoint's current session id, or else an earlier id of the
    same launch. `pid` matches the harness process or any process under it: the walk goes
    up from `pid`, and the nearest harness process met wins. The match counts only when
    the endpoint answers and its answer still names it. A live version 1 endpoint that is
    the best match raises `VersionError`.
    """
    if (session_id is None) == (pid is None):
        raise TypeError("find() takes exactly one of session_id= or pid=")
    candidates = _candidates()
    if session_id is not None:
        current = [c for c in candidates if c[1].get("session_id") == session_id]
        earlier = [c for c in candidates
                   if session_id in (c[1].get("earlier") or ()) and c not in current]
        order = current + earlier
    else:
        by_pid: dict[int, list] = {}
        for c in candidates:
            if isinstance(c[1].get("pid"), int):
                by_pid.setdefault(c[1]["pid"], []).append(c)
        order = []
        for ancestor in (proc or DEFAULT_PROC).ancestry(pid):
            order.extend(by_pid.get(ancestor, ()))
    for directory, hello, version in order:
        if version == 1:
            answer = protocol.v1_hello(directory)
            if answer is None:
                continue
            if session_id is not None and not _names(answer, session_id):
                continue
            raise _v1_error(directory)
        session = _probe(directory, hello)
        if session is None:
            continue
        if session_id is not None and not _names(session._info, session_id):
            session.close()
            continue
        return session
    return None


def wait_ready(*, session_id: str | None = None, pid: int | None = None,
               timeout: float, proc: ProcView | None = None) -> Session:
    """The `Session` once an endpoint names the session and its `facts().ready` is set:
    the session started, resumed, was cleared or compacted. Polls `find` every 100 ms,
    backing off to 1 s, and reads `ready` from the facts, so a Ready raised before this
    looked is not missed. Raises `TimeoutError` when `timeout` passes first, and
    `VersionError` as `find` does."""
    if (session_id is None) == (pid is None):
        raise TypeError("wait_ready() takes exactly one of session_id= or pid=")
    deadline = time.monotonic() + timeout
    delay = 0.1
    while True:
        session = find(session_id=session_id, pid=pid, proc=proc)
        if session is not None:
            try:
                if session.facts().ready is not None:
                    return session
            except TurnsError as exc:
                log.debug("wait_ready read no facts yet: %s", exc)
            session.close()
        left = deadline - time.monotonic()
        if left <= 0:
            who = f"session {session_id}" if session_id is not None else f"pid {pid}"
            raise TimeoutError(f"no ready endpoint for {who} in {timeout} s")
        time.sleep(min(delay, left))
        delay = min(delay * 2, 1.0)


__all__ = ["CONNECT_S", "READ_S", "LIVE_READ_S", "EXIT_BOUND_S", "NOT_EXITED",
           "TurnsError", "VersionError", "Session", "Delivery", "Watch", "StopHandler",
           "runtime_dir", "sessions", "find", "wait_ready"]
