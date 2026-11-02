"""The harness homes a run resolves, recorded in the ledger and compared by a page job.

A page job runs in the browser shell's environment, not the user's terminal's, and the
variables that place a harness's home (Claude Code's `CLAUDE_CONFIG_DIR`, Codex's
`CODEX_HOME`, OpenCode's `XDG_CONFIG_HOME`, each with its default under `HOME`) may be set
in one and not the other. So the ledger's `homes` holds, for each harness, the home the
user's own last run resolved:

- `SETUP` records it under the core lock it already holds, so every `moxi install loadout`
  and upgrade records it from the terminal;
- every verb the user runs that installs records it (`install`, `add`, `remove`,
  `enable`, `disable`, `global mode`, `global off`);
- a run whose caller sets `MARKER` in its environment is not the user's own and leaves
  it as it is: the git hook's child install and a page job set it.

A page job compares the homes it resolves with the record before anything is written,
and refuses with one line (`check`) when any differs or none is recorded. `status` shows
the same line as a note. The `claude` and `codex` programs are never compared: a page
whose `PATH` finds no CLI does what a terminal without it does.

Standard library only at import.
"""

from __future__ import annotations

import os
import sys

#: The variable a caller sets, to any value, when a run is not the user's own: the run
#: then leaves the recorded homes as they are. Its value names the caller, for the log.
MARKER = "MOXI_LOADOUT_CALLER"
#: The ledger's key.
KEY = "homes"
#: The variable that places each harness's home.
VARIABLES = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME",
             "opencode": "XDG_CONFIG_HOME"}
#: Each harness by the name a person knows it by.
NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
#: The command that records the homes from a terminal.
COMMAND = "moxi loadout install"


def resolved() -> dict[str, str]:
    """Each harness's home as this process's environment places it, absolute."""
    from moxi_loadout import harness

    return {h: os.path.abspath(harness.home(h)) for h in harness.HARNESSES}


def users_own() -> bool:
    """Whether this run is the user's own: no caller set `MARKER`."""
    return not os.environ.get(MARKER)


def record(data: dict) -> bool:
    """Put this run's homes in ledger data `data`, unless the run isn't the user's own.
    Returns whether it did. The caller writes the ledger."""
    if not users_own():
        return False
    data[KEY] = resolved()
    return True


def recorded(data: dict) -> dict[str, str] | None:
    """The homes ledger data `data` holds, or None when it holds none for some harness
    (a lost ledger, or one older than this record)."""
    from moxi_loadout import harness

    found = data.get(KEY)
    if not isinstance(found, dict):
        return None
    if not all(isinstance(found.get(h), str) and found.get(h) for h in harness.HARNESSES):
        return None
    return {h: found[h] for h in harness.HARNESSES}


def check(data: dict) -> str | None:
    """The one line a page job refuses with, or None when this process's homes are the
    ones recorded in ledger data `data`."""
    then = recorded(data)
    if then is None:
        return (f"loadout has no record of the harness homes your terminal uses (its "
                f"ledger is new or was lost), so it can't tell where this would write; run "
                f"{COMMAND} once in a terminal, then try again")
    now = resolved()
    differ = [h for h in now if os.path.normcase(now[h]) != os.path.normcase(then[h])]
    if not differ:
        return None
    parts = [f"{NAMES[h]}'s home here is {now[h]}, but the last run in a terminal used "
             f"{then[h]}" for h in differ]
    names = ", ".join(dict.fromkeys(VARIABLES[h] for h in differ))
    return ("; ".join(parts) + f". Set {names} " + lasting_fix()
            + ", or run the command in a terminal")


def lasting_fix() -> str:
    """Where a variable is set so the browser shell sees it at every login, and what
    then restarts the page server: the end of a line that starts "Set NAME "."""
    if sys.platform == "darwin":
        where = "in your LaunchAgent environment"
    elif sys.platform == "win32":
        where = "in your user environment variables"
    else:
        where = "in a file under ~/.config/environment.d/"
    return f"{where}, so the browser shell has it at every login, then run moxi ui stop"


def path_fix(program: str) -> str:
    """The lasting fix for a `program` that `PATH` doesn't find."""
    return (f"put {program}'s folder on PATH, and for the browser page set PATH "
            + lasting_fix())


__all__ = ["MARKER", "KEY", "VARIABLES", "NAMES", "COMMAND", "resolved", "users_own",
           "record", "recorded", "check", "lasting_fix", "path_fix"]
