"""The export table: no exported name is a module's name, and every export still works
after every module of the package is imported, called or read twice."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import moxi_repo
from moxi_repo import EXPORTS
from tests.conftest import POSIX_ONLY

pytestmark = POSIX_ONLY


def test_exports_and_module_names_are_disjoint():
    modules = {m.name for m in pkgutil.iter_modules(moxi_repo.__path__)}
    assert "committer" in modules and "gitrun" in modules
    assert not set(EXPORTS) & modules


def test_every_export_survives_every_module_being_imported(demo):
    for info in pkgutil.iter_modules(moxi_repo.__path__):
        importlib.import_module(f"moxi_repo.{info.name}")
    repo = demo.repo()
    spec = moxi_repo.Spec(name="demo/snap", root=repo, paths=("notes",),
                          author=("snap-bot", "snap@example.invalid"))
    for _ in range(2):
        assert inspect.isfunction(moxi_repo.git)
        assert moxi_repo.git(repo, "rev-parse", "HEAD").returncode == 0
        assert inspect.isfunction(moxi_repo.commit)
        assert moxi_repo.commit(spec).status == "clean"
    for name in EXPORTS:
        for _ in range(2):
            value = getattr(moxi_repo, name)
            assert inspect.isfunction(value) or inspect.isclass(value), (name, value)
