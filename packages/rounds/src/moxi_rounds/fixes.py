"""The lead's mark of a finding fixed: `mark ROUND B2.L3 --fixed --commit SHA...
--no-behaviour-change`.

`fix(round, key, commits, no_behaviour_change=..., actor=...)` appends one
`finding.marked` event: the finding's key (`B2.L3`), its new state, `fixed`, and the
whole id of every commit of the fix. It sets the finding's state as a report would, and
the commits it names count as covered when the build's verification is read at a tip
(`findings.why_not_counted`), so a small fix after the verdict needs no re-check.

It is refused:

- for a finding that is a `blocker` or `major` as its newest listing gives it: such a
  fix gets a re-check of its diff;
- without the lead's statement that the fix changes no behaviour, that it changes
  nothing a command, hook or page reads, decides, writes, refuses or accepts: a fix
  that does gets a re-check of its diff;
- for a finding the build's record does not hold, or a stage with no open build;
- for a commit git does not hold, or one that is not a commit of the build: on its
  branch, or under its head where the branch is gone, and not under the base it was
  cut from.

A mark takes no finding away without a fix: only a report sets `no-action`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from . import builds, ending, findings, folders, open_builds, report_block, write
from . import settings as own
from . import state as states
from .errors import BadArgument, NoLog, NoRound, Refused, RoundsError, Unmet
from .gitreads import Git

#: A finding's key outside its report.
KEY = re.compile(r"(B[1-9][0-9]*)\.(L[1-9][0-9]*)")
#: The severities a mark may set fixed.
MARKABLE = (report_block.MINOR, report_block.NOTE)


@dataclass(frozen=True)
class Fixed:
    """A finding after the lead's mark: the round's `name`, the `finding` as the log now
    gives it, and the `event` written. `failed` pairs each after-append callback that
    raised with what it raised."""

    name: str
    finding: findings.Finding
    event: dict
    failed: tuple[tuple[Callable, Exception], ...] = ()


def fix(target: str, key: str, commits: Sequence[str], *, no_behaviour_change: bool,
        by: str, settings=None, wait_s: float = write.WAIT_S,
        at: str | None = None) -> Fixed:
    """Mark the finding `key`, `B<n>.L<k>`, of the round `target` fixed by `commits`.

    Raises `BadArgument` for a key or a commit that can't be one, or no commit;
    `UnknownProject`, `NoFolder` or `NoRound` for a round that can't be found; and
    `Refused` for a rule above. A refusal leaves the log as it was."""
    matched = KEY.fullmatch(key or "")
    if matched is None:
        raise BadArgument(f"{key!r} is not the key of a build's finding",
                          "name the finding as B<n>.L<k>, as show lists it")
    stage, finding_id = matched.groups()
    named = [str(one).strip() for one in commits]
    if not named:
        raise BadArgument("the mark names no commit of the fix",
                          "name every commit of the fix, each with --commit SHA")
    if any(not re.fullmatch(r"[0-9a-fA-F]{4,64}", one) for one in named):
        raise BadArgument(f"{', '.join(named)}: a commit is named by its hex id",
                          "give each commit's id, as git log prints it")
    project, slug = folders.split(target)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    shown = f"moxi rounds show {name}"
    recheck = ("have a checker re-check the fix's diff at the verify-build step's "
               "re-check tier, and record the report with moxi rounds verdict")

    def build_of(state: states.State) -> builds.Build | None:
        build = builds.current(state, stage)
        return None if build is None or build.ended is not None else build

    def held(view):
        build = build_of(view.state)
        if build is None:
            return Unmet(f"{stage} of {name} has no open build, so it has no finding "
                         "to mark", shown)
        finding = next((one for one in findings.of_build(view.state, build)
                        if one.id == finding_id), None)
        if finding is None:
            return Unmet(f"{stage}'s build holds no finding {finding_id}", shown)
        if finding.severity not in MARKABLE:
            return Unmet(
                f"{key} is a {finding.severity} finding: the lead marks only a minor or "
                "a note fixed without a re-check", recheck)
        return None

    def stated(view):
        if no_behaviour_change:
            return None
        return Unmet("the mark does not state that the fix changes no behaviour: a fix "
                     "that changes what a command, hook or page reads, decides, "
                     "writes, refuses or accepts gets a re-check of its diff",
                     f"{recheck}; or, when it changes none of that, pass "
                     "--no-behaviour-change")

    def marked(view, result):
        build = build_of(view.state)
        return write.Entry(findings.MARKED, {
            "id": key, "state": report_block.FIXED,
            "commits": _resolved(name, build, named, settings=settings)})

    try:
        written = write.record(
            write.Where(home.project, home.rounds_dir, slug), by=by,
            gates=[ending.takes_new_work(name, "a mark"), held, stated],
            event=marked, wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    after = states.fold(written.round_events)
    build = builds.current(after, stage)
    finding = next(one for one in findings.of_build(after, build) if one.id == finding_id)
    return Fixed(name=name, finding=finding, event=written.events[0],
                 failed=written.failed)


def _resolved(name: str, build: builds.Build, named: list[str], *,
              settings) -> list[str]:
    """The whole id of each commit named, each once; refused for one that git does not
    hold or that is not a commit of the build."""
    shown = f"moxi rounds show {name}"
    try:
        _key, root = folders.root(build.repo)
        git = Git(root)
        head = git.branch(build.branch)
        if head is None:
            now = open_builds.of_round(name, [build.repo], settings=settings).get(
                build.stage)
            head = None if now is None or now.status is None else now.status.head
        whole = [git.commit_of(one) for one in named]
        unmet = [Unmet(f"{one} is no commit {build.repo} holds",
                       "name each commit of the fix by its id")
                 for one, found in zip(named, whole, strict=True) if found is None]
        for one, found in zip(named, whole, strict=True):
            if found is None:
                continue
            if head is None or not git.is_ancestor(found, head) or (
                    git.present([build.base]) and git.is_ancestor(found, build.base)):
                unmet.append(Unmet(
                    f"{one} is not a commit of {build.stage}: it is not on "
                    f"{build.branch} above the base the build was cut from",
                    "name the commits of the fix, made on the build's branch"))
    except RoundsError as exc:
        unmet = [Unmet(exc.reason, exc.fix or shown)]
        whole = []
    if unmet:
        raise Refused(unmet)
    return list(dict.fromkeys(whole))


__all__ = ["KEY", "MARKABLE", "Fixed", "fix"]
