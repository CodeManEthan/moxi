"""The document types file: the vocabulary validation reads, as data the user writes.

The file is TOML at `<root>/<types>` (the `types` setting, `doc-types.toml` by default).
notes reads it and never writes it, and no type name is spelled in notes' code:

    required = ["type", "project", "description", "tags", "updated"]   # the default

    [[type]]
    name = "..."
    homes = ["*/designs/*/design.md"]   # globs relative to the notes root
    nature = "record"                   # record | living-state | ephemeral
    priority = 50                       # optional; the highest matching type wins
    status = ["open", "closed"]         # optional: the values `status:` may take
    frozen = false                      # optional: a corpus nothing writes any more
    exempt = false                      # optional: another tool's format; never opened

In a home, `*` matches within one folder, `**` across folders, `?` one character and
`[...]` one of a set. A path is matched relative to the notes root, with `/` between its
parts.

`load(path)` gives `Types`: `present` False when there is no file, which declares no
vocabulary. A file that can't be read, isn't TOML, has a key notes doesn't know or a value
of the wrong kind raises `TypesError` with the file, the line and the reason.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .errors import NotesError
from .frontmatter import DEFAULT_REQUIRED, KEY

NATURES = ("record", "living-state", "ephemeral")
DEFAULT_PRIORITY = 50
#: The header key a type's `status` list governs.
STATUS_KEY = "status"
#: The header key that names a note's type.
TYPE_KEY = "type"

_TOP_KEYS = ("required", "type")
_TYPE_KEYS = ("name", "homes", "nature", "priority", "status", "frozen", "exempt")
_NEEDED = ("name", "homes", "nature")
_NAME = re.compile(r"^[^\s]+$")


class TypesError(NotesError):
    """The types file can't be used. `file`, `line` (1-based, or None) and `why` say
    where and why; the message is `<file>:<line>: <why>`."""

    def __init__(self, file, line: int | None, why: str):
        self.file, self.line, self.why = str(file), line, why
        where = f"{self.file}:{line}" if line else self.file
        super().__init__(f"{where}: {why}", f"fix the file, or move it aside to declare "
                                            f"no types")


@dataclass(frozen=True)
class DocType:
    name: str
    homes: tuple[str, ...]
    nature: str
    priority: int = DEFAULT_PRIORITY
    status: tuple[str, ...] = ()
    frozen: bool = False
    exempt: bool = False

    def permitted(self) -> tuple[str, ...]:
        """The header keys this type allows beyond the required ones."""
        return (STATUS_KEY,) if self.status else ()

    def as_dict(self) -> dict:
        return {"name": self.name, "homes": list(self.homes), "nature": self.nature,
                "priority": self.priority, "status": list(self.status),
                "frozen": self.frozen, "exempt": self.exempt}


@dataclass(frozen=True)
class Types:
    """What the types file declares. With `present` False there is no file: no types,
    and the default required keys."""

    path: str
    present: bool
    required: tuple[str, ...] = DEFAULT_REQUIRED
    types: tuple[DocType, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.types)

    def get(self, name: str) -> DocType | None:
        return next((t for t in self.types if t.name == name), None)

    def match(self, relative: str) -> list[DocType]:
        """Every type a home of which covers `relative`, a `/`-separated path under the
        notes root."""
        return [t for t in self.types if any(_compile(h).match(relative) for h in t.homes)]

    def as_dict(self) -> dict:
        return {"path": self.path, "present": self.present, "required": list(self.required),
                "types": [t.as_dict() for t in self.types]}


def winners(hits: list[DocType]) -> list[DocType]:
    """The hits at the highest priority: one is the file's type, more is a tie."""
    if not hits:
        return []
    top = max(hit.priority for hit in hits)
    return [hit for hit in hits if hit.priority == top]


_compiled: dict[str, re.Pattern] = {}


def _compile(glob: str) -> re.Pattern:
    found = _compiled.get(glob)
    if found is not None:
        return found
    out, index = [], 0
    while index < len(glob):
        char = glob[index]
        if glob.startswith("**/", index):
            out.append(r"(?:[^/]+/)*")
            index += 3
        elif glob.startswith("**", index):
            out.append(r".*")
            index += 2
        elif char == "*":
            out.append(r"[^/]*")
            index += 1
        elif char == "?":
            out.append(r"[^/]")
            index += 1
        elif char == "[" and glob.find("]", index + 1) != -1:
            close = glob.find("]", index + 1)
            inner = glob[index + 1:close]
            if inner.startswith("!"):
                inner = "^" + inner[1:]
            out.append("[" + inner.replace("\\", "\\\\") + "]")
            index = close + 1
        else:
            out.append(re.escape(char))
            index += 1
    found = _compiled[glob] = re.compile("".join(out) + r"\Z", re.DOTALL)
    return found


def path() -> str:
    """Where the types file is: the `types` setting under the notes root."""
    from . import settings

    places = settings.read()
    types = settings.types_file(places.settings["types"], places.settings.file)
    return os.path.join(places.root, types)


def load(file=None) -> Types:
    """The types file at `file` (default: `path()`). Raises `TypesError`."""
    import tomllib

    file = os.fspath(file if file is not None else path())
    try:
        with open(file, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return Types(path=file, present=False)
    except OSError as exc:
        raise TypesError(file, None, f"can't be read: {exc.strerror or exc}") from None
    try:
        text = raw.decode("utf-8")
        data = tomllib.loads(text)
    except UnicodeDecodeError:
        raise TypesError(file, None, "is not UTF-8 text") from None
    except tomllib.TOMLDecodeError as exc:
        found = re.search(r"\(at line (\d+), column \d+\)", str(exc))
        why = re.sub(r"\s*\(at line \d+, column \d+\)", "", str(exc)) or "is not TOML"
        raise TypesError(file, int(found.group(1)) if found else None,
                         f"is not TOML: {why}") from None
    return _types(file, text, data)


def _types(file: str, text: str, data: dict) -> Types:
    def bad(why: str, key: str | None = None, at: int | None = None):
        return TypesError(file, _line(text, key, at), why)

    for key in data:
        if key not in _TOP_KEYS:
            raise bad(f"{key!r} is not a key notes knows; it knows {', '.join(_TOP_KEYS)}",
                      key)
    required = data.get("required", list(DEFAULT_REQUIRED))
    if (not isinstance(required, list) or not required
            or not all(isinstance(k, str) and KEY.match(f"{k}:") for k in required)):
        raise bad("required must be a list of one or more frontmatter keys", "required")
    if len(set(required)) != len(required):
        raise bad("required names a key twice", "required")
    rows = data.get("type", [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise bad("type must be a list of tables, each written [[type]]", "type")
    types: list[DocType] = []
    for at, row in enumerate(rows):
        types.append(_row(row, at, bad))
    names = [t.name for t in types]
    for at, name in enumerate(names):
        if name in names[:at]:
            raise bad(f"the type {name!r} is declared twice", "name", at)
    return Types(path=file, present=True, required=tuple(required), types=tuple(types))


def _row(row: dict, at: int, bad) -> DocType:
    for key in row:
        if key not in _TYPE_KEYS:
            raise bad(f"{key!r} is not a key of a type; a type takes "
                      f"{', '.join(_TYPE_KEYS)}", key, at)
    for key in _NEEDED:
        if key not in row:
            raise bad(f"type {at + 1} has no {key}", None, at)
    name = row["name"]
    if not isinstance(name, str) or not _NAME.match(name):
        raise bad("name must be a word with no spaces", "name", at)
    homes = row["homes"]
    if not isinstance(homes, list) or not all(isinstance(h, str) for h in homes):
        raise bad(f"homes of {name!r} must be a list of globs", "homes", at)
    for home in homes:
        why = _bad_home(home)
        if why:
            raise bad(f"the home {home!r} of {name!r} {why}", "homes", at)
    nature = row["nature"]
    if nature not in NATURES:
        raise bad(f"nature of {name!r} must be one of {', '.join(NATURES)}", "nature", at)
    priority = row.get("priority", DEFAULT_PRIORITY)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise bad(f"priority of {name!r} must be a whole number", "priority", at)
    status = row.get("status", [])
    if (not isinstance(status, list)
            or not all(isinstance(s, str) and s.strip() for s in status)):
        raise bad(f"status of {name!r} must be a list of values", "status", at)
    flags = {}
    for key in ("frozen", "exempt"):
        flags[key] = row.get(key, False)
        if not isinstance(flags[key], bool):
            raise bad(f"{key} of {name!r} must be true or false", key, at)
    return DocType(name=name, homes=tuple(homes), nature=nature, priority=priority,
                   status=tuple(status), **flags)


def _bad_home(home: str) -> str | None:
    if not home:
        return "is empty"
    if home.startswith(("/", "~", "\\")) or re.match(r"^[A-Za-z]:", home):
        return "must be relative to the notes root"
    if "\\" in home:
        return "must separate folders with /"
    if ".." in home.split("/"):
        return "climbs out of the notes root with .."
    try:
        _compile(home)
    except re.error as exc:
        return f"is not a glob notes can read: {exc.msg}"
    return None


_TABLE = re.compile(r"^\s*\[")
_TYPE_TABLE = re.compile(r"^\s*\[\[\s*type\s*\]\]")


def _line(text: str, key: str | None, at: int | None) -> int | None:
    """The 1-based line of `key` in the top level (`at` None) or in the `at`-th
    `[[type]]` table; the table's own line when the key isn't found there."""
    table, header = -1, None
    pattern = re.compile(rf"""^\s*(?:{re.escape(key)}|"{re.escape(key)}"|'{re.escape(key)}')\s*="""
                         ) if key else None
    want = -1 if at is None else at
    for number, line in enumerate(text.splitlines(), 1):
        if _TYPE_TABLE.match(line):
            table += 1
            if table == want:
                header = number
            continue
        if _TABLE.match(line):
            table = -2
            continue
        if table == want and pattern is not None and pattern.match(line):
            return number
    return header


__all__ = ["NATURES", "DEFAULT_PRIORITY", "STATUS_KEY", "TYPE_KEY", "TypesError", "DocType",
           "Types", "winners", "path", "load"]
