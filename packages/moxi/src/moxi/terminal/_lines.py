"""The words of the terminal check and the doctor's terminal record row.

`where()` is where a person sets a variable for the browser shell on this platform, and the
step that makes the shell's next process take it. The other functions build the one line a
page refuses with (`moxi.terminal.check`) and the doctor row's words. Every command they
name is a verb of the core itself (`moxi modules`, `moxi doctor`), so the step works with
no module installed; `where()`'s restart step names `moxi ui stop`, the browser shell's own
verb, since only the browser shell reads what it sets.

Standard library only.
"""

from __future__ import annotations

import sys
from collections import namedtuple
from collections.abc import Iterable

#: The variable only test kits set (`moxi.paths.ANCHOR_HOME`, repeated here so the words
#: import nothing).
ANCHOR_HOME = "MOXI_ANCHOR_HOME"

#: The command a line names to make a record: cheap, a core verb, and not one the front
#: door skips, so typing it records.
RECORD_COMMAND = "moxi modules"
#: The command a line names to find out what stops a record.
DOCTOR_COMMAND = "moxi doctor"

#: The agents by the names a person knows them by.
HARNESS_NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


class Where(namedtuple("Where", ("place", "step"))):
    """Where to set a variable for the browser shell (`place`, words that follow "set X"),
    and the step after it that makes the shell's next process see the new value (`step`,
    words that follow "then")."""

    __slots__ = ()


def where(platform: str | None = None) -> Where:
    """Where the browser shell takes its environment from on `platform` (`sys.platform`
    without one), and the step that makes it take a change.

    Linux: a file under `~/.config/environment.d/`, which the user's service manager reads
    only when it starts or reloads, so the step reloads it and then stops the shell, which
    its socket starts again with the new environment. macOS and Windows: the LaunchAgent
    environment or the user environment variables, which the service manager takes at
    login, so the step is to log out and back in.
    """
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return Where("in your LaunchAgent environment", "log out and back in")
    if platform == "win32":
        return Where("in your user environment variables", "log out and back in")
    return Where("in a file under ~/.config/environment.d/",
                 "run systemctl --user daemon-reload and moxi ui stop")


# -- the check's lines -----------------------------------------------------------------------

def differs(label: str, here: str, there: str) -> str:
    return f"{label} here is {here}, but the last run in a terminal used {there}"


def placed_only_here(label: str, here: str) -> str:
    return f"{label} here is {here}, but the last run in a terminal placed none"


def placed_only_there(label: str, there: str) -> str:
    return f"{label} isn't placed here, but the last run in a terminal used {there}"


def not_full(label: str, here: bool, name: str | None, value: str | None) -> str:
    side = "here" if here else "in the last run in a terminal"
    text = f"{label} isn't a full path {side}"
    if name is not None:
        text += f' ({name} is "{value}")'
    return text


def program_missing(name: str, why: str) -> str:
    return (f"{name} isn't on PATH here, but the last run in a terminal found it, "
            f"so {why}")


def to_value(name: str, value: str) -> str:
    return f"{name} to {value}"


def to_empty(name: str) -> str:
    return f"{name} to an empty value"


def to_full_path(name: str) -> str:
    return f"{name} to a full path"


def path_with(program: str) -> str:
    return f"PATH, with {program}'s folder on it,"


def fix(problems: Iterable[str], sets: list[str], takes: list[str], *, page: bool,
        ui: bool, platform: str | None = None) -> str:
    """The difference line: `problems` joined, then the fix that sets every name in `sets`
    (already worded) and takes every name in `takes` out of the browser shell's
    environment. With neither, no variable places it differently."""
    said = "; ".join(problems) + ". "
    if not sets and not takes:
        if page:
            return said + ("No variable places it differently here, so run the command "
                           "in a terminal.")
        return said + ("No variable places it differently; the browser page refuses to "
                       "write while it resolves another place than the last run in a "
                       "terminal.")
    place, step = where(platform)
    action = _action(sets, takes, place)
    if page:
        return said + f"{_capital(action)}, then {step}; or run the command in a terminal."
    tail = f", then {step}" if ui else ""
    return (said + "The browser page refuses to write while its shell differs from the "
            f"last run in a terminal; to give the browser shell the terminal's values at "
            f"every login, {action}{tail}.")


def _action(sets: list[str], takes: list[str], place: str) -> str:
    parts = []
    if sets:
        parts.append(f"set {_and(sets)} {place}")
    if takes:
        named = _and(takes)
        parts.append(f"take {named} out of the browser shell's environment"
                     + ("" if sets else f" ({place.removeprefix('in ')})"))
    return ", and ".join(parts)


def no_record(record: str, *, page: bool) -> str:
    """No record at `record`: the step that makes one, and where to look when it doesn't."""
    if page:
        return ("Moxi has no record of the environment your terminal uses (it is kept at "
                f"{record}), so this page can't tell whether it would write where your "
                f"terminal does. Type {RECORD_COMMAND} in a terminal of your own, not one "
                "Moxi opened such as a slot's window or an agent's shell, then try again. "
                f"If this line comes back, run {DOCTOR_COMMAND} in that terminal: its "
                "terminal record row says what stops it.")
    return ("Moxi has no record of the environment your terminal uses (it is kept at "
            f"{record}), so the browser page refuses to write until one is made. Type "
            f"{RECORD_COMMAND} in a terminal of your own, not one Moxi opened such as a "
            f"slot's window or an agent's shell. If the browser page still says so, run "
            f"{DOCTOR_COMMAND} in that terminal: its terminal record row says what stops "
            "it.")


def no_place(cause: str, step: str, *, page: bool) -> str:
    """The account's folder can't be placed (`moxi.paths.NoAnchor`'s `cause` and `step`)."""
    who = "this page" if page else "the browser page"
    return (f"Moxi can't find where it keeps its record of your terminal: {cause}, so "
            f"{who} refuses to write; {step}, or run the command in a terminal.")


def unreadable(record: str, error: str, *, page: bool) -> str:
    who = "this page" if page else "the browser page"
    return (f"Moxi's record of your terminal's environment at {record} can't be read "
            f"({error}), so {who} refuses to write; fix its permissions or remove it, "
            f"then type {RECORD_COMMAND} in a terminal of your own.")


def newer(record: str, found_format: object, writer: object, mine: str, *,
          page: bool) -> str:
    who = "this page" if page else "the browser page"
    by = "a newer moxi" if writer is None else f"moxi {writer}"
    return (f"Moxi's record of your terminal's environment at {record} was written by "
            f"{by} in format {found_format}, newer than the moxi {mine} {who} "
            f"runs, so {who} refuses to write; restart {who} on the new code (run moxi ui "
            "stop); if the browser shell runs another Moxi environment that is older, run "
            "the command in a terminal.")


def anchor_set(*, page: bool) -> str:
    """The clause a line ends with when its own environment sets `MOXI_ANCHOR_HOME`."""
    whose = "This page's environment" if page else "This environment"
    return (f"{whose} sets {ANCHOR_HOME}, which only test kits set; take it out of the "
            "browser shell's environment.")


# -- the doctor row's words ------------------------------------------------------------------

def found_words(found) -> tuple[str, str]:
    """(what the walk found, the one step) for a walk result that stops a record: an
    `Evidence` or an `Undecided` of `moxi.terminal`."""
    kind = found.kind
    if kind == "E2":
        harness = HARNESS_NAMES.get(found.detail, found.detail)
        return (f"this command runs under {harness} (pid {found.pid}), an agent, and "
                "nothing an agent runs records", "type it in a terminal of your own")
    if kind in ("E1", "E3", "E4"):
        return (f"this terminal was started by Moxi ({_started(found)}), so nothing typed "
                "in it records", "type the command in a terminal of your own")
    reason = getattr(found, "reason", "")
    if kind == "N2":
        return (f"this terminal is inside a tmux server (pid {found.pid}) that moxi can't "
                "tell from slots' own, because no service manager runs slots' server "
                "here", "type the command in a terminal outside tmux")
    if reason == "ended":
        return ("a process above this one ended while moxi read it",
                "run the command again")
    name = found.program or "a process"
    error = {"deep": f"more than {_limit()} processes are above it",
             "loop": "its parents go round in a loop"}.get(reason) or found.error or reason
    return (f"moxi can't read {name} (pid {found.pid}) above this terminal ({error}), so "
            "it can't tell whether Moxi started this terminal",
            f"type the command in a terminal that {name} didn't start")


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


def _limit() -> int:
    from ._walk import LIMIT

    return LIMIT


NO_KEYBOARD = ("moxi sees no terminal on its input here (it is redirected, or the terminal "
               "gives none)", "type it in a terminal without redirecting its input")


def row_no_place(cause: str, step: str) -> tuple[str, str]:
    return f"Moxi can't find where it keeps the record: {cause}", step


def row_anchor_set(record: str) -> tuple[str, str]:
    return (f"{ANCHOR_HOME} is set, which only test kits set, so the record goes to "
            f"{record}", "take it out of this terminal's environment")


def row_unwritable(record: str, folder: str, error: str) -> tuple[str, str]:
    return (f"the record at {record} can't be written ({error})",
            f"make {folder} writable by you")


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _and(items: list[str]) -> str:
    items = list(dict.fromkeys(items))
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]
