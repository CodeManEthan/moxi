"""`units.json`: the declaration digest of each switched timer the settle step applied.

`STATE/moxi/units.json` holds `{"<module>/<timer>": {"digest": <sha256>, "at": <UTC>}}`,
written with `moxi.files.update`. Only the settle step writes an entry, after it has
written the timer's switch; `apply` writes a switched timer only when its entry holds the
change's digest, so no other path makes a changed timer fire before its switch was reset.
A daemon switch, a switchless timer and a wrap's key have no entry.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

FILE = "units.json"


def path() -> Path:
    from .. import paths

    return paths.core().state_dir() / FILE


def read() -> dict[str, dict]:
    """Every entry; an empty table when the file is missing or unreadable."""
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def recorded(module: str, timer: str) -> str | None:
    """The digest recorded for `module`'s timer `timer`, or None."""
    entry = read().get(f"{module}/{timer}")
    digest = entry.get("digest") if isinstance(entry, dict) else None
    return digest if isinstance(digest, str) else None


def record(module: str, timer: str, digest: str) -> None:
    """Record `digest` as applied for `module`'s timer `timer` (the settle step, after it
    wrote the switch)."""
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _change(lambda data: data.__setitem__(f"{module}/{timer}",
                                          {"digest": digest, "at": at}))


def forget(module: str) -> None:
    """Drop every entry of `module` (uninstall)."""
    prefix = f"{module}/"
    if not path().exists():
        return

    def drop(data: dict) -> None:
        for key in [k for k in data if k.startswith(prefix)]:
            del data[key]
    _change(drop)


def _change(fn) -> None:
    from .. import files

    def edit(old: bytes | None) -> bytes:
        try:
            data = json.loads(old) if old else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        fn(data)
        return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")

    files.update(path(), edit)
