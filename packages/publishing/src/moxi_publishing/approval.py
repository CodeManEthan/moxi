"""Push approval: the approval piece, `publishing/approval`, and the verbs that set it.

    moxi publishing approval enable PATH --branch PATTERN [--branch PATTERN]...
    moxi publishing approval disable PATH

The piece runs on pre-push, fail closed, order 40, in any repo it is registered in: it
reads no public or clean flag and assumes no branch names or CI. Its one setting,
`branch`, holds patterns over a branch's name (`main`, not `refs/heads/main`) in which
`*`, `?` and `[...]` are wildcards; `*` alone, which also matches a `/`, is every branch.

A ref line is guarded when it creates, moves or deletes a branch a pattern names, or any
tag (`refs/tags/...`), whatever the patterns name. When the push runs inside an agent's
session (the core's agent test, `moxi.terminal.agent()`) and carries a guarded line, it
needs `MOXI_PUBLISHING_PUSH_WORD`, matching publishing's `push.word_pattern` in full; a
blank one is none. A push from the user's own terminal, or from a Moxi unit, needs no word.

A push that passes is logged in publishing's push log, `pushes.jsonl`, as the push piece
logs one, with the word or null and the harness the agent test named, or null. Where the
push piece is enabled in the same repo it logs the push, word included, so this piece
writes nothing there. A log that can't be written refuses the push.

`enable` registers the piece with the patterns given, replacing those it had; `disable`
removes it.

Standard library only at import; the agent test and the config load when the piece runs.
"""

from __future__ import annotations

import os

#: The piece's declaration, read through the entry point `publishing.approval`.
APPROVAL = {
    "event": "pre-push",
    "target": "moxi_publishing.approval:pre_push",
    "fail": "closed",
    "order": 40,
    "doc": "Ask an agent's push for the word where it moves a branch the repo names, or "
           "any tag.",
    "settings": {"branch": "multi"},
}

PIECE_ID = "publishing/approval"
BRANCH = "branch"
_HEADS = "refs/heads/"
_TAGS = "refs/tags/"
#: The push piece, whose own log record covers a push where it is enabled.
_PUSH = "publishing/push"


def patterns_of(settings: dict) -> list[str]:
    """The piece's `branch` patterns, blank values left out."""
    return [v.strip() for v in settings.get(BRANCH, ()) if v.strip()]


def guarded(remote_ref: str, patterns: list[str]) -> bool:
    """Whether a ref line to `remote_ref` needs the word in an agent's push: a tag, or a
    branch whose name a pattern matches."""
    from fnmatch import fnmatchcase

    if remote_ref.startswith(_TAGS):
        return True
    if not remote_ref.startswith(_HEADS):
        return False
    name = remote_ref[len(_HEADS):]
    return any(fnmatchcase(name, pattern) for pattern in patterns)


def _push_piece_runs(ctx) -> bool:
    done = ctx.git("config", "--get-regexp",
                   r"^moxi-repo\.piece\.publishing/push\.(event|enabled)$")
    if done.returncode != 0:
        return False
    values = dict(line.partition(" ")[::2] for line in done.stdout.splitlines())
    keys = {key.rpartition(".")[2]: value for key, value in values.items()}
    return "event" in keys and keys.get("enabled", "true").strip().lower() not in (
        "false", "no", "off", "0")


def judge(ctx, *, harness: str | None) -> list[str]:
    """Why the push in `ctx` can't go, or nothing, for a push the agent test reads as run
    in `harness`'s session (None: not an agent's)."""
    import re

    from moxi_publishing import settings
    from moxi_publishing.push import WORD_ENV

    if harness is None:
        return []
    patterns = patterns_of(ctx.settings)
    needs = [u.remote_ref for u in ctx.updates if guarded(u.remote_ref, patterns)]
    if not needs:
        return []
    from moxi_publishing.push import given_word

    word = given_word()
    shown = ", ".join(needs[:3]) + (f" and {len(needs) - 3} more" if len(needs) > 3 else "")
    if word is None:
        return [f"this push runs in a {harness} session and moves {shown}, which this "
                f"repo's push approval guards: set {WORD_ENV} to where the push was "
                "approved"]
    pattern = settings.load()[settings.WORD_PATTERN]
    if re.fullmatch(pattern, word) is None:
        return [f"{WORD_ENV} does not match push.word_pattern ({pattern})"]
    return []


def _record(ctx, harness: str | None) -> dict:
    import datetime

    from moxi_publishing.push import _shown_url, given_word

    remote = ctx.args[0] if ctx.args else ""
    url = ctx.args[1] if len(ctx.args) > 1 else remote
    return {"time": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "repo": ctx.root, "remote": _shown_url(remote), "url": _shown_url(url),
            "word": given_word(), "agent": harness, "piece": PIECE_ID,
            "refs": [{"local_ref": u.local_ref, "local_sha": u.local_sha,
                      "remote_ref": u.remote_ref, "remote_sha": u.remote_sha}
                     for u in ctx.updates]}


def pre_push(ctx):
    """The piece."""
    from moxi_repo.api import Outcome

    try:
        import moxi.terminal

        harness = moxi.terminal.agent()
        reasons = judge(ctx, harness=harness)
    except Exception as exc:  # noqa: BLE001 - every failure to judge refuses, named
        return Outcome.refuse(f"this push's approval can't be checked, so it is refused: "
                              f"{exc}")
    if reasons:
        return Outcome.refuse(*reasons, "Nothing was pushed.")
    if not _push_piece_runs(ctx):
        from moxi_publishing.push import _log

        try:
            _log(_record(ctx, harness))
        except (OSError, ValueError) as exc:
            return Outcome.refuse(f"the push log can't be written, so the push is "
                                  f"refused: {exc}")
    return Outcome.ok()


# -- enable, disable ---------------------------------------------------------------------

def _pattern_problem(pattern: str) -> str | None:
    if not pattern.strip():
        return "a pattern can't be blank"
    if pattern.startswith("refs/"):
        return (f"{pattern!r} is a ref name; a pattern names the branch, as "
                f"{pattern.removeprefix(_HEADS)!r}")
    return None


def enable(path: str | os.PathLike, branches: list[str] | tuple) -> list[str]:
    """Register the approval piece in the repo at `path` guarding `branches`, replacing
    the patterns it had. Returns the lines to print. Raises `flag.Failure`."""
    from moxi_repo import api

    from moxi_publishing import flag

    if not branches:
        raise flag.Failure("name the branches to guard with --branch PATTERN ('*' is "
                           "every branch); nothing was written", 2)
    for pattern in branches:
        why = _pattern_problem(pattern)
        if why is not None:
            raise flag.Failure(f"--branch: {why}; nothing was written", 2)
    root = flag.resolve(path)
    try:
        report = api.add(root, PIECE_ID, sets=[f"{BRANCH}={p}" for p in branches])
    except api.Failure as exc:
        raise flag.Failure(exc.text, exc.code) from None
    return [*report.lines, *report.problems,
            f"an agent's push of {' '.join(branches)}, or of any tag, needs the word in "
            f"{root}"]


def disable(path: str | os.PathLike) -> list[str]:
    """Remove the approval piece from the repo at `path`. Returns the lines to print.
    Raises `flag.Failure`."""
    from moxi_repo import api

    from moxi_publishing import flag

    root = flag.resolve(path)
    if PIECE_ID not in flag.registered(root):
        return [f"{PIECE_ID} is not registered in {root}; nothing to remove"]
    try:
        report = api.remove(root, PIECE_ID)
    except api.Failure as exc:
        raise flag.Failure(exc.text, exc.code) from None
    return [*report.lines, *report.problems]


__all__ = ["APPROVAL", "PIECE_ID", "BRANCH", "patterns_of", "guarded", "judge",
           "pre_push", "enable", "disable"]
