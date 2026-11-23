"""A project's private files: the files its `private_files` setting lists, which the
repo neither tracks nor shows, copied into each new build worktree and kept out of the
target.

**What a commission reads, before it makes anything** (`unmet`), for each entry `P`:
`<root>/P` is a file (a link to a file counts); `P` is not held by the tree of the commit
the worktree checks out (`held`, read on that one commit); and git at the root reads
`P` as ignored.

**What it does, right after the worktree** (`bring`): git in the worktree must read
every entry as ignored, or nothing is copied. Then each source is copied to its place
in the worktree, never linked, so an edit there never reaches the project's own file. A
file already at a place is kept only when it is a regular file with the source's bytes;
anything else stops the commission instead of writing over it. Each copy's
`sha256` is recorded, so the removal rule can tell a copy nobody changed
(`is_unchanged`).

**What finishing may bring** (`held`): no commit reachable from the build's tip and not
from the target's head, walked over every parent of every merge, may hold in its tree a
private path, a path under one, or an entry that is not a folder where a folder a
private path lies under would be. Components compare exactly, or by `casefold` on both
sides where the repo's `core.ignorecase` is true. One `git cat-file --batch` process
reads only the trees on the private paths' folders, each tree object once, so the cost
follows the number of commits and the depth of the paths, not the size of the repo. No
path is passed to git as a pathspec.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import places
from . import settings as own
from .errors import RoundsError, Unmet
from .gitreads import GIT_TIMEOUT_S, Git, GitError, git_env

#: The mode of a folder entry in a git tree object.
_TREE_MODE = b"40000"
#: How many commit ids the words name before they give the count.
NAMED_COMMITS = 5


@dataclass(frozen=True)
class Held:
    """A private `path` some commit holds: the tree `entry` that matched it, and the
    `commits` that hold it, oldest first. `in_target` is true when the target's head
    holds it too, so every build inherits it."""

    path: str
    entry: str
    commits: tuple[str, ...]
    in_target: bool = False


def listed(settings, repo: str) -> list[str]:
    """The entries of the repo's `private_files`, as the setting stands now."""
    return list(own.build_setting(settings, repo, "private_files"))


def private_paths(settings, repo: str, brought: object = ()) -> list[str]:
    """A build's private paths: each `path` of its event's `brought`, then each entry of
    the repo's `private_files` now, each once."""
    found = [one["path"] for one in _entries(brought)]
    found += listed(settings, repo)
    return list(dict.fromkeys(found))


def _entries(brought: object) -> list[dict]:
    """The entries of an event's `brought` that name a path, as anyone may have typed
    them."""
    if not isinstance(brought, list):
        return []
    return [one for one in brought
            if isinstance(one, dict) and isinstance(one.get("path"), str) and one["path"]]


# -- the tree read ------------------------------------------------------------------------

class Trees:
    """Tree objects of the repo at `root`, read on demand by one `git cat-file --batch`
    process, each object once by id. `read` counts the objects read. Use it in a `with`
    block, which ends the process."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.read = 0
        self._cache: dict[bytes, list[tuple[bytes, bytes, bytes]]] = {}
        self._proc: subprocess.Popen | None = None
        self._timer: threading.Timer | None = None
        self._id_size = 0

    def __enter__(self) -> Trees:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if self._timer is not None:
            self._timer.cancel()
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=GIT_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait()
        finally:
            proc.stdout.close()

    def _start(self) -> subprocess.Popen:
        if self._proc is None:
            fmt = Git(self.root)._run("rev-parse", "--show-object-format")[1].strip()
            self._id_size = 32 if fmt == "sha256" else 20
            argv = ["git", "-C", str(self.root), "cat-file", "--batch"]
            try:
                self._proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                              stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, env=git_env())
            except OSError as exc:
                raise GitError(argv, f"could not run: {exc}") from None
            # One read of a repo's own objects answers at once; a process that hangs is
            # ended, and the read that waits on it fails.
            self._timer = threading.Timer(GIT_TIMEOUT_S * 5, self._proc.kill)
            self._timer.daemon = True
            self._timer.start()
        return self._proc

    def entries(self, tree: bytes) -> list[tuple[bytes, bytes, bytes]]:
        """The entries of the tree object `tree` (a hex id, as bytes): each one's mode,
        name and object id (hex, as bytes)."""
        if tree in self._cache:
            return self._cache[tree]
        proc = self._start()
        argv = ["git", "-C", str(self.root), "cat-file", "--batch"]
        try:
            proc.stdin.write(tree + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline()
            parts = header.split()
            if len(parts) != 3 or parts[1] != b"tree":
                raise GitError(argv, f"gave no tree for {tree.decode()}")
            size = int(parts[2])
            body = proc.stdout.read(size + 1)[:size]
        except (OSError, ValueError) as exc:
            raise GitError(argv, f"stopped answering: {exc}") from None
        if len(body) != size:
            raise GitError(argv, f"stopped answering in the tree {tree.decode()}")
        self.read += 1
        found = []
        at = 0
        while at < len(body):
            space = body.index(b" ", at)
            nul = body.index(b"\0", space)
            oid = body[nul + 1:nul + 1 + self._id_size]
            found.append((body[at:space], body[space + 1:nul], oid.hex().encode()))
            at = nul + 1 + self._id_size
        self._cache[tree] = found
        return found


def ignores_case(git: Git) -> bool:
    """Whether the repo's `core.ignorecase` is true."""
    code, out = git._run("config", "--bool", "core.ignorecase", ok=(0, 1))
    return code == 0 and out.strip() == "true"


def _matches(trees: Trees, tree: bytes, parts: Sequence[str], *,
             casefold: bool) -> list[str]:
    """The entries of `tree` that match the private path `parts`, each as a path."""
    want = parts[0]
    found = []
    for mode, name, oid in trees.entries(tree):
        text = name.decode("utf-8", "surrogateescape")
        same = (text.casefold() == want.casefold()) if casefold else text == want
        if not same:
            continue
        if len(parts) == 1 or mode != _TREE_MODE:
            found.append(text)
        else:
            found += [f"{text}/{rest}"
                      for rest in _matches(trees, oid, parts[1:], casefold=casefold)]
    return found


def _holds(trees: Trees, tree: str, paths: Sequence[str], *,
           casefold: bool) -> dict[str, str]:
    """Each private path of `paths` the tree `tree` holds, with the entry that matched."""
    found = {}
    for path in paths:
        hits = _matches(trees, tree.encode(), path.split("/"), casefold=casefold)
        if hits:
            found[path] = hits[0]
    return found


def held(git: Git, paths: Sequence[str], tip: str, head: str | None, *,
         trees: Trees | None = None) -> list[Held]:
    """Each private path of `paths` some commit holds that finishing would bring: every
    commit reachable from `tip` and not from `head`, oldest first, walked over every
    parent with no simplification. `head` None reads the one commit `tip`. `trees`, when
    given, is the reader used, and stays open."""
    if not paths:
        return []
    if head is None:
        out = git._run("rev-list", "--no-walk", "--format=%T", tip)[1]
    else:
        out = git._run("rev-list", "--reverse", "--format=%T", tip, "--not", head)[1]
    words = out.split()
    commits = [(words[at + 1], words[at + 2]) for at in range(0, len(words) - 2, 3)
               if words[at] == "commit"]
    if not commits:
        return []
    casefold = ignores_case(git)
    reader = Trees(git.root) if trees is None else trees
    try:
        by_path: dict[str, tuple[str, list[str]]] = {}
        for commit, tree in commits:
            for path, entry in _holds(reader, tree, paths, casefold=casefold).items():
                by_path.setdefault(path, (entry, []))[1].append(commit)
        if not by_path:
            return []
        at_head = {}
        if head is not None:
            head_tree = git.tree(head)
            at_head = _holds(reader, head_tree, list(by_path), casefold=casefold)
    finally:
        if trees is None:
            reader.close()
    return [Held(path=path, entry=entry, commits=tuple(found), in_target=path in at_head)
            for path in paths if path in by_path
            for entry, found in [by_path[path]]]


def words(found: Sequence[Held], *, repo: str, target: str,
          round_name: str) -> tuple[str, str]:
    """What gate 11 says of `found`, as a reason and its fix."""
    said = []
    for one in found:
        named = ", ".join(commit[:12] for commit in one.commits[:NAMED_COMMITS])
        if len(one.commits) > NAMED_COMMITS:
            named += f" and {len(one.commits) - NAMED_COMMITS} more, {len(one.commits)} in all"
        what = one.path if one.entry == one.path else f"{one.path} (as {one.entry})"
        if one.in_target:
            said.append(f"{what} is a private file of {repo} (brought by commission, "
                        f"or listed in private_files), and {target} holds it, so every "
                        f"commit finish would bring holds it: {named}")
        else:
            said.append(f"{what} is a private file of {repo} (brought by commission, "
                        f"or listed in private_files), and commits finish would bring "
                        f"into {target} hold it: {named}")
    reason = "; ".join(said)
    fixes = []
    inherited = [one.path for one in found if one.in_target]
    if inherited:
        fixes.append(f"take {', '.join(inherited)} out of private_files in "
                     f"[projects.{repo}], or out of {target}")
    own_paths = [one.path for one in found if not one.in_target]
    if own_paths:
        first = own_paths[0]
        fixes.append(
            f"make a branch none of whose commits holds {first}. One way: git rm "
            f"--cached -- {first} if the tip holds it, then git reset --soft $(git "
            f"merge-base {target} HEAD), then git commit, which leaves one commit with "
            "the work. This rewrites the branch: its tip changes, so the build "
            "verification, the check and any report step read at the old tip are owed "
            "again; and a commit it shared with another build becomes a copy of it, so "
            "finish that build first")
    return reason, "; and ".join(fixes) + f". Then: moxi rounds next {round_name}"


# -- what a commission reads --------------------------------------------------------------

def exclude_file(git: Git) -> Path:
    """The repo's exclude file, shared by all its worktrees."""
    out = git._run("rev-parse", "--git-path", "info/exclude")[1].strip()
    path = Path(out)
    return path if path.is_absolute() else git.root / path


def is_ignored(git: Git, path: str) -> bool:
    """Whether git at its root reads `path` as ignored."""
    code, _out = git._run("check-ignore", "-q", "--", path, ok=(0, 1))
    return code == 0


def unmet(git: Git, entries: Sequence[str], commit: str, *, repo: str,
          again: str) -> list[Unmet]:
    """What refuses a commission that would bring `entries` from the repo at `git`'s
    root into a worktree that checks out `commit`: a source that is not a file, an entry
    the commit's tree holds, an entry git at the root does not ignore."""
    found: list[Unmet] = []
    if not entries:
        return found
    root = git.root
    tracked = {one.path: one for one in held(git, entries, commit, None)}
    exclude = None
    for path in entries:
        source = root / path
        try:
            is_file = stat.S_ISREG(os.stat(source).st_mode)
        except OSError:
            found.append(Unmet(
                f"the private file {path} of {repo} is not at {source}, and commission "
                "copies each one listed into the worktree",
                f"put the file at {source}, or take {path} out of private_files in "
                f"[projects.{repo}]; then: {again}"))
            continue
        if not is_file:
            found.append(Unmet(
                f"the private file {path} of {repo} is not a file at {source}, and "
                "only files are copied into a worktree",
                f"list each file in it in private_files in [projects.{repo}] in place "
                f"of {path}; then: {again}"))
            continue
        if path in tracked:
            found.append(Unmet(
                f"the private file {path} of {repo} is tracked, as "
                f"{tracked[path].entry}, at {commit[:12]}, the commit the worktree "
                "checks out, so it is the repo's and copying it would change it",
                f"take {path} out of private_files in [projects.{repo}]; then: {again}"))
            continue
        if not is_ignored(git, path):
            exclude = exclude or exclude_file(git)
            found.append(Unmet(
                f"git at {root} does not read the private file {path} of {repo} as "
                "ignored, so in a worktree it would read as untracked, one git add -A "
                "away from a commit",
                f"add the line {path} to {exclude}; then: {again}"))
    return found


def doctor_warnings(git: Git, entries: Sequence[str]) -> list[str]:
    """For the doctor: each entry missing at the root, not a file there, tracked at the
    root's `HEAD`, or not read as ignored by git at the root, each with its fix."""
    found = []
    root = git.root
    head = git._commit("HEAD")
    tracked = {} if head is None else {one.path: one for one in held(git, entries,
                                                                     head, None)}
    for path in entries:
        source = root / path
        try:
            is_file = stat.S_ISREG(os.stat(source).st_mode)
        except OSError:
            found.append(f"{path} is not at {source}: put it there, or take it out of "
                         "private_files")
            continue
        if not is_file:
            found.append(f"{path} is not a file: list the files in it in its place")
        elif path in tracked:
            found.append(f"{path} is tracked at HEAD: take it out of private_files")
        elif not is_ignored(git, path):
            found.append(f"{path} is not ignored: add the line {path} to "
                         f"{exclude_file(git)}")
    return found


# -- the act ------------------------------------------------------------------------------

def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


@dataclass(frozen=True)
class Negation:
    """A `!` `pattern` that un-ignores a path: the file it is in (`source`, as git names
    it) and its `line`."""

    source: str
    line: str
    pattern: str

    def __str__(self) -> str:
        return f"{self.source}:{self.line}:{self.pattern}"

    @property
    def in_tree(self) -> bool:
        """Whether it is in a `.gitignore` of the work tree, which git names by a path
        relative to it; an exclude file is named by its own path."""
        source = Path(self.source)
        return not source.is_absolute() and source.name == ".gitignore"


def negation_of(said: str) -> Negation | None:
    """The negation `git check-ignore -v -z` reports in `said`, its fields each ended by
    a NUL, or None when it reports no pattern or one that is not a negation. The fields
    are read by the NUL alone, so a colon in the file's path (a drive letter) or in the
    pattern is read as text."""
    fields = said.split("\0")
    if len(fields) < 3 or not fields[0] or not fields[2].startswith("!"):
        return None
    return Negation(source=fields[0], line=fields[1], pattern=fields[2])


def _negation(worktree: Path, path: str) -> Negation | None:
    """The `!` pattern that un-ignores `path` in the worktree, or None when no pattern
    matches it."""
    code, out = Git(worktree)._run("check-ignore", "-v", "-z", "--no-index", "--stdin",
                                   ok=(0, 1), stdin_data=path + "\0")
    return negation_of(out) if code == 0 else None


def bring(root: Path, worktree: Path, entries: Sequence[str], *, branch: str,
          is_new_branch: bool, again: str) -> list[dict]:
    """Copy each private file of `entries` from the repo at `root` into `worktree`, and
    give each one's `{"path", "from", "sha256"}`. Raises `RoundsError` with nothing
    copied when git in the worktree does not read every entry as ignored, and when a
    place holds anything but a regular file with the source's bytes; and on any failure
    of the copy, naming the entry."""
    if not entries:
        return []
    worktree_git = Git(worktree)
    for path in entries:
        code, _out = worktree_git._run("check-ignore", "-q", "--", path, ok=(0, 1))
        if code == 0:
            continue
        negation = _negation(worktree, path)
        if negation is None or not negation.in_tree:
            exclude = exclude_file(Git(root))
        if negation is None:
            raise RoundsError(
                f"git in the worktree {worktree} does not read the private file {path} "
                "as ignored: no pattern there matches it, so nothing was copied",
                f"add the line {path} to {exclude}, then: {again}")
        if not negation.in_tree:
            raise RoundsError(
                f"git in the worktree {worktree} does not read the private file {path} as "
                f"ignored: {negation} un-ignores it, and no pattern of the commit checked "
                "out ignores it, so nothing was copied",
                f"remove the line {negation.pattern} from {negation.source}, and add the "
                f"line {path} to {exclude} if it is not there; then: {again}")
        undo = f"git -C {root} worktree remove {worktree}"
        if is_new_branch:
            undo += f", then git -C {root} branch -D {branch}"
        raise RoundsError(
            f"git in the worktree {worktree} does not read the private file {path} as "
            f"ignored: {negation} un-ignores it at the commit checked out, and no "
            "exclude entry wins over it, so nothing was copied",
            f"remove the worktree and the branch commission made ({undo}), and fix "
            f"that .gitignore on the target or the branch, or take {path} out of "
            f"private_files; then: {again}")
    todo = []
    for path in entries:
        source, place = root / path, worktree / path
        try:
            if not os.path.lexists(place):
                todo.append(path)
                continue
            if _is_regular(place) and _hash(place) == _hash(source):
                continue
        except OSError as exc:
            raise RoundsError(f"the private file {path} can't be read: {exc}",
                              f"look at {source} and {place}, then: {again}") from None
        raise RoundsError(f"{place} is there and is not a copy of {source} as it is now, "
                          "and commission stops instead of writing over it",
                          f"look at {place}, remove it, then: {again}")
    copied = []
    for path in entries:
        source, place = root / path, worktree / path
        if path in todo:
            try:
                place.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, place)
                shutil.copymode(source, place)
            except OSError as exc:
                raise RoundsError(f"the private file {path} can't be copied from {source} "
                                  f"to {place}: {exc}",
                                  f"look at {source} and {place}, then: {again}") from None
        try:
            digest = _hash(place)
        except OSError as exc:
            raise RoundsError(f"the copy {place} can't be read: {exc}",
                              f"look at {place}, then: {again}") from None
        copied.append({"path": path, "from": places.contract_home(source),
                       "sha256": digest})
    return copied


# -- the removal rule ---------------------------------------------------------------------

def is_unchanged(place: Path, path: str, brought: object) -> bool:
    """Whether the ignored `path` of the worktree at `place` is a brought file nobody
    changed: a regular file whose bytes hash to the recorded `sha256` or equal the
    source's bytes now."""
    for one in _entries(brought):
        if one["path"] != path:
            continue
        copy = place / path
        if not _is_regular(copy):
            return False
        try:
            digest = _hash(copy)
        except OSError:
            return False
        if digest == one.get("sha256"):
            return True
        source = one.get("from")
        if not isinstance(source, str):
            return False
        try:
            return digest == _hash(Path(places.expand_home(source)))
        except OSError:
            return False
    return False


__all__ = ["NAMED_COMMITS", "Held", "Trees", "listed", "private_paths", "held", "words",
           "ignores_case", "exclude_file", "is_ignored", "unmet", "doctor_warnings",
           "Negation", "negation_of", "bring", "is_unchanged"]
