"""Helpers loadout's install tests share: a loadout folder with items in it, and readers
of what the core placer and loadout recorded."""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path


def configure(folder: Path) -> Path:
    """Make `folder` the configured loadout folder; return it."""
    from moxi import config

    folder.mkdir(parents=True, exist_ok=True)
    config.set("loadout", "source", str(folder), by="test")
    return folder


def manifest(folder: Path, text: str) -> Path:
    path = folder / "loadout.toml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def skill(folder: Path, name: str, body: str = "Do the thing.\n") -> Path:
    path = folder / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: A skill.\n---\n{body}",
                                   encoding="utf-8")
    return path


def agent(path: Path, name: str, extra: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: An agent.\n{extra}---\nYou help.\n",
                    encoding="utf-8")
    return path


def claude(kind: str = "skills") -> Path:
    return Path(os.environ["CLAUDE_CONFIG_DIR"]) / kind


def codex_skills() -> Path:
    return Path(os.environ["HOME"]) / ".agents" / "skills"


def codex_agents() -> Path:
    return Path(os.environ["CODEX_HOME"]) / "agents"


def core_ledger() -> dict:
    from moxi import paths, place

    path = paths.core().state_dir() / place.LEDGER
    return json.loads(path.read_text()) if path.exists() else {}


def owner_of(target: Path) -> str | None:
    from moxi.place import target_key

    key = target_key(target)
    for owner, record in core_ledger().items():
        if key in record.get("placed", {}):
            return owner
    return None


def handed(owner: str) -> dict:
    return core_ledger().get(owner, {}).get("handed", {})
