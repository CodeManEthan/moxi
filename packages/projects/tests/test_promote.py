"""`promote`: an inbox one-off of notes' made a project through add's own steps, its
folder moved to the project's notes folder, every refusal before anything moves, and the
move undone when the row write fails.

These read the installed notes through `moxi.modules`, as promote does, and skip where it
is not installed. The test kit points the home into the test's tmp, so notes' root is
the folder `notes` in that home, with its `inbox` and `topics` folders inside. git is
faked by a script on PATH.
"""

from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
from pathlib import Path

import moxi.modules
import pytest

from moxi_projects import acts, api, cli, store
from moxi_projects import contributions as contributions_
from moxi_projects.contributions import Contribution, Contributions
from moxi_projects.errors import Broken, MoveFailed, Refused

needs_notes = pytest.mark.skipif(not moxi.modules.present("notes"),
                                 reason="notes is not installed")
posix_only = pytest.mark.skipif(sys.platform == "win32",
                                reason="the fake git is a POSIX shell script")


@pytest.fixture(autouse=True)
def _fresh_modules():
    moxi.modules.reset()
    yield
    moxi.modules.reset()


def notes() -> Path:
    return Path.home() / "notes"


def one_off(slug: str = "sketch", files: int = 2) -> Path:
    """An inbox one-off holding `files` notes."""
    folder = notes() / "inbox" / slug
    folder.mkdir(parents=True)
    for n in range(files):
        (folder / f"note-{n}.md").write_text(f"note {n}\n")
    return folder


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def untouched(folder: Path, files: int = 2) -> bool:
    return folder.is_dir() and len(os.listdir(folder)) == files


@pytest.fixture
def git_ok(tmp_path, monkeypatch):
    """A git on PATH that makes `.git` in its last argument and logs its arguments."""
    log = tmp_path / "git.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "git"
    script.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nmkdir -p "$2/.git"\nexit 0\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]))
    return log


# --- the topic arm -----------------------------------------------------------------------

@needs_notes
def test_a_one_off_promoted_to_a_topic_moves_to_its_topic_folder(capsys):
    source = one_off()
    code, out, err = run(capsys, "promote", "sketch", "gardening")
    assert code == 0, err
    target = notes() / "topics" / "gardening"
    assert not source.exists() and untouched(target)
    project = api.get("gardening")
    assert (project.kind, project.root, project.notes_root) == (
        "topic", str(target), str(target))
    assert out.splitlines()[0] == "gardening"
    assert f"moved {source} to {target}" in err


@needs_notes
def test_a_promoted_topic_takes_no_mint_and_starts_no_process(monkeypatch):
    one_off()

    def refuse(*args, **kwargs):
        raise AssertionError("promote started a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    added = acts.promote("sketch", "gardening")
    assert added.project.kind == "topic"
    assert not (notes() / "topics" / "gardening" / ".git").exists()


@needs_notes
def test_the_row_comes_from_adds_own_path(monkeypatch):
    one_off()
    seen = []
    plan = contributions_.plan_add

    def spy(key, **fields):
        seen.append((key, fields))
        return plan(key, **fields)

    monkeypatch.setattr(contributions_, "plan_add", spy)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("a subprocess"))
    acts.promote("sketch", "gardening")
    (key, fields), = seen
    assert key == "gardening"
    assert (fields["kind"], fields["root"], fields["notes_root"]) == ("topic", None, None)


# --- the project arm ---------------------------------------------------------------------

@needs_notes
def test_a_one_off_promoted_to_a_project_moves_to_the_folder_notes_computes(capsys):
    source = one_off()
    root = Path.home() / "code" / "alpha"
    code, _out, err = run(capsys, "promote", "sketch", "alpha", "--kind", "project",
                          "--root", str(root))
    assert code == 0, err
    target = notes() / "code" / "alpha"
    assert not source.exists() and untouched(target)
    project = api.get("alpha")
    assert (project.kind, project.root, project.notes_root) == (
        "project", str(root), str(target))
    assert "doesn't exist yet" in err and f"moved {source} to {target}" in err


@needs_notes
@posix_only
def test_a_project_promoted_with_create_mints_its_root_first(git_ok):
    source = one_off()
    root = Path.home() / "code" / "alpha"
    added = acts.promote("sketch", "alpha", kind="project", root=str(root), create=True)
    assert (root / ".git").is_dir()
    assert git_ok.read_text().split() == ["init", str(root)]
    assert not source.exists() and untouched(notes() / "code" / "alpha")
    assert added.notes[0].startswith("made ") and "git init" in added.notes[0]


@needs_notes
def test_the_project_arm_stores_notes_folder_even_past_another_default():
    one_off()
    other = Contribution("beta", "beta", 2, {}, {}, None,
                         lambda fields: {"notes_root": "/srv/elsewhere"})
    found = contributions_.load()
    both = Contributions([*found.loaded, other], list(found.unloadable))
    root = Path.home() / "code" / "alpha"
    added = acts.promote("sketch", "alpha", kind="project", root=str(root),
                         contributions=both)
    assert added.project.notes_root == str(notes() / "code" / "alpha")


# --- refusals, each with nothing moved ---------------------------------------------------

@needs_notes
@pytest.mark.parametrize("argv, code, said", [
    (("promote", "Bad_Slug", "gardening"), 3, "not an inbox slug"),
    (("promote", "nosuch", "gardening"), 3, "no inbox one-off is named 'nosuch'"),
    (("promote", "sketch", "bad key"), 3, "not a valid project key"),
    (("promote", "sketch", "gardening", "--kind", "nosuch", "--root", "/srv/x"), 3,
     "unknown"),
    (("promote", "sketch", "gardening", "--root", "/srv/x"), 3, "drop --root"),
    (("promote", "sketch", "alpha", "--kind", "project"), 2, "needs the project's root"),
])
def test_refusals_move_nothing_and_print_the_fix(capsys, argv, code, said):
    source = one_off()
    got, out, err = run(capsys, *argv)
    assert (got, out) == (code, "")
    assert said in err and "fix:" in err
    assert untouched(source)
    assert not (notes() / "topics").exists()
    assert api.list_projects() == []


@needs_notes
def test_create_on_a_topic_is_refused_and_nothing_is_made(capsys):
    source = one_off()
    code, _out, err = run(capsys, "promote", "sketch", "gardening", "--create")
    assert code == 3
    assert ("promote makes a topic's folder by moving the one-off there; drop --create"
            in err)
    assert untouched(source) and not (notes() / "topics").exists()


@needs_notes
def test_a_taken_key_or_root_is_refused_before_anything_moves(tmp_path):
    source = one_off()
    root = Path.home() / "code" / "alpha"
    acts.add("alpha", root=str(tmp_path / "elsewhere"))
    with pytest.raises(Refused, match="taken") as caught:
        acts.promote("sketch", "ALPHA", kind="project", root=str(root))
    assert caught.value.exit_code == 3
    acts.add("beta", root=str(root))
    with pytest.raises(Refused, match="already project 'beta'"):
        acts.promote("sketch", "gamma", kind="project", root=str(root))
    assert untouched(source) and not (notes() / "code").exists()


@needs_notes
@pytest.mark.parametrize("topic", [True, False])
def test_a_target_that_exists_is_refused_and_never_merged(topic):
    source = one_off()
    target = notes() / ("topics/gardening" if topic else "code/alpha")
    target.mkdir(parents=True)
    (target / "mine.md").write_text("mine\n")
    kwargs = {} if topic else {"kind": "project", "root": str(Path.home() / "code/alpha")}
    with pytest.raises(Refused, match="never merges two folders"):
        acts.promote("sketch", "gardening" if topic else "alpha", **kwargs)
    assert untouched(source) and os.listdir(target) == ["mine.md"]
    assert api.list_projects() == []


@needs_notes
def test_a_root_notes_gives_no_folder_is_refused(tmp_path):
    source = one_off()
    with pytest.raises(Refused, match="no notes folder") as caught:
        acts.promote("sketch", "alpha", kind="project", root=str(tmp_path / "off-home"))
    assert caught.value.exit_code == 3 and "as a topic" in caught.value.fix
    assert untouched(source) and api.list_projects() == []


@needs_notes
@posix_only
def test_a_git_mint_inside_the_notes_root_is_refused(git_ok):
    source = one_off()
    root = notes() / "work" / "alpha"
    with pytest.raises(Refused, match="inside the notes root") as caught:
        acts.promote("sketch", "alpha", kind="project", root=str(root), create=True)
    assert caught.value.exit_code == 3
    assert not root.exists() and not git_ok.exists()
    assert untouched(source) and api.list_projects() == []


@needs_notes
def test_a_contribution_that_wont_load_refuses_as_add_does():
    source = one_off()
    found = contributions_.load()
    broken = Contributions(list(found.loaded), [("beta", "the entry beta won't load")])
    with pytest.raises(Broken) as caught:
        acts.promote("sketch", "gardening", contributions=broken)
    assert caught.value.exit_code == 1 and "beta" in caught.value.reason
    assert untouched(source)


@pytest.mark.parametrize("how", ["not importable", "absent"])
def test_without_notes_it_refuses_naming_the_install(capsys, monkeypatch, how):
    class Absent:
        state, ok, reason = "absent", False, "no distribution moxi-notes"

    available, load = moxi.modules.available, moxi.modules.load
    monkeypatch.setattr(moxi.modules, "available", lambda name: (
        how != "not importable" if name == "notes" else available(name)))
    monkeypatch.setattr(moxi.modules, "load", lambda name, interface=None: (
        Absent() if name == "notes" else load(name, interface=interface)))
    code, out, err = run(capsys, "promote", "sketch", "gardening")
    assert (code, out) == (3, "")
    assert "notes is not installed" in err and "moxi install notes" in err


def test_a_notes_that_wont_load_is_exit_1(monkeypatch):
    class Found:
        state, ok, reason = "broken", False, "its API raised ImportError"

    monkeypatch.setattr(moxi.modules, "available", lambda name: True)
    monkeypatch.setattr(moxi.modules, "load", lambda name, interface=None: Found())
    with pytest.raises(Broken) as caught:
        acts.promote("sketch", "gardening")
    assert caught.value.exit_code == 1 and "ImportError" in caught.value.reason
    assert "moxi uninstall notes" in caught.value.fix


# --- a move or a write that fails --------------------------------------------------------

@needs_notes
def test_a_move_across_file_systems_is_exit_1_with_nothing_moved(monkeypatch, capsys):
    source = one_off()

    def across(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", across)
    code, _out, err = run(capsys, "promote", "sketch", "gardening")
    assert code == 1
    assert "different file systems" in err and "nothing was moved" in err
    assert "moxi projects add gardening" in err
    assert untouched(source)
    assert not (notes() / "topics").exists()
    assert api.list_projects() == []


@needs_notes
def test_a_write_locked_out_moves_the_one_off_back_and_exits_75(monkeypatch, capsys):
    source = one_off()

    def busy(fn, where=None):
        raise api.Busy(where)

    monkeypatch.setattr(store, "write", busy)
    code, _out, err = run(capsys, "promote", "sketch", "gardening")
    assert code == 75
    assert f"moved back to {source}" in err
    assert "moxi projects promote sketch gardening" in err
    assert untouched(source)
    assert not (notes() / "topics").exists()


@needs_notes
@posix_only
def test_a_key_lost_to_a_race_moves_the_one_off_back_and_leaves_the_minted_root(
        git_ok, monkeypatch):
    source = one_off()
    root = Path.home() / "code" / "alpha"
    write = store.write

    def overtaken(fn, where=None):
        write(lambda conn: store.insert(conn, key="alpha", root="/srv/elsewhere"))
        return write(fn, where)

    monkeypatch.setattr(store, "write", overtaken)
    with pytest.raises(Refused, match="taken") as caught:
        acts.promote("sketch", "alpha", kind="project", root=str(root), create=True)
    assert caught.value.exit_code == 3
    assert f"moved back to {source}" in caught.value.reason
    assert "is left as it is" in caught.value.reason
    assert (f"moxi projects promote sketch alpha --kind project --root {root}`"
            in caught.value.fix)
    assert untouched(source) and (root / ".git").is_dir()
    assert not (notes() / "code").exists()


@needs_notes
def test_a_move_back_that_fails_names_both_folders_and_the_add(monkeypatch):
    source = one_off()
    target = notes() / "topics" / "gardening"
    rename = os.rename

    def once(src, dst):
        if Path(src) == target:
            raise PermissionError(errno.EACCES, "Permission denied")
        rename(src, dst)

    def busy(fn, where=None):
        raise api.Busy(where)

    monkeypatch.setattr(os, "rename", once)
    monkeypatch.setattr(store, "write", busy)
    with pytest.raises(api.Busy) as caught:
        acts.promote("sketch", "gardening")
    assert caught.value.exit_code == 75
    assert f"at {target} and could not be moved back to {source}" in caught.value.reason
    assert "moxi projects add gardening" in caught.value.fix
    assert untouched(target)

