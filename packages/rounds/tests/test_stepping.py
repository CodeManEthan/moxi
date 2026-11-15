"""The flow a round follows: read from its log and never from the config, moved up by
the lead with `mark --flow`, and moved anywhere else by the operator's ruling."""

from __future__ import annotations

import pytest

from moxi_rounds import (asking, dropping, flows, holds, index, listing, log, opening,
                         questions, ruling, showing, state, stepping, write)
from moxi_rounds import settings as own
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Refused

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"

STEP = {name: f'{{ step = "{name}", by = "helper" }}'
        for name in ("design", "verify-design", "build", "verify-build", "finish",
                     "retro")}


def flow_text(name, *steps):
    """The config text of one flow, `name`, of the steps named."""
    return (f"[flows.{name}]\nsteps = [\n"
            + "".join(f"  {STEP[step]},\n" for step in steps) + "]\n")


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of `proj-a`."""
    add_project("proj-a", notes_root=tmp_path / "notes")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds"


def opened(rounds, flow="small", name=ROUND):
    """Open a round on `flow` with the stage B1 declared, and return its log."""
    opening.open_round(name, description="a placeholder round", actor="planner",
                       flow=flow)
    path = log.round_log(rounds / name.partition("/")[2])
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "branch-b1"}, actor="planner")
    return path


def folded(path):
    return state.fold(log.read(path))


def last(path):
    event = log.read(path)[-1]
    return {key: event[key] for key in event if key not in ("v", "n", "at")}


def rule(item, record="comms:12", **more):
    return ruling.rule(ROUND, item=item, record=record, actor="planner", **more)


def up(flow):
    return stepping.step_up(ROUND, flow, actor="planner")


# -- the flow a round follows ------------------------------------------------------------

def test_a_round_follows_the_steps_its_log_recorded(rounds):
    path = opened(rounds)

    found = stepping.current(folded(path))

    assert found.name == "small"
    assert list(found.steps) == flows.BUILT_IN["small"]


def test_editing_a_flow_in_the_config_changes_no_open_round(rounds, write_config):
    path = opened(rounds)
    before = path.read_bytes()

    write_config(flow_text("small", "build", "finish"))

    assert path.read_bytes() == before
    assert list(stepping.current(folded(path)).steps) == flows.BUILT_IN["small"]
    assert showing.data(showing.show(ROUND))["steps"] == flows.BUILT_IN["small"]
    # A round opened from now on gets the flow as the config has it.
    later = opened(rounds, name="proj-a/b-round")
    assert flows.names(stepping.current(folded(later)).steps) == ("build", "finish")


@pytest.mark.parametrize("flow, has", [
    ("fix", {"build": True, "commission": True, "check": True, "ratify": True,
             "docket": False, "design": False, "retro": False}),
    ("small", {"design": True, "retro": True, "docket": False, "verify-design": False}),
    ("full", {"verify-design": True, "docket": True, "commission": True})])
def test_a_flow_has_the_steps_it_lists_and_the_ones_that_come_with_them(
        rounds, flow, has):
    found = folded(opened(rounds, flow))

    assert {step: stepping.has_step(found, step) for step in has} == has


def test_a_flow_that_builds_nothing_has_no_step_of_a_build(rounds, write_config):
    write_config(flow_text("paper", "design", "verify-design"))
    found = folded(opened(rounds, "paper"))

    assert stepping.has_step(found, "docket")
    assert not any(stepping.has_step(found, step)
                   for step in ("commission", "check", "ratify", "build"))


# -- the lead's step up ------------------------------------------------------------------

def test_the_lead_moves_a_round_up_to_a_later_tier(rounds, capsys):
    path = opened(rounds)

    assert main(["mark", ROUND, "--flow", "full", "--as", "planner"]) == 0

    assert last(path) == {"actor": "planner", "type": "flow.changed", "was": "small",
                          "flow": "full", "steps": flows.BUILT_IN["full"], "by": "lead"}
    found = stepping.current(folded(path))
    assert (found.name, list(found.steps)) == ("full", flows.BUILT_IN["full"])
    assert capsys.readouterr().out == (
        f"{ROUND}   flow full (design, verify-design, build, verify-build, finish, "
        "retro)   was: small\n")


def test_a_step_up_is_one_event_and_changes_no_other_record(rounds):
    path = opened(rounds, "fix")
    asking.ask(ROUND, ASKED, origin="comms:11", actor="scout")
    before = folded(path)

    stepped = up("full")

    after = folded(path)
    assert len(after.history) == len(before.history) + 1
    assert (stepped.was, stepped.flow) == ("fix", "full")
    assert after.questions == before.questions and after.rulings == before.rulings
    assert questions.recorded_stages(after) == questions.recorded_stages(before)


def test_a_step_up_while_a_question_holds_a_stage_lifts_no_hold(rounds):
    path = opened(rounds, "fix")
    asking.ask(ROUND, ASKED, origin="comms:11", actor="scout")
    assert [one.id for one in holds.on_stage(folded(path), "B1")] == ["Q1"]

    up("small")
    up("full")

    found = folded(path)
    assert [one.id for one in holds.on_stage(found, "B1")] == ["Q1"]
    assert [one.id for one in holds.on_close(found)] == ["Q1"]


def test_a_same_name_change_takes_the_steps_the_config_has_now(rounds, write_config):
    path = opened(rounds)
    write_config(flow_text("small", "design", "verify-design", "build", "verify-build",
                           "finish", "retro"))

    up("small")

    assert last(path)["was"] == last(path)["flow"] == "small"
    assert flows.names(stepping.current(folded(path)).steps) == (
        "design", "verify-design", "build", "verify-build", "finish", "retro")


def test_a_same_name_change_that_loses_a_step_is_refused_to_the_lead(
        rounds, write_config):
    path = opened(rounds)
    write_config(flow_text("small", "design", "build", "verify-build", "finish"))
    before = path.read_bytes()

    with pytest.raises(Refused) as refused:
        up("small")

    assert "the flow small has no retro step" in str(refused.value)
    assert "is the operator's" in str(refused.value)
    assert f"moxi rounds rule {ROUND} flow:small --record PTR" in str(refused.value)
    assert path.read_bytes() == before
    # The operator's ruling makes the same move.
    rule("flow:small")
    assert "retro" not in flows.names(stepping.current(folded(path)).steps)


CUSTOM = ('tiers = ["fix", "twin", "small", "lean", "full"]\n'
          + flow_text("twin", "design", "build", "verify-build", "finish", "retro")
          + flow_text("lean", "design", "build", "verify-build", "finish")
          + flow_text("side", "design", "build", "verify-build", "finish", "retro"))


@pytest.mark.parametrize("start, flow, why", [
    ("small", "twin", "twin comes before small in the tiers"),
    ("full", "small", "the flow small has no verify-design step"),
    ("small", "fix", "the flow fix has no design and no retro step"),
    ("small", "side", "side is not among the tiers"),
    ("side", "full", "side is not among the tiers"),
    ("small", "lean", "the flow lean has no retro step"),
    ("full", "fix", "the flow fix has no design and no verify-design and no retro "
                    "step")])
def test_any_move_but_a_step_up_is_refused_to_the_lead_and_names_the_ruling(
        rounds, write_config, start, flow, why):
    write_config(CUSTOM)
    path = opened(rounds, start)
    before = path.read_bytes()

    with pytest.raises(Refused) as refused:
        up(flow)

    assert why in str(refused.value)
    assert f"moxi rounds rule {ROUND} flow:{flow} --record PTR" in str(refused.value)
    assert path.read_bytes() == before
    assert main(["mark", ROUND, "--flow", flow]) == 3
    assert path.read_bytes() == before


def test_a_move_to_the_flow_and_the_steps_the_round_follows_is_refused(rounds):
    path = opened(rounds)
    before = path.read_bytes()

    with pytest.raises(Refused, match="follows the flow small already"):
        up("small")
    with pytest.raises(Refused, match="follows the flow small already"):
        rule("flow:small")

    assert path.read_bytes() == before


def test_a_move_to_a_flow_the_config_does_not_have_is_refused(rounds):
    path = opened(rounds)
    before = path.read_bytes()

    with pytest.raises(Refused) as by_lead:
        up("heavy")
    with pytest.raises(Refused) as by_ruling:
        rule("flow:heavy")

    assert "there is no flow 'heavy'; NAME below is one of fix, small, full" in str(
        by_lead.value)
    assert f"moxi rounds mark {ROUND} --flow NAME" in str(by_lead.value)
    assert f"moxi rounds rule {ROUND} flow:NAME --record PTR" in str(by_ruling.value)
    assert path.read_bytes() == before


def test_a_round_that_has_ended_is_moved_to_no_flow(rounds):
    path = opened(rounds)
    dropping.drop(ROUND, by="operator", record="comms:14", actor="planner")
    before = path.read_bytes()

    with pytest.raises(Refused, match="takes a change of flow no longer"):
        up("full")
    with pytest.raises(Refused, match="takes a new ruling no longer"):
        rule("flow:fix")

    assert path.read_bytes() == before


@pytest.mark.parametrize("argv", [
    [ROUND, "Q1", "--flow", "full"], [ROUND, "--flow", "full", "--record", "comms:12"],
    [ROUND, "--flow", "full", "--reopen"], [ROUND, "--flow"], ["proj-a", "--flow", "full"],
    [ROUND, "--flow", " "], [ROUND, "--blocks", "all"]])
def test_a_step_up_that_cant_be_read_is_a_usage_error(rounds, argv):
    path = opened(rounds)
    before = path.read_bytes()

    assert main(["mark", *argv]) == 2

    assert path.read_bytes() == before


# -- the operator's move -----------------------------------------------------------------

@pytest.mark.parametrize("start, flow", [("small", "fix"), ("full", "small"),
                                         ("small", "full"), ("fix", "full")])
def test_the_operators_ruling_moves_a_round_to_any_flow(rounds, start, flow):
    path = opened(rounds, start)
    count = len(log.read(path))

    ruled = rule(f"flow:{flow}")

    assert len(log.read(path)) == count + 1
    assert last(path) == {
        "actor": "planner", "type": "ruling.recorded", "id": "C1", "record": "comms:12",
        "kind": "process", "rules": f"flow:{flow}", "was": start, "flow": flow,
        "steps": flows.BUILT_IN[flow]}
    assert ruled.id == "C1" and ruled.text is None
    found = stepping.current(folded(path))
    assert (found.name, list(found.steps)) == (flow, flows.BUILT_IN[flow])


def test_a_step_down_lifts_no_hold_and_keeps_every_stage(rounds):
    path = opened(rounds, "full")
    asking.ask(ROUND, ASKED, origin="comms:11", actor="scout")

    rule("flow:fix")

    found = folded(path)
    assert [one.id for one in holds.on_stage(found, "B1")] == ["Q1"]
    assert questions.recorded_stages(found) == ("B1",)


def test_the_ruling_is_printed_with_the_flow_the_round_follows_from_then_on(
        rounds, capsys):
    opened(rounds)

    assert main(["rule", ROUND, "flow:fix", "--record", "comms:12"]) == 0

    assert capsys.readouterr().out.splitlines()[:2] == [
        "Recorded as C1: flow:fix",
        f"{ROUND} follows the flow fix (build, verify-build, finish) from now on; it "
        "followed small"]


def test_a_change_of_flow_without_his_pointer_is_refused(rounds):
    path = opened(rounds)
    before = path.read_bytes()

    with pytest.raises(Refused, match="needs a pointer to where he said it"):
        rule("flow:fix", record=None)

    assert path.read_bytes() == before


def test_a_change_of_flow_is_corrected_by_another_and_never_taken_back(rounds):
    path = opened(rounds)
    rule("flow:fix")
    before = path.read_bytes()

    with pytest.raises(Refused) as refused:
        rule("revoke:C1", record="comms:13")
    with pytest.raises(Refused, match="a ruling of flow:fix is not ruled a second "
                                      "time"):
        ruling.rule(ROUND, text="Back to the small flow.", record="comms:13",
                    actor="planner", supersedes="C1")
    with pytest.raises(BadArgument, match="a change of flow replaces none before it"):
        rule("flow:small", record="comms:13", supersedes="C1")

    assert "a change of flow is neither taken back nor replaced" in str(refused.value)
    assert f"moxi rounds rule {ROUND} flow:small --record PTR" in str(refused.value)
    assert path.read_bytes() == before
    # The way back is the move itself, with no earlier ruling named.
    rule("flow:small", record="comms:13")
    found = folded(path)
    assert found.flow == "small" and found.steps == flows.BUILT_IN["small"]
    assert [(one.id, one.replaced_by, one.revoked_by) for one in found.rulings] == [
        ("C1", None, None), ("C2", None, None)]


@pytest.mark.parametrize("target, item, more", [
    ("proj-a", "flow:fix", {}), (ROUND, "flow:", {}), (ROUND, "flow:fix", {"stages": "B1"}),
    (ROUND, "flow:fix", {"standing": True}), (ROUND, "flow:fix", {"text": " "}),
    (ROUND, "flow:fix", {"chose": "a"}), (ROUND, "flow:fix", {"until": "B1"})])
def test_a_ruling_of_the_flow_that_cant_be_read_is_a_usage_error(
        rounds, target, item, more):
    path = opened(rounds)
    before = path.read_bytes()

    with pytest.raises(BadArgument):
        ruling.rule(target, item=item, record="comms:12", actor="planner", **more)

    assert path.read_bytes() == before


# -- where a change of flow shows --------------------------------------------------------

def test_the_flow_a_round_follows_now_is_shown_listed_and_in_its_note(rounds, capsys):
    path = opened(rounds)
    up("full")
    rule("flow:fix")
    capsys.readouterr()

    assert main(["show", ROUND]) == 0
    shown = capsys.readouterr().out
    assert f"round {ROUND}   open   flow fix (build, verify-build, finish)\n" in shown
    assert "planner  flow.changed  small to full, by lead\n" in shown
    assert "planner  ruling.recorded  C1\n" in shown
    data = showing.data(showing.show(ROUND))
    assert (data["flow"], data["steps"]) == ("fix", flows.BUILT_IN["fix"])
    note = (path.parent / "round-note.md").read_text(encoding="utf-8")
    assert "## Flow\n\nfix: build, verify-build, finish\n" in note
    assert "planner flow.changed small to full, by lead\n" in note
    assert "- C1: flow:fix\n" in note
    found = own.load()
    assert [one.flow for one in listing.rounds(settings=found).rounds] == ["fix"]
    assert index.read("proj-a", rounds, "a-round").flow == "fix"
    assert main(["list"]) == 0
    assert f"{ROUND}   open   flow fix   " in capsys.readouterr().out


# -- a change of flow stopped right after its record -------------------------------------

def stopped_after_its_append(monkeypatch, act):
    """Run `act`, and stop it as soon as its event is in the log: before anything that
    follows an append has run."""
    def stop(written):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(write, "ON_APPEND", (stop,))
        with pytest.raises(KeyboardInterrupt):
            act()


def test_a_ruling_of_the_flow_stopped_after_its_append_has_moved_the_round(
        rounds, monkeypatch):
    path = opened(rounds)
    events_before = len(log.read(path))

    stopped_after_its_append(monkeypatch, lambda: rule("flow:full"))

    with pytest.raises(Refused, match="nothing to move"):
        rule("flow:full")
    after = log.read(path)
    assert len(after) == events_before + 1
    assert [(event["id"], event["rules"], event["was"], event["flow"])
            for event in after if event["type"] == "ruling.recorded"] == [
        ("C1", "flow:full", "small", "full")]
    assert not [event for event in after if event["type"] == "flow.changed"]
    found = stepping.current(folded(path))
    assert (found.name, list(found.steps)) == ("full", flows.BUILT_IN["full"])


def test_a_step_up_stopped_after_its_append_has_moved_the_round(rounds, monkeypatch):
    path = opened(rounds)
    events_before = len(log.read(path))

    stopped_after_its_append(monkeypatch, lambda: up("full"))

    with pytest.raises(Refused, match="nothing to move"):
        up("full")
    after = log.read(path)
    assert len(after) == events_before + 1
    assert [event["flow"] for event in after if event["type"] == "flow.changed"] == [
        "full"]
    found = stepping.current(folded(path))
    assert (found.name, list(found.steps)) == ("full", flows.BUILT_IN["full"])
