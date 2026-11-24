"""Taking over a recall's line whose owner is gone, so a recall whose child died is
finished by another process and an answer its fork gave is never dropped.

**Which lines (`needs_taking`, `owner_gone`).** A recall's line is `open` from its
child's start, and carries the kept mark from each round's ask until that round's end
is acked to turns. A line that is `open` or still kept needs an owner. Its owner is the
process named on it: the taker that took it (`taker`), else its child (`child`). The
owner is gone when `moxi.proc.alive` is false for its pid and start time, or when the
line names none.

**Who takes them.** Two takers: every recall child at its start, for the lines of every
session (`take_all`, in a thread beside its own recall), and the taker memory's `gone`
listener starts when a session with the subconscious on ends, for that session's lines
(`moxi-memory-subconscious --take --session SESSION`, through the detached starter). A
taker claims a line under the
line's own session's lock (`claim`): it reads the line again there and writes its own
identity as `taker` only while the owner is still gone, so of two takers one claims the
line and the other leaves it. A line a taker left is free again once that taker exits.

**What a taker does (`take`).**

- A line whose round is already settled on it (`back`, `lost` or `discarded`) and still
  kept: what the round's end says is safe on the line, so it acks the round's ref and
  clears the mark, and ends the record as the line says (`lost` for a `lost` line, else
  `ended`), since no further round will be asked.
- An `open` line with no ref: its child died before it asked the fork anything, so the
  line and the record end `lost` (`gone`).
- An `open` line with a ref: it reads the round's end by the ref without waiting. When
  turns has none yet (the fork's turn still runs), it leaves the line as it is, kept,
  for the next taker. When turns has it and the line names no fork yet (its child died
  before it read the fork's start), it first reads the round's start the same way and
  writes the fork's id. Then it settles the round as the child would
  (`recall_stream.settle`): the line's state first, then the ack, then the mark. On
  Claude Code's subagent route the harness has already brought the answer back to the
  main agent, so the taker writes the text on the line and sends nothing. When turns
  has no delivery for the ref (`unknown delivery`), no answer of that round ever reached
  turns: a first round ends the recall `lost` (`gone`), a later one ends the stream with
  the line `back` and the round's loss noted. A taker gives no further round, and ends
  the record as the stream's end does.

Standard library at import; the core and turns are imported at use.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from . import recalls, records

#: The line field naming the process that took the line over.
TAKER = "taker"
#: What `take` did with a line.
NONE = "none"
LEFT = "left"
ACKED = "acked"
SETTLED = "settled"
#: The reason on a recall a taker found no answer for.
GONE = "gone"
#: How turns' `Lost` reason opens when turns holds no delivery for a ref.
UNKNOWN_DELIVERY = "unknown delivery"


def me() -> dict | None:
    """This process's identity as a line holds it."""
    import moxi.proc

    found = moxi.proc.identity(os.getpid())
    return {"pid": found.pid, "started": found.started} if found is not None else None


def owner(line: dict) -> dict | None:
    """The process that owns `line`: its taker, else its child."""
    found = line.get(TAKER) or line.get("child")
    return found if isinstance(found, dict) else None


def owner_gone(line: dict) -> bool:
    """Whether `line`'s owner has ended, or the line names none."""
    import moxi.proc

    found = owner(line)
    try:
        proc = moxi.proc.ProcId(int(found["pid"]), float(found["started"]))
    except (TypeError, KeyError, ValueError):
        return True
    return not moxi.proc.alive(proc)


def needs_taking(line: dict) -> bool:
    """Whether `line` is a recall's line still `open` or still kept."""
    return bool(line.get("recall")) and (line.get("state") == recalls.OPEN
                                         or line.get("kept") is True)


def takeable(line: dict | None) -> bool:
    """Whether a taker may claim `line` now."""
    return line is not None and needs_taking(line) and owner_gone(line)


def claim(session_id: str, recall_id: str, identity: dict | None) -> dict | None:
    """Under the session's lock, claim recall `recall_id`'s line for `identity` when it
    is still takeable, and return it as written; None when it is not."""
    with recalls.session_lock(session_id):
        line = recalls.find_line(session_id, recall_id)
        if not takeable(line):
            return None
        line[TAKER] = identity
        return recalls.put_line(session_id, line)


def round_number(ref: str) -> int | None:
    """The round a ref `<recall>:<N>` names."""
    try:
        return int(str(ref).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _read_end(session_id: str, until: str = "ended") -> Callable[[str], object]:
    def read(ref: str):
        import moxi_turns as client

        return client.wait_ref(ref, session_id=session_id, until=until, timeout_s=0)

    return read


def _note_fork(recall, ref: str, read_start: Callable[[str], object]) -> None:
    """Write the fork's id on a line that names none, read from the round's start
    before its end is acked (turns drops the kept start with the end). A start that
    can't be read leaves the line as it is."""
    from moxi_turns import Started

    from . import recall_stream

    if recall.line.get("fork") is not None:
        return
    try:
        start = read_start(ref)
    except Exception:  # noqa: BLE001 - a start not read leaves the id unknown
        return
    if isinstance(start, Started):
        recall_stream.note_fork(recall, start.agent_id)


def _as_gone(end):
    """An `unknown delivery` end read as the round's fork gone."""
    from moxi_turns import Lost

    if isinstance(end, Lost) and str(end.reason or "").startswith(UNKNOWN_DELIVERY):
        return Lost(f"{GONE}: {end.reason}", ref=end.ref)
    return end


def _ack_kept(recall, ref: str, route) -> str:
    with recalls.session_lock(recall.session_id):
        current = recalls.find_line(recall.session_id, recall.recall_id)
        if current is None or current.get("kept") is not True:
            return NONE
        recall.line = current
        try:
            route.ack(ref)
        except Exception:  # noqa: BLE001 - an ack not taken leaves the mark for a taker
            return LEFT
        recall.write(kept=False)
    return ACKED


def _end_record(recall) -> None:
    """End the record of a recall no further round will be asked of: `lost` beside a
    `lost` line, else `ended`. A record already over is left as it is."""
    current = records.read(recall.session_id, recall.recall_id) or recall.record
    if current.get("state") in records.DONE:
        return
    hurried = current.get("state") == records.HURRIED or bool(recall.line.get("hurried"))
    if recall.line.get("state") == recalls.LOST:
        recall.put(record={"state": records.LOST, "reason": recall.line.get("reason"),
                           "error": recall.line.get("error")}, hurried=hurried)
        return
    recall.put(record={"state": records.ENDED}, hurried=hurried)


def take(session_id: str, recall_id: str, *, identity: dict | None = None,
         read_end: Callable[[str], object] | None = None,
         read_start: Callable[[str], object] | None = None, route=None) -> str:
    """Take recall `recall_id`'s line over when its owner is gone, and finish what its
    owner left. Returns `none` (not taken), `left` (turns holds no end for its round
    yet, or the ack was not taken), `acked` or `settled`. See the module's docstring."""
    from . import recall_child, recall_stream

    line = claim(session_id, recall_id, me() if identity is None else identity)
    if line is None:
        return NONE
    record = records.read(session_id, recall_id) or {
        "recall": recall_id, "session": session_id, "state": records.RUNNING,
        "harness": line.get("harness"), "start": line.get("start")}
    recall = recall_child.Recall(record, line=line)
    if route is None:
        from . import recall_route

        route = recall_route.for_recall(recall)
    ref = line.get("ref")
    if line.get("state") != recalls.OPEN:
        if not ref:
            return NONE
        outcome = _ack_kept(recall, str(ref), route)
        _end_record(recall)
        return outcome
    if not ref:
        recall_child.lose(recall, GONE, "the recall's child ended before it asked the fork")
        return SETTLED
    number = round_number(ref)
    try:
        end = (read_end or _read_end(session_id))(str(ref))
    except Exception:  # noqa: BLE001 - no end yet, or turns can't be asked: left for later
        return LEFT
    _note_fork(recall, str(ref), read_start or _read_end(session_id, "started"))
    recall_stream.settle(recall, number or 1, _as_gone(end), route)
    _end_record(recall)
    return SETTLED


def take_all(*, sessions: list[str] | None = None, identity: dict | None = None,
             skip: str | None = None, read_end=None, read_start=None,
             route=None) -> dict[str, str]:
    """Take every takeable line of `sessions` (every session with a log when None),
    leaving out recall `skip`. Returns what was done with each line taken, by recall."""
    identity = me() if identity is None else identity
    done: dict[str, str] = {}
    for session_id in (recalls.logged_sessions() if sessions is None else sessions):
        if not recalls.is_safe(session_id):
            continue
        for line in recalls.read_lines(session_id):
            recall_id = line.get("recall")
            if not recall_id or recall_id == skip or not takeable(line):
                continue
            try:
                outcome = take(session_id, str(recall_id), identity=identity,
                               read_end=read_end, read_start=read_start, route=route)
            except Exception:  # noqa: BLE001 - one line that fails leaves the others
                outcome = LEFT
            if outcome != NONE:
                done[str(recall_id)] = outcome
    return done


def session_needs_taking(session_id: str) -> bool:
    """Whether any of the session's lines is still `open` or kept."""
    return recalls.is_safe(session_id) and any(
        needs_taking(line) for line in recalls.read_lines(session_id))


def start_taker(session_id: str) -> int:
    """Start `moxi-memory-subconscious --take` for the session through the detached
    starter, so the taker runs outside every session and can send what a line holds,
    and return the starter's process id without waiting for it."""
    from .recall_start import spawn_starter

    return spawn_starter(["--take", "--session", session_id])


__all__ = ["TAKER", "NONE", "LEFT", "ACKED", "SETTLED", "GONE", "UNKNOWN_DELIVERY", "me",
           "owner", "owner_gone", "needs_taking", "takeable", "claim", "round_number", "take", "take_all",
           "session_needs_taking", "start_taker"]
