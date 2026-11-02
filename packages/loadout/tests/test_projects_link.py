"""The projects link: a key section resolved through a stand-in projects module, each
projects error refusing only the key sections it touches."""

from __future__ import annotations

import json
import os
import subprocess

import loadout_kit as kit
import pytest

from moxi_loadout import install, projects_link

API = '''
import json, os


class ProjectsError(Exception):
    exit_code = 1


class NotFound(ProjectsError):
    exit_code = 3

    def __init__(self, key):
        super().__init__(f"no project is named {key!r}")


class Unreadable(ProjectsError):
    pass


class TooNew(Unreadable):
    exit_code = 3


class Busy(Unreadable):
    exit_code = 75


class Project:
    def __init__(self, key, root):
        self.key, self.root = key, root


def get(key):
    calls = os.environ["FAKE_PROJECTS_CALLS"]
    with open(calls, "a") as fh:
        fh.write(key + "\\n")
    error = os.environ.get("FAKE_PROJECTS_ERROR")
    if error == "busy":
        raise Busy("the projects store stayed locked")
    if error == "too-new":
        raise TooNew("the projects store is newer than this version reads")
    if error == "unreadable":
        raise Unreadable("the projects store can't be read; move it aside")
    roots = json.loads(os.environ.get("FAKE_PROJECTS_ROOTS", "{}"))
    for k, root in roots.items():
        if k.casefold() == key.casefold():
            return Project(k, root)
    raise NotFound(key)
'''


@pytest.fixture
def projects(site, tmp_path, monkeypatch):
    """A stand-in projects module offering interface 1; returns a setter for its roots."""
    site.module("projects", API="'moxi_projects.api'", files={"api.py": API})
    calls = tmp_path / "projects-calls"
    monkeypatch.setenv("FAKE_PROJECTS_CALLS", str(calls))

    class Stand:
        def roots(self, **roots):
            monkeypatch.setenv("FAKE_PROJECTS_ROOTS",
                               json.dumps({k: str(v) for k, v in roots.items()}))

        def fail(self, error):
            monkeypatch.setenv("FAKE_PROJECTS_ERROR", error)

        def calls(self):
            return calls.read_text().split() if calls.exists() else []

    return Stand()


@pytest.fixture
def lo(folder):
    kit.configure(folder)
    kit.skill(folder / "skills", "review")
    return folder


def root(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path


def placed(path) -> bool:
    return os.path.lexists(path / ".claude" / "skills" / "review")


def test_a_key_section_resolves_to_its_projects_root(lo, projects, tmp_path):
    app = root(tmp_path, "app")
    projects.roots(App=app)
    kit.manifest(lo, '[project.app]\nskills = ["review"]\n')
    out = install.install()
    assert out.code == 0, out.text()
    assert placed(app)
    assert install.status().code == 0


def test_resolving_starts_no_process(projects, tmp_path, monkeypatch):
    app = root(tmp_path, "app")
    projects.roots(app=app)

    def refuse(*args, **kwargs):
        raise AssertionError("the projects link started a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    assert projects_link.resolve("app").root == str(app)


def test_not_found_refuses_only_that_section(lo, projects, tmp_path):
    app, web = root(tmp_path, "app"), root(tmp_path, "web")
    projects.roots(app=app, web=web)
    kit.manifest(lo, '[project.app]\nskills = ["review"]\n[project.web]\nskills = ["review"]\n')
    assert install.install().code == 0
    projects.roots(web=web)
    out = install.install()
    assert out.code == 3
    assert any("no project is named 'app'" in line for line in out.text())
    # Its placement is carried as it stands; the other section goes on.
    assert placed(app) and placed(web)


@pytest.mark.parametrize("error, code", [("unreadable", 3), ("too-new", 3), ("busy", 75)])
def test_a_store_error_refuses_every_key_section_once(lo, projects, tmp_path, error, code):
    app, web, here = root(tmp_path, "app"), root(tmp_path, "web"), root(tmp_path, "here")
    projects.roots(app=app, web=web)
    kit.manifest(lo, f'[project.app]\nskills = ["review"]\n[project.web]\nskills = ["review"]\n'
                     f'[project."{here}"]\nskills = ["review"]\n')
    assert install.install().code == 0
    projects.fail(error)
    before = len(projects.calls())
    out = install.install()
    assert out.code == code, out.text()
    assert len(projects.calls()) == before + 1
    assert placed(app) and placed(web) and placed(here)
    blocked = {r.name for r in out.rows if r.kind == "section"}
    assert blocked == {"app", "web"}
    if error == "busy":
        assert any("projects is busy; run again" in line for line in out.text())


def test_without_projects_every_key_section_is_refused(lo, site, tmp_path):
    kit.manifest(lo, '[project.app]\nskills = ["review"]\n')
    out = install.install()
    assert out.code == 3
    assert any("name it by path, or moxi install projects" in line for line in out.text())


def test_a_projects_module_that_wont_load_refuses_every_key_section(lo, site):
    site.module("projects", API="'moxi_projects.missing'")
    kit.manifest(lo, '[project.app]\nskills = ["review"]\n[project.web]\nskills = ["review"]\n')
    out = install.install()
    assert out.code == 3
    assert any("projects can't be loaded" in line for line in out.text())
