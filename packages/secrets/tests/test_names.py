"""Private names: the matching rule as a property test against today's guards, both
engines alike, the worked cases, linear time, and reading the list from the config."""

from __future__ import annotations

import importlib.util
import random
import re
import time
import unicodedata
from pathlib import Path

import pytest

from moxi_secrets import PrivateNames, SetUnavailable, _folds, engine, private_names, redact
from moxi_secrets import names as names_mod
from moxi_secrets import scan
from moxi_secrets._folds import FOLDS

RE2 = engine.Re2Engine(engine.re2_module())
PY = engine.ReEngine()
SEPS = set(names_mod.SEPARATORS)


def hits(text, words=(), anywhere=(), emails=(), allowed=(), eng=RE2, **author):
    pn = PrivateNames(words=words, anywhere=anywhere, emails=emails, allowed=allowed,
                      **author)
    return [(s, e) for _, _, _, s, e in names_mod.spans(engine.Text(text), pn, eng)]


def texts_of(text, spans):
    return [text[s:e] for s, e in spans]


def test_whitespace_is_every_character_python_calls_space():
    assert set(names_mod.WHITESPACE) == {chr(c) for c in range(0x110000)
                                         if chr(c).isspace()}


@pytest.mark.parametrize("raw, norm", [
    ("Alexa  Quill", "Alexa Quill"), ("  -alexa_quill- ", "alexa_quill"),
    ("a -_ b", "a b"), ("a--b", "a--b"), ("-_-", ""), ("   ", ""), ("x\t\ny", "x y"),
])
def test_normalise(raw, norm):
    assert names_mod.normalise(raw) == norm


# -- the worked cases -----------------------------------------------------------------------

@pytest.mark.parametrize("eng", [RE2, PY], ids=["re2", "re"])
def test_the_worked_cases(eng):
    assert texts_of("alexa-quillx", hits("alexa-quillx", ["alexa", "alexa-quill"],
                                         eng=eng)) == ["alexa"]
    assert texts_of("see alexa quillx", hits("see alexa quillx", ["alexa", "alexa-quill"],
                                             eng=eng)) == ["alexa"]
    text = "/var/home/aquill9/x"
    assert texts_of(text, hits(text, ["/home/aquill9"], eng=eng)) == ["/home/aquill9"]
    assert texts_of("cd~/base", hits("cd~/base", ["~/base"], eng=eng)) == ["~/base"]
    assert texts_of("C++a", hits("C++a", ["c++"], eng=eng)) == ["C++"]
    for text in ("alexa-quill", "aquill9-laptop", "quill_family", "/home/aquill9/",
                 "Alexa's"):
        assert hits(text, ["alexa", "aquill9", "quill"], eng=eng), text
    for text in ("PublicAlexa", "alexa2000"):
        assert hits(text, ["alexa"], eng=eng) == [], text
    assert texts_of("acmecorp_x xacmecorp", hits(
        "acmecorp_x xacmecorp", anywhere=["acmecorp"], eng=eng)) == ["acmecorp"] * 2
    # A match inside a longer reported one comes back once, as the longer one.
    assert texts_of("hi alexa quill", hits("hi alexa quill", ["alexa", "alexa quill"],
                                           eng=eng)) == ["alexa quill"]
    # The search restarts one character after a match's start, so a match refused for
    # its neighbour does not hide one it overlaps, and overlapping matches all count.
    assert hits("xab ab ab", ["ab ab"], eng=eng) == [(4, 9)]
    assert hits("aaa", anywhere=["aa"], eng=eng) == [(0, 2), (1, 3)]


@pytest.mark.parametrize("eng", [RE2, PY], ids=["re2", "re"])
def test_allowed_text_and_the_author_never_hit(eng):
    text = "mail alexa@example.test or alexa.quill.dev, by Alexa Quill"
    found = hits(text, ["alexa"], emails=["alexa@example.test"],
                 allowed=["alexa.quill.dev"], author_name="Alexa Quill",
                 author_email="alexa@example.test", eng=eng)
    assert found == []
    assert hits("alexa here", ["alexa"], allowed=["alexa.quill.dev"], eng=eng) == [(0, 5)]


@pytest.mark.parametrize("eng", [RE2, PY], ids=["re2", "re"])
@pytest.mark.parametrize("entry", [" ", " b", "- b"])
def test_separator_heavy_entries_run_in_linear_time(eng, entry):
    text = " " * 320_000 + "b"
    started = time.perf_counter()
    hits(text, [entry, "a b"], eng=eng)
    assert time.perf_counter() - started < 3.0


def test_the_case_folds_agree_on_both_engines():
    text = "İstanbul ISTANBUL ıstanbul ſome Kelvin kelvin"
    for entry in ("istanbul", "some", "kelvin"):
        assert hits(text, [entry], eng=RE2) == hits(text, [entry], eng=PY)
        assert hits(text, [entry], eng=RE2)


# -- the property test ----------------------------------------------------------------------

# Unicode 16 letters (U+A7CB with U+0264, Garay), the Greek and ligature pairs Go folds
# apart and Python together, the i family, the long s and the Kelvin sign.
ALPHABET = ("abcXYZ" + "İıſK" + "éßσж" + "019" +
            "\ua7cb\u0264\U00010d50\U00010d70" + "\u0390\u1fd3\u03b0\u1fe3\ufb05\ufb06" +
            "-_ ./~+'@")
NEIGHBOURS = "aZé0 ._-/\nж'"


def gen_entry(r):
    entry = "".join(r.choice(ALPHABET) for _ in range(r.randrange(1, 7)))
    if r.random() < 0.15:
        entry = r.choice(" -_") + entry + r.choice(" -_")
    return entry


def gen_list(r):
    entries = [gen_entry(r) for _ in range(r.randrange(1, 7))]
    if r.random() < 0.5:    # prefixes and extensions of each other
        base = entries[0]
        entries.append(base + r.choice(ALPHABET))
        if len(base) > 1:
            entries.append(base[:-1])
    return entries


def dress(r, entry):
    out = []
    for c in entry:
        if c in SEPS:
            out.append(r.choice([" ", "-", "_", "\t", "  ", " - "]))
        else:
            out.append(c.upper() if r.random() < 0.3 and len(c.upper()) == 1 else c)
    return "".join(out)


def gen_text(r, entries):
    parts = []
    for _ in range(r.randrange(1, 8)):
        if r.random() < 0.7:
            parts.append(dress(r, r.choice(entries)))
        else:
            parts.append("".join(r.choice(ALPHABET) for _ in range(r.randrange(1, 4))))
        parts.append("".join(r.choice(NEIGHBOURS) for _ in range(r.randrange(0, 3))))
    return "".join(parts)


def guard_spans(text, entries):
    """The spans today's guards hit: \\b as one alternation and per entry, the strict
    form and the path form, each over the raw entry under IGNORECASE."""
    out = []
    escaped = [re.escape(e) for e in entries if e]
    if escaped:
        out += [m.span() for m in re.finditer(r"(?i)\b(?:" + "|".join(escaped) + r")\b",
                                              text)]
    for raw in entries:
        if not raw:
            continue
        e = re.escape(raw)
        forms = [r"(?i)\b{e}\b", r"(?i)(?<![\w-]){e}(?![\w-])"]
        if not raw.lstrip(" -_")[:1].isalnum():
            # The path form, which checks no left neighbour, guards paths only.
            forms.append(r"(?i){e}(?![\w.-])")
        for form in forms:
            out += [m.span() for m in re.finditer(form.replace("{e}", e), text)]
    return out


def covered(spans):
    return {i for s, e in spans for i in range(s, e)}


def test_the_rule_covers_todays_guards_and_both_engines_agree():
    r = random.Random(20261030)
    checked = 0
    for _ in range(400):
        entries = gen_list(r)
        for _ in range(4):
            text = gen_text(r, entries)
            on_re2 = hits(text, entries, eng=RE2)
            assert on_re2 == hits(text, entries, eng=PY), (entries, text)
            cover = covered(on_re2)
            for s, e in guard_spans(text, entries):
                for i in range(s, e):
                    if text[i] not in SEPS:
                        assert i in cover, (entries, text, (s, e))
                        checked += 1
            for entry in entries:
                assert covered(hits(text, [entry], eng=RE2)) <= cover, (entries, text)
    assert checked > 1000


# -- case, written out ----------------------------------------------------------------------

def test_no_entry_pattern_carries_a_case_insensitive_flag():
    for entry in ("alexa quill", "İstanbul", "kelvin", "ſome", "Ɤ\U00010d50", "c++"):
        norm = names_mod.normalise(entry)
        py, r2 = names_mod.python_pattern(norm), names_mod.re2_pattern(norm)
        assert "(?i" not in py and "(?i" not in r2
        assert not re.compile(py).flags & re.IGNORECASE


def _foldable() -> set[int]:
    """Every character this Python could fold to another, and the single characters its
    case mappings give, closed under one case-insensitive pass over every code point."""
    pool = set()
    for c in range(0x110000):
        if 0xD800 <= c <= 0xDFFF:
            continue
        ch = chr(c)
        for mapped in (ch.lower(), ch.upper(), ch.casefold(), ch.title()):
            if mapped != ch:
                pool.add(c)
                if len(mapped) == 1:
                    pool.add(ord(mapped))
    everything = "".join(chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF)
    while True:
        rx = re.compile("(?i)[" + "".join(re.escape(chr(c)) for c in sorted(pool)) + "]")
        more = {ord(m.group()) for m in rx.finditer(everything)} - pool
        if not more:
            return pool
        pool |= more


def test_this_python_folds_no_character_outside_its_set():
    """On a Python whose Unicode is newer than the table's this fails first: regenerate
    `_folds.py` with tools/name_folds.py on that Python."""
    pool = sorted(_foldable())
    text = "".join(chr(c) for c in pool)
    outside = []
    for c in pool:
        held = set(FOLDS.get(chr(c), chr(c)))
        for form in ("(?i)" + re.escape(chr(c)), "(?i)[" + re.escape(chr(c)) + "]"):
            if not {m.group() for m in re.finditer(form, text)} <= held:
                outside.append(f"U+{c:04X}")
    assert outside == [], (unicodedata.unidata_version, _folds.UNICODE, outside[:20])


def test_each_fold_set_is_one_class_shared_by_its_members():
    for c, group in FOLDS.items():
        assert c in group and all(FOLDS[x] == group for x in group)
        assert len(set(group)) == len(group) > 1


def test_a_one_character_entry_matches_exactly_its_set_on_both_engines():
    r = random.Random(32)
    pool = set(FOLDS) | {chr(c) for c in range(0x21, 0x7F)} | set(ALPHABET)
    while len(pool) < len(FOLDS) + 1500:
        c = r.randrange(0x110000)
        if not 0xD800 <= c <= 0xDFFF:
            pool.add(chr(c))
    pool -= SEPS
    t = engine.Text("".join(sorted(pool)))
    for eng, pattern in ((RE2, names_mod.re2_pattern), (PY, names_mod.python_pattern)):
        target = eng.target(t)
        for c in sorted(pool):
            rx = eng.compile(pattern(c))
            got = {t.text[eng.to_str(t, m.start())] for m in rx.finditer(target)}
            assert got == set(FOLDS.get(c, c)), (f"U+{ord(c):04X}", eng.name)


def test_the_fold_table_equals_a_fresh_generation():
    tool = Path(__file__).resolve().parent.parent / "tools" / "name_folds.py"
    if not tool.exists():
        pytest.skip("tools/name_folds.py is not in this tree (an sdist leaves it out)")
    if unicodedata.unidata_version != _folds.UNICODE:
        pytest.skip(f"the table is generated on Unicode {_folds.UNICODE}, this Python has "
                    f"{unicodedata.unidata_version}")
    spec = importlib.util.spec_from_file_location("name_folds", tool)
    name_folds = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(name_folds)
    fresh = name_folds.render(name_folds.groups(name_folds.python_folds()), _folds.UNICODE)
    assert fresh == Path(_folds.__file__).read_text("utf-8")


# -- the config -----------------------------------------------------------------------------

def test_an_edit_to_the_config_is_seen_at_the_next_scan(names_config):
    names_config(words=["quillfeather"])
    assert [h.rule for h in scan("by quillfeather", ("private-names",))] == ["private-name"]
    names_config(words=["inkwell"], emails=["someone@example.test"])
    found = scan("by quillfeather, inkwell, someone@example.test", ("private-names",))
    assert [h.rule for h in found] == ["private-name", "private-email"]
    assert private_names().words == ("inkwell",)


def test_an_empty_list_finds_nothing_and_an_empty_entry_is_left_out(names_config):
    assert scan("anything", ("private-names",)) == []
    names_config(words=["  ", "quill", "-_"])
    assert len(scan("a quill", ("private-names",))) == 1
    assert names_mod.empties(private_names()) == [("private_names.words", 1),
                                                  ("private_names.words", 3)]


@pytest.mark.parametrize("raw", [
    '[private_names]\nwords = ["quillfeather", 2024]\n',
    '[private_names]\nwords = "quillfeather"\n',
    '[private_names\nwords = ["quillfeather"]\n',
])
def test_a_list_that_will_not_load_raises_and_never_returns_no_hits(names_config, raw):
    names_config(raw=raw)
    with pytest.raises(SetUnavailable) as caught:
        scan("quillfeather", ("private-names",))
    assert "quillfeather" not in str(caught.value)
    with pytest.raises(SetUnavailable):
        redact("quillfeather", ("private-names",))
    with pytest.raises(SetUnavailable):
        private_names()


def test_private_names_repr_carries_no_entry(names_config):
    names_config(words=["quillfeather"], author_name="Alexa Quill")
    pn = private_names()
    assert pn.author_name == "Alexa Quill"
    assert "quill" not in repr(pn).lower()
