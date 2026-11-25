"""The pieces of Moxi's line a user's own status line can call: one command per installed
module with a segment, cache's timer first.

Each command runs statusline's own render for that one item, fed the input the user's
script was given:

    printf '%s' "$input" | /path/to/moxi-statusline-render --only cache

so every command printed exists whenever statusline is installed, and stops working when
it is uninstalled. Each piece starts Python once per render of the user's line.

Every cost printed is a measurement: `measure` runs each printed command as Claude Code
starts a line, through the shell `shell.find` names with the sample input on stdin (and
in `$input`), once to warm up and then 5 times, and gives the median. cache's figure is
for a session with no clock record, since the sample's session has none.

The commands are bash. On Windows with no Git Bash, Claude Code runs a status line
through PowerShell, where they don't run: nothing is measured then, and the lines say
the commands need Git Bash.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import time
from collections.abc import Mapping, Sequence

from . import shell

#: Pieces named for what they show; any other is named by its module.
LABELS = {"cache": "cache timer", "usage": "usage figures"}
#: Pieces that come first, in this order; the rest follow by name.
FIRST = ("cache", "usage")
#: How many timed runs each measurement takes, after one to warm up.
RUNS = 5
#: How long one timed run may take, in seconds.
TIMEOUT = 5.0
#: What the figures say for cache, whose sample session has no clock record.
CACHE_NOTE = "a session with no clock record"
LEAD = ("Call these from your own status line script, where $input holds the JSON Claude "
        "Code gave it on stdin:")
COST = "Each piece starts Python once per render."
NEEDS = "These need statusline installed; uninstalling it stops them."
NONE = "No installed module adds a piece to the status line."
NEEDS_BASH = ("These commands are bash, and Claude Code runs your status line through "
              "PowerShell here, since it found no Git Bash. Install Git for Windows, or set "
              f"{shell.GIT_BASH_PATH_ENV} to its bash.exe, for them to run.")


class Piece:
    """One piece: its `module`, its `label` and the `command` that prints it."""

    __slots__ = ("module", "label", "command")

    def __init__(self, module: str, label: str, command: str):
        self.module = module
        self.label = label
        self.command = command

    def __repr__(self) -> str:
        return f"Piece({self.module!r}, {self.command!r})"


def label(module: str) -> str:
    """What piece `module` is called."""
    return LABELS.get(module, module)


def ordered(modules: Sequence[str]) -> list[str]:
    """`modules`, cache first, then usage, then the rest by name."""
    first = [m for m in FIRST if m in modules]
    return first + sorted(m for m in set(modules) if m not in FIRST)


def pieces(segments: Sequence[str] | None = None, *, render: str | None = None) -> list[Piece]:
    """The pieces of the installed modules with a segment (`segments`, found when None).
    `render` is the render's command (this environment's when None; raises
    `FileNotFoundError` when it isn't installed)."""
    if segments is None:
        from .compile import present_segments

        segments = present_segments()
    if render is None:
        from .switch import render_command

        render = render_command()
    return [Piece(module, label(module),
                  f"printf '%s' \"$input\" | {render} --only {module}")
            for module in ordered(segments)]


def measure(piece: Piece, *, payload: Mapping | None = None, runs: int = RUNS,
            timeout: float = TIMEOUT, env: Mapping[str, str] | None = None) -> float | None:
    """The median wall time, in ms, of `piece`'s command run as Claude Code runs a line
    (`shell.find`) with the sample input on stdin and in `$input`, after one run to warm
    up. None when a run fails or doesn't finish in `timeout` seconds, or when the shell
    is PowerShell, which doesn't run the command."""
    if payload is None:
        from .sample import payload as sample

        payload = sample()
    data = json.dumps(payload)
    run_env = dict(os.environ if env is None else env)
    run_shell = shell.find(run_env)
    if run_shell.kind == "powershell":
        return None
    run_env["input"] = data
    times = []
    for i in range(runs + 1):
        start = time.perf_counter()
        try:
            done = subprocess.run(run_shell.argv(piece.command), input=data.encode("utf-8"),
                                  capture_output=True, env=run_env, timeout=timeout,
                                  check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        took = (time.perf_counter() - start) * 1000
        if done.returncode != 0:
            return None
        if i:
            times.append(took)
    return statistics.median(times)


def lines(found: Sequence[Piece], times: Mapping[str, float | None] | None = None, *,
          env: Mapping[str, str] | None = None) -> list[str]:
    """What the keep path and `moxi statusline pieces` print: the lead, one command per
    piece, then, with `times`, what each measured, and what they need. Where Claude Code
    runs a line through PowerShell under `env` (None: this process's), `NEEDS_BASH` takes
    the place of the figures."""
    if not found:
        return [NONE]
    width = max(len(p.label) for p in found) + 2
    out = [LEAD]
    out += [f"  {(p.label + ':').ljust(width)}{p.command}" for p in found]
    if shell.find(env).kind == "powershell":
        out.append(NEEDS_BASH)
    elif times is not None:
        figures = []
        for p in found:
            ms = times.get(p.module)
            if ms is None:
                figures.append(f"{p.module} not measured (its run failed or ran out of "
                               "time)")
                continue
            note = f" ({CACHE_NOTE})" if p.module == "cache" else ""
            figures.append(f"{p.module} {ms:.0f} ms{note}")
        out.append(f"{COST} Measured just now: {', '.join(figures)}.")
    out.append(NEEDS)
    return out


def measured(found: Sequence[Piece], *, env: Mapping[str, str] | None = None
             ) -> dict[str, float | None]:
    """Each piece's measured median, by module."""
    return {p.module: measure(p, env=env) for p in found}


__all__ = ["Piece", "pieces", "measure", "measured", "lines", "label", "ordered", "LABELS",
           "LEAD", "COST", "NEEDS", "NEEDS_BASH", "NONE", "CACHE_NOTE"]
