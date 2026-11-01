"""The committer's and the git hook pieces' names, handed out by `moxi_repo.api` for other
modules: each is the package's own object, and importing the API loads nothing more."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys

import pytest

import moxi_repo
from moxi_repo import api

#: Every name another module reaches repo's committer and pieces by.
NAMES = ("git", "changes", "Change", "literal_pathspec", "Spec", "save_spec", "remove_spec",
         "commit", "commit_saved", "CommitResult", "PrepareContext", "Prepared",
         "HookContext", "Outcome", "PATHSPEC_VARS", "SpecError")

#: The package's own modules that `import moxi_repo.api` loaded before the API handed out
#: any of these names. It loads no other module outside the standard library.
API_LOADS = {"moxi_repo", "moxi_repo._records", "moxi_repo.api", "moxi_repo.gitconf",
             "moxi_repo.gitrun", "moxi_repo.pieces", "moxi_repo.run", "moxi_repo.shim"}


def _loaded(code: str) -> set[str]:
    """What a fresh, isolated interpreter has loaded after running `code`."""
    script = ("import json, sys\n" + code +
              "\nprint(json.dumps(sorted(sys.modules)))")
    done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return set(json.loads(done.stdout.splitlines()[-1]))


def _not_stdlib(names: set[str]) -> set[str]:
    return {n for n in names if n.split(".")[0] not in sys.stdlib_module_names} - {
        "__main__", "_virtualenv", "site", "sitecustomize", "usercustomize"}


@pytest.mark.parametrize("name", NAMES)
def test_each_name_is_its_home_modules_own_object(name):
    home = importlib.import_module(api.REEXPORTS[name])
    assert getattr(api, name) is getattr(home, name)
    if name in moxi_repo.EXPORTS:
        assert getattr(api, name) is getattr(moxi_repo, name)
    assert name in api.__all__ and name in dir(api)


def test_the_api_hands_out_every_package_export_and_nothing_unasked():
    assert set(moxi_repo.EXPORTS) <= set(api.REEXPORTS)
    assert set(api.REEXPORTS) == set(NAMES)
    with pytest.raises(AttributeError):
        api.no_such_name


def test_the_names_import_with_from():
    done = subprocess.run([sys.executable, "-I", "-c",
                           f"from moxi_repo.api import {', '.join(NAMES)}"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_importing_the_api_loads_no_module_it_did_not_load_before():
    assert _not_stdlib(_loaded("import moxi_repo.api")) == API_LOADS


def test_a_name_loads_its_home_module_only_when_first_asked_for():
    loaded = _loaded("import moxi_repo.api")
    assert "moxi_repo.committer" not in loaded
    assert "moxi.proc" not in loaded
    asked = _loaded("import moxi_repo.api\nmoxi_repo.api.Spec")
    assert "moxi_repo.committer" in asked
