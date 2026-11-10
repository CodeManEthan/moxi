"""rounds' doctor rows: git, each project's rounds folder, each log and the index, each
failing and passing; and that looking changes nothing."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest
from moxi import cli, modules
from moxi.doctor import Row

from moxi_rounds import asking, doctor, log, opening, paths
from moxi_rounds.cli import main

A = "proj-a/a-round"
B = "proj-a/b-round"
SAID = "Do agent-read times follow the settings?"
T1, T2 = "2026-01-02T03:04:01Z", "2026-01-02T03:04:02Z"


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The project `proj-a` with the rounds `a-round` and `b-round`, one question
    asked. Returns its rounds folder."""
    add_project("proj-a", notes_root=tmp_path / "notes-a")
    for name in (A, B):
        opening.open_round(name, description="a placeholder round", actor="planner",
                           flow="fix", at=T1)
    asking.ask(A, SAID, actor="scout", origin="comms:11", at=T2)
    capsys.readouterr()
    return tmp_path / "notes-a" / "rounds"


@pytest.fixture
def git_says(tmp_path, monkeypatch):
    """`git_says(line, code=0)`: the only git on PATH is one that prints `line` and
    exits `code`. `git_says(None)`: no git is on PATH."""

    def stand_in(line: str | None, code: int = 0) -> None:
        folder = tmp_path / "bin"
        folder.mkdir(exist_ok=True)
        if line is not None:
            script = folder / "git"
            script.write_text(f"#!/bin/sh\necho '{line}'\nexit {code}\n",
                              encoding="utf-8")
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", str(folder))

    return stand_in


def by_name() -> dict[str, Row]:
    found = {}
    for row in doctor.rows():
        assert isinstance(row, Row) and row.module == "rounds"
        assert row.name not in found, row.name
        found[row.name] = row
    return found


def everything(tmp_path):
    """Every file the test's rounds, projects and settings are kept in, with its bytes
    and the time it was last written."""
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for folder in {tmp_path, Path.home(), paths.data_dir(), paths.state_dir()}
            for path in sorted(folder.rglob("*")) if path.is_file()}


def elsewhere(rounds, slug="a-round"):
    """A line another machine's verb appended, which the index here has not seen."""
    log.append(log.round_log(rounds / slug), "question.asked",
               {"id": "Q9", "text": SAID, "from": "comms:13", "blocks": "all",
                "state": "open", "asked_by": "helper", "asked_at": T2}, actor="helper")


def spoil(path):
    with path.open("ab") as file:
        file.write(b"not an event\n")


# -- the announcement ----------------------------------------------------------------------

def test_the_announcement_names_the_doctor_and_the_core_runs_it(rounds, capsys):
    found = modules.load("rounds")
    assert found.state == modules.PRESENT, found.reason
    assert found.doctor == "moxi_rounds.doctor:rows"

    assert cli.main(["doctor", "rounds"]) == 0
    lines = capsys.readouterr().out.splitlines()
    ours = [line.split()[2:] for line in lines if line.split()[:2] == ["ok", "rounds"]]
    for name in (["git"], ["rounds", "folders"], ["logs"], ["index"]):
        assert any(words[:len(name)] == name for words in ours), (name, lines)
    assert not any("DOCTOR" in line for line in lines)


def test_with_no_project_every_row_is_fine():
    found = by_name()
    assert list(found) == ["git", "rounds folders", "logs", "index"]
    assert all(row.state == "ok" for row in found.values()), found


# -- git -------------------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "git version 2.17.0", "git version 2.17.1", "git version 2.56.0",
    "git version 2.39.3 (Apple Git-146)", "git version 2.45.1.windows.1",
    "git version 3.0.0", "git version 2.17"])
def test_a_git_at_the_floor_or_newer_is_fine(git_says, line):
    git_says(line)

    row = by_name()["git"]
    assert (row.state, row.fix) == ("ok", None)
    assert row.detail == f"{line}, which is 2.17 or later"


@pytest.mark.parametrize("line", [
    "git version 2.16.6", "git version 2.9.5", "git version 1.9.5.msysgit.1",
    "git version 2.16"])
def test_a_git_older_than_the_floor_fails_and_says_what_it_lacks(git_says, line):
    git_says(line)

    row = by_name()["git"]
    assert row.state == "fail"
    assert row.detail.startswith(f"{line}, which is older than 2.17")
    assert "git worktree remove" in row.detail
    assert row.fix == "install git 2.17 or later, and put it first on PATH"


def test_no_git_on_path_fails(git_says):
    git_says(None)

    row = by_name()["git"]
    assert row.state == "fail"
    assert row.detail == "no git is on PATH; rounds needs git 2.17 or later"
    assert row.fix == "install git 2.17 or later, and put it first on PATH"


@pytest.mark.parametrize("line, code", [("no version here", 0),
                                        ("git version 2.56.0", 1), ("", 0)])
def test_a_git_that_gives_no_version_fails(git_says, line, code):
    git_says(line, code)

    row = by_name()["git"]
    assert row.state == "fail" and "2.17" in row.fix
    assert f"`git version` exited {code} and gave no version" in row.detail


def test_a_git_that_does_not_answer_fails(git_says, tmp_path, monkeypatch):
    git_says("unused")
    (tmp_path / "bin" / "git").write_text("#!/bin/sh\nexec /bin/sleep 30\n",
                                          encoding="utf-8")
    monkeypatch.setattr(doctor, "GIT_TIMEOUT_S", 0.2)

    row = by_name()["git"]
    assert row.state == "fail"
    assert row.detail.startswith("`git version` gave no answer in 0.2 seconds")


def test_the_git_that_is_asked_sees_no_git_variable(git_says, tmp_path, monkeypatch):
    git_says("unused")
    (tmp_path / "bin" / "git").write_text(
        "#!/bin/sh\necho \"git version 2.50.0 ${GIT_DIR:-unset} $KEPT\"\n",
        encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", "/a/placeholder/dir")
    monkeypatch.setenv("KEPT", "kept")

    assert doctor.git_version() == ((2, 50, 0), "git version 2.50.0 unset kept")


def test_the_floor_is_the_git_that_first_removes_a_worktree():
    assert doctor.GIT_FLOOR == (2, 17)


# -- the rounds folders ------------------------------------------------------------------------

def test_a_project_whose_rounds_folder_cant_be_worked_out_is_named_with_the_fix(
        rounds, add_project, without_notes):
    add_project("proj-b")

    found = by_name()
    assert "rounds folders" not in found
    row = found["rounds folder proj-b"]
    assert row.state == "warn"
    assert "the rounds folder of the project 'proj-b' can't be worked out" in row.detail
    assert "[projects.proj-b]" in row.fix
    assert "moxi projects set proj-b --notes-root DIR" in row.fix
    # The projects that do have one are still looked at.
    assert found["logs"].state == "ok" and found["index"].state == "ok"


def test_a_folder_given_by_the_setting_is_fine(rounds, add_project, without_notes,
                                               write_config, tmp_path):
    add_project("proj-b")
    write_config(f"""
        [projects.proj-b]
        dir = "{tmp_path / 'elsewhere'}"
        """)

    row = by_name()["rounds folders"]
    assert row.state == "ok"
    assert row.detail == "the rounds folder of each of 2 projects can be worked out"


def test_settings_that_cant_be_read_are_one_failing_row(rounds, write_config):
    write_config("""
        no_such_key = 1
        """)

    found = by_name()
    assert list(found) == ["git", "rounds folders"]
    row = found["rounds folders"]
    assert row.state == "fail" and "no_such_key" in row.detail and row.fix


# -- the logs ----------------------------------------------------------------------------------

def test_logs_that_can_be_read_are_fine(rounds):
    row = by_name()["logs"]
    assert (row.state, row.detail) == ("ok", "every log of 1 project can be read")


def test_a_round_log_that_does_not_parse_is_named_with_its_file_and_line(rounds):
    path = log.round_log(rounds / "a-round")
    spoil(path)

    found = by_name()
    assert "logs" not in found and f"log {B}" not in found
    row = found[f"log {A}"]
    assert row.state == "fail"
    assert row.detail.startswith(f"{path} line 3 ")
    assert "restore one machine's copy of this file whole from git" in row.fix


def test_a_log_whose_numbers_skip_one_is_named(rounds):
    path = log.round_log(rounds / "b-round")
    with path.open("ab") as file:
        file.write(path.read_bytes().splitlines(keepends=True)[0])

    row = by_name()[f"log {B}"]
    assert row.state == "fail" and row.detail.startswith(f"{path} line 2 ")


def test_a_projects_own_log_that_does_not_parse_is_named(rounds):
    asking.ask("proj-a", SAID, actor="scout", origin="comms:11", at=T2)
    path = log.project_log(rounds)
    spoil(path)

    row = by_name()["log proj-a"]
    assert row.state == "fail" and row.detail.startswith(f"{path} line 2 ")


def test_every_log_that_cant_be_read_has_a_row_of_its_own(rounds):
    spoil(log.round_log(rounds / "a-round"))
    spoil(log.round_log(rounds / "b-round"))

    found = by_name()
    assert found[f"log {A}"].state == found[f"log {B}"].state == "fail"


@pytest.mark.parametrize("content", [b"", b'{"v":1,"n":1,"at":"2026-01-02T03:04:05Z",'
                                          b'"actor":"pilot","type":"repair"}\n'])
def test_a_log_that_opened_no_round_has_a_failing_row_with_the_way_out(rounds, content):
    (rounds / "c-round").mkdir()
    file = log.round_log(rounds / "c-round")
    file.write_bytes(content)

    row = by_name()["log proj-a/c-round"]
    assert row.state == "fail" and row.detail.startswith(f"{file} line 1 ")
    assert "remove the file" in row.fix and "restore this file" in row.fix


def test_a_log_is_named_the_same_with_no_index(rounds):
    spoil(log.round_log(rounds / "a-round"))
    with_index = by_name()[f"log {A}"]
    paths.index_file().unlink()

    assert by_name()[f"log {A}"] == with_index


# -- the index ---------------------------------------------------------------------------------

def test_an_index_the_writes_made_agrees_with_the_logs(rounds):
    row = by_name()["index"]
    assert (row.state, row.fix) == ("ok", None)
    assert row.detail == f"{paths.index_file()} says what the logs say"


def test_a_line_appended_elsewhere_makes_the_index_differ_until_a_listing(
        rounds, capsys):
    elsewhere(rounds)

    row = by_name()["index"]
    assert row.state == "warn"
    assert row.detail == ("the index differs from the logs on 1 log: "
                          f"{A} has a log that changed since the index last read it")
    assert row.fix.startswith("`moxi rounds list` brings the index up to the logs")

    assert main(["list"]) == 0
    capsys.readouterr()
    assert by_name()["index"].state == "ok"


def test_every_log_the_index_differs_on_is_named(rounds, capsys):
    elsewhere(rounds)
    elsewhere(rounds, "b-round")
    (rounds / "c-round").mkdir()
    log.append(log.round_log(rounds / "c-round"), "round.opened",
               {"description": "a placeholder round", "flow": "fix", "steps": []},
               actor="planner")

    row = by_name()["index"]
    assert row.state == "warn"
    assert row.detail.startswith("the index differs from the logs on 3 logs: ")
    assert "proj-a/c-round has a log and no row in the index" in row.detail


def test_a_row_whose_log_is_gone_makes_the_index_differ(rounds):
    log.round_log(rounds / "b-round").unlink()

    row = by_name()["index"]
    assert row.state == "warn"
    assert f"{B} has a row in the index and no log" in row.detail


def test_no_index_yet_is_fine(rounds):
    paths.index_file().unlink()

    row = by_name()["index"]
    assert (row.state, row.fix) == ("ok", None)
    assert row.detail.startswith(f"no index yet at {paths.index_file()}")
    assert not paths.index_file().exists()


def test_an_index_of_another_schema_is_named_and_the_logs_still_answer(rounds):
    conn = sqlite3.connect(paths.index_file())
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()

    found = by_name()
    assert found["index"].state == "warn"
    assert "is not an index this version of moxi-rounds reads" in found["index"].detail
    assert found["logs"].state == "ok"


# -- looking changes nothing -------------------------------------------------------------------

@pytest.mark.parametrize("index", ["fresh", "stale", "none"])
def test_the_doctor_changes_no_byte_or_time_of_any_file(rounds, tmp_path, capsys,
                                                        index):
    spoil(log.round_log(rounds / "b-round"))
    if index == "fresh":
        assert main(["list"]) == 0   # brings the index up to every log
        capsys.readouterr()
    elif index == "stale":
        elsewhere(rounds)
    else:
        paths.index_file().unlink()
    before = everything(tmp_path)
    assert any(path.endswith(".jsonl") for path in before)

    states = {name: row.state for name, row in by_name().items()}

    assert everything(tmp_path) == before
    assert states == {"git": "ok", "rounds folders": "ok", f"log {B}": "fail",
                      "index": "warn" if index == "stale" else "ok"}
    assert os.path.exists(paths.index_file()) == (index != "none")
