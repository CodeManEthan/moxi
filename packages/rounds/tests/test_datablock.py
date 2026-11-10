"""The data-block reader: which block of a file is read, what is refused with its line,
where each table and key is said to start, and the empty block of a schema."""

from __future__ import annotations

import hashlib
import re
import textwrap
import tomllib

import pytest

from moxi import exit as exits
from moxi_rounds import datablock
from moxi_rounds.datablock import Table, count, flag, ident, idents, line, word
from moxi_rounds.errors import BadBlock

WHAT = "a test's data block"

ITEM = Table("item", "one for each item", (
    ident("id", re.compile(r"T[0-9]+"), "T<n>", "T<n>"),
    word("size", ("small", "large"), "how big it is"),
    count("minutes", "how long it takes, in minutes", required=False),
    flag("done", "whether it is done", required=False),
    idents("after", re.compile(r"T[0-9]+"), "T<n>", "the items before it"),
    line("note", "anything else", required=False),
), (Table("part", "one for each part", (line("what", "what the part is"),)),
    Table("owner", "who has it", (line("name", "their name"),), many=False)))
SCHEMA = Table("", keys=(line("title", "what the list is"),), tables=(ITEM,))


@pytest.fixture
def write(tmp_path):
    """`write(text)`: write `text`, dedented, as a markdown file; returns its path."""

    def put(text: str, name: str = "report.md"):
        path = tmp_path / name
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
        return path

    return put


def line_with(path, needle: str, nth: int = 1) -> int:
    """The number of the `nth` line of the file that holds `needle`."""
    hits = [number for number, text in enumerate(path.read_text().split("\n"), 1)
            if needle in text]
    return hits[nth - 1]


def refused(path) -> BadBlock:
    with pytest.raises(BadBlock) as caught:
        datablock.find(path, WHAT)
    return caught.value


def test_the_block_is_read_from_a_file_with_other_fenced_blocks_around_it(write):
    path = write("""
        # A report

        Some prose, and code that is not the block:

        ```python
        title = "not this"
        ```

        ```toml
        title = "nor this"
        ```

        ```toml rounds
        title = "this one"

        [[item]]
        id = "T1"
        ```

        ```text
        title = "nor this either"
        ```
    """)
    found = datablock.find(path, WHAT)
    assert found.data == {"title": "this one", "item": [{"id": "T1"}]}
    assert found.file == path
    assert found.line == line_with(path, "toml rounds")
    assert found.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_nothing_outside_the_block_is_read(write):
    path = write("""
        title = "prose that looks like TOML"
        [[[ and prose that is not TOML at all

        ```toml rounds
        title = "inside"
        ```
    """)
    assert datablock.find(path, WHAT).data == {"title": "inside"}


@pytest.mark.parametrize("fence", ["~~~toml rounds", "````toml rounds", "```  toml   rounds  "])
def test_a_fence_of_tildes_or_more_marks_or_spaced_words_opens_a_block(write, fence):
    marks = re.match(r"[`~]+", fence)[0]
    path = write(f"""
        {fence}
        title = "inside"
        {marks}
    """)
    assert datablock.find(path, WHAT).data == {"title": "inside"}


def test_a_block_under_an_indented_fence_is_read_without_the_indent(write):
    path = write("""
        prose

          ```toml rounds
          title = '''
          two
          lines'''
          ```
    """)
    assert datablock.find(path, WHAT).data == {"title": "two\nlines"}


def test_a_file_with_carriage_returns_is_read_and_its_lines_counted_the_same(write, tmp_path):
    path = tmp_path / "report.md"
    path.write_bytes(b"prose\r\n\r\n```toml rounds\r\ntitle = \"inside\"\r\nsize = 1\r\n```\r\n")
    found = datablock.find(path, WHAT)
    assert found.data == {"title": "inside", "size": 1}
    assert found.line == 3
    assert found.line_of("size") == 5


def test_a_file_with_no_block_is_refused_by_name(write):
    path = write("""
        # A report

        ```python
        title = "not this"
        ```
    """)
    error = refused(path)
    assert error.file == path
    assert error.exit_code == exits.REFUSED
    [problem] = error.problems
    assert problem.line is None
    assert "holds no data block" in problem.what
    assert datablock.OPENS in problem.fix
    assert str(path) in error.reason
    assert WHAT in error.reason


@pytest.mark.parametrize("info", ["toml", "toml round", "rounds", "toml-rounds", "TOML rounds x"])
def test_a_file_whose_block_is_almost_named_right_is_told_which_line(write, info):
    path = write(f"""
        prose

        ```{info}
        title = "meant to be the block"
        ```
    """)
    [problem] = refused(path).problems
    assert problem.line == 3
    assert "holds no data block" in problem.what
    assert repr(info) in problem.what


def test_a_file_with_two_blocks_is_refused_at_the_second(write):
    path = write("""
        ```toml rounds
        title = "one"
        ```

        prose

        ```toml rounds
        title = "two"
        ```
    """)
    [problem] = refused(path).problems
    assert problem.line == line_with(path, "toml rounds", 2) == 7
    assert "line 1" in problem.what
    assert "a file carries one" in problem.what


def test_a_block_shown_inside_a_longer_fence_is_text_and_not_a_block(write):
    path = write("""
        How a block is written:

        ````markdown
        ```toml rounds
        title = "an example"
        ```
        ````

        ```toml rounds
        title = "the block"
        ```
    """)
    found = datablock.find(path, WHAT)
    assert found.data == {"title": "the block"}
    assert found.line == 9


def test_a_file_whose_only_block_is_inside_another_fence_has_none(write):
    path = write("""
        ~~~text
        ```toml rounds
        title = "an example"
        ```
        ~~~
    """)
    [problem] = refused(path).problems
    assert "holds no data block" in problem.what


def test_a_block_that_is_never_closed_is_refused_at_its_opening(write):
    path = write("""
        prose

        ```toml rounds
        title = "inside"
    """)
    [problem] = refused(path).problems
    assert problem.line == 3
    assert "never closed" in problem.what


@pytest.mark.parametrize(("bad", "says"), [
    ("size = large", "Invalid value"),
    ('title = "said twice"', "overwrite"),
    ("[[item]", "Expected"),
])
def test_a_block_that_is_not_toml_is_refused_at_the_line_of_the_file(write, bad, says):
    path = write(f"""
        prose
        more prose

        ```toml rounds
        title = "inside"

        {bad}
        ```
    """)
    [problem] = refused(path).problems
    assert problem.line == line_with(path, bad) == 7
    assert "not TOML" in problem.what
    assert says in problem.what
    assert "at line" not in problem.what


def test_a_block_whose_toml_ends_too_soon_is_refused_at_a_line_of_the_block(write):
    path = write("""
        ```toml rounds
        title = "inside"
        after = [
        ```
    """)
    [problem] = refused(path).problems
    assert problem.line in (3, 4)
    assert "not TOML" in problem.what


def test_a_file_that_is_not_there_or_not_text_is_refused(tmp_path):
    missing = refused(tmp_path / "none.md")
    assert missing.file == tmp_path / "none.md"
    assert "can't be read" in missing.problems[0].what
    binary = tmp_path / "binary.md"
    binary.write_bytes(b"\xff\xfe```toml rounds\n```\n")
    assert "not UTF-8" in refused(binary).problems[0].what


def test_each_table_and_key_is_placed_on_its_line_of_the_file(write):
    path = write("""
        prose

        ```toml rounds
        # a comment
        title = "inside"   # with a comment

        [[item]]
        id = "T1"
        after = [
          "T2",
          "T3",
        ]
        size = "small"

          [[item.part]]
          what = "first"

          [[item.part]]
          what = "second"

          [item.owner]
          name = "alice"

        [[item]]
        id = "T2"

          [[item.part]]
          what = "third"
        ```
    """)
    found = datablock.find(path, WHAT)
    assert found.line == 3
    assert found.line_of("title") == 5
    assert found.line_of("item", 0) == 7
    assert found.line_of("item", 0, "id") == 8
    assert found.line_of("item", 0, "after") == 9
    assert found.line_of("item", 0, "size") == 13
    assert found.line_of("item", 0, "part", 0) == 15
    assert found.line_of("item", 0, "part", 1) == 18
    assert found.line_of("item", 0, "part", 1, "what") == 19
    assert found.line_of("item", 0, "owner") == 21
    assert found.line_of("item", 0, "owner", "name") == 22
    assert found.line_of("item", 1) == 24
    assert found.line_of("item", 1, "id") == 25
    assert found.line_of("item", 1, "part", 0, "what") == 28
    assert found.lines(("item", 1), found.data["item"][1]) == {"id": 25, "part": 27}


def test_text_inside_a_string_of_many_lines_starts_no_table_and_no_key(write):
    path = write('''
        ```toml rounds
        title = """
        [[item]]
        id = "T9"
        a quote " and an escaped \\""" end"""
        note = \'\'\'
        [[item]]
        size = "large"\'\'\'

        [[item]]
        id = "T1"
        ```
    ''')
    found = datablock.find(path, WHAT)
    assert [one["id"] for one in found.data["item"]] == ["T1"]
    assert found.line_of("title") == 2
    assert found.line_of("note") == 6
    assert found.line_of("item", 0) == 10
    assert found.line_of("item", 0, "id") == 11
    assert ("item", 1) not in found.places


def test_what_is_written_inline_is_placed_on_the_line_around_it(write):
    path = write("""
        ```toml rounds
        title = "inside"
        item = [{ id = "T1", owner = { name = "alice" } }]
        ```
    """)
    found = datablock.find(path, WHAT)
    assert found.line_of("item") == 3
    assert found.line_of("item", 0, "id") == 3
    assert found.line_of("item", 0, "owner", "name") == 3
    assert found.line_of("nothing", "here") == found.line == 1


def problems_of(path):
    return datablock.check(datablock.find(path, WHAT), SCHEMA)


def test_a_block_that_fits_its_schema_has_no_problem(write):
    path = write("""
        ```toml rounds
        title = "a list"

        [[item]]
        id = "T1"
        size = "small"
        minutes = 5
        done = false
        after = ["T2"]
        note = ""

          [[item.part]]
          what = "one"

          [item.owner]
          name = "alice"
        ```
    """)
    assert problems_of(path) == []


def test_a_key_the_schema_does_not_have_is_refused_on_its_line(write):
    path = write("""
        ```toml rounds
        title = "a list"
        colour = "red"

        [[item]]
        id = "T1"
        size = "small"
        weight = 3
        ```
    """)
    top, inner = problems_of(path)
    assert (top.line, inner.line) == (3, 8)
    assert top.what == "`colour` is not a key of the block"
    assert "title, item" in top.fix
    assert inner.what == "`weight` is not a key of this `[[item]]`"
    assert "id, size, minutes, done, after, note, part, owner" in inner.fix


def test_a_key_a_table_needs_is_asked_for_on_the_tables_line(write):
    path = write("""
        ```toml rounds
        [[item]]
        id = "T1"

          [[item.part]]
        ```
    """)
    title, size, what = problems_of(path)
    assert (title.line, title.what) == (1, "the block has no `title`")
    assert (size.line, size.what) == (2, "this `[[item]]` has no `size`")
    assert size.fix == 'add size = one of "small", "large"'
    assert (what.line, what.what) == (5, "this `[[item.part]]` has no `what`")


@pytest.mark.parametrize(("bad", "says"), [
    ('id = "X1"', '`id` is "X1", which is not T<n>'),
    ("id = 1", "`id` is 1, which is not T<n>"),
    ('size = "huge"', '`size` is "huge", which is none of small | large'),
    ("size = true", "`size` is true, which is none of small | large"),
    ("minutes = 0", "`minutes` is 0, not a whole number from 1 up"),
    ("minutes = true", "`minutes` is true, not a whole number from 1 up"),
    ('minutes = "5"', '`minutes` is "5", not a whole number from 1 up'),
    ("minutes = 1.5", "`minutes` is 1.5, not a whole number from 1 up"),
    ('done = "no"', '`done` is "no", not true or false'),
    ("done = 0", "`done` is 0, not true or false"),
    ('after = "T2"', '`after` is "T2", not a list'),
    ('after = ["T2", "x"]', '`after` holds "x", which is not T<n>'),
    ('after = ["T2", "T2"]', "`after` names T2 twice"),
    ("note = 3", "`note` is 3, not text"),
    ('note = "two\\nlines"', "`note` has a line break, and it is one line"),
    ('owner = "alice"', '`owner` is "alice", not a table'),
    ('part = ["one"]', '`part` is ["one"], not a table'),
    ('part = { what = "one" }', "`part` is a table, not a table"),
])
def test_a_wrong_value_is_refused_on_the_line_of_its_key(write, bad, says):
    rows = {"id": 'id = "T1"', "size": 'size = "small"'}
    rows[bad.split()[0]] = bad
    body = "\n".join(rows.values())
    path = write(f'```toml rounds\ntitle = "a list"\n\n[[item]]\n{body}\n```\n')
    [problem] = problems_of(path)
    assert problem.what == says
    assert problem.line == line_with(path, bad)
    assert problem.fix


def test_a_text_a_table_needs_says_something(write):
    path = write("""
        ```toml rounds
        title = "  "
        ```
    """)
    [problem] = problems_of(path)
    assert (problem.line, problem.what) == (2, "`title` says nothing")


def test_every_problem_of_a_block_is_given_and_a_refusal_lists_them_by_line(write):
    path = write("""
        ```toml rounds
        colour = "red"

        [[item]]
        id = "X"
        size = "huge"
        ```
    """)
    problems = problems_of(path)
    assert sorted(one.line for one in problems) == [1, 2, 5, 6]
    error = BadBlock(path, WHAT, problems)
    assert [one.line for one in error.problems] == [1, 2, 5, 6]
    lines = error.reason.split("\n")
    assert lines[0].endswith(f"can't be read as {WHAT}:")
    assert [text.split(":")[0] for text in lines[1::2]] == [
        "  - line 1", "  - line 2", "  - line 5", "  - line 6"]
    assert all(text.startswith("    fix: ") for text in lines[2::2])


def test_the_empty_block_has_every_key_once_with_one_comment_and_is_toml():
    text = datablock.empty(SCHEMA)
    lines = text.split("\n")
    assert lines[0] == datablock.OPENS
    assert lines[-2:] == [datablock.CLOSES, ""]
    rows = [row.strip() for row in lines[1:-2] if row.strip()]
    assert [row.split("#")[0].strip() for row in rows] == [
        'title = ""', "[[item]]", 'id = ""', 'size = ""', "minutes = 0", "done = false",
        "after = []", 'note = ""', "[[item.part]]", 'what = ""', "[item.owner]",
        'name = ""']
    assert all(row.count("#") == 1 and row.split("#")[1].strip() for row in rows)
    comments = {row.split("=")[0].strip(): row.split("# ")[1] for row in rows if "=" in row}
    assert comments["size"] == "small | large; how big it is"
    assert comments["minutes"] == "optional: how long it takes, in minutes"
    assert comments["id"] == "T<n>"
    read = tomllib.loads("\n".join(lines[1:-2]))
    assert read == {"title": "", "item": [{
        "id": "", "size": "", "minutes": 0, "done": False, "after": [], "note": "",
        "part": [{"what": ""}], "owner": {"name": ""}}]}


def test_the_empty_block_is_read_back_as_the_files_one_block(write):
    path = write("A report.\n\n" + datablock.empty(SCHEMA) + "\nMore prose.\n")
    found = datablock.find(path, WHAT)
    assert found.line == 3
    assert found.data["item"][0]["owner"] == {"name": ""}
