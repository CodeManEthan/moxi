"""Clean history: a repo's own choice to keep its history publishable, public or not.

    moxi publishing clean-history enable PATH
    moxi publishing clean-history disable PATH

`enable` registers the message and content pieces (each seeded from publishing's
`defaults.*` keys where it is first registered, as `moxi publishing enable` seeds them),
makes sure repo's commit-rules piece refuses each key of `defaults.refused_trailers`, and
then writes the clean flag, `moxi-publishing.clean = true`, beside
`moxi-publishing.interpreter`. A key the repo already refuses (matched ignoring case) is
kept as it is; the others are added to its `refused-trailer` list, never replacing what is
there, and recorded in `moxi-publishing.clean-trailers`. Where commit-rules wasn't
registered, `enable` registers it and records that in `moxi-publishing.clean-rules`. Where
repo had written nothing in the repo's git config and hadn't indexed it, `enable` records
that in `moxi-publishing.clean-hooks`.

`disable` refuses in a public repo, which always keeps clean history. Elsewhere it
removes the two pieces, takes back out of `refused-trailer` only the keys it recorded, and
removes commit-rules only when it registered the piece and nothing else is set there: no
refused key left, no `rule` or `tag-check-trailers`, and the tag patterns repo seeds a new
registration with. Where `enable` found nothing of repo's and no piece is left
registered, it then removes everything repo wrote there, as `moxi repo git-hooks remove
PATH` does, so the repo's git config is as it was. Last it unsets the flag, the records
and the interpreter.

`moxi publishing enable` turns clean history on too, through `Prepared`, recording in
`moxi-publishing.clean-with-public` that it did when the clean flag wasn't set; `moxi
publishing disable` then turns clean history off with the public flag.
"""

from __future__ import annotations

import os

from moxi_publishing import flag

#: What `enable` writes beside the flag: the keys it added to `refused-trailer`, and
#: whether it registered commit-rules.
CLEAN_TRAILERS = "moxi-publishing.clean-trailers"
CLEAN_RULES = "moxi-publishing.clean-rules"
#: Whether repo had written nothing in the repo's git config, nor indexed it, before.
CLEAN_HOOKS = "moxi-publishing.clean-hooks"
#: Whether `moxi publishing enable` turned clean history on, so `disable` turns it off.
CLEAN_WITH_PUBLIC = "moxi-publishing.clean-with-public"
#: The two pieces clean history registers, in the order it registers them.
PIECES = (flag.MESSAGE, flag.CONTENT)
COMMIT_RULES = "repo/commit-rules"
REFUSED_TRAILER = "refused-trailer"


def _refused_keys() -> list[str]:
    from moxi import config

    from moxi_publishing import settings

    try:
        values = settings.load()
    except config.ConfigError as exc:
        raise flag.Failure(f"publishing's config can't be read: {exc}") from None
    keys = [str(k).strip() for k in values[settings.REFUSED_TRAILERS]]
    return list(dict.fromkeys(k for k in keys if k))


def _set(root: str, *args: str) -> None:
    done = flag._git(root, "config", *args)
    if done.returncode not in (0, 5):
        raise flag.Failure(f"git config {' '.join(args)} failed in {root}: "
                           f"{done.stderr.strip()}")


def _repo_untouched(root: str) -> bool:
    """Whether repo has written nothing in the repo's git config and hasn't indexed it."""
    from moxi_repo import api

    done = flag._git(root, "config", "--local", "--get-regexp", r"^moxi-repo\.")
    if done.returncode == 0:
        return False
    if done.returncode != 1:
        raise flag.Failure(f"git config can't be read in {root}: {done.stderr.strip()}")
    try:
        return not api.indexed_at(root)
    except api.Failure as exc:
        raise flag.Failure(exc.text, exc.code) from None


class Prepared:
    """What turning clean history on adds in one repo, read before anything is written:
    `added`, the keys of `defaults.refused_trailers` the repo doesn't refuse yet; `named`
    and `sets`, the commit-rules piece and the `refused-trailer` values to register it
    with (none when nothing is added); `rules_absent`, whether commit-rules isn't
    registered; `untouched`, whether repo has written nothing there yet."""

    def __init__(self, root: str) -> None:
        keys = _refused_keys()
        rules = flag.piece_settings(root, COMMIT_RULES)
        current = [v for v in (rules or {}).get(REFUSED_TRAILER, []) if v.strip()]
        held = {v.strip().casefold() for v in current}
        self.added = [key for key in keys if key.casefold() not in held]
        self.rules_absent = rules is None
        self.untouched = _repo_untouched(root)
        self.named = [COMMIT_RULES] if self.added else []
        self.sets = ([f"{REFUSED_TRAILER}={value}" for value in current + self.added]
                     if self.added else [])

    def record(self, root: str) -> None:
        """Write the records `disable` reads, after the pieces are registered."""
        recorded = {v.casefold() for v in flag.config_get_all(root, CLEAN_TRAILERS)}
        for key in self.added:
            if key.casefold() not in recorded:
                _set(root, "--add", CLEAN_TRAILERS, key)
        if self.added and self.rules_absent:
            _set(root, CLEAN_RULES, "true")
        if self.untouched:
            _set(root, CLEAN_HOOKS, "true")

    def lines(self) -> list[str]:
        return ([f"{COMMIT_RULES} refuses {', '.join(self.added)} ({REFUSED_TRAILER})"]
                if self.added else [])


def enable(path: str | os.PathLike) -> list[str]:
    """Turn clean history on in the repo at `path`. Returns the lines to print. Raises
    `flag.Failure`.

    The pieces are registered first and the flag written last, as `moxi publishing
    enable` does: a run that stops between them leaves pieces that refuse every commit
    until the flag is there, never a flag with nothing guarding it."""
    from moxi_repo import api

    root = flag.resolve(path)
    seeds = flag._seeds()
    have = flag.registered(root)
    prepared = Prepared(root)
    given = [f"{key}={value}" for piece in PIECES if piece not in have
             for key, values in seeds.get(piece, {}).items() for value in values]
    try:
        report = api.add(root, *PIECES, *prepared.named, sets=given + prepared.sets)
    except api.Failure as exc:
        raise flag.Failure(exc.text, exc.code) from None
    prepared.record(root)
    for key, value in ((flag.CLEAN, "true"), (flag.INTERPRETER, api.interpreter())):
        done = flag._git(root, "config", key, value)
        if done.returncode != 0:
            raise flag.Failure(f"git config {key} failed in {root}: {done.stderr.strip()}; "
                               "the pieces are registered and refuse every commit until "
                               f"moxi publishing clean-history enable {root} runs again")
    lines = [f"{root} keeps a clean history ({flag.CLEAN})", *report.lines,
             *report.problems, *prepared.lines()]
    warning = flag._names_empty()
    if warning:
        lines.append(warning)
    return lines


def _left_alone(rules: dict[str, list[str]], left: list[str]) -> list[str]:
    """What keeps commit-rules registered once the recorded keys are out: the names of
    the settings still set there beyond a new registration's seed."""
    from moxi_repo import api

    try:
        seed = api.SEEDS[COMMIT_RULES]()
    except api.Failure:
        seed = None
    out = [REFUSED_TRAILER] if left else []
    for name, values in sorted(rules.items()):
        if name == REFUSED_TRAILER:
            continue
        values = [v for v in values if v.strip()]
        if values and (seed is None or values != seed.get(name)):
            out.append(name)
    return out


def _any_piece(root: str) -> bool:
    done = flag._git(root, "config", "--local", "--get-regexp", r"^moxi-repo\.piece\.")
    return done.returncode == 0 and bool(done.stdout.strip())


def disable(path: str | os.PathLike) -> list[str]:
    """Turn clean history off in the repo at `path`. Returns the lines to print. Raises
    `flag.Failure`, with code 3 in a public repo."""
    from moxi_repo import api
    from moxi_repo.api import quote_arg

    root = flag.resolve(path)
    if flag.is_public(root):
        raise flag.Failure("a public repo keeps clean history; run moxi publishing disable "
                           f"{quote_arg(root)} first", 3)
    have = flag.registered(root)
    removing = [piece for piece in PIECES if piece in have]
    taken = {v.strip().casefold() for v in flag.config_get_all(root, CLEAN_TRAILERS)}
    ours = flag.config_get(root, CLEAN_RULES) == "true"
    brought = flag.config_get(root, CLEAN_HOOKS) == "true"
    rules = flag.piece_settings(root, COMMIT_RULES)
    rewrite, kept = None, []
    if rules is not None:
        current = [v for v in rules.get(REFUSED_TRAILER, []) if v.strip()]
        left = [v for v in current if v.strip().casefold() not in taken]
        kept = _left_alone(rules, left)
        if ours and not kept:
            removing.append(COMMIT_RULES)
        elif len(left) != len(current):
            rewrite = left
    lines = []
    try:
        if removing:
            report = api.remove(root, *removing)
            lines += report.lines + report.problems
        if rewrite is not None:
            sets = [f"{REFUSED_TRAILER}={value}" for value in rewrite] or [
                f"{REFUSED_TRAILER}="]
            report = api.add(root, COMMIT_RULES, sets=sets)
            lines += report.lines + report.problems
        if brought and not _any_piece(root):
            report = api.remove(root)
            lines += report.lines + report.problems
    except api.Failure as exc:
        raise flag.Failure(exc.text, exc.code) from None
    if ours and rules is not None and COMMIT_RULES not in removing:
        lines.append(f"kept {COMMIT_RULES}: {', '.join(kept)} is set there beyond what "
                     "clean history wrote")
    for key in (flag.CLEAN, CLEAN_TRAILERS, CLEAN_RULES, CLEAN_HOOKS, CLEAN_WITH_PUBLIC,
                flag.INTERPRETER):
        _set(root, "--unset-all", key)
    lines.append(f"{root} no longer keeps a clean history")
    return lines


__all__ = ["CLEAN_TRAILERS", "CLEAN_RULES", "CLEAN_HOOKS", "CLEAN_WITH_PUBLIC", "Prepared", "PIECES", "COMMIT_RULES", "REFUSED_TRAILER",
           "enable", "disable"]
