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

**Under a test.** While a test runs (`running_under_pytest`), each of the five roots must
be placed by a Moxi variable: `MOXI_ROUNDS_<KIND>_HOME` (`MOXI_ROUNDS_RUNTIME_DIR`),
`MOXI_ROUNDS_HOME` or `MOXI_HOME`, set and not empty in the mapping the root is resolved
from. A root that falls through to the platform's default is the real one, wherever
the home folder, `XDG_*` or the Windows folders point, so `LAYOUT` refuses to give it and raises
`UnfencedTest` (`refused_root`) before any caller can use the path. Every directory and
file here reaches a root through `LAYOUT`, so nothing of rounds' own lands in the real
home under a test. Outside a test nothing changes. `PROJECTS_LAYOUT` is the projects
module's layout, whose data root holds the projects registry; rounds holds that root to
the same rule before it takes a project's folders from the registry.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from moxi.paths import HOME_ENV as MOXI_HOME_ENV
from moxi.paths import Layout

from .errors import UnfencedTest

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

#: The variables pytest sets while it runs: for each test's setup, call and teardown, and
#: for the whole session (pytest 8.2 and later). A child process inherits both.
_PYTEST_VARIABLES = ("PYTEST_CURRENT_TEST", "PYTEST_VERSION")

#: The variable suffix of each root kind's own variable.
_OWN_SUFFIX = {"data": "DATA_HOME", "state": "STATE_HOME", "cache": "CACHE_HOME",
               "config": "CONFIG_HOME", "runtime": "RUNTIME_DIR"}


def running_under_pytest(env: Mapping[str, str],
                         modules: Mapping[str, object]) -> str | None:
    """The sign that a test is running in the process whose environment is `env` and
    whose loaded modules are `modules`, in words ("PYTEST_VERSION is set"), or None when
    there is none: `PYTEST_CURRENT_TEST` or `PYTEST_VERSION` set, or pytest imported."""
    for variable in _PYTEST_VARIABLES:
        if variable in env:
            return f"{variable} is set"
    if "pytest" in modules:
        return "pytest is imported"
    return None


def _placers(layout: Layout, kind: str) -> tuple[str, ...]:
    """The Moxi variables that place the `kind` root of `layout`, most particular first:
    the root's own, the module's home, then `MOXI_HOME`."""
    if not layout.prefix:
        return (MOXI_HOME_ENV,)
    module_home = layout.locators()[0]
    return (f"{layout.prefix}{_OWN_SUFFIX[kind]}", module_home, MOXI_HOME_ENV)


def refused_root(layout: Layout, kind: str, env: Mapping[str, str],
                 signal: str | None) -> str | None:
    """The text that refuses the `kind` root of `layout`, resolved from `env`, when a test
    is running (`signal`, from `running_under_pytest`) and no Moxi variable of
    `_placers` is set and not empty in `env`; else None."""
    if signal is None:
        return None
    placers = _placers(layout, kind)
    placed = [variable for variable in placers if env.get(variable)]
    if placed:
        return None
    try:
        root = getattr(Layout(layout.module, layout.prefix), f"{kind}_root")(env)
        where = str(root / layout.module)
    except (LookupError, OSError) as exc:
        where = f"(which can't be placed: {exc})"
    fix = placers[-2] if len(placers) > 1 else MOXI_HOME_ENV
    return (f"{layout.module} refused its {kind} directory {where}: a test is running "
            f"({signal}) and no Moxi variable ({', '.join(placers)}) places it, so it "
            "would be the real one. Run the test inside moxi-dev's fence "
            f"({layout.module}'s tests folder, run from packages/{layout.module}), or set "
            f"{fix} to a folder of the test's own.")


class _GuardedLayout(Layout):
    """`Layout`, whose five roots are refused under a test unless a Moxi variable places
    them (`refused_root`). Every directory and file method reaches a root through these."""

    def _guarded(self, kind: str, env: Mapping[str, str] | None, root: Path) -> Path:
        refused = refused_root(self, kind, os.environ if env is None else env,
                               running_under_pytest(os.environ, sys.modules))
        if refused is not None:
            raise UnfencedTest(refused)
        return root

    def data_root(self, env: Mapping[str, str] | None = None) -> Path:
        return self._guarded("data", env, super().data_root(env))

    def state_root(self, env: Mapping[str, str] | None = None) -> Path:
        return self._guarded("state", env, super().state_root(env))

    def cache_root(self, env: Mapping[str, str] | None = None) -> Path:
        return self._guarded("cache", env, super().cache_root(env))

    def config_root(self, env: Mapping[str, str] | None = None) -> Path:
        return self._guarded("config", env, super().config_root(env))

    def runtime_root(self, env: Mapping[str, str] | None = None) -> Path:
        return self._guarded("runtime", env, super().runtime_root(env))


#: rounds' layout: the core's roots, rounds' prefix `MOXI_ROUNDS_`, refused under a test
#: unless a Moxi variable places them.
LAYOUT = _GuardedLayout(MODULE)

#: The projects module's layout: its data root holds the projects registry.
PROJECTS_LAYOUT = Layout("projects")

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
           "CONFIG_HOME_ENV", "RUNTIME_DIR_ENV", "LOCATOR_ENVS", "LAYOUT", "PROJECTS_LAYOUT",
           "running_under_pytest", "refused_root", "INDEX_FILE",
           "LOCKS_DIR", "WORKTREES_DIR", "data_dir", "state_dir", "cache_dir",
           "config_dir", "runtime_dir", "index_file", "locks_dir", "default_worktree_dir"]
