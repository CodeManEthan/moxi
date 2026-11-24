"""usage's guards for turns: the worker ceiling (`moxi_usage.ceiling`) and the stop
(`moxi_usage.stop`).

turns reads `GUARDS` from the `moxi.turns.guards` entry `usage`, so the guards' ids are
`usage.ceiling` and `usage.stop`. The ceiling covers every tool call of a subagent on
Claude and Codex (OpenCode has no point before a tool call that turns can reach), and runs
only when turned on: by usage's switch `ceiling.enabled`, which the install asks about, or
by naming `usage.ceiling` on turns' `guards.run`. The stop covers every tool call of the
main loop on Claude, and runs in a session whose launch stamped a stop mark
(`stop.on_for`), or where `guards.run` names `usage.stop`. A break of either lets the call
run, as each judge itself does on any error.

turns is reached through `moxi.modules.load`, since usage installs it only as an extra;
turns' loader is the only importer of this module, so turns is there when it runs.
"""

from __future__ import annotations

from moxi import modules

from . import stop
from .ceiling import NAME, SWITCH

_turns = modules.load("turns")
if not _turns.ok or _turns.api is None:
    raise ImportError(f"usage's guards need turns, which is {_turns.state}")

GUARDS = (
    _turns.api.Guard(
        name=NAME, calls={"claude": ("*",), "codex": ("*",)}, who="agents",
        judge="moxi_usage.ceiling:judge", on_error="allow", switch=SWITCH,
        doc="Warn a subagent whose context has grown past ceiling.warn_tokens, and past "
            "ceiling.hard_tokens refuse its tools except to hand off."),
    _turns.api.Guard(
        name=stop.NAME, calls={"claude": ("*",)}, who="main", judge="moxi_usage.stop:judge",
        on_error="allow", sessions="moxi_usage.stop:on_for",
        doc="Past the session's stop mark, refuse every tool but wrap-up: git's wrap-up "
            "forms, the handoff, comms and the self relaunch."),
)

__all__ = ["GUARDS"]
