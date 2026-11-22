"""`finish`: a build's last step, which puts it where it goes.

`finish(name, stage)` runs the finish of the stage's open build at `T`, the branch's
tip as read (its head, once the branch is gone), in this order:

1. Under the round's lock, read the round and evaluate every gate of `finish_gates` at
   `T`, and the scratch gate (`sweeping.scratch_gate`): no process uses the build's
   scratch folder. Refuse with every unmet one. A build already finished or dropped is
   refused before any gate: nothing is left to do, and no second `finish.done` is
   written. Where the processes can't be read, the scratch gate is not run, and
   `Finished` says so.
2. Take the repo's finish lock (`finish_lock`), recording `lock.cleared` when the take
   swapped out a dead holder.
3. Read the round, the target's head `M` and the branch's tip again. Refuse when the
   tip is no longer `T`, or when a gate another finish may have changed by moving the
   target (`UNDER_LOCK`: drift, the target, work, sharing) no longer holds.
4. Run the repo's finish actions in order, each under its `id`, and record each one's
   result as `finish.action` before the next starts; each record takes the round's
   lock for that write only. An action whose record at `T` is `ok` or `found` is not
   run again; one recorded `failed`, or with no record, is run. An action the operator
   skipped for the stage is not run and counts as done, with no record of its own. A
   failed action is recorded `failed` and ends the verb (`ActionFailed`, exit 1);
   nothing is undone.
5. Under the round's lock, read the log again and append `finish.done` when the log
   holds none for the build and the gates read on the log (`AFTER`: verification,
   findings, the check, questions, steps of the user's own) still hold at `T`; else
   refuse with what is owed. The build is read at `T` here, not at its branch's tip
   now: `T` is what the merge took, and a commit made on the branch while the actions
   ran is not part of this finish.
6. Clean up (`_clean_up`), with the finish lock released. After a merge action that is
   `ok` or `found`, and only while the branch's tip is still `T`: remove the build's
   worktree, then its scratch folder, then delete the branch with `git branch -d`,
   unless the repo's `keep_branch` is set or its worktree was kept. With no such merge
   the work is not on the target: the worktree and the branch stay, and only the scratch
   folder is removed. Each removal obeys the removal rule (`removal`), and a scratch
   folder a process came to use after step 1 is not moved
   (`sweeping.remove_build_dirs`). A folder kept, or a branch git won't delete, is named
   with why in `Finished`, and the finish stands: a later sweep takes the folder once
   the rule holds and no process uses it.
7. Remove CI's own refs from the remote, those the build's check recorded pushing and
   those its repo's `ci` templates (`ref`, `full_ref`) give its branch, under the keep
   rule (`ci_refs.remove`). A ref it could not remove is named with why in `Finished`,
   and the finish stands.

The finish lock is released however the verb ends once it was taken: only while it
still points at this finish's own blob. A refusal or a failed action after the verb
recorded anything (a dead holder's lock cleared, an action's result) names each thing
done and recorded, and, when a merge is on the target, that the build reads merged and
is not finished: those records stand, and a rerun goes on from them.

**The merge.** `git merge --no-ff T` in the checkout of the target: the worktree that
has the target checked out, which must hold nothing uncommitted with its `HEAD` at `M`;
or, when no worktree has it, `<worktree_dir>/<repo>/.finish-<into>`, added for the merge
and removed after it. A checkout at that place that a stopped finish left is finish's own
too: the merge is made there and it is removed after, and a merge found made is followed
by its removal. `T` is given as the commit id the gates read, so a commit made on
the branch after them is not merged; the message names the branch. Its record holds the
landing, the merge commit, and `P`, the target before it, which is `M`. A conflict is
aborted and refused with the way through: merge the target into the branch in the
build's worktree, check, and finish again. A merge that fails otherwise (a hook refused
it, say) is aborted too, and recorded as failed. A checkout added for the merge is
removed after it under the removal rule's no-lost-work half; one that half keeps is left
and named in the action's `kept`.

**A merge found in git.** Before a merge is made, the build is read as step 3 read it:
when it is already `merged` (by a hand merge, or by a fast-forward of the target onto
its branch), the merge is not made again but recorded `found`, with the landing and
`P` that read gives: the bringing merge and its first parent for a hand merge, the tip
and the target before it for a fast-forward. A hand merge's record carries what it put
on the target, `git diff --shortstat` of its first parent and itself, for the caller to
print. That covers a merge made by hand, and one a stopped finish made and did not
record. A build merged by a landing of its own on record at another tip is refused:
recording that landing again would give the build two landings no head tells apart.

**A push** is `git push <remote>` of the target, at the landing a merge action before
it recorded, or with no merge action before it, of the build's branch at `T`; both by
commit id, so nothing the gates did not read is pushed. It runs in the repo's root. A
push of the target that git refuses passes when the remote's target already holds the
landing (another finish, or the user, pushed it and more): pushing a ref that is already
there changes nothing, so a rerun after a failed push does not fail only because the
target moved on. Nothing is forced, so the remote's target never moves back.

**A command** runs its argv, placeholders filled as for a check, with no shell, in the
build's worktree, its output going to the caller's terminal, for at most its
`timeout_s` (no limit without one). `started` is recorded before it runs, so a finish
that dies while it runs leaves a record saying so. A command whose newest record at `T`
is `started` with no result may have done its work: it is run again when its `repeat`
is set, or once when the caller passes its id as `again`, and either way the run says
why (`Action.again`); otherwise `finish` refuses, names when it started and the two ways
on, and runs nothing. `finish_actions` has the rules, which the finish step's row reads
too.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from moxi import exit as exits
from moxi import proc

from . import (builds, checking, ci_refs, finish_actions, finish_gates, finish_lock,
               folders, gitreads, open_builds, places, questions, removal, sweeping, write)
from . import settings as own
from . import state as states
from .errors import RoundsError, Refused, Unmet
from .gitreads import Git, GitError, git_env

#: The event of one finish action's result, and of a whole finish done.
ACTION = finish_actions.ACTION
DONE = "finish.done"
#: The states of a finish action's record: run and passed, run and failed, or a merge
#: found already on the target.
OK, FAILED, FOUND = finish_actions.OK, finish_actions.FAILED, finish_actions.FOUND
#: The state of a command's record appended before it runs.
STARTED = finish_actions.STARTED
#: The states of a record that mean the action is not run again.
SETTLED = finish_actions.SETTLED
#: Why a command that started and has no result is run again.
REPEAT, AGAIN = finish_actions.REPEAT, finish_actions.AGAIN
#: The state an action the operator skipped is given here; it is never recorded.
SKIPPED = "skipped"
#: The gates read again under the finish lock before any action is run: those another
#: finish may have changed by moving the target.
UNDER_LOCK = (finish_gates.DRIFT, finish_gates.TARGET, finish_gates.WORK,
              finish_gates.SHARING)
#: The gates read again on the log after the actions, before `finish.done`.
AFTER = (finish_gates.VERIFIED, finish_gates.FINDINGS, finish_gates.CHECKED,
         finish_gates.QUESTIONS, finish_gates.USER_STEPS)
#: The folder name, in `<worktree_dir>/<repo>`, of the checkout of a target that no
#: worktree has checked out, before the target's encoded name.
CHECKOUT_PREFIX = ".finish-"
#: How long the merge may take, hooks included, in seconds.
MERGE_TIMEOUT_S = 3600.0
#: How long a push may take, in seconds.
PUSH_TIMEOUT_S = 600.0


class ActionFailed(RoundsError):
    """A finish action failed: `id` is the action, `output` what it printed, and `done`
    what this finish did and recorded before it, a line each. Its `failed`
    record is on the log, nothing is undone, and the finish lock is released."""

    exit_code = exits.ERROR

    def __init__(self, name: str, stage: str, action_id: str, output: str, *,
                 done: tuple[str, ...] = ()):
        self.id = action_id
        self.output = output
        self.done = tuple(done)
        before = "" if not self.done else "; done before it, recorded: " + "; ".join(
            self.done)
        super().__init__(f"the finish action {action_id} of {stage} failed; it is "
                         f"recorded as failed, and nothing is undone{before}",
                         f"fix what it printed, then moxi rounds finish {name} {stage}")


@dataclass(frozen=True)
class Action:
    """One finish action as this finish recorded or passed it: its `id` and `kind`, the
    `state` recorded (`OK`, `FAILED`, `FOUND`, `STARTED`), or `ran` false when a record
    at the tip already settled it or the operator skipped it (`skipped_by`, his ruling;
    `state` is then `SKIPPED`); a merge's `landing` and `target_before`; what it
    printed, or for a merge found made by hand (`hand_merge`), what that merge put on
    the target. A command's `started` record that runs it again after an earlier start
    with no result says why in `again` (`REPEAT`, `AGAIN`) and when that one started in
    `started_at`."""

    id: str
    kind: str
    state: str
    ran: bool = True
    landing: str | None = None
    target_before: str | None = None
    output: str = ""
    kept: str | None = None
    hand_merge: bool = False
    again: str | None = None
    started_at: str | None = None
    skipped_by: str | None = None


@dataclass(frozen=True)
class Finished:
    """What `finish` did: the round, the stage, the tip `T` it finished, each action,
    the dead holder's line when its lock was cleared, the `finish.done` event (None when
    the log already held one), the after-append callbacks that failed, and what the
    cleanup did: the folders it `removed`, those it `kept` with why, and the branch:
    `deleted`, or why it stayed (None when the repo keeps its branches or no merge put
    the work on the target); what became of each of CI's own `refs` the build
    pushed; and `unscanned`, a line saying the scratch gate was not run, where the
    processes could not be read."""

    name: str
    stage: str
    tip: str
    actions: tuple[Action, ...]
    cleared: str | None = None
    event: dict | None = None
    released: bool = True
    failed: tuple = field(default=())
    removed: tuple[Path, ...] = ()
    kept: tuple[removal.Kept, ...] = ()
    branch: str | None = None
    refs: tuple = ()
    unscanned: tuple[str, ...] = ()


def finish(name: str, stage: str, *, by: str, settings=None,
           wait_s: float = write.WAIT_S, at: str | None = None, env=None,
           on_action: Callable[[Action], None] | None = None,
           again: str | None = None) -> Finished:
    """Finish the open build of `stage` in the round `name`. `settings` is what
    `settings.load()` gives, read now when left out. `on_action` is called with each
    action this finish records, a command's `started` included, as soon as its record
    is on the log, so a caller can say what was done even when the finish is refused
    later. `again` is the id of a command that started at the tip and has no result,
    to be run once more; any other id is refused.

    Raises what `folders.home` raises for a round that can't be found; `Refused` with
    every unmet gate, a held finish lock, a branch that moved, a checkout of the target
    that can't take the merge, a conflict, or gates the log no longer meets after the
    actions, its `done` naming what this finish recorded before it (empty when it
    recorded nothing); `ActionFailed` when an action failed, its `done` the same; and
    `GitError` when git can't be read."""
    project, slug = folders.split(name)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    where = write.Where(home.project, home.rounds_dir, slug)
    rerun = f"moxi rounds finish {name} {stage}"

    view = write.look(where, wait_s=wait_s, env=env)
    build = _open_build(view.state, name, stage)
    seen = _seen(view, name, stage, settings)
    tip = seen.tip or seen.head
    # Read again at `T` after the actions only where `T` was the branch's tip: a build
    # whose branch was gone is read as gone.
    at_tip = None if seen.tip is None else {stage: tip}
    found = [one.unmet for one in _gates(view, name, stage, seen, tip, settings)]
    actions = own.build_setting(settings, build.repo, "finish")
    if again is not None:
        found += finish_actions.again_refused(view.state, build, tip, actions, again,
                                              name=name, repo=build.repo)
    found += finish_actions.blocked(view.state, build, tip, actions, name=name,
                                    worktree_gone=seen.tree is None, again=again)
    in_use, unscanned = sweeping.scratch_gate(view.state, [build], again=rerun)
    if in_use is not None:
        found.append(in_use)
    if found:
        raise Refused(found)

    _key, root = folders.root(build.repo)
    taken = finish_lock.take(root, finish_lock.ref_of(settings, build.repo, build.target),
                             by=by, name=name, stage=stage)
    released = None
    done: list[Action] = []
    recorded: list[str] = []
    try:
        cleared = finish_lock.cleared_entry(taken)
        if cleared is not None:
            write.record(where, by=by, event=lambda _view, _result: cleared,
                         wait_s=wait_s, at=at, env=env)
            recorded.append("the finish lock of a holder that had ended was cleared")
        view = write.look(where, wait_s=wait_s, env=env)
        read = open_builds.of_round(name, [build.repo], settings=settings)
        seen = _seen(view, name, stage, settings, read=read)
        status = read[stage].status
        target_head = Git(root).branch(build.target)
        moved = _moved(seen, tip, name=name, stage=stage)
        unmet = moved or [one.unmet for one in _gates(view, name, stage, seen, tip, settings)
                          if one.number in UNDER_LOCK]
        if target_head is None:
            unmet.append(Unmet(f"the target {build.target} is no longer a branch of "
                               f"{build.repo}",
                               f"make {build.target} again, then {rerun}"))
        if unmet:
            raise Refused(unmet)

        settled = finish_actions.records(view.state, build, tip)
        passed = finish_actions.skipped(view.state, stage)
        for action in actions:
            last = settled.get(action["id"])
            if action["id"] in passed:
                done.append(Action(action["id"], action["do"], SKIPPED, ran=False,
                                   skipped_by=passed[action["id"]]))
                continue
            if last is not None and last.get("state") in SETTLED:
                done.append(Action(action["id"], action["do"], last["state"], ran=False,
                                   landing=last.get("landing"),
                                   target_before=last.get("target_before")))
                continue
            def append(result: Action) -> None:
                write.record(where, by=by, event=lambda _view, _result: (
                    write.Entry(ACTION, {"stage": stage, "tip": tip, "id": result.id,
                                         "kind": result.kind, "state": result.state,
                                         "landing": result.landing,
                                         "target_before": result.target_before})),
                    wait_s=wait_s, at=at, env=env)
                if result.state != STARTED:
                    recorded.append(_said(result))

            if action["do"] == own.MERGE:
                result = _found(status, root, tip, action_id=action["id"], name=name,
                                stage=stage)
                if result is not None:
                    result = _left_checkout_removed(result, root, build, settings)
                else:
                    result = _merge(root, build, tip, target_head, settings=settings,
                                    action_id=action["id"], rerun=rerun)
            elif action["do"] == own.PUSH:
                landing = next((one.landing for one in done if one.kind == own.MERGE),
                               None)
                result = _push(root, build, tip, action, landing=landing)
            else:
                why = finish_actions.run_again(action, last, again=again)
                started = Action(action["id"], own.COMMAND, STARTED, again=why,
                                 started_at=None if why is None else last.get("at"))
                append(started)
                if on_action is not None:
                    on_action(started)
                result = _command(view.state, name, build, root, tip, action)
            append(result)
            done.append(result)
            if on_action is not None:
                on_action(result)
            if result.state == FAILED:
                raise ActionFailed(name, stage, result.id, result.output,
                                   done=_acts(recorded[:-1], done, build, stage))

        def after(view) -> list[Unmet]:
            if _ended(view.state, stage):
                return []
            now = _seen(view, name, stage, settings, refuse=False, tips=at_tip)
            if isinstance(now, Unmet):
                return [now]
            return [one.unmet for one in _gates(view, name, stage, now, tip, settings)
                    if one.number in AFTER]

        def event(view, _result):
            if _ended(view.state, stage):
                return None
            return write.Entry(DONE, {"stage": stage, "tip": tip})

        written = write.record(where, by=by, gates=[after], event=event,
                               wait_s=wait_s, at=at, env=env)
    except Refused as exc:
        said = _acts(recorded, done, build, stage)
        if not said or exc.done:
            raise
        raise Refused(exc.unmet, done=said) from None
    finally:
        released = finish_lock.release(taken)
    cleaned = _clean_up(view.state, name, build, tip, done, root, settings=settings,
                        env=env)
    refs = ci_refs.remove(written.view.state, build, settings)
    return Finished(name=name, stage=stage, tip=tip, actions=tuple(done),
                    cleared=None if taken.cleared is None else taken.cleared.line,
                    event=written.events[-1] if written.events else None,
                    released=released, failed=written.failed, removed=cleaned.removed,
                    kept=cleaned.kept, branch=cleaned.branch, refs=refs,
                    unscanned=tuple(dict.fromkeys(unscanned + cleaned.unscanned)))


def _acts(recorded: list[str], done: list[Action], build: builds.Build,
         stage: str) -> tuple[str, ...]:
    """What a finish stopped before `finish.done` did and recorded, a line each: the
    `recorded` lines of this run, then, when a merge of this run or one before it is on
    the target, that the build reads merged and is not finished."""
    merged = next((one for one in done if one.kind == own.MERGE
                   and one.state in (OK, FOUND)), None)
    if merged is None:
        return tuple(recorded)
    return (*recorded, f"{stage} reads merged, not finished: its merge "
            f"{str(merged.landing)[:12]} is on {build.target}")


def _said(result: Action) -> str:
    landed = ("" if result.landing is None
              else f", {result.landing[:12]} on the target")
    return f"{result.id} ({result.kind}): {result.state}{landed}"


# -- the cleanup -------------------------------------------------------------------------

def _clean_up(state: states.State, name: str, build: builds.Build, tip: str,
              done: list[Action], root: Path, *, settings, env) -> _Cleaned:
    """Step 6: remove what the finish of `build` at `tip` leaves, under the removal
    rule, and give what was done; `branch` is said on the result. Nothing here undoes
    the finish."""
    commissioned = checking.commission_of(state, build)
    merged = any(one.kind == own.MERGE and one.state in (OK, FOUND) for one in done)
    recorded = {kind: Path(places.expand_home(commissioned[kind]))
                for kind in (removal.WORKTREE, removal.SCRATCH) if commissioned.get(kind)}
    there = {kind: place for kind, place in recorded.items() if place.is_dir()}
    if not merged:
        cleaned, unread = sweeping.remove_build_dirs(
            name, commissioned, (removal.SCRATCH,), stage=build.stage,
            settings=settings, env=env, after_gate=True)
        stays = tuple(removal.Kept(there[kind], "no merge", "no merge action put the "
                                   "work on the target, so the worktree and the "
                                   "branch stay", kind=kind)
                      for kind in there if kind == removal.WORKTREE)
        return _Cleaned(cleaned.removed, stays + cleaned.kept, unscanned=unread)
    try:
        now = Git(root).branch(build.branch)
        why = (f"the branch {build.branch} is gone" if now is None else
               f"the branch {build.branch} moved to {now[:12]} after the finish at "
               f"{tip[:12]}")
    except GitError as exc:
        now, why = None, f"the branch {build.branch} can't be read: {exc.reason}"
    if now != tip:
        return _Cleaned(kept=tuple(removal.Kept(place, "tip", f"{why}, so its folders "
                                                "and the branch stay", kind=kind)
                                   for kind, place in there.items()))
    keep_branch = own.build_setting(settings, build.repo, "keep_branch")
    cleaned, unread = sweeping.remove_build_dirs(
        name, commissioned, stage=build.stage, settings=settings, env=env,
        deletes=None if keep_branch else build.branch, after_gate=True)
    if keep_branch:
        return _Cleaned(cleaned.removed, cleaned.kept, unscanned=unread)
    if any(one.kind == removal.WORKTREE for one in cleaned.kept):
        return _Cleaned(cleaned.removed, cleaned.kept,
                        "kept: its worktree was kept, and git deletes no branch a "
                        "worktree has checked out", unread)
    try:
        Git(root)._run("branch", "-d", build.branch)
    except GitError as exc:
        return _Cleaned(cleaned.removed, cleaned.kept, f"kept: {exc.reason}", unread)
    return _Cleaned(cleaned.removed, cleaned.kept, "deleted", unread)


@dataclass(frozen=True)
class _Cleaned:
    removed: tuple[Path, ...] = ()
    kept: tuple[removal.Kept, ...] = ()
    branch: str | None = None
    unscanned: tuple[str, ...] = ()


# -- reading the build -------------------------------------------------------------------

def _open_build(state: states.State, name: str, stage: str) -> builds.Build:
    build = builds.current(state, stage)
    if state.status != states.OPEN:
        raise Refused([Unmet(f"the round {name} is {state.status}: it finishes no build",
                             f"moxi rounds show {name}")])
    if build is None:
        raise Refused([Unmet(f"{stage} has no build in {name}, so nothing is finished",
                             f"moxi rounds commission {name} {stage} --builder NAME")])
    if build.ended is not None:
        raise Refused([Unmet(f"{stage}'s build is {build.ended}: nothing is left to do, "
                             "and no second finish is recorded",
                             f"moxi rounds show {name}")])
    return build


def _ended(state: states.State, stage: str) -> bool:
    build = builds.current(state, stage)
    return build is not None and build.ended is not None


def _seen(view, name: str, stage: str, settings, *, refuse: bool = True,
          tips: Mapping[str, str] | None = None, read=None):
    """What git and the settings say now of the stage's build, at the tip `tips` gives
    it, else at its branch's tip now; `read` is the round's open builds as
    `open_builds.of_round` read them, read here when left out. With `refuse` false,
    the problem that kept it from being read is given back as an `Unmet` instead of
    raised."""
    seen = checking.seen_of(view.state, name, settings=settings, tips=tips,
                            read=read).get(stage)
    problem = (Unmet(f"{stage}'s build can't be read", f"moxi rounds show {name}")
               if seen is None else seen.problem)
    if problem is None:
        return seen
    if not refuse:
        return problem
    raise Refused([problem])


def _gates(view, name: str, stage: str, seen, tip: str, settings) -> tuple:
    return finish_gates.unmet(
        view.state, stage, seen=seen, tip=tip, name=name, register=view.project_state,
        on_closed=lambda round_name: questions.is_closed(round_name, settings))


def _moved(seen, tip: str, *, name: str, stage: str) -> list[Unmet]:
    if seen.tip is None or seen.tip == tip:
        return []
    return [Unmet(f"{stage}'s branch moved from {tip[:12]} to {seen.tip[:12]} since its "
                  "gates were read", f"moxi rounds finish {name} {stage}")]


# -- a merge found in git ----------------------------------------------------------------

def _found(status: builds.Status, root: Path, tip: str, *, action_id: str, name: str,
           stage: str) -> Action | None:
    """The merge action recorded `found` when the build, as `status` read it, is already
    merged into its target; None when it is not, and the merge is to be made."""
    if status.state != builds.MERGED:
        return None
    if status.by == builds.HAND_MERGE:
        merge = str(status.hand_merge)
        return Action(action_id, own.MERGE, FOUND, landing=merge,
                      target_before=status.target_before,
                      output=_MergeGit(root).shortstat(f"{merge}^1", merge),
                      hand_merge=True)
    if status.by == builds.FAST_FORWARD:
        return Action(action_id, own.MERGE, FOUND, landing=tip,
                      target_before=status.target_before)
    raise Refused([Unmet(
        f"{stage}'s build is on its target by a merge of its own finish at another tip, "
        f"and its branch is now at {tip[:12]}, which holds nothing more; recording that "
        "merge again would leave the build's landings telling no head apart",
        f"move {stage}'s branch back to the tip its finish merged, then moxi rounds "
        f"finish {name} {stage}")])


# -- a push and a command ----------------------------------------------------------------

def _push(root: Path, build: builds.Build, tip: str, action: Mapping, *,
          landing: str | None) -> Action:
    """Push the target at `landing` when a merge action came before, else the build's
    branch at `tip`. A push of the target git refuses passes when the remote's target
    already holds the landing: it is there or moved on past it, and the push would have
    changed nothing. git never moves the remote's target back, since nothing is forced."""
    remote = str(action["remote"])
    ref = (f"{landing}:refs/heads/{build.target}" if landing is not None
           else f"{tip}:refs/heads/{build.branch}")
    done = _remote_git(root, "push", remote, ref)
    if done.code == 0 or landing is None:
        return Action(action["id"], own.PUSH, OK if done.code == 0 else FAILED,
                      output=done.said)
    there = _remote_holds(root, remote, build.target, landing)
    if there is None:
        return Action(action["id"], own.PUSH, FAILED, output=done.said)
    return Action(action["id"], own.PUSH, OK,
                  output=f"{remote}'s {build.target} is at {there[:12]}, which holds "
                         f"{landing[:12]} already: nothing was pushed")


@dataclass(frozen=True)
class _Said:
    code: int
    said: str


def _remote_git(root: Path, *args: str) -> _Said:
    """Run a git command that reaches a remote, in the repo's root, for at most
    `PUSH_TIMEOUT_S`: its exit code, 1 for a git that gave no answer or could not run,
    and what it printed."""
    argv = ["git", "-C", str(root), *args]
    try:
        done = proc.run(argv, timeout_s=PUSH_TIMEOUT_S, env=gitreads.git_env(),
                        stdin_data="")
    except subprocess.TimeoutExpired:
        return _Said(1, f"git {args[0]} gave no answer in {PUSH_TIMEOUT_S:g} seconds")
    except OSError as exc:
        return _Said(1, f"git could not run: {exc}")
    said = "\n".join(part.strip() for part in (done.stdout or "", done.stderr or "")
                     if part.strip())
    return _Said(done.returncode, said)


def _remote_holds(root: Path, remote: str, target: str, landing: str) -> str | None:
    """The commit `remote`'s branch `target` is at when it holds `landing` (is it, or
    has it as an ancestor); None when it does not, or can't be read. A head this repo
    lacks is fetched so its history can be read. The fetch writes no FETCH_HEAD and
    names no ref to write, but git still refreshes the remote-tracking branch
    `refs/remotes/<remote>/<target>` from it, as a push that succeeds does; it changes
    no other ref."""
    ref = f"refs/heads/{target}"
    listed = _remote_git(root, "ls-remote", remote, ref)
    heads = [line.split()[0] for line in listed.said.splitlines()
             if listed.code == 0 and line.split()[1:] == [ref]]
    if len(heads) != 1 or not gitreads.is_id(heads[0]):
        return None
    head = heads[0]
    git = Git(root)
    try:
        if not git.present([head]):
            _remote_git(root, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
                        remote, ref)
            if not git.present([head]):
                return None
        return head if git.is_ancestor(landing, head) else None
    except GitError:
        return None


def _command(state: states.State, name: str, build: builds.Build, root: Path, tip: str,
             action: Mapping) -> Action:
    """Run a command action in the build's worktree, its output the caller's own."""
    commissioned = checking.commission_of(state, build)
    worktree = Path(places.expand_home(commissioned.get("worktree")))
    argv = checking.fill(action["argv"], checking.values(name, build, root=root, tip=tip))
    timeout_s = action.get("timeout_s")
    try:
        done = proc.run(argv, timeout_s=timeout_s, env=gitreads.git_env(),
                        cwd=worktree, capture=False)
    except subprocess.TimeoutExpired:
        return Action(action["id"], own.COMMAND, FAILED,
                      output=f"{shlex.join(argv)} ran past {timeout_s:g} seconds")
    except OSError as exc:
        return Action(action["id"], own.COMMAND, FAILED,
                      output=f"{shlex.join(argv)} could not be started: {exc}")
    if done.returncode == 0:
        return Action(action["id"], own.COMMAND, OK)
    return Action(action["id"], own.COMMAND, FAILED,
                  output=f"{shlex.join(argv)} exited {done.returncode}")


# -- the merge ---------------------------------------------------------------------------

def _own_checkout(build: builds.Build, settings) -> Path:
    """Where finish adds a checkout of the build's target that no worktree has."""
    return (places.worktree_dir(settings) / build.repo
            / f"{CHECKOUT_PREFIX}{places.encode(build.target)}")


def _is_own(place: Path, build: builds.Build, settings) -> bool:
    return place.resolve() == _own_checkout(build, settings).resolve()


def _merge(root: Path, build: builds.Build, tip: str, target_head: str, *, settings,
           action_id: str, rerun: str) -> Action:
    git = _MergeGit(root)
    place = git.checkout_of(build.target)
    # A checkout of the target at finish's own place is finish's own, though a stopped
    # finish left it: it is merged in and removed after, as one added now would be.
    if place is not None and not _is_own(place, build, settings):
        return _merge_at(place, build, tip, target_head, action_id=action_id,
                         rerun=rerun)
    if place is None:
        place = _own_checkout(build, settings)
        git.add_checkout(place, build.target)
    try:
        made = _merge_at(place, build, tip, target_head, action_id=action_id,
                         rerun=rerun)
    finally:
        # The checkout made for the merge goes only when it holds nothing a removal
        # would lose; the merge it made stands either way.
        kept = removal.checkout_removal(root, place, settings=settings, repo=build.repo)
    return made if kept is None else replace(made, kept=f"{kept.place}: {kept.line()}")


def _merge_at(place: Path, build: builds.Build, tip: str, target_head: str, *,
              action_id: str, rerun: str) -> Action:
    there = _MergeGit(place)
    unmet = []
    if there.has_changes():
        unmet.append(Unmet(f"the checkout of {build.target} at {place} holds "
                           "uncommitted changes, so the merge can't be made there",
                           f"commit or remove them, then {rerun}"))
    head = there.head()
    if head != target_head:
        unmet.append(Unmet(f"the checkout of {build.target} at {place} is at "
                           f"{str(head)[:12]}, not the target's head "
                           f"{target_head[:12]}",
                           f"bring that checkout to {build.target}, then {rerun}"))
    if unmet:
        raise Refused(unmet)
    code, output = there.merge(tip, f"Merge branch '{build.branch}'")
    conflicts = code != 0 and there.conflicts()
    if code != 0 and there.merging():
        # A merge stopped by a conflict or by a hook is taken back whole, so the
        # target's checkout is as it was.
        there.abort()
    if conflicts:
        raise Refused([Unmet(
            f"merging {build.branch} at {tip[:12]} into {build.target} conflicts; "
            "the merge was aborted",
            f"git merge --no-ff {build.target} in the build's worktree, run moxi "
            f"rounds check, then {rerun}")])
    if code != 0:
        return Action(action_id, own.MERGE, FAILED, output=output)
    landing = there.head()
    return Action(action_id, own.MERGE, OK, landing=landing,
                  target_before=target_head, output=output)


def _left_checkout_removed(found: Action, root: Path, build: builds.Build,
                           settings) -> Action:
    """`found` once the checkout of the target at finish's own place, which a stopped
    finish left and may have merged in, is removed under the no-lost-work rule; one that
    rule keeps is named in `kept`."""
    place = _MergeGit(root).checkout_of(build.target)
    if place is None or not _is_own(place, build, settings):
        return found
    kept = removal.checkout_removal(root, place, settings=settings, repo=build.repo)
    return found if kept is None else replace(found, kept=f"{kept.place}: {kept.line()}")


class _MergeGit(Git):
    """The git calls of the merge action, in the repo or worktree at `root`."""

    def checkout_of(self, branch: str) -> Path | None:
        """The worktree that has `branch` checked out, or None."""
        _, out = self._run("worktree", "list", "--porcelain")
        place = None
        for line in out.splitlines():
            if line.startswith("worktree "):
                place = Path(line[len("worktree "):])
            elif line == f"branch refs/heads/{branch}" and place is not None:
                return place
        return None

    def add_checkout(self, place: Path, branch: str) -> None:
        self._run("worktree", "add", "--quiet", str(place), branch)

    def head(self) -> str | None:
        return self._commit("HEAD")

    def conflicts(self) -> bool:
        """Whether the work tree holds paths a merge left unmerged."""
        _, out = self._run("diff", "--name-only", "--diff-filter=U")
        return bool(out.strip())

    def merging(self) -> bool:
        code, _ = self._run("rev-parse", "--verify", "--quiet", "MERGE_HEAD", ok=(0, 1))
        return code == 0

    def shortstat(self, start: str, end: str) -> str:
        """What `git diff --shortstat` says of `start` to `end`, one line."""
        _, out = self._run("diff", "--shortstat", start, end)
        return out.strip()

    def abort(self) -> None:
        self._run("merge", "--abort")

    def merge(self, tip: str, message: str) -> tuple[int, str]:
        """Merge the commit `tip` with a merge commit whose message is `message`: the
        exit code and what git printed."""
        argv = ["git", "-C", str(self.root), "merge", "--no-ff", "--no-edit", "-m",
                message, tip]
        try:
            done = proc.run(argv, timeout_s=MERGE_TIMEOUT_S, env=git_env(),
                            stdin_data="")
        except subprocess.TimeoutExpired:
            return 124, f"the merge gave no answer in {MERGE_TIMEOUT_S:g} seconds"
        except OSError as exc:
            raise GitError(argv, f"could not run: {exc}") from None
        said = "\n".join(part.strip() for part in (done.stdout or "", done.stderr or "")
                         if part.strip())
        return done.returncode, said


__all__ = ["ACTION", "DONE", "OK", "FAILED", "FOUND", "STARTED", "SETTLED", "REPEAT",
           "AGAIN", "SKIPPED", "UNDER_LOCK",
           "AFTER", "CHECKOUT_PREFIX", "MERGE_TIMEOUT_S", "PUSH_TIMEOUT_S", "ActionFailed",
           "Action", "Finished", "finish"]
