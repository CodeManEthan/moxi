"""The lead's mark of a finding fixed, and when a build's verification counts at its tip:
the `verify-build` step, what `next` says of it and what the finish waits for."""

from __future__ import annotations

import pytest

from moxi_rounds import (checking, findings, fixes, log, next_step, opening, state,
                         steps, verdicts)
from moxi_rounds import settings as own
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Refused

ROUND = "proj-a/a-round"


@pytest.fixture
def built(git_repo, tmp_path, capsys):
    """`proj-a` rooted at the test's git repo, its round in the `fix` flow, and B1 on the
    branch `work` commissioned to `coder` with one commit made on it. Returns `(round
    folder, the repo, the worktree's repo)`."""
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
    return round_dir, git_repo, tree


def verify(round_dir, head, verdict="publishable", *, findings_=(), name="report.md"):
    """Record a report of B1 read at `head`, written as `name`, listing `findings_`,
    each `(id, severity, state)`."""
    keys = ['kind = "build"', f'verdict = "{verdict}"', 'pass = "full"', 'by = "critic"',
            'model = "a-model"', 'stage = "B1"', f'head = "{head}"']
    listed = "".join(f'\n\n[[finding]]\nid = "{id_}"\nseverity = "{severity}"\n'
                     f'state = "{state_}"\ntext = "a line is unclear"'
                     for id_, severity, state_ in findings_)
    path = round_dir / name
    path.write_text(f"# {name}\n\n```toml rounds\n" + "\n".join(keys) + listed
                    + "\n```\n", encoding="utf-8")
    return verdicts.record(ROUND, path, by="critic")


def fix(*commits, key="B1.L1", no_behaviour_change=True):
    return fixes.fix(ROUND, key, list(commits), no_behaviour_change=no_behaviour_change,
                     by="planner")


def folded(round_dir):
    return state.fold(log.read(log.round_log(round_dir)))


def raw(round_dir) -> bytes:
    return log.round_log(round_dir).read_bytes()


def refusal(*commits, **more) -> str:
    with pytest.raises(Refused) as refused:
        fix(*commits, **more)
    return str(refused.value)


def verify_build(round_dir) -> steps.Step:
    """B1's verify-build step as git reads the build now."""
    now = folded(round_dir)
    seen = checking.seen_of(now, ROUND, settings=own.load())
    return steps.one(now, "verify-build", "B1", name=ROUND, seen=seen)


# -- the mark -----------------------------------------------------------------------------

def test_a_minor_finding_marked_fixed_is_one_event_naming_the_fix_s_commits(built):
    round_dir, _repo, tree = built
    verify(round_dir, tree.rev(), "fold-first", findings_=[("L1", "minor", "open")])
    made = tree.commit("fix.txt", "the fix")

    done = fix(made[:8])

    [event] = [one for one in log.read(log.round_log(round_dir))
               if one["type"] == "finding.marked"]
    assert {key: event[key] for key in ("id", "state", "commits")} == {
        "id": "B1.L1", "state": "fixed", "commits": [made]}
    assert (done.finding.state, done.finding.set_by) == ("fixed", event["n"])
    [held] = findings.of_stage(folded(round_dir), "B1")
    assert (held.state, held.severity, held.step) == ("fixed", "minor", "verify-build")


@pytest.mark.parametrize("severity", ["blocker", "major"])
def test_a_blocker_or_a_major_is_not_marked_fixed_without_a_recheck(built, severity):
    round_dir, _repo, tree = built
    verify(round_dir, tree.rev(), "fold-first", findings_=[("L1", severity, "open")])
    made = tree.commit("fix.txt", "the fix")
    before = raw(round_dir)

    said = refusal(made)

    assert f"B1.L1 is a {severity} finding" in said
    assert "re-check the fix's diff" in said
    assert raw(round_dir) == before


def test_a_mark_without_the_no_behaviour_change_statement_is_refused(built):
    round_dir, _repo, tree = built
    verify(round_dir, tree.rev(), "fold-first", findings_=[("L1", "note", "open")])
    made = tree.commit("fix.txt", "the fix")

    said = refusal(made, no_behaviour_change=False)

    assert "does not state that the fix changes no behaviour" in said
    assert "--no-behaviour-change" in said


def test_a_finding_the_build_does_not_hold_or_a_stage_with_no_build_is_refused(built):
    round_dir, _repo, tree = built
    verify(round_dir, tree.rev(), "fold-first", findings_=[("L1", "minor", "open")])
    made = tree.commit("fix.txt", "the fix")

    assert "B1's build holds no finding L2" in refusal(made, key="B1.L2")
    assert "B2 of proj-a/a-round has no open build" in refusal(made, key="B2.L1")


def test_a_commit_that_is_no_commit_of_the_build_is_refused(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev(), "fold-first", findings_=[("L1", "minor", "open")])
    base = repo.rev("main")
    repo.switch("elsewhere", new_from="main")
    stray = repo.commit("stray.txt")
    repo.switch("main")

    said = refusal(stray, base, "f" * 40)

    assert f"{stray} is not a commit of B1" in said
    assert f"{base} is not a commit of B1" in said
    assert f"{'f' * 40} is no commit proj-a holds" in said


@pytest.mark.parametrize("key", ["B1", "L1", "B1.W1", "b1.l1"])
def test_a_key_that_names_no_finding_is_a_bad_argument(built, key):
    with pytest.raises(BadArgument):
        fix("a" * 40, key=key)


def test_the_command_marks_a_finding_and_says_what_it_needs(built, capsys):
    round_dir, _repo, tree = built
    verify(round_dir, tree.rev(), "fold-first", findings_=[("L1", "minor", "open")])
    made = tree.commit("fix.txt", "the fix")
    capsys.readouterr()

    assert main(["mark", ROUND, "B1.L1", "--fixed", "--commit", made]) == 3
    assert "changes no behaviour" in capsys.readouterr().err
    assert main(["mark", ROUND, "B1.L1", "--fixed", "--no-behaviour-change"]) == 2
    assert main(["mark", ROUND, "Q1", "--reopen", "--commit", made]) == 2
    capsys.readouterr()

    assert main(["mark", ROUND, "B1.L1", "--fixed", "--commit", made,
                 "--no-behaviour-change"]) == 0
    assert f"finding B1.L1: minor, fixed   commits: {made[:12]}" in (
        capsys.readouterr().out)


def test_a_later_report_sets_a_marked_finding_again(built):
    round_dir, _repo, tree = built
    verify(round_dir, tree.rev(), "fold-first", findings_=[("L1", "minor", "open")])
    made = tree.commit("fix.txt", "the fix")
    fix(made)

    verify(round_dir, made, "fold-first", findings_=[("L1", "minor", "open")],
           name="recheck.md")

    [held] = findings.of_stage(folded(round_dir), "B1")
    assert held.state == "open"


# -- when verification counts at a tip ---------------------------------------------------

def test_verify_build_is_ready_with_work_and_no_report(built):
    round_dir, _repo, _tree = built

    found = verify_build(round_dir)

    assert found.state == steps.READY
    assert found.owes == ("no report of B1 is recorded",)


def test_verify_build_is_done_once_a_publishable_report_read_the_tip(built):
    round_dir, _repo, tree = built
    verify(round_dir, tree.rev(), "fold-first")
    assert verify_build(round_dir).state == steps.READY

    verify(round_dir, tree.rev(), name="recheck.md")

    assert verify_build(round_dir).state == steps.DONE


def test_a_commit_after_the_verdict_owes_a_recheck_until_a_lead_mark_names_it(built):
    round_dir, _repo, tree = built
    read_at = tree.rev()
    verify(round_dir, read_at, "fold-first", findings_=[("L1", "minor", "open")])
    verify(round_dir, read_at, findings_=[("L1", "minor", "open")], name="second.md")
    assert verify_build(round_dir).state == steps.DONE
    made = tree.commit("fix.txt", "the fix")

    owed = verify_build(round_dir)

    assert owed.state == steps.READY
    assert f"reading {read_at}..{made}" in owed.owes[0]

    fix(made)

    assert verify_build(round_dir).state == steps.DONE


def test_a_merge_of_the_target_apart_from_the_build_needs_no_recheck(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.commit("other.txt")
    tree.merge("main")

    assert verify_build(round_dir).state == steps.DONE


def test_a_merge_of_the_target_that_meets_the_build_owes_a_recheck(built):
    round_dir, repo, tree = built
    verify(round_dir, tree.rev())
    repo.commit("uv.lock", "a lock")
    tree.merge("main")

    assert verify_build(round_dir).state == steps.READY


def test_next_says_what_the_verification_reads_and_owes(built):
    round_dir, _repo, tree = built
    read_at = tree.rev()
    verify(round_dir, read_at, "fold-first")
    tree.commit("fix.txt", "the fix")

    [group] = [one for one in next_step.read(ROUND)["groups"]
               if one["step"] == "verify-build"]
    text = next_step.as_text(next_step.read(ROUND))

    assert group["reads"][0].startswith("the diff ") and group["reads"][0].endswith(
        "..work")
    assert group["then"] == [f"moxi rounds verdict {ROUND} <your report>"]
    assert group["tier"] == "third"
    assert f"owes:  the newest report of B1, at {read_at[:7]}, is fold-first" in text


def test_what_finish_waits_for_names_the_verification_owed(built):
    round_dir, _repo, tree = built

    done = verify(round_dir, tree.rev(), "fold-first")

    assert any(line.startswith("gate 1, verification: the verification of B1 does not "
                               "count at ")
               and line.endswith("is fold-first: a re-check is owed")
               for line in done.waits)
    assert not any("not-built" in line for line in done.waits)
