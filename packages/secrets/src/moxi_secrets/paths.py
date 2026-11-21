"""Secret paths: files whose content must not be read at all.

The globs live in `rules/secret-paths.txt`. A glob without a slash matches the file's
name; a glob ending in `/` names a directory and matches every path with a directory of
that name among its parts; any other glob matches the whole path or any run of its
trailing parts. Matching ignores case on every OS and reads `\\` as `/`. The file is
never opened.

`secret_path` walks a tool call's input for such a path: every whitespace-separated word
of every string, split at shell joins, with strings that hold JSON read into.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from functools import lru_cache
TYPE_CHECKING = False  # typing itself costs more at import than the hint is worth

if TYPE_CHECKING:
    from pathlib import PurePath
    from typing import Any

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules",
                     "secret-paths.txt")
_globs: tuple | None = None


def globs() -> tuple:
    """The globs of `secret-paths.txt`, lowercased: (names, directories, paths)."""
    global _globs
    if _globs is None:
        names, dirs, paths = [], [], []
        with open(_FILE, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                line = line.lower().replace("\\", "/")
                if line.endswith("/"):
                    dirs.append(line.rstrip("/"))
                elif "/" in line:
                    paths.append(line)
                else:
                    names.append(line)
        _globs = (tuple(names), tuple(dirs), tuple(paths))
    return _globs


def is_secret_path(path: str | PurePath) -> bool:
    """True when `path` names a file whose content must not be read."""
    if isinstance(path, str):
        text = path
    else:
        # A PurePath exists only once pathlib has loaded, so the import doesn't need it.
        pathlib = sys.modules.get("pathlib")
        if pathlib is None or not isinstance(path, pathlib.PurePath):
            raise TypeError("path is a str or a PurePath")
        text = os.fspath(path)
    parts = [p for p in text.replace("\\", "/").lower().split("/") if p]
    if not parts:
        return False
    names, dirs, paths = globs()
    name = parts[-1]
    if any(fnmatchcase(name, g) for g in names):
        return True
    if any(fnmatchcase(part, g) for part in parts[:-1] for g in dirs):
        return True
    for g in paths:
        for i in range(len(parts)):
            if fnmatchcase("/".join(parts[i:]), g):
                return True
    return False


# -- a path in a tool call's input ----------------------------------------------------------

#: The characters stripped from each end of a word before it is tested as a path.
_PUNCTUATION = "\"'`()[]{}<>,;:|&"
#: The characters a word is split at, so that a path joined to an assignment, a shell
#: redirection or other text (`--env-file=.env`, `cat<.env`, `x>/a/.env`,
#: `{"path":"/a/.env","content":"A=1"}`) is tested, and shown, alone.
_JOINS = re.compile(r"[=<>|;&,:\"'`()\[\]{}\x00]+")


@lru_cache(maxsize=65536)
def _is_secret(word: str) -> bool:
    return is_secret_path(word)


def _candidates(text: str) -> Iterable[str]:
    """The words of `text` to test as paths, narrowest first: for each
    whitespace-separated word, the pieces between the characters of `_JOINS`, then
    the word with quotes and brackets taken off its ends, then the word itself. None of
    them holds whitespace, so the path a call is shown with is never more than one word."""
    for word in text.split():
        bare = word.strip(_PUNCTUATION)
        pieces = [p.strip(_PUNCTUATION) for p in _JOINS.split(bare)]
        if len(pieces) > 1:
            yield from (p for p in pieces if p)
        if bare != word:
            yield bare
        yield word


def secret_path(value: Any, _depth: int = 0) -> str | None:
    """The first path in a tool's input that `is_secret_path` names, or None.

    Each whitespace-separated word of every string counts, inside lists and nested
    objects too; a string that holds a JSON object or list is read into and searched the
    same way. What comes back is one word, never a string with whitespace in it."""
    if _depth > 32:
        return None
    if isinstance(value, str):
        for word in _candidates(value):
            if word and _is_secret(word):
                return word
        head = value.lstrip()[:1]
        if head in ("{", "["):
            try:
                inner = json.loads(value)
            except ValueError:
                return None
            if isinstance(inner, (dict, list)):
                return secret_path(inner, _depth + 1)
        return None
    if isinstance(value, Mapping):
        values: Iterable[Any] = value.values()
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        return None
    for item in values:
        found = secret_path(item, _depth + 1)
        if found is not None:
            return found
    return None
