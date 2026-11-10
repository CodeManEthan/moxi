"""The brake on verifying a design: how many passes read it, and what ends them.

A design whose flow verifies it is read by one full pass. What follows that pass is
bounded by five rules, and the functions here are those rules:

1. **One full pass.** `asked(state)` says which pass is asked for next: a full pass, at
   the tier the verifying step names as `model`, while no report of a full pass is
   recorded; after one, only re-checks, at the step's `recheck_model`, or at its
   `model` when it names none. A second report of a full pass is still recorded, with
   the flag `EXTRA_FULL_PASS` that `flags` gives; it is refused nothing.
2. **Publishable is final.** A design that stands at `publishable` or at the lead's
   decision is asked for no pass. That standing holds for the version the verdict read
   and for every later version reached by changes of records only
   (`versions.standing`).
3. **A change of mechanism needs a re-check.** Freezing the design again leaves it at
   no standing, and `asked` then asks for a re-check that reads what changed: `since`
   is the newest version a pass read and `version` the current one, and `files` gives
   the two frozen files to compare.
4. **A change of records only needs no pass.** It is frozen with the lead's pointer to
   its record, and is one only when what the gates read is as the version before
   declared it: `differences` lists what is not.
5. **Two verdicts in a row that send the design back.** `asked` then asks for no pass
   and says what to do instead: state the rule, write the section again from it,
   freeze the design, and record the lead's decision on the thread with `decide`,
   which closes the thread or accepts its risk and gives the design the standing
   `lead-decision`.
   The run is counted from the lead's newest decision, so a decided thread is over:
   a design frozen again after it is asked for a re-check, and a second thread is
   decided only after two more such verdicts. A verdict counts only when it gave the
   design its standing as it was recorded: one for a version that was no longer
   current, or that already stood verified, does not. One text is counted once: a
   second verdict that sends back a version a counted one sent back, or a version
   frozen after it with the same bytes, does not count. The freezing that must follow
   the two is one that changed the design's text. `asked` and `decide` read the one
   count, `versions.sent_back`.

A round whose flow has no step that verifies its design never meets any of this:
`asked` gives None for it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import (ending, folders, model_tiers, pointers, questions, report_block, stages,
               stepping, versions, write)
from . import settings as own
from . import state as states
from .errors import BadArgument, NoLog, NoRound, Unmet

#: The step of a flow that verifies the round's design.
VERIFIES = stages.VERIFIES

#: The two passes that are asked for.
FULL = report_block.FULL
RECHECK = report_block.RECHECK

#: What is noted of a report of a full pass recorded after another one.
EXTRA_FULL_PASS = "extra-full-pass"

#: How many verdicts in a row that send the design back end the passes.
SENT_BACK_IN_A_ROW = 2

#: What the lead decides of a thread: it is closed, or its risk is accepted.
CLOSE = "close"
ACCEPT_RISK = "accept-risk"
DECISIONS = (CLOSE, ACCEPT_RISK)

#: A thread's name: letters, digits, `.`, `_` and `-`.
THREAD = re.compile(r"[A-Za-z0-9._-]+")

#: What names a thread where a mark names what it marks.
PREFIX = "thread:"


@dataclass(frozen=True)
class Asked:
    """What the brake asks for next of a round whose flow verifies its design.

    - `pass_`: `FULL` or `RECHECK`, or None when no pass is asked for.
    - `tier`: the tier the flow's verifying step names for that pass, as the round's
      log recorded the step; None when the step names none, or no pass is asked for.
    - `version`: the frozen version the pass reads, the current one. None when no
      pass is asked for, and for a re-check that has nothing to read yet because the
      design was not frozen again since it was sent back.
    - `since`: for a re-check, the newest version a pass read, which the re-check
      compares the current one with.
    - `standing`: the design's standing, which this was read from.
    - `why`: one line that says why this is what is asked for.
    - `instead`: what to do before a pass, or in place of one, in order; each line is
      a command or an instruction. Empty when the pass can be run now, and when the
      design is verified.
    """

    pass_: str | None
    standing: versions.Standing
    why: str
    tier: str | None = None
    version: int | None = None
    since: int | None = None
    instead: tuple[str, ...] = ()


def sent_back(state: states.State) -> bool:
    """Whether `SENT_BACK_IN_A_ROW` verdicts in a row each sent the design back, as
    `versions.sent_back` counts them: since the lead's newest decision, and only the
    ones that gave the design its standing as they were recorded."""
    return len(versions.sent_back(state)) >= SENT_BACK_IN_A_ROW


def frozen_since(state: states.State) -> bool:
    """Whether the design's text changed since the last verdict that sent it back: a
    version frozen after it with other bytes than the version before."""
    return versions.rewritten(state)


def had_full_pass(state: states.State) -> bool:
    """Whether a report of a full pass over the design is recorded."""
    return any(one.pass_ == FULL for one in versions.design_verdicts(state))


def flags(state: states.State, pass_: str | None) -> list[str]:
    """What is noted of a report of a design whose pass was `pass_`, recorded onto
    `state`: a full pass after another one is an extra."""
    return [EXTRA_FULL_PASS] if pass_ == FULL and had_full_pass(state) else []


def asked(state: states.State, *, name: str = "ROUND") -> Asked | None:
    """Which pass over the round's design is asked for next, or None for a round
    whose flow has no step that verifies its design. `name` is the round's name, for
    the commands in `instead`.

    In order: a design never frozen is asked for no pass yet; one that stands
    verified is asked for none; after `SENT_BACK_IN_A_ROW` verdicts in a row that
    sent it back, counted from the lead's newest decision, none, with what to do in
    place of one; while no full pass is
    recorded, a full pass; after one, a re-check of what changed since the newest
    version a pass read.
    """
    if not stepping.has_step(state, VERIFIES):
        return None
    stands = versions.standing(state)
    current = versions.current(state)
    fold = f"moxi rounds fold {name}"
    if current is None:
        return Asked(None, stands, "no version of the design is frozen, so there is "
                     "nothing for a pass to read", instead=(fold,))
    if stands.word in versions.VERIFIED:
        return Asked(None, stands, f"the design stands at {stands}")
    if sent_back(state):
        decide_it = (f"moxi rounds mark {name} {PREFIX}NAME --{CLOSE} --record PTR, or "
                     f"--{ACCEPT_RISK} in place of --{CLOSE}; PTR names where the lead "
                     "wrote why")
        rewrite = ("state the rule that the findings of the two passes break, and "
                   "write the section again from that rule", fold)
        return Asked(None, stands,
                     f"the last {SENT_BACK_IN_A_ROW} verdicts of the design each sent "
                     "it back, so no further pass is asked for",
                     instead=(decide_it,) if frozen_since(state)
                     else (*rewrite, decide_it))
    if not had_full_pass(state):
        return Asked(FULL, stands, "no full pass over the design is recorded",
                     tier=model_tiers.held_to(state.steps, VERIFIES, FULL),
                     version=current.n)
    tier = model_tiers.held_to(state.steps, VERIFIES, RECHECK)
    since = versions.verified(state)
    if since == current.n:
        return Asked(RECHECK, stands,
                     f"v{current.n} was sent back and the design was not frozen "
                     "since: a re-check reads what changed", tier=tier, since=since,
                     instead=(fold,))
    if (stands.word != versions.NONE and since is not None
            and versions.text_unchanged_since(state, since)):
        return Asked(RECHECK, stands,
                     f"v{since} was sent back and the design's text was not changed "
                     "since: a re-check reads what changed", tier=tier, since=since,
                     # Freezing the same text again would only ask this again.
                     instead=(f"change {versions.DESIGN_FILE} to answer what the "
                              "pass found", fold))
    return Asked(RECHECK, stands,
                 f"the design changed since v{since}, the last version a pass read",
                 tier=tier, version=current.n, since=since)


def files(folder: Path | str, found: Asked | None) -> tuple[Path, Path] | None:
    """The two frozen files a re-check compares, in the folder of the round: the
    newest version a pass read and the current one. None when `found` asks for no
    re-check, or for one that has nothing to read yet."""
    if (found is None or found.pass_ != RECHECK or found.since is None
            or found.version is None):
        return None
    return versions.file(folder, found.since), versions.file(folder, found.version)


# -- a change of records only ------------------------------------------------------------

def _by_id(declared: dict, kind: str) -> dict[str, dict]:
    return {one.get("id"): one for one in declared.get(kind) or ()
            if isinstance(one, dict)}


def _held(blocks) -> str:
    return blocks if isinstance(blocks, str) else ", ".join(blocks or ())


def differences(before: dict, now: dict) -> list[str]:
    """Where what the gates read differs between two versions' declarations, each as
    a line: a question or a stage that only one of them has, a question that holds up
    other stages, a stage that builds elsewhere or waits for other stages. A
    question's sentence, its options, a stage's slices and what else a block declares
    are read by no gate and are not compared."""
    found = []
    for kind, what in ((states.QUESTION, "question"), (states.STAGE, "stage")):
        was, new = _by_id(before, kind), _by_id(now, kind)
        found += [f"the {what} {one} is new" for one in new if one not in was]
        found += [f"the {what} {one} is gone" for one in was if one not in new]
        for one in new:
            if one not in was:
                continue
            if kind == states.QUESTION:
                held, holds = (entry.get("blocks", questions.ALL)
                               for entry in (was[one], new[one]))
                same = (held == holds if isinstance(held, str) or isinstance(holds, str)
                        else set(held or ()) == set(holds or ()))
                if not same:
                    found.append(f"{one} held up {_held(held)} and now holds up "
                                 f"{_held(holds)}")
                continue
            for key in ("repo", "branch", "round"):
                if was[one].get(key) != new[one].get(key):
                    found.append(f"{one} had {key} {was[one].get(key)} and now has "
                                 f"{new[one].get(key)}")
            after, waits = (set(entry.get("after") or ()) for entry in (was[one], new[one]))
            if after != waits:
                found.append(f"{one} waited for {', '.join(sorted(after)) or 'nothing'} "
                             f"and now waits for {', '.join(sorted(waits)) or 'nothing'}")
    return found


# -- the lead's decision on a thread -----------------------------------------------------

@dataclass(frozen=True)
class Decided:
    """A thread as the lead decided it: the round's `name`, the `decision` as the log
    now holds it, the design's `standing` after it, and the `event` written. `found`
    says the log held this decision already, so nothing was written and `event` is
    None. `failed` pairs each after-append callback that raised with what it
    raised."""

    name: str
    decision: states.Decided
    standing: versions.Standing
    event: dict | None
    failed: tuple[tuple[Callable, Exception], ...] = ()
    found: bool = False


def decide(name: str, thread: str, decision: str, *, record: str | None, actor: str,
           settings=None, wait_s: float = write.WAIT_S, at: str | None = None) -> Decided:
    """Record the lead's decision on the thread `thread` of the verification of the
    design of the round `name`, `<project>/<slug>`: `CLOSE` or `ACCEPT_RISK`. The
    design then stands at `lead-decision` for its current version.

    - `record`: the pointer to where the lead wrote why.
    - `actor`: who records it, the event's actor.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found, and `Refused` when the
    round has ended, the pointer is missing or has no known shape, the last
    `SENT_BACK_IN_A_ROW` verdicts in a row did not each send the design back since
    the lead's last decision, or the design's text was not changed and frozen since
    the last of them. A refusal leaves the log as it was.

    The same decision run again (the same thread and word, at the version it was
    recorded for) is found and returned with `found` set, and nothing is appended.
    """
    project, slug = folders.split(name)
    if not isinstance(thread, str) or not THREAD.fullmatch(thread):
        raise BadArgument(f"{thread!r} is not a thread's name",
                          "name the thread in letters, digits and . _ -")
    if decision not in DECISIONS:
        raise BadArgument(f"{decision!r} is not what is decided of a thread",
                          f"give {CLOSE} or {ACCEPT_RISK}")
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    again = f"moxi rounds mark {name} {PREFIX}{thread} --{decision} --record PTR"
    shown = f"moxi rounds show {name}"

    def has_a_pointer(view):
        if record is None:
            return Unmet("a decision on a thread says where the lead wrote why; PTR "
                         f"below is one token: {pointers.SHAPES}", again)
        return pointers.unmet(record, again, names="where the lead wrote why")

    def found(view) -> states.Decided | None:
        """This decision as the log already holds it: the same thread and word at
        the version that is current, which a run stopped after its append left."""
        current = versions.current(view.state)
        return next((one for one in view.state.threads
                     if current is not None and one.version == current.n
                     and (one.thread, one.decision) == (thread, decision)), None)

    def was_sent_back_twice(view):
        if found(view) is not None or sent_back(view.state):
            return None
        run = versions.sent_back(view.state)
        since = (" since the lead's last decision"
                 if versions.lead_decided(view.state) else "")
        have = ("no verdict of the design is recorded"
                if not versions.design_verdicts(view.state) else
                f"none has{since}" if not run else
                f"one has{since}, for v{run[0].design}")
        return Unmet(f"a thread is decided only after {SENT_BACK_IN_A_ROW} verdicts in "
                     "a row each sent the design back, counted from the lead's last "
                     "decision and only where the verdict gave the design its "
                     f"standing: {have}", shown)

    def was_frozen_since(view):
        if (found(view) is not None or not sent_back(view.state)
                or frozen_since(view.state)):
            return None
        return Unmet("the design's text was not changed and frozen since the last "
                     "verdict sent it back: write the section again from its rule "
                     "and freeze the design first", f"moxi rounds fold {name}")

    def decided(view, result):
        if found(view) is not None:
            return None
        return write.Entry("thread.decided", {"thread": thread, "decision": decision,
                                              "record": record})

    try:
        written = write.record(
            write.Where(home.project, home.folder, slug), actor=actor,
            gates=[ending.takes_new_work(name, "a decision on a thread"),
                   has_a_pointer, was_sent_back_twice, was_frozen_since],
            event=decided, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    after = states.fold(written.round_events)
    if not written.events:
        return Decided(name=name, decision=found(written.view),
                       standing=versions.standing(after), event=None, found=True,
                       failed=written.failed)
    return Decided(name=name, decision=after.threads[-1],
                   standing=versions.standing(after), event=written.events[0],
                   failed=written.failed)


__all__ = ["VERIFIES", "FULL", "RECHECK", "EXTRA_FULL_PASS", "SENT_BACK_IN_A_ROW", "CLOSE",
           "ACCEPT_RISK", "DECISIONS", "THREAD", "PREFIX", "Asked", "sent_back",
           "frozen_since", "had_full_pass", "flags", "asked", "files", "differences",
           "Decided", "decide"]
