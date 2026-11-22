"""One pass of the lander over a repo's queue (`step`): take members, build a candidate,
check it, read its CI, and land it.

Every pass reads the queue file and git afresh and does the next thing they call for,
so a lander killed anywhere is finished by the next one. The lander's records name it
`rounds` (`LANDER`).

**Take** (`take`): every urgent member that waits, in join order, and no other; else,
unless the queue is paused, every member that waits, in join order.

**Build** the candidate on a local branch `queue-<M12>-<n>` (`BRANCH_PREFIX`) from the
target's head `M`, in the lander's own worktree `<worktree_dir>/<repo>/.queue`
(`place_of`), merging each member's joined tip `T` in turn with `git merge --no-ff` and
the finish's message, so each merge runs the repo's commit hooks. A member whose round
or build has ended, or whose branch moved off `T`, is recorded `left`. A member that
conflicts is ejected with the member before it whose change it conflicts with (or the
target, when none is), and one a hook refuses is ejected with the hook's output; either
merge is aborted and the rest go on. Members are batched only with members of the same
target. The batch is recorded `batch` (state `built`) with its candidate, and each
member's round log gets `queue.batch`.

**Check**: push the candidate to the ref the repo's `ci.ref` gives the branch (the
branch's own name with no `ref`), so each batch has a ref of its own and the ref carries
a local branch of the same name. A push the publishing scan refuses ejects each member
whose joined tip or changed path the refusal names, and the batch ends to be rebuilt
without them. A refusal that names none (the scan's own, the network, a ruleset) is
tried again at the next pass, and a second pauses the queue with it as the reason. Then
`ci.local` runs at the candidate; a failure there counts as a red job `local`. A repo
with no `ci` setting has no CI to read a candidate by: the queue pauses, saying so.

**Read** CI each pass until it reads `green`, `red`, `cancelled`, or `absent` for
`ABSENT_S` after the push. A cancelled run is rerun once; an absent one ends the batch,
so the next pass pushes it again under the next batch number. A second cancelled run,
or a second absent one in a row, pauses the queue with that reason. A read that fails
(`ci.ReadFailed`) is tried again at each pass, and the queue pauses once `READ_FAILS_MAX`
fail in a row. A finish lock another holds is tried again at the next pass.

**Green: land** (`_land`), under the repo's finish lock, taken as `queue/<repo>` and
the batch's id. Read again: the target is still at `M`; each member is still in this
batch at its joined tip, its round and build open, and passes every finish gate but the
check (3), the drift (5) and the target (6) at `T`. Drift was read at join against `J`,
and `J` does not move. The checkout that has the target is clean and at `M`. If the
target moved, the batch ends and is rebuilt on it, with a new run. If a member fails,
it is recorded `left` with the gate, and the batch ends and is rebuilt without it. If
the checkout is not clean, `waiting` is recorded once and the landing is tried again
at the next pass. Otherwise the checkout is fast-forwarded to the candidate (`git merge
--ff-only`). Still under the lock, each member in batch order gets its check records at
`T` naming the candidate (`ci.read` and, where `ci.local` ran, `check.ran` under
`check.local`) and is finished by `finishing.finish_member`. Then the lock is released,
each member is recorded `landed` at its own merge commit, and the batch ends.

A candidate found on the target already (a lander killed after the fast-forward) is
landed from its member finishes on, with no re-read.

**Red** (`red`): a candidate whose CI reads red, or whose `ci.local` failed, has its
failed jobs rerun once (`ci.local` run again for `local`); green then lands. Still red,
its failures are listed (`failures.of_run`, the local job as `local`) and compared with
the target's own at `M` (`target_failures`): the target's full run at `M`, read once it
has ended and, when red, after one rerun of its failed jobs, recorded `green` or `red`
with its failures; and, when the candidate's `local` failed, `ci.local` run once at `M`.
Until both are known the batch waits, recorded `waiting`; a target that moved off `M`
meanwhile ends the batch, to be rebuilt on it. No failure the target lacks: the
candidate lands, each member's `ci.read` naming the failures in `excused` and its
`landed` event too. A new failure with one member ejects it as `CI red`, with the run
and the new failures. With two or more, the batch is split in two by join order
(`failures.halves`): it ends naming the halves, and the next passes build each as a
batch of its own on the target's head then, run side by side by this same rule.

**Several batches** run only as the halves of a split. A pass advances each, oldest
first, and a batch lands only when no older one still runs: so the earlier half lands
first, and a later green half waits for it. A half whose target moved, or that lost a
member at the re-read, ends naming its remaining members as one group, which the next
pass rebuilds on the target's head then. Members that join meanwhile wait until no
batch runs and no group waits to be built.

When a batch ends, its ref is removed from the remote and its local branch deleted.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from . import (builds, checking, ci, finish_gates, finish_lock, finishing, folders,
               places, queue_ci, queue_file, queue_log, removal, write)
from . import failures as failing
from . import settings as own
from . import state as states
from .errors import Refused, RoundsError, Unmet
from .gitreads import GitError

#: Who the lander's records name.
LANDER = "rounds"
#: The lander's worktree's folder name in `<worktree_dir>/<repo>`.
QUEUE_PLACE = ".queue"
#: The start of a candidate's local branch name.
BRANCH_PREFIX = "queue-"
#: How long after its push a candidate's run may read absent before it is pushed again.
ABSENT_S = 600.0
#: The local run's job name, as a red candidate names it.
LOCAL_JOB = ci.LOCAL_JOB
#: Why a batch ended.
LANDED_WHY = "landed"
MOVED_WHY = "the target moved"
ABSENT_WHY = "absent"
CANCELLED_WHY = "cancelled"
SCAN_WHY = "the publishing scan refused its push"
PAUSED_WHY = "paused"
RED_WHY = "red"
SPLIT_WHY = "split"
#: The `run` events' kinds: the push, the local run, a read, a rerun, a push refused
#: with no member named, a read that failed, a red candidate's failures, and the
#: failures it lands with as the target's own.
PUSHED, LOCAL, READ, RERUN = "pushed", "local", "read", "rerun"
PUSH_FAILED, READ_FAILED = "push failed", "read failed"
FAILURES, EXCUSED = "failures", "excused"
#: How many reads of a candidate's CI may fail in a row before the queue pauses.
READ_FAILS_MAX = 5
#: The finish gates the landing re-read does not read.
NOT_REREAD = (finish_gates.CHECKED, finish_gates.DRIFT, finish_gates.TARGET)


def place_of(settings, repo: str) -> Path:
    """The lander's worktree for `repo`."""
    return places.worktree_dir(settings) / repo / QUEUE_PLACE


def take(queue: queue_file.Queue) -> list[queue_file.Member]:
    """The members the next batch takes: the urgent ones that wait, if any, else every
    one that waits, unless the queue is paused."""
    waiting = queue.waiting()
    urgent = [one for one in waiting if one.urgent]
    if urgent:
        return urgent
    return [] if queue.paused else waiting


def step(repo: str, *, settings=None, candidate_ci=None, env=None,
         say: Callable[[str], None] = print,
         clock: Callable[[], float] = time.time) -> None:
    """One pass of the lander over `repo`'s queue. `candidate_ci` stands in for the
    candidate's CI (`queue_ci.QueueCi` from the repo's `ci` setting when left out), and
    `clock` for the time now. A failure of rounds or git is said, and the next pass
    tries again."""
    settings = own.load() if settings is None else settings
    try:
        _Pass(repo, settings=settings, candidate_ci=candidate_ci, env=env, say=say,
              clock=clock).run()
    except RoundsError as exc:
        say(f"lander of {repo}: {exc}")


class _MemberGone(Exception):
    """A member no longer to be merged: `why` says why, `kind` is the queue event."""

    def __init__(self, why: str, kind: str = queue_file.LEFT):
        super().__init__(why)
        self.why = why
        self.kind = kind


class _Pass:
    def __init__(self, repo: str, *, settings, candidate_ci, env, say, clock):
        self.repo, self.settings, self.env, self.say, self.clock = (
            repo, settings, env, say, clock)
        _key, self.root = folders.root(repo)
        self.place = place_of(settings, repo)
        found = ci.declared(settings, repo)
        self.ci = candidate_ci
        if self.ci is None and found is not None:
            self.ci = queue_ci.QueueCi(
                found, local_log=queue_file.queue_dir(repo, env) / "local.log")
        self.ref_template = None if found is None else found.ref

    # -- the pass ------------------------------------------------------------------------

    def run(self) -> None:
        queue = queue_file.read(self.repo, self.env)
        if self.ci is None:
            if queue.queued() and queue.paused is None:
                self.pause(f"{self.repo} has no ci setting, so a candidate has no CI to "
                           "be read by", queue.running())
            return
        running = queue.running_batches()
        for batch in running:
            now = queue_file.read(self.repo, self.env)
            found = now.batch(batch.id)
            if found is not None and not found.ended:
                self.advance(now, found)
        regrouped = queue_file.read(self.repo, self.env).regrouped()
        for source, members in regrouped:
            paused = queue_file.read(self.repo, self.env).paused
            members = [one for one in members if paused is None or one.urgent]
            if members:
                self.build(queue_file.read(self.repo, self.env), members, source=source)
        if running or regrouped:
            return
        members = take(queue)
        if members:
            self.build(queue, members)

    def read_member(self, member: queue_file.Member) -> tuple[write.Where, builds.Build]:
        """The member's round's place and its build; raises `_MemberGone` when the
        round or the build has ended or its branch is no longer at the joined tip."""
        where = _where(member.name, self.settings)
        try:
            view = write.look(where, env=self.env)
        except RoundsError as exc:
            raise _MemberGone(f"its round can't be read: {exc.reason}") from None
        if view.state.status != states.OPEN:
            raise _MemberGone(f"its round is {view.state.status}")
        build = builds.current(view.state, member.stage)
        if build is None or build.ended is not None:
            raise _MemberGone("its build has ended")
        if QueueGit(self.root).branch(build.branch) != member.tip:
            raise _MemberGone(queue_file.MOVED)
        return where, build

    # -- build ---------------------------------------------------------------------------

    def build(self, queue: queue_file.Queue, members: list[queue_file.Member], *,
              source: tuple[str, int] | None = None) -> None:
        """Build a batch of `members` on the target's head; `source` is the group of an
        ended batch it is built from."""
        git = QueueGit(self.root)
        target = None
        ready: list[tuple[queue_file.Member, builds.Build]] = []
        for member in members:
            try:
                _place, build = self.read_member(member)
            except _MemberGone as exc:
                self.end_member(member, exc.kind, reason=exc.why)
                continue
            target = target or build.target
            if build.target == target:
                ready.append((member, build))
        if not ready:
            return
        main = git.branch(str(target))
        if main is None:
            self.say(f"lander of {self.repo}: the target {target} is not a branch")
            return
        n = max((one.n for one in queue.batches), default=0) + 1
        batch_id = f"{main[:12]}-{n}"
        branch = BRANCH_PREFIX + batch_id
        self.ready_place(branch, main)
        there = QueueGit(self.place)
        merged: list[tuple[queue_file.Member, builds.Build]] = []
        for member, build in ready:
            code, output = there.merge(member.tip, f"Merge branch '{build.branch}'")
            if code == 0:
                merged.append((member, build))
                continue
            conflicts = there.conflict_paths()
            if there.merging():
                there.abort()
            if conflicts:
                blocker = self.blocker(conflicts, merged, main, str(target))
                self.end_member(member, queue_file.EJECTED, reason=(
                    f"conflict with {blocker}: once that has landed or left, merge "
                    f"{target} into the branch, run moxi rounds check, make any drift "
                    "re-check owed, and finish again"), blocker=blocker)
            else:
                self.end_member(member, queue_file.EJECTED,
                                reason="a commit hook refused its merge", output=output)
        if not merged:
            self.drop_branch(branch, None)
            return
        candidate = there.head()
        queue_file.append(self.repo, {
            "event": queue_file.BATCH, "state": queue_file.BUILT, "id": batch_id, "n": n,
            "main": main, "branch": branch, "candidate": candidate,
            "members": [{"round": one.name, "stage": one.stage, "tip": one.tip}
                        for one, _build in merged],
            **({} if source is None else {"source": list(source)})}, self.env)
        for member, _build in merged:
            self.mirror(member.name, member.stage, queue_log.BATCHED,
                        {"tip": member.tip, "batch": batch_id, "main": main,
                         "candidate": candidate})
        self.say(f"lander of {self.repo}: batch {batch_id} built with "
                 f"{len(merged)} member(s)")
        batch = queue_file.read(self.repo, self.env).batch(batch_id)
        if batch is not None:
            self.check(batch)

    def ready_place(self, branch: str, main: str) -> None:
        """The lander's worktree, made when missing, on a new branch `branch` at
        `main`, holding nothing else."""
        git = QueueGit(self.root)
        if places.worktree_git_dir(self.place) is None:
            git.run_git("worktree", "prune")
            self.place.parent.mkdir(parents=True, exist_ok=True)
            git.run_git("worktree", "add", "--quiet", "--detach", str(self.place), main)
        there = QueueGit(self.place)
        if there.merging():
            there.abort()
        there.run_git("checkout", "--quiet", "--force", "-B", branch, main)
        there.run_git("clean", "-fdq")

    def blocker(self, conflicts: frozenset[str], merged, main: str, target: str) -> str:
        """The member merged before whose change a conflict on `conflicts` meets, by its
        round and stage; the target when none changed those paths."""
        git = QueueGit(self.root)
        for member, _build in reversed(merged):
            if git.changed_paths(main, member.tip) & conflicts:
                return f"{member.name} {member.stage}"
        return target

    # -- check ---------------------------------------------------------------------------

    def check(self, batch: queue_file.Batch) -> None:
        ref = (batch.branch if self.ref_template is None
               else ci.ref_for(self.ref_template, batch.branch))
        pushed = self.ci.push(self.place, str(batch.candidate), ref)
        if pushed.code != 0:
            named = [one for one in batch.members if self.names(pushed.said, one, batch)]
            for name, stage, tip in named:
                member = queue_file.read(self.repo, self.env).member(name, stage)
                if member is not None:
                    self.end_member(member, queue_file.EJECTED, reason=SCAN_WHY,
                                    output=pushed.said, batch=batch.id)
            first = (pushed.said.splitlines() or ["no output"])[0]
            if named:
                self.end_batch(batch, SCAN_WHY)
            elif any(one.get("kind") == PUSH_FAILED for one in batch.runs):
                self.pause(f"the push of {ref} was refused twice: {first}", batch)
            else:
                self.run_event(batch, PUSH_FAILED, said=pushed.said)
            return
        self.run_event(batch, PUSHED, ref=ref, pushed_s=self.clock())
        self.run_local(batch, str(batch.candidate))

    def run_local(self, batch: queue_file.Batch, commit: str, *,
                  head: bool = False) -> dict | None:
        """Run `ci.local` at `commit` in the lander's worktree and record it: as the
        batch's, or, with `head`, as the target's at `commit`. The event; None when
        there is no local run."""
        if QueueGit(self.place).head() != commit:
            QueueGit(self.place).run_git("checkout", "--quiet", "--force", "--detach", commit)
        target = self.target_of(batch) or ""
        values = {"round": f"queue/{self.repo}", "stage": batch.id, "repo": self.repo,
                  "root": str(self.root), "branch": target if head else batch.branch,
                  "tip": commit, "into": target, "base": batch.main}
        local = self.ci.local(self.place, values)
        if local is None:
            return None
        fields = {"kind": LOCAL, "argv": list(local.argv), "exit_code": local.exit_code,
                  "duration_s": local.duration_s, "output": local.output}
        if head:
            return queue_file.append(self.repo, {"event": queue_file.RUN, "head": commit,
                                                 **fields}, self.env)
        self.run_event(batch, LOCAL, **{key: value for key, value in fields.items()
                                        if key != "kind"})
        return batch.run

    def names(self, said: str, member: tuple[str, str, str],
              batch: queue_file.Batch) -> bool:
        """Whether a refusal's words name the member: its joined tip, or a path its
        change touches."""
        _name, _stage, tip = member
        if tip[:12] in said:
            return True
        return any(path in said
                   for path in QueueGit(self.root).changed_paths(batch.main, tip))

    # -- read ----------------------------------------------------------------------------

    def advance(self, queue: queue_file.Queue, batch: queue_file.Batch) -> None:
        git = QueueGit(self.root)
        if batch.candidate is None:
            self.end_batch(batch, "no candidate was recorded")
            return
        target = self.target_of(batch)
        head = None if target is None else git.branch(target)
        if head is not None and head != batch.main and git.present([batch.candidate]) \
                and git.is_ancestor(batch.candidate, head):
            self.land(batch, target=str(target), resumed=True)
            return
        if not any(one.get("kind") == PUSHED for one in batch.runs):
            self.check(batch)
            return
        try:
            read = self.ci.read(self.place, batch.candidate)
        except ci.ReadFailed as exc:
            self.read_failed(batch, exc)
            return
        last = next((one for one in reversed(batch.runs) if one.get("kind") == READ), None)
        if last is None or last.get("reading") != read.word:
            self.run_event(batch, READ, reading=read.word, runs=list(read.runs),
                           workflows=[dict(one) for one in read.workflows],
                           status_argv=list(read.status_argv),
                           ids_argv=list(read.ids_argv))
        local = next((one for one in reversed(batch.runs) if one.get("kind") == LOCAL),
                     None)
        word = read.word
        if word == ci.PENDING:
            return
        if word == queue_ci.CANCELLED:
            if any(one.get("kind") == RERUN and one.get("why") == CANCELLED_WHY
                   for one in batch.runs):
                self.pause("the candidate's run was cancelled twice", batch)
                return
            self.ci.rerun(self.place, read.runs, failed=False)
            self.run_event(batch, RERUN, why=CANCELLED_WHY, runs=list(read.runs))
            return
        if word == ci.ABSENT:
            pushed = next(one for one in batch.runs if one.get("kind") == PUSHED)
            if self.clock() - float(pushed.get("pushed_s") or 0) < ABSENT_S:
                return
            ended = [one for one in queue.batches if one.ended]
            if ended and ended[-1].why == ABSENT_WHY:
                self.pause("the candidate's run was absent twice", batch)
                return
            self.end_batch(batch, ABSENT_WHY)
            return
        local_red = local is not None and local.get("exit_code") != 0
        if target is None:
            return
        if word == ci.GREEN and not local_red:
            if not self.behind(batch):
                self.land(batch, target=target)
            return
        try:
            self.red(batch, read, target=target, local_red=local_red)
        except ci.ReadFailed as exc:
            self.read_failed(batch, exc)

    def read_failed(self, batch: queue_file.Batch, exc: ci.ReadFailed) -> None:
        """Record a failed read of the batch's CI; the queue pauses once
        `READ_FAILS_MAX` fail in a row."""
        failed = 0
        for one in reversed(batch.runs):
            if one.get("kind") != READ_FAILED:
                break
            failed += 1
        if failed + 1 >= READ_FAILS_MAX:
            self.pause(f"CI could not be read {READ_FAILS_MAX} times in a row: {exc}",
                       batch)
        else:
            self.run_event(batch, READ_FAILED, said=str(exc))

    def behind(self, batch: queue_file.Batch) -> bool:
        """Whether an older batch still runs, so this one may not land yet."""
        return any(one.n < batch.n
                   for one in queue_file.read(self.repo, self.env).running_batches())

    def target_of(self, batch: queue_file.Batch) -> str | None:
        for name, stage, _tip in batch.members:
            try:
                view = write.look(_where(name, self.settings), env=self.env)
            except RoundsError:
                continue
            build = builds.current(view.state, stage)
            if build is not None:
                return build.target
        return None

    # -- red -----------------------------------------------------------------------------

    def red(self, batch: queue_file.Batch, read: queue_ci.Read, *, target: str,
            local_red: bool) -> None:
        """The next step for a red candidate: rerun it once, list its failures, compare
        them with the target's at `M`, and land it, eject its member, or split it."""
        ci_red = read.word == ci.RED
        rerun = _newest(batch.runs, RERUN, why=RED_WHY)
        if rerun is None:
            said = self.ci.rerun(self.place, read.runs, failed=True) if ci_red else None
            if local_red:
                self.run_local(batch, str(batch.candidate))
            self.run_event(batch, RERUN, why=RED_WHY, runs=list(read.runs) if ci_red else [],
                           local=local_red, at_s=self.clock(),
                           code=None if said is None else said.code,
                           said=None if said is None else said.said)
            return
        if not self.rerun_over(batch.runs, rerun):
            return
        found = _newest(batch.runs, FAILURES)
        if found is None:
            failures = (self.ci.failures(self.place, str(batch.candidate), read)
                        if ci_red else [])
            if local_red:
                failures.append({"job": LOCAL_JOB, "test": None})
            self.run_event(batch, FAILURES, failures=failures, runs=list(read.runs))
        else:
            failures = list(found.get("failures") or [])
        own = self.target_failures(batch, local=local_red)
        if own is None:
            if QueueGit(self.root).branch(target) not in (None, batch.main):
                self.end_batch(batch, MOVED_WHY, regroups=self.regroups_of(batch))
            else:
                self.wait(f"the full run of {target} at {batch.main[:12]}", batch)
            return
        new = failing.new(failures, own)
        if not new:
            if _newest(batch.runs, EXCUSED) is None:
                self.run_event(batch, EXCUSED, failures=failures)
            if not self.behind(batch):
                self.land(batch, target=target)
            return
        queue = queue_file.read(self.repo, self.env)
        members = [one for one in (queue.member(name, stage)
                                   for name, stage, _tip in batch.members)
                   if one is not None and one.state == queue_file.IN_BATCH
                   and one.batch == batch.id]
        if len(members) < 2:
            for member in members:
                self.end_member(member, queue_file.EJECTED,
                                reason=f"CI red: {failing.said(new)}", batch=batch.id,
                                run=list(read.runs), failures=new)
            self.end_batch(batch, RED_WHY)
            return
        first, second = failing.halves(members)
        self.say(f"lander of {self.repo}: batch {batch.id} has new failures "
                 f"({failing.said(new)}): split in two")
        self.end_batch(batch, SPLIT_WHY, regroups=[
            [[one.name, one.stage, one.tip] for one in half] for half in (first, second)])

    def rerun_over(self, runs: list[dict], rerun: dict) -> bool:
        """Whether the rerun `rerun` has been read past: it reran nothing, it was
        refused, a read after it found the run going again, or `ABSENT_S` passed."""
        if not rerun.get("runs") or rerun.get("code") not in (None, 0):
            return True
        after = runs[next(index for index, one in enumerate(runs) if one is rerun) + 1:]
        if any(one.get("kind") == READ and one.get("reading") == ci.PENDING
               for one in after):
            return True
        return self.clock() - float(rerun.get("at_s") or 0) >= ABSENT_S

    def target_failures(self, batch: queue_file.Batch, *, local: bool) -> list[dict] | None:
        """The target's failures at the batch's `M`: those of its full run there, read
        once it has ended and, when red, after one rerun of its failed jobs, and, with
        `local`, `local` when `ci.local` fails at `M`. None until all are known."""
        head = batch.main
        queue = queue_file.read(self.repo, self.env)
        known = queue.main_at(head)
        if known is None:
            read = self.ci.read(self.place, head)
            runs = [one for one in queue.head_runs if one.get("head") == head]
            last = _newest(runs, READ)
            if last is None or last.get("reading") != read.word:
                runs.append(queue_file.append(self.repo, {
                    "event": queue_file.RUN, "head": head, "kind": READ,
                    "reading": read.word, "runs": list(read.runs)}, self.env))
            if read.word == ci.GREEN:
                known = queue_file.append(self.repo, {
                    "event": queue_file.GREEN, "head": head, "run": list(read.runs)},
                    self.env)
            elif read.word == ci.RED:
                rerun = _newest(runs, RERUN)
                if rerun is None:
                    said = self.ci.rerun(self.place, read.runs, failed=True)
                    queue_file.append(self.repo, {
                        "event": queue_file.RUN, "head": head, "kind": RERUN,
                        "runs": list(read.runs), "at_s": self.clock(), "code": said.code,
                        "said": said.said}, self.env)
                    return None
                if not self.rerun_over(runs, rerun):
                    return None
                known = queue_file.append(self.repo, {
                    "event": queue_file.RED, "head": head, "run": list(read.runs),
                    "failures": self.ci.failures(self.place, head, read)}, self.env)
            else:
                return None
        own = list(known.get("failures") or [])
        if local:
            ran = _newest([one for one in queue_file.read(self.repo, self.env).head_runs
                           if one.get("head") == head], LOCAL)
            if ran is None:
                ran = self.run_local(batch, head, head=True)
            if ran is not None and ran.get("exit_code") != 0:
                own.append({"job": LOCAL_JOB, "test": None})
        return own

    def regroups_of(self, batch: queue_file.Batch) -> list | None:
        """For a half of a split, its members still in it, as the one group to rebuild;
        None for a batch that is no half, or a half no member is still in."""
        if batch.source is None:
            return None
        queue = queue_file.read(self.repo, self.env)
        group = [[name, stage, tip] for name, stage, tip in batch.members
                 if (one := queue.member(name, stage)) is not None
                 and one.state == queue_file.IN_BATCH and one.batch == batch.id]
        return [group] if group else None

    def wait(self, reason: str, batch: queue_file.Batch) -> None:
        """Record that the lander waits on `reason`, once while it lasts."""
        if (queue_file.read(self.repo, self.env).waiting_on or {}).get("reason") != reason:
            queue_file.append(self.repo, {"event": queue_file.WAITING, "reason": reason,
                                          "batch": batch.id}, self.env)

    # -- land ----------------------------------------------------------------------------

    def land(self, batch: queue_file.Batch, *, target: str, resumed: bool = False) -> None:
        try:
            taken = finish_lock.take(self.root,
                                     finish_lock.ref_of(self.settings, self.repo, target),
                                     by=LANDER, name=f"queue/{self.repo}", stage=batch.id)
        except Refused as exc:
            self.say(f"lander of {self.repo}: the finish lock is held: {exc}")
            return
        landed: list[tuple[str, str, str, str | None]] = []
        try:
            if not resumed and not self.reread(batch, target):
                return
            if not resumed and not self.fast_forward(batch, target):
                return
            queue = queue_file.read(self.repo, self.env)
            for name, stage, tip in batch.members:
                member = queue.member(name, stage)
                if member is None or member.state != queue_file.IN_BATCH:
                    continue
                self.write_check_records(batch, name, stage, tip)
                try:
                    finishing.finish_member(name, stage, tip=tip, by=LANDER,
                                            settings=self.settings, env=self.env)
                except RoundsError as exc:
                    self.say(f"lander of {self.repo}: {name} {stage} landed and is not "
                             f"finished: {exc}")
                landed.append((name, stage, tip, QueueGit(self.root).bringing_merge(
                    tip, str(batch.candidate))))
        finally:
            finish_lock.release(taken)
        queue = queue_file.read(self.repo, self.env)
        excused = (_newest(batch.runs, EXCUSED) or {}).get("failures") or None
        for name, stage, tip, landing in landed:
            member = queue.member(name, stage)
            if member is not None:
                self.end_member(member, queue_file.LANDED, batch=batch.id, landing=landing,
                                excused=excused)
        self.end_batch(batch, LANDED_WHY)
        self.say(f"lander of {self.repo}: batch {batch.id} landed at "
                 f"{str(batch.candidate)[:12]}")

    def reread(self, batch: queue_file.Batch, target: str) -> bool:
        """The landing re-read under the finish lock: whether the batch may land now.
        When it may not, what was found is recorded and the batch is ended or left to
        wait."""
        git = QueueGit(self.root)
        if git.branch(target) != batch.main:
            self.end_batch(batch, MOVED_WHY, regroups=self.regroups_of(batch))
            return False
        queue = queue_file.read(self.repo, self.env)
        failed = []
        for name, stage, tip in batch.members:
            member = queue.member(name, stage)
            if (member is None or member.state != queue_file.IN_BATCH
                    or member.batch != batch.id or member.tip != tip):
                failed.append(f"{name} {stage}")
                continue
            why = self.gate_problem(member)
            if why is not None:
                self.end_member(member, queue_file.LEFT, reason=why, batch=batch.id)
                failed.append(f"{name} {stage}")
        if failed:
            self.end_batch(batch, "rebuilt without " + ", ".join(failed),
                           regroups=self.regroups_of(batch))
            return False
        checkout = finishing.MergeGit(self.root).checkout_of(target)
        if checkout is not None:
            there = QueueGit(checkout)
            if there.has_changes() or there.head() != batch.main:
                reason = f"{places.contract_home(str(checkout))} not clean"
                if (queue.waiting_on or {}).get("reason") != reason:
                    queue_file.append(self.repo, {"event": queue_file.WAITING,
                                                  "reason": reason, "batch": batch.id},
                                      self.env)
                return False
        return True

    def gate_problem(self, member: queue_file.Member) -> str | None:
        """Why the member may not land now, in words: its round or build ended, its
        branch moved, or a finish gate the re-read reads does not hold at its joined
        tip. None when it may."""
        try:
            where, _build = self.read_member(member)
        except _MemberGone as exc:
            return exc.why
        view = write.look(where, env=self.env)
        tips = {member.stage: member.tip}
        seen = finishing.seen_now(view, member.name, member.stage, self.settings,
                                  refuse=False, tips=tips)
        if isinstance(seen, Unmet):
            return seen.reason
        for gate in finishing.gates_now(view, member.name, member.stage, seen, member.tip,
                                        self.settings):
            if gate.number not in NOT_REREAD:
                return f"gate {gate.number}: {gate.unmet.reason}"
        return None

    def fast_forward(self, batch: queue_file.Batch, target: str) -> bool:
        """Move the target to the candidate by `git merge --ff-only` in the checkout that
        has it, or in a checkout added for it at finish's own place and removed after."""
        git = finishing.MergeGit(self.root)
        checkout = git.checkout_of(target)
        added = checkout is None
        if added:
            checkout = (places.worktree_dir(self.settings) / self.repo
                        / f"{finishing.CHECKOUT_PREFIX}{places.encode(target)}")
            git.add_checkout(checkout, target)
        try:
            code, said = QueueGit(checkout).run_git("merge", "--ff-only", "--quiet",
                                                    str(batch.candidate), ok=(0, 1, 128))
        finally:
            if added:
                removal.checkout_removal(self.root, checkout, settings=self.settings,
                                         repo=self.repo)
        if code != 0:
            queue_file.append(self.repo, {"event": queue_file.WAITING, "batch": batch.id,
                                          "reason": f"the fast-forward failed: {said}"},
                              self.env)
            return False
        return True

    def write_check_records(self, batch: queue_file.Batch, name: str, stage: str,
                            tip: str) -> None:
        """The member's check records at its joined tip, naming the candidate, once."""
        read = next((one for one in reversed(batch.runs) if one.get("kind") == READ), {})
        local = next((one for one in reversed(batch.runs) if one.get("kind") == LOCAL),
                     None)
        bases = QueueGit(self.root).merge_bases(tip, batch.main)
        merge_base = bases[0] if len(bases) == 1 else None
        same = {"stage": stage, "commit": tip, "merge_base": merge_base,
                "candidate": batch.candidate, "batch": batch.id}
        entries = [write.Entry(ci.READ, {
            **same, "run": read.get("runs") or [], "reading": read.get("reading"),
            "excused": (_newest(batch.runs, EXCUSED) or {}).get("failures") or [],
            "workflows": read.get("workflows") or [],
            "status_argv": read.get("status_argv") or [],
            "ids_argv": read.get("ids_argv") or []})]
        if local is not None:
            entries.append(write.Entry(checking.RAN, {
                **same, "step": ci.LOCAL, "argv": checking.recorded_argv(local["argv"]),
                "exit_code": local.get("exit_code"),
                "duration_s": local.get("duration_s"),
                "output": places.contract_home(str(local.get("output") or ""))}))

        def event(view, _result):
            done = ci.newest(view.state, stage, tip)
            if done is not None and done.get("candidate") == batch.candidate:
                return None
            return entries

        write.record(_where(name, self.settings), by=LANDER, event=event, env=self.env)

    # -- records -------------------------------------------------------------------------

    def run_event(self, batch: queue_file.Batch, kind: str, **fields) -> None:
        queue_file.append(self.repo, {"event": queue_file.RUN, "batch": batch.id,
                                      "kind": kind, **fields}, self.env)
        refreshed = queue_file.read(self.repo, self.env).batch(batch.id)
        if refreshed is not None:
            batch.runs[:] = refreshed.runs
            batch.run = refreshed.run

    def end_member(self, member: queue_file.Member, kind: str, **fields) -> None:
        """Record the member's end in the queue file and mirror it into its round's
        log."""
        fields = {key: value for key, value in fields.items() if value is not None}
        queue_file.append(self.repo, {"event": kind, "round": member.name,
                                      "stage": member.stage, "tip": member.tip,
                                      "by": LANDER, **fields}, self.env)
        mirrored = {queue_file.LEFT: queue_log.LEFT, queue_file.EJECTED: queue_log.EJECTED,
                    queue_file.LANDED: queue_log.LANDED}[kind]
        allowed = {queue_log.LEFT: ("reason", "batch"),
                   queue_log.EJECTED: ("batch", "reason", "blocker", "run", "failures",
                                       "output"),
                   queue_log.LANDED: ("batch", "landing", "excused")}[mirrored]
        self.mirror(member.name, member.stage, mirrored,
                    {"tip": member.tip, **{key: fields[key] for key in allowed
                                           if key in fields}}
                    | ({"by": LANDER} if mirrored == queue_log.LEFT else {}))
        self.say(f"lander of {self.repo}: {member.name} {member.stage} {kind}"
                 + (f": {fields['reason']}" if fields.get("reason") else ""))

    def mirror(self, name: str, stage: str, kind: str, fields: dict) -> None:
        try:
            write.record(_where(name, self.settings), by=LANDER,
                         event=lambda _view, _result: write.Entry(
                             kind, {"stage": stage, **fields}), env=self.env)
        except RoundsError as exc:
            self.say(f"lander of {self.repo}: {kind} of {name} {stage} is not in its "
                     f"round's log: {exc}")

    def end_batch(self, batch: queue_file.Batch, why: str, *,
                  regroups: list | None = None) -> None:
        """End the batch, naming the groups of its members to build as batches of their
        own (`regroups`), and remove its ref and branch."""
        queue_file.append(self.repo, {"event": queue_file.BATCH, "state": queue_file.ENDED,
                                      "id": batch.id, "why": why,
                                      **({"regroups": regroups} if regroups else {})},
                          self.env)
        ref = (batch.branch if self.ref_template is None
               else ci.ref_for(self.ref_template, batch.branch))
        self.drop_branch(batch.branch, ref if any(
            one.get("kind") == PUSHED for one in batch.runs) else None)

    def pause(self, reason: str, batch: queue_file.Batch | None) -> None:
        queue_file.append(self.repo, {"event": queue_file.PAUSED, "reason": reason},
                          self.env)
        if batch is not None:
            self.end_batch(batch, PAUSED_WHY)
        self.say(f"lander of {self.repo}: the queue is paused: {reason}")

    def drop_branch(self, branch: str, ref: str | None) -> None:
        """Remove a batch's ref from the remote, when it was pushed, and its local
        branch."""
        if ref is not None and self.ci is not None:
            self.ci.remove(self.place, ref)
        try:
            if places.worktree_git_dir(self.place) is not None:
                QueueGit(self.place).run_git("checkout", "--quiet", "--detach")
            QueueGit(self.root).run_git("branch", "-D", "--quiet", branch, ok=(0, 1))
        except GitError as exc:
            self.say(f"lander of {self.repo}: {branch} is not deleted: {exc}")


def _newest(runs: list[dict], kind: str, **fields) -> dict | None:
    """The newest of `runs` of `kind` whose `fields` match."""
    return next((one for one in reversed(runs) if one.get("kind") == kind
                 and all(one.get(key) == value for key, value in fields.items())), None)


def _where(name: str, settings) -> write.Where:
    project, slug = folders.split(name)
    home = folders.home(project, settings)
    return write.Where(home.project, home.rounds_dir, slug)


class QueueGit(finishing.MergeGit):
    """The git calls of the lander, in the repo or worktree at `root`."""

    def run_git(self, *args: str, ok: tuple[int, ...] = (0,)) -> tuple[int, str]:
        return self._run(*args, ok=ok)

    def conflict_paths(self) -> frozenset[str]:
        """The paths a stopped merge left unmerged."""
        _code, out = self._run("diff", "--name-only", "--diff-filter=U")
        return frozenset(line for line in out.splitlines() if line.strip())


__all__ = ["LANDER", "QUEUE_PLACE", "BRANCH_PREFIX", "ABSENT_S", "LOCAL_JOB", "NOT_REREAD",
           "READ_FAILS_MAX", "place_of", "take", "step", "QueueGit"]
