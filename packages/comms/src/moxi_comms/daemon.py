"""The comms daemon: one record connection, one routing table, in memory only.

Contract anchors:
  * routing lives in daemon MEMORY ONLY, rebuilt from live connections;
  * addressability = something that can deliver is connected right now;
  * the connection IS the liveness signal (close == deregistration);
  * the `block` setting is enforced HERE, symmetric, read at use;
  * catch-up at a bind is the name's rows, then `replay_complete`; nothing is replayed
    from the record, since every routed message holds a row until it is acked or
    dropped;
  * a record reconnect that could not RESUME triggers the #names rescan;
  * the queue: every message to a name is delivered live or waits
    in the name's queue, the delivery books' rows in comms.db mirrored in `queue` here.
    Live means: the name is bound, its cache reads `warm`, `fresh` or `no-cache` (or
    `unknown` while the send's `[cold-at:…]` for it is still ahead, or is `-`), and the
    message is not flagged `[held]`. Anything else waits: offline, cold, or flagged. A
    queued message goes out only at the name's next release point (a `ready` with a
    `source`, a `turn_ended`, or a bind on a passing read), never because another
    message to the name went out. A `turn_ended` marked `answered` (the turn ended
    with the model's answer, so a request was just made) releases every row at once,
    with no read. Any other turn end whose read does not pass stays open for a settle
    window (`RELEASE_SETTLE_STEPS`), read again until a read passes or the window ends;
    an answered one opens the window too, for a send that queues in the record's lag. A row is deleted only when a `turn_ended` from
    the binding names the digest that holds it `handled`; never by cursor and never
    at unbind. An unhandled digest's rows, the rows a binding held at its unbind, and
    a hand-off whose socket send failed wait again. Rows are never trimmed by age;
  * the audience mark: an absent audience on a reply is
    filled in at `_deliver` from an in-memory index of effective audiences, and what
    the index cannot answer is delivered as `unknown`, never fetched. The index holds
    three values: a tag, known none, or unknown, and unknown never decays
    into known none down a chain or on a re-index;
  * the ask index: `asks` holds each verified
    `[reply-once]` message this daemon has routed, with the names it still binds. It
    NOMINATES candidates for the send verb, which decides on its own read of the record;
    the daemon refuses, holds and rewrites nothing over a posture. Fed in
    `_route` only, forward-only, bounded to the newest `RESCAN_LIMIT` entries, and a
    missing entry only ever lets a send through.
  * the sent index: `sent` holds, per author,
    every `#names` message of the last `OWED_TTL` with its recipient tags as written, its
    posture, its `re:`, its time, whether the operator's session was up when it was
    routed, and its attribution. It is rebuilt at boot from one read of
    the record and fed in `_route` after the recipients are claimed, and it answers the
    shim's `query kind="owed"` at a Stop: whether each debt is paid, what each kept
    `re:` points at, who the session is waiting on, and whether mail is on its way. The
    daemon decides nothing from it; the shim does. Memory only, forward-only, a derived
    index of the record.
  * the hand-over index: `handed_at` holds, per (name, message id), when `_deliver`
    handed the message to that name's binding, in memory, trimmed past `THREAD_IDLE`.
    With `started_at` and the held and pending sets it answers the send verb's
    `query kind="readable"`, which the verb reads to tell when a thread went idle for
    the replier. It records delivery and decides nothing.
  * the unanswered record: a shim that drops a debt
    whose reminder was ignored says so with an `unanswered` notify from its binding's
    own connection. The daemon keeps these in memory until the owing name's next
    payment to that author lands in the sent index, the message passes `OWED_TTL`, or
    the owing binding ends; `moxi comms agents` lists them and the author's next prompt delta
    shows the ones naming it. No timer runs anywhere.

Routing stays memory-only. What the daemon also does is record custody in the binding
history (`bindings.py`): the two moments it alone witnesses -- the bind, and the
    connection-close release -- are appended to comms.db's `bindings`. The binding history is
    evidence and never a routing input: after the old record's one-time conversion it is
    read only for whether an identity has existed, and no lookup in it can make anything
    addressable.

The daemon holds no posting credential: it posts nothing to any room.
"""

from __future__ import annotations

import bisect
import contextlib
import hashlib
import hmac
import logging
import os
import secrets
import signal
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from moxi import ipc

from . import (attribution, bindings, cache, claimfile, config, constants, directory,
               membership, wire)
from .store import books as books_mod
from .mcp import tokens as mcp_tokens_mod
from .attribution import Author
from .machine_line import (COLD_AT_LIVE, POSTURE_NONE,
                           POSTURE_ONCE, parse as parse_machine_line)
from .record import AUTHOR_IDENTITY, AUTHOR_OPERATOR, Message
from . import kinds as kinds_mod
from . import reserved as reserved_mod
from .paths import comms_db, daemon_socket
from .record import Forbidden
from .provision import NAMES
from .scoping import (GLOBAL, NAME_RE, OPERATOR_NAME, is_auto_name, qualify, split_tag,
                      tag_matches)

log = logging.getLogger("comms.daemon")

TIER_EPHEMERAL = attribution.TIER_EPHEMERAL

#: The most messages one `replies_to` read answers.
REPLY_QUERY_LIMIT = 8
TIER_CHOSEN = attribution.TIER_CHOSEN
TIER_STANDING = attribution.TIER_STANDING

#: comms' own adapter kinds (`kinds.BUILT_IN`). A module adds kinds through the
#: `moxi.comms.kinds` group; the daemon accepts these and those (`Daemon.kinds`).
ADAPTER_KINDS = tuple(kinds_mod.BUILT_IN)

#: The sent index is trimmed by age once per this many entries fed, so the cut is paid
#: rarely rather than once per message.
SENT_TRIM_EVERY = 200

#: Where a delivered payload's `audience` came from. The fourth state is None: no
#: audience on line zero and the message is not a reply.
AUDIENCE_WRITTEN = "written"        # the token was on this message's line zero
AUDIENCE_INHERITED = "inherited"    # filled in from the referent's effective audience
AUDIENCE_UNKNOWN = "unknown"        # a reply whose referent this daemon cannot answer for


class _UnknownAudience:
    """The index's third value: this message's effective
    audience could not be worked out -- a reply whose referent was not indexed, or was
    itself unknown. Distinct from None (known to carry no audience), so an unknown is
    delivered as `unknown` at every later hop instead of reading as "no mark"."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "AUDIENCE_INDEX_UNKNOWN"


#: The sentinel the audience index stores for an unknown effective audience.
AUDIENCE_INDEX_UNKNOWN = _UnknownAudience()

#: How far past its window the audience index may grow before it is cut back to it. The
#: cut sorts, so it is paid once per this many messages rather than once per message.
AUDIENCE_INDEX_SLACK = constants.RESCAN_LIMIT // 10

#: The ask index's slack past its window, for the same reason as the audience index's.
ASK_INDEX_SLACK = constants.RESCAN_LIMIT // 10

#: How often the daemon trims the record past retention, once past boot. Daily, checked
#: on the trim loop's tick.
TRIM_INTERVAL = 24 * 3600
#: How often the trim loop wakes to check `TRIM_INTERVAL`.
TRIM_TICK = 5.0

# `moxi comms explain`'s four cases, classified on demand
CASE_HANDLED = "handled"
CASE_NOT_ADDRESSABLE = "not-addressable"
CASE_IGNORED = "delivered-and-ignored"
CASE_TURN_INCOMPLETE = "delivered-but-the-turn-never-completed"
#: Not one of the four: the message waits in the name's queue, so nothing has handed it
#: out since it was queued. `moxi comms explain` checks it before the cursor, so a queued id
#: never reads `cursor-behind-it`.
CASE_WAITING = books_mod.WAITING
#: Two more: the message left the name's queue undelivered, past its expiry or withdrawn
#: by its author. Read from the books' `dropped` rows, before every other case.
CASE_EXPIRED = books_mod.EXPIRED
CASE_WITHDRAWN = books_mod.WITHDRAWN
#: And one more, read the same way: the message waited for a name that a module then
#: reserved, so it was dropped.
CASE_RESERVED = books_mod.RESERVED

#: The `kind` of an `activity` notify for a turn that ran and ended. With `handled` on
#: it, it is a release point and settles the ids it names.
ACTIVITY_TURN = "turn"
#: The `kind` of an `activity` notify for a session start, clear, compaction or resume.
#: With a `source` on it, it is a release point.
ACTIVITY_READY = "ready"
#: The `kind` of an `activity` notify for a Claude adapter's first look at its session's
#: turns endpoint while the session is idle: a release point, on a passing read, that
#: settles nothing and is no evidence of a turn (`turned` is left as the frame says).
ACTIVITY_LOOK = "look"
#: A `ready` source that starts a context with nothing cached to lose: it releases every
#: row without reading the cache. Any other source (`resume`, Claude Code's `fork`, a
#: value this daemon does not know) reads the cache first.
SOURCES_UNREAD = ("startup", "clear", "compact")
#: The settle window: the seconds after a `turn` frame at which the daemon reads
#: the binding's cache again when the frame's own read did not pass. The harness can
#: write the turn's record after its end is reported (Claude Code drains its transcript
#: writes on a 100 ms timer), so the read at the frame can judge the cache as it was
#: before the turn. 8 s, the last step, is eighty times that timer and leaves room for a
#: loaded machine; a window costs at most this many reads.
RELEASE_SETTLE_STEPS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
# The operator's identity, `OPERATOR_NAME`. He has no session to hold mail for, so
# `[held]` is read as absent for him: his mail is live when a session of his is bound and
# waits as offline mail otherwise.

#: The doctor record a daemon that can't open comms.db leaves, and a start that opens it
#: clears.
STORE_RECORD = "store"

#: The key a routing pass keeps in its memo beside the cursors: the cache's one read per
#: binding per pass.
_GATE_MEMO = "\0gate"
#: The key a rescan keeps in its memo for the routed mark it read as it began.
_ROUTED_MEMO = "\0routed"

#: What each case means, for the person reading `moxi comms explain` because an agent has
#: gone quiet. These live beside the constants so there is one wording and it cannot
#: drift from the four.
CASE_REASONS = {
    CASE_HANDLED: (
        "The cursor is past this message. The turn completed and comms counts it "
        "handled."),
    CASE_NOT_ADDRESSABLE: (
        "Nothing holds this name now. There is no adapter left to have handled the "
        "message, and it waits in the record until a matching identity binds."),
    CASE_IGNORED: (
        "comms handed the message to a live adapter and the adapter never reported it "
        "reaching the model. Either the injection never happened or its ping was lost."),
    CASE_TURN_INCOMPLETE: (
        "The adapter reported the message reaching the model and no turn has completed "
        "since. The cursor moves on the adapter's turn boundary and on nothing else, "
        "so a session that died mid-turn stops exactly here."),
    CASE_EXPIRED: (
        "The message waited past its expiry and was dropped before it reached the "
        "model. Its sender was not told; the expiry is on its line zero."),
    CASE_WITHDRAWN: (
        "Its author withdrew the message (moxi comms delete) before it reached the "
        "model, so it was never handed over."),
    CASE_RESERVED: (
        "The message was waiting for this name when a module reserved it as a send-only "
        "name. Nobody receives mail under a reserved name, so it was dropped undelivered, "
        "and its sender was not told."),
}


@dataclass
class SentEntry:
    """One `#names` message in the sent index.

    `tags` are the recipient tags exactly as written on line zero: the index keys on
    nothing `resolve` computes, because a bare tag qualifies differently live and at boot.
    """

    id: str
    author: str
    tags: tuple
    posture: str
    reply_to: str | None
    at: float
    verified: bool


@dataclass(eq=False)
class SettleWindow:
    """One open settle window: the binding whose `turn` frame read a cache
    that did not pass, when the frame arrived (`time.monotonic`), the steps it reads
    at, the ids the frame reported `unhandled` (never handed by its release) and how
    many reads it has made. `closed` is set once, by its pass, its end or whatever
    closed it early."""

    binding: "Binding"
    opened: float
    steps: tuple
    exclude: frozenset
    taken: int = 0
    closed: bool = False

    def due(self) -> float:
        return self.opened + self.steps[self.taken]


#: The most bytes of an `injected` digest's text the daemon keeps.
INJECTED_CAP = 64 * 1024

#: The most `deliver` frames one binding may have out that its adapter hasn't yet taken
#: into a digest. A push past it waits in the binding's overflow and goes out, by id, as
#: the adapter takes what it has, so a long catch-up never fills the connection's queue
#: of frames and drops it.
WINDOW = 64


@dataclass
class Binding:
    conn: object          # the `moxi.ipc.Connection` that holds the binding
    qualified: str
    name: str
    scope: str
    tier: str
    adapter: str
    bound_at: float
    token_hash: bytes = b""
    replay_hwm: str = "0"     # the record's high-water mark when this binding was made
    delivered: set = field(default_factory=set)
    history_id: int | None = None   # the custody interval to close at deregistration
    #: The live Claude session behind this binding, as its own adapter last reported it,
    #: and when. Written only from the connection that holds this binding: an
    #: `activity` frame's identity is not authenticated, and a forged id would point
    #: the send check at the wrong transcript. None until reported; an adapter built
    #: before this never does.
    session_id: str | None = None
    session_seen_at: float | None = None
    #: The evidence a missing transcript needs to read `fresh` (cold-sends): the root
    #: the session's own Claude Code writes under, as its shim reported it, and whether a
    #: prompt or a turn end has been seen in `session_id`. Taken only from this binding's
    #: own connection. A shim built before them reports neither, and its
    #: `projects_root` stays None, so its missing transcript reads `unknown`.
    projects_root: str | None = None
    turned: bool = False
    #: A codex or opencode binding's evidence for its cache clock, taken only from this
    #: binding's own connection: where its harness keeps the record the clock reads (the
    #: Codex sessions root, the OpenCode database), the wrapper's word on `register`
    #: that its own attempt found no record and starts a new thread or session (cleared
    #: once an id is held), and the opencode default agent and its model, `MISSING`
    #: until reported and kept apart from a reported agent with no model.
    cache_source: str | None = None
    new_session: bool = False
    agent_model: object = cache.MISSING
    #: The newest id CLAIMED for this binding since it registered (what the `owed`
    #: query reads as undelivered mail).
    handed_max: str = "0"
    #: The ids this binding's adapter named `handled` on a `turn` frame. Their rows are
    #: gone, and an unbind does not queue them again: a queued id can be handled while
    #: the cursor is still below it.
    handled: set = field(default_factory=set)
    #: Whether the binding's session has a turns endpoint its adapter can deliver
    #: through, as the adapter last reported it on its own connection, and since when
    #: (this daemon's clock). None when the adapter reports no such state (a Codex or
    #: OpenCode host is its own endpoint; a hand-run shim delivers by its own seam). A
    #: bound name whose session has no endpoint reads `waiting` to a sender: its adapter
    #: holds the mail until it finds one.
    endpoint: bool | None = None
    endpoint_since: float | None = None
    #: How many releases have begun for this binding, counted under the lock before a
    #: release reads the queue. A send's gate read notes it as the read begins: a send
    #: that would queue a row after a release that began since reads once more
    #: (`_route_recipients`), so a read made in the record's lag never queues a row
    #: behind the release that read past it.
    releases_begun: int = 0
    #: The ids pushed to the adapter that it hasn't yet taken into a digest (at most
    #: `WINDOW`), and the claimed ids waiting for room, oldest first, with what their
    #: `deliver` frame needs.
    window: set = field(default_factory=set)
    overflow: dict = field(default_factory=dict)
    #: The ids a turn of this binding left unhandled, back to `waiting` until the next
    #: release point. A live send above one waits with them (`_behind_returned`).
    returned: set = field(default_factory=set)
    #: The claim key of the adapter that holds this binding (its claim file's key), as
    #: its `bind` named it. A tool token's row maps to it. None when the bind named none;
    #: such a binding registers no tool token.
    claim_key: str | None = None


@dataclass
class Resolution:
    tag: str
    name: str
    scope: str
    qualified: str
    tier: str
    binding: Binding | None
    resolvable: bool = True
    deferred: bool = False
    hint: str | None = None       # why it is unresolvable, in the sender's terms
    #: (name, scope) is a reserved name, active or kept from an older comms: never a
    #: recipient
    send_only: bool = False


def _config_kept(exc) -> None:
    log.error("%s; keeping the last good settings until the file is fixed", exc)


class Daemon:
    def __init__(self, record, *, db_path=None, socket_path=None, clock=time.time,
                 start_trim_thread: bool = True, history_path=None,
                 first_start=None, kinds=None, settings=None,
                 cache_store=cache.MISSING, serve_tools: bool = False,
                 mcp_port: int | None = None, on_fatal=None):
        self.record = record
        #: The adapter kinds a register may name: the built-in four and each kind the
        #: `moxi.comms.kinds` group declares (`kinds.load`), read once at construction.
        self.kinds = kinds if kinds is not None else kinds_mod.load()
        #: comms' settings, read at use with the last good kept (`config.Config`): a
        #: bad edit to the config file mid-run leaves `block` and the rest as they
        #: were, and the error is logged once per change of the file. `main` starts it
        #: before anything else, so a file that is bad at start refuses the start.
        if settings is None:
            settings = config.Config(report=_config_kept)
            settings.start()
        self.settings = settings
        #: A step `start()` runs after the single-instance probe and before anything
        #: else, given (record, db_path): a daemon on the local backend provisions its
        #: directory here (`provision.first_start`). None elsewhere.
        self.first_start = first_start
        self.db_path = Path(db_path or comms_db())
        self.socket_path = Path(socket_path or daemon_socket())
        self.clock = clock
        #: cache's drift store (`cache.open_store(learn=True)`), opened at `start()`:
        #: the daemon is the learner in practice, and cache writes a lowering before the
        #: read that found it returns. None while not started, and when cache is not
        #: present. A test may hand one in (`cache_store=`), which `start()` keeps and
        #: `stop()` leaves open.
        self.cache_store = None if cache_store is cache.MISSING else cache_store
        self._own_store = cache_store is cache.MISSING
        #: What the drift log lines have said already (a lowering, a held record), so
        #: each is said once.
        self._drift_said: set = set()
        self._drift_lock = threading.Lock()
        self._start_trim_thread = start_trim_thread
        #: How the daemon reads a sender's process ancestry for a send under a reserved
        #: name; a test hands in its own.
        self.proc_view: membership.ProcView = membership.DEFAULT_PROC
        # custody, recorded: the daemon is the only writer, and the only witness
        self.binding_history = bindings.BindingHistory(history_path, clock=clock)
        #: The tool tokens' registry (`mcp.tokens`): comms.db's `mcp_tokens` through the
        #: binding history's connection, and its active rows in memory. Loaded at boot
        #: after the fence. Its evidence reads processes through `proc_view`.
        self.mcp_tokens = mcp_tokens_mod.Registry(self.binding_history, clock=clock,
                                                  proc=lambda: self.proc_view)
        #: Whether this daemon serves comms' tools over MCP now: what `mcp_token`
        #: answers as `served`. The endpoint sets it once it settles; until then False.
        self.mcp_served = False
        #: Whether this daemon serves comms' tools at all when `[mcp] enabled` says so.
        #: `main` passes True. A daemon a test or an embedding caller builds serves none
        #: unless it asks, so it never binds the box's port.
        self.serve_tools = serve_tools
        #: The port in place of `[mcp] port`; a test passes 0 for any free port.
        self._mcp_port = mcp_port
        #: The endpoint's URL while it serves, and why it doesn't when it doesn't
        #: (`disabled`, `supervised`, `port-taken`, `bind-failed`, `failed`): what
        #: `mcp_endpoint` answers. `mcp_held` says whether the supervisor holds the port
        #: (an inherited socket), and `mcp_detail` is the plain line behind either, for
        #: the doctor. `mcp_unit` is the state comms' socket unit read after a failed
        #: bind, None when none was read.
        self.mcp_url: str | None = None
        self.mcp_why: str | None = None
        self.mcp_held = False
        self.mcp_detail: str | None = None
        self.mcp_unit: str | None = None
        #: What takes the endpoint's socket, given the port: `mcp.listen.take` when
        #: None. A test hands in one with its own environment and launchd.
        self.mcp_listen = None
        #: The endpoint (`mcp.server.Endpoint`) once it serves.
        self.mcp_endpoint = None
        #: What adds the tools to the endpoint's server, given the SDK's server; None
        #: serves comms' six tools (`mcp.tools`).
        self.mcp_tools = None
        #: Where the tools' calls run (`mcp.inprocess.Calls`), made with the endpoint.
        self.tool_calls = None
        #: Called once, from the endpoint's loop, when something this daemon can't run
        #: without has failed (the endpoint, after it started); `main` stops the daemon
        #: and exits nonzero. `fatal` says what failed.
        self._on_fatal = on_fatal
        self.fatal: str | None = None

        self._lock = threading.RLock()
        self.bindings: dict[str, Binding] = {}
        #: The newest #names id the ROUTER has taken responsibility for. It is what makes
        #: the catch-up boundary exact now that the boundary's record read happens
        #: OUTSIDE the lock: see `_register`.
        self._seen_max = "0"
        #: The lowest `#names` id the `block` setting kept from routing, or None. The
        #: routed mark stays below it until a rescan routes it, so the next rescan (at
        #: boot, too) still finds it. `_skips` counts every such skip, so a rescan knows
        #: whether the block skipped anything while it ran. Both under `_lock`.
        self._unrouted: int | None = None
        self._skips = 0
        #: Set under `_lock` by `stop()` before anything closes. From then on a register
        #: is answered with a close.
        self._stopping = False
        self._auto_seq = 0
        self.auto_windows: dict[str, list] = {}        # agent-N -> [start, end|None]
        #: The queue: qualified -> {message id: the message's epoch, or None until it is
        #: read}. Every message waiting for a name, offline, cold or flagged alike: the
        #: `waiting` rows of the delivery books, loaded at boot before any method is
        #: answered and written through on every change.
        self.queue: dict[str, dict[str, float | None]] = {}
        #: The delivery books in comms.db (`store.books`): every routed message's row
        #: per recipient, waiting, handed or in a digest. Each change to `queue` above
        #: and each hand-over is written through to it at once; opened at boot.
        self.books = books_mod.Books(self.db_path, clock=clock)
        #: message ids a record line addressed to a send-only identity, logged once
        #: each so a rescan does not say it again. Bounded (`_note_send_only`).
        self.send_only_seen: dict[str, None] = {}
        #: (qualified, message id) of rows whose message the record does not have (or
        #: whose line zero does not parse), logged once each: such a row is skipped at
        #: every release and kept, since only a `handled` report deletes a row.
        self._row_unreadable_logged: set[tuple[str, str]] = set()
        #: One session id, one live binding: session id -> the qualified name whose live
        #: binding holds it.
        self.session_owner: dict[str, str] = {}
        #: Session ids this daemon has seen a prompt or a turn end in. `turned` is never
        #: lowered for one of them for the life of the daemon, so a shim respawned in a
        #: session that has had turns cannot read `fresh`.
        self.turned_ids: set = set()
        #: One release at a time PER NAME, so a name's queued mail goes out in one piece.
        #: Per name, not global: a delivery blocks in `sendall`, and a shim that stops
        #: reading must stall only its own name.
        self._release_locks: dict[str, threading.RLock] = {}
        #: The open settle windows by qualified name: one binding holds a name, so
        #: one window per name. Changed under `_window_cond`, which the window worker
        #: waits on; `_lock` may be held when it is taken, never the other way round. A
        #: window lives in memory only: a restart loses it, and `[held]` rows then wait
        #: for the next turn end.
        self._windows: dict[str, SettleWindow] = {}
        self._window_cond = threading.Condition()
        #: Has the queue been loaded from the record in this daemon's life? The retention
        #: trim keeps every queued message, so it does not run until it has.
        self._queue_loaded = False
        #: message id -> its EFFECTIVE audience, None where it has none. A derived index:
        #: fed at parse by live routing, the rescan and register replay, rebuilt by the
        #: boot rescan, bounded to the rescan window, and never fetched into on demand --
        #: the fill-in reads this and nothing else Three values: a tag; None, known to
        #: carry no audience; or AUDIENCE_INDEX_UNKNOWN. An absent id answers the same as
        #: the sentinel.
        self.audiences: dict[str, str | None | _UnknownAudience] = {}
        #: ask id -> {"asker": qualified, "at": the ask's epoch, "open": set of the
        #: qualified names it still binds}. A
        #: derived index, never authoritative: it only nominates asks for the send verb,
        #: which decides on a fresh read of the record. It records which messages a name
        #: has authored in the record, never what anyone was delivered or read. In memory,
        #: under `_lock`, fed in `_route` alone, and it only moves forward: an entry is
        #: created once and a name that leaves `open` never returns.
        self.asks: dict[str, dict] = {}
        #: The sent index: author qualified
        #: name -> its `#names` messages of the last `OWED_TTL`, ascending by id, and the
        #: same entries by id. Rebuilt at boot from the record (`rebuild_sent`), fed in
        #: `_route` alone after the claims, forward-only, trimmed by age. `sent_floor` is
        #: the epoch the index is complete from: a debt older than it is undecidable.
        self.sent: dict[str, list[SentEntry]] = {}
        self.sent_by_id: dict[str, SentEntry] = {}
        self.sent_floor: float | None = None
        self._sent_feeds = 0
        #: (owing qualified name, message id) -> {"owing", "author", "message_id", "at"}:
        #: debts a shim dropped with their reminder ignored.
        #: It names no standing: it says which message went unanswered, by whom.
        self.unanswered: dict[tuple, dict] = {}
        self.injected_through: dict[str, str] = {}
        #: Message ids withdrawn by their author in this daemon's life (`delete`). A
        #: withdrawn message is never handed again: routing, a release and a push all
        #: check it under `_lock`, and the record's `withdrawn_at` says the same after a
        #: restart.
        self.withdrawn: set[str] = set()
        #: message id -> its line zero's `[expires:...]` Unix second, for every message
        #: this daemon has parsed that has one and may still hold a row. The expiry
        #: sweep's timer waits for the soonest after `_swept_at`, the clock of the last
        #: sweep; a sweep forgets an expired id no binding still holds. Both under
        #: `_expiry_cond`.
        self.expires: dict[str, int] = {}
        self._swept_at = 0.0
        self._expiry_cond = threading.Condition()
        #: The LAST DIGEST TEXT each live identity was shown.
        #: Carried on the `injected` ping and readable back out with
        #: `query kind="injected"`, so the explainer can show the input and not only
        #: the verdict. Keyed and cleared exactly like `injected_through`: it lives
        #: as long as the binding does and no longer. Nothing routes off it.
        self.injected_digest: dict[str, str] = {}
        self.activity: dict[str, float] = {}
        #: Last ping `seq` accepted per SOURCE, where a source is one (connection,
        #: identity) pair -- see `_ping_ordered`. Dropped with the connection.
        self._ping_seq: dict[object, dict[str, int]] = {}
        self.rescans = 0
        self._last_trim = 0.0
        #: (qualified, message id) -> when `_deliver` handed that message to that name's
        #: binding, by this daemon's clock. Memory only, under `_lock`; the trim drops
        #: entries older than `THREAD_IDLE`. It answers `query kind="readable"`.
        self.handed_at: dict[tuple[str, str], float] = {}
        #: When this daemon started: the earliest a message it never handed over can
        #: have become readable since (`query kind="readable"`).
        self.started_at = self.clock()

        #: The daemon socket's server (`wire.Host`), made at `start()`. `moxi.ipc.serve`
        #: holds the lock beside the socket for the daemon's life, so a second daemon
        #: refuses, and removes only the socket it bound.
        self.host: wire.Host | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._sub = None
        #: comms' methods on the daemon socket, by name: the reads any client may call,
        #: `send`, and the adapter's methods on the connection that holds its binding.
        self._methods = {
            "send": self._send,
            "bind": self._bind,
            # The reads take no connection.
            **{name: _read(self, f"_{name}_query") for name in READS},
            "delete": self._delete,
            "open_digest": self._open_digest,
            "turn_ended": self._turn_ended,
            "injected": self._injected,
            "ready": self._ready,
            "look": self._look,
            "activity": self._activity,
            "unanswered": self._note_unanswered,
            "mcp_token": self._mcp_token,
            "mcp_endpoint": _read(self, "_mcp_endpoint_query"),
        }

    # ------------------------------------------------------------------ directory
    @contextlib.contextmanager
    def _db(self):
        """Per-claim directory read -- never a boot-time cache."""
        conn = directory.connect(self.db_path, read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    def _names_channel(self) -> str:
        with self._db() as conn:
            row = directory.room_by_name(conn, NAMES)
            if row is None:
                raise RuntimeError(f"{NAMES} is not provisioned")
            return row["room_id"]

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "Daemon":
        # `[cache] required`: a box that says its cold-send hold must hold refuses to
        # start without the cache module, before anything is served or written.
        refusal = cache.required_refusal(self.settings.use("cache").required)
        if refusal:
            raise RuntimeError(refusal)
        # The socket first, before anything is written or fenced: serving it takes the
        # lock beside it, which is the one question the rest of boot assumes the answer
        # to. A second daemon refuses here. Clients may connect from now on; their hello
        # is answered and their first method waits until boot has loaded the queue.
        self.host = wire.Host(str(self.socket_path), self._call, self._deregister)
        try:
            self.host.start()
        except ipc.AlreadyServing as e:
            self.host = None
            who = f"pid {e.holder}" if e.holder else "another process"
            raise RuntimeError(
                f"a live comms daemon ({who}) is serving at {self.socket_path}; refusing "
                f"to start a second one. Stop the running daemon first.") from None
        except OSError as e:
            self.host = None
            raise RuntimeError(f"the comms daemon can't serve {self.socket_path}: {e}") \
                from None
        try:
            self._boot()
        except BaseException:
            self.host.close()
            self.host = None
            self.books.close()
            raise
        return self

    def _boot(self) -> None:
        self.started_at = self.clock()
        # First-start provisioning, when this daemon has it, runs only now: the lock
        # shows that no other daemon serves the socket, so two daemons starting on one
        # empty home cannot both provision it. It may refuse (RuntimeError).
        if self.first_start is not None:
            self.first_start(self.record, self.db_path)
        # The fence comes after the lock and before any method is answered. The lock
        # proves no other daemon can still own an open interval. Live readers use the
        # binding history only for never-bound checks, while the one-time conversion
        # reads older intervals through its frozen compatibility reader before a new
        # binding can open.
        if self.binding_history.open():
            fenced = self.binding_history.fence()
            if fenced:
                log.warning("boot fence: closed %d custody interval(s) left open by a "
                            "daemon that did not shut down cleanly", fenced)
            # The tool tokens, after the fence cleared their bindings: those whose
            # session has positively ended are revoked here, the rest wait for their
            # adapter's next registration.
            self.mcp_tokens.load()
        # boot: the reserved names, brought in line with what the installed modules
        # declare, which catches a module installed before comms and a store restored
        # from a backup. A failure is logged; the names stay as they were.
        self._reconcile_reserved()
        # boot: the delivery books, fenced BEFORE any method is answered: every row a
        # binding of the last daemon held (handed, or in a digest whose turn never
        # reported) waits again, and every digest it left open ends `fenced`. Then the
        # queue, so no bind or routing pass can hand a queued id as live mail. An open
        # or a read that RAISES stops the start here: routing without the books would
        # hand queued mail as if it were new.
        self.books.open()
        rows, digests = self.books.fence()
        if rows or digests:
            log.warning("boot fence: %d row(s) handed by the last daemon wait again, and "
                        "%d digest(s) it left open are closed", rows, digests)
        self._load_queue()
        # boot: cache's drift store, opened to learn; every lowering is written by it.
        if self._own_store:
            self.cache_store = cache.open_store(learn=True)
        # boot: the sent index, also before any method is answered, so no `owed` query is
        # ever answered from a half-read record.
        self._rebuild_sent_logged()
        self._spawn(self._window_loop)
        self._spawn(self._expiry_loop)
        # boot: the endpoint of comms' tools settles before any method is answered, so
        # an adapter's answered `bind` means the endpoint is up when this daemon serves
        # it, and `mcp_endpoint` never answers from a half-started one.
        self._settle_endpoint()
        self.host.open()

        self._sub = self.record.subscribe()
        self._spawn(self._event_loop)
        self.trim_record()                                 # boot: retention
        try:
            self.rescan()                                  # boot: queue what waits
        except Exception:
            # The rescan queues mail posted to unbound names while no daemon ran. A
            # daemon that cannot do it is still a daemon that can deliver, and refusing
            # to boot would take live routing down with it -- so this is
            # log-and-continue, exactly as the event loop's rescan already is. The
            # routed mark is held where it stands, so the next rescan or the next boot
            # still finds what this one would have queued.
            log.exception("boot rescan failed; the next rescan or boot queues what it "
                          "would have")
            self._hold_routed()
        if self._start_trim_thread:
            self._spawn(self._trim_loop)

    def _settle_endpoint(self) -> None:
        """Serve comms' tools, or settle why not: `[mcp] enabled` and `[mcp] port`, read
        once here. The MCP libraries are imported only now, and only when enabled. The
        endpoint runs on the daemon socket host's loop and pool, and the host's stop hook
        shuts it down before the daemon socket closes.

        The socket is the one the supervisor holds, else the daemon's own on the port
        (`mcp.listen.take`). A token is revoked only on positive evidence that its cause
        holds: every live one is revoked `port-taken` when a listener answers on the port
        and is shown not to be comms' own socket unit, since sessions may have sent their
        tokens to it. A failed bind beside comms' active socket unit (`supervised`: this
        daemon runs outside its unit), a listener that may be that unit, and a bind that
        fails with nothing listening revoke none."""
        self.mcp_served, self.mcp_url, self.mcp_endpoint = False, None, None
        self.mcp_held, self.mcp_detail, self.mcp_unit = False, None, None
        if not self.serve_tools:
            self.mcp_why = "disabled"
            return
        mcp = self.settings.use("mcp")
        if not mcp.enabled:
            self.mcp_why = "disabled"
            log.info("comms' tools aren't served: [mcp] enabled is false")
            return
        port = mcp.port if self._mcp_port is None else self._mcp_port
        try:
            from .mcp import server as mcp_server
            from .mcp import tools as mcp_tools
        except Exception as e:                                   # noqa: BLE001
            self.mcp_why = "failed"
            self.mcp_detail = f"the MCP libraries didn't load: {type(e).__name__}: {e}"
            log.error("comms' tools aren't served: %s", self.mcp_detail)
            return
        from .mcp import listen

        taken = (self.mcp_listen or listen.take)(port)
        self.mcp_detail, self.mcp_unit = taken.detail, taken.unit
        if taken.sock is None:
            self.mcp_why = taken.why
            if taken.why == "port-taken":
                revoked = self.mcp_tokens.revoke_all("port-taken")
                log.error("comms' tools aren't served: %s; %d live tool token(s) revoked",
                          taken.detail, revoked)
            else:
                log.error("comms' tools aren't served: %s", taken.detail)
            return
        sock = taken.sock
        self.mcp_held = taken.inherited
        if taken.inherited and sock.getsockname()[1] != port:
            log.warning("comms' tools are served on the supervisor's port %d, not "
                        "[mcp] port %d; launches get the port served", sock.getsockname()[1],
                        port)
        verifier = mcp_server.Verifier(self.mcp_tokens.verify, self.host.pool,
                                       self.host.opened)
        from .mcp import inprocess

        # Where tool calls run: on the host's pool, a few at a time, so adapters' and
        # the verbs' methods always find threads free. Made before the tools are added.
        self.tool_calls = inprocess.Calls(self.host)
        tools = self.mcp_tools if self.mcp_tools is not None else mcp_tools.adder(self)
        endpoint = mcp_server.Endpoint(sock, verifier, tools=tools,
                                       on_failure=self._endpoint_failed)
        # Set before the start, so a start that runs late is still stopped with the host.
        self.host.stop_hook = endpoint.stop
        try:
            self.host.run_on_loop(endpoint.start(), timeout=wire.START_WAIT)
        except Exception as e:                                   # noqa: BLE001
            self.mcp_why, self.mcp_held = "failed", False
            self.mcp_detail = f"the endpoint didn't start: {e}"
            log.error("comms' tools aren't served: %s", self.mcp_detail)
            return
        self.mcp_endpoint = endpoint
        self.mcp_url = endpoint.url
        self.mcp_why = None
        self.mcp_served = True
        log.info("comms' tools served at %s", endpoint.url)

    def _endpoint_failed(self, why: str) -> None:
        """The endpoint stopped serving after it started (on the host's loop): it isn't
        restarted here. The daemon stops and exits nonzero, and its supervisor starts it
        again, which every adapter sees as a rebind."""
        self.mcp_served, self.mcp_url, self.mcp_why = False, None, why
        self.fatal = f"comms' tools endpoint {why}"
        log.error("%s; the daemon stops so its supervisor can start it again",
                  self.fatal)
        if self._on_fatal is not None:
            self._on_fatal(self.fatal)

    def live_credential(self, claim_key: str, identity: str | None = None):
        """The live binding behind `claim_key`'s active tool token: `(identity,
        credential)`, the identity the token's row maps to and the credential of that
        identity's binding now, its token's hash as hex. The daemon keeps no binding's
        token, only its hash, and accepts the hash in place of the token only from a
        connection in its own process (`_presented`). None when the claim has no active row (or its row maps to another identity
        than `identity`), and when the row's binding isn't live: the daemon restarted and
        the adapter hasn't bound again, or its connection dropped. comms' tools send as
        this pair, in this process, and the post checks it against the binding as it
        checks an adapter's token."""
        rows = [row for row in self.mcp_tokens.active() if row.claim_key == claim_key]
        with self._lock:
            for row in rows:
                if identity is not None and row.identity != identity:
                    continue
                if row.binding_id is None:
                    continue
                b = self.bindings.get(row.identity)
                if (b is not None and b.history_id == row.binding_id
                        and b.claim_key == claim_key and b.token_hash):
                    return b.qualified, b.token_hash.hex()
        return None

    def _mcp_endpoint_query(self, frame: dict) -> dict:
        """`mcp_endpoint`: whether this daemon serves comms' tools, at which URL, and
        why not when it doesn't; `held`, whether the supervisor holds the port it serves
        on, `detail`, the plain line behind the answer, `unit`, the state comms' socket
        unit read after a failed bind (None when none was read), and `revoked`, the live
        bindings whose session's token was revoked, for the doctor. Settled before any
        method is answered."""
        served = bool(self.mcp_served)
        return {"served": served, "url": self.mcp_url if served else None,
                "why": None if served else (self.mcp_why or "disabled"),
                "held": served and self.mcp_held, "detail": self.mcp_detail,
                "unit": None if served else self.mcp_unit,
                "revoked": self._revoked_bindings()}

    def _revoked_bindings(self) -> list[dict]:
        """Each live binding whose claim holds no active tool token and whose newest
        token was revoked: `{identity, why}`, by identity. The claim key a binding's
        `bind` named is kept only in memory, so only this daemon can tell. A binding
        that named no claim, or whose claim never registered a token (a session launched
        without comms' tools), isn't listed."""
        with self._lock:
            live = [(b.qualified, b.claim_key) for b in self.bindings.values()
                    if b.claim_key]
        if not live:
            return []
        whys = self.mcp_tokens.revoked_of(claim for _, claim in live)
        return [{"identity": identity, "why": whys[claim]}
                for identity, claim in sorted(live) if claim in whys]

    def _reconcile_reserved(self) -> None:
        from . import reserved

        try:
            result = reserved.reconcile(target=Path(self.db_path))
        except Exception:                                        # noqa: BLE001
            log.exception("reserved names: the reconcile at start failed")
            return
        for line in result.lines:
            log.info("reserved names: %s", line)

    def _spawn(self, target) -> None:
        t = threading.Thread(target=target, daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self) -> None:
        """Stop: new `bind` and `send` are answered `stopping`, the socket stops
        accepting, and every connection closes without waiting on a peer past the write
        deadline. Each binding's release runs as its connection closes; then the binding
        history and cache's store close. It takes at most the write deadline and the
        releases."""
        with self._lock:
            self._stopping = True
        self._stop.set()
        with self._window_cond:
            self._window_cond.notify_all()
        with self._expiry_cond:
            self._expiry_cond.notify_all()
        if self.host is not None:
            # The endpoint of comms' tools stops first, through the host's stop hook,
            # while the daemon still holds its socket's lock.
            if not self.host.close():
                log.warning("the daemon socket did not close within %.0f s",
                            wire.CLOSE_WAIT)
            self.host = None
        self.mcp_served = False
        if self._sub is not None:
            self.record.unsubscribe(self._sub)
        for t in self._threads:
            t.join(timeout=1.0)
        # after the connections' releases; a write that lands after this is a no-op,
        # and the next boot's fence closes it
        self.binding_history.close()
        self.books.close()
        if self._own_store and self.cache_store is not None:
            cache.close_store(self.cache_store)      # the learning lock goes with it
            self.cache_store = None

    # ------------------------------------------------------------------ the wire
    def _call(self, conn, method: str, params: object):
        """One comms method from `conn`, on a pool thread: its answer, or the
        `RpcError` it is refused with. A refusal names its code; an accident is logged
        and answered `failed`, which a client may try again."""
        handler = self._methods.get(method)
        if handler is None:
            raise ipc.RpcError(wire.METHOD_NOT_FOUND, f"method not found: {method}")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ipc.RpcError(wire.INVALID_PARAMS, f"{method}: params is an object")
        try:
            return handler(conn, params)
        except ipc.RpcError:
            raise
        except DaemonStopping:
            raise wire.error(wire.STOPPING, "the comms daemon is stopping; try again in "
                             "a moment") from None
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) and "busy" not in str(e):
                log.exception("%s failed", method)
                raise wire.error(wire.FAILED, f"{type(e).__name__}: {e}") from None
            # comms.db's busy timeout passed: nothing of this method was committed.
            raise wire.error(wire.STORE_BUSY, "comms' store is busy; try again in a "
                             "moment") from None
        except Refusal as e:
            if method == "bind":
                # One journal line per refused attempt, naming what was asked. A waiting
                # shim at the backoff ceiling adds about two a minute; that is the price
                # of the journal showing the attempt at all.
                log.warning("bind refused: %s: %s", _asked(params), e)
            raise wire.error(e.code, str(e), **e.extra) from None
        except Exception as e:  # never take the daemon down for one bad message
            # An ACCIDENT, not a refusal: a record read that failed, say. Answered
            # `failed`, in the try-again range, so a shim that has held its name keeps
            # retrying instead of reading it as a rival claim.
            log.exception("%s failed", method)
            raise wire.error(wire.FAILED, f"{type(e).__name__}: {e}") from None

    def _own_binding(self, conn, params: dict) -> Binding | None:
        """The binding `conn` holds that `params` speaks for: the one its `identity`
        names, or the connection's one binding when it names none. None when the
        connection holds no such binding."""
        identity = params.get("identity")
        with self._lock:
            if identity:
                b = self.bindings.get(str(identity))
                return b if b is not None and b.conn is conn else None
            mine = [b for b in self.bindings.values() if b.conn is conn]
        return mine[0] if len(mine) == 1 else None

    @staticmethod
    def _not_bound(b: Binding | None) -> None:
        """An adapter's method answers `not-bound` when its connection holds no binding
        it speaks for. A notification's answer is never written, so an adapter that
        notifies sees nothing; what the method keeps from an unbound connection, it has
        kept before this."""
        if b is None:
            raise Refusal("this connection holds no binding", code=wire.NOT_BOUND)
        return None

    def _bind(self, conn, params: dict):
        """`bind`: the binding, answered with its identity, token and catch-up boundary.
        Its catch-up follows the answer: the release of the name's unflagged rows on a
        passing read, then `replay_complete`. Flagged rows wait for a real boundary."""
        result = self._register(conn, params)
        bound = self.bindings[result["identity"]["qualified"]]

        def catch_up() -> None:
            self._on_register(bound)
            self._replay_complete(bound)

        return wire.Then(result, catch_up)

    def _open_digest(self, conn, params: dict) -> dict:
        """`open_digest`: the adapter is about to put `ids` in one digest. The rows it
        may still deliver go in a new digest, whose id the adapter hands turns as the
        delivery's ref; withdrawn and expired ones are dropped and named. The ids leave
        the binding's window, which is topped up."""
        b = self._own_binding(conn, params)
        self._not_bound(b)
        ids = params.get("ids")
        if not isinstance(ids, list):
            raise Refusal("open_digest: `ids` lists the message ids of the digest",
                          code=wire.INVALID_PARAMS)
        asked = {str(i) for i in ids}
        # Only what this binding was handed goes in its digest: a turn end acks the
        # digest's rows, and a row the binding never got is not its to ack.
        with self._lock:
            mine = [mid for mid in dict.fromkeys(str(i) for i in ids)
                    if mid in b.delivered]
        if len(mine) < len(asked):
            log.info("open_digest from %s names %s, which it was not handed; left out",
                     b.qualified, ", ".join(sorted(asked - set(mine), key=_as_id)))
        with self._expiry_cond:
            expires = {mid: self.expires.get(mid) for mid in mine}
        opened = self.books.open_digest(b.qualified, b.history_id, mine, expires=expires)
        if opened.dropped:
            with self._lock:
                rows = self.queue.get(b.qualified, {})
                for gone in opened.dropped:
                    rows.pop(gone["id"], None)
                    b.delivered.discard(gone["id"])
                if not rows:
                    self.queue.pop(b.qualified, None)
        self._window_take(b, lambda mid: mid in asked)
        return {"digest": opened.digest, "ids": opened.ids, "dropped": opened.dropped}

    def _injected(self, conn, params: dict) -> None:
        """`injected`: the adapter put the digest `digest` names in. Its text is kept on
        the digest and for `last_digest`, and its highest id is what `explain` reads as
        injected."""
        b = self._own_binding(conn, params)
        if b is None:
            return self._not_bound(b)
        identity = b.qualified
        if not self._ping_ordered(conn, identity, "injected", params.get("seq")):
            return None
        text = params.get("text")
        if isinstance(text, str) and len(text.encode()) > INJECTED_CAP:
            text = text.encode()[:INJECTED_CAP].decode(errors="ignore")
        try:
            digest = int(params.get("digest"))
        except (TypeError, ValueError):
            digest = None
        found = None
        if digest is not None:
            try:
                found = self.books.digest(digest)
            except Exception:                                      # noqa: BLE001
                log.exception("could not read digest %s of %s", digest, identity)
            if found is not None and found["name"] != identity:
                found = None
        ids = [] if found is None else found["ids"]
        with self._lock:
            if ids:
                self.injected_through[identity] = max(ids, key=_as_id)
            if text is not None:
                self.injected_digest[identity] = str(text)
        if found is not None and text is not None:
            self._book("keep the digest text of", identity,
                       lambda: self.books.injected(identity, digest, str(text)))
        return None

    def _ready(self, conn, params: dict) -> None:
        return self._activity(conn, params, kind=ACTIVITY_READY)

    def _look(self, conn, params: dict) -> None:
        return self._activity(conn, params, kind=ACTIVITY_LOOK)

    def _turn_ended(self, conn, params: dict) -> dict:
        """`turn_ended`, the ack: a turn of the binding's session ended, answering the
        digests `handled` names and not the ones `unhandled` names (both digest ids;
        `turn_id` names the turn, and none is a hand-in that failed before any turn).
        The books delete the handled digests' rows and advance the cursor, and put the
        unhandled ones' rows back to waiting, in one transaction. It is a release point
        with its settle window, and empty lists are the person's own turn ending.
        `answered: true` says the turn ended with the model's answer (`_on_turn`).
        Answered with the name's cursor."""
        b = self._own_binding(conn, params)
        self._not_bound(b)
        lists = {}
        for key in ("handled", "unhandled"):
            got = params.get(key)
            if got is None:
                got = []
            if not isinstance(got, list):
                raise Refusal(f"turn_ended: `{key}` lists digest ids",
                              code=wire.INVALID_PARAMS)
            lists[key] = got
        with self._lock:
            self.activity[b.qualified] = self.clock()
            self._take_session(b, params, at=self.clock(), turn=True)
        ended = self.books.turn_ended(b.qualified, lists["handled"], lists["unhandled"])
        self._on_turn(b, ended, answered=params.get("answered") is True)
        return {"cursor": ended.cursor}

    def _activity(self, conn, params: dict, kind: str | None = None) -> None:
        """`activity` and its kinds: the session reports, from the binding's own
        connection, and the release points (`ready`, `look`, a turn's end)."""
        b = self._own_binding(conn, params)
        identity = b.qualified if b is not None else str(params.get("identity") or "")
        method = kind or "activity"
        if not identity or not self._ping_ordered(conn, identity, method,
                                                  params.get("seq")):
            return self._not_bound(b)
        with self._lock:
            self.activity[identity] = self.clock()
            # The session id rides the notification, and is taken only from the
            # connection that holds this binding: the identity a message names is a
            # claim, the connection is not. So are `projects_root`, `turned` and
            # `source`: a forged message releases nothing.
            if b is not None:
                self._take_session(b, params, at=self.clock())
        if b is None:
            return self._not_bound(b)
        ref = params.get("ref")
        if ref is not None:
            # A turn took this digest: it started, or a steer went in. Not an ack.
            refs = ref if isinstance(ref, list) else [ref]
            self._book("mark the digest started of", identity,
                       lambda: self.books.started(identity, refs))
        if kind == ACTIVITY_READY:
            self._on_ready(b, params.get("source"))
        elif kind == ACTIVITY_LOOK:
            self._on_look(b)
        return None

    # ------------------------------------------------------------------ register
    def _register(self, conn, frame: dict) -> dict:
        adapter = frame.get("adapter") or "claude-shim"
        if adapter not in self.kinds:
            raise Refusal(f"unknown adapter kind {adapter!r}; expected one of "
                          f"{', '.join(self.kinds.names())}", code=wire.UNKNOWN_ADAPTER)
        # The adapter's claim key, which a tool token's row maps to. Optional: an adapter
        # that registers no tool token need not name it.
        claim_key = frame.get("claim")
        if claim_key is not None and not mcp_tokens_mod.valid_claim_key(claim_key):
            raise Refusal("claim: not a claim key", code=wire.INVALID_PARAMS)
        # The catch-up boundary is READ BEFORE THE LOCK. It is a record round trip -- a
        # file read, local or mock -- and taking it under the daemon's ONE lock made every register
        # wait out every other register's network call, and every router, resolver and
        # query wait with them.
        #
        # The read is deferred-raise rather than plain, so the REFUSAL ORDER is unchanged:
        # a name that is already bound is refused with the daemon's own words even
        # when the record is also broken, exactly as when the call sat below those checks.
        hwm, hwm_error = self._high_water_mark()
        with self._lock, self._db() as db:
            if self._stopping:
                # Checked under the lock the freeze took, so a register is either
                # installed before the freeze (and is in the mark) or turned away here.
                raise DaemonStopping()
            name = frame.get("name")
            if not name or name == "auto":
                self._auto_seq += 1                     # monotonic; NEVER reused
                name, scope, tier = f"agent-{self._auto_seq}", GLOBAL, TIER_EPHEMERAL
            else:
                if not isinstance(name, str) or not NAME_RE.match(name):
                    raise Refusal(f"{name!r} is not a valid name", code=wire.INVALID_PARAMS)
                if is_auto_name(name):
                    raise Refusal(
                        f"{name!r} is refused: the agent-N namespace is reserved for the "
                        "auto-namer in every scope", code=wire.NAME_RESERVED, name=name)
                # A register that names no scope binds global. The frame's `project` is
                # the session's project, never its scope.
                scope = GLOBAL if frame.get("global") else (frame.get("scope") or GLOBAL)
                if directory.retired(db, name, scope):
                    raise Refusal(directory.RETIRED_BIND.format(q=qualify(name, scope),
                                                                name=name),
                                  code=wire.NAME_RETIRED, name=qualify(name, scope))
                tier = TIER_STANDING if directory.standing_row(db, name, scope) else TIER_CHOSEN
            q = qualify(name, scope)
            held_for = reserved_mod.lookup(db, q)
            if held_for is not None:
                # No session binds a reserved name, of any adapter kind: one in conflict
                # or kept from an older comms included, so it is still free to reserve
                # once whatever stood in its way has gone.
                raise Refusal(reserved_mod.bind_refusal(held_for), code=wire.NAME_RESERVED,
                              name=q)
            if q in self.bindings:
                held = self.bindings[q]
                raise Refusal(
                    f"{q} is already bound (adapter {held.adapter})", code=wire.NAME_HELD,
                    name=q, holder={"qualified": q, "adapter": held.adapter})
            # The catch-up boundary, FIXED here -- at the same instant the binding becomes
            # visible, and under the same lock. Everything at or below it is replay's
            # business (bounded, possibly dropped); everything above it is live. Without
            # this line a lagging event loop would re-deliver what the bound just omitted.
            #
            # The record's answer above was read before the lock, so it can be stale by
            # the width of the round trip; `_seen_max` is what closes that gap. A message
            # the router handled while this register was in flight was resolved against a
            # table with no binding in it, so live routing will never deliver it -- and if
            # it sat ABOVE the boundary, replay would skip it too and it would wait in
            # `pending` for a rescan. Raising the boundary to include it hands it to
            # replay, which reads history AFTER this point and therefore sees it.
            #
            # That is the whole safety argument, and it is an ordering one: `_seen_max` is
            # raised before the router resolves, and the boundary is fixed in the same
            # critical section that installs the binding. So for any message the router
            # resolved unbound, either its id was already in `_seen_max` when the boundary
            # was fixed (replay owns it), or the boundary was fixed first -- in which case
            # the binding was installed first too, and the router could not have seen it
            # unbound. Exactly one of the two owns every message, as before the hoist.
            if hwm_error is not None:
                raise hwm_error
            hwm = max(hwm, self._seen_max, key=int)
            binding = Binding(conn=conn, qualified=q, name=name, scope=scope, tier=tier,
                              adapter=adapter, bound_at=self.clock(),
                              replay_hwm=hwm, claim_key=claim_key)
            # The session behind the name, from the register itself: this frame is the
            # binding's own connection by construction. With it, the shim's evidence for
            # `fresh`; an id another live binding holds is refused.
            self._take_session(binding, frame, at=binding.bound_at, register=True)
            token = secrets.token_urlsafe(32)
            binding.token_hash = hashlib.sha256(token.encode()).digest()
            self.bindings[q] = binding
            if tier == TIER_EPHEMERAL:
                self.auto_windows[name] = [binding.bound_at, None]
            # custody, recorded at the moment only the daemon sees it. An `agent-N` is
            # a binding like any other and is recorded like any other; a failed write
            # returns None and the binding proceeds regardless (evidence, not product).
            binding.history_id = self.binding_history.bind(
                qualified=q, name=name, scope=scope, tier=tier,
                adapter=adapter, at=binding.bound_at)
            # The name went to this claim: another claim's tool tokens for it are
            # revoked, so a session that lost its name loses its tools with it.
            self.mcp_tokens.name_bound(q, claim_key)
            log.info("bind %s tier=%s adapter=%s", q, tier, adapter)
        # `high`, the catch-up boundary: the shim's registration high-water id. A `re:`
        # below it was sent before that shim could see payments.
        return {"identity": {"name": name, "scope": scope, "tier": tier, "qualified": q},
                "high": hwm, "token": token}

    def _send(self, conn, params: dict) -> dict:
        """`send`: one message, written by the daemon, under the credential `as` and
        `token` present. A bound identity's token is its live binding's; the operator's
        is the operator's credential, which the record checks."""
        who = params.get("as")
        token = params.get("token")
        content = params.get("content")
        if not isinstance(who, str) or not who:
            raise Refusal("send: `as` names the sending identity", code=wire.INVALID_PARAMS)
        if not isinstance(content, str):
            raise Refusal("send: `content` is the message's text", code=wire.INVALID_PARAMS)
        if len(content) > self.record.max_content_length:
            raise Refusal(
                f"content is {len(content)} characters, over the "
                f"{self.record.max_content_length}-character cap on one message",
                code=wire.TOO_LONG)
        self._refuse_unmailable(content)
        with self._lock:
            # Until the operator's name is reserved, an agent may hold a binding under
            # it: a live binding of that name sends as itself, with its binding's token.
            # A send that names the operator's `user` is the operator's own lane, and is
            # checked against the operator's credential whoever holds the name.
            operator = who == OPERATOR_NAME and (params.get("user") is not None
                                                 or who not in self.bindings)
        if operator:
            return self._send_as_operator(params, token, content)
        with self._lock, self._db() as db:
            if self._stopping:
                raise DaemonStopping()
            row = reserved_mod.lookup(db, who)
        if row is not None:
            return self._send_reserved(conn, row, token, content)
        with self._lock, self._db() as db:
            if self._stopping:
                raise DaemonStopping()
            binding = self.bindings.get(who)
            given = _presented(conn, token)
            if binding is None or not hmac.compare_digest(binding.token_hash, given):
                raise Refusal("posting credential refused", code=wire.CREDENTIAL,
                              credential=True)
            if directory.retired(db, binding.name, binding.scope):
                raise Refusal(f"{binding.qualified} is retired", code=wire.NAME_RETIRED,
                              name=binding.qualified)
            qualified = binding.qualified
        message = self.record.post_as_identity(qualified, self._names_channel(), content)
        return {"message_id": str(message.id)}

    def _refuse_unmailable(self, content: str) -> None:
        """Refuse a `send` whose line zero names a name nobody mails: a reserved one,
        active or kept from an older comms. The verbs refuse it first, in their own
        words; this makes it hold for every sender."""
        ml = parse_machine_line(content)
        if ml is None:
            return
        for tag in ml.recipients:
            r = self.resolve(tag)
            if r.resolvable and r.send_only:
                raise Refusal(f"{r.qualified} receives no mail: it is a send-only name",
                              code=wire.NOT_MAILABLE, name=r.qualified)

    def _send_reserved(self, conn, row, token, content: str) -> dict:
        """A `send` under a reserved name: only its owning module's token sends under it,
        and never from inside a session. A name an older comms kept, or one in conflict,
        sends nothing."""
        name = row["name"]
        if row["state"] != reserved_mod.ACTIVE:
            raise Refusal(f"{name} is a reserved name no module holds; nothing sends "
                          "under it", code=wire.NAME_RESERVED, name=name)
        given_raw = (hashlib.sha256(token.encode()).digest()
                     if isinstance(token, str) else None)
        if given_raw is not None:
            with self._lock:
                session_token = any(hmac.compare_digest(b.token_hash, given_raw)
                                    for b in self.bindings.values() if b.token_hash)
            if session_token:
                raise Refusal(f"{name} is reserved for {row['module']}: a session's token "
                              "never sends under it", code=wire.NAME_RESERVED, name=name)
        self._refuse_session_peer(conn, name)
        given = reserved_mod.token_hash(token) if isinstance(token, str) else ""
        if not row["token_hash"] or not hmac.compare_digest(row["token_hash"], given):
            raise Refusal("posting credential refused", code=wire.CREDENTIAL,
                          credential=True)
        message = self.record.post_as_reserved(name, self._names_channel(), content)
        log.info("send %s under %s, reserved for %s", message.id, name, row["module"])
        return {"message_id": str(message.id)}

    def _refuse_session_peer(self, conn, name: str) -> None:
        """Refuse a send under the reserved `name` from any process inside a live session:
        the connection's peer, walked up its ancestry against every live claim's roots.
        A peer the daemon can't place is refused too, never read as outside."""
        peer = getattr(conn, "peer", None)
        pid = getattr(peer, "pid", None)
        ident = getattr(peer, "proc", None)
        unplaced = Refusal(f"comms can't tell whether this process is inside a session, so "
                           f"it sends nothing under {name}", code=wire.NAME_RESERVED,
                           name=name)
        if not pid or ident is None:
            raise unplaced
        from moxi import proc as moxi_proc

        if not moxi_proc.alive(ident):
            raise unplaced
        view = self.proc_view
        try:
            inside = membership.sessions_of(
                pid, claimfile.membership_table(claimfile.live_records(view)), view,
                strict=True)
        except membership.Unplaceable as e:
            log.info("send under %s refused: process %s's ancestry stops at %s, which "
                     "can't be read", name, pid, e.pid)
            raise unplaced from None
        if inside:
            raise Refusal(f"a send under a reserved name from inside a session: {name} "
                          "sends only from a process outside every session",
                          code=wire.NAME_RESERVED, name=name)

    def _delete(self, conn, params: dict) -> dict:
        """`delete`: the author withdraws `message_id` from every recipient that hasn't
        had it in a digest. Refused `not-sender` for anyone but its author,
        `already-delivered` when every recipient has had it, `unknown-message` for an
        id the record doesn't hold. A message withdrawn again answers as the first time
        did. The record keeps the message, marked withdrawn."""
        who = params.get("as")
        token = params.get("token")
        mid = params.get("message_id")
        if not isinstance(who, str) or not who:
            raise Refusal("delete: `as` names the deleting identity",
                          code=wire.INVALID_PARAMS)
        if mid is None or not str(mid).isdigit():
            raise Refusal("delete: `message_id` is a message id",
                          code=wire.INVALID_PARAMS)
        mid = str(mid)
        with self._lock:
            if self._stopping:
                raise DaemonStopping()
            operator = who == OPERATOR_NAME and (params.get("user") is not None
                                                 or who not in self.bindings)
        if operator:
            user = self._operator_user(params)
            if not self.record.operator_token_ok(token if isinstance(token, str)
                                                 else None):
                raise Refusal("the operator's credential was refused",
                              code=wire.CREDENTIAL, credential=True)
            author = (AUTHOR_OPERATOR, user)
        else:
            with self._lock:
                binding = self.bindings.get(who)
                given = _presented(conn, token)
                if binding is None or not hmac.compare_digest(binding.token_hash, given):
                    raise Refusal("posting credential refused", code=wire.CREDENTIAL,
                                  credential=True)
                author = (AUTHOR_IDENTITY, binding.qualified)
        msg = self.record.get_message(mid)
        if msg is None:
            raise _unknown_message(mid)
        if (msg.author_kind, str(msg.author_id)) != author:
            raise Refusal(f"message {mid} is not yours to delete: only its author may "
                          "withdraw it", code=wire.NOT_SENDER, message_id=mid)
        ml = parse_machine_line(msg.content)
        names = []
        if ml is not None:
            for tag in ml.recipients:
                r = self.resolve(tag)
                if r.resolvable and not r.send_only and r.qualified not in names:
                    names.append(r.qualified)
        # Under the lock, so routing (which checks `withdrawn` under it before it
        # queues or hands a row) and this delete see one order. A name with no row
        # that routing hasn't reached yet is withdrawn from too; one routing passed
        # (acked, or at or below its cursor) had it.
        with self._lock:
            through = self.books.routed_through()
            routed = (through is not None and int(mid) <= through
                      and not (self._unrouted is not None and int(mid) >= self._unrouted))
            unrouted = [] if routed else [
                q for q in names if not self._past_cursor(q, mid, {})]
            done = self.books.withdraw(mid, unrouted=unrouted)
            if done.written:
                self.withdrawn.add(mid)
                for q in done.withdrawn:
                    rows_q = self.queue.get(q)
                    if rows_q is not None:
                        rows_q.pop(mid, None)
                        if not rows_q:
                            self.queue.pop(q, None)
                    b = self.bindings.get(q)
                    if b is not None and b.overflow.pop(mid, None) is not None:
                        b.delivered.discard(mid)
        if done.written:
            self._book("mark withdrawn", mid,
                       lambda: self.record.withdraw(mid, books_mod.stamp(self.clock())))
            log.info("delete %s by %s: withdrawn from %s; delivered to %s", mid,
                     author[1], ", ".join(done.withdrawn) or "nobody",
                     ", ".join(done.delivered) or "nobody")
        delivered = [q for q in names if q not in done.withdrawn and q not in done.expired]
        if not done.withdrawn:
            raise Refusal(f"message {mid} has already reached every recipient; nothing was "
                          "withdrawn", code=wire.ALREADY_DELIVERED, message_id=mid,
                          delivered=delivered, expired=list(done.expired))
        return {"message_id": mid, "withdrawn": list(done.withdrawn),
                "delivered": delivered, "expired": list(done.expired)}

    def _operator_user(self, params: dict) -> str:
        """The operator's user id: `user`, or the directory's one operator."""
        user = params.get("user")
        if isinstance(user, str) and user:
            return user
        with self._db() as db:
            rows = db.execute("SELECT user_id FROM operators").fetchall()
        if len(rows) != 1:
            raise Refusal("the operator's lane: `user` names the operator's user id",
                          code=wire.INVALID_PARAMS)
        return rows[0][0]

    def _send_as_operator(self, params: dict, token, content: str) -> dict:
        """The operator's send. `user` names the operator's user id; without it, the
        directory's one operator."""
        with self._lock:
            if self._stopping:
                raise DaemonStopping()
        user = params.get("user")
        if not isinstance(user, str) or not user:
            with self._db() as db:
                rows = db.execute("SELECT user_id FROM operators").fetchall()
            if len(rows) != 1:
                raise Refusal("send as the operator: `user` names the operator's user id",
                              code=wire.INVALID_PARAMS)
            user = rows[0][0]
        try:
            message = self.record.post_as_user(user, self._names_channel(), content,
                                               token=token if isinstance(token, str)
                                               else None)
        except Forbidden as e:
            raise Refusal(f"the operator's credential was refused: {e}",
                          code=wire.CREDENTIAL, credential=True) from None
        return {"message_id": str(message.id)}

    def _high_water_mark(self):
        """(hwm, error): the record's high id for a register's catch-up boundary.

        One read per register, concurrent with every other register's: no register
        waits out another's round trip. A failed read fails the register `transient`, and
        the shim's backoff retries it a moment later. See `_register`.
        """
        try:
            return self.record.max_message_id(self._names_channel()) or "0", None
        except Exception as e:            # re-raised by the caller, in the old call's place
            return "0", e

    def _ping_ordered(self, conn, identity: str, method: str, raw) -> bool:
        """Is this ordered notification (`activity` and its kinds, `injected`) newer
        than the last from its source? `raw` is its `seq`.

        The SOURCE is `(connection, identity)` -- one adapter process's attachment to
        this daemon, reporting on one name. That is the grain the sender numbers at
        (`DaemonClient` holds one counter, and every adapter builds a fresh client on
        every reconnect), and it is the grain that survives the right things: a
        reconnect resets both sides together, and a forged ping on some OTHER
        connection cannot run this identity's counter forward and starve its real
        adapter. What it deliberately does NOT do is authenticate -- the frame's
        identity is still not checked against the connection's binding. This is the
        replay half only.

        A frame with no `seq`, or an unreadable one, is accepted unordered and leaves
        the counter alone. These pings are hints, and dropping a real one costs the same as
        losing it.
        """
        if raw is None:
            return True
        try:
            seq = int(raw)
        except (TypeError, ValueError):
            return True
        with self._lock:
            seen = self._ping_seq.setdefault(conn, {})
            last = seen.get(identity)
            if last is not None and seq <= last:
                log.debug("dropped a stale %s ping for %s: seq %s is not past %s",
                          method, identity, seq, last)
                return False
            seen[identity] = seq
        return True

    def _deregister(self, conn) -> None:
        """A connection closed: each binding it held ends. Its handed mail not yet
        handled is queued again for the name's next binding. A peer that stopped reading
        is dropped by `moxi.ipc` after the write deadline, and ends here the same way."""
        with self._lock:
            # The ping counters die with the connection that fed them: the next
            # process to hold this name comes in on a new connection and starts its
            # count at 1 again, so a kept number would drop its whole first run.
            self._ping_seq.pop(conn, None)
            gone = [b for b in self.bindings.values() if b.conn is conn]
            for b in gone:
                # queued BEFORE the binding disappears from `bindings`: a reader that
                # sees the unbind must already see the mail it left behind
                self._queue_unbound(b)
                if b.session_id and self.session_owner.get(b.session_id) == b.qualified:
                    del self.session_owner[b.session_id]
                self.bindings.pop(b.qualified, None)
                if b.tier == TIER_EPHEMERAL and b.name in self.auto_windows:
                    self.auto_windows[b.name][1] = self.clock()
                self.injected_through.pop(b.qualified, None)
                self.injected_digest.pop(b.qualified, None)
                # Its settle window closes with it.
                self._close_window(b)
                # The unanswered record's third drop: the owing binding ended.
                for key in [k for k in self.unanswered if k[0] == b.qualified]:
                    del self.unanswered[key]
                # the close of the custody interval: the connection IS the liveness
                # signal, so the connection's end is the release, here and nowhere else.
                # Its tool tokens lose the binding in the same transaction.
                self.binding_history.release(b.history_id)
                self.mcp_tokens.released(b.history_id)
                log.info("unbind %s: its connection from pid %s ended (a peer that stops "
                         "reading is dropped after %.0f s)", b.qualified,
                         getattr(getattr(conn, "peer", None), "pid", None),
                         wire.WRITE_DEADLINE)
        if gone:
            self._queue_flush()
            # Each released claim's session may have ended with it: its tool tokens are
            # revoked only on positive evidence.
            claims = {b.claim_key for b in gone if b.claim_key}
            if claims:
                self.mcp_tokens.check_ended(claims)

    # ------------------------------------------------------------------ the tool token
    def _mcp_token(self, conn, params: dict) -> dict:
        """`mcp_token`: register the hash of the session's tool token for the binding
        this connection holds, after its bind. Answers `served`, whether this daemon
        serves comms' tools now. Refused `mcp-token-taken` when another claim's row has
        the hash (or this claim's row for another identity), `mcp-token-revoked` for a
        revoked one, and `name-reserved` for a reserved identity."""
        token_hash = params.get("hash")
        if not mcp_tokens_mod.is_digest(token_hash):
            raise Refusal("hash: the token's SHA-256 as 64 lowercase hex digits",
                          code=wire.INVALID_PARAMS)
        b = self._own_binding(conn, params)
        self._not_bound(b)
        with self._lock:
            # Under the lock a release takes too, so the row never names a binding that
            # was released while this ran.
            if self.bindings.get(b.qualified) is not b:
                self._not_bound(None)
            with self._db() as db:
                if reserved_mod.lookup(db, b.qualified) is not None:
                    raise Refusal(f"{b.qualified} is a reserved name, and a reserved name "
                                  "has no tools", code=wire.NAME_RESERVED,
                                  name=b.qualified)
            if not b.claim_key:
                raise Refusal("this binding's bind named no claim, so it registers no "
                              "token", code=wire.INVALID_PARAMS)
            if b.history_id is None:
                raise wire.error(wire.FAILED, "this binding isn't recorded in comms' "
                                 "store; bind again and register then")
            try:
                self.mcp_tokens.register(token_hash, claim=b.claim_key,
                                         identity=b.qualified, adapter=b.adapter,
                                         binding_id=b.history_id)
            except mcp_tokens_mod.Refused as e:
                raise Refusal(e.reason, code=e.code) from None
            except RuntimeError as e:
                raise wire.error(wire.FAILED, str(e)) from None
        return {"served": bool(self.mcp_served)}

    # ------------------------------------------------------------------ query
    def _agents_query(self, frame: dict) -> dict:
        self._drop_reserved()
        with self._lock:
            # `handed`: the ids handed to the binding and not yet acked (pushed, waiting
            # in its overflow, or in a digest not settled), which `waiting` leaves out.
            bindings = [{"qualified": b.qualified, "name": b.name, "scope": b.scope,
                         "tier": b.tier, "adapter": b.adapter,
                         "endpoint": b.endpoint, "endpoint_since": b.endpoint_since,
                         "handed": len(b.delivered - b.handled)}
                        for b in sorted(self.bindings.values(), key=lambda x: x.qualified)]
            now = self.clock()
            waiting = self._waiting_view(now)
            unanswered = self._unanswered_rows(now)
        # `waiting`: what waits in the queue for each name, one count, since one
        # queue holds offline, cold and flagged mail alike and no reason is stored.
        # `pending` stays in the answer, always empty and read by nothing here: an
        # adapter started before the queue checks for it before it renders a roster.
        # `standing`: the standing names, bound or not, read from comms.db per call.
        return {"bindings": bindings, "waiting": waiting, "pending": {},
                "unanswered": unanswered, "standing": self._standing_view()}

    def _standing_view(self) -> list:
        """The standing names, for `agents`: each directory row (kind `agent`), then
        each reserved name not freed that is active or kept from an older comms (kind
        `send-only`), as {qualified, name, scope, kind, module}. `module` is the module
        a name is reserved for, or declared by when it is in conflict, and None when no
        module owns it. A conflict row isn't reserved, so it is no send-only name. A
        store that can't be read lists none."""
        try:
            with self._db() as db:
                rows = directory.standing_rows(db)
                held = {r["name"]: (r["module"], r["state"]) for r in db.execute(
                    "SELECT name, module, state FROM reserved WHERE freed_at IS NULL")}
        except sqlite3.Error:
            log.exception("agents: the standing names couldn't be read")
            return []
        out = []
        for r in rows:
            q = qualify(r["name"], r["scope"])
            out.append({"qualified": q, "name": r["name"], "scope": r["scope"],
                        "kind": directory.KIND_AGENT, "module": held.get(q, (None,))[0]})
        seen = {e["qualified"] for e in out}
        for q, (module, state) in sorted(held.items()):
            if q in seen or state not in (reserved_mod.ACTIVE, reserved_mod.UNOWNED):
                continue
            name, scope = split_tag(q)
            out.append({"qualified": q, "name": name, "scope": scope or GLOBAL,
                        "kind": reserved_mod.SEND_ONLY, "module": module})
        return out

    def _last_digest_query(self, frame: dict) -> dict:
        """The LAST DIGEST a live identity was shown: what `moxi comms explain` reads to
        show the INPUT and not only the verdict. `identity` is a qualified name, exactly
        as `agents` prints it; nothing is resolved here.

        `digest` is None when nothing has been injected into this binding yet, and the
        whole row is empty once the binding ends: this is runtime state for a LIVE
        identity, not a history. It is never a refusal to ask about a name that is not
        bound: `bound: false` is the answer."""
        identity = str(frame.get("identity") or "")
        with self._lock:
            return {"identity": identity,
                    "bound": identity in self.bindings,
                    "through": self.injected_through.get(identity),
                    "digest": self.injected_digest.get(identity)}

    def _binding_window_query(self, frame: dict) -> dict:
        with self._lock:
            window = self.auto_windows.get(frame.get("name"))
        return {"window": window}

    def _rooms_query(self, frame: dict) -> dict:
        """The rooms the directory names: id, name and kind."""
        with self._db() as db:
            rows = db.execute("SELECT room_id, name, kind FROM rooms ORDER BY name").fetchall()
        return {"rooms": [{"room_id": r[0], "name": r[1], "kind": r[2]} for r in rows]}

    # --- record reads, for callers outside comms ------------------------------------
    def _message_query(self, frame: dict) -> dict:
        mid = str(frame.get("message_id"))
        msg = self.record.get_message(mid)
        if msg is None:
            raise _unknown_message(mid)
        return {"message": msg.to_dict()}

    def _replies_to_query(self, frame: dict) -> dict:
        """The replies one identity can read to one message, by one author.

            -> {"op": "query", "kind": "replies_to", "identity": "claude-app",
                "reply_to": "4611686018427387905", "author": "planner@proj-a"}
            <- {"messages": [{"message_id": "4611686018427387912",
                              "author": {"name": "planner@proj-a", "tier": "chosen",
                                         "attribution": "verified"},
                              "posture": "open", "open_mark": false,
                              "reply_to": "4611686018427387905",
                              "text": "the body, line zero removed",
                              "timestamp": "2026-01-01T12:00:00Z"}]}

        A message is returned only when its id is above `reply_to`, its line zero's
        `re:` is `reply_to`, `author` wrote it (as the sent index and the record's
        attribution both say), and one of its recipient tags names `identity`. Nothing
        else is read from the record: the candidates come from the sent index, which
        holds every `#names` message of the last `OWED_TTL`, and only their own ids are
        fetched. So mail to any other name, the operator's included, is never read here.
        Oldest first, at most `REPLY_QUERY_LIMIT`.

        It is for a caller that waits on a reply its binding may not have been pushed
        (the push window full of other mail): comms' connector provider. A READ: it
        changes no hold, delivery, cursor or ask state. Who may ask: any process that
        can open this socket, as for `attributed_message`, which already answers any one
        message to such a process, so no caller learns more through it.
        """
        identity, author = frame.get("identity"), frame.get("author")
        raw = frame.get("reply_to")
        reply_to = str(raw).strip() if isinstance(raw, (str, int)) else ""
        if not reply_to.isdigit():
            raise Refusal("replies_to: `reply_to` must be a message id")
        for key, value in (("identity", identity), ("author", author)):
            if not isinstance(value, str) or not value.strip():
                raise Refusal(f"replies_to: `{key}` must be a qualified name")
        with self._lock:
            entries = list(self.sent.get(author, ()))
        floor = _as_id(reply_to)
        candidates = [e for e in entries
                      if _as_id(e.id) > floor and str(e.reply_to or "") == reply_to
                      and any(tag_matches(t, identity) for t in e.tags)]
        out = []
        for entry in candidates:
            if len(out) >= REPLY_QUERY_LIMIT:
                break
            msg = self.record.get_message(entry.id)
            ml = None if msg is None else parse_machine_line(msg.content)
            if ml is None or str(ml.reply_to or "") != reply_to:
                continue
            who = self.attribute(msg, live=False)
            if who.qualified != author:
                continue
            out.append({"message_id": str(msg.id),
                        "author": {"name": who.qualified, "tier": who.tier,
                                   "attribution": who.attribution},
                        "posture": ml.posture, "open_mark": bool(ml.open_mark),
                        "reply_to": str(ml.reply_to), "text": ml.text,
                        "timestamp": msg.timestamp})
        return {"messages": out}

    def _history_query(self, frame: dict) -> dict:
        channel_id = frame.get("channel_id") or self._names_channel()
        msgs = self.record.history(str(channel_id), after=frame.get("after"),
                                   limit=frame.get("limit"))
        return {"messages": [m.to_dict() for m in msgs]}

    def _max_id_query(self, frame: dict) -> dict:
        channel_id = frame.get("channel_id")
        return {"message_id": self.record.max_message_id(
            str(channel_id) if channel_id else None)}

    def _session_query(self, frame: dict) -> dict:
        """Which live session sits behind one bound name, and since when.

            -> {"op": "query", "kind": "session", "identity": "planner@proj-a"}
            <- {"ok": true, "identity": "planner@proj-a", "bound": true,
                "adapter": "claude-shim", "session_id": "3d19…", "session_seen_at": …,
                "bound_at": …, "bound_for": 41.2}

        It is NOT resolution: `identity` is a qualified name taken literally, and the
        answer says nothing about where mail goes. `cache_kind` (`warm`, `cold`, `fresh`,
        `no-cache` or `unknown`), `age_s` and `cache_reason` are the daemon's own
        read of the bound session's cache, the one its gate delivers by, with
        `cache_ttl` (the lifetime that read used, None when it used none), `cache_key`
        (what the lifetime was looked up by, in words), `cache_code` (the read's code,
        for a `no-cache` or `unknown`) and `cache_source` (the root a
        codex or opencode binding reported); an unbound name answers them as None. `bound_for` is `bound_at` read against
        this daemon's own clock, so a caller on another clock can still tell a binding
        that has not had its first look or turn event yet from one that never will.
        `endpoint` is whether the bound session has a turns endpoint its adapter delivers
        through (None when its adapter reports no such state), and `endpoint_for` how
        long, on this daemon's clock, it has had it or lacked it. An unbound name answers `bound:
        false` with every other field None; never a refusal.
        """
        identity = str(frame.get("identity") or "")
        now = self.clock()
        with self._lock:
            b = self.bindings.get(identity)
            if b is None:
                return {"identity": identity, "bound": False, "adapter": None,
                        "session_id": None, "session_seen_at": None, "bound_at": None,
                        "bound_for": None, "cache_kind": None, "age_s": None,
                        "cache_reason": None, "cache_ttl": None, "cache_key": None,
                        "cache_code": None, "cache_source": None, "endpoint": None,
                        "endpoint_for": None}
            answer = {"identity": identity, "bound": True, "adapter": b.adapter,
                      "session_id": b.session_id, "session_seen_at": b.session_seen_at,
                      "bound_at": b.bound_at, "bound_for": now - b.bound_at,
                      "cache_source": b.cache_source, "endpoint": b.endpoint,
                      "endpoint_for": (None if b.endpoint_since is None
                                       else now - b.endpoint_since)}
        # The daemon's own read of the bound session's cache, by the function the gate
        # delivers by: the verb's hold and `moxi comms cache` read these.
        clock = self._read(b)
        answer.update(cache_kind=clock.kind, age_s=clock.age_s,
                      cache_reason=clock.reason, cache_ttl=clock.ttl_s,
                      cache_key=clock.key, cache_code=clock.code)
        return answer

    def _owner_query(self, frame: dict) -> dict:
        """Which live binding owns one session id now, and whom that identity awaits.
        `session`'s question asked the other way round.

            -> {"op": "query", "kind": "owner", "session_id": "3d19…"}
            <- {"ok": true, "session_id": "3d19…", "owner": "lead",
                "adapter": "claude-shim", "awaiting": ["planner@proj-a"]}

        `owner` is the qualified name of the live binding `session_owner` maps the id to,
        exactly as `kind="agents"` prints it (a global identity bare). It is null when no
        live binding holds the id: a session this daemon never heard of and one whose
        binding has ended both answer the same, since the daemon keeps nothing for an
        id once its binding lets go.
        `adapter` is that binding's kind, null with no owner. `awaiting` is the
        owner's list as `owed` computes it (`_awaiting`, the same call under the same
        lock), empty with no owner.

        A READ. It changes no hold, pend, delivery or ask state. Asked through `moxi comms
        owner --json` by a program that nudges idle sessions, before it nudges one. An
        empty `session_id` is refused.
        """
        sid = frame.get("session_id")
        if not isinstance(sid, str) or not sid:
            raise Refusal("owner: `session_id` must be a non-empty session id")
        now = self.clock()
        with self._lock:
            owner = self.session_owner.get(sid)
            b = self.bindings.get(owner) if owner is not None else None
            if b is None or b.session_id != sid:
                return {"session_id": sid, "owner": None, "adapter": None,
                        "awaiting": []}
            awaiting = self._awaiting(owner, list(self.sent.get(owner, ())), now)
            return {"session_id": sid, "owner": owner, "adapter": b.adapter,
                    "awaiting": awaiting}

    def _attributed_message_query(self, frame: dict) -> dict:
        """One message from the record, with its author as the daemon attributes it.

            -> {"op": "query", "kind": "attributed_message",
                "message_id": "4611686018427387905"}
            <- {"message_id": "4611686018427387905",
                "room": "#names",
                "author": {"name": "planner@proj-a", "tier": "chosen",
                           "attribution": "verified"},
                "recipients": ["operator"], "resolved": ["operator"],
                "text": "the body, line zero removed",
                "timestamp": "2026-01-01T12:00:00Z"}

        `author` is `attribute(msg, live=False)`: the record and the binding history,
        the function replay delivers by and `moxi comms history` and `moxi comms reply` print. It
        answers for who held the posting seat at the message's own timestamp, never for
        whoever holds it now, so it can never say `verified` where live delivery said
        `labeled`. `attribution` is the wire value (`verified` or `labeled`), as in a
        delivered payload. `recipients` are the tags line zero names, as a delivered
        payload carries them; `resolved` is each tag as the daemon resolves it now,
        by the one rule (null for a tag that does not resolve). A message with
        no machine line has no recipients, and its `text` is the whole body. `room` is the
        room's name; only `#names` is routed.

        An id the record does not hold is refused `unknown-message`, naming the id. An
        id that is not a message id is refused as a bad param.

        A READ. It changes no hold, pend, delivery, cursor or ask state. Who may ask:
        any process that can open this socket, as for `record_message`, `session` and
        `owner`. It answers nothing those reads, `moxi comms history` and `moxi comms reply` do
        not already show any process of this user (the body, its author with tier and
        verified or unverified, its recipients), so no caller learns more through it.
        """
        raw = frame.get("message_id")
        mid = str(raw).strip() if isinstance(raw, (str, int)) else ""
        if not mid.isdigit():
            raise Refusal("attributed_message: `message_id` must be a message id")
        msg = self.record.get_message(mid)
        if msg is None:
            raise _unknown_message(mid)
        author = self.attribute(msg, live=False)
        ml = parse_machine_line(msg.content)
        tags = list(ml.recipients) if ml is not None else []
        resolved = []
        for tag in tags:
            r = self.resolve(tag)
            resolved.append(r.qualified if r.resolvable else None)
        return {"message_id": msg.id,
                "room": self.record.channel_name(msg.channel_id) or msg.channel_id,
                "author": {"name": author.qualified, "tier": author.tier,
                           "attribution": author.attribution},
                "recipients": tags, "resolved": resolved,
                "text": ml.text if ml is not None else msg.content,
                "timestamp": msg.timestamp}

    def _explain_query(self, frame: dict) -> dict:
        """Every live fact behind one identity's classification.

        Five questions decide whether mail landed, and each has one place its ground
        truth lives. Four of those five live in this process's memory and the fifth is a
        file on this box, so the whole table can be answered in one pass under one lock,
        and `moxi comms explain` renders what comes back.

        Three things this deliberately is not:

        * **Not a second opinion.** The verdict comes from `self.classify`, the one
          classifier, over the hand-over times in `handed_at`.
        * **Not a history.** Everything here is runtime state for a LIVE identity apart
          from the cursor, which is the one durable fact. When the binding ends the
          binding facts, the injection and the digest go with it. What survives is the
          cursor and the hand-over times `handed_at` still holds, and that is what makes
          the not-addressable case answerable at all.
        * **Not a ledger.** Nothing is recorded because this was asked. Asking costs two
          cursor read (`moxi comms explain` is not on any hot path). The one write it
          can cause is the books catching up with a reservation another process made:
          mail queued for the name before it was reserved is dropped first, as the next
          release would drop it, so the answer says `reserved` rather than `waiting`.

        `identity` is a qualified name, taken literally, exactly as `kind="agents"`
        prints it. `message_id` names the message to explain; without one the daemon
        picks the newest it can see for this identity, which is what a person wants when
        they ask why an agent has gone quiet.
        """
        identity = str(frame.get("identity") or "")
        asked = frame.get("message_id")
        self._drop_reserved(identity)
        now = self.clock()
        with self._lock:
            b = self.bindings.get(identity)
            binding = None if b is None else {
                "name": b.name, "scope": b.scope, "tier": b.tier, "adapter": b.adapter,
                "bound_for": now - b.bound_at,
                "handed": len(b.delivered)}
            # Everything this daemon has seen delivered to the name: what the live
            # binding was handed, plus every hand-over `handed_at` still holds. The two
            # differ after an unbind, and that gap is the whole not-addressable case.
            seen = set(b.delivered) if b is not None else set()
            handed = {mid: at for (q, mid), at in self.handed_at.items() if q == identity}
            seen |= set(handed)
            waiting = sorted(self.queue.get(identity, {}), key=_as_id)
            through = self.injected_through.get(identity)
            digest = self.injected_digest.get(identity)
            activity = self.activity.get(identity)
        # With no id asked, the newest message this daemon handed the name or holds
        # queued for it.
        known = seen | set(waiting)
        message_id = str(asked) if asked is not None else (
            max(known, key=_as_id) if known else None)
        handed_at = handed.get(message_id)
        delivery = None if message_id is None else {
            "message_id": message_id,
            # The trim drops a hand-over past `THREAD_IDLE`, so a message it has dropped
            # is still explainable and its delivery age is not. Said, not guessed at.
            "delivered_for": None if handed_at is None else now - handed_at,
            "asked_for": asked is not None}
        try:
            row = self.books.cursor_row(identity) if self.books.is_open else None
        except Exception:                                          # noqa: BLE001
            log.exception("explain: could not read the cursor of %s", identity)
            row = None
        cursor = books_mod.explain_cursor(row, message_id)
        case = case_error = None
        dropped = None
        if message_id is not None and self.books.is_open:
            try:
                dropped = self.books.dropped_reason(identity, message_id)
            except (TypeError, ValueError):
                dropped = None
            except Exception:                                      # noqa: BLE001
                log.exception("explain: could not read why %s left %s's queue",
                              message_id, identity)
        if dropped is not None:
            case = dropped
        elif message_id is not None and message_id in waiting:
            # Queued BEFORE the cursor: a queued id is not `cursor-behind-it`, whose
            # sentence says a turn that would advance it has not completed -- not why
            # it waits.
            case = CASE_WAITING
            cursor = {**cursor, "reason": books_mod.WAITING,
                      "sentence": books_mod.REASONS[books_mod.WAITING]}
        elif message_id is not None:
            try:
                case = self.classify(identity, message_id)
            except (TypeError, ValueError) as e:
                # A cursor that is not a message id reaches `classify`'s `int()` and
                # raises. `cursor["reason"]` already names that, so the command has
                # something true to print; what it must not do is answer the whole
                # query with a traceback.
                case_error = f"{type(e).__name__}: {e}"
        return {"identity": identity,
                "bound": b is not None,
                "binding": binding,
                "delivery": delivery,
                "deliveries": len(seen),
                "waiting": waiting,
                "injected_through": through,
                "digest": digest,
                "activity_for": None if activity is None else now - activity,
                "cursor": cursor,
                "case": case,
                "case_error": case_error}

    def _resolve_query(self, frame: dict) -> dict:
        """Tier/route RESOLUTION for the send verb -- never a liveness check."""
        # The tag alone decides: the frame's `scope` and `project`, if an older verb
        # sends them, are not read.
        tag = frame.get("name") or ""
        r = self.resolve(tag)
        if not r.resolvable:
            raise Refusal(r.hint or (
                f"{tag}: no such ephemeral identity. agent-N names are never reused and a "
                "retired one is nobody -- there is nothing to deliver to."),
                code=wire.NAME_RETIRED if r.hint else wire.NAME_UNKNOWN, name=r.qualified)
        bound = r.binding is not None
        ever = True if bound else self.binding_history.ever_bound(r.name, r.scope)
        answer = {"tier": r.tier, "route": NAMES, "qualified": r.qualified,
                  "bound": bound, "deferred": r.deferred,
                  "send_only": r.send_only,
                  # the directory fact, read at the SAME (name, scope) the send resolved
                  # to -- not the live binding's tier, which can predate a row being added
                  "standing_row": self.has_standing_row(r.name, r.scope),
                  # has this exact identity ever been bound (the binding history)? None
                  # when the binding history cannot say; the send's never-bound check reads it
                  "ever_bound": ever}
        if ever is False:
            # The identities of this name that were bound, for that refusal to name:
            # never the one asked about, never a retired one. A hint; it resolves
            # nothing.
            answer["seen_as"] = self._seen_as(r.name, r.scope)
        return answer

    def _seen_as(self, name: str, scope: str) -> list:
        """The other identities of `name` the binding history has seen bound, most recent first,
        leaving out retired and send-only ones."""
        scopes = [sc for sc in self.binding_history.known_scopes(name) if sc != scope]
        if not scopes:
            return []
        with self._db() as db:
            return [qualify(name, sc) for sc in scopes
                    if not directory.retired(db, name, sc)
                    and not reserved_mod.unmailable(db, qualify(name, sc))]

    def has_standing_row(self, name: str, scope: str) -> bool:
        """Is there an active standing row at this exact (name, scope)? Read per call."""
        with self._db() as db:
            return directory.standing_row(db, name, scope) is not None

    def resolve(self, tag: str) -> Resolution:
        """The identity `tag` names, by the one rule (`scoping.identity_of`): no scope
        of anyone's is read. Then whether it is bound, its tier, and whether it is
        send-only or retired."""
        with self._lock, self._db() as db:
            r = self._resolve(db, tag)
            # The row decides, bound or not: a reserved name is never a recipient.
            if r.resolvable and reserved_mod.unmailable(db, r.qualified):
                r.send_only = True
            return r

    def _resolve(self, db, tag: str) -> Resolution:
        """`resolve`'s body, under the lock with the directory open."""
        name, scope = split_tag(tag)
        if is_auto_name(name):
            b = self.bindings.get(name)      # auto names are global and unscoped
            return Resolution(tag, name, GLOBAL, name, TIER_EPHEMERAL, b,
                              resolvable=b is not None)
        scope = scope or GLOBAL              # a bare name is the global identity
        q = qualify(name, scope)
        if directory.retired(db, name, scope):
            # Nobody: no reader routes, holds or queues for it, and the verbs refuse it.
            return Resolution(tag, name, scope, q, TIER_CHOSEN, None, resolvable=False,
                              hint=directory.RETIRED_SEND.format(q=q, name=name))
        b = self.bindings.get(q)
        tier = TIER_STANDING if directory.standing_row(db, name, scope) else TIER_CHOSEN
        return Resolution(tag, name, scope, q, tier, b, deferred=b is None)

    # ------------------------------------------------------------------ record events
    def _event_loop(self) -> None:
        while not self._stop.is_set():
            event = self._sub.next_event(timeout=0.2)
            if event is None:
                continue
            try:
                if event["t"] == "MESSAGE_CREATE":
                    self._route(Message.from_dict(event["d"]), backlog=False)
                elif event["t"] == "READY" and not event.get("resumed"):
                    # fresh IDENTIFY: events in the gap are gone from the socket. The
                    # record still has them -- re-derive rather than continue blind.
                    log.warning("record reconnected without RESUME; rescanning %s", NAMES)
                    self.rescan()
            except Exception:
                log.exception("event handling failed")

    def rescan(self) -> None:
        """Route the record again: every message above the routed mark, however many,
        and the newest `RESCAN_LIMIT` for the indexes. Mail to an unbound name gets a
        row, and mail a bound name has not been handed is routed as live. With no
        routed mark (a home that has routed nothing since its migration) the pass is
        the newest `RESCAN_LIMIT`, gated by each name's cursor alone."""
        channel_id = self._names_channel()
        msgs = self.record.history(channel_id, limit=constants.RESCAN_LIMIT)
        with self._lock:
            self.rescans += 1
            skips = self._skips
        memo: dict = {}   # one cursor read per name for THIS pass only
        # The ids routed before this pass began have their rows, or had them until they
        # were acked: the pass routes only what is above them.
        try:
            through = self.books.routed_through() if self.books.is_open else None
        except Exception:                                          # noqa: BLE001
            log.exception("rescan: could not read the routed mark; every message is "
                          "routed by its cursor alone")
            through = None
        memo[_ROUTED_MEMO] = through
        if (through is not None and len(msgs) >= constants.RESCAN_LIMIT
                and _as_id(msgs[0].id) > through + 1):
            # The newest window does not reach down to the mark: everything above the
            # mark is read, so no message the daemon never routed is left behind.
            msgs = self.record.history(channel_id, after=str(through))
        try:
            for m in msgs:
                try:
                    self._route(m, backlog=True, cursor_memo=memo)
                except Exception:
                    # One poison message must not cost the rest of the pass. The live
                    # event loop already absorbs a bad message this way; a rescan
                    # re-reads the same record and has the same reason to.
                    log.exception("rescan: routing message %s failed",
                                  getattr(m, "id", "?"))
            # The pass routed what the block had kept back, unless the block skipped
            # more while it ran: the mark may rise past those ids now.
            last = _as_id(msgs[-1].id) if msgs else -1
            with self._lock:
                cleared = self._skips == skips and self._unrouted is not None
                if cleared:
                    self._unrouted = None
            if cleared and last > 0:
                self._raise_routed(last)
        finally:
            self._end_pass(memo)

    def _route_recipients(self, msg, ml, author: Author, memo, opens, backlog) -> None:
        """The per-recipient half of `_route`: resolve, index the ask, queue or claim,
        and deliver. Split out so `_route` can feed the sent index after it.

        The rules every reader of the record keeps (this, a rescan, and replay): an id
        with a row for the name is handed only by a release point, never here; a
        flagged message, or one to a name that is unbound or whose cache does not pass,
        becomes a row, never a delivery; and no row is written for an id at or below
        the name's cursor. A row that exists is never re-created or dropped here."""
        opening = None          # decided at the first recipient: does this pass create it?
        through = memo.get(_ROUTED_MEMO) if backlog else None
        routed = through is not None and _as_id(msg.id) <= through
        for position, tag in enumerate(ml.recipients):
            r = self.resolve(tag)
            if opens or ml.reply_to is not None:
                opening = self._index_ask(msg, ml, author, r, opens=opens, opening=opening)
            if routed:
                # Routed before this pass: its rows are in the books, or it was acked.
                continue
            q = r.qualified
            gone = self._gone(msg.id, ml, msg)
            if gone is not None and not r.send_only and r.resolvable and (
                    r.binding is not None or r.tier in (TIER_CHOSEN, TIER_STANDING)):
                # Withdrawn, or past its expiry: no name gets it, and `explain` says why.
                self._book("drop at routing for", q,
                           lambda q=q, gone=gone: self.books.drop_gone(q, [msg.id], gone))
                continue
            if r.send_only:
                # A record line that names a send-only identity (typed by hand, or from
                # an older client): delivered to nobody, queued for nobody. The verbs
                # refuse such a send; this is the record's side.
                self._note_send_only(q, msg.id)
                continue
            if r.binding is None:
                # An unbound chosen or standing name: the message waits in its queue,
                # unless the name already handled it before it unbound (past its
                # cursor). A dead agent-N is inert, not misdelivered. A binding made
                # since the resolve takes it as live mail: its bind's catch-up may
                # already have read the rows.
                if not (r.resolvable and r.tier in (TIER_CHOSEN, TIER_STANDING)):
                    continue
                with self._lock:
                    late = self.bindings.get(q)
                    if late is None and not self._past_cursor(q, msg.id, memo):
                        self._queue_add(q, msg.id, _epoch(msg.timestamp))
                if late is None:
                    continue
                r.binding = late
            if backlog:
                # one guarded reading of "handled?" for bound and unbound alike
                if self._past_cursor(q, msg.id, memo):
                    continue
            with self._lock:
                if msg.id in self.queue.get(q, {}) or msg.id in r.binding.delivered:
                    # A row waits for a release point; a handed id is not handed again.
                    continue
            flagged = _flagged(ml, q)
            clock = now = begun = None
            for attempt in range(2):
                if not flagged:
                    clock, now, begun = self._gate_read(r.binding, memo, fresh=attempt > 0)
                with self._lock:
                    passes = not flagged and _passes(ml, position, clock, now)
                    if (not passes and not flagged and attempt == 0
                            and r.binding.releases_begun != begun):
                        # A release of the name began after this read did, and the row
                        # would be queued behind it: the read may have met the record's
                        # lag that release read past. One more read decides.
                        continue
                    if not passes:
                        if not flagged:
                            log.info("queued msg=%s for %s: cache %s at delivery", msg.id,
                                     q, self._describe(clock))
                        if backlog or not self._past_cursor(q, msg.id, memo):
                            self._queue_add(q, msg.id, _epoch(msg.timestamp))
                        live = False
                    elif not backlog and self._behind_returned(r.binding, msg.id):
                        # Rows a turn of this binding left unhandled wait below this
                        # one: it waits with them for the next release point, and goes
                        # out after them by id.
                        self._queue_add(q, msg.id, _epoch(msg.timestamp))
                        live = False
                    else:
                        live = self._claim(r.binding, msg.id)
                break
            if not live:
                continue
            # Live delivery: no queued mail goes with it. A queued message goes out only
            # at a release point of its own name, never because this one went.
            with self._name_lock(q):
                self._deliver(r.binding, msg, ml, author, backlog=backlog)

    # ------------------------------------------------------------------ routing
    def _cursor(self, qualified: str) -> str | None:
        """The name's cursor in the books; None when it has none, or the books are
        closed or can't be read (no cursor degrades toward delivery, never loss)."""
        if not self.books.is_open:
            return None
        try:
            return self.books.cursor(qualified)
        except Exception:                                          # noqa: BLE001
            log.exception("could not read the cursor of %s; treating it as unset",
                          qualified)
            return None

    def _past_cursor(self, qualified: str, message_id: str, memo: dict) -> bool:
        """Has this identity's cursor already passed this message? The cursor moves
        only at an ack, in the books; `memo` is scoped to a single routing pass so a
        rescan does not read it once per message."""
        if qualified not in memo:
            memo[qualified] = self._cursor(qualified)
        cursor = memo[qualified]
        if cursor is None:
            return False
        return _as_id(message_id) <= _as_id(cursor)

    def _route(self, msg, *, backlog: bool, cursor_memo: dict | None = None) -> None:
        if msg.channel_id != self._names_channel():
            return          # only #names routes
        # Before the resolve, always: a register whose boundary is fixed after this point
        # must count this message as one the router has already taken responsibility for,
        # whether or not it turns out to be routable (a non-routable id can only raise the
        # boundary to a value the record itself would have answered with).
        #
        # The conversion is guarded because it now runs for EVERY #names message, ahead of
        # the machine-line parse: an id that is not a number is not one `max_message_id`
        # could ever answer with, so it can never be needed as a boundary -- and reading
        # the boundary must not be the thing that takes down a message the router would
        # otherwise have shrugged off.
        try:
            seen = int(msg.id)
        except (TypeError, ValueError):
            seen = None
        if seen is not None:
            with self._lock:
                if seen > int(self._seen_max):
                    self._seen_max = msg.id
        ml = parse_machine_line(msg.content)
        if ml is None:
            return                                    # no machine line at position zero
        self._index_audience(msg, ml)                 # at parse, before any claim
        self._note_message(msg, ml)
        if self.settings.use("block"):
            # symmetric: a blocked identity is neither delivered to nor routed from.
            # The setting is a bare bool, read at use, so it blocks all routing. The
            # message is not routed yet: the routed mark stays below it.
            log.info("block on: not routing message %s", msg.id)
            if seen is not None:
                with self._lock:
                    self._skips += 1
                    if self._unrouted is None or seen < self._unrouted:
                        self._unrouted = seen
            return
        author = self.attribute(msg, live=not backlog)
        memo = {} if cursor_memo is None else cursor_memo
        # The ask index: whether this message can open an ask is a per-message fact; the
        # index is fed per resolved recipient, right after the resolve and before any of
        # the loop's `continue`s, so every recipient is seen.
        opens = ml.posture == POSTURE_ONCE and author.attribution == attribution.VERIFIED
        try:
            self._route_recipients(msg, ml, author, memo, opens, backlog)
            if seen is not None:
                self._raise_routed(seen)
        finally:
            # The sent index is fed AFTER the recipients are claimed (the claim that
            # hands a message to a recipient's binding precedes the lock section that
            # feeds the index), so no `owed` query can see an answer in the
            # index that is not yet counted as handed. In `finally`: a recipient that
            # raised costs that recipient, never the index's view of the record.
            self._index_sent(msg, ml, author)
            if cursor_memo is None:
                # A live message is a pass of its own; a rescan ends its pass itself.
                self._end_pass(memo)

    def _hold_routed(self) -> None:
        """Keep the routed mark where it stands until a rescan completes: what lies
        above it was not routed."""
        try:
            through = self.books.routed_through() if self.books.is_open else None
        except Exception:                                          # noqa: BLE001
            log.exception("could not read the routed mark to hold it")
            return
        if through is None:
            return
        with self._lock:
            self._skips += 1
            if self._unrouted is None or through + 1 < self._unrouted:
                self._unrouted = through + 1

    def _raise_routed(self, through: int) -> None:
        """Raise the routed mark to `through`, but never to or past a message the block
        kept from routing: a rescan must still find that one."""
        with self._lock:
            hold = self._unrouted
        if hold is not None:
            through = min(through, hold - 1)
        if through > 0 and self.books.is_open:
            self._book("raise the routed mark for", str(through),
                       lambda: self.books.routed(through))

    def _index_ask(self, msg, ml, author: Author, r: Resolution, *, opens: bool,
                   opening: bool | None) -> bool | None:
        """Feed the ask index from one resolved recipient of one routed message.

        **Open.** A verified part-1 `[reply-once]` message (`opens`) gets an entry the
        first time any pass reaches it, and that pass alone adds each resolved recipient
        other than the asker to its `open` set. `opening` carries that decision from the
        first recipient to the rest; a re-parse (a rescan, a boot race) finds the entry
        held and changes nothing.

        **Close.** A message with `re:N`, where N is held and this recipient is N's asker,
        takes its author out of N's `open`. Attribution is not asked for: closing only
        relaxes, and the verb re-checks on the record anyway.

        Returns `opening` for the next recipient. Under the lock, because live routing
        and the boot rescan route concurrently.
        """
        with self._lock:
            if opens:
                if opening is None:
                    opening = msg.id not in self.asks
                    if opening:
                        self.asks[msg.id] = {"asker": author.qualified,
                                             "at": _epoch(msg.timestamp), "open": set()}
                        self._trim_asks()
                entry = self.asks.get(msg.id) if opening else None
                if (entry is not None and r.resolvable and not r.send_only
                        and r.qualified != author.qualified):
                    entry["open"].add(r.qualified)
            if ml.reply_to is not None:
                referent = self.asks.get(ml.reply_to)
                if referent is not None and r.qualified == referent["asker"]:
                    referent["open"].discard(author.qualified)
        return opening

    def _trim_asks(self) -> None:
        """Keep the newest `RESCAN_LIMIT` asks by id, cut with slack. Under the
        lock. Asks are sparse, so a live daemon may hold asks older than the rescan
        window; a restart rebuilds fewer, and a missing entry lets a send through."""
        if len(self.asks) > constants.RESCAN_LIMIT + ASK_INDEX_SLACK:
            keep = sorted(self.asks, key=_as_id)[-constants.RESCAN_LIMIT:]
            self.asks = {k: self.asks[k] for k in keep}

    def _asks_query(self, frame: dict) -> dict:
        """The asks this daemon holds open to `sender` from any of `recipients`.

            -> {"op": "query", "kind": "asks", "sender": "reviewer@proj-a",
                "recipients": ["planner@proj-a"]}
            <- {"ok": true, "asks": ["4611686018427999001"]}

        Held entries whose asker is among `recipients` (qualified names), whose `open`
        holds `sender`, whose `at` is within `ASK_TTL` of this daemon's clock, and which
        this daemon is not still holding back from `sender` (a queued ask waits in
        `sender`'s queue until a release point hands it, and an ask binds only once it
        has been handed over); newest first, at most `ASK_QUERY_LIMIT`. A NOMINATION: the verb
        reads each one back from the record and decides. Leaving an id out only relaxes.
        Nothing is resolved here.
        """
        sender = str(frame.get("sender") or "")
        recipients = {str(q) for q in (frame.get("recipients") or ())}
        now = self.clock()
        with self._lock:
            b = self.bindings.get(sender)
            withheld = {mid for mid in self.queue.get(sender, {})
                        if b is None or mid not in b.delivered}
            ids = [mid for mid, e in self.asks.items()
                   if e["asker"] in recipients and sender in e["open"]
                   and now - e["at"] <= constants.ASK_TTL and mid not in withheld]
        ids.sort(key=_as_id, reverse=True)
        return {"asks": ids[:constants.ASK_QUERY_LIMIT]}

    # ------------------------------------------------------------------ the sent index
    def _index_sent(self, msg, ml, author: Author) -> None:
        """Feed one parsed `#names` message into the sent index, once.

        Forward-only: an id already held adds nothing, so a rescan or a replay that
        re-parses a message changes nothing. A message older than `OWED_TTL` by this
        daemon's clock is not added (nothing can owe on it, and trimming would take it
        out again). Attribution is not required: an entry only
        relaxes a debt, except as an ask, which the query checks `verified` for.
        """
        epoch = _epoch(msg.timestamp)
        with self._lock:
            if msg.id in self.sent_by_id:
                return
            if self.clock() - epoch > constants.OWED_TTL:
                return
            entry = SentEntry(id=msg.id, author=author.qualified,
                              tags=tuple(ml.recipients), posture=ml.posture,
                              reply_to=ml.reply_to, at=epoch,
                              verified=author.attribution == attribution.VERIFIED)
            entries = self.sent.setdefault(author.qualified, [])
            if entries and _as_id(entries[-1].id) > _as_id(msg.id):
                bisect.insort(entries, entry, key=lambda e: _as_id(e.id))
            else:
                entries.append(entry)
            self.sent_by_id[msg.id] = entry
            self._sent_feeds += 1
            if self._sent_feeds % SENT_TRIM_EVERY == 0:
                self._trim_sent()
            # The unanswered record's first drop: the owing name's payment landed.
            for key, row in list(self.unanswered.items()):
                if self._pays(entry, row["owing"], row["author"], row["message_id"]):
                    del self.unanswered[key]

    def _pays(self, entry: SentEntry, owing: str, author: str, message_id: str) -> bool:
        """Is `entry` `owing`'s payment of `author`'s message `message_id`? Its
        own message to the author, after the message, not asking back with `once`."""
        if entry.author != owing or entry.posture == POSTURE_ONCE:
            return False
        if _as_id(entry.id) <= _as_id(message_id):
            return False
        return any(tag_matches(t, author) for t in entry.tags)

    def _note_unanswered(self, conn, frame: dict) -> None:
        identity = str(frame.get("identity") or "")
        message_id = str(frame.get("message_id") or "")
        author = str(frame.get("author") or "")
        if not (identity and message_id.isdigit() and author):
            return
        try:
            at = float(frame.get("at"))
        except (TypeError, ValueError):
            at = self.clock()
        with self._lock:
            b = self.bindings.get(identity)
            if b is None or b.conn is not conn:
                log.info("dropped an unanswered notify for %s from a connection that does "
                         "not hold it", identity)
                return self._not_bound(None)
            if any(self._pays(e, identity, author, message_id)
                   for e in self.sent.get(identity, ())):
                return          # paid already, between the shim's query and this notify
            self.unanswered[(identity, message_id)] = {
                "owing": identity, "author": author, "message_id": message_id, "at": at}
        log.info("unanswered: %s ended a turn without answering %s's msg %s", identity,
                 author, message_id)

    def _unanswered_rows(self, now: float) -> list[dict]:
        """The record as `moxi comms agents` reads it, oldest first, after the second drop
: a message past `OWED_TTL` leaves. Under the lock."""
        for key in [k for k, row in self.unanswered.items()
                    if now - row["at"] > constants.OWED_TTL]:
            del self.unanswered[key]
        rows = sorted(self.unanswered.values(), key=lambda r: _as_id(r["message_id"]))
        return [{"owing": r["owing"], "author": r["author"],
                 "message_id": r["message_id"], "for": now - r["at"]} for r in rows]

    def _trim_sent(self) -> None:
        """Drop entries older than `OWED_TTL`. Under the lock."""
        cutoff = self.clock() - constants.OWED_TTL
        for author in list(self.sent):
            entries = self.sent[author]
            keep = [e for e in entries if e.at >= cutoff]
            for e in entries:
                if e.at < cutoff:
                    self.sent_by_id.pop(e.id, None)
            if keep:
                self.sent[author] = keep
            else:
                del self.sent[author]

    def _rebuild_sent_logged(self) -> None:
        try:
            count = self.rebuild_sent()
            if count:
                log.info("boot: %d message(s) of the last day read into the sent index",
                         count)
        except Exception:
            # The index starts empty and its floor is now: every debt older than boot is
            # undecidable and lets its Stop through.
            with self._lock:
                self.sent_floor = self.clock()
            log.exception("boot read of the sent index failed; debts older than this boot "
                          "are undecidable and never block a Stop")

    def rebuild_sent(self) -> int:
        """Boot: one read of every `#names` message of the last `OWED_TTL`, unbounded,
        through the record's `since`. The floor is that window's start.
        """
        now = self.clock()
        since = self.record.since
        floor = now - constants.OWED_TTL
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(floor))
        count = 0
        for m in since(self._names_channel(), stamp):
            ml = parse_machine_line(m.content)
            if ml is None:
                continue
            author = self.attribute(m, live=False)
            self._index_sent(m, ml, author)
            count += 1
        with self._lock:
            self.sent_floor = floor
        return count

    def _owed_query(self, frame: dict) -> dict:
        """What the shim needs to settle its ledger at a Stop.

            -> {"op": "query", "kind": "owed", "identity": "reviewer@proj-a",
                "authors": {"lead": "4611…600"}, "referents": ["4611…580"]}
            <- {"ok": true, "sent": {"lead": [{"id", "posture"}]},
                "referents": {"4611…580": {"own": true, "posture": "open"} | null},
                "awaiting": ["worker@proj-a"], "undelivered": false, "floor": 1759…}

        `sent`: for each author, `identity`'s entries addressed to it after that id, tags matched literally in `identity`'s scope.
        `referents`: for each id, whether it is `identity`'s own message and its posture,
        or null when the index does not hold it. `awaiting`: the bound names `identity`
        has a verified part-1 `[reply-once]` ask to, within `ASK_TTL`, that have not yet
        answered it (a message threaded to the ask, or the first message after it not
        marked `[no reply]`). `undelivered`: whether a message was claimed for
        `identity`'s binding above its `injected_through`, counting only claims since it
        registered. `awaiting` and `undelivered` are read under one lock. The query names
        no standing; the shim decides everything else.
        """
        identity = str(frame.get("identity") or "")
        raw = frame.get("authors") or {}
        if not isinstance(raw, dict):
            raise Refusal("owed: `authors` must map each author to its oldest unpaid id")
        authors = {str(a): str(i) for a, i in raw.items()}
        referents = [str(r) for r in (frame.get("referents") or ())]
        now = self.clock()
        with self._lock:
            mine = list(self.sent.get(identity, ()))
            sent = {}
            for author, after in authors.items():
                sent[author] = [
                    {"id": e.id, "posture": e.posture}
                    for e in mine if _as_id(e.id) > _as_id(after)
                    and any(tag_matches(t, author) for t in e.tags)]
            refs = {}
            for rid in referents:
                e = self.sent_by_id.get(rid)
                refs[rid] = None if e is None else {"own": e.author == identity,
                                                     "posture": e.posture}
            awaiting = self._awaiting(identity, mine, now)
            b = self.bindings.get(identity)
            through = self.injected_through.get(identity) or "0"
            undelivered = b is not None and _as_id(b.handed_max) > _as_id(through)
            floor = self.sent_floor
        return {"sent": sent, "referents": refs, "awaiting": awaiting,
                "undelivered": undelivered, "floor": floor}

    def _readable_query(self, frame: dict) -> dict:
        """When `identity` could first read each of `ids`, as far as delivery knows.

            -> {"op": "query", "kind": "readable", "identity": "reviewer@proj-a",
                "ids": ["4611…580", "4611…600"]}
            <- {"ok": true, "since": <started_at>, "handed": {"4611…580": <epoch>},
                "waiting": ["4611…600"]}

        `handed`: the asked ids this daemon handed to `identity`'s binding, with when.
        `waiting`: the asked ids that wait in `identity`'s queue. `since`: when
        this daemon started. The send verb reads these to tell when a thread went idle
        for the replier; the daemon decides nothing from them. Under the lock.
        """
        identity = str(frame.get("identity") or "")
        ids = [str(i) for i in (frame.get("ids") or ())]
        with self._lock:
            rows = self.queue.get(identity, {})
            handed = {mid: self.handed_at[(identity, mid)] for mid in ids
                      if (identity, mid) in self.handed_at}
            waiting = [mid for mid in ids if mid in rows]
            since = self.started_at
        return {"since": since, "handed": handed, "waiting": waiting}

    def _trim_handed(self) -> None:
        """Drop hand-over times older than `THREAD_IDLE`: a thread idle that long since
        a hand-over is idle by its post time too, which is older still."""
        now = self.clock()
        with self._lock:
            for key in [k for k, at in self.handed_at.items()
                        if now - at >= constants.THREAD_IDLE]:
                del self.handed_at[key]

    def _awaiting(self, identity: str, mine, now: float) -> list[str]:
        """The names `identity` awaits. Under the lock.

        An ask is `identity`'s own `[reply-once]` entry, verified, within `ASK_TTL`;
        nothing else is. For each of its tags, the live binding the tag
        names (by the one rule) is awaited until it answers: its entry to
        `identity` threaded to the ask, of any posture, or its first entry to `identity`
        after the ask not marked `[no reply]`. A name that is not bound is not awaited.
        """
        names: list[str] = []
        for ask in mine:
            if (ask.posture != POSTURE_ONCE or not ask.verified
                    or now - ask.at > constants.ASK_TTL):
                continue
            for tag in ask.tags:
                for b in self.bindings.values():
                    q = b.qualified
                    if q == identity or q in names or not tag_matches(tag, q):
                        continue
                    if not self._answered(ask, q, identity):
                        names.append(q)
        return sorted(names)

    def _answered(self, ask: SentEntry, answerer: str, asker: str) -> bool:
        """Has `answerer` answered `ask` (by `asker`) in the index? Under the lock."""
        for e in self.sent.get(answerer, ()):
            if _as_id(e.id) <= _as_id(ask.id):
                continue
            if not any(tag_matches(t, asker) for t in e.tags):
                continue
            if e.reply_to == ask.id or e.posture != POSTURE_NONE:
                return True
        return False

    def _queue_unbound(self, b: Binding) -> None:
        """At unbind, the release: every row the binding held, handed or in a digest,
        waits again for the name's next binding, and its open digests end. An `agent-N`
        gets no deferred mail: its rows go with its binding, since a later session
        numbered the same is someone else. Called under the lock."""
        if b.tier == TIER_EPHEMERAL:
            dropped = sorted(self.queue.get(b.qualified, {}), key=_as_id)
            if dropped:
                self.queue.pop(b.qualified, None)
            self._book("release the rows of", b.qualified,
                       lambda: self.books.release(b.qualified, ephemeral=True))
            return
        released: list = []

        def release() -> None:
            released.extend(self.books.release(b.qualified))

        self._book("release the rows of", b.qualified, release)
        # And what this binding was handed with no row (the ids its adapter said it held
        # at bind) and never handled: it waits again too.
        back = set(released) | {mid for mid in b.delivered if mid not in b.handled}
        # A withdrawn or expired message doesn't wait again: its row is dropped.
        gone = {}
        for mid in back:
            why = self._gone(mid)
            if why is not None:
                gone.setdefault(why, []).append(mid)
        for why, ids in gone.items():
            back -= set(ids)
            self._book("drop rows of", b.qualified,
                       lambda why=why, ids=ids: self.books.drop_gone(b.qualified, ids, why))
        rows = self.queue.setdefault(b.qualified, {})
        memo: dict = {}
        # A row that waited before this binding took it waits again whatever the
        # cursor says (a held row can sit below an acked one); a live hand-over the
        # cursor has passed was handled.
        past = [mid for mid in back if mid not in rows
                and self._past_cursor(b.qualified, mid, memo)]
        if past:
            self._book("drop rows of", b.qualified,
                       lambda: self.books.drop(b.qualified, past))
        added = sorted((mid for mid in back - set(past) if mid not in rows), key=_as_id)
        for mid in added:
            rows[mid] = None
            if mid not in released:
                self._book("queue a row for", b.qualified,
                           lambda mid=mid: self.books.add(b.qualified, mid))
        if not rows:
            self.queue.pop(b.qualified, None)
        if added:
            log.info("unbind %s: %d handed message(s) not yet handled are queued again",
                     b.qualified, len(added))

    def _claim(self, binding: Binding, message_id: str) -> bool:
        """Atomically claim a message for a binding. Live routing, catch-up and a release
        race by construction; the claim is what makes at-least-once not mean
        twice-in-one-breath."""
        with self._lock:
            if message_id in binding.delivered or message_id in self.withdrawn:
                return False
            binding.delivered.add(message_id)
            binding.returned.discard(message_id)
            if _as_id(message_id) > _as_id(binding.handed_max):
                binding.handed_max = message_id      # the `owed` query's `undelivered`
            # Its row, handed to this binding: made for a live send, taken from
            # `waiting` for a release. The row stays until the name acks it.
            self._book("hand a row of", binding.qualified,
                       lambda: self.books.hand(binding.qualified, message_id,
                                               binding.history_id))
            return True

    # ------------------------------------------------------------------ gone mail
    def _note_message(self, msg, ml) -> None:
        """What a parsed message says about whether it may still go: its expiry, kept
        for the sweep's timer, and the record's withdrawn mark."""
        if getattr(msg, "withdrawn_at", None):
            with self._lock:
                self.withdrawn.add(str(msg.id))
        if ml is None or ml.expires_at is None:
            return
        with self._expiry_cond:
            mid = str(msg.id)
            if mid in self.expires:
                return
            soonest = self._next_expiry()
            self.expires[mid] = ml.expires_at
            if soonest is None or ml.expires_at < soonest:
                self._expiry_cond.notify_all()

    def _next_expiry(self) -> int | None:
        """The soonest expiry the last sweep did not pass. Under `_expiry_cond`."""
        return min((at for at in self.expires.values() if at > self._swept_at),
                   default=None)

    def _gone(self, message_id, ml=None, msg=None, *, expires_at=None) -> str | None:
        """`withdrawn` or `expired` for a message that may no longer be handed to a
        model, by this daemon's clock; None while it may. `ml` and `msg` are the parsed
        message when the caller has it; `expires_at` an expiry it already holds."""
        mid = str(message_id)
        if mid in self.withdrawn or (msg is not None and getattr(msg, "withdrawn_at", None)):
            return books_mod.WITHDRAWN
        if ml is not None and ml.expires_at is not None:
            expires_at = ml.expires_at
        if expires_at is None:
            with self._expiry_cond:
                expires_at = self.expires.get(mid)
        if expires_at is not None and expires_at <= self.clock():
            return books_mod.EXPIRED
        return None

    def _drop_rows(self, qualified: str, ids, reason: str) -> None:
        """Rows that may no longer go to `qualified`: out of the queue and the books,
        with why."""
        ids = [str(i) for i in ids]
        with self._lock:
            rows = self.queue.get(qualified, {})
            for mid in ids:
                rows.pop(mid, None)
            if not rows:
                self.queue.pop(qualified, None)
        self._book("drop rows of", qualified,
                   lambda: self.books.drop_gone(qualified, ids, reason))
        log.info("dropped %s for %s: %s", ", ".join(ids), qualified, reason)

    def sweep_expired(self) -> int:
        """The expiry sweep: every waiting row past its message's expiry is dropped, in
        the books and in memory. Run by the timer for the soonest expiry and at every
        release; returns how many rows went."""
        now = self.clock()
        with self._expiry_cond:
            due = {mid: at for mid, at in self.expires.items() if at <= now}
        if not self.books.is_open:
            return 0
        try:
            gone = self.books.sweep(now, due)
        except Exception:                                          # noqa: BLE001
            log.exception("the expiry sweep failed; the next one tries again")
            return 0
        with self._lock:
            for q, mid in gone:
                rows = self.queue.get(q)
                if rows is not None:
                    rows.pop(mid, None)
                    if not rows:
                        self.queue.pop(q, None)
            # An expired id a binding still holds (handed, or in a digest) is kept: its
            # next move (`open_digest`, a release) checks it. The rest are forgotten.
            held = {mid for rows in self.queue.values() for mid in rows}
            for b in self.bindings.values():
                held |= b.delivered
        with self._expiry_cond:
            self._swept_at = max(self._swept_at, now)
            for mid in due:
                if mid not in held:
                    self.expires.pop(mid, None)
        if gone:
            log.info("expiry sweep: dropped %d row(s) past their expiry: %s", len(gone),
                     ", ".join(f"{mid} for {q}" for q, mid in gone[:10]))
        return len(gone)

    def _drop_reserved(self, name: str | None = None) -> int:
        """Mail that waited for a name before a module reserved it: dropped, in the
        books and in memory, with reason `reserved` (`Books.drop_reserved`). The daemon
        does it, as the books' one writer, at boot, at each release and when `agents` or
        `explain` asks, so a reserve made by another process (the doctor, an install)
        takes effect at the next of those. `name` limits it to one name. Never raises;
        returns how many rows went."""
        if not self.books.is_open:
            return 0
        try:
            gone = self.books.drop_reserved(name)
        except Exception:                                          # noqa: BLE001
            log.exception("dropping mail queued for a reserved name failed; the next "
                          "check tries again")
            return 0
        if not gone:
            return 0
        with self._lock:
            for q, mid in gone:
                rows = self.queue.get(q)
                if rows is not None:
                    rows.pop(mid, None)
                    if not rows:
                        self.queue.pop(q, None)
        log.info("dropped %d row(s) queued before their name was reserved: %s", len(gone),
                 ", ".join(f"{mid} for {q}" for q, mid in gone[:10]))
        return len(gone)

    def _expiry_loop(self) -> None:
        """One timer, set for the soonest expiry this daemon knows of, never a poll:
        it sleeps until then (or until a sooner one is noted) and sweeps."""
        while not self._stop.is_set():
            with self._expiry_cond:
                soonest = self._next_expiry()
                wait = None if soonest is None else soonest - self.clock()
                if wait is None or wait > 0:
                    # A clock that moves by hand (a test's) is re-read at least once a
                    # minute; the real clock wakes at the expiry.
                    self._expiry_cond.wait(None if wait is None else min(wait, 60.0))
                    continue
            self.sweep_expired()

    # ------------------------------------------------------------------ the queue
    def _book(self, what: str, qualified: str, write) -> None:
        """One write to the delivery books. Never raises: a write that fails is logged,
        and memory stays the authority for this daemon's life. The row it would have
        changed is wrong in the store until the next change to it, and the next boot's
        fence and rescan read the store as it is."""
        try:
            write()
        except Exception:                                          # noqa: BLE001
            log.exception("could not %s %s in the delivery books; this daemon keeps "
                          "going on what it holds in memory", what, qualified)

    def _queue_add(self, qualified: str, message_id: str, epoch: float | None) -> None:
        """One row: `message_id` waits for `qualified`. A row that exists is left as it
        is. An id the name's live binding was handed gets none (a reader that raced the
        hand-over). An `agent-N` gets a row only while it is bound. Under the lock; the
        row is written through to the books at once (a handed row waits again)."""
        rows = self.queue.get(qualified)
        if rows is not None and message_id in rows:
            return
        if message_id in self.withdrawn:
            return                          # its author withdrew it
        b = self.bindings.get(qualified)
        if b is not None and message_id in b.delivered:
            return
        if b is None and is_auto_name(split_tag(qualified)[0]):
            return
        self.queue.setdefault(qualified, {})[message_id] = epoch
        self._book("queue a row for", qualified,
                   lambda: self.books.add(qualified, message_id))

    def _queue_drop(self, qualified: str, ids) -> None:
        """Delete rows. Only an ack deletes a row of a name that can still be handed
        it; the other callers drop rows nobody can take (an `agent-N`'s at its unbind or
        at boot, a send-only name's). Under the lock."""
        ids = list(ids)
        rows = self.queue.get(qualified, {})
        for mid in ids:
            rows.pop(mid, None)
        if not rows:
            self.queue.pop(qualified, None)
        self._book("drop rows of", qualified, lambda: self.books.drop(qualified, ids))

    def _queue_flush(self) -> None:
        """Fill in the epoch of any row added without one. Never raises. The rows
        themselves are written as they change (`_queue_add`, `_queue_drop`)."""
        with self._lock:
            unknown = [(q, mid) for q, rows in self.queue.items()
                       for mid, at in rows.items() if at is None]
        for q, mid in unknown:
            try:
                msg = self.record.get_message(mid)
            except Exception:                                      # noqa: BLE001
                continue
            if msg is None:
                continue
            self._note_message(msg, parse_machine_line(msg.content))
            with self._lock:
                rows = self.queue.get(q)
                if rows is not None and rows.get(mid, 0) is None:
                    rows[mid] = _epoch(msg.timestamp)

    def _waiting_view(self, now: float) -> dict:
        """`agents`' `waiting`: qualified -> {"count", "oldest"}, the oldest row's message
        time (epoch seconds; a row whose time is not read yet counts as now). Under the
        lock."""
        view = {}
        for q, rows in sorted(self.queue.items()):
            if not rows:
                continue
            times = [at for at in rows.values() if at is not None]
            view[q] = {"count": len(rows), "oldest": min(times) if times else now}
        return view

    def queued_ids(self) -> set:
        """Every id with a row for any name, in any state: what the retention trim
        keeps."""
        with self._lock:
            ids = {mid for rows in self.queue.values() for mid in rows}
        # A stopped daemon's books are closed; it trims with the queue it loaded.
        return ids | self.books.held_ids() if self.books.is_open else ids

    # ------------------------------------------------------------------ send-only lines
    def _note_send_only(self, qualified: str, message_id: str) -> None:
        """One log line per message id that names a send-only identity on its machine
        line; a re-read of the same line (a rescan, the boot read) says nothing."""
        with self._lock:
            if message_id in self.send_only_seen:
                return
            self.send_only_seen[message_id] = None
            while len(self.send_only_seen) > 4096:
                self.send_only_seen.pop(next(iter(self.send_only_seen)))
        log.info("msg=%s names %s, a send-only identity: delivered to nobody, held for "
                 "nobody", message_id, qualified)

    # ------------------------------------------------------------------ the cache read
    def _take_session(self, b: Binding, frame: dict, *, at: float, turn: bool = False,
                      register: bool = False) -> None:
        """A frame from `b`'s own connection: its session id, `projects_root` and
        `turned`. Called under the lock.

        One session id, one live binding: an id another live binding holds is refused
        and logged, and `b` keeps the id it had (a new binding has none, so it reads
        `unknown` and is held until the old connection closes). A new id starts `turned`
        false; the frame's `turned` and a `kind: turn` raise it. `turned` is never
        lowered for a session id this daemon has seen turn, so a respawned shim, which
        starts `turned` false, cannot make a session that has had turns read `fresh`. A
        frame with neither field leaves `projects_root` as it was.
        """
        sid = frame.get("session_id")
        if isinstance(sid, str) and sid and sid != b.session_id:
            owner = self.session_owner.get(sid)
            holder = self.bindings.get(owner) if owner is not None else None
            if holder is not None and holder is not b and holder.session_id == sid:
                log.warning("session id %s reported by %s is held by the live binding "
                            "%s; refused (one session id, one binding), %s keeps %s",
                            sid, b.qualified, owner, b.qualified, b.session_id or "none")
            else:
                if b.session_id and self.session_owner.get(b.session_id) == b.qualified:
                    del self.session_owner[b.session_id]
                b.session_id = sid
                b.turned = False
                self.session_owner[sid] = b.qualified
        if isinstance(sid, str) and sid and sid == b.session_id:
            b.session_seen_at = at
            if frame.get("turned") is True:
                b.turned = True
        root = frame.get("projects_root")
        if isinstance(root, str) and root:
            b.projects_root = root
        source = frame.get("cache_source")
        if isinstance(source, str) and source:
            b.cache_source = source
        if "agent_model" in frame:
            # A wrapper that re-read its config and could not name the default agent
            # sends null: the report it made before may be stale, so it is withdrawn.
            reported = frame.get("agent_model")
            b.agent_model = dict(reported) if isinstance(reported, dict) else cache.MISSING
        endpoint = frame.get("endpoint")
        if isinstance(endpoint, bool) and endpoint is not b.endpoint:
            b.endpoint, b.endpoint_since = endpoint, at
        if register:
            b.new_session = frame.get("new_session") is True
        if b.session_id:
            b.new_session = False
        if turn:
            b.turned = True
        if b.session_id is not None:
            if b.session_id in self.turned_ids:
                b.turned = True
            elif b.turned:
                self.turned_ids.add(b.session_id)

    def _read(self, binding: Binding, *, quiet: bool = False):
        """The daemon's read of one binding's cache: `cache.clock_state`, comms' one
        seam to cache, on the binding's kind and session (the id, root and `turned` its
        adapter reported), with the daemon's drift store. It passes on `warm`, `fresh`
        and `no-cache`, for every adapter alike. An exception anywhere in here reads
        `unknown`, with one log line; it never falls through to a delivery on its own.
        `quiet` (a settle window's reads) leaves out the read's journal notes, which the
        read at the window's frame logged already."""
        try:
            with self._lock:
                adapter, sid = binding.adapter, binding.session_id
                root, turned = binding.projects_root, binding.turned
                source, new_session = binding.cache_source, binding.new_session
                agent_model, bound_at = binding.agent_model, binding.bound_at
            clock = cache.clock_state(adapter, sid, projects_root=root, turned=turned,
                                      name=binding.qualified, cache_source=source,
                                      new_session=new_session, agent_model=agent_model,
                                      bound_at=bound_at, store=self.cache_store,
                                      kinds=self.kinds)
            self._drift_lines(binding, clock, quiet=quiet)
        except Exception as e:                                     # noqa: BLE001
            log.exception("the cache read failed for %s; it reads unknown",
                          getattr(binding, "qualified", "?"))
            clock = cache.Clock(cache.UNKNOWN,
                                reason=f"the daemon's check failed ({type(e).__name__})",
                                code=cache.READ_FAILED)
        return clock

    def _read_memo(self, binding: Binding, memo: dict | None):
        """(the read, the daemon's clock at it), read once per binding per routing pass
        and kept in `memo` for the rest of it."""
        clock, now, _ = self._gate_read(binding, memo)
        return clock, now

    def _gate_read(self, binding: Binding, memo: dict | None, *, fresh: bool = False):
        """(the read, the daemon's clock at it, the binding's `releases_begun` as the read
        began), read once per binding per routing pass and kept in `memo` for the rest
        of it. `fresh` reads again and keeps the new read."""
        cache_of = memo.setdefault(_GATE_MEMO, {}) if memo is not None else None
        if cache_of is not None and not fresh:
            kept = cache_of.get(binding.qualified)
            if kept is not None and kept[0] is binding:
                return kept[1], kept[2], kept[3]
        with self._lock:
            begun = binding.releases_begun
        clock = self._read(binding)
        now = self.clock()
        if cache_of is not None:
            cache_of[binding.qualified] = (binding, clock, now, begun)
        return clock, now, begun

    @staticmethod
    def _describe(clock) -> str:
        """For the journal and the log, with the hold's one meaning of a cold
        duration: `cold for 12m (last request 42m ago)` for a read with an age and a
        lifetime, `cold (<reason>; last request 3m ago)` for one without a lifetime, and
        `unreadable (<reason>)`."""
        if clock is not None and clock.kind == cache.COLD:
            age, ttl = clock.age_s, getattr(clock, "ttl_s", None)
            if age is not None and ttl is not None:
                return f"cold for {cache.span(age - ttl)} (last request {cache.span(age)} ago)"
            reason = clock.reason or "no cache lifetime to count it by"
            if age is not None:
                return f"cold ({reason}; last request {cache.span(age)} ago)"
            return f"cold ({reason})"
        reason = getattr(clock, "reason", None) or "no reason given"
        return f"unreadable ({reason})"

    # ------------------------------------------------------------------ the drift record
    def _drift_lines(self, binding: Binding, clock, *, quiet: bool = False) -> None:
        """comms' words about a Codex read's drift results: the journal notes (not on a
        `quiet` read), a lowering cache's store wrote (said once per model and
        lifetime), and a measured lifetime held because the drift record cannot be read
        or accepted (said once per reason). cache owns the record and decides every
        verdict; this only logs."""
        for note in () if quiet else clock.notes or ():
            log.info("%s", note)
        lowered = clock.lowering
        if lowered and clock.kind != cache.UNKNOWN and getattr(
                self.cache_store, "learning", False):
            key = ("lowering", lowered.get("harness"), lowered.get("provider"),
                   lowered.get("model"), lowered.get("lifetime"))
            with self._drift_lock:
                fresh = key not in self._drift_said
                self._drift_said.add(key)
            if fresh:
                self._say(self._lowering_text(binding, lowered))
        if clock.code == cache.DRIFT_HELD and clock.reason:
            key = ("held", clock.reason)
            with self._drift_lock:
                fresh = key not in self._drift_said
                self._drift_said.add(key)
            if fresh:
                where = getattr(self.cache_store, "path", None)
                self._say(f"cache clock: {clock.reason}"
                          + (f" ({where})" if where else "")
                          + ". Every codex read on a measured cache lifetime is held until "
                          "it is fixed or the record is removed; removing it restores the "
                          "lifetimes the table ships.")

    @staticmethod
    def _lowering_text(binding: Binding, lowered: dict) -> str:
        at = lowered.get("at")
        when = time.strftime("%H:%M", time.localtime(at)) if isinstance(at, (int, float)) \
            else "?"
        cached, inp = lowered.get("cached"), lowered.get("input")
        tokens = (f"cached {cached:,} of {inp:,} tokens" if isinstance(cached, int)
                  and isinstance(inp, int) else "cached tokens not known")
        return (f"cache clock: {lowered['model']} on {lowered['harness']} now counts "
                f"{cache.span(lowered['lifetime'])}, down from {cache.span(lowered['from'])}: "
                f"{binding.qualified}'s request at {when} (thread {lowered.get('thread')}) "
                f"found its cache cold {cache.span(lowered.get('gap') or 0)} after the one "
                f"before ({tokens}). Raising it takes a new probe or the operator's word, "
                "written as a change to the lifetime table.")

    def _say(self, text: str) -> None:
        """One daemon log line; never raises."""
        log.warning("%s", text)

    # ------------------------------------------------------------------ release points
    def _on_ready(self, b: Binding, source) -> None:
        """`activity kind=ready` from the binding's own connection. `startup`, `clear`
        and `compact` start a context with nothing cached to lose, so they release every
        row without a read. Any other source (`resume`, `fork`, a value this daemon does
        not know) releases every row only on a passing read. A `ready` with no `source`
        comes from an adapter started before the queue, and releases nothing."""
        if not isinstance(source, str) or not source:
            return
        if source not in SOURCES_UNREAD and not self._passing(b):
            return
        self._release(b, flagged=True)

    def _on_turn(self, b: Binding, ended, *, answered: bool = False) -> None:
        """A `turn_ended` from the binding's own connection, after the books took the
        ack (`ended`: the message ids acked and the ids back to waiting). A release point
        that stays open for a settle window.

        `answered`: the turn ended with the model's answer, so it made a request just
        now and the cache is warm whatever the harness's record says yet. The daemon
        reads no cache for it: when rows wait it releases every one at once, however
        long the record takes to show the turn. It still opens the window, so a send
        whose gate read meets that lag and queues goes at the window's first passing
        read.

        Otherwise (an interrupted or failed turn, or a turn end an adapter can't vouch
        for), its read passing, it releases every row at once and opens no window. Its
        read not passing, it opens the binding's window (`RELEASE_SETTLE_STEPS`): the
        harness may not have written the turn yet, and a send can queue a row a moment
        later in the same lag. The window releases every row then queued at its first
        read that passes, except the ids this turn left unhandled.

        A newer turn end of the binding closes the window before its own read; a
        `ready`, `look` or bind leaves it open. The acked ids leave memory before the
        release; the unhandled ones wait again after it, so not this release but the
        next hands them."""
        opened = time.monotonic()
        self._close_window(b)
        named = set(ended.handled) | set(ended.unhandled)
        if named:
            self._window_take(b, lambda mid: mid in named)
        self._settle(b, handled=ended.handled)
        if answered:
            if self._rows_wait(b):
                self._release(b, flagged=True)
            self._open_window(b, opened, ended.unhandled)
        elif self._passing(b):
            self._release(b, flagged=True)
        else:
            self._open_window(b, opened, ended.unhandled)
        self._settle(b, unhandled=ended.unhandled)

    def _on_look(self, b: Binding) -> None:
        """`activity kind=look` from the binding's own connection: its adapter found the
        session's endpoint with the session idle. It releases every row on a passing
        read, as a turn end does, and settles nothing: a `ready` the session raised before
        its adapter could watch loses no mail. It takes one read and leaves an open
        settle window as it is: a `look` that meets the record's lag does not cut the
        window short."""
        if self._passing(b):
            self._release(b, flagged=True)

    def _on_register(self, b: Binding) -> None:
        """After a register's replay: the rows of unflagged messages, on a passing read.
        A bind is how offline mail reaches a name. Flagged rows wait for a real
        boundary: a reconnect is not a turn. A register opens no settle window."""
        self._queue_flush()
        with self._lock:
            if not self.queue.get(b.qualified):
                return
        if self._passing(b):
            self._release(b, flagged=False)

    def _passing(self, b: Binding) -> bool:
        """A release point's read: it passes or it does not. The send's cold-at is never
        read here: a row already waits, and it waits for a release point that reads a
        passing cache or reads none. A read that does not pass is logged only when rows
        wait for the name: with none, nothing waits on it."""
        clock = self._read(b)
        if clock.kind in cache.PASSING:
            return True
        if self._rows_wait(b):
            log.info("release for %s waits: its cache reads %s", b.qualified,
                     self._describe(clock))
        return False

    def _rows_wait(self, b: Binding) -> bool:
        """Whether any row waits for `b`'s name now that `b` was not handed already."""
        with self._lock:
            return any(mid not in b.delivered for mid in self.queue.get(b.qualified, {}))

    def _behind_returned(self, binding: Binding, message_id: str) -> bool:
        """Whether a row this binding's turns left unhandled still waits below
        `message_id`. A live send behind one gets a row instead of a push, so it never
        overtakes mail already handed to the name once. Rows that never went out (a
        `[held]` message's, one queued while the cache was cold) don't hold it back:
        those wait for their own release point while live mail goes alone. Under the
        lock."""
        rows = self.queue.get(binding.qualified, {})
        binding.returned &= rows.keys()
        mid = _as_id(message_id)
        return any(_as_id(m) < mid for m in binding.returned)

    def _release(self, binding: Binding, *, flagged: bool, exclude=()) -> int:
        """Hand the name's rows this binding was not handed already, oldest first, each
        with `held: true`. `flagged=False` leaves the rows of `[held]` messages; ids in
        `exclude` are left too (a settle window's frame named them `unhandled`). Returns
        how many went out. The rows stay until the adapter names them `handled`.

        A row whose message the record does not have, or whose line zero does not
        parse, is skipped and kept: only a `handled` report deletes a row, and the trim
        keeps every queued message, so only a record damaged by hand gets here. It is
        logged once. A record read that raises leaves the rest for the next release
        point.
        """
        q = binding.qualified
        handed = 0
        self.sweep_expired()
        self._drop_reserved(q)
        with self._name_lock(q):
            with self._lock:
                if self.bindings.get(q) is not binding:
                    return 0
                binding.releases_begun += 1
                order = sorted((mid for mid in self.queue.get(q, {})
                                if mid not in binding.delivered and mid not in exclude),
                               key=_as_id)
            for n, mid in enumerate(order):
                try:
                    msg = self.record.get_message(mid)
                except Exception:                                  # noqa: BLE001
                    log.exception("release for %s: reading %s failed; %d row(s) wait for "
                                  "the next release point", q, mid, len(order) - n)
                    break
                ml = parse_machine_line(msg.content) if msg is not None else None
                if ml is None:
                    with self._lock:
                        first = (q, mid) not in self._row_unreadable_logged
                        self._row_unreadable_logged.add((q, mid))
                    if first:
                        log.warning("queued message %s for %s is %s; its row stays and "
                                    "is skipped at every release", mid, q,
                                    "gone from the record" if msg is None
                                    else "not a machine line the daemon can read")
                    continue
                self._note_message(msg, ml)
                gone = self._gone(mid, ml, msg)
                if gone is not None:
                    # Past its expiry, or withdrawn: dropped here, never handed.
                    self._drop_rows(q, [mid], gone)
                    continue
                if not flagged and _flagged(ml, q):
                    continue
                with self._lock:
                    if mid not in self.queue.get(q, {}):
                        continue                 # settled meanwhile
                if not self._claim(binding, mid):
                    continue
                self._deliver(binding, msg, ml, self.attribute(msg, live=False),
                              backlog=False, held=True)
                handed += 1
        self._queue_flush()
        if handed:
            log.info("released %d queued message(s) to %s", handed, q)
        return handed

    # ------------------------------------------------------------------ settle windows
    def _open_window(self, b: Binding, opened: float, unhandled) -> None:
        """Open `b`'s settle window for a `turn` frame that arrived at
        `opened` (`time.monotonic`) and whose read did not pass. Any window the name
        still has is closed first."""
        w = SettleWindow(binding=b, opened=opened, steps=tuple(RELEASE_SETTLE_STEPS),
                         exclude=frozenset(str(mid) for mid in unhandled))
        with self._window_cond:
            old = self._windows.get(b.qualified)
            if old is not None:
                old.closed = True
            self._windows[b.qualified] = w
            self._window_cond.notify_all()

    def _close_window(self, b: Binding) -> None:
        """Close `b`'s open settle window, if it has one: a newer `turn` frame of the
        binding, or the binding's end. Another binding's window under the same name is
        left to find its binding gone."""
        with self._window_cond:
            w = self._windows.get(b.qualified)
            if w is not None and w.binding is b:
                w.closed = True
                del self._windows[b.qualified]

    def _window_loop(self) -> None:
        """The one worker for every settle window: it waits until the next window's
        next read is due and makes it. A frame's handler never sleeps for a window."""
        while not self._stop.is_set():
            with self._window_cond:
                due = [w for w in self._windows.values() if not w.closed]
                if not due:
                    self._window_cond.wait(1.0)
                    continue
                w = min(due, key=SettleWindow.due)
                wait = w.due() - time.monotonic()
                if wait > 0:
                    self._window_cond.wait(wait)
                    continue
            try:
                self._window_read(w)
            except Exception:                                      # noqa: BLE001
                log.exception("settle window for %s failed; its rows wait for the next "
                              "release point", w.binding.qualified)
                with self._window_cond:
                    w.closed = True
                    if self._windows.get(w.binding.qualified) is w:
                        del self._windows[w.binding.qualified]

    def _window_read(self, w: SettleWindow) -> None:
        """One read of a settle window. A read that passes closes the window and
        releases every row then queued but the frame's `unhandled` ids. The last step's
        read not passing closes it, with one log line when rows wait. Its reads log
        nothing else: the frame's own read said why the release waited."""
        b, q = w.binding, w.binding.qualified
        with self._lock:
            bound = self.bindings.get(q) is b
        if not bound:
            self._close_window(b)
            return
        clock = self._read(b, quiet=True)
        passed = clock.kind in cache.PASSING
        with self._window_cond:
            if w.closed:
                return                        # a newer turn or the binding's end
            w.taken += 1
            ran_out = not passed and w.taken >= len(w.steps)
            if passed or ran_out:
                w.closed = True
                if self._windows.get(q) is w:
                    del self._windows[q]
        if passed:
            self._release(b, flagged=True, exclude=w.exclude)
        elif ran_out and self._rows_wait(b):
            log.info("release for %s: its cache still reads %s %gs after the turn ended; "
                     "the rows wait for the next release point", q, self._describe(clock),
                     w.steps[-1])

    def _settle(self, binding: Binding, handled=(), unhandled=()) -> None:
        """An ack, in memory, after the books took it: each `handled` id has no row now
        and is this binding's handled; each `unhandled` id waits again (while the
        binding is the name's) and leaves `delivered`, so the next release point hands
        it again."""
        q = binding.qualified
        with self._lock:
            rows = self.queue.get(q, {})
            for mid in handled:
                binding.handled.add(mid)
                rows.pop(mid, None)
            if not rows:
                self.queue.pop(q, None)
            for mid in unhandled:
                binding.delivered.discard(mid)
                if self.bindings.get(q) is binding:
                    self.queue.setdefault(q, {}).setdefault(mid, None)
                    binding.returned.add(mid)
        if unhandled:
            self._queue_flush()

    def _end_pass(self, memo: dict) -> None:
        """The end of one routing pass: the cache reads it kept go, and the rows' times
        are filled in. Never raises."""
        memo.pop(_GATE_MEMO, None)
        memo.pop(_ROUTED_MEMO, None)
        self._queue_flush()

    # ------------------------------------------------------------------ boot
    def _load_queue(self) -> None:
        """Boot, after the fence and before any method is answered: every row, all of
        them waiting now. A row for an `agent-N` is dropped: its numbering restarts with
        the daemon, so the name would be a later, unrelated session's. A read that
        RAISES stops the start."""
        rows = self.books.rows(states=[books_mod.WAITING])
        dropped: dict[str, list] = {}
        with self._lock:
            self.queue = {}
            for row in rows:
                q, mid = row.name, row.message_id
                if is_auto_name(split_tag(q)[0]):
                    dropped.setdefault(q, []).append(mid)
                    continue
                self.queue.setdefault(q, {})[mid] = None
            self._queue_loaded = True
        for q, ids in dropped.items():
            self._book("drop the rows of", q, lambda q=q, ids=ids: self.books.drop(q, ids))
        self._queue_flush()
        # After the reconcile at start: mail that waited for a name now reserved.
        self._drop_reserved()
        n = sum(len(v) for v in self.queue.values())
        if n:
            log.info("boot: %d queued message row(s) for %d name(s)", n, len(self.queue))
        if dropped:
            log.info("boot: %d queue row(s) for agent-N names dropped: an agent-N ends "
                     "with its binding", sum(len(v) for v in dropped.values()))

    def _name_lock(self, qualified: str) -> threading.RLock:
        with self._lock:
            lock = self._release_locks.get(qualified)
            if lock is None:
                lock = self._release_locks[qualified] = threading.RLock()
            return lock

    def _deliver(self, binding: Binding, msg, ml, author: Author, *, backlog: bool,
                 held: bool = False) -> None:
        # The audience fill-in lives HERE, the one point live routing, the rescan and
        # register replay all pass through, so all three deliver the same payload. A
        # defect in it costs the audience fields, never the delivery.
        try:
            audience, audience_source = self._audience_of(msg, ml)
        except Exception:
            log.exception("audience fill-in failed for msg=%s; delivering it as unknown",
                          msg.id)
            audience, audience_source = None, AUDIENCE_UNKNOWN
        payload = {
            "message_id": msg.id,
            "channel_id": msg.channel_id,
            "room": self.record.channel_name(msg.channel_id) or msg.channel_id,
            "author": {"name": author.qualified, "tier": author.tier,
                       "attribution": author.attribution},
            "recipients": list(ml.recipients),
            # the posture, from this message's own line zero: "open", "none", "optional"
            # or "once".
            "posture": ml.posture,
            # `[open]`: a new send that stated open, the root of a thread with limits.
            "open_mark": ml.open_mark,
            "backlog": backlog,
            "text": ml.text,
            # correlation comes from the record's own line zero (a post carries no
            # reply field).
            "reply_to": ml.reply_to,
            "audience": audience,
            "audience_source": audience_source,
            "timestamp": msg.timestamp,
            # line zero's `[interrupt]`: the sender asks for a running turn where the
            # harness can take it there.
            "interrupt": ml.interrupt,
            # line zero's `[expires:...]`, a Unix second; None never expires.
            "expires_at": ml.expires_at,
        }
        if ml.thread_mark:
            # "one-left" or "last", the thread mark the send verb wrote on a reply in an
            # open thread; absent otherwise.
            payload["thread_mark"] = ml.thread_mark
        if held:
            # Handed from the queue: the recipient is told it waited, and how long.
            # Replay and live delivery send no `held`.
            payload["held"] = True
        with self._lock:
            if len(binding.window) >= WINDOW or binding.overflow:
                # The window is full, or older mail already waits for room in it: this
                # one waits behind it, so a live message never overtakes an older one.
                binding.overflow[msg.id] = payload
                return
            binding.window.add(msg.id)
        self._push(binding, payload)

    def _push(self, binding: Binding, payload: dict) -> None:
        """Hand one `deliver` frame to the binding's connection, whose writer sends it
        without blocking this thread. A connection already closing takes nothing: the
        id leaves `delivered` and is queued, and the name's next release point hands it
        again. Not for an `agent-N`, whose mail ends with its binding. A frame the
        connection drops after this is queued at its unbind, which re-queues every id
        handed and not handled."""
        mid = payload["message_id"]
        if binding.conn.closed:
            log.warning("delivery to %s failed (connection closed); msg=%s is queued",
                        binding.qualified, mid)
            with self._lock:
                binding.delivered.discard(mid)
                binding.window.discard(mid)
                if binding.tier != TIER_EPHEMERAL:
                    self._queue_add(binding.qualified, mid, _epoch(payload["timestamp"]))
            self._queue_flush()
            return
        binding.conn.notify_threadsafe("deliver", {"payload": payload})
        with self._lock:
            now = self.clock()
            # The hand-over time, for `readable` and `moxi comms explain`: live routing,
            # released held mail and replay all hand over here.
            self.handed_at[(binding.qualified, mid)] = now
        log.info("deliver msg=%s room=%s to=%s backlog=%s",
                 mid, payload["room"], binding.qualified, payload["backlog"])

    def _window_take(self, binding: Binding, taken) -> None:
        """The adapter took into a digest every pushed id for which `taken(id)` is
        true: they leave the binding's window, and the window is topped up from its
        overflow, oldest first."""
        with self._lock:
            binding.window = {mid for mid in binding.window if not taken(mid)}
            ready = []
            while binding.overflow and len(binding.window) + len(ready) < WINDOW:
                mid = min(binding.overflow, key=_as_id)
                ready.append(binding.overflow.pop(mid))
            binding.window.update(p["message_id"] for p in ready)
        if not ready:
            return
        with self._name_lock(binding.qualified):
            for payload in ready:
                mid = payload["message_id"]
                gone = self._gone(mid, expires_at=payload.get("expires_at"))
                if gone is not None:
                    # It waited for room past its expiry, or was withdrawn meanwhile.
                    with self._lock:
                        binding.window.discard(mid)
                        binding.delivered.discard(mid)
                    self._book("drop a row of", binding.qualified,
                               lambda gone=gone, mid=mid: self.books.drop_gone(
                                   binding.qualified, [mid], gone))
                    continue
                self._push(binding, payload)

    # ------------------------------------------------------------------ the audience
    def _index_audience(self, msg, ml) -> None:
        """Record one parsed message's EFFECTIVE audience.

        Its written audience if it has one; else, with no `re:`, None (known none); else
        its referent's effective audience if the index holds it; else
        AUDIENCE_INDEX_UNKNOWN. A referent held as unknown passes unknown on, so unknown
        never decays into known none down a chain. The index stores the effective value,
        so a chain of hand-typed replies inherits transitively with one lookup and no
        walk. Under the daemon's one lock, because live routing, the rescan and register
        replay feed it concurrently.

        A re-index never replaces a known value with unknown: when a rescan or a
        replay re-parses a message whose referent has left the index, the entry already
        held stands. A recomputed known value (tag or None) does replace the entry, which
        is how a reply that raced the boot rescan heals once the rescan reaches it.
        """
        with self._lock:
            effective = ml.audience
            if effective is None and ml.reply_to is not None:
                effective = self.audiences.get(ml.reply_to, AUDIENCE_INDEX_UNKNOWN)
            if effective is AUDIENCE_INDEX_UNKNOWN and msg.id in self.audiences:
                return                  # keep what is held; unknown adds nothing to it
            self.audiences[msg.id] = effective
            if len(self.audiences) > constants.RESCAN_LIMIT + AUDIENCE_INDEX_SLACK:
                # Bounded to the rescan window: the newest RESCAN_LIMIT ids stay, by id
                # and not by arrival, because the boot race lets a live id in first.
                keep = sorted(self.audiences, key=_as_id)[-constants.RESCAN_LIMIT:]
                self.audiences = {k: self.audiences[k] for k in keep}

    def _audience_of(self, msg, ml) -> tuple[str | None, str | None]:
        """(audience, audience_source) for one delivery -- a dictionary lookup, no read.

        Touches ABSENCE only: an audience on line zero is delivered as typed, even where
        it differs from the referent's, because the verb is where a change is refused and
        the daemon polices no line zero. Nothing here reaches the record:
        a referent the index does not hold, or holds as unknown, is `unknown`, said in the
        payload. A referent held as known none delivers no mark.
        """
        if ml.audience is not None:
            return ml.audience, AUDIENCE_WRITTEN
        if ml.reply_to is None:
            return None, None
        with self._lock:
            known = ml.reply_to in self.audiences
            inherited = self.audiences.get(ml.reply_to, AUDIENCE_INDEX_UNKNOWN)
        if inherited is None:
            return None, None           # the referent is indexed and carries no audience
        if inherited is not AUDIENCE_INDEX_UNKNOWN:
            return inherited, AUDIENCE_INHERITED
        if known:
            log.info("audience unknown: msg=%s answers re:%s, whose own audience this "
                     "daemon could not work out (its chain reaches a referent it never "
                     "indexed)", msg.id, ml.reply_to)
        else:
            log.info("audience unknown: msg=%s answers re:%s, which this daemon has not "
                     "indexed (older than the rescan window, never in the record, or not "
                     "yet reached by the boot rescan)", msg.id, ml.reply_to)
        return None, AUDIENCE_UNKNOWN

    # ------------------------------------------------------------------ attribution
    def attribute(self, msg, *, live: bool) -> Author:
        """What the record proves."""
        with self._db() as db:
            return attribution.from_record(db, msg)

    # ------------------------------------------------------------------ catch-up
    def _replay_complete(self, binding: Binding) -> None:
        """The end of a bind's catch-up: `replay_complete`, with the name's cursor.

        Catch-up is the name's rows, which `_on_register` releases before this; nothing
        is replayed from the record. Every routed message holds a row until it is acked
        or dropped, so a message with no row is not one to deliver again."""
        q = binding.qualified
        cursor = None if binding.tier == TIER_EPHEMERAL else self._cursor(q)
        binding.conn.notify_threadsafe("replay_complete", {"identity": q, "cursor": cursor,
                                                           "truncated": False})

    def _position(self, ml, binding: Binding, msg) -> int | None:
        """The position on line zero of the first tag that names this binding's
        identity, or None when none does. The send's cold-at value at that position is
        this recipient's."""
        for position, tag in enumerate(ml.recipients):
            r = self.resolve(tag)
            if r.resolvable and r.qualified == binding.qualified:
                return position
        return None

    # ------------------------------------------------------------------ retention
    def trim_record(self) -> None:
        """Drop what the record has kept past `retain_days` (local backend only).

        The daemon is the one process that runs all day, so it is where "at boot and once a
        day" lives. A backend with no `trim` (the mock) has nothing to do here. A
        failure is logged and nothing else: retention is housekeeping, and a daemon that
        cannot housekeep is still a daemon that delivers.
        """
        # Stamped whatever the backend is, so a daemon on the mock asks
        # once a day and not once a trim tick.
        self._last_trim = self.clock()
        self._trim_handed()
        trim = getattr(self.record, "trim", None)
        if trim is None:
            return
        if not self._queue_loaded:
            # The trim keeps what the queue holds, and the queue has not been loaded in
            # this daemon's life, so a queued message past retention would go with the
            # rest. Skipped until it is; the record only grows meanwhile.
            log.warning("record trim skipped: the queue has not been loaded yet")
            return
        try:
            # Queued mail stays past `retain_days` until it is handled: no age limit.
            deleted = trim(keep=self.queued_ids())
        except Exception:
            log.exception("record trim failed; the record keeps what it has")
            return
        if deleted:
            log.info("record trim removed %d message(s) past retention", deleted)

    # ------------------------------------------------------------------ the trim loop
    def _trim_loop(self) -> None:
        """The record trim's clock: the daily `TRIM_INTERVAL` check, every `TRIM_TICK`."""
        while not self._stop.wait(TRIM_TICK):
            if self.clock() - self._last_trim >= TRIM_INTERVAL:
                self.trim_record()

    def classify(self, qualified: str, message_id: str) -> str:
        """`moxi comms explain`'s case for one message handed to one name, on demand."""
        cursor = self._cursor(qualified)
        if cursor is not None and int(cursor) >= int(message_id):
            return CASE_HANDLED
        with self._lock:
            bound = qualified in self.bindings
            injected = self.injected_through.get(qualified)
        if not bound:
            return CASE_NOT_ADDRESSABLE
        if injected is not None and int(injected) >= int(message_id):
            return CASE_TURN_INCOMPLETE
        return CASE_IGNORED


class DaemonStopping(Exception):
    """A `bind` or `send` that arrived after `stop()` began: answered `stopping`, in the
    try-again range. Every adapter already reads that as "the daemon is not there" and
    retries."""


#: The read methods: any client may call them, and they take no connection.
READS = ("resolve", "agents", "session", "owner", "asks", "owed", "readable", "explain",
         "binding_window", "attributed_message", "rooms", "message", "history", "max_id",
         "last_digest", "replies_to")


class Refusal(Exception):
    """The daemon's own no, with its comms error `code` (`wire`), and in `extra` what
    it names (the holder of a name, say), which goes in the error's `data`. A refusal
    without a code is a request whose params don't fit the method."""

    def __init__(self, message, *, code: int = wire.INVALID_PARAMS, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


def _presented(conn, token) -> bytes:
    """The hash the `token` of a `send` or `delete` stands for: the token's SHA-256, or,
    from a connection in comms' own process (its tools, `mcp.inprocess`), the hash
    itself as hex, since the daemon keeps only the hash of a binding's token. No socket
    connection is in process, so over the socket only the token itself is accepted."""
    if not isinstance(token, str):
        return b""
    if getattr(conn, "in_process", False) is True:
        try:
            return bytes.fromhex(token)
        except ValueError:
            return b""
    return hashlib.sha256(token.encode()).digest()


def _unknown_message(mid: str) -> Refusal:
    """The refusal for a message id the record doesn't hold."""
    return Refusal(f"no message {mid} in the record", code=wire.UNKNOWN_MESSAGE,
                   message_id=mid)


def _read(daemon, attr: str):
    """A read method as the method table calls it, with the connection it ignores. The
    read is looked up on the daemon at each call, so the table follows the daemon's own
    attribute."""
    return lambda conn, params: getattr(daemon, attr)(params)


def _asked(frame: dict) -> str:
    """What a register asked for, for the refused-register journal line."""
    name = frame.get("name") or "auto"
    scope = GLOBAL if frame.get("global") else (frame.get("scope") or GLOBAL)
    return (f"name={name} scope={scope} "
            f"adapter={frame.get('adapter') or 'claude-shim'}")


def _as_id(message_id: str) -> int:
    """A message id as a number, for ordering. Anything unreadable sorts first."""
    try:
        return int(message_id)
    except (TypeError, ValueError):
        return -1



def _flagged(ml, qualified: str) -> bool:
    """Does line zero's `[held]` (or old `[next-turn]`) hold this message for
    `qualified`? For every name but the operator's, whom the flag never holds."""
    return bool(ml.held) and qualified != OPERATOR_NAME


def _passes(ml, position: int, clock, now) -> bool:
    """Does a live hand-over of this message pass on this read? A known read decides
    alone: `warm`, `fresh` and `no-cache` pass, `cold` does not. On an `unknown` read the
    send's reading decides, from the `[cold-at:…]` value at the recipient's position on
    line zero: `-` passes, a time passes while the daemon's clock is still before it,
    and a line with no token (posted before the queue, or not by the send verb)
    carries no reading and does not pass."""
    if clock is None:
        return False
    if clock.kind in cache.PASSING:
        return True
    if clock.kind != cache.UNKNOWN:
        return False
    cold_at = getattr(ml, "cold_at", None)
    if not cold_at or position >= len(cold_at):
        return False
    value = cold_at[position]
    if value == COLD_AT_LIVE:
        return True
    return isinstance(value, int) and now is not None and now < value


def _epoch(timestamp: str) -> float:
    try:
        dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


# --------------------------------------------------------------------------- main

def main(argv=None) -> int:  # pragma: no cover - exercised by the CLI smoke run
    import argparse

    from moxi import doctor as moxi_doctor
    from moxi.files import LockTimeout
    from moxi.store import StoreError

    from .record import BACKENDS, LOCAL, MOCK, RecordError, backend_choice, open_record
    from .release import MODULE

    from . import provision, tokens

    ap = argparse.ArgumentParser(prog="moxi-comms-daemon")
    ap.add_argument("--backend", choices=BACKENDS,
                    help="which backend to hold: local or mock; default: "
                         "MOXI_COMMS_BACKEND, then the config file, then local")
    ap.add_argument("--backend-store", help="JSON store of the mock record")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.backend_store:
        os.environ["MOXI_COMMS_MOCK_BACKEND"] = args.backend_store
    # The flag may name a backend the box's config does not, and every adapter resolves the
    # backend WITHOUT a flag. A daemon holding one record while the shims and the CLI write
    # another is a fleet that looks connected and delivers nothing, so the disagreement is
    # a refusal to start. The exception is `mock` with a scratch store, which is the test
    # and by-hand case.
    # The settings first: a config file that does not load refuses the start. After
    # this the daemon reads them at use and keeps the last good ones.
    settings = config.Config(report=_config_kept)
    try:
        settings.start()
    except config.ConfigError as e:
        raise SystemExit(str(e)) from None
    try:
        choice = backend_choice(None, env=os.environ)
    except RecordError as e:
        raise SystemExit(str(e)) from None
    resolved = choice.name
    if args.backend and args.backend != resolved and not (
            args.backend == MOCK and args.backend_store):
        raise SystemExit(
            f"--backend {args.backend} disagrees with this box's configured backend "
            f"({resolved}). Every adapter and the CLI resolve the backend from the "
            f"config and the environment, with no flag, so a daemon started this way "
            f"would hold a record nothing else writes. Change `backend` in comms' config "
            f"file (or MOXI_COMMS_BACKEND) if the flip is what you meant, or drop the flag.")
    try:
        record = open_record(args.backend, role="reader")
    except RecordError as e:
        raise SystemExit(str(e)) from None
    except (StoreError, LockTimeout) as e:
        # The store's gate refused (comms 0.2's files can't be moved, or a store sits
        # beside them that the move didn't make): the daemon doesn't start. The log has
        # it, and so does the next doctor run, until a start opens the store.
        log.error("comms.db can't be opened, so the daemon doesn't start: %s", e)
        with contextlib.suppress(Exception):
            moxi_doctor.record(MODULE, f"the daemon didn't start: {e}", key=STORE_RECORD,
                               fix="moxi comms migrate --dry-run")
        raise SystemExit(f"moxi-comms-daemon: {e}") from None
    with contextlib.suppress(Exception):
        moxi_doctor.clear(MODULE, STORE_RECORD)
    # systemd stops the unit with SIGTERM. Without a handler the process died before the
    # `finally`, every restart left its custody intervals open for the next boot's
    # fence, and the fence's warning could not tell a crash from a restart. Installed
    # before `start()` binds the socket, so no stop can land in a window the handler
    # does not cover.
    stopping = threading.Event()
    # SIGHUP does not exist on Windows.
    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, lambda *_: stopping.set())
    # A daemon on the local backend provisions an empty directory itself, so an install
    # that names nothing ends with a working daemon; an existing directory is left alone
    # (`provision.first_start`). The mock keeps its own record. Built HERE,
    # in `main`: a Daemon constructed by a test, a smoke script or an embedding caller
    # provisions nothing.
    store = (tokens.TokenStore(None, env=os.environ)
             if (args.backend or resolved) == LOCAL else None)
    first_start = provision.first_start(store) if store is not None else None
    daemon = Daemon(record, first_start=first_start, settings=settings, serve_tools=True,
                    on_fatal=lambda why: stopping.set())
    try:
        daemon.start()
    except RuntimeError as e:
        # A refusal is an answer, not a crash: the operator gets the recovery sentence
        # and systemd gets a clean nonzero exit, and the record connection this process
        # opened does not leak past the `finally` it never reaches.
        with contextlib.suppress(Exception):
            record.close()
        raise SystemExit(str(e)) from None
    log.info("comms daemon listening on %s", daemon.socket_path)
    try:
        stopping.wait()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop()
        with contextlib.suppress(Exception):
            record.close()          # the process owns the socket it opened
    # Exits nonzero when something it can't run without failed, so its supervisor
    # starts it again.
    return 1 if daemon.fatal else 0


__all__ = ["Daemon", "Refusal", "Author", "Binding", "TRIM_INTERVAL", "TRIM_TICK",
           "CASE_HANDLED", "CASE_IGNORED", "CASE_NOT_ADDRESSABLE", "CASE_TURN_INCOMPLETE",
           "CASE_EXPIRED", "CASE_WITHDRAWN", "CASE_RESERVED",
           "CASE_REASONS"]
