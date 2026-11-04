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

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

#: The extension group providers are declared in.
GROUP = "moxi.connector.providers"
#: The levels a status row may carry.
LEVELS = ("ok", "warn", "fail")
#: What a tool that raised answers.
TOOL_FAILED = "The tool failed; the connector's log has the request id."

#: Field names no log row may hold, at any depth, from the connector or a provider: what
#: would put a secret or a message body in the call log.
FORBIDDEN = frozenset({
    "query", "query_string", "header", "headers", "cookie", "cookies", "key", "token",
    "code", "pairing_code", "challenge", "body", "request_body", "response_body",
    "session_id", "secret", "credential", "authorization", "text",
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
      (`FORBIDDEN`, at any depth) raises `ValueError` naming it, and nothing is added.
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
        self._fields.update(fields)


__all__ = ["Ctx", "FORBIDDEN", "GROUP", "Gate", "LEVELS", "Provider", "Result",
           "StatusRow", "TOOL_FAILED", "Tool", "refused_field"]
