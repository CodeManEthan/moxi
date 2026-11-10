"""rounds' directories, and the variables that move them."""

from __future__ import annotations

import pytest
from moxi.paths import HOME_ENV, LOCATORS

from moxi_rounds import paths


def test_one_constant_names_each_variable_that_moves_a_root():
    assert paths.LOCATOR_ENVS == tuple(f"MOXI_ROUNDS_{suffix}" for suffix in LOCATORS)
    assert set(paths.LAYOUT.locators()) == {*paths.LOCATOR_ENVS, HOME_ENV}


def test_the_module_home_holds_all_five_directories(tmp_path):
    env = {paths.HOME_ENV: str(tmp_path)}
    assert paths.data_dir(env) == tmp_path / "data" / "rounds"
    assert paths.state_dir(env) == tmp_path / "state" / "rounds"
    assert paths.cache_dir(env) == tmp_path / "cache" / "rounds"
    assert paths.config_dir(env) == tmp_path / "config" / "rounds"
    assert paths.runtime_dir(env) == tmp_path / "runtime" / "rounds"


@pytest.mark.parametrize("variable, directory", [
    (paths.DATA_HOME_ENV, paths.data_dir),
    (paths.STATE_HOME_ENV, paths.state_dir),
    (paths.CACHE_HOME_ENV, paths.cache_dir),
    (paths.CONFIG_HOME_ENV, paths.config_dir),
    (paths.RUNTIME_DIR_ENV, paths.runtime_dir),
])
def test_a_root_variable_moves_its_own_directory_and_no_other(tmp_path, variable,
                                                             directory):
    home, own = tmp_path / "home", tmp_path / "own"
    env = {paths.HOME_ENV: str(home), variable: str(own)}
    assert directory(env) == own / "rounds"
    others = {paths.data_dir, paths.state_dir, paths.cache_dir, paths.config_dir,
              paths.runtime_dir} - {directory}
    assert all(home in other(env).parents for other in others)


def test_the_core_home_places_rounds_when_it_has_no_home_of_its_own(tmp_path):
    env = {HOME_ENV: str(tmp_path)}
    assert paths.data_dir(env) == tmp_path / "data" / "rounds"
    assert paths.LAYOUT.config_file(env) == tmp_path / "config" / "rounds" / "config.toml"


def test_the_index_the_locks_and_the_worktrees_have_their_places(tmp_path):
    env = {paths.HOME_ENV: str(tmp_path)}
    assert paths.index_file(env) == tmp_path / "data" / "rounds" / "index.db"
    assert paths.locks_dir(env) == tmp_path / "state" / "rounds" / "locks"
    assert paths.default_worktree_dir(env) == tmp_path / "data" / "rounds" / "worktrees"

