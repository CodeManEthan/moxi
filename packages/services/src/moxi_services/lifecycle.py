"""Install and uninstall: the announcement's `SETUP` and `TEARDOWN`.

`setup()` runs on every `moxi install services`, the upgrade path: it migrates an
existing store and makes none (the first add makes it). The install's own plan of
services' units then re-reads the rows, so an upgrade rewrites the written units;
a daemon's new unit takes effect at its next start, since an upgrade never restarts a
user's daemon.

`teardown()` runs before `moxi uninstall services` removes anything. It refuses while a
row services wrote a unit for remains on this system with a supervisor, naming the
`moxi services remove` for each: uninstalling would take the user's services away
without asking. Rows of another system, or with no supervisor here, have no unit here
and don't hold it up. It then removes each adopted Linux timer's row as `remove` does,
so the timer runs as it did before it was adopted, and refuses on one it can't finish.
Every row it names can be removed. Other adopted rows are left: their units were never
services' to change.
"""

from __future__ import annotations

import sys

import moxi.modules
from moxi import units

from . import acts, state, store
from .errors import ServicesError


def setup() -> int:
    """Migrate an existing store to this code's schema. Returns the exit code, and
    prints why on a failure."""
    try:
        store.migrate_existing()
    except ServicesError as exc:
        print(f"services: {exc.reason}", file=sys.stderr)
        if exc.fix:
            print(f"  fix: {exc.fix}", file=sys.stderr)
        return exc.exit_code
    return 0


def teardown() -> None:
    """Accept the uninstall, or raise `moxi.modules.Refused` saying what to do first."""
    try:
        found = store.read_rows()
    except ServicesError as exc:
        raise moxi.modules.Refused(
            f"services' rows can't be read, so whether a service still runs can't be "
            f"told: {exc.reason}" + (f"; {exc.fix}" if exc.fix else "")) from None
    if units.backend() is not None:
        held = [row for row in found if row.form == "written" and row.here]
        if held:
            names = ", ".join(row.name for row in held)
            steps = "; ".join(f"moxi services remove {row.name}" for row in held)
            raise moxi.modules.Refused(
                f"services still runs {len(held)} service{'s' if len(held) != 1 else ''} "
                f"you added ({names}); remove "
                f"{'them' if len(held) != 1 else 'it'} first: {steps}")
    for row in found:
        if not state.is_wrapped(row):
            continue
        outcome = acts.remove(row.name)
        for line in outcome.lines:
            print(line)
        if not outcome.ok:
            raise moxi.modules.Refused(
                f"{row.id} could not be removed: "
                + ("; ".join(outcome.lines[-1:]) or "see above")
                + (f"; fix it, then run `{outcome.fix}` and uninstall again"
                   if outcome.fix else ""))


__all__ = ["setup", "teardown"]
