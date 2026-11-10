"""The shell's settings, read through `moxi.config`: `port`, `idle_s`, `theme.mode` and
`theme.name`."""

from __future__ import annotations

from moxi.config import Schema, Settings, load

MODULE = "ui"
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


def port(settings: Settings | None = None) -> int:
    """The shell's port. Raises `ValueError` for one outside 1 to 65535."""
    value = (settings or current())["port"]
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"ui's port is {value!r}, not a port from 1 to 65535; "
                         "fix it with `moxi config set ui port N`")
    return value


def idle_s(settings: Settings | None = None) -> int:
    """Seconds a socket-started shell waits with nothing open before it exits."""
    value = (settings or current())["idle_s"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"ui's idle_s is {value!r}, not a whole number of seconds above 0")
    return value


#: The theme modes `theme.mode` takes.
THEME_MODES = ("system", "light", "dark")


def theme_mode(settings: Settings | None = None) -> str:
    """The pages' theme mode, `system` when the file can't be read or holds no mode."""
    try:
        value = (settings or current())["theme.mode"]
    except Exception:  # A broken config file must not stop the document from loading.
        return "system"
    return value if value in THEME_MODES else "system"


#: The themes `theme.name` takes, the default first: the themes the web build's
#: tokens.json names.
THEME_NAMES = ("plain", "midnight-neon")


def theme_name(settings: Settings | None = None) -> str:
    """The pages' theme, the default when the file can't be read or holds no theme."""
    try:
        value = (settings or current())["theme.name"]
    except Exception:  # A broken config file must not stop the document from loading.
        return THEME_NAMES[0]
    return value if value in THEME_NAMES else THEME_NAMES[0]
