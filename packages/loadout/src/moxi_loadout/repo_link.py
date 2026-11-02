"""The optional link to repo: the two git hook pieces that reinstall the loadout folder.

repo is reached in process, never by a subprocess: `moxi.modules.available("repo")`
first, then `load("repo", interface=1)` and its API, at the point of use, so nothing of
repo is imported unless the link is used.

`sync(folder, on)` is what `install` and `SETUP` run. With repo installed and the folder
in a git work tree, it registers each piece the work tree lacks while `on`
(`reinstall_on_commit`) is true, one piece per call, and removes each registered one
while it is false. A piece the user disabled through repo stays registered and disabled.
Without repo it does nothing: the user runs `moxi loadout install` after a commit, and
the doctor reads the link `off`. Repo's own listener rewrites the pieces when loadout is
upgraded and removes them when loadout is uninstalled.

`state(folder, on)` is the doctor's reading of the same: one `moxi.doctor.Row`.

What it says goes through `say`; a failure is a line, never an exit code, since the
reinstall is a convenience the doctor reports on, not part of what install applies. That
holds for repo's own failures and for its git calls that run out of time or can't run.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

#: The repo interface loadout reads.
INTERFACE = 1
#: The two pieces, by id, in the order they are registered.
PIECES = ("loadout/reinstall-commit", "loadout/reinstall-merge")
#: The setting that switches them.
SETTING = "reinstall_on_commit"


def api():
    """repo's API, or a line saying why there is none (`(api, None)` or `(None, line)`).
    `None` for the line means repo is not installed."""
    from moxi import modules

    if not modules.available("repo"):
        return None, None
    found = modules.load("repo", interface=INTERFACE)
    if found.state == "absent":
        return None, None
    if not found.ok or found.api is None:
        return None, f"repo can't be loaded ({found.reason}); moxi doctor says more"
    return found.api, None


def in_work_tree(folder: str | os.PathLike) -> bool:
    """Whether `folder` is an existing folder inside a git work tree: it, or a folder
    above it, holds a `.git` (a folder, or the file a linked worktree or a submodule
    has). It runs no git, so it is cheap enough for every install and the doctor; repo
    reads the work tree itself when it registers."""
    here = os.path.abspath(folder)
    if not os.path.isdir(here):
        return False
    while True:
        if os.path.lexists(os.path.join(here, ".git")):
            return True
        up = os.path.dirname(here)
        if up == here:
            return False
        here = up


def registered(link, folder) -> dict[str, dict]:
    """loadout's pieces registered in the repo holding `folder`, by id, each as repo's
    status lists it. Raises the link's `Failure`."""
    listed = link.status(folder)
    found = listed[0]["pieces"] if listed else []
    return {p["id"]: p for p in found if p.get("id") in PIECES}


def sync(folder: str | os.PathLike, on: bool, *,
         say: Callable[[str], object] = print) -> None:
    """Register the pieces in the work tree holding `folder` while `on`, or remove them
    while not. See the module's docs."""
    if not in_work_tree(folder):
        return
    link, why = api()
    if link is None:
        if why:
            say(f"the git hook pieces weren't checked: {why}")
        return
    try:
        have = registered(link, folder)
        if on:
            for pid in PIECES:
                if pid not in have:
                    _report(link.add(folder, pid), say)
        else:
            for pid in PIECES:
                if pid in have:
                    _report(link.remove(folder, pid), say)
    except link.Failure as exc:
        say(f"the git hook pieces weren't {_verb(on)}: {exc.text}")
        for line in exc.lines:
            say(f"  {line}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        say(f"the git hook pieces weren't {_verb(on)}: {_git_failed(exc)}")


def _verb(on: bool) -> str:
    return "registered" if on else "removed"


def _git_failed(exc: BaseException) -> str:
    """One line for a git call of repo's that ran out of time or couldn't run."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"git did not answer in {exc.timeout:g} s"
    return f"git couldn't run ({exc})"


def _report(report, say) -> None:
    """Say what one of repo's verbs did, as loadout's lines."""
    for line in report.lines:
        say(line)
    for root, event, hook, line, present in report.chain:
        if not present:
            say(f"{root}: {event}: git runs the reinstall only once this line is "
                f"in {hook}, before any line that reads stdin:\n  {line}")
    for problem in report.problems:
        say(str(problem))


def state(folder: str | os.PathLike, on: bool):
    """The doctor's row for the link: `off` when the folder is in no git work tree, when
    repo is absent (with repo, a commit reinstalls) or when the setting is off; `warn`
    when repo can't be loaded or read, or a piece is unregistered or disabled while the
    setting is on; else `ok`."""
    from moxi.doctor import Row

    name = "git hook"
    if not in_work_tree(folder):
        return Row("loadout", name, "off", f"the loadout folder {folder} is in no git "
                   "work tree, so no git hook reinstalls it")
    link, why = api()
    if link is None and why is None:
        return Row("loadout", name, "off", "the loadout folder is in git, and repo is not "
                   "installed: with repo, a commit reinstalls",
                   "moxi install loadout --with repo")
    if link is None:
        return Row("loadout", name, "warn", why, "moxi doctor")
    if not on:
        return Row("loadout", name, "off", f"{SETTING} is off, so a commit to the loadout "
                   "folder doesn't reinstall it",
                   f"moxi config set loadout {SETTING} true")
    try:
        have = registered(link, folder)
    except link.Failure as exc:
        return Row("loadout", name, "warn", f"repo can't read the work tree holding the "
                   f"loadout folder: {exc.text}", "moxi repo git-hooks list")
    missing = [pid for pid in PIECES if pid not in have]
    if missing:
        return Row("loadout", name, "warn", f"{SETTING} is on, and the git hook "
                   f"piece{'s' * (len(missing) != 1)} {', '.join(missing)} "
                   f"{'are' if len(missing) != 1 else 'is'} not registered, so a commit "
                   "doesn't reinstall", "moxi loadout install")
    off = [pid for pid in PIECES if have[pid].get("enabled") is False]
    if off:
        root = _quoted(folder)
        return Row("loadout", name, "warn", f"the git hook piece{'s' * (len(off) != 1)} "
                   f"{', '.join(off)} {'are' if len(off) != 1 else 'is'} disabled in repo",
                   " and ".join(f"moxi repo git-hooks enable {root} {pid}" for pid in off)
                   + f" (or, to stop it: moxi config set loadout {SETTING} false)")
    return Row("loadout", name, "ok", "a commit or a merge that changes the loadout folder "
               "reinstalls it")


def _quoted(path) -> str:
    import shlex
    import sys

    text = os.fspath(path)
    if sys.platform == "win32":
        return f'"{text}"' if " " in text else text
    return shlex.quote(text)


__all__ = ["INTERFACE", "PIECES", "SETTING", "api", "in_work_tree", "registered", "sync",
           "state"]
