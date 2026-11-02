"""The frontmatter reader and the one write it makes: add a missing key as a line."""

from __future__ import annotations

import pytest

from moxi_notes import api
from moxi_notes import frontmatter as fm

FULL = """---
type: plan
project: alpha
description: the plan for alpha
tags: [planning, alpha]
updated: 2026-08-12
---

# body

text
"""


def test_a_full_header_reads_into_its_keys():
    header = api.frontmatter(FULL)
    assert header.present and header.ok and header.reason is None
    assert header.fields["type"] == "plan"
    assert header.fields["tags"] == ["planning", "alpha"]
    assert header.missing() == []
    assert header.unknown(fm.DEFAULT_REQUIRED) == []
    assert (header.start, header.end) == (0, 7)


def test_no_header_is_present_false_and_ok_true():
    header = api.frontmatter("# just a heading\n")
    assert (header.present, header.ok) == (False, True)
    assert "does not open with ---" in header.reason
    assert api.frontmatter("").present is False


def test_an_unclosed_fence_can_not_be_read_and_is_never_read_as_absent():
    header = api.frontmatter("---\ntype: plan\n\n# body\n")
    assert (header.present, header.ok) == (True, False)
    assert "never closed" in header.reason


@pytest.mark.parametrize("body,why", [
    ("type: a\ntype: b\n", "there twice"),
    ("  indented: x\n", "indented but follows no key"),
    ("not a key line\n", "is not `key: value`"),
])
def test_a_header_it_can_not_read_says_why(body, why):
    header = api.frontmatter(f"---\n{body}---\n")
    assert (header.present, header.ok) == (True, False)
    assert why in header.reason


def test_dots_close_a_header_too_and_crlf_reads_the_same():
    assert api.frontmatter("---\ntype: plan\n...\n").fields == {"type": "plan"}
    crlf = FULL.replace("\n", "\r\n")
    assert api.frontmatter(crlf).fields == api.frontmatter(FULL).fields


def test_block_scalars_literal_folded_and_kept():
    text = ("---\n"
            "description: |\n  first line\n  second line\n"
            "summary: >\n  one\n  two\n"
            "kept: |+\n  last\n"
            "type: plan\n---\n")
    fields = api.frontmatter(text).fields
    assert fields["description"] == "first line\nsecond line"
    assert fields["summary"] == "one two"
    assert fields["kept"] == "last\n"
    assert fields["type"] == "plan"


def test_a_block_sequence_is_a_list_and_a_nested_map_is_kept_as_lines():
    text = "---\ntags:\n  - alpha\n  - beta\n  -\nextra:\n  a: 1\n  b: 2\nempty:\n---\n"
    fields = api.frontmatter(text).fields
    assert fields["tags"] == ["alpha", "beta", ""]
    assert fields["extra"] == "a: 1\nb: 2"
    assert fields["empty"] == ""


def test_quotes_comments_and_flow_lists():
    header = api.frontmatter("---\n"
                             "description: fixes issue#12 today  # a comment\n"
                             "title: 'a, b'\n"
                             "said: \"x: y\"\n"
                             "own: 'it''s'\n"
                             "tags: ['x, y', z, []]\n"
                             "none: []\n"
                             "# a whole-line comment\n"
                             "---\n")
    assert header.fields == {"description": "fixes issue#12 today", "title": "a, b",
                             "said": "x: y", "own": "it's", "tags": ["x, y", "z", []],
                             "none": []}
    assert header.order == ["description", "title", "said", "own", "tags", "none"]


def test_missing_and_unknown_are_separate_answers():
    header = api.frontmatter("---\ntype: plan\nticket: 42\n---\n")
    assert header.missing() == ["project", "description", "tags", "updated"]
    assert header.missing(("type", "owner")) == ["owner"]
    assert header.unknown(fm.DEFAULT_REQUIRED) == ["ticket"]


# --- adding a key ------------------------------------------------------------------------

def test_upsert_makes_a_header_when_there_is_none():
    text, changed = api.upsert_frontmatter("# body\n", "type", "plan")
    assert changed and text == "---\ntype: plan\n---\n\n# body\n"
    assert api.upsert_frontmatter("", "type", "plan") == ("---\ntype: plan\n---\n", True)


def test_upsert_never_replaces_a_key_that_is_there():
    text = "---\ntype: journal\ndescription: |\n  one\n  two\n---\n"
    assert api.upsert_frontmatter(text, "type", "plan") == (text, False)
    assert api.upsert_frontmatter(text, "description", "short") == (text, False)


def test_upsert_twice_writes_once():
    once, first = api.upsert_frontmatter("# body\n", "type", "plan")
    twice, second = api.upsert_frontmatter(once, "type", "plan")
    assert (first, second) == (True, False) and once == twice


def test_upsert_lands_a_key_in_the_canonical_order():
    text = "---\ntype: plan\nupdated: 2026-08-12\n---\n\nbody\n"
    out, _ = api.upsert_frontmatter(text, "project", "alpha")
    assert api.frontmatter(out).order == ["type", "project", "updated"]
    out, _ = api.upsert_frontmatter(text, "owner", "sample", order=("type", "owner"))
    assert api.frontmatter(out).order == ["type", "owner", "updated"]


def test_upsert_lands_after_a_whole_block_scalar():
    text = "---\ntype: plan\ndescription: |\n  one\n  two\nupdated: 1\n---\n"
    out, _ = api.upsert_frontmatter(text, "tags", ["a"])
    header = api.frontmatter(out)
    assert header.order == ["type", "description", "tags", "updated"]
    assert header.fields["description"] == "one\ntwo"


def test_upsert_leaves_keys_it_does_not_own_where_they_are():
    text = "---\nticket: 42\nslug: x\n---\n"
    out, _ = api.upsert_frontmatter(text, "type", "plan")
    assert api.frontmatter(out).order == ["type", "ticket", "slug"]
    out, _ = api.upsert_frontmatter(text, "owner", "sample")
    assert api.frontmatter(out).order == ["ticket", "slug", "owner"]


def test_upsert_never_writes_into_a_header_it_can_not_read():
    broken = "---\ntype: plan\n\n# body with no closing fence\n"
    assert api.upsert_frontmatter(broken, "project", "alpha") == (broken, False)


def test_upsert_changes_no_other_byte():
    text = "---\r\ntype: plan\r\nupdated: 1\r\n---\r\nbody   line\r\nend"
    out, _ = api.upsert_frontmatter(text, "project", "alpha")
    assert out == "---\r\ntype: plan\r\nproject: alpha\r\nupdated: 1\r\n---\r\nbody   line\r\nend"
    bom = "﻿# body\n"
    out, _ = api.upsert_frontmatter(bom, "type", "plan")
    assert out == "﻿---\ntype: plan\n---\n\n# body\n"
    assert api.frontmatter(out).fields == {"type": "plan"}


def test_upsert_refuses_a_bad_key_or_a_value_on_two_lines():
    with pytest.raises(ValueError):
        api.upsert_frontmatter("x\n", "not a key", "v")
    with pytest.raises(ValueError):
        api.upsert_frontmatter("x\n", "type", "a\nb")


@pytest.mark.parametrize("value,line", [
    ("plain", "plain"), ("", "''"), (["a", "b"], "[a, b]"), ("has: colon", "'has: colon'"),
    ("#hash", "'#hash'"), ("it's", "it's"), ("'quoted'", "'''quoted'''"),
    ("a #b", "'a #b'"), ("a\t#b", "'a\t#b'"), ("issue#12", "issue#12"),
    ("2026-11-01", "2026-11-01"),
])
def test_a_written_value_reads_back_the_same(value, line):
    assert fm.render(value) == line
    text, _ = api.upsert_frontmatter("body\n", "description", value)
    assert api.frontmatter(text).fields["description"] == value
