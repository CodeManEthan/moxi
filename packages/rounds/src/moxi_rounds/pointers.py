"""Pointers: where the operator said something.

An act that is the operator's is recorded with a pointer to where he said it. A pointer
is one token, with no white space, of one of four shapes:

- `comms:<digits>`: a message;
- `<8 or more hex digits>@<UTC time>`: a moment in a session, the time written
  `2026-01-02T03:04Z` or `2026-01-02T03:04:05Z`;
- an `https://` URL;
- `file:<path>#<anchor>`: a place in a file of the round's folder, the path relative to
  that folder.

Only the shape is checked. Nothing here reads a message, a session, a page or a file, so
a pointer of a right shape that points at nothing is accepted.
"""

from __future__ import annotations

import re
from datetime import datetime

from .errors import Unmet

#: The four shapes, as `shape` names them.
COMMS = "comms"
SESSION = "session"
URL = "url"
FILE = "file"

#: The shapes in words, for a refusal.
SHAPES = ("comms:<digits>, <8 or more hex digits>@<UTC time> (2026-01-02T03:04Z), an "
          "https:// URL, or file:<path in the round's folder>#<anchor>")

_COMMS = re.compile(r"comms:[0-9]+")
_SESSION = re.compile(r"[0-9A-Fa-f]{8,}@([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
                      r"(:[0-9]{2})?)Z")
_URL = re.compile(r"https://[^/?#]+.*")
_FILE = re.compile(r"file:([^#]+)#(.+)")


def shape(pointer: object) -> str | None:
    """Which of the four shapes `pointer` has (`COMMS`, `SESSION`, `URL` or `FILE`), or
    None when it has none of them."""
    if not isinstance(pointer, str) or not pointer or re.search(r"\s", pointer):
        return None
    if _COMMS.fullmatch(pointer):
        return COMMS
    session = _SESSION.fullmatch(pointer)
    if session:
        real = _is_time(session.group(1), seconds=bool(session.group(2)))
        return SESSION if real else None
    if _URL.fullmatch(pointer):
        return URL
    file = _FILE.fullmatch(pointer)
    if file and _is_under(file.group(1)):
        return FILE
    return None


def number(pointer: str) -> int:
    """The number of the message a `COMMS` pointer names. A message is its number, so
    `comms:012` and `comms:12` name one message."""
    return int(pointer.partition(":")[2])


def same(one: object, other: object) -> bool:
    """Whether two pointers point at one place: the same token, or two `COMMS`
    pointers of one number, however each is written."""
    if one == other:
        return True
    return shape(one) == COMMS and shape(other) == COMMS and number(one) == number(other)


def unmet(pointer: object, fix: str) -> Unmet | None:
    """The unmet condition of a pointer that has none of the four shapes, or None for one
    that has. `fix` is the command that gives a right one."""
    if shape(pointer) is not None:
        return None
    return Unmet(f"{pointer!r} is not a pointer to where the operator said it; PTR below "
                 f"is one token: {SHAPES}", fix)


def _is_time(text: str, *, seconds: bool) -> bool:
    """Whether the digits are a real date and time, not only the right count of them."""
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%S" if seconds else "%Y-%m-%dT%H:%M")
    except ValueError:
        return False
    return True


def _is_under(path: str) -> bool:
    """Whether a path stays in the folder it is relative to: not absolute, not from a
    home folder, and with no step up or empty step."""
    if path.startswith(("/", "~")):
        return False
    return all(part not in ("", "..") for part in path.split("/"))


__all__ = ["COMMS", "SESSION", "URL", "FILE", "SHAPES", "shape", "number", "same",
           "unmet"]
