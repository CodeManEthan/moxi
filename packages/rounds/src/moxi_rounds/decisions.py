"""Decisions: as-built choices the operator would notice, because he gets something
different from what he ruled.

A report of a build declares each as a `[[decision]]`; a majors report declares the
ones no build report will carry, each with its stage. When the report's verdict is
recorded, each is a record of the round in the basic form of a question: its key
`B2.M1`, its sentence, `from` the report that declared it, `blocks` `none`, its state
(`open` until the operator rules it), `by` the report's author and `at` when its
verdict was recorded. Beside those it keeps its `weight`, `major` or `minor`, and
whether it `forecloses` a capability, and it offers two options, `accept` and
`reject`. The verdict's event holds it, and nothing else is written for it.

**It stays.** A decision on a build's record stays there whether or not a later report
lists it. A report may list a recorded one again with the same or a heavier `weight`
and `forecloses`, which then become its own; one that lightens either is refused, so no
report removes or lowers a decision. A new decision takes an id no earlier report of
its stage used. The sentence is the one it was declared with: a report that lists
the id with another sentence is refused.

**His ruling.** `rule ROUND B2.M1=accept|reject --record PTR [--text TEXT]` rules one;
it is one `ruling.recorded` event, of substance, that applies to no stage. A second
ruling of a decision replaces the first and names it, and `revoke:` takes one back,
which leaves the decision open. A rejected decision owes a fix: it is also a waiting
item of the round, `B2.M1`, from its ruling until `mark ... --done --record` names the
fix (`waits`).

**Its build.** A decision belongs to each build of its stage whose reports listed it: a
build report recorded while that build was the stage's, or a majors report recorded
then that gives it that stage (`of_build`). A dropped build counts for nothing, so a
decision only a dropped build's reports declared never reached the target; a later
build of the stage covers only the decisions its own reports declare.

**The target.** A decision reached the target when a report of its stage's current
build declared it and that build is finished (`reached`). A decision holds no stage.
What it holds, when it is major, reached the target and is not ruled (`unruled_majors`),
is the round's close and its `ratify` step, which both read it here. It is never
parked, narrowed, moved or withdrawn: those rulings are of asked questions.

**Put to him.** A decision not yet ruled is put to the operator when the project's
`ratify` setting names its weight and it reached the target (`not_put`, `why_not`):
only then is it labelled his to rule. One that is not is recorded only if he rules it.
In a round that has ended none takes a ruling: each is left open, and the close lists
those of the finished builds (`left_open`).

Nothing here reads a file, the config or git.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace

from . import builds, corrections, findings, questions, report_block
from . import state as states

#: A decision's key outside its report, and the item of a ruling of one.
KEY = re.compile(r"(B[1-9][0-9]*)\.(M[1-9][0-9]*)")
ITEM = re.compile(r"(B[1-9][0-9]*\.M[1-9][0-9]*)=([a-z]+)")
#: The two options every decision offers, in order, each with its line.
ACCEPT = "accept"
REJECT = "reject"
OPTIONS = ((ACCEPT, "accept it as built"), (REJECT, "reject it: a fix is owed"))
#: What a decision holds up: no stage.
BLOCKS = "none"
#: Its states.
OPEN = "open"
RULED = "ruled"

_ORDER = {report_block.MINOR: 0, report_block.MAJOR: 1}


@dataclass(frozen=True)
class Decision:
    """One decision of a round as the reports and rulings leave it."""

    stage: str
    id: str
    text: str
    #: The report that declared it, its author, and when its verdict was recorded.
    origin: str | None
    by: str | None
    at: str
    weight: str
    forecloses: bool
    #: The line of the verdict that declared it.
    raised: int
    state: str = OPEN
    #: Once ruled: the ruling that stands, what he chose, its pointer and its line.
    ruling: str | None = None
    chose: str | None = None
    record: str | None = None
    ruled: int | None = None
    ruled_by: str | None = None
    ruled_at: str | None = None

    @property
    def key(self) -> str:
        """Its key outside the report: `B2.M1`."""
        return f"{self.stage}.{self.id}"

    @property
    def major(self) -> bool:
        return self.weight == report_block.MAJOR

    def data(self) -> dict:
        """The decision as data: the basic form, then its options, weight, whether it
        forecloses, its stage, and once ruled the ruling."""
        return {"id": self.key, "text": self.text, "from": self.origin,
                "blocks": BLOCKS, "state": self.state, "by": self.by, "at": self.at,
                "options": [{"key": key, "text": line} for key, line in OPTIONS],
                "stage": self.stage, "weight": self.weight,
                "forecloses": self.forecloses, "ruling": self.ruling,
                "chose": self.chose, "record": self.record}


def listed(event: Mapping) -> Iterator[tuple[str, dict]]:
    """Each decision a verdict's event lists, with its stage: a majors report's gives
    its own, a build report's is the report's."""
    found = event.get("decisions")
    for one in found if isinstance(found, list) else ():
        if not isinstance(one, dict) or not isinstance(one.get("id"), str):
            continue
        stage = one.get("stage") or event.get("stage")
        if isinstance(stage, str):
            yield stage, one


def lighter(weight: str, *, forecloses: bool, than: Decision) -> list[str]:
    """What of `weight` and `forecloses` is lighter than the decision on record."""
    said = []
    if _ORDER.get(weight, 0) < _ORDER.get(than.weight, 0):
        said.append(f"its weight {weight} is lighter than {than.weight}")
    if than.forecloses and not forecloses:
        said.append("it no longer forecloses")
    return said


def of_round(state: states.State) -> tuple[Decision, ...]:
    """Every decision of the round, in the order declared, as the reports and the
    rulings that stand leave it."""
    found: dict[str, Decision] = {}
    for event in states.with_rereads(state):
        if event.get("type") != "verdict.recorded":
            continue
        for stage, one in listed(event):
            key = f"{stage}.{one['id']}"
            weight = str(one.get("weight"))
            forecloses = bool(one.get("forecloses"))
            was = found.get(key)
            if was is None:
                found[key] = Decision(
                    stage=stage, id=one["id"], text=str(one.get("text")),
                    origin=event.get("report"), by=event.get("by"), at=event["at"],
                    weight=weight, forecloses=forecloses, raised=event["n"])
            elif not lighter(weight, forecloses=forecloses, than=was):
                found[key] = replace(was, weight=weight,
                                     forecloses=forecloses or was.forecloses)
    rulings = {ruling.id: ruling for ruling in state.rulings}
    for ruling in corrections.live(state.rulings):
        matched = ITEM.fullmatch(ruling.rules or "")
        if matched is None or matched.group(1) not in found:
            continue
        event = next((one for one in state.history if one.get("type") ==
                      "ruling.recorded" and one.get("id") == ruling.id), {})
        found[matched.group(1)] = replace(
            found[matched.group(1)], state=RULED, ruling=ruling.id,
            chose=matched.group(2), record=ruling.record, ruled=event.get("n"),
            ruled_by=rulings[ruling.id].actor, ruled_at=rulings[ruling.id].at)
    return tuple(found.values())


def of_build(state: states.State, build: builds.Build) -> tuple[Decision, ...]:
    """The decisions of the round that a report recorded while `build` was its stage's
    build declared or listed again, in the order declared."""
    keys = {f"{stage}.{one['id']}"
            for event in states.with_rereads(state)
            if event.get("type") == "verdict.recorded"
            and findings.within(state, build, event.get("n"))
            for stage, one in listed(event) if stage == build.stage}
    return tuple(one for one in of_round(state) if one.key in keys)


def finished(state: states.State, build: builds.Build) -> bool:
    """Whether `build` is its stage's finished build: the finish is recorded after its
    commission and before the stage's next."""
    return any(event["type"] == "finish.done" and event.get("stage") == build.stage
               and findings.within(state, build, event.get("n"))
               for event in state.history)


def finished_builds(state: states.State) -> tuple[builds.Build, ...]:
    """The current build of each stage of the plan, where it is finished, in the
    order the stages were declared: the builds whose decisions reached the target."""
    # The plan is read as `stages.in_plan` reads it, which can't be imported here.
    out = questions.left_stages(state)
    found = []
    for stage in state.stages:
        if stage.id in out:
            continue
        build = builds.current(state, stage.id)
        if build is not None and finished(state, build):
            found.append(build)
    return tuple(found)


def reached(state: states.State) -> tuple[Decision, ...]:
    """The decisions that reached the target: those a finished build's reports
    declared (`finished_builds`, `of_build`), in that order."""
    return tuple(one for build in finished_builds(state)
                 for one in of_build(state, build))


def unruled_majors(state: states.State) -> tuple[Decision, ...]:
    """The major decisions that reached the target and are not ruled."""
    return tuple(one for one in reached(state)
                 if one.weight == report_block.MAJOR and one.state == OPEN)


def find(state: states.State, key: str) -> Decision | None:
    """The decision of the round with that key, or None."""
    return next((one for one in of_round(state) if one.key == key), None)


def item(rules: str | None) -> str | None:
    """The decision a ruling whose item is `rules` rules, or None for another
    ruling."""
    matched = ITEM.fullmatch(rules or "")
    return None if matched is None else matched.group(1)


def items(state: states.State) -> Iterator[tuple[str, Mapping]]:
    """The decisions of `state` not yet ruled, each as `(key, body)` for the index: the
    basic form with its stage, weight and whether it forecloses."""
    for one in of_round(state):
        if one.state == OPEN:
            body = one.data()
            yield body.pop("id"), {key: value for key, value in body.items()
                                   if key not in ("ruling", "chose", "record",
                                                  "options")}


def rule_line(name: str, key: str) -> str:
    """The command that records his ruling of a decision."""
    return f"moxi rounds rule {name} {key}={ACCEPT}|{REJECT} --record PTR"


# -- put to him --------------------------------------------------------------------------

#: The weights each value of the project's `ratify` setting puts to him: `majors` the
#: major ones, `majors+minors` both. `off` puts none.
PUT_BY = {"majors": (report_block.MAJOR,),
          "majors+minors": (report_block.MAJOR, report_block.MINOR)}


def not_put(weight: str, *, setting: str, stage: str, finished: bool,
            declared: bool) -> str | None:
    """Why a decision not yet ruled is not put to him, in a few words, or None when it
    is. It is put when `setting`, the project's `ratify` setting, names its `weight`,
    its `stage` is `finished`, and the build that finished the stage `declared` it: the
    rule the ratify export draws by."""
    if weight not in PUT_BY.get(setting, ()):
        return f"ratify is {setting}"
    if not finished:
        return f"stage {stage} is not finished"
    if not declared:
        return f"the build that finished {stage} did not declare it"
    return None


def why_not(state: states.State, setting: str) -> dict[str, str | None]:
    """Each decision of the round not yet ruled, by key, with why it is not put to him
    under `setting` (`not_put`), None where it is."""
    done = {build.stage for build in finished_builds(state)}
    landed = {one.key for one in reached(state)}
    return {one.key: not_put(one.weight, setting=setting, stage=one.stage,
                             finished=one.stage in done, declared=one.key in landed)
            for one in of_round(state) if one.state == OPEN}


def open_line(name: str, key: str, *, why: str | None, ended: bool) -> str:
    """What is said under a decision not yet ruled: `his to rule` with the command
    only when it is put to him (`why` None) in a round still open. One not put to him
    is recorded only if he rules it. A round that has ended takes no ruling, so there
    it is left open, with no command."""
    if ended:
        return ("left open, put to him and not ruled" if why is None
                else f"left open, not put to him ({why})")
    if why is None:
        return f"his to rule: {rule_line(name, key)}"
    return f"not put to him ({why}); recorded only if he rules it: {rule_line(name, key)}"


def left_open(state: states.State, setting: str) -> tuple[dict, ...]:
    """The decisions of the finished builds still open, in that order, each with
    whether `setting` puts it to him: what the close of the round leaves open. One only
    a dropped build declared is not among them."""
    return tuple({"id": one.key, "weight": one.weight, "forecloses": one.forecloses,
                  "text": one.text, "put": one.weight in PUT_BY.get(setting, ())}
                 for one in reached(state) if one.state == OPEN)


def left_open_line(one: dict) -> str:
    """A decision the close left open, as `left_open` gives it, in a line."""
    head = ("left open, put to him and not ruled" if one["put"]
            else "left open, not put to him")
    weight = one["weight"] + (", forecloses" if one["forecloses"] else "")
    return f"{head}: decision {one['id']}, {weight}: {one['text']}"


__all__ = ["KEY", "ITEM", "ACCEPT", "REJECT", "OPTIONS", "BLOCKS", "OPEN", "RULED",
           "PUT_BY", "Decision", "listed", "lighter", "of_round", "of_build", "finished",
           "finished_builds", "reached", "unruled_majors", "find", "item", "items",
           "rule_line", "not_put", "why_not", "open_line", "left_open", "left_open_line"]
