"""Where a project's rounds are: its rounds folder, and the rounds found in it.

A project has one rounds folder, its `dir` setting. An absolute `dir` (or one that starts
with `~`) is the folder. A relative one is placed in the project's notes folder: the one
its projects record names, else the one the notes module gives the project by default,
when notes is installed. That one call is all rounds asks of notes. A project the
projects list does not hold is `UnknownProject`; a relative `dir` with no notes folder to
place it in is `NoFolder`, which names the setting that fixes it. A project's record
also names its lead agent, or none, which `Home.lead` carries for a round that has no
lead of its own.

A rounds folder belongs to one project. Every log in it is written under its project's
lock, so two projects that reached one folder would write one log under two locks. A
project whose folder is another project's too, once every link on the way is followed,
is `SharedFolder`, which names both; it is so for each of the two, whichever is asked
for.

The rounds of a project are the folders of its rounds folder that hold a `round.jsonl`.
Nothing else lists them: a folder copied in, or one that arrived by git, is a round as
soon as it is there. A round's name is `<project>/<slug>`, the slug its folder's name.

Nothing here makes a folder or writes a file.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import log
from .errors import (BadName, NoFolder, RoundsError, SharedFolder, UnfencedTest,
                     UnknownProject)

#: What a slug is: lower-case letters, digits and `-`.
SLUG = re.compile(r"[a-z0-9-]+")


@dataclass(frozen=True)
class Home:
    """A project as rounds' home for rounds: `project` is its key as the projects list
    stores it, `rounds_dir` its rounds folder, which need not exist yet, and `lead`
    the lead agent its record names, None when it names none."""

    project: str
    rounds_dir: Path
    lead: str | None = None

    def round_dir(self, slug: str) -> Path:
        """The folder of the round `<project>/<slug>`."""
        return self.rounds_dir / slug


def is_slug(text: object) -> bool:
    """Whether `text` can be a round's slug."""
    return isinstance(text, str) and SLUG.fullmatch(text) is not None


def split(name: str) -> tuple[str, str]:
    """The project and the slug of the round name `name`, `<project>/<slug>`. Raises
    `BadName`. The project is as written: `home` gives its stored key."""
    project, slash, slug = name.partition("/") if isinstance(name, str) else ("", "", "")
    if not project or not slash or not is_slug(slug):
        raise BadName(name)
    return project, slug


def home(project: str, settings=None) -> Home:
    """The project `project` (its key, in any case) with its rounds folder. `settings`
    are rounds' settings as `settings.load()` gives them, read now when left out. Raises
    `UnknownProject`, `NoFolder` when the folder can't be worked out, or `SharedFolder`
    when it is another project's rounds folder too."""
    api = _projects()
    record = _record(api.get, project)
    settings = _settings(settings)
    found = Home(record.key, _rounds_dir(record, settings), record.lead)
    mine = os.path.realpath(found.rounds_dir)
    for other in _records(api):
        if other.key == record.key:
            continue
        try:
            theirs = _rounds_dir(other, settings)
        except NoFolder:
            continue
        if os.path.realpath(theirs) == mine:
            raise SharedFolder(record.key, other.key, Path(mine))
    return found


def homes(settings=None) -> tuple[list[Home], list[RoundsError]]:
    """Every project of the projects list with its rounds folder, by key, and beside
    them the error of each project that has none to use: the `NoFolder` of one whose
    folder can't be worked out, and the `SharedFolder` of each of two that reach one
    folder."""
    records = _records(_projects())
    settings = _settings(settings)
    placed: list[Home | NoFolder] = []
    reach: dict[str, list[str]] = {}
    for record in records:
        try:
            one = Home(record.key, _rounds_dir(record, settings), record.lead)
        except NoFolder as exc:
            placed.append(exc)
            continue
        placed.append(one)
        reach.setdefault(os.path.realpath(one.rounds_dir), []).append(record.key)
    found, failed = [], []
    for one in placed:
        if isinstance(one, NoFolder):
            failed.append(one)
            continue
        real = os.path.realpath(one.rounds_dir)
        others = [key for key in reach[real] if key != one.project]
        if others:
            failed.append(SharedFolder(one.project, others[0], Path(real)))
        else:
            found.append(one)
    return found, failed


def notes_dir(project: str) -> Path:
    """The notes folder of the project `project` (its key, in any case): the one its
    projects record names, else the one the notes module gives it. Raises
    `UnknownProject`, or `NoFolder` when it has none or none can be found out."""
    return _notes_dir(_record(_projects().get, project))


def lead(project: str) -> str | None:
    """The lead agent the record of the project `project` (its key, in any case) names,
    or None when it names none. Raises `UnknownProject`."""
    return _record(_projects().get, project).lead


def root(project: str) -> tuple[str, Path]:
    """The key of the project `project` (its key, in any case) as the projects list
    stores it, and the folder its record names as its root: for a build's repo, where
    git is run. Raises `UnknownProject`."""
    record = _record(_projects().get, project)
    return record.key, Path(os.path.expanduser(record.root))


def roots() -> list[tuple[str, Path]]:
    """Each project of the projects list, by key, with the folder its record names as
    its root."""
    return [(record.key, Path(os.path.expanduser(record.root)))
            for record in _records(_projects())]


def project_at(path: str | os.PathLike) -> str | None:
    """The key of the project the folder `path` is in, or None when it is in none. A
    linked git worktree is in the project of the work tree it was added from."""
    api = _projects()
    try:
        found = api.for_path(os.fspath(path))
    except api.ProjectsError as exc:
        raise RoundsError(f"the projects list can't be read: {exc}") from None
    return None if found is None else found.key


def slugs(rounds_dir: Path) -> list[str]:
    """The slug of each round in the rounds folder `rounds_dir`, sorted: every folder there
    that holds a round's log and whose name is a slug. A rounds folder that does not
    exist holds no round."""
    try:
        entries = sorted(os.listdir(rounds_dir))
    except (FileNotFoundError, NotADirectoryError):
        return []
    return [name for name in entries
            if is_slug(name) and log.exists(log.round_log(Path(rounds_dir) / name))]


def names(found: Iterable[Home]) -> list[str]:
    """The name of every round of the projects `found`, in their order."""
    return [f"{one.project}/{slug}" for one in found for slug in slugs(one.rounds_dir)]


# -- the lookups -------------------------------------------------------------------------

def _settings(settings):
    if settings is not None:
        return settings
    from . import settings as own

    return own.load()


def _projects():
    """The projects module's read API."""
    from moxi import modules

    found = modules.load("projects", interface=1)
    if not found.ok or found.api is None:
        raise RoundsError(f"the projects module can't be used: {found.reason}",
                          "run `moxi doctor`")
    return found.api


def _records(api) -> list:
    """Every record of the projects list."""
    try:
        return api.list_projects()
    except api.ProjectsError as exc:
        raise RoundsError(f"the projects list can't be read: {exc}") from None


def _rounds_dir(record, settings) -> Path:
    """The rounds folder the `dir` setting gives the project `record`. Raises
    `NoFolder`."""
    from . import settings as own

    rounds_dir = Path(os.path.expanduser(own.round_setting(settings, record.key, "dir")))
    return rounds_dir if rounds_dir.is_absolute() else _notes_dir(record) / rounds_dir


def _record(get, project: str):
    """The projects record `get` gives for `project`. Raises `UnknownProject`."""
    api = _projects()
    try:
        return get(project)
    except api.NotFound:
        raise UnknownProject(project) from None
    except api.ProjectsError as exc:
        raise RoundsError(f"the projects list can't be read: {exc}") from None


def _notes_dir(record) -> Path:
    """The notes folder of the project `record`: the one its record names, else the one
    notes gives it. Raises `NoFolder`."""
    if record.notes_root:
        return Path(os.path.expanduser(record.notes_root))
    from moxi import modules

    relative = "its `dir` setting is a relative path, the project's record names no "\
               "notes folder"
    if not modules.available("notes"):
        raise NoFolder(record.key, f"{relative} and the notes module is not installed")
    notes = modules.load("notes", interface=1)
    if not notes.ok or notes.api is None:
        raise NoFolder(record.key, f"{relative} and the notes module can't be used "
                                   f"({notes.reason})", known=False)
    try:
        found = notes.api.notes_root_for(record.root, record.kind, record.key)
    except UnfencedTest:
        raise
    except Exception as exc:  # noqa: BLE001 - whatever notes raises, the answer is none
        raise NoFolder(record.key, f"{relative} and notes could not give it one "
                                   f"({exc})", known=False) from None
    if found is None:
        raise NoFolder(record.key, f"{relative} and notes gives it none")
    return Path(found)


__all__ = ["SLUG", "Home", "is_slug", "split", "home", "homes", "notes_dir", "lead",
           "root", "slugs", "names"]
