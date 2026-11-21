"""What a module gives the connector to serve its tools, and what the connector gives each
call: the connector's interface 1, the one part of it a provider reaches.

A provider that has the connector only through an extra reaches this module through
`moxi.modules.load("connector", interface=1).api`, the way any module reaches an optional
one, and never imports it at its own import time.

A module offers a provider as an entry of the group `moxi.connector.providers`, named by
the module and pointing at a `Provider` in its package:

    [project.entry-points."moxi.connector.providers"]
    comms = "moxi_comms.connector:PROVIDER"

The connector refuses an entry whose name is not the declaring distribution's module.

The seam runs both ways. The connector imports nothing of any provider; it loads the entry
and nothing else. A provider imports nothing of the connector but this module. No code,
token, grant, grants file or origin crosses to a provider: per call it gets a `Ctx`, and
nothing more. This module is standard library only, so importing it costs a provider
nothing of the connector's HTTP or MCP code.

A provider gives:

- `tools`: each a `Tool` with a name, a description, an input schema, an optional output
  schema, MCP annotations passed to the app unchanged, and `call(args, ctx)`, an async
  function that returns a `Result`. A refusal is a `Result` with `is_error` and a
  `refusal` code, never an exception;
- `start(env)` and `stop()`, async, each run once by the connector's process. A provider
  whose start raises serves no tools and shows a `fail` status row; the others serve;
- `status()`: rows of `(name, level, text)`, `level` one of `ok`, `warn` and `fail`, shown
  by `moxi connector status` and the doctor;
- `gate`, optional: the sign-in code's mint gate, a `Gate` whose `peer(peer)` runs on the
  control socket's peer before every mint and `shell(env)` in the verb, each answering a
  refusal text or None.

The connector scrubs every answer after the provider gives it: from the text part and
from every string of the structured value, at any depth, it removes the C0 control
characters but newline and tab, DEL, the C1 range and every Unicode format character
(category `Cf`: bidi controls, zero-width characters, the soft hyphen, the byte order
mark, tag characters). A provider that redacts hands the connector text its last
redaction saw in final form, so the scrub leaves it unchanged: no text may reach the chat
that redaction didn't see as it goes out. For a provider that redacts, that means
redacting the raw text, then removing those characters, then redacting again. Removing
them first and redacting once is not enough: the redactor sees through some invisible
characters, and removing one can glue a word onto a key and break the boundary its
pattern needs.

A provider may bound its own calls with a `Limit`, one per provider, shared by its tools;
nothing makes a provider use one, and comms doesn't. At most two calls of the provider run
at once. A third waits up to 5 seconds for a place, then answers the refusal `busy`. Each
call's work runs in a thread of its own, and a call still running 20 seconds after it
began answers the refusal `timeout`. A thread that outlives its call finishes, its answer
is dropped, and it holds its place until it ends; a caller that hangs up ends the call the
same way. The work is a plain function of no arguments that returns a `Result`:

    LIMIT = Limit()

    async def read_note(args, ctx):
        result = await LIMIT.run(functools.partial(_read_note, args))
        ctx.log(refusal=result.refusal, ...)
        return result

The work runs off the event loop, so it calls nothing of `ctx`; the coroutine logs what
the work returned. An exception the work raises comes out of `run`, as a tool's own would.

Two providers that declare one tool name both lose it: no provider's tool shadows
another's. A tool whose `call` raises answers "The tool failed; the connector's log has
the request id." and the exception stays in the connector's log, never sent.

`after_answer` callbacks follow one rule: `written` is True only for a call whose tool
answered a `Result` without `is_error` and whose answer's last byte went to the server.
A call whose tool raised, answered something not a `Result`, or answered a `Result` with
`is_error` runs its callbacks with False as soon as it ends, whatever becomes of the
error answer; so does a call whose caller hung up first, or whose answer failed to go out.
"""

from __future__ import annotations

import asyncio
import collections
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

#: The extension group providers are declared in.
GROUP = "moxi.connector.providers"
#: The levels a status row may carry.
LEVELS = ("ok", "warn", "fail")
#: What a tool that raised answers.
TOOL_FAILED = "The tool failed; the connector's log has the request id."
#: A `Limit`'s calls at once, seconds a call waits for a place, and seconds a call may run.
LIMIT_AT_ONCE = 2
LIMIT_WAIT_S = 5.0
LIMIT_CALL_S = 20.0
#: What a call answers, refused `busy`, when no place frees in time.
BUSY = "Another read is running; try again in a moment."
#: What a call answers, refused `timeout`, when it runs past its time.
TIMEOUT = "The read took longer than 20 seconds and nothing came back; try a smaller one."

#: Field names no log row may hold, at any depth, from the connector or a provider: what
#: would put a secret or a message body in the call log.
FORBIDDEN = frozenset({
    "query", "query_string", "header", "headers", "cookie", "cookies", "key", "token",
    "code", "pairing_code", "challenge", "body", "request_body", "response_body",
    "session_id", "secret", "credential", "authorization", "text",
})

#: The fields of a request's log row the connector itself writes. A tool's `ctx.log` may
#: not name one.
HOST_FIELDS = frozenset({
    "at", "kind", "request", "method", "route", "rpc", "tool", "provider", "status", "ms",
    "grant", "client", "user_agent", "forwarded_for_untrusted", "dropped",
})


@dataclass(frozen=True)
class Result:
    """What a tool answers: one text part, written to be read aloud, a structured value
    under the tool's output schema, and, for a refusal, `is_error` with a `refusal` code.
    """

    text: str
    structured: Mapping[str, Any] | None = None
    is_error: bool = False
    refusal: str | None = None

    @classmethod
    def refused(cls, refusal: str, text: str, **fields: Any) -> Result:
        """A refusal: the reason as text, its code word as `refusal` in the structured
        value too."""
        return cls(text, {"refusal": refusal, **fields}, is_error=True, refusal=refusal)


@dataclass(frozen=True)
class Tool:
    """One tool: its MCP description and the function that answers it.

    `annotations` takes MCP's hint names (`readOnlyHint`, `destructiveHint`,
    `idempotentHint`, `openWorldHint`, `title`), passed to the app unchanged."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    call: Callable[[Mapping[str, Any], Ctx], Awaitable[Result]]
    output_schema: Mapping[str, Any] | None = None
    annotations: Mapping[str, Any] | None = None
    title: str | None = None


@dataclass(frozen=True)
class Gate:
    """The sign-in code's mint gate. Each check answers a refusal text, or None to let the
    mint through. `peer` gets the control socket's `moxi.ipc.Peer`; `shell` the verb's
    environment."""

    peer: Callable[[Any], str | None]
    shell: Callable[[Mapping[str, str]], str | None]


class StatusRow(NamedTuple):
    """One status row of a provider: a short name, a level of `LEVELS` and a line."""

    name: str
    level: str
    text: str


async def _nothing(*_args: Any) -> None:
    return None


def _no_rows() -> Sequence[StatusRow]:
    return ()


@dataclass(frozen=True)
class Provider:
    """What a module's entry in `moxi.connector.providers` points at."""

    tools: Sequence[Tool] = ()
    start: Callable[[Mapping[str, str]], Awaitable[None]] = _nothing
    stop: Callable[[], Awaitable[None]] = _nothing
    status: Callable[[], Sequence[StatusRow | tuple[str, str, str]]] = _no_rows
    gate: Gate | None = None


def refused_field(fields: Mapping[str, Any], forbidden: frozenset[str] = FORBIDDEN
                  ) -> str | None:
    """The first field name of `forbidden` that `fields` holds as a key at any depth of
    nested mappings and lists, or None."""
    stack: list[Any] = [fields]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            for key, inner in value.items():
                if isinstance(key, str) and key.lower() in forbidden:
                    return key
                stack.append(inner)
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return None


@dataclass(eq=False)
class Ctx:
    """What the connector gives one tool call. A provider reads it and never builds one.

    - `grant_id` and `grant_label`: the sign-in the call came through;
    - `request_id`: the call's id, which its log row carries;
    - `after_answer(fn)`: `fn(written)` runs once the answer's fate is known, `written`
      True when the answer's last byte went to the server without an error, False when
      writing it failed, no answer was written, the caller hung up first, or the call
      raised or answered an error (the module's text above has the whole rule). A caller
      that hangs up also cancels the call: the tool sees `CancelledError` at its next
      await. `fn` may be a coroutine function;
    - `log(**fields)`: adds fields to the call's log row. A field name the log refuses
      (`FORBIDDEN`, at any depth), or one the connector writes itself (`HOST_FIELDS`),
      raises `ValueError` naming it, and nothing is added.
    """

    grant_id: str | None
    grant_label: str | None
    request_id: str
    _after: list[Callable[[bool], Any]] = field(default_factory=list, repr=False)
    _fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def after_answer(self, fn: Callable[[bool], Any]) -> None:
        if not callable(fn):
            raise TypeError("after_answer takes a function of one argument, written")
        self._after.append(fn)

    def log(self, **fields: Any) -> None:
        bad = refused_field(fields)
        if bad is not None:
            raise ValueError(f"the call log refuses the field {bad!r}")
        owned = sorted(HOST_FIELDS.intersection(fields))
        if owned:
            raise ValueError(f"the call log's field {owned[0]!r} is the connector's own")
        self._fields.update(fields)


class Limit:
    """How many calls of one provider run at once, how long a call waits for a place, and
    how long it may run: `at_once` calls, a wait of `wait_s` seconds, `call_s` seconds from
    the call's start. One provider makes one and every tool of it calls `run`.

    It belongs to no event loop: a provider's `Limit` is made at import and serves every
    loop the process runs."""

    def __init__(self, *, at_once: int = LIMIT_AT_ONCE, wait_s: float = LIMIT_WAIT_S,
                 call_s: float = LIMIT_CALL_S):
        if at_once < 1:
            raise ValueError("a Limit lets at least one call run")
        self.at_once = at_once
        self.wait_s = wait_s
        self.call_s = call_s
        self._lock = threading.Lock()
        self._held = 0
        self._waiting: collections.deque[tuple[asyncio.AbstractEventLoop, asyncio.Future]] \
            = collections.deque()

    @property
    def held(self) -> int:
        """The places held now, by calls running and by threads that outlived theirs."""
        with self._lock:
            return self._held

    async def run(self, work: Callable[[], Result]) -> Result:
        """`work()` in a thread of its own once a place is free, its `Result` answered; the
        refusal `busy` when no place frees within `wait_s`, `timeout` when `call_s` pass
        from this call's start first."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.call_s
        if not await self._take(loop, min(self.wait_s, self.call_s)):
            return Result.refused("busy", BUSY)
        answered: asyncio.Future = loop.create_future()

        def body() -> None:
            try:
                outcome: tuple[bool, Any] = (True, work())
            except BaseException as exc:  # noqa: BLE001 - handed to the call, which raises it
                outcome = (False, exc)
            finally:
                self._release()
            try:
                loop.call_soon_threadsafe(_settle, answered, outcome)
            except RuntimeError:
                pass                    # the loop has closed; nobody waits for the answer

        try:
            threading.Thread(target=body, name="moxi-connector-limit", daemon=True).start()
        except BaseException:
            self._release()
            raise
        try:
            async with asyncio.timeout_at(deadline):
                ok, value = await asyncio.shield(answered)
        except TimeoutError:
            return Result.refused("timeout", TIMEOUT)
        if not ok:
            raise value
        return value

    async def _take(self, loop: asyncio.AbstractEventLoop, wait_s: float) -> bool:
        """Take a place, waiting up to `wait_s` seconds for one. True once taken."""
        with self._lock:
            if self._held < self.at_once and not self._waiting:
                self._held += 1
                return True
            given: asyncio.Future = loop.create_future()
            self._waiting.append((loop, given))
        try:
            async with asyncio.timeout(wait_s):
                return await given
        except TimeoutError:
            return self._abandon(loop, given)    # True: the place came as the wait ended
        except asyncio.CancelledError:
            if self._abandon(loop, given):
                self._release()
            raise

    def _abandon(self, loop: asyncio.AbstractEventLoop, given: asyncio.Future) -> bool:
        """Stop waiting. True when a release had already handed this waiter its place."""
        with self._lock:
            try:
                self._waiting.remove((loop, given))
            except ValueError:
                pass                    # a release chose this waiter
            else:
                return False
        # The release's hand-over has run (the place is ours) or will run on this loop and
        # find the wait cancelled, and pass the place on.
        return given.done() and not given.cancelled()

    def _release(self) -> None:
        """Give a place back: to the longest waiter, else to the pool. Any thread."""
        with self._lock:
            while self._waiting:
                loop, given = self._waiting.popleft()
                try:
                    loop.call_soon_threadsafe(self._give, given)
                except RuntimeError:
                    continue            # that waiter's loop has closed
                return
            self._held -= 1

    def _give(self, given: asyncio.Future) -> None:
        """On the waiter's loop: hand it the place, or pass it on when it stopped waiting."""
        if given.done():
            self._release()
        else:
            given.set_result(True)


def _settle(answered: asyncio.Future, outcome: tuple[bool, Any]) -> None:
    if not answered.done():
        answered.set_result(outcome)


__all__ = ["BUSY", "Ctx", "FORBIDDEN", "GROUP", "Gate", "HOST_FIELDS", "LEVELS",
           "LIMIT_AT_ONCE", "LIMIT_CALL_S", "LIMIT_WAIT_S", "Limit", "Provider", "Result",
           "StatusRow", "TIMEOUT", "TOOL_FAILED", "Tool", "refused_field"]
