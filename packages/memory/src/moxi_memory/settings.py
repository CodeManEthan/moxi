"""memory's config and its places on disk.

The schema is `settings.toml` beside this file, the file it governs is
`CONFIG/memory/config.toml`, and every key is read at its use: `read()` loads the file
again whenever it changed, so an edit takes effect at the next read.

`check_text(text)` is the module's whole loader over a config text, the announcement's
`CONFIG_CHECK`: the schema's check, then the rules the schema can't say. `harnesses`
names only harnesses memory reads, `index.tail_chars` is at least 1, `redact.sets`
names at least one set, `subconscious.limit_s` is 1 to `LIMIT_S_MAX` (so a round's bound,
`limit_s` plus 30 s, stays inside turns' longest timer), the subconscious's counts are at
least their floor (`FLOORS`), and `subconscious.fork_commands` names no empty command.

memory reads no environment variable of its own. Its folders move with the core's root
variables like every module's (`moxi.paths.Layout`): `state_dir()` holds the session table,
the writer lock and the rebuild lock; `cache_dir()` holds the index generations, in its
`index` folder, which can be deleted and rebuilt from the transcripts.

Standard library and `moxi.config` only at import.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from moxi.config import ConfigError, Schema

MODULE = "memory"
SCHEMA_RESOURCE = "settings.toml"

#: The harnesses memory can index, by sessions' names for them.
HARNESSES = ("claude", "codex", "opencode")
#: The longest `subconscious.limit_s` the check takes, in seconds.
LIMIT_S_MAX = 3600
#: The least each of the subconscious's counts may be.
FLOORS = {"subconscious.limit_s": 1, "subconscious.max_tools": 1,
          "subconscious.max_rounds": 1, "subconscious.max_points": 1,
          "subconscious.prefetch_hits": 0, "subconscious.on_demand_per_prompt": 0}

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


def read(env: Mapping[str, str] | None = None):
    """memory's settings as they stand (`moxi.config.Settings`). Raises `ConfigError`,
    also for a value the schema lets through but memory can't use."""
    from moxi import config

    settings = config.load(MODULE, schema(), env=env)
    _check(settings, settings.file)
    return settings


def check_text(text: str) -> None:
    """The module's whole loader over config text `text`. Raises `ConfigError` when it
    refuses."""
    from moxi import config

    _check(config.check_text(MODULE, schema(), text), None)


def _check(settings, file) -> None:
    unknown = [h for h in settings["harnesses"] if h not in HARNESSES]
    if unknown:
        raise ConfigError(file, "harnesses", f"names {', '.join(map(repr, unknown))}, "
                                             "which memory doesn't read",
                          f"use any of {', '.join(HARNESSES)}")
    if settings["index.tail_chars"] < 1:
        raise ConfigError(file, "index.tail_chars",
                          f"is {settings['index.tail_chars']}, below 1",
                          "give the number of characters to keep, such as 1000")
    sets = settings["redact.sets"]
    if not sets or any(not s.strip() for s in sets):
        raise ConfigError(file, "redact.sets", "names no set, or an empty one",
                          "name at least one secrets set, such as credentials")
    for key, floor in FLOORS.items():
        if settings[key] < floor:
            raise ConfigError(file, key, f"is {settings[key]}, below {floor}",
                              f"give a whole number of {floor} or more")
    if settings["subconscious.limit_s"] > LIMIT_S_MAX:
        raise ConfigError(file, "subconscious.limit_s",
                          f"is {settings['subconscious.limit_s']}, above {LIMIT_S_MAX}",
                          f"give a number of seconds from 1 to {LIMIT_S_MAX}, such as 60")
    commands = settings["subconscious.fork_commands"]
    if any(not c.split() for c in commands):
        raise ConfigError(file, "subconscious.fork_commands", "holds an empty command",
                          "give each command as its program and words, such as grep")


def layout():
    """memory's `moxi.paths.Layout`."""
    from moxi.paths import Layout

    return Layout(MODULE)


def state_dir(env: Mapping[str, str] | None = None) -> Path:
    """memory's state directory: the session table and the locks."""
    return layout().state_dir(env)


def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """memory's cache directory: the index generations and their pointer, in its `index`
    folder."""
    return layout().cache_dir(env)


__all__ = ["MODULE", "HARNESSES", "LIMIT_S_MAX", "FLOORS", "schema", "read", "check_text",
           "layout", "state_dir", "cache_dir"]
