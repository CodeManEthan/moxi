"""The optional projects link: a project key in place of PATH, keys in listings, and
`list --projects`, against a fake projects distribution offering interface 1, and with
projects absent or unusable.

The fake reads its projects from the JSON file `FAKE_PROJECTS` names, each key to its
root, and raises the error `FAKE_PROJECTS_FAIL` names (`unreadable`, `toonew`, `busy`).
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest
from conftest import POSIX_ONLY, change, commit

from moxi_repo import cli, gitconf

FAKE_API = '''
import json
import os


class ProjectsError(Exception):
    exit_code = 1

    def __init__(self, reason, fix=None):
        self.reason = reason
        self.fix = fix
        super().__init__(f"{reason}; {fix}" if fix else reason)


class NotFound(ProjectsError):
    exit_code = 3

    def __init__(self, key):
        self.key = key
        super().__init__(f"no project is named {key!r}", "see the projects list")


class Unreadable(ProjectsError):
    pass


class TooNew(Unreadable):
    exit_code = 3


class Busy(Unreadable):
    exit_code = 75


class Project:
    def __init__(self, key, root):
        self.key = key
        self.root = root


CALLS = []


def _rows():
    fail = os.environ.get("FAKE_PROJECTS_FAIL")
    if fail == "unreadable":
        raise Unreadable("the projects store is corrupt", "restore it from a backup")
    if fail == "toonew":
        raise TooNew("the projects store is newer than this code",
                     "upgrade it with `moxi install projects`")
    if fail == "busy":
        raise Busy("the projects store stayed locked", "try again in a moment")
    path = os.environ.get("FAKE_PROJECTS")
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [Project(k, r) for k, r in sorted(data.items())]


def list_projects(kind=None):
    CALLS.append("list_projects")
    return _rows()


def get(key):
    CALLS.append("get")
    for project in _rows():
        if project.key.lower() == key.lower():
            return project
    raise NotFound(key)


def for_path(path):
    CALLS.append("for_path")
    real = os.path.realpath(path)
    best = None
    for project in _rows():
        root = os.path.realpath(project.root)
        if real == root or real.startswith(root + os.sep):
            if best is None or len(root) > len(os.path.realpath(best.root)):
                best = project
    return best
'''


@pytest.fixture
def fake(site, tmp_path, monkeypatch):
    """A fake projects module installed in the site; returns a function that sets its
    projects."""
    site.module("projects", API="'moxi_projects.api'")
    site.write("moxi_projects/api.py", FAKE_API)
    data = tmp_path / "projects.json"
    monkeypatch.setenv("FAKE_PROJECTS", str(data))
    monkeypatch.delenv("FAKE_PROJECTS_FAIL", raising=False)

    def set_projects(**projects: Path) -> None:
        data.write_text(json.dumps({k: str(v) for k, v in projects.items()}),
                        encoding="utf-8")

    return set_projects


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def pieces_of(repo: Path) -> set[str]:
    return set(gitconf.read(gitconf.resolve(repo)).pieces)


# -- with projects ------------------------------------------------------------------------

@POSIX_ONLY
def test_a_project_key_works_in_place_of_a_path(demo, fake, capsys, monkeypatch):
    alpha = demo.repo("alpha-notes")
    fake(alpha=alpha)
    monkeypatch.chdir(demo.tmp)
    code, out, err = run(capsys, "git-hooks", "add", "ALPHA", "demo/gate")
    assert code == 0, err
    assert f"added demo/gate (pre-commit, closed) in {alpha}" in out
    assert pieces_of(alpha) == {"demo/gate"}
    assert run(capsys, "git-hooks", "disable", "alpha", "demo/gate")[0] == 0
    assert run(capsys, "git-hooks", "enable", "alpha", "demo/gate")[0] == 0
    assert run(capsys, "git-hooks", "refresh", "alpha")[0] == 0
    code, out, _ = run(capsys, "git-hooks", "list", "alpha")
    assert code == 0 and out.startswith(f"{alpha}  project alpha  git ")
    code, out, _ = run(capsys, "specs", "alpha")
    assert code == 0
    code, out, _ = run(capsys, "commit", "alpha")
    assert code == 0 and "no spec is saved" in out
    assert run(capsys, "git-hooks", "remove", "alpha", "demo/gate")[0] == 0
    assert pieces_of(alpha) == set()
    # The pieces still run from the repo's own git config; no key is involved there.
    assert run(capsys, "git-hooks", "add", "alpha", "demo/gate")[0] == 0
    demo.act(gate="refuse")
    change(alpha)
    assert commit(alpha).returncode != 0


@POSIX_ONLY
def test_an_unknown_key_is_refused_with_exit_2(demo, fake, capsys, monkeypatch):
    fake(alpha=demo.repo("alpha-notes"))
    monkeypatch.chdir(demo.tmp)
    code, _, err = run(capsys, "git-hooks", "add", "nope", "demo/gate")
    assert (code, err) == (2, "repo: no project or path named nope\n")
    assert run(capsys, "specs", "remove", "nope", "demo/x")[0] == 2


@POSIX_ONLY
@pytest.mark.parametrize("fail, code", [("unreadable", 1), ("toonew", 3), ("busy", 75)])
def test_a_store_that_cant_be_read_refuses_with_its_reason_and_fix(demo, fake, capsys,
                                                                   monkeypatch, fail, code):
    fake(alpha=demo.repo("alpha-notes"))
    monkeypatch.chdir(demo.tmp)
    monkeypatch.setenv("FAKE_PROJECTS_FAIL", fail)
    got, _, err = run(capsys, "git-hooks", "add", "alpha", "demo/gate")
    assert got == code
    assert err.startswith("repo: projects can't be read: ")
    assert "; " in err  # the fix follows the reason


@POSIX_ONLY
def test_an_existing_path_wins_over_a_key_and_a_path_never_asks_projects(
        demo, fake, capsys, monkeypatch):
    import moxi.modules

    fake_api = moxi.modules.load("projects", interface=1).api
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    fake(**{"beta-code": alpha})
    monkeypatch.chdir(demo.tmp)
    fake_api.CALLS.clear()
    assert run(capsys, "git-hooks", "add", "beta-code", "demo/gate")[0] == 0
    assert run(capsys, "git-hooks", "add", str(beta), "demo/lint")[0] == 0
    assert "get" not in fake_api.CALLS
    assert pieces_of(beta) == {"demo/gate", "demo/lint"} and pieces_of(alpha) == set()


@POSIX_ONLY
def test_remove_of_a_vanished_indexed_repo_stays_a_path(demo, fake, capsys, monkeypatch):
    gone = demo.repo("gone-repo")
    fake(alpha=demo.repo("alpha-notes"))
    monkeypatch.chdir(demo.tmp)
    assert run(capsys, "git-hooks", "add", "gone-repo")[0] == 0
    shutil.rmtree(gone)
    code, out, err = run(capsys, "git-hooks", "remove", "gone-repo")
    assert code == 0, err
    assert "dropped gone-repo from the index" in out


@POSIX_ONLY
def test_listings_show_keys_and_list_projects_adds_untouched_repos(demo, fake, capsys):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    untouched = demo.repo("untouched")
    plain = demo.tmp / "not-a-repo"
    plain.mkdir()
    fake(alpha=alpha, beta=beta / "sub", untouched=untouched, plain=plain)
    (beta / "sub").mkdir()
    assert run(capsys, "git-hooks", "add", str(alpha), "demo/gate")[0] == 0
    assert run(capsys, "git-hooks", "add", str(beta), "demo/gate")[0] == 0
    unkeyed = demo.repo("unkeyed")
    assert run(capsys, "git-hooks", "add", str(unkeyed), "demo/gate")[0] == 0

    code, out, err = run(capsys, "git-hooks", "list", "--json")
    assert code == 0 and err == ""
    keys = {r["root"]: r["project"] for r in json.loads(out)}
    assert keys == {str(alpha): "alpha", str(beta): None, str(unkeyed): None}

    code, out, _ = run(capsys, "git-hooks", "list", "--projects", "--json")
    listed = {r["root"]: r for r in json.loads(out)}
    assert set(listed) == {str(alpha), str(beta), str(unkeyed), str(untouched)}
    assert listed[str(untouched)]["indexed"] is False
    assert listed[str(untouched)]["project"] == "untouched"
    assert listed[str(alpha)]["indexed"] is True
    code, out, _ = run(capsys, "git-hooks", "list", "--projects")
    assert f"{untouched}  project untouched  (not indexed)  git " in out
    assert f"{alpha}  project alpha  git " in out



@POSIX_ONLY
def test_a_projects_error_leaves_keys_empty_with_one_line(demo, fake, capsys, monkeypatch):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    fake(alpha=alpha)
    for repo in (alpha, beta):
        assert run(capsys, "git-hooks", "add", str(repo), "demo/gate")[0] == 0
    monkeypatch.setenv("FAKE_PROJECTS_FAIL", "busy")
    code, out, err = run(capsys, "git-hooks", "list", "--projects", "--json")
    assert code == 0
    assert [r["project"] for r in json.loads(out)] == [None, None]
    assert err == ("repo: projects can't be read: the projects store stayed locked; try "
                   "again in a moment\n")
    code, _, err = run(capsys, "specs", str(alpha))
    assert code == 0 and err.count("projects can't be read") == 1


# -- without projects ---------------------------------------------------------------------

def _help(capsys, *argv: str) -> str:
    with pytest.raises(SystemExit):
        cli.main([*argv, "--help"])
    return capsys.readouterr().out


@POSIX_ONLY
def test_with_projects_absent_paths_work_and_help_names_the_install(
        demo, site, capsys, monkeypatch):
    import moxi.modules

    assert not moxi.modules.load("projects").ok
    alpha = demo.repo("alpha-notes")
    monkeypatch.chdir(demo.tmp)
    assert "moxi install projects" in " ".join(_help(capsys, "git-hooks", "add").split())
    assert "moxi install projects" in " ".join(_help(capsys).split())
    code, _, err = run(capsys, "git-hooks", "add", "alpha", "demo/gate")
    assert code == 2 and "no git work tree at alpha" in err
    assert run(capsys, "git-hooks", "add", "alpha-notes", "demo/gate")[0] == 0
    code, out, err = run(capsys, "git-hooks", "list", "--json")
    assert code == 0 and err == ""
    assert [r["project"] for r in json.loads(out)] == [None]
    code, out, err = run(capsys, "git-hooks", "list", "--projects")
    assert code == 0 and str(alpha) in out
    assert err == ("repo: projects isn't installed, so --projects adds nothing; run: moxi "
                   "install projects\n")


@POSIX_ONLY
@pytest.mark.parametrize("announce", [
    {"INTERFACES": "frozenset({2})", "API": "'moxi_projects.api'"},
    {"API": "'moxi_projects.missing'"},
])
def test_a_projects_that_cant_serve_interface_1_is_off(demo, site, capsys, monkeypatch,
                                                      announce):
    site.module("projects", **announce)
    site.write("moxi_projects/api.py", FAKE_API)
    alpha = demo.repo("alpha-notes")
    monkeypatch.chdir(demo.tmp)
    monkeypatch.setenv("FAKE_PROJECTS", str(demo.tmp / "p.json"))
    (demo.tmp / "p.json").write_text(json.dumps({"alpha": str(alpha)}), encoding="utf-8")
    code, _, err = run(capsys, "git-hooks", "add", "alpha", "demo/gate")
    assert code == 2 and "no git work tree at alpha" in err
    help_text = " ".join(_help(capsys, "git-hooks", "list").split())
    assert "With projects installed (moxi install projects)" in help_text


def test_with_projects_installed_help_says_a_key_works(demo, fake, capsys):
    text = " ".join(_help(capsys, "git-hooks", "add").split())
    assert "or a project key (matched ignoring case)" in text


def test_only_the_command_line_reaches_projects():
    """The git hook runner, the pieces and the API never import the projects link."""
    src = Path(cli.__file__).parent
    importers = set()
    for path in src.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [f"{node.module}.{a.name}" for a in node.names] + [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any(n == "moxi_repo.projects" or n.startswith("moxi_projects") for n in names):
                importers.add(path.name)
    assert importers == {"cli.py"}
