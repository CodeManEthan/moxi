"""cache's rows in `moxi doctor`.

One row, and only where it applies: on a system where a process's environment can't be
read (Windows), a subscription with `requires_env` can't be checked, so the timer never
asks for it. The row names those subscriptions. Everywhere else, and with no such
subscription, cache adds no row here; `moxi cache doctor` keeps its own full report.
"""

from __future__ import annotations

from collections.abc import Iterator

from moxi.doctor import Row

from . import timer
from .settings import MODULE


def rows() -> Iterator[Row]:
    """cache's doctor rows: the `requires_env` warning where it can't be checked."""
    if timer.env_checkable():
        return
    from .cli import _config_entries

    entries, _ = _config_entries()
    names = [s.name for s in timer.load(entries).subscriptions if s.requires_env]
    if names:
        yield Row(MODULE, "timer requires_env", "warn",
                  "a process's environment can't be read on this system, so requires_env "
                  f"can't be checked and these subscriptions never ask: {', '.join(names)}")


__all__ = ["rows"]
