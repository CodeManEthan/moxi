"""Personal data: the set without stdnum, the numeric ids Moxi emits staying quiet, grouped
cards and IBANs, US SSNs, and the false-positive rate on bare digit runs."""

from __future__ import annotations

import string
import sys

import corpus
import pytest

from moxi_secrets import SetUnavailable, describe, personal, scan

PD = ("personal-data",)
DIGITS = string.digits

#: Ids per kind in the suite. The design's full run is 100,000 per kind.
IDS_PER_KIND = 5_000


def cards_in(text):
    return [text[h.start:h.end] for h in scan(text, PD) if h.rule == "payment-card"]


# -- without stdnum -------------------------------------------------------------------------

def test_without_stdnum_the_set_is_unavailable_and_says_how_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "stdnum", None)
    monkeypatch.setattr(personal, "_stdnum", None)
    assert personal.available() is False
    assert {i.name: i.available for i in describe()}["personal-data"] is False
    with pytest.raises(SetUnavailable) as caught:
        scan("pay 4111 1111 1111 1111", PD)
    assert "moxi-secrets[personal-data]" in str(caught.value)
    # The other sets still run.
    assert scan("hello", ("credentials",)) == []


def test_with_stdnum_the_set_is_available():
    assert personal.available() is True
    assert {i.name: i.available for i in describe()}["personal-data"] is True


# -- the ids Moxi emits ---------------------------------------------------------------------

def comms_ids(r, n):
    base = 2**62 + r.randrange(10**9)
    return [str(base + i) for i in range(n)]


def snowflakes(r, n):
    # Milliseconds since 2015 (from a month in, where ids reach 17 digits) in the top
    # bits, 22 bits of worker, process and sequence.
    return [str((r.randrange(3 * 10**9, 6 * 10**11) << 22) | r.randrange(2**22))
            for _ in range(n)]


def timestamps(r, n, per_second):
    # 2015 to 2035.
    lo, hi = 1_420_070_400, 2_051_222_400
    return [str(r.randrange(lo * per_second, hi * per_second)) for _ in range(n)]


@pytest.mark.parametrize("kind, make, lengths", [
    ("comms local ids", comms_ids, {19}),
    ("discord snowflakes", snowflakes, {17, 18, 19}),
    ("ns timestamps", lambda r, n: timestamps(r, n, 10**9), {19}),
    ("us timestamps", lambda r, n: timestamps(r, n, 10**6), {16}),
    ("ms timestamps", lambda r, n: timestamps(r, n, 10**3), {13}),
])
def test_the_numeric_ids_moxi_emits_raise_no_card_hit(engine_name, kind, make, lengths):
    ids = make(corpus.rng(len(kind)), IDS_PER_KIND)
    assert {len(i) for i in ids} <= lengths
    for sep in ("\n", " ", ", "):
        assert cards_in(sep.join(ids)) == [], (kind, sep)


# -- cards ----------------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["4", "51", "55", "2221", "2720", "34", "37", "6011",
                                    "65", "35", "30", "36", "38"])
def test_a_card_of_each_issuer_hits_bare_and_grouped(engine_name, prefix):
    r = corpus.rng(int(prefix))
    number = corpus.card(r, 16, prefix)
    for written in (number, corpus.grouped(number), corpus.grouped(number, sep="-")):
        assert cards_in(f"card: {written}.") == [written]


def test_a_grouped_19_digit_card_hits_and_a_bare_one_does_not(engine_name):
    r = corpus.rng(19)
    number = corpus.card(r, 19, "4")
    for sep in (" ", "-"):
        written = corpus.grouped(number, sep=sep)
        assert cards_in(f"card {written} exp") == [written]
    assert cards_in(f"card {number} exp") == []


@pytest.mark.parametrize("length", [13, 14, 15, 17, 18])
def test_grouped_cards_of_other_lengths_hit(engine_name, length):
    r = corpus.rng(length)
    number = corpus.card(r, length, "4")
    written = corpus.grouped(number)
    assert cards_in(f"x {written} y") == [written]


def test_amex_and_diners_groups_hit(engine_name):
    r = corpus.rng(20)
    amex = corpus.card(r, 15, "37")
    diners = corpus.card(r, 14, "36")
    for number, sizes in ((amex, (4, 6, 5)), (diners, (4, 6, 4))):
        parts, at = [], 0
        for size in sizes:
            parts.append(number[at:at + size])
            at += size
        written = " ".join(parts)
        assert cards_in(f"x {written} y") == [written]


def test_a_card_followed_by_a_cvv_group_is_still_found(engine_name):
    r = corpus.rng(21)
    number = corpus.card(r, 16, "5")
    written = corpus.grouped(number)
    assert written in cards_in(f"card {written} 123 exp 09")


def test_no_digit_next_to_a_card(engine_name):
    r = corpus.rng(22)
    number = corpus.card(r, 16, "4")
    for text in (f"7{number}", f"{number}7", f"9{number}9", f"1 {number[:-1]}"):
        assert cards_in(text) == [], text
    assert cards_in(f"ref:{number};") == [number]
    assert cards_in(f"ref{number}end") == [number]


def test_numbers_that_fail_luhn_or_have_no_issuer_are_quiet(engine_name):
    r = corpus.rng(23)
    good = corpus.card(r, 16, "4")
    bad_luhn = good[:-1] + str((int(good[-1]) + 1) % 10)
    no_issuer = corpus.card(r, 16, "9")
    assert cards_in(f"{bad_luhn} {corpus.grouped(bad_luhn)} {no_issuer}") == []


def test_the_bare_false_positive_rate_on_random_runs_is_near_three_percent(engine_name):
    r = corpus.rng(24)
    n = 20_000
    runs = [corpus.chars(r, r.choice((15, 16)), DIGITS) for _ in range(n)]
    rate = len(cards_in("\n".join(runs))) / n
    assert 0.02 <= rate <= 0.04, rate
    other = [corpus.chars(r, r.choice((9, 10, 12, 13, 14, 17, 18, 19, 20)), DIGITS)
             for _ in range(2_000)]
    assert cards_in("\n".join(other)) == []


# -- IBANs ----------------------------------------------------------------------------------

@pytest.mark.parametrize("country, length, last", [
    ("CH", 21, 1), ("DE", 22, 2), ("FR", 27, 3), ("SI", 19, 3), ("AT", 20, 0),
])
def test_an_iban_hits_bare_and_in_groups(engine_name, country, length, last):
    r = corpus.rng(length)
    value = corpus.iban(r, country, length)
    written = corpus.grouped(value)
    assert len(written.split(" ")[-1]) == (last or 4)
    for text in (value, written):
        hits = [h for h in scan(f"IBAN: {text}, thanks", PD) if h.rule == "iban"]
        assert [f"IBAN: {text}, thanks"[h.start:h.end] for h in hits] == [text]


def test_an_iban_followed_by_a_word_is_still_found(engine_name):
    r = corpus.rng(30)
    value = corpus.grouped(corpus.iban(r, "AT", 20))
    text = f"to {value} from acct"
    hits = [h for h in scan(text, PD) if h.rule == "iban"]
    assert [text[h.start:h.end] for h in hits] == [value]


@pytest.mark.parametrize("before", ["AB12", "ref XY00 1234", "AB12 CD34 EF56 GH78"])
def test_a_grouped_iban_after_a_token_of_its_shape_is_found(engine_name, before):
    # The leftmost candidate starts at the token and takes the IBAN's groups as its own;
    # the search starts again inside it.
    r = corpus.rng(32)
    value = corpus.grouped(corpus.iban(r, "DE", 22))
    text = f"{before} {value} end"
    hits = [h for h in scan(text, PD) if h.rule == "iban"]
    assert [text[h.start:h.end] for h in hits] == [value]


def test_a_wrong_check_or_a_glued_iban_is_quiet(engine_name):
    r = corpus.rng(31)
    value = corpus.iban(r, "DE", 22)
    wrong = value[:2] + f"{(int(value[2:4]) + 1) % 100:02d}" + value[4:]
    assert [h for h in scan(f"x {wrong} y", PD) if h.rule == "iban"] == []
    assert [h for h in scan(f"x Z{value} y", PD) if h.rule == "iban"] == []


# -- US SSNs --------------------------------------------------------------------------------

def test_a_dashed_ssn_hits_and_the_refused_areas_do_not(engine_name):
    def ssns(text):
        return [text[h.start:h.end] for h in scan(text, PD) if h.rule == "us-ssn"]
    assert ssns("ssn 123-45-6789.") == ["123-45-6789"]
    for refused in ("000-12-3456", "666-12-3456", "912-34-5678", "123-00-4567",
                    "123-45-0000"):
        assert ssns(f"ssn {refused}") == [], refused
    assert ssns("1123-45-6789 123-45-67890 123456789") == []
