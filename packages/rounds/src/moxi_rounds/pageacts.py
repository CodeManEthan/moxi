"""What the operator does on rounds' page, run in process by its page server: the
answers sent from the page's tray (`send`), the drop of one build (`drop`) and what the
drop's confirm says (`drop_facts`), and the receipts each act is answered with.

**Who records an answer.** An answer that needs no wording is recorded by the page when
the operator sends it, through rounds' own calls, each under the round's lock with a
wait of `WAIT_S`, `actors.default()` as who recorded it and a page pointer of its own,
`page:rounds/<act id>@<UTC second>`: an option picked (`PICK`), a decision accepted or
rejected (`ACCEPT`, `REJECT`), a ruling read back (`READ_BACK`), a job marked done
(`DONE`), a question parked with nothing to wait for (`PARK`), and a parking taken back
(`UNPARK`, a ruling of `revoke:` the parking that stands). An answer in the operator's
own words (`WORDS`), a correction of a ruling (`CORRECT`) and a request to explain
(`EXPLAIN`) are recorded by nobody here: they go, quoted, in the message to the agent,
who words and records them with that message as the pointer. The operator's words are
quoted once, line by line, and never put in a command: the command the agent runs takes
its own wording in place of `TEXT`, so no shell it is pasted into reads the words.

**Who is told.** One message per recipient, sent under rounds' send-only name with
comms' `send_as`. A draft's recipient is its round's lead (its own, else its project's),
a project's item its project's lead, or the one agent the operator picked. A draft with
no recipient goes through nothing: nothing is recorded for it and no message names it,
and it is answered `KEPT`. With comms not installed nothing is sent, so no recipient is
needed: the page records its own kinds, and the agent's kinds are kept with the command
a lead would run.

**In this order:** the page records its drafts, in the order given; then it sends the
messages. A record refused leaves that draft out of its message and answered `REFUSED`
with rounds' reason and fix; the drafts after it go on. A record whose message comms
refused is answered `NOT_TOLD`, and sent again (a draft carrying its `tell` id) it
records nothing: its record is read from the log by its act id and named in the new
message. An agent's draft whose message comms refused is answered `REFUSED` with comms'
words, and stays a draft.

**An act posted twice runs once.** `Receipts` keeps the receipt of every act that did
something (`RECORDED`, `NOT_TOLD` or `SENT`) by its act id, or its `tell` id for one sent
again, for the life of the process, and answers a repeat with it, recording nothing and
sending nothing again; a repeat that comes while the first runs waits for it. A
`REFUSED` or `KEPT` act changed nothing and is not kept, so it runs again. After a
restart the store is empty, and the log is the guard: before each call, the page looks
in the item's log for an event whose pointer holds the draft's act id, and one found is
that draft's success, recorded once. Its message may go again; a message names each act
by id, so a second message about one act is noise and never a second act.

Nothing here imports loopback: the page server's handlers (`pagehandlers`) call these
functions, and the tests call them with a stand-in for comms.
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import (actors, builds, checking, ci_refs, corrections, dropping, folders, leads, log,
               pointers, questions, removal, ruling, shown, telling, waits, write)
from . import settings as own
from . import state as states
from .errors import BadName, Locked, NoLog, NoRound, Refused, RoundsError, UnfencedTest, Unmet
from .gitreads import Git

#: How long a page act waits for a round's lock, in seconds; a lock held longer is a
#: refusal that says the round is busy.
WAIT_S = 5.0
#: The page a pointer names.
PAGE_NAME = "rounds"
#: An act id: what the browser mints per draft (43 characters), within what a page
#: pointer takes.
ACT_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
#: The To choice that sends each draft to its own round's lead.
EACH = "each"

# The kinds of draft.
PICK, ACCEPT, REJECT, READ_BACK, DONE, PARK, UNPARK = (
    "pick", "accept", "reject", "read-back", "done", "park", "unpark")
WORDS, CORRECT, EXPLAIN = "words", "correct", "explain"
#: The kinds the page records on Send.
PAGE_KINDS = (PICK, ACCEPT, REJECT, READ_BACK, DONE, PARK, UNPARK)
#: The kinds the chosen agent words and records, or that need no record.
AGENT_KINDS = (WORDS, CORRECT, EXPLAIN)
KINDS = PAGE_KINDS + AGENT_KINDS

# What became of a draft.
RECORDED, NOT_TOLD, SENT, REFUSED, KEPT = (
    "recorded", "recorded-not-told", "sent", "refused", "kept")
#: The answers after which something happened: only these are kept for a repeat.
_KEPT_ANSWERS = (RECORDED, NOT_TOLD, SENT)

#: Where the command an agent runs names its message, before the message has an id.
THIS_MESSAGE = "comms:<this message>"
#: Where the command an agent runs takes the agent's wording of the operator's words.
TEXT = "TEXT"
#: The lines a message ends with when it holds nothing for the agent to record.
NOTHING_TO_RECORD = "Nothing for you to record."


@dataclass(frozen=True)
class Draft:
    """One answer from the tray: its `act` id, the `holder` (`<project>/<slug>`, or a
    project's key), the `item` it answers (`Q<n>`, `P<n>`, `C<n>`, `S<n>`, `B<n>.M<k>`,
    `B<n>.W<k>`), its `kind`, the option it `chose`, the operator's `text` (own words, a
    correction, a done job's note, or what to explain), and for a draft sent again its
    `tell` id."""

    act: str
    holder: str
    item: str
    kind: str
    chose: str | None = None
    text: str | None = None
    tell: str | None = None

    @property
    def key(self) -> str:
        """What its receipt is kept under: the `tell` id of a draft sent again, else
        its act id."""
        return self.tell or self.act


class Receipts:
    """The receipts of the acts that did something, by act id, kept in memory for the
    life of the process and never written; and one lock per id while its act runs."""

    def __init__(self):
        self._lock = threading.Lock()
        self._kept: dict[str, dict] = {}
        self._running: dict[str, threading.Lock] = {}

    def get(self, key: str) -> dict | None:
        with self._lock:
            return self._kept.get(key)

    def keep(self, key: str, receipt: Mapping) -> None:
        """Keep `receipt` under `key` when the act did something. A draft sent again
        whose message was refused again did nothing, so it is not kept and its next
        try runs."""
        if receipt.get("state") == NOT_TOLD and receipt.get("tell"):
            return
        if receipt.get("state") in _KEPT_ANSWERS:
            with self._lock:
                self._kept[key] = dict(receipt)

    def clear(self) -> None:
        """Forget every receipt, as a restart of the server does."""
        with self._lock:
            self._kept.clear()

    @contextlib.contextmanager
    def holding(self, keys: Sequence[str]) -> Iterator[None]:
        """Run the block while no other act with one of `keys` runs: a repeat that
        comes while its first post runs waits for it."""
        with self._lock:
            locks = [self._running.setdefault(key, threading.Lock())
                     for key in sorted(set(keys))]
        for one in locks:
            one.acquire()
        try:
            yield
        finally:
            for one in reversed(locks):
                one.release()


@dataclass
class _Held:
    """A draft's holder as read now: its log's state, the project's log's state, its
    rounds folder and the lead to tell."""

    state: states.State
    register: states.State | None
    lead: str | None


@dataclass
class _Done:
    """What happened to one draft as the send goes."""

    draft: Draft
    receipt: dict = field(default_factory=dict)
    to: str | None = None
    recorded: bool = False
    line: str | None = None


# -- reading what a draft names ------------------------------------------------------------

def _holder(holder: str) -> tuple[str, str]:
    """The project and the slug of `holder`, a round's name (`<project>/<slug>`), or a
    project's key with an empty slug. Raises `BadName` for a name that is neither, so no
    read goes outside the project's rounds folder."""
    if "/" in holder:
        return folders.split(holder)
    if not holder or holder in (".", ".."):
        raise BadName(holder)
    return holder, ""


def _logs(holder: str, settings) -> tuple[Any, Any]:
    """The path of `holder`'s log and of its project's log. Raises `BadName` for a holder
    that is not a round's name or a project's key."""
    project, slug = _holder(holder)
    home = folders.home(project, settings)
    if slug:
        return log.round_log(home.round_dir(slug)), log.project_log(home.rounds_dir)
    return log.project_log(home.rounds_dir), log.project_log(home.rounds_dir)


def _read(holder: str, settings) -> _Held:
    """`holder`'s log and its project's, as they read now, with the lead to tell.
    Raises what finding them raises."""
    project, slug = _holder(holder)
    home = folders.home(project, settings)
    own_log, project_log = _logs(holder, settings)
    try:
        events = log.read(own_log)
    except NoLog:
        raise NoRound(holder) from None
    state = states.fold(events)
    register = state if not slug else _fold(project_log)
    if slug:
        lead = leads.of(state, home.lead)
        return _Held(state, register, None if lead is None else lead["name"])
    return _Held(state, register, home.lead)


def _fold(path) -> states.State | None:
    try:
        return states.fold(log.read(path))
    except (OSError, RoundsError, ValueError):
        return None


def on_record(holder: str, act: str, settings) -> dict | None:
    """The newest event of `holder`'s log, or its project's, whose page pointer holds
    the act id `act`: the record that act made, or None."""
    for path in dict.fromkeys(_logs(holder, settings)):
        try:
            events = log.read(path)
        except (OSError, RoundsError, ValueError):
            continue
        for event in reversed(events):
            if pointers.page_act(event.get("record")) == act:
                return event
    return None


def _open_left(state: states.State) -> tuple[int, set[str] | None]:
    """How many questions read open, and the stages they hold (None: every stage)."""
    held: set[str] | None = set()
    n = 0
    for question in state.questions:
        if questions.current(question, state) != questions.OPEN:
            continue
        n += 1
        if question.blocks == "all":
            held = None
        elif held is not None and isinstance(question.blocks, (list, tuple)):
            held.update(question.blocks)
    return n, held


def _parking(state: states.State, item: str, holder: str) -> str:
    """The ruling that parks `item`, the parking `UNPARK` takes back. Raises `Refused`
    when the question is not parked by a ruling."""
    question = questions.find(state, item)
    if question is None:
        raise Refused([_unmet(f"{holder} holds no question {item}",
                              f"moxi rounds show {holder}")])
    put = corrections.put_by(question)
    if question.state != questions.PARKED or put is None:
        raise Refused([_unmet(f"{item} of {holder} is not parked by a ruling, so there is "
                              "no parking to take back", f"moxi rounds show {holder}")])
    return put


def _unmet(reason: str, fix: str) -> Unmet:
    return Unmet(reason, fix)


# -- recording one draft -------------------------------------------------------------------

def pointer(act: str, at: str) -> str:
    """The page pointer of one act, at the handler's UTC second."""
    return f"page:{PAGE_NAME}/{act}@{at}"


def _facts(event: Mapping, draft: Draft) -> dict:
    """What a record says, as a receipt carries it, from the event it wrote."""
    made: dict[str, Any] = {"record": event.get("record"), "at": event.get("at")}
    kind = event.get("type")
    if kind == "ruling.recorded":
        made["ruling"] = {"id": event.get("id"), "text": event.get("text") or event.get("rules")}
    elif kind == "ruling.shown":
        made["marked"] = [str(one) for one in event.get("ids") or [draft.item]]
    elif kind == "wait.marked":
        made["done"] = draft.item
    elif kind == "build.dropped":
        made["dropped"] = event.get("stage")
    return made


def _record(draft: Draft, held: _Held, at: str, *, by: str, settings) -> dict:
    """Record one draft of the page's kinds, or find the record its act id made; its
    receipt's facts. Raises a `RoundsError` for a refusal."""
    found = on_record(draft.holder, draft.act, settings)
    if found is not None:
        return {**_facts(found, draft), "again": True}
    ptr = pointer(draft.act, at)
    common = {"record": ptr, "by": by, "wait_s": WAIT_S, "settings": settings}
    if draft.kind in (PICK, ACCEPT, REJECT, PARK, UNPARK):
        if draft.kind == PICK:
            if not draft.chose:
                raise Refused([_unmet(f"a pick of {draft.item} names no option",
                                      "pick one of the question's options")])
            ruled = ruling.rule(draft.holder, item=draft.item, chose=draft.chose, **common)
        elif draft.kind in (ACCEPT, REJECT):
            ruled = ruling.rule(draft.holder, item=f"{draft.item}={draft.kind}", **common)
        elif draft.kind == PARK:
            ruled = ruling.rule(draft.holder, item=f"park:{draft.item}", **common)
        else:
            parking = _parking(held.state, draft.item, draft.holder)
            ruled = ruling.rule(draft.holder, item=f"revoke:{parking}", **common)
        return {"record": ptr, "at": ruled.event.get("at"),
                "ruling": {"id": ruled.id, "text": ruled.text or ruled.event.get("rules")}}
    if draft.kind == READ_BACK:
        marked = shown.mark(draft.holder, draft.item, **common)
        return {"record": ptr, "at": at, "marked": list(marked.marked or marked.already),
                "already": not marked.marked}
    with write.watching() as writes:
        done = waits.done(draft.holder, draft.item, **common)
    return {"record": ptr, "at": at, "done": draft.item,
            "closed": any(one.closed is not None for one in writes),
            "already": done.found}


def _freed(draft: Draft, before: _Held, settings) -> dict:
    """What a question's answer freed: the stages it held that no open question holds
    now, and how many questions are left open in its log."""
    if draft.kind not in (PICK, PARK, UNPARK):
        return {}
    try:
        after = _read(draft.holder, settings)
    except RoundsError:
        return {}
    left, held = _open_left(after.state)
    question = questions.find(before.state, draft.item)
    blocks = question.blocks if question is not None else None
    freed = ([one for one in blocks if held is not None and one not in held]
             if isinstance(blocks, (list, tuple)) and draft.kind != UNPARK else [])
    return {"left": left, "freed": freed}


def _refusal(exc: RoundsError) -> dict:
    """A refusal as a receipt carries it: rounds' reasons and fixes, and whether the
    round was busy."""
    if isinstance(exc, Refused):
        reason = "; ".join(one.reason for one in exc.unmet)
        fix = next((one.fix for one in exc.unmet if one.fix), None)
    else:
        reason, fix = exc.reason, exc.fix
    busy = isinstance(exc, Locked)
    if busy:
        reason = f"the round is busy: another act holds its lock ({exc.reason})"
    return {"state": REFUSED, "reason": reason, "fix": fix, "busy": busy}


# -- the message ---------------------------------------------------------------------------

def quoted(text: str | None, indent: str = "    ") -> str:
    """The operator's `text` quoted as written, each of its lines on a line of its own
    after `indent` and `> `, for a message to read, never for a command line."""
    return "\n".join(f"{indent}> {line}" for line in (text or "").splitlines() or [""])


def command(draft: Draft, held: _Held | None, record: str) -> str | None:
    """The command that records an own-words answer or a correction, with `record` the
    pointer and `TEXT` where the agent's wording of the operator's words goes; None for
    any other kind. It holds only names and ids rounds checks, so it reads the same in
    every shell."""
    if draft.kind == WORDS:
        return f"moxi rounds rule {draft.holder} {draft.item} --text {TEXT} --record {record}"
    if draft.kind == CORRECT:
        ruled = None if held is None else corrections.find(held.state, draft.item)
        rules = f" {ruled.rules}" if ruled is not None and ruled.rules else ""
        return (f"moxi rounds rule {draft.holder}{rules} --text {TEXT} "
                f"--supersedes {draft.item} --record {record}")
    return None


def _recorded_line(done: _Done) -> str:
    draft, receipt = done.draft, done.receipt
    where = f"{draft.holder} {draft.item}"
    if draft.kind in (PICK, ACCEPT, REJECT):
        ruled = receipt.get("ruling") or {}
        said = f"recorded as {ruled.get('id')}: {ruled.get('text')}"
        return f"- {where}: {draft.kind}, {said} ({receipt.get('record')})"
    if draft.kind == PARK:
        return f"- {where}: parked, as {(receipt.get('ruling') or {}).get('id')} ({receipt.get('record')})"
    if draft.kind == UNPARK:
        return (f"- {where}: unparked, open again, as {(receipt.get('ruling') or {}).get('id')} "
                f"({receipt.get('record')})")
    if draft.kind == READ_BACK:
        return f"- {where}: marked as read back ({receipt.get('record')})"
    note = f"\n  The operator's note:\n{quoted(draft.text)}" if draft.text else ""
    closed = " The round closed." if receipt.get("closed") else ""
    return f"- {where}: marked done ({receipt.get('record')}).{closed}{note}"


def message_text(done: Sequence[_Done], helds: Mapping[str, _Held]) -> str:
    """The one message to a recipient: what the page recorded, each by id; each answer
    in the operator's own words and each correction, quoted line by line, with the
    command that records the agent's wording of it with this message as the pointer;
    then each request to explain, with the operator's words quoted the same way."""
    recorded = [one for one in done if one.recorded]
    words = [one for one in done if one.draft.kind in (WORDS, CORRECT)]
    explain = [one for one in done if one.draft.kind == EXPLAIN]
    lines = ["The operator answered on the rounds page."]
    if recorded:
        lines += ["", "Recorded by the page:"] + [_recorded_line(one) for one in recorded]
    if words:
        lines += ["", "In the operator's own words, for you to word and record, with this "
                      f"message as the pointer; {TEXT} in each command is your wording of "
                      "the words quoted above it:"]
        for one in words:
            draft = one.draft
            if draft.kind == WORDS:
                lines.append(f"- {draft.holder} {draft.item}, in the operator's words:")
            else:
                lines.append(f"- {draft.holder} {draft.item} is not what the operator said, "
                             "who said:")
            lines.append(quoted(draft.text))
            lines.append(f"  {command(draft, helds.get(draft.holder), THIS_MESSAGE)}")
    if explain:
        lines += ["", "The operator asks you to explain:"]
        for one in explain:
            draft = one.draft
            question = None
            held = helds.get(draft.holder)
            if held is not None:
                question = questions.find(held.state, draft.item)
            asked = f": {question.text}" if question is not None and question.text else ""
            lines.append(f"- {draft.holder} {draft.item}{asked}")
            if draft.text:
                lines += ["  The operator adds:", quoted(draft.text)]
    if not words:
        lines += ["", NOTHING_TO_RECORD]
    return "\n".join(lines)


def tell(comms: Any, to: str, text: str, at: str) -> dict:
    """Send `text` to `to` under rounds' send-only name: `{"to", "at", "id", "delivery"}`
    when comms took it, else `{"to", "at", "refused"}` with comms' words."""
    try:
        result = comms.send_as(telling.MODULE, telling.SENDER, [to], text)
    except UnfencedTest:
        raise
    except Exception as exc:  # noqa: BLE001 - a send that raises is said, not raised
        return {"to": to, "at": at, "refused": str(exc) or type(exc).__name__}
    if getattr(result, "outcome", None) == "sent":
        recipients = getattr(result, "recipients", ()) or ()
        delivery = getattr(recipients[0], "delivery", None) if recipients else None
        return {"to": to, "at": at, "id": getattr(result, "message_id", None),
                "delivery": delivery}
    refusal = getattr(result, "refusal", None)
    code = getattr(refusal, "code", None)
    words = getattr(refusal, "text", None) or code or str(getattr(result, "outcome", None))
    return {"to": to, "at": at,
            "refused": f"{code}: {words}" if code and words != code else words}


# -- the send ------------------------------------------------------------------------------

def _no_lead(holder: str) -> dict:
    return {"state": KEPT, "reason": f"No lead for {holder}: pick an agent to send it to",
            "fix": None}


def _base(draft: Draft) -> dict:
    return {"act": draft.act, "tell": draft.tell, "holder": draft.holder, "item": draft.item,
            "kind": draft.kind}


def send(drafts: Sequence[Draft], to: str, *, receipts: Receipts, comms: Any,
         settings=None, at: str | None = None, by: str | None = None) -> dict:
    """Send the tray's `drafts` to `to` (`EACH`, or one agent's name), with `comms`
    comms' API or None for none installed: record the page's drafts, then send one
    message per recipient. Returns `{"receipts": [...], "messages": [...]}`, a receipt
    per draft in the order given and each message sent or refused."""
    settings = own.load() if settings is None else settings
    at = log.stamp() if at is None else at
    with receipts.holding([one.key for one in drafts]):
        done = [_Done(one) for one in drafts]
        helds: dict[str, _Held] = {}
        for one in done:
            kept = receipts.get(one.draft.key)
            if kept is not None:
                one.receipt = {**kept, "repeat": True}
        fresh = [one for one in done if not one.receipt]
        actor = None
        for one in fresh:
            draft = one.draft
            try:
                held = helds.get(draft.holder) or _read(draft.holder, settings)
            except RoundsError as exc:
                one.receipt = {**_base(draft), **_refusal(exc)}
                continue
            helds[draft.holder] = held
            if comms is None:
                if draft.kind in AGENT_KINDS:
                    one.receipt = {**_base(draft), "state": KEPT,
                                   "reason": "comms is not installed, so nothing sends the "
                                             "operator's words: a lead records them",
                                   "fix": command(draft, held, "PTR")}
                    continue
            else:
                one.to = held.lead if to == EACH else to
                if not one.to:
                    one.receipt = {**_base(draft), **_no_lead(draft.holder)}
                    continue
            if draft.kind not in PAGE_KINDS:
                continue
            if draft.tell:
                found = on_record(draft.holder, draft.act, settings)
                if found is None:
                    one.receipt = {**_base(draft), "state": REFUSED,
                                   "reason": f"{draft.item} of {draft.holder} holds no record "
                                             "made by this act: not on record",
                                   "fix": None}
                    continue
                one.receipt = {**_base(draft), **_facts(found, draft)}
                one.recorded = True
                continue
            if actor is None:
                actor = by or actors.default()
            try:
                facts = _record(draft, held, at, by=actor, settings=settings)
            except UnfencedTest:
                raise
            except RoundsError as exc:
                one.receipt = {**_base(draft), **_refusal(exc)}
                continue
            one.receipt = {**_base(draft), **facts, **_freed(draft, held, settings)}
            one.recorded = True
        messages = []
        if comms is not None:
            by_recipient: dict[str, list[_Done]] = {}
            for one in fresh:
                if one.to and (one.recorded or (not one.receipt
                                                and one.draft.kind in AGENT_KINDS)):
                    by_recipient.setdefault(one.to, []).append(one)
            for recipient, listed in by_recipient.items():
                sent = tell(comms, recipient, message_text(listed, helds), at)
                messages.append(sent)
                for one in listed:
                    _answered(one, sent, helds.get(one.draft.holder))
        for one in fresh:
            if not one.receipt:
                one.receipt = {**_base(one.draft), "state": RECORDED}
            elif "state" not in one.receipt:
                one.receipt["state"] = RECORDED
            receipts.keep(one.draft.key, one.receipt)
        return {"receipts": [one.receipt for one in done], "messages": messages}


def _answered(one: _Done, sent: Mapping, held: _Held | None) -> None:
    """Set a draft's receipt from what became of its message."""
    draft = one.draft
    told = {"to": sent["to"], "at": sent["at"], "id": sent.get("id"),
            "delivery": sent.get("delivery")}
    refused = sent.get("refused")
    if one.recorded:
        one.receipt["to"] = sent["to"]
        if refused is None:
            one.receipt.update(state=RECORDED, message=told)
        else:
            one.receipt.update(state=NOT_TOLD, not_told=refused)
        return
    if refused is not None:
        one.receipt = {**_base(draft), "state": REFUSED, "to": sent["to"],
                       "reason": f"comms did not send it: {refused}", "fix": None}
        return
    record = f"comms:{told['id']}" if told["id"] else THIS_MESSAGE
    one.receipt = {**_base(draft), "state": SENT, "to": sent["to"], "message": told,
                   "command": command(draft, held, record)}


# -- dropping a build ----------------------------------------------------------------------

def kept_why(kept: removal.Kept) -> str:
    """Why the removal rule keeps a folder, in words, with the paths that are work: what
    the drop's confirm, its receipt and the lead's message each say beside the path."""
    listed = ", ".join(kept.work[:10]) + (", ..." if len(kept.work) > 10 else "")
    return f"{kept.why} ({listed})" if kept.work else kept.why


def _judged(name: str, commissioned: Mapping, kind: str, settings) -> dict | None:
    """What the removal rule says now of one folder of a build: `{"kind", "path",
    "goes", "why"}`, or None when the build records none or nothing is at its place."""
    if not commissioned.get(kind):
        return None
    try:
        found = removal.judge(name, commissioned, kind, settings=settings)
    except RoundsError as exc:
        return {"kind": kind, "path": str(commissioned[kind]), "goes": False,
                "why": exc.reason}
    if found is None:
        return {"kind": kind, "path": str(commissioned[kind]), "goes": False,
                "why": "nothing is there"}
    if isinstance(found, removal.Kept):
        return {"kind": kind, "path": str(found.place), "goes": False,
                "why": kept_why(found)}
    return {"kind": kind, "path": str(found.place), "goes": True, "why": None}


def drop_facts(name: str, stage: str, *, settings=None) -> dict:
    """What the drop's confirm says of the open build of `stage` of the round `name`,
    read now, writing nothing: the build (branch, builder, when it was commissioned and
    the branch's tip), each of its folders with what the removal rule says of it now,
    and CI's own refs the drop removes. `open` is False, with `reason`, when the stage
    has no open build."""
    settings = own.load() if settings is None else settings
    made: dict[str, Any] = {"round": name, "stage": stage, "open": False, "reason": None,
                            "branch": None, "builder": None, "at": None, "tip": None,
                            "folders": [], "refs": []}
    try:
        held = _read(name, settings)
        build = builds.current(held.state, stage)
    except UnfencedTest:
        raise
    except RoundsError as exc:
        made["reason"] = exc.reason
        return made
    if build is None or build.ended is not None:
        made["reason"] = (f"{stage} has no open build" if build is None else
                          f"{stage} has no open build: its last was {build.ended}")
        return made
    commissioned = checking.commission_of(held.state, build)
    try:
        _key, root = folders.root(build.repo)
        tip = Git(root).branch(build.branch)
    except RoundsError:
        tip = None
    made.update(open=True, branch=build.branch, builder=commissioned.get("builder"),
                at=commissioned.get("at"), tip=tip,
                folders=[one for one in (_judged(name, commissioned, kind, settings)
                                         for kind in (removal.WORKTREE, removal.SCRATCH))
                         if one is not None],
                refs=[f"{remote} {ref}" for remote, ref in
                      ci_refs.of_build(held.state, build, settings)])
    return made


def _drop_text(name: str, stage: str, receipt: Mapping) -> str:
    lines = [f"The operator dropped the open build of {stage} of {name} on the rounds page "
             f"({receipt.get('record')}). The stage stays in the plan, owed."]
    if receipt.get("again"):
        lines.append("The drop was recorded earlier; the round's log holds it.")
    for path in receipt.get("removed") or ():
        lines.append(f"- removed {path}")
    for one in receipt.get("kept") or ():
        lines.append(f"- kept {one['path']}: {one['why']}")
    for one in receipt.get("refs") or ():
        lines.append(f"- {one}")
    if receipt.get("branch"):
        lines.append(f"The branch {receipt['branch']} stays, with every commit on it.")
    lines += ["", NOTHING_TO_RECORD]
    return "\n".join(lines)


def drop(name: str, stage: str, act: str, *, receipts: Receipts, comms: Any,
         settings=None, at: str | None = None, run_by: str | None = None) -> dict:
    """Drop the open build of `stage` of the round `name` as the operator's drop, with
    the page pointer of `act`, then tell the round's lead with comms and a lead. A drop
    posted again with one act id runs once; after a restart, a drop its act id already
    made reads as recorded and the lead is told."""
    settings = own.load() if settings is None else settings
    at = log.stamp() if at is None else at
    base = {"act": act, "holder": name, "item": stage, "kind": "drop"}
    with receipts.holding([act]):
        kept = receipts.get(act)
        if kept is not None:
            return {**kept, "repeat": True}
        try:
            held = _read(name, settings)
            found = on_record(name, act, settings)
            if found is not None:
                receipt = {**base, **_facts(found, Draft(act, name, stage, "drop")),
                           "again": True, "state": RECORDED, "removed": [], "kept": [],
                           "refs": [], "unscanned": []}
            else:
                dropped = dropping.drop_build(
                    name, stage, by=dropping.OPERATOR, record=pointer(act, at),
                    run_by=run_by or actors.default(), wait_s=WAIT_S, settings=settings)
                tips = dropped.event.get("tips") or [{}]
                receipt = {**base, "state": RECORDED, "record": dropped.record,
                           "at": dropped.event.get("at"), "dropped": stage,
                           "branch": tips[0].get("branch"), "tip": tips[0].get("tip"),
                           "removed": [str(one) for one in dropped.removed],
                           "kept": [{"path": str(one.place), "why": kept_why(one)}
                                    for one in dropped.kept],
                           "refs": [one.line() for one in dropped.refs],
                           "unscanned": list(dropped.unscanned)}
        except UnfencedTest:
            raise
        except RoundsError as exc:
            return {**base, **_refusal(exc)}
        if comms is None:
            receipt["not_told"] = "comms is not installed, so no one is told"
        elif not held.lead:
            receipt["not_told"] = f"no lead for {name}, so no one is told"
        else:
            sent = tell(comms, held.lead, _drop_text(name, stage, receipt), at)
            receipt["to"] = held.lead
            if sent.get("refused") is None:
                receipt["message"] = {key: sent.get(key) for key in ("to", "at", "id",
                                                                      "delivery")}
            else:
                receipt["not_told"] = sent["refused"]
        receipts.keep(act, receipt)
        return receipt


def drafts_of(body: Mapping) -> list[Draft]:
    """The drafts of a post's body, whose keys are lists of one length, a draft per
    place: `act`, `holder`, `item`, `kind`, `chose`, `text` and `tell`, an empty string
    standing for none. Raises `ValueError` naming the key that is wrong."""
    listed = {key: list(body.get(key) or ()) for key in
              ("act", "holder", "item", "kind", "chose", "text", "tell")}
    n = len(listed["act"])
    for key, values in listed.items():
        if key in ("chose", "text", "tell") and not values:
            listed[key] = [""] * n
        elif len(values) != n:
            raise ValueError(key)
    made = []
    for place in range(n):
        one = {key: values[place] for key, values in listed.items()}
        if not ACT_ID.fullmatch(one["act"]) or (one["tell"] and not ACT_ID.fullmatch(one["tell"])):
            raise ValueError("act" if not ACT_ID.fullmatch(one["act"]) else "tell")
        if one["kind"] not in KINDS:
            raise ValueError("kind")
        made.append(Draft(act=one["act"], holder=one["holder"], item=one["item"],
                          kind=one["kind"], chose=one["chose"] or None,
                          text=one["text"] or None, tell=one["tell"] or None))
    return made


def comms_loaded() -> Any | None:
    """comms' API, None when comms is not installed."""
    return telling.comms_api()


def agents(comms: Any) -> dict:
    """The names comms' roster gives as bound, for the To picker: `{"agents", "reason",
    "comms"}`, an empty list with comms' reason when comms is not installed or its daemon
    can't be read; `comms` says whether comms is installed, as the answers route finds it,
    so the page words the tray as the route records it."""
    if comms is None:
        return {"agents": [], "reason": "comms is not installed", "comms": False}
    try:
        roster = comms.roster()
    except UnfencedTest:
        raise
    except Exception as exc:  # noqa: BLE001 - the picker says why it is empty
        return {"agents": [], "reason": str(exc) or type(exc).__name__, "comms": True}
    names = sorted({str(getattr(one, "qualified", "")) for one in roster.bound} - {""})
    return {"agents": names, "reason": None, "comms": True}


__all__ = ["WAIT_S", "PAGE_NAME", "ACT_ID", "EACH", "PICK", "ACCEPT", "REJECT", "READ_BACK",
           "DONE", "PARK", "UNPARK", "WORDS", "CORRECT", "EXPLAIN", "PAGE_KINDS",
           "AGENT_KINDS", "KINDS", "RECORDED", "NOT_TOLD", "SENT", "REFUSED", "KEPT",
           "THIS_MESSAGE", "TEXT", "NOTHING_TO_RECORD", "Draft", "Receipts", "on_record",
           "pointer", "quoted", "command", "message_text", "tell", "send", "drop_facts",
           "kept_why", "drop",
           "drafts_of", "comms_loaded", "agents"]
