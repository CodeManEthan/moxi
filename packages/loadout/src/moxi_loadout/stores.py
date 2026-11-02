"""Where loadout keeps its files, and the lock files it holds.

The announcement's `STORES` declares the four stores; this module gives their paths, each
from `moxi.paths.Layout("loadout")` under the one Moxi home:

- `source`, the default loadout folder, `DATA/loadout/source/`, used when the setting
  `source` is empty. It is the user's own data and often a git work tree, so loadout
  never creates it private and never leaves a lock file in it.
- `ledger`, `STATE/loadout/ledger.json`: what loadout placed and wrote.
- `generated`, `STATE/loadout/generated/`: the Codex and OpenCode forms of agent files.
- `backups`, `DATA/loadout/backups/`: each harness file before loadout rewrites it.

Lock files, all in loadout's state directory, so none lands in a folder the user keeps:

- `install.lock`, one install at a time;
- `manifest.lock`, held by every edit of the manifest, whoever makes it;
- `locks/<name>-<hash>.lock`, one per harness TOML file loadout edits, named from the
  file's whole path, so two files of one name never share a lock.

`moxi.files.lock` creates a lock file but no folder for it, so each lock path here comes
from a call that makes its folder first (`manifest_lock()`, `harness_lock()`,
`install_lock()`).

Standard library only at import.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

MODULE = "loadout"
MANIFEST = "loadout.toml"
LEDGER = "ledger.json"
GENERATED = "generated"
BACKUPS = "backups"
SOURCE = "source"
INSTALL_LOCK = "install.lock"
MANIFEST_LOCK = "manifest.lock"
LOCKS = "locks"


def _layout():
    from moxi.paths import Layout

    return Layout(MODULE)


def data_dir() -> Path:
    """`DATA/loadout/`, not created."""
    return _layout().data_dir()


def state_dir(*, create: bool = False) -> Path:
    """`STATE/loadout/`; with `create`, made private (0700) when missing."""
    path = _layout().state_dir()
    if create:
        from moxi.files import private_dir

        private_dir(path)
    return path


def default_source() -> Path:
    """The default loadout folder, `DATA/loadout/source/`, not created."""
    return data_dir() / SOURCE


def source_dir() -> Path:
    """The loadout folder on this machine: the setting `source`, else the default.
    Raises `moxi.config.ConfigError` when loadout's config doesn't read."""
    from moxi_loadout import settings

    return settings.read().source


def manifest_path(source: str | os.PathLike | None = None) -> Path:
    """The manifest, `loadout.toml` in `source` (the configured folder when None)."""
    return Path(source if source is not None else source_dir()) / MANIFEST


def ledger_path() -> Path:
    """`STATE/loadout/ledger.json`, not created."""
    return state_dir() / LEDGER


def generated_dir() -> Path:
    """`STATE/loadout/generated/`, not created."""
    return state_dir() / GENERATED


def backups_dir() -> Path:
    """`DATA/loadout/backups/`, not created."""
    return data_dir() / BACKUPS


def install_lock() -> Path:
    """`STATE/loadout/install.lock`, its folder made."""
    return state_dir(create=True) / INSTALL_LOCK


def manifest_lock() -> Path:
    """`STATE/loadout/manifest.lock`, its folder made. Every edit of the manifest holds
    this one lock, so none is left beside the manifest."""
    return state_dir(create=True) / MANIFEST_LOCK


def harness_lock(path: str | os.PathLike) -> Path:
    """The lock for harness TOML file `path`, `STATE/loadout/locks/<name>-<hash>.lock`,
    its folder made. The hash is of the file's absolute path, `~` expanded."""
    from moxi.files import private_dir

    whole = os.path.abspath(os.path.expanduser(os.fspath(path)))
    digest = hashlib.sha256(whole.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    folder = private_dir(state_dir(create=True) / LOCKS)
    return folder / f"{os.path.basename(whole) or 'file'}-{digest}.lock"


__all__ = ["MODULE", "MANIFEST", "data_dir", "state_dir", "default_source", "source_dir",
           "manifest_path", "ledger_path", "generated_dir", "backups_dir", "install_lock",
           "manifest_lock", "harness_lock"]
