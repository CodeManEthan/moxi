"""Codex folder trust, settled on a launch's own argv: every Codex launch the launcher
composes, in interactive, host and exec mode, passes its folder's trust level itself, so
Codex neither shows its trust screen nor writes a trust entry into its config.

1. `trust_cwd` on: `trusted` for the launch folder (`ON`).
2. `trust_cwd` off, and the user's Codex config already gives the launch folder or its
   trust root a level, `trusted` or `untrusted`: nothing is passed, and Codex reads the
   user's entry (`USERS`).
3. `trust_cwd` off, and no entry: `trusted` for the launch folder, for this launch only
   (`NO_ENTRY`).

The launcher reads `$CODEX_HOME/config.toml` (default `~/.codex/config.toml`) and never
writes it. The lookup is Codex's own: the launch folder's key as given, then as its
canonical path, then the trust root's. The trust root is the nearest folder at or above
the launch folder's canonical path holding `.git`; when that `.git` is a file naming a
linked worktree's git folder (`<repo>/.git/worktrees/<name>`), it is the main checkout's
folder instead; any other `.git` file (a submodule's) gives none. A folder's entry counts
only when it names a trust level; one that names none leaves Codex to ask, so the launch
passes `trusted`. A config that can't be read or doesn't parse counts as no entry, and the
line says so.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

#: The three cases.
ON = "trust-cwd"
USERS = "users-entry"
NO_ENTRY = "no-entry"
#: The level the launcher passes in cases 1 and 3.
TRUSTED = "trusted"
#: The levels Codex reads from a project entry.
LEVELS = ("trusted", "untrusted")
#: The file Codex keeps its config in, in its home folder.
CONFIG_FILE = "config.toml"


@dataclass(frozen=True)
class Trust:
    """How one launch settles its folder's trust: the case, the level it passes (None in
    case 2), and a line for the preview and the launch record."""

    case: str
    level: str | None
    line: str

    def recorded(self) -> dict[str, object]:
        """The launch record's entry."""
        return {"case": self.case, "level": self.level, "line": self.line}


def config_file(env: Mapping[str, str]) -> Path:
    """Codex's config file for a session with environment `env`."""
    codex_home = env.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / CONFIG_FILE
    home = env.get("HOME") or os.path.expanduser("~")
    return Path(home) / ".codex" / CONFIG_FILE


def trust_root(cwd: str | os.PathLike) -> Path | None:
    """The folder Codex keys a git project's trust by, for a launch in `cwd`; None
    outside any repository, and for a `.git` file that names no linked worktree. The walk
    starts at the folder's canonical path, as git run in a symlinked folder does."""
    here = _canonical(Path(cwd))
    if not here.is_dir():
        here = here.parent
    for candidate in (here, *here.parents):
        dot_git = candidate / ".git"
        if dot_git.is_dir():
            return _canonical(candidate)
        if dot_git.is_file():
            return _worktree_main(dot_git)
    return None


def _worktree_main(dot_git: Path) -> Path | None:
    try:
        text = dot_git.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if not first_line.startswith("gitdir:"):
        return None
    git_dir = Path(first_line.removeprefix("gitdir:").strip())
    if not git_dir.is_absolute():
        git_dir = dot_git.parent / git_dir
    git_dir = _canonical(git_dir)
    worktrees = git_dir.parent
    if worktrees.name != "worktrees":
        return None
    return _canonical(worktrees.parent.parent)


def _canonical(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError:
        return path


def level_of(projects: Mapping[str, object], cwd: str) -> tuple[str | None, str | None]:
    """The trust level the user's `projects` table gives a launch in `cwd`, and the key it
    is under, as Codex looks it up: the first key found decides, even when it names no
    level."""
    keys = [cwd, str(_canonical(Path(cwd)))]
    root = trust_root(cwd)
    if root is not None:
        keys.append(str(root))
    for key in dict.fromkeys(keys):
        entry = projects.get(key)
        if isinstance(entry, Mapping):
            level = entry.get("trust_level")
            return (str(level) if level in LEVELS else None), key
    return None, None


def settle(cwd: str, env: Mapping[str, str], *, trust_cwd: bool) -> Trust:
    """How a Codex launch in `cwd`, for a session with environment `env`, settles its
    folder's trust; see the module's text for the rule."""
    if trust_cwd:
        return Trust(ON, TRUSTED, f"trust_cwd is on: {cwd} runs {TRUSTED} for this launch")
    path = config_file(env)
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config, why = {}, f"{path} does not exist"
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        config, why = {}, f"{path} can't be read: {exc}"
    else:
        why = f"{path} has no trust level for it"
    projects = config.get("projects") if isinstance(config, dict) else None
    level, key = level_of(projects if isinstance(projects, Mapping) else {}, cwd)
    if level is not None:
        return Trust(USERS, None, f"your Codex config gives {key} the level {level} "
                                  f"({path}): the launcher passes none")
    return Trust(NO_ENTRY, TRUSTED, f"{why}: {cwd} runs {TRUSTED} for this launch only, "
                                    "written nowhere")


__all__ = ["CONFIG_FILE", "LEVELS", "NO_ENTRY", "ON", "TRUSTED", "USERS", "Trust",
           "config_file", "level_of", "settle", "trust_root"]
