"""The chat redaction: the chat sets, the scrub between two strict passes, failing closed,
and unscanned reported, never returned as clean."""

from __future__ import annotations

import importlib
import sys

import corpus
import pytest

import moxi_secrets
from moxi_secrets import CHAT_SETS, SetUnavailable, chat_redact, fallback

chat_mod = importlib.import_module("moxi_secrets.chat")
scan_mod = importlib.import_module("moxi_secrets.scan")
personal_mod = importlib.import_module("moxi_secrets.personal")

UNSCANNED = "[REDACTED:unscanned]"
VALUE = "Zq8Rk2Lm9Xw4Tb7Yp3Hd"

#: One character of each class the scrub removes.
SCRUBBED = {
    "zero-width-space": "​", "soft-hyphen": "­", "word-joiner": "⁠",
    "byte-order-mark": "﻿", "tag": "\U000e0041", "cr": "\r", "esc": "\x1b",
    "c1": "\x90", "nel": "\x85", "del": "\x7f",
}


def holds_scrubbed(text: str) -> bool:
    return any(chat_mod.scrubbed(c) for c in text)


def test_the_chat_sets_are_fixed_in_code():
    assert CHAT_SETS == ("credentials", "broad", "personal-data")
    assert moxi_secrets.CHAT_SETS is chat_mod.CHAT_SETS


def test_the_scrub_removes_controls_del_c1_and_format_characters_only():
    kept = "a\tb\nc é 中   　 \U0001f600"
    assert chat_mod.scrub(kept) == kept
    for c in SCRUBBED.values():
        assert chat_mod.scrubbed(c) and chat_mod.scrub(f"x{c}y") == "xy", repr(c)
    assert chat_mod.scrub("".join(chr(i) for i in range(0x20))) == "\t\n"
    assert chat_mod.scrub("".join(chr(i) for i in range(0x7f, 0xa0))) == ""


@pytest.mark.parametrize("kind", list(SCRUBBED))
def test_a_secret_split_by_a_scrubbed_character_comes_back_redacted(engine_name, kind):
    c = SCRUBBED[kind]
    token = corpus.github(corpus.rng(31), "p")
    for text in (f"token {token[:12]}{c}{token[12:]} end",
                 f"api_key={VALUE[:8]}{c}{VALUE[8:]}"):
        out = chat_redact(text)
        assert "REDACTED" in out, (kind, out)
        assert token[12:] not in out and VALUE[8:] not in out
        assert not holds_scrubbed(out)


@pytest.mark.parametrize("sep", ["​", "\x0b", "\x0c", "\r", "\x85"],
                         ids=["zero-width-space", "vt", "ff", "lone-cr", "nel"])
def test_a_word_glued_on_by_the_scrub_does_not_unmake_a_token(engine_name, sep):
    # Removing the separator would glue "abc" onto the key, and a key's prefix needs a
    # boundary before it; the first pass, on the raw text, sees the separator.
    key = corpus.stripe(corpus.rng(32))
    out = chat_redact(f"abc{sep}{key} done")
    assert key[8:] not in out and "REDACTED" in out
    assert not holds_scrubbed(out)


def test_the_text_sent_is_the_text_the_last_redaction_saw(engine_name):
    text = f"note\x1b[1m api_key={VALUE}​ and more­ text"
    out = chat_redact(text)
    assert chat_mod.scrub(out) == out
    assert moxi_secrets.redact(out, CHAT_SETS, strict=True) == out
    assert chat_redact(out) == out


def test_nothing_beside_a_value_keeps_it_whole(engine_name):
    for text in (f"api_key={VALUE} # gitleaks:allow", f"author-api_key = {VALUE}",
                 f"api_key={VALUE}false", f"api_key={VALUE}" + "a" * 60,
                 f"https://x.test/v1?api_key={VALUE}&page=2"):
        assert VALUE not in chat_redact(text), text


def test_personal_data_is_redacted(engine_name):
    r = corpus.rng(33)
    card, iban = corpus.card(r, 16), corpus.iban(r)
    out = chat_redact(f"card {card}, iban {iban}, ssn 536-22-1987 # gitleaks:allow")
    assert card not in out and iban not in out and "536-22-1987" not in out


def test_text_with_nothing_to_redact_comes_back_as_it_was(engine_name):
    text = "Plain words, a path src/app/main.py and a date 2026-11-20.\n\tIndented."
    assert chat_redact(text) == text
    assert chat_redact("") == ""


# -- failing closed ----------------------------------------------------------------------------

def test_without_python_stdnum_it_refuses(monkeypatch):
    monkeypatch.setitem(sys.modules, "stdnum", None)
    monkeypatch.setattr(personal_mod, "_stdnum", None)
    with pytest.raises(SetUnavailable):
        chat_redact("")
    with pytest.raises(SetUnavailable):
        chat_redact(f"api_key={VALUE}")


def test_a_failed_worker_gives_the_placeholder_alone(fallback_engine, monkeypatch):
    monkeypatch.setattr(fallback, "scan",
                        lambda t, names, path, **kw: {name: None for name in names})
    assert chat_redact(f"api_key={VALUE} and words") == UNSCANNED
    assert chat_redact("") == UNSCANNED


@pytest.mark.parametrize("which", [0, 1])
def test_unscanned_in_either_pass_gives_the_placeholder_alone(monkeypatch, which):
    real = scan_mod.redacted
    calls = []

    def stub(text, names, **kw):
        calls.append(text)
        out, _ = real(text, names, **kw)
        return out, len(calls) - 1 == which

    monkeypatch.setattr(chat_mod, "redacted", stub)
    assert chat_redact("ordinary words") == UNSCANNED


def test_a_text_past_the_floor_search_bound_gives_the_placeholder_alone(engine_name):
    import string

    others = (string.ascii_letters + string.digits).replace("a", "")
    hard = "".join("a" * 30 + others[i % len(others)] for i in range(300))
    assert chat_redact(f"x {hard} y") == UNSCANNED


def test_placeholder_text_in_the_input_is_not_read_as_unscanned(engine_name):
    text = f"we saw {UNSCANNED} yesterday"
    assert chat_redact(text) == text


def test_only_a_str_is_redacted():
    with pytest.raises(TypeError):
        chat_redact(b"bytes")  # type: ignore[arg-type]


# --- a reader's change between the passes ----------------------------------------------------

#: A made-up token of the GitHub personal-token rule's shape.
GHP = corpus.github(corpus.rng(11))
_PATH = __import__("re").compile(r"/(?:home|srv)/[^\s\"'`<>|,;(){}\[\]]*")


def cut_paths(text):
    """A stand-in for a read tool's path cut: each path under /home/ or /srv/ cut to its
    last segment."""
    return _PATH.sub(lambda m: [p for p in m.group(0).split("/") if p][-1], text)


def test_between_runs_after_the_strip_and_before_the_last_redaction():
    seen = []

    def between(text):
        seen.append(text)
        return text + " tail"

    assert chat_redact("a​b", between=between) == "ab tail"
    assert seen == ["ab"]


def test_a_token_a_cut_path_glues_a_word_onto_comes_back_redacted():
    out = chat_redact(f"{GHP}/home/u/file", between=cut_paths)
    assert "ghp_" not in out and "[REDACTED:" in out
    glued = chat_redact(f"deploy/srv/app/{GHP} now", between=cut_paths)
    assert "ghp_" not in glued and "[REDACTED:" in glued


def test_a_value_the_cut_glues_onto_a_word_is_judged_as_sent():
    # The first pass sees a path; only the cut text puts the key after its name.
    out = chat_redact("aws_secret_access_key=/home/u/" + VALUE, between=cut_paths)
    assert VALUE not in out


def test_a_placeholder_inside_a_path_survives_the_cut():
    out = chat_redact(f"see /srv/app/{GHP}/x", between=cut_paths)
    assert "[REDACTED:" in out and "ghp_" not in out


@pytest.mark.parametrize("text", [
    "", "plain words", f"token {GHP} here", "a​b\x1bc", f"key={VALUE}",
    "4111 1111 1111 1111",
], ids=["empty", "plain", "token", "invisible", "keyed", "card"])
def test_between_none_is_the_redaction_as_before(text):
    assert chat_redact(text, between=None) == chat_redact(text)
