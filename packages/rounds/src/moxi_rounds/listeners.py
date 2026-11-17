"""rounds' registration with turns for session events (the `moxi.turns.listeners` group).

Standard library only, and it imports nothing else of rounds: turns' endpoint reads this
module when it starts, and imports the function from `moxi_rounds.listener` in its own
thread at its first ask.

One row. `ready` (a session's start, resume, clear or compaction) starts the sweep of
leftover folders in a detached process of its own when the machine's settings ask for it,
and returns at once, so a session's start waits on nothing. Without turns nothing calls
it, and the sweep still runs by hand and in the doctor's dry run.
"""

LISTENERS = (
    (("ready",), "moxi_rounds.listener:ready"),
)

__all__ = ["LISTENERS"]
