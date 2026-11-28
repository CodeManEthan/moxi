"""A chat recall: a recall for a chat in the Claude apps, started and collected by the
connector's `recall` tool (`moxi_memory.connector.recall`), beside the terminal recall
and sharing its brief, stream, log, records and takers.

**Its key.** A chat recall's records and lines are kept under the session key
`chat-<conversation>` (`key`), `<conversation>` being the label the chat makes up once
per conversation (`CONVERSATION`). The server can't tell two chats apart, so two chats
share pieces only when both make up the same label.

**The conversation's state file** (`subconscious/chat/<conversation>.json`, written under
the key's lock): each piece handed, with when; each piece dropped, with when; each line
handed, with when; the number of the last call that carried words (`last_call`), and
each such call's number and when. Entries older than `STATE_KEEP_S` are dropped when it
is written, never the last call's number. Pieces being handed by an open call are held
in this process's memory only (`_held`), so a connector restart frees them.

**Collecting (`collect`).** In one hold of the key's lock, which fixes the call's
instant: every round answer of the conversation's lines that is not `NOTHING`, not
handed, not held by another open call, and back less than `subconscious.chat.drop_after_s`
ago (an older one is recorded dropped and never handed), oldest first, at most
`PIECES_MAX` of them (the rest wait). Each piece's lines that equal a line handed in the
conversation within `HANDED_LINES_S` are cut (`recall_brief.split_recalled`'s exact-line
rule); a piece left empty is recorded handed and not returned. A piece is stale when its
recall's call number is below the last call number written before this call's instant:
a later call carried new words before this one. Then, for a call with words, the next
call number is written on the state file, whatever happens after, so this call's own
words never make a piece it hands stale. `settle` marks the pieces of an answer the
connector wrote handed, with their lines; an answer not written frees them for the next
call.

**Whether a chat recall can start (`why_not`).** The checks of `START_CHECKS`, in order,
the first that fails giving the reason: `subconscious.chat.enabled` is on (`off`); the
launcher offers interface 4 (`no-launcher`); the launcher's `check` takes a chat recall's
headless request from this process's environment (`refused`, naming the launcher's
refusal); and, asked of `moxi_memory.recall_fence.verdict`, the launcher's preview of
that request finds `claude` on its PATH (`no-harness`; any other refusal is `refused`)
and the fence holds no failed outcome for that binary (`fence`). A new check joins the
list as one entry.

**Its start (`start`).** Under the start lock (`subconscious/chat/start.lock`, the core's
lock helper), the chat recalls running over every conversation are counted; below
`CHATS_RUNNING_MAX` the recall is begun (`recall_start.begin`, start kind `chat`, route
`headless`, the words as its text, the conversation's call number and context on its
record, hurrying only the oldest of the conversation past
`subconscious.chat.running_max`), and the start lock is let go once the record is
written. At the cap, or past the call's deadline at any lock, it is `busy`. The start
lock is always taken before the key's.

**The child's chat branch (`prepare`).** The recall's child mints its agent's Claude
Code session id and writes it on the record and the line before it asks the launcher
for anything; runs the first search with no project and builds the brief:
`CHAT_PREAMBLE` with the context, then `recall_brief.brief`'s output with the
conversation's handed lines of `HANDED_LINES_S` (at most `HANDED_LINES_MAX`) as what the
assistant already has; and asks `recall_fence.ensure` (the probe, when the fence record
holds no outcome for the binary) about the very launch its first round runs: that brief,
the notes folders it names and the minted ids, with a request id of its own that is
written nowhere, since each round mints its own. A failed outcome ends the recall `lost`
with the reason `fence`, a launcher refusal `denied` (as a round's refused launch does),
and no `claude`, or no outcome, `gone`. Its route is `recall_route.Headless`, with the
same launch.

Standard library at import; the core and the launcher are reached at use.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from . import recalls, records

#: A conversation's label.
CONVERSATION = re.compile(r"[a-z0-9][a-z0-9-]{7,39}")
#: The prefix of a chat recall's session key.
KEY_PREFIX = "chat-"
#: The most chat recalls running at once over every conversation.
CHATS_RUNNING_MAX = 4
#: The most pieces one call hands over.
PIECES_MAX = 20
#: How long a handed line is cut from later pieces and told to later recalls, in seconds.
HANDED_LINES_S = 6 * 3600
#: The most handed lines a recall's brief carries.
HANDED_LINES_MAX = 40
#: How long the state file keeps an entry, in seconds.
STATE_KEEP_S = 86400
#: The start lock's file name, in the chat recall's folder.
START_LOCK = "start.lock"
#: How a piece names the words it answers, in characters.
FOR_WORDS_CHARS = recalls.HEAD_CHARS

#: Why no chat recall started (`skipped`), beside the checks' own.
NO_WORDS = "no-words"
NO_KEYWORD = "no-keyword"
OFF = "off"
BUSY = "busy"
NO_LAUNCHER = "no-launcher"
NO_HARNESS = "no-harness"
REFUSED = "refused"
FENCE = "fence"
#: Said with `no-harness`: why a connector can miss a `claude` a terminal finds.
SERVICE_PATH = "a connector run as a service may have a shorter PATH"
#: The lost reason of a recall whose launch the launcher refused, at the fence's check
#: as at a round's launch (`recall_route.Headless`).
DENIED = "denied"

#: What the chat recall's agent is told before the brief.
CHAT_PREAMBLE = """\
You were not forked from the conversation below. You work beside a voice assistant
talking with the user. Read "this session" and "the main agent" in what follows as that
assistant's conversation and that assistant. All you have of the conversation is this:
<<<
{context}
>>>
The user's words just now are the prompt below.
You have no shell and no other tools than Read, Grep and Glob{web}. Where the brief
below tells you to run a command, such as moxi memory search or moxi sessions read, you
can't: answer from the first search, and read the notes folders it names with Read,
Grep and Glob.

"""
#: The preamble's words for the web, where it is allowed.
WEB_WORDS = " (and web search and fetch, where the brief allows the web)"
#: The context's stand-in when the chat passed none.
NO_CONTEXT = "(the assistant passed no context)"


def key(conversation: str) -> str:
    """The session key a conversation's recalls are kept under."""
    if not CONVERSATION.fullmatch(conversation or ""):
        raise ValueError(f"{conversation!r} is not a conversation label")
    return KEY_PREFIX + conversation


def conversation_of(session_key: str) -> str | None:
    """The conversation a chat recall's key names, or None for any other key."""
    if not str(session_key).startswith(KEY_PREFIX):
        return None
    rest = str(session_key)[len(KEY_PREFIX):]
    return rest if CONVERSATION.fullmatch(rest) else None


def chat_dir():
    """The chat recall's folder: the agent's working folder, the state files, the
    fence record and the locks."""
    from . import recall_fence

    return recall_fence.chat_dir()


def _left(deadline: float | None) -> float | None:
    return None if deadline is None else max(deadline - time.monotonic(), 0.0)


# -- the state file -----------------------------------------------------------------------

def _state_path(conversation: str):
    key(conversation)
    return chat_dir() / f"{conversation}.json"


def read_state(conversation: str) -> dict:
    """The conversation's state, with every part present."""
    try:
        raw = json.loads(_state_path(conversation).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {"last_call": 0, "calls": {}, "pieces": {}, "dropped": {}, "lines": {}}
    last = raw.get("last_call")
    out["last_call"] = last if isinstance(last, int) and last >= 0 else 0
    for part in ("calls", "pieces", "dropped", "lines"):
        found = raw.get(part)
        if isinstance(found, dict):
            out[part] = {str(k): float(v) for k, v in found.items()
                         if isinstance(v, (int, float))}
    return out


def write_state(conversation: str, state: dict, *, now: float | None = None) -> None:
    """Write the conversation's state, its entries older than `STATE_KEEP_S` dropped
    (never `last_call`). The caller holds the key's lock."""
    from moxi import files

    now = time.time() if now is None else now
    kept = {"last_call": int(state.get("last_call") or 0)}
    for part in ("calls", "pieces", "dropped", "lines"):
        kept[part] = {k: v for k, v in (state.get(part) or {}).items()
                      if now - float(v) < STATE_KEEP_S}
    files.private_dir(chat_dir())
    files.atomic_write(_state_path(conversation), json.dumps(kept, indent=1) + "\n")


def handed_lines(state: dict, *, now: float) -> list[str]:
    """The lines handed in the conversation within `HANDED_LINES_S`, oldest first."""
    lines = sorted((at, line) for line, at in state["lines"].items()
                   if now - at < HANDED_LINES_S)
    return [line for _, line in lines]


# -- collecting ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Piece:
    """One round's answer, as a call hands it over."""

    id: str
    for_words: str
    age_s: float
    stale: bool
    text: str
    lines: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"id": self.id, "for_words": self.for_words, "age_s": round(self.age_s, 1),
                "stale": self.stale, "text": self.text}


@dataclass
class Collected:
    """What a call collected: its pieces, the number it gave its words (None for a call
    with none, or one that got no hold), how many pieces wait for a later call, and
    notes for anything held back."""

    pieces: list[Piece] = field(default_factory=list)
    call: int | None = None
    waiting: int = 0
    notes: list[str] = field(default_factory=list)
    held: bool = True


_held: dict[str, set[str]] = {}
_held_guard = threading.Lock()


def _hold(conversation: str, ids: list[str]) -> None:
    with _held_guard:
        _held.setdefault(conversation, set()).update(ids)


def release(conversation: str, ids) -> None:
    """Free `ids`, held by an open call, for the next call."""
    with _held_guard:
        _held.get(conversation, set()).difference_update(ids)


def _held_now(conversation: str) -> frozenset[str]:
    with _held_guard:
        return frozenset(_held.get(conversation, ()))


def _strip_end(text: str) -> str:
    from .recall_stream import END_LINE

    lines = text.rstrip().splitlines()
    if lines and lines[-1].strip() == END_LINE:
        lines = lines[:-1]
    return "\n".join(lines).strip()


def collect(conversation: str, *, has_words: bool, drop_after_s: float,
            deadline: float | None = None, now: float | None = None) -> Collected:
    """Collect the conversation's pieces, and number the call's words when it has any.
    See the module's docstring. A hold of the key's lock not got by `deadline` collects
    nothing (`held` False) and gives the words no number."""
    from moxi import files

    from .recall_brief import split_recalled
    from .recall_stream import NOTHING

    now = time.time() if now is None else now
    session_key = key(conversation)
    try:
        with recalls.session_lock(session_key, timeout_s=_left(deadline)):
            state = read_state(conversation)
            last = state["last_call"]
            held = _held_now(conversation)
            handed = set(handed_lines(state, now=now))
            found: list[tuple[float, Piece]] = []
            for line in recalls.read_lines(session_key):
                recall_id = line.get("recall")
                if not recall_id:
                    continue
                call = line.get("call")
                stale = isinstance(call, int) and call < last
                for entry in line.get("rounds") or []:
                    text = entry.get("text") if isinstance(entry, dict) else None
                    at = entry.get("at") if isinstance(entry, dict) else None
                    if not isinstance(text, str) or not isinstance(at, (int, float)):
                        continue
                    piece_id = f"{recall_id}:{entry.get('round')}"
                    if (piece_id in state["pieces"] or piece_id in state["dropped"]
                            or piece_id in held):
                        continue
                    body = _strip_end(text)
                    if not body or body == NOTHING:
                        state["pieces"][piece_id] = now
                        continue
                    if now - at >= drop_after_s:
                        state["dropped"][piece_id] = now
                        continue
                    kept, _cut = split_recalled(body, handed)
                    kept = kept.strip()
                    if not kept:
                        state["pieces"][piece_id] = now
                        continue
                    found.append((float(at), Piece(
                        piece_id, str(line.get("head") or "")[:FOR_WORDS_CHARS],
                        max(now - float(at), 0.0), stale, kept,
                        tuple(t.strip() for t in kept.splitlines() if t.strip()))))
            found.sort(key=lambda item: item[0])
            pieces = [piece for _, piece in found[:PIECES_MAX]]
            number = None
            if has_words:
                number = last + 1
                state["last_call"] = number
                state["calls"][str(number)] = now
            write_state(conversation, state, now=now)
            _hold(conversation, [p.id for p in pieces])
    except files.LockTimeout:
        return Collected(notes=["the conversation was busy, so nothing was collected; "
                                "the pieces wait for the next call"], held=False)
    waiting = len(found) - len(pieces)
    notes = [f"{waiting} more pieces wait for the next call"] if waiting else []
    return Collected(pieces, number, waiting, notes)


def settle(conversation: str, pieces, *, written: bool, now: float | None = None,
           timeout_s: float | None = None) -> None:
    """Settle a call's `pieces` once its answer is written or not: written, each is
    marked handed with its lines; not, they wait for the next call. Frees them either
    way."""
    ids = [p.id for p in pieces]
    try:
        if written and pieces:
            now = time.time() if now is None else now
            with recalls.session_lock(key(conversation), timeout_s=timeout_s):
                state = read_state(conversation)
                for piece in pieces:
                    state["pieces"][piece.id] = now
                    for line in piece.lines:
                        state["lines"][line] = now
                write_state(conversation, state, now=now)
    finally:
        release(conversation, ids)


# -- whether a chat recall can start ------------------------------------------------------

@dataclass(frozen=True)
class Skip:
    """Why no chat recall starts: `reason`, with `detail` for the notes."""

    reason: str
    detail: str = ""


@dataclass
class Context:
    """What the start checks read: memory's settings, and what earlier checks found
    (the launcher, the launch they ask about, and the fence's verdict)."""

    settings: Mapping[str, object]
    env: Mapping[str, str] | None = None
    launcher: Any = None
    spec: Any = None
    verdict: Any = None


def check_spec(settings: Mapping[str, object]):
    """A chat recall's launch as the checks ask about it: ids of its own, so nothing is
    taken."""
    from . import recall_fence

    return recall_fence.Spec(brief="check", cwd=str(chat_dir()), name="recall-check",
                             request_id=str(uuid.uuid4()), session_id=str(uuid.uuid4()),
                             max_tools=int(settings["subconscious.max_tools"]),
                             model=str(settings["subconscious.chat.model"] or ""))


def check_enabled(ctx: Context) -> Skip | None:
    if not ctx.settings["subconscious.chat.enabled"]:
        return Skip(OFF, "the chat recall is off: moxi config set memory "
                         "subconscious.chat.enabled true turns it on")
    return None


def check_launcher(ctx: Context) -> Skip | None:
    from . import links

    link = links.launcher_headless()
    if not link.ready:
        return Skip(NO_LAUNCHER, link.reason or "the launcher doesn't offer interface 4")
    ctx.launcher = link.api
    return None


def _refused(verdict) -> Skip:
    """The skip for `recall_fence.refusal_verdict`'s verdict."""
    from . import recall_fence

    if verdict.reason == recall_fence.NO_HARNESS:
        return Skip(NO_HARNESS, f"{verdict.detail} ({SERVICE_PATH})")
    return Skip(REFUSED, verdict.detail)


def check_harness(ctx: Context) -> Skip | None:
    """The launcher's `check` takes a chat recall's launch in its folder, made first:
    before the first call nothing has, and the launcher refuses a working folder that
    isn't there. Its first refusal is the skip; a check that fails with anything but a
    refusal is `refused` as well, said as the check failing."""
    from moxi import files
    from moxi.store import StoreError

    from . import recall_fence

    files.private_dir(chat_dir())
    ctx.spec = check_spec(ctx.settings)
    try:
        refusals = ctx.launcher.check(recall_fence.request(ctx.launcher, ctx.spec),
                                      env=ctx.env)
    except (ctx.launcher.Refusal, StoreError) as exc:
        refusals = [exc]
    except Exception as exc:  # noqa: BLE001 - a check that breaks lets no recall start
        return Skip(REFUSED, f"the launcher's check failed: {type(exc).__name__}: {exc}")
    if refusals:
        return _refused(recall_fence.refusal_verdict(refusals[0]))
    return None


def check_fence(ctx: Context) -> Skip | None:
    from . import recall_fence

    found = recall_fence.verdict(ctx.launcher, ctx.spec, env=ctx.env)
    ctx.verdict = found
    if found.reason in (recall_fence.NO_HARNESS, recall_fence.REFUSED):
        return _refused(found)
    if found.reason is not None:
        return Skip(FENCE, f"the fence failed on this machine: {found.detail}")
    return None


#: The checks a chat recall's start passes, in order; the first that fails is the reason.
START_CHECKS: tuple[Callable[[Context], Skip | None], ...] = (
    check_enabled, check_launcher, check_harness, check_fence)


def checked(settings: Mapping[str, object], *, env: Mapping[str, str] | None = None,
            checks=START_CHECKS) -> tuple[Skip | None, Context]:
    """The checks run in order to the first that fails: (why no chat recall can start
    now, or None when one can; what the checks found)."""
    ctx = Context(settings, env)
    for check in checks:
        found = check(ctx)
        if found is not None:
            return found, ctx
    return None, ctx


def why_not(settings: Mapping[str, object], *, env: Mapping[str, str] | None = None,
            checks=START_CHECKS) -> Skip | None:
    """Why no chat recall can start now, or None when one can."""
    return checked(settings, env=env, checks=checks)[0]


# -- the start ----------------------------------------------------------------------------

def running_chats() -> int:
    """How many chat recalls run now over every conversation."""
    return sum(running(s) for s in records.session_ids() if conversation_of(s))


def running(session_key: str) -> int:
    """How many recalls under `session_key` run now."""
    return sum(1 for r in records.session_records(session_key)
               if r.get("state") not in records.DONE)


def started_since(at: float) -> int:
    """How many chat recalls, over every conversation, the log has opened since `at`."""
    return sum(1 for s in recalls.logged_sessions() if conversation_of(s)
               for line in recalls.read_lines(s)
               if line.get("recall") and float(line.get("at") or 0) >= at)


_start_gate = threading.Lock()


@contextmanager
def start_lock(deadline: float | None = None) -> Iterator[None]:
    """Hold the start lock for the block: this process's threads queue for it first,
    then the file lock holds it across processes. Past `deadline`,
    `moxi.files.LockTimeout`."""
    from moxi import files

    files.private_dir(chat_dir())
    path = chat_dir() / START_LOCK
    left = _left(deadline)
    if not _start_gate.acquire(timeout=-1 if left is None else left):
        raise files.LockTimeout(path, os.getpid())
    try:
        with files.lock(path, timeout_s=_left(deadline)):
            yield
    finally:
        _start_gate.release()


def start(conversation: str, words: str, context: str, *, call: int,
          settings: Mapping[str, object], deadline: float | None = None,
          spawn=None) -> tuple[dict | None, Skip | None]:
    """Begin a chat recall for `words` under the start lock: (its record, None), or
    (None, `busy`) at the cap or past `deadline`."""
    from moxi import files

    from . import recall_start

    held = recall_start.subconscious_settings(settings)
    keep = int(settings["subconscious.chat.running_max"]) - 1
    try:
        with start_lock(deadline):
            if running_chats() >= CHATS_RUNNING_MAX:
                return None, Skip(BUSY, f"{CHATS_RUNNING_MAX} chat recalls already run")
            record = recall_start.begin(
                key(conversation), "claude", start=recalls.CHAT, text=words, senders=(),
                settings=held, cwd=None, notes_dir=None,
                route_name=recall_start.HEADLESS, keep_running=keep,
                lock_s=_left(deadline), chat={"context": context, "call": call},
                spawn=spawn)
    except files.LockTimeout:
        return None, Skip(BUSY, "another call held the start past this call's budget")
    return record, None


# -- the child's chat branch --------------------------------------------------------------

def preamble(context: str, *, allows_web: bool = False) -> str:
    """`CHAT_PREAMBLE` for `context`."""
    return CHAT_PREAMBLE.format(context=(context or "").strip() or NO_CONTEXT,
                                web=WEB_WORDS if allows_web else "")


def chat_brief(recall, *, search=None, places=None) -> tuple[str, list[str]]:
    """The chat recall's brief and the notes folders it names: the first search with no
    project, `CHAT_PREAMBLE`, and `recall_brief.brief` with the conversation's handed
    lines as what the assistant already has. Noted on the line and the record."""
    from . import recall_brief

    record = recall.record
    text = str(record.get("text") or "")
    conversation = conversation_of(recall.session_id)
    recalled = None
    if conversation is not None:
        lines = handed_lines(read_state(conversation), now=time.time())
        recalled = lines[-HANDED_LINES_MAX:] or None
    found = recall_brief.prefetch(text, session_id=recall.session_id, cwd=None,
                                  notes_dir=None, scope=recall_brief.EVERYWHERE,
                                  hits=int(recall.settings["prefetch_hits"]),
                                  search=search, places=places)
    body = recall_brief.brief(start=recalls.CHAT, text=text, session_id=recall.session_id,
                              found=found, settings=recall.settings, recalled=recalled)
    text_brief = preamble(str(record.get("context") or "")) + body
    counts = found.counts()
    recall.put(record={"brief": text_brief}, prefetch_hits=found.count,
               project_hits=counts[recall_brief.PROJECT_PART],
               beyond_hits=counts[recall_brief.BEYOND_PART])
    return text_brief, list(found.notes_dirs)


def prepare(recall, *, launcher=None, search=None, places=None, ensure=None):
    """The child's chat branch before its stream: the agent's session id minted and
    written first, the brief built, and the fence ensured for the launch the first round
    runs. Returns (the brief, the route), or None when the recall was ended `lost`."""
    from . import links, recall_child, recall_fence, recall_route
    from .recall_stream import GONE

    session = str(uuid.uuid4())
    recall.put(record={"headless_session": session}, headless_session=session,
               call=recall.record.get("call"))
    if launcher is None:
        link = links.launcher_headless()
        if not link.ready:
            recall_child.lose(recall, GONE, link.reason or "no launcher with interface 4")
            return None
        launcher = link.api
    chat_dir().mkdir(parents=True, exist_ok=True)
    text_brief, notes_dirs = chat_brief(recall, search=search, places=places)
    settings = recall.settings
    spec = recall_fence.Spec(
        brief=text_brief, cwd=str(chat_dir()), name=f"recall-{recall.recall_id}",
        request_id=str(uuid.uuid4()), session_id=session,
        max_tools=int(settings["max_tools"]), add_dirs=tuple(notes_dirs),
        model=str(settings.get("chat.model") or ""), recall_id=recall.recall_id)
    verdict = (ensure or recall_fence.ensure)(launcher, spec)
    if verdict.reason == recall_fence.FENCE:
        recall_child.lose(recall, recalls.FENCE, verdict.detail)
        return None
    if verdict.reason == recall_fence.REFUSED:
        recall_child.lose(recall, DENIED, f"the launcher: {verdict.detail}")
        return None
    if not verdict.ok:
        recall_child.lose(recall, GONE, verdict.detail)
        return None
    route = recall_route.Headless(recall.session_id, recall.recall_id, spec=spec,
                                  launcher=launcher,
                                  on_request=lambda rid: recall.put(request=rid))
    return text_brief, route


__all__ = ["CONVERSATION", "KEY_PREFIX", "CHATS_RUNNING_MAX", "PIECES_MAX", "HANDED_LINES_S",
           "HANDED_LINES_MAX", "STATE_KEEP_S", "START_LOCK", "FOR_WORDS_CHARS", "NO_WORDS",
           "NO_KEYWORD", "OFF", "BUSY", "NO_LAUNCHER", "NO_HARNESS", "REFUSED", "FENCE",
           "SERVICE_PATH", "DENIED",
           "CHAT_PREAMBLE", "WEB_WORDS", "NO_CONTEXT", "key", "conversation_of",
           "chat_dir", "read_state", "write_state", "handed_lines", "Piece", "Collected",
           "release", "collect", "settle", "Skip", "Context", "check_spec", "check_enabled",
           "check_launcher", "check_harness", "check_fence", "START_CHECKS", "checked",
           "why_not", "running_chats", "running", "started_since", "start_lock", "start", "preamble", "chat_brief", "prepare"]
