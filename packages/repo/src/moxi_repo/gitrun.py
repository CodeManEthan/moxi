"""A small git helper: `git()` with a timeout and no prompts, `changes()` and
`literal_pathspec()`.

`git(root, *args)` runs `git -C <root> <args>` through `moxi.proc.run`, so a git that
hangs is killed with everything it started. It never waits on a person: the terminal
prompt is off (`GIT_TERMINAL_PROMPT=0`) and ssh runs in batch mode
(`GIT_SSH_COMMAND="ssh -o BatchMode=yes"`), unless the user set their own ssh: the
environment's `GIT_SSH_COMMAND` or `GIT_SSH`, or, for a command that reaches a remote
(`push`, `fetch`, `ls-remote` and the like), the repo's `core.sshCommand`, read with one
`git config` call before it. With `clean_env` (the default) it first drops
git's local variables (`GIT_DIR`, `GIT_INDEX_FILE` and the rest that
`git rev-parse --local-env-vars` lists) and the four global pathspec variables, so a call
made from inside a git hook acts on the repo it names, and pathspecs read as git's
defaults. It never sets a pathspec variable.

`changes(root, staged=True | range=(a, b), paths=())` lists what changed, from git's `-z`
output, with each path exactly as git gave it. Every path handed to git is written in its
literal form, `literal_pathspec(path)`, so `[`, `*`, `?` and a leading `:` in a file name
are plain characters.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: git's global pathspec variables, which `--local-env-vars` does not list.
PATHSPEC_VARS = ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS",
                 "GIT_ICASE_PATHSPECS")
DEFAULT_TIMEOUT = 30.0
#: The most paths, and the most bytes of path text, a list given to git in argv may hold.
#: A longer list goes on stdin, on the commands that take one there.
MAX_PATHS = 64
MAX_PATH_BYTES = 8 * 1024

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


#: The ssh command git runs when the user set none of their own.
BATCH_SSH = "ssh -o BatchMode=yes"
#: git commands that can reach a remote, so ssh; before one, `git()` reads
#: `core.sshCommand`. (`remote` only with one of `_REMOTE_VERBS`.)
_REMOTE_COMMANDS = frozenset({"push", "fetch", "pull", "ls-remote", "clone", "archive",
                              "submodule", "remote"})
_REMOTE_VERBS = frozenset({"update", "prune", "show", "set-head"})
#: git's own options that take their value as the next argument.
_VALUED = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})


def environment(*, clean_env: bool = True, author: tuple[str, str] | None = None,
                extra: dict[str, str] | None = None,
                batch_ssh: bool = True) -> dict[str, str]:
    """The environment `git()` gives git. `GIT_SSH_COMMAND` is set to `BATCH_SSH` only
    with `batch_ssh` and when the environment sets neither it nor `GIT_SSH`."""
    env = dict(os.environ)
    if clean_env:
        for name in (*local_env_vars(), *PATHSPEC_VARS):
            env.pop(name, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if batch_ssh and not env.get("GIT_SSH_COMMAND") and not env.get("GIT_SSH"):
        env["GIT_SSH_COMMAND"] = BATCH_SSH
    if author is not None:
        name, email = author
        env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
                   GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    if extra:
        env.update(extra)
    return env


def git(root: str | os.PathLike, *args: str, timeout: float = DEFAULT_TIMEOUT,
        input: bytes | str | None = None, author: tuple[str, str] | None = None,
        clean_env: bool = True, binary: bool = False,
        extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run `git -C <root> <args>`; return the finished process.

    `stdout` and `stderr` are text (UTF-8, with bytes that aren't kept as surrogates);
    with `binary`, `stdout` is the bytes git wrote. `input` is fed on stdin, else stdin is
    closed. The exit status is not checked; git missing raises `OSError`, and a run past
    `timeout` seconds is killed and raises `subprocess.TimeoutExpired`. `author` (name,
    email) is set as author and committer. `extra_env` adds variables to git's
    environment.
    """
    from moxi import proc

    data = input.encode("utf-8") if isinstance(input, str) else input
    batch = not _users_ssh(root, args, clean_env, timeout)
    done = proc.run(["git", "-C", os.fspath(Path(root)), *args], timeout=timeout,
                    input=data, env=environment(clean_env=clean_env, author=author,
                                                extra=extra_env, batch_ssh=batch))
    out = done.stdout or b""
    return subprocess.CompletedProcess(
        done.args, done.returncode, out if binary else out.decode("utf-8", "surrogateescape"),
        (done.stderr or b"").decode("utf-8", "surrogateescape"))


def _split(args) -> tuple[list[str], str | None, list[str]]:
    """git's own options, the command, and the command's arguments."""
    args = list(args)
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in _VALUED else 1
    if i >= len(args):
        return args, None, []
    return args[:i], args[i], args[i + 1:]


def _users_ssh(root, args, clean_env: bool, timeout: float) -> bool:
    """True when the command can reach a remote and the user set their own ssh in git's
    config: a `-c core.sshCommand=...` in `args`, or the repo's `core.sshCommand`. The
    environment's own variables are `environment()`'s to see."""
    lead, command, rest = _split(args)
    if command not in _REMOTE_COMMANDS:
        return False
    if command == "remote" and not (_REMOTE_VERBS & set(rest[:1])):
        return False
    env = os.environ
    if env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH"):
        return False
    for i, arg in enumerate(lead):
        if arg == "-c" and i + 1 < len(lead):
            if lead[i + 1].partition("=")[0].lower() == "core.sshcommand":
                return True
    from moxi import proc

    try:
        done = proc.run(["git", "-C", os.fspath(Path(root)), *lead, "config", "--get",
                         "core.sshCommand"], timeout=timeout,
                        env=environment(clean_env=clean_env))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and bool((done.stdout or b"").strip())


def literal_pathspec(path: str | os.PathLike) -> str:
    """`path` as a literal pathspec: `[`, `*`, `?` and a leading `:` are plain characters."""
    return ":(literal)" + os.fspath(path)


def paths_problem(paths) -> str | None:
    """Why a list of paths can't go to git in argv, or None when it can: over
    `MAX_PATHS` paths or `MAX_PATH_BYTES` bytes of path text."""
    paths = list(paths)
    if len(paths) > MAX_PATHS:
        return f"{len(paths)} paths, more than the {MAX_PATHS} a list in argv may hold"
    size = sum(len(os.fsencode(p)) for p in paths)
    if size > MAX_PATH_BYTES:
        return (f"{size} bytes of path text, more than the {MAX_PATH_BYTES} a list in argv "
                "may hold")
    return None


class GitFailed(Exception):
    """A git call `changes()` made failed; the message holds git's own line."""

    def __init__(self, args, returncode: int, stderr: str):
        line = (stderr or "").strip().splitlines()
        super().__init__(f"git {' '.join(args)} exited {returncode}"
                         + (f": {line[-1]}" if line else ""))
        self.returncode = returncode
        self.stderr = stderr


def _zero(blob: str) -> bool:
    """True for the all-zero id git gives the side of a change that isn't there."""
    return not blob.strip("0")


@dataclass(frozen=True)
class Change:
    """One changed path. `status` is git's letter (`A`, `M`, `D`, `R`, `T`, ...);
    `old_path` is a rename's old side, else None; `blob` is the new side's object id,
    None for a deletion. `path` holds git's bytes exactly (`os.fsencode(path)` gives
    them back)."""

    status: str
    path: str
    old_path: str | None = None
    blob: str | None = None


def parse_raw(data: bytes) -> list[Change]:
    """`git diff-index`/`diff-tree --raw -z` output as `Change`s."""
    fields = data.split(b"\0")
    out: list[Change] = []
    i = 0
    while i < len(fields):
        meta = fields[i]
        if not meta.startswith(b":"):
            i += 1
            continue
        parts = meta[1:].decode("ascii", "replace").split()
        status, blob = parts[4][:1], parts[3]
        if status in ("R", "C"):
            old, new = os.fsdecode(fields[i + 1]), os.fsdecode(fields[i + 2])
            i += 3
        else:
            old, new = None, os.fsdecode(fields[i + 1])
            i += 2
        out.append(Change(status, new, old, None if _zero(blob) else blob))
    return out


def empty_tree(root: str | os.PathLike, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """The id of the empty tree in this repo's hash."""
    done = git(root, "hash-object", "-t", "tree", "--stdin", input=b"", timeout=timeout)
    if done.returncode != 0:
        raise GitFailed(("hash-object",), done.returncode, done.stderr)
    return done.stdout.strip()


def head_commit(root: str | os.PathLike, *, timeout: float = DEFAULT_TIMEOUT) -> str | None:
    """`HEAD`'s commit id, or None on an unborn branch."""
    done = git(root, "rev-parse", "-q", "--verify", "HEAD^{commit}", timeout=timeout)
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def changes(root: str | os.PathLike, staged: bool = True, range: tuple[str, str] | None = None,
            paths=(), *, renames: bool = True,
            timeout: float = DEFAULT_TIMEOUT) -> list[Change]:
    """What changed under `paths` (every path when none): what is staged against `HEAD`
    (the empty tree on an unborn branch), or with `range=(a, b)` between two commits.

    Each path goes to git in its literal form, in argv: `paths` over `MAX_PATHS` paths or
    `MAX_PATH_BYTES` bytes raises `ValueError`. Renames are found (`-M`) unless
    `renames` is False. A git failure raises `GitFailed`.

    The one git call: `git diff-index --cached --raw -z [-M | --no-renames] <HEAD or the
    empty tree> -- <paths>`, or `git diff-tree -r --raw -z [-M | --no-renames] <a> <b> --
    <paths>`.
    """
    paths = [os.fspath(p) for p in paths]
    problem = paths_problem(paths)
    if problem:
        raise ValueError(f"changes() takes at most {MAX_PATHS} paths and {MAX_PATH_BYTES} "
                         f"bytes in all; this list has {problem.split(',')[0]}")
    find = "-M" if renames else "--no-renames"
    tail = ["--", *(literal_pathspec(p) for p in paths)]
    if range is not None:
        a, b = range
        args = ["diff-tree", "-r", "--raw", "-z", find, a, b, *tail]
    else:
        if not staged:
            raise ValueError("changes() reads what is staged, or a range=(a, b)")
        base = head_commit(root, timeout=timeout) or empty_tree(root, timeout=timeout)
        args = ["diff-index", "--cached", "--raw", "-z", find, base, *tail]
    done = git(root, *args, binary=True, timeout=timeout)
    if done.returncode != 0:
        raise GitFailed(args[:1], done.returncode, done.stderr)
    return parse_raw(done.stdout)


__all__ = ["git", "changes", "Change", "literal_pathspec", "environment", "local_env_vars",
           "PATHSPEC_VARS", "MAX_PATHS", "MAX_PATH_BYTES", "paths_problem", "GitFailed",
           "parse_raw", "empty_tree", "head_commit"]
