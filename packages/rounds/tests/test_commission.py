"""`commission`: the branch, its worktree and scratch folder with their markers, the
build recorded with its id and host, `--existing`, the rerun that keeps only its own
stopped run's worktree, and the conditions it refuses on."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from moxi_rounds import by_branch, commissioning, log, opening, places, steps, write
from moxi_rounds import state as states
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Refused

ROUND = "proj-a/a-round"
OTHER = "proj-a/b-round"


@pytest.fixture
def repo(git_repo, tmp_path):
    """The project `proj-a`, whose root is the test's git repo with `main` checked out,
    keeping its rounds under a notes folder of its own. Returns the repo."""
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    return git_repo


@pytest.fixture
def opened(repo, tmp_path, capsys):
    """`opened(name=ROUND, flow="fix", stages=(("B1", "work"),))`: open the round with
    those stages declared through the log, and return its log."""

    def open_on(name=ROUND, flow="fix", stages=(("B1", "work"),), after=()):
        opening.open_round(name, description="a placeholder round", actor="planner",
                           flow=flow)
        path = log.round_log(tmp_path / "notes" / "rounds" / name.split("/")[1])
        for stage_id, branch in stages:
            log.append(path, "stage.declared",
                       {"id": stage_id, "repo": "proj-a", "branch": branch,
                        "after": list(after) if stage_id != stages[0][0] else []},
                       actor="planner")
        capsys.readouterr()
        return path

    return open_on


def run(*argv) -> int:
    return main(["commission", *argv])


def events(path: Path, kind: str) -> list[dict]:
    return [one for one in log.read(path) if one["type"] == kind]


def refused(name=ROUND, stage="B1", **given) -> list[str]:
    with pytest.raises(Refused) as caught:
        commissioning.commission(name, stage, builder="coder", actor="planner", **given)
    return [one.reason for one in caught.value.unmet]


# -- what it makes -----------------------------------------------------------------------

def test_a_commission_cuts_the_branch_from_the_target_and_records_the_build(
        repo, opened, capsys):
    path = opened()
    base = repo.rev("main")

    assert run(ROUND, "B1", "--builder", "coder", "--record", "comms:1234567",
               "--as", "planner") == 0

    [event] = events(path, "build.commissioned")
    assert {key: event[key] for key in ("stage", "repo", "branch", "base", "target",
                                        "builder", "record", "host")} == {
        "stage": "B1", "repo": "proj-a", "branch": "work", "base": base,
        "target": "main", "builder": "coder", "record": "comms:1234567",
        "host": places.host()}
    assert places.is_build_id(event["build"])
    assert repo.rev("work") == base
    worktree = Path(event["worktree"])
    assert worktree == places.worktree_dir({"worktree_dir": None}) / "proj-a" / "work"
    assert repo.git("-C", str(worktree), "branch", "--show-current") == "work"
    scratch = Path(event["scratch"])
    assert scratch == places.scratch_root() / "proj-a" / "work" / event["build"]
    assert os.listdir(scratch) == [places.SCRATCH_MARKER]
    said = capsys.readouterr().out
    assert f"round {ROUND}   stage B1   flow fix\n" in said
    assert f"commissioned for coder   build {event['build']}" in said


def test_each_folder_carries_a_marker_naming_the_build(repo, opened):
    path = opened()

    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")

    names = {"round": ROUND, "stage": "B1", "repo": "proj-a", "branch": "work"}
    for found in (places.read_marker(made.scratch / places.SCRATCH_MARKER),
                  places.worktree_marker(made.worktree)):
        assert found is not None and found.build == made.build and found.names(**names)
    git_dir = places.worktree_git_dir(made.worktree)
    assert (git_dir / places.WORKTREE_MARKER).is_file()
    # The worktree's marker is in its own git folder, which its status never lists.
    assert repo.git("-C", str(made.worktree), "status", "--porcelain",
                    "--untracked-files=all") == ""
    assert events(path, "build.commissioned")[0]["build"] == made.build


def test_a_branch_with_a_slash_or_a_percent_is_encoded_in_both_paths(repo, opened):
    opened(stages=(("B1", "release/1.0"),))
    opened(OTHER, stages=(("B1", "release%2F"),))

    one = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    two = commissioning.commission(OTHER, "B1", builder="coder", actor="planner")

    assert one.worktree.name == "release%2F1.0"
    assert two.worktree.name == "release%252F"
    assert one.worktree.parent == two.worktree.parent
    assert one.scratch.parent.name == "release%2F1.0"
    assert places.encode("a/b%c") == "a%2Fb%25c"


def test_the_worktree_dir_setting_says_where_worktrees_go(repo, opened, write_config,
                                                           tmp_path):
    write_config(f'worktree_dir = "{tmp_path / "trees"}"\n')
    opened()

    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")

    assert made.worktree == tmp_path / "trees" / "proj-a" / "work"


def test_the_into_setting_names_the_target(repo, opened, write_config):
    write_config('[projects.proj-a]\ninto = "trunk"\n')
    repo.switch("trunk", new_from="main")
    trunk = repo.commit("trunk.txt")
    repo.switch("main")
    opened()

    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")

    assert (made.target, made.base) == ("trunk", trunk)
    assert repo.rev("work") == trunk


def test_the_step_reads_done_once_the_build_is_recorded(repo, opened):
    path = opened()
    commissioning.commission(ROUND, "B1", builder="coder", actor="planner")

    found = steps.one(states.fold(log.read(path)), "commission", "B1")

    assert found.state == steps.DONE


# -- what it refuses ---------------------------------------------------------------------

def test_an_unknown_stage_and_one_that_left_the_plan_are_refused(repo, opened):
    path = opened()
    assert refused(stage="B7") == ["B7 is not a stage of proj-a/a-round"]
    log.append(path, "ruling.recorded",
               {"id": "C1", "record": "comms:1234567", "kind": "process",
                "rules": "unstage:B1"}, actor="planner")
    assert "left the plan" in refused()[0]


def test_a_stage_with_an_open_build_is_refused(repo, opened):
    opened()
    commissioning.commission(ROUND, "B1", builder="coder", actor="planner")

    assert refused() == ["B1 has a build that is open or finished, and a stage is built "
                         "by one build at a time"]


def test_a_full_flow_waits_for_the_design_and_its_verification(repo, opened):
    opened(flow="full")

    reasons = refused()

    assert reasons[:2] == ["no version of the design is frozen",
                           "the design is not verified: it stands at none"]


def test_a_stage_waits_for_the_stages_it_comes_after(repo, opened):
    opened(stages=(("B1", "work"), ("B2", "more")), after=("B1",))

    assert refused(stage="B2") == ["B2 waits for B1, which is not finished"]


def test_a_question_holding_the_stage_is_refused(repo, opened):
    path = opened()
    log.append(path, "question.asked",
               {"id": "Q1", "text": "Which name?", "from": "comms:1234567",
                "blocks": ["B1"], "state": "open", "asked_by": "planner",
                "asked_at": "2026-01-02T03:04:05Z"}, actor="planner")

    assert refused() == ["Q1 holds B1: Which name?"]


def test_a_branch_that_is_there_already_needs_existing(repo, opened):
    opened()
    repo.switch("work", new_from="main")
    repo.switch("main")

    [reason] = refused()

    assert "the branch work is in proj-a already" in reason


def test_existing_on_a_branch_that_is_not_there_is_refused(repo, opened):
    opened()

    assert refused(existing=True) == ["--existing names the branch work, which is not "
                                      "in proj-a"]


def test_existing_takes_the_branch_that_is_there(repo, opened):
    opened()
    repo.switch("work", new_from="main")
    tip = repo.commit("feature.txt")
    repo.switch("main")

    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner",
                                    existing=True)

    assert made.base == repo.rev("main")
    assert repo.git("-C", str(made.worktree), "rev-parse", "HEAD") == tip


def test_existing_on_a_branch_that_shares_commits_with_an_open_build_is_refused(
        repo, opened):
    opened()
    opened(name=OTHER, stages=(("B1", "first"),))
    first = commissioning.commission(OTHER, "B1", builder="coder", actor="planner")
    (first.worktree / "shared.txt").write_text("shared\n", encoding="utf-8")
    repo.git("-C", str(first.worktree), "add", "shared.txt")
    repo.git("-C", str(first.worktree), "commit", "-q", "-m", "shared work")
    repo.git("branch", "work", "first")

    [reason] = refused(existing=True)

    assert reason.startswith("the branch work shares commits with the open build of "
                             f"{OTHER} B1")


def test_a_detached_root_with_no_into_is_refused(repo, opened):
    opened()
    repo.git("switch", "-q", "--detach", "main")

    [reason] = refused()

    assert "sets no into" in reason and "detached head" in reason


def test_a_root_on_an_open_builds_branch_with_no_into_is_refused(repo, opened):
    opened()
    opened(name=OTHER, stages=(("B1", "first"),))
    first = commissioning.commission(OTHER, "B1", builder="coder", actor="planner")
    repo.git("-C", str(first.worktree), "switch", "-q", "--detach")
    repo.git("switch", "-q", "first")

    [reason] = refused()

    assert f"the branch of the open build B1 of {OTHER}" in reason


def test_a_bad_pointer_is_refused_and_a_refusal_writes_nothing(repo, opened):
    path = opened()
    before = path.read_bytes()

    [reason] = refused(record="not a pointer")

    assert "is not a pointer to where the operator gave the go" in reason
    assert path.read_bytes() == before
    assert not places.worktree_dir({"worktree_dir": None}).exists()
    assert not places.scratch_root().exists()


def test_the_command_exits_refused_with_each_unmet_condition(repo, opened, capsys):
    opened(flow="full")

    assert run(ROUND, "B1", "--builder", "coder") == 3

    said = capsys.readouterr().err
    assert "no version of the design is frozen" in said
    assert "run: moxi rounds fold proj-a/a-round" in said


# -- a rerun after a stop -----------------------------------------------------------------

def _interrupt(*_args):
    raise KeyboardInterrupt


def _stop_before_the_append(monkeypatch):
    """Make the next commission stop after its act and before its append."""
    real = write._append

    def stop(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(write, "_append", stop)
    return real


def test_a_rerun_after_a_stop_keeps_its_own_worktree_and_records_one_build(
        repo, opened, monkeypatch, capsys):
    path = opened()
    real = _stop_before_the_append(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    stopped = places.worktree_marker(places.worktree_place({"worktree_dir": None},
                                                           "proj-a", "work"))
    assert events(path, "build.commissioned") == []
    monkeypatch.setattr(write, "_append", real)

    assert run(ROUND, "B1", "--builder", "coder") == 0

    [event] = events(path, "build.commissioned")
    assert event["build"] == stopped.build
    assert "kept: the worktree a stopped run" in capsys.readouterr().out
    assert repo.git("worktree", "list", "--porcelain").count("branch refs/heads/work") == 1
    assert os.listdir(Path(event["scratch"])) == [places.SCRATCH_MARKER]


def test_a_rerun_keeps_the_base_its_stopped_run_cut_the_branch_from_when_the_target_moved(
        repo, opened, monkeypatch):
    path = opened()
    cut_from = repo.rev("main")
    real = _stop_before_the_append(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    monkeypatch.setattr(write, "_append", real)
    moved_to = repo.commit("later.txt")
    assert moved_to != cut_from and repo.rev("main") == moved_to

    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")

    assert made.adopted
    [event] = events(path, "build.commissioned")
    assert event["base"] == cut_from and event["target"] == "main"


def test_a_rerun_makes_the_scratch_folder_a_stop_left_missing(repo, opened, monkeypatch):
    path = opened()
    real = commissioning._make_scratch
    monkeypatch.setattr(commissioning, "_make_scratch", _interrupt)
    with pytest.raises(KeyboardInterrupt):
        commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    monkeypatch.setattr(commissioning, "_make_scratch", real)

    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")

    assert made.adopted and made.scratch.is_dir()
    assert len(events(path, "build.commissioned")) == 1


def test_an_unmarked_worktree_at_the_place_is_refused_and_kept(repo, opened, monkeypatch):
    opened()
    real = commissioning._mark
    monkeypatch.setattr(commissioning, "_mark", _interrupt)
    with pytest.raises(KeyboardInterrupt):
        commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    monkeypatch.setattr(commissioning, "_mark", real)
    place = places.worktree_place({"worktree_dir": None}, "proj-a", "work")

    [reason] = refused()

    assert reason == (f"{place} is there and holds no marker of a build: rounds never "
                      "takes or removes a folder it can't tell it made")
    assert place.is_dir()


def test_an_unmarked_scratch_folder_is_refused_and_kept(repo, opened, monkeypatch):
    opened()
    real_mark = commissioning._mark

    def mark_only_the_worktree(path, marker, again):
        if path.name == places.SCRATCH_MARKER:
            raise KeyboardInterrupt
        real_mark(path, marker, again)

    monkeypatch.setattr(commissioning, "_mark", mark_only_the_worktree)
    with pytest.raises(KeyboardInterrupt):
        commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    monkeypatch.setattr(commissioning, "_mark", real_mark)
    [scratch] = list((places.scratch_root() / "proj-a" / "work").iterdir())

    [reason] = refused()

    assert reason.startswith(f"{scratch} is there and holds no marker of this build")
    assert scratch.is_dir()


def test_a_worktree_whose_marker_names_a_recorded_build_is_refused(repo, opened):
    path = opened()
    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    log.append(path, "build.dropped", {"stage": "B1", "by": "lead",
                                       "record": "comms:1234567"}, actor="planner")

    [reason] = refused(existing=True)

    assert reason.startswith(f"{made.worktree} holds the worktree of the build "
                             f"{made.build} of {ROUND} B1, which is on record")


def test_a_marker_naming_another_round_is_not_adopted(repo, opened):
    opened()
    place = places.worktree_place({"worktree_dir": None}, "proj-a", "work")
    repo.git("worktree", "add", "-q", str(place), "-b", "work", "main")
    git_dir = places.worktree_git_dir(place)
    places.write_marker(git_dir / places.WORKTREE_MARKER,
                        places.marker("0123456789abcdef", round=OTHER, stage="B1",
                                      repo="proj-a", branch="work"))

    [reason] = refused()

    assert reason == (f"{place} holds a worktree marked for the build 0123456789abcdef "
                      f"of {OTHER} B1, which is not this commission's own stopped run")


def test_a_marker_is_one_json_line_with_the_five_names_and_a_time():
    found = places.marker("0123456789abcdef", round=ROUND, stage="B1", repo="proj-a",
                          branch="work", at="2026-01-02T03:04:05Z")

    assert json.loads(found.line()) == {
        "build": "0123456789abcdef", "round": ROUND, "stage": "B1", "repo": "proj-a",
        "branch": "work", "at": "2026-01-02T03:04:05Z"}
    assert found.line().count("\n") == 1


# -- where a build is shown ---------------------------------------------------------------

def test_show_gives_each_build_with_its_folders_and_its_state_from_git(repo, opened,
                                                                      capsys):
    opened()
    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    base = made.base[:7]

    assert main(["show", ROUND]) == 0
    out = capsys.readouterr().out
    assert (f"builds: 1\n  B1: proj-a on work, base {base}, target main, builder "
            f"coder; empty\n    worktree: {made.worktree}\n    scratch: {made.scratch}\n"
            ) in out

    repo.git("-C", str(made.worktree), "commit", "-q", "--allow-empty", "-m", "empty")
    (made.worktree / "feature.txt").write_text("work\n", encoding="utf-8")
    repo.git("-C", str(made.worktree), "add", "feature.txt")
    repo.git("-C", str(made.worktree), "commit", "-q", "-m", "work")
    assert main(["show", ROUND, "--json"]) == 0
    [found] = json.loads(capsys.readouterr().out)["builds"]
    assert {key: found[key] for key in ("stage", "state", "build", "host", "ended",
                                        "problem")} == {
        "stage": "B1", "state": "building", "build": made.build, "host": places.host(),
        "ended": None, "problem": None}


def test_a_build_to_look_at_waits_on_the_operator_and_one_with_fresh_work_does_not(
        repo, opened, capsys, write_config):
    opened()
    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")

    assert main(["list", "--waiting"]) == 0
    assert (f"builds idle, gone, empty, unclear, sharing or not read:\n"
            f"  {ROUND} B1: proj-a on work; empty\n") in capsys.readouterr().out

    (made.worktree / "feature.txt").write_text("work\n", encoding="utf-8")
    repo.git("-C", str(made.worktree), "add", "feature.txt")
    repo.git("-C", str(made.worktree), "commit", "-q", "-m", "work")
    # The test repo dates its commits in 2023, long before now.
    assert main(["list", "--waiting"]) == 0
    assert f"  {ROUND} B1: proj-a on work; building, idle\n" in capsys.readouterr().out
    write_config("[defaults]\nidle_days = 100000\n")
    assert main(["list", "--waiting", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["builds"] == []


def test_the_round_note_lists_the_build_and_names_no_folder_of_this_machine(
        repo, opened, tmp_path):
    opened()
    made = commissioning.commission(ROUND, "B1", builder="coder", actor="planner",
                                    record="comms:1234567")

    note = (tmp_path / "notes" / "rounds" / "a-round" / "round-note.md").read_text(
        encoding="utf-8")

    assert (f"## Builds\n\n- B1: proj-a on work, base {made.base[:7]}, target main, "
            "builder coder; empty\n") in note
    assert "  - go: comms:1234567" in note
    assert str(made.worktree) not in note and str(made.scratch) not in note


# -- one build to a repo's branch, one to a repo in a light flow --------------------------

def ask_on(path, blocks, question_id="Q1") -> None:
    log.append(path, "question.asked",
               {"id": question_id, "text": "Which name?", "from": "comms:1234567",
                "blocks": blocks, "state": "open", "asked_by": "planner",
                "asked_at": "2026-01-02T03:04:05Z"}, actor="planner")


def drop_build(path, stage="B1") -> None:
    log.append(path, "build.dropped", {"stage": stage, "by": "lead",
                                       "record": "comms:1234567", "tips": {},
                                       "questions": []}, actor="planner")


def test_a_branch_another_round_builds_on_is_refused_naming_that_round(repo, opened):
    opened()
    opened(name=OTHER, stages=(("B1", "work"),))
    commissioning.commission(OTHER, "B1", builder="coder", actor="planner")

    assert refused() == [f"the branch work of proj-a is the branch of the open build B1 "
                         f"of {OTHER}, and a repo's branch has one open build at a "
                         "time: that build is finished or dropped from its own round "
                         "first"]


def test_a_light_flow_builds_one_stage_in_a_repo_and_a_dropped_build_does_not_count(
        repo, opened):
    path = opened(stages=(("B1", "work"), ("B2", "more")))
    commissioning.commission(ROUND, "B1", builder="coder", actor="planner")

    with pytest.raises(Refused) as caught:
        commissioning.commission(ROUND, "B2", builder="coder", actor="planner")
    [unmet] = caught.value.unmet
    assert unmet.reason == ("B1 has a build in proj-a that is open or finished, and the "
                            "flow fix does not verify its design: such a round builds "
                            "one stage in a repo")
    assert unmet.fix.endswith(f"moxi rounds mark {ROUND} --flow NAME")

    drop_build(path)
    made = commissioning.commission(ROUND, "B2", builder="coder", actor="planner")
    assert made.stage == "B2"


def test_a_finished_build_counts_for_the_one_build_in_a_repo(repo, opened):
    path = opened(stages=(("B1", "work"), ("B2", "more")))
    commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    log.append(path, "finish.done", {"stage": "B1", "tip": repo.rev("work")},
               actor="coder")

    [reason] = refused(stage="B2")

    assert reason.startswith("B1 has a build in proj-a that is open or finished")


# -- a stage declared at commission ------------------------------------------------------

def declare(name=ROUND, **given):
    return commissioning.commission(name, builder="coder", actor="planner", **given)


def refused_declaring(name=ROUND, **given) -> list:
    with pytest.raises(Refused) as caught:
        declare(name, **given)
    return caught.value.unmet


def test_a_light_flow_declares_the_stage_as_it_commissions_it(repo, opened, capsys):
    path = opened(stages=())

    assert run(ROUND, "--repo", "PROJ-A", "--branch", "work", "--builder", "coder",
               "--slice", "1a=the log", "--minutes", "30",
               "--slice", "1b=the fold", "--minutes", "20") == 0

    declared, commissioned = [one for one in log.read(path)
                              if one["type"] in ("stage.declared", "build.commissioned")]
    assert declared["n"] + 1 == commissioned["n"]
    assert {key: declared[key] for key in ("id", "repo", "branch", "after", "slices")} == {
        "id": "B1", "repo": "proj-a", "branch": "work", "after": [],
        "slices": [{"id": "1a", "what": "the log", "minutes": 30},
                   {"id": "1b", "what": "the fold", "minutes": 20}]}
    assert (commissioned["stage"], commissioned["branch"]) == ("B1", "work")
    said = capsys.readouterr().out
    assert f"round {ROUND}   stage B1   flow fix\n" in said
    assert "slices: 1a the log (30 min); 1b the fold (20 min)" in said
    assert steps.one(states.fold(log.read(path)), "commission", "B1").state == steps.DONE


def test_a_slice_declared_over_the_limit_is_commissioned_with_a_warning(repo, opened,
                                                                       capsys):
    path = opened(stages=())

    assert run(ROUND, "--repo", "proj-a", "--branch", "work", "--builder", "coder",
               "--slice", "1a=the log", "--minutes", "45",
               "--slice", "1b=the fold", "--minutes", "50") == 0

    [declared] = events(path, "stage.declared")
    assert [one["minutes"] for one in declared["slices"]] == [45, 50]
    assert len(events(path, "build.commissioned")) == 1
    warnings = [line for line in capsys.readouterr().out.splitlines()
                if line.startswith("warning:")]
    assert warnings == [
        "warning: the slice 1b of B1 is given 50 minutes, over the 45 of slice_minutes: "
        "a worker gets one piece, well under an hour, and a brief for it is refused "
        "until it is cut"]


def test_a_stage_commissioned_by_its_id_warns_of_no_slice(repo, opened):
    opened()

    assert commissioning.commission(ROUND, "B1", builder="coder",
                                    actor="planner").oversized == ()


def test_the_stage_declared_takes_the_lowest_id_the_round_has_not_used(repo, opened):
    path = opened(stages=())
    log.append(path, "stage.declared", {"id": "B1", "round": "proj-a/child", "after": []},
               actor="planner")

    made = declare(repo="proj-a", branch="work")

    assert made.stage == "B2"
    assert made.block.lines[0] == f"round {ROUND}   stage B2   flow fix"


def test_declaring_in_a_flow_that_verifies_its_design_is_refused(repo, opened):
    opened(flow="full", stages=())

    [unmet] = refused_declaring(repo="proj-a", branch="work")

    assert unmet.reason == (f"the flow full of {ROUND} verifies its design, so each of "
                            "its stages is declared in the design and commissioned by "
                            "its id")


def test_declaring_while_the_repo_has_an_unbuilt_stage_names_that_stage(repo, opened):
    path = opened(stages=(("B1", "work"),))
    commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    drop_build(path)

    [unmet] = refused_declaring(repo="proj-a", branch="other")

    assert unmet.reason.startswith(f"B1 is a stage of {ROUND} in proj-a with no open or "
                                   "finished build")
    assert unmet.fix == f"moxi rounds commission {ROUND} B1 --builder coder"


def test_declaring_a_second_stage_in_a_repo_with_a_build_is_refused(repo, opened):
    opened(stages=())
    declare(repo="proj-a", branch="work")

    [unmet] = refused_declaring(repo="proj-a", branch="other")

    assert unmet.reason.startswith("B1 has a build in proj-a that is open or finished")


def test_a_question_on_every_stage_holds_the_stage_declaring_would_make(repo, opened):
    path = opened(stages=())
    ask_on(path, "all")
    before = path.read_bytes()

    [unmet] = refused_declaring(repo="proj-a", branch="work")

    assert unmet.reason == ("Q1 holds every stage, so it holds B1 the moment commission "
                            "declares it: Which name?")
    for way in (f"moxi rounds rule {ROUND} Q1 --text TEXT --record PTR",
                "withdraw:Q1", "park:Q1 --until WHAT", "narrow:Q1=LIST|none"):
        assert way in unmet.fix
    assert path.read_bytes() == before


def test_a_question_narrowed_off_the_stage_lets_it_be_declared(repo, opened):
    path = opened(stages=())
    ask_on(path, ["B5"])

    assert declare(repo="proj-a", branch="work").stage == "B1"


def test_a_rerun_of_a_stopped_declaring_commission_declares_one_stage(
        repo, opened, monkeypatch):
    path = opened(stages=())
    real = _stop_before_the_append(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        declare(repo="proj-a", branch="work")
    monkeypatch.setattr(write, "_append", real)

    made = declare(repo="proj-a", branch="work")

    assert made.adopted and made.stage == "B1"
    assert [one["id"] for one in events(path, "stage.declared")] == ["B1"]
    assert len(events(path, "build.commissioned")) == 1


@pytest.mark.parametrize("given, said", [
    ({"stage_id": "B1", "repo": "proj-a"}, "declare a stage of their own"),
    ({"repo": "proj-a"}, "needs both the repo and the branch"),
    ({"repo": "proj-a", "branch": "work", "slices": [("1 a", "x", 5)]}, "no slice id"),
    ({"repo": "proj-a", "branch": "work", "slices": [("1a", "x", 5), ("1a", "y", 5)]},
     "given twice"),
    ({"repo": "proj-a", "branch": "work", "slices": [("1a", " ", 5)]}, "says nothing"),
    ({"repo": "proj-a", "branch": "work", "slices": [("1a", "x", 0)]}, "whole number"),
])
def test_a_form_that_mixes_or_lacks_what_declaring_needs_is_bad_arguments(
        repo, opened, given, said):
    path = opened(stages=())
    before = path.read_bytes()

    with pytest.raises(BadArgument) as caught:
        commissioning.commission(ROUND, builder="coder", actor="planner", **given)

    assert said in caught.value.reason
    assert path.read_bytes() == before


def test_each_slice_needs_its_minutes(repo, opened, capsys):
    opened(stages=())

    assert run(ROUND, "--repo", "proj-a", "--branch", "work", "--builder", "coder",
               "--slice", "1a=the log") == 2

    assert "each slice has its minutes" in capsys.readouterr().err


# -- a stage commissioned again after a drop ---------------------------------------------

def test_a_dropped_build_s_stage_is_commissioned_again_under_its_id(repo, opened):
    path = opened()
    first = commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    drop_build(path)

    # The dropped build's worktree was kept, and its place is used once it is gone.
    [reason] = refused()
    assert f"{first.worktree} holds the worktree of the build {first.build}" in reason
    repo.git("worktree", "remove", "--force", str(first.worktree))
    [reason] = refused()
    assert "the branch work is in proj-a already" in reason

    again = commissioning.commission(ROUND, "B1", builder="coder", actor="planner",
                                     existing=True)

    assert again.build != first.build and again.stage == "B1"
    assert [one["build"] for one in events(path, "build.commissioned")] == [
        first.build, again.build]
    assert by_branch.find(again.worktree, verb="next").stage == "B1"


def test_a_dropped_build_s_stage_meets_the_hold_again_at_commission(repo, opened):
    path = opened()
    commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    ask_on(path, ["B1"])
    drop_build(path)
    repo.git("worktree", "remove", "--force",
             str(places.worktree_place({"worktree_dir": None}, "proj-a", "work")))
    repo.git("branch", "-D", "work")

    assert refused() == ["Q1 holds B1: Which name?"]
