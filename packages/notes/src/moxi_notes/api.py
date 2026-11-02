"""The notes API, interface 1: where a session's notes live, its handoffs folder, the
inbox one-offs and the frontmatter reader.

A module reaches it through the core, `available()` first and `load()` only on yes:

    found = (moxi.modules.load("notes", interface=1)
             if moxi.modules.available("notes") else None)
    if found is None or not found.ok:
        ...  # no notes
    folder = found.api.handoffs_dir(cwd, create=True, override=notes_dir)

The calls:

- `root()`: the notes root, `~` expanded.
- `notes_dir(path, *, override=None)`: the notes directory for a session in `path`, as a
  `Resolution(dir, rule, warning)`; `dir` None means none (see `moxi_notes.resolve`).
- `handoffs_dir(path, *, create=False, override=None)`: that directory's `handoffs`
  folder, or None; `create=True` makes it, parents included.
- `notes_root_for(root, kind="project", key=None)`: the notes folder a project of `kind`
  at `root` gets by default, or None.
- `inbox_dir(slug)`, `make_inbox(slug)`, `inbox_list()`, `rename_inbox(slug, new)`: the
  inbox one-offs (see `moxi_notes.inbox`).
- `frontmatter(text)`: a note's header, as `Frontmatter(present, ok, reason, fields, order,
  start, end)`; it never raises.
- `upsert_frontmatter(text, key, value, *, order=...)`: `(text, changed)` with `key` added
  as one line when the header lacks it; a key that is there is never replaced.

Nothing here reads the process environment for the override: the caller passes it, so a
launch inside an agent session never hands a child its parent's folder. Paths are
absolute; a relative one raises `ValueError`. notes' config that can't be read raises
`moxi.config.ConfigError`; a refusal raises `moxi_notes.errors.Refused`.
"""

from __future__ import annotations

import os

from . import settings as _settings
from .errors import BadSlug, NotesError, Refused
from .inbox import Inbox, inbox_dir, inbox_list, make_inbox, rename_inbox
from .resolve import Resolution, notes_dir, notes_root_for

#: The folder under a notes directory that holds a project's handoffs.
HANDOFFS = "handoffs"


def root():
    """The notes root, `~` expanded, as a `Path`."""
    return _settings.read().root


def handoffs_dir(path, *, create: bool = False, override=None):
    """The `handoffs` folder of the notes directory for `path` and `override`, or None
    when the resolver gives none. With `create`, the folder (and its parents) is made."""
    found = notes_dir(path, override=override).dir
    if found is None:
        return None
    folder = found / HANDOFFS
    if create:
        try:
            os.makedirs(folder, exist_ok=True)
        except FileExistsError:
            raise Refused(f"{folder} exists and is not a folder",
                          "move it aside so notes can make the handoffs folder") from None
    return folder


def frontmatter(text: str):
    """The header of a note's `text`, as a `Frontmatter`. Never raises."""
    from .frontmatter import parse

    return parse(text)


def upsert_frontmatter(text: str, key: str, value, *, order=None) -> tuple[str, bool]:
    """`(text, changed)`: `text` with `key: value` added as one line when its header
    lacks the key, or a header made to hold it. `order` is the required keys' canonical
    order (default: the five). A key that is there is never replaced, and a header that
    can't be read is never written into."""
    from .frontmatter import DEFAULT_REQUIRED, upsert

    return upsert(text, key, value, order=DEFAULT_REQUIRED if order is None else order)


__all__ = ["HANDOFFS", "Resolution", "Inbox", "NotesError", "Refused", "BadSlug", "root",
           "notes_dir", "handoffs_dir", "notes_root_for", "inbox_dir", "make_inbox",
           "inbox_list", "rename_inbox", "frontmatter", "upsert_frontmatter"]
