"""The launcher's own doctor checks, `rows()`, which the announcement's `DOCTOR` names.

The core already checks the launcher's settings and its declared stores. The launcher adds
one row per harness, `catalog.<harness>`: the installed harness's `--help` against the
shipped flag catalog (`moxi_launcher.catalog.diff`).

* `fail`: a flag a catalog row spells is no longer listed, so a launch that sets the row
  would be refused by the harness. This holds when another `--help` could not be read.
* `warn`: the help lists flags the catalog does not know, or no longer lists one the
  catalog recorded as there; or the help could not be read. Nothing a launch composes
  breaks; the new flags pass on after `--`.
* `off`: the harness is not on PATH.
* `ok`: every flag matches.

With cache installed it adds `keep-warm.timer`: whether cache's watcher asks about the
launched sessions, which `Keep warm` on a running session's row needs. It reads cache's
API (`asking` and `clock_records`) and the launch log, and takes the first of these that
holds:

* `warn`: the installed cache has no `asking`, so it is too old to say what its timer
  asks. A restart cures nothing; cache is upgraded first.
* `off`: a process's environment can't be read on this system, so the timer never asks
  about a launched session. cache's own row carries the warning.
* `warn`: cache refuses the keep-warm subscription; `moxi cache timers` says why.
* `warn`: a fresh clock record has no `asks`, so the running watcher is older than the
  installed cache. The fix is the restart.
* `warn`: a launched session's fresh record has `asks` without the subscription, which a
  watcher started now would ask. The fix is the restart.
* `ok`: none of these.

A record is fresh when its `seen_at` is no older than its own `stale_after`.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from moxi.doctor import Row
from moxi.store import StoreError

from . import cachetimers, catalog, joiner, store

MODULE = "launcher"
#: How many flags a row's detail names before it says how many more.
SHOWN = 8
#: The keep-warm row's name.
KEEP_WARM = "keep-warm.timer"
#: The environment a launched session's process is asked about: the request stamp, which
#: the keep-warm subscription requires to be set to any value.
LAUNCHED = {"MOXI_LAUNCHER_REQUEST": "doctor"}
#: The restart of cache's watcher: by services where it runs the watcher, else by hand.
RESTART = ("moxi services restart cache/watch, or without services stop moxi cache watch "
           "and start it again")


def rows(read: catalog.Reader | None = None) -> Iterator[Row]:
    """Every row the launcher adds to `moxi doctor`. `read` stands in for running the
    harness in tests."""
    for harness in catalog.HARNESSES:
        yield catalog_row(catalog.diff(harness, read=read))
    cache = joiner.cache_api()
    if cache is not None:
        yield keep_warm_row(cache)


def catalog_row(diff: catalog.Diff) -> Row:
    """The doctor row of one harness's `--help` diff."""
    name = f"catalog.{diff.harness}"
    if diff.missing:
        return Row(MODULE, name, "off", f"{diff.harness} is not on PATH")
    # A help that could not be read doesn't hide a flag removed from one that could.
    errors = [h.error for h in diff.helps if h.error]
    removed = [f"{h.command} no longer lists {_flags(h.removed)}"
               for h in diff.helps if h.removed and not h.error]
    if removed:
        return Row(MODULE, name, "fail", "; ".join(removed + errors) + _version(diff),
                   "update moxi-launcher, or install the harness version its catalog "
                   "was checked against")
    if errors:
        return Row(MODULE, name, "warn", "; ".join(errors))
    said = []
    for h in diff.helps:
        if h.added:
            said.append(f"{h.command} lists flags the catalog does not know: "
                        f"{_flags(h.added)}")
        if h.gone:
            said.append(f"{h.command} no longer lists {_flags(h.gone)}")
    if said:
        return Row(MODULE, name, "warn", "; ".join(said) + _version(diff),
                   "nothing to do now: an unknown flag passes on after --")
    helps = " and ".join(h.command for h in diff.helps)
    return Row(MODULE, name, "ok", f"the catalog matches {helps}{_version(diff)}")


def keep_warm_row(cache: Any, *, now: float | None = None) -> Row:
    """The `keep-warm.timer` row, from cache's API `cache` and the launch log."""
    asking = getattr(cache, "asking", None)
    if asking is None:
        return Row(MODULE, KEEP_WARM, "warn",
                   "the installed moxi-cache is older than 0.1.6 and does not say what its "
                   "timer asks, so Keep warm is not offered; upgrade moxi-cache, then "
                   "restart its watcher")
    expected = asking(LAUNCHED)
    if expected is None:
        return Row(MODULE, KEEP_WARM, "off",
                   "a process's environment can't be read on this system, so cache's "
                   "timer never asks about a launched session and Keep warm is not "
                   "offered")
    if cachetimers.KEEP_WARM not in expected:
        return Row(MODULE, KEEP_WARM, "warn",
                   "cache does not take the launcher's keep-warm subscription, so no "
                   "launched session is kept warm; moxi cache timers lists it under "
                   "refused with the reason", "moxi cache timers")
    now = time.time() if now is None else now
    fresh = [record for record in cache.clock_records() if _fresh(record, now)]
    if any("asks" not in record for record in fresh):
        return Row(MODULE, KEEP_WARM, "warn",
                   "cache's watcher was started before cache was upgraded, so Keep warm "
                   "is not offered; restart the watcher", RESTART)
    try:
        launched = _launched(fresh)
    except (StoreError, sqlite3.Error, OSError) as exc:
        return Row(MODULE, KEEP_WARM, "warn",
                   f"the launch log could not be read ({exc}), so whether cache's "
                   "watcher asks about the launched sessions is not known")
    behind = [record for record in launched
              if cachetimers.KEEP_WARM not in (record["asks"] or ())]
    if behind:
        count = len(behind)
        sessions = "session" if count == 1 else "sessions"
        return Row(MODULE, KEEP_WARM, "warn",
                   f"cache's watcher does not ask about {count} launched {sessions} that "
                   "a watcher started now would ask about, so Keep warm is not offered "
                   "for them; restart the watcher", RESTART)
    if launched:
        return Row(MODULE, KEEP_WARM, "ok",
                   "cache's timer asks about every launched session it times")
    return Row(MODULE, KEEP_WARM, "ok",
               "cache takes the keep-warm subscription; no launched session is being timed")


def _fresh(record: Mapping[str, Any], now: float) -> bool:
    seen, stale = record.get("seen_at"), record.get("stale_after")
    return (isinstance(seen, (int, float)) and isinstance(stale, (int, float))
            and now - seen <= stale)


def _launched(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The records of sessions a launch holds; `[]` with no launch log."""
    with store.reading() as conn:
        if conn is None:
            return []
        return [record for record in records
                if store.resolve(conn, str(record.get("session"))) is not None]


def _flags(flags) -> str:
    shown = ", ".join(flags[:SHOWN])
    more = len(flags) - SHOWN
    return shown + (f" and {more} more" if more > 0 else "")


def _version(diff: catalog.Diff) -> str:
    version = next((h.version for h in diff.helps if h.version), None)
    return "" if version is None else f" ({diff.harness} {version})"
