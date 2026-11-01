"""Where a session's notes live: the resolver, stated once.

`notes_dir(path, *, override=None)` answers with a `Resolution(dir, rule, warning)`.
`dir` is a `Path`, or None: no notes directory, no start line, no folder. The resolver
never creates a folder, and nothing here reads the process environment for the
override; the caller passes it.

The words used below. *home* is `Path.home()`; the *notes root* is the `root` setting;
the *base* is the root joined with `prefix`; *topics* and *inbox* are folders under the
base. "A is under B" (B itself included) compares the case-folded (`os.path.normcase`)
absolute spellings of A and B, and of each one's real path; any of the four pairs that
matches by path parts counts, so a symlinked home or notes root resolves and Windows
compares without case.

The rules, first answer wins (each answer's `rule` is the name in brackets):

1. [override] `override`, when given and not empty, `~` expanded, as given.
2. [project] With projects installed, the project `path` is in (`for_path`); one with
   `notes_root` set answers with it. When projects can't be read, the rules go on with
   a warning, since a project may keep its notes elsewhere.
3. [excluded] `path` under an `exclude` folder: None.
4. [inside] `path` under the notes root: `path` itself.
5. [computed] The anchor is the project's root when rule 2 found one, else the git work
   tree's top (for a linked worktree, its main work tree), else `path`. An anchor
   strictly under home, outside the notes root and outside every `exclude` folder gives
   the base joined with the anchor's parts below home. Home itself is not strictly under
   home, so a session started there gets None. [reserved] An answer under topics or
   inbox is refused, None, so a code project never claims a topic's or a one-off's
   folder.
6. [none] Otherwise None.

One project gives one answer: every path inside it, whichever subfolder, worktree or
symlinked spelling, has the same anchor, so the same folder.
"""

from __future__ import annotations

import os

from . import settings as _settings

#: The rule names a `Resolution` carries.
RULES = ("override", "project", "excluded", "inside", "computed", "reserved", "none")

#: The warning when projects can't be read.
PROJECTS_UNREADABLE = ("projects could not be read; a project may keep its notes "
                       "elsewhere, check before creating")

#: How long the one git call may take, in seconds.
GIT_TIMEOUT = 2.0

#: Variables that point git at another repository than the one around a path; the git
#: call drops them so it answers for the path it is given.
_GIT_LOCAL = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
              "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
              "GIT_IMPLICIT_WORK_TREE", "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_COUNT",
              "GIT_CONFIG_PARAMETERS", "GIT_GRAFT_FILE", "GIT_NO_REPLACE_OBJECTS",
              "GIT_REPLACE_REF_BASE", "GIT_SHALLOW_FILE")


class Resolution:
    """The resolver's answer: `dir` (a `Path` or None), `rule` (one of `RULES`) and
    `warning` (a sentence, or None)."""

    __slots__ = ("dir", "rule", "warning")

    def __init__(self, dir, rule: str, warning: str | None = None):
        object.__setattr__(self, "dir", dir)
        object.__setattr__(self, "rule", rule)
        object.__setattr__(self, "warning", warning)

    def __setattr__(self, name, value):
        raise AttributeError("Resolution is read-only")

    def __eq__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return (self.dir, self.rule, self.warning) == (other.dir, other.rule, other.warning)

    def __hash__(self):
        return hash((self.dir, self.rule, self.warning))

    def __repr__(self) -> str:
        return f"Resolution(dir={self.dir!r}, rule={self.rule!r}, warning={self.warning!r})"

    def as_dict(self) -> dict:
        """The fields as plain values (for JSON)."""
        return {"dir": None if self.dir is None else str(self.dir), "rule": self.rule,
                "warning": self.warning}


# --- "A is under B" -------------------------------------------------------------------------

def _parts(path: str, flavour) -> list[str]:
    """`path`'s parts: its anchor (drive and root) first, then each folder name."""
    drive, rest = flavour.splitdrive(path)
    seps = flavour.sep + (flavour.altsep or "")
    anchor = drive + (flavour.sep if rest[:1] and rest[:1] in seps else "")
    names = [p for p in rest.replace(flavour.altsep or flavour.sep, flavour.sep)
             .split(flavour.sep) if p]
    return [anchor, *names]


def _spellings(path: str, flavour, real: bool) -> list[str]:
    spelled = flavour.normpath(path)
    out = [spelled]
    if real:
        resolved = os.path.realpath(spelled)
        if resolved != spelled:
            out.append(resolved)
    return out


def below(path, root, *, flavour=os.path, real: bool | None = None) -> tuple[str, ...] | None:
    """The folder names of `path` below `root` (empty for `root` itself), or None when
    `path` is not under `root`. Both are absolute. The names come from `path`'s own
    spelling (or its real path's, for a pair only the real paths match).

    `flavour` is `os.path` or one of `posixpath` and `ntpath`, so another OS's path
    shapes can be compared as pure strings; real paths are read only for `os.path`
    (`real` overrides that)."""
    if real is None:
        real = flavour is os.path
    path, root = os.fspath(path), os.fspath(root)
    for p in _spellings(path, flavour, real):
        p_parts = _parts(p, flavour)
        p_folded = _parts(flavour.normcase(p), flavour)
        for r in _spellings(root, flavour, real):
            r_folded = _parts(flavour.normcase(r), flavour)
            if p_folded[:len(r_folded)] == r_folded:
                return tuple(p_parts[len(r_folded):])
    return None


def under(path, root, **kw) -> bool:
    """True when `path` is `root` or inside it (see `below`)."""
    return below(path, root, **kw) is not None


def same(a, b, **kw) -> bool:
    """True when `a` and `b` name one folder by the rule of `below`."""
    return below(a, b, **kw) == ()


# --- the rules ------------------------------------------------------------------------------

def _absolute(path) -> str:
    text = os.path.expanduser(os.fspath(path))
    if "\0" in text:
        raise ValueError("a path holding a NUL character names no folder")
    if not os.path.isabs(text):
        raise ValueError(f"the resolver needs an absolute path, not {text!r}: a relative "
                         "path would resolve against whoever asks")
    return os.path.normpath(text)


def computed(anchor: str, places) -> tuple[object, str]:
    """Rule 5 for `anchor`: `(Path, "computed")`, or `(None, "reserved")` for an answer
    under topics or inbox, or `(None, "none")`."""
    from pathlib import Path

    rel = below(anchor, str(Path.home()))
    if not rel:
        return None, "none"
    if under(anchor, places.root) or any(under(anchor, ex) for ex in places.exclude):
        return None, "none"
    answer = places.base.joinpath(*rel)
    if under(answer, places.topics) or under(answer, places.inbox):
        return None, "reserved"
    return answer, "computed"


def by_path(path: str, places, anchor) -> tuple[object, str]:
    """Rules 3 to 6 for `path`, projects aside. `anchor()` gives rule 5's anchor; it is
    called only when rules 3 and 4 don't answer."""
    from pathlib import Path

    if any(under(path, ex) for ex in places.exclude):
        return None, "excluded"
    if under(path, places.root):
        return Path(path), "inside"
    return computed(anchor(), places)


def git_anchor(path: str) -> str | None:
    """The git work tree's top for `path`, or for a linked worktree its main work tree;
    None when git can't say within `GIT_TIMEOUT`."""
    import subprocess

    import moxi.proc

    env = os.environ.copy()
    for name in _GIT_LOCAL:
        env.pop(name, None)
    try:
        done = moxi.proc.run(["git", "-C", path, "rev-parse", "--path-format=absolute",
                              "--show-toplevel", "--git-common-dir"],
                             timeout=GIT_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    lines = done.stdout.decode("utf-8", "surrogateescape").splitlines()
    if len(lines) < 2 or not lines[0] or not lines[1]:
        return None
    top = os.path.normpath(lines[0])
    common = os.path.normpath(lines[1])
    if os.path.basename(common).lower() == ".git":
        main = os.path.dirname(common)
        if not same(main, top):
            return main
    return top


def _project(path: str):
    """`(project or None, warning or None)` from projects, when it is installed."""
    import moxi.modules

    if not moxi.modules.available("projects"):
        return None, None
    found = moxi.modules.load("projects", interface=1)
    if found.state == "absent":
        return None, None
    if not found.ok:
        return None, PROJECTS_UNREADABLE
    try:
        return found.api.for_path(path), None
    except found.api.Unreadable:
        return None, PROJECTS_UNREADABLE


def notes_dir(path, *, override=None) -> Resolution:
    """The notes directory for a session in `path`, an absolute path (`~` expanded).

    Raises `ValueError` for a relative path, and `moxi.config.ConfigError` when notes'
    config can't be read."""
    from pathlib import Path

    if override is not None and os.fspath(override) != "":
        return Resolution(Path(os.path.expanduser(os.fspath(override))), "override")
    path = _absolute(path)
    project, warning = _project(path)
    if project is not None and project.notes_root:
        return Resolution(Path(project.notes_root), "project", warning)
    places = _settings.read()
    if project is not None:
        anchor = lambda: os.path.normpath(project.root)  # noqa: E731
    else:
        anchor = lambda: git_anchor(path) or path  # noqa: E731
    found, rule = by_path(path, places, anchor)
    return Resolution(found, rule, warning)


def notes_root_for(root, kind: str = "project", key: str | None = None):
    """The notes folder a project of `kind` at `root` gets by default, or None.

    A topic's notes folder is its root, which defaults to `topics/<key>`. Any other kind's
    is rule 5's answer with `root` as the anchor. Local reads only: no git process."""
    from pathlib import Path

    places = _settings.read()
    if kind == "topic":
        if root is not None:
            return Path(_absolute(root))
        if not key:
            raise ValueError("a topic's notes folder needs its root or its key")
        return places.topics / key
    if root is None:
        return None
    return computed(_absolute(root), places)[0]


__all__ = ["RULES", "PROJECTS_UNREADABLE", "Resolution", "below", "under", "same",
           "notes_dir", "notes_root_for", "git_anchor", "computed", "by_path"]
