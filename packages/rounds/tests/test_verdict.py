"""`verdict`: recording a design report's verdict against the version it read, what a
publishable one takes out of the record, and the standing the verdicts give a design."""

from __future__ import annotations

import hashlib
import json

import pytest

from moxi_rounds import (dropping, freezing, index, log, opening, questions, ruling,
                         stages, state, verdicts, versions)
from moxi_rounds.cli import main
from moxi_rounds.errors import BadBlock, NoRound, Refused

ROUND = "proj-a/a-round"
ASKED = "Do agent-read times follow the settings?"
AT = "2026-01-02T03:04:05Z"


@pytest.fixture
def opened(tmp_path, add_project, capsys):
    """`opened(flow="full")`: open `a-round` of `proj-a` on that flow and return its
    folder."""
    add_project("proj-a", notes_root=tmp_path / "notes")

    def open_on(flow: str = "full"):
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


def stage(id_, *, after="[]") -> str:
    return "\n".join(["[[stage]]", f'id = "{id_}"', 'repo = "proj-a"',
                      f'branch = "branch-{id_.lower()}"', f"after = {after}"])


def fold(folder, *entries, prose="What the round builds."):
    """Write `design.md` with a block of `entries` and freeze it."""
    text = (f"# A design\n\n{prose}\n\n```toml rounds\n" + "\n\n".join(entries)
            + "\n```\n")
    (folder / "design.md").write_text(text, encoding="utf-8")
    return freezing.freeze(ROUND, actor="scout")


def finding(id_, severity="minor", state_="open", text="a line is unclear") -> str:
    return "\n".join(["[[finding]]", f'id = "{id_}"', f'severity = "{severity}"',
                      f'state = "{state_}"', f'text = "{text}"'])


def report(folder, verdict="publishable", *, design=1, pass_="full", name="report.md",
           findings=(), prose="What the pass found.", model="a-model"):
    """Write a design report in the round's folder and return its path."""
    keys = ['kind = "design"', f'verdict = "{verdict}"', f'pass = "{pass_}"',
            'by = "critic"', f'model = "{model}"', f"design = {design}"]
    text = (f"# A report\n\n{prose}\n\n```toml rounds\n" + "\n".join(keys)
            + "".join(f"\n\n{one}" for one in findings) + "\n```\n")
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def other(folder, kind) -> str:
    """A report of another kind, whole and well formed."""
    keys = {"build": ['verdict = "publishable"', 'pass = "full"', 'stage = "B1"',
                      f'head = "{"0" * 40}"'],
            "step": ['verdict = "publishable"', 'pass = "full"', 'stage = "B1"',
                     f'head = "{"0" * 40}"', 'step = "a-step"'],
            "majors": ['stages = ["B1"]'], "retro": []}[kind]
    path = folder / f"{kind}.md"
    path.write_text("```toml rounds\n" + "\n".join(
        [f'kind = "{kind}"', 'by = "critic"', 'model = "a-model"', *keys]) + "\n```\n",
        encoding="utf-8")
    return path


def record(path, **more):
    return verdicts.record(ROUND, path, actor="critic", **more)


def events(folder):
    return log.read(log.round_log(folder))


def folded(folder):
    return state.fold(events(folder))


def raw(folder) -> bytes:
    return log.round_log(folder).read_bytes()


def standing(folder) -> str:
    return str(versions.standing(folded(folder)))


def build(folder, stage_id, *more):
    """Record that a build of the stage was commissioned, and each event of `more`
    after it, as the verbs that make them will."""
    path = log.round_log(folder)
    log.append(path, "build.commissioned", {"stage": stage_id, "repo": "proj-a",
                                            "branch": f"branch-{stage_id.lower()}"},
               actor="coder")
    for name in more:
        fields = {"stage": stage_id, "tip": "0" * 40} if name == "finish.done" else {
            "stage": stage_id}
        log.append(path, name, fields, actor="coder")


def line_with(path, needle: str) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    return next(n for n, line in enumerate(lines, 1) if needle in line)


# -- recording -------------------------------------------------------------------------

def test_a_verdict_is_one_event_that_holds_what_the_report_says(folder):
    fold(folder, question("Q1"))
    path = report(folder, "fold-first", findings=[
        finding("L1", "major", "open", "a gate reads prose"), finding("L2", "note")])
    data, written = path.read_bytes(), path.stat().st_mtime_ns

    done = record(path, at=AT)

    [event] = [one for one in events(folder) if one["type"] == "verdict.recorded"]
    assert {key: event[key] for key in event if key not in ("v", "n")} == {
        "at": AT, "actor": "critic", "type": "verdict.recorded", "kind": "design",
        "pass": "full", "verdict": "fold-first", "design": 1, "model": "a-model",
        "by": "critic", "report": "report.md",
        "sha256": hashlib.sha256(data).hexdigest(),
        "findings": [
            {"id": "L1", "severity": "major", "state": "open",
             "text": "a gate reads prose"},
            {"id": "L2", "severity": "note", "state": "open",
             "text": "a line is unclear"}]}
    assert done.event == event and done.name == ROUND
    assert (done.verdict.verdict, done.verdict.design, done.verdict.n) == (
        "fold-first", 1, event["n"])
    assert str(done.standing) == "fold-first@v1"
    # The report is read and never written.
    assert (path.read_bytes(), path.stat().st_mtime_ns) == (data, written)
    assert sorted(one.name for one in folder.iterdir()) == [
        "design.md", "report.md", "round-note.md", "round.jsonl", "versions"]


def test_a_report_outside_the_rounds_folder_is_named_by_its_whole_path(
        folder, tmp_path):
    fold(folder, question("Q1"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    path = report(elsewhere)

    assert record(path).verdict.report == str(path)


def test_a_verdict_is_recorded_for_the_version_it_read_after_a_later_one_was_frozen(
        folder):
    fold(folder, question("Q1"))
    path = report(folder, "fold-first", design=1)
    fold(folder, question("Q1"), prose="What the round builds, said better.")
    (folder / "design.md").write_text("edited since, with no block\n", encoding="utf-8")

    done = record(path)

    assert done.verdict.design == 1
    found = folded(folder)
    assert [one.design for one in versions.design_verdicts(found)] == [1]
    assert versions.design_verdicts(found, 2) == ()
    # The newer version has no verdict of its own.
    assert str(done.standing) == standing(folder) == "none"
    assert versions.standing(found).version == 2


def test_a_verb_stopped_right_after_its_append_has_applied_everything(folder):
    fold(folder, question("Q1"), question("Q2"))
    fold(folder, question("Q2"))
    before = len(events(folder))

    record(report(folder, design=2))

    # One event is the whole act: the log as it stands folds to the applied record.
    assert len(events(folder)) == before + 1
    assert questions.find(folded(folder), "Q1").state == questions.WITHDRAWN


# -- what is refused -------------------------------------------------------------------

def test_a_report_whose_block_cannot_be_read_is_refused_with_its_line(folder):
    fold(folder, question("Q1"))
    path = report(folder)
    path.write_text(path.read_text(encoding="utf-8").replace(
        'pass = "full"', 'pass = "thorough"'), encoding="utf-8")
    before = raw(folder)

    with pytest.raises(BadBlock) as refused:
        record(path)

    assert refused.value.file == path
    assert [one.line for one in refused.value.problems] == [line_with(path, "pass =")]
    assert raw(folder) == before


def test_a_file_that_is_no_report_is_refused(folder):
    fold(folder, question("Q1"))
    before = raw(folder)

    with pytest.raises(BadBlock) as refused:
        record(folder / "no-such-report.md")

    assert refused.value.file == folder / "no-such-report.md"
    assert raw(folder) == before


@pytest.mark.parametrize("frozen, named", [(0, 1), (1, 2), (2, 3)])
def test_a_report_of_a_version_never_frozen_is_refused(folder, frozen, named):
    for number in range(frozen):
        fold(folder, question("Q1"), prose=f"The design, written {number} times over.")
    path = report(folder, design=named)
    before = raw(folder)

    with pytest.raises(BadBlock) as refused:
        record(path)

    [problem] = refused.value.problems
    assert problem.line == line_with(path, "design =")
    assert f"read version {named} of the design of {ROUND}, which was never frozen" in (
        problem.what)
    assert ("no version of the design was frozen", "the one version frozen is 1",
            "the versions frozen are 1 to 2")[frozen] in problem.what
    assert raw(folder) == before


def test_a_publishable_report_with_an_open_major_is_refused_at_the_finding(folder):
    fold(folder, question("Q1"))
    path = report(folder, findings=[finding("L1", "major", "open")])
    before = raw(folder)

    with pytest.raises(BadBlock) as refused:
        record(path)

    assert [one.line for one in refused.value.problems] == [line_with(path, "[[finding]]")]
    assert raw(folder) == before


def test_a_report_recorded_already_is_refused_under_any_name(folder):
    fold(folder, question("Q1"))
    path = report(folder, "fold-first")
    record(path, at=AT)
    copy = folder / "copy.md"
    copy.write_bytes(path.read_bytes())
    before = raw(folder)

    for again in (path, copy):
        with pytest.raises(Refused) as refused:
            record(again)
        [unmet] = refused.value.unmet
        assert (f"a report with these bytes was recorded already, at {AT} by critic "
                "as report.md") in unmet.reason
        assert unmet.fix == f"moxi rounds show {ROUND}"
    assert raw(folder) == before


def test_a_copied_report_with_new_content_is_a_new_report(folder):
    fold(folder, question("Q1"))
    record(report(folder, "fold-first", findings=[finding("L1", "major")]))
    record(report(folder, "publishable", pass_="re-check", name="copy.md",
                  findings=[finding("L1", "major", "fixed")]))
    # The same file, written again with other content, is new too.
    record(report(folder, "publishable", pass_="re-check", prose="Read once more."))

    found = versions.design_verdicts(folded(folder))
    assert [(one.report, one.verdict, one.pass_) for one in found] == [
        ("report.md", "fold-first", "full"), ("copy.md", "publishable", "re-check"),
        ("report.md", "publishable", "re-check")]
    assert len({one.sha256 for one in found}) == 3


@pytest.mark.parametrize("kind", ["build", "step", "majors", "retro"])
def test_a_report_of_another_kind_is_refused_as_not_recorded_yet(folder, kind):
    fold(folder, question("Q1"), stage("B1"))
    path = other(folder, kind)
    before = raw(folder)

    with pytest.raises(Refused) as refused:
        record(path)

    [unmet] = refused.value.unmet
    assert f"is a {kind} report, and only a report of a design is recorded" in (
        unmet.reason)
    assert raw(folder) == before


def test_a_design_report_is_refused_where_the_flow_verifies_no_design(opened):
    folder = opened("small")
    fold(folder, question("Q1"))
    path = report(folder)
    before = raw(folder)

    with pytest.raises(Refused) as refused:
        record(path)

    [unmet] = refused.value.unmet
    assert "the flow small of proj-a/a-round has no verify-design step" in unmet.reason
    assert unmet.fix == f"moxi rounds mark {ROUND} --flow NAME"
    assert raw(folder) == before


def test_a_round_that_ended_takes_no_verdict(folder):
    fold(folder, question("Q1"))
    path = report(folder)
    dropping.drop(ROUND, by="operator", record="comms:1234567", actor="planner")
    before = raw(folder)

    with pytest.raises(Refused, match="was dropped and stays as it ended"):
        record(path)

    assert raw(folder) == before


def test_a_round_never_opened_has_no_verdict(folder):
    with pytest.raises(NoRound):
        verdicts.record("proj-a/no-such-round", report(folder), actor="critic")


# -- the standing ----------------------------------------------------------------------

def test_a_design_never_frozen_stands_at_none(folder):
    found = versions.standing(folded(folder))

    assert (str(found), found.word, found.version, found.verdict) == (
        "none", versions.NONE, None, None)


def test_the_standing_is_the_newest_verdict_of_the_current_version(folder):
    said = []
    fold(folder, question("Q1"))
    said.append(standing(folder))
    record(report(folder, "fold-first", name="one.md"))
    said.append(standing(folder))
    record(report(folder, "publishable", pass_="re-check", name="two.md"))
    said.append(standing(folder))
    record(report(folder, "fold-first", pass_="re-check", name="three.md"))
    said.append(standing(folder))
    # A design frozen again has no verdict yet, whatever the one before it had.
    fold(folder, question("Q1"), prose="What the round builds, said better.")
    said.append(standing(folder))
    # A verdict that read the earlier version is recorded and is not the standing.
    record(report(folder, "publishable", pass_="re-check", name="four.md", design=1,
                  prose="What a late pass of the first version found."))
    said.append(standing(folder))
    record(report(folder, "publishable", pass_="re-check", name="five.md", design=2))
    said.append(standing(folder))
    fold(folder, question("Q1"), prose="What the round builds, said a third way.")
    said.append(standing(folder))

    assert said == ["none", "fold-first@v1", "publishable@v1", "fold-first@v1", "none",
                    "none", "publishable@v2", "none"]
    found = versions.standing(folded(folder))
    assert (found.version, found.verdict) == (3, None)


def test_the_standing_names_the_verdict_that_gives_it(folder):
    fold(folder, question("Q1"))
    record(report(folder, "fold-first"))

    found = versions.standing(folded(folder))

    assert found.word == "fold-first" and found.version == 1
    assert found.verdict == folded(folder).verdicts[-1]


# -- what a publishable verdict takes out ----------------------------------------------

def taken_away(folder):
    """A round whose second version took away a question, a hold, a stage and a wait."""
    fold(folder, question("Q1"), question("Q2"), stage("B1"), stage("B2"),
         stage("B3", after='["B1", "B2"]'), stage("B4"))
    return fold(folder, question("Q2", blocks='["B2"]'), stage("B1"), stage("B2"),
                stage("B3", after='["B2"]'))


def test_a_publishable_verdict_applies_what_its_version_took_away(folder):
    frozen = taken_away(folder)
    assert {one.what for one in frozen.plan.pending} == {
        "question", "blocks", "stage", "after"}
    before = folded(folder)
    assert questions.find(before, "Q1").state == questions.OPEN
    assert questions.find(before, "Q2").blocks == "all"
    assert stages.find(before, "B3").after == ("B1", "B2")
    assert stages.state_of(before, "B4") == stages.PLANNED

    done = record(report(folder, design=2))

    assert done.event["applied"] == [
        {"what": "after", "id": "B3", "gone": ["B1"]},
        {"what": "stage", "id": "B4"},
        {"what": "blocks", "id": "Q2", "blocks": ["B2"]},
        {"what": "question", "id": "Q1"}]
    assert done.event["not_applied"] == []
    after = folded(folder)
    assert questions.find(after, "Q1").state == questions.WITHDRAWN
    assert questions.find(after, "Q2").blocks == ("B2",)
    assert questions.find(after, "Q2").state == questions.OPEN
    assert stages.find(after, "B3").after == ("B2",)
    assert stages.state_of(after, "B4") == stages.LEFT
    assert [one.id for one in stages.in_plan(after)] == ["B1", "B2", "B3"]
    assert versions.outstanding(after, 2) == ()
    # What the version took away is still on its record.
    assert len(versions.pending_removals(after, 2)) == 4
    # The events written: the one verdict.
    assert [one["type"] for one in events(folder)][-2:] == [
        "design.frozen", "verdict.recorded"]


def test_a_verdict_that_sends_the_design_back_applies_nothing(folder):
    taken_away(folder)
    before = folded(folder)

    done = record(report(folder, "fold-first", design=2,
                         findings=[finding("L1", "major")]))

    assert "applied" not in done.event and "not_applied" not in done.event
    after = folded(folder)
    assert after.questions == before.questions and after.stages == before.stages
    assert questions.left_stages(after) == frozenset()
    assert len(versions.outstanding(after, 2)) == 4


@pytest.mark.parametrize("built, why", [
    ((), "B4 has an open build, and a stage with one leaves only after that build "
         "is dropped"),
    (("finish.done",), "B4 is finished: its work is met, and a finished stage has "
                       "nothing to leave")])
def test_a_stage_with_a_build_is_not_taken_out_and_is_named(folder, built, why, capsys):
    taken_away(folder)
    build(folder, "B4", *built)

    assert main(["verdict", ROUND, str(report(folder, design=2)), "--as", "critic"]) == 0

    printed = capsys.readouterr().out
    event = events(folder)[-1]
    if built:
        # A finished stage is met: its removal no longer stands, and nothing names it.
        assert event["not_applied"] == []
        assert "B4" not in printed
    else:
        assert event["not_applied"] == [{"what": "stage", "id": "B4", "why": why}]
        assert f"not applied: the stage B4 is gone from the block: {why}" in printed
    assert {"what": "stage", "id": "B4"} not in event["applied"]
    assert len(event["applied"]) == 3
    assert stages.state_of(folded(folder), "B4") == stages.PLANNED


def test_a_stage_whose_build_was_dropped_is_taken_out(folder):
    taken_away(folder)
    build(folder, "B4", "build.dropped")

    record(report(folder, design=2))

    assert stages.state_of(folded(folder), "B4") == stages.LEFT


def test_only_what_still_stands_is_applied(folder):
    taken_away(folder)
    ruling.rule(ROUND, item="Q1", text="They follow the settings.",
                record="comms:1234567", actor="planner")
    ruling.rule(ROUND, item="unstage:B4", record="comms:1234567", actor="planner")
    ruling.rule(ROUND, item="unafter:B3=B1", record="comms:1234567", actor="planner")
    ruling.rule(ROUND, item="narrow:Q2=none", record="comms:1234567", actor="planner")

    done = record(report(folder, design=2))

    assert done.event["applied"] == [] and done.event["not_applied"] == []
    after = folded(folder)
    assert questions.find(after, "Q1").state == questions.RULED
    assert questions.find(after, "Q2").blocks == "none"


def test_a_hold_a_verdict_made_smaller_stays_so_when_an_earlier_ruling_is_taken_back(
        folder):
    fold(folder, question("Q1"), question("Q2"), stage("B1"), stage("B2"), stage("B3"))
    ruling.rule(ROUND, item="narrow:Q2=B2,B3", record="comms:1234567", actor="planner")
    fold(folder, question("Q1"), question("Q2", blocks='["B2"]'), stage("B1"),
         stage("B2"), stage("B3"))
    record(report(folder, design=2))
    assert questions.find(folded(folder), "Q2").blocks == ("B2",)

    ruling.rule(ROUND, item="revoke:C1", record="comms:1234567", actor="planner")

    found = questions.find(folded(folder), "Q2")
    assert (found.state, found.blocks) == (questions.OPEN, ("B2",))


def test_a_question_a_design_declared_reads_as_open_again_when_its_answer_is_taken_back(
        folder):
    fold(folder, question("Q1", blocks='["B1"]'), stage("B1"))
    ruling.rule(ROUND, item="Q1", text="They follow the settings.",
                record="comms:1234567", actor="planner")
    assert questions.find(folded(folder), "Q1").state == questions.RULED

    ruling.rule(ROUND, item="revoke:C1", record="comms:1234567", actor="planner")

    found = questions.find(folded(folder), "Q1")
    assert (found.state, found.blocks) == (questions.OPEN, ("B1",))


def test_a_verdict_for_an_earlier_version_applies_what_that_version_took_away(folder):
    taken_away(folder)
    path = report(folder, design=2)
    # The third version takes nothing more away, and still leaves the same out.
    fold(folder, question("Q2", blocks='["B2"]'), stage("B1"), stage("B2"),
         stage("B3", after='["B2"]'), prose="What the round builds, said better.")

    done = record(path)

    assert len(done.event["applied"]) == 4
    assert str(done.standing) == "none"
    assert versions.outstanding(folded(folder), 3) == ()


def test_a_second_publishable_verdict_has_nothing_left_to_apply(folder):
    taken_away(folder)
    record(report(folder, design=2))

    done = record(report(folder, design=2, pass_="re-check", name="again.md"))

    assert done.event["applied"] == [] and done.event["not_applied"] == []


def test_a_stage_a_verdict_took_out_does_not_come_back(folder):
    taken_away(folder)
    record(report(folder, design=2))

    with pytest.raises(BadBlock, match="B4"):
        fold(folder, question("Q2", blocks='["B2"]'), stage("B1"), stage("B2"),
             stage("B3", after='["B2"]'), stage("B4"))


# -- where a verdict is shown ----------------------------------------------------------

def test_the_command_prints_what_was_recorded_and_the_standing(folder, capsys):
    taken_away(folder)
    path = report(folder, design=2)

    assert main(["verdict", ROUND, str(path), "--as", "critic"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert out[0] == (f"recorded for {ROUND}: publishable for v2 of its design, full "
                      "pass by critic, model a-model")
    assert out[1] == "report: report.md"
    assert out[2] == f"sha256: {hashlib.sha256(path.read_bytes()).hexdigest()}"
    assert out[3:] == [
        "applied: B3 waits for B1 no longer",
        "applied: the stage B4 is gone from the block",
        "applied: Q2 holds up fewer stages: B2",
        "applied: the question Q1 is gone from the block",
        "standing: publishable@v2"]
    assert events(folder)[-1]["actor"] == "critic"


def test_a_refusal_exits_with_the_file_and_the_line(folder, capsys):
    fold(folder, question("Q1"))
    path = report(folder, design=4)

    assert main(["verdict", ROUND, str(path)]) == 3

    err = capsys.readouterr().err
    assert f"line {line_with(path, 'design =')}: the report read version 4" in err
    assert main(["verdict", ROUND]) == 2


def test_verdicts_and_the_standing_are_shown_and_in_the_note(folder, capsys):
    taken_away(folder)
    assert main(["show", ROUND]) == 0
    before = capsys.readouterr().out
    assert "standing: none" in before and "verdicts: none" in before
    assert "pending in v2: the stage B4 is gone from the block" in before
    record(report(folder, "fold-first", design=1, name="first.md",
                  findings=[finding("L1", "major", "open", "a gate reads prose")]),
           at=AT)
    build(folder, "B4")
    path = report(folder, design=2)
    record(path, at=AT)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    assert main(["show", ROUND]) == 0
    shown = capsys.readouterr().out
    assert main(["show", ROUND, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    note = (folder / "round-note.md").read_text(encoding="utf-8")

    assert "standing: publishable@v2\n" in shown
    assert "verdicts: 2\n" in shown
    assert "  fold-first for v1: full pass by critic, model a-model\n" in shown
    assert "    finding L1: major, open: a gate reads prose\n" in shown
    assert (f"    report: report.md   sha256: {digest}   recorded: {AT} by critic\n"
            in shown)
    assert "    applied: the question Q1 is gone from the block\n" in shown
    assert ("    not applied: the stage B4 is gone from the block: B4 has an open "
            "build") in shown
    # What was applied waits no longer; the stage that stayed still does.
    assert [line for line in shown.splitlines() if line.startswith("pending in")] == [
        "pending in v2: the stage B4 is gone from the block"]
    assert "verdict.recorded  publishable for v2" in shown
    assert data["standing"] == "publishable@v2"
    assert [(one["version"], one["verdict"], one["pass"], one["by"], one["model"],
             one["report"], one["flags"]) for one in data["verdicts"]] == [
        (1, "fold-first", "full", "critic", "a-model", "first.md", []),
        (2, "publishable", "full", "critic", "a-model", "report.md",
         ["extra-full-pass"])]
    assert data["verdicts"][0]["findings"] == [
        {"id": "L1", "severity": "major", "state": "open", "text": "a gate reads prose"}]
    assert [one["id"] for one in data["verdicts"][1]["applied"]] == ["B3", "Q2", "Q1"]
    assert [one["id"] for one in data["verdicts"][1]["not_applied"]] == ["B4"]
    assert [one["id"] for one in data["versions"][-1]["outstanding"]] == ["B4"]
    assert "- standing: publishable@v2\n" in note
    assert "## Verdicts\n\n- fold-first for v1: full pass by critic, model a-model\n" in (
        note)
    assert "  - finding L1: major, open: a gate reads prose\n" in note
    assert "  - applied: the question Q1 is gone from the block\n" in note
    assert "- pending in v2: the stage B4 is gone from the block\n" in note


def test_a_flag_recorded_with_a_verdict_is_kept_and_shown(folder, capsys):
    fold(folder, question("Q1"))
    log.append(log.round_log(folder), "verdict.recorded",
               {"kind": "design", "pass": "full", "verdict": "publishable", "design": 1,
                "model": "a-model", "by": "critic", "report": "report.md",
                "sha256": "0" * 64, "findings": [], "flags": ["a-flag"]},
               actor="critic")

    assert folded(folder).verdicts[-1].flags == ("a-flag",)
    assert main(["show", ROUND]) == 0
    assert "model a-model   flags: a-flag\n" in capsys.readouterr().out


def test_verdicts_and_the_standing_are_in_the_index_and_the_list(folder, capsys):
    taken_away(folder)
    record(report(folder, "fold-first", design=1, name="first.md",
                  findings=[finding("L1", "major")]), at=AT)
    record(report(folder, design=2), at=AT)

    found = index.read("proj-a", folder.parent, "a-round")

    assert [item.body["standing"] for item in found.of(index.VERSION)] == [
        "publishable@v2"]
    assert found.of(index.VERDICT) == (
        index.Item(index.VERDICT, "d1", {
            "version": 1, "verdict": "fold-first", "pass": "full", "by": "critic",
            "model": "a-model", "report": "first.md", "at": AT, "flags": []}),
        index.Item(index.VERDICT, "d2", {
            "version": 2, "verdict": "publishable", "pass": "full", "by": "critic",
            "model": "a-model", "report": "report.md", "at": AT,
            "flags": ["extra-full-pass"]}))
    assert found.of(index.PENDING) == ()
    assert main(["list"]) == 0
    assert (f"{ROUND}   open   flow full   step docket, ready   "
            "design: publishable@v2   ") in capsys.readouterr().out
    assert main(["list", "--json"]) == 0
    [listed] = json.loads(capsys.readouterr().out)["rounds"]
    assert listed["standing"] == "publishable@v2"


def test_a_round_with_no_frozen_design_is_listed_with_no_standing(folder, capsys):
    assert main(["list"]) == 0
    assert "design:" not in capsys.readouterr().out
    assert main(["list", "--json"]) == 0
    [listed] = json.loads(capsys.readouterr().out)["rounds"]
    assert listed["standing"] is None


# -- a model weaker than the pass's tier -------------------------------------------------

# The built-in flow holds a full pass over a design to `second` and a re-check to `third`.
TIERS = """\
    [models]
    first  = { claude = "model-one", codex = "model-a" }
    second = { claude = "model-two", codex = "model-b" }
    third  = { claude = "model-three", codex = "model-c" }
    fourth = { claude = "model-four", codex = "model-d" }
"""


def passed(folder, model, *, pass_="full", verdict="fold-first"):
    """Record a report of `model` over the first version, each under its own name."""
    name = f"report-{len(folded(folder).verdicts) + 1}.md"
    return record(report(folder, verdict, pass_=pass_, model=model, name=name,
                         prose=f"What the pass {name} found."))


def test_a_report_by_a_model_below_its_pass_s_tier_is_recorded_with_a_flag(
        folder, write_config):
    write_config(TIERS)
    fold(folder, question("Q1"))

    done = passed(folder, "model-three", verdict="publishable")

    assert done.event["flags"] == ["model-below-tier"]
    assert done.verdict.flags == ("model-below-tier",)
    assert str(done.standing) == "publishable@v1"
    assert standing(folder) == "publishable@v1"


@pytest.mark.parametrize("model", ["model-two", "model-one", "model-b", "model-unlisted"])
def test_a_report_by_a_model_at_its_pass_s_tier_or_in_no_tier_has_no_flag(
        folder, write_config, model):
    write_config(TIERS)
    fold(folder, question("Q1"))

    done = passed(folder, model)

    assert "flags" not in done.event and done.verdict.flags == ()


def test_a_model_is_placed_whichever_harness_lists_it(folder, write_config):
    write_config(TIERS)
    fold(folder, question("Q1"))

    assert passed(folder, "model-c").event["flags"] == ["model-below-tier"]


def test_a_recheck_is_held_to_the_step_s_tier_for_one(folder, write_config):
    write_config(TIERS)
    fold(folder, question("Q1"))
    passed(folder, "model-two")

    at_tier = passed(folder, "model-three", pass_="re-check")
    below = passed(folder, "model-four", pass_="re-check")

    assert "flags" not in at_tier.event
    assert below.event["flags"] == ["model-below-tier"]


def test_no_table_of_tiers_flags_no_model(folder):
    fold(folder, question("Q1"))

    assert "flags" not in passed(folder, "model-three").event


def test_a_second_full_pass_by_a_weaker_model_carries_both_flags(folder, write_config):
    write_config(TIERS)
    fold(folder, question("Q1"))
    passed(folder, "model-two")

    again = passed(folder, "model-three")

    assert again.event["flags"] == ["extra-full-pass", "model-below-tier"]
    assert again.verdict.flags == ("extra-full-pass", "model-below-tier")


def test_the_flag_for_a_weaker_model_is_shown_and_in_the_note(
        folder, write_config, capsys):
    write_config(TIERS)
    fold(folder, question("Q1"))
    passed(folder, "model-three")

    assert main(["show", ROUND]) == 0
    shown = capsys.readouterr().out
    assert main(["show", ROUND, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    note = (folder / "round-note.md").read_text(encoding="utf-8")

    assert "model model-three   flags: model-below-tier\n" in shown
    assert data["verdicts"][0]["flags"] == ["model-below-tier"]
    assert "  - flags: model-below-tier\n" in note
