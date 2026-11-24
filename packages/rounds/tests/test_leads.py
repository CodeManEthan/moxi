"""A round's lead: named at the opening or by `mark --lead`, folded from the log, and
read as the round's own, else the project's, else none."""

from __future__ import annotations

import pytest
from moxi import cli

from moxi_rounds import dropping, leads, log, opening, state, write
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Locked, NoRound, Refused

ROUND = "atlas/billing-export"


@pytest.fixture
def rounds(tmp_path, add_project, capsys):
    """The rounds folder of `atlas`, a project whose record names no lead."""
    add_project("atlas", notes_root=tmp_path / "notes")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds"


def opened(rounds, **more):
    """Open the round and return its log."""
    opening.open_round(ROUND, description="a placeholder round", by="builder-1", **more)
    return log.round_log(rounds / "billing-export")


def folded(path):
    return state.fold(log.read(path))


def mark(*argv):
    return main(["mark", ROUND, *argv])


def set_project_lead(name):
    argv = (["projects", "set", "atlas", "--no-lead"] if name is None
            else ["projects", "set", "atlas", "--lead", name])
    assert cli.main(argv) == 0


# -- the name ----------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["a.b-c_d", "name@scope", "kestrel", "A9@b.c-d"])
def test_a_leads_name_is_letters_digits_and_dot_underscore_dash_with_one_scope(name):
    assert leads.is_name(name)
    assert leads.check(name, "again") == name


@pytest.mark.parametrize("name", ["", "a b", "a@b@c", "a/b", "@scope", "name@",
                                  "name\n", None, 7])
def test_any_other_shape_is_no_leads_name(name):
    assert not leads.is_name(name)
    with pytest.raises(BadArgument) as refused:
        leads.check(name, "moxi rounds mark ROUND --lead NAME")
    assert refused.value.reason == (f"{name!r} is not a lead's name: letters, digits, "
                                    ". _ -, and at most one @scope")
    assert refused.value.fix == "moxi rounds mark ROUND --lead NAME"


# -- the fold ----------------------------------------------------------------------------

def test_the_lead_is_the_openings_then_the_newest_changes(rounds):
    path = opened(rounds, lead="atlas-lead")
    assert folded(path).lead == "atlas-lead"

    leads.mark(ROUND, "kestrel", by="builder-1")
    leads.mark(ROUND, "builder-1", by="builder-1")

    assert folded(path).lead == "builder-1"


def test_a_round_opened_with_no_lead_has_none_of_its_own(rounds):
    assert folded(opened(rounds)).lead is None


def test_a_change_that_names_nobody_changes_no_lead():
    found = state.fold([
        {"v": 1, "n": 1, "at": "2026-11-20T10:00:00Z", "actor": "builder-1",
         "type": "round.opened", "description": "d", "flow": "fix", "steps": [],
         "lead": "atlas-lead"},
        {"v": 1, "n": 2, "at": "2026-11-20T10:00:01Z", "actor": "builder-1",
         "type": "lead.changed", "lead": None, "was": "atlas-lead"}])
    assert found.lead == "atlas-lead"


# -- the lead to use ---------------------------------------------------------------------

def test_the_rounds_own_lead_wins_over_the_projects():
    assert leads.of(state.State(lead="kestrel"), "atlas-lead") == {
        "name": "kestrel", "from": "round"}


def test_with_none_of_its_own_a_round_reads_the_projects_lead():
    assert leads.of(state.State(), "atlas-lead") == {"name": "atlas-lead",
                                                      "from": "project"}


def test_with_neither_there_is_no_lead():
    assert leads.of(state.State(), None) is None


def test_a_projects_lead_changed_in_projects_shows_at_the_next_read(rounds):
    from moxi_rounds import folders

    path = opened(rounds)
    set_project_lead("atlas-lead")
    assert leads.of(folded(path), folders.home("atlas").lead) == {
        "name": "atlas-lead", "from": "project"}

    set_project_lead("kestrel")
    assert leads.of(folded(path), folders.home("atlas").lead) == {
        "name": "kestrel", "from": "project"}

    set_project_lead(None)
    assert leads.of(folded(path), folders.home("atlas").lead) is None


# -- mark --------------------------------------------------------------------------------

def test_a_mark_appends_one_change_naming_the_lead_before(rounds):
    path = opened(rounds)

    led = leads.mark(ROUND, "atlas-lead", by="builder-1")

    event = log.read(path)[-1]
    assert (event["type"], event["lead"], event["was"], event["actor"]) == (
        "lead.changed", "atlas-lead", None, "builder-1")
    assert (led.lead, led.was, led.found, led.event) == ("atlas-lead", None, False,
                                                         event)

    leads.mark(ROUND, "kestrel", by="builder-1")
    assert log.read(path)[-1]["was"] == "atlas-lead"


def test_the_same_lead_again_writes_nothing(rounds, capsys):
    path = opened(rounds, lead="atlas-lead")
    before = path.read_bytes()

    led = leads.mark(ROUND, "atlas-lead", by="builder-1")

    assert led.found and led.event is None
    assert path.read_bytes() == before
    assert mark("--lead", "atlas-lead") == 0
    assert capsys.readouterr().out == (f"atlas-lead leads {ROUND} already; nothing was "
                                       "written\n")
    assert path.read_bytes() == before


def test_the_projects_lead_marked_as_the_rounds_own_is_written(rounds):
    path = opened(rounds)
    set_project_lead("atlas-lead")

    assert not leads.mark(ROUND, "atlas-lead", by="builder-1").found
    assert folded(path).lead == "atlas-lead"


def test_the_command_says_the_new_lead_and_the_one_before(rounds, capsys):
    opened(rounds)

    assert mark("--lead", "atlas-lead", "--as", "kestrel") == 0
    assert capsys.readouterr().out == f"{ROUND}   lead: atlas-lead   was: none\n"
    assert mark("--lead", "builder-1") == 0
    assert capsys.readouterr().out == f"{ROUND}   lead: builder-1   was: atlas-lead\n"


def test_a_mark_given_an_id_is_refused(rounds, capsys):
    path = opened(rounds)
    before = path.read_bytes()

    assert mark("Q1", "--lead", "atlas-lead") == 2

    err = capsys.readouterr().err
    assert "--lead marks the round, and takes no ID" in err
    assert "mark ROUND --lead NAME" in err
    assert path.read_bytes() == before


def test_a_mark_with_a_pointer_is_refused(rounds, capsys):
    path = opened(rounds)
    before = path.read_bytes()

    assert mark("--lead", "atlas-lead", "--record", "comms:12") == 2

    assert "leave --record out" in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["x y", "a@b@c", ""])
def test_a_mark_with_no_leads_name_is_refused(rounds, capsys, name):
    path = opened(rounds)
    before = path.read_bytes()

    assert mark("--lead", name) == 2

    err = capsys.readouterr().err
    assert f"{name!r} is not a lead's name: letters, digits, . _ -, and at most one " \
           "@scope" in err
    assert f"moxi rounds mark {ROUND} --lead NAME" in err
    assert path.read_bytes() == before


def test_an_ended_round_takes_no_lead(rounds):
    path = opened(rounds)
    dropping.drop(ROUND, by="operator", record="comms:14", run_by="builder-1")
    before = path.read_bytes()

    with pytest.raises(Refused, match="takes a lead no longer"):
        leads.mark(ROUND, "atlas-lead", by="builder-1")

    assert path.read_bytes() == before


def test_a_round_that_is_not_there_is_no_round(rounds):
    with pytest.raises(NoRound):
        leads.mark(ROUND, "atlas-lead", by="builder-1")


def test_a_held_lock_stops_the_mark(rounds, holder):
    path = opened(rounds)
    before = path.read_bytes()
    held = holder(write.round_lock("atlas", "billing-export"))

    with pytest.raises(Locked) as stopped:
        leads.mark(ROUND, "atlas-lead", by="builder-1", wait_s=0.2)

    assert stopped.value.holder == held.own_pid
    assert path.read_bytes() == before
