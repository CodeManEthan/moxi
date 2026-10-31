"""comms' Claude Code adapter: the process that holds one launched session's comms.

`moxi comms claude` forks it before it execs `claude` (`session.launch`), one per session
it launches, and it lives as long as the session's `claude` process. It holds what the
claude-shim held when Claude Code spawned it as an MCP server: the session's daemon
binding, the claim socket, the `turned` and root evidence, and the queue, digests and owed
ledger (`ClaudeShim`, which this process runs). What it no longer holds is any way into
the session: turns is the one interface to a harness session, so this adapter is a turns
caller. It finds the session's endpoint, becomes its stop handler, watches its turn
events and hands each digest to it, named by the digest's id as its `ref`.

    the binding        ClaudeShim.start(): registered before `claude` starts
    the claim socket   ClaudeShim's ClaimHolder, rooted at the `claude` process
    finding            turns.find(pid=<claude>), once a second until an endpoint answers
    the stop handler   Session.handle_stops(on_stop): the decision is ClaudeShim.on_stop
    release points     each TurnEnded, and an idle session (no turn open, mail queued)
    a digest           Session.deliver(text, ref=<digest>), settled from its turn's end
    interrupt mail     Session.steer(text, ref=<digest>) into the running turn

**Finding the endpoint.** Turns' Claude runner starts the endpoint at the session's
start, and it answers once it has its session id. Until this adapter has found it, mail
stays in the shim's queue and the daemon is told the session has no endpoint
(`ClaudeShim.notify_endpoint`), which a sender reads as `waiting`. A look takes the stop
handler, then the watch, whose facts are read at the subscription: no turn event falls
between what the look read and the first event the watch gives. The first look is a
release point when the session is idle (a `look` frame: no turn, nothing settled), so mail
the daemon held for a `ready` raised before the look is not left waiting. A look also
learns the session id and reads `turned` from the turn number: a session that has had a
turn has `turned` set.

**Delivery.** Plain mail goes one digest at a time, at turn boundaries only: when a turn
ends (any turn: the person's, or the one that took the digest before it) or when the
session is idle with mail queued and the queue has stopped growing. turns' `deliver`
starts the turn now if the session is idle and else when the running turn ends.

**Interrupt mail.** A queued message whose sender asked to interrupt, while a turn runs
on a session that offers `steer`, goes at once: the shim opens a digest of the interrupt
mail alone, marks its frames `[interrupt]`, and the adapter steers it into the turn. A
steer refused `idle` (the turn ended between the check and the call) or `loading` falls
back to `deliver` with the same text and ref, once; turns runs the text once whichever
way the race went. A session without `steer` takes interrupt mail at the boundary like
any other.

**Settling.** The ack is the end of the turn that took the digest. turns' `turn.ended`
names every delivery the turn took, the opening one and each steered one, by ref, so each
digest settles from its own turn:

    turn ended answer                  handled: the daemon deletes its rows
    turn ended otherwise               unhandled: the daemon hands it again later

What a hand-in's own outcome does before a turn names it:

    taken (an id, no value)            nothing yet: its turn's end settles it
    Refused idle/loading, a steer      one `deliver` with the same text and ref
    Refused gone                       unhandled at once; the endpoint is gone
    Refused (any other reason)         unhandled at once: the text never went in
    unknown (no id, no value)          waited on: the library resolves it by ref; a
                                       timeout keeps the digest open and waits again
    Lost, untied or expired            unhandled at once; the endpoint is kept
    Lost (any other)                   unhandled at once; the endpoint is looked for again

**The endpoint's loss.** A watch that ends, a `Lost` that isn't untied or expired, or a
refusal `gone` means the endpoint went away (a plugin reload, a crash): the adapter
settles every open digest `unhandled`, tells the daemon the session has no endpoint, and
looks again, which finds the same endpoint when it still serves. A Stop in that gap is
not held, and ends the turn.

**Esc.** turns reports an interrupted turn as `aborted`, so the turn's outcome says
whether the digest was handled.

**What ends it.** `claude`'s exit, seen on the handle the wrapper took before it forked
(a pidfd, so a reused pid cannot stand in for it). The adapter closes its binding, which
deregisters the name, and removes its claim socket and claim file. An adapter that dies
first leaves the session unbound until it is relaunched.
"""

from __future__ import annotations

import logging
import os
import select
import threading
import time

from .. import constants
from .claude_shim import ClaudeShim

log = logging.getLogger("comms.claude.adapter")

#: How often the adapter looks for its session's endpoint while it has none: once
#: a second.
LOOK_INTERVAL = 1.0
#: How long a wait that timed out pauses before it asks again, in seconds. The digest
#: stays open meanwhile.
WAIT_RETRY = 0.5
#: The `activity` kind of a first look at an idle session (the daemon's `ACTIVITY_LOOK`).
LOOK = "look"
#: The outcome that settles a digest `handled`.
ANSWER = "answer"
#: The steer refusals that fall back to `deliver`: no turn runs to take the text, or the
#: host has no accepted load.
STEER_FALLBACK = ("idle", "loading")
#: The library's refusal for a hand-in it never wrote, or the endpoint's `gone` error.
GONE = "gone"


def _turns_api():
    import moxi_turns

    return moxi_turns


class _Unsent:
    """A digest that could not be handed to an endpoint (it went away under the flush,
    or the hand-in failed): both waits answer `value` at once."""

    id = None

    def __init__(self, value, ref=None):
        self._value = value
        self.ref = ref

    def wait_started(self, timeout):
        return self._value

    def wait_ended(self, timeout):
        return self._value


class Hand:
    """One digest handed to turns: its `ref` (the digest's id as a string), the
    `digest`, the `text`, whether it went by `steer`, and the library's `delivery`.
    `boundary` is the shim's one boundary digest; a steered digest is not, nor is its
    fallback `deliver`."""

    def __init__(self, ref, digest, text: str, delivery, *, steer: bool):
        self.ref = ref
        self.digest = digest
        self.text = text
        self.delivery = delivery
        self.steer = steer
        self.boundary = not steer
        self.fell_back = False

    def __repr__(self) -> str:
        how = "steer" if self.steer else "deliver"
        return f"Hand({self.ref!r}, {how}, {self.delivery!r})"


class ClaudeAdapter:
    """One Claude Code session's comms: the shim's binding, queue and ledger, delivered
    through turns.

    `harness_pid` is the session's `claude` process, which `find(pid=…)` walks to.
    `turns` is the `moxi_turns` API (injectable for a test)."""

    def __init__(self, shim: ClaudeShim, harness_pid: int, *, turns=None,
                 look_interval: float = LOOK_INTERVAL,
                 idle_poll: float = constants.IDLE_POLL,
                 idle_settle: float = constants.IDLE_SETTLE,
                 wait_retry: float = WAIT_RETRY, clock=time.monotonic):
        self.shim = shim
        self.harness_pid = int(harness_pid)
        self.turns = turns if turns is not None else _turns_api()
        self.look_interval = look_interval
        self.idle_poll = idle_poll
        self.idle_settle = idle_settle
        self.wait_retry = wait_retry
        self._clock = clock
        #: The endpoint (`moxi_turns.Session`), or None while the session has none.
        self.session = None
        #: Every digest handed and not settled, by ref.
        self.hands: dict[str, Hand] = {}
        #: Whether a turn runs, as the watch last said (and the look's facts).
        self.turn_open = False
        #: Endpoints found (test and log visibility).
        self.looks = 0
        self._stops = None
        self._watch_handle = None
        #: Bumped at each look and each loss, so a watch or a wait of an endpoint that is
        #: gone changes nothing.
        self._generation = 0
        #: One decision at a time: a flush, a settle, a turn end, an endpoint's loss. The
        #: shim's own lock nests inside it.
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._seen = 0
        self._settled_at: float | None = None
        shim.handover = self._handover
        shim.endpoint = False

    @property
    def boundary(self) -> Hand | None:
        """The boundary digest's hand, or None."""
        return next((h for h in self.hands.values() if h.boundary), None)

    # ------------------------------------------------------------------ lifecycle
    def start(self, *, tolerate_offline: bool = True) -> ClaudeAdapter:
        """Bind (the shim's register, which needs no endpoint), then look and tick."""
        self.shim.start(tolerate_offline=tolerate_offline)
        for target, name in ((self._look_loop, "look"), (self._idle_loop, "idle")):
            t = threading.Thread(target=target, daemon=True, name=f"comms-claude-{name}")
            t.start()
            self._threads.append(t)
        return self

    def stop(self, *, remove_claim_file: bool = True) -> None:
        """The session ended: give up the stop handler and the watch, close the binding
        (the name deregisters) and remove the claim socket and, by default, the claim
        file."""
        self._stop.set()
        with self._lock:
            held = self._drop_endpoint()
        _close(*held)
        for t in self._threads:
            t.join(timeout=2.0)
        self.shim.stop(remove_claim_file=remove_claim_file)

    def _drop_endpoint(self) -> tuple:
        """Forget the endpoint, under the lock; what to close outside it."""
        held = (self._stops, self._watch_handle, self.session)
        self._stops = self._watch_handle = self.session = None
        self._generation += 1
        return held

    # ------------------------------------------------------------------ finding
    def _look_loop(self) -> None:
        while not self._stop.is_set():
            if self.session is None:
                try:
                    self.look()
                except Exception:                                  # noqa: BLE001
                    log.exception("looking for the session's endpoint failed")
            self._stop.wait(self.look_interval)

    def look(self) -> bool:
        """One look for the session's endpoint. True when one was found, its stops
        handled and its watch begun."""
        T = self.turns
        s = T.find(pid=self.harness_pid)
        if s is None:
            return False
        try:
            stops = s.handle_stops(self._on_stop)
        except T.TurnsError as e:
            log.info("the session's endpoint did not take this adapter as its stop "
                     "handler (%s); looking again", e)
            _close(s)
            return False
        try:
            watch = s.watch()
        except T.TurnsError as e:
            log.info("the session's endpoint did not start a watch (%s); looking again", e)
            _close(stops, s)
            return False
        facts = watch.facts
        with self._lock:
            if self._stop.is_set():
                _close(stops, watch, s)
                return False
            self._generation += 1
            generation = self._generation
            self.session, self._stops, self._watch_handle = s, stops, watch
            self.turn_open = bool(facts.turn_open)
            self.looks += 1
            first = self.looks == 1
            self._learn_session(facts.session_id or s.session_id)
            if facts.turn_number > 0:
                self.shim.note_turned()
            self.shim.notify_endpoint(True)
        threading.Thread(target=self._watch, args=(watch, generation), daemon=True,
                         name="comms-claude-watch").start()
        log.info("found the session's endpoint at %s (session %s, %s)", s.dir,
                 facts.session_id or s.session_id, "a turn runs" if facts.turn_open
                 else "idle")
        if first and not facts.turn_open:
            # The first look is a release point when the session is idle: a `ready` the
            # session raised before this adapter was watching loses nothing. It is not a
            # turn, so it is no evidence for `turned` and settles nothing. A later look
            # (after a plugin reload or an endpoint crash) follows no `ready`, so it
            # releases nothing: `--held` mail waits for a real release point.
            self.shim.notify_activity(LOOK)
        return True

    def _learn_session(self, session_id) -> None:
        """The session id a look read. The first one this adapter learns is not a new
        session (it changes nothing about `turned`); a later one that differs may hold a
        context, so `note_session` sets `turned`."""
        if not session_id:
            return
        with self.shim._lock:
            first = self.shim.session_id is None
            if first:
                self.shim.session_id = session_id
        if not first:
            self.shim.note_session(session_id)

    def _can_steer(self) -> bool:
        s = self.session
        if s is None:
            return False
        try:
            return "steer" in s.capabilities
        except self.turns.TurnsError:
            return False

    # ------------------------------------------------------------------ events
    def _watch(self, watch, generation: int) -> None:
        T = self.turns
        reason = "the watch ended"
        try:
            for event in watch:
                if self._stop.is_set() or generation != self._generation:
                    return
                if isinstance(event, T.Gone):
                    reason = event.reason
                    break
                self._on_event(event)
        except Exception as e:                                     # noqa: BLE001
            log.exception("the watch of the session's endpoint failed")
            reason = f"the watch failed: {type(e).__name__}: {e}"
        self._endpoint_gone(generation, reason)

    def _current(self, session_id) -> bool:
        return not session_id or session_id == self.shim.session_id

    def _on_event(self, event) -> None:
        T = self.turns
        if isinstance(event, T.Ready):
            # A start, resume, clear or compaction. A `clear` lowers `turned` for the new
            # id, and empties the debts the running context never saw; the daemon releases
            # held mail by its source.
            with self._lock:
                if event.session_id:
                    self.shim.note_session(event.session_id, event.source)
                with self.shim._lock:
                    gone = self.shim.ledger.session_changed(event.source, event.session_id)
            if gone:
                log.info("ready %s: %d owed repl(ies) left the ledger with the context",
                         event.source, gone)
            self.shim.notify_ready(event.source)
        elif isinstance(event, T.TurnStarted):
            with self._lock:
                self.turn_open = True
                refs = [self.hands[r.ref].digest for r in event.deliveries
                        if r.ref in self.hands]
            if self._current(event.session_id):
                self.shim.note_turned()
            latency = self.shim.stall.witness()
            if latency is not None:
                log.info("turn %s started %.2fs after the hand-over (%s)", event.turn_id,
                         latency, ", ".join(f"ref {r.ref}" for r in event.deliveries)
                         or "no delivery")
            self.shim.notify_activity(None, ref=refs or None)
        elif isinstance(event, T.TurnSteered):
            with self._lock:
                hand = self.hands.get(event.delivery.ref)
            if hand is not None:
                log.info("turn %s took the steered digest %s", event.turn_id, hand.digest)
                self.shim.notify_activity(None, ref=[hand.digest])
        elif isinstance(event, T.TurnEnded):
            self._turn_ended(event)

    def _turn_ended(self, event) -> None:
        """A turn ended: each digest it names settles from its outcome, and the end is a
        boundary, unless the boundary digest still waits behind it."""
        handled = event.outcome == ANSWER
        with self._lock:
            self.turn_open = False
            boundary_named = False
            for r in event.deliveries:
                hand = self.hands.pop(r.ref, None)
                if hand is None:
                    continue
                log.info("digest %s: its turn ended %s; it is %s", hand.digest,
                         event.outcome, "handled" if handled else "unhandled")
                if hand.boundary:
                    boundary_named = True
                else:
                    self.shim.settle_steered(hand.digest, handled=handled)
            if boundary_named:
                # The boundary digest's own turn: it settles, and the next one goes in.
                self.shim.on_turn_boundary(failed=not handled)
            elif self.boundary is not None:
                # Another turn ended (the person's, or one the digest waits behind): the
                # digest still awaits its own. The turn's block is over, and the daemon
                # hears of the boundary, which releases its held mail.
                self.shim._clear_block()
                if self._current(event.session_id):
                    self.shim.note_turned()
                self.shim._send_turn()
            else:
                # A boundary with no digest awaiting: it reports what settled, tells the
                # daemon, and hands over what is queued.
                self.shim.on_turn_boundary()

    # ------------------------------------------------------------------ delivery
    def _handover(self, text: str, digest, *, steer: bool = False) -> None:
        """The shim's hand-over: one digest to the endpoint, named by its id. Called from
        a flush, under the adapter's lock. Never raises."""
        T = self.turns
        s = self.session
        ref = None if digest is None else str(digest)
        if s is None:
            d = _Unsent(T.Refused(GONE), ref)
        else:
            try:
                if steer and self._can_steer():
                    d = s.steer(text, ref=ref)
                else:
                    d = s.deliver(text, ref=ref)
            except Exception as e:                                 # noqa: BLE001
                log.warning("the hand-in of digest %s failed: %s", digest, e)
                d = _Unsent(T.Lost(f"the hand-in failed: {type(e).__name__}: {e}"), ref)
        hand = Hand(ref, digest, text, d, steer=steer)
        log.info("handed digest %s to turns by %s as delivery %s", digest,
                 "steer" if steer else "deliver", d.id)
        self.hands[ref] = hand
        threading.Thread(target=self._wait, args=(hand, self._generation), daemon=True,
                         name="comms-claude-wait").start()

    def _live(self, hand: Hand, generation: int) -> bool:
        return (not self._stop.is_set() and generation == self._generation
                and self.hands.get(hand.ref) is hand)

    def _wait(self, hand: Hand, generation: int) -> None:
        """Wait for a hand-in's value on its own thread. A steer waits for its start
        first: refused `idle` or `loading` it falls back to `deliver`, once; taken, it
        waits for its turn's end. An unknown outcome is waited on, never replayed; a
        timeout keeps the digest open and waits again."""
        T = self.turns
        while True:
            d = hand.delivery
            try:
                if hand.steer and not hand.fell_back:
                    value = d.wait_started(None)
                    if isinstance(value, T.Refused) and value.reason in STEER_FALLBACK:
                        if self._fall_back(hand, generation, value.reason):
                            continue
                        return
                    if isinstance(value, T.Started):
                        value = d.wait_ended(None)
                else:
                    value = d.wait_ended(None)
            except TimeoutError:
                if not self._live(hand, generation):
                    return
                self._stop.wait(self.wait_retry)
                continue
            except Exception as e:                                 # noqa: BLE001
                log.exception("waiting on digest %s failed", hand.digest)
                value = T.Lost(f"the wait failed: {type(e).__name__}: {e}")
            break
        self._on_result(hand, value, generation)

    def _fall_back(self, hand: Hand, generation: int, reason: str) -> bool:
        """A steer refused `idle` or `loading`: `deliver` the same text with the same
        ref, once. False when the hand settled or the endpoint went meanwhile."""
        T = self.turns
        with self._lock:
            if not self._live(hand, generation):
                return False
            s = self.session
            log.info("the steer of digest %s was refused %s; delivering it instead",
                     hand.digest, reason)
            try:
                hand.delivery = s.deliver(hand.text, ref=hand.ref)
            except Exception as e:                                 # noqa: BLE001
                hand.delivery = _Unsent(T.Lost(f"the hand-in failed: {type(e).__name__}: "
                                               f"{e}"), hand.ref)
            hand.fell_back = True
        return True

    def _on_result(self, hand: Hand, result, generation: int) -> None:
        T = self.turns
        with self._lock:
            if self.hands.get(hand.ref) is not hand:
                return                 # settled already, by its turn's end on the watch
            del self.hands[hand.ref]
            if isinstance(result, T.Ended):
                handled = result.outcome == ANSWER
                log.info("digest %s: its turn ended %s; it is %s", hand.digest,
                         result.outcome, "handled" if handled else "unhandled")
                if hand.boundary:
                    self.shim.on_turn_boundary(failed=not handled)
                else:
                    self.shim.settle_steered(hand.digest, handled=handled)
                    self.shim._send_turn()
                return
            reason = getattr(result, "reason", "")
            gone = ((isinstance(result, T.Lost) and not result.untied)
                    or (isinstance(result, T.Refused) and reason == GONE))
            log.info("digest %s %s (%s): it is unhandled", hand.digest,
                     "was refused" if isinstance(result, T.Refused) else "was lost", reason)
            if hand.boundary:
                self.shim._complete_turn(failed=True)
            else:
                self.shim.settle_steered(hand.digest, handled=False)
                self.shim._send_turn()
        if gone:
            self._endpoint_gone(generation, f"digest {hand.digest}: {reason}")

    def _endpoint_gone(self, generation: int, reason: str) -> None:
        """The endpoint went away while `claude` may live (a reload, a crash): settle
        every open digest `unhandled`, tell the daemon, and look again."""
        with self._lock:
            if generation != self._generation or self.session is None:
                return
            held = self._drop_endpoint()
            self.turn_open = False
            hands, self.hands = list(self.hands.values()), {}
            for hand in hands:
                if hand.boundary:
                    self.shim._settle_awaiting(failed=True)
                else:
                    self.shim.settle_steered(hand.digest, handled=False)
            if hands:
                self.shim._send_turn()
            self.shim._clear_block()
            self.shim.notify_endpoint(False)
        _close(*held)
        if not self._stop.is_set():
            log.info("the session's endpoint went away (%s); looking again", reason)

    # ------------------------------------------------------------------ the stop handler
    def _on_stop(self, facts) -> str | None:
        """The stop handler's answer at a Stop: the shim's reply check. A reminder holds
        the turn open; None lets it end. A fact the harness did not give stays missing,
        and a missing fact lets the turn end (`ClaudeShim.on_stop`)."""
        fields = {}
        if facts.stop_hook_active is not None:
            fields["stop_hook_active"] = facts.stop_hook_active
        if facts.task_types is not None:
            fields["task_types"] = list(facts.task_types)
        if facts.crons is not None:
            fields["crons"] = facts.crons
        decision = self.shim.on_stop(fields)
        if decision.block:
            self.shim.mark_blocked(decision.names)
            return decision.block
        return None

    # ------------------------------------------------------------------ idle
    def _idle_loop(self) -> None:
        while not self._stop.wait(self.idle_poll):
            try:
                self.tick()
            except Exception:                                      # noqa: BLE001
                log.exception("the idle check failed")

    def tick(self) -> bool:
        """One check. True when it handed a digest over.

        Interrupt mail while a turn runs, on a session that can steer, goes into that
        turn at once (`ClaudeShim.flush_interrupt`).

        An idle session with mail queued is standing at a boundary: once the queue has
        stopped growing for `idle_settle`, the queue goes in as one digest, through the
        same flush a turn end uses. It is not a turn end: it sets no `turned` and its
        notify carries no `kind`, so it releases nothing held."""
        with self._lock:
            shim = self.shim
            if self.session is None or not shim.queue:
                self._settled_at = None
                self._seen = len(shim.queue)
                return False
            if self.turn_open and shim.has_interrupt() and self._can_steer():
                return shim.flush_interrupt() is not None
            if (self.boundary is not None or shim.awaiting_completion
                    or self.turn_open or shim.blocked):
                self._settled_at = None
                self._seen = len(shim.queue)
                return False
            queued = len(shim.queue)
            now = self._clock()
            if queued != self._seen or self._settled_at is None:
                self._seen, self._settled_at = queued, now
                return False
            if now - self._settled_at < self.idle_settle:
                return False
            self._settled_at = None
            shim.on_turn_boundary(idle=True)
            return self.boundary is not None


def _close(*things) -> None:
    """Close each of a stop handler, a watch and a session that is not None; a close
    that fails changes nothing."""
    for thing in things:
        if thing is None:
            continue
        try:
            thing.close()
        except Exception:                                          # noqa: BLE001
            log.debug("closing %r failed", thing, exc_info=True)


# --------------------------------------------------------------------------- the process

def wait_for_exit(fd: int, stop: threading.Event | None = None) -> None:
    """Block until the process `fd` (a pidfd) refers to has ended, or `stop` is set."""
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    while not (stop is not None and stop.is_set()):
        if poller.poll(500):
            return


def serve(shim: ClaudeShim, harness_pid: int, harness_fd: int, *, say=None,
          turns=None) -> int:
    """The adapter process's life: bind, give the wrapper its word, deliver until
    `claude` ends, close.

    `say(code, reason)` gives the wrapper its word (`adapters.word`): `BOUND` once the
    bind has been tried (bound, or the daemon not there and the supervisor left to bind
    when it returns) and the claim socket listens, so the wrapper can exec `claude`. A
    bind the daemon refused (the name held by another session, or reserved) or failed is
    said with the daemon's words, and the wrapper ends the launch before `claude`
    starts."""
    from .word import BOUND, exit_of, word_for

    say = say or (lambda code, reason="": None)
    adapter = ClaudeAdapter(shim, harness_pid, turns=turns)
    try:
        adapter.start(tolerate_offline=True)
    except Exception as e:                                  # noqa: BLE001
        log.warning("the launch of this session failed: %s", e)
        word = word_for(e)
        say(word, str(e))
        shim.stop(remove_claim_file=True)
        return exit_of(word)
    say(BOUND)
    log.info("adapter for claude pid %d is up (pid %d)", harness_pid, os.getpid())
    try:
        wait_for_exit(harness_fd)
    finally:
        log.info("claude pid %d has ended; closing this session's comms", harness_pid)
        adapter.stop(remove_claim_file=True)
    return 0


__all__ = ["ClaudeAdapter", "Hand", "LOOK_INTERVAL", "WAIT_RETRY", "serve", "wait_for_exit"]
