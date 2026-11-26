"""Pointers: where the operator said something.

An act that is the operator's is recorded with a pointer to where the operator said it.
A pointer is one token, with no white space, of one of five shapes:

- `comms:<digits>`: a message;
- `<8 or more hex digits>@<UTC time>`: a moment in a session, the time written
  `2026-01-02T03:04Z` or `2026-01-02T03:04:05Z`;
- an `https://` URL;
- `file:<path>#<anchor>`: a place in a file of the round's folder, the path relative to
  that folder;
- `page:<page>/<act>@<UTC time>`: one act on a module's page, as in
  `page:rounds/k3J9xQ2aZf1T@2026-11-21T04:18:05Z`. `<page>` is the module whose page it
  was (lower-case letters, digits and `-`, from a letter), `<act>` the id the page
  minted for that one act (8 to 64 letters, digits, `_` and `-`), and the time is UTC
  to the second. The act id keeps two acts in one second two pointers. A page on this
  machine has no `https://` address, so its acts point at themselves this way.

Only the shape is checked. Nothing here reads a message, a session, a page or a file, so
a pointer of a right shape that points at nothing is accepted.

A question has one more shape of origin, which no act of the operator's takes: the
build it came up in, `build:<stage>@<commit>`, the stage and the tip of its build when
the question was asked (`build_origin`). Only `ask --build` writes it, so `shape`
does not know it and no flag accepts it.
"""

from __future__ import annotations

import re
from datetime import datetime

from .errors import Unmet

#: The five shapes, as `shape` names them.
COMMS = "comms"
SESSION = "session"
URL = "url"
FILE = "file"
PAGE = "page"

#: The shapes in words, for a refusal.
SHAPES = ("comms:<digits>, <8 or more hex digits>@<UTC time> (2026-01-02T03:04Z), an "
          "https:// URL, file:<path in the round's folder>#<anchor>, or "
          "page:<page>/<act id>@<UTC time to the second> (2026-01-02T03:04:05Z)")

_COMMS = re.compile(r"comms:[0-9]+")
_SESSION = re.compile(r"[0-9A-Fa-f]{8,}@([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
                      r"(:[0-9]{2})?)Z")
_URL = re.compile(r"https://[^/?#]+.*")
_FILE = re.compile(r"file:([^#]+)#(.+)")
_PAGE = re.compile(r"page:[a-z][a-z0-9-]*/([A-Za-z0-9_-]{8,64})@"
                   r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})Z")

#: The shape of a question's origin in a build, as `build_origin` writes it.
BUILD = "build"
_BUILD = re.compile(r"build:([^@\s]+)@([0-9a-f]{40})")


def build_origin(stage: str, tip: str) -> str:
    """The origin of a question that came up in the build of `stage`, whose tip was
    `tip` when it was asked."""
    return f"{BUILD}:{stage}@{tip}"


def is_build_origin(origin: object) -> bool:
    """Whether `origin` is the origin `build_origin` writes."""
    return isinstance(origin, str) and _BUILD.fullmatch(origin) is not None


def shape(pointer: object) -> str | None:
    """Which of the five shapes `pointer` has (`COMMS`, `SESSION`, `URL`, `FILE` or
    `PAGE`), or None when it has none of them."""
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
    if page_at(pointer) is not None:
        return PAGE
    return None


def page_at(pointer: object) -> str | None:
    """The time a `PAGE` pointer names, as the log writes a time
    (`2026-01-02T03:04:05Z`), or None for any other pointer."""
    if not isinstance(pointer, str):
        return None
    page = _PAGE.fullmatch(pointer)
    if page is None or not _is_time(page.group(2), seconds=True):
        return None
    return page.group(2) + "Z"


def page_act(pointer: object) -> str | None:
    """The act id a `PAGE` pointer names (`page:rounds/<act id>@<time>`), or None for
    any other pointer."""
    if page_at(pointer) is None:
        return None
    return _PAGE.fullmatch(pointer).group(1)


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


def unmet(pointer: object, fix: str, *,
          names: str = "where the operator said it") -> Unmet | None:
    """The unmet condition of a pointer that has none of the five shapes, or None for one
    that has. `fix` is the command that gives a right one, and `names` what the pointer
    is to, for one that names something else than the operator's word."""
    if shape(pointer) is not None:
        return None
    return Unmet(f"{pointer!r} is not a pointer to {names}; PTR below is one token: "
                 f"{SHAPES}", fix)


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


__all__ = ["COMMS", "SESSION", "URL", "FILE", "PAGE", "SHAPES", "BUILD", "shape",
           "page_at", "page_act", "number", "same", "unmet", "build_origin", "is_build_origin"]
