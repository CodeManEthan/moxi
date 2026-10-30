"""turns' settings, read through `moxi.config` from `CONFIG/turns/config.toml`.

The schema is `settings.toml` in this package (the announcement's `SETTINGS`). One more
input sits outside the file: `MOXI_TURNS_COMMANDS`, a launcher's grant of session commands
to one launch, read from the environment the endpoint inherits from its session.

turns refuses nothing its schema passes, so it declares no `CONFIG_CHECK`: a name in
`commands.allow` that is not a session command grants nothing (the doctor warns of it),
and a start-list entry that names no installed module or readable file adds nothing (the
doctor names it).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from moxi import config as moxi_config

from .results import COMMANDS

MODULE = "turns"
#: A launcher stamps `clear,exit` (or any of the session commands) into one launch's
#: environment.
COMMANDS_ENV = "MOXI_TURNS_COMMANDS"
#: The stop handler's wait.
STOP_WAIT = "stop_handler.wait_ms"


def schema() -> moxi_config.Schema:
    return moxi_config.Schema.from_resource(__package__, "settings.toml")


def reader(env: Mapping[str, str] | None = None) -> moxi_config.Config:
    """A started-on-demand `moxi.config.Config` for turns."""
    return moxi_config.Config(MODULE, schema(), env=env)


def load(env: Mapping[str, str] | None = None) -> moxi_config.Settings:
    """turns' settings now. Raises `moxi.config.ConfigError`."""
    return reader(env).start()


def allowed(settings_allow: object, env: Mapping[str, str]) -> frozenset[str]:
    """The session commands consented to: the config's `commands.allow` and the launch's
    stamp together. Names that are not session commands are dropped."""
    names: set[str] = set()
    if isinstance(settings_allow, (list, tuple)):
        names.update(n for n in settings_allow if isinstance(n, str))
    stamp = env.get(COMMANDS_ENV, "")
    names.update(part.strip() for part in stamp.split(","))
    return frozenset(n for n in names if n in COMMANDS)


def unknown_commands(settings_allow: Iterable[object]) -> list[str]:
    """The names in `commands.allow` that are not session commands, which grant nothing."""
    return [str(n) for n in settings_allow if n not in COMMANDS]


__all__ = ["MODULE", "COMMANDS_ENV", "STOP_WAIT", "schema", "reader", "load", "allowed",
           "unknown_commands"]
