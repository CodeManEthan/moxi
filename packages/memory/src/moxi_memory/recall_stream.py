"""A recall's stream: its rounds, each asked of the fork through a route, each settled on
the log line and acked to turns.

**A round.** Before the child asks for round N it writes the round's ref
(`<recall>:<N>`, `ref`) on the log line with the kept mark, the line `open`; it asks
with that ref, with `keep`, and with the round's bound, `subconscious.limit_s` plus
`ROUND_MARGIN_S` from the round's own send, which turns ends `lost` (`gone`) when no
answer came by then. The first round's task is the brief; a later one's is
`CONTINUE`, with the tool calls and seconds the recall has left.

**Settling a round.** Once turns has settled the round's delivery (its end read), the
child, under the session's lock, writes the line's state first, then acks the round's
ref to turns, then clears the kept mark. A child that dies between two of them leaves the
mark, and a taker finishes them (`moxi_memory.recall_take`). An answer goes on the line
with the time it came back, the line `back`, and the record `back` at the first answer
(a `hurried` record stays `hurried`). A first round with no answer ends the recall `lost`, record and line, with
the reason (`denied`, `gone`, or the delivery's refusal); a later one ends the stream,
the line `back` with the round's loss noted. A round whose end turns never gave within
the wait is left with its kept mark and the line as it was, for a taker. An ack turns
did not take (refused, or the call raised) leaves the mark and ends the stream at that
round: the line keeps naming the round's ref, so a taker acks it (and turns releases
the round's kept end), and no later round's ref is ever written over a kept one.

**When the stream ends.** At an answer of `NOTHING`, or with no text, or whose last line
is `End of recall.` (a fork that says it is done is done); after `subconscious.max_rounds` rounds; once the fork has made
`subconscious.max_tools` tool calls (counted on the record); at `subconscious.limit_s`
from the recall's start; once the record is `hurried` (read again under the session's
lock before each round is asked); or at an ack turns did not take. Then the record ends
`ended` when an answer came back, the line noting whether the recall was hurried or ran
past its limit. A line a person discarded (`moxi_memory.recall_discard`) ends the stream as
it is: the child asks no further round and leaves the line's state and the record as
the discard set them. A recall hurried before its first round was asked never starts its
fork: record and line end `lost`, reason `hurried`. One hurried after its round's ref
was written still asks that round, since the lock is not held over turns' call; its
fork's calls before the fork's id is written are judged by its own record
(`moxi_memory.recall_guard`, "Knowing its fork").

**A route that answers by comms** (its name in `recall_start.BY_COMMS`). The harness does
not bring the fork's answer back, so memory sends it. An answer with points goes on the
line `sending` before anything else is done with it, then the ack, then the mark; then
the child sends it under the session's recall sender (`moxi_memory.recall_send.send`),
to the session's live comms name, else the one the recall started under (`start_name`),
until comms takes it (`deliver`): a send comms refuses is tried again every
`RESEND_FAST_S` for `RESEND_FAST_FOR_S`, then every `RESEND_SLOW_S`, with no last try,
each refusal's reason kept on the line. The line goes `back` once comms takes it. An
answer of `NOTHING` sends nothing. Before each try the line is read again under the lock,
and a line a person discarded is left alone; a try comms took while the discard was made
is noted on the line, not undone.

On these routes turns does not end a round at its bound, so the child waits the bound
itself. Past it, with no end, it notes the loss as the bound sets it (`note_bound`: a
first round ends record and line `lost`, `gone`; a later one the line `back` with the
round's loss noted and the record `ended`), marks the line `past_bound` and keeps the
mark, and waits on at most `LATE_WAIT_S` only to settle the round (`settle_late`): an
answer with points is a late text, which takes the line to `sending`, is acked and sent
as above, the record left as the bound set it; any other end is acked, the line keeping
the bound's state. At the wait's last limit it stops, the line kept for a taker.

A route (`Route`) asks turns for the rounds; `moxi_memory.recall_route` holds the
subagent route's.

Standard library at import; turns' result values are imported at use.
"""

from __future__ import annotations

import math
import time
from typing import Any, Protocol

from . import recalls, records

#: How far past `subconscious.limit_s` each round is bounded, in seconds.
ROUND_MARGIN_S = 30
#: How long past a round's bound the child waits for turns to give the round's end.
END_WAIT_MARGIN_S = 60
#: On a route that answers by comms, how long past a round's bound the child waits only to
#: settle the round.
LATE_WAIT_S = 300
#: A send comms did not take is tried again every `RESEND_FAST_S` seconds for the first
#: `RESEND_FAST_FOR_S` seconds, then every `RESEND_SLOW_S` seconds.
RESEND_FAST_S = 10
RESEND_FAST_FOR_S = 60
RESEND_SLOW_S = 300
#: Why a round got no answer by its bound.
GONE = "gone"
PAST_BOUND = "the round's bound passed with no answer"
#: How `run` ended when a round's end never came.
UNSETTLED = "unsettled"
#: The answer that says the fork found nothing, and the line that ends a stream.
NOTHING = "NOTHING"
END_LINE = "End of recall."
#: A later round's task.
CONTINUE = ("Keep digging on the same {what_lower}, within what is left of your limits: "
            "{tools_left} tool calls and about {secs_left} seconds. Answer when you find "
            "something new, with only what is new since your last answer, under the same "
            "rules, and end it with the line End of recall. when nothing more is worth "
            "looking for. Answer NOTHING if you find nothing new.")


class Route(Protocol):
    """How a recall's rounds reach its fork."""

    name: str

    def first(self, text: str, *, ref: str, round_timeout_s: float) -> Any:
        """Start the fork with its first round's task; a handle on the round."""

    def next(self, prior: Any, text: str, *, ref: str, round_timeout_s: float) -> Any:
        """Give the fork its next round; a handle on the round."""

    def started(self, handle: Any) -> str | None:
        """The fork's id once the round went in, else None."""

    def ended(self, handle: Any, timeout_s: float) -> Any:
        """The round's end: turns' `Ended`, `Lost` or `Refused`. Raises `TimeoutError`
        when turns gave none in time."""

    def ack(self, ref: str) -> None:
        """Done with the kept end of `ref`."""


def ref(recall_id: str, round_number: int) -> str:
    """Round `round_number`'s ref."""
    return f"{recall_id}:{round_number}"


def round_bound_s(settings) -> float:
    """Each round's bound, from its own send."""
    return float(settings["limit_s"]) + ROUND_MARGIN_S


def ends_stream(answer: str | None) -> bool:
    """Whether `answer` ends the stream by what it says: empty, `NOTHING`, or `END_LINE`
    as its last line, in any round."""
    text = (answer or "").strip()
    if not text or text == NOTHING:
        return True
    return text.splitlines()[-1].strip() == END_LINE


def continue_text(record: dict, settings, *, now: float) -> str:
    """A later round's task."""
    tools_left = int(settings["max_tools"]) - int(record.get("tools") or 0)
    secs_left = math.ceil(float(settings["limit_s"]) - (now - float(record["at"])))
    what = "topic" if record.get("start") == recalls.ASKED else "prompt"
    return CONTINUE.format(what_lower=what, tools_left=tools_left, secs_left=secs_left)


def why_stop(record: dict, settings, round_number: int, *, now: float) -> str | None:
    """Why the stream gets no further round, from the record and the limits, or None."""
    if record.get("state") == records.HURRIED:
        return "hurried"
    if round_number >= int(settings["max_rounds"]):
        return "rounds"
    if int(record.get("tools") or 0) >= int(settings["max_tools"]):
        return "tools"
    if now - float(record["at"]) >= float(settings["limit_s"]):
        return "limit"
    return None


def by_comms(route) -> bool:
    """Whether `route` (a route, or a route's name) answers by comms."""
    from . import recall_start

    name = route if isinstance(route, str) else getattr(route, "name", None)
    return name in recall_start.BY_COMMS


def has_points(answer: str | None) -> bool:
    """Whether `answer` has anything to send: some text, and not `NOTHING`."""
    text = (answer or "").strip()
    return bool(text) and text != NOTHING


def _refresh(recall) -> None:
    """Read the record and the line again; the caller holds the session's lock."""
    recall.record = records.read(recall.session_id, recall.recall_id) or recall.record
    recall.line = recalls.find_line(recall.session_id, recall.recall_id) or recall.line


def _sending(recall, rounds: list, *, now: float, record: dict | None = None) -> None:
    recall.write(record=record, state=recalls.SENDING, rounds=rounds, sending_at=now,
                 send_tries=0, send_reason=None)


def _newest_text(line: dict) -> str:
    for entry in reversed(line.get("rounds") or []):
        if isinstance(entry, dict) and isinstance(entry.get("text"), str):
            return entry["text"]
    return ""


def start_name(recall) -> str | None:
    """The session's comms name as it stood when the recall started, from its line or
    its record: where the answer goes once the session's binding has ended."""
    return recall.line.get("comms_name") or recall.record.get("comms_name") or None


def deliver(recall, *, send=None, sleep=time.sleep, clock=time.time) -> str:
    """Send a `sending` line's newest text under the session's recall sender until comms
    takes it, and return the line's state then: `back`, or the state someone else set
    (`discarded`). Never gives up: a refusal is kept on the line and tried again."""
    if send is None:
        from .recall_send import send
    began = clock()
    while True:
        with recalls.session_lock(recall.session_id):
            _refresh(recall)
            if recall.line.get("state") != recalls.SENDING:
                return str(recall.line.get("state"))
            text = _newest_text(recall.line)
        sent = send(recall.session_id, text, comms_name=start_name(recall))
        now = clock()
        with recalls.session_lock(recall.session_id):
            _refresh(recall)
            tries = int(recall.line.get("send_tries") or 0)
            if sent.taken:
                fields = {"sent_at": now, "message_id": sent.message_id}
                if recall.line.get("state") == recalls.SENDING:
                    fields["state"] = recalls.BACK
                recall.write(**fields)
                return str(recall.line.get("state"))
            if recall.line.get("state") != recalls.SENDING:
                return str(recall.line.get("state"))
            recall.write(send_tries=tries + 1, send_reason=sent.reason)
        sleep(RESEND_FAST_S if now - began < RESEND_FAST_FOR_S else RESEND_SLOW_S)


def note_bound(recall, round_number: int, *, now: float | None = None) -> None:
    """On a route that answers by comms, note that round `round_number`'s bound passed
    with no answer, as the bound sets it, keeping the line's kept mark: a first round
    (or one with no answer before it) ends record and line `lost` (`gone`); a later one
    leaves the line `back` with the round's loss noted and ends the record."""
    now = time.time() if now is None else now
    with recalls.session_lock(recall.session_id):
        _refresh(recall)
        if recall.line.get("state") == recalls.DISCARDED:
            return
        if round_number == 1 or not recall.record.get("answered"):
            recall.write(record={"state": records.LOST, "reason": GONE, "error": PAST_BOUND},
                         state=recalls.LOST, reason=GONE, error=PAST_BOUND, past_bound=True)
            return
        rounds = list(recall.line.get("rounds") or [])
        rounds.append({"round": round_number, "lost": GONE, "error": PAST_BOUND,
                       "at": now})
        recall.write(record={"state": records.ENDED}, state=recalls.BACK, rounds=rounds,
                     past_bound=True)


def settle_late(recall, round_number: int, end, route: Route, *, now: float | None = None,
                send=None, sleep=time.sleep) -> str:
    """Settle a round its bound ended, on a route that answers by comms, with turns'
    `end` for it: an answer with points is a late text, written on the line `sending`,
    then acked, the mark cleared, and sent; any other end is acked and the mark cleared,
    the line keeping the bound's state. The record keeps the state the bound set. Returns
    the line's state at the end."""
    from moxi_turns import Ended

    now = time.time() if now is None else now
    with recalls.session_lock(recall.session_id):
        _refresh(recall)
        if (recall.line.get("state") != recalls.DISCARDED and isinstance(end, Ended)
                and has_points(end.answer)):
            rounds = list(recall.line.get("rounds") or [])
            rounds.append({"round": round_number, "text": end.answer, "at": now,
                           "late": True})
            _sending(recall, rounds, now=now)
        try:
            route.ack(ref(recall.recall_id, round_number))
        except Exception:  # noqa: BLE001 - an ack not taken leaves the mark for a taker
            pass
        else:
            recall.write(kept=False)
    if recall.line.get("state") == recalls.SENDING:
        return deliver(recall, send=send, sleep=sleep)
    return str(recall.line.get("state"))


def _reason(end) -> tuple[str, str]:
    text = str(getattr(end, "reason", "") or "gone")
    return text.split(":")[0].split()[0] if text.strip() else "gone", text


def settle(recall, round_number: int, end, route: Route, *, now: float | None = None) -> bool:
    """Settle round `round_number` with turns' `end` for it: the line, then the ack,
    then the mark. True when an answer came back. The line is read again under the lock;
    one a person discarded keeps its state, and only the ack and the mark follow. On a
    route that answers by comms an answer with points goes on the line `sending`, for
    `deliver` to send once the mark is cleared."""
    from moxi_turns import Ended

    now = time.time() if now is None else now
    round_ref = ref(recall.recall_id, round_number)
    with recalls.session_lock(recall.session_id):
        recall.record = records.read(recall.session_id, recall.recall_id) or recall.record
        recall.line = recalls.find_line(recall.session_id, recall.recall_id) or recall.line
        rounds = list(recall.line.get("rounds") or [])
        if recall.line.get("state") == recalls.DISCARDED:
            got = False
        elif isinstance(end, Ended):
            answer = end.answer or ""
            rounds.append({"round": round_number, "text": answer, "at": now})
            state = recall.record.get("state")
            fields = {"answered": True}
            if state in (records.STARTING, records.RUNNING):
                fields["state"] = records.BACK
            if by_comms(route) and has_points(answer):
                _sending(recall, rounds, now=now, record=fields)
            else:
                recall.write(record=fields, state=recalls.BACK, rounds=rounds)
            got = True
        else:
            reason, error = _reason(end)
            if round_number == 1 or not recall.record.get("answered"):
                recall.write(record={"state": records.LOST, "reason": reason,
                                     "error": error},
                             state=recalls.LOST, reason=reason, error=error)
            else:
                rounds.append({"round": round_number, "lost": reason, "error": error,
                               "at": now})
                recall.write(state=recalls.BACK, rounds=rounds)
            got = False
        try:
            route.ack(round_ref)
        except Exception:  # noqa: BLE001 - an ack not taken leaves the mark for a taker
            return got
        recall.write(kept=False)
    return got


#: Why `open_round` asked no round.
KEPT = "kept"


def open_round(recall, round_number: int, round_ref: str) -> str | None:
    """Under the session's lock, write round `round_number`'s ref on the line with the
    kept mark, the line `open`, before the round is asked, and return None. Writes
    nothing and returns why when no round may be asked: `discarded` (a person discarded
    the line), `hurried` (a newer recall of the session started), or `kept` (the line
    still names an earlier round whose ack turns did not take; that ref stays on the
    line for a taker)."""
    with recalls.session_lock(recall.session_id):
        current = recalls.find_line(recall.session_id, recall.recall_id)
        if current is not None:
            if current.get("state") == recalls.DISCARDED:
                recall.line = current
                return recalls.DISCARDED
            if current.get("kept") is True and current.get("ref") not in (None, round_ref):
                recall.line = current
                return KEPT
        record = records.read(recall.session_id, recall.recall_id) or recall.record
        recall.record = record
        if record.get("state") == records.HURRIED:
            return records.HURRIED
        recall.write(record={"rounds": round_number}, state=recalls.OPEN, ref=round_ref,
                     kept=True)
    return None


def note_fork(recall, fork_id: str | None) -> None:
    """Write the fork's id on the record and the line, the record `running` when it was
    still `starting`, unless the line already names a fork or `fork_id` is None. The
    log's fork ids are what keep a fork's own transcript out of memory's index."""
    if fork_id is None or recall.line.get("fork") is not None:
        return
    with recalls.session_lock(recall.session_id):
        state = records.read(recall.session_id, recall.recall_id) or recall.record
        fields: dict = {"fork": fork_id}
        if state.get("state") == records.STARTING:
            fields["state"] = records.RUNNING
        recall.write(record=fields, fork=fork_id)


def lose_hurried(recall) -> None:
    """End a recall hurried before its first round was asked: no fork was started, so
    record and line end `lost`, reason `hurried`, with nothing to ack."""
    error = "a newer recall of the session started before this one asked its fork"
    recall.put(record={"state": records.LOST, "reason": records.HURRIED, "error": error},
               state=recalls.LOST, reason=records.HURRIED, error=error, hurried=True)


def _past_bound(recall, route: Route, handle, round_number: int, *, clock, send,
                sleep) -> str:
    """A round its bound ended on a route that answers by comms: note the bound, wait on
    at most `LATE_WAIT_S` only to settle the round, and return how the recall ended."""
    note_bound(recall, round_number, now=clock())
    try:
        end = route.ended(handle, LATE_WAIT_S)
    except TimeoutError:
        return UNSETTLED
    if recall.line.get("fork") is None:
        note_fork(recall, route.started(handle))
    if settle_late(recall, round_number, end, route, now=clock(), send=send,
                   sleep=sleep) == recalls.DISCARDED:
        return recalls.DISCARDED
    current = records.read(recall.session_id, recall.recall_id) or recall.record
    return str(current.get("state"))


def run(recall, route: Route, brief: str, *, clock=time.time, send=None,
        sleep=time.sleep) -> str:
    """Run the recall's rounds to the stream's end, and return how it ended: `ended`,
    `lost`, `discarded` (a person discarded its line; no further round is asked), or
    `unsettled` (a round's end never came; the line keeps its mark). A round whose ack
    turns did not take ends the stream there, its ref and mark left on the line. On a
    route that answers by comms, `send` and `sleep` are `deliver`'s."""
    settings = recall.settings
    bound = round_bound_s(settings)
    handle = None
    round_number = 1
    while True:
        round_ref = ref(recall.recall_id, round_number)
        refused = open_round(recall, round_number, round_ref)
        if refused == recalls.DISCARDED:
            return recalls.DISCARDED
        if refused == records.HURRIED and round_number == 1:
            lose_hurried(recall)
            return records.LOST
        if refused == records.HURRIED:
            recall.put(hurried=True)
            break
        if refused is not None:
            break
        if handle is None:
            launch = getattr(route, "launch", None)
            if launch is not None:
                # a fork of its own is named before anything waits on it
                note_fork(recall, launch())
            handle = route.first(brief, ref=round_ref, round_timeout_s=bound)
        else:
            text = continue_text(recall.record, settings, now=clock())
            handle = route.next(handle, text, ref=round_ref, round_timeout_s=bound)
        note_fork(recall, route.started(handle))
        comms = by_comms(route)
        try:
            end = route.ended(handle, bound if comms else bound + END_WAIT_MARGIN_S)
        except TimeoutError:
            if not comms:
                return UNSETTLED
            return _past_bound(recall, route, handle, round_number, clock=clock,
                               send=send, sleep=sleep)
        if recall.line.get("fork") is None:
            # the start was not read in its wait; once the round has ended it is known
            note_fork(recall, route.started(handle))
        got = settle(recall, round_number, end, route, now=clock())
        if recall.line.get("state") == recalls.SENDING:
            deliver(recall, send=send, sleep=sleep, clock=clock)
        if recall.line.get("state") == recalls.DISCARDED:
            return recalls.DISCARDED
        if not got:
            if recall.record.get("state") == records.LOST:
                return records.LOST
            break
        if ends_stream(recall.line["rounds"][-1].get("text")):
            break
        current = records.read(recall.session_id, recall.recall_id) or recall.record
        recall.record = current
        stop = why_stop(current, settings, round_number, now=clock())
        if stop is not None:
            recall.put(hurried=stop == "hurried", past_limit=stop == "limit")
            break
        round_number += 1
    current = records.read(recall.session_id, recall.recall_id) or recall.record
    recall.put(record={"state": records.ENDED},
               hurried=current.get("state") == records.HURRIED or recall.line.get("hurried"))
    return records.ENDED


__all__ = ["ROUND_MARGIN_S", "END_WAIT_MARGIN_S", "LATE_WAIT_S", "RESEND_FAST_S",
           "RESEND_FAST_FOR_S", "RESEND_SLOW_S", "GONE", "PAST_BOUND", "UNSETTLED", "NOTHING",
           "END_LINE", "CONTINUE", "Route", "ref", "round_bound_s", "ends_stream",
           "continue_text", "why_stop", "by_comms", "has_points", "start_name", "deliver", "note_bound",
           "settle_late", "settle", "KEPT", "open_round", "note_fork", "lose_hurried", "run"]
