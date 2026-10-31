"""A stand-in turns endpoint for comms' adapter tests, written to turns' socket protocol
(version 2, turns' `contracts/socket-protocol.md`), the contract every caller speaks. It
serves on `moxi.ipc` at the address turns' own endpoints use and writes its `hello.json`
where `moxi_turns.find` reads it, so an adapter reaches it through the real library
(`find`, `handle_stops`, `watch`, `deliver`, `steer`, `wait`) and these tests exercise
comms against the published protocol, never against turns' internals, which comms may
not import.

The test plays the harness: `start_turn` and `end_turn` are the session's turns, `ready`
a SessionStart, `stop` a Stop the stop handler is asked about, `go_away` the endpoint's
end (a plugin reload or a crash). With `auto` on (the default) a delivery to an idle
session starts its own turn at once, as turns' fake harness does; a delivery made while a
turn runs waits for that turn's end.

What a hand-in's outcome will be is scripted ahead of it, one entry per hand-in, in
`script`:

    refuse(reason)        the answer carries `refused` (the endpoint's own record decided)
    refuse_later(reason)  answered with an id; the value `refused` comes through `wait`
    lose(reason)          answered with an id; the value `lost` comes through `wait`
    drop()                recorded, then the connection closes with no answer: the
                          library's unknown outcome, resolved by `ref` on a new connection
    late(seconds, then)   recorded as `then` would record it (None: as no entry would),
                          answered after `seconds`: past the library's read bound it is
                          the unknown outcome, resolved on the same connection
    gone()                answered with the `gone` error, recording nothing
    hold()                a steer only: recorded, and the harness neither takes nor
                          refuses it until the test calls `take` or `settle`

A steer with no script entry is taken at once (`steer_auto`), and a steer while no turn
runs is refused `idle` in its answer, as the contract says.

The Codex adapter's tests reach this endpoint the same way: build it with
`harness="codex"` and `capabilities=CODEX`, and drive the turns by hand.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import itertools
import json
import os
import threading
import time
import uuid
from pathlib import Path

from moxi import ipc

VERSION = 2

#: The capabilities each harness offers, as the contract's table lists them.
CLAUDE = frozenset({"deliver", "only-if-idle", "steer", "stop-handler", "clear", "exit",
                    "compact", "agents", "stop-shells"})
CODEX = frozenset({"deliver", "only-if-idle", "steer", "stop-handler", "compact",
                   "agents", "stop-shells"})
OPENCODE = frozenset({"deliver", "only-if-idle", "compact", "agents"})

INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601
ERROR_GONE = 1001
ERROR_CAPABILITY = 1002
ERROR_STOP_HANDLER_TAKEN = 1003


def runtime_root() -> Path:
    """turns' runtime directory as the library finds it (`MOXI_TURNS_RUNTIME_DIR`)."""
    return Path(os.environ["MOXI_TURNS_RUNTIME_DIR"]) / "turns"


# -- the script ---------------------------------------------------------------------------

def refuse(reason: str) -> tuple:
    return ("refuse", reason)


def refuse_later(reason: str) -> tuple:
    return ("refuse-later", reason)


def lose(reason: str) -> tuple:
    return ("lose", reason)


def drop() -> tuple:
    return ("drop",)


def late(seconds: float, then: tuple | None = None) -> tuple:
    return ("late", seconds, then)


def gone() -> tuple:
    return ("gone",)


def hold() -> tuple:
    return ("hold",)


class FakeEndpoint:
    def __init__(self, *, pid: int | None = None, session_id: str | None = "s-1",
                 harness: str = "claude", auto: bool = True, steer_auto: bool = True,
                 turn_number: int = 0, capabilities=CLAUDE):
        self.pid = os.getpid() if pid is None else pid
        self.session_id = session_id
        self.earlier: list[str] = []
        self.harness = harness
        self.auto = auto
        self.steer_auto = steer_auto
        self.turn_number = turn_number
        self.capabilities = frozenset(capabilities)
        self.nonce = uuid.uuid4().hex[:12]
        self.name = f"{harness}-{self.nonce}"
        self.address = ipc.address("turns", self.name)
        self.dir = runtime_root() / "v2" / self.name
        #: The open turn: {"id", "opening" (a delivery id or None), "deliveries"
        #: [(delivery id, how)]}, or None.
        self.turn: dict | None = None
        #: Every delivery the endpoint recorded, by id: {"text", "ref", "method",
        #: "started", "ended"} (the values as they go on the wire, None while pending).
        self.deliveries: dict[str, dict] = {}
        self.waiting: list[str] = []           # deliveries held for the turn's end
        self.texts: list[str] = []             # every text recorded, in order
        #: Every hand-in recorded, in order: (method, delivery id, ref, text).
        self.calls: list[tuple[str, str, str | None, str]] = []
        self.script: list[tuple] = []
        self.watchers: list = []               # moxi.ipc Connections watching
        self.stop_handler = None               # the Connection handling stops
        self.handlers = 0                      # stops.handle requests answered ok
        self.stop_answers: dict[int, object] = {}
        #: A stalled endpoint that still listens: while this is an unset Event, once a
        #: hand-in is recorded, each new connection's `session` waits until it is set.
        self.session_gate: threading.Event | None = None
        self._ids = itertools.count(1)
        self._turns = itertools.count(1)
        self._asks = itertools.count(1)
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._stopped = False
        self._server = None
        self._loop = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ life
    def open(self) -> FakeEndpoint:
        ready: concurrent.futures.Future = concurrent.futures.Future()

        def run():
            from moxi import aio

            async def main():
                try:
                    self._server = await ipc.serve(
                        self.address, self._handle,
                        hello=ipc.Hello((VERSION, VERSION), self.capabilities),
                        on_close=self._closed)
                except BaseException as exc:
                    ready.set_exception(exc)
                    raise
                self._loop = asyncio.get_running_loop()
                ready.set_result(None)
                await self._server.serve_forever()

            with contextlib.suppress(Exception):
                aio.run(main)

        self._thread = threading.Thread(target=run, daemon=True, name=f"fake-{self.name}")
        self._thread.start()
        ready.result(5)
        self.dir.mkdir(parents=True, mode=0o700)
        self._write_hello()
        return self

    def info(self) -> dict:
        return {"harness": self.harness, "pid": self.pid, "endpoint_pid": os.getpid(),
                "session_id": self.session_id, "earlier": list(self.earlier),
                "cwd": os.getcwd(), "nonce": self.nonce, "started": time.time(),
                "endpoint_version": "0.0.0-test", "mod_version": None}

    def _write_hello(self) -> None:
        hello = {"address": self.address, "protocol": VERSION, **self.info()}
        tmp = self.dir / ".hello.json.tmp"
        tmp.write_text(json.dumps(hello))
        os.replace(tmp, self.dir / "hello.json")

    def go_away(self, reason: str = "the endpoint went away") -> None:
        """The endpoint ends: every unsettled delivery reads `lost` (not untied),
        watchers get `session.gone`, then the server, its socket and the directory go."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            for did, d in self.deliveries.items():
                if d["ended"] is None:
                    lost = {"kind": "lost", "reason": reason, "delivery": did,
                            "ref": d["ref"]}
                    d["ended"] = lost
                    if d["started"] is None:
                        d["started"] = lost
            self._cond.notify_all()
            self._emit("session.gone", {"reason": reason})
        if self._loop is not None:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._server.close)
        if self._thread is not None:
            self._thread.join(5)
        for name in ("hello.json", ".hello.json.tmp"):
            (self.dir / name).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            self.dir.rmdir()

    close = go_away

    # ------------------------------------------------------------------ the harness
    def start_turn(self, delivery: str | None = None) -> str:
        """A main-loop turn starts, opened by `delivery` or by the person (None)."""
        with self._lock:
            turn_id = f"t{next(self._turns)}"
            self.turn_number += 1
            self.turn = {"id": turn_id, "opening": delivery, "deliveries": [],
                         "started_at": time.time()}
            refs = []
            if delivery is not None:
                d = self.deliveries[delivery]
                d["started"] = {"kind": "started", "turn_id": turn_id,
                                "delivery": delivery, "ref": d["ref"]}
                self.turn["deliveries"].append((delivery, "opened"))
                refs.append({"id": delivery, "ref": d["ref"], "how": "opened"})
                self._cond.notify_all()
            self._emit("turn.started", {"turn_id": turn_id, "session_id": self.session_id,
                                        "turn_number": self.turn_number,
                                        "deliveries": refs, "command": None})
            return turn_id

    def take(self, delivery: str) -> None:
        """The harness confirms a held steer: it joins the running turn."""
        with self._lock:
            assert self.turn is not None, "no turn is open"
            d = self.deliveries[delivery]
            d["started"] = {"kind": "started", "turn_id": self.turn["id"],
                            "delivery": delivery, "ref": d["ref"]}
            self.turn["deliveries"].append((delivery, "steered"))
            self._cond.notify_all()
            self._emit("turn.steered", {"turn_id": self.turn["id"],
                                        "session_id": self.session_id,
                                        "delivery": {"id": delivery, "ref": d["ref"]}})

    def end_turn(self, outcome: str = "answer") -> str:
        with self._lock:
            turn, self.turn = self.turn, None
            assert turn is not None, "no turn is open"
            named = []
            for did, how in turn["deliveries"]:
                d = self.deliveries[did]
                d["ended"] = {"kind": "ended", "turn_id": turn["id"], "outcome": outcome,
                              "delivery": did, "ref": d["ref"]}
                named.append({"id": did, "ref": d["ref"], "how": how})
            # A steer the harness had not confirmed when the turn ended is refused
            # `idle`: it never went in.
            for did, d in self.deliveries.items():
                if d["method"] == "steer" and d["started"] is None:
                    self._settle(did, {"kind": "refused", "reason": "idle"})
            self._cond.notify_all()
            self._emit("turn.ended", {"turn_id": turn["id"], "session_id": self.session_id,
                                      "turn_number": self.turn_number,
                                      "outcome": outcome, "deliveries": named,
                                      "command": None})
            if self.waiting and self.auto:
                self.start_turn(self.waiting.pop(0))
            return turn["id"]

    def ready(self, source: str, session_id: str | None = None) -> None:
        with self._lock:
            if session_id is not None and session_id != self.session_id:
                if self.session_id:
                    self.earlier.append(self.session_id)
                self.session_id = session_id
                self.turn_number = 0
                self._write_hello()
            self._emit("session.ready", {"source": source, "session_id": self.session_id})

    def stop(self, facts: dict | None = None, timeout: float = 3.0):
        """A Stop: ask the stop handler and return its reason (None: the turn ends; also
        when there is no handler or it does not answer in time)."""
        facts = {"kind": "stop", "turn_id": (self.turn or {}).get("id"),
                 "session_id": self.session_id, "stop_hook_active": False,
                 "task_types": [], "crons": 0, **(facts or {})}
        with self._lock:
            handler = self.stop_handler
            if handler is None:
                return None
            ask = next(self._asks)
        handler.notify_threadsafe("stop.asked", {"ask": ask, "facts": facts})
        deadline = time.monotonic() + timeout
        with self._lock:
            while ask not in self.stop_answers:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(left)
            return self.stop_answers.pop(ask)

    def settle(self, delivery: str, result: dict) -> None:
        """Settle a recorded delivery by hand (a `refused` or `lost` value)."""
        with self._lock:
            self._settle(delivery, result)

    def _settle(self, delivery: str, result: dict) -> None:
        d = self.deliveries[delivery]
        value = {**result, "delivery": delivery, "ref": d["ref"]}
        d["ended"] = value
        if d["started"] is None:
            d["started"] = value
        if delivery in self.waiting:
            self.waiting.remove(delivery)
        self._cond.notify_all()

    # ------------------------------------------------------------------ the wire
    def _emit(self, method: str, params: dict) -> None:
        """A notification to every watcher, under the lock that orders them."""
        for conn in list(self.watchers):
            conn.notify_threadsafe(method, params)

    def _closed(self, conn) -> None:
        with self._lock:
            if conn in self.watchers:
                self.watchers.remove(conn)
            if self.stop_handler is conn:
                self.stop_handler = None

    async def _handle(self, conn, method: str, params):
        params = params if isinstance(params, dict) else {}
        if self._stopped:
            raise ipc.RpcError(ERROR_GONE, "the endpoint is ending")
        if method == "session":
            gate = self.session_gate
            if gate is not None and self.calls:
                await asyncio.to_thread(gate.wait, 10)
            return self.info()
        if method == "facts":
            with self._lock:
                return self._facts()
        if method in ("deliver", "steer"):
            return await self._hand(conn, method, params)
        if method == "wait":
            return await asyncio.to_thread(self._wait, params)
        if method == "watch":
            # Read the facts and join the watchers under the lock that orders the
            # notifications, and answer before anything else is written: nothing falls
            # between the facts and the first notification.
            with self._lock:
                self.watchers.append(conn)
                return {"facts": self._facts()}
        if method == "stops.handle":
            self._needs("stop-handler")
            with self._lock:
                if self.stop_handler is not None and not self.stop_handler.closed:
                    raise ipc.RpcError(ERROR_STOP_HANDLER_TAKEN,
                                       "another connection handles this session's stops")
                self.stop_handler = conn
                self.handlers += 1
            return {}
        if method == "stops.answer":
            with self._lock:
                taken = conn is self.stop_handler
                if taken:
                    self.stop_answers[params.get("ask")] = params.get("reason")
                    self._cond.notify_all()
            return {"taken": taken}
        raise ipc.RpcError(METHOD_NOT_FOUND, f"no method {method!r}")

    def _needs(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise ipc.RpcError(ERROR_CAPABILITY, f"this session does not offer {capability}",
                               {"capability": capability})

    def _facts(self) -> dict:
        turn = None
        if self.turn is not None:
            opening = self.turn["opening"]
            turn = {"turn_id": self.turn["id"], "command": None,
                    "started_at": self.turn["started_at"],
                    "opening": None if opening is None else
                    {"id": opening, "ref": self.deliveries[opening]["ref"]}}
        return {"kind": "facts", "session_id": self.session_id,
                "turn_number": self.turn_number, "turn_open": self.turn is not None,
                "turn": turn, "agents": 0, "draft": False, "at": time.time(),
                "stale": False, "ready": None}

    async def _hand(self, conn, method: str, params: dict):
        self._needs(method)
        text, ref = params.get("text"), params.get("ref")
        if not isinstance(text, str) or not text:
            raise ipc.RpcError(INVALID_PARAMS, "text is a non-empty string")
        with self._lock:
            step = self.script.pop(0) if self.script else None
            if step is not None and step[0] == "gone":
                raise ipc.RpcError(ERROR_GONE, "the endpoint is ending")
            recorded = step[2] if step is not None and step[0] == "late" else step
            answer = self._record(method, text, ref, recorded)
        if step is not None and step[0] == "drop":
            conn.close()
            return answer
        if step is not None and step[0] == "late":
            await asyncio.sleep(step[1])
        return answer

    def _record(self, method: str, text: str, ref, step) -> dict:
        did = f"d{next(self._ids)}"
        self.texts.append(text)
        self.calls.append((method, did, ref, text))
        self.deliveries[did] = {"text": text, "ref": ref, "method": method,
                                "started": None, "ended": None}
        answer = {"delivery": did, "ref": ref}
        kind = step[0] if step is not None else None
        if kind == "refuse":
            value = {"kind": "refused", "reason": step[1], "delivery": did, "ref": ref}
            self.deliveries[did]["started"] = self.deliveries[did]["ended"] = value
            return {**answer, "result": value}
        if method == "steer" and self.turn is None:
            value = {"kind": "refused", "reason": "idle", "delivery": did, "ref": ref}
            self.deliveries[did]["started"] = self.deliveries[did]["ended"] = value
            return {**answer, "result": value}
        if kind == "refuse-later":
            self._settle(did, {"kind": "refused", "reason": step[1]})
            return answer
        if kind == "lose":
            self._settle(did, {"kind": "lost", "reason": step[1]})
            return answer
        if method == "steer":
            if kind != "hold" and self.steer_auto:
                self.take(did)
            return answer
        if self.auto:
            if self.turn is None and not self.waiting:
                self.start_turn(did)
            else:
                self.waiting.append(did)
        return answer

    def _wait(self, params: dict) -> dict:
        until = params.get("until")
        key = "started" if until == "started" else "ended"
        timeout_ms = params.get("timeout_ms")
        deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000
        with self._lock:
            did = params.get("delivery")
            by_ref = did is None
            if by_ref:
                ref = params.get("ref")
                found = [i for i, d in self.deliveries.items() if d["ref"] == ref]
                if not found:
                    return {"delivery": None,
                            "result": {"kind": "lost",
                                       "reason": f"unknown delivery: no delivery has "
                                                 f"ref {ref!r}", "ref": ref}}
                did = found[-1]
            d = self.deliveries.get(did)
            if d is None:
                return {"result": {"kind": "lost",
                                   "reason": f"unknown delivery: {did!r}"}}
            extra = {"delivery": did} if by_ref else {}
            while d[key] is None:
                if self._stopped:
                    break
                left = None if deadline is None else deadline - time.monotonic()
                if left is not None and left <= 0:
                    return {"pending": True, **extra}
                self._cond.wait(0.2 if left is None else min(left, 0.2))
            if d[key] is None:
                raise ipc.RpcError(ERROR_GONE, "the endpoint is ending")
            return {"result": d[key], **extra}

    # ------------------------------------------------------------------ for the test
    def by_ref(self, ref) -> list[str]:
        """The ids of the deliveries recorded with `ref`, in order."""
        with self._lock:
            return [i for i, d in self.deliveries.items() if d["ref"] == ref]

    def wait_for(self, predicate, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("timed out waiting on the fake endpoint")
            time.sleep(0.01)
