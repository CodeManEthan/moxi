"""comms' Codex adapter: one process per launched identity.

This lives OUTSIDE the daemon: the adapter is an **ordinary daemon-socket client**, and
**its socket connection is the liveness anchor -- its death is its deregistration**. The
daemon stays a pure router with no child supervision.

The Codex session itself is turns': comms' Codex wrapper (`codex_wrapper`, `moxi comms
codex`) binds the name through this adapter, forks it, and execs turns' Codex host,
`moxi-turns-codex-host`, which spawns and supervises `codex app-server`, holds the
WebSocket link, starts the thread, wires Codex's own hooks, opens the window and reaps
every child when the session ends. The exec keeps the wrapper's pid, so the adapter
finds the session with `turns.wait_ready(pid=<that pid>)`, and that process is the root
of the claim. comms reaches the session only through turns' public API. What this
process owns:

  * one registration on the daemon socket for the roster identity, and the claim socket
    it stamps into that environment;
  * the thread record (`codex_threads`), written once the session names its thread;
  * the queue, the digests and the owed ledger. Each digest is handed to turns named by
    its digest id as its `ref`;
  * the stop handler: at each Stop turns asks `on_stop`, which runs the shim's reply
    check (`HarnessAdapter.reply_check`) and answers the reminder text, which holds the
    turn open, or None. turns gives the Stop's facts, the thread's running background
    terminals among them; Codex has no crons.

**A resume.** A launch starts a new thread unless it is told to resume one. Told to
resume thread T (the wrapper's `--resume T`, which the host also gets), every report the
adapter makes describes T as resumed, once: a register before the session is found
gives `session_id` T with `new_session: false`; once found, the launch reports one
`ready` with the resume source, the owed ledger hears that T resumed, and the thread
record names T. turns raises its own resume `Ready` for that first load; when the watch
gives it, it is not reported a second time. A host that ends before it has loaded T ends
the launch, and the binding with it, as a failed first start does.

**Delivery.** Plain mail goes one digest at a time, at turn boundaries only: while no
turn runs, by `deliver(text, ref=<digest>)`, which starts a turn. Mail that arrives
while a turn runs waits in the queue for that turn's end, and goes as one digest then.

**Interrupt mail.** A queued message whose sender asked to interrupt, while a turn runs
on a session that offers `steer`, goes at once: one digest of the interrupt mail alone,
its frame lines marked `[interrupt]`, by `steer(text, ref=<digest>)`. A steer refused
`idle` (the turn ended between the check and the call) or `loading` falls back to
`deliver` with the same text and ref, once; turns runs the text once whichever way the
race went.

**comms' tools.** A launch the daemon serves comms' tools for carries the session's token
(`MOXI_COMMS_MCP_TOKEN`), and this adapter registers its hash after every bind
(`binding.ToolToken`), the first before it says bound, so the host's thread normally
starts after it. A Codex thread whose MCP server failed at its start (a connect before
the registration got a 401) keeps it failed until the server list is reloaded. So after
each registration the daemon answers `served: true`, the adapter calls the host's
`mcp.reload` (turns' `Session.mcp_reload`): after the first as soon as `find_session`
hands it the session, and after each rebind. Only a host whose hello lists `mcp-token`
offers it. The call can take twice the host's 10 s settle when it waits behind a settle
already under way, so it gets `RELOAD_WAIT`, and it runs on a thread of its own: a slow
reload holds up no delivery.

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
import threading
import time
from pathlib import Path

from .. import codex_threads
from ..client import DaemonClient
from ..context import BARE_COMMS, MOXI_COMMS_PATH_LINE, layer_one_form  # noqa: F401
from . import codex_roster
from .claude_shim import render_digest
from .harness import (SOURCE_COMPACT, SOURCE_RESUME, SOURCE_STARTUP, HarnessAdapter,
                      launch_stamp)

log = logging.getLogger("comms.codex")

ADAPTER_KIND = "codex"

FLUSH_RETRY = 1.0
#: How long a wait that timed out pauses before it asks again, in seconds. The digest
#: stays open meanwhile.
WAIT_RETRY = 0.5
#: The steer refusals that fall back to `deliver`: no turn runs to take the text, or the
#: host has no accepted load.
STEER_FALLBACK = ("idle", "loading")
#: A turn outcome that settles its digests `handled`.
ANSWER = "answer"
#: How long one look for the host's session waits before the adapter checks that the
#: host still runs, in seconds.
FIND_STEP = 2.0
#: The capability of a host that offers `mcp.reload`.
MCP_TOKEN_CAPABILITY = "mcp-token"
#: The request bound of `mcp.reload`, in seconds: above the 20 s a call can take when it
#: waits behind a settle already past its first read and then runs one of its own (10 s
#: each), so a slow settle answers rather than timing out.
RELOAD_WAIT = 25.0
#: How long `stop` waits for a reload in flight to end, in seconds.
RELOAD_JOIN = 2.0


# --------------------------------------------------------------------------- layer one
# The line naming which comms to run (`MOXI_COMMS_PATH_LINE`), the bare fallback
# (`BARE_COMMS`) and `own_comms` are `context`'s, one copy for every harness.


def layer_one_text(roster_info, comms: str = BARE_COMMS, *, directors=(),
                   form: str | None = None) -> str:
    """Layer one for a Codex thread: the context block, then the line naming the
    `comms` to use, then the labeled roster snapshot (or the offline line). A tools
    `form` (`context.layer_one_form` of the launch's environment) puts the tools block
    first and the CLI fallback, with the path line, last.

    The block is `context.layer_one_block` for the agent's declared `directors`, and the
    roster is `roster_view`'s one rendering, as for a Claude session, so the two
    harnesses read the same text."""
    from ..context import layer_one
    from ..roster_view import render_agents
    from .shim_roster import LABEL, OFFLINE, usable

    if usable(roster_info):
        roster = f"{LABEL}\n{render_agents(roster_info)}"
    else:
        roster = OFFLINE
    return layer_one(roster, directors=directors, comms=comms, form=form)


def layer_one_hash(comms: str = BARE_COMMS, *, directors=(), form: str | None = None) -> str:
    """What the thread file records of the layer one a thread was given: a hash of its
    fixed part in the form it was given, the context block (with the director sentence
    when the agent declares a director) and the comms path line, or the tools form's
    block and last line. The roster is a snapshot and changes every launch, so it is not
    part of the hash."""
    import hashlib

    from ..context import layer_one_fixed

    text = layer_one_fixed(directors=directors, comms=comms, form=form)
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


# --------------------------------------------------------------------------- the session
def _turns_find(pid: int, timeout: float):
    """turns' session of the host process `pid`, once its first load is accepted.
    Imported at the call: `import moxi_turns` loads nothing."""
    import moxi_turns as turns

    return turns.wait_ready(pid=pid, timeout=timeout)


class HostGone(RuntimeError):
    """The host process ended before its session was ready."""


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
    handler, over the turns session of the thread this launch started or resumed."""

    KIND = ADAPTER_KIND
    STATE_NOUN = "thread-id file"
    QUIET_OFFLINE_ON_STOP = True   # a wrapper stopping closed it: nothing to say
    HOLDS_UNTIL_TURN_ENDS = True   # a digest's ids are held until its turn ends
    log = log

    def __init__(self, agent: codex_roster.CodexAgent, *, socket_path=None, find=None,
                 harness_pid: int | None = None, claim_socket=None, alive=None,
                 on_bound=None, autoreconnect: bool = True, backoff=None,
                 status_sink=None, clock=time.time, comms_path: str | None = None,
                 layer_one: bool = True, exit_hook=None, wait_retry: float = WAIT_RETRY,
                 find_step: float = FIND_STEP, wait_when_held: bool = True,
                 resume: str | None = None):
        #: The host process: turns' Codex host, the wrapper's pid kept across its exec.
        #: The claim's root. None in a test that hands in its own `find`.
        self.harness_pid = harness_pid
        self._claim_socket = claim_socket
        self.WAIT_WHEN_HELD = wait_when_held
        super().__init__(agent, socket_path=socket_path, autoreconnect=autoreconnect,
                         backoff=backoff, status_sink=status_sink, clock=clock,
                         exit_hook=exit_hook)
        #: `find(pid, timeout)`: turns' `wait_ready(pid=)`, or what a test hands in.
        self._find = find or _turns_find
        #: Whether the host still runs; the look for its session stops when it does not.
        self._alive = alive or (lambda: True)
        #: Called once the bind has been tried and the claim socket listens, before the
        #: look for the session: the wrapper execs the host then.
        self._on_bound = on_bound
        self.wait_retry = wait_retry
        self.find_step = find_step
        self.comms_path = comms_path or BARE_COMMS
        #: Whether the thread was given layer one (the wrapper's instructions file), so
        #: the thread record carries its hash.
        self.layer_one = layer_one
        #: The thread this launch was told to resume, or None: it starts a new one.
        self.resume = resume or None
        #: Whether the watch may still give turns' resume ready for the first load,
        #: which the launch reported itself (`start_port`).
        self._first_resume_unseen = False

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
        #: A resumed thread has had its turns before this launch.
        self._own_turned = self.resume is not None
        #: Which launch wrote the thread record: `<pid>@<UTC start>`.
        self.launch = launch_stamp()
        #: What the thread record says the thread was born with (layer one's hash).
        self._born_with: dict = {}
        #: A registration the daemon answered `served: true` whose `mcp.reload` has not
        #: been sent yet, and the thread that sends it, while one runs.
        self._reload_owed = False
        self._reloader: threading.Thread | None = None
        #: Each `mcp.reload` answer, in order (log and test visibility).
        self.reloads: list = []

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
        thread, or resumes the one its caller names, and reads no id from the record."""
        return codex_threads.read_outcome(self.agent.qualified)

    def session_report(self, *, register: bool) -> dict:
        """The thread id, its sessions root and `turned`; on `register`, `new_session`.

        A launch not told to resume a thread starts its own, so a `register` before the
        launch reports `new_session: true`: the thread that follows is this launch's own
        creation. A launch told to resume thread T reports T from its first `register`,
        `new_session: false`, before the session is found. A `register` that holds the
        launch's thread (a daemon reconnect inside the launch) reports it,
        `new_session: false`. `turned` is false until a turn of a new thread ends, and
        true for a resumed one, which had turns before this launch."""
        with self._lock:
            found = self.thread_id
            thread_id = found or self.resume
            turned = self._own_turned
        if found:
            self.claims.note_session(found)
        report: dict = {}
        if thread_id:
            report.update(session_id=thread_id, turned=turned)
            if register:
                report["new_session"] = False
        elif register:
            report["new_session"] = True
        source = self.cache_source()
        if source:
            report["cache_source"] = source
        return report

    # ------------------------------------------------------------------ the launch
    def _make_holder(self) -> None:
        """The base's holder, with the wrapper's claim socket in the session's runtime
        directory and the host process as the claim's root: the adapter is forked away
        from the host, so its own process tree holds no part of the session."""
        super()._make_holder()
        from ..membership import TREE, root_of

        if self._claim_socket is not None:
            self.claims.socket_path = Path(self._claim_socket)
        if self.harness_pid is not None:
            root = root_of(self.harness_pid, TREE)
            self.claims.roots = [root] if root is not None else []

    def _directors(self) -> tuple:
        return tuple(getattr(self.agent, "directors", None) or ())

    def find_session(self):
        """turns' session of the host, once it is ready. Looks in steps of `find_step`
        while the host runs and the adapter has not been stopped; raises `HostGone`
        when the host ended first."""
        while True:
            if self._stop.is_set():
                raise HostGone("the adapter was stopped before the session was ready")
            if not self._alive():
                if self.resume is not None:
                    # The daemon has held T for this name since the register: the
                    # launch's log says the resume failed.
                    self.log.warning("the Codex host of %s ended before it resumed thread "
                                     "%s", self.agent.qualified, self.resume)
                raise HostGone("the Codex host ended before its session was ready")
            try:
                return self._find(self.harness_pid, self.find_step)
            except TimeoutError:
                continue

    def start_port(self) -> None:
        """Say bound, find the host's session, become its stop handler and watch it. A
        failure here ends the launch: there is no session to deliver into."""
        if self._on_bound is not None:
            self._on_bound()
        session = self.find_session()
        thread_id = session.session_id
        born_with = {}
        if self.layer_one:
            # The wrapper wrote layer one from the launch's environment, which this
            # adapter's process carries; `serve` read its form before the token left it.
            born_with["layer_one"] = layer_one_hash(self.comms_path,
                                                    directors=self._directors(),
                                                    form=self.layer_form)
        self._born_with = born_with
        if thread_id:
            codex_threads.write(self.agent.qualified, thread_id,
                                identity=self.agent.qualified, cwd=str(self.agent.cwd),
                                launch=self.launch, **born_with)
        with self._lock:
            self.session = session
            self.thread_id = thread_id
        # The first registration's reload, now that there is a session to ask.
        self._start_reload()
        self._stops = session.handle_stops(self.on_stop)
        # The facts are read at the subscription: no turn event falls between them and
        # the first event the watch gives.
        watch = self._watch_handle = session.watch()
        with self._lock:
            self.turn_open = bool(watch.facts.turn_open)
            # The watch replays nothing: turns' resume ready for the first load reaches
            # it only when no ready had been raised at the subscription.
            self._first_resume_unseen = (self.resume is not None
                                         and getattr(watch.facts, "ready", None) is None)
        self._watcher = threading.Thread(target=self._watch, args=(watch,),
                                         daemon=True, name="comms-codex-watch")
        self._watcher.start()
        self.log.info("found codex thread %s for %s through turns", thread_id,
                      self.agent.qualified)
        if self.resume is not None:
            # The first load resumed the thread: one resume ready, from here.
            self.owed_session_changed(SOURCE_RESUME, thread_id)
            self.report_ready(SOURCE_RESUME)
        else:
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
        with self._lock:
            reloader = self._reloader
        if reloader is not None:
            reloader.join(RELOAD_JOIN)

    # ------------------------------------------------------------------ comms' tools
    def tool_token_registered(self, served: bool) -> None:
        """A registration the daemon answered: when it serves the tools, the host's
        server list is reloaded, now or once the session is found."""
        if not served:
            return
        with self._lock:
            self._reload_owed = True
        self._start_reload()

    def _start_reload(self) -> None:
        """Start the reload thread when a reload is owed, the session is known, and none
        runs (the one that runs sends an owed one when it is done)."""
        with self._lock:
            if (not self._reload_owed or self.session is None or self._stop.is_set()
                    or self._reloader is not None):
                return
            t = self._reloader = threading.Thread(target=self._reload_loop, daemon=True,
                                                  name="comms-codex-reload")
        t.start()

    def _reload_loop(self) -> None:
        try:
            while True:
                with self._lock:
                    session = self.session
                    if not self._reload_owed or session is None or self._stop.is_set():
                        self._reloader = None
                        return
                    self._reload_owed = False
                self._reload(session)
        except BaseException:
            with self._lock:
                self._reloader = None
            raise

    def _reload(self, session) -> None:
        """One `mcp.reload` of the host's servers, when the host offers it. A failure is
        logged: the host settles its servers again at its next ready."""
        try:
            if MCP_TOKEN_CAPABILITY not in session.capabilities:
                self.log.info("the codex host of %s offers no mcp.reload; its servers are "
                              "not reloaded", self.agent.qualified)
                return
            answer = session.mcp_reload(timeout=RELOAD_WAIT)
        except Exception as e:                              # noqa: BLE001
            self.log.warning("mcp.reload for %s failed: %s", self.agent.qualified, e)
            return
        self.reloads.append(answer)
        self.log.info("mcp.reload for %s: reloaded %s, servers %s", self.agent.qualified,
                      answer.get("reloaded"), answer.get("servers"))

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
        """A resume or a compaction of the thread. The startup was reported at launch,
        and so was the first load's resume of a thread the launch was told to resume:
        turns' resume ready for that load, when the watch gives it, is dropped, and every
        later one (a reconnect's) is reported."""
        if source == SOURCE_RESUME:
            with self._lock:
                first = self._first_resume_unseen and session_id in (None, self.resume)
                if first:
                    self._first_resume_unseen = False
            if first:
                self.log.info("the first load's resume of thread %s was reported at the "
                              "launch", self.resume)
                return
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

def main(argv=None) -> int:
    """`moxi-comms-codex`: the Codex wrapper (`codex_wrapper.main`)."""
    from .codex_wrapper import main as wrapper_main

    return wrapper_main(argv)


__all__ = ["ADAPTER_KIND", "CodexAdapter", "FIND_STEP", "HostGone", "MCP_TOKEN_CAPABILITY",
           "RELOAD_WAIT", "layer_one_hash", "layer_one_text", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
