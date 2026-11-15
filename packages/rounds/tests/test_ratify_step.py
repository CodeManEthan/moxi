"""The `ratify` step: ready while a major decision that reached the target is not
ruled, done once none is left, and the project's `ratify` setting read from what the
caller read of the round, as every reader of the step reads it."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import checking, closing, log, opening, ruling, state, steps
from moxi_rounds import settings as own
from moxi_rounds.cli import main

ROUND = "proj-a/a-round"
PTR = "comms:1234567"
BASE = "0" * 40
TIP = "1" * 40


@pytest.fixture
def folder(tmp_path, add_project, write_config, capsys):
    """`a-round` of `proj-a`, opened on the `fix` flow."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    write_config("")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds" / "a-round"


def built(folder, stage="B1", *, finished=True, decisions=()):
    """`stage` declared and commissioned through the log, with a build report listing
    `decisions` (`(id, weight)`), and finished when `finished`."""
    path = log.round_log(folder)
    log.append(path, "stage.declared",
               {"id": stage, "repo": "proj-a", "branch": f"work-{stage.lower()}",
                "after": []}, actor="planner")
    log.append(path, "build.commissioned",
               {"stage": stage, "repo": "proj-a", "branch": f"work-{stage.lower()}",
                "base": BASE, "target": "main", "builder": "coder",
                "build": "0123456789abcdef"}, actor="planner")
    listed = [{"id": id_, "text": "a choice", "weight": weight, "forecloses": False}
              for id_, weight in decisions]
    log.append(path, "verdict.recorded",
               {"kind": "build", "verdict": "publishable", "pass": "full",
                "stage": stage, "head": TIP, "by": "critic", "decisions": listed},
               actor="critic")
    if finished:
        log.append(path, "finish.done", {"stage": stage, "tip": TIP}, actor="coder")


def folded(folder):
    return state.fold(log.read(log.round_log(folder)))


def seen(folder):
    return checking.seen_of(folded(folder), ROUND, settings=own.load())


def ratify(folder, *, read=True) -> steps.Step:
    found = folded(folder)
    return steps.one(found, "ratify", name=ROUND, seen=seen(folder) if read else None)


def rule(item, **more):
    return ruling.rule(ROUND, item=item, record=PTR, actor="planner", **more)


def test_ratify_is_not_due_while_a_stage_of_the_plan_is_not_finished(folder):
    built(folder, finished=False, decisions=[("M1", "major")])
    assert ratify(folder).state == steps.NOT_DUE


def test_ratify_is_ready_while_a_major_decision_that_reached_the_target_is_unruled(
        folder):
    built(folder, decisions=[("M1", "major"), ("M2", "minor")])

    found = ratify(folder)

    assert found.state == steps.READY
    assert [(one.reason, one.fix) for one in found.waits] == [
        ("decision B1.M1, major and not yet ruled: a choice",
         f"moxi rounds rule {ROUND} B1.M1=accept|reject --record PTR")]
    assert steps.current(folded(folder), name=ROUND, seen=seen(folder)) == found


def test_ratify_is_done_once_every_such_decision_is_ruled(folder):
    built(folder, decisions=[("M1", "major")])
    rule("B1.M1=accept")
    assert ratify(folder).state == steps.DONE
    assert folded(folder).status == "closed"


def test_a_minor_decision_alone_leaves_ratify_done_once_the_plan_is_finished(folder):
    built(folder, decisions=[("M1", "minor")])
    assert ratify(folder).state == steps.DONE


def test_a_decision_of_a_build_not_finished_has_not_reached_the_target(folder):
    built(folder, "B1", decisions=[("M1", "major")])
    built(folder, "B2", finished=False, decisions=[("M1", "major")])

    found = ratify(folder)

    assert found.state == steps.READY
    assert [one.reason.split(",")[0] for one in found.waits] == ["decision B1.M1"]


def test_the_ratify_setting_reaches_the_row_through_what_the_caller_read(
        folder, write_config):
    built(folder, decisions=[("M1", "major")])
    assert seen(folder)[None].ratify == "majors"

    write_config('[defaults]\nratify = "off"\n')

    assert seen(folder)[None].ratify == "off"
    assert ratify(folder).state == steps.DONE


def test_ratify_with_nothing_read_waits_on_a_read_where_a_decision_makes_it_due(folder):
    built(folder, decisions=[("M1", "major")])

    found = ratify(folder, read=False)

    assert found.state == steps.WAITING and found.unread
    assert found.waits[0].fix == f"moxi rounds next {ROUND}"


def test_ratify_with_nothing_read_and_nothing_unruled_needs_no_read(folder):
    built(folder, decisions=[("M1", "minor")])
    found = ratify(folder, read=False)
    assert found.state == steps.DONE and not found.unread


def test_a_skip_of_ratify_reads_skipped(folder):
    built(folder, decisions=[("M1", "major")])
    rule("skip:ratify")
    assert ratify(folder).state == steps.SKIPPED


def test_the_row_and_the_close_wait_for_the_same_decision(folder):
    built(folder, decisions=[("M1", "major")])
    now = folded(folder)
    assert ratify(folder).state == steps.READY
    assert closing.waits(now) == ("decision B1.M1, major and not yet ruled",)


def test_next_gives_ratify_as_the_operator_s_step_with_the_command_that_rules(
        folder, capsys):
    built(folder, decisions=[("M1", "major")])
    capsys.readouterr()

    assert main(["next", ROUND, "--json"]) == 0
    said = json.loads(capsys.readouterr().out)

    group = said["groups"][0]
    assert (group["step"], group["state"], group["by"], group["built"]) == (
        "ratify", "ready", "operator", True)
    assert group["waits"] == [
        {"reason": "decision B1.M1, major and not yet ruled: a choice",
         "fix": f"moxi rounds rule {ROUND} B1.M1=accept|reject --record PTR"}]
