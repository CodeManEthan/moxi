"""When the target moved under a build: whether its finish owes one more re-check.

A build verdict records the head its pass read, `V`, and `B0`, the merge-base of `V`
and `P`, the target as it was before the build reached it (its head while the build is
off it). `read` takes those with `T`, the tip being finished, and `P` as they read now.

- The **build's paths** are what the branch changes against the target at `T`: the
  paths that differ between `merge-base(T, P)` and `T`. A fix committed after `V` is in
  them.
- The **target's paths** are the paths that differ between `B0` and `P`.
- The **shared paths** are the paths the target changed that match a glob of
  `shared_globs`: the common lockfiles and the repo's own `shared_paths`. A glob with no
  `/` is matched against a path's last part, so a lockfile in a subfolder is one too; a
  glob with a `/` is matched against the whole path.

The rules, in order:

1. `P` is `B0`: the target has not moved, and nothing more is read.
2. The target moved and the two sets of paths share none, and the target changed no
   shared path: no re-check is owed.
3. They share a path, or the target changed a shared path: a re-check is owed, read at
   the verify-build step's re-check tier with `pass = "drift"`, at a tip that holds `P`,
   so the target is merged into the branch first.
4. The target was merged into the branch after `V` (a commit of `V..T` is `P` or under
   it): `T` counts as verified without a re-check when every commit of `V..T` that is not
   `P` or under it is a merge with a parent that is, and the paths that differ between
   `V` and `T` hold none of the build's paths and no path a shared glob matches.
   Otherwise a re-check is owed, as in rule 3.

When rule 4 accepts the merge but the target moved again after it, rule 3 still reads
the target's paths up to `P`: a change on the target that meets the build is owed a
re-check whichever rule finds it.

A verdict whose `B0` git gave none or several of, a verdict head or `B0` git no longer
holds, and a tip with no single merge-base with `P` can't be read this way, and each
owes the re-check: what isn't read isn't taken as unmoved.

Path overlap misses a clash between files that don't overlap, such as a renamed
function; the check on the merged tree is the net for that.

Nothing here writes. git is only read.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass

from . import builds, findings, report_block
from . import settings as own
from . import state as states
from .gitreads import Git

#: The lockfiles every repo shares between builds, whatever its own `shared_paths` add.
LOCKFILES = ("uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml",
             "yarn.lock", "Cargo.lock", "go.sum", "Gemfile.lock")

#: The rule that gave a reading.
NOT_MOVED = 1
APART = 2
OWED = 3
MERGED_IN = 4


@dataclass(frozen=True)
class Drift:
    """The drift of one build at one tip.

    `rule` is the rule that decided (1 to 4), `owed` whether a re-check is owed, and
    `why` says so in words when it is. `head`, `merge_base`, `tip` and `target_before`
    are `V`, `B0`, `T` and `P`. `moved` is the number of commits the target gained
    since `B0`, None when that could not be read. `meeting` holds the build's paths the
    target also changed and `shared` the shared paths it changed, each sorted.
    `merged_in` says the target was merged into the branch after `V`; `covered` holds
    the commits of `V..T` that rule 4 accepts, none when it accepts none.
    `build_from` is `merge-base(T, P)`, the start of the build's diff."""

    rule: int
    owed: bool
    head: str
    merge_base: str | None
    tip: str
    target_before: str
    moved: int | None = None
    meeting: tuple[str, ...] = ()
    shared: tuple[str, ...] = ()
    merged_in: bool = False
    covered: tuple[str, ...] = ()
    build_from: str | None = None
    why: str | None = None

    @property
    def ranges(self) -> dict[str, str | None]:
        """The two ranges a re-check reads: the target's change, `B0..P`, and the
        build's diff, `merge-base(T, P)..T`; None for one that could not be read."""
        return {"target": None if self.merge_base is None
                else f"{self.merge_base}..{self.target_before}",
                "build": None if self.build_from is None
                else f"{self.build_from}..{self.tip}"}

    def data(self) -> dict:
        return {"rule": self.rule, "owed": self.owed, "why": self.why,
                "head": self.head, "merge_base": self.merge_base, "tip": self.tip,
                "target_before": self.target_before, "moved": self.moved,
                "meeting": list(self.meeting), "shared": list(self.shared),
                "merged_in": self.merged_in, "covered": list(self.covered),
                "ranges": self.ranges}


def shared_globs(settings, repo: str) -> tuple[str, ...]:
    """The shared globs of builds in `repo`: the lockfiles, then the repo's own
    `shared_paths`."""
    return tuple(dict.fromkeys(
        (*LOCKFILES, *own.build_setting(settings, repo, "shared_paths"))))


def is_shared(path: str, globs: Iterable[str]) -> bool:
    """Whether a glob of `globs` matches `path`, a path git names with `/`."""
    last = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(path if "/" in glob else last, glob)
               for glob in globs)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def read(git: Git, *, head: str, merge_base: str | None, tip: str, target_before: str,
         globs: Iterable[str]) -> Drift:
    """The drift of a build whose verdict read `head` with `merge_base` as its `B0`,
    at the tip `tip` with the target before it at `target_before`, `globs` being the
    shared globs of its repo."""
    globs = tuple(globs)
    at = {"head": head, "merge_base": merge_base, "tip": tip,
          "target_before": target_before}
    if merge_base is None:
        return Drift(OWED, owed=True, **at, why=(
            "the verdict's head had no single merge-base with the target, so what "
            "the target changed since can't be read"))
    if merge_base == target_before:
        return Drift(NOT_MOVED, owed=False, moved=0, **at)
    held = git.present([head, merge_base, tip, target_before])
    missing = [one for one in dict.fromkeys(at.values()) if one not in held]
    if missing:
        return Drift(OWED, owed=True, **at, why=(
            f"git does not hold {', '.join(one[:12] for one in missing)}, so what the "
            "target changed since the verdict can't be read"))
    moved = len(git.between(merge_base, target_before))
    bases = git.merge_bases(tip, target_before)
    if len(bases) != 1:
        return Drift(OWED, owed=True, moved=moved, **at, why=(
            f"the tip {tip[:12]} has {'no' if not bases else 'more than one'} "
            "merge-base with the target, so the build's paths can't be read"))
    build_from = bases[0]
    built = git.changed_paths(build_from, tip)
    changed = git.changed_paths(merge_base, target_before)
    meeting = tuple(sorted(built & changed))
    shared = tuple(sorted(one for one in changed if is_shared(one, globs)))
    whys = []
    if meeting or shared:
        parts = [f"{_plural(len(meeting), 'path')} the build changes"] if meeting else []
        parts += [f"{_plural(len(shared), 'shared path')}"] if shared else []
        whys.append(f"the target changed {' and '.join(parts)}")
    merged_in, covered = _merged_in(git, head=head, tip=tip, target_before=target_before,
                                    built=built, globs=globs, whys=whys)
    owed = bool(whys)
    rule = OWED if owed else MERGED_IN if merged_in else APART
    return Drift(rule, owed=owed, moved=moved, meeting=meeting, shared=shared,
                 merged_in=merged_in, covered=covered, build_from=build_from,
                 why="; ".join(whys) or None, **at)


def _merged_in(git: Git, *, head: str, tip: str, target_before: str,
               built: frozenset[str], globs: tuple[str, ...],
               whys: list[str]) -> tuple[bool, tuple[str, ...]]:
    """Whether the target was merged into the branch after `head`, and the commits of
    `head..tip` rule 4 accepts; adds to `whys` what keeps it from accepting them."""
    inside = git.between(head, tip)
    own_commits = git.between(head, tip, nor=[target_before])
    if len(own_commits) == len(inside):
        return False, ()
    unmerged = [one for one in own_commits if not _merges_target(git, one, target_before)]
    if unmerged:
        whys.append(f"the target was merged into the branch after the verdict, and "
                    f"{_plural(len(unmerged), 'commit')} since the verdict "
                    f"{'is' if len(unmerged) == 1 else 'are'} no merge of the target "
                    f"({', '.join(one[:12] for one in unmerged)})")
        return True, ()
    moved = git.changed_paths(head, tip)
    touched = sorted(one for one in moved if one in built or is_shared(one, globs))
    if touched:
        whys.append(f"the target was merged into the branch after the verdict, and "
                    f"the merge changes {_plural(len(touched), 'path')} of the build "
                    f"or shared ({', '.join(touched[:5])}"
                    + (", ..." if len(touched) > 5 else "") + ")")
        return True, ()
    return True, tuple(inside)


def _merges_target(git: Git, commit: str, target_before: str) -> bool:
    """Whether `commit` is a merge with a parent that is `target_before` or under it."""
    parents = git.parents(commit)
    return len(parents) > 1 and any(git.is_ancestor(one, target_before)
                                    for one in parents)


def newest_verdict(state: states.State, build: builds.Build) -> states.Verdict | None:
    """The newest verdict on a report of `build` itself, not of a step of the user's
    own; None when none is recorded."""
    listed = [one for one in findings.verdicts_of(state, build)
              if one.kind == report_block.BUILD]
    return listed[-1] if listed else None


def of_build(state: states.State, build: builds.Build, status: builds.Status, git: Git,
             *, settings) -> Drift | None:
    """The drift of `build` at the head its `status` reads now, against its newest
    build verdict; None when it has no such verdict, or no head or no target before
    it was read."""
    verdict = newest_verdict(state, build)
    if (verdict is None or not verdict.head or status.head is None
            or status.target_before is None):
        return None
    return read(git, head=verdict.head, merge_base=verdict.merge_base, tip=status.head,
                target_before=status.target_before,
                globs=shared_globs(settings, build.repo))


def line(stage: str, drift: dict) -> str:
    """A build's drift in words, from its data: `target moved 4 commits under B2; 0
    paths meet` or `...; 2 paths meet: re-check owed`."""
    if drift["rule"] == NOT_MOVED:
        return f"target has not moved under {stage}"
    if drift["moved"] is None or (drift["owed"] and drift["ranges"]["build"] is None):
        moved = ("" if drift["moved"] is None
                 else f"target moved {_plural(drift['moved'], 'commit')} under {stage}; ")
        return f"{moved}drift not read: {drift['why']}: re-check owed"
    meet = len(drift["meeting"])
    text = (f"target moved {_plural(drift['moved'], 'commit')} under {stage}; "
            f"{meet} {'path meets' if meet == 1 else 'paths meet'}")
    if drift["shared"]:
        text += f", {_plural(len(drift['shared']), 'shared path')} changed"
    if drift["merged_in"]:
        text += ", merged into the branch"
    return text + (": re-check owed" if drift["owed"] else "")


__all__ = ["LOCKFILES", "NOT_MOVED", "APART", "OWED", "MERGED_IN", "Drift",
           "shared_globs", "is_shared", "read", "newest_verdict", "of_build", "line"]
