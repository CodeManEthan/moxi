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


NOT_APPLIED_HERE = sorted(set(events.TYPES) - {"round.opened"})


@pytest.mark.parametrize("name", NOT_APPLIED_HERE)
def test_a_type_with_no_part_in_the_state_changes_only_the_history(name):
    before = state.fold([opened(parent="proj-a/program")])
    later = event(2, name)

    after = state.fold([opened(parent="proj-a/program"), later])

    assert after.history == [*before.history, later]
    assert dataclasses.replace(after, history=before.history) == before


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
