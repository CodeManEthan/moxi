"""The endpoint's caller wire: turns' methods on `moxi.ipc`, mapped to the endpoint core.

`moxi.ipc.serve` answers each connection's `rpc.hello` itself and hands every later
request here, one at a time per connection, in order. This module checks a request's
params against the contract (`contracts/socket-protocol.md`), checks the session offers
what it asks for, and calls the core (`endpoint.Endpoint`), whose answer it writes back.

The errors, each a `moxi.ipc.RpcError`: -32601 for a method turns does not have, -32602
for params that break the contract, and turns' own: `gone` (the endpoint is ending),
`capability` (the session does not offer the method or option, `data` naming it) and
`stop-handler-taken`. Every reason the session's state decides is a value instead.

A handler on the loop never waits on the harness or on a blocking write. `session`,
`deliver`, `steer`, `stops.handle`, `stops.answer` and a `wait` with `timeout_ms` 0 read or
write the core's record and answer at once: `deliver` and `steer` record the delivery and
put its hand-off on the endpoint's ordered worker in one locked step, before the handler's
first `await`. `environ` reads the endpoint's own copy of the session's environment and answers at once.
`usage.codex_limits` and `mcp.reload` (a Codex host's) run on a thread of their own: the
first waits on one app-server request, the second on the host's MCP settle, which its
deadline bounds. `usage.codex_limits` with `cached` answers from the host's record at once.
`facts`, `agents` and `watch` await the endpoint's bounded live read, which
runs on its reader threads, so they answer within the harness's live bound. A `wait` with
a bound or none, and a `command`, which waits for its settle, run on a thread of their
own, so the loop keeps serving every other connection while they wait.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import re
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from moxi import ipc

from .results import (CAPABILITIES, COMMAND_WAIT_S, COMMANDS, ENVIRON_MAX_NAMES,
                      ERROR_CAPABILITY, ERROR_GONE, ERROR_STOP_HANDLER_TAKEN, REFUSED_GONE,
                      STOP_SHELLS_WAIT_S, Refused, check_ref)

if TYPE_CHECKING:
    from .endpoint import Endpoint

METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
#: A name `environ` may ask: an environment variable's name.
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def gone(reason: str | None) -> ipc.RpcError:
    return ipc.RpcError(ERROR_GONE, f"the endpoint is ending: {reason or 'it ended'}")


def lacks(capability: str) -> ipc.RpcError:
    return ipc.RpcError(ERROR_CAPABILITY, f"this session does not offer {capability}",
                        {"capability": capability})


def invalid(message: str) -> ipc.RpcError:
    return ipc.RpcError(INVALID_PARAMS, message)


class Rpc:
    """The handler `moxi.ipc.serve` calls for one endpoint: `await rpc(conn, method,
    params)`, and `rpc.closed(conn)` once a connection has closed."""

    def __init__(self, endpoint: Endpoint):
        self.ep = endpoint
        #: Calls waiting on a thread for their answer (`wait`, `command`). The endpoint's
        #: stop lets them answer before it closes the server.
        self.answering = 0
        self._methods: dict[str, Callable[[ipc.Connection, dict], Any]] = {
            "session": self._session,
            "facts": self._facts,
            "agents": self._agents,
            "deliver": self._deliver,
            "steer": self._steer,
            "wait": self._wait,
            "command": self._command,
            "watch": self._watch,
            "stops.handle": self._stops_handle,
            "stops.answer": self._stops_answer,
            "environ": self._environ,
            "usage.codex_limits": self._codex_limits,
            "mcp.reload": self._mcp_reload,
        }

    async def __call__(self, conn: ipc.Connection, method: str, params: object) -> Any:
        handler = self._methods.get(method)
        if handler is None:
            raise ipc.RpcError(METHOD_NOT_FOUND, f"method not found: {method}; turns' "
                               "methods are " + ", ".join(self._methods))
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise invalid("params is an object")
        self._alive()
        return await handler(conn, params)

    def closed(self, conn: ipc.Connection) -> None:
        """A connection closed: it no longer watches, and gives up the stop handler."""
        self.ep.drop_connection(conn)

    # -- the methods --------------------------------------------------------------------

    async def _session(self, conn, params: dict) -> dict:
        return self.ep.session()

    async def _facts(self, conn, params: dict) -> dict:
        facts = await self.ep.facts_async()
        return facts.to_wire()

    async def _agents(self, conn, params: dict) -> dict:
        self._needs("agents")
        agents = await self.ep.agents_async()
        return agents.to_wire()

    async def _deliver(self, conn, params: dict) -> dict:
        text = _text(params)
        ref = _ref(params)
        only_if_idle = _bool(params, "only_if_idle")
        if_turn_number = _opt_int(params, "if_turn_number")
        refuse_draft = _bool(params, "refuse_draft")
        refuse_agents = _bool(params, "refuse_agents")
        if not only_if_idle and (if_turn_number is not None or refuse_draft
                                 or refuse_agents):
            raise invalid("if_turn_number, refuse_draft and refuse_agents are valid only "
                          "with only_if_idle: true")
        self._needs("only-if-idle" if only_if_idle else "deliver")
        # Recorded, checked and handed to the worker before this handler's first await.
        d = self.ep.deliver(text, ref=ref, only_if_idle=only_if_idle,
                            if_turn_number=if_turn_number, refuse_draft=refuse_draft,
                            refuse_agents=refuse_agents)
        await self._hook("deliver", d)
        return self._handed(d)

    async def _steer(self, conn, params: dict) -> dict:
        text = _text(params)
        ref = _ref(params)
        self._needs("steer")
        d = self.ep.steer(text, ref=ref)
        await self._hook("steer", d)
        return self._handed(d)

    async def _in_thread(self, fn: Callable[..., Any], *args: Any) -> Any:
        """`in_thread`, counted in `answering` until its result is back on the loop. The
        handler's answer is queued in the same step, with no await after this one."""
        self.answering += 1
        try:
            return await in_thread(fn, *args)
        finally:
            self.answering -= 1

    async def _hook(self, method: str, d) -> None:
        hook = self.ep.answer_hook
        if hook is not None:
            await hook(method, d)

    async def _wait(self, conn, params: dict) -> dict:
        delivery = params.get("delivery")
        ref = _ref(params)
        if delivery is not None and not isinstance(delivery, str):
            raise invalid("delivery is a delivery's id, a string")
        if (delivery is None) == (ref is None):
            raise invalid("wait takes exactly one of delivery and ref")
        until = params.get("until")
        if until not in ("started", "ended"):
            raise invalid('until is "started" or "ended"')
        timeout = _timeout_ms(params)
        if delivery is not None:
            if timeout == 0:
                value = self.ep.wait(delivery, until, 0)
            else:
                value = await self._in_thread(self.ep.wait, delivery, until, timeout)
            return _waited(value)
        if timeout == 0:
            delivery, value = self.ep.wait_ref(ref, until, 0)
        else:
            delivery, value = await self._in_thread(self.ep.wait_ref, ref, until, timeout)
        return {"delivery": delivery, **_waited(value)}

    async def _command(self, conn, params: dict) -> dict:
        name = params.get("name")
        if name not in COMMANDS:
            raise invalid("name is one of " + ", ".join(COMMANDS))
        if_turn_number = _opt_int(params, "if_turn_number")
        timeout = _timeout_ms(params)
        if "stop_shells" in params and name == "exit":
            raise invalid("stop_shells is valid with clear and compact only")
        stop_shells = _bool(params, "stop_shells")
        self._needs(name)
        if stop_shells:
            self._needs("stop-shells")
        if timeout is None:
            timeout = COMMAND_WAIT_S[name] + (STOP_SHELLS_WAIT_S if stop_shells else 0)
        result = await self._in_thread(lambda: self.ep.command(
            name, if_turn_number=if_turn_number, timeout=timeout, stop_shells=stop_shells))
        if isinstance(result, Refused) and result.reason == REFUSED_GONE:
            raise gone(self.ep.gone_reason)
        return {"result": result.to_wire()}

    async def _watch(self, conn, params: dict) -> dict:
        first = self.ep.reading()
        live = await self.ep.live_async()
        # Then, with no await between: the facts and the subscription in one hold of the
        # endpoint's lock, and the answer queued before any notification after it.
        facts = self.ep.watch(conn, first, live)
        if facts is None:
            raise gone(self.ep.gone_reason)
        return {"facts": facts.to_wire()}

    async def _stops_handle(self, conn, params: dict) -> dict:
        self._needs("stop-handler")
        taken = self.ep.handle_stops(conn)
        if taken is None:
            raise gone(self.ep.gone_reason)
        if not taken:
            raise ipc.RpcError(ERROR_STOP_HANDLER_TAKEN,
                               "another connection handles this session's stops")
        return {}

    async def _stops_answer(self, conn, params: dict) -> dict:
        ask = params.get("ask")
        if isinstance(ask, bool) or not isinstance(ask, int):
            raise invalid("ask is the stop.asked notification's ask, an integer")
        if "reason" not in params:
            raise invalid("reason is a string, or null to let the turn end")
        reason = params["reason"]
        if reason is not None and not isinstance(reason, str):
            raise invalid("reason is a string, or null to let the turn end")
        return {"taken": self.ep.stop_answer(ask, reason)}

    async def _environ(self, conn, params: dict) -> dict:
        """The named variables in the endpoint's copy of the session's environment, null
        for one that is unset. Only the names asked; no other process is read."""
        names = params.get("names")
        if not isinstance(names, list):
            raise invalid("names is a list of environment variable names")
        if len(names) > ENVIRON_MAX_NAMES:
            raise invalid(f"names lists at most {ENVIRON_MAX_NAMES} names")
        for name in names:
            if not isinstance(name, str) or not ENV_NAME.fullmatch(name):
                raise invalid(f"{name!r} is not an environment variable name "
                              "([A-Za-z_][A-Za-z0-9_]*)")
        self._needs("environ")
        env = self.ep.env
        return {"values": {name: env.get(name) for name in names}}

    async def _codex_limits(self, conn, params: dict) -> dict:
        """A Codex host's rate-limits reading: read now on its app-server, or with
        `cached` the last one it saw."""
        cached = _bool(params, "cached")
        self._needs("usage")
        read = getattr(self.ep.harness, "codex_limits", None)
        if read is None:
            raise ipc.RpcError(ERROR_CAPABILITY, f"this {self.ep.harness.name} session "
                               "does not offer usage.codex_limits", {"capability": "usage"})
        if cached:
            return read(cached=True)
        return await self._in_thread(read)

    async def _mcp_reload(self, conn, params: dict) -> dict:
        """A Codex host's MCP settle: `{reloaded, servers}` once it ends or its deadline
        passes."""
        self._needs("mcp-token")
        settle = getattr(self.ep.harness, "mcp_reload", None)
        if settle is None:
            raise lacks("mcp-token")
        return await self._in_thread(settle)

    # -- checks -------------------------------------------------------------------------

    def _alive(self) -> None:
        if self.ep.is_gone:
            raise gone(self.ep.gone_reason)

    def _needs(self, capability: str) -> None:
        if capability not in self.ep.capabilities:
            raise lacks(capability)

    def _handed(self, d) -> dict:
        """A `deliver` or `steer` answer: the delivery's id, the caller's ref echoed, and
        the refusal the endpoint decided from its own record, when it did."""
        early = d.early
        if isinstance(early, Refused) and early.reason == REFUSED_GONE:
            raise gone(self.ep.gone_reason)
        answer: dict[str, Any] = {"delivery": d.id, "ref": d.ref}
        if early is not None:
            answer["result"] = d.stamp(early).to_wire()
        return answer


def capabilities(can) -> tuple[str, ...]:
    """The capabilities a harness's `can` offers, in the contract's order. Names the
    contract does not have are left out."""
    return tuple(name for name in CAPABILITIES if name in can)


def _waited(value) -> dict:
    return {"pending": True} if value is None else {"result": value.to_wire()}


def _text(params: dict) -> str:
    text = params.get("text")
    if not isinstance(text, str) or not text:
        raise invalid("text is a non-empty string")
    return text


def _ref(params: dict) -> str | None:
    try:
        return check_ref(params.get("ref"))
    except ValueError as exc:
        raise invalid(str(exc)) from None


def _bool(params: dict, key: str) -> bool:
    value = params.get(key, False)
    if not isinstance(value, bool):
        raise invalid(f"{key} is true or false")
    return value


def _opt_int(params: dict, key: str) -> int | None:
    value = params.get(key)
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise invalid(f"{key} is an integer or null")
    return value


def _timeout_ms(params: dict) -> float | None:
    """`timeout_ms` in seconds: None when absent or null."""
    value = params.get("timeout_ms")
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise invalid("timeout_ms is a number of milliseconds of at least 0, or null")
    return value / 1000


def in_thread(fn: Callable[..., Any], *args: Any) -> asyncio.Future:
    """Run `fn(*args)` on a thread of its own and give the loop a future of its result.

    Not the loop's executor: a `wait` with no bound holds its thread for as long as it
    waits, and no number of such waits may hold up another caller's call.
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(ok: bool, value: Any) -> None:
        if future.done():
            return
        if ok:
            future.set_result(value)
        else:
            future.set_exception(value)

    def run() -> None:
        try:
            value, ok = fn(*args), True
        except BaseException as exc:  # noqa: BLE001 - handed to the loop to raise there
            value, ok = exc, False
        with contextlib.suppress(RuntimeError):     # the loop has closed
            loop.call_soon_threadsafe(settle, ok, value)

    threading.Thread(target=run, daemon=True, name="turns-endpoint-call").start()
    return future


__all__ = ["Rpc", "capabilities", "in_thread", "COMMAND_WAIT_S", "STOP_SHELLS_WAIT_S"]
