"""The harness homes: the user's own runs record them, loadout's own callers leave them,
and status notes a difference without a row or a code."""

from __future__ import annotations

import os

import loadout_kit as kit
import pytest

from moxi_loadout import cli, gitpiece, homes, install, ledger, setup


@pytest.fixture
def lo(folder):
    kit.configure(folder)
    kit.skill(folder / "skills", "review")
    kit.manifest(folder, "[user]\n")
    return folder


def recorded_homes(monkeypatch) -> dict:
    """Run the user's own install once, which records the homes; return them."""
    monkeypatch.delenv(homes.MARKER, raising=False)
    assert install.install().code == 0
    return ledger.read()[homes.KEY]


def test_a_users_install_records_every_harness_home(lo, monkeypatch):
    assert recorded_homes(monkeypatch) == {
        "claude": os.environ["CLAUDE_CONFIG_DIR"], "codex": os.environ["CODEX_HOME"],
        "opencode": os.path.join(os.environ["XDG_CONFIG_HOME"], "opencode")}


def test_a_users_add_records_the_homes(lo):
    assert homes.KEY not in ledger.read()
    assert cli.main(["add", "skill", "review", "--json"]) == 0
    assert ledger.read()[homes.KEY] == homes.resolved()


def test_setup_on_a_fresh_state_records_the_homes(folder):
    assert setup.setup(say=lambda line: None) == 0
    assert ledger.read()[homes.KEY] == homes.resolved()


def test_a_marked_install_leaves_the_homes_as_they_are(lo, monkeypatch, tmp_path):
    first = recorded_homes(monkeypatch)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "other-codex"))
    monkeypatch.setenv(homes.MARKER, "git-hook")
    assert install.install().code == 0
    assert setup.record_homes() is False
    assert ledger.read()[homes.KEY] == first


def test_the_git_hooks_child_install_carries_the_marker():
    assert gitpiece.clean_env()[homes.MARKER] == "git-hook"


def test_status_notes_other_homes_without_a_row_or_a_code(lo, monkeypatch, tmp_path):
    recorded_homes(monkeypatch)
    before = install.status()
    assert before.code == 0 and not [n for n in before.notes if "home" in n]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "other-claude"))
    after = install.status()
    assert after.code == 0 and len(after.rows) == len(before.rows)
    [note] = [n for n in after.notes if "home" in n]
    assert "Claude Code's home here is" in note and "CLAUDE_CONFIG_DIR" in note
    assert "moxi ui stop" in note


def test_the_missing_cli_note_names_the_lasting_path_fix(lo, monkeypatch):
    import shutil

    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n: None if n == "codex" else real(n))
    kit.manifest(lo, '[user]\nmcp = ["docs"]\n[mcp.docs]\ncommand = "uvx"\n')
    [row] = [r for r in install.status().rows if (r.kind, r.harness) == ("mcp", "codex")]
    assert row.state == "blocked"
    assert "no codex on PATH" in row.reason and "PATH" in row.reason
    assert "moxi ui stop" in row.reason
