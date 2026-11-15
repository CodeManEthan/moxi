"""The sizing check: `mark ROUND B<n> --brief FILE` passes a brief that names one slice
the stage declares within the slice limit, or one verification, and records it; `fold`
warns of a slice over the limit; `show` and the round note say which slices of an open
build have a sized brief, and `next` names the ones that have none."""

from __future__ import annotations

import hashlib
import json

import pytest

from moxi_rounds import (builds, freezing, log, next_step, opening, ruling, showing,
                         sizing, state)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadBlock, Refused

ROUND = "proj-a/a-round"
SLICES = [{"id": "1a", "what": "the log and its fold", "minutes": 40},
          {"id": "1b", "what": "the verbs", "minutes": 30},
          {"id": "1c", "what": "everything else", "minutes": 60}]


@pytest.fixture
def built(git_repo, tmp_path, capsys):
    """`proj-a` rooted at the test's git repo, its round in the `fix` flow, B1 on the
    branch `work` with three slices commissioned to `coder`, and B2, a stage with no
    build. Returns the round's folder."""
    from moxi import cli
    from moxi_rounds import commissioning

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="fix")
    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    log.append(log.round_log(round_dir), "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": [],
                "slices": SLICES}, by="planner")
    log.append(log.round_log(round_dir), "stage.declared",
               {"id": "B2", "repo": "proj-a", "branch": "more", "after": []},
               by="planner")
    commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    capsys.readouterr()
    return round_dir


def brief(round_dir, *keys, name="brief-1a.md"):
    """Write a brief in the round's folder whose block holds the lines `keys`, and
    return its path."""
    path = round_dir / name
    path.write_text("# A brief\n\nThe commission block goes here.\n\n```toml rounds\n"
                    + "\n".join(keys) + "\n```\n", encoding="utf-8")
    return path


def size(path, stage="B1", **more):
    return sizing.size(ROUND, stage, path, by="planner", **more)


def events(round_dir):
    return log.read(log.round_log(round_dir))


def sized_events(round_dir):
    return [one for one in events(round_dir) if one["type"] == sizing.SIZED]


# -- passing -----------------------------------------------------------------------------

def test_a_brief_for_one_declared_slice_within_the_limit_is_recorded(built):
    path = brief(built, 'stage = "B1"', 'slice = "1a"')

    done = size(path)

    assert done.found is False
    assert (done.minutes, done.limit) == (40, 45)
    [event] = sized_events(built)
    assert {key: event[key] for key in ("stage", "slice", "verify", "brief", "sha256",
                                        "result", "actor")} == {
        "stage": "B1", "slice": "1a", "verify": None, "brief": "brief-1a.md",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "result": "passed",
        "actor": "planner"}


def test_a_brief_for_one_verification_is_recorded_for_any_kind(built):
    for kind in sizing.VERIFIES:
        size(brief(built, 'stage = "B1"', f'verify = "{kind}"', name=f"{kind}.md"))

    assert [(one["slice"], one["verify"]) for one in sized_events(built)] == [
        (None, "build"), (None, "design"), (None, "retro")]


def test_a_brief_outside_the_round_dir_is_named_by_its_whole_path(built, tmp_path):
    elsewhere = tmp_path / "briefs"
    elsewhere.mkdir()
    path = brief(elsewhere, 'stage = "B1"', 'slice = "1b"')

    size(path)

    assert sized_events(built)[0]["brief"] == str(path)


def test_the_same_brief_sized_again_writes_nothing_and_says_so(built):
    path = brief(built, 'stage = "B1"', 'slice = "1a"')
    first = size(path)
    before = events(built)

    again = size(path)

    assert again.found is True and again.event is None
    assert again.sized == first.sized
    assert events(built) == before


def test_a_brief_changed_since_it_was_sized_is_recorded_again(built):
    path = brief(built, 'stage = "B1"', 'slice = "1a"')
    size(path)
    path.write_text(path.read_text(encoding="utf-8") + "\nOne more line.\n",
                    encoding="utf-8")

    done = size(path)

    assert done.found is False
    assert [one["sha256"] for one in sized_events(built)][-1] == done.sized.sha256
    assert len(sized_events(built)) == 2


def test_the_limit_is_the_projects_slice_minutes_as_read_when_sized(built, write_config):
    write_config("[projects.proj-a]\nslice_minutes = 60\n")

    done = size(brief(built, 'stage = "B1"', 'slice = "1c"'))

    assert (done.minutes, done.limit) == (60, 60)


# -- refusals ----------------------------------------------------------------------------

def test_a_slice_over_the_limit_is_refused_naming_its_minutes_and_the_limit(built):
    before = events(built)

    with pytest.raises(Refused) as caught:
        size(brief(built, 'stage = "B1"', 'slice = "1c"'))

    said = str(caught.value)
    assert "1c of B1 is given 60 minutes, over the limit of 45 (slice_minutes)" in said
    # The fix flow has no design to cut the slice in.
    assert "cut the slice" not in said
    assert ("raise slice_minutes in the project's settings: with no design step, a "
            "stage's slices are the ones its commission declared") in said
    assert events(built) == before


def test_a_slice_over_the_limit_in_a_flow_with_a_design_is_cut_in_the_design(
        git_repo, tmp_path):
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="small")
    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    log.append(log.round_log(round_dir), "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": [],
                "slices": SLICES}, by="planner")

    with pytest.raises(Refused) as caught:
        size(brief(round_dir, 'stage = "B1"', 'slice = "1c"'))

    assert "cut the slice in two in the design and fold it" in str(caught.value)


def test_a_slice_the_stage_does_not_declare_is_refused_naming_its_slices(built):
    with pytest.raises(Refused) as caught:
        size(brief(built, 'stage = "B1"', 'slice = "1z"'))

    assert "B1 declares no slice 1z: its slices are 1a, 1b, 1c" in str(caught.value)
    assert sized_events(built) == []


def test_a_slice_of_a_stage_with_no_slices_is_refused_and_a_verification_passes(built):
    with pytest.raises(Refused) as caught:
        size(brief(built, 'stage = "B2"', 'slice = "2a"'), stage="B2")
    assert ("B2 declares no slice 2a: it declares none, so it is one slice: leave slice "
            "out of the block") in str(caught.value)

    size(brief(built, 'stage = "B2"', 'verify = "build"', name="check.md"), stage="B2")
    assert [one["stage"] for one in sized_events(built)] == ["B2"]


def test_a_stage_with_no_slices_is_sized_as_its_one_slice_with_no_minutes(built):
    done = size(brief(built, 'stage = "B2"', name="brief-b2.md"), stage="B2")

    assert (done.minutes, done.limit) == (None, None)
    assert done.sized.piece == "one slice"
    [event] = sized_events(built)
    assert (event["stage"], event["slice"], event["verify"], event["result"]) == (
        "B2", None, None, "passed")


def test_a_block_naming_no_piece_is_refused_for_a_stage_that_declares_slices(built):
    with pytest.raises(Refused) as caught:
        size(brief(built, 'stage = "B1"'))

    assert ("the brief's block names no slice and no verification, and B1 declares "
            "slices: 1a, 1b, 1c") in str(caught.value)
    assert sized_events(built) == []


def test_show_and_next_treat_the_one_slice_of_a_stage_like_any_other(built, tmp_path,
                                                                    capsys):
    from moxi_rounds import commissioning

    other = "proj-a/b-round"
    opening.open_round(other, description="a placeholder round", by="planner",
                       flow="fix")
    commissioning.commission(other, repo="proj-a", branch="more", builder="coder",
                             by="planner")
    capsys.readouterr()

    def row():
        [one] = showing.data(showing.show(other))["builds"]
        return one

    def unsized_warnings():
        return [one for one in next_step.read(other)["warnings"]
                if one["what"] == next_step.UNSIZED]

    assert row()["slices"] == [{"id": None, "what": None, "minutes": None,
                                "sized": None}]
    assert showing.briefs_lines(row()) == ["slices: one slice not sized"]
    [warning] = unsized_warnings()
    assert warning["text"] == ("B1 declares no slices, and its one slice has no sized "
                               "brief: size the brief before a worker gets it")

    path = brief(tmp_path / "notes" / "rounds" / "b-round", 'stage = "B1"')
    assert main(["mark", other, "B1", "--brief", str(path)]) == 0
    assert capsys.readouterr().out.startswith(f"{other} B1 one slice: sized   brief: ")

    assert showing.briefs_lines(row()) == ["slices: one slice sized"]
    assert unsized_warnings() == []


@pytest.mark.parametrize(("keys", "problem"), [
    (('stage = "B1"', 'slice = ["1a", "1b"]'),
     "a list: a brief names one slice, since a worker gets one"),
    (('stage = "B1"', 'slice = "1a"', 'verify = "build"'),
     "names a slice and a verification, and a brief is for one piece of work"),
    (('stage = "B1"', 'verify = "everything"'), "which is none of build | design | retro"),
    (('slice = "1a"',), "the block has no `stage`"),
    (('stage = "B1"', 'slice = "1a"', 'minutes = 40'), "`minutes` is not a key"),
])
def test_a_brief_whose_block_names_no_one_piece_is_refused_as_a_bad_block(
        built, keys, problem):
    with pytest.raises(BadBlock) as caught:
        size(brief(built, *keys))

    assert problem in str(caught.value)
    assert sized_events(built) == []


def test_a_brief_with_no_data_block_is_refused(built):
    path = built / "plain.md"
    path.write_text("# A brief\n\nNo block.\n", encoding="utf-8")

    with pytest.raises(BadBlock):
        size(path)


def test_a_block_that_names_another_stage_is_refused_naming_both(built):
    with pytest.raises(Refused) as caught:
        size(brief(built, 'stage = "B2"', 'verify = "build"'))

    assert "the brief's block names B2, and the command B1" in str(caught.value)


def test_a_stage_the_round_does_not_have_is_refused(built):
    with pytest.raises(Refused) as caught:
        size(brief(built, 'stage = "B7"', 'verify = "build"'), stage="B7")

    assert "B7 is not a stage of proj-a/a-round" in str(caught.value)


def test_a_stage_that_left_the_plan_is_refused(built):
    ruling.rule(ROUND, item="unstage:B2", record="comms:1234567", by="planner")

    with pytest.raises(Refused) as caught:
        size(brief(built, 'stage = "B2"', 'verify = "build"'), stage="B2")

    assert "B2 left the plan of proj-a/a-round" in str(caught.value)


# -- which build a sizing is of ----------------------------------------------------------

def _commissioned(stage, n):
    return {"v": 1, "n": n, "at": "2026-01-02T03:04:05Z", "actor": "planner",
            "type": "build.commissioned", "stage": stage, "repo": "proj-a",
            "branch": "work", "base": "a" * 40, "target": "main"}


def _event(kind, n, **fields):
    return {"v": 1, "n": n, "at": "2026-01-02T03:04:05Z", "actor": "planner",
            "type": kind, **fields}


def _sized(n, slice_):
    return _event(sizing.SIZED, n, stage="B1", slice=slice_, verify=None,
                  brief=f"brief-{n}.md", sha256=str(n) * 64, result="passed")


def test_a_sizing_counts_for_the_open_build_and_a_dropped_ones_stay_with_it():
    folded = state.fold([
        _event("stage.declared", 1, id="B1", repo="proj-a", branch="work", after=[],
               slices=SLICES),
        _sized(2, "1a"),
        _commissioned("B1", 3),
        _sized(4, "1b"),
        _event("build.dropped", 5, stage="B1", by="lead", record="comms:1234567"),
        _sized(6, "1c"),
        _commissioned("B1", 7),
    ])
    first, second = builds.builds(folded)

    assert [one.n for one in sizing.of_build(folded, first)] == [2, 4]
    assert [one.n for one in sizing.of_build(folded, second)] == [6]
    assert sizing.unsized(folded, second) == ["1a", "1b"]


# -- show, the note and next -------------------------------------------------------------

def test_show_says_which_slices_of_an_open_build_have_a_sized_brief(built, capsys):
    size(brief(built, 'stage = "B1"', 'slice = "1a"'))
    size(brief(built, 'stage = "B1"', 'verify = "build"', name="check.md"))

    assert main(["show", ROUND]) == 0
    text = capsys.readouterr().out
    assert "    slices: 1a sized, 1b not sized, 1c not sized" in text
    assert "    verification briefs sized: build" in text
    assert "brief.sized  B1 slice 1a, brief-1a.md" in text

    data = showing.data(showing.show(ROUND))
    [row] = data["builds"]
    assert [(one["id"], one["sized"] is not None) for one in row["slices"]] == [
        ("1a", True), ("1b", False), ("1c", False)]
    assert row["slices"][0]["sized"]["brief"] == "brief-1a.md"
    assert [one["verify"] for one in row["briefs"]] == ["build"]


def test_the_round_note_says_which_slices_have_a_sized_brief(built):
    size(brief(built, 'stage = "B1"', 'slice = "1b"'))

    note = (built / "round-note.md").read_text(encoding="utf-8")
    assert "slices: 1a not sized, 1b sized, 1c not sized" in note


def test_next_names_the_unsized_slices_of_an_open_build_with_the_mark(built):
    said = next_step.read(ROUND)
    [warning] = [one for one in said["warnings"] if one["what"] == next_step.UNSIZED]
    assert warning["text"] == ("B1's slices 1a, 1b, 1c have no sized brief: size each "
                               "brief before a worker gets it")
    assert warning["fix"] == f"moxi rounds mark {ROUND} B1 --brief FILE"

    size(brief(built, 'stage = "B1"', 'slice = "1a"'))
    size(brief(built, 'stage = "B1"', 'slice = "1b"', name="b.md"))
    [warning] = [one for one in next_step.read(ROUND)["warnings"]
                 if one["what"] == next_step.UNSIZED]
    assert warning["text"].startswith("B1's slice 1c has no sized brief")


def test_next_has_no_sizing_warning_once_every_slice_is_sized(built, write_config):
    write_config("[projects.proj-a]\nslice_minutes = 60\n")
    for one in SLICES:
        size(brief(built, 'stage = "B1"', f'slice = "{one["id"]}"',
                   name=f"{one['id']}.md"))

    assert [one for one in next_step.read(ROUND)["warnings"]
            if one["what"] == next_step.UNSIZED] == []


# -- the command -------------------------------------------------------------------------

def test_the_command_sizes_a_brief_and_says_its_minutes_and_the_limit(built, capsys):
    path = brief(built, 'stage = "B1"', 'slice = "1a"')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    assert main(["mark", ROUND, "B1", "--brief", str(path)]) == 0
    assert capsys.readouterr().out == (
        f"{ROUND} B1 slice 1a (40 of 45 minutes): sized   brief: brief-1a.md   "
        f"sha256: {digest[:12]}\n")

    assert main(["mark", ROUND, "B1", "--brief", str(path)]) == 0
    assert "nothing was written" in capsys.readouterr().out


def test_the_command_refuses_a_slice_over_the_limit_with_exit_3(built, capsys):
    path = brief(built, 'stage = "B1"', 'slice = "1c"')

    assert main(["mark", ROUND, "B1", "--brief", str(path)]) == 3
    assert "over the limit of 45" in capsys.readouterr().err


def test_the_command_takes_no_pointer(built, capsys):
    path = brief(built, 'stage = "B1"', 'verify = "build"')

    assert main(["mark", ROUND, "B1", "--brief", str(path), "--record",
                 "comms:1234567"]) == 2
    assert sized_events(built) == []


# -- fold --------------------------------------------------------------------------------

def test_fold_records_a_slice_over_the_limit_and_warns_of_it(tmp_path, add_project,
                                                              capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="full")
    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    block = "\n".join([
        "[[stage]]", 'id = "B1"', 'repo = "proj-a"', 'branch = "work"', "after = []",
        "  [[stage.slice]]", '  id = "1a"', '  what = "the log"', "  minutes = 40",
        "  [[stage.slice]]", '  id = "1b"', '  what = "the rest"', "  minutes = 50"])
    (round_dir / "design.md").write_text(f"# A design\n\n```toml rounds\n{block}\n```\n",
                                      encoding="utf-8")
    capsys.readouterr()

    frozen = freezing.freeze(ROUND, by="planner")
    assert frozen.oversized == ({"stage": "B1", "slice": "1b", "minutes": 50},)
    assert frozen.slice_minutes == 45
    assert [one["id"] for one in state.fold(events(round_dir)).stages[0].slices] == [
        "1a", "1b"]

    (round_dir / "design.md").write_text(f"# A design, again\n\n```toml rounds\n{block}\n"
                                      "```\n", encoding="utf-8")
    assert main(["fold", ROUND]) == 0
    assert ("warning: the slice 1b of B1 is given 50 minutes, over the 45 of "
            "slice_minutes") in capsys.readouterr().out


def test_show_json_carries_the_sizing_through_the_read_api(built):
    from moxi_rounds import api

    size(brief(built, 'stage = "B1"', 'slice = "1a"'))

    [row] = api.round_data(ROUND)["builds"]
    assert json.loads(json.dumps(row["slices"]))[0]["sized"]["slice"] == "1a"
