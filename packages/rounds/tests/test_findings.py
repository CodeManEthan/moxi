"""The findings of a build: the newest report that lists one sets its state, a build
holds only its own, and an id once used for a stage stays taken."""

from __future__ import annotations

from moxi_rounds import builds, findings
from moxi_rounds import state as states

AT = "2026-01-02T03:04:05Z"
HEAD = "a" * 40


def event(n: int, type_: str, **fields) -> dict:
    return {"v": 1, "n": n, "at": AT, "actor": "planner", "type": type_, **fields}


def commissioned(n: int, stage: str = "B1") -> dict:
    return event(n, "build.commissioned", stage=stage, repo="proj-a", branch="work",
                 base="b" * 40, target="main", builder="coder")


def report(n: int, *listed, stage: str = "B1", kind: str = "build") -> dict:
    return event(n, "verdict.recorded", kind=kind, verdict="fold-first", stage=stage,
                 head=HEAD, merge_base=None, by="critic", findings=[
                     {"id": id_, "severity": severity, "state": state, "text": text}
                     for id_, severity, state, text in listed])


def fold(*events) -> states.State:
    return states.fold([event(1, "round.opened", description="a placeholder round",
                              flow="fix", steps=[]), *events])


def test_the_newest_report_that_lists_a_finding_sets_it():
    state = fold(commissioned(2),
                 report(3, ("L1", "major", "open", "first said"),
                        ("L2", "minor", "open", "a slip")),
                 report(4, ("L1", "minor", "fixed", "said again")))

    found = findings.of_stage(state, "B1")

    assert [(one.key, one.severity, one.state, one.text, one.raised, one.set_by)
            for one in found] == [("B1.L1", "minor", "fixed", "said again", 3, 4),
                                  ("B1.L2", "minor", "open", "a slip", 3, 3)]
    assert [one.step for one in found] == ["verify-build", "verify-build"]
    assert [one.open for one in found] == [False, True]


def test_a_build_holds_only_the_findings_recorded_while_it_was_its_stage_s():
    state = fold(commissioned(2), report(3, ("L1", "major", "open", "the old one")),
                 event(4, "build.dropped", stage="B1"), commissioned(5),
                 report(6, ("L2", "minor", "open", "the new one")),
                 commissioned(7, "B2"), report(8, ("L1", "note", "open", "B2's"),
                                               stage="B2"))
    old, new = [one for one in builds.builds(state) if one.stage == "B1"]

    assert [one.key for one in findings.of_build(state, old)] == ["B1.L1"]
    assert [one.key for one in findings.of_build(state, new)] == ["B1.L2"]
    assert [one.n for one in findings.verdicts_of(state, old)] == [3]
    assert findings.taken(state, "B1") == {"L1", "L2"}
    assert findings.taken(state, "B2") == {"L1"}


def test_a_design_report_s_findings_are_no_build_s():
    state = fold(commissioned(2), event(3, "verdict.recorded", kind="design",
                                        verdict="fold-first", design=1, findings=[
                                            {"id": "L1", "severity": "major",
                                             "state": "open", "text": "a slip"}]))

    assert findings.of_stage(state, "B1") == ()
    assert findings.taken(state, "B1") == frozenset()
    assert findings.of_stage(state, "B9") == ()
