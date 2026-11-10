"""rounds' locations: its own directories under Moxi's roots (`moxi.paths`).

    DATA/rounds      the index, and build worktrees unless `worktree_dir` names a folder
    STATE/rounds     the lock files, under `locks/`
    CACHE/rounds
    CONFIG/rounds    `config.toml`
    RUNTIME/rounds

Each root moves with its own variable; `MOXI_ROUNDS_HOME` collapses all five under one
directory, and `MOXI_HOME` those of every module (`moxi.paths.Layout`). rounds reads no
other variable. A round's log is not here: it lives in the project's rounds folder, beside
the design it records.

Every function takes an optional `env` mapping and reads the process environment without
one.
"""

from __future__ import annotations

from pathlib import Path

from moxi.paths import Layout

MODULE = "rounds"

#: The one home for all five of rounds' roots.
HOME_ENV = "MOXI_ROUNDS_HOME"
#: The root of rounds' data directory.
DATA_HOME_ENV = "MOXI_ROUNDS_DATA_HOME"
#: The root of rounds' state directory.
STATE_HOME_ENV = "MOXI_ROUNDS_STATE_HOME"
#: The root of rounds' cache directory.
CACHE_HOME_ENV = "MOXI_ROUNDS_CACHE_HOME"
#: The root of rounds' config directory.
CONFIG_HOME_ENV = "MOXI_ROUNDS_CONFIG_HOME"
#: The root of rounds' runtime directory.
RUNTIME_DIR_ENV = "MOXI_ROUNDS_RUNTIME_DIR"
#: Every variable of rounds' own that moves a root, in the order `moxi.paths.LOCATORS`
#: names their suffixes.
LOCATOR_ENVS = (HOME_ENV, DATA_HOME_ENV, STATE_HOME_ENV, CACHE_HOME_ENV, CONFIG_HOME_ENV,
                RUNTIME_DIR_ENV)

#: rounds' layout: the core's roots, rounds' prefix `MOXI_ROUNDS_`.
LAYOUT = Layout(MODULE)

#: The index's file name in the data directory.
INDEX_FILE = "index.db"
#: The folder of lock files in the state directory.
LOCKS_DIR = "locks"
#: The folder of build worktrees in the data directory, when `worktree_dir` names none.
WORKTREES_DIR = "worktrees"


def data_dir(env=None) -> Path:
    """DATA/rounds."""
    return LAYOUT.data_dir(env)


def state_dir(env=None) -> Path:
    """STATE/rounds, this machine's own."""
    return LAYOUT.state_dir(env)


def cache_dir(env=None) -> Path:
    """CACHE/rounds, this machine's own."""
    return LAYOUT.cache_dir(env)


def config_dir(env=None) -> Path:
    """CONFIG/rounds, which holds `config.toml`."""
    return LAYOUT.config_dir(env)


def runtime_dir(env=None) -> Path:
    """RUNTIME/rounds."""
    return LAYOUT.runtime_dir(env)


def index_file(env=None) -> Path:
    """The index, DATA/rounds/index.db."""
    return data_dir(env) / INDEX_FILE


def locks_dir(env=None) -> Path:
    """The folder of lock files, STATE/rounds/locks."""
    return state_dir(env) / LOCKS_DIR


def default_worktree_dir(env=None) -> Path:
    """Where build worktrees go when `worktree_dir` is empty: DATA/rounds/worktrees."""
    return data_dir(env) / WORKTREES_DIR


__all__ = ["MODULE", "HOME_ENV", "DATA_HOME_ENV", "STATE_HOME_ENV", "CACHE_HOME_ENV",
           "CONFIG_HOME_ENV", "RUNTIME_DIR_ENV", "LOCATOR_ENVS", "LAYOUT", "INDEX_FILE",
           "LOCKS_DIR", "WORKTREES_DIR", "data_dir", "state_dir", "cache_dir",
           "config_dir", "runtime_dir", "index_file", "locks_dir", "default_worktree_dir"]
