"""rounds' config, read through `moxi.config` at each use.

The schema is `settings.toml` beside this file. `load()` gives the settings as they
stand. Every key is read at its use, so an edit takes effect at the next command.

`check_text(text)` is the module's whole loader over a config text, the announcement's
`CONFIG_CHECK`. It reads the text against the schema. `flows`, `models` and `projects`
are tables the schema gives no item type, so the schema takes whatever tables they hold.
"""

from __future__ import annotations

from moxi.config import Schema

from .paths import MODULE

SCHEMA_RESOURCE = "settings.toml"

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


def load():
    """rounds' settings as they stand. Raises `moxi.config.ConfigError`."""
    from moxi import config

    return check(config.load(MODULE, schema()))


def check_text(text: str) -> None:
    """The module's whole loader over config text `text`. Raises
    `moxi.config.ConfigError` when it refuses."""
    from moxi import config

    check(config.check_text(MODULE, schema(), text))


def check(settings):
    """`settings` as read against the schema, returned as given: rounds adds no rule to
    the schema's. `load` and `check_text` both pass what they read through here."""
    return settings


__all__ = ["MODULE", "SCHEMA_RESOURCE", "schema", "load", "check_text", "check"]
