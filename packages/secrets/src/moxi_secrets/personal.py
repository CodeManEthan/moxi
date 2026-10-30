"""Personal data: payment cards, IBANs and dashed US SSNs.

python-stdnum validates a string but does not find one, so each format is a candidate
pattern here plus stdnum's validator. Each pattern is one pass with no lookaround; the
"no digit next to it" checks run in code on each match. Every candidate has a bounded
length and a small fixed number of readings, so the pass stays linear on RE2 and on
Python's re alike.

stdnum is the optional extra `moxi-secrets[personal-data]`, imported at first use.
"""

from __future__ import annotations

from . import engine as _engine
from .scan import SetUnavailable

INSTALL = "install the extra moxi-secrets[personal-data] (python-stdnum)"

#: A chain of digit groups joined by single spaces or hyphens; card candidates are
#: windows of its groups (see `_cards`).
_CHAIN = r"[0-9]+(?:[ \-][0-9]+)*"
#: Two letters, two digits, then 11-30 letters or digits, or two to seven groups of four
#: each led by a space, then at most one group of one to three led by a space.
_IBAN = (r"[A-Za-z]{2}[0-9]{2}(?:[A-Za-z0-9]{11,30}|(?: [A-Za-z0-9]{4}){2,7}"
         r"(?: [A-Za-z0-9]{1,3})?)")
_SSN = r"[0-9]{3}-[0-9]{2}-[0-9]{4}"

#: Card shapes written in groups: the group lengths, the last one possibly shorter.
_GROUPED = ((4, 4, 4, 1), (4, 4, 4, 2), (4, 4, 4, 3), (4, 4, 4, 4),
            (4, 4, 4, 4, 1), (4, 4, 4, 4, 2), (4, 4, 4, 4, 3),
            (4, 6, 5), (4, 6, 4))
_WIDEST = max(len(s) for s in _GROUPED)

_stdnum = None


def _validators():
    global _stdnum
    if _stdnum is None:
        try:
            from stdnum import iban, luhn
            from stdnum.us import ssn
        except ImportError:
            raise SetUnavailable(f"the personal-data set needs python-stdnum: {INSTALL}") \
                from None
        _stdnum = (luhn.is_valid, iban.is_valid, ssn.is_valid)
    return _stdnum


def available() -> bool:
    """True when stdnum is installed. Imports nothing."""
    from importlib.util import find_spec

    try:
        return find_spec("stdnum") is not None
    except (ImportError, ValueError):
        return False


def issuer(digits: str) -> bool:
    """True when the number starts with a card issuer's prefix."""
    if digits[0] == "4":
        return True
    two, four = int(digits[:2]), int(digits[:4])
    return (51 <= two <= 55 or 2221 <= four <= 2720 or two in (34, 37, 65, 35, 30, 36, 38)
            or four == 6011)


def _is_card(digits: str, luhn_ok) -> bool:
    return issuer(digits) and luhn_ok(digits)


def _digit(text, i) -> bool:
    return 0 <= i < len(text) and "0" <= text[i] <= "9"


def _alnum(text, i) -> bool:
    return 0 <= i < len(text) and text[i].isascii() and text[i].isalnum()


def _cards(t, eng, luhn_ok, out) -> None:
    etext = t.etext
    rx = eng.compile(_CHAIN)
    target = eng.target(t)
    for m in rx.finditer(target):
        s, e = eng.to_str(t, m.start()), eng.to_str(t, m.end())
        chain = etext[s:e]
        # The groups of the chain, as (start, end) in the text.
        groups, at = [], s
        for part in chain.replace("-", " ").split(" "):
            groups.append((at, at + len(part)))
            at += len(part) + 1
        sizes = [b - a for a, b in groups]
        for i, (gs, ge) in enumerate(groups):
            if sizes[i] in (15, 16):
                digits = etext[gs:ge]
                if _is_card(digits, luhn_ok):
                    out.append(("personal-data", "payment-card", "a payment card number",
                                gs, ge))
            if sizes[i] != 4:
                continue
            window = tuple(sizes[i:i + _WIDEST])
            for shape in _GROUPED:
                if window[:len(shape)] != shape:
                    continue
                end = groups[i + len(shape) - 1][1]
                digits = "".join(etext[a:b] for a, b in groups[i:i + len(shape)])
                if _is_card(digits, luhn_ok):
                    out.append(("personal-data", "payment-card", "a payment card number",
                                gs, end))


def _ibans(t, eng, iban_ok, out) -> None:
    etext = t.etext
    rx = eng.compile(_IBAN)
    for m in rx.finditer(eng.target(t)):
        s, e = eng.to_str(t, m.start()), eng.to_str(t, m.end())
        if _alnum(etext, s - 1):
            continue
        text = etext[s:e]
        # Readings, longest first: the whole match, then without each trailing group.
        ends = [e]
        if " " in text:
            cut = e
            while True:
                cut = etext.rfind(" ", s, cut)
                if cut < s + 4 + 10:   # at least two groups of four stay
                    break
                ends.append(cut)
        for end in ends:
            if _alnum(etext, end):
                continue
            if iban_ok(etext[s:end].replace(" ", "")):
                out.append(("personal-data", "iban", "an IBAN", s, end))
                break


def _ssns(t, eng, ssn_ok, out) -> None:
    etext = t.etext
    rx = eng.compile(_SSN)
    for m in rx.finditer(eng.target(t)):
        s, e = eng.to_str(t, m.start()), eng.to_str(t, m.end())
        if _digit(etext, s - 1) or _digit(etext, e):
            continue
        if ssn_ok(etext[s:e]):
            out.append(("personal-data", "us-ssn", "a US social security number", s, e))


def spans(t) -> list:
    """The personal-data set's hits on `t`, as (set, rule, description, start, end)."""
    luhn_ok, iban_ok, ssn_ok = _validators()
    eng = _engine.active()
    out: list = []
    _cards(t, eng, luhn_ok, out)
    _ibans(t, eng, iban_ok, out)
    _ssns(t, eng, ssn_ok, out)
    return out
