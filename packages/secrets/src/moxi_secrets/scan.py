"""The scan: the named sets, hits, redaction and line batches.

Every scan reads past the see-through characters (`seen_through`) and the backslashes
that escape a quote: it scans the text as given, and, when it holds any, a copy without
them whose hits it maps back.

`sets.json` is read once per process. A gitleaks-format rule is compiled the first time
its keyword appears. On RE2 the credentials and broad sets run here; without RE2 they run
in the fallback worker child (`moxi_secrets.fallback`), which calls `run_gitleaks` below
with Python's re. Private names (`names`) and personal data (`personal`) always run here.

Standard library only at import.
"""

from __future__ import annotations

import json
import os
import re
from bisect import bisect_left, bisect_right

from . import engine as _engine

SETS = ("credentials", "broad", "private-names", "personal-data")
#: The sets in gitleaks' format, which sets.json holds.
GITLEAKS_SETS = ("credentials", "broad")
#: The rule ids secrets emits beyond those in sets.json.
OWN_RULES = ("private-name", "private-email", "payment-card", "iban", "us-ssn", "known",
             "unscanned")

UNSCANNED = "unscanned"
_UNSCANNED_TEXT = ("not scanned: the fallback engine ran out of time or its worker failed, "
                   "so the text counts as a hit")
_ALLOW_MARK = "gitleaks:allow"
_PLACEHOLDER = re.compile(r"\[REDACTED:([^\[\]]*)\]")
_RULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules")

#: Which `redact` this is. It rises with a release in which `redact`, called with its
#: default keywords, redacts in some text a span the release before returned whole,
#: because of how it reads text (a suppression it stops applying, characters it newly
#: reads past). A change of rule data alone (a rule, regex, keyword, allowlist or
#: stopword in sets.json or the private-names lists) leaves it as it is.
REDACT_VERSION = 3

#: Unicode 17.0's Default_Ignorable_Code_Point: the 27 ranges DerivedCoreProperties.txt
#: lists, unassigned code points included, as (first, last).
_DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4,
    0x17B5), (0x180B, 0x180D), (0x180E, 0x180E), (0x180F, 0x180F), (0x200B, 0x200F),
    (0x202A, 0x202E), (0x2060, 0x2064), (0x2065, 0x2065), (0x2066, 0x206F), (0x3164,
    0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0000), (0xE0001, 0xE0001),
    (0xE0002, 0xE001F), (0xE0020, 0xE007F), (0xE0080, 0xE00FF), (0xE0100, 0xE01EF),
    (0xE01F0, 0xE0FFF))
_IGNORABLE_FIRSTS = tuple(first for first, _ in _DEFAULT_IGNORABLE)
#: The see-through characters ASCII holds.
_ASCII_SEEN = "".join(chr(c) for c in (*range(0x20), 0x7F) if chr(c) not in "\t\n")
_ASCII_SEEN_RX = re.compile("[" + "".join("\\x%02x" % ord(c) for c in _ASCII_SEEN) + "]")
#: One backslash of a run that stands directly before a quote (`"` or `'`): the escape
#: a quote inside a string takes (JSON's `\"`, Python's `\'`), once per level of
#: nesting. It matches one character, so each removed character is one match.
_ESCAPE = r"\\(?=\\*[\"'])"


class SetUnavailable(Exception):
    """A set a caller asked for cannot run: its list will not load, or its extra is not
    installed. The message names a key and position or an install line, never an entry."""


# -- the see-through set --------------------------------------------------------------------

def seen_through(c: str) -> bool:
    """Whether the scan reads past the character `c`: a character a renderer may show as
    nothing. That is a C0 control other than tab and newline, DEL or a C1 control; a
    format character (category Cf, as the running Python's `unicodedata` reads it); or a
    code point Unicode names default-ignorable, assigned or not."""
    code = ord(c)
    if code < 0x20:
        return c not in "\t\n"
    if code < 0x7F:
        return False
    if code <= 0x9F:
        return True
    import unicodedata

    if unicodedata.category(c) == "Cf":
        return True
    k = bisect_right(_IGNORABLE_FIRSTS, code) - 1
    return k >= 0 and code <= _DEFAULT_IGNORABLE[k][1]


def _seen_in(text: str) -> str:
    """The distinct see-through characters `text` holds."""
    if text.isascii():
        if _ASCII_SEEN_RX.search(text) is None:
            return ""
        return "".join(c for c in _ASCII_SEEN if c in text)
    return "".join(sorted(c for c in set(text) if seen_through(c)))


# -- hits -----------------------------------------------------------------------------------

class Hit:
    """One hit: where it is and what found it, never the secret's text.

    `start` and `end` are `str` offsets of the secret in the text scanned; `line` is the
    1-based line of `start`. `fingerprint` is "sha256:" and the first 16 hex digits of
    SHA-256 over the secret encoded as UTF-8 with surrogateescape.
    """

    __slots__ = ("set", "rule", "description", "start", "end", "line", "fingerprint")

    def __init__(self, set, rule, description, start, end, line, fingerprint):  # noqa: A002 -- set is the stored field name
        for name, value in zip(self.__slots__,
                               (set, rule, description, start, end, line, fingerprint)):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError(f"Hit is frozen: cannot set {name}")

    def __delattr__(self, name):
        raise AttributeError(f"Hit is frozen: cannot delete {name}")

    def _key(self):
        return tuple(getattr(self, name) for name in self.__slots__)

    def __eq__(self, other):
        return self._key() == other._key() if type(other) is Hit else NotImplemented

    def __hash__(self):
        return hash(self._key())

    def __repr__(self):
        return (f"Hit(set={self.set!r}, rule={self.rule!r}, line={self.line}, "
                f"fingerprint={self.fingerprint!r})")


class LineHit:
    """A hit of `scan_lines`: the path and line number it maps to, and the hit, whose
    offsets are in that line's text."""

    __slots__ = ("path", "line", "hit")

    def __init__(self, path, line, hit):
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "line", line)
        object.__setattr__(self, "hit", hit)

    def __setattr__(self, name, value):
        raise AttributeError(f"LineHit is frozen: cannot set {name}")

    def __eq__(self, other):
        if type(other) is not LineHit:
            return NotImplemented
        return (self.path, self.line, self.hit) == (other.path, other.line, other.hit)

    def __hash__(self):
        return hash((self.path, self.line, self.hit))

    def __repr__(self):
        return f"LineHit(path={self.path!r}, line={self.line}, hit={self.hit!r})"


class SetInfo:
    """What `describe()` says of one set."""

    __slots__ = ("name", "rules", "source", "pin", "dropped", "available")

    def __init__(self, name, rules, source, pin, dropped, available):
        for field, value in zip(self.__slots__,
                                (name, rules, source, pin, dropped, available)):
            object.__setattr__(self, field, value)

    def __setattr__(self, name, value):
        raise AttributeError(f"SetInfo is frozen: cannot set {name}")

    def __repr__(self):
        return (f"SetInfo(name={self.name!r}, rules={len(self.rules)}, "
                f"dropped={len(self.dropped)}, available={self.available})")


def fingerprint(secret: str) -> str:
    # hashlib loads here, at the first fingerprint, to keep it off the import's cost.
    import hashlib

    try:
        raw = secret.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        raw = secret.encode("utf-8", "surrogatepass")
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:16]


# -- sets.json ------------------------------------------------------------------------------

class _Allowlist:
    __slots__ = ("condition", "target", "regexes", "paths", "stopwords", "_stops")

    def __init__(self, raw):
        self.condition = raw["condition"]
        self.target = raw["regexTarget"]
        self.regexes = raw["regexes"]
        self.paths = raw["paths"]
        self.stopwords = raw["stopwords"]
        self._stops = {}

    def path_allowed(self, path, eng) -> bool:
        if not path or not self.paths:
            return False
        key = eng.key
        return any(eng.compile(p[key]).search(path) for p in self.paths)

    def regex_allowed(self, target, eng) -> bool:
        if not target or not self.regexes:
            return False
        key = eng.key
        return any(eng.compile(p[key]).search(target) for p in self.regexes)

    def has_stopword(self, secret, eng) -> bool:
        if not secret or not self.stopwords:
            return False
        index = self._stops.get(eng.name)
        if index is None:
            index = self._stops[eng.name] = _Stopwords(self.stopwords, eng)
        return index.found_in(secret.lower())


class _Stopwords:
    """One allowlist's stopwords: an RE2 alternation, or on Python's re a set of words
    looked up by each length they come in (an re alternation of a thousand words tries
    each at every position)."""

    __slots__ = ("rx", "words", "lengths")

    def __init__(self, words, eng):
        self.rx = None
        if eng.name == "re2":
            self.rx = eng.compile("|".join(_engine.re2_literal(w) for w in words))
        self.words = frozenset(words)
        self.lengths = sorted({len(w) for w in words})

    def found_in(self, text) -> bool:
        if self.rx is not None:
            return self.rx.search(text) is not None
        words, n = self.words, len(text)
        for size in self.lengths:
            if size > n:
                break
            for i in range(n - size + 1):
                if text[i:i + size] in words:
                    return True
        return False


def _unlined(allowlists) -> tuple:
    """`allowlists` without those that read the finding's line."""
    return tuple(a for a in allowlists if a.target != "line")


class _Rule:
    __slots__ = ("set", "id", "description", "regex", "group", "entropy", "keywords",
                 "path", "allowlists", "unlined")

    def __init__(self, set_name, raw):
        self.set = set_name
        self.id = raw["id"]
        self.description = raw["description"]
        self.regex = raw["regex"]
        self.group = raw["secretGroup"]
        self.entropy = raw["entropy"]
        self.keywords = tuple(raw["keywords"])
        self.path = raw["path"]
        self.allowlists = tuple(_Allowlist(a) for a in raw["allowlists"])
        self.unlined = _unlined(self.allowlists)


class Sets:
    """sets.json, read."""

    def __init__(self, data: dict):
        self.data = data
        self.upstream = data["upstream"]
        self.allowlists = tuple(_Allowlist(a) for a in data["allowlists"])
        self.unlined = _unlined(self.allowlists)
        self.rules = {name: tuple(_Rule(name, r) for r in data["sets"][name]["rules"])
                      for name in GITLEAKS_SETS}
        self.dropped = {name: tuple(d["id"] for d in data["sets"][name]["dropped"])
                        for name in GITLEAKS_SETS}
        self.by_id = {(r.set, r.id): r for rules in self.rules.values() for r in rules}
        self.keywords = tuple(sorted({k for rules in self.rules.values() for r in rules
                                      for k in r.keywords}))
        self._keyword_set = None
        self._keyword_starts: dict = {}

    def keyword_start(self, set_names):
        """A pattern that matches, at its start, a keyword of a rule in the named
        gitleaks-format sets, and the longest such keyword's length; None when they have
        no keyword."""
        key = tuple(set_names)
        if key not in self._keyword_starts:
            words = sorted({k for name in key for r in self.rules[name] for k in r.keywords},
                           key=len, reverse=True)
            self._keyword_starts[key] = (
                (re.compile("|".join(re.escape(k) for k in words)), len(words[0]))
                if words else None)
        return self._keyword_starts[key]

    def rule_ids(self) -> frozenset:
        return frozenset(r.id for rules in self.rules.values() for r in rules)

    def keywords_in(self, t, eng) -> frozenset:
        """The keywords that occur in the lowercased text."""
        lower = t.lower
        if eng.name == "re2" and len(lower) > 65536:
            if self._keyword_set is None:
                self._keyword_set = eng.set_of([_engine.re2_literal(k)
                                                for k in self.keywords]) or False
            if self._keyword_set:
                found = self._keyword_set.Match(lower.encode("utf-8", "replace")) or ()
                return frozenset(self.keywords[i] for i in found)
        return frozenset(k for k in self.keywords if k in lower)


_sets: Sets | None = None


def sets_path() -> str:
    return os.path.join(_RULES_DIR, "sets.json")


def load_sets() -> Sets:
    """sets.json, read once per process."""
    global _sets
    if _sets is None:
        with open(sets_path(), "rb") as fh:
            _sets = Sets(json.loads(fh.read()))
    return _sets


def emittable() -> frozenset:
    """Every rule id a placeholder may name."""
    return load_sets().rule_ids() | frozenset(OWN_RULES)


# -- the gitleaks-format sets ---------------------------------------------------------------

def _entropy(secret: str) -> float:
    """Shannon entropy as gitleaks computes it: counts per character, over the length in
    UTF-8 bytes."""
    from math import log2

    counts: dict = {}
    for c in secret:
        counts[c] = counts.get(c, 0) + 1
    size = len(secret) if secret.isascii() else len(secret.encode("utf-8"))
    inv = 1.0 / size
    total = 0.0
    for count in counts.values():
        freq = count * inv
        total -= freq * log2(freq)
    return total


def path_skipped(path, eng, sets: Sets | None = None) -> bool:
    """True when a global allowlist's path allowance matches `path`, so the
    gitleaks-format sets skip the text before any rule runs."""
    if not path:
        return False
    return _path_allowance(load_sets().allowlists if sets is None else sets.allowlists,
                           eng.path_target(path), eng)


def _path_allowance(allowlists, path, eng) -> bool:
    # gitleaks' checkCommitOrPathAllowed: an AND allowlist that also has regexes or
    # stopwords is checked per finding instead.
    for a in allowlists:
        if a.condition == "AND" and (a.regexes or a.stopwords):
            continue
        if a.path_allowed(path, eng):
            return True
    return False


def _finding_allowed(allowlists, secret, match, line, path, eng) -> bool:
    for a in allowlists:
        target = secret if a.target == "secret" else match if a.target == "match" else line()
        if a.condition == "AND":
            if a.paths and not a.path_allowed(path, eng):
                continue
            if a.regexes and not a.regex_allowed(target, eng):
                continue
            if a.stopwords and not a.has_stopword(secret, eng):
                continue
            return True
        if a.regex_allowed(target, eng) or a.has_stopword(secret, eng):
            return True
    return False


def trimmed(target, start: int, end: int) -> tuple[int, int]:
    """The span `start:end` of `target` (bytes or str) with its leading and trailing
    newlines left out, as gitleaks trims a match."""
    newline = 10 if isinstance(target, bytes) else "\n"
    while start < end and target[start] == newline:
        start += 1
    while end > start and target[end - 1] == newline:
        end -= 1
    return start, end


def secret_span(rx, target, start: int, end: int, group: int):
    """The secret gitleaks takes from the trimmed match `target[start:end]`, as a span of
    `target`, or None where gitleaks drops the match.

    gitleaks runs the rule's regex again on the trimmed match alone, as a string of its
    own, so `^`, `$` and `\\b` meet its ends and not the text around it. If that finds a
    match and the pattern has a group, the secret is the group `group` names (the match
    is dropped if there is no such group; a group with no span is the empty secret), else
    the first non-empty group, else the trimmed match. If the second run finds nothing,
    the secret is the trimmed match.
    """
    groups = rx.groups
    if not groups:
        return start, end
    m = rx.search(target[start:end])
    if m is None:
        return start, end
    if group > 0:
        if group > groups:
            return None
        gs, ge = m.span(group)
        return (start, start) if gs < 0 else (start + gs, start + ge)
    for g in range(1, groups + 1):
        gs, ge = m.span(g)
        if gs >= 0 and ge > gs:
            return start + gs, start + ge
    return start, end


def _utf8_size(c: str) -> int:
    code = ord(c)
    return 1 if code < 0x80 else 2 if code < 0x800 else 3 if code < 0x10000 else 4


def gitleaks_line(t, start: int, s: int, e: int) -> tuple[int, int, int]:
    """The line gitleaks reads for a match (its finding's Line, which `gitleaks:allow`
    and a "line" allowlist read), as (lo, hi, cut) in `str` offsets of `t.etext`.

    `start` is the match's start and `s:e` the match with its newlines trimmed. gitleaks
    keeps the untrimmed start and ends the match at the start plus the trimmed match's
    length in bytes, so a match with leading newlines ends that many bytes early, perhaps
    inside a character. It then bounds the line by the newlines (location.go):
    - from the last newline at or before the start, that newline included (from 0 on the
      first line), to the first newline at or after the end, or to the end itself where
      that is further;
    - where the start is past the last newline, from the start of the text to the first
      newline or carriage return at or after the end;
    - with no newline at all, the whole text.
    The line is `etext[lo:hi]`, except that when `cut` is above zero it ends inside the
    character at `hi - 1`, after its first `cut` bytes (see `line_text`).
    """
    etext = t.etext
    end, cut, back = e, 0, s - start
    while back > 0:
        size = _utf8_size(etext[end - 1])
        if back < size:
            cut = size - back
            break
        back -= size
        end -= 1
    marks = t.newlines or [len(etext)]
    k = bisect_right(marks, start)
    if k < len(marks):
        lo, hi = (marks[k - 1] if k else 0), marks[k]
        j = bisect_left(marks, end)
        if j < len(marks):
            hi = marks[j]
        if end > hi:
            return lo, end, cut
        return lo, hi, 0
    hi, size = end, len(etext)
    while hi < size and etext[hi] not in "\n\r":
        hi += 1
    return 0, hi, 0


def line_text(t, bounds) -> str:
    """The text of a `gitleaks_line` span. A line that ends inside a character keeps a
    U+FFFD for each of its bytes, as Go's regexp reads each byte of a cut character."""
    lo, hi, cut = bounds
    if not cut:
        return t.etext[lo:hi]
    return t.etext[lo:hi - 1] + "\ufffd" * cut


def run_gitleaks(t, set_names, path, eng, *, keep_marked=True) -> dict:
    """The gitleaks-format sets' hits on `t`, as gitleaks' detector finds them.

    Returns {set: [(rule id, start, end), ...]} in `str` offsets; a path-only hit spans
    the whole text. No decoding passes run. With `keep_marked` false, a line that holds
    `gitleaks:allow` and an allowlist that reads the finding's line keep no hit out.
    """
    sets = load_sets()
    out = {name: [] for name in set_names}
    # Every path pattern searches the path as the engine sees it, mapped as the text is.
    path = eng.path_target(path) if path else None
    if path and _path_allowance(sets.allowlists, path, eng):
        return out
    keywords = sets.keywords_in(t, eng)
    target = eng.target(t)
    conv = eng.to_str
    etext = t.etext
    allowed_lines: dict = {}
    global_lists = sets.allowlists if keep_marked else sets.unlined
    for name in set_names:
        found = out[name]
        for rule in sets.rules[name]:
            if rule.regex is None:
                # A path-only rule reads no keyword, entropy or marker.
                if (path and rule.path is not None
                        and eng.compile(rule.path[eng.key]).search(path)
                        and not _path_allowance(rule.allowlists, path, eng)):
                    found.append((rule.id, 0, len(etext)))
                continue
            if rule.keywords and not any(k in keywords for k in rule.keywords):
                continue
            if path and _path_allowance(rule.allowlists, path, eng):
                continue
            if rule.path is not None and not (
                    path and eng.compile(rule.path[eng.key]).search(path)):
                continue
            rx = eng.compile(rule.regex[eng.key])
            rule_lists = rule.allowlists if keep_marked else rule.unlined
            for m in rx.finditer(target):
                bs, be = trimmed(target, *m.span())
                if bs == be:
                    continue
                s, e = conv(t, bs), conv(t, be)
                bounds = gitleaks_line(t, conv(t, m.start()), s, e)
                if keep_marked:
                    marked = allowed_lines.get(bounds)
                    if marked is None:
                        lo, hi, cut = bounds
                        marked = allowed_lines[bounds] = (
                            etext.find(_ALLOW_MARK, lo, hi - 1 if cut else hi) >= 0)
                    if marked:
                        continue
                span = secret_span(rx, target, bs, be, rule.group)
                # An empty secret is no hit: a hit is a span to redact.
                if span is None or span[1] <= span[0]:
                    continue
                ss, se = conv(t, span[0]), conv(t, span[1])
                secret = etext[ss:se]
                if rule.entropy and _entropy(secret) <= rule.entropy:
                    continue
                match = etext[s:e]

                def line(bounds=bounds):
                    return line_text(t, bounds)

                if _finding_allowed(global_lists, secret, match, line, path, eng):
                    continue
                if _finding_allowed(rule_lists, secret, match, line, path, eng):
                    continue
                found.append((rule.id, ss, se))
    return out


# -- the scan -------------------------------------------------------------------------------

def _set_names(sets) -> tuple:
    if isinstance(sets, str):
        raise ValueError(f"sets is one name, {sets!r}; pass an iterable of names, such "
                         f"as ({sets!r},)")
    names = []
    for name in sets:
        if name not in SETS:
            raise ValueError(f"no set named {name!r}; the sets are {', '.join(SETS)}")
        if name not in names:
            names.append(name)
    return tuple(names)


def _known_spans(text, known) -> list:
    out = []
    for value in known:
        if not isinstance(value, str):
            raise TypeError("each known value is a str")
        if not value:
            continue
        i = text.find(value)
        while i >= 0:
            out.append(("known", "known", "a value the caller holds", i, i + len(value)))
            i = text.find(value, i + 1)
    return out


def _gitleaks_spans(t, names, path, *, keep_marked) -> list:
    sets = load_sets()
    eng = _engine.active()
    out = []
    if path and path_skipped(path, eng if eng.name == "re2" else _engine.python(), sets):
        return out
    if eng.name == "re2":
        results = run_gitleaks(t, names, path, eng, keep_marked=keep_marked)
    else:
        from . import fallback

        results = fallback.scan(t, names, path, keep_marked=keep_marked)
    whole = len(t.text)
    for name in names:
        found = results.get(name)
        if found is None:
            out.append((name, UNSCANNED, _UNSCANNED_TEXT, 0, whole))
            continue
        for rule_id, start, end in found:
            rule = sets.by_id.get((name, rule_id))
            description = rule.description if rule is not None else rule_id
            out.append((name, rule_id, description, start, end))
    return out


def placeholder(rule: str) -> str:
    return f"[REDACTED:{rule}]"


def placeholder_regions(text: str) -> list:
    """The runs of placeholder text in `text`, adjacent placeholders joined."""
    if "[REDACTED:" not in text:
        return []
    ids = emittable()
    regions: list = []
    for m in _PLACEHOLDER.finditer(text):
        if m.group(1) not in ids:
            continue
        s, e = m.span()
        if regions and regions[-1][1] == s:
            regions[-1][1] = e
        else:
            regions.append([s, e])
    return regions


def _opaque(start, end, regions, starts) -> bool:
    if start >= end:
        return False
    k = bisect_right(starts, start) - 1
    return k >= 0 and end <= regions[k][1]


def scan(text: str, sets, *, path: str | None = None, allow=None, known=()) -> list:
    """The hits of the named sets in `text`, sorted by start.

    `path`, repo-relative with `/`, turns on the path parts of the rules. `allow` keeps
    hits someone has judged. `known` values are found first, as set "known". A line
    that holds `gitleaks:allow`, or text a rule's line allowlist names, keeps that
    line's gitleaks-set hits out.

    The scan reads past see-through characters (`seen_through`): it also finds what the
    text holds with them removed, so a token a zero-width space or a soft hyphen splits
    is a hit, spanning the characters that split it. It reads past a run of backslashes
    directly before a quote the same way, so a key followed by an escaped quote (`\\"`,
    as JSON writes a quote inside a string) is a hit, the backslashes left outside it.
    """
    names = _set_names(sets)
    if not isinstance(text, str):
        raise TypeError("text is a str")
    if path is not None and not isinstance(path, str):
        path = str(path)
    return _seen_hits(text, names, path, allow, tuple(known), keep_marked=True)


#: The see-through characters a rule may read as whitespace, as `str.isspace` does: VT,
#: FF, a carriage return no newline follows, U+001C to U+001F and NEL.
_SPACED_RX = re.compile("[\x0b\x0c\x1c-\x1f\x85]|\r(?!\n)")


def _spaced_copy(text, rx, names) -> str | None:
    """The third copy `_seen_hits` scans, as long as `text`, or None when it would equal
    the copy without the characters `rx` matches (the see-through characters, and the
    backslashes that escape a quote when `text` holds any).

    Each character `_SPACED_RX` matches becomes a space. A run of see-through characters
    that holds none of those, and that stands directly before a keyword of a rule in the
    named gitleaks-format sets, read in the copy without them at the run's place and
    lowered as the keyword check lowers, has its first character turned into a space,
    unless a keyword of that copy runs across the place (starts before it and ends past
    it): a run that splits a keyword, such as `sk_` and `live_` in a Stripe key's
    `sk_live`, stays removed. The copy's scan removes the rest, so a word before such a
    run stays apart from the token after it. A run that ends in a backslash stands
    before a quote, which starts no keyword, so it is never one of those.
    """
    spaced = _SPACED_RX.search(text) is not None
    found = load_sets().keyword_start([n for n in names if n in GITLEAKS_SETS])
    starts = []
    if found is not None:
        keyword, size = found
        copy = None

        def keyword_size(position):
            """The length of the longest keyword at `position` in the copy, or 0."""
            ahead = copy[position:position + size].lower()
            if not ahead.isascii():
                ahead = ahead.replace("i\u0307", "i")
            m = keyword.match(ahead)
            return len(m.group()) if m else 0

        removed = 0
        for m in re.finditer("(?:" + rx.pattern + ")+", text):
            run_start, run_end = m.span()
            position = run_start - removed
            removed += run_end - run_start
            if text[run_end - 1] == "\\":
                continue
            # One character past the run, so a carriage return at its end sees the
            # newline after it; that character is not see-through, so no match starts there.
            if spaced and _SPACED_RX.search(text, run_start, run_end + 1):
                continue
            if copy is None:
                copy = rx.sub("", text)
            if keyword_size(position) and not any(
                    keyword_size(before) > position - before
                    for before in range(max(0, position - size + 1), position)):
                starts.append(run_start)
    if not spaced and not starts:
        return None
    out = list(_SPACED_RX.sub(" ", text) if spaced else text)
    for i in starts:
        out[i] = " "
    return "".join(out)


def _seen_hits(text, names, path, allow, known, *, keep_marked) -> list:
    """The hits of `text` and of a copy of it without its see-through characters and
    without each run of backslashes directly before a quote (`_ESCAPE`), the copy's
    mapped back to `text`, sorted by start. One copy removes both kinds; there is none
    when `text` holds neither. When `text` holds a see-through character a rule may read
    as whitespace (`_SPACED_RX`), or a run of see-through characters just before a
    rule's keyword, a third copy (`_spaced_copy`) holds a space for each of those and
    none of the others, so a boundary one gave, or the boundary a word needs from the
    token after it, survives; it drops the escaping backslashes too, and its hits are
    mapped back the same way.

    A copy hit's start and end map to the characters of `text` they came from, except
    that one at the copy's start starts at 0 and one at its end ends at `len(text)`, so
    it takes in the see-through characters at that end. Its line and fingerprint are the
    copy's: the fingerprint is the secret without them. A copy hit is left out where a
    hit already taken of the same set and rule is the same finding: one with the same
    span, or one with the same fingerprint whose span holds the copy hit's span trimmed
    of see-through characters at both ends. So a text gets the hits it has without the
    copies when no see-through character splits or borders a secret, or sits in the text
    a rule matches around one (the keyword of an assignment); where one does, a copy may
    add a hit, such as a token whose end a following byte order mark broke. An escaped
    quote does the same: a key a rule needs a quote after is a hit when `\\"` follows it.
    The trim reads see-through characters only; a hit never starts or ends on a removed
    backslash, since a quote follows each.

    A set the text scan left unscanned (the fallback ran out of time or its worker
    failed) already has a hit over the whole text, so the copies don't scan it.
    """
    hits = _hits(text, names, path, allow, known, keep_marked=keep_marked)
    chars = _seen_in(text)
    escaped = '\\"' in text or "\\'" in text
    if not chars and not escaped:
        return hits
    unscanned = {h.set for h in hits if h.rule == UNSCANNED}
    if unscanned:
        names = tuple(n for n in names if n not in unscanned)
    parts = ["[" + "".join("\\U%08x" % ord(c) for c in chars) + "]"] if chars else []
    if escaped:
        parts.append(_ESCAPE)
    rx = re.compile("|".join(parts))
    bases = [text]
    if chars:
        spaced = _spaced_copy(text, rx, names)
        if spaced is not None:
            bases.append(spaced)
    spans = {(h.set, h.rule, h.start, h.end) for h in hits}
    by_secret: dict = {}
    for h in hits:
        by_secret.setdefault((h.set, h.rule, h.fingerprint), []).append((h.start, h.end))
    out = list(hits)
    for base in bases:
        taken = []
        for h in _copy_hits(base, rx, names, path, allow, known, keep_marked=keep_marked):
            inner_start, inner_end = h.start, h.end
            while inner_start < inner_end and seen_through(text[inner_start]):
                inner_start += 1
            while inner_end > inner_start and seen_through(text[inner_end - 1]):
                inner_end -= 1
            if (h.set, h.rule, h.start, h.end) in spans or any(
                    s <= inner_start and inner_end <= e
                    for s, e in by_secret.get((h.set, h.rule, h.fingerprint), ())):
                continue
            taken.append(h)
        for h in taken:
            spans.add((h.set, h.rule, h.start, h.end))
            by_secret.setdefault((h.set, h.rule, h.fingerprint), []).append(
                (h.start, h.end))
        out += taken
    out.sort(key=lambda h: h.start)
    return out


def _copy_hits(base, rx, names, path, allow, known, *, keep_marked) -> list:
    """The hits of `base` without the characters `rx` matches, mapped back to `base`:
    a hit at the copy's start starts at 0, and one at its end ends at `len(base)`. Each
    match of `rx` is one character."""
    copy = rx.sub("", base)
    if not copy:
        return []
    # Each removed character's offset in the copy, where it would sit: a copy offset `c`
    # maps to `c` plus the count of these at or before it.
    shifted = [m.start() - i for i, m in enumerate(rx.finditer(base))]
    size = len(copy)
    out = []
    for h in _hits(copy, names, path, allow, known, keep_marked=keep_marked):
        start = h.start + bisect_right(shifted, h.start) if h.start else 0
        end = h.end + bisect_right(shifted, h.end - 1) if h.end < size else len(base)
        out.append(Hit(h.set, h.rule, h.description, start, end, h.line, h.fingerprint))
    return out


def _hits(text, names, path, allow, known, *, keep_marked) -> list:
    """`scan`'s hits for checked arguments. With `keep_marked` false, neither the mark
    nor a line allowlist keeps a hit out."""
    t = _engine.Text(text)
    spans = _known_spans(text, known)
    gitleaks = [n for n in names if n in GITLEAKS_SETS]
    if gitleaks:
        spans += _gitleaks_spans(t, gitleaks, path, keep_marked=keep_marked)
    if "private-names" in names:
        from . import names as _names

        spans += _names.spans(t)
    if "personal-data" in names:
        from . import personal

        spans += personal.spans(t)
    regions = placeholder_regions(text)
    starts = [r[0] for r in regions]
    hits = []
    for set_name, rule, description, start, end in spans:
        if regions and _opaque(start, end, regions, starts):
            continue
        hit = Hit(set_name, rule, description, start, end, t.line(start),
                  fingerprint(text[start:end]))
        if allow is not None and allow.keeps(hit, text, path):
            continue
        hits.append(hit)
    hits.sort(key=lambda h: h.start)
    return hits


# -- redaction ------------------------------------------------------------------------------

def _replace(text: str, hits) -> str:
    """Each hit's span as its rule's placeholder; overlapping spans merged, labelled by
    the first."""
    spans = sorted(((h.start, h.end, i, h.rule) for i, h in enumerate(hits)),
                   key=lambda s: (s[0], -s[1], s[2]))
    merged: list = []
    for start, end, _, rule in spans:
        if merged and (start < merged[-1][1] or start == merged[-1][0]):
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, rule])
    out, at = [], 0
    for start, end, rule in merged:
        out.append(text[at:start])
        out.append(placeholder(rule))
        at = end
    out.append(text[at:])
    return "".join(out)


def _replace_lines(text: str, hits) -> str:
    """Each line holding a character of a hit as one placeholder, newlines kept."""
    lines = text.split("\n")
    starts, at = [], 0
    for line in lines:
        starts.append(at)
        at += len(line) + 1
    labels: dict = {}
    for h in sorted(hits, key=lambda h: h.start):
        first = bisect_right(starts, h.start) - 1
        last = bisect_right(starts, max(h.start, h.end - 1)) - 1
        for k in range(first, last + 1):
            labels.setdefault(k, h.rule)
    return "\n".join(placeholder(labels[k]) if k in labels else line
                     for k, line in enumerate(lines))


def redact(text: str, sets, *, path: str | None = None, allow=None, known=()) -> str:
    """`text` with every hit replaced by `[REDACTED:<rule>]`.

    Unlike `scan`, `redact` lets nothing on a secret's line keep it: a `gitleaks:allow`
    mark and a rule's line allowlist keep no hit out. Like `scan`, it reads past
    see-through characters, and replaces a token they split together with the characters
    inside it; every character outside a hit comes back as it was. No secret survives:
    the result scans empty with the same arguments, under `scan` and under `redact`'s own
    rules. Redacting the result again changes nothing. Up to three passes replace hits;
    then each line that still holds one becomes a placeholder; then, if a hit spans
    lines, the whole text.
    """
    names = _set_names(sets)
    if not isinstance(text, str):
        raise TypeError("text is a str")
    if path is not None and not isinstance(path, str):
        path = str(path)
    known = tuple(known)

    def hits_in(current):
        return _seen_hits(current, names, path, allow, known, keep_marked=False)

    current = text
    for _ in range(3):
        hits = hits_in(current)
        if not hits:
            return current
        current = _replace(current, hits)
    hits = hits_in(current)
    if not hits:
        return current
    current = _replace_lines(current, hits)
    hits = hits_in(current)
    if not hits:
        return current
    return placeholder(hits[0].rule)


# -- line batches ---------------------------------------------------------------------------

def scan_lines(lines, sets, *, allow=None) -> list:
    """Hits in `(path, line number, text)` triples: each path's lines joined and scanned
    once with that path, each hit mapped back to its path and line.

    A hit's offsets are in its line's text and stop at its end; a whole-text hit comes
    back at the first line of its path's batch.
    """
    names = _set_names(sets)
    batches: dict = {}
    for path, number, text in lines:
        batches.setdefault(path, []).append((number, text))
    out = []
    for path, batch in batches.items():
        texts = [text for _, text in batch]
        joined = "\n".join(texts)
        starts, at = [], 0
        for text in texts:
            starts.append(at)
            at += len(text) + 1
        for hit in scan(joined, names, path=path, allow=allow):
            whole = hit.start == 0 and hit.end == len(joined) and (
                hit.rule == UNSCANNED or _path_only(hit))
            k = 0 if whole else bisect_right(starts, hit.start) - 1
            base, size = starts[k], len(texts[k])
            start = hit.start - base
            end = size if whole else min(hit.end - base, size)
            out.append(LineHit(path, batch[k][0], Hit(
                hit.set, hit.rule, hit.description, start, end, 1, hit.fingerprint)))
    return out


def _path_only(hit) -> bool:
    if hit.set not in GITLEAKS_SETS:
        return False
    rule = load_sets().by_id.get((hit.set, hit.rule))
    return rule is not None and rule.regex is None


# -- describe -------------------------------------------------------------------------------

def describe() -> list:
    """Per set: its rule ids, source and pin, dropped ids and whether it can run now."""
    sets = load_sets()
    pin = dict(sets.upstream)
    out = []
    for name in GITLEAKS_SETS:
        rules = tuple(r.id for r in sets.rules[name])
        upstream = sum(1 for r in sets.data["sets"][name]["rules"]
                       if r["source"] == "gitleaks")
        source = (f"gitleaks {pin['tag']} ({upstream} rules) and "
                  f"{len(rules) - upstream} local rules")
        out.append(SetInfo(name, rules, source, pin, sets.dropped[name], available=True))
    from . import names as _names
    from . import personal

    out.append(SetInfo("private-names", ("private-name", "private-email"),
                       "your list in the secrets config", None, (), _names.available()))
    out.append(SetInfo("personal-data", ("payment-card", "iban", "us-ssn"),
                       "python-stdnum validators", None, (), personal.available()))
    return out
