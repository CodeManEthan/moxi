"""The record of the last guard table: what the Claude runner refuses before a table loads.

Until the endpoint hands the runner a table, the runner can't ask any guard, so it refuses
the calls one of three guards could refuse, but only for the guards the last table held.
Each time the endpoint hands the runner a table (in its first line and in each `guards`
line) it writes this record, `STATE/turns/guards-seen.json`:

    {"at": <seconds since the epoch>, "removal": bool, "no_nested_agents": bool,
     "ask_step": bool}

- `removal`: the removal guard (`turns.removal`) is in the table;
- `no_nested_agents`: the no-nested-agents guard (`turns.no-nested-agents`) is in it;
- `ask_step`: some entry runs at the ask step. turns ships no guard for a permission
  prompt in a bypass session; a user's own ask-step guard is what refuses one, so an
  ask-step entry stands for it.

An empty table writes all three false. The record holds no entry's text. It is written
whole through a temp file beside it, so a reader sees the old record or the new one; when
sessions write it at once the last write wins, and they read one machine's config, so
they agree.

The runner finds the record through `MOXI_TURNS_GUARDS_SEEN`, which a launch names
(`launch_additions`), else through `guards_seen` in `endpoint.json`, which turns' setup
writes. The runner never writes it.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

log = logging.getLogger("moxi.turns.guards")

#: The variable a launch names the record's path in, for the runner.
GUARDS_SEEN_ENV = "MOXI_TURNS_GUARDS_SEEN"
#: The store's name in `STORES`, and its file under turns' state directory.
STORE = "guards-seen"
FILE = "guards-seen.json"
#: The record's three facts, in order.
FACTS = ("removal", "no_nested_agents", "ask_step")
#: The table ids of the two shipped guards the record names.
REMOVAL_ID = "turns.removal"
NO_NESTED_AGENTS_ID = "turns.no-nested-agents"


def record_path(env: Mapping[str, str] | None = None) -> Path:
    """`STATE/turns/guards-seen.json`, from `moxi.paths` (`env`, default the process's)."""
    from moxi.paths import Layout

    from ..config import MODULE

    return Layout(MODULE).state_dir(env) / FILE


def facts_of(table: Iterable[Mapping[str, Any]]) -> dict[str, bool]:
    """The three facts a table in the leg's form gives."""
    entries = list(table)
    ids = {e.get("id") for e in entries}
    return {"removal": REMOVAL_ID in ids,
            "no_nested_agents": NO_NESTED_AGENTS_ID in ids,
            "ask_step": any(e.get("step") == "ask" for e in entries)}


def write_record(table: Iterable[Mapping[str, Any]], *, path: Path | None = None,
                 now: float | None = None) -> Path:
    """Write the record of `table` (the leg's form) whole, at `path` (default
    `record_path()`). Returns the path; raises `OSError` when it can't be written."""
    from moxi import files

    path = record_path() if path is None else Path(path)
    record = {"at": time.time() if now is None else now, **facts_of(table)}
    files.atomic_write(path, json.dumps(record) + "\n", mode=0o644)
    return path


def read_record(path: Path | None = None) -> dict[str, Any] | None:
    """The record at `path` (default `record_path()`), or None when it is missing, not
    JSON, or lacks one of the three facts as a bool."""
    path = record_path() if path is None else Path(path)
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not all(isinstance(data.get(k), bool) for k in FACTS):
        return None
    return data


__all__ = ["GUARDS_SEEN_ENV", "STORE", "FILE", "FACTS", "REMOVAL_ID", "NO_NESTED_AGENTS_ID",
           "record_path", "facts_of", "write_record", "read_record"]
