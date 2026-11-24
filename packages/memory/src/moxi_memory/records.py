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
(`answered`), how many rounds were asked (`rounds`), the brief the fork was given, and
when it was written first and last.

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
#: The fields of a record.
RECORD_FIELDS = ("recall", "session", "harness", "state", "reason", "error", "start",
                 "text", "senders", "settings", "cwd", "notes_dir", "route", "fork",
                 "delivery", "tools", "answered", "rounds", "brief", "at", "updated")


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
    return sorted(out, key=lambda r: r.get("at") or 0)


def write(record: Mapping[str, object], *, now: float | None = None) -> dict:
    """Write `record` whole and return it as written. The caller holds the session's
    lock."""
    from moxi import files

    now = time.time() if now is None else now
    written = dict(record)
    if written.get("state") not in RECORD_STATES:
        raise ValueError(f"a record's state is one of {', '.join(RECORD_STATES)}, not "
                         f"{written.get('state')!r}")
    written.setdefault("at", now)
    written["updated"] = now
    path = record_path(str(written["session"]), str(written["recall"]))
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


__all__ = ["RECORDS", "STARTING", "RUNNING", "HURRIED", "BACK", "ENDED", "LOST",
           "RECORD_STATES", "DONE", "RECORD_FIELDS", "records_dir", "record_path", "read",
           "session_records", "write", "update", "hurry_running"]
