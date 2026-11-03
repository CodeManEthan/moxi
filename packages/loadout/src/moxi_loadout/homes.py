"""The harness homes a run resolves, recorded in the ledger and compared by a page job.

A page job runs in the browser shell's environment, not the user's terminal's, and what
decides where loadout writes may differ between the two:

- the variables that place a harness's home (Claude Code's `CLAUDE_CONFIG_DIR`, Codex's
  `CODEX_HOME`, OpenCode's `XDG_CONFIG_HOME`, each with its default under `HOME`);
- `HOME` itself, which places Codex's skills folder and OpenCode's row folders;
- whether `PATH` finds git: without git the would-commit check can't run, so a run
  places where a run with git refuses.

So the ledger's `homes` holds each harness's home and the home folder (`home`) as the
user's own last run resolved them, and its `git` holds whether that run found git:

- `SETUP` records them under the core lock it already holds, so every `moxi install
  loadout` and upgrade records them from the terminal;
- every verb the user runs that installs records them (`install`, `add`, `remove`,
  `enable`, `disable`, `global mode`, `global off`);
- a run whose caller sets `MARKER` in its environment is not the user's own and leaves
  them as they are: the git hook's child install and a page job set it.

A page job compares what it resolves with the record before anything is written, and
refuses with one line (`check`) when a home differs, when the record has git and the
job's `PATH` finds none, or when nothing is recorded. A job that finds git where the
record has none is let through: the check then refuses more, never less. `status` shows
the same comparison as a note. Inside a page job, and on the page (`as_page`), the line
says what to set for the browser shell; in a terminal it says what the page does. The
`claude` and `codex` programs are never compared: a page whose `PATH` finds no CLI does
what a terminal without it does, which writes less.

Standard library only at import.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import shutil
import sys
from collections.abc import Iterator

#: The variable a caller sets, to any value, when a run is not the user's own: the run
#: then leaves the recorded homes as they are. Its value names the caller, for the log.
MARKER = "MOXI_LOADOUT_CALLER"
#: The marker's value in a page job.
PAGE_JOB = "page-job"
#: The ledger's key for the homes.
KEY = "homes"
#: The ledger's key for whether the run found git.
GIT = "git"
#: The homes' key for the home folder itself.
HOME = "home"
#: The variable that places each home.
VARIABLES = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME",
             "opencode": "XDG_CONFIG_HOME", HOME: "HOME"}
#: Each harness by the name a person knows it by.
NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
#: The command that records the homes from a terminal.
COMMAND = "moxi loadout install"

_page = contextvars.ContextVar("moxi_loadout_page", default=False)


def resolved() -> dict[str, str]:
    """Each harness's home and the home folder, as this process's environment places
    them, absolute."""
    from pathlib import Path

    from moxi_loadout import harness

    found = {h: os.path.abspath(harness.home(h)) for h in harness.HARNESSES}
    found[HOME] = os.path.abspath(Path.home())
    return found


def git_found() -> bool:
    """Whether this process's `PATH` finds git."""
    return shutil.which("git") is not None


def users_own() -> bool:
    """Whether this run is the user's own: no caller set `MARKER`."""
    return not os.environ.get(MARKER)


def on_page() -> bool:
    """Whether a line said now is read on the browser page: in a page job, or inside
    `as_page` (the page server's reads)."""
    return os.environ.get(MARKER) == PAGE_JOB or _page.get()


@contextlib.contextmanager
def as_page() -> Iterator[None]:
    """Word the lines said inside for the browser page, in this thread."""
    token = _page.set(True)
    try:
        yield
    finally:
        _page.reset(token)


def same(data: dict) -> bool:
    """Whether ledger data `data` records what this run would."""
    return data.get(KEY) == resolved() and data.get(GIT) is git_found()


def record(data: dict) -> bool:
    """Put this run's homes and whether it found git in ledger data `data`, unless the
    run isn't the user's own. Returns whether it did. The caller writes the ledger."""
    if not users_own():
        return False
    data[KEY] = resolved()
    data[GIT] = git_found()
    return True


def recorded(data: dict) -> dict[str, str] | None:
    """The homes ledger data `data` holds, or None when it holds none for some home, or
    no word on git (a lost ledger, or one older than this record)."""
    found = data.get(KEY)
    if not isinstance(found, dict) or not isinstance(data.get(GIT), bool):
        return None
    if not all(isinstance(found.get(h), str) and found.get(h) for h in VARIABLES):
        return None
    return {h: found[h] for h in VARIABLES}


def check(data: dict, *, page: bool | None = None) -> str | None:
    """The one line a page job refuses with, or None when this process places what the
    user's own last run (ledger data `data`) placed. `page` words it for the browser
    page (the default inside a page job or `as_page`) or for a terminal."""
    page = on_page() if page is None else page
    then = recorded(data)
    if then is None:
        why = ("loadout has no record of the harness homes your terminal uses (its ledger "
               "is new, older than this record, or was lost)")
        if page:
            return (f"{why}, so it can't tell where this would write; run {COMMAND} once "
                    "in a terminal, then try again")
        return f"{why}, so the browser page refuses to write until {COMMAND} runs once here"
    now = resolved()
    differ = [h for h in now if os.path.normcase(now[h]) != os.path.normcase(then[h])]
    parts = [f"{_label(h)} here is {now[h]}, but the last run in a terminal used "
             f"{then[h]}" for h in differ]
    names = [VARIABLES[h] for h in differ]
    if data[GIT] and not git_found():
        parts.append("git isn't on PATH here, but the last run in a terminal found it, "
                     "so nothing can check what git would commit")
        names.append("PATH, with git's folder on it,")
    if not parts:
        return None
    said = "; ".join(parts) + ". "
    them = "it" if len(names) == 1 else "them"
    named = _and(names)
    if page:
        return (said + f"Set {named} {_where()}, so the browser shell has {them} at every "
                f"login{_restart(True)}, or run the command in a terminal")
    return (said + "The browser page refuses to write while its shell differs from the "
            f"last run in a terminal; to give the browser shell {named} at every login, "
            f"set {them} {_where()}{_restart(False)}")


def path_fix(program: str) -> str:
    """The lasting fix for a `program` that `PATH` doesn't find, worded for the browser
    page or for a terminal as `check` words its line."""
    if on_page():
        return (f"set PATH, with {program}'s folder on it, {_where()}, so the browser "
                f"shell finds {program} at every login{_restart(True)}, or run the command "
                "in a terminal")
    return (f"put {program}'s folder on PATH; the browser page does the same until its "
            f"own PATH finds {program}, so to give the browser shell that PATH at every "
            f"login, set it {_where()}{_restart(False)}")


def _label(home: str) -> str:
    return "the home folder" if home == HOME else f"{NAMES[home]}'s home"


def _and(names: list[str]) -> str:
    names = list(dict.fromkeys(names))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _where() -> str:
    """Where a variable is set so the browser shell sees it at every login."""
    if sys.platform == "darwin":
        return "in your LaunchAgent environment"
    if sys.platform == "win32":
        return "in your user environment variables"
    return "in a file under ~/.config/environment.d/"


def _restart(page: bool) -> str:
    """The clause that restarts the page server so it sees the change: on the page
    always, in a terminal only when the browser UI is installed."""
    if not page:
        from moxi import modules

        if not modules.available("ui"):
            return ""
    return ", then run moxi ui stop"


__all__ = ["MARKER", "PAGE_JOB", "KEY", "GIT", "HOME", "VARIABLES", "NAMES", "COMMAND",
           "resolved", "git_found", "users_own", "on_page", "as_page", "same", "record",
           "recorded", "check", "path_fix"]
