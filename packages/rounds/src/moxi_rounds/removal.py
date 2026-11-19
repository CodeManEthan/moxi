"""The removal rule: when rounds may remove a folder it made for a build.

Every removal of a build's worktree or scratch folder obeys both halves of one rule, read
at the moment it removes. `judge` reads them for one folder and gives the folder
`Proven`, or `Kept` naming the check it failed; `remove` judges, reads the second half
again just before its removal call, and removes. `finish` and `drop` use it for the
build they end, and what it keeps is left for a later sweep, once the rule holds.

**Ownership** (`A`): rounds removes only a folder it made, on this machine, for a build
that has ended. The caller ends the build; the folder and this machine's config prove
the rest, never the record alone, which only names a folder to look at:

- A1. The record was made on this machine (`host`).
- A2. The recorded path, a `~`-led one read as the path under the home folder
  (`places.expand_home`), is, as text, the place rounds builds for that build from the
  roots as config stands now (`places`): `<worktree_dir>/<repo>/<branch>` or
  `<scratch root>/<repo>/<branch>/<build id>`. The repo is a projects key, the branch
  has no `..`, `.` or empty part, no `\\` and no `:`, and the build id is 16 hex digits.
  A path under a root the config moved from is outside the roots.
- A3. Each root is resolved once to its real path; a link at or above it is followed.
  Below it each folder of the place is a real directory on the root's device: not a
  link, and on Windows no reparse point.
- A4. The folder holds the marker naming the record's build id, round, stage, repo and
  branch. For a worktree: git in the repo's root lists the place as a worktree on the
  recorded branch; its `.git` is a regular file whose `gitdir:` line names a folder
  directly under `<common dir>/worktrees/`, matched by device and file id; and the
  marker is in that folder. No git runs at the place before A holds, since git run in
  a folder reads its config there.

**No lost work** (`B`): a removal loses no byte that exists only in that folder, except
what is disposable: the whole scratch folder, and in a worktree the ignored paths the
repo's `disposable` globs name. What can't be read counts as work. For a worktree:

- B1. git runs with no `GIT_` variable and with the user's settings that would hide work
  overridden (`B1_CONFIG`); the status read is `B1_STATUS`.
- B2. `git ls-files -v` marks no path skip-worktree or assume-unchanged.
- B3. The read prints no tracked change, conflict, changed submodule or untracked path,
  and every ignored path has a part a `disposable` glob matches. The rest are work, and
  the account lists them.
- B4. The worktree's `HEAD` and each ref of its own (`refs/worktree/`, `refs/bisect/`,
  `refs/rewritten/`) name commits a branch, tag or remote ref of the repo contains, less
  a branch the same act deletes.
- B5. No submodule is checked out in it.
- B6. It is not locked with `git worktree lock`.

For both kinds:

- B7. A walk of the folder that follows no link finds no entry on another device than
  the place, no folder the system's mount table lists, and on Windows no junction or
  volume mount point.

**The removal.** A worktree goes by `git worktree remove` in the repo's root with B1's
settings and never `--force`, so git's own check refuses one that became dirty or
locked. A scratch folder is renamed into `<cache dir>/trash/<build id>` and deleted
there; a rename that fails, in use or across disks, keeps it. The delete follows no link
and leaves an entry on another device. No branch is deleted here, and nothing in git
changes but the removed worktree's own entry.

`checkout_removal` removes the checkout of the target a merge added, which has no marker
and so no owner proof: under B alone.

`changes` is B1's read of what a worktree holds that is not committed, which the finish
gate on a build's worktree reads too.
"""

from __future__ import annotations

import fnmatch
import os
import stat
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from . import folders, paths, places
from . import settings as own
from .errors import RoundsError
from .gitreads import Git, GitError

#: The two folders of a build.
WORKTREE = "worktree"
SCRATCH = "scratch"
#: The folder of the cache directory a scratch folder is renamed into before its delete.
TRASH_DIR = "trash"

#: The settings every git command that decides or makes a removal runs with, so git
#: reads the work as it stores it, not as the user's config shows it.
B1_CONFIG = ("-c", "status.showUntrackedFiles=all", "-c", "core.fsmonitor=false",
             "-c", "core.untrackedCache=false", "-c", "core.ignoreStat=false")
#: The status read of B1. Its flags override settings that would hide an untracked file
#: or a changed submodule.
B1_STATUS = ("--no-optional-locks", "status", "--porcelain=v1", "-z",
             "--untracked-files=all", "--ignored=matching", "--ignore-submodules=none")
#: The refs a worktree has of its own, deleted with it.
OWN_REFS = ("refs/worktree", "refs/bisect", "refs/rewritten")
#: The refs that stay when a worktree goes.
STAYING_REFS = ("refs/heads", "refs/tags", "refs/remotes")

_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


@dataclass(frozen=True)
class Kept:
    """A folder the rule keeps: its `place`, the `check` it failed (`A1` to `A4`, `B1`
    to `B7`, or `removal` when the removal call itself failed), `why` in words, for a
    worktree kept by B the paths that are `work`, and its `kind` when `removes` gives
    it."""

    place: Path
    check: str
    why: str
    work: tuple[str, ...] = ()
    kind: str = ""

    def line(self) -> str:
        """The account's words for it: the check, why, and the paths that are work."""
        listed = f" ({', '.join(self.work[:10])}{', ...' if len(self.work) > 10 else ''})"
        return f"{self.check}: {self.why}{listed if self.work else ''}"


@dataclass(frozen=True)
class Proven:
    """A folder both halves allow to go: its `kind`, its `place` below the real root,
    the repo `root` it is a worktree of (None for a scratch folder), the `disposable`
    globs read for it, the branch the same act deletes, and the record's `build` id."""

    kind: str
    place: Path
    root: Path | None
    disposable: tuple[str, ...]
    deletes: str | None
    build: str


@dataclass(frozen=True)
class Removed:
    """What `removes` did with a build's folders: those it `removed`, and those it
    `kept`."""

    removed: tuple[Path, ...] = ()
    kept: tuple[Kept, ...] = ()


def judge(name: str, commissioned: Mapping, kind: str, *, settings, env=None,
          deletes: str | None = None) -> Proven | Kept | None:
    """Read both halves of the rule for the `kind` folder (`WORKTREE` or `SCRATCH`) of
    the build `commissioned`, its `build.commissioned` event in the round `name`: `Proven`
    when it may go,
    `Kept` with the first check it fails, None when nothing is at its place. `deletes`
    is the branch the same act deletes, which B4 does not count as a ref that stays.
    The caller has ended the build."""
    owned = _owned(name, commissioned, kind, settings=settings, env=env)
    if not isinstance(owned, tuple):
        return owned
    place, root = owned
    disposable = tuple(own.build_setting(settings, str(commissioned["repo"]),
                                         "disposable"))
    proven = Proven(kind=kind, place=place, root=root, disposable=disposable,
                    deletes=deletes, build=str(commissioned["build"]))
    return _no_lost_work(proven) or proven


def remove(name: str, commissioned: Mapping, kind: str, *, settings, env=None,
           deletes: str | None = None, on_rename: Callable[[Path], None] | None = None,
           on_delete: Callable[[Path, bool], None] | None = None) -> Path | Kept | None:
    """Remove the `kind` folder of the build `commissioned` when `judge` proves it may
    go, reading B again just before the removal call: the place removed, `Kept` with
    the check that keeps it, or None when nothing is at its place. `on_rename` and
    `on_delete` are `take`'s."""
    found = judge(name, commissioned, kind, settings=settings, env=env, deletes=deletes)
    if not isinstance(found, Proven):
        return found
    # A file written since the first read keeps the folder.
    again = _no_lost_work(found)
    if again is not None:
        return again
    return take(found, env=env, on_rename=on_rename, on_delete=on_delete)


def removes(name: str, commissioned: Mapping, kinds: Iterable[str] = (WORKTREE, SCRATCH),
            *, settings, env=None, deletes: str | None = None,
            on_rename: Callable[[Path], None] | None = None,
            on_delete: Callable[[Path, bool], None] | None = None) -> Removed:
    """`remove` for each of `kinds` of the build `commissioned` of the round `name`, in
    that order."""
    removed, kept = [], []
    for kind in kinds:
        try:
            found = remove(name, commissioned, kind, settings=settings, env=env,
                           deletes=deletes, on_rename=on_rename, on_delete=on_delete)
        except RoundsError as exc:
            # What can't be read counts as work.
            found = Kept(Path(places.expand_home(commissioned.get(kind) or "")), "read",
                         exc.reason)
        if isinstance(found, Kept):
            kept.append(replace(found, kind=kind))
        elif found is not None:
            removed.append(found)
    return Removed(tuple(removed), tuple(kept))


def checkout_removal(root: Path, place: Path, *, settings, repo: str) -> Kept | None:
    """Remove the checkout of a target that a merge added at `place` in the repo at
    `root`, under B alone: it has no marker, so nothing proves who made it. `Kept` when
    B or the removal keeps it."""
    proven = Proven(kind=WORKTREE, place=Path(place), root=Path(root),
                    disposable=tuple(own.build_setting(settings, repo, "disposable")),
                    deletes=None, build="")
    try:
        found = _no_lost_work(proven)
        if found is not None:
            return found
        removed = take(proven)
    except RoundsError as exc:
        return Kept(Path(place), "B1", exc.reason)
    return removed if isinstance(removed, Kept) else None


def changes(place: Path) -> tuple[str, ...]:
    """B1's read of the worktree at `place`: each tracked change, conflict, changed
    submodule and untracked path, as git's status code and path. Ignored paths are left
    out. Raises `GitError` when git can't read it."""
    return tuple(f"{code} {path}" for code, path in _status(_B1Git(place))
                 if code != "!!")


# -- A: ownership ------------------------------------------------------------------------

def _owned(name: str, commissioned: Mapping, kind: str, *, settings, env):
    """The place below the real root and the repo's root when A2 to A4 hold for the
    folder; else `Kept`, or None when nothing is at its place."""
    if not commissioned.get(kind):
        return None
    recorded = Path(places.expand_home(commissioned[kind]))
    if not places.made_here(commissioned, unrecorded_is_here=False):
        made = commissioned.get("host")
        return Kept(recorded, "A1", "its record names no machine it was made on"
                    if made is None else f"it was made on {made}, not on this machine")
    repo, branch, build = (commissioned.get(key) for key in ("repo", "branch", "build"))
    if not isinstance(repo, str) or not isinstance(branch, str):
        return Kept(recorded, "A2", "its record names no repo and branch")
    if not places.is_build_id(build):
        return Kept(recorded, "A2", "its record carries no build id of 16 hex digits")
    if (branch in ("", ".", "..") or ".." in branch.split("/") or "\\" in branch
            or ":" in branch):
        return Kept(recorded, "A2", f"its branch {branch!r} is not one rounds builds a "
                    "place from")
    try:
        _key, root = folders.root(repo)
    except RoundsError:
        return Kept(recorded, "A2", f"its repo {repo!r} is not a projects key")
    if kind == WORKTREE:
        base = places.worktree_dir(settings, env)
        built = places.worktree_place(settings, repo, branch, env)
    else:
        base = places.scratch_root(env)
        built = places.scratch_place(repo, branch, build, env)
    if os.path.normcase(str(recorded)) != os.path.normcase(str(built)):
        return Kept(recorded, "A2", f"it is outside rounds' roots: rounds puts this "
                    f"build's {kind} at {built}")
    real = Path(os.path.realpath(base))
    place = real.joinpath(*built.relative_to(base).parts)
    found = _real_dirs(real, place)
    if found is not None:
        return None if found is _ABSENT else found
    names = {"round": name, "stage": str(commissioned.get("stage")), "repo": repo,
             "branch": branch, "build": build}
    try:
        marked = (_scratch_marked(place, names) if kind == SCRATCH
                  else _worktree_marked(place, Path(root), names))
    except GitError as exc:
        return Kept(place, "A4", f"git in the repo's root can't read it: {exc.reason}")
    if marked is not None:
        return marked
    return place, (Path(root) if kind == WORKTREE else None)


_ABSENT = object()


def _real_dirs(root: Path, place: Path):
    """A3: None when each folder from below `root` down to `place` is a real directory
    on the root's device; `_ABSENT` when one is not there; else `Kept`."""
    try:
        device = os.stat(root).st_dev
    except OSError:
        return _ABSENT
    at = root
    for part in place.relative_to(root).parts:
        at = at / part
        try:
            found = os.lstat(at)
        except FileNotFoundError:
            return _ABSENT
        except OSError as exc:
            return Kept(place, "A3", f"{at} can't be read: {exc.strerror}")
        if stat.S_ISLNK(found.st_mode) or _reparse(found):
            return Kept(place, "A3", f"{at} is a link, not a folder rounds made")
        if not stat.S_ISDIR(found.st_mode):
            return Kept(place, "A3", f"{at} is not a folder")
        if found.st_dev != device:
            return Kept(place, "A3", f"{at} is on another device than its root")
    return None


def _scratch_marked(place: Path, names: Mapping) -> Kept | None:
    """A4 for a scratch folder: its marker is a regular file naming the record's build."""
    found = places.read_marker(place / places.SCRATCH_MARKER)
    return None if _names(found, names) else _unmarked(place, found)


def _worktree_marked(place: Path, root: Path, names: Mapping) -> Kept | None:
    """A4 for a worktree: git lists it on the recorded branch, its `.git` file names a
    folder directly under the repo's `worktrees` folder, and the marker there names the
    record's build."""
    listed = _listed(root, place)
    if listed is None:
        return Kept(place, "A4", "git in the repo's root lists no worktree there")
    if listed.get("branch") != f"refs/heads/{names['branch']}":
        return Kept(place, "A4", f"the worktree there is not on {names['branch']}")
    dot_git = place / ".git"
    try:
        if not stat.S_ISREG(os.lstat(dot_git).st_mode):
            return Kept(place, "A4", "its .git is not a regular file")
    except OSError:
        return Kept(place, "A4", "it holds no .git file")
    git_dir = places.worktree_git_dir(place)
    common = Git(root)._run("rev-parse", "--git-common-dir")[1].strip()
    worktrees = (root / common) / "worktrees"
    if git_dir is None or not _same(git_dir.parent, worktrees):
        return Kept(place, "A4", "its .git file names no worktree folder of the repo")
    found = places.read_marker(git_dir / places.WORKTREE_MARKER)
    return None if _names(found, names) else _unmarked(place, found)


def _names(found: places.Marker | None, names: Mapping) -> bool:
    return (found is not None and found.build == names["build"]
            and found.names(round=names["round"], stage=names["stage"],
                            repo=names["repo"], branch=names["branch"]))


def _unmarked(place: Path, found: places.Marker | None) -> Kept:
    if found is None:
        return Kept(place, "A4", "unmarked: it holds no marker of a build, so rounds "
                    "can't tell it made it; look at it and remove it by hand")
    return Kept(place, "A4", f"its marker names the build {found.build} of "
                f"{found.round} {found.stage}, not this one")


def _same(one: Path, other: Path) -> bool:
    try:
        return os.path.samefile(one, other)
    except OSError:
        return False


def _listed(root: Path, place: Path) -> dict | None:
    """The entry of `git worktree list --porcelain` in the repo at `root` for the
    worktree at `place`, matched by device and file id: its lines by first word."""
    out = Git(root)._run("worktree", "list", "--porcelain")[1]
    for block in out.split("\n\n"):
        entry = {}
        for line in block.splitlines():
            word, _, rest = line.partition(" ")
            if word:
                entry[word] = rest
        if "worktree" in entry and _same(Path(entry["worktree"]), place):
            return entry
    return None


# -- B: no lost work ---------------------------------------------------------------------

class _B1Git(Git):
    """git at a folder with B1's settings: no `GIT_` variable (as `Git` runs it) and the
    user's settings that hide work overridden."""

    def _run(self, *args, **kwargs):
        return super()._run(*B1_CONFIG, *args, **kwargs)


def _no_lost_work(proven: Proven) -> Kept | None:
    """B for the folder `proven`: None when removing it loses nothing but what is
    disposable, else `Kept` with the first check that fails."""
    try:
        if proven.kind == WORKTREE:
            found = _worktree_work(proven)
            if found is not None:
                return found
        return _walk(proven.place)
    except GitError as exc:
        return Kept(proven.place, "B1", f"git can't read it: {exc.reason}")


def _worktree_work(proven: Proven) -> Kept | None:
    place, root = proven.place, proven.root
    here = _B1Git(place)
    _, out = here._run("ls-files", "-v", "-z")
    hidden = [entry[2:] for entry in out.split("\0")
              if entry and (entry[0] == "S" or entry[0].islower())]
    if hidden:
        return Kept(place, "B2", "git is told not to look at changes to some paths "
                    "(skip-worktree or assume-unchanged)", tuple(hidden))
    work = []
    for code, path in _status(here):
        if code != "!!":
            work.append(path)
        elif not _disposable(path, proven.disposable):
            work.append(path)
    if work:
        return Kept(place, "B3", "it holds work that is not committed: a change, an "
                    "untracked path, or an ignored path no disposable glob names",
                    tuple(work))
    lost = _unreachable(place, root, proven.deletes)
    if lost:
        return Kept(place, "B4", "a commit of its own is on no branch, tag or remote "
                    "ref that stays", tuple(one[:12] for one in lost))
    checked_out = _submodules(place)
    if checked_out:
        return Kept(place, "B5", "a submodule is checked out in it", checked_out)
    listed = _listed(root, place) or {}
    if "locked" in listed:
        return Kept(place, "B6", "it is locked with git worktree lock")
    return None


def _status(git: Git) -> list[tuple[str, str]]:
    """B1's status read: each entry's two-letter code and path."""
    fields = git._run(*B1_STATUS)[1].split("\0")
    found, skip = [], False
    for field in fields:
        if skip:
            skip = False
            continue
        if len(field) < 4:
            continue
        code, path = field[:2], field[3:]
        # A rename or copy is followed by the path it came from.
        skip = code[0] in "RC"
        found.append((code, path))
    return found


def _disposable(path: str, globs: tuple[str, ...]) -> bool:
    """Whether one part of the ignored `path` matches one of `globs`."""
    return any(fnmatch.fnmatchcase(part, glob)
               for part in path.rstrip("/").split("/") if part for glob in globs)


def _unreachable(place: Path, root: Path, deletes: str | None) -> list[str]:
    """The commits named by the worktree's `HEAD` and its own refs that no ref that
    stays contains."""
    here = _B1Git(place)
    named = []
    code, head = here._run("rev-parse", "--verify", "--quiet", "HEAD", ok=(0, 1))
    if code == 0 and head.strip():
        named.append(head.strip())
    named += here._lines("for-each-ref", "--format=%(objectname)", *OWN_REFS)
    gone = None if deletes is None else f"refs/heads/{deletes}"
    lost = []
    for commit in dict.fromkeys(named):
        holders = _B1Git(root)._lines("for-each-ref", "--contains", commit,
                                      "--format=%(refname)", *STAYING_REFS)
        if not [one for one in holders if one != gone]:
            lost.append(commit)
    return lost


def _submodules(place: Path) -> tuple[str, ...]:
    """The submodules checked out in the worktree at `place`: its git folder holds
    `modules`, or a submodule's path holds a `.git`."""
    found = []
    git_dir = places.worktree_git_dir(place)
    if git_dir is not None and os.path.lexists(git_dir / "modules"):
        found.append(str(git_dir / "modules"))
    out = _B1Git(place)._run("ls-files", "--stage", "-z")[1]
    for entry in out.split("\0"):
        info, _, path = entry.partition("\t")
        if info.startswith("160000 ") and os.path.lexists(place / path / ".git"):
            found.append(path)
    return tuple(found)


def _walk(place: Path) -> Kept | None:
    """B7: walk the folder at `place`, following no link: `Kept` for the first entry
    on another device, a folder the mount table lists, a reparse point on Windows, or
    an entry that can't be read."""
    try:
        device = os.lstat(place).st_dev
    except OSError as exc:
        return Kept(place, "B7", f"it can't be read: {exc.strerror}")
    mounted = mount_points(place)
    if mounted:
        return Kept(place, "B7", "another filesystem is mounted inside it",
                    tuple(mounted))
    dirs_left = [place]
    while dirs_left:
        walked_dir = dirs_left.pop()
        try:
            with os.scandir(walked_dir) as entries:
                listed = list(entries)
        except OSError as exc:
            return Kept(place, "B7", f"{walked_dir} can't be read: {exc.strerror}")
        for entry in listed:
            try:
                found = entry.stat(follow_symlinks=False)
            except OSError as exc:
                return Kept(place, "B7", f"{entry.path} can't be read: {exc.strerror}")
            if found.st_dev != device:
                return Kept(place, "B7", f"{entry.path} is on another device")
            if stat.S_ISDIR(found.st_mode) and _reparse(found):
                return Kept(place, "B7", f"{entry.path} is a junction or mount point")
            if stat.S_ISDIR(found.st_mode):
                dirs_left.append(Path(entry.path))
    return None


def mount_points(place: Path) -> list[str]:
    """The mount points the system lists strictly inside `place`. On Linux the mount
    table of this process; elsewhere what psutil reads, when it is installed."""
    inside = str(place).rstrip(os.sep) + os.sep
    return sorted(one for one in _mount_table() if one.startswith(inside))


def _mount_table() -> list[str]:
    table = Path("/proc/self/mountinfo")
    if table.is_file():
        found = []
        for line in table.read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split(" ")
            if len(fields) > 4:
                found.append(_unescape(fields[4]))
        return found
    try:
        import psutil
    except ImportError:
        return []
    try:
        return [one.mountpoint for one in psutil.disk_partitions(all=True)]
    except Exception:  # noqa: BLE001 -- a table that can't be read lists nothing; the device check stands
        return []


def _unescape(field: str) -> str:
    """A mount point as the mount table writes it, with `\\040` and the like read back."""
    out, at = [], 0
    while at < len(field):
        if field[at] == "\\" and field[at + 1:at + 4].isdigit():
            out.append(chr(int(field[at + 1:at + 4], 8)))
            at += 4
        else:
            out.append(field[at])
            at += 1
    return "".join(out)


def _reparse(found: os.stat_result) -> bool:
    return sys.platform == "win32" and bool(
        getattr(found, "st_file_attributes", 0) & _REPARSE_POINT)


# -- the removal -------------------------------------------------------------------------

def take(proven: Proven, *, env=None, on_rename: Callable[[Path], None] | None = None,
         on_delete: Callable[[Path, bool], None] | None = None) -> Path | Kept:
    """Remove the folder `proven`, which the caller has just judged: the place removed,
    or `Kept` when the removal itself fails. A scratch folder's trash entry is given to
    `on_rename` before the rename, and to `on_delete` after its delete with whether the
    entry is gone, so a log of them can tell a later run which entries are its own."""
    if proven.kind == WORKTREE:
        try:
            _B1Git(proven.root)._run("worktree", "remove", str(proven.place))
        except GitError as exc:
            return Kept(proven.place, "removal", f"git did not remove it: {exc.reason}")
        return proven.place
    trash = trash_dir(env)
    entry = trash / proven.build
    try:
        trash.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(entry):
            raise FileExistsError(entry)
        if on_rename is not None:
            on_rename(entry)
        os.rename(proven.place, entry)
    except OSError as exc:
        return Kept(proven.place, "removal", f"it could not be moved into {trash}: "
                    f"{exc.strerror or exc}")
    delete(entry)
    if on_delete is not None:
        on_delete(entry, not os.path.lexists(entry))
    return proven.place


def trash_dir(env=None) -> Path:
    """The folder a scratch folder is renamed into before its delete."""
    return paths.cache_dir(env) / TRASH_DIR


def delete(target_dir: Path) -> None:
    """Delete the folder at `target_dir`, following no link and leaving an entry on another
    device than the folder's, with the folders above it. What can't be deleted stays, and
    so does a `target_dir` that is a link."""
    try:
        found = os.lstat(target_dir)
    except OSError:
        return
    if not stat.S_ISDIR(found.st_mode) or _reparse(found):
        return
    _delete(target_dir, found.st_dev)


def _delete(target_dir: Path, device: int) -> None:
    """Delete the folder at `target_dir`, following no link and leaving an entry on another
    device than `device`, with the folders above it. What can't be deleted stays."""
    try:
        with os.scandir(target_dir) as entries:
            listed = list(entries)
    except OSError:
        return
    for entry in listed:
        try:
            found = entry.stat(follow_symlinks=False)
            if found.st_dev != device:
                continue
            if stat.S_ISDIR(found.st_mode) and not _reparse(found):
                _delete(Path(entry.path), device)
            else:
                os.unlink(entry.path)
        except OSError:
            continue
    try:
        os.rmdir(target_dir)
    except OSError:
        pass


__all__ = ["WORKTREE", "SCRATCH", "TRASH_DIR", "B1_CONFIG", "B1_STATUS", "OWN_REFS",
           "STAYING_REFS", "Kept", "Proven", "Removed", "judge", "remove", "removes",
           "checkout_removal", "changes", "mount_points", "take", "trash_dir", "delete"]
