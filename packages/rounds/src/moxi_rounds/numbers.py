"""A round's numbers: what happened in it, counted from its log when it ends.

`of(state)` gives them as data, computed from the round's log alone, and the event that
ends a round, `round.closed` or `round.dropped`, holds them. Every flow gets them, one
with a `retro` step or not.

- `design_passes`: verdicts on the design; `fold_first`: verdicts of any kind that sent
  the work back; `folds`: freezings of the design, and `records_only_folds` those that
  changed records only; `lead_decisions`: the lead's decisions on a thread.
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

from collections import Counter
from collections.abc import Mapping

from . import brake, builds, corrections, decisions, findings, model_tiers, pointers
from . import report_block, stepping
from . import state as states

#: The flags a report may be recorded with that the numbers count.
FLAGS = (brake.EXTRA_FULL_PASS, model_tiers.BELOW_TIER)
#: What a question's origin is counted under when it has no pointer shape.
OTHER = "other"


def _rules(event: Mapping) -> str:
    return str(event.get("rules") or "")


def of(state: states.State) -> dict:
    """The numbers of the round `state` is of, as data."""
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


__all__ = ["FLAGS", "OTHER", "of"]
