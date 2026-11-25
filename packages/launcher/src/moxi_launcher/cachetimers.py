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
  session whose launch set `recycle_on_expiry = clear` (the stamp
  `MOXI_LAUNCHER_RECYCLE_ON_EXPIRY` in its environment), `moxi-launcher-job expiry
  <session>` clears it once it is idle.
- `launcher.compact-before-expiry`: from 360 s before the session's cache expires until 60 s
  before, every 60 s, for a session whose launch set `recycle_on_expiry = compact`, the
  same job compacts it once in an idle stretch while it is idle and its cache still warm.
  The numbers are the launcher's compact window (`recycle.COMPACT_OPEN_S`, which is
  keep-warm's `SHUT_S`, and `recycle.COMPACT_SHUT_S`), written here as literals.

The ask branches on the launch's own `recycle_on_expiry`, never on which subscription
asked, and each subscription requires its own value of the stamp.
- `launcher.keep-warm` (`KEEP_WARM_ENV`): from 900 s before the session's cache expires until
  360 s before, every 120 s, for every session the launcher started (the stamp
  `MOXI_LAUNCHER_REQUEST`), `moxi-launcher-job keep-warm <session>` nudges it when
  keep-warm is on for its launch and it waits on work it handed out. An ask about a launch
  where keep-warm is off ends at its read of the launch log.
"""

from __future__ import annotations

#: The job each subscription starts, by its bare console script name.
JOB = "moxi-launcher-job"
#: The keep-warm subscription's name.
KEEP_WARM = "launcher.keep-warm"
#: The compact-before-expiry subscription's name.
COMPACT_BEFORE_EXPIRY = "launcher.compact-before-expiry"

TIMERS = (
    {"name": "launcher.recycle-on-expiry", "at": 0, "every": 120,
     "requires_env": {"MOXI_LAUNCHER_RECYCLE_ON_EXPIRY": "clear"},
     "run": [JOB, "expiry", "{session}"]},
    {"name": COMPACT_BEFORE_EXPIRY, "at": -360, "until": -60, "every": 60,
     "requires_env": {"MOXI_LAUNCHER_RECYCLE_ON_EXPIRY": "compact"},
     "run": [JOB, "expiry", "{session}"]},
    {"name": KEEP_WARM, "at": -900, "until": -360, "every": 120,
     "requires_env": {"MOXI_LAUNCHER_REQUEST": "*"},
     "run": [JOB, "keep-warm", "{session}"]},
)

__all__ = ["COMPACT_BEFORE_EXPIRY", "JOB", "KEEP_WARM", "TIMERS"]
