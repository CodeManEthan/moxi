"""The data block of a design: the questions, stages, trace rows and commitments it
declares.

`read(file)` gives the block as a `DesignBlock`, each entry with the line of the file
it came from, or raises `BadBlock` with every problem, each with its line and its fix.
`empty()` is the block's empty text.

```toml
[[question]]
id = "Q4"
text = "Should search read archived notes too?"
blocks = ["B2"]            # left out: every stage; "none": no stage
options = { a = "yes, all notes", b = "no, live notes only" }

[[stage]]
id = "B1"
repo = "proj-a"
branch = "search-index"
after = []

  [[stage.slice]]
  id = "1a"
  what = "the index and its reader"
  minutes = 40

[[stage]]
id = "B2"
round = "proj-a/search-page"    # a round of its own in place of a build

[[trace]]
id = "S12"
as = "carried"

[[commitment]]
id = "R1"
text = "A search never reads a note that was deleted."
```

A question may also carry `[question.prepared]`, an answer prepared for it, with one
`[[question.prepared.option]]` for each of its options.

**What is checked here** is what the block shows on its own: every key and its value,
that an id is used once among the entries of its kind (a slice's among its stage's
slices), that a stage is a build (`repo` and `branch`) or a round and not both, that a
stage does not wait for itself, that a question's options are two to four, and that a
prepared answer recommends one of the options it speaks of and speaks of each once. A trace row and a commitment
may share an id: the row is where the design says what became of that commitment.

**What is not** is all that needs the round's record: whether a question's id was used
before, whether a stage it names is one the round has, whether a key was retired,
whether a prepared answer speaks of every option of its question and of no other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from . import datablock, folders, options, pointers, questions
from .datablock import Entry, Found, Table, count, ident, idents, line, word
from .errors import BadBlock, Problem

#: What a refusal calls the block.
WHAT = "a design's data block"

#: What `blocks` holds in a block for a question that holds up no stage. A question
#: that holds up every stage leaves the key out.
NONE = questions.NONE
ALL = questions.ALL

#: What became of a ruling or a commitment in the design, a trace row's `as`.
CARRIED = "carried"
AMENDED = "amended"
CUT = "cut"
TRACED = (CARRIED, AMENDED, CUT)

#: How sure a prepared answer is of what it recommends.
SURE = ("low", "medium", "high")

#: The id of a commitment the design makes.
COMMITMENT_ID = re.compile(r"R[1-9][0-9]*")

#: What a trace row may cite: a standing ruling, a round's ruling, or a commitment.
TRACE_ID = re.compile(r"[SCR][1-9][0-9]*")

#: The id of a slice: letters, digits, `.`, `_` and `-`.
SLICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: A project's key or a branch's name, as far as a block is checked for it: one token.
_TOKEN = re.compile(r"\S+")


@dataclass(frozen=True, kw_only=True)
class PreparedOption(Entry):
    """What a prepared answer says of one option. `text` is the option's line, given
    where the question has no options yet, else None."""

    key: str
    text: str | None
    costs: str
    forecloses: str


@dataclass(frozen=True, kw_only=True)
class Prepared(Entry):
    """An answer prepared for a question: the key it recommends, who prepared it and
    with which model, how sure it is and why, the fact that would flip it, the
    pointers to what was read, and what it says of each option."""

    recommend: str
    by: str
    model: str
    sure: str
    why: str
    flips: str
    read: tuple[str, ...]
    options: tuple[PreparedOption, ...]


@dataclass(frozen=True, kw_only=True)
class Question(Entry):
    """A question the design declares. `blocks` is `ALL` when the block leaves it out,
    `NONE`, or stage ids. `options` is each `(key, line)` in the block's order, or None
    when the block carries no options for it."""

    id: str
    text: str
    blocks: str | tuple[str, ...]
    options: tuple[tuple[str, str], ...] | None
    prepared: Prepared | None


@dataclass(frozen=True, kw_only=True)
class Slice(Entry):
    """One worker's piece of a stage: what it is and how many minutes it is given."""

    id: str
    what: str
    minutes: int


@dataclass(frozen=True, kw_only=True)
class Stage(Entry):
    """A stage the design plans: a build in `repo` on `branch`, or the round `round`
    in place of one; the other is None. `after` names the stages finished first."""

    id: str
    repo: str | None
    branch: str | None
    round: str | None
    after: tuple[str, ...]
    why: str
    slices: tuple[Slice, ...]


@dataclass(frozen=True, kw_only=True)
class Trace(Entry):
    """A row that says what became of a ruling or a commitment: `how` is the row's
    `as`, one of `TRACED`; `stages` the stages it is for, none given meaning every
    one."""

    id: str
    how: str
    where: str
    record: str
    question: str | None
    stages: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class Commitment(Entry):
    """Something the design commits to, in one line."""

    id: str
    text: str
    where: str


@dataclass(frozen=True)
class DesignBlock:
    """A design's data block as read: the `file`, the `line` its block opens on, the
    SHA-256 of the file as read, and what it declares, each in the block's order."""

    file: Path
    line: int
    sha256: str
    questions: tuple[Question, ...]
    stages: tuple[Stage, ...]
    traces: tuple[Trace, ...]
    commitments: tuple[Commitment, ...]


# The schema.

def _blocks(value: object) -> str | None:
    if value == NONE and isinstance(value, str):
        return None
    if isinstance(value, str):
        return (f"is {datablock.shown(value)}; as text it is only \"{NONE}\", and a "
                "question that holds up every stage leaves the key out")
    if value == []:
        return ("is an empty list, which could mean every stage or none: leave the key "
                f"out for every stage, or say \"{NONE}\" for none")
    return datablock.wrong_ids(value, questions.STAGE, "B<n>")


def _options(value: object) -> str | None:
    if not isinstance(value, dict):
        return f"is {datablock.shown(value)}, not a table of choices"
    for key, text in value.items():
        if not options.KEY.fullmatch(key):
            return (f"has the key {datablock.shown(key)}, and a key is short: lower-case "
                    "letters, digits and -, sixteen at most")
        if not datablock.is_line(text):
            return f"gives {key} {datablock.shown(text)}, and an option is one line of text"
    if not options.FEWEST <= len(value) <= options.MOST:
        return (f"holds {len(value)}, and a question that has options has "
                f"{options.FEWEST} to {options.MOST}")
    return None


def _read(value: object) -> str | None:
    if not isinstance(value, list) or not value:
        return f"is {datablock.shown(value)}, not a list of at least one pointer"
    for one in value:
        if pointers.shape(one) is None:
            return f"holds {datablock.shown(one)}, which is not a pointer"
    return None


def _round(value: object) -> str | None:
    project, slash, slug = value.partition("/") if isinstance(value, str) else ("", "", "")
    if _TOKEN.fullmatch(project) and slash and folders.is_slug(slug):
        return None
    return f"is {datablock.shown(value)}, which is not a round's name"


def _token(name: str, says: str) -> datablock.Key:
    def check(value: object) -> str | None:
        if isinstance(value, str) and _TOKEN.fullmatch(value):
            return None
        return f"is {datablock.shown(value)}, not one word"

    return datablock.Key(name, check, '"one word"', says, required=False)


_STAGE_IDS = "the stages finished before this one starts; none when left out"

PREPARED_OPTION = Table("option", "one for each option of the question", (
    ident("key", options.KEY, "a short key, as a", "the option's key"),
    line("text", "the option's line, only where the question has no options yet",
         required=False),
    line("costs", "what choosing it costs"),
    line("forecloses", "what it rules out later, or: nothing"),
))

PREPARED = Table("prepared", "optional: an answer prepared for the question", (
    ident("recommend", options.KEY, "a short key, as a",
          "the option recommended, by key"),
    line("by", "who prepared the answer"),
    line("model", "the model that prepared it"),
    word("sure", SURE, "how sure the recommendation is"),
    line("why", "why it is that sure"),
    line("flips", "the one fact that, if different, flips the recommendation"),
    datablock.Key("read", _read, 'a list of pointers, as ["file:design.md#index"]',
                  "what was read: at least one pointer", blank="[]"),
), (PREPARED_OPTION,), many=False)

QUESTION = Table("question", "one for each question put to the operator", (
    ident("id", questions.ROUND_ID, "Q<n>", "Q<n>, an id no question of the round had"),
    line("text", "one sentence that asks it"),
    datablock.Key("blocks", _blocks, f'a list of stage ids, as ["B1"], or "{NONE}"',
                  f'the stages it holds up, or "{NONE}"; every stage when left out',
                  blank="[]", required=False),
    datablock.Key("options", _options,
                  'two to four choices, as { a = "one line", b = "another" }',
                  "two to four choices, a line each under a short key",
                  blank="{}", required=False),
), (PREPARED,))

SLICE = Table("slice", "one for each worker's piece of the stage", (
    ident("id", SLICE_ID, "a short id, as 1a", "a short id, as 1a"),
    line("what", "the one mechanism or file group it builds"),
    count("minutes", "how long it is given, in minutes"),
))

STAGE = Table("stage", "one for each planned build, or round in place of a build", (
    ident("id", questions.STAGE, "B<n>", "B<n>"),
    _token("repo", "the project whose repo it is built in, by key"),
    _token("branch", "the branch it is built on"),
    datablock.Key("round", _round, '"<project>/<slug>"',
                  "the round that stands in place of a build", required=False),
    idents("after", questions.STAGE, "B<n>", _STAGE_IDS),
    line("why", "for a second stage in one repo: why it lands on its own",
         required=False),
), (SLICE,))

TRACE = Table("trace", "one for each ruling or commitment the design follows", (
    ident("id", TRACE_ID, "S<n>, C<n> or R<n>",
          "a standing ruling S<n>, a round's ruling C<n>, or a commitment R<n>"),
    word("as", TRACED, "what the design does with it"),
    line("where", "the section of the design that does it", required=False),
    line("record", "amended and cut: the pointer to where the operator said so",
         required=False),
    ident("question", questions.ROUND_ID, "Q<n>",
          "amended and cut, in place of record: the open question that asks him",
          required=False),
    idents("stages", questions.STAGE, "B<n>",
           "the stages the row is for; every stage when left out"),
))

COMMITMENT = Table("commitment", "one for each thing the design commits to", (
    ident("id", COMMITMENT_ID, "R<n>", "R<n>"),
    line("text", "what the design commits to, in one line"),
    line("where", "the section of the design that keeps it", required=False),
))

#: The schema of the block's top.
SCHEMA = Table("", tables=(QUESTION, STAGE, TRACE, COMMITMENT))

#: A stage's keys in each of its two forms: a build, and a round in place of one.
_BUILD = ("id", "repo", "branch", "after", "why", "slice")
_CHILD = ("id", "round", "after")


def empty() -> str:
    """The empty block of a design: every table once, a stage in each of its two
    forms, each key with an empty value and its comment."""
    return datablock.empty(Table("", tables=(
        QUESTION, _form_of(_BUILD, "one for each planned build"),
        _form_of(_CHILD, "one for each round planned in place of a build"),
        TRACE, COMMITMENT)))


def _form_of(names: tuple[str, ...], says: str) -> Table:
    """A stage in one of its two forms, for the empty block: there, the keys that
    make the form are not ones it can go without."""
    form = STAGE.keeping(*names)
    return replace(form, says=says, keys=tuple(
        replace(key, required=True) if key.name in ("repo", "branch", "round") else key
        for key in form.keys))


# Reading.

def read(file: Path | str) -> DesignBlock:
    """The data block of the design `file`.

    Raises `BadBlock` when the file has no one data block that is TOML, and when the
    block does not fit: the problems of its keys and values first, and once those are
    right the ones between its entries.
    """
    found = datablock.find(file, WHAT)
    problems = datablock.check(found, SCHEMA) or _between(found)
    if problems:
        raise BadBlock(found.file, WHAT, problems)
    data = found.data
    return DesignBlock(
        file=found.file, line=found.line, sha256=found.sha256,
        questions=tuple(_question(found, number, one)
                        for number, one in enumerate(data.get("question", ()))),
        stages=tuple(_stage(found, number, one)
                     for number, one in enumerate(data.get("stage", ()))),
        traces=tuple(_trace(found, number, one)
                     for number, one in enumerate(data.get("trace", ()))),
        commitments=tuple(
            Commitment(id=one["id"], text=one["text"], where=one.get("where", ""),
                       **_placed(found, ("commitment", number), one))
            for number, one in enumerate(data.get("commitment", ()))))


def _between(found: Found) -> list[Problem]:
    """The problems between the entries of a block whose keys and values are right."""
    data = found.data
    problems: list[Problem] = []
    for name in ("question", "stage", "trace", "commitment"):
        problems += _twice(found, (name,), data.get(name, ()), "id", f"`[[{name}]]`")
    for number, question in enumerate(data.get("question", ())):
        prepared = question.get("prepared")
        if prepared is None:
            continue
        at = ("question", number, "prepared")
        offered = prepared.get("option", [])
        problems += _twice(found, at + ("option",), offered, "key",
                           "`[[question.prepared.option]]`")
        keys = [one["key"] for one in offered]
        if prepared["recommend"] not in keys:
            lists = f"lists ({', '.join(keys)})" if keys else "lists: it lists none"
            problems.append(Problem(
                found.line_of(*at, "recommend"),
                f"`recommend` is {datablock.shown(prepared['recommend'])}, which is "
                f"none of the options the answer {lists}",
                "add one [[question.prepared.option]] for each option of the question, "
                "and recommend the key of one"))
    for number, stage in enumerate(data.get("stage", ())):
        at = ("stage", number)
        problems += _form(found, at, stage)
        problems += _twice(found, at + ("slice",), stage.get("slice", ()), "id",
                           "`[[stage.slice]]` of the stage")
        if stage["id"] in stage.get("after", ()):
            problems.append(Problem(
                found.line_of(*at, "after"),
                f"`after` names {stage['id']}, the stage it is in: a stage can't wait "
                "for itself", f"take {stage['id']} out of its own after"))
    return problems


def _form(found: Found, at: tuple, stage: dict) -> list[Problem]:
    """The problems of a stage that is neither a build nor a round in place of one,
    or is written as both."""
    line_of = found.line_of
    if "round" in stage:
        extra = [name for name in ("repo", "branch", "why", "slice") if name in stage]
        if not extra:
            return []
        return [Problem(
            line_of(*at, extra[0]),
            f"the stage {stage['id']} names a round and has `{'`, `'.join(extra)}` "
            "too: a round in place of a build has no repo, branch or slices of its own",
            f"keep id, round and after for a round in place of a build, or take "
            "`round` out for a build")]
    missing = [name for name in ("repo", "branch") if name not in stage]
    if not missing:
        return []
    return [Problem(
        line_of(*at),
        f"the stage {stage['id']} has no `{'` and no `'.join(missing)}`: a stage is a "
        "build, with a repo and a branch, or a round in place of one",
        'add repo = "<a project\'s key>" and branch = "<a branch>", or round = '
        '"<project>/<slug>"')]


def _twice(found: Found, at: tuple, entries, key: str, kind: str) -> list[Problem]:
    """A problem for each entry whose `key` an earlier entry of the list has."""
    first: dict[str, int] = {}
    problems = []
    for number, one in enumerate(entries):
        value = one[key]
        if value in first:
            problems.append(Problem(
                found.line_of(*at, number, key),
                f"the {key} {value} is used twice: a {kind} on line "
                f"{found.line_of(*at, first[value])} has it too",
                f"give each {kind} a {key} of its own"))
        else:
            first[value] = number
    return problems


def _placed(found: Found, at: tuple, entry: dict) -> dict:
    return {"line": found.line_of(*at), "lines": found.lines(at, entry)}


def _question(found: Found, number: int, one: dict) -> Question:
    at = ("question", number)
    blocks = one.get("blocks", ALL)
    offered = one.get("options")
    return Question(
        id=one["id"], text=one["text"],
        blocks=blocks if isinstance(blocks, str) else tuple(blocks),
        options=None if offered is None else tuple(offered.items()),
        prepared=_prepared(found, at + ("prepared",), one.get("prepared")),
        **_placed(found, at, one))


def _prepared(found: Found, at: tuple, one: dict | None) -> Prepared | None:
    if one is None:
        return None
    return Prepared(
        recommend=one["recommend"], by=one["by"], model=one["model"], sure=one["sure"],
        why=one["why"], flips=one["flips"], read=tuple(one["read"]),
        options=tuple(
            PreparedOption(key=option["key"], text=option.get("text") or None,
                           costs=option["costs"], forecloses=option["forecloses"],
                           **_placed(found, at + ("option", number), option))
            for number, option in enumerate(one.get("option", ()))),
        **_placed(found, at, one))


def _stage(found: Found, number: int, one: dict) -> Stage:
    at = ("stage", number)
    return Stage(
        id=one["id"], repo=one.get("repo"), branch=one.get("branch"),
        round=one.get("round"), after=tuple(one.get("after", ())),
        why=one.get("why", ""),
        slices=tuple(Slice(id=part["id"], what=part["what"], minutes=part["minutes"],
                           **_placed(found, at + ("slice", index), part))
                     for index, part in enumerate(one.get("slice", ()))),
        **_placed(found, at, one))


def _trace(found: Found, number: int, one: dict) -> Trace:
    return Trace(
        id=one["id"], how=one["as"], where=one.get("where", ""),
        record=one.get("record", ""), question=one.get("question"),
        stages=tuple(one.get("stages", ())), **_placed(found, ("trace", number), one))


__all__ = ["WHAT", "NONE", "ALL", "CARRIED", "AMENDED", "CUT", "TRACED", "SURE",
           "COMMITMENT_ID", "TRACE_ID", "SLICE_ID", "PreparedOption", "Prepared",
           "Question", "Slice", "Stage", "Trace", "Commitment", "DesignBlock",
           "QUESTION", "STAGE", "TRACE", "COMMITMENT", "SCHEMA", "empty", "read"]
