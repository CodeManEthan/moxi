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
def round_dir(tmp_path, add_project, write_config, capsys):
    """`a-round` of `proj-a`, opened on the `fix` flow."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    write_config("")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds" / "a-round"


def append(round_dir, kind, body, by="planner"):
    log.append(log.round_log(round_dir), kind, body, by=by)


def counted(round_dir) -> dict:
    return numbers.of(state.fold(log.read(log.round_log(round_dir))))


def finding(id_, severity, state_="open"):
    return {"id": id_, "severity": severity, "state": state_, "text": "a finding"}


def build_report(*, pass_="full", word="fold-first", found=(), flags=()):
    body = {"kind": "build", "verdict": word, "pass": pass_, "stage": "B1", "head": TIP,
            "by": "critic", "findings": list(found)}
    if flags:
        body["flags"] = list(flags)
    return body


@pytest.fixture
def built(round_dir):
    """B1 declared and commissioned through the log."""
    append(round_dir, "stage.declared",
           {"id": "B1", "repo": "proj-a", "branch": "work", "after": []})
    append(round_dir, "build.commissioned",
           {"stage": "B1", "repo": "proj-a", "branch": "work", "base": BASE,
            "target": "main", "builder": "coder", "build": "0123456789abcdef"})
    return round_dir


def test_a_round_with_nothing_done_counts_nothing(round_dir):
    found = counted(round_dir)
    assert found["design_passes"] == found["folds"] == found["checks_failed"] == 0
    assert found["findings"] == {"blocker": 0, "major": 0, "minor": 0, "note": 0}
    assert found["decisions"] == {"accept": 0, "reject": 0}
    assert found["flags"] == {"extra-full-pass": 0, "model-below-tier": 0}
    assert found["questions"] == {}


def test_findings_are_counted_once_each_at_the_severity_the_newest_report_gave(built):
    append(built, "verdict.recorded",
           build_report(found=[finding("L1", "minor"), finding("L2", "major")]),
           by="critic")
    append(built, "verdict.recorded",
           build_report(pass_="re-check", word="publishable",
                        found=[finding("L1", "major", "fixed"), finding("L2", "major")]),
           by="critic")

    found = counted(built)

    assert found["findings"] == {"blocker": 0, "major": 2, "minor": 0, "note": 0}
    assert (found["rechecks"], found["drift_rechecks"], found["fold_first"]) == (1, 0, 1)


def test_a_drift_recheck_and_the_flags_of_a_report_are_counted(built):
    append(built, "verdict.recorded",
           build_report(pass_="drift", word="publishable",
                        flags=["model-below-tier"]), by="critic")
    found = counted(built)
    assert found["drift_rechecks"] == 1
    assert found["flags"] == {"extra-full-pass": 0, "model-below-tier": 1}


def test_a_check_run_that_did_not_exit_0_is_failed_one_at_its_limit_included(built):
    for code in (0, 1, None):
        append(built, "check.ran",
               {"stage": "B1", "step": "check", "commit": TIP, "argv": ["make", "check"],
                "exit_code": code, "duration_s": 1.0, "output": None}, by="coder")
    assert counted(built)["checks_failed"] == 2


def test_questions_are_counted_by_the_shape_of_the_pointer_they_came_from(round_dir):
    for id_, origin in (("Q1", "comms:12"), ("Q2", "comms:13"),
                        ("Q3", "https://example.test/a")):
        append(round_dir, "question.asked",
               {"id": id_, "text": "Does it hold?", "from": origin, "blocks": "all",
                "state": "open", "asked_by": "planner", "asked_at": AT})
    assert counted(round_dir)["questions"] == {"comms": 2, "url": 1}


def test_skips_corrections_and_the_lead_s_acts_are_counted(built):
    first = ruling.rule(ROUND, item="skip:verify-build:B1", record=PTR, by="planner")
    ruling.rule(ROUND, item=f"revoke:{first.id}", record=PTR, by="planner")
    append(built, "flow.changed",
           {"was": "fix", "flow": "small", "steps": [], "by": "lead"})
    append(built, "repair", {"what": "base", "stage": "B1", "fields": {}})
    append(built, "lock.cleared", {"ref": "a-ref", "holder": "a holder line"})
    append(built, "build.dropped",
           {"stage": "B1", "by": "operator", "record": PTR, "tips": []})

    found = counted(built)

    assert (found["skips"], found["corrections"], found["step_ups"], found["repairs"],
            found["cleared_locks"], found["drops"]) == (1, 1, 1, 1, 1, 1)


def test_the_folds_and_the_lead_s_decisions_on_the_design_are_counted(round_dir):
    append(round_dir, "design.frozen", {"version": 1, "digest": "a" * 64})
    append(round_dir, "design.frozen",
           {"version": 2, "digest": "b" * 64, "records_only": True, "record": PTR})
    append(round_dir, "thread.decided",
           {"thread": "T1", "decision": "lead-decision", "record": PTR})
    found = counted(round_dir)
    assert (found["folds"], found["records_only_folds"], found["lead_decisions"]) == (
        2, 1, 1)


def test_a_records_only_fold_of_a_design_sent_back_is_a_lead_decision(round_dir):
    append(round_dir, "design.frozen", {"version": 1, "digest": "a" * 64})
    append(round_dir, "verdict.recorded",
           {"kind": "design", "verdict": "fold-first", "pass": "full", "design": 1,
            "by": "critic"}, by="critic")
    append(round_dir, "design.frozen",
           {"version": 2, "digest": "b" * 64, "records_only": True, "record": PTR})
    append(round_dir, "design.frozen",
           {"version": 3, "digest": "c" * 64, "records_only": True, "record": PTR})

    found = counted(round_dir)

    # The second fold of records only carries the lead's decision; it makes none.
    assert (found["records_only_folds"], found["lead_decisions"]) == (2, 1)


def test_a_dropped_round_holds_its_numbers_in_its_drop(round_dir):
    append(round_dir, "design.frozen", {"version": 1, "digest": "a" * 64})
    before = counted(round_dir)

    dropping.drop(ROUND, by="operator", record=PTR, run_by="planner")

    event = log.read(log.round_log(round_dir))[-1]
    assert event["type"] == "round.dropped"
    assert event["numbers"] == {**before, **{key: event["numbers"][key] for key in (
        "step_time_s", "round_time_s", "longest_gap")}}
    assert before["folds"] == 1


def timed(events, *seconds):
    """The events with their times set, `seconds` after a fixed start each."""
    return [{**one, "at": log.stamp(1_000_000 + after)}
            for one, after in zip(events, seconds, strict=True)]


def test_each_step_s_time_runs_from_the_event_that_reached_it_to_the_next(built):
    events = timed(log.read(log.round_log(built)), 0, 10, 30)

    found = numbers.step_time_s(events, ended_at=log.stamp(1_000_000 + 100))

    # Opened with no stage, the commission that declares one is due; once B1 is
    # declared, B1's; once commissioned, its build, its verification and its finish,
    # side by side, while the log names no commit of it.
    assert found == {"commission": 10, "commission B1": 20, "build B1": 70,
                     "verify-build B1": 70, "finish B1": 70}
    assert list(found) == ["commission", "commission B1", "build B1", "verify-build B1",
                           "finish B1"]


def _of(events, **given) -> dict:
    return numbers.of(state.fold(events), **given)


def test_the_round_s_time_runs_from_its_first_event_to_its_end(built):
    events = timed(log.read(log.round_log(built)), 0, 10, 30)

    assert _of(events, ended_at=log.stamp(1_000_000 + 100))["round_time_s"] == 100
    assert _of(events, now=log.stamp(1_000_000 + 250))["round_time_s"] == 250
    assert _of(events)["round_time_s"] == 30


def test_the_longest_stretch_with_nothing_logged_names_when_and_what_was_due(built):
    events = timed(log.read(log.round_log(built)), 0, 10, 30)

    found = _of(events, ended_at=log.stamp(1_000_000 + 100))["longest_gap"]

    # The stretch from the commission to the end is the longest, and the build's steps
    # were due across it.
    assert found == {"since": log.stamp(1_000_030), "until": log.stamp(1_000_100),
                     "due": ["build B1", "verify-build B1", "finish B1"]}


def test_of_equal_stretches_the_earliest_is_the_longest(built):
    events = timed(log.read(log.round_log(built)), 0, 20, 40)

    found = _of(events)["longest_gap"]

    assert found == {"since": log.stamp(1_000_000), "until": log.stamp(1_000_020),
                     "due": ["commission"]}


def test_the_clocks_read_as_lines():
    found = numbers.lines({"step_time_s": {}, "round_time_s": 7_505, "longest_gap": {
        "since": "2026-11-17T08:00:00Z", "until": "2026-11-21T08:00:12Z",
        "due": ["finish B1"]}})

    assert found[3:5] == [
        "time open: 2h 5m 5s, open to close (to now while the round is open)",
        "longest stretch with nothing logged: 96h 12s, since 2026-11-17T08:00:00Z, "
        "while finish B1 was due"]
    assert numbers.lines({"longest_gap": {
        "since": "2026-11-17T08:00:00Z", "until": "2026-11-17T08:00:01Z",
        "due": []}})[3] == ("longest stretch with nothing logged: 1s, since "
                            "2026-11-17T08:00:00Z, while no step was due")


def test_the_longest_stretch_is_not_summed_and_the_round_s_time_is():
    gap = {"since": "a", "until": "b", "due": ["build B1"]}
    total = numbers.summed([{"round_time_s": 10, "longest_gap": gap},
                            {"round_time_s": 5, "longest_gap": gap}, {}])
    assert total == {"round_time_s": 15}


def test_a_round_that_ended_before_its_clocks_were_kept_gets_them_from_its_log(
        built):
    append(built, "round.dropped", {"by": "operator", "record": PTR, "numbers": {}})
    append(built, "repair", {"what": "digest", "version": 1, "fields": {}})
    events = timed(log.read(log.round_log(built)), 0, 10, 30, 100, 900)
    folded = state.fold(events)

    found = numbers.with_clocks(folded, folded.ended.numbers or {})

    assert found["round_time_s"] == 100
    assert found["longest_gap"] == {"since": log.stamp(1_000_030),
                                    "until": log.stamp(1_000_100),
                                    "due": ["build B1", "verify-build B1", "finish B1"]}
    kept = {"round_time_s": 1, "longest_gap": None}
    assert numbers.with_clocks(folded, kept) == kept


def check_ran(commit, code=0):
    return ("check.ran", {"stage": "B1", "step": "check", "commit": commit,
                          "argv": ["make", "check"], "exit_code": code,
                          "duration_s": 1.0, "output": None})


def test_a_build_s_steps_settle_as_the_log_shows_them_done(built):
    for kind, body in (check_ran(TIP),
                       ("verdict.recorded", build_report(word="publishable")),
                       ("finish.done", {"stage": "B1", "tip": TIP})):
        append(built, kind, body)
    events = timed(log.read(log.round_log(built)), 0, 10, 30, 40, 60, 90)

    found = numbers.step_time_s(events, ended_at=log.stamp(1_000_000 + 90))

    # The check run names B1's first commit, so its build has work; the run passed
    # there, so its check is done as soon as it has one; the report settles its
    # verification and the finish its finish, with no read of git.
    assert found == {"commission": 10, "commission B1": 20, "build B1": 10,
                     "verify-build B1": 30, "finish B1": 60}


def test_a_commit_after_the_verdict_owes_verification_until_a_mark_names_it(built):
    later = "2" * 40
    for kind, body in (
            check_ran(TIP),
            ("verdict.recorded", build_report(word="publishable",
                                              found=[finding("L1", "minor")])),
            check_ran(later),
            ("finding.marked", {"id": "B1.L1", "state": "fixed", "commits": [later]})):
        append(built, kind, body)
    events = timed(log.read(log.round_log(built)), 0, 10, 30, 40, 50, 70, 100)

    found = numbers.step_time_s(events)

    assert found["verify-build B1"] == 20 + 30
    assert "check B1" not in found


STAGING = """
    [flows.mine]
    steps = [
      { step = "build", by = "builder" },
      { step = "staging", does = "command", before = "finish", by = "builder", argv = ["make", "staging", "{tip}", "{stage}"] },
      { step = "finish", by = "builder" },
    ]
"""


def staging_ran(code):
    return ("check.ran", {"stage": "B1", "step": "staging", "commit": TIP,
                          "argv": ["make", "staging", TIP, "B1"], "exit_code": code,
                          "duration_s": 1.0, "output": None})


def test_a_command_step_of_the_user_s_own_stops_counting_at_its_run_that_passed(
        tmp_path, add_project, write_config, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    write_config(STAGING)
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="mine")
    capsys.readouterr()
    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    append(round_dir, "stage.declared",
           {"id": "B1", "repo": "proj-a", "branch": "work", "after": []})
    append(round_dir, "build.commissioned",
           {"stage": "B1", "repo": "proj-a", "branch": "work", "base": BASE,
            "target": "main", "builder": "coder", "build": "0123456789abcdef"})
    for kind, body in (staging_ran(1), staging_ran(0)):
        append(round_dir, kind, body, by="coder")
    events = timed(log.read(log.round_log(round_dir)), 0, 10, 30, 40, 60)

    found = numbers.step_time_s(events, ended_at=log.stamp(1_000_000 + 100))

    # Due from the commission; the failed run leaves it due; the run that passed at the
    # newest commit the log names settles it, its own argv standing for the filled one.
    assert found["staging B1"] == 10 + 20
    assert found["finish B1"] == 70


def test_ratify_gets_time_only_where_the_project_asks_for_it(built):
    report = {**build_report(word="publishable"), "decisions": [
        {"id": "M1", "text": "a choice", "weight": "major", "forecloses": False}]}
    for kind, body in (check_ran(TIP), ("verdict.recorded", report),
                       ("finish.done", {"stage": "B1", "tip": TIP})):
        append(built, kind, body)
    events = timed(log.read(log.round_log(built)), 0, 10, 30, 40, 60, 90)
    end = log.stamp(1_000_000 + 150)

    asked = numbers.step_time_s(events, ended_at=end, ratify="majors")
    off = numbers.step_time_s(events, ended_at=end, ratify="off")

    # Once B1 is finished its major decision is unruled: the ratify step is due from
    # then to the end, unless the project sets ratify off, when the flow never asks it.
    assert asked["ratify"] == 60
    assert "ratify" not in off
    assert {key: value for key, value in asked.items() if key != "ratify"} == off
    folded = state.fold(events)
    assert "ratify" not in numbers.of(folded, ended_at=end, ratify="off")["step_time_s"]


def test_with_no_ending_time_the_count_stops_at_the_last_event(built):
    events = timed(log.read(log.round_log(built)), 0, 10, 30)
    assert numbers.step_time_s(events) == {"commission": 10, "commission B1": 20}


def test_a_stretch_that_runs_backwards_or_has_no_time_counts_nothing(built):
    events = timed(log.read(log.round_log(built)), 0, 10, 5)
    events[1]["at"] = "not a time"

    found = numbers.step_time_s(events, ended_at=log.stamp(1_000_000 + 25))

    assert found == {"build B1": 20, "verify-build B1": 20, "finish B1": 20}


def test_a_round_that_has_ended_is_at_no_step_after_its_ending(round_dir):
    dropping.drop(ROUND, by="operator", record=PTR, run_by="planner")
    events = timed(log.read(log.round_log(round_dir)), 0, 40)

    found = numbers.step_time_s(events, ended_at=log.stamp(1_000_000 + 90))

    assert found == {"commission": 40}


def test_the_drop_s_numbers_time_its_last_step_to_the_drop_itself(round_dir):
    dropping.drop(ROUND, by="operator", record=PTR, run_by="planner", at=log.stamp(4e9))

    opened, dropped = log.read(log.round_log(round_dir))

    spent = dropped["numbers"]["step_time_s"]
    assert dropped["at"] == log.stamp(4e9)
    assert spent == numbers.step_time_s([opened], ended_at=dropped["at"])
    assert spent["commission"] > 0


def test_the_numbers_read_as_one_line_a_group():
    found = numbers.lines({
        "design_passes": 2, "fold_first": 1, "folds": 3, "records_only_folds": 1,
        "lead_decisions": 1, "step_time_s": {"design": 3725, "build B1": 0},
        "findings": {"blocker": 0, "major": 2, "minor": 1, "note": 0}, "rechecks": 1,
        "drift_rechecks": 0, "checks_failed": 1, "questions": {"comms": 2},
        "decisions": {"accept": 1, "reject": 0}, "skips": 1, "step_ups": 0,
        "repairs": 0, "corrections": 1, "drops": 1, "cleared_locks": 0,
        "flags": {"extra-full-pass": 0, "model-below-tier": 1},
        "a_later_count": 4})

    assert found == [
        "design: passes 2, folds 3 (records only 1), lead decisions 1",
        "reports sent back (fold-first, of any kind): 1",
        "time in steps, overlapping (steps due side by side each count the same time): "
        "design 1h 2m 5s, build B1 0s",
        "findings: blocker 0, major 2, minor 1, note 0; re-checks 1, drift re-checks 0; "
        "check runs failed 1",
        "questions by origin: comms 2",
        "decisions: accepted 1, rejected 0; settled by the lead: accepted 0, "
        "rejected 0",
        "skips 1, step-ups 0, checklist step-ups 0, repairs 0, corrections 1, "
        "dropped builds 1, cleared locks 0",
        "flags: extra-full-pass 0, model-below-tier 1",
        "a_later_count: 4"]


def test_numbers_missing_a_key_read_as_none_counted():
    assert numbers.lines({})[:3] == [
        "design: passes 0, folds 0 (records only 0), lead decisions 0",
        "reports sent back (fold-first, of any kind): 0",
        "time in steps, overlapping (steps due side by side each count the same time): "
        "none"]


def test_numbers_are_summed_key_by_key_and_group_by_group():
    found = numbers.summed([
        {"folds": 1, "step_time_s": {"design": 10}, "questions": {"comms": 1}},
        {"folds": 2, "step_time_s": {"design": 5, "build B1": 7}, "questions": {},
         "drops": 1},
        {"folds": "not a count", "flagged": True}])

    assert found == {"folds": 3, "step_time_s": {"design": 15, "build B1": 7},
                     "questions": {"comms": 1}, "drops": 1}
    assert numbers.summed([]) == {}
