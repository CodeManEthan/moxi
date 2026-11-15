"""`finish`: a build's last step, which puts it where it goes.

`finish(name, stage)` runs the finish of the stage's open build at `T`, the branch's
tip as read (its head, once the branch is gone), in this order:

1. Under the round's lock, read the round and evaluate every gate of `finish_gates` at
   `T`. Refuse with every unmet one. A build already finished or dropped is refused
   before any gate: nothing is left to do, and no second `finish.done` is written.
2. Take the repo's finish lock (`finish_lock`), recording `lock.cleared` when the take
   swapped out a dead holder.
3. Read the round, the target's head `M` and the branch's tip again. Refuse when the
   tip is no longer `T`, or when a gate another finish may have changed by moving the
   target (`UNDER_LOCK`: drift, the target, work, sharing) no longer holds.
4. Run the repo's finish actions in order, each under its `id`, and record each one's
   result as `finish.action` before the next starts; each record takes the round's
   lock for that write only. An action whose record at `T` is `ok` or `found` is not
   run again; one recorded `failed`, or with no record, is run. A failed action is
   recorded `failed` and ends the verb (`ActionFailed`, exit 1); nothing is undone.
5. Under the round's lock, read the log again and append `finish.done` when the log
   holds none for the build and the gates read on the log (`AFTER`: verification,
   findings, the check, questions, steps of the user's own) still hold at `T`; else
   refuse with what is owed. The build is read at `T` here, not at its branch's tip
   now: `T` is what the merge took, and a commit made on the branch while the actions
   ran is not part of this finish.

The finish lock is released however the verb ends once it was taken: only while it
still points at this finish's own blob.

**The merge.** `git merge --no-ff T` in the checkout of the target: the worktree that
has the target checked out, which must hold nothing uncommitted with its `HEAD` at `M`;
or, when no worktree has it, `<worktree_dir>/<repo>/.finish-<into>`, added for the merge
and removed after it. `T` is given as the commit id the gates read, so a commit made on
the branch after them is not merged; the message names the branch. Its record holds the
landing, the merge commit, and `P`, the target before it, which is `M`. A conflict is
aborted and refused with the way through: merge the target into the branch in the
build's worktree, check, and finish again. A merge that fails otherwise (a hook refused
it, say) is aborted too, and recorded as failed.

**A merge found in git.** Before a merge is made, the build is read as step 3 read it:
when it is already `merged` (by a hand merge, or by a fast-forward of the target onto
its branch), the merge is not made again but recorded `found`, with the landing and
`P` that read gives: the bringing merge and its first parent for a hand merge, the tip
and the target before it for a fast-forward. A hand merge's record carries what it put
on the target, `git diff --shortstat` of its first parent and itself, for the caller to
print. That covers a merge made by hand, and one a stopped finish made and did not
record. A build merged by a landing of its own on record at another tip is refused:
recording that landing again would give the build two landings no head tells apart.

This version runs the `merge` action; a repo whose finish names an action of another
kind is refused before anything is run.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from moxi import exit as exits
from moxi import proc

from . import (builds, checking, finish_gates, finish_lock, folders, open_builds, places,
               questions, write)
from . import settings as own
from . import state as states
from .errors import RoundsError, Refused, Unmet
from .gitreads import Git, GitError, environment

#: The event of one finish action's result, and of a whole finish done.
ACTION = "finish.action"
DONE = "finish.done"
#: The states of a finish action's record: run and passed, run and failed, or a merge
#: found already on the target.
OK, FAILED, FOUND = "ok", "failed", "found"
#: The states of a record that mean the action is not run again.
SETTLED = (OK, FOUND)
#: The gates read again under the finish lock before any action is run: those another
#: finish may have changed by moving the target.
UNDER_LOCK = (finish_gates.DRIFT, finish_gates.TARGET, finish_gates.WORK,
              finish_gates.SHARING)
#: The gates read again on the log after the actions, before `finish.done`.
AFTER = (finish_gates.VERIFIED, finish_gates.FINDINGS, finish_gates.CHECKED,
         finish_gates.QUESTIONS, finish_gates.USER_STEPS)
#: The kinds of finish action this version runs.
RUNS = (own.MERGE,)
#: The folder name, in `<worktree_dir>/<repo>`, of the checkout of a target that no
#: worktree has checked out, before the target's encoded name.
CHECKOUT_PREFIX = ".finish-"
#: How long the merge may take, hooks included, in seconds.
MERGE_TIMEOUT_S = 3600.0


class ActionFailed(RoundsError):
    """A finish action failed: `id` is the action, `output` what it printed. Its
    `failed` record is on the log, nothing is undone, and the finish lock is released."""

    exit_code = exits.ERROR

    def __init__(self, name: str, stage: str, action_id: str, output: str):
        self.id = action_id
        self.output = output
        super().__init__(f"the finish action {action_id} of {stage} failed; it is "
                         f"recorded as failed, and nothing is undone",
                         f"fix what it printed, then moxi rounds finish {name} {stage}")


@dataclass(frozen=True)
class Action:
    """One finish action as this finish recorded or passed it: its `id` and `kind`, the
    `state` recorded (`OK`, `FAILED`, `FOUND`), or `ran` false when a record at the tip
    already settled it; a merge's `landing` and `target_before`; what it printed, or for
    a merge found made by hand (`hand_merge`), what that merge put on the target."""

    id: str
    kind: str
    state: str
    ran: bool = True
    landing: str | None = None
    target_before: str | None = None
    output: str = ""
    kept: str | None = None
    hand_merge: bool = False


@dataclass(frozen=True)
class Finished:
    """What `finish` did: the round, the stage, the tip `T` it finished, each action,
    the dead holder's line when its lock was cleared, the `finish.done` event (None when
    the log already held one), and the after-append callbacks that failed."""

    name: str
    stage: str
    tip: str
    actions: tuple[Action, ...]
    cleared: str | None = None
    event: dict | None = None
    released: bool = True
    failed: tuple = field(default=())


def finish(name: str, stage: str, *, actor: str, settings=None,
           wait_s: float = write.WAIT_S, at: str | None = None, env=None,
           on_action: Callable[[Action], None] | None = None) -> Finished:
    """Finish the open build of `stage` in the round `name`. `settings` is what
    `settings.load()` gives, read now when left out. `on_action` is called with each
    action this finish records, as soon as its record is on the log, so a caller can
    say what was done even when the finish is refused later.

    Raises what `folders.home` raises for a round that can't be found; `Refused` with
    every unmet gate, a held finish lock, a branch that moved, a checkout of the target
    that can't take the merge, or a conflict, in which case no action was recorded;
    `ActionFailed` when an action failed; and `GitError` when git can't be read."""
    project, slug = folders.split(name)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    where = write.Where(home.project, home.folder, slug)
    again = f"moxi rounds finish {name} {stage}"

    view = write.look(where, wait_s=wait_s, env=env)
    build = _open_build(view.state, name, stage)
    seen = _seen(view, name, stage, settings)
    tip = seen.tip or seen.head
    # Read again at `T` after the actions only where `T` was the branch's tip: a build
    # whose branch was gone is read as gone.
    at_tip = None if seen.tip is None else {stage: tip}
    found = [one.unmet for one in _gates(view, name, stage, seen, tip, settings)]
    actions = own.finish_actions(own.build_setting(settings, build.repo, "finish"))
    found += _not_run(actions, build.repo)
    if found:
        raise Refused(found)

    _key, root = folders.root(build.repo)
    taken = finish_lock.take(root, finish_lock.ref_of(settings, build.repo, build.target),
                             actor=actor, name=name, stage=stage)
    released = None
    try:
        cleared = finish_lock.cleared_entry(taken)
        if cleared is not None:
            write.record(where, actor=actor, event=lambda _view, _result: cleared,
                         wait_s=wait_s, at=at, env=env)
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
                               f"{build.repo}", f"make {build.target} again, then {again}"))
        if unmet:
            raise Refused(unmet)

        done = []
        settled = _records(view.state, build, tip)
        for action in actions:
            last = settled.get(action["id"])
            if last is not None and last.get("state") in SETTLED:
                done.append(Action(action["id"], action["do"], last["state"], ran=False,
                                   landing=last.get("landing"),
                                   target_before=last.get("target_before")))
                continue
            result = _found(status, root, tip, action_id=action["id"], name=name,
                            stage=stage)
            if result is None:
                result = _merge(root, build, tip, target_head, settings=settings,
                                action_id=action["id"], again=again)
            write.record(where, actor=actor, event=lambda _view, _result, result=result: (
                write.Entry(ACTION, {"stage": stage, "tip": tip, "id": result.id,
                                     "kind": result.kind, "state": result.state,
                                     "landing": result.landing,
                                     "target_before": result.target_before})),
                wait_s=wait_s, at=at, env=env)
            done.append(result)
            if on_action is not None:
                on_action(result)
            if result.state == FAILED:
                raise ActionFailed(name, stage, result.id, result.output)

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

        written = write.record(where, actor=actor, gates=[after], event=event,
                               wait_s=wait_s, at=at, env=env)
    finally:
        released = finish_lock.release(taken)
    return Finished(name=name, stage=stage, tip=tip, actions=tuple(done),
                    cleared=None if taken.cleared is None else taken.cleared.line,
                    event=written.events[-1] if written.events else None,
                    released=released, failed=written.failed)


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
        closed=lambda round_name: questions.is_closed(round_name, settings))


def _not_run(actions: list[dict], repo: str) -> list[Unmet]:
    other = [action["id"] for action in actions if action["do"] not in RUNS]
    if not other:
        return []
    return [Unmet(f"the finish of {repo} names the action"
                  f"{'' if len(other) == 1 else 's'} {', '.join(other)}, of a kind this "
                  "version of moxi-rounds does not run",
                  f"set [projects.{repo}] finish to the merge action alone, or leave it "
                  "out")]


def _moved(seen, tip: str, *, name: str, stage: str) -> list[Unmet]:
    if seen.tip is None or seen.tip == tip:
        return []
    return [Unmet(f"{stage}'s branch moved from {tip[:12]} to {seen.tip[:12]} since its "
                  "gates were read", f"moxi rounds finish {name} {stage}")]


def _records(state: states.State, build: builds.Build, tip: str) -> dict[str, dict]:
    """The newest `finish.action` record of each action id for the build at `tip`."""
    newest: dict[str, dict] = {}
    for event in state.history:
        if (event["type"] == ACTION and event["n"] > build.n
                and event.get("stage") == build.stage and event.get("tip") == tip
                and isinstance(event.get("id"), str)):
            newest[event["id"]] = event
    return newest


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


# -- the merge ---------------------------------------------------------------------------

def _merge(root: Path, build: builds.Build, tip: str, target_head: str, *, settings,
           action_id: str, again: str) -> Action:
    git = _MergeGit(root)
    place = git.checkout_of(build.target)
    added = None
    if place is None:
        place = (places.worktree_dir(settings) / build.repo
                 / f"{CHECKOUT_PREFIX}{places.encode(build.target)}")
        git.add_checkout(place, build.target)
        added = place
    try:
        there = _MergeGit(place)
        unmet = []
        if there.has_changes():
            unmet.append(Unmet(f"the checkout of {build.target} at {place} holds "
                               "uncommitted changes, so the merge can't be made there",
                               f"commit or remove them, then {again}"))
        head = there.head()
        if head != target_head:
            unmet.append(Unmet(f"the checkout of {build.target} at {place} is at "
                               f"{str(head)[:12]}, not the target's head "
                               f"{target_head[:12]}",
                               f"bring that checkout to {build.target}, then {again}"))
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
                f"rounds check, then {again}")])
        if code != 0:
            return Action(action_id, own.MERGE, FAILED, output=output)
        landing = there.head()
        return Action(action_id, own.MERGE, OK, landing=landing,
                      target_before=target_head, output=output)
    finally:
        if added is not None:
            git.remove_checkout(added)


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

    def remove_checkout(self, place: Path) -> None:
        try:
            self._run("worktree", "remove", str(place))
        except GitError:
            # A checkout that can't be removed is left; the merge it made stands.
            pass

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
            done = proc.run(argv, timeout_s=MERGE_TIMEOUT_S, env=environment(),
                            stdin_data="")
        except subprocess.TimeoutExpired:
            return 124, f"the merge gave no answer in {MERGE_TIMEOUT_S:g} seconds"
        except OSError as error:
            raise GitError(argv, f"could not run: {error}") from None
        said = "\n".join(part.strip() for part in (done.stdout or "", done.stderr or "")
                         if part.strip())
        return done.returncode, said


__all__ = ["ACTION", "DONE", "OK", "FAILED", "FOUND", "SETTLED", "UNDER_LOCK",
           "AFTER", "RUNS", "CHECKOUT_PREFIX", "MERGE_TIMEOUT_S", "ActionFailed",
           "Action", "Finished", "finish"]
