"""`moxi rounds open`: the folder and the first event it leaves, the flow a round keeps
from then on, who it is recorded as done by, and each thing it refuses."""

from __future__ import annotations

import pytest
from moxi import cli

from moxi_rounds import flows, folders, index, log, opening, paths, state, write
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, BadName, NoLog, Refused

ONE_STEP = """
    [flows.light]
    steps = [ { step = "design", by = "planner" } ]
"""
TWO_STEPS = """
    [flows.light]
    steps = [ { step = "design", by = "planner" }, { step = "retro", by = "helper" } ]
"""


@pytest.fixture
def rounds(tmp_path, add_project):
    """The rounds folder of the project `proj-a`, which its notes folder places and
    nothing has made yet."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    return tmp_path / "notes" / "rounds"


def run(*argv, description="a placeholder round"):
    """Run `moxi rounds open` with `argv`, and a description unless it is None."""
    said = [] if description is None else ["--description", description]
    return main(["open", *argv, *said])


def folded(folder):
    return state.fold(log.read(log.round_log(folder)))


def files_under(folder):
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))


# -- what an opening leaves --------------------------------------------------------------

def test_an_opened_round_folds_to_open_with_its_description_flow_and_steps(
        rounds, capsys):
    assert run("proj-a/a-round", "--flow", "small", description="tidy the parser") == 0

    opened = folded(rounds / "a-round")
    assert opened.status == state.OPEN
    assert opened.description == "tidy the parser"
    assert opened.flow == "small" and opened.steps == flows.BUILT_IN["small"]
    assert opened.parent is None
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "opened proj-a/a-round   flow small (design, build, verify-build, finish, retro)",
        f"folder: {rounds / 'a-round'}"]
    assert err == ""


def test_an_opening_writes_one_event_and_the_note_made_from_it_and_no_more(rounds):
    assert run("proj-a/a-round", "--flow", "fix") == 0

    assert files_under(rounds) == ["a-round", "a-round/round-note.md",
                                   "a-round/round.jsonl"]
    assert [(event["n"], event["type"]) for event in log.read(
        log.round_log(rounds / "a-round"))] == [(1, "round.opened")]
    assert write.round_lock("proj-a", "a-round").read_bytes() == b""


def test_an_opened_round_is_in_the_index(rounds):
    assert run("proj-a/a-round", "--flow", "fix") == 0

    found = index.read("proj-a", rounds, "a-round")
    assert (found.status, found.flow, found.folder) == ("open", "fix", rounds / "a-round")
    assert paths.index_file().is_file()


def test_the_real_command_opens_a_round(rounds, capsys):
    assert cli.main(["rounds", "open", "proj-a/a-round", "--description", "a round"]) == 0

    assert folded(rounds / "a-round").status == state.OPEN
    assert capsys.readouterr().out.startswith("opened proj-a/a-round")


def test_a_round_is_opened_under_the_projects_key_as_the_projects_list_stores_it(
        rounds, capsys):
    assert run("PROJ-A/a-round") == 0

    assert capsys.readouterr().out.startswith("opened proj-a/a-round ")
    assert write.round_lock("proj-a", "a-round").is_file()
    assert not write.round_lock("PROJ-A", "a-round").exists()


def test_a_round_opens_in_a_folder_that_is_already_there(rounds):
    (rounds / "a-round").mkdir(parents=True)
    (rounds / "a-round" / "design.md").write_text("a draft\n", encoding="utf-8")

    assert run("proj-a/a-round") == 0

    assert files_under(rounds / "a-round") == ["design.md", "round-note.md",
                                               "round.jsonl"]
    assert (rounds / "a-round" / "design.md").read_text(encoding="utf-8") == "a draft\n"


def test_help_says_what_open_does(capsys):
    assert main(["--help"]) == 0
    assert "Open a round: make its folder" in " ".join(capsys.readouterr().out.split())
    assert main(["open", "--help"]) == 0
    out = capsys.readouterr().out
    for word in ("ROUND", "--description TEXT", "--flow NAME", "--record PTR",
                 "--as NAME"):
        assert word in out


# -- the flow ----------------------------------------------------------------------------

def test_a_round_opened_with_no_flow_named_takes_the_default_flow(rounds):
    assert run("proj-a/a-round") == 0

    opened = folded(rounds / "a-round")
    assert opened.flow == "full" and opened.steps == flows.BUILT_IN["full"]


def test_a_round_opened_with_no_flow_named_takes_its_projects_flow(rounds, write_config):
    write_config("""
        [defaults]
        flow = "small"
        [projects.proj-a]
        flow = "fix"
    """)
    assert run("proj-a/a-round") == 0

    opened = folded(rounds / "a-round")
    assert opened.flow == "fix" and opened.steps == flows.BUILT_IN["fix"]


def test_a_flow_of_the_configs_own_is_recorded_with_its_steps(rounds, write_config):
    write_config(ONE_STEP)
    assert run("proj-a/a-round", "--flow", "light") == 0

    assert folded(rounds / "a-round").steps == [{"step": "design", "by": "planner"}]


def test_a_flow_edited_in_the_config_changes_nothing_for_an_open_round(
        rounds, write_config):
    write_config(ONE_STEP)
    assert run("proj-a/a-round", "--flow", "light") == 0
    before = log.round_log(rounds / "a-round").read_bytes()

    write_config(TWO_STEPS)
    assert run("proj-a/b-round", "--flow", "light") == 0

    assert log.round_log(rounds / "a-round").read_bytes() == before
    assert folded(rounds / "a-round").steps == [{"step": "design", "by": "planner"}]
    assert index.read("proj-a", rounds, "a-round").flow == "light"
    assert folded(rounds / "b-round").steps == [{"step": "design", "by": "planner"},
                                                {"step": "retro", "by": "helper"}]


def test_a_flow_removed_from_the_config_leaves_an_open_round_its_steps(
        rounds, write_config, config_file):
    write_config(ONE_STEP)
    assert run("proj-a/a-round", "--flow", "light") == 0

    config_file.unlink()

    opened = folded(rounds / "a-round")
    assert opened.flow == "light" and opened.steps == [{"step": "design", "by": "planner"}]


# -- who, and the pointer ----------------------------------------------------------------

def test_the_one_named_with_as_is_the_events_actor(rounds):
    assert run("proj-a/a-round", "--as", "planner") == 0

    assert log.read(log.round_log(rounds / "a-round"))[0]["actor"] == "planner"


def test_with_nobody_named_the_actor_is_the_os_user(rounds, monkeypatch):
    monkeypatch.setattr("getpass.getuser", lambda: "alice")
    assert run("proj-a/a-round") == 0

    assert log.read(log.round_log(rounds / "a-round"))[0]["actor"] == "alice"


def test_an_empty_name_or_a_user_with_no_name_is_a_usage_error(rounds, monkeypatch,
                                                               capsys):
    assert run("proj-a/a-round", "--as", " ") == 2
    assert "--as NAME" in capsys.readouterr().err

    def nobody():
        raise KeyError("uid not found")

    monkeypatch.setattr("getpass.getuser", nobody)
    assert run("proj-a/a-round") == 2
    assert "--as NAME" in capsys.readouterr().err
    assert not rounds.exists()


def test_the_pointer_to_the_go_is_kept_as_given(rounds):
    assert run("proj-a/a-round", "--record", "comms:12") == 0
    assert run("proj-a/b-round") == 0

    assert log.read(log.round_log(rounds / "a-round"))[0]["record"] == "comms:12"
    assert "record" not in log.read(log.round_log(rounds / "b-round"))[0]


@pytest.mark.parametrize("pointer", ["0a1b2c3d@2026-01-02T03:04Z",
                                     "https://example.com/pages/7",
                                     "file:design.md#the-go"])
def test_a_go_pointer_of_any_known_shape_is_kept(rounds, pointer):
    assert run("proj-a/a-round", "--record", pointer) == 0

    assert log.read(log.round_log(rounds / "a-round"))[0]["record"] == pointer


@pytest.mark.parametrize("pointer", ["he said go", "comms:twelve",
                                     "http://example.com/pages/7"])
def test_a_go_pointer_of_no_known_shape_is_refused_and_no_folder_is_made(
        rounds, capsys, pointer):
    assert run("proj-a/a-round", "--record", pointer) == 3

    out, err = capsys.readouterr()
    assert out == "" and "refused, with nothing written" in err
    assert repr(pointer) in err and "comms:<digits>" in err
    assert "run: moxi rounds open proj-a/a-round --description TEXT --record PTR" in err
    assert not rounds.exists()


# -- what is refused ---------------------------------------------------------------------

def test_a_folder_that_already_holds_a_log_is_refused_and_left_as_it_was(rounds, capsys):
    assert run("proj-a/a-round", "--flow", "fix", description="the first") == 0
    before = log.round_log(rounds / "a-round").read_bytes()
    capsys.readouterr()

    assert run("proj-a/a-round", "--flow", "small", description="the second") == 3

    out, err = capsys.readouterr()
    assert out == ""
    assert "refused, with nothing written" in err
    assert "proj-a/a-round is already there" in err
    assert "run: moxi rounds show proj-a/a-round" in err
    assert log.round_log(rounds / "a-round").read_bytes() == before
    assert files_under(rounds) == ["a-round", "a-round/round-note.md",
                                   "a-round/round.jsonl"]


def test_an_empty_log_file_counts_as_a_log(rounds):
    (rounds / "a-round").mkdir(parents=True)
    log.round_log(rounds / "a-round").write_bytes(b"")

    assert run("proj-a/a-round") == 3

    assert log.round_log(rounds / "a-round").read_bytes() == b""


FOREIGN = b"---\ntype: round-note\n---\n\nkept by another tool\n"


def test_a_folder_with_another_tools_note_and_no_log_is_refused_and_the_note_kept(
        rounds, capsys):
    theirs = rounds / "a-round" / "round-note.md"
    theirs.parent.mkdir(parents=True)
    theirs.write_bytes(FOREIGN)
    before = (theirs.read_bytes(), theirs.stat().st_mtime_ns, theirs.stat().st_ino)

    assert run("proj-a/a-round", "--flow", "fix") == 3

    out, err = capsys.readouterr()
    assert out == ""
    assert "refused, with nothing written" in err
    assert f"{rounds / 'a-round'} holds a round-note.md and no log" in err
    assert "belongs to another tool" in err and "pick another slug" in err
    assert "run: moxi rounds open proj-a/SLUG --description TEXT" in err
    assert (theirs.read_bytes(), theirs.stat().st_mtime_ns, theirs.stat().st_ino) == before
    assert files_under(rounds) == ["a-round", "a-round/round-note.md"]
    with pytest.raises(NoLog):
        index.read("proj-a", rounds, "a-round")


def test_another_tools_round_is_no_round_to_any_reading_command(rounds, capsys):
    theirs = rounds / "a-round" / "round-note.md"
    theirs.parent.mkdir(parents=True)
    theirs.write_bytes(FOREIGN)
    assert run("proj-a/b-round", "--flow", "fix") == 0
    capsys.readouterr()

    assert folders.slugs(rounds) == ["b-round"]
    assert main(["list"]) == 0
    assert "a-round" not in capsys.readouterr().out
    assert main(["show", "proj-a/a-round"]) == 3
    assert theirs.read_bytes() == FOREIGN
    assert files_under(rounds / "a-round") == ["round-note.md"]


def test_a_folder_with_a_note_and_a_log_is_refused_as_a_round_already_there(
        rounds, capsys):
    assert run("proj-a/a-round", "--flow", "fix") == 0
    capsys.readouterr()

    assert run("proj-a/a-round", "--flow", "fix") == 3

    err = capsys.readouterr().err
    assert "proj-a/a-round is already there" in err
    assert "another tool" not in err


def test_a_folder_that_is_there_and_empty_is_opened_into(rounds):
    (rounds / "a-round").mkdir(parents=True)

    assert run("proj-a/a-round", "--flow", "fix") == 0

    assert folded(rounds / "a-round").status == state.OPEN
    assert files_under(rounds / "a-round") == ["round-note.md", "round.jsonl"]


def test_a_project_the_projects_list_does_not_hold_is_refused(rounds, capsys):
    assert run("proj-b/a-round") == 3

    out, err = capsys.readouterr()
    assert out == "" and "there is no project 'proj-b'" in err
    assert "fix: add it with `moxi projects add proj-b" in err
    assert not rounds.exists() and not paths.locks_dir().exists()


def test_a_rounds_folder_that_cant_be_worked_out_is_refused(
        tmp_path, add_project, without_notes, capsys):
    add_project("proj-a")
    capsys.readouterr()

    assert run("proj-a/a-round") == 3

    out, err = capsys.readouterr()
    assert out == "" and "the rounds folder of the project 'proj-a'" in err
    assert "[projects.proj-a]" in err and "--notes-root" in err
    assert not paths.locks_dir().exists()
    assert not list(tmp_path.rglob("round.jsonl"))


def test_a_flow_the_config_does_not_have_is_refused_and_no_folder_is_made(rounds, capsys):
    assert run("proj-a/a-round", "--flow", "heavy") == 3

    out, err = capsys.readouterr()
    assert out == ""
    assert "there is no flow 'heavy'" in err and "fix, small, full" in err
    assert "run: moxi rounds open proj-a/a-round --flow NAME --description TEXT" in err
    assert not rounds.exists()


def test_a_projects_flow_setting_that_names_no_flow_is_refused(
        rounds, write_config, capsys):
    write_config("""
        [projects.proj-a]
        flow = "heavy"
    """)

    assert run("proj-a/a-round") == 3

    err = capsys.readouterr().err
    assert "the flow setting of the project proj-a names 'heavy'" in err
    assert not rounds.exists()
    assert run("proj-a/a-round", "--flow", "fix") == 0


def test_a_refusal_names_everything_that_stopped_it(rounds):
    opening.open_round("proj-a/a-round", description="the first", actor="planner")
    before = log.round_log(rounds / "a-round").read_bytes()

    with pytest.raises(Refused) as caught:
        opening.open_round("proj-a/a-round", description="the second", actor="planner",
                           flow="heavy")

    assert len(caught.value.unmet) == 2 and caught.value.exit_code == 3
    assert log.round_log(rounds / "a-round").read_bytes() == before


@pytest.mark.parametrize("name", ["proj-a", "proj-a/", "/a-round", "proj-a/A-Round",
                                  "proj-a/a_round", "proj-a/a round", "proj-a/a/b"])
def test_a_name_that_is_no_rounds_name_is_a_usage_error(rounds, capsys, name):
    assert run(name) == 2

    out, err = capsys.readouterr()
    assert out == "" and "is not a round's name" in err and "<project>/<slug>" in err
    assert not rounds.exists()
    with pytest.raises(BadName):
        opening.open_round(name, description="a round", actor="planner")


def test_an_opening_with_no_description_is_a_usage_error(rounds, capsys):
    assert run("proj-a/a-round", description=None) == 2
    assert "--description" in capsys.readouterr().err

    assert run("proj-a/a-round", description="  ") == 2
    assert "--description TEXT" in capsys.readouterr().err
    assert not rounds.exists()
    with pytest.raises(BadArgument):
        opening.open_round("proj-a/a-round", description="", actor="planner")


@pytest.mark.parametrize("flag", [["--parent", "proj-a/b-round"], ["--inherits", "S1"]])
def test_open_takes_no_parent_and_no_inherited_rulings(rounds, capsys, flag):
    assert run("proj-a/a-round", *flag) == 2

    assert "unrecognized arguments" in capsys.readouterr().err
    assert not rounds.exists()


def test_a_config_that_cant_be_read_is_a_bad_setting(rounds, write_config, capsys):
    write_config("""
        [defaults]
        sitting_at = -1
    """)

    assert run("proj-a/a-round") == 2

    assert "defaults.sitting_at" in capsys.readouterr().err
    assert not rounds.exists()


def test_a_round_folder_that_cant_be_made_is_an_error_and_nothing_is_recorded(
        rounds, capsys):
    rounds.mkdir(parents=True)
    (rounds / "a-round").write_text("not a folder\n", encoding="utf-8")

    assert run("proj-a/a-round") == 1

    assert "can't be made" in capsys.readouterr().err
    assert (rounds / "a-round").read_text(encoding="utf-8") == "not a folder\n"


# -- after the record --------------------------------------------------------------------

def test_work_that_fails_after_the_record_leaves_the_round_opened(
        rounds, monkeypatch, capsys):
    def fail(written):
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(write, "ON_APPEND", (fail,))

    assert run("proj-a/a-round") == 0

    out, err = capsys.readouterr()
    assert out.startswith("opened proj-a/a-round")
    assert "warning" in err and "the disk is full" in err
    assert folded(rounds / "a-round").status == state.OPEN
