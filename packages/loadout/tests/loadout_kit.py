"""Helpers loadout's install tests share: a loadout folder with items in it, and readers
of what the core placer and loadout recorded."""

from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

from moxi.paths import link_target

#: The form the core's placer gives a skill's folder by default: a link, and on Windows,
#: where a folder links only as a junction the `skills.form` setting asks for, a copy.
DIR_FORM = "copy" if sys.platform == "win32" else "link"

#: The step ui's line gives for the browser shell to take a change (`moxi_ui.terminal.where`).
UI_STEP = ("log out and back in" if sys.platform in ("darwin", "win32")
           else "moxi ui stop")


def configure(loadout_dir: Path) -> Path:
    """Make `loadout_dir` the configured loadout folder; return it."""
    from moxi import config

    loadout_dir.mkdir(parents=True, exist_ok=True)
    config.set_value("loadout", "source", str(loadout_dir), by="test")
    return loadout_dir


def manifest(loadout_dir: Path, text: str) -> Path:
    path = loadout_dir / "loadout.toml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def skill(loadout_dir: Path, name: str, body: str = "Do the thing.\n") -> Path:
    path = loadout_dir / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: A skill.\n---\n{body}",
                                   encoding="utf-8")
    return path


def agent(path: Path, name: str, extra: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: An agent.\n{extra}---\nYou help.\n",
                    encoding="utf-8")
    return path


def _same_path(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    def norm(p):
        return os.path.normcase(os.path.normpath(os.fspath(p)))
    return norm(a) == norm(b)


def placed(path: Path, source: Path | str | None = None) -> bool:
    """Whether the placer's own form stands at `path`, of `source` when it is named: a link
    to it, or, on Windows, a copy (a folder holding the placer's mark, or a file with the
    source's bytes where the account can't make symlinks)."""
    from moxi import place

    path = Path(path)
    if path.is_symlink():
        return source is None or _same_path(link_target(path), source)
    if sys.platform != "win32":
        return False
    if path.is_dir():
        mark = path / place.MARK
        if not mark.is_file():
            return False
        named = json.loads(mark.read_text(encoding="utf-8")).get("source")
        return source is None or (named is not None and _same_path(named, source))
    return path.is_file() and (source is None
                               or path.read_bytes() == Path(source).read_bytes())


def placement_of(path: Path) -> str | bytes:
    """What the placer left at `path`, to compare across a run: a link's text, or a copied
    folder's mark (which holds the copy's hash)."""
    from moxi import place

    path = Path(path)
    return link_target(path) if path.is_symlink() else (path / place.MARK).read_bytes()


def unplace(path: Path) -> None:
    """Remove what the placer put at `path`: a link, or a folder's copy (`DIR_FORM`)."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


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
