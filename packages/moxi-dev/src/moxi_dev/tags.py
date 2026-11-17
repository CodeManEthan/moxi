"""The tag check: no short reference ids in the repository's files.

Code, tests and docs say a reason in plain words. A reference id is shorthand that points
into notes a reader of the repository can't see, such as the notes a change was planned
or reviewed in, so the reader can't follow it. The check refuses the shapes of
publishing's `ruling-tag` and `round-pointer` rules, read from `moxi_publishing.rules` so
the two checks never drift apart, and two shapes only this repository's own records use,
which publishing leaves out so they never fire on someone else's repo: a D and two
digits, perhaps with a letter after; and a finding id, an L and a number, bare or
bracketed. No example of one is written here, so this file passes its own check.

Two kinds of text are let through. A finding is one reported issue, and
`@pytest.mark.finding("L50")` marks the tests that cover it, so `moxi-dev check
--finding L<n>` can run just those. The mark's arguments are data a tool reads, and a
finding id is let through there and nowhere else: in a comment, a docstring or a string
it is refused like the rest, so a label of that shape (a numbered rule, say) is written
another way. And a line of `.residue-allow` at the repository's root, `<path> TAB <exact
text> TAB <why>`, lets that text stand in that one file, for an id that is itself data
(the input a pattern's test feeds it, say).

`scan(root)` reads every tracked and untracked-but-not-ignored file that is UTF-8 text,
and returns what it found. A file under the core's `packages/moxi/src/moxi/_vendor/` is a
released library kept as released (the core vendors two), so it is not read; the
contract check and deptry skip the same directory. Any other `_vendor` directory, a
module's own, is read like the rest.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from moxi_publishing.rules import ROUND_POINTER, RULING_TAG

from .contract import CORE
from .contract import VENDORED as _CORE_VENDORED

#: The shapes only this repository's own records use: a D-number and a finding id.
OWN = r"\bD[0-9]{2}[a-z]?\b|\bL[0-9]+\b"

#: The shapes of a tag: publishing's two rules, then this repository's own.
TAG = re.compile(f"{RULING_TAG}|{ROUND_POINTER}|{OWN}")

#: A finding mark with its arguments, one or more strings, which are data. The call may
#: run over several lines, as a formatter wraps it; each string stays on one line.
FINDING_MARK = re.compile(
    r"""mark\.finding\(\s*(["'])[^"'\n]*\1(?:\s*,\s*(["'])[^"'\n]*\2)*\s*,?\s*\)""")

#: The file that declares allowed text, and files that are not prose or code.
ALLOW_FILE = ".residue-allow"
SKIPPED = frozenset({ALLOW_FILE, "uv.lock"})
#: The core's directory of vendored copies of released libraries, whose text is not ours,
#: as a path from the workspace root.
VENDORED = f"packages/{CORE}/{_CORE_VENDORED}"


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
        if (path in SKIPPED or path.startswith(VENDORED)
                or not (root / path).is_file()):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits += [Hit(path, number, tag) for number, tag in tags_in(text)
                 if (path, tag) not in allow]
    return sorted(hits)


__all__ = ["Hit", "OWN", "TAG", "FINDING_MARK", "allowed", "scan", "tags_in"]
