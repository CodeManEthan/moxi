"""`moxi rounds show ROUND`: one round, its state and its records, as lines and as
data; a round that can't be read, one changed outside this machine, and a project."""

from __future__ import annotations

import json

import pytest
from moxi import cli

from moxi_rounds import (asking, index, log, moving, opening, paths, ruling, showing,
                         shown)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadName, NoRound, Refused

A = "proj-a/a-round"
B = "proj-a/b-round"
SAID = "Do agent-read times follow the settings?"
OTHER = "Is the status line the place for usage?"
THIRD = "Does a parsed row keep its own format?"
ANSWER = "Prose follows the settings; parsed rows do not."
RULED = "Usage is not calculated in the status line; it may be shown there."

#: Times a second apart.
T1, T2, T3, T4, T5, T6, T7, T8 = (f"2026-01-02T03:04:0{second}Z"
                                  for second in range(1, 9))


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of the project `proj-a`, with the rounds `a-round` and
    `b-round` opened in it at `T1`."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    for name in (A, B):
        opening.open_round(name, description="a placeholder round", actor="planner",
                           flow="fix", at=T1)
    capsys.readouterr()
    return tmp_path / "notes" / "rounds"


def ask(target=A, text=SAID, *, at=T2, actor="scout", origin="comms:11", **more):
    return asking.ask(target, text, actor=actor, origin=origin, at=at, **more)


def rule(target=A, *, at, record="comms:12", **more):
    return ruling.rule(target, record=record, actor="planner", at=at, **more)


def stage(rounds, stage_id, slug="a-round"):
    log.append(log.round_log(rounds / slug), "stage.declared",
               {"id": stage_id, "repo": "proj-a", "branch": f"branch-{stage_id.lower()}"},
               actor="planner")


def printed(capsys, *argv):
    """What `moxi rounds show` prints for the arguments, having exited 0."""
    assert main(["show", *argv]) == 0
    return capsys.readouterr().out


def data(capsys, name=A):
    return json.loads(printed(capsys, name, "--json"))


def logs(rounds):
    """Every log under the rounds folder, with its bytes and the time it was last
    written."""
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(rounds.rglob("*.jsonl"))}


# -- a round and its records -------------------------------------------------------------

def test_a_round_just_opened_is_shown_with_what_it_is_for_and_its_flow(rounds, capsys):
    steps = showing.show(B).state.steps
    names = ", ".join(step["step"] for step in steps)

    assert printed(capsys, B) == f"""\
round {B}   open   flow fix ({names})
description: a placeholder round
folder: {rounds / "b-round"}

questions: none

rulings: none

history: 1 event
  1  {T1}  planner  round.opened
"""
    found = data(capsys, B)
    assert list(found) == ["name", "project", "slug", "folder", "notice", "status",
                           "ended", "description", "parent", "flow", "steps", "skips",
                           "register_unreadable", "questions", "rulings", "history"]
    assert {key: found[key] for key in list(found)[:13]} == {
        "name": B, "project": "proj-a", "slug": "b-round",
        "folder": str(rounds / "b-round"), "notice": None, "status": "open",
        "ended": None, "description": "a placeholder round", "parent": None, "flow": "fix",
        "steps": steps, "skips": [], "register_unreadable": None}
    assert found["questions"] == found["rulings"] == []
    assert found["history"] == log.read(log.round_log(rounds / "b-round"))


def test_no_step_is_shown_as_the_one_the_round_is_at(rounds, capsys):
    found = data(capsys)
    assert not {"step", "current", "next"} & set(found)
    assert "step:" not in printed(capsys, A)


def test_questions_are_shown_by_state_each_with_who_asked_it_and_when(rounds, capsys):
    stage(rounds, "B1")
    ask(options=["a=prose yes, parsed rows no", "b=both follow the settings"])
    ask(text=OTHER, at=T3, actor="critic", blocks="B1")
    ask(text=THIRD, at=T4, actor="helper", origin="https://example.test/q")
    ask(text="Is a fourth one needed?", at=T5)
    rule(item="Q1", chose="a", at=T6)
    rule(item="park:Q2", until="B1", at=T6)
    rule(item="withdraw:Q4", at=T7)

    out = printed(capsys, A)

    assert f"""\
questions: 1 open, 1 parked, 1 ruled, 1 withdrawn
  Q3: {THIRD}
    from: https://example.test/q   blocks: all   state: open   by: helper   at: {T4}
  Q2: {OTHER}
    from: comms:11   blocks: B1   state: parked   by: critic   at: {T3}   \
until: B1   ruling: C2
  Q1: {SAID}
    from: comms:11   blocks: all   state: ruled   by: scout   at: {T2}   \
ruling: C1   chose: a
    option a: prose yes, parsed rows no
    option b: both follow the settings
  Q4: Is a fourth one needed?
    from: comms:11   blocks: all   state: withdrawn   by: scout   at: {T5}   \
ruling: C3
""" in out
    found = data(capsys)["questions"]
    assert [(one["id"], one["state"], one["by"], one["at"]) for one in found] == [
        ("Q1", "ruled", "scout", T2), ("Q2", "parked", "critic", T3),
        ("Q3", "open", "helper", T4), ("Q4", "withdrawn", "scout", T5)]
    assert found[0] == {
        "id": "Q1", "text": SAID, "from": "comms:11", "blocks": "all", "state": "ruled",
        "by": "scout", "at": T2,
        "options": [{"key": "a", "text": "prose yes, parsed rows no"},
                    {"key": "b", "text": "both follow the settings"}],
        "chose": "a", "ruling": "C1"}
    assert found[1]["until"] == "B1" and found[1]["blocks"] == ["B1"]
    # A bare question is shown bare.
    assert found[2] == {"id": "Q3", "text": THIRD, "from": "https://example.test/q",
                        "blocks": "all", "state": "open", "by": "helper", "at": T4}


def test_a_parked_question_whose_wait_is_over_is_shown_open_with_its_parking(
        rounds, capsys):
    stage(rounds, "B1")
    ask()
    rule(item="park:Q1", until="B1", at=T3)
    log.append(log.round_log(rounds / "a-round"), "finish.done",
               {"stage": "B1", "tip": "1" * 40}, actor="pilot")

    [found] = data(capsys)["questions"]
    assert (found["state"], found["recorded"], found["until"]) == (
        "open", "parked", "B1")
    out = printed(capsys, A)
    assert "questions: 1 open\n" in out
    assert "state: open   by: scout" in out
    assert "recorded as parked until B1, which has come" in out


def test_a_question_moved_away_is_shown_as_moved_with_where_it_went(rounds, capsys):
    ask()
    moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")
    moving.adopt(B, "P1", actor="curator")

    [left] = data(capsys)["questions"]
    assert (left["state"], left["moved_to"]) == ("moved", "P1")
    assert "state: moved   by: scout" in printed(capsys, A)
    [arrived] = data(capsys, B)["questions"]
    assert (arrived["state"], arrived["moved_from"], arrived["by"], arrived["at"]) == (
        "open", "P1", "scout", T2)
    assert "moved from: P1" in printed(capsys, B)


def test_rulings_are_shown_with_their_pointers_and_whether_each_was_shown(
        rounds, capsys):
    rule(text=RULED, at=T2, reason="it is only a display")
    rule(text=RULED + " Again.", at=T3, record="https://example.test/said")
    rule(text=RULED + " Thrice.", at=T5)
    shown.mark(A, "C2", record="comms:20", actor="planner")
    rule("proj-a", item=f"readback={T2}", record="comms:90", at=T6)

    assert f"""\
rulings: 3
  C1: {RULED}
    record: comms:12   kind: substance   by: planner   at: {T2}   stages: all   \
shown: read back through {T2}
    reason: it is only a display
  C2: {RULED} Again.
    record: https://example.test/said   kind: substance   by: planner   at: {T3}   \
stages: all   shown: comms:20
  C3: {RULED} Thrice.
    record: comms:12   kind: substance   by: planner   at: {T5}   stages: all   \
not shown
""" in printed(capsys, A)
    found = data(capsys)["rulings"]
    assert [(one["id"], one["record"], one["shown"], one["shown_record"],
             one["read_back"]) for one in found] == [
        ("C1", "comms:12", True, None, T2),
        ("C2", "https://example.test/said", True, "comms:20", None),
        ("C3", "comms:12", False, None, None)]
    assert found[0] == {
        "id": "C1", "text": RULED, "record": "comms:12",
        "reason": "it is only a display", "kind": "substance", "rules": None,
        "stages": "all", "chose": None, "by": "planner", "at": T2, "supersedes": None,
        "revokes": None, "replaced_by": None, "revoked_by": None, "shown": True,
        "shown_record": None, "read_back": T2}


def test_a_ruling_replaced_or_taken_back_says_by_which(rounds, capsys):
    rule(text=RULED, at=T2)
    rule(text=RULED + " Again.", at=T3, supersedes="C1")
    rule(item="revoke:C2", at=T4)

    found = data(capsys)["rulings"]
    assert [(one["id"], one["supersedes"], one["revokes"], one["replaced_by"],
             one["revoked_by"]) for one in found] == [
        ("C1", None, None, "C2", None), ("C2", "C1", None, None, "C3"),
        ("C3", None, "C2", None, None)]
    out = printed(capsys, A)
    assert "replaced by: C2" in out and "replaces: C1   taken back by: C3" in out
    assert "  C3: revoke:C2\n" in out and "takes back: C2" in out


def test_the_history_is_every_event_oldest_first(rounds, capsys):
    ask()
    rule(item="Q1", text=ANSWER, at=T3)
    shown.mark(A, "C1", record="comms:20", actor="curator", at=T4)

    assert printed(capsys, A).endswith(f"""\
history: 4 events
  1  {T1}  planner  round.opened
  2  {T2}  scout  question.asked  Q1
  3  {T3}  planner  ruling.recorded  C1
  4  {T4}  curator  ruling.shown  C1
""")
    assert data(capsys)["history"] == log.read(log.round_log(rounds / "a-round"))


def test_a_round_is_shown_alone(rounds, capsys):
    ask()
    ask(B, text=OTHER, at=T3)
    rule("proj-a", text=RULED, at=T4)

    found = data(capsys, B)
    assert [one["text"] for one in found["questions"]] == [OTHER]
    assert found["rulings"] == []


# -- what show leaves alone, and the index -----------------------------------------------

def test_showing_changes_no_byte_of_a_log(rounds, capsys):
    ask()
    rule(text=RULED, at=T3)
    before = logs(rounds)

    for argv in ([A], [A, "--json"], [B]):
        assert main(["show", *argv]) == 0

    assert logs(rounds) == before


def test_showing_brings_the_rounds_row_of_the_index_up_to_its_log(rounds, capsys):
    ask()
    log.append(log.round_log(rounds / "a-round"), "question.asked",
               {"id": "Q2", "text": OTHER, "from": "comms:13", "blocks": "all",
                "state": "open", "asked_by": "helper", "asked_at": T3}, actor="helper")
    from moxi_rounds import folders
    home = folders.home("proj-a")
    assert index.disagreements([home]) != []

    assert [one["id"] for one in data(capsys)["questions"]] == ["Q1", "Q2"]

    assert index.disagreements([home]) == []


def test_a_round_is_shown_when_the_index_cant_be_written(rounds, capsys, monkeypatch):
    import sqlite3

    ask()
    expected = data(capsys)
    paths.index_file().unlink()

    def fails(*args, **more):
        raise sqlite3.OperationalError("the index can't be written")

    monkeypatch.setattr(index, "refresh", fails)

    assert data(capsys) == expected


# -- a round that can't be read, and one changed outside ---------------------------------

def test_a_round_that_cant_be_read_is_shown_as_unreadable_with_file_and_line(
        rounds, capsys):
    ask()
    rule(text=RULED, at=T3)
    file = log.round_log(rounds / "a-round")
    with file.open("ab") as written:
        written.write(b"not an event\n")

    out = printed(capsys, A)
    assert out.startswith(f"round {A}   unreadable\nfolder: {rounds / 'a-round'}\n"
                          f"{file} line 4 ")
    assert "  fix: " in out
    # Not as an empty round, and not as it was before.
    assert "questions" not in out and "history" not in out and SAID not in out

    found = data(capsys)
    assert set(found) == {"name", "project", "slug", "folder", "notice", "unreadable"}
    assert (found["unreadable"]["file"], found["unreadable"]["line"]) == (str(file), 4)
    assert found["unreadable"]["reason"] and found["unreadable"]["fix"]


#: What a round's log may hold in place of the event that opened it: nothing, and an
#: event of another kind.
NOT_OPENED = [b"", b'{"v":1,"n":1,"at":"2026-01-02T03:04:05Z","actor":"pilot",'
                   b'"type":"finish.done","stage":"B1","tip":"' + b"1" * 40 + b'"}\n']


def unopened(rounds, content, slug="c-round"):
    """A round's folder whose log holds `content` and opened no round. Returns the
    log."""
    (rounds / slug).mkdir(parents=True)
    file = log.round_log(rounds / slug)
    file.write_bytes(content)
    return file


@pytest.mark.parametrize("content", NOT_OPENED)
def test_a_log_that_opened_no_round_is_shown_as_unreadable_and_not_as_a_round(
        rounds, capsys, content):
    file = unopened(rounds, content)
    name = "proj-a/c-round"

    out = printed(capsys, name)
    assert out.startswith(f"round {name}   unreadable\nfolder: {rounds / 'c-round'}\n"
                          f"{file} line 1 ")
    assert "None" not in out and "description" not in out
    assert "remove the file" in out and "restore this file" in out
    found = data(capsys, name)
    assert set(found) == {"name", "project", "slug", "folder", "notice", "unreadable"}
    assert (found["unreadable"]["file"], found["unreadable"]["line"]) == (str(file), 1)


@pytest.mark.parametrize("content", NOT_OPENED)
def test_no_verb_writes_to_a_log_that_opened_no_round(rounds, capsys, content):
    file = unopened(rounds, content)

    assert main(["ask", "proj-a/c-round", SAID, "--from", "comms:11",
                 "--as", "scout"]) == 3
    assert f"{file} line 1 " in capsys.readouterr().err
    assert main(["open", "proj-a/c-round", "--description", "a placeholder round",
                 "--as", "planner"]) == 3
    assert "already there" in capsys.readouterr().err
    assert file.read_bytes() == content
    assert sorted(path.name for path in file.parent.iterdir()) == ["round.jsonl"]


def test_a_round_whose_numbers_skip_one_is_shown_as_unreadable(rounds, capsys):
    file = log.round_log(rounds / "a-round")
    first = file.read_bytes()
    file.write_bytes(first + first.replace(b'"n":1', b'"n":3'))

    assert f"round {A}   unreadable\n" in printed(capsys, A)
    assert data(capsys)["unreadable"]["line"] == 2


def test_a_log_changed_outside_this_machine_is_shown_with_the_notice(rounds, capsys):
    ask()
    assert data(capsys)["notice"] is None
    file = log.round_log(rounds / "a-round")
    seen = index.read("proj-a", rounds, "a-round")
    assert seen.notice is None
    file.write_bytes(file.read_bytes().replace(b"a placeholder round",
                                               b"another placeholder"))

    found = data(capsys)
    notice = found["notice"]
    assert notice.startswith("log changed outside this machine since ")
    assert found["description"] == "another placeholder"
    assert printed(capsys, A).splitlines()[3] == notice
    # It stays through every showing, until the next write here.
    assert data(capsys)["notice"] == notice
    ask(text=OTHER, at=T3)
    assert data(capsys)["notice"] is None


def test_with_the_projects_log_unreadable_a_ruling_is_not_said_to_be_unshown(
        rounds, capsys):
    rule(text=RULED, at=T2)
    rule(text=RULED + " Again.", at=T3)
    shown.mark(A, "C1", record="comms:20", actor="planner")
    with log.project_log(rounds).open("ab") as written:
        written.write(b"not an event\n")

    found = data(capsys)
    assert [one["shown"] for one in found["rulings"]] == [True, None]
    assert str(log.project_log(rounds)) in found["register_unreadable"]
    out = printed(capsys, A)
    assert "what he has read back is not known: " in out and "shown: not known" in out


# -- what is not shown -------------------------------------------------------------------

def test_a_project_is_not_shown_and_the_refusal_names_the_listing(rounds, capsys):
    assert main(["show", "proj-a"]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "run: moxi rounds list proj-a" in captured.err
    with pytest.raises(Refused):
        showing.show("proj-a")


def test_a_round_never_opened_and_a_project_not_known_are_refused(rounds, capsys):
    assert main(["show", "proj-a/no-round"]) == 3
    assert "moxi rounds open proj-a/no-round" in capsys.readouterr().err
    assert main(["show", "proj-z/a-round"]) == 3
    assert "proj-z" in capsys.readouterr().err
    with pytest.raises(NoRound):
        showing.show("proj-a/no-round")


@pytest.mark.parametrize("argv", [
    [], ["proj-a/A-Round"], ["proj-a/a-round/more"], [""], [A, B], [A, "--waiting"],
    [A, "--questions"]])
def test_a_showing_that_cant_be_read_is_a_usage_error(rounds, capsys, argv):
    assert main(["show", *argv]) == 2
    assert capsys.readouterr().out == ""


def test_a_name_that_is_no_rounds_is_a_bad_name(rounds):
    with pytest.raises(BadName):
        showing.show("proj-a/A-Round")


def test_the_core_command_runs_show(rounds, capsys):
    assert cli.main(["rounds", "show", A, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == A


def test_help_says_what_show_does(capsys):
    assert main(["show", "--help"]) == 0

    said = " ".join(capsys.readouterr().out.split())
    for part in ("ROUND", "--json", "Show one round", "listed with list"):
        assert part in said
