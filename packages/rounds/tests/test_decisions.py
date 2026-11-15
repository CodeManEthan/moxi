"""Decisions: a report's `[[decision]]` is a record of the round in a question's basic
form, it stays and is never lightened, the operator rules it with `B<n>.M<k>=accept|
reject`, a rejection owes a fix, and a majors report declares what no build report
carries."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import (api, closing, decisions, export, log, opening, rendering,
                         ruling, showing, state, verdicts, waits)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Refused

ROUND = "proj-a/a-round"
RETRIED = "a search is retried twice"
SAID = "comms:1234567"


@pytest.fixture
def built(git_repo, tmp_path, capsys):
    """`proj-a` rooted at the test's git repo, its round in the `fix` flow, and B1 on the
    branch `work` commissioned to `coder` with one commit made on it. Returns `(round
    folder, the worktree's repo)`."""
    from moxi import cli
    from moxi_rounds import commissioning

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    log.append(log.round_log(round_dir), "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    tree = type(git_repo)(made.worktree)
    tree.commit("work.txt", "the work")
    capsys.readouterr()
    return round_dir, tree


def decision(id_="M1", weight="major", *, forecloses=False, text=RETRIED, stage=None):
    staged = "" if stage is None else f'stage = "{stage}"\n'
    return (f'[[decision]]\nid = "{id_}"\n{staged}weight = "{weight}"\n'
            f'forecloses = {str(forecloses).lower()}\ntext = "{text}"')


def report(round_dir, head, *listed, name="report.md"):
    keys = ['kind = "build"', 'verdict = "publishable"', 'pass = "full"',
            'by = "critic"', 'model = "a-model"', 'stage = "B1"', f'head = "{head}"']
    path = round_dir / name
    path.write_text(f"# {name}\n\n```toml rounds\n" + "\n".join(keys)
                    + "".join(f"\n\n{one}" for one in listed) + "\n```\n",
                    encoding="utf-8")
    return path


def verify(round_dir, head, *listed, name="report.md"):
    return verdicts.record(ROUND, report(round_dir, head, *listed, name=name),
                           by="critic")


def majors(round_dir, *listed, stages=("B1",), by="reviewer", name="majors.md"):
    keys = ['kind = "majors"', f'by = "{by}"', 'model = "a-model"',
            f"stages = {json.dumps(list(stages))}"]
    path = round_dir / name
    path.write_text(f"# {name}\n\n```toml rounds\n" + "\n".join(keys)
                    + "".join(f"\n\n{one}" for one in listed) + "\n```\n",
                    encoding="utf-8")
    return path


def folded(round_dir):
    return state.fold(log.read(log.round_log(round_dir)))


def raw(round_dir) -> bytes:
    return log.round_log(round_dir).read_bytes()


def rule(item, **more):
    return ruling.rule(ROUND, item=item, record=SAID, by="planner", **more)


def refused(call, *args, **more) -> str:
    with pytest.raises(Refused) as caught:
        call(*args, **more)
    return str(caught.value)


# -- declared -----------------------------------------------------------------------------

def test_a_build_report_s_decision_is_a_record_in_the_basic_form(built):
    round_dir, tree = built

    recorded = verify(round_dir, tree.rev(), decision(forecloses=True))

    [event] = [one for one in log.read(log.round_log(round_dir))
               if one["type"] == "verdict.recorded"]
    assert event["decisions"] == [{"id": "M1", "weight": "major", "forecloses": True,
                                   "text": RETRIED}]
    [held] = decisions.of_round(folded(round_dir))
    assert held.data() == {
        "id": "B1.M1", "text": RETRIED, "from": "report.md", "blocks": "none",
        "state": "open", "by": "critic", "at": event["at"],
        "options": [{"key": "accept", "text": "accept it as built"},
                    {"key": "reject", "text": "reject it: a fix is owed"}],
        "stage": "B1", "weight": "major", "forecloses": True, "ruling": None,
        "chose": None, "record": None}
    assert [one.key for one in recorded.decisions] == ["B1.M1"]


def test_verdict_prints_each_decision_with_the_ruling_that_settles_it(built, capsys):
    round_dir, tree = built

    assert main(["verdict", ROUND, str(report(round_dir, tree.rev(), decision()))]) == 0

    out = capsys.readouterr().out
    assert f"decision B1.M1: major, open: {RETRIED}\n" in out
    assert f"his to rule: moxi rounds rule {ROUND} B1.M1=accept|reject --record PTR" in out


def test_a_decision_listed_again_with_another_sentence_is_refused(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), decision(weight="minor"))
    before = raw(round_dir)

    said = refused(verify, round_dir, tree.rev(),
                   decision(weight="major", text="a wholly different choice"),
                   name="second.md")

    assert (f"the decision B1.M1 is on record as '{RETRIED}', from report.md, and the "
            "report lists M1 as 'a wholly different choice'") in said
    assert raw(round_dir) == before
    held = decisions.find(folded(round_dir), "B1.M1")
    assert (held.text, held.weight) == (RETRIED, "minor")


def test_a_later_report_leaves_a_decision_on_record_and_may_only_make_it_heavier(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), decision(weight="minor"))
    verify(round_dir, tree.rev(), name="second.md")
    assert decisions.find(folded(round_dir), "B1.M1").weight == "minor"

    verify(round_dir, tree.rev(), decision(weight="major", forecloses=True), name="third.md")
    heavier = decisions.find(folded(round_dir), "B1.M1")
    assert (heavier.weight, heavier.forecloses, heavier.text) == ("major", True, RETRIED)

    before = raw(round_dir)
    said = refused(verify, round_dir, tree.rev(), decision(weight="minor"), name="fourth.md")
    assert "the decision B1.M1 is on record as major, foreclosing" in said
    assert "its weight minor is lighter than major" in said
    assert "it no longer forecloses" in said
    assert raw(round_dir) == before


def test_a_decision_holds_no_stage_and_no_question_s_place(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), decision())
    now = folded(round_dir)

    close = [line for condition in closing.CONDITIONS
             for line in condition(now, lambda _name: False, "majors")]
    assert not any("M1" in line for line in close)
    assert now.questions == []


# -- ruled --------------------------------------------------------------------------------

def test_his_ruling_of_a_decision_is_one_event_of_substance_on_no_stage(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), decision())

    assert main(["rule", ROUND, "B1.M1=accept", "--record", SAID]) == 0

    out = capsys.readouterr().out
    assert f"in answer to B1.M1: {RETRIED}" in out
    event = log.read(log.round_log(round_dir))[-1]
    assert {key: event[key] for key in ("type", "rules", "chose", "kind", "stages")} == {
        "type": "ruling.recorded", "rules": "B1.M1=accept", "chose": "accept",
        "kind": "substance", "stages": "none"}
    held = decisions.find(folded(round_dir), "B1.M1")
    assert (held.state, held.chose, held.ruling, held.record) == (
        "ruled", "accept", event["id"], SAID)
    assert waits.open_of(folded(round_dir)) == ()


def test_a_second_ruling_names_the_first_and_one_taken_back_leaves_it_open(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), decision())
    first = rule("B1.M1=accept")

    said = refused(rule, "B1.M1=reject")
    assert f"B1.M1 is already ruled, accept, by {first.id}" in said
    assert f"--supersedes {first.id}" in said
    second = rule("B1.M1=reject", supersedes=first.id)
    assert decisions.find(folded(round_dir), "B1.M1").ruling == second.id

    rule(f"revoke:{second.id}")
    assert decisions.find(folded(round_dir), "B1.M1").state == "open"


def test_a_decision_not_on_record_or_a_word_that_is_neither_option_is_refused(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), decision())

    assert f"{ROUND} has no decision B1.M2" in refused(rule, "B1.M2=accept")
    with pytest.raises(BadArgument):
        rule("B1.M1=maybe")
    with pytest.raises(BadArgument):
        rule("B1.M1=accept", stages="B1")


def test_a_rejected_decision_owes_a_fix_until_it_is_marked_done(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), decision())
    rejected = rule("B1.M1=reject")

    [owed] = waits.open_of(folded(round_dir))
    assert (owed.key, owed.text) == ("B1.M1", "B1.M1 rejected: fix owed")
    assert main(["list", "--waiting", "--json"]) == 0
    assert [row["id"] for row in json.loads(capsys.readouterr().out)["waits"]] == [
        "B1.M1"]

    assert main(["mark", ROUND, "B1.M1", "--done", "--record",
                 "https://example.test/fix"]) == 0
    assert waits.open_of(folded(round_dir)) == ()

    # Rejected again by a ruling after the mark, the fix is owed again.
    accepted = rule("B1.M1=accept", supersedes=rejected.id)
    assert waits.open_of(folded(round_dir)) == ()
    rule("B1.M1=reject", supersedes=accepted.id)
    assert [one.key for one in waits.open_of(folded(round_dir))] == ["B1.M1"]


# -- listed -------------------------------------------------------------------------------

def test_list_waiting_and_the_project_export_hold_each_unruled_major(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), decision(), decision("M2", weight="minor"),
           decision("M3", forecloses=True, text="the old flag is gone"))
    rule("B1.M3=accept")

    assert main(["list", "--waiting"]) == 0
    out = capsys.readouterr().out
    assert main(["list", "--waiting", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["decisions"]
    project = export.questions_of("proj-a")
    docket = export.questions_of(ROUND)

    assert f"major decisions not yet ruled:\n  {ROUND} B1.M1: {RETRIED}\n" in out
    assert [row["id"] for row in rows] == ["B1.M1"]
    assert {key: rows[0][key] for key in ("holder", "blocks", "state", "stage",
                                          "weight", "forecloses")} == {
        "holder": ROUND, "blocks": "none", "state": "open", "stage": "B1",
        "weight": "major", "forecloses": False}
    assert [entry.data() for entry in project.decisions] == rows
    assert "## Major decisions not yet ruled" in export.as_markdown(project)
    assert docket.entries == ()
    assert api.waiting()["decisions"] == rows


def test_a_foreclosing_decision_is_an_entry_of_its_own(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), decision(forecloses=True))

    assert main(["list", "--waiting", "--json"]) == 0

    [row] = json.loads(capsys.readouterr().out)["decisions"]
    assert row["alone"] is True


def test_show_the_note_and_the_api_hold_every_decision(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), decision(), decision("M2", weight="minor"))
    rule("B1.M2=accept")

    assert main(["show", ROUND]) == 0
    out = capsys.readouterr().out
    shown_as = showing.data(showing.show(ROUND))
    note = (round_dir / rendering.NOTE_FILE).read_text(encoding="utf-8")

    assert f"decisions: 2\n  decision B1.M1: major, open: {RETRIED}\n" in out
    assert [(one["id"], one["state"]) for one in shown_as["decisions"]] == [
        ("B1.M1", "open"), ("B1.M2", "ruled")]
    assert api.round_data(ROUND)["decisions"] == shown_as["decisions"]
    assert "## Decisions" in note
    assert f"- decision B1.M2: minor, ruled: {RETRIED}" in note


# -- a majors report ----------------------------------------------------------------------

def test_a_majors_report_declares_decisions_of_the_builds_it_names(built, capsys):
    round_dir, _tree = built

    assert main(["verdict", ROUND, str(majors(round_dir, decision(stage="B1")))]) == 0

    assert "recorded for proj-a/a-round: the major decisions of B1" in (
        capsys.readouterr().out)
    event = log.read(log.round_log(round_dir))[-1]
    assert (event["kind"], event["stages"]) == ("majors", ["B1"])
    assert event["decisions"] == [{"id": "M1", "weight": "major", "forecloses": False,
                                   "text": RETRIED, "stage": "B1"}]
    held = decisions.find(folded(round_dir), "B1.M1")
    assert (held.origin, held.by) == ("majors.md", "reviewer")


def test_a_majors_report_by_a_builder_or_of_a_stage_never_built_is_refused(built):
    round_dir, _tree = built
    before = raw(round_dir)

    by_builder = refused(verdicts.record, ROUND, majors(round_dir, by="coder"),
                         by="coder")
    never = refused(verdicts.record, ROUND, majors(round_dir, stages=("B1", "B2")),
                    by="reviewer")

    assert "the report is by coder, who built B1" in by_builder
    assert "B2 of proj-a/a-round was never commissioned" in never
    assert raw(round_dir) == before


def test_a_new_decision_takes_an_id_no_earlier_report_of_its_stage_used(built):
    round_dir, tree = built
    verdicts.record(ROUND, majors(round_dir, decision(stage="B1")), by="reviewer")

    # Listed again by a build report, it is the same decision, made heavier.
    verify(round_dir, tree.rev(), decision(forecloses=True))

    held = decisions.find(folded(round_dir), "B1.M1")
    assert (held.origin, held.forecloses) == ("majors.md", True)
