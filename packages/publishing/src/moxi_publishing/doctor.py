"""publishing's doctor rows: `rows()`, which the announcement's `DOCTOR` names.

Read-only, and no row uses the network. For each repo repo has indexed:

- `fail` when the repo carries the public flag and one of the three pieces is missing or
  disabled there, since part of what the flag promises then doesn't run;
- `fail` when a publishing piece is registered in a repo without the flag: it refuses
  every commit or push there;
- `ok`, one row, for a flagged repo with all three pieces enabled.

With no flagged repo and no stray piece, one `ok` row says so.
"""

from __future__ import annotations

from collections.abc import Iterator

from moxi.doctor import Row

MODULE = "publishing"


def rows() -> Iterator[Row]:
    from moxi_repo import api
    from moxi_repo.api import quote_arg

    from moxi_publishing import flag

    try:
        repos = api.indexed_repos()
    except api.Failure as exc:
        yield Row(MODULE, "repos", "fail", f"repo's index can't be read: {exc.text}")
        return
    seen = False
    for _key, _entry, repo, _why in repos:
        if repo is None:
            continue
        root = str(repo.root)
        try:
            public = flag.is_public(root)
            have = flag.registered(root)
        except flag.Failure as exc:
            yield Row(MODULE, f"repo {root}", "fail", exc.text)
            continue
        if not public and not have:
            continue
        seen = True
        name = f"repo {root}"
        if not public:
            yield Row(MODULE, name, "fail",
                      f"{', '.join(sorted(have))} registered, but the repo isn't marked "
                      "public, so each refuses",
                      f"moxi publishing enable {quote_arg(root)} (or: moxi publishing "
                      f"disable {quote_arg(root)})")
            continue
        missing = [piece for piece in flag.PIECES if piece not in have]
        off = [piece for piece in flag.PIECES if piece in have and not have[piece]["enabled"]]
        if missing or off:
            parts = ([f"missing: {', '.join(missing)}"] if missing else []) + (
                [f"disabled: {', '.join(off)}"] if off else [])
            yield Row(MODULE, name, "fail",
                      f"marked public, but not every guard runs ({'; '.join(parts)})",
                      f"moxi publishing enable {quote_arg(root)}" if missing else
                      f"moxi repo git-hooks enable {quote_arg(root)} {off[0]}")
            continue
        yield Row(MODULE, name, "ok", "marked public; the three pieces are enabled")
    if not seen:
        yield Row(MODULE, "repos", "ok", "no repo is marked public")
