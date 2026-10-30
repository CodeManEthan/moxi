"""The projects read API, interface 1: the list of named places, read in process.

A module that takes projects as optional reaches this through the core, `available()`
first and `load()` only on yes:

    found = (moxi.modules.load("projects", interface=1)
             if moxi.modules.available("projects") else None)
    if found is None or not found.ok:
        ...  # the feature is off
    project = found.api.get(key)

Four calls:

- `list_projects(kind=None)`: every project, by key, optionally of one kind.
- `get(key)`: one project, its key matched ignoring case; `NotFound` when there is none.
- `for_path(path)`: the project a directory is in, or None.
- `kinds()`: the kinds a project may have, built in and contributed, with every
  contribution that won't load named. It never raises.

A missing store is an answer, not an error: no projects, `NotFound`, None. A store this
code can't read raises `Unreadable` (with the reason and the fix); one written by a newer
version of projects raises `TooNew`, which is both an `Unreadable` and the core's
`moxi.store.StoreTooNew`; a store locked past the wait raises `Busy`. Every error carries
`exit_code`, the code a command exits with on it.

A read never writes: it never creates the store, never migrates it and never changes a
byte of it. Writes are not part of this API; projects' own verbs and page make them.

This module sits on hook paths, so it imports `os` and `sqlite3` and the core's store
helper, and nothing heavier: no `dataclasses`, `pathlib` or `typing` of its own.
"""

from __future__ import annotations

import os
import sqlite3

import moxi.store

#: The store's schema version this code writes.
_VERSION = 1
#: The schema versions this code reads as they are. A store at an older version than
#: these is refused until `moxi install projects` (or any write) migrates it.
_READS = frozenset({1})
#: The store's file name in projects' data directory.
_FILE = "projects.db"
#: The built-in kind every row takes unless told otherwise.
DEFAULT_KIND = "project"

_INSTALL = "run `moxi install projects`, which migrates the store"


# --- errors -----------------------------------------------------------------------------

class ProjectsError(Exception):
    """Something projects can't do as asked. `reason` says what, `fix` the command or
    step that fixes it (or None), and `exit_code` is what a command exits with."""

    exit_code = 1

    def __init__(self, reason: str, fix: str | None = None):
        self.reason = reason
        self.fix = fix
        super().__init__(f"{reason}; {fix}" if fix else reason)


class NotFound(ProjectsError):
    """No project has this key. `key` is the key asked for."""

    exit_code = 3

    def __init__(self, key: str):
        self.key = key
        super().__init__(f"no project is named {key!r}",
                         f"`moxi projects list` shows them, and `moxi projects add {key} "
                         "--root DIR` adds it")


class Unreadable(ProjectsError):
    """The store exists and can't be read: missing permissions, a corrupt file, or a
    schema older than this code reads. `path` is the store's file."""

    exit_code = 1

    def __init__(self, reason: str, fix: str | None = None, path: str | None = None):
        self.path = path
        super().__init__(reason, fix)


class TooNew(Unreadable, moxi.store.StoreTooNew):
    """The store was written by a newer version of projects than this one. A reader that
    catches `Unreadable` catches it; so does one that catches `moxi.store.StoreTooNew`."""

    exit_code = 3

    def __init__(self, path: str, version: int, known: int):
        moxi.store.StoreTooNew.__init__(self, path, version, known)
        self.path = path
        self.reason = (f"the projects store {path} is at schema version {version}, newer "
                       f"than the {known} this version of moxi-projects knows")
        self.fix = "upgrade it with `moxi install projects`"
        self.args = (f"{self.reason}; {self.fix}",)


class Busy(Unreadable):
    """The store was locked by another process past the wait. Trying again may work."""

    exit_code = 75

    def __init__(self, path: str | None = None):
        super().__init__("the projects store stayed locked by another process past the "
                         "wait", "try again in a moment", path)


# --- types ------------------------------------------------------------------------------

class _Record:
    """A plain read-only record: fields set once, compared and shown by value."""

    __slots__ = ()
    _fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        values = dict(zip(self._fields, args))
        values.update(kwargs)
        for name in self._fields:
            object.__setattr__(self, name, values[name])

    def __setattr__(self, name, value):
        raise AttributeError(f"{type(self).__name__} is read-only")

    def __delattr__(self, name):
        raise AttributeError(f"{type(self).__name__} is read-only")

    def _values(self) -> tuple:
        return tuple(getattr(self, name) for name in self._fields)

    def __eq__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return self._values() == other._values()

    def __hash__(self):
        return hash(self._values())

    def __repr__(self):
        inner = ", ".join(f"{name}={getattr(self, name)!r}" for name in self._fields)
        return f"{type(self).__name__}({inner})"


class Project(_Record):
    """One project: its key, root directory, kind, lead, associated agents, notes root and
    when it was added (UTC, ISO 8601 with `Z`)."""

    __slots__ = _fields = ("key", "root", "kind", "lead", "associated", "notes_root",
                           "created")

    key: str
    root: str
    kind: str
    lead: str | None
    associated: tuple[str, ...]
    notes_root: str | None
    created: str

    @property
    def agents(self) -> tuple[str, ...]:
        """The project's agents: the lead first, when there is one, then the associated
        set."""
        return ((self.lead,) if self.lead else ()) + self.associated

    def as_dict(self) -> dict:
        """The fields by their names, and `agents`, as plain values (for JSON)."""
        out = {name: getattr(self, name) for name in self._fields}
        out["associated"] = list(self.associated)
        out["agents"] = list(self.agents)
        return out


class Kind(_Record):
    """A kind a project may have. `module` is `projects` for a built-in kind, else the
    module that contributes it. `mint` is how `add --create` makes a root of this kind:
    `git` (a directory and `git init`), `dir` (a directory only) or `refuse`."""

    __slots__ = _fields = ("name", "doc", "module", "mint")

    name: str
    doc: str
    module: str
    mint: str


class Kinds(_Record):
    """What `kinds()` found: `found`, the kinds that may be used, and `broken`, one
    `(module, reason)` pair for each contribution that won't load and each kind that is
    blocked (declared twice, or declared with a wrong shape)."""

    __slots__ = _fields = ("found", "broken")

    found: tuple[Kind, ...]
    broken: tuple[tuple[str, str], ...]


# --- reading the store ------------------------------------------------------------------

def _store_path() -> str:
    import moxi.paths

    return os.path.join(str(moxi.paths.Layout("projects").data_dir()), _FILE)


def _is_busy(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (5, 6)  # SQLITE_BUSY, SQLITE_LOCKED
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _unreadable(path: str, exc: BaseException) -> Unreadable:
    if isinstance(exc, sqlite3.Error) and _is_busy(exc):
        return Busy(path)
    if isinstance(exc, PermissionError) or "permission" in str(exc).lower():
        return Unreadable(f"the projects store {path} can't be read ({exc})",
                          "give your user read and write access to it", path)
    return Unreadable(f"the projects store {path} can't be read ({exc})",
                      "move the file aside and add the projects again, or restore it from "
                      "a backup", path)


def _rows(path: str | None = None) -> list[tuple]:
    """Every project row with its associated agents, by key: tuples of the `Project`
    fields followed by `root_real`. A missing store, or one no write has finished
    setting up, has none."""
    path = path or _store_path()
    try:
        if os.path.getsize(path) == 0:
            # Created a moment ago by a write that hasn't set it up yet. `connect` would
            # switch an empty file to WAL, which is a write.
            return []
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise _unreadable(path, exc) from exc
    try:
        conn = moxi.store.connect(path, create=False)
    except moxi.store.StoreError as exc:
        if not os.path.exists(path):
            return []
        raise _unreadable(path, exc) from exc
    except (sqlite3.Error, OSError) as exc:
        raise _unreadable(path, exc) from exc
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            # Created and not yet set up: a write is between `connect` and its migration.
            return []
        if version > _VERSION:
            raise TooNew(path, version, _VERSION)
        if version not in _READS:
            raise Unreadable(f"the projects store {path} is at schema version {version}, "
                             "older than this version of moxi-projects reads",
                             _INSTALL, path)
        associated: dict[str, list[str]] = {}
        for project_key, agent in conn.execute(
                "SELECT project_key, agent FROM project_agents ORDER BY project_key, agent"):
            associated.setdefault(project_key, []).append(agent)
        return [(key, root, kind, lead, tuple(associated.get(key, ())), notes_root, created,
                 root_real)
                for key, root, kind, lead, notes_root, created, root_real in conn.execute(
                    "SELECT key, root, kind, lead, notes_root, created, root_real "
                    "FROM projects ORDER BY key")]
    except Unreadable:
        raise
    except (sqlite3.Error, OSError) as exc:
        raise _unreadable(path, exc) from exc
    finally:
        conn.close()


def _project(row: tuple) -> Project:
    return Project(*row[:7])


# --- the calls --------------------------------------------------------------------------

def list_projects(kind: str | None = None) -> list[Project]:
    """Every project, by key; only those of `kind` when it is given. Raises `Unreadable`
    (or its `TooNew` and `Busy`)."""
    return [_project(row) for row in _rows() if kind is None or row[2] == kind]


def get(key: str) -> Project:
    """The project whose key is `key`, ignoring case; the result carries the key as it is
    stored. Raises `NotFound`, or `Unreadable` (or its `TooNew` and `Busy`)."""
    wanted = key.casefold() if isinstance(key, str) else None
    for row in _rows():
        if row[0].casefold() == wanted:
            return _project(row)
    raise NotFound(key)


def kinds() -> Kinds:
    """The kinds a project may have now, read afresh from the installed contributions,
    and every contribution or kind that can't be used, with why. Never raises."""
    try:
        from . import contributions

        return contributions.load().kinds()
    except Exception as exc:  # noqa: BLE001 - kinds() answers rather than raises
        return Kinds(found=(), broken=(("projects", f"reading the kinds failed: {exc}"),))


def for_path(path: str) -> Project | None:
    """The project directory `path` is in, or None.

    `path` is made absolute (`~` expanded) and compared, with each of its parents, against
    every root: first as strings (the root as stored, and as it resolved when it was last
    written), then, for those candidates alone, by file identity. The deepest match wins;
    at one depth, the lower key. A linked git worktree whose own path matches nothing
    above its top is looked up again at the same place in its main work tree, which is
    found by reading git's own `.git` and `commondir` files; no git process runs. The
    filesystem calls stay on `path`'s own ancestry (and its main work tree's): a root
    that is not a candidate is never touched. The answer is about the filesystem as it
    stands now.

    Raises `Unreadable` (or its `TooNew` and `Busy`).
    """
    rows = _rows()
    if not rows:
        return None
    index = _Index(rows)
    path = os.path.normpath(os.path.abspath(os.path.expanduser(os.fspath(path))))
    best = index.deepest(path)
    top, top_depth = _work_tree_top(path)
    if top is None:
        return _project(best[2]) if best else None
    if best is not None and best[0] <= top_depth:
        return _project(best[2])
    main = _main_work_tree(top)
    if main is not None:
        rel = os.path.relpath(path, top)
        moved = os.path.normpath(os.path.join(main, rel))
        found = index.deepest(moved)
        if found is not None:
            return _project(found[2])
    return _project(best[2]) if best else None


# --- path lookup ------------------------------------------------------------------------

def _identity(path: str) -> tuple[int, int] | None:
    """`(st_dev, st_ino)`, or None for a path that can't be statted or has no inode."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    if not st.st_ino:
        return None
    return st.st_dev, st.st_ino


def _ancestry(path: str) -> list[str]:
    """`path` and each of its parents up to the filesystem root, nearest first."""
    out = [path]
    while True:
        parent = os.path.dirname(path)
        if parent == path:
            return out
        out.append(parent)
        path = parent


class _Index:
    """The rows keyed by their roots' spellings, compared in memory."""

    def __init__(self, rows: list[tuple]):
        self.exact: dict[str, list[tuple]] = {}
        self.real: dict[str, list[tuple]] = {}
        self.folded: dict[str, list[tuple]] = {}
        for row in rows:
            root, real = os.path.normcase(row[1]), os.path.normcase(row[7])
            self.exact.setdefault(root, []).append(row)
            if real != root:
                self.real.setdefault(real, []).append(row)
            for spelling in {root.casefold(), real.casefold()}:
                self.folded.setdefault(spelling, []).append(row)

    def deepest(self, path: str) -> tuple[int, tuple, tuple] | None:
        """The deepest match for `path`, as `(steps above path, sort key, row)`."""
        best = None
        identities: dict[str, tuple[int, int] | None] = {}

        def identity(p: str):
            if p not in identities:
                identities[p] = _identity(p)
            return identities[p]

        spellings = [path]
        real = os.path.realpath(path)
        if real != path:
            spellings.append(real)
        for spelling in spellings:
            for depth, ancestor in enumerate(_ancestry(spelling)):
                if best is not None and depth > best[0]:
                    break
                for row in self._matches(ancestor, identity):
                    candidate = (depth, (row[0].casefold(), row[0]), row)
                    if best is None or candidate[:2] < best[:2]:
                        best = candidate
        return best

    def _matches(self, ancestor: str, identity) -> list[tuple]:
        spelled = os.path.normcase(ancestor)
        out = list(self.exact.get(spelled, ()))
        seen = {row[0] for row in out}
        for row in self.real.get(spelled, ()):
            if row[0] in seen:
                continue
            here, there = identity(ancestor), identity(row[1])
            if here is not None and there is not None and here != there:
                continue  # a symlinked root that now points elsewhere
            out.append(row)
            seen.add(row[0])
        for row in self.folded.get(spelled.casefold(), ()):
            if row[0] in seen:
                continue
            here = identity(ancestor)
            if here is not None and here == (identity(row[1]) or identity(row[7])):
                out.append(row)
                seen.add(row[0])
        return out


def _work_tree_top(path: str) -> tuple[str | None, int]:
    """The nearest ancestor of `path` holding `.git`, and its steps above `path`."""
    for depth, ancestor in enumerate(_ancestry(path)):
        try:
            os.stat(os.path.join(ancestor, ".git"))
        except (OSError, ValueError):
            continue
        return ancestor, depth
    return None, -1


def _read_small(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(4096)
    except (OSError, ValueError):
        return None


def _main_work_tree(top: str) -> str | None:
    """For a linked worktree whose top is `top`, its main work tree; else None.

    The top's `.git` is a file `gitdir: <dir>` (relative to the file, or absolute), and
    that git dir holds a `commondir` file naming the common git dir (relative to the git
    dir, or absolute). The main work tree is the parent of the common dir, once resolved,
    when that dir is named `.git`. A submodule's git dir has no `commondir`, so a submodule
    is never mapped.
    """
    text = _read_small(os.path.join(top, ".git"))
    if text is None or not text.startswith("gitdir:"):
        return None
    gitdir = text[len("gitdir:"):].strip()
    if not gitdir:
        return None
    gitdir = os.path.normpath(os.path.join(top, gitdir))
    common = _read_small(os.path.join(gitdir, "commondir"))
    if common is None or not common.strip():
        return None
    common = os.path.realpath(os.path.normpath(os.path.join(gitdir, common.strip())))
    if os.path.basename(common) != ".git":
        return None
    return os.path.dirname(common)


__all__ = ["ProjectsError", "NotFound", "Unreadable", "TooNew", "Busy", "Project", "Kind",
           "Kinds", "DEFAULT_KIND", "list_projects", "get", "for_path", "kinds"]
