"""Private names: the user's list of what identifies them, compiled and matched.

The list lives in secrets' config file, read through `moxi.config` at each scan that asks
for it (so an edit is seen at the next scan) and compiled once per change of its values.
No exception or message here carries an entry's text: each names the key and position.

The rule: an entry matches a span when the span matches the entry's normalised form
character for character (a space matches a run of separators, `-` and `_` match either
one, every other character matches its fold set) and, for a `words` entry, no letter or
digit stands outside an end of the entry that is a letter or digit. Every such span is a match. Then a match inside an occurrence of an `allowed`
entry or of the author name or email is cut, and a match inside another remaining match
is cut.

Case is written out, never left to an engine: no pattern carries a case-insensitive flag.
Each character in `_folds.FOLDS` (the sets CPython's re treats as equal under IGNORECASE)
is written as the class of its set, the same class on RE2 and on re, so both engines match
an entry at the same places on every Python version. Any other character matches itself.

Standard library only at import; `moxi.config` is imported at first use.
"""

from __future__ import annotations

import re
from bisect import bisect_right

from . import engine as _engine
from ._folds import FOLDS
from .scan import SetUnavailable

#: Every character `str.isspace()` is true for (checked against Python's tables by a test).
WHITESPACE = ("\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0     "
              "           　")
SEPARATORS = WHITESPACE + "-_"

_RUN = re.compile("[" + re.escape(SEPARATORS) + "]+")
_TRIM = SEPARATORS
_HAS_SPACE = re.compile("[" + re.escape(WHITESPACE) + "]")
_PY_RUN = "[" + re.escape(SEPARATORS) + "]++"
_RE2_RUN = "[" + "".join("\\x{%x}" % ord(c) for c in SEPARATORS) + "]+"

KEYS = ("private_names.words", "private_names.anywhere", "private_names.emails",
        "private_names.allowed")


def normalise(entry: str) -> str:
    """Every run of whitespace, `-` and `_` that holds whitespace as one space, then
    those characters trimmed from both ends. Empty means the entry names nothing."""
    def one(m):
        run = m.group()
        return " " if _HAS_SPACE.search(run) else run
    return _RUN.sub(one, entry).strip(_TRIM)


def python_pattern(norm: str) -> str:
    out = []
    for c in norm:
        if c == " ":
            out.append(_PY_RUN)
        elif c in "-_":
            out.append("[-_]")
        elif c in FOLDS:
            out.append("[" + "".join(re.escape(f) for f in FOLDS[c]) + "]")
        else:
            out.append(re.escape(c))
    return "".join(out)


def re2_pattern(norm: str) -> str:
    out = []
    for c in norm:
        if c == " ":
            out.append(_RE2_RUN)
        elif c in "-_":
            out.append("[\\x{2d}\\x{5f}]")
        elif c in FOLDS:
            out.append("[" + "".join("\\x{%x}" % ord(f) for f in FOLDS[c]) + "]")
        else:
            out.append(_engine.re2_literal(c))
    return "".join(out)


class PrivateNames:
    """The list as the config holds it: words, anywhere, emails, allowed, author_name,
    author_email. Frozen."""

    __slots__ = ("words", "anywhere", "emails", "allowed", "author_name", "author_email")

    def __init__(self, words=(), anywhere=(), emails=(), allowed=(), author_name="",
                 author_email=""):
        for name, value in zip(self.__slots__, (tuple(words), tuple(anywhere),
                                                tuple(emails), tuple(allowed),
                                                author_name, author_email)):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError(f"PrivateNames is frozen: cannot set {name}")

    def __eq__(self, other):
        if type(other) is not PrivateNames:
            return NotImplemented
        return all(getattr(self, n) == getattr(other, n) for n in self.__slots__)

    def __hash__(self):
        return hash(tuple(getattr(self, n) for n in self.__slots__))

    def __repr__(self):
        # Counts only: the entries are what identifies the user.
        return (f"PrivateNames(words={len(self.words)}, anywhere={len(self.anywhere)}, "
                f"emails={len(self.emails)}, allowed={len(self.allowed)})")


# -- reading the list -----------------------------------------------------------------------

_schema = None


def schema():
    global _schema
    if _schema is None:
        from moxi.config import Schema

        _schema = Schema.from_resource(__package__, "settings.toml")
    return _schema


def private_names() -> PrivateNames:
    """The list from secrets' config file. Raises `SetUnavailable` when it will not load."""
    from moxi.config import ConfigError, load

    try:
        settings = load("secrets", schema())
    except ConfigError as exc:
        raise SetUnavailable(_refusal(exc)) from None
    values = {}
    for key in KEYS:
        items = settings[key]
        for i, item in enumerate(items):
            if not isinstance(item, str):
                raise SetUnavailable(f"the private names list will not load: {key} item "
                                     f"{i + 1} is not a string")
        values[key.rpartition(".")[2]] = tuple(items)
    for key in ("author.name", "author.email"):
        if not isinstance(settings[key], str):
            raise SetUnavailable(f"the private names list will not load: {key} is not a "
                                 "string")
    return PrivateNames(author_name=settings["author.name"],
                        author_email=settings["author.email"], **values)


def _refusal(exc) -> str:
    where = f"key {exc.key}: " if getattr(exc, "key", None) else ""
    problem = getattr(exc, "problem", None) or type(exc).__name__
    if not getattr(exc, "key", None):
        # A whole-file problem (TOML syntax and the like) may quote the file; keep only
        # its first clause, which names what is wrong.
        problem = problem.split("(")[0].strip()
    return f"the private names list will not load: {where}{problem}"


def available() -> bool:
    try:
        private_names()
    except SetUnavailable:
        return False
    return True


def empties(names: PrivateNames) -> list:
    """(key, 1-based position) of each entry that is empty once normalised."""
    out = []
    for key in KEYS:
        for i, entry in enumerate(getattr(names, key.rpartition(".")[2])):
            if not normalise(entry):
                out.append((key, i + 1))
    return out


# -- compiling ------------------------------------------------------------------------------

class _Entry:
    __slots__ = ("norm", "rule", "check_start", "check_end", "patterns")

    def __init__(self, norm, rule, words):
        self.norm = norm
        self.rule = rule
        self.check_start = words and norm[0].isalnum()
        self.check_end = words and norm[-1].isalnum()
        self.patterns = {}


class Compiled:
    """One list, compiled: entries to report and occurrences that cut."""

    def __init__(self, names: PrivateNames):
        self.entries = []
        seen = set()
        for kind, rule, words in (("words", "private-name", True),
                                  ("anywhere", "private-name", False),
                                  ("emails", "private-email", False)):
            for entry in getattr(names, kind):
                norm = normalise(entry)
                if norm and (norm, rule, words) not in seen:
                    seen.add((norm, rule, words))
                    self.entries.append(_Entry(norm, rule, words))
        cuts = list(names.allowed) + [names.author_name, names.author_email]
        self.cuts = []
        for entry in cuts:
            norm = normalise(entry) if isinstance(entry, str) else ""
            if norm and norm not in {c.norm for c in self.cuts}:
                self.cuts.append(_Entry(norm, None, False))
        self._sets = {}

    def pattern(self, entry, eng):
        found = entry.patterns.get(eng.name)
        if found is None:
            source = re2_pattern(entry.norm) if eng.name == "re2" else \
                python_pattern(entry.norm)
            found = entry.patterns[eng.name] = eng.compile(source)
        return found

    def present(self, t, eng) -> tuple:
        """(entries, cuts) that may occur in the text: on RE2, those one set pass names."""
        everything = self.entries + self.cuts
        if eng.name != "re2" or not everything:
            return self.entries, self.cuts
        found = self._sets.get(eng.name)
        if found is None:
            found = self._sets[eng.name] = eng.set_of(
                [re2_pattern(e.norm) for e in everything]) or False
        if not found:
            return self.entries, self.cuts
        hit = set(found.Match(t.data) or ())
        n = len(self.entries)
        return ([e for i, e in enumerate(self.entries) if i in hit],
                [c for i, c in enumerate(self.cuts) if i + n in hit])

    def occurrences(self, entry, t, eng):
        """Every span of `entry` in `t`, overlapping ones included, as `str` offsets."""
        rx = self.pattern(entry, eng)
        target = eng.target(t)
        etext = t.etext
        pos, size = 0, len(target)
        while pos <= size:
            m = rx.search(target, pos)
            if m is None:
                return
            bs, be = m.span()
            s, e = eng.to_str(t, bs), eng.to_str(t, be)
            if e > s and not (
                    (entry.check_start and s > 0 and etext[s - 1].isalnum())
                    or (entry.check_end and e < len(etext) and etext[e].isalnum())):
                yield s, e
            pos = eng.next_start(t, bs)


_compiled: tuple | None = None


def compiled(names: PrivateNames) -> Compiled:
    global _compiled
    if _compiled is None or _compiled[0] != names:
        _compiled = (names, Compiled(names))
    return _compiled[1]


def spans(t, names: PrivateNames | None = None, eng=None) -> list:
    """The private-names set's hits on `t`, as (set, rule, description, start, end).

    `names` defaults to the config's list and `eng` to the active engine.
    """
    if names is None:
        names = private_names()
    comp = compiled(names)
    if not comp.entries:
        return []
    if eng is None:
        eng = _engine.active()
    entries, cuts = comp.present(t, eng)
    if not entries:
        return []
    allowed = sorted(span for c in cuts for span in comp.occurrences(c, t, eng))
    a_starts = [s for s, _ in allowed]
    a_reach = []
    reach = -1
    for _, e in allowed:
        reach = max(reach, e)
        a_reach.append(reach)
    matches = []
    for entry in entries:
        for s, e in comp.occurrences(entry, t, eng):
            if allowed:
                k = bisect_right(a_starts, s) - 1
                if k >= 0 and a_reach[k] >= e:
                    continue
            matches.append((s, -e, entry.rule))
    matches.sort()
    out = []
    reach = -1
    for s, neg_e, rule in matches:
        e = -neg_e
        if e <= reach:
            continue
        reach = e
        description = "a private email" if rule == "private-email" else "a private name"
        out.append(("private-names", rule, description, s, e))
    return out
