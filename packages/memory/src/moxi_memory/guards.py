"""memory's guards for turns: a recall's fork reads and answers, and asks no one
(`moxi_memory.recall_guard`).

turns reads `GUARDS` from the `moxi.turns.guards` entry `memory`, so the ids are
`memory.subconscious-fork` and `memory.subconscious-fork-ask`. Both cover every tool a
subagent of Claude Code calls, and run only in a session whose launch turned the
subconscious on (`recall_guard.guarded`), so a session with it off pays nothing.

- `subconscious-fork`, at the call step, judges a call only when its subagent is a
  recall's fork: reads and plain-read shell calls run, within the recall's time and
  tool calls; everything else is refused. A break lets the call through
  (`on_error = "allow"`), since the guard also sees the main agent's own subagents.
- `subconscious-fork-ask`, at the ask step, refuses every permission ask of a recall's
  fork, so the person never sees a dialog for one. A break refuses the ask
  (`on_error = "deny"`): it may refuse one ask of the main agent's own subagents in
  that session, where a dialog would have shown.

turns' loader is the only importer of this module.
"""

from __future__ import annotations

from moxi_turns import Guard

CALLS = {"claude": ("*",)}
SESSIONS = "moxi_memory.recall_guard:guarded"

GUARDS = (
    Guard(name="subconscious-fork", calls=CALLS, who="agents",
          judge="moxi_memory.recall_guard:judge_call", on_error="allow",
          deadline_ms=2000, sessions=SESSIONS,
          doc="Keep a recall's fork to reads within its time and tool calls; other "
              "subagents' calls are left alone."),
    Guard(name="subconscious-fork-ask", calls=CALLS, who="agents",
          judge="moxi_memory.recall_guard:judge_ask", on_error="deny",
          deadline_ms=2000, sessions=SESSIONS, step="ask",
          doc="Refuse every permission ask of a recall's fork, so no dialog shows for "
              "one; other subagents' asks go to you."),
)

__all__ = ["CALLS", "SESSIONS", "GUARDS"]
