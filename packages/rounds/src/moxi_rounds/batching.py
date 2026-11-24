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
`ci.local` runs at the candidate; a failure there counts as a red job `local`. A batch
pushed with no local run recorded (a lander killed during it) has it run at the next
pass, before its CI is read, so nothing lands without it. A repo with no `ci` setting
has no CI to read a candidate by: the queue pauses, saying so.

**Read** CI each pass until it reads `green`, `red`, `cancelled`, or `absent` for
`ABSENT_S` after the push. A read that would be red or cancelled while a run it names
has not completed reads `pending` (`queue_ci.Read.running`): nothing is rerun, no failure
log is read and no failed read counted until every run it names has ended, and its
`read` event names the runs still running. A read green on the jobs it reads stands at
once, while other jobs of the run still go. Rounds sets no limit on a running run; the
CI's own job limits end one. A cancelled run is rerun once; an absent one ends the batch,
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
each member is recorded `landed` at its own merge commit, and the batch ends. Every
member the candidate holds lands at the tip it had there, whatever its queue state
reads: one that left, or joined again at a new tip, after the fast-forward has its
`landed` name `left_late`. One that joined again stays queued at its new tip, and its
build, finished at the old one, has the next pass record it `left`.

A candidate found on the target already (a lander killed after the fast-forward) is
landed from its member finishes on, with no re-read.

**Red** (`red`): a candidate whose CI reads red, or whose `ci.local` failed, has its
failed jobs rerun once (`ci.local` run again for `local`); green then lands. Still red,
its failures are listed (`failures.of_run`, the local job as `local`) and compared with
the target's own at `M` (`target_failures`), each with the job of the target's run it
stands for (`failures.is_own`): the target's full run at `M`, read once it
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
batch runs and no group waits to be built. While the queue is paused, a group with no
urgent member is not built and keeps no urgent member waiting.

When a batch ends, its ref is removed from the remote and its local branch deleted,
and each member still in it, back to waiting in its place, gets `queue.requeued` in its
round's log with the batch and why it ended.

**Telling** (`telling`, through comms where it is installed): a member's end (landed,
ejected, left) goes to its round's lead (`leads.of`: the round's own, else the
project's), and to its builder, the name its commission recorded, only while comms reads
that name held by a running session; with no lead recorded, that is recorded. A notice
that reached no name (no lead, the builder not held, each send refused, failed or, once
it is no longer tried, unsent) goes to the project's director once, naming who it could
not reach and why. When a member
ends, each member ejected for a conflict with it is told too, with what to do now: after
a landing, merge the target and finish again; after an ejection or a leave, finish again
as it is. A pause, and a
wait on the target's checkout, go to the project's director (`director`), and "no
director set" is recorded where none is. Each message is recorded once, as `told`, and
one comms could not take is tried again at each pass for `telling.UNSENT_S`.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable
from pathlib import Path

from . import (builds, checking, ci, finish_gates, finish_lock, finishing, folders,
               leads, places, queue_ci, queue_file, queue_log, removal, telling, write)
from . import failures as failing
from . import settings as own
from . import state as states
from .errors import Refused, RoundsError, UnfencedTest, Unmet
from .gitreads import GitError

#: Who the lander's records name.
LANDER = "rounds"
#: The lander's worktree's folder name in `<worktree_dir>/<repo>`.
QUEUE_PLACE = ".queue"
#: The start of a candidate's local branch name.
BRANCH_PREFIX = queue_file.BRANCH_PREFIX
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
PUSHED, LOCAL, READ, RERUN = "pushed", "local", queue_file.READ_KIND, "rerun"
PUSH_FAILED, READ_FAILED = "push failed", "read failed"
FAILURES, EXCUSED = "failures", "excused"
#: A target's head whose pushes showed no run, no longer waited for.
GAVE_UP = "gave up"
#: Why a head is given up with no push: the `ci` setting names no ref to push it to.
NO_REF_WHY = "no ref"
#: How many reads of a candidate's CI may fail in a row before the queue pauses.
READ_FAILS_MAX = 5
#: The identity state comms reads for a name a running session holds.
HELD = "held"
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
         comms=telling.LOAD, say: Callable[[str], None] = print,
         clock: Callable[[], float] = time.time) -> None:
    """One pass of the lander over `repo`'s queue. `candidate_ci` stands in for the
    candidate's CI (`queue_ci.QueueCi` from the repo's `ci` setting when left out),
    `comms` for comms' API (None for none installed; loaded when left out), and `clock`
    for the time now. A failure of rounds or git is said, and the next pass tries
    again."""
    settings = own.load() if settings is None else settings
    try:
        _Pass(repo, settings=settings, candidate_ci=candidate_ci, env=env, comms=comms,
              say=say, clock=clock).run()
    except RoundsError as exc:
        say(f"lander of {repo}: {exc}")


def _not_landed(member) -> str:
    """The tail of a leave whose member can no longer land: its commits stay on its
    branch, and the lead or builder lands them in a build of their own."""
    return (f"; its commits at {member.tip[:12]} are on its branch, not on the target: "
            "land them in a new build")


class _MemberGone(Exception):
    """A member no longer to be merged: `why` says why, `kind` is the queue event."""

    def __init__(self, why: str, kind: str = queue_file.LEFT):
        super().__init__(why)
        self.why = why
        self.kind = kind


class _Pass:
    def __init__(self, repo: str, *, settings, candidate_ci, env, comms, say, clock):
        self.repo, self.settings, self.env, self.say, self.clock = (
            repo, settings, env, say, clock)
        self.teller = telling.Teller(repo, env=env, comms=comms, clock=clock, say=say)
        _key, self.root = folders.root(repo)
        self.place = place_of(settings, repo)
        found = ci.declared(settings, repo)
        self.ci = candidate_ci
        if self.ci is None and found is not None:
            self.ci = queue_ci.QueueCi(
                found, local_log=queue_file.queue_dir(repo, env) / "local.log")
        self.ref_template = None if found is None else found.ref
        self.has_local = found is not None and found.local is not None

    # -- the pass ------------------------------------------------------------------------

    def run(self) -> None:
        for ended in self.teller.retry(reread=self.reread_owner):
            self.tell_unsent(ended)
        queue = queue_file.read(self.repo, self.env)
        if self.ci is None:
            if queue.queued() and queue.paused is None:
                self.pause(f"{self.repo} has no ci setting, so a candidate has no CI to "
                           "be read by", queue.running())
            return
        try:
            self.batches(queue)
        finally:
            self.main_afterwards()

    def batches(self, queue: queue_file.Queue) -> None:
        """Advance each running batch, build each waiting group (while paused, only its
        urgent members), and take new members when no batch ran and no group was
        built."""
        running = queue.running_batches()
        for batch in running:
            now = queue_file.read(self.repo, self.env)
            found = now.batch(batch.id)
            if found is not None and not found.ended:
                self.advance(now, found)
        built = False
        for source, members in queue_file.read(self.repo, self.env).regrouped():
            paused = queue_file.read(self.repo, self.env).paused
            members = [one for one in members if paused is None or one.urgent]
            if members:
                self.build(queue_file.read(self.repo, self.env), members, source=source)
                built = True
        if running or built:
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
            raise _MemberGone(f"its round is {view.state.status}{_not_landed(member)}")
        build = builds.current(view.state, member.stage)
        if build is None or build.ended is not None:
            raise _MemberGone(f"its build has ended{_not_landed(member)}")
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
            "main": main, "branch": branch, "candidate": candidate, "target": str(target),
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
        if self.has_local and _newest(batch.runs, LOCAL) is None:
            self.run_local(batch, str(batch.candidate))
        try:
            read = self.ci.read(self.place, batch.candidate)
        except ci.ReadFailed as exc:
            self.read_failed(batch, exc)
            return
        last = next((one for one in reversed(batch.runs) if one.get("kind") == READ), None)
        if _read_differs(last, read):
            self.run_event(batch, READ, reading=read.word, runs=list(read.runs),
                           workflows=[dict(one) for one in read.workflows],
                           status_argv=list(read.status_argv),
                           ids_argv=list(read.ids_argv), **_running(read))
            self.say_running(f"batch {batch.id}", last, read)
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

    def say_running(self, what: str, last: dict | None, read: queue_ci.Read) -> None:
        """Say in the lander's log, once as a read first finds it, that a run `what`
        names is still running, so nothing is done with it until it ends."""
        if read.running and not (last or {}).get("running"):
            self.say(f"lander of {self.repo}: {what}: run {', '.join(read.running)} is "
                     "still running; waiting for it to end")

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
        """The target's failures at the batch's `M`: those of its full run there
        (`read_head`), and, with `local`, `local` when `ci.local` fails at `M`. None until
        all are known."""
        head = batch.main
        known = self.read_head(head)
        if known is None:
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

    # -- the target's full runs ---------------------------------------------------------

    def read_head(self, head: str, *, push: bool = False) -> dict | None:
        """The target's full run at `head` as read: its `green` or `red` event, read once
        green, or once the run has ended and, when red, after one rerun of its failed
        jobs. None until then. Only the full run's workflows are read (`read_full`), and no run a
        candidate was read by (`queue_file.Queue.candidate_runs`), so a candidate's run
        at the same commit, scoped or full, never stands in for it. With `push`, a head
        with no run is pushed to the target's `ci` ref, again once when no run shows
        `ABSENT_S` after the push; then it is given up on (`GAVE_UP`) and the director
        is told. With no `ref` to push to, it is given up once it has read absent for
        `ABSENT_S`."""
        queue = queue_file.read(self.repo, self.env)
        known = queue.main_at(head)
        if known is not None:
            return known
        runs = [one for one in queue.head_runs if one.get("head") == head]
        if _newest(runs, GAVE_UP) is not None:
            return None
        read = self.ci.read_full(self.place, head, not_runs=sorted(queue.candidate_runs))
        last = _newest(runs, READ)
        if _read_differs(last, read):
            runs.append(queue_file.append(self.repo, {
                "event": queue_file.RUN, "head": head, "kind": READ,
                "reading": read.word, "runs": list(read.runs), "at_s": self.clock(),
                **_running(read)}, self.env))
            self.say_running(f"{self.target_name() or 'the target'}'s full run at "
                             f"{head[:12]}", last, read)
        if read.word == ci.GREEN:
            return self.main_read(queue_file.GREEN, head, read, [])
        if read.word == ci.RED:
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
            return self.main_read(queue_file.RED, head, read,
                                  self.ci.failures(self.place, head, read))
        if push and read.word in (ci.ABSENT, queue_ci.CANCELLED):
            self.push_head(head, runs)
        return None

    def push_head(self, head: str, runs: list[dict]) -> None:
        """Push `head` to the target's `ci` ref, forced, when it has no run: once, again
        once `ABSENT_S` after the push, then give it up. With no ref to push to, give it
        up once it has read absent for `ABSENT_S`."""
        target = self.target_name()
        if target is None:
            return
        if self.ref_template is None:
            self.give_up_unpushed(head, target, runs)
            return
        pushes = [one for one in runs if one.get("kind") == PUSHED]
        if pushes and self.clock() - float(pushes[-1].get("pushed_s") or 0) < ABSENT_S:
            return
        ref = ci.ref_for(self.ref_template, target)
        if len(pushes) >= 2:
            queue_file.append(self.repo, {"event": queue_file.RUN, "head": head,
                                          "kind": GAVE_UP, "ref": ref}, self.env)
            self.tell_director(
                "main run", f"gave up:{head}",
                f"{target}'s full run at {head[:12]} never started: {ref} was pushed "
                f"twice and no run showed {int(ABSENT_S)} s after either push. The "
                f"lander stopped waiting for it. Push {ref} by hand to run it.")
            return
        pushed = self.ci.push(self.place, head, ref)
        queue_file.append(self.repo, {"event": queue_file.RUN, "head": head,
                                      "kind": PUSHED, "ref": ref, "pushed_s": self.clock(),
                                      "code": pushed.code, "said": pushed.said}, self.env)
        self.say(f"lander of {self.repo}: pushed {head[:12]} to {ref}"
                 + ("" if pushed.code == 0 else f", refused: {pushed.said}"))

    def give_up_unpushed(self, head: str, target: str, runs: list[dict]) -> None:
        """Give up `head`, which the `ci` setting names no ref to push to, once its read
        has said `absent` for `ABSENT_S`, and tell the director once."""
        last = _newest(runs, READ)
        if last is None or last.get("reading") != ci.ABSENT \
                or self.clock() - float(last.get("at_s") or 0) < ABSENT_S:
            return
        queue_file.append(self.repo, {"event": queue_file.RUN, "head": head,
                                      "kind": GAVE_UP, "why": NO_REF_WHY}, self.env)
        self.tell_director(
            "main run", f"gave up:{head}",
            f"{target}'s full run at {head[:12]} never showed: no run of its full "
            f"workflows was there {int(ABSENT_S)} s after it was first read, and "
            f"{self.repo}'s ci setting names no ref the lander could push it to. The "
            "lander stopped waiting for it. Run the full workflows at that commit by "
            "hand to read it.")

    def target_name(self) -> str | None:
        """The target the queue lands on: the newest batch's."""
        for batch in reversed(queue_file.read(self.repo, self.env).batches):
            target = batch.target or self.target_of(batch)
            if target:
                return target
        return None

    def main_afterwards(self) -> None:
        """The target's head always gets a full run, and none is cancelled: each pushed
        head not yet read is read, oldest first, and while one has not ended nothing is
        pushed; then the head, when it has no run read, is read or pushed."""
        if self.ci is None:
            return
        target = self.target_name()
        head = None if target is None else QueueGit(self.root).branch(target)
        if head is None:
            return
        queue = queue_file.read(self.repo, self.env)
        for older in owed_heads(queue):
            if older != head and self.read_head(older) is None:
                return
        try:
            self.read_head(head, push=True)
        except ci.ReadFailed as exc:
            self.say(f"lander of {self.repo}: {target}'s run at {head[:12]} can't be "
                     f"read: {exc}")

    def main_read(self, kind: str, head: str, read: queue_ci.Read,
                  failures: list[dict]) -> dict:
        """Record the target's full run at `head` as read, `green` or `red`, with the
        landings it covers and, for red, its spell: where it started (`since`), its owner
        and the build that owns it; and tell who section 6.5 names."""
        queue = queue_file.read(self.repo, self.env)
        before = queue.mains[-1] if queue.mains else None
        covers = self.covers(queue, head)
        fields: dict = {"event": kind, "head": head, "run": list(read.runs),
                        "covers": covers}
        if kind == queue_file.RED:
            fields["failures"] = failures
            if before is not None and before.get("event") == queue_file.RED:
                fields.update({"since": before.get("since") or before.get("head"),
                               "owner": before.get("owner"),
                               "owning": before.get("owning")})
            else:
                owner, owning = self.owner_of(covers)
                fields.update({"since": head, "owner": owner, "owning": owning,
                               "starts": True})
        event = queue_file.append(self.repo, fields, self.env)
        self.tell_main(event, before)
        return event

    def covers(self, queue: queue_file.Queue, head: str) -> list[dict]:
        """The landings since the head of the full run read before: each member landed
        at a commit `head` has and that head has not, in the order they landed."""
        git = QueueGit(self.root)
        before = next((one.get("head") for one in reversed(queue.mains)
                       if one.get("head") != head and git.present([str(one.get("head"))])
                       and git.is_ancestor(str(one.get("head")), head)), None)
        found = []
        for event in queue.events:
            landing = event.get("landing")
            if event.get("event") != queue_file.LANDED or not landing \
                    or not git.present([landing]) or not git.is_ancestor(landing, head):
                continue
            if before is not None and git.is_ancestor(landing, before):
                continue
            found.append({"round": event.get("round"), "stage": event.get("stage"),
                          "landing": landing})
        return found

    def owner_of(self, covers: list[dict]) -> tuple[str | None, list | None]:
        """Main's owner for a spell that starts at a run covering `covers`: the lead of
        the earliest covered landing whose round has a lead recorded; None when none has
        (only hand commits, or no lead anywhere), and the director is told instead."""
        for one in covers:
            _builder, lead = self.people(str(one["round"]), str(one["stage"]))
            if lead is not None:
                return lead, [one["round"], one["stage"]]
        return None, None

    def tell_main(self, event: dict, before: dict | None) -> None:
        """Tell what a full run on the target means: a spell's start to every covered
        lead and its owner (or the director); a red run that differs from the one before
        to the owner; a green one after red to the owner, the spell over."""
        head = str(event["head"])
        if event["event"] == queue_file.GREEN:
            if before is not None and before.get("event") == queue_file.RED:
                self.reach_owner(before, "green", f"green:{head}",
                                 f"main is green again at {head[:12]}, run "
                                 f"{_runs(event)}: the red spell since "
                                 f"{str(before.get('since'))[:12]} is over.")
            return
        failures = failing.said(event.get("failures") or []) or "no failure listed"
        said = (f"main is red at {head[:12]}, run {_runs(event)}, after its one rerun: "
                f"{failures}.")
        owner = event.get("owner")
        if event.get("starts"):
            owns = (f"{owner} owns the fix, as the lead of the earliest landing it "
                    f"covers ({' '.join(event.get('owning') or [])})" if owner else
                    "no landing with a lead owns the fix, so the director gives it to a lead")
            for one in event.get("covers") or []:
                _builder, lead = self.people(str(one["round"]), str(one["stage"]))
                if lead is not None and lead != owner:
                    self.teller.tell(lead, "red", f"red:{head}:{lead}",
                                     f"{said} It covers your landing of {one['round']} "
                                     f"{one['stage']} at {str(one['landing'])[:12]}. {owns}.")
            self.reach_owner(event, "red", f"red:{head}",
                             f"{said} {owns}. Land the fix with moxi rounds finish "
                             "--urgent; the queue keeps landing builds that add no new "
                             "failure meanwhile.")
            return
        if sorted(map(str, event.get("failures") or [])) != sorted(
                map(str, (before or {}).get("failures") or [])):
            self.reach_owner(event, "red", f"red:{head}",
                             f"{said} Its failures differ from the run before. The red "
                             f"spell since {str(event.get('since'))[:12]} is still yours.")

    def reach_owner(self, red: dict, about: str, key: str, text: str) -> None:
        """Send `text` to the owner of the spell `red` names, and, when comms reads no
        running session for the owner (`offline`, comms not reached, included) or no
        owner is recorded, the director's notice. Both records name the spell (`spell`,
        its red run's head, and `spell_key`), so each try of either reads the owner's
        state again (`reread_owner`)."""
        owner = red.get("owner")
        state = self.identity(owner) if owner else "no owner"
        if owner:
            self.teller.tell(owner, about, f"{key}:{owner}", text, owner_state=state,
                             spell=red.get("head"), spell_key=key)
        self.tell_no_session(red, key, text, owner, state)

    def tell_no_session(self, red: dict, key: str, text: str, owner: str | None,
                        state: str) -> None:
        """The director's notice of the spell `red` names, once, when `state`, the
        owner's as comms reads it, is no running session."""
        if state == HELD or self.teller.comms is None:
            return
        director = self.director()
        self.teller.tell(director, "director", f"{key}:director:{director}",
                         self.director_notice(red, text, owner, state),
                         why_nobody="no director set", owner=owner, owner_state=state,
                         director_state=self.identity(director) if director else None,
                         spell=red.get("head"), spell_key=key)

    def reread_owner(self, record: dict, fields: dict) -> str | None:
        """Before a red spell's notice is tried again, read its owner's state again: an
        owner with no running session now gets the director told, once; a director's
        notice whose owner has one now is no longer owed, and that is why."""
        head = fields.get("spell")
        if head is None:
            return None
        to_director = record.get("about") == "director"
        owner = fields.get("owner") if to_director else record.get("to")
        if not owner:
            return None
        state = self.identity(str(owner))
        fields["owner_state"] = state
        if to_director:
            director = record.get("to")
            fields["director_state"] = self.identity(str(director)) if director else None
            return f"{owner} has a running session now" if state == HELD else None
        red = queue_file.read(self.repo, self.env).main_at(str(head))
        if red is not None:
            self.tell_no_session(red, str(fields.get("spell_key")), str(record.get("text")),
                                 str(owner), state)
        return None

    def identity(self, name: str) -> str:
        """What comms reads of `name`: one of its identity states, or `error` when the
        read raised, which reads as no running session."""
        try:
            return str(self.teller.comms.identity_state(name).state)
        except UnfencedTest:
            raise
        except Exception:  # noqa: BLE001  (a read that raises reads as no session)
            return "error"

    def director_notice(self, red: dict, text: str, owner: str | None, state: str) -> str:
        """The director's notice about a red main whose owner has no running session."""
        lines = [f"For the director of {self.repo}: {text}"]
        covers = red.get("covers") or []
        if covers:
            lines.append("The run covers: " + "; ".join(
                f"{one['round']} {one['stage']} at {str(one['landing'])[:12]} (lead "
                f"{self.people(str(one['round']), str(one['stage']))[1] or 'none'})"
                for one in covers) + ".")
        else:
            lines.append("The run covers no landing of the queue: only commits made by "
                         "hand.")
        if owner and state == telling.OFFLINE:
            lines.append(f"{owner} owns the fix, and comms could not be reached to read "
                         "whether that name has a running session.")
        elif owner:
            lines.append(f"{owner} owns the fix, and comms reads no running session for "
                         f"that name ({state}).")
            lines.append(f"Bring {owner} back (moxi launcher sessions lists its launches; "
                         "moxi launcher resume SESSION_ID --brief-file FILE brings one "
                         "back with this notice as its brief), or give the fix to another "
                         "lead.")
        else:
            lines.append("No landing with a lead owns the fix: give it to a lead.")
        lines.append("The fix lands with moxi rounds finish --urgent, at the front of the "
                     "queue; the queue keeps landing builds that add no new failure.")
        return " ".join(lines)

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
            recorded = {(one.get("round"), one.get("stage"), one.get("tip"))
                        for one in queue_file.read(self.repo, self.env).events
                        if one.get("event") == queue_file.LANDED
                        and one.get("batch") == batch.id}
            for name, stage, tip in batch.members:
                if (name, stage, tip) in recorded:
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
            if member is None:
                continue
            late = member.tip != tip or not member.waiting
            self.end_member(dataclasses.replace(member, tip=tip), queue_file.LANDED,
                            batch=batch.id, landing=landing, excused=excused,
                            left_late=late or None)
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
                    waited = queue_file.append(self.repo, {
                        "event": queue_file.WAITING, "reason": reason, "batch": batch.id},
                        self.env)
                    self.tell_director(
                        "waiting", f"waiting:{waited['at']}",
                        f"{self.repo}'s queue waits to land batch {batch.id}: {reason} "
                        f"(it must be clean and at {batch.main[:12]}). The lander tries "
                        "again each minute.")
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
                   queue_log.LANDED: ("batch", "landing", "excused", "left_late")}[mirrored]
        self.mirror(member.name, member.stage, mirrored,
                    {"tip": member.tip, **{key: fields[key] for key in allowed
                                           if key in fields}}
                    | ({"by": LANDER} if mirrored == queue_log.LEFT else {}))
        self.say(f"lander of {self.repo}: {member.name} {member.stage} {kind}"
                 + (f": {fields['reason']}" if fields.get("reason") else ""))
        self.tell_end(member, kind, fields)

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
        own (`regroups`), and remove its ref and branch. Each member still in it waits
        again, and its round's log says so."""
        with queue_file.appending(self.repo, self.env) as (queue, add):
            back = [one for one in queue.members.values()
                    if one.state == queue_file.IN_BATCH and one.batch == batch.id]
            add({"event": queue_file.BATCH, "state": queue_file.ENDED, "id": batch.id,
                 "why": why, **({"regroups": regroups} if regroups else {})})
        for member in back:
            self.mirror(member.name, member.stage, queue_log.REQUEUED,
                        {"tip": member.tip, "batch": batch.id, "why": why})
        ref = (batch.branch if self.ref_template is None
               else ci.ref_for(self.ref_template, batch.branch))
        self.drop_branch(batch.branch, ref if any(
            one.get("kind") == PUSHED for one in batch.runs) else None)

    def pause(self, reason: str, batch: queue_file.Batch | None) -> None:
        paused = queue_file.append(self.repo, {"event": queue_file.PAUSED,
                                               "reason": reason}, self.env)
        if batch is not None:
            self.end_batch(batch, PAUSED_WHY)
        self.say(f"lander of {self.repo}: the queue is paused: {reason}")
        self.tell_director(
            "paused", f"paused:{paused['at']}",
            f"{self.repo}'s queue is paused: {reason}. Only urgent members land until "
            f"it is resumed: moxi rounds queue --repo {self.repo} --resume, once that "
            "is dealt with.")

    # -- telling -------------------------------------------------------------------------

    def director(self) -> str | None:
        """The project's director, None when none is set."""
        return own.build_setting(self.settings, self.repo, "director") or None

    def tell_director(self, about: str, key: str, text: str) -> None:
        """Tell the project's director about the queue as a whole."""
        to = self.director()
        self.teller.tell(to, about, f"{key}:{to}", text, why_nobody="no director set")

    def people(self, name: str, stage: str) -> tuple[str | None, str | None]:
        """The build's builder, from its commission, and its round's lead; either None
        when none is recorded or the round can't be read."""
        try:
            project, _slug = folders.split(name)
            home = folders.home(project, self.settings)
            view = write.look(_where(name, self.settings), env=self.env)
        except RoundsError:
            return None, None
        build = builds.current(view.state, stage)
        builder = None
        if build is not None:
            builder = next((one.get("builder") for one in view.state.history
                            if one.get("n") == build.n), None)
        lead = leads.of(view.state, home.lead)
        return builder or None, None if lead is None else lead["name"]

    def tell_member(self, member: queue_file.Member, about: str, key: str,
                    text: str) -> None:
        """Tell the member's round's lead, and its builder only while comms reads that
        name held by a running session (`telling.Teller.can_receive`), each once; a
        builder passed over is neither sent nor recorded. With no lead recorded, that is
        recorded. Then the director is told when no send reached a name
        (`tell_unreached`)."""
        builder, lead = self.people(member.name, member.stage)
        passed = None
        if builder and builder != lead and not self.teller.can_receive(builder):
            passed, builder = builder, None
        for to in [one for one in dict.fromkeys((lead, builder)) if one]:
            self.teller.tell(to, about, f"{key}:{to}", text,
                             member=[member.name, member.stage])
        if lead is None:
            self.teller.tell(None, about, f"{key}:lead", text,
                             why_nobody=f"{member.name} has no lead recorded",
                             member=[member.name, member.stage])
        self.tell_unreached(key, text, passed=passed)

    def tell_unsent(self, ended: dict) -> None:
        """After the send `ended` was recorded `unsent`: when it was a send of a
        member's notice, tell the director if no send of that notice reached a name."""
        to, member = ended.get("to"), ended.get("member")
        if not to or not member or len(member) != 2:
            return
        key = str(ended.get("key")).removesuffix(f":{to}")
        builder, lead = self.people(str(member[0]), str(member[1]))
        sent = {one.get("to") for one in self.sends(key)}
        passed = builder if builder and builder != lead and builder not in sent else None
        self.tell_unreached(key, str(ended.get("text")), passed=passed)

    def sends(self, key: str) -> list[dict]:
        """The newest record of each send of the member's notice `key`."""
        return [one for one in telling.sends_of(queue_file.read(self.repo, self.env), key)
                if one.get("member")]

    def tell_unreached(self, key: str, text: str, *, passed: str | None) -> None:
        """When no send of the member's notice `key`, whose text is `text`, reached a
        name (none was a delivery, and none is still tried), tell the director once,
        under `<key>:director`, naming who was not reached and why; `passed` is the
        builder passed over, never sent to."""
        tried = self.sends(key)
        if self.teller.comms is None or not tried or any(
                one.get("delivery") not in telling.NOT_DELIVERED
                or one.get("delivery") == telling.OFFLINE for one in tried):
            return
        reasons = [f"{one.get('to') or 'no lead'}: {one.get('delivery')}"
                   + (f" ({one['why']})" if one.get("why") else "") for one in tried]
        if passed is not None:
            reasons.append(f"{passed}: not sent (comms reads no running session for it: "
                           f"{self.identity(passed)})")
        director = self.director()
        self.teller.tell(director, "director", f"{key}:director",
                         f"For the director of {self.repo}: {text} It reached nobody: "
                         + "; ".join(reasons) + ".", why_nobody="no director set")

    def tell_excused(self, member: queue_file.Member, who: str, fields: dict) -> None:
        """Tell main's owner, while a red spell holds, that a member landed past the
        target's own failures."""
        mains = queue_file.read(self.repo, self.env).mains
        red = mains[-1] if mains and mains[-1].get("event") == queue_file.RED else None
        if red is None or not red.get("owner"):
            return
        owner = str(red["owner"])
        self.teller.tell(owner, "excused", f"excused:{who}:{member.line}:{owner}",
                         f"{who} landed in {self.repo}'s queue at "
                         f"{str(fields.get('landing') or '')[:12]} past failures main has "
                         f"too, in the red spell since {str(red.get('since'))[:12]} you "
                         f"own: {failing.said(fields['excused'])}.")

    def tell_end(self, member: queue_file.Member, kind: str, fields: dict) -> None:
        """Tell a member's end, and tell each member ejected for a conflict with it
        that its blocker is out of the way."""
        who = f"{member.name} {member.stage}"
        key = f"{kind}:{who}:{member.line}"
        if kind == queue_file.LANDED:
            text = (f"{who} landed in {self.repo}'s queue at "
                    f"{str(fields.get('landing') or '')[:12]}, batch {fields.get('batch')}.")
            if fields.get("excused"):
                text += (" It landed past failures the target has too: "
                         f"{failing.said(fields['excused'])}.")
                self.tell_excused(member, who, fields)
        elif kind == queue_file.EJECTED:
            text = f"{who} was ejected from {self.repo}'s queue: {fields.get('reason')}."
        else:
            text = f"{who} left {self.repo}'s queue: {fields.get('reason')}."
        self.tell_member(member, kind, key, text)
        how = {queue_file.LANDED: "landed", queue_file.EJECTED: "was ejected",
               queue_file.LEFT: "left the queue"}[kind]
        queue = queue_file.read(self.repo, self.env)
        for blocked in queue.members.values():
            if blocked.state != queue_file.EJECTED or blocked.blocker != who:
                continue
            what = ("merge its target into the branch, run moxi rounds check, make any "
                    "drift re-check owed, and finish again"
                    if kind == queue_file.LANDED else
                    "finish again as it is: the conflict may not come back")
            self.tell_member(
                blocked, "blocker", f"blocker:{blocked.name} {blocked.stage}:{blocked.line}",
                f"{blocked.name} {blocked.stage} was ejected from {self.repo}'s queue for "
                f"a conflict with {who}, which {how}. Next: {what}.")

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


def _read_differs(last: dict | None, read: queue_ci.Read) -> bool:
    """Whether `read` is to be recorded after the read event `last`: there is none, or
    its reading or its runs still running differ."""
    return (last is None or last.get("reading") != read.word
            or list(last.get("running") or ()) != list(read.running))


def _running(read: queue_ci.Read) -> dict:
    """A read event's `running` field: the runs not completed, only when there is one."""
    return {"running": list(read.running)} if read.running else {}


def _runs(event: dict) -> str:
    return ", ".join(map(str, event.get("run") or [])) or "unknown"


def owed_heads(queue: queue_file.Queue) -> list[str]:
    """The target's heads pushed for a full run and not read yet nor given up, oldest
    first."""
    found: list[str] = []
    for one in queue.head_runs:
        head = str(one.get("head"))
        if one.get("kind") == PUSHED and head not in found:
            found.append(head)
    given_up = {str(one.get("head")) for one in queue.head_runs
                if one.get("kind") == GAVE_UP}
    return [head for head in found
            if queue.main_at(head) is None and head not in given_up]


def main_owed(repo: str, queue: queue_file.Queue) -> bool:
    """Whether the target's head has no full run read yet, nor given up on, so the
    lander stays; False before any batch was built, since no target is known."""
    target = next((one.target for one in reversed(queue.batches) if one.target), None)
    if target is None:
        return False
    try:
        _key, root = folders.root(repo)
        head = QueueGit(root).branch(target)
    except (RoundsError, GitError):
        return False
    if head is None or queue.main_at(head) is not None:
        return False
    return not any(one.get("kind") == GAVE_UP and one.get("head") == head
                   for one in queue.head_runs)


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
           "READ_FAILS_MAX", "GAVE_UP", "NO_REF_WHY", "place_of", "take", "step",
           "owed_heads", "main_owed", "QueueGit"]
