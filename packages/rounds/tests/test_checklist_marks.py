"""Checklist marks: a design's block and a fix round's opening say which items of the
round's projects' checklists apply, and the config check of each mark."""

from __future__ import annotations

import pytest

from moxi_rounds import brake, checklist, freezing, log, opening, state
from moxi_rounds.cli import main
from moxi_rounds.errors import BadBlock, Refused

ROUND = "proj-a/a-round"
FIX = "proj-a/a-fix"

CONFIG = """\
    [projects.proj-a]
    checklist = [
      { id = "public-surface", text = "Adds a public command.", skills = ["layout-review"] },
      { id = "ships-default", text = "Ships a default.", skills = ["layout-review", "kit:naming"] },
    ]

    [projects.proj-b]
    checklist = [
      { id = "core-code", text = "Adds code to the core.", skills = ["core-review"] },
    ]
    """


@pytest.fixture(name="round_dir")
def round_dir_fixture(tmp_path, add_project, write_config, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    add_project("proj-b", notes_root=tmp_path / "notes-b")
    add_project("proj-c", notes_root=tmp_path / "notes-c")
    write_config(CONFIG)
    opening.open_round(ROUND, description="a round", by="planner", flow="full")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds" / "a-round"


def design(round_dir, top: str | None, *, repo: str = "proj-a") -> None:
    head = "" if top is None else f"checklist = {top}\n\n"
    text = (f"# A design\n\n```toml rounds\n{head}[[stage]]\nid = \"B1\"\n"
            f"repo = \"{repo}\"\nbranch = \"b1\"\nafter = []\n```\n")
    (round_dir / "design.md").write_text(text, encoding="utf-8")


def fold(**more):
    return freezing.freeze(ROUND, by="scout", **more)


def declared(round_dir) -> dict:
    return log.read(log.round_log(round_dir))[-1]["declared"]


def refused_with(round_dir, top, *, repo="proj-a") -> str:
    design(round_dir, top, repo=repo)
    with pytest.raises(BadBlock) as caught:
        fold()
    return str(caught.value)


# -- in a design's block -----------------------------------------------------------------

def test_a_fold_keeps_the_marks_bare_and_of_a_stages_repo(round_dir):
    design(round_dir, '["public-surface", "proj-b:core-code"]', repo="proj-b")
    fold()
    assert declared(round_dir)["checklist"] == ["public-surface", "proj-b:core-code"]
    found = state.fold(log.read(log.round_log(round_dir)))
    assert checklist.marks_of(found) == checklist.Marks(
        ("public-surface", "proj-b:core-code"), "v1")


def test_an_empty_list_marks_none(round_dir):
    design(round_dir, "[]")
    fold()
    assert declared(round_dir)["checklist"] == []


def test_a_block_that_says_nothing_is_refused_while_a_weighed_checklist_is_not_empty(
        round_dir):
    said = refused_with(round_dir, None)
    assert "no `checklist` key" in said and "checklist = []" in said


def test_a_block_whose_projects_have_no_checklist_may_leave_the_key_out(
        round_dir, write_config):
    write_config("")
    design(round_dir, None)
    fold()
    assert declared(round_dir)["checklist"] == []


@pytest.mark.parametrize("top, repo, words", [
    ('["no-such"]', "proj-a", "proj-a's checklist has no item no-such"),
    ('["proj-a:public-surface"]', "proj-a", "write it bare: public-surface"),
    ('["proj-b:core-code"]', "proj-a", "neither the home project nor a repo a stage names"),
    ('["proj-b:no-such"]', "proj-b", "proj-b's checklist has no item no-such"),
    ('["public-surface", "public-surface"]', "proj-a", "twice"),
    ('"public-surface"', "proj-a", "not a list of marks"),
])
def test_a_wrong_mark_is_refused_at_the_fold(round_dir, top, repo, words):
    assert words in refused_with(round_dir, top, repo=repo)
    assert not (round_dir / "versions").exists()


def test_a_block_that_says_nothing_is_refused_while_only_a_stage_repo_has_a_checklist(
        round_dir):
    opening.open_round("proj-c/a-round", description="a round", by="planner",
                       flow="full")
    other = round_dir.parent.parent.parent / "notes-c" / "rounds" / "a-round"
    text = ("# A design\n\n```toml rounds\n[[stage]]\nid = \"B1\"\n"
            "repo = \"proj-b\"\nbranch = \"b1\"\nafter = []\n```\n")
    (other / "design.md").write_text(text, encoding="utf-8")

    with pytest.raises(BadBlock) as caught:
        freezing.freeze("proj-c/a-round", by="scout")

    assert "no `checklist` key" in str(caught.value)
    assert "checklist of proj-b is not empty" in str(caught.value)


def test_a_version_frozen_with_no_key_marks_none_not_the_opening_s(round_dir,
                                                                    write_config):
    open_fix(("public-surface",))
    fix_dir = round_dir.parent / "a-fix"
    write_config("")
    (fix_dir / "design.md").write_text(
        "# A design\n\n```toml rounds\n[[stage]]\nid = \"B1\"\nrepo = \"proj-a\"\n"
        "branch = \"b1\"\nafter = []\n```\n", encoding="utf-8")
    freezing.freeze(FIX, by="scout")
    events = log.read(log.round_log(fix_dir))
    for event in events:
        if event["type"] == "design.frozen":
            event["declared"].pop("checklist", None)

    assert checklist.marks_of(state.fold(events)) == checklist.Marks((), "v1")


def test_marks_compare_as_a_set_and_a_version_with_no_key_marks_none():
    assert brake.differences({"checklist": ["a", "b"]}, {"checklist": ["b", "a"]}) == []
    assert brake.differences({}, {"checklist": []}) == []
    assert brake.differences({"checklist": ["a"]}, {"checklist": ["a", "b"]}) == [
        "the checklist marks changed from a to a, b"]


def test_a_fold_that_changes_the_marks_is_no_change_of_records_only(round_dir):
    design(round_dir, "[]")
    fold()
    design(round_dir, '["public-surface"]')
    with pytest.raises(Refused) as caught:
        fold(records_only=True, record="comms:77")
    assert "the checklist marks changed from none to public-surface" in str(caught.value)
    design(round_dir, "[ ]")
    fold(records_only=True, record="comms:77")


# -- at a fix round's opening ------------------------------------------------------------

def open_fix(marks, **more):
    return opening.open_round(FIX, description="a fix", by="lead", flow="fix",
                              checklist=marks, **more)


def test_a_fix_round_keeps_its_marks_on_the_opening(round_dir):
    opened = open_fix(("public-surface", "proj-b:core-code", "public-surface"))
    assert opened.event["marked"] == ["public-surface", "proj-b:core-code"]
    found = state.fold([opened.event])
    assert checklist.marks_of(found) == checklist.Marks(
        ("public-surface", "proj-b:core-code"), "opening")


def test_none_marks_nothing(round_dir):
    assert open_fix(()).event["marked"] == []


def test_a_fix_round_that_says_nothing_is_refused_while_its_home_has_a_checklist(round_dir):
    with pytest.raises(Refused) as caught:
        open_fix(None)
    assert "--checklist LIST|none" in str(caught.value)
    assert not (round_dir.parent / "a-fix").exists()


def test_a_fix_round_in_a_home_with_no_checklist_may_say_nothing(round_dir):
    opened = opening.open_round("proj-c/a-fix", description="a fix", by="lead", flow="fix")
    assert "marked" not in opened.event


@pytest.mark.parametrize("marks, words", [
    (("no-such",), "proj-a's checklist has no item no-such"),
    (("proj-a:public-surface",), "write it bare"),
    (("proj-z:core-code",), "no project of rounds' config"),
    (("proj-b:no-such",), "proj-b's checklist has no item no-such"),
])
def test_a_wrong_mark_is_refused_at_the_opening(round_dir, marks, words):
    with pytest.raises(Refused) as caught:
        open_fix(marks)
    assert words in str(caught.value)


def test_marks_on_a_round_with_a_design_step_are_refused(round_dir):
    with pytest.raises(Refused) as caught:
        opening.open_round("proj-a/another", description="x", by="lead", flow="full",
                           checklist=("public-surface",))
    assert "checklist = [...]" in str(caught.value)


def test_none_on_a_round_with_a_design_step_is_refused_too(round_dir):
    with pytest.raises(Refused) as caught:
        opening.open_round("proj-a/another", description="x", by="lead", flow="full",
                           checklist=())
    assert "checklist = [...]" in str(caught.value)
    assert not (round_dir.parent / "another").exists()


def test_the_command_takes_the_marks_and_none(round_dir, capsys):
    assert main(["open", FIX, "--description", "a fix", "--flow", "fix",
                 "--checklist", "none"]) == 0
    assert "checklist: none marked" in capsys.readouterr().out
    assert checklist.parse("a, b,a") == ("a", "b")
    assert checklist.parse(" , ") is None
