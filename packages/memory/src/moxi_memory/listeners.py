"""memory's registration with turns for session events (the `moxi.turns.listeners` group).

Standard library only, and it imports nothing else of memory: turns' endpoint reads this
module when it starts, and imports each function from `moxi_memory.listener` in its own
thread at the function's first ask.

Four rows, in this order:

- `start` answers each session start, resume, clear and compaction with the start line,
  which says the search exists and names this session's project.
- `lists` answers the same events with the two lists of recent sessions. turns joins a
  module's start answers in the order its rows are declared, so the lists follow the
  line.
- `prompt` answers each main-loop prompt with the short reminder, or nothing when config
  `reminder` is off.
- `fire` starts the ingest child, detached, when a main-loop turn ends and when the
  session's endpoint is ending.

memory is on turns' default start list, so installing it sets nothing.
"""

LISTENERS = (
    (("start",), "moxi_memory.listener:start"),
    (("start",), "moxi_memory.listener:lists"),
    (("prompt",), "moxi_memory.listener:prompt"),
    (("turn", "gone"), "moxi_memory.listener:fire"),
)

__all__ = ["LISTENERS"]
