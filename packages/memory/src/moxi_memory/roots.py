"""Each folder of each harness as memory's last run found it, kept in the index's `meta`.

A harness's folders are its live root and the archive folders sessions' config names for
it, read through `moxi_sessions.api.corpus`. A run (one walk or one rebuild, in one ingest
child) records, for each folder it lists, one `Root` under the meta key
`folders.<harness>`, a JSON list in the order sessions lists them.

**A folder answered a run only when the run listed it, every listing and every read of it
in the run succeeded, and it still answered when tested again just before the run's
decision.** What a folder that didn't answer records is why: `offline` (it didn't answer
the test, or its live root couldn't be listed), `failed` (an error at any depth during the
run, with the first error's path and the count), or `empty` (it lists nothing, though
memory read sources from under it: a disk that isn't mounted can leave its mount point an
empty directory). `since_at` is when it stopped answering.

**The last walk.** A walk lists a folder from its `last_walk_at`, or whole when it has
never been listed whole. A folder that answered moves its last walk to the walk's start.
One that failed during the run moves it too, and keeps the paths that failed in `retry`,
which the next walk reads again by their session: one lasting bad file doesn't make every
walk list the folder from an old time. The folder stays not answered while `retry` holds
anything. A folder that was offline or empty keeps its last walk.

`missed_rebuild_at` is the start of the last rebuild the folder didn't answer: that
rebuild's tier rules, sizes, sets and labels haven't reached the sessions it kept, and only
a rebuild that reads them does. `in_config` is false for a folder a walk found no longer
in sessions' config; its record stays until the next rebuild, which leaves it out.

Nothing here reads a transcript or asks sessions anything.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

#: The meta key of a harness's folder records, before the harness's name.
PREFIX = "folders."
#: Why a folder didn't answer a run.
OFFLINE = "offline"
FAILED = "failed"
EMPTY = "empty"


@dataclass
class Root:
    """One folder's record. Times are seconds since the epoch."""

    path: str
    is_archive: bool = False
    in_config: bool = True
    run_at: float | None = None
    answered: bool = False
    why: str | None = None
    reason: str | None = None
    first_error: tuple[str, str] | None = None
    errors: int = 0
    since_at: float | None = None
    last_walk_at: float | None = None
    listed_whole: bool = False
    retry: tuple[str, ...] = field(default_factory=tuple)
    missed_rebuild_at: float | None = None

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["first_error"] = list(self.first_error) if self.first_error else None
        out["retry"] = list(self.retry)
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Root:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        first = known.get("first_error")
        known["first_error"] = tuple(first) if isinstance(first, list) and len(first) == 2 \
            else None
        known["retry"] = tuple(str(p) for p in known.get("retry") or ())
        return cls(**known)

    def describe(self) -> str:
        """What the folder did at the last run, in words."""
        from .clock import show

        if self.answered:
            return "answered"
        since = f" since {show(self.since_at)}" if self.since_at else ""
        if self.why == FAILED:
            where = (f": first {self.first_error[0]}: {self.first_error[1]}"
                     if self.first_error else "")
            many = "" if self.errors == 1 else f" ({self.errors} errors)"
            return f"failed during the run{since}{many}{where}"
        if self.why == EMPTY:
            return f"{self.reason or 'lists nothing'}{since}"
        return f"offline{since}: {self.reason or 'no answer'}"


def read(conn, harness: str) -> list[Root]:
    """`harness`'s folder records in `conn`'s generation, or none."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?",
                       (PREFIX + harness,)).fetchone()
    if row is None:
        return []
    try:
        data = json.loads(row[0])
    except ValueError:
        return []
    return [Root.from_json(item) for item in data if isinstance(item, dict)]


def read_all(conn) -> dict[str, list[Root]]:
    """Every harness's folder records in `conn`'s generation."""
    out: dict[str, list[Root]] = {}
    for (key,) in conn.execute("SELECT key FROM meta WHERE key LIKE 'folders.%' "
                               "ORDER BY key"):
        harness = key[len(PREFIX):]
        out[harness] = read(conn, harness)
    return out


def write(conn, harness: str, records: list[Root]) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (PREFIX + harness, json.dumps([r.to_json() for r in records])))


def held(conn, harness: str, root: str) -> int:
    """How many sources the index holds that memory read from under `root`: a file below
    it, or an OpenCode session of the database `root`."""
    if conn is None:
        return 0
    return conn.execute(
        "SELECT count(*) FROM sources WHERE harness = ? AND (source = ? "
        "OR substr(source, 1, ?) = ? OR substr(source, 1, ?) = ?)",
        (harness, root, len(root) + 1, root + "/", len(root) + 1, root + "#")).fetchone()[0]


def not_read_yet(records: dict[str, list[Root]]) -> list[str]:
    """The archive folders sessions' config names that no run they answered has listed
    whole: sessions there are not in the index yet."""
    return [r.path for recs in records.values() for r in recs
            if r.is_archive and r.in_config and not r.listed_whole]


def lines(records) -> list[str]:
    """One line for each folder that needs saying: one that didn't answer the last run
    (and why, since when), an archive folder no run has listed whole, a folder the last
    rebuild didn't read, and one walked before that sessions' config no longer names.
    `records` maps a harness to its `roots.Root` list."""
    from .clock import show

    out = []
    for harness, recs in sorted(records.items()):
        for rec in recs:
            where = f"{harness} {'archive folder' if rec.is_archive else 'root'} {rec.path}"
            if not rec.in_config:
                out.append(f"{where}: walked before, and sessions' config no longer "
                             "names it; its sessions leave at the next rebuild unless "
                             "another folder holds them")
                continue
            if rec.run_at is None:
                out.append(f"{where}: no walk has listed it yet")
                continue
            said = [] if rec.answered else [rec.describe()]
            if rec.is_archive and not rec.listed_whole:
                said.append("not listed whole yet: its sessions are not in the index")
            if rec.missed_rebuild_at is not None:
                said.append(f"the rebuild of {show(rec.missed_rebuild_at)} kept its "
                            "harness's sessions as they were; run `moxi memory reindex` "
                            "again once it answers")
            if said:
                out.append(f"{where}: {'; '.join(said)}")
    return out


__all__ = ["PREFIX", "OFFLINE", "FAILED", "EMPTY", "Root", "read", "read_all", "write",
           "held", "not_read_yet", "lines"]
