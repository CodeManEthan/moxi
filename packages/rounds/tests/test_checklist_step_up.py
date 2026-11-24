"""A round with no design step whose opening marks a checklist item steps up to the full
flow: the two events it writes, the refusal when that is no step up, and how the step up
shows in `open`, `next`, `show`, the round note and the numbers."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import flows, log, next_step, numbers, opening, showing, state, stepping
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

FIX = "proj-a/a-fix"

CONFIG = """\
    [projects.proj-a]
    checklist = [
      { id = "public-surface", text = "Adds a public command.", skills = ["layout-review"] },
    ]

    [projects.proj-b]
    checklist = [
      { id = "core-code", text = "Adds code to the core.", skills = ["core-review"] },
    ]
    """


@pytest.fixture(name="notes")
def notes_fixture(tmp_path, add_project, write_config):
    add_project("proj-a", notes_root=tmp_path / "notes")
    add_project("proj-b", notes_root=tmp_path / "notes-b")
    write_config(CONFIG)
    return tmp_path / "notes"


def events(notes) -> list[dict]:
    return log.read(log.round_log(notes / "rounds" / "a-fix"))


def open_fix(marks):
    return opening.open_round(FIX, description="a fix", by="lead", flow="fix",
                              checklist=marks)


def test_a_mark_steps_the_round_up_to_full_in_the_same_append(notes):
    opened = open_fix(("proj-b:core-code",))

    written = events(notes)
    assert [one["type"] for one in written] == ["round.opened", "flow.changed"]
    assert (written[0]["flow"], written[0]["marked"]) == ("fix", ["proj-b:core-code"])
    changed = written[1]
    assert (changed["was"], changed["flow"], changed["by"], changed["marked"]) == (
        "fix", "full", stepping.LEAD, ["proj-b:core-code"])
    assert changed["steps"] == flows.recorded(flows.BUILT_IN["full"])
    assert opened.stepped == changed
    found = state.fold(written)
    assert found.flow == "full"


def test_none_steps_nothing_up(notes):
    opened = open_fix(())

    assert [one["type"] for one in events(notes)] == ["round.opened"]
    assert opened.stepped is None


def test_marks_whose_move_to_full_is_no_step_up_refuse_the_opening_whole(notes, write_config):
    write_config('    tiers = ["full", "small", "fix"]\n' + CONFIG)

    with pytest.raises(Refused) as caught:
        open_fix(("public-surface",))

    said = str(caught.value)
    assert "full comes before fix in the tiers" in said
    assert "open it with --flow NAME, a flow with a design step" in said
    assert not (notes / "rounds" / "a-fix").exists()


def test_open_says_the_step_up(notes, capsys):
    assert main(["open", FIX, "--description", "a fix", "--flow", "fix",
                 "--checklist", "public-surface"]) == 0

    out = capsys.readouterr().out
    assert ("stepped up to full (design, verify-design, build, verify-build, finish, "
            "retro): the checklist marks public-surface") in out


def test_next_says_the_step_up_in_the_design_group_and_the_opening_s_marks(notes, capsys):
    open_fix(("public-surface",))

    assert main(["next", FIX]) == 0
    text = capsys.readouterr().out
    assert main(["next", FIX, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert next_step.as_text(data) == text

    group = data["groups"][0]
    assert (data["flow"], group["step"]) == ("full", "design")
    assert group["stepped"] == {"was": "fix", "marked": ["public-surface"]}
    lines = text.splitlines()
    assert lines[1] == ("stepped up: from fix at its opening, by the checklist marks "
                        "public-surface")
    assert "  the opening marks public-surface: load layout-review" in lines
    assert group["checklist"]["from"] == "opening"


def test_show_and_the_note_give_the_marks_with_their_source_and_the_history_line(
        notes, capsys):
    open_fix(("public-surface",))

    found = showing.show(FIX)
    data = showing.data(found)
    assert data["checklist"] == {"marked": ["public-surface"], "from": "opening"}
    text = showing.as_text(found)
    assert "checklist: public-surface (from the opening)" in text
    assert ("flow.changed  flow fix -> full, by the lead at its opening, for the "
            "checklist marks public-surface") in text
    note = (notes / "rounds" / "a-fix" / "round-note.md").read_text(encoding="utf-8")
    assert "checklist marks: public-surface (from the opening)" in note
    assert ("flow fix -> full, by the lead at its opening, for the checklist marks "
            "public-surface") in note


def test_a_round_that_marked_nothing_shows_no_marks(notes):
    opening.open_round("proj-a/a-round", description="a round", by="lead", flow="full")

    assert showing.data(showing.show("proj-a/a-round"))["checklist"] is None


def test_the_numbers_count_the_checklist_s_step_ups_apart_from_the_lead_s(notes):
    open_fix(("public-surface",))
    opening.open_round("proj-a/other", description="x", by="lead", flow="fix",
                       checklist=())
    stepping.step_up("proj-a/other", "full", by="lead")

    marked = numbers.of(state.fold(events(notes)))
    by_lead = numbers.of(state.fold(log.read(log.round_log(notes / "rounds" / "other"))))

    assert (marked["step_ups"], marked["checklist_step_ups"]) == (0, 1)
    assert (by_lead["step_ups"], by_lead["checklist_step_ups"]) == (1, 0)
    assert any("step-ups 0, checklist step-ups 1," in line
               for line in numbers.lines(marked))
    assert any("checklist step-ups 0," in line for line in numbers.lines({}))
