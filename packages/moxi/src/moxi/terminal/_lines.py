"""The words of the doctor's terminal record row, and the commands every line about the
record names.

Every command named here is a verb of the core itself (`moxi modules`, `moxi doctor`), so
the step works with no module installed. ui's check names the same two
(`moxi.terminal.RECORD_COMMAND`, `DOCTOR_COMMAND`), so its lines and this row can't
disagree.

Standard library only.
"""

from __future__ import annotations

#: The variable only test kits set (`moxi.paths.ANCHOR_HOME_ENV`, repeated here so the words
#: import nothing).
ANCHOR_HOME = "MOXI_ANCHOR_HOME"

#: The command a line names to make a record: cheap, a core verb, and not one the front
#: door skips, so typing it records.
RECORD_COMMAND = "moxi modules"
#: The command a line names to find out what stops a record.
DOCTOR_COMMAND = "moxi doctor"

#: The agents by the names a person knows them by.
HARNESS_NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
#: The harnesses' tool marks, each with its harness (`moxi._mark.TOOL_MARKS`, repeated here
#: so the words import nothing).
TOOL_MARKS = {"CLAUDECODE": "claude"}
#: How the walk names a program run from a file descriptor.
FD_PREFIX = "/proc/self/fd/"


# -- the doctor row's words ------------------------------------------------------------------

def found_words(found) -> tuple[str, str]:
    """(what the walk found, the one step) for an `Evidence` of `moxi.terminal`: a Moxi
    program, unit or start mark (E1, E3, E4), or an agent (E2). What the walk couldn't
    decide is worded by the record step (`moxi.terminal.row`), which prints the same
    words."""
    if found.kind == "E2":
        step = "type it in a terminal of your own"
        if found.detail in TOOL_MARKS:
            harness = HARNESS_NAMES.get(TOOL_MARKS[found.detail], found.detail)
            return (f"this command runs in a {harness} tool command (its environment "
                    f"carries {found.detail}), and nothing an agent runs records", step)
        if found.detail.startswith(FD_PREFIX):
            return (f"this command runs under a program started from a file descriptor "
                    f"(pid {found.pid}), as an agent's tool sandbox runs its commands, and "
                    "nothing an agent runs records", step)
        harness = HARNESS_NAMES.get(found.detail, found.detail)
        return (f"this command runs under {harness} (pid {found.pid}), an agent, and "
                "nothing an agent runs records", step)
    return (f"this terminal was started by Moxi ({_started(found)}), so nothing typed "
            "in it records", "type the command in a terminal of your own")


def _started(found) -> str:
    if found.kind == "E1":
        what = (f"{found.program} {found.detail}" if found.detail.startswith("-m ")
                else found.detail)
    elif found.kind == "E3":
        owner = _possessive(found.module) if found.module else "a Moxi unit's"
        what = f"{owner} {found.program} under {found.detail}"
    else:
        what = f"{found.program}, started by {found.detail}"
    return f"{what}, pid {found.pid}"


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


NO_KEYBOARD = ("moxi sees no terminal on its input here (it is redirected, or the terminal "
               "gives none)", "type it in a terminal without redirecting its input")


def row_anchor_set(record: str) -> tuple[str, str]:
    return (f"{ANCHOR_HOME} is set, which only test kits set, so the record goes to "
            f"{record}", "take it out of this terminal's environment")
