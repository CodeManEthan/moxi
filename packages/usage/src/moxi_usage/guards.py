"""usage's guard for turns: the worker ceiling (`moxi_usage.ceiling`).

turns reads `GUARDS` from the `moxi.turns.guards` entry `usage`, so the guard's id is
`usage.ceiling`. It covers every tool call of a subagent on Claude and Codex (OpenCode
has no point before a tool call that turns can reach), and runs only when turned on: by
usage's switch `ceiling.enabled`, which the install asks about, or by naming `usage.ceiling`
on turns' `guards.run`. A break lets the call run, as the judge itself does on any error.

turns is reached through `moxi.modules.load`, since usage installs it only as an extra;
turns' loader is the only importer of this module, so turns is there when it runs.
"""

from __future__ import annotations

from moxi import modules

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
)

__all__ = ["GUARDS"]
