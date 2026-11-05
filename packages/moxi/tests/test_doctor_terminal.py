"""The doctor's terminal record row: that a command typed here records, or what stops it.

The account's folder is the test's own (`_account_home`, or the shell's local folder on
Windows, patched), so a test without the kit's `MOXI_ANCHOR_HOME` still never reaches the
real account's record.
"""

from __future__ import annotations

import os
import pytest

from moxi import _win, paths, terminal
from moxi.doctor import _run
from moxi.terminal import Evidence, Undecided, _lines, _record, _walk


@pytest.fixture
def acct(tmp_path, monkeypatch):
    """The account's folder under the test's tmp, `MOXI_ANCHOR_HOME` unset, and a walk
    that finds nothing; returns the record's path."""
    folder = tmp_path / "acct"
    folder.mkdir()
    monkeypatch.delenv(paths.ANCHOR_HOME)
    if os.name == "nt":
        real = _win.known_folder
        monkeypatch.setattr(_win, "known_folder",
                            lambda name: str(folder) if name == "local" else real(name))
    else:
        monkeypatch.setattr(paths, "_account_home", lambda: str(folder))
    monkeypatch.setattr(_walk, "walk", lambda view=None, run=None: None)
    monkeypatch.setattr(terminal, "last", None)
    return paths.terminal_record()


def row(**kw):
    kw.setdefault("keyboard", True)
    return _run.terminal_row(**kw)


def test_the_kits_anchor_variable_is_named():
    got = row()
    assert got.state == "warn" and got.module == "moxi" and got.name == "terminal record"
    assert got.detail == (f"MOXI_ANCHOR_HOME is set, which only test kits set, so the record "
                          f"goes to {paths.terminal_record()}")
    assert got.fix == "take it out of this terminal's environment"


def test_a_record_of_this_environment_is_ok(acct):
    _record.write(None)
    got = row()
    assert got.state == "ok"
    assert got.detail.startswith(f"the record at {acct} holds this terminal's environment")


def test_no_keyboard(acct):
    got = row(keyboard=False)
    assert got.state == "warn"
    assert (got.detail, got.fix) == _lines.NO_KEYBOARD
    assert got.detail.startswith("moxi sees no terminal on its input here")


@pytest.mark.parametrize("found, detail, fix", [
    (Evidence("E1", 4121, "moxi-slots-server", "moxi-slots-server", "slots"),
     "this terminal was started by Moxi (moxi-slots-server, pid 4121), so nothing typed in "
     "it records", "type the command in a terminal of your own"),
    (Evidence("E1", 77, "python3", "-m moxi_loadout.pagejob", "loadout"),
     "this terminal was started by Moxi (python3 -m moxi_loadout.pagejob, pid 77), so "
     "nothing typed in it records", "type the command in a terminal of your own"),
    (Evidence("E3", 4121, "tmux", "moxi-slots-server.service", "slots"),
     "this terminal was started by Moxi (slots' tmux under moxi-slots-server.service, pid "
     "4121), so nothing typed in it records", "type the command in a terminal of your own"),
    (Evidence("E4", 900, "bash", "moxi-launcher-job"),
     "this terminal was started by Moxi (bash, started by moxi-launcher-job, pid 900), so "
     "nothing typed in it records", "type the command in a terminal of your own"),
    (Evidence("E2", 312, "claude", "claude"),
     "this command runs under Claude Code (pid 312), an agent, and nothing an agent runs "
     "records", "type it in a terminal of your own"),
    # A helper the harness names after itself is named as its harness.
    (Evidence("E2", 1, "codex-linux-sandbox", "codex"),
     "this command runs under Codex (pid 1), an agent, and nothing an agent runs records",
     "type it in a terminal of your own"),
    (Evidence("E2", 1, "3", "/proc/self/fd/3"),
     "this command runs under a program started from a file descriptor (pid 1), as an "
     "agent's tool sandbox runs its commands, and nothing an agent runs records",
     "type it in a terminal of your own"),
    (Evidence("E2", 100, "moxi", "CLAUDECODE"),
     "this command runs in a Claude Code tool command (its environment carries "
     "CLAUDECODE), and nothing an agent runs records", "type it in a terminal of your own"),
])
def test_each_walk_result_names_what_it_found_and_one_step(acct, monkeypatch, found, detail,
                                                           fix):
    _record.write(None)
    monkeypatch.setattr(_walk, "walk", lambda view=None, run=None: found)
    got = row()
    assert (got.state, got.detail, got.fix) == ("warn", detail, fix)


@pytest.mark.parametrize("found", [
    Undecided("N1", "ended", 55),
    Undecided("N1", "unread", 55, "zsh", "Input/output error"),
    Undecided("N1", "deep", 55, "sh"),
    Undecided("N1", "loop", 55),
    Undecided("N2", "tmux", 4121, "tmux"),
])
def test_an_undecided_walk_gives_the_record_steps_own_words(acct, monkeypatch, found):
    monkeypatch.setattr(_walk, "walk", lambda view=None, run=None: found)
    got = row()
    words = _record.row(_record.Outcome(_record.UNDECIDED, acct, found))
    assert got.state == "warn"
    assert f"{got.detail}; {got.fix}" == words
    assert got.fix.startswith(("run the command again", "type the command in a terminal"))


def test_the_row_hands_its_view_and_run_to_the_walk(acct, monkeypatch):
    seen = []
    monkeypatch.setattr(_walk, "walk", lambda view=None, run=None: seen.append((view, run)))
    row(view="v", run="r")
    assert seen == [("v", "r")]


@pytest.mark.skipif(os.name == "nt", reason="the user database places the anchor on POSIX")
def test_no_place_for_the_record(acct, monkeypatch):
    monkeypatch.setattr(paths, "_account_home", lambda: None)
    got = row()
    uid = os.getuid()
    assert got.state == "warn"
    assert got.detail == ("Moxi can't find where it keeps the record: the account has no "
                          f"entry in the user database (uid {uid})")
    assert got.fix == f"give uid {uid} an entry in the user database"


def test_an_unreadable_record_fails(acct, monkeypatch):
    _record.write(None)
    monkeypatch.setattr(_record, "read", lambda path=None: _record.Unreadable(
        acct, PermissionError(13, "Permission denied")))
    got = row()
    assert got.state == "fail"
    assert got.detail == f"the record at {acct} can't be read (Permission denied)"
    assert got.fix == "fix its permissions or remove it, then type moxi modules here"


def test_a_write_the_step_tried_and_failed_is_named(acct, monkeypatch):
    monkeypatch.setattr(terminal, "last", _record.Outcome(
        _record.FAILED, acct, error=OSError(28, "No space left on device")))
    got = row()
    assert got.state == "fail"
    assert got.detail == f"the record at {acct} can't be written (No space left on device)"
    assert got.fix == f"make {acct.parent} writable by you"


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="a POSIX folder's mode, which root ignores")
def test_a_folder_that_cant_be_written_fails_with_no_step_run(acct):
    folder = acct.parent.parent.parent.parent  # the account folder's .local
    folder.mkdir(parents=True)
    folder.chmod(0o500)
    try:
        got = row()
    finally:
        folder.chmod(0o700)
    assert got.state == "fail"
    assert got.detail.startswith(f"the record at {acct} can't be written ({folder}: ")
    assert got.fix == f"make {acct.parent} writable by you"


def test_a_differing_record_with_no_step_names_the_command(acct, monkeypatch):
    _record.write({**os.environ, "MOXI_HOME": "/elsewhere"})
    got = row()
    assert got.state == "warn"
    assert "doesn't hold this terminal's environment" in got.detail
    assert got.fix == "type moxi modules in this terminal"


def test_the_doctor_run_has_the_row_on_module_moxi(acct):
    rows = {(r.module, r.name): r for r in _run.run(["moxi"])}
    assert ("moxi", "terminal record") in rows
    assert ("moxi", "terminal record") not in {
        (r.module, r.name) for r in _run.run(["projects"])}
