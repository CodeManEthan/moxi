"""The state of each step of a round: the order of a flow's steps, the row of each step
that has one, what a skip does to every reader, the hold shown on a stage, and the step a
round is at."""

from __future__ import annotations

import shutil

import pytest

from moxi_rounds import (api, asking, brake, dropping, freezing, index, listing, log,
                         opening, ruling, state, steps, verdicts)
from moxi_rounds.cli import main

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
PTR = "comms:1234567"
FOLD = f"moxi rounds fold {ROUND}"

DESIGN_ONLY = """\
    [flows.paper]
    steps = [ { step = "design", by = "lead", model = "second" },
              { step = "verify-design", by = "checker", model = "second" } ]
    """
TRACED = """\
    [projects.proj-a]
    trace = true
    """


@pytest.fixture
def opened(tmp_path, add_project, write_config, capsys):
    """`opened(flow="full", config=...)`: open `a-round` of `proj-a` on that flow and
    return its folder. The config also has a flow, `paper`, that builds nothing."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    add_project("proj-b")

    def open_on(flow: str = "full", config: str = ""):
        write_config(DESIGN_ONLY + config)
        opening.open_round(ROUND, description="a placeholder round", by="planner",
                           flow=flow)
        capsys.readouterr()
        return tmp_path / "notes" / "rounds" / "a-round"

    return open_on


@pytest.fixture(name="round_dir")
def round_dir_fixture(opened):
    return opened()


def question(id_, **more) -> str:
    lines = ["[[question]]", f'id = "{id_}"', f'text = "{ASKED}"']
    lines += [f"{key} = {value}" for key, value in more.items()]
    return "\n".join(lines)


def stage(id_, repo="proj-a") -> str:
    return "\n".join(["[[stage]]", f'id = "{id_}"', f'repo = "{repo}"',
                      f'branch = "branch-{id_.lower()}"', "after = []", "",
                      "  [[stage.slice]]", '  id = "1a"', '  what = "a piece"',
                      "  minutes = 40"])


def row(id_) -> str:
    return "\n".join(["[[trace]]", f'id = "{id_}"', 'as = "carried"',
                      'where = "the index"'])


_folds = iter(range(1, 1000))


def fold(round_dir, *entries):
    text = (f"# A design\n\nDraft {next(_folds)}.\n\n```toml rounds\n"
            + "\n\n".join(entries) + "\n```\n")
    (round_dir / "design.md").write_text(text, encoding="utf-8")
    return freezing.freeze(ROUND, by="scout")


_reports = iter(range(1, 1000))


def verdict(round_dir, word="fold-first", *, design=1, pass_="full"):
    count = next(_reports)
    keys = ['kind = "design"', f'verdict = "{word}"', f'pass = "{pass_}"',
            'by = "critic"', 'model = "a-model"', f"design = {design}"]
    path = round_dir / f"report-{count}.md"
    path.write_text(f"# A report\n\nPass {count}.\n\n```toml rounds\n"
                    + "\n".join(keys) + "\n```\n", encoding="utf-8")
    return verdicts.record(ROUND, path, by="critic")


def ask(blocks=None) -> str:
    return asking.ask(ROUND, ASKED, origin="comms:11", blocks=blocks, by="scout").id


def rule(item=None, **more):
    return ruling.rule(ROUND, item=item, record=PTR, by="planner", **more)


def folded(round_dir):
    return state.fold(log.read(log.round_log(round_dir)))


def step(round_dir, name, stage_id=None, **how):
    return steps.one(folded(round_dir), name, stage_id, name=ROUND, **how)


def at(round_dir, **how):
    found = steps.current(folded(round_dir), name=ROUND, **how)
    return None if found is None else (found.step, found.stage, found.state)


# -- the order of a flow's steps ---------------------------------------------------------

@pytest.mark.parametrize("flow, expected", [
    ("full", ["design", "verify-design", "docket", "commission", "build", "check",
              "verify-build", "finish", "ratify", "retro"]),
    ("small", ["design", "commission", "build", "check", "verify-build", "finish",
               "ratify", "retro"]),
    ("fix", ["commission", "build", "check", "verify-build", "finish", "ratify"]),
    ("paper", ["design", "verify-design", "docket"]),
])
def test_a_flows_steps_are_the_listed_ones_with_the_unlisted_beside_them(
        opened, flow, expected):
    found = steps.of(folded(opened(flow)), name=ROUND)
    assert [one.step for one in found] == expected
    assert all(one.stage is None for one in found)


def test_a_step_the_flow_lists_itself_stays_where_it_is_listed():
    listed = [{"step": "check"}, {"step": "build"}, {"step": "finish"}]
    assert [name for name, _entry in steps.order(listed)] == [
        "check", "commission", "build", "finish", "ratify"]


def test_a_listed_step_carries_its_entry_and_an_unlisted_one_none(round_dir):
    assert step(round_dir, "verify-design").entry["recheck_model"] == "third"
    assert step(round_dir, "docket").entry is None


def test_a_step_of_a_stage_is_given_for_each_stage_of_the_plan(round_dir):
    fold(round_dir, stage("B1"), stage("B2"))
    rule("unstage:B1")
    fold(round_dir, stage("B2"), stage("B3"))
    found = [(one.step, one.stage) for one in steps.of(folded(round_dir))]
    assert found[3:7] == [("commission", "B2"), ("commission", "B3"),
                          ("build", "B2"), ("build", "B3")]
    assert ("ratify", None) in found and ("retro", None) in found


def test_a_flow_without_the_step_has_no_state_for_it(opened):
    path = opened("fix")
    assert step(path, "design") is None
    assert step(path, "verify-design") is None
    assert step(path, "docket") is None
    assert steps.is_settled(folded(path), "verify-design")


# -- the design --------------------------------------------------------------------------

@pytest.mark.parametrize("flow", ["full", "small", "paper"])
def test_the_design_is_ready_until_a_version_is_frozen_and_done_from_then(opened, flow):
    path = opened(flow)
    assert step(path, "design").state == steps.READY
    assert at(path) == ("design", None, steps.READY)
    fold(path)
    assert step(path, "design").state == steps.DONE
    assert at(path)[0] != "design"


# -- the step that verifies the design ---------------------------------------------------

def never_frozen(_round_dir):
    pass


def trace_fails(round_dir):
    rule(text="Agent-read times follow the settings.")
    fold(round_dir)


def sent_back(round_dir):
    fold(round_dir)
    verdict(round_dir)


def sent_back_twice(round_dir):
    fold(round_dir)
    verdict(round_dir)
    fold(round_dir)
    verdict(round_dir, design=2, pass_="re-check")
    fold(round_dir)


@pytest.mark.parametrize("arrange, reason, fix", [
    (never_frozen, "no version of the design is frozen", FOLD),
    (trace_fails, "trace: v1 fails: no row for C1; a version whose rows pass is "
                  "waited for", f"give design.md the rows, then {FOLD}"),
    (sent_back, "v1 was sent back and the design was not frozen since: a re-check "
                "reads what changed", FOLD),
    (sent_back_twice, "the last 2 verdicts of the design each sent it back, so no "
                      "further pass is asked for",
     f"moxi rounds mark {ROUND} thread:NAME --close --record PTR, or --accept-risk "
     "in place of --close; PTR names where the lead wrote why"),
], ids=["never-frozen", "trace-fails", "sent-back", "sent-back-twice"])
def test_each_thing_verifying_the_design_waits_for_is_named_alone(
        opened, arrange, reason, fix):
    round_dir = opened(config=TRACED)
    arrange(round_dir)
    found = step(round_dir, "verify-design")
    assert found.state == steps.WAITING
    assert [(one.reason, one.fix) for one in found.waits] == [(reason, fix)]
    expected = "design" if arrange is never_frozen else "verify-design"
    assert at(round_dir)[0] == expected


def test_a_design_frozen_and_unread_is_ready_for_a_full_pass(round_dir):
    fold(round_dir)
    found = step(round_dir, "verify-design")
    assert (found.state, found.waits) == (steps.READY, ())
    assert (found.asked.pass_, found.asked.version) == (brake.FULL, 1)


def test_a_version_whose_trace_rows_pass_is_ready(opened):
    round_dir = opened(config=TRACED)
    rule(text="Agent-read times follow the settings.")
    fold(round_dir, row("C1"))
    assert step(round_dir, "verify-design").state == steps.READY


def test_a_design_changed_after_a_pass_is_ready_for_a_re_check(round_dir):
    sent_back(round_dir)
    fold(round_dir)
    found = step(round_dir, "verify-design")
    assert found.state == steps.READY
    assert (found.asked.pass_, found.asked.since, found.asked.version) == (
        brake.RECHECK, 1, 2)


def test_a_design_found_publishable_is_verified_until_it_changes(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable")
    assert step(round_dir, "verify-design").state == steps.DONE
    fold(round_dir)
    assert step(round_dir, "verify-design").state == steps.READY


def test_the_leads_decision_on_a_thread_verifies_the_design(round_dir):
    sent_back_twice(round_dir)
    brake.decide(ROUND, "the-holds", brake.CLOSE, record="file:notes.md#why",
                 by="planner")
    assert step(round_dir, "verify-design").state == steps.DONE


def test_a_version_that_fails_its_trace_rows_and_stands_verified_is_done(opened):
    round_dir = opened(config=TRACED)
    trace_fails(round_dir)
    verdict(round_dir, "publishable")
    assert step(round_dir, "verify-design").state == steps.DONE


# -- the questions step ------------------------------------------------------------------

def test_the_questions_step_is_done_while_no_question_is_open(round_dir):
    assert step(round_dir, "docket").state == steps.DONE


def test_the_questions_step_is_ready_while_a_question_is_open_and_names_it(round_dir):
    ask()
    ask("none")
    found = step(round_dir, "docket")
    assert (found.state, found.questions, found.waits) == (steps.READY, ("Q1", "Q2"), ())


@pytest.mark.parametrize("item, more", [
    ("Q1", {"text": "They do."}), ("withdraw:Q1", {}), ("park:Q1", {})])
def test_a_question_ruled_withdrawn_or_parked_leaves_the_step_done(round_dir, item, more):
    ask()
    rule(item, **more)
    assert step(round_dir, "docket").state == steps.DONE


def test_a_parked_question_makes_the_step_due_once_what_it_waits_for_has_come(round_dir):
    ask()
    rule("park:Q1", until="round:proj-a/another")
    assert step(round_dir, "docket", closed=lambda name: False).state == steps.DONE
    found = step(round_dir, "docket", closed=lambda name: name == "proj-a/another")
    assert (found.state, found.questions) == (steps.READY, ("Q1",))


def test_the_round_is_at_the_questions_step_once_its_design_is_verified(round_dir):
    fold(round_dir, question("Q1"), stage("B1"))
    assert at(round_dir) == ("verify-design", None, steps.READY)
    verdict(round_dir, "publishable")
    assert at(round_dir) == ("docket", None, steps.READY)
    rule("Q1", text="They do.")
    assert at(round_dir) == ("commission", "B1", steps.NOT_BUILT)


# -- a skip ------------------------------------------------------------------------------

def test_a_skip_of_verifying_the_design_is_read_by_everything_that_asks(round_dir):
    fold(round_dir)
    rule("skip:verify-design")
    found = folded(round_dir)
    assert steps.one(found, "verify-design").state == steps.SKIPPED
    assert steps.is_settled(found, "verify-design")
    assert [one.state for one in steps.of(found)][:2] == [steps.DONE, steps.SKIPPED]
    assert steps.current(found).step == "commission"
    assert next(steps.at(found))[1]["step"] == "commission"


def test_a_skip_taken_back_leaves_the_step_as_it_was(round_dir):
    fold(round_dir)
    rule("skip:verify-design")
    rule("revoke:C1")
    assert step(round_dir, "verify-design").state == steps.READY


@pytest.mark.parametrize("item, name, stage_id", [
    ("skip:retro", "retro", None),
    ("skip:ratify", "ratify", None),
    ("skip:check:B1", "check", "B1"),
    ("skip:verify-build:B1", "verify-build", "B1"),
])
def test_a_skip_of_a_step_whose_row_is_not_built_is_read_all_the_same(
        round_dir, item, name, stage_id):
    fold(round_dir, stage("B1"), stage("B2"))
    assert step(round_dir, name, stage_id).state == steps.NOT_BUILT

    rule(item)

    found = step(round_dir, name, stage_id)
    assert (found.state, found.stage, found.settled) == (steps.SKIPPED, stage_id, True)
    assert found.entry == step(round_dir, name, "B2").entry
    assert steps.is_settled(folded(round_dir), name, stage_id)
    assert [(one.stage, one.state) for one in steps.of(folded(round_dir))
            if one.step == name] == (
        [(None, steps.SKIPPED)] if stage_id is None
        else [("B1", steps.SKIPPED), ("B2", steps.NOT_BUILT)])

    rule("revoke:C1")

    assert step(round_dir, name, stage_id).state == steps.NOT_BUILT


def test_a_skip_is_read_before_the_row_of_its_step_is_asked(round_dir, monkeypatch):
    fold(round_dir, stage("B1"))
    rule("skip:retro")

    def never_asked(state, about):
        raise AssertionError("the row of a skipped step was asked")

    monkeypatch.setitem(steps.ROWS, "retro", never_asked)
    monkeypatch.setitem(steps.ROWS, "check", lambda state, about: steps.Step(
        "check", steps.READY, stage=about.stage))

    assert step(round_dir, "retro").state == steps.SKIPPED
    assert step(round_dir, "check", "B1").state == steps.READY
    rule("skip:check:B1")
    assert step(round_dir, "check", "B1").state == steps.SKIPPED


@pytest.mark.parametrize("flow", ["full", "small"])
def test_a_skip_of_the_questions_step_lets_its_stage_past_for_everything_that_asks(
        opened, flow):
    round_dir = opened(flow)
    fold(round_dir, question("Q1", blocks='["B1", "B2"]'), stage("B1"),
         stage("B2", "proj-b"))
    held = (f"Q1 holds B1: {ASKED}", f"moxi rounds rule {ROUND} Q1 --text TEXT --record PTR")
    before = folded(round_dir)
    assert [(one.reason, one.fix) for one in steps.held_by(before, "B1", name=ROUND)] \
        == [held]
    rule("skip:docket:B1")
    found = folded(round_dir)
    assert steps.held_by(found, "B1", name=ROUND) == ()
    assert steps.one(found, "commission", "B1").waits == ()
    assert steps.one(found, "finish", "B1").waits == ()
    assert len(steps.one(found, "commission", "B2").waits) == 1
    assert not any(one.waits for one in steps.of(found) if one.stage == "B1")
    if flow == "full":
        assert steps.one(found, "docket", "B1").state == steps.SKIPPED
        assert steps.one(found, "docket", "B2").questions == ("Q1",)
        # The question is still his to answer: it holds the other stage.
        assert steps.one(found, "docket").state == steps.READY


def test_the_command_for_a_hold_chooses_where_the_question_offers_options(round_dir):
    fold(round_dir, question("Q1", options='{ a = "yes", b = "no" }'), question("Q2"),
         stage("B1"))
    assert [one.fix for one in steps.held_by(folded(round_dir), "B1", name=ROUND)] == [
        f"moxi rounds rule {ROUND} Q1 --chose K --record PTR",
        f"moxi rounds rule {ROUND} Q2 --text TEXT --record PTR"]


def test_a_question_asked_after_the_skip_holds_the_stage_again(round_dir):
    fold(round_dir, question("Q1"), stage("B1"))
    rule("skip:docket:B1")
    later = ask("B1")
    found = step(round_dir, "docket", "B1")
    assert (found.state, found.questions) == (steps.READY, (later,))
    assert [one.reason for one in step(round_dir, "commission", "B1").waits] == [
        f"{later} holds B1: {ASKED}"]


def test_a_stage_no_question_holds_and_no_skip_names_is_done_with_its_questions(round_dir):
    fold(round_dir, question("Q1", blocks='["B2"]'), stage("B1"), stage("B2"))
    assert step(round_dir, "docket", "B1").state == steps.DONE


# -- a flow with no questions step -------------------------------------------------------

def test_a_flow_with_no_questions_step_shows_the_hold_on_the_stage_it_stops(opened):
    round_dir = opened("small")
    fold(round_dir, question("Q1", blocks='["B1"]'), stage("B1"))
    found = steps.current(folded(round_dir), name=ROUND)
    assert (found.step, found.stage, found.state) == ("commission", "B1", steps.NOT_BUILT)
    assert [(one.reason, one.fix) for one in found.waits] == [
        (f"Q1 holds B1: {ASKED}", f"moxi rounds rule {ROUND} Q1 --text TEXT --record PTR")]
    assert [(one.step, bool(one.waits)) for one in steps.of(folded(round_dir))
            if one.stage == "B1"] == [
        ("commission", True), ("build", False), ("check", False),
        ("verify-build", False), ("finish", True)]
    rule("Q1", text="They do.")
    assert steps.current(folded(round_dir), name=ROUND).waits == ()


# -- a step with no row ------------------------------------------------------------------

@pytest.mark.parametrize("name", ["commission", "build", "check", "verify-build",
                                  "finish", "ratify", "retro"])
def test_a_step_with_no_row_is_not_built_and_never_read_as_done(round_dir, name):
    fold(round_dir, stage("B1"))
    found = step(round_dir, name, "B1" if name not in ("ratify", "retro") else None)
    assert found.state == steps.NOT_BUILT
    assert steps.NOT_BUILT not in steps.STATES
    assert not found.settled
    assert not steps.is_settled(folded(round_dir), name)


def test_a_row_added_for_a_step_is_what_says_its_state(round_dir, monkeypatch):
    fold(round_dir, stage("B1"), stage("B2"))
    monkeypatch.setitem(
        steps.ROWS, "commission",
        lambda state, about: steps.Step(
            "commission", steps.DONE if about.stage == "B1" else steps.READY,
            stage=about.stage))
    monkeypatch.setitem(steps.ROWS, "verify-design",
                        lambda state, about: steps.Step("verify-design", steps.DONE))
    assert at(round_dir) == ("commission", "B2", steps.READY)


# -- the step a round is at --------------------------------------------------------------

def test_a_round_that_builds_nothing_is_at_no_step_once_its_design_is_verified(opened):
    round_dir = opened("paper")
    fold(round_dir, question("Q1", blocks='"none"'))
    verdict(round_dir, "publishable")
    assert at(round_dir) == ("docket", None, steps.READY)
    rule("Q1", text="They do.")
    assert at(round_dir) is None
    assert list(steps.at(folded(round_dir))) == []


def test_a_round_on_the_lightest_flow_starts_at_a_step_that_is_not_built(opened):
    assert at(opened("fix")) == ("commission", None, steps.NOT_BUILT)


def test_a_round_that_has_ended_is_at_no_step(round_dir):
    found = folded(round_dir)
    found.status = state.DROPPED
    assert steps.current(found) is None
    assert list(steps.at(found)) == []


def test_the_state_is_the_whole_input(round_dir, tmp_path):
    fold(round_dir, question("Q1"), stage("B1"))
    found = folded(round_dir)
    shutil.rmtree(tmp_path / "notes")
    assert [one.step for one in steps.of(found, name=ROUND)][:3] == [
        "design", "verify-design", "docket"]
    assert steps.current(found, name=ROUND).step == "verify-design"


# -- what the index keeps ----------------------------------------------------------------

def test_the_index_keeps_the_step_its_stage_and_its_state(round_dir):
    assert list(steps.at(folded(round_dir))) == [
        ("step", {"step": "design", "stage": None, "state": steps.READY})]


def test_the_index_names_the_rounds_a_parked_question_waits_for(round_dir):
    fold(round_dir, question("Q1"))
    verdict(round_dir, "publishable")
    rule("park:Q1", until="round:proj-a/another")
    assert list(steps.at(folded(round_dir))) == [
        ("step", {"step": "commission", "stage": None, "state": steps.NOT_BUILT,
                  "until": ["proj-a/another"]})]


# -- the step where rounds are shown and listed ------------------------------------------

def listed(**how):
    return listing.rounds(**how).rounds[0].step


def test_the_list_the_index_and_the_calls_say_the_step_a_round_is_at(round_dir, capsys):
    fold(round_dir, question("Q1"), stage("B1"))
    waiting = {"step": "verify-design", "stage": None, "state": steps.READY}
    assert listed() == waiting
    assert [item.body for item in
            index.read("proj-a", round_dir.parent, "a-round").of(index.STEP)] == [waiting]
    assert api.rounds()["rounds"][0]["step"] == waiting
    assert api.round_data(ROUND)["step"] == {**waiting, "waits": [], "questions": []}
    assert main(["list"]) == 0
    assert f"{ROUND}   open   flow full   step verify-design, ready   design: none" in (
        capsys.readouterr().out)

    verdict(round_dir, "publishable")
    assert listed() == {"step": "docket", "stage": None, "state": steps.READY}
    assert main(["show", ROUND]) == 0
    assert "step: docket, ready   questions: Q1\n" in capsys.readouterr().out
    assert "- step: docket, ready   questions: Q1\n" in (
        round_dir / "round-note.md").read_text(encoding="utf-8")


def test_a_skipped_step_is_passed_wherever_the_step_is_shown(round_dir, capsys):
    fold(round_dir)
    rule("skip:verify-design")
    assert listed()["step"] == "commission"
    assert api.round_data(ROUND)["step"]["step"] == "commission"
    assert main(["show", ROUND]) == 0
    assert "step: commission, not-built\n" in capsys.readouterr().out


def test_a_round_at_no_step_and_one_that_ended_are_listed_at_none(opened, capsys):
    round_dir = opened("paper")
    # A stage keeps the round open, and a flow that builds nothing has no step for it.
    fold(round_dir, stage("B1"))
    verdict(round_dir, "publishable")
    assert listed() is None
    assert main(["list"]) == 0
    assert "flow paper   step none   design: publishable@v1" in capsys.readouterr().out
    dropping.drop(ROUND, by="operator", record=PTR, run_by="planner")
    assert listed(ended=True) is None
    assert main(["list", "--closed"]) == 0
    assert "   step " not in capsys.readouterr().out


def test_the_list_reads_the_log_again_once_a_round_a_question_waits_for_closed(
        opened, monkeypatch):
    round_dir = opened("paper")
    fold(round_dir, question("Q1", blocks='"none"'))
    verdict(round_dir, "publishable")
    rule("park:Q1", until="round:proj-a/another")
    assert listed() is None
    monkeypatch.setattr("moxi_rounds.questions.is_closed",
                        lambda name, settings=None: name == "proj-a/another")
    assert listed() == {"step": "docket", "stage": None, "state": steps.READY}
    assert listed(refresh=False) == listed()


@pytest.mark.parametrize("found, expected", [
    (None, "none"), ({"step": None, "stage": None, "state": None}, "none"),
    ({"step": "docket", "stage": None, "state": "ready"}, "docket, ready"),
    ({"step": "commission", "stage": "B2", "state": "not-built"},
     "commission of B2, not-built")])
def test_a_step_in_words(found, expected):
    assert steps.said(found) == expected
