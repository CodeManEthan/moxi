"""The tag check: no ruling, design, review or build tags in the repository's files.

Code, tests and docs say a reason in plain words. A tag is the shorthand of the notes a
change was planned in, which a reader of the repository cannot follow: a short capital id
with a number in square brackets, one of a few capital letters with a number in
parentheses, a decision id (a D and two digits, perhaps a letter after), a section sign
with a number, a design version, a severity with a number, a round named by its home
and slug, one of the two hyphenated (two plain words with a slash between, after
"round", are as likely prose), or a finding id, an L and a number, bare or bracketed.
No example of one is written here, so this file passes its own check.

Two kinds of text are let through. A finding mark's arguments,
`@pytest.mark.finding("L50")`, are data a tool reads, and a finding id is let through
there and nowhere else: in a comment, a docstring or a string it is a tag like the rest,
so a label of that shape (a numbered rule, say) is written another way. And a line of
the root's
`.residue-allow`, `<path> TAB <exact text> TAB <why>`, lets that text stand in that file,
for a tag that is itself data (a pattern's test input, say); the repository's commit scan
reads the same file.

`scan(root)` reads every tracked and untracked-but-not-ignored file that is UTF-8 text,
and returns what it found.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: The shapes of a tag.
TAG = re.compile(
    r"\[[A-Z]{1,2}[0-9]{1,3}[a-z]?\]"
    r"|\([RSCFQNBIX][0-9]+\)"
    r"|\bD[0-9]{2}[a-z]?\b"
    r"|\u00a7 ?[0-9]"
    r"|\bdesign v[0-9]"
    r"|\b(?:MINOR|MAJOR|BLOCKER) [0-9]"
    r"|\b[Rr]ound (?:[a-z0-9]+-[a-z0-9-]+/[a-z0-9-]+|[a-z0-9-]+/[a-z0-9]+-[a-z0-9-]+)"
    r"|\bL[0-9]+\b"
)

#: A finding mark with its arguments, one or more strings, which are data. The call may
#: run over several lines, as a formatter wraps it; each string stays on one line.
FINDING_MARK = re.compile(
    r"""mark\.finding\(\s*(["'])[^"'\n]*\1(?:\s*,\s*(["'])[^"'\n]*\2)*\s*,?\s*\)""")

#: The file that declares allowed text, and files that are not prose or code.
ALLOW_FILE = ".residue-allow"
SKIPPED = frozenset({ALLOW_FILE, "uv.lock"})


@dataclass(frozen=True, order=True)
class Hit:
    path: str
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: tag {self.text!r}"


def allowed(root: Path) -> set[tuple[str, str]]:
    """The (path, exact text) pairs `.residue-allow` lets stand."""
    try:
        raw = (root / ALLOW_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    pairs = set()
    for line in raw.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            pairs.add((parts[0], parts[1]))
    return pairs


def _empty_mark(match: re.Match[str]) -> str:
    """A finding mark with its arguments taken out and its line breaks kept."""
    return "mark.finding(" + "\n" * match.group(0).count("\n") + ")"


def tags_in(text: str) -> list[tuple[int, str]]:
    """Each tag in `text`, with its line number; a finding mark's arguments excepted.

    The marks are taken out of the whole text before it is split into lines, so a mark
    wrapped over lines passes as a one-line mark does, and every line keeps its number.
    """
    text = FINDING_MARK.sub(_empty_mark, text)
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        found += [(number, match.group(0)) for match in TAG.finditer(line)]
    return found


def _files(root: Path) -> list[str]:
    done = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                           "--exclude-standard"], cwd=root, capture_output=True, check=True)
    return sorted({p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0") if p})


def scan(root: Path) -> list[Hit]:
    """Every tag in the repository at `root` that `.residue-allow` does not let stand."""
    root = Path(root)
    allow = allowed(root)
    hits = []
    for path in _files(root):
        if path in SKIPPED or not (root / path).is_file():
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits += [Hit(path, number, tag) for number, tag in tags_in(text)
                 if (path, tag) not in allow]
    return sorted(hits)


__all__ = ["Hit", "TAG", "FINDING_MARK", "allowed", "scan", "tags_in"]
