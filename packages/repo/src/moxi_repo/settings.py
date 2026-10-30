"""repo's config, read through `moxi.config` at each use.

Keys: `git_hooks.mode`, the mode a repo gets when it pins none (`auto`, `config` or
`shim`). Every value scoped to one repo lives in that repo's git config instead.
"""

from __future__ import annotations

from moxi.config import Schema

MODULE = "repo"
SCHEMA_RESOURCE = "settings.toml"

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema
