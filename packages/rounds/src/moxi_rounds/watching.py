"""The watched-paths guard's judge: one line after a write to a path a project watches.

A project's `watched_paths` (a setting of its own table) lists entries, each a
`pattern`, a `note`, the `skills` to load and optionally `only_new`. turns calls
`judge(call)` before each covered write while rounds' switch `watched_paths_guard.enabled`
is on (`moxi_rounds.guards`); the judge answers `Allow(line)` when an entry counts for a
file the call writes, and None otherwise. It never refuses a call.

**Which files a call writes** are read by turns' `touched_files`: the entries of kind
`write`, `add`, `update` and `moved-to`, the files the call leaves at a path. A deleted
file, and the old path of a moved one, are written by no one.

**Matching a written file.**

1. The path is made absolute against the call's `cwd` and normalized, without resolving
   links.
2. Its project is `projects.for_path`'s answer; a file in no project is not watched.
3. The base, the folder the project's patterns are relative to, is the project's root as
   it stands in the tree the file is in, never the git top:
   - in a linked git worktree of the root's repo (the nearest folder above the file
     holding a `.git` entry holds a file there, whose main work tree is the root's), the
     worktree's top joined with the root's place under its main work tree's top. This is
     looked for first, so a worktree inside the root's own folder is not read as the root;
   - else, the file inside the root (by the path as written, then by file identity), the
     root.
   A file in neither is not watched, nor is one on another drive than its base.
4. Each entry's pattern is matched, case-blind on every system, against the file's
   parts relative to the base: `*` any run of characters within a part, `?` one
   character, `[...]` one of a set, a whole part `**` any number of parts. A pattern
   with the folder mark (one `/` at its end) is matched against each folder above the
   file inside the base.
5. With `only_new` an entry counts only when what it matched did not exist before the
   call; a Codex `Add File` section is new by its own word.

Each `(agent, project, entry, path)` is named once in this process; an agent is the main
loop or one subagent by its id. Every error is caught and logged once per kind, and the
call runs. rounds' config is read again only when its file's modification time or size
changed; the projects store is read once per call.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass

from . import paths
from . import settings as own

log = logging.getLogger(__name__)

#: The guard's name; turns gives it the id `rounds.watched-paths`.
NAME = "watched-paths"
#: The guard's switch, a setting of rounds' config.
SWITCH = own.GUARD_SWITCH
#: What the guard covers: each harness's file-writing tools.
CALLS = {"claude": ("Write", "Edit"), "codex": ("apply_patch",),
         "opencode": ("write", "edit")}
#: The kinds of `touched_files` entry that leave a file at a path.
WRITES = ("write", "add", "update", "moved-to")

#: What was named in this process: `(agent, project, pattern, note, path)`.
_named: set[tuple] = set()
#: The kinds of error already logged.
_said: set[str] = set()
#: rounds' settings as last read, with the config file's `(mtime_ns, size)` then.
_cache: dict = {}


@dataclass(frozen=True)
class Match:
    """One entry that counts for a written file: the `project`, the base-relative
    `path` it names (a folder with a `/` at its end), the entry's `note` and `skills`,
    and its `key` for the once-per-agent memory."""

    project: str
    path: str
    note: str
    skills: tuple[str, ...]
    key: tuple


def judge(call):
    """turns' judge: `Allow(line)` when an entry of a project's `watched_paths` counts
    for a file `call` writes and was not yet named to its agent; else None. Never
    refuses, never raises."""
    try:
        line = said(call)
        if line is not None:
            return _turns().Allow(line)
    except Exception as exc:  # noqa: BLE001 - the guard fails open, by design
        kind = type(exc).__name__
        if kind not in _said:
            _said.add(kind)
            log.warning("rounds' watched-paths guard could not judge a call: %s: %s",
                        kind, exc)
    return None


def said(call) -> str | None:
    """The line the call earns, or None; what it names is remembered as named."""
    touched = [one for one in _turns().touched_files(call) if one.kind in WRITES]
    if not touched:
        return None
    settings = _settings()
    if settings is None:
        return None
    cwd = call.cwd or os.getcwd()
    found = []
    for one in touched:
        found += matches(_absolute(one.path, cwd), settings, added=one.kind == "add")
    agent = call.agent_id
    fresh = []
    for match in found:
        key = (agent, *match.key)
        if key in _named:
            continue
        _named.add(key)
        fresh.append(match)
    return None if not fresh else line(fresh)


def matches(file: str, settings, *, added: bool = False) -> list[Match]:
    """Each entry of the `watched_paths` of the project `file` is in that counts for it,
    `file` absolute and normalized. `added` says the call makes the file new by its own
    word."""
    project = _project_of(file)
    if project is None:
        return []
    entries = own.project_setting(settings, project.key, "watched_paths")
    if not entries:
        return []
    root = os.path.normpath(os.path.expanduser(project.root))
    base = base_of(file, root)
    if base is None:
        return []
    parts = relative_parts(file, base)
    if not parts:
        return []
    out = []
    for entry in entries:
        pattern = entry["pattern"]
        folder = pattern.endswith("/")
        wanted = [part for part in pattern.rstrip("/").split("/")]
        if folder:
            hit = next((parts[:n] for n in range(1, len(parts))
                        if matched(wanted, parts[:n])), None)
            if hit is None:
                continue
            named = "/".join(hit) + "/"
            new = not os.path.lexists(os.path.join(base, *hit))
        else:
            if not matched(wanted, parts):
                continue
            named = "/".join(parts)
            new = added or not os.path.lexists(file)
        if entry.get("only_new") and not new:
            continue
        out.append(Match(project.key, named, entry["note"], tuple(entry["skills"]),
                         (project.key, pattern, entry["note"], named)))
    return out


def line(found: Sequence[Match]) -> str:
    """The line the agent reads after the call: each matched path with its note, by
    project, then each skill once, in the order first named."""
    by_project: dict[str, list[str]] = {}
    for match in found:
        by_project.setdefault(match.project, []).append(f"{match.path} - {match.note}")
    text = "; ".join(f"of {project}: {'; '.join(named)}"
                     for project, named in by_project.items())
    if not text.endswith((".", "!", "?")):
        text += "."
    skills = list(dict.fromkeys(skill for match in found for skill in match.skills))
    return f"rounds: watched path {text} Load: {', '.join(skills)}."


# -- the base -------------------------------------------------------------------------

def base_of(file: str, root: str) -> str | None:
    """The folder `file`'s project patterns are relative to, the project's root being
    `root`; None when the file is in neither a linked worktree of the root's repo nor
    the root."""
    top = _git_holder(os.path.dirname(file))
    if top is not None and os.path.isfile(os.path.join(top, ".git")):
        main = _main_work_tree(top)
        root_top = _git_holder(root)
        if main is not None and root_top is not None and _same(main, root_top):
            base = os.path.normpath(os.path.join(top, os.path.relpath(root, root_top)))
            return base if relative_parts(file, base) is not None else None
    return root if relative_parts(file, root) is not None else None


def relative_parts(file: str, base: str) -> list[str] | None:
    """`file`'s parts relative to `base`, compared by the path as written and then by
    file identity; None when it is not inside `base`."""
    try:
        rel = os.path.relpath(file, base)
    except ValueError:
        return None  # another drive
    if not os.path.isabs(rel) and rel != os.pardir and not rel.startswith(
            os.pardir + os.sep):
        return [] if rel == os.curdir else _split(rel)
    try:
        wanted = os.stat(base)
    except OSError:
        return None
    parts, current = [], file
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            return None
        parts.insert(0, os.path.basename(current))
        current = parent
        try:
            if os.path.samestat(os.stat(current), wanted):
                return parts
        except OSError:
            continue


def _split(rel: str) -> list[str]:
    seps = re.escape(os.sep) + (re.escape(os.altsep) if os.altsep else "")
    return [part for part in re.split(f"[{seps}]", rel) if part]


def _git_holder(folder: str) -> str | None:
    """The nearest folder at or above `folder` holding a `.git` entry, or None."""
    current = folder
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _main_work_tree(top: str) -> str | None:
    """For a linked worktree whose top is `top`, its main work tree, read from git's
    own `.git` and `commondir` files with no git process; else None."""
    text = _read_small(os.path.join(top, ".git"))
    if text is None or not text.startswith("gitdir:"):
        return None
    gitdir = os.path.normpath(os.path.join(top, text[len("gitdir:"):].strip()))
    common = _read_small(os.path.join(gitdir, "commondir"))
    if common is None or not common.strip():
        return None
    common = os.path.normpath(os.path.join(gitdir, common.strip()))
    if os.path.basename(common) != ".git":
        return None
    return os.path.dirname(common)


def _read_small(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(4096).strip()
    except (OSError, UnicodeDecodeError):
        return None


def _same(one: str, other: str) -> bool:
    if os.path.normcase(os.path.normpath(one)) == os.path.normcase(os.path.normpath(other)):
        return True
    try:
        return os.path.samefile(one, other)
    except OSError:
        return False


# -- matching -------------------------------------------------------------------------

def matched(pattern: Sequence[str], parts: Sequence[str]) -> bool:
    """Whether the pattern's parts match `parts`, case-blind: a whole part `**` stands
    for any number of parts, none included."""
    if not pattern:
        return not parts
    if pattern[0] == "**":
        return any(matched(pattern[1:], parts[n:]) for n in range(len(parts) + 1))
    return (bool(parts) and _part(pattern[0]).match(parts[0]) is not None
            and matched(pattern[1:], parts[1:]))


def _part(pattern: str) -> re.Pattern:
    return re.compile(fnmatch.translate(pattern), re.IGNORECASE | re.DOTALL)


# -- what it reads ----------------------------------------------------------------------

def _absolute(path: str, cwd: str) -> str:
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))


def _project_of(file: str):
    from moxi import modules

    projects = modules.load("projects")
    if not projects.ok or projects.api is None:
        return None
    return projects.api.for_path(file)


def _settings():
    """rounds' settings, read again only when the config file's modification time or
    size changed; None when there is nothing to read."""
    config = paths.LAYOUT.config_file()
    try:
        stat = os.stat(config)
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    if _cache.get("stamp", ()) != stamp or "settings" not in _cache:
        _cache.clear()
        _cache["stamp"] = stamp
        _cache["settings"] = own.load()
    return _cache["settings"]


def _turns():
    from moxi import modules

    found = modules.load("turns")
    if not found.ok or found.api is None:
        raise ImportError(f"the watched-paths guard needs turns, which is {found.state}")
    return found.api


def forget() -> None:
    """Forget what was named and the settings read: a new endpoint process starts so."""
    _named.clear()
    _said.clear()
    _cache.clear()


__all__ = ["NAME", "SWITCH", "CALLS", "WRITES", "Match", "judge", "said", "matches",
           "line", "base_of", "relative_parts", "matched", "forget"]
