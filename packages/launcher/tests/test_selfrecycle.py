"""Whether a launch's session can recycle itself: each item of the one test, in order, on
slot, terminal, Codex, scratch, no-notes and pre-build rows."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from moxi import modules

from moxi_launcher import handoffs, selfrecycle


@pytest.fixture(autouse=True)
def without_notes(monkeypatch):
    """notes not installed, so the launcher keeps the handoffs; a test that means notes
    installed says so itself."""
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "notes" and real(name))


def row(*, harness="claude", target="slot", slot="3", **values):
    values = {"recycle_at_limit": "relaunch", "warn_at": "2x", **values}
    return SimpleNamespace(harness=harness, target=target, slot=slot, cwd="/work",
                           values={k: v for k, v in values.items() if v is not None})


def item(found_row, *, slots_here=True):
    found = selfrecycle.missing_of_row(found_row, slots_here=lambda: slots_here)
    return None if found is None else found.item


def test_a_claude_slot_launch_warned_at_a_mark_can_recycle_itself():
    assert item(row()) is None


@pytest.mark.parametrize("value, sources, expected", [
    # Made before relaunch was the default: the off it never chose.
    ("off", None, None),
    ("off", {"recycle_at_limit": "shipped"}, None),
    # Chosen off, or compact, from any other layer opts out.
    ("off", {"recycle_at_limit": "typed"}, selfrecycle.OPTED_OUT),
    ("off", {"recycle_at_limit": "preset"}, selfrecycle.OPTED_OUT),
    ("off", {"recycle_at_limit": "defaults"}, selfrecycle.OPTED_OUT),
    ("compact", None, selfrecycle.OPTED_OUT),
    (None, None, selfrecycle.OPTED_OUT),
])
def test_what_a_row_is_set_to_and_where_it_came_from(value, sources, expected):
    assert item(row(recycle_at_limit=value, sources=sources)) == expected


@pytest.mark.parametrize("found_row, expected", [
    (row(harness="codex"), selfrecycle.HARNESS),
    (row(target="terminal", slot=None), selfrecycle.SLOT),
    (row(target="headless", slot=None), selfrecycle.SLOT),
    (row(slot=None), selfrecycle.SLOT),
    (row(scratch=True), selfrecycle.SCRATCH),
    (row(warn_at="off"), selfrecycle.NO_MARK),
    (row(warn_at=""), selfrecycle.NO_MARK),
    (row(warn_at=None), selfrecycle.NO_MARK),
])
def test_each_launch_that_cannot_recycle_itself_names_its_item(found_row, expected):
    assert item(found_row) == expected


def test_a_slot_launch_where_slots_do_not_run_cannot():
    assert item(row(), slots_here=False) == selfrecycle.SLOTS


def test_a_launch_with_no_notes_dir_has_no_handoff_folder(monkeypatch):
    def homeless(cwd, **kw):
        raise handoffs.NoNotesDir(cwd)

    monkeypatch.setattr(handoffs, "handoff_dir", homeless)
    found = selfrecycle.missing_of_row(row(), slots_here=lambda: True)
    assert found.item == selfrecycle.HANDOFFS
    assert found.problem == ("has no notes directory (no notes_dir and none for /work), so "
                             "it has no handoffs/ to read from")


def test_the_handoff_folder_is_looked_up_without_being_made(monkeypatch):
    asked = []
    monkeypatch.setattr(handoffs, "handoff_dir", lambda cwd, **kw: asked.append(kw))
    assert item(row(notes_dir="/n")) is None
    assert asked == [{"notes_dir": "/n", "env": None, "create": False, "create_own": False}]


def test_an_off_the_launcher_held_names_the_item_that_made_it_so():
    # The shipped relaunch held off on a terminal launch, quietly: the arm names why.
    left = {"recycle_at_limit": "left-out"}
    assert item(row(recycle_at_limit="off", sources=left, target="terminal",
                    slot=None)) == selfrecycle.SLOT
    assert item(row(recycle_at_limit="off", sources=left)) == selfrecycle.OPTED_OUT


def test_a_relaunch_launch_holds_by_default_only_on_the_off_it_never_chose():
    assert selfrecycle.holds_by_default(row(recycle_at_limit="off"), slots_here=lambda: True)
    assert not selfrecycle.holds_by_default(row(), slots_here=lambda: True)
    assert not selfrecycle.holds_by_default(row(recycle_at_limit="off", warn_at="off"),
                                            slots_here=lambda: True)
