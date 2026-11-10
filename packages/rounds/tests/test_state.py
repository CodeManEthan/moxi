"""The fold: the same log gives the same state, and each event type changes only its own
part of it."""

from __future__ import annotations

import copy
import dataclasses

import pytest

from moxi_rounds import events, log, state

STEPS = [{"step": "build", "by": "builder", "model": "second"},
         {"step": "finish", "by": "builder"}]
OPENED = {"description": "a placeholder round", "flow": "fix", "steps": STEPS}
AT = "2026-01-02T03:04:05Z"


def event(n, name, **fields):
    return {"v": 1, "n": n, "at": AT, "actor": "planner", "type": name, **fields}


def opened(**more):
    return event(1, "round.opened", **copy.deepcopy(OPENED), **more)


def test_no_events_fold_to_a_state_with_nothing_set():
    assert state.fold([]) == state.State()
    assert state.State().status is None
    assert state.State().history == []


def test_an_opening_sets_the_description_the_flow_its_steps_and_the_status():
    found = state.fold([opened()])

    assert found.description == "a placeholder round"
    assert found.flow == "fix"
    assert found.steps == STEPS
    assert found.parent is None
    assert found.status == state.OPEN == "open"


def test_an_opening_under_a_parent_sets_the_parent():
    assert state.fold([opened(parent="proj-a/program")]).parent == "proj-a/program"


def test_an_opening_changes_only_those_five_fields_and_the_history():
    first = opened(parent="proj-a/program", inherits=["S1"], record="comms:12")

    changed = {field.name for field in dataclasses.fields(state.State)
               if getattr(state.fold([first]), field.name) != getattr(state.State(),
                                                                       field.name)}

    assert changed == {"description", "flow", "steps", "parent", "status", "history"}


def test_the_history_is_every_event_in_order():
    log_events = [opened(), event(2, "finish.done", stage="B1", tip="0" * 40),
                  event(3, "round.archived")]

    assert state.fold(log_events).history == log_events


def test_the_same_log_folds_to_the_same_state():
    log_events = [opened(), event(2, "ruling.recorded", id="C1", text="a ruling",
                                  record="comms:12")]

    assert state.fold(log_events) == state.fold(copy.deepcopy(log_events))
    assert state.fold(log_events) == state.fold(iter(log_events))


def test_a_log_read_from_its_file_folds_to_what_its_events_fold_to(tmp_path):
    path = log.round_log(tmp_path)
    written = [log.append(path, "round.opened", OPENED, actor="planner"),
               log.append(path, "finish.done", {"stage": "B1", "tip": "0" * 40},
                          actor="coder")]

    assert state.fold(log.read(path)) == state.fold(written)
    assert state.fold(log.read(path)) == state.fold(log.read(path))


def test_a_fold_changes_none_of_its_events():
    log_events = [opened(), event(2, "finish.done", stage="B1", tip="0" * 40)]
    before = copy.deepcopy(log_events)

    found = state.fold(log_events)
    found.steps[0]["by"] = "someone else"
    found.steps.append({"step": "retro"})

    assert log_events == before


def test_every_event_type_has_a_handler_and_no_handler_is_for_another_type():
    assert set(state._HANDLERS) == set(events.TYPES)


NOT_APPLIED_HERE = sorted(set(events.TYPES)
                          - {"round.opened", "ruling.recorded", "question.asked",
                             "question.changed", "ruling.shown", "round.dropped",
                             "flow.changed", "design.frozen", "stage.declared",
                             "stage.changed"})


@pytest.mark.parametrize("name", NOT_APPLIED_HERE)
def test_a_type_with_no_part_in_the_state_changes_only_the_history(name):
    before = state.fold([opened(parent="proj-a/program")])
    later = event(2, name)

    after = state.fold([opened(parent="proj-a/program"), later])

    assert after.history == [*before.history, later]
    assert dataclasses.replace(after, history=before.history) == before


def test_a_drop_ends_the_round_and_says_where_each_question_went():
    asked_three = [
        opened(),
        *(event(n, "question.asked", id=f"Q{n - 1}", text="A placeholder question?",
                **{"from": "comms:11"}, blocks="all", state="open", asked_by="scout",
                asked_at="2026-01-02T03:04:05Z") for n in (2, 3, 4)),
        event(5, "ruling.recorded", id="C1", record="comms:12", kind="substance",
              rules="Q3", question="Q3", state="ruled", text="A placeholder answer.")]
    drop = event(6, "round.dropped", by="operator", record="comms:30",
                 questions={"Q1": "P4", "Q2": "withdrawn"})
    before = state.fold(asked_three)

    after = state.fold([*asked_three, drop])

    assert after.status == state.DROPPED
    assert after.ended == state.Ended(
        how="dropped", by="operator", record="comms:30", actor=drop["actor"],
        at=drop["at"], questions=(("Q1", "P4"), ("Q2", "withdrawn")))
    assert [(one.state, one.moved_to) for one in after.questions] == [
        ("moved", "P4"), ("withdrawn", None), ("ruled", None)]
    assert after.questions[0].changes[-1] == state.Change(
        state="moved", blocks=None, until=None, ruling=None, record="comms:30",
        actor=drop["actor"], at=drop["at"], moved_to="P4")
    assert after.questions[2] == before.questions[2]
    assert after.rulings == before.rulings and after.flow == before.flow


def test_a_drop_that_names_no_question_ends_the_round_and_changes_none():
    after = state.fold([opened(), event(2, "round.dropped")])

    assert (after.status, after.ended.by, after.ended.questions) == ("dropped", None, ())
    assert state.fold([opened()]).ended is None


def test_a_ruling_is_added_to_the_rulings_and_changes_nothing_else():
    before = state.fold([opened()])
    first = event(2, "ruling.recorded", id="C1", text="a ruling", record="comms:12",
                  kind="substance", stages="all")
    second = {**event(3, "ruling.recorded", id="C2", record="comms:13", kind="process",
                      rules="skip:retro", reason="he said so", stages=["B2", "B3"]),
              "actor": "curator", "at": "2026-01-03T00:00:00Z"}

    after = state.fold([opened(), first, second])

    assert after.rulings == [
        state.Ruling(id="C1", text="a ruling", record="comms:12", reason=None,
                     kind="substance", rules=None, stages="all", scope=(),
                     actor="planner", at=AT),
        state.Ruling(id="C2", text=None, record="comms:13", reason="he said so",
                     kind="process", rules="skip:retro", stages=("B2", "B3"), scope=(),
                     actor="curator", at="2026-01-03T00:00:00Z")]
    assert dataclasses.replace(after, rulings=[], history=before.history) == before


def test_a_projects_rulings_are_its_register_with_each_entrys_scope():
    entry = event(1, "ruling.recorded", id="S1", text="a ruling", record="comms:12",
                  kind="substance",
                  scope=[{"repo": "proj-a", "path": "packages/ui/**"}, {"repo": "proj-b"}])

    found = state.fold([entry])
    found.rulings[0].scope[0]["path"] = "changed"

    assert found.status is None
    assert found.rulings[0].stages is None
    assert found.rulings[0].scope[1] == {"repo": "proj-b"}
    assert entry["scope"][0] == {"repo": "proj-a", "path": "packages/ui/**"}


def asked(n, **more):
    fields = {"id": "Q1", "text": "Is it so?", "from": "comms:12", "blocks": "all",
              "state": "open", "asked_by": "planner", "asked_at": AT, **more}
    return event(n, "question.asked", **fields)


def test_a_question_is_added_to_the_questions_and_changes_nothing_else():
    before = state.fold([opened()])
    second = {**asked(3, id="Q2", blocks=["B1", "B2"], record="comms:13",
                      asked_by="scout", asked_at="2025-12-31T00:00:00Z"),
              "actor": "curator", "at": "2026-01-03T00:00:00Z"}
    second["from"] = "comms:13"

    after = state.fold([opened(), asked(2), second])

    assert after.questions == [
        state.Question(id="Q1", text="Is it so?", origin="comms:12", blocks="all",
                       state="open", by="planner", at=AT),
        state.Question(id="Q2", text="Is it so?", origin="comms:13", blocks=("B1", "B2"),
                       state="open", by="scout", at="2025-12-31T00:00:00Z",
                       record="comms:13")]
    assert dataclasses.replace(after, questions=[], history=before.history) == before


def test_a_question_that_does_not_say_what_it_holds_up_holds_up_every_stage():
    bare = event(2, "question.asked", id="Q1", text="Is it so?")

    [found] = state.fold([opened(), bare]).questions

    assert found.blocks == "all"
    assert (found.by, found.at) == ("planner", AT)


def ruled_as(n, ruling_id, **more):
    """A ruling of the question Q1, which is the whole of the change it makes."""
    return event(n, "ruling.recorded", id=ruling_id, kind="process", question="Q1",
                 **more)


def test_a_question_keeps_every_change_made_to_it_in_order():
    parked = {**ruled_as(3, "C1", rules="park:Q1", state="parked", until="B2",
                         record="comms:13"), "at": "2026-01-03T00:00:00Z"}
    widened = {**event(4, "question.changed", id="Q1", blocks="all"), "actor": "scout"}
    reopened = event(5, "question.changed", id="Q1", state="open")
    ruled = ruled_as(6, "C2", rules="Q1", state="ruled", record="comms:14")
    log_events = [opened(), asked(2, blocks=["B1"]), parked, widened, reopened, ruled]

    def after(count):
        [found] = state.fold(log_events[:count]).questions
        return found

    assert (after(3).state, after(3).until, after(3).ruling) == ("parked", "B2", "C1")
    assert (after(4).state, after(4).until, after(4).blocks) == ("parked", "B2", "all")
    assert (after(5).state, after(5).until, after(5).ruling) == ("open", None, "C1")
    assert (after(6).state, after(6).blocks, after(6).ruling) == ("ruled", "all", "C2")
    assert after(6).changes == (
        state.Change(state="parked", blocks=None, until="B2", ruling="C1",
                     record="comms:13", actor="planner", at="2026-01-03T00:00:00Z"),
        state.Change(state=None, blocks="all", until=None, ruling=None, record=None,
                     actor="scout", at=AT),
        state.Change(state="open", blocks=None, until=None, ruling=None, record=None,
                     actor="planner", at=AT),
        state.Change(state="ruled", blocks=None, until=None, ruling="C2",
                     record="comms:14", actor="planner", at=AT))
    assert (after(6).by, after(6).at, after(6).origin) == ("planner", AT, "comms:12")


def test_a_change_touches_only_the_question_it_names():
    before = state.fold([opened(), asked(2), asked(3, id="Q2")])

    after = state.fold([opened(), asked(2), asked(3, id="Q2"),
                        event(4, "question.changed", id="Q2", state="parked"),
                        event(5, "question.changed", id="Q9", state="ruled")])

    assert after.questions[0] == before.questions[0]
    assert [found.state for found in after.questions] == ["open", "parked"]
    assert dataclasses.replace(after, questions=before.questions,
                               history=before.history) == before


def test_a_ruling_of_a_question_is_the_whole_of_its_change():
    asked_two = [opened(), asked(2, blocks=["B1", "B2"]), asked(3, id="Q2")]
    before = state.fold(asked_two)
    narrowed = ruled_as(4, "C1", rules="narrow:Q1=B2", blocks=["B2"], record="comms:13")
    withdrawn = ruled_as(5, "C2", rules="withdraw:Q1", state="withdrawn",
                         record="comms:14")

    after = state.fold([*asked_two, narrowed, withdrawn])

    first, second = after.questions
    assert (first.state, first.blocks, first.ruling) == ("withdrawn", ("B2",), "C2")
    assert first.changes == (
        state.Change(state=None, blocks=("B2",), until=None, ruling="C1",
                     record="comms:13", actor="planner", at=AT),
        state.Change(state="withdrawn", blocks=None, until=None, ruling="C2",
                     record="comms:14", actor="planner", at=AT))
    assert second == before.questions[1]
    assert [(one.id, one.question) for one in after.rulings] == [("C1", "Q1"),
                                                                 ("C2", "Q1")]
    assert dataclasses.replace(after, questions=before.questions, rulings=[],
                               history=before.history) == before


def test_a_ruling_of_a_question_the_log_never_asked_changes_no_question():
    before = state.fold([opened(), asked(2)])

    after = state.fold([opened(), asked(2),
                        {**ruled_as(3, "C1", state="ruled"), "question": "Q9"}])

    assert after.questions == before.questions
    assert [one.id for one in after.rulings] == ["C1"]


def test_a_change_that_is_no_ruling_names_no_ruling():
    [found] = state.fold([opened(), asked(2),
                          event(3, "question.changed", id="Q1", state="parked",
                                ruling="C7")]).questions

    assert (found.state, found.ruling, found.changes[0].ruling) == ("parked", None, None)


def test_a_question_keeps_its_options_its_retired_keys_and_the_key_chosen():
    offered = asked(2, blocks=["B1"], options={"a": "one", "b": "two", "c": "three"})
    retired = event(3, "question.changed", id="Q1", retired=["c"],
                    options={"d": "four"})
    answered = event(4, "ruling.recorded", id="C1", text="two", record="comms:13",
                     kind="substance", rules="Q1", chose="b", stages=["B1"],
                     question="Q1", state="ruled")
    log_events = [opened(), offered, retired, answered]

    def after(count):
        [found] = state.fold(log_events[:count]).questions
        return found

    assert after(2).options == (("a", "one"), ("b", "two"), ("c", "three"))
    assert (after(2).retired, after(2).chose) == ((), None)
    assert after(3).options == (("a", "one"), ("b", "two"), ("d", "four"))
    assert after(3).retired == ("c",)
    assert after(3).changes == (
        state.Change(state=None, blocks=None, until=None, ruling=None, record=None,
                     actor="planner", at=AT, options=(("d", "four"),),
                     retired=("c",)),)
    assert (after(4).chose, after(4).state, after(4).retired) == ("b", "ruled", ("c",))
    assert after(4).ruling == "C1"
    assert state.fold(log_events).rulings[0].chose == "b"


def test_options_change_no_other_field_of_a_question():
    events_ = [opened(), asked(2, blocks=["B1"]),
               ruled_as(3, "C1", rules="park:Q1", state="parked", until="B2",
                        record="comms:13")]
    [before] = state.fold(events_).questions

    [after] = state.fold([*events_,
                          event(4, "question.changed", id="Q1",
                                options={"a": "one", "b": "two", "c": "three"}),
                          event(5, "question.changed", id="Q1", retired=["a"])]).questions

    assert dataclasses.replace(after, options=(), retired=(),
                               changes=before.changes) == before
    assert (after.state, after.blocks, after.until, after.ruling) == (
        "parked", ("B1",), "B2", "C1")


def test_a_key_keeps_its_first_text_and_a_retired_key_stays_retired_in_the_fold():
    [found] = state.fold([
        opened(), asked(2, options={"a": "one", "b": "two", "c": "three"}),
        event(3, "question.changed", id="Q1", options={"a": "another"}),
        event(4, "question.changed", id="Q1", retired=["c", "c", "z"]),
        event(5, "question.changed", id="Q1", options={"c": "three"}, retired=["c"]),
    ]).questions

    assert found.options == (("a", "one"), ("b", "two"))
    assert found.retired == ("c",)


def test_a_ruling_that_rules_no_question_chooses_nothing():
    [found] = state.fold([
        opened(), asked(2, options={"a": "one", "b": "two"}),
        event(3, "ruling.recorded", id="C1", record="comms:13", kind="process",
              rules="park:Q1"),
        event(4, "ruling.recorded", id="C2", text="a ruling", record="comms:13",
              kind="substance", stages="all")]).questions

    assert found.chose is None


def test_a_question_brought_from_another_log_says_what_it_was_there():
    arrived = asked(2, id="P1", blocks="none", state="parked", asked_by="scout",
                    asked_at="2025-12-31T00:00:00Z", until="round:proj-a/b-round",
                    options={"a": "one", "b": "two"}, retired=["c"],
                    moved_from="proj-a/a-round:Q4", held=["B1", "B2"])

    [found] = state.fold([arrived]).questions

    assert found == state.Question(
        id="P1", text="Is it so?", origin="comms:12", blocks="none", state="parked",
        by="scout", at="2025-12-31T00:00:00Z", until="round:proj-a/b-round",
        options=(("a", "one"), ("b", "two")), retired=("c",),
        moved_from="proj-a/a-round:Q4", held=("B1", "B2"))


def test_a_question_moved_to_another_log_is_moved_here_and_names_what_it_became():
    by_anyone = event(4, "question.changed", id="Q1", state="moved", moved_to="P3")
    his = {**ruled_as(5, "C1", rules="move:Q2", state="moved", moved_to="P4"),
           "question": "Q2"}
    before = state.fold([opened(), asked(2), asked(3, id="Q2", blocks=["B1"])])

    after = state.fold([opened(), asked(2), asked(3, id="Q2", blocks=["B1"]),
                        by_anyone, his])

    first, second = after.questions
    assert (first.state, first.moved_to, first.ruling, first.blocks) == (
        "moved", "P3", None, "all")
    assert (second.state, second.moved_to, second.ruling, second.blocks) == (
        "moved", "P4", "C1", ("B1",))
    assert [(change.state, change.moved_to, change.ruling) for change in
            (*first.changes, *second.changes)] == [("moved", "P3", None),
                                                   ("moved", "P4", "C1")]
    assert [ruling.id for ruling in after.rulings] == ["C1"]
    assert dataclasses.replace(after, questions=before.questions, rulings=[],
                               history=before.history) == before


def test_a_projects_questions_are_its_list():
    found = state.fold([event(1, "question.asked", id="P1", text="Is it so?",
                              blocks="none", state="open", asked_by="scout",
                              asked_at=AT, **{"from": "comms:12"})])

    assert found.status is None
    assert [(one.id, one.blocks, one.by) for one in found.questions] == [
        ("P1", "none", "scout")]


def test_a_type_this_code_does_not_know_changes_only_the_history():
    before = state.fold([opened()])

    after = state.fold([opened(), event(2, "round.archived", why="a later version's")])

    assert dataclasses.replace(after, history=before.history) == before
    assert after.history[-1]["type"] == "round.archived"


def test_a_log_that_opened_no_round_has_no_status():
    found = state.fold([event(1, "ruling.recorded", id="S1", text="a ruling",
                              record="comms:12")])

    assert found.status is None
    assert found.description is None and found.flow is None and found.steps == []
    assert len(found.history) == 1


# -- a ruling that replaces another, or takes one back -----------------------------------

def test_a_ruling_marks_the_one_it_replaces_and_the_one_it_takes_back():
    found = state.fold([
        opened(), event(2, "ruling.recorded", id="C1", kind="substance", text="One."),
        event(3, "ruling.recorded", id="C2", kind="substance", text="Two.",
              supersedes="C1"),
        event(4, "ruling.recorded", id="C3", kind="process", rules="revoke:C2",
              revokes="C2")])

    assert [(one.id, one.supersedes, one.revokes, one.replaced_by, one.revoked_by)
            for one in found.rulings] == [
        ("C1", None, None, "C2", None), ("C2", "C1", None, None, "C3"),
        ("C3", None, "C2", None, None)]


def test_a_ruling_already_replaced_or_one_the_log_never_held_is_left_as_it_is():
    found = state.fold([
        opened(), event(2, "ruling.recorded", id="C1", kind="substance", text="One."),
        event(3, "ruling.recorded", id="C2", kind="substance", supersedes="C1"),
        event(4, "ruling.recorded", id="C3", kind="process", revokes="C1"),
        event(5, "ruling.recorded", id="C4", kind="process", revokes="C9"),
        event(6, "ruling.recorded", id="C5", kind="process", revokes="C5")])

    assert [(one.replaced_by, one.revoked_by) for one in found.rulings] == [
        ("C2", None), (None, None), (None, None), (None, None), (None, None)]


def test_a_question_reads_as_the_changes_left_once_a_ruling_of_it_is_taken_back():
    parked = ruled_as(3, "C1", rules="park:Q1", state="parked", until="B2")
    narrowed = ruled_as(4, "C2", rules="narrow:Q1=B1", blocks=["B1"])
    widened = event(5, "question.changed", id="Q1", blocks=["B1", "B3"])
    unparked = ruled_as(6, "C3", rules="revoke:C1", revokes="C1")
    unnarrowed = ruled_as(7, "C4", rules="revoke:C2", revokes="C2")
    log_events = [opened(), asked(2, blocks=["B1", "B2"]), parked, narrowed, widened,
                  unparked, unnarrowed]

    def after(count):
        [found] = state.fold(log_events[:count]).questions
        return found

    assert (after(5).state, after(5).until, after(5).blocks) == (
        "parked", "B2", ("B1", "B3"))
    assert (after(6).state, after(6).until, after(6).blocks, after(6).ruling) == (
        "open", None, ("B1", "B3"), "C3")
    assert (after(7).state, after(7).blocks, after(7).ruling) == (
        "open", ("B1", "B2", "B3"), "C4")
    assert [(change.ruling, change.undone_by) for change in after(7).changes] == [
        ("C1", "C3"), ("C2", "C4"), (None, None), ("C3", None), ("C4", None)]


def test_an_answer_taken_back_leaves_no_key_chosen_and_a_replaced_one_the_later_key():
    first = ruled_as(3, "C1", rules="Q1", state="ruled", chose="a")
    second = ruled_as(4, "C2", rules="Q1", state="ruled", chose="b", supersedes="C1")
    taken_back = ruled_as(5, "C3", rules="revoke:C2", revokes="C2", state="open")
    log_events = [opened(), asked(2, options={"a": "One.", "b": "Two."}), first, second,
                  taken_back]

    def after(count):
        [found] = state.fold(log_events[:count]).questions
        return found

    assert (after(4).state, after(4).chose, after(4).ruling) == ("ruled", "b", "C2")
    assert (after(5).state, after(5).chose, after(5).ruling) == ("open", None, "C3")


def test_a_question_with_no_ruling_left_out_reads_as_the_fold_gives_it():
    log_events = [
        opened(), asked(2, blocks=["B1"]),
        ruled_as(3, "C1", rules="park:Q1", state="parked", until="B2"),
        event(4, "question.changed", id="Q1", blocks="all"),
        ruled_as(5, "C2", rules="narrow:Q1=B2,B3", blocks=["B2", "B3"]),
        event(6, "question.changed", id="Q1", state="open"),
        ruled_as(7, "C3", rules="Q1", state="ruled"),
        ruled_as(8, "C4", rules="revoke:C3", revokes="C3", state="open"),
        event(9, "question.changed", id="Q1", blocks="all"),
        ruled_as(10, "C5", rules="withdraw:Q1", state="withdrawn")]

    for count in range(2, len(log_events) + 1):
        found = state.fold(log_events[:count])
        assert state.without(found, found.questions[0]) == found.questions[0]


def test_a_question_read_without_a_ruling_is_as_if_that_ruling_was_never_made():
    found = state.fold([
        opened(), asked(2, blocks=["B1", "B2"]),
        ruled_as(3, "C1", rules="narrow:Q1=B1", blocks=["B1"]),
        event(4, "question.changed", id="Q1", blocks=["B1", "B3"]),
        ruled_as(5, "C2", rules="park:Q1", state="parked")])
    [question] = found.questions

    assert state.without(found, question, ["C1"]).blocks == ("B1", "B2", "B3")
    assert state.without(found, question, ["C2"]).state == "open"
    assert state.without(found, question, ["C1", "C2"]).ruling is None
    assert found.questions == [question]


# -- rulings marked as shown -------------------------------------------------------------

def test_a_mark_makes_the_rulings_it_names_shown_and_changes_nothing_else():
    recorded = [opened(),
                event(2, "ruling.recorded", id="C1", kind="substance", text="One."),
                event(3, "ruling.recorded", id="C2", kind="substance", text="Two."),
                event(4, "ruling.recorded", id="C3", kind="substance", text="Three.")]
    before = state.fold(recorded)
    mark = event(5, "ruling.shown", ids=["C1", "C3", "C9"], record="comms:20")

    after = state.fold([*recorded, mark])

    assert [one.shown for one in after.rulings] == ["comms:20", None, "comms:20"]
    assert [dataclasses.replace(one, shown=None) for one in after.rulings] == (
        before.rulings)
    assert dataclasses.replace(after, rulings=before.rulings,
                               history=before.history) == before


def test_the_first_mark_of_a_ruling_is_the_one_kept():
    found = state.fold([
        opened(), event(2, "ruling.recorded", id="C1", kind="substance", text="One."),
        event(3, "ruling.shown", ids=["C1"], record="comms:20"),
        event(4, "ruling.shown", ids=["C1"], record="comms:30")])

    assert found.rulings[0].shown == "comms:20"


@pytest.mark.parametrize("fields", [
    {"ids": ["C1"]}, {"ids": ["C1"], "record": ""}, {"ids": ["C1"], "record": 20},
    {"record": "comms:20"}, {"ids": "C1", "record": "comms:20"}])
def test_only_a_pointer_and_a_list_of_ids_make_a_mark(fields):
    found = state.fold([
        opened(), event(2, "ruling.recorded", id="C1", kind="substance", text="One."),
        event(3, "ruling.shown", **fields)])

    assert found.rulings[0].shown is None


def test_a_mark_outlasts_the_replacing_and_the_taking_back_of_its_ruling():
    found = state.fold([
        opened(), event(2, "ruling.recorded", id="C1", kind="substance", text="One."),
        event(3, "ruling.shown", ids=["C1"], record="comms:20"),
        event(4, "ruling.recorded", id="C2", kind="substance", text="Two.",
              supersedes="C1"),
        event(5, "ruling.recorded", id="C3", kind="process", rules="revoke:C2",
              revokes="C2")])

    assert [(one.id, one.shown, one.replaced_by, one.revoked_by)
            for one in found.rulings] == [
        ("C1", "comms:20", "C2", None), ("C2", None, None, "C3"),
        ("C3", None, None, None)]


# -- the flow a round follows, and the skips that stand ----------------------------------

LATER = [*STEPS, {"step": "retro", "by": "helper"}]


def test_a_change_of_flow_sets_the_flow_and_its_steps_and_nothing_else():
    before = state.fold([opened()])
    moved = event(2, "flow.changed", was="fix", flow="small", steps=LATER, by="lead")

    after = state.fold([opened(), moved])
    after.steps.append({"step": "one more"})

    assert (after.flow, after.steps[:-1]) == ("small", LATER)
    assert moved["steps"] == LATER
    assert dataclasses.replace(after, flow="fix", steps=before.steps,
                               history=before.history) == before


@pytest.mark.parametrize("fields", [{}, {"flow": "small"}, {"steps": LATER},
                                    {"flow": 3, "steps": LATER},
                                    {"flow": "small", "steps": "retro"}])
def test_a_change_of_flow_with_no_name_or_no_steps_moves_the_round_nowhere(fields):
    found = state.fold([opened(), event(2, "flow.changed", **fields)])

    assert (found.flow, found.steps) == ("fix", STEPS)


def test_a_ruling_of_the_flow_moves_the_round_as_the_leads_change_does():
    ruled = event(2, "ruling.recorded", id="C1", kind="process", rules="flow:small",
                  record="comms:12", was="fix", flow="small", steps=LATER)

    found = state.fold([opened(), ruled])

    assert (found.flow, found.steps) == ("small", LATER)
    assert [one.rules for one in found.rulings] == ["flow:small"]


def skip_of_the_questions_step(n, ruling_id, **more):
    return event(n, "ruling.recorded", id=ruling_id, kind="process",
                 rules="skip:docket:B1", record="comms:13", **more)


def test_a_skip_taken_back_is_marked_with_the_ruling_that_took_it_back():
    skipped = skip_of_the_questions_step(3, "C1", covers=["Q1"])
    taken_back = event(4, "ruling.recorded", id="C2", kind="process",
                       rules="revoke:C1", revokes="C1", record="comms:14")
    before = state.fold([opened(), asked(2, blocks=["B1"]), skipped])

    after = state.fold([opened(), asked(2, blocks=["B1"]), skipped, taken_back])

    assert (before.rulings[0].revoked_by, before.rulings[0].covers) == (None, ("Q1",))
    assert [(one.id, one.revoked_by, one.covers, one.let_go)
            for one in after.rulings] == [("C1", "C2", ("Q1",), ()),
                                          ("C2", None, (), ())]
    assert after.questions == before.questions


def test_a_skip_lets_go_of_a_listed_question_narrowed_off_its_stage_for_good():
    start = [opened(), asked(2, blocks=["B1", "B2"]), asked(3, id="Q2", blocks=["B1"]),
             skip_of_the_questions_step(4, "C1", covers=["Q1", "Q2"])]
    narrowed = ruled_as(5, "C2", rules="narrow:Q1=B2", blocks=["B2"], record="comms:14")
    widened = {**event(6, "question.changed", id="Q1", blocks=["B1", "B2"]),
               "actor": "scout"}

    before = state.fold(start)
    after = state.fold([*start, narrowed])
    again = state.fold([*start, narrowed, widened])

    assert before.rulings[0].let_go == ()
    assert after.rulings[0].let_go == ("Q1",)
    assert after.rulings[0].covers == ("Q1", "Q2")
    assert again.rulings[0].let_go == ("Q1",)
    assert again.questions[0].blocks == ("B1", "B2")


def test_a_skip_keeps_a_listed_question_that_still_holds_its_stage():
    start = [opened(), asked(2, blocks=["B1", "B2"]),
             skip_of_the_questions_step(3, "C1", covers=["Q1"])]
    narrowed = ruled_as(4, "C2", rules="narrow:Q1=B1", blocks=["B1"], record="comms:14")
    widened = {**event(5, "question.changed", id="Q1", blocks="all"), "actor": "scout"}

    found = state.fold([*start, narrowed, widened])

    assert found.rulings[0].let_go == ()


# -- stages and frozen versions ----------------------------------------------------------

def test_a_declared_stage_joins_the_plan_and_changes_nothing_else():
    before = state.fold([opened()])
    build = event(2, "stage.declared", id="B1", repo="proj-a", branch="branch-b1",
                  after=[], slices=[{"id": "1a", "what": "a piece", "minutes": 40}])
    child = {**event(3, "stage.declared", id="B2", round="proj-a/a-child",
                     after=["B1"]), "actor": "scout"}

    after = state.fold([opened(), build, child, event(4, "stage.declared", id="B1",
                                                      repo="proj-b", branch="other")])

    assert after.stages == [
        state.Stage(id="B1", repo="proj-a", branch="branch-b1", round=None, after=(),
                    slices=({"id": "1a", "what": "a piece", "minutes": 40},), why=None,
                    version=None, actor="planner", at=AT),
        state.Stage(id="B2", repo=None, branch=None, round="proj-a/a-child",
                    after=("B1",), slices=(), why=None, version=None, actor="scout",
                    at=AT)]
    assert dataclasses.replace(after, stages=[], history=before.history) == before


def test_a_stage_declared_with_no_id_declares_none():
    assert state.fold([opened(), event(2, "stage.declared", repo="proj-a")]).stages == []


DECLARED = {
    "question": [{"id": "Q1", "text": "Is it so?", "blocks": ["B1"],
                  "options": {"a": "one", "b": "two"}},
                 {"id": "Q2", "text": "Is it not?", "blocks": "all"}],
    "stage": [{"id": "B1", "repo": "proj-a", "branch": "branch-b1", "after": [],
               "why": "it lands alone",
               "slices": [{"id": "1a", "what": "a piece", "minutes": 40}]},
              {"id": "B2", "round": "proj-a/a-child", "after": ["B1"]}],
    "trace": [], "commitment": []}


def frozen(n, version, **more):
    fields = {"version": version, "digest": "0" * 64, "records_only": False,
              "declared": copy.deepcopy(DECLARED), "added": {}, "pending": [], **more}
    return {**event(n, "design.frozen", **fields), "actor": "scout",
            "at": "2026-01-03T00:00:00Z"}


def test_a_frozen_version_adds_what_it_says_is_new_and_is_the_whole_of_the_freezing():
    first = frozen(2, 1, added={"questions": ["Q1", "Q2"], "stages": ["B1", "B2"]})
    before = state.fold([opened()])

    after = state.fold([opened(), first])

    who = {"by": "scout", "at": "2026-01-03T00:00:00Z"}
    assert after.questions == [
        state.Question(id="Q1", text="Is it so?", origin="file:versions/design-v1.md#Q1",
                       blocks=("B1",), state="open",
                       options=(("a", "one"), ("b", "two")), **who),
        state.Question(id="Q2", text="Is it not?", origin="file:versions/design-v1.md#Q2",
                       blocks="all", state="open", **who)]
    assert [(one.id, one.repo, one.branch, one.round, one.after, one.why, one.version,
             one.actor) for one in after.stages] == [
        ("B1", "proj-a", "branch-b1", None, (), "it lands alone", 1, "scout"),
        ("B2", None, None, "proj-a/a-child", ("B1",), None, 1, "scout")]
    assert after.stages[0].slices == ({"id": "1a", "what": "a piece", "minutes": 40},)
    assert after.versions == [state.Version(
        n=1, digest="0" * 64, records_only=False, record=None, trace=None,
        declared=DECLARED, pending=(), actor="scout", at="2026-01-03T00:00:00Z")]
    assert dataclasses.replace(after, questions=[], stages=[], versions=[],
                               history=before.history) == before
    assert first["declared"] == DECLARED


def test_a_frozen_version_adds_nothing_its_event_does_not_say_is_new():
    found = state.fold([opened(), frozen(2, 1)])

    assert (found.questions, found.stages) == ([], [])
    assert [one.n for one in found.versions] == [1]


def test_a_later_version_widens_a_hold_adds_a_wait_and_changes_options():
    first = frozen(2, 1, added={"questions": ["Q1", "Q2"], "stages": ["B1", "B2"]})
    second = frozen(3, 2, added={
        "after": {"B1": ["B2"]}, "blocks": {"Q1": ["B1", "B2"]},
        "options": {"Q1": {"options": {"c": "three"}, "retired": ["a"]}}})

    found = state.fold([opened(), first, second])

    assert found.stages[0].after == ("B2",)
    assert found.questions[0].blocks == ("B1", "B2")
    assert found.questions[0].options == (("b", "two"), ("c", "three"))
    assert found.questions[0].retired == ("a",)
    assert [change.ruling for change in found.questions[0].changes] == [None, None]
    assert found.questions[1] == state.fold([opened(), first]).questions[1]


def test_what_a_version_took_away_is_kept_with_it_and_changes_nothing():
    first = frozen(2, 1, added={"questions": ["Q1", "Q2"], "stages": ["B1", "B2"]})
    second = frozen(3, 2, pending=[
        {"what": "question", "id": "Q2"}, {"what": "stage", "id": "B2"},
        {"what": "after", "id": "B2", "gone": ["B1"]},
        {"what": "blocks", "id": "Q1", "blocks": "none"},
        {"what": "blocks", "id": "Q2", "blocks": ["B1"]}])
    before = state.fold([opened(), first])

    after = state.fold([opened(), first, second])

    assert after.versions[1].pending == (
        state.Pending("question", "Q2"), state.Pending("stage", "B2"),
        state.Pending("after", "B2", gone=("B1",)),
        state.Pending("blocks", "Q1", blocks="none"),
        state.Pending("blocks", "Q2", blocks=("B1",)))
    assert (after.questions, after.stages) == (before.questions, before.stages)


def test_a_frozen_event_that_numbers_no_version_freezes_none():
    found = state.fold([opened(), event(2, "design.frozen", digest="0" * 64,
                                        added={"stages": ["B1"]},
                                        declared=copy.deepcopy(DECLARED))])

    assert (found.versions, found.stages) == ([], [])


def declared(n, stage_id, after=()):
    return event(n, "stage.declared", id=stage_id, repo="proj-a",
                 branch=f"branch-{stage_id.lower()}", after=list(after))


def test_a_change_to_a_stage_adds_to_what_it_waits_for_and_takes_off():
    start = [opened(), declared(2, "B1"), declared(3, "B2"),
             declared(4, "B3", after=["B1"])]
    more = event(5, "stage.changed", id="B3", after=["B2", "B1"])
    fewer = event(6, "stage.changed", id="B3", gone=["B1"], verdict="a verdict")

    before = state.fold(start)
    added = state.fold([*start, more])
    taken = state.fold([*start, more, fewer])

    assert [one.after for one in added.stages] == [(), (), ("B1", "B2")]
    assert [one.after for one in taken.stages] == [(), (), ("B2",)]
    assert added.stages[:2] == before.stages[:2]
    assert dataclasses.replace(added.stages[2], after=("B1",)) == before.stages[2]


def test_a_stage_that_left_keeps_its_record():
    start = [opened(), declared(2, "B1"), declared(3, "B2", after=["B1"])]
    left = event(4, "stage.changed", id="B1", removed=True, verdict="a verdict")

    assert state.fold([*start, left]).stages == state.fold(start).stages


def test_a_change_to_a_stage_the_log_never_declared_changes_no_stage():
    start = [opened(), declared(2, "B1")]
    odd = [event(3, "stage.changed", id="B9", after=["B1"]),
           event(4, "stage.changed", after=["B1"]),
           event(5, "stage.changed", id="B1", after="B2", gone=7)]

    assert state.fold([*start, *odd]).stages == state.fold(start).stages


def test_a_ruling_that_takes_a_wait_off_a_stage_is_the_whole_of_that_change():
    start = [opened(), declared(2, "B1"), declared(3, "B2"),
             declared(4, "B3", after=["B1", "B2"])]
    ruled = event(5, "ruling.recorded", id="C1", kind="process", record="comms:12",
                  rules="unafter:B3=B1")
    none = event(6, "ruling.recorded", id="C2", kind="process", record="comms:12",
                 rules="unafter:B9=B1")

    found = state.fold([*start, ruled, none])

    assert [one.after for one in found.stages] == [(), (), ("B2",)]
    assert [one.rules for one in found.rulings] == ["unafter:B3=B1", "unafter:B9=B1"]
