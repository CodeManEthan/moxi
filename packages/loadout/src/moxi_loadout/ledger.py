"""loadout's ledger, `STATE/loadout/ledger.json`: what the last install placed, and from where.

    {"source": "<the loadout folder last installed from>",
     "placed": {<target key>: <record>},
     "entries": [...]}

- `source` is the folder the last install read. A placement loadout made stays loadout's
  after the folder moves: a link under this old folder is loadout's own.
- `placed` holds one record per placement loadout made through the core placer, keyed as
  the placer keys its ledger (`moxi.place.target_key`). A record names the manifest item
  it came from (`section`, `kind`, `item`, `harness`) and the `moxi.place.Item` last
  applied for it (`place`), so a run that can't resolve the item this time passes that
  same `Item` and the placer keeps the placement. An adopted module skill also records
  its `module` and whether the hand-over was `seeded` (placed from the manifest) or not
  (the module's own yes). A placement with no record reads unseeded.
- `entries` holds one record per harness entry loadout wrote or found already there
  (`moxi_loadout.entries`): `{item, harness, file, kind, key, written, prior}`, and `sent`
  for an MCP server written through its CLI. `prior` marks an entry the user had first,
  which loadout never removes.
- Earlier releases kept `homes` and `git` here, a record of the harness homes a page job
  compared; Moxi's own record of the terminal's environment replaced it, and the next
  write drops both keys.

The file is written whole, through `moxi.files.atomic_write` (0600), under the core
lock: `install` reads and writes it under `install.lock` and then the core lock, and
`SETUP`, which runs under an install's claim on the core lock and never takes
`install.lock`, records the opencode row under the core lock alone. A missing file reads empty; a file that doesn't read as a JSON object raises
`LedgerError` naming the fix.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

#: The section name a record of the user section carries.
USER = "user"
#: Keys earlier releases wrote, which a write drops.
RETIRED = ("homes", "git")


class LedgerError(Exception):
    """The ledger can't be read."""

    exit_code = 1


def read() -> dict:
    """The ledger as plain data, with `source` (str or None), `placed` and `entries`."""
    from moxi_loadout import stores

    path = stores.ledger_path()
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as exc:
        raise LedgerError(f"{path} can't be read ({exc}); move it aside and run install "
                          "again, which starts a new one") from None
    if not isinstance(data, dict):
        raise LedgerError(f"{path} is not a JSON object; move it aside and run install "
                          "again, which starts a new one")
    data.setdefault("source", None)
    placed = data.get("placed")
    data["placed"] = {k: v for k, v in placed.items() if isinstance(v, dict)} \
        if isinstance(placed, dict) else {}
    data.setdefault("entries", [])
    return data


def write(data: Mapping) -> None:
    """Write the ledger whole. The caller holds the core lock (and `install.lock`, for an
    install)."""
    from moxi.files import atomic_write

    from moxi_loadout import stores

    stores.state_dir(create=True)
    whole = {k: v for k, v in data.items() if k not in RETIRED}
    atomic_write(stores.ledger_path(),
                 json.dumps(whole, indent=1, sort_keys=True) + "\n")


def item_record(item) -> dict:
    """A `moxi.place.Item` as plain data."""
    return {"source": os.fspath(item.source), "harness": item.harness, "name": item.name,
            "kind": item.kind, "dest": None if item.dest is None else os.fspath(item.dest),
            "held": bool(item.held),
            "root": None if item.root is None else os.fspath(item.root)}


def item_of(record: Mapping):
    """The `moxi.place.Item` a record holds, or None when it holds none that reads."""
    from moxi.place import Item

    place = record.get("place")
    if not isinstance(place, dict):
        return None
    try:
        return Item(Path(place["source"]), place["harness"], place["name"],
                    place.get("kind", "skill"),
                    dest=None if place.get("dest") is None else Path(place["dest"]),
                    held=bool(place.get("held")),
                    root=None if place.get("root") is None else Path(place["root"]))
    except (KeyError, TypeError):
        return None


__all__ = ["USER", "LedgerError", "read", "write", "item_record", "item_of"]
