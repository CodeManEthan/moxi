"""Kept ends: the end of a delivery asked with `keep`, held in turns' state folder until
its asker acks it.

The endpoint writes a kept delivery's end here when the delivery settles, whatever
settles it (the harness, a refusal, the endpoint's bound or its own end), one file per
delivery: `STATE/turns/kept/<endpoint name>-<hash of the ref>.json`, 0600. A file holds
the delivery's `started` and `ended` values as they go on the wire, its id, `ref`, kind
and asker (`by`), the session's ids when it settled, the endpoint's name and when it
settled. It stays until an `ack` of its `ref` removes it: turns removes none by age, and
its doctor lists each one older than a day.

Who may read a file: a lookup names the session it asks about, by its ids (the current
one and the earlier ones of the same launch) or by the endpoint's name, and reads only the
files of that session. One `ref` holds one kept end per session: the endpoint refuses a
`keep` under a `ref` that a kept end of its session still holds (`RefKept`), here or in
its own record, so no kept end replaces another. Should two files of a session carry one
`ref` all the same, the newest is the one a lookup reads.

The endpoint reads and writes here under its own lock. A client reads and removes here
only for a session whose endpoint is gone, so the two never act on one file at once.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from moxi import files
from moxi.paths import Layout

from . import config as turns_config

log = logging.getLogger("moxi.turns.kept")

#: The folder of kept ends under turns' state folder.
KEPT_DIR = "kept"
#: A kept end older than this, in seconds, is one the doctor lists.
OLD_S = 24 * 3600.0


class RefKept(ValueError):
    """A `keep` under a `ref` that still holds a kept end of the session, not acked yet.
    Nothing is recorded for it: each kept delivery needs a `ref` of its own."""


def kept_dir(env: Mapping[str, str] | None = None) -> Path:
    """The folder of kept ends."""
    return Layout(turns_config.MODULE).state_dir(env) / KEPT_DIR


def _file_name(endpoint: str, ref: str) -> str:
    digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:32]
    return f"{endpoint}-{digest}.json"


def write(record: dict[str, Any], env: Mapping[str, str] | None = None) -> Path:
    """Write one kept end (`record`, as `entry` makes it) in one step. Raises `OSError`."""
    path = kept_dir(env) / _file_name(record["endpoint"], record["ref"])
    files.atomic_write(path, json.dumps(record, sort_keys=True) + "\n")
    return path


def entry(*, endpoint: str, session_ids: Iterable[str], delivery: str, ref: str,
          kind: str, by: str | None, started: Any, ended: Any) -> dict[str, Any]:
    """A kept end's record: `started` and `ended` are the delivery's values on the wire."""
    return {"endpoint": endpoint, "session_ids": [s for s in session_ids if s],
            "delivery": delivery, "ref": ref, "kind": kind, "by": by,
            "settled_at": time.time(), "started": started, "ended": ended}


def records(env: Mapping[str, str] | None = None) -> list[tuple[Path, dict[str, Any]]]:
    """Every kept end that reads, oldest first. A file that does not read as one is
    logged and left alone."""
    out = []
    ends_dir = kept_dir(env)
    try:
        paths = sorted(ends_dir.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.info("the kept end %s does not read: %s", path, exc)
            continue
        if isinstance(record, dict) and isinstance(record.get("ref"), str):
            out.append((path, record))
    out.sort(key=lambda item: _number(item[1].get("settled_at")))
    return out


def _number(value: Any) -> float:
    return value if isinstance(value, (int, float)) else 0.0


def _of(record: dict[str, Any], session_ids: Iterable[str], endpoint: str | None) -> bool:
    """True when `record` is a kept end of the session named by `session_ids` or by
    `endpoint`."""
    if endpoint is not None and record.get("endpoint") == endpoint:
        return True
    ids = record.get("session_ids") or ()
    return any(s in ids for s in session_ids if s)


def find(ref: str, *, session_ids: Iterable[str] = (), endpoint: str | None = None,
         env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """The newest kept end of that session carrying `ref`, or None."""
    session_ids = tuple(session_ids)
    found = [r for _, r in records(env)
             if r["ref"] == ref and _of(r, session_ids, endpoint)]
    return found[-1] if found else None


def remove(ref: str, *, session_ids: Iterable[str] = (), endpoint: str | None = None,
           env: Mapping[str, str] | None = None) -> bool:
    """Remove every kept end of that session carrying `ref`. True when one was removed."""
    session_ids = tuple(session_ids)
    removed = False
    for path, record in records(env):
        if record["ref"] == ref and _of(record, session_ids, endpoint):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)
                removed = True
    return removed


__all__ = ["KEPT_DIR", "OLD_S", "RefKept", "kept_dir", "write", "entry", "records", "find", "remove"]
