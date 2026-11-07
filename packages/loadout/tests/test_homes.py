"""The page's check, through ui: a page job refuses before it writes when it would read or
write elsewhere than the user's last run in a terminal did, and `status` shows the same as
a note. loadout records nothing of its own: Moxi's record of the terminal is the one the
check reads, written here by the test kit's `terminal_record`."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import loadout_kit as kit
import pytest

from moxi import modules, paths
from moxi_loadout import gitpiece, harness_row, homes, install, ledger, pagejob, setup


@pytest.fixture
def lo(folder):
    return make(folder)


def make(folder: Path) -> Path:
    kit.configure(folder)
    kit.skill(folder / "skills", "review")
    kit.manifest(folder, "[user]\n")
    return folder


def page(monkeypatch):
    """This process as a page job runs: loadout's marker set to the page job's value."""
    monkeypatch.setenv(homes.MARKER, homes.PAGE_JOB)


def end(job: str) -> dict:
    return json.loads(pagejob.end_path(job).read_text())


def refused(job: str, folder: Path) -> str:
    """The one line job `job` refused with, having written nothing."""
    record = end(job)
    assert record["exit"] == 3 and record["outcome"] is None, record
    [line] = record["lines"]
    assert not (kit.claude() / "review").exists()
    assert "review" not in (folder / "loadout.toml").read_text()
    return line


def ui_as(monkeypatch, state: str):
    """ui as `absent`, or as `older` (present, offering interface 1 only)."""
    real = modules.load

    def load(name, *, interface=None):
        if name != "ui":
            return real(name, interface=interface)
        if state == "absent":
            return modules.Found(name="ui", state=modules.ABSENT,
                                 reason="moxi-ui is not installed")
        return modules.Found(name="ui", state=modules.BROKEN, distribution="moxi-ui",
                             version="0.1.0", interfaces=frozenset({1}),
                             reason="moxi-ui 0.1.0 offers interfaces {1}; the caller "
                                    "speaks 2")

    monkeypatch.setattr(modules, "load", load)


# -- the page job ---------------------------------------------------------------------------

def test_a_job_refuses_while_only_the_terminal_sets_moxi_home_and_runs_once_the_shell_does(
        folder, monkeypatch, tmp_path, terminal_record):
    # The user sets MOXI_HOME in the terminal's rc file only, and installed there.
    monkeypatch.delenv("MOXI_LOADOUT_HOME", raising=False)
    there = tmp_path / "m"
    monkeypatch.setenv("MOXI_HOME", str(there))
    make(folder)
    terminal_record()
    theirs = paths.Layout("loadout").state_dir()
    # The browser shell has no MOXI_HOME.
    monkeypatch.delenv("MOXI_HOME")
    page(monkeypatch)
    assert pagejob.run("j1", ["add", "skill", "review", "--user"]) == 3
    line = refused("j1", folder)
    assert (f"loadout's state folder here is {paths.Layout('loadout').state_dir()}, but the "
            f"last run in a terminal used {theirs}") in line
    assert "Moxi's state folder" in line
    assert f"Set MOXI_HOME to {there} " in line and "run the command in a terminal" in line
    # Once the shell has it, the job agrees and acts on the terminal's ledger.
    monkeypatch.setenv("MOXI_HOME", str(there))
    assert pagejob.run("j2", ["add", "skill", "review", "--user"]) == 0
    assert os.readlink(kit.claude() / "review") == str(folder / "skills" / "review")
    assert ledger.read()["placed"]


def test_a_job_refuses_when_only_moxis_own_state_folder_and_settings_file_differ(
        lo, monkeypatch, tmp_path, terminal_record):
    # loadout's own folders are pinned in both; MOXI_HOME is set in the terminal only.
    assert os.environ.get("MOXI_LOADOUT_HOME")
    terminal_record({**os.environ, "MOXI_HOME": str(tmp_path / "m")})
    monkeypatch.delenv("MOXI_HOME", raising=False)
    page(monkeypatch)
    assert pagejob.run("j1", ["add", "skill", "review", "--user"]) == 3
    line = refused("j1", lo)
    assert "Moxi's state folder here is" in line and "Moxi's settings file here is" in line
    assert "loadout's" not in line
    assert f"MOXI_HOME to {tmp_path / 'm'}" in line


def test_a_job_refuses_when_a_harness_rows_folder_differs(
        lo, monkeypatch, tmp_path, terminal_record):
    # The opencode row loadout adds is written under ~, so HOME moves it.
    assert harness_row.add(say=lambda line: None) == "added"
    terminal_record()
    monkeypatch.setenv("HOME", str(tmp_path / "other-home"))
    page(monkeypatch)
    assert pagejob.run("j1", ["add", "skill", "review", "--user"]) == 3
    line = refused("j1", lo)
    assert f"opencode's skills folder here is {tmp_path / 'other-home'}" in line
    assert "the home folder here is" in line and "HOME to " in line


@pytest.mark.parametrize("variable, label", [
    ("CLAUDE_CONFIG_DIR", "Claude Code's home"), ("CODEX_HOME", "Codex's home"),
    ("XDG_CONFIG_HOME", "OpenCode's home"), ("HOME", "the home folder")])
def test_a_job_under_another_harness_home_is_refused(
        lo, monkeypatch, tmp_path, terminal_record, variable, label):
    terminal_record()
    before = ledger.read()
    monkeypatch.setenv(variable, str(tmp_path / "elsewhere"))
    page(monkeypatch)
    assert pagejob.run("j1", ["add", "skill", "review", "--user"]) == 3
    line = refused("j1", lo)
    assert f"{label} here is {tmp_path / 'elsewhere'}" in line
    assert f"{variable} to " in line and "moxi ui stop" in line
    assert ledger.read() == before


def test_a_job_whose_path_has_no_git_is_refused_when_the_terminals_had_it(
        lo, monkeypatch, terminal_record):
    terminal_record()
    # The job's PATH holds the fake CLIs and nothing else: no git.
    fakes = os.environ["PATH"].split(os.pathsep)[0]
    assert not (Path(fakes) / "git").exists()
    monkeypatch.setenv("PATH", fakes)
    page(monkeypatch)
    assert pagejob.run("j1", ["add", "skill", "review", "--user"]) == 3
    line = refused("j1", lo)
    assert ("git isn't on PATH here, but the last run in a terminal found it, so nothing "
            "can check what git would commit") in line
    assert "PATH, with git's folder on it," in line


def test_a_job_with_git_runs_when_the_terminal_had_none(lo, monkeypatch, terminal_record):
    fakes = os.environ["PATH"].split(os.pathsep)[0]
    assert not (Path(fakes) / "git").exists()
    terminal_record({**os.environ, "PATH": fakes})
    page(monkeypatch)
    assert homes.check(["add", "skill", "review", "--user"]) is None


def test_a_job_with_no_record_is_refused_with_the_step_that_makes_one(lo, monkeypatch):
    page(monkeypatch)
    assert pagejob.run("j1", ["add", "skill", "review", "--user"]) == 3
    line = refused("j1", lo)
    assert "Moxi has no record of the environment your terminal uses" in line
    assert "Type moxi modules in a terminal of your own" in line


def test_a_job_reading_a_project_by_key_compares_projects_folders(
        lo, monkeypatch, tmp_path, terminal_record):
    assert modules.available("projects") and os.environ.get("MOXI_PROJECTS_HOME")
    terminal_record()
    monkeypatch.setenv("MOXI_PROJECTS_HOME", str(tmp_path / "other-projects"))
    page(monkeypatch)
    # Nothing in the act reads projects: its folders are not compared.
    assert homes.check(["add", "skill", "review", "--user"]) is None
    # A section named by key is read through projects.
    line = homes.check(["add", "skill", "review", "--project", "app"])
    assert "projects' data folder here is" in line and "MOXI_PROJECTS_HOME" in line
    kit.manifest(lo, "[user]\n[project.app]\nskills = []\n")
    assert "projects' state folder here is" in homes.check(["install"])


def test_without_ui_at_interface_2_a_job_refuses_with_the_reason(
        lo, monkeypatch, terminal_record):
    terminal_record()
    page(monkeypatch)
    ui_as(monkeypatch, "older")
    assert pagejob.run("j1", ["add", "skill", "review", "--user"]) == 3
    line = refused("j1", lo)
    assert "the browser UI's check can't be loaded" in line
    assert "moxi-ui 0.1.0 offers interfaces {1}" in line and line.endswith("update moxi-ui")
    ui_as(monkeypatch, "absent")
    assert pagejob.run("j2", ["install"]) == 3
    assert end("j2")["lines"][0].endswith("moxi install ui")


# -- notes ----------------------------------------------------------------------------------

def test_status_notes_a_difference_without_a_row_or_a_code(
        lo, monkeypatch, tmp_path, terminal_record):
    terminal_record()
    before = install.status()
    assert before.code == 0 and not [n for n in before.notes if "terminal" in n]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "other-claude"))
    after = install.status()
    assert after.code == 0 and len(after.rows) == len(before.rows)
    [note] = [n for n in after.notes if "terminal" in n]
    assert "Claude Code's home here is" in note and "CLAUDE_CONFIG_DIR" in note
    # A terminal is told what the page does, not given the page's own advice.
    assert "The browser page refuses to write" in note and "moxi ui stop" in note
    assert "run the command in a terminal" not in note
    with homes.as_page():
        [note] = [n for n in install.status().notes if "terminal" in n]
    assert "run the command in a terminal" in note


@pytest.mark.parametrize("state", ["absent", "older"])
def test_without_ui_at_interface_2_a_terminal_status_has_no_note(
        lo, monkeypatch, tmp_path, terminal_record, state):
    terminal_record()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "other-claude"))
    ui_as(monkeypatch, state)
    assert not [n for n in install.status().notes if "terminal" in n or "UI" in n]
    # The page's reads show why they can't check.
    with homes.as_page():
        [note] = [n for n in install.status().notes if "terminal" in n]
    assert "the browser UI's check can't be loaded" in note


def test_the_missing_cli_note_names_the_lasting_path_fix_only_with_ui(lo, monkeypatch):
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: None if n == "codex"
                        else real(n, *a, **k))
    kit.manifest(lo, '[user]\nmcp = ["docs"]\n[mcp.docs]\ncommand = "uvx"\n')

    def reason() -> str:
        [row] = [r for r in install.status().rows if (r.kind, r.harness) == ("mcp", "codex")]
        assert row.state == "blocked" and not row.replaceable
        return row.reason

    said = reason()
    assert "no codex on PATH" in said and "put codex's folder on PATH" in said
    assert "the browser page does the same" in said
    assert said.endswith("then run systemctl --user daemon-reload and moxi ui stop") \
        or "log out and back in" in said
    with homes.as_page():
        said = reason()
    assert "set PATH, with codex's folder on it," in said
    assert said.endswith("; or run the command in a terminal")
    ui_as(monkeypatch, "absent")
    assert reason().endswith("not written; put codex's folder on PATH")


# -- loadout records nothing ----------------------------------------------------------------

def test_setup_and_install_leave_the_ledger_without_homes_or_git(folder, monkeypatch):
    monkeypatch.delenv(homes.MARKER, raising=False)
    assert setup.setup(say=lambda line: None) == 0
    assert not {"homes", "git"} & set(ledger.read())
    make(folder)
    assert install.install().code == 0
    assert not {"homes", "git"} & set(ledger.read())


def test_the_next_ledger_write_drops_an_older_releases_homes_and_git(lo):
    from moxi_loadout import stores

    stores.state_dir(create=True)
    older = {"source": None, "placed": {}, "entries": [],
             "homes": {"home": "/elsewhere"}, "git": True}
    stores.ledger_path().write_text(json.dumps(older))
    assert install.install().code == 0
    assert not {"homes", "git"} & set(json.loads(stores.ledger_path().read_text()))


def test_the_git_hooks_child_install_carries_no_marker():
    assert homes.MARKER not in gitpiece.clean_env()


# -- the places -----------------------------------------------------------------------------

def test_the_places_follow_the_mapping_not_this_process(lo, monkeypatch, tmp_path):
    from moxi import terminal

    places = homes.places(reads=homes.READS)
    honest = dict(os.environ)
    expected = places(honest)
    assert {"loadout's data folder", "loadout's state folder", "loadout's config file",
            "Moxi's state folder", "Moxi's settings file", "Claude Code's home",
            "Codex's home", "OpenCode's home", "claude's skills folder",
            "codex's agents folder", "projects' data folder", "repo's config folder",
            "the home folder"} <= set(expected)
    poison = tmp_path / "poison"
    for name in [n for n in honest if terminal.in_set(n) and n != "PATH"]:
        monkeypatch.setenv(name, str(poison / name))
    for name in ("MOXI_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.setenv(name, str(poison / name))
    got = places(honest)
    assert got == expected
    assert not [p for p in got.values() if str(poison) in os.fspath(p)]
