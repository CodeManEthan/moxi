"""The fixture's `CONFIG_CHECK`: a check across two keys, as a module's own loader may
make one. It refuses more `workers` than `limit`."""

from __future__ import annotations

import tomllib

from moxi.config import ConfigError


def check(text: str) -> None:
    data = tomllib.loads(text)
    workers, limit = data.get("workers", 2), data.get("limit", 10)
    if isinstance(workers, int) and isinstance(limit, int) and workers > limit:
        raise ConfigError(None, "workers", f"is {workers}, more than limit ({limit})",
                          "set workers to limit or less")
