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

`remove` takes a `record` from the page: what the remove came to is then kept in the
store under the request's idempotency key, so a repeat of the request gets the same answer
(`record_status`, `remove_status`). The verb passes none and keeps nothing.

`promote` turns one of notes' inbox one-offs into a project. It reaches notes only through
`moxi.modules`, and writes the row through `add`'s own steps, putting its move between
them: every check `add` makes and the defaults; for a kind other than a topic with
`create=True`, the mint of the root; the move of the one-off's folder to the row's notes
folder; then the row. A topic's notes folder is its root, so the move makes it; a topic
is never minted. A refusal comes before anything is made or moved. If the row write
fails, the folder is moved back and a minted root stays, as `add` leaves it.
"""

from __future__ import annotations

import errno
import os
import shlex
import shutil
import sqlite3
import subprocess
from collections.abc import Callable

from . import api, store
from . import contributions as _contributions
from .api import DEFAULT_KIND, NotFound, Project
from .contributions import Contributions, Planned
from .errors import Broken, MintFailed, MoveFailed, ProjectsError, Refused, Usage
from .rules import check_root, same_root

#: How long `git init` may run, in seconds, unless the caller gives another bound. The page
#: gives a shorter one, so its answer comes inside the shell's deadline for it.
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
        contributions: Contributions | None = None, notes: bool | None = None,
        git_timeout: float = GIT_TIMEOUT) -> Added:
    """Add project `key`; with `create`, mint its root first.

    Paths must already be absolute (`rules.absolutize` makes a command line's so). A root
    left out comes from a contribution's default, or the add stops with `Usage`.
    `contributions` and `notes` stand in for the installed contributions and for whether
    notes is installed. `git_timeout` bounds a `git` mint's `git init`, in seconds.
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
    made = _mint(planned, git, command, timeout=git_timeout)
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


def _mint(planned: Planned, git: str | None, command: str,
          timeout: float = GIT_TIMEOUT) -> str | None:
    """Make the root (and `git init` it for a `git` mint, stopped past `timeout`
    seconds). Returns the topmost directory made, or None when the root was there already,
    empty."""
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
        done = moxi.proc.run([git, "init", root], timeout=timeout)
        why = None if done.returncode == 0 else _git_said(done)
    except subprocess.TimeoutExpired:
        why = f"it ran past {timeout:g} s and was stopped"
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


#: Characters a Windows shell (cmd or PowerShell) reads specially outside double quotes.
_WINDOWS_SPECIAL = frozenset("&|<>^()%!;,=`$@{}'")


def _quote(text: str) -> str:
    """`text` as one word a pasted command line reads back unchanged: shell quoting on
    POSIX; on Windows, double quotes around text holding a space, a tab or a character
    the shell reads specially, the way programs split their command lines."""
    if os.name != "nt":
        return shlex.quote(text)
    quoted = subprocess.list2cmdline([text])
    if quoted != text or not any(c in _WINDOWS_SPECIAL for c in text):
        return quoted
    # A run of backslashes before the closing quote is doubled, or it escapes the quote.
    tail = len(text) - len(text.rstrip("\\"))
    return '"' + text + "\\" * tail + '"'


def _amend(exc: ProjectsError, reason: str, fix: str | None) -> None:
    """Change what `exc` says, keeping its type and exit code."""
    exc.reason = reason
    exc.fix = fix
    exc.args = (f"{reason}; {fix}" if fix else reason,)


# --- promote ------------------------------------------------------------------------------

#: The kind `promote` gives a one-off unless told another.
TOPIC = "topic"


def promote(slug: str, key: str, *, kind: str = TOPIC, root: str | None = None,
            create: bool = False, contributions: Contributions | None = None) -> Added:
    """Make notes' inbox one-off `slug` project `key`, and move its folder to the
    project's notes folder.

    A topic (the default kind) takes `key` and `kind` alone: its root and notes folder
    are the defaults notes gives, its folder under the notes' topics folder, and the move
    makes that folder. `root` and `create` are refused for a topic. Any other kind needs
    `root`, which must already be absolute; its row's notes folder is the one notes
    computes for `root`, passed as the user's own value so no default replaces it, and
    `create` mints `root` as `add` does.

    Every refusal comes before anything is made or moved: notes not installed (or not
    loading), each refusal `add` makes with the code `add` gives it, a bad slug or no such
    one-off, a notes folder that already exists (promote never merges two folders), a
    root notes gives no notes folder, and a `git` mint inside the notes root. A move that
    fails (across file systems, say) is `MoveFailed`, nothing moved. If the row write
    then fails, the folder is moved back and the write's error raised, saying so.
    `contributions` stands in for the installed contributions.
    """
    notes = _notes()
    topic = kind == TOPIC
    again = f"`moxi projects promote {_quote(slug)} {_quote(key)}`"
    if topic and root is not None:
        raise Refused("a topic's root is its folder under the notes' topics folder, which "
                      "promote makes by moving the one-off there; drop --root",
                      f"{again}, or pass --kind with --root for a project with code")
    if topic and create:
        raise Refused("promote makes a topic's folder by moving the one-off there; drop "
                      "--create", again)
    if not topic and root is None:
        raise Usage(f"promoting to kind {kind!r} needs the project's root",
                    f"pass --root DIR: `moxi projects promote {_quote(slug)} {_quote(key)} "
                    f"--kind {_quote(kind)} --root DIR`")
    source = str(_ask(notes, f"where the one-off {slug!r} is", notes.inbox_dir, slug))
    if not os.path.isdir(source) or os.path.islink(source):
        raise Refused(f"no inbox one-off is named {slug!r} ({source} is not a folder)",
                      "`moxi notes inbox list` shows them")
    notes_root = None
    if not topic:
        root = check_root(root)
        found = _ask(notes, f"the notes folder for {root}", notes.notes_root_for, root)
        notes_root = None if found is None else str(found)
    planned = _contributions.plan_add(key, kind=kind, root=root, notes_root=notes_root,
                                      contributions=contributions, notes=True)
    root = planned.row["root"]
    if create and planned.kind.kind.mint == "git":
        top = str(_ask(notes, "where the notes root is", notes.root))
        if _inside(root, top):
            raise Refused(f"--create runs `git init` in {root}, inside the notes root "
                          f"{top}, and a repository there keeps its files out of the "
                          "notes' snapshots", "pick a root outside the notes root")
    if not topic and notes_root is None:
        raise Refused(f"notes gives no notes folder for the root {root}, so the one-off "
                      "has nowhere to go", "pick a root under your home and outside the "
                                           "notes root, or promote it as a topic")
    target = planned.row["notes_root"]
    if target is None:
        raise Broken("notes", f"notes gave kind {kind!r} no notes folder for {key!r}",
                     _contributions._fix("notes"))
    git = _check_mint(planned) if create else None
    _check_free(planned)
    if os.path.lexists(target):
        raise Refused(f"{target} exists already, and promote never merges two folders",
                      f"move {_quote(target)} aside, or pick another "
                      + ("key" if topic else "root"))
    by_hand = _add_command(planned, notes_root)
    command = _promote_command(slug, planned)
    made = _mint(planned, git, command) if create else None
    made_words = _made_words(planned, made) if create else None
    left = f"; {made_words}, and is left as it is" if made_words else ""
    parent = os.path.dirname(target)
    top_made = _missing_top(parent)
    try:
        os.makedirs(parent, exist_ok=True)
        os.rename(source, target)
    except OSError as exc:
        _unmake(top_made, parent)
        why = ("they are on different file systems, and promote moves a folder, never "
               "copies one" if exc.errno == errno.EXDEV else exc.strerror or str(exc))
        raise MoveFailed(f"{source} could not be moved to {target} ({why}); nothing was "
                         f"moved and no project was added{left}",
                         f"move it yourself, then run `{by_hand}`") from exc
    try:
        project = store.write(planned.insert)
    except BaseException as exc:
        back = _move_back(target, source, top_made, parent)
        if isinstance(exc, ProjectsError):
            then = f"{exc.fix}; then " if exc.fix else ""
            if back:
                _amend(exc, f"{exc.reason}; the one-off is moved back to {source}{left}",
                       f"{then}promote it again: `{command}`")
            else:
                _amend(exc, f"{exc.reason}; the one-off is at {target} and could not be "
                            f"moved back to {source}{left}",
                       f"{then}`{by_hand}` adds its row, or move {_quote(target)} back to "
                       f"{_quote(source)} by hand")
        raise
    said = [made_words] if made_words else []
    if not topic and not create:
        note = root_note(project.root)
        if note:
            said.append(note)
    said.append(f"moved {source} to {target}")
    return Added(project, said)


def _notes():
    """notes' API, reached through the core: `Refused` while notes is not installed,
    `Broken` while it won't load."""
    import moxi.modules

    found = (moxi.modules.load("notes", interface=1)
             if moxi.modules.available("notes") else None)
    if found is None or found.state == "absent":
        raise Refused("promote moves one of notes' inbox one-offs, and notes is not "
                      "installed", "run `moxi install notes`, then promote again")
    if not found.ok:
        raise Broken("notes", f"promote needs notes' API, and notes won't load "
                              f"({found.reason})", _contributions._fix("notes"))
    return found.api


def _ask(notes, what: str, call, *args):
    """`call(*args)` on notes' API. A refusal of notes' own is `Refused`; anything else
    it raises is `Broken`, naming notes."""
    try:
        return call(*args)
    except notes.Refused as exc:
        raise Refused(exc.reason, exc.fix) from exc
    except Exception as exc:  # noqa: BLE001 - whatever notes raises is named as notes'
        raise Broken("notes", f"notes could not say {what} "
                              f"({_contributions._describe(exc)})",
                     getattr(exc, "fix", None) or _contributions._fix("notes")) from exc


def _inside(path: str, top: str) -> bool:
    """Whether `path` is `top` or under it, as spelled or with links resolved."""
    def spelled(p: str) -> str:
        return os.path.normcase(os.path.normpath(p))

    for inner in (spelled(path), spelled(os.path.realpath(path))):
        for outer in (spelled(top), spelled(os.path.realpath(top))):
            if inner == outer or inner.startswith(outer.rstrip(os.sep) + os.sep):
                return True
    return False


def _unmake(top: str | None, deepest: str) -> None:
    """Take away the empty folders a move made, from `deepest` up to `top`; None means
    it made none. A folder that can't go stops it."""
    if top is None:
        return
    here = deepest
    while True:
        try:
            os.rmdir(here)
        except OSError:
            return
        if here == top:
            return
        here = os.path.dirname(here)


def _move_back(target: str, source: str, top: str | None, parent: str) -> bool:
    """Move the one-off back after a failed write; whether it went."""
    try:
        os.rename(target, source)
    except OSError:
        return False
    _unmake(top, parent)
    return True


def _promote_command(slug: str, planned: Planned) -> str:
    """The `moxi projects promote` that tries again, with no `--create`: a root a mint
    made is there by then."""
    row = planned.row
    parts = ["moxi projects promote", _quote(slug), _quote(row["key"])]
    if row["kind"] != TOPIC:
        parts += ["--kind", _quote(row["kind"]), "--root", _quote(row["root"])]
    return " ".join(parts)


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


#: What `remove` gives its `record`: the connection of the write it runs in, and the
#: outcome, a dict of plain values (see `remove`).
Record = Callable[[sqlite3.Connection, dict], None]


def remove(key: str, *, contributions: Contributions | None = None,
           record: Record | None = None) -> Project:
    """Remove project `key` and return the row as it was.

    First a read refuses while the project has associated agents. Then every
    contribution is asked whether something it keeps still holds the project, outside
    any write, so a slow one never holds the store's lock; any answer refuses, listing
    every one. Then one write checks the associated set again and deletes the row.

    With `record`, what the remove came to is kept. `record(conn, outcome)` runs inside
    the delete's own write, with `{"state": "removed", "key": <the key as stored>}`. A
    refusal decided about the project (associated agents, a contribution's sentences,
    the key not found in a store that exists, the check again inside the write) is
    recorded through a write of its own, after the delete's write has rolled back, with
    `{"state": "refused", "key", "reason", "fix", "sentences", "exit_code"}`, and then
    raised as without `record`. So is a contribution that won't load, raises or answers
    out of shape (`Broken`): the remove can't say whether that module holds the project,
    and that too is an answer about this project's remove. `sentences` lists each
    contribution's sentence, named by its module, or holds the reason alone. A refusal
    that is the store's own (newer than this code, unreadable, locked past the wait, or
    no store file yet) is raised and records nothing; nothing is written and no store
    file is made. If the refusal's own write meets one of those, that is raised in its
    place. So after a raise, a caller tells a kept refusal from the rest by whether its
    record was written.

    Without `record` nothing is written on a refusal."""
    stored, sentences = key, None
    try:
        project = api.get(key)
        stored = project.key
        if project.associated:
            raise store.refuse_held_by_agents(project)
        found = contributions if contributions is not None else _contributions.load()
        refusals = found.remove_refusals(project)
        if refusals:
            sentences = [f"{module}: {sentence}" for module, sentence in refusals]
            raise Refused(f"project {project.key!r} is still held ({'; '.join(sentences)})",
                          f"free it as each says, then run `moxi projects remove "
                          f"{project.key}` again")

        def delete(conn: sqlite3.Connection) -> Project:
            gone = store.delete(conn, project.key)
            if record is not None:
                record(conn, {"state": "removed", "key": gone.key})
            return gone

        return store.write(delete)
    except (Refused, NotFound, Broken) as exc:
        if record is None or (isinstance(exc, NotFound) and not _store_exists()):
            raise
        outcome = {"state": "refused",
                   "key": stored,
                   "reason": exc.reason, "fix": exc.fix,
                   "sentences": sentences or [exc.reason], "exit_code": exc.exit_code}
        store.write(lambda conn: record(conn, outcome))
        raise


def _store_exists() -> bool:
    return os.path.lexists(store.path())


def record_status(job: str) -> Record:
    """A `record` for `remove` that keeps its outcome under the request's idempotency key
    `job`, for `remove_status` to read back."""
    def record(conn: sqlite3.Connection, outcome: dict) -> None:
        store.keep_remove_status(conn, job, outcome["key"], outcome)

    return record


def remove_status(job: str) -> dict | None:
    """What the remove request `job` came to, kept by `record_status`: the outcome `remove`
    recorded, with `job`, `project` (the key it was for) and `created`; or None for no
    such request (never kept, older than a day, or a store before the table). A read:
    it never writes."""
    return store.remove_status(job)


def add_agent(key: str, name: str) -> Project:
    """Add `name` to project `key`'s associated agents; return the project."""
    return store.write(lambda conn: store.add_agent(conn, key, name))


def remove_agent(key: str, name: str) -> Project:
    """Take `name` out of project `key`'s associated agents; return the project. A pair
    that isn't there refuses."""
    return store.write(lambda conn: store.remove_agent(conn, key, name))


__all__ = ["GIT_TIMEOUT", "TOPIC", "Added", "Record", "root_note", "add", "promote",
           "set_fields", "remove", "record_status", "remove_status", "add_agent",
           "remove_agent"]
