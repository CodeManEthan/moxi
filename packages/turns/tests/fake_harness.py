"""A fake harness: an adapter whose turns the test starts and ends by hand.

With `auto` on, a handed delivery enters at once (the re-check, then an only-if-idle
delivery's own draft and subagent checks, then the turn starts with the tie); with it off
the test calls `enter()` itself, so a turn can be typed between the submit and the entry.
With `auto_steer` on, a handed steer is confirmed at once (`steered`), or refused `idle`
when no turn is open; with it off the test calls `take()` or `endpoint.refused` itself.

Its subagents: `entries` is the harness's own record, read on every `agents()` call; left
None, the record is made from the counts `live_agents` (None: unreadable) and `shells`.
The live count is always the one derived from that list.
"""

from __future__ import annotations

import itertools
import os
import threading
import time

from moxi_turns.harness import CommandRequest, Delivery, Harness, Live
from moxi_turns.results import Agent, Agents, Ran, Shells


class FakeHarness(Harness):
    name = "fake"

    def __init__(self, can=("deliver", "only-if-idle", "steer", "stop-handler", "clear",
                            "exit", "compact", "agents", "stop-shells"), *, auto=True, auto_steer=True,
                 pid=None):
        self.can = frozenset(can)
        self.pid = os.getpid() if pid is None else pid
        self.cwd = os.getcwd()
        self.auto = auto
        self.auto_steer = auto_steer
        #: How many subagents are live (None: the list cannot be read).
        self.live_agents: int | None = 0
        self.draft = False
        #: Of `live_agents`, how many are background shells.
        self.shells = 0
        #: The harness's own record of its subagents, when a test sets one.
        self.entries: list[Agent] | None = None
        self.agents_complete = True
        self.agents_fail = False
        self.live_fails = False
        self.submitted: list[Delivery] = []
        self.appended: list[Delivery] = []
        self.commands: list[CommandRequest] = []
        self.auto_commands = True
        self.open_turn: str | None = None
        #: What a command's shell stop reports, and the stops made, in order, after the
        #: command took effect.
        self.stop_report = Shells(("sh1",), (), True)
        self.stops: list[str] = []
        #: With `compact_ready` off a compaction raises no Ready; the test raises it.
        self.compact_ready = True
        self._turns = itertools.count(1)
        self._sessions = itertools.count(2)
        self._lock = threading.Lock()

    # -- the core calls these ----------------------------------------------------------

    def live(self) -> Live:
        if self.live_fails:
            raise OSError("the prompt box cannot be read")
        try:
            agents = self.agents()
            return Live.of(agents, self.draft)
        except Exception:  # noqa: BLE001 - the box was read; the list is unknown
            return Live(None, self.draft)

    def agents(self) -> Agents:
        if self.agents_fail:
            raise OSError("the subagent list cannot be read")
        if self.entries is not None:
            return Agents(tuple(self.entries), self.agents_complete, time.time())
        if self.live_agents is None:
            return Agents((), False, time.time())
        shells = min(self.shells, self.live_agents)
        made = [Agent(f"a{n}", "subagent", "running", False)
                for n in range(self.live_agents - shells)]
        made += [Agent(f"sh{n}", "shell", "running", False) for n in range(shells)]
        return Agents(tuple(made), self.agents_complete, time.time())

    def submit(self, delivery: Delivery) -> None:
        self.submitted.append(delivery)
        if self.auto:
            self.enter(delivery)

    def append(self, delivery: Delivery) -> None:
        self.appended.append(delivery)
        if self.auto_steer:
            self.take(delivery)

    def command(self, request: CommandRequest) -> None:
        self.commands.append(request)
        if self.auto_commands:
            self.run_command(request)

    # -- the test drives these ---------------------------------------------------------

    def enter(self, delivery: Delivery) -> str | None:
        """The prompt enters the session: the re-check, then the start's own checks."""
        reason = self.endpoint.entering(delivery.id)
        live = self._entry_live() if delivery.refuse_draft or delivery.refuse_agents else None
        if reason is None and delivery.refuse_draft and (live is None or live.draft):
            reason = "draft"            # an unreadable box holds
        if reason is None and delivery.refuse_agents and (
                live is None or live.agents is None or live.agents):
            reason = "busy-agents"      # an unknown count never reads as 0
        if reason is not None:
            self.endpoint.refused(delivery.id, reason)
            return reason
        self.start_turn(delivery.id)
        return None

    def _entry_live(self) -> Live | None:
        """What the harness reads where the prompt enters (not `live`, which a test may
        slow down): None when `live_fails`, an unreadable read."""
        if self.live_fails:
            return None
        try:
            return Live.of(self.agents(), self.draft)
        except Exception:  # noqa: BLE001 - an unreadable list reads as unknown
            return None

    def handed(self, count: int, timeout: float = 5.0) -> list[Delivery]:
        """The deliveries submitted so far, once there are `count` of them or the timeout
        has passed. The endpoint's worker hands them over after the call has answered."""
        deadline = time.monotonic() + timeout
        while len(self.submitted) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        return list(self.submitted)

    def take(self, delivery: Delivery) -> None:
        """The harness's word on a steer: taken into the open turn, else `idle`."""
        if self.open_turn is None:
            self.endpoint.refused(delivery.id, "idle")
        else:
            self.endpoint.steered(delivery.id)

    def start_turn(self, delivery_id: str | None = None) -> str:
        turn_id = f"t{next(self._turns)}"
        self.open_turn = turn_id
        self.endpoint.turn_started(turn_id, delivery_id)
        return turn_id

    def end_turn(self, outcome: str = "answer") -> None:
        turn_id, self.open_turn = self.open_turn, None
        self.endpoint.turn_ended(turn_id, outcome)

    def run_command(self, request: CommandRequest) -> None:
        if self.endpoint.command_entering(request.id) is not None:
            return
        ep = self.endpoint
        if request.name == "clear":
            new_id = f"s-{next(self._sessions)}"
            ep.ready("clear", new_id)
            if request.stop_shells:
                ep.command_took_effect(request.id)
                self.stops.append(request.id)
                ep.command_ran(request.id, new_session_id=new_id, shells=self.stop_report)
            else:
                ep.command_settled(request.id, Ran(new_id))
        elif request.name == "compact":
            if self.compact_ready:
                ep.ready("compact", ep.ledger.session_id)
            if request.stop_shells:
                self.stops.append(request.id)
                ep.command_ran(request.id, shells=self.stop_report)
            else:
                ep.command_ran(request.id)
        else:
            ep.command_settled(request.id, Ran(None))
