"""The tag check: what it refuses, what it lets through, and which files it reads.

The tags below are test data for the pattern, and the root's `.residue-allow` names each
one for this file, as it does for any tag that is data.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from moxi_dev import tags
from moxi_dev.tags import Hit, scan, tags_in


@pytest.mark.parametrize("tag", ["[B5]", "(R5)", "D07", "D07b", "§5", "design v4",
                                 "MAJOR 2", "round home/slug-two", "Round home-one/slug"])
def test_each_shape_is_a_tag(tag):
    assert tags_in(f"a line that cites {tag} in passing\n") == [(1, tag)]


@pytest.mark.parametrize("text", [
    "the hooks' law 4 holds", "def test_l5_holds():", "--finding L<n>", "L1x and XL12",
    "x[0] = y[1]", "D3 and D100",
    "a list [a1] of things", "version 3.12", "X11 sockets", "around home/slug",
    "@pytest.mark.finding(\"L50\")", "pytest.mark.finding('L7')",
    "@pytest.mark.finding('L7', \"L8\")", "@pytest.mark.finding( 'L7' , )",
    "a round trip/second", "around home-one/slug", "D07bc",
])
def test_plain_text_and_a_finding_mark_are_not_tags(text):
    assert tags_in(text) == []


@pytest.mark.parametrize("text", [
    'def test_it():\n    """Fixes L12: the record is kept."""\n',
    "# see L12\n", "FIX = 'L12'\n", "the [L12] note\n",
])
def test_a_finding_id_anywhere_but_a_finding_mark_is_a_tag(text):
    found = tags_in(text)
    assert [tag for _, tag in found] in (["L12"], ["[L12]"])


def test_only_the_mark_s_own_argument_is_let_through():
    line = '@pytest.mark.finding("L12")  # fixes L13\n'
    assert tags_in(line) == [(1, "L13")]
    assert tags_in('finding("L12")\n') == [(1, "L12")]


def test_a_finding_mark_wrapped_over_lines_passes_as_a_one_line_mark_does():
    wrapped = ('@pytest.mark.finding(\n    "L12",\n    "L13",\n)\n'
               "def test_it():\n    pass  # see L50\n")
    assert tags_in(wrapped) == [(6, "L50")]
    assert tags_in("@pytest.mark.finding(\n    'L7'\n)\n") == []
    assert tags_in('finding(\n    "L12",\n)\nD07\n') == [(2, "L12"), (4, "D07")]


def test_line_numbers_count_from_one():
    assert tags_in("one\ntwo D07\nthree\n") == [(2, "D07")]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q")
    (root / ".gitignore").write_text("ignored/\n")
    return root


def test_scan_reads_tracked_and_untracked_files_but_not_ignored_ones(repo):
    (repo / "a.py").write_text("# per D07\n")
    git(repo, "add", "a.py")
    (repo / "b.md").write_text("see design v4\n")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "c.py").write_text("# per D07\n")
    assert scan(repo) == [Hit("a.py", 1, "D07"), Hit("b.md", 1, "design v4")]


def test_scan_skips_binary_files_the_lock_and_the_allow_file(repo):
    (repo / "blob.bin").write_bytes(b"\xff\xfe D07 \x00")
    (repo / "uv.lock").write_text("D07\n")
    (repo / ".residue-allow").write_text("a.py\t[B5]\tdata\n")
    assert scan(repo) == []


def test_the_allow_file_lets_exact_text_stand_in_one_file(repo):
    (repo / "a.py").write_text("PATTERN_INPUT = '[B5]'\nOTHER = '(R5)'\n")
    (repo / "b.py").write_text("PATTERN_INPUT = '[B5]'\n")
    (repo / ".residue-allow").write_text("# comment\n\na.py\t[B5]\tdata: the pattern's input\n")
    assert scan(repo) == [Hit("a.py", 2, "(R5)"), Hit("b.py", 1, "[B5]")]
    assert tags.allowed(repo) == {("a.py", "[B5]")}


def test_a_hit_reads_as_path_line_and_text():
    assert str(Hit("a.py", 3, "D07")) == "a.py:3: tag 'D07'"

