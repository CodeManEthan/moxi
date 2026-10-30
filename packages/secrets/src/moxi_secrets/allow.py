"""The allowlist: hits someone has judged and kept, in one line format and two files.

    # path <TAB> set/rule <TAB> match <TAB> YYYY-MM-DD <TAB> why

The public file (`.secrets-allow` at a repo's root) takes credentials lines only, each
about a value already in the public text it allows. The private file takes lines of every
set, `text:` matches for private names among them; it must lie inside a directory named
`.git` or inside secrets' config directory, checked after `..` and every symlink are
resolved, so git never tracks it.

A malformed line raises `AllowlistError` naming the file and the line number, never the
line's text: an allowlist that half-parses is a gate that half-opens.
"""

from __future__ import annotations

import re
TYPE_CHECKING = False  # typing itself costs more at import than the hint is worth

if TYPE_CHECKING:
    from pathlib import Path

_SETS = ("credentials", "broad", "private-names", "personal-data")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SHA = re.compile(r"sha256:([0-9A-Fa-f]{16})")
_SEPARATED = re.compile(r"[\s_-]+")
_SPACE = re.compile(r"\s")
_ENDS = re.compile(r"^[\s_-]+|[\s_-]+$")


class AllowlistError(ValueError):
    """A line or a file the allowlist refuses. Names the file and line, never the text."""


def normalise_text(text: str) -> str:
    """How a `text:` value and a hit's text are compared: casefolded, every run of
    whitespace, `-` and `_` that holds whitespace as one space, `_` as `-`, and those
    characters trimmed from both ends."""
    folded = text.casefold()
    joined = _SEPARATED.sub(lambda m: " " if _SPACE.search(m.group()) else m.group(),
                            folded)
    return _ENDS.sub("", joined.replace("_", "-"))


class _Line:
    __slots__ = ("path", "set", "rule", "kind", "value")

    def __init__(self, path, set_name, rule, kind, value):
        self.path, self.set, self.rule, self.kind, self.value = (path, set_name, rule,
                                                                 kind, value)

    def keeps(self, hit, text, path) -> bool:
        if self.path != "*":
            if path is None:
                return False
            if self.path.endswith("/"):
                if not path.startswith(self.path):
                    return False
            elif path != self.path:
                return False
        if self.set != "*":
            if hit.set != self.set or (self.rule != "*" and hit.rule != self.rule):
                return False
        if self.kind == "*":
            return True
        if self.kind == "sha256":
            return hit.fingerprint.lower() == self.value
        return hit.set == "private-names" and \
            normalise_text(text[hit.start:hit.end]) == self.value


def _parse(raw: str, where: str, n: int, public: bool) -> _Line | None:
    line = raw.rstrip("\r\n")
    if not line.strip() or line.lstrip().startswith("#"):
        return None

    def refuse(problem):
        return AllowlistError(f"{where}: line {n}: {problem}")

    fields = line.split("\t")
    if len(fields) != 5:
        raise refuse(f"has {len(fields)} tab-separated fields, not 5 (path, set/rule, "
                     "match, date, why)")
    path, which, match, day, _why = fields
    if not path or path.startswith("/") or "\\" in path:
        raise refuse("the path is not *, @author, or a repo-relative path with /")
    if which == "*":
        set_name, rule = "*", "*"
    else:
        set_name, slash, rule = which.partition("/")
        if not slash or not rule:
            raise refuse("the set/rule field is not *, <set>/*, or <set>/<rule id>")
        if set_name not in _SETS:
            raise refuse("names an unknown set")
    if match == "*":
        kind, value = "*", None
    elif match.startswith("sha256:"):
        found = _SHA.fullmatch(match)
        if not found:
            raise refuse("a sha256: match takes exactly 16 hex digits")
        kind, value = "sha256", "sha256:" + found.group(1).lower()
    elif match.startswith("text:"):
        if public:
            raise refuse("a text: match belongs only in the private file")
        if set_name != "private-names":
            raise refuse("a text: match is only for private-names hits")
        kind, value = "text", normalise_text(match[len("text:"):])
        if not value:
            raise refuse("the text: match is empty")
    else:
        raise refuse("the match is not *, sha256:<16 hex>, or text:<text>")
    if not _DATE.fullmatch(day):
        raise refuse("the date is not YYYY-MM-DD")
    from datetime import date

    try:
        date.fromisoformat(day)
    except ValueError:
        raise refuse("the date is not a real date") from None
    if public and set_name != "credentials":
        raise refuse("the public file takes credentials lines only; put this line in the "
                     "private file")
    if path == "*" and rule == "*" and match == "*":
        # A caller who wants a set off doesn't ask for it; such a line only opens the gate.
        raise refuse("allows a whole set everywhere: the path and match are *, and the "
                     "set/rule is * or <set>/*")
    return _Line(path, set_name, rule, kind, value)


def private_place_ok(path: Path) -> bool:
    """True when `path`, resolved, lies inside a `.git` directory or secrets' config
    directory."""
    from pathlib import Path

    resolved = Path(path).resolve()
    if ".git" in resolved.parent.parts:
        return True
    from moxi.paths import Layout

    config = Layout("secrets").config_dir().resolve()
    return resolved.is_relative_to(config)


class Allowlist:
    """The lines of a public file, a private file, or both."""

    def __init__(self, lines=(), own_text: str | None = None):
        self._lines = tuple(lines)
        self._own_text = own_text
        self._own_values = frozenset(line.value for line in self._lines
                                     if line.kind == "text")

    def __len__(self) -> int:
        return len(self._lines)

    def __repr__(self) -> str:
        return f"Allowlist({len(self._lines)} lines)"

    @classmethod
    def read(cls, public: Path | None = None, private: Path | None = None) -> Allowlist:
        """Read either file or both. A file that does not exist adds no line."""
        from pathlib import Path

        lines = []
        own_text = None
        for path, is_public in ((public, True), (private, False)):
            if path is None:
                continue
            path = Path(path)
            if not is_public and not private_place_ok(path):
                raise AllowlistError(f"{path}: the private allowlist must lie inside a .git "
                                     "directory or the secrets config directory, where git "
                                     "never tracks it")
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            except UnicodeDecodeError:
                raise AllowlistError(f"{path}: is not UTF-8") from None
            if not is_public:
                own_text = text
            for n, raw in enumerate(text.split("\n"), 1):
                parsed = _parse(raw, str(path), n, is_public)
                if parsed is not None:
                    lines.append(parsed)
        return cls(lines, own_text)

    def keeps(self, hit, text: str, path: str | None) -> bool:
        """True when a line keeps `hit`, found in `text` scanned with `path`."""
        if any(line.keeps(hit, text, path) for line in self._lines):
            return True
        # The private file has to spell what it keeps: scanned itself, its own text:
        # values are no hit.
        return (hit.set == "private-names" and self._own_text is not None
                and text == self._own_text
                and normalise_text(text[hit.start:hit.end]) in self._own_values)
