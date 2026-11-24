"""A round's numbers: what happened in it, counted from its log when it ends.

`of(state)` gives them as data, computed from the round's log alone, and the event that
ends a round, `round.closed` or `round.dropped`, holds them. Every flow gets them, one
with a `retro` step or not.

- `design_passes`: verdicts on the design; `fold_first`: verdicts of any kind that sent
  the work back; `folds`: freezings of the design, and `records_only_folds` those that
  changed records only; `lead_decisions`: the lead's decisions on a thread, and the
  freezings of records only that made a design sent back stand at `lead-decision`.
- `step_time_s`: how long each step was due, in whole seconds, under the step's name
  for a step of the round and `<step> <stage>` (`build B1`) for a step of one stage.
  The log records acts, not steps, so the log is folded once, and after each event
  every step is read: the time to the next event goes to each step that is `ready` or
  `waiting` then, so steps due side by side (a stage's build while another stage
  waits on a question, a build's check and its verification) each get it. The last
  stretch runs to the event that ends the round. A step whose state git says is read
  from what the log holds of the build instead (`logged`), so it settles when the log
  shows it did, with no read of git. The times overlap, and are shown so labelled.
- `round_time_s`: the round's own time, from the log's first event to the event that
  ends it (to now in the retrospective's numbers of an open round).
- `longest_gap`: the longest stretch with no event in the log, the stretch to the end
  of the round included, as `{"since", "until", "due"}`: when it began and ended, and
  the steps due across it, read as `step_time_s` reads them; the earliest of equal
  ones. It shows a build that sat idle, which the finish step's time, counted from
  the commission, hides. `with_clocks` computes these two for a round that ended
  before its ending held them, from its log up to the ending event.
- `findings`: the findings of every build of the round by severity, as the newest
  report listing each left it; `rechecks` and `drift_rechecks`: the reports of those
  passes; `checks_failed`: check runs that did not exit 0, one ended at its time limit
  included.
- `questions`: the round's questions by the shape of the pointer they came from
  (`comms`, `session`, `url`, `file`, `build`, or `other`).
- `decisions`: the decisions the operator ruled `accept` and ruled `reject`, as the
  rulings that stand leave them; `settled`: the minor ones the lead settled `accept`
  and `reject`, as the settlements that stand leave them. Numbers written before
  `settled` was counted read it as zero.
- `skips`: skips of a step or of a finish action; `step_ups`: the lead's moves to a
  later flow; `checklist_step_ups`: the moves a round's opening made for its checklist
  marks, a `flow.changed` that holds `marked`, which `step_ups` leaves out (numbers
  written before it was counted read it as zero); `repairs`; `corrections`: rulings that replace or take back another;
  `drops`: builds dropped; `cleared_locks`: finish locks of a dead holder cleared;
  `flags`: the reports flagged `extra-full-pass` and `model-below-tier`.

Every reader of a verdict's lists reads it with what its rereads added. Nothing here
reads a file, the config or git.
"""

from __future__ import annotations

import calendar
import time
from collections import Counter
from collections.abc import Iterable, Mapping

from . import (brake, builds, checking, corrections, decisions, findings, model_tiers,
               pointers, report_block, stepping, steps, versions)
from . import state as states

#: The flags a report may be recorded with that the numbers count.
FLAGS = (brake.EXTRA_FULL_PASS, model_tiers.BELOW_TIER)
#: What a question's origin is counted under when it has no pointer shape.
OTHER = "other"


def _rules(event: Mapping) -> str:
    return str(event.get("rules") or "")


def _seconds(at: object) -> int | None:
    """An event's `at` as seconds since the epoch; None for one that doesn't read."""
    try:
        return calendar.timegm(time.strptime(str(at), "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def _never(_name: str) -> bool:
    return False


def _key(step: steps.Step) -> str:
    return step.step if step.stage is None else f"{step.step} {step.stage}"


#: The kinds of event that name a commit of a build, and the field that names it.
_NAMES_A_COMMIT = {"check.ran": "commit", "ci.read": "commit", "verdict.recorded": "head",
                   "finish.action": "tip", "finish.done": "tip"}


def _named(state: states.State, build: builds.Build) -> list[tuple[int, str]]:
    """Each commit the log names for `build` after its commission, with the line that
    names it, oldest first: the commit of a check run or a read of CI, the head a
    report read, the tip of a finish record, and each commit of a lead's mark."""
    prefix = f"{build.stage}."
    found = []
    for event in state.history:
        if event["n"] <= build.n:
            continue
        kind = event.get("type")
        if kind == findings.MARKED and str(event.get("id") or "").startswith(prefix):
            found += [(event["n"], str(one)) for one in event.get("commits") or ()]
            continue
        field = _NAMES_A_COMMIT.get(kind)
        if field is not None and event.get("stage") == build.stage and event.get(field):
            found.append((event["n"], str(event[field])))
    return found


def _since(state: states.State, build: builds.Build, named: list[tuple[int, str]],
           step: str) -> tuple[str, ...] | None:
    """The commits the log names for `build` after its newest verdict of `step`, other
    than the head that verdict read; None with no such verdict."""
    listed = [one for one in findings.verdicts_of(state, build)
              if findings.step_of(one) == step]
    if not listed:
        return None
    newest = listed[-1]
    return tuple(dict.fromkeys(commit for n, commit in named
                               if n > (newest.n or 0) and commit != newest.head))


def logged(state: states.State) -> dict[str, steps.Seen]:
    """What the log alone holds of each open build of the round, by stage, as the step
    function reads it (`steps.Seen`), for counting time with no read of git. The
    build's tip is the commit the newest record of it names; it has work once a record
    names one; its check is the newest run's command, so the check is done when that
    run passed at the tip, and a command step of the user's own is done when its newest
    run passed at the tip, the run's own argv standing for the filled one (`commands`);
    the commits since a verdict are the ones the log names after
    it, so a lead's mark of them, or a later publishable report, settles the
    verification. The worktree is taken as clean, which only says ready for waiting."""
    found = {}
    for build in builds.builds(state):
        if build.ended is not None or builds.current(state, build.stage) != build:
            continue
        named = _named(state, build)
        tip = named[-1][1] if named else None
        run = checking.newest(state, build.stage, checking.CHECK)
        found[build.stage] = steps.Seen(
            state=builds.BUILDING if tip else builds.EMPTY, tip=tip, clean=True,
            check=None if run is None else tuple(run.get("argv") or ()), head=tip,
            commands={step: tuple(ran.get("argv") or ())
                      for step in checking.command_steps(state)
                      if (ran := checking.newest(state, build.stage, step)) is not None},
            since=_since(state, build, named, findings.VERIFY_BUILD),
            reported={step: (_since(state, build, named, step), None)
                      for step in checking.report_steps(state)})
    return found


#: The states of a step that is due: the time it is waited on counts towards it.
_DUE = (steps.READY, steps.WAITING)


def step_time_s(events: Iterable[dict], *, ended_at: str | None = None,
                ratify: str | None = None) -> dict[str, int]:
    """How long each step of the round whose log holds `events` was due, in whole
    seconds, in the order each first came due. The time from one event to the next
    goes to every step that was `ready` or `waiting` after the first of them, and the
    time from the last to `ended_at` (the time of the event that ends the round, as
    `log.stamp` writes one) to every step due then. Steps are read from the log alone,
    an open build as `logged` gives it, and `ratify`, the project's `ratify` setting,
    as the ratify step reads it: with it `off` the step is never asked and gets no
    time. Left out, the setting reads as not read, and the step is due while a major
    decision is unruled. A stretch whose ends don't both read as times,
    or that runs backwards, as two machines' clocks can make it, counts nothing."""
    return _timed(events, ended_at=ended_at, ratify=ratify).spent


class _Timed:
    """What one walk of a log's events gives: the time each step was `spent` due, the
    round's `round_time_s`, and its `longest_gap`."""

    def __init__(self) -> None:
        self.spent: dict[str, int] = {}
        self.round_time_s: int | None = None
        self.longest_gap: dict | None = None


def _timed(events: Iterable[dict], *, ended_at: str | None = None,
           until: str | None = None, ratify: str | None = None) -> _Timed:
    """The walk `step_time_s` makes of `events`, `ended_at` and `ratify` as it takes
    them, which also gives the round's time, from the first event to `until` (else
    `ended_at`, else the last event), and the longest stretch with no event, the one
    from the last event to that time included."""
    timed = _Timed()
    spent = timed.spent
    due: list[str] = []
    since: int | None = None
    since_at: str | None = None
    first: int | None = None
    longest = 0

    def gap(to: int | None, to_at: str) -> None:
        nonlocal longest
        if since is None or to is None or to - since <= longest:
            return
        longest = to - since
        timed.longest_gap = {"since": since_at, "until": to_at, "due": list(due)}

    def add(to: int | None) -> None:
        if since is None or to is None or to <= since:
            return
        for key in due:
            spent[key] = spent.get(key, 0) + to - since

    settings_read = {} if ratify is None else {None: steps.Seen(ratify=ratify)}
    for folded in states.folding(events):
        at = folded.history[-1]["at"]
        now = _seconds(at)
        add(now)
        gap(now, at)
        first = now if first is None and len(folded.history) == 1 else first
        due = [] if folded.status != states.OPEN else [
            _key(one) for one in steps.of(folded, on_closed=_never,
                                          seen={**logged(folded), **settings_read})
            if one.state in _DUE]
        since, since_at = now, at
    if ended_at is not None:
        add(_seconds(ended_at))
    end_at = until or ended_at
    end = since if end_at is None else _seconds(end_at)
    if end_at is not None:
        gap(end, end_at)
    if first is not None and end is not None and end >= first:
        timed.round_time_s = end - first
    return timed


def _ending_at(history: list[dict]) -> int | None:
    """Where the latest event that ended the round is in `history`; None for none."""
    found = None
    for place, event in enumerate(history):
        if event.get("type") in _ENDINGS:
            found = place
    return found


#: The events that end a round.
_ENDINGS = ("round.closed", "round.dropped")


def with_clocks(state: states.State, numbers: Mapping, *,
                ratify: str | None = None) -> dict:
    """The numbers `numbers` the ending of the round `state` holds, with
    `round_time_s` and `longest_gap` computed from its log where they lack them, as
    the ending of a round that ended before they were kept does: up to the event that
    ended it, so nothing logged after (a re-stamp) changes either, with the steps due
    read under `ratify`, the project's setting now. Writes nothing."""
    found = dict(numbers)
    if "round_time_s" in found and "longest_gap" in found:
        return found
    history = list(state.history)
    place = _ending_at(history)
    if place is None:
        return found
    timed = _timed(history[:place], ended_at=history[place].get("at"), ratify=ratify)
    found.setdefault("round_time_s", timed.round_time_s)
    found.setdefault("longest_gap", timed.longest_gap)
    return found


def lead_decisions(events: Iterable[dict]) -> int:
    """The lead's decisions in the log of `events`: each on a thread, and each freezing
    of records only that made a design sent back stand at `lead-decision`."""
    count = 0
    for folded in states.folding(events):
        event = folded.history[-1]
        if event.get("type") == "thread.decided":
            count += 1
        elif event.get("type") == "design.frozen" and event.get("records_only"):
            stood = versions.standing(folded)
            count += stood.word == versions.LEAD_DECISION and stood.fold == stood.version
    return count


def of(state: states.State, *, ended_at: str | None = None,
       ratify: str | None = None, now: str | None = None) -> dict:
    """The numbers of the round `state` is of, as data. `ended_at` is the time of the
    event that ends the round, which the last stretch of `step_time_s`, `round_time_s`
    and `longest_gap` run to; left out, the step times stop at the log's last event,
    and the other two run to `now` (a time as `log.stamp` writes one), or stop there
    too without it. `ratify` is the project's setting, as `step_time_s` takes it."""
    history = list(state.history)
    timed = _timed(history, ended_at=ended_at, until=ended_at or now, ratify=ratify)
    verdicts = [one for one in states.with_rereads(state)
                if one.get("type") == "verdict.recorded"]
    rulings = [one for one in history if one.get("type") == "ruling.recorded"]
    frozen = [one for one in history if one.get("type") == "design.frozen"]
    severity = Counter(one.severity for build in builds.builds(state)
                       for one in findings.of_build(state, build))
    origins = Counter(pointers.shape(one.origin) or OTHER for one in state.questions)
    ruled = Counter(one.chose for one in decisions.of_round(state)
                    if one.state == decisions.RULED)
    settled = Counter(one.chose for one in decisions.of_round(state)
                      if one.state == decisions.SETTLED)
    flagged = Counter(flag for one in verdicts for flag in one.get("flags") or ())
    return {
        "design_passes": sum(one.get("kind") == report_block.DESIGN for one in verdicts),
        "fold_first": sum(one.get("verdict") == report_block.FOLD_FIRST
                          for one in verdicts),
        "folds": len(frozen),
        "records_only_folds": sum(bool(one.get("records_only")) for one in frozen),
        "lead_decisions": lead_decisions(history),
        "step_time_s": timed.spent,
        "round_time_s": timed.round_time_s,
        "longest_gap": timed.longest_gap,
        "findings": {name: severity[name] for name in report_block.SEVERITIES},
        "rechecks": sum(one.get("pass") == report_block.RECHECK for one in verdicts),
        "drift_rechecks": sum(one.get("pass") == report_block.DRIFT for one in verdicts),
        "checks_failed": sum(one.get("type") == "check.ran" and one.get("exit_code") != 0
                             for one in history),
        "questions": dict(sorted(origins.items())),
        "decisions": {name: ruled[name] for name in (decisions.ACCEPT, decisions.REJECT)},
        "settled": {name: settled[name]
                    for name in (decisions.ACCEPT, decisions.REJECT)},
        "skips": sum(_rules(one).startswith((corrections.SKIP, corrections.SKIP_ACTION))
                     for one in rulings),
        "step_ups": sum(one.get("type") == "flow.changed" and one.get("by") == stepping.LEAD
                        and "marked" not in one for one in history),
        "checklist_step_ups": sum(one.get("type") == "flow.changed" and "marked" in one
                                  for one in history),
        "repairs": sum(one.get("type") == "repair" for one in history),
        "corrections": sum(bool(one.get("supersedes"))
                           or _rules(one).startswith(corrections.REVOKE)
                           for one in rulings),
        "drops": sum(one.get("type") == "build.dropped" for one in history),
        "cleared_locks": sum(one.get("type") == "lock.cleared" for one in history),
        "flags": {name: flagged[name] for name in FLAGS},
    }


# -- the numbers as people read them, and summed -----------------------------------------

def _count(numbers: Mapping, key: str) -> object:
    value = numbers.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _counts(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "none"
    return ", ".join(f"{name} {count}" for name, count in value.items())


def _duration(seconds: object) -> str:
    """Whole seconds as `2h 5m 3s`, leaving out what is nothing."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        return "0s"
    hours, rest = divmod(seconds, 3600)
    minutes, rest = divmod(rest, 60)
    return " ".join(f"{amount}{unit}" for amount, unit in
                    ((hours, "h"), (minutes, "m"), (rest, "s")) if amount)


#: The keys `lines` gives a line of their own to; any other is given as it is.
_SAID = ("design_passes", "fold_first", "folds", "records_only_folds", "lead_decisions",
         "step_time_s", "round_time_s", "longest_gap", "findings", "rechecks", "drift_rechecks", "checks_failed",
         "questions", "decisions", "settled", "skips", "step_ups", "checklist_step_ups",
         "repairs", "corrections", "drops",
         "cleared_locks", "flags")


def lines(numbers: Mapping) -> list[str]:
    """The numbers as lines for people to read, each group on one. A key a later
    version counts is given as it is, so nothing held is left unsaid."""
    spent = numbers.get("step_time_s")
    times = (", ".join(f"{step} {_duration(seconds)}" for step, seconds in spent.items())
             if isinstance(spent, Mapping) and spent else "none")
    decided = numbers.get("decisions")
    decided = decided if isinstance(decided, Mapping) else {}
    settled = numbers.get("settled")
    settled = settled if isinstance(settled, Mapping) else {}
    found = [
        f"design: passes {_count(numbers, 'design_passes')}, folds "
        f"{_count(numbers, 'folds')} (records only "
        f"{_count(numbers, 'records_only_folds')}), lead decisions "
        f"{_count(numbers, 'lead_decisions')}",
        f"reports sent back (fold-first, of any kind): {_count(numbers, 'fold_first')}",
        f"time in steps, overlapping (steps due side by side each count the same "
        f"time): {times}",
        *_clock_lines(numbers),
        f"findings: {_counts(numbers.get('findings'))}; re-checks "
        f"{_count(numbers, 'rechecks')}, drift re-checks "
        f"{_count(numbers, 'drift_rechecks')}; check runs failed "
        f"{_count(numbers, 'checks_failed')}",
        f"questions by origin: {_counts(numbers.get('questions'))}",
        f"decisions: accepted {_count(decided, decisions.ACCEPT)}, rejected "
        f"{_count(decided, decisions.REJECT)}; settled by the lead: accepted "
        f"{_count(settled, decisions.ACCEPT)}, rejected "
        f"{_count(settled, decisions.REJECT)}",
        f"skips {_count(numbers, 'skips')}, step-ups {_count(numbers, 'step_ups')}, "
        f"checklist step-ups {_count(numbers, 'checklist_step_ups')}, "
        f"repairs {_count(numbers, 'repairs')}, corrections "
        f"{_count(numbers, 'corrections')}, dropped builds {_count(numbers, 'drops')}, "
        f"cleared locks {_count(numbers, 'cleared_locks')}",
        f"flags: {_counts(numbers.get('flags'))}"]
    found += [f"{key}: {value}" for key, value in numbers.items() if key not in _SAID]
    return found


def _clock_lines(numbers: Mapping) -> list[str]:
    """The round's own time and its longest stretch with nothing logged, each in a line,
    for numbers that hold them."""
    found = []
    if "round_time_s" in numbers:
        found.append(f"time open: {_duration(numbers['round_time_s'])}, open to close "
                     "(to now while the round is open)")
    gap = numbers.get("longest_gap")
    if isinstance(gap, Mapping):
        length = None
        since, until = _seconds(gap.get("since")), _seconds(gap.get("until"))
        if since is not None and until is not None:
            length = until - since
        due = [str(one) for one in gap.get("due") or ()]
        while_ = ("while no step was due" if not due else
                  f"while {due[0]} was due" if len(due) == 1 else
                  f"while {', '.join(due)} were due")
        found.append(f"longest stretch with nothing logged: {_duration(length)}, since "
                     f"{gap.get('since')}, {while_}")
    return found


#: The keys `summed` leaves out: a stretch of one round does not add across rounds.
_NOT_SUMMED = ("longest_gap",)


def summed(each: Iterable[Mapping]) -> dict:
    """The numbers of several rounds added together key by key: a count to the same
    count, and a group (findings by severity, time by step) name by name. A value of
    another kind, which no version writes, is passed over, and so is `longest_gap`."""
    total: dict = {}
    for numbers in each:
        _add(total, {key: value for key, value in numbers.items()
                     if key not in _NOT_SUMMED})
    return total


def _add(total: dict, numbers: Mapping) -> None:
    for key, value in numbers.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            had = total.get(key, 0)
            total[key] = (had if isinstance(had, int) else 0) + value
        elif isinstance(value, Mapping):
            had = total.get(key)
            if not isinstance(had, dict):
                had = total[key] = {}
            _add(had, value)


__all__ = ["FLAGS", "OTHER", "logged", "step_time_s", "with_clocks", "lead_decisions",
           "of", "lines", "summed"]
