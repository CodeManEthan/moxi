"""The remove refusal rounds gives projects: a project stays while any build of any
round, open, finished or dropped, has it as its repo."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moxi_rounds import log, opening, projects_link

ROUND = "proj-a/a-round"
BASE = "0" * 40


@pytest.fixture
def round_log(tmp_path, add_project, capsys):
    """`proj-a` with a rounds folder, `proj-b` beside it, and a round of `proj-a` that
    declared a stage in `proj-b`. Returns the round's log."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    add_project("proj-b")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared", {"id": "B1", "repo": "proj-b", "branch": "work",
                                        "after": []}, by="planner")
    capsys.readouterr()
    return path


def commission(path, stage="B1"):
    log.append(path, "build.commissioned", {
        "stage": stage, "repo": "proj-b", "branch": "work", "base": BASE,
        "target": "main", "builder": "coder"}, by="planner")


def remove(key, capsys) -> tuple[int, str]:
    from moxi import cli

    code = cli.main(["projects", "remove", key])
    said = capsys.readouterr()
    return code, said.out + said.err


def test_a_project_no_build_names_is_removed(round_log, capsys):
    assert projects_link.refuse_remove(SimpleNamespace(key="proj-b")) is None
    assert remove("proj-b", capsys)[0] == 0


@pytest.mark.parametrize("ending", [None, "build.dropped", "finish.done"])
def test_a_project_a_build_names_is_not_removed_open_or_ended(round_log, capsys, ending):
    # Core's project unregister refused while any round build row, open or closed,
    # named the project as its repo, with the count and the rounds.
    commission(round_log)
    if ending is not None:
        log.append(round_log, ending, {"stage": "B1"}, by="planner")

    code, said = remove("proj-b", capsys)

    assert code != 0
    assert "project proj-b is the repo of 1 build of proj-a/a-round" in said
    assert remove("proj-b", capsys)[0] == code


def test_the_refusal_counts_every_build_of_every_round(round_log, capsys):
    commission(round_log)
    log.append(round_log, "build.dropped", {"stage": "B1"}, by="planner")
    commission(round_log)

    found = projects_link.refuse_remove(SimpleNamespace(key="PROJ-B"))

    assert found.startswith("project PROJ-B is the repo of 2 builds of proj-a/a-round,")


def test_a_round_log_that_cannot_be_read_refuses_and_is_named(round_log, capsys):
    with round_log.open("a", encoding="utf-8") as fh:
        fh.write("not an event\n")

    found = projects_link.refuse_remove(SimpleNamespace(key="proj-b"))

    assert found.startswith("the round log of proj-a/a-round can't be read")
