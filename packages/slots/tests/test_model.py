"""The rules for numbers, names, labels, holders and meta, and launchability."""

from __future__ import annotations

import pytest

from moxi_slots import model
from moxi_slots.errors import UsageError
from moxi_slots.model import ENDED, FREE, MISSING, OCCUPIED, Hold

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/model.py")


def test_numbers_run_from_1_to_999():
    assert model.check_number(1) == 1 and model.check_number(999) == 999
    for bad in (0, 1000, -1, True, "3", 2.0):
        with pytest.raises(UsageError):
            model.check_number(bad)


def test_a_usage_error_is_a_value_error():
    with pytest.raises(ValueError):
        model.check_number(0)


def test_session_names_and_targets():
    assert model.session_name(7) == "slot-7"
    assert model.session_target(7) == "=slot-7"
    assert model.window_target(7) == "=slot-7:"
    assert model.number_of("slot-7") == 7 and model.number_of("slot-999") == 999
    for other in ("slot-0", "slot-07", "slot-1000", "slot-", "slot-x", "moxi-jobs",
                  "slot-1a"):
        assert model.number_of(other) is None


@pytest.mark.parametrize("name", ["review", "a", "x1", "my-slot-2", "a" * 32, "1a"])
def test_good_names(name):
    assert model.check_name(name) == name


@pytest.mark.parametrize("name", ["", "123", "Review", "a_b", "a b", "a" * 33, "é", 3,
                                  None])
def test_bad_names(name):
    with pytest.raises(UsageError):
        model.check_name(name)


def test_labels_are_up_to_64_printable_characters():
    assert model.check_label("agent · review") == "agent · review"
    assert model.check_label("") == ""
    for bad in ("a" * 65, "tab\there", "new\nline", 5):
        with pytest.raises(UsageError):
            model.check_label(bad)


def test_holders_are_one_printable_word():
    assert model.check_holder("relaunch-3f2a") == "relaunch-3f2a"
    for bad in ("", "two words", "x" * 129, "tab\t", None):
        with pytest.raises(UsageError):
            model.check_holder(bad)


def test_meta_is_a_json_object_of_at_most_4_kib():
    assert model.check_meta(None) is None
    assert model.check_meta({"session": "abc", "n": [1, 2]}) == {"session": "abc",
                                                                  "n": [1, 2]}
    assert model.check_meta({"pad": "x" * 4000}) is not None
    for bad in ([1], "x", {"pad": "x" * 4100}, {"f": object()}):
        with pytest.raises(UsageError):
            model.check_meta(bad)


def test_a_slot_is_launchable_when_free_or_ended_and_unheld_or_held_by_the_caller():
    held = Hold("run-1", 2e9)
    assert model.launchable(FREE, None) and model.launchable(ENDED, None)
    assert not model.launchable(OCCUPIED, None) and not model.launchable(MISSING, None)
    assert not model.launchable(FREE, held)
    assert model.launchable(FREE, held, "run-1")
    assert not model.launchable(FREE, held, "run-2")
    assert not model.launchable(OCCUPIED, held, "run-1")


def test_a_hold_reads_as_absent_past_until():
    hold = Hold("run-1", 100.0)
    assert hold.live(99.9) and not hold.live(100.0)
