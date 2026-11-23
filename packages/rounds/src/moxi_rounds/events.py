"""The events a log holds: each type's name and the fields it carries.

A log line is one JSON object. Five keys are on every line, the envelope: `v` (the format,
`VERSION`), `n` (the line's number, from 1), `at` (when, UTC to the second), `actor` (who
ran the verb) and `type`. The rest are the type's own fields, listed in `TYPES`.

`check(name, fields)` is what an append runs first: it refuses a type that is not in
`TYPES`, a field the type does not list, a field named like an envelope key, and a missing
required field. It looks at no value. A reader does not run it: a line that parses and
numbers in order is an event whatever its type, so a log written by a later version, or
on another machine, is still read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: The log format this code writes and reads.
VERSION = 1
#: The keys every line holds, in the order they are written.
ENVELOPE = ("v", "n", "at", "actor", "type")


@dataclass(frozen=True)
class EventType:
    """One type of event: its `name`, the `fields` it may carry and the ones among them it
    always carries, `required`."""

    name: str
    fields: tuple[str, ...]
    required: tuple[str, ...] = ()


def _types(*types: EventType) -> dict[str, EventType]:
    return {one.name: one for one in types}


#: Every event type, by name.
TYPES = _types(
    # A round begins: what it is for, the flow it follows with that flow's steps as they
    # stood then, the round it is a child of, the register entries it starts from, the
    # pointer to the operator's go, and the agent who leads it (`lead`).
    EventType("round.opened",
              ("description", "flow", "steps", "parent", "inherits", "record", "lead"),
              required=("description", "flow", "steps")),
    # The round's own lead was set or changed: the new name (`lead`) and the round's
    # own lead before, None when it had none (`was`). A lead is a record the leads
    # keep, not the operator's ruling, so it carries no pointer.
    EventType("lead.changed", ("lead", "was"), required=("lead",)),
    # The round moved to another flow: the name it had, the new name and its steps, and
    # who moved it. The operator's move is a ruling, and that ruling's own event
    # holds the same three.
    EventType("flow.changed", ("was", "flow", "steps", "by")),
    # A design version was frozen: its number and digest, whether only records changed
    # and the pointer that says so, the trace result, what the design's block declared
    # (`declared`), what of that was new to the record and is added by this event
    # (`added`), and what the block took away, which waits for a verdict or a ruling
    # (`pending`).
    EventType("design.frozen",
              ("version", "digest", "records_only", "record", "trace", "declared",
               "added", "pending"),
              required=("version", "digest")),
    # A report's verdict: what kind of report and pass, the verdict word, what it read
    # (a design version, a stage with its head and merge-base, or for a majors report
    # the `stages` it covers), the user step it is for, the model and author, the report file and its digest, and what its block
    # listed. A design report's verdict that finds its version publishable is also the
    # whole of what it applies: `applied` lists what that version's block took away
    # and this event takes out of the record (for a hold, `gone` are the stages taken
    # off it and `blocks` the hold that leaves), and `not_applied` the ones it leaves,
    # each with `why`. `flags` are what is noted of
    # the report and refuses nothing.
    EventType("verdict.recorded",
              ("kind", "pass", "verdict", "design", "stage", "stages", "head",
               "merge_base", "step", "model", "by", "report", "sha256", "findings",
               "waits", "decisions", "applied", "not_applied", "flags")),
    # A question was put: its id, sentence, origin and what it holds up, its state, and
    # who asked it and when, which outlive a move to another log. `record` is the
    # operator's pointer when the question is the operator's own; it is then the origin too.
    # `options` are the choices it offers, each key with its one line. A question
    # brought here from another log also says the record it was there (`moved_from`),
    # what it held up in the round it left (`held`), what a parked one waits for
    # (`until`), the keys that were `retired` from it and every answer `prepared` for
    # it, each with who recorded it there and when. A question that came up in
    # a build names that build's `builder`, whom the asker asked it for.
    EventType("question.asked",
              ("id", "text", "from", "blocks", "state", "asked_by", "asked_at", "record",
               "options", "prepared", "until", "retired", "moved_from", "held",
               "builder"),
              required=("id", "text", "from", "blocks", "state", "asked_by", "asked_at")),
    # A question changed by something that is no ruling: its state or what it holds
    # up, with who changed it and the operator's pointer when the change needs one;
    # `options` added, each key with its one line, and the keys `retired`; an answer
    # `prepared` for it, the keys of its block less `kind` and `question`; the record
    # it became in another log when it was moved there (`moved_to`). A ruling of a question writes none of these: its own event says
    # what the question becomes.
    EventType("question.changed",
              ("id", "state", "blocks", "until", "by", "record", "options",
               "retired", "prepared", "moved_to"),
              required=("id",)),
    # A stage joined the plan: its id, the repo and branch it builds on or the child
    # round it stands for, the stages it comes after, and its slices.
    EventType("stage.declared",
              ("id", "repo", "branch", "round", "after", "slices", "why")),
    # A stage changed: it waits for more stages (`after`, the ones added), which a
    # mark writes. A stage waits for fewer, or leaves the plan, by the operator's
    # ruling or by a verdict that applies it, and each of those is its own event:
    # this one says neither.
    EventType("stage.changed", ("id", "after"), required=("id",)),
    # A ruling: its id and sentence, the pointer to where the operator said it, a reason,
    # the repos and paths or the stages it applies to, its kind (of substance, or an act
    # on the process) and the item it rules, the key of the option an answer chose, and
    # the ruling it replaces or takes back. A ruling of a question is the whole of the
    # change: it names the `question` and holds what the question becomes, its `state`,
    # what a parked one waits for (`until`) and what it holds up from then on
    # (`blocks`), each only when the ruling changes it; a move of the question to
    # another log names the record it became there, `moved_to`. A skip of the questions
    # step for a stage lists the questions it lets that stage past, `covers`. A ruling
    # of the round's flow holds the name the flow had (`was`), the new name (`flow`)
    # and its `steps`, and is the whole of the move. The operator's credit of a
    # fast-forward names the build by its commission's line (`build`) and holds the
    # head it credits (`head`). A ruling of a decision the lead settled names the
    # settlement's line, `settlement`, which it replaces. A ruling
    # that replaces an earlier one names it in `supersedes`, and one that takes an
    # earlier one back in `revokes`; the earlier ruling's line is never changed.
    EventType("ruling.recorded",
              ("id", "text", "record", "reason", "scope", "stages", "kind", "rules",
               "chose", "question", "state", "until", "blocks", "covers", "supersedes",
               "revokes", "moved_to", "was", "flow", "steps", "build", "head",
               "settlement"),
              required=("id", "record", "kind")),
    # Rulings of this log were put in front of the operator: their ids, and the pointer
    # to where the operator was shown them.
    EventType("ruling.shown", ("ids", "record"), required=("ids", "record")),
    # A build began: its stage, repo, branch, the commit it was cut from, the branch it
    # is headed for, its worktree and scratch folder, its builder, the go pointer, the
    # machine that made its folders (`host`) and the id their markers carry (`build`).
    EventType("build.commissioned",
              ("stage", "repo", "branch", "base", "target", "worktree", "scratch",
               "builder", "record", "host", "build")),
    # A command ran for a build: the step it ran as, the commit, that commit's merge-base
    # with the target, the argv, and how it went: its exit code (None for a run ended at
    # its time limit), its duration and the file its output went to. A check by CI's
    # local part that pushed the tip to CI's own ref names the remote and that ref
    # (`pushed`). A queue member's run of `check.local` at its candidate, which the
    # lander writes, names the candidate commit and the batch.
    EventType("check.ran",
              ("stage", "step", "commit", "merge_base", "argv", "exit_code", "duration_s",
               "output", "pushed", "candidate", "batch")),
    # CI was read for a build: the commit, its merge-base with the target, the argvs
    # that ran, each workflow by path with the id it read as, its reading and its
    # run's url, and for one read by its jobs each job's reading and url (with the
    # entry's `matrix` patterns and `pick` job, and the packages that job picked), the
    # whole reading, and the remote and CI's own ref the tip was pushed to (`pushed`). A
    # queue member's read, which the lander writes, also names the candidate commit, the
    # batch, the run ids and the failures excused as the target's own.
    EventType("ci.read",
              ("stage", "commit", "merge_base", "status_argv", "ids_argv", "jobs_argvs",
               "workflows", "reading", "pushed", "candidate", "batch", "run", "excused")),
    # A finding or a waiting item moved to a new state.
    EventType("finding.marked", ("id", "state", "commits", "record")),
    EventType("wait.marked", ("id", "state", "commits", "record")),
    # The lead settled a minor decision that is not the operator's to rule: its key
    # (`id`), what was chosen of it (`accept` or `reject`), the pointer, and `by`, the
    # role, `lead`. It is never the operator's ruling.
    EventType("decision.settled", ("id", "chose", "record", "by"),
              required=("id", "chose", "record", "by")),
    # The lead made a decision major: its key (`id`), the pointer, and `by`, `lead`.
    EventType("decision.raised", ("id", "record", "by"), required=("id", "record", "by")),
    # A brief was sized: the stage, the one slice or verification it is for, the file,
    # its digest and the result.
    EventType("brief.sized", ("stage", "slice", "verify", "brief", "sha256", "result")),
    # One finish action reached a state at a build's tip; a merge also holds where it
    # landed and the target's head before it.
    EventType("finish.action",
              ("stage", "tip", "id", "kind", "state", "landing", "target_before")),
    # A build's finish completed at this tip.
    EventType("finish.done", ("stage", "tip")),
    # A build of a queue project joined its repo's queue at its tip `tip`, the target's
    # head being `main`; `urgent` puts it ahead of every member that is not.
    EventType("queue.join", ("stage", "tip", "main", "urgent", "by"),
              required=("stage", "tip")),
    # A member left the queue: by hand, because its branch moved, or a gate it failed.
    EventType("queue.left", ("stage", "tip", "reason", "by", "batch"),
              required=("stage", "reason")),
    # A member was taken into a batch: the batch's id, the target's head it was built on
    # and its candidate commit.
    EventType("queue.batch", ("stage", "tip", "batch", "main", "candidate"),
              required=("stage", "batch")),
    # A member landed in its batch, at its landing commit, with the failures its
    # candidate's run had that were the target's own (`excused`); `left_late` when it
    # left, or joined again, after the candidate reached the target.
    EventType("queue.landed", ("stage", "tip", "batch", "landing", "excused",
                               "left_late"),
              required=("stage", "batch")),
    # A member was sent back: a conflict with an earlier member (`blocker`), a commit
    # hook's or the publishing scan's refusal, or a new failure in its batch's CI run.
    EventType("queue.ejected", ("stage", "tip", "batch", "reason", "blocker", "run",
                                "failures", "output"), required=("stage", "reason")),
    # A member's batch ended without landing or ejecting it (`why`: paused, absent,
    # split, the target moved, rebuilt without another member), so it waits again in
    # its place.
    EventType("queue.requeued", ("stage", "tip", "batch", "why"),
              required=("stage", "batch")),
    # A dead holder's finish lock was cleared.
    EventType("lock.cleared", ("ref", "holder")),
    # A build or the whole round was withdrawn: by whom, the pointer, each open build's
    # branch and its tip (`tips`, each `{"stage", "repo", "branch", "tip"}`, the tip
    # None for a branch that is not there), and, for a round, where the questions
    # went.
    EventType("build.dropped", ("stage", "by", "record", "tips", "questions")),
    EventType("round.dropped", ("by", "record", "tips", "questions", "numbers")),
    # A thread of design verification was closed or its risk accepted.
    EventType("thread.decided", ("thread", "decision", "record")),
    # A record was derived again: what (`base`, the base of the open build of
    # `stage`; `report`, the record of the verdict on line `verdict`; or `digest`, the
    # digest of the design's version `version`, re-stamped from its frozen copy), each
    # field's old and new value, and what was read to derive it: git's answer, the
    # report's file and digest, or the frozen copy's path. A re-stamp also holds
    # `record`, the pointer to where the lead named what edited the design.
    EventType("repair", ("what", "stage", "verdict", "version", "fields", "read",
                         "record"),
              required=("what", "fields")),
    # The round ended, with its numbers.
    EventType("round.closed", ("numbers",)),
)


def check(name: str, fields: Mapping[str, object]) -> None:
    """Refuse, with `ValueError`, an event no log should hold: a `name` that is no type, a
    field the type does not list or one named like an envelope key, or a required field
    left out."""
    kind = TYPES.get(name)
    if kind is None:
        raise ValueError(f"{name!r} is not an event type")
    shadowing = sorted(key for key in fields if key in ENVELOPE)
    if shadowing:
        raise ValueError(f"{name}: {', '.join(shadowing)} is set by the log, not by "
                         "the event")
    unknown = sorted(key for key in fields if key not in kind.fields)
    if unknown:
        raise ValueError(f"{name} holds no field named {', '.join(unknown)}")
    missing = [key for key in kind.required if key not in fields]
    if missing:
        raise ValueError(f"{name} needs {', '.join(missing)}")


__all__ = ["VERSION", "ENVELOPE", "EventType", "TYPES", "check"]
