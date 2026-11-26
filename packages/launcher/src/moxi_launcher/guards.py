"""The launcher's guard for turns: the wake guard (`moxi_launcher.wake`).

turns reads `GUARDS` from the `moxi.turns.guards` entry `launcher`, so the guard's id is
`launcher.wake`. It covers Claude Code's `Bash` and `PowerShell` and Codex's `Bash`, main
loop and subagents, and turns' pre-filter passes only a command holding a kill-family
word. It runs where cache loads and the launcher's setting `wake_guard.enabled` isn't false
(`wake.on_for`), or where turns' `guards.run` names `launcher.wake`. A break lets the call
run.

turns is reached through `moxi.modules.load`, since the launcher installs it only as an
extra; turns' loader is the only importer of this module, so turns is there when it runs.
"""

from __future__ import annotations

from moxi import modules

from .wake import CALLS, DEADLINE_MS, MATCH, NAME

_turns = modules.load("turns")
if not _turns.ok or _turns.api is None:
    raise ImportError(f"the launcher's guards need turns, which is {_turns.state}")

GUARDS = (
    _turns.api.Guard(
        name=NAME, calls=CALLS, who="any", match=MATCH, match_flags="i",
        judge="moxi_launcher.wake:judge", on_error="allow", deadline_ms=DEADLINE_MS,
        sessions="moxi_launcher.wake:on_for",
        doc="Refuse a shell call that would end processes under another live session "
            "whose cache is cold, since ending them would give it a turn on a cold cache."),
)

__all__ = ["GUARDS"]
