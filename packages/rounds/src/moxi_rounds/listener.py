"""The sweep at a session's start: what turns calls on `ready`.

turns imports this module in a listener thread of the session's endpoint, at the first
ask. It starts the sweep of leftover folders (`sweeping`) only when `sweep.at_start` and
`sweep.armed` are both true and the last removing run started more than
`EVERY_S` ago, as `sweep.started` says; otherwise it returns. The sweep runs in a
detached process of its own, `moxi rounds drop --leftovers --remove`, with this
process's environment less every `GIT_` variable, and the listener returns without
waiting for it. Two sessions that start together may both start one: the sweep's own
lock lets one run, and the other stops.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

#: The least time between the starts of two sweeps a session's start begins.
EVERY_S = 600
#: What the detached process runs, after the path of the `moxi` command.
ARGV = ("rounds", "drop", "--leftovers", "--remove")


def ready(event) -> None:
    """Start the sweep, detached, when the settings ask for it and the last removing
    run started more than `EVERY_S` ago."""
    from moxi import config

    from . import settings as own
    from . import sweeping

    try:
        settings = own.load()
    except config.ConfigError:
        # The doctor's settings row says what is wrong; a session's start goes on.
        return None
    if not (settings["sweep.at_start"] and settings["sweep.armed"]):
        return None
    if not due(sweeping.started_file()):
        return None
    _spawn()
    return None


def due(started) -> bool:
    """Whether the run whose start time the file `started` holds began more than
    `EVERY_S` ago. A file that is missing or holds no time it can read is due: the
    sweep's lock still keeps two runs apart."""
    try:
        text = started.read_text(encoding="utf-8").strip()
        at = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (OSError, ValueError):
        return True
    age_s = time.time() - at.timestamp()
    return not 0 <= age_s < EVERY_S


def _spawn() -> int:
    import moxi.paths
    import moxi.proc

    env = {name: value for name, value in os.environ.items()
           if not name.startswith("GIT_")}
    return moxi.proc.spawn([str(moxi.paths.script("moxi")), *ARGV], env=env,
                           detached=True, output=os.devnull)


__all__ = ["EVERY_S", "ARGV", "ready", "due"]
