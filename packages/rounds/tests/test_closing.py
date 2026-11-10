"""A round closes itself: what its close waits for, the write that closes it, a close
that stopped short, and what a closed round is from then on."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import (api, asking, closing, dropping, freezing, holds, log, opening,
                         ruling, state, verdicts)
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
PTR = "comms:1234567"
SHOWN = "comms:1234599"
BUILDS = "a finished stage, which this version does not build, so the round does not close in it"

FLOWS = """\
    [flows.paper]
    steps = [ { step = "design", by = "lead", model = "second" },
              { step = "verify-design", by = "checker", model = "second" } ]

    [flows.sketch]
    steps = [ { step = "design", by = "lead" } ]

    [flows.empty]
    steps = []

    [flows.review]
    steps = [ { step = "design", by = "lead" },
              { step = "retro", by = "worker" } ]
    """


@pytest.fixture
def opened(tmp_path, add_project, write_config, capsys):
    """`opened(flow="paper")`: open `a-round` of `proj-a` on that flow and return its
    folder. `paper` is a design and its verification; `sketch` is a design alone."""
    add_project("proj-a", notes_root=tmp_path / "notes")

    def open_on(flow: str = "paper"):
        write_config(FLOWS)
        opening.open_round(ROUND, description="a placeholder round", actor="planner",
                           flow=flow)
        capsys.readouterr()
        return tmp_path / "notes" / "rounds" / "a-round"

    return open_on


@pytest.fixture
def folder(opened):
    return opened()


def question(id_, **more) -> str:
    lines = ["[[question]]", f'id = "{id_}"', f'text = "{ASKED}"']
    lines += [f"{key} = {value}" for key, value in more.items()]
    return "\n".join(lines)


def stage(id_) -> str:
    return "\n".join(["[[stage]]", f'id = "{id_}"', 'repo = "proj-a"',
                      f'branch = "branch-{id_.lower()}"', "after = []", "",
                      "  [[stage.slice]]", '  id = "1a"', '  what = "a piece"',
                      "  minutes = 40"])


_folds = iter(range(1, 1000))


def fold(folder, *entries):
    text = (f"# A design\n\nDraft {next(_folds)}.\n\n```toml rounds\n"
            + "\n\n".join(entries) + "\n```\n")
    (folder / "design.md").write_text(text, encoding="utf-8")
    return freezing.freeze(ROUND, actor="scout")


_reports = iter(range(1, 1000))


def verdict(folder, word="publishable", *, design=1):
    count = next(_reports)
    keys = ['kind = "design"', f'verdict = "{word}"', 'pass = "full"',
            'by = "critic"', 'model = "a-model"', f"design = {design}"]
    path = folder / f"report-{count}.md"
    path.write_text(f"# A report\n\nPass {count}.\n\n```toml rounds\n"
                    + "\n".join(keys) + "\n```\n", encoding="utf-8")
    return verdicts.record(ROUND, path, actor="critic")


def rule(item=None, **more):
    return ruling.rule(ROUND, item=item, record=PTR, actor="planner", **more)


def events(folder) -> list[dict]:
    return log.read(log.round_log(folder))


def types(folder) -> list[str]:
    return [event["type"] for event in events(folder)]


def folded(folder):
    return state.fold(events(folder))


def status(folder) -> str:
    return folded(folder).status


def waits(folder) -> tuple[str, ...]:
    return closing.waits(folded(folder))


def run(capsys, *argv) -> str:
    assert main(list(argv)) == 0
    return capsys.readouterr().out


# -- the write that closes a round -------------------------------------------------------

def test_a_round_that_builds_nothing_closes_on_the_verdict_that_verifies_its_design(
        folder):
    fold(folder)
    assert status(folder) == "open"
    assert waits(folder) == ("step verify-design, ready",)

    verdict(folder)

    assert types(folder)[-2:] == ["verdict.recorded", "round.closed"]
    assert status(folder) == "closed"


def test_the_closing_event_is_the_verb_s_own_and_holds_nothing_more(folder):
    fold(folder)
    verdict(folder)

    recorded, closed = events(folder)[-2:]

    assert closed == {"v": 1, "n": recorded["n"] + 1, "at": closed["at"],
                      "actor": "critic", "type": "round.closed"}


def test_a_verdict_that_sends_the_design_back_closes_nothing(folder):
    fold(folder)
    verdict(folder, "fold-first")

    assert status(folder) == "open"
    assert "round.closed" not in types(folder)


def test_a_round_with_a_question_open_closes_on_the_ruling_that_answers_the_last_one(
        folder):
    fold(folder, question("Q1", blocks='"none"'), question("Q2", blocks='"none"'))
    verdict(folder)
    assert waits(folder) == ("question Q1, open", "question Q2, open")

    rule("Q1", text="They do.")
    assert status(folder) == "open"
    rule("Q2", text="They do not.")

    assert types(folder)[-2:] == ["ruling.recorded", "round.closed"]
    assert status(folder) == "closed"


def test_a_parked_question_keeps_a_round_open_until_it_is_ruled(folder):
    fold(folder, question("Q1", blocks='"none"'))
    verdict(folder)
    rule("park:Q1", until="round:proj-a/another")

    assert waits(folder) == ("question Q1, parked",)
    assert status(folder) == "open"

    rule("Q1", text="They do.")
    assert status(folder) == "closed"


def test_a_question_asked_after_the_design_is_what_the_close_waits_for(folder):
    fold(folder)
    asked = asking.ask(ROUND, ASKED, origin="comms:11", blocks="none", actor="scout").id
    verdict(folder)

    assert waits(folder) == (f"question {asked}, open",)
    rule(f"withdraw:{asked}")
    assert status(folder) == "closed"


def test_a_skip_of_the_design_s_verification_closes_a_round_whose_design_is_frozen(
        folder):
    fold(folder)

    rule("skip:verify-design")

    assert types(folder)[-2:] == ["ruling.recorded", "round.closed"]
    assert status(folder) == "closed"


def test_a_skip_recorded_before_the_design_closes_on_the_freezing(folder):
    rule("skip:verify-design")
    assert waits(folder) == ("step design, ready",)

    fold(folder)

    assert types(folder)[-2:] == ["design.frozen", "round.closed"]


def test_a_round_whose_flow_ends_in_a_retro_closes_on_the_skip_of_it(opened):
    folder = opened("review")
    fold(folder)
    assert status(folder) == "open"
    assert waits(folder) == ("step retro, not-built",)

    rule("skip:retro")

    assert types(folder)[-2:] == ["ruling.recorded", "round.closed"]
    assert status(folder) == "closed"


def test_a_retro_skipped_before_the_design_closes_on_the_freezing(opened):
    folder = opened("review")
    rule("skip:retro")
    assert waits(folder) == ("step design, ready",)

    fold(folder)

    assert types(folder)[-2:] == ["design.frozen", "round.closed"]


def test_a_skipped_retro_reads_the_same_in_every_place_that_shows_it(opened, capsys):
    folder = opened("review")
    fold(folder, question("Q1", blocks='"none"'))
    before = run(capsys, "show", ROUND)
    assert "step: retro, not-built\n" in before
    assert "close waits for: step retro, not-built; question Q1, open\n" in before
    assert run(capsys, "next", ROUND).splitlines() == [
        f"round {ROUND}   flow review   step retro   not built in this version"]

    rule("skip:retro")

    shown = run(capsys, "show", ROUND)
    assert not [line for line in shown.splitlines() if line.startswith("step: ")]
    assert "skipped: retro, by C1" in shown
    assert "close waits for: question Q1, open\n" in shown
    assert "not-built" not in shown
    assert run(capsys, "next", ROUND).splitlines()[:2] == [
        f"round {ROUND}   flow review   step none: every step of the flow is done or "
        "skipped",
        "close waits for: question Q1, open"]
    assert waits(folder) == ("question Q1, open",)
    assert api.round(ROUND)["step"] is None


def test_a_skip_of_the_retro_taken_back_keeps_the_round_open_again(opened, capsys):
    folder = opened("review")
    fold(folder, question("Q1", blocks='"none"'))
    rule("skip:retro")

    rule("revoke:C1")

    assert waits(folder) == ("step retro, not-built", "question Q1, open")
    assert "step: retro, not-built\n" in run(capsys, "show", ROUND)
    rule("Q1", text="They do.")
    assert status(folder) == "open"


def test_a_flow_that_is_a_design_alone_closes_on_its_freezing(opened):
    folder = opened("sketch")
    assert status(folder) == "open"

    fold(folder)

    assert status(folder) == "closed"


def test_a_stage_in_the_plan_keeps_a_round_that_builds_nothing_open(folder):
    fold(folder, stage("B1"))
    verdict(folder)
    assert waits(folder) == ("stage B1, not finished",)
    assert status(folder) == "open"

    rule("unstage:B1")

    assert status(folder) == "closed"


# -- a flow that builds ------------------------------------------------------------------

@pytest.mark.parametrize("flow", ["full", "small", "fix"])
def test_a_round_whose_flow_builds_does_not_close(opened, capsys, flow):
    folder = opened(flow)
    if flow != "fix":
        fold(folder)
    if flow == "full":
        verdict(folder)

    assert waits(folder) == (BUILDS,)
    assert status(folder) == "open"
    assert "round.closed" not in types(folder)
    assert f"close waits for: {BUILDS}\n" in run(capsys, "show", ROUND)


# -- the verb whose write closes a round says so -----------------------------------------

CLOSED_LINE = f"closed {ROUND}: nothing is left for its close to wait for"


def report(folder, word="publishable"):
    path = folder / "a-report.md"
    path.write_text('# A report\n\n```toml rounds\nkind = "design"\n'
                    f'verdict = "{word}"\npass = "full"\nby = "critic"\n'
                    'model = "a-model"\ndesign = 1\n```\n', encoding="utf-8")
    return str(path)


def test_the_verdict_that_closes_a_round_says_so_in_its_last_line(folder, capsys):
    fold(folder)

    printed = run(capsys, "verdict", ROUND, report(folder)).splitlines()

    assert printed[-1] == CLOSED_LINE
    assert printed.count(CLOSED_LINE) == 1


def test_a_verdict_that_closes_nothing_does_not_say_closed(folder, capsys):
    fold(folder)

    printed = run(capsys, "verdict", ROUND, report(folder, "fold-first"))

    assert CLOSED_LINE not in printed.splitlines()
    assert status(folder) == "open"


def test_the_ruling_that_answers_the_last_question_says_it_closed_the_round(
        opened, capsys):
    folder = opened("sketch")
    (folder / "design.md").write_text(
        "# A design\n\n```toml rounds\n" + question("Q1", blocks='"none"')
        + "\n```\n", encoding="utf-8")
    assert CLOSED_LINE not in run(capsys, "fold", ROUND).splitlines()
    assert run(capsys, "rule", ROUND, "Q1", "--text", "They do.", "--record",
               PTR).splitlines()[-1] == CLOSED_LINE


def test_the_freezing_that_closes_a_round_says_so(opened, capsys):
    folder = opened("sketch")
    (folder / "design.md").write_text("# A design\n\n```toml rounds\n```\n",
                                      encoding="utf-8")

    assert run(capsys, "fold", ROUND).splitlines()[-1] == CLOSED_LINE


def test_the_skip_that_closes_a_round_says_so(folder, capsys):
    fold(folder)

    assert run(capsys, "rule", ROUND, "skip:verify-design", "--record",
               PTR).splitlines()[-1] == CLOSED_LINE


def test_a_round_opened_on_a_flow_with_no_steps_is_said_to_be_closed_by_open(
        tmp_path, add_project, write_config, capsys):
    add_project("proj-a", notes_root=tmp_path / "notes")
    write_config(FLOWS)
    capsys.readouterr()

    printed = run(capsys, "open", ROUND, "--description", "a placeholder round",
                  "--flow", "empty").splitlines()

    assert printed[0] == f"opened {ROUND}   flow empty (no steps)"
    assert printed[-1] == CLOSED_LINE
    assert status(tmp_path / "notes" / "rounds" / "a-round") == "closed"


def test_a_write_to_a_round_already_closed_does_not_say_closed_again(folder, capsys):
    fold(folder)
    rule("skip:verify-design")

    printed = run(capsys, "rule", ROUND, "revoke:C1", "--record", PTR)

    assert CLOSED_LINE not in printed.splitlines()


def test_a_verb_that_is_refused_says_nothing_of_a_close(folder, capsys):
    fold(folder)
    verdict(folder)

    assert main(["ask", ROUND, ASKED, "--from", PTR]) != 0

    assert "closed proj-a/a-round:" not in capsys.readouterr().out


def test_only_the_writes_made_while_watching_are_collected(folder):
    from moxi_rounds import write

    fold(folder)
    with write.watching() as writes:
        rule("skip:verify-design")
    rule("revoke:C1")

    assert [one.closed["type"] for one in writes] == ["round.closed"]


# -- a close that stopped short ----------------------------------------------------------

def test_a_close_that_failed_is_reported_and_made_by_the_next_write(
        folder, monkeypatch, capsys):
    fold(folder)
    with monkeypatch.context() as patched:
        def fails(_state):
            raise OSError("no room")
        patched.setattr(closing, "entry", fails)
        made = rule("skip:verify-design")

    assert [str(error) for _what, error in made.failed] == ["no room"]
    assert types(folder)[-1] == "ruling.recorded" and status(folder) == "open"
    assert ("close waits for: nothing; the next write to the round closes it\n"
            in run(capsys, "show", ROUND))

    run(capsys, "mark", ROUND, made.id, "--shown", "--record", SHOWN)

    assert types(folder)[-3:] == ["ruling.recorded", "ruling.shown", "round.closed"]
    assert status(folder) == "closed"


def test_a_verb_stopped_before_the_close_leaves_it_to_the_next_write(
        folder, monkeypatch, capsys):
    fold(folder)
    with monkeypatch.context() as patched:
        def stops(_state):
            raise KeyboardInterrupt
        patched.setattr(closing, "entry", stops)
        with pytest.raises(KeyboardInterrupt):
            rule("skip:verify-design")
    assert types(folder)[-1] == "ruling.recorded" and status(folder) == "open"

    run(capsys, "mark", ROUND, "C1", "--shown", "--record", SHOWN)

    assert types(folder)[-1] == "round.closed"


# -- a closed round ----------------------------------------------------------------------

@pytest.fixture
def closed(folder):
    fold(folder)
    rule("skip:verify-design")
    assert status(folder) == "closed"
    return folder


def test_a_closed_round_takes_no_new_work(closed):
    with pytest.raises(Refused) as refused:
        asking.ask(ROUND, ASKED, origin="comms:11", actor="scout")
    assert "was closed and stays as it ended" in str(refused.value)
    with pytest.raises(Refused):
        fold(closed)
    with pytest.raises(Refused):
        rule(text="A new ruling.")
    with pytest.raises(Refused):
        dropping.drop(ROUND, by="operator", record=PTR, actor="planner")


def test_a_write_to_a_closed_round_does_not_close_it_again(closed, capsys):
    run(capsys, "mark", ROUND, "C1", "--shown", "--record", SHOWN)

    assert types(closed).count("round.closed") == 1
    assert types(closed)[-1] == "ruling.shown"
    assert status(closed) == "closed"


def test_a_dropped_round_is_not_closed_by_a_later_write(folder, monkeypatch, capsys):
    fold(folder)
    with monkeypatch.context() as patched:
        patched.setattr(closing, "entry", lambda _state: None)
        rule("skip:verify-design")
        dropping.drop(ROUND, by="operator", record=PTR, actor="planner")

    run(capsys, "mark", ROUND, "C1", "--shown", "--record", SHOWN)

    assert "round.closed" not in types(folder)
    assert status(folder) == "dropped"


def test_a_closed_round_is_shown_as_closed_with_when(closed, capsys):
    at = events(closed)[-1]["at"]

    shown = run(capsys, "show", ROUND)
    found = json.loads(run(capsys, "show", ROUND, "--json"))

    assert shown.splitlines()[0].startswith(f"round {ROUND}   closed   flow paper ")
    assert f"ended: closed at {at}\n" in shown
    assert "close waits for" not in shown and "step:" not in shown
    assert found["status"] == "closed" and found["close"] is None
    assert found["step"] is None
    assert found["ended"] == {"how": "closed", "by": None, "record": None,
                              "actor": "planner", "at": at, "questions": {}}
    assert shown.splitlines()[-1].endswith("  planner  round.closed")


def test_a_closed_round_is_listed_with_the_ended_ones(closed, capsys):
    at = events(closed)[-1]["at"]

    assert run(capsys, "list") == "no round is open\n"
    listed = run(capsys, "list", "--closed")
    assert listed.startswith(f"{ROUND}   closed at {at}   flow paper   ")
    assert " by " not in listed and "record" not in listed
    assert api.rounds()["rounds"] == []
    (found,) = api.rounds(status="closed")["rounds"]
    assert (found["name"], found["status"], found["step"]) == (ROUND, "closed", None)
    assert found["ended"] == {"how": "closed", "by": None, "record": None, "at": at}
    assert api.round(ROUND)["close"] is None


def test_a_closed_round_s_note_and_its_export_say_so(closed, capsys):
    at = events(closed)[-1]["at"]

    note = (closed / "round-note.md").read_text(encoding="utf-8")
    assert "status: closed\n" in note.split("---")[1]
    assert f"- status: closed\n- ended: closed at {at}\n" in note
    assert "close waits for" not in note
    export = run(capsys, "list", ROUND, "--questions")
    assert (f"This round has ended: closed at {at}. Nothing of it is put to the "
            "operator.") in export
    assert run(capsys, "next", ROUND).splitlines()[0] == (
        f"round {ROUND}   flow paper   step none: the round is closed")


# -- what an open round says of its close ------------------------------------------------

def test_an_open_round_is_shown_with_what_its_close_waits_for(folder, capsys):
    fold(folder, question("Q1", blocks='"none"'), stage("B1"))

    said = "step verify-design, ready; stage B1, not finished; question Q1, open"
    assert f"close waits for: {said}\n" in run(capsys, "show", ROUND)
    assert json.loads(run(capsys, "show", ROUND, "--json"))["close"] == {
        "waits": said.split("; ")}
    assert api.round(ROUND)["close"] == {"waits": said.split("; ")}
    note = (folder / "round-note.md").read_text(encoding="utf-8")
    assert f"- close waits for: {said}\n" in note


def test_a_round_at_no_step_is_told_what_keeps_it_open(folder, capsys):
    fold(folder, question("Q1", blocks='"none"'))
    verdict(folder)
    rule("park:Q1", until="round:proj-a/another")

    assert run(capsys, "next", ROUND).splitlines()[:2] == [
        f"round {ROUND}   flow paper   step none: every step of the flow is done or "
        "skipped",
        "close waits for: question Q1, parked"]
    assert json.loads(run(capsys, "next", ROUND, "--json"))["close"] == {
        "waits": ["question Q1, parked"]}


def test_a_parked_question_whose_wait_is_over_has_one_state_in_what_show_prints(
        opened, capsys):
    folder = opened("sketch")
    opening.open_round("proj-a/another", description="a placeholder round",
                       actor="planner", flow="empty")
    fold(folder, question("Q1", blocks='"none"'))
    rule("park:Q1", until="round:proj-a/another")

    shown = run(capsys, "show", ROUND)
    as_data = json.loads(run(capsys, "show", ROUND, "--json"))

    assert "close waits for: question Q1, open\n" in shown
    assert "question Q1, parked" not in shown
    assert as_data["close"] == {"waits": ["question Q1, open"]}
    assert [one["state"] for one in as_data["questions"]] == ["open"]
    assert json.loads(run(capsys, "next", ROUND, "--json"))["close"] == {
        "waits": ["question Q1, open"]}
    assert api.round(ROUND)["close"] == {"waits": ["question Q1, open"]}
    # Whether the round closes reads its own log alone, and is the same either way.
    assert waits(folder) == ("question Q1, parked",)
    assert status(folder) == "open"


def test_a_question_parked_until_a_round_still_open_is_named_as_parked(opened, capsys):
    folder = opened("sketch")
    opening.open_round("proj-a/another", description="a placeholder round",
                       actor="planner", flow="paper")
    fold(folder, question("Q1", blocks='"none"'))
    rule("park:Q1", until="round:proj-a/another")

    assert "close waits for: question Q1, parked\n" in run(capsys, "show", ROUND)


def test_the_questions_the_close_waits_for_are_the_ones_the_hold_rule_gives(
        folder, monkeypatch):
    fold(folder, question("Q1", blocks='"none"'), question("Q2"), question("Q3"))
    verdict(folder)
    rule("park:Q2", until="round:proj-a/another")
    rule("withdraw:Q3")
    found = folded(folder)
    assert closing.waits(found) == tuple(
        f"question {one.id}, {one.state}" for one in holds.on_close(found))
    assert closing.waits(found) == ("question Q1, open", "question Q2, parked")

    monkeypatch.setattr(holds, "on_close", lambda state: ())

    assert closing.waits(found) == () and closing.is_due(found)


def test_what_a_close_waits_for_is_read_from_the_state_alone(folder):
    fold(folder, question("Q1", blocks='"none"'))
    found = folded(folder)
    (folder / "design.md").unlink()

    assert closing.waits(found) == ("step verify-design, ready", "question Q1, open")
    assert not closing.is_due(found) and closing.entry(found) is None


def test_a_part_added_to_the_condition_is_asked_with_the_rest(folder, monkeypatch):
    fold(folder)
    monkeypatch.setattr(closing, "CONDITIONS",
                        (*closing.CONDITIONS, lambda _state, _closed: iter(["a placeholder"])))

    rule("skip:verify-design")

    assert waits(folder) == ("a placeholder",)
    assert status(folder) == "open"
