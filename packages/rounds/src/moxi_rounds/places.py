"""Where a build's two folders go, and the marker each one carries.

`commission` makes two folders for a build: its worktree, at
`<worktree_dir>/<repo>/<branch>`, and its scratch folder, at
`<cache dir>/scratch/<repo>/<branch>/<build id>`. The build id is 16 random hex digits
drawn at `commission`, so no two builds ever share a scratch folder.

**Names in a path.** A branch is percent-encoded wherever a path or a ref is built from
it (`encode`): `/` as `%2F` and `%` as `%25`. So `release` and `release/1.0` never nest
or collide, and the encoding reads back to one name.

**The marker.** Each folder carries one JSON line naming the build id, the round, stage,
repo and branch, and the UTC time it was written: `.rounds-build`, the first thing in
the scratch folder, and `rounds-build` in the worktree's own git folder, which
`git status` never lists and `git worktree remove` deletes. A marker is what tells a
folder rounds made from one it did not: a folder with no marker, or one naming another
build, is never taken for a build's own.

**The host** is this machine's name, recorded with the build that made folders here.

**A path in a record.** A path rounds writes into a round's records is written `~` when
it is the home folder and `~/<rest>` when it is under it, the rest with the separators
the path has (`contract_home`); any other path is written as it is. A path read from a
record is expanded before rounds acts on it (`expand_home`): a `~`-led path names the
home folder, and every other path, a relative one included, is read as it is written.
A record that holds the whole path reads the same as one that holds the `~` form.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from moxi import files

from . import log, paths

#: The folder of the cache directory that holds every build's scratch folder.
SCRATCH_DIR = "scratch"
#: The marker's name in a scratch folder.
SCRATCH_MARKER = ".rounds-build"
#: The marker's name in a worktree's own git folder.
WORKTREE_MARKER = "rounds-build"

#: A build id: 16 hex digits.
_BUILD_ID = re.compile(r"[0-9a-f]{16}")
#: The fields a marker holds, in the order written.
_FIELDS = ("build", "round", "stage", "repo", "branch", "at")


def encode(name: str) -> str:
    """`name`, a branch or target, as one part of a path or a ref: `%` as `%25` and `/` as
    `%2F`, in that order, so that what is encoded reads back to one name."""
    return name.replace("%", "%25").replace("/", "%2F")


def new_build_id() -> str:
    """A new build id: 16 random hex digits."""
    return secrets.token_hex(8)


def is_build_id(value: object) -> bool:
    """Whether `value` is written as a build id."""
    return isinstance(value, str) and _BUILD_ID.fullmatch(value) is not None


def host() -> str:
    """This machine's name, as a build that made folders here records it."""
    return socket.gethostname()


def made_here(commissioned: Mapping, *, unrecorded_is_here: bool) -> bool:
    """Whether the folders a build's commission record names were made on this machine,
    by the `host` the record holds. A record with no host (only a hand-typed one has
    none) is read as `unrecorded_is_here` says: a read or a run there may take it as
    here, but a removal may not, since what it can't prove is not removed."""
    recorded = commissioned.get("host")
    if recorded is None:
        return unrecorded_is_here
    return recorded == host()


def expand_home(recorded: object) -> str:
    """The path `recorded`, as a round's record holds it, as it names a place on this
    machine: a `~`-led path with the home folder in place of `~`, any other as written."""
    return os.path.expanduser(str(recorded))


def contract_home(path: str | os.PathLike) -> str:
    """`path` as a round's record holds it: `~` for the home folder, `~` followed by the
    rest for a path under it, any other path as it is. A path that only starts with the
    home folder's name, as `/home/ann2` does with `/home/ann`, is not under it."""
    text = os.fspath(path)
    seps = os.sep + (os.altsep or "")
    home = os.path.expanduser("~").rstrip(seps)
    if not home or not os.path.isabs(home):
        return text
    if text == home:
        return "~"
    if text.startswith(home) and text[len(home)] in seps:
        return "~" + text[len(home):]
    return text


def worktree_dir(settings, env=None) -> Path:
    """Where build worktrees go: the `worktree_dir` setting, else the folder in rounds'
    data directory."""
    chosen = settings["worktree_dir"]
    return paths.default_worktree_dir(env) if chosen is None else Path(chosen)


def scratch_root(env=None) -> Path:
    """The folder that holds every build's scratch folder, in rounds' cache directory."""
    return paths.cache_dir(env) / SCRATCH_DIR


def worktree_place(settings, repo: str, branch: str, env=None) -> Path:
    """Where the worktree of a build of `repo` on `branch` goes."""
    return worktree_dir(settings, env) / repo / encode(branch)


def scratch_place(repo: str, branch: str, build_id: str, env=None) -> Path:
    """Where the scratch folder of the build `build_id` of `repo` on `branch` goes."""
    return scratch_root(env) / repo / encode(branch) / build_id


@dataclass(frozen=True)
class Marker:
    """What a marker names: the `build` id, and the `round`, `stage`, `repo` and
    `branch` of that build, with the time `at` it was written."""

    build: str
    round: str
    stage: str
    repo: str
    branch: str
    at: str

    def names(self, *, round: str, stage: str, repo: str, branch: str) -> bool:  # noqa: A002 -- round is the marker file's field name
        """Whether the marker names this round, stage, repo and branch."""
        return (self.round, self.stage, self.repo, self.branch) == (round, stage, repo,
                                                                    branch)

    def line(self) -> str:
        return json.dumps({name: getattr(self, name) for name in _FIELDS},
                          ensure_ascii=False) + "\n"


def marker(build: str, *, round: str, stage: str, repo: str, branch: str,  # noqa: A002 -- round is the marker file's field name
           at: str | None = None) -> Marker:
    """The marker of a build, written now unless `at` says when."""
    return Marker(build=build, round=round, stage=stage, repo=repo, branch=branch,
                  at=log.stamp() if at is None else at)


def read_marker(path: Path) -> Marker | None:
    """The marker in the file `path`, or None when there is no such regular file or it
    holds no marker: a link is not read, and neither is a line that is not one JSON
    object with each field a string and a build id that is 16 hex digits."""
    try:
        if not path.is_file() or path.is_symlink():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not all(isinstance(data.get(name), str)
                                             for name in _FIELDS):
        return None
    if not is_build_id(data["build"]):
        return None
    return Marker(**{name: data[name] for name in _FIELDS})


def write_marker(path: Path, found: Marker) -> None:
    """Write `found` as the new file `path`, whole and synced; `FileExistsError` when
    something is there. A stop part-way leaves no marker, so the folder reads as one
    rounds can't tell it made."""
    if os.path.lexists(path):
        raise FileExistsError(path)
    files.atomic_write(path, found.line(), mode=0o644)


def worktree_git_dir(place: Path) -> Path | None:
    """The worktree's own git folder, as the `gitdir:` line of the `.git` file at
    `place` names it; None when `place` holds no such file. A relative line is read
    from `place`. No git runs at `place`: git run in a folder reads its config."""
    dot_git = place / ".git"
    try:
        if not dot_git.is_file() or dot_git.is_symlink():
            return None
        text = dot_git.read_text(encoding="utf-8")
    except OSError:
        return None
    first = text.splitlines()[0] if text else ""
    if not first.startswith("gitdir:"):
        return None
    named = Path(first[len("gitdir:"):].strip())
    return named if named.is_absolute() else Path(os.path.normpath(place / named))


def worktree_marker(place: Path) -> Marker | None:
    """The marker of the worktree at `place`, or None when it has none."""
    git_dir = worktree_git_dir(place)
    return None if git_dir is None else read_marker(git_dir / WORKTREE_MARKER)


__all__ = ["SCRATCH_DIR", "SCRATCH_MARKER", "WORKTREE_MARKER", "Marker", "encode",
           "new_build_id", "is_build_id", "host", "expand_home", "contract_home",
           "worktree_dir", "scratch_root", "worktree_place", "scratch_place", "marker",
           "read_marker", "write_marker", "worktree_git_dir", "worktree_marker"]
