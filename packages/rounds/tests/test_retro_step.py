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
def round_dir(tmp_path, add_project, write_config, capsys):
    """`a-round` of `proj-a`, opened on a flow that builds and ends in a retro."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    write_config(FLOWS)
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="built")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds" / "a-round"


def built(round_dir, stage="B1", *, finished=True, decisions=()):
    """`stage` declared and commissioned through the log, with a build report listing
    `decisions` (`(id, weight)`), and finished when `finished`."""
    path = log.round_log(round_dir)
    log.append(path, "stage.declared",
               {"id": stage, "repo": "proj-a", "branch": f"work-{stage.lower()}",
                "after": []}, by="planner")
    log.append(path, "build.commissioned",
               {"stage": stage, "repo": "proj-a", "branch": f"work-{stage.lower()}",
                "base": BASE, "target": "main", "builder": "coder",
                "build": "0123456789abcdef"}, by="planner")
    listed = [{"id": id_, "text": "a choice", "weight": weight, "forecloses": False}
              for id_, weight in decisions]
    log.append(path, "verdict.recorded",
               {"kind": "build", "verdict": "publishable", "pass": "full",
                "stage": stage, "head": TIP, "by": "critic", "decisions": listed},
               by="critic")
    if finished:
        log.append(path, "finish.done", {"stage": stage, "tip": TIP}, by="coder")


def folded(round_dir):
    return state.fold(log.read(log.round_log(round_dir)))


def seen(round_dir):
    return checking.seen_of(folded(round_dir), ROUND, settings=own.load())


def retro(round_dir, *, read=True) -> steps.Step:
    return steps.one(folded(round_dir), "retro", name=ROUND,
                     seen=seen(round_dir) if read else None)


def report(round_dir, name="retro-1.md") -> str:
    path = round_dir / name
    path.write_text("# What went wrong\n\n```toml rounds\nkind = \"retro\"\n"
                    "by = \"helper\"\nmodel = \"a-model\"\n```\n", encoding="utf-8")
    return str(path)


def rule(item, **more):
    return ruling.rule(ROUND, item=item, record=PTR, by="planner", **more)


def test_retro_is_not_due_while_the_close_waits_for_something_else(round_dir):
    built(round_dir, finished=False)
    assert retro(round_dir).state == steps.NOT_DUE
    assert "step retro, not-due" in closing.waits(folded(round_dir))


def test_retro_is_ready_once_the_close_waits_for_nothing_else(round_dir):
    built(round_dir)

    found = retro(round_dir)

    assert found.state == steps.READY and found.waits == ()
    assert steps.current(folded(round_dir), name=ROUND, seen=seen(round_dir)) == found
    assert closing.waits(folded(round_dir)) == ("step retro, ready",)
    assert closing.waits_before_retro(folded(round_dir)) == ()
    assert folded(round_dir).status == "open"


def test_the_round_closes_on_the_retro_report(round_dir, capsys):
    built(round_dir)

    assert main(["verdict", ROUND, report(round_dir)]) == 0

    after = folded(round_dir)
    assert [one["type"] for one in after.history[-2:]] == [
        "verdict.recorded", "round.closed"]
    assert after.status == "closed"
    assert steps.one(after, "retro").state == steps.DONE


def test_a_retro_recorded_before_it_is_due_counts_once_the_rest_is_done(round_dir):
    built(round_dir, finished=False)
    verdicts.record(ROUND, report(round_dir), by="helper")
    assert retro(round_dir).state == steps.DONE
    assert folded(round_dir).status == "open"

    log.append(log.round_log(round_dir), "finish.done", {"stage": "B1", "tip": TIP},
               by="coder")
    rule(None, text="a placeholder ruling")

    assert folded(round_dir).status == "closed"


def test_an_unruled_major_decision_keeps_retro_not_due_until_it_is_ruled(round_dir):
    built(round_dir, decisions=[("M1", "major")])
    assert retro(round_dir).state == steps.NOT_DUE

    rule("B1.M1=accept")

    assert retro(round_dir).state == steps.READY


def test_with_ratify_off_the_decision_keeps_nothing_back(round_dir, write_config):
    built(round_dir, decisions=[("M1", "major")])
    write_config(FLOWS + '\n[defaults]\nratify = "off"\n')
    assert retro(round_dir).state == steps.READY


def test_with_the_settings_not_read_retro_waits_on_that_read_where_they_decide(round_dir):
    built(round_dir, decisions=[("M1", "major")])

    found = retro(round_dir, read=False)

    assert (found.state, found.unread) == (steps.WAITING, True)
    assert found.waits[0].fix == f"moxi rounds next {ROUND}"


def test_with_the_settings_not_read_retro_is_ready_where_they_decide_nothing(round_dir):
    built(round_dir)
    assert retro(round_dir, read=False).state == steps.READY


def test_next_gives_the_retro_its_reads_its_block_its_verdict_and_the_numbers(
        round_dir, capsys):
    built(round_dir)

    assert main(["next", ROUND, "--json"]) == 0
    [group] = json.loads(capsys.readouterr().out)["groups"]
    assert main(["next", ROUND]) == 0
    text = capsys.readouterr().out

    assert (group["step"], group["state"], group["by"]) == ("retro", "ready", "helper")
    assert group["reads"] == ["the numbers below", str(round_dir / "round-note.md"),
                              f"the reports in {round_dir}", "what went wrong"]
    assert group["write"] == (f"a report in {round_dir} with the block from: moxi rounds "
                              "next --schema retro")
    assert group["then"] == [f"moxi rounds verdict {ROUND} <your report>"]
    assert group["numbers"]["design_passes"] == 0
    assert "\nnumbers:\n  design: passes 0, " in text
    assert "\n  time in steps, overlapping (steps due side by side each count the same time): " in text


def test_a_skip_of_retro_lets_the_round_close(round_dir):
    built(round_dir)
    rule("skip:retro")
    assert folded(round_dir).status == "closed"
    assert steps.one(folded(round_dir), "retro").state == steps.SKIPPED
