"""rounds' guard for turns: the watched-paths guard (`moxi_rounds.watching`).

turns reads `GUARDS` from the `moxi.turns.guards` entry `rounds`, so the guard's id is
`rounds.watched-paths`. It covers the file-writing tools (Claude Code's Write and Edit,
Codex's apply_patch, OpenCode's write and edit), the main loop's and every subagent's,
and runs only when turned on: by rounds' switch `watched_paths_guard.enabled`, which the
install asks about, or by naming `rounds.watched-paths` on turns' `guards.run`. A
project's `watched_paths` say what it matches once it runs, never whether it runs. It
adds a line and never refuses; a break lets the call run, as the judge itself does on
any error.

turns is reached through `moxi.modules.load`, since rounds installs it only as an extra;
turns' loader is the only importer of this module, so turns is there when it runs.
"""

from __future__ import annotations

from moxi import modules

from .watching import CALLS, NAME, SWITCH

_turns = modules.load("turns")
if not _turns.ok or _turns.api is None:
    raise ImportError(f"rounds' guards need turns, which is {_turns.state}")

GUARDS = (
    _turns.api.Guard(
        name=NAME, calls=CALLS, who="any", judge="moxi_rounds.watching:judge",
        on_error="allow", switch=SWITCH,
        doc="After a write to a path a project's watched_paths names, add one line with "
            "the entry's note and the skills to load; it never refuses."),
)

__all__ = ["GUARDS"]
