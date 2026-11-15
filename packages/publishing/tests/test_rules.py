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
