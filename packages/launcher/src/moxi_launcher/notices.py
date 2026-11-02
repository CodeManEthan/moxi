"""What a relaunch run tells, and whom, when it ends without relaunching its session.

    note(conn, run, session, wait_s=)          # a self run that never touched its session
    notify(conn, run, row, comms=, ...)         # the directors, then the fallback

| outcome | session | told |
|---|---|---|
| `relaunched` | new | nobody |
| `expired`, `held`, `not-held`, `stopped` | untouched | a self run's session, one note |
| `not-exited`, `exit-pending` | still up, maybe at its exit dialog | directors, then fallback |
| `relaunch-failed`, `first-turn-not-sent`, `gone`, `error` | down, or up without its handoff | directors, then fallback |
| `note-not-taken` | the note was refused or lost | directors, then fallback |

**The note** goes to the session as a plain delivery through turns, bounded by
`relaunch.wait_s`; refused, lost or not answered in time, it is not taken, and that is told
past the session as `note-not-taken`. The run's own outcome stays what it ended with (the
run was ended before the note, so the session can arm again at once); the note's fate is
an event of the run.

**A notice** goes through comms under the launcher's reserved send-only name, `console`,
to the directors the launch named (`values_json["director"]`). Refused, or with no
director, it goes once more to `relaunch.fallback` when that names someone. comms' daemon
offline: one more try after `OFFLINE_RETRY_S`, then it stays recorded.

**A notice is sent only from a process outside every session; a refusal that can clear is
retried within a bound; a notice never sent stays in the run's events.** comms refuses a
send under a reserved name from inside a live session, and from a process whose ancestry
it can't read yet (`name-reserved`). The relaunch and repair jobs are slots jobs, outside
every session from their start, so the refusal can only be the passing kind: it is tried
again every `RESERVED_RETRY_S` for `relaunch.notice_retry_s`, then written as a
`notice-refused` event with the text, and the fallback is tried the same way. Without
comms nothing is sent and the text is recorded. The run's events are the notice log.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from . import store

log = logging.getLogger("moxi.launcher.notices")

#: The outcomes after which a self run's session is told, by a note: it was not touched.
NOTE_OUTCOMES = ("expired", "held", "not-held", "stopped")
#: The outcomes told past the session, to the directors and then the fallback.
NOTICE_OUTCOMES = ("not-exited", "exit-pending", "relaunch-failed", "first-turn-not-sent",
                   "gone", "error")
#: What a note that was not taken is told as.
NOTE_NOT_TAKEN = "note-not-taken"
#: The module and the reserved name a notice goes out under.
MODULE, SENDER = "launcher", "console"
#: How often a notice refused for its sender's place is tried again, in seconds.
RESERVED_RETRY_S = 2.0
#: The refusal that can clear: the daemon could not place the sender outside a session.
NAME_RESERVED = "name-reserved"
#: How long a notice waits before its one more try when comms' daemon is offline.
OFFLINE_RETRY_S = 60.0

NOTE = ("Relaunch note from launcher: run {run} did not run ({word}: {detail}). Nothing "
        "was exited. Carry on, and arm it again at your next pause point if you still "
        "need it.")

_SESSION = {
    "not-exited": "The session is still up, and may be at its exit dialog.",
    "exit-pending": "The session is at its exit dialog, which was left for a person to "
                    "answer.",
    "relaunch-failed": "The session is down and was not launched again.",
    "first-turn-not-sent": "The session was launched again, without its handoff.",
    "gone": "The session is down.",
    "error": "The session may be down.",
    NOTE_NOT_TAKEN: "The session was not touched, and did not take the note saying so.",
}


def note_text(run: store.Run) -> str:
    """The note a self run's untouched session is sent."""
    return NOTE.format(run=run.run_id, word=run.word or run.outcome or "ended",
                       detail=run.detail or "no detail")


def note(conn: sqlite3.Connection, run: store.Run, session: Any, *, wait_s: float) -> bool:
    """Deliver the note to `session` (turns' `Session`) and say whether it was taken:
    turns says its turn started within `wait_s`. Writes a `note` event either way."""
    text = note_text(run)
    try:
        delivery = session.deliver(text, ref=f"launcher-note:{run.run_id}")
        value = delivery.wait_started(wait_s)
    except TimeoutError:
        verdict, words = "unconfirmed", f"no answer within {wait_s:g} s"
    except Exception as error:  # noqa: BLE001  (a note that fails is a note not taken)
        verdict, words = "failed", _words(error)
    else:
        kind = getattr(value, "KIND", type(value).__name__.lower())
        verdict = "taken" if kind == "started" else kind
        words = getattr(value, "reason", "") or ""
    store.add_run_event(conn, run.run_id, "note",
                        {"verdict": verdict, "why": words, "text": text})
    return verdict == "taken"


def text(run: store.Run, row: store.LaunchRow | None, outcome: str, *,
         repairable: bool = False) -> str:
    """A notice's text: the session, the slot, the run, the outcome, and the repair
    command when the outcome allows one."""
    session = run.session_id
    slot = f" in slot {run.slot}" if run.slot else ""
    said = outcome
    if outcome == NOTE_NOT_TAKEN:
        said += f" (the run ended {run.outcome}" + (f": {run.word}" if run.word else "") + ")"
    elif run.word:
        said += f": {run.word}"
    lines = [f"Relaunch notice from launcher: run {run.run_id} for session {session}{slot} "
             f"ended {said}."]
    if run.detail and outcome != NOTE_NOT_TAKEN:
        lines.append(run.detail)
    lines.append(_SESSION.get(outcome, ""))
    if repairable:
        lines.append(f"To launch it again from the run's record: moxi launcher relaunch "
                     f"--from-record {run.run_id}")
    return " ".join(line for line in lines if line)


def directors(row: store.LaunchRow | None) -> list[str]:
    """The directors the launch named, as comms names them."""
    if row is None:
        return []
    named = row.values.get("director") or []
    if isinstance(named, str):
        named = [named]
    return [str(name).strip() for name in named if str(name).strip()]


def notify(conn: sqlite3.Connection, run: store.Run, row: store.LaunchRow | None,
           outcome: str, *, comms: Any | None, fallback: str = "",
           retry_s: float = 30.0, repairable: bool = False,
           clock: Callable[[], float] = time.monotonic,
           sleep: Callable[[float], None] = time.sleep) -> str:
    """Tell the directors, then the fallback, that `run` ended `outcome`, and return what
    became of it: `sent`, `refused`, `offline`, `unsent` or `recorded` (no comms). Every
    try is a run event."""
    body = text(run, row, outcome, repairable=repairable)
    if comms is None:
        store.add_run_event(conn, run.run_id, "notice",
                            {"verdict": "recorded", "why": "comms is not installed",
                             "text": body})
        return "recorded"
    to = directors(row)
    fallback = fallback.strip()
    verdict = "unsent"
    if to:
        verdict = _send(conn, run, comms, to, body, retry_s, clock, sleep)
        if verdict in ("sent", "offline"):
            return verdict
    if fallback and fallback not in to:
        return _send(conn, run, comms, [fallback], body, retry_s, clock, sleep,
                     fallback=True)
    if not to:
        store.add_run_event(conn, run.run_id, "notice",
                            {"verdict": "unsent", "why": "the launch named no director "
                                                         "and relaunch.fallback is empty",
                             "text": body})
    return verdict


def _send(conn: sqlite3.Connection, run: store.Run, comms: Any, to: Sequence[str],
          body: str, retry_s: float, clock: Callable[[], float],
          sleep: Callable[[float], None], *, fallback: bool = False) -> str:
    deadline = clock() + retry_s
    offline_tried = False
    while True:
        try:
            result = comms.send_as(MODULE, SENDER, list(to), body)
        except Exception as error:  # noqa: BLE001  (a send that raises is recorded)
            return _record(conn, run, "notice-refused", to, body, "error",
                           _words(error), fallback)
        outcome = getattr(result, "outcome", None)
        refusal = getattr(result, "refusal", None)
        code = getattr(refusal, "code", None)
        if outcome == "sent":
            return _record(conn, run, "notice", to, body, "sent", None, fallback,
                           message_id=getattr(result, "message_id", None))
        if outcome == "refused" and code == NAME_RESERVED and clock() < deadline:
            sleep(RESERVED_RETRY_S)
            continue
        if outcome == "offline" and not offline_tried:
            offline_tried = True
            sleep(OFFLINE_RETRY_S)
            continue
        why = getattr(refusal, "text", None) or code or outcome
        if outcome == "offline":
            return _record(conn, run, "notice", to, body, "offline", why, fallback)
        return _record(conn, run, "notice-refused", to, body, "refused",
                       f"{code}: {why}" if code and why != code else why, fallback)


def _record(conn: sqlite3.Connection, run: store.Run, kind: str, to: Sequence[str],
            body: str, verdict: str, why: str | None, fallback: bool,
            **more: Any) -> str:
    detail: dict[str, Any] = {"verdict": verdict, "to": list(to), "fallback": fallback,
                              "text": body}
    if why:
        detail["why"] = why
    detail.update({k: v for k, v in more.items() if v is not None})
    store.add_run_event(conn, run.run_id, kind, detail)
    return verdict


def _words(error: BaseException) -> str:
    return str(error) or type(error).__name__


def settings_of(current: Mapping[str, Any]) -> dict[str, Any]:
    """The settings a notice reads: the fallback and the reserved-name retry bound."""
    return {"fallback": str(current.get("relaunch.fallback") or ""),
            "retry_s": float(current.get("relaunch.notice_retry_s", 30))}


__all__ = ["MODULE", "NAME_RESERVED", "NOTE", "NOTE_NOT_TAKEN", "NOTE_OUTCOMES",
           "NOTICE_OUTCOMES", "OFFLINE_RETRY_S", "RESERVED_RETRY_S", "SENDER", "directors",
           "note", "note_text", "notify", "settings_of", "text"]
