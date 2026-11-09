"""`moxi notes scan`: what a snapshot would leave out now, read-only, in text and JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import moxi.paths
from moxi_notes import filter as notes_filter
from moxi_notes.scan import CANT, JSON_SHAPE, LINK

from gate_kit import notes_repo, private_names, sh, snapshot, token, tree, write
from notes_kit import configure, git_env, home, moxi_cmd

posix = pytest.mark.skipif(sys.platform == "win32",
                           reason="the clean filter's wrapper through POSIX sh; the "
                                  "Windows run is owed")


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


def scan_json() -> tuple[int, dict, str]:
    code, out, err = moxi_cmd("notes", "scan", "--json")
    return code, json.loads(out), err


def state(root: Path) -> tuple[str, str, str]:
    """HEAD, the index's tree and the status, to show the scan changed nothing."""
    return (sh(root, "rev-parse", "HEAD").stdout, sh(root, "write-tree").stdout,
            sh(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout)


def a_mixed_tree(root: Path) -> None:
    private_names("quillon")
    write(root, "plan.md", "intro\nkey = " + token() + "\nmore\n")
    write(root, "paths.md", "see /home/quillon/code/alpha\n")
    write(root, "clean.md", "nothing here\n")
    write(root, "draft/untracked.md", "a note\n")


def test_a_clean_tree_exits_0_with_nothing_left_out():
    root = notes_repo()
    write(root, "clean.md", "nothing here\n")
    code, found, _ = scan_json()
    assert code == 0
    assert found == {"root": str(root), "action": "leave-out", "judged": 2,
                     "left_out": [], "redacted": [], "authors": 0,
                     "allowlist_problem": None, "error": None}
    code, out, _ = moxi_cmd("notes", "scan")
    assert code == 0 and "judged 2 files" in out and "0 would be left out" in out


def test_leave_out_lists_each_file_with_its_rules_and_lines_and_exits_1():
    root = notes_repo()
    a_mixed_tree(root)
    code, found, _ = scan_json()
    assert code == 1
    assert found["judged"] == 5 and found["redacted"] == []
    assert found["left_out"] == [
        {"path": "paths.md", "reason": "private-names/private-name line 1",
         "findings": [{"rule": "private-names/private-name", "line": 1, "note": ""}]},
        {"path": "plan.md", "reason": "credentials/github-pat line 2",
         "findings": [{"rule": "credentials/github-pat", "line": 2, "note": ""}]},
    ]
    code, out, _ = moxi_cmd("notes", "scan")
    assert code == 1
    assert "2 would be left out" in out
    assert "  paths.md:1 private-names/private-name\n" in out
    assert "  plan.md:2 credentials/github-pat\n" in out
    assert token() not in out and "quillon" not in out


def test_the_scan_changes_nothing():
    root = notes_repo()
    a_mixed_tree(root)
    sh(root, "add", "plan.md")
    before = state(root)
    assert moxi_cmd("notes", "scan")[0] == 1
    assert scan_json()[0] == 1
    assert state(root) == before


def test_under_leave_out_the_scan_names_what_a_snapshot_leaves_out():
    root = notes_repo()
    a_mixed_tree(root)
    code, found, _ = scan_json()
    snapshot(root)
    held = {one["path"] for one in found["left_out"]}
    assert held == {"plan.md", "paths.md"}
    assert {"clean.md", "draft/untracked.md"} <= tree(root)
    assert not held & tree(root)
    assert scan_json()[0] == 1


@posix
def test_under_redact_a_redacted_file_is_not_left_out_and_one_that_cant_be_is():
    root = notes_repo()
    configure(action="redact")
    notes_filter.install(root)
    write(root, "plan.md", "intro\nkey = " + token() + "\nmore\n")
    write(root, "whole.md", token(2))
    write(root, "clean.md", "nothing here\n")
    code, found, _ = scan_json()
    assert code == 1
    assert [one["path"] for one in found["redacted"]] == ["plan.md"]
    assert found["left_out"] == [
        {"path": "whole.md", "reason": f"credentials/github-pat line 1; {CANT}",
         "findings": [{"rule": "credentials/github-pat", "line": 1, "note": ""}]}]
    code, out, _ = moxi_cmd("notes", "scan")
    assert "  whole.md:1 credentials/github-pat (can't be redacted)" in out
    assert "1 would be committed with the hits blanked" in out
    snapshot(root)
    assert "plan.md" in tree(root) and "whole.md" not in tree(root)
    assert "[REDACTED:github-pat]" in sh(root, "show", "HEAD:plan.md").stdout


@posix
def test_under_redact_a_link_whose_target_holds_a_hit_is_left_out_as_the_snapshot_does():
    root = notes_repo()
    configure(action="redact")
    notes_filter.install(root)
    (root / "link.md").symlink_to("key-" + token())
    write(root, "clean.md", "nothing here\n")
    code, found, _ = scan_json()
    assert code == 1 and found["redacted"] == []
    assert [(one["path"], one["reason"].endswith(LINK)) for one in found["left_out"]] == [
        ("link.md", True)]
    code, out, _ = moxi_cmd("notes", "scan")
    assert "(a link, which git commits unfiltered)" in out
    snapshot(root)
    assert "clean.md" in tree(root) and "link.md" not in tree(root)


def test_under_redact_without_the_filter_every_file_with_a_finding_is_left_out():
    root = notes_repo()
    configure(action="redact")
    write(root, "plan.md", "intro\nkey = " + token() + "\nmore\n")
    code, found, _ = scan_json()
    assert code == 1 and found["redacted"] == []
    assert found["left_out"][0]["path"] == "plan.md"
    assert "the clean filter is not in place" in found["left_out"][0]["reason"]


def test_a_file_over_max_mb_is_left_out_with_no_line():
    root = notes_repo()
    configure(max_mib=1)
    write(root, "big.md", "x" * (2 * 1024 * 1024))
    code, found, _ = scan_json()
    assert code == 1
    [one] = found["left_out"]
    assert one["path"] == "big.md"
    assert one["findings"][0]["rule"] == "notes/too-large"
    assert one["findings"][0]["line"] is None and "max_mib" in one["findings"][0]["note"]


def test_a_malformed_allowlist_is_named_and_every_file_with_a_hit_is_left_out():
    root = notes_repo()
    write(root, "plan.md", "intro\nkey = " + token() + "\n")
    (root / ".git" / "secrets-allow-private").write_text("not a line\n", encoding="utf-8")
    code, found, _ = scan_json()
    assert code == 1 and found["allowlist_problem"]
    assert "judged with no allowances" in found["left_out"][0]["reason"]


def test_a_root_that_is_no_repo_exits_1_with_the_error():
    (home() / "notes").mkdir()
    code, found, err = scan_json()
    assert code == 1
    assert found["error"] and "moxi notes setup" in found["error"]
    assert found["left_out"] == [] and found["judged"] == 0
    assert "nothing could be judged" in err


def test_a_bad_setting_exits_2():
    path = Path(moxi.paths.Layout("notes").config_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("action = 'shred'\n", encoding="utf-8")
    code, out, err = moxi_cmd("notes", "scan", "--json")
    assert code == 2 and out == "" and "action" in err


def test_the_help_states_the_json_shape():
    code, out, _ = moxi_cmd("notes", "scan", "--help")
    assert code == 0
    assert JSON_SHAPE.splitlines()[1] in out and JSON_SHAPE.splitlines()[-1] in out
