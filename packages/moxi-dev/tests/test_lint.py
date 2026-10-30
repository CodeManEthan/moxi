"""The contract's import rules as import-linter and deptry check them, over small repos
written for each test, with the tools this environment installs."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from moxi_dev.lint import IMPORTLINTER_CONFIG, LintError, plan
from test_contract_rules import Repo, announcement, pyproject

BIN = Path(sys.executable).parent


def lint(repo: Repo, *, members=(), path=()) -> dict[str, str]:
    """Run every command `plan` gives for `repo`; each failed one's output, by what it
    checks. `path` adds import roots (a sibling's `src`) for import-linter."""
    work = repo.root.parent / f".lint-{repo.name}"
    failed = {}
    for command in plan(repo.root, work, members):
        entries = [*command.pythonpath, *map(str, path)]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(entries)}
        done = subprocess.run([str(BIN / command.argv[0]), *command.argv[1:]],
                              cwd=repo.root, env=env, capture_output=True, text=True)
        if done.returncode:
            failed[command.what] = done.stdout + done.stderr
    return failed


INTERNALS = "another module's internals"
OUTSIDE = "undeclared dependencies outside tests/"
TESTS = "undeclared dependencies in tests/"


@pytest.fixture
def repo(tmp_path):
    """alpha, requiring the core and beta, which keeps the contract."""
    beta = Repo(tmp_path / "beta", "beta")
    beta.write("pyproject.toml", pyproject("moxi-beta"))
    beta.src("__init__.py")
    beta.src("api.py", "def send(): pass\n")
    beta.src("engine.py", "")
    beta.src("module.py", announcement(API="'moxi_beta.api'"))
    r = Repo(tmp_path / "alpha")
    r.write("pyproject.toml", pyproject(deps=("moxi>=0.1", "moxi-beta>=0.1")))
    r.src("__init__.py")
    r.src("module.py", announcement())
    r.src("core.py", """\
        import os
        from moxi import names
        from moxi.paths import Layout
        import moxi.config
        from moxi_beta.api import send
        from moxi_beta import api
        """)
    r.write("tests/test_core.py", "import pytest\nimport moxi_alpha.core\n")
    r.beta = beta
    return r


def run(repo: Repo, **kw) -> dict[str, str]:
    return lint(repo, path=[repo.root / "src", repo.beta.root / "src"], **kw)


def test_a_repo_that_keeps_the_contract_passes_both_tools(repo):
    assert run(repo) == {}


def test_the_commands_are_written_from_the_declarations(repo):
    whats = [command.what for command in plan(repo.root, repo.root.parent / "w")]
    assert whats == [INTERNALS, OUTSIDE, TESTS]
    config = (repo.root.parent / "w" / IMPORTLINTER_CONFIG).read_text()
    assert '"moxi_beta.api"' not in config.split("ignore_imports")[0]
    assert '"moxi_alpha -> moxi_beta.api"' in config and '"tests.** -> moxi_beta.api"' in config


# --- another module's internals -------------------------------------------------------------

def test_another_modules_internals_are_refused(repo):
    repo.src("core.py", "import moxi_beta.engine\nfrom moxi_beta import api\n")
    out = run(repo)[INTERNALS]
    assert "moxi_alpha.core -> moxi_beta.engine (l.1)" in out
    assert "-> moxi_beta.api" not in out


def test_a_sibling_module_beside_the_api_is_refused(repo):
    repo.src("core.py", "from moxi_beta import api, engine\n")
    assert "moxi_alpha.core -> moxi_beta.engine" in run(repo)[INTERNALS]


def test_internals_in_tests_are_refused_too(repo):
    repo.write("tests/test_core.py", "import moxi_beta.module\n")
    assert "tests.test_core -> moxi_beta.module (l.1)" in run(repo)[INTERNALS]


def test_a_module_without_an_api_cannot_be_imported(repo):
    repo.beta.src("module.py", announcement())
    out = run(repo)[INTERNALS]
    assert "moxi-beta names no API module" in out and "-> moxi_beta.api" in out


def test_an_optional_module_is_reached_only_through_load(repo):
    repo.write("pyproject.toml", pyproject(extras={"beta": ["moxi-beta>=0.1"]}))
    repo.src("module.py", announcement(OPTIONAL="{'beta': 'Beta things.'}"))
    out = run(repo)[INTERNALS]
    assert "moxi.modules.load" in out and "moxi_alpha.core -> moxi_beta" in out


def test_a_required_module_an_extra_also_names_stays_importable(repo):
    repo.write("pyproject.toml", pyproject(deps=("moxi>=0.1", "moxi-beta>=0.1"),
                                           extras={"codex": ["moxi-beta[codex]>=0.1"]}))
    repo.src("module.py", announcement(OPTIONAL="{'codex': 'The Codex host.'}"))
    assert run(repo) == {}


def test_an_unreadable_announcement_of_a_dependency_stops_the_plan(repo):
    (repo.beta.root / "pyproject.toml").unlink()
    with pytest.raises(LintError, match="cannot be read to learn its API module"):
        plan(repo.root, repo.root.parent / "w")


def test_a_workspace_member_s_announcement_is_read_from_its_directory(repo, tmp_path):
    """A sibling named in `members` is read where it is, not beside the package."""
    elsewhere = tmp_path / "elsewhere" / "beta"
    elsewhere.parent.mkdir()
    repo.beta.root.rename(elsewhere)
    with pytest.raises(LintError):
        plan(repo.root, tmp_path / "w")
    assert plan(repo.root, tmp_path / "w", [elsewhere, repo.root])


@pytest.mark.parametrize("line", ["import moxi.install", "from moxi import install"])
def test_the_core_s_modules_outside_its_table_are_refused(repo, line):
    repo.src("core.py", line + "\n")
    assert "moxi_alpha.core -> moxi.install (l.1)" in run(repo)[INTERNALS]


def core_repo(root: Path) -> Repo:
    r = Repo(root, "core")
    r.pkg = "moxi"
    r.write("pyproject.toml", pyproject("moxi", deps=(), entry_points={},
                                        groups={"dev": ["pytest", "moxi-dev"]}))
    r.src("__init__.py")
    r.src("cli.py", "def main(argv=None):\n    return 0\n")
    return r


def test_the_core_imports_no_module(repo, tmp_path):
    core = core_repo(tmp_path / "moxi")
    core.src("names.py", "import moxi_beta.api\n")
    core.write("tests/test_contract.py", "from moxi_dev.contract import check\n")
    dev = Repo(tmp_path / "moxi-dev", "dev")
    dev.write("pyproject.toml", pyproject("moxi-dev", entry_points={}, scripts={}))
    members = [core.root, repo.beta.root, dev.root]
    out = lint(core, members=members,
               path=[core.root / "src", repo.beta.root / "src"])[INTERNALS]
    assert "moxi.names -> moxi_beta" in out
    assert "moxi_dev" not in out


# --- undeclared dependencies -----------------------------------------------------------------

def test_an_undeclared_import_is_refused(repo):
    repo.src("core.py", "def f():\n    import requests\n")
    out = run(repo)
    assert list(out) == [OUTSIDE]
    assert "src/moxi_alpha/core.py:2:12: DEP001 'requests' imported but missing" in out[OUTSIDE]


def test_a_declared_distribution_counts_whether_or_not_it_is_installed(repo):
    repo.write("pyproject.toml", pyproject(
        deps=("moxi>=0.1", "moxi-beta", "no-such-dist-anywhere>=1"),
        extras={"fast": ["another_missing.dist"]}))
    repo.src("module.py", announcement(OPTIONAL="{'fast': 'Faster.'}"))
    repo.src("core.py", "import no_such_dist_anywhere\ndef f():\n"
                        "    import another_missing_dist\n")
    assert run(repo) == {}


def test_the_imports_table_maps_an_import_to_its_distribution(repo):
    repo.write("pyproject.toml", pyproject(deps=("moxi", "moxi-beta", "PyYAML>=6"),
                                           tool='[tool.moxi.imports]\nyaml = "pyyaml"\n'))
    repo.src("core.py", "import yaml\n")
    assert run(repo) == {}
    repo.write("pyproject.toml", pyproject(deps=("moxi", "moxi-beta", "PyYAML>=6")))
    assert "'yaml' imported but missing" in run(repo)[OUTSIDE]


def test_a_moxi_import_maps_by_its_spelling(repo):
    repo.write("pyproject.toml", pyproject(deps=("moxi-beta",)))
    repo.src("core.py", "from moxi import names\nimport moxi_beta.api\n")
    out = run(repo)
    assert "'moxi'" in out[OUTSIDE] and "moxi_beta" not in out[OUTSIDE]


def test_tests_may_import_a_dependency_group(repo):
    repo.write("tests/test_core.py", "import pytest\nimport hypothesis\nimport moxi_alpha\n")
    repo.write("pyproject.toml", pyproject(deps=("moxi", "moxi-beta"),
                                           groups={"dev": ["pytest", "hypothesis>=6"]}))
    assert run(repo) == {}
    repo.write("tests/test_core.py", "import requests\n")
    assert "tests/test_core.py:1:8: DEP001 'requests'" in run(repo)[TESTS]


def test_src_may_not_import_a_dependency_group(repo):
    repo.src("core.py", "import pytest\n")
    assert "DEP004 'pytest' imported but declared as a dev dependency" in run(repo)[OUTSIDE]


def test_tests_import_their_own_helpers(repo):
    repo.write("tests/helpers.py", "")
    repo.write("tests/fakes/__init__.py", "")
    repo.write("tests/test_core.py", "import helpers\nfrom fakes import x\nimport conftest\n")
    assert run(repo) == {}


def test_relative_and_future_imports_pass(repo):
    repo.src("core.py", "from __future__ import annotations\nfrom . import module\n")
    assert run(repo) == {}


# --- which packages get which tools -----------------------------------------------------------

def test_a_distribution_that_is_not_moxi_s_gets_no_commands(tmp_path):
    other = Repo(tmp_path / "low", "low")
    other.write("pyproject.toml", '[project]\nname = "low"\nversion = "0"\n')
    assert plan(other.root, tmp_path / "w") == []


def test_the_contributors_package_gets_deptry_alone(tmp_path):
    dev = Repo(tmp_path / "moxi-dev", "dev")
    dev.write("pyproject.toml", pyproject("moxi-dev", entry_points={}, scripts={}))
    dev.src("__init__.py", "import moxi_beta.engine\n")
    dev.write("tests/test_x.py", "import moxi_dev\n")
    assert [c.what for c in plan(dev.root, tmp_path / "w")] == [OUTSIDE, TESTS]
    assert not (tmp_path / "w" / IMPORTLINTER_CONFIG).exists()
