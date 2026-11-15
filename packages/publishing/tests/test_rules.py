"""The provenance rules: each shape, where it applies, and what it lets pass."""

from __future__ import annotations

import pytest

from moxi_publishing import rules

LINK = "https://claude" + ".ai/code/" + "session_01ab"


@pytest.mark.parametrize(("text", "rule"), [
    (LINK, "session-link"),
    ("0123abcd9" + "@" + "2026-01-02", "session-pointer"),
    ("[C" + "7]", "ruling-tag"),
    ("(R" + "12)", "ruling-tag"),
    ("§" + " 3", "ruling-tag"),
    ("design" + " v2", "ruling-tag"),
    ("MAJOR" + " 2", "ruling-tag"),
    ("rul" + "ed on " + "20" + "26-01", "ruling-tag"),
    ("round" + " proj-a/first-pass", "round-pointer"),
    ("mem" + " proj-a-note", "round-pointer"),
])
def test_each_file_rule(text, rule):
    assert [name for name, _ in rules.hits(f"x {text} y", rules.FILES)] == [rule]


@pytest.mark.parametrize("text", [
    "version 1.2.3", "on 2026-01-02 we met", "a lint code E501", "commit abc1234",
    "see section 3", "[x] done", "phone 555-0100",
])
def test_ordinary_text_passes(text):
    assert rules.hits(text, rules.FILES) == []
    assert rules.hits(text, rules.MESSAGES) == []


def test_message_only_rules_skip_files_and_back():
    digits = "1234567890" + "123"
    assert rules.hits(digits, rules.FILES) == []
    assert [n for n, _ in rules.hits(digits, rules.MESSAGES)] == ["message-id"]
    tag = "[C" + "7]"
    assert [n for n, _ in rules.hits(tag, rules.FILES)] == ["ruling-tag"]
    assert rules.hits(tag, rules.MESSAGES) == []


def test_only_the_named_rules_run():
    assert rules.hits(LINK, rules.MESSAGES, names=("message-id",)) == []


def test_every_rule_applies_somewhere():
    assert all(rules.applies(n, rules.FILES) or rules.applies(n, rules.MESSAGES)
               for n in rules.NAMES)
    assert len(rules.NAMES) == 6


# -- the ruling tag's start set -------------------------------------------------------------

def _starts(items, *, ignorecase: bool) -> set[str]:
    """Every character a match of the parsed pattern `items` can start with; fails on a
    construct this reader doesn't know, so a new branch can't slip past it."""
    import re
    from re import _constants as c

    def cased(ch: str) -> set[str]:
        if not ignorecase:
            return {ch}
        rx = re.compile(re.escape(ch), re.IGNORECASE)
        return {chr(x) for x in range(0x10000) if rx.fullmatch(chr(x))}

    for op, av in items:
        if op is c.AT:
            continue
        if op is c.LITERAL:
            return cased(chr(av))
        if op is c.IN:
            found = set()
            for kind, value in av:
                assert kind in (c.LITERAL, c.RANGE), (kind, value)
                low, high = (value, value) if kind is c.LITERAL else value
                for x in range(low, high + 1):
                    found |= cased(chr(x))
            return found
        if op is c.SUBPATTERN:
            _group, add, remove, inner = av
            on = (ignorecase or bool(add & c.SRE_FLAG_IGNORECASE)) and not (
                remove & c.SRE_FLAG_IGNORECASE)
            return _starts(inner, ignorecase=on)
        if op is c.BRANCH:
            return set().union(*(_starts(branch, ignorecase=ignorecase) for branch in av[1]))
        if op in (c.MAX_REPEAT, c.MIN_REPEAT) and av[0] >= 1:
            return _starts(av[2], ignorecase=ignorecase)
        raise AssertionError(f"a branch starts with {op}, which this reader can't follow")
    raise AssertionError("a branch can match empty")


def test_the_ruling_tags_start_set_is_its_branches_first_characters():
    from re import _parser

    assert _starts(_parser.parse(rules._RULING_TAG), ignorecase=False) == set(rules._RULING_TAG_START)


def test_the_lookahead_changes_no_match():
    import random
    import re

    plain, scan = re.compile(rules._RULING_TAG), re.compile(rules.RULING_TAG)
    # Joined at run time, so this file spells no tag whole.
    words = [("RU", "LE "), ("des", "ign v"), ("bui", "ld verif"), ("re-", "check v"),
             ("B4", " verification"), ("MIN", "OR "), ("Find", "ing 3 ("),
             ("pro", "be trial "), ("opera", "tor ruling"), ("opera", "tor, 20"),
             ("Rul", "ed on "), ("wo", "rd: "), ("rul", "ings "), ("20", "26-01")]
    pieces = (list("[]()|0123456789abcdefRSCFQNBIXrtv \u00a7\u27d0\u2691*-,.:")
              + [a + b for a, b in words] + ["\u2691*"])
    rng = random.Random(3)
    for _ in range(20000):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 12)))
        assert ([m.span() for m in plain.finditer(text)]
                == [m.span() for m in scan.finditer(text)]), text
