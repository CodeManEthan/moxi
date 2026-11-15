"""Waiting items: a build report's `[[wait]]` is an open item of the round at once, it
is listed wherever what waits on the operator is, it holds the round's close and no
finish, and `mark ... --done --record` ends it."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import (api, closing, dropping, index, log, opening, rendering,
                         showing, state, verdicts, waits)
from moxi_rounds import settings as own
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Refused

ROUND = "proj-a/a-round"
PAGE = "check the page on a phone"
DONE_AT = "https://example.test/done"


@pytest.fixture
def built(git_repo, tmp_path, capsys):
    """`proj-a` rooted at the test's git repo, its round in the `fix` flow, and B1 on the
    branch `work` commissioned to `coder` with one commit made on it. Returns `(round
    folder, the worktree's repo)`."""
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
    return round_dir, tree


def report(round_dir, head, *listed, name="report.md", verdict="publishable"):
    """Write a report of B1 read at `head` listing the waiting items `listed`, each
    `(id, text)`, and return its path."""
    keys = ['kind = "build"', f'verdict = "{verdict}"', 'pass = "full"',
            'by = "critic"', 'model = "a-model"', 'stage = "B1"', f'head = "{head}"']
    more = "".join(f'\n\n[[wait]]\nid = "{id_}"\ntext = "{text}"' for id_, text in listed)
    path = round_dir / name
    path.write_text(f"# {name}\n\n```toml rounds\n" + "\n".join(keys) + more + "\n```\n",
                    encoding="utf-8")
    return path


def verify(round_dir, head, *listed, name="report.md"):
    return verdicts.record(ROUND, report(round_dir, head, *listed, name=name),
                           by="critic")


def folded(round_dir):
    return state.fold(log.read(log.round_log(round_dir)))


def raw(round_dir) -> bytes:
    return log.round_log(round_dir).read_bytes()


def done(key="B1.W1", record=DONE_AT):
    return waits.done(ROUND, key, record=record, by="planner")


def refusal(**more) -> str:
    with pytest.raises(Refused) as refused:
        done(**more)
    return str(refused.value)


# -- declared by a report -----------------------------------------------------------------

def test_each_wait_of_a_build_report_is_an_open_waiting_item_once_recorded(built):
    round_dir, tree = built

    recorded = verify(round_dir, tree.rev(), ("W1", PAGE), ("W2", "read the page aloud"))

    [event] = [one for one in log.read(log.round_log(round_dir))
               if one["type"] == "verdict.recorded"]
    assert event["waits"] == [{"id": "W1", "text": PAGE},
                              {"id": "W2", "text": "read the page aloud"}]
    found = waits.of_round(folded(round_dir))
    assert [(one.key, one.state, one.text, one.report, one.by) for one in found] == [
        ("B1.W1", "open", PAGE, "report.md", "critic"),
        ("B1.W2", "open", "read the page aloud", "report.md", "critic")]
    assert [one.key for one in recorded.opened] == ["B1.W1", "B1.W2"]
    # One event is the whole act: nothing else was appended for the items.
    assert [one["type"] for one in log.read(log.round_log(round_dir))][-1] == (
        "verdict.recorded")


def test_verdict_prints_each_waiting_item_with_the_command_that_ends_it(built, capsys):
    round_dir, tree = built

    assert main(["verdict", ROUND, str(report(round_dir, tree.rev(), ("W1", PAGE)))]) == 0

    out = capsys.readouterr().out
    assert f"waiting item B1.W1, open: {PAGE}\n" in out
    assert f"once done: moxi rounds mark {ROUND} B1.W1 --done --record PTR" in out


def test_a_later_report_of_the_build_that_restates_a_waiting_item_names_the_same_item(
        built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))

    again = verify(round_dir, tree.rev(), ("W1", PAGE), ("W2", "another check"),
                   name="second.md")

    found = waits.of_round(folded(round_dir))
    assert [(one.key, one.report) for one in found] == [
        ("B1.W1", "report.md"), ("B1.W2", "second.md")]
    assert [one.key for one in again.opened] == ["B1.W2"]


def test_a_restated_waiting_item_that_is_done_stays_done(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))
    done()

    verify(round_dir, tree.rev(), ("W1", PAGE), name="second.md")

    [one] = waits.of_round(folded(round_dir))
    assert (one.state, one.record) == ("done", DONE_AT)


def test_a_restated_waiting_item_id_with_another_sentence_is_refused_naming_both(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))
    before = raw(round_dir)

    with pytest.raises(Refused) as refused:
        verify(round_dir, tree.rev(), ("W1", "read the page aloud"), name="second.md")

    said = str(refused.value)
    assert f"the waiting item B1.W1 is on record as '{PAGE}'" in said
    assert "the report lists W1 as 'read the page aloud'" in said
    assert raw(round_dir) == before


def test_a_waiting_item_id_a_dropped_build_s_report_used_is_refused(built, git_repo):
    from moxi_rounds import commissioning

    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))
    log.append(log.round_log(round_dir), "build.dropped", {"stage": "B1"}, by="planner")
    git_repo.git("worktree", "remove", "--force", str(tree.root))
    git_repo.git("branch", "-D", "work")
    made = commissioning.commission(ROUND, "B1", builder="coder", by="planner")
    again = type(git_repo)(made.worktree)
    again.commit("work.txt", "the work again")

    with pytest.raises(Refused) as refused:
        verify(round_dir, again.rev(), ("W1", PAGE), name="second.md")

    said = str(refused.value)
    assert "the waiting item W1 is new, and an earlier report of B1 used the id W1" in said
    assert f"moxi rounds mark {ROUND} B1.W1 --done --record PTR" in said


def test_a_waiting_item_holds_the_close_and_not_the_finish(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))
    now = folded(round_dir)

    finish = verdicts.finish_waits(now, "B1", name=ROUND, settings=own.load())
    close = closing.waits(now)

    assert not any("W1" in line for line in finish)
    assert "waiting item B1.W1, open" in close


def test_the_lead_may_not_drop_a_round_whose_waiting_item_is_open(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))

    held = dropping.his_acts(folded(round_dir), state.fold([]), ROUND)

    assert "the open waiting item B1.W1" in held
    done()
    assert not any("waiting item" in line for line in
                   dropping.his_acts(folded(round_dir), state.fold([]), ROUND))


# -- listed -------------------------------------------------------------------------------

def test_list_waiting_holds_each_open_waiting_item_with_its_round(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))

    assert main(["list", "--waiting"]) == 0
    out = capsys.readouterr().out
    assert main(["list", "--waiting", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)

    assert f"open waiting items:\n  {ROUND} B1.W1: {PAGE}\n" in out
    assert f"once done: moxi rounds mark {ROUND} B1.W1 --done --record PTR" in out
    [row] = found["waits"]
    assert {key: row[key] for key in ("round", "id", "stage", "text", "report", "by")} == {
        "round": ROUND, "id": "B1.W1", "stage": "B1", "text": PAGE,
        "report": "report.md", "by": "critic"}
    assert api.waiting()["waits"] == found["waits"]
    summary = index.read("proj-a", round_dir.parent, "a-round")
    assert [item.id for item in summary.of(index.WAIT)] == ["B1.W1"]


def test_show_the_note_and_the_api_hold_every_waiting_item(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE), ("W2", "read the page aloud"))
    done(key="B1.W2")

    assert main(["show", ROUND]) == 0
    out = capsys.readouterr().out
    shown_as = showing.data(showing.show(ROUND))
    note = (round_dir / rendering.NOTE_FILE).read_text(encoding="utf-8")

    assert f"waiting items: 2\n  waiting item B1.W1, open: {PAGE}\n" in out
    assert f"once done: moxi rounds mark {ROUND} B1.W1 --done --record PTR" in out
    assert f"record: {DONE_AT}" in out
    assert [(one["id"], one["state"]) for one in shown_as["waits"]] == [
        ("B1.W1", "open"), ("B1.W2", "done")]
    assert shown_as["waits"][1]["record"] == DONE_AT
    assert shown_as["waits"][1]["once_done"] is None
    assert api.round_data(ROUND)["waits"] == shown_as["waits"]
    assert "## Waiting items" in note
    assert f"- waiting item B1.W1, open: {PAGE}" in note
    assert "- waiting item B1.W2, done: read the page aloud" in note


# -- marked done --------------------------------------------------------------------------

def test_a_waiting_item_marked_done_is_one_event_with_its_pointer(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))

    assert main(["mark", ROUND, "B1.W1", "--done", "--record", DONE_AT]) == 0

    assert capsys.readouterr().out == f"{ROUND} B1.W1: done   record: {DONE_AT}\n"
    [event] = [one for one in log.read(log.round_log(round_dir))
               if one["type"] == "wait.marked"]
    assert {key: event[key] for key in ("id", "state", "record")} == {
        "id": "B1.W1", "state": "done", "record": DONE_AT}
    assert waits.open_of(folded(round_dir)) == ()
    assert main(["list", "--waiting", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["waits"] == []


def test_a_mark_run_again_finds_the_item_done_and_writes_nothing(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))
    done()
    before = raw(round_dir)

    again = done(record="comms:1234567")

    assert again.found and again.event is None
    assert again.wait.record == DONE_AT
    assert raw(round_dir) == before
    assert main(["mark", ROUND, "B1.W1", "--done", "--record", DONE_AT]) == 0
    assert "done already" in capsys.readouterr().out


def test_a_mark_without_a_pointer_or_with_one_of_no_shape_is_refused(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))
    before = raw(round_dir)

    assert "marked done with a pointer" in refusal(record=None)
    assert "is not a pointer to what shows it done" in refusal(record="the phone")
    assert main(["mark", ROUND, "B1.W1", "--done"]) == 3
    assert raw(round_dir) == before


def test_an_item_the_round_does_not_hold_or_a_bad_key_is_refused(built, capsys):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))

    assert f"{ROUND} holds no waiting item B1.W2" in refusal(key="B1.W2")
    assert f"{ROUND} holds no waiting item B2.W1" in refusal(key="B2.W1")
    with pytest.raises(BadArgument):
        done(key="B1.L1")
    assert main(["mark", ROUND, "W1", "--done", "--record", DONE_AT]) == 2


def test_a_commit_of_the_build_s_repo_is_a_pointer_and_its_whole_id_is_recorded(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))
    commit = tree.rev()

    marked = done(record=commit[:9])

    assert marked.wait.record == commit
    [event] = [one for one in log.read(log.round_log(round_dir))
               if one["type"] == "wait.marked"]
    assert event["record"] == commit


def test_a_commit_the_build_s_repo_does_not_hold_is_refused(built):
    round_dir, tree = built
    verify(round_dir, tree.rev(), ("W1", PAGE))
    before = raw(round_dir)

    said = refusal(record="0123abc")

    assert "0123abc names no one commit that proj-a holds" in said
    assert raw(round_dir) == before
    # Shorter than seven hex digits is no commit id, and no pointer either.
    assert "is not a pointer to what shows it done" in refusal(record="abc12")
