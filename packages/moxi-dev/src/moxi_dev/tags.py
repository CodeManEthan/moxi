"""The tag check: no ruling, design, review or build tags in the repository's files.

Code, tests and docs say a reason in plain words. A tag is the shorthand of the notes a
change was planned in, which a reader of the repository cannot follow: a short capital id
with a number in square brackets, one of a few capital letters with a number in
parentheses, a decision id (a D and two digits), a section sign with a number, a design
version, a severity with a number, or a round named by its home and slug. No example
of one is written here, so this file passes its own check. Hook laws written
`L4` and other plain ids are not tags.

Two kinds of text are let through. The argument of a finding mark,
`@pytest.mark.finding("L50")`, is data a tool reads. And a line of the root's
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
    r"|\bD[0-9]{2}\b"
    r"|\u00a7 ?[0-9]"
    r"|\bdesign v[0-9]"
    r"|\b(?:MINOR|MAJOR|BLOCKER) [0-9]"
    r"|(?i:\bround [a-z0-9-]+/[a-z0-9-]+)"
)

#: A finding mark's argument, which is data.
FINDING_MARK = re.compile(r"""mark\.finding\(\s*(["'])[^"'\n]*\1\s*\)""")

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


def tags_in(text: str) -> list[tuple[int, str]]:
    """Each tag in `text`, with its line number; finding marks' arguments excepted."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        line = FINDING_MARK.sub("mark.finding()", line)
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
