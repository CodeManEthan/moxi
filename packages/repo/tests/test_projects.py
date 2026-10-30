"""The optional projects link: a project key in place of PATH, keys in listings, and
`list --projects`, against the real projects module, and with projects absent or unusable.

The real module keeps its store in its own home, which the test fence points into each
test's tmp (`MOXI_PROJECTS_HOME`). Projects are registered the way a user does it,
`moxi projects add KEY --root DIR`. A store that can't be read is made for real: a
corrupt file, one at a newer schema version, or one another connection holds locked.
"""

from __future__ import annotations

import ast
import contextlib
import functools
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import POSIX_ONLY, change, commit, git

from moxi_repo import cli, gitconf
from moxi_repo import projects as link


class Projects:
    """The real projects module with its store in this test's projects home."""

    def __init__(self, capsys, monkeypatch):
        import moxi.modules
        import moxi.paths

        self.capsys = capsys
        self.mp = monkeypatch
        moxi.modules.reset()
        found = moxi.modules.load("projects", interface=1)
        assert found.ok, found.reason
        self.api = found.api
        home = os.environ["MOXI_PROJECTS_HOME"]
        self.store = Path(moxi.paths.Layout("projects").data_dir()) / "projects.db"
        assert self.store.is_relative_to(home) and not self.store.exists()

    def add(self, key: str, root: Path) -> None:
        """`moxi projects add KEY --root DIR`, run in process as the umbrella runs it."""
        import moxi.cli

        code = moxi.cli.main(["projects", "add", key, "--root", str(root)])
        out, err = self.capsys.readouterr()
        assert code == 0, err
        assert key in out

    def corrupt(self) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_bytes(b"this is not a database, not at all, " * 200)

    def too_new(self) -> None:
        conn = sqlite3.connect(self.store)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.execute(f"PRAGMA user_version = {version + 1}")
        finally:
            conn.close()

    @contextlib.contextmanager
    def held(self):
        """The store locked by another connection, and every connection's wait for a lock
        cut to 50 ms so the read gives up quickly."""
        import moxi.store

        self.mp.setattr(moxi.store, "connect",
                        functools.partial(moxi.store.connect, timeout=0.05))
        holder = sqlite3.connect(self.store, isolation_level=None)
        try:
            holder.execute("PRAGMA locking_mode = EXCLUSIVE")
            holder.execute("BEGIN EXCLUSIVE")
            holder.execute("SELECT count(*) FROM projects").fetchone()
            yield
        finally:
            holder.execute("ROLLBACK")
            holder.close()

    def error(self, key: str = "alpha"):
        """The error projects' own `get` raises on the store as it stands."""
        with pytest.raises(self.api.Unreadable) as caught:
            self.api.get(key)
        return caught.value


@pytest.fixture
def projects(capsys, monkeypatch):
    import moxi.modules

    yield Projects(capsys, monkeypatch)
    moxi.modules.reset()


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def pieces_of(repo: Path) -> set[str]:
    return set(gitconf.read(gitconf.resolve(repo)).pieces)


def keys_listed(capsys, *argv: str) -> dict[str, str | None]:
    code, out, err = run(capsys, "git-hooks", "list", *argv, "--json")
    assert code == 0 and err == "", err
    return {r["root"]: r["project"] for r in json.loads(out)}


# -- with projects ------------------------------------------------------------------------

@POSIX_ONLY
def test_a_project_key_works_in_place_of_a_path(demo, projects, capsys, monkeypatch):
    alpha = demo.repo("alpha-notes")
    projects.add("alpha", alpha)
    monkeypatch.chdir(demo.tmp)
    code, out, err = run(capsys, "git-hooks", "add", "ALPHA", "demo/gate")
    assert code == 0, err
    assert f"added demo/gate (pre-commit, closed) in {alpha}" in out
    assert pieces_of(alpha) == {"demo/gate"}
    assert run(capsys, "git-hooks", "disable", "Alpha", "demo/gate")[0] == 0
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
def test_an_unknown_key_is_refused_with_exit_2(demo, projects, capsys, monkeypatch):
    projects.add("alpha", demo.repo("alpha-notes"))
    monkeypatch.chdir(demo.tmp)
    code, _, err = run(capsys, "git-hooks", "add", "nope", "demo/gate")
    assert (code, err) == (2, "repo: no project or path named nope\n")
    assert run(capsys, "specs", "remove", "nope", "demo/x")[0] == 2


@POSIX_ONLY
def test_an_empty_store_has_no_keys(demo, projects, capsys, monkeypatch):
    monkeypatch.chdir(demo.tmp)
    code, _, err = run(capsys, "git-hooks", "add", "alpha", "demo/gate")
    assert (code, err) == (2, "repo: no project or path named alpha\n")
    assert not projects.store.exists()


@POSIX_ONLY
@pytest.mark.parametrize("state, code", [("corrupt", 1), ("too_new", 3), ("held", 75)])
def test_a_store_that_cant_be_read_refuses_with_its_reason_and_fix(demo, projects, capsys,
                                                                   monkeypatch, state, code):
    projects.add("alpha", demo.repo("alpha-notes"))
    monkeypatch.chdir(demo.tmp)
    with contextlib.ExitStack() as stack:
        if state == "held":
            stack.enter_context(projects.held())
        else:
            getattr(projects, state)()
        exc = projects.error()
        assert not isinstance(exc, projects.api.NotFound)
        got, _, err = run(capsys, "git-hooks", "add", "alpha", "demo/gate")
    assert got == code
    assert err == f"repo: projects can't be read: {exc.reason}; {exc.fix}\n"


@POSIX_ONLY
def test_an_existing_path_wins_over_a_key_and_a_path_never_asks_projects(
        demo, projects, capsys, monkeypatch):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    projects.add("beta-code", alpha)
    calls = []
    real_get = projects.api.get

    def get(key):
        calls.append(key)
        return real_get(key)

    monkeypatch.setattr(projects.api, "get", get)
    monkeypatch.chdir(demo.tmp)
    assert run(capsys, "git-hooks", "add", "beta-code", "demo/gate")[0] == 0
    assert run(capsys, "git-hooks", "add", str(beta), "demo/lint")[0] == 0
    assert calls == []
    assert pieces_of(beta) == {"demo/gate", "demo/lint"} and pieces_of(alpha) == set()
    # The spy is the one repo reaches: a key does go through it.
    assert run(capsys, "git-hooks", "add", "BETA-CODE", "demo/gate")[0] == 0
    assert calls == ["BETA-CODE"] and pieces_of(alpha) == {"demo/gate"}


@POSIX_ONLY
def test_remove_of_a_vanished_indexed_repo_stays_a_path(demo, projects, capsys,
                                                        monkeypatch):
    gone = demo.repo("gone-repo")
    projects.add("alpha", demo.repo("alpha-notes"))
    monkeypatch.chdir(demo.tmp)
    assert run(capsys, "git-hooks", "add", "gone-repo")[0] == 0
    shutil.rmtree(gone)
    code, out, err = run(capsys, "git-hooks", "remove", "gone-repo")
    assert code == 0, err
    assert "dropped gone-repo from the index" in out


@POSIX_ONLY
def test_listings_show_keys_and_list_projects_adds_untouched_repos(demo, projects, capsys):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    untouched = demo.repo("untouched")
    plain = demo.tmp / "not-a-repo"
    plain.mkdir()
    (beta / "sub").mkdir()
    for key, root in (("alpha", alpha), ("beta", beta / "sub"), ("untouched", untouched),
                      ("plain", plain)):
        projects.add(key, root)
    assert run(capsys, "git-hooks", "add", str(alpha), "demo/gate")[0] == 0
    assert run(capsys, "git-hooks", "add", str(beta), "demo/gate")[0] == 0
    unkeyed = demo.repo("unkeyed")
    assert run(capsys, "git-hooks", "add", str(unkeyed), "demo/gate")[0] == 0

    assert keys_listed(capsys) == {str(alpha): "alpha", str(beta): None,
                                   str(unkeyed): None}

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
def test_a_subdirectory_and_a_linked_worktree_take_their_main_repos_key(demo, projects,
                                                                        capsys):
    alpha = demo.repo("alpha-notes")
    (alpha / "src" / "deep").mkdir(parents=True)
    projects.add("Alpha", alpha)
    worktree = demo.tmp / "scratch" / "alpha-feature"
    git(alpha, "worktree", "add", "-q", "-b", "feature", str(worktree))
    assert run(capsys, "git-hooks", "add", str(worktree), "demo/gate")[0] == 0

    # The key as projects stores it, for the worktree it was never given.
    assert keys_listed(capsys) == {str(worktree): "Alpha"}
    code, out, _ = run(capsys, "specs", str(worktree))
    assert code == 0 and "project Alpha" in out
    # A subdirectory, and the same place inside the worktree, resolve to alpha too.
    found = link.api()
    assert link.key_for(found, str(alpha / "src" / "deep")) == ("Alpha", None)
    assert link.key_for(found, str(worktree / "src")) == ("Alpha", None)
    # A key still names the main work tree, never the worktree.
    assert run(capsys, "git-hooks", "add", "alpha", "demo/lint")[0] == 0
    assert pieces_of(alpha) == {"demo/gate", "demo/lint"}


@POSIX_ONLY
def test_a_projects_error_leaves_keys_empty_with_one_line(demo, projects, capsys):
    alpha, beta = demo.repo("alpha-notes"), demo.repo("beta-code")
    projects.add("alpha", alpha)
    for repo in (alpha, beta):
        assert run(capsys, "git-hooks", "add", str(repo), "demo/gate")[0] == 0
    with projects.held():
        exc = projects.error()
        assert isinstance(exc, projects.api.Busy)
        code, out, err = run(capsys, "git-hooks", "list", "--projects", "--json")
        assert code == 0
        assert [r["project"] for r in json.loads(out)] == [None, None]
        assert err == f"repo: projects can't be read: {exc.reason}; {exc.fix}\n"
        code, _, err = run(capsys, "specs", str(alpha))
        assert code == 0 and err.count("projects can't be read") == 1


def test_with_projects_installed_help_says_a_key_works(demo, projects, capsys):
    text = " ".join(_help(capsys, "git-hooks", "add").split())
    assert "or a project key (matched ignoring case)" in text


# -- without projects ---------------------------------------------------------------------

def _help(capsys, *argv: str) -> str:
    with pytest.raises(SystemExit):
        cli.main([*argv, "--help"])
    return capsys.readouterr().out


@pytest.fixture(params=["not installed", "not importable"])
def absent(request, monkeypatch):
    """projects absent: its distribution hidden from the environment's metadata (the
    site), or its package not importable at all, so `available` says no."""
    import moxi.modules

    if request.param == "not installed":
        request.getfixturevalue("site")
    else:
        monkeypatch.setitem(sys.modules, "moxi_projects", None)
        moxi.modules.reset()
        assert not moxi.modules.available("projects")
    yield request.param
    moxi.modules.reset()


@POSIX_ONLY
def test_with_projects_absent_paths_work_and_help_names_the_install(
        demo, absent, capsys, monkeypatch):
    import moxi.modules

    if absent == "not installed":
        assert not moxi.modules.load("projects").ok
    else:  # repo asks `available` first and never reaches `load`
        assert not moxi.modules.available("projects")
    alpha = demo.repo("alpha-notes")
    monkeypatch.chdir(demo.tmp)
    assert "moxi install projects" in " ".join(_help(capsys, "git-hooks", "add").split())
    assert "moxi install projects" in " ".join(_help(capsys).split())
    code, _, err = run(capsys, "git-hooks", "add", "alpha", "demo/gate")
    assert code == 2 and "no git work tree at alpha" in err
    assert run(capsys, "git-hooks", "add", "alpha-notes", "demo/gate")[0] == 0
    assert keys_listed(capsys) == {str(alpha): None}
    code, out, err = run(capsys, "git-hooks", "list", "--projects")
    assert code == 0 and str(alpha) in out
    assert err == ("repo: projects isn't installed, so --projects adds nothing; run: moxi "
                   "install projects\n")


# A stand-in API that would resolve any key to `FAKE_ROOT`, if repo ever used it.
FAKE_API = '''
import os


class Project:
    def __init__(self, key, root):
        self.key = key
        self.root = root


def get(key):
    return Project(key, os.environ["FAKE_ROOT"])


def for_path(path):
    return Project("fake", path)


def list_projects(kind=None):
    return []
'''


@POSIX_ONLY
@pytest.mark.parametrize("announce", [
    {"INTERFACES": "frozenset({2})", "API": "'fake_projects.api'"},
    {"API": "'fake_projects.missing'"},
])
def test_a_projects_that_cant_serve_interface_1_is_off(demo, site, capsys, monkeypatch,
                                                      announce):
    site.module("projects", package="fake_projects", **announce)
    site.write("fake_projects/api.py", FAKE_API)
    alpha = demo.repo("alpha-notes")
    monkeypatch.chdir(demo.tmp)
    monkeypatch.setenv("FAKE_ROOT", str(alpha))
    code, _, err = run(capsys, "git-hooks", "add", "alpha", "demo/gate")
    assert code == 2 and "no git work tree at alpha" in err
    help_text = " ".join(_help(capsys, "git-hooks", "list").split())
    assert "With projects installed (moxi install projects)" in help_text


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
