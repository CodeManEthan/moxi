"""The harness homes, the home folder and whether git was found: the user's own runs
record them, loadout's own callers leave them, and status notes a difference without a
row or a code, worded for a terminal or for the page."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

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


def no_ui(monkeypatch):
    """The browser UI as if it weren't installed."""
    from moxi import modules

    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "ui" and real(name))


def hide_git(monkeypatch):
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: None if n == "git"
                        else real(n, *a, **k))


def test_a_users_install_records_every_home_and_whether_git_was_found(lo, monkeypatch):
    assert recorded_homes(monkeypatch) == {
        "claude": os.environ["CLAUDE_CONFIG_DIR"], "codex": os.environ["CODEX_HOME"],
        "opencode": os.path.join(os.environ["XDG_CONFIG_HOME"], "opencode"),
        "home": os.path.abspath(Path.home())}
    assert ledger.read()[homes.GIT] is (shutil.which("git") is not None)


def test_setup_records_again_when_git_comes_or_goes(lo, monkeypatch):
    recorded_homes(monkeypatch)
    assert setup.record_homes() is False
    hide_git(monkeypatch)
    assert setup.record_homes() is True
    assert ledger.read()[homes.GIT] is False


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
    # A terminal is told what the page does, not given the page's own advice.
    assert "The browser page refuses to write" in note and "moxi ui stop" in note
    assert "run the command in a terminal" not in note


def test_status_notes_another_home_folder_and_a_missing_git(lo, monkeypatch, tmp_path):
    recorded_homes(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "other-home"))
    hide_git(monkeypatch)
    [note] = [n for n in install.status().notes if "home" in n]
    assert "the home folder here is" in note and str(tmp_path / "other-home") in note
    assert "git isn't on PATH here, but the last run in a terminal found it" in note
    assert "HOME and PATH" in note


def test_found_git_where_the_record_has_none_is_no_difference(lo, monkeypatch):
    real = shutil.which
    hide_git(monkeypatch)
    recorded_homes(monkeypatch)
    monkeypatch.setattr(shutil, "which", real)
    assert homes.check(ledger.read()) is None


def test_without_the_browser_ui_the_terminal_note_leaves_out_its_stop(
        lo, monkeypatch, tmp_path):
    recorded_homes(monkeypatch)
    no_ui(monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "other-claude"))
    [note] = [n for n in install.status().notes if "home" in n]
    assert "CLAUDE_CONFIG_DIR" in note and "moxi ui stop" not in note
    # On the page the stop is always said: the page runs in the shell.
    page = homes.check(ledger.read(), page=True)
    assert page.endswith("then run moxi ui stop, or run the command in a terminal")


def test_a_ledger_older_than_this_record_reads_as_no_record(lo, monkeypatch):
    recorded_homes(monkeypatch)
    data = ledger.read()
    del data[homes.GIT]
    assert "has no record of the harness homes" in homes.check(data)
    data = ledger.read()
    del data[homes.KEY][homes.HOME]
    assert "has no record of the harness homes" in homes.check(data)


def test_the_missing_cli_note_names_the_lasting_path_fix(lo, monkeypatch):
    import shutil

    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n: None if n == "codex" else real(n))
    kit.manifest(lo, '[user]\nmcp = ["docs"]\n[mcp.docs]\ncommand = "uvx"\n')
    [row] = [r for r in install.status().rows if (r.kind, r.harness) == ("mcp", "codex")]
    assert row.state == "blocked"
    assert "no codex on PATH" in row.reason and "put codex's folder on PATH" in row.reason
    assert "the browser page does the same" in row.reason and "moxi ui stop" in row.reason
    with homes.as_page():
        [row] = [r for r in install.status().rows
                 if (r.kind, r.harness) == ("mcp", "codex")]
    assert "set PATH, with codex's folder on it," in row.reason
    assert row.reason.endswith("moxi ui stop, or run the command in a terminal")
    no_ui(monkeypatch)
    [row] = [r for r in install.status().rows if (r.kind, r.harness) == ("mcp", "codex")]
    assert "no codex on PATH" in row.reason and "moxi ui stop" not in row.reason
