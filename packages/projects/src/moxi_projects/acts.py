"""projects' acts: add (for an existing directory or a minted one), set, remove, and the
associated agents. The verbs call these, and so will the page.

Each act checks everything it can before it writes, writes through the store's one write
sequence (`moxi_projects.store.write`), and returns the project as it now stands (for
remove, as it was). Each refusal is a `moxi_projects.api.ProjectsError` carrying the
reason, the fix and the exit code.

`add` with `create=True` mints the root first, then writes the row, in three steps:

1. Every check `add` makes, plus: the kind's mint is not `refuse`; the root is absent or
   an empty directory; for a `git` mint, `git` is on PATH. Any failure refuses (exit 3)
   and nothing is made.
2. Make the root, with any missing parents. For a `git` mint, then run `git init` on it,
   with a timeout, reading only its exit status. A `dir` mint stops at the directory.
3. Write the row.

A mint refuses before it makes anything, and never leaves a row whose root it failed to
make. If making the directory or `git init` fails, no row is written (exit 1) and the
message names the directory left, the topmost one the mint made, and the commands that
finish by hand. If the row write then fails, the directory stays as made and the message
names the `moxi projects add` that adds its row.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess

from . import api, store
from . import contributions as _contributions
from .api import DEFAULT_KIND, Project
from .contributions import Contributions, Planned
from .errors import MintFailed, ProjectsError, Refused, Usage
from .rules import same_root

#: How long `git init` may run, in seconds.
GIT_TIMEOUT = 30


class Added:
    """What `add` did: `project`, the row as written, and `notes`, sentences worth
    showing that refuse nothing (a root that doesn't exist yet, what a mint made)."""

    __slots__ = ("project", "notes")

    def __init__(self, project: Project, notes: list[str]):
        self.project = project
        self.notes = notes


def root_note(root: str) -> str | None:
    """A sentence about a root that doesn't exist yet, or isn't a directory; else None."""
    if os.path.isdir(root):
        return None
    if os.path.lexists(root):
        return f"the root {root} is not a directory"
    return f"the root {root} doesn't exist yet; the project is kept all the same"


# --- add ----------------------------------------------------------------------------------

def add(key: str, *, root: str | None = None, kind: str = DEFAULT_KIND,
        lead: str | None = None, notes_root: str | None = None, create: bool = False,
        contributions: Contributions | None = None, notes: bool | None = None) -> Added:
    """Add project `key`; with `create`, mint its root first.

    Paths must already be absolute (`rules.absolutize` makes a command line's so). A root
    left out comes from a contribution's default, or the add stops with `Usage`.
    `contributions` and `notes` stand in for the installed contributions and for whether
    notes is installed.
    """
    planned = _contributions.plan_add(key, kind=kind, root=root, lead=lead,
                                      notes_root=notes_root,
                                      contributions=contributions, notes=notes)
    if not create:
        project = store.write(planned.insert)
        note = root_note(project.root)
        return Added(project, [note] if note else [])
    command = _add_command(planned, notes_root)
    git = _check_mint(planned)
    _check_free(planned)
    made = _mint(planned, git, command)
    try:
        project = store.write(planned.insert)
    except ProjectsError as exc:
        what = _made_words(planned, made)
        _amend(exc, f"{exc.reason}; {what}, and is left as it is",
               f"{exc.fix + '; then ' if exc.fix else ''}`{command}` adds its row")
        raise
    return Added(project, [_made_words(planned, made)])


def _check_mint(planned: Planned) -> str | None:
    """Refuse a mint that can't go ahead, before anything is made. Returns git's path
    for a `git` mint."""
    kind = planned.kind.kind
    root = planned.row["root"]
    if kind.mint == "refuse":
        raise Refused(f"kind {kind.name!r} ({kind.module}) can't be made with --create",
                      f"make the directory yourself, then run `moxi projects add "
                      f"{planned.row['key']} --root DIR --kind {kind.name}` without "
                      "--create")
    if os.path.lexists(root):
        if not os.path.isdir(root):
            raise Refused(f"--create makes the root {root}, and something that is not a "
                          "directory is there", "pick another root, or add it without "
                                                "--create")
        try:
            entries = os.listdir(root)
        except OSError as exc:
            raise Refused(f"--create makes the root {root}, and it can't be listed "
                          f"({exc.strerror or exc})", "pick another root") from exc
        if entries:
            raise Refused(f"--create makes the root {root}, and it is not empty",
                          f"add it without --create (`moxi projects add "
                          f"{planned.row['key']} --root {_quote(root)}`), or pick an "
                          "empty or absent directory")
    if kind.mint != "git":
        return None
    git = shutil.which("git")
    if git is None:
        raise Refused(f"--create for kind {kind.name!r} runs `git init`, and git is not "
                      "on PATH", "install git, or make the directory yourself and add it "
                                 "without --create")
    return git


def _check_free(planned: Planned) -> None:
    """Refuse a key or root already taken, before the mint makes anything. The write
    checks again under its lock."""
    key, root = planned.row["key"], planned.row["root"]
    for project in api.list_projects():
        if project.key.casefold() == key.casefold():
            raise planned.annotate(store.refuse_key(key, project.key))
        if same_root(project.root, root):
            raise planned.annotate(store.refuse_root(root, project.key))


def _missing_top(path: str) -> str | None:
    """The topmost of `path` and its parents that doesn't exist, or None."""
    top = None
    while not os.path.lexists(path):
        top = path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return top


def _mint(planned: Planned, git: str | None, command: str) -> str | None:
    """Make the root (and `git init` it for a `git` mint). Returns the topmost directory
    made, or None when the root was there already, empty."""
    root = planned.row["root"]
    top = _missing_top(root)
    by_hand = ((f"`git init {_quote(root)}`, then " if git else "") + f"`{command}`")
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as exc:
        left = top if top is not None and os.path.lexists(top) else None
        what = (f"{left} is left as made" if left else "nothing was made")
        raise MintFailed(f"the root {root} could not be made ({exc.strerror or exc}); "
                         f"{what}, and no project was added",
                         f"make {_quote(root)} yourself, then run {by_hand}",
                         left) from exc
    if git is None:
        return top
    import moxi.proc

    try:
        done = moxi.proc.run([git, "init", root], timeout=GIT_TIMEOUT)
        why = None if done.returncode == 0 else _git_said(done)
    except subprocess.TimeoutExpired:
        why = f"it ran past {GIT_TIMEOUT} s and was stopped"
    except OSError as exc:
        why = f"it could not run: {exc.strerror or exc}"
    if why is None:
        return top
    left = top if top is not None else root
    what = (f"{top} is left as made" if top is not None
            else f"{root} was there before and is left")
    raise MintFailed(f"`git init {root}` failed ({why}); {what}, and no project was added",
                     f"finish by hand: {by_hand}", left)


def _git_said(done: subprocess.CompletedProcess) -> str:
    err = done.stderr or b""
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    first = next((line.strip() for line in err.splitlines() if line.strip()), "")
    return f"exit {done.returncode}" + (f": {first}" if first else "")


def _made_words(planned: Planned, made: str | None) -> str:
    root = planned.row["root"]
    git = planned.kind.kind.mint == "git"
    if made is None:
        return (f"ran git init in {root}, which was empty" if git
                else f"{root} was there already, empty")
    return f"made {made}" + (f" and ran git init in {root}" if git else "")


def _add_command(planned: Planned, typed_notes_root: str | None) -> str:
    """The `moxi projects add` that adds `planned`'s row for a root made by hand."""
    row = planned.row
    parts = ["moxi projects add", _quote(row["key"]), "--root", _quote(row["root"])]
    if row["kind"] != DEFAULT_KIND:
        parts += ["--kind", _quote(row["kind"])]
    if row["lead"] is not None:
        parts += ["--lead", _quote(row["lead"])]
    if typed_notes_root is not None:
        parts += ["--notes-root", _quote(row["notes_root"])]
    return " ".join(parts)


def _quote(text: str) -> str:
    return text if os.name == "nt" else shlex.quote(text)


def _amend(exc: ProjectsError, reason: str, fix: str | None) -> None:
    """Change what `exc` says, keeping its type and exit code."""
    exc.reason = reason
    exc.fix = fix
    exc.args = (f"{reason}; {fix}" if fix else reason,)


# --- set, remove and the agents ------------------------------------------------------------

_FIELDS = ("root", "kind", "lead", "notes_root")


def set_fields(key: str, *, contributions: Contributions | None = None,
               notes: bool | None = None, **changes) -> Project:
    """Change the named fields of project `key` and return it as it now stands.

    `changes` holds any of `root`, `kind`, `lead` and `notes_root`; None clears `lead` or
    `notes_root`. Naming none is `Usage`. A set takes no defaults."""
    if not changes:
        raise Usage("nothing to change: name at least one field",
                    "pass --root, --kind, --lead, --no-lead, --notes-root or "
                    "--no-notes-root")
    _contributions.plan_set(key, contributions=contributions, notes=notes, **changes)
    return store.write(lambda conn: store.update(conn, key, **changes))


def remove(key: str, *, contributions: Contributions | None = None) -> Project:
    """Remove project `key` and return the row as it was.

    First a read refuses while the project has associated agents. Then every
    contribution is asked whether something it keeps still holds the project, outside
    any write, so a slow one never holds the store's lock; any answer refuses, listing
    every one. Then one write checks the associated set again and deletes the row."""
    project = api.get(key)
    if project.associated:
        raise store.refuse_held_by_agents(project)
    found = contributions if contributions is not None else _contributions.load()
    refusals = found.remove_refusals(project)
    if refusals:
        listed = "; ".join(f"{module}: {sentence}" for module, sentence in refusals)
        raise Refused(f"project {project.key!r} is still held ({listed})",
                      f"free it as each says, then run `moxi projects remove "
                      f"{project.key}` again")
    return store.write(lambda conn: store.delete(conn, project.key))


def add_agent(key: str, name: str) -> Project:
    """Add `name` to project `key`'s associated agents; return the project."""
    return store.write(lambda conn: store.add_agent(conn, key, name))


def remove_agent(key: str, name: str) -> Project:
    """Take `name` out of project `key`'s associated agents; return the project. A pair
    that isn't there refuses."""
    return store.write(lambda conn: store.remove_agent(conn, key, name))


__all__ = ["GIT_TIMEOUT", "Added", "root_note", "add", "set_fields", "remove", "add_agent",
           "remove_agent"]
