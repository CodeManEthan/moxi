"""What a relaunch run tells, and whom, when it ends without relaunching its session, or
relaunches it flagged.

    note(conn, run, session, wait_s=)          # a self run that left its session in place
    notify(conn, run, row, comms=, ...)         # the directors, then the fallback

| outcome | session | told |
|---|---|---|
| `relaunched`, not flagged | new | nobody |
| `relaunched`, flagged (its word the finding) | new, held on a screen or not ready | directors, then fallback |
| `expired`, `held`, `not-held`, `stopped` | untouched | a self run's session, one note |
| `changed` | untouched: a `/clear` since, the conversation that armed the run is gone | a self run's session, one note |
| `refused` | untouched: its launch would have been refused | a self run's session, one note; a director's directors, then fallback |
| `refused`, a repair | down: the repair's launch would have been refused | directors, then fallback, with the `--from-record` command to repeat it |
| `not-exited` | still up, maybe at its exit dialog | a self run's session, one note; a director's directors, then fallback |
| `exit-pending` | still up, at its exit dialog | directors, then fallback |
| `error`, before the exit | still up | a self run's session, one note; a director's or repair's directors, then fallback |
| `relaunch-failed`, `first-turn-not-sent`, `gone`, `error` | down, or up without its handoff | directors, then fallback |
| `note-not-taken` | the note was refused or lost | directors, then fallback |

**The note** goes to the session as a plain delivery through turns, bounded by
`relaunch.wait_s`; refused, lost or not answered in time, it is not taken, and that is told
past the session as `note-not-taken`. The run's own outcome stays what it ended with (the
run was ended before the note, so the session can arm again at once); the note's fate is
an event of the run.

**A notice** goes through comms under the launcher's reserved send-only name, `console`,
to the directors the launch named (`values_json["director"]`) and the box's policy
directors (`policy_directors`), each once. The policy names are comms'
`policy_directors()` read at the notice, so only names provisioned now; they go to a
launch with a comms identity that didn't opt out (`takes_policy_directors = false`).
Refused, or with no director, it goes once more to `relaunch.fallback` when that names
someone. comms' daemon offline: one more try after `OFFLINE_RETRY_S`, then it stays
recorded.

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
from .catalog import TAKES_POLICY

log = logging.getLogger("moxi.launcher.notices")

#: The outcomes after which a self run's session is told, by a note: it was not touched.
NOTE_OUTCOMES = ("expired", "held", "not-held", "stopped", "refused", "changed")
#: The outcomes after which a self run's session is still up though the run tried to act
#: on it, also told by a note; `error` only when the run wrote no `exit` event.
LEFT_IN_PLACE = ("not-exited", "error")
#: The outcomes told past the session, to the directors and then the fallback; `refused`
#: is told so only for a director's run or a repair, a self run's session by its note.
NOTICE_OUTCOMES = ("not-exited", "exit-pending", "relaunch-failed", "first-turn-not-sent",
                   "gone", "error", "refused")
#: The outcome of a relaunch whose new session runs; told past the session only when the
#: launch flagged it (`is_flagged`).
RELAUNCHED = "relaunched"
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
#: The note for an end that left the session in place by another way, by outcome. Its
#: why field is the run's word in brackets and its said field the word and the detail,
#: each without the word when the word repeats the outcome the note names.
NOTES = {
    "not-exited": ("Relaunch note from launcher: run {run} ended not-exited ({detail}). The "
                   "relaunch did not exit this session{why}: carry on with your work, and "
                   "arm it again at your next pause point if you still need it."),
    "error": ("Relaunch note from launcher: run {run} ended error ({said}). The relaunch "
              "stopped with an error before the exit: carry on with your work."),
    "changed": ("Relaunch note from launcher: run {run} did not run ({detail}). Nothing was "
                "exited: carry on, and arm a run of this conversation's own if you need "
                "one."),
}

_SESSION = {
    "not-exited": "The session is still up, and may be at its exit dialog.",
    "exit-pending": "The session is at its exit dialog, which was left for a person to "
                    "answer.",
    "relaunch-failed": "The session is down and was not launched again.",
    "first-turn-not-sent": "The session was launched again, without its handoff.",
    "gone": "The session is down.",
    "error": "The session may be down.",
    "refused": "Nothing was exited or launched: the launch would have been refused.",
    NOTE_NOT_TAKEN: "The session was not touched, and did not take the note saying so.",
    RELAUNCHED: "The session was launched again and is running, but it may be held on a "
                "screen or not yet ready: attach to its slot to see it.",
}
#: What a repair that ended `refused` says of its session, which it found down.
_REPAIR_REFUSED = ("The session is down and nothing was launched: the repair's launch would "
                   "have been refused.")


def is_flagged(run: store.Run) -> bool:
    """Whether `run` relaunched its session and the launch flagged the new one: it ended
    `relaunched` with the finding as its word."""
    return run.outcome == RELAUNCHED and bool(run.word)


def owes_note(run: store.Run, outcome: str | None,
              events: Sequence[store.RunEvent] = ()) -> bool:
    """Whether a self run that ended `outcome` tells its session by a note: the session
    was left in place, untouched (`NOTE_OUTCOMES`), not exited (`not-exited`), or past an
    error before the run's `exit` event."""
    if run.mode != "self" or outcome is None:
        return False
    if outcome in NOTE_OUTCOMES:
        return True
    if outcome not in LEFT_IN_PLACE:
        return False
    return outcome != "error" or not any(event.kind == "exit" for event in events)


def note_text(run: store.Run) -> str:
    """The note a self run's session left in place is sent."""
    word = _own_word(run)
    detail = run.detail or "no detail"
    return NOTES.get(run.outcome or "", NOTE).format(
        run=run.run_id, word=run.word or run.outcome or "ended", detail=detail,
        why=f" ({word})" if word else "", said=f"{word}: {detail}" if word else detail)


def _own_word(run: store.Run) -> str:
    """The run's word, or nothing when it only repeats the run's outcome (gone, gone)."""
    return "" if not run.word or run.word == run.outcome else run.word


def _sentence(words: str) -> str:
    """`words` as a sentence of its own: its first letter capitalised, and a full stop
    added when it ends without closing punctuation."""
    words = words.strip()
    if not words:
        return words
    words = words[0].upper() + words[1:]
    return words if words.endswith((".", "!", "?")) else f"{words}."


def note(conn: sqlite3.Connection, run: store.Run, session: Any, *, wait_s: float) -> bool:
    """Deliver the note to `session` (turns' `Session`) and say whether it was taken:
    turns says its turn started within `wait_s`. Writes a `note` event either way."""
    text = note_text(run)
    try:
        delivery = session.deliver(text, ref=f"launcher-note:{run.run_id}")
        value = delivery.wait_started(wait_s)
    except TimeoutError:
        verdict, words = "unconfirmed", f"no answer within {wait_s:g} s"
    except Exception as exc:  # noqa: BLE001  (a note that fails is a note not taken)
        verdict, words = "failed", _words(exc)
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
    command when the outcome allows one (for `exit-pending`, once the session has
    exited). A repair that ended `refused` left its session down: its text says so and
    names the command that repeats the repair, of the run it repaired. The run's word is
    left out when it repeats the outcome, and its detail is a sentence of its own."""
    session = run.session_id
    slot = f" in slot {run.slot}" if run.slot else ""
    said, word = outcome, _own_word(run)
    if outcome == NOTE_NOT_TAKEN:
        said += f" (the run ended {run.outcome}" + (f": {word}" if word else "") + ")"
    elif word and word != outcome:
        said += f": {word}"
    lines = [f"Relaunch notice from launcher: run {run.run_id} for session {session}{slot} "
             f"ended {said}."]
    if run.detail and outcome != NOTE_NOT_TAKEN:
        lines.append(_sentence(run.detail))
    refused_repair = outcome == "refused" and run.mode == "repair"
    lines.append(_REPAIR_REFUSED if refused_repair else _SESSION.get(outcome, ""))
    if refused_repair:
        lines.append("Once the launch's problem is fixed, to try the repair again: moxi "
                     f"launcher relaunch --from-record {run.repair_of or run.run_id}")
    elif repairable:
        when = ("Once a person has answered the dialog and the session has exited, to "
                "launch it again" if run.outcome == "exit-pending" else "To launch it again")
        lines.append(f"{when} from the run's record: moxi launcher relaunch "
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


def policy_directors(row: store.LaunchRow | None, comms: Any | None) -> list[str]:
    """The box's policy directors that direct the launch now: comms'
    `policy_directors()`, read at this call, so the names provisioned as standing
    identities now, qualified as comms qualifies them (a bare name is the global
    identity). Empty for a launch without a comms identity, one whose stored values carry
    `takes_policy_directors = false`, a comms older than the call, or a read that fails."""
    if row is None or comms is None or not row.identity:
        return []
    if row.values.get(TAKES_POLICY) is False:
        return []
    read = getattr(comms, "policy_directors", None)
    if read is None:
        return []
    try:
        names = read()
    except Exception as exc:  # noqa: BLE001  (an unread setting adds no director)
        log.warning("comms' policy directors could not be read: %s", exc)
        return []
    return [str(name).strip() for name in names or () if str(name).strip()]


def recipients(comms: Any, named: Sequence[str], policy: Sequence[str]) -> list[str]:
    """The launch's own directors, then the policy names, each once: a policy name the
    launch also named (as comms reads a bare name, the global identity) is not added."""
    out = list(dict.fromkeys(named))
    seen = {_as_comms_reads(comms, name) for name in out}
    for name in policy:
        if _as_comms_reads(comms, name) not in seen:
            seen.add(_as_comms_reads(comms, name))
            out.append(name)
    return out


def _as_comms_reads(comms: Any, name: str) -> str:
    """`name` as comms qualifies a recipient: a bare name is the global identity."""
    try:
        bare, scope = comms.split_tag(name)
        return comms.qualify(bare, scope or comms.GLOBAL)
    except Exception:  # noqa: BLE001  (comms' own form unknown: the name as given)
        return name


def notify(conn: sqlite3.Connection, run: store.Run, row: store.LaunchRow | None,
           outcome: str, *, comms: Any | None, fallback: str = "",
           retry_s: float = 30.0, repairable: bool = False,
           clock: Callable[[], float] = time.monotonic,
           sleep: Callable[[float], None] = time.sleep) -> str:
    """Tell the directors, the launch's own and the box's policy directors
    (`recipients`), then the fallback, that `run` ended `outcome`, and return what
    became of it: `sent`, `refused`, `offline`, `unsent` or `recorded` (no comms). Every
    try is a run event."""
    body = text(run, row, outcome, repairable=repairable)
    if comms is None:
        store.add_run_event(conn, run.run_id, "notice",
                            {"verdict": "recorded", "why": "comms is not installed",
                             "text": body})
        return "recorded"
    to = recipients(comms, directors(row), policy_directors(row, comms))
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
                            {"verdict": "unsent", "why": "the launch has no director "
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
        except Exception as exc:  # noqa: BLE001  (a send that raises is recorded)
            return _record(conn, run, "notice-refused", to, body, "error",
                           _words(exc), fallback=fallback)
        outcome = getattr(result, "outcome", None)
        refusal = getattr(result, "refusal", None)
        code = getattr(refusal, "code", None)
        if outcome == "sent":
            return _record(conn, run, "notice", to, body, "sent", None, fallback=fallback,
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
            return _record(conn, run, "notice", to, body, "offline", why, fallback=fallback)
        return _record(conn, run, "notice-refused", to, body, "refused",
                       f"{code}: {why}" if code and why != code else why, fallback=fallback)


def _record(conn: sqlite3.Connection, run: store.Run, kind: str, to: Sequence[str],
            body: str, verdict: str, why: str | None, *, fallback: bool,
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


#: The line comms adds under a cold recipient's hold line when the sender directs its
#: launch (`hold_line`).
RELAUNCH_LINE = ("To reach it now, relaunch it fresh from its handoff: moxi launcher "
                 "relaunch {name}.")


def hold_line(by: str, recipient: str, session: str | None, *,
              env: Mapping[str, str] | None = None) -> str | None:
    """comms' `moxi.comms.hold_lines` entry: `RELAUNCH_LINE` for the cold `recipient`
    when `by` (the sender's qualified name) is a director of the live launch that holds
    `session`, as a director's relaunch checks it (`relaunch.is_director` over
    `directors` and `policy_directors`). None for no session id, no launch log, no launch
    holding the id, a launch whose endpoint never claimed it or has ended, comms not
    loadable at the launcher's interface, or a sender that is not a director. Opens the
    launch log for this read alone; an error from it raises, and comms leaves the line
    out."""
    if not by or not session:
        return None
    with store.reading(env=env) as conn:
        row = None if conn is None else store.resolve(conn, session)
    if row is None or not row.endpoint or store.endpoint_gone(row.endpoint):
        return None
    # Imported here: relaunch imports this module. job comes first, as the launcher's
    # own entries load it: relaunch imported before it meets fork half loaded.
    from . import job, presets, relaunch  # noqa: F401  (job: the import order)

    comms = presets.comms_api()
    if comms is None:
        return None
    if not relaunch.is_director(comms, by, directors(row), row.scope or None,
                                policy=policy_directors(row, comms)):
        return None
    return RELAUNCH_LINE.format(name=recipient)


def settings_of(current: Mapping[str, Any]) -> dict[str, Any]:
    """The settings a notice reads: the fallback and the reserved-name retry bound.
    `current` is the launcher's `moxi.config.Settings`, which reads by key and has no
    `get`, or a mapping that holds the keys."""
    return {"fallback": str(current["relaunch.fallback"] or ""),
            "retry_s": float(current["relaunch.notice_retry_s"])}


__all__ = ["LEFT_IN_PLACE", "MODULE", "NAME_RESERVED", "NOTE", "NOTES", "NOTE_NOT_TAKEN",
           "NOTE_OUTCOMES", "NOTICE_OUTCOMES", "OFFLINE_RETRY_S", "RELAUNCHED",
           "RELAUNCH_LINE", "RESERVED_RETRY_S", "SENDER", "directors", "hold_line",
           "is_flagged", "note",
           "note_text", "notify", "owes_note", "policy_directors", "recipients",
           "settings_of", "text"]
