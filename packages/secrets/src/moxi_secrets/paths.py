"""Secret paths: files whose content must not be read at all.

The globs live in `rules/secret-paths.txt`. A glob without a slash matches the file's
name; a glob ending in `/` names a directory and matches every path with a directory of
that name among its parts; any other glob matches the whole path or any run of its
trailing parts. Matching ignores case on every OS and reads `\\` as `/`. The file is
never opened.
"""

from __future__ import annotations

import os
import sys
from fnmatch import fnmatchcase
TYPE_CHECKING = False  # typing itself costs more at import than the hint is worth

if TYPE_CHECKING:
    from pathlib import PurePath

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules",
                     "secret-paths.txt")
_globs: tuple | None = None


def globs() -> tuple:
    """The globs of `secret-paths.txt`, lowercased: (names, directories, paths)."""
    global _globs
    if _globs is None:
        names, dirs, paths = [], [], []
        with open(_FILE, encoding="utf-8") as handle:
            for raw in handle:
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
