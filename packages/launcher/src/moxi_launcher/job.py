"""`moxi-launcher-job`: the launcher's detached runs, one process per run.

    moxi-launcher-job relaunch RUN        # a relaunch run, self or director
    moxi-launcher-job repair RUN          # a repair run, from a failed run's record
    moxi-launcher-job expiry SESSION      # an expiry ask, started by cache's timer
    moxi-launcher-job keep-warm SESSION   # a keep-warm ask, started by cache's timer
    moxi-launcher-job compact RUN         # a compact at the context limit
    moxi-launcher-job launch REQUEST      # a launch the page asked for, by its request id

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
   While a turn is open nothing is read between events: its end is one. Any other hold
   (a draft, subagents, a read that failed) is read whole every 2 s, since its end may
   bring no event.
   Each change of the first word that holds is one `wait` event. Nothing holding goes on;
   `relaunch.wait_s` after the run was armed it ends `expired`. Within 60 s of a refusal
   of turns' pace (`rate`, `in-flight`) a quiet session holds as `rate`, so the exit is
   asked at most once a minute while the pace holds. A session whose current
   id is no longer the run's (a `/clear` since: the conversation that armed the run is
   gone) ends the run `changed`, untouched. Then, for every run, the
   model the relaunch comes back on (`pin.choose`), read on each pass, a `model` event
   when it changed.
2. Hold the slot for the run (`slots.hold`, holder the run id) for 510 s. A hold left by
   a dead run of the same launch (its job gone) is released under that run's id and the
   hold taken again, once. Refused: `not-held`.
   A director's run then reads the session's cache again: warm or fresh ends it `held`,
   word `warm`. Then the launch check (`launch_check`): the relaunch's own launch, on its
   pin, would be accepted now, the session's own name and slot not counted against it;
   one that would be refused ends the run `refused`, word the refusal's code, with the
   session untouched.
3. Exit, through turns, with the turn number the last quiet read saw. turns checks the
   launch's consent, idleness and the number again. `Ran`, any `Failed` and turns' `gone`
   go on to the pane; a refusal that clears by itself (`busy-turn`, `draft`,
   `busy-agents`, `stale-turn`, `rate`, `in-flight`, `loading`) sends a self run back to
   step 1 with the hold released, after a 2 s pause, and ends a director's run `held`;
   `not-allowed` and `unsupported` end it `not-exited`.
4. The exit dialog. Within `relaunch.dialog_s`: a pane that ended goes on; once the dialog
   has fully matched on two reads at least 0.5 s apart, the last checks (turns' facts show
   the same turn number and no open turn; the pane shows no person) and then at once the
   answer, with no other read between. The pane check is the quiet read's person signs:
   copy mode, or a client active within `relaunch.person_s`; the pane's own output (the
   dialog drawing itself) never holds. A failed check presses nothing: `exit-pending`. A
   turns that answers `gone` at the dialog leaves the pane check alone, recorded
   `turns-gone`. The answer: `ended` goes on, `mismatch` or `not-ended` end the run
   `not-exited`, `gone` or `no-pane` end it `relaunch-failed`. No dialog and no end of the
   pane in time: `not-exited`.
5. The name. With comms, up to `relaunch.name_wait_s` for the old session's name to be let
   go; still held: `relaunch-failed`.
6. Respawn: the launch row made again (`api.ReplayRequest`), into the same slot's ended
   pane under the run's hold, which the respawn ends. A refusal or failure:
   `relaunch-failed`. The launch reads the new session at its startup screens and waits
   for ready, a director's relaunch too, as any slot launch does; what it finds there (a
   screen it won't answer, no ready in time) flags the relaunch, the launch's `finding`,
   and never fails it.
7. A self run's new session gets its handoff as its first turn; not sent:
   `first-turn-not-sent`. A flagged one gets none: the run ends `first-turn-not-sent` at
   once, with the finding as its word and the launch's line, which names the screen and
   the slot.
8. `relaunched`, with the new request id on the run. A director's relaunch the launch
   flagged ends `relaunched` with the finding as its word, and tells the directors. The
   `ended` event of a run that launched records what came back: the new session id, the
   identity, the posture, the model and how it was chosen, and comms' state of the
   identity, read every 2 s for up to 60 s until the new session has bound it (`held`)
   or a read fails (`unreadable: <why>`, comms offline included).

**A moved endpoint.** Claude Code replaces a session's turns endpoint when its plugins
reload, while its `claude` process and session id stay; it applies a pending reload when a
turn ends, the moment a self run's wait releases. A Claude launch's run that meets its
endpoint gone (turns' `gone` or its error at the exit, the watch's end in the wait, or the
endpoint process ended before the closing note) while the launch's `claude` still runs
(`store.is_launch_parent`'s test of the row's `pid` and `pid_started`) looks the session
up again (`follow_move`): up to `listen_ready.OVERLAP_WAIT_S`, every `MOVE_STEP_S`, until a
live endpoint other than the old one passes the claim check, rechecking each time that
`claude` runs. A move writes a `moved` event with both endpoints, and a run moves at most
`MOVES` times. At the exit the run goes back to step 1 on the new endpoint, a self run
waiting again for a fresh turn number and a director's run reading it from the new
endpoint (its count restarts), with the hold, warm and launch checks taken again; the wait
waits on with a watch of the new endpoint; the note goes to it. With `claude` ended, no
new endpoint by the bound, the moves spent or another harness, the run goes on as it
always did: to the pane from the exit, `gone` from the wait, the old endpoint for the note.
The look-up writes no claim.

**A stop signal before step 3 ends the run `stopped`; from step 3 on it is recorded and
deferred until step 6 has been tried.** **A job acts only while its run is open**: right
before each act on the session (the exit, the dialog's answer, the respawn) it moves its
run forward with one conditional update, and one that matches nothing means the run was
ended under it, and it stops without acting. **Every terminal path releases the run's
slot hold**; only a respawn that succeeds ends it another way.

**The repair run** repeats steps 5-7 from a run's record, for a run that exited its
session and did not relaunch it, or left its exit dialog for a person who then answered
it (`exit-pending`). It acts under the original run's id as the slot's
holder, so a hold the original left behind reads as its own. The recorded slot is used
when it is free or ended, or held by the original run; it is refused `occupied-by-chain`
when it is occupied by the launch chain's own session (the original died after its
respawn); otherwise (held or occupied by anyone outside the chain, or missing) the launch
goes to the lowest launchable slot. Slots that don't answer about the recorded slot's hold
are asked again a few times, then the repair ends `relaunch-failed` (`slots-busy`) with
nothing launched, so it can be repeated. Its launch is checked before it is made, as
step 2's is: one that would be refused ends the repair `refused`, nothing launched. Its
posture, first turn and model pin are the
original run's: the pin from the original's last `model` event, or read from the old
session's transcript when it wrote none.

Each run ends with its outcome and tells whom `notices` says. As it starts, each job
deletes ended runs older than `records.keep_days`.

**The page's launch** is no run: its record is its launch row. The launcher's page
server writes what the page asked for as a request file,
`STATE/runs/<request id>.request.json` (0600, made only if it is not there, so a ticket
starts one job), and spawns `moxi-launcher-job launch <request id>` detached. The file
names its act, `launch` or `resume`. For `launch` the job runs `api.launch` with the
request id as its idempotency key, bounded by `page.launch_wait_s`; a launch that names
no directory, project or scratch is refused `no-cwd` (it would start in the job's own
directory), and one whose target is `terminal` is refused, since there is no terminal to
run it in. A refusal before the launch row is written is recorded as a `launch-refused`
event under the request id, with its `code`, `row`, `problem` and `hint`, which the page
reads back; after the row is written, the row says what happened. For `resume` the job
runs `resume.resume` for the session the file names, with the brief or brief file it
names and its `plain`, and with the request id as the new
launch's, so a retry never launches twice, bounded the same way; it runs only a resume into
a slot, and refuses one whose launch turns out to be in a terminal, since that would take
the job's place. Its refusals before the row are recorded as a launch's are. The job
deletes the request file when it ends, whatever the end; the run-file sweep takes one a
job left behind.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from moxi import files

from . import catalog, notices, parks, quiet, slots, store, targets

log = logging.getLogger("moxi.launcher.job")

#: The runs this script runs.
KINDS = ("relaunch", "repair", "expiry", "compact", "keep-warm", "launch")
#: The kind that runs a page's request file, by its request id.
PAGE = "launch"
#: The acts a page's request file names.
ACTS = ("launch", "resume")
#: The launch states a page launch's job exits 0 with: the session started.
LAUNCHED = ("started", "ready", "sending", "first-sent")
#: The kinds that are an ask of a session, by its id.
ASKS = ("expiry", "keep-warm")
#: Refusals of the exit that clear by themselves.
TRANSIENT = ("busy-turn", "draft", "busy-agents", "stale-turn", "rate", "in-flight",
             "loading")
#: Refusals of the exit that end the run `not-exited`.
TERMINAL = ("not-allowed", "unsupported")
#: turns' word for an endpoint that is gone or could not be reached.
GONE = "gone"
#: How often the wait re-reads the person's signs between the watch's events, seconds;
#: a self run also waits this long after a refusal before it reads the session again.
POLL_S = 2.0
#: The exit refusals of turns' pace: it takes one command a minute, and refuses another
#: while one is in flight.
PACED = ("rate", "in-flight")
#: How long a refusal of turns' pace keeps a self run out of the exit, counted from the
#: refusal, in seconds: turns' own 60 s between accepted commands.
PACE_HOLD_OFF_S = 60.0
#: How long a run holds its slot, in seconds: the hold is taken after the wait, so it
#: spans the launch check, the exit, the dialog, the name and the respawn.
HOLD_S = 510.0
#: How far apart two full matches of the exit dialog must be before it is answered.
SETTLE_S = 0.5
#: The word slots' answer leads its words with when its settle bound ran out.
NOT_SETTLED = "not-settled"
#: How many times a repair asks slots for its recorded slot's hold when slots don't
#: answer, and how long it waits between, in seconds.
HOLD_TRIES, HOLD_RETRY_S = 5, 1.0
#: How often the name wait asks comms again, in seconds.
NAME_POLL_S = 0.5
#: How long a relaunch waits for its new session to bind the comms name, and how often it
#: asks, in seconds.
BIND_WAIT_S, BIND_POLL_S = 60.0, 2.0
#: How long a compact waits after a refusal of turns' before it reads the session again.
RETRY_S = 5.0
#: How often, and how many times, a job tries its run's first read.
FIRST_READS, FIRST_READ_S = 5, 0.2
#: The outcomes after which a run's session is down and its record can launch it again.
REPAIRABLE = ("relaunch-failed", "gone", "exit-pending", "error")
#: The events that mark a run as having acted on its session (the exit, or later).
ACTED = ("exit", "respawn")
#: The most times a run follows its session to a new endpoint (`follow_move`).
MOVES = 3
#: How often a run looks a moved session up again, in seconds; a new endpoint takes the
#: launch about 0.25 s after the old one ends.
MOVE_STEP_S = 0.25
#: How far apart two reads of one process's start time may be, in seconds.
SAME_START_S = 1.0

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


#: Raised to send a run back to its wait; a run that takes the steps in its own order
#: catches it.
Again = _Again


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
    """Whether a run ended having exited its session without relaunching it, or may have:
    ended `relaunch-failed` or `gone`, `exit-pending` (its exit dialog left for a person,
    who may answer it), or `error` after it acted on the session (or in a repair, whose
    session was already down). A repair still refuses a session that is live."""
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

    def __init__(self, slot: str, *, respawn: bool = False, held: bool = False,
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


def replay_request(row: store.LaunchRow, run_id: str, *, mode: str, handoff: str | None,
                   by: str, slot: str | None, hold: str | None, respawn: bool,
                   director: bool = False, model_pin: str | None = None) -> Any:
    """The replay a relaunch or repair run launches: `row` made again as its relaunch,
    with a self run's handoff as its first turn (a director's run carries none and the
    posture `with:<director>`), into `slot` under `hold`, on `model_pin`."""
    from . import api
    from .compose import common

    handoff = handoff if mode == "self" else None
    posture = (common.RELAUNCHED.format(path=handoff) if handoff
               else f"with:{by}")
    return api.ReplayRequest(
        row, relaunch_of=row.request_id, request_id=str(uuid.uuid4()),
        actor=by, posture=posture, handoff=handoff, run=run_id,
        slot=slot, hold=hold, respawn=respawn, director=director, model_pin=model_pin)


def recorded_pin(conn: sqlite3.Connection, original: store.Run, row: store.LaunchRow, *,
                 sessions: Any = LOOK) -> Any:
    """A repair's model pin: the root run's, from its last `model` event; read from the
    old session's transcript, which outlives it, when the root run wrote none."""
    from . import pin

    recorded = [event for event in store.run_events(conn, original.run_id)
                if event.kind == "model"]
    if recorded:
        return pin.Pin.of(recorded[-1].detail)
    return pin.choose(row, original.session_id,
                      sessions=pin.LOOK if sessions is LOOK else sessions)


class LaunchCheckFailed(catalog.Refused):
    """A launch check that broke on something other than a refusal."""

    code = "launch-check"


def launch_check(request: Any, env: Mapping[str, str] | None = None) -> catalog.Refused | None:
    """Whether the launch `request` makes would be accepted now: `api.check`, then
    `api.preview`, then its target's `prepare` (the harness command found on the child's
    `PATH`). None when it would; else the first refusal. Starts and writes nothing. A
    request whose `replacing` names the session it takes over is not refused for that
    session's own comms name or slot."""
    from . import api

    try:
        refusals = api.check(request, env=env)
        if refusals:
            return refusals[0]
        composition = api.preview(request, env=env)
        api.prepare(request, composition, env=env)
    except catalog.Refused as refused_exc:
        return refused_exc
    except Exception as exc:  # noqa: BLE001  (a check that breaks refuses the launch)
        return LaunchCheckFailed(None, f"the launch could not be checked: {_words(exc)}",
                                 "check moxi doctor, then try again")
    return None


# -- the runs ---------------------------------------------------------------------------


class RelaunchJob:
    """One relaunch run, from its record to its end. `run()` takes the steps and returns
    the outcome. The seams stand in for turns, slots, comms, the launch, the settings
    and the clocks; each left as is is looked up as the launcher reaches it."""

    #: The run's kind of job, for its name.
    kind = "relaunch"
    #: Whether a client's activity holds at the exit dialog (copy mode always does).
    count_activity = True
    #: Whether the run follows its session to a new endpoint (`follow_move`).
    follows_moves = True

    def __init__(self, conn: sqlite3.Connection, run_id: str, *, turns: Any = LOOK,
                 slots_api: Any = LOOK, comms: Any = LOOK, sessions: Any = LOOK,
                 cache: Any = LOOK, launch: Callable[..., Any] | None = None,
                 check: Callable[[Any], Any] | None = None,
                 settings: Mapping[str, Any] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.conn, self.run_id = conn, run_id
        self._turns, self._slots, self._comms = turns, slots_api, comms
        self._sessions, self._cache = sessions, cache
        self._check = check
        self.pin: Any | None = None
        self.facts: Any | None = None
        #: The wall time before which a self run stays out of the exit (turns' pace).
        self.act_after: float | None = None
        self._launch = launch
        self._settings = settings
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.run: store.Run | None = None
        self.row: store.LaunchRow | None = None
        self.target: Any | None = None
        #: The endpoint process serving `target`, read when it was found.
        self.endpoint: Any | None = None
        #: How many times the run followed its session to a new endpoint.
        self.moves = 0
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

    @property
    def sessions(self) -> Any:
        if self._sessions is LOOK:
            from . import joiner

            self._sessions = joiner.sessions_api()
        return self._sessions

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
        except Exception as exc:  # noqa: BLE001  (a run never stays open on a fault)
            log.exception("run %s stopped on a fault", self.run_id)
            try:
                self.end("error", type(exc).__name__, _words(exc))
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
            number = self.wait() if run.mode == "self" else self.turn_number()
            self.end_if_changed()
            self.refresh_pin()
            try:
                self.hold()
                self.check_stop()
                if run.mode == "director":
                    self.end_if_warm()
                self.end_if_refused(self.request())
                self.guard()
                self.acting = True
                self.exit(number)
                break
            except _Again as exc:
                self.acting = False
                self._release()
                retry = {"word": exc.word, "detail": exc.detail}
                if exc.word in PACED:
                    self.act_after = self.wall() + PACE_HOLD_OFF_S
                    retry["act_after"] = _stamp(self.act_after)
                self.event("again", retry)
                self.sleep(POLL_S)
        self.dialog(number)
        self.name()
        self.respawn(self.request())

    # -- writing ----------------------------------------------------------------------------

    def event(self, kind: str, detail: Mapping[str, Any] | None = None) -> None:
        store.add_run_event(self.conn, self.run_id, kind, detail)

    def end(self, outcome: str, word: str | None = None, detail: str | None = None,
            *, noted: Mapping[str, Any] | None = None, **fields: Any) -> None:
        """End the run with `outcome`, release its hold and stop the steps. `noted` goes
        on the `ended` event only."""
        self.ended_here = store.end_run(self.conn, self.run_id, outcome, word=word,
                                        detail=detail, **fields)
        if self.ended_here:
            self.event("ended", {"outcome": outcome, "word": word, "detail": detail,
                                 **fields, **(noted or {})})
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
        except catalog.Refused as refused_exc:
            self.end("error", refused_exc.code, refused_exc.problem)
        except Exception as exc:  # noqa: BLE001  (a find that breaks ends the run)
            self.end("error", "turns-failed", _words(exc))
        if not found.live:
            found.close()
            self.end("gone", GONE, f"session {run.session_id} is not live")
        self.endpoint = _endpoint_of(found.session)
        return found

    def turn_number(self) -> int | None:
        """A director's run's turn number: the run's own, or after a move the new
        endpoint's, whose count restarts. A read that fails keeps the run's own, which
        turns then refuses `stale-turn`."""
        if self.moves:
            try:
                return self.target.session.facts().turn_number
            except Exception:  # noqa: BLE001  (an unread number keeps the run's own)
                pass
        return self.run.turn_number

    def claude_runs(self) -> bool:
        """Whether the run's launch is Claude's and its own `claude` process still runs:
        `store.is_launch_parent`'s test of the row's `pid` and `pid_started`."""
        from moxi import proc

        row = self.row
        if row is None or row.harness != "claude" or row.pid is None or row.pid_started is None:
            return False
        return store.is_launch_parent(row, proc.ProcId(row.pid, row.pid_started))

    def endpoint_gone(self) -> bool:
        """Whether the endpoint process that served the run's target when it was found
        has ended. False when that process could not be read."""
        from moxi import proc

        return self.endpoint is not None and not proc.alive(self.endpoint)

    def follow_move(self, why: str) -> bool:
        """Follow the session to a new endpoint, after `why` said the old one is gone:
        while the moves last (`MOVES`) and the launch's `claude` runs (`claude_runs`), look
        the session up again every `MOVE_STEP_S` for up to `listen_ready.OVERLAP_WAIT_S`.
        A find that is not live, refused `changed` with the reclaim pending, or `unclaimed`
        is tried again, and so is one of the old endpoint, which is no move. A live new
        endpoint becomes the run's target: the old target and the watch are closed, and a
        `moved` event names both endpoints. True when the run moved; False to go on as
        before the move was looked for. Writes no claim."""
        from . import listen_ready, target

        if not self.follows_moves or self.moves >= MOVES or not self.claude_runs():
            return False
        old, old_endpoint = self.target, self.endpoint
        deadline = self.clock() + listen_ready.OVERLAP_WAIT_S
        while True:
            try:
                found = target.find(self.run.session_id, conn=self.conn, turns=self.turns,
                                    slot=False)
            except target.Unclaimed:
                found = None
            except target.Changed as refused_exc:
                # The old endpoint, found once the claim has moved, is no move either.
                if not (refused_exc.reclaim_pending
                        or _serving_pid(refused_exc) == _pid_of(old)):
                    return False
                found = None
            except Exception:  # noqa: BLE001  (any other refusal or fault: as before)
                return False
            if found is not None:
                new_endpoint = _endpoint_of(found.session)
                if found.live and not _same(found, old, new_endpoint, old_endpoint):
                    break
                found.close()
            if self.clock() >= deadline or not self.claude_runs():
                return False
            self.sleep(MOVE_STEP_S)
        self.moves += 1
        if self.watch is not None:
            self.watch.close()
            self.watch = None
        if old is not None:
            old.close()
        self.target, self.endpoint = found, new_endpoint
        self.event("moved", {"from": _endpoint_text(old_endpoint, old),
                             "to": _endpoint_text(new_endpoint, found),
                             "why": why, "move": self.moves})
        return True

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
        person_s = float(self.setting("relaunch.person_s"))
        if wait_s is None:
            wait_s = float(self.setting("relaunch.wait_s"))
        bound = store.parse_time(self.run.created_at).timestamp() + wait_s
        watch = self._watch()
        read = dict(slot=self.slot, slots_api=self.slots_api, person_s=person_s)
        seen = watch.events
        reading = watch.read(**read)
        said: str | None = None
        while True:
            if watch.gone is not None:
                if not self.follow_move(f"the watch ended: {watch.gone}"):
                    self.end("gone", GONE, f"the session ended while the run waited: "
                                           f"{watch.gone}")
                # A new endpoint: its own watch, read whole.
                watch = self._watch()
                seen = watch.events
                reading = watch.read(**read)
                continue
            self.check_stop()
            first = reading.first
            holds = first or self.paced()
            if holds is not None and holds.word != said:
                said = holds.word
                self.event("wait", {"word": holds.word, "detail": holds.detail})
            if holds is None:
                self.facts = reading.facts
                return reading.turn_number
            left = bound - self.wall()
            if left <= 0:
                self.end(expired, holds.word, holds.detail)
            now = watch.wait(seen, min(POLL_S, left))
            if now > seen or watch.gone is not None or first is None:
                # A quiet session held by turns' pace alone is read whole again too.
                seen = max(seen, now)
                reading = watch.read(**read)
            elif first.word == "turn":
                # An open turn ends with a watch event; nothing to read on the clock.
                continue
            elif set(reading.words) <= {"person"}:
                reading = watch.reread_person(reading, **read)
            else:
                # A draft cleared, a subagent done or a read that failed again may come
                # with no event: the whole read again.
                reading = watch.read(**read)

    def _watch(self) -> quiet.Watch:
        """The watch of the run's session, opened when there is none."""
        if self.watch is None:
            try:
                self.watch = quiet.Watch(self.target.session, clock=self.wall)
            except Exception as exc:  # noqa: BLE001  (a watch that can't start ends it)
                self.end("error", "turns-failed",
                         f"the session's watch could not start: {_words(exc)}")
        return self.watch

    def paced(self) -> quiet.Hold | None:
        """The hold `rate` while a refusal of turns' pace keeps the run out of the exit,
        else None."""
        if self.act_after is None or self.wall() >= self.act_after:
            return None
        return quiet.Hold("rate", f"turns' pace holds the exit until "
                                  f"{_stamp(self.act_after)}")

    def end_if_changed(self) -> None:
        """The run acts only on the conversation that armed it: a session whose current id
        is no longer the run's (a `/clear` since) ends the run `changed`, untouched. A self
        run reads the id its wait's last facts gave; a director's run reads the facts once.
        An id that can't be read is no change."""
        facts = self.facts
        if self.run.mode != "self":
            try:
                facts = self.target.session.facts()
            except Exception:  # noqa: BLE001  (an id not read is no change)
                facts = None
        found = getattr(facts, "session_id", None)
        if isinstance(found, str) and found and found != self.run.session_id:
            self.end("changed", "changed",
                     f"the session's current id is {found}, not {self.run.session_id}: "
                     "the conversation that armed the run is gone")

    def refresh_pin(self) -> None:
        """The model the relaunch comes back on, read again on each pass so a switch
        while the run waits is seen; a `model` event each time it changes."""
        from . import pin

        found = pin.choose(self.row, self.run.session_id, sessions=self.sessions)
        self.note_pin(found)

    def note_pin(self, found: Any) -> None:
        if found != self.pin:
            self.event("model", found.detail())
        self.pin = found

    def end_if_refused(self, request: Any) -> None:
        """The launch check before an act: a launch that would be refused ends the run
        `refused`, its word the refusal's code, with the session untouched."""
        import dataclasses

        check = self._check or launch_check
        refused = check(dataclasses.replace(request, replacing=self.replacing))
        if refused is not None:
            self.end("refused", refused.code, str(refused.problem))

    @property
    def replacing(self) -> str | None:
        """The session the replay takes over: the run's, still live before the exit."""
        return self.run.session_id

    def end_if_warm(self) -> None:
        """A director's run reads the session's cache again after its hold: one warm or
        fresh can take a message, and ends the run `held`, word `warm`."""
        from . import relaunch

        try:
            relaunch.not_warm(self.row, self.run.session_id,
                              relaunch.LOOK if self._cache is LOOK else self._cache)
        except relaunch.Refused as refused_exc:
            self.end("held", refused_exc.code, refused_exc.problem)

    def hold(self) -> None:
        """Step 2: hold the slot for the run, for `HOLD_S`. A hold left by a dead run of
        the same launch is released under its holder and the hold taken again, once."""
        api, slot = self.slots_api, self.slot
        if api is None or not slot:
            self.end("not-held", "no-slot", "the run has no slot to hold, or slots can't "
                                            "be loaded")
        seconds = HOLD_S
        released = False
        while True:
            try:
                api.hold(slot, holder=self.holder, seconds=seconds)
            except api.TryAgain as exc:
                self._transient("try-again", f"slots did not answer: {exc}")
            except api.Refused as refused_exc:
                dead = None if released else self.dead_holder(refused_exc)
                if dead is not None and self._release_dead(api, slot, dead):
                    released = True
                    continue
                self.end("not-held", refused_exc.reason,
                         refused_exc.words or str(refused_exc))
            except api.SlotsError as exc:
                self.end("not-held", type(exc).__name__, _words(exc))
            break
        self.held = str(slot)
        self.event("hold", {"slot": str(slot), "holder": self.holder, "seconds": seconds})

    def dead_holder(self, refused_exc: Any) -> str | None:
        """The holder a `held` refusal names, when it is another run of this run's launch
        whose job is gone (open and stale, or ended with its job gone); else None."""
        named = refused_exc.holder if getattr(refused_exc, "reason", None) == "held" else None
        holder = named.get("holder") if isinstance(named, Mapping) else None
        if not isinstance(holder, str) or not holder or holder == self.holder:
            return None
        other = store.get_run(self.conn, holder)
        if other is None or other.request_id != self.run.request_id:
            return None

        def is_gone(job_text: str) -> bool:
            return gone(job_text, slots_api=self.slots_api)

        if other.open:
            return holder if store.run_stale(other, is_gone) else None
        return holder if other.job is None or is_gone(other.job) else None

    def _release_dead(self, api: Any, slot: str, holder: str) -> bool:
        try:
            released = api.release(slot, holder=holder)
        except Exception as exc:  # noqa: BLE001  (a release that fails leaves the refusal)
            log.warning("run %s could not release slot %s's dead hold by %s: %s",
                        self.run_id, slot, holder, exc)
            return False
        self.event("released-dead", {"slot": str(slot), "holder": holder,
                                     "released": bool(released)})
        return True

    def _transient(self, word: str, detail: str) -> None:
        if self.run.mode == "self":
            raise _Again(word, detail)
        self.end("held", word, detail)

    def exit(self, number: int | None) -> tuple[str, str | None]:
        """Step 3: exit the session through turns. Returns the result's kind and reason
        when the steps go on to the pane (`ran`, a `failed`, turns' `gone` or its error).
        turns' `gone` or its error from an endpoint that moved (`follow_move`) sends the
        run back to its wait instead, `_Again` with the word `moved`."""
        self.event("exit", {"turn_number": number})
        try:
            result = self.target.session.command("exit", if_turn_number=number)
        except Exception as exc:  # noqa: BLE001  (turns' error reads as gone)
            self.event("exit-result", {"kind": "error", "reason": GONE,
                                       "why": _words(exc)})
            self._again_if_moved(f"turns' error at the exit: {_words(exc)}")
            return "error", GONE
        kind = getattr(result, "KIND", type(result).__name__.lower())
        reason = getattr(result, "reason", None)
        self.event("exit-result", {"kind": kind, "reason": reason})
        if kind == "refused" and reason == GONE:
            self._again_if_moved("turns refused the exit: gone")
        if kind != "refused" or reason == GONE:
            return kind, reason
        if reason in TRANSIENT:
            self._transient(reason, f"turns refused the exit: {reason}")
        self.end("not-exited", reason, f"turns refused the exit: {reason}")

    def _again_if_moved(self, why: str) -> None:
        if self.follow_move(why):
            raise _Again("moved", f"{why}; the session moved to a new endpoint")

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
                    ended = api.wait_ended(slot, pane_id=pane, timeout_s=1)
                except api.TryAgain:
                    ended = None
                except api.SlotsError as exc:
                    self.end("relaunch-failed", "slots", f"the pane could not be watched: "
                                                         f"{_words(exc)}")
                if ended is not None and (ended.ended or ended.gone):
                    self.event("pane-ended", {"pane_id": pane, "status": ended.dead_status,
                                              "gone": ended.gone})
                    return
            try:
                seen = api.read_screen(slot, [screen])
            except api.TryAgain:
                seen = None
            except api.SlotsError as exc:
                self.end("relaunch-failed", "slots", f"the pane could not be read: "
                                                     f"{_words(exc)}")
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
        except api.SlotsError as exc:
            self.end("exit-pending", "unreadable", f"the pane could not be read at the "
                                                   f"exit dialog: {_words(exc)}")
        person = quiet.judge(None, None, pane, now=self.wall(),
                             person_s=float(self.setting("relaunch.person_s")),
                             count_activity=self.count_activity).hold("person")
        if person is not None:
            self.end("exit-pending", "person", f"{person.detail}; the exit dialog was left "
                                               "for them")
        answer = api.answer_screen(slot, screen, wait_s=0)
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
            except Exception as exc:  # noqa: BLE001  (an unreadable state is not held)
                state = f"unreadable: {_words(exc)}"
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
                            by=self.run.actor, slot=self.slot, respawn=True,
                            hold=self.holder, director=self.run.mode == "director")

    def _replay(self, *, mode: str, handoff: str | None, by: str, slot: str | None,
                respawn: bool, hold: str | None, director: bool = False) -> Any:
        """The replay of the run's launch, on the run's model pin."""
        return replay_request(
            self.row, self.run_id, mode=mode, handoff=handoff, by=by, slot=slot, hold=hold,
            respawn=respawn, director=director,
            model_pin=self.pin.model if self.pin is not None and self.pin.pinned else None)

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
        except catalog.Refused as refused_exc:
            self.end("relaunch-failed", refused_exc.code, refused_exc.problem)
        except Exception as exc:  # noqa: BLE001  (every failed launch is relaunch-failed)
            self.end("relaunch-failed", getattr(exc, "code", None) or type(exc).__name__,
                     _words(exc))
        self.check_stop()
        if result.base in ("job", "no-credentials"):
            self.event("base", {"base": result.base})
        sent = result.outcome
        if request.handoff and sent is not None and sent.outcome not in ("sent",
                                                                         "unconfirmed"):
            self.end("first-turn-not-sent", sent.outcome, result.line,
                     new_request_id=result.request_id)
        if result.state == "failed":
            self.end("relaunch-failed", result.reason, result.line,
                     new_request_id=result.request_id)
        self.held = None
        came_back = self.came_back(result, request)
        finding = getattr(result, "finding", None)
        if finding is not None:
            self.event("flagged", {"code": finding, "request_id": result.request_id})
            if request.handoff:
                self.end("first-turn-not-sent", finding, result.line,
                         new_request_id=result.request_id, noted=came_back)
            self.end("relaunched", finding, result.line, new_request_id=result.request_id,
                     noted=came_back)
        if request.handoff and sent is None:
            self.end("first-turn-not-sent", "not-sent", result.line,
                     new_request_id=result.request_id, noted=came_back)
        self.end("relaunched", None, result.line, new_request_id=result.request_id,
                 noted=came_back)

    def came_back(self, result: Any, request: Any) -> dict[str, Any]:
        """What the relaunch came back as, for the `ended` event: the new session id, the
        identity, the posture, the model pin, and comms' state of the identity after the
        respawn (`wait_for_bind`; None without comms or a name)."""
        row = self.row
        state = self.wait_for_bind() if row.identity and self.comms is not None else None
        found = self.pin
        return {"session_id": getattr(result, "session_id", None),
                "identity": row.identity or None,
                "posture": getattr(request, "posture", None),
                "model": found.model if found is not None else None,
                "model_how": found.how if found is not None else None,
                "identity_state": state}

    def wait_for_bind(self) -> str:
        """comms' state of the row's identity once the new session has bound it (`held`),
        asked every `BIND_POLL_S` for up to `BIND_WAIT_S`: the last state read when the
        bound passes, or `unreadable: <why>` as soon as a read fails or comms is offline.
        "Not bound" and "could not be read" stay two answers."""
        row, comms = self.row, self.comms
        deadline = self.clock() + BIND_WAIT_S
        while True:
            try:
                read = comms.identity_state(row.identity, scope=row.scope or None)
            except Exception as exc:  # noqa: BLE001  (an unread state is said so)
                return f"unreadable: {_words(exc)}"
            state = read.state
            if state == "offline":
                return f"unreadable: {getattr(read, 'reason', None) or 'comms is offline'}"
            if state == "held" or self.clock() >= deadline:
                return state
            self.sleep(BIND_POLL_S)

    # -- the end ------------------------------------------------------------------------

    def _release(self) -> None:
        if self.held is None:
            return
        slot, self.held = self.held, None
        api = self.slots_api
        try:
            if api.release(slot, holder=self.holder):
                self.event("released", {"slot": slot, "holder": self.holder})
        except Exception as exc:  # noqa: BLE001  (a hold that can't be released expires)
            log.warning("run %s could not release slot %s: %s", self.run_id, slot, exc)

    def _tell(self) -> None:
        run = store.get_run(self.conn, self.run_id)
        outcome = self.outcome
        if run is None or outcome is None:
            return
        told = notices.settings_of(self.settings)
        events = store.run_events(self.conn, self.run_id)
        if notices.owes_note(run, outcome, events):
            if self.target is not None and self.endpoint_gone():
                self.follow_move("the endpoint ended before the note")
            session = self.target.session if self.target is not None else None
            if session is not None and notices.note(
                    self.conn, run, session, wait_s=float(self.setting("relaunch.wait_s"))):
                return
            outcome = notices.NOTE_NOT_TAKEN
        elif outcome not in notices.NOTICE_OUTCOMES and not notices.is_flagged(run):
            return
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
        except Exception as exc:  # noqa: BLE001  (retention never stops a run)
            log.warning("the launcher's records were not pruned: %s", exc)


def _endpoint_of(session: Any) -> Any | None:
    """The endpoint process serving turns' `session` (a `moxi.proc.ProcId`), or None
    when it names none or the process can't be read."""
    from moxi import proc

    pid = getattr(session, "endpoint_pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    return proc.identity(pid)


def _same(found: Any, old: Any | None, endpoint: Any | None,
          old_endpoint: Any | None) -> bool:
    """Whether the target `found` is served by the old target's endpoint: the same
    endpoint pid, and, where both processes were read, start times within
    `SAME_START_S`."""
    pid = getattr(found.session, "endpoint_pid", None)
    if pid is None or pid != _pid_of(old):
        return False
    if endpoint is None or old_endpoint is None:
        return True
    return abs(endpoint.started - old_endpoint.started) < SAME_START_S


def _pid_of(found: Any | None) -> int | None:
    """The endpoint pid of the target `found`'s session, or None."""
    return getattr(getattr(found, "session", None), "endpoint_pid", None)


def _serving_pid(refused_exc: Any) -> int | None:
    """The pid of the endpoint a `changed` refusal names as serving the session, or None."""
    parsed = store.parse_endpoint(refused_exc.serving) if refused_exc.serving else None
    return parsed[0] if parsed is not None else None


def _endpoint_text(endpoint: Any | None, found: Any | None) -> str | None:
    """An endpoint as a claim names it, `<pid>@<start time>`, or its pid alone."""
    if endpoint is not None:
        return f"{endpoint.pid}@{endpoint.started:.6f}"
    pid = _pid_of(found)
    return None if pid is None else str(pid)


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
        # Crash discovery before the recorded slot is chosen and held: a crashed slot of
        # another session is reserved for it, and so passed over; one of this chain's own
        # is settled, since this repair brings its session back.
        try:
            parks.discover_before_launch(by=original.actor, api=api, conn=self.conn,
                                         chain=chain)
        except parks.ParkRunning as exc:
            self.end("relaunch-failed", exc.code, f"{exc.problem}: {exc.hint}")
        try:
            choice = choose_slot(api, original.slot or self.row.slot, self.holder, chain)
        except Exception as exc:  # noqa: BLE001  (a slot that can't be read: nothing done)
            self.end("relaunch-failed", "slots", f"slot {original.slot} could not be read: "
                                                 f"{_words(exc)}")
        if choice.chain:
            self.end("occupied-by-chain", "occupied-by-chain", choice.why)
        if choice.recorded:
            choice = self._hold_recorded(api, choice)
        self.event("slot", {"slot": choice.slot or None, "respawn": choice.respawn,
                            "holder": self.holder, "why": choice.why or None})
        self.note_pin(self.recorded_pin(original))
        self.name()
        request = self._replay(
            mode=original.mode, handoff=original.handoff_path, by=original.actor,
            slot=choice.slot, respawn=choice.respawn,
            hold=self.holder if choice.recorded else None)
        self.end_if_refused(request)
        self.respawn(request)

    @property
    def replacing(self) -> str | None:
        """A repair's session is down already: it replaces none."""
        return None

    def recorded_pin(self, original: store.Run) -> Any:
        return recorded_pin(self.conn, original, self.row, sessions=self.sessions)

    def _hold_recorded(self, api: Any, choice: Choice) -> Choice:
        """Hold the recorded slot under the original run's id. Slots that don't answer
        are asked again a few times, then the run ends with nothing launched, so the
        repair can be repeated; a hold refused sends the launch to the lowest slot."""
        for attempt in range(HOLD_TRIES):
            try:
                api.hold(choice.slot, holder=self.holder, seconds=HOLD_S)
            except api.TryAgain as exc:
                if attempt + 1 == HOLD_TRIES:
                    self.end("relaunch-failed", "slots-busy",
                             f"slots did not answer about slot {choice.slot}: "
                             f"{_words(exc)}; nothing was launched")
                self.sleep(HOLD_RETRY_S)
            except api.SlotsError as exc:
                return Choice("", why=f"slot {choice.slot} could not be held: "
                                      f"{_words(exc)}")
            else:
                self.held = choice.slot
                return choice
        raise AssertionError("unreachable")

    def check_stop(self) -> None:
        if self.stop_asked is not None and not self._stop_noted:
            self._stop_noted = True
            self.event("stop-deferred", {"signal": self.stop_asked})

    def _tell(self) -> None:
        run = store.get_run(self.conn, self.run_id)
        if self.outcome in notices.NOTICE_OUTCOMES or (run is not None
                                                       and notices.is_flagged(run)):
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
            except Exception as exc:  # noqa: BLE001  (turns' error is the verdict)
                result = exc
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
            *, noted: Mapping[str, Any] | None = None, **fields: Any) -> None:
        mapped = self.OUTCOMES.get(outcome, outcome)
        if mapped != outcome and word is None:
            word = outcome
        super().end(mapped, word, detail, noted=noted, **fields)

    def _tell(self) -> None:
        """A compact tells no one: its run is its record."""


#: The job each run kind runs; `expiry` and `keep-warm` are asks of a session (`ASKS`).
JOBS: dict[str, type[RelaunchJob]] = {"relaunch": RelaunchJob, "repair": RepairJob,
                                      "compact": CompactJob}
#: The outcomes a job exits 0 with.
DONE = ("relaunched", "cleared", "compacted", "nudged")


# -- the page's launch ---------------------------------------------------------------------


class NoPlace(catalog.Refused):
    """A launch from the page that names no directory, project or scratch."""

    code = "no-cwd"


class NotHere(catalog.Refused):
    """A request the job can't run: a launch in a terminal, an act this version doesn't
    run, or a request file it can't read."""

    code = "not-run"


def write_request(request_id: str, data: Mapping[str, Any],
                  env: Mapping[str, str] | None = None) -> Path:
    """Write a page launch's request file, 0600, and return its path. Raises
    `FileExistsError` when one is there already: a ticket starts one job. The file is
    written whole beside its name and then linked to it, which fails when the name is
    taken, so it is made only if it is not there, and is never seen half written."""
    path = targets.request_file(request_id, env)
    files.private_dir(path.parent)
    written = path.with_name(f".{request_id}.{uuid.uuid4().hex}.tmp")
    files.atomic_write(written, json.dumps(dict(data), sort_keys=True, ensure_ascii=False),
                       mode=0o600)
    try:
        os.link(written, path)
    finally:
        with suppress(OSError):
            written.unlink()
    return path


def launch_request(request_id: str, data: Mapping[str, Any]):
    """The `api.LaunchRequest` a request file holds: its preset, the values the page set,
    typed as `catalog.parse` gives them, the flags passed on, the rows left out and the
    rows filled in from an earlier launch (none when the file names none) and
    `off_posture`, under `request_id`. It names no caller's directory: a page launch
    names its own place."""
    from . import api

    def text(key: str) -> str | None:
        value = data.get(key)
        return value if isinstance(value, str) and value else None

    typed = data.get("typed") or {}
    extra = data.get("extra") or []
    left_out = data.get("left_out") or []
    filled = data.get("filled") or []
    if not isinstance(typed, dict) or not isinstance(extra, list) \
            or not isinstance(left_out, list) or not isinstance(filled, list):
        raise NotHere(None, f"the page's request for launch {request_id} is not one the "
                            "launcher wrote", "preview the launch again and launch it")
    return api.LaunchRequest(seat=text("seat"), project_preset=text("project_preset"),
                             typed=typed, extra=tuple(str(word) for word in extra),
                             off_posture=bool(data.get("off_posture")),
                             left_out=tuple(str(name) for name in left_out),
                             filled=tuple(str(name) for name in filled),
                             request_id=request_id)


def resume_request(request_id: str, data: Mapping[str, Any], *,
                   launch: Callable[..., Any] | None = None):
    """Resume the session a request file names, into a slot, as the launch `request_id`
    (`resume.resume`), and return its `Result`. Raises `resume`'s refusals, and `not-run`
    when the launch it resumes ran in a terminal or the file names no session. `launch`
    stands in for `api.launch`."""
    from . import api, resume

    session = data.get("session")
    if not isinstance(session, str) or not session.strip():
        raise NotHere(None, f"the page's request for launch {request_id} names no session "
                            "to resume", "resume the session from the page again")
    slot = data.get("slot")
    slot = slot.strip() if isinstance(slot, str) and slot.strip() else None
    go = launch or api.launch

    def in_slot(request, **kw: Any) -> Any:
        # The launch replayed is the one checked: a terminal resume would become the job.
        if request.row.target != "slot":
            raise NotHere("target", f"launch {request.row.request_id} ran in its "
                                    f"{request.row.target}, and the page resumes only a "
                                    "slot launch",
                          f"run moxi launcher resume {session} in a terminal of your own")
        return go(request, **kw)

    brief, brief_file = data.get("brief"), data.get("brief_file")
    return resume.resume(session.strip(), slot=slot,
                         brief=brief if isinstance(brief, str) else None,
                         brief_file=brief_file if isinstance(brief_file, str) else None,
                         plain=data.get("plain") is True,
                         request_id=request_id, launch=in_slot)


def placed(request, env: Mapping[str, str] | None = None):
    """`request`'s values resolved (`presets.resolve`), refused `no-cwd` when no layer of
    them names where the session starts, the rows left out counted as unnamed: a page
    launch has no caller's directory to fall back on. Raises the resolver's own refusals
    too."""
    from . import presets

    resolved = presets.resolve(request, env=env)
    if resolved.sources.get("cwd") in (presets.SHIPPED, presets.LEFT_OUT):
        raise NoPlace("cwd", "a launch from the page names no directory, project or scratch "
                             "to start in", "choose a directory, a project or scratch, or a "
                                            "preset that names one")
    return resolved


#: What a page launch refused `off-posture` is told to do: the page has no
#: `--off-posture` flag, only its checkbox.
OFF_POSTURE_HINT = ('tick "Allow a permission posture off your default" to allow it, or '
                    "set it in [defaults]")
#: What a refusal record says for an error with no words of its own.
UNEXPECTED_JOB = "the launch job hit an unexpected error ({}); its log has the details"


def refused_detail(error: BaseException) -> dict[str, Any]:
    """`error`, a refusal from before a launch's row, as a `launch-refused` event's detail
    and the page's refusal: `code`, `row`, `problem`, `hint`. An `off-posture` refusal's
    hint names the page's control, not the command line's flag. A configuration file that
    can't be read is `bad-config`; one of the launch log's own refusals has the code the
    command line gives it; an error that is no refusal names only its class."""
    from moxi.config import ConfigError
    from moxi.store import StoreError

    from .cli import _OTHER_ERRORS

    if isinstance(error, catalog.Refused):
        hint = OFF_POSTURE_HINT if error.code == "off-posture" else str(error.hint)
        return {"code": error.code, "row": error.row, "problem": str(error.problem),
                "hint": hint}
    if isinstance(error, ConfigError):
        return {"code": "bad-config", "row": error.key if isinstance(error.key, str) else None,
                "problem": str(error.problem), "hint": str(error.fix or "")}
    if isinstance(error, StoreError) and type(error).__name__ in _OTHER_ERRORS:
        code, hint = _OTHER_ERRORS[type(error).__name__]
        if "{run}" in hint:
            hint = "that run must end first"
        return {"code": code, "row": None, "problem": str(error), "hint": hint}
    return {"code": "error", "row": None,
            "problem": UNEXPECTED_JOB.format(type(error).__name__),
            "hint": "try the launch again; if it fails the same way, read the log"}


def record_refused(request_id: str, detail: Mapping[str, Any]) -> bool:
    """Record a page launch refused before its row as a `launch-refused` event under its
    request id, unless its row is written; say whether it was recorded."""
    conn = store.connect()
    try:
        if store.get(conn, request_id) is not None:
            return False
        store.add_event(conn, store.LAUNCH_REFUSED, dict(detail), request_id=request_id)
        return True
    finally:
        conn.close()


def run_request(request_id: str, *, wait_s: float | None = None,
                launch: Callable[..., Any] | None = None) -> str:
    """Run the page's request file for `request_id` to its end, a launch or a resume, and
    return its outcome: the launch row's state, `refused` (recorded before the row), or
    `timed-out` (it passed `page.launch_wait_s`). The request file is deleted at the end.
    `launch` stands in for `api.launch`."""
    from . import api, settings

    path = targets.request_file(request_id)
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            act = data.get("act")
        except (OSError, ValueError, AttributeError) as request_exc:
            log.warning("the request file of launch %s could not be read: %s", request_id,
                        _words(request_exc))
            record_refused(request_id, refused_detail(NotHere(
                None, f"the page's request for launch {request_id} could not be read",
                "preview the launch again and launch it")))
            return "refused"
        if act not in ACTS:
            record_refused(request_id, refused_detail(NotHere(
                None, f"the page's request names no act it runs ({act!r})",
                "preview the launch again and launch it")))
            return "refused"
        if wait_s is None:
            wait_s = float(settings.current()["page.launch_wait_s"])
        go = launch or api.launch
        done: dict[str, Any] = {}

        def start() -> None:
            try:
                if act == "resume":
                    done["state"] = resume_request(request_id, data,
                                                   launch=launch).data["state"]
                    return
                request = launch_request(request_id, data)
                resolved = placed(request)
                if resolved.target == "terminal":
                    raise NotHere("target", "a launch in a terminal is not run from the page",
                                  "run its command in a terminal of your own")
                done["state"] = go(request).state
            except BaseException as exc:  # noqa: BLE001  (each end is recorded)
                done["error"] = exc

        worker = threading.Thread(target=start, name=f"{act} {request_id}", daemon=True)
        worker.start()
        worker.join(wait_s)
        if worker.is_alive():
            log.warning("%s %s passed page.launch_wait_s (%s s)", act, request_id, wait_s)
            record_refused(request_id, {
                "code": "timed-out", "row": None,
                "problem": f"the {act} did not finish within page.launch_wait_s "
                           f"({wait_s:g} s)",
                "hint": "look at moxi launcher log, then try again"})
            return "timed-out"
        if "error" in done:
            detail = refused_detail(done["error"])
            if detail["code"] == "error":
                log.error("%s %s failed", act, request_id, exc_info=done["error"])
            record_refused(request_id, detail)
            return "refused"
        return str(done["state"])
    finally:
        with suppress(OSError):
            path.unlink()


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
        description="Internal: run one of the launcher's detached runs, or a launch the "
                    "page asked for, to its end.")
    parser.add_argument("kind", choices=KINDS, help="the run's kind")
    parser.add_argument("arg", metavar="ARG",
                        help="the run's id; for expiry and keep-warm, the session's id; "
                             "for launch, the request id")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    if args.kind == PAGE:
        outcome = run_request(args.arg)
        print(f"launch {args.arg}: {outcome}", flush=True)
        return 0 if outcome in LAUNCHED else 1
    if args.kind in ASKS:
        from . import keepwarm, recycle

        asked = recycle.ask_expiry if args.kind == "expiry" else keepwarm.ask
        verdict = asked(args.arg)
        print(f"session {args.arg}: {verdict.line}", flush=True)
        return 0 if verdict.outcome is None or verdict.outcome in DONE else 1
    outcome = run(args.kind, args.arg, signals=True)
    print(f"run {args.arg}: {outcome}", flush=True)
    return 0 if outcome in DONE else 1


def _stamp(at: float) -> str:
    """A wall time as the store writes times."""
    from datetime import datetime, timezone

    return store.stamp(datetime.fromtimestamp(at, timezone.utc))


def _words(error: BaseException) -> str:
    return str(error) or type(error).__name__


__all__ = ["ACTED", "ACTS", "ASKS", "DONE", "GONE", "JOBS", "KINDS", "LAUNCHED", "LOOK",
           "HOLD_S", "NOT_SETTLED", "OFF_POSTURE_HINT", "PACED", "PACE_HOLD_OFF_S", "PAGE",
           "POLL_S", "REPAIRABLE", "RETRY_S",
           "SETTLE_S", "TERMINAL", "TRANSIENT", "UNEXPECTED_JOB", "Choice", "CompactJob", "Ended",
           "LaunchCheckFailed", "NoPlace", "NotHere", "RelaunchJob", "RepairJob", "chain_of",
           "choose_slot", "gone", "job_name", "launch_check", "launch_request", "main",
           "placed", "recorded_pin", "record_refused", "refused_detail", "repairable",
           "replay_request", "resume_request", "root_run", "run", "run_request", "write_request"]

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
