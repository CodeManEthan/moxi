"""The shell text repo writes, and the shims it places in a repo's hooks dir.

Three things reach the runner, each a few lines of `sh` that start with the same guard:

- a **config entry** (git 2.54 and later): `hook.moxi-repo-<event>.command`, one per
  event, in the repo's local config (written by `gitconf`);
- a **shim** (below 2.54): `<hooks dir>/<event>`, marked by its second line, which runs
  the pieces and then the existing git hook, moved beside it as `<event>.local`;
- the **launcher**, `<common git dir>/moxi-repo/hook`, for shim mode where the hooks dir
  is outside the git dir (a tracked `.githooks/`, husky's `.husky/_`, a dir other repos
  share). Repo writes nothing there; the user adds the **chain line** to the manager's
  git hook instead. The chain line names no path, and where the launcher is missing (a
  teammate's clone, CI) it does nothing.

The guard tests the interpreter (`-x`) and the runner's file (`-f`) without starting
Python. When either is missing it prints one line and exits 1 if the event has an
enabled closed piece, else 0; repo writes that status in, so every change to an event's
pieces rewrites the text.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from moxi_repo.run import quote_arg

#: The shim's second line starts with this, then `(<event>).`
SHIM_MARK = "# moxi-repo shim ("
_SHIM_LINE = re.compile(r"# moxi-repo shim \(([a-z-]+)\)\.")
LAUNCHER_MARK = "# moxi-repo launcher."
LOCAL = ".local"
#: The name a hook manager that runs the hook it moved aside gives it: `<event>.legacy`.
LEGACY = ".legacy"
#: The launcher's place under the common git dir.
LAUNCHER = ("moxi-repo", "hook")


def sh_quote(text: str) -> str:
    """`text` as one single-quoted sh word."""
    return "'" + text.replace("'", "'\\''") + "'"


def sh_path(path: str | os.PathLike) -> str:
    """A path as repo writes it into sh: forward slashes, on every OS."""
    return Path(path).as_posix()


def _missing_python(python: str, root: str) -> str:
    return (f"repo: git hooks can't run here: {python} is missing; run: moxi install repo "
            f"(or: moxi repo git-hooks remove {quote_arg(root)})")


def _missing_runner(python: str, root: str) -> str:
    return (f"repo: git hooks can't run here: the environment of {python} has no moxi_repo; "
            f"run: moxi install repo (or: moxi repo git-hooks remove {quote_arg(root)})")


def entry_command(event: str, python: str, runner: str, root: str, code: int) -> str:
    """A config entry's command. git appends the hook's arguments to it."""
    py, run = sh_quote(python), sh_quote(runner)
    return (f"[ -x {py} ] || {{ printf '%s\\n' {sh_quote(_missing_python(python, root))} >&2; "
            f"exit {code}; }}; "
            f"[ -f {run} ] || {{ printf '%s\\n' {sh_quote(_missing_runner(python, root))} >&2; "
            f"exit {code}; }}; "
            f"{py} -I -m moxi_repo.run {event}")


def shim_text(event: str, python: str, runner: str, root: str, code: int,
              stdin: bool) -> str:
    """The shim for `event`: the guard, the pieces, then `<event>.local` if it is there.

    The existing git hook runs whatever the pieces did, with the same arguments and
    stdin, and also when the guard stops the pieces.
    """
    lines = [
        "#!/bin/sh",
        f"{SHIM_MARK}{event}). The existing git hook, if any, is {event}{LOCAL} beside it.",
        f"PY={sh_quote(python)}; RUN={sh_quote(runner)}",
    ]
    if stdin:
        lines.append("input=$(cat; echo .); input=${input%.}")
    run = (f"printf '%s' \"$input\" | \"$PY\" -I -m moxi_repo.run {event} \"$@\"" if stdin
           else f"\"$PY\" -I -m moxi_repo.run {event} \"$@\"")
    lines += [
        f"if [ ! -x \"$PY\" ]; then printf '%s\\n' {sh_quote(_missing_python(python, root))} >&2;"
        f" rc={code}",
        f"elif [ ! -f \"$RUN\" ]; then printf '%s\\n' "
        f"{sh_quote(_missing_runner(python, root))} >&2; rc={code}",
        f"else {run}; rc=$?",
        "fi",
        f"local_hook=\"$(dirname \"$0\")/{event}{LOCAL}\"",
    ]
    if stdin:
        lines.append("if [ -x \"$local_hook\" ]; then printf '%s' \"$input\" | "
                     "\"$local_hook\" \"$@\" || rc=1; fi")
    else:
        lines.append("if [ -x \"$local_hook\" ]; then \"$local_hook\" \"$@\" || rc=1; fi")
    lines.append("exit $rc")
    return "\n".join(lines) + "\n"


def launcher_text(python: str, runner: str, root: str, closed_events: list[str]) -> str:
    """The launcher a chain line runs: `sh <launcher> <event> [git's arguments]`."""
    lines = [
        "#!/bin/sh",
        f"{LAUNCHER_MARK} A chain line in a git hook runs it as: sh <this file> <event> "
        "[arguments].",
        f"PY={sh_quote(python)}; RUN={sh_quote(runner)}",
    ]
    if closed_events:
        lines.append(f"case \"$1\" in {'|'.join(sorted(closed_events))}) rc=1 ;; *) rc=0 ;; esac")
    else:
        lines.append("rc=0")
    lines += [
        f"[ -x \"$PY\" ] || {{ printf '%s\\n' {sh_quote(_missing_python(python, root))} >&2; "
        "exit $rc; }",
        f"[ -f \"$RUN\" ] || {{ printf '%s\\n' {sh_quote(_missing_runner(python, root))} >&2; "
        "exit $rc; }",
        "exec \"$PY\" -I -m moxi_repo.run \"$@\"",
    ]
    return "\n".join(lines) + "\n"


_FIND = ('h="$(git rev-parse --path-format=absolute --git-common-dir)/moxi-repo/hook"; '
         'if [ -f "$h" ]; then ')


def chain_line(event: str, stdin: bool) -> str:
    """The line a user adds to a manager's git hook for `event`. It names no path.

    For an event with stdin it saves stdin to a temp file, feeds the launcher from it and
    points the rest of the git hook at the same file, so the lines after it read stdin as
    they would have without it.
    """
    if stdin:
        return (_FIND + 't="$(mktemp)" || exit 1; cat > "$t"; '
                f'sh "$h" {event} "$@" < "$t"; r=$?; exec < "$t"; rm -f "$t"; '
                '[ "$r" = 0 ] || exit 1; fi')
    return _FIND + f'sh "$h" {event} "$@" || exit 1; fi'


def has_chain_line(text: str, event: str) -> bool:
    """True when a git hook's text holds the chain line for `event`."""
    return "/moxi-repo/hook\"" in text and f'sh "$h" {event} "$@"' in text


# -- shims in a hooks dir -----------------------------------------------------------------

class ShimRefused(Exception):
    """A shim repo won't place; the message says why."""


def shim_event(path: Path) -> str | None:
    """The event a file in the hooks dir is our shim for, or None when it isn't ours."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(256)
    except OSError:
        return None
    lines = head.decode("utf-8", "replace").split("\n")
    if len(lines) < 2:
        return None
    found = _SHIM_LINE.match(lines[1])
    return found.group(1) if found else None


def our_shims(hooks_dir: Path) -> dict[str, list[Path]]:
    """Event -> every file in `hooks_dir` that is our shim for it, whatever its name."""
    out: dict[str, list[Path]] = {}
    try:
        entries = sorted(hooks_dir.iterdir())
    except OSError:
        return out
    for path in entries:
        if path.is_file() and not path.is_symlink():
            event = shim_event(path)
            if event:
                out.setdefault(event, []).append(path)
    return out


def check_place(hooks_dir: Path, event: str, moved: bool) -> None:
    """Raise `ShimRefused` when a shim for `event` can't be placed.

    `moved` says repo moved the existing git hook to `<event>.local` before. A
    `<event>.local` that repo didn't move is someone else's, and repo won't run it.
    """
    hook, local = hooks_dir / event, hooks_dir / (event + LOCAL)
    if our_shims(hooks_dir).get(event):
        return
    if not (local.exists() or local.is_symlink()):
        return
    if hook.exists() or hook.is_symlink():
        raise ShimRefused(f"{hook} and {local} both exist, so repo can't move the existing "
                          f"git hook aside without losing one; merge them into {hook} "
                          f"or remove {local} first")
    if not moved:
        raise ShimRefused(f"{local} already exists and repo didn't put it there, so the "
                          f"shim would run it; move or remove {local} first")


def place(hooks_dir: Path, event: str, text: str, moved: bool) -> list[str]:
    """Write (or rewrite) our shim for `event`; return what was done, one line each.

    Every file that is already our shim for the event is rewritten in place under its
    own name (a manager may have renamed it `<event>.legacy` or `<event>.old`). Where
    there is none, the existing git hook moves to `<event>.local` and the shim takes its
    place. Returns lines naming any move; raises `ShimRefused` as `check_place` does.
    """
    from moxi import files

    check_place(hooks_dir, event, moved)
    ours = our_shims(hooks_dir).get(event, [])
    if ours:
        for path in ours:
            files.atomic_write(path, text, mode=0o755)
        return []
    done = []
    hook = hooks_dir / event
    hooks_dir.mkdir(parents=True, exist_ok=True)
    if hook.exists() or hook.is_symlink():
        os.rename(hook, hooks_dir / (event + LOCAL))
        done.append(f"moved the existing git hook {hook} to {event}{LOCAL}")
    files.atomic_write(hook, text, mode=0o755)
    return done


def left_line(local: Path, hook: Path, *, final: bool = False) -> str:
    """The line for a git hook repo moved aside that stays at `local`, since another git
    hook took its slot. `final` (repo's own uninstall) adds that repo no longer records
    it, so it is the user's to merge or delete."""
    text = (f"left {local}: {hook} is now another tool's git hook, so the one repo moved "
            "aside stays where it is")
    if final:
        text += (f"; repo no longer records {local}, so merge it into {hook} or delete "
                 "it")
    return text


def take_away(hooks_dir: Path, event: str, moved: bool, *, final: bool = False) -> list[str]:
    """Remove every shim of ours for `event` and move `<event>.local` back when repo moved
    it; return what was done, one line each."""
    done = []
    for path in our_shims(hooks_dir).get(event, []):
        os.unlink(path)
    hook, local = hooks_dir / event, hooks_dir / (event + LOCAL)
    if moved and (local.exists() or local.is_symlink()):
        if hook.exists() or hook.is_symlink():
            done.append(left_line(local, hook, final=final))
        else:
            os.rename(local, hook)
            done.append(f"moved {event}{LOCAL} back to {hook}")
    return done


__all__ = ["entry_command", "shim_text", "launcher_text", "chain_line", "has_chain_line",
           "shim_event", "our_shims", "place", "take_away", "left_line", "check_place", "ShimRefused",
           "sh_quote", "sh_path"]
