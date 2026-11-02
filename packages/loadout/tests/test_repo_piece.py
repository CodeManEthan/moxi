"""The link to repo: the two git hook pieces, their registration by install and SETUP, the
setting that switches them, and the doctor's row.

repo is a stand-in on the core's site fixture, so no test runs another module's code or
binary. The stand-in's `git` and `changes` run real git; the rows that build a real work
tree are `slow`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import loadout_kit as kit
import pytest

from moxi_loadout import doctor, gitpiece, install, repo_link, setup

GLOBS = ("packages/loadout/src/moxi_loadout/gitpiece.py",
         "packages/loadout/src/moxi_loadout/repo_link.py",
         "packages/loadout/tests/test_repo_piece.py")

API = '''
import json, os, subprocess


class Failure(Exception):
    code = 1

    def __init__(self, text, *, lines=None):
        super().__init__(text)
        self.text = text
        self.lines = list(lines or [])


class Report:
    def __init__(self):
        self.lines, self.problems, self.chain = [], [], []

    @property
    def ok(self):
        return not self.problems


class Outcome:
    def __init__(self, kind, lines=()):
        self.kind, self.lines = kind, tuple(lines)

    @classmethod
    def ok(cls):
        return cls("ok")

    @classmethod
    def notice(cls, line):
        return cls("notice", (line,))

    def __repr__(self):
        return f"Outcome.{self.kind}{self.lines!r}"


def _log(*words):
    with open(os.environ["FAKE_REPO_CALLS"], "a") as fh:
        fh.write(" ".join(str(w) for w in words) + "\\n")


def _state():
    path = os.environ["FAKE_REPO_STATE"]
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


def _save(state):
    with open(os.environ["FAKE_REPO_STATE"], "w") as fh:
        json.dump(state, fh)


def _key(path):
    return os.path.realpath(path)


def _fail():
    why = os.environ.get("FAKE_REPO_FAIL")
    if why:
        raise Failure(why, lines=["git said no"])


def add(path, *ids):
    _log("add", path, *ids)
    _fail()
    state = _state()
    mine = state.setdefault(_key(path), {})
    report = Report()
    for pid in ids:
        mine[pid] = {"enabled": True}
        report.lines.append(f"added {pid} in {path}")
    _save(state)
    return report


def remove(path, *ids):
    _log("remove", path, *ids)
    _fail()
    state = _state()
    mine = state.setdefault(_key(path), {})
    report = Report()
    for pid in ids:
        mine.pop(pid, None)
        report.lines.append(f"removed {pid} from {path}")
    _save(state)
    return report


def status(path=None):
    _fail()
    if os.environ.get("FAKE_REPO_STATUS") == "timeout":
        raise subprocess.TimeoutExpired(["git", "rev-parse"], 30)
    mine = _state().get(_key(path), {})
    return [{"root": path, "pieces": [{"id": pid, **row} for pid, row in mine.items()]}]


def _env():
    return {k: v for k, v in os.environ.items()
            if not k.startswith("GIT_") or k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}


def git(root, *args, timeout=30.0, input=None):
    done = subprocess.run(["git", "-C", os.fspath(root), *args], input=input,
                          capture_output=True, timeout=timeout, env=_env())
    return subprocess.CompletedProcess(done.args, done.returncode,
                                       done.stdout.decode(), done.stderr.decode())


def changes(root, staged=True, range=None, paths=(), *, timeout=30.0):
    _log("changes", root, *range, *paths)
    a, b = range
    tail = ["--", *(":(literal)" + p for p in paths)]
    done = subprocess.run(["git", "-C", os.fspath(root), "diff-tree", "-r", "--name-only",
                           "-z", a, b, *tail], capture_output=True, check=True,
                          timeout=timeout, env=_env())
    return [p for p in done.stdout.decode().split("\\0") if p]
'''


class Repo:
    """The stand-in repo's state and calls."""

    def __init__(self, tmp_path):
        self.state_file = tmp_path / "fake-repo-state.json"
        self.calls_file = tmp_path / "fake-repo-calls"

    def pieces(self, path) -> dict:
        if not self.state_file.exists():
            return {}
        return json.loads(self.state_file.read_text()).get(os.path.realpath(path), {})

    def calls(self) -> list[str]:
        if not self.calls_file.exists():
            return []
        return self.calls_file.read_text().splitlines()

    def verbs(self) -> list[str]:
        return [c for c in self.calls() if c.split()[0] in ("add", "remove")]


@pytest.fixture
def repo(site, tmp_path, monkeypatch):
    """A stand-in repo module offering interface 1."""
    site.module("repo", API="'moxi_repo.api'", files={"api.py": API})
    stand = Repo(tmp_path)
    monkeypatch.setenv("FAKE_REPO_STATE", str(stand.state_file))
    monkeypatch.setenv("FAKE_REPO_CALLS", str(stand.calls_file))
    return stand


@pytest.fixture
def no_repo(site):
    """No repo installed: the site shows only moxi and loadout."""
    return site


def git(cwd, *args) -> str:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") or k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=True, env=env).stdout.strip()


def commit(root, path: str, text: str = "x\n", message: str = "change") -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(root, "add", "--", path)
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


class Ctx:
    """The parts of repo's `HookContext` the piece reads."""

    def __init__(self, event: str, root):
        self.event = event
        self.root = None if root is None else str(root)


class Installs:
    """`moxi.proc.run` for the child install: each call recorded, an answer given."""

    def __init__(self, monkeypatch):
        from moxi import proc

        self.calls: list[dict] = []
        self.answer = subprocess.CompletedProcess([], 0, b"", b"")
        real = proc.run

        def run(argv, **kwargs):
            if list(argv[1:3]) == ["-m", "moxi_loadout"]:
                self.calls.append({"argv": list(argv), **kwargs})
                if isinstance(self.answer, BaseException):
                    raise self.answer
                return self.answer
            return real(argv, **kwargs)

        monkeypatch.setattr(proc, "run", run)


@pytest.fixture
def installs(monkeypatch):
    return Installs(monkeypatch)


def records() -> dict:
    from moxi import doctor as core_doctor
    from moxi import paths

    path = paths.Layout("loadout").state_dir() / core_doctor.RECORDS_FILE
    return json.loads(path.read_text()) if path.exists() else {}


# -- the declarations ----------------------------------------------------------------------

def test_each_declaration_names_its_event_the_one_target_and_fails_open():
    for decl, event in ((gitpiece.REINSTALL_COMMIT, "post-commit"),
                        (gitpiece.REINSTALL_MERGE, "post-merge")):
        assert decl["event"] == event
        assert decl["target"] == "moxi_loadout.gitpiece:run"
        assert decl["fail"] == "open" and decl["order"] == 50
        assert decl["doc"] and "\n" not in decl["doc"] and "settings" not in decl


def test_the_package_declares_both_pieces_in_repos_group():
    from importlib.metadata import entry_points

    found = {e.name: e.value for e in entry_points(group="moxi.repo.git_hooks")}
    assert found["loadout.reinstall-commit"] == "moxi_loadout.gitpiece:REINSTALL_COMMIT"
    assert found["loadout.reinstall-merge"] == "moxi_loadout.gitpiece:REINSTALL_MERGE"
    assert repo_link.PIECES == ("loadout/reinstall-commit", "loadout/reinstall-merge")


def test_repo_is_optional_with_its_extra():
    import tomllib
    from pathlib import Path

    from moxi_loadout import module

    assert "repo" in module.OPTIONAL
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["optional-dependencies"]["repo"] == ["moxi-repo>=0.1,<0.2"]


def test_the_setting_defaults_on_and_needs_repo(folder):
    from moxi_loadout import settings

    kit.configure(folder)
    assert settings.read().reinstall_on_commit is True
    key = settings.schema().keys["reinstall_on_commit"]
    assert key.needs == "repo" and key.type == "bool"


# -- the piece's import --------------------------------------------------------------------

def test_the_piece_imports_nothing_but_the_standard_library():
    code = ("import sys; before = set(sys.modules); import moxi_loadout.gitpiece; "
            "print(' '.join(sorted(set(sys.modules) - before)))")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                          text=True, check=True)
    added = set(done.stdout.split())
    assert {m for m in added if m.split(".")[0] not in sys.stdlib_module_names} == {
        "moxi_loadout", "moxi_loadout.gitpiece"}


@pytest.mark.timed
def test_the_piece_imports_in_under_10_ms():
    """Repo's runner pays this import on every commit; the median of five runs, measured
    again twice if the machine was busy."""
    import statistics

    def once() -> int:
        done = subprocess.run([sys.executable, "-I", "-X", "importtime", "-c",
                               "import moxi_loadout.gitpiece"], capture_output=True,
                              text=True, timeout=60)
        total = 0
        for line in done.stderr.splitlines():
            parts = line.split("|")
            if len(parts) == 3 and parts[2].strip() == "moxi_loadout.gitpiece":
                total = int(parts[1])
        return total

    for _ in range(3):
        times = [once() for _ in range(5)]
        if statistics.median(times) < 10_000:
            break
        time.sleep(1)
    assert statistics.median(times) < 10_000, times


# -- the piece's skips, without git ----------------------------------------------------------

def test_no_root_is_skipped(repo, folder, installs):
    kit.configure(folder)
    assert gitpiece.run(Ctx("post-commit", None)).kind == "ok"
    assert installs.calls == []


def test_the_setting_off_skips_before_any_git(repo, folder, installs):
    from moxi import config

    kit.configure(folder)
    config.set("loadout", "reinstall_on_commit", "false", by="test")
    assert gitpiece.run(Ctx("post-commit", folder)).kind == "ok"
    assert installs.calls == [] and repo.calls() == []


def test_a_commit_in_another_repository_is_skipped(repo, folder, tmp_path, installs):
    kit.configure(folder)
    other = tmp_path / "other"
    other.mkdir()
    assert gitpiece.run(Ctx("post-commit", other)).kind == "ok"
    assert installs.calls == [] and repo.calls() == []


def test_the_clean_env_drops_gits_local_and_pathspec_variables(monkeypatch):
    leaked = {"GIT_DIR": "/elsewhere/.git", "GIT_INDEX_FILE": "/elsewhere/index",
              "GIT_WORK_TREE": "/elsewhere", "GIT_PREFIX": "sub/",
              "GIT_LITERAL_PATHSPECS": "1", "GIT_GLOB_PATHSPECS": "1",
              "GIT_NOGLOB_PATHSPECS": "1", "GIT_ICASE_PATHSPECS": "1"}
    for k, v in leaked.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SAMPLE_KEPT", "yes")
    env = gitpiece.clean_env()
    assert not set(leaked) & set(env)
    assert env["SAMPLE_KEPT"] == "yes" and env["HOME"] == os.environ["HOME"]


# -- the piece against real git ------------------------------------------------------------

@pytest.fixture
def tree(repo, folder, tmp_path):
    """A work tree whose `loadout/` folder is the configured loadout folder, with one
    commit outside it."""
    root = tmp_path / "dotfiles"
    root.mkdir()
    git(root, "init", "-q")
    commit(root, "README", "readme\n", "start")
    kit.configure(root / "loadout")
    return root


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_commit_that_changes_the_folder_runs_the_install_with_a_clean_env(
        tree, installs, monkeypatch):
    commit(tree, "loadout/loadout.toml", "[user]\n")
    for k, v in {"GIT_DIR": str(tree / ".git"), "GIT_INDEX_FILE": str(tree / ".git/index"),
                 "GIT_LITERAL_PATHSPECS": "1"}.items():
        monkeypatch.setenv(k, v)
    outcome = gitpiece.run(Ctx("post-commit", tree))
    assert outcome.kind == "ok"
    [call] = installs.calls
    from moxi import paths

    assert call["argv"] == [os.fspath(paths.python()), "-m", "moxi_loadout", "install"]
    assert call["timeout"] == gitpiece.INSTALL_SECONDS == 180
    assert not {"GIT_DIR", "GIT_INDEX_FILE", "GIT_LITERAL_PATHSPECS"} & set(call["env"])
    assert records() == {}


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_commit_elsewhere_in_the_work_tree_is_skipped(tree, installs, repo):
    commit(tree, "loadout/loadout.toml", "[user]\n")
    commit(tree, "notes.txt", "elsewhere\n")
    assert gitpiece.run(Ctx("post-commit", tree)).kind == "ok"
    assert installs.calls == []
    parent = git(tree, "rev-parse", "HEAD^")
    assert f"changes {tree} {parent} HEAD loadout" in repo.calls()


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_capitalised_folder_keeps_its_case_where_the_os_folds_case(
        repo, folder, tmp_path, installs, monkeypatch):
    """Windows' normcase lowercases; git's pathspec still matches case exactly, so the
    path the piece gives git is the folder's own spelling."""
    root = tmp_path / "Dots"
    root.mkdir()
    git(root, "init", "-q")
    commit(root, "README", "readme\n", "start")
    kit.configure(root / "Loadout")
    commit(root, "Loadout/loadout.toml", "[user]\n")
    monkeypatch.setattr(os.path, "normcase", str.lower)
    assert gitpiece.run(Ctx("post-commit", root)).kind == "ok"
    assert len(installs.calls) == 1
    assert [c for c in repo.calls() if c.startswith("changes ")][0].endswith(" Loadout")


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_the_root_commit_is_read_against_the_empty_tree(repo, folder, tmp_path, installs):
    root = tmp_path / "fresh"
    root.mkdir()
    git(root, "init", "-q")
    kit.configure(root)
    commit(root, "loadout.toml", "[user]\n", "first")
    assert gitpiece.run(Ctx("post-commit", root)).kind == "ok"
    assert len(installs.calls) == 1
    empty = subprocess.run(["git", "-C", str(root), "hash-object", "-t", "tree", "--stdin"],
                           input=b"", capture_output=True, check=True).stdout.decode().strip()
    assert any(c.startswith("changes ") and f" {empty} HEAD" in c for c in repo.calls())


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_merge_that_changes_the_folder_runs_the_install(tree, installs):
    git(tree, "checkout", "-q", "-b", "side")
    commit(tree, "loadout/skills/review/SKILL.md", "---\nname: review\n---\n")
    git(tree, "checkout", "-q", "main")
    git(tree, "merge", "-q", "--ff-only", "side")
    assert gitpiece.run(Ctx("post-merge", tree)).kind == "ok"
    assert len(installs.calls) == 1


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_merge_with_no_orig_head_is_skipped(tree, installs):
    commit(tree, "loadout/loadout.toml", "[user]\n")
    orig = tree / ".git" / "ORIG_HEAD"
    if orig.exists():
        orig.unlink()
    assert gitpiece.run(Ctx("post-merge", tree)).kind == "ok"
    assert installs.calls == []


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_linked_worktree_has_its_own_root_and_is_skipped(tree, tmp_path, installs):
    linked = tmp_path / "linked"
    git(tree, "worktree", "add", "-q", "-b", "elsewhere", str(linked))
    commit(linked, "loadout/loadout.toml", "[user]\n")
    assert gitpiece.run(Ctx("post-commit", linked)).kind == "ok"
    assert installs.calls == []


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_nested_repository_inside_the_root_is_not_the_folders_work_tree(
        repo, folder, tmp_path, installs):
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    commit(outer, "README", "outer\n", "start")
    inner = outer / "inner"
    inner.mkdir()
    git(inner, "init", "-q")
    kit.configure(inner / "loadout")
    commit(inner, "loadout/loadout.toml", "[user]\n")
    assert gitpiece.run(Ctx("post-commit", outer)).kind == "ok"
    assert installs.calls == []


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_failed_install_prints_the_command_and_leaves_a_record_a_clean_install_clears(
        tree, installs):
    commit(tree, "loadout/loadout.toml", "[user]\n")
    installs.answer = subprocess.CompletedProcess([], 3, b"loadout: 1 blocked\n", b"")
    outcome = gitpiece.run(Ctx("post-commit", tree))
    assert outcome.kind == "notice"
    [line] = outcome.lines
    assert "git hook" in line and "exited 3 (loadout: 1 blocked)" in line
    assert line.endswith("run: moxi loadout install")
    record = records()["reinstall"]
    assert record["fix"] == "moxi loadout install"
    assert record["text"].startswith("post-commit git hook: moxi loadout install after a "
                                     "commit (")
    assert "exited 3" in record["text"]
    assert install.install().code == 0
    assert "reinstall" not in records()


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_an_install_that_runs_out_of_time_is_a_failure(tree, installs):
    commit(tree, "loadout/loadout.toml", "[user]\n")
    installs.answer = subprocess.TimeoutExpired(["python"], 180)
    outcome = gitpiece.run(Ctx("post-commit", tree))
    assert outcome.kind == "notice" and "took longer than 180 s" in outcome.lines[0]
    assert "took longer than 180 s" in records()["reinstall"]["text"]


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_scoped_install_leaves_the_record(tree, installs):
    from moxi_loadout.planner import Scope

    commit(tree, "loadout/loadout.toml", "[user]\n")
    installs.answer = subprocess.CompletedProcess([], 1, b"", b"boom\n")
    gitpiece.run(Ctx("post-commit", tree))
    assert install.install(Scope(user=True)).code == 0
    assert "reinstall" in records()


# -- registration --------------------------------------------------------------------------

def in_git(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".git").mkdir()
    return folder


def test_install_registers_both_pieces_one_per_call(repo, folder):
    kit.configure(in_git(folder))
    out = install.install()
    assert out.code == 0
    assert repo.verbs() == [f"add {folder} loadout/reinstall-commit",
                            f"add {folder} loadout/reinstall-merge"]
    assert set(repo.pieces(folder)) == set(repo_link.PIECES)
    assert f"added loadout/reinstall-commit in {folder}" in out.lines
    # A second install finds them there and writes nothing.
    install.install()
    assert len(repo.verbs()) == 2


def test_setup_registers_them_too(repo, folder, capsys):
    kit.configure(in_git(folder))
    assert setup.setup() == 0
    assert set(repo.pieces(folder)) == set(repo_link.PIECES)
    assert f"loadout: added loadout/reinstall-merge in {folder}" in capsys.readouterr().out


def test_the_setting_off_removes_each_registered_piece(repo, folder):
    from moxi import config

    kit.configure(in_git(folder))
    install.install()
    config.set("loadout", "reinstall_on_commit", "false", by="test")
    install.install()
    assert repo.pieces(folder) == {}
    assert repo.verbs()[2:] == [f"remove {folder} loadout/reinstall-commit",
                                f"remove {folder} loadout/reinstall-merge"]


def test_a_folder_in_no_work_tree_asks_nothing_of_repo(repo, folder):
    kit.configure(folder)
    assert install.install().code == 0
    assert repo.calls() == []


def test_without_repo_nothing_is_registered_and_install_goes_on(no_repo, folder):
    kit.configure(in_git(folder))
    out = install.install()
    assert out.code == 0
    assert not [line for line in out.lines if "git hook" in line]


def test_a_repo_failure_is_a_line_not_an_exit_code(repo, folder, monkeypatch):
    kit.configure(in_git(folder))
    monkeypatch.setenv("FAKE_REPO_FAIL", "git is too old")
    out = install.install()
    assert out.code == 0
    assert "the git hook pieces weren't registered: git is too old" in out.lines
    assert "  git said no" in out.lines
    assert setup.setup() == 0


def test_a_git_call_of_repos_that_runs_out_of_time_is_a_line_too(repo, folder, monkeypatch,
                                                                  capsys):
    kit.configure(in_git(folder))
    monkeypatch.setenv("FAKE_REPO_STATUS", "timeout")
    out = install.install()
    assert out.code == 0
    assert "the git hook pieces weren't registered: git did not answer in 30 s" in out.lines
    assert setup.setup() == 0
    assert ("loadout: the git hook pieces weren't registered: git did not answer in 30 s"
            in capsys.readouterr().out)


# -- the doctor's row ----------------------------------------------------------------------

def row(rows):
    [found] = [r for r in rows if r.name == "git hook"]
    return found


def test_the_row_is_off_for_a_folder_in_no_work_tree(repo, folder):
    kit.configure(folder)
    assert row(doctor.rows()).state == "off"


def test_the_row_is_off_with_repo_absent_saying_what_repo_would_do(no_repo, folder):
    kit.configure(in_git(folder))
    found = row(doctor.rows())
    assert found.state == "off" and "with repo, a commit reinstalls" in found.detail
    assert found.fix == "moxi install loadout --with repo"


def test_the_row_warns_while_the_setting_is_on_and_a_piece_is_unregistered(repo, folder):
    kit.configure(in_git(folder))
    found = row(doctor.rows())
    assert found.state == "warn" and "loadout/reinstall-commit" in found.detail
    assert found.fix == "moxi loadout install"
    install.install()
    assert row(doctor.rows()).state == "ok"


def test_the_row_is_off_while_the_setting_is_off(repo, folder):
    from moxi import config

    kit.configure(in_git(folder))
    config.set("loadout", "reinstall_on_commit", "false", by="test")
    found = row(doctor.rows())
    assert found.state == "off" and "reinstall_on_commit" in found.detail


def test_the_row_warns_on_a_piece_disabled_in_repo(repo, folder):
    kit.configure(in_git(folder))
    install.install()
    state = json.loads(repo.state_file.read_text())
    state[os.path.realpath(folder)]["loadout/reinstall-merge"]["enabled"] = False
    repo.state_file.write_text(json.dumps(state))
    found = row(doctor.rows())
    assert found.state == "warn" and "disabled" in found.detail
    assert "moxi repo git-hooks enable" in found.fix
