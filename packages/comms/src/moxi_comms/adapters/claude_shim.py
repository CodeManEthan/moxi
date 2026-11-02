"""The `claude-shim` delivery adapter.

Emission timing: the daemon delivers immediately; the ADAPTER owns when its harness can
safely take the message -- turn boundaries only, queue and coalesce. Backlog coalesces into
ONE injected turn; live messages, arriving one at a time, flush one at a time through the
same code -- the boundary rule produces both.

The adapter reports completion; the daemon keeps the books. Before it renders a batch the
shim opens a digest with the daemon (`open_digest`) and renders only the ids the answer
keeps. Turn completion for `claude-shim` is the Stop hook, which is exactly
`on_turn_boundary()`: one call both completes the turn that was running and opens the
next one. Every turn end sends `turn_ended` with `handled` (each digest whose turn
completed) and `unhandled` (each digest whose turn failed): the daemon deletes the
handled digests' rows and advances the cursor in one transaction, and the unhandled ones
wait again. A StopFailure (an API error) ends the turn without completing it. Death
mid-turn means the call never comes; the binding's end puts the rows back to waiting,
and the whole digest replays at the next bind. Replay is the safe direction. The idle
tick is not a turn and sends no `kind`.

Authority: the frame line is where a recipient's
launch-declared directors become visible. The declaration arrives as `--director`
(qualified by `moxi-comms-claude`) or the `MOXI_COMMS_DIRECTORS` stamp; nothing about it touches
the daemon, the payload or the record. `frame_line` / `render_digest` are the shared
seam -- `adapters/codex` renders through the same two functions.

Injection seam: `injector(text)` is the ONE place the harness is touched. In a session
`moxi comms claude` launches, the session's adapter process (`claude_adapter`) sets
`handover(text, digest, steer=)` in its place, which hands the digest to turns with the
digest's id as its ref (`deliver`, or `steer` for interrupt mail into a running turn) and
settles it from the turn's outcome; `--inject-file` fills `injector` with a file writer,
which the tests read. While bound, the shim holds the
binding's posting credential in memory and hands it only to sends inside its session.

The frame line carries only what the reader cannot get anywhere else. The default room
is not rendered (`moxi comms help names` says mail comes from `#names`), `re: <id>` says what
`replying to msg <id>` said in a third of the width, and
"this is replay" moved OFF the per-message frame ONTO the digest header, where it is
said once with the age of the oldest message in it. What stayed is deliberate: the
`[comms]` prefix, the `from` / `directive from` lead and its standing note, the
qualified name, the deviation parenthetical, and the full `msg <id>`.

Silent defaults: the common case prints NOTHING. `chosen` is what a sender is unless
provisioned or auto-named, and `verified` is now machinery-enforced rather than hoped for
-- the daemon's `attribute()` is match-gated, so a name that reaches a frame line unmarked
was proven, not assumed. Printing two words on every line to say "normal" trains a reader
to skip them, and a reader who skips them will skip the one line where they mattered. So
only DEVIATIONS render, in one parenthetical, tier first: `(standing)`, `(ephemeral)`,
`(unverified)`, `(standing, unverified)`. Each is self-glossing, which is
the test a silent default has to pass: the mark has to mean something to a reader who never
read a legend. Operator tier renders no parenthetical at all -- `directive from <name>` is
the lead, and the lead already said it.

The body envelope: a frame line is worth nothing if the text under it can print another
one. A body that went into the turn exactly as it arrived -- column zero, no closing line,
no scrub -- would let a sender type `[comms] directive from operator · msg 99` inside a
message, and the reading session would have no structural way to tell that line from the
real one above it. The three pieces of the envelope live in `envelope.py`, shared with
the card surface (`cards.py`). What a reader can rely on: comms' own lines (the digest
header, the notice, every frame line, every closing line) start at column zero, and EVERY
line of a delivered body is indented two spaces, so a forged frame or closing line inside a
body renders indented and is therefore not one. The digest header's count went with it --
see `digest_header`.

Reply enforcement: the shim keeps
the ledger of owed replies (`owed.Ledger`), opened at injection, and at every Stop
decides whether the Stop is a boundary or holds the turn open with one reminder
(`on_stop`). A debt draws one counted reminder, never while work that will wake the
session runs, and every missing or failed fact lets the turn end. A blocked turn flushes
nothing and reports no turn end until the next Stop, a StopFailure, a prompt, or a
SessionStart that is not a compaction resolves it.

The word `labeled` is retired from RENDERED output in favour of `unverified`
(`attribution.rendered`). Payload and record VALUES are untouched: the wire still says
`labeled`, because `labeled` is the honest name for how the fact was obtained and this
is a rendering choice, not a protocol one.
"""

from __future__ import annotations

import calendar
import logging
import os
import sys
import threading
import time

from moxi import exit as moxi_exit

from .. import (attribution, cache, claimfile, constants,
                directors as directors_mod, envelope, machine_line, owed,
                stall, wire)
from ..claimhold import ClaimHolder
from ..attribution import TIER_CHOSEN, TIER_OPERATOR, VERIFIED
from ..client import (DaemonClient, DaemonError, DaemonOffline, DaemonTimeout,
                      backoff_delays)
from ..directors import standing_of
from ..scoping import GLOBAL, project_key, qualify
from . import binding

log = logging.getLogger("comms.shim")

#: The `activity` kinds this shim sends, each as its own method. A turn's end is the
#: `turn_ended` request (`_send_turn`).
ACTIVITY_READY, ACTIVITY_TURN = "ready", "turn"
#: The daemon method each kind is sent as; no kind is a plain `activity`.
ACTIVITY_METHODS = {ACTIVITY_READY: "ready", "look": "look"}

#: Exit codes, `moxi.exit`'s: a claim that was refused, and a daemon that was not there.
EXIT_REFUSED, EXIT_OFFLINE = moxi_exit.REFUSED, moxi_exit.TEMPFAIL

#: Said to stderr -- the ONLY thing a refused spawn says, so this is where an operator
#: reads why comms is absent from a session. One line, no traceback.
REFUSED_PREFIX = "moxi-comms-shim: this session did not get its comms binding"

OFFLINE_MESSAGE = ("comms offline: the delivery daemon is unreachable. Messages are not "
                   "flowing; they wait in the record and arrive when it is back.")

#: The launch line when the daemon was down as this session started, and the session
#: started anyway. "Binds when it returns" is exact about the binding and deliberately
#: quiet about the backlog -- what delivers a late first bind's mail is the daemon's own
#: catch-up, which hands the name's rows, every message routed to it that no turn has
#: acked. (`self.replaying` is test visibility on this path; nothing in the claude path
#: reads it.)
#:
#: KNOWN GAP, and it is the TRANSITION, not the launch. Telling the agent comms is
#: offline is met at launch by comms' listener: at the session's start it answers turns
#: with "comms was offline at session start; `moxi comms agents` shows the live roster
#: once comms is back" (`shim_roster.OFFLINE`), which turns carries to the agent. What
#: NOTHING tells the agent is that the wait ended. `ONLINE_MESSAGE` goes to `status_sink`
#: -> `log.warning` -> the shim's log, and the roster delta the listener answers at each
#: prompt cannot cover it either: the offline snapshot seeds no roster memory, so the
#: first successful delta after the outage finds no baseline, seeds quietly and
#: announces nothing. An agent told "comms was offline at session start" runs on that
#: belief until mail happens to arrive. Both candidate fixes
#: -- routing status through the injector, or announcing the first usable delta after an
#: offline start -- change a seam; the gap is named here rather than assumed shut.
TOLERATED_OFFLINE_MESSAGE = ("comms offline: the delivery daemon is unreachable; this "
                             "session will bind when it returns.")

ONLINE_MESSAGE = "comms online: reconnected to the delivery daemon."

#: A supervisor-side refusal by a shim that has NEVER held the name. The holder is
#: legitimate and holders release by closing their connection, so this
#: shim keeps polling -- on the CURRENT backoff, never re-seeded.
REFUSED_WAITING = ("comms unbound: this session's name is held by another session. "
                   "Still waiting; this session binds when the name frees.")

#: A supervisor-side refusal by a shim that HELD the name and lost the connection
#: (the succession fence). A successor that claimed the name after our disconnect is
#: relaunch-with-identity working as intended; polling on would make this
#: process a permanent competitor for its own former identity. So: stop polling, stay
#: alive, serve the session offline, and say so once.
REFUSED_TERMINAL = ("comms offline for the rest of this session: another session now "
                    "holds this name. Not retrying -- relaunching with this identity is "
                    "the successor. Messages wait in the record.")

#: A supervisor-side refusal that is not this shim's name: an invalid name or an unknown
#: adapter kind. Said once per
#: distinct reason, in the daemon's words, and retried on the current backoff -- the
#: same generator, never re-seeded.
REFUSED_RETRYING = ("comms unbound: the delivery daemon refused this session's register "
                    "({reason}). Still retrying; this session binds when that clears.")

#: Anything the supervisor did not expect. Said once per exception type; the loop
#: carries on at its next backoff.
SUPERVISOR_ERROR = ("comms supervisor: unexpected error ({error}); still retrying. The "
                    "traceback is in this log.")

#: How a register refusal reads.
NAME_REFUSAL, OTHER_REFUSAL = "name", "other"


def survive_supervisor_error(adapter, error, delays, logger):
    """The harness adapters' supervisor catch-all.

    Called from inside their `except Exception`: log with the traceback, say it once per
    exception type through `status_sink`, wait out the next backoff, and hand back the
    generator to go on with -- a fresh one if a finite test backoff ran dry. The adapter
    clears `_supervisor_error` on its next successful connect.
    """
    logger.exception("comms supervisor: unexpected error, retrying")
    kind = type(error).__name__
    if getattr(adapter, "_supervisor_error", None) != kind:
        adapter._supervisor_error = kind
        adapter.status_sink(SUPERVISOR_ERROR.format(error=f"{kind}: {error}"))
    try:
        wait = next(delays)
    except StopIteration:
        delays = backoff_delays()
        wait = next(delays)
    adapter._stop.wait(wait)
    return delays

#: The room every name-lane message rides. Rendered ONLY when a message did not come
#: from it. Compared sigil-stripped because the directory spells the room `#names` and
#: the wire `names`; the payload carries whatever the record's
#: `channel_name` returned, and both spellings mean the same room.
DEFAULT_ROOM = "names"

#: The record's timestamp format.
TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"

#: The tiers that render NOTHING: `chosen` is the default a sender is unless something
#: made it otherwise, and `operator` is already in the `directive from` lead.
SILENT_TIERS = (TIER_CHOSEN, TIER_OPERATOR)

#: Said once per catch-up batch, in the header, instead of once per frame inside it.
REPLAY_ATTRIBUTION_NOTE = "attribution is unverified in replay"


def frame_marks(author: dict, *, suppress_unverified: bool = False) -> str:
    """The deviation parenthetical for one author, or `""` when nothing deviates.

    Tier first, then attribution, one parenthetical: `(standing)`, `(unverified)`,
    `(standing, unverified)`. `suppress_unverified` is the catch-up digest's batch
    rule -- the header already said replay is unverified, so the REPLAYED frames under
    it do not each repeat it (the same shape as backlog itself, said once per batch).
    `render_digest` passes it only for backlog frames, and only when the note it defers
    to actually rendered: a mark is not dropped unless something else said it.
    """
    marks = []
    tier = author.get("tier")
    if tier and tier not in SILENT_TIERS:
        marks.append(tier)
    if author.get("attribution") != VERIFIED and not suppress_unverified:
        marks.append(attribution.UNVERIFIED)
    return f" ({', '.join(marks)})" if marks else ""


def frame_line(payload: dict, directors=(), *, suppress_unverified: bool = False,
               now: float | None = None, steered: bool = False) -> str:
    """ONE attributed frame line per message: who is speaking is never inferred.

    `directors` is the RECIPIENT's launch-time declaration, already qualified. A verified
    message from one of them -- or any operator-tier message -- leads with `directive
    from`; a declared director whose attribution is not verified stays information standing
    and the line says why.

    Silence is the common case and it is load-bearing: no parenthetical means chosen
    and verified, both of which the machinery guarantees rather than guesses.

    `steered` is a digest handed into a running turn: a message whose sender asked to
    interrupt says so (`[interrupt]`), so the reader knows why mail came mid-turn.
    """
    a = payload["author"]
    standing = standing_of(a, directors)
    marks = frame_marks(a, suppress_unverified=suppress_unverified)
    bits = [f"[comms] {standing.lead} {a['name']}{marks}"]
    if standing.note:
        bits.append(standing.note)
    bits.append(f"msg {payload['message_id']}")
    # `str()` because the daemon falls back to the bare channel_id when the record
    # cannot name the channel, and a rendering must never raise over a helpful field.
    # Missing or empty renders no room bit at all, never a dangling `· in `.
    room = str(payload.get("room") or "")
    if room and room.lstrip("#") != DEFAULT_ROOM:
        bits.append(f"in {room}")
    if payload.get("reply_to"):
        bits.append(f"re: {payload['reply_to']}")
    # The audience mark: silent when the message has none, like every message before the
    # mark existed. `written` and `inherited` render alike -- the audience is the
    # exchange's, and `audience_source` is there for a reader that wants the difference.
    # `unknown` is a deviation of its own: a reply whose ask comms could not read, said
    # so rather than left looking like "for you".
    if payload.get("audience"):
        bits.append(f"audience: {payload['audience']}")
    elif payload.get("audience_source") == "unknown":
        bits.append("audience: unknown")
    # The held mark: this one waited in the queue (flagged or not), and when it was sent
    # and how long it waited say how old it is: waiting mail has no age limit.
    if payload.get("held"):
        bits.append(held_mark(payload.get("timestamp"), now=now))
    # The thread mark on a reply in an open thread: one more reply closes it, or this
    # reply closed it.
    mark = machine_line.THREAD_MARKS.get(payload.get("thread_mark") or "")
    if mark:
        bits.append(mark)
    # The posture: none is `[no reply]`, optional is `[reply-optional]`, once is
    # `[reply-once]`, verified or not (an unverified ask carries `(unverified)` among the
    # marks like any other), and `[open]` on the message that opens a thread with limits.
    # Any other open message renders nothing.
    posture = payload.get("posture")
    if posture in (machine_line.POSTURE_NONE, machine_line.POSTURE_OPTIONAL,
                   machine_line.POSTURE_ONCE):
        bits.append(machine_line.POSTURE_TOKENS[posture])
    elif payload.get("open_mark"):
        bits.append(machine_line.OPEN_TOKEN)
    if steered and payload.get("interrupt"):
        bits.append(machine_line.INTERRUPT_TOKEN)
    return " · ".join(bits)


def held_mark(stamp, *, now=None) -> str:
    """`[held] sent 19:22, waited 3h`: the record's timestamp as the local wall clock and
    the wait at render (`humanize_age`). `[held]` alone when the stamp will not parse."""
    now = time.time() if now is None else now
    sent = sent_at(stamp, now=now)
    if sent is None:
        return "[held]"
    return f"[held] sent {sent}, waited {humanize_age(now - parse_timestamp(stamp))}"


def sent_at(stamp, *, now=None) -> str | None:
    """A record timestamp as the local wall clock: `19:22`, or `Sep 26 19:22` when it
    was not today. None when the stamp will not parse; a rendering never raises."""
    epoch = parse_timestamp(stamp)
    if epoch is None:
        return None
    now = time.time() if now is None else now
    then = time.localtime(epoch)
    if time.strftime("%Y-%m-%d", then) == time.strftime("%Y-%m-%d", time.localtime(now)):
        return time.strftime("%H:%M", then)
    return time.strftime("%b %d %H:%M", then)


def end_line(payload: dict) -> str:
    """The line that CLOSES one framed message, paired to its frame line by message id.

    A body can contain this text -- that is fine and it is not what the defense rests on.
    Real body lines are ALWAYS indented (`envelope.INDENT`) and this line never is, so the
    un-indented `[comms] end · msg <id>` is the only one that closes anything, and the id
    says which frame line it closes. `[comms] end` rather than a rule of dashes for the
    same reason the card footer is a sentence: a reader meeting it mid-turn can look it up
    (`moxi comms help end`), and a decoration cannot be looked up.
    """
    return f"[comms] end · msg {payload['message_id']}"


def render_message(payload: dict, directors=(), *,
                   suppress_unverified: bool = False, now: float | None = None,
                   steered: bool = False) -> str:
    """ONE delivered message: the frame line, the body inside it, the closing line.

    The body is somebody else's prose arriving in a context comms does not own, so it gets
    the card treatment (`envelope.body`: scrubbed of control and format characters, every
    line indented). Nothing in the text is inspected, refused or rewritten -- the guard is
    positional, which is why it holds for text nobody anticipated.
    """
    return "\n".join([
        frame_line(payload, directors, suppress_unverified=suppress_unverified, now=now,
                   steered=steered),
        *envelope.body(str(payload.get("text") or "")),
        end_line(payload),
    ])


def parse_timestamp(stamp) -> float | None:
    """Record timestamp -> epoch seconds, or None when it is missing or malformed.

    A rendering must never raise over a field it only uses to be helpful.
    """
    try:
        return float(calendar.timegm(time.strptime(stamp, TIMESTAMP_FMT)))
    except (TypeError, ValueError):
        return None


def humanize_age(seconds: float) -> str:
    """Coarse on purpose: minutes under an hour, hours under two days, then days.

    The reader needs to know whether a catch-up is stale, not how stale to the second.
    """
    seconds = max(0.0, seconds)
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 2 * 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def is_catch_up(payloads: list) -> bool:
    """A batch with ANY replayed message in it is a catch-up batch (mixed batches are
    real: registration replay and live routing race by construction)."""
    return bool(payloads) and any(p.get("backlog") for p in payloads)


def backlog_frames(payloads: list) -> list:
    """Just the replayed frames. The header's attribution note is about REPLAY, so the
    only frames that can earn it -- or be silenced by it -- are the replayed ones."""
    return [p for p in payloads if p.get("backlog")]


def replay_is_unverified(payloads: list) -> bool:
    """Does this catch-up batch need the header's attribution note?

    Replay reads the record's author. A batch with any unverified author needs the note;
    an all-verified catch-up says nothing, and the silence is honest.

    `all` over the BACKLOG frames, not `any` over every frame: the note is a
    blanket statement and a blanket statement earns its silence only when it is true of
    everything it silences. Under the old `any`-over-all-payloads reading a mixed batch
    lied twice -- a live unverified arrival lost its own mark to a note that says `in
    replay`, and a backlog batch holding one verified and one unverified frame rendered
    both identically. Either falsifies the default "a frame with no `(unverified)` mark
    is verified" (the glossary's `verified` entry), which is the one thing the frame
    line's silence is worth. So: no backlog, no note; mixed verification in the backlog,
    no note and every frame carries its own mark; wholly unverified backlog, one note and
    quiet frames.
    """
    backlog = backlog_frames(payloads)
    return bool(backlog) and all(
        (p.get("author") or {}).get("attribution") != VERIFIED for p in backlog)


def digest_header(payloads: list, *, clock=time.time) -> str | None:
    """The one line that says what KIND of batch this is, and how many messages are in
    it. None only for an empty batch.

    A backlog batch always gets the envelope -- one replayed message is still replay,
    and being told so is the whole point -- and carries the age of its oldest message.
    A live batch keeps the plain count.

    **A live single says `1 message` too.** The count is not a courtesy, it is how a reader
    knows how many framed messages to expect, and it was missing in exactly the case where
    a body forging a second frame would have nothing to contradict it. The count and the
    closing lines answer the same question from both ends: N frames announced, N un-indented
    `[comms] end` lines to find.
    """
    if not payloads:
        return None
    if not is_catch_up(payloads):
        return f"== comms: {len(payloads)} message{'s' if len(payloads) > 1 else ''} =="
    stamps = [t for t in (parse_timestamp(p.get("timestamp")) for p in payloads)
              if t is not None]
    inside = []
    if stamps:
        humanized = humanize_age(clock() - min(stamps))
        # "oldest" only means something when there is more than one to be oldest of.
        inside.append(f"{humanized} ago" if len(payloads) == 1
                      else f"oldest {humanized} ago")
    if replay_is_unverified(payloads):
        inside.append(REPLAY_ATTRIBUTION_NOTE)
    said = f" ({'; '.join(inside)})" if inside else ""
    return f"== comms catch-up: {len(payloads)} message(s){said} =="


def render_digest(payloads: list, notice: str | None = None, directors=(), *,
                  clock=time.time, steered: bool = False) -> str:
    # The header's attribution note and the per-frame `(unverified)` marks are ONE
    # statement: whichever place says it, the other stays quiet. The note only ever
    # speaks for the REPLAYED frames (`replay_is_unverified`), so it can only silence
    # those -- a live frame in a mixed batch keeps its own mark, because a note that
    # says `in replay` was never about it.
    #
    # Every line this function writes -- header, notice, frame lines, closing lines --
    # sits at column zero, and every line of every BODY is indented by `render_message`.
    # That is the whole of the envelope: the notice above is unenveloped, and it is safe
    # only because a body can no longer reach column zero to forge one.
    note = replay_is_unverified(payloads)
    parts = []
    header = digest_header(payloads, clock=clock)
    if header:
        parts.append(header)
    if notice:
        parts.append(f"[comms] {notice}")
    for p in payloads:
        suppress = note and bool(p.get("backlog"))
        parts.append(render_message(p, directors, suppress_unverified=suppress,
                                    now=clock(), steered=steered))
    return "\n\n".join(parts)


class ClaudeShim:
    #: A first bind into a name another session holds waits for it. A wrapper that
    #: refuses such a launch before its harness starts turns this off.
    WAIT_WHEN_HELD: bool = True
    #: The session's token for comms' tools (`binding.ToolToken`), registered after
    #: every bind; None when the launch carries none.
    tool_token: binding.ToolToken | None = None
    #: The form of layer one this session was taught (`context.layer_one_form` of its
    #: launch), read before the token leaves the environment; None is the CLI form. The
    #: reply reminder follows it.
    layer_form: str | None = None

    def __init__(self, *, name=None, scope=None, global_=False,
                 project=None, injector=None, socket_path=None, status_sink=None,
                 autoreconnect: bool = False, backoff=None, directors=(),
                 clock=time.time, session_id=None, owed_query=None,
                 projects_root=None,
                 claim_key=None, claim_socket=None, claim_roots=None,
                 listen_claims: bool = False, claim_proc=None):
        #: The live Claude session behind this shim, the latest it has been told. It
        #: starts from the environment Claude
        #: Code spawned the shim with, which is right at spawn and goes stale after a
        #: `/clear`; the first look or turn event that carries one replaces it. The
        #: daemon gets it on `register` and on every `activity`, and the send check
        #: reads the recipient's transcript by it.
        self.session_id: str | None = (
            session_id if session_id is not None
            else (os.environ.get("CLAUDE_CODE_SESSION_ID") or None))
        #: The evidence the daemon needs before it reads a missing transcript as `fresh`
        #: (cold-sends). `projects_root` is where this session's own Claude Code writes
        #: transcripts, worked out once from the environment Claude Code spawned the
        #: shim with, which is the session's. `turned` is whether a prompt or a turn end
        #: has been seen in the CURRENT session id: false at spawn, set by a turn's start
        #: or end, and lowered only by a `/clear` naming a new id. Both ride `register` and every `activity`, and both are read
        #: with `session_id` under `_lock`, so a frame never pairs one id with another
        #: id's `turned`.
        self.projects_root: str | None = (
            str(projects_root) if projects_root is not None
            else cache.projects_root(os.environ))
        self.turned = False
        #: Whether the session has a turns endpoint this shim delivers through, as its
        #: adapter last found it. None for a shim with no such state (the `--inject-file`
        #: seam, a test's shim): nothing is reported. The Claude adapter sets it, and it
        #: rides `register` and every `activity`, so the daemon reports a bound session
        #: with no endpoint as `waiting`.
        self.endpoint: bool | None = None
        # only the catch-up header reads it; injectable so a test can age a backlog
        self.clock = clock
        self.name = name
        self.scope = scope
        self.global_ = global_
        self.project = project if project is not None else project_key()
        # the recipient's grant, qualified at launch and never re-qualified here
        self.directors = tuple(directors or ())
        self.injector = injector or (lambda text: None)
        #: `handover(text, digest, *, steer)`: when set, a flush hands its digest here
        #: instead of to `injector`, with the digest's id, so the caller can name it to
        #: the harness. The Claude adapter sets it.
        self.handover = None
        self.socket_path = socket_path
        self.status_sink = status_sink or (lambda text: log.warning("%s", text))
        self.autoreconnect = autoreconnect
        self._backoff = backoff
        #: The fast local stall signal. Armed the instant the injector returns,
        #: disarmed by the session's next turn START -- not its end. The end is minutes
        #: away on a healthy turn and is what `moxi comms explain` already shows; the
        #: start is the thing a wake that went nowhere never reaches. It reports through
        #: `status_sink`, the same place this adapter says everything else an operator
        #: needs, and through nothing else.
        self.stall = stall.StallTimer(lambda text: self.status_sink(text))

        self.identity: str | None = None
        self.tier: str | None = None
        self.queue: list = []
        self.injected: list = []          # every digest text injected (test visibility)
        self.awaiting_completion: str | None = None
        #: Ids queued or injected under this binding whose turn has not ended: a second
        #: hand-over of one is dropped. A new binding starts it empty, since the daemon
        #: hands the name's rows again at every bind.
        self.held_ids: set[str] = set()
        #: The ids of the digest awaiting completion, beside `awaiting_completion`, and
        #: the daemon's id for that digest.
        self.awaiting_ids: tuple[str, ...] = ()
        self.awaiting_digest: int | None = None
        #: The digests whose turn ended since the last `turn_ended` went out: completed
        #: (`handled`) and failed (`unhandled`). A failed digest's turn did not end
        #: `answer`.
        self.unreported_handled: list[int] = []
        self.unreported_unhandled: list[int] = []
        #: The digests handed apart from the boundary digest above (interrupt mail steered into
        #: a running turn), by digest id: their message ids and the hold they went in
        #: under. Each settles on its own, from its own outcome.
        self.steered: dict[int, tuple[tuple[str, ...], object]] = {}
        #: A SessionStart that came while this shim was not bound: `{"source": ...}`,
        #: sent once right after the next register answer. None when nothing is
        #: owed.
        self.owed_ready: dict | None = None
        self.replaying = False
        self.replay_notice: str | None = None
        self.offline = True
        #: The supervisor's last report. `offline` cannot express this: it stays
        #: True across every refusal, so nothing distinguishes "refused again" from
        #: "refused for the first time" and the supervisor would say it once per retry.
        #: One of "offline" / "online" / "refused"; None until anything is reported.
        self._last_reported_state: str | None = None
        self._lock = threading.RLock()
        self._client: DaemonClient | None = None
        self._stop = threading.Event()
        self._supervisor: threading.Thread | None = None
        #: Reply enforcement. The ledger of owed replies; per turn, this
        #: shim's own blocks (0 or 1) and whether the running turn is one it blocked.
        #: `owed_query(identity, authors, referents)` is the daemon's `owed` query on a
        #: short connection of its own; injectable so a test can answer it.
        self.owed_query = owed_query or self._query_owed
        self.replies = owed.ReplyCheck(
            self._lock, clock=lambda: self.clock(), identity=lambda: self.identity,
            queued=lambda: bool(self.queue),
            owed_query=lambda *args: self.owed_query(*args),
            connection=lambda: self._client, log=log, form=lambda: self.layer_form)
        #: Why the supervisor ended, recorded on each of its two ways out: "stopped", or
        #: the has-held stop's reason. None while it runs.
        self.ended_because: str | None = None
        #: The daemon's words for the last refusal this shim was given, or None.
        self.last_refusal: str | None = None
        #: The register answer the last bind got.
        self._last_resp: dict | None = None
        #: The hold the digest awaiting completion was injected under: its turn's end is
        #: reported only in that hold of that name.
        self.awaiting_mark = None
        self.listen_claims = listen_claims
        self._make_holder(claim_key, claim_socket, claim_roots, claim_proc)

    # ------------------------------------------------------------------ the claim
    def _make_holder(self, key, path, roots, proc=None) -> None:
        """This shim is its session's claim holder. Its file is keyed by the session id
        moxi-comms-claude launched with (the control socket's key); a respawned shim inside
        the same session reads it and comes up in the recorded state. A file for the key
        whose recorded session roots have all ended is a dead session's, not this one's:
        this is a fresh launch, and its own file replaces it."""
        from ..paths import claim_socket
        known = key is not None
        key = key or f"shim-{os.getpid()}-{id(self):x}"
        path = path or claim_socket(key)
        self.claims = ClaimHolder(kind="claude-shim", key=key, socket_path=path,
                                  lock=self._lock, roots=list(roots or ()),
                                  keying="session",
                                  launch_session=key if known else self.session_id,
                                  proc=proc)
        record = claimfile.read(key) if known else None
        if record is not None and not claimfile.tree_root_alive(record, self.claims.proc):
            log.info("the claim file for session %s was a session that has ended; this "
                     "is a new launch", key)
            record = None
        if record is not None:
            self.claims.load(record)
            self.claims.roots = list(roots or ()) or self.claims.roots
            if self.claims.claim:
                self._apply_claim(self.claims.claim)
            self.claims.save()
            return
        name = None if not self.name or self.name == "auto" else self.name
        scope = GLOBAL if self.global_ else (self.scope or GLOBAL)
        claim = {"name": name, "scope": scope, "global": bool(self.global_),
                 "qualified": qualify(name, scope) if name else None,
                 "tier": None, "directors": list(self.directors)}
        self.claims.start_launch(claim)

    def _apply_claim(self, claim: dict) -> None:
        """Register what the claim file holds, for a shim respawned inside its session:
        the name with its declared directors."""
        self.name = claim.get("name")
        self.global_ = bool(claim.get("global"))
        self.scope = claim.get("scope")
        if claim.get("directors") is not None:
            self.directors = tuple(claim["directors"])

    # ------------------------------------------------------------------ lifecycle
    @property
    def ever_bound(self) -> bool:
        """Has this shim EVER held its name? The has-held/never-held split turns on it.

        Derived, not stored: `identity` is assigned on EVERY successful register
        (`connect()` overwrites it, and a `next-free` shim can come back as a different
        agent) but is never returned to None -- not by `_on_close`, not by `stop()`, and
        not by the stop-path return, which lands before the assignment. Non-None is
        therefore the record of having held A name, which is the only thing this
        property is asked about, and needs no second field to say it.
        """
        return self.identity is not None

    def start(self, *, tolerate_offline: bool = False) -> "ClaudeShim":
        """Bind, then supervise. `tolerate_offline` is an explicit opt-in.

        With `tolerate_offline=True` an unreachable daemon at the FIRST bind is not an
        exit: the shim says one line, stays unbound, and lets the supervisor bind when
        the daemon returns. Neither launch path passes it: the wrapper's adapter ends
        the launch with "try again" before `claude` starts, and `_serve_inject_file`
        (the hand-run seam) keeps its `EXIT_OFFLINE`.

        A REFUSAL is still a refusal: only an unreachable daemon and a register that
        never answered are tolerated here. The claim gate is untouched.
        """
        if tolerate_offline and not self.autoreconnect:
            # Tolerate and autoreconnect are ONE feature. Without the supervisor a
            # tolerated shim would neither bind, nor retry, nor exit -- strictly worse
            # than the honest exit code it replaces. Fail loudly at the mis-wiring
            # instead: production passes autoreconnect=True, the test rig defaults False.
            raise ValueError(
                "tolerate_offline requires autoreconnect: without the supervisor a "
                "tolerated shim would never bind and never say so")
        if self.listen_claims:
            self.claims.listen()
        try:
            if self.claims.may_register():
                with self.claims.register_lock:
                    self.connect()
        except (DaemonOffline, DaemonTimeout) as e:
            if not tolerate_offline:
                raise
            log.info("the daemon is not there at launch (%s); serving this session "
                     "unbound and binding when it returns", e)
            self._report("offline", TOLERATED_OFFLINE_MESSAGE)
        except DaemonError as e:
            # A launch into a name another session holds waits for it and cannot send
            # while it waits, unless its wrapper refuses that launch instead. Any other
            # refusal ends the adapter, with one line.
            if (not self.autoreconnect or not self.WAIT_WHEN_HELD
                    or self.refusal_kind(e) != NAME_REFUSAL):
                raise
            self.claims.name_refused((e.data.get("holder") or {}).get("qualified"))
            self._report("refused", REFUSED_WAITING)
        if self.autoreconnect:
            self._supervisor = threading.Thread(target=self._supervise, daemon=True)
            self._supervisor.start()
        return self

    def own_qualified(self) -> str | None:
        """The name a refusal must carry as `holder` to be about THIS shim's identity:
        the one it holds, or before any bind the name qualified as the daemon qualifies
        it. None for an auto-named shim, whose name no refusal can be about."""
        if self.identity is not None:
            return self.identity
        if not self.name or self.name == "auto":
            return None
        scope = GLOBAL if self.global_ else (self.scope or GLOBAL)
        return qualify(self.name, scope)

    def refusal_kind(self, error) -> str | None:
        """Classify a register's `DaemonError`.

        None when it is not a refusal at all (a transient accident, a timeout). A NAME
        refusal names this shim's own identity as the holder. Everything else is OTHER.
        """
        if (error.code is None or error.try_again
                or error.code == wire.INTERNAL_ERROR):
            return None
        holder = (error.data.get("holder") or {}).get("qualified")
        own = self.own_qualified()
        if own is not None and holder == own:
            return NAME_REFUSAL
        return OTHER_REFUSAL

    def connect(self) -> bool:
        """Bind once. The first bind and every reconnect come through here alike."""
        try:
            return self._connect_once()
        except DaemonError as e:
            kind = self.refusal_kind(e)
            if kind is not None:
                with self._lock:
                    self.last_refusal = str(e)
            raise

    def _connect_once(self) -> bool:
        """One register. True when a binding was INSTALLED.

        False is not an error and never raises: the register landed, and the binding it
        created was released again before this shim ever went online -- because `stop()`
        arrived while the round trip was in flight, or because the connection died under
        it. Both leave `offline` True with nothing bound, and both are things the caller
        must be able to tell apart from success. `_supervise` is why: a bare `return`
        here used to be indistinguishable from a bind, so the supervisor said "comms
        online: reconnected" over a shim that was shutting down bound to nothing.
        """
        def register(client):
            with self._lock:
                # A new binding, before its first hand-over can land: the last one's
                # rows went back to waiting when it ended, and its digests with it. The
                # daemon hands the rows again, so what this shim held or queued under
                # it goes, and so do the turn ends it had not reported: the digests
                # they name have ended.
                self.queue = []
                self.held_ids = set()
                self.awaiting_digest = None
                self.steered = {}
                self._forget_turns()
                session_id, projects_root, turned = (self.session_id, self.projects_root,
                                                     self.turned)
                endpoint = self.endpoint
            return client.bind(name=self.name, scope=self.scope,
                               global_=self.global_, project=self.project,
                               adapter="claude-shim", session_id=session_id,
                               projects_root=projects_root,
                               turned=turned, endpoint=endpoint,
                               claim=self.claims.key)

        owed_ready = None

        def install(client, resp):
            nonlocal owed_ready
            self._client = client
            self._last_resp = resp

        def bound(resp):
            # After the claim file is written: when that write raises, the owed `ready`
            # and the unreported turns stay for the next bind.
            nonlocal owed_ready
            owed_ready, self.owed_ready = self.owed_ready, None

        resp = binding.register_and_install(
            self, client_type=DaemonClient, on_close=self._on_close, register=register,
            install=install, bound=bound, ledger=self.ledger, token=self.tool_token)
        if resp in (binding.STOPPED, binding.DROPPED):
            return False
        log.info("registered as %s (tier %s)", self.identity, self.tier)
        if owed_ready is not None:
            # The SessionStart that came before this bind, once, right after the register
            # answer.
            self._send_ready(owed_ready["source"])
        return True

    def stop(self, *, remove_claim_file: bool = False) -> None:
        """Close the binding, which deregisters the name, and the claim socket.

        By default the claim file stays while the Claude session lives: a shim respawned
        inside it comes back in the recorded state, and it is not live once its root has
        ended. `remove_claim_file` is for an adapter whose session has ended."""
        self._stop.set()
        self.stall.cancel()      # a session that is going away is not a stalled one
        if self._client:
            self._client.close()
        self.claims.close(remove_file=remove_claim_file)

    def _report(self, state: str, text: str) -> None:
        """One status line per STATE CHANGE, not one per attempt.

        The supervisor retries forever, so anything it says unconditionally it says
        forever. `state` is the coarse thing an operator needs -- unbound and waiting,
        bound, refused -- and a line is emitted only when it differs from the last one.
        """
        with self._lock:
            if self._last_reported_state == state:
                return
            self._last_reported_state = state
        self.status_sink(text)

    def _on_close(self) -> None:
        with self._lock:
            was_online = not self.offline
            self.offline = True
            released = self.claims.state == claimfile.STANDBY
        if released:
            return                   # standby: the name went elsewhere; nothing to say
        self.claims.went_offline()
        if was_online and not self._stop.is_set():
            # A close this shim made itself (`stop()`) is no outage.
            self._report("offline", OFFLINE_MESSAGE)

    def _supervise(self) -> None:
        """Reconnect with backoff; re-register and take replay through catch-up.

        This loop also owns the FIRST bind of a session launched while the daemon was
        down. What it does with a refusal turns on what the refusal names. A NAME
        refusal splits on whether this shim has EVER held the name: held it, and a
        successor has it, so stop; never held it, so wait. Anything else is retried on the
        backoff and said once.

        It ends only on `stop()` or the has-held stop, and records which.
        Any other exception is logged with its traceback, said once, and the loop
        carries on at its next backoff: a supervisor that died silently left a session
        offline with nothing in its status to say so.
        """
        delays = self._backoff or backoff_delays()
        try:
            while not self._stop.is_set():
                try:
                    if (self.offline and self.claims.may_register()
                            and self._attempt_held(delays) == "fresh"):
                        delays = self._backoff or backoff_delays()
                    if self.ended_because is not None:
                        return                              # the has-held stop
                    if self.offline and self.claims.may_register():
                        continue
                    self._stop.wait(0.05)
                except Exception as e:                      # noqa: BLE001 -- see docstring
                    log.exception("comms supervisor: unexpected error, retrying")
                    self._report(f"error:{type(e).__name__}",
                                 SUPERVISOR_ERROR.format(error=f"{type(e).__name__}: {e}"))
                    try:
                        wait = next(delays)
                    except StopIteration:
                        # A finite test backoff ran dry; production's never does.
                        delays = backoff_delays()
                        wait = next(delays)
                    self._stop.wait(wait)
        finally:
            if self.ended_because is None and self._stop.is_set():
                self.ended_because = "stopped"

    def _attempt_held(self, delays) -> str | None:
        """One attempt, under the claim's one-register-at-a-time lock; the launch's
        first register holding it skips this turn."""
        if not self.claims.register_lock.acquire(blocking=False):
            self._stop.wait(0.05)
            return None
        try:
            if not self.claims.may_register():
                return None
            return self._attempt(delays)
        finally:
            self.claims.register_lock.release()

    def _attempt(self, delays) -> str | None:
        """One bind attempt from the supervisor. "fresh" when the backoff re-seeds (a
        bind), None otherwise; the has-held stop records `ended_because`."""
        try:
            if not self.connect():
                # No binding was installed and nothing raised: `stop()` landed
                # mid-register, or the connection died under it. Neither is a
                # reconnect, so neither says so.
                if not self._stop.is_set():
                    self._stop.wait(next(delays))
                return None
            self._report("online", ONLINE_MESSAGE)
            return "fresh"
        except (DaemonOffline, DaemonTimeout):
            # A daemon that did not answer is a daemon that is not there.
            self._stop.wait(next(delays))
            return None
        except DaemonError as e:
            if e.try_again or e.code == wire.INTERNAL_ERROR:
                # The daemon ANSWERED, but with an accident, not a refusal: a boundary
                # read that 429'd under a restart burst, a broken record. Nobody holds
                # the name; keep the backoff going. Falling into the ever-bound arm below
                # would give the name up for good.
                log.warning("register failed transiently (%s); retrying", e)
                self._stop.wait(next(delays))
                return None
            kind = self.refusal_kind(e)
            if kind == NAME_REFUSAL and self.ever_bound:
                # Held it, and a successor now holds it. Stop competing: this process
                # stays alive and serves the session offline.
                log.warning("this session's name was claimed by another holder (%s); "
                            "no longer retrying", e)
                self.ended_because = f"name held by another session: {e}"
                self.claims.name_refused((e.data.get("holder") or {}).get("qualified"))
                self._report("refused", REFUSED_TERMINAL)
                return None
            if kind == NAME_REFUSAL:
                # Never held it. The holder is legitimate and holders release by closing
                # their connection, so keep polling -- on the SAME backoff generator,
                # deliberately not re-seeded. Re-seeding on every refusal would pin the
                # retry at BACKOFF_START forever (~2 registers/second, each taking the
                # daemon's one lock); the ceiling is the steady state.
                log.info("this session's name is held by another session (%s); still "
                         "waiting for it to free", e)
                self.claims.name_refused((e.data.get("holder") or {}).get("qualified"))
                self._report("refused", REFUSED_WAITING)
                self._stop.wait(next(delays))
                return None
            # Anything else: an invalid name, an unknown adapter kind, or
            # an error that is no refusal at all. Retried on the same backoff, said once
            # per distinct reason, in the daemon's words.
            log.warning("register refused (%s); still retrying", e)
            self._report(f"refused:{e}", REFUSED_RETRYING.format(reason=e))
            self._stop.wait(next(delays))
            return None

    # ------------------------------------------------------------------ daemon pushes
    def _on_push(self, method: str, params: dict) -> None:
        if method == "deliver":
            payload = params["payload"]
            with self._lock:
                if not self.claims.holds:
                    return       # nothing is injected addressed to a claim not held
                if payload["message_id"] in self.held_ids:
                    return
                self.held_ids.add(payload["message_id"])
                self.queue.append(payload)
        elif method == "replay_complete":
            with self._lock:
                self.replaying = False
                if params.get("truncated"):
                    self.replay_notice = params.get("notice")
            log.info("replay complete for %s at cursor %s", params.get("identity"),
                     params.get("cursor"))

    # ------------------------------------------------------------------ turn boundary
    def on_turn_boundary(self, *, idle: bool = False, failed: bool = False) -> str | None:
        """The Stop hook. Ends the turn that was running, then opens the next.

        Returns the digest text injected, or None when there was nothing to inject. A
        boundary also ends any block this shim holds on the turn: its pending
        marks are voided unless the Stop that got here already settled them.

        `failed` is a turn that did not end `answer` (the Claude adapter reads the
        outcome from turns' report of the turn): the digest it held did not complete and
        its ids go back to the daemon as `unhandled`. A block that a SessionStart
        resolves (`on_session_start`) ends here too.

        `idle` is the adapter's idle tick, which injects queued mail into a
        session standing at its prompt. It is not a turn: it ends nothing, sets no
        `turned`, and its notify carries no `kind`, so it releases nothing held.
        """
        self._clear_block()
        if idle:
            self.stall.witness()
            self.notify_activity(None)
            return self._flush()
        self.note_turned()
        self._complete_turn(failed=failed)
        return self._flush()

    # ------------------------------------------------------------------ reply enforcement
    @property
    def ledger(self) -> owed.Ledger:
        """The ledger of owed replies (`replies.ledger`)."""
        return self.replies.ledger

    @property
    def blocked(self) -> bool:
        """Whether the running turn is one this shim blocked."""
        return self.replies.blocked

    @property
    def turn_blocks(self) -> int:
        """This shim's blocks in the running turn (0 or 1)."""
        return self.replies.turn_blocks

    def _clear_block(self) -> None:
        self.replies.clear_block()

    def on_stop(self, fields: dict) -> owed.Decision:
        """The steps for one Stop: a boundary (`block` None) or a block.

        Every missing or failed fact returns a boundary with every pending mark voided;
        nothing here raises into the holder. `dropped` lists the debts counted and
        dropped at this Stop, already recorded with the daemon.
        """
        return self.replies.check(fields)

    def _query_owed(self, identity: str, authors: dict, referents: list) -> dict:
        """The daemon's `owed` query on a short connection of its own, with a 0.5 s
        timeout. Raises on any failure."""
        return binding.query_owed(self.socket_path, identity, authors, referents,
                                  client_type=DaemonClient)

    def mark_blocked(self, names) -> None:
        """The holder returned the block: the turn is held open, the named
        debts are pending, and this turn has had its one block."""
        self.replies.mark_blocked(names)

    def on_turn_failed(self) -> str | None:
        """StopFailure: a boundary that never blocks, whose digest did not complete: its
        digest is `unhandled` on the `turn_ended`, and its rows wait again."""
        return self.on_turn_boundary(failed=True)

    def on_session_start(self, source, session_id) -> bool:
        """A SessionStart. `clear`, or `resume` into another session,
        empties the debts the running context never saw. While blocked, a start with
        source startup, resume or clear resolves the turn as a full boundary (True: the
        caller has had its boundary); `compact`, or no source, resolves nothing."""
        if self.replies.session_changed(source, session_id):
            self.on_turn_boundary()
            return True
        return False

    def note_session(self, session_id: str, source: str | None = None) -> None:
        """A look or turn event named the live session. The latest one wins.

        A new id changes what `turned` is about. Only a `/clear` (a SessionStart whose
        `source` is `clear`) starts a session with an empty context, so only it lowers
        `turned`. Any other new id -- a fork or `/branch` that carries its parent's
        conversation, a resume into another session, a hook with no source -- may hold a
        context, so `turned` is set and a missing transcript reads `unknown`, never
        `fresh`: a new id is not evidence of an empty context.
        """
        with self._lock:
            if session_id != self.session_id:
                self.turned = source != "clear"
            self.session_id = session_id

    def note_turned(self) -> None:
        """A prompt or a turn end in the current session (`turned`)."""
        with self._lock:
            self.turned = True

    def notify_activity(self, kind: str | None, *, source: str | None = None,
                        ref=None) -> bool:
        """Tell the daemon the harness is alive, which session it is, and what kind of
        moment this is: `ready` (a SessionStart, with its `source`) or `look`. None
        sends no `kind`, which releases nothing. Every notify carries the session id, `projects_root` and
        `turned`, read together under the lock. `ref` names the digests a turn took
        (one, or a list): the daemon marks them started, which is not an ack. True when
        the frame went out on a connection that is still open."""
        with self._lock:
            identity, client = self.identity, self._client
            session_id, projects_root, turned = (self.session_id, self.projects_root,
                                                 self.turned)
            endpoint = self.endpoint
        if not (identity and client and client.connected):
            return False
        fields = {"turned": turned}
        if endpoint is not None:
            fields["endpoint"] = endpoint
        if source:
            fields["source"] = source
        if session_id:
            fields["session_id"] = session_id
        if projects_root:
            fields["projects_root"] = projects_root
        if ref is not None:
            fields["ref"] = ref
        client.notify(ACTIVITY_METHODS.get(kind, "activity"), {"identity": identity, **fields})
        return client.connected

    def notify_endpoint(self, present: bool) -> bool:
        """The session's turns endpoint was found (True) or went away (False): kept, and
        told to the daemon at once by an `activity` with no `kind`, which releases
        nothing. A shim not bound now tells it on its next register."""
        with self._lock:
            self.endpoint = bool(present)
        return self.notify_activity(None)

    def notify_ready(self, source: str | None) -> None:
        """A SessionStart: `activity kind=ready` with the hook's `source` (startup,
        resume, clear, compact, or whatever else Claude Code said; the daemon reads an
        unknown one as resume). One that comes while this shim is not bound is owed,
        and goes once, right after the next register answer. A hook input that
        carried no `source` sends none."""
        with self._lock:
            client = self._client
            if not (self.identity and client and client.connected):
                self.owed_ready = {"source": source}
                return
            self.owed_ready = None
        self._send_ready(source)

    def _send_ready(self, source: str | None) -> None:
        if not self.notify_activity(ACTIVITY_READY, source=source):
            with self._lock:
                self.owed_ready = {"source": source}

    def _forget_turns(self) -> None:
        """The name's unreported turn ends, when another binding starts."""
        self.unreported_handled, self.unreported_unhandled = [], []

    def _complete_turn(self, *, failed: bool = False) -> None:
        """End the turn that was running: its digest is handled, or `unhandled` when
        `failed` (its turn did not end `answer`), and `turn_ended` tells the daemon,
        which keeps the books."""
        self._settle_awaiting(failed=failed)
        self._send_turn()

    def _settle_awaiting(self, *, failed: bool = False) -> None:
        """The boundary digest's turn ended: put it on the next `turn_ended`, handled, or
        unhandled when `failed`. Sends nothing."""
        # A boundary proves the turn both started and ended, so it answers the timer too.
        self.stall.witness()
        with self._lock:
            through = self.awaiting_completion
            ids, self.awaiting_ids = self.awaiting_ids, ()
            mark, self.awaiting_mark = self.awaiting_mark, None
            digest, self.awaiting_digest = self.awaiting_digest, None
            self.awaiting_completion = None
            identity = self.identity
            self.held_ids.difference_update(ids)
            if through and not self.claims.may_advance(mark):
                # The digest went in under a hold this shim no longer has (the name
                # went elsewhere and the claim is standby): not this binding's to report.
                log.info("turn ended after %s was released; its digest is not reported",
                         identity)
            elif digest is not None and failed:
                if digest not in self.unreported_unhandled:
                    self.unreported_unhandled.append(digest)
            elif digest is not None:
                if digest not in self.unreported_handled:
                    self.unreported_handled.append(digest)

    def settle_steered(self, digest, *, handled: bool) -> bool:
        """A digest handed apart from the boundary digest settled: put it on the next
        `turn_ended`, `handled` or not. False when this shim holds no such digest (a
        new binding forgot it, or it settled already). Sends nothing."""
        with self._lock:
            entry = self.steered.pop(digest, None)
            if entry is None:
                return False
            ids, mark = entry
            self.held_ids.difference_update(ids)
            if not self.claims.may_advance(mark):
                log.info("a steered digest settled after %s was released; it is not "
                         "reported", self.identity)
                return True
            target = self.unreported_handled if handled else self.unreported_unhandled
            if digest not in target:
                target.append(digest)
        return True

    def _send_turn(self) -> None:
        """`turn_ended`, carrying every digest that ended since the last one went out.
        One that could not go out leaves them for the next, under this binding."""
        with self._lock:
            handled = list(self.unreported_handled)
            unhandled = list(self.unreported_unhandled)
            identity, client = self.identity, self._client
            session_id, projects_root, turned = (self.session_id, self.projects_root,
                                                 self.turned)
            endpoint = self.endpoint
        if not (identity and client and client.connected):
            return
        fields = {"identity": identity, "handled": handled, "unhandled": unhandled,
                  "turned": turned}
        if endpoint is not None:
            fields["endpoint"] = endpoint
        if session_id:
            fields["session_id"] = session_id
        if projects_root:
            fields["projects_root"] = projects_root
        try:
            answer = client.call("turn_ended", fields)
        except (DaemonOffline, DaemonError) as e:
            log.warning("turn end for %s not reported: %s", identity, e)
            return
        with self._lock:
            self.unreported_handled = [d for d in self.unreported_handled
                                       if d not in handled]
            self.unreported_unhandled = [d for d in self.unreported_unhandled
                                         if d not in unhandled]
        log.info("turn ended for %s: %d digest(s) handled, %d unhandled; cursor %s",
                 identity, len(handled), len(unhandled), answer.get("cursor"))

    def _open(self, batch: list) -> tuple[list, int | None]:
        """`open_digest` for a batch: the payloads the answer keeps and the digest's
        id. With the daemon gone the batch is dropped (its rows wait again with the
        binding's end); a refused or unanswered open puts it back for the next
        boundary."""
        ids = [str(p["message_id"]) for p in batch]
        with self._lock:
            client = self._client
        try:
            if client is None or not client.connected:
                raise DaemonOffline("comms offline: not connected to the daemon")
            opened = client.call("open_digest", {"ids": ids})
        except DaemonOffline as e:
            log.info("digest not opened (%s); its rows wait again for the next binding", e)
            with self._lock:
                self.held_ids.difference_update(ids)
            return [], None
        except DaemonError as e:
            log.warning("digest not opened: %s; the batch waits for the next boundary", e)
            with self._lock:
                self.queue = batch + self.queue
            return [], None
        kept = {str(mid) for mid in opened.get("ids") or ()}
        dropped = [mid for mid in ids if mid not in kept]
        if dropped:
            log.info("digest leaves out %s: withdrawn, expired or already acked",
                     ", ".join(dropped))
            with self._lock:
                self.held_ids.difference_update(dropped)
        return [p for p in batch if str(p["message_id"]) in kept], opened.get("digest")

    def _flush(self) -> str | None:
        with self._lock:
            if not self.queue:
                return None
            batch = sorted(self.queue, key=lambda p: int(p["message_id"]))
            self.queue = []
            notice, self.replay_notice = self.replay_notice, None
            identity = self.identity
        batch, digest = self._open(batch)
        if not batch:
            return None
        text = render_digest(batch, notice, self.directors, clock=self.clock)
        if self.handover is not None:
            self.handover(text, digest, steer=False)
        else:
            self.injector(text)
        high = max(int(p["message_id"]) for p in batch)
        with self._lock:
            # The ledger opens at injection, after the injector returned: the
            # model has seen these. The ledger checks the sender, the mark and the age of
            # each payload.
            now = self.clock()
            for p in batch:
                self.ledger.open(p, self.directors, self.session_id, now)
        # The injection site, and the instant this adapter knows it injected. Armed
        # after the injector returns, because an injector that raised injected nothing.
        self.stall.arm(identity, high)
        with self._lock:
            self.injected.append(text)
            self.awaiting_completion = str(high)
            self.awaiting_ids = tuple(str(p["message_id"]) for p in batch)
            self.awaiting_digest = digest
            self.awaiting_mark = self.claims.hold_mark()
            client = self._client
        if client:
            client.notify("injected", {"identity": identity, "digest": digest,
                                       "text": text})
        log.info("injected %d message(s) for %s through %s (digest %s)", len(batch),
                 identity, high, digest)
        return text

    def has_interrupt(self) -> bool:
        """Whether a queued message carries the sender's interrupt mark."""
        with self._lock:
            return any(p.get("interrupt") for p in self.queue)

    def flush_interrupt(self) -> str | None:
        """Interrupt mail into the running turn: one digest of the queued messages whose
        sender asked to interrupt, and only those, rendered with `[interrupt]` on their
        frame lines and handed with `steer`. The rest of the queue waits for the
        boundary. Returns the digest text, or None when nothing went.

        It arms no stall timer: a steer starts no turn, so no turn start would answer
        it. The digest settles on its own (`settle_steered`)."""
        with self._lock:
            batch = [p for p in self.queue if p.get("interrupt")]
            if not batch:
                return None
            self.queue = [p for p in self.queue if not p.get("interrupt")]
            batch.sort(key=lambda p: int(p["message_id"]))
            identity = self.identity
        batch, digest = self._open(batch)
        if not batch:
            return None
        text = render_digest(batch, None, self.directors, clock=self.clock, steered=True)
        if self.handover is not None:
            self.handover(text, digest, steer=True)
        else:
            self.injector(text)
        with self._lock:
            now = self.clock()
            for p in batch:
                self.ledger.open(p, self.directors, self.session_id, now)
            self.injected.append(text)
            if digest is not None:
                self.steered[digest] = (tuple(str(p["message_id"]) for p in batch),
                                        self.claims.hold_mark())
            client = self._client
        if client:
            client.notify("injected", {"identity": identity, "digest": digest,
                                       "text": text})
        log.info("steered %d interrupt message(s) for %s (digest %s)", len(batch),
                 identity, digest)
        return text

    # ------------------------------------------------------------------ helpers
    def die_mid_turn(self) -> None:
        """Simulate a crash mid-turn: the Stop hook never fires; nothing advances."""
        self.stop()


def build_parser():
    import argparse

    ap = argparse.ArgumentParser(
        prog="moxi-comms-shim",
        description="the claude-shim delivery adapter (one process, one session)")
    ap.add_argument("--comm-slot", dest="retired_slot", help=argparse.SUPPRESS)
    ap.add_argument("--name")
    ap.add_argument("--scope")
    ap.add_argument("--global", dest="global_", action="store_true")
    ap.add_argument("--project")
    ap.add_argument("--director", action="append", default=[],
                    help="an ALREADY-QUALIFIED director name (repeatable). moxi-comms-claude "
                         "qualifies at launch and passes them here; a hand-run shim must "
                         f"write `name@scope` itself. Default: ${directors_mod.ENV_VAR}")
    ap.add_argument("--inject-file",
                    help="the injection seam: write each digest to this file")
    ap.add_argument("--boundary-file", help="touch-file poll: each change = a Stop hook")
    ap.add_argument("--claim-socket",
                    help="serve this session's claim socket at this path, with the "
                         "shim's parent as the session's root. A `moxi comms send` run "
                         "inside that session with MOXI_COMMS_CLAIM_SOCKET set to it sends "
                         "as this shim's claim; without it, nothing sends as this shim's "
                         "name")
    return ap


def main(argv=None) -> int:  # pragma: no cover - exercised by the smoke runs
    """One adapter, one seam: `--inject-file` writes each digest to a file."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level="INFO", stream=sys.stderr)
    if args.retired_slot is not None:
        print("moxi-comms-shim: --comm-slot is retired; remove it: comms has no slots.",
              file=sys.stderr)
        return 2
    if not args.inject_file:
        print("moxi-comms-shim: no injection seam: pass --inject-file. The transport into "
              "a live session is the turns module's; comms carries none.", file=sys.stderr)
        return 2
    try:
        declared = directors_mod.validated(args.director or directors_mod.from_env())
    except directors_mod.BadDirector as e:
        print(f"moxi-comms-shim: {e}", file=sys.stderr)
        return 2
    if declared:
        log.info("directors declared for this session: %s", ", ".join(declared))
    common = dict(name=args.name, scope=args.scope, global_=args.global_,
                  autoreconnect=True, directors=declared)
    if args.project:
        common["project"] = args.project
    return _serve_inject_file(args, common)


def _serve_inject_file(args, common) -> int:  # pragma: no cover - the smoke run
    import json
    from pathlib import Path

    inject_path = Path(args.inject_file)

    def injector(text):
        with inject_path.open("a") as f:
            f.write(json.dumps({"digest": text}) + "\n")

    # A refused claim says one line and leaves: a traceback is never the honest report
    # of "somebody else holds this name".
    #
    # `start()` takes NO `tolerate_offline` here, deliberately. This is the hand-run
    # path, and its `EXIT_OFFLINE` is what a person at the terminal wants to see.
    claim = {}
    if args.claim_socket:
        from ..membership import TREE, root_of
        parent = root_of(os.getppid(), TREE)
        claim = dict(claim_socket=Path(args.claim_socket), listen_claims=True,
                     claim_roots=[parent] if parent else [])
    try:
        shim = ClaudeShim(injector=injector, **common, **claim).start()
    except (DaemonOffline, DaemonTimeout) as e:
        # FIRST, because `DaemonTimeout` is a `DaemonError` and the arms are tried in
        # order. This is the hand-run seam's `EXIT_OFFLINE` -- a register nobody answered
        # now reaches it instead of printing a refusal that names no holder.
        print(f"moxi-comms-shim: {e}", file=sys.stderr)
        return EXIT_OFFLINE
    except DaemonError as e:
        print(f"{REFUSED_PREFIX}: {e}", file=sys.stderr)
        return EXIT_REFUSED
    boundary = Path(args.boundary_file) if args.boundary_file else None
    seen = None
    try:
        while True:
            time.sleep(0.05)
            if boundary and boundary.exists():
                stamp = boundary.stat().st_mtime_ns
                if stamp != seen:
                    seen = stamp
                    shim.on_turn_boundary()
    except KeyboardInterrupt:
        pass
    finally:
        shim.stop()
    return 0


__all__ = ["ClaudeShim", "frame_line", "frame_marks", "render_digest", "digest_header",
           "held_mark", "humanize_age", "is_catch_up", "parse_timestamp",
           "replay_is_unverified",
           "backlog_frames",
           "OFFLINE_MESSAGE", "DEFAULT_ROOM", "REPLAY_ATTRIBUTION_NOTE", "SILENT_TIERS",
           "REFUSED_PREFIX",
           "EXIT_REFUSED", "EXIT_OFFLINE",
           "TOLERATED_OFFLINE_MESSAGE", "ONLINE_MESSAGE",
           "REFUSED_WAITING", "REFUSED_TERMINAL", "REFUSED_RETRYING",
           "SUPERVISOR_ERROR", "survive_supervisor_error",
           "NAME_REFUSAL", "OTHER_REFUSAL",
           "constants"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
