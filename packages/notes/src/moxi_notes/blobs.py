"""The one blob reader every judge uses: what changed, and each blob's whole content.

A judge never reads a diff. It takes blob ids from git's `-z --raw` output (`diff-index`,
`diff-tree`) or from the committer's prepare context, and reads each blob's whole content
from git's object store with `git cat-file` and the ids on stdin. So no path is parsed
from a diff header, and a commit, a range and a snapshot use one reader.

Every function takes `git`, a callable that runs git in the repo being judged:
`git(*args, input=None, timeout=..., binary=True)` returning a finished process. Both
`HookContext.git` and `PrepareContext.git` have that shape, so a git hook piece reads the
index git is committing and a prepare step reads the snapshot's.

Standard library only.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

#: Seconds any one git call here may take.
TIMEOUT = 30.0
#: A gitlink: a submodule's commit, not a blob.
GITLINK = "160000"


class GitError(Exception):
    """A git call a judge made failed, or gave output it can't read."""


@dataclass(frozen=True)
class Entry:
    """One changed path of `-z --raw` output. `status` is git's letter; `blob` is the new
    side's id, None for a deletion or a gitlink; `old_path` is a rename's or copy's old
    side. `path` holds git's bytes exactly (`os.fsencode(path)` gives them back)."""

    status: str
    path: str
    blob: str | None = None
    old_path: str | None = None


def _zero(oid: str) -> bool:
    return not oid.strip("0")


def parse_raw(data: bytes) -> list[Entry]:
    """`git diff-index` or `diff-tree --raw -z` output as entries.

    A field that is not a `:` entry (a commit id line of `diff-tree --stdin`) is skipped.
    """
    fields = data.split(b"\0")
    out: list[Entry] = []
    i = 0
    while i < len(fields):
        meta = fields[i]
        if not meta.startswith(b":"):
            i += 1
            continue
        parts = meta[1:].decode("ascii", "replace").split()
        if len(parts) < 5 or i + 1 >= len(fields):
            raise GitError("git gave a raw diff line that can't be read")
        mode, blob, status = parts[1], parts[3], parts[4][:1]
        if status in ("R", "C"):
            if i + 2 >= len(fields):
                raise GitError("git gave a raw diff line that can't be read")
            old, new = os.fsdecode(fields[i + 1]), os.fsdecode(fields[i + 2])
            i += 3
        else:
            old, new = None, os.fsdecode(fields[i + 1])
            i += 2
        keep = not _zero(blob) and mode != GITLINK and status != "D"
        out.append(Entry(status, new, blob if keep else None, old))
    return out


def run(git, *args: str, input: bytes | None = None) -> bytes:
    """`git <args>`'s stdout; `GitError` when it fails, runs past `TIMEOUT` or can't
    start."""
    try:
        done = git(*args, input=input, timeout=TIMEOUT, binary=True)
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} ran past {int(TIMEOUT)} s") from None
    except OSError as exc:
        raise GitError(f"git {args[0]} could not start ({exc})") from None
    if done.returncode != 0:
        lines = (done.stderr or "").strip().splitlines()
        raise GitError(f"git {args[0]} exited {done.returncode}"
                       + (f": {lines[-1]}" if lines else ""))
    return done.stdout or b""


def sizes(git, ids) -> dict[str, int]:
    """Each blob id's size in bytes. An id that is not a blob is left out; one git
    doesn't have raises `GitError`."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    out: dict[str, int] = {}
    data = run(git, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
               input=("\n".join(ids) + "\n").encode("ascii"))
    for line in data.decode("ascii", "replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "missing":
            raise GitError(f"git has no object {parts[0]}")
        if len(parts) != 3:
            raise GitError("git cat-file gave a line that can't be read")
        if parts[1] == "blob":
            out[parts[0]] = int(parts[2])
    return out


def contents(git, ids) -> dict[str, bytes]:
    """Each blob's whole content, from one `git cat-file --batch` with the ids on
    stdin."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    data = run(git, "cat-file", "--batch", input=("\n".join(ids) + "\n").encode("ascii"))
    out: dict[str, bytes] = {}
    at = 0
    for _ in ids:
        end = data.find(b"\n", at)
        if end < 0:
            raise GitError("git cat-file --batch ended early")
        parts = data[at:end].decode("ascii", "replace").split()
        if len(parts) != 3:
            raise GitError(f"git cat-file --batch: {' '.join(parts) or 'an empty line'}")
        size = int(parts[2])
        body = data[end + 1:end + 1 + size]
        if len(body) != size:
            raise GitError("git cat-file --batch ended early")
        out[parts[0]] = body
        at = end + 1 + size + 1
    return out


def read(git, ids, *, max_bytes: int) -> tuple[dict[str, bytes], dict[str, int]]:
    """(content by id, size by id of each blob over `max_bytes`). A blob over the cap is
    never read; an id that is not a blob is in neither."""
    found = sizes(git, ids)
    big = {oid: n for oid, n in found.items() if n > max_bytes}
    return contents(git, [oid for oid in found if oid not in big]), big


def text(data: bytes) -> str:
    """A blob as the scan reads it: UTF-8, with bytes that aren't kept as surrogates."""
    return data.decode("utf-8", "surrogateescape")


__all__ = ["TIMEOUT", "GitError", "Entry", "parse_raw", "run", "sizes", "contents", "read",
           "text"]
