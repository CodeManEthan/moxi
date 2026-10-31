"""`moxi-comms-codex` -- comms' Codex adapter, one wrapper process per identity.

This lives OUTSIDE the daemon: the wrapper is an **ordinary daemon-socket client**, and
**its socket connection is the liveness anchor -- its death is its deregistration**. The
daemon stays a pure router with no child supervision.

The Codex session itself is turns': the wrapper calls `turns.codex.launch`, and turns'
host spawns and supervises `codex app-server`, holds the WebSocket link, starts and
resumes the thread, wires Codex's own hooks (`moxi-turns-ping`) with their per-launch
trust, opens the window and reaps every child when the launch ends. comms reaches the
session only through turns' public API. What this process owns:

  * one registration on the daemon socket for the roster identity, and the claim socket
    it stamps into that environment;
  * what comms adds to the launch: the identity stamp in the environment, layer one as
    the thread's developer instructions, and the roster's Codex thread options. turns
    passes them on and reads none of them;
  * the queue, the digests and the owed ledger. Each digest is handed to turns named by
    its digest id as its `ref`;
  * the stop handler: at each Stop turns asks `on_stop`, which runs the shim's reply
    check (`HarnessAdapter.reply_check`) and answers the reminder text, which holds the
    turn open, or None. turns gives the Stop's facts, the thread's running background
    terminals among them; Codex has no crons.

**Delivery.** Plain mail goes one digest at a time, at turn boundaries only: while no
turn runs, by `deliver(text, ref=<digest>)`, which starts a turn. Mail that arrives
while a turn runs waits in the queue for that turn's end, and goes as one digest then.

**Interrupt mail.** A queued message whose sender asked to interrupt, while a turn runs
on a session that offers `steer`, goes at once: one digest of the interrupt mail alone,
its frame lines marked `[interrupt]`, by `steer(text, ref=<digest>)`. A steer refused
`idle` (the turn ended between the check and the call) or `loading` falls back to
`deliver` with the same text and ref, once; turns runs the text once whichever way the
race went.

**Settling.** The ack is the end of the turn that took the digest. turns' `turn.ended`
names every delivery the turn took, the opening one and each steered one, by ref:

    turn ended answer                  handled: the daemon deletes its rows
    turn ended otherwise               unhandled: the daemon hands it again later

What a hand-in's own outcome does before a turn names it:

    taken (Started)                    nothing yet: its turn's end settles it
    Refused idle/loading, a steer      one `deliver` with the same text and ref
    Refused (any other reason)         unhandled at once: the text never went in
    unknown (no id, no value)          waited on: the library resolves it by ref; a
                                       timeout keeps the digest open and waits again
    Lost                               unhandled at once: no turn will name it

An unanswered hand-in is never put back in the queue: the text may already be in a
turn. The session's end (the window closed, or the host failed) settles every open digest
`unhandled` and ends the launch.

The digest itself is `claude_shim`'s `render_digest` / `frame_line` -- one attributed
frame line per message, the routing line already stripped by the daemon -- so the model
sees the same thing on both harnesses, directive standing for the roster's declared
`directors` included.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import codex_threads
from ..client import DaemonClient, DaemonError, DaemonOffline
from ..config import ConfigError
from ..context import BARE_COMMS, MOXI_COMMS_PATH_LINE, own_comms
from . import codex_roster
from .claude_shim import render_digest
from .harness import (SOURCE_COMPACT, SOURCE_RESUME, SOURCE_STARTUP, HarnessAdapter,
                      launch_stamp, plan_comms, plan_head)

log = logging.getLogger("comms.codex")

ADAPTER_KIND = "codex"
BINARY = "codex"

FLUSH_RETRY = 1.0
#: How long a wait that timed out pauses before it asks again, in seconds. The digest
#: stays open meanwhile.
WAIT_RETRY = 0.5
#: The steer refusals that fall back to `deliver`: no turn runs to take the text, or the
#: host has no accepted load.
STEER_FALLBACK = ("idle", "loading")
#: A turn outcome that settles its digests `handled`.
ANSWER = "answer"
#: The reason turns gives when the person closes a Codex window (its prefix).
WINDOW_CLOSED = "the Codex window closed"
EXIT_GAVE_UP = 3


# --------------------------------------------------------------------------- layer one
# The line naming which comms to run (`MOXI_COMMS_PATH_LINE`), the bare fallback
# (`BARE_COMMS`) and `own_comms` are `context`'s, one copy for every harness.


def layer_one_text(roster_info, comms: str = BARE_COMMS, *, directors=()) -> str:
    """Layer one for a Codex thread: the context block, then the line naming the
    `comms` to use, then the labeled roster snapshot (or the offline line).

    The block is `context.layer_one_block` for the agent's declared `directors`, and the
    roster is `roster_view`'s one rendering, as for a Claude session, so the two
    harnesses read the same text."""
    from ..context import layer_one_block
    from ..roster_view import render_agents
    from .shim_roster import LABEL, OFFLINE, usable

    if usable(roster_info):
        roster = f"{LABEL}\n{render_agents(roster_info)}"
    else:
        roster = OFFLINE
    return (f"{layer_one_block(directors)}\n\n{MOXI_COMMS_PATH_LINE.format(comms=comms)}"
            f"\n\n{roster}")


def layer_one_hash(comms: str = BARE_COMMS, *, directors=()) -> str:
    """What the thread file records of the layer one a thread was given: a hash of its
    fixed part, the context block (with the director sentence when the agent declares a
    director) and the comms path line. The roster under them is a snapshot and changes
    every launch, so it is not part of the hash."""
    import hashlib

    from ..context import layer_one_block

    text = f"{layer_one_block(directors)}\n\n{MOXI_COMMS_PATH_LINE.format(comms=comms)}"
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def fetch_roster(socket_path=None, timeout: float = 2.0):
    """The daemon's `agents` answer for layer one, or None when it does not answer."""
    try:
        client = DaemonClient(socket_path, timeout=timeout).connect()
        try:
            return client.call("agents")
        finally:
            client.close()
    except Exception as e:                                  # noqa: BLE001
        log.warning("roster for layer one unavailable: %s", e)
        return None


# --------------------------------------------------------------------------- the install
def binary_version(binary: str, env=None, timeout: float = 15.0) -> str:
    """What `<binary> --version` prints, or why it printed nothing."""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             env=env, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return f"unknown ({e})"
    text = (out.stdout or out.stderr).strip().splitlines()
    return text[0] if text else f"unknown (exit {out.returncode})"


def _turns_launch(cwd, *, instructions=None, env=None, window=True, options=None,
                  verify=None):
    """turns' Codex host. Imported at the call: `import moxi_turns` loads nothing."""
    import moxi_turns as turns

    return turns.codex.launch(cwd, instructions=instructions, env=env, window=window,
                              options=options, verify=verify)


def scrub_own_environ(stamped: dict, environ=None) -> None:
    """turns' host builds the session's environment as this process's own with the
    launch's `env` over it, so a comms stamp the identity does not carry must not be
    in this process's environment either: an agent that declared no directors would
    otherwise inherit the wrapper's `MOXI_COMMS_DIRECTORS`."""
    from .session import INHERITED_COMMS_ENV

    environ = os.environ if environ is None else environ
    for var in INHERITED_COMMS_ENV:
        if var not in stamped:
            environ.pop(var, None)


class HandInFailed:
    """The value of a hand-in that raised, or of a wait that failed: read as lost."""

    def __init__(self, reason: str):
        self.reason = reason

    def __repr__(self) -> str:
        return f"HandInFailed({self.reason!r})"


class _Unsent:
    """A digest that could not be handed to the session (the call raised): its wait
    answers `value` at once."""

    id = None

    def __init__(self, value, ref=None):
        self._value = value
        self.ref = ref

    def wait_started(self, timeout):
        return self._value


class Hand:
    """One digest handed to turns: its `ref` (the digest's id as a string), the
    `digest`, its message `ids`, the `text`, whether it went by `steer`, and the
    library's `delivery` (None while the hand-in call runs). A digest that went by
    `deliver` is the boundary digest; a steered one is not, nor is its fallback."""

    def __init__(self, ref: str, digest, ids, text: str, *, steer: bool):
        self.ref = ref
        self.digest = digest
        self.ids = tuple(ids)
        self.text = text
        self.steer = steer
        self.delivery = None
        self.fell_back = False

    @property
    def boundary(self) -> bool:
        return not self.steer

    def __repr__(self) -> str:
        how = "steer" if self.steer else "deliver"
        return f"Hand({self.ref!r}, {how}, {self.delivery!r})"


class CodexAdapter(HarnessAdapter):
    """One identity: the daemon binding, the queue, the digests in flight and the stop
    handler, over the turns session of the thread this launch started."""

    KIND = ADAPTER_KIND
    STATE_NOUN = "thread-id file"
    QUIET_OFFLINE_ON_STOP = True   # a wrapper stopping closed it: nothing to say
    HOLDS_UNTIL_TURN_ENDS = True   # a digest's ids are held until its turn ends
    log = log

    def __init__(self, agent: codex_roster.CodexAgent, *, socket_path=None, launch=None,
                 window: bool = False, autoreconnect: bool = True, backoff=None,
                 status_sink=None, clock=time.time, write_root=...,
                 comms_path: str | None = None, layer_one: bool = True, exit_hook=None,
                 wait_retry: float = WAIT_RETRY):
        super().__init__(agent, socket_path=socket_path, autoreconnect=autoreconnect,
                         backoff=backoff, status_sink=status_sink, clock=clock,
                         exit_hook=exit_hook)
        #: `turns.codex.launch`, or what a test hands in its place.
        self._launch = launch or _turns_launch
        self.window = window
        self.wait_retry = wait_retry
        self._write_root = write_root
        self.write_root_reason = "given by the caller"
        self.comms_path = comms_path or BARE_COMMS
        self.layer_one = layer_one

        #: The turns session of this launch's thread, once launched.
        self.session = None
        self.thread_id: str | None = None
        self.turn_open = False
        self.active_turn_id: str | None = None
        self._stops = None
        self._watch_handle = None
        self._watcher: threading.Thread | None = None
        #: Why the session ended (`Gone`), once it has.
        self.gone_reason: str | None = None
        self.gone = threading.Event()
        #: Every digest handed to turns and not settled, by ref.
        self.hands: dict[str, Hand] = {}
        #: The turn the reply reminder holds open, while it does.
        self._blocked_turn: str | None = None
        #: Whether a turn of this launch's thread has ended (the cache clock's `turned`).
        self._own_turned = False
        #: Which launch wrote the thread record: `<pid>@<UTC start>`.
        self.launch = launch_stamp()
        #: What the thread record says the thread was born with (layer one's hash).
        self._born_with: dict = {}

    # ------------------------------------------------------------------ the clock's evidence
    def cache_source(self) -> str | None:
        """Where Codex writes rollouts: `$CODEX_HOME/sessions`, else
        `~/.codex/sessions`, in the environment the session gets."""
        env = self.agent.env()
        home = env.get("CODEX_HOME")
        if home:
            return str(Path(home).expanduser() / "sessions")
        base = env.get("HOME")
        root = Path(base) if base else Path.home()
        return str(root / ".codex" / "sessions")

    def record_read(self) -> tuple:
        """The base class's record read. No launch calls it: a launch starts its own
        thread and reads no id from the record."""
        return codex_threads.read_outcome(self.agent.qualified)

    def session_report(self, *, register: bool) -> dict:
        """The thread id, its sessions root and `turned`; on `register`, `new_session`.

        Every launch starts its own thread, so a `register` before the launch reports
        `new_session: true`: the thread that follows is this launch's own creation. A
        `register` that holds the launch's thread (a daemon reconnect inside the launch)
        reports it, `new_session: false`. `turned` is false until a turn of it ends."""
        with self._lock:
            thread_id = self.thread_id
        if thread_id:
            self.claims.note_session(thread_id)
        report: dict = {}
        if thread_id:
            report.update(session_id=thread_id, turned=self._own_turned)
            if register:
                report["new_session"] = False
        elif register:
            report["new_session"] = True
        source = self.cache_source()
        if source:
            report["cache_source"] = source
        return report

    # ------------------------------------------------------------------ the launch
    @property
    def write_root(self):
        if self._write_root is ...:
            self._write_root, self.write_root_reason = codex_roster.record_write_root(
                self.agent.env(), self.agent.cwd)
        return self._write_root

    @write_root.setter
    def write_root(self, value) -> None:
        self._write_root = value

    def launch_options(self) -> dict:
        """The roster-owned settings turns lays on every thread load."""
        return self.agent.launch_options(self.write_root)

    def _directors(self) -> tuple:
        return tuple(getattr(self.agent, "directors", None) or ())

    def developer_instructions(self) -> str | None:
        """Layer one, the thread's developer instructions from its start."""
        if not self.layer_one:
            return None
        return layer_one_text(fetch_roster(self.socket_path), self.comms_path,
                              directors=self._directors())

    def launch_env(self) -> dict:
        """The session's environment: the identity stamp and the claim socket."""
        return {**self.agent.env(), **self.claim_env()}

    def start_port(self) -> None:
        """Launch the thread through turns, become its stop handler and watch it. A
        failure here ends the launch: there is no session to deliver into."""
        instructions = self.developer_instructions()
        options = self.launch_options()
        session = self._launch(str(self.agent.cwd), instructions=instructions,
                               env=self.launch_env(), window=self.window, options=options,
                               verify=lambda answer: self.agent.verify_load(options, answer))
        thread_id = session.session_id
        born_with = {}
        if instructions:
            born_with["layer_one"] = layer_one_hash(self.comms_path,
                                                    directors=self._directors())
        self._born_with = born_with
        if thread_id:
            codex_threads.write(self.agent.qualified, thread_id,
                                identity=self.agent.qualified, cwd=str(self.agent.cwd),
                                launch=self.launch, **born_with)
        with self._lock:
            self.session = session
            self.thread_id = thread_id
        self._stops = session.handle_stops(self.on_stop)
        # The facts are read at the subscription: no turn event falls between them and
        # the first event the watch gives.
        watch = self._watch_handle = session.watch()
        with self._lock:
            self.turn_open = bool(watch.facts.turn_open)
        self._watcher = threading.Thread(target=self._watch, args=(watch,),
                                         daemon=True, name="comms-codex-watch")
        self._watcher.start()
        self.log.info("launched codex thread %s for %s through turns", thread_id,
                      self.agent.qualified)
        # The launch is the session start; a fresh session's mail goes as it comes up.
        self.report_ready(SOURCE_STARTUP)
        self._wake.set()

    def stop_before_unbind(self) -> None:
        stops, self._stops = self._stops, None
        watch, self._watch_handle = self._watch_handle, None
        for thing in (stops, watch):
            if thing is not None:
                try:
                    thing.close()
                except Exception:                           # noqa: BLE001
                    self.log.debug("closing %r failed", thing, exc_info=True)

    # ------------------------------------------------------------------ the session's events
    def _watch(self, watch) -> None:
        try:
            for event in watch:
                if self._stop.is_set():
                    return
                try:
                    if self._on_event(event):
                        return
                except Exception:                           # noqa: BLE001
                    self.log.exception("a session event of %s failed", self.agent.qualified)
        finally:
            if not self._stop.is_set() and not self.gone.is_set():
                self._on_gone("the session's event stream ended")

    def _on_event(self, event) -> bool:
        """One event of `watch()`. True once the session is gone."""
        self._adopt_thread(getattr(event, "session_id", None))
        kind = type(event).__name__
        if kind == "TurnStarted":
            self._turn_started(event.turn_id, [r.ref for r in event.deliveries])
        elif kind == "TurnSteered":
            self._turn_steered(event.turn_id, event.delivery.ref)
        elif kind == "TurnEnded":
            self._turn_ended(event.turn_id, event.outcome,
                             [r.ref for r in event.deliveries])
        elif kind == "Ready":
            self._ready(event.source, event.session_id)
        elif kind == "Gone":
            self._on_gone(event.reason)
            return True
        return False

    def _adopt_thread(self, session_id) -> None:
        """A launch with no window returns once its first load was tried, and a failed
        one leaves turns with no thread yet. The first event that names the thread
        gives it to comms: the thread record, and the daemon's evidence."""
        if not session_id:
            return
        with self._lock:
            if self.thread_id is not None:
                return
            self.thread_id = session_id
        codex_threads.write(self.agent.qualified, session_id,
                            identity=self.agent.qualified, cwd=str(self.agent.cwd),
                            launch=self.launch, **self._born_with)
        self.log.info("codex thread %s of %s named by the session", session_id,
                      self.agent.qualified)
        self.report_refresh()

    def _turn_started(self, turn_id, refs=()) -> None:
        """A turn started, whoever started it. While the reply reminder holds a turn
        open, a new turn resolves it, as a prompt does on the shim. The digests it opened
        with are marked started (`activity` with their ids), which is not an ack."""
        with self._lock:
            self.turn_open = True
            self.active_turn_id = turn_id
            resolves = self.blocked and turn_id != self._blocked_turn
            digests = [self.hands[r].digest for r in refs
                       if r in self.hands and self.hands[r].digest is not None]
        if resolves:
            self.clear_block()
        self._report_started(digests)

    def _turn_steered(self, turn_id, ref) -> None:
        """The running turn took a steered digest: it is marked started."""
        with self._lock:
            hand = self.hands.get(ref)
        if hand is not None and hand.digest is not None:
            self.log.info("turn %s of %s took the steered digest %s", turn_id,
                          self.agent.qualified, hand.digest)
            self._report_started([hand.digest])

    def _report_started(self, digests) -> None:
        """`activity` with no kind, naming the digests a turn took when there are any."""
        with self._lock:
            identity = self.identity
        if identity and not self.offline:
            self.notify_activity(identity, **({"ref": list(digests)} if digests else {}))

    def _turn_ended(self, turn_id, outcome: str, refs) -> None:
        """A turn ended: each digest it names settles, `handled` on `answer` and
        `unhandled` otherwise, in one `turn` frame."""
        ids: list = []
        with self._lock:
            self.turn_open = False
            if self.active_turn_id == turn_id:
                self.active_turn_id = None
            for ref in refs:
                hand = self.hands.pop(ref, None) if ref else None
                if hand is not None:
                    ids.extend(hand.ids)
            first = not self._own_turned
            self._own_turned = True
        completed = outcome == ANSWER
        self.report_turn_end(handled=ids if completed else (),
                             unhandled=() if completed else ids)
        self.clear_block()
        self.log.info("turn %s of %s ended %s: %d message(s) %s", turn_id,
                      self.agent.qualified, outcome, len(ids),
                      "handled" if completed else "unhandled")
        if first:
            self.report_refresh()          # `turned` changed: a report refresh

    def _ready(self, source, session_id) -> None:
        """A resume or a compaction of the thread. The startup was reported at launch."""
        if source == SOURCE_RESUME:
            self.owed_session_changed(SOURCE_RESUME, session_id)
            self.report_ready(SOURCE_RESUME)
        elif source == SOURCE_COMPACT:
            self.report_ready(SOURCE_COMPACT)

    def _on_gone(self, reason) -> None:
        """The session ended (the window closed, or the host failed): every digest still
        open is `unhandled`, and the launch ends."""
        with self._lock:
            if self.gone.is_set():
                return
            self.gone_reason = str(reason)
            self.gone.set()
            ids = [mid for hand in self.hands.values() for mid in hand.ids]
            self.hands.clear()
            self.turn_open = False
        if ids:
            self.report_turn_end(unhandled=ids)
        self.log.info("the codex session of %s ended: %s", self.agent.qualified, reason)
        if not self._stop.is_set():
            self.exit_hook()

    # ------------------------------------------------------------------ the holder
    def on_stop(self, facts) -> str | None:
        """turns' Stop ask: the reply check. The reminder text holds the turn open."""
        tasks = getattr(facts, "task_types", None)
        fields = {"stop_hook_active": getattr(facts, "stop_hook_active", None),
                  "task_types": list(tasks) if tasks is not None else None,
                  "crons": getattr(facts, "crons", None)}
        decision = self.reply_check(fields)
        if not decision.block:
            return None
        self.mark_blocked(decision.names)
        with self._lock:
            self._blocked_turn = getattr(facts, "turn_id", None)
        return decision.block

    def clear_block(self) -> None:
        super().clear_block()
        with self._lock:
            self._blocked_turn = None

    # ------------------------------------------------------------------ delivery
    def can_inject(self) -> bool:
        return self.session is not None and not self.gone.is_set()

    def may_take_held(self) -> bool:
        """A payload released from the queue goes only while no turn runs; at the
        running turn's end the flush starts a new turn with it."""
        return not self.turn_open

    def mirror_session(self) -> str | None:
        with self._lock:
            return self.thread_id

    def _can_steer(self) -> bool:
        session = self.session
        if session is None:
            return False
        try:
            return "steer" in session.capabilities
        except Exception:                                   # noqa: BLE001
            return False

    def flush(self) -> str | None:
        """Plain mail goes only at a boundary: no turn runs and no plain digest waits
        for its turn. While a turn runs, interrupt mail goes into it by `steer` when the
        session offers it (`flush_interrupt`); the rest waits for the turn's end."""
        with self._lock:
            if self.turn_open:
                steer = (not self.replaying and self.can_inject() and self._can_steer()
                         and any(self._steerable(p) for p in self.queue))
                if not steer:
                    return None
            elif any(hand.boundary for hand in self.hands.values()):
                return None
            else:
                steer = False
        if steer:
            return self.flush_interrupt()
        return super().flush()

    @staticmethod
    def _steerable(payload) -> bool:
        """Interrupt mail that may go into a running turn. Held mail never does."""
        return bool(payload.get("interrupt")) and not payload.get("held")

    def flush_interrupt(self) -> str | None:
        """One digest of the queued interrupt mail, and only that, rendered with
        `[interrupt]` on its frame lines and handed by `steer`. The rest of the queue
        waits for the turn's end. Returns the digest text, or None when nothing went."""
        with self._lock:
            batch = sorted((p for p in self.queue if self._steerable(p)),
                           key=lambda p: int(p["message_id"]))
            if not batch:
                return None
            taken = {id(p) for p in batch}
            self.queue = [p for p in self.queue if id(p) not in taken]
            identity = self.identity
        batch = self._open_batch(batch, None)
        if not batch:
            return None
        ids = tuple(str(p["message_id"]) for p in batch)
        high = max(int(mid) for mid in ids)
        try:
            text = render_digest(batch, None, self.agent.directors, clock=self.clock,
                                 steered=True)
            self.marks[str(high)] = self.claims.hold_mark()
            receipt = self._hand_in(text, high, ids, steer=True)
        except BaseException:
            self.release_held(ids)
            raise
        with self._lock:
            self.injected.append(text)
            now = self.clock()
            directors = tuple(getattr(self.agent, "directors", None) or ())
            session_id = self.mirror_session()
            for p in batch:
                self.reply_ledger.open(p, directors, session_id, now)
        self.delivered(text, high, identity, len(batch), receipt)
        return text

    def deliver(self, text: str, high: int, identity, count: int):
        """Hand the boundary digest to turns by `deliver`, named by its digest id. The
        receipt is the ref; the digest settles from its turn's end, or from its own
        outcome when no turn will name it. Never `REQUEUE`: a hand-in whose answer did
        not come may be in a turn already, so it is waited on, never handed again."""
        return self._hand_in(text, high, self.digest_ids(high), steer=False)

    def _hand_in(self, text: str, high: int, ids, *, steer: bool) -> str:
        """One digest to the session, by `steer` or `deliver`, and a thread that waits
        for its outcome. The hand is recorded before the call, so a turn that names it
        before the call returns still finds it."""
        with self._lock:
            digest = self.digest_of.get(str(high))
            ref = str(digest) if digest is not None else f"message-{high}"
            hand = Hand(ref, digest, ids, text, steer=steer)
            self.hands[ref] = hand
            session = self.session
        try:
            if steer:
                delivery = session.steer(text, ref=ref)
            else:
                delivery = session.deliver(text, ref=ref)
        except Exception as e:                              # noqa: BLE001
            self.log.warning("the hand-in of digest %s failed: %s", ref, e)
            delivery = _Unsent(HandInFailed(f"the hand-in failed: {type(e).__name__}: "
                                            f"{e}"), ref)
        hand.delivery = delivery
        self.log.info("handed digest %s to turns by %s as delivery %s", ref,
                      "steer" if steer else "deliver", delivery.id)
        threading.Thread(target=self._wait, args=(hand,), daemon=True,
                         name="comms-codex-wait").start()
        return ref

    def _live(self, hand: Hand) -> bool:
        return not self._stop.is_set() and self.hands.get(hand.ref) is hand

    def _wait(self, hand: Hand) -> None:
        """Wait for a hand-in's start on its own thread. Taken, the turn's end settles
        it. A steer refused `idle` or `loading` falls back to `deliver`, once. An unknown
        outcome is waited on, never replayed; a timeout keeps the digest open and waits
        again."""
        while True:
            try:
                value = hand.delivery.wait_started(None)
            except TimeoutError:
                if not self._live(hand):
                    return
                self._stop.wait(self.wait_retry)
                continue
            except Exception as e:                          # noqa: BLE001
                self.log.exception("waiting on digest %s failed", hand.ref)
                value = HandInFailed(f"the wait failed: {type(e).__name__}: {e}")
            if (type(value).__name__ == "Refused" and hand.steer and not hand.fell_back
                    and value.reason in STEER_FALLBACK):
                if self._fall_back(hand, value.reason):
                    continue
                return
            break
        self._on_result(hand, value)

    def _fall_back(self, hand: Hand, reason: str) -> bool:
        """A steer refused `idle` or `loading`: `deliver` the same text with the same
        ref, once. False when the hand settled or the session ended meanwhile."""
        with self._lock:
            if not self._live(hand) or self.gone.is_set():
                return False
            session = self.session
            hand.fell_back = True
        self.log.info("the steer of digest %s was refused %s; delivering it instead",
                      hand.ref, reason)
        try:
            hand.delivery = session.deliver(hand.text, ref=hand.ref)
        except Exception as e:                              # noqa: BLE001
            hand.delivery = _Unsent(HandInFailed(f"the hand-in failed: "
                                                 f"{type(e).__name__}: {e}"), hand.ref)
        return True

    def _on_result(self, hand: Hand, value) -> None:
        """A hand-in's start, or why it has none. Taken (`Started`, or `Ended` for a
        turn already over), the turn's end on the watch settles it. Anything else means
        no turn will name it: it is unhandled at once."""
        kind = type(value).__name__
        if kind in ("Started", "Ended"):
            return
        with self._lock:
            if self.hands.get(hand.ref) is not hand:
                return                     # settled already, by its turn's end
            del self.hands[hand.ref]
        reason = getattr(value, "reason", "")
        self.log.warning("digest %s for %s %s (%s): %d message(s) unhandled", hand.ref,
                         self.agent.qualified,
                         "was refused" if kind == "Refused" else "was lost", reason,
                         len(hand.ids))
        self.report_turn_end(unhandled=hand.ids)

    def delivered(self, text: str, high: int, identity, count: int, receipt) -> None:
        if self._daemon is not None:
            self.notify_injected(identity, high, text)
        log.info("injected %d message(s) for %s through %s as digest %s",
                 count, identity, high, receipt)

    def retry_delay(self) -> float:
        return FLUSH_RETRY


# --------------------------------------------------------------------------- main

def build_parser():
    import argparse

    ap = argparse.ArgumentParser(
        prog="moxi-comms-codex",
        description="comms' Codex adapter: one wrapper per identity, over turns' Codex "
                    "host")
    ap.add_argument("identity", help="qualified roster identity, e.g. planner@proj-a")
    ap.add_argument("--window", action="store_true",
                    help="also open the Codex window on this launch's thread, in this "
                         "terminal; closing the window stops the wrapper and takes the "
                         "identity offline")
    ap.add_argument("--config", metavar="PATH",
                    help="a comms config file, in config.toml's format, whose [codex] "
                         "table is this launch's roster in place of the box's config "
                         "file's; the file is checked whole, and every other setting "
                         "still comes from the box's config file")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan for this identity and exit; launch nothing")
    ap.add_argument("--log-level", default="INFO")
    return ap


def window_log_path(qualified: str) -> Path:
    """Where a `--window` run's wrapper log goes."""
    from ..paths import codex_threads_dir

    return codex_threads_dir() / f"{qualified}.log"


def print_plan(agent, roster, args, *, version, comms_path, write_root,
               write_root_reason, out=None) -> None:
    out = out or sys.stdout

    def say(text):
        print(text, file=out)

    plan_head(agent, roster, say,
              record=f"  thread     {codex_threads.path(agent.qualified)} "
                     "(a new thread at this launch)")
    say(f"  sandbox    {agent.permissions or agent.sandbox} "
        f"(network {'on' if agent.network_access else 'OFF'}, approvals never by turns)")
    if not agent.permissions:
        for root in agent.effective_writable_roots(write_root):
            say(f"  writable   {root}")
        if write_root is None:
            say(f"  writable   (nothing added: {write_root_reason})")
    say(f"  codex      {BINARY} on the session's PATH ({version})")
    plan_comms(comms_path, say)
    say("  host       turns' Codex host (app-server, hooks, delivery, window)")
    if args.window:
        say(f"  log        {window_log_path(agent.qualified)}")


def main(argv=None) -> int:  # pragma: no cover - exercised end to end by the suite
    import shutil
    import signal

    args = build_parser().parse_args(argv)
    try:
        roster = codex_roster.load(path=args.config)
        agent = roster.require(args.identity)
    except ConfigError as e:
        print(f"moxi-comms-codex: {e}", file=sys.stderr)
        return 2

    comms_path = own_comms()
    write_root, write_root_reason = codex_roster.record_write_root(agent.env(), agent.cwd)
    env = agent.env()
    binary = shutil.which(BINARY, path=env.get("PATH"))
    version = binary_version(binary, env=env) if binary else "not found"
    print_plan(agent, roster, args, version=version, comms_path=comms_path,
               write_root=write_root, write_root_reason=write_root_reason)
    sys.stdout.flush()             # a log reader sees the plan before anything else
    if args.window and comms_path is None:
        print("moxi-comms-codex: this environment has no `moxi` script, so the agent "
              "could not be told which comms to run.", file=sys.stderr)
        return 2
    if args.dry_run:
        return 0
    if args.window:
        from moxi_comms.adapters.terminal import in_terminal_foreground, terminal_fd

        if not in_terminal_foreground(terminal_fd()):
            print("moxi-comms-codex: --window needs to run in the foreground of a terminal, "
                  "because the window takes that terminal.", file=sys.stderr)
            return 2
    log_path = window_log_path(agent.qualified) if args.window else None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=args.log_level, filename=str(log_path),
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        print(f"moxi-comms-codex: logging to {log_path}", file=sys.stderr, flush=True)
    else:
        logging.basicConfig(level=args.log_level, stream=sys.stderr,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if comms_path is None:
        log.warning("this environment has no moxi script; layer one names a bare "
                    "`moxi comms`, which the agent's PATH decides")

    stopping = threading.Event()
    adapter = CodexAdapter(agent, window=args.window, write_root=write_root,
                           comms_path=comms_path, exit_hook=stopping.set)
    adapter.write_root_reason = write_root_reason
    scrub_own_environ(adapter.launch_env())

    def _bye(signum, _frame):
        log.info("signal %d: stopping", signum)
        stopping.set()

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT, signal.SIGHUP):
        signal.signal(sig, _bye)
    if args.window:
        from moxi_comms.adapters.terminal import JOB_CONTROL_SIGNALS

        for sig in JOB_CONTROL_SIGNALS:
            signal.signal(sig, signal.SIG_IGN)
    # ONE finally for every road out; turns reaps the host when this process ends.
    try:
        try:
            adapter.start()
        except (DaemonOffline, DaemonError, ConfigError) as e:
            print(f"moxi-comms-codex: {e}", file=sys.stderr)
            return 4 if isinstance(e, DaemonOffline) else 2
        except Exception as e:                              # noqa: BLE001
            if args.window and type(e).__name__ != "HooksUntrusted":
                # turns' window wait ran out, or the window failed before a load was
                # accepted: the wrapper gives up, exit 3, with the log named.
                print(f"moxi-comms-codex: gave up on {agent.qualified}: "
                      f"{str(e).rstrip('. ')}. The log is {log_path}.",
                      file=sys.stderr, flush=True)
                return EXIT_GAVE_UP
            # turns could not launch the session (no codex, untrusted hooks, a dead
            # app-server): one line, never a traceback.
            print(f"moxi-comms-codex: {agent.qualified} was not launched: {e}",
                  file=sys.stderr)
            return 2
        log.info("moxi-comms-codex running for %s", agent.qualified)
        while not stopping.wait(0.5):
            pass
        reason = adapter.gone_reason
        if reason is None or reason.startswith(WINDOW_CLOSED):
            return 0
        print(f"moxi-comms-codex: {agent.qualified}'s session ended: {reason}"
              + (f". The log is {log_path}." if log_path else ""), file=sys.stderr,
              flush=True)
        return EXIT_GAVE_UP
    finally:
        adapter.stop()


__all__ = ["CodexAdapter", "ADAPTER_KIND", "build_parser", "main", "scrub_own_environ"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
