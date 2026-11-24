"""A person's discard of a recall's line: `moxi memory subconscious discard RECALL_ID`.

A discard ends a line whose answer memory still holds to send and never will (a state
of `recalls.DISCARDABLE`: a `sending` line, whose answer comms has not taken); it
refuses a line in any other state. Under the line's session lock it sets the line
`discarded`, with the user it ran as and the time, and the record `ended`, except for a
late text (an answer that came after its round's bound), whose record keeps the state
the bound set; when the line still carries the kept mark, it then acks the round's end
to turns and clears the mark, in the order every settled round follows. It stops no
process: the line's owner reads the line under the lock before each step, leaves a
`discarded` line's state alone and gives the recall no further round. memory never
discards on its own.

Standard library at import; turns' client is imported at use.
"""

from __future__ import annotations

import getpass
import time

from . import recalls, records


class Refused(Exception):
    """A discard memory refuses, with the reason."""


def _user() -> str | None:
    try:
        return getpass.getuser()
    except (OSError, KeyError, ImportError):
        return None


def _ack(session_id: str, ref: str) -> None:
    import moxi_turns as client

    client.ack(ref, session_id=session_id)


def find(recall_id: str) -> dict | None:
    """The newest line of recall `recall_id` in any session's log, or None."""
    found = [line for line in recalls.all_lines() if line.get("recall") == recall_id]
    return found[-1] if found else None


def late(line: dict) -> bool:
    """Whether the newest text on `line` came after its round's bound."""
    texts = [r for r in line.get("rounds") or [] if isinstance(r, dict) and "text" in r]
    return bool(texts) and bool(texts[-1].get("late"))


def discard(recall_id: str, *, by: str | None = None, ack=None,
            now: float | None = None) -> dict:
    """Discard recall `recall_id`'s line and return it as written. Raises `Refused`
    for a recall with no line or a line in a state no discard ends."""
    now = time.time() if now is None else now
    line = find(recall_id)
    if line is None:
        raise Refused(f"no recall {recall_id} in the log")
    session_id = str(line.get("session"))
    with recalls.session_lock(session_id):
        line = recalls.find_line(session_id, recall_id)
        state = (line or {}).get("state")
        if line is None or state not in recalls.DISCARDABLE:
            raise Refused(f"recall {recall_id} is {state}: only an answer memory still "
                          "holds to send can be discarded")
        line.update(state=recalls.DISCARDED, discarded_by=by or _user(), discarded_at=now)
        line = recalls.put_line(session_id, line, now=now)
        if not late(line):
            records.update(session_id, recall_id, now=now, state=records.ENDED)
        ref = line.get("ref")
        if line.get("kept") is True and ref:
            try:
                (ack or _ack)(session_id, str(ref))
            except Exception:  # noqa: BLE001 - an ack not taken leaves the mark for a taker
                return line
            line["kept"] = False
            line = recalls.put_line(session_id, line, now=now)
    return line


__all__ = ["Refused", "find", "late", "discard"]
