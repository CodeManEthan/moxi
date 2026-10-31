"""services' doctor checks: `rows()`, which the announcement's `DOCTOR` names.

The core's own doctor already reports every module's daemons and timers, and a written
row's unit as the core sees it (declared against installed, a refusal, an editable
install, a wrapped timer's last run line), each with its fix. These rows add only what
services knows, and repeat none of those:

- store: `fail` when services' store can't be read or is newer than this code; `warn`
  while it stays locked. A missing store is `ok`: the first add makes it.
- `services/<name>`, one per user row: the row's verdict, with its reason and fix.
- `project:<key>`, for a project key rows carry: `off` while projects is not installed
  (the rows keep their keys), `warn` for a key projects does not have.

They finish inside the doctor's limit whatever the row count: the one supervisor read
gets `STATUS_SECONDS` in a worker thread, past which every row is `warn`, "unknown: the
supervisor did not answer", and no health check runs; otherwise every running daemon's
health check starts at once, each under its own limit.
"""

from __future__ import annotations

from collections.abc import Iterator

from moxi.doctor import Row

from . import projects_link, state, store
from .errors import Busy, ServicesError, TooNew
from .rows import MODULE
from .verdict import views

#: Seconds the doctor waits for the supervisor read.
STATUS_SECONDS = 3.0


def rows() -> Iterator[Row]:
    """Every check's row."""
    try:
        found = store.read_rows()
    except TooNew as exc:
        yield Row(MODULE, "store", "fail", exc.reason, exc.fix)
        return
    except Busy as exc:
        yield Row(MODULE, "store", "warn", exc.reason, exc.fix)
        return
    except ServicesError as exc:
        yield Row(MODULE, "store", "fail", exc.reason, exc.fix)
        return
    count = len(found)
    yield Row(MODULE, "store", "ok",
              f"{count} row{'s' if count != 1 else ''} at {store.path()}" if count
              else "no rows yet: the first `moxi services add` makes them")
    if not found:
        return
    for view in views(found, health=True, status_seconds=STATUS_SECONDS,
                      health_seconds=state.HEALTH_SECONDS):
        verdict = view.verdict
        yield Row(MODULE, view.row.id, verdict.state, verdict.reason, verdict.fix)
    yield from _projects(found)


def _projects(found) -> Iterator[Row]:
    keys: dict[str, list[str]] = {}
    for row in found:
        if row.project is not None:
            keys.setdefault(row.project, []).append(row.name)
    if not keys:
        return
    try:
        known = projects_link.known_keys()
    except ServicesError as exc:
        yield Row(MODULE, "project keys", "warn",
                  f"projects could not be read, so the rows' project keys were not "
                  f"checked: {exc.reason}", exc.fix)
        return
    for key in sorted(keys, key=lambda k: (k.casefold(), k)):
        names = ", ".join(keys[key])
        if known is None:
            yield Row(MODULE, f"project:{key}", "off",
                      f"projects is not installed; {names} keep the key")
        elif key.casefold() not in known:
            yield Row(MODULE, f"project:{key}", "warn",
                      f"projects has no project {key}, which {names} name",
                      f"add it with `moxi projects add {key} --root DIR`, or remove "
                      f"those rows and add them again without --project")


__all__ = ["STATUS_SECONDS", "rows"]
