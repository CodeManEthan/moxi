"""The scan: the named sets, hits, redaction and line batches.

`sets.json` is read once per process. A gitleaks-format rule is compiled the first time
its keyword appears. On RE2 the credentials and broad sets run here; without RE2 they run
in the fallback worker child (`moxi_secrets.fallback`), which calls `run_gitleaks` below
with Python's re. Private names (`names`) and personal data (`personal`) always run here.

Standard library only at import.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from bisect import bisect_right

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


class SetUnavailable(Exception):
    """A set a caller asked for cannot run: its list will not load, or its extra is not
    installed. The message names a key and position or an install line, never an entry."""


# -- hits -----------------------------------------------------------------------------------

class Hit:
    """One hit: where it is and what found it, never the secret's text.

    `start` and `end` are `str` offsets of the secret in the text scanned; `line` is the
    1-based line of `start`. `fingerprint` is "sha256:" and the first 16 hex digits of
    SHA-256 over the secret encoded as UTF-8 with surrogateescape.
    """

    __slots__ = ("set", "rule", "description", "start", "end", "line", "fingerprint")

    def __init__(self, set, rule, description, start, end, line, fingerprint):  # noqa: A002
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


class _Rule:
    __slots__ = ("set", "id", "description", "regex", "group", "entropy", "keywords",
                 "path", "allowlists")

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


class Sets:
    """sets.json, read."""

    def __init__(self, data: dict):
        self.data = data
        self.upstream = data["upstream"]
        self.allowlists = tuple(_Allowlist(a) for a in data["allowlists"])
        self.rules = {name: tuple(_Rule(name, r) for r in data["sets"][name]["rules"])
                      for name in GITLEAKS_SETS}
        self.dropped = {name: tuple(d["id"] for d in data["sets"][name]["dropped"])
                        for name in GITLEAKS_SETS}
        self.by_id = {(r.set, r.id): r for rules in self.rules.values() for r in rules}
        self.keywords = tuple(sorted({k for rules in self.rules.values() for r in rules
                                      for k in r.keywords}))
        self._keyword_set = None

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
        with open(sets_path(), "rb") as handle:
            _sets = Sets(json.loads(handle.read()))
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


def run_gitleaks(t, set_names, path, eng) -> dict:
    """The gitleaks-format sets' hits on `t`, as gitleaks' detector finds them.

    Returns {set: [(rule id, start, end), ...]} in `str` offsets; a path-only hit spans
    the whole text. No decoding passes run.
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
            groups = rx.groups
            for m in rx.finditer(target):
                bs, be = m.span()
                if bs == be:
                    continue
                s, e = conv(t, bs), conv(t, be)
                while s < e and etext[s] == "\n":
                    s += 1
                while e > s and etext[e - 1] == "\n":
                    e -= 1
                if s == e:
                    continue
                bounds = t.line_bounds(s, e)
                marked = allowed_lines.get(bounds)
                if marked is None:
                    marked = allowed_lines[bounds] = (
                        _ALLOW_MARK in etext[bounds[0]:bounds[1]])
                if marked:
                    continue
                ss, se = s, e
                if groups:
                    if rule.group > 0:
                        if rule.group > groups:
                            continue
                        gs, ge = m.span(rule.group)
                        if gs < 0:
                            continue
                        ss, se = conv(t, gs), conv(t, ge)
                    else:
                        for g in range(1, groups + 1):
                            gs, ge = m.span(g)
                            if gs >= 0 and ge > gs:
                                ss, se = conv(t, gs), conv(t, ge)
                                break
                if se <= ss:
                    continue
                secret = etext[ss:se]
                if rule.entropy and _entropy(secret) <= rule.entropy:
                    continue
                match = etext[s:e]

                def line(bounds=bounds):
                    return etext[bounds[0]:bounds[1]]

                if _finding_allowed(sets.allowlists, secret, match, line, path, eng):
                    continue
                if _finding_allowed(rule.allowlists, secret, match, line, path, eng):
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


def _gitleaks_spans(t, names, path) -> list:
    sets = load_sets()
    eng = _engine.active()
    out = []
    if path and path_skipped(path, eng if eng.name == "re2" else _engine.python(), sets):
        return out
    if eng.name == "re2":
        results = run_gitleaks(t, names, path, eng)
    else:
        from . import fallback

        results = fallback.scan(t, names, path)
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
    hits someone has judged. `known` values are found first, as set "known".
    """
    names = _set_names(sets)
    if not isinstance(text, str):
        raise TypeError("text is a str")
    if path is not None and not isinstance(path, str):
        path = str(path)
    known = tuple(known)
    t = _engine.Text(text)
    spans = _known_spans(text, known)
    gitleaks = [n for n in names if n in GITLEAKS_SETS]
    if gitleaks:
        spans += _gitleaks_spans(t, gitleaks, path)
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

    No secret survives: the result scans empty with the same arguments. Redacting the
    result again changes nothing. Up to three passes replace hits; then each line that
    still holds one becomes a placeholder; then, if a hit spans lines, the whole text.
    """
    names = _set_names(sets)
    known = tuple(known)
    current = text
    for _ in range(3):
        hits = scan(current, names, path=path, allow=allow, known=known)
        if not hits:
            return current
        current = _replace(current, hits)
    hits = scan(current, names, path=path, allow=allow, known=known)
    if not hits:
        return current
    current = _replace_lines(current, hits)
    hits = scan(current, names, path=path, allow=allow, known=known)
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
        out.append(SetInfo(name, rules, source, pin, sets.dropped[name], True))
    from . import names as _names
    from . import personal

    out.append(SetInfo("private-names", ("private-name", "private-email"),
                       "your list in the secrets config", None, (), _names.available()))
    out.append(SetInfo("personal-data", ("payment-card", "iban", "us-ssn"),
                       "python-stdnum validators", None, (), personal.available()))
    return out
