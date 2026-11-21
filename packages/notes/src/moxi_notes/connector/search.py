"""`search_notes`: notes found by words in their path and text.

**Words.** The words are cut as memory cuts a query: letters and digits make a word, and
everything else separates two. Case is ignored. A word matches a whole word only, so
`note` doesn't match `notes`. A file matches when every word matches its path or its
text.

**The walk.** From the root, through `pathrules.walk`: never through a link, never into a
dot name, `node_modules` or `__pycache__`, and only files a read would read, each opened
and checked as `read_note` opens a file, at most 1 MiB. The walk matches the raw text,
which keeps it fast, and stops `WALK_S` seconds after the call began, saying so (`cut`).

**Ranking.** Files whose path holds a word first, then newest first.

**Confirming.** In ranking order, each matching file whose path the chat redaction leaves
as it is, is read again whole, put through the chat redaction, and kept only when every
word still matches its path or its redacted text. A text secrets couldn't scan is
skipped, and counted in the call log only. Confirming stops at `limit` hits, or
`CONFIRM_S` seconds after the call began (`cut`). Every hit is confirmed on the redacted
text, and `files_read` counts what the walk read whatever the words, so neither a hit nor
a count confirms a value the answer hides.

Each hit gives its path, when it changed, and its shown line: the first line of the
redacted text that holds a word, cut at `SHOWN_CHARS`, or None when the words match only
the path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from time import monotonic

from . import pathrules

#: The largest file the walk opens.
SEARCH_CAP_BYTES = 1 << 20
#: Seconds from the call's start after which the walk stops, and confirming.
WALK_S = 6.0
CONFIRM_S = 16.0
#: Most words a search takes, and the shown line's length.
WORDS_MAX = 8
SHOWN_CHARS = 300

_WORD = re.compile(r"[^\W_]+")


def cut(words: str) -> tuple[str, ...]:
    """The words of `words`, case folded, each kept once, in order."""
    seen: dict[str, None] = {}
    for word in _WORD.findall(words.casefold()):
        seen.setdefault(word)
    return tuple(seen)


def patterns(words: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    """For each word, a pattern that finds it as a whole word in case-folded text."""
    return tuple(re.compile(r"(?<![^\W_])" + re.escape(w) + r"(?![^\W_])") for w in words)


def _all_match(found: tuple[re.Pattern, ...], path_cf: str, text_cf: str) -> bool:
    return all(p.search(path_cf) or p.search(text_cf) for p in found)


@dataclass
class Found:
    """What a search found: `hits` (each `path`, `modified` as seconds since the epoch,
    and `line`), and for the call log what it read and confirmed."""

    hits: list[tuple[str, float, str | None]] = field(default_factory=list)
    files_read: int = 0
    confirmed: int = 0
    unscanned: int = 0
    cut: bool = False


def search(root_dir: str, words: tuple[str, ...], limit: int, started: float) -> Found:
    """Search the notes under `root_dir` for `words` (as `cut` gives them), keeping at
    most `limit` hits; `started` is the call's start on `time.monotonic`'s clock. Raises
    `pathrules.RedactionOff` when the chat redaction can't run."""
    found = patterns(words)
    out = Found()
    candidates = []
    for walked in pathrules.walk(root_dir, cap_bytes=SEARCH_CAP_BYTES):
        if monotonic() - started > WALK_S:
            out.cut = True
            break
        try:
            data = pathrules.read_judged(walked.path, walked.st, cap_bytes=SEARCH_CAP_BYTES)
        except pathrules.Refused:
            continue
        out.files_read += 1
        path_cf = walked.rel.casefold()
        if _all_match(found, path_cf, pathrules.decode(data).casefold()):
            in_path = any(p.search(path_cf) for p in found)
            candidates.append((not in_path, -walked.st.st_mtime, walked.rel, walked))
    candidates.sort(key=lambda c: c[:3])
    for _, _, rel, walked in candidates:
        if len(out.hits) >= limit:
            break
        if monotonic() - started > CONFIRM_S:
            out.cut = True
            break
        if pathrules.redaction_changes(rel):
            continue
        try:
            data = pathrules.read_judged(walked.path, walked.st, cap_bytes=SEARCH_CAP_BYTES)
        except pathrules.Refused:
            continue
        text = pathrules.chat_redact(pathrules.decode(data))
        out.confirmed += 1
        if text == pathrules.UNSCANNED:
            out.unscanned += 1
            continue
        if not _all_match(found, rel.casefold(), text.casefold()):
            continue
        line = next((ln for ln in text.split("\n")
                     if any(p.search(ln.casefold()) for p in found)), None)
        out.hits.append((rel, walked.st.st_mtime,
                         None if line is None else line[:SHOWN_CHARS]))
    return out


__all__ = ["CONFIRM_S", "Found", "SEARCH_CAP_BYTES", "SHOWN_CHARS", "WALK_S",
           "WORDS_MAX", "cut", "patterns", "search"]
