"""notes' link with projects, which is optional: the topic kind, field defaults and the
remove refusal.

projects reads this module through its `moxi.projects` entry-point group, at interface 2:

- `KINDS` adds `topic`, a subject with no code whose root is its notes folder under
  topics. `add --kind topic --create` makes that folder, parents included, and never
  runs `git init`, so the topic's notes stay in the notes repo.
- `prepare(fields)` gives `add` its defaults: a topic's root (`topics/<key>`) and notes
  folder (its root), and any other project's notes folder, the one the resolver computes
  for its root, so a later move of the code keeps its notes.
- `refuse_remove(project)` refuses while removing the row would move where that
  project's sessions find their notes and the folder holds files.

Every function does local reads only: no git process, no writes. This package never
imports projects; notes reaches it only through `moxi.modules`.
"""

from __future__ import annotations

import os

from . import resolve
from . import settings as _settings

#: The version of projects' contribution contract this module implements.
INTERFACE = 2

TOPIC = "topic"


def _check_topic(fields) -> str | None:
    """Refuse a topic whose root is not a folder under topics, or whose notes folder is
    not its root."""
    places = _settings.read()
    root = fields.get("root")
    if root is None:
        return None
    if not resolve.below(root, places.topics):
        return (f"a topic's root is a folder under {places.topics}, and {root} is not; "
                "leave --root out to take the default")
    notes_root = fields.get("notes_root")
    if notes_root is None or not resolve.same(notes_root, root):
        return (f"a topic's notes folder is its root, {root}, not "
                f"{notes_root if notes_root is not None else 'none'}; leave --notes-root "
                "out to take the default")
    return None


KINDS = {TOPIC: ("a subject with no code: its root is its notes folder, under the notes' "
                 "topics folder", _check_topic, "dir")}


def prepare(fields) -> dict | None:
    """The defaults for an `add` given `fields`, for fields the user left unset."""
    kind = fields.get("kind")
    root = fields.get("root")
    out = {}
    if kind == TOPIC:
        if root is None:
            root = str(resolve.notes_root_for(None, TOPIC, fields.get("key")))
            out["root"] = root
        if fields.get("notes_root") is None:
            out["notes_root"] = root
        return out or None
    if root is not None and fields.get("notes_root") is None:
        found = resolve.notes_root_for(root)
        if found is not None:
            out["notes_root"] = str(found)
    return out or None


def _files_in(folder) -> int:
    """How many files `folder` holds, at any depth; links are not followed."""
    count = 0
    for _here, _dirs, files in os.walk(folder):
        count += len(files)
    return count


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def refuse_remove(project) -> str | None:
    """One sentence when removing `project` (projects' `Project`) would move where its
    sessions find their notes and the folder holds files; else None."""
    places = _settings.read()
    root = os.path.normpath(project.root)
    if project.notes_root:
        from pathlib import Path

        with_row = Path(project.notes_root)
    else:
        with_row = resolve.by_path(root, places, lambda: root)[0]
    without = resolve.by_path(root, places, lambda: resolve.git_top(root) or root)[0]
    if with_row is None:
        return None
    if without is not None and resolve.same(with_row, without):
        return None
    files = _files_in(with_row)
    if not files:
        return None
    moves = (f"moves its sessions' notes to {without}" if without is not None
             else "leaves its sessions with no notes folder")
    fix = (f"move them, or clear it first: moxi projects set {project.key} --no-notes-root"
           if project.notes_root else "move them first")
    return (f"project {project.key}'s notes folder {with_row} holds "
            f"{_plural(files, 'file')}; removing {project.key} {moves}; {fix}")


__all__ = ["INTERFACE", "KINDS", "prepare", "refuse_remove"]
