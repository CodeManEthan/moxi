"""The exception mark the pronoun and machine-text steps read: its reason, its comment
syntax and inline code. The marks here name a made-up code, `XX9`, so neither step reads
them as its own."""

from __future__ import annotations

import pytest

from moxi_dev.marks import mark_patterns, marked_lines, unquoted

CODE = "XX9"


def kind(line: str) -> str:
    """"given", "bare" or "" for one line."""
    marked, bare = marked_lines(line, CODE)
    return "given" if marked else "bare" if bare else ""


@pytest.mark.parametrize("line", [
    "x = 1  # noqa: XX9 -- a sample",
    "x = 1  # noqa: MX5, XX9 -- a sample",
    "x = 1  # noqa: XX9, MX5 -- a sample",
    "const p = 1;  // noqa: XX9 -- the macOS sample",
    "const p = 1;  /* noqa: XX9 -- the macOS sample */",
    "a line <!-- noqa: XX9 -- written for Windows -->",
    "a line <!--noqa: XX9 -- Linux-->",
    "text (noqa: XX9 -- a docstring's inner line)",
    "x = 1  # noqa: XX9 -- é",
    "x = 1  # noqa: XX9 -- 2",
    "x = 1  # noqa: XX9 -- quotes `Row.label`",
    "x = 1  # noqa: XX9 -- `Row.label`",
    "x = 1  # noqa: XX9 -- a sample\r",
])
def test_a_mark_with_a_reason_is_given(line):
    assert kind(line) == "given"


@pytest.mark.parametrize("line", [
    "x = 1  # noqa: XX9",
    "x = 1  # noqa: XX9 --",
    "x = 1  # noqa: XX9 --   ",
    "x = 1  # noqa: XX9 --\r",
    "a line <!-- noqa: XX9 -- -->",
    "a line <!-- noqa: XX9 -->",
    "const p = 1;  /* noqa: XX9 -- */",
    "x = 1  # noqa: XX9 -- ...",
    "a line <!-- noqa: XX9 -- --> and text after",
])
def test_a_mark_with_no_reason_is_bare(line):
    assert kind(line) == "bare"


@pytest.mark.parametrize("line", [
    "x = 1  # noqa: XX90 -- another code",
    "x = 1  # noqa: AXX9 -- another code",
    "x = 1  # noqa: MX5 -- another code",
    "x = 1  # XX9 -- no noqa",
])
def test_a_mark_for_another_code_is_none(line):
    assert kind(line) == ""


@pytest.mark.parametrize("line", [
    "A genuine use carries `noqa: XX9 -- <why>`.",
    "Write `# noqa: XX9 -- <why>` or ``noqa: XX9 -- `x` `` on the line.",
    "see `a` then `# noqa: XX9 -- a sample`",
])
def test_a_mark_in_inline_code_is_an_example(line):
    assert kind(line) == ""


@pytest.mark.parametrize("line", [
    "Run `a`. <!-- noqa: XX9 -- written for macOS --> Then `b`.",
    "<!-- noqa: XX9 -- written for macOS --> see `b`",
    "`a` <!-- noqa: XX9 -- Linux --> `b` and `c`",
    "(`C:\\Users\\a`, noqa: XX9 -- a Windows runner) and `b`",
    "``a ` b`` <!-- noqa: XX9 -- Windows --> `c`",
])
def test_a_mark_beside_inline_code_is_a_mark(line):
    assert kind(line) == "given"


def test_inline_code_pairs_as_markdown_pairs_it():
    assert unquoted("`a` x `noqa: b` y `c`") == "`a` x  y `c`"
    assert unquoted("``a `noqa:` b`` x") == " x"
    assert unquoted("an odd ` backtick, noqa: x") == "an odd ` backtick, noqa: x"


def test_lines_are_numbered_by_newlines_alone():
    text = "a\x0cb\r\nx  # noqa: XX9 -- a sample\r\ny  # noqa: XX9\n"
    assert marked_lines(text, CODE) == ({2}, {3})


def test_the_patterns_name_the_code_given():
    given, named = mark_patterns(CODE)
    assert given.search("# noqa: XX9 -- why") and not given.search("# noqa: XX9")
    assert named.search("# noqa: XX9") and not named.search("# noqa: MX1 -- why")
