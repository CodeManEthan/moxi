"""The state of each step of a round: the one place that says whether a step is done.

A round follows the steps its log recorded. `of(state)` gives each of them in order with
its state, `one(state, step, stage)` a single one, and `current(state)` the step the
round is at: the first that is neither done nor skipped. Everything that needs to know
whether a step is done asks here, so what refuses a verb and what explains the refusal
are the same answer, and a skip that lets one of them past lets all of them past.

**The states.** A step is `NOT_DUE` (nothing asks for it yet), `WAITING` (it can't be
done until what its `waits` name is met; each names what is waited for and the command
that meets it), `READY` (it can be done now), `DONE` or `SKIPPED`. A step whose row is
not in `ROWS` reads `NOT_BUILT` unless it is skipped: nothing is known of it, and
nothing reads it as done.

**The order.** The steps a flow lists, in its order, with the ones it has without
listing them put beside the step each comes with: the questions step after the step that
verifies the design, `commission` before `build`, `check` after it, and `ratify` after
`finish`. A step done once for each build is given once for each stage of the plan, in
the order the stages were declared; with no stage in the plan it is given once, for no
stage.

**The rows.** `ROWS` maps a step to the function that says its state. A row is given the
round's state and an `Asked` (the round's name for the commands it prints, the stage
for a step of one stage, and how to tell whether another round is closed) and returns a
`Step`. A step is added by adding its row; nothing else changes.

**A skip.** A skip that stands is read here, before any row and whether or not the step
has one: the step reads `SKIPPED`, so a row never asks about its own skip and a step
whose row is not built is passed like any other. The questions step is the one
exception: a skip of it for a stage lets the stage past the questions it lists and no
other, so its row says when the stage reads as skipped.

- `design`: done once a version is frozen; ready until then.
- `verify-design`: done while the design's standing at
  its current version is one that counts as verified. It waits for a frozen version,
  for that version to pass the check of its trace rows where the check was asked for,
  and for whatever must happen before a pass can read the design. `Step.asked` says
  which pass is asked for.
- the questions step: ready while a question of the round reads as open, with the
  questions in `Step.questions`; done when none does. Asked of one stage, it reads the
  questions that hold that stage: skipped where a skip for the stage stands and nothing
  holds it.
- `commission` of a stage: done once the stage has a build that is open or finished.
  Until then it waits for a frozen design where the flow has a design step, a
  verified one where it verifies the design (each step skipped counts as done), no
  question holding the stage, every stage in its `after` finished, where the trace
  check was asked for a row for every ruling that applies to the stage, and, in a
  flow that does not verify its design, no other stage of its repo with a build that
  is open or finished. Of a stage that stands for a child round it is not built.
  Asked with no stage (the plan holds none), it waits in a flow that verifies its
  design for the design to declare one; in any other flow it is the form that
  declares the stage as it commissions it, and waits for a frozen design where the
  flow has a design step and for no question that would hold the stage
  (`held_unbuilt`).

- `build` of a stage: not due until the stage has a build that is open or finished;
  then ready until the build has work, which is when git reads it `building` or
  `merged`, and done from then on, and once the build is finished. It is read from
  what `Asked.seen` says of the stage's build.
- `check` of a stage: done once the newest run of the repo's check passed at the
  build's tip, with the argv the check gives now (`checking.passes`), or, where the
  repo declares `ci`, once CI's form of the gate is met there apart from the target
  (`ci.why_not`), and once the build is finished. Where neither is met in a repo with
  `ci`, `Step.owes` says what each still owes. Until then it waits for the build to have work and for its
  worktree to be clean. It is read from what `Asked.seen` says of the stage's build;
  where that is not given the step waits on a read of git. A stage whose repo declares
  no check has no `check` step, where `Asked.seen` says so.

- `verify-build` of a stage: done once the build's verification counts at its tip
  (`findings.why_not_counted`): its newest build verdict is `publishable` and every
  commit since the head it read is named by a lead's mark or brought by a merge of
  the target the drift rule accepts; and once the build is finished. Until then it
  waits for the build to have work, and `Step.owes` says why a re-check is owed, with
  the range to read. It is read from what `Asked.seen` says of the stage's build.

**What git says.** The `build`, `check`, `verify-build` and `finish` rows, and a
step's of the user's own, read a stage's open build as the caller read it from git (`Asked.seen`,
which `checking.seen_of` gives). Every reader of a step passes it. A caller that read nothing of git gets such a step as `WAITING`
on that read, with `Step.unread` set, and is to read git before it says the step: the
index keeps the step the log alone gives, and marks it so (`at`).

- a command step of the user's own, of a stage: done once the newest run recorded
  under its name passed at the build's tip with the argv of its entry in the round's
  recorded flow, its placeholders filled for the build there, and once the build is
  finished. Until then it waits, as `check` does, for the build to have work and for
  its worktree to be clean. A run of one step is never
  read for another. It is read from what `Asked.seen` says of the stage's build.
  (`command_row` gives its row; `row_of` finds it for a step the flow lists.)
- a report step of the user's own, of a stage: done once its newest report for the
  build is `publishable` and counts at the tip by `verify-build`'s rule, read on this
  step's reports alone (`Seen.reported`), and once the build is finished. Until then
  it waits for the build to have work, and `Step.owes` says why a report is owed.
  (`report_row` gives its row.)

- `finish` of a stage: not due until the stage has a build that is open or finished,
  though it carries the holds on its stage from the start; done once the build is
  finished. Until then it waits for every gate of `finish_gates.unmet` that does not
  hold at the build's tip, each named with its number and the command that meets it
  (`Step.gates`), and for what keeps `finish` from running a command action
  (`finish_actions.blocked`); it is ready once none is left. It is read from what
  `Asked.seen` says of the stage's build.

- `ratify`, of the round: ready while a major decision that reached the target is not
  ruled (`decisions.unruled_majors`), each named with the command that rules it; done
  once none is left and every stage of the plan is finished, and not due before then.
  The project's `ratify` setting of `off` makes it done where it would be ready. The
  setting is read from what `Asked.seen` holds under no stage, the round's own; where
  that is not given and a decision would make the step ready, it waits on a read of
  the settings, as a step read from git waits on a read of git.
- `retro`, of the round: done once a retro report is recorded. Ready once the close
  waits for nothing else (`closing.waits_before_retro`), and not due before then. The
  `ratify` setting that close reads is read as the `ratify` row reads it; where it is
  not given and it would decide whether the step is due, the step waits on that read.

**A hold.** `held_by(state, stage)` gives what the questions that hold a stage make it
wait for. A stage's `commission` and `finish` carry these as their `waits` even where
their rows are not built, so a round whose flow has no questions step still shows the hold, on
the stage it stops.

Nothing here reads a file, the config or git: a round's state, with its project's
for the standing rulings, and what the caller read of each open build (`Seen`), is the
whole input.
Whether `design.md` still has the bytes that were frozen is not a step's state; a verb
that needs the design as it was verified asks `versions.unchanged` beside this.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass

from . import (brake, builds, checking, decisions, findings, finish_actions, finish_gates,
               flows, holds, questions, skips, stages, tracing, versions)
from . import ci as ci_reads
from . import state as states
from .errors import Unmet

NOT_DUE = "not-due"
WAITING = "waiting"
READY = "ready"
DONE = "done"
SKIPPED = "skipped"
#: The five states a step has.
STATES = (NOT_DUE, WAITING, READY, DONE, SKIPPED)
#: What a step reads as while no row says its state. It is none of the five, and is
#: never read as done.
NOT_BUILT = "not-built"
#: The states that let a round past a step.
SETTLED = (DONE, SKIPPED)

DESIGN = "design"
VERIFIES = "verify-design"
QUESTIONS = skips.QUESTIONS
#: The steps of a stage that a question holding the stage stops.
HELD = ("commission", "finish")

#: Where a step a flow has without listing it goes: `(the step, before or after, the
#: step it comes with)`.
_BEFORE, _AFTER = "before", "after"
_UNLISTED = ((QUESTIONS, _AFTER, VERIFIES), ("commission", _BEFORE, "build"),
             ("check", _AFTER, "build"), ("ratify", _AFTER, "finish"))


@dataclass(frozen=True)
class Seen:
    """What the caller read of a stage's open build now, from git and the settings:
    `state`, the build's state as git reads it; `tip`, its branch's tip; `clean`,
    whether its worktree is on this machine with the build's branch checked out and
    nothing uncommitted; `check`, the argv the repo's check gives now at the tip, None
    when the repo declares no check; `values`, what the placeholders of a command read
    for the build at the tip (`checking.values`); `ci`, what CI's form of the check
    gate compares at the tip where the repo declares `ci` (`ci.Now`), else None;
    `head`, the tip, or the build's head where its branch is gone; `since`, the commits
    from the head its newest build verdict read to `head`, None with no such verdict or
    when git does not hold that head; `drift`, the build's drift against that verdict
    (`drift.Drift`), None with none; `reported`, for each report step of the user's
    own, its `(since, drift)` read the same way against that step's newest verdict;
    `target_before`, `P`, the target before the build reached it (its head while the
    build is off it); `holds_target`, whether `P` is the tip or an ancestor of it, None
    when that could not be read; `tree`, the worktree as `finish` reads it: None where
    it no longer exists, else `clean`, `changed` (a tracked change or an untracked path)
    or `elsewhere` (made on another machine); `check_on`, the repo's setting of that
    name; `actions`, the repo's finish actions, each with its `id`; `shares`, the
    `(round, stage)` of each build it shares commits with; `ratify`, the project's
    setting of that name;
    `problem`, what kept the build from being read, in which case nothing else is
    known. What was read for the round's own steps is kept under no stage (`None`),
    with `ratify` alone filled, so it is there with no build open."""

    state: str | None = None
    tip: str | None = None
    clean: bool = False
    check: tuple[str, ...] | None = None
    values: Mapping[str, str] | None = None
    ci: ci_reads.Now | None = None
    head: str | None = None
    since: tuple[str, ...] | None = None
    drift: object = None
    reported: Mapping[str, tuple] | None = None
    target_before: str | None = None
    holds_target: bool | None = None
    tree: str | None = None
    check_on: str = "merged"
    actions: tuple[Mapping, ...] = ()
    shares: tuple[tuple[str, str], ...] = ()
    ratify: str = "majors"
    problem: Unmet | None = None


@dataclass(frozen=True)
class Asked:
    """What a row is asked about: `name`, the round's name, for the commands it
    prints; `stage`, for a step of one stage; `closed`, which answers whether a round,
    by name, is closed, for a question parked until one is (`questions.is_closed` when
    None); `register`, the state of the project's log, for the standing rulings a
    stage's trace rows owe (with None, only the round's own rulings are read);
    `seen`, what was read of each stage's open build, by stage (None: nothing was)."""

    name: str = "ROUND"
    stage: str | None = None
    closed: Callable[[str], bool] | None = None
    register: states.State | None = None
    seen: Mapping[str, Seen] | None = None


@dataclass(frozen=True)
class Step:
    """One step of a round and its state.

    - `step`: its name; `stage`: the stage, for a step of one stage.
    - `state`: one of `STATES`, or `NOT_BUILT`.
    - `waits`: for a step that waits, each thing waited for with the command that
      meets it. A step of a stage whose row is not built carries the holds on its
      stage here, whatever else it would wait for.
    - `questions`: for the questions step, the ids of the questions that make it due.
    - `asked`: for the step that verifies the design, which pass is asked for.
    - `entry`: the step's entry in the flow, which says who does it, at which tier
      and what they read; None for a step the flow has without listing it.
    - `unread`: its state is read from git, which the caller did not read, so the
      state given is no answer.
    - `owes`: for a gate that more than one thing meets, what each still owes, in
      words: the `check` of a repo that declares `ci`.
    - `gates`: for `finish`, the numbers of the gates in `waits`, one for each.
    """

    step: str
    state: str
    stage: str | None = None
    waits: tuple[Unmet, ...] = ()
    questions: tuple[str, ...] = ()
    asked: brake.Asked | None = None
    entry: Mapping | None = None
    unread: bool = False
    owes: tuple[str, ...] = ()
    gates: tuple[int, ...] = ()

    @property
    def settled(self) -> bool:
        """Whether the round is past this step: it is done or skipped."""
        return self.state in SETTLED

    def data(self) -> dict:
        """The step as data: its name, stage and state, and what it waits for."""
        return {"step": self.step, "stage": self.stage, "state": self.state,
                "waits": [{"reason": one.reason, "fix": one.fix} for one in self.waits],
                "questions": list(self.questions)}


Row = Callable[[states.State, Asked], Step]


# -- the rows ----------------------------------------------------------------------------

def _design(state: states.State, about: Asked) -> Step:
    return Step(DESIGN, READY if versions.current(state) is None else DONE)


def _verify_design(state: states.State, about: Asked) -> Step:
    asked = brake.asked(state, name=about.name)
    if versions.standing(state).word in versions.VERIFIED:
        return Step(VERIFIES, DONE, asked=asked)
    fold = f"moxi rounds fold {about.name}"
    version = versions.current(state)
    if version is None:
        return Step(VERIFIES, WAITING, asked=asked, waits=(
            Unmet("no version of the design is frozen", fold),))
    waits = []
    if not tracing.passes(state):
        found = tracing.recorded(version)
        said = tracing.lines({**found.stored(), "version": version.n, "stale_now": ()})
        waits.append(Unmet(f"{said[0]}; a version whose rows pass is waited for",
                           f"give {versions.DESIGN_FILE} the rows, then {fold}"))
    if asked is not None and asked.instead:
        # No pass can read the design until this is done.
        waits.append(Unmet(asked.why, "; ".join(asked.instead)))
    return Step(VERIFIES, WAITING if waits else READY, waits=tuple(waits), asked=asked)


def _docket(state: states.State, about: Asked) -> Step:
    if about.stage is not None:
        holding = holds.on_stage(state, about.stage, closed=about.closed)
        if holding:
            return Step(QUESTIONS, READY, stage=about.stage,
                        questions=tuple(one.id for one in holding))
        skipped = skips.is_skipped(state, QUESTIONS, about.stage)
        return Step(QUESTIONS, SKIPPED if skipped else DONE, stage=about.stage)
    asked = tuple(
        one.id for one in state.questions
        if questions.current(one, state, closed=about.closed) == questions.OPEN)
    return Step(QUESTIONS, READY if asked else DONE, questions=asked)


COMMISSION = "commission"


def _commission(state: states.State, about: Asked) -> Step:
    if about.stage is None:
        return _declaring(state, about)
    stage = stages.find(state, about.stage)
    if stage is None or stage.round is not None:
        # A stage that stands for a child round has no build: it is not built here.
        return Step(COMMISSION, NOT_BUILT, stage=about.stage,
                    waits=() if stage is None else held_by(
                        state, stage.id, name=about.name, closed=about.closed))
    if stages.build_of(state, stage.id) is not None:
        return Step(COMMISSION, DONE, stage=stage.id)
    name = about.name
    waits: list[Unmet] = []
    if not flows.has(state.steps, VERIFIES) and stage.repo is not None:
        other = stages.built_in(state, stage.repo, besides=stage.id)
        if other is not None:
            waits.append(Unmet(
                f"{other.id} has a build in {stage.repo} that is open or finished, and "
                f"the flow {state.flow} does not verify its design: such a round builds "
                "one stage in a repo",
                f"move the round up to a flow that has a {VERIFIES} step: moxi rounds "
                f"mark {name} --flow NAME"))
    if not is_settled(state, DESIGN, closed=about.closed):
        waits.append(Unmet("no version of the design is frozen",
                           f"moxi rounds fold {name}"))
    if not is_settled(state, VERIFIES, closed=about.closed):
        word = versions.standing(state).word
        waits.append(Unmet(f"the design is not verified: it stands at {word}",
                           f"moxi rounds next {name}"))
    waits += held_by(state, stage.id, name=name, closed=about.closed)
    waits += [Unmet(f"{stage.id} waits for {other}, which is not finished",
                    f"moxi rounds next {name}")
              for other in stages.waits_for(state, stage.id)]
    fold = f"moxi rounds fold {name}"
    waits += [Unmet(f"{one.id} holds {stage.id} until a version of the design has a "
                    f"row for it: {one.text}",
                    f"give {versions.DESIGN_FILE} a row for {one.id}, then {fold}")
              for one in tracing.holding(state, stage.id, about.register)]
    return Step(COMMISSION, WAITING if waits else READY, stage=stage.id,
                waits=tuple(waits))


def _declaring(state: states.State, about: Asked) -> Step:
    """`commission` with no stage: the round's plan holds none. In a flow that does
    not verify its design, `commission` declares the stage as it commissions it, and
    the step waits for what that stage would: a frozen design where the flow has a
    design step, and no question that would hold it. In a flow that verifies its
    design, the design declares each stage and nothing can be commissioned until a
    version does."""
    name = about.name
    fold = f"moxi rounds fold {name}"
    if flows.has(state.steps, VERIFIES):
        return Step(COMMISSION, WAITING, waits=(
            Unmet("no stage is planned: in a flow that verifies its design, the "
                  "design declares each stage it builds",
                  f"give {versions.DESIGN_FILE} a [[stage]], then {fold}"),))
    waits: list[Unmet] = []
    if not is_settled(state, DESIGN, closed=about.closed):
        waits.append(Unmet("no version of the design is frozen", fold))
    waits += held_unbuilt(state, stages.next_id(state), name=name, closed=about.closed)
    return Step(COMMISSION, WAITING if waits else READY, waits=tuple(waits))


BUILD = "build"


def _not_read(step: str, stage: str, what: str, name: str) -> Step:
    return Step(step, WAITING, stage=stage, unread=True, waits=(Unmet(
        f"{what} is read from git, which was not read here", f"moxi rounds next {name}"),))


def _build(state: states.State, about: Asked) -> Step:
    stage = about.stage
    planned = None if stage is None else stages.find(state, stage)
    if planned is not None and planned.round is not None:
        # A stage that stands for a child round has no build: it is not built here.
        return Step(BUILD, NOT_BUILT, stage=stage)
    built = None if stage is None else stages.build_of(state, stage)
    if built is None:
        return Step(BUILD, NOT_DUE, stage=stage)
    if built == stages.FINISHED:
        return Step(BUILD, DONE, stage=stage)
    seen = (about.seen or {}).get(stage)
    if seen is None:
        return _not_read(BUILD, stage, f"whether {stage} has work", about.name)
    if seen.problem is not None:
        return Step(BUILD, WAITING, stage=stage, waits=(seen.problem,))
    return Step(BUILD, DONE if seen.state in checking.WITH_WORK else READY, stage=stage)


CHECK = checking.CHECK


def _check(state: states.State, about: Asked) -> Step:
    stage = about.stage
    built = None if stage is None else stages.build_of(state, stage)
    if built is None:
        return Step(CHECK, NOT_DUE, stage=stage)
    if built == stages.FINISHED:
        return Step(CHECK, DONE, stage=stage)
    seen = (about.seen or {}).get(stage)
    if seen is None:
        return _not_read(CHECK, stage, f"whether {stage}'s check passed at its tip",
                         about.name)
    if seen.problem is not None:
        return Step(CHECK, WAITING, stage=stage, waits=(seen.problem,))
    if seen.tip is not None and seen.check is not None and checking.passes(
            state, stage, CHECK, tip=seen.tip, argv=seen.check):
        return Step(CHECK, DONE, stage=stage)
    owes: tuple[str, ...] = ()
    if seen.ci is not None and seen.tip is not None and seen.check is not None:
        by_ci = ci_reads.why_not(state, stage, tip=seen.tip, now=seen.ci)
        if by_ci is None:
            return Step(CHECK, DONE, stage=stage)
        full = checking.why_not(state, stage, CHECK, tip=seen.tip, argv=seen.check)
        owes = (f"by the full check: {full}", f"by CI: {by_ci}")
    waits = _before_a_run(stage, seen, "check")
    return Step(CHECK, WAITING if waits else READY, stage=stage, waits=waits, owes=owes)


VERIFY_BUILD = findings.VERIFY_BUILD


def _verify_build(state: states.State, about: Asked) -> Step:
    stage = about.stage
    built = None if stage is None else stages.build_of(state, stage)
    if built is None:
        return Step(VERIFY_BUILD, NOT_DUE, stage=stage)
    if built == stages.FINISHED:
        return Step(VERIFY_BUILD, DONE, stage=stage)
    seen = (about.seen or {}).get(stage)
    if seen is None:
        return _not_read(VERIFY_BUILD, stage, f"whether {stage}'s verification counts "
                         "at its tip", about.name)
    if seen.problem is not None:
        return Step(VERIFY_BUILD, WAITING, stage=stage, waits=(seen.problem,))
    build = builds.current(state, stage)
    covered = () if seen.drift is None else seen.drift.covered
    why = findings.why_not_counted(state, build, tip=seen.head, since=seen.since,
                                   covered=covered)
    if why is None:
        return Step(VERIFY_BUILD, DONE, stage=stage)
    waits = ()
    if seen.state not in checking.WITH_WORK:
        waits = (Unmet(f"{stage} has no work to verify: it reads {seen.state}",
                       "commit the build's work on its branch"),)
    return Step(VERIFY_BUILD, WAITING if waits else READY, stage=stage, waits=waits,
                owes=(why,))


def _before_a_run(stage: str, seen: Seen, what: str) -> tuple[Unmet, ...]:
    """What a run at a build's tip waits for, as the verb that runs it checks before it
    runs: work on the branch and a clean worktree. A row that read ready where the verb
    refuses would tell the user to run what is then refused."""
    waits = []
    if seen.state not in checking.WITH_WORK:
        waits.append(Unmet(f"{stage} has no work to {what}: it reads {seen.state}",
                           "commit the build's work on its branch"))
    if not seen.clean:
        waits.append(Unmet(f"the worktree of {stage} is not clean on its branch here",
                           "commit or put away what is not committed, in the build's "
                           "worktree on the machine that made it"))
    return tuple(waits)


FINISH = findings.FINISH


def _finish(state: states.State, about: Asked) -> Step:
    stage = about.stage
    planned = None if stage is None else stages.find(state, stage)
    built = None if stage is None else stages.build_of(state, stage)
    if planned is not None and planned.round is not None:
        # A stage that stands for a child round has no build: it is not built here.
        return Step(FINISH, NOT_BUILT, stage=stage, waits=held_by(
            state, stage, name=about.name, closed=about.closed))
    if built is None:
        return Step(FINISH, NOT_DUE, stage=stage, waits=() if stage is None else held_by(
            state, stage, name=about.name, closed=about.closed))
    if built == stages.FINISHED:
        return Step(FINISH, DONE, stage=stage)
    seen = (about.seen or {}).get(stage)
    if seen is None:
        return _not_read(FINISH, stage, f"whether the gates of {stage}'s finish hold",
                         about.name)
    if seen.problem is not None:
        return Step(FINISH, WAITING, stage=stage, waits=(seen.problem,))
    found = finish_gates.unmet(state, stage, seen=seen, name=about.name,
                               closed=about.closed, register=about.register)
    waits = [one.unmet for one in found]
    build = builds.current(state, stage)
    tip = seen.tip or seen.head
    if build is not None and tip is not None:
        # The verb refuses these before it runs anything, so the row waits for them.
        waits += finish_actions.blocked(state, build, tip, seen.actions,
                                        name=about.name, worktree_gone=seen.tree is None)
    return Step(FINISH, WAITING if waits else READY, stage=stage, waits=tuple(waits),
                gates=tuple(one.number for one in found))



RATIFY = "ratify"
#: The project's `ratify` setting that puts no decision to the operator.
RATIFY_OFF = "off"


def _ratify(state: states.State, about: Asked) -> Step:
    unruled = decisions.unruled_majors(state)
    if not unruled:
        planned = stages.in_plan(state)
        finished = questions.finished_stages(state)
        done = bool(planned) and all(stage.id in finished for stage in planned)
        return Step(RATIFY, DONE if done else NOT_DUE)
    seen = (about.seen or {}).get(None)
    if seen is None:
        return Step(RATIFY, WAITING, unread=True, waits=(Unmet(
            "whether the project's ratify setting puts these decisions to the operator "
            "is read from the settings, which were not read here",
            f"moxi rounds next {about.name}"),))
    if seen.ratify == RATIFY_OFF:
        return Step(RATIFY, DONE)
    return Step(RATIFY, READY, waits=tuple(
        Unmet(f"decision {one.key}, major and not yet ruled: {one.text}",
              decisions.rule_line(about.name, one.key)) for one in unruled))

RETRO = "retro"


def _retro(state: states.State, about: Asked) -> Step:
    if any(one.kind == RETRO for one in state.verdicts):
        return Step(RETRO, DONE)
    # Imported here: the close reads every step, this one among them.
    from . import closing

    seen = (about.seen or {}).get(None)
    ratify = closing.RATIFY_DEFAULT if seen is None else seen.ratify
    if not closing.waits_before_retro(state, ratify=ratify):
        return Step(RETRO, READY)
    if seen is None and not closing.waits_before_retro(state, ratify=RATIFY_OFF):
        return Step(RETRO, WAITING, unread=True, waits=(Unmet(
            "whether the project's ratify setting puts the round's decisions to the "
            "operator before its retrospective is read from the settings, which were "
            "not read here", f"moxi rounds next {about.name}"),))
    return Step(RETRO, NOT_DUE)


def report_row(step: str) -> Row:
    """The row of the report step `step` of the user's own: done at the tip when its
    own newest report is publishable and counts there by the rule the build's
    verification counts by, read on this step's reports alone."""

    def row(state: states.State, about: Asked) -> Step:
        stage = about.stage
        built = None if stage is None else stages.build_of(state, stage)
        if built is None:
            return Step(step, NOT_DUE, stage=stage)
        if built == stages.FINISHED:
            return Step(step, DONE, stage=stage)
        seen = (about.seen or {}).get(stage)
        if seen is None:
            return _not_read(step, stage, f"whether {stage}'s report of {step} counts "
                             "at its tip", about.name)
        if seen.problem is not None:
            return Step(step, WAITING, stage=stage, waits=(seen.problem,))
        build = builds.current(state, stage)
        since, moved = (seen.reported or {}).get(step, (None, None))
        covered = () if moved is None else moved.covered
        why = findings.why_not_counted(state, build, tip=seen.head, since=since,
                                       covered=covered, step=step)
        if why is None:
            return Step(step, DONE, stage=stage)
        waits = ()
        if seen.state not in checking.WITH_WORK:
            waits = (Unmet(f"{stage} has no work for {step} to read: it reads "
                           f"{seen.state}", "commit the build's work on its branch"),)
        return Step(step, WAITING if waits else READY, stage=stage, waits=waits,
                    owes=(why,))

    return row


def command_row(step: str) -> Row:
    """The row of the command step `step` of the user's own."""

    def row(state: states.State, about: Asked) -> Step:
        stage = about.stage
        built = None if stage is None else stages.build_of(state, stage)
        if built is None:
            return Step(step, NOT_DUE, stage=stage)
        if built == stages.FINISHED:
            return Step(step, DONE, stage=stage)
        seen = (about.seen or {}).get(stage)
        if seen is None:
            return _not_read(step, stage, f"whether {stage}'s run of {step} passed at its "
                             "tip", about.name)
        if seen.problem is not None:
            return Step(step, WAITING, stage=stage, waits=(seen.problem,))
        entry = checking.step_entry(state, step)
        if (entry is not None and seen.tip is not None and seen.values is not None
                and checking.passes(state, stage, step, tip=seen.tip,
                                    argv=checking.fill(entry["argv"], seen.values))):
            return Step(step, DONE, stage=stage)
        waits = _before_a_run(stage, seen, f"run {step} on")
        return Step(step, WAITING if waits else READY, stage=stage, waits=waits)

    return row


#: The function that says each step's state. A step with no row reads `NOT_BUILT`,
#: and a skipped step is read before its row is asked. A step of the user's own has
#: no row here: `row_of` gives it its own.
ROWS: dict[str, Row] = {DESIGN: _design, VERIFIES: _verify_design, QUESTIONS: _docket,
                        COMMISSION: _commission, BUILD: _build, CHECK: _check,
                        VERIFY_BUILD: _verify_build, FINISH: _finish, RATIFY: _ratify,
                        RETRO: _retro}


def row_of(step: str, entry: Mapping | None) -> Row | None:
    """The row of the step `step`, whose entry in the flow is `entry`: its row in
    `ROWS`, the command or the report row for a step of the user's own, else None."""
    row = ROWS.get(step)
    if row is None and entry is not None and flows.is_user_step(entry):
        if entry.get("does") == flows.COMMAND:
            return command_row(step)
        if entry.get("does") == flows.REPORT:
            return report_row(step)
    return row


# -- a hold on a stage -------------------------------------------------------------------

def answer(name: str, question: states.Question) -> str:
    """The command that records the operator's answer to `question` of the round
    `name`: he chooses a key where the question offers options, and says a sentence
    where it offers none. Everything that prints the command for a question asks
    here, so the line is one that runs once its placeholders are filled."""
    how = "--chose K" if question.options else "--text TEXT"
    return f"moxi rounds rule {name} {question.id} {how} --record PTR"


def held_by(state: states.State, stage: str, *, name: str = "ROUND",
            closed: Callable[[str], bool] | None = None) -> tuple[Unmet, ...]:
    """What the questions that hold the stage `stage` make it wait for, in the order
    they were asked: each names the question and the command that records the
    operator's answer. Empty when nothing holds the stage."""
    return tuple(
        Unmet(f"{one.id} holds {stage}: {one.text}", answer(name, one))
        for one in holds.on_stage(state, stage, closed=closed))


def held_unbuilt(state: states.State, stage: str, *, name: str = "ROUND",
                 closed: Callable[[str], bool] | None = None) -> tuple[Unmet, ...]:
    """What the questions that would hold the stage `stage`, not declared yet, make a
    `commission` that declares it wait for: a question whose `blocks` is every stage
    holds a stage the moment it is declared, so nothing is declared while one is
    open. Each names the operator's ways past it."""
    found = []
    for one in holds.on_stage(state, stage, closed=closed):
        said = ("holds every stage" if one.blocks == questions.ALL
                else f"names {stage}")
        found.append(Unmet(
            f"{one.id} {said}, so it holds {stage} the moment commission declares it: "
            f"{one.text}",
            f"the operator rules it ({answer(name, one)}), withdraws or parks it "
            f"(moxi rounds rule {name} withdraw:{one.id} --record PTR, or "
            f"park:{one.id} --until WHAT), or narrows it (moxi rounds rule {name} "
            f"narrow:{one.id}=LIST|none --record PTR)"))
    return tuple(found)


# -- the steps of a round ----------------------------------------------------------------

def order(steps) -> tuple[tuple[str, Mapping | None], ...]:
    """The steps a flow whose entries are `steps` has, in order, each with its entry:
    the listed ones as listed, and the ones it has without listing them beside the
    step each comes with."""
    found: list[tuple[str, Mapping | None]] = [
        (str(entry.get("step")), entry) for entry in steps]
    for step, side, beside in _UNLISTED:
        names = [name for name, _entry in found]
        if step in names or beside not in names or not flows.has(steps, step):
            continue
        found.insert(names.index(beside) + (side == _AFTER), (step, None))
    return tuple(found)


def _is_of_a_stage(step: str) -> bool:
    # A step of the user's own is done for each build, like the stage steps.
    return flows.STEPS.get(step, flows.STAGE) == flows.STAGE


def _read(state: states.State, step: str, entry: Mapping | None, about: Asked) -> Step:
    # The questions step's own row reads its skip: such a skip lets a stage past the
    # questions it lists, and the stage is held again by one asked afterwards.
    if step != QUESTIONS and skips.is_skipped(state, step, about.stage):
        return Step(step, SKIPPED, stage=about.stage, entry=entry)
    row = row_of(step, entry)
    if row is not None:
        return dataclasses.replace(row(state, about), entry=entry)
    waits = ()
    if step in HELD and about.stage is not None:
        waits = held_by(state, about.stage, name=about.name, closed=about.closed)
    return Step(step, NOT_BUILT, stage=about.stage, waits=waits, entry=entry)


def _has(step: str, stage: str | None, seen: Mapping[str, Seen] | None) -> bool:
    """Whether the stage has the step: a stage whose repo, as read, declares no check
    has no `check` step."""
    if step != CHECK or stage is None or seen is None or stage not in seen:
        return True
    found = seen[stage]
    return found.problem is not None or found.check is not None


def _each(state: states.State, name: str, closed: Callable[[str], bool] | None,
          register: states.State | None,
          seen: Mapping[str, Seen] | None, but: Collection[str] = ()) -> Iterator[Step]:
    planned = [stage.id for stage in stages.in_plan(state)]
    for step, entry in order(state.steps):
        if step in but:
            continue
        of_stages = planned if _is_of_a_stage(step) and planned else [None]
        for stage in of_stages:
            if _has(step, stage, seen):
                yield _read(state, step, entry, Asked(name, stage, closed, register, seen))


def of(state: states.State, *, name: str = "ROUND",
       closed: Callable[[str], bool] | None = None,
       register: states.State | None = None,
       seen: Mapping[str, Seen] | None = None,
       but: Collection[str] = ()) -> tuple[Step, ...]:
    """Every step of the round `state` is of, in order, each with its state. `name`
    is the round's name, for the commands a wait prints, `closed` answers whether
    a round, by name, is closed (`questions.is_closed` when left out), `register`
    is the state of the project's log and `seen` what was read of each open build
    (see `Asked`). The steps named in `but` are left out, and their rows not asked."""
    return tuple(_each(state, name, closed, register, seen, but))


def one(state: states.State, step: str, stage: str | None = None, *,
        name: str = "ROUND", closed: Callable[[str], bool] | None = None,
        register: states.State | None = None,
        seen: Mapping[str, Seen] | None = None) -> Step | None:
    """The step `step` of the round, for the stage `stage` when it is a step of one
    stage; None when the round's flow has no such step, or the stage has none. The
    questions step asked of a stage reads the questions that hold that stage."""
    if not _has(step, stage, seen):
        return None
    for found, entry in order(state.steps):
        if found == step:
            return _read(state, step, entry, Asked(name, stage, closed, register, seen))
    return None


def is_settled(state: states.State, step: str, stage: str | None = None, *,
               closed: Callable[[str], bool] | None = None) -> bool:
    """Whether nothing waits on the step any longer: it is done or skipped, or the
    round's flow has no such step."""
    found = one(state, step, stage, closed=closed)
    return found is None or found.settled


def current(state: states.State, *, name: str = "ROUND",
            closed: Callable[[str], bool] | None = None,
            register: states.State | None = None,
            seen: Mapping[str, Seen] | None = None) -> Step | None:
    """The step the round is at: the first, in order, that is neither done nor
    skipped. None when every step is, and for a round that has ended."""
    if state.status != states.OPEN:
        return None
    return next((step for step in _each(state, name, closed, register, seen)
                 if not step.settled), None)


# -- what the index keeps ----------------------------------------------------------------

def _never(_name: str) -> bool:
    return False


def at(state: states.State):
    """The step the round is at, as one `(id, body)` for the index: the step, its
    stage and its state, or nothing for a round at no step.

    Git is not read here. Where the step is one whose state git says, the body has
    `git` true: whoever lists the row reads the step again, with git read.

    Only the round's own log is read, so a question parked until another round
    closes is read as parked. The body then names those rounds in `until`: once one
    of them is closed, whoever lists the row reads the step from the log again."""
    found = current(state, closed=_never)
    until = sorted({one.until[len(questions.UNTIL_ROUND):] for one in state.questions
                    if one.state == questions.PARKED
                    and (one.until or "").startswith(questions.UNTIL_ROUND)})
    if found is None and not (until and state.status == states.OPEN):
        return
    body = {"step": None, "stage": None, "state": None}
    if found is not None:
        body = {"step": found.step, "stage": found.stage, "state": found.state}
        if found.unread:
            body["git"] = True
    if until:
        body["until"] = until
    yield "step", body


def said(step: Mapping | None) -> str:
    """A step as data, in words: `verify-design, waiting` or `commission of B2,
    not-built`; `none` for a round at no step."""
    if step is None or step.get("step") is None:
        return "none"
    stage = "" if step.get("stage") is None else f" of {step['stage']}"
    return f"{step['step']}{stage}, {step['state']}"


__all__ = ["NOT_DUE", "WAITING", "READY", "DONE", "SKIPPED", "STATES", "NOT_BUILT",
           "SETTLED", "HELD", "COMMISSION", "BUILD", "CHECK", "FINISH", "RATIFY",
           "RATIFY_OFF", "RETRO", "Seen", "Asked",
           "Step", "Row",
           "ROWS", "report_row", "command_row", "row_of", "answer",
           "held_by", "held_unbuilt", "order", "of", "one", "is_settled", "current", "at",
           "said"]
