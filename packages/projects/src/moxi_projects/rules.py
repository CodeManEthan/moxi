"""The rules a project's key, root, agent names and kind names follow.

A key is what other modules store to name a project, and some of them use it as a folder
name. So a key is refused if Linux, macOS or Windows would read it as a folder name other
than as spelled: it holds only letters, digits, `.`, `_` and `-`; it starts with a letter
or a digit; it does not end with `.` (Windows drops a trailing dot, so `proj.` and `proj`
would share a folder); and it is not a Windows device name (`con`, `nul`, `com1` and the
rest), with or without an extension (`con.txt`). Keys are also unique ignoring case, and
looked up ignoring case, which the store enforces, so two keys never share a folder on a
case-insensitive disk.

A root is an absolute path, stored normalized (`os.path.normpath`) with its symlinks left
as written, so the user sees what they typed. Roots are unique after `os.path.normcase`,
which the store enforces. A root that doesn't exist yet is allowed: a project may be added
before it is cloned.

An agent name is a non-empty string with no whitespace or control characters, stored as
given and matched exactly. Whether it is a live identity anywhere is not projects'
question.

A kind name follows the key's alphabet: letters, digits, `.`, `_` and `-`, starting with a
letter or digit.
"""

from __future__ import annotations

import os
import re
import unicodedata

from .errors import Refused

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: The names Windows keeps for devices, with or without an extension.
DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul",
                          *(f"com{n}" for n in range(10)),
                          *(f"lpt{n}" for n in range(10))})

_KEY_RULE = ("a key holds only letters, digits, '.', '_' and '-', starts with a letter or "
             "a digit and doesn't end with '.'")


def check_key(key: object) -> str:
    """`key` when it is a valid key; else `Refused`, saying which rule it breaks."""
    if not isinstance(key, str) or not key:
        raise Refused("a project needs a key", _KEY_RULE)
    if not _KEY.fullmatch(key) or key.endswith("."):
        raise Refused(f"{key!r} is not a valid project key", _KEY_RULE)
    if key.split(".", 1)[0].lower() in DEVICE_NAMES:
        raise Refused(f"{key!r} is not a valid project key: Windows keeps "
                      f"{key.split('.', 1)[0]!r} as a device name, with or without an "
                      "extension", "pick another key")
    return key


def absolutize(path: str, cwd: str | None = None) -> str:
    """`path` as a command line means it: `~` expanded, made absolute against `cwd` (the
    working directory by default) and normalized. Symlinks are left as written."""
    path = os.path.expanduser(os.fspath(path))
    if not os.path.isabs(path):
        path = os.path.join(cwd if cwd is not None else os.getcwd(), path)
    return os.path.normpath(path)


def check_root(root: object, what: str = "root") -> str:
    """`root` normalized, when it is an absolute path; else `Refused`. `what` names the
    field in the message (`root` or `notes_root`)."""
    if not isinstance(root, str) or not root or "\0" in root:
        raise Refused(f"a project's {what} is a path", f"give the {what} as a directory")
    if not os.path.isabs(root):
        raise Refused(f"the {what} {root!r} is not an absolute path",
                      f"give the {what} as an absolute path")
    return os.path.normpath(root)


def same_root(a: str, b: str) -> bool:
    """Whether two stored roots are one root by the store's uniqueness rule."""
    return os.path.normcase(a) == os.path.normcase(b)


def _bad_char(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf", "Cs")


def check_agent(name: object, what: str = "agent") -> str:
    """`name` when it is a valid agent name; else `Refused`. `what` names the role in the
    message (`agent` or `lead`)."""
    if not isinstance(name, str) or not name:
        raise Refused(f"the {what} needs a name", "give a name with no spaces")
    if any(_bad_char(ch) for ch in name):
        raise Refused(f"the {what} name {name!r} holds whitespace or a control character",
                      "give a name with no spaces or control characters")
    return name


def check_kind_name(name: object) -> str:
    """`name` when it is a valid kind name; else `Refused`."""
    if not isinstance(name, str) or not _KEY.fullmatch(name):
        raise Refused(f"{name!r} is not a valid kind name",
                      "a kind name holds only letters, digits, '.', '_' and '-' and "
                      "starts with a letter or a digit")
    return name


__all__ = ["DEVICE_NAMES", "check_key", "absolutize", "check_root", "same_root",
           "check_agent", "check_kind_name"]
