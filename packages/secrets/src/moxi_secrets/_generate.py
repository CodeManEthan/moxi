"""Resolve the rule files into `rules/sets.json`, with each pattern's fallback translation.

    python -m moxi_secrets._generate [--check] [RULES_DIR]

The maintainer runs this when a rule file changes, and the bump runs it on every new
gitleaks tag. Nothing imports it at runtime: the scanner loads the generated file, because
parsing the TOML and compiling every rule costs too much for a git hook.

It reads `credentials.toml`, following its `[extend]` to the pinned upstream file and
applying `disabledRules`, and `broad.toml`, which takes every rule credentials disables.
It writes, with `\\n` line endings, the format version, the upstream pin, the global
allowlists, and per set its rules and a `dropped` list. Each pattern is kept verbatim for
RE2 beside its translation for Python's `re`, the engine used where RE2 is not installed.

A rule is left out, and listed as dropped with the reason, when it uses a gitleaks field
the scanner does not implement, when `drop.txt` names it, or when the translator refuses
one of its patterns, so both engines always run the same rules. Anything the gitleaks
binary itself would refuse, an unknown field at the top level or in a global allowlist, a
local rule id that clashes with another, or a pin that does not match the upstream bytes
stops generation.

`--check` writes nothing and exits 1 when `sets.json` differs from a fresh generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
import warnings
from pathlib import Path

from moxi import files

FORMAT = 1
RULES = Path(__file__).resolve().parent / "rules"
OUTPUT = "sets.json"
CREDENTIALS = "credentials.toml"
BROAD = "broad.toml"
DROP = "drop.txt"
PIN = "upstream/PIN"

# The gitleaks fields the scanner implements. A rule or rule allowlist using any other
# field is dropped; any other field at the top level or in a global allowlist is refused.
TOP_FIELDS = frozenset({"title", "minVersion", "extend", "allowlist", "allowlists", "rules"})
EXTEND_FIELDS = frozenset({"path", "disabledRules"})
RULE_FIELDS = frozenset({"id", "description", "regex", "keywords", "entropy", "secretGroup",
                         "path", "tags", "allowlists"})
ALLOWLIST_FIELDS = frozenset({"description", "regexes", "regexTarget", "stopwords",
                              "condition", "paths"})
PIN_FIELDS = frozenset({"tag", "commit", "sha256", "fetched"})


class GenerationError(Exception):
    """The rule files cannot be resolved; the message says which file and why."""


class Unsupported(ValueError):
    """The fallback translator cannot express this pattern in Python's `re`."""


# --- the fallback translator --------------------------------------------------------------
#
# It reads a pattern in Go's regexp syntax (which RE2 shares) and writes one Python's `re`
# matches the same way. What it changes:
#
# - It writes no i flag. Under case-insensitive matching each literal and each class becomes
#   the case-sensitive class of Go's simple case-fold orbits of its characters (unicode.
#   SimpleFold): k as [Kk\u212a], s as [Ss\u017f], every other ASCII letter with its other
#   case, a negated class as the complement of the folded one. Python's own folding is wider
#   (i with \u0130 and \u0131, and more by Unicode version), so it is never used. A
#   case-insensitive literal or class item outside ASCII is refused: its orbit is not known.
# - \z becomes \Z, Python's end of text.
# - $ outside multi-line mode becomes \Z: in Go it is the end of the text, while Python's
#   also matches before a final newline.
# - \s, \w and \d, and their negations, become Go's ASCII classes ([\t\n\f\r ], [0-9A-Za-z_],
#   [0-9]); Python's are Unicode. \b and \B become ASCII boundaries.
# - POSIX classes such as [[:alnum:]] become their ranges; Python reads them as the
#   characters [ : a l n u m.
# - A flag group such as (?i) after the start of a pattern becomes a scoped group over the
#   rest of each remaining alternative of its group: Python allows a global flag only at
#   the start, and in Go a flag holds to the end of its group, across later alternatives.
# - (?<name>...) becomes (?P<name>...), {,n} is a literal brace as in Go, and every class is
#   written out item by item with its characters escaped, so none of Python's own class
#   syntax ([[, --, &&, ~~, ||) can change its meaning.
#
# It refuses Unicode classes (\p, \P), \Q...\E, \C, the U flag, a case-insensitive item
# outside ASCII and anything else it does not know; a refused pattern drops its rule.

_PERL = {
    "d": ((0x30, 0x39),),
    "s": ((0x09, 0x0A), (0x0C, 0x0D), (0x20, 0x20)),
    "w": ((0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A)),
}
_POSIX = {
    "alnum": ((0x30, 0x39), (0x41, 0x5A), (0x61, 0x7A)),
    "alpha": ((0x41, 0x5A), (0x61, 0x7A)),
    "ascii": ((0x00, 0x7F),),
    "blank": ((0x09, 0x09), (0x20, 0x20)),
    "cntrl": ((0x00, 0x1F), (0x7F, 0x7F)),
    "digit": ((0x30, 0x39),),
    "graph": ((0x21, 0x7E),),
    "lower": ((0x61, 0x7A),),
    "print": ((0x20, 0x7E),),
    "punct": ((0x21, 0x2F), (0x3A, 0x40), (0x5B, 0x60), (0x7B, 0x7E)),
    "space": ((0x09, 0x0D), (0x20, 0x20)),
    "upper": ((0x41, 0x5A),),
    "word": ((0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A)),
    "xdigit": ((0x30, 0x39), (0x41, 0x46), (0x61, 0x66)),
}
_CONTROL = {"a": 0x07, "f": 0x0C, "n": 0x0A, "r": 0x0D, "t": 0x09, "v": 0x0B}
_SPECIAL = frozenset(".^$*+?{}[]\\|()")
_FLAGS = re.compile(r"\(\?([imsU]*)(?:-([imsU]*))?([:)])")
_NAMED = re.compile(r"\(\?P?<([A-Za-z0-9_]+)>")
_REPEAT = re.compile(r"\{(\d+)(,(\d*))?\}")
_OCTAL = frozenset("01234567")

def _ascii_orbits() -> dict[int, frozenset[int]]:
    """Go's case-fold orbits of the ASCII letters: each letter with its other case, plus
    the Kelvin sign with k and the long s with s. No other character folds to an ASCII
    letter, and an ASCII character that is not a letter folds only to itself."""
    orbits = {c: frozenset({c, c ^ 0x20}) for c in [*range(0x41, 0x5B), *range(0x61, 0x7B)]}
    for members in (frozenset({0x4B, 0x6B, 0x212A}), frozenset({0x53, 0x73, 0x17F})):
        for c in members:
            orbits[c] = members
    return orbits


_ORBIT = _ascii_orbits()


def orbit(cp: int) -> tuple[int, ...]:
    """The translator's fold orbit of the ASCII character `cp`, sorted (the bump checks it
    against Go's `unicode.SimpleFold`)."""
    if cp > 0x7F:
        raise ValueError("the translator holds the orbits of ASCII only")
    return tuple(sorted(_ORBIT.get(cp, (cp,))))


def _fold(items) -> tuple[tuple[int, int], ...]:
    """The ranges `items` with every ASCII character's fold orbit added, as Go's (?i) does."""
    chars: set[int] = set()
    for lo, hi in items:
        if hi > 0x7F:
            raise Unsupported("a non-ASCII character under case-insensitive matching")
        for c in range(lo, hi + 1):
            chars |= _ORBIT.get(c, {c})
    out: list[list[int]] = []
    for c in sorted(chars):
        if out and out[-1][1] == c - 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return tuple((lo, hi) for lo, hi in out)


def _char(cp: int, in_class: bool) -> str:
    """One character, escaped where Python's `re` could read it as syntax."""
    c = chr(cp)
    if c.isascii() and c.isalnum():
        return c
    if 0x20 <= cp < 0x7F:
        if in_class or c in _SPECIAL:
            return "\\" + c
        return c
    if cp < 0x100:
        return f"\\x{cp:02x}"
    if cp < 0x10000:
        return f"\\u{cp:04x}"
    return f"\\U{cp:08x}"


def _ranges(items) -> str:
    return "".join(_char(lo, True) if lo == hi else f"{_char(lo, True)}-{_char(hi, True)}"
                   for lo, hi in items)


class _Frame:
    """One open group: its flags now, and the scoped flag groups opened inside it, which
    close at each `|` and `)` and reopen after each `|`."""

    def __init__(self, multiline: bool, fold: bool = False):
        self.multiline = multiline
        self.fold = fold
        self.wraps: list[str] = []


class _Translator:
    def __init__(self, pattern: str):
        self.p = pattern
        self.i = 0
        self.out: list[str] = []
        self.stack = [_Frame(multiline=False)]

    def run(self) -> str:
        p = self.p
        while self.i < len(p):
            c = p[self.i]
            if c == "\\":
                self.out.append(self._escape_outside())
            elif c == "[":
                self.out.append(self._class())
            elif c == "(":
                self._open()
            elif c == ")":
                self._close()
            elif c == "|":
                frame = self.stack[-1]
                self.out.append(")" * len(frame.wraps) + "|")
                self.out.extend(f"(?{flags}:" for flags in frame.wraps)
                self.i += 1
            elif c == "$":
                self.out.append("$" if self.stack[-1].multiline else r"\Z")
                self.i += 1
            elif c in "^.*+?":
                self.out.append(c)
                self.i += 1
            elif c == "{":
                m = _REPEAT.match(p, self.i)
                if m:                               # {n}, {n,} or {n,m}; {,n} is literal
                    self.out.append(m.group(0))
                    self.i = m.end()
                else:
                    self.out.append(r"\{")
                    self.i += 1
            else:
                self.out.append(self._literal(ord(c)))
                self.i += 1
        if len(self.stack) != 1:
            raise Unsupported("an unclosed group")
        self.out.append(")" * len(self.stack[0].wraps))
        return "".join(self.out)

    def _literal(self, cp: int) -> str:
        if not self.stack[-1].fold:
            return _char(cp, False)
        items = _fold(((cp, cp),))
        return _char(cp, False) if items == ((cp, cp),) else f"[{_ranges(items)}]"

    # groups and flags

    def _open(self) -> None:
        p, frame = self.p, self.stack[-1]
        m = _FLAGS.match(p, self.i)
        if m:
            on, off, end = m.group(1), m.group(2) or "", m.group(3)
            if "U" in on + off:
                raise Unsupported("the U flag")
            if not on and not off:
                if end == ":":                      # (?:  a plain group
                    self.out.append("(?:")
                    self.stack.append(_Frame(frame.multiline, frame.fold))
                    self.i = m.end()
                    return
                raise Unsupported("an empty flag group")
            multiline = ("m" in on) or (frame.multiline and "m" not in off)
            fold = ("i" in on) or (frame.fold and "i" not in off)
            # The i flag is never written: _literal and _class write the folds out.
            on, off = on.replace("i", ""), off.replace("i", "")
            flags = on + ("-" + off if off else "")
            self.i = m.end()
            if end == ":":                          # (?flags:...)  a scoped group
                self.out.append(f"(?{flags}:")
                self.stack.append(_Frame(multiline, fold))
                return
            # (?flags): holds to the end of the enclosing group.
            if not flags:
                pass                                # only i changed: nothing to write
            elif len(self.stack) == 1 and not self.out and not off:
                self.out.append(f"(?{on})")         # a global flag at the very start
            else:
                self.out.append(f"(?{flags}:")
                frame.wraps.append(flags)
            frame.multiline = multiline
            frame.fold = fold
            return
        m = _NAMED.match(p, self.i)
        if m:
            self.out.append(f"(?P<{m.group(1)}>")
            self.stack.append(_Frame(frame.multiline, frame.fold))
            self.i = m.end()
            return
        if p.startswith("(?", self.i):
            raise Unsupported(f"the group {p[self.i:self.i + 4]!r}")
        self.out.append("(")
        self.stack.append(_Frame(frame.multiline, frame.fold))
        self.i += 1

    def _close(self) -> None:
        if len(self.stack) == 1:
            raise Unsupported("an unopened )")
        frame = self.stack.pop()
        self.out.append(")" * len(frame.wraps) + ")")
        self.i += 1

    # escapes

    def _escape_outside(self) -> str:
        p, i = self.p, self.i
        if i + 1 >= len(p):
            raise Unsupported("a trailing backslash")
        c = p[i + 1]
        simple = {"A": r"\A", "z": r"\Z", "b": r"(?a:\b)", "B": r"(?a:\B)"}
        if c in simple:
            self.i += 2
            return simple[c]
        if c.lower() in _PERL:
            self.i += 2
            items = _PERL[c.lower()]
            if self.stack[-1].fold:
                items = _fold(items)
            return f"[^{_ranges(items)}]" if c.isupper() else f"[{_ranges(items)}]"
        cp = self._escaped_char()
        return self._literal(cp)

    def _escaped_char(self) -> int:
        """A single-character escape at self.i, as Go's parser reads it; advances."""
        p, i = self.p, self.i
        if i + 1 >= len(p):
            raise Unsupported("a trailing backslash")
        c = p[i + 1]
        if c in _CONTROL:
            self.i += 2
            return _CONTROL[c]
        if c == "x":
            if p.startswith("{", i + 2):
                end = p.find("}", i + 3)
                digits = p[i + 3:end] if end > 0 else ""
                if not digits or not all(d in "0123456789abcdefABCDEF" for d in digits):
                    raise Unsupported("a bad \\x{...} escape")
                self.i = end + 1
                cp = int(digits, 16)
            else:
                digits = p[i + 2:i + 4]
                if len(digits) != 2 or not all(d in "0123456789abcdefABCDEF" for d in digits):
                    raise Unsupported("a bad \\x escape")
                self.i = i + 4
                cp = int(digits, 16)
            if cp > 0x10FFFF:
                raise Unsupported("a \\x escape past U+10FFFF")
            return cp
        if c in _OCTAL:
            # Go: \0 takes up to two more octal digits; \1-\7 must be followed by one,
            # since a lone digit would be a back-reference, which Go refuses.
            j = i + 2
            if c != "0" and (j >= len(p) or p[j] not in _OCTAL):
                raise Unsupported("a back-reference")
            value, j = int(c), i + 2
            while j < len(p) and j < i + 4 and p[j] in _OCTAL:
                value = value * 8 + int(p[j])
                j += 1
            self.i = j
            return value
        if c in "pP":
            raise Unsupported("a Unicode class")
        if c == "Q":
            raise Unsupported("\\Q...\\E")
        if c == "C":
            raise Unsupported("\\C")
        if c.isascii() and not c.isalnum():
            self.i += 2
            return ord(c)
        raise Unsupported(f"the escape \\{c}")

    # classes

    def _class(self) -> str:
        p = self.p
        self.i += 1
        negated = p.startswith("^", self.i)
        if negated:
            self.i += 1
        positive: list[tuple[int, int]] = []
        negatives: list[tuple[tuple[int, int], ...]] = []
        first = True
        while True:
            if self.i >= len(p):
                raise Unsupported("an unclosed class")
            if p[self.i] == "]" and not first:
                self.i += 1
                break
            first = False
            end = p.find(":]", self.i + 2) if p.startswith("[:", self.i) else -1
            if end >= 0:
                # As in Go: a [: with a :] after it must name a class; without one, the
                # [ is a literal.
                name = p[self.i + 2:end]
                neg = name.startswith("^")
                items = _POSIX.get(name.lstrip("^"))
                if items is None:
                    raise Unsupported("an unknown POSIX class")
                (negatives.append(items) if neg else positive.extend(items))
                self.i = end + 2
                continue
            if p[self.i] == "\\" and self.i + 1 < len(p) and p[self.i + 1].lower() in _PERL:
                c = p[self.i + 1]
                items = _PERL[c.lower()]
                (negatives.append(items) if c.isupper() else positive.extend(items))
                self.i += 2
                continue
            lo = self._class_char()
            hi = lo
            if p.startswith("-", self.i) and self.i + 1 < len(p) and p[self.i + 1] != "]":
                self.i += 1
                hi = self._class_char()
                if hi < lo:
                    raise Unsupported("a reversed range in a class")
            positive.append((lo, hi))
        if self.stack[-1].fold:
            positive = list(_fold(positive))
            negatives = [_fold(n) for n in negatives]
        if not negatives:
            if not positive:
                raise Unsupported("an empty class")
            return f"[{'^' if negated else ''}{_ranges(positive)}]"
        if not negated:
            # the union: one character from the positive items or outside a negated one
            parts = ([f"[{_ranges(positive)}]"] if positive else [])
            parts += [f"[^{_ranges(n)}]" for n in negatives]
            return parts[0] if len(parts) == 1 else "(?:" + "|".join(parts) + ")"
        # the complement of that union: no positive item, and inside every negated one
        head = f"(?![{_ranges(positive)}])" if positive else ""
        looks = "".join(f"(?=[{_ranges(n)}])" for n in negatives[:-1])
        return f"(?:{head}{looks}[{_ranges(negatives[-1])}])"

    def _class_char(self) -> int:
        p = self.p
        if p[self.i] == "\\":
            if self.i + 1 < len(p) and p[self.i + 1] in "bBAz":
                raise Unsupported(f"\\{p[self.i + 1]} inside a class")
            return self._escaped_char()
        cp = ord(p[self.i])
        self.i += 1
        return cp


def translate(pattern: str) -> str:
    """The Python `re` pattern that matches as Go's regexp matches `pattern`.

    Raises Unsupported when it cannot, including when Python will not compile the result
    or warns about it.
    """
    result = _Translator(pattern).run()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            re.compile(result)
    except (re.error, Warning) as exc:
        raise Unsupported(f"Python's re refuses the translation: {exc}") from None
    return result


# --- reading the rule files ---------------------------------------------------------------

class _Dropped(Exception):
    """This rule is left out of its set, for the reason given."""


def _load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        raise GenerationError(f"{path.name}: missing") from None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GenerationError(f"{path.name}: not valid TOML: {exc}") from None


def _pattern(source: str, where: str) -> dict:
    """A pattern verbatim for RE2 and translated for the fallback."""
    try:
        return {"pattern": source, "fallback": translate(source)}
    except Unsupported as exc:
        raise _Dropped(f"the fallback translator refuses {where}: {exc}") from None


def _strings(value, what: str, file: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise GenerationError(f"{file}: {what} is not a list of strings")
    return value


def _allowlist(table, file: str, where: str, *, rule: bool) -> dict:
    """One allowlist, its patterns translated. In a rule, an unimplemented field drops the
    rule; in a global allowlist it stops generation."""
    if not isinstance(table, dict):
        raise GenerationError(f"{file}: {where} is not a table")
    unknown = sorted(set(table) - ALLOWLIST_FIELDS)
    if unknown:
        message = (f"{where} uses {', '.join(unknown)}, which the scanner does not "
                   "implement")
        if rule:
            raise _Dropped(message)
        raise GenerationError(f"{file}: {message}")
    condition = table.get("condition", "OR")
    if not isinstance(condition, str) or condition.upper() not in ("OR", "||", "AND", "&&"):
        raise GenerationError(f"{file}: {where} has an unknown condition")
    target = table.get("regexTarget", "secret")
    if target not in ("secret", "match", "line", ""):
        raise GenerationError(f"{file}: {where} has an unknown regexTarget")
    description = table.get("description", "")
    if not isinstance(description, str):
        raise GenerationError(f"{file}: {where}'s description is not a string")
    regexes = _strings(table.get("regexes", []), f"{where}'s regexes", file)
    paths = _strings(table.get("paths", []), f"{where}'s paths", file)
    stopwords = _strings(table.get("stopwords", []), f"{where}'s stopwords", file)
    if not (regexes or paths or stopwords):
        raise GenerationError(f"{file}: {where} checks nothing")
    try:
        return {
            "description": description,
            "condition": "AND" if condition.upper() in ("AND", "&&") else "OR",
            "regexTarget": target or "secret",
            "regexes": [_pattern(r, f"{where}'s regex {n}") for n, r in enumerate(regexes, 1)],
            "paths": [_pattern(r, f"{where}'s path {n}") for n, r in enumerate(paths, 1)],
            "stopwords": [s.lower() for s in stopwords],
        }
    except _Dropped as exc:
        if rule:
            raise
        raise GenerationError(f"{file}: {exc}") from None


def _rule(table: dict, file: str, source: str) -> dict:
    """One rule, resolved; raises _Dropped when the scanner cannot run it as gitleaks
    would."""
    rid = table["id"]
    unknown = sorted(set(table) - RULE_FIELDS)
    if unknown:
        raise _Dropped(f"uses {', '.join(unknown)}, which the scanner does not implement")
    allowlists = table.get("allowlists", [])
    if not isinstance(allowlists, list):
        raise GenerationError(f"{file}: rule {rid}'s allowlists is not a list of tables")
    for n, a in enumerate(allowlists, 1):
        if isinstance(a, dict) and set(a) - ALLOWLIST_FIELDS:
            _allowlist(a, file, f"allowlist {n}", rule=True)   # raises _Dropped
    regex, path = table.get("regex"), table.get("path")
    for name, value in (("regex", regex), ("path", path), ("description",
                                                           table.get("description", ""))):
        if value is not None and not isinstance(value, str):
            raise GenerationError(f"{file}: rule {rid}'s {name} is not a string")
    if not regex and not path:
        raise GenerationError(f"{file}: rule {rid} has neither a regex nor a path")
    entropy = table.get("entropy", 0)
    if isinstance(entropy, bool) or not isinstance(entropy, (int, float)) or entropy < 0:
        raise GenerationError(f"{file}: rule {rid}'s entropy is not a number of 0 or more")
    group = table.get("secretGroup", 0)
    if isinstance(group, bool) or not isinstance(group, int) or group < 0:
        raise GenerationError(f"{file}: rule {rid}'s secretGroup is not a whole number")
    keywords = _strings(table.get("keywords", []), f"rule {rid}'s keywords", file)
    tags = _strings(table.get("tags", []), f"rule {rid}'s tags", file)
    return {
        "id": rid,
        "description": table.get("description", ""),
        "source": source,
        "regex": _pattern(regex, "its regex") if regex else None,
        "secretGroup": group,
        "entropy": float(entropy),
        "keywords": [k.lower() for k in keywords],
        "path": _pattern(path, "its path") if path else None,
        "tags": tags,
        "allowlists": [_allowlist(a, file, f"rule {rid}'s allowlist {n}", rule=True)
                       for n, a in enumerate(allowlists, 1)],
    }


def _top(data: dict, file: str) -> None:
    unknown = sorted(set(data) - TOP_FIELDS)
    if unknown:
        raise GenerationError(f"{file}: unknown top-level field {', '.join(unknown)}")
    if "allowlist" in data and "allowlists" in data:
        raise GenerationError(f"{file}: [allowlist] and [[allowlists]] cannot both be used")


def _rule_tables(data: dict, file: str) -> list[dict]:
    rules = data.get("rules", [])
    if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
        raise GenerationError(f"{file}: rules is not a list of tables")
    seen = set()
    for n, r in enumerate(rules, 1):
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            raise GenerationError(f"{file}: rule {n} has no id")
        if rid in seen:
            raise GenerationError(f"{file}: rule id {rid} is used twice")
        seen.add(rid)
    return rules


def _global_allowlists(data: dict, file: str) -> list[dict]:
    tables = []
    if "allowlist" in data:
        tables.append(data["allowlist"])
    extra = data.get("allowlists", [])
    if not isinstance(extra, list):
        raise GenerationError(f"{file}: allowlists is not a list of tables")
    tables += extra
    return [_allowlist(t, file, f"global allowlist {n}", rule=False)
            for n, t in enumerate(tables, 1)]


def _drops(path: Path) -> dict[str, str]:
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise GenerationError(f"{path.name}: missing") from None
    drops: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        rid, tab, reason = line.partition("\t")
        if not tab or not rid.strip() or not reason.strip():
            raise GenerationError(f"{path.name}: line {n} is not a rule id, a tab and a reason")
        if rid.strip() in drops:
            raise GenerationError(f"{path.name}: line {n} names {rid.strip()} again")
        drops[rid.strip()] = reason.strip()
    return drops


def _pin(rules: Path, upstream: Path) -> dict:
    data = _load_toml(rules / PIN)
    if set(data) != PIN_FIELDS:
        raise GenerationError(f"PIN: holds {sorted(data)}, not {sorted(PIN_FIELDS)}")
    digest = hashlib.sha256(upstream.read_bytes()).hexdigest()
    if data["sha256"] != digest:
        raise GenerationError(f"PIN: sha256 {data['sha256']} is not {upstream.name}'s "
                              f"bytes ({digest})")
    fetched = data["fetched"]
    return {"tag": str(data["tag"]), "commit": str(data["commit"]), "sha256": digest,
            "fetched": fetched.isoformat() if hasattr(fetched, "isoformat") else str(fetched)}


def build(rules: Path = RULES) -> dict:
    """The resolved sets as the structure `sets.json` holds."""
    local = _load_toml(rules / CREDENTIALS)
    _top(local, CREDENTIALS)
    extend = local.get("extend")
    if not isinstance(extend, dict) or not isinstance(extend.get("path"), str):
        raise GenerationError(f"{CREDENTIALS}: [extend] path is missing")
    unknown = sorted(set(extend) - EXTEND_FIELDS)
    if unknown:
        raise GenerationError(f"{CREDENTIALS}: unknown [extend] field {', '.join(unknown)}")
    upstream_path = rules / extend["path"]
    upstream_file = extend["path"].rsplit("/", 1)[-1]
    upstream = _load_toml(upstream_path)
    _top(upstream, upstream_file)
    if "extend" in upstream:
        raise GenerationError(f"{upstream_file}: extends another file")
    pin = _pin(rules, upstream_path)

    broad = _load_toml(rules / BROAD)
    _top(broad, BROAD)
    for field in ("extend", "allowlist", "allowlists"):
        if field in broad:
            raise GenerationError(f"{BROAD}: {field} belongs in {CREDENTIALS}, which the "
                                  "binary reads")

    upstream_rules = _rule_tables(upstream, upstream_file)
    local_rules = _rule_tables(local, CREDENTIALS)
    broad_rules = _rule_tables(broad, BROAD)
    upstream_ids = {r["id"] for r in upstream_rules}
    for file, tables in ((CREDENTIALS, local_rules), (BROAD, broad_rules)):
        for r in tables:
            if r["id"] in upstream_ids:
                raise GenerationError(f"{file}: rule id {r['id']} is an upstream rule's")
    clash = {r["id"] for r in local_rules} & {r["id"] for r in broad_rules}
    if clash:
        raise GenerationError(f"{BROAD}: rule id {', '.join(sorted(clash))} is also in "
                              f"{CREDENTIALS}")
    disabled = _strings(extend.get("disabledRules", []), "disabledRules", CREDENTIALS)
    missing = sorted(set(disabled) - upstream_ids)
    if missing:
        raise GenerationError(f"{CREDENTIALS}: disabledRules names {', '.join(missing)}, "
                              "which upstream does not have")

    drops = _drops(rules / DROP)
    known = upstream_ids | {r["id"] for r in local_rules} | {r["id"] for r in broad_rules}
    stale = sorted(set(drops) - known)
    if stale:
        raise GenerationError(f"{DROP}: names {', '.join(stale)}, which no rule has")

    members = {
        "credentials": [(r, upstream_file, "gitleaks") for r in upstream_rules
                        if r["id"] not in disabled]
                       + [(r, CREDENTIALS, "local") for r in local_rules],
        "broad": [(r, upstream_file, "gitleaks") for r in upstream_rules
                  if r["id"] in disabled]
                 + [(r, BROAD, "local") for r in broad_rules],
    }
    titles = {"credentials": local.get("title", ""), "broad": broad.get("title", "")}
    sets = {}
    for name, entries in members.items():
        resolved, dropped = [], []
        for table, file, source in entries:
            try:
                rule = _rule(table, file, source)
                if table["id"] in drops:
                    raise _Dropped(drops[table["id"]])
                resolved.append(rule)
            except _Dropped as exc:
                dropped.append({"id": table["id"], "source": source, "reason": str(exc)})
        sets[name] = {"title": titles[name], "rules": resolved, "dropped": dropped}

    allowlists = (_global_allowlists(upstream, upstream_file)
                  + _global_allowlists(local, CREDENTIALS))
    return {"format": FORMAT, "upstream": pin, "allowlists": allowlists, "sets": sets}


def generate(rules: Path = RULES) -> bytes:
    """The bytes of `sets.json` for the rule files in `rules`: ASCII JSON, `\\n` endings."""
    text = json.dumps(build(rules), indent=1, ensure_ascii=True)
    return (text + "\n").encode("ascii")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m moxi_secrets._generate",
                                     description="Resolve the rule files into sets.json.")
    parser.add_argument("rules", nargs="?", type=Path, default=RULES,
                        help="the rules directory (default: the package's own)")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 when sets.json differs")
    args = parser.parse_args(argv)
    try:
        data = generate(args.rules)
    except GenerationError as exc:
        print(f"generate: {exc}", file=sys.stderr)
        return 2
    target = args.rules / OUTPUT
    if args.check:
        current = target.read_bytes() if target.exists() else b""
        if current != data:
            print(f"generate: {target} differs from a fresh generation", file=sys.stderr)
            return 1
        return 0
    files.atomic_write(target, data, mode=0o644)
    sets = json.loads(data)["sets"]
    for name, body in sets.items():
        print(f"{name}: {len(body['rules'])} rules, {len(body['dropped'])} dropped")
        for d in body["dropped"]:
            print(f"  dropped {d['id']}: {d['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
