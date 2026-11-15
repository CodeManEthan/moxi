"""The `retro` step: due once the round's close waits for nothing else, done once a
retro report is recorded, and the round closing on that record."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import checking, closing, log, opening, ruling, state, steps, verdicts
from moxi_rounds import settings as own
from moxi_rounds.cli import main

ROUND = "proj-a/a-round"
PTR = "comms:1234567"
BASE = "0" * 40
TIP = "1" * 40

FLOWS = """\
[flows.built]
steps = [ { step = "build", by = "coder" },
          { step = "finish", by = "coder" },
          { step = "retro", by = "helper", reads = ["what went wrong"] } ]
"""


@pytest.fixture
def folder(tmp_path, add_project, write_config, capsys):
    """`a-round` of `proj-a`, opened on a flow that builds and ends in a retro."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    write_config(FLOWS)
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="built")
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


def retro(folder, *, read=True) -> steps.Step:
    return steps.one(folded(folder), "retro", name=ROUND,
                     seen=seen(folder) if read else None)


def report(folder, name="retro-1.md") -> str:
    path = folder / name
    path.write_text("# What went wrong\n\n```toml rounds\nkind = \"retro\"\n"
                    "by = \"helper\"\nmodel = \"a-model\"\n```\n", encoding="utf-8")
    return str(path)


def rule(item, **more):
    return ruling.rule(ROUND, item=item, record=PTR, actor="planner", **more)


def test_retro_is_not_due_while_the_close_waits_for_something_else(folder):
    built(folder, finished=False)
    assert retro(folder).state == steps.NOT_DUE
    assert "step retro, not-due" in closing.waits(folded(folder))


def test_retro_is_ready_once_the_close_waits_for_nothing_else(folder):
    built(folder)

    found = retro(folder)

    assert found.state == steps.READY and found.waits == ()
    assert steps.current(folded(folder), name=ROUND, seen=seen(folder)) == found
    assert closing.waits(folded(folder)) == ("step retro, ready",)
    assert closing.waits_before_retro(folded(folder)) == ()
    assert folded(folder).status == "open"


def test_the_round_closes_on_the_retro_report(folder, capsys):
    built(folder)

    assert main(["verdict", ROUND, report(folder)]) == 0

    after = folded(folder)
    assert [one["type"] for one in after.history[-2:]] == [
        "verdict.recorded", "round.closed"]
    assert after.status == "closed"
    assert steps.one(after, "retro").state == steps.DONE


def test_a_retro_recorded_before_it_is_due_counts_once_the_rest_is_done(folder):
    built(folder, finished=False)
    verdicts.record(ROUND, report(folder), actor="helper")
    assert retro(folder).state == steps.DONE
    assert folded(folder).status == "open"

    log.append(log.round_log(folder), "finish.done", {"stage": "B1", "tip": TIP},
               actor="coder")
    rule(None, text="a placeholder ruling")

    assert folded(folder).status == "closed"


def test_an_unruled_major_decision_keeps_retro_not_due_until_it_is_ruled(folder):
    built(folder, decisions=[("M1", "major")])
    assert retro(folder).state == steps.NOT_DUE

    rule("B1.M1=accept")

    assert retro(folder).state == steps.READY


def test_with_ratify_off_the_decision_keeps_nothing_back(folder, write_config):
    built(folder, decisions=[("M1", "major")])
    write_config(FLOWS + '\n[defaults]\nratify = "off"\n')
    assert retro(folder).state == steps.READY


def test_with_the_settings_not_read_retro_waits_on_that_read_where_they_decide(folder):
    built(folder, decisions=[("M1", "major")])

    found = retro(folder, read=False)

    assert (found.state, found.unread) == (steps.WAITING, True)
    assert found.waits[0].fix == f"moxi rounds next {ROUND}"


def test_with_the_settings_not_read_retro_is_ready_where_they_decide_nothing(folder):
    built(folder)
    assert retro(folder, read=False).state == steps.READY


def test_next_gives_the_retro_its_reads_its_block_its_verdict_and_the_numbers(
        folder, capsys):
    built(folder)

    assert main(["next", ROUND, "--json"]) == 0
    [group] = json.loads(capsys.readouterr().out)["groups"]
    assert main(["next", ROUND]) == 0
    text = capsys.readouterr().out

    assert (group["step"], group["state"], group["by"]) == ("retro", "ready", "helper")
    assert group["reads"] == ["the numbers below", str(folder / "round-note.md"),
                              f"the reports in {folder}", "what went wrong"]
    assert group["write"] == (f"a report in {folder} with the block from: moxi rounds "
                              "next --schema retro")
    assert group["then"] == [f"moxi rounds verdict {ROUND} <your report>"]
    assert group["numbers"]["design_passes"] == 0
    assert "\nnumbers:\n  design: passes 0, " in text
    assert "\n  time in steps: " in text


def test_a_skip_of_retro_lets_the_round_close(folder):
    built(folder)
    rule("skip:retro")
    assert folded(folder).status == "closed"
    assert steps.one(folded(folder), "retro").state == steps.SKIPPED
