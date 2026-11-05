"""The check a page makes before it writes, its lines, and `where()`.

Each test writes the record under the test's own anchor (the kit's `MOXI_ANCHOR_HOME`), so
no test reads or writes the real account's record.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from moxi import cli, paths, terminal
from moxi.terminal import _check, _lines, _record


def store(env):
    """A page's places, as an adopter builds them: one module's data folder."""
    return {"the demo store": paths.Layout("demo").data_dir(env) / "demo.db"}


def write_record(env, **kw):
    return _record.write(env, **kw)


@pytest.fixture
def at(tmp_path, monkeypatch):
    """Absolute folders under the test's tmp, and an environment that sets them."""
    def folder(name):
        return str(tmp_path / name)

    return folder


def without_anchor_clause(line):
    return line.replace(" " + _lines.anchor_set(page=True), "").replace(
        " " + _lines.anchor_set(page=False), "")


# -- agreement and differences -----------------------------------------------------------------

def test_agreement_gives_none(monkeypatch, at):
    monkeypatch.setenv("MOXI_HOME", at("m"))
    write_record(None)
    assert terminal.check(store) is None


def test_a_differing_place_names_its_label_both_paths_and_the_fix(monkeypatch, at):
    write_record({**os.environ, "MOXI_HOME": at("terminal")})
    monkeypatch.setenv("MOXI_HOME", at("page"))
    line = terminal.check(store)
    page = Path(at("page")) / "data" / "demo" / "demo.db"
    there = Path(at("terminal")) / "data" / "demo" / "demo.db"
    assert (f"the demo store here is {page}, but the last run in a terminal used {there}"
            in line)
    place, step = terminal.where()
    assert f"Set MOXI_HOME to {at('terminal')} {place}, then {step}; or run the command " \
           "in a terminal." in line


def test_a_variable_only_the_page_sets_is_taken_out(monkeypatch, at):
    write_record(None)
    monkeypatch.setenv("MOXI_DEMO_HOME", at("page"))
    line = terminal.check(store)
    place, step = terminal.where()
    assert (f"Take MOXI_DEMO_HOME out of the browser shell's environment "
            f"({place.removeprefix('in ')}), then {step}; or run the command in a "
            "terminal.") in line


def test_an_empty_terminal_value_is_named_as_empty(monkeypatch, at):
    monkeypatch.setenv("MOXI_DEMO_DATA_HOME", at("pinned"))
    write_record({**os.environ, "MOXI_DEMO_DATA_HOME": ""})
    line = terminal.check(store)
    assert "MOXI_DEMO_DATA_HOME to an empty value" in line


def test_the_terminal_wording_says_what_the_page_does(monkeypatch, at):
    write_record({**os.environ, "MOXI_HOME": at("terminal")})
    monkeypatch.setenv("MOXI_HOME", at("page"))
    line = terminal.check(store, page=False)
    assert ("The browser page refuses to write while its shell differs from the last run "
            "in a terminal" in line)
    assert f"set MOXI_HOME to {at('terminal')}" in line


def test_a_place_only_one_side_resolves_differs(monkeypatch, at):
    write_record(None)

    def places(env):
        found = store(env)
        if env.get("MOXI_DEMO_DEVICE") == "x":
            found["the extra file"] = Path(at("extra"))
        return found

    monkeypatch.setenv("MOXI_DEMO_DEVICE", "x")
    line = terminal.check(places)
    assert f"the extra file here is {at('extra')}, but the last run in a terminal placed none" \
        in line
    assert "Take MOXI_DEMO_DEVICE out" in line


def test_a_difference_no_variable_gives_says_so(at):
    write_record(None)
    calls = []

    def places(env):
        env.get("HOME")
        calls.append(1)
        return {"the file": Path(at(f"f{len(calls)}"))}

    line = without_anchor_clause(terminal.check(places))
    assert line.endswith("No variable places it differently here, so run the command in a "
                         "terminal.")


# -- relative values ---------------------------------------------------------------------------

def test_a_relative_terminal_value_refuses_and_names_its_variable(monkeypatch):
    write_record({**os.environ, "MOXI_HOME": "moxi-data"})
    line = terminal.check(store)
    assert ('the demo store isn\'t a full path in the last run in a terminal '
            '(MOXI_HOME is "moxi-data")') in line
    assert "MOXI_HOME to a full path" in line


def test_a_quoted_tilde_is_not_a_full_path(monkeypatch):
    write_record({**os.environ, "MOXI_HOME": "~/m"})
    monkeypatch.setenv("MOXI_HOME", "~/m")
    line = terminal.check(store)
    assert 'isn\'t a full path here (MOXI_HOME is "~/m")' in line
    assert 'isn\'t a full path in the last run in a terminal (MOXI_HOME is "~/m")' in line
    assert "MOXI_HOME to a full path" in line


def test_a_relative_page_value_names_the_terminals_value(monkeypatch, at):
    write_record({**os.environ, "MOXI_HOME": at("t")})
    monkeypatch.setenv("MOXI_HOME", "rel")
    line = terminal.check(store)
    assert 'isn\'t a full path here (MOXI_HOME is "rel")' in line
    assert f"MOXI_HOME to {at('t')}" in line


@pytest.mark.skipif(os.name == "nt", reason="HOME places no home on Windows")
def test_a_relative_home_names_home(monkeypatch):
    monkeypatch.delenv("MOXI_HOME")
    monkeypatch.delenv("XDG_DATA_HOME")
    write_record({**os.environ, "HOME": "ada"})
    line = terminal.check(store)
    assert '(HOME is "ada")' in line
    assert "HOME to a full path" in line


@pytest.mark.skipif(os.name == "nt", reason="HOME places no home on Windows")
def test_an_empty_home_reads_as_unset_and_is_a_full_path(monkeypatch):
    monkeypatch.delenv("MOXI_HOME")
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setattr(paths, "_account_home", lambda: os.environ["HOME"])
    write_record({**os.environ, "HOME": ""})
    assert terminal.check(store) is None


# -- the one-step fix --------------------------------------------------------------------------

def test_the_masked_case_names_both_and_the_fix_passes(monkeypatch, at):
    write_record({**os.environ, "MOXI_HOME": at("terminal")})
    monkeypatch.setenv("MOXI_HOME", at("shell"))
    monkeypatch.setenv("MOXI_DEMO_DATA_HOME", at("pinned"))
    line = terminal.check(store)
    assert f"MOXI_HOME to {at('terminal')}" in line
    assert "take MOXI_DEMO_DATA_HOME out of the browser shell's environment" in line
    monkeypatch.setenv("MOXI_HOME", at("terminal"))
    monkeypatch.delenv("MOXI_DEMO_DATA_HOME")
    assert terminal.check(store) is None


# -- the contract ------------------------------------------------------------------------------

def test_a_read_outside_the_set_raises():
    write_record(None)

    def places(env):
        env.get("SOME_TOKEN")
        return store(env)

    with pytest.raises(ValueError, match="SOME_TOKEN"):
        terminal.check(places)


def test_the_anchor_variable_is_outside_the_set():
    write_record(None)

    def places(env):
        env.get(paths.ANCHOR_HOME)
        return store(env)

    with pytest.raises(ValueError, match=paths.ANCHOR_HOME):
        terminal.check(places)


def test_iterating_the_environment_is_refused():
    write_record(None)
    with pytest.raises(TypeError):
        terminal.check(lambda env: {k: Path("/x") for k in env})


# -- programs ----------------------------------------------------------------------------------

def tool(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + (".exe" if os.name == "nt" else ""))
    path.write_text("")
    path.chmod(0o755)
    return path


def test_a_program_the_terminal_finds_and_the_page_doesnt_refuses(monkeypatch, tmp_path):
    tool(tmp_path / "bin", "git")
    write_record({**os.environ, "PATH": str(tmp_path / "bin")})
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    line = terminal.check(store, programs={"git": "nothing can check what git would commit"})
    assert ("git isn't on PATH here, but the last run in a terminal found it, so nothing "
            "can check what git would commit") in line
    assert "Set PATH, with git's folder on it, " in line


def test_a_program_only_the_page_finds_passes(monkeypatch, tmp_path):
    tool(tmp_path / "bin", "git")
    write_record({**os.environ, "PATH": str(tmp_path / "empty")})
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert terminal.check(store, programs={"git": "why"}) is None


# -- no record, unreadable, newer, no place ----------------------------------------------------

def test_no_record_gives_the_no_record_line():
    line = terminal.check(store)
    record = paths.terminal_record()
    assert line.startswith("Moxi has no record of the environment your terminal uses (it is "
                           f"kept at {record})")
    assert "Type moxi modules in a terminal of your own" in line
    assert "run moxi doctor in that terminal" in line


def test_a_shapeless_record_is_no_record():
    record = paths.terminal_record()
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text('{"format": 1, "env": []}')
    assert terminal.check(store).startswith("Moxi has no record")


def test_a_persisting_read_error_gives_the_unreadable_line(monkeypatch):
    write_record(None)
    real = _record.read
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(
        PermissionError(13, "Permission denied")))
    monkeypatch.setattr(_record, "read", lambda path=None: real(path, retry=0.05))
    line = terminal.check(store)
    assert "can't be read (Permission denied), so this page refuses to write" in line
    assert "then type moxi modules in a terminal of your own" in line


def test_a_read_error_that_clears_within_the_retry_reads_the_record(monkeypatch):
    write_record(None)
    real = Path.read_bytes
    failures = [PermissionError(13, "Permission denied")]

    def flaky(self):
        if failures and self.name == paths.TERMINAL_RECORD:
            raise failures.pop()
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    assert terminal.check(store) is None
    assert not failures


def test_a_newer_format_names_both_versions():
    record = paths.terminal_record()
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text('{"format": 2, "moxi": "9.0.0", "env": {}}')
    line = terminal.check(store)
    assert f"written by moxi 9.0.0 in format 2, newer than the moxi {_record.version()}" \
        in line
    assert "restart this page on the new code (run moxi ui stop)" in line


def test_the_pages_anchor_variable_is_named():
    line = terminal.check(store)
    assert line.endswith("This page's environment sets MOXI_ANCHOR_HOME, which only test "
                         "kits set; take it out of the browser shell's environment.")


@pytest.mark.skipif(os.name == "nt", reason="the user database places the anchor on POSIX")
def test_no_anchor_variable_no_clause_and_no_place_line(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.ANCHOR_HOME)
    monkeypatch.setattr(paths, "_account_home", lambda: str(tmp_path / "acct"))
    line = terminal.check(store)
    assert "MOXI_ANCHOR_HOME" not in line
    monkeypatch.setattr(paths, "_account_home", lambda: None)
    line = terminal.check(store)
    uid = os.getuid()
    assert line == ("Moxi can't find where it keeps its record of your terminal: the "
                    f"account has no entry in the user database (uid {uid}), so this page "
                    f"refuses to write; give uid {uid} an entry in the user database, or "
                    "run the command in a terminal.")


# -- Windows names ------------------------------------------------------------------------------

def test_names_compare_without_case_on_windows():
    env = _check.Noting({"Path": "a", "moxi_home": "b"}, windows=True)
    assert env.get("PATH") == "a" and env["MOXI_HOME"] == "b" and "path" in env
    assert list(env.read) == ["PATH", "MOXI_HOME"]
    posix = _check.Noting({"Path": "a"}, windows=False)
    assert posix.get("PATH") is None


def test_the_check_folds_names_on_windows(monkeypatch):
    monkeypatch.setattr(_check, "_windows", lambda: True)
    write_record({"MOXI_HOME": r"C:\m"})
    monkeypatch.delenv("MOXI_HOME", raising=False)
    monkeypatch.setenv("moxi_home", r"C:\m")

    def places(env):
        return {"the folder": env.get("Moxi_Home") + r"\data"}

    assert terminal.check(places) is None


def test_windows_paths_compare_after_case_folding(monkeypatch):
    monkeypatch.setattr(_check, "_windows", lambda: True)
    write_record({"MOXI_HOME": r"C:\M"})
    monkeypatch.setenv("MOXI_HOME", r"c:/m")
    assert terminal.check(lambda env: {"the folder": env["MOXI_HOME"]}) is None


# -- where() and the commands the lines name -------------------------------------------------

def test_where_per_platform():
    assert terminal.where("linux") == (
        "in a file under ~/.config/environment.d/",
        "run systemctl --user daemon-reload and moxi ui stop")
    assert terminal.where("darwin") == ("in your LaunchAgent environment",
                                        "log out and back in")
    assert terminal.where("win32") == ("in your user environment variables",
                                       "log out and back in")


def test_the_page_fix_reads_set_where_then_step(monkeypatch, at):
    for platform in ("linux", "darwin", "win32"):
        line = _lines.fix(["x"], ["A to /b"], [], page=True, ui=True, platform=platform)
        place, step = terminal.where(platform)
        assert line == f"x. Set A to /b {place}, then {step}; or run the command in a terminal."


def test_the_terminal_fix_names_the_step_only_with_ui():
    with_ui = _lines.fix(["x"], ["A to /b"], [], page=False, ui=True, platform="linux")
    without = _lines.fix(["x"], ["A to /b"], [], page=False, ui=False, platform="linux")
    assert "daemon-reload" in with_ui and "daemon-reload" not in without


@pytest.mark.parametrize("command", [_lines.RECORD_COMMAND, _lines.DOCTOR_COMMAND])
def test_each_command_a_line_names_is_a_core_verb(command):
    program, verb = command.split()
    assert program == "moxi" and verb in cli._VERBS
    assert verb not in cli._NO_STRAY_CHECK
