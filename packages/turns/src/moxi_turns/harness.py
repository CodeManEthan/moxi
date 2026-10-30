"""What an adapter gives the endpoint core: the harness half of an endpoint.

One adapter per harness: the Claude Code endpoint half, the Codex host, the
OpenCode host. The endpoint core (`endpoint.Endpoint`) owns the caller address, the turn
ledger, the deliveries, the stop handler and the listeners; the adapter owns the harness.
They meet here.

The core calls the adapter:

    attach(endpoint)      once, before the address opens
    live()                the live facts: subagents and the prompt box (`Live`); the core
                          bounds the read by `live_wait_s` and reads a late one as
                          unreadable
    steering(delivery_id) a steer is being recorded: called under the endpoint's lock, so
                          it touches only the adapter's own state and never calls the
                          harness or the endpoint (the base does nothing)
    submit(delivery)      put the text in as a new turn
    append(delivery)      put the text into the running turn (a steer)
    command(request)      run `/clear` or `/exit`
    expired(delivery_id)  the core settled a delivery it handed without the adapter's word
                          (`PENDING_TTL_S`), or the worker skipped a steer that had
                          settled: the adapter lets go of it (the base does nothing)
    close()               the endpoint is stopping

`submit`, `append` and `command` run on the endpoint's one ordered worker, in the order
the endpoint recorded them, never on the loop that answers callers. They may block (a line
write, a request to the harness), which holds only the worker; what happened comes back
through the endpoint's adapter-side calls:

    endpoint.ready(source, session_id, announce=True) -> str | None
                                          a start, resume, clear or compaction; returns
                                          the listeners' added context; `announce=False`
                                          raises no Ready (the host raised it already)
    endpoint.identify(session_id, ready=None)
                                          the harness learned its session id before
                                          a Ready event; updates addressing, and with
                                          `ready` raises Ready with that source and
                                          asks no listener (an accepted load)
    endpoint.prompt() -> str | None       a UserPromptSubmit; returns the added context
    endpoint.turn_started(turn_id, delivery_id=None)
                                          a main-loop turn started; the adapter's tie
                                          names the delivery that took it
    endpoint.turn_started(turn_id, held=True)
                                          a main-loop turn started whose delivery, if
                                          any, is decided later (Claude Code's tie):
                                          the ledger opens it now, its events wait
    endpoint.turn_tied(turn_id, delivery_id) / endpoint.turn_released(turn_id)
                                          that decision: the held turn took the
                                          delivery, or is the person's; its held
                                          events go out in order
    endpoint.lost(delivery_id, reason)    the adapter cannot say which turn, if any,
                                          took a delivery (reason `untied: …`)
    endpoint.learn_session(session_id)    the adapter learned the session's id
    endpoint.turn_ended(turn_id, outcome) the open main-loop turn ended
    endpoint.entering(delivery_id) -> str | None
                                          a delivery is about to take effect: an
                                          only-if-idle one is re-checked against the
                                          ledger; a reason refuses it
    endpoint.steered(delivery_id)         the harness took a steer into the running turn
    endpoint.refused(delivery_id, reason) the adapter refused a delivery it was handed
    endpoint.on_stop(facts) -> str | None a Stop of the main loop: the stop handler's
                                          answer
    endpoint.command_entering(request_id) -> str | None
                                          a command is about to take effect: the re-check
    endpoint.command_settled(request_id, result)
                                          a command ran (`Ran`), failed or was refused
    endpoint.gone(reason)                 the session ended

The adapter's duty to settle: every delivery handed to `submit` ends in exactly one
`turn_started(…, delivery.id)` or `turn_tied(…, delivery.id)` (the tie),
`refused(delivery.id, reason)` or `lost(delivery.id, reason)`; every delivery handed to
`append` in one `steered(delivery.id)`, `refused(delivery.id, "idle")` (the harness had no
turn to take it into) or `lost(delivery.id, reason)`; and every request handed to `command`
in one `command_settled(request.id, result)`. A `submit`, `append` or `command` that
raises counts as settled (the delivery is refused, the command fails). Until it settles,
a submitted delivery holds the endpoint's one in-flight slot (later only-if-idle
deliveries refuse `busy-turn`, later plain ones queue) and a command holds the one command
slot (later commands refuse `in-flight`). A steer holds no slot. The core does not trust
this duty: past `endpoint.PENDING_TTL_S` it settles the delivery `Lost` or the command
`Failed` with reason `expired`, frees the slot, ignores the adapter's late word about it,
and answers `expired` to its `entering` or `command_entering`, which the adapter takes as a
refusal.

A hand-off already in flight when its delivery settles `lost` may still reach the harness:
a line write the runner reads late, a `turn/steer` the app-server answers after the
bound. The harness may then run that text although its caller read `lost`; that duplicate
is the accepted cost of a bound on every delivery, and the harness's late word on it
changes nothing.

An only-if-idle delivery carries its preconditions (`Delivery.refuse_draft`,
`Delivery.refuse_agents`), which the adapter checks with the live facts where the delivery
takes effect, after `entering` passes. A steer is taken into the running turn or refused
`idle`; it never becomes a turn of its own.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Live:
    """The facts the ledger does not hold. `agents` counts subagents pending, running or
    waiting, or is None when the harness could not list them; `draft` says the person has
    typed into the prompt box (an adapter that cannot read the box says True).

    Rule: an unreadable subagent list is unknown, and unknown counts as
    busy. It never reads as 0: `facts().agents` is None, a start that asked for the
    subagent refusal refuses `busy-agents`, and a session command refuses `busy-agents`."""

    agents: int | None = 0
    draft: bool = False


@dataclass(frozen=True)
class Delivery:
    """One text handed to the adapter. `kind` is "deliver" (a new turn, handed to
    `submit`) or "steer" (into the running turn, handed to `append`). `only_if_idle`: the
    caller asked for a new turn only if none is open, so `entering` re-checks it, and the
    refusal flags apply. `ref` is the caller's, carried for the adapter's logs; turns never
    acts on it."""

    id: str
    text: str
    kind: str
    only_if_idle: bool = False
    refuse_draft: bool = False
    refuse_agents: bool = False
    ref: str | None = None


@dataclass(frozen=True)
class CommandRequest:
    """One session command handed to the adapter."""

    id: str
    name: str                    # "clear" or "exit"


class Harness:
    """The base an adapter builds on. Every method but `attach` is the adapter's to write."""

    #: "claude", "codex" or "opencode".
    name: str = ""
    #: What this session can do: a subset of `results.CAPABILITIES`.
    can: frozenset[str] = frozenset()
    #: The harness process's pid, which `find(pid=…)` matches.
    pid: int = 0
    #: The session's working directory.
    cwd: str = ""
    #: The version of the harness-side code the endpoint talks to (the Claude runner), or
    #: None.
    mod_version: str | None = None
    #: How long the core waits for `live()` before it reads the live facts as unreadable.
    live_wait_s: float = 2.0

    def attach(self, endpoint) -> None:
        self.endpoint = endpoint

    def live(self) -> Live:
        return Live()

    def loading(self) -> bool:
        """Whether this adapter has no accepted load to receive input."""
        return False

    def steering(self, delivery_id: str) -> None:
        """A steer is being recorded, under the endpoint's lock: no harness call here."""

    def submit(self, delivery: Delivery) -> None:
        raise NotImplementedError

    def append(self, delivery: Delivery) -> None:
        raise NotImplementedError

    def command(self, request: CommandRequest) -> None:
        raise NotImplementedError

    def expired(self, delivery_id: str) -> None:
        pass

    def close(self) -> None:
        pass


__all__ = ["Live", "Delivery", "CommandRequest", "Harness"]
