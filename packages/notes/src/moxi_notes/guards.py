"""notes' guard for turns: the home-paths guard (`moxi_notes.homeguard`).

turns reads `GUARDS` from the `moxi.turns.guards` entry `notes`, so the guard's id is
`notes.home-paths`. It covers the file-writing tools (Claude Code's Write and Edit, Codex's
apply_patch, OpenCode's write and edit), the main loop's and every subagent's, and runs
only when turned on: by notes' switch `guard.home_paths`, which setup asks about, or by
naming `notes.home-paths` on turns' `guards.run`. It tells and never refuses; a break lets
the call run, as the judge itself does on any error.

turns' loader is the only importer of this module.
"""

from __future__ import annotations

from moxi_turns import Guard

from .homeguard import CALLS, NAME, SWITCH

GUARDS = (
    Guard(name=NAME, calls=CALLS, who="any", judge="moxi_notes.homeguard:judge",
          on_error="allow", switch=SWITCH,
          doc="Tell an agent whose note under the notes root spells your home folder or "
              "your login to write ~/ and session ids instead; it never refuses."),
)

__all__ = ["GUARDS"]
