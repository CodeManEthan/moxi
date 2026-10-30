"""The launcher's config, read through `moxi.config` at each use.

Six keys, all read at the point of use: `ready.wait_s` (how long a launch waits for the
session to be ready), `first.wait_s` (how long it waits for the first turn to start),
`startup.wait_s`, `startup.trust_dialog` and `startup.dev_channels_dialog` (the startup
screens a slot launch may answer, which need slots), and `runs.keep_days` (how long a
launch's run files are kept). Seats and project presets are a file of their own,
`presets.toml`, not keys here.
"""

from __future__ import annotations

from moxi.config import Schema, Settings, load

MODULE = "launcher"
SCHEMA_RESOURCE = "settings.toml"

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


def current() -> Settings:
    """The settings now. Raises `moxi.config.ConfigError` on a file that cannot be read."""
    return load(MODULE, schema())
