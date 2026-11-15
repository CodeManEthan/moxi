"""The brake on verifying a design: which pass is asked for next, what a version frozen
as a change of records only stands at and when it is refused, the lead's decision on a
thread, and the flag on a second full pass."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import (brake, dropping, freezing, index, log, opening, questions, ruling,
                         state, verdicts, versions)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, BadBlock, Refused

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
        opening.open_round(ROUND, description="a placeholder round", by="planner",
                           flow=flow)
        capsys.readouterr()
        return tmp_path / "notes" / "rounds" / "a-round"

    return open_on


@pytest.fixture(name="round_dir")
def round_dir_fixture(opened):
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


def fold(round_dir, *entries, **how):
    """Write `design.md` with a block of `entries`, the plan when none is given, and
    freeze it. The prose differs each time, as a design's does."""
    text = (f"# A design\n\nDraft {next(_folds)}.\n\n```toml rounds\n"
            + "\n\n".join(entries or PLAN) + "\n```\n")
    (round_dir / "design.md").write_text(text, encoding="utf-8")
    return freezing.freeze(ROUND, by="scout", **how)


def records_only(round_dir, *entries, record=WHY):
    return fold(round_dir, *entries, records_only=True, record=record)


_reports = iter(range(1, 1000))


def verdict(round_dir, word="fold-first", *, design=1, pass_="re-check"):
    """Write a design report in the round's folder and record it."""
    count = next(_reports)
    keys = ['kind = "design"', f'verdict = "{word}"', f'pass = "{pass_}"',
            'by = "critic"', 'model = "a-model"', f"design = {design}"]
    path = round_dir / f"report-{count}.md"
    path.write_text(f"# A report\n\nPass {count}.\n\n```toml rounds\n"
                    + "\n".join(keys) + "\n```\n", encoding="utf-8")
    return verdicts.record(ROUND, path, by="critic")


def events(round_dir):
    return log.read(log.round_log(round_dir))


def folded(round_dir):
    return state.fold(events(round_dir))


def raw(round_dir) -> bytes:
    return log.round_log(round_dir).read_bytes()


def standing(round_dir) -> str:
    return str(versions.standing(folded(round_dir)))


def asked(round_dir):
    return brake.asked(folded(round_dir), name=ROUND)


def decide(round_dir, thread="the-holds", decision=brake.CLOSE, record=WHY):
    return brake.decide(ROUND, thread, decision, record=record, by="planner")


def sent_back_twice(round_dir):
    """A design whose two passes each sent it back, frozen again since."""
    fold(round_dir)
    verdict(round_dir, pass_="full")
    fold(round_dir)
    verdict(round_dir, design=2)
    return fold(round_dir)


# -- what a change of records only stands at ---------------------------------------------

def test_a_change_of_records_only_carries_a_publishable_standing(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")

    frozen = records_only(round_dir)

    assert (frozen.version, str(frozen.standing)) == (2, "publishable@v2")
    assert standing(round_dir) == "publishable@v2"
    assert versions.standing(folded(round_dir)).verdict.design == 1
    assert asked(round_dir).pass_ is None


def test_a_change_of_records_only_after_the_design_was_sent_back_is_the_leads_decision(
        round_dir):
    fold(round_dir)
    sent = verdict(round_dir, pass_="full")

    records_only(round_dir)

    found = versions.standing(folded(round_dir))
    assert str(found) == "lead-decision@v2"
    assert (found.fold, found.verdict, found.decision) == (2, sent.verdict, None)
    assert asked(round_dir).pass_ is None


def test_a_change_of_records_only_after_an_unchecked_version_stays_at_none(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")
    fold(round_dir)

    records_only(round_dir)

    assert standing(round_dir) == "none"
    found = asked(round_dir)
    assert (found.pass_, found.since, found.version) == (brake.RECHECK, 1, 3)


def test_a_change_of_records_only_carries_the_leads_decision(round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")
    records_only(round_dir)

    records_only(round_dir)

    assert standing(round_dir) == "lead-decision@v3"


def test_a_change_of_records_only_on_a_design_never_verified_stays_at_none(round_dir):
    fold(round_dir)

    records_only(round_dir)

    assert standing(round_dir) == "none"
    assert asked(round_dir).pass_ == brake.FULL


def test_a_version_found_publishable_stands_for_later_changes_of_records_only(round_dir):
    fold(round_dir)
    records_only(round_dir)
    records_only(round_dir)

    verdict(round_dir, "publishable", pass_="full")

    assert standing(round_dir) == "publishable@v3"


def test_a_publishable_verdict_does_not_reach_past_a_change_of_mechanism(round_dir):
    fold(round_dir)
    fold(round_dir)
    records_only(round_dir)

    verdict(round_dir, "publishable", pass_="full")

    assert standing(round_dir) == "none"


def test_a_verdict_that_sends_an_earlier_version_back_leaves_the_carried_standing(
        round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")
    records_only(round_dir)

    verdict(round_dir, design=1)

    assert standing(round_dir) == "publishable@v2"


def test_a_plain_version_after_a_carried_standing_stands_at_none(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")
    records_only(round_dir)

    fold(round_dir)

    assert standing(round_dir) == "none"


# -- a design frozen again with its text unchanged ---------------------------------------

def again(round_dir, **how):
    """Freeze the design as it is, with no edit since the version before."""
    frozen = freezing.freeze(ROUND, by="scout", **how)
    found = folded(round_dir)
    assert found.versions[-1].digest == found.versions[-2].digest
    return frozen


def test_an_unchanged_design_frozen_again_keeps_a_publishable_standing(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")

    frozen = again(round_dir)

    assert str(frozen.standing) == standing(round_dir) == "publishable@v2"
    assert asked(round_dir).pass_ is None and asked(round_dir).instead == ()
    assert again(round_dir).version == 3
    assert standing(round_dir) == "publishable@v3"


def test_an_unchanged_design_frozen_again_keeps_the_leads_decision_on_a_thread(round_dir):
    sent_back_twice(round_dir)
    decided = decide(round_dir)

    again(round_dir)

    found = versions.standing(folded(round_dir))
    assert str(found) == "lead-decision@v4"
    assert found.decision.thread == decided.event["thread"]


def test_an_unchanged_design_frozen_again_keeps_a_decision_made_by_a_change_of_records(
        round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")
    records_only(round_dir)

    again(round_dir)

    found = versions.standing(folded(round_dir))
    assert str(found) == "lead-decision@v3"
    assert found.fold == 2


def test_an_unchanged_design_frozen_again_is_still_sent_back(round_dir):
    fold(round_dir)
    sent = verdict(round_dir, pass_="full")

    again(round_dir)

    found = versions.standing(folded(round_dir))
    assert str(found) == "fold-first@v2"
    assert found.verdict.n == sent.event["n"]
    # The freezing is no decision of the lead's: the verdict is still the one counted.
    assert [one.design for one in versions.sent_back(folded(round_dir))] == [1]
    assert not versions.lead_decided(folded(round_dir))


def test_no_pass_is_asked_for_over_a_text_that_was_sent_back_and_not_changed(round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")

    again(round_dir)

    found = asked(round_dir)
    assert (found.pass_, found.since, found.version) == (brake.RECHECK, 1, None)
    assert found.instead == ("change design.md to answer what the pass found",
                             f"moxi rounds fold {ROUND}")
    assert "not changed since" in found.why
    # Freezing the same text once more is asked the same again.
    again(round_dir)
    assert asked(round_dir).instead == found.instead
    fold(round_dir)
    found = asked(round_dir)
    assert (found.pass_, found.since, found.version) == (brake.RECHECK, 1, 4)
    assert found.instead == ()


def test_a_design_sent_back_and_not_frozen_since_is_asked_for_the_fold_alone(round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")

    assert asked(round_dir).instead == (f"moxi rounds fold {ROUND}",)


def test_an_unchanged_design_frozen_again_with_no_standing_has_none(round_dir):
    fold(round_dir)
    again(round_dir)
    assert standing(round_dir) == "none"

    verdict(round_dir, "publishable", pass_="full")
    fold(round_dir)
    again(round_dir)

    assert standing(round_dir) == "none"


def test_a_fold_run_again_after_it_was_recorded_costs_no_pass(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")
    fold(round_dir)
    again(round_dir)
    assert standing(round_dir) == "none"
    assert (asked(round_dir).since, asked(round_dir).version) == (1, 3)

    verdict(round_dir, "publishable", design=2)

    assert standing(round_dir) == "publishable@v3"
    assert asked(round_dir).pass_ is None


def test_a_design_frozen_with_the_text_of_an_older_version_starts_at_none(round_dir):
    fold(round_dir)
    first = (round_dir / "design.md").read_bytes()
    verdict(round_dir, "publishable", pass_="full")
    fold(round_dir)
    (round_dir / "design.md").write_bytes(first)

    freezing.freeze(ROUND, by="scout")

    found = folded(round_dir)
    assert found.versions[2].digest == found.versions[0].digest
    assert standing(round_dir) == "none"
    verdict(round_dir, "publishable", design=1)
    assert standing(round_dir) == "none"


@pytest.mark.parametrize("word", ["publishable", "fold-first"])
def test_a_verdict_for_a_version_whose_text_is_the_current_one_gives_the_standing(
        round_dir, word):
    fold(round_dir)
    again(round_dir)
    again(round_dir)

    recorded = verdict(round_dir, word, design=1, pass_="full")

    assert str(recorded.standing) == standing(round_dir) == f"{word}@v3"
    assert versions.standing(folded(round_dir)).verdict.design == 1


def test_a_verdict_for_an_unchanged_text_does_not_reach_past_a_change_of_it(round_dir):
    fold(round_dir)
    again(round_dir)
    fold(round_dir)

    verdict(round_dir, design=1, pass_="full")

    assert standing(round_dir) == "none"


def test_a_verdict_that_sends_back_an_unchanged_text_leaves_a_verified_standing(
        round_dir):
    fold(round_dir)
    again(round_dir)
    verdict(round_dir, "publishable", design=2, pass_="full")

    verdict(round_dir, design=1)

    assert standing(round_dir) == "publishable@v2"
    assert versions.sent_back(folded(round_dir)) == ()


def test_a_change_of_records_only_decides_whatever_the_text(round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")

    again(round_dir, records_only=True, record=WHY)

    assert standing(round_dir) == "lead-decision@v2"
    assert versions.lead_decided(folded(round_dir))
    assert versions.sent_back(folded(round_dir)) == ()


def test_a_change_of_records_only_with_the_text_unchanged_and_no_standing_has_none(
        round_dir):
    fold(round_dir)

    again(round_dir, records_only=True, record=WHY)

    assert standing(round_dir) == "none"


# -- a change of records only: the verb --------------------------------------------------

def test_a_change_of_records_only_is_one_event_with_its_pointer(round_dir):
    fold(round_dir)

    frozen = records_only(round_dir)

    assert [one["type"] for one in events(round_dir)] == [
        "round.opened", "design.frozen", "design.frozen"]
    assert (frozen.event["records_only"], frozen.event["record"]) == (True, WHY)
    assert "record" not in events(round_dir)[1]
    newest = versions.current(folded(round_dir))
    assert (newest.n, newest.records_only, newest.record) == (2, True, WHY)
    assert versions.file(round_dir, 2).is_file()


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
        round_dir, case):
    block, said = CHANGED[case]
    fold(round_dir)
    before = raw(round_dir)

    with pytest.raises(Refused) as refused:
        records_only(round_dir, *block)

    assert [one.reason for one in refused.value.unmet] == [
        f"this is no change of records only: {said}; a change to what the gates read "
        "is frozen as a plain version, which a re-check then reads"]
    assert refused.value.unmet[0].fix == f"moxi rounds fold {ROUND}"
    assert raw(round_dir) == before
    assert not versions.file(round_dir, 2).exists()


def test_every_difference_is_named_in_one_refusal(round_dir):
    fold(round_dir)

    with pytest.raises(Refused) as refused:
        records_only(round_dir, question("Q2"), question("Q3"), stage("B1"))

    assert len(refused.value.unmet) == 3


def test_a_change_of_records_only_is_compared_with_the_version_before_not_the_record(
        round_dir):
    fold(round_dir)
    fold(round_dir, *PLAN, question("Q3"))

    # Q3 is in the record, and the version before declared it: leaving it out differs.
    with pytest.raises(Refused, match="the question Q3 is gone"):
        records_only(round_dir)


def settle(item: str, **more):
    """Record the operator's ruling `item` on the round."""
    return ruling.rule(ROUND, item=item, record="comms:1234567", by="planner", **more)


SETTLED = {"ruled": {"item": "Q1", "text": "They do."},
           "withdrawn": {"item": "withdraw:Q1"}}


@pytest.mark.parametrize("how", SETTLED)
def test_a_block_without_a_question_since_settled_is_a_change_of_records_only(
        round_dir, how):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")
    settle(**SETTLED[how])

    # The answered question can't stay in the block, and leaving it out changes
    # nothing a gate reads.
    with pytest.raises(BadBlock):
        fold(round_dir)
    frozen = records_only(round_dir, *PLAN[1:])

    assert (frozen.version, str(frozen.standing)) == (2, "publishable@v2")
    assert standing(round_dir) == "publishable@v2"
    assert asked(round_dir).pass_ is None
    assert [one["id"] for one in versions.declarations(folded(round_dir))["question"]] == [
        "Q2"]


def test_a_block_without_a_settled_question_stays_one_of_records_only_after(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")
    settle(**SETTLED["ruled"])
    records_only(round_dir, *PLAN[1:])

    frozen = records_only(round_dir, *PLAN[1:])

    assert (frozen.version, str(frozen.standing)) == (3, "publishable@v3")


@pytest.mark.parametrize("how", ["open", "parked"])
def test_a_block_without_a_question_still_asked_is_no_change_of_records_only(
        round_dir, how):
    fold(round_dir)
    if how == "parked":
        settle("park:Q1")
    assert questions.find(folded(round_dir), "Q1").state == how
    before = raw(round_dir)

    with pytest.raises(Refused) as refused:
        records_only(round_dir, *PLAN[1:])

    assert [one.reason for one in refused.value.unmet] == [
        "this is no change of records only: the question Q1 is gone; a change to what "
        "the gates read is frozen as a plain version, which a re-check then reads"]
    assert raw(round_dir) == before
    assert not versions.file(round_dir, 2).exists()


def test_a_settled_question_left_out_beside_a_changed_stage_is_refused_for_the_stage(
        round_dir):
    fold(round_dir)
    settle(**SETTLED["ruled"])
    before = raw(round_dir)

    with pytest.raises(Refused) as refused:
        records_only(round_dir, PLAN[1], PLAN[2], stage("B2"))

    assert [one.reason for one in refused.value.unmet] == [
        "this is no change of records only: B2 waited for B1 and now waits for "
        "nothing; a change to what the gates read is frozen as a plain version, which "
        "a re-check then reads"]
    assert raw(round_dir) == before


def test_a_settled_question_left_out_beside_another_still_asked_is_refused_for_that(
        round_dir):
    fold(round_dir)
    settle(**SETTLED["ruled"])

    with pytest.raises(Refused) as refused:
        records_only(round_dir, *PLAN[2:])

    assert [one.reason.split(";")[0] for one in refused.value.unmet] == [
        "this is no change of records only: the question Q2 is gone"]


@pytest.mark.parametrize(("settled", "said"), [
    ((), ["the question Q1 is gone", "the question Q2 is gone"]),
    (("Q1",), ["the question Q2 is gone"]),
    (("Q1", "Q2"), []),
    # Only a question that is gone is passed over: one that stays is still compared.
    (("Q3",), ["the question Q1 is gone", "the question Q2 is gone"]),
])
def test_only_a_settled_question_that_is_gone_is_no_difference(settled, said):
    before = {"question": [{"id": "Q1", "blocks": ["B1"]}, {"id": "Q2"}],
              "stage": [{"id": "B1"}]}
    now = {"question": [], "stage": [{"id": "B1"}]}

    assert brake.differences(before, now, settled=settled) == said


def test_a_settled_question_still_in_the_block_is_compared_as_any_other():
    before = {"question": [{"id": "Q1", "blocks": ["B1"]}]}
    now = {"question": [{"id": "Q1", "blocks": "all"}, {"id": "Q2"}]}

    assert brake.differences(before, now, settled=("Q1", "Q2")) == [
        "the question Q2 is new", "Q1 held up B1 and now holds up all"]


def test_a_stage_that_is_gone_is_a_difference_whatever_is_settled():
    before = {"stage": [{"id": "B1"}, {"id": "B2"}]}

    assert brake.differences(before, {"stage": [{"id": "B1"}]}, settled=("B2",)) == [
        "the stage B2 is gone"]


def test_a_block_that_only_adds_options_is_a_change_of_records_only(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")

    frozen = records_only(
        round_dir, question("Q1", blocks='["B1"]', options='{ a = "one", b = "two" }'),
        *PLAN[1:])

    assert frozen.plan.added == {"options": {"Q1": {"options": {"a": "one", "b": "two"}}}}
    assert standing(round_dir) == "publishable@v2"
    assert [key for key, _text in folded(round_dir).questions[0].options] == ["a", "b"]


def test_what_no_gate_reads_is_no_difference(round_dir):
    fold(round_dir)

    records_only(round_dir, question("Q1", text="Do they, then?", blocks='["B1"]'),
                 PLAN[1], stage("B1", minutes=30), PLAN[3],
                 '[[commitment]]\nid = "R1"\ntext = "A promise."\nwhere = "part 2"')

    assert versions.current(folded(round_dir)).n == 2


def test_a_first_version_with_declarations_is_no_change_of_records_only(round_dir):
    with pytest.raises(Refused, match="the question Q1 is new"):
        records_only(round_dir)

    assert versions.current(folded(round_dir)) is None


def test_a_change_of_records_only_needs_a_pointer_of_a_known_shape(round_dir):
    fold(round_dir)
    before = raw(round_dir)

    with pytest.raises(Refused, match="says where the record of the change is"):
        records_only(round_dir, record=None)
    with pytest.raises(Refused) as refused:
        records_only(round_dir, record="somewhere")
    assert refused.value.unmet[0].reason.startswith(
        "'somewhere' is not a pointer to the lead's record of the change; PTR below")
    assert "operator" not in refused.value.unmet[0].reason
    with pytest.raises(BadArgument, match="only with a version that changes records"):
        fold(round_dir, record=WHY)

    assert raw(round_dir) == before
    assert not versions.file(round_dir, 2).exists()


def test_fold_takes_the_flag_and_says_what_the_design_stands_at(round_dir, capsys):
    fold(round_dir)
    verdict(round_dir, pass_="full")

    assert main(["fold", ROUND, "--records-only", "--record", "comms:1234567"]) == 0

    out = capsys.readouterr().out
    assert "records only: comms:1234567\n" in out
    assert "standing: lead-decision@v2\n" in out
    assert main(["fold", ROUND, "--records-only"]) == 3
    assert main(["fold", ROUND, "--record", "comms:1234567"]) == 2
    assert versions.current(folded(round_dir)).n == 2


def test_a_plain_fold_prints_no_standing(round_dir, capsys):
    fold(round_dir)

    assert main(["fold", ROUND]) == 0

    out = capsys.readouterr().out
    assert "standing:" not in out and "records only" not in out


# -- which pass is asked for -------------------------------------------------------------

def test_the_pass_asked_for_across_a_design_that_is_sent_back_twice(round_dir):
    found = asked(round_dir)
    assert (found.pass_, found.instead) == (None, (f"moxi rounds fold {ROUND}",))
    assert "no version of the design is frozen" in found.why

    fold(round_dir)
    found = asked(round_dir)
    assert (found.pass_, found.tier, found.version, found.since, found.instead) == (
        brake.FULL, "second", 1, None, ())
    assert brake.files(round_dir, found) is None

    verdict(round_dir, pass_="full")
    found = asked(round_dir)
    assert (found.pass_, found.tier, found.version, found.since, found.instead) == (
        brake.RECHECK, "third", None, 1, (f"moxi rounds fold {ROUND}",))
    assert str(found.standing) == "fold-first@v1"
    assert brake.files(round_dir, found) is None

    fold(round_dir)
    found = asked(round_dir)
    assert (found.pass_, found.tier, found.version, found.since, found.instead) == (
        brake.RECHECK, "third", 2, 1, ())
    assert brake.files(round_dir, found) == (versions.file(round_dir, 1),
                                          versions.file(round_dir, 2))

    verdict(round_dir, design=2)
    found = asked(round_dir)
    assert (found.pass_, found.tier, found.version) == (None, None, None)
    assert "the last 2 verdicts of the design each sent it back" in found.why
    assert found.instead == (
        "state the rule that the findings of the two passes break, and write the "
        "section again from that rule",
        f"moxi rounds fold {ROUND}",
        f"moxi rounds mark {ROUND} thread:NAME --close --record PTR, or --accept-risk "
        "in place of --close; PTR names where the lead wrote why")

    fold(round_dir)
    found = asked(round_dir)
    assert found.pass_ is None and len(found.instead) == 1
    assert found.instead[0].startswith(f"moxi rounds mark {ROUND} thread:NAME --close")

    decide(round_dir)
    found = asked(round_dir)
    assert (found.pass_, found.instead, found.why) == (
        None, (), "the design stands at lead-decision@v3")


def test_the_pass_asked_for_across_a_design_found_publishable_and_changed(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")
    found = asked(round_dir)
    assert (found.pass_, found.instead, found.why) == (
        None, (), "the design stands at publishable@v1")

    fold(round_dir)
    found = asked(round_dir)
    assert (found.pass_, found.tier, found.since, found.version) == (
        brake.RECHECK, "third", 1, 2)
    assert found.why == "the design changed since v1, the last version a pass read"

    verdict(round_dir, "publishable", design=2)
    assert asked(round_dir).pass_ is None

    fold(round_dir)
    fold(round_dir)
    found = asked(round_dir)
    assert (found.pass_, found.since, found.version) == (brake.RECHECK, 2, 4)


def test_a_full_pass_is_asked_for_until_one_is_recorded(round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="re-check")
    fold(round_dir)

    assert asked(round_dir).pass_ == brake.FULL

    verdict(round_dir, "publishable", design=2, pass_="full")
    fold(round_dir)

    assert asked(round_dir).pass_ == brake.RECHECK


def test_one_verdict_that_sends_the_design_back_between_two_others_is_no_two_in_a_row(
        round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")
    fold(round_dir)
    verdict(round_dir, "publishable", design=2)
    fold(round_dir)
    verdict(round_dir, design=3)
    fold(round_dir)

    assert asked(round_dir).pass_ == brake.RECHECK


@pytest.mark.parametrize("flow", ["small", "fix"])
def test_a_flow_that_does_not_verify_its_design_never_meets_the_brake(opened, flow):
    round_dir = opened(flow)

    assert asked(round_dir) is None
    assert brake.files(round_dir, None) is None


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
        round_dir, decision):
    sent_back_twice(round_dir)

    done = decide(round_dir, "build.derivation_2", decision)

    assert events(round_dir)[-1] == {
        **{key: events(round_dir)[-1][key] for key in ("v", "n", "at")}, "actor": "planner",
        "type": "thread.decided", "thread": "build.derivation_2", "decision": decision,
        "record": WHY}
    assert str(done.standing) == "lead-decision@v3"
    assert (done.decision.thread, done.decision.version) == ("build.derivation_2", 3)
    assert versions.standing(folded(round_dir)).decision == done.decision


def test_a_thread_is_not_decided_before_two_verdicts_in_a_row_sent_the_design_back(
        round_dir):
    def refused(said):
        before = raw(round_dir)
        with pytest.raises(Refused) as found:
            decide(round_dir)
        assert [one.reason.partition(": ")[2] for one in found.value.unmet] == [said]
        assert found.value.unmet[0].fix == f"moxi rounds show {ROUND}"
        assert raw(round_dir) == before

    refused("no verdict of the design is recorded")
    fold(round_dir)
    refused("no verdict of the design is recorded")
    verdict(round_dir, pass_="full")
    fold(round_dir)
    refused("one has, for v1")
    verdict(round_dir, "publishable", design=2)
    fold(round_dir)
    refused("none has")
    verdict(round_dir, design=3)
    fold(round_dir)
    refused("one has, for v3")


def run_of(round_dir) -> list[int]:
    return [one.n for one in versions.sent_back(folded(round_dir))]


def test_two_reports_that_send_back_one_version_count_as_one(round_dir):
    fold(round_dir)
    first = verdict(round_dir, pass_="full")
    second = verdict(round_dir)

    assert run_of(round_dir) == [first.event["n"]]
    found = versions.standing(folded(round_dir))
    # The second is recorded and is what the standing shows; it is not counted.
    assert (str(found), found.verdict.n) == ("fold-first@v1", second.event["n"])
    assert len(versions.design_verdicts(folded(round_dir))) == 2
    assert asked(round_dir).pass_ == brake.RECHECK

    fold(round_dir)
    assert (asked(round_dir).pass_, asked(round_dir).version) == (brake.RECHECK, 2)
    before = raw(round_dir)
    with pytest.raises(Refused) as refused:
        decide(round_dir)
    assert "one has, for v1" in refused.value.unmet[0].reason
    assert raw(round_dir) == before


def test_a_third_report_on_one_version_is_not_counted_either(round_dir):
    fold(round_dir)
    first = verdict(round_dir, pass_="full")
    verdict(round_dir)
    verdict(round_dir)

    assert run_of(round_dir) == [first.event["n"]]


def test_a_report_that_sends_back_an_unchanged_text_again_is_not_counted(round_dir):
    fold(round_dir)
    first = verdict(round_dir, pass_="full")
    again(round_dir)
    again(round_dir)

    verdict(round_dir, design=3)

    assert run_of(round_dir) == [first.event["n"]]
    assert standing(round_dir) == "fold-first@v3"
    assert asked(round_dir).instead != ()


def test_a_text_sent_back_through_a_later_version_is_counted_once(round_dir):
    fold(round_dir)
    again(round_dir)
    first = verdict(round_dir, design=2, pass_="full")
    again(round_dir)

    verdict(round_dir, design=3)

    assert run_of(round_dir) == [first.event["n"]]


def test_a_changed_text_sent_back_after_two_reports_on_the_first_is_the_second(round_dir):
    fold(round_dir)
    first = verdict(round_dir, pass_="full")
    verdict(round_dir)
    fold(round_dir)

    second = verdict(round_dir, design=2)

    assert run_of(round_dir) == [first.event["n"], second.event["n"]]
    fold(round_dir)
    assert str(decide(round_dir).standing) == "lead-decision@v3"


def test_the_text_of_an_older_version_frozen_again_is_counted_as_a_new_one(round_dir):
    fold(round_dir)
    text = (round_dir / "design.md").read_text(encoding="utf-8")
    first = verdict(round_dir, pass_="full")
    fold(round_dir)
    (round_dir / "design.md").write_text(text, encoding="utf-8")
    freezing.freeze(ROUND, by="scout")

    second = verdict(round_dir, design=3)

    assert run_of(round_dir) == [first.event["n"], second.event["n"]]


def test_a_report_on_a_version_the_lead_decided_then_one_on_the_next_starts_a_count(
        round_dir):
    sent_back_twice(round_dir)
    decide(round_dir)
    verdict(round_dir, design=3)
    fold(round_dir)

    counted = verdict(round_dir, design=4)
    verdict(round_dir, design=4)

    assert run_of(round_dir) == [counted.event["n"]]


def test_a_thread_is_not_decided_before_the_design_was_frozen_again(round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")
    fold(round_dir)
    verdict(round_dir, design=2)
    before = raw(round_dir)

    with pytest.raises(Refused) as refused:
        decide(round_dir)

    assert [(one.reason.partition(":")[0], one.fix) for one in refused.value.unmet] == [
        ("the design's text was not changed and frozen since the last verdict sent "
         "it back",
         f"moxi rounds fold {ROUND}")]
    assert raw(round_dir) == before

    fold(round_dir)
    assert str(decide(round_dir).standing) == "lead-decision@v3"


def test_a_decision_on_a_thread_needs_a_pointer_of_a_known_shape(round_dir):
    sent_back_twice(round_dir)
    before = raw(round_dir)

    with pytest.raises(Refused, match="says where the lead wrote why"):
        decide(round_dir, record=None)
    with pytest.raises(Refused) as refused:
        decide(round_dir, record="somewhere")
    assert refused.value.unmet[0].reason.startswith(
        "'somewhere' is not a pointer to where the lead wrote why; PTR below")
    assert "operator" not in refused.value.unmet[0].reason

    assert raw(round_dir) == before


@pytest.mark.parametrize("name", ["", "two words", "a/b", "a:b", "né"])
def test_a_threads_name_is_letters_digits_dots_underscores_and_dashes(round_dir, name):
    sent_back_twice(round_dir)
    before = raw(round_dir)

    with pytest.raises(BadArgument, match="is not a thread's name"):
        decide(round_dir, name)

    assert raw(round_dir) == before


def test_a_decision_is_close_or_accept_risk(round_dir):
    sent_back_twice(round_dir)

    with pytest.raises(BadArgument, match="is not what is decided of a thread"):
        decide(round_dir, decision="reopen")


def test_a_round_that_ended_takes_no_decision_on_a_thread(round_dir):
    sent_back_twice(round_dir)
    dropping.drop(ROUND, by="operator", record="comms:1234567", run_by="planner")
    before = raw(round_dir)

    with pytest.raises(Refused, match="was dropped and stays as it ended"):
        decide(round_dir)

    assert raw(round_dir) == before


def test_a_verdict_recorded_after_a_decision_is_the_standing(round_dir):
    sent_back_twice(round_dir)
    decide(round_dir)

    verdict(round_dir, "publishable", design=3)

    assert standing(round_dir) == "publishable@v3"


def test_a_version_frozen_after_a_decision_is_asked_for_a_recheck_and_no_second_thread(
        round_dir):
    sent_back_twice(round_dir)
    decide(round_dir)

    fold(round_dir, *PLAN, stage("B9"))

    assert standing(round_dir) == "none"
    found = asked(round_dir)
    assert (found.pass_, found.since, found.version, found.instead) == (
        brake.RECHECK, 2, 4, ())
    before = raw(round_dir)
    with pytest.raises(Refused) as refused:
        decide(round_dir, "another", brake.ACCEPT_RISK)
    assert [one.reason.partition(": ")[2] for one in refused.value.unmet] == [
        "none has since the lead's last decision"]
    assert raw(round_dir) == before


def test_a_second_thread_is_decided_after_two_more_verdicts_sent_the_design_back(
        round_dir):
    sent_back_twice(round_dir)
    decide(round_dir)
    fold(round_dir)
    verdict(round_dir, design=4)
    fold(round_dir)
    with pytest.raises(Refused, match="one has since the lead's last decision, for v4"):
        decide(round_dir, "another")
    verdict(round_dir, design=5)
    assert asked(round_dir).pass_ is None
    fold(round_dir)

    assert str(decide(round_dir, "another", brake.ACCEPT_RISK).standing) == (
        "lead-decision@v6")
    assert [(one.thread, one.decision, one.version)
            for one in folded(round_dir).threads] == [
        ("the-holds", "close", 3), ("another", "accept-risk", 6)]


def test_the_count_starts_again_after_a_change_of_records_only_made_the_decision(
        round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")
    fold(round_dir)
    verdict(round_dir, design=2)
    records_only(round_dir)
    assert standing(round_dir) == "lead-decision@v3"

    fold(round_dir, *PLAN, stage("B9"))

    assert asked(round_dir).pass_ == brake.RECHECK
    with pytest.raises(Refused, match="none has since the lead's last decision"):
        decide(round_dir)


def test_a_verdict_that_sends_back_a_publishable_version_leaves_its_standing(
        round_dir, capsys):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")

    late = verdict(round_dir)

    assert str(late.standing) == "publishable@v1"
    found = asked(round_dir)
    assert (found.pass_, found.instead) == (None, ())
    assert versions.standing(folded(round_dir)).verdict.verdict == "publishable"
    # It is in the record and shown all the same.
    assert [one.verdict for one in folded(round_dir).verdicts] == [
        "publishable", "fold-first"]
    assert main(["show", ROUND]) == 0
    assert "fold-first for v1" in capsys.readouterr().out
    # A change of records only still carries what the version stands at.
    assert str(records_only(round_dir).standing) == "publishable@v2"


def test_a_verdict_that_sends_back_a_version_the_lead_decided_leaves_its_standing(
        round_dir):
    sent_back_twice(round_dir)
    decided = decide(round_dir)

    assert str(verdict(round_dir, design=3).standing) == "lead-decision@v3"
    assert versions.standing(folded(round_dir)).decision == decided.decision
    assert asked(round_dir).pass_ is None


def test_a_verdict_that_sends_back_a_change_of_records_the_lead_decided_by_leaves_it(
        round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")
    records_only(round_dir)
    assert standing(round_dir) == "lead-decision@v2"

    assert str(verdict(round_dir, design=2).standing) == "lead-decision@v2"
    # The version before was not verified, and a verdict of it is still its standing.
    verdict(round_dir, design=1)
    assert standing(round_dir) == "lead-decision@v2"


def test_a_verdict_that_sends_back_a_version_carried_as_publishable_leaves_it(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")
    records_only(round_dir)

    assert str(verdict(round_dir, design=2).standing) == "publishable@v2"


def test_a_verdict_that_sends_back_a_verified_version_is_not_counted(round_dir):
    fold(round_dir)
    verdict(round_dir, "publishable", pass_="full")
    verdict(round_dir)
    verdict(round_dir)
    assert len(folded(round_dir).verdicts) == 3
    assert versions.sent_back(folded(round_dir)) == ()

    fold(round_dir, *PLAN, stage("B9"))

    assert asked(round_dir).pass_ == brake.RECHECK
    with pytest.raises(Refused, match="none has"):
        decide(round_dir)


def test_a_verdict_that_sends_back_a_version_the_lead_decided_is_not_counted(round_dir):
    sent_back_twice(round_dir)
    decide(round_dir)
    verdict(round_dir, design=3)
    verdict(round_dir, design=3)

    assert versions.sent_back(folded(round_dir)) == ()
    fold(round_dir)
    assert asked(round_dir).pass_ == brake.RECHECK


def test_a_verdict_that_sends_back_an_older_version_is_not_counted(round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")
    fold(round_dir)
    verdict(round_dir, design=1)
    assert [one.design for one in versions.sent_back(folded(round_dir))] == [1]
    assert asked(round_dir).pass_ == brake.RECHECK

    verdict(round_dir, design=2)

    assert [one.design for one in versions.sent_back(folded(round_dir))] == [1, 2]
    assert asked(round_dir).pass_ is None


def test_a_design_frozen_with_the_same_text_does_not_open_the_gate(round_dir):
    fold(round_dir)
    verdict(round_dir, pass_="full")
    fold(round_dir)
    verdict(round_dir, design=2)
    freezing.freeze(ROUND, by="scout")
    assert folded(round_dir).versions[-1].digest == folded(round_dir).versions[-2].digest
    before = raw(round_dir)

    with pytest.raises(Refused, match="text was not changed and frozen"):
        decide(round_dir)

    assert raw(round_dir) == before
    assert len(asked(round_dir).instead) == 3
    fold(round_dir)
    assert len(asked(round_dir).instead) == 1
    assert str(decide(round_dir).standing) == "lead-decision@v4"


def test_the_same_decision_run_again_is_found_and_not_recorded_twice(round_dir, capsys):
    sent_back_twice(round_dir)
    first = decide(round_dir)
    before = raw(round_dir)

    again = decide(round_dir)

    assert raw(round_dir) == before
    assert (again.found, again.event, again.decision) == (True, None, first.decision)
    assert str(again.standing) == "lead-decision@v3"
    assert len(folded(round_dir).threads) == 1
    assert main(["mark", ROUND, "thread:the-holds", "--close", "--record", WHY]) == 0
    assert "recorded already" in capsys.readouterr().out
    assert raw(round_dir) == before
    # Another word, another thread or another version is not the same decision.
    with pytest.raises(Refused):
        decide(round_dir, decision=brake.ACCEPT_RISK)
    with pytest.raises(Refused):
        decide(round_dir, "another")
    fold(round_dir)
    with pytest.raises(Refused):
        decide(round_dir)
    assert len(folded(round_dir).threads) == 1


def test_mark_decides_a_thread(round_dir, capsys):
    sent_back_twice(round_dir)

    assert main(["mark", ROUND, "thread:the-holds", "--accept-risk", "--record",
                 WHY, "--as", "planner"]) == 0

    assert capsys.readouterr().out == (
        f"{ROUND} thread:the-holds: accept-risk   record: {WHY}\n"
        "standing: lead-decision@v3\n")
    before = raw(round_dir)
    assert main(["mark", ROUND, "the-holds", "--close", "--record", WHY]) == 2
    assert "names no thread" in capsys.readouterr().err
    assert main(["mark", ROUND, "thread:the-holds", "--close"]) == 3
    assert main(["mark", ROUND, "thread:the holds", "--close", "--record", WHY]) == 2
    assert main(["mark", ROUND, "thread:the-holds", "--close", "--accept-risk",
                 "--record", WHY]) == 2
    assert main(["mark", ROUND, "--close", "--record", WHY]) == 2
    assert raw(round_dir) == before


# -- a second full pass ------------------------------------------------------------------

def test_a_second_full_pass_is_recorded_with_a_flag(round_dir, capsys):
    fold(round_dir)
    first = verdict(round_dir, pass_="full")
    recheck = verdict(round_dir, pass_="re-check")
    second = verdict(round_dir, "publishable", pass_="full")
    third = verdict(round_dir, pass_="full")

    assert [one.verdict.flags for one in (first, recheck, second, third)] == [
        (), (), ("extra-full-pass",), ("extra-full-pass",)]
    assert "flags" not in first.event and "flags" not in recheck.event
    assert second.event["flags"] == [brake.EXTRA_FULL_PASS]
    assert str(second.standing) == "publishable@v1"
    assert main(["show", ROUND]) == 0
    assert capsys.readouterr().out.count("model a-model   flags: extra-full-pass\n") == 2


def test_a_full_pass_after_a_re_check_alone_is_no_extra(round_dir, capsys):
    fold(round_dir)
    verdict(round_dir, pass_="re-check")
    path = round_dir / "full.md"
    path.write_text('```toml rounds\nkind = "design"\nverdict = "publishable"\n'
                    'pass = "full"\nby = "critic"\nmodel = "a-model"\ndesign = 1\n```\n',
                    encoding="utf-8")

    assert main(["verdict", ROUND, str(path)]) == 0

    assert "flags:" not in capsys.readouterr().out
    assert folded(round_dir).verdicts[-1].flags == ()


def test_verdict_prints_the_flag(round_dir, capsys):
    fold(round_dir)
    verdict(round_dir, pass_="full")
    path = round_dir / "again.md"
    path.write_text('```toml rounds\nkind = "design"\nverdict = "fold-first"\n'
                    'pass = "full"\nby = "critic"\nmodel = "a-model"\ndesign = 1\n```\n',
                    encoding="utf-8")

    assert main(["verdict", ROUND, str(path)]) == 0

    assert "flags: extra-full-pass\n" in capsys.readouterr().out


# -- shown -------------------------------------------------------------------------------

def test_the_standing_the_decisions_and_the_records_only_versions_are_shown(
        round_dir, capsys):
    sent_back_twice(round_dir)
    done = decide(round_dir)
    records_only(round_dir, record="comms:1234567")
    at, frozen_at = done.decision.at, versions.current(folded(round_dir)).at

    assert main(["show", ROUND]) == 0
    shown = capsys.readouterr().out
    assert main(["show", ROUND, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    note = (round_dir / "round-note.md").read_text(encoding="utf-8")

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
        round_dir, capsys):
    fold(round_dir)

    assert main(["show", ROUND]) == 0

    shown = capsys.readouterr().out
    assert "thread " not in shown and "records only" not in shown
    assert "standing: none\n" in shown


def test_the_decisions_and_the_carried_standing_are_in_the_index_and_the_list(
        round_dir, capsys):
    sent_back_twice(round_dir)
    done = decide(round_dir, decision=brake.ACCEPT_RISK)
    records_only(round_dir)

    found = index.read("proj-a", round_dir.parent, "a-round")

    assert found.of(index.THREAD) == (index.Item(index.THREAD, "t1", {
        "thread": "the-holds", "decision": "accept-risk", "version": 3, "record": WHY,
        "by": "planner", "at": done.decision.at}),)
    assert [(item.body["standing"], item.body["records_only"])
            for item in found.of(index.VERSION)] == [("lead-decision@v4", True)]
    assert main(["list"]) == 0
    assert (f"{ROUND}   open   flow full   step docket, ready   "
            "design: lead-decision@v4   ") in capsys.readouterr().out
