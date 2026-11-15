"""The commission block: what it holds, the rulings it gives as they stand, what the
lead is told beside it, and what `next` says changed for a build since its block."""

from __future__ import annotations

import pytest

from moxi_rounds import (commission_block, freezing, log, next_step, opening, ruling,
                         state, tracing)
from moxi_rounds.cli import main
from moxi_rounds.errors import NoLog

ROUND = "proj-a/a-round"
SAID = "Times are shown in the reader's zone."
PTR = "comms:1234567"


@pytest.fixture
def repo(git_repo, tmp_path):
    """The project `proj-a`, whose root is the test's git repo with `main` checked
    out, keeping its rounds under a notes folder of its own."""
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    return git_repo


@pytest.fixture
def round_dir(repo, tmp_path, capsys):
    """`a-round` opened in the full flow; returns its folder."""
    opening.open_round(ROUND, description="a placeholder round", by="planner",
                       flow="full")
    capsys.readouterr()
    return tmp_path / "notes" / "rounds" / "a-round"


def rule(item=None, target=ROUND, text=SAID, **more):
    if item is not None and item[0] not in "QP":
        text = None
    return ruling.rule(target, item=item, record=PTR, by="planner", text=text,
                       **more).id


def row(id_, stages=None) -> str:
    lines = ["[[trace]]", f'id = "{id_}"', 'as = "carried"', 'where = "the index"']
    if stages is not None:
        lines.append("stages = [" + ", ".join(f'"{one}"' for one in stages) + "]")
    return "\n".join(lines)


STAGE = "\n".join(['[[stage]]', 'id = "B1"', 'repo = "proj-a"', 'branch = "work"',
                   "after = []", "", "  [[stage.slice]]", '  id = "1a"',
                   '  what = "the log and its fold"', "  minutes = 40", "",
                   '[[stage]]', 'id = "B2"', 'repo = "proj-a"', 'branch = "other"',
                   'after = ["B1"]', 'why = "it lands on its own"'])


def fold(round_dir, *rows):
    text = ("# A design\n\n```toml rounds\n" + "\n\n".join([STAGE, *rows])
            + "\n```\n")
    (round_dir / "design.md").write_text(text, encoding="utf-8")
    freezing.freeze(ROUND, by="scout")


def commission(capsys) -> str:
    assert main(["commission", ROUND, "B1", "--builder", "coder"]) == 0
    return capsys.readouterr().out


def ready(round_dir, *rows):
    """Freeze the design with `rows` and skip its verification, so B1 may be
    commissioned."""
    fold(round_dir, *rows)
    rule("skip:verify-design")


def folded(round_dir):
    return state.fold(log.read(log.round_log(round_dir)))


def register(round_dir):
    try:
        return state.fold(log.read(log.project_log(round_dir.parent)))
    except NoLog:
        return state.State()


# -- what the block holds ----------------------------------------------------------------

def test_the_block_says_the_build_its_dirs_commands_and_who_finishes_after_what(
        round_dir, repo, capsys):
    ready(round_dir)

    said = commission(capsys).splitlines()

    base = repo.rev("main")[:7]
    assert said[:2] == [f"round {ROUND}   stage B1   flow full",
                        f"repo proj-a   branch work   base {base}   target main"]
    assert said[4] == "         (logs and probes only: removed when the build ends)"
    assert said[5:] [:6] == [
        "you run two commands, from the worktree: moxi rounds next, moxi rounds finish",
        "a question for the operator: tell your lead",
        "finish: merge into main, by builder, after its verification is recorded "
        "publishable by the checker.",
        "        You do not record a verdict or mark a finding.",
        "reads: nothing named",
        "rulings this build follows: none"]
    assert "slices: 1a the log and its fold (40 min)" in said
    assert any(line.startswith("skipped by the operator: verify-design (C") for line in said)


def test_a_declared_check_and_the_repos_brief_lines_reach_the_block(
        round_dir, write_config, capsys):
    write_config("""\
        [projects.proj-a]
        check = { command = "true", timeout_s = 60 }
        brief = ["Keep test data made up.", "Run tests bounded."]
        """)
    ready(round_dir)

    said = commission(capsys).splitlines()

    assert ("you run three commands, from the worktree: moxi rounds next, "
            "moxi rounds check, moxi rounds finish") in said
    assert any(line.startswith("finish: merge into main, by builder, after its "
                               "verification is recorded publishable by the checker and "
                               "the check passes at the tip.") for line in said)
    blank = said.index("")
    assert said[blank - 2:blank] == ["Keep test data made up.", "Run tests bounded."]


# -- the rulings it gives ----------------------------------------------------------------

def test_the_block_gives_the_live_text_of_a_replaced_ruling_and_leaves_out_a_revoked_one(
        round_dir, capsys):
    first = rule()
    other = rule(text="Rows are sorted by name.")
    ready(round_dir, row(first), row(other))
    later = rule(text="Times are shown in UTC.", supersedes=first)
    rule(f"revoke:{other}")

    said = commission(capsys)

    assert f"  {later} replaces {first}  Times are shown in UTC. ({PTR})\n" in said
    assert SAID not in said and "Rows are sorted by name." not in said


def test_with_the_trace_check_on_the_block_gives_the_replacement_a_fold_cites(
        round_dir, write_config, capsys):
    write_config("""\
        [projects.proj-a]
        trace = true
        """)
    first = rule()
    later = rule(text="Times are shown in UTC.", supersedes=first)
    ready(round_dir, row(first), row(later))

    said = commission(capsys)

    assert f"  {later} replaces {first}  Times are shown in UTC. ({PTR})\n" in said
    assert SAID not in said


def test_a_row_for_another_stage_gives_its_ruling_to_that_stage_only(round_dir, capsys):
    first = rule()
    ready(round_dir, row(first, stages=["B2"]))

    assert "rulings this build follows: none\n" in commission(capsys)


def test_the_lead_is_told_each_unshown_ruling_and_the_build_is_commissioned_anyway(
        round_dir, tmp_path, capsys):
    first = rule()
    standing = rule(target="proj-a", text="Logs are kept a year.", standing=True)
    ready(round_dir, row(first), row(standing))
    opening.open_round("proj-a/b-round", description="a placeholder round",
                       by="planner", flow="fix")

    said = commission(capsys)

    assert (f"not yet shown to the operator: {ROUND} {first} {SAID}; "
            f"proj-a {standing} Logs are kept a year.\n") in said
    assert f"  then: moxi rounds mark {ROUND} {first} --shown --record PTR\n" in said
    assert f"  then: moxi rounds mark proj-a {standing} --shown --record PTR\n" in said
    events = log.read(log.round_log(round_dir))
    assert [one["type"] for one in events].count("build.commissioned") == 1


def test_a_ruling_marked_shown_is_not_listed_for_the_lead(round_dir, capsys):
    first = rule()
    ready(round_dir, row(first))
    assert main(["mark", ROUND, first, "--shown", "--record", "comms:2345678"]) == 0
    capsys.readouterr()

    assert "not yet shown" not in commission(capsys)


def test_a_standing_ruling_in_the_repos_scope_that_no_row_cites_is_named_to_the_lead(
        round_dir, capsys):
    scoped = rule(target="proj-a", text="Logs are kept a year.", standing=True,
                  scope="proj-a")
    rule(target="proj-a", text="Pages load in a second.", standing=True,
         scope="proj-b")
    ready(round_dir)

    said = commission(capsys)

    assert f"not cited, check: {scoped} Logs are kept a year.\n" in said
    assert "Pages load in a second." not in said


# -- what changed since the block --------------------------------------------------------

def test_next_tells_a_build_each_correction_recorded_since_its_block(round_dir, capsys):
    first, other = rule(), rule(text="Rows are sorted by name.")
    ready(round_dir, row(first), row(other))
    commission(capsys)
    later = rule(text="Times are shown in UTC.", supersedes=first)
    taken = rule(f"revoke:{other}")
    new = rule(text="Lists show twenty rows.")
    elsewhere = rule(text="Only B2 pages scroll.", stages="B2")

    found = next_step.read(ROUND)
    said = next_step.as_text(found)

    assert (f"changed since your block: {later} replaces {first}: "
            "Times are shown in UTC.\n") in said
    assert (f"changed since your block: {taken} takes back {other}: "
            "Rows are sorted by name.\n") in said
    assert f"changed since your block: {new}: Lists show twenty rows.\n" in said
    assert f"changed since your block: {elsewhere}" not in said
    [group] = [one for one in found["groups"] if one["stage"] == "B1"]
    assert [one["id"] for one in group["changed"]] == [later, taken, new]


def test_nothing_recorded_before_the_block_is_told_as_changed(round_dir, capsys):
    first = rule()
    ready(round_dir, row(first))
    commission(capsys)

    found = next_step.read(ROUND)

    [group] = [one for one in found["groups"] if one["stage"] == "B1"]
    assert group["changed"] == []
    assert "changed since your block" not in next_step.as_text(found)


def test_a_correction_of_a_standing_ruling_the_block_gave_is_told(round_dir, capsys):
    # Recorded a second before the block, as a register ruling of the block's own
    # second is told in case it came after.
    standing = rule(target="proj-a", text="Logs are kept a year.", standing=True,
                    at="2026-01-02T03:04:05Z")
    ready(round_dir, row(standing))
    commission(capsys)
    later = rule(target="proj-a", text="Logs are kept a month.", standing=True,
                 supersedes=standing)

    changed = commission_block.changed(folded(round_dir), register(round_dir), "B1")

    assert [(one.ruling.id, one.replaces) for one in changed] == [(later, standing)]


def test_a_stage_with_no_open_build_has_nothing_changed(round_dir):
    first = rule()
    ready(round_dir, row(first))
    rule(text="Times are shown in UTC.", supersedes=first)

    assert commission_block.changed(folded(round_dir), register(round_dir), "B1") == ()
    assert tracing.given(folded(round_dir), register(round_dir), "B1")[0].replaces == first
