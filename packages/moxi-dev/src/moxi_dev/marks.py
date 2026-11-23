"""The exception mark the pronoun and machine-text steps read.

A mark is the house form CONTRIBUTING.md's "Marking an exception" gives: `noqa:`, codes, one
of them the step's own, then two hyphens and the reason, on the line it lets stand.
`marked_lines(text, code)` finds the lines of a text that carry a step's mark.

- A reason holds a letter or a digit before the comment around the mark closes (`-->`,
  `*/`), so a mark followed by nothing but its comment's end gives no reason.
- A mark inside inline code, in backticks, is an example of the form and not a mark.
  Backticks pair as Markdown pairs them: a run of backticks opens inline code and the next
  run of the same length closes it. So a mark between two pieces of inline code, or before
  one, is still a mark.

Lines are numbered from 1 and split on a newline alone, as git numbers them.
"""

from __future__ import annotations

import re

#: Inline code: a run of backticks, its text, then the next run of the same length.
CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
#: The end of a comment, which is no reason.
_COMMENT_END = r"(?:-->|\*/)"


def mark_patterns(code: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """The mark that names `code` among its codes and gives a reason, and any mark that
    names `code`, with or without one."""
    named = r"\bnoqa:\s*[A-Z0-9, ]*?\b" + re.escape(code) + r"\b"
    reason = r"[A-Z0-9, ]*?\s+--\s+(?:(?!" + _COMMENT_END + r").)*?\w"
    return re.compile(named + reason), re.compile(named)


def unquoted(line: str) -> str:
    """`line` without the inline code that quotes a mark."""
    return CODE_SPAN.sub(lambda span: "" if "noqa:" in span.group(2) else span.group(0), line)


def marked_lines(text: str, code: str) -> tuple[set[int], set[int]]:
    """The lines of `text` that carry a mark naming `code` with a reason, and those that
    carry one without."""
    given, named = mark_patterns(code)
    marked, bare = set(), set()
    for number, line in enumerate(text.split("\n"), 1):
        line = unquoted(line)
        if given.search(line):
            marked.add(number)
        elif named.search(line):
            bare.add(number)
    return marked, bare


__all__ = ["CODE_SPAN", "mark_patterns", "marked_lines", "unquoted"]
