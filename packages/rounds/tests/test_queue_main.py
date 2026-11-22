"""The run on main afterwards: the target's head always gets a full run, pushed to its
`ci` ref when it has none and never while one is going; the lander stays until that run
has ended and been read, and gives a head up after two pushes show no run. A red run,
after its one rerun, starts a red spell owned by the lead of the earliest landing it
covers that has a lead, or goes to the director; the owner is reached by what comms
reads of its session; a green run ends the spell."""

from __future__ import annotations

import pytest
from test_queue_batch import (  # noqa: F401 -- fixtures
    OTHER, ROUND, configure, fake, queue, ready, repo)
from test_queue_telling import (  # noqa: F401 -- fixtures
    comms, config_text, lander_pass, no_lander_process, told)

from moxi_rounds import batching, ci, lander, leads, queue_file, queue_view
from moxi_rounds.cli import main

ONE_FAILS = {"job": "linux", "test": "tests/test_a.py::test_one"}
TWO_FAILS = {"job": "linux", "test": "tests/test_b.py::test_two"}


@pytest.fixture
def with_ref(write_config):
    """The project's `ci` names `ref = "ci/{branch}"`, and `director` names `dir-a`."""
    write_config(config_text(ref=True, table='director = "dir-a"'))


def hand_commit(repo, name: str) -> str:
    repo.git("checkout", "--quiet", "main")
    return repo.commit(name, f"a commit made by hand: {name}")


def landed(lander_pass, name: str = ROUND) -> None:
    lander_pass()
    lander_pass()
    assert queue().member(name, "B1").state == queue_file.LANDED


def main_pushes(fake) -> list[str]:
    return [commit for commit, ref in fake.pushes if ref == "ci/main"]


# -- the push and the read ---------------------------------------------------------------

def test_a_landing_whose_head_has_its_candidates_full_run_pushes_nothing(
        repo, ready, fake, lander_pass, with_ref):
    ready()
    landed(lander_pass)
    head = repo.rev("main")
    assert queue().main_at(head)["event"] == queue_file.GREEN
    assert main_pushes(fake) == []
    assert lander.settled("proj-a", queue())


def test_a_head_with_no_run_is_pushed_and_a_newer_head_waits_for_the_run_going(
        repo, ready, fake, lander_pass, with_ref):
    ready()
    landed(lander_pass)
    first = hand_commit(repo, "one.txt")
    fake.words[first] = ci.ABSENT
    lander_pass()
    assert main_pushes(fake) == [first]
    assert not lander.settled("proj-a", queue())
    lander_pass(60.0)
    assert main_pushes(fake) == [first]

    fake.words[first] = ci.PENDING
    second = hand_commit(repo, "two.txt")
    fake.words[second] = ci.ABSENT
    lander_pass(120.0)
    assert main_pushes(fake) == [first]
    assert lander.ensure("proj-a", spawn=lambda argv, env: None) is not None

    fake.words[first] = ci.GREEN
    lander_pass(180.0)
    assert queue().main_at(first)["event"] == queue_file.GREEN
    assert main_pushes(fake) == [first, second]
    fake.words[second] = ci.GREEN
    lander_pass(240.0)
    assert queue().main_at(second)["covers"] == []
    assert lander.settled("proj-a", queue())


def test_a_head_whose_pushes_show_no_run_is_given_up_and_the_director_told(
        repo, ready, fake, lander_pass, with_ref, comms):
    ready()
    landed(lander_pass)
    head = hand_commit(repo, "one.txt")
    fake.words[head] = ci.ABSENT
    lander_pass(0.0)
    lander_pass(batching.ABSENT_S)
    assert main_pushes(fake) == [head, head]
    lander_pass(2 * batching.ABSENT_S)
    assert main_pushes(fake) == [head, head]
    assert lander.settled("proj-a", queue())
    assert "never started" in comms.to("dir-a")[0]
    assert any("never started" in line for line in queue_view.lines(queue(),
                                                                   lander_runs=False))


# -- red spells --------------------------------------------------------------------------

def test_a_red_run_is_rerun_once_and_green_after_the_rerun_is_green(
        repo, ready, fake, lander_pass, with_ref):
    ready()
    lander_pass()
    fake.main_of = lambda: repo.rev("main")
    fake.landed_word = ci.RED
    lander_pass()
    head = repo.rev("main")
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert fake.reruns == [(("202",), True)]
    assert queue().main_at(head) is None and not lander.settled("proj-a", queue())
    fake.landed_word = ci.PENDING
    lander_pass(60.0)
    fake.landed_word = ci.GREEN
    lander_pass(120.0)
    assert queue().main_at(head)["event"] == queue_file.GREEN
    assert queue_view.spell_line(queue()) is None


def test_a_red_run_starts_a_spell_its_earliest_landings_lead_owns(
        repo, ready, fake, lander_pass, with_ref, comms, capsys):
    ready()
    leads.mark(ROUND, "lead-a", by="planner")
    fake.main_of = lambda: repo.rev("main")
    fake.landed_word, fake.failed = ci.RED, [ONE_FAILS]
    landed(lander_pass)
    head = repo.rev("main")
    lander_pass(batching.ABSENT_S)
    red = queue().main_at(head)
    assert (red["event"], red["owner"], red["owning"], red["since"], red["starts"]) == (
        queue_file.RED, "lead-a", [ROUND, "B1"], head, True)
    assert [one["round"] for one in red["covers"]] == [ROUND]
    assert "lead-a owns the fix" in comms.to("lead-a")[-1]
    assert "finish --urgent" in comms.to("lead-a")[-1]
    assert comms.to("dir-a") == []
    assert queue_view.spell_line(queue()) == f"main red since {head[:12]}: lead-a owns the fix"

    # a build that adds no new failure lands past it, and the owner is told
    ready(OTHER, branch="other", file="other.txt")
    assert main(["next", OTHER]) == 0
    assert f"main red since {head[:12]}: lead-a owns the fix" in capsys.readouterr().out
    fake.word, fake.failed = ci.RED, [ONE_FAILS]
    fake.landed_word = None
    fake.words[head] = ci.RED
    for now in (2 * batching.ABSENT_S, 2 * batching.ABSENT_S, 3 * batching.ABSENT_S):
        lander_pass(now)
    assert queue().member(OTHER, "B1").state == queue_file.LANDED
    assert any("past failures main has too" in text for text in comms.to("lead-a"))

    # a green run ends the spell, and the owner is told
    after = hand_commit(repo, "fix.txt")
    fake.words[after] = ci.GREEN
    lander_pass(4 * batching.ABSENT_S)
    assert queue().mains[-1]["event"] == queue_file.GREEN
    assert "is over" in comms.to("lead-a")[-1]
    assert queue_view.spell_line(queue()) is None


def test_a_landing_with_no_lead_passes_ownership_to_the_next_with_one(
        repo, ready, fake, lander_pass, with_ref, comms):
    ready()
    ready(OTHER, branch="other", file="other.txt")
    leads.mark(OTHER, "lead-b", by="planner")
    fake.main_of = lambda: repo.rev("main")
    fake.landed_word, fake.failed = ci.RED, [ONE_FAILS]
    lander_pass()
    lander_pass()
    lander_pass(batching.ABSENT_S)
    red = queue().mains[-1]
    assert [one["round"] for one in red["covers"]] == [ROUND, OTHER]
    assert (red["owner"], red["owning"]) == ("lead-b", [OTHER, "B1"])


def test_an_owner_with_no_running_session_is_still_told_and_the_director_gets_the_notice(
        repo, ready, fake, lander_pass, with_ref, comms):
    ready()
    leads.mark(ROUND, "lead-a", by="planner")
    comms.states.update({"lead-a": "standing", "dir-a": "free"})
    comms.delivery.update({"lead-a": "unbound", "dir-a": "unbound"})
    fake.main_of = lambda: repo.rev("main")
    fake.landed_word, fake.failed = ci.RED, [ONE_FAILS]
    landed(lander_pass)
    head = repo.rev("main")
    lander_pass(batching.ABSENT_S)
    assert "lead-a owns the fix" in comms.to("lead-a")[-1]
    notice = comms.to("dir-a")[-1]
    for words in ("main is red at", f"{ROUND} B1 at", "comms reads no running session",
                  "moxi launcher resume SESSION_ID --brief-file FILE",
                  "finish --urgent"):
        assert words in notice, words
    record = told("director")[-1]
    assert (record["owner_state"], record["director_state"], record["delivery"]) == (
        "standing", "free", "unbound")
    assert queue_view.spell_line(queue()) == (
        f"main red since {head[:12]}; lead-a and dir-a have no running session; the "
        "notice waits")


def test_an_identity_read_that_raises_reads_as_no_session(
        repo, ready, fake, lander_pass, with_ref, comms):
    ready()
    leads.mark(ROUND, "lead-a", by="planner")
    comms.states["lead-a"] = "raises"
    fake.main_of = lambda: repo.rev("main")
    fake.landed_word, fake.failed = ci.RED, [ONE_FAILS]
    landed(lander_pass)
    lander_pass(batching.ABSENT_S)
    assert told("director")[-1]["owner_state"] == "error"
    assert comms.to("dir-a")


def test_a_red_run_covering_no_landing_goes_to_the_director(
        repo, ready, fake, lander_pass, with_ref, comms):
    ready()
    leads.mark(ROUND, "lead-a", by="planner")
    landed(lander_pass)
    head = hand_commit(repo, "broken.txt")
    fake.words[head], fake.failed_at[head] = ci.RED, [TWO_FAILS]
    lander_pass(0.0)
    lander_pass(batching.ABSENT_S)
    red = queue().main_at(head)
    assert (red["covers"], red["owner"]) == ([], None)
    assert "covers no landing" in comms.to("dir-a")[-1]
    assert not any("red" in text for text in comms.to("lead-a"))
    assert "no landing with a lead owns it" in queue_view.spell_line(queue())


def test_a_red_run_after_red_tells_the_owner_only_when_its_failures_differ(
        repo, ready, fake, lander_pass, with_ref, comms):
    ready()
    leads.mark(ROUND, "lead-a", by="planner")
    fake.main_of = lambda: repo.rev("main")
    fake.landed_word, fake.failed = ci.RED, [ONE_FAILS]
    landed(lander_pass)
    lander_pass(batching.ABSENT_S)
    told_before = len(comms.to("lead-a"))
    fake.landed_word = None
    same = hand_commit(repo, "same.txt")
    fake.words[same], fake.failed_at[same] = ci.RED, [ONE_FAILS]
    lander_pass(2 * batching.ABSENT_S)
    lander_pass(3 * batching.ABSENT_S)
    assert queue().main_at(same)["owner"] == "lead-a"
    assert len(comms.to("lead-a")) == told_before
    other = hand_commit(repo, "other.txt")
    fake.words[other], fake.failed_at[other] = ci.RED, [TWO_FAILS]
    lander_pass(4 * batching.ABSENT_S)
    lander_pass(5 * batching.ABSENT_S)
    assert "differ from the run before" in comms.to("lead-a")[-1]
