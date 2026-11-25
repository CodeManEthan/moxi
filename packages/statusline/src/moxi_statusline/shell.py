"""The shell Claude Code runs a status line command with, found the way Claude Code finds it.

POSIX: `sh -c`. Windows, as Claude Code's docs say: Git Bash when it is installed, else
PowerShell. Git Bash is the file `CLAUDE_CODE_GIT_BASH_PATH` names when that is an existing
file named `bash.exe`, `sh.exe`, `bash` or `sh` (any other value is ignored, as Claude Code
ignores it), else Git for Windows' own `bash.exe`, found from the `git` on `PATH`: git sits
in `cmd`, `bin` or `mingw64\\bin` under Git's folder, and bash in its `bin`. A `bash` that
is only on `PATH` is never taken, since the one Windows ships is WSL's. With neither,
PowerShell: `powershell.exe -NoProfile -NonInteractive -Command`. Claude Code's docs name
no flags for it; these are Moxi's.

detect and pieces both run a command through `find(env)`.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass

_WINDOWS = sys.platform == "win32"

#: The variable Claude Code reads for Git Bash's path on Windows.
GIT_BASH_PATH_ENV = "CLAUDE_CODE_GIT_BASH_PATH"
#: The file names Claude Code takes `CLAUDE_CODE_GIT_BASH_PATH` to name.
BASH_NAMES = ("bash.exe", "sh.exe", "bash", "sh")
#: What runs a command where no Git Bash is found on Windows; the command follows.
POWERSHELL = ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command")
#: The kinds of shell `find` gives.
KINDS = ("sh", "bash", "powershell")


@dataclass(frozen=True)
class Shell:
    """A shell: its `kind` (one of `KINDS`) and the `program` with its flags, which the
    command follows."""

    kind: str
    program: tuple[str, ...]

    def argv(self, command: str) -> list[str]:
        """The argv that runs `command` in this shell."""
        return [*self.program, command]


def find(env: Mapping[str, str] | None = None) -> Shell:
    """The shell Claude Code would run a status line with under `env` (None: this
    process's environment)."""
    if not _WINDOWS:
        return Shell("sh", ("sh", "-c"))
    env = os.environ if env is None else env
    bash = _named_bash(_get(env, GIT_BASH_PATH_ENV)) or _git_bash(_get(env, "PATH"))
    if bash is not None:
        return Shell("bash", (bash, "-c"))
    return Shell("powershell", POWERSHELL)


def _get(env: Mapping[str, str], name: str) -> str | None:
    """`env`'s value for `name`, whatever the case it is spelt in (Windows' rule)."""
    if name in env:
        return env[name]
    return next((value for key, value in env.items() if key.upper() == name), None)


def _named_bash(path: str | None) -> str | None:
    """`path`, when it is an existing file named as a bash or sh."""
    if path and os.path.basename(path).lower() in BASH_NAMES and os.path.isfile(path):
        return path
    return None


def _git_bash(search: str | None) -> str | None:
    """Git for Windows' `bin\\bash.exe`, from the `git` found on `search`, a `PATH`."""
    git = shutil.which("git", path=search)
    if git is None:
        return None
    where = os.path.dirname(os.path.abspath(git))
    parts = [os.path.basename(where).lower(), os.path.basename(os.path.dirname(where)).lower()]
    if parts == ["bin", "mingw64"]:
        root = os.path.dirname(os.path.dirname(where))
    elif parts[0] in ("cmd", "bin"):
        root = os.path.dirname(where)
    else:
        return None
    bash = os.path.join(root, "bin", "bash.exe")
    return bash if os.path.isfile(bash) else None


__all__ = ["Shell", "find", "GIT_BASH_PATH_ENV", "BASH_NAMES", "POWERSHELL", "KINDS"]
