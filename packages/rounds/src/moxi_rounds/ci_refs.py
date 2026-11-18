"""CI's own refs on a remote: the branches `check` pushes a build's tip to where the
repo's `ci` names a `ref`, and the ones someone else pushes for a build's branch under
`full_ref` (no code here pushes those); their removal once the build has ended; and the
account of stale ones that `drop --leftovers --refs` gives.

**What a build pushed** (`of_build`): each remote and branch name the records of its
check name as pushed (`pushed` on its `check.ran` and `ci.read` events), and the one
each template the setting names gives its branch now, so a push that went out but was
not recorded, or that someone else made under `full_ref`, is still found.

**The keep rule** (`Keep`), one rule for the removal at a build's end and for the sweep.
A ref is kept when it is the branch name a template the setting names (`ref` or
`full_ref`), or the prefix the ref was pushed under, gives the branch of a build of that
repo that has not ended, in any round of any project, or gives a target branch of the
repo: the project's target (its `into`, else the branch checked out at its root, as a
new build would take) and the target each such build recorded; and when a build that
has not ended recorded pushing it. While any round's log, or the project's target, can't
be read, every ref is kept, since an open build may be recorded there, or the ref may be
the target's. The rule is read again just before each deletion.

**Removal** (`remove`), run from the repo's root: `git ls-remote --heads` for each ref
first, then `git push <remote> --delete refs/heads/<ref>` for each one there that the
keep rule does not keep, each git call bounded by the push time limit. The deletion goes
through the repo's hooks. Each ref comes back `removed`, `absent`, `kept` with the rule
that kept it, or `left` with why (the network, a hook, the time limit); the way to remove
a left one later is `drop --leftovers --refs --remove`. Nothing here writes a round's
log, and nothing here undoes the finish or the drop it follows.

**The account** (`account`): for each project whose `ci` names a `ref`, the remote's
branches under each template's prefix, `ref`'s and `full_ref`'s, in one listing (`git
ls-remote --heads <remote> 'refs/heads/<prefix>*' ...`). A ref the keep rule keeps is
named with why; any other is a candidate, which an armed run with `remove` removes as
`remove` does. It reaches the network, so it runs only when asked for by name.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from moxi import proc

from . import builds, checking, ci, folders, gitreads, log
from . import state as states
from .errors import NoLog, RoundsError, SharedFolder, Unreadable

#: What became of a ref.
REMOVED = "removed"
ABSENT = "absent"
KEPT = "kept"
LEFT = "left"
WOULD_GO = "would go"

#: The command that removes, later, a ref that was left.
REMOVE = "moxi rounds drop --leftovers --refs --remove"
#: How long each git call that reaches the remote may take.
TIMEOUT_S = checking.PUSH_TIMEOUT_S


@dataclass(frozen=True)
class Ref:
    """One of CI's own refs: the project `repo` it is of, the `remote`, the branch name
    `ref` on it, the `fate` it met, and `why` for one kept or left."""

    repo: str
    remote: str
    ref: str
    fate: str
    why: str = ""

    def line(self) -> str:
        said = f"ref {self.fate} {self.remote} {self.ref}"
        return f"{said}: {self.why}" if self.why else said

    def as_json(self) -> dict:
        return {"repo": self.repo, "remote": self.remote, "ref": self.ref,
                "fate": self.fate, "why": self.why or None}


# -- what a build pushed -----------------------------------------------------------------

def recorded(state: states.State, build: builds.Build) -> list[tuple[str, str]]:
    """Each `(remote, ref)` the records of the build's check name as pushed, oldest
    first, once each."""
    found: dict[tuple[str, str], None] = {}
    for event in state.history:
        if (event["n"] > build.n and event.get("stage") == build.stage
                and event["type"] in (checking.RAN, ci.READ)):
            pushed = event.get("pushed")
            if (isinstance(pushed, Mapping) and isinstance(pushed.get("remote"), str)
                    and isinstance(pushed.get("ref"), str)):
                found[(pushed["remote"], pushed["ref"])] = None
    return list(found)


def of_build(state: states.State, build: builds.Build, settings) -> list[tuple[str, str]]:
    """Each `(remote, ref)` the build's end removes: those its records name, and the
    one each template of the repo's setting gives its branch now, `ref`'s then
    `full_ref`'s."""
    found = dict.fromkeys(recorded(state, build))
    declared = ci.declared(settings, build.repo)
    if declared is not None and declared.ref is not None and declared.remote:
        for template in declared.templates:
            found[(declared.remote, ci.ref_for(template, build.branch))] = None
    return list(found)


# -- the keep rule -----------------------------------------------------------------------

@dataclass(frozen=True)
class Keep:
    """What the keep rule reads for one repo: the `templates` its setting names now,
    `ref`'s and `full_ref`'s (none where it names no `ref`), the branches of its builds
    that have not ended (`branches`, each with the round and stage), its `targets`, the
    refs those builds recorded pushing (`pushed`), and why any log, or the project's
    target, can't be read (`unreadable`, each a reason the rule keeps every ref)."""

    templates: tuple[str, ...]
    branches: Mapping[str, str]
    targets: tuple[str, ...]
    pushed: Mapping[str, str]
    unreadable: tuple[str, ...]

    def why(self, ref: str, *, prefixes: Iterable[str] = ()) -> str | None:
        """Why the keep rule keeps `ref`, pushed under the `prefixes` besides the
        templates'; None when it does not."""
        if self.unreadable:
            return self.unreadable[0]
        if ref in self.pushed:
            return f"a build that has not ended pushed it: {self.pushed[ref]}"
        known = dict.fromkeys(prefixes)
        for template in self.templates:
            known[ci.prefix_of(template)] = None
        for prefix in known:
            if not ref.startswith(prefix):
                continue
            branch = ref[len(prefix):]
            if branch in self.branches:
                return f"it is the ref of a build that has not ended: {self.branches[branch]}"
            if branch in self.targets:
                return f"it is the ref of the target {branch}, which is the lead's"
        return None


#: What the keep rule says of a round's log it can't read.
_LOG = "a round's log can't be read, and an open build may be recorded there: "


def keep(repo: str, settings) -> Keep:
    """The keep rule for the project `repo`, read now from every round's log."""
    declared = ci.declared(settings, repo)
    templates = () if declared is None or declared.ref is None else declared.templates
    homes, unresolved = folders.homes(settings)
    unreadable = [_LOG + exc.reason for exc in unresolved if isinstance(exc, SharedFolder)]
    branches: dict[str, str] = {}
    pushed: dict[str, str] = {}
    target, why = _target(settings, repo)
    if why is not None:
        unreadable.append(why)
    targets = dict.fromkeys([target] if target else [])
    for home in homes:
        for slug in folders.slugs(home.rounds_dir):
            name = f"{home.project}/{slug}"
            try:
                state = states.fold(log.read_round(log.round_log(home.round_dir(slug))))
            except NoLog:  # removed since the folder was listed
                continue
            except Unreadable as exc:
                unreadable.append(_LOG + exc.reason)
                continue
            except OSError as exc:
                unreadable.append(f"{_LOG}the log of {name} can't be read: {exc}")
                continue
            for build in builds.builds(state):
                if build.ended is not None or build.repo != repo:
                    continue
                branches[build.branch] = f"{name} {build.stage}"
                targets[build.target] = None
                for _remote, ref in recorded(state, build):
                    pushed[ref] = f"{name} {build.stage}"
    return Keep(templates, branches, tuple(targets), pushed, tuple(unreadable))


def _target(settings, repo: str) -> tuple[str | None, str | None]:
    """The project's target, the branch a new build of `repo` would land on: its
    `into`, else the branch checked out at its root (None for a detached head); and,
    when that can't be read, why."""
    from . import settings as own

    into = own.build_setting(settings, repo, "into")
    if isinstance(into, str) and into:
        return into, None
    try:
        _key, root = folders.root(repo)
        return gitreads.Git(root).checked_out(), None
    except RoundsError as exc:
        return None, ("the project's target can't be read, and the ref may be its: "
                      f"{exc.reason}")


# -- removal -----------------------------------------------------------------------------

@dataclass(frozen=True)
class _Said:
    code: int
    said: str


def _git(root: Path, *args: str) -> _Said:
    """Run git in the repo's root for at most `TIMEOUT_S`: its exit code (1 for a git
    that gave no answer or could not run) and what it printed."""
    argv = ["git", "-C", str(root), *args]
    try:
        done = proc.run(argv, timeout_s=TIMEOUT_S, env=gitreads.git_env(), stdin_data="")
    except subprocess.TimeoutExpired:
        return _Said(1, f"git {args[0]} gave no answer in {TIMEOUT_S:g} seconds")
    except OSError as exc:
        return _Said(1, f"git could not run: {exc}")
    said = "\n".join(part.strip() for part in (done.stdout or "", done.stderr or "")
                     if part.strip())
    return _Said(done.returncode, said)


def _listed(root: Path, remote: str, *patterns: str) -> list[str] | str:
    """The branch names under `refs/heads/` the remote lists for the `patterns`, or what
    git said when it could not list them."""
    done = _git(root, "ls-remote", "--heads", remote, *patterns)
    if done.code != 0:
        return done.said or f"git ls-remote exited {done.code}"
    names = []
    for line in done.said.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            names.append(parts[1][len("refs/heads/"):])
    return names


def _delete(root: Path, repo: str, remote: str, ref: str, settings, *,
            prefixes: Iterable[str]) -> Ref:
    """Read the keep rule again, then delete the ref from the remote unless it keeps it."""
    why = keep(repo, settings).why(ref, prefixes=prefixes)
    if why is not None:
        return Ref(repo, remote, ref, KEPT, why)
    done = _git(root, "push", remote, "--delete", f"refs/heads/{ref}")
    if done.code != 0:
        return Ref(repo, remote, ref, LEFT, (done.said or f"git push exited {done.code}")
                   + f"; remove it later with {REMOVE}")
    return Ref(repo, remote, ref, REMOVED)


def remove(state: states.State, build: builds.Build, settings) -> tuple[Ref, ...]:
    """Remove from the remote each of CI's own refs the ended build `build` pushed, or
    that the setting gives its branch now, under the keep rule; and say what became of
    each. Never raises for a ref it could not remove: that one is `left`, with why."""
    wanted = of_build(state, build, settings)
    if not wanted:
        return ()
    try:
        _key, root = folders.root(build.repo)
    except RoundsError as exc:
        return tuple(Ref(build.repo, remote, ref, LEFT, exc.reason) for remote, ref in wanted)
    done = []
    for remote, ref in wanted:
        prefixes = [ref[:-len(build.branch)]] if ref.endswith(build.branch) else []
        listed = _listed(root, remote, f"refs/heads/{ref}")
        if isinstance(listed, str):
            done.append(Ref(build.repo, remote, ref, LEFT,
                            f"{listed}; remove it later with {REMOVE}"))
        elif ref not in listed:
            done.append(Ref(build.repo, remote, ref, ABSENT))
        else:
            done.append(_delete(root, build.repo, remote, ref, settings,
                                prefixes=prefixes))
    return tuple(done)


# -- the account -------------------------------------------------------------------------

@dataclass(frozen=True)
class Account:
    """The account of CI's own refs: each ref listed with its fate, and each project
    whose remote could not be listed (`unlisted`, as lines)."""

    refs: tuple[Ref, ...] = ()
    unlisted: tuple[str, ...] = ()

    @property
    def would_go(self) -> tuple[Ref, ...]:
        return tuple(one for one in self.refs if one.fate == WOULD_GO)

    def lines(self) -> list[str]:
        return [one.line() for one in self.refs] + list(self.unlisted)

    def as_json(self) -> dict:
        return {"refs": [one.as_json() for one in self.refs],
                "unlisted": list(self.unlisted)}


def account(settings, *, removing: bool) -> Account:
    """The account of every project whose `ci` names a `ref`, over the prefix of each
    template it names; with `removing`, each candidate is removed as `remove` removes
    one."""
    refs, unlisted = [], []
    for repo, table in settings["projects"].items():
        if not isinstance(table, Mapping) or "ci" not in table:
            continue
        declared = ci.declared(settings, repo)
        if declared is None or declared.ref is None or not declared.remote:
            continue
        prefixes = tuple(ci.prefix_of(one) for one in declared.templates)
        try:
            _key, root = folders.root(repo)
        except RoundsError as exc:
            unlisted.append(f"refs of {repo} not listed: {exc.reason}")
            continue
        listed = _listed(root, declared.remote,
                         *(f"refs/heads/{prefix}*" for prefix in prefixes))
        if isinstance(listed, str):
            unlisted.append(f"refs of {repo} not listed from {declared.remote}: {listed}")
            continue
        rule = keep(repo, settings)
        for ref in listed:
            if not ref.startswith(prefixes):
                continue
            why = rule.why(ref)
            if why is not None:
                refs.append(Ref(repo, declared.remote, ref, KEPT, why))
            elif not removing:
                refs.append(Ref(repo, declared.remote, ref, WOULD_GO))
            else:
                refs.append(_delete(root, repo, declared.remote, ref, settings,
                                    prefixes=()))
    return Account(tuple(refs), tuple(unlisted))


def lines(refs: Iterable[Ref]) -> list[str]:
    """The lines a finish or a drop prints for the refs it removed or did not."""
    return [one.line() for one in refs]


__all__ = ["REMOVED", "ABSENT", "KEPT", "LEFT", "WOULD_GO", "REMOVE", "TIMEOUT_S", "Ref",
           "recorded", "of_build", "Keep", "keep", "remove", "Account", "account",
           "lines"]
