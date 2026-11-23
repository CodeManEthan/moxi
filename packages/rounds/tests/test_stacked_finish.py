"""Two open builds whose branches share commits, read end to end: a build whose range is
strictly inside another's finishes first and the outer one is held until that work is
on the target; an outer build merged or fast-forwarded by hand finishes first and the
inner one is held until that finish records the landing; overlapping and equal ranges
are refused. Under the finish lock as well, and in show, list and the drop refusal."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from moxi_rounds import (api, builds, checking, commissioning, dropping, finish_gates,
                         finish_lock, finishing, log, opening, verdicts)
from moxi_rounds import settings as own
from moxi_rounds import state as states
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

INNER = "proj-a/inner-round"
OUTER = "proj-a/outer-round"
CHECK = shlex.join([sys.executable, "-c", "print('the check passed')"])
POINTER = "comms:1234567"


@pytest.fixture
def repo(git_repo, tmp_path, write_config):
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    git_repo.git("config", "user.name", "coder")
    git_repo.git("config", "user.email", "coder@example.test")
    # The check passes at the tip alone, so the outer build needs no merge of the target
    # once the inner one has landed.
    write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(CHECK)} }}\n"
                 'check_on = "tip"\n')
    return git_repo


class Built:
    """A round in `fix` with B1 commissioned on `branch`: its `name`, its log `path`
    and its worktree `tree`."""

    def __init__(self, repo, tmp_path: Path, name: str, branch: str):
        self.name = name
        opening.open_round(name, description="a placeholder round", by="planner",
                           flow="fix")
        self.path = log.round_log(tmp_path / "notes" / "rounds" / name.split("/")[1])
        log.append(self.path, "stage.declared",
                   {"id": "B1", "repo": "proj-a", "branch": branch, "after": []},
                   by="planner")
        made = commissioning.commission(name, "B1", builder="coder", by="planner")
        self.tree = type(repo)(made.worktree)

    def ready(self) -> None:
        """Check B1 and record a publishable build report read at its tip."""
        checking.check(self.name, "B1", by="coder")
        report = self.path.parent / "build-report.md"
        report.write_text("# report\n\n```toml rounds\n" + "\n".join([
            'kind = "build"', 'verdict = "publishable"', 'pass = "full"',
            'by = "critic"', 'model = "a-model"', 'stage = "B1"',
            f'head = "{self.tree.rev()}"']) + "\n```\n", encoding="utf-8")
        verdicts.record(self.name, report, by="critic")

    def events(self, kind: str) -> list[dict]:
        return [one for one in log.read_round(self.path) if one["type"] == kind]

    def gates(self) -> list[finish_gates.Gate]:
        state = states.fold(log.read_round(self.path))
        seen = checking.seen_of(state, self.name, settings=own.load())["B1"]
        return list(finish_gates.unmet(state, "B1", seen=seen, name=self.name))

    def sharing(self) -> list[str]:
        return [one.unmet.reason for one in self.gates()
                if one.number == finish_gates.SHARING]

    def build(self) -> builds.Build:
        return builds.current(states.fold(log.read_round(self.path)), "B1")


def refused(name: str) -> list[str]:
    with pytest.raises(Refused) as caught:
        finishing.finish(name, "B1", by="coder")
    return [one.reason for one in caught.value.unmet]


def read_now(name: str) -> dict:
    (found,) = [one for one in api.builds()["builds"] if one["round"] == name]
    return found


HELD_FOR_WORK = (f"B1 is held until {INNER} B1's work is on the target, which its "
                 "finish does")
HELD_FOR_LANDING = (f"B1 is held until {OUTER} B1's finish records the landing that "
                    "brought B1's work onto the target")


@pytest.fixture
def stacked(repo, tmp_path, capsys):
    """The inner build with one commit on `inner`, and the outer build on `outer` with
    that commit and one of its own; both checked and verified at their tips."""
    inner = Built(repo, tmp_path, INNER, "inner")
    outer = Built(repo, tmp_path, OUTER, "outer")
    inner.tree.commit("inner.txt", "the inner work")
    outer.tree.git("reset", "-q", "--hard", inner.tree.rev())
    outer.tree.commit("outer.txt", "the outer work")
    inner.ready()
    outer.ready()
    capsys.readouterr()
    return inner, outer


def test_the_inner_build_finishes_first_and_the_outer_is_held_until_its_work_lands(
        stacked, repo):
    inner, outer = stacked
    assert read_now(INNER)["shares"] == [
        {"round": OUTER, "stage": "B1", "relation": "inside"}]
    assert read_now(OUTER)["shares"] == [
        {"round": INNER, "stage": "B1", "relation": "contains"}]
    assert inner.sharing() == []
    assert outer.sharing() == [HELD_FOR_WORK]
    before = repo.rev("main")

    assert refused(OUTER) == [HELD_FOR_WORK]
    assert outer.events("finish.action") == [] and repo.rev("main") == before

    finishing.finish(INNER, "B1", by="coder")

    assert inner.build().ended == builds.FINISHED
    assert read_now(OUTER)["shares"] == []
    assert outer.gates() == []
    finishing.finish(OUTER, "B1", by="coder")
    assert outer.build().ended == builds.FINISHED
    assert repo.git("show", "main:outer.txt") == "the outer work"


def test_show_list_and_the_drop_refusal_word_each_share_by_its_relation(stacked,
                                                                         capsys):
    inner, outer = stacked
    assert main(["show", INNER]) == 0
    said = capsys.readouterr().out
    assert f"building, idle, inside {OUTER} B1 (finishes first)" in said
    assert main(["show", OUTER]) == 0
    assert (f"building, idle, contains {INNER} B1 (held until its work is on the target)"
            in capsys.readouterr().out)
    assert main(["list", "--waiting"]) == 0
    said = capsys.readouterr().out
    assert f"building, idle, inside {OUTER} B1 (finishes first)" in said
    assert (f"building, idle, contains {INNER} B1 (held until its work is on the target)"
            in said)
    with pytest.raises(Refused) as caught:
        dropping.drop_build(INNER, "B1", by="lead", record=POINTER, run_by="planner")
    assert f"inside {OUTER} B1 (finishes first)" in caught.value.unmet[0].reason


def test_an_outer_build_that_takes_the_lock_while_its_inner_build_is_open_is_held(
        repo, tmp_path, capsys, monkeypatch):
    inner = Built(repo, tmp_path, INNER, "inner")
    outer = Built(repo, tmp_path, OUTER, "outer")
    first = outer.tree.commit("outer-1.txt", "the first of the outer work")
    outer.tree.commit("outer-2.txt", "the rest of the outer work")
    inner.tree.commit("inner.txt", "work of its own")
    outer.ready()
    capsys.readouterr()
    assert outer.gates() == []
    before = repo.rev("main")
    take = finish_lock.take

    def take_then_stack(*args, **kwargs):
        taken = take(*args, **kwargs)
        inner.tree.git("reset", "-q", "--hard", first)
        return taken

    monkeypatch.setattr(finish_lock, "take", take_then_stack)
    assert refused(OUTER) == [HELD_FOR_WORK]
    assert outer.events("finish.action") == [] and repo.rev("main") == before
    assert finish_lock.found(repo.root, "refs/rounds/finishing/main") is None


def test_an_outer_build_that_takes_the_lock_once_its_inner_build_landed_passes(
        repo, tmp_path, capsys, monkeypatch):
    inner = Built(repo, tmp_path, INNER, "inner")
    outer = Built(repo, tmp_path, OUTER, "outer")
    first = outer.tree.commit("outer-1.txt", "the first of the outer work")
    outer.tree.commit("outer-2.txt", "the rest of the outer work")
    inner.tree.commit("inner.txt", "work of its own")
    outer.ready()
    capsys.readouterr()
    take = finish_lock.take

    def take_then_land_the_inner(*args, **kwargs):
        taken = take(*args, **kwargs)
        inner.tree.git("reset", "-q", "--hard", first)
        repo.merge("inner")
        return taken

    monkeypatch.setattr(finish_lock, "take", take_then_land_the_inner)
    finishing.finish(OUTER, "B1", by="coder")
    assert outer.build().ended == builds.FINISHED


def _ends_by_a_lead_drop(inner: Built, why: str) -> None:
    """The inner build reads empty with nothing of its own and `why`, is refused at
    the work gate, and the lead drops it."""
    now = read_now(INNER)
    assert (now["state"], now["by"], now["shares"]) == (builds.EMPTY,
                                                        builds.NOTHING_OWN, [])
    assert why in now["why"]
    assert any(f"it reads {builds.EMPTY}" in reason for reason in refused(INNER))
    assert inner.events("finish.action") == []
    dropping.drop_build(INNER, "B1", by="lead", record=POINTER, run_by="planner")
    assert inner.build().ended == builds.DROPPED


def test_an_outer_build_merged_by_hand_finishes_first_and_the_inner_ends_by_a_drop(
        stacked, repo):
    inner, outer = stacked
    repo.merge("outer")
    assert (read_now(INNER)["state"], read_now(OUTER)["state"]) == (builds.MERGED,
                                                                    builds.MERGED)
    assert read_now(INNER)["shares"][0]["relation"] == builds.INSIDE
    assert inner.sharing() == [HELD_FOR_LANDING]
    assert outer.sharing() == []

    assert refused(INNER) == [HELD_FOR_LANDING]
    assert inner.events("finish.action") == []

    finishing.finish(OUTER, "B1", by="coder")
    assert outer.build().ended == builds.FINISHED
    _ends_by_a_lead_drop(inner, "came onto main by another build's finish")


def test_an_outer_build_fast_forwarded_by_hand_finishes_first_and_the_inner_ends_by_a_drop(
        stacked, repo):
    inner, outer = stacked
    repo.git("merge", "-q", "--ff-only", "outer")
    assert (read_now(INNER)["state"], read_now(OUTER)["state"]) == (builds.MERGED,
                                                                    builds.MERGED)
    assert inner.sharing() == [HELD_FOR_LANDING]
    assert outer.sharing() == []

    assert refused(INNER) == [HELD_FOR_LANDING]

    finishing.finish(OUTER, "B1", by="coder")
    assert outer.build().ended == builds.FINISHED
    _ends_by_a_lead_drop(inner, "came by other builds' finishes")


@pytest.mark.parametrize("equal", [True, False])
def test_overlapping_and_equal_ranges_refuse_both_builds(repo, tmp_path, capsys, equal):
    one = Built(repo, tmp_path, INNER, "inner")
    other = Built(repo, tmp_path, OUTER, "outer")
    shared = one.tree.commit("shared.txt", "work on both branches")
    other.tree.git("reset", "-q", "--hard", shared)
    if not equal:
        one.tree.commit("one.txt", "work of the first")
        other.tree.commit("other.txt", "work of the second")
    one.ready()
    other.ready()
    capsys.readouterr()

    for built, against in ((one, OUTER), (other, INNER)):
        assert read_now(built.name)["shares"][0]["relation"] == builds.OVERLAPS
        said = f"B1 shares commits with {against} B1; a build finishes only with work"
        assert [reason.startswith(said) for reason in refused(built.name)] == [True]
        assert built.events("finish.action") == []
