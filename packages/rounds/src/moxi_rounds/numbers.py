"""A round's numbers: what happened in it, counted from its log when it ends.

`of(state)` gives them as data, computed from the round's log alone, and the event that
ends a round, `round.closed` or `round.dropped`, holds them. Every flow gets them, one
with a `retro` step or not.

- `design_passes`: verdicts on the design; `fold_first`: verdicts of any kind that sent
  the work back; `folds`: freezings of the design, and `records_only_folds` those that
  changed records only; `lead_decisions`: the lead's decisions on a thread.
- `step_time_s`: how long the round was at each step, in whole seconds, under the
  step's name for a step of the round and `<step> <stage>` (`build B1`) for a step of
  one stage. The log records acts, not steps, so the log is folded once, and after
  each event the step the round is at is read from the log alone, as the index reads
  it: the time to the next event goes to that step, and the last stretch runs to the
  event that ends the round. A step whose state git says is counted under its name all
  the same.
- `findings`: the findings of every build of the round by severity, as the newest
  report listing each left it; `rechecks` and `drift_rechecks`: the reports of those
  passes; `checks_failed`: check runs that did not exit 0, one ended at its time limit
  included.
- `questions`: the round's questions by the shape of the pointer they came from
  (`comms`, `session`, `url`, `file`, `build`, or `other`).
- `decisions`: the decisions ruled `accept` and ruled `reject`, as the rulings that
  stand leave them.
- `skips`: skips of a step or of a finish action; `step_ups`: the lead's moves to a
  later flow; `repairs`; `corrections`: rulings that replace or take back another;
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

from . import brake, builds, corrections, decisions, findings, model_tiers, pointers
from . import report_block, stepping, steps
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


def step_time_s(events: Iterable[dict], *, ended_at: str | None = None) -> dict[str, int]:
    """How long the round whose log holds `events` was at each step, in whole seconds,
    in the order it first came to each. The time from one event to the next goes to
    the step the round was at after the first of them, and the time from the last to
    `ended_at` (the time of the event that ends the round, as `log.stamp` writes one)
    to the step it was at then. A stretch whose ends don't both read as times, or that
    runs backwards, as two machines' clocks can make it, counts nothing."""
    spent: dict[str, int] = {}
    at_step: str | None = None
    since: int | None = None

    def add(until: int | None) -> None:
        if at_step is not None and since is not None and until is not None \
                and until > since:
            spent[at_step] = spent.get(at_step, 0) + until - since

    for folded in states.folding(events):
        now = _seconds(folded.history[-1]["at"])
        add(now)
        # Read from the log alone, as the index reads it: a step git would settle
        # waits on that read, so it is still the step the round is at.
        found = steps.current(folded, closed=_never)
        at_step = None if found is None else _key(found)
        since = now
    if ended_at is not None:
        add(_seconds(ended_at))
    return spent


def of(state: states.State, *, ended_at: str | None = None) -> dict:
    """The numbers of the round `state` is of, as data. `ended_at` is the time of the
    event that ends the round, which the last stretch of `step_time_s` runs to; left
    out, the time stops at the log's last event."""
    history = list(state.history)
    verdicts = [one for one in states.with_rereads(state)
                if one.get("type") == "verdict.recorded"]
    rulings = [one for one in history if one.get("type") == "ruling.recorded"]
    frozen = [one for one in history if one.get("type") == "design.frozen"]
    severity = Counter(one.severity for build in builds.builds(state)
                       for one in findings.of_build(state, build))
    origins = Counter(pointers.shape(one.origin) or OTHER for one in state.questions)
    ruled = Counter(one.chose for one in decisions.of_round(state)
                    if one.state == decisions.RULED)
    flagged = Counter(flag for one in verdicts for flag in one.get("flags") or ())
    return {
        "design_passes": sum(one.get("kind") == report_block.DESIGN for one in verdicts),
        "fold_first": sum(one.get("verdict") == report_block.FOLD_FIRST
                          for one in verdicts),
        "folds": len(frozen),
        "records_only_folds": sum(bool(one.get("records_only")) for one in frozen),
        "lead_decisions": sum(one.get("type") == "thread.decided" for one in history),
        "step_time_s": step_time_s(history, ended_at=ended_at),
        "findings": {name: severity[name] for name in report_block.SEVERITIES},
        "rechecks": sum(one.get("pass") == report_block.RECHECK for one in verdicts),
        "drift_rechecks": sum(one.get("pass") == report_block.DRIFT for one in verdicts),
        "checks_failed": sum(one.get("type") == "check.ran" and one.get("exit_code") != 0
                             for one in history),
        "questions": dict(sorted(origins.items())),
        "decisions": {name: ruled[name] for name in (decisions.ACCEPT, decisions.REJECT)},
        "skips": sum(_rules(one).startswith((corrections.SKIP, corrections.SKIP_ACTION))
                     for one in rulings),
        "step_ups": sum(one.get("type") == "flow.changed" and one.get("by") == stepping.LEAD
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
         "step_time_s", "findings", "rechecks", "drift_rechecks", "checks_failed",
         "questions", "decisions", "skips", "step_ups", "repairs", "corrections", "drops",
         "cleared_locks", "flags")


def lines(numbers: Mapping) -> list[str]:
    """The numbers as lines for people to read, each group on one. A key a later
    version counts is given as it is, so nothing held is left unsaid."""
    spent = numbers.get("step_time_s")
    times = (", ".join(f"{step} {_duration(seconds)}" for step, seconds in spent.items())
             if isinstance(spent, Mapping) and spent else "none")
    decided = numbers.get("decisions")
    decided = decided if isinstance(decided, Mapping) else {}
    found = [
        f"design: passes {_count(numbers, 'design_passes')}, fold-first "
        f"{_count(numbers, 'fold_first')}, folds {_count(numbers, 'folds')} "
        f"(records only {_count(numbers, 'records_only_folds')}), lead decisions "
        f"{_count(numbers, 'lead_decisions')}",
        f"time in steps: {times}",
        f"findings: {_counts(numbers.get('findings'))}; re-checks "
        f"{_count(numbers, 'rechecks')}, drift re-checks "
        f"{_count(numbers, 'drift_rechecks')}; check runs failed "
        f"{_count(numbers, 'checks_failed')}",
        f"questions by origin: {_counts(numbers.get('questions'))}",
        f"decisions: accepted {_count(decided, decisions.ACCEPT)}, rejected "
        f"{_count(decided, decisions.REJECT)}",
        f"skips {_count(numbers, 'skips')}, step-ups {_count(numbers, 'step_ups')}, "
        f"repairs {_count(numbers, 'repairs')}, corrections "
        f"{_count(numbers, 'corrections')}, dropped builds {_count(numbers, 'drops')}, "
        f"cleared locks {_count(numbers, 'cleared_locks')}",
        f"flags: {_counts(numbers.get('flags'))}"]
    found += [f"{key}: {value}" for key, value in numbers.items() if key not in _SAID]
    return found


def summed(each: Iterable[Mapping]) -> dict:
    """The numbers of several rounds added together key by key: a count to the same
    count, and a group (findings by severity, time by step) name by name. A value of
    another kind, which no version writes, is passed over."""
    total: dict = {}
    for numbers in each:
        _add(total, numbers)
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


__all__ = ["FLAGS", "OTHER", "step_time_s", "of", "lines", "summed"]
