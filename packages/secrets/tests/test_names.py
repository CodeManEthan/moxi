"""Private names: the matching rule as a property test against today's guards, both
engines alike, the worked cases, linear time, and reading the list from the config."""

from __future__ import annotations

import random
import re
import time

import pytest

from moxi_secrets import PrivateNames, SetUnavailable, engine, private_names, redact, scan
from moxi_secrets import names as names_mod

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

ALPHABET = ("abcXYZ" + "İıſK" + "éßσж" + "019" +
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
