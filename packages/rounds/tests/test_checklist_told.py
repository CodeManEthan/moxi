"""What rounds tells each step of a round's checklist marks: `next`'s design group, the
design's and a build's verification, the commission block, and the `verifier_brief`
lines a verifying group prints."""

from __future__ import annotations

import json
import shlex
import sys

import pytest

from moxi_rounds import (checking, checklist, commissioning, freezing, next_step, opening,
                         ruling)
from moxi_rounds.cli import main

ROUND = "proj-a/a-round"
FIX = "proj-a/a-fix"
PTR = "comms:1234567"

CHECKLISTS = """\
    [projects.proj-a]
    checklist = [
      { id = "public-surface", text = "Adds a public command.", skills = ["layout-review"] },
      { id = "ships-default", text = "Ships a default.", skills = ["layout-review", "kit:naming"] },
    ]
    verifier_brief = ["Review check: a home path is a finding."]

    [projects.proj-b]
    checklist = [
      { id = "core-code", text = "Adds code to the core.", skills = ["core-review"] },
    ]
    verifier_brief = ["Review check: proj-b's own."]
    brief = ["Builder line of proj-b."]
    """


@pytest.fixture(name="round_dir")
def round_dir_fixture(tmp_path, add_project, write_config, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    add_project("proj-b", notes_root=tmp_path / "notes-b")
    add_project("proj-c", notes_root=tmp_path / "notes-c")
    write_config(CHECKLISTS)
    opening.open_round(ROUND, description="a round", by="planner", flow="full")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds" / "a-round"


def fold(round_dir, top: str, *, repo: str = "proj-a", name: str = ROUND) -> None:
    text = (f"# A design\n\n```toml rounds\nchecklist = {top}\n\n[[stage]]\nid = \"B1\"\n"
            f"repo = \"{repo}\"\nbranch = \"b1\"\nafter = []\n```\n")
    (round_dir / "design.md").write_text(text, encoding="utf-8")
    freezing.freeze(name, by="scout")


def printed(capsys, name: str = ROUND) -> tuple[list[str], dict]:
    """What `next` prints for the round, in lines, and its first group's data; the
    lines are made from the data."""
    assert main(["next", name]) == 0
    text = capsys.readouterr().out
    assert main(["next", name, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert next_step.as_text(data) == text
    return text.splitlines(), data["groups"][0]


# -- the design and its verification -----------------------------------------------------

def test_the_design_group_lists_the_home_project_s_items_to_mark(round_dir, capsys):
    lines, group = printed(capsys)

    assert group["step"] == "design"
    assert group["checklist"] == {
        "marked": [], "from": None, "skills": [], "gone": [],
        "items": [{"id": "public-surface", "text": "Adds a public command.",
                   "skills": ["layout-review"]},
                  {"id": "ships-default", "text": "Ships a default.",
                   "skills": ["layout-review", "kit:naming"]}]}
    start = lines.index("checklist: mark the items that apply in the block's checklist "
                        "= [...]")
    assert lines[start - 1].startswith("then:  moxi rounds fold")
    assert lines[start + 1:start + 4] == [
        "  public-surface: Adds a public command. (layout-review)",
        "  ships-default: Ships a default. (layout-review, kit:naming)",
        "  a stage in another project: mark its items as <key>:<id>"]
    assert "brief" not in group


def test_the_verify_group_gives_the_marks_their_skills_every_item_and_the_brief(
        round_dir, capsys):
    fold(round_dir, '["ships-default", "proj-b:core-code"]', repo="proj-b")

    lines, group = printed(capsys)

    assert group["step"] == "verify-design"
    said = group["checklist"]
    assert (said["marked"], said["from"], said["skills"]) == (
        ["ships-default", "proj-b:core-code"], "v1",
        ["layout-review", "kit:naming", "core-review"])
    assert [one["id"] for one in said["items"]] == [
        "public-surface", "ships-default", "proj-b:core-code"]
    assert group["brief"] == ["Review check: a home path is a finding.",
                              "Review check: proj-b's own."]
    start = lines.index("checklist: v1 marks ships-default, proj-b:core-code: load "
                        "layout-review, kit:naming, core-review")
    assert lines[start - 1].startswith("then:  moxi rounds verdict")
    assert lines[start + 1:start + 7] == [
        "  the items, to check the marks against:",
        "    public-surface: Adds a public command. (layout-review)",
        "    ships-default: Ships a default. (layout-review, kit:naming)",
        "    proj-b:core-code: Adds code to the core. (core-review)",
        "brief: Review check: a home path is a finding.",
        "brief: Review check: proj-b's own."]


def test_the_design_group_after_a_fold_says_the_marks(round_dir, capsys):
    fold(round_dir, '["public-surface"]')
    said = checklist.told(_state(round_dir), "proj-a", _settings(),
                          kind=checklist.DRAFTING)

    assert checklist.lines(said, checklist.DRAFTING)[:2] == [
        "checklist: mark the items that apply in the block's checklist = [...]",
        "  v1 marks public-surface: load layout-review"]
    assert not any("a stage in another project" in line
                   for line in checklist.lines(said, checklist.DRAFTING))


def test_a_mark_no_longer_in_the_checklist_is_said_so_and_names_no_skill(
        round_dir, write_config, capsys):
    fold(round_dir, '["public-surface", "ships-default"]')
    write_config(CHECKLISTS.replace(
        '      { id = "public-surface", text = "Adds a public command.", skills = '
        '["layout-review"] },\n', ""))

    lines, group = printed(capsys)

    assert group["checklist"]["gone"] == ["public-surface"]
    assert group["checklist"]["skills"] == ["layout-review", "kit:naming"]
    assert ("checklist: v1 marks public-surface (no longer in the checklist), "
            "ships-default: load layout-review, kit:naming") in lines


def test_a_round_whose_projects_have_no_checklist_and_no_brief_is_told_nothing(
        round_dir, write_config, capsys):
    write_config("")
    lines, group = printed(capsys)
    assert "checklist" not in group and "brief" not in group
    fold(round_dir, "[]")

    lines, group = printed(capsys)

    assert group["step"] == "verify-design"
    assert "checklist" not in group and "brief" not in group
    assert not any(line.startswith(("checklist:", "brief:")) for line in lines)


def test_a_brief_with_no_checklist_still_prints(round_dir, write_config, capsys):
    write_config('[projects.proj-a]\nverifier_brief = ["Review check: one."]\n')
    fold(round_dir, "[]")

    lines, group = printed(capsys)

    assert "checklist" not in group
    assert "brief: Review check: one." in lines


def test_none_marked_says_none(round_dir, capsys):
    fold(round_dir, "[]")

    lines, _group = printed(capsys)

    assert "checklist: v1 marks none" in lines


def _state(round_dir):
    from moxi_rounds import log, state

    return state.fold(log.read(log.round_log(round_dir)))


def _settings():
    from moxi_rounds import settings

    return settings.load()


# -- a build -----------------------------------------------------------------------------

@pytest.fixture
def repos(git_repo, tmp_path, add_project, write_config):
    """proj-a, the home, a plain folder; proj-b, whose root is the test's git repo with
    a check; both with checklists."""
    from moxi import cli

    add_project("proj-a", notes_root=tmp_path / "notes")
    assert cli.main(["projects", "add", "proj-b", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-b", "--notes-root",
                     str(tmp_path / "notes-b")]) == 0
    command = shlex.join([sys.executable, "-c", "print('the check passed')"])
    write_config(CHECKLISTS + f"    check = {{ command = {json.dumps(command)} }}\n")
    return git_repo


def test_a_fix_round_building_in_a_project_it_did_not_weigh_lists_that_project_s_items(
        repos, capsys):
    opening.open_round(FIX, description="a fix", by="lead", flow="fix", checklist=())
    made = commissioning.commission(FIX, repo="proj-b", branch="work", builder="coder",
                                    by="lead")

    lines = list(made.block.lines)
    start = lines.index("checklist: none marked")
    assert lines[start + 1:start + 4] == [
        "checklist: proj-b's items were not weighed at this round's opening:",
        "  proj-b:core-code: Adds code to the core. (core-review)",
        "Builder line of proj-b."]

    type(repos)(made.worktree).commit("work.txt", "the work")
    checking.check(FIX, made.stage, by="coder")
    capsys.readouterr()
    text, group = printed(capsys, FIX)

    assert group["step"] == "verify-build"
    assert group["checklist"]["unweighed"] == {
        "project": "proj-b",
        "items": [{"id": "proj-b:core-code", "text": "Adds code to the core.",
                   "skills": ["core-review"]}]}
    assert group["brief"] == ["Review check: proj-b's own."]
    assert "checklist: proj-b's items were not weighed at this round's opening:" in text
    assert "brief: Review check: a home path is a finding." not in text


def test_a_design_round_s_build_is_told_its_marks_above_the_repo_s_brief(repos, tmp_path,
                                                                        capsys):
    name = "proj-b/a-round"
    opening.open_round(name, description="a round", by="planner", flow="full")
    fold(tmp_path / "notes-b" / "rounds" / "a-round", '["core-code"]', repo="proj-b",
         name=name)
    ruling.rule(name, item="skip:verify-design", record=PTR, by="planner", text=None)
    made = commissioning.commission(name, "B1", builder="coder", by="planner")

    lines = list(made.block.lines)
    assert lines[-2:] == ["checklist: marks core-code: load core-review",
                          "Builder line of proj-b."]
    capsys.readouterr()
    text, group = printed(capsys, name)
    assert group["step"] == "build"
    assert "checklist: marks core-code: load core-review" in text
    assert "unweighed" not in group["checklist"] and "brief" not in group
