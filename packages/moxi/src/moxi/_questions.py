"""`questions.json`: which install questions each module has had answered.

`STATE/moxi/questions.json` holds `{"<module>": {"<key>": {"at": <UTC>}}}`, written with
`moxi.files.update`, beside `units.json`. The settle step records a key after it has
written the key's setting (or found the key already set in the config file), so an
install cut short between the two leaves the answer in the file, and the next run finds
the key set and records it with no question. A recorded key is never asked again and
never reset by an install, so an upgrade asks only the keys it adds. `moxi uninstall`
drops the module's record. Only the record is kept here, never the answer.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

FILE = "questions.json"


def path() -> Path:
    from . import paths

    return paths.core().state_dir() / FILE


def read() -> dict[str, dict]:
    """Every module's record; an empty table when the file is missing or unreadable."""
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def recorded(module: str) -> set[str]:
    """The keys recorded for `module`."""
    keys = read().get(module)
    return set(keys) if isinstance(keys, dict) else set()


def record(module: str, key: str) -> None:
    """Record `module`'s question `key` as answered."""
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def add(data: dict) -> None:
        keys = data.get(module)
        if not isinstance(keys, dict):
            keys = data[module] = {}
        keys[key] = {"at": at}
    _change(add)


def forget(module: str) -> None:
    """Drop `module`'s record (uninstall)."""
    if not path().exists():
        return
    _change(lambda data: data.pop(module, None))


def _change(fn) -> None:
    from . import files

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
