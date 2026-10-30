"""The endpoint's caller wire: turns' methods on `moxi.ipc`, mapped to the endpoint core.

`moxi.ipc.serve` answers each connection's `rpc.hello` itself and hands every later
request here, one at a time per connection, in order. This module checks a request's
params against the contract (`contracts/socket-protocol.md`), checks the session offers
what it asks for, and calls the core (`endpoint.Endpoint`), whose answer it writes back.

The errors, each a `moxi.ipc.RpcError`: -32601 for a method turns does not have, -32602
for params that break the contract, and turns' own: `gone` (the endpoint is ending),
`capability` (the session does not offer the method or option, `data` naming it) and
`stop-handler-taken`. Every reason the session's state decides is a value instead.

A handler on the loop never waits on the harness. `session`, `stops.handle`,
`stops.answer`, `watch`'s registration and a `wait` with `timeout_ms` 0 read the core's
record and answer at once; every call that may wait (a live read, a bounded `wait`, a
`command`, a hand-off to the adapter) runs on a thread of its own, so the loop keeps
serving every other connection while it waits.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from moxi import ipc

from .results import (CAPABILITIES, COMMAND_WAIT_S, COMMANDS, ERROR_CAPABILITY,
                      ERROR_GONE, ERROR_STOP_HANDLER_TAKEN, REFUSED_GONE,
                      STOP_SHELLS_WAIT_S, Refused, check_ref)

if TYPE_CHECKING:
    from .endpoint import Endpoint

METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


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
        facts = await in_thread(self.ep.facts)
        return facts.to_wire()

    async def _agents(self, conn, params: dict) -> dict:
        self._needs("agents")
        agents = await in_thread(self.ep.agents)
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
        if only_if_idle:
            self._needs("only-if-idle")
            d = await in_thread(lambda: self.ep.start(
                text, if_turn_number=if_turn_number, refuse_draft=refuse_draft,
                refuse_agents=refuse_agents, ref=ref))
        else:
            self._needs("deliver")
            d = await in_thread(lambda: self.ep.deliver(text, ref=ref))
        return self._handed(d)

    async def _steer(self, conn, params: dict) -> dict:
        text = _text(params)
        ref = _ref(params)
        self._needs("steer")
        d = await in_thread(lambda: self.ep.steer(text, ref=ref))
        return self._handed(d)

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
                value = await in_thread(self.ep.wait, delivery, until, timeout)
            return _waited(value)
        if timeout == 0:
            delivery, value = self.ep.wait_ref(ref, until, 0)
        else:
            delivery, value = await in_thread(self.ep.wait_ref, ref, until, timeout)
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
        result = await in_thread(lambda: self.ep.command(
            name, if_turn_number=if_turn_number, timeout=timeout, stop_shells=stop_shells))
        if isinstance(result, Refused) and result.reason == REFUSED_GONE:
            raise gone(self.ep.gone_reason)
        return {"result": result.to_wire()}

    async def _watch(self, conn, params: dict) -> dict:
        first, live = await in_thread(self.ep.watch_read)
        # Back on the loop: the facts and the subscription in one hold of the endpoint's
        # lock, and the answer queued before any notification the loop runs after it.
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
