"""The message piece, `publishing/message`: a clean or public repo's commit messages.

It runs on commit-msg, fail closed, order 90, so repo's own commit-msg pieces (order 10
and 20) read the message before it, and the repo's existing git hook reads it after. On
every branch, detached HEAD, merges and replays alike, it reads every line above git's
scissors line, comment lines included, and:

- drops each trailer line whose key is on the repo's `drop-trailer` setting, with the
  lines that continue its value, and rewrites the message file. It removes whole lines
  only, and prints one line per key it dropped;
- leaves each trailer line whose key is on `pass-trailer` as it is: a later git hook reads
  it and removes it. A key on both settings is never dropped. The provenance rules skip
  such a line, since its value (a check hash, say) is that hook's;
- refuses a line holding a hit of secrets' `credentials` or `private-names` set, or of a
  provenance rule that applies to messages. A provenance hit is shown with its text, cut
  at 100 characters; a secrets hit with its rule only, so a credential is never printed.

(The hook can't see git's cleanup mode, and git drops the lines below the scissors line
only for an edited message. The push piece and `scan` read a made commit or tag whole.)

A trailer line is one git's trailer parser reads as one: a key of ASCII letters, digits
and `-` at the start of the line, then spaces or tabs, then `:`; a `:` followed by `//`
(a URL) is no separator. Keys match ignoring case.

The piece first reads the clean and public flags; registered in a repo with neither, it
refuses, since whoever registered it meant the repo to be guarded. A public repo always
keeps clean history, so the public flag alone is enough.

Standard library only at import; secrets and the rules load when the piece runs.
"""

from __future__ import annotations

import os
import re
import sys

#: The piece's declaration, read through the entry point `publishing.message`.
MESSAGE = {
    "event": "commit-msg",
    "target": "moxi_publishing.message:commit_msg",
    "fail": "closed",
    "order": 90,
    "doc": "In a clean or public repo, drop the trailers you name and refuse a message "
           "holding a credential, a private name or a provenance pointer.",
    "settings": {"drop-trailer": "multi", "pass-trailer": "multi"},
}

PIECE_ID = "publishing/message"
DROP_TRAILER = "drop-trailer"
PASS_TRAILER = "pass-trailer"
#: The sets of secrets a message is scanned with.
SETS = ("credentials", "private-names")
#: How much of a refused line is shown.
SHOWN_CHARS = 100

#: The scissors line follows the comment prefix and a space.
_SCISSORS = "------------------------ >8 ------------------------"
_KEY = re.compile(r"([A-Za-z0-9-]+)[ \t]*:(?!//)")


def trailer_key(line: str) -> str | None:
    """The key of `line` when git's parser reads it as a trailer, else None."""
    match = _KEY.match(line)
    return match.group(1) if match else None


def _keys(values) -> set[str]:
    return {value.strip().lower() for value in values if value.strip()}


def above_scissors(text: str, comment: str) -> list[str]:
    """The lines of `text` above git's scissors line, each without its line end."""
    scissors = f"{comment} {_SCISSORS}"
    out = []
    for line in text.split("\n"):
        if line.rstrip("\r") == scissors:
            break
        out.append(line)
    return out


class Judged:
    """What the piece made of one message: `text`, the message with the dropped lines
    gone; `dropped`, the keys it dropped in the spelling of their first line, in order;
    `refusals`, `(line number, line to print)` in line order."""

    __slots__ = ("text", "dropped", "refusals")

    def __init__(self, text: str, dropped: list[str], refusals: list[tuple[int, str]]):
        self.text, self.dropped, self.refusals = text, dropped, refusals


def judge(text: str, *, drop=(), passed=(), comment: str = "#", rules=None,
          sets=SETS, whole: bool = False) -> Judged:
    """Judge a commit message as the piece does. `drop` and `passed` are the trailer keys
    of the repo's `drop-trailer` and `pass-trailer` settings; `rules`, the provenance rules
    that run (None: publishing's config); `sets`, the secrets sets to scan with. Line
    numbers count the message as git gave it. With `whole`, every line is read, scissors
    line or not: a message already made into a commit or tag object keeps whatever git's
    cleanup left below that line (`-m`, `-F` and verbatim cleanup leave it all). Raises
    `moxi_secrets.SetUnavailable` when a set can't run."""
    from moxi_publishing import rules as _rules

    passed_keys = _keys(passed)
    drop_keys = _keys(drop) - passed_keys
    names = _rules.enabled_rules() if rules is None else tuple(rules)
    lines = text.split("\n")
    above = len(lines) if whole else len(above_scissors(text, comment))
    kept: list[tuple[int, str]] = []
    gone: set[int] = set()
    dropped: list[str] = []
    dropping = False
    for index, line in enumerate(lines[:above]):
        bare = line.rstrip("\r")
        if dropping and bare[:1] in (" ", "\t") and bare.strip():
            gone.add(index)
            continue
        dropping = False
        key = trailer_key(bare)
        if key is not None and key.lower() in drop_keys:
            gone.add(index)
            dropping = True
            if key.lower() not in {k.lower() for k in dropped}:
                dropped.append(key)
            continue
        kept.append((index + 1, bare))
    from moxi_publishing.content import masked

    refusals: list[tuple[int, str]] = []
    for number, line in kept:
        key = trailer_key(line)
        if key is not None and key.lower() in passed_keys:
            continue
        for rule, _ in _rules.hits(line, _rules.MESSAGES, names):
            shown = _shown(masked(line, sets))
            refusals.append((number, f"line {number}, {rule}: {shown}"))
    if sets:
        from moxi_secrets import scan_lines

        for hit in scan_lines([(None, number, line) for number, line in kept], sets):
            refusals.append((hit.line, f"line {hit.line}, {hit.hit.set}/{hit.hit.rule}"))
    refusals.sort(key=lambda item: item[0])
    out = "\n".join(line for index, line in enumerate(lines) if index not in gone)
    return Judged(out, dropped, refusals)


def _shown(line: str) -> str:
    return line if len(line) <= SHOWN_CHARS else line[:SHOWN_CHARS - 3] + "..."


def is_public(ctx) -> bool:
    """Whether the repo's git config reads `moxi-publishing.public` as true."""
    from moxi_publishing.flag import FLAG

    done = ctx.git("config", "--type=bool", "--get", FLAG)
    return done.returncode == 0 and done.stdout.strip() == "true"


def keeps_clean(ctx) -> bool:
    """Whether the repo's git config reads `moxi-publishing.clean` or
    `moxi-publishing.public` as true: a public repo always keeps clean history."""
    from moxi_publishing.flag import CLEAN

    done = ctx.git("config", "--type=bool", "--get", CLEAN)
    return (done.returncode == 0 and done.stdout.strip() == "true") or is_public(ctx)


def no_flag(ctx, piece_id: str, *, clean: bool = False):
    """The refusal of a piece registered in a repo without its flag: the public flag, or
    with `clean` the clean or public flag."""
    from moxi_repo.api import Outcome, quote_arg

    root = quote_arg(ctx.root or ".")
    if clean:
        return Outcome.refuse(
            f"{piece_id} is registered here but the repo is marked neither clean nor "
            "public (moxi-publishing.clean, moxi-publishing.public); keep its history "
            f"clean with: moxi publishing clean-history enable {root}, or mark it "
            f"public with: moxi publishing enable {root}; or take the pieces out "
            f"with: moxi publishing clean-history disable {root}")
    return Outcome.refuse(
        f"{piece_id} is registered here but the repo isn't marked public "
        "(moxi-publishing.public); guard it with: moxi publishing enable "
        f"{root}, or take the pieces out with: moxi publishing disable {root}")


def _next_step(ctx) -> str:
    git_dir = ctx.git_dir or ""
    if os.path.exists(os.path.join(git_dir, "MERGE_HEAD")):
        return ("Nothing was committed, and the merge is still in progress: run git commit "
                "again with those lines reworded, or git merge --abort.")
    if any(os.path.exists(os.path.join(git_dir, name))
           for name in ("rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD")):
        return ("Nothing was committed: reword those lines, commit again, then continue "
                "the rebase, cherry-pick or revert.")
    return "Nothing was committed: reword those lines and commit again."


def commit_msg(ctx):
    """The piece."""
    from moxi_repo.api import Outcome
    from moxi_repo.api import comment_prefix

    if not keeps_clean(ctx):
        return no_flag(ctx, PIECE_ID, clean=True)
    with open(ctx.message_path, "rb") as fh:
        text = fh.read().decode("utf-8", "surrogateescape")
    try:
        judged = judge(text, drop=ctx.settings.get(DROP_TRAILER, ()),
                       passed=ctx.settings.get(PASS_TRAILER, ()),
                       comment=comment_prefix(ctx))
    except Exception as exc:  # noqa: BLE001 - every failure to judge refuses, named
        return Outcome.refuse(f"the message can't be checked, so it is refused: {exc}")
    if judged.refusals:
        return Outcome.refuse(
            "this repo keeps a clean history, and these lines of the message can't be "
            "published:",
            *(line for _, line in judged.refusals), _next_step(ctx))
    if judged.dropped:
        from moxi.files import atomic_write

        atomic_write(ctx.message_path, judged.text.encode("utf-8", "surrogateescape"),
                     mode=os.stat(ctx.message_path).st_mode & 0o777)
        for key in judged.dropped:
            print(f"publishing: dropped the {key} line: this repo keeps a clean history "
                  "and drops it at commit", file=sys.stderr)
    return Outcome.ok()


__all__ = ["MESSAGE", "PIECE_ID", "DROP_TRAILER", "PASS_TRAILER", "Judged", "judge",
           "trailer_key", "above_scissors", "is_public", "keeps_clean", "no_flag", "commit_msg"]
