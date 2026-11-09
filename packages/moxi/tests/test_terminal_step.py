"""The `moxi` command's record step: who records the terminal's environment, and what a run
prints when it can't.

The walk is handed in (patched) here; what it finds over each process tree is
test_terminal_walk.py's. The keyboard is a real pseudo-terminal on POSIX and patched
elsewhere.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest

from moxi import cli, files, paths, terminal
from moxi.terminal import _record, _walk
from moxi.terminal._walk import Evidence, Undecided

WINDOWS = sys.platform == "win32"
posix_only = pytest.mark.skipif(WINDOWS, reason="POSIX ttys and modes")


class Walks:
    """A stand-in for the walk: returns `found` and counts its calls."""

    def __init__(self, found=None):
        self.found = found
        self.calls = 0

    def __call__(self, view=None, run=None):
        self.calls += 1
        if isinstance(self.found, BaseException):
            raise self.found
        return self.found


@pytest.fixture
def walks(monkeypatch) -> Walks:
    walker = Walks()
    monkeypatch.setattr(_walk, "walk", walker)
    return walker


@pytest.fixture
def typed(monkeypatch):
    """A keyboard on the input."""
    monkeypatch.setattr(_record, "keyboard", lambda stdin=None: True)


@pytest.fixture
def console(monkeypatch, capsys):
    """Run `moxi <argv>` as the console script does, in process; returns (code, stderr).

    The console script tells strays first, and every other `moxi` on PATH is one place it
    looks (`moxi.strays`). The test runner's PATH can hold another environment's `moxi`
    (a workspace's own, beside the check's environment), whose modules would be told as
    strays on stderr; PATH here holds no `moxi`, so stderr is the record step's alone."""
    script = "moxi.exe" if WINDOWS else "moxi"
    monkeypatch.setenv("PATH", os.pathsep.join(
        entry for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not os.path.isfile(os.path.join(entry, script))))

    def run(*argv: str):
        monkeypatch.setattr(sys, "argv", ["moxi", *argv])
        monkeypatch.setattr(terminal, "last", None)
        capsys.readouterr()
        code = cli.main()
        return code, capsys.readouterr().err

    return run


def recorded() -> dict | None:
    try:
        return json.loads(paths.terminal_record().read_text("utf-8"))
    except FileNotFoundError:
        return None


# -- who records -------------------------------------------------------------------------------

def test_a_typed_command_with_an_empty_walk_writes_the_record(fence, walks, typed, console):
    code, err = console("modules")
    assert code == 0 and "wasn't recorded" not in err
    body = recorded()
    assert body["format"] == 1 and body["moxi"] == terminal.version()
    assert body["env"] == terminal.of()
    assert terminal.last.kind == terminal.WRITTEN and walks.calls == 1
    assert terminal.last.path == paths.terminal_record()


@posix_only
def test_the_record_is_the_accounts_alone_to_read(fence, walks, typed, console):
    console("modules")
    assert stat.S_IMODE(paths.terminal_record().stat().st_mode) == 0o600


@pytest.mark.parametrize("argv", [
    ("run", "x"), ("help",), ("version",), ("-h",), ("--help",), ("--version",), ()])
def test_the_words_the_stray_check_skips_record_nothing(fence, walks, typed, console, argv):
    console(*argv)
    assert recorded() is None and walks.calls == 0 and terminal.last is None


def test_an_in_process_caller_records_nothing(fence, walks, typed, monkeypatch):
    monkeypatch.setattr(terminal, "last", None)
    assert cli.main(["modules"]) == 0
    assert recorded() is None and walks.calls == 0 and terminal.last is None


def test_no_keyboard_records_nothing_and_walks_nothing(fence, walks, console, monkeypatch):
    monkeypatch.setattr(_record, "keyboard", lambda stdin=None: False)
    code, err = console("modules")
    assert code == 0 and err == ""
    assert recorded() is None and walks.calls == 0
    assert terminal.last.kind == terminal.NO_KEYBOARD


@pytest.mark.parametrize("found", [
    Evidence("E1", 4121, "moxi-slots-server", "moxi-slots-server", "slots"),
    Evidence("E2", 3001, "claude", "claude"),
    Evidence("E3", 4121, "tmux", "moxi-slots-server.service", "slots"),
    Evidence("E4", 100, "python", "moxi"),
])
def test_evidence_records_nothing_and_prints_nothing(fence, walks, typed, console, found):
    walks.found = found
    code, err = console("modules")
    assert code == 0 and err == "" and recorded() is None
    assert terminal.last == terminal.Outcome(terminal.FOUND, paths.terminal_record(), found)


@pytest.mark.parametrize(("found", "words"), [
    (Undecided("N1", "ended", 4000, "bash"),
     "a process above this one ended while moxi read it; run the command again"),
    (Undecided("N1", "unread", 4000, "sshd", "Permission denied"),
     "moxi can't read sshd (pid 4000) above this terminal (Permission denied), so it "
     "can't tell whether Moxi started this terminal; type the command in a terminal that "
     "sshd didn't start"),
    (Undecided("N1", "deep", 4000),
     "moxi can't read a process (pid 4000) above this terminal (more than 64 processes "
     "deep), so it can't tell whether Moxi started this terminal; type the command in a "
     "terminal that that process didn't start"),
    (Undecided("N1", "loop", 4000),
     "moxi can't read a process (pid 4000) above this terminal (its parents loop), so it "
     "can't tell whether Moxi started this terminal; type the command in a terminal that "
     "that process didn't start"),
    (Undecided("N2", "tmux", 4121, "tmux"),
     "this terminal is inside a tmux server (pid 4121) that moxi can't tell from slots' "
     "own, because no service manager runs slots' server here; type the command in a "
     "terminal outside tmux"),
])
def test_an_undecided_walk_prints_one_note_with_the_doctor_rows_words(
        fence, walks, typed, console, found, words):
    walks.found = found
    code, err = console("modules")
    assert code == 0 and recorded() is None
    assert err.splitlines() == [
        f"moxi: note: this terminal's environment wasn't recorded: {words}"]
    assert terminal.row(terminal.last) == words


def test_the_exit_code_is_the_commands_own(fence, walks, typed, console):
    walks.found = Undecided("N1", "ended", 4000)
    code, err = console("paths", "nosuchmodule")
    assert code != 0 and err.count("moxi: note:") == 1


def test_a_broken_step_never_stops_the_command(fence, walks, typed, console):
    walks.found = RuntimeError("boom")
    code, err = console("modules")
    assert code == 0 and err == "" and recorded() is None


def test_an_interrupt_during_the_step_ends_the_command_as_an_interrupt(
        fence, walks, typed, console):
    walks.found = KeyboardInterrupt()
    code, err = console("modules")
    assert code == 130 and "interrupted" in err


# -- written only on a change ------------------------------------------------------------------

def test_an_unchanged_environment_rewrites_nothing_and_walks_nothing(
        fence, walks, typed, console):
    console("modules")
    before = paths.terminal_record().stat()
    walks.found = RuntimeError("the walk ran")
    walks.calls = 0
    code, err = console("modules")
    assert code == 0 and err == "" and walks.calls == 0
    assert terminal.last.kind == terminal.SAME
    after = paths.terminal_record().stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_a_changed_value_is_recorded(fence, walks, typed, console, monkeypatch, tmp_path):
    console("modules")
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "elsewhere"))
    console("modules")
    assert recorded()["env"]["MOXI_HOME"] == str(tmp_path / "elsewhere")
    assert walks.calls == 2


def test_a_change_outside_the_set_rewrites_nothing(fence, walks, typed, console,
                                                    monkeypatch):
    console("modules")
    monkeypatch.setenv("SOME_TOKEN", "t")
    console("modules")
    assert walks.calls == 1 and "SOME_TOKEN" not in recorded()["env"]


def test_another_writers_version_is_rewritten(fence, walks, typed, console):
    terminal.write(moxi="0.0.1")
    console("modules")
    assert recorded()["moxi"] == terminal.version() and walks.calls == 1


@pytest.mark.parametrize("data", [b"{", b'{"format": 2, "moxi": "9"}', b"[]"])
def test_a_record_that_doesnt_read_is_replaced(fence, walks, typed, console, data):
    files.atomic_write(paths.terminal_record(), data)
    console("modules")
    assert recorded()["format"] == 1


@posix_only
def test_a_file_that_cant_be_read_is_replaced_without_waiting(fence, walks, typed, console):
    path = terminal.write({"HOME": "/x"})
    path.chmod(0)
    if os.access(path, os.R_OK):
        pytest.skip("this account reads any file")
    import time

    started = time.monotonic()
    console("modules")
    assert time.monotonic() - started < 0.5
    assert recorded()["env"] == terminal.of()


def test_an_unchanged_step_reads_no_json_and_no_walk(fence):
    """An unchanged step compares bytes: it imports neither `json` nor the walk."""
    terminal.write()
    code = ("import sys; from moxi import terminal; o = terminal.step(); "
            "print(o.kind, *sorted(m for m in ('json', 'moxi.terminal._walk', "
            "'moxi.terminal._view', 'dataclasses', 'psutil') if m in sys.modules))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=120)
    assert done.stdout.split() == ["same"], done.stderr


# -- the record holds only the set, and doesn't move -----------------------------------------

def test_only_set_names_are_written_empty_kept_and_no_token(
        fence, walks, typed, console, monkeypatch):
    monkeypatch.setenv("MOXI_PROJECTS_DATA_HOME", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "tok-4f9c2a")
    monkeypatch.setenv("GITHUB_TOKEN", "tok-81be0d")
    console("modules")
    text = paths.terminal_record().read_text("utf-8")
    env = json.loads(text)["env"]
    assert env["MOXI_PROJECTS_DATA_HOME"] == ""
    assert "tok-" not in text and "ANTHROPIC_API_KEY" not in env
    assert "MOXI_ANCHOR_HOME" not in env and "MOXI_STARTED_BY" not in env
    assert all(terminal.in_set(name) for name in env)


@pytest.mark.parametrize("name", ["HOME", "MOXI_HOME", "XDG_DATA_HOME"])
def test_a_home_or_store_change_does_not_move_the_file(
        fence, walks, typed, console, monkeypatch, tmp_path, name):
    where = paths.terminal_record()
    monkeypatch.setenv(name, str(tmp_path / "moved"))
    console("modules")
    assert terminal.last.path == where and recorded()["env"][name] == str(tmp_path / "moved")


# -- a record that can't be placed or written ------------------------------------------------

@pytest.fixture
def no_account(monkeypatch):
    """No entry for this uid in the user database (no local folder on Windows), and no
    test kit's anchor variable, so the anchor has no place."""
    monkeypatch.delenv(paths.ANCHOR_HOME_ENV)
    if WINDOWS:
        from moxi import _win

        def no_folder(name):
            raise OSError("no folder")

        monkeypatch.setattr(_win, "known_folder", no_folder)
    else:
        monkeypatch.setattr(paths, "_account_home", lambda: None)


def test_an_account_with_no_place_gives_one_warning_and_the_commands_code(
        fence, walks, typed, console, no_account):
    code, err = console("modules")
    assert code == 0 and walks.calls == 1
    lines = err.splitlines()
    assert len(lines) == 1 and lines[0].startswith(
        "moxi: warning: this terminal's environment wasn't recorded: Moxi can't find "
        "where it keeps the record: ")
    if not WINDOWS:
        uid = os.getuid()
        assert lines[0].endswith(f"the account has no entry in the user database (uid {uid}); "
                                 f"give uid {uid} an entry in the user database")
    assert terminal.last.kind == terminal.NO_PLACE
    assert isinstance(terminal.last.error, paths.NoAnchor)


def test_no_place_and_no_keyboard_prints_nothing(fence, walks, console, no_account,
                                                 monkeypatch):
    monkeypatch.setattr(_record, "keyboard", lambda stdin=None: False)
    code, err = console("modules")
    assert code == 0 and err == "" and walks.calls == 0


def test_no_place_and_evidence_prints_nothing(fence, walks, typed, console, no_account):
    walks.found = Evidence("E2", 3001, "claude", "claude")
    code, err = console("modules")
    assert code == 0 and err == ""


def test_a_write_that_fails_gives_one_warning_and_the_commands_code(
        fence, walks, typed, console, monkeypatch):
    def full(path, data, *, mode=0o600):
        raise OSError(28, "No space left on device", str(path))

    monkeypatch.setattr(files, "atomic_write", full)
    code, err = console("modules")
    path = paths.terminal_record()
    assert code == 0
    assert err.splitlines() == [
        f"moxi: warning: this terminal's environment wasn't recorded: the record at {path} "
        f"can't be written (No space left on device); make {path.parent} writable by you"]
    assert terminal.last.kind == terminal.FAILED


@posix_only
def test_a_read_only_folder_gives_one_warning(fence, walks, typed, console):
    folder = paths.anchor_dir()
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o500)
    try:
        if os.access(folder, os.W_OK):
            pytest.skip("this account writes any folder")
        code, err = console("modules")
    finally:
        folder.chmod(0o700)
    assert code == 0 and err.count("moxi: warning:") == 1
    assert "can't be written (Permission denied)" in err and recorded() is None


# -- live: the console script over a real terminal ------------------------------------------

LIVE = textwrap.dedent("""\
    import sys
    from moxi import cli, terminal
    from moxi.terminal import _walk
    _walk.walk = lambda view=None, run=None: {found}
    sys.argv = ["moxi", *sys.argv[1:]]
    code = cli.main()
    print("outcome", terminal.last.kind if terminal.last else None, file=sys.stderr)
    sys.exit(code)
    """)


def _typed(argv, found="None", tty=True) -> tuple[int, str]:
    """Run the console path in a child with a pseudo-terminal (or a pipe) on its input,
    the walk replaced by one that finds `found`."""
    code = LIVE.format(found=found)
    if tty:
        leader, follower = os.openpty()
        stdin = follower
    else:
        leader, stdin = None, None
    try:
        done = subprocess.run([sys.executable, "-c", code, *argv], stdin=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              timeout=120, input=None if tty else "")
    finally:
        if leader is not None:
            os.close(leader)
            os.close(follower)
    return done.returncode, done.stderr


@posix_only
def test_live_a_terminal_on_the_input_records(fence):
    code, err = _typed(["modules"])
    assert code == 0 and "outcome written" in err, err
    assert recorded()["env"] == terminal.of()


@posix_only
def test_live_a_pipe_on_the_input_records_nothing(fence):
    code, err = _typed(["modules"], tty=False)
    assert code == 0 and "outcome no keyboard" in err, err
    assert recorded() is None


#: A parent of the run's own that started with the mark: it prints its pid, then runs its
#: arguments with the mark taken out of their environment, so the run's own start
#: environment has none and only the walk can find it.
MARKED_PARENT = textwrap.dedent("""\
    import os, subprocess, sys
    print("parent", os.getpid(), file=sys.stderr, flush=True)
    env = {k: v for k, v in os.environ.items() if k != "MOXI_STARTED_BY"}
    sys.exit(subprocess.run(sys.argv[1:], env=env).returncode)
    """)

REAL_WALK = textwrap.dedent("""\
    import sys
    from moxi import cli, terminal
    sys.argv = ["moxi", "modules"]
    code = cli.main()
    last = terminal.last
    print("outcome", last.kind, file=sys.stderr)
    if last.found is not None:
        print("found", last.found.kind, last.found.pid, file=sys.stderr)
    sys.exit(code)
    """)


@posix_only
def test_live_the_real_walk_above_a_test_records_nothing(fence):
    """Unpatched, the walk above the run finds its parent, which started with the mark,
    though the run's own environment holds none. The parent is the test's own, so this
    holds whatever runs the tests: an agent, `moxi-dev check`, or a terminal with no
    marked process above it."""
    leader, follower = os.openpty()
    try:
        done = subprocess.run(
            [sys.executable, "-c", MARKED_PARENT, sys.executable, "-c", REAL_WALK],
            env={**os.environ, "MOXI_STARTED_BY": "a test"},
            stdin=follower, capture_output=True, text=True, timeout=120)
    finally:
        os.close(leader)
        os.close(follower)
    parent = next(line.split()[1] for line in done.stderr.splitlines()
                  if line.startswith("parent "))
    assert done.returncode == 0 and "outcome found" in done.stderr, done.stderr
    assert f"found E4 {parent}" in done.stderr, done.stderr
    assert recorded() is None


def test_each_command_the_lines_name_is_a_core_verb():
    from moxi import names

    for words in ("moxi modules", "moxi doctor"):
        assert words.split()[1] in names.VERBS


# -- cost ---------------------------------------------------------------------------------------

#: What the step may add to a command when the environment is unchanged. On Windows
#: loading the two modules of `moxi.terminal` alone costs about 3 ms (each module file
#: costs over a millisecond to load there), so its bound is wider.
UNCHANGED_MS = 5.0 if WINDOWS else 2.0


@pytest.mark.slow
@pytest.mark.timed
def test_an_unchanged_step_costs_under_two_milliseconds(fence):
    """Measured as test_import_cost.py measures: the lower quartile of 51 pairs, each a
    run that imports what the `moxi` command imports anyway, then one that also runs the
    step over a record that already holds the environment."""
    import statistics
    import time

    terminal.write()
    base = "import moxi.cli, moxi.paths, moxi.files"
    step = (base + "; from moxi import terminal; o = terminal.step(); "
            "assert o.kind == 'same', o")

    def ms(code: str) -> float:
        start = time.perf_counter()
        subprocess.run([sys.executable, "-I", "-c", code], check=True)
        return (time.perf_counter() - start) * 1000

    ms(step)
    differences = []
    for _ in range(51):
        before = ms(base)
        differences.append(ms(step) - before)
    cost = statistics.quantiles(differences, n=4, method="inclusive")[0]
    assert cost < UNCHANGED_MS, f"an unchanged step costs {cost:.2f} ms"
