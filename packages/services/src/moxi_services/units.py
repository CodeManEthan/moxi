"""Written rows as run-time declarations: the one way a user's unit follows from the store.

services' announcement names `UNITS_FROM = "moxi_services.units:declared"`. The core
imports and calls `declared()` whenever it reads services' declarations (`plan`, the home
move, the settle step after an install or upgrade), and nothing else writes a user's
unit. So a written row's unit changes only by changing the row and planning again.

`declared()` returns one entry per written row that is not being removed, under
`daemons` or `timers` by the row's kind:

    argv         the row's command (never `command`)
    cwd          the row's working directory, when it has one
    every, or at and days
                 a timer's schedule
    enabled      false while the row's flag is off; left out while it is on
    platforms    [the system the row was added on], so a row carried to another system
                 is skipped there
    stop         "group" (daemons): stopping a service ends everything it started
    upgrade      "keep" (daemons): upgrading services never restarts a user's daemon
    doc          the row's doc, when it has one

No entry has a `switch`: a written timer's gate runs without one, and its on and off are
the supervisor's own. The health command, project, `launches_session` and a recorded stop
stay in the store.

It reads without creating the store. A missing store file is the one failure-free way to
read no rows at all; any read failure raises (busy past the wait, a store too new, one
that can't be read or is not set up yet). The core then leaves every written unit as it
is, so a read that failed never removes a unit. An empty answer always means no written
row is declared.
"""

from __future__ import annotations

from . import store
from .rows import UserRow


def entry(row: UserRow) -> dict[str, object]:
    """The run-time declaration of written row `row`."""
    out: dict[str, object] = {"argv": list(row.argv)}
    if row.cwd is not None:
        out["cwd"] = row.cwd
    if row.kind == "timer":
        if row.every is not None:
            out["every"] = row.every
        else:
            out["at"] = list(row.at)
            if row.days is not None:
                out["days"] = list(row.days)
    if not row.enabled:
        out["enabled"] = False
    out["platforms"] = [row.platform]
    if row.kind == "daemon":
        out["stop"] = "group"
        out["upgrade"] = "keep"
    if row.doc:
        out["doc"] = row.doc
    return out


def declared() -> dict[str, dict[str, dict[str, object]]]:
    """services' run-time declarations: `{"daemons": {...}, "timers": {...}}`, each key
    present only when it has entries, so `{}` when there are none. Raises when the
    store can't be read (see the module's docstring)."""
    out: dict[str, dict[str, dict[str, object]]] = {}
    for row in store.read_rows(strict=True):
        if row.form != "written" or row.removing:
            continue
        out.setdefault(f"{row.kind}s", {})[row.name] = entry(row)
    return out


__all__ = ["declared", "entry"]
