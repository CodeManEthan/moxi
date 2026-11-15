"""The lead's repairs: `mark ROUND B<n> --resync` reads a build's base again from git
and moves it only after the target was rewritten under it; `mark ROUND REPORT.md
--reread` reads a recorded report again at its recorded bytes and only adds to its
verdict's record. Each writes one `repair` event, and neither names a ruling, a skip or
a verdict word."""

from __future__ import annotations

import hashlib

import pytest

from moxi_rounds import (api, asking, builds, decisions, findings, holds, log, opening,
                         repairs, showing, state, verdicts, waits)
from moxi_rounds.errors import BadArgument, BadBlock, Refused
from moxi_rounds.gitreads import Git

ROUND = "proj-a/a-round"

FLOW = """
    [flows.mine]
    steps = [
      { step = "build", by = "builder" },
      { step = "verify-build", by = "checker" },
      { step = "finish", by = "builder" },
    ]
"""


def events(round_dir) -> list[dict]:
    return list(log.read(log.round_log(round_dir)))


def folded(round_dir) -> state.State:
    return state.fold(events(round_dir))


def unchanged(round_dir, call) -> str:
    """Run `call`, which must be refused, and give the refusal's text; the log must be
    as it was."""
    before = log.round_log(round_dir).read_bytes()
    with pytest.raises(Refused) as error:
        call()
    assert log.round_log(round_dir).read_bytes() == before
    return str(error.value)


@pytest.fixture
def built(git_repo, tmp_path, write_config, capsys):
    """`proj-a` rooted at the test's git repo, whose `main` has two commits, A and B;
    the round's B1 on the branch `work` commissioned at B to `coder`, with one commit
    of work on it. Returns `(round folder, the main repo, the worktree's repo, A,
    B)`."""
    from moxi import cli
    from moxi_rounds import commissioning

    write_config(FLOW)
    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    first = git_repo.rev("main")
    second = git_repo.commit("second.txt", "the target's second commit")
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
    return round_dir, git_repo, tree, first, second


def rewrite_target(repo, onto: str) -> str:
    """Rewrite `main` so it holds `onto` and a new commit of its own, and no longer the
    commits above `onto`. Returns its new head."""
    repo.git("reset", "-q", "--hard", onto)
    return repo.commit("rewritten.txt", "the target after a rewrite")


def status(git_repo, round_dir) -> builds.Status:
    return builds.read(builds.current(folded(round_dir), "B1"), Git(git_repo.root))


# -- resync ------------------------------------------------------------------------------

def test_a_resync_after_the_target_was_rewritten_moves_the_base_to_what_it_still_has(
        built):
    round_dir, repo, _tree, first, second = built
    head = rewrite_target(repo, first)

    done = repairs.resync(ROUND, "B1", by="planner")

    assert builds.current(folded(round_dir), "B1").base == first
    event = done.event
    assert event["type"] == "repair" and event["what"] == "base"
    assert event["stage"] == "B1"
    assert event["fields"] == {"base": {"was": second, "now": first}}
    assert event["read"]["target_head"] == head
    assert event["read"]["output"] == [first]
    assert "rewritten" in done.said
    # A repair records git facts and never a step's completion.
    assert [one["type"] for one in events(round_dir)][-1] == "repair"
    assert not [one for one in events(round_dir)
                if one["type"] in ("finish.action", "finish.done")]


def test_a_base_still_on_the_target_is_left_as_it_is_and_nothing_is_written(built):
    round_dir, repo, _tree, _first, second = built
    repo.commit("more.txt", "the target moved on")
    before = log.round_log(round_dir).read_bytes()

    done = repairs.resync(ROUND, "B1", by="planner")

    assert done.event is None and "nothing to repair" in done.said
    assert log.round_log(round_dir).read_bytes() == before
    assert builds.current(folded(round_dir), "B1").base == second


def test_with_no_merge_base_nothing_moves_and_the_build_stays_unclear(built):
    round_dir, repo, _tree, _first, second = built
    repo.git("checkout", "-q", "--orphan", "fresh")
    repo.git("rm", "-q", "-rf", ".")
    repo.commit("fresh.txt", "an unrelated history")
    repo.git("branch", "-q", "-f", "main", "fresh")
    repo.git("switch", "-q", "main")
    before = log.round_log(round_dir).read_bytes()

    done = repairs.resync(ROUND, "B1", by="planner")

    assert done.event is None and "no merge base" in done.said
    assert log.round_log(round_dir).read_bytes() == before
    assert builds.current(folded(round_dir), "B1").base == second
    assert status(repo, round_dir).state == builds.UNCLEAR


def test_a_target_rewritten_under_a_build_on_it_reads_unclear_before_and_after(built):
    round_dir, repo, tree, first, _second = built
    head = rewrite_target(repo, first)
    # The branch is rebased onto the rewritten target, and the target fast-forwarded
    # onto it: the build is on the target, and its base is not.
    tree.git("rebase", "-q", "--onto", head, "HEAD~1")
    repo.git("merge", "-q", "--ff-only", "work")
    before = status(repo, round_dir)
    assert (before.state, before.by) == (builds.UNCLEAR, builds.NO_ANSWER)

    repairs.resync(ROUND, "B1", by="planner")

    after = status(repo, round_dir)
    # The base is on the target again, and with no reading that shows the work the
    # fast-forward credits nothing: a resync makes no build merged.
    assert (after.state, after.by) == (builds.UNCLEAR, builds.FAST_FORWARD)


def test_a_resync_turns_no_build_empty(built):
    round_dir, repo, _tree, first, _second = built
    rewrite_target(repo, first)
    before = status(repo, round_dir).state

    repairs.resync(ROUND, "B1", by="planner")

    assert before == status(repo, round_dir).state == builds.BUILDING


def test_a_resync_leaves_an_open_question_holding_the_stage(built):
    round_dir, repo, _tree, first, _second = built
    asking.ask(ROUND, "Is the placeholder right?", origin="comms:1234567",
               by="planner")
    held = [one.id for one in holds.on_stage(folded(round_dir), "B1")]
    assert held
    rewrite_target(repo, first)

    repairs.resync(ROUND, "B1", by="planner")

    assert [one.id for one in holds.on_stage(folded(round_dir), "B1")] == held


def test_a_resync_of_a_stage_with_no_open_build_is_refused(built):
    round_dir, repo, _tree, first, _second = built
    rewrite_target(repo, first)
    said = unchanged(round_dir, lambda: repairs.resync(ROUND, "B2", by="planner"))
    assert "never commissioned" in said
    log.append(log.round_log(round_dir), "build.dropped",
               {"stage": "B1", "by": "lead", "record": "comms:1234567", "tips": {}},
               by="planner")
    said = unchanged(round_dir, lambda: repairs.resync(ROUND, "B1", by="planner"))
    assert "dropped" in said


@pytest.mark.parametrize("given", ["C1", "C1,C2", "S3", "skip:check:B1",
                                   "skip-action:B1=merge", "publishable", "fold-first"])
def test_neither_repair_names_a_ruling_a_skip_or_a_verdict_word(built, given):
    round_dir, *_ = built
    said = unchanged(round_dir, lambda: repairs.resync(ROUND, given, by="planner"))
    assert "operator" in said or "report" in said
    said = unchanged(round_dir, lambda: repairs.reread(ROUND, given, by="planner"))
    assert "operator" in said or "report" in said


def test_a_resync_names_a_stage(built):
    with pytest.raises(BadArgument):
        repairs.resync(ROUND, "Q1", by="planner")


def test_the_repair_is_shown_and_in_the_history(built):
    round_dir, repo, _tree, first, second = built
    rewrite_target(repo, first)
    repairs.resync(ROUND, "B1", by="planner")

    found = showing.show(ROUND)
    shown_as = showing.data(found)
    assert [one["what"] for one in shown_as["repairs"]] == ["base"]
    text = showing.as_text(found)
    assert f"base of B1: {second[:12]} to {first[:12]}" in text
    assert "repair  base of B1" in text
    assert api.round_data(ROUND)["repairs"][0]["fields"]["base"]["now"] == first
    note = (round_dir / "round-note.md").read_text(encoding="utf-8")
    assert f"base of B1: {second[:12]} to {first[:12]}" in note


def test_mark_resync_prints_what_it_did_and_takes_no_pointer(built, capsys):
    from moxi import cli

    round_dir, repo, _tree, first, _second = built
    rewrite_target(repo, first)
    assert cli.main(["rounds", "mark", ROUND, "B1", "--resync",
                     "--record", "comms:1234567"]) == 2
    assert cli.main(["rounds", "mark", ROUND, "B1", "--resync"]) == 0
    assert f"to {first[:12]}" in capsys.readouterr().out
    assert cli.main(["rounds", "mark", ROUND, "B1", "--resync"]) == 0
    assert "nothing to repair" in capsys.readouterr().out
    assert cli.main(["rounds", "mark", ROUND, "C1", "--resync"]) == 3


# -- reread ------------------------------------------------------------------------------

def report(round_dir, head, *, findings_=(), waits_=(), decisions_=(),
           verdict="fold-first", name="review.md"):
    """Write a report of B1 read at `head`. `findings_` are `(id, severity, state)`,
    `waits_` ids, `decisions_` `(id, weight)`. Returns its path."""
    keys = ['kind = "build"', f'verdict = "{verdict}"', 'pass = "full"',
            'by = "critic"', 'model = "a-model"', 'stage = "B1"', f'head = "{head}"']
    more = "".join(f'\n\n[[finding]]\nid = "{id_}"\nseverity = "{severity}"\n'
                   f'state = "{state_}"\ntext = "a finding {id_}"'
                   for id_, severity, state_ in findings_)
    more += "".join(f'\n\n[[wait]]\nid = "{id_}"\ntext = "a wait {id_}"' for id_ in waits_)
    more += "".join(f'\n\n[[decision]]\nid = "{id_}"\nweight = "{weight}"\n'
                    f'forecloses = false\ntext = "a decision {id_}"'
                    for id_, weight in decisions_)
    path = round_dir / name
    path.write_text(f"# {name}\n\n```toml rounds\n" + "\n".join(keys) + more + "\n```\n",
                    encoding="utf-8")
    return path


def as_an_older_reader_recorded(round_dir, path, head, *, findings_=(), waits_=(),
                                decisions_=(), verdict="fold-first") -> dict:
    """Append the verdict of the report `path` as a reader that saw only the items
    given would have recorded it, at the report's bytes. Returns the event."""
    fields = {"kind": "build", "pass": "full", "verdict": verdict, "stage": "B1",
              "head": head, "merge_base": None, "model": "a-model", "by": "critic",
              "report": path.name,
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "findings": [{"id": id_, "severity": severity, "state": state_,
                            "text": f"a finding {id_}"}
                           for id_, severity, state_ in findings_]}
    if waits_:
        fields["waits"] = [{"id": id_, "text": f"a wait {id_}"} for id_ in waits_]
    if decisions_:
        fields["decisions"] = [{"id": id_, "weight": weight, "forecloses": False,
                                "text": f"a decision {id_}"} for id_, weight in decisions_]
    return log.append(log.round_log(round_dir), "verdict.recorded", fields, by="critic")


def test_a_reread_adds_what_the_block_holds_and_the_record_lacks(built):
    round_dir, _repo, tree, *_ = built
    head = tree.rev()
    path = report(round_dir, head, findings_=[("L1", "minor", "open"),
                                           ("L2", "major", "open")],
                  waits_=["W1"], decisions_=[("M1", "major")])
    recorded = as_an_older_reader_recorded(round_dir, path, head,
                                           findings_=[("L1", "minor", "open")])

    done = repairs.reread(ROUND, path, by="planner")

    event = done.event
    assert (event["what"], event["verdict"]) == ("report", recorded["n"])
    assert set(event["fields"]) == {"findings", "waits", "decisions"}
    assert [one["id"] for one in event["fields"]["findings"]["was"]] == ["L1"]
    assert [one["id"] for one in event["fields"]["findings"]["now"]] == ["L1", "L2"]
    assert event["read"]["sha256"] == recorded["sha256"]
    after = folded(round_dir)
    # Each joins the record as the verdict's own: raised by its report.
    assert [(one.id, one.raised) for one in findings.of_stage(after, "B1")] == [
        ("L1", recorded["n"]), ("L2", recorded["n"])]
    assert [one.key for one in waits.open_of(after)] == ["B1.W1"]
    assert [one.key for one in decisions.of_round(after)] == ["B1.M1"]
    assert after.verdicts[-1].findings[-1]["id"] == "L2"
    waiting = api.waiting()
    assert {"B1.W1", "B1.M1"} <= {row["id"] for row in
                                  [*waiting["waits"], *waiting["decisions"]]}
    assert "added findings L2" not in done.said and "finding L2" in done.said


def test_a_later_report_must_list_the_finding_a_reread_added(built):
    round_dir, _repo, tree, *_ = built
    head = tree.rev()
    path = report(round_dir, head, findings_=[("L1", "major", "open")])
    as_an_older_reader_recorded(round_dir, path, head)
    repairs.reread(ROUND, path, by="planner")

    again = report(round_dir, head, verdict="publishable", name="again.md")
    said = unchanged(round_dir, lambda: verdicts.record(ROUND, again, by="critic"))
    assert "B1.L1" in said


def test_a_report_read_as_recorded_adds_nothing_and_writes_nothing(built):
    round_dir, _repo, tree, *_ = built
    path = report(round_dir, tree.rev(), findings_=[("L1", "minor", "open")],
                  waits_=["W1"])
    verdicts.record(ROUND, path, by="critic")
    before = log.round_log(round_dir).read_bytes()

    done = repairs.reread(ROUND, path, by="planner")

    assert done.event is None and "nothing to add" in done.said
    assert log.round_log(round_dir).read_bytes() == before


def test_a_second_reread_finds_nothing_more(built):
    round_dir, _repo, tree, *_ = built
    head = tree.rev()
    path = report(round_dir, head, waits_=["W1"])
    as_an_older_reader_recorded(round_dir, path, head)
    assert repairs.reread(ROUND, path, by="planner").event is not None

    assert repairs.reread(ROUND, path, by="planner").event is None
    assert [one.key for one in waits.of_round(folded(round_dir))] == ["B1.W1"]


def test_a_report_whose_bytes_changed_is_refused_as_a_new_report(built):
    round_dir, _repo, tree, *_ = built
    path = report(round_dir, tree.rev())
    verdicts.record(ROUND, path, by="critic")
    path.write_text(path.read_text(encoding="utf-8") + "\nmore prose\n", encoding="utf-8")

    said = unchanged(round_dir, lambda: repairs.reread(ROUND, path, by="planner"))
    assert "changed since its verdict was recorded" in said
    assert "moxi rounds verdict" in said


def test_a_report_never_recorded_is_refused(built):
    round_dir, _repo, tree, *_ = built
    path = report(round_dir, tree.rev())
    said = unchanged(round_dir, lambda: repairs.reread(ROUND, path, by="planner"))
    assert "no verdict" in said


def test_a_reread_that_would_take_an_item_away_is_refused_naming_it(built):
    round_dir, _repo, tree, *_ = built
    head = tree.rev()
    path = report(round_dir, head)
    as_an_older_reader_recorded(round_dir, path, head, findings_=[("L1", "minor", "open")])

    said = unchanged(round_dir, lambda: repairs.reread(ROUND, path, by="planner"))
    assert "B1.L1" in said and "never takes an item away" in said


def test_a_reread_that_would_lower_an_item_is_refused_naming_it(built):
    round_dir, _repo, tree, *_ = built
    head = tree.rev()
    path = report(round_dir, head, findings_=[("L1", "minor", "open")],
                  decisions_=[("M1", "minor")])
    as_an_older_reader_recorded(round_dir, path, head, findings_=[("L1", "major", "open")],
                                decisions_=[("M1", "major")])

    said = unchanged(round_dir, lambda: repairs.reread(ROUND, path, by="planner"))
    assert "B1.L1" in said and "severity 'major' to 'minor'" in said
    assert "B1.M1" in said and "weight 'major' to 'minor'" in said


def test_a_reread_that_would_change_the_verdict_word_is_refused(built):
    round_dir, _repo, tree, *_ = built
    head = tree.rev()
    path = report(round_dir, head, verdict="publishable")
    as_an_older_reader_recorded(round_dir, path, head, verdict="fold-first")

    said = unchanged(round_dir, lambda: repairs.reread(ROUND, path, by="planner"))
    assert "verdict word" in said


def test_an_item_under_an_id_another_report_used_is_refused(built):
    round_dir, _repo, tree, *_ = built
    head = tree.rev()
    path = report(round_dir, head, waits_=["W1"])
    as_an_older_reader_recorded(round_dir, path, head)
    other = report(round_dir, head, waits_=["W1"], name="other.md")
    verdicts.record(ROUND, other, by="critic")

    said = unchanged(round_dir, lambda: repairs.reread(ROUND, path, by="planner"))
    assert "B1.W1" in said and "another report of B1 used" in said


def test_a_block_the_current_reader_refuses_is_refused_and_nothing_is_written(built):
    round_dir, _repo, tree, *_ = built
    head = tree.rev()
    # A publishable report with an open major contradicts itself, so a reread can't
    # give a publishable verdict one.
    path = report(round_dir, head, findings_=[("L1", "major", "open")],
                  verdict="publishable")
    as_an_older_reader_recorded(round_dir, path, head, verdict="publishable")
    before = log.round_log(round_dir).read_bytes()

    with pytest.raises(BadBlock) as error:
        repairs.reread(ROUND, path, by="planner")
    assert "open major" in str(error.value)
    assert log.round_log(round_dir).read_bytes() == before


def test_a_reread_is_shown_as_what_it_added(built):
    round_dir, _repo, tree, *_ = built
    head = tree.rev()
    path = report(round_dir, head, findings_=[("L1", "minor", "open")], waits_=["W1"])
    recorded = as_an_older_reader_recorded(round_dir, path, head)
    repairs.reread(ROUND, path, by="planner")

    text = showing.as_text(showing.show(ROUND))
    assert (f"verdict on line {recorded['n']} (review.md): added finding L1; "
            "waiting item W1") in text


def test_mark_reread_prints_what_it_added(built, capsys):
    from moxi import cli

    round_dir, _repo, tree, *_ = built
    head = tree.rev()
    path = report(round_dir, head, waits_=["W1"])
    as_an_older_reader_recorded(round_dir, path, head)
    assert cli.main(["rounds", "mark", ROUND, str(path), "--reread"]) == 0
    assert "waiting item W1" in capsys.readouterr().out
    assert cli.main(["rounds", "mark", ROUND, str(path), "--reread"]) == 0
    assert "nothing to add" in capsys.readouterr().out
