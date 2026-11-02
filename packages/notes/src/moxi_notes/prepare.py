"""The snapshot's prepare step: which staged files hold a secret, so the pass leaves them out.

The committer calls `prepare(ctx)` after it stages the snapshot's paths. Each change
carries its staged blob id, so no list of paths goes to git: the blobs are read by id on
stdin and judged whole (`moxi_notes.judge`). A file with a finding is dropped, with its
rule and line as the reason and never the text; the committer unstages it, takes a rename
whole and holds back deletions when an added file is dropped, and names every dropped
path in the pass's record. The rest commits. Under either action the step is the same: a
file the clean filter redacted judges clean, and one it couldn't is left out.

One file costs one file: a blob over `max_mb` is left out, and an allowlist that can't be
read leaves out every file with a hit, each reason naming the allowlist's file and line.
The step refuses the whole pass only when it can judge no file: a set that can't run,
notes' config that can't be read, or a git call that fails.

It prints nothing, so a quiet pass leaves no record.
"""

from __future__ import annotations


def prepare(ctx):
    """Judge the staged changes in `ctx` (a `moxi_repo` `PrepareContext`); a `Prepared`."""
    from moxi.config import ConfigError
    from moxi_repo.api import Prepared
    from moxi_secrets import SetUnavailable

    from . import blobs, judge

    try:
        settings = judge.config()
        common = judge.common_dir(ctx.git)
        allow, problem = judge.allowlist(ctx.root, common)
        entries = [(c.path, c.blob) for c in ctx.changes if c.blob]
        found = judge.judge(entries, ctx.git, cap=judge.max_bytes(settings), allow=allow)
    except SetUnavailable as exc:
        return Prepared(refuse=f"a set notes judges with can't run: {exc}")
    except ConfigError as exc:
        return Prepared(refuse=f"notes' config can't be read: {exc}")
    except blobs.GitError as exc:
        return Prepared(refuse=f"the staged files could not be judged: {exc}")
    tail = f"; judged with no allowances, since {problem}" if problem else ""
    return Prepared(drop=[(path, judge.words(found[path]) + tail) for path in sorted(found)])


__all__ = ["prepare"]
