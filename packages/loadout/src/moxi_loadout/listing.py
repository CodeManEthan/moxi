"""`moxi loadout list`: every item the manifest lists, with its kind, scope, harnesses and
source, read from the manifest alone (nothing is planned)."""

from __future__ import annotations

import os

from moxi_loadout.manifest import USER, is_module_skill

_FOLDERS = {"skill": "skills", "agent": "agents"}


def entries(folder=None) -> list[dict]:
    """One entry per item per section that lists it: `kind`, `name`, `scope` (`user` or
    the project section's name), `harnesses` (None for every harness), `source` and
    `disabled` (the harnesses switched off there, `*` for all)."""
    from moxi_loadout import manifest as manifest_mod

    found = manifest_mod.load(folder)
    out = []
    for section in found.sections():
        scope = USER if section.is_user else section.name
        for kind, names in section.lists.items():
            for name in names:
                out.append({
                    "kind": kind, "name": name, "scope": scope,
                    "harnesses": found.harnesses(kind, name),
                    "source": _source(found, kind, name, section),
                    "disabled": sorted(i.harness for i in section.disabled
                                       if (i.kind, i.name) == (kind, name)),
                })
    return out


def _source(found, kind: str, name: str, section) -> str:
    if kind == "skill" and is_module_skill(name):
        return f"module {name.partition('/')[0]}"
    own = found.source_of(kind, name)
    if own is not None:
        return os.fspath(own)
    if kind not in _FOLDERS:
        return os.fspath(found.path)
    filename = name + (".md" if kind == "agent" else "")
    mine = found.folder / _FOLDERS[kind] / filename
    if os.path.lexists(mine) or section.is_user:
        return os.fspath(mine)
    return "held by the project"


__all__ = ["entries"]
