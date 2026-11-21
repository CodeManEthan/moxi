"""The board: each step's cell, the lines, groups and phrases, the strip, the records
and a question's passage, read from rounds kept in the test's own home, with what git
says of each build given as `steps.Seen`."""

from __future__ import annotations

import pytest

from moxi_rounds import (board, checking, drift, events, freezing, listing, log, next_step,
                         opening, ruling, showing, steps, verdicts)
from moxi_rounds import settings as own
from moxi_rounds.errors import Unmet

ROUND = "kestrel/billing-export"
PTR = "comms:1234567"
BASE, TIP = "0" * 40, "1" * 40
ASKED = "Does the export keep the old column names?"

FLOWS = """\
[flows.staged]
steps = [ { step = "design", by = "lead" },
          { step = "verify-design", by = "checker" },
          { step = "build", by = "builder" },
          { step = "staging", does = "command", before = "finish", by = "builder", argv = ["true"] },
          { step = "verify-build", by = "checker" },
          { step = "finish", by = "builder" },
          { step = "retro", by = "worker" } ]
"""


@pytest.fixture
def kit(tmp_path, add_project, write_config, capsys):
    """`kit(flow="full")`: open the round on that flow and return its folder."""
    add_project("kestrel", notes_root=tmp_path / "notes")
    write_config(FLOWS)

    def open_on(flow: str = "full"):
        opening.open_round(ROUND, description="billing export", by="planner", flow=flow)
        capsys.readouterr()
        return tmp_path / "notes" / "rounds" / "billing-export"

    return open_on


_drafts = iter(range(1, 1000))


def stage(id_: str, *, after=(), round_=None) -> str:
    if round_ is not None:
        return "\n".join(["[[stage]]", f'id = "{id_}"', f'round = "{round_}"',
                          "after = []"])
    return "\n".join(["[[stage]]", f'id = "{id_}"', 'repo = "kestrel"',
                      f'branch = "branch-{id_.lower()}"',
                      f"after = [{', '.join(repr(one) for one in after)}]"])


def question(id_: str, blocks: str | None = None) -> str:
    lines = ["[[question]]", f'id = "{id_}"', f'text = "{ASKED}"']
    return "\n".join(lines if blocks is None else [*lines, f"blocks = {blocks}"])


def fold(round_dir, *entries, prose: str = "") -> None:
    text = (f"# A design\n\nDraft {next(_drafts)}.\n\n{prose}```toml rounds\n"
            + "\n\n".join(entries) + "\n```\n")
    (round_dir / "design.md").write_text(text, encoding="utf-8")
    freezing.freeze(ROUND, by="lead")


_reports = iter(range(1, 1000))


def verdict(round_dir, word: str = "publishable", **keys) -> None:
    count = next(_reports)
    lines = [f'verdict = "{word}"', 'pass = "full"', 'by = "checker"', 'model = "m"']
    lines += [f"{key} = {value}" for key, value in keys.items()]
    path = round_dir / f"report-{count}.md"
    path.write_text(f"# Report {count}\n\n```toml rounds\n" + "\n".join(lines) + "\n```\n",
                    encoding="utf-8")
    verdicts.record(ROUND, path, by="checker")


def verified(round_dir, *entries, prose: str = "") -> None:
    fold(round_dir, *entries, prose=prose)
    verdict(round_dir, kind='"design"', design=1)


def append(round_dir, event_type: str, **fields) -> None:
    log.append(log.round_log(round_dir), event_type, fields, by="lead")


def commission(round_dir, stage_id: str = "B1") -> None:
    append(round_dir, "build.commissioned", stage=stage_id, repo="kestrel",
           branch=f"branch-{stage_id.lower()}", base=BASE, target="main",
           builder="builder-1", build="0123456789abcdef")


def build_verdict(round_dir, word: str, stage_id: str = "B1") -> None:
    append(round_dir, "verdict.recorded", kind="build", verdict=word, stage=stage_id,
           head=TIP, by="checker", model="m", report="report.md", findings=[],
           decisions=[])


def ran(round_dir, stage_id: str = "B1", *, step: str = "check", code: int = 0) -> None:
    append(round_dir, "check.ran", stage=stage_id, step=step, commit=TIP,
           argv=["true"], exit_code=code, duration_s=1.0, output="check-1.log")


def seen_of(*stages_, check=("true",), **more) -> dict:
    return {one: steps.Seen(state="building", tip=TIP, clean=True, check=check, head=TIP,
                            since=(), values={}, **more) for one in stages_}


def part(*, seen=None) -> board.Part:
    found = showing.show(ROUND, refresh=False)
    return board.part(found, seen or {}, models=own.load()["models"])


def cell_of(made: board.Part, column: str, stage_id=None, items=board.Items()) -> dict:
    return board.cells(made, items)[(column, stage_id)]


def kinds(made: board.Part, items=board.Items()) -> dict:
    return {key: one["state"] for key, one in board.cells(made, items).items()}


# -- the columns -------------------------------------------------------------------------

def test_columns_hold_the_round_s_own_then_each_stage_s_then_the_end_steps():
    flow = [{"step": "build"}, {"step": "staging", "does": "command"},
            {"step": "finish"}]
    found = board.columns([flow, flow])
    assert [one["id"] for one in found] == [
        "design", "verify-design", "docket", "commission", "build", "check", "staging",
        "verify-build", "finish", "ratify", "retro", "close"]
    assert [one["label"] for one in found][:2] == ["design", "verify"]
    assert {one["id"]: one["part"] for one in found}["staging"] == "stage"


def test_a_user_step_gets_no_column_while_no_flow_has_it():
    assert "staging" not in [one["id"] for one in board.columns([[{"step": "build"}]])]


# -- each state of a cell ----------------------------------------------------------------

def test_a_fresh_round_has_the_design_owed_and_the_rest_to_come(kit):
    kit()
    found = kinds(part())
    assert found[("design", None)] == "owed"
    assert found[("verify-design", None)] == "waiting"
    assert found[("docket", None)] == "none"
    assert found[("close", None)] == "todo"


def test_a_frozen_design_is_done_with_its_version(kit):
    round_dir = kit()
    fold(round_dir, stage("B1"))
    made = cell_of(part(), "design")
    assert (made["state"], made["word"], made["settled"]) == ("done", "v1", True)


def test_a_design_sent_back_at_its_version_is_failed(kit):
    round_dir = kit()
    fold(round_dir, stage("B1"))
    verdict(round_dir, "fold-first", kind='"design"', design=1)
    made = cell_of(part(), "verify-design")
    assert (made["state"], made["word"]) == ("failed", "back")


def test_a_docket_with_an_open_question_is_his_with_its_count(kit):
    round_dir = kit()
    verified(round_dir, question("Q1"), question("Q2"), stage("B1"))
    items = board.Items(questions=("Q1", "Q2"))
    made = cell_of(part(), "docket", items=items)
    assert (made["state"], made["count"], made["by"]) == ("yours", 2, "operator")


def test_a_docket_all_ruled_with_a_ruling_not_read_back_is_his_before_done(kit):
    round_dir = kit()
    verified(round_dir, question("Q1"), stage("B1"))
    ruling.rule(ROUND, item="Q1", text="Yes.", record=PTR, by="lead")
    made = part()
    assert made.step("docket", None).state == steps.DONE
    found = cell_of(made, "docket", items=board.Items(not_shown=("C1",)))
    assert (found["state"], found["count"], found["step_state"]) == ("yours", 1, "done")


def test_a_skipped_step_is_none_with_its_word(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    ruling.rule(ROUND, item="skip:retro", record=PTR, by="lead")
    made = cell_of(part(), "retro")
    assert (made["state"], made["word"], made["settled"]) == ("none", "skipped", True)


def test_a_commission_ready_is_owed_and_one_a_question_holds_is_held(kit):
    round_dir = kit()
    verified(round_dir, question("Q1", '["B2"]'), stage("B1"), stage("B2"))
    made = part()
    assert (cell_of(made, "commission", "B1")["state"],
            cell_of(made, "commission", "B1")["word"]) == ("owed", "ready")
    assert (cell_of(made, "commission", "B2")["state"],
            cell_of(made, "commission", "B2")["word"]) == ("waiting", "held")
    assert cell_of(made, "commission", "B1")["by"] == "lead"


def test_a_commission_waiting_on_its_after_stage_waits(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"), stage("B2", after=("B1",)))
    made = cell_of(part(), "commission", "B2")
    assert (made["state"], made["word"]) == ("waiting", "waits")


def test_a_child_round_s_stage_is_child_and_a_step_not_built_is_todo(kit):
    round_dir = kit()
    verified(round_dir, stage("B1", round_="kestrel/a-child"))
    made = cell_of(part(), "commission", "B1")
    assert (made["state"], made["word"]) == ("child", "child")


def test_a_build_with_its_check_owed_and_its_drift_owed(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)

    moved = drift.Drift(rule=drift.OWED, owed=True, head=TIP, merge_base=BASE, tip=TIP,
                        target_before=BASE, why="the target moved")
    made = part(seen=seen_of("B1", drift=moved))
    assert (cell_of(made, "build", "B1")["state"], cell_of(made, "build", "B1")["word"]) \
        == ("done", None)
    assert cell_of(made, "check", "B1")["word"] == "owed"
    assert cell_of(made, "check", "B1")["by"] == "builder"
    assert cell_of(made, "check", "B1")["who"] is None
    assert cell_of(made, "build", "B1")["who"] == "builder-1"
    assert cell_of(made, "verify-build", "B1")["word"] == "drift"


def test_a_build_not_yet_with_work_is_building(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    made = part(seen={"B1": steps.Seen(state="empty", tip=TIP, clean=True, check=("true",),
                                       head=TIP, since=(), values={})})
    assert (cell_of(made, "build", "B1")["state"], cell_of(made, "build", "B1")["word"]) \
        == ("owed", "building")


def test_a_check_whose_newest_run_at_the_tip_failed_is_failed(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    ran(round_dir, code=1)
    made = cell_of(part(seen=seen_of("B1")), "check", "B1")
    assert (made["state"], made["word"], made["step_state"]) == ("failed", "failed",
                                                                 "ready")


def test_a_check_whose_newest_ci_read_at_the_tip_is_red_is_failed(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    append(round_dir, "ci.read", stage="B1", commit=TIP, reading="red",
           workflows=[{"path": ".github/workflows/ci.yml", "reading": "red",
                       "jobs": [{"name": "linux", "reading": "red"}]}])
    made = part(seen=seen_of("B1"))
    assert cell_of(made, "check", "B1")["word"] == "red"
    texts = [one["text"] for one in board.phrase(made, board.cells(made, board.Items()),
                                                 board.Items())]
    assert "CI linux red on B1" in texts


def test_a_build_sent_back_is_failed_at_its_verification(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    build_verdict(round_dir, "fold-first")
    made = cell_of(part(seen=seen_of("B1")), "verify-build", "B1")
    assert (made["state"], made["word"]) == ("failed", "back")


def test_a_command_step_whose_run_failed_is_failed_and_says_who_does_it(kit):
    round_dir = kit("staged")
    verified(round_dir, stage("B1"))
    commission(round_dir)
    ran(round_dir, step="staging", code=2)
    made = cell_of(part(seen=seen_of("B1")), "staging", "B1")
    assert (made["state"], made["word"], made["by"]) == ("failed", "failed", "builder")


def test_a_stage_whose_repo_declares_no_check_has_no_check_cell(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    made = part(seen=seen_of("B1", check=None))
    columns = board.columns([made.state.steps])
    first = board.lines(made, columns, board.Items())[0][0]
    position = [one["id"] for one in columns].index("check")
    assert first["cells"][position] is None
    assert ("check", "B1") not in board.cells(made, board.Items())


def test_close_holds_his_jobs_then_a_fix_owed(kit):
    kit()
    made = part()
    assert board.close_cell(made, board.Items(jobs=("B1.W1",)))["state"] == "yours"
    assert board.close_cell(made, board.Items())["state"] == "todo"


def test_ratify_with_majors_not_ruled_is_his(kit):
    kit()
    made = cell_of(part(), "ratify", items=board.Items(decisions=("B1.M1",)))
    assert (made["state"], made["count"], made["by"]) == ("yours", 1, "operator")


# -- lines, here and the lead stage ------------------------------------------------------

def test_a_round_at_docket_with_a_build_open_is_here_twice(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"), stage("B2"))
    commission(round_dir)
    append(round_dir, "question.asked", id="Q9", text=ASKED, **{"from": PTR},
           blocks="none", state="open", asked_by="lead", asked_at="2026-01-01T00:00:00Z")
    made = part(seen=seen_of("B1"))
    columns = board.columns([made.state.steps])
    first = board.lines(made, columns, board.Items(questions=("Q9",)))[0][0]
    here = [column["id"] for column, one in zip(columns, first["cells"], strict=True)
            if one and one["here"]]
    assert first["stage"] == "B1"
    assert here == ["docket", "check"]


def test_a_round_at_its_lead_stage_s_step_is_here_once(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    made = part()
    columns = board.columns([made.state.steps])
    first = board.lines(made, columns, board.Items())[0][0]
    assert sum(bool(one and one["here"]) for one in first["cells"]) == 1


@pytest.mark.parametrize(("plan", "built", "expected"), [
    ([], [], None),
    (["B1"], [], "B1"),
    (["B1", "B2"], ["B2"], "B1"),
    (["B1", "B2"], [], "B1"),
])
def test_the_lead_stage_is_the_first_that_fits(kit, plan, built, expected):
    round_dir = kit()
    if plan:
        verified(round_dir, *(stage(one) for one in plan))
    for one in built:
        commission(round_dir, one)
    assert board.lead_stage(part(seen=seen_of(*built))) == expected


@pytest.mark.parametrize(("count", "folds"), [(5, {"more": 4}), (6, {"more": 0})])
def test_four_other_stages_unfold_as_one_and_five_fold_by_how_far(kit, count, folds):
    round_dir = kit()
    ids = [f"B{n}" for n in range(1, count + 1)]
    verified(round_dir, *(stage(one) for one in ids))
    made = part()
    found, counted = board.lines(made, board.columns([made.state.steps]), board.Items())
    assert counted["more"] == folds["more"]
    if count == 6:
        assert counted["not_started"]["n"] == 5
        assert counted["not_started"]["ready"] == 5
        assert {one["fold"] for one in found[1:]} == {"not-started"}
    else:
        assert {one["fold"] for one in found[1:]} == {"more"}


def test_a_stage_with_an_open_build_has_its_own_line(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"), stage("B2"))
    commission(round_dir, "B1")
    commission(round_dir, "B2")
    made = part(seen=seen_of("B1", "B2"))
    found, _folds = board.lines(made, board.columns([made.state.steps]), board.Items())
    assert [(one["kind"], one["stage"]) for one in found] == [("round", "B1"),
                                                              ("build", "B2")]
    assert found[1]["branch"] == "branch-b2"


# -- groups ------------------------------------------------------------------------------

def group_of(made, items=board.Items()):
    return board.group(made, board.cells(made, items), items)


def test_a_round_holding_his_items_is_yours_before_stuck(kit):
    round_dir = kit()
    fold(round_dir, stage("B1"))
    verdict(round_dir, "fold-first", kind='"design"', design=1)
    assert group_of(part()) == "stuck"
    assert group_of(part(), board.Items(questions=("Q1",))) == "yours"


def test_a_fresh_build_with_no_failure_is_moving(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    assert group_of(part(seen=seen_of("B1"))) == "moving"


def test_a_build_with_its_check_owed_and_finish_waiting_is_moving(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    made = part(seen=seen_of("B1"))
    assert made.step("finish", "B1").state == steps.WAITING
    assert group_of(made) == "moving"


def test_a_commission_waiting_on_its_after_stage_is_moving(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"), stage("B2", after=("B1",)))
    commission(round_dir, "B1")
    assert group_of(part(seen=seen_of("B1"))) == "moving"


def test_a_build_checked_and_verified_whose_finish_waits_is_stuck(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    ran(round_dir)
    build_verdict(round_dir, "publishable")
    made = part(seen=seen_of("B1"))
    first = [one for one in made.every if one.stage == "B1" and not one.settled][0]
    assert (first.step, first.state) == ("finish", "waiting")
    assert group_of(made) == "stuck"
    texts = [one["text"] for one in board.phrase(made, board.cells(made, board.Items()),
                                                 board.Items())]
    assert any(text.startswith("finish of B1 waits: ") for text in texts)


def test_a_failed_cell_is_stuck(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    ran(round_dir, code=1)
    assert group_of(part(seen=seen_of("B1"))) == "stuck"


def test_a_design_changed_since_its_version_was_frozen_is_stuck(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    (round_dir / "design.md").write_text("# changed\n", encoding="utf-8")
    found = showing.show(ROUND, refresh=False)
    seen = checking.seen_of(found.state, ROUND, settings=own.load())
    made = board.part(found, seen)
    assert board.design_moved(made) is not None
    assert group_of(made) == "stuck"


def test_a_round_whose_retro_is_ready_is_wrapping(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    build_verdict(round_dir, "publishable")
    append(round_dir, "finish.done", stage="B1", tip=TIP)
    made = part(seen={None: steps.Seen()})
    assert made.step("retro", None).state == steps.READY
    assert group_of(made) == "wrapping"


def test_the_board_orders_his_rounds_by_his_items_then_the_oldest_first():
    rows = [{"name": "a", "group": "moving", "mine": 0, "last_at": "2026-01-03"},
            {"name": "b", "group": "yours", "mine": 1, "last_at": "2026-01-01"},
            {"name": "c", "group": "yours", "mine": 3, "last_at": "2026-01-02"},
            {"name": "d", "group": "stuck", "mine": 0, "last_at": "2026-01-02"},
            {"name": "e", "group": "moving", "mine": 0, "last_at": "2026-01-01"}]
    assert [one["name"] for one in board.ordered(rows)] == ["c", "b", "d", "e", "a"]


# -- the phrase --------------------------------------------------------------------------

def test_the_phrase_puts_his_items_then_what_went_wrong_then_what_moves(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"), stage("B2"))
    commission(round_dir, "B1")
    ran(round_dir, code=1)
    made = part(seen=seen_of("B1"))
    items = board.Items(questions=("Q1", "Q2"), not_shown=("C1",), jobs=("B1.W1",))
    found = board.phrase(made, board.cells(made, items), items)
    assert [(one["text"], one["weight"]) for one in found] == [
        ("2 questions", "yours"), ("1 to read back", "yours"),
        ("1 job for you", "yours"), ("check failed on B1", "bad"),
        ("B2 ready to start (lead)", "moves")]


def test_a_round_with_nothing_owed_says_so(kit):
    round_dir = kit()
    verified(round_dir, stage("B1", round_="kestrel/a-child"))
    made = part()
    found = board.phrase(made, board.cells(made, board.Items()), board.Items())
    assert found == [{"text": "nothing owed", "weight": "quiet"}]


# -- records -----------------------------------------------------------------------------

def test_a_record_fills_its_step_s_lines_on_demand(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    made = part()
    assert all(one["then"] == [] for one in made.groups)
    found = board.record(made, "commission", "B1", board.Items())
    assert found["next"]["then"] == [f"moxi rounds commission {ROUND} B1 --builder NAME"]
    assert found["next"]["by"] == "lead"
    assert found["state"] == "owed"


def test_a_retro_s_record_has_no_numbers(kit, monkeypatch):
    from moxi_rounds import numbers as counted

    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    append(round_dir, "finish.done", stage="B1", tip=TIP)
    made = part()
    monkeypatch.setattr(counted, "of", lambda *_a, **_k: pytest.fail("counted"))
    found = board.record(made, "retro", None, board.Items())
    assert found["next"]["then"] == [f"moxi rounds verdict {ROUND} <your report>"]
    assert "numbers" not in found["next"]


def test_a_check_s_record_has_its_runs_and_ci_red_first(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"))
    commission(round_dir)
    ran(round_dir, code=1)
    append(round_dir, "ci.read", stage="B1", commit=TIP, reading="red",
           workflows=[{"path": "ci.yml", "reading": "red",
                       "jobs": [{"name": "mac", "reading": "green"},
                                {"name": "linux", "reading": "red"}]}])
    found = board.record(part(seen=seen_of("B1")), "check", "B1", board.Items())
    assert [one["name"] for one in found["ci"]["jobs"]] == ["linux", "mac"]
    assert [(one["exit_code"], one["output"]) for one in found["runs"]] == [
        (1, "check-1.log")]
    assert [one["type"] for one in found["events"]] == ["check.ran", "ci.read"]


def test_a_closed_round_s_card_is_read_from_its_log_alone(kit):
    round_dir = kit("fix")
    append(round_dir, "stage.declared", id="B1", repo="kestrel", branch="branch-b1",
           after=[])
    commission(round_dir)
    append(round_dir, "finish.done", stage="B1", tip=TIP)
    append(round_dir, "round.closed")
    found = showing.show(ROUND, refresh=False)
    assert found.state.status == "closed"
    made = board.card(board.part(found, {}), board.Items())
    assert made["ended"]["how"] == "closed"
    assert made["ends"] == [{"stage": "B1", "how": "landed"}]
    assert made["stages"] == {"finished": 1, "building": 0, "not_started": 0}


# -- which step an event belongs to ------------------------------------------------------

def test_every_type_of_event_rounds_writes_has_a_place_in_the_table():
    assert set(board.EVENT_STEPS) == set(events.TYPES)


@pytest.mark.parametrize(("event", "expected"), [
    ({"type": "build.dropped", "stage": "B2"}, ("commission", "B2")),
    ({"type": "ruling.recorded", "rules": "B1.M2"}, ("ratify", None)),
    ({"type": "ruling.recorded", "rules": "Q1"}, ("docket", None)),
    ({"type": "question.asked", "id": "Q1"}, ("docket", None)),
    ({"type": "verdict.recorded", "kind": "build", "stage": "B1"},
     ("verify-build", "B1")),
    ({"type": "verdict.recorded", "kind": "design", "design": 2}, ("verify-design", None)),
    ({"type": "check.ran", "stage": "B1", "step": "check.local"}, ("check", "B1")),
    ({"type": "check.ran", "stage": "B1", "step": "staging"}, ("staging", "B1")),
    ({"type": "finding.marked", "id": "B3.L2"}, ("verify-build", "B3")),
    ({"type": "wait.marked", "id": "B1.W1"}, ("close", None)),
    ({"type": "finish.done", "stage": "B1"}, ("finish", "B1")),
    ({"type": "repair"}, (None, None)),
])
def test_an_event_belongs_to_the_step_its_fields_say(event, expected):
    assert board.event_step(event) == expected


# -- the strip ---------------------------------------------------------------------------

@pytest.mark.parametrize(("event", "words"), [
    ({"type": "finish.done"}, "landed"),
    ({"type": "ci.read", "reading": "red"}, "CI red"),
    ({"type": "check.ran", "step": "check.local", "exit_code": 0}, "local check passed"),
    ({"type": "verdict.recorded", "stage": "B1", "verdict": "publishable",
      "pass": "re-check"}, "re-check passed"),
    ({"type": "verdict.recorded", "stage": "B1", "verdict": "publishable",
      "pass": "drift"}, "drift re-check passed"),
    ({"type": "verdict.recorded", "stage": "B1", "verdict": "fold-first"}, "sent back"),
    ({"type": "build.commissioned"}, "commissioned"),
    ({"type": "finding.marked", "id": "B1.L2", "state": "fixed"}, "B1.L2 marked fixed"),
    ({"type": "build.dropped"}, "dropped"),
    ({"type": "verdict.recorded", "stage": "B1", "verdict": "publishable",
      "pass": "full"}, "verify passed"),
    ({"type": "brief.sized", "stage": "B1", "slice": 2}, "brief sized"),
    ({"type": "ruling.recorded", "id": "B1.M2", "rules": "B1.M2"}, "ruling recorded"),
    ({"type": "ci.read", "reading": "green"}, "CI green"),
    ({"type": "wait.marked", "id": "B1.W1", "state": "done"}, "job marked done"),
    ({"type": "finish.action", "stage": "B1", "id": "merge", "kind": "merge",
      "state": "ok"}, "finish: merge ok"),
    ({"type": "later.type_of_event", "stage": "B1"}, "later type of event"),
])
def test_an_act_is_said_in_words(event, words):
    assert board.act_words(event) == words


@pytest.mark.parametrize("kind", sorted(events.TYPES))
def test_no_type_of_event_is_said_by_its_name(kind):
    words = board.act_words({"type": kind, "stage": "B1"})
    assert words and words != kind and "." not in words and "_" not in words


def test_the_strip_holds_the_newest_acts_and_the_stage_as_it_reads_now(kit):
    round_dir = kit()
    verified(round_dir, stage("B1"), stage("B2"))
    commission(round_dir, "B1")
    commission(round_dir, "B2")
    ran(round_dir, "B1")
    made = part(seen=seen_of("B1", "B2"))
    found = board.strip([made], size=1)
    assert [(one["stage"], one["act"]) for one in found] == [("B1", "check passed")]
    assert found[0]["builder"] == "builder-1"
    assert [one["step_state"] for one in found[0]["cells"]][:2] == ["done", "done"]
    assert found[0]["steps"][:3] == ["commission", "build", "check"]
    assert len(found[0]["steps"]) == len(found[0]["cells"])
    assert [one["stage"] for one in board.strip([made])] == ["B1", "B2"]


# -- the passage -------------------------------------------------------------------------

PROSE = ("Some words first.\n\n"
         "Q1 is the question of the columns: " + ASKED + "\n\n"
         "Nothing about it here.\n\n"
         "Another paragraph that names Q1 again.\n\n"
         "And Q10, which is another question.\n\n")


def test_the_passage_is_the_paragraphs_naming_the_question_quoting_it_first(kit):
    round_dir = kit()
    fold(round_dir, question("Q1"), stage("B1"), prose=PROSE)
    pointer = "file:versions/design-v1.md#Q1"
    found = board.passage(round_dir, pointer, "Q1", text=ASKED, max_chars=2000)
    assert found.split("\n\n") == [f"Q1 is the question of the columns: {ASKED}",
                                   "Another paragraph that names Q1 again."]


def test_the_passage_is_cut_to_its_size(kit):
    round_dir = kit()
    fold(round_dir, question("Q1"), stage("B1"), prose=PROSE)
    found = board.passage(round_dir, "file:versions/design-v1.md#Q1", "Q1", max_chars=20)
    assert len(found) == 20 and found.endswith("…")


@pytest.mark.parametrize("pointer", ["file:versions/design-v9.md#Q1", PTR, None,
                                     "file:../../outside.md#Q1"])
def test_no_passage_where_the_pointer_names_no_file_of_the_round(kit, pointer):
    round_dir = kit()
    assert board.passage(round_dir, pointer, "Q1", max_chars=200) is None


def test_no_passage_where_no_paragraph_names_the_question(kit):
    round_dir = kit()
    fold(round_dir, question("Q2"), stage("B1"), prose="Nothing named.\n\n")
    assert board.passage(round_dir, "file:versions/design-v1.md#Q2", "Q7",
                         max_chars=200) is None


# -- parity with rounds' own reads -------------------------------------------------------

def _kit_rounds(kit) -> None:
    round_dir = kit()
    verified(round_dir, question("Q1", '["B2"]'), stage("B1"), stage("B2"))
    commission(round_dir, "B1")


def test_each_cell_says_the_state_steps_gives_and_none_where_it_gives_none(kit):
    _kit_rounds(kit)
    made = part(seen=seen_of("B1"))
    columns = board.columns([made.state.steps])
    found, _folds = board.lines(made, columns, board.Items())
    for line in found:
        for column, one in zip(columns, line["cells"], strict=True):
            stage_id = line["stage"] if column["part"] == "stage" else None
            if column["id"] == "close" or (column["part"] != "stage"
                                           and line["kind"] != "round"):
                continue
            step = made.step(column["id"], stage_id)
            assert (one is None) == (step is None)
            if step is not None:
                assert one["step_state"] == step.state


def test_each_group_next_gives_has_its_state_and_by_on_its_cell(kit):
    _kit_rounds(kit)
    found = showing.show(ROUND, refresh=False)
    seen = checking.seen_of(found.state, ROUND, settings=own.load())
    made = board.part(found, seen)
    cells = board.cells(made, board.Items())
    for one in next_step.read(ROUND)["groups"]:
        got = cells[(one["step"], one["stage"])]
        assert got["step_state"] == one["state"]
        if one["by"] is not None:
            assert got["by"] == one["by"]


def test_the_round_line_is_here_at_the_step_the_listing_gives(kit):
    _kit_rounds(kit)
    found = showing.show(ROUND, refresh=False)
    seen = checking.seen_of(found.state, ROUND, settings=own.load())
    made = board.part(found, seen)
    [row] = listing.rounds(refresh=False).rounds
    columns = board.columns([made.state.steps])
    first = board.lines(made, columns, board.Items())[0][0]
    here = [(column["id"], first["stage"] if column["part"] == "stage" else None)
            for column, one in zip(columns, first["cells"], strict=True)
            if one and one["here"]]
    assert (row.step["step"], row.step["stage"]) in here


def test_his_cells_count_what_waits_on_him_for_the_round(kit):
    _kit_rounds(kit)
    waited = listing.waiting(refresh=False).data()
    value = {"questions": waited["open"], "not_shown": waited["not_shown"],
             "decisions": waited["decisions"], "waits": waited["waits"]}
    items = board.items_of(value, ROUND)
    made = part(seen=seen_of("B1"))
    yours = sum(one["count"] for one in board.cells(made, items).values()
                if one["state"] == "yours")
    assert yours == items.count == len(items.questions) + len(items.not_shown)
    assert items.questions == ("Q1",)
