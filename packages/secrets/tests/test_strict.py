"""Strict mode: nothing beside a value keeps it whole, on both engines and through every
copy the scan reads; the end-condition regexes; the floors and their search's bound."""

from __future__ import annotations

import importlib
import json
import string

import corpus
import pytest

from moxi_secrets import _generate as gen
from moxi_secrets import engine as eng_mod
from moxi_secrets import fallback, redact, scan

scan_mod = importlib.import_module("moxi_secrets.scan")

GITLEAKS = ("credentials", "broad")
#: A made-up value of the shape a generic key takes.
VALUE = "Zq8Rk2Lm9Xw4Tb7Yp3Hd"

#: Each kind of text that keeps a value whole today, written beside a keyed value.
BESIDE = {
    "mark": f"api_key={VALUE} # gitleaks:allow",
    "mark-elsewhere-json": '{"note": "gitleaks:allow", "api_key": "%s"}' % VALUE,
    "mount-line": f"RUN --mount=type=secret,id=x api_key={VALUE}",
    "import-line": f"import x from 'y'; api_key={VALUE}",
    "allowlisted-name": f"author-api_key = {VALUE}",
    "allowlisted-name-json": '{"author-token": "%s"}' % VALUE,
    "example": f"api_key={VALUE}-example",
    "test": f"api_key={VALUE}.test",
    "false": f"api_key={VALUE}false",
    "null": f"api_key={VALUE}null",
    "alphabet": f"api_key={VALUE}abcdefghijklmnopqrstuvwxyz",
    "padding-a": f"api_key={VALUE}" + "a" * 60,
    "padding-dash": f"api_key={VALUE}" + "-" * 60,
    "padding-quoted": f'api_key="{VALUE}' + "a" * 60 + '"',
    "padding-dash-quoted": f'api_key="{VALUE}' + "-" * 60 + '"',
    "comma": f"api_key={VALUE},",
    "ampersand": f"https://x.test/v1?api_key={VALUE}&page=2",
    "paren": f"(api_key={VALUE})",
}


def left_whole(text, value=VALUE, sets=GITLEAKS, **kw) -> bool:
    return value in redact(text, sets, strict=True, **kw)


def test_a_keyed_value_alone_is_redacted(engine_name):
    assert not left_whole(f"api_key={VALUE}")


@pytest.mark.parametrize("kind", list(BESIDE))
def test_nothing_beside_a_value_keeps_it_whole(engine_name, kind):
    assert not left_whole(BESIDE[kind]), BESIDE[kind]


def test_strict_redacts_what_the_default_keeps_and_the_default_is_unchanged(engine_name):
    kept = [k for k, text in BESIDE.items() if VALUE in redact(text, GITLEAKS)]
    # The default redact still keeps these whole; strict keeps none of them.
    assert {"allowlisted-name", "false", "padding-a", "comma", "ampersand"} <= set(kept)


def test_a_google_key_in_a_url_followed_by_an_ampersand(engine_name):
    key = corpus.google_api(corpus.rng(21))
    text = f"https://maps.googleapis.com/x?key={key}&page=2"
    assert key in redact(text, GITLEAKS)
    assert not left_whole(text, key)


@pytest.mark.parametrize("pad", ["a" * 60, "-" * 60])
def test_a_bare_random_run_stays_redacted_with_padding_glued_on(engine_name, pad):
    r = corpus.rng(22)
    for _ in range(10):
        run = corpus.chars(r, 40, corpus.ALNUM)
        assert not left_whole(f"x {run}{pad} y", run)


def test_random_values_after_a_key_name_are_all_redacted(engine_name):
    r = corpus.rng(23)
    for size, alphabet, count in ((32, corpus.ALNUM, 1500), (12, corpus.ALNUM, 300),
                                  (32, "0123456789abcdef", 300)):
        values = [corpus.chars(r, size, alphabet) for _ in range(count)]
        text = "\n".join(f'api_key = "{v}"' for v in values)
        out = redact(text, GITLEAKS, strict=True)
        assert not [v for v in values if v in out], (size, alphabet)


def test_bare_small_alphabet_runs_stay_whole_no_more_often_than_stated(engine_name):
    # A run found only by how random it looks keeps its floor: by chance a 40-character
    # base32 run stays whole about 3 times in 10 and a lowercase base36 run 1 in 10.
    r = corpus.rng(24)
    for alphabet, most in ((string.ascii_uppercase + "234567", 108),
                           (string.ascii_lowercase + string.digits, 45),
                           (corpus.ALNUM, 0)):
        values = [corpus.chars(r, 40, alphabet) for _ in range(300)]
        text = " \n".join(values)
        found = {text[h.start:h.end] for h in scan(text, ("broad",), strict=True)}
        assert sum(v not in found for v in values) <= most, alphabet


# -- personal data and private names ---------------------------------------------------------

def test_personal_data_is_redacted_in_strict_mode_with_text_beside_it(engine_name):
    r = corpus.rng(25)
    card, iban, ssn = corpus.card(r, 16), corpus.iban(r), "536-22-1987"
    for value in (card, iban, ssn):
        for text in (f"pay {value} # gitleaks:allow", f"RUN --mount=type=secret, {value}",
                     f"author: {value} ok", f"({value}),"):
            assert not left_whole(text, value, ("personal-data",)), text


def test_a_private_name_inside_an_allowed_entry_is_redacted(engine_name, names_config):
    names_config(words=["quillfeather"], allowed=["quillfeather labs"],
                 author_name="Quillfeather")
    text = "made by quillfeather labs"
    assert "quillfeather" in redact(text, ("private-names",))
    assert "quillfeather" not in redact(text, ("private-names",), strict=True)
    assert "Quillfeather" not in redact("signed Quillfeather", ("private-names",),
                                        strict=True)


# -- every copy the scan reads ----------------------------------------------------------------

SPLIT = VALUE[:10] + "​" + VALUE[10:]


@pytest.mark.parametrize("text", [
    f"https://x.test/v1?api_key={SPLIT}&page=2",
    f"api_key={SPLIT}-example",
    f"a\x0bb api_key={SPLIT},",
    f"author-api_key = {SPLIT}",
], ids=["copy-ampersand", "copy-stopword", "spaced-copy-comma", "copy-allowlisted-name"])
def test_a_split_token_is_never_judged_non_strict(engine_name, text):
    assert SPLIT in redact(text, GITLEAKS) or VALUE in redact(text, GITLEAKS)
    out = redact(text, GITLEAKS, strict=True)
    assert SPLIT not in out and VALUE[:10] not in out


def test_every_worker_request_of_a_strict_scan_is_strict(fallback_engine, monkeypatch):
    seen = []
    real = fallback.scan

    def spy(t, names, path, *, keep_marked=True, strict=False):
        seen.append(strict)
        return real(t, names, path, keep_marked=keep_marked, strict=strict)

    monkeypatch.setattr(fallback, "scan", spy)
    text = f"a\x0bb api_key={SPLIT},"
    redact(text, GITLEAKS, strict=True)
    assert len(seen) >= 3 and all(seen)
    seen.clear()
    redact(text, GITLEAKS)
    assert seen and not any(seen)


# -- the end-condition regexes -----------------------------------------------------------------

def test_each_strict_regex_is_its_regex_without_the_end_condition():
    with open(scan_mod.sets_path(), "rb") as fh:
        data = json.loads(fh.read())
    count = 0
    for name in GITLEAKS:
        for rule in data["sets"][name]["rules"]:
            regex, strict = rule["regex"], rule["strictRegex"]
            if regex is None or not regex["pattern"].endswith(gen.END_CONDITION):
                assert strict is None, rule["id"]
                continue
            count += 1
            assert strict["pattern"] + gen.END_CONDITION == regex["pattern"]
            assert strict["fallback"] == gen.translate(strict["pattern"])
    assert count == 151


def test_the_strict_forms_find_the_same_spans_on_both_engines():
    re2_engine = eng_mod.Re2Engine(eng_mod.re2_module())
    py_engine = eng_mod.python()
    texts = list(BESIDE.values()) + [
        f"x {corpus.chars(corpus.rng(26), 40, corpus.ALNUM)}" + "a" * 60,
        f"https://maps.googleapis.com/x?key={corpus.google_api(corpus.rng(27))}&page=2",
        f"token: {corpus.github(corpus.rng(28), 'p')},",
        f"stripe {corpus.stripe(corpus.rng(29))})",
    ]
    for text in texts:
        t = eng_mod.Text(text)
        a = scan_mod.run_gitleaks(t, GITLEAKS, None, re2_engine, strict=True)
        b = scan_mod.run_gitleaks(t, GITLEAKS, None, py_engine, strict=True)
        assert a == b, text


# -- the floor's search ------------------------------------------------------------------------

def test_a_value_of_few_distinct_characters_costs_no_search():
    budget = scan_mod._Budget(10)
    assert not scan_mod.random_enough("-" * 100_000, 4.3, budget)
    assert budget.left == 10


def test_padding_never_pulls_a_value_under_its_floor():
    run = corpus.chars(corpus.rng(30), 40, corpus.ALNUM)
    assert scan_mod._entropy(run + "a" * 60) <= 4.3
    assert scan_mod.random_enough(run + "a" * 60, 4.3, scan_mod._Budget(10_000))
    assert scan_mod.random_enough("a" * 60 + run, 4.3, scan_mod._Budget(100_000))


def test_the_search_raises_past_its_budget():
    hard = pathological()
    with pytest.raises(scan_mod._OverBudget):
        scan_mod.random_enough(hard, 4.3, scan_mod._Budget(scan_mod.STRETCH_STEPS))


def pathological() -> str:
    """A run of 9,300 letters and digits, 30 `a` then one other character, repeated: it
    holds many distinct characters, and no stretch passes the floor."""
    others = (string.ascii_letters + string.digits).replace("a", "")
    return "".join("a" * 30 + others[i % len(others)] for i in range(300))


def test_a_text_past_the_bound_comes_back_unscanned(engine_name):
    text = "x " + pathological() + " y"
    assert [h.rule for h in scan(text, ("broad",), strict=True)] == ["unscanned"]
    assert redact(text, ("broad",), strict=True) == "[REDACTED:unscanned]"
    assert "unscanned" not in [h.rule for h in scan(text, ("broad",))]


def test_a_hundred_thousand_dashes_are_judged_in_one_pass(engine_name):
    assert scan("x " + "-" * 100_000 + " y", ("broad",), strict=True) == []
