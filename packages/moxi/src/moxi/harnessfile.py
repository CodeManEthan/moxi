"""Reading and rewriting a harness's own files: Claude Code's `settings.json` and the like.

Two modules may write one harness file, and the harness itself writes it too. This is the
one writer they share, so they share one rule for it:

- A `Doc` is one file read once. A JSON file is read with `json` and written back with
  its key order and the indent of its first indented line (two spaces if none); a file
  that doesn't parse as a JSON object (comments, `.jsonc`, a syntax error) is refused
  with one line (`Refused`), and nothing is changed. A text file is read as UTF-8, and
  any bytes that aren't are written back as they were.
- `write(doc, backups, keep)` is the one write per file: the file is read again just
  before the write, and the write is dropped, with one line, when its bytes changed
  since the `Doc` read them (the harness or another writer wrote it meanwhile). Else the
  old bytes are kept under `backups` (`backup`), and the new ones written with
  `moxi.files.atomic_write` in the file's own mode (a new JSON file 0600, a new text file
  0644). A link is written through, to the file it points at, so a linked file stays a
  link. No lock file is left beside the file: the harness's folder is not ours.

A refusal's line names the file and why, and never the module that read it: each caller
says what it does about it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .files import atomic_write, private_dir


class Refused(Exception):
    """A harness file that can't be used as it is: one line naming it and why."""


class Doc:
    """One harness file as read: `raw` (None when missing), and `data` (a dict for JSON,
    a str for text) that the caller changes in place.

    `kind` is `"json"` or `"text"`. `indent` is a JSON file's indent; `original` the
    bytes `dumps()` gives for the file as read (None when missing), so `changed` is true
    only when the caller's change alters what would be written.
    """

    def __init__(self, path: str | os.PathLike, kind: str):
        self.path = Path(path)
        self.kind = kind
        try:
            self.raw = self.path.read_bytes()
        except FileNotFoundError:
            self.raw = None
        except OSError as exc:
            raise Refused(f"{self.path} can't be read ({exc.strerror or exc})") from None
        if kind == "json":
            self.data = self._json()
            self.indent = _indent(self.raw)
        else:
            # Bytes that aren't UTF-8 pass through as they are, so the user's text comes
            # back byte-equal around the caller's part.
            self.data = "" if self.raw is None else self.raw.decode("utf-8",
                                                                    "surrogateescape")
        self.original = self.dumps() if self.raw is not None else None

    def _json(self) -> dict:
        if self.raw is None or not self.raw.strip():
            return {}
        try:
            data = json.loads(self.raw)
        except ValueError as exc:
            raise Refused(f"{self.path} is not plain JSON ({exc})") from None
        if not isinstance(data, dict):
            raise Refused(f"{self.path} is not a JSON object")
        return data

    def dumps(self) -> bytes:
        if self.kind == "json":
            return (json.dumps(self.data, indent=self.indent, ensure_ascii=False)
                    + "\n").encode("utf-8")
        return self.data.encode("utf-8", "surrogateescape")

    @property
    def changed(self) -> bool:
        if self.raw is None:
            return bool(self.data)
        return self.dumps() != self.original

    @property
    def empty(self) -> bool:
        return (not self.data) if self.kind == "json" else not self.data.strip()


def _indent(raw: bytes | None) -> int:
    for line in (raw or b"").decode("utf-8", "replace").splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped and len(stripped) < len(line):
            return len(line) - len(stripped)
    return 2


def write(doc: Doc, backups: Path, keep: int, *, remove_empty: bool = False,
          keep_copy=None) -> str | None:
    """Write `doc` back once, or remove it when `remove_empty` and nothing is left in it,
    keeping the old bytes under `backups` (`backup`, the newest `keep`). With
    `keep_copy`, `keep_copy(path, now)` is called first, on a file that exists, with the
    bytes just read again and checked.

    Returns None when written, else the one line saying why not: `<path> changed since it
    was read`, `<path> can't be read again (<why>)`, or, when `keep_copy` raises
    `OSError`, `a copy of <path> couldn't be kept (<why>), so it was not written`. Either
    way nothing was written.
    """
    target = Path(os.path.realpath(doc.path))
    try:
        now = target.read_bytes()
    except FileNotFoundError:
        now = None
    except OSError as exc:
        return f"{doc.path} can't be read again ({exc.strerror or exc})"
    if now != doc.raw:
        return f"{doc.path} changed since it was read"
    if now is not None and keep_copy is not None:
        try:
            keep_copy(doc.path, now)
        except OSError as exc:
            return (f"a copy of {doc.path} couldn't be kept ({exc.strerror or exc}), so it "
                    "was not written")
    if now is not None:
        backup(target, backups, now, keep)
    if remove_empty and doc.empty:
        if now is not None:
            target.unlink()
        return None
    mode = 0o644 if doc.kind == "text" else 0o600
    if now is not None:
        mode = os.stat(target).st_mode & 0o7777
    os.makedirs(target.parent, exist_ok=True)
    atomic_write(target, doc.dumps(), mode=mode)
    return None


def backup(path: Path, backups: Path, data: bytes, keep: int) -> Path:
    """Keep `data`, file `path`'s bytes before a write, as `backups/<stamp>-<name>` (the
    folder made private, the file 0600), and prune that name's backups to the newest
    `keep`."""
    folder = private_dir(backups)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + f"{time.time_ns() % 10**9:09d}"
    out = folder / f"{stamp}-{path.name}"
    atomic_write(out, data, mode=0o600)
    mine = sorted(p for p in folder.iterdir() if p.name.endswith("-" + path.name))
    for old in mine[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass
    return out


__all__ = ["Refused", "Doc", "write", "backup"]
