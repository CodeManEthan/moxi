"""The data block of a prepared answer: what an agent that looked into a question
recommends, how sure it is, what it read, and what each option costs and forecloses.

`read(file)` gives the block as an `AnswerBlock`, or raises `BadBlock` with every
problem, each with its line and its fix. `empty()` is the block's empty text, which
`next --schema answer` prints.

```toml
kind = "answer"
question = "Q4"
by = "scout"
model = "a-model"
recommend = "b"
sure = "medium"
why = "archived notes are read once a year"
flips = "a reader who asks for an archived note by name"
read = ["file:design.md#search"]

[[option]]
key = "a"
costs = "a slower index"
forecloses = "nothing"

[[option]]
key = "b"
costs = "archived notes are not found"
forecloses = "nothing"
```

An `[[option]]` carries `text`, the option's line, only where the question has no
options yet: the answer then gives the question its options.

**What is checked here** is what the block shows on its own: every key and its value,
that an option's key is used once, and that `recommend` is one of them.

**What is not** is all that needs the record: whether the question is one on record
and still asked, and whether the answer speaks of every option it offers and of no
other (`preparing.problems`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import datablock, design_block, options
from .datablock import ident, word
from .errors import BadBlock

#: What a refusal calls the block.
WHAT = "a prepared answer's data block"

#: The block's `kind`.
KIND = "answer"

#: The table each option is written in.
OPTION_HEADER = "[[option]]"

_PREPARED = {key.name: key for key in design_block.PREPARED.keys}

#: The schema of the block: `kind` and the question it answers, then the keys of a
#: design's `[question.prepared]`, and one `[[option]]` for each option.
SCHEMA = datablock.Table("", keys=(
    word("kind", (KIND,), "the kind of block this is", blank=f'"{KIND}"'),
    ident("question", options.QUESTION_ID, "Q<n> or P<n>",
          "the question it answers: Q<n> of a round, P<n> of a project's list"),
    *(_PREPARED[name] for name in ("by", "model", "recommend", "sure", "why", "flips",
                                   "read")),
), tables=(design_block.PREPARED_OPTION,))


@dataclass(frozen=True)
class AnswerBlock:
    """A prepared answer's block as read: the `file`, the SHA-256 of the file as read,
    the `line` the block opens on, the `question` it answers with the line it is on,
    and the answer itself."""

    file: Path
    sha256: str
    line: int
    question: str
    question_line: int
    prepared: design_block.Prepared


def empty() -> str:
    """The empty block of a prepared answer: `kind` filled in, every other key with an
    empty value and its comment, and one `[[option]]`."""
    return datablock.empty(SCHEMA)


def read(file: Path | str) -> AnswerBlock:
    """The data block of the prepared answer `file`.

    Raises `BadBlock` when the file has no one data block that is TOML, and when the
    block does not fit: the problems of its keys and values first, and once those are
    right an option's key used twice or a `recommend` that is none of them.
    """
    found = datablock.find(file, WHAT)
    problems = datablock.check(found, SCHEMA) or design_block.prepared_between(
        found, (), found.data, OPTION_HEADER)
    if problems:
        raise BadBlock(found.file, WHAT, problems)
    prepared = design_block.prepared_of(found, (), found.data)
    return AnswerBlock(file=found.file, sha256=found.sha256, line=found.line,
                       question=found.data["question"],
                       question_line=found.line_of("question"), prepared=prepared)


__all__ = ["WHAT", "KIND", "OPTION_HEADER", "SCHEMA", "AnswerBlock", "empty", "read"]
