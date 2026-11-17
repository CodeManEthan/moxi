"""A report's data block: the keys each kind of report takes, what it gives a caller
with the line of each entry, what it refuses on the block alone, and its empty block."""

from __future__ import annotations

import textwrap

import pytest

from moxi_rounds import report_block
from moxi_rounds.errors import BadArgument, BadBlock

HEAD = "0123456789abcdef0123456789abcdef01234567"

FINDINGS = """
[[finding]]
id = "L1"
severity = "major"
state = "fixed"
text = "the index is read before its lock is taken"

[[finding]]
id = "L2"
severity = "note"
state = "open"
text = "a comment names a file that moved"
"""

BUILD = f"""
kind = "build"
verdict = "publishable"
pass = "drift"
by = "critic"
model = "a-model"
stage = "B2"
head = "{HEAD}"
{FINDINGS}
[[wait]]
id = "W1"
text = "check the page on a phone"

[[decision]]
id = "M1"
weight = "major"
forecloses = true
text = "a search is retried twice, where the design said once"

[[decision]]
id = "M2"
weight = "minor"
forecloses = false
text = "the index file has another name"
"""

DESIGN = f"""
kind = "design"
verdict = "fold-first"
pass = "re-check"
by = "critic"
model = "a-model"
design = 3
{FINDINGS}"""

STEP = f"""
kind = "step"
verdict = "publishable"
pass = "full"
step = "security-review"
by = "reviewer"
model = "a-model"
stage = "B2"
head = "{HEAD}"
{FINDINGS}"""

MAJORS = """
kind = "majors"
by = "critic"
model = "a-model"
stages = ["B1", "B2", "B3"]

[[decision]]
id = "M1"
stage = "B1"
weight = "major"
forecloses = false
text = "a search is retried twice, where the design said once"

[[decision]]
id = "M1"
stage = "B2"
weight = "minor"
forecloses = false
text = "the index file has another name"
"""

RETRO = """
kind = "retro"
by = "curator"
model = "a-model"
"""

WHOLE = {"design": DESIGN, "build": BUILD, "step": STEP, "majors": MAJORS, "retro": RETRO}


@pytest.fixture
def block(tmp_path):
    """`block(body)`: a report file whose data block holds `body`, its first line the
    file's eighth, with another fenced block before it and one after."""

    def put(body: str):
        path = tmp_path / "report.md"
        path.write_text("# A report\n\n```toml\nkind = \"not this\"\n```\n\n```toml rounds\n"
                        + textwrap.dedent(body).strip("\n")
                        + "\n```\n\n```text\nkind = nor this\n```\n", encoding="utf-8")
        return path

    return put


def line_with(path, needle: str, nth: int = 1) -> int:
    hits = [number for number, text in enumerate(path.read_text().split("\n"), 1)
            if needle in text]
    return hits[nth - 1]


def problems(path) -> list:
    with pytest.raises(BadBlock) as caught:
        report_block.read(path)
    assert caught.value.file == path
    assert caught.value.what == report_block.WHAT
    return list(caught.value.problems)


def changed(body: str, old: str, new: str) -> str:
    """`body` with its one line `old` replaced by `new`, or taken out for no `new`."""
    lines = body.split("\n")
    assert lines.count(old) == 1, old
    at = lines.index(old)
    lines[at:at + 1] = [new] if new else []
    return "\n".join(lines)


def test_every_key_of_a_build_report_is_read(block):
    path = block(BUILD)
    read = report_block.read(path)
    assert (read.file, read.kind, read.line) == (path, "build", 7)
    assert len(read.sha256) == 64
    assert (read.verdict, read.pass_, read.by, read.model) == (
        "publishable", "drift", "critic", "a-model")
    assert (read.stage, read.head) == ("B2", HEAD)
    assert (read.design, read.step, read.stages) == (None, None, ())
    assert [(one.id, one.severity, one.state) for one in read.findings] == [
        ("L1", "major", "fixed"), ("L2", "note", "open")]
    assert read.findings[0].text == "the index is read before its lock is taken"
    assert [(one.id, one.text) for one in read.waits] == [("W1", "check the page on a phone")]
    assert [(one.id, one.weight, one.forecloses, one.stage) for one in read.decisions] == [
        ("M1", "major", True, None), ("M2", "minor", False, None)]
    assert read.decisions[1].text == "the index file has another name"


def test_every_key_of_a_design_report_is_read(block):
    read = report_block.read(block(DESIGN))
    assert (read.kind, read.verdict, read.pass_, read.design) == (
        "design", "fold-first", "re-check", 3)
    assert (read.stage, read.head, read.step) == (None, None, None)
    assert [one.id for one in read.findings] == ["L1", "L2"]
    assert (read.waits, read.decisions) == ((), ())


def test_every_key_of_a_step_report_is_read(block):
    read = report_block.read(block(STEP))
    assert (read.kind, read.step, read.stage, read.head) == (
        "step", "security-review", "B2", HEAD)
    assert (read.verdict, read.pass_, read.by) == ("publishable", "full", "reviewer")
    assert [one.id for one in read.findings] == ["L1", "L2"]


def test_every_key_of_a_majors_report_is_read(block):
    read = report_block.read(block(MAJORS))
    assert (read.kind, read.stages) == ("majors", ("B1", "B2", "B3"))
    assert (read.verdict, read.pass_, read.stage, read.head) == (None, None, None, None)
    assert [(one.stage, one.id, one.weight) for one in read.decisions] == [
        ("B1", "M1", "major"), ("B2", "M1", "minor")]
    assert read.findings == ()


def test_a_majors_report_may_say_that_its_builds_have_no_decision(block):
    read = report_block.read(block('kind = "majors"\nby = "critic"\nmodel = "a-model"\n'
                                   'stages = ["B4"]'))
    assert (read.stages, read.decisions) == (("B4",), ())


def test_a_retro_report_carries_who_wrote_it_and_nothing_more(block):
    read = report_block.read(block(RETRO))
    assert (read.kind, read.by, read.model, read.verdict) == (
        "retro", "curator", "a-model", None)


def test_each_entry_says_the_line_of_the_file_it_came_from(block):
    path = block(BUILD)
    read = report_block.read(path)
    assert read.line_of("verdict") == line_with(path, "verdict =")
    assert read.line_of("head") == line_with(path, "head =")
    assert read.line_of("design") == read.line
    assert read.findings[1].line == line_with(path, "[[finding]]", 2)
    assert read.findings[1].line_of("state") == line_with(path, 'state = "open"')
    assert read.waits[0].line == line_with(path, "[[wait]]")
    assert read.decisions[1].line == line_with(path, "[[decision]]", 2)
    assert read.decisions[1].line_of("weight") == line_with(path, 'weight = "minor"')


def test_a_report_with_no_finding_wait_or_decision_is_read(block):
    read = report_block.read(block(changed(BUILD.split("\n[[finding]]")[0],
                                           'pass = "drift"', 'pass = "full"')))
    assert (read.findings, read.waits, read.decisions) == ((), (), ())


@pytest.mark.parametrize(("body", "says", "line"), [
    (changed(BUILD, 'kind = "build"', ""), "the block has no `kind`: a report says which "
                                           "kind it is", 7),
    (changed(BUILD, 'kind = "build"', 'kind = "audit"'),
     '`kind` is "audit", which is none of design | build | step | majors | retro', 8),
    (changed(BUILD, 'kind = "build"', 'kind = "answer"'), '`kind` is "answer"', 8),
    (changed(BUILD, 'kind = "build"', "kind = 3"), "`kind` is 3", 8),
])
def test_a_block_that_does_not_say_which_kind_of_report_it_is_is_refused(
        block, body, says, line):
    [problem] = problems(block(body))
    assert says in problem.what
    assert problem.line == line
    assert '"design", "build", "step", "majors", "retro"' in problem.fix


WRONG_VALUES = [
    (BUILD, 'verdict = "publishable"', 'verdict = "pass"',
     '`verdict` is "pass", which is none of publishable | fold-first'),
    (BUILD, 'pass = "drift"', 'pass = "second"',
     '`pass` is "second", which is none of full | re-check | drift'),
    (DESIGN, 'pass = "re-check"', 'pass = "drift"',
     '`pass` is "drift", which is none of full | re-check'),
    (STEP, 'pass = "full"', 'pass = "drift"',
     '`pass` is "drift", which is none of full | re-check'),
    (BUILD, 'by = "critic"', 'by = ""', "`by` says nothing"),
    (BUILD, 'model = "a-model"', "model = 4", "`model` is 4, not text"),
    (BUILD, 'stage = "B2"', 'stage = "2"', '`stage` is "2", which is not B<n>'),
    (BUILD, f'head = "{HEAD}"', 'head = "0123abc"',
     '`head` is "0123abc", which is not a commit\'s whole hash, 40 hex digits'),
    (BUILD, f'head = "{HEAD}"', f'head = "{HEAD.upper()}"', "which is not a commit's whole hash"),
    (DESIGN, "design = 3", 'design = "3"', '`design` is "3", not a whole number from 1 up'),
    (DESIGN, "design = 3", "design = 0", "`design` is 0, not a whole number from 1 up"),
    (STEP, 'step = "security-review"', 'step = "Security Review"',
     '`step` is "Security Review", which is not a step\'s name'),
    (MAJORS, 'stages = ["B1", "B2", "B3"]', "stages = []",
     "`stages` is an empty list, and it names at least one"),
    (MAJORS, 'stages = ["B1", "B2", "B3"]', 'stages = "B1"', '`stages` is "B1", not a list'),
    (BUILD, 'id = "L2"', 'id = "F2"', '`id` is "F2", which is not L<n>'),
    (BUILD, 'id = "L2"', 'id = "B2.L2"', '`id` is "B2.L2", which is not L<n>'),
    (BUILD, 'severity = "note"', 'severity = "nit"',
     '`severity` is "nit", which is none of blocker | major | minor | note'),
    (BUILD, 'state = "open"', 'state = "closed"',
     '`state` is "closed", which is none of open | fixed | no-action'),
    (BUILD, 'text = "a comment names a file that moved"', 'text = ""', "`text` says nothing"),
    (BUILD, 'id = "W1"', 'id = "L9"', '`id` is "L9", which is not W<n>'),
    (BUILD, 'text = "check the page on a phone"', "text = 5", "`text` is 5, not text"),
    (BUILD, 'id = "M2"', 'id = "m2"', '`id` is "m2", which is not M<n>'),
    (BUILD, 'weight = "minor"', 'weight = "heavy"',
     '`weight` is "heavy", which is none of major | minor'),
    (BUILD, "forecloses = false", 'forecloses = "no"', '`forecloses` is "no", not true or false'),
    (BUILD, 'text = "the index file has another name"', 'text = "two\\nlines"',
     "`text` has a line break"),
    (MAJORS, 'stage = "B2"', 'stage = "b2"', '`stage` is "b2", which is not B<n>'),
]


@pytest.mark.parametrize(("body", "old", "bad", "says"), WRONG_VALUES)
def test_a_wrong_value_names_its_line(block, body, old, bad, says):
    path = block(changed(body, old, bad))
    [problem] = problems(path)
    assert says in problem.what
    assert problem.line == line_with(path, bad)
    assert problem.fix.startswith(f"give {bad.split()[0]} = ")


MISSING = [
    (BUILD, 'verdict = "publishable"', "the block", 7),
    (BUILD, 'pass = "drift"', "the block", 7),
    (BUILD, 'by = "critic"', "the block", 7),
    (BUILD, 'model = "a-model"', "the block", 7),
    (BUILD, 'stage = "B2"', "the block", 7),
    (BUILD, f'head = "{HEAD}"', "the block", 7),
    (DESIGN, "design = 3", "the block", 7),
    (DESIGN, 'verdict = "fold-first"', "the block", 7),
    (STEP, 'step = "security-review"', "the block", 7),
    (STEP, 'stage = "B2"', "the block", 7),
    (MAJORS, 'stages = ["B1", "B2", "B3"]', "the block", 7),
    (RETRO, 'by = "curator"', "the block", 7),
    (BUILD, 'id = "L2"', "this `[[finding]]`", ("[[finding]]", 2)),
    (BUILD, 'severity = "note"', "this `[[finding]]`", ("[[finding]]", 2)),
    (BUILD, 'state = "open"', "this `[[finding]]`", ("[[finding]]", 2)),
    (BUILD, 'text = "a comment names a file that moved"', "this `[[finding]]`", ("[[finding]]", 2)),
    (BUILD, 'id = "W1"', "this `[[wait]]`", ("[[wait]]", 1)),
    (BUILD, 'text = "check the page on a phone"', "this `[[wait]]`", ("[[wait]]", 1)),
    (BUILD, 'id = "M2"', "this `[[decision]]`", ("[[decision]]", 2)),
    (BUILD, 'weight = "minor"', "this `[[decision]]`", ("[[decision]]", 2)),
    (BUILD, "forecloses = false", "this `[[decision]]`", ("[[decision]]", 2)),
    (BUILD, 'text = "the index file has another name"', "this `[[decision]]`", ("[[decision]]", 2)),
    (MAJORS, 'stage = "B2"', "this `[[decision]]`", ("[[decision]]", 2)),
]


@pytest.mark.parametrize(("body", "row", "where", "at"), MISSING)
def test_a_key_a_report_needs_is_asked_for_on_the_line_of_its_table(
        block, body, row, where, at):
    path = block(changed(body, row, ""))
    [problem] = problems(path)
    key = row.split()[0]
    assert problem.what == f"{where} has no `{key}`"
    assert problem.line == (at if isinstance(at, int) else line_with(path, *at))
    assert problem.fix.startswith(f"add {key} = ")


OTHER_KINDS = [
    (BUILD, "design = 3", "a build report has none; it is a key of a design report"),
    (BUILD, 'step = "security-review"', "a build report has none; it is a key of a step report"),
    (BUILD, 'stages = ["B2"]', "a build report has none; it is a key of a majors report"),
    (DESIGN, 'stage = "B2"', "a design report has none; it is a key of a build or step report"),
    (DESIGN, f'head = "{HEAD}"', "a design report has none; it is a key of a build or step report"),
    (STEP, "design = 3", "a step report has none; it is a key of a design report"),
    (MAJORS, 'verdict = "publishable"',
     "a majors report has none; it is a key of a design or build or step report"),
    (MAJORS, 'pass = "full"', "a majors report has none"),
    (MAJORS, 'stage = "B2" # of the report', "it is a key of a build or step report"),
    (RETRO, 'verdict = "publishable"', "a retro report has none"),
    (RETRO, 'pass = "full"', "a retro report has none"),
    (RETRO, "design = 3", "a retro report has none"),
]


@pytest.mark.parametrize(("body", "row", "says"), OTHER_KINDS)
def test_a_key_of_another_kind_of_report_is_refused_and_says_whose_it_is(
        block, body, row, says):
    lines = body.strip("\n").split("\n")
    path = block("\n".join([lines[0], row, *lines[1:]]))
    [problem] = problems(path)
    key = row.split()[0]
    assert problem.what.startswith(f"`{key}` is not a key of the block: ")
    assert says in problem.what
    assert problem.line == 9


@pytest.mark.parametrize(("body", "table"), [
    (DESIGN, "wait"), (DESIGN, "decision"), (STEP, "wait"), (STEP, "decision"),
    (MAJORS, "finding"), (MAJORS, "wait"), (RETRO, "finding"), (RETRO, "wait"),
    (RETRO, "decision")])
def test_a_table_of_another_kind_of_report_is_refused(block, body, table):
    path = block(body + f'\n[[{table}]]\nid = "X1"\n')
    [problem] = problems(path)
    assert problem.what.startswith(f"`{table}` is not a key of the block: ")
    assert problem.line == line_with(path, f"[[{table}]]")


def test_a_key_no_report_has_is_refused_on_its_line(block):
    path = block(BUILD.replace('id = "W1"', 'id = "W1"\ncolour = "red"')
                 .replace('stage = "B2"', 'stage = "B2"\nreviewed = true'))
    top, inner = problems(path)
    assert top.what == "`reviewed` is not a key of the block"
    assert top.line == line_with(path, "reviewed")
    assert inner.what == "`colour` is not a key of this `[[wait]]`"
    assert inner.line == line_with(path, "colour")
    assert inner.fix == "take it out; this `[[wait]]` takes id, text"


def test_a_decision_of_a_build_report_names_no_stage(block):
    path = block(changed(BUILD, 'id = "M2"', 'id = "M2"\nstage = "B2"'))
    [problem] = problems(path)
    assert problem.line == line_with(path, 'stage = "B2"', 2)
    assert problem.what.startswith("`stage` is not a key of this `[[decision]]`: ")
    assert "only a majors report gives each decision its stage" in problem.what


@pytest.mark.parametrize(("table", "old", "new"), [
    ("finding", 'id = "L2"', 'id = "L1"'), ("decision", 'id = "M2"', 'id = "M1"')])
def test_an_id_used_twice_in_a_report_is_refused_at_the_second(block, table, old, new):
    path = block(changed(BUILD, old, new))
    [problem] = problems(path)
    assert problem.line == line_with(path, new, 2)
    used, first = new.split('"')[1], line_with(path, f"[[{table}]]")
    assert problem.what == (f"the id {used} is used twice: a `[[{table}]]` on line "
                            f"{first} has it too")


def test_a_majors_report_uses_a_decisions_id_once_for_each_stage(block):
    path = block(changed(MAJORS, 'stage = "B2"', 'stage = "B1"'))
    [problem] = problems(path)
    assert problem.line == line_with(path, 'id = "M1"', 2)
    assert "the id M1 of B1 is used twice" in problem.what


def test_a_decision_that_forecloses_is_major(block):
    path = block(changed(BUILD, "forecloses = false", "forecloses = true"))
    [problem] = problems(path)
    assert problem.line == line_with(path, 'weight = "minor"')
    assert problem.what == ("the decision M2 forecloses something and its weight is "
                            "minor: a decision that forecloses is major")


@pytest.mark.parametrize("severity", ["blocker", "major"])
@pytest.mark.parametrize("body", [BUILD, STEP, changed(DESIGN, 'verdict = "fold-first"',
                                                       'verdict = "publishable"')])
def test_a_publishable_verdict_with_an_open_blocker_or_major_contradicts_itself(
        block, body, severity):
    path = block(changed(body, 'severity = "note"', f'severity = "{severity}"'))
    [problem] = problems(path)
    assert problem.line == line_with(path, "[[finding]]", 2)
    assert problem.what == (f"the verdict is publishable and the finding L2 is an open "
                            f"{severity}: the two contradict each other")


@pytest.mark.parametrize("body", [
    changed(BUILD, 'severity = "note"', 'severity = "minor"'),
    changed(changed(BUILD, 'severity = "note"', 'severity = "blocker"'),
            'state = "open"', 'state = "no-action"'),
    changed(changed(BUILD, 'severity = "note"', 'severity = "blocker"'),
            'verdict = "publishable"', 'verdict = "fold-first"'),
])
def test_a_verdict_that_does_not_contradict_its_findings_is_read(block, body):
    assert report_block.read(block(body)).kind == "build"


def test_a_decision_of_a_majors_report_is_of_a_stage_the_report_covers(block):
    path = block(changed(MAJORS, 'stage = "B2"', 'stage = "B7"'))
    [problem] = problems(path)
    assert problem.line == line_with(path, 'stage = "B7"')
    assert problem.what == ("the decision M1 is of the stage B7, which `stages` does not "
                            "name (B1, B2, B3)")


def test_every_wrong_value_of_a_report_is_refused_together(block):
    body = changed(changed(changed(BUILD, 'by = "critic"', ""), 'state = "open"',
                           'state = "done"'), 'id = "W1"', 'id = "w"')
    path = block(body)
    assert [one.line for one in problems(path)] == [
        7, line_with(path, 'state = "done"'), line_with(path, 'id = "w"')]


def test_a_file_with_no_block_or_two_is_refused_as_a_reports(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("# A report\n\nOnly prose.\n", encoding="utf-8")
    [none] = problems(path)
    assert "holds no data block" in none.what
    path.write_text('```toml rounds\nkind = "retro"\n```\n\n```toml rounds\n```\n',
                    encoding="utf-8")
    [two] = problems(path)
    assert two.line == 5


FILL = {
    ("", "verdict"): '"fold-first"', ("", "pass"): '"full"', ("", "by"): '"critic"',
    ("", "model"): '"a-model"', ("", "design"): "2", ("", "stage"): '"B1"',
    ("", "head"): f'"{HEAD}"', ("", "step"): '"security-review"', ("", "stages"): '["B1"]',
    ("[[finding]]", "id"): '"L1"', ("[[finding]]", "severity"): '"blocker"',
    ("[[finding]]", "state"): '"open"', ("[[finding]]", "text"): '"it loses a note"',
    ("[[wait]]", "id"): '"W1"', ("[[wait]]", "text"): '"look at the page"',
    ("[[decision]]", "id"): '"M1"', ("[[decision]]", "stage"): '"B1"',
    ("[[decision]]", "weight"): '"major"', ("[[decision]]", "forecloses"): "true",
    ("[[decision]]", "text"): '"it searches twice"',
}


def filled(text: str) -> str:
    """An empty block with each key's empty value replaced by the one `FILL` has for it
    under its table's header. Every key but `kind` must have one."""
    out, header = [], ""
    for row in text.split("\n"):
        code = row.split("#")[0].strip()
        if code.startswith("["):
            header = code
        elif " = " in code and not code.startswith("kind = "):
            key = code.split(" = ")[0]
            row = f"{key} = {FILL[header, key]}"
        out.append(row)
    return "\n".join(out)


@pytest.mark.parametrize("kind", report_block.KINDS)
def test_the_empty_block_of_each_kind_has_one_comment_for_every_key(kind):
    text = report_block.empty(kind)
    rows = [row.strip() for row in text.split("\n")[1:-2] if row.strip()]
    assert rows[0].startswith(f'kind = "{kind}"')
    assert all(row.count("# ") == 1 for row in rows)
    schema = report_block.SCHEMAS[kind]
    top = [row.split(" = ")[0] for row in rows[:len(schema.keys)]]
    assert top == [key.name for key in schema.keys]
    headers = [row.split("#")[0].strip() for row in rows if row.startswith("[")]
    assert headers == [f"[[{table.name}]]" for table in schema.tables]
    for table in schema.tables:
        at = rows.index(next(row for row in rows if row.startswith(f"[[{table.name}]]")))
        under = [row.split(" = ")[0] for row in rows[at + 1:at + 1 + len(table.keys)]]
        assert under == [key.name for key in table.keys]


@pytest.mark.parametrize("kind", report_block.KINDS)
def test_the_empty_block_of_each_kind_is_read_once_filled(tmp_path, kind):
    path = tmp_path / "report.md"
    path.write_text("# A report\n\n" + report_block.empty(kind), encoding="utf-8")
    assert problems(path)
    path.write_text("# A report\n\n" + filled(report_block.empty(kind)), encoding="utf-8")
    read = report_block.read(path)
    assert (read.kind, read.by, read.line) == (kind, "critic", 3)
    assert len(read.findings) == (1 if kind in ("design", "build", "step") else 0)
    assert len(read.waits) == (1 if kind == "build" else 0)
    assert len(read.decisions) == (1 if kind in ("build", "majors") else 0)


def test_the_empty_block_of_a_retro_report_needs_only_who_wrote_it(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(report_block.empty("retro"), encoding="utf-8")
    assert [one.what for one in problems(path)] == ["`by` says nothing", "`model` says nothing"]


@pytest.mark.parametrize("kind", ["answer", "audit", ""])
def test_no_empty_block_is_given_for_a_word_that_is_no_kind_of_report(kind):
    with pytest.raises(BadArgument) as caught:
        report_block.empty(kind)
    assert "design, build, step, majors, retro" in caught.value.fix
