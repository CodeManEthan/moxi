"""Reports of a step of the user's own: `verdict` records one under its step's name, its
findings are the build's, kept with the step that raised them, a step's report sets
only its own step's findings, and the step is done at the tip its own reports count
at."""

from __future__ import annotations

import pytest

from moxi_rounds import checking, findings, log, opening, state, steps, verdicts
from moxi_rounds import settings as own
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
REVIEW = "security-review"

FLOW = """
    [flows.mine]
    steps = [
      { step = "build", by = "builder" },
      { step = "verify-build", by = "checker" },
      { step = "security-review", does = "report", before = "finish", by = "checker" },
      { step = "staging", does = "command", before = "finish", argv = ["true"] },
      { step = "finish", by = "builder" },
    ]
"""


@pytest.fixture
def built(git_repo, tmp_path, write_config, capsys):
    """`proj-a` rooted at the test's git repo, its round in a flow with the report step
    `security-review`, and B1 on the branch `work` commissioned to `coder` with one
    commit made on it. Returns `(round folder, the worktree's repo)`."""
    from moxi import cli
    from moxi_rounds import commissioning

    write_config(FLOW)
    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="mine")
    round_dir = tmp_path / "notes" / "rounds" / "a-round"
    log.append(log.round_log(round_dir), "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    tree = type(git_repo)(made.worktree)
    tree.commit("work.txt", "the work")
    capsys.readouterr()
    return round_dir, tree


def report(round_dir, head, *listed, step=REVIEW, name="review.md", verdict="publishable",
           by="critic"):
    """Write a report read at `head` listing the findings `listed`, each `(id,
    severity, state)`: a report of the step `step`, or of the build with `step` None.
    Returns its path."""
    kind = 'kind = "build"' if step is None else f'kind = "step"\nstep = "{step}"'
    keys = [kind, f'verdict = "{verdict}"', 'pass = "full"', f'by = "{by}"',
            'model = "a-model"', 'stage = "B1"', f'head = "{head}"']
    more = "".join(f'\n\n[[finding]]\nid = "{id_}"\nseverity = "{severity}"\n'
                   f'state = "{state_}"\ntext = "a finding of {id_}"'
                   for id_, severity, state_ in listed)
    path = round_dir / name
    path.write_text(f"# {name}\n\n```toml rounds\n" + "\n".join(keys) + more + "\n```\n",
                    encoding="utf-8")
    return path


def verify(round_dir, head, *listed, **more):
    return verdicts.record(ROUND, report(round_dir, head, *listed, **more), by="critic")


def refused(round_dir, head, *listed, **more) -> str:
    before = log.round_log(round_dir).read_bytes()
    with pytest.raises(Refused) as error:
        verify(round_dir, head, *listed, **more)
    assert log.round_log(round_dir).read_bytes() == before
    return str(error.value)


def folded(round_dir):
    return state.fold(log.read(log.round_log(round_dir)))


def step_of(round_dir, name=REVIEW) -> steps.Step:
    found = folded(round_dir)
    return steps.one(found, name, "B1", name=ROUND,
                     seen=checking.seen_of(found, ROUND, settings=own.load()))


# -- recorded under its step's name ---------------------------------------------------------

def test_a_step_report_is_recorded_under_its_step_and_its_findings_keep_the_step(built):
    round_dir, tree = built

    done = verify(round_dir, tree.rev(), ("L1", "minor", "open"), verdict="fold-first")

    event = done.event
    assert (event["kind"], event["step"], event["stage"], event["head"]) == (
        "step", REVIEW, "B1", tree.rev())
    assert done.verdict.step == REVIEW
    [found] = findings.of_stage(folded(round_dir), "B1")
    assert (found.key, found.step, found.state) == ("B1.L1", REVIEW, "open")
    assert "gate 2, findings: finding B1.L1: minor, open: a finding of L1" in done.waits


def test_a_build_report_has_no_step_field(built):
    round_dir, tree = built

    done = verify(round_dir, tree.rev(), step=None, name="build.md")

    assert "step" not in done.event
    assert done.verdict.step is None


def test_a_report_for_a_step_the_flow_has_no_report_step_of_is_refused(built):
    round_dir, tree = built

    for name in ("audit", "staging"):
        said = refused(round_dir, tree.rev(), step=name, name=f"{name}.md")
        assert (f"the report is for the step {name}, and the flow mine of {ROUND} has "
                f'no step {name} with does = "report"') in said


def test_the_builder_s_own_step_report_is_refused(built):
    round_dir, tree = built

    said = refused(round_dir, tree.rev(), by="coder")

    assert "the report is by coder, who built B1" in said


# -- a step's report sets only its own step's findings ---------------------------------------

def test_a_step_report_that_lists_another_report_s_finding_is_refused(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("L1", "minor", "open"), step=None, name="build.md",
           verdict="fold-first")

    said = refused(round_dir, tree.rev(), ("L1", "minor", "fixed"))

    assert ("the finding B1.L1 was raised by a report of verify-build, and this report "
            "of the step security-review lists it") in said


def test_a_step_report_need_not_list_a_finding_another_report_raised(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("L1", "minor", "open"), step=None, name="build.md",
           verdict="fold-first")

    verify(round_dir, tree.rev())

    [found] = findings.of_stage(folded(round_dir), "B1")
    assert (found.key, found.step, found.state) == ("B1.L1", "verify-build", "open")


def test_a_step_report_that_leaves_out_its_own_step_s_open_finding_is_refused(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("L1", "minor", "open"), verdict="fold-first")

    said = refused(round_dir, tree.rev(), name="again.md")

    assert ("the finding B1.L1 is open on B1's record and the report does not list "
            "it") in said


def test_the_build_report_lists_and_sets_a_step_s_open_finding(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("L1", "minor", "open"), verdict="fold-first")

    said = refused(round_dir, tree.rev(), step=None, name="build.md")
    assert "the finding B1.L1 is open on B1's record" in said

    verify(round_dir, tree.rev(), ("L1", "minor", "fixed"), step=None, name="build-2.md")

    [found] = findings.of_stage(folded(round_dir), "B1")
    assert (found.step, found.state) == (REVIEW, "fixed")


def test_a_new_finding_of_a_step_report_takes_an_id_no_report_of_the_stage_used(
        built, git_repo):
    from moxi_rounds import commissioning

    round_dir, tree = built
    verify(round_dir, tree.rev(), ("L1", "minor", "open"), verdict="fold-first")
    log.append(log.round_log(round_dir), "build.dropped", {"stage": "B1"}, by="planner")
    git_repo.git("worktree", "remove", "--force", str(tree.root))
    git_repo.git("branch", "-D", "work")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    again = type(git_repo)(made.worktree)
    again.commit("work.txt", "the work again")

    said = refused(round_dir, again.rev(), ("L1", "minor", "open"), name="again.md")

    assert "an earlier report of B1 used the id L1" in said


# -- the step's own row --------------------------------------------------------------------

def test_the_report_step_is_done_once_its_own_report_counts_at_the_tip(built):
    round_dir, tree = built
    found = step_of(round_dir)
    assert found.state == steps.READY
    assert found.owes == ("no report of the step security-review of B1 is recorded",)

    verify(round_dir, tree.rev(), step=None, name="build.md")
    assert step_of(round_dir).state == steps.READY
    assert step_of(round_dir, "verify-build").state == steps.DONE

    verify(round_dir, tree.rev())
    assert step_of(round_dir).state == steps.DONE

    tree.commit("more.txt")
    found = step_of(round_dir)
    assert found.state == steps.READY
    [owed] = found.owes
    assert "1 commit since the verdict" in owed and "a re-check" in owed


def test_a_step_report_does_not_verify_the_build(built):
    round_dir, tree = built

    verify(round_dir, tree.rev())

    assert step_of(round_dir).state == steps.DONE
    assert step_of(round_dir, "verify-build").state == steps.READY


def test_a_fold_first_step_report_leaves_the_step_owing_a_re_check(built):
    round_dir, tree = built

    verify(round_dir, tree.rev(), ("L1", "minor", "open"), verdict="fold-first")

    found = step_of(round_dir)
    assert found.state == steps.READY
    assert found.owes == (f"the newest report of the step security-review of B1, at "
                          f"{tree.rev()[:7]}, is fold-first: a re-check is owed",)


def test_verdict_names_the_report_step_among_what_finish_waits_for(built):
    round_dir, tree = built

    done = verify(round_dir, tree.rev(), step=None, name="build.md")

    assert any(line.startswith("gate 8, user steps: the step security-review of B1 is "
                               "not done at ")
               and line.endswith(": no report of the step security-review of B1 is "
                                 "recorded")
               for line in done.waits)


def test_next_prints_the_report_step_s_schema_and_verdict(built, capsys):
    from moxi_rounds.cli import main

    round_dir, tree = built
    verify(round_dir, tree.rev(), step=None, name="build.md")

    assert main(["next", ROUND]) == 0
    out = capsys.readouterr().out

    assert "security-review" in out
    assert "moxi rounds next --schema step, its step 'security-review'" in out
    assert f"moxi rounds verdict {ROUND} <your report>" in out
