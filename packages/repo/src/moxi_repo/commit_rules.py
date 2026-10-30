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

- **Trailer shape.** The trailer block is the message's last paragraph when git's own
  parser reads it as trailers: every line a trailer, or at least a quarter of them with one
  a `Signed-off-by:`, a cherry-pick line or a key set in the repo's `trailer.*` config. A
  last paragraph git doesn't read that way is refused only when it holds a trailer whose
  key the piece knows as one (hyphenated, refused, or set in `trailer.*` config), since git
  then reads none of its trailers; otherwise it is prose (`Note: check length first.`). A
  line whose `:` is followed by `//` (a URL) is never a trailer. A line outside the block
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

#: The separator git reads between a trailer's key and value when `trailer.separators`
#: sets none.
SEPARATORS = ":"
#: The trailer lines git writes itself; one of them in a paragraph makes git read it as
#: trailers when a quarter of its lines are.
_GENERATED = ("Signed-off-by: ", "(cherry picked from commit ")
#: What git writes on a cherry-pick with -x; its parser lets it sit among trailers.
_CHERRY = "(cherry picked from commit "
#: The scissors line follows the comment prefix and a space.
_SCISSORS = "------------------------ >8 ------------------------"


def commit_msg(ctx) -> Outcome:
    """The piece: read the message file and refuse it if it breaks a rule."""
    with open(ctx.message_path, "rb") as fh:
        text = fh.read().decode("utf-8", "replace")
    comment, configured, separators = git_settings(ctx)
    problems = check_message(text, refused=ctx.settings.get(REFUSED_TRAILER, ()),
                             patterns=ctx.settings.get(TAG_PATTERN, ()),
                             comment=comment, root=ctx.root, configured=configured,
                             separators=separators)
    if problems:
        return Outcome.refuse(*problems)
    return Outcome.ok()


def git_settings(ctx) -> tuple[str, list[str], str]:
    """What the repo's git config says, in one `git config` call: the comment prefix
    (`core.commentString` or `core.commentChar`, the last one set wins; `#` when neither
    is set or it is `auto`), the trailer keys its `trailer.*` config names (each
    `trailer.<key>.*` and each `trailer.<name>.key` value), and `trailer.separators`."""
    done = ctx.git("config", "--get-regexp", r"^(core\.comment(char|string)|trailer\..*)$")
    prefix, configured, separators = "#", [], SEPARATORS
    if done.returncode != 0:
        return prefix, configured, separators
    for line in done.stdout.splitlines():
        name, _, value = line.partition(" ")
        if name in ("core.commentchar", "core.commentstring"):
            if value and value != "auto":
                prefix = value
        elif name == "trailer.separators":
            separators = value or SEPARATORS
        elif name.startswith("trailer.") and name.count(".") >= 2:
            key, var = name[len("trailer."):].rsplit(".", 1)
            configured.append(key)
            if var == "key" and value.strip():
                configured.append(value.strip().rstrip(separators + " \t"))
    return prefix, configured, separators


def comment_prefix(ctx) -> str:
    """The repo's comment prefix, as `git_settings` reads it."""
    return git_settings(ctx)[0]


def pattern_problem(pattern: str) -> str | None:
    """Why `pattern` can't be a tag pattern, or None. A pattern that matches the empty
    string is refused: it would find an empty match at the start of every line and check
    nothing. The empty pattern itself means no pattern."""
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"{pattern!r} is not a regular expression ({exc})"
    if pattern and regex.search("") is not None:
        return (f"{pattern!r} matches the empty string, so it would check nothing; "
                "make it match at least one character")
    return None


def check_message(text: str, *, refused=(), patterns=(), comment: str = "#",
                  root: str | None = None, configured=(),
                  separators: str = SEPARATORS) -> list[str]:
    """Every rule `text` breaks, one line each, in the order the lines appear; empty when
    it passes. Line numbers count the message as written, comment lines included.
    `configured` holds the trailer keys the repo's `trailer.*` config names, and
    `separators` its `trailer.separators`."""
    lines = _kept_lines(text, comment)
    paragraphs = _paragraphs(lines)
    refused_keys = {k.lower() for k in refused if k}
    configured_keys = {k.lower() for k in configured if k}
    shape = _Shape(separators or SEPARATORS, configured_keys)
    block, read_by_git = _trailer_block(paragraphs, shape, refused_keys | configured_keys)
    problems: list[tuple[int, str]] = []

    in_block: set[int] = set()  # the block's trailer lines, which no tag check reads
    block_keys: set[str] = set()
    trailer = None  # the key of the trailer a continuation line would belong to
    for n, line in block:
        if line.startswith(_CHERRY):
            trailer = None
            in_block.add(n)
            continue
        if line[:1] in (" ", "\t") and trailer is not None:
            trailer[1].append(line.strip())
            in_block.add(n)
            continue
        found = shape.trailer(line)
        if found is None:
            trailer = None
            if not read_by_git:
                problems.append((n, f"trailer shape: line {n} is prose in the trailer block "
                                    "(the last paragraph); a trailer block holds only "
                                    "'Key: value' lines, so put a blank line before the "
                                    "trailers"))
            continue
        in_block.add(n)
        trailer = (n, [found[1]], found[0])
        block_keys.add(found[0].lower())
        problems += _trailer_problems(trailer, refused_keys, block, n)

    block_lines = {n for n, _ in block}
    for n, line in lines:
        if n in block_lines:
            continue
        found = shape.trailer(line)
        if found is None:
            continue
        key = found[0]
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


class _Shape:
    """How git's trailer parser reads a line."""

    def __init__(self, separators: str, configured: set[str]):
        self.separators = separators
        self.configured = configured

    def separator(self, line: str) -> int:
        """Where the separator is, or -1: a key of ASCII letters, digits and `-`, then
        spaces or tabs, then one of the separators, as git's `find_separator` reads it."""
        spaced = False
        for i, c in enumerate(line):
            if c in self.separators:
                return i
            if not spaced and c.isascii() and (c.isalnum() or c == "-"):
                continue
            if i and c in " \t":
                spaced = True
                continue
            break
        return -1

    def trailer(self, line: str) -> tuple[str, str] | None:
        """(key, value) when git reads `line` as a trailer, else None. A separator `:`
        followed by `//` (a URL) is not one."""
        at = self.separator(line)
        if at < 1 or line[:1] in (" ", "\t"):
            return None
        if line[at] == ":" and line[at + 1:at + 3] == "//":
            return None
        return line[:at].strip(), line[at + 1:].strip()

    def recognised(self, line: str) -> bool:
        """A line git counts towards reading a paragraph as trailers with prose in it: one
        it writes itself, or one whose key names a `trailer.*` entry (git compares the
        key as a prefix of the entry, ignoring case)."""
        if line.startswith(_GENERATED):
            return True
        found = self.trailer(line)
        if found is None:
            return False
        token = line[:self.separator(line)].lower()
        return any(name.startswith(token) for name in self.configured)


def _read_by_git(paragraph, shape: _Shape) -> bool:
    """Whether git's parser reads `paragraph` as a trailer block: every line a trailer
    (or a continuation of one), or a recognised line and at least a quarter of the lines
    trailers."""
    trailers = prose = continuing = 0
    recognised = False
    for _, line in reversed(paragraph):
        if line.startswith(_GENERATED):
            trailers += 1
            continuing = 0
            recognised = True
        elif shape.trailer(line) is not None:
            trailers += 1
            continuing = 0
            recognised = recognised or shape.recognised(line)
        elif line[:1] in (" ", "\t"):
            continuing += 1
        else:
            prose += 1 + continuing
            continuing = 0
    prose += continuing
    return bool(trailers and not prose) or (recognised and trailers * 3 >= prose)


def _trailer_block(paragraphs, shape: _Shape, known: set[str]
                   ) -> tuple[list[tuple[int, str]], bool]:
    """(the trailer block, whether git reads it as one). The block is the last paragraph,
    never the subject's, when git reads it as trailers, or when it holds a trailer whose
    key is known as one (hyphenated, refused or configured) though git doesn't read it;
    else empty."""
    if len(paragraphs) < 2:
        return [], False
    last = paragraphs[-1]
    if _read_by_git(last, shape):
        return last, True
    for _, line in last:
        found = shape.trailer(line)
        if found is not None and ("-" in found[0] or found[0].lower() in known):
            return last, False
    return [], False


__all__ = ["COMMIT_RULES", "PIECE_ID", "check_message", "commit_msg", "git_settings",
           "pattern_problem"]
