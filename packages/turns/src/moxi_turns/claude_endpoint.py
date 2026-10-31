"""The Claude Code endpoint: the endpoint core with the Claude half (`ClaudeHarness`).

`moxi-turns-endpoint` runs this. The runner plugin spawns it at `session.start` with the
absolute path `MOXI_TURNS_ENDPOINT` names in the session's environment, else the one
`endpoint.json` names; it lives for the session and inherits the session's environment.
It speaks to its runner over the leg in `claude_leg` and to every caller over the
endpoint socket (`contracts/socket-protocol.md`).

The tie rule: each plugin submit has one window, opened when the endpoint hands
the runner its `submit` line and closed exactly once, at the first end it accepts: the
runner's `submit.close` report (`resolve`, `drop` or `fail`), the runner's `submit.bound`
report while the prompt is not admitted, a report the runner could not deliver, the
core's expiry, or the endpoint's own end. Inside the window the endpoint holds the events
of the last main-loop turn whose `turn.start` was reported there; a later `turn.start`
releases the held one as the person's. At the close:

- resolve: the delivery is tied to the held turn; with none held it reads `Lost`
  (`untied: …`);
- drop: a held turn is released; the delivery reads `Refused` with the reason the
  endpoint recorded, else the plugins' own code from the runner's report (`gone`,
  `unsupported`), else `dropped` for another plugin's drop;
- fail: a held turn is released; the delivery reads `Lost` (`untied: …`).

The endpoint hands the next submit only once the window has closed. The ledger opens and
closes turns on the reports at once; only the events and the delivery's answers wait.

The entry rule: a runner prompt enters only as the prompt of the window now open,
admitted once by the endpoint. Every other runner prompt is dropped at entry, logged, and
never runs. The endpoint mints a random window nonce (128 bits) at each hand-over, the
runner puts it on the prompt's first line, and the gate asks `/gate/entry` with it for
every runner prompt. The endpoint admits the prompt only if the nonce names the open
window and that window has admitted nothing; for a start it re-checks there. Admission
and the bound's fail are both decided here, under one lock, in the order the gate's ask
and the runner's report arrive, so no window is both admitted and failed by the bound.

An admitted window's liveness bound: from admission the 5 s
bound no longer fails it. It closes at its submit's settle, by a report the runner could
not deliver, at the endpoint's end, or at the core's expiry, `PENDING_TTL_S` (300 s) from
the hand-over, when the delivery reads `Lost('expired: …')` and the window closes and frees
the slot. A settings hook that hangs, a modal opened between the gate's pass and the
entry, or a Claude Code fault can hold an admitted window that long; nothing is lost, and
a caller that will not wait that long frees itself with its own `wait_*` timeout.

The endpoint's life, a rule: it ends on its own SIGTERM, an EPIPE on its stdout, or
a change of parent, and never on a `session.end` hook. It takes its session id from the
runner on every load, and opens its caller socket (answers `hello`) once it has the id.

Lines and the adapter's lock: `submit`, `append` and `command` come from the endpoint's
ordered worker, and each writes a line through `LineWriter.write`, which blocks while the
runner's pipe is full. No line is written while the adapter's lock is held, so a write
the runner does not read holds the worker and nothing else: the reports, the gate's asks
and the core's expiry still take the lock and settle what they must. A submit line the
window's close (on a leg thread) hands over is written the same way. A line already
being written when its delivery settles `lost` (the core's expiry) may still reach the
runner once it reads again, and its prompt may run; that is the accepted cost of the
bound, and the gate drops it unless its window is the open one.

Guards: the endpoint loads the guards that are on at its first line and at each `ready`,
and hands the runner Claude's table, in the first line and in a `guards` line before the
`ready`'s context answer. The runner matches each tool call against it in its own
process and asks `/ask/guard` only for calls judge guards cover; the endpoint runs those
judges (`moxi_turns.guards.run`). A guard on the list that did not load is named once in
a `ready`'s start context.

After-tool and usage: the hello carries each after-tool row's floor, and a `floors` line
follows each change. The runner asks `/ask/after-tool` after a call ran, naming the rows
the floors let through on the main loop (every row for an agent's call), and the
endpoint asks those rows (`Endpoint.after_tool`), with the call's `record_path`: the
`transcript_path` of the last `ready`. It posts `/report/usage` with each
`session.measure` that moved the context or the rate limits and at each main turn's end;
the endpoint hands the reading to the `usage` rows. A prompt's ask carries its text and
its origin's kind, which go on the `prompt` event.

A steer reads `Started`, and its turn names it, once the runner reports `append.done`
(`$.session.append` resolved without a deny); `append.failed` refuses it `idle`.

Session commands: the runner runs `/clear`, `/exit` and `/compact` through
`$.command.run`, and the gate re-checks each (`/gate/command`). `exit` settles `passed` at
the gate's pass. A plain `clear` settles `ran` at the new session's SessionStart. A
`compact` settles `ran` at its SessionStart with source `compact` (the core's rule for
every harness). With `stop_shells` the runner stops the session's background shells and
Monitors after the command has taken effect, and both settle only at its `command.done`,
which carries the stop's report: a `clear`'s SessionStart records the new id and marks the
moment of effect, and settles nothing.
"""

from __future__ import annotations

import contextlib
import itertools
import logging
import os
import secrets
import select
import signal
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from . import claude_leg as leg
from . import listeners as turns_listeners
from .guards import ToolCall
from .guards.run import GuardRunner, prompts_of
from .harness import CommandRequest, Delivery, Harness, Live
from .results import (Agent, Agents, COMMAND_REFUSALS, LOST_UNTIED, OUTCOMES, SHELL_UNLISTED, Failed,
                      LeftShell, Passed, Ran, Refused, Shells, StopFacts)

log = logging.getLogger("moxi.turns.claude")

#: How long `live()` waits for the runner's `/live` answer. Past it the live facts are
#: unreadable: a start that asked for the draft or subagent refusal refuses, and a
#: command refuses `draft` (an unreadable box holds).
LIVE_WAIT_S = 1.0
#: How often the endpoint looks at its parent when no pidfd can watch it.
PARENT_POLL_S = 0.5
#: How many closed windows the endpoint remembers, to name a late entry's delivery.
CLOSED_KEPT = 64


@dataclass
class _Window:
    """One plugin submit's window."""

    delivery: str
    #: The leg's word for the submit: `start` for an only-if-idle delivery, else
    #: `deliver`.
    kind: str
    #: The window nonce: random, 128 bits, minted at the hand-over.
    nonce: str = field(default_factory=lambda: secrets.token_hex(16))
    #: The endpoint admitted the window's prompt (`/gate/entry` passed it). From here the
    #: runner's bound no longer fails the window.
    admitted: bool = False
    opened_at: float = field(default_factory=time.monotonic)
    #: The turn held in the window: the last main-loop `turn.start` reported in it.
    held: str | None = None
    held_set: bool = False
    #: The delivery, for the window's submit line.
    spec: Delivery | None = None


class ClaudeHarness(Harness):
    """The Claude half of an endpoint: the runner's leg under the endpoint core."""

    name = "claude"
    can = frozenset({"deliver", "only-if-idle", "steer", "stop-handler", "clear", "exit",
                     "compact", "agents", "stop-shells"})
    live_wait_s = LIVE_WAIT_S

    def __init__(self, out: BinaryIO, *, pid: int | None = None, cwd: str | None = None,
                 on_end=None, guards: GuardRunner | None = None):
        self.pid = os.getppid() if pid is None else pid
        self.cwd = os.getcwd() if cwd is None else cwd
        #: The guard runner; made at `attach` with the endpoint's environment when None.
        self.guards = guards
        #: The file Claude writes the session's records to, once the runner names it.
        self.record_path: str | None = None
        self.mod_version: str | None = None
        self._lines = leg.LineWriter(out, on_broken=on_end)
        self._lock = threading.RLock()
        self._window: _Window | None = None
        self._pending: list[Delivery] = []
        self._last_seq = 0
        self._asks = itertools.count(1)
        self._live: dict[int, list] = {}
        self._commands: dict[str, CommandRequest] = {}
        self._clear_passed: str | None = None
        #: A `clear` with `stop_shells` past its SessionStart: its request id and the new
        #: session's id, which its `command.done` settles with.
        self._clear_effect: tuple[str, str | None] | None = None
        #: Delivery ids the gate refused, for a `drop` close.
        self._gate_refused: set[str] = set()
        #: Closed windows by nonce: (delivery, how it closed), to name a late entry.
        self._closed: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self.session_known = threading.Event()
        self.server: leg.LegServer | None = None

    # -- the leg ------------------------------------------------------------------------

    @property
    def broken(self) -> bool:
        return self._lines.broken

    def attach(self, endpoint) -> None:
        super().attach(endpoint)
        if self.guards is None:
            self.guards = GuardRunner(self.name, env=endpoint.env)

    def serve(self, path: str) -> None:
        """Listen on `mod.sock` at `path`."""
        self.server = leg.LegServer(path, self.handle).start()
        os.chmod(path, 0o600)

    def first_line(self) -> bool:
        ep = self.endpoint
        return self._lines.write({
            "op": leg.LINE_HELLO, "leg": leg.LEG, "mod_sock": str(self.server.path),
            "stop_wait_ms": int(ep.stop_wait_s * 1000),
            "listen_wait_ms": int(ep.listen_wait_s * 1000),
            "endpoint_version": ep.version, "pid": os.getpid(),
            "stop_after_clear": leg.STOP_AFTER_CLEAR, "guards": self.guards.reload(),
            "after_tool_wait_ms": int(ep.after_tool_wait_s * 1000),
            "floors": ep.floors.values()})

    def handle(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path == leg.PATH_REPORT:
            self.report(body)
            return {}
        if path in (leg.PATH_LIVE, leg.PATH_AGENTS):
            self._live_answer(body)
            return {}
        if path == leg.PATH_STOP:
            return {"reason": self.endpoint.on_stop(_stop_facts(body))}
        if path == leg.PATH_CONTEXT:
            return {"context": self._context(body)}
        if path == leg.PATH_GUARD:
            return self._guard(body)
        if path == leg.PATH_AFTER_TOOL:
            return {"context": self._after_tool(body)}
        if path == leg.PATH_USAGE:
            usage = body.get("usage")
            if not isinstance(usage, dict):
                raise leg.BadRequest('needs "usage", an object')
            self.endpoint.usage(usage)
            return {}
        if path == leg.PATH_GATE_ENTRY:
            return self._gate_entry(body)
        if path == leg.PATH_GATE_COMMAND:
            return self._gate_command(body)
        raise leg.BadRequest(f"no such path {path!r}")

    def close(self) -> None:
        if self.guards is not None:
            self.guards.close()
        if self.server is not None:
            try:
                self.server.close()
            except OSError:
                pass
            # A socket outside the endpoint's directory goes with the server; one inside
            # goes with the directory.
            with contextlib.suppress(OSError):
                os.unlink(self.server.path)

    # -- the core calls these -----------------------------------------------------------

    def live(self) -> Live:
        answer = self._ask_runner(leg.LINE_LIVE, "the live facts")
        agents, draft = answer.get("agents"), answer.get("draft")
        # `agents` null: the runner could not list the subagents. Unknown, which counts
        # as busy and never reads as 0.
        if (isinstance(agents, bool) or not (agents is None or isinstance(agents, int))
                or not isinstance(draft, bool)):
            raise OSError(f"the runner's live answer is malformed: {answer!r}")
        return Live(agents=agents, draft=draft)

    def agents(self) -> Agents:
        """The runner's `$.agent.list()`, read on this call; the states are mapped here. A
        list the runner could not read, or that came back malformed, is not complete."""
        answer = self._ask_runner(leg.LINE_AGENTS, "agents")
        return agents_from_runner(answer.get("entries"), time.time())

    def floors_changed(self, values: dict[str, int]) -> None:
        """The core read a floor that changed: the runner gets the whole map."""
        self._lines.write({"op": leg.LINE_FLOORS, "rows": values})

    def _ask_runner(self, op: str, what: str) -> dict:
        """Send the runner a line that asks for a live read and wait `LIVE_WAIT_S` for its
        answer. Raises when the line is closed, the answer is late or it names an error."""
        ask = next(self._asks)
        slot: list = [threading.Event(), None]
        with self._lock:
            self._live[ask] = slot
        try:
            if not self._lines.write({"op": op, "ask": ask}):
                raise OSError("the runner's line is closed")
            if not slot[0].wait(LIVE_WAIT_S):
                raise TimeoutError(f"the runner did not answer {what} within {LIVE_WAIT_S} s")
        finally:
            with self._lock:
                self._live.pop(ask, None)
        answer = slot[1] or {}
        if answer.get("error") is not None:
            raise OSError(f"the runner could not read {what}: {answer['error']}")
        return answer

    def submit(self, delivery: Delivery) -> None:
        with self._lock:
            self._pending.append(delivery)
        self._hand_next()

    def expired(self, delivery_id: str) -> None:
        """The core expired the delivery (`PENDING_TTL_S`): its window closes now, admitted
        or not, so it holds no turn and no later submit (the liveness bound, above)."""
        with self._lock:
            w = self._window
            if w is not None and w.delivery == delivery_id:
                log.warning("window of %s (%s) expired; it closes", delivery_id,
                            "admitted" if w.admitted else "not admitted")
                self._close(leg.END_FAIL, "expired: the submit never settled")
        self._hand_next()

    def append(self, delivery: Delivery) -> None:
        if not self._lines.write({"op": leg.LINE_APPEND, "delivery": delivery.id,
                                  "text": delivery.text}):
            raise OSError("the runner's line is closed")

    def command(self, request: CommandRequest) -> None:
        with self._lock:
            self._commands[request.id] = request
        if not self._lines.write({"op": leg.LINE_COMMAND, "request": request.id,
                                  "name": request.name,
                                  "stop_shells": request.stop_shells}):
            raise OSError("the runner's line is closed")

    # -- the window ---------------------------------------------------------------------

    def _hand_next(self) -> None:
        """Hand the runner the next pending submit, once no window is open. A delivery
        the core settled meanwhile (refused, expired) is skipped. The window opens under
        the lock; its line is written after the lock is let go."""
        while True:
            with self._lock:
                w = self._open_next()
                if w is None:
                    return
                d = w.spec
            if self._lines.write({"op": leg.LINE_SUBMIT, "delivery": d.id,
                                  "kind": w.kind, "text": d.text, "nonce": w.nonce}):
                return
            with self._lock:
                if self._window is not w:
                    return
                self._close(leg.END_FAIL, "the runner's line is closed")

    def _open_next(self) -> _Window | None:
        """Under the lock: open the window of the next pending submit still waiting, or
        None (a window is open, or nothing waits)."""
        while self._window is None and self._pending:
            d = self._pending.pop(0)
            if not self.endpoint.is_waiting(d.id):
                continue
            # The window counts as open from here, a few ms before the runner's call.
            w = self._window = _Window(d.id, "start" if d.only_if_idle else "deliver")
            w.spec = d
            return w
        return None

    def _close(self, end: str, reason: str | None) -> None:
        """Under the lock: the open window closes by `end`."""
        w, self._window = self._window, None
        if w is None:
            return
        self._closed[w.nonce] = (w.delivery, end)
        while len(self._closed) > CLOSED_KEPT:
            self._closed.popitem(last=False)
        ep = self.endpoint
        if end == leg.END_RESOLVE:
            if w.held_set:
                ep.turn_tied(w.held, w.delivery)
            else:
                ep.lost(w.delivery, f"{LOST_UNTIED}: the submit resolved with no turn "
                                    "started in its window")
        else:
            if w.held_set:
                ep.turn_released(w.held)
            if end == leg.END_DROP:
                # The reason the endpoint recorded stands; else the plugins' own code
                # (the gate's `gone`, the runner's `unsupported`); else another plugin's
                # drop, `dropped`.
                refused_by_gate = w.delivery in self._gate_refused
                if not refused_by_gate:
                    ep.refused(w.delivery, reason if reason in leg.GATE_CODES
                               else "dropped")
                log.info("submit %s dropped%s: %s", w.delivery,
                         " by the gate" if refused_by_gate else "", reason)
            else:
                ep.lost(w.delivery, f"{LOST_UNTIED}: the submit failed"
                                    + (f": {reason}" if reason else ""))
        self._gate_refused.discard(w.delivery)

    # -- reports ------------------------------------------------------------------------

    def report(self, body: dict[str, Any]) -> None:
        kind = body.get("kind")
        if kind not in leg.REPORTS:
            raise leg.BadRequest(f"unknown report kind {kind!r}")
        seq = body.get("seq")
        with self._lock:
            if isinstance(seq, int) and not isinstance(seq, bool):
                if seq <= self._last_seq:
                    return                      # applied already: a resend
                if seq > self._last_seq + 1 and self._window is not None:
                    log.warning("reports %d to %d from the runner were lost; the open "
                                "submit window fails", self._last_seq + 1, seq - 1)
                    self._close(leg.END_FAIL, "a report from the runner was lost")
                self._last_seq = seq
            self._apply(kind, body)
        self._hand_next()                       # a report may have closed the window

    def _apply(self, kind: str, body: dict[str, Any]) -> None:
        ep = self.endpoint
        if kind == leg.R_SESSION:
            version = body.get("mod_version")
            if isinstance(version, str):
                self.mod_version = version
            self._learn(_opt_str(body, "session_id"))
        elif kind == leg.R_TURN_START:
            turn_id = _opt_str(body, "turn_id")
            w = self._window
            if w is None:
                ep.turn_started(turn_id)
                return
            if w.held_set:
                ep.turn_released(w.held)   # only the last can be the delivery's
            ep.turn_started(turn_id, held=True)
            w.held, w.held_set = turn_id, True
        elif kind == leg.R_TURN_COMPLETE:
            ep.turn_ended(_opt_str(body, "turn_id"), _outcome(body))
        elif kind == leg.R_SUBMIT_CLOSE:
            delivery, end = body.get("delivery"), body.get("end")
            if end not in leg.ENDS:
                raise leg.BadRequest(f"unknown submit end {end!r}")
            if self._window is None or self._window.delivery != delivery:
                log.info("submit %s settled (%s) after its window closed; it changes "
                         "nothing", delivery, end)
                return
            self._close(end, _opt_str(body, "reason"))
        elif kind == leg.R_SUBMIT_BOUND:
            delivery, w = body.get("delivery"), self._window
            if w is None or w.delivery != delivery:
                log.info("the bound of submit %s came after its window closed; it changes "
                         "nothing", delivery)
            elif w.admitted:
                log.info("the bound of submit %s came after its prompt was admitted; the "
                         "window waits for the settle", delivery)
            else:
                self._close(leg.END_FAIL, _opt_str(body, "reason") or "the runner's bound")
        elif kind == leg.R_APPEND_DONE:
            delivery = body.get("delivery")
            if isinstance(delivery, str):
                ep.steered(delivery)
        elif kind == leg.R_APPEND_FAILED:
            delivery = body.get("delivery")
            if isinstance(delivery, str):
                ep.refused(delivery, "idle")
        elif kind == leg.R_COMMAND_DONE:
            self._command_done(body)

    def _learn(self, session_id: str | None) -> None:
        if session_id:
            self.endpoint.learn_session(session_id)
            self.session_known.set()

    # -- asks ---------------------------------------------------------------------------

    def _live_answer(self, body: dict[str, Any]) -> None:
        with self._lock:
            slot = self._live.get(body.get("ask"))
        if slot is not None:
            slot[1] = body
            slot[0].set()

    def _context(self, body: dict[str, Any]) -> str | None:
        event = body.get("event")
        session_id = _opt_str(body, "session_id")
        if event == "ready":
            source = _opt_str(body, "source")
            record_path = _opt_str(body, "transcript_path")
            if record_path is not None:
                self.record_path = record_path
            self._learn(session_id)
            # The new table reaches the runner before the session's first call can.
            self._lines.write({"op": leg.LINE_GUARDS, "table": self.guards.reload()})
            if source == "clear":
                self._clear_ran(session_id)
            context = self.endpoint.ready(source, session_id)
            notes = self.guards.take_notes()
            if notes:
                context = turns_listeners.JOIN.join([*([context] if context else []),
                                                     "\n".join(notes)])
            return context
        if event == "prompt":
            text = body.get("text")
            return self.endpoint.prompt(text if isinstance(text, str) else None,
                                        _opt_str(body, "origin"))
        raise leg.BadRequest(f"unknown context event {event!r}")

    def _guard(self, body: dict[str, Any]) -> dict[str, Any]:
        """`/ask/guard`: judge one tool call by the guards the runner names. Holds this
        request's thread until the largest named deadline at most."""
        call, ids = body.get("call"), body.get("guards")
        if not isinstance(call, dict) or not isinstance(call.get("tool"), str) \
                or not call["tool"]:
            raise leg.BadRequest('needs "call" with a "tool"')
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise leg.BadRequest('needs "guards", a list of guard ids')
        given = call.get("input")
        if given is not None and not isinstance(given, dict):
            raise leg.BadRequest('"input" must be an object')
        ep = self.endpoint
        tc = ToolCall(self.name, ep.ledger.session_id, _opt_str(call, "agentId"),
                      call["tool"], _opt_str(call, "tool_use_id"), dict(given or {}),
                      self._call_cwd(call), self.record_path, ep.env,
                      prompts_of(call.get("prompts")))
        return self.guards.ask(tc, ids).to_leg()

    def _call_cwd(self, call: dict[str, Any]) -> str:
        """The directory a call runs in: the ask's `cwd` when it is an absolute path, else
        the launch directory. A shell `cd` moves the first and never the second."""
        cwd = _opt_str(call, "cwd")
        return cwd if cwd is not None and os.path.isabs(cwd) else self.cwd

    def _after_tool(self, body: dict[str, Any]) -> str | None:
        """`/ask/after-tool`: ask the rows the runner names about one call that ran.
        Holds this request's thread for `after_tool.wait_ms` at most."""
        call, rows = body.get("call"), body.get("rows")
        if not isinstance(call, dict) or not isinstance(call.get("tool"), str) \
                or not call["tool"]:
            raise leg.BadRequest('needs "call" with a "tool"')
        if rows is not None and (not isinstance(rows, list)
                                 or not all(isinstance(i, str) for i in rows)):
            raise leg.BadRequest('"rows" must be a list of row ids')
        is_error = call.get("is_error")
        return self.endpoint.after_tool(
            call["tool"], tool_use_id=_opt_str(call, "tool_use_id"),
            agent_id=_opt_str(call, "agentId"),
            is_error=is_error if isinstance(is_error, bool) else None, rows=rows)

    def _gate_entry(self, body: dict[str, Any]) -> dict[str, Any]:
        """The gate asks for a runner prompt as it enters. Under the lock
        that applies the runner's reports, so the ask and the bound's report are decided
        in the order they arrive. The answer is recorded before it is sent."""
        nonce = body.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            raise leg.BadRequest('needs "nonce"')
        ep = self.endpoint
        with self._lock:
            w = self._window
            if w is None or w.nonce != nonce or w.admitted:
                if w is not None and w.nonce == nonce:
                    what = f"window of {w.delivery} admitted already"
                elif nonce in self._closed:
                    delivery, end = self._closed[nonce]
                    what = f"window of {delivery} closed by {end}; its answer stands"
                else:
                    what = "a nonce this endpoint never minted (an earlier endpoint's?)"
                log.warning("late entry: a runner prompt for a %s; dropped", what)
                return _drop(leg.LATE, None)
            reason = ep.entering(w.delivery)
            if reason is None and not ep.is_waiting(w.delivery):
                reason = "gone"           # the core settled it meanwhile
            spec = w.spec
            if reason is None and spec is not None and spec.only_if_idle:
                if spec.refuse_draft and body.get("draft") is not False:
                    reason = "draft"      # a draft, or a box the gate could not read
                elif spec.refuse_agents and _busy_agents(body.get("agents")):
                    reason = "busy-agents"
                if reason is not None:
                    ep.refused(w.delivery, reason)
            if reason is not None:
                self._gate_refused.add(w.delivery)
                log.info("entry of %s refused: %s", w.delivery, reason)
                return _drop(reason, w.kind)
            w.admitted = True
            log.info("entry of %s admitted", w.delivery)
        return {"verdict": "pass"}

    def _gate_command(self, body: dict[str, Any]) -> dict[str, Any]:
        request_id = body.get("request")
        ep = self.endpoint
        with self._lock:
            request = self._commands.get(request_id) if isinstance(request_id, str) else None
        if request is None:
            reason = "unknown"
        else:
            reason = ep.command_entering(request.id)
            if reason is None:
                if body.get("draft") is not False:
                    reason = "draft"
                elif _busy_agents(body.get("agents")):
                    reason = "busy-agents"
                if reason is not None:
                    ep.command_settled(request.id, Refused(reason))
        if reason is not None:
            return {"verdict": "drop", "reason": reason,
                    "text": f"moxi-turns-gate: /{request.name if request else '?'} was "
                            f"refused ({reason})"}
        # The decision is recorded before the gate passes it.
        # A compaction settles at its Ready, the core's rule.
        if request.name == "exit":
            ep.command_settled(request.id, Passed())
        elif request.name == "clear":
            with self._lock:
                self._clear_passed = request.id
        return {"verdict": "pass"}

    def _clear_ran(self, new_session_id: str | None) -> None:
        """The SessionStart of a `clear` the gate passed: a plain one settles `ran` here;
        one with `stop_shells` has taken effect, and settles at its `command.done`."""
        with self._lock:
            request_id, self._clear_passed = self._clear_passed, None
            request = self._commands.get(request_id) if request_id is not None else None
            if request is not None and request.stop_shells:
                self._clear_effect = (request.id, new_session_id)
        if request_id is None:
            return
        if request is not None and request.stop_shells:
            self.endpoint.command_took_effect(request_id)
        else:
            self.endpoint.command_settled(request_id, Ran(new_session_id))

    def _command_done(self, body: dict[str, Any]) -> None:
        request_id, state = body.get("request"), body.get("state")
        with self._lock:
            request = self._commands.pop(request_id, None) if isinstance(request_id,
                                                                        str) else None
        if request is None:
            return
        reason = _opt_str(body, "reason") or state or "unknown"
        ep = self.endpoint
        with self._lock:
            effect, self._clear_effect = self._clear_effect, None
            if effect is not None and effect[0] != request.id:
                self._clear_effect, effect = effect, None
            if self._clear_passed == request.id:
                self._clear_passed = None
        if state == "ran":
            shells = _shells(body.get("shells")) if request.stop_shells else None
            if request.name == "clear":
                new_id = _opt_str(body, "new_session_id") or (effect[1] if effect else None)
                self._learn(new_id)
                new_id = new_id or ep.ledger.session_id
                if request.stop_shells:
                    ep.command_ran(request.id, new_session_id=new_id, shells=shells)
                else:
                    ep.command_settled(request.id, Ran(new_id))
            elif request.name == "compact":
                ep.command_ran(request.id, shells=shells)
            else:
                ep.command_settled(request.id, Passed())
        elif state == "refused" and reason in COMMAND_REFUSALS:
            ep.command_settled(request.id, Refused(reason))
        elif state == "refused":
            # The plugins' own word (the gate's `gone`: it could not reach this endpoint
            # in time) is no refusal the contract names: the command did not run.
            ep.command_settled(request.id, Failed(f"the gate withdrew it: {reason}"))
        else:
            ep.command_settled(request.id, Failed(reason))


def _drop(reason: str, kind: str | None) -> dict[str, Any]:
    """A `/gate/entry` drop: the reason, and the one line the person sees, which the runner
    reads the reason back from."""
    what = {"start": "the start was refused", "deliver": "the delivery was refused"}.get(
        kind or "", "a late prompt was dropped")
    return {"verdict": "drop", "reason": reason, "text": f"moxi-turns-gate: {what} ({reason})"}


def _shells(value: Any) -> Shells:
    """The runner's report of a shell stop. One turns cannot read is a list it cannot
    vouch for: nothing named, the unnamed entry left."""
    unnamed = LeftShell(None, SHELL_UNLISTED)
    try:
        if not isinstance(value, dict):
            raise ValueError("not an object")
        stopped, left, complete = value.get("stopped"), value.get("left"), value.get("complete")
        if not isinstance(complete, bool):
            raise ValueError("complete is not a bool")
        if not isinstance(stopped, list) or not all(isinstance(i, str) for i in stopped):
            raise ValueError("stopped is not a list of ids")
        if not isinstance(left, list):
            raise ValueError("left is not a list")
        entries = []
        for e in left:
            if (not isinstance(e, dict) or not isinstance(e.get("why"), str)
                    or not (e.get("id") is None or isinstance(e.get("id"), str))):
                raise ValueError(f"a left entry is malformed: {e!r}")
            entries.append(LeftShell(e.get("id"), e["why"]))
    except ValueError as exc:
        log.warning("the runner's shell report cannot be read (%s): %r", exc, value)
        return Shells((), (unnamed,), False)
    if not complete and unnamed not in entries:
        entries.append(unnamed)
    return Shells(tuple(stopped), tuple(entries), complete)


#: Claude Code's agent statuses (`AgentStatus`) as turns' states.
CLAUDE_STATES = {"pending": "pending", "running": "running", "waiting": "waiting",
                 "idle": "idle", "completed": "done", "failed": "failed",
                 "killed": "stopped"}
#: The statuses that are a final notice.
CLAUDE_FINAL = frozenset({"completed", "failed", "killed"})


def agents_from_runner(entries: Any, at: float) -> Agents:
    """The runner's unmapped `$.agent.list()` entries as `Agents`. Null is a list the runner
    could not read; an entry with no id makes the list not complete, never shorter. A
    status turns does not know reads `unknown`, with no notice to call final."""
    if not isinstance(entries, list):
        return Agents((), False, at)
    out: list[Agent] = []
    complete = True
    for raw in entries:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) \
                or not raw["id"]:
            complete = False
            continue
        status = raw.get("status")
        state = CLAUDE_STATES.get(status, "unknown") if isinstance(status, str) else "unknown"
        final = (status in CLAUDE_FINAL) if state != "unknown" else None
        teammate = isinstance(raw.get("teammateId"), str) or raw.get("type") == "teammate"
        out.append(Agent(raw["id"], "teammate" if teammate else "subagent", state, final,
                         _opt_str(raw, "description"), _opt_str(raw, "parentId")))
    return Agents(tuple(out), complete, at)


def _opt_str(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) and value else None


def _busy_agents(agents: Any) -> bool:
    """A subagent pending, running or waiting, or a count the gate could not read."""
    return isinstance(agents, bool) or not isinstance(agents, int) or agents > 0


def _outcome(body: dict[str, Any]) -> str:
    if body.get("is_aborted") is True:
        return "aborted"
    reason = body.get("reason")
    return reason if reason in OUTCOMES else "error"


def _stop_facts(body: dict[str, Any]) -> StopFacts:
    tasks = body.get("task_types")
    crons = body.get("crons")
    active = body.get("stop_hook_active")
    return StopFacts(
        turn_id=_opt_str(body, "turn_id"), session_id=_opt_str(body, "session_id"),
        stop_hook_active=active if isinstance(active, bool) else None,
        task_types=tuple(t for t in tasks if isinstance(t, str))
        if isinstance(tasks, list) else None,
        crons=crons if isinstance(crons, int) and not isinstance(crons, bool) else None)


# -- the process ------------------------------------------------------------------------


class Life:
    """What ends the endpoint: its SIGTERM, an EPIPE on its stdout, a change of parent."""

    def __init__(self):
        self.ended = threading.Event()
        self.reason: str | None = None
        self._lock = threading.Lock()

    def end(self, reason: str) -> None:
        with self._lock:
            if self.reason is None:
                self.reason = reason
        self.ended.set()

    def wait(self, *events: threading.Event) -> None:
        """Until the end, or one of `events` is set. Wakes often, so a signal's handler
        runs promptly on the main thread."""
        while not self.ended.is_set() and not any(e.is_set() for e in events):
            self.ended.wait(0.2)

    def watch_parent(self, ppid: int) -> threading.Thread:
        thread = threading.Thread(target=self._watch_parent, args=(ppid,), daemon=True,
                                  name="turns-claude-parent")
        thread.start()
        return thread

    def _watch_parent(self, ppid: int) -> None:
        fd = None
        try:
            fd = os.pidfd_open(ppid)
        except (AttributeError, OSError):
            fd = None
        poller = None
        if fd is not None:
            poller = select.poll()
            poller.register(fd, select.POLLIN)
        try:
            while not self.ended.is_set():
                if os.getppid() != ppid:
                    self.end(f"the endpoint's parent {ppid} is gone (now {os.getppid()})")
                    return
                if poller is not None:
                    if poller.poll(int(PARENT_POLL_S * 1000)):
                        self.end(f"the endpoint's parent {ppid} ended")
                        return
                else:
                    time.sleep(PARENT_POLL_S)
        finally:
            if fd is not None:
                os.close(fd)


def mod_sock_path(ep) -> str:
    """Where endpoint `ep` listens for its runner: `mod.sock` in its directory, or, when
    that path is too long for a socket address (a long `MOXI_HOME` or runtime directory),
    `moxi.paths.socket_path`'s path for the name `<endpoint name>-mod`, which falls back to
    a short hashed path as the endpoint's own address does."""
    from . import protocol

    return protocol.endpoint_socket(ep.dir, protocol.MOD_SOCK)


def main(argv: list[str] | None = None) -> int:
    """`moxi-turns-endpoint`: spawned by the runner, never typed by a person."""
    # The leg owns the real stdout. Anything else that prints (a listener, a library)
    # goes to stderr, so it can never break a line the runner reads.
    leg_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="moxi-turns-endpoint[%(process)d] %(levelname)s %(message)s")
    life = Life()
    # Only the three ends count: a terminal's interrupt or hangup is not one.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: life.end("SIGTERM: Claude Code ended the "
                                                      "endpoint"))
    ppid = os.getppid()
    harness = ClaudeHarness(leg_out, pid=ppid, on_end=life.end)

    from moxi.config import ConfigError

    from . import protocol
    from .endpoint import Endpoint

    try:
        ep = Endpoint(harness)
    except ConfigError as exc:
        log.error("turns' config cannot be read; no endpoint: %s", exc)
        leg.LineWriter(leg_out).write({"op": leg.LINE_FAILED, "leg": leg.LEG,
                                       "error": str(exc)})
        return 2
    ep.prepare()
    sock: str | None = None
    try:
        sock = mod_sock_path(ep)
        harness.serve(sock)
    except OSError as exc:
        log.error("cannot listen on %s: %s", sock or "mod.sock", exc)
        leg.LineWriter(leg_out).write({"op": leg.LINE_FAILED, "leg": leg.LEG,
                                       "error": f"mod.sock: {exc}"})
        ep.stop(f"mod.sock: {exc}")
        return 2
    life.watch_parent(ppid)
    harness.first_line()
    # Callers find the endpoint only once it has its session id.
    life.wait(harness.session_known)
    if not life.ended.is_set():
        try:
            ep.open()
        except OSError as exc:
            life.end(f"cannot open the endpoint socket: {exc}")
    life.wait()
    ep.stop(life.reason or "the endpoint ended")
    return 0


if __name__ == "__main__":
    sys.exit(main())
