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
    agents()              the session's subagents, teammates and background shells as the
                          harness records them (`Agents`), bounded the same way; `Live`'s
                          count is the one derived from this list (`Live.of`)
    steering(delivery_id) a steer is being recorded: called under the endpoint's lock, so
                          it touches only the adapter's own state and never calls the
                          harness or the endpoint (the base does nothing)
    submit(delivery)      put the text in as a new turn
    append(delivery)      put the text into the running turn (a steer)
    command(request)      run `/clear`, `/exit` or a compaction, and with
                          `request.stop_shells` stop the session's background shells once
                          it has taken effect
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
    endpoint.turn_started(turn_id, command="compact")
                                          a main-loop turn the harness ran for a turns
                                          compaction (Codex runs one as a turn)
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
    endpoint.command_took_effect(request_id) -> bool
                                          the command took effect: with `stop_shells`
                                          the stop begins, bounded by `SHELL_STOP_S`
                                          (a compaction's Ready marks it by itself);
                                          False when the command has already settled,
                                          and then nothing is stopped
    endpoint.command_ran(request_id, new_session_id=None, shells=None)
                                          the command ran, with the stop's report when
                                          `stop_shells` was set; a compaction settles at
                                          the later of this and its Ready
    endpoint.gone(reason)                 the session ended

The adapter's duty to settle: every delivery handed to `submit` ends in exactly one
`turn_started(…, delivery.id)` or `turn_tied(…, delivery.id)` (the tie),
`refused(delivery.id, reason)` or `lost(delivery.id, reason)`; every delivery handed to
`append` in one `steered(delivery.id)`, `refused(delivery.id, "idle")` (the harness had no
turn to take it into) or `lost(delivery.id, reason)`; and every request handed to `command`
in one `command_settled(request.id, result)` or `command_ran(request.id, …)`. A compaction
is confirmed by the session: the core settles it `ran` at the next Ready with source
`compact` (with `stop_shells`, once `command_ran` has reported the stop too); the adapter
settles it itself only when it fails or is refused.

Stopping shells (`request.stop_shells`): the adapter checks it may stop them before the
command runs, with nothing stopped yet, and a stop the harness turns down settles
`Refused("unsupported")` and runs nothing. A list it cannot vouch for, or no list at all,
is no refusal: the command runs, and the report says so. A harness that lacks the list
call altogether refuses `unsupported` there. Once the command has taken effect it stops
the shells it can list within `SHELL_STOP_S` and reports `Shells(stopped, left,
complete)`: an entry still running is `left` with its `why` (`timeout`, `denied`,
`unsupported`, or the harness's own words), and `complete` False adds the one entry
`LeftShell(None, "unlisted")` for the shells it could not name. A listed entry it chose
not to stop carries a `why` of its own, never `unlisted`, which stays the unnamed entry's.
A report that never comes reads as a list turns cannot vouch for. Subagent liveness: `agents()` reads the harness's own record on every call and keeps no copy
between calls, so an entry reads `final` as soon as the harness records its final notice,
whether or not a main-loop turn is open. An adapter maps the harness's states into
`AGENT_STATES` and sets `final` True only for a final notice (done, failed or stopped),
None when the harness gives no notice to read. A list it could not read whole is
`complete` False, never a shorter list: an unknown count is not zero.

A `submit`, `append` or `command` that
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

import time
from dataclasses import dataclass

from .results import Agents

#: How long an adapter's stop of a command's background shells may take, from the
#: command's moment of effect, in seconds. A stop not back by then is `left` `timeout`.
SHELL_STOP_S = 10.0


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
    #: Of `agents`, the background shells (Codex's terminals), which a command with
    #: `stop_shells` leaves out of its agent check. Claude's list holds none.
    shells: int = 0
    #: With `agents` None because the shells alone could not be listed: the count with
    #: the shells left out, which a command with `stop_shells` reads. None otherwise.
    without_shells: int | None = None

    @classmethod
    def of(cls, agents: Agents, draft: bool = False) -> Live:
        """The live facts with the count derived from `agents`: entries not final and
        pending, running or waiting, and of them the `shell` entries; None when the list
        is not complete."""
        count = agents.busy()
        if count is None:
            return cls(None, draft)
        return cls(count, draft, count - (agents.busy(shells=False) or 0))

    def busy(self, *, shells: bool = True) -> bool:
        """Whether a live subagent keeps the session busy; an unreadable list does. With
        `shells` False the background shells counted among `agents` are left out, as a
        command with `stop_shells` reads it."""
        if self.agents is None:
            return shells or self.without_shells is None or self.without_shells > 0
        return self.agents - (0 if shells else max(0, self.shells)) > 0


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
    name: str                    # "clear", "exit" or "compact"
    #: Stop the session's background shells once the command has taken effect.
    stop_shells: bool = False


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
        """The live facts. The base derives the count from `agents()` and reads no box."""
        return Live.of(self.agents())

    def agents(self) -> Agents:
        """The session's subagents as the harness records them, read on every call. The
        base cannot list any: its list is not complete, which counts as busy."""
        return Agents((), False, time.time())

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


__all__ = ["SHELL_STOP_S", "Live", "Delivery", "CommandRequest", "Harness"]
