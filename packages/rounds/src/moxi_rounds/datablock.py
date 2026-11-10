"""Data blocks: the one part of a design or a report that a verb reads.

A file carries one data block: the fenced code block whose opening line says
`toml rounds`. `find(file, what)` reads that block as TOML and nothing else of the
file, so the prose around it, and any other fenced block, changes nothing a verb sees.

**What is refused.** A file with no such block, with two, with one that is never closed
or is not TOML, raises `BadBlock`, and so does a block that does not fit its schema:
a key the schema does not have, a key it needs and does not find, a value of the wrong
kind. Every problem names its line of the file and the edit that fixes it.

**A schema is data.** A `Table` lists its `Key`s and the tables under it. Each `Key`
holds the check of its value, the words of its fix, and the comment and the empty value
it has in the empty block. `check(found, table)` gives every problem of a block against
a table, and `empty(table)` writes the table's empty block: each key with an empty
value and one comment.

**Lines.** TOML's reader gives values and no lines, so `find` reads the block a second
time for where each table and each key starts. `Found.line_of("finding", 1, "state")`
is the line of that key, or of the table it is in when the key is written in a way
that second reading does not follow, or of the block's opening line.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from .errors import BadBlock, Problem

#: What follows the opening marks of a data block's fence.
INFO = "toml rounds"

#: The line that opens a data block, and the one that closes it.
OPENS = "```" + INFO
CLOSES = "```"

_OPEN = re.compile(r"( {0,3})(`{3,}|~{3,})(.*)")
_AT = re.compile(r"\s*\(at line ([0-9]+), column [0-9]+\)\Z")
_AT_END = re.compile(r"\s*\(at end of document\)\Z")


@dataclass(frozen=True)
class Key:
    """One key of a table: `check(value)` says what is wrong with a value, or None;
    `takes` says what a right value is, for the fix; `says` is its comment in the empty
    block and `blank` its empty value there, as TOML. A table without a `required`
    key is refused."""

    name: str
    check: Callable[[object], str | None] = field(repr=False)
    takes: str
    says: str
    blank: str = '""'
    required: bool = True


@dataclass(frozen=True)
class Table:
    """A table of a block: its `keys`, and the `tables` under it. `many` says it is
    written `[[name]]`, once for each entry, and not `[name]`. `says` is its comment in
    the empty block. `elsewhere` maps a key this table does not take to where that key
    does belong, for the refusal of one put here. The top of a block is a table with no
    name."""

    name: str
    says: str = ""
    keys: tuple[Key, ...] = ()
    tables: tuple[Table, ...] = ()
    many: bool = True
    elsewhere: Mapping[str, str] = field(default_factory=dict)

    def keeping(self, *names: str) -> Table:
        """This table with only the keys and the tables of those names, in its own
        order: one of the forms an entry may take, for the empty block."""
        return replace(self, keys=tuple(key for key in self.keys if key.name in names),
                       tables=tuple(one for one in self.tables if one.name in names))


@dataclass(frozen=True)
class Found:
    """A file's data block as read: the `file`, the `line` of the fence that opens the
    block, the `data` TOML gave, and the SHA-256 of the whole file as its bytes were
    when the block was read."""

    file: Path
    line: int
    data: dict
    sha256: str
    places: Mapping[tuple, int] = field(default_factory=dict, repr=False)

    def line_of(self, *path: str | int) -> int:
        """The line of the file where the table or key at `path` starts: names, and
        for an entry of a `[[table]]` its number from 0. The nearest table around it
        when its own line is not known, else the block's opening line."""
        while path:
            if path in self.places:
                return self.places[path]
            path = path[:-1]
        return self.line

    def lines(self, path: Sequence[str | int], entry: Mapping) -> dict[str, int]:
        """The line of each key of the table `entry` at `path`."""
        return {name: self.line_of(*path, name) for name in entry}


@dataclass(frozen=True, kw_only=True)
class Entry:
    """One table of a block as a verb reads it: `line` is the line of the file it
    starts on and `lines` the line of each key it carries."""

    line: int
    lines: Mapping[str, int] = field(default_factory=dict, repr=False, compare=False)

    def line_of(self, key: str) -> int:
        """The line of the file `key` is on, or the entry's own for a key it does not
        carry."""
        return self.lines.get(key, self.line)


# Reading the file.

def find(file: Path | str, what: str) -> Found:
    """The data block of `file`. `what` names what the block is read as, for the
    refusal: `"a design's data block"`.

    Raises `BadBlock` when the file can't be read or is not UTF-8, holds no data
    block or more than one, or its block is never closed or is not TOML.
    """
    file = Path(file)
    try:
        raw = file.read_bytes()
    except OSError as error:
        raise BadBlock(file, what, [Problem(
            None, f"it can't be read: {error.strerror or error}",
            "name a file that is there and that you may read")]) from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BadBlock(file, what, [Problem(
            None, "it is not UTF-8 text", "save the file as UTF-8")]) from None
    # Lines are counted as an editor counts them, by line feeds alone.
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    fences = _fences(lines)
    blocks = [fence for fence in fences if fence.info.split() == INFO.split()]
    if not blocks:
        raise BadBlock(file, what, [_none(fences)])
    first = blocks[0]
    if len(blocks) > 1:
        raise BadBlock(file, what, [Problem(
            blocks[1].start + 1,
            f"a second data block opens here, and the first opens on line "
            f"{first.start + 1}: a file carries one",
            "put everything in one block, or take one of the two out; a block shown "
            f"as an example opens with {CLOSES}toml alone")])
    if first.end is None:
        raise BadBlock(file, what, [Problem(
            first.start + 1, "the data block that opens here is never closed",
            f"close it with a line of {CLOSES} after its last line")])
    body = [_dedent(line, first.indent) for line in lines[first.start + 1:first.end]]
    source = "\n".join(body)
    # The block's first line is the line after its fence.
    top = first.start + 2
    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError as error:
        at, said = _decode_error(error, len(body))
        raise BadBlock(file, what, [Problem(
            top + at - 1, f"the block is not TOML: {said}",
            "correct the TOML on that line; a string is in quotes, a table is "
            "[[name]], and a key is written once")]) from None
    return Found(file=file, line=first.start + 1, data=data,
                 sha256=hashlib.sha256(raw).hexdigest(),
                 places=_places(body, top))


@dataclass(frozen=True)
class _Fence:
    """A fenced code block: the index of its opening line and of its closing one (None
    when it runs to the end of the file), how far the opening is indented, and what
    follows the opening marks."""

    start: int
    end: int | None
    indent: int
    info: str


def _fences(lines: Sequence[str]) -> list[_Fence]:
    """Every fenced code block of the file, in order. A fence inside another block is
    that block's text and opens nothing."""
    found: list[_Fence] = []
    opened: tuple[int, int, str, str] | None = None
    for index, line in enumerate(lines):
        if opened is None:
            match = _OPEN.fullmatch(line)
            # Backticks after backtick marks make the line inline code, not a fence.
            if match and not (match[2][0] == "`" and "`" in match[3]):
                opened = (index, len(match[1]), match[2], match[3].strip())
            continue
        start, indent, marks, info = opened
        closes = rf" {{0,3}}{re.escape(marks[0])}{{{len(marks)},}}[ \t]*"
        if re.fullmatch(closes, line):
            found.append(_Fence(start, index, indent, info))
            opened = None
    if opened is not None:
        start, indent, _marks, info = opened
        found.append(_Fence(start, None, indent, info))
    return found


def _none(fences: Sequence[_Fence]) -> Problem:
    """The problem of a file with no data block, naming the block that looks meant to
    be one when there is such a block."""
    fix = (f"put the data in one fenced code block that opens with a line of {OPENS} "
           f"and closes with a line of {CLOSES}")
    for fence in fences:
        words = fence.info.lower().replace("-", " ").replace("_", " ").split()
        if words[:1] == ["toml"] or "rounds" in words:
            return Problem(
                fence.start + 1,
                f"the file holds no data block; the block that opens here says "
                f"{fence.info!r} where a data block says {INFO!r}",
                f"open it with a line of {OPENS} if it is the data block; else " + fix)
    return Problem(None, "it holds no data block", fix)


def _dedent(line: str, indent: int) -> str:
    """A line of a block whose fence is indented, without that much of its own
    indent."""
    spaces = len(line) - len(line.lstrip(" "))
    return line[min(indent, spaces):]


def _decode_error(error: tomllib.TOMLDecodeError, count: int) -> tuple[int, str]:
    """The line of the block a TOML error is on, counted from 1, and what it says.
    An error at the end of the block is on its last line."""
    said = str(error)
    at = getattr(error, "lineno", None)
    match = _AT.search(said)
    if match:
        at = at or int(match[1])
        said = said[:match.start()]
    said = _AT_END.sub("", said)
    if not isinstance(at, int) or not 1 <= at <= count:
        at = max(count, 1)
    return at, said.rstrip(". ")


# Where each table and key starts.

def _places(body: Sequence[str], top: int) -> dict[tuple, int]:
    """The line of the file where each table and each key of the block starts, `top`
    being the line of `body`'s first. A table is keyed by its path, names and entry
    numbers, and a key by its table's path and its name. What is written so that
    this reading can't follow it (a table given inline, a dotted key) has no line of
    its own, and nothing after a statement it can't read has one."""
    places: dict[tuple, int] = {}
    # The number of the last entry of each `[[table]]` met, by the table's path.
    last: dict[tuple, int] = {}
    here: tuple = ()
    starts = _starts("\n".join(body))
    for number, start in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(body)
        text = "\n".join(body[start:end])
        try:
            read = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return places
        if not read:
            return places
        if not text.lstrip().startswith("["):
            places.setdefault(here + (next(iter(read)),), top + start)
            continue
        names, many = _header(read)
        path: tuple = ()
        for depth, name in enumerate(names):
            path += (name,)
            if depth == len(names) - 1 and many:
                last[path] = last.get(path, -1) + 1
                # The list itself is placed where its first entry starts.
                places.setdefault(path, top + start)
            if path in last:
                path += (last[path],)
        here = path
        places.setdefault(here, top + start)
    return places


def _header(read: dict) -> tuple[list[str], bool]:
    """The names of a table header read on its own, and whether it is a `[[table]]`:
    such a header reads as one name inside another, down to an empty table or a list
    of one."""
    names: list[str] = []
    value: object = read
    while isinstance(value, dict) and value:
        name, value = next(iter(value.items()))
        names.append(name)
    return names, isinstance(value, list)


def _starts(source: str) -> list[int]:
    """The index of each line of a TOML text on which a statement starts: a line with
    something on it that is not inside a string or a list that an earlier line
    opened."""
    starts: list[int] = []
    line = depth = at = 0
    fresh = True
    size = len(source)
    while at < size:
        char = source[at]
        if char == "\n":
            line, fresh, at = line + 1, True, at + 1
        elif char in " \t":
            at += 1
        elif char == "#":
            at = _until(source, at, "\n")
        else:
            if fresh and depth == 0:
                starts.append(line)
            fresh = False
            if char in "\"'":
                end = _string_end(source, at)
                line += source.count("\n", at, end)
                at = end
            else:
                if char in "[{":
                    depth += 1
                elif char in "]}":
                    depth = max(depth - 1, 0)
                at += 1
    return starts


def _until(source: str, at: int, mark: str) -> int:
    """The index of the next `mark` from `at`, or the text's end."""
    found = source.find(mark, at)
    return len(source) if found < 0 else found


def _string_end(source: str, at: int) -> int:
    """The index after the string that opens at `at`. A one-line string that is never
    closed ends with its line."""
    quote = source[at]
    escapes = quote == '"'
    if source.startswith(quote * 3, at):
        scan = at + 3
        while scan < len(source):
            if escapes and source[scan] == "\\":
                scan += 2
            elif source.startswith(quote * 3, scan):
                scan += 3
                # A string may end in one or two quotes of its own before the three.
                while scan < len(source) and source[scan] == quote:
                    scan += 1
                return scan
            else:
                scan += 1
        return len(source)
    scan = at + 1
    while scan < len(source) and source[scan] != "\n":
        if escapes and source[scan] == "\\":
            scan += 2
        elif source[scan] == quote:
            return scan + 1
        else:
            scan += 1
    return min(scan, len(source))


# Checking a block against a table.

def check(found: Found, table: Table) -> list[Problem]:
    """Every problem of the block against `table`, the schema of its top: a key no
    table has, a key a table needs and does not carry, a value its key's check
    refuses, a table written as something else. Empty when the block fits."""
    problems: list[Problem] = []
    _check(found, table, found.data, (), problems)
    return problems


def _check(found: Found, table: Table, entry: Mapping, path: tuple,
           problems: list[Problem]) -> None:
    where = _where(path, many=table.many)
    line = found.line_of(*path)
    known = [key.name for key in table.keys] + [one.name for one in table.tables]
    for name in entry:
        if name not in known:
            belongs = table.elsewhere.get(name)
            problems.append(Problem(
                found.line_of(*path, name),
                f"`{name}` is not a key of {where}" + (f": {belongs}" if belongs else ""),
                f"take it out; {where} takes {', '.join(known)}"))
    for key in table.keys:
        if key.name not in entry:
            if key.required:
                problems.append(Problem(line, f"{where} has no `{key.name}`",
                                        f"add {key.name} = {key.takes}"))
            continue
        wrong = key.check(entry[key.name])
        if wrong:
            problems.append(Problem(found.line_of(*path, key.name),
                                    f"`{key.name}` {wrong}",
                                    f"give {key.name} = {key.takes}"))
    for under in table.tables:
        if under.name not in entry:
            continue
        value = entry[under.name]
        at = path + (under.name,)
        if not under.many:
            if isinstance(value, dict):
                _check(found, under, value, at, problems)
                continue
        elif isinstance(value, list) and all(isinstance(one, dict) for one in value):
            for number, one in enumerate(value):
                _check(found, under, one, at + (number,), problems)
            continue
        header = _where(at, many=under.many)
        problems.append(Problem(found.line_of(*at),
                                f"`{under.name}` is {shown(value)}, not a table",
                                f"write it as {header.removeprefix('this ')}"))


def _where(path: Sequence[str | int], *, many: bool) -> str:
    """How a refusal names the table at `path`: its header, or the block itself."""
    names = ".".join(part for part in path if isinstance(part, str))
    if not names:
        return "the block"
    return f"this `[[{names}]]`" if many else f"this `[{names}]`"


def shown(value: object) -> str:
    """A value as a refusal prints it: as it is written in TOML, cut when long."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (str, int, float)):
        text = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, list):
        text = "[" + ", ".join(shown(one) for one in value) + "]"
    elif isinstance(value, dict):
        text = "a table"
    else:
        text = str(value)
    return text if len(text) <= 60 else text[:57] + "..."


# The keys a schema is made of.

def is_line(value: object) -> bool:
    """Whether `value` is text on one line that says something."""
    return (isinstance(value, str) and bool(value.strip())
            and "\n" not in value and "\r" not in value)


def line(name: str, says: str, *, required: bool = True) -> Key:
    """A key that holds one line of text. One a table needs says something; one it
    can go without may be left empty."""

    def check(value: object) -> str | None:
        if not isinstance(value, str):
            return f"is {shown(value)}, not text"
        if "\n" in value or "\r" in value:
            return "has a line break, and it is one line"
        if required and not value.strip():
            return "says nothing"
        return None

    return Key(name, check, '"one line of text"', says, required=required)


def word(name: str, words: Sequence[str], says: str, *, required: bool = True,
         blank: str = '""') -> Key:
    """A key that holds one of `words`."""
    listed = " | ".join(words)

    def check(value: object) -> str | None:
        if value in words and isinstance(value, str):
            return None
        return f"is {shown(value)}, which is none of {listed}"

    return Key(name, check, "one of " + ", ".join(f'"{one}"' for one in words),
               f"{listed}; {says}" if says else listed, blank=blank, required=required)


def ident(name: str, pattern: re.Pattern, shape: str, says: str, *,
          required: bool = True) -> Key:
    """A key that holds an id: text that `pattern` matches whole, described to the
    writer as `shape` (`B<n>`)."""

    def check(value: object) -> str | None:
        if isinstance(value, str) and pattern.fullmatch(value):
            return None
        return f"is {shown(value)}, which is not {shape}"

    return Key(name, check, f'"{shape}"', says, required=required)


def idents(name: str, pattern: re.Pattern, shape: str, says: str, *,
           required: bool = False, fewest: int = 0) -> Key:
    """A key that holds a list of ids, each once, and at least `fewest` of them."""

    def check(value: object) -> str | None:
        return wrong_ids(value, pattern, shape, fewest=fewest)

    return Key(name, check, f'a list of {shape} ids, as ["{shape}"]', says, blank="[]",
               required=required)


def wrong_ids(value: object, pattern: re.Pattern, shape: str, *,
              fewest: int = 0) -> str | None:
    """What is wrong with `value` as a list of ids that `pattern` matches whole, each
    once and at least `fewest` of them, or None."""
    if not isinstance(value, list):
        return f"is {shown(value)}, not a list"
    for one in value:
        if not isinstance(one, str) or not pattern.fullmatch(one):
            return f"holds {shown(one)}, which is not {shape}"
    twice = [one for one in dict.fromkeys(value) if value.count(one) > 1]
    if twice:
        return f"names {twice[0]} twice"
    if len(value) < fewest:
        return ("is an empty list, and it names at least one" if not value
                else f"names {len(value)}, and it names at least {fewest}")
    return None


def count(name: str, says: str, *, required: bool = True) -> Key:
    """A key that holds a whole number from 1 up."""

    def check(value: object) -> str | None:
        # A TOML true is a Python int too, and is no number here.
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return None
        return f"is {shown(value)}, not a whole number from 1 up"

    return Key(name, check, "a whole number from 1 up, without quotes", says,
               blank="0", required=required)


def flag(name: str, says: str, *, required: bool = True) -> Key:
    """A key that holds true or false."""

    def check(value: object) -> str | None:
        return None if isinstance(value, bool) else f"is {shown(value)}, not true or false"

    return Key(name, check, "true or false, without quotes", says, blank="false",
               required=required)


# The empty block.

def empty(table: Table) -> str:
    """The empty block of `table`, the schema of a block's top, fences included: every
    key with its empty value and one comment that says what goes there, and every
    table under it once. A key the block can go without says so in its comment."""
    rows: list[tuple[str, str]] = []
    _rows(table, (), rows)
    width = max(len(code) for code, _comment in rows) + 2
    lines = [OPENS]
    for code, comment in rows:
        lines.append(f"{code:<{width}}# {comment}" if comment else code)
    return "\n".join([*lines, CLOSES]) + "\n"


def _rows(table: Table, names: tuple[str, ...], rows: list[tuple[str, str]]) -> None:
    indent = "  " * max(len(names) - 1, 0)
    if names:
        if rows:
            rows.append(("", ""))
        header = ".".join(names)
        rows.append((indent + (f"[[{header}]]" if table.many else f"[{header}]"),
                     table.says))
    for key in table.keys:
        says = key.says if key.required else f"optional: {key.says}"
        rows.append((f"{indent}{key.name} = {key.blank}", says))
    for under in table.tables:
        _rows(under, names + (under.name,), rows)


__all__ = ["INFO", "OPENS", "CLOSES", "Key", "Table", "Found", "Entry", "find", "check",
           "shown", "is_line", "line", "word", "ident", "idents", "wrong_ids", "count",
           "flag", "empty"]
