"""The frontmatter reader: read a note's YAML header, and add one missing key to it.

`parse(text)` reads a small YAML subset: plain and quoted scalars, flow sequences
(`[a, b]`), block sequences (`- a`) and block scalars (`|`, `>`, with a chomping
indicator). It never raises. "Could not read it" and "there is nothing there" are kept
apart: a header that can't be read is `ok=False` with a reason, and a file with no
header is `present=False`, `ok=True`. A nested map is kept as its lines, not modelled.

`upsert(text, key, value)` adds `key` as one line when the header lacks it, in the
canonical order of the required keys, and creates the header when there is none. It
never replaces a key that is there, never writes into a header it can't read, and never
round-trips the YAML: every other byte of the file stays as it was, line endings
included. A second call with the same key changes nothing.

notes' API exports both (`frontmatter`, `upsert_frontmatter`). This module is not a
public import path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: The keys a note's header must carry when the types file names none.
DEFAULT_REQUIRED: tuple[str, ...] = ("type", "project", "description", "tags", "updated")

FENCE = "---"
_CLOSERS = ("---", "...")
_BOM = "\ufeff"
KEY = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:(?P<rest>.*)$")
_BLOCK = re.compile(r"^[|>][+-]?\d*$")


@dataclass
class Frontmatter:
    """A note's header. `present` (a block opens the file) and `ok` (it could be read)
    are separate answers; `reason` says why when either is no. `start` is the line index
    of the opening fence and `end` that of the line after the closing one."""

    present: bool
    ok: bool
    reason: str | None = None
    fields: dict[str, object] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    start: int = 0
    end: int = 0

    def get(self, key: str) -> object | None:
        return self.fields.get(key)

    def missing(self, required=DEFAULT_REQUIRED) -> list[str]:
        """The keys of `required` the header lacks, in `required`'s order."""
        return [key for key in required if key not in self.fields]

    def unknown(self, known) -> list[str]:
        """The header's keys outside `known`, in the header's order."""
        return [key for key in self.order if key not in known]

    def as_dict(self) -> dict:
        return {"present": self.present, "ok": self.ok, "reason": self.reason,
                "fields": dict(self.fields), "order": list(self.order)}


def _strip(line: str) -> str:
    return line.rstrip("\r\n")


def parse(text: str) -> Frontmatter:
    """The header of `text`. Never raises."""
    lines = [_strip(line) for line in text.splitlines(keepends=True)]
    if not lines or lines[0].lstrip(_BOM).strip() != FENCE:
        return Frontmatter(present=False, ok=True,
                           reason="no frontmatter: the file does not open with ---")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() in _CLOSERS), None)
    if close is None:
        return Frontmatter(present=True, ok=False, end=len(lines),
                           reason="the frontmatter opens with --- and is never closed")
    try:
        fields, order = _block(lines[1:close])
    except ValueError as exc:
        return Frontmatter(present=True, ok=False, end=close + 1,
                           reason=f"the frontmatter can't be read: {exc}")
    return Frontmatter(present=True, ok=True, fields=fields, order=order, end=close + 1)


def _block(body: list[str]) -> tuple[dict[str, object], list[str]]:
    fields: dict[str, object] = {}
    order: list[str] = []
    index = 0
    while index < len(body):
        line = body[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1] in (" ", "\t"):
            raise ValueError(f"line {index + 2} is indented but follows no key")
        match = KEY.match(line)
        if match is None:
            raise ValueError(f"line {index + 2} is not `key: value`: {line.strip()!r}")
        key, rest = match.group("key"), match.group("rest").strip()
        if key in fields:
            raise ValueError(f"the key {key!r} is there twice")
        if _BLOCK.match(rest):
            value, index = _block_scalar(body, index + 1, folded=rest[0] == ">",
                                         chomp=rest[1:2])
        elif rest:
            value = _scalar(rest)
            index += 1
        else:
            value, index = _indented(body, index + 1)
        fields[key] = value
        order.append(key)
    return fields, order


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block_scalar(body: list[str], index: int, *, folded: bool,
                  chomp: str) -> tuple[str, int]:
    """`key: |` or `key: >`: every more-indented line, dedented by the first one's."""
    collected: list[str] = []
    base: int | None = None
    while index < len(body):
        line = body[index]
        if line.strip() and _indent(line) == 0:
            break
        if base is None and line.strip():
            base = _indent(line)
        collected.append(line[base:] if base else line.lstrip(" ") if line.strip() else "")
        index += 1
    while collected and not collected[-1].strip():
        collected.pop()
    text = (" ".join(part.strip() for part in collected if part.strip()) if folded
            else "\n".join(collected))
    if chomp == "+":
        text += "\n"
    return text, index


def _indented(body: list[str], index: int) -> tuple[object, int]:
    """A key with no value on its line: a block sequence, a nested map, or empty."""
    items: list[object] = []
    nested: list[str] = []
    while index < len(body):
        line = body[index]
        if line.strip() and _indent(line) == 0:
            break
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(_scalar(stripped[2:].strip()))
        elif stripped == "-":
            items.append("")
        elif stripped:
            nested.append(stripped)
        index += 1
    if items:
        return items, index
    if nested:
        return "\n".join(nested), index
    return "", index


def _scalar(raw: str) -> object:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [] if not inner else [_scalar(part) for part in _split_flow(inner)]
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        inner = raw[1:-1]
        return inner.replace("''", "'") if raw[0] == "'" else inner
    # ` #` starts a comment on a plain scalar; a `#` glued to a word (`issue#12`) is
    # part of the value.
    cut = re.search(r"\s+#", raw)
    if cut:
        raw = raw[:cut.start()].rstrip()
    return raw


def _split_flow(inner: str) -> list[str]:
    parts, current, quote = [], [], None
    for char in inner:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
            current.append(char)
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


# --- adding a key ------------------------------------------------------------------------

def render(value) -> str:
    """One value as YAML the reader reads back the same. A list renders flow-style."""
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_render_item(item) for item in value) + "]"
    text = str(value)
    if text == "":
        return "''"
    if (text[0] in "[]{}#&*!|>%@`'\"," or text.strip() != text or ": " in text
            or text.endswith(":") or re.search(r"\s#", text) or "\n" in text
            or "\r" in text):
        return "'" + text.replace("'", "''") + "'"
    return text


def _render_item(item) -> str:
    """One list item: as `render`, quoted too when it holds a character the flow reader
    treats as structure (a comma, a bracket or brace, or a quote, which opens a quoted span
    anywhere in an item), so the reader gives back the one item."""
    text = render(item)
    if not text.startswith("'") and any(char in text for char in ",[]{}'\""):
        return "'" + text.replace("'", "''") + "'"
    return text


def upsert(text: str, key: str, value, *, order=DEFAULT_REQUIRED) -> tuple[str, bool]:
    """`text` with `key: value` added, and whether it changed. `order` is the canonical
    order a required key lands in. A key that is there is never replaced, and a header
    that can't be read is never written into: both return `(text, False)`."""
    if KEY.match(f"{key}:") is None:
        raise ValueError(f"{key!r} is not a frontmatter key")
    if "\n" in render(value) or "\r" in render(value):
        raise ValueError("a value must fit on one line")
    parsed = parse(text)
    lines = text.splitlines(keepends=True)
    newline = _newline(lines)
    line = f"{key}: {render(value)}{newline}"
    if not parsed.present:
        bom = _BOM if text.startswith(_BOM) else ""
        block = f"{bom}{FENCE}{newline}{line}{FENCE}{newline}"
        rest = text[len(bom):]
        return block + (newline + rest if rest else ""), True
    if not parsed.ok or key in parsed.fields:
        return text, False
    lines.insert(_insert_at(lines, parsed.start + 1, parsed.end - 1, key, tuple(order)),
                 line)
    return "".join(lines), True


def _newline(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith(("\n", "\r")):
            return line[-1]
    return "\n"


def _key_at(lines: list[str], index: int) -> str | None:
    match = KEY.match(_strip(lines[index]))
    return match.group("key") if match else None


def _span_end(lines: list[str], index: int, end: int) -> int:
    """The line after `index`'s key and its indented or blank continuation."""
    last = index + 1
    while last < end and (not lines[last].strip() or lines[last][:1] in (" ", "\t")):
        last += 1
    return last


def _insert_at(lines: list[str], start: int, end: int, key: str, order) -> int:
    """After the last key of `order` that comes before `key`, else at the top; a key
    outside `order` goes last. Keys outside `order` keep their places."""
    if key not in order:
        return end
    rank = order.index(key)
    at = start
    for index in range(start, end):
        other = _key_at(lines, index)
        if other in order and order.index(other) < rank:
            at = _span_end(lines, index, end)
    return at


__all__ = ["DEFAULT_REQUIRED", "Frontmatter", "parse", "upsert", "render"]
