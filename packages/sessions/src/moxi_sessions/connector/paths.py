"""Local paths cut for a reader outside the machine: a folder goes out as its last part
only, so no full path leaves.

`last_segment(path)` splits a path on `/` and drops the empty parts, so a trailing slash
or a relative path cuts the same way, and gives the last part, cut to `SEGMENT_MAX_BYTES`
UTF-8 bytes on a character's edge. `/` gives `/`; an empty or missing path gives None.
`cut_paths(text, known)` cuts every path a text names the same way: each of `known`
first, the longest first, then each absolute path left in the text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: The most of a folder's last part that goes out, in UTF-8 bytes.
SEGMENT_MAX_BYTES = 255

#: An absolute path in prose: a `/` at the text's start or after a space or an opening
#: bracket or quote, and what follows up to the next space, bracket or quote.
_ABSOLUTE = re.compile(r"(?:(?<=^)|(?<=[\s(\[\"'=]))/[^\s()\[\]\"']*")


def last_segment(path: str | None) -> str | None:
    """The last part of `path`, cut to `SEGMENT_MAX_BYTES` bytes; `/` for the root; None
    for an empty or missing path."""
    if not isinstance(path, str) or not path:
        return None
    parts = [part for part in path.split("/") if part]
    if not parts:
        return "/"
    data = parts[-1].encode("utf-8", "surrogatepass")
    if len(data) <= SEGMENT_MAX_BYTES:
        return parts[-1]
    return data[:SEGMENT_MAX_BYTES].decode("utf-8", "ignore")


def cut_paths(text: str, known: Iterable[str | None] = ()) -> str:
    """`text` with each path it names cut to its last part: each of `known` first, the
    longest first, then every absolute path left."""
    for path in sorted({p for p in known if isinstance(p, str) and p}, key=len,
                       reverse=True):
        text = text.replace(path, last_segment(path) or "")
    return _ABSOLUTE.sub(lambda m: last_segment(m.group(0)) or "", text)


__all__ = ["SEGMENT_MAX_BYTES", "cut_paths", "last_segment"]
