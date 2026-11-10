"""`moxi rounds list`: the open rounds and the ended ones, of every project or of one,
and with `--waiting` what waits on the operator; each as lines and as data."""

from __future__ import annotations

import json

import pytest
from moxi import cli

from moxi_rounds import (asking, dropping, export, index, listing, log, opening, paths,
                         ruling, shown)
from moxi_rounds.cli import main

A = "proj-a/a-round"
B = "proj-a/b-round"
C = "proj-b/c-round"
SAID = "Do agent-read times follow the settings?"
OTHER = "Is the status line the place for usage?"
THIRD = "Does a parsed row keep its own format?"
RULED = "Usage is not calculated in the status line; it may be shown there."

#: Times a second apart, and one three days after the first, in which a listing is
#: computed.
T1, T2, T3, T4, T5, T6 = (f"2026-01-02T03:04:0{second}Z" for second in range(1, 7))
NOW = "2026-01-05T03:04:01Z"

#: The fields every question has, as data.
BARE = {"holder", "id", "text", "from", "blocks", "state", "by", "at"}


@pytest.fixture
def rounds(tmp_path, add_project, capsys, monkeypatch):
    """Two projects: `proj-a` with the rounds `a-round` and `b-round`, and `proj-b`
    with `c-round`; `a-round` has the stages B1 and B2. Returns the rounds folder of
    `proj-a`. Every listing the test computes is computed at `NOW`."""
    add_project("proj-a", notes_root=tmp_path / "notes-a")
    add_project("proj-b", notes_root=tmp_path / "notes-b")
    for name in (A, B, C):
        opening.open_round(name, description="a placeholder round", actor="planner",
                           flow="fix", at=T1)
    folder = tmp_path / "notes-a" / "rounds"
    for stage in ("B1", "B2"):
        log.append(log.round_log(folder / "a-round"), "stage.declared",
                   {"id": stage, "repo": "proj-a", "branch": f"branch-{stage.lower()}"},
                   actor="planner")
    capsys.readouterr()
    real = log.stamp
    monkeypatch.setattr(log, "stamp", lambda when_s=None: NOW if when_s is None
                        else real(when_s))
    return folder


def ask(target=A, text=SAID, *, at=T1, actor="scout", origin="comms:11", **more):
    return asking.ask(target, text, actor=actor, origin=origin, at=at, **more)


def rule(target=A, *, at=T5, record="comms:12", **more):
    return ruling.rule(target, record=record, actor="planner", at=at, **more)


def printed(capsys, *argv):
    """What `moxi rounds list` prints for the arguments, having exited 0."""
    assert main(["list", *argv]) == 0
    return capsys.readouterr().out


def data(capsys, *argv):
    return json.loads(printed(capsys, *argv, "--json"))


def logs(*folders):
    """Every log under the folders, with its bytes and the time it was last written."""
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for folder in folders for path in sorted(folder.rglob("*.jsonl"))}


def spoil(rounds, slug="a-round"):
    """Make a round's log one that can't be read."""
    with log.round_log(rounds / slug).open("ab") as file:
        file.write(b"not an event\n")


# -- the rounds --------------------------------------------------------------------------

def test_the_open_rounds_of_every_project_are_listed_with_flow_and_counts(
        rounds, capsys):
    ask()
    ask(text=OTHER, at=T2)
    ask(text=THIRD, at=T3)
    rule(item="park:Q2", until="B2", at=T4)
    rule(text=RULED, at=T5)
    ask(C, at=T2)

    assert printed(capsys) == (
        f"{A}   open   flow fix   open questions: 2   parked: 1   "
        "rulings not shown: 2\n"
        f"{B}   open   flow fix   open questions: 0   parked: 0   "
        "rulings not shown: 0\n"
        f"{C}   open   flow fix   open questions: 1   parked: 0   "
        "rulings not shown: 0\n")


def test_the_rounds_as_data_are_the_rounds_as_lines(rounds, capsys):
    ask()
    rule(text=RULED, at=T5)

    found = data(capsys)

    assert found["computed"] == NOW and found["project"] is None
    assert found["ended"] is False
    assert found["unreadable"] == [] and found["no_folder"] == []
    assert found["rounds"] == [
        {"name": A, "project": "proj-a", "folder": str(rounds / "a-round"),
         "status": "open", "flow": "fix",
         "counts": {"open": 1, "parked": 0, "not_shown": 1}, "ended": None},
        {"name": B, "project": "proj-a", "folder": str(rounds / "b-round"),
         "status": "open", "flow": "fix",
         "counts": {"open": 0, "parked": 0, "not_shown": 0}, "ended": None},
        {"name": C, "project": "proj-b",
         "folder": str(rounds.parent.parent / "notes-b" / "rounds" / "c-round"),
         "status": "open", "flow": "fix",
         "counts": {"open": 0, "parked": 0, "not_shown": 0}, "ended": None}]


def test_a_project_named_keeps_the_rounds_to_its_own(rounds, capsys):
    assert [one["name"] for one in data(capsys, "proj-a")["rounds"]] == [A, B]
    assert [one["name"] for one in data(capsys, "proj-b")["rounds"]] == [C]
    assert data(capsys, "proj-b")["project"] == "proj-b"
    assert printed(capsys, "proj-b").splitlines() == [
        f"{C}   open   flow fix   open questions: 0   parked: 0   "
        "rulings not shown: 0"]


def test_a_ruling_shown_or_read_back_is_not_counted(rounds, capsys):
    rule(text=RULED, at=T2)
    rule(text=RULED, at=T3)
    shown.mark(A, "C1", record="comms:20", actor="planner")
    assert data(capsys)["rounds"][0]["counts"]["not_shown"] == 1

    rule("proj-a", item=f"readback={T3}", record="comms:90", at=T6)
    assert data(capsys)["rounds"][0]["counts"]["not_shown"] == 0


def test_a_parked_question_whose_wait_is_over_is_counted_as_open(rounds, capsys):
    ask()
    rule(item="park:Q1", until="B2", at=T2)
    assert data(capsys)["rounds"][0]["counts"] == {
        "open": 0, "parked": 1, "not_shown": 1}

    log.append(log.round_log(rounds / "a-round"), "finish.done",
               {"stage": "B2", "tip": "1" * 40}, actor="pilot")
    assert data(capsys)["rounds"][0]["counts"] == {
        "open": 1, "parked": 0, "not_shown": 1}


def test_no_open_round_is_among_the_ended_ones(rounds, capsys):
    assert printed(capsys, "--closed") == "no round has ended\n"
    assert printed(capsys, "proj-a", "--closed") == "no round of proj-a has ended\n"
    found = data(capsys, "--closed")
    assert found["ended"] is True and found["rounds"] == []


def test_a_dropped_round_is_listed_only_among_the_ended_with_how_and_when(
        rounds, capsys):
    rule(B, text=RULED)
    dropping.drop(B, by="operator", record="comms:30", actor="planner", at=T6)

    assert [one["name"] for one in data(capsys)["rounds"]] == [A, C]
    assert B not in printed(capsys) and B not in printed(capsys, "proj-a")
    [found] = data(capsys, "--closed")["rounds"]
    assert (found["name"], found["status"]) == (B, "dropped")
    assert found["ended"] == {"how": "dropped", "by": "operator", "record": "comms:30",
                              "at": T6}
    assert found["counts"] == {"open": 0, "parked": 0, "not_shown": 1}
    assert printed(capsys, "--closed") == printed(capsys, "proj-a", "--closed") == (
        f"{B}   dropped by operator at {T6}, record comms:30   flow fix   "
        "open questions: 0   parked: 0   rulings not shown: 1\n")
    assert printed(capsys, "proj-b", "--closed") == "no round of proj-b has ended\n"


def test_a_dropped_round_is_listed_the_same_with_no_index(rounds, capsys):
    dropping.drop(B, by="lead", record="comms:30", actor="planner", at=T6)
    paths.index_file().unlink()

    assert listing.rounds(ended=True).rounds[0].ended == {
        "how": "dropped", "by": "lead", "record": "comms:30", "at": T6}


def test_nothing_of_a_dropped_round_waits_on_the_operator(rounds, capsys):
    # A question left open in a round that has ended: its answer was taken back
    # after the drop, which reopens nothing.
    ask(B)
    rule(B, item="Q1", text="They do.", at=T3)
    dropping.drop(B, by="operator", record="comms:30", actor="planner", at=T4)
    rule(B, item="revoke:C1", at=T5)
    ask(A, OTHER)

    found = data(capsys, "--waiting")
    assert [(one["holder"], one["id"]) for one in found["open"]] == [(A, "Q1")]
    assert found["parked"] == []
    # Its rulings are still his to be shown.
    assert {row["holder"] for row in found["not_shown"][0]["rows"]} == {B}


def test_a_round_that_cant_be_read_is_listed_as_unreadable(rounds, capsys):
    ask()
    spoil(rounds)
    file = log.round_log(rounds / "a-round")

    lines = printed(capsys).splitlines()
    assert lines[0].startswith(f"{A}   unreadable: {file} line 5 ")
    assert "open" not in lines[0] and lines[1].startswith(f"{B}   open")

    first = data(capsys)["rounds"][0]
    assert set(first) == {"name", "project", "folder", "unreadable"}
    assert str(file) in first["unreadable"]["reason"]
    assert first["unreadable"]["fix"]
    # Nothing says whether it ended, so it is in that listing too.
    assert [one["name"] for one in data(capsys, "--closed")["rounds"]] == [A]


def test_a_project_whose_own_log_cant_be_read_is_named_and_its_counts_not_known(
        rounds, capsys):
    ask()
    rule("proj-a", text=RULED, at=T2)
    with log.project_log(rounds).open("ab") as file:
        file.write(b"not an event\n")

    out = printed(capsys)
    assert out.splitlines()[0] == (
        f"{A}   open   flow fix   open questions: 1   parked: 0   "
        "rulings not shown: not known")
    assert "logs that can't be read, whose records are not listed:" in out
    found = data(capsys)
    assert found["rounds"][0]["counts"]["not_shown"] is None
    assert found["rounds"][2]["counts"]["not_shown"] == 0
    [reason] = found["unreadable"]
    assert str(log.project_log(rounds)) in reason


def test_a_project_with_no_rounds_folder_is_named_and_the_rest_listed(
        rounds, add_project, without_notes, capsys):
    add_project("box-1")
    capsys.readouterr()

    out = printed(capsys)
    assert out.startswith(f"{A}   open")
    assert "projects whose rounds folder can't be worked out:" in out
    [reason] = data(capsys)["no_folder"]
    assert "box-1" in reason


# -- what waits on the operator ----------------------------------------------------------

def waiting_setup():
    """Questions and rulings in both projects: an open question with options, an open
    one on a project's list, a parked one, and three rulings."""
    ask(options=["a=prose yes, parsed rows no", "b=both follow the settings"])
    ask(text=OTHER, at=T2, blocks="B2")
    ask("proj-a", text=THIRD, at=T3, actor="critic")
    rule(item="park:Q2", until="B2", at=T4)
    rule(text=RULED, at=T5)
    ask(C, at=T2, actor="helper", origin="https://example.test/q")
    rule("proj-b", text=RULED, at=T3, record="https://example.test/said")


def test_what_waits_is_every_open_and_parked_question_and_every_ruling_not_shown(
        rounds, capsys):
    waiting_setup()

    assert printed(capsys, "--waiting") == f"""\
waiting on the operator, computed {NOW}

open questions:
  proj-a P1: {THIRD}
    from: comms:11   blocks: none   state: open   by: critic   at: {T3}   \
age: 2 days   options: none
  {A} Q1: {SAID}
    from: comms:11   blocks: all   state: open   by: scout   at: {T1}   \
age: 3 days   options: a, b
  {C} Q1: {SAID}
    from: https://example.test/q   blocks: all   state: open   by: helper   \
at: {T2}   age: 2 days   options: none

parked questions, not put to him:
  {A} Q2: {OTHER}
    from: comms:11   blocks: B2   state: parked   by: scout   at: {T2}   until: B2

rulings not yet shown to him:
  proj-a, not yet shown, through {T5}; once he has read them: moxi rounds rule \
proj-a readback={T5} --record PTR
    {A} C1: park:Q2 (record: comms:12)
    {A} C2: {RULED} (record: comms:12)
  proj-b, not yet shown, through {T3}; once he has read them: moxi rounds rule \
proj-b readback={T3} --record PTR
    proj-b S1: {RULED} (record: https://example.test/said)
"""


def test_what_waits_as_data_holds_the_same_rows(rounds, capsys):
    waiting_setup()

    found = data(capsys, "--waiting")

    assert list(found) == ["computed", "project", "open", "parked", "not_shown",
                           "unreadable", "no_folder"]
    assert found["computed"] == NOW and found["project"] is None
    assert [(one["holder"], one["id"]) for one in found["open"]] == [
        ("proj-a", "P1"), (A, "Q1"), (C, "Q1")]
    assert found["open"][1] == {
        "holder": A, "id": "Q1", "text": SAID, "from": "comms:11", "blocks": "all",
        "state": "open", "by": "scout", "at": T1,
        "options": [{"key": "a", "text": "prose yes, parsed rows no"},
                    {"key": "b", "text": "both follow the settings"}]}
    assert found["parked"] == [
        {"holder": A, "id": "Q2", "text": OTHER, "from": "comms:11", "blocks": ["B2"],
         "state": "parked", "by": "scout", "at": T2, "until": "B2"}]
    assert [(one["project"], one["through"], [row["id"] for row in one["rows"]])
            for one in found["not_shown"]] == [
        ("proj-a", T5, ["C1", "C2"]), ("proj-b", T3, ["S1"])]
    assert found["not_shown"][1]["rows"] == [
        {"holder": "proj-b", "id": "S1", "text": RULED, "rules": None,
         "record": "https://example.test/said", "supersedes": None, "revokes": None,
         "at": T3}]


def test_every_question_that_waits_says_who_asked_it_and_when(rounds, capsys):
    waiting_setup()

    found = data(capsys, "--waiting")
    out = printed(capsys, "--waiting")

    for one in (*found["open"], *found["parked"]):
        assert BARE <= set(one)
        assert f"by: {one['by']}   at: {one['at']}" in out
    assert {(one["by"], one["at"]) for one in found["open"]} == {
        ("critic", T3), ("scout", T1), ("helper", T2)}


def test_a_projects_waiting_questions_equal_the_entries_of_its_export(rounds, capsys):
    waiting_setup()
    ask(B, text=THIRD, at=T4, options=["x=its own", "y=the settings'"])

    waits = data(capsys, "proj-a", "--waiting")
    exported = data(capsys, "proj-a", "--questions")

    assert waits["open"] + waits["parked"] == exported["entries"]
    assert len(exported["entries"]) == 4
    assert waits["project"] == "proj-a"
    # The rulings not yet shown are the export's list too.
    [back] = waits["not_shown"]
    assert {key: back[key] for key in ("through", "rows")} == exported["read_back"]


def test_the_question_rows_are_the_export_slices_rows(rounds):
    waiting_setup()
    from moxi_rounds import folders

    rows = export.waiting(folders.home("proj-a"))
    found = dict(listing.waiting("proj-a").found)

    assert (*found["open"], *found["parked"]) == rows.entries


def test_a_project_named_keeps_what_waits_to_its_own(rounds, capsys):
    waiting_setup()

    found = data(capsys, "proj-b", "--waiting")

    assert [(one["holder"], one["id"]) for one in found["open"]] == [(C, "Q1")]
    assert found["parked"] == []
    assert [one["project"] for one in found["not_shown"]] == ["proj-b"]
    out = printed(capsys, "proj-b", "--waiting")
    assert out.startswith(f"waiting on the operator in proj-b, computed {NOW}\n")
    assert "parked questions, not put to him:\n  none\n" in out
    assert "proj-a" not in out


def test_with_nothing_waiting_each_kind_says_none(rounds, capsys):
    assert printed(capsys, "--waiting") == f"""\
waiting on the operator, computed {NOW}

open questions:
  none

parked questions, not put to him:
  none

rulings not yet shown to him:
  none
"""
    found = data(capsys, "--waiting")
    assert found["open"] == found["parked"] == found["not_shown"] == []


def test_a_question_ruled_or_a_ruling_shown_waits_no_longer(rounds, capsys):
    ask()
    rule(item="Q1", text=RULED, at=T2)
    assert data(capsys, "--waiting")["open"] == []
    assert [row["id"] for row in data(capsys, "--waiting")["not_shown"][0]["rows"]] == [
        "C1"]

    shown.mark(A, "C1", record="comms:20", actor="planner")
    assert data(capsys, "--waiting")["not_shown"] == []


def test_a_question_moved_to_the_projects_list_waits_with_where_it_came_from(
        rounds, capsys):
    from moxi_rounds import moving

    ask()
    moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")

    [found] = data(capsys, "proj-a", "--waiting")["open"]
    assert (found["holder"], found["id"], found["moved_from"]) == (
        "proj-a", "P1", f"{A}:Q1")
    assert f"moved from: {A}:Q1, where it held up all" in printed(
        capsys, "proj-a", "--waiting")


def test_a_round_that_cant_be_read_is_named_among_what_waits(rounds, capsys):
    ask()
    ask(B, text=OTHER, at=T2)
    spoil(rounds)

    found = data(capsys, "--waiting")
    assert [(one["holder"], one["id"]) for one in found["open"]] == [(B, "Q1")]
    [reason] = found["unreadable"]
    assert str(log.round_log(rounds / "a-round")) in reason
    assert (f"logs that can't be read, whose records are not listed:\n  {reason}\n"
            in printed(capsys, "--waiting"))


@pytest.mark.parametrize("at, said", [
    (T1, "3 days"), ("2026-01-04T03:04:01Z", "1 day"), ("2026-01-05T01:04:01Z", "2 hours"),
    ("2026-01-05T03:03:01Z", "1 minute"), ("2026-01-05T03:04:00Z", "under a minute"),
    (None, "not known")])
def test_an_age_is_in_the_largest_whole_unit(at, said):
    assert listing.age(at, NOW) == said


# -- what a listing leaves alone ---------------------------------------------------------

def test_no_listing_changes_a_byte_of_a_log(rounds, capsys):
    waiting_setup()
    before = logs(rounds.parent.parent)

    for argv in ([], ["--json"], ["proj-a"], ["--closed"], ["--waiting"],
                 ["--waiting", "--json"], ["proj-b", "--waiting"]):
        assert main(["list", *argv]) == 0

    assert logs(rounds.parent.parent) == before


def test_a_listing_brings_the_index_up_to_logs_written_elsewhere(rounds, capsys):
    ask()
    log.append(log.round_log(rounds / "a-round"), "question.asked",
               {"id": "Q2", "text": OTHER, "from": "comms:13", "blocks": "all",
                "state": "open", "asked_by": "helper", "asked_at": T2}, actor="helper")
    assert index.disagreements([_home("proj-a"), _home("proj-b")]) != []

    assert data(capsys)["rounds"][0]["counts"]["open"] == 2

    assert index.disagreements([_home("proj-a"), _home("proj-b")]) == []


def test_a_listing_with_no_index_to_write_reads_the_logs(rounds, capsys, monkeypatch):
    import sqlite3

    waiting_setup()
    expected = data(capsys, "--waiting")
    paths.index_file().unlink()

    def fails(homes, **more):
        raise sqlite3.OperationalError("the index can't be written")

    monkeypatch.setattr(index, "rebuild", fails)

    assert data(capsys, "--waiting") == expected
    assert not paths.index_file().exists()


def _home(project):
    from moxi_rounds import folders
    return folders.home(project)


# -- what can't be listed ----------------------------------------------------------------

def test_a_round_named_without_its_questions_is_refused_with_the_command_that_shows_it(
        rounds, capsys):
    for argv in ([A], [A, "--waiting"], [A, "--closed"], [A, "--json"]):
        assert main(["list", *argv]) == 3
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"run: moxi rounds show {A}" in captured.err


def test_a_project_that_is_not_known_is_refused(rounds, capsys):
    for argv in (["proj-z"], ["proj-z", "--waiting"]):
        assert main(["list", *argv]) == 3
        captured = capsys.readouterr()
        assert captured.out == "" and "proj-z" in captured.err


@pytest.mark.parametrize("argv", [
    ["--waiting", "--closed"], ["--waiting", "--questions"],
    ["proj-a", "--closed", "--questions"], ["--view", "docket"], ["--out", "somewhere"],
    ["--waiting", "--out", "somewhere"], ["--dormant"], ["--numbers"], [""],
    ["proj-a", "proj-b"]])
def test_a_listing_that_cant_be_read_is_a_usage_error(rounds, capsys, argv):
    assert main(["list", *argv]) == 2
    assert capsys.readouterr().out == ""


def test_the_core_command_runs_list(rounds, capsys):
    assert cli.main(["rounds", "list", "--waiting", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["computed"] == NOW


def test_help_says_what_each_listing_is(capsys):
    assert main(["list", "--help"]) == 0

    said = " ".join(capsys.readouterr().out.split())
    for part in ("--waiting", "--closed", "--questions", "--json",
                 "what waits on the operator", "the rounds that have ended"):
        assert part in said
