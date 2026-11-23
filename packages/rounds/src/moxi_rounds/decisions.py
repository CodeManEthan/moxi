"""Decisions: as-built choices the operator would notice, because the operator gets
something different from what was ruled.

A report of a build declares each as a `[[decision]]`; a majors report declares the ones
no build report will carry, each with its stage. When the report's verdict is recorded,
each is a record of the round in the basic form of a question: its key `B2.M1`, its
sentence, `from` the report that declared it, `blocks` `none`, its state (`open` until
the operator rules it or the lead settles it), `by` the report's author and `at` when
its verdict was recorded. Beside those it keeps its `weight`, `major` or `minor`, and
whether it `forecloses` a capability, and it offers two options, `accept` and `reject`.
The verdict's event holds it, and nothing else is written for it.

**It stays.** A decision on a build's record stays there whether or not a later report
lists it. A report may list a recorded one again with the same or a heavier `weight`
and `forecloses`, which then become its own; one that lightens either is refused, so no
report removes or lowers a decision. A new decision takes an id no earlier report of
its stage used. The sentence is the one it was declared with: a report that lists
the id with another sentence is refused.

**The operator's ruling.** `rule ROUND B2.M1=accept|reject --record PTR [--text TEXT]` rules one;
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
What it holds, when it reached the target and is open, is the round's close: the
close and the `ratify` step wait for the operator's ruling of each that is the
operator's (`to_rule`), and the close for the lead's settlement of each other minor one
(`to_settle`). It is never parked, narrowed, moved or withdrawn: those rulings are of
asked questions.

**Whose it is.** One rule says it, and every reader reads it (`why_not`, `to_rule`): an
open decision is the operator's exactly when the close waits for the operator's ruling
of it. That is when the round's flow has a `ratify` step that is not skipped
(`not_ratified`), the project's `ratify` setting names its weight, its stage is
finished and the build that finished it declared it. Only then is it labelled the
operator's to rule. A major one that is not is left open, recorded only if the operator
rules it; one whose stage is not finished yet is not yet put to the operator. A minor
one of a finished build that is not the operator's is the lead's to settle (`to_settle`).
In a round that has ended none takes a ruling: each is left open, and the close lists
those of the finished builds (`left_open`).

**The lead's settlement.** `mark ROUND B2.M1 --accept|--reject --record PTR` settles a
minor decision that is not the operator's: one `decision.settled` event, with `chose`,
the pointer and `by` the lead. The decision then reads `settled`, keeping what was
chosen, the pointer and the event's line, and a reject owes a fix as the operator's
does. A settlement is never the operator's ruling: the operator's ruling of the decision
replaces it and names it. It covers a minor only: once the decision's weight is major
the settlement lapses and the decision is open again, with no fix owed.

**Raised.** `mark ROUND B2.M1 --major --record PTR` is one `decision.raised` event; the
decision is major from then on, as when a later report lists it heavier.

Nothing here reads a file, the config or git.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace

from . import builds, corrections, findings, flows, questions, report_block
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
#: Its states: `settled` is the lead's settlement of a minor one, never a ruling.
OPEN = "open"
RULED = "ruled"
SETTLED = "settled"
#: The events of the lead's settlement and of the lead's raise to major, and the role
#: each records as `by`.
SETTLE = "decision.settled"
RAISE = "decision.raised"
LEAD = "lead"
#: The step that puts decisions to the operator.
RATIFY = "ratify"

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
    #: Once ruled or settled: the ruling that stands (None for a settlement), what was
    #: chosen, its pointer, its line, who recorded it and when.
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
    """Every decision of the round, in the order declared, as the reports, the lead's
    raises and settlements, and the rulings that stand leave it."""
    found: dict[str, Decision] = {}
    settlements: dict[str, dict] = {}
    for event in states.with_rereads(state):
        kind = event.get("type")
        if kind == RAISE and event.get("id") in found:
            found[event["id"]] = replace(found[event["id"]], weight=report_block.MAJOR)
        elif kind == SETTLE and event.get("id") in found:
            settlements[event["id"]] = event
        if kind != "verdict.recorded":
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
    named = {}
    for event in state.history:
        if event.get("type") == "ruling.recorded" and item(event.get("rules")):
            named[item(event.get("rules"))] = event.get("n", 0)
    for key, event in settlements.items():
        # A settlement covers a minor only, and a ruling of the operator recorded after
        # it replaces it, whether or not that ruling still stands.
        if found[key].weight == report_block.MAJOR or named.get(key, 0) > event["n"]:
            continue
        found[key] = replace(found[key], state=SETTLED, chose=event.get("chose"),
                             record=event.get("record"), ruled=event["n"],
                             ruled_by=event.get("actor"), ruled_at=event.get("at"))
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
    """The decisions of `state` not yet ruled, open or settled by the lead, each as
    `(key, body)` for the index: the basic form with its state, stage, weight, whether
    it forecloses, and `not_ratified`, why the round's flow puts no decision to the
    operator, None when it does."""
    unratified = not_ratified(state)
    for one in of_round(state):
        if one.state in (OPEN, SETTLED):
            body = one.data()
            yield body.pop("id"), {**{key: value for key, value in body.items()
                                      if key not in ("ruling", "chose", "record",
                                                     "options")},
                                   "not_ratified": unratified}


def rule_line(name: str, key: str) -> str:
    """The command that records the operator's ruling of a decision."""
    return f"moxi rounds rule {name} {key}={ACCEPT}|{REJECT} --record PTR"


def settle_line(name: str, key: str) -> str:
    """The commands the lead settles a minor decision with, or raises it to major."""
    return f"moxi rounds mark {name} {key} --accept, --reject or --major --record PTR"


# -- whose it is -------------------------------------------------------------------------

#: The weights each value of the project's `ratify` setting puts to the operator:
#: `majors` the major ones, `majors+minors` both. `off` puts none.
PUT_BY = {"majors": (report_block.MAJOR,),
          "majors+minors": (report_block.MAJOR, report_block.MINOR)}

#: Why a decision of a stage not yet finished is not put to the operator: it is put once
#: the stage finishes, when the rest of the rule holds.
NOT_FINISHED = "stage {stage} is not finished"


def not_ratified(state: states.State) -> str | None:
    """Why the round's flow puts no decision to the operator, or None when it does: the
    flow has a `ratify` step, as `flows.has` reads it, and no skip of it stands."""
    if not flows.has(state.steps, RATIFY):
        return "the flow has no ratify step"
    # Imported here: skips writes through the write path, which loads the index,
    # which reads this module.
    from . import skips

    if skips.is_skipped(state, RATIFY):
        return "ratify is skipped"
    return None


def not_put(weight: str, *, setting: str, stage: str, finished: bool, declared: bool,
            unratified: str | None) -> str | None:
    """Why a decision not yet ruled is not the operator's, in a few words, or None when
    it is: the first that fails of the round's flow putting decisions to the operator
    (`unratified` None, as `not_ratified` gives it), `setting`, the project's `ratify`
    setting, naming its `weight`, its `stage` being `finished`, and the build that
    finished the stage having `declared` it. The ratify export draws by the same rule."""
    if unratified is not None:
        return unratified
    if weight not in PUT_BY.get(setting, ()):
        return f"ratify is {setting}"
    if not finished:
        return NOT_FINISHED.format(stage=stage)
    if not declared:
        return f"the build that finished {stage} did not declare it"
    return None


def why_not(state: states.State, setting: str) -> dict[str, str | None]:
    """Each decision of the round not yet ruled or settled, by key, with why it is not
    the operator's under `setting` (`not_put`), None where it is."""
    done = {build.stage for build in finished_builds(state)}
    landed = {one.key for one in reached(state)}
    unratified = not_ratified(state)
    return {one.key: not_put(one.weight, setting=setting, stage=one.stage,
                             finished=one.stage in done, declared=one.key in landed,
                             unratified=unratified)
            for one in of_round(state) if one.state == OPEN}


def to_rule(state: states.State, setting: str) -> tuple[Decision, ...]:
    """The open decisions that are the operator's under `setting`: those the close
    waits for the operator to rule, in the order they reached the target."""
    why = why_not(state, setting)
    return tuple(one for one in reached(state)
                 if one.state == OPEN and why.get(one.key, "") is None)


def to_settle(state: states.State, setting: str) -> tuple[Decision, ...]:
    """The open minor decisions of the finished builds that are not the operator's under
    `setting`: the lead's to settle before the round closes."""
    why = why_not(state, setting)
    return tuple(one for one in reached(state)
                 if one.state == OPEN and one.weight == report_block.MINOR
                 and why.get(one.key) is not None)


def is_yet_to_come(why: str | None, stage: str) -> bool:
    """Whether a decision not put to the operator for `why` is put once its `stage`
    finishes: the rest of the rule already holds."""
    return why == NOT_FINISHED.format(stage=stage)


def open_line(name: str, key: str, *, weight: str, why: str | None, ended: bool) -> str:
    """What is said under a decision not yet ruled or settled, by the one rule: the
    operator's to rule, with the command, when `why` is None; not yet put to the operator
    while only its stage is unfinished; a major one not put to the operator is recorded
    only if the operator rules it; a minor one not the operator's is the lead's to
    settle. A round that has ended takes no ruling, so there it is left open, with no
    command."""
    if ended:
        return ("left open, put to the operator and not ruled" if why is None
                else f"left open, not put to the operator ({why})")
    if why is None:
        return f"the operator's to rule: {rule_line(name, key)}"
    if is_yet_to_come(why, key.split(".", 1)[0]):
        return f"not yet put to the operator ({why}): {rule_line(name, key)}"
    if weight == report_block.MAJOR:
        return (f"not put to the operator ({why}); recorded only if the operator rules "
                f"it: {rule_line(name, key)}")
    return f"the lead's to settle: {settle_line(name, key)}"


def settled_line(one: Decision | Mapping) -> str:
    """What is said under a decision the lead settled."""
    chose, record = ((one.chose, one.record) if isinstance(one, Decision)
                     else (one.get("chose"), one.get("record")))
    return f"settled by the lead: {chose}, record {record}"


def left_open(state: states.State, setting: str) -> tuple[dict, ...]:
    """The decisions of the finished builds still open, in that order, each with
    whether it is the operator's under `setting`: what the close of the round leaves
    open. One only a dropped build declared is not among them."""
    why = why_not(state, setting)
    return tuple({"id": one.key, "weight": one.weight, "forecloses": one.forecloses,
                  "text": one.text, "put": why.get(one.key, "") is None}
                 for one in reached(state) if one.state == OPEN)


def left_open_line(one: dict) -> str:
    """A decision the close left open, as `left_open` gives it, in a line."""
    head = ("left open, put to the operator and not ruled" if one["put"]
            else "left open, not put to the operator")
    weight = one["weight"] + (", forecloses" if one["forecloses"] else "")
    return f"{head}: decision {one['id']}, {weight}: {one['text']}"


__all__ = ["KEY", "ITEM", "ACCEPT", "REJECT", "OPTIONS", "BLOCKS", "OPEN", "RULED",
           "SETTLED", "SETTLE", "RAISE", "LEAD", "RATIFY", "PUT_BY", "NOT_FINISHED",
           "Decision", "listed", "lighter", "of_round", "of_build", "finished",
           "finished_builds", "reached", "unruled_majors", "find", "item", "items",
           "rule_line", "settle_line", "not_ratified", "not_put", "why_not", "to_rule",
           "to_settle", "is_yet_to_come", "open_line", "settled_line", "left_open",
           "left_open_line"]
