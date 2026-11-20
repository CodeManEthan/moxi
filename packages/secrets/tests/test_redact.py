"""Redaction: no secret survives and redact is idempotent, as a seeded fuzz over every
set, plus the line step, the whole-text step, path-only hits, merging, `known` and the
caller's bytes.

The fuzz, whose texts hold allow marks, line-allowlist text and see-through characters,
checks each text three ways: the result scans empty with the same arguments, under
`scan` and under `redact`'s own rules, redacting the result changes nothing, and the
result equals a reference model of the documented mechanism (up to three passes of
replacement, overlapping spans merged and labelled by the first, then the line step,
then the whole text) that tracks which of the original characters survive. In the model
no character of any hit `redact` acts on survives, so where the two agree, none survives
in `redact`'s result either.
"""

from __future__ import annotations

import importlib
import inspect
import string
import sys
from bisect import bisect_right

import corpus
import pytest

import moxi_secrets
from moxi_secrets import REDACT_VERSION, SETS, Allowlist, fallback, redact, scan

scan_mod = importlib.import_module("moxi_secrets.scan")

P12 = "keys/a.p12"
GITLEAKS = ("credentials", "broad")

#: Texts per engine in the suite. The design's full run is 80,000.
FUZZ_TEXTS = 300


def ph(rule):
    return scan_mod.placeholder(rule)


def rule_words():
    return sorted({w for rid in scan_mod.load_sets().rule_ids() for w in rid.split("-")})


@pytest.fixture
def fuzz_names(names_config):
    names_config(words=["redacted", "pat", "github", *rule_words()])


@pytest.fixture
def count_scans(monkeypatch):
    """Counts the scans redact makes: count_scans() -> the count since the last call."""
    real = scan_mod._seen_hits
    seen = [0]

    def counting(*args, **kwargs):
        seen[0] += 1
        return real(*args, **kwargs)
    monkeypatch.setattr(scan_mod, "_seen_hits", counting)

    def take():
        n, seen[0] = seen[0], 0
        return n
    return take


# -- the reference model --------------------------------------------------------------------

def _merged(hits):
    spans = sorted(((h.start, h.end, i, h.rule) for i, h in enumerate(hits)),
                   key=lambda s: (s[0], -s[1], s[2]))
    merged = []
    for start, end, _, rule in spans:
        if merged and (start < merged[-1][1] or start == merged[-1][0]):
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, rule])
    return merged


def _replace(text, origin, hits):
    out, keep, at = [], [], 0
    for start, end, rule in _merged(hits):
        out.append(text[at:start])
        keep.extend(origin[at:start])
        out.append(ph(rule))
        keep.extend([None] * len(ph(rule)))
        at = end
    out.append(text[at:])
    keep.extend(origin[at:])
    return "".join(out), keep


def _replace_lines(text, origin, hits):
    lines = text.split("\n")
    starts, at = [], 0
    for line in lines:
        starts.append(at)
        at += len(line) + 1
    labels = {}
    for h in sorted(hits, key=lambda h: h.start):
        first = bisect_right(starts, h.start) - 1
        last = bisect_right(starts, max(h.start, h.end - 1)) - 1
        for k in range(first, last + 1):
            labels.setdefault(k, h.rule)
    out, keep = [], []
    for k, line in enumerate(lines):
        if k:
            out.append("\n")
            keep.append(origin[starts[k] - 1])
        if k in labels:
            out.append(ph(labels[k]))
            keep.extend([None] * len(ph(labels[k])))
        else:
            out.append(line)
            keep.extend(origin[starts[k]:starts[k] + len(line)])
    return "".join(out), keep


def redact_hits(text, sets, *, path=None, known=()):
    """The hits `redact` acts on: `scan`'s, with no mark or line allowlist keeping one."""
    return scan_mod._seen_hits(text, scan_mod._set_names(sets), path, None, tuple(known),
                               keep_marked=False)


def model(text, sets, **kw):
    """(result, origin of each result character or None, hits of the first scan, step)."""
    current, origin = text, list(range(len(text)))
    first = None
    for step in range(3):
        hits = redact_hits(current, sets, **kw)
        if first is None:
            first = hits
        if not hits:
            return current, origin, first, step
        current, origin = _replace(current, origin, hits)
    hits = redact_hits(current, sets, **kw)
    if not hits:
        return current, origin, first, 3
    current, origin = _replace_lines(current, origin, hits)
    hits = redact_hits(current, sets, **kw)
    if not hits:
        return current, origin, first, "line"
    out = ph(hits[0].rule)
    return out, [None] * len(out), first, "whole"


# -- the fuzz -------------------------------------------------------------------------------

SEPS = (" ", "", "\n", ": ", "'", "=", "\t", " \n", ",", "\r\n", "\u200b")
#: Text beside a secret that keeps it out of `scan` and never out of `redact`.
BESIDE = (" # gitleaks:allow", "RUN --mount=type=secret,id=a ", "import { a } from 'b'; ")


class Fuzz:
    def __init__(self, seed):
        self.r = corpus.rng(seed)
        self.ids = sorted(scan_mod.emittable())
        self.words = rule_words()
        self.secrets = []

    def secret(self):
        r = self.r
        kind = r.randrange(9)
        if kind < 3:
            make, _ = r.choice(corpus.CREDENTIALS)
            value = make(r)
        elif kind == 3:
            value = r.choice((corpus.password_assignment, corpus.bearer, corpus.curl_user,
                              corpus.url_credential))(r)
        elif kind == 4:
            value = corpus.grouped(corpus.card(r, 16, r.choice("456")),
                                   sep=r.choice(" -"))
        elif kind == 5:
            value = corpus.grouped(corpus.iban(r, "DE", 22))
        elif kind == 6:
            value = f"{r.randrange(1, 666):03d}-{r.randrange(1, 99):02d}-" \
                    f"{r.randrange(1, 9999):04d}"
        elif kind == 7:
            value = corpus.key_header()
        else:
            value = corpus.url_credential(r, "admin")
        self.secrets.append(value)
        return value

    def placeholder(self):
        r = self.r
        kind = r.randrange(4)
        if kind == 0:
            return ph(r.choice(self.ids))
        if kind == 1:
            return ph(r.choice(("a", "x", "REDACTED", "github pat", "")))
        if kind == 2:
            return ph(corpus.chars(r, r.randrange(8, 40), "0123456789abcdef"))
        return ph(self.secret())                       # a secret dressed as a placeholder

    def word(self):
        r = self.r
        return r.choice(("redacted", "pat", "github", "REDACTED", "Pat", r.choice(self.words),
                         corpus.chars(r, r.randrange(2, 9), string.ascii_lowercase)))

    def piece(self):
        r = self.r
        kind = r.randrange(11)
        if kind <= 1:
            return self.secret()
        if kind == 2:
            return self.placeholder()
        if kind == 3:                                  # a placeholder inside a secret
            s = self.secret()
            k = r.randrange(len(s) + 1)
            return s[:k] + self.placeholder() + s[k:]
        if kind == 4:                                  # glued on either side
            return self.placeholder() + self.secret() + self.placeholder() * r.randrange(2)
        if kind == 5:                                  # several curl -u on one line
            return " ".join(corpus.curl_user(r) for _ in range(r.randrange(2, 5)))
        if kind == 6:
            return "\udcff\udc80café\udcc3"         # escaped bytes and non-ASCII
        if kind == 7:                                  # a mark or a line allowlist's text
            return r.choice(BESIDE)
        if kind == 8:                                  # split by a see-through character
            s = self.secret()
            return corpus.split(s, r.choice(corpus.see_through()), r.randrange(len(s) + 1))
        return " ".join(self.word() for _ in range(r.randrange(1, 4)))

    def text(self):
        r = self.r
        n = r.choice((0, 1, 2, 3, 4, 6, 8))
        out = []
        for i in range(n):
            if i:
                out.append(r.choice(SEPS))
            out.append(self.piece())
        return "".join(out)

    def known(self, text):
        r = self.r
        kind = r.randrange(6)
        if kind == 0 and text:
            a = r.randrange(len(text))
            return [text[a:a + r.randrange(1, 14)]]
        if kind == 1 and self.secrets:
            return [r.choice(self.secrets), ""]
        if kind == 2:
            return [""]
        return []

    def sets(self):
        r = self.r
        if r.random() < 0.4:
            return SETS
        return tuple(s for s in SETS if r.random() < 0.5) or (r.choice(SETS),)

    def path(self):
        return self.r.choice((None, None, P12, "src/app.py"))


def test_redaction_fuzz(engine_name, fuzz_names, count_scans):
    fuzz = Fuzz(80 if engine_name == "re2" else 81)
    steps = {}
    texts = [""] + [fuzz.text() for _ in range(FUZZ_TEXTS - 1)]
    for t in texts:
        sets, path = fuzz.sets(), fuzz.path()
        kw = {"path": path, "known": fuzz.known(t)}
        count_scans()
        out = redact(t, sets, **kw)
        assert count_scans() <= 5
        assert scan(out, sets, **kw) == [], (t, kw)
        assert redact_hits(out, sets, **kw) == [], (t, kw)
        assert redact(out, sets, **kw) == out, (t, kw)
        expected, origin, hits, step = model(t, sets, **kw)
        assert out == expected, (t, kw)
        steps[step] = steps.get(step, 0) + 1
        survived = {i for i in origin if i is not None}
        for h in hits:
            assert not survived & set(range(h.start, h.end)), (t, kw, h)
        if any(scan_mod._path_only(h) for h in hits):
            # A reported path-only hit makes the whole text one placeholder.
            one = scan_mod._PLACEHOLDER.fullmatch(out)
            assert one and one.group(1) in fuzz.ids, (t, kw, out)
        count_scans()
    # The fuzz reaches texts with hits and texts that need a second pass.
    assert steps.get(0, 0) and steps.get(1, 0) and steps.get(2, 0), steps


# -- the steps ------------------------------------------------------------------------------

def test_a_text_that_needs_the_line_step(engine_name, names_config, count_scans):
    # Each replacement leaves a "]" next to the following "x", which hits again.
    names_config(anywhere=["]x"])
    text = "keep this\n" + ph("jwt") + "xxxxx\nand this"
    count_scans()
    out = redact(text, ("private-names",))
    assert out == "keep this\n" + ph("private-name") + "\nand this"
    assert count_scans() == 5
    assert scan(out, ("private-names",)) == []


def test_a_text_that_needs_the_whole_text_step(engine_name, names_config, count_scans):
    # The line step leaves two placeholder lines, and "] [" spans the newline between.
    names_config(anywhere=["]x", "] ["])
    text = ph("jwt") + "xxxxx\n" + ph("jwt") + "xxxxx"
    count_scans()
    out = redact(text, ("private-names",))
    assert out == ph("private-name")
    assert count_scans() == 5
    assert redact(out, ("private-names",)) == out


# -- path-only hits -------------------------------------------------------------------------

@pytest.mark.parametrize("sets", [("credentials",), SETS])
def test_a_path_only_hit_redacts_to_one_placeholder(engine_name, fuzz_names, sets):
    for text in ("x", "", "two\nlines with ghp", ph("jwt") + "\n"):
        out = redact(text, sets, path=P12)
        assert out == ph("pkcs12-file"), text
        assert scan(out, sets, path=P12) == []
        assert redact(out, sets, path=P12) == out


def test_a_placeholder_naming_what_secrets_cannot_emit_is_ordinary_text(engine_name,
                                                                         names_config):
    # Only a placeholder secrets could have written is opaque: a secret wrapped as one
    # (a token, a private name, a known value) is still reported and redacted.
    names_config(words=["alexa quill"])
    token = corpus.github(corpus.rng(43))
    value = "kv-" + corpus.chars(corpus.rng(44), 12)
    for inside, sets, known, rule in ((token, ("credentials",), (), "github-pat"),
                                      ("Alexa Quill", ("private-names",), (), "private-name"),
                                      (value, ("credentials",), (value,), "known")):
        text = f"a {ph(inside)} b"
        hits = scan(text, sets, known=known)
        assert [(h.rule, text[h.start:h.end]) for h in hits] == [(rule, inside)], rule
        out = redact(text, sets, known=known)
        assert inside not in out and out == f"a [REDACTED:{ph(rule)}] b", rule


def test_placeholder_only_text_comes_back_unchanged(engine_name, fuzz_names, count_scans):
    every = [ph(rule) for rule in sorted(scan_mod.emittable())]
    for text in every + ["".join(every)]:
        count_scans()
        assert redact(text, SETS, path=P12) == text
        assert count_scans() == 1


# -- merging, known, the caller's text ------------------------------------------------------

def test_overlapping_spans_merge_labelled_by_the_first(engine_name):
    text = "xx abcdefghi yy"
    assert redact(text, ("credentials",), known=["abcdef", "defghi"]) == \
        "xx " + ph("known") + " yy"
    token = corpus.github(corpus.rng(40))
    assert redact("id=" + token + " end", ("credentials",), known=["id=" + token[:4]]) == \
        ph("known") + " end"
    # Touching spans stay two placeholders.
    assert redact("abcdef", ("credentials",), known=["abc", "def"]) == ph("known") * 2


def test_known_values_come_first(engine_name):
    token = corpus.github(corpus.rng(41))
    assert redact(f"x {token}", ("credentials",)) == "x " + ph("github-pat")
    assert redact(f"x {token}", ("credentials",), known=[token]) == "x " + ph("known")


def test_an_empty_known_value_is_ignored(engine_name):
    assert redact("abc", ("credentials",), known=[""]) == "abc"
    assert redact("", ("credentials",), known=[""]) == ""


def test_text_outside_hits_is_unchanged_including_escaped_bytes(engine_name):
    r = corpus.rng(42)
    token = corpus.stripe(r)
    raw = (b"\xef\xbb\xbfline \xff one\r\n\tcaf\xc3\xa9 " + token.encode() +
           b"\r\nend \x80\xfe")
    text = raw.decode("utf-8", "surrogateescape")
    out = redact(text, ("credentials",))
    assert out.encode("utf-8", "surrogateescape") == raw.replace(
        token.encode(), b"[REDACTED:stripe-access-token]")


def test_redact_takes_no_mask():
    assert "mask" not in inspect.signature(redact).parameters
    with pytest.raises(TypeError):
        redact("x", ("credentials",), mask="{rule}")


@pytest.fixture
def dead_worker(monkeypatch, fallback_engine, tmp_path):
    fallback.shutdown()
    monkeypatch.setattr(fallback, "command",
                        lambda: [str(tmp_path / "no-such-python"), "-c", "pass"])
    yield
    fallback.shutdown()


def test_a_worker_that_will_not_start_gives_one_placeholder(dead_worker):
    for text in ("hello", "two\nlines", ""):
        assert redact(text, GITLEAKS) == ph("unscanned"), text
    assert redact("a\nb", ("credentials",), path=P12) == ph("unscanned")
    assert sys.modules.get("re2") is None


def test_a_dead_worker_makes_a_text_with_a_see_through_character_unscanned(dead_worker):
    text = "a " + corpus.split("value", "\u200b") + "\nb"
    assert redact(text, GITLEAKS) == ph("unscanned")
    # The copy's unscanned hit is the text's own: one per set, not two.
    assert [(h.set, h.rule) for h in scan(text, GITLEAKS)] == [
        ("credentials", "unscanned"), ("broad", "unscanned")]


# -- nothing beside a secret keeps it -------------------------------------------------------

def test_the_allow_mark_keeps_nothing_out_of_redact(engine_name):
    token = corpus.github(corpus.rng(50))
    text = '{"role": "user", "text": "t = ' + token + '  # gitleaks:allow"}'
    out = redact(text, GITLEAKS)
    assert token not in out
    assert out == '{"role": "user", "text": "t = ' + ph("github-pat") + '  # gitleaks:allow"}'


def test_a_line_allowlist_keeps_nothing_out_of_redact(engine_name):
    r = corpus.rng(51)
    for form, path in corpus.LINE_ALLOWED:
        value = corpus.generic_value(r)
        text = form.format(value=value)
        out = redact(text, ("broad",), path=path)
        assert value.split("=")[1] not in out, form
        assert ph("generic-api-key") in out, form
        assert redact(out, ("broad",), path=path) == out, form


# -- see-through characters -----------------------------------------------------------------

def test_every_see_through_character_splits_no_token(engine_name):
    token = corpus.github(corpus.rng(52))
    chars = corpus.see_through()
    text = "\n".join("k" + str(i) + " " + corpus.split(token, c) + " end"
                     for i, c in enumerate(chars))
    out = redact(text, ("credentials",))
    assert out == "\n".join("k" + str(i) + " " + ph("github-pat") + " end"
                            for i in range(len(chars)))


def test_tab_newline_and_spaces_are_not_read_past(engine_name):
    token = corpus.github(corpus.rng(53))
    for c in corpus.NOT_SEEN:
        text = "k " + corpus.split(token, c) + " end"
        assert redact(text, ("credentials",)) == text, hex(ord(c))


def test_a_split_token_at_either_end_takes_in_the_characters_there(engine_name):
    token = corpus.github(corpus.rng(54))
    z = "\u200b"
    every = z + z.join(token) + "\u00ad"
    assert redact("a " + every + " b", ("credentials",)) == \
        "a " + z + ph("github-pat") + "\u00ad b"
    assert redact(z + corpus.split(token, z) + " tail", ("credentials",)) == \
        ph("github-pat") + " tail"
    assert redact("head " + corpus.split(token, z) + z, ("credentials",)) == \
        "head " + ph("github-pat")
    for one in (corpus.split(token, z), every, z + z.join(token)):
        assert redact(one, ("credentials",)) == ph("github-pat")


def test_an_allowlist_line_keeps_a_split_value_and_a_split_known_value_is_found(engine_name):
    token = corpus.github(corpus.rng(55))
    allow = Allowlist.from_text("\t".join(("*", "credentials/github-pat",
                                           scan_mod.fingerprint(token), "2026-11-20",
                                           "a test value")) + "\n")
    text = "k " + corpus.split(token, "\u200b") + " end"
    assert redact(text, ("credentials",), allow=allow) == text
    assert redact(text, ("credentials",)) == "k " + ph("github-pat") + " end"
    value = "kv-" + corpus.chars(corpus.rng(56), 12)
    assert redact("x " + corpus.split(value, "\u200b", 5) + " y", ("credentials",),
                  known=[value]) == "x " + ph("known") + " y"


def test_a_split_value_that_overlaps_its_own_whole_copy_is_redacted_whole(engine_name):
    # The value starts and ends with the same two characters, so two copies of it can
    # share them; the first copy is split, the second is whole in the text.
    r = corpus.rng(57)
    border, middle = "Q7", corpus.chars(r, 20)
    value = border + middle + border
    text = border + corpus.split(middle, "​") + border + middle + border
    assert redact(text, ("credentials",), known=[value]) == ph("known")
    assert [(h.start, h.end) for h in scan(text, ("credentials",), known=[value])] == [
        (0, len(value) + 1), (len(value) - 1, len(text))]


def test_redact_version():
    assert REDACT_VERSION == 2
    assert "REDACT_VERSION" in moxi_secrets.__all__


def test_seen_through_is_the_api_modules():
    assert moxi_secrets.seen_through is scan_mod.seen_through
    assert "seen_through" in moxi_secrets.__all__
