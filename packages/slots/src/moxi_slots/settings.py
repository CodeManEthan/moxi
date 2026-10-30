"""slots' config, read through `moxi.config` at each use.

Keys: `tmux`, the tmux binary, which `SETUP` turns into the absolute path every slots
process uses; `tmux_conf`, a config file for the server (None: the user's own).

No slots process looks tmux up on a PATH at run time. `tmux_path()` returns the recorded
path only when it is absolute; anything else is a setup that has not run (or ran before
the binary moved), and the error names `moxi install slots`.
"""

from __future__ import annotations

import os

from moxi.config import ConfigError, Schema, Settings, load

from .errors import TmuxError
from .places import MODULE

SCHEMA_RESOURCE = "settings.toml"
#: The `by` that `SETUP` writes the tmux path under, in the core's settings history.
SETUP_BY = "slots setup"
#: The command that records tmux's path again.
REINSTALL = f"moxi install {MODULE}"

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


class NotSetUp(TmuxError):
    """tmux's path is not recorded, or no longer names a program."""


def tmux_path() -> str:
    """The recorded absolute path of tmux. Raises `NotSetUp` when the setting is not an
    absolute path to an executable file, or the config file cannot be read."""
    try:
        value = current()["tmux"]
    except ConfigError as exc:
        raise NotSetUp(f"slots' config cannot be read: {exc}") from None
    if not os.path.isabs(value):
        raise NotSetUp(f"tmux's path is not recorded (the setting reads {value!r}); "
                       f"run `{REINSTALL}` to record it")
    if not (os.path.isfile(value) and os.access(value, os.X_OK)):
        raise NotSetUp(f"the recorded tmux, {value}, is not an executable file; run "
                       f"`{REINSTALL}` to look it up again")
    return value


def tmux_conf() -> str | None:
    """The server's config file, or None for the user's own usual config."""
    value = current()["tmux_conf"]
    return str(value) if value else None


__all__ = ["SCHEMA_RESOURCE", "SETUP_BY", "REINSTALL", "NotSetUp", "schema", "current",
           "tmux_path", "tmux_conf"]
