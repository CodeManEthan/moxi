"""Showing the operator what was recorded: what `rule` prints for whoever recorded a
ruling, `moxi rounds mark --shown` and what it refuses, his read-back of a project's
list, and the list of rulings not yet shown."""

from __future__ import annotations

import pytest

from moxi_rounds import (asking, corrections, folders, index, log, opening, paths, ruling,
                         shown, state)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, NoRound, Refused, Unreadable

ROUND = "proj-a/a-round"
OTHER = "proj-a/b-round"
SAID = "Usage is not calculated in the status line; it may be shown there."
ASKED = "Do agent-read times follow the settings?"
ANSWER = "Prose follows the settings; parsed rows do not."

#: Times a second apart, and one long after them all.
T1, T2, T3, T4, T5 = (f"2026-01-02T03:04:0{second}Z" for second in range(1, 6))
LATER = "2026-01-03T00:00:00Z"


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of the project `proj-a`, with the rounds `a-round` and
    `b-round` opened in it."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    for slug in ("a-round", "b-round"):
        opening.open_round(f"proj-a/{slug}", description="a placeholder round",
                           actor="planner", flow="fix")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds"


def rule(target=ROUND, *, at, text=SAID, record="comms:12", **more):
    return ruling.rule(target, text=text, record=record, actor="planner", at=at, **more)


def read_back(time, *, at=LATER, record="comms:90", **more):
    return ruling.rule("proj-a", item=f"readback={time}", record=record,
                       actor="planner", at=at, **more)


def mark(target, ids, record="comms:20", **more):
    return shown.mark(target, ids, record=record, actor="planner", **more)


def listed(now=LATER):
    """The read-back list of `proj-a`, each ruling as `(holder, id)`."""
    return [(row.holder, row.id) for row in shown.unshown("proj-a", now=now).rows]


def round_log(rounds, slug="a-round"):
    return log.round_log(rounds / slug)


def logs(rounds):
    """Every log file under the rounds folder, with its bytes."""
    return {str(path.relative_to(rounds)): path.read_bytes()
            for path in sorted(rounds.rglob("*.jsonl"))}


def last(path):
    event = log.read(path)[-1]
    return {key: event[key] for key in event if key not in ("v", "n", "at")}


def everything(tmp_path):
    """Every file under the test's folder, with its bytes and the time it was last
    written."""
    return {str(path.relative_to(tmp_path)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(tmp_path.rglob("*")) if path.is_file()}


# -- what rule prints --------------------------------------------------------------------

def test_a_ruling_is_printed_as_recorded_with_the_mark_that_says_he_saw_it(
        rounds, capsys):
    said = "  Two  spaces; a tab\there, and “quotes”.  "

    assert main(["rule", ROUND, "--text", said, "--record", "comms:12"]) == 0

    [recorded] = [event for event in log.read(round_log(rounds))
                  if event["type"] == "ruling.recorded"]
    assert capsys.readouterr().out == (
        f"Recorded as C1: {recorded['text']}\n"
        'show him this under "Recorded as" in your next message, then:\n'
        f"  moxi rounds mark {ROUND} C1 --shown --record <pointer to that message>\n")
    assert recorded["text"] == said


def test_a_register_entry_is_named_by_its_project_wherever_it_was_recorded_from(
        rounds, capsys):
    assert main(["rule", ROUND, "--standing", "--text", SAID, "--record",
                 "comms:12"]) == 0
    assert main(["rule", "proj-a", "--text", SAID, "--record", "comms:13"]) == 0

    out = capsys.readouterr().out
    assert "  moxi rounds mark proj-a S1 --shown --record <pointer" in out
    assert "  moxi rounds mark proj-a S2 --shown --record <pointer" in out
    assert ROUND not in out


def test_an_answer_is_printed_with_the_sentence_of_its_question(rounds, capsys):
    asking.ask(ROUND, ASKED, origin="comms:11", actor="scout")
    capsys.readouterr()

    assert main(["rule", ROUND, "Q1", "--text", ANSWER, "--record", "comms:12"]) == 0

    assert capsys.readouterr().out.splitlines()[:2] == [
        f"Recorded as C1: {ANSWER}", f"in answer to Q1: {ASKED}"]


def test_a_correction_is_printed_the_same_way_and_is_not_shown(rounds, capsys):
    rule(at=T1)
    mark(ROUND, "C1")
    capsys.readouterr()

    assert main(["rule", ROUND, "--text", "Said better.", "--record", "comms:30",
                 "--supersedes", "C1"]) == 0

    assert capsys.readouterr().out == (
        "Recorded as C2: Said better.\n"
        'show him this under "Recorded as" in your next message, then:\n'
        f"  moxi rounds mark {ROUND} C2 --shown --record <pointer to that message>\n")
    found = shown.unshown("proj-a", now="9999-01-01T00:00:00Z").rows
    assert [(row.id, row.supersedes) for row in found] == [("C2", "C1")]


# -- the list ----------------------------------------------------------------------------

def test_the_list_is_every_ruling_of_the_register_and_the_rounds_not_yet_shown(rounds):
    rule(at=T1)
    rule("proj-a", at=T2, text="A standing one.", record="https://example.com/said")
    rule(OTHER, at=T3, text="Of the other round.", record="comms:14")

    found = shown.unshown("proj-a", now=LATER)

    assert found.project == "proj-a"
    assert found.rows == (
        shown.Row(holder=ROUND, id="C1", text=SAID, rules=None, record="comms:12",
                  supersedes=None, revokes=None, at=T1),
        shown.Row(holder="proj-a", id="S1", text="A standing one.", rules=None,
                  record="https://example.com/said", supersedes=None, revokes=None,
                  at=T2),
        shown.Row(holder=OTHER, id="C1", text="Of the other round.", rules=None,
                  record="comms:14", supersedes=None, revokes=None, at=T3))
    assert found.through == T3
    assert found.heading == f"not yet shown, through {T3}"
    assert found.unreadable == ()


def test_a_project_with_nothing_unshown_has_an_empty_list_with_no_heading(rounds):
    found = shown.unshown("proj-a", now=LATER)

    assert (found.rows, found.through, found.heading) == ((), None, None)


def test_a_row_says_what_its_ruling_rules_replaces_and_takes_back(rounds):
    asking.ask(ROUND, ASKED, origin="comms:11", actor="scout")
    rule(at=T1, item="Q1", text=ANSWER)
    rule(at=T2, item="Q1", text="Everything follows them.", supersedes="C1")
    rule(at=T3, item="revoke:C2", text=None)

    rows = shown.unshown("proj-a", now=LATER).rows

    assert [(row.id, row.rules, row.text, row.supersedes, row.revokes)
            for row in rows] == [
        ("C1", "Q1", ANSWER, None, None),
        ("C2", "Q1", "Everything follows them.", "C1", None),
        ("C3", "revoke:C2", None, None, "C2")]


def test_a_ruling_is_on_the_list_until_a_mark_names_it(rounds):
    rule(at=T1)
    rule(at=T2, text="A second.")
    rule("proj-a", at=T3)
    assert listed() == [(ROUND, "C1"), (ROUND, "C2"), ("proj-a", "S1")]

    mark(ROUND, "C2")
    assert listed() == [(ROUND, "C1"), ("proj-a", "S1")]

    mark("proj-a", "S1")
    assert listed() == [(ROUND, "C1")]
    assert shown.unshown("proj-a", now=LATER).through == T1


def test_a_ruling_is_on_the_list_until_a_read_back_covers_its_time(rounds):
    rule(at=T1)
    rule(OTHER, at=T2)
    rule("proj-a", at=T3)
    rule(at=T4, text="After what he read.")

    read_back(T3)

    assert listed() == [(ROUND, "C2")]


def test_a_ruling_recorded_in_the_second_the_list_is_computed_is_left_for_the_next(
        rounds):
    rule(at=T1)
    rule(at=T2, text="In this very second.")

    now = shown.unshown("proj-a", now=T2)
    later = shown.unshown("proj-a", now=T3)

    assert ([row.id for row in now.rows], now.through) == (["C1"], T1)
    assert ([row.id for row in later.rows], later.through) == (["C1", "C2"], T2)


def test_a_list_computed_now_leaves_out_what_was_recorded_this_second(
        rounds, monkeypatch):
    rule(at=T1)
    rule(at=T2, text="Just now.")
    monkeypatch.setattr(log, "stamp", lambda when_s=None: T2)

    assert [row.id for row in shown.unshown("proj-a").rows] == ["C1"]


def test_a_list_computed_twice_is_the_same_and_changes_no_file(rounds, tmp_path):
    rule(at=T1)
    rule("proj-a", at=T2)
    rule(OTHER, at=T3)
    mark(OTHER, "C1")
    before = everything(tmp_path)

    first = shown.unshown("proj-a", now=LATER)
    second = shown.unshown("proj-a", now=LATER)

    assert first == second
    assert [row.id for row in first.rows] == ["C1", "S1"]
    assert everything(tmp_path) == before
    # A read sets no mark: what the logs fold to is as it was.
    assert [one.shown for one in state.fold(log.read(round_log(rounds))).rulings] == [
        None]


def test_the_rulings_of_a_round_that_cant_be_read_are_left_out_and_the_round_named(
        rounds):
    rule(at=T1)
    rule(OTHER, at=T2)
    with round_log(rounds, "b-round").open("ab") as file:
        file.write(b"not an event\n")

    found = shown.unshown("proj-a", now=LATER)

    assert [(row.holder, row.id) for row in found.rows] == [(ROUND, "C1")]
    [error] = found.unreadable
    assert isinstance(error, Unreadable) and "b-round" in error.reason


def test_a_ruling_is_shown_by_a_mark_or_by_a_read_back_of_its_time(rounds):
    rule(at=T1)
    rule(at=T2, text="A second.")
    rule(at=T4, text="A third.")
    mark(ROUND, "C3")
    read_back(T1)

    project = state.fold(log.read(log.project_log(rounds)))
    first, second, third = state.fold(log.read(round_log(rounds))).rulings

    assert shown.through(project) == T1
    assert [shown.is_shown(one, project) for one in (first, second, third)] == [
        True, False, True]
    assert (first.shown, third.shown) == (None, "comms:20")
    assert not shown.is_shown(second, state.State())


# -- the mark ----------------------------------------------------------------------------

def test_a_mark_is_one_event_in_the_rounds_log_that_names_the_rulings(rounds, capsys):
    rule(at=T1)
    rule(at=T2, text="A second.")
    rule(at=T3, text="A third.")
    project = logs(rounds).get("project.jsonl")

    assert main(["mark", ROUND, "C1,C3", "--shown", "--record", "comms:20", "--as",
                 "planner"]) == 0

    assert last(round_log(rounds)) == {"actor": "planner", "type": "ruling.shown",
                                       "ids": ["C1", "C3"], "record": "comms:20"}
    assert logs(rounds).get("project.jsonl") == project
    assert [one.shown for one in state.fold(log.read(round_log(rounds))).rulings] == [
        "comms:20", None, "comms:20"]
    assert capsys.readouterr().out == f"{ROUND} C1, C3: shown   record: comms:20\n"


def test_a_mark_of_register_entries_is_in_the_projects_log(rounds, capsys):
    rule("proj-a", at=T1)
    rule(ROUND, at=T2, standing=True, text="From a round.")
    before = round_log(rounds).read_bytes()

    assert main(["mark", "proj-a", "S1,S2", "--shown", "--record",
                 "https://example.com/page", "--as", "planner"]) == 0

    assert last(log.project_log(rounds)) == {
        "actor": "planner", "type": "ruling.shown", "ids": ["S1", "S2"],
        "record": "https://example.com/page"}
    assert round_log(rounds).read_bytes() == before
    assert capsys.readouterr().out == (
        "proj-a S1, S2: shown   record: https://example.com/page\n")
    assert listed() == []


@pytest.mark.parametrize("pointer", ["comms:13", "comms:4000",
                                     "https://example.com/page#recorded"])
def test_a_message_after_the_ruling_or_a_page_is_a_showing(rounds, pointer):
    rule(at=T1, record="comms:12")

    assert mark(ROUND, "C1", record=pointer).marked == ("C1",)


def test_a_rulings_own_message_written_with_zeros_is_no_showing_of_it(rounds):
    rule(at=T1, record="comms:012")

    with pytest.raises(Refused, match="comms:12 is the pointer of C1 itself"):
        mark(ROUND, "C1", record="comms:12")
    assert mark(ROUND, "C1", record="comms:013").marked == ("C1",)


def test_a_message_numbered_lower_than_a_page_or_session_ruling_is_not_compared(rounds):
    rule(at=T1, record="https://example.com/said")

    assert mark(ROUND, "C1", record="comms:1").marked == ("C1",)


@pytest.mark.parametrize("target, ids, pointer, says, fix", [
    (ROUND, "C1", None, "a mark needs a pointer to where he was shown the sentence",
     f"moxi rounds mark {ROUND} C1 --shown --record PTR"),
    (ROUND, "C1", "not-a-pointer", "'not-a-pointer' is not a pointer to where he was "
     "shown", f"moxi rounds mark {ROUND} C1 --shown --record PTR"),
    (ROUND, "C1", "file:design.md#rulings", "a file is not a showing",
     f"moxi rounds mark {ROUND} C1 --shown --record PTR"),
    (ROUND, "C1", "comms:12", "comms:12 is the pointer of C1 itself",
     f"moxi rounds mark {ROUND} C1 --shown --record PTR"),
    (ROUND, "C1", "comms:012", "comms:012 is the pointer of C1 itself",
     f"moxi rounds mark {ROUND} C1 --shown --record PTR"),
    (ROUND, "C1,C2", "comms:00040", "comms:00040 is the pointer of C2 itself",
     f"moxi rounds mark {ROUND} C1,C2 --shown --record PTR"),
    (ROUND, "C1", "comms:11", "comms:11 came before comms:12, where he said C1",
     f"moxi rounds mark {ROUND} C1 --shown --record PTR"),
    (ROUND, "C1,C2", "comms:13", "comms:13 came before comms:40, where he said C2",
     f"moxi rounds mark {ROUND} C1,C2 --shown --record PTR"),
    (ROUND, "C1,C2", "comms:40", "comms:40 is the pointer of C2 itself",
     f"moxi rounds mark {ROUND} C1,C2 --shown --record PTR"),
    (ROUND, "S1", "comms:50", f"S1 is of the project's register, and {ROUND} is a "
     "round", "moxi rounds mark proj-a S1 --shown --record PTR"),
    (ROUND, "C1,S1", "comms:50", "a register entry is named by its project",
     "moxi rounds mark proj-a S1 --shown --record PTR"),
    ("proj-a", "C1", "comms:50", "C1 is a round's own ruling, and proj-a is a project",
     "moxi rounds mark proj-a/<slug> C1 --shown --record PTR"),
    ("proj-a", "S1,C2", "comms:50", "a ruling is named by the round that holds it",
     "moxi rounds mark proj-a/<slug> C2 --shown --record PTR"),
    (ROUND, "C1,C9", "comms:50", f"{ROUND} has no ruling C9",
     f"moxi rounds show {ROUND}"),
    ("proj-a", "S9", "comms:50", "proj-a has no ruling S9",
     "moxi rounds list proj-a --waiting"),
])
def test_a_mark_that_cant_be_a_showing_is_refused_and_marks_none_of_its_rulings(
        rounds, capsys, target, ids, pointer, says, fix):
    rule(at=T1, record="comms:12")
    rule(at=T2, record="comms:40", text="A second.")
    rule("proj-a", at=T3, record="comms:12")
    before = logs(rounds)
    record = [] if pointer is None else ["--record", pointer]

    assert main(["mark", target, ids, "--shown", *record]) == 3

    out, err = capsys.readouterr()
    assert out == "" and says in err and f"run: {fix}" in err
    assert logs(rounds) == before
    assert listed() == [(ROUND, "C1"), (ROUND, "C2"), ("proj-a", "S1")]


def test_a_refusal_lists_everything_that_is_wrong_with_the_call(rounds):
    rule(at=T1, record="comms:12")

    with pytest.raises(Refused) as refused:
        mark(ROUND, "C1,C7,S2", record="file:notes.md#shown")

    said = str(refused.value)
    for part in ("S2 is of the project's register", "has no ruling C7",
                 "a file is not a showing"):
        assert part in said


def test_marking_a_ruling_already_shown_writes_nothing_and_says_so(rounds, capsys):
    rule(at=T1)
    mark(ROUND, "C1", record="comms:20")
    before = logs(rounds)

    assert main(["mark", ROUND, "C1", "--shown", "--record", "comms:21"]) == 0

    assert logs(rounds) == before
    assert capsys.readouterr().out == (
        f"{ROUND} C1: shown already; nothing is written\n")
    [found] = state.fold(log.read(round_log(rounds))).rulings
    assert found.shown == "comms:20"


def test_a_mark_names_only_the_rulings_that_were_not_shown_yet(rounds, capsys):
    rule(at=T1)
    rule(at=T2, text="A second.")
    mark(ROUND, "C1")

    assert main(["mark", ROUND, "C1,C2", "--shown", "--record", "comms:21"]) == 0

    assert last(round_log(rounds))["ids"] == ["C2"]
    assert capsys.readouterr().out == (
        f"{ROUND} C2: shown   record: comms:21\n"
        f"{ROUND} C1: shown already; nothing is written for it\n")


def test_a_ruling_his_read_back_covers_takes_no_mark(rounds):
    rule(at=T1)
    read_back(T1)
    before = logs(rounds)

    marked = mark(ROUND, "C1")

    assert (marked.marked, marked.already, marked.event) == ((), ("C1",), None)
    assert logs(rounds) == before


def test_a_mark_stays_when_its_ruling_is_replaced_or_taken_back(rounds):
    rule(at=T1)
    rule("proj-a", at=T1)
    mark(ROUND, "C1")
    mark("proj-a", "S1")

    rule(at=T2, text="Said better.", supersedes="C1")
    rule("proj-a", at=T3, item="revoke:S1", text=None)

    first = state.fold(log.read(round_log(rounds))).rulings[0]
    entry = state.fold(log.read(log.project_log(rounds))).rulings[0]
    assert (first.shown, first.replaced_by) == ("comms:20", "C2")
    assert (entry.shown, entry.revoked_by) == ("comms:20", "S2")
    assert listed() == [(ROUND, "C2"), ("proj-a", "S2")]


def test_a_read_back_is_his_own_word_and_takes_no_mark(rounds):
    rule(at=T1)
    read_back(T1)
    before = logs(rounds)

    with pytest.raises(Refused, match="S1 is a read-back"):
        mark("proj-a", "S1")

    assert logs(rounds) == before


@pytest.mark.parametrize("argv", [
    [ROUND, "C1,", "--shown"], [ROUND, "c1", "--shown"], [ROUND, "Q1", "--shown"],
    [ROUND, "C1,C1", "--shown"], [ROUND, "C0", "--shown"], ["proj-a", "", "--shown"],
    ["proj-a/Not-A-Slug", "C1", "--shown"],
    [ROUND, "C1", "--shown", "--reopen"], [ROUND, "C1", "--shown", "--blocks", "all"],
    [ROUND, "Q1", "--reopen", "--record", "comms:20"],
    [ROUND, "Q1", "--blocks", "all", "--record", "comms:20"],
])
def test_a_mark_that_cant_be_read_is_a_usage_error(rounds, capsys, argv):
    rule(at=T1)
    before = logs(rounds)

    pointer = [] if "--record" in argv else ["--record", "comms:20"]

    assert main(["mark", *argv, *pointer]) == 2

    assert capsys.readouterr().out == ""
    assert logs(rounds) == before


def test_a_mark_on_a_round_that_was_never_opened_names_open(rounds):
    with pytest.raises(NoRound):
        mark("proj-a/c-round", "C1")


def test_help_says_how_rulings_are_marked_as_shown(capsys):
    assert main(["mark", "--help"]) == 0

    out = " ".join(capsys.readouterr().out.split())
    for word in ("--shown", "--record PTR", "ROUND|PROJECT", "S<n> of a project"):
        assert word in out


# -- the read-back -----------------------------------------------------------------------

def test_a_read_back_is_a_ruling_of_the_project_and_an_act_on_the_process(
        rounds, capsys):
    rule(at=T1)
    before = round_log(rounds).read_bytes()

    assert main(["rule", "proj-a", f"readback={T1}", "--record",
                 "https://example.com/page", "--as", "planner"]) == 0

    assert last(log.project_log(rounds)) == {
        "actor": "planner", "type": "ruling.recorded", "id": "S1",
        "record": "https://example.com/page", "kind": "process",
        "rules": f"readback={T1}"}
    assert round_log(rounds).read_bytes() == before
    assert capsys.readouterr().out == (
        f"Recorded as S1: readback={T1}\n"
        f"it covers every ruling of proj-a recorded at or before {T1}; a read-back "
        "is his own word, and is not itself shown to him\n")
    assert listed() == []


def test_a_read_back_is_never_on_the_list(rounds):
    rule(at=T1)
    read_back(T1, at=T2)
    rule(at=T3, text="After it.")

    assert listed() == [(ROUND, "C2")]
    assert shown.unshown("proj-a", now=LATER).through == T3


@pytest.mark.parametrize("target", [ROUND, OTHER, "proj-a"])
def test_a_read_back_takes_the_time_of_any_ruling_of_the_project(rounds, target):
    rule(target, at=T2)

    assert read_back(T2).event["rules"] == f"readback={T2}"


def test_a_read_back_through_a_time_that_is_no_rulings_is_refused(rounds, capsys):
    rule(at=T1)
    rule(at=T3, text="A second.")
    before = logs(rounds)

    assert main(["rule", "proj-a", f"readback={T2}", "--record", "comms:90"]) == 3

    out, err = capsys.readouterr()
    assert out == "" and f"no ruling of proj-a was recorded at {T2}" in err
    assert "run: moxi rounds rule proj-a readback=TIME --record PTR" in err
    assert logs(rounds) == before


def test_a_read_back_through_the_second_it_is_recorded_in_is_refused(rounds):
    rule(at=T1)
    before = logs(rounds)

    with pytest.raises(Refused, match=f"{T1} is not yet past"):
        read_back(T1, at=T1)

    assert logs(rounds) == before
    assert read_back(T1, at=T2).id == "S1"


def test_a_read_back_through_the_present_second_is_refused_by_the_command(
        rounds, capsys, monkeypatch):
    rule(at=T2)
    monkeypatch.setattr(log, "stamp", lambda when_s=None: T2)

    assert main(["rule", "proj-a", f"readback={T2}", "--record", "comms:90"]) == 3

    assert f"{T2} is not yet past" in capsys.readouterr().err


def test_a_read_back_of_a_time_no_ruling_has_yet_names_both_things_wrong(rounds):
    rule(at=T1)

    with pytest.raises(Refused) as refused:
        read_back(T5, at=T2)

    assert "no ruling of proj-a was recorded" in str(refused.value)
    assert "is not yet past" in str(refused.value)


def test_the_time_of_a_read_back_is_not_a_time_a_list_ended_at(rounds):
    rule(at=T1)
    read_back(T1, at=T3)

    with pytest.raises(Refused, match=f"no ruling of proj-a was recorded at {T3}"):
        read_back(T3)


def test_a_read_back_at_the_time_of_a_ruling_already_marked_shown_is_accepted(rounds):
    rule(at=T1)
    rule(OTHER, at=T2)
    mark(OTHER, "C1")
    assert shown.unshown("proj-a", now=LATER).through == T1

    read_back(T2)

    assert listed() == []


def test_a_read_back_covers_no_ruling_recorded_after_its_time(rounds):
    rule(at=T1)
    rule(at=T2, text="A second.")
    rule("proj-a", at=T3)

    read_back(T1, at=T4)
    rule(OTHER, at=T5)

    assert listed() == [(ROUND, "C2"), ("proj-a", "S1"), (OTHER, "C1")]


def test_a_second_read_back_of_the_same_time_or_a_later_one_is_a_ruling_of_its_own(
        rounds):
    rule(at=T1)
    rule(at=T2, text="A second.")
    rule(at=T3, text="A third.")

    ids = [read_back(T1, record="comms:90").id, read_back(T1, record="comms:91").id]
    assert listed() == [(ROUND, "C2"), (ROUND, "C3")]
    ids.append(read_back(T2, record="comms:92").id)

    assert ids == ["S1", "S2", "S3"]
    assert listed() == [(ROUND, "C3")]
    register = state.fold(log.read(log.project_log(rounds))).rulings
    assert all(corrections.stands(one) and one.supersedes is None for one in register)


def test_an_earlier_time_read_back_later_uncovers_nothing(rounds):
    rule(at=T1)
    rule(at=T2, text="A second.")
    rule(at=T3, text="A third.")
    read_back(T2)

    read_back(T1)

    assert listed() == [(ROUND, "C3")]
    assert shown.through(state.fold(log.read(log.project_log(rounds)))) == T2


def test_a_read_back_needs_his_pointer(rounds):
    rule(at=T1)
    before = logs(rounds)

    with pytest.raises(Refused, match="needs a pointer to where he said it"):
        read_back(T1, record=None)

    assert logs(rounds) == before


def test_a_read_back_is_neither_taken_back_nor_replaced(rounds, capsys):
    rule(at=T1)
    rule(at=T2, text="A second.")
    read_back(T1)
    before = logs(rounds)

    assert main(["rule", "proj-a", "revoke:S1", "--record", "comms:95"]) == 3
    err = capsys.readouterr().err
    assert "a read-back is neither taken back nor replaced" in err
    assert "run: moxi rounds rule proj-a readback=TIME --record PTR" in err
    with pytest.raises(Refused, match="is not ruled a second time"):
        rule("proj-a", at=LATER, supersedes="S1")
    with pytest.raises(BadArgument, match="a read-back replaces none before it"):
        read_back(T2, supersedes="S1")

    assert logs(rounds) == before
    assert corrections.kind_of(f"readback={T1}").name == "a read-back"


@pytest.mark.parametrize("target, item, flags", [
    (ROUND, f"readback={T1}", []), (ROUND, f"readback={T1}", ["--standing"]),
    ("proj-a", "readback=", []), ("proj-a", "readback=yesterday", []),
    ("proj-a", "readback=2026-01-02T03:04Z", []),
    ("proj-a", "readback=2026-01-02T03:04:01", []),
    ("proj-a", f"readback={T1}", ["--scope", "proj-a"]),
    ("proj-a", f"readback={T1}", ["--text", " "]),
    ("proj-a", f"readback={T1}", ["--supersedes", "S1"]),
    ("proj-a", f"readback={T1}", ["--chose", "a"]),
    ("proj-a", f"readback={T1}", ["--until", "B1"]),
])
def test_a_read_back_that_cant_be_read_is_a_usage_error(rounds, capsys, target, item,
                                                        flags):
    rule(at=T1)
    rule("proj-a", at=T1)
    before = logs(rounds)

    assert main(["rule", target, item, "--record", "comms:90", *flags]) == 2

    assert capsys.readouterr().out == ""
    assert logs(rounds) == before


def test_help_says_how_a_read_back_is_recorded(capsys):
    assert main(["rule", "--help"]) == 0

    out = " ".join(capsys.readouterr().out.split())
    for word in ("readback=TIME", "not yet shown"):
        assert word in out


# -- the list, from the index ------------------------------------------------------------

def test_the_index_keeps_the_rulings_no_mark_names_and_the_read_backs(rounds):
    rule(at=T1)
    rule(at=T2, record="https://example.test/said")
    rule("proj-a", at=T3)
    mark(ROUND, "C1")
    read_back(T1)

    kept = index.read("proj-a", rounds, "a-round")
    assert kept.of(index.RULING) == (index.Item(index.RULING, "C2", {
        "text": SAID, "rules": None, "record": "https://example.test/said",
        "supersedes": None, "revokes": None, "at": T2}),)
    register = index.read("proj-a", rounds)
    # A ruling his read-back covers has no mark, so it stays a row; a read-back is
    # a row of its own kind and never one of the rulings.
    assert [item.id for item in register.of(index.RULING)] == ["S1"]
    assert register.of(index.READ_BACK) == (
        index.Item(index.READ_BACK, "S2", {"through": T1}),)


def test_the_list_is_the_same_from_the_index_from_a_stale_one_and_from_none(rounds):
    rule(at=T1)
    rule(OTHER, at=T2)
    rule("proj-a", at=T3)
    mark(OTHER, "C1")
    read_back(T1)
    home = folders.home("proj-a")
    index.rebuild([home])
    assert index.disagreements([home]) == []
    fresh = shown.unshown("proj-a", now=LATER)
    assert [(row.holder, row.id) for row in fresh.rows] == [("proj-a", "S1")]

    # A ruling the index has not seen, appended as another machine's verb would.
    log.append(round_log(rounds), "ruling.recorded",
               {"id": "C2", "text": SAID, "record": "comms:13", "kind": "substance",
                "stages": "all"}, actor="pilot", at=T4)
    assert index.disagreements([home]) != []
    stale = shown.unshown("proj-a", now=LATER)
    assert [(row.holder, row.id) for row in stale.rows] == [
        ("proj-a", "S1"), (ROUND, "C2")]

    paths.index_file().unlink()
    assert shown.unshown("proj-a", now=LATER) == stale
    assert not paths.index_file().exists()
