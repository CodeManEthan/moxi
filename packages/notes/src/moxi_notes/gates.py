"""notes' two gates, called by repo's git hook runner: `pre_commit` and `pre_push`.

Both judge whole blobs read from git's object store (`moxi_notes.judge`), never a diff.

**The pre-commit gate** reads the index the commit will use (a pathspec commit's own
index included) with `git diff-index --cached --raw -z --no-renames`, and judges every
added or changed blob. What a file with a finding does depends on the commit:

- a hand commit under `action = leave-out`: the file is left out and the rest commits.
  Every path with a finding is unstaged; when one of them is an added file, every staged
  deletion is held back too, so a rename whose new side is left out stays where it was.
  The index is read again, and the commit is refused if a path is still staged. Each
  left-out file prints one line on stderr. When nothing would be left, the gate refuses
  without touching the index, rather than let git make an empty commit;
- a hand commit under `action = redact`: refused, one line per finding, with the fixes.
  The clean filter redacts at `git add`, so a staged blob that still holds a secret is one
  it didn't redact;
- a hand commit that concludes a merge, rebase step, cherry-pick or revert: refused under
  either action, with the fixes and the index unchanged. Unstaging there would put
  `HEAD`'s version back and drop the change the commit brings in;
- a commit by repo's committer: refused, naming the files, with the index unchanged. The
  snapshot's prepare step left out every file it judged; a finding here is content that
  changed after it, and the next pass judges it again. Nothing prints on a commit that
  passes, so a quiet pass leaves no record.

**The push gate** judges, for each ref the push updates, every commit the remote lacks:
each new blob, and each author and committer line, scanned for private names at the path
`@author`. Any finding refuses the whole push. It is the backstop for whatever slips past
either action.

The gates' index writes and reads keep the git hook's index and read every path
literally, whatever the user's environment sets: they run through `moxi_repo.api.git`
with the git hook's environment and each of git's four pathspec variables set to `0`,
and each path is written `:(literal)<path>` on stdin.

Neither gate ever opens on a failure: a set that can't run, a config or a git call that
fails refuses with the reason. An allowlist that can't be read is judged as empty, and
the gate says so.

Standard library only at import; repo's names, secrets and the config are imported inside
the calls that use them, so a git hook pays for them only when a gate runs.
"""

from __future__ import annotations

import os
import sys

#: What a hand commit concludes, by the file or folder git keeps in the git dir, with the
#: command that abandons it.
IN_PROGRESS = (("MERGE_HEAD", "merge", "git merge --abort"),
               ("CHERRY_PICK_HEAD", "cherry-pick", "git cherry-pick --abort"),
               ("REVERT_HEAD", "revert", "git revert --abort"),
               ("rebase-merge", "rebase step", "git rebase --abort"),
               ("rebase-apply", "rebase step", "git rebase --abort"))

ALLOWANCE = ("add an allowance to .secrets-allow at the top of the work tree (credentials "
             "only) or to secrets-allow-private in the git dir")


class _Refusal(Exception):
    """A gate that can't judge: its lines are the refusal."""

    def __init__(self, *lines: str):
        super().__init__(lines[0])
        self.lines = lines


def _zero(oid: str) -> bool:
    return not oid.strip("0")


def pathspecs_off() -> dict[str, str]:
    """Each of git's four pathspec variables off, whatever the user's environment sets."""
    from moxi_repo.api import PATHSPEC_VARS

    return {name: "0" for name in PATHSPEC_VARS}


def _setup(ctx):
    """(settings, allowlist, why it reads as empty) for this repo, or `_Refusal`."""
    from moxi.config import ConfigError

    from . import judge

    try:
        settings = judge.config()
    except ConfigError as exc:
        raise _Refusal(f"notes' config can't be read: {exc}",
                       "fix: moxi config notes") from None
    allow, problem = judge.allowlist(ctx.root, ctx.common_dir)
    return settings, allow, problem


def _judge(ctx, entries, settings, allow):
    """Each path's findings, or `_Refusal` when a set or git call fails."""
    from moxi_secrets import SetUnavailable

    from . import blobs, judge

    try:
        return judge.judge(entries, ctx.git, cap=judge.max_bytes(settings), allow=allow)
    except SetUnavailable as exc:
        raise _Refusal(f"a set notes judges with can't run: {exc}") from None
    except blobs.GitError as exc:
        raise _Refusal(f"the files could not be judged: {exc}") from None


def _lines(found) -> list[str]:
    """One line per finding: `notes/plan.md:12 credentials/github-pat`."""
    out = []
    for path in sorted(found):
        for f in found[path]:
            out.append(f"  {path}:{f.line} {f.rule}" if f.line is not None
                       else f"  {path} {f.words()}")
    return out


def _count(found) -> str:
    n = len(found)
    return f"{n} staged file{'s' if n != 1 else ''} hold{'s' if n == 1 else ''} a secret"


# -- pre-commit ---------------------------------------------------------------------------

def _base(ctx) -> str:
    """`HEAD`'s commit, or the empty tree on an unborn branch."""
    from . import blobs

    done = ctx.git("rev-parse", "-q", "--verify", "HEAD^{commit}", timeout_s=blobs.TIMEOUT)
    head = done.stdout.strip() if done.returncode == 0 else ""
    if head:
        return head
    return os.fsdecode(blobs.run(ctx.git, "hash-object", "-t", "tree", "--stdin",
                                 input=b"")).strip()


def _staged(ctx, base: str):
    from . import blobs

    return blobs.parse_raw(blobs.run(ctx.git, "diff-index", "--cached", "--raw", "-z",
                                     "--no-renames", base))


def _concluding(ctx):
    """(what the commit concludes, its abort command), or None for a plain commit."""
    git_dir = ctx.git_dir
    if not git_dir:
        return None
    for name, what, abort in IN_PROGRESS:
        if os.path.exists(os.path.join(git_dir, name)):
            return what, abort
    return None


def pre_commit(ctx):
    """The pre-commit gate (see the module's text). Returns a `moxi_repo` `Outcome`."""
    from moxi_repo.api import Outcome

    from . import blobs

    try:
        settings, allow, problem = _setup(ctx)
        try:
            base = _base(ctx)
            entries = _staged(ctx, base)
        except blobs.GitError as exc:
            raise _Refusal(f"the staged files could not be read: {exc}") from None
        found = _judge(ctx, [(e.path, e.blob) for e in entries if e.blob], settings, allow)
        if not found:
            return Outcome.ok()
        empty = [f"notes judged with no allowances: {problem}"] if problem else []
        if ctx.committer:
            one = len(found) == 1
            them = "it" if one else "them"
            return Outcome.refuse(
                f"{len(found)} file{'' if one else 's'} changed after the snapshot judged "
                f"{them} and hold{'s' if one else ''} a secret; the next snapshot judges "
                f"{them} again", *_lines(found), *empty)
        concluding = _concluding(ctx)
        if concluding is not None:
            what, abort = concluding
            return Outcome.refuse(
                f"{_count(found)}, and this commit concludes a {what}: leaving a file out "
                "would drop the change it brings in", *_lines(found), *empty,
                f"fix: take the secret out of the file or {ALLOWANCE}, git add it and "
                f"commit again; or {abort}")
        if settings["action"] == "redact":
            return Outcome.refuse(
                f"{_count(found)} the clean filter did not redact", *_lines(found), *empty,
                "fix: git add the file again so the filter redacts it, or remove the "
                f"secret, or {ALLOWANCE}")
        return _leave_out(ctx, base, entries, found, problem)
    except _Refusal as exc:
        return Outcome.refuse(*exc.lines)


def _unstage_args(paths) -> tuple[tuple[str, ...], bytes]:
    from moxi_repo.api import literal_pathspec

    data = b"\0".join(os.fsencode(literal_pathspec(p)) for p in paths)
    return ("reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul"), data


def _index_git(ctx, *args, input=None):
    """git in the git hook's own index, every pathspec read literally."""
    from moxi_repo.api import git

    from . import blobs

    return blobs.run(lambda *a, **kw: git(ctx.root, *a, clean_env=False,
                                          extra_env=pathspecs_off(), **kw),
                     *args, input=input)


def _leave_out(ctx, base, entries, found, problem):
    from moxi_repo.api import Outcome

    from . import blobs, judge

    by_path = {e.path: e for e in entries}
    out = sorted(found)
    added = any(by_path[p].status == "A" for p in out)
    held = sorted(e.path for e in entries if e.status == "D" and e.path not in found) \
        if added else []
    reset = out + held
    if set(reset) >= set(by_path):
        return Outcome.refuse("every staged file holds a secret; nothing left to commit",
                              *_lines(found),
                              *([f"notes judged with no allowances: {problem}"]
                                if problem else []),
                              f"fix: remove the secret or {ALLOWANCE}, then commit again")
    args, data = _unstage_args(reset)
    try:
        _index_git(ctx, *args, input=data)
        after = blobs.parse_raw(_index_git(ctx, "diff-index", "--cached", "--raw", "-z",
                                           "--no-renames", base))
    except blobs.GitError as exc:
        return Outcome.refuse(f"{_count(found)} and could not be left out: {exc}",
                              *_lines(found), "nothing was committed")
    still = {e.path for e in after}
    stuck = [p for p in reset if p in still]
    if stuck:
        return Outcome.refuse(f"{_count(found)} and could not be left out; nothing was "
                              "committed", *(f"  {p} is still staged" for p in stuck))
    if not still:
        return Outcome.refuse("every staged file holds a secret; nothing left to commit",
                              *_lines(found))
    say = sys.stderr.write
    if problem:
        say(f"notes: judged with no allowances: {problem}\n")
    for path in out:
        say(f"notes: left out {path} ({judge.words(found[path])}); remove it or add an "
            "allowance\n")
    for path in held:
        say(f"notes: held back the deletion of {path}, since an added file was left out\n")
    sys.stderr.flush()
    return Outcome.ok()


# -- pre-push -----------------------------------------------------------------------------

def _known(ctx, oid: str) -> bool:
    from . import blobs

    done = ctx.git("cat-file", "-e", f"{oid}^{{commit}}", timeout_s=blobs.TIMEOUT)
    return done.returncode == 0


def _new_commits(ctx) -> list[str]:
    """Every commit the remote lacks, over every ref the push updates; a delete adds
    none."""
    from . import blobs

    remote = ctx.args[0] if ctx.args else ""
    out: dict[str, None] = {}
    for u in ctx.updates:
        if _zero(u.local_sha):
            continue
        if not _zero(u.remote_sha) and _known(ctx, u.remote_sha):
            args = ("rev-list", u.local_sha, f"^{u.remote_sha}")
        elif remote:
            args = ("rev-list", u.local_sha, "--not", f"--remotes={remote}")
        else:
            args = ("rev-list", u.local_sha)
        for oid in os.fsdecode(blobs.run(ctx.git, *args)).split():
            out[oid] = None
    return list(out)


def pre_push(ctx):
    """The push gate (see the module's text). Returns a `moxi_repo` `Outcome`."""
    from moxi_repo.api import Outcome
    from moxi_secrets import SetUnavailable

    from . import blobs, judge

    try:
        settings, allow, problem = _setup(ctx)
        try:
            commits = _new_commits(ctx)
            if not commits:
                return Outcome.ok()
            listing = ("\n".join(commits) + "\n").encode("ascii")
            entries = blobs.parse_raw(blobs.run(
                ctx.git, "diff-tree", "--stdin", "-r", "-z", "--raw", "-m", "--root",
                "--no-renames", input=listing))
            people = blobs.run(ctx.git, "log", "--no-walk=unsorted", "--stdin", "-z",
                               "--format=%an <%ae>%x00%cn <%ce>", input=listing)
        except blobs.GitError as exc:
            raise _Refusal(f"the commits to push could not be read: {exc}") from None
        pairs = list(dict.fromkeys((e.path, e.blob) for e in entries if e.blob))
        found = _judge(ctx, pairs, settings, allow)
        fields = os.fsdecode(people).split("\0")
        names: list[str] = []
        try:
            for i, oid in enumerate(commits):
                for who, text in (("author", fields[2 * i:2 * i + 1]),
                                  ("committer", fields[2 * i + 1:2 * i + 2])):
                    for f in judge.scan_names(text[0] if text else "", allow):
                        names.append(f"  {who} of {oid[:12]}: {f.rule}")
        except SetUnavailable as exc:
            raise _Refusal(f"a set notes judges with can't run: {exc}") from None
        if not found and not names:
            return Outcome.ok()
        n = len(found) + len(names)
        return Outcome.refuse(
            f"the push would carry a secret ({n} finding{'s' if n != 1 else ''}); nothing "
            "was pushed", *_lines(found), *names,
            *([f"notes judged with no allowances: {problem}"] if problem else []),
            f"fix: rewrite the commits that carry it, or {ALLOWANCE} (an author or "
            "committer's allowance takes the path @author), then push again")
    except _Refusal as exc:
        return Outcome.refuse(*exc.lines)


__all__ = ["pre_commit", "pre_push", "pathspecs_off", "IN_PROGRESS"]
