"""`next`: what it prints at each step of a round's design, that each command it prints
completes the step when run, that its data and its lines say the same, that it writes
nothing, and the empty blocks it prints."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest

from moxi_rounds import (asking, dropping, freezing, log, next_step, opening,
                         report_block, ruling, steps, verdicts)
from moxi_rounds.cli import main

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
SAID = "Agent-read times follow the settings."
PTR = "comms:1234567"

FLOWS = """\
    [flows.paper]
    steps = [ { step = "design", by = "lead", model = "second" },
              { step = "verify-design", by = "checker", model = "second" } ]

    [flows.read-first]
    steps = [
      { step = "design", by = "lead", reads = ["the notes of the last round", "the survey"] },
      { step = "verify-design", by = "checker", model = "second", reads = ["the rulings"] },
    ]
    """
ONE_HARNESS = """\
    [models]
    second = { claude = "model-two" }
    third = { claude = "model-three" }
    """
TWO_HARNESSES = """\
    [models]
    first = { claude = "model-one", codex = "model-a" }
    second = { claude = "model-two", codex = "model-b" }
    third = { claude = "model-three", codex = "model-c" }
    """
TRACED = """\
    [projects.proj-a]
    trace = true
    """


@pytest.fixture
def opened(tmp_path, add_project, write_config, capsys):
    """`opened(flow="full", config=...)`: open `a-round` of `proj-a` on that flow and
    return its folder."""
    add_project("proj-a", notes_root=tmp_path / "notes")

    def open_on(flow: str = "full", config: str = ""):
        write_config(FLOWS + config)
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


def stage(id_, repo="proj-a") -> str:
    return "\n".join(["[[stage]]", f'id = "{id_}"', f'repo = "{repo}"',
                      f'branch = "branch-{id_.lower()}"', "after = []", "",
                      "  [[stage.slice]]", '  id = "1a"', '  what = "a piece"',
                      "  minutes = 40"])


def row(id_) -> str:
    return "\n".join(["[[trace]]", f'id = "{id_}"', 'as = "carried"',
                      'where = "the index"'])


_drafts = iter(range(1, 1000))


def design(folder, *entries) -> None:
    text = (f"# A design\n\nDraft {next(_drafts)}.\n\n```toml rounds\n"
            + "\n\n".join(entries) + "\n```\n")
    (folder / "design.md").write_text(text, encoding="utf-8")


def fold(folder, *entries):
    design(folder, *entries)
    return freezing.freeze(ROUND, actor="scout")


_reports = iter(range(1, 1000))


def report(folder, word="fold-first", *, version=1, pass_="full",
           model="a-model") -> Path:
    count = next(_reports)
    keys = ['kind = "design"', f'verdict = "{word}"', f'pass = "{pass_}"',
            'by = "critic"', f'model = "{model}"', f"design = {version}"]
    path = folder / f"report-{count}.md"
    path.write_text(f"# A report\n\nPass {count}.\n\n```toml rounds\n"
                    + "\n".join(keys) + "\n```\n", encoding="utf-8")
    return path


def verdict(folder, word="fold-first", **how):
    return verdicts.record(ROUND, report(folder, word, **how), actor="critic")


def rule(item=None, text=SAID, **more) -> str:
    return ruling.rule(ROUND, item=item, record=PTR, actor="planner", text=text,
                       **more).id


def run(capsys, *argv) -> str:
    assert main(list(argv)) == 0
    return capsys.readouterr().out


def lines(capsys) -> list[str]:
    """What `next` prints for the round, in lines; its data says the same."""
    text = run(capsys, "next", ROUND)
    as_data = json.loads(run(capsys, "next", ROUND, "--json"))
    assert next_step.as_text(as_data) == text
    return text.splitlines()


def said(printed: list[str], label: str) -> list[str]:
    """What follows `label` on each line of `printed` that starts with it."""
    return [line[len(label):].strip() for line in printed if line.startswith(label)]


def do(capsys, command: str, **filled) -> str:
    """Run a command as `next` printed it, each placeholder in `filled` put in."""
    for word, value in filled.items():
        if " " in word:
            command = command.replace(word, shlex.quote(value))
    argv = [filled.get(word, word) for word in shlex.split(command)]
    assert argv[:2] == ["moxi", "rounds"]
    return run(capsys, *argv[2:])


def at(folder):
    found = steps.current(_folded(folder), name=ROUND)
    return None if found is None else (found.step, found.stage, found.state)


def _folded(folder):
    from moxi_rounds import state

    return state.fold(log.read(log.round_log(folder)))


def files_under(*roots) -> dict:
    """Every file under the roots, with its bytes and the time it was last written."""
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for root in roots for path in sorted(Path(root).rglob("*"))
            if path.is_file()}


# -- a round's design, step by step ------------------------------------------------------

def test_the_design_step_names_who_writes_it_the_file_and_the_command(folder, capsys):
    printed = lines(capsys)

    assert printed == [
        f"round {ROUND}   flow full   step design   by lead, tier second",
        "reads: nothing named",
        f"write: design.md in {folder}",
        f"then:  moxi rounds fold {ROUND}",
        "waits: nothing"]


def test_each_command_printed_completes_its_step_through_a_round_s_design(
        folder, capsys):
    design(folder, question("Q1"), stage("B1"))
    do(capsys, said(lines(capsys), "then:")[0])
    assert at(folder) == ("verify-design", None, "ready")

    first = folder / "versions" / "design-v1.md"
    printed = lines(capsys)
    assert printed == [
        f"round {ROUND}   flow full   step verify-design   by checker, tier second",
        "pass:  full of v1: no full pass over the design is recorded",
        f"reads: {first}",
        f"write: a report in {folder} with the block from: moxi rounds next --schema "
        "design",
        f"then:  moxi rounds verdict {ROUND} <your report>",
        "waits: nothing",
        "",
        f"round {ROUND}   flow full   stage B1   step commission   not built in this "
        "version",
        f"waits: Q1 holds B1: {ASKED}",
        f"  fix: moxi rounds rule {ROUND} Q1 --record PTR"]
    do(capsys, said(printed, "then:")[0],
       **{"<your report>": str(report(folder, "fold-first"))})

    printed = lines(capsys)
    assert printed[:4] == [
        f"round {ROUND}   flow full   step verify-design   by checker, tier third",
        "pass:  re-check: v1 was sent back and the design was not frozen since: a "
        "re-check reads what changed",
        f"do:    moxi rounds fold {ROUND}",
        "waits: nothing"]
    design(folder, question("Q1"), stage("B1"))
    do(capsys, said(printed, "do:")[0])

    second = folder / "versions" / "design-v2.md"
    printed = lines(capsys)
    assert printed[:6] == [
        f"round {ROUND}   flow full   step verify-design   by checker, tier third",
        "pass:  re-check of v2: the design changed since v1, the last version a pass "
        "read",
        f"reads: the diff from {first} to {second}, and the sections that depend on it",
        f"write: a report in {folder} with the block from: moxi rounds next --schema "
        "design",
        f"then:  moxi rounds verdict {ROUND} <your report>",
        "waits: nothing"]
    do(capsys, said(printed, "then:")[0], **{"<your report>": str(report(
        folder, "publishable", version=2, pass_="re-check"))})

    printed = lines(capsys)
    assert printed[:5] == [
        f"round {ROUND}   flow full   step docket   by operator",
        "questions: Q1",
        f"export: moxi rounds list {ROUND} --questions",
        f"then:  moxi rounds rule {ROUND} Q1 --text TEXT --record PTR",
        "waits: nothing"]
    assert ASKED in do(capsys, said(printed, "export:")[0])
    do(capsys, said(printed, "then:")[0], TEXT=SAID, PTR=PTR)

    printed = lines(capsys)
    assert printed[:2] == [
        f"round {ROUND}   flow full   stage B1   step commission   not built in this "
        "version",
        "waits: nothing"]


def test_a_design_that_builds_nothing_ends_at_no_step(opened, capsys):
    folder = opened("paper")
    design(folder)
    do(capsys, said(lines(capsys), "then:")[0])
    do(capsys, said(lines(capsys), "then:")[0],
       **{"<your report>": str(report(folder, "publishable"))})

    assert lines(capsys) == [
        f"round {ROUND}   flow paper   step none: every step of the flow is done or "
        "skipped"]


def test_a_question_that_offers_options_is_answered_by_choosing_one(folder, capsys):
    fold(folder, question("Q1", options='{ a = "yes", b = "no" }'), question("Q2"))
    verdict(folder, "publishable")

    printed = lines(capsys)

    assert said(printed, "questions:") == ["Q1, Q2"]
    assert said(printed, "then:") == [
        f"moxi rounds rule {ROUND} Q1 --chose K --record PTR",
        f"moxi rounds rule {ROUND} Q2 --text TEXT --record PTR"]
    do(capsys, said(printed, "then:")[0], K="a", PTR=PTR)
    do(capsys, said(printed, "then:")[1], TEXT=SAID, PTR=PTR)
    assert at(folder) != ("docket", None, "ready")


def test_what_a_step_s_entry_says_to_read_is_printed(opened, capsys):
    folder = opened("read-first")

    assert said(lines(capsys), "reads:") == ["the notes of the last round; the survey"]

    fold(folder)
    assert said(lines(capsys), "reads:") == [
        f"{folder / 'versions' / 'design-v1.md'}; the rulings"]


def test_after_two_verdicts_that_sent_the_design_back_no_pass_is_asked_for(
        folder, capsys):
    fold(folder)
    verdict(folder)
    fold(folder)
    verdict(folder, version=2, pass_="re-check")

    printed = lines(capsys)

    mark = (f"moxi rounds mark {ROUND} thread:NAME --close --record PTR, or "
            "--accept-risk in place of --close; PTR names where the lead wrote why")
    assert printed == [
        f"round {ROUND}   flow full   step verify-design   by checker",
        "pass:  none: the last 2 verdicts of the design each sent it back, so no "
        "further pass is asked for",
        "do:    state the rule that the findings of the two passes break, and write "
        "the section again from that rule",
        f"do:    moxi rounds fold {ROUND}",
        f"do:    {mark}",
        "waits: nothing"]

    fold(folder)
    printed = lines(capsys)
    assert said(printed, "do:") == [mark]
    do(capsys, said(printed, "do:")[0].split(", or ")[0], NAME="a-thread", PTR=PTR)
    assert at(folder)[0] != "verify-design"


def test_a_version_whose_trace_rows_fail_is_waited_for_with_the_pass_named(
        opened, capsys):
    folder = opened(config=TRACED)
    rule()
    fold(folder)

    printed = lines(capsys)

    assert printed[1] == "pass:  full of v1: no full pass over the design is recorded"
    assert printed[-4:-2] == [
        "waits: trace: v1 fails: no row for C1; a version whose rows pass is waited "
        "for",
        f"  fix: give design.md the rows, then moxi rounds fold {ROUND}"]


def test_a_flow_with_no_questions_step_prints_the_hold_on_each_stage(opened, capsys):
    folder = opened("small")
    fold(folder, question("Q1", blocks='["B2"]'), stage("B1"),
         stage("B2", repo="proj-b"))

    printed = lines(capsys)

    head = f"round {ROUND}   flow small   stage"
    assert printed == [
        f"{head} B1   step commission   not built in this version",
        "waits: nothing",
        "",
        f"{head} B2   step commission   not built in this version",
        f"waits: Q1 holds B2: {ASKED}",
        f"  fix: moxi rounds rule {ROUND} Q1 --record PTR"]


def test_a_round_that_ended_is_at_no_step(folder, capsys):
    dropping.drop(ROUND, by="operator", record=PTR, actor="planner")
    capsys.readouterr()

    assert lines(capsys) == [
        f"round {ROUND}   flow full   step none: the round is dropped"]


def test_a_round_that_cant_be_read_is_printed_as_unreadable(folder, capsys):
    with log.round_log(folder).open("ab") as written:
        written.write(b"not an event\n")

    printed = lines(capsys)

    assert printed[:2] == [f"round {ROUND}   unreadable", f"folder: {folder}"]
    found = json.loads(run(capsys, "next", ROUND, "--json"))
    assert set(found) == {"round", "folder", "notice", "unreadable"}
    assert found["unreadable"]["line"] == 2


# -- the data ----------------------------------------------------------------------------

def test_the_data_holds_each_group_and_each_warning(opened, capsys):
    folder = opened(config=ONE_HARNESS)
    fold(folder, stage("B1"))
    verdict(folder)
    fold(folder, stage("B1"))
    made = rule()

    found = json.loads(run(capsys, "next", ROUND, "--json"))

    first, second = (str(folder / "versions" / f"design-v{n}.md") for n in (1, 2))
    assert found == {
        "round": ROUND, "folder": str(folder), "notice": None, "status": "open",
        "flow": "full",
        "groups": [
            {"step": "verify-design", "stage": None, "state": "ready", "built": True,
             "by": "checker", "tier": "third", "model": "model-three",
             "reads": [f"the diff from {first} to {second}, and the sections that "
                       "depend on it"],
             "write": f"a report in {folder} with the block from: moxi rounds next "
                      "--schema design",
             "then": [f"moxi rounds verdict {ROUND} <your report>"],
             "questions": [], "waits": [], "pass": "re-check",
             "why": "the design changed since v1, the last version a pass read",
             "version": 2, "since": 1, "instead": [], "files": [first, second]},
            {"step": "commission", "stage": "B1", "state": "not-built", "built": False,
             "by": None, "tier": None, "model": None, "reads": [], "write": None,
             "then": [], "questions": [], "waits": []}],
        "warnings": [
            {"what": "not-shown",
             "text": f"not yet shown to the operator: {ROUND} {made} {SAID}",
             "fix": f"moxi rounds mark {ROUND} {made} --shown --record PTR"}]}


# -- the model of a tier -----------------------------------------------------------------

def test_a_tier_the_setting_gives_one_model_is_printed_with_it(opened, capsys):
    opened(config=ONE_HARNESS)

    assert lines(capsys)[0].endswith("by lead, tier second (model-two)")


def test_a_tier_with_no_model_or_more_than_one_is_printed_alone(opened, capsys):
    opened(config=TWO_HARNESSES)

    assert lines(capsys)[0].endswith("by lead, tier second")


@pytest.mark.parametrize("models, tier, expected", [
    ({}, "second", None),
    ({"second": {"claude": "model-two"}}, "second", "model-two"),
    ({"second": {"claude": "model-two"}}, "third", None),
    ({"second": {"claude": "model-two"}}, None, None),
    ({"second": {"claude": "model-two", "codex": "model-two"}}, "second", "model-two"),
    ({"second": {"claude": "model-two", "codex": "model-b"}}, "second", None),
    ({"second": "model-two"}, "second", "model-two"),
    ({"second": {}}, "second", None),
    ("no table", "second", None),
])
def test_the_model_of_a_tier_is_the_one_name_the_table_gives_it(models, tier, expected):
    assert next_step.model_of(models, tier) == expected


# -- the warnings ------------------------------------------------------------------------

def warned(capsys, what: str) -> list[dict]:
    found = json.loads(run(capsys, "next", ROUND, "--json"))
    return [one for one in found["warnings"] if one["what"] == what]


def test_a_design_edited_since_it_was_frozen_is_warned_of(folder, capsys):
    fold(folder)
    assert warned(capsys, "design-changed") == []

    design(folder)

    assert said(lines(capsys), "warning:") == ["design.md changed since v1 was frozen"]
    assert warned(capsys, "design-changed")[0]["fix"] == f"moxi rounds fold {ROUND}"


def test_a_design_never_frozen_is_not_warned_of(folder, capsys):
    design(folder)

    assert warned(capsys, "design-changed") == []


def test_a_newest_report_by_a_model_below_its_tier_is_warned_of(opened, capsys):
    folder = opened(config=TWO_HARNESSES)
    fold(folder)
    verdict(folder, model="model-c")

    assert [one["text"] for one in warned(capsys, "model-below-tier")] == [
        "the newest report of the design, of v1, says model-c wrote it, a model "
        "below the tier its pass is held to"]

    fold(folder)
    verdict(folder, "publishable", version=2, pass_="re-check", model="model-c")
    assert warned(capsys, "model-below-tier") == []


def test_a_ruling_not_yet_shown_is_warned_of_until_the_printed_mark_is_run(
        folder, capsys):
    first, second = rule(), rule(text="Times are shown in UTC.")

    printed = lines(capsys)

    assert said(printed, "warning:") == [
        f"not yet shown to the operator: {ROUND} {first} {SAID}; {ROUND} {second} "
        "Times are shown in UTC."]
    do(capsys, said(printed, "  fix:")[0], PTR="comms:7654321")
    assert warned(capsys, "not-shown") == []


def test_a_row_gone_stale_is_warned_of_and_waits_for_nothing(folder, capsys):
    first = rule()
    fold(folder, row(first))
    second = rule(text="Times are shown in UTC.", supersedes=first)

    found = warned(capsys, "stale-row")

    assert [one["text"] for one in found] == [
        "the design's row cites a ruling that stands no longer: "
        f"{first}, replaced by {second}"]
    assert said(lines(capsys), "waits:") == ["nothing"]


def test_a_ruling_no_row_cites_is_warned_of_for_each_stage_it_holds(opened, capsys):
    folder = opened(config=TRACED)
    fold(folder, stage("B1"), stage("B2", repo="proj-b"))
    made = rule(stages="B2")

    assert warned(capsys, "holds-stage") == [{
        "what": "holds-stage",
        "text": f"{made} holds B2 until a version of the design has a row for it: "
                f"{SAID}",
        "fix": f"give design.md a row for {made}, then moxi rounds fold {ROUND}"}]


# -- a later step ------------------------------------------------------------------------

def test_a_step_given_a_row_and_its_lines_is_printed_like_the_others(
        folder, capsys, monkeypatch):
    fold(folder, stage("B1"))
    verdict(folder, "publishable")
    monkeypatch.setitem(steps.ROWS, "commission", lambda _state, about: steps.Step(
        "commission", steps.READY, stage=about.stage))
    monkeypatch.setitem(next_step.LINES, "commission", lambda found, step: {
        "by": "lead", "then": [f"moxi rounds commission {found.name} {step.stage}"]})

    assert lines(capsys) == [
        f"round {ROUND}   flow full   stage B1   step commission   by lead",
        f"then:  moxi rounds commission {ROUND} B1",
        "waits: nothing"]


# -- nothing is written ------------------------------------------------------------------

def test_no_file_changes_whatever_is_printed(opened, tmp_path, capsys):
    folder = opened(config=TRACED + ONE_HARNESS)
    first = rule()
    fold(folder, question("Q1"), stage("B1"), row(first))
    verdict(folder)
    rule(text="Times are shown in UTC.", supersedes=first)
    design(folder, stage("B1"))
    roots = (tmp_path, Path.home(), os.environ["MOXI_ROUNDS_HOME"],
             os.environ["MOXI_PROJECTS_HOME"])
    before = files_under(*roots)

    for argv in (["next", ROUND], ["next", ROUND, "--json"],
                 ["next", "--schema", "design"]):
        assert main(argv) == 0
        assert capsys.readouterr().out
    assert main(["next", "proj-a/no-such-round"]) != 0

    assert files_under(*roots) == before


# -- the empty blocks --------------------------------------------------------------------

@pytest.mark.parametrize("kind", report_block.KINDS)
def test_the_empty_block_of_each_kind_of_report_is_printed(kind, capsys):
    assert run(capsys, "next", "--schema", kind) == report_block.empty(kind)


def test_the_block_of_a_prepared_answer_is_not_built(capsys):
    assert main(["next", "--schema", "answer"]) == 2

    err = capsys.readouterr().err
    assert "the block of a prepared answer is not built in this version" in err
    assert "name one of design, build, step, majors, retro" in err


def test_a_word_that_is_no_kind_of_block_is_bad_arguments(capsys):
    assert main(["next", "--schema", "sketch"]) == 2

    assert "'sketch' is no kind of report" in capsys.readouterr().err


# -- which round -------------------------------------------------------------------------

def test_with_no_round_it_says_a_round_is_needed(capsys):
    assert main(["next"]) == 2

    err = capsys.readouterr().err
    assert "a round is needed" in err
    assert "moxi rounds next <project>/<slug>" in err


def test_a_project_s_key_is_bad_arguments_and_names_the_listing(folder, capsys):
    assert main(["next", "proj-a"]) == 2

    assert "moxi rounds list proj-a" in capsys.readouterr().err
