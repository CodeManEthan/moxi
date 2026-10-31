"""`moxi-launcher-job`: the launcher's detached runs, one process per run.

    moxi-launcher-job relaunch RUN        # a relaunch run, self or director
    moxi-launcher-job repair RUN          # a repair run, from a failed run's record
    moxi-launcher-job expiry SESSION      # an expiry ask, started by cache's timer
    moxi-launcher-job keep-warm SESSION   # a keep-warm ask, started by cache's timer
    moxi-launcher-job compact RUN         # a compact at the context limit

Internal, never typed. A relaunch and a repair are spawned as slots jobs
(`relaunch-<run>`, `repair-<run>`) by `moxi launcher relaunch`. A slots job is a child of
slots' own tmux server, so it runs outside every session from its first instant: it
outlives the session it exits, and comms takes its notices under the launcher's reserved
name. Cache's timer starts an expiry ask (`recycle.ask_expiry`) and a keep-warm ask
(`keepwarm.ask`), and a crossing or the end of a run that took a pending compact spawns a
compact detached (`moxi.proc.spawn`); each records its own process as its run's job.

**The relaunch run** takes these steps, each writing its event before the next starts:

1. Wait (self runs only). The quiet read of the session, taken when its watch opens and
   at each of the watch's events, and between events every 2 s the person's signs alone
   (the pane and the window of a turn no delivery opened, which change with no event).
   Each change of the first word that holds is one `wait` event. Nothing holding goes on;
   `relaunch.wait_s` after the run was armed it ends `expired`.
2. Hold the slot for the run (`slots.hold`, holder the run id). Refused: `not-held`.
3. Exit, through turns, with the turn number the last quiet read saw. turns checks the
   launch's consent, idleness and the number again. `Ran`, any `Failed` and turns' `gone`
   go on to the pane; a refusal that clears by itself (`busy-turn`, `draft`,
   `busy-agents`, `stale-turn`, `rate`, `in-flight`, `loading`) sends a self run back to
   step 1 with the hold released and ends a director's run `held`; `not-allowed` and
   `unsupported` end it `not-exited`.
4. The exit dialog. Within `relaunch.dialog_s`: a pane that ended goes on; once the dialog
   has fully matched on two reads at least 0.5 s apart, the last checks (turns' facts show
   the same turn number and no open turn; the pane shows no person) and then at once the
   answer, with no other read between. A failed check presses nothing: `exit-pending`. A
   turns that answers `gone` at the dialog leaves the pane check alone, recorded
   `turns-gone`. The answer: `ended` goes on, `mismatch` or `not-ended` end the run
   `not-exited`, `gone` or `no-pane` end it `relaunch-failed`. No dialog and no end of the
   pane in time: `not-exited`.
5. The name. With comms, up to `relaunch.name_wait_s` for the old session's name to be let
   go; still held: `relaunch-failed`.
6. Respawn: the launch row made again (`api.ReplayRequest`), into the same slot's ended
   pane under the run's hold, which the respawn ends. A refusal or failure:
   `relaunch-failed`.
7. A self run's new session gets its handoff as its first turn; not sent:
   `first-turn-not-sent`.
8. `relaunched`, with the new request id on the run.

**A stop signal before step 3 ends the run `stopped`; from step 3 on it is recorded and
deferred until step 6 has been tried.** **A job acts only while its run is open**: right
before each act on the session (the exit, the dialog's answer, the respawn) it moves its
run forward with one conditional update, and one that matches nothing means the run was
ended under it, and it stops without acting. **Every terminal path releases the run's
slot hold**; only a respawn that succeeds ends it another way.

**The repair run** repeats steps 5-7 from a run's record, for a run that exited its
session and did not relaunch it. It acts under the original run's id as the slot's
holder, so a hold the original left behind reads as its own. The recorded slot is used
when it is free or ended, or held by the original run; it is refused `occupied-by-chain`
when it is occupied by the launch chain's own session (the original died after its
respawn); otherwise (held or occupied by anyone outside the chain, or missing) the launch
goes to the lowest launchable slot. Its posture and first turn are the original run's.

Each run ends with its outcome and tells whom `notices` says. As it starts, each job
deletes ended runs older than `records.keep_days`.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from . import notices, quiet, slots, store

log = logging.getLogger("moxi.launcher.job")

#: The runs this script runs.
KINDS = ("relaunch", "repair", "expiry", "compact", "keep-warm")
#: The kinds that are an ask of a session, by its id.
ASKS = ("expiry", "keep-warm")
#: Refusals of the exit that clear by themselves.
TRANSIENT = ("busy-turn", "draft", "busy-agents", "stale-turn", "rate", "in-flight",
             "loading")
#: Refusals of the exit that end the run `not-exited`.
TERMINAL = ("not-allowed", "unsupported")
#: turns' word for an endpoint that is gone or could not be reached.
GONE = "gone"
#: How often the wait re-reads the person's signs between the watch's events, seconds.
POLL_S = 2.0
#: How far apart two full matches of the exit dialog must be before it is answered.
SETTLE_S = 0.5
#: The word slots' answer leads its words with when its settle bound ran out.
NOT_SETTLED = "not-settled"
#: How often the name wait asks comms again, in seconds.
NAME_POLL_S = 0.5
#: How long a compact waits after a refusal of turns' before it reads the session again.
RETRY_S = 5.0
#: How often, and how many times, a job tries its run's first read.
FIRST_READS, FIRST_READ_S = 5, 0.2
#: The outcomes after which a run's session is down and its record can launch it again.
REPAIRABLE = ("relaunch-failed", "gone", "error")
#: The events that mark a run as having acted on its session (the exit, or later).
ACTED = ("exit", "respawn")

#: Stands for a seam the job looks up itself.
LOOK: Any = object()


class _Ended(Exception):
    """The run has ended, here or under the job."""


#: Raised by a run's `end` (and when the run was ended under it) to stop its steps; a
#: run that takes the relaunch's steps in its own order catches it.
Ended = _Ended


class _Again(Exception):
    """A self run goes back to its wait."""

    def __init__(self, word: str, detail: str = ""):
        super().__init__(word)
        self.word, self.detail = word, detail


# -- what other parts share -------------------------------------------------------------


def job_name(kind: str, run_id: str) -> str:
    """The slots job a run of `kind` runs as: `relaunch-<run>`, `repair-<run>`."""
    return f"{kind}-{run_id}"


def gone(job: str, *, slots_api: Any | None = None) -> bool:
    """Whether the job a run records (`slots:<name>`, `proc:<pid>@<start>`) is gone. A job
    that can't be asked about reads as alive, so a run is never ended on a read that
    failed."""
    kind, _, name = job.partition(":")
    if kind == "slots":
        api = slots_api if slots_api is not None else slots.api()
        if api is None:
            return False
        try:
            return api.job(name) is None
        except Exception:  # noqa: BLE001  (an unanswered read is not a gone job)
            return False
    if kind == "proc":
        from moxi import proc

        parsed = store.parse_endpoint(name)
        if parsed is None:
            return False
        pid, started = parsed
        if started is None:
            return proc.identity(pid) is None
        return not proc.alive(proc.ProcId(pid, started))
    return False


def repairable(run: store.Run, events: Sequence[store.RunEvent]) -> bool:
    """Whether a run ended having exited its session without relaunching it: ended
    `relaunch-failed` or `gone`, or `error` after it acted on the session (or in a
    repair, whose session was already down)."""
    if run.kind != "relaunch" or run.open or run.outcome not in REPAIRABLE:
        return False
    if run.outcome != "error" or run.mode == "repair":
        return True
    return any(event.kind in ACTED for event in events)


def root_run(conn: sqlite3.Connection, run: store.Run) -> store.Run:
    """The run a repair run repairs at the root: through `repair_of` to the first run
    that is not a repair."""
    seen = {run.run_id}
    while run.mode == "repair" and run.repair_of and run.repair_of not in seen:
        seen.add(run.repair_of)
        found = store.get_run(conn, run.repair_of)
        if found is None:
            break
        run = found
    return run


def chain_of(conn: sqlite3.Connection, request_id: str) -> str:
    """The first request id of a launch's chain of relaunches."""
    seen = {request_id}
    first = request_id
    while True:
        row = store.get(conn, first)
        if row is None or not row.relaunch_of or row.relaunch_of in seen:
            return first
        seen.add(row.relaunch_of)
        first = row.relaunch_of


class Choice:
    """Where a repair launches: `slot` and whether it `respawn`s there (the recorded
    slot), or `slot` "" for the lowest launchable one; `chain` set when the recorded slot
    holds the chain's own session, which refuses the repair."""

    def __init__(self, slot: str, respawn: bool = False, held: bool = False,
                 why: str = "", chain: bool = False):
        self.slot, self.respawn, self.held, self.why, self.chain = (slot, respawn, held,
                                                                    why, chain)

    @property
    def recorded(self) -> bool:
        return bool(self.slot)


def choose_slot(api: Any, slot: str | None, holder: str, chain: str) -> Choice:
    """Where a repair of a run holding `holder` in the launch chain `chain` launches,
    from slots' view of the recorded `slot` now. Raises slots' errors when it can't be
    read."""
    if not slot:
        return Choice("", why="the run recorded no slot")
    found = api.show(slot)
    if found is None or found.created is None or found.state == "missing":
        return Choice("", why=f"slot {slot} is missing")
    hold = found.hold
    ours = hold is not None and hold.holder == holder
    if found.state == "occupied":
        meta = getattr(found.last, "meta", None) if found.last is not None else None
        if isinstance(meta, Mapping) and meta.get("chain") == chain:
            return Choice(str(slot), chain=True,
                          why=f"slot {slot} runs a session of this launch chain "
                              f"(launch {meta.get('request_id')})")
        return Choice("", why=f"slot {slot} is occupied by another launch")
    if hold is not None and not ours:
        return Choice("", why=f"slot {slot} is held by {hold.holder}")
    if found.state in ("free", "ended"):
        return Choice(str(slot), respawn=found.state == "ended", held=ours)
    return Choice("", why=f"slot {slot} is {found.state}")


# -- the runs ---------------------------------------------------------------------------


class RelaunchJob:
    """One relaunch run, from its record to its end. `run()` takes the steps and returns
    the outcome. The seams stand in for turns, slots, comms, the launch, the settings
    and the clocks; each left as is is looked up as the launcher reaches it."""

    #: The run's kind of job, for its name.
    kind = "relaunch"

    def __init__(self, conn: sqlite3.Connection, run_id: str, *, turns: Any = LOOK,
                 slots_api: Any = LOOK, comms: Any = LOOK,
                 launch: Callable[..., Any] | None = None,
                 settings: Mapping[str, Any] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.conn, self.run_id = conn, run_id
        self._turns, self._slots, self._comms = turns, slots_api, comms
        self._launch = launch
        self._settings = settings
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.run: store.Run | None = None
        self.row: store.LaunchRow | None = None
        self.target: Any | None = None
        self.watch: quiet.Watch | None = None
        self.holder = run_id
        self.held: str | None = None
        self.acting = False
        self.stop_asked: str | None = None
        self._stop_noted = False
        self.outcome: str | None = None
        self.ended_here = False

    # -- seams ---------------------------------------------------------------------------

    @property
    def settings(self) -> Mapping[str, Any]:
        if self._settings is None:
            from . import settings

            self._settings = settings.current()
        return self._settings

    def setting(self, key: str) -> Any:
        return self.settings[key]

    @property
    def slots_api(self) -> Any:
        if self._slots is LOOK:
            self._slots = slots.api()
        return self._slots

    @property
    def turns(self) -> Any:
        if self._turns is LOOK:
            from . import first

            self._turns = first.turns_api(quiet=True)
        return self._turns

    @property
    def comms(self) -> Any:
        if self._comms is LOOK:
            from . import presets

            self._comms = presets.comms_api()
        return self._comms

    def launch(self, request: Any) -> Any:
        if self._launch is None:
            from . import api

            return api.launch(request)
        return self._launch(request)

    # -- the run -------------------------------------------------------------------------

    def ask_stop(self, signum: int, _frame: Any = None) -> None:
        """A stop signal's handler: noted here, acted on at the job's next step."""
        try:
            self.stop_asked = signal.Signals(signum).name
        except ValueError:
            self.stop_asked = str(signum)

    def go(self) -> str:
        """Take the run's steps; return its outcome (`no-run` when there is no such run,
        and the stored outcome of a run already ended)."""
        self.run = self._first_read()
        if self.run is None:
            return "no-run"
        if not self.run.open:
            return self.run.outcome or "ended"
        self._prune()
        self.row = store.get(self.conn, self.run.request_id)
        try:
            if self.row is None:
                self.end("error", "no-launch",
                         f"the launch log holds no launch {self.run.request_id}")
            self.steps()
        except _Ended:
            pass
        except Exception as error:  # noqa: BLE001  (a run never stays open on a fault)
            log.exception("run %s stopped on a fault", self.run_id)
            try:
                self.end("error", type(error).__name__, _words(error))
            except _Ended:
                pass
        finally:
            if self.watch is not None:
                self.watch.close()
                self.watch = None
            self._release()
        try:
            if self.ended_here:
                self._tell()
        finally:
            if self.target is not None:
                self.target.close()
        return self.outcome or "ended"

    def steps(self) -> None:
        run = self.run
        assert run is not None
        self.target = self._find()
        if run.mode == "self" and run.state == "armed":
            if not store.advance_run(self.conn, run.run_id, "waiting", leaving=("armed",)):
                self._ended_under()
        while True:
            number = self.wait() if run.mode == "self" else run.turn_number
            try:
                self.hold()
                self.check_stop()
                self.guard()
                self.acting = True
                self.exit(number)
                break
            except _Again as again:
                self.acting = False
                self._release()
                self.event("again", {"word": again.word, "detail": again.detail})
        self.dialog(number)
        self.name()
        self.respawn(self.request())

    # -- writing ----------------------------------------------------------------------------

    def event(self, kind: str, detail: Mapping[str, Any] | None = None) -> None:
        store.add_run_event(self.conn, self.run_id, kind, detail)

    def end(self, outcome: str, word: str | None = None, detail: str | None = None,
            **fields: Any) -> None:
        """End the run with `outcome`, release its hold and stop the steps."""
        self.ended_here = store.end_run(self.conn, self.run_id, outcome, word=word,
                                        detail=detail, **fields)
        if self.ended_here:
            self.event("ended", {"outcome": outcome, "word": word, "detail": detail,
                                 **fields})
            self.outcome = outcome
        else:
            self._ended_under()
        raise _Ended()

    def guard(self) -> None:
        """The check right before an act: the run moves forward to `acting`, or was ended
        under the job, which then stops without acting."""
        if not store.advance_run(self.conn, self.run_id, "acting",
                                 leaving=("armed", "waiting", "acting")):
            self._ended_under()

    def _ended_under(self) -> None:
        now = store.get_run(self.conn, self.run_id)
        self.outcome = now.outcome if now is not None else "no-run"
        self.ended_here = False
        log.info("run %s was ended under its job (%s)", self.run_id, self.outcome)
        raise _Ended()

    def check_stop(self) -> None:
        """Before the exit, a stop ends the run `stopped`; from the exit on it is
        recorded once and deferred until the respawn was tried."""
        if self.stop_asked is None:
            return
        if not self.acting:
            self.end("stopped", "stopped", f"the job was sent {self.stop_asked}")
        if not self._stop_noted:
            self._stop_noted = True
            self.event("stop-deferred", {"signal": self.stop_asked})

    # -- the steps ------------------------------------------------------------------------

    def _find(self) -> Any:
        from . import catalog, target

        run = self.run
        try:
            found = target.find(run.session_id, conn=self.conn, turns=self.turns,
                                slots_api=self.slots_api)
        except catalog.Refused as refused:
            self.end("error", refused.code, refused.problem)
        except Exception as error:  # noqa: BLE001  (a find that breaks ends the run)
            self.end("error", "turns-failed", _words(error))
        if not found.live:
            found.close()
            self.end("gone", GONE, f"session {run.session_id} is not live")
        return found

    @property
    def slot(self) -> str | None:
        return self.run.slot or (self.row.slot if self.row else None)

    @property
    def pane_id(self) -> str | None:
        return self.run.pane_id or (self.row.pane_id if self.row else None)

    def wait(self, wait_s: float | None = None, expired: str = "expired") -> int | None:
        """Step 1: wait for the session to be quiet; return the turn number the last
        read saw. Past `wait_s` after the run's insert (`relaunch.wait_s` when None) the
        run ends with the outcome `expired`, worded by what still held."""
        session = self.target.session
        person_s = float(self.setting("relaunch.person_s"))
        if wait_s is None:
            wait_s = float(self.setting("relaunch.wait_s"))
        bound = store.parse_time(self.run.created_at).timestamp() + wait_s
        if self.watch is None:
            try:
                self.watch = quiet.Watch(session, clock=self.wall)
            except Exception as error:  # noqa: BLE001  (a watch that can't start ends it)
                self.end("error", "turns-failed",
                         f"the session's watch could not start: {_words(error)}")
        watch = self.watch
        read = dict(slot=self.slot, slots_api=self.slots_api, person_s=person_s)
        seen = watch.events
        reading = watch.read(**read)
        said: str | None = None
        while True:
            if watch.gone is not None:
                self.end("gone", GONE, f"the session ended while the run waited: "
                                       f"{watch.gone}")
            self.check_stop()
            first = reading.first
            if first is not None and first.word != said:
                said = first.word
                self.event("wait", {"word": first.word, "detail": first.detail})
            if first is None:
                return reading.turn_number
            left = bound - self.wall()
            if left <= 0:
                self.end(expired, first.word, first.detail)
            now = watch.wait(seen, min(POLL_S, left))
            if now > seen or watch.gone is not None:
                seen = now
                reading = watch.read(**read)
            elif set(reading.words) <= {"person"}:
                reading = watch.reread_person(reading, **read)
            else:
                reading = watch.read(**read)

    def hold(self) -> None:
        """Step 2: hold the slot for the run."""
        api, slot = self.slots_api, self.slot
        if api is None or not slot:
            self.end("not-held", "no-slot", "the run has no slot to hold, or slots can't "
                                            "be loaded")
        seconds = float(self.setting("relaunch.wait_s"))
        try:
            api.hold(slot, holder=self.holder, seconds=seconds)
        except api.TryAgain as error:
            self._transient("try-again", f"slots did not answer: {error}")
        except api.Refused as refused:
            self.end("not-held", refused.reason, refused.words or str(refused))
        except api.SlotsError as error:
            self.end("not-held", type(error).__name__, _words(error))
        self.held = str(slot)
        self.event("hold", {"slot": str(slot), "holder": self.holder, "seconds": seconds})

    def _transient(self, word: str, detail: str) -> None:
        if self.run.mode == "self":
            raise _Again(word, detail)
        self.end("held", word, detail)

    def exit(self, number: int | None) -> tuple[str, str | None]:
        """Step 3: exit the session through turns. Returns the result's kind and reason
        when the steps go on to the pane (`ran`, a `failed`, turns' `gone` or its error)."""
        self.event("exit", {"turn_number": number})
        try:
            result = self.target.session.command("exit", if_turn_number=number)
        except Exception as error:  # noqa: BLE001  (turns' error reads as gone)
            self.event("exit-result", {"kind": "error", "reason": GONE,
                                       "why": _words(error)})
            return "error", GONE
        kind = getattr(result, "KIND", type(result).__name__.lower())
        reason = getattr(result, "reason", None)
        self.event("exit-result", {"kind": kind, "reason": reason})
        if kind != "refused" or reason == GONE:
            return kind, reason
        if reason in TRANSIENT:
            self._transient(reason, f"turns refused the exit: {reason}")
        self.end("not-exited", reason, f"turns refused the exit: {reason}")

    def dialog(self, number: int | None) -> None:
        """Step 4: the exit dialog, answered after its last checks, or a pane that
        ended."""
        api, slot, pane = self.slots_api, self.slot, self.pane_id
        screen = api.CLAUDE_EXIT_DIALOG
        deadline = self.clock() + float(self.setting("relaunch.dialog_s"))
        full_at: float | None = None
        while True:
            self.check_stop()
            if pane:
                try:
                    ended = api.wait_ended(slot, pane_id=pane, timeout=1)
                except api.TryAgain:
                    ended = None
                except api.SlotsError as error:
                    self.end("relaunch-failed", "slots", f"the pane could not be watched: "
                                                         f"{_words(error)}")
                if ended is not None and (ended.ended or ended.gone):
                    self.event("pane-ended", {"pane_id": pane, "status": ended.dead_status,
                                              "gone": ended.gone})
                    return
            try:
                seen = api.read_screen(slot, [screen])
            except api.TryAgain:
                seen = None
            except api.SlotsError as error:
                self.end("relaunch-failed", "slots", f"the pane could not be read: "
                                                     f"{_words(error)}")
            if seen is not None and seen.dead:
                self.event("pane-ended", {"pane_id": pane})
                return
            matched = (seen is not None and seen.full
                       and getattr(seen.screen, "name", None) == screen.name)
            now = self.clock()
            if not matched:
                full_at = None
            elif full_at is None:
                full_at = now
            elif now - full_at >= SETTLE_S:
                self.answer(number)
                return
            if now >= deadline:
                self.end("not-exited", "no-dialog",
                         f"no exit dialog, and no end of the pane, within "
                         f"{self.setting('relaunch.dialog_s')} s")
            if pane is None:
                self.sleep(SETTLE_S)

    def answer(self, number: int | None) -> None:
        """The dialog's last checks, then its answer at once."""
        api, slot = self.slots_api, self.slot
        screen = api.CLAUDE_EXIT_DIALOG
        self.guard()
        turns_gone = False
        try:
            facts = self.target.session.facts()
        except Exception:  # noqa: BLE001  (turns gone at the dialog: the pane check alone)
            turns_gone = True
        else:
            if facts.turn_open or (number is not None and facts.turn_number != number):
                self.end("exit-pending", "turn",
                         f"turn {facts.turn_number} is {'open' if facts.turn_open else 'new'}"
                         " at the exit dialog; it was left for a person")
        try:
            pane = api.pane_use(slot)
        except api.SlotsError as error:
            self.end("exit-pending", "unreadable", f"the pane could not be read at the "
                                                   f"exit dialog: {_words(error)}")
        person = quiet.judge(None, None, pane, now=self.wall(),
                             person_s=float(self.setting("relaunch.person_s"))).hold("person")
        if person is not None:
            self.end("exit-pending", "person", f"{person.detail}; the exit dialog was left "
                                               "for them")
        answer = api.answer_screen(slot, screen, wait=0)
        outcome, why = answer.outcome, answer.why or ""
        detail = {"outcome": outcome, "why": why, "capture": answer.capture}
        if turns_gone:
            detail["turns"] = "turns-gone"
        if outcome in ("mismatch", "not-ended"):
            detail["settle"] = "not-settled" if why.startswith(NOT_SETTLED) else "changed"
        self.event("answer", detail)
        if outcome in ("ended", "ended-without-screen"):
            return
        capture = f" (the screen is saved at {answer.capture})" if answer.capture else ""
        if outcome in ("gone", "no-pane"):
            self.end("relaunch-failed", outcome, f"{why or outcome}{capture}")
        self.end("not-exited", outcome, f"{why or outcome}{capture}")

    def name(self) -> None:
        """Step 5: wait for comms to let the old session's name go."""
        row, comms = self.row, self.comms
        if not row.identity or comms is None:
            return
        wait_s = float(self.setting("relaunch.name_wait_s"))
        deadline = self.clock() + wait_s
        while True:
            try:
                state = comms.identity_state(row.identity, scope=row.scope or None).state
            except Exception as error:  # noqa: BLE001  (an unreadable state is not held)
                state = f"unreadable: {_words(error)}"
            if state != "held":
                self.event("name", {"identity": row.identity, "state": state})
                return
            if self.clock() >= deadline:
                self.end("relaunch-failed", "name-held",
                         f"comms still holds {row.identity} {wait_s:g} s after the exit")
            self.sleep(NAME_POLL_S)

    def request(self) -> Any:
        """The replay a relaunch respawns: the row made again into its slot's ended
        pane, under the run's hold."""
        return self._replay(mode=self.run.mode, handoff=self.run.handoff_path,
                            actor=self.run.actor, slot=self.slot, respawn=True,
                            hold=self.holder)

    def _replay(self, *, mode: str, handoff: str | None, actor: str, slot: str | None,
                respawn: bool, hold: str | None) -> Any:
        from . import api
        from .compose import common

        handoff = handoff if mode == "self" else None
        posture = (common.RELAUNCHED.format(path=handoff) if handoff
                   else f"with:{actor}")
        return api.ReplayRequest(
            self.row, relaunch_of=self.row.request_id, request_id=str(uuid.uuid4()),
            actor=actor, posture=posture, handoff=handoff, run=self.run_id,
            slot=slot, hold=hold, respawn=respawn)

    def respawn(self, request: Any) -> None:
        """Steps 6 to 8: launch the row again; a self run's handoff goes as its first
        turn."""
        from . import catalog

        self.guard()
        new_id = request.request_id
        self.event("respawn", {"request_id": new_id, "slot": request.slot,
                               "respawn": request.respawn, "holder": request.hold})
        try:
            result = self.launch(request)
        except catalog.Refused as refused:
            self.end("relaunch-failed", refused.code, refused.problem)
        except Exception as error:  # noqa: BLE001  (every failed launch is relaunch-failed)
            self.end("relaunch-failed", getattr(error, "code", None) or type(error).__name__,
                     _words(error))
        self.check_stop()
        if result.base == "job":
            self.event("base", {"base": "job"})
        sent = result.outcome
        if request.handoff and sent is not None and sent.outcome not in ("sent",
                                                                         "unconfirmed"):
            self.end("first-turn-not-sent", sent.outcome, result.line,
                     new_request_id=result.request_id)
        if result.state == "failed":
            self.end("relaunch-failed", result.reason, result.line,
                     new_request_id=result.request_id)
        self.held = None
        if request.handoff and sent is None:
            self.end("first-turn-not-sent", "not-sent", result.line,
                     new_request_id=result.request_id)
        self.end("relaunched", None, result.line, new_request_id=result.request_id)

    # -- the end ------------------------------------------------------------------------

    def _release(self) -> None:
        if self.held is None:
            return
        slot, self.held = self.held, None
        api = self.slots_api
        try:
            if api.release(slot, holder=self.holder):
                self.event("released", {"slot": slot, "holder": self.holder})
        except Exception as error:  # noqa: BLE001  (a hold that can't be released expires)
            log.warning("run %s could not release slot %s: %s", self.run_id, slot, error)

    def _tell(self) -> None:
        run = store.get_run(self.conn, self.run_id)
        outcome = self.outcome
        if run is None or outcome is None:
            return
        told = notices.settings_of(self.settings)
        if outcome in notices.NOTE_OUTCOMES:
            if run.mode != "self":
                return
            session = self.target.session if self.target is not None else None
            if session is not None and notices.note(
                    self.conn, run, session, wait_s=float(self.setting("relaunch.wait_s"))):
                return
            outcome = notices.NOTE_NOT_TAKEN
        elif outcome not in notices.NOTICE_OUTCOMES:
            return
        events = store.run_events(self.conn, self.run_id)
        notices.notify(self.conn, run, self.row, outcome, comms=self.comms,
                       repairable=repairable(run, events), clock=self.clock,
                       sleep=self.sleep, **told)

    def _first_read(self) -> store.Run | None:
        for attempt in range(FIRST_READS):
            run = store.get_run(self.conn, self.run_id)
            if run is not None:
                return run
            if attempt + 1 < FIRST_READS:
                self.sleep(FIRST_READ_S)
        return None

    def _prune(self) -> None:
        try:
            store.prune(self.conn, float(self.setting("records.keep_days")))
        except Exception as error:  # noqa: BLE001  (retention never stops a run)
            log.warning("the launcher's records were not pruned: %s", error)


class RepairJob(RelaunchJob):
    """One repair run: the original run's steps 5 to 7, from its record."""

    kind = "repair"

    def steps(self) -> None:
        run = self.run
        original = root_run(self.conn, run)
        self.original = original
        self.holder = original.run_id
        # The session is down already: a stop is deferred until the launch was tried.
        self.acting = True
        self.guard()
        api = self.slots_api
        if api is None:
            self.end("relaunch-failed", "no-slots", "slots can't be loaded")
        chain = chain_of(self.conn, self.row.request_id)
        try:
            choice = choose_slot(api, original.slot or self.row.slot, self.holder, chain)
        except Exception as error:  # noqa: BLE001  (a slot that can't be read: nothing done)
            self.end("relaunch-failed", "slots", f"slot {original.slot} could not be read: "
                                                 f"{_words(error)}")
        if choice.chain:
            self.end("occupied-by-chain", "occupied-by-chain", choice.why)
        if choice.recorded:
            try:
                api.hold(choice.slot, holder=self.holder,
                         seconds=float(self.setting("relaunch.wait_s")))
            except api.SlotsError as error:
                choice = Choice("", why=f"slot {choice.slot} could not be held: "
                                        f"{_words(error)}")
            else:
                self.held = choice.slot
        self.event("slot", {"slot": choice.slot or None, "respawn": choice.respawn,
                            "holder": self.holder, "why": choice.why or None})
        self.name()
        self.respawn(self._replay(
            mode=original.mode, handoff=original.handoff_path, actor=original.actor,
            slot=choice.slot, respawn=choice.respawn,
            hold=self.holder if choice.recorded else None))

    def check_stop(self) -> None:
        if self.stop_asked is not None and not self._stop_noted:
            self._stop_noted = True
            self.event("stop-deferred", {"signal": self.stop_asked})

    def _tell(self) -> None:
        if self.outcome in notices.NOTICE_OUTCOMES:
            super()._tell()


class CompactJob(RelaunchJob):
    """One compact at the context limit: the session's quiet read, waited for up to
    `recycle.limit_wait_s` after the crossing, then turns' `compact` with the turn number
    that read saw. Its end is `compacted`, `blocked` (still held at the bound, or turns'
    `draft` or `busy-agents` then), `failed` (turns' `not-allowed`, `unsupported`, `gone`
    or `failed`, or the session not live), or `unconfirmed` (turns' answer did not come).
    A refusal of turns' that may clear (`busy-turn`, `stale-turn`, `rate`, `in-flight`,
    `loading`, `draft`, `busy-agents`) sends it back to the wait. It passes no
    `stop_shells`: the session is at work, not idle an hour."""

    kind = "compact"
    #: Outcomes the relaunch's shared steps end with, as a compact's.
    OUTCOMES = {"gone": "failed"}

    def steps(self) -> None:
        from . import recycle
        from .exits import own_job

        run = self.run
        if run.job is None:
            store.set_run_job(self.conn, run.run_id, own_job())
        self.target = self._find()
        if run.state == "armed":
            if not store.advance_run(self.conn, run.run_id, "waiting", leaving=("armed",)):
                self._ended_under()
        wait_s = float(self.setting("recycle.limit_wait_s"))
        bound = store.parse_time(run.created_at).timestamp() + wait_s
        while True:
            number = self.wait(wait_s, expired="blocked")
            self.check_stop()
            self.guard()
            self.acting = True
            self.event("compact", {"turn_number": number})
            try:
                result = self.target.session.command("compact", if_turn_number=number)
            except Exception as error:  # noqa: BLE001  (turns' error is the verdict)
                result = error
            verdict = recycle.verdict_of("compact", result)
            self.event("result", {"outcome": verdict.outcome, "word": verdict.word,
                                  "detail": verdict.detail})
            self.acting = False
            if verdict.outcome not in (None, "blocked"):
                self.end(verdict.outcome, verdict.word, verdict.detail)
            if self.wall() >= bound:
                self.end("blocked", verdict.word, verdict.detail)
            self.event("again", {"word": verdict.word})
            self.sleep(RETRY_S)

    def end(self, outcome: str, word: str | None = None, detail: str | None = None,
            **fields: Any) -> None:
        mapped = self.OUTCOMES.get(outcome, outcome)
        if mapped != outcome and word is None:
            word = outcome
        super().end(mapped, word, detail, **fields)

    def _tell(self) -> None:
        """A compact tells no one: its run is its record."""


#: The job each run kind runs; `expiry` and `keep-warm` are asks of a session (`ASKS`).
JOBS: dict[str, type[RelaunchJob]] = {"relaunch": RelaunchJob, "repair": RepairJob,
                                      "compact": CompactJob}
#: The outcomes a job exits 0 with.
DONE = ("relaunched", "cleared", "compacted", "nudged")


def run(kind: str, run_id: str, *, signals: bool = False, **seams: Any) -> str:
    """Run one run of `kind` to its end, and return its outcome. With `signals`, a stop
    signal (SIGTERM, SIGINT, SIGHUP) is noted for the job to act on at its next step."""
    conn = store.connect(create=False)
    try:
        job = JOBS[kind](conn, run_id, **seams)
        if signals:
            for name in ("SIGTERM", "SIGINT", "SIGHUP"):
                if hasattr(signal, name):
                    signal.signal(getattr(signal, name), job.ask_stop)
        return job.go()
    finally:
        conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    """`moxi-launcher-job KIND ARG`: 0 when the run relaunched, cleared, compacted or
    nudged its session, or an ask ended with nothing to do; 1 for any other end; 2 for
    bad arguments."""
    parser = argparse.ArgumentParser(
        prog="moxi-launcher-job",
        description="Internal: run one of the launcher's detached runs to its end.")
    parser.add_argument("kind", choices=KINDS, help="the run's kind")
    parser.add_argument("arg", metavar="ARG",
                        help="the run's id; for expiry and keep-warm, the session's id")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    if args.kind in ASKS:
        from . import keepwarm, recycle

        asked = recycle.ask_expiry if args.kind == "expiry" else keepwarm.ask
        verdict = asked(args.arg)
        print(f"session {args.arg}: {verdict.line}", flush=True)
        return 0 if verdict.outcome is None or verdict.outcome in DONE else 1
    outcome = run(args.kind, args.arg, signals=True)
    print(f"run {args.arg}: {outcome}", flush=True)
    return 0 if outcome in DONE else 1


def _words(error: BaseException) -> str:
    return str(error) or type(error).__name__


__all__ = ["ACTED", "ASKS", "DONE", "GONE", "JOBS", "KINDS", "LOOK", "NOT_SETTLED", "POLL_S",
           "REPAIRABLE", "RETRY_S", "SETTLE_S", "TERMINAL", "TRANSIENT", "Choice",
           "CompactJob", "Ended", "RelaunchJob", "RepairJob",
           "chain_of", "choose_slot", "gone", "job_name", "main", "repairable", "root_run",
           "run"]

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
