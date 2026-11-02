"""The doctor's rows: each one's states."""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

import pytest

import moxi.paths
from moxi_notes import doctor, setup

from gate_kit import allow_line, notes_repo, sh
from notes_kit import configure, git_env, home, moxi_cmd, no_projects, repo

posix = pytest.mark.skipif(sys.platform == "win32",
                           reason="git hooks through POSIX sh; the Windows run is owed")


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


def rows() -> dict[str, object]:
    return {row.name: row for row in doctor.rows()}


def state(name: str) -> str:
    return rows()[name].state


def set_up(**values) -> Path:
    root = notes_repo()
    if values:
        configure(**values)
    assert setup.setup(out=io.StringIO()) == 0
    return root


# -- root ---------------------------------------------------------------------------------

def test_root_fails_when_the_folder_is_missing():
    row = rows()["root"]
    assert row.state == "fail" and "missing" in row.detail
    assert "moxi notes setup" in row.fix


def test_root_warns_when_the_folder_is_no_git_repo_and_the_rest_is_off():
    (home() / "notes").mkdir()
    found = rows()
    assert found["root"].state == "warn" and found["root"].fix == "moxi notes setup"
    for name in ("snapshot", "gates", "filter", "allowlist"):
        assert found[name].state == "off", name


def test_root_warns_when_it_is_inside_a_repo_but_not_its_top():
    outer = repo(home() / "code", commit=True)
    (outer / "notes").mkdir()
    configure(root=str(outer / "notes"))
    row = rows()["root"]
    assert row.state == "warn" and "not its top" in row.detail


def test_a_config_that_cannot_be_read_fails_the_root_row():
    path = Path(moxi.paths.Layout("notes").config_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("max_mb = 'many'\n", encoding="utf-8")
    found = list(doctor.rows())
    assert [(r.name, r.state) for r in found] == [("root", "fail")]


@posix
def test_after_setup_every_row_is_ok():
    set_up()
    found = rows()
    assert {name: row.state for name, row in found.items()} == {
        "root": "ok", "snapshot": "ok", "gates": "ok", "filter": "ok", "allowlist": "ok",
        "topics": "ok"}


# -- snapshot -----------------------------------------------------------------------------

@posix
def test_snapshot_is_off_with_both_triggers_off_and_warns_on_a_spec_that_differs():
    set_up()
    configure(snapshot__interval=30)
    row = rows()["snapshot"]
    assert row.state == "warn" and "differs" in row.detail
    assert row.fix == "moxi notes setup"
    configure(snapshot__auto=False)
    assert state("snapshot") == "off"
    configure(snapshot__timer=True)
    assert state("snapshot") == "warn"
    assert setup.setup(out=io.StringIO()) == 0
    assert state("snapshot") == "ok"


@posix
def test_snapshot_warns_when_no_spec_is_saved():
    notes_repo()
    row = rows()["snapshot"]
    assert row.state == "warn" and "no spec" in row.detail


# -- gates --------------------------------------------------------------------------------

@posix
def test_gates_fail_when_a_piece_is_missing_or_disabled():
    from moxi_repo import api

    root = notes_repo()
    row = rows()["gates"]
    assert row.state == "fail" and "notes/secret-gate is not registered" in row.detail
    assert setup.setup(out=io.StringIO()) == 0
    api.disable(root, "notes/secret-gate")
    row = rows()["gates"]
    assert row.state == "fail" and "notes/secret-gate is disabled" in row.detail
    assert row.fix == "moxi notes setup"
    api.remove(root, "notes/push-gate")
    assert "notes/push-gate is not registered" in rows()["gates"].detail


# -- filter -------------------------------------------------------------------------------

@posix
def test_filter_fails_under_redact_without_the_driver_or_the_attribute():
    root = set_up(action="redact")
    assert state("filter") == "ok"
    attributes = root / ".git" / "info" / "attributes"
    attributes.write_text("", encoding="utf-8")
    row = rows()["filter"]
    assert row.state == "fail" and "attribute line" in row.detail
    sh(root, "config", "--remove-section", "filter.moxi-notes")
    assert "the driver command" in rows()["filter"].detail
    configure(action="leave-out")
    assert state("filter") == "ok"


@posix
def test_filter_fails_when_its_interpreter_is_missing():
    root = set_up(action="redact")
    sh(root, "config", "filter.moxi-notes.process",
       f"'{home() / 'gone' / 'python'}' -I -m moxi_notes.filter")
    row = rows()["filter"]
    assert row.state == "fail" and "interpreter" in row.detail and "missing" in row.detail


@posix
def test_filter_warns_when_required_or_written_for_another_interpreter():
    root = set_up(action="redact")
    sh(root, "config", "filter.moxi-notes.required", "true")
    row = rows()["filter"]
    assert row.state == "warn" and "required" in row.detail
    sh(root, "config", "filter.moxi-notes.required", "false")
    other = home() / "other-python"
    shutil.copyfile(sys.executable, other)
    sh(root, "config", "filter.moxi-notes.process", f"'{other}' -I -m moxi_notes.filter")
    row = rows()["filter"]
    assert row.state == "warn" and "another interpreter" in row.detail


@posix
def test_filter_warns_on_leftovers_under_leave_out():
    from moxi_notes import filter as clean_filter

    root = set_up()
    clean_filter.install(root)
    row = rows()["filter"]
    assert row.state == "warn" and "left from redact" in row.detail
    assert row.fix == "moxi notes setup"
    assert setup.setup(out=io.StringIO()) == 0
    assert state("filter") == "ok"


# -- allowlist ----------------------------------------------------------------------------

@posix
def test_allowlist_reads_the_private_file_in_the_common_git_dir():
    root = set_up()
    private = root / ".git" / "secrets-allow-private"
    private.write_text(allow_line("*", "private-names/private-name", "text:quillon"),
                       encoding="utf-8")
    row = rows()["allowlist"]
    assert row.state == "ok" and "1 allowance lines" in row.detail
    assert str(private) in row.detail


@posix
def test_allowlist_fails_on_a_malformed_line_by_file_and_line():
    root = set_up()
    private = root / ".git" / "secrets-allow-private"
    private.write_text("# fine\nnot a line\n", encoding="utf-8")
    row = rows()["allowlist"]
    assert row.state == "fail"
    assert f"{private}: line 2" in row.detail and "no allowance applies" in row.detail
    private.unlink()
    (root / ".secrets-allow").write_text(
        allow_line("*", "private-names/private-name", "text:quillon"), encoding="utf-8")
    row = rows()["allowlist"]
    assert row.state == "fail" and ".secrets-allow: line 1" in row.detail


@posix
def test_allowlist_warns_on_a_private_file_at_the_root():
    root = set_up()
    (root / ".secrets-allow-private").write_text("", encoding="utf-8")
    row = rows()["allowlist"]
    assert row.state == "warn" and "is never read" in row.detail
    assert row.fix == f"move its lines to {root / '.git' / 'secrets-allow-private'}"


@posix
def test_allowlist_fails_closed_for_a_separate_git_dir(tmp_path):
    root = home() / "notes"
    (tmp_path / "store").mkdir()
    sh(tmp_path, "init", "-q", f"--separate-git-dir={tmp_path / 'store' / 'notes.git'}",
       str(root))
    assert setup.setup(out=io.StringIO()) == 0
    row = rows()["allowlist"]
    assert row.state == "fail" and "is not a folder named .git" in row.detail


# -- topics -------------------------------------------------------------------------------

def test_topics_is_off_without_projects(monkeypatch):
    no_projects(monkeypatch)
    row = rows()["topics"]
    assert row.state == "off" and row.fix == "moxi install projects"


def test_topics_warns_on_a_missing_folder_and_on_a_git_inside():
    for key in ("gardening", "baking"):
        code, out, err = moxi_cmd("projects", "add", key, "--kind", "topic", "--create")
        assert code == 0, out + err
    row = rows()["topics"]
    assert row.state == "ok" and "2 topics" in row.detail
    topics = home() / "notes" / "topics"
    (topics / "baking" / ".git").mkdir()
    row = rows()["topics"]
    assert row.state == "warn" and "baking" in row.detail and ".git" in row.detail
    (topics / "gardening").rmdir()
    row = rows()["topics"]
    assert row.state == "warn" and "missing: gardening" in row.detail


def test_moxi_doctor_notes_shows_the_rows():
    code, out, err = moxi_cmd("doctor", "notes")
    for name in ("root", "topics"):
        assert name in out + err
