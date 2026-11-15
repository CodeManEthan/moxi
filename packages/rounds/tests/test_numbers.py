"""A round's numbers: what happened in it, counted from its log alone, and held by the
event that ends it."""

from __future__ import annotations

import pytest

from moxi_rounds import dropping, log, numbers, opening, ruling, state

ROUND = "proj-a/a-round"
PTR = "comms:1234567"
AT = "2026-01-02T03:04:05Z"
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


def append(folder, kind, body, actor="planner"):
    log.append(log.round_log(folder), kind, body, actor=actor)


def counted(folder) -> dict:
    return numbers.of(state.fold(log.read(log.round_log(folder))))


def finding(id_, severity, state_="open"):
    return {"id": id_, "severity": severity, "state": state_, "text": "a finding"}


def build_report(*, pass_="full", word="fold-first", found=(), flags=()):
    body = {"kind": "build", "verdict": word, "pass": pass_, "stage": "B1", "head": TIP,
            "by": "critic", "findings": list(found)}
    if flags:
        body["flags"] = list(flags)
    return body


@pytest.fixture
def built(folder):
    """B1 declared and commissioned through the log."""
    append(folder, "stage.declared",
           {"id": "B1", "repo": "proj-a", "branch": "work", "after": []})
    append(folder, "build.commissioned",
           {"stage": "B1", "repo": "proj-a", "branch": "work", "base": BASE,
            "target": "main", "builder": "coder", "build": "0123456789abcdef"})
    return folder


def test_a_round_with_nothing_done_counts_nothing(folder):
    found = counted(folder)
    assert found["design_passes"] == found["folds"] == found["checks_failed"] == 0
    assert found["findings"] == {"blocker": 0, "major": 0, "minor": 0, "note": 0}
    assert found["decisions"] == {"accept": 0, "reject": 0}
    assert found["flags"] == {"extra-full-pass": 0, "model-below-tier": 0}
    assert found["questions"] == {}


def test_findings_are_counted_once_each_at_the_severity_the_newest_report_gave(built):
    append(built, "verdict.recorded",
           build_report(found=[finding("L1", "minor"), finding("L2", "major")]),
           actor="critic")
    append(built, "verdict.recorded",
           build_report(pass_="re-check", word="publishable",
                        found=[finding("L1", "major", "fixed"), finding("L2", "major")]),
           actor="critic")

    found = counted(built)

    assert found["findings"] == {"blocker": 0, "major": 2, "minor": 0, "note": 0}
    assert (found["rechecks"], found["drift_rechecks"], found["fold_first"]) == (1, 0, 1)


def test_a_drift_recheck_and_the_flags_of_a_report_are_counted(built):
    append(built, "verdict.recorded",
           build_report(pass_="drift", word="publishable",
                        flags=["model-below-tier"]), actor="critic")
    found = counted(built)
    assert found["drift_rechecks"] == 1
    assert found["flags"] == {"extra-full-pass": 0, "model-below-tier": 1}


def test_a_check_run_that_did_not_exit_0_is_failed_one_at_its_limit_included(built):
    for code in (0, 1, None):
        append(built, "check.ran",
               {"stage": "B1", "step": "check", "commit": TIP, "argv": ["make", "check"],
                "exit_code": code, "duration_s": 1.0, "output": None}, actor="coder")
    assert counted(built)["checks_failed"] == 2


def test_questions_are_counted_by_the_shape_of_the_pointer_they_came_from(folder):
    for id_, origin in (("Q1", "comms:12"), ("Q2", "comms:13"),
                        ("Q3", "https://example.test/a")):
        append(folder, "question.asked",
               {"id": id_, "text": "Does it hold?", "from": origin, "blocks": "all",
                "state": "open", "asked_by": "planner", "asked_at": AT})
    assert counted(folder)["questions"] == {"comms": 2, "url": 1}


def test_skips_corrections_and_the_lead_s_acts_are_counted(built):
    first = ruling.rule(ROUND, item="skip:verify-build:B1", record=PTR, actor="planner")
    ruling.rule(ROUND, item=f"revoke:{first.id}", record=PTR, actor="planner")
    append(built, "flow.changed",
           {"was": "fix", "flow": "small", "steps": [], "by": "lead"})
    append(built, "repair", {"what": "base", "stage": "B1", "fields": {}})
    append(built, "lock.cleared", {"ref": "a-ref", "holder": "a holder line"})
    append(built, "build.dropped",
           {"stage": "B1", "by": "operator", "record": PTR, "tips": []})

    found = counted(built)

    assert (found["skips"], found["corrections"], found["step_ups"], found["repairs"],
            found["cleared_locks"], found["drops"]) == (1, 1, 1, 1, 1, 1)


def test_the_folds_and_the_lead_s_decisions_on_the_design_are_counted(folder):
    append(folder, "design.frozen", {"version": 1, "digest": "a" * 64})
    append(folder, "design.frozen",
           {"version": 2, "digest": "b" * 64, "records_only": True, "record": PTR})
    append(folder, "thread.decided",
           {"thread": "T1", "decision": "lead-decision", "record": PTR})
    found = counted(folder)
    assert (found["folds"], found["records_only_folds"], found["lead_decisions"]) == (
        2, 1, 1)


def test_a_dropped_round_holds_its_numbers_in_its_drop(folder):
    append(folder, "design.frozen", {"version": 1, "digest": "a" * 64})
    before = counted(folder)

    dropping.drop(ROUND, by="operator", record=PTR, actor="planner")

    event = log.read(log.round_log(folder))[-1]
    assert event["type"] == "round.dropped"
    assert event["numbers"] == {**before, "step_time_s": event["numbers"]["step_time_s"]}
    assert before["folds"] == 1


def timed(events, *seconds):
    """The events with their times set, `seconds` after a fixed start each."""
    return [{**one, "at": log.stamp(1_000_000 + after)}
            for one, after in zip(events, seconds, strict=True)]


def test_each_step_s_time_runs_from_the_event_that_reached_it_to_the_next(built):
    events = timed(log.read(log.round_log(built)), 0, 10, 30)

    found = numbers.step_time_s(events, ended_at=log.stamp(1_000_000 + 100))

    # Opened with no stage, the round is at the commission that declares one; once
    # B1 is declared, at B1's; once commissioned, at its build, which git would read.
    assert found == {"commission": 10, "commission B1": 20, "build B1": 70}
    assert list(found) == ["commission", "commission B1", "build B1"]


def test_with_no_ending_time_the_count_stops_at_the_last_event(built):
    events = timed(log.read(log.round_log(built)), 0, 10, 30)
    assert numbers.step_time_s(events) == {"commission": 10, "commission B1": 20}


def test_a_stretch_that_runs_backwards_or_has_no_time_counts_nothing(built):
    events = timed(log.read(log.round_log(built)), 0, 10, 5)
    events[1]["at"] = "not a time"

    found = numbers.step_time_s(events, ended_at=log.stamp(1_000_000 + 25))

    assert found == {"build B1": 20}


def test_a_round_that_has_ended_is_at_no_step_after_its_ending(folder):
    dropping.drop(ROUND, by="operator", record=PTR, actor="planner")
    events = timed(log.read(log.round_log(folder)), 0, 40)

    found = numbers.step_time_s(events, ended_at=log.stamp(1_000_000 + 90))

    assert found == {"commission": 40}


def test_the_drop_s_numbers_time_its_last_step_to_the_drop_itself(folder):
    dropping.drop(ROUND, by="operator", record=PTR, actor="planner", at=log.stamp(4e9))

    opened, dropped = log.read(log.round_log(folder))

    spent = dropped["numbers"]["step_time_s"]
    assert dropped["at"] == log.stamp(4e9)
    assert spent == numbers.step_time_s([opened], ended_at=dropped["at"])
    assert spent["commission"] > 0
