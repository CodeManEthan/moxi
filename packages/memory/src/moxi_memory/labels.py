"""A session's project label, derived from its working directory.

The label is the name of the nearest folder at or above the cwd that holds `.git`. When
that `.git` is a file, as in a linked worktree, the label is the name of the main
repository's folder the file leads to, so a worktree's sessions share their project's
label. With no `.git` anywhere above, the label is the cwd's own name.

The walk is a few `stat` calls up the tree and one or two small reads, standard library
only, so a start listener can make it. It reads what is on disk now: a cwd that no
longer exists still gets a label from the folders above it that do, and from its own
name. `cwd_exists` tells a caller (a relabel) whether the cwd is still there.
"""

from __future__ import annotations

import os

#: The most bytes of a `.git` file or a `commondir` file read.
_READ_LIMIT = 4096


def project_label(cwd: str | os.PathLike | None) -> str | None:
    """The project label for a session whose working directory is `cwd`, or None when
    `cwd` is empty, relative or the filesystem's root."""
    if not cwd:
        return None
    path = os.path.normpath(os.fspath(cwd))
    if not os.path.isabs(path):
        return None
    search_dir = path
    while True:
        dot_git = os.path.join(search_dir, ".git")
        if os.path.isdir(dot_git):
            return os.path.basename(search_dir) or None
        if os.path.isfile(dot_git):
            return _linked_label(search_dir, dot_git) or os.path.basename(search_dir) or None
        parent = os.path.dirname(search_dir)
        if parent == search_dir:
            break
        search_dir = parent
    return os.path.basename(path) or None


def cwd_exists(cwd: str | os.PathLike | None) -> bool:
    """Whether `cwd` names a folder that exists on this machine now."""
    return bool(cwd) and os.path.isdir(os.fspath(cwd))


def _linked_label(work_dir: str, dot_git: str) -> str | None:
    """The main repository folder's name for a `.git` file, or None when the file doesn't
    lead to one (a submodule's, or one that can't be read)."""
    target = _read_line(dot_git)
    if not target or not target.startswith("gitdir:"):
        return None
    gitdir = target[len("gitdir:"):].strip()
    if not gitdir:
        return None
    gitdir = os.path.normpath(os.path.join(work_dir, gitdir))
    common = _read_line(os.path.join(gitdir, "commondir"))
    if not common:
        return None
    common = os.path.normpath(os.path.join(gitdir, common))
    name = os.path.basename(common)
    if name == ".git":
        return os.path.basename(os.path.dirname(common)) or None
    # A bare repository: its folder is the repository, often named `<name>.git`.
    return name.removesuffix(".git") or None


def _read_line(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_READ_LIMIT)
    except OSError:
        return None
    text = raw.decode("utf-8", "replace").splitlines()
    return text[0].strip() if text else None


__all__ = ["project_label", "cwd_exists"]
