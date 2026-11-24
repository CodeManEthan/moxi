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
is `End of recall.`; after `subconscious.max_rounds` rounds; once the fork has made
`subconscious.max_tools` tool calls (counted on the record); at `subconscious.limit_s`
from the recall's start; once the record is `hurried` (read again under the session's
lock before each round is asked); or at an ack turns did not take. Then the record ends
`ended` when an answer came back, the line noting whether the recall was hurried or ran
past its limit. A line a person discarded (`moxi_memory.recall_discard`) ends the stream as
it is: the child asks no further round and leaves the line's state and the record as
the discard set them. A recall hurried before its first round was asked never starts its
fork: record and line end `lost`, reason `hurried`.

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
    """Whether `answer` ends the stream by what it says."""
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


def _reason(end) -> tuple[str, str]:
    text = str(getattr(end, "reason", "") or "gone")
    return text.split(":")[0].split()[0] if text.strip() else "gone", text


def settle(recall, round_number: int, end, route: Route, *, now: float | None = None) -> bool:
    """Settle round `round_number` with turns' `end` for it: the line, then the ack,
    then the mark. True when an answer came back. The line is read again under the lock;
    one a person discarded keeps its state, and only the ack and the mark follow."""
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


def lose_hurried(recall) -> None:
    """End a recall hurried before its first round was asked: no fork was started, so
    record and line end `lost`, reason `hurried`, with nothing to ack."""
    error = "a newer recall of the session started before this one asked its fork"
    recall.put(record={"state": records.LOST, "reason": records.HURRIED, "error": error},
               state=recalls.LOST, reason=records.HURRIED, error=error, hurried=True)


def run(recall, route: Route, brief: str, *, clock=time.time) -> str:
    """Run the recall's rounds to the stream's end, and return how it ended: `ended`,
    `lost`, `discarded` (a person discarded its line; no further round is asked), or
    `unsettled` (a round's end never came; the line keeps its mark). A round whose ack
    turns did not take ends the stream there, its ref and mark left on the line."""
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
            handle = route.first(brief, ref=round_ref, round_timeout_s=bound)
        else:
            text = continue_text(recall.record, settings, now=clock())
            handle = route.next(handle, text, ref=round_ref, round_timeout_s=bound)
        fork_id = route.started(handle)
        if fork_id is not None and recall.line.get("fork") is None:
            with recalls.session_lock(recall.session_id):
                state = records.read(recall.session_id, recall.recall_id) or recall.record
                fields: dict = {"fork": fork_id}
                if state.get("state") == records.STARTING:
                    fields["state"] = records.RUNNING
                recall.write(record=fields, fork=fork_id)
        try:
            end = route.ended(handle, bound + END_WAIT_MARGIN_S)
        except TimeoutError:
            return "unsettled"
        got = settle(recall, round_number, end, route, now=clock())
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


__all__ = ["ROUND_MARGIN_S", "END_WAIT_MARGIN_S", "NOTHING", "END_LINE", "CONTINUE", "Route",
           "ref", "round_bound_s", "ends_stream", "continue_text", "why_stop", "settle",
           "KEPT", "open_round", "lose_hurried", "run"]
