"""Inbox one-offs: small pieces of work with a notes folder and no project row yet.

Each one is a folder `<inbox>/<slug>` under the base (see `moxi_notes.settings`). A slug
is a lower-case letter or digit, then up to 62 more of letters, digits and `-`: a project
key's alphabet, because promoting a one-off makes it a project. Making one is a plain
`mkdir`; nothing here runs `git init`, so a one-off's notes stay in the notes repo.
"""

from __future__ import annotations

import os
import re

from . import settings as _settings
from .errors import BadSlug, Refused

SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
SLUG_RULE = ("a slug is a lower-case letter or digit, then up to 62 more lower-case "
             "letters, digits and -")


class Inbox:
    """One inbox one-off: its `slug`, its folder `path` (a `Path`) and when the folder
    last changed, `modified` (UTC, ISO 8601 with `Z`)."""

    __slots__ = ("slug", "path", "modified")

    def __init__(self, slug: str, path, modified: str):
        object.__setattr__(self, "slug", slug)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "modified", modified)

    def __setattr__(self, name, value):
        raise AttributeError("Inbox is read-only")

    def __eq__(self, other):
        if type(other) is not type(self):
            return NotImplemented
        return (self.slug, self.path, self.modified) == (other.slug, other.path,
                                                         other.modified)

    def __hash__(self):
        return hash((self.slug, self.path, self.modified))

    def __repr__(self) -> str:
        return f"Inbox(slug={self.slug!r}, path={self.path!r}, modified={self.modified!r})"

    def as_dict(self) -> dict:
        return {"slug": self.slug, "path": str(self.path), "modified": self.modified}


def check_slug(slug: object) -> str:
    """`slug` when it keeps the slug rule; else `BadSlug` (a `ValueError`)."""
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise BadSlug(f"{slug!r} is not an inbox slug", SLUG_RULE)
    return slug


def inbox_dir(slug: str):
    """The folder of one-off `slug`, `<inbox>/<slug>`, whether or not it exists."""
    return _settings.read().inbox / check_slug(slug)


def make_inbox(slug: str):
    """Make one-off `slug`'s folder, and its parents; an existing folder is returned as
    it is."""
    path = inbox_dir(slug)
    try:
        os.makedirs(path, exist_ok=True)
    except FileExistsError:
        raise Refused(f"{path} exists and is not a folder",
                      "move it aside, or pick another slug") from None
    return path


def _stamp(seconds: float) -> str:
    import datetime

    when = datetime.datetime.fromtimestamp(seconds, datetime.UTC)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def inbox_list() -> list[Inbox]:
    """Every one-off, by slug: each folder in the inbox whose name is a slug. A symbolic
    link is not followed, and a file or a folder with another name is left out."""
    import stat

    inbox = _settings.read().inbox
    try:
        entries = list(os.scandir(inbox))
    except (FileNotFoundError, NotADirectoryError):
        return []
    out = []
    for entry in entries:
        if not SLUG.fullmatch(entry.name):
            continue
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            out.append(Inbox(entry.name, inbox / entry.name, _stamp(st.st_mtime)))
    return sorted(out, key=lambda one: one.slug)


def _project_key(name: str) -> str | None:
    """The project key `name` matches, ignoring case, or None; `Refused` when projects
    is installed and can't say."""
    import moxi.modules

    if not moxi.modules.available("projects"):
        return None
    found = moxi.modules.load("projects", interface=1)
    if found.state == "absent":
        return None
    if not found.ok:
        raise Refused(f"projects can't be read ({found.reason}), so notes can't check that "
                      f"{name!r} is no project key", "repair projects, then try again")
    try:
        return found.api.get(name).key
    except found.api.NotFound:
        return None
    except found.api.Unreadable as exc:
        raise Refused(f"projects can't be read ({exc.reason}), so notes can't check that "
                      f"{name!r} is no project key", exc.fix) from exc


def rename_inbox(slug: str, new: str):
    """Rename one-off `slug` to `new`; returns the new folder.

    Refuses (`BadSlug` or `Refused`, nothing changed) a bad slug on either side, a
    missing source, an existing target, and, with projects installed, a `new` that is a
    project key: a slug never shadows a key."""
    places = _settings.read()
    source = places.inbox / check_slug(slug)
    target = places.inbox / check_slug(new)
    if not source.is_dir() or source.is_symlink():
        raise Refused(f"no inbox one-off is named {slug!r}",
                      "`moxi notes inbox list` shows them")
    if os.path.lexists(target):
        raise Refused(f"{target} exists already",
                      "pick another slug; a rename never merges two folders")
    key = _project_key(new)
    if key is not None:
        raise Refused(f"{new!r} is the key of project {key!r}, and a slug never shadows a "
                      "project key", "pick another slug")
    os.rename(source, target)
    return target


__all__ = ["SLUG", "SLUG_RULE", "Inbox", "check_slug", "inbox_dir", "make_inbox",
           "inbox_list", "rename_inbox"]
