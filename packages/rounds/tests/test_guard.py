"""The guard in this folder's conftest.py: a run without the test kit's fence fails every
test at setup, every fixture a test can borrow checks the fence's homes at setup, and the
config fixtures refuse a path outside the test's own tmp."""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent

PROBE = '''
def test_writes_the_config(write_config):
    write_config("x = 1\\n")


def test_needs_nothing():
    pass
'''


def test_a_run_without_the_fence_fails_every_test_at_setup(tmp_path):
    copy = tmp_path / "copy"
    copy.mkdir()
    shutil.copy(HERE / "conftest.py", copy / "conftest.py")
    (copy / "test_probe.py").write_text(PROBE, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rE", str(copy)],
        cwd=copy, capture_output=True, text=True, timeout=120, check=False)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert "2 errors" in out, out
    assert "fixture 'fence' not found" in out, out
    assert "passed" not in out, out


@pytest.mark.parametrize("fixture", ["config_file", "write_config"])
def test_a_config_path_outside_the_tests_tmp_fails_the_test(fixture, monkeypatch, request,
                                                            tmp_path):
    outside = tmp_path.parent.parent / "elsewhere"
    monkeypatch.setenv("MOXI_ROUNDS_HOME", str(outside))
    with pytest.raises(pytest.fail.Exception, match="outside the test's own tmp"):
        request.getfixturevalue(fixture)
    assert not outside.exists()


def test_the_config_file_is_the_tests_own(write_config, config_file, fence):
    assert write_config("x = 1\n") == config_file
    assert config_file.resolve().is_relative_to(fence.short.resolve())


def _conftest():
    """This folder's conftest.py as a module of its own, for its plain functions."""
    spec = importlib.util.spec_from_file_location("rounds_tests_conftest",
                                                  HERE / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_fence_is_in_force_when_both_homes_are_inside_the_tests_roots(tmp_path):
    short = tmp_path / "short"
    env = {"MOXI_ROUNDS_HOME": str(short / "rounds"),
           "MOXI_PROJECTS_HOME": str(tmp_path / "projects")}
    assert _conftest().fence_in_force(env, (tmp_path / "nothing", short, tmp_path)) is None


@pytest.mark.parametrize("variable", ["MOXI_ROUNDS_HOME", "MOXI_PROJECTS_HOME"])
def test_a_home_missing_or_outside_the_tests_roots_is_not_in_force(tmp_path, variable):
    inside = {"MOXI_ROUNDS_HOME": str(tmp_path / "rounds"),
              "MOXI_PROJECTS_HOME": str(tmp_path / "projects")}
    fence_in_force = _conftest().fence_in_force
    missing = {key: value for key, value in inside.items() if key != variable}
    text = fence_in_force(missing, (tmp_path,))
    assert text is not None and f"{variable} is not set" in text
    assert "run pytest from packages/rounds" in text
    elsewhere = tmp_path.parent / "elsewhere"
    text = fence_in_force({**inside, variable: str(elsewhere)}, (tmp_path,))
    assert text is not None and f"{variable} is {elsewhere}" in text
    assert fence_in_force({**inside, variable: ""}, (tmp_path,)) is not None


def test_a_home_behind_a_link_into_the_tests_roots_is_in_force(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path.parent / f"{tmp_path.name}-link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("this system makes no symbolic links for this user")
    try:
        env = {"MOXI_ROUNDS_HOME": str(link / "rounds"),
               "MOXI_PROJECTS_HOME": str(real / "projects")}
        assert _conftest().fence_in_force(env, (real,)) is None
    finally:
        link.unlink()


def test_the_fenced_fixture_fails_a_test_whose_home_is_outside_its_tmp(
        monkeypatch, request, tmp_path):
    outside = tmp_path.parent.parent / "elsewhere"
    monkeypatch.setenv("MOXI_PROJECTS_HOME", str(outside))
    fenced = request.node.funcargs["fenced"]
    with pytest.raises(pytest.fail.Exception, match="MOXI_PROJECTS_HOME is "):
        _conftest().fenced.__wrapped__(fenced, tmp_path)


def _fixtures(tree: ast.Module) -> dict[str, list[str]]:
    """Each fixture the module defines, with the fixtures it takes by name."""
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if ast.unparse(target) == "pytest.fixture":
                found[node.name] = [arg.arg for arg in node.args.args]
    return found


def test_every_fixture_a_test_can_borrow_takes_the_fenced_one():
    fixtures = _fixtures(ast.parse((HERE / "conftest.py").read_text(encoding="utf-8")))
    assert {"fenced", "config_file", "write_config", "add_project", "without_notes",
            "holder", "git_repo", "_git_template"} <= fixtures.keys()

    def reaches(name: str, seen: frozenset = frozenset()) -> bool:
        taken = fixtures.get(name, [])
        through = [other for other in taken if other in fixtures and other not in seen
                   and reaches(other, seen | {name})]
        return "fenced" in taken or through != []

    # The session's git template writes only under pytest's own temp folder, and the
    # one fixture that takes it takes `fenced` too.
    unfenced = [name for name in fixtures
                if name not in ("fenced", "_git_template") and not reaches(name)]
    assert unfenced == []
    assert fixtures["fenced"][0] == "fence"


# -- the escape: a test file elsewhere that borrows these fixtures -----------------------

#: A test file outside this folder that borrows rounds' fixtures, as the escaped one did:
#: `write_config` from this folder's conftest.py (by `-p conftest`, or imported) and
#: `configure` from `test_queue_batch`, which takes it.
BORROWS_BY_PLUGIN = '''
from test_queue_batch import configure  # noqa: F401


def test_borrows_configure(configure):
    pass


def test_borrows_write_config(write_config):
    write_config("x = 1\\n")
'''

BORROWS_BY_IMPORT = '''
from conftest import write_config  # noqa: F401
from test_queue_batch import configure  # noqa: F401


def test_borrows_configure(configure):
    pass


def test_borrows_write_config(write_config):
    write_config("x = 1\\n")
'''

#: A test file elsewhere with no conftest at all, whose own fixture writes rounds' config
#: and opens its index.
WRITES_ON_ITS_OWN = '''
import pytest

from moxi_rounds import index, paths


@pytest.fixture
def written():
    config = paths.LAYOUT.config_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("x = 1\\n", encoding="utf-8")
    index.rebuild([])


def test_writes(written):
    pass
'''


def _stand_in_env(fence) -> dict[str, str]:
    """The fence's own environment, where the home, every XDG folder and the Windows
    folders are the test's stand-in for the real home, with no Moxi variable that places
    a module's files: a child run in it finds the stand-in's defaults as the real ones."""
    env = {key: value for key, value in fence.env.items()
           if not (key.startswith("MOXI_") and key.endswith("_HOME")
                   and key != "MOXI_ANCHOR_HOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _files_under(env) -> list[Path]:
    """Every file under rounds' five directories as the defaults place them in `env`."""
    from moxi.paths import Layout

    plain = Layout("rounds")
    found = []
    for kind in ("data", "state", "cache", "config", "runtime"):
        module_dir = getattr(plain, f"{kind}_root")(env) / "rounds"
        found += [path for path in module_dir.rglob("*") if path.is_file()]
    return sorted(set(found))


def test_a_borrowed_or_unfenced_test_elsewhere_writes_nothing_of_rounds(fence, tmp_path):
    from moxi.paths import Layout

    env = _stand_in_env(fence)
    real_config = Layout("rounds").config_file(env)
    assert os.path.realpath(real_config).startswith(os.path.realpath(fence.home) + os.sep)
    real_config.parent.mkdir(parents=True)
    real_config.write_bytes(b"# the real config\n")
    away = tmp_path / "away"
    away.mkdir()

    def run(name: str, text: str, *options: str, pythonpath: bool = True) -> str:
        (away / name).write_text(text, encoding="utf-8")
        child_env = {**env, "PYTHONPATH": str(HERE)} if pythonpath else env
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rE", "-p", "no:cacheprovider",
             "--basetemp", str(tmp_path / f"basetemp-{name[:-3]}"), *options,
             str(away / name)],
            cwd=away, env=child_env, capture_output=True, text=True, timeout=120,
            check=False)
        out = done.stdout + done.stderr
        assert done.returncode != 0, out
        assert "passed" not in out, out
        return out

    by_plugin = run("test_by_plugin.py", BORROWS_BY_PLUGIN, "-p", "conftest")
    assert "fixture 'fence' not found" in by_plugin
    by_import = run("test_by_import.py", BORROWS_BY_IMPORT)
    assert re.search(r"fixture '(fence|fenced|config_file)' not found", by_import), by_import
    on_its_own = run("test_on_its_own.py", WRITES_ON_ITS_OWN, pythonpath=False)
    assert "UnfencedTest" in on_its_own and "a test is running" in on_its_own
    assert real_config.read_bytes() == b"# the real config\n"
    assert _files_under(env) == [real_config]
