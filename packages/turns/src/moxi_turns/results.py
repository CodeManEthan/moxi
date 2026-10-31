"""The values turns hands back: facts, agents, delivery and command results, and the
notifications a watch carries.

A value a method answers with is a frozen dataclass with a `kind`, a `to_wire()` and a
`from_wire()`, so the endpoint writes it on the socket and the client reads it back as the
same value. A notification is a frozen dataclass with its JSON-RPC method name (`METHOD`),
a `to_params()` and `from_notification()`. A value inside another value (a delivery's
`{id, ref}`, the open turn, a subagent, the shells a command stopped) is written as a plain
object with no `kind`. The contract, `contracts/socket-protocol.md`, spells each one.
Callers reach them as `moxi_turns.<Name>`.

This module imports nothing beyond the standard library's `dataclasses`, so a hook process
can read a value without paying for the core.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar

#: What a session offers, as its hello's `capabilities` lists them. `session`, `facts`,
#: `watch` and `wait` are always there and carry no capability. `guards`: the session runs
#: the guard list at its tool calls. `after-tool`: an `after-tool` row's text reaches the
#: model after the tool. `usage`: the session reports usage (Claude's readings, Codex's
#: limits). `mcp-token`: the Codex host's `mcp.reload`. `environ`: the `environ` method.
CAPABILITIES = ("deliver", "only-if-idle", "steer", "stop-handler", "clear", "exit",
                "compact", "agents", "stop-shells", "guards", "after-tool", "usage",
                "mcp-token", "environ")

#: The most names one `environ` call may ask.
ENVIRON_MAX_NAMES = 64

#: How a turn ended (`Ended.outcome`, `TurnEnded.outcome`).
OUTCOMES = ("answer", "aborted", "error", "refusal")

#: Why the endpoint refused a delivery (`Refused.reason` on a delivery). `idle` is a
#: steer's alone: no turn runs to take the text. `loading`: a Codex host has no accepted
#: load. `dropped`: another plugin's hook dropped the prompt. `unsupported`: the harness
#: turned it down after the endpoint offered it. The library adds one reason of its own,
#: `REFUSED_GONE`, which no endpoint sends as a value.
DELIVERY_REFUSALS = ("busy-turn", "stale-turn", "draft", "busy-agents", "idle", "loading",
                     "dropped", "unsupported")

#: Why the endpoint refused a session command (`Refused.reason` on a command): the
#: delivery reasons it shares, and a command's own: `not-allowed` (no consent),
#: `in-flight` (one already runs), `rate` (one was accepted in the last 60 s), `loading`
#: (a Codex host has no accepted load) and `unsupported` (the harness has no way at run
#: time, or turned the stop of its shells down before the command ran). The library adds
#: `REFUSED_GONE`, as for a delivery.
COMMAND_REFUSALS = ("busy-turn", "stale-turn", "draft", "busy-agents", "not-allowed",
                    "in-flight", "rate", "loading", "unsupported")

#: Where a `Ready` came from: a new session, a resume, a `/clear` or a compaction.
READY_SOURCES = ("startup", "resume", "clear", "compact")

#: The session commands.
COMMANDS = ("clear", "exit", "compact")

#: How long a `command` call waits for its settle when the caller gives no bound, in
#: seconds: a compaction often outlasts 30 s.
COMMAND_WAIT_S = {"clear": 30.0, "exit": 30.0, "compact": 330.0}
#: What `stop_shells` adds to that wait, in seconds.
STOP_SHELLS_WAIT_S = 10.0

#: How a delivery joined a turn (`DeliveryRef.how`): it opened the turn, or was steered in.
DELIVERY_HOW = ("opened", "steered")

#: What kind of thing an `Agent` is. `shell` is a Codex background terminal.
AGENT_KINDS = ("subagent", "teammate", "shell")

#: An `Agent`'s state.
AGENT_STATES = ("pending", "running", "waiting", "idle", "done", "failed", "stopped",
                "unknown")

#: The states that keep an agent live for the busy checks, unless its notice is final.
BUSY_STATES = ("pending", "running", "waiting")

#: The longest `ref`, in bytes of UTF-8.
REF_MAX_BYTES = 256

#: turns' own JSON-RPC error codes, in the application range outside JSON-RPC's reserved
#: block. `gone`: the endpoint is ending or ended. `capability`: the session does not
#: offer the method or option, and the error's `data` is `{"capability": <name>}`.
#: `stop-handler-taken`: another connection handles the session's stops.
ERROR_GONE = 1001
ERROR_CAPABILITY = 1002
ERROR_STOP_HANDLER_TAKEN = 1003

#: Each of turns' error codes and its name.
ERRORS = {ERROR_GONE: "gone", ERROR_CAPABILITY: "capability",
          ERROR_STOP_HANDLER_TAKEN: "stop-handler-taken"}

#: The reason of the library's `Refused` for a request it never wrote (it could not
#: reach the endpoint) or that the endpoint answered with the `gone` error.
REFUSED_GONE = "gone"

#: The reason prefix of a `Lost` the endpoint decided while it lived: the delivery's
#: submit closed with no turn tied to it (a resolve with no turn held, a rejection, the
#: runner's bound).
LOST_UNTIED = "untied"
#: The reason prefix of a `Lost` from the endpoint's own bound on an unsettled delivery.
#: A `Failed` command the endpoint's bound settled carries the same prefix.
LOST_EXPIRED = "expired"
#: The reason prefix of the endpoint's `Lost` for a delivery it holds no record of: an id
#: it did not mint or settled long ago, or a `ref` no delivery of it carries.
LOST_UNKNOWN = "unknown delivery"
#: The reason prefix of the library's `Lost` for a hand-in whose outcome it could not
#: learn because the endpoint no longer serves, or the call carried no `ref` to look it up
#: by. No endpoint sends it.
LOST_UNANSWERED = "unanswered"

#: The library's `Failed` reason for a command it wrote and got no answer to. The command
#: may still run.
FAILED_UNANSWERED = "unanswered"
#: The `Failed` reason when the call's own wait for a command (`timeout_ms`, the
#: library's `timeout`) passed first. The command may still run.
FAILED_TIMEOUT = "timeout"

#: A `LeftShell.why` standing for the shells turns could not name.
SHELL_UNLISTED = "unlisted"


def check_ref(ref: Any) -> str | None:
    """`ref` itself when it is None or a string of at most `REF_MAX_BYTES` bytes as UTF-8.
    Anything else raises `ValueError` (the endpoint answers it as invalid params)."""
    if ref is None:
        return None
    if not isinstance(ref, str):
        raise ValueError(f"a ref is a string, not {type(ref).__name__}")
    try:
        size = len(ref.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("a ref must encode as UTF-8") from None
    if size > REF_MAX_BYTES:
        raise ValueError(f"a ref is at most {REF_MAX_BYTES} bytes as UTF-8, not {size}")
    return ref


def _out(value: Any) -> Any:
    if isinstance(value, _Value):
        return value.to_wire()
    if isinstance(value, tuple):
        return [_out(v) for v in value]
    return value


def _in(value: Any, nested: type[_Value] | None) -> Any:
    if isinstance(value, list):
        return tuple(_in(v, nested) for v in value)
    if nested is not None and value is not None:
        if not isinstance(value, dict):
            raise TypeError(f"{nested.__name__} is an object, not {value!r}")
        return nested._build(value)
    return value


class _Value:
    """`to_wire()` gives `{"kind": KIND, ...fields}`, or the fields alone for a nested
    value (`KIND` empty); `_build()` reads that back. `_NESTED` names the fields that hold
    another value, or a list of them, and its class."""

    KIND: ClassVar[str] = ""
    _NESTED: ClassVar[dict[str, type[_Value]]] = {}

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.KIND} if self.KIND else {}
        for f in fields(self):  # type: ignore[arg-type]
            out[f.name] = _out(getattr(self, f.name))
        return out

    @classmethod
    def _build(cls, raw: dict[str, Any]):
        if not isinstance(raw, dict):
            raise TypeError(f"{cls.__name__} is an object, not {raw!r}")
        values = {}
        for f in fields(cls):  # type: ignore[arg-type]
            if f.name in raw:
                values[f.name] = _in(raw[f.name], cls._NESTED.get(f.name))
        return cls(**values)


class _Notification(_Value):
    """A notification's params: its fields, with no `kind`. `METHOD` is its method."""

    METHOD: ClassVar[str] = ""

    def to_params(self) -> dict[str, Any]:
        return {f.name: _out(getattr(self, f.name)) for f in fields(self)}  # type: ignore[arg-type]

    def to_wire(self) -> dict[str, Any]:
        return self.to_params()


# -- values inside other values -----------------------------------------------------------

@dataclass(frozen=True)
class DeliveryRef(_Value):
    """A delivery as a turn names it: its `id`, the caller's `ref` (or None), and, in a
    turn's `deliveries`, `how` it joined the turn (`opened` or `steered`, `DELIVERY_HOW`).
    Where only `{id, ref}` is written (a turn's `opening`, `turn.steered`'s `delivery`),
    `how` is None and is left off the wire."""

    id: str
    ref: str | None = None
    how: str | None = None

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "ref": self.ref}
        if self.how is not None:
            out["how"] = self.how
        return out


@dataclass(frozen=True)
class OpenTurn(_Value):
    """The open main-loop turn, as its `turn.started` named it (`Facts.turn`).

    `opening` is the delivery that opened it, or None when no delivery did: the person's
    own turn when `command` is also None, a turns command's turn (a Codex `compact`) when
    it is not. `started_at` is the wall-clock time (Unix seconds) the turn began, which a
    turn held for its tie keeps once it is tied.
    """

    _NESTED = {"opening": DeliveryRef}
    turn_id: str | None
    opening: DeliveryRef | None
    command: str | None
    started_at: float


@dataclass(frozen=True)
class Agent(_Value):
    """One subagent, teammate or Codex background terminal (`Agents.entries`).

    `kind` is one of `AGENT_KINDS`, `state` one of `AGENT_STATES`. `final` is True when its
    latest notice is a final one (done, failed or stopped), False when not, and None when
    the harness gives no notice to read. `parent_id` is the agent that spawned it, or None.
    """

    id: str
    kind: str
    state: str
    final: bool | None
    description: str | None = None
    parent_id: str | None = None

    @property
    def busy(self) -> bool:
        """True when this entry keeps the session busy: not final, and pending, running
        or waiting."""
        return self.final is not True and self.state in BUSY_STATES


@dataclass(frozen=True)
class LeftShell(_Value):
    """A background shell a command's stop left running, and `why`: `timeout`, `denied`,
    `unsupported`, `unlisted` or the harness's own words. `id` None with `why`
    `unlisted` stands for the shells turns could not name."""

    id: str | None
    why: str


@dataclass(frozen=True)
class Shells(_Value):
    """What a command's `stop_shells` did (`Ran.shells`).

    `stopped` lists the ids stopped; `left` each shell still running with its `why`.
    `complete` says whether turns could vouch for the list it stopped from. A list turns
    cannot vouch for is reported, never read as empty: when `complete` is False, `left`
    holds a `LeftShell(None, "unlisted")`. So `Shells((), (), True)` means nothing was
    running, and only that.
    """

    _NESTED = {"left": LeftShell}
    stopped: tuple[str, ...] = ()
    left: tuple[LeftShell, ...] = ()
    complete: bool = True


# -- notifications ----------------------------------------------------------------------

@dataclass(frozen=True)
class Ready(_Notification):
    """A session started, resumed, was cleared or compacted (`source`, one of
    `READY_SOURCES`, or None when the harness did not say). As the `session.ready`
    notification it carries `source` and `session_id`; as `Facts.ready` it also carries
    `at`, the wall-clock time the endpoint raised it."""

    METHOD = "session.ready"
    source: str | None
    session_id: str | None
    at: float | None = None

    def to_params(self) -> dict[str, Any]:
        return {"source": self.source, "session_id": self.session_id}

    def to_wire(self) -> dict[str, Any]:
        return {"source": self.source, "session_id": self.session_id, "at": self.at}


@dataclass(frozen=True)
class TurnStarted(_Notification):
    """A main-loop turn started, whoever caused it.

    `deliveries` holds the delivery that opened it (`how` `opened`), or is empty for the
    person's own turn. `command` is `compact` for a turn the harness ran to compact on a
    turns command (Codex), else None.
    """

    METHOD = "turn.started"
    _NESTED = {"deliveries": DeliveryRef}
    turn_id: str | None
    session_id: str | None
    turn_number: int
    deliveries: tuple[DeliveryRef, ...] = ()
    command: str | None = None


@dataclass(frozen=True)
class TurnSteered(_Notification):
    """The running turn took a steered delivery: the harness confirmed it."""

    METHOD = "turn.steered"
    _NESTED = {"delivery": DeliveryRef}
    turn_id: str | None
    session_id: str | None
    delivery: DeliveryRef


@dataclass(frozen=True)
class TurnEnded(_Notification):
    """A main-loop turn ended, with its `outcome` (one of `OUTCOMES`).

    `deliveries` holds every delivery the turn took: the opening one first (`opened`),
    then each steered one in the order taken (`steered`). `command` is as on
    `TurnStarted`.
    """

    METHOD = "turn.ended"
    _NESTED = {"deliveries": DeliveryRef}
    turn_id: str | None
    session_id: str | None
    turn_number: int
    outcome: str
    deliveries: tuple[DeliveryRef, ...] = ()
    command: str | None = None


@dataclass(frozen=True)
class Gone(_Notification):
    """The endpoint is ending: the session ended, or the endpoint went away. The watch's
    connection closes after it."""

    METHOD = "session.gone"
    reason: str


# -- values -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Facts(_Value):
    """One observation of a session (`Session.facts()`).

    `turn_number` is the current session's count of main-loop turns started, from 0 at
    each new session id; `turn_open` says one runs now. `turn` is the open turn as its
    `turn.started` named it, or None: set in the same step that sends `turn.started` and
    cleared in the one that sends `turn.ended`, so a turn held for its tie reads
    `turn_open` True and `turn` None until it is tied. `agents` counts subagents pending,
    running or waiting whose notice is not final, None when they could not be listed
    (unknown, which counts as busy); `draft` says the person has typed into the prompt
    box (True when the box could not be read; False where the harness cannot tell); `at`
    is the wall-clock time of the read. `stale` is True when the turn ledger moved while
    the live facts were read, so the reading is not one moment. `ready` is the last
    `session.ready` the endpoint raised, with its `at`, or None before the first.
    """

    KIND = "facts"
    _NESTED = {"turn": OpenTurn, "ready": Ready}
    session_id: str | None
    turn_number: int
    turn_open: bool
    turn: OpenTurn | None
    agents: int | None
    draft: bool
    at: float
    stale: bool = False
    ready: Ready | None = None


@dataclass(frozen=True)
class Agents(_Value):
    """The session's subagents, teammates and Codex background terminals
    (`Session.agents()`), read from the harness's own record at `at`.

    `complete` is False when the list could not be read whole; then `Facts.agents` is
    None.
    """

    KIND = "agents"
    _NESTED = {"entries": Agent}
    entries: tuple[Agent, ...]
    complete: bool
    at: float

    def busy(self, *, shells: bool = True) -> int | None:
        """The count the busy checks read (`Facts.agents`): entries not final and pending,
        running or waiting, or None when the list is not complete. With `shells` False
        the `shell` entries are left out, as a command with `stop_shells` reads it."""
        if not self.complete:
            return None
        return sum(1 for a in self.entries if a.busy and (shells or a.kind != "shell"))


@dataclass(frozen=True)
class Started(_Value):
    """A delivery went in: the harness turn that took it. For a steer, given when the
    harness confirmed it took the text."""

    KIND = "started"
    turn_id: str | None
    delivery: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class Refused(_Value):
    """A delivery or command that did not go in, and why (`DELIVERY_REFUSALS`,
    `COMMAND_REFUSALS`, or the library's `REFUSED_GONE`).

    A delivery's refusal names the `delivery` and its `ref`; a command's carries the
    reason alone, and so does the library's `Refused("gone")`.
    """

    KIND = "refused"
    reason: str
    delivery: str | None = None
    ref: str | None = None

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.KIND, "reason": self.reason}
        if self.delivery is not None:
            out["delivery"] = self.delivery
            out["ref"] = self.ref
        return out


@dataclass(frozen=True)
class Lost(_Value):
    """turns cannot say which turn, if any, took the delivery.

    Either the endpoint went away first (a reload, a crash, the session ended), or the
    endpoint lived and the delivery's submit closed with no turn tied to it (reason
    `untied: …`), or the harness neither started nor refused it in time (reason
    `expired: …`), or the endpoint holds no record of it (`unknown delivery: …`), or the
    library could not learn its outcome (`unanswered`, which no endpoint sends). `untied`
    tells the endpoint's own two apart: True for `untied` and `expired`, where the
    endpoint is still there and only this delivery is unaccounted for.
    """

    KIND = "lost"
    reason: str
    delivery: str | None = None
    ref: str | None = None

    @property
    def untied(self) -> bool:
        """True when the endpoint lived and decided this delivery reached no turn; False
        when the endpoint went away, could not be reached or holds no record of it."""
        return self.reason.startswith((LOST_UNTIED, LOST_EXPIRED))


@dataclass(frozen=True)
class Ended(_Value):
    """The turn that took a delivery ended, with its outcome (one of `OUTCOMES`)."""

    KIND = "ended"
    turn_id: str | None
    outcome: str
    delivery: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class Ran(_Value):
    """A session command ran. After `clear`, `new_session_id` is the new session's id;
    after `compact`, None. `shells` reports the stop of a command given `stop_shells`, and
    is None without it."""

    KIND = "ran"
    _NESTED = {"shells": Shells}
    new_session_id: str | None = None
    shells: Shells | None = None


@dataclass(frozen=True)
class Failed(_Value):
    """A session command was accepted but did not run, or no answer came in time: the
    endpoint's bound (reason `expired: …`), the call's own wait (`timeout`), or a request
    the library wrote with no answer (`unanswered`). After the last two the command may
    still run."""

    KIND = "failed"
    reason: str


@dataclass(frozen=True)
class Passed(_Value):
    """The wire's answer to an `exit` the harness's gate let through. It is not a result a
    caller sees: the library turns it into `Ran` once the harness process is gone, or
    `Failed("not-exited")` when it still runs after the bound."""

    KIND = "passed"


@dataclass(frozen=True)
class StopFacts(_Value):
    """What the harness says at a Stop of the main loop, for the stop handler's `on_stop`.

    Each fact is None when the harness did not give it.
    """

    KIND = "stop"
    turn_id: str | None = None
    session_id: str | None = None
    stop_hook_active: bool | None = None
    task_types: tuple[str, ...] | None = None
    crons: int | None = None


@dataclass(frozen=True)
class SessionEvent:
    """What a listener is called with: the event and the session's facts.

    `event` is `ready` (observe only), `start` (adds start context), `prompt`, `turn`,
    `gone`, `after-tool`, `usage` or `codex_limits`; `source` is a `ready`'s or a
    `start`'s source; `turn_id` and `outcome` are a `turn`'s; `reason` is a `gone`'s.
    `env` is the session's environment, which the endpoint inherited from the session.

    The fields after `reason` are optional and None where they don't apply, so a
    listener written for the first five events reads this unchanged:

    - `cwd`, on every event, `after-tool` included: the session's launch directory. A
      shell `cd` does not move it, so after one an `after-tool` event's `cwd` is not the
      directory the call ran in; only a guard's `ToolCall.cwd` is that.
    - On `after-tool`: `agent_id` (None on the main loop), `tool`, `tool_use_id`,
      `is_error`, `point` (`after`, or `pre` where the harness has only a pre-tool point)
      and `record_path`, the file the harness writes this session's records to (None
      where it has none).
    - On `prompt`: `text`, the prompt as the harness gave it, and `origin`, its origin
      kind (`composer`, `plugin`, ...), None where the harness gives none.
    - On `usage`: `usage`, the harness's usage reading as given, unparsed, and
      `received_at`, when it arrived (Unix seconds).
    - On `codex_limits`: `payload`, the limits reading as given, unparsed, `received_at`,
      and `kind`, `read` (asked for) or `updated` (pushed).
    """

    event: str
    harness: str
    session_id: str | None
    env: dict[str, str] = field(repr=False, default_factory=dict)
    source: str | None = None
    turn_id: str | None = None
    outcome: str | None = None
    reason: str | None = None
    cwd: str | None = None
    agent_id: str | None = None
    tool: str | None = None
    tool_use_id: str | None = None
    is_error: bool | None = None
    point: str | None = None
    record_path: str | None = None
    text: str | None = None
    origin: str | None = None
    usage: dict[str, Any] | None = None
    received_at: float | None = None
    payload: dict[str, Any] | None = None
    kind: str | None = None


_KINDS: dict[str, type[_Value]] = {
    cls.KIND: cls for cls in (Facts, Agents, Started, Refused, Lost, Ended, Ran, Failed,
                              Passed, StopFacts)}

_METHODS: dict[str, type[_Notification]] = {
    cls.METHOD: cls for cls in (Ready, TurnStarted, TurnSteered, TurnEnded, Gone)}

#: The notifications a `watch` connection carries, in no particular order.
NOTIFICATIONS = tuple(_METHODS)


def from_wire(raw: Any):
    """The value a `to_wire()` dict stands for. Raises `ValueError` on anything else."""
    if not isinstance(raw, dict) or raw.get("kind") not in _KINDS:
        raise ValueError(f"not a turns value: {raw!r}")
    try:
        return _KINDS[raw["kind"]]._build(raw)
    except TypeError as exc:
        raise ValueError(f"malformed {raw.get('kind')} value: {exc}") from None


def from_notification(method: str, params: Any):
    """The notification value a watch's `method` and `params` stand for. Raises
    `ValueError` on a method that is not one of `NOTIFICATIONS`, or malformed params."""
    cls = _METHODS.get(method)
    if cls is None:
        raise ValueError(f"not a turns notification: {method!r}")
    try:
        return cls._build(params)
    except TypeError as exc:
        raise ValueError(f"malformed {method} notification: {exc}") from None


__all__ = ["CAPABILITIES", "ENVIRON_MAX_NAMES", "OUTCOMES", "DELIVERY_REFUSALS", "COMMAND_REFUSALS",
           "READY_SOURCES", "COMMANDS", "COMMAND_WAIT_S", "STOP_SHELLS_WAIT_S",
           "DELIVERY_HOW", "AGENT_KINDS", "AGENT_STATES",
           "BUSY_STATES", "REF_MAX_BYTES", "ERROR_GONE", "ERROR_CAPABILITY",
           "ERROR_STOP_HANDLER_TAKEN", "ERRORS", "REFUSED_GONE", "LOST_UNTIED",
           "LOST_EXPIRED", "LOST_UNKNOWN", "LOST_UNANSWERED", "FAILED_UNANSWERED",
           "FAILED_TIMEOUT", "SHELL_UNLISTED", "NOTIFICATIONS", "check_ref",
           "DeliveryRef", "OpenTurn", "Agent", "LeftShell", "Shells", "Ready",
           "TurnStarted", "TurnSteered", "TurnEnded", "Gone", "Facts", "Agents", "Started",
           "Refused", "Lost", "Ended", "Ran", "Failed", "Passed", "StopFacts",
           "SessionEvent", "from_wire", "from_notification"]
