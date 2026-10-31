"""The launcher's subscriptions to cache's timer, read through the entry point group
`moxi.cache.timers`.

Standard library only: cache's watcher imports this module and reads `TIMERS`, and nothing
else of the launcher is loaded in its process.

**The timer decides only when to ask; the launcher acts only on its own reads.** Each
subscription starts `moxi-launcher-job`, the launcher's own console script, which reads the
launch log, the session through turns and its cache through cache before it does anything.
An ask early, late, twice or not at all can waste a run or miss one, and never causes an
act.

- `launcher.recycle-on-expiry`: from the session's cache expiry on, every 120 s, for a
  session whose launch set `recycle_on_expiry` (the stamp `MOXI_LAUNCHER_RECYCLE_ON_EXPIRY`
  in its environment), `moxi-launcher-job expiry <session>` clears or compacts it once it
  is idle.
- `launcher.keep-warm`: from 900 s before the session's cache expires until 360 s before,
  every 120 s, for a session whose launch set `keep_warm` on (the stamp
  `MOXI_LAUNCHER_KEEP_WARM=on`), `moxi-launcher-job keep-warm <session>` nudges it when it
  waits on work it handed out.
"""

from __future__ import annotations

#: The job each subscription starts, by its bare console script name.
JOB = "moxi-launcher-job"

TIMERS = (
    {"name": "launcher.recycle-on-expiry", "at": 0, "every": 120,
     "requires_env": {"MOXI_LAUNCHER_RECYCLE_ON_EXPIRY": "*"},
     "run": [JOB, "expiry", "{session}"]},
    {"name": "launcher.keep-warm", "at": -900, "until": -360, "every": 120,
     "requires_env": {"MOXI_LAUNCHER_KEEP_WARM": "on"},
     "run": [JOB, "keep-warm", "{session}"]},
)

__all__ = ["JOB", "TIMERS"]
