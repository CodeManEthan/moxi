"""Reads of a repo's git, as the build states ask them.

`Git(root)` runs plain git in the repo at `root` and answers one question per method:
the top of the work tree `root` is in, which branch is checked out at the root, whether
its work tree holds anything not committed, whether a name is one git takes for a branch,
which commit a branch or a commit's short id is, which of some commits git still has, a
commit's tree, parents and commit date, whether one commit is an ancestor of another,
the merge bases of two, the one commit of several that has the others as ancestors, the
merge that brought a commit onto a branch's first-parent line, the commits of a range,
whether a range holds a commit that is not a merge, and the paths two commits differ
in. It writes nothing.

git runs with the caller's environment less every `GIT_` variable, so a variable meant
for another repo or another git never steers what is read here.

A commit id given to a method comes from a log, where anyone may have typed it, so only a
full hex id is passed to git as a commit; anything else is a commit git does not have.
A git that fails, or gives no answer in `GIT_TIMEOUT_S` seconds, raises `GitError`.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from moxi import proc

from .errors import RoundsError

#: How long one git read gets to answer.
GIT_TIMEOUT_S = 60.0

# A full commit id: SHA-1, or SHA-256 in a repo that uses it.
_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class GitError(RoundsError):
    """git could not answer: `argv` is what ran, and the reason says how it failed."""

    def __init__(self, argv: list[str], said: str):
        self.argv = argv
        super().__init__(f"`{' '.join(argv)}` {said}",
                         "check that the repo is there and git can read it")


def git_env() -> dict[str, str]:
    """The environment git runs in: this process's, less every `GIT_` variable."""
    return {name: value for name, value in os.environ.items()
            if not name.startswith("GIT_")}


def is_id(value: object) -> bool:
    """Whether `value` is written as a full commit id."""
    return isinstance(value, str) and _ID.fullmatch(value) is not None


class Git:
    """The git of the repo at `root`."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def _run(self, *args: str, ok: tuple[int, ...] = (0,),
             stdin_data: str | None = None) -> tuple[int, str]:
        argv = ["git", "-C", str(self.root), *args]
        try:
            done = proc.run(argv, timeout_s=GIT_TIMEOUT_S, env=git_env(),
                            stdin_data="" if stdin_data is None else stdin_data)
        except FileNotFoundError:
            raise GitError(argv, "could not run: no git is on PATH") from None
        except OSError as exc:
            raise GitError(argv, f"could not run: {exc}") from None
        except subprocess.TimeoutExpired:
            raise GitError(argv, f"gave no answer in {GIT_TIMEOUT_S:g} seconds") from None
        if done.returncode not in ok:
            said = " ".join((done.stderr or "").split())[:200]
            raise GitError(argv, f"exited {done.returncode}" + (f": {said}" if said else ""))
        return done.returncode, done.stdout or ""

    def _lines(self, *args: str, ok: tuple[int, ...] = (0,)) -> list[str]:
        return self._run(*args, ok=ok)[1].split()

    def branch(self, name: str) -> str | None:
        """The commit the local branch `name` points at, or None when there is no such
        branch."""
        return self._commit(f"refs/heads/{name}")

    def top(self) -> Path | None:
        """The top folder of the work tree the root is in, or None when the root is in
        no work tree (or is not a folder git can enter)."""
        code, out = self._run("rev-parse", "--show-toplevel", ok=(0, 128))
        return Path(out.strip()) if code == 0 and out.strip() else None

    def checked_out(self) -> str | None:
        """The branch checked out at the root, or None when its head is detached."""
        code, out = self._run("symbolic-ref", "--quiet", "HEAD", ok=(0, 1))
        ref = out.strip()
        if code != 0 or not ref.startswith("refs/heads/"):
            return None
        return ref[len("refs/heads/"):]

    def has_changes(self) -> bool:
        """Whether the work tree at the root holds anything not committed: a change to a
        tracked file, staged or not, or a file git neither tracks nor ignores. git takes
        no optional lock for it, so the read leaves the repo's index as it was."""
        _, out = self._run("--no-optional-locks", "status", "--porcelain",
                           "--untracked-files=all")
        return bool(out.strip())

    def is_branch_name(self, name: str) -> bool:
        """Whether git takes `name` as the name of a branch."""
        if not name or name.startswith("-"):
            return False
        code, _out = self._run("check-ref-format", "--branch", name, ok=(0, 1, 128))
        return code == 0

    def _commit(self, ref: str) -> str | None:
        code, out = self._run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
                              ok=(0, 1))
        return out.strip() if code == 0 and out.strip() else None

    def commit_of(self, prefix: str) -> str | None:
        """The whole id of the one commit whose id starts with the hex `prefix`, or None
        when git holds none, or more than one, by it."""
        if not re.fullmatch(r"[0-9a-fA-F]{4,64}", prefix or ""):
            return None
        return self._commit(prefix.lower())

    def present(self, commits: Iterable[str]) -> tuple[str, ...]:
        """The commits of `commits` that git has, in the order given, each once."""
        wanted = list(dict.fromkeys(one for one in commits if is_id(one)))
        if not wanted:
            return ()
        _, out = self._run("cat-file", "--batch-check",
                           stdin_data="".join(f"{one}^{{commit}}\n" for one in wanted))
        have = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1] == "commit":
                have.add(parts[0])
        return tuple(one for one in wanted if one in have)

    def tree(self, commit: str) -> str:
        """The tree of `commit`."""
        return self._run("rev-parse", "--verify", f"{commit}^{{tree}}")[1].strip()

    def committed_at(self, commit: str) -> int:
        """When `commit` was committed: its committer date, in seconds since the epoch."""
        # The raw commit object, not `show`: a user's config can make `show` print a
        # signature check before the date, and nothing decorates `cat-file`.
        argv = ["git", "-C", str(self.root), "cat-file", "commit", commit]
        out = self._run("cat-file", "commit", commit)[1]
        for line in out.split("\n"):
            if not line:
                break
            if line.startswith("committer "):
                stamp = line.rsplit(" ", 2)[-2:]
                if len(stamp) == 2 and stamp[0].isdigit():
                    return int(stamp[0])
                break
        raise GitError(argv, "gave a commit with no committer date git could read")

    def parents(self, commit: str) -> tuple[str, ...]:
        """The parents of `commit`, the first first."""
        return tuple(self._lines("rev-list", "--parents", "-n", "1", commit)[1:])

    def is_ancestor(self, commit: str, of: str) -> bool:
        """Whether `commit` is `of` or an ancestor of it."""
        code, _ = self._run("merge-base", "--is-ancestor", commit, of, ok=(0, 1))
        return code == 0

    def merge_bases(self, one: str, other: str) -> tuple[str, ...]:
        """Every merge base of two commits: none for unrelated histories, more than one
        for a criss-cross."""
        return tuple(self._lines("merge-base", "--all", one, other, ok=(0, 1)))

    def newest(self, commits: Iterable[str]) -> str | None:
        """The one commit of `commits` that has all the others as ancestors, or None
        when no one has: they lie on lines that parted. `commits` are commits git has."""
        wanted = list(dict.fromkeys(commits))
        if len(wanted) == 1:
            return wanted[0]
        tips = self._lines("merge-base", "--independent", *wanted) if wanted else []
        return tips[0] if len(tips) == 1 else None

    def bringing_merge(self, commit: str, head: str) -> str | None:
        """The merge that brought `commit` onto the first-parent line from `head`: the
        oldest commit of that line that has `commit` as an ancestor. None when `commit`
        is on that line itself. `commit` is `head` or an ancestor of it."""
        # `--first-parent` with `--ancestry-path` drops a merge whose path to the commit
        # leaves the first-parent line before its last step, so the line and the
        # commit's descendants are read apart and met here.
        descendants = set(self._lines("rev-list", "--ancestry-path", f"{commit}..{head}"))
        oldest = None
        for one in self._lines("rev-list", "--first-parent", f"{commit}..{head}"):
            if one not in descendants:
                break
            oldest = one
        if oldest is None:
            return None
        return None if self.parents(oldest)[:1] == (commit,) else oldest

    def between(self, start: str, end: str, *, first_parent: bool = False,
                nor: Iterable[str] = ()) -> list[str]:
        """The commits reachable from `end` and not from `start`, newest first; with
        `first_parent`, only those on the first-parent line from `end`; with `nor`, not
        from any of those commits either."""
        return self._lines("rev-list", *(["--first-parent"] if first_parent else []),
                           f"{start}..{end}", *(f"^{one}" for one in nor))

    def changed_paths(self, start: str, end: str) -> frozenset[str]:
        """The paths whose content differs between the trees of two commits. A renamed
        file is a change to both its names, and a path is read whatever bytes it holds."""
        # diff-tree is plumbing: no diff setting of the user's renames or hides a path.
        out = self._run("diff-tree", "-r", "--name-only", "--no-renames", "-z", start,
                        end)[1]
        return frozenset(one for one in out.split("\0") if one)

    def holds_non_merge(self, start: str, end: str) -> bool:
        """Whether a commit that is not a merge is reachable from `end` and not from
        `start`."""
        return bool(self._lines("rev-list", "--no-merges", "-n", "1", f"{start}..{end}"))


__all__ = ["GIT_TIMEOUT_S", "GitError", "Git", "git_env", "is_id"]
