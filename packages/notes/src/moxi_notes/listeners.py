"""notes' registration with turns for session events (the `moxi.turns.listeners` group).

Standard library only, and it imports nothing else of notes: turns' endpoint reads this
module when it starts, and imports each function from `moxi_notes.listener` in its own
thread at the function's first ask.

Two rows. `start` answers each session start, resume, clear and compaction with the one
line "Notes directory: <path>". `fire` starts a snapshot pass of the notes repo, detached,
when a main-loop turn ends and when the session's endpoint is ending. notes is on turns'
default start list, so installing it sets nothing.
"""

LISTENERS = (
    (("start",), "moxi_notes.listener:start"),
    (("turn", "gone"), "moxi_notes.listener:fire"),
)

__all__ = ["LISTENERS"]
