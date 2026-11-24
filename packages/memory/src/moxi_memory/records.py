"""The subconscious's records: one per recall, saying where the recall's fork is.

A record lives at `subconscious/records/<session>/<recall>.json` in memory's state
directory, beside the log (`moxi_memory.recalls`), and is written only under the
session's subconscious lock (`recalls.session_lock`), each write replacing the file whole
(`moxi.files.atomic_write`), so a reader takes no lock.

A record's state (`RECORD_STATES`) says where the recall's fork stands, and the guards read
it: `starting` from its write until the child has started the fork, `running` once it
has, `hurried` once a newer recall of the session started (its fork is refused further
calls and answers from what it has), `back` once its first answer came back, `ended` when
the recall is over with an answer back, and `lost` when it ended with none. A recall is
running until its record is `ended` or `lost`.

A record holds (`RECORD_FIELDS`): its id (`recall`), the session and its harness, its
state, the reason for a `lost` state and the error behind it, how it started (`start`:
`prompt` or `asked`), the prompt's text or the topic (`text`), the prompt's
`delivery_senders` (`senders`), the subconscious settings its child runs on (`settings`,
every `subconscious.*` key by its last name), the session's launch directory (`cwd`) and
notes directory override (`notes_dir`), the route, the fork's id and its fork delivery's
id, the tool calls the fork has made (`tools`), whether any answer came back
(`answered`), how many rounds were asked (`rounds`), the brief the fork was given, the
process that holds the recall (`holder`, `moxi_memory.recall_holder`), when it was
written first and last (`at`, `updated`), the clock that counts from boot at its first
write where the system has one (`at_boot`), and its place among the session's records
(`seq`, one more than the highest the session's records held when it was first
written). Records are ordered by `seq`, never by `at`, which the clamp below moves;
records written before `seq` come first, by `at`.

**An `at` later than now (the clamp).** `at` is the wall clock of the process that
first wrote the record, so after the clock steps back it can be later than now, and
every age read from it would stay below zero. Whoever holds the session's lock over a
record and finds its `at` later than its own now writes `at` back as that now: every
write of a record (`write`), the guard's judge (`clamp`) and the day sweep. A reader
without the lock reads such an `at` as now and writes nothing.

**The day sweep (`sweep_old`).** A record stays, in whatever state, until it is a day
old (`SWEEP_AFTER_S` from when it was first written), so a fork still calling after its
recall ended is judged by its own record. Then the sweep removes it, except a record
whose log line still carries the kept mark: turns still keeps that round's end for
memory, and the record stays until a taker settles the line. Every recall child sweeps
at its start, and so does the taker memory's `gone` listener starts; the sweep clamps a
record whose `at` is later than now as it goes.

Standard library at import; the core is imported at the first write.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path

from . import recalls

#: The records' directory under the subconscious's directory.
RECORDS = "records"

#: A record's states.
STARTING = "starting"
RUNNING = "running"
HURRIED = "hurried"
BACK = "back"
ENDED = "ended"
LOST = "lost"
RECORD_STATES = (STARTING, RUNNING, HURRIED, BACK, ENDED, LOST)
#: The states of a recall that is over.
DONE = (ENDED, LOST)
#: How old a record is, in seconds, when the day sweep removes it.
SWEEP_AFTER_S = 86400.0
#: How long a recall's child has, in seconds from its record's `at`, to write its log
#: line. A record still with no line past it has a child that never ran or ended first.
STARTING_GRACE_S = 30.0
#: The fields of a record.
RECORD_FIELDS = ("recall", "session", "harness", "state", "reason", "error", "start",
                 "text", "senders", "settings", "cwd", "notes_dir", "route", "fork",
                 "delivery", "tools", "answered", "rounds", "brief", "holder", "seq",
                 "at", "at_boot", "updated")


def records_dir(session_id: str) -> Path:
    """The session's records directory."""
    if not recalls.is_safe(session_id):
        raise ValueError(f"{session_id!r} is not a session id memory keeps records for")
    return recalls.subconscious_dir() / RECORDS / session_id


def record_path(session_id: str, recall_id: str) -> Path:
    """The file of recall `recall_id`'s record."""
    if not recalls.is_safe(recall_id):
        raise ValueError(f"{recall_id!r} is not a recall id")
    return records_dir(session_id) / f"{recall_id}.json"


def read(session_id: str, recall_id: str) -> dict | None:
    """Recall `recall_id`'s record, or None when there is none or it can't be read."""
    try:
        text = record_path(session_id, recall_id).read_text(encoding="utf-8")
        record = json.loads(text)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def session_records(session_id: str) -> list[dict]:
    """Every record of the session, oldest first."""
    try:
        paths = list(records_dir(session_id).glob("*.json"))
    except (OSError, ValueError):
        return []
    out = []
    for path in paths:
        if not recalls.is_safe(path.stem):
            continue
        record = read(session_id, path.stem)
        if record is not None:
            out.append(record)
    return sorted(out, key=order)


def order(record: Mapping[str, object]) -> tuple:
    """A record's place among its session's records: by `seq`, those with none first by
    `at`."""
    seq = record.get("seq")
    if isinstance(seq, int) and not isinstance(seq, bool):
        return (1, seq, 0.0)
    at = record.get("at")
    return (0, 0, float(at) if isinstance(at, (int, float)) else 0.0)


def _next_seq(session_id: str) -> int:
    seqs = [r.get("seq") for r in session_records(session_id)]
    return 1 + max([s for s in seqs if isinstance(s, int) and not isinstance(s, bool)],
                   default=0)


def write(record: Mapping[str, object], *, now: float | None = None) -> dict:
    """Write `record` whole and return it as written, its `at` clamped to now (see the
    module's docstring). A new record gets its `seq` and, when `now` is not given, the
    clock that counts from boot (`at_boot`). The caller holds the session's lock."""
    from moxi import files

    from . import recall_holder

    real = now is None
    now = time.time() if now is None else now
    written = dict(record)
    if written.get("state") not in RECORD_STATES:
        raise ValueError(f"a record's state is one of {', '.join(RECORD_STATES)}, not "
                         f"{written.get('state')!r}")
    if "at" not in written:
        written["at"] = now
        if real and "at_boot" not in written:
            written["at_boot"] = recall_holder.boot_now()
    elif isinstance(written["at"], (int, float)) and written["at"] > now:
        written["at"] = now
    written["updated"] = now
    path = record_path(str(written["session"]), str(written["recall"]))
    if "seq" not in written and not path.exists():
        written["seq"] = _next_seq(str(written["session"]))
    files.private_dir(path.parent)
    files.atomic_write(path, json.dumps(written, sort_keys=True, ensure_ascii=False) + "\n")
    return written


def update(session_id: str, recall_id: str, *, now: float | None = None,
           **fields: object) -> dict | None:
    """Merge `fields` into recall `recall_id`'s record and write it; None when there is
    no record. The caller holds the session's lock."""
    record = read(session_id, recall_id)
    if record is None:
        return None
    record.update(fields)
    return write(record, now=now)


def clamp(session_id: str, recall_id: str, *, now: float | None = None) -> dict | None:
    """Recall `recall_id`'s record, its `at` written back as now when it was later than
    now; None when there is none. The caller holds the session's lock."""
    now = time.time() if now is None else now
    record = read(session_id, recall_id)
    if record is None:
        return None
    at = record.get("at")
    if isinstance(at, (int, float)) and at > now:
        return write(record, now=now)
    return record


def hurry_running(session_id: str, *, now: float | None = None) -> list[str]:
    """Set every running recall of the session `hurried`, and return their ids. The
    caller holds the session's lock."""
    hurried = []
    for record in session_records(session_id):
        if record.get("state") in DONE or record.get("state") == HURRIED:
            continue
        record["state"] = HURRIED
        write(record, now=now)
        hurried.append(str(record["recall"]))
    return hurried


def session_ids() -> list[str]:
    """Every session that has a records directory."""
    try:
        return sorted(p.name for p in (recalls.subconscious_dir() / RECORDS).iterdir()
                      if p.is_dir() and recalls.is_safe(p.name))
    except OSError:
        return []


def find(recall_id: str) -> dict | None:
    """Recall `recall_id`'s record, in whichever session's directory holds it, or None.
    A recall's id is unique across sessions, so the first found is the one."""
    if not recalls.is_safe(recall_id):
        return None
    for session_id in session_ids():
        if record_path(session_id, recall_id).is_file():
            return read(session_id, recall_id)
    return None


def sweep_old(*, sessions: list[str] | None = None, now: float | None = None) -> list[str]:
    """Remove every record older than `SWEEP_AFTER_S` whose log line does not carry the
    kept mark, and clamp every record whose `at` is later than now, in `sessions` (every
    session with records when None), each session under its lock, and return the ids
    removed."""
    now = time.time() if now is None else now
    if sessions is None:
        sessions = session_ids()
    removed = []
    for session_id in sessions:
        if not recalls.is_safe(session_id):
            continue
        timed = [r for r in session_records(session_id)
                 if isinstance(r.get("at"), (int, float))]
        old = [r for r in timed if now - r["at"] >= SWEEP_AFTER_S]
        ahead = [r for r in timed if r["at"] > now]
        if not old and not ahead:
            continue
        with recalls.session_lock(session_id):
            for record in ahead:
                if recalls.is_safe(str(record.get("recall"))):
                    clamp(session_id, str(record["recall"]), now=now)
            kept = {line.get("recall") for line in recalls.read_lines(session_id)
                    if line.get("kept") is True}
            for record in old:
                recall_id = str(record.get("recall"))
                if recall_id in kept or not recalls.is_safe(recall_id):
                    continue
                try:
                    record_path(session_id, recall_id).unlink(missing_ok=True)
                except OSError:
                    continue
                removed.append(recall_id)
            try:
                records_dir(session_id).rmdir()
            except OSError:
                pass
    return removed


__all__ = ["RECORDS", "STARTING", "RUNNING", "HURRIED", "BACK", "ENDED", "LOST",
           "RECORD_STATES", "DONE", "RECORD_FIELDS", "records_dir", "record_path", "read",
           "session_records", "order", "write", "update", "clamp", "hurry_running",
           "SWEEP_AFTER_S",
           "STARTING_GRACE_S", "session_ids", "find", "sweep_old"]
