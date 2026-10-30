"""A small git helper: `git()` with a timeout and no prompts, and `literal_pathspec()`.

`git(root, *args)` runs `git -C <root> <args>` through `moxi.proc.run`, so a git that
hangs is killed with everything it started. It never waits on a person: the terminal
prompt is off (`GIT_TERMINAL_PROMPT=0`) and ssh runs in batch mode, unless the caller's
environment already sets `GIT_SSH_COMMAND`. With `clean_env` (the default) it first drops
git's local variables (`GIT_DIR`, `GIT_INDEX_FILE` and the rest that
`git rev-parse --local-env-vars` lists) and the four global pathspec variables, so a call
made from inside a git hook acts on the repo it names, and pathspecs read as git's
defaults. It never sets a pathspec variable.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: git's global pathspec variables, which `--local-env-vars` does not list.
PATHSPEC_VARS = ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS",
                 "GIT_ICASE_PATHSPECS")
DEFAULT_TIMEOUT = 30.0

_local_vars: tuple[str, ...] | None = None


def local_env_vars() -> tuple[str, ...]:
    """The variables `git rev-parse --local-env-vars` names, asked once per process."""
    global _local_vars
    if _local_vars is None:
        from moxi import proc

        try:
            done = proc.run(["git", "rev-parse", "--local-env-vars"], timeout=DEFAULT_TIMEOUT)
            names = done.stdout.decode("utf-8", "replace").split()
        except (OSError, subprocess.TimeoutExpired):
            names = []
        _local_vars = tuple(names) or (
            "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
            "GIT_CONFIG_COUNT", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE",
            "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_INDEX_FILE",
            "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
            "GIT_INTERNAL_SUPER_PREFIX", "GIT_SHALLOW_FILE", "GIT_COMMON_DIR")
    return _local_vars


def environment(*, clean_env: bool = True, author: tuple[str, str] | None = None,
                extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment `git()` gives git."""
    env = dict(os.environ)
    if clean_env:
        for name in (*local_env_vars(), *PATHSPEC_VARS):
            env.pop(name, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    if author is not None:
        name, email = author
        env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
                   GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    if extra:
        env.update(extra)
    return env


def git(root: str | os.PathLike, *args: str, timeout: float = DEFAULT_TIMEOUT,
        input: bytes | str | None = None, author: tuple[str, str] | None = None,
        clean_env: bool = True) -> subprocess.CompletedProcess:
    """Run `git -C <root> <args>`; return the finished process.

    `stdout` and `stderr` are text (UTF-8, with bytes that aren't kept as surrogates).
    `input` is fed on stdin, else stdin is closed. The exit status is not checked; git
    missing raises `OSError`, and a run past `timeout` seconds is killed and raises
    `subprocess.TimeoutExpired`. `author` (name, email) is set as author and committer.
    """
    from moxi import proc

    data = input.encode("utf-8") if isinstance(input, str) else input
    done = proc.run(["git", "-C", os.fspath(Path(root)), *args], timeout=timeout,
                    input=data, env=environment(clean_env=clean_env, author=author))
    return subprocess.CompletedProcess(
        done.args, done.returncode, (done.stdout or b"").decode("utf-8", "surrogateescape"),
        (done.stderr or b"").decode("utf-8", "surrogateescape"))


def literal_pathspec(path: str | os.PathLike) -> str:
    """`path` as a literal pathspec: `[`, `*`, `?` and a leading `:` are plain characters."""
    return ":(literal)" + os.fspath(path)


__all__ = ["git", "literal_pathspec", "environment", "local_env_vars", "PATHSPEC_VARS"]
