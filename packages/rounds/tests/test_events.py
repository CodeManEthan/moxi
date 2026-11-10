"""The event types as data, and the check an append runs on an event."""

from __future__ import annotations

import pytest

from moxi_rounds import events

OPENED = {"description": "a placeholder round", "flow": "fix",
          "steps": [{"step": "build", "by": "builder"}]}


def test_the_types_are_the_ones_a_log_holds():
    assert sorted(events.TYPES) == sorted([
        "round.opened", "flow.changed", "design.frozen", "verdict.recorded",
        "question.asked", "question.changed", "stage.declared", "stage.changed",
        "ruling.recorded", "ruling.shown", "build.commissioned", "check.ran",
        "finding.marked", "wait.marked", "brief.sized", "finish.action", "finish.done",
        "lock.cleared", "build.dropped", "round.dropped", "thread.decided", "repair",
        "round.closed"])


@pytest.mark.parametrize("name", sorted(events.TYPES))
def test_a_type_is_filed_under_its_name_and_lists_its_fields_once(name):
    kind = events.TYPES[name]
    assert kind.name == name
    assert kind.fields
    assert len(set(kind.fields)) == len(kind.fields)
    assert set(kind.required) <= set(kind.fields)


@pytest.mark.parametrize("name", sorted(events.TYPES))
def test_no_type_has_a_field_named_like_an_envelope_key(name):
    assert not set(events.TYPES[name].fields) & set(events.ENVELOPE)


def test_an_opening_with_its_required_fields_passes():
    events.check("round.opened", OPENED)
    events.check("round.opened", {**OPENED, "parent": "proj-a/program",
                                  "inherits": ["S1"], "record": "comms:12"})


def test_an_unknown_type_is_refused():
    with pytest.raises(ValueError, match="'round.started' is not an event type"):
        events.check("round.started", {})


def test_a_field_the_type_does_not_list_is_refused():
    with pytest.raises(ValueError, match="round.opened holds no field named colour"):
        events.check("round.opened", {**OPENED, "colour": "red"})


@pytest.mark.parametrize("key", events.ENVELOPE)
def test_a_field_named_like_an_envelope_key_is_refused(key):
    with pytest.raises(ValueError, match=f"{key} is set by the log"):
        events.check("round.opened", {**OPENED, key: 1})


@pytest.mark.parametrize("key", ["description", "flow", "steps"])
def test_an_opening_without_a_required_field_is_refused(key):
    fields = {name: value for name, value in OPENED.items() if name != key}
    with pytest.raises(ValueError, match=f"round.opened needs {key}"):
        events.check("round.opened", fields)
