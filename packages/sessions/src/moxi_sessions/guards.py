"""sessions' guard for turns: the transcript-read guard (`moxi_sessions.readguard`).

turns reads `GUARDS` from the `moxi.turns.guards` entry `sessions`, so the guard's id is
`sessions.transcript-read`. It covers Claude Code's Read, Grep, Glob and Bash and Codex's
shell calls, the main loop's and every subagent's, and runs only when turned on: by
sessions' switch `guard.transcript_read`, which the install asks about, or by naming
`sessions.transcript-read` on turns' `guards.run`. A break lets the call run, as the judge
itself does on any error.

turns is reached through `moxi.modules.load`, since sessions installs it only as an extra;
turns' loader is the only importer of this module, so turns is there when it runs.
"""

from __future__ import annotations

from moxi import modules

from .readguard import CALLS, NAME, SWITCH

_turns = modules.load("turns")
if not _turns.ok or _turns.api is None:
    raise ImportError(f"sessions' guards need turns, which is {_turns.state}")

GUARDS = (
    _turns.api.Guard(
        name=NAME, calls=CALLS, who="any", judge="moxi_sessions.readguard:judge",
        on_error="allow", switch=SWITCH,
        doc="Refuse an agent's Read, Grep, Glob or shell read of a harness transcript, "
            "and point it to moxi sessions read."),
)

__all__ = ["GUARDS"]
