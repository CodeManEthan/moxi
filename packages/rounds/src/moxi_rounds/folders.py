"""Where a project's rounds are: its rounds folder, and the rounds found in it.

A project has one rounds folder, its `dir` setting. An absolute `dir` (or one that starts
with `~`) is the folder. A relative one is placed in the project's notes folder: the one
its projects record names, else the one the notes module gives the project by default,
when notes is installed. That one call is all rounds asks of notes. A project the
projects list does not hold is `UnknownProject`; a relative `dir` with no notes folder to
place it in is `NoFolder`, which names the setting that fixes it.

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
from .errors import BadName, NoFolder, RoundsError, SharedFolder, UnknownProject

#: What a slug is: lower-case letters, digits and `-`.
SLUG = re.compile(r"[a-z0-9-]+")


@dataclass(frozen=True)
class Home:
    """A project as rounds' home for rounds: `project` is its key as the projects list
    stores it, and `folder` its rounds folder, which need not exist yet."""

    project: str
    folder: Path

    def round_folder(self, slug: str) -> Path:
        """The folder of the round `<project>/<slug>`."""
        return self.folder / slug


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
    found = Home(record.key, _folder(record, settings))
    mine = os.path.realpath(found.folder)
    for other in _records(api):
        if other.key == record.key:
            continue
        try:
            theirs = _folder(other, settings)
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
            one = Home(record.key, _folder(record, settings))
        except NoFolder as error:
            placed.append(error)
            continue
        placed.append(one)
        reach.setdefault(os.path.realpath(one.folder), []).append(record.key)
    found, failed = [], []
    for one in placed:
        if isinstance(one, NoFolder):
            failed.append(one)
            continue
        real = os.path.realpath(one.folder)
        others = [key for key in reach[real] if key != one.project]
        if others:
            failed.append(SharedFolder(one.project, others[0], Path(real)))
        else:
            found.append(one)
    return found, failed


def notes_folder(project: str) -> Path:
    """The notes folder of the project `project` (its key, in any case): the one its
    projects record names, else the one the notes module gives it. Raises
    `UnknownProject`, or `NoFolder` when it has none or none can be found out."""
    return _notes_folder(_record(_projects().get, project))


def slugs(folder: Path) -> list[str]:
    """The slug of each round in the rounds folder `folder`, sorted: every folder there
    that holds a round's log and whose name is a slug. A rounds folder that does not
    exist holds no round."""
    try:
        entries = sorted(os.listdir(folder))
    except (FileNotFoundError, NotADirectoryError):
        return []
    return [name for name in entries
            if is_slug(name) and log.exists(log.round_log(Path(folder) / name))]


def names(found: Iterable[Home]) -> list[str]:
    """The name of every round of the projects `found`, in their order."""
    return [f"{one.project}/{slug}" for one in found for slug in slugs(one.folder)]


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
    except api.ProjectsError as error:
        raise RoundsError(f"the projects list can't be read: {error}") from None


def _folder(record, settings) -> Path:
    """The rounds folder the `dir` setting gives the project `record`. Raises
    `NoFolder`."""
    from . import settings as own

    folder = Path(os.path.expanduser(own.round_setting(settings, record.key, "dir")))
    return folder if folder.is_absolute() else _notes_folder(record) / folder


def _record(get, project: str):
    """The projects record `get` gives for `project`. Raises `UnknownProject`."""
    api = _projects()
    try:
        return get(project)
    except api.NotFound:
        raise UnknownProject(project) from None
    except api.ProjectsError as error:
        raise RoundsError(f"the projects list can't be read: {error}") from None


def _notes_folder(record) -> Path:
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
    except Exception as error:  # noqa: BLE001 - whatever notes raises, the answer is none
        raise NoFolder(record.key, f"{relative} and notes could not give it one "
                                   f"({error})", known=False) from None
    if found is None:
        raise NoFolder(record.key, f"{relative} and notes gives it none")
    return Path(found)


__all__ = ["SLUG", "Home", "is_slug", "split", "home", "homes", "notes_folder", "slugs",
           "names"]
