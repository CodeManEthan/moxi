"""The open builds of every project, each with its state read from git now: what the
read API's `builds` gives.

Each open build (`builds.opened`) of every round that can be read is taken from the
index, or from its log where the index is behind, and read in its repo with every
landing on record for that repo and target, in any round. Two qualifiers follow the
state: `idle`, for a `building` build whose head was committed more than its round's
`idle_days` ago, and `shares`, each other open build of the same repo and target whose
range has a commit in common with this one's. Its data says who may drop it
(`builds.drop_by`): the operator for one that could not be read.

A build that can't be read (its repo is not in the projects list, git can't read the
repo, its target branch is not there) is listed with its `problem` and no state. It
shares with nothing, since nothing of it was read. A round whose log can't be read is
named in `unreadable`, and a project whose rounds folder can't be worked out in
`no_folder`; neither is left out silently.

Nothing here writes: not a log, not the index, and git is only read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from . import builds, folders, index, log
from . import settings as own
from .errors import BadArgument, RoundsError
from .gitreads import Git

#: The fields of a build's state as data, each None for a build that could not be
#: read.
_STATUS = ("state", "by", "why", "head", "target_head", "target_before", "merge_base",
           "own_commits", "hand_merge")


@dataclass(frozen=True)
class OpenBuild:
    """One open build: `round`, the name of the round that holds it, the `build` as its
    records give it, and either its `status` as read now with `idle` and `shares` (the
    `(round, stage)` of each build it shares with, sorted), or the `problem` that kept
    it from being read."""

    round: str
    build: builds.Build
    status: builds.Status | None = None
    idle: bool = False
    shares: tuple[tuple[str, str], ...] = ()
    problem: RoundsError | None = None

    def data(self) -> dict:
        one = self.build
        found = {"round": self.round, "stage": one.stage, "repo": one.repo,
                 "branch": one.branch, "target": one.target, "base": one.base}
        found.update({name: None if self.status is None else getattr(self.status, name)
                      for name in _STATUS})
        found.update({
            "idle": self.idle,
            "drop_by": builds.OPERATOR if self.status is None
            else builds.drop_by(self.status, self.shares),
            "shares": [{"round": name, "stage": stage} for name, stage in self.shares],
            "problem": None if self.problem is None
            else {"reason": self.problem.reason, "fix": self.problem.fix}})
        return found


@dataclass(frozen=True)
class OpenBuilds:
    """The open builds, by round and then in the order commissioned: `repo` is the one
    they were kept to, or None. `unreadable` holds a line for each round whose log
    can't be read, whose builds are not here, and `no_folder` one for each project
    whose rounds folder can't be worked out."""

    computed: str
    repo: str | None
    builds: tuple[OpenBuild, ...]
    unreadable: tuple[str, ...] = ()
    no_folder: tuple[str, ...] = ()

    def data(self) -> dict:
        return {"computed": self.computed, "repo": self.repo,
                "builds": [one.data() for one in self.builds],
                "unreadable": list(self.unreadable), "no_folder": list(self.no_folder)}


def read(repo: str | None = None, *, settings=None, now: str | None = None,
         tips: Mapping[tuple[str, str], str] | None = None) -> OpenBuilds:
    """The open builds of every project, or with `repo` those that build in that
    project, each read from git now. `settings` is what `settings.load()` gives, read
    now when left out, and `now` the second idle is read at, as a log writes a time.
    `tips` holds, by `(round, stage)`, the commit to read a build at as its branch's
    tip; every other build is read at its branch's tip as it is now.

    Raises `BadArgument` for a `repo` with no name and `UnknownProject` for one the
    projects list does not hold."""
    now = log.stamp() if now is None else now
    settings = own.load() if settings is None else settings
    if repo is not None:
        if not isinstance(repo, str) or not repo.strip():
            raise BadArgument("the repo to keep the listing to has no name",
                              "name a project by its key, or leave it out")
        repo, _root = folders.root(repo)
    homes, unresolved = folders.homes(settings)
    found: list[tuple[index.Summary, builds.Build]] = []
    landed: list[builds.Landing] = []
    unreadable: list[str] = []
    for home in homes:
        for summary in index.rounds(home.project, home.folder):
            if summary.unreadable is not None:
                unreadable.append(summary.unreadable.reason)
                continue
            landed += summary.landings
            for item in summary.of(index.BUILD):
                one = builds.from_item(item.body, summary.landings)
                if repo is None or one.repo.casefold() == repo.casefold():
                    found.append((summary, one))
    groups: dict[tuple[str, str], list[tuple[index.Summary, builds.Build]]] = {}
    for summary, one in found:
        groups.setdefault((one.repo.casefold(), one.target), []).append((summary, one))
    read_now: dict[tuple[str, str], OpenBuild] = {}
    at = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    for (key, target), members in groups.items():
        here = [one for one in landed if one.repo.casefold() == key and one.target == target]
        read_now.update(_group(members, here, settings, at, tips or {}))
    return OpenBuilds(
        computed=now, repo=repo,
        builds=tuple(read_now[(summary.name, one.stage)] for summary, one in found),
        unreadable=tuple(unreadable),
        no_folder=tuple(error.reason for error in unresolved))


def _group(members: list[tuple[index.Summary, builds.Build]],
           landed: list[builds.Landing], settings, at: datetime,
           tips: Mapping[tuple[str, str], str]) -> Mapping[tuple[str, str], OpenBuild]:
    """The builds `members`, all of one repo and one target, read in that repo."""
    try:
        _key, root = folders.root(members[0][1].repo)
    except RoundsError as error:
        return {(summary.name, one.stage): OpenBuild(summary.name, one, problem=error)
                for summary, one in members}
    git = Git(root)
    done: dict[tuple[str, str], OpenBuild] = {}
    for summary, one in members:
        name = (summary.name, one.stage)
        try:
            status = builds.read(one, git, landings=landed, tip=tips.get(name))
            idle = builds.idle(
                git, status, now=at,
                idle_days=own.round_setting(settings, summary.project, "idle_days"))
        except RoundsError as error:
            done[name] = OpenBuild(summary.name, one, problem=error)
            continue
        done[name] = OpenBuild(summary.name, one, status=status, idle=idle)
    read_ok = {name: found.status for name, found in done.items() if found.problem is None}
    try:
        shared = builds.sharing(git, read_ok)
    except RoundsError as error:
        # git failed part-way through a group it had just read: nothing of the
        # group is known to be so now.
        return {name: OpenBuild(found.round, found.build, problem=error)
                for name, found in done.items()}
    return {name: found if found.problem is not None
            else OpenBuild(found.round, found.build, status=found.status,
                           idle=found.idle, shares=shared[name])
            for name, found in done.items()}


def of_round(name: str, repos, *, settings=None,
             tips: Mapping[str, str] | None = None) -> dict[str, OpenBuild]:
    """The open builds of the round `name` by stage, read as `read` reads every open
    build of each repo of `repos` (so sharing is read against every round). `tips`
    holds, by stage, the commit to read a build of this round at as its branch's tip.
    A repo that can't be read at all gives no build here; the caller says so."""
    found: dict[str, OpenBuild] = {}
    at = {(name, stage): tip for stage, tip in (tips or {}).items()}
    for repo in dict.fromkeys(repos):
        try:
            listed = read(repo, settings=settings, tips=at)
        except RoundsError:
            continue
        found.update({one.build.stage: one for one in listed.builds if one.round == name})
    return found


__all__ = ["OpenBuild", "OpenBuilds", "read", "of_round"]
