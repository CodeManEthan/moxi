"""Local paths cut for a reader outside the machine, through sessions' one cut
(`pathcut.cut_local`): a folder goes out as its last segment only, and no full path
leaves. `cut_paths` changes text, so it runs as `chat_redact`'s `between` only.

`last_segment(path)` is the cut's display of one path (`pathcut.last_segment`).
`cut_paths(text, known, env)` cuts each of `known` (paths the caller holds whole, such as
a source's own path, which may hold spaces the cut's run stops at) to its last segment
first, the longest first, then runs `cut_local` over the text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..pathcut import SEGMENT_MAX_BYTES, cut_local, last_segment


def cut_paths(text: str, known: Iterable[str | None] = (),
              env: Mapping[str, str] | None = None) -> str:
    """`text` with each path it names cut to its last segment: each of `known` first,
    the longest first, then every path `cut_local` finds."""
    for path in sorted({p for p in known if isinstance(p, str) and p}, key=len,
                       reverse=True):
        text = text.replace(path, last_segment(path) or "")
    return cut_local(text, env)


__all__ = ["SEGMENT_MAX_BYTES", "cut_local", "cut_paths", "last_segment"]
