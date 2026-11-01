"""notes' config, read through `moxi.config` at each use.

The schema is `settings.toml` beside this file. `read()` gives the keys the resolver and
the inbox use as paths: the notes root, the base (the root joined with `prefix`), the
topics and inbox folders under the base, and the `exclude` folders, each with `~`
expanded. Every key is read at its use, so an edit takes effect at the next read.

`check_text(text)` is the module's whole loader over a config text, the announcement's
`CONFIG_CHECK`: the schema's check, then the two rules the schema can't say. `prefix` is a
relative folder that stays under the root (no `..`, no drive, not absolute), and each
`exclude` entry is an absolute path once `~` is expanded.

Standard library only at import; `moxi.config` is light and `pathlib` is imported inside
the calls that build paths.
"""

from __future__ import annotations

import os
import re

from moxi.config import ConfigError, Schema

MODULE = "notes"
SCHEMA_RESOURCE = "settings.toml"

#: The folder names under the base that hold topics and inbox one-offs.
TOPICS = "topics"
INBOX = "inbox"

_SEPARATORS = re.compile(r"[\\/]+")

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


class Places:
    """The settings as paths. `root`, `base`, `topics` and `inbox` are `Path`s;
    `exclude` is a tuple of absolute path strings; `settings` is the `Settings` read."""

    __slots__ = ("settings", "root", "base", "topics", "inbox", "exclude")

    def __init__(self, settings):
        from pathlib import Path

        self.settings = settings
        self.root = Path(os.path.normpath(settings["root"]))
        parts = prefix_parts(settings["prefix"], settings.file)
        self.base = self.root.joinpath(*parts)
        self.topics = self.base / TOPICS
        self.inbox = self.base / INBOX
        self.exclude = exclude_paths(settings["exclude"], settings.file)

    def __repr__(self) -> str:
        return f"Places(root={str(self.root)!r}, base={str(self.base)!r})"


def read() -> Places:
    """notes' settings as they stand, as paths. Raises `moxi.config.ConfigError`."""
    from moxi import config

    return Places(config.load(MODULE, schema()))


def check_text(text: str) -> None:
    """The module's whole loader over config text `text`. Raises `ConfigError` when it
    refuses."""
    from moxi import config

    settings = config.check_text(MODULE, schema(), text)
    prefix_parts(settings["prefix"], None)
    exclude_paths(settings["exclude"], None)


def prefix_parts(prefix: str, file) -> tuple[str, ...]:
    """`prefix` as folder names under the root, or `ConfigError`."""
    if not prefix:
        return ()
    if os.path.isabs(prefix) or prefix[:1] in ("/", "\\") or re.match(r"^[A-Za-z]:", prefix):
        raise ConfigError(file, "prefix", f"is the absolute path {prefix!r}",
                          "give a folder name under the root, such as your user name")
    parts = tuple(p for p in _SEPARATORS.split(prefix) if p not in ("", "."))
    if ".." in parts:
        raise ConfigError(file, "prefix", f"{prefix!r} climbs out of the root with ..",
                          "give a folder name under the root, such as your user name")
    return parts


def exclude_paths(entries, file) -> tuple[str, ...]:
    """Each `exclude` entry with `~` expanded and normalized, or `ConfigError`."""
    out = []
    for i, entry in enumerate(entries):
        expanded = os.path.expanduser(entry)
        if not entry or not os.path.isabs(expanded):
            raise ConfigError(file, "exclude", f"item {i + 1} is the relative path "
                                               f"{entry!r}",
                              "give an absolute path, or one starting with ~")
        out.append(os.path.normpath(expanded))
    return tuple(out)


__all__ = ["MODULE", "TOPICS", "INBOX", "Places", "schema", "read", "check_text"]
