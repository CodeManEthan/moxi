"""Codex's rollout: where a thread's rollout files are, and its lines read forward.

* **Finding them** (`find_rollout`). Codex writes a thread's rollout under
  `<sessions root>/YYYY/MM/DD/` and names it, by its own rule,
  `rollout-<YYYY-MM-DDThh-mm-ss>-<thread id>[_<rollout id>].jsonl`, compressed to
  `.jsonl.zst` after a week idle. A reverted thread can leave a second file with a rollout
  id suffix. `find_rollout` returns every file whose name parses as the thread's rollout;
  what none, one, a compressed one or two of them mean is the caller's.
* **Reading one forward** (`stream_rollout`): every line's entry, through the one decoder
  in `claude` (`stream_entries`), so a rollout line decodes exactly as a transcript line
  does. A backward read is `read_back`'s.

Nothing here judges a line.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

from .claude import SAFE_ID_RE, stream_entries

#: Codex's rollout name rule. `thread` is the thread id, `rollout` the suffix a revert
#: adds, `zst` present when the file is compressed.
NAME_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
    r"(?P<thread>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"(?:_(?P<rollout>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}))?\.jsonl(?P<zst>\.zst)?$")


def find_rollout(sessions_root: str | Path, thread_id: str) -> list[Path]:
    """Every rollout file for `thread_id` under `sessions_root`, sorted by path.

    A file is a candidate when it sits three directories down (`YYYY/MM/DD`) and its name
    parses by `NAME_RE` with this thread id; `.jsonl` and `.jsonl.zst` alike. The id is
    matched as Codex writes it, in lower case. An empty list means the root was read and holds none.

    Raises ValueError for an id the search refuses (`claude.SAFE_ID_RE`: empty, a path
    separator, a glob character or a leading dot), and OSError when the root is not a
    directory that can be read, so an empty list is never said of a root nobody looked in.
    """
    if not isinstance(thread_id, str) or not SAFE_ID_RE.match(thread_id):
        raise ValueError(f"{thread_id!r} is not a thread id the search looks up")
    root = Path(sessions_root)
    with os.scandir(root):
        pass                              # raises OSError for an absent or unreadable root
    wanted = thread_id.lower()
    found = []
    for path in root.glob(f"*/*/*/rollout-*{thread_id}*"):
        match = NAME_RE.match(path.name)
        if match and match.group("thread").lower() == wanted and path.is_file():
            found.append(path)
    return sorted(found)


def stream_rollout(path: str | Path, *, start_line: int = 0) -> Iterator[dict]:
    """Every entry of one rollout, in file order, from `start_line` on.

    Lines decode by `claude.stream_entries`: a line that will not decode, a last line
    Codex is still writing among them, is yielded as a MALFORMED entry, never dropped. The
    file is read as UTF-8 with undecodable bytes replaced, and opened when iteration
    starts; an OSError opening or reading it is raised from the iteration. A compressed
    rollout, which this does not read, raises ValueError at the call.
    """
    if str(path).endswith(".zst"):
        raise ValueError(f"{path} is a compressed rollout; it is not read")
    return _entries(Path(path), start_line)


def _entries(path: Path, start_line: int) -> Iterator[dict]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        yield from stream_entries(handle, start_line=start_line)
