"""`add --create`: the mint refuses before it makes anything, makes the root before the row,
and never leaves a row whose root it failed to make.

git is faked by a script on PATH, so these run without git; one test in the slow tier
runs the real one.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys

import pytest

from moxi_projects import acts, api, cli, store
from moxi_projects.errors import MintFailed, Refused

posix_only = pytest.mark.skipif(sys.platform == "win32",
                                reason="the fake git is a POSIX shell script")

KINDS = '''
INTERFACE = 2
KINDS = {
    "topic": ("a notes topic", lambda fields: None, "dir"),
    "remote": ("a place made elsewhere", lambda fields: None, "refuse"),
    "repo": ("a repository", lambda fields: None),
}
'''


def fake_git(directory, body: str) -> str:
    """A `git` script holding `body`. Returns PATH with its directory first."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "git"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return os.pathsep.join([str(directory), "/usr/bin", "/bin"])


@pytest.fixture
def git_ok(tmp_path, monkeypatch):
    """A git that makes `.git` in its last argument and records its arguments."""
    log = tmp_path / "git.log"
    monkeypatch.setenv("PATH", fake_git(tmp_path / "bin", f'echo "$@" >> "{log}"\n'
                                                          'mkdir -p "$2/.git"\nexit 0'))
    return log


@pytest.fixture
def no_git(tmp_path, monkeypatch):
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


# --- the git mint ------------------------------------------------------------------------

@posix_only
def test_a_git_mint_makes_the_root_with_its_parents_runs_git_init_then_adds_the_row(
        tmp_path, git_ok, capsys):
    root = tmp_path / "a" / "b" / "web"
    code, out, err = run(capsys, "add", "web", "--root", str(root), "--create")
    assert code == 0, err
    assert root.is_dir() and (root / ".git").is_dir()
    assert git_ok.read_text().split() == ["init", str(root)]
    assert f"made {tmp_path / 'a'}" in err and "git init" in err
    assert api.get("web").root == str(root)
    assert out.splitlines()[0] == "web"


@posix_only
def test_a_git_mint_onto_an_empty_directory(tmp_path, git_ok):
    root = tmp_path / "empty"
    root.mkdir()
    added = acts.add("e", root=str(root), create=True)
    assert (root / ".git").is_dir()
    assert added.notes == [f"ran git init in {root}, which was empty"]


def test_git_missing_refuses_and_makes_nothing(tmp_path, no_git, capsys):
    root = tmp_path / "a" / "web"
    code, _out, err = run(capsys, "add", "web", "--root", str(root), "--create")
    assert code == 3 and "git is not on PATH" in err and "fix:" in err
    assert not (tmp_path / "a").exists()
    assert api.list_projects() == []


@pytest.mark.parametrize("kind", ["project", "topic"])
def test_a_non_empty_directory_refuses_and_is_left_alone(tmp_path, site, git_ok, kind):
    site.contribution("notes", KINDS)
    root = tmp_path / "full"
    root.mkdir()
    (root / "keep.txt").write_text("mine")
    with pytest.raises(Refused) as caught:
        acts.add("full", root=str(root), kind=kind, create=True)
    assert caught.value.exit_code == 3
    assert "not empty" in caught.value.reason and "without --create" in caught.value.fix
    assert os.listdir(root) == ["keep.txt"]
    assert api.list_projects() == []


def test_a_file_where_the_root_goes_refuses(tmp_path, git_ok):
    (tmp_path / "f").write_text("x")
    with pytest.raises(Refused, match="not a directory"):
        acts.add("f", root=str(tmp_path / "f"), create=True)


@posix_only
def test_a_failing_git_leaves_the_directory_it_made_and_no_row(tmp_path, monkeypatch,
                                                                capsys):
    monkeypatch.setenv("PATH", fake_git(tmp_path / "bin",
                                        'echo "fatal: no space" >&2\nexit 128'))
    root = tmp_path / "new" / "web"
    code, out, err = run(capsys, "add", "web", "--root", str(root), "--create")
    assert code == 1 and out == ""
    assert "fatal: no space" in err
    assert f"{tmp_path / 'new'} is left as made" in err
    assert f"git init {root}" in err and f"moxi projects add web --root {root}" in err
    assert root.is_dir()
    assert api.list_projects() == []


@posix_only
def test_a_git_that_runs_too_long_is_stopped_and_leaves_no_row(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", fake_git(tmp_path / "bin", "sleep 30"))
    monkeypatch.setattr(acts, "GIT_TIMEOUT", 0.2)
    root = tmp_path / "slow"
    with pytest.raises(MintFailed) as caught:
        acts.add("slow", root=str(root), create=True)
    assert caught.value.exit_code == 1 and "ran past" in caught.value.reason
    assert caught.value.left == str(root)
    assert api.list_projects() == []


@posix_only
def test_a_row_lost_after_a_git_mint_leaves_the_directory_and_names_the_add(
        tmp_path, git_ok, monkeypatch, capsys):
    mint = acts._mint

    def overtaken(planned, git, command):
        made = mint(planned, git, command)
        store.write(lambda conn: store.insert(conn, key="web", root="/srv/elsewhere"))
        return made

    monkeypatch.setattr(acts, "_mint", overtaken)
    root = tmp_path / "web"
    code, _out, err = run(capsys, "add", "web", "--root", str(root), "--create")
    assert code == 3 and "taken" in err
    assert f"made {root} and ran git init" in err and "is left as it is" in err
    assert f"moxi projects add web --root {root}` adds its row" in err
    assert (root / ".git").is_dir()
    assert api.get("web").root == "/srv/elsewhere"


@posix_only
def test_a_row_write_locked_out_after_the_mint_is_exit_75_naming_the_add(
        tmp_path, git_ok, monkeypatch):
    def busy(fn, where=None):
        raise api.Busy(where)

    monkeypatch.setattr(store, "write", busy)
    root = tmp_path / "web"
    with pytest.raises(api.Busy) as caught:
        acts.add("web", root=str(root), create=True)
    assert caught.value.exit_code == 75
    assert f"moxi projects add web --root {root}" in caught.value.fix
    assert root.is_dir()


@posix_only
def test_a_taken_key_or_root_refuses_before_anything_is_made(tmp_path, golden_copy, git_ok):
    with pytest.raises(Refused, match="taken"):
        acts.add("ALPHA", root=str(tmp_path / "x"), kind="agent", create=True)
    with pytest.raises(Refused, match="already project 'gamma'"):
        acts.add("other", root=os.path.join(golden_copy, "gamma"), create=True)
    assert not (tmp_path / "x").exists() and not git_ok.exists()
    assert not os.path.exists(os.path.join(golden_copy, "gamma"))


# --- the dir and refuse mints ------------------------------------------------------------

def test_a_dir_mint_makes_only_the_directory_with_git_off_path(tmp_path, site, no_git):
    site.contribution("notes", KINDS)
    root = tmp_path / "notes" / "t1"
    added = acts.add("t1", root=str(root), kind="topic", create=True)
    assert root.is_dir() and os.listdir(root) == []
    assert added.project.kind == "topic"
    assert added.notes == [f"made {tmp_path / 'notes'}"]


def test_a_refuse_kind_is_refused_before_anything_is_made(tmp_path, site, git_ok, capsys):
    site.contribution("other", KINDS)
    root = tmp_path / "r"
    code, _out, err = run(capsys, "add", "r", "--root", str(root), "--kind", "remote",
                          "--create")
    assert code == 3
    assert "'remote' (other)" in err and "without --create" in err
    assert not root.exists() and not git_ok.exists()
    assert api.list_projects() == []
    assert run(capsys, "add", "r", "--root", str(root), "--kind", "remote")[0] == 0


@posix_only
def test_a_two_element_kind_mints_with_git(tmp_path, site, git_ok):
    site.contribution("other", KINDS)
    acts.add("rp", root=str(tmp_path / "rp"), kind="repo", create=True)
    assert (tmp_path / "rp" / ".git").is_dir()


def test_a_dir_mint_onto_a_non_empty_directory_refuses(tmp_path, site, no_git):
    site.contribution("notes", KINDS)
    root = tmp_path / "t"
    root.mkdir()
    (root / "a").write_text("x")
    with pytest.raises(Refused, match="not empty"):
        acts.add("t", root=str(root), kind="topic", create=True)


def test_a_mint_with_a_defaulted_root(tmp_path, site, no_git):
    base = tmp_path / "notes"
    site.contribution("notes", KINDS.replace("INTERFACE = 2", f'''INTERFACE = 2

def prepare(fields):
    if fields["kind"] == "topic":
        return {{"root": {str(base)!r} + "/" + fields["key"]}}
    return None
'''))
    added = acts.add("t2", kind="topic", create=True)
    assert added.project.root == str(base / "t2")
    assert (base / "t2").is_dir()


def test_a_mkdir_that_fails_leaves_no_row_and_nothing_made(tmp_path, no_git, site, capsys):
    site.contribution("notes", KINDS)
    (tmp_path / "file").write_text("in the way")
    root = tmp_path / "file" / "t"
    code, _out, err = run(capsys, "add", "t", "--root", str(root), "--kind", "topic",
                          "--create")
    assert code == 1
    assert "could not be made" in err and "nothing was made" in err
    assert f"moxi projects add t --root {root} --kind topic" in err
    assert api.list_projects() == []


def test_a_mkdir_that_fails_part_way_names_the_topmost_directory_left(
        tmp_path, no_git, site, monkeypatch):
    site.contribution("notes", KINDS)
    root = tmp_path / "top" / "mid" / "t"

    def part_way(path, exist_ok=False):
        os.mkdir(tmp_path / "top")
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(acts.os, "makedirs", part_way)
    with pytest.raises(MintFailed) as caught:
        acts.add("t", root=str(root), kind="topic", create=True)
    assert caught.value.exit_code == 1
    assert caught.value.left == str(tmp_path / "top")
    assert f"{tmp_path / 'top'} is left as made" in caught.value.reason
    assert api.list_projects() == []


def test_a_row_lost_after_a_dir_mint_leaves_the_directory(tmp_path, site, no_git,
                                                          monkeypatch):
    site.contribution("notes", KINDS)
    mint = acts._mint

    def overtaken(planned, git, command):
        made = mint(planned, git, command)
        store.write(lambda conn: store.insert(conn, key="t", root="/srv/other"))
        return made

    monkeypatch.setattr(acts, "_mint", overtaken)
    root = tmp_path / "t"
    with pytest.raises(Refused) as caught:
        acts.add("t", root=str(root), kind="topic", create=True)
    assert f"made {root}, and is left as it is" in caught.value.reason
    assert f"moxi projects add t --root {root} --kind topic" in caught.value.fix
    assert root.is_dir() and not (root / ".git").exists()


# --- the real git ------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_a_real_add_create_makes_a_git_repository(tmp_path, capsys):
    root = tmp_path / "real" / "repo"
    code, _out, err = run(capsys, "add", "real", "--root", str(root), "--create")
    assert code == 0, err
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                          capture_output=True, text=True, check=False)
    assert done.stdout.strip() == "true"
    assert api.get("real").root == str(root)


# --- the commands a message names --------------------------------------------------------

@pytest.mark.parametrize("text, quoted", [
    (r"C:\src\web", r"C:\src\web"),
    (r"C:\Users\Jane Doe\web", r'"C:\Users\Jane Doe\web"'),
    ("C:\\Program Files (x86)\\web", '"C:\\Program Files (x86)\\web"'),
    (r"C:\src\R&D", r'"C:\src\R&D"'),
    (r"\\server\share\my web", r'"\\server\share\my web"'),
    ("C:\\a&b\\", '"C:\\a&b\\\\"'),
    ("", '""'),
])
def test_a_windows_command_quotes_a_path_holding_a_space_or_special_character(
        monkeypatch, text, quoted):
    monkeypatch.setattr(os, "name", "nt")
    assert acts._quote(text) == quoted


def test_a_windows_add_command_pastes_back_a_root_with_a_space(monkeypatch):
    from moxi_projects.contributions import Planned

    row = {"key": "web", "root": r"C:\Users\Jane Doe\web", "kind": "project",
           "lead": None, "notes_root": None}
    monkeypatch.setattr(os, "name", "nt")
    assert acts._add_command(Planned(row, {}, None), None) == (
        r'moxi projects add web --root "C:\Users\Jane Doe\web"')


def test_a_posix_command_quotes_as_the_shell_does(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert acts._quote("/srv/my web") == "'/srv/my web'"
    assert acts._quote("/srv/web") == "/srv/web"
