"""A design's data block: every key it takes, what it gives a caller with the line of
each entry, what it refuses on the block alone, and its empty block."""

from __future__ import annotations

import textwrap

import pytest

from moxi_rounds import design_block
from moxi_rounds.errors import BadBlock

WHOLE = """
# A design

Prose about the design, with a block that is not the data:

```toml
[[question]]
id = "Q99"
```

```toml rounds
[[question]]
id = "Q1"
text = "Should search read archived notes too?"
blocks = ["B2"]
options = { a = "yes, all notes", b = "no, live notes only", c = "ask each time" }

  [question.prepared]
  recommend = "b"
  by = "scout"
  model = "a-model"
  sure = "medium"
  why = "archived notes are a tenth of the folder"
  flips = "a reader who asks for an archived note by name"
  read = ["file:design.md#search", "comms:1234567"]

    [[question.prepared.option]]
    key = "a"
    costs = "a slower first search"
    forecloses = "nothing"

    [[question.prepared.option]]
    key = "b"
    costs = "an archived note is never found"
    forecloses = "nothing"

    [[question.prepared.option]]
    key = "c"
    text = "ask each time"
    costs = "a prompt on every search"
    forecloses = "a search with no prompt"

[[question]]
id = "Q2"
text = "Is the index kept between runs?"
blocks = "none"

[[question]]
id = "Q3"
text = "Does a search follow links?"

[[stage]]
id = "B1"
repo = "proj-a"
branch = "search-index"
after = []
why = ""

  [[stage.slice]]
  id = "1a"
  what = "the index and its reader"
  minutes = 40

  [[stage.slice]]
  id = "1b"
  what = "the search verb"
  minutes = 30

[[stage]]
id = "B2"
repo = "proj-a"
branch = "search-links"
after = ["B1"]
why = "links can wait for the index to be in use"

[[stage]]
id = "B3"
round = "proj-b/search-page"
after = ["B1", "B2"]

[[trace]]
id = "S12"
as = "carried"
where = "the index"
record = ""
stages = []

[[trace]]
id = "C3"
as = "amended"
where = "the search verb"
record = "comms:1234567"
stages = ["B1"]

[[trace]]
id = "R1"
as = "cut"
question = "Q3"

[[commitment]]
id = "R1"
text = "A search never reads a note that was deleted."
where = "the index"

[[commitment]]
id = "R2"
text = "A search writes nothing."
```

More prose.
"""


@pytest.fixture
def design(tmp_path):
    """`design(text)`: write `text` as a design file; returns its path."""

    def put(text: str):
        path = tmp_path / "design.md"
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
        return path

    return put


@pytest.fixture
def block(design):
    """`block(body)`: a design file whose data block holds `body`, its first line the
    file's third."""
    return lambda body: design("# A design\n\n```toml rounds\n"
                               + textwrap.dedent(body).strip("\n") + "\n```\n")


def line_with(path, needle: str, nth: int = 1) -> int:
    hits = [number for number, text in enumerate(path.read_text().split("\n"), 1)
            if needle in text]
    return hits[nth - 1]


def problems(path) -> list:
    with pytest.raises(BadBlock) as caught:
        design_block.read(path)
    assert caught.value.file == path
    assert caught.value.what == design_block.WHAT
    return list(caught.value.problems)


def test_every_key_of_a_design_block_is_read(design):
    path = design(WHOLE)
    read = design_block.read(path)
    assert read.file == path
    assert read.line == line_with(path, "toml rounds")
    assert len(read.sha256) == 64

    first, second, third = read.questions
    assert (first.id, first.text) == ("Q1", "Should search read archived notes too?")
    assert first.blocks == ("B2",)
    assert first.options == (("a", "yes, all notes"), ("b", "no, live notes only"),
                             ("c", "ask each time"))
    prepared = first.prepared
    assert (prepared.recommend, prepared.by, prepared.model) == ("b", "scout", "a-model")
    assert (prepared.sure, prepared.why) == (
        "medium", "archived notes are a tenth of the folder")
    assert prepared.flips == "a reader who asks for an archived note by name"
    assert prepared.read == ("file:design.md#search", "comms:1234567")
    assert [one.key for one in prepared.options] == ["a", "b", "c"]
    assert [one.text for one in prepared.options] == [None, None, "ask each time"]
    assert prepared.options[0].costs == "a slower first search"
    assert prepared.options[2].forecloses == "a search with no prompt"
    assert (second.blocks, second.options, second.prepared) == ("none", None, None)
    assert third.blocks == "all"

    build, later, child = read.stages
    assert (build.id, build.repo, build.branch, build.round) == (
        "B1", "proj-a", "search-index", None)
    assert (build.after, build.why) == ((), "")
    assert [(one.id, one.what, one.minutes) for one in build.slices] == [
        ("1a", "the index and its reader", 40), ("1b", "the search verb", 30)]
    assert (later.after, later.slices) == (("B1",), ())
    assert later.why == "links can wait for the index to be in use"
    assert (child.id, child.repo, child.branch, child.round) == (
        "B3", None, None, "proj-b/search-page")
    assert child.after == ("B1", "B2")

    carried, amended, cut = read.traces
    assert (carried.id, carried.how, carried.where) == ("S12", "carried", "the index")
    assert (carried.record, carried.question, carried.stages) == ("", None, ())
    assert (amended.id, amended.how, amended.record, amended.stages) == (
        "C3", "amended", "comms:1234567", ("B1",))
    assert (cut.id, cut.how, cut.where, cut.question) == ("R1", "cut", "", "Q3")

    kept, other = read.commitments
    assert (kept.id, kept.text, kept.where) == (
        "R1", "A search never reads a note that was deleted.", "the index")
    assert (other.id, other.where) == ("R2", "")


def test_each_entry_says_the_line_of_the_file_it_came_from(design):
    path = design(WHOLE)
    read = design_block.read(path)
    first = read.questions[0]
    assert first.line == line_with(path, "[[question]]", 2)
    assert first.line_of("blocks") == line_with(path, 'blocks = ["B2"]')
    assert first.line_of("no such key") == first.line
    assert first.prepared.line == line_with(path, "[question.prepared]")
    assert first.prepared.line_of("sure") == line_with(path, "sure =")
    assert first.prepared.options[2].line == line_with(path, "[[question.prepared.option]]", 3)
    assert first.prepared.options[2].line_of("text") == line_with(path, 'text = "ask each time"')
    assert read.questions[2].line == line_with(path, "[[question]]", 4)
    child = read.stages[2]
    assert child.line == line_with(path, "[[stage]]", 3)
    assert child.line_of("round") == line_with(path, "round =")
    assert read.stages[0].slices[1].line == line_with(path, "[[stage.slice]]", 2)
    assert read.stages[0].slices[1].line_of("minutes") == line_with(path, "minutes = 30")
    assert read.traces[1].line_of("record") == line_with(path, 'record = "comms:1234567"')
    assert read.commitments[1].line == line_with(path, "[[commitment]]", 2)


def test_a_block_that_declares_nothing_is_read_as_empty(block):
    read = design_block.read(block(""))
    assert (read.questions, read.stages, read.traces, read.commitments) == ((), (), (), ())


def test_a_block_may_give_a_questions_options_as_a_table_of_its_own(block):
    read = design_block.read(block("""
        [[question]]
        id = "Q1"
        text = "Which?"

          [question.options]
          a = "this"
          b = "that"
    """))
    assert read.questions[0].options == (("a", "this"), ("b", "that"))


QUESTION = {"id": 'id = "Q1"', "text": 'text = "Which?"'}
STAGE = {"id": 'id = "B1"', "repo": 'repo = "proj-a"', "branch": 'branch = "search"'}
TRACE = {"id": 'id = "S1"', "as": 'as = "carried"'}
COMMITMENT = {"id": 'id = "R1"', "text": 'text = "It writes nothing."'}
SLICE = {"id": 'id = "1a"', "what": 'what = "the index"', "minutes": "minutes = 40"}
PREPARED = {
    "recommend": 'recommend = "a"', "by": 'by = "scout"', "model": 'model = "a-model"',
    "sure": 'sure = "low"', "why": 'why = "it is small"', "flips": 'flips = "a big one"',
    "read": 'read = ["comms:1234567"]'}
OPTION = {"key": 'key = "c"', "costs": 'costs = "time"', "forecloses": 'forecloses = "nothing"'}
OPTIONS = ('\n[[question.prepared.option]]\nkey = "a"\ncosts = "time"\nforecloses = "nothing"\n'
           '\n[[question.prepared.option]]\nkey = "b"\ncosts = "time"\nforecloses = "nothing"\n')


def entry(header: str, rows: dict, change: str | None, before: str = "", after: str = "") -> str:
    """A block with one entry under `header`: `rows`, with the row of `change`'s key
    replaced by `change`, or the row left out for a `change` of `-key`."""
    rows = dict(rows)
    if change and change.startswith("-"):
        del rows[change[1:]]
    elif change:
        rows[change.split()[0]] = change
    return f"{before}\n{header}\n" + "\n".join(rows.values()) + f"\n{after}"


WRONG_VALUES = [
    ("[[question]]", QUESTION, 'id = "P1"', '`id` is "P1", which is not Q<n>'),
    ("[[question]]", QUESTION, 'id = "Q0"', '`id` is "Q0", which is not Q<n>'),
    ("[[question]]", QUESTION, "id = 1", "`id` is 1, which is not Q<n>"),
    ("[[question]]", QUESTION, 'text = ""', "`text` says nothing"),
    ("[[question]]", QUESTION, 'text = "one\\ntwo"', "`text` has a line break"),
    ("[[question]]", QUESTION, "blocks = []", "`blocks` is an empty list, which could mean"),
    ("[[question]]", QUESTION, 'blocks = "all"', '`blocks` is "all"; as text it is only "none"'),
    ("[[question]]", QUESTION, 'blocks = "B1"', '`blocks` is "B1"; as text it is only "none"'),
    ("[[question]]", QUESTION, 'blocks = ["B1", "Q2"]', '`blocks` holds "Q2", which is not B<n>'),
    ("[[question]]", QUESTION, 'blocks = ["B1", "B1"]', "`blocks` names B1 twice"),
    ("[[question]]", QUESTION, "blocks = 2", "`blocks` is 2, not a list"),
    ("[[question]]", QUESTION, 'options = ["a", "b"]', "not a table of choices"),
    ("[[question]]", QUESTION, 'options = { a = "only one" }', "`options` holds 1, and a question that has options has 2 to 4"),
    ("[[question]]", QUESTION, "options = {}", "`options` holds 0"),
    ("[[question]]", QUESTION, 'options = { a = "1", b = "2", c = "3", d = "4", e = "5" }', "`options` holds 5"),
    ("[[question]]", QUESTION, 'options = { A = "1", b = "2" }', '`options` has the key "A"'),
    ("[[question]]", QUESTION, 'options = { a = 1, b = "2" }', "`options` gives a 1, and an option is one line"),
    ("[[question]]", QUESTION, 'options = { a = "", b = "2" }', '`options` gives a ""'),
    ("[[stage]]", STAGE, 'id = "S1"', '`id` is "S1", which is not B<n>'),
    ("[[stage]]", STAGE, 'repo = ""', '`repo` is "", not one word'),
    ("[[stage]]", STAGE, 'repo = "two words"', '`repo` is "two words", not one word'),
    ("[[stage]]", STAGE, "branch = 7", "`branch` is 7, not one word"),
    ("[[stage]]", STAGE, 'after = "B2"', '`after` is "B2", not a list'),
    ("[[stage]]", STAGE, 'after = ["1a"]', '`after` holds "1a", which is not B<n>'),
    ("[[stage]]", STAGE, "why = 1", "`why` is 1, not text"),
    ("[[stage]]", {"id": 'id = "B1"'}, 'round = "proj-b"', '`round` is "proj-b", which is not a round\'s name'),
    ("[[stage]]", {"id": 'id = "B1"'}, 'round = "proj-b/Not A Slug"', "which is not a round's name"),
    ("[[trace]]", TRACE, 'id = "Q1"', '`id` is "Q1", which is not S<n>, C<n> or R<n>'),
    ("[[trace]]", TRACE, 'as = "kept"', '`as` is "kept", which is none of carried | amended | cut'),
    ("[[trace]]", TRACE, "where = 4", "`where` is 4, not text"),
    ("[[trace]]", TRACE, "record = false", "`record` is false, not text"),
    ("[[trace]]", TRACE, 'question = "P1"', '`question` is "P1", which is not Q<n>'),
    ("[[trace]]", TRACE, 'stages = ["all"]', '`stages` holds "all", which is not B<n>'),
    ("[[commitment]]", COMMITMENT, 'id = "C1"', '`id` is "C1", which is not R<n>'),
    ("[[commitment]]", COMMITMENT, 'text = "  "', "`text` says nothing"),
    ("[[commitment]]", COMMITMENT, "where = []", "`where` is [], not text"),
]


@pytest.mark.parametrize(("header", "rows", "bad", "says"), WRONG_VALUES)
def test_a_wrong_value_names_its_line(block, header, rows, bad, says):
    path = block(entry(header, rows, bad))
    [problem] = problems(path)
    assert says in problem.what
    assert problem.line == line_with(path, bad)
    assert problem.fix.startswith(f"give {bad.split()[0]} = ")


WRONG_INSIDE = [
    ("[[stage.slice]]", SLICE, 'id = "one a"', '`id` is "one a"'),
    ("[[stage.slice]]", SLICE, 'what = ""', "`what` says nothing"),
    ("[[stage.slice]]", SLICE, "minutes = 0", "`minutes` is 0, not a whole number from 1 up"),
    ("[[stage.slice]]", SLICE, 'minutes = "40"', '`minutes` is "40", not a whole number'),
    ("[question.prepared]", PREPARED, 'recommend = "Not A Key"', '`recommend` is "Not A Key"'),
    ("[question.prepared]", PREPARED, 'by = ""', "`by` says nothing"),
    ("[question.prepared]", PREPARED, "model = 4", "`model` is 4, not text"),
    ("[question.prepared]", PREPARED, 'sure = "very"', '`sure` is "very", which is none of low | medium | high'),
    ("[question.prepared]", PREPARED, 'why = "one\\ntwo"', "`why` has a line break"),
    ("[question.prepared]", PREPARED, 'flips = ""', "`flips` says nothing"),
    ("[question.prepared]", PREPARED, "read = []", "`read` is [], not a list of at least one pointer"),
    ("[question.prepared]", PREPARED, 'read = "comms:1234567"', "not a list of at least one pointer"),
    ("[question.prepared]", PREPARED, 'read = ["comms:1234567", "he said so"]', '`read` holds "he said so", which is not a pointer'),
    ("[[question.prepared.option]]", OPTION, 'key = "C"', '`key` is "C"'),
    ("[[question.prepared.option]]", OPTION, "text = 3", "`text` is 3, not text"),
    ("[[question.prepared.option]]", OPTION, 'costs = ""', "`costs` says nothing"),
    ("[[question.prepared.option]]", OPTION, 'forecloses = "a\\nb"', "`forecloses` has a line break"),
]


def inside(header: str, rows: dict, change: str | None) -> str:
    """A block with the entry under `header` inside the table it belongs to."""
    if header == "[[stage.slice]]":
        return entry(header, rows, change, before=entry("[[stage]]", STAGE, None))
    question = entry("[[question]]", QUESTION, None)
    if header == "[question.prepared]":
        return entry(header, rows, change, before=question, after=OPTIONS)
    prepared = entry("[question.prepared]", PREPARED, None, before=question, after=OPTIONS)
    return entry(header, rows, change, before=prepared)


@pytest.mark.parametrize(("header", "rows", "bad", "says"), WRONG_INSIDE)
def test_a_wrong_value_in_a_table_under_another_names_its_line(block, header, rows, bad, says):
    path = block(inside(header, rows, bad))
    [problem] = problems(path)
    assert says in problem.what
    assert problem.line == line_with(path, bad)


MISSING = [
    ("[[question]]", QUESTION, "id"), ("[[question]]", QUESTION, "text"),
    ("[[stage]]", STAGE, "id"),
    ("[[trace]]", TRACE, "id"), ("[[trace]]", TRACE, "as"),
    ("[[commitment]]", COMMITMENT, "id"), ("[[commitment]]", COMMITMENT, "text"),
    ("[[stage.slice]]", SLICE, "id"), ("[[stage.slice]]", SLICE, "what"),
    ("[[stage.slice]]", SLICE, "minutes"),
    *[("[question.prepared]", PREPARED, key) for key in PREPARED],
    *[("[[question.prepared.option]]", OPTION, key) for key in OPTION],
]


@pytest.mark.parametrize(("header", "rows", "key"), MISSING)
def test_a_key_an_entry_needs_is_asked_for_on_the_entrys_line(block, header, rows, key):
    top = header.count(".") == 0
    path = block(entry(header, rows, f"-{key}") if top else inside(header, rows, f"-{key}"))
    [problem] = problems(path)
    assert problem.what == f"this `{header}` has no `{key}`"
    assert problem.line == line_with(path, header, 3 if header.endswith("option]]") else 1)
    assert problem.fix.startswith(f"add {key} = ")


@pytest.mark.parametrize(("header", "rows"), [
    ("[[question]]", QUESTION), ("[[stage]]", STAGE), ("[[trace]]", TRACE),
    ("[[commitment]]", COMMITMENT), ("[[stage.slice]]", SLICE),
    ("[question.prepared]", PREPARED), ("[[question.prepared.option]]", OPTION)])
def test_a_key_no_entry_has_is_refused_on_its_line(block, header, rows):
    rows = {**rows, "colour": 'colour = "red"'}
    path = block(entry(header, rows, None) if "." not in header else inside(header, rows, None))
    [problem] = problems(path)
    assert problem.what == f"`colour` is not a key of this `{header}`"
    assert problem.line == line_with(path, "colour")


def test_a_key_at_the_top_of_the_block_is_refused(block):
    path = block('kind = "design"\n' + entry("[[question]]", QUESTION, None))
    [problem] = problems(path)
    assert (problem.line, problem.what) == (4, "`kind` is not a key of the block")
    assert "question, stage, trace, commitment" in problem.fix


@pytest.mark.parametrize(("header", "rows", "kind"), [
    ("[[question]]", QUESTION, "`[[question]]`"), ("[[stage]]", STAGE, "`[[stage]]`"),
    ("[[trace]]", TRACE, "`[[trace]]`"), ("[[commitment]]", COMMITMENT, "`[[commitment]]`")])
def test_an_id_used_twice_among_entries_of_one_kind_is_refused_at_the_second(
        block, header, rows, kind):
    path = block(entry(header, rows, None) + entry(header, rows, None))
    [problem] = problems(path)
    value = rows["id"].split('"')[1]
    assert problem.line == line_with(path, rows["id"], 2)
    assert problem.what == (f"the id {value} is used twice: a {kind} on line "
                            f"{line_with(path, header)} has it too")


def test_a_trace_row_and_a_commitment_may_share_an_id(block):
    read = design_block.read(block(
        entry("[[trace]]", TRACE, 'id = "R1"') + entry("[[commitment]]", COMMITMENT, None)))
    assert (read.traces[0].id, read.commitments[0].id) == ("R1", "R1")


def test_a_slices_id_is_used_once_in_its_stage_and_may_be_used_in_another(block):
    one = entry("[[stage.slice]]", SLICE, None, before=entry("[[stage]]", STAGE, None))
    other = entry("[[stage.slice]]", SLICE, None,
                  before=entry("[[stage]]", STAGE, 'id = "B2"'))
    read = design_block.read(block(one + other))
    assert [stage.slices[0].id for stage in read.stages] == ["1a", "1a"]
    path = block(one + entry("[[stage.slice]]", SLICE, None))
    [problem] = problems(path)
    assert problem.line == line_with(path, 'id = "1a"', 2)
    assert "the id 1a is used twice" in problem.what


def test_a_stage_is_a_build_or_a_round_and_not_both(block):
    path = block(entry("[[stage]]", STAGE, 'round = "proj-b/child"'))
    [problem] = problems(path)
    assert problem.line == line_with(path, "repo =")
    assert "names a round and has `repo`, `branch` too" in problem.what

    path = block(entry("[[stage]]", {"id": 'id = "B1"', "round": 'round = "proj-b/child"'},
                       None, after=entry("[[stage.slice]]", SLICE, None)))
    [problem] = problems(path)
    assert problem.line == line_with(path, "[[stage.slice]]")
    assert "names a round and has `slice` too" in problem.what


@pytest.mark.parametrize(("left_out", "says"), [
    ("repo", "has no `repo`"), ("branch", "has no `branch`")])
def test_a_stage_that_is_neither_a_build_nor_a_round_is_refused(block, left_out, says):
    path = block(entry("[[stage]]", STAGE, f"-{left_out}"))
    [problem] = problems(path)
    assert problem.line == line_with(path, "[[stage]]")
    assert f"the stage B1 {says}" in problem.what
    assert "round = " in problem.fix


def test_a_stage_with_neither_repo_nor_branch_nor_round_names_both(block):
    [problem] = problems(block('[[stage]]\nid = "B1"'))
    assert "the stage B1 has no `repo` and no `branch`" in problem.what


def test_a_stage_does_not_wait_for_itself(block):
    path = block(entry("[[stage]]", STAGE, 'after = ["B2", "B1"]'))
    [problem] = problems(path)
    assert problem.line == line_with(path, "after =")
    assert "`after` names B1, the stage it is in" in problem.what


def test_a_prepared_answer_recommends_one_of_the_options_it_lists(block):
    path = block(inside("[question.prepared]", PREPARED, 'recommend = "c"'))
    [problem] = problems(path)
    assert problem.line == line_with(path, "recommend =")
    assert '`recommend` is "c", which is none of the options the answer lists (a, b)' \
        in problem.what


def test_a_prepared_answer_that_speaks_of_no_option_recommends_none(block):
    path = block(entry("[question.prepared]", PREPARED, None,
                       before=entry("[[question]]", QUESTION, None)))
    [problem] = problems(path)
    assert problem.line == line_with(path, "recommend =")
    assert "none of the options the answer lists: it lists none" in problem.what


def test_a_prepared_answer_speaks_of_each_option_once(block):
    path = block(inside("[[question.prepared.option]]", OPTION, 'key = "a"'))
    [problem] = problems(path)
    assert problem.line == line_with(path, 'key = "a"', 2)
    assert "the key a is used twice" in problem.what


def test_every_wrong_value_of_a_block_is_refused_together(block):
    path = block(entry("[[question]]", QUESTION, "blocks = []")
                 + entry("[[stage]]", STAGE, 'id = "one"')
                 + entry("[[trace]]", TRACE, "-as"))
    found = problems(path)
    assert [one.line for one in found] == [
        line_with(path, "blocks = []"), line_with(path, 'id = "one"'),
        line_with(path, "[[trace]]")]


def test_a_file_with_no_block_or_two_is_refused_as_a_designs(design):
    [none] = problems(design("# A design\n\nOnly prose.\n"))
    assert "holds no data block" in none.what
    [two] = problems(design("```toml rounds\n```\n\n```toml rounds\n```\n"))
    assert two.line == 4


FILL = {
    ("[[question]]", "id"): '"Q1"', ("[[question]]", "text"): '"Which?"',
    ("[[question]]", "blocks"): '["B1"]',
    ("[[question]]", "options"): '{ a = "this", b = "that" }',
    ("[question.prepared]", "recommend"): '"a"', ("[question.prepared]", "by"): '"scout"',
    ("[question.prepared]", "model"): '"a-model"', ("[question.prepared]", "sure"): '"high"',
    ("[question.prepared]", "why"): '"it is plain"',
    ("[question.prepared]", "flips"): '"a second reader"',
    ("[question.prepared]", "read"): '["comms:1234567"]',
    ("[[question.prepared.option]]", "key"): '"a"',
    ("[[question.prepared.option]]", "text"): '"this"',
    ("[[question.prepared.option]]", "costs"): '"time"',
    ("[[question.prepared.option]]", "forecloses"): '"nothing"',
    ("[[stage]]", "id"): ['"B1"', '"B2"'], ("[[stage]]", "repo"): '"proj-a"',
    ("[[stage]]", "branch"): '"search"', ("[[stage]]", "after"): ["[]", '["B1"]'],
    ("[[stage]]", "why"): '"it is the first"', ("[[stage]]", "round"): '"proj-b/child"',
    ("[[stage.slice]]", "id"): '"1a"', ("[[stage.slice]]", "what"): '"the index"',
    ("[[stage.slice]]", "minutes"): "40",
    ("[[trace]]", "id"): '"S1"', ("[[trace]]", "as"): '"amended"',
    ("[[trace]]", "where"): '"the index"', ("[[trace]]", "record"): '"comms:1234567"',
    ("[[trace]]", "question"): '"Q1"', ("[[trace]]", "stages"): '["B1"]',
    ("[[commitment]]", "id"): '"R1"', ("[[commitment]]", "text"): '"It writes nothing."',
    ("[[commitment]]", "where"): '"the index"',
}


def filled(text: str, values: dict) -> str:
    """An empty block with each key's empty value replaced by the one `values` has for
    it under its table's header; a list gives a table written twice a value each time.
    Every key of the block must have one."""
    out, header, met = [], "", {}
    for row in text.split("\n"):
        code = row.split("#")[0].strip()
        if code.startswith("["):
            header = code
            met[header] = met.get(header, 0) + 1
        elif " = " in code:
            key = code.split(" = ")[0]
            value = values[header, key]
            if isinstance(value, list):
                value = value[met[header] - 1]
            row = f"{key} = {value}"
        out.append(row)
    return "\n".join(out)


def test_the_empty_block_has_one_comment_for_every_key_of_the_schema():
    rows = [row.strip() for row in design_block.empty().split("\n")[1:-2] if row.strip()]
    assert all(row.count("# ") == 1 for row in rows)
    keys = {row.split(" = ")[0] for row in rows if " = " in row}
    tables = [design_block.QUESTION, design_block.STAGE, design_block.TRACE,
              design_block.COMMITMENT]
    while tables:
        table = tables.pop()
        assert {key.name for key in table.keys} <= keys
        tables += table.tables
    headers = [row.split("#")[0].strip() for row in rows if row.startswith("[")]
    assert headers == ["[[question]]", "[question.prepared]", "[[question.prepared.option]]",
                       "[[stage]]", "[[stage.slice]]", "[[stage]]", "[[trace]]",
                       "[[commitment]]"]


def test_the_empty_block_is_refused_as_it_stands_and_read_once_filled(design):
    path = design("# A design\n\n" + design_block.empty())
    assert problems(path)
    read = design_block.read(design("# A design\n\n" + filled(design_block.empty(), FILL)))
    assert read.questions[0].prepared.options[0].text == "this"
    assert [(stage.id, stage.repo, stage.round) for stage in read.stages] == [
        ("B1", "proj-a", None), ("B2", None, "proj-b/child")]
    assert read.stages[0].slices[0].minutes == 40
    assert read.traces[0].question == "Q1"
    assert read.commitments[0].where == "the index"
