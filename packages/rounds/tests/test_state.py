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
                             "question.changed"})


@pytest.mark.parametrize("name", NOT_APPLIED_HERE)
def test_a_type_with_no_part_in_the_state_changes_only_the_history(name):
    before = state.fold([opened(parent="proj-a/program")])
    later = event(2, name)

    after = state.fold([opened(parent="proj-a/program"), later])

    assert after.history == [*before.history, later]
    assert dataclasses.replace(after, history=before.history) == before


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


def test_a_question_keeps_every_change_made_to_it_in_order():
    parked = {**event(3, "question.changed", id="Q1", state="parked", until="B2",
                      ruling="C1", record="comms:13"), "at": "2026-01-03T00:00:00Z"}
    widened = {**event(4, "question.changed", id="Q1", blocks="all"), "actor": "scout"}
    reopened = event(5, "question.changed", id="Q1", state="open")
    ruled = event(6, "question.changed", id="Q1", state="ruled", ruling="C2",
                  record="comms:14")
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
                        event(4, "question.changed", id="Q2", state="withdrawn",
                              ruling="C1", record="comms:13"),
                        event(5, "question.changed", id="Q9", state="ruled")])

    assert after.questions[0] == before.questions[0]
    assert [found.state for found in after.questions] == ["open", "withdrawn"]
    assert dataclasses.replace(after, questions=before.questions,
                               history=before.history) == before


def test_a_question_keeps_its_options_its_retired_keys_and_the_key_chosen():
    offered = asked(2, blocks=["B1"], options={"a": "one", "b": "two", "c": "three"})
    retired = event(3, "question.changed", id="Q1", retired=["c"],
                    options={"d": "four"})
    answered = event(4, "ruling.recorded", id="C1", text="two", record="comms:13",
                     kind="substance", rules="Q1", chose="b", stages=["B1"])
    ruled = event(5, "question.changed", id="Q1", state="ruled", ruling="C1",
                  record="comms:13")
    log_events = [opened(), offered, retired, answered, ruled]

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
    assert (after(4).chose, after(4).state) == ("b", "open")
    assert (after(5).chose, after(5).state, after(5).retired) == ("b", "ruled", ("c",))
    assert state.fold(log_events).rulings[0].chose == "b"


def test_options_change_no_other_field_of_a_question():
    events_ = [opened(), asked(2, blocks=["B1"]),
               event(3, "question.changed", id="Q1", state="parked", until="B2",
                     ruling="C1", record="comms:13")]
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
