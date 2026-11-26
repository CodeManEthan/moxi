"""The lead's settlement of a minor decision, the raise to major, and the one rule that
says whose an open decision is: the close, the ratify row, the labels, the export and
the project's list each read it."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import (closing, decisions, events, export, folders, index, log,
                         numbers, ruling, settings, settling, state, steps, waits)
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
PTR = "comms:1234567"
WHY = "comms:7654321"
BASE = "0" * 40
TIP = "1" * 40

FLOWS = """\
    [flows.paper]
    steps = [ { step = "design", by = "lead" } ]
    """


@pytest.fixture
def opened(tmp_path, add_project, write_config, capsys):
    """`opened(flow="fix", ratify="majors")`: open `a-round` of `proj-a` on that flow
    under that `ratify` setting and return its folder. `fix` builds and so has a
    `ratify` step; `paper` has none."""
    add_project("proj-a", notes_root=tmp_path / "notes")

    def open_on(flow: str = "fix", ratify: str = "majors"):
        from moxi_rounds import opening

        write_config(FLOWS + f'\n[defaults]\nratify = "{ratify}"\n')
        opening.open_round(ROUND, description="a placeholder round", by="planner",
                           flow=flow)
        capsys.readouterr()
        return tmp_path / "notes" / "rounds" / "a-round"

    return open_on


def built(round_dir, *, finished=True, decisions_=(("M1", "minor"),), forecloses=False,
          build="0123456789abcdef"):
    """B1 declared, commissioned, reported with `decisions_` (`(id, weight)`) and, when
    `finished`, finished, through the log."""
    path = log.round_log(round_dir)
    if not any(one["type"] == "stage.declared" for one in log.read(path)):
        log.append(path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
                   by="planner")
    log.append(path, "build.commissioned",
               {"stage": "B1", "repo": "proj-a", "branch": "work", "base": BASE,
                "target": "main", "builder": "coder", "build": build}, by="planner")
    report(round_dir, decisions_, forecloses=forecloses)
    if finished:
        finish(round_dir)


def report(round_dir, decisions_, *, forecloses=False):
    log.append(log.round_log(round_dir), "verdict.recorded",
               {"kind": "build", "verdict": "publishable", "pass": "full",
                "stage": "B1", "head": TIP, "by": "critic",
                "decisions": [{"id": id_, "text": f"a choice {id_}", "weight": weight,
                               "forecloses": forecloses}
                              for id_, weight in decisions_]},
               by="critic")


def finish(round_dir):
    log.append(log.round_log(round_dir), "finish.done", {"stage": "B1", "tip": TIP},
               by="coder")


def history(round_dir) -> list[dict]:
    return log.read(log.round_log(round_dir))


def folded(round_dir):
    return state.fold(history(round_dir))


def waits_of(round_dir, ratify="majors") -> tuple[str, ...]:
    return closing.waits(folded(round_dir), ratify=ratify)


def held(round_dir, key="B1.M1") -> decisions.Decision:
    return decisions.find(folded(round_dir), key)


def settle(chose="accept", key="B1.M1", record=PTR, by="lead-agent"):
    return settling.settle(ROUND, key, chose, record=record, by=by)


def reasons(caught) -> list[str]:
    return [one.reason for one in caught.value.unmet]


def run(capsys, *argv) -> str:
    assert main(list(argv)) == 0
    return capsys.readouterr().out


def label(capsys, key="B1.M1") -> str:
    """The line show prints under the decision, checked against show's data."""
    shown = run(capsys, "show", ROUND).splitlines()
    [data] = [one for one in json.loads(run(capsys, "show", ROUND, "--json"))["decisions"]
              if one["id"] == key]
    [at] = [at for at, line in enumerate(shown) if line.startswith(f"  decision {key}:")]
    said = shown[at + 2].strip()
    if data["state"] == "open":
        assert data["open_as"] == said
    return said


RULE = f"moxi rounds rule {ROUND} B1.M1=accept|reject --record PTR"
SETTLE = f"moxi rounds mark {ROUND} B1.M1 --accept, --reject or --major --record PTR"
OWED = "waiting item B1.M1, open"


# -- the settlement ----------------------------------------------------------------------

@pytest.mark.parametrize("chose", ["accept", "reject"])
def test_a_settlement_is_one_event_by_the_lead_and_reads_settled(opened, chose):
    round_dir = opened()
    built(round_dir)

    done = settle(chose)

    [event] = [one for one in history(round_dir) if one["type"] == "decision.settled"]
    assert {key: event[key] for key in ("id", "chose", "record", "by", "actor")} == {
        "id": "B1.M1", "chose": chose, "record": PTR, "by": "lead", "actor": "lead-agent"}
    assert done.event == event
    now = held(round_dir)
    assert (now.state, now.chose, now.record, now.ruling) == ("settled", chose, PTR, None)
    assert now.ruled == event["n"]
    # The operator's rulings count none of it.
    counted = numbers.of(folded(round_dir))
    assert counted["decisions"] == {"accept": 0, "reject": 0}
    assert counted["settled"] == {"accept": int(chose == "accept"),
                                  "reject": int(chose == "reject")}


def test_an_accept_closes_the_round_it_held(opened):
    round_dir = opened()
    built(round_dir)
    assert waits_of(round_dir) == ("decision B1.M1, minor, not yet settled by the lead",)

    settle()

    assert folded(round_dir).status == "closed"


def test_a_reject_holds_the_close_until_its_fix_is_marked_done(opened):
    round_dir = opened()
    built(round_dir)

    settle("reject")

    assert waits_of(round_dir) == (OWED,)
    [owed] = [one for one in waits.of_round(folded(round_dir)) if one.key == "B1.M1"]
    assert owed.text == "B1.M1 rejected: fix owed" and owed.by == "lead-agent"
    waits.done(ROUND, "B1.M1", record=WHY, by="lead-agent")
    assert folded(round_dir).status == "closed"


def test_the_settlement_is_refused_for_what_is_not_the_lead_s_to_settle(opened):
    round_dir = opened()
    built(round_dir, decisions_=(("M1", "major"), ("M2", "minor"), ("M3", "minor")))
    path = log.round_log(round_dir)
    before = len(history(round_dir))

    for key, record, said in (
            ("B1.M9", PTR, f"{ROUND} has no decision B1.M9"),
            ("B1.M1", PTR, "B1.M1 is major, and the lead settles only a minor decision"),
            ("B1.M2", None, "--accept names where the lead wrote why, with --record"),
            ("B1.M2", "a note", "'a note' is not a pointer to where the lead wrote why")):
        with pytest.raises(Refused) as caught:
            settle(key=key, record=record)
        assert any(one.startswith(said) for one in reasons(caught)), reasons(caught)
    assert len(history(round_dir)) == before

    settle(key="B1.M2")
    with pytest.raises(Refused) as caught:
        settle("reject", key="B1.M2")
    assert reasons(caught) == [f"B1.M2 is settled already, accept, record {PTR}"]

    ruling.rule(ROUND, item="B1.M3=accept", record=PTR, by="planner")
    with pytest.raises(Refused) as caught:
        settle(key="B1.M3")
    assert reasons(caught)[0].startswith("B1.M3 is ruled by the operator, accept")
    assert log.read(path)[-1]["type"] == "ruling.recorded"


def test_the_settlement_is_refused_for_a_minor_decision_that_is_the_operator_s(opened):
    round_dir = opened(ratify="majors+minors")
    built(round_dir)

    with pytest.raises(Refused) as caught:
        settle()

    assert reasons(caught) == ["B1.M1 is the operator's to rule, and the lead does not "
                               "settle it"]
    assert caught.value.unmet[0].fix == RULE


def test_the_settlement_is_refused_for_a_minor_that_is_the_operator_s_once_its_stage_finishes(
        opened):
    # Settled ahead, it would never reach the operator.
    round_dir = opened(ratify="majors+minors")
    built(round_dir, finished=False)

    with pytest.raises(Refused) as caught:
        settle()

    assert reasons(caught) == ["B1.M1 is put to the operator once B1 finishes (stage B1 "
                               "is not finished), and the lead does not settle it"]


def test_under_majors_the_lead_may_settle_a_minor_of_a_stage_not_yet_finished(opened):
    round_dir = opened()
    built(round_dir, finished=False)

    settle()

    assert held(round_dir).state == "settled"
    finish(round_dir)
    assert "decision B1.M1, minor, not yet settled by the lead" not in waits_of(round_dir)


def test_the_settlement_and_the_raise_are_refused_in_a_round_that_has_ended(opened):
    round_dir = opened()
    built(round_dir)
    log.append(log.round_log(round_dir), "round.dropped",
               {"by": "lead", "record": PTR}, by="planner")

    with pytest.raises(Refused) as caught:
        settle()
    assert "dropped" in reasons(caught)[0]
    with pytest.raises(Refused) as caught:
        settling.make_major(ROUND, "B1.M1", record=PTR, by="lead-agent")
    assert "dropped" in reasons(caught)[0]


# -- the close, in every flow ------------------------------------------------------------

@pytest.mark.parametrize("flow, ratify, skip", [
    ("paper", "majors+minors", False), ("fix", "off", False),
    ("fix", "majors+minors", True)])
def test_an_open_minor_the_operator_is_not_put_holds_the_close_until_settled(
        opened, flow, ratify, skip):
    round_dir = opened(flow, ratify=ratify)
    built(round_dir)
    if skip:
        log.append(log.round_log(round_dir), "ruling.recorded",
                   {"id": "C1", "record": PTR, "kind": "process", "rules": "skip:ratify"},
                   by="planner")

    assert "decision B1.M1, minor, not yet settled by the lead" in waits_of(
        round_dir, ratify)
    why = {"paper": "the flow has no ratify step", "off": "ratify is off"}.get(
        flow if flow == "paper" else ratify, "ratify is skipped")
    assert decisions.why_not(folded(round_dir), ratify) == {"B1.M1": why}

    settle()

    assert "decision B1.M1, minor, not yet settled by the lead" not in waits_of(
        round_dir, ratify)


def test_under_majors_and_minors_the_minor_holds_the_close_and_the_row_until_ruled(
        opened, capsys):
    round_dir = opened(ratify="majors+minors")
    built(round_dir)

    assert waits_of(round_dir, "majors+minors") == (
        "decision B1.M1, minor and not yet ruled",)
    row = steps.one(folded(round_dir), "ratify", name=ROUND,
                    seen={None: steps.Seen(ratify="majors+minors")})
    assert row.state == steps.READY
    assert label(capsys) == f"the operator's to rule: {RULE}"

    ruling.rule(ROUND, item="B1.M1=accept", record=PTR, by="planner")

    assert folded(round_dir).status == "closed"


def test_a_minor_only_a_dropped_build_declared_holds_nothing(opened):
    round_dir = opened()
    built(round_dir, finished=False)
    log.append(log.round_log(round_dir), "build.dropped",
               {"stage": "B1", "by": "lead", "record": PTR}, by="planner")
    built(round_dir, decisions_=(), build="fedcba9876543210")

    assert waits_of(round_dir) == ()
    assert decisions.to_settle(folded(round_dir), "majors") == ()


# -- the raise, and the lapse ------------------------------------------------------------

def test_a_raise_makes_the_decision_major_and_the_operator_s_under_majors(opened, capsys):
    round_dir = opened()
    built(round_dir)

    done = settling.make_major(ROUND, "B1.M1", record=WHY, by="lead-agent")

    assert {key: done.event[key] for key in ("type", "id", "record", "by", "actor")} == {
        "type": "decision.raised", "id": "B1.M1", "record": WHY, "by": "lead",
        "actor": "lead-agent"}
    assert held(round_dir).weight == "major"
    assert waits_of(round_dir) == ("decision B1.M1, major and not yet ruled",)
    assert label(capsys) == f"the operator's to rule: {RULE}"
    with pytest.raises(Refused) as caught:
        settling.make_major(ROUND, "B1.M1", record=WHY, by="lead-agent")
    assert reasons(caught) == ["B1.M1 is major already"]


def test_a_raise_is_refused_for_a_decision_the_operator_ruled(opened):
    round_dir = opened(ratify="majors+minors")
    built(round_dir, decisions_=(("M1", "minor"), ("M2", "minor")))
    ruling.rule(ROUND, item="B1.M1=reject", record=PTR, by="planner")

    with pytest.raises(Refused) as caught:
        settling.make_major(ROUND, "B1.M1", record=WHY, by="lead-agent")

    assert reasons(caught)[0].startswith("B1.M1 is ruled by the operator, reject")


@pytest.mark.parametrize("how", ["raise", "report"])
def test_a_settled_reject_lapses_when_the_weight_rises_and_owes_no_fix(opened, how):
    round_dir = opened()
    built(round_dir)
    settle("reject")
    assert OWED in waits_of(round_dir)

    if how == "raise":
        settling.make_major(ROUND, "B1.M1", record=WHY, by="lead-agent")
    else:
        report(round_dir, (("M1", "major"),))

    now = held(round_dir)
    assert (now.state, now.weight, now.chose) == ("open", "major", None)
    assert waits_of(round_dir) == ("decision B1.M1, major and not yet ruled",)


def test_the_operator_s_ruling_replaces_a_settlement_and_names_it(opened):
    round_dir = opened()
    built(round_dir, decisions_=(("M1", "minor"), ("M2", "major")))
    settle("reject")
    [settled] = [one for one in history(round_dir) if one["type"] == "decision.settled"]

    ruling.rule(ROUND, item="B1.M1=accept", record=WHY, by="planner")

    [ruled] = [one for one in history(round_dir) if one["type"] == "ruling.recorded"]
    assert ruled["settlement"] == settled["n"]
    now = held(round_dir)
    assert (now.state, now.chose, now.ruling) == ("ruled", "accept", ruled["id"])
    # The reject's fix is no longer owed.
    assert waits_of(round_dir) == ("decision B1.M2, major and not yet ruled",)
    counted = numbers.of(folded(round_dir))
    assert (counted["decisions"], counted["settled"]) == (
        {"accept": 1, "reject": 0}, {"accept": 0, "reject": 0})


# -- the labels --------------------------------------------------------------------------

@pytest.mark.parametrize("flow, ratify, weight, said", [
    ("fix", "majors", "minor", f"the lead's to settle: {SETTLE}"),
    ("fix", "majors+minors", "minor", f"the operator's to rule: {RULE}"),
    ("fix", "majors", "major", f"the operator's to rule: {RULE}"),
    ("fix", "off", "major",
     f"not put to the operator (ratify is off); recorded only if the operator rules "
     f"it: {RULE}"),
    ("paper", "majors+minors", "minor", f"the lead's to settle: {SETTLE}"),
    ("paper", "majors", "major",
     "not put to the operator (the flow has no ratify step); recorded only if the "
     f"operator rules it: {RULE}")])
def test_the_label_under_an_open_decision_follows_the_one_rule(
        opened, capsys, flow, ratify, weight, said):
    round_dir = opened(flow, ratify=ratify)
    built(round_dir, decisions_=(("M1", weight),))
    assert label(capsys) == said


def test_a_settled_decision_is_labelled_as_the_lead_s_settlement(opened, capsys):
    round_dir = opened()
    built(round_dir, decisions_=(("M1", "minor"), ("M2", "major")))
    settle()

    assert label(capsys) == f"settled by the lead: accept, record {PTR}"
    data = json.loads(run(capsys, "show", ROUND, "--json"))["decisions"][0]
    assert (data["state"], data["chose"], data["record"]) == ("settled", "accept", PTR)


def test_mark_settles_and_raises_from_the_command_line(opened, capsys):
    round_dir = opened()
    built(round_dir, decisions_=(("M1", "minor"), ("M2", "minor"), ("M3", "major")))

    said = run(capsys, "mark", ROUND, "B1.M1", "--reject", "--record", PTR)
    assert said.splitlines()[:2] == [
        f"{ROUND} decision B1.M1: minor, settled   settled by the lead: reject, record "
        f"{PTR}",
        f"a fix is owed: moxi rounds mark {ROUND} B1.M1 --done --record PTR"]
    said = run(capsys, "mark", ROUND, "B1.M2", "--major", "--record", PTR)
    assert said.splitlines()[0] == f"{ROUND} decision B1.M2: major, open"
    # The exact flag is taken over the longer one it begins.
    assert main(["mark", ROUND, "B1.M3", "--accept", "--record", PTR]) != 0
    assert "B1.M3 is major" in capsys.readouterr().err


# -- the export, the index and the project's list ----------------------------------------

@pytest.mark.parametrize("finished", [True, False])
def test_a_major_under_a_skipped_ratify_leaves_the_export_and_the_later_group(
        opened, finished):
    round_dir = opened()
    built(round_dir, finished=finished, decisions_=(("M1", "major"),))
    drawn = export.questions_of(ROUND, view="ratify")
    assert [one.id for one in (drawn.entries if finished else drawn.later)] == ["B1.M1"]

    log.append(log.round_log(round_dir), "ruling.recorded",
               {"id": "C1", "record": PTR, "kind": "process", "rules": "skip:ratify"},
               by="planner")
    drawn = export.questions_of(ROUND, view="ratify")

    assert (drawn.entries, drawn.later, drawn.not_ratified) == (
        (), (), "ratify is skipped")
    assert "It puts to the operator nothing: ratify is skipped." in export.as_markdown(
        drawn)


def test_an_export_with_no_decision_does_not_say_the_flow_ratifies(opened):
    round_dir = opened()
    log.append(log.round_log(round_dir), "ruling.recorded",
               {"id": "C1", "record": PTR, "kind": "process", "rules": "skip:ratify"},
               by="planner")
    drawn = export.questions_of(ROUND, view="ratify")
    text = export.as_markdown(drawn)

    assert (drawn.entries, drawn.later) == ((), ())
    assert "It puts to the operator each major decision" not in text
    assert ("No decision waits. Where the round's flow has a ratify step that is not "
            "skipped, it puts to the operator each major decision not yet ruled.") in text


def test_the_project_list_says_a_major_under_a_skipped_ratify_is_not_put(
        opened, capsys):
    round_dir = opened()
    built(round_dir, finished=False, decisions_=(("M1", "major"), ("M2", "minor")))
    log.append(log.round_log(round_dir), "ruling.recorded",
               {"id": "C1", "record": PTR, "kind": "process", "rules": "skip:ratify"},
               by="planner")

    [row] = json.loads(run(capsys, "list", "--waiting", "--json"))["decisions"]
    listed = run(capsys, "list", "--waiting")

    assert (row["id"], row["not_put"]) == ("B1.M1", "ratify is skipped")
    assert ("    not put to the operator (ratify is skipped); recorded only if the "
            f"operator rules it: {RULE}\n") in listed


def test_under_majors_and_minors_the_export_and_the_list_hold_the_operator_s_minors(
        opened, capsys):
    round_dir = opened(ratify="majors+minors")
    built(round_dir, decisions_=(("M1", "minor"), ("M2", "minor")))
    assert [one.key for one in decisions.to_rule(folded(round_dir), "majors+minors")] == [
        "B1.M1", "B1.M2"]

    drawn = export.questions_of(ROUND, view="ratify")
    rows = json.loads(run(capsys, "list", "--waiting", "--json"))["decisions"]

    assert [(one.id, one.group) for one in drawn.entries] == [
        ("B1.M1", "minors"), ("B1.M2", "minors")]
    assert [row["id"] for row in rows] == ["B1.M1", "B1.M2"]
    # The minors are listed under a heading that does not call them major.
    listed = run(capsys, "list", "--waiting")
    assert f"\ndecisions not yet ruled:\n  {ROUND} B1.M1: " in listed
    assert "major decisions" not in listed
    project_text = export.as_markdown(export.questions_of(ROUND.split("/")[0]))
    assert "## Decisions not yet ruled\n" in project_text
    assert "Major decisions" not in project_text


def test_with_no_ratify_step_the_same_minor_is_the_lead_s_and_not_in_the_export(
        opened, capsys):
    round_dir = opened("paper", ratify="majors+minors")
    built(round_dir)

    drawn = export.questions_of(ROUND, view="ratify")

    assert drawn.entries == () and drawn.not_ratified == "the flow has no ratify step"
    assert json.loads(run(capsys, "list", "--waiting", "--json"))["decisions"] == []
    assert label(capsys) == f"the lead's to settle: {SETTLE}"


def test_a_minor_that_forecloses_is_drawn_alone(opened):
    # No report declares one now; a log written before that rule may hold one.
    round_dir = opened(ratify="majors+minors")
    built(round_dir, decisions_=(("M1", "minor"), ("M2", "minor")))
    report(round_dir, (("M3", "minor"),), forecloses=True)

    drawn = export.questions_of(ROUND, view="ratify")

    data = {one["id"]: one for one in drawn.data()["entries"]}
    assert data["B1.M3"].get("alone") is True and "group" not in data["B1.M3"]
    assert data["B1.M1"]["group"] == "minors"
    text = export.as_markdown(drawn)
    assert "## Ratification: minor decisions that foreclose, each on its own" in text
    assert "## Ratification: minor decisions, one group: B1.M1, B1.M2" in text


def test_the_index_keeps_a_settled_decision_as_settled_and_the_export_skips_it(opened):
    round_dir = opened()
    built(round_dir, decisions_=(("M1", "minor"), ("M2", "major")))
    settle()

    summary = index.read("proj-a", round_dir.parent, "a-round")
    bodies = {item.id: item.body for item in summary.of(index.DECISION)}

    assert (bodies["B1.M1"]["state"], bodies["B1.M2"]["state"]) == ("settled", "open")
    assert bodies["B1.M1"]["not_ratified"] is None
    assert [one.id for one in export.waiting(
        folders.home("proj-a", settings.load())).entries] == ["B1.M2"]


# -- the events, and logs from before ----------------------------------------------------

def test_the_two_events_are_checked_as_any_other():
    events.check("decision.settled",
                 {"id": "B1.M1", "chose": "accept", "record": PTR, "by": "lead"})
    events.check("decision.raised", {"id": "B1.M1", "record": PTR, "by": "lead"})
    for name, fields in (
            ("decision.settled", {"id": "B1.M1", "chose": "accept", "by": "lead"}),
            ("decision.settled", {"id": "B1.M1", "chose": "accept", "record": PTR,
                                  "by": "lead", "actor": "x"}),
            ("decision.raised", {"id": "B1.M1", "record": PTR}),
            ("decision.raised", {"id": "B1.M1", "record": PTR, "by": "lead",
                                 "weight": "major"})):
        with pytest.raises(ValueError):
            events.check(name, fields)


def test_numbers_written_before_settled_was_counted_read_it_as_zero():
    old = {"decisions": {"accept": 2, "reject": 1}}
    assert "decisions: accepted 2, rejected 1; settled by the lead: accepted 0, " \
        "rejected 0" in numbers.lines(old)
    assert numbers.summed([old, {"decisions": {"accept": 1, "reject": 0},
                                 "settled": {"accept": 3, "reject": 0}}]) == {
        "decisions": {"accept": 3, "reject": 1}, "settled": {"accept": 3, "reject": 0}}


def test_a_round_closed_before_this_rule_reads_as_it_did(opened, capsys):
    # Its close did not wait for the lead's settlement; nothing is read again.
    round_dir = opened()
    built(round_dir)
    log.append(log.round_log(round_dir), "round.closed",
               {"numbers": {"decisions": {"accept": 0, "reject": 0}}}, by="planner")
    before = len(history(round_dir))

    assert folded(round_dir).status == "closed"
    assert label(capsys) == "left open, not put to the operator (ratify is majors)"
    shown = run(capsys, "show", ROUND)
    assert ("  left open, not put to the operator: decision B1.M1, minor: a choice M1"
            in shown)
    assert len(history(round_dir)) == before
