"""The connector's config, read through `moxi.config`.

The schema is `settings.toml` beside this file, governing `CONFIG/connector/config.toml`.
`check(text)` is the module's whole loader over a config text, the announcement's
`CONFIG_CHECK`: the schema's check, then the rules the schema can't say, so `moxi config
set` never writes a file the connector would refuse:

- `public_url` is empty, or `https://` with a DNS name and no path (`origin.parse`);
- `port` is a port, 1 to 65535;
- each of `redirect_uris` is an absolute `https://` URL with no fragment;
- `refresh_days` is at least 1.

`enabled` is the daemon's switch, read at each use. `port`, `redirect_uris` and
`refresh_days` are read once, at the process's start. `public_url` is read with every read
of the origin (`origin.read`).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from moxi import config
from moxi.config import ConfigError, Schema

from . import origin

MODULE = "connector"
SCHEMA_RESOURCE = "settings.toml"

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


def load() -> config.Settings:
    """The module's settings as the config file gives them now."""
    return config.load(MODULE, schema())


def enabled() -> bool:
    """The daemon's switch, read now."""
    return bool(load()["enabled"])


def check(text: str) -> None:
    """Refuse config text `text` that the schema passes and the connector would not take."""
    settings = config.check_text(MODULE, schema(), text)
    public_url = settings["public_url"]
    if public_url:
        try:
            origin.parse(public_url)
        except origin.BadOrigin as e:
            raise ConfigError(None, "public_url", str(e),
                              "set https://<name> or https://<name>:<port>, with no "
                              "path, or leave it empty to use tunnel's address") from None
    port = settings["port"]
    if not 0 < port < 65536:
        raise ConfigError(None, "port", f"{port} is not a port", "set a port from 1 to 65535")
    for uri in settings["redirect_uris"]:
        parts = urlsplit(uri)
        if parts.scheme != "https" or not parts.netloc or parts.fragment:
            raise ConfigError(None, "redirect_uris", f"{uri!r} is not an https:// URL "
                              "without a fragment", "list Claude's callback URLs")
    if settings["refresh_days"] < 1:
        raise ConfigError(None, "refresh_days", "a sign-in must last at least a day",
                          "set 1 or more")


__all__ = ["MODULE", "check", "enabled", "load", "schema"]
