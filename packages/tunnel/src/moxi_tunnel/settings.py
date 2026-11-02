"""tunnel's config, read through `moxi.config` at each use.

The schema is `settings.toml` beside this file. `check(text)` is the module's whole loader
over a config text, the announcement's `CONFIG_CHECK`: the schema's check, then the rules
the schema can't say, so `moxi config set` never writes a file tunnel would then refuse.

- `cloudflare.tunnel_id` is empty or a UUID;
- no path (`tailscale.binary`, `cloudflare.credentials`, `cloudflare.binary`) holds a
  control character;
- `cloudflare.metrics_port` is a port, 1 to 65535.

The Cloudflare writer refuses the same values again when it writes its file.
"""

from __future__ import annotations

import re

from moxi import config
from moxi.config import ConfigError, Schema

MODULE = "tunnel"
SCHEMA_RESOURCE = "settings.toml"

#: The path settings, each checked for a control character.
PATHS = ("tailscale.binary", "cloudflare.credentials", "cloudflare.binary")

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{12}")

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


def load() -> config.Settings:
    """The module's settings as the config file and environment give them now."""
    return config.load(MODULE, schema())


def has_control(text: str) -> bool:
    """True when `text` holds a control character (C0, DEL or C1)."""
    return any(ord(ch) < 0x20 or 0x7F <= ord(ch) < 0xA0 for ch in text)


def is_uuid(text: str) -> bool:
    return bool(_UUID.fullmatch(text))


def check(text: str) -> None:
    """Refuse config text `text` that the schema passes and tunnel would not take."""
    settings = config.check_text(MODULE, schema(), text)
    tunnel_id = settings["cloudflare.tunnel_id"]
    if tunnel_id and not is_uuid(tunnel_id):
        raise ConfigError(None, "cloudflare.tunnel_id", f"{tunnel_id!r} is not a UUID",
                          "set the id `cloudflared tunnel list` shows for the tunnel")
    for key in PATHS:
        value = settings[key]
        if value is not None and has_control(str(value)):
            raise ConfigError(None, key, "the path holds a control character",
                              "set the path without it")
    port = settings["cloudflare.metrics_port"]
    if not 0 < port < 65536:
        raise ConfigError(None, "cloudflare.metrics_port", f"{port} is not a port",
                          "set a port from 1 to 65535")


__all__ = ["MODULE", "schema", "load", "check", "has_control", "is_uuid"]
