"""memory's registration with turns for session events (the `moxi.turns.listeners` group).

Standard library only, and it imports nothing else of memory: turns' endpoint reads this
module when it starts, and imports each function from `moxi_memory.listener` in its own
thread at the function's first ask.

Six rows, in this order:

- `start` answers each session start, resume, clear and compaction with the start line,
  which says the search exists and names this session's project.
- `lists` answers the same events with the two lists of recent sessions. turns joins a
  module's start answers in the order its rows are declared, so the lists follow the
  line.
- `prompt` answers each main-loop prompt with the short reminder, or nothing when config
  `reminder.enabled` is off.
- `subconscious` answers each main-loop prompt with nothing: in a session whose launch
  turned the subconscious on, it starts a recall's child, detached, or resets the
  agent's count of recalls, and returns at once.
- `fire` starts the ingest child, detached, when a main-loop turn ends and when the
  session's endpoint is ending.
- `subconscious_gone`, when the endpoint of a session with the subconscious on is
  ending and a recall's line of the session is still open or kept, starts a taker for
  those lines, detached.

memory is on turns' default start list, so installing it sets nothing.
"""

LISTENERS = (
    (("start",), "moxi_memory.listener:start"),
    (("start",), "moxi_memory.listener:lists"),
    (("prompt",), "moxi_memory.listener:prompt"),
    (("prompt",), "moxi_memory.listener:subconscious"),
    (("turn", "gone"), "moxi_memory.listener:fire"),
    (("gone",), "moxi_memory.listener:subconscious_gone"),
)

__all__ = ["LISTENERS"]
