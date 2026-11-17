"""The harness file writer, `moxi.harnessfile`, over plain folders under tmp."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import WINDOWS
from moxi import harnessfile
from moxi.harnessfile import Doc, Refused
from moxi_dev.testing import need_symlinks

POSIX = pytest.mark.skipif(WINDOWS, reason="POSIX permission bits")


@pytest.fixture(name="harness_dir")
def harness_dir_fixture(tmp_path):
    """A harness's folder: where its files live, apart from the backups."""
    path = tmp_path / "harness"
    path.mkdir()
    return path


@pytest.fixture
def backups(tmp_path):
    return tmp_path / "backups"


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# -- reading ------------------------------------------------------------------------------

def test_a_missing_file_reads_empty_and_is_unchanged_until_given_data(harness_dir):
    doc = Doc(harness_dir / "settings.json", "json")
    assert doc.raw is None and doc.data == {} and doc.original is None
    assert not doc.changed and doc.empty
    doc.data["model"] = "x"
    assert doc.changed and not doc.empty
    text = Doc(harness_dir / "NOTES.md", "text")
    assert text.raw is None and text.data == "" and text.empty and not text.changed


def test_a_blank_json_file_reads_as_an_empty_object(harness_dir):
    (harness_dir / "settings.json").write_text("  \n")
    doc = Doc(harness_dir / "settings.json", "json")
    assert doc.data == {} and doc.raw == b"  \n"


def test_doc_takes_a_string_path(harness_dir):
    (harness_dir / "settings.json").write_text('{"a": 1}\n')
    doc = Doc(str(harness_dir / "settings.json"), "json")
    assert doc.path == harness_dir / "settings.json" and doc.data == {"a": 1}


def test_a_doc_unchanged_by_the_caller_is_not_changed_whatever_its_layout(harness_dir):
    (harness_dir / "settings.json").write_text('{"b":1,"a":2}')
    doc = Doc(harness_dir / "settings.json", "json")
    assert not doc.changed
    doc.data["b"] = 1
    assert not doc.changed


# -- refusals -----------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "line"), [
    ('{\n  // mine\n  "model": "opus"\n}\n', "is not plain JSON ("),
    ("[1, 2]\n", "is not a JSON object"),
    ('"text"\n', "is not a JSON object")])
def test_json_that_is_not_an_object_is_refused_with_one_line(harness_dir, text, line):
    path = harness_dir / "settings.json"
    path.write_text(text)
    with pytest.raises(Refused) as caught:
        Doc(path, "json")
    said = str(caught.value)
    assert said.startswith(f"{path} {line}")
    assert "\n" not in said
    assert path.read_text() == text


def test_the_not_an_object_line_is_exactly_the_path_and_why(harness_dir):
    path = harness_dir / "settings.json"
    path.write_text("[]")
    with pytest.raises(Refused, match=r"is not a JSON object$") as caught:
        Doc(path, "json")
    assert str(caught.value) == f"{path} is not a JSON object"


def test_a_file_that_cant_be_read_is_refused(harness_dir):
    path = harness_dir / "settings.json"
    path.mkdir()
    with pytest.raises(Refused) as caught:
        Doc(path, "json")
    assert str(caught.value).startswith(f"{path} can't be read (")


@pytest.mark.parametrize("text", ["{oops", "[]", None])
def test_refusal_lines_name_no_module(harness_dir, text):
    path = harness_dir / "settings.json"
    if text is None:
        path.mkdir()
    else:
        path.write_text(text)
    with pytest.raises(Refused) as caught:
        Doc(path, "json")
    said = str(caught.value).replace(str(path), "<path>")
    for name in ("loadout", "statusline", "moxi", "install"):
        assert name not in said


# -- writing ------------------------------------------------------------------------------

@POSIX
def test_a_write_keeps_the_files_mode_key_order_and_indent(harness_dir, backups):
    path = harness_dir / "settings.json"
    path.write_text('{\n    "model": "opus",\n    "env": {"A": "1"}\n}\n')
    path.chmod(0o644)
    doc = Doc(path, "json")
    doc.data["hooks"] = {}
    assert harnessfile.write(doc, backups, 5) is None
    assert path.read_text() == ('{\n    "model": "opus",\n    "env": {\n        "A": "1"\n'
                                '    },\n    "hooks": {}\n}\n')
    assert mode(path) == 0o644


@POSIX
@pytest.mark.parametrize(("kind", "name", "want"), [("json", "settings.json", 0o600),
                                                     ("text", "NOTES.md", 0o644)])
def test_a_new_file_gets_its_kinds_mode_and_its_dir(tmp_path, backups, kind, name, want):
    path = tmp_path / "new-folder" / name
    doc = Doc(path, kind)
    if kind == "json":
        doc.data["a"] = 1
    else:
        doc.data = "hello\n"
    assert harnessfile.write(doc, backups, 5) is None
    assert mode(path) == want
    assert not backups.exists()   # nothing was there to keep


def test_a_text_files_bytes_that_arent_utf8_come_back_as_they_were(harness_dir, backups):
    path = harness_dir / "NOTES.md"
    path.write_bytes(b"caf\xe9 mine\n")
    doc = Doc(path, "text")
    doc.data += "added\n"
    assert harnessfile.write(doc, backups, 5) is None
    assert path.read_bytes() == b"caf\xe9 mine\nadded\n"


def test_a_link_is_written_through_and_stays_a_link(harness_dir, backups, tmp_path):
    need_symlinks()
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text('{"a": 1}\n')
    link = harness_dir / "settings.json"
    link.symlink_to(real)
    doc = Doc(link, "json")
    doc.data["b"] = 2
    assert harnessfile.write(doc, backups, 5) is None
    assert link.is_symlink() and os.readlink(link) == str(real)
    assert json.loads(real.read_text()) == {"a": 1, "b": 2}
    assert sorted(p.name for p in harness_dir.iterdir()) == ["settings.json"]


def test_a_file_changed_between_read_and_write_is_not_written(harness_dir, backups):
    path = harness_dir / "settings.json"
    path.write_text("{}")
    doc = Doc(path, "json")
    doc.data["mine"] = 1
    path.write_text('{"theirs": 1}')
    assert harnessfile.write(doc, backups, 5) == f"{path} changed since it was read"
    assert json.loads(path.read_text()) == {"theirs": 1}
    assert not backups.exists()


def test_a_file_made_since_a_missing_read_is_not_written(harness_dir, backups):
    path = harness_dir / "settings.json"
    doc = Doc(path, "json")
    doc.data["mine"] = 1
    path.write_text('{"theirs": 1}')
    assert harnessfile.write(doc, backups, 5) == f"{path} changed since it was read"
    assert json.loads(path.read_text()) == {"theirs": 1}


def test_a_file_that_cant_be_read_again_is_not_written(harness_dir, backups):
    path = harness_dir / "settings.json"
    path.write_text("{}")
    doc = Doc(path, "json")
    doc.data["mine"] = 1
    path.unlink()
    path.mkdir()
    line = harnessfile.write(doc, backups, 5)
    assert line.startswith(f"{path} can't be read again (")
    for name in ("loadout", "statusline", "install"):
        assert name not in line.replace(str(path), "<path>")


def test_a_write_leaves_no_lock_or_temp_file_beside_the_file(harness_dir, backups):
    path = harness_dir / "settings.json"
    path.write_text("{}")
    for n in range(3):
        doc = Doc(path, "json")
        doc.data["n"] = n
        assert harnessfile.write(doc, backups, 5) is None
    assert sorted(p.name for p in harness_dir.iterdir()) == ["settings.json"]


# -- backups ------------------------------------------------------------------------------

def test_a_backup_holds_the_old_bytes(harness_dir, backups):
    path = harness_dir / "settings.json"
    path.write_bytes(b'{"old":   true}\n')
    doc = Doc(path, "json")
    doc.data["new"] = True
    assert harnessfile.write(doc, backups, 5) is None
    kept = list(backups.iterdir())
    assert len(kept) == 1 and kept[0].name.endswith("-settings.json")
    assert kept[0].read_bytes() == b'{"old":   true}\n'
    if not WINDOWS:
        assert mode(backups) == 0o700 and mode(kept[0]) == 0o600


def test_backups_are_pruned_to_the_newest_keep_of_that_name(harness_dir, backups):
    path = harness_dir / "settings.json"
    other = harnessfile.backup(harness_dir / "other.json", backups, b"other", 1)
    for n in range(5):
        path.write_text(json.dumps({"n": n}))
        doc = Doc(path, "json")
        doc.data["n"] = n + 100
        assert harnessfile.write(doc, backups, 2) is None
    mine = sorted(p for p in backups.iterdir() if p.name.endswith("-settings.json"))
    assert [json.loads(p.read_text()) for p in mine] == [{"n": 3}, {"n": 4}]
    assert other.exists()   # another file's backups are not this file's to prune


def test_backup_returns_the_file_it_kept(harness_dir, backups):
    out = harnessfile.backup(harness_dir / "settings.json", backups, b"bytes", 5)
    assert out.parent == backups and out.read_bytes() == b"bytes"


# -- remove_empty -------------------------------------------------------------------------

def test_remove_empty_removes_a_file_with_nothing_left_and_keeps_a_backup(harness_dir, backups):
    path = harness_dir / "settings.json"
    path.write_text('{"hooks": {}}\n')
    doc = Doc(path, "json")
    del doc.data["hooks"]
    assert harnessfile.write(doc, backups, 5, remove_empty=True) is None
    assert not path.exists()
    assert [p.read_text() for p in backups.iterdir()] == ['{"hooks": {}}\n']


def test_remove_empty_of_a_missing_file_does_nothing(harness_dir, backups):
    doc = Doc(harness_dir / "NOTES.md", "text")
    assert harnessfile.write(doc, backups, 5, remove_empty=True) is None
    assert list(harness_dir.iterdir()) == []


def test_without_remove_empty_an_empty_doc_is_written(harness_dir, backups):
    path = harness_dir / "NOTES.md"
    path.write_text("gone\n")
    doc = Doc(path, "text")
    doc.data = "   \n"
    assert harnessfile.write(doc, backups, 5) is None
    assert path.read_text() == "   \n"


# -- imports ------------------------------------------------------------------------------

def test_harnessfile_imports_nothing_of_the_core_but_files():
    code = ("import sys, moxi.files; before = set(sys.modules); import moxi.harnessfile; "
            "print(' '.join(sorted(set(sys.modules) - before)))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                         check=True).stdout.split()
    assert [m for m in out if m.split(".")[0] == "moxi"] == [
        "moxi.harnessfile"]
