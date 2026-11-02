"""The opencode row in the core's harness table.

The core's `harnesses` setting names each harness the placer knows and its skills and
agents folders; it ships rows for claude and codex only. `SETUP` adds an opencode row,
`{name = "opencode", skills = "~/.config/opencode/skills", agents =
"~/.config/opencode/agents"}` (under `XDG_CONFIG_HOME` when that is set), through
`moxi.config.add_item`, when the table has no opencode row, and records it in the
ledger's `entries`:

    {"item": "harness:opencode", "harness": "opencode", "file": <the core's config file>,
     "kind": "harness-row", "key": ["harnesses", "opencode"], "written": <the row>,
     "prior": false}

When the config file didn't set `harnesses` before, the record also holds `made_key`,
the rows the key read then (its default), and uninstall unsets the key again when what is
left after the opencode row equals them.

A row already there when loadout first looks is the user's: it is recorded with `prior`
true, and loadout never removes it. A row loadout recorded is left as it is, edited or
not; uninstall reads the record to decide what to take out. A recorded row the user has
since removed is added again, as an entry found missing is.

The ledger is read and written under the core lock (`nested=True`, so it yields under
an install's claim), which `moxi loadout install` also holds while it reads and writes
the ledger; `SETUP` never takes `install.lock`.
"""

from __future__ import annotations

import os
from collections.abc import Callable

NAME = "opencode"
KIND = "harness-row"
ITEM = f"harness:{NAME}"
KEY = ["harnesses", NAME]
#: Who the core's config history names as making the change.
BY = "moxi install"


def wanted() -> dict:
    """The row loadout adds: OpenCode's skills and agents folders, written `~/...` when
    they are under the home folder."""
    from moxi_loadout import harness

    base = os.path.abspath(harness.opencode_home())
    home = os.path.abspath(os.path.expanduser("~"))
    if base.startswith(home.rstrip(os.sep) + os.sep):
        base = "~/" + os.path.relpath(base, home).replace(os.sep, "/")
    return {"name": NAME, "skills": f"{base}/skills", "agents": f"{base}/agents"}


def record_of(data: dict) -> dict | None:
    """The ledger's record of the opencode row, or None."""
    return next((r for r in data.get("entries", []) if isinstance(r, dict)
                 and r.get("kind") == KIND and r.get("key") == KEY), None)


def present() -> bool:
    """Whether the core's harness table has an opencode row, a row skipped for a fault
    included (a second row would be skipped as a repeat)."""
    from moxi import place

    rows, lines = place.harnesses()
    return NAME in rows or any(NAME in line for line in lines)


def add(*, say: Callable[[str], object] = print) -> str:
    """Add the opencode row when the table has none, and record it. Returns `added`,
    `prior` (the user's row, recorded as theirs), or `kept` (recorded already, or there
    and recorded). Raises `moxi.config.ConfigError` when the core's config can't be
    written, and `moxi_loadout.ledger.LedgerError` when the ledger can't be read."""
    from moxi import config, files, paths

    from moxi_loadout import ledger

    with files.lock(paths.core_lock(), nested=True):
        data = ledger.read()
        record = record_of(data)
        there = present()
        if there and record is not None:
            return "kept"
        if there:
            data["entries"].append(_record(_current() or wanted(), prior=True))
            ledger.write(data)
            say("loadout: the core's harness table has an opencode row already; "
                "loadout leaves it as yours")
            return "prior"
        row = wanted()
        try:
            made = file_rows() is None
        except ValueError:
            made = False   # add_item names the fault
        config.add_item("moxi", "harnesses", row, by=BY)
        new = _record(row, prior=False)
        if made:
            # The key held its default until now: what it held, so uninstall can unset
            # it again rather than leave the table pinned at that value.
            new["made_key"] = [r for r in (file_rows() or []) if r != row]
        data["entries"] = [r for r in data["entries"] if r is not record]
        data["entries"].append(new)
        ledger.write(data)
        say(f"loadout: added an opencode row to the core's harness table (skills in "
            f"{row['skills']}, agents in {row['agents']})")
        return "added"


def file_rows() -> list | None:
    """The `harnesses` list as the core's config file holds it, or None when the file
    doesn't set the key. Raises `ValueError` when the file is there and doesn't read."""
    import tomllib
    from pathlib import Path

    from moxi import paths

    path = Path(paths.core().config_file())
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path} can't be read ({exc})") from None
    rows = data.get("harnesses")
    return rows if isinstance(rows, list) else None


def _current() -> dict | None:
    """The opencode row as the placer reads it, with its folders as absolute paths."""
    from moxi import place

    rows, _ = place.harnesses()
    row = rows.get(NAME)
    if row is None:
        return None
    return {"name": NAME, "skills": "" if row.skills is None else os.fspath(row.skills),
            "agents": "" if row.agents is None else os.fspath(row.agents)}


def _record(row: dict, *, prior: bool) -> dict:
    from moxi import paths

    return {"item": ITEM, "harness": NAME, "file": os.fspath(paths.core().config_file()),
            "kind": KIND, "key": list(KEY), "written": dict(row), "prior": prior}


__all__ = ["NAME", "KIND", "ITEM", "KEY", "wanted", "record_of", "present", "add",
           "file_rows"]
