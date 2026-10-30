"""The launcher's Python API: what the CLI calls, and what any other caller may.

`preview(request)` resolves one launch (`presets.resolve`) and composes it, and starts
nothing. It composes
through `compose.build`, the same call the launch makes, with the request's ids, so for one
request it shows the argv that runs. Two limits: composing a Claude launch without comms
asks turns for its launch additions, which writes turns' own endpoint file, so `preview` is
not free of side effects outside the launcher; and for a Codex launch under turns' host it
shows the host's command, not the `codex` command the host builds.
"""

from __future__ import annotations

from collections.abc import Mapping

from . import compose, presets
from .compose import Composition


def preview(request, *, environ: Mapping[str, str] | None = None) -> Composition:
    """The composition `request` would launch: argv, the stamped environment, target and
    mode. Starts nothing and writes nothing of the launcher's."""
    return compose.build(request, _resolve(request, environ), environ=environ)


def _resolve(request, environ: Mapping[str, str] | None = None):
    return presets.resolve(request, env=environ)


__all__ = ["preview"]
