"""sessions' config, read through `moxi.config` at each use.

Three keys, each a root read when the caller hands none in: `claude.projects` (Claude
Code's projects root), `codex.sessions` (Codex's sessions root) and `opencode.database`
(OpenCode's database). Unset, each is the harness's own default in the reading process's
environment.
"""

from __future__ import annotations

from pathlib import Path

from moxi.config import Schema, Settings, load

MODULE = "sessions"
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


def configured_projects() -> Path | None:
    """`claude.projects` when the file or its default sets it, else None."""
    return current()["claude.projects"]


def configured(key: str) -> Path | None:
    """`key` (`claude.projects`, `codex.sessions` or `opencode.database`) when the file or
    its default sets it, else None."""
    return current()[key]
