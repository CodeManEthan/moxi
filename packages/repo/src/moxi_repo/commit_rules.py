"""The commit-rules piece, `repo/commit-rules`: a commit message's trailer shape, and no tags.

It runs on commit-msg, fail closed, order 10, and is in no repo until someone adds it:

    moxi repo git-hooks add PATH repo/commit-rules \\
        --set refused-trailer=KEY --set tag-pattern=REGEX

Its two settings live in that repo's git config, each with any number of values:

- `refused-trailer`: trailer keys this repo refuses, matched ignoring case;
- `tag-pattern`: regular expressions (Python's `re`) a tag matches. With none, there is no
  tag check. A repo that has none when the piece is first added to it starts with the
  module config's `commit_rules.tag_patterns`; later changes to that config change no repo.

What it checks, refusing with the rule named:

- **Trailer shape.** When the message's last paragraph holds any trailer (`Key: value`),
  it holds nothing but trailers, so git's parser reads them all. A line outside that block
  whose key is one of the block's keys, or a refused key, is a trailer stranded in the
  body. Every trailer has a value. A refused key is refused.
- **No tags.** The subject and body, without the trailer block, comment lines and anything
  below the scissors line (commit-msg sees the message before git cleans it up), are
  matched against the repo's tag patterns.

The piece runs inside the git hook, so this module imports the standard library only.
"""

from __future__ import annotations

import re

from moxi_repo.pieces import Outcome

#: The piece's declaration, read through the entry point `repo.commit-rules`.
COMMIT_RULES = {
    "event": "commit-msg",
    "target": "moxi_repo.commit_rules:commit_msg",
    "fail": "closed",
    "order": 10,
    "doc": "Check a commit message's trailer shape, and refuse tags in its subject and body.",
    "settings": {"refused-trailer": "multi", "tag-pattern": "multi"},
}

PIECE_ID = "repo/commit-rules"
REFUSED_TRAILER = "refused-trailer"
TAG_PATTERN = "tag-pattern"

#: A trailer line: a key of letters, digits and `-`, then `:`, as git's parser reads one.
_TRAILER = re.compile(r"([A-Za-z0-9][A-Za-z0-9-]*)[ \t]*:[ \t]*(.*)")
#: What git writes on a cherry-pick with -x; its parser lets it sit among trailers.
_CHERRY = "(cherry picked from commit "
#: The scissors line follows the comment prefix and a space.
_SCISSORS = "------------------------ >8 ------------------------"


def commit_msg(ctx) -> Outcome:
    """The piece: read the message file and refuse it if it breaks a rule."""
    with open(ctx.message_path, "rb") as fh:
        text = fh.read().decode("utf-8", "replace")
    problems = check_message(text, refused=ctx.settings.get(REFUSED_TRAILER, ()),
                             patterns=ctx.settings.get(TAG_PATTERN, ()),
                             comment=comment_prefix(ctx), root=ctx.root)
    if problems:
        return Outcome.refuse(*problems)
    return Outcome.ok()


def comment_prefix(ctx) -> str:
    """The repo's comment prefix (`core.commentString` or `core.commentChar`, the last one
    set wins), `#` when neither is set or it is `auto`."""
    done = ctx.git("config", "--get-regexp", r"^core\.comment(char|string)$")
    prefix = "#"
    if done.returncode == 0:
        for line in done.stdout.splitlines():
            _, _, value = line.partition(" ")
            if value and value != "auto":
                prefix = value
    return prefix


def pattern_problem(pattern: str) -> str | None:
    """Why `pattern` can't be a tag pattern, or None."""
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"{pattern!r} is not a regular expression ({exc})"
    return None


def check_message(text: str, *, refused=(), patterns=(), comment: str = "#",
                  root: str | None = None) -> list[str]:
    """Every rule `text` breaks, one line each, in the order the lines appear; empty when
    it passes. Line numbers count the message as written, comment lines included."""
    lines = _kept_lines(text, comment)
    paragraphs = _paragraphs(lines)
    block = _trailer_block(paragraphs)
    in_block = {n for n, _ in block}
    refused_keys = {k.lower() for k in refused if k}
    problems: list[tuple[int, str]] = []

    block_keys: set[str] = set()
    trailer = None  # the key of the trailer a continuation line would belong to
    for n, line in block:
        if line.startswith(_CHERRY):
            trailer = None
            continue
        if line[:1] in (" ", "\t") and trailer is not None:
            trailer[1].append(line.strip())
            continue
        match = _TRAILER.fullmatch(line)
        if match is None:
            trailer = None
            problems.append((n, f"trailer shape: line {n} is prose in the trailer block "
                                "(the last paragraph); a trailer block holds only "
                                "'Key: value' lines, so put a blank line before the "
                                "trailers"))
            continue
        trailer = (n, [match.group(2).strip()], match.group(1))
        block_keys.add(match.group(1).lower())
        problems += _trailer_problems(trailer, refused_keys, block, n)

    for n, line in lines:
        if n in in_block:
            continue
        match = _TRAILER.fullmatch(line)
        if match is None:
            continue
        key = match.group(1)
        if key.lower() in refused_keys:
            problems.append((n, f"refused trailer: line {n} has {key}, which this repo "
                                "refuses; remove it"))
        elif key.lower() in block_keys:
            problems.append((n, f"trailer shape: line {n} has the trailer {key} stranded "
                                "in the body; move it into the trailer block, the last "
                                "paragraph"))

    compiled = []
    for pattern in patterns:
        if not pattern:
            continue
        why = pattern_problem(pattern)
        if why is not None:
            where = root or "PATH"
            problems.append((0, f"tag-pattern {why}; fix it with: moxi repo git-hooks add "
                                f"{where} {PIECE_ID} --set tag-pattern=REGEX"))
            continue
        compiled.append(re.compile(pattern))
    if compiled:
        for n, line in lines:
            if n in in_block:
                continue
            for regex in compiled:
                found = regex.search(line)
                if found and found.group(0):
                    problems.append((n, f"no tags: line {n} has \"{found.group(0)}\"; "
                                        "say it in plain words"))
                    break
    problems.sort(key=lambda p: p[0])
    return [text for _, text in problems]


def _trailer_problems(trailer, refused_keys, block, n) -> list[tuple[int, str]]:
    """An empty value is found once its continuation lines are read, so this looks ahead."""
    _, values, key = trailer
    out = []
    if key.lower() in refused_keys:
        out.append((n, f"refused trailer: line {n} has {key}, which this repo refuses; "
                       "remove it"))
    if not values[0] and not _continues(block, n):
        out.append((n, f"trailer shape: line {n}: the trailer {key} has no value"))
    return out


def _continues(block, n) -> bool:
    """True when the lines after line `n` in the block continue its value."""
    after = False
    for m, line in block:
        if m == n:
            after = True
            continue
        if after:
            return line[:1] in (" ", "\t") and bool(line.strip())
    return False


def _kept_lines(text: str, comment: str) -> list[tuple[int, str]]:
    """(line number, line) for each line git keeps: no comment line, nothing from the
    scissors line down, no blank lines at the end."""
    scissors = f"{comment} {_SCISSORS}"
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if line == scissors:
            break
        if comment and line.startswith(comment):
            continue
        out.append((n, line.rstrip()))
    while out and not out[-1][1]:
        out.pop()
    return out


def _paragraphs(lines: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    out: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for n, line in lines:
        if line.strip():
            current.append((n, line))
        elif current:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out


def _trailer_block(paragraphs) -> list[tuple[int, str]]:
    """The last paragraph when it holds a trailer and is not the subject's; else empty."""
    if len(paragraphs) < 2:
        return []
    last = paragraphs[-1]
    if any(_TRAILER.fullmatch(line) for _, line in last):
        return last
    return []


__all__ = ["COMMIT_RULES", "PIECE_ID", "check_message", "commit_msg", "pattern_problem"]
