"""The doctor's terminal record row: that a command typed here records, or what stops it.

The account's folder is the test's own (`_account_home`, or the shell's local folder on
Windows, patched), so a test without the kit's `MOXI_ANCHOR_HOME` still never reaches the
real account's record.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from moxi import _win, cli, paths, terminal
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


#: The row in a fresh interpreter with no module installed: every `moxi.modules` entry
#: point hidden, and every module's package refused (and noted) on import. Its walk runs
#: over a view where the run's parent is a tmux server, so it asks whether slots is
#: there. It prints the row before and after a record is written, and what was imported.
CORE_ONLY = textwrap.dedent("""\
    import importlib.abc, importlib.metadata, json, os, sys

    asked = []

    class Refused(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.partition(".")[0].startswith("moxi_"):
                asked.append(name)
                raise ImportError(f"no module named {name!r}")
            return None

    sys.meta_path.insert(0, Refused())
    every = importlib.metadata.entry_points

    def entry_points(**params):
        if params.get("group") == "moxi.modules":
            return importlib.metadata.EntryPoints(())
        return every(**params)

    importlib.metadata.entry_points = entry_points

    folder = sys.argv[1]
    os.environ.pop("MOXI_ANCHOR_HOME", None)
    from moxi import _win, modules, paths
    if os.name == "nt":
        real = _win.known_folder
        _win.known_folder = lambda name: folder if name == "local" else real(name)
    else:
        paths._account_home = lambda: folder
    from moxi.doctor import _run
    from moxi.terminal import _record, _view, _walk

    class UnderTmux(_view._View):
        def read(self, pid):
            if pid == os.getpid():
                return _view.Proc(pid, 2, 2.0, ("moxi", "doctor"), {})
            if pid == 2:
                return _view.Proc(2, 0, 1.0, ("tmux",), {})
            raise _view.Gone(pid)

    run = _walk.Run(os.getpid(), sys.executable, "moxi", None)
    rows = [_run.terminal_row(keyboard=True, view=UnderTmux(), run=run)._asdict()]
    _record.write(None)
    rows.append(_run.terminal_row(keyboard=True, view=UnderTmux(), run=run)._asdict())
    print(json.dumps({"installed": [e.name for e in modules.installed()], "rows": rows,
                      "asked": asked,
                      "loaded": sorted(n for n in sys.modules if n.startswith("moxi_"))}))
    """)


def test_the_row_runs_with_no_module_installed(tmp_path):
    """No module installed, ui and slots included: the row imports none, and a tmux
    server above the run is passed, since no slots server can run there."""
    folder = tmp_path / "acct"
    folder.mkdir()
    done = subprocess.run([sys.executable, "-c", CORE_ONLY, str(folder)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    assert got["installed"] == [] and got["asked"] == [] and got["loaded"] == [], got
    unrecorded, recorded = got["rows"]
    assert (unrecorded["module"], unrecorded["name"]) == ("moxi", "terminal record")
    assert unrecorded["state"] == "warn", unrecorded
    assert "doesn't hold this terminal's environment" in unrecorded["detail"], unrecorded
    assert unrecorded["fix"] == "type moxi modules in this terminal"
    assert recorded["state"] == "ok", recorded
    assert recorded["detail"].startswith("the record at "), recorded


def test_the_doctor_run_has_the_row_on_module_moxi(acct):
    rows = {(r.module, r.name): r for r in _run.run(["moxi"])}
    assert ("moxi", "terminal record") in rows
    assert ("moxi", "terminal record") not in {
        (r.module, r.name) for r in _run.run(["projects"])}


# -- what ui's check reads of the core ------------------------------------------------------

@pytest.mark.parametrize("command", [terminal.RECORD_COMMAND, terminal.DOCTOR_COMMAND])
def test_each_command_a_line_names_is_a_core_verb(command):
    program, verb = command.split()
    assert program == "moxi" and verb in cli._VERBS
    assert verb not in cli._NO_STRAY_CHECK


@pytest.mark.parametrize("fold", [False, True])
def test_anchor_set_answers_as_the_row_reads_it(acct, monkeypatch, fold):
    assert not terminal.anchor_set({}, fold=fold)
    assert not terminal.anchor_set({paths.ANCHOR_HOME: ""}, fold=fold)
    assert terminal.anchor_set({paths.ANCHOR_HOME: "/t"}, fold=fold)
    assert terminal.anchor_set({paths.ANCHOR_HOME.lower(): "/t"}, fold=fold) is fold
    assert not terminal.anchor_set() and row().name == "terminal record"
    assert row().detail.startswith("the record at")
    monkeypatch.setenv(paths.ANCHOR_HOME, str(acct.parent))
    assert terminal.anchor_set()
    assert row().detail.startswith("MOXI_ANCHOR_HOME is set")


def test_the_core_offers_no_page_check():
    # The check a page makes before it writes is ui's (moxi_ui.api); the core keeps the
    # record, the resolvers and this row.
    import importlib.util

    for name in ("check", "where", "Where"):
        assert not hasattr(terminal, name)
    assert importlib.util.find_spec("moxi.terminal._check") is None
