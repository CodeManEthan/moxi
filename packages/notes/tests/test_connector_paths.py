"""The path rules notes' tools keep (`moxi_notes.connector.pathrules`): every refusal, both
spellings of a link, the file read checked against the file judged, and the search's walk
opening only files a read would read."""

from __future__ import annotations

import os

import pytest
from connector_kit import FIFOS, HOST, make_fifo, make_root, token, write

if not HOST.ok:
    pytest.skip(f"the connector module is not installed: {HOST.reason}",
                allow_module_level=True)

from moxi_notes.connector import pathrules  # noqa: E402 - after the skip above
from moxi_notes.connector.pathrules import Refused  # noqa: E402

CAP = 2 << 20


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


def refusal(root, rel, *, cap=CAP):
    """The code the rules refuse `rel` with, reading it whole, or None."""
    try:
        judged = pathrules.judge_file(str(root), rel, cap_bytes=cap)
        pathrules.read_judged(judged.resolved, judged.st, cap_bytes=cap)
    except Refused as exc:
        return exc.code
    return None


@pytest.mark.parametrize("rel", ["", "/etc/passwd", "~/notes/a.md", "~", "a\x00b.md",
                                 "a\\b.md", "../a.md", "a/../../b.md", "a/.."])
def test_a_path_that_isnt_plainly_relative_is_bad_path(root, rel):
    write(root, "a.md", "alpha\n")
    assert refusal(root, rel) == "bad-path"


@pytest.mark.parametrize("rel", [".git/config", ".secrets-allow", "a/.env", "x.pem",
                                 "keys/credentials", "a/./b.md"])
def test_dot_parts_and_secret_paths_are_blocked(root, rel):
    write(root, rel.replace("/./", "/"), "alpha\n")
    assert refusal(root, rel) == "blocked"


def test_a_name_the_chat_redaction_would_change_is_blocked(root):
    rel = f"leak-{token()}.md"
    write(root, rel, "alpha\n")
    assert refusal(root, rel) == "blocked"


def test_a_plain_note_is_read_and_empty_parts_drop(root):
    write(root, "topics/a.md", "alpha\n")
    judged = pathrules.judge_file(str(root), "topics//a.md/", cap_bytes=CAP)
    assert judged.rel == "topics/a.md"
    assert pathrules.read_judged(judged.resolved, judged.st, cap_bytes=CAP) == b"alpha\n"


def test_a_link_out_of_the_root_is_outside_root(root, tmp_path):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("beta\n")
    os.symlink(outside, root / "away.md")
    os.symlink(tmp_path, root / "up")
    assert refusal(root, "away.md") == "outside-root"
    assert refusal(root, "up/elsewhere.md") == "outside-root"


def test_a_link_in_the_root_to_a_dot_dir_is_blocked_on_its_resolved_spelling(root):
    write(root, ".private/a.md", "alpha\n")
    os.symlink(root / ".private", root / "open")
    os.symlink(root / ".private" / "a.md", root / "plain.md")
    assert refusal(root, "open/a.md") == "blocked"
    assert refusal(root, "plain.md") == "blocked"


def test_a_link_inside_the_root_to_a_plain_note_is_followed(root):
    write(root, "real/a.md", "alpha\n")
    os.symlink(root / "real", root / "alias")
    assert refusal(root, "alias/a.md") is None


def test_a_file_with_a_second_hard_link_is_blocked(root, tmp_path):
    write(root, "a.md", "alpha\n")
    os.link(root / "a.md", tmp_path / "other")
    assert refusal(root, "a.md") == "blocked"


@FIFOS
def test_a_fifo_is_not_a_file_and_the_read_doesnt_hang(root):
    os.mkfifo(root / "pipe.md")
    assert refusal(root, "pipe.md") == "not-a-file"


def test_a_dir_is_not_a_file(root):
    (root / "topics").mkdir()
    assert refusal(root, "topics") == "not-a-file"


def test_a_binary_and_a_pdf_are_not_text(root):
    write(root, "image.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    write(root, "paper.pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n")
    write(root, "late.bin", b"a" * 8000 + b"\x00")
    assert refusal(root, "image.png") == "not-text"
    assert refusal(root, "paper.pdf") == "not-text"
    assert refusal(root, "late.bin") == "not-text"


def test_a_nul_past_the_first_8_kib_is_text(root):
    write(root, "late.txt", b"a" * 9000 + b"\x00")
    assert refusal(root, "late.txt") is None


def test_a_file_over_the_cap_is_too_large(root):
    write(root, "big.md", b"a" * (3 << 20))
    assert refusal(root, "big.md") == "too-large"
    assert refusal(root, "big.md", cap=4 << 20) is None


def test_a_missing_file_is_not_found(root):
    assert refusal(root, "missing.md") == "not-found"
    assert refusal(root, "missing/a.md") == "not-found"


def test_a_file_swapped_between_the_check_and_the_open_is_changed(root):
    write(root, "a.md", "alpha\n")
    judged = pathrules.judge_file(str(root), "a.md", cap_bytes=CAP)
    os.replace(write(root, "b.md", "beta\n"), root / "a.md")
    with pytest.raises(Refused) as caught:
        pathrules.read_judged(judged.resolved, judged.st, cap_bytes=CAP)
    assert caught.value.code == "changed"


def test_a_file_swapped_for_a_link_between_the_check_and_the_open_is_changed(root, tmp_path):
    write(root, "a.md", "alpha\n")
    judged = pathrules.judge_file(str(root), "a.md", cap_bytes=CAP)
    (tmp_path / "secret").write_text("gamma\n")
    os.remove(root / "a.md")
    os.symlink(tmp_path / "secret", root / "a.md")
    with pytest.raises(Refused) as caught:
        pathrules.read_judged(judged.resolved, judged.st, cap_bytes=CAP)
    assert caught.value.code == "changed"


def test_a_missing_root_is_no_root(tmp_path):
    root = make_root(tmp_path)
    root.rmdir()
    with pytest.raises(Refused) as caught:
        pathrules.notes_root()
    assert caught.value.code == "no-root"
    assert "missing" in pathrules.root_problem()


def test_a_dir_is_judged_by_rules_one_to_three(root, tmp_path):
    (root / "topics").mkdir()
    write(root, "a.md", "alpha\n")
    assert pathrules.judge_dir(str(root), "") == ("", str(root))
    assert pathrules.judge_dir(str(root), "topics/")[0] == "topics"
    for rel, code in (("..", "bad-path"), (".git", "blocked"), ("a.md", "not-a-folder"),
                      ("missing", "not-found")):
        with pytest.raises(Refused) as caught:
            pathrules.judge_dir(str(root), rel)
        assert caught.value.code == code, rel
    os.symlink(tmp_path, root / "up")
    with pytest.raises(Refused) as caught:
        pathrules.judge_dir(str(root), "up")
    assert caught.value.code == "outside-root"


def test_no_refusal_text_names_the_path():
    for text in pathrules.TEXTS.values():
        assert "{" not in text.replace("{cap_mib}", "")


def test_the_walk_opens_only_files_a_read_would_read(root, tmp_path):
    for rel in ("a.md", "sub/b.md", ".git/config", "sub/.env", "node_modules/x.md",
                "__pycache__/y.md", "x_history", "credentials", "a.pem", "auth.json",
                "big.md", "linked.md"):
        write(root, rel, "alpha\n")
    write(root, "big.md", b"a" * ((1 << 20) + 1))
    os.link(root / "linked.md", tmp_path / "second")
    os.symlink(root / "a.md", root / "via-link.md")
    os.symlink(root / "sub", root / "sub-link")
    make_fifo(root / "pipe.md")
    found = [w.rel for w in pathrules.walk(str(root), cap_bytes=1 << 20)]
    assert found == ["a.md", "sub/b.md"]
