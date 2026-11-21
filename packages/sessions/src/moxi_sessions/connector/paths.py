"""Local paths cut for a reader outside the machine, through sessions' one cut
(`pathcut.cut_local`): a folder goes out as its last segment only, and no full path
leaves. Both cuts here change text, so each runs as `chat_redact`'s `between` only,
after the strip and before the last redaction.

`cut_to_last_segment(text)` is a folder's own cut (`pathcut.last_segment`) as a
`between` step: the last segment of the text, or an empty text when it has none.

`cut_paths(text, known, env)` cuts each of `known` (paths the caller holds whole, such as
a root, an archive folder or a source's own path, which may lie outside the cut's roots
or hold spaces the cut's run stops at) wherever it occurs in the text, the longest first:
the known path and the run that goes on from it are cut to their last segment by the
folder's rule. The first redaction pass may have changed a known path (a secret inside
it is a placeholder by then), so each absolute known path's top folder (`/data/` for
`/data/sessions`) is then a root of its own for that text, as the cut's roots are. Then
it runs `cut_local` over the rest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from ..pathcut import RUN, SEGMENT_MAX_BYTES, cut_local, last_segment


def cut_to_last_segment(text: str) -> str:
    """`text` as a folder goes out: its last segment, `/` when it has none, and an empty
    text for an empty one."""
    return last_segment(text) or ""


def cut_paths(text: str, known: Iterable[str | None] = (),
              env: Mapping[str, str] | None = None) -> str:
    """`text` with each path it names cut to its last segment: each of `known` and the
    run after it first, the longest first, then each run from a known path's top folder,
    then every path `cut_local` finds."""
    whole = sorted({p for p in known if isinstance(p, str) and p}, key=len, reverse=True)
    tops = sorted({_top(p) for p in whole} - {None}, key=len, reverse=True)
    for starts in (whole, tops):
        if starts:
            pattern = re.compile("(?:" + "|".join(map(re.escape, starts)) + ")" + RUN)
            text = pattern.sub(lambda m: cut_to_last_segment(m.group(0)), text)
    return cut_local(text, env)


def _top(path: str) -> str | None:
    """An absolute path's top folder (`/data/` for `/data/sessions`), or None for a
    relative path or one with a single segment."""
    parts = [part for part in path.split("/") if part]
    return f"/{parts[0]}/" if path.startswith("/") and len(parts) > 1 else None


__all__ = ["SEGMENT_MAX_BYTES", "cut_local", "cut_paths", "cut_to_last_segment",
           "last_segment"]
