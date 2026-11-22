"""A round closes itself: what its close waits for, the write that closes it, a close
that stopped short, and what a closed round is from then on."""

from __future__ import annotations

import json

import pytest

from moxi_rounds import (api, asking, closing, dropping, freezing, holds, log, numbers,
                         opening, rendering, ruling, settling, state, verdicts)
from moxi_rounds.cli import main
from moxi_rounds.errors import Refused

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
PTR = "comms:1234567"
SHOWN = "comms:1234599"

FLOWS = """\
    [flows.paper]
    steps = [ { step = "design", by = "lead", model = "second" },
              { step = "verify-design", by = "checker", model = "second" } ]

    [flows.sketch]
    steps = [ { step = "design", by = "lead" } ]

    [flows.empty]
    steps = []

    [flows.studied]
    steps = [ { step = "design", by = "lead" },
              { step = "verify-design", by = "checker", model = "second" },
              { step = "retro", by = "worker" } ]

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
        opening.open_round(ROUND, description="a placeholder round", by="planner",
                           flow=flow)
        capsys.readouterr()
        return tmp_path / "notes" / "rounds" / "a-round"

    return open_on


@pytest.fixture(name="round_dir")
def round_dir_fixture(opened):
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


def fold(round_dir, *entries):
    text = (f"# A design\n\nDraft {next(_folds)}.\n\n```toml rounds\n"
            + "\n\n".join(entries) + "\n```\n")
    (round_dir / "design.md").write_text(text, encoding="utf-8")
    return freezing.freeze(ROUND, by="scout")


_reports = iter(range(1, 1000))


def verdict(round_dir, word="publishable", *, design=1):
    count = next(_reports)
    keys = ['kind = "design"', f'verdict = "{word}"', 'pass = "full"',
            'by = "critic"', 'model = "a-model"', f"design = {design}"]
    path = round_dir / f"report-{count}.md"
    path.write_text(f"# A report\n\nPass {count}.\n\n```toml rounds\n"
                    + "\n".join(keys) + "\n```\n", encoding="utf-8")
    return verdicts.record(ROUND, path, by="critic")


def rule(item=None, **more):
    return ruling.rule(ROUND, item=item, record=PTR, by="planner", **more)


def events(round_dir) -> list[dict]:
    return log.read(log.round_log(round_dir))


def types(round_dir) -> list[str]:
    return [event["type"] for event in events(round_dir)]


def folded(round_dir):
    return state.fold(events(round_dir))


def status(round_dir) -> str:
    return folded(round_dir).status


def waits(round_dir) -> tuple[str, ...]:
    return closing.waits(folded(round_dir))


def run(capsys, *argv) -> str:
    assert main(list(argv)) == 0
    return capsys.readouterr().out


# -- the write that closes a round -------------------------------------------------------

def test_a_round_that_builds_nothing_closes_on_the_verdict_that_verifies_its_design(
        round_dir):
    fold(round_dir)
    assert status(round_dir) == "open"
    assert waits(round_dir) == ("step verify-design, ready",)

    verdict(round_dir)

    assert types(round_dir)[-2:] == ["verdict.recorded", "round.closed"]
    assert status(round_dir) == "closed"


def test_the_closing_event_is_the_verb_s_own_and_holds_the_round_s_numbers(round_dir):
    fold(round_dir)
    verdict(round_dir)

    recorded, closed = events(round_dir)[-2:]

    assert closed == {"v": 1, "n": recorded["n"] + 1, "at": closed["at"],
                      "actor": "critic", "type": "round.closed",
                      "numbers": numbers.of(state.fold(events(round_dir)[:-1]),
                                            ended_at=closed["at"])}
    assert closed["numbers"]["design_passes"] == 1 and closed["numbers"]["folds"] == 1


def test_a_verdict_that_sends_the_design_back_closes_nothing(round_dir):
    fold(round_dir)
    verdict(round_dir, "fold-first")

    assert status(round_dir) == "open"
    assert "round.closed" not in types(round_dir)


def test_a_round_with_a_question_open_closes_on_the_ruling_that_answers_the_last_one(
        round_dir):
    fold(round_dir, question("Q1", blocks='"none"'), question("Q2", blocks='"none"'))
    verdict(round_dir)
    assert waits(round_dir) == ("question Q1, open", "question Q2, open")

    rule("Q1", text="They do.")
    assert status(round_dir) == "open"
    rule("Q2", text="They do not.")

    assert types(round_dir)[-2:] == ["ruling.recorded", "round.closed"]
    assert status(round_dir) == "closed"


def test_a_parked_question_keeps_a_round_open_until_it_is_ruled(round_dir):
    fold(round_dir, question("Q1", blocks='"none"'))
    verdict(round_dir)
    rule("park:Q1", until="round:proj-a/another")

    assert waits(round_dir) == ("question Q1, parked",)
    assert status(round_dir) == "open"

    rule("Q1", text="They do.")
    assert status(round_dir) == "closed"


def test_a_question_asked_after_the_design_is_what_the_close_waits_for(round_dir):
    fold(round_dir)
    asked = asking.ask(ROUND, ASKED, origin="comms:11", blocks="none", by="scout").id
    verdict(round_dir)

    assert waits(round_dir) == (f"question {asked}, open",)
    rule(f"withdraw:{asked}")
    assert status(round_dir) == "closed"


def test_a_skip_of_the_design_s_verification_closes_a_round_whose_design_is_frozen(
        round_dir):
    fold(round_dir)

    rule("skip:verify-design")

    assert types(round_dir)[-2:] == ["ruling.recorded", "round.closed"]
    assert status(round_dir) == "closed"


def test_a_skip_recorded_before_the_design_closes_on_the_freezing(round_dir):
    rule("skip:verify-design")
    assert waits(round_dir) == ("step design, ready",)

    fold(round_dir)

    assert types(round_dir)[-2:] == ["design.frozen", "round.closed"]


def test_a_round_whose_flow_ends_in_a_retro_closes_on_the_skip_of_it(opened):
    round_dir = opened("review")
    fold(round_dir)
    assert status(round_dir) == "open"
    assert waits(round_dir) == ("step retro, ready",)

    rule("skip:retro")

    assert types(round_dir)[-2:] == ["ruling.recorded", "round.closed"]
    assert status(round_dir) == "closed"


def test_a_retro_skipped_before_the_design_closes_on_the_freezing(opened):
    round_dir = opened("review")
    rule("skip:retro")
    assert waits(round_dir) == ("step design, ready",)

    fold(round_dir)

    assert types(round_dir)[-2:] == ["design.frozen", "round.closed"]


def test_a_skipped_retro_reads_the_same_in_every_place_that_shows_it(opened, capsys):
    round_dir = opened("review")
    fold(round_dir, question("Q1", blocks='"none"'))
    before = run(capsys, "show", ROUND)
    # Not due while the question holds the close.
    assert "step: retro, not-due\n" in before
    assert "close waits for: step retro, not-due; question Q1, open\n" in before
    assert run(capsys, "next", ROUND).splitlines()[0] == (
        f"round {ROUND}   flow review   step retro   by worker")

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
    assert waits(round_dir) == ("question Q1, open",)
    assert api.round_data(ROUND)["step"] is None


def test_a_skip_of_the_retro_taken_back_keeps_the_round_open_again(opened, capsys):
    round_dir = opened("review")
    fold(round_dir, question("Q1", blocks='"none"'))
    rule("skip:retro")

    rule("revoke:C1")

    assert waits(round_dir) == ("step retro, not-due", "question Q1, open")
    assert "step: retro, not-due\n" in run(capsys, "show", ROUND)
    rule("Q1", text="They do.")
    assert status(round_dir) == "open"


def test_a_flow_that_is_a_design_alone_closes_on_its_freezing(opened):
    round_dir = opened("sketch")
    assert status(round_dir) == "open"

    fold(round_dir)

    assert status(round_dir) == "closed"


def test_a_stage_in_the_plan_keeps_a_round_that_builds_nothing_open(round_dir):
    fold(round_dir, stage("B1"))
    verdict(round_dir)
    assert waits(round_dir) == ("stage B1, not finished",)
    assert status(round_dir) == "open"

    rule("unstage:B1")

    assert status(round_dir) == "closed"


# -- a flow that builds ------------------------------------------------------------------

BASE = "0" * 40
TIP = "1" * 40


def built(round_dir, *, finished=True, decisions=(), report="build"):
    """B1 declared, commissioned and, when `finished`, finished, through the log, with
    a report of kind `report` (none when None) listing `decisions` (`(id, weight)`)."""
    path = log.round_log(round_dir)
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               by="planner")
    log.append(path, "build.commissioned",
               {"stage": "B1", "repo": "proj-a", "branch": "work", "base": BASE,
                "target": "main", "builder": "coder", "build": "0123456789abcdef"},
               by="planner")
    listed = [{"id": id_, "text": "a choice", "weight": weight, "forecloses": False}
              for id_, weight in decisions]
    if report == "build":
        log.append(path, "verdict.recorded",
                   {"kind": "build", "verdict": "publishable", "pass": "full",
                    "stage": "B1", "head": TIP, "by": "critic", "decisions": listed},
                   by="critic")
    elif report == "majors":
        log.append(path, "verdict.recorded",
                   {"kind": "majors", "stages": ["B1"], "by": "critic",
                    "decisions": [{**one, "stage": "B1"} for one in listed]},
                   by="critic")
    if finished:
        log.append(path, "finish.done", {"stage": "B1", "tip": TIP}, by="coder")


def test_a_round_that_builds_waits_for_its_open_build(opened):
    round_dir = opened("fix")
    built(round_dir, finished=False)
    assert {"stage B1, not finished", "build of B1, open"} <= set(waits(round_dir))


def test_a_round_that_builds_with_nothing_planned_waits_for_a_finished_stage(opened):
    round_dir = opened("fix")
    assert "a finished stage: the plan holds none" in waits(round_dir)


def test_a_finished_build_whose_report_declares_no_decision_leaves_nothing_to_wait_for(
        opened):
    round_dir = opened("fix")
    built(round_dir)
    assert waits(round_dir) == ()


@pytest.mark.parametrize("report", [None, "majors"])
def test_a_finished_build_with_no_report_declaring_its_decisions_holds_the_close(
        opened, report):
    round_dir = opened("fix")
    built(round_dir, report=report)
    if report is None:
        assert waits(round_dir) == ("a report that declares the decisions of B1's build: a "
                                 "build report, or a majors report naming B1",)
        assert closing.waits(folded(round_dir), ratify="off") == ()
    else:
        assert waits(round_dir) == ()


def test_an_unruled_major_and_an_unsettled_minor_hold_the_close_until_each_is_done(
        opened):
    round_dir = opened("fix")
    built(round_dir, decisions=[("M1", "major"), ("M2", "minor")])
    assert waits(round_dir) == ("decision B1.M1, major and not yet ruled",
                                "decision B1.M2, minor, not yet settled by the lead")

    rule("B1.M1=accept")
    assert waits(round_dir) == ("decision B1.M2, minor, not yet settled by the lead",)
    settling.settle(ROUND, "B1.M2", "accept", record=PTR, by="planner")

    assert status(round_dir) == "closed"


@pytest.mark.parametrize("past", ["off", "skip"])
def test_a_ratify_setting_of_off_or_a_skip_of_ratify_lets_an_unruled_major_past(
        opened, write_config, past):
    round_dir = opened("fix")
    built(round_dir, decisions=[("M1", "major")])
    if past == "off":
        write_config(FLOWS + '\n[defaults]\nratify = "off"\n')
        assert closing.waits(folded(round_dir), ratify="off") == ()
        # The next write reads the setting and closes the round.
        rule(text="a placeholder ruling")
    else:
        rule("skip:ratify")
    assert status(round_dir) == "closed"


def test_a_round_with_a_retro_step_waits_for_it_after_its_build(opened):
    round_dir = opened("small")
    fold(round_dir)
    built(round_dir)
    assert waits(round_dir) == ("step retro, ready",)


# -- which decisions are his to rule, and what a close leaves open ----------------------

TWO = [("M1", "major"), ("M2", "minor")]


def ratify(write_config, setting):
    write_config(FLOWS + f'\n[defaults]\nratify = "{setting}"\n')


def facts(capsys) -> dict[str, str]:
    """Each open decision of the round, by id, with the line under it that says whether
    it is his to rule, as show writes it; and the same from show's data."""
    shown = run(capsys, "show", ROUND).splitlines()
    data = {one["id"]: one for one in json.loads(run(capsys, "show", ROUND,
                                                     "--json"))["decisions"]
            if one["state"] == "open"}
    said = {}
    for at, line in enumerate(shown):
        for key in data:
            if line.startswith(f"  decision {key}:"):
                said[key] = shown[at + 2].strip()
    assert {key: one.get("open_as") for key, one in data.items()} == said
    return said


def rule_of(key):
    return f"moxi rounds rule {ROUND} {key}=accept|reject --record PTR"


def settle_of(key):
    return (f"moxi rounds mark {ROUND} {key} --accept, --reject or --major --record "
            "PTR")


def label(key, why, weight):
    """The line under an open decision of a finished build, by the one rule."""
    if why is None:
        return f"the operator's to rule: {rule_of(key)}"
    if weight == "minor":
        return f"the lead's to settle: {settle_of(key)}"
    return (f"not put to the operator ({why}); recorded only if the operator rules it: "
            f"{rule_of(key)}")


@pytest.mark.parametrize("setting, m1, m2", [
    ("majors", None, "ratify is majors"),
    ("majors+minors", None, None),
    ("off", "ratify is off", "ratify is off")])
def test_a_decision_is_the_operator_s_to_rule_only_where_the_ratify_setting_puts_it(
        opened, write_config, capsys, setting, m1, m2):
    round_dir = opened("fix")
    ratify(write_config, setting)
    built(round_dir, decisions=TWO)

    said = facts(capsys)

    assert said["B1.M1"] == label("B1.M1", m1, "major")
    assert said["B1.M2"] == label("B1.M2", m2, "minor")


def test_a_decision_of_a_build_not_finished_is_not_yet_put_to_the_operator(
        opened, capsys):
    round_dir = opened("fix")
    built(round_dir, finished=False, decisions=[("M1", "major")])

    assert facts(capsys)["B1.M1"] == (
        f"not yet put to the operator (stage B1 is not finished): {rule_of('B1.M1')}")
    listed = run(capsys, "list", "--waiting")
    assert (f"    not yet put to the operator (stage B1 is not finished): "
            f"{rule_of('B1.M1')}\n") in listed


def test_a_decision_only_a_dropped_build_declared_is_not_the_operator_s_to_rule(
        opened, capsys):
    round_dir = opened("fix")
    built(round_dir, finished=False, decisions=[("M1", "major")])
    path = log.round_log(round_dir)
    log.append(path, "build.dropped", {"stage": "B1", "by": "lead", "record": PTR},
               by="planner")
    log.append(path, "build.commissioned",
               {"stage": "B1", "repo": "proj-a", "branch": "work", "base": BASE,
                "target": "main", "builder": "coder", "build": "fedcba9876543210"},
               by="planner")
    log.append(path, "verdict.recorded",
               {"kind": "build", "verdict": "publishable", "pass": "full",
                "stage": "B1", "head": TIP, "by": "critic", "decisions": []},
               by="critic")
    log.append(path, "finish.done", {"stage": "B1", "tip": TIP}, by="coder")

    assert facts(capsys)["B1.M1"] == label(
        "B1.M1", "the build that finished B1 did not declare it", "major")
    # It never reached the target, so nothing waits for it.
    assert waits(round_dir) == ()


@pytest.mark.parametrize("setting", ["majors", "off"])
def test_the_project_list_says_the_operator_s_to_rule_only_where_it_is_put(
        opened, write_config, capsys, setting):
    round_dir = opened("fix")
    ratify(write_config, setting)
    built(round_dir, decisions=[("M1", "major")])

    listed = run(capsys, "list", "--waiting")
    [row] = json.loads(run(capsys, "list", "--waiting", "--json"))["decisions"]

    if setting == "majors":
        assert f"    {label('B1.M1', None, 'major')}\n" in listed
        assert "not_put" not in row
    else:
        assert f"    {label('B1.M1', 'ratify is off', 'major')}\n" in listed
        assert row["not_put"] == "ratify is off"


def test_the_close_lists_each_decision_it_leaves_open_and_show_keeps_the_list(
        opened, write_config, capsys):
    round_dir = opened("fix")
    ratify(write_config, "off")
    built(round_dir, decisions=TWO)

    # The lead's settlement of the minor one is the write that closes the round.
    printed = run(capsys, "mark", ROUND, "B1.M2", "--accept", "--record", PTR)

    left = "left open, not put to the operator: decision B1.M1, major: a choice"
    assert printed.splitlines()[-2:] == [CLOSED_LINE, f"  {left}"]
    shown = run(capsys, "show", ROUND)
    assert f"\n  {left}\n" in shown.split("ended: ", 1)[1].split("numbers:", 1)[0]
    ended = json.loads(run(capsys, "show", ROUND, "--json"))["ended"]
    assert ended["left_open"] == [{"id": "B1.M1", "weight": "major",
                                   "forecloses": False, "text": "a choice",
                                   "put": False}]
    # An ended round takes no ruling: no command is offered under it.
    assert facts(capsys)["B1.M1"] == "left open, not put to the operator (ratify is off)"


def test_a_close_past_a_skipped_ratify_lists_a_major_as_not_put_to_the_operator(
        opened, capsys):
    round_dir = opened("fix")
    built(round_dir, decisions=[("M1", "major")])

    printed = run(capsys, "rule", ROUND, "skip:ratify", "--record", PTR)

    assert printed.splitlines()[-1] == (
        "  left open, not put to the operator: decision B1.M1, major: a choice")
    assert facts(capsys)["B1.M1"] == (
        "left open, not put to the operator (ratify is skipped)")


def test_a_close_under_ratify_off_says_a_major_was_not_put_to_the_operator(
        opened, write_config, capsys):
    round_dir = opened("fix")
    ratify(write_config, "off")
    built(round_dir, decisions=[("M1", "major")])

    printed = run(capsys, "rule", ROUND, "--text", "a placeholder ruling", "--record",
                  PTR)

    assert printed.splitlines()[-2:] == [
        CLOSED_LINE,
        "  left open, not put to the operator: decision B1.M1, major: a choice"]


# -- the verb whose write closes a round says so -----------------------------------------

CLOSED_LINE = f"closed {ROUND}: nothing is left for its close to wait for"


def report(round_dir, word="publishable"):
    path = round_dir / "a-report.md"
    path.write_text('# A report\n\n```toml rounds\nkind = "design"\n'
                    f'verdict = "{word}"\npass = "full"\nby = "critic"\n'
                    'model = "a-model"\ndesign = 1\n```\n', encoding="utf-8")
    return str(path)


def test_the_verdict_that_closes_a_round_says_so_in_its_last_line(round_dir, capsys):
    fold(round_dir)

    printed = run(capsys, "verdict", ROUND, report(round_dir)).splitlines()

    assert printed[-1] == CLOSED_LINE
    assert printed.count(CLOSED_LINE) == 1


def test_a_verdict_that_closes_nothing_does_not_say_closed(round_dir, capsys):
    fold(round_dir)

    printed = run(capsys, "verdict", ROUND, report(round_dir, "fold-first"))

    assert CLOSED_LINE not in printed.splitlines()
    assert status(round_dir) == "open"


def test_the_ruling_that_answers_the_last_question_says_it_closed_the_round(
        opened, capsys):
    round_dir = opened("sketch")
    (round_dir / "design.md").write_text(
        "# A design\n\n```toml rounds\n" + question("Q1", blocks='"none"')
        + "\n```\n", encoding="utf-8")
    assert CLOSED_LINE not in run(capsys, "fold", ROUND).splitlines()
    assert run(capsys, "rule", ROUND, "Q1", "--text", "They do.", "--record",
               PTR).splitlines()[-1] == CLOSED_LINE


def test_the_freezing_that_closes_a_round_says_so(opened, capsys):
    round_dir = opened("sketch")
    (round_dir / "design.md").write_text("# A design\n\n```toml rounds\n```\n",
                                      encoding="utf-8")

    assert run(capsys, "fold", ROUND).splitlines()[-1] == CLOSED_LINE


def test_the_skip_that_closes_a_round_says_so(round_dir, capsys):
    fold(round_dir)

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


def test_a_write_to_a_round_already_closed_does_not_say_closed_again(round_dir, capsys):
    fold(round_dir)
    rule("skip:verify-design")

    printed = run(capsys, "rule", ROUND, "revoke:C1", "--record", PTR)

    assert CLOSED_LINE not in printed.splitlines()


def test_a_verb_that_is_refused_says_nothing_of_a_close(round_dir, capsys):
    fold(round_dir)
    verdict(round_dir)

    assert main(["ask", ROUND, ASKED, "--from", PTR]) != 0

    assert "closed proj-a/a-round:" not in capsys.readouterr().out


def test_only_the_writes_made_while_watching_are_collected(round_dir):
    from moxi_rounds import write

    fold(round_dir)
    with write.watching() as writes:
        rule("skip:verify-design")
    rule("revoke:C1")

    assert [one.closed["type"] for one in writes] == ["round.closed"]


# -- a close that stopped short ----------------------------------------------------------

def test_a_close_that_failed_is_reported_and_made_by_the_next_write(
        round_dir, monkeypatch, capsys):
    fold(round_dir)
    with monkeypatch.context() as patched:
        def fails(_state, **_given):
            raise OSError("no room")
        patched.setattr(closing, "entry", fails)
        made = rule("skip:verify-design")

    assert [str(error) for _what, error in made.failed] == ["no room"]
    assert types(round_dir)[-1] == "ruling.recorded" and status(round_dir) == "open"
    assert ("close waits for: nothing; the next write to the round closes it\n"
            in run(capsys, "show", ROUND))

    run(capsys, "mark", ROUND, made.id, "--shown", "--record", SHOWN)

    assert types(round_dir)[-3:] == ["ruling.recorded", "ruling.shown", "round.closed"]
    assert status(round_dir) == "closed"


def test_a_verb_stopped_before_the_close_leaves_it_to_the_next_write(
        round_dir, monkeypatch, capsys):
    fold(round_dir)
    with monkeypatch.context() as patched:
        def stops(_state, **_given):
            raise KeyboardInterrupt
        patched.setattr(closing, "entry", stops)
        with pytest.raises(KeyboardInterrupt):
            rule("skip:verify-design")
    assert types(round_dir)[-1] == "ruling.recorded" and status(round_dir) == "open"

    run(capsys, "mark", ROUND, "C1", "--shown", "--record", SHOWN)

    assert types(round_dir)[-1] == "round.closed"


def test_a_write_that_appends_nothing_still_makes_a_close_that_stopped_short(
        round_dir, monkeypatch, capsys):
    fold(round_dir)
    with monkeypatch.context() as patched:
        def fails(_state, **_given):
            raise OSError("no room")
        patched.setattr(closing, "entry", fails)
        rule("skip:verify-design")
        run(capsys, "mark", ROUND, "C1", "--shown", "--record", SHOWN)
    assert types(round_dir)[-1] == "ruling.shown" and status(round_dir) == "open"
    before = len(events(round_dir))

    # The ruling is marked as shown already, so this verb has nothing of its own to
    # record.
    printed = run(capsys, "mark", ROUND, "C1", "--shown", "--record", SHOWN)

    assert types(round_dir)[before:] == ["round.closed"]
    assert printed.splitlines()[-1] == CLOSED_LINE


def test_every_step_of_the_flow_keeps_a_round_open_not_only_the_design_s(
        opened, capsys):
    round_dir = opened("studied")
    fold(round_dir)

    verdict(round_dir)

    assert types(round_dir)[-1] == "verdict.recorded" and status(round_dir) == "open"
    assert waits(round_dir) == ("step retro, ready",)
    rule("skip:retro")
    assert status(round_dir) == "closed"


# -- a closed round ----------------------------------------------------------------------

@pytest.fixture
def closed(round_dir):
    fold(round_dir)
    rule("skip:verify-design")
    assert status(round_dir) == "closed"
    return round_dir


def test_a_closed_round_takes_no_new_work(closed):
    with pytest.raises(Refused) as refused:
        asking.ask(ROUND, ASKED, origin="comms:11", by="scout")
    assert "was closed and stays as it ended" in str(refused.value)
    with pytest.raises(Refused):
        fold(closed)
    with pytest.raises(Refused):
        rule(text="A new ruling.")
    with pytest.raises(Refused):
        dropping.drop(ROUND, by="operator", record=PTR, run_by="planner")


def test_a_write_to_a_closed_round_does_not_close_it_again(closed, capsys):
    run(capsys, "mark", ROUND, "C1", "--shown", "--record", SHOWN)

    assert types(closed).count("round.closed") == 1
    assert types(closed)[-1] == "ruling.shown"
    assert status(closed) == "closed"


def test_a_dropped_round_is_not_closed_by_a_later_write(round_dir, monkeypatch, capsys):
    fold(round_dir)
    with monkeypatch.context() as patched:
        patched.setattr(closing, "entry", lambda _state, **_given: None)
        rule("skip:verify-design")
        dropping.drop(ROUND, by="operator", record=PTR, run_by="planner")

    run(capsys, "mark", ROUND, "C1", "--shown", "--record", SHOWN)

    assert "round.closed" not in types(round_dir)
    assert status(round_dir) == "dropped"


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
                              "actor": "planner", "at": at, "questions": {},
                              "numbers": events(closed)[-1]["numbers"],
                              "left_open": []}
    assert "\nnumbers:\n  design: passes 0, folds 1 (records only 0)" in shown
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
    assert found["ended"] == {"how": "closed", "by": None, "record": None, "at": at,
                              "numbers": events(closed)[-1]["numbers"]}
    assert api.round_data(ROUND)["close"] is None
    assert api.round_data(ROUND)["ended"]["numbers"] == events(closed)[-1]["numbers"]


def test_a_closed_round_s_note_and_its_export_say_so(closed, capsys):
    at = events(closed)[-1]["at"]

    note = (closed / "round-note.md").read_text(encoding="utf-8")
    assert "status: closed\n" in note.split("---")[1]
    assert f"- status: closed\n- lead: none\n- ended: closed at {at}\n" in note
    assert "close waits for" not in note
    export = run(capsys, "list", ROUND, "--questions")
    assert (f"This round has ended: closed at {at}. Nothing of it is put to the "
            "operator.") in export
    assert run(capsys, "next", ROUND).splitlines()[0] == (
        f"round {ROUND}   flow paper   step none: the round is closed")


# -- what an open round says of its close ------------------------------------------------

def test_an_open_round_is_shown_with_what_its_close_waits_for(round_dir, capsys):
    fold(round_dir, question("Q1", blocks='"none"'), stage("B1"))

    said = "step verify-design, ready; stage B1, not finished; question Q1, open"
    assert f"close waits for: {said}\n" in run(capsys, "show", ROUND)
    assert json.loads(run(capsys, "show", ROUND, "--json"))["close"] == {
        "waits": said.split("; ")}
    assert api.round_data(ROUND)["close"] == {"waits": said.split("; ")}
    note = (round_dir / "round-note.md").read_text(encoding="utf-8")
    assert f"- close waits for: {said}\n" in note


def test_a_round_at_no_step_is_told_what_keeps_it_open(round_dir, capsys):
    fold(round_dir, question("Q1", blocks='"none"'))
    verdict(round_dir)
    rule("park:Q1", until="round:proj-a/another")

    assert run(capsys, "next", ROUND).splitlines()[:2] == [
        f"round {ROUND}   flow paper   step none: every step of the flow is done or "
        "skipped",
        "close waits for: question Q1, parked"]
    assert json.loads(run(capsys, "next", ROUND, "--json"))["close"] == {
        "waits": ["question Q1, parked"]}


def test_a_parked_question_whose_wait_is_over_has_one_state_in_what_show_prints(
        opened, capsys):
    round_dir = opened("sketch")
    opening.open_round("proj-a/another", description="a placeholder round",
                       by="planner", flow="empty")
    fold(round_dir, question("Q1", blocks='"none"'))
    rule("park:Q1", until="round:proj-a/another")

    shown = run(capsys, "show", ROUND)
    as_data = json.loads(run(capsys, "show", ROUND, "--json"))

    assert "close waits for: question Q1, open\n" in shown
    assert "question Q1, parked" not in shown
    assert as_data["close"] == {"waits": ["question Q1, open"]}
    assert [one["state"] for one in as_data["questions"]] == ["open"]
    assert json.loads(run(capsys, "next", ROUND, "--json"))["close"] == {
        "waits": ["question Q1, open"]}
    assert api.round_data(ROUND)["close"] == {"waits": ["question Q1, open"]}
    # Whether the round closes reads its own log alone, and is the same either way.
    assert waits(round_dir) == ("question Q1, parked",)
    assert status(round_dir) == "open"


def test_a_question_parked_until_a_round_still_open_is_named_as_parked(opened, capsys):
    round_dir = opened("sketch")
    opening.open_round("proj-a/another", description="a placeholder round",
                       by="planner", flow="paper")
    fold(round_dir, question("Q1", blocks='"none"'))
    rule("park:Q1", until="round:proj-a/another")

    assert "close waits for: question Q1, parked\n" in run(capsys, "show", ROUND)


def test_the_questions_the_close_waits_for_are_the_ones_the_hold_rule_gives(
        round_dir, monkeypatch):
    fold(round_dir, question("Q1", blocks='"none"'), question("Q2"), question("Q3"))
    verdict(round_dir)
    rule("park:Q2", until="round:proj-a/another")
    rule("withdraw:Q3")
    found = folded(round_dir)
    assert closing.waits(found) == tuple(
        f"question {one.id}, {one.state}" for one in holds.on_close(found))
    assert closing.waits(found) == ("question Q1, open", "question Q2, parked")

    monkeypatch.setattr(holds, "on_close", lambda state: ())

    assert closing.waits(found) == () and closing.is_due(found)


def test_what_a_close_waits_for_is_read_from_the_state_alone(round_dir):
    fold(round_dir, question("Q1", blocks='"none"'))
    found = folded(round_dir)
    (round_dir / "design.md").unlink()

    assert closing.waits(found) == ("step verify-design, ready", "question Q1, open")
    assert not closing.is_due(found) and closing.entry(found) is None


def test_a_part_added_to_the_condition_is_asked_with_the_rest(round_dir, monkeypatch):
    fold(round_dir)
    def placeholder(_state, _closed, _ratify):
        return iter(["a placeholder"])

    monkeypatch.setattr(closing, "CONDITIONS", (*closing.CONDITIONS, placeholder))

    rule("skip:verify-design")

    assert waits(round_dir) == ("a placeholder",)
    assert status(round_dir) == "open"


def test_a_closed_round_s_numbers_are_written_beside_its_note(closed):
    closing_event = events(closed)[-1]

    text = (closed / rendering.RETRO_FILE).read_text(encoding="utf-8")

    assert "\nstatus: closed\n" in text
    assert f"\n- ended: closed at {closing_event['at']}\n" in text
    assert "\n- skips 1, step-ups 0, repairs 0, corrections 0, dropped builds 0, " \
           "cleared locks 0\n" in text
    assert text == rendering.retro("proj-a", "a-round", events(closed))
