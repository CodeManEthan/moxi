"""The brake on verifying a design: which pass is asked for next, what a version frozen
as a change of records only stands at and when it is refused, the lead's decision on a
thread, and the flag on a second full pass."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import (brake, dropping, freezing, index, log, opening, state,
                         verdicts, versions)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Refused

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
AT = "2026-01-02T03:04:05Z"
WHY = "file:notes.md#why"


@pytest.fixture
def opened(tmp_path, add_project, capsys):
    """`opened(flow="full")`: open `a-round` of `proj-a` on that flow and return its
    folder."""
    add_project("proj-a", notes_root=tmp_path / "notes")

    def open_on(flow: str = "full"):
        opening.open_round(ROUND, description="a placeholder round", actor="planner",
                           flow=flow)
        capsys.readouterr()
        return tmp_path / "notes" / "rounds" / "a-round"

    return open_on


@pytest.fixture
def folder(opened):
    return opened()


def question(id_, text=ASKED, **more) -> str:
    lines = ["[[question]]", f'id = "{id_}"', f'text = "{text}"']
    lines += [f"{key} = {value}" for key, value in more.items()]
    return "\n".join(lines)


def stage(id_, *, after="[]", minutes=40) -> str:
    return "\n".join(["[[stage]]", f'id = "{id_}"', 'repo = "proj-a"',
                      f'branch = "branch-{id_.lower()}"', f"after = {after}", "",
                      "  [[stage.slice]]", '  id = "1a"', '  what = "a piece"',
                      f"  minutes = {minutes}"])


PLAN = (question("Q1", blocks='["B1"]'), question("Q2"), stage("B1"),
        stage("B2", after='["B1"]'))

_folds = iter(range(1, 1000))


def fold(folder, *entries, **how):
    """Write `design.md` with a block of `entries`, the plan when none is given, and
    freeze it. The prose differs each time, as a design's does."""
    text = (f"# A design\n\nDraft {next(_folds)}.\n\n```toml rounds\n"
            + "\n\n".join(entries or PLAN) + "\n```\n")
    (folder / "design.md").write_text(text, encoding="utf-8")
    return freezing.freeze(ROUND, actor="scout", **how)


def records_only(folder, *entries, record=WHY):
    return fold(folder, *entries, records_only=True, record=record)


_reports = iter(range(1, 1000))


def verdict(folder, word="fold-first", *, design=1, pass_="re-check"):
    """Write a design report in the round's folder and record it."""
    count = next(_reports)
    keys = ['kind = "design"', f'verdict = "{word}"', f'pass = "{pass_}"',
            'by = "critic"', 'model = "a-model"', f"design = {design}"]
    path = folder / f"report-{count}.md"
    path.write_text(f"# A report\n\nPass {count}.\n\n```toml rounds\n"
                    + "\n".join(keys) + "\n```\n", encoding="utf-8")
    return verdicts.record(ROUND, path, actor="critic")


def events(folder):
    return log.read(log.round_log(folder))


def folded(folder):
    return state.fold(events(folder))


def raw(folder) -> bytes:
    return log.round_log(folder).read_bytes()


def standing(folder) -> str:
    return str(versions.standing(folded(folder)))


def asked(folder):
    return brake.asked(folded(folder), name=ROUND)


def decide(folder, thread="the-holds", decision=brake.CLOSE, record=WHY):
    return brake.decide(ROUND, thread, decision, record=record, actor="planner")


def sent_back_twice(folder):
    """A design whose two passes each sent it back, frozen again since."""
    fold(folder)
    verdict(folder, pass_="full")
    fold(folder)
    verdict(folder, design=2)
    return fold(folder)


# -- what a change of records only stands at ---------------------------------------------

def test_a_change_of_records_only_carries_a_publishable_standing(folder):
    fold(folder)
    verdict(folder, "publishable", pass_="full")

    frozen = records_only(folder)

    assert (frozen.version, str(frozen.standing)) == (2, "publishable@v2")
    assert standing(folder) == "publishable@v2"
    assert versions.standing(folded(folder)).verdict.design == 1
    assert asked(folder).pass_ is None


def test_a_change_of_records_only_after_the_design_was_sent_back_is_the_leads_decision(
        folder):
    fold(folder)
    sent = verdict(folder, pass_="full")

    records_only(folder)

    found = versions.standing(folded(folder))
    assert str(found) == "lead-decision@v2"
    assert (found.fold, found.verdict, found.decision) == (2, sent.verdict, None)
    assert asked(folder).pass_ is None


def test_a_change_of_records_only_after_an_unchecked_version_stays_at_none(folder):
    fold(folder)
    verdict(folder, "publishable", pass_="full")
    fold(folder)

    records_only(folder)

    assert standing(folder) == "none"
    found = asked(folder)
    assert (found.pass_, found.since, found.version) == (brake.RECHECK, 1, 3)


def test_a_change_of_records_only_carries_the_leads_decision(folder):
    fold(folder)
    verdict(folder, pass_="full")
    records_only(folder)

    records_only(folder)

    assert standing(folder) == "lead-decision@v3"


def test_a_change_of_records_only_on_a_design_never_verified_stays_at_none(folder):
    fold(folder)

    records_only(folder)

    assert standing(folder) == "none"
    assert asked(folder).pass_ == brake.FULL


def test_a_version_found_publishable_stands_for_later_changes_of_records_only(folder):
    fold(folder)
    records_only(folder)
    records_only(folder)

    verdict(folder, "publishable", pass_="full")

    assert standing(folder) == "publishable@v3"


def test_a_publishable_verdict_does_not_reach_past_a_change_of_mechanism(folder):
    fold(folder)
    fold(folder)
    records_only(folder)

    verdict(folder, "publishable", pass_="full")

    assert standing(folder) == "none"


def test_a_verdict_that_sends_an_earlier_version_back_leaves_the_carried_standing(
        folder):
    fold(folder)
    verdict(folder, "publishable", pass_="full")
    records_only(folder)

    verdict(folder, design=1)

    assert standing(folder) == "publishable@v2"


def test_a_plain_version_after_a_carried_standing_stands_at_none(folder):
    fold(folder)
    verdict(folder, "publishable", pass_="full")
    records_only(folder)

    fold(folder)

    assert standing(folder) == "none"


# -- a design frozen again with its text unchanged ---------------------------------------

def again(folder, **how):
    """Freeze the design as it is, with no edit since the version before."""
    frozen = freezing.freeze(ROUND, actor="scout", **how)
    found = folded(folder)
    assert found.versions[-1].digest == found.versions[-2].digest
    return frozen


def test_an_unchanged_design_frozen_again_keeps_a_publishable_standing(folder):
    fold(folder)
    verdict(folder, "publishable", pass_="full")

    frozen = again(folder)

    assert str(frozen.standing) == standing(folder) == "publishable@v2"
    assert asked(folder).pass_ is None and asked(folder).instead == ()
    assert again(folder).version == 3
    assert standing(folder) == "publishable@v3"


def test_an_unchanged_design_frozen_again_keeps_the_leads_decision_on_a_thread(folder):
    sent_back_twice(folder)
    decided = decide(folder)

    again(folder)

    found = versions.standing(folded(folder))
    assert str(found) == "lead-decision@v4"
    assert found.decision.thread == decided.event["thread"]


def test_an_unchanged_design_frozen_again_keeps_a_decision_made_by_a_change_of_records(
        folder):
    fold(folder)
    verdict(folder, pass_="full")
    records_only(folder)

    again(folder)

    found = versions.standing(folded(folder))
    assert str(found) == "lead-decision@v3"
    assert found.fold == 2


def test_an_unchanged_design_frozen_again_is_still_sent_back(folder):
    fold(folder)
    sent = verdict(folder, pass_="full")

    again(folder)

    found = versions.standing(folded(folder))
    assert str(found) == "fold-first@v2"
    assert found.verdict.n == sent.event["n"]
    # The freezing is no decision of the lead's: the verdict is still the one counted.
    assert [one.design for one in versions.sent_back(folded(folder))] == [1]
    assert not versions.lead_decided(folded(folder))


def test_no_pass_is_asked_for_over_a_text_that_was_sent_back_and_not_changed(folder):
    fold(folder)
    verdict(folder, pass_="full")

    again(folder)

    found = asked(folder)
    assert (found.pass_, found.since, found.version) == (brake.RECHECK, 1, None)
    assert found.instead == (f"moxi rounds fold {ROUND}",)
    assert "not changed since" in found.why
    fold(folder)
    found = asked(folder)
    assert (found.pass_, found.since, found.version) == (brake.RECHECK, 1, 3)


def test_an_unchanged_design_frozen_again_with_no_standing_has_none(folder):
    fold(folder)
    again(folder)
    assert standing(folder) == "none"

    verdict(folder, "publishable", pass_="full")
    fold(folder)
    again(folder)

    assert standing(folder) == "none"


def test_a_fold_run_again_after_it_was_recorded_costs_no_pass(folder):
    fold(folder)
    verdict(folder, "publishable", pass_="full")
    fold(folder)
    again(folder)
    assert standing(folder) == "none"
    assert (asked(folder).since, asked(folder).version) == (1, 3)

    verdict(folder, "publishable", design=2)

    assert standing(folder) == "publishable@v3"
    assert asked(folder).pass_ is None


def test_a_design_frozen_with_the_text_of_an_older_version_starts_at_none(folder):
    fold(folder)
    first = (folder / "design.md").read_bytes()
    verdict(folder, "publishable", pass_="full")
    fold(folder)
    (folder / "design.md").write_bytes(first)

    freezing.freeze(ROUND, actor="scout")

    found = folded(folder)
    assert found.versions[2].digest == found.versions[0].digest
    assert standing(folder) == "none"
    verdict(folder, "publishable", design=1)
    assert standing(folder) == "none"


@pytest.mark.parametrize("word", ["publishable", "fold-first"])
def test_a_verdict_for_a_version_whose_text_is_the_current_one_gives_the_standing(
        folder, word):
    fold(folder)
    again(folder)
    again(folder)

    recorded = verdict(folder, word, design=1, pass_="full")

    assert str(recorded.standing) == standing(folder) == f"{word}@v3"
    assert versions.standing(folded(folder)).verdict.design == 1


def test_a_verdict_for_an_unchanged_text_does_not_reach_past_a_change_of_it(folder):
    fold(folder)
    again(folder)
    fold(folder)

    verdict(folder, design=1, pass_="full")

    assert standing(folder) == "none"


def test_a_verdict_that_sends_back_an_unchanged_text_leaves_a_verified_standing(
        folder):
    fold(folder)
    again(folder)
    verdict(folder, "publishable", design=2, pass_="full")

    verdict(folder, design=1)

    assert standing(folder) == "publishable@v2"
    assert versions.sent_back(folded(folder)) == ()


def test_a_change_of_records_only_decides_whatever_the_text(folder):
    fold(folder)
    verdict(folder, pass_="full")

    again(folder, records_only=True, record=WHY)

    assert standing(folder) == "lead-decision@v2"
    assert versions.lead_decided(folded(folder))
    assert versions.sent_back(folded(folder)) == ()


def test_a_change_of_records_only_with_the_text_unchanged_and_no_standing_has_none(
        folder):
    fold(folder)

    again(folder, records_only=True, record=WHY)

    assert standing(folder) == "none"


# -- a change of records only: the verb --------------------------------------------------

def test_a_change_of_records_only_is_one_event_with_its_pointer(folder):
    fold(folder)

    frozen = records_only(folder)

    assert [one["type"] for one in events(folder)] == [
        "round.opened", "design.frozen", "design.frozen"]
    assert (frozen.event["records_only"], frozen.event["record"]) == (True, WHY)
    assert "record" not in events(folder)[1]
    newest = versions.current(folded(folder))
    assert (newest.n, newest.records_only, newest.record) == (2, True, WHY)
    assert versions.file(folder, 2).is_file()


CHANGED = {
    "a new question": ((*PLAN, question("Q3")), "the question Q3 is new"),
    "a question gone": (PLAN[1:], "the question Q1 is gone"),
    "a new stage": ((*PLAN, stage("B3")), "the stage B3 is new"),
    "a stage gone": ((PLAN[0], PLAN[1], PLAN[2]), "the stage B2 is gone"),
    "a stage that waits for more": (
        (PLAN[0], PLAN[1], stage("B1", after='["B2"]'), PLAN[3]),
        "B1 waited for nothing and now waits for B2"),
    "a stage that waits for fewer": (
        (PLAN[0], PLAN[1], PLAN[2], stage("B2")),
        "B2 waited for B1 and now waits for nothing"),
    "a wider hold": (
        (question("Q1", blocks='["B1", "B2"]'), *PLAN[1:]),
        "Q1 held up B1 and now holds up B1, B2"),
    "a narrower hold": (
        (PLAN[0], question("Q2", blocks='["B2"]'), *PLAN[2:]),
        "Q2 held up all and now holds up B2"),
    "a hold on nothing": (
        (question("Q1", blocks='"none"'), *PLAN[1:]),
        "Q1 held up B1 and now holds up none"),
}


@pytest.mark.parametrize("case", CHANGED)
def test_a_change_of_records_only_is_refused_when_what_the_gates_read_changed(
        folder, case):
    block, said = CHANGED[case]
    fold(folder)
    before = raw(folder)

    with pytest.raises(Refused) as refused:
        records_only(folder, *block)

    assert [one.reason for one in refused.value.unmet] == [
        f"this is no change of records only: {said}; a change to what the gates read "
        "is frozen as a plain version, which a re-check then reads"]
    assert refused.value.unmet[0].fix == f"moxi rounds fold {ROUND}"
    assert raw(folder) == before
    assert not versions.file(folder, 2).exists()


def test_every_difference_is_named_in_one_refusal(folder):
    fold(folder)

    with pytest.raises(Refused) as refused:
        records_only(folder, question("Q2"), question("Q3"), stage("B1"))

    assert len(refused.value.unmet) == 3


def test_a_change_of_records_only_is_compared_with_the_version_before_not_the_record(
        folder):
    fold(folder)
    fold(folder, *PLAN, question("Q3"))

    # Q3 is in the record, and the version before declared it: leaving it out differs.
    with pytest.raises(Refused, match="the question Q3 is gone"):
        records_only(folder)


def test_a_block_that_only_adds_options_is_a_change_of_records_only(folder):
    fold(folder)
    verdict(folder, "publishable", pass_="full")

    frozen = records_only(
        folder, question("Q1", blocks='["B1"]', options='{ a = "one", b = "two" }'),
        *PLAN[1:])

    assert frozen.plan.added == {"options": {"Q1": {"options": {"a": "one", "b": "two"}}}}
    assert standing(folder) == "publishable@v2"
    assert [key for key, _text in folded(folder).questions[0].options] == ["a", "b"]


def test_what_no_gate_reads_is_no_difference(folder):
    fold(folder)

    records_only(folder, question("Q1", text="Do they, then?", blocks='["B1"]'),
                 PLAN[1], stage("B1", minutes=30), PLAN[3],
                 '[[commitment]]\nid = "R1"\ntext = "A promise."\nwhere = "part 2"')

    assert versions.current(folded(folder)).n == 2


def test_a_first_version_with_declarations_is_no_change_of_records_only(folder):
    with pytest.raises(Refused, match="the question Q1 is new"):
        records_only(folder)

    assert versions.current(folded(folder)) is None


def test_a_change_of_records_only_needs_a_pointer_of_a_known_shape(folder):
    fold(folder)
    before = raw(folder)

    with pytest.raises(Refused, match="says where the record of the change is"):
        records_only(folder, record=None)
    with pytest.raises(Refused) as refused:
        records_only(folder, record="somewhere")
    assert refused.value.unmet[0].reason.startswith(
        "'somewhere' is not a pointer to the lead's record of the change; PTR below")
    assert "operator" not in refused.value.unmet[0].reason
    with pytest.raises(BadArgument, match="only with a version that changes records"):
        fold(folder, record=WHY)

    assert raw(folder) == before
    assert not versions.file(folder, 2).exists()


def test_fold_takes_the_flag_and_says_what_the_design_stands_at(folder, capsys):
    fold(folder)
    verdict(folder, pass_="full")

    assert main(["fold", ROUND, "--records-only", "--record", "comms:1234567"]) == 0

    out = capsys.readouterr().out
    assert "records only: comms:1234567\n" in out
    assert "standing: lead-decision@v2\n" in out
    assert main(["fold", ROUND, "--records-only"]) == 3
    assert main(["fold", ROUND, "--record", "comms:1234567"]) == 2
    assert versions.current(folded(folder)).n == 2


def test_a_plain_fold_prints_no_standing(folder, capsys):
    fold(folder)

    assert main(["fold", ROUND]) == 0

    out = capsys.readouterr().out
    assert "standing:" not in out and "records only" not in out


# -- which pass is asked for -------------------------------------------------------------

def test_the_pass_asked_for_across_a_design_that_is_sent_back_twice(folder):
    found = asked(folder)
    assert (found.pass_, found.instead) == (None, (f"moxi rounds fold {ROUND}",))
    assert "no version of the design is frozen" in found.why

    fold(folder)
    found = asked(folder)
    assert (found.pass_, found.tier, found.version, found.since, found.instead) == (
        brake.FULL, "second", 1, None, ())
    assert brake.files(folder, found) is None

    verdict(folder, pass_="full")
    found = asked(folder)
    assert (found.pass_, found.tier, found.version, found.since, found.instead) == (
        brake.RECHECK, "third", None, 1, (f"moxi rounds fold {ROUND}",))
    assert str(found.standing) == "fold-first@v1"
    assert brake.files(folder, found) is None

    fold(folder)
    found = asked(folder)
    assert (found.pass_, found.tier, found.version, found.since, found.instead) == (
        brake.RECHECK, "third", 2, 1, ())
    assert brake.files(folder, found) == (versions.file(folder, 1),
                                          versions.file(folder, 2))

    verdict(folder, design=2)
    found = asked(folder)
    assert (found.pass_, found.tier, found.version) == (None, None, None)
    assert "the last 2 verdicts of the design each sent it back" in found.why
    assert found.instead == (
        "state the rule that the findings of the two passes break, and write the "
        "section again from that rule",
        f"moxi rounds fold {ROUND}",
        f"moxi rounds mark {ROUND} thread:NAME --close --record PTR, or --accept-risk "
        "in place of --close; PTR names where the lead wrote why")

    fold(folder)
    found = asked(folder)
    assert found.pass_ is None and len(found.instead) == 1
    assert found.instead[0].startswith(f"moxi rounds mark {ROUND} thread:NAME --close")

    decide(folder)
    found = asked(folder)
    assert (found.pass_, found.instead, found.why) == (
        None, (), "the design stands at lead-decision@v3")


def test_the_pass_asked_for_across_a_design_found_publishable_and_changed(folder):
    fold(folder)
    verdict(folder, "publishable", pass_="full")
    found = asked(folder)
    assert (found.pass_, found.instead, found.why) == (
        None, (), "the design stands at publishable@v1")

    fold(folder)
    found = asked(folder)
    assert (found.pass_, found.tier, found.since, found.version) == (
        brake.RECHECK, "third", 1, 2)
    assert found.why == "the design changed since v1, the last version a pass read"

    verdict(folder, "publishable", design=2)
    assert asked(folder).pass_ is None

    fold(folder)
    fold(folder)
    found = asked(folder)
    assert (found.pass_, found.since, found.version) == (brake.RECHECK, 2, 4)


def test_a_full_pass_is_asked_for_until_one_is_recorded(folder):
    fold(folder)
    verdict(folder, pass_="re-check")
    fold(folder)

    assert asked(folder).pass_ == brake.FULL

    verdict(folder, "publishable", design=2, pass_="full")
    fold(folder)

    assert asked(folder).pass_ == brake.RECHECK


def test_one_verdict_that_sends_the_design_back_between_two_others_is_no_two_in_a_row(
        folder):
    fold(folder)
    verdict(folder, pass_="full")
    fold(folder)
    verdict(folder, "publishable", design=2)
    fold(folder)
    verdict(folder, design=3)
    fold(folder)

    assert asked(folder).pass_ == brake.RECHECK


@pytest.mark.parametrize("flow", ["small", "fix"])
def test_a_flow_that_does_not_verify_its_design_never_meets_the_brake(opened, flow):
    folder = opened(flow)

    assert asked(folder) is None
    assert brake.files(folder, None) is None


def test_the_tier_is_the_one_the_rounds_own_step_names():
    def of(step):
        steps = [{"step": "design"}, step]
        return state.fold([
            {"v": 1, "n": 1, "at": AT, "actor": "planner", "type": "round.opened",
             "description": "a placeholder round", "flow": "full", "steps": steps},
            {"v": 1, "n": 2, "at": AT, "actor": "scout", "type": "design.frozen",
             "version": 1, "digest": "0" * 64},
            {"v": 1, "n": 3, "at": AT, "actor": "critic", "type": "verdict.recorded",
             "kind": "design", "pass": "full", "verdict": "publishable", "design": 1},
            {"v": 1, "n": 4, "at": AT, "actor": "scout", "type": "design.frozen",
             "version": 2, "digest": "1" * 64}])

    named = brake.asked(of({"step": "verify-design", "model": "first",
                            "recheck_model": "fourth"}))
    bare = brake.asked(of({"step": "verify-design", "model": "first"}))

    assert (named.pass_, named.tier) == (brake.RECHECK, "fourth")
    # A step that names no tier for a re-check is re-checked at the tier of its full pass.
    assert (bare.pass_, bare.tier) == (brake.RECHECK, "first")


# -- the lead's decision on a thread -----------------------------------------------------

@pytest.mark.parametrize("decision", brake.DECISIONS)
def test_a_decision_on_a_thread_is_one_event_and_the_design_stands_at_it(
        folder, decision):
    sent_back_twice(folder)

    done = decide(folder, "build.derivation_2", decision)

    assert events(folder)[-1] == {
        **{key: events(folder)[-1][key] for key in ("v", "n", "at")}, "actor": "planner",
        "type": "thread.decided", "thread": "build.derivation_2", "decision": decision,
        "record": WHY}
    assert str(done.standing) == "lead-decision@v3"
    assert (done.decision.thread, done.decision.version) == ("build.derivation_2", 3)
    assert versions.standing(folded(folder)).decision == done.decision


def test_a_thread_is_not_decided_before_two_verdicts_in_a_row_sent_the_design_back(
        folder):
    def refused(said):
        before = raw(folder)
        with pytest.raises(Refused) as found:
            decide(folder)
        assert [one.reason.partition(": ")[2] for one in found.value.unmet] == [said]
        assert found.value.unmet[0].fix == f"moxi rounds show {ROUND}"
        assert raw(folder) == before

    refused("no verdict of the design is recorded")
    fold(folder)
    refused("no verdict of the design is recorded")
    verdict(folder, pass_="full")
    fold(folder)
    refused("one has, for v1")
    verdict(folder, "publishable", design=2)
    fold(folder)
    refused("none has")
    verdict(folder, design=3)
    fold(folder)
    refused("one has, for v3")


def test_a_thread_is_not_decided_before_the_design_was_frozen_again(folder):
    fold(folder)
    verdict(folder, pass_="full")
    fold(folder)
    verdict(folder, design=2)
    before = raw(folder)

    with pytest.raises(Refused) as refused:
        decide(folder)

    assert [(one.reason.partition(":")[0], one.fix) for one in refused.value.unmet] == [
        ("the design's text was not changed and frozen since the last verdict sent "
         "it back",
         f"moxi rounds fold {ROUND}")]
    assert raw(folder) == before

    fold(folder)
    assert str(decide(folder).standing) == "lead-decision@v3"


def test_a_decision_on_a_thread_needs_a_pointer_of_a_known_shape(folder):
    sent_back_twice(folder)
    before = raw(folder)

    with pytest.raises(Refused, match="says where the lead wrote why"):
        decide(folder, record=None)
    with pytest.raises(Refused) as refused:
        decide(folder, record="somewhere")
    assert refused.value.unmet[0].reason.startswith(
        "'somewhere' is not a pointer to where the lead wrote why; PTR below")
    assert "operator" not in refused.value.unmet[0].reason

    assert raw(folder) == before


@pytest.mark.parametrize("name", ["", "two words", "a/b", "a:b", "né"])
def test_a_threads_name_is_letters_digits_dots_underscores_and_dashes(folder, name):
    sent_back_twice(folder)
    before = raw(folder)

    with pytest.raises(BadArgument, match="is not a thread's name"):
        decide(folder, name)

    assert raw(folder) == before


def test_a_decision_is_close_or_accept_risk(folder):
    sent_back_twice(folder)

    with pytest.raises(BadArgument, match="is not what is decided of a thread"):
        decide(folder, decision="reopen")


def test_a_round_that_ended_takes_no_decision_on_a_thread(folder):
    sent_back_twice(folder)
    dropping.drop(ROUND, by="operator", record="comms:1234567", actor="planner")
    before = raw(folder)

    with pytest.raises(Refused, match="was dropped and stays as it ended"):
        decide(folder)

    assert raw(folder) == before


def test_a_verdict_recorded_after_a_decision_is_the_standing(folder):
    sent_back_twice(folder)
    decide(folder)

    verdict(folder, "publishable", design=3)

    assert standing(folder) == "publishable@v3"


def test_a_version_frozen_after_a_decision_is_asked_for_a_recheck_and_no_second_thread(
        folder):
    sent_back_twice(folder)
    decide(folder)

    fold(folder, *PLAN, stage("B9"))

    assert standing(folder) == "none"
    found = asked(folder)
    assert (found.pass_, found.since, found.version, found.instead) == (
        brake.RECHECK, 2, 4, ())
    before = raw(folder)
    with pytest.raises(Refused) as refused:
        decide(folder, "another", brake.ACCEPT_RISK)
    assert [one.reason.partition(": ")[2] for one in refused.value.unmet] == [
        "none has since the lead's last decision"]
    assert raw(folder) == before


def test_a_second_thread_is_decided_after_two_more_verdicts_sent_the_design_back(
        folder):
    sent_back_twice(folder)
    decide(folder)
    fold(folder)
    verdict(folder, design=4)
    fold(folder)
    with pytest.raises(Refused, match="one has since the lead's last decision, for v4"):
        decide(folder, "another")
    verdict(folder, design=5)
    assert asked(folder).pass_ is None
    fold(folder)

    assert str(decide(folder, "another", brake.ACCEPT_RISK).standing) == (
        "lead-decision@v6")
    assert [(one.thread, one.decision, one.version)
            for one in folded(folder).threads] == [
        ("the-holds", "close", 3), ("another", "accept-risk", 6)]


def test_the_count_starts_again_after_a_change_of_records_only_made_the_decision(
        folder):
    fold(folder)
    verdict(folder, pass_="full")
    fold(folder)
    verdict(folder, design=2)
    records_only(folder)
    assert standing(folder) == "lead-decision@v3"

    fold(folder, *PLAN, stage("B9"))

    assert asked(folder).pass_ == brake.RECHECK
    with pytest.raises(Refused, match="none has since the lead's last decision"):
        decide(folder)


def test_a_verdict_that_sends_back_a_publishable_version_leaves_its_standing(
        folder, capsys):
    fold(folder)
    verdict(folder, "publishable", pass_="full")

    late = verdict(folder)

    assert str(late.standing) == "publishable@v1"
    found = asked(folder)
    assert (found.pass_, found.instead) == (None, ())
    assert versions.standing(folded(folder)).verdict.verdict == "publishable"
    # It is in the record and shown all the same.
    assert [one.verdict for one in folded(folder).verdicts] == [
        "publishable", "fold-first"]
    assert main(["show", ROUND]) == 0
    assert "fold-first for v1" in capsys.readouterr().out
    # A change of records only still carries what the version stands at.
    assert str(records_only(folder).standing) == "publishable@v2"


def test_a_verdict_that_sends_back_a_version_the_lead_decided_leaves_its_standing(
        folder):
    sent_back_twice(folder)
    decided = decide(folder)

    assert str(verdict(folder, design=3).standing) == "lead-decision@v3"
    assert versions.standing(folded(folder)).decision == decided.decision
    assert asked(folder).pass_ is None


def test_a_verdict_that_sends_back_a_change_of_records_the_lead_decided_by_leaves_it(
        folder):
    fold(folder)
    verdict(folder, pass_="full")
    records_only(folder)
    assert standing(folder) == "lead-decision@v2"

    assert str(verdict(folder, design=2).standing) == "lead-decision@v2"
    # The version before was not verified, and a verdict of it is still its standing.
    verdict(folder, design=1)
    assert standing(folder) == "lead-decision@v2"


def test_a_verdict_that_sends_back_a_version_carried_as_publishable_leaves_it(folder):
    fold(folder)
    verdict(folder, "publishable", pass_="full")
    records_only(folder)

    assert str(verdict(folder, design=2).standing) == "publishable@v2"


def test_a_verdict_that_sends_back_a_verified_version_is_not_counted(folder):
    fold(folder)
    verdict(folder, "publishable", pass_="full")
    verdict(folder)
    verdict(folder)
    assert len(folded(folder).verdicts) == 3
    assert versions.sent_back(folded(folder)) == ()

    fold(folder, *PLAN, stage("B9"))

    assert asked(folder).pass_ == brake.RECHECK
    with pytest.raises(Refused, match="none has"):
        decide(folder)


def test_a_verdict_that_sends_back_a_version_the_lead_decided_is_not_counted(folder):
    sent_back_twice(folder)
    decide(folder)
    verdict(folder, design=3)
    verdict(folder, design=3)

    assert versions.sent_back(folded(folder)) == ()
    fold(folder)
    assert asked(folder).pass_ == brake.RECHECK


def test_a_verdict_that_sends_back_an_older_version_is_not_counted(folder):
    fold(folder)
    verdict(folder, pass_="full")
    fold(folder)
    verdict(folder, design=1)
    assert [one.design for one in versions.sent_back(folded(folder))] == [1]
    assert asked(folder).pass_ == brake.RECHECK

    verdict(folder, design=2)

    assert [one.design for one in versions.sent_back(folded(folder))] == [1, 2]
    assert asked(folder).pass_ is None


def test_a_design_frozen_with_the_same_text_does_not_open_the_gate(folder):
    fold(folder)
    verdict(folder, pass_="full")
    fold(folder)
    verdict(folder, design=2)
    freezing.freeze(ROUND, actor="scout")
    assert folded(folder).versions[-1].digest == folded(folder).versions[-2].digest
    before = raw(folder)

    with pytest.raises(Refused, match="text was not changed and frozen"):
        decide(folder)

    assert raw(folder) == before
    assert len(asked(folder).instead) == 3
    fold(folder)
    assert len(asked(folder).instead) == 1
    assert str(decide(folder).standing) == "lead-decision@v4"


def test_the_same_decision_run_again_is_found_and_not_recorded_twice(folder, capsys):
    sent_back_twice(folder)
    first = decide(folder)
    before = raw(folder)

    again = decide(folder)

    assert raw(folder) == before
    assert (again.found, again.event, again.decision) == (True, None, first.decision)
    assert str(again.standing) == "lead-decision@v3"
    assert len(folded(folder).threads) == 1
    assert main(["mark", ROUND, "thread:the-holds", "--close", "--record", WHY]) == 0
    assert "recorded already" in capsys.readouterr().out
    assert raw(folder) == before
    # Another word, another thread or another version is not the same decision.
    with pytest.raises(Refused):
        decide(folder, decision=brake.ACCEPT_RISK)
    with pytest.raises(Refused):
        decide(folder, "another")
    fold(folder)
    with pytest.raises(Refused):
        decide(folder)
    assert len(folded(folder).threads) == 1


def test_mark_decides_a_thread(folder, capsys):
    sent_back_twice(folder)

    assert main(["mark", ROUND, "thread:the-holds", "--accept-risk", "--record",
                 WHY, "--as", "planner"]) == 0

    assert capsys.readouterr().out == (
        f"{ROUND} thread:the-holds: accept-risk   record: {WHY}\n"
        "standing: lead-decision@v3\n")
    before = raw(folder)
    assert main(["mark", ROUND, "the-holds", "--close", "--record", WHY]) == 2
    assert "names no thread" in capsys.readouterr().err
    assert main(["mark", ROUND, "thread:the-holds", "--close"]) == 3
    assert main(["mark", ROUND, "thread:the holds", "--close", "--record", WHY]) == 2
    assert main(["mark", ROUND, "thread:the-holds", "--close", "--accept-risk",
                 "--record", WHY]) == 2
    assert main(["mark", ROUND, "--close", "--record", WHY]) == 2
    assert raw(folder) == before


# -- a second full pass ------------------------------------------------------------------

def test_a_second_full_pass_is_recorded_with_a_flag(folder, capsys):
    fold(folder)
    first = verdict(folder, pass_="full")
    recheck = verdict(folder, pass_="re-check")
    second = verdict(folder, "publishable", pass_="full")
    third = verdict(folder, pass_="full")

    assert [one.verdict.flags for one in (first, recheck, second, third)] == [
        (), (), ("extra-full-pass",), ("extra-full-pass",)]
    assert "flags" not in first.event and "flags" not in recheck.event
    assert second.event["flags"] == [brake.EXTRA_FULL_PASS]
    assert str(second.standing) == "publishable@v1"
    assert main(["show", ROUND]) == 0
    assert capsys.readouterr().out.count("model a-model   flags: extra-full-pass\n") == 2


def test_a_full_pass_after_a_re_check_alone_is_no_extra(folder, capsys):
    fold(folder)
    verdict(folder, pass_="re-check")
    path = folder / "full.md"
    path.write_text('```toml rounds\nkind = "design"\nverdict = "publishable"\n'
                    'pass = "full"\nby = "critic"\nmodel = "a-model"\ndesign = 1\n```\n',
                    encoding="utf-8")

    assert main(["verdict", ROUND, str(path)]) == 0

    assert "flags:" not in capsys.readouterr().out
    assert folded(folder).verdicts[-1].flags == ()


def test_verdict_prints_the_flag(folder, capsys):
    fold(folder)
    verdict(folder, pass_="full")
    path = folder / "again.md"
    path.write_text('```toml rounds\nkind = "design"\nverdict = "fold-first"\n'
                    'pass = "full"\nby = "critic"\nmodel = "a-model"\ndesign = 1\n```\n',
                    encoding="utf-8")

    assert main(["verdict", ROUND, str(path)]) == 0

    assert "flags: extra-full-pass\n" in capsys.readouterr().out


# -- shown -------------------------------------------------------------------------------

def test_the_standing_the_decisions_and_the_records_only_versions_are_shown(
        folder, capsys):
    sent_back_twice(folder)
    done = decide(folder)
    records_only(folder, record="comms:1234567")
    at, frozen_at = done.decision.at, versions.current(folded(folder)).at

    assert main(["show", ROUND]) == 0
    shown = capsys.readouterr().out
    assert main(["show", ROUND, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    note = (folder / "round-note.md").read_text(encoding="utf-8")

    lines = [f"records only: v4, {frozen_at} by scout   record: comms:1234567",
             "standing: lead-decision@v4",
             f"thread the-holds: close at v3, {at} by planner   record: {WHY}"]
    assert "\n".join(lines) + "\n" in shown
    assert "thread.decided  the-holds, close\n" in shown
    for line in lines:
        assert f"- {line}\n" in note
    assert data["standing"] == "lead-decision@v4"
    assert data["threads"] == [{
        "n": done.event["n"], "thread": "the-holds", "decision": "close", "version": 3,
        "record": WHY, "by": "planner", "at": at}]
    assert [(one["version"], one["records_only"], one["record"])
            for one in data["versions"]][-2:] == [(3, False, None),
                                                  (4, True, "comms:1234567")]


def test_a_round_with_no_decision_and_no_records_only_version_shows_neither(
        folder, capsys):
    fold(folder)

    assert main(["show", ROUND]) == 0

    shown = capsys.readouterr().out
    assert "thread " not in shown and "records only" not in shown
    assert "standing: none\n" in shown


def test_the_decisions_and_the_carried_standing_are_in_the_index_and_the_list(
        folder, capsys):
    sent_back_twice(folder)
    done = decide(folder, decision=brake.ACCEPT_RISK)
    records_only(folder)

    found = index.read("proj-a", folder.parent, "a-round")

    assert found.of(index.THREAD) == (index.Item(index.THREAD, "t1", {
        "thread": "the-holds", "decision": "accept-risk", "version": 3, "record": WHY,
        "by": "planner", "at": done.decision.at}),)
    assert [(item.body["standing"], item.body["records_only"])
            for item in found.of(index.VERSION)] == [("lead-decision@v4", True)]
    assert main(["list"]) == 0
    assert (f"{ROUND}   open   flow full   step docket, ready   "
            "design: lead-decision@v4   ") in capsys.readouterr().out
