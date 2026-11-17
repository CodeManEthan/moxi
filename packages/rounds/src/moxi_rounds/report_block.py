"""The data block of a report: what a pass read, its verdict, and the findings, waiting
items and decisions it declares.

`read(file)` gives the block as a `ReportBlock`, each entry with the line of the file it
came from, or raises `BadBlock` with every problem, each with its line and its fix.
`empty(kind)` is the empty block of one kind of report.

```toml
kind = "build"
verdict = "fold-first"
pass = "full"
by = "critic"
model = "opus"
stage = "B2"
head = "0123456789abcdef0123456789abcdef01234567"

[[finding]]
id = "L1"
severity = "major"
state = "open"
text = "the index is read before its lock is taken"

[[wait]]
id = "W1"
text = "check the page on a phone"

[[decision]]
id = "M1"
weight = "major"
forecloses = false
text = "a search is retried twice, where the design said once"
```

**The kinds**, and what each carries beside `kind`, `by` and `model`:

| Kind | Reads | Carries |
|---|---|---|
| `design` | a frozen version of the design, `design` | `verdict`, `pass`, findings |
| `build` | one build, `stage`, at the tip `head` | `verdict`, `pass`, findings, waiting items, decisions |
| `step` | one build, `stage`, at `head`, for the step `step` | `verdict`, `pass`, findings |
| `majors` | the builds in `stages` | decisions, each with its `stage` |
| `retro` | the round | nothing more |

A key that belongs to another kind is refused, and names the kind it belongs to.

**What is checked here** is what the block shows on its own: every key and its value,
that an id is used once among the findings, the waiting items and the decisions (a
majors report's decisions once for each stage), that a decision which forecloses
something is major, that a `publishable` verdict lists no open blocker or major
finding, and that each decision of a majors report is of a stage the report covers.

**What is not** is all that needs the round's record: whether the version, the stage
or the head is one the round has, whether an id was used by an earlier report, whether
every open finding is listed, whether a decision was made lighter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import datablock, questions
from .datablock import Entry, Found, Key, Table, count, flag, ident, idents, line, word
from .errors import BadArgument, BadBlock, Problem

#: What a refusal calls the block.
WHAT = "a report's data block"

#: The kinds of report.
DESIGN = "design"
BUILD = "build"
STEP = "step"
MAJORS = "majors"
RETRO = "retro"
KINDS = (DESIGN, BUILD, STEP, MAJORS, RETRO)

#: The two verdicts.
PUBLISHABLE = "publishable"
FOLD_FIRST = "fold-first"
VERDICTS = (PUBLISHABLE, FOLD_FIRST)

#: What a pass was: the first whole reading, a re-check of what changed since, or a
#: re-check of a build after its target moved.
FULL = "full"
RECHECK = "re-check"
DRIFT = "drift"

#: A finding's severities, heaviest first, and its states.
BLOCKER = "blocker"
MAJOR = "major"
MINOR = "minor"
NOTE = "note"
SEVERITIES = (BLOCKER, MAJOR, MINOR, NOTE)
OPEN = "open"
FIXED = "fixed"
NO_ACTION = "no-action"
STATES = (OPEN, FIXED, NO_ACTION)

#: A decision's weights.
WEIGHTS = (MAJOR, MINOR)

#: The ids of a finding, a waiting item and a decision, as a report writes them.
FINDING_ID = re.compile(r"L[1-9][0-9]*")
WAIT_ID = re.compile(r"W[1-9][0-9]*")
DECISION_ID = re.compile(r"M[1-9][0-9]*")

#: A commit as a report names it: its whole hash.
HEAD = re.compile(r"[0-9a-f]{40}")

#: A user step's name.
STEP_NAME = re.compile(r"[a-z0-9-]+")


@dataclass(frozen=True, kw_only=True)
class Finding(Entry):
    """Something a pass found: how heavy it is, and whether it is still open."""

    id: str
    severity: str
    state: str
    text: str


@dataclass(frozen=True, kw_only=True)
class Wait(Entry):
    """Work that can't be done without the operator."""

    id: str
    text: str


@dataclass(frozen=True, kw_only=True)
class Decision(Entry):
    """A choice made in the building that the operator would notice. `stage` is the
    build it was made in, given in a majors report and None in a build's own."""

    id: str
    weight: str
    forecloses: bool
    text: str
    stage: str | None


@dataclass(frozen=True, kw_only=True)
class ReportBlock(Entry):
    """A report's data block as read: the `file`, the SHA-256 of the file as read,
    and its keys. A key the report's kind does not carry is None, or empty for a list.
    `pass_` is the block's `pass`. `line` is the line the block opens on."""

    file: Path
    sha256: str
    kind: str
    by: str
    model: str
    verdict: str | None
    pass_: str | None
    design: int | None
    stage: str | None
    head: str | None
    step: str | None
    stages: tuple[str, ...]
    findings: tuple[Finding, ...]
    waits: tuple[Wait, ...]
    decisions: tuple[Decision, ...]


# The schemas.

FINDING = Table("finding", "one for each finding; none when the pass found nothing", (
    ident("id", FINDING_ID, "L<n>", "L<n>: a new one takes an id no earlier report used"),
    word("severity", SEVERITIES, "how heavy it is"),
    word("state", STATES, "open while it is not fixed at what the pass read"),
    line("text", "what is wrong, in one line"),
))

WAIT = Table("wait", "one for each piece of work that needs the operator; often none", (
    ident("id", WAIT_ID, "W<n>", "W<n>: an id no earlier report of the stage used"),
    line("text", "what waits for him, in one line"),
))

_DECISION = (
    ident("id", DECISION_ID, "M<n>", "M<n>: a new one takes an id no earlier report of "
                                      "the stage used"),
    ident("stage", questions.STAGE, "B<n>", "the build it was made in"),
    word("weight", WEIGHTS, "major when the operator would rule on it"),
    flag("forecloses", "true when it rules out something he could have had; then it "
                       "is major"),
    line("text", "what was built, and what he ruled or the design said, in one line"),
)

DECISION = Table(
    "decision", "one for each choice the operator would notice; often none",
    tuple(key for key in _DECISION if key.name != "stage"),
    elsewhere={"stage": "a decision here is of the build the report read; only a "
                        "majors report gives each decision its stage"})
MAJORS_DECISION = Table("decision", "one for each choice the operator would notice; "
                                    "none says the builds have none", _DECISION)


def _keys(kind: str) -> tuple[Key, ...]:
    """The keys at the top of a report of `kind`, in the order the empty block has
    them."""
    passes = (FULL, RECHECK, DRIFT) if kind == BUILD else (FULL, RECHECK)
    keys = [word("kind", KINDS, "the kind of report this is", blank=f'"{kind}"')]
    if kind in (DESIGN, BUILD, STEP):
        keys += [word("verdict", VERDICTS, "publishable with no open blocker or major"),
                 word("pass", passes, "what this pass was")]
    if kind == STEP:
        keys.append(ident("step", STEP_NAME, "a step's name",
                          "the step of the flow this report is for"))
    keys += [line("by", "who wrote the report"), line("model", "the model that wrote it")]
    if kind == DESIGN:
        keys.append(count("design", "the frozen version of the design the pass read"))
    if kind in (BUILD, STEP):
        keys += [ident("stage", questions.STAGE, "B<n>", "the one build the pass read"),
                 ident("head", HEAD, "a commit's whole hash, 40 hex digits",
                       "the tip the pass read: the commit's whole hash")]
    if kind == MAJORS:
        keys.append(idents("stages", questions.STAGE, "B<n>",
                           "the builds whose decisions this report declares",
                           required=True, fewest=1))
    return tuple(keys)


def _tables(kind: str) -> tuple[Table, ...]:
    return {DESIGN: (FINDING,), BUILD: (FINDING, WAIT, DECISION), STEP: (FINDING,),
            MAJORS: (MAJORS_DECISION,), RETRO: ()}[kind]


def _names(kind: str) -> list[str]:
    return [key.name for key in _keys(kind)] + [one.name for one in _tables(kind)]


def _elsewhere(kind: str) -> dict[str, str]:
    """For each key another kind of report has and this one does not, the kinds that
    have it, in words."""
    mine = _names(kind)
    theirs: dict[str, list[str]] = {}
    for other in KINDS:
        for name in _names(other):
            if name not in mine:
                theirs.setdefault(name, []).append(other)
    return {name: f"a {kind} report has none; it is a key of a "
                  f"{' or '.join(kinds)} report" for name, kinds in theirs.items()}


#: The schema of the top of a report's block, by kind. A prepared answer is no report
#: and has no row here.
SCHEMAS: dict[str, Table] = {
    kind: Table("", keys=_keys(kind), tables=_tables(kind), elsewhere=_elsewhere(kind))
    for kind in KINDS}


def empty(kind: str) -> str:
    """The empty block of a report of `kind`, one of `KINDS`: `kind` filled in, every
    other key with an empty value and its comment, and every table once. Raises
    `BadArgument` for a word that is no kind of report."""
    if kind not in SCHEMAS:
        raise BadArgument(f"{kind!r} is no kind of report",
                          f"name one of {', '.join(KINDS)}")
    return datablock.empty(SCHEMAS[kind])


# Reading.

def read(file: Path | str) -> ReportBlock:
    """The data block of the report `file`.

    Raises `BadBlock` when the file has no one data block that is TOML, when the block
    does not say which kind of report it is, and when it does not fit that kind: the
    problems of its keys and values first, and once those are right the ones between
    its entries.
    """
    found = datablock.find(file, WHAT)
    data = found.data
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in SCHEMAS:
        says = _keys(DESIGN)[0]
        if "kind" not in data:
            problem = Problem(found.line, "the block has no `kind`: a report says "
                                          "which kind it is", f"add kind = {says.takes}")
        else:
            problem = Problem(found.line_of("kind"), f"`kind` {says.check(kind)}",
                              f"give kind = {says.takes}")
        raise BadBlock(found.file, WHAT, [problem])
    problems = datablock.check(found, SCHEMAS[kind]) or _between(found, kind)
    if problems:
        raise BadBlock(found.file, WHAT, problems)
    return ReportBlock(
        file=found.file, sha256=found.sha256, kind=kind, by=data["by"],
        model=data["model"], verdict=data.get("verdict"), pass_=data.get("pass"),
        design=data.get("design"), stage=data.get("stage"), head=data.get("head"),
        step=data.get("step"), stages=tuple(data.get("stages", ())),
        findings=tuple(
            Finding(id=one["id"], severity=one["severity"], state=one["state"],
                    text=one["text"], **_placed(found, ("finding", number), one))
            for number, one in enumerate(data.get("finding", ()))),
        waits=tuple(
            Wait(id=one["id"], text=one["text"], **_placed(found, ("wait", number), one))
            for number, one in enumerate(data.get("wait", ()))),
        decisions=tuple(
            Decision(id=one["id"], weight=one["weight"], forecloses=one["forecloses"],
                     text=one["text"], stage=one.get("stage"),
                     **_placed(found, ("decision", number), one))
            for number, one in enumerate(data.get("decision", ()))),
        **_placed(found, (), data))


def _placed(found: Found, at: tuple, entry: dict) -> dict:
    return {"line": found.line_of(*at), "lines": found.lines(at, entry)}


def _between(found: Found, kind: str) -> list[Problem]:
    """The problems between the entries of a block whose keys and values are right."""
    data = found.data
    problems: list[Problem] = []
    for name in ("finding", "wait", "decision"):
        problems += _twice(found, name, data.get(name, ()))
    for number, decision in enumerate(data.get("decision", ())):
        at = ("decision", number)
        if decision["forecloses"] and decision["weight"] != MAJOR:
            problems.append(Problem(
                found.line_of(*at, "weight"),
                f"the decision {decision['id']} forecloses something and its weight is "
                f"{decision['weight']}: a decision that forecloses is major",
                f'give it weight = "{MAJOR}", or forecloses = false when it rules '
                "nothing out"))
        if kind == MAJORS and decision["stage"] not in data["stages"]:
            problems.append(Problem(
                found.line_of(*at, "stage"),
                f"the decision {decision['id']} is of the stage {decision['stage']}, "
                f"which `stages` does not name ({', '.join(data['stages'])})",
                f"add {decision['stage']} to stages, or give the decision the stage "
                "it was made in"))
    if data.get("verdict") == PUBLISHABLE:
        for number, finding in enumerate(data.get("finding", ())):
            if finding["state"] == OPEN and finding["severity"] in (BLOCKER, MAJOR):
                problems.append(Problem(
                    found.line_of("finding", number),
                    f"the verdict is {PUBLISHABLE} and the finding {finding['id']} is "
                    f"an open {finding['severity']}: the two contradict each other",
                    f'give verdict = "{FOLD_FIRST}", or the finding the state it has '
                    "at what the pass read"))
    return problems


def _twice(found: Found, name: str, entries) -> list[Problem]:
    """A problem for each entry of the `[[name]]` list whose id an earlier one has. A
    decision that names its stage is told apart by the two together."""
    first: dict[tuple, int] = {}
    problems = []
    for number, one in enumerate(entries):
        key = (one.get("stage"), one["id"])
        if key in first:
            whose = f" of {one['stage']}" if one.get("stage") else ""
            problems.append(Problem(
                found.line_of(name, number, "id"),
                f"the id {one['id']}{whose} is used twice: a `[[{name}]]` on line "
                f"{found.line_of(name, first[key])} has it too",
                f"give each `[[{name}]]` an id of its own"))
        else:
            first[key] = number
    return problems


__all__ = ["WHAT", "DESIGN", "BUILD", "STEP", "MAJORS", "RETRO", "KINDS", "PUBLISHABLE",
           "FOLD_FIRST", "VERDICTS", "FULL", "RECHECK", "DRIFT", "BLOCKER", "MAJOR",
           "MINOR", "NOTE", "SEVERITIES", "OPEN", "FIXED", "NO_ACTION", "STATES",
           "WEIGHTS", "FINDING_ID", "WAIT_ID", "DECISION_ID", "HEAD", "STEP_NAME",
           "Finding", "Wait", "Decision", "ReportBlock", "FINDING", "WAIT", "DECISION",
           "MAJORS_DECISION", "SCHEMAS", "empty", "read"]
