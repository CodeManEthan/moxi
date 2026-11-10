"""`moxi rounds drop`: who may drop a round, where its questions go, what a dropped
round still takes, what a call that was stopped half way leaves for the next, and what
the opening of a later round prints."""

from __future__ import annotations

import json

import pytest
from moxi import cli

from moxi_rounds import (asking, dropping, holds, index, log, marking, moving, opening,
                         questions, ruling, state, write)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Locked, NoRound, Refused

A = "proj-a/a-round"
B = "proj-a/b-round"
SAID = "Do agent-read times follow the settings?"
OTHER = "Is the status line the place for usage?"
THIRD = "Does a parsed row keep its own format?"
RULED = "Usage is not calculated in the status line; it may be shown there."
OPENED_AT = "2026-01-02T03:04:01Z"
ASKED_AT = "2026-01-02T03:04:05Z"
DROPPED_AT = "2026-02-03T04:05:06Z"


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of the project `proj-a`, with the round `a-round` opened in
    it, with the stages B1 and B2."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    folder = tmp_path / "notes" / "rounds"
    opening.open_round(A, description="a placeholder round", actor="planner",
                       flow="fix", at=OPENED_AT)
    for stage in ("B1", "B2"):
        append(folder, "stage.declared", {"id": stage, "repo": "proj-a",
                                          "branch": f"branch-{stage.lower()}"})
    capsys.readouterr()
    return folder


def round_log(rounds, slug="a-round"):
    return log.round_log(rounds / slug)


def project_log(rounds):
    return log.project_log(rounds)


def append(rounds, kind, fields, slug="a-round"):
    """Append an event of a later kind of record, as the verb that records it will."""
    return log.append(round_log(rounds, slug), kind, fields, actor="coder")


def logs(rounds):
    """Every log file under the rounds folder, with its bytes."""
    return {str(path.relative_to(rounds)): path.read_bytes()
            for path in sorted(rounds.rglob("*.jsonl"))}


def ask(text=SAID, target=A, blocks=None, **more):
    """Ask a question of `target`, by `scout` at `ASKED_AT`, and return its id."""
    more.setdefault("origin", None if "record" in more else "comms:11")
    return asking.ask(target, text, blocks=blocks, actor="scout", at=ASKED_AT,
                      **more).id


def rule(target=A, **more):
    more.setdefault("record", "comms:12")
    return ruling.rule(target, actor="planner", **more)


def drop(name=A, by="operator", record="comms:30", **more):
    more.setdefault("at", DROPPED_AT)
    return dropping.drop(name, by=by, record=record, actor="planner", **more)


def held(path):
    return state.fold(log.read(path))


def count(path):
    return len(log.read(path)) if log.exists(path) else 0


def added(path, before):
    """The events a log gained since it held `before` of them, without what the log
    itself sets."""
    return [{key: event[key] for key in event if key not in ("v", "n")}
            for event in log.read(path)[before:]]


# -- the operator's drop -----------------------------------------------------------------

def test_the_operator_drops_a_round_that_holds_his_rulings(rounds, capsys):
    rule(text=RULED)
    before = count(round_log(rounds))

    assert main(["drop", A, "--by", "operator", "--record", "comms:30",
                 "--as", "curator"]) == 0

    [event] = added(round_log(rounds), before)
    assert event == {"actor": "curator", "type": "round.dropped", "at": event["at"],
                     "by": "operator", "record": "comms:30", "questions": {}}
    found = held(round_log(rounds))
    assert found.status == state.DROPPED
    assert found.ended == state.Ended(how="dropped", by="operator", record="comms:30",
                                      actor="curator", at=event["at"])
    assert not log.exists(project_log(rounds))
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        f"dropped {A}   by: operator   record: comms:30",
        "it had no open or parked question"]
    assert err == ""


def test_the_real_command_drops_a_round(rounds, capsys):
    assert cli.main(["rounds", "drop", A, "--by", "operator", "--record",
                     "comms:30"]) == 0

    assert held(round_log(rounds)).status == "dropped"
    assert capsys.readouterr().out.startswith(f"dropped {A} ")


def test_help_says_what_drop_does(capsys):
    assert main(["drop", "--help"]) == 0

    out = " ".join(capsys.readouterr().out.split())
    for word in ("ROUND", "--by {lead,operator}", "--record PTR",
                 "--withdraw-questions", "--as NAME", "holds none of the operator's"):
        assert word in out


# -- where the questions go --------------------------------------------------------------

def test_open_and_parked_questions_go_to_the_projects_list_as_they_are(rounds, capsys):
    ask(SAID)
    ask(OTHER, blocks="B1")
    ask(THIRD, blocks="none", options=["a=prose only", "b=every row"])
    ask("Is a ruled question left where it is?")
    rule(item="park:Q2")
    rule(item="Q4", text="It is.")
    before = count(round_log(rounds))

    assert main(["drop", A, "--by", "operator", "--record", "comms:30"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"dropped {A}   by: operator   record: comms:30",
        "questions now on the list of proj-a: Q1 as P1, Q2 as P2, Q3 as P3"]
    [event] = added(round_log(rounds), before)
    assert event["questions"] == {"Q1": "P1", "Q2": "P2", "Q3": "P3"}
    assert [one["type"] for one in log.read(project_log(rounds))] == [
        "question.asked"] * 3
    listed = {one.id: one for one in held(project_log(rounds)).questions}
    assert [(one.id, one.text, one.state, one.blocks, one.held, one.moved_from,
             one.origin, one.by, one.at) for one in listed.values()] == [
        ("P1", SAID, "open", "none", "all", f"{A}:Q1", "comms:11", "scout", ASKED_AT),
        ("P2", OTHER, "parked", "none", ("B1",), f"{A}:Q2", "comms:11", "scout",
         ASKED_AT),
        ("P3", THIRD, "open", "none", "none", f"{A}:Q3", "comms:11", "scout", ASKED_AT)]
    assert listed["P3"].options == (("a", "prose only"), ("b", "every row"))

    after = held(round_log(rounds))
    assert [(one.id, one.state, one.moved_to) for one in after.questions] == [
        ("Q1", "moved", "P1"), ("Q2", "moved", "P2"), ("Q3", "moved", "P3"),
        ("Q4", "ruled", None)]
    assert questions.find(after, "Q1").changes[-1].record == "comms:30"
    # They hold nothing, where they were and where they are.
    assert holds.on_stage(after, "B1") == () and holds.on_close(after) == ()
    assert holds.on_stage(held(project_log(rounds)), "B1") == ()


def test_a_drop_prints_ids_and_is_no_listing(rounds, capsys):
    ask()

    assert main(["drop", A, "--by", "operator", "--record", "comms:30"]) == 0

    out = capsys.readouterr().out
    assert SAID not in out and "scout" not in out and ASKED_AT not in out


def test_the_questions_wait_on_the_projects_list_afterwards(rounds, capsys):
    ask()
    drop()

    assert main(["list", "proj-a", "--waiting", "--json"]) == 0
    [entry] = json.loads(capsys.readouterr().out)["open"]
    assert (entry["holder"], entry["id"], entry["moved_from"], entry["held"],
            entry["by"], entry["at"]) == ("proj-a", "P1", f"{A}:Q1", "all", "scout",
                                          ASKED_AT)
    waiting = index.read("proj-a", rounds).of("question")
    assert [item.id for item in waiting] == ["P1"]
    assert index.read("proj-a", rounds, "a-round").of("question") == ()
    assert "- P1: " + SAID in (rounds / "questions.md").read_text(encoding="utf-8")


def test_the_operator_may_have_the_questions_withdrawn(rounds, capsys):
    ask(SAID)
    ask(OTHER)
    ask(THIRD)
    rule(item="park:Q2")
    rule(item="Q3", text="It does.")
    before = count(round_log(rounds))

    assert main(["drop", A, "--by", "operator", "--record", "comms:30",
                 "--withdraw-questions"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"dropped {A}   by: operator   record: comms:30",
        "questions withdrawn: Q1, Q2"]
    [event] = added(round_log(rounds), before)
    assert event["questions"] == {"Q1": "withdrawn", "Q2": "withdrawn"}
    after = held(round_log(rounds))
    assert [(one.id, one.state) for one in after.questions] == [
        ("Q1", "withdrawn"), ("Q2", "withdrawn"), ("Q3", "ruled")]
    assert questions.find(after, "Q2").changes[-1].record == "comms:30"
    assert not log.exists(project_log(rounds))


# -- the lead's drop ---------------------------------------------------------------------

def test_the_lead_drops_a_round_that_holds_nothing_of_the_operators(rounds, capsys):
    ask()
    marking.widen(A, "Q1", "all", actor="planner")

    assert main(["drop", A, "--by", "lead", "--record", "comms:31"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"dropped {A}   by: lead   record: comms:31",
        "questions now on the list of proj-a: Q1 as P1"]
    assert held(round_log(rounds)).ended.by == "lead"


def a_ruling(rounds):
    rule(text=RULED)
    return ["his ruling C1"]


def a_ruling_since_taken_back(rounds):
    rule(text=RULED)
    rule(item="revoke:C1")
    return ["his rulings C1, C2"]


def a_skip(rounds):
    append(rounds, "ruling.recorded", {"id": "C1", "record": "comms:12",
                                       "kind": "process", "rules": "skip:docket:B1",
                                       "covers": []})
    return ["his skip C1"]


def his_own_question(rounds):
    ask(record="comms:14")
    return ["Q1, asked with his pointer"]


def a_list_he_gave(rounds):
    ask(target="proj-a")
    moving.adopt(A, "P1", blocks="B1", record="comms:15", actor="planner")
    return ["Q1, adopted with the list he gave (S1 of the project's register)"]


def a_finished_build(rounds):
    append(rounds, "finish.done", {"stage": "B1", "tip": "0" * 40})
    return ["the build of B1, which is finished"]


def a_merge_that_may_have_landed(rounds):
    append(rounds, "finish.action", {"stage": "B2", "tip": "0" * 40, "id": "merge",
                                     "kind": "merge", "state": "started"})
    return ["the build of B2, whose merge may have reached its target"]


def a_decision_that_reached_the_target(rounds):
    append(rounds, "verdict.recorded", {
        "kind": "build", "stage": "B1", "decisions": [
            {"id": "M1", "weight": "major", "text": "a placeholder decision"}]})
    append(rounds, "finish.done", {"stage": "B1", "tip": "0" * 40})
    return ["the build of B1, which is finished",
            "the decision B1.M1, on a build that reached its target"]


def an_open_waiting_item(rounds):
    append(rounds, "verdict.recorded", {
        "kind": "build", "stage": "B2", "waits": [
            {"id": "W1", "text": "a placeholder check"}]})
    return ["the open waiting item B2.W1"]


@pytest.mark.parametrize("holds_his", [
    a_ruling, a_ruling_since_taken_back, a_skip, his_own_question, a_list_he_gave,
    a_finished_build, a_merge_that_may_have_landed, a_decision_that_reached_the_target,
    an_open_waiting_item])
def test_the_lead_is_refused_a_round_that_holds_an_act_of_the_operators(
        rounds, capsys, holds_his):
    said = holds_his(rounds)
    before = logs(rounds)

    assert main(["drop", A, "--by", "lead", "--record", "comms:31"]) == 3

    out, err = capsys.readouterr()
    assert out == ""
    assert "refused, with nothing written" in err
    assert f"It holds: {'; '.join(said)}." in err
    assert f"run: moxi rounds drop {A} --by operator --record PTR" in err
    assert logs(rounds) == before
    # And it is his to drop.
    assert drop().by == "operator"


def test_the_lead_is_refused_a_round_opened_on_the_operators_go(rounds, capsys):
    opening.open_round(B, description="a placeholder round", actor="planner",
                       flow="fix", record="comms:5")
    before = logs(rounds)

    with pytest.raises(Refused) as refused:
        drop(B, by="lead")

    assert "It holds: its opening, on his go (comms:5)." in str(refused.value)
    assert logs(rounds) == before


def test_a_refused_lead_is_told_everything_the_round_holds(rounds):
    rule(text=RULED)
    ask(record="comms:14")
    append(rounds, "finish.done", {"stage": "B1", "tip": "0" * 40})

    with pytest.raises(Refused) as refused:
        drop(by="lead")

    assert ("It holds: his ruling C1; Q1, asked with his pointer; the build of B1, "
            "which is finished.") in str(refused.value)


def a_merge_that_failed(rounds):
    append(rounds, "finish.action", {"stage": "B2", "tip": "0" * 40, "id": "merge",
                                     "kind": "merge", "state": "failed"})


def a_waiting_item_that_is_done(rounds):
    an_open_waiting_item(rounds)
    append(rounds, "wait.marked", {"id": "B2.W1", "state": "done",
                                   "record": "comms:16"})


def a_decision_on_a_build_that_reached_nothing(rounds):
    append(rounds, "verdict.recorded", {
        "kind": "build", "stage": "B1", "decisions": [
            {"id": "M1", "weight": "major", "text": "a placeholder decision"}]})


def a_project_question_adopted_with_no_list(rounds):
    ask(target="proj-a")
    moving.adopt(A, "P1", actor="planner")


@pytest.mark.parametrize("holds_none_of_his", [
    a_merge_that_failed, a_waiting_item_that_is_done,
    a_decision_on_a_build_that_reached_nothing, a_project_question_adopted_with_no_list])
def test_the_lead_drops_a_round_whose_records_are_not_the_operators(
        rounds, holds_none_of_his):
    holds_none_of_his(rounds)

    assert drop(by="lead").by == "lead"
    assert held(round_log(rounds)).status == "dropped"


def test_the_lead_does_not_withdraw_the_questions(rounds, capsys):
    ask()
    before = logs(rounds)

    assert main(["drop", A, "--by", "lead", "--record", "comms:31",
                 "--withdraw-questions"]) == 3

    err = capsys.readouterr().err
    assert "withdrawing a round's questions is the operator's" in err
    assert f"run: moxi rounds drop {A} --by lead --record PTR" in err
    assert logs(rounds) == before


# -- what else is refused ----------------------------------------------------------------

@pytest.mark.parametrize("by", ["lead", "operator"])
def test_a_drop_with_no_pointer_is_refused(rounds, capsys, by):
    before = logs(rounds)

    assert main(["drop", A, "--by", by]) == 3

    err = capsys.readouterr().err
    assert "a drop carries a pointer" in err and "comms:<digits>" in err
    assert f"run: moxi rounds drop {A} --by {by} --record PTR" in err
    assert logs(rounds) == before


@pytest.mark.parametrize("by", ["lead", "operator"])
def test_a_pointer_of_no_known_shape_is_refused(rounds, capsys, by):
    before = logs(rounds)

    assert main(["drop", A, "--by", by, "--record", "nobody wants it"]) == 3

    assert "'nobody wants it'" in capsys.readouterr().err
    assert logs(rounds) == before


def test_a_round_is_dropped_once(rounds, capsys):
    ask()
    drop()
    before = logs(rounds)

    assert main(["drop", A, "--by", "operator", "--record", "comms:32"]) == 3

    err = capsys.readouterr().err
    assert f"{A} was dropped already, and a round ends once" in err
    assert f"run: moxi rounds show {A}" in err
    assert logs(rounds) == before


def test_a_round_that_was_never_opened_is_not_dropped(rounds, capsys):
    assert main(["drop", B, "--by", "operator", "--record", "comms:30"]) == 3

    assert f"there is no round {B}" in capsys.readouterr().err
    with pytest.raises(NoRound):
        drop(B)
    assert not (rounds / "b-round").exists()


@pytest.mark.parametrize("argv", [
    [A], [A, "--by", "checker", "--record", "comms:30"],
    ["proj-a", "--by", "operator", "--record", "comms:30"],
    [A, "B1", "--by", "operator", "--record", "comms:30"],
    ["--by", "operator", "--record", "comms:30"]])
def test_a_call_that_cant_be_read_is_a_usage_error(rounds, capsys, argv):
    before = logs(rounds)

    assert main(["drop", *argv]) == 2

    assert logs(rounds) == before


def test_who_drops_is_one_of_the_two(rounds):
    with pytest.raises(BadArgument):
        drop(by="checker")


@pytest.mark.parametrize("which", ["round", "project"])
def test_a_drop_waits_for_both_locks(rounds, holder, which):
    ask()
    before = logs(rounds)
    path = (write.round_lock("proj-a", "a-round") if which == "round" else
            write.project_lock("proj-a"))
    busy = holder(path)

    with pytest.raises(Locked) as stopped:
        drop(wait_s=0.2)

    assert (stopped.value.file, stopped.value.holder) == (path, busy.pid)
    assert logs(rounds) == before


# -- a dropped round takes no new work ---------------------------------------------------

def dropped_with_a_ruling_and_a_question(rounds):
    ask(SAID)
    ask(OTHER)
    rule(item="Q1", text="They do.", record="comms:12")
    rule(text=RULED, record="comms:13")
    ask(target="proj-a", text=THIRD)
    drop(withdraw_questions=True)


@pytest.mark.parametrize("argv, what", [
    (["ask", A, "A new question?", "--from", "comms:40"], "a new question"),
    (["ask", A, "Q2", "--option", "a=one", "--option", "b=two"],
     "a change to a question's options"),
    (["ask", A, "--adopt", "P1"], "a question"),
    (["ask", "proj-a", "--move", f"{A}:Q2", "--record", "comms:40"],
     "a move of one of its questions"),
    (["mark", A, "Q2", "--blocks", "all"], "a mark"),
    (["mark", A, "Q2", "--reopen"], "a mark"),
    (["rule", A, "--text", "A new ruling.", "--record", "comms:40"], "a new ruling"),
    (["rule", A, "park:Q2", "--record", "comms:40"], "a new ruling"),
    (["rule", A, "Q2", "--text", "An answer.", "--record", "comms:40"],
     "a new ruling")])
def test_a_dropped_round_takes_no_new_work_and_names_a_new_round(
        rounds, capsys, argv, what):
    dropped_with_a_ruling_and_a_question(rounds)
    before = logs(rounds)

    assert main(argv) == 3

    err = capsys.readouterr().err
    assert f"{A} was dropped and stays as it ended: it takes {what} no longer" in err
    assert "new work, in a new round" in err
    assert "run: moxi rounds open proj-a/SLUG --description TEXT" in err
    assert logs(rounds) == before


def test_a_correction_on_a_dropped_round_is_recorded_and_reopens_nothing(
        rounds, capsys):
    dropped_with_a_ruling_and_a_question(rounds)

    assert main(["rule", A, "--text", "Shown there, and only there.", "--record",
                 "comms:42", "--supersedes", "C2"]) == 0
    assert "new work, in a new round" in capsys.readouterr().err
    assert main(["rule", A, "revoke:C1", "--record", "comms:43"]) == 0
    assert f"note: {A} was dropped and stays as it ended" in capsys.readouterr().err

    found = held(round_log(rounds))
    assert found.status == "dropped" and found.ended.at == DROPPED_AT
    assert [(one.id, one.replaced_by, one.revoked_by) for one in found.rulings] == [
        ("C1", None, "C4"), ("C2", "C3", None), ("C3", None, None), ("C4", None, None)]
    # The round is no more open for it: it is in no listing of open rounds, and
    # nothing of it waits.
    assert main(["list", "proj-a"]) == 0
    assert A not in capsys.readouterr().out
    assert main(["list", "proj-a", "--waiting", "--json"]) == 0
    waiting = json.loads(capsys.readouterr().out)
    assert [one["holder"] for one in waiting["open"] + waiting["parked"]] == ["proj-a"]


def test_a_dropped_round_is_dropped_whatever_is_recorded_on_it_later(rounds):
    drop()
    rule(target=A, standing=True, text=RULED)

    assert held(round_log(rounds)).status == "dropped"
    assert index.read("proj-a", rounds, "a-round").status == "dropped"


# -- a drop that was stopped -------------------------------------------------------------

def stopped_after(monkeypatch, appends, call):
    """Run `call`, and stop it once `appends` events are in the logs: before the next
    is appended."""
    append_one = log.append
    done = []

    def some(*args, **more):
        if len(done) == appends:
            raise KeyboardInterrupt
        done.append(append_one(*args, **more))
        return done[-1]

    with monkeypatch.context() as patched:
        patched.setattr(log, "append", some)
        with pytest.raises(KeyboardInterrupt):
            call()
    return done


@pytest.mark.parametrize("appends", [1, 2])
def test_a_drop_stopped_between_the_two_logs_is_finished_by_running_it_again(
        rounds, monkeypatch, appends):
    ask(SAID)
    ask(OTHER)
    before = count(round_log(rounds))

    done = stopped_after(monkeypatch, appends, drop)

    # What was written is on the project's list, and the round is as it was: open,
    # its questions still its own.
    assert [one["type"] for one in done] == ["question.asked"] * appends
    assert count(round_log(rounds)) == before
    assert held(round_log(rounds)).status == "open"
    assert [one.state for one in held(round_log(rounds)).questions] == ["open", "open"]

    again = drop()

    assert again.finished and again.went == (("Q1", "P1"), ("Q2", "P2"))
    assert [one["type"] for one in again.events] == [
        *["question.asked"] * (2 - appends), "round.dropped"]
    assert [(one.id, one.moved_from) for one in held(project_log(rounds)).questions] == [
        ("P1", f"{A}:Q1"), ("P2", f"{A}:Q2")]
    assert count(project_log(rounds)) == 2
    assert count(round_log(rounds)) == before + 1
    assert [one.moved_to for one in held(round_log(rounds)).questions] == ["P1", "P2"]
    with pytest.raises(Refused):
        drop()
    assert count(project_log(rounds)) == 2


def test_a_drop_that_was_not_stopped_is_one_event_in_the_rounds_log(rounds):
    ask(SAID)
    ask(OTHER)
    before = count(round_log(rounds))

    dropped = drop()

    assert not dropped.finished
    assert [one["type"] for one in dropped.events] == [
        "question.asked", "question.asked", "round.dropped"]
    assert count(round_log(rounds)) == before + 1


def test_a_stopped_drop_that_moved_questions_is_not_finished_by_withdrawing_them(
        rounds, monkeypatch, capsys):
    ask(SAID)
    ask(OTHER)
    stopped_after(monkeypatch, 1, drop)
    before = logs(rounds)

    assert main(["drop", A, "--by", "operator", "--record", "comms:30",
                 "--withdraw-questions"]) == 3

    err = capsys.readouterr().err
    assert f"a drop of {A} was stopped after it put Q1 as P1 on the list" in err
    assert f"run: moxi rounds drop {A} --by operator --record PTR\n" in err
    assert logs(rounds) == before


# -- the opening of a later round --------------------------------------------------------

def test_the_next_opening_prints_the_questions_a_dropped_round_left(rounds, capsys):
    ask(SAID)
    ask(OTHER, blocks="B2")
    rule(item="park:Q2")
    drop()

    assert main(["open", B, "--description", "the work taken up again",
                 "--flow", "fix"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"opened {B}   flow fix (build, verify-build, finish)",
        f"folder: {rounds / 'b-round'}",
        "",
        "questions on the list of proj-a that came from a dropped round; adopt the "
        "ones whose work this round takes up:",
        f"  moxi rounds ask {B} --adopt P<n>",
        f"  P1: {SAID}",
        f"    from: comms:11   blocks: none   state: open   by: scout   at: {ASKED_AT}"
        f"   moved from: {A}:Q1   held up there: all",
        f"  P2: {OTHER}",
        f"    from: comms:11   blocks: none   state: parked   by: scout   at: {ASKED_AT}"
        f"   moved from: {A}:Q2   held up there: B2"]


def test_an_opening_prints_only_what_a_dropped_round_left_and_is_still_asked(
        rounds, capsys):
    ask(SAID)                                  # moved out of a round that stays open
    moving.move("proj-a", f"{A}:Q1", record="comms:20", actor="planner")
    ask(OTHER, target="proj-a")                # asked on the list
    opening.open_round(B, description="a placeholder round", actor="planner",
                       flow="fix")
    ask(THIRD, target=B)
    ask("Is a ruled question printed?", target=B)
    drop(B)
    rule("proj-a", item="P4", text="It is not.")

    opened = opening.open_round("proj-a/c-round", description="a placeholder round",
                                actor="planner", flow="fix")

    assert [(one.question.id, one.question.text, one.state) for one in opened.left] == [
        ("P3", THIRD, "open")]

    moving.adopt("proj-a/c-round", "P3", actor="planner")
    later = opening.open_round("proj-a/d-round", description="a placeholder round",
                               actor="planner", flow="fix")
    assert later.left == ()


def test_an_opening_with_nothing_left_by_a_dropped_round_prints_no_more(rounds, capsys):
    ask(target="proj-a")
    drop()

    assert main(["open", B, "--description", "another round", "--flow", "fix"]) == 0

    assert len(capsys.readouterr().out.splitlines()) == 2


def test_a_question_of_a_dropped_round_is_adopted_holding_up_every_stage(rounds):
    ask(blocks="B1")
    drop()
    opening.open_round(B, description="a placeholder round", actor="planner",
                       flow="fix")
    append(rounds, "stage.declared", {"id": "B1", "repo": "proj-a", "branch": "again"},
           slug="b-round")
    before = logs(rounds)

    with pytest.raises(Refused) as refused:
        moving.adopt(B, "P1", blocks="B1", actor="planner")

    assert f"P1 came from {A}:Q1, so it arrives holding up every stage" in str(
        refused.value)
    assert logs(rounds) == before

    adopted = moving.adopt(B, "P1", actor="planner")
    assert (adopted.id, adopted.question.blocks, adopted.question.by,
            adopted.question.at) == ("Q1", "all", "scout", ASKED_AT)


def test_a_question_of_a_dropped_round_takes_a_list_with_the_operators_pointer(rounds):
    ask(blocks="B1")
    drop()
    opening.open_round(B, description="a placeholder round", actor="planner",
                       flow="fix")
    append(rounds, "stage.declared", {"id": "B1", "repo": "proj-a", "branch": "again"},
           slug="b-round")

    adopted = moving.adopt(B, "P1", blocks="B1", record="comms:33", actor="planner")

    assert adopted.question.blocks == ("B1",) and adopted.ruling == "S1"


# -- where a dropped round shows ---------------------------------------------------------

def test_a_dropped_round_is_shown_as_dropped_with_who_and_when(rounds, capsys):
    ask()
    drop()

    assert main(["show", A]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"round {A}   dropped   flow fix")
    assert (f"ended: dropped by operator at {DROPPED_AT}, record comms:30; "
            "questions: Q1 to P1") in lines
    assert "questions: 1 moved" in lines
    assert main(["show", A, "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["status"] == "dropped"
    assert found["ended"] == {"how": "dropped", "by": "operator", "record": "comms:30",
                              "actor": "planner", "at": DROPPED_AT,
                              "questions": {"Q1": "P1"}}
    assert found["questions"][0]["moved_to"] == "P1"
    assert found["history"][-1]["type"] == "round.dropped"


def test_the_note_of_a_dropped_round_says_so(rounds):
    ask()
    drop(withdraw_questions=True)

    note = (rounds / "a-round" / "round-note.md").read_text(encoding="utf-8")
    assert "\nstatus: dropped\n" in note
    assert "\n- status: dropped\n" in note
    assert (f"\n- ended: dropped by operator at {DROPPED_AT}, record comms:30; "
            "questions: Q1 withdrawn\n") in note
    assert "### Withdrawn" in note and "### Open" not in note


def test_the_index_keeps_how_a_round_ended(rounds):
    drop()
    kept = index.read("proj-a", rounds, "a-round")
    assert (kept.status, kept.ended) == ("dropped", {
        "how": "dropped", "by": "operator", "record": "comms:30", "at": DROPPED_AT})

    # The same from the logs alone, and from an index made again.
    from moxi_rounds import folders, paths
    paths.index_file().unlink()
    assert index.read("proj-a", rounds, "a-round").ended == kept.ended
    index.rebuild(folders.homes()[0])
    assert index.read("proj-a", rounds, "a-round") == kept
    assert index.disagreements(folders.homes()[0]) == []
