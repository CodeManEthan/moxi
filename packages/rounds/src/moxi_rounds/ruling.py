"""Recording a ruling: what the operator decided, in a sentence, with where he said it.

`rule("<project>/<slug>", text=..., record=..., actor=...)` appends one `ruling.recorded`
event through the write path and returns the ruling as recorded.

**Where it goes.** A round's ruling is `C<n>` in the round's log. A standing ruling, one
meant to last past a round, is `S<n>` in the project's log, the project's register: it is
recorded with `standing=True` on a round, or by naming a project and no round. Each log
numbers its own rulings from 1. A standing ruling recorded from a round takes the round's
lock and then the project's, and appends to the project's log only.

**The pointer.** Every ruling is the operator's, so every one carries a pointer to where
he said it, one token of a shape `pointers` knows. A ruling with no pointer, or one of no
known shape, is refused with nothing written.

**The two kinds.** A ruling of substance (`SUBSTANCE`) says what is built. An act on the
process (`PROCESS`) changes a record: a skip, a park, a withdrawal and the like. The kind
is a field of the event. A plain ruling, a sentence that rules no item, is of substance.

**What it applies to.** A round's plain ruling applies to every stage of the round
(`EVERY_STAGE`), or to the stages it names. A standing ruling may name a scope: repos, as
project keys, each with a path glob or without.

**Forms.** What is ruled is a `Form`: the kind, the item, the form's own event fields and
its own gates, and the other events the ruling makes. `_plain` builds the form of a plain
ruling. A ruling of an item is built by the first function of `_ITEM_FORMS` that knows
the item.

**Questions.** Four items are a question of the round, `Q<n>`, or of the project,
`P<n>`. The question's id alone is its answer, a ruling of substance whose sentence is
the answer and which applies to the stages the question held up. `park:Q<n>` sets it
aside, until a stage is finished or a round is closed when `until` says so;
`narrow:Q<n>=LIST` makes it hold up fewer stages, or `none`; `withdraw:Q<n>` takes it
back. Those three are acts on the process. Each appends the ruling and then the change
to the question, which names the ruling. A question that is already ruled or withdrawn
takes none of the four.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from . import folders, pointers, questions, write
from . import settings as own
from .errors import BadArgument, NoLog, NoRound, Unmet

#: The kind of a ruling that says what is built.
SUBSTANCE = "substance"
#: The kind of a ruling that changes a record of the process.
PROCESS = "process"

#: What `stages` holds for a ruling that applies to every stage of its round.
EVERY_STAGE = "all"

#: The length, in characters, past which a ruling gets a note that it is long.
LONG = 400

#: A stage's id.
STAGE = questions.STAGE

# A project's key, as the projects list writes one.
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_ID = re.compile(r"[CS]([1-9][0-9]*)")


@dataclass(frozen=True)
class Asked:
    """What a call to `rule` gave, as a form's builder reads it: the round's or the
    project's `name`, whether the ruling is `standing`, the `text` as given (None when
    there was none), and the `stages` named, parsed (None when none were). `slug` is the
    round's, None for a ruling on a project. `record` is the pointer as given, `until`
    what a parking waits for, and `scoped` whether a scope was given."""

    name: str
    standing: bool
    text: str | None
    stages: tuple[str, ...] | None
    slug: str | None = None
    record: str | None = None
    until: str | None = None
    scoped: bool = False


@dataclass(frozen=True)
class Form:
    """One thing that is ruled: its `kind` (`SUBSTANCE` or `PROCESS`), the item it
    `rules` (None for a ruling that stands on its own), the event `fields` that are the
    form's own, and the `gates` it adds, each as the write path calls one. `found`
    gives the fields that depend on what the logs hold: it is called with what was read
    under the lock, once every gate holds. `changes` gives the other events the ruling
    makes, to the records it rules: it is called with the same and the ruling's id, and
    its events are appended after the ruling, to the log the ruling goes to."""

    kind: str
    rules: str | None = None
    fields: Mapping[str, object] = field(default_factory=dict)
    gates: tuple[write.Gate, ...] = ()
    found: Callable[[write.View], Mapping[str, object]] | None = None
    changes: Callable[[write.View, str], Iterable[write.Entry]] | None = None


@dataclass(frozen=True)
class Ruled:
    """A ruling as recorded: its `id`, the `name` of the round or project whose log
    holds it, whether it is `standing`, its `text` read back from the event, and the
    `event` itself. `long` says the text is past `LONG` characters. `failed` pairs each
    after-append callback that raised with what it raised."""

    id: str
    name: str
    standing: bool
    text: str | None
    event: dict
    long: bool = False
    failed: tuple[tuple[Callable, Exception], ...] = ()


def rule(target: str, *, actor: str, record: str | None = None, text: str | None = None,
         reason: str | None = None, standing: bool = False, scope: str | None = None,
         stages: str | None = None, item: str | None = None,
         until: str | None = None, settings=None,
         wait_s: float = write.WAIT_S, at: str | None = None) -> Ruled:
    """Record one ruling on `target`, a round `<project>/<slug>` or a project's key, and
    return it as recorded.

    - `actor`: who records it, the event's actor.
    - `record`: the pointer to where the operator said it.
    - `text`: the sentence. A plain ruling needs one.
    - `reason`: why, in short.
    - `standing`: on a round, record it in the project's register. A ruling on a project
      is always standing.
    - `scope`: a standing ruling's repos and paths, `KEY` or `KEY:GLOB`, several
      separated by commas.
    - `stages`: the stages of the round a round's ruling applies to, `B2,B3`; every stage
      when left out.
    - `item`: what is ruled, or None for a plain ruling: a question's id for its
      answer, or `park:`, `narrow:` or `withdraw:` and the question.
    - `until`: with `park:`, what the question waits for: a stage of the round, `B2`,
      or another round, `round:<project>/<slug>`.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for each lock; `at`: the event's time.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject` or
    `NoFolder` for a project with no rounds folder, `NoRound` for a round that was never
    opened, and `Refused` when a gate does not hold, the pointer's among them: an item
    that names no question, or a question already ruled or withdrawn. A refusal leaves
    both logs as they were.
    """
    if not isinstance(target, str) or not target.strip():
        raise BadArgument("nothing says what the ruling is on",
                          "name a round as <project>/<slug>, or a project by its key")
    project, slug = folders.split(target) if "/" in target else (target, None)
    standing = standing or slug is None
    if reason is not None and not reason.strip():
        raise BadArgument("--reason says nothing", "give the reason, or leave --reason "
                                                   "out")
    if scope is not None and not standing:
        raise BadArgument("--scope is for a standing ruling, and this one is the "
                          "round's own", "add --standing, or leave --scope out")
    if stages is not None and standing:
        raise BadArgument("--stages names stages of a round, and a standing ruling is "
                          "the project's", "leave --stages out, or record the ruling on "
                                           "the round without --standing")
    if until is not None and not (item or "").startswith(_PARK):
        raise BadArgument("--until says what a parked question waits for",
                          f"give it with {_PARK}Q<n>, or leave --until out")
    scoped = None if scope is None else parse_scope(scope)
    staged = None if stages is None else parse_stages(stages)

    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = home.project if slug is None else f"{home.project}/{slug}"
    form = _form(item, Asked(name=name, standing=standing, text=text, stages=staged,
                             slug=slug, record=record, until=until,
                             scoped=scope is not None))
    # The command that records it, with the sentence only where the form has one.
    again = (f"moxi rounds rule {name}{'' if item is None else ' ' + item}"
             f"{' --text TEXT' if 'text' in form.fields else ''} "
             f"--record PTR{' --standing' if standing and slug is not None else ''}")

    def has_a_pointer(view):
        if record is None:
            return Unmet(
                "a ruling is the operator's and needs a pointer to where he said it; "
                f"PTR below is one token: {pointers.SHAPES}", again)
        return pointers.unmet(record, again)

    def recorded(view, result):
        held = view.project_state if standing else view.state
        fields = {"id": _next_id("S" if standing else "C", held.rulings),
                  **form.fields, **(form.found(view) if form.found else {}),
                  "record": record, "kind": form.kind}
        if form.rules is not None:
            fields["rules"] = form.rules
        if reason is not None:
            fields["reason"] = reason
        if scoped is not None:
            fields["scope"] = scoped
        ruled = write.Entry("ruling.recorded", fields,
                            log=write.PROJECT if standing else write.ROUND)
        # The ruling goes first: were the verb stopped between the two, a ruling with
        # its record unchanged is ruled again, where a changed record with no ruling
        # could not be.
        return [ruled, *(form.changes(view, fields["id"]) if form.changes else ())]

    # Whether a stage named here is a stage of the round is a gate for this list, once
    # the round's state holds its stages.
    try:
        written = write.record(write.Where(home.project, home.folder, slug), actor=actor,
                               gates=[has_a_pointer, *form.gates], event=recorded,
                               with_project=standing, wait_s=wait_s, at=at)
    except NoLog:
        # Only a round's log can be missing here: a project's reads as empty.
        raise NoRound(name) from None
    event = written.events[0]
    said = event.get("text")
    return Ruled(id=event["id"], name=name, standing=standing, text=said, event=event,
                 long=said is not None and len(said) > LONG, failed=written.failed)


# -- the forms ---------------------------------------------------------------------------

def _plain(asked: Asked) -> Form:
    """A plain ruling: a sentence of substance that rules no item."""
    if asked.text is None or not asked.text.strip():
        raise BadArgument("a ruling needs its sentence",
                          "say what was ruled with --text TEXT")
    fields: dict[str, object] = {"text": asked.text}
    if not asked.standing:
        fields["stages"] = EVERY_STAGE if asked.stages is None else list(asked.stages)
    return Form(kind=SUBSTANCE, fields=fields)


# -- the forms that rule a question ------------------------------------------------------

_PARK = "park:"
_NARROW = "narrow:"
_WITHDRAW = "withdraw:"
_QUESTION = re.compile(r"[QP][1-9][0-9]*")


def _held(view: write.View, asked: Asked):
    """The state of the log the ruling goes to, which is the one that holds the
    question."""
    return view.project_state if asked.standing else view.state


def _of_a_question(asked: Asked, *, act: bool) -> None:
    """Refuse what a ruling of a question can't be given. `act` says the ruling is an
    act on the process, not an answer."""
    if asked.stages is not None:
        raise BadArgument("--stages is for a plain ruling; a ruling of a question "
                          "applies to the stages the question holds up",
                          "leave --stages out")
    if asked.standing and asked.slug is not None:
        raise BadArgument("--standing is for a plain ruling; a ruling of a round's "
                          "question is the round's", "leave --standing out")
    if act and asked.scoped:
        raise BadArgument("--scope is for a ruling that says what is built",
                          "leave --scope out")
    if act and asked.text is not None and not asked.text.strip():
        raise BadArgument("--text says nothing", "give the sentence, or leave --text "
                                                 "out")


def _can_be_ruled(question_id: str, asked: Asked, *, answer: bool = False) -> write.Gate:
    """The gate every ruling of a question has: the log holds the question, and it is
    not ruled or withdrawn already."""
    def gate(view):
        question = questions.find(_held(view, asked), question_id)
        if question is None:
            listing = (f"moxi rounds show {asked.name}" if asked.slug is not None else
                       f"moxi rounds list {asked.name} --questions")
            return Unmet(f"{asked.name} has no question {question_id}", listing)
        if question.state not in (questions.RULED, questions.WITHDRAWN):
            return None
        by = question.ruling or ("S<n>" if asked.standing else "C<n>")
        if answer and question.state == questions.RULED:
            return Unmet(
                f"{question_id} is already ruled, by {by}; a second answer replaces "
                "the first and names it",
                f"moxi rounds rule {asked.name} {question_id} --text TEXT --record PTR "
                f"--supersedes {by}")
        return Unmet(f"{question_id} is already {question.state}, by {by}; it takes "
                     f"another ruling only once {by} is taken back, which makes it "
                     "open again",
                     f"moxi rounds rule {asked.name} revoke:{by} --record PTR")
    return gate


def _changes(question_id: str, asked: Asked, change: Mapping[str, object]):
    """The change a ruling makes to its question, as `Form.changes`."""
    def changes(view, ruling_id):
        return [write.Entry("question.changed",
                            {"id": question_id, **change, "ruling": ruling_id,
                             "record": asked.record},
                            log=write.PROJECT if asked.standing else write.ROUND)]
    return changes


def _said(asked: Asked) -> dict[str, object]:
    return {} if asked.text is None else {"text": asked.text}


def _answer(item: str, asked: Asked) -> Form | None:
    """The answer to a question: a ruling of substance that applies to the stages the
    question held up when it was answered."""
    if not _QUESTION.fullmatch(item):
        return None
    _of_a_question(asked, act=False)
    if asked.text is None or not asked.text.strip():
        raise BadArgument("an answer needs its sentence",
                          "say what he answered with --text TEXT")

    def found(view):
        if asked.standing:
            return {}
        return {"stages": questions.stored(questions.find(view.state, item).blocks)}

    return Form(kind=SUBSTANCE, rules=item, fields={"text": asked.text},
                gates=(_can_be_ruled(item, asked, answer=True),), found=found,
                changes=_changes(item, asked, {"state": questions.RULED}))


def _park(item: str, asked: Asked) -> Form | None:
    """Setting a question aside, until a stage is finished or a round is closed when
    the ruling says so."""
    question_id = item[len(_PARK):]
    if not item.startswith(_PARK) or not _QUESTION.fullmatch(question_id):
        return None
    _of_a_question(asked, act=True)
    until = asked.until
    change: dict[str, object] = {"state": questions.PARKED}
    gates = [_can_be_ruled(question_id, asked)]
    if until is not None:
        change["until"] = until
        if until.startswith(questions.UNTIL_ROUND):
            folders.split(until[len(questions.UNTIL_ROUND):])
        elif not questions.STAGE.fullmatch(until):
            raise BadArgument(f"--until {until!r} is neither a stage nor a round",
                              "name a stage of the round as B<n>, or another round as "
                              "round:<project>/<slug>")
        elif asked.slug is None:
            raise BadArgument("a project has no stage for its question to wait for",
                              "name a round as --until round:<project>/<slug>, or "
                              "leave --until out")
        else:
            def waits_for_a_stage_of_the_round(view):
                if not questions.unrecorded((until,), view.state):
                    return None
                return Unmet(f"{until} is not a stage of {asked.name}: "
                             f"{questions.stages_said(view.state)}",
                             f"moxi rounds rule {asked.name} {item} --record PTR "
                             "[--until B<n>|round:<project>/<slug>]")
            gates.append(waits_for_a_stage_of_the_round)
    return Form(kind=PROCESS, rules=item, fields=_said(asked), gates=tuple(gates),
                changes=_changes(question_id, asked, change))


def _narrow(item: str, asked: Asked) -> Form | None:
    """Making a round's question hold up fewer stages, or none."""
    question_id, equals, value = item[len(_NARROW):].partition("=")
    if not item.startswith(_NARROW) or not _QUESTION.fullmatch(question_id):
        return None
    if asked.slug is None:
        raise BadArgument("a project's question holds up no stage, so there is "
                          "nothing to narrow", "rule it, park it or withdraw it")
    if not equals:
        raise BadArgument(f"{item!r} does not say what the question is to hold up",
                          f"say {_NARROW}{question_id}=B2,B3, or "
                          f"{_NARROW}{question_id}={questions.NONE}")
    _of_a_question(asked, act=True)
    wanted = questions.parse_blocks(value, _NARROW + question_id + "=")

    def names_stages_of_the_round(view):
        missing = questions.unrecorded(wanted, view.state)
        if not missing:
            return None
        return Unmet(f"{', '.join(missing)} is not a stage of {asked.name}: "
                     f"{questions.stages_said(view.state)}",
                     f"moxi rounds show {asked.name}")

    def narrows(view):
        question = questions.find(view.state, question_id)
        if question is None or questions.holds_every(question.blocks, wanted):
            return None
        return Unmet(
            f"{question_id} holds up {questions.said(question.blocks)}, and "
            f"{questions.said(wanted)} would hold up a stage that does not; a ruling "
            "narrows what a question holds up, and anyone may widen it",
            f"moxi rounds mark {asked.name} {question_id} --blocks "
            f"{questions.said(wanted)}")

    return Form(kind=PROCESS, rules=item, fields=_said(asked),
                gates=(_can_be_ruled(question_id, asked), names_stages_of_the_round,
                       narrows),
                changes=_changes(question_id, asked,
                                 {"blocks": questions.stored(wanted)}))


def _withdraw(item: str, asked: Asked) -> Form | None:
    """Taking a question back: it is no longer asked."""
    question_id = item[len(_WITHDRAW):]
    if not item.startswith(_WITHDRAW) or not _QUESTION.fullmatch(question_id):
        return None
    _of_a_question(asked, act=True)
    return Form(kind=PROCESS, rules=item, fields=_said(asked),
                gates=(_can_be_ruled(question_id, asked),),
                changes=_changes(question_id, asked, {"state": questions.WITHDRAWN}))


#: The builders of the forms that rule an item, each `fn(item, asked) -> Form | None`,
#: None when the item is not its own. The first that knows the item builds the form.
_ITEM_FORMS: tuple[Callable[[str, Asked], Form | None], ...] = (
    _answer, _park, _narrow, _withdraw)


def _form(item: str | None, asked: Asked) -> Form:
    if item is None:
        return _plain(asked)
    for build in _ITEM_FORMS:
        form = build(item, asked)
        if form is not None:
            return form
    raise BadArgument(f"{item!r} is nothing a ruling can be on",
                      "leave it out for a plain ruling: --text TEXT --record PTR")


def _next_id(letter: str, rulings: Iterable) -> str:
    """The next id of a log's rulings: one past the highest it holds, so an id is never
    given twice even when a line was taken out by hand."""
    numbers = [int(found.group(1)) for found in
               (_ID.fullmatch(ruling.id or "") for ruling in rulings) if found]
    return f"{letter}{max(numbers, default=0) + 1}"


# -- what a ruling applies to ------------------------------------------------------------

def parse_stages(text: str) -> tuple[str, ...]:
    """The stage ids of `B2,B3`, in the order given. Raises `BadArgument` for an empty
    list, an id that is not `B<n>`, or one named twice."""
    ids = tuple(part.strip() for part in text.split(","))
    wrong = [one for one in ids if not STAGE.fullmatch(one)]
    if wrong or len(set(ids)) != len(ids):
        problem = (f"{wrong[0]!r} is not a stage's id" if wrong else
                   "a stage is named twice")
        raise BadArgument(f"--stages {text!r} can't be read: {problem}",
                          "name stages as B<n>, separated by commas: --stages B2,B3")
    return ids


def parse_scope(text: str) -> list[dict]:
    """The entries of a scope, in the order given: `{"repo": key}` for a repo named
    alone and `{"repo": key, "path": glob}` for `KEY:GLOB`. Raises `BadArgument` for an
    empty entry, a repo that can't be a project's key, a path that is empty, absolute or
    steps up out of the repo, or an entry given twice."""
    entries = []
    for part in text.split(","):
        repo, colon, path = part.strip().partition(":")
        problem = None
        if not _KEY.fullmatch(repo):
            problem = f"{repo!r} can't be a project's key"
        elif colon and (not path or path.startswith(("/", "~"))
                        or any(step in ("", "..") for step in path.split("/"))):
            problem = f"{path!r} is not a path inside the repo {repo}"
        entry = {"repo": repo, "path": path} if colon else {"repo": repo}
        if problem is None and entry in entries:
            problem = f"{part.strip()!r} is given twice"
        if problem is not None:
            raise BadArgument(f"--scope {text!r} can't be read: {problem}",
                              "name each repo by its project key, alone or with a path "
                              "glob, separated by commas: --scope KEY:GLOB,KEY")
        entries.append(entry)
    return entries


__all__ = ["SUBSTANCE", "PROCESS", "EVERY_STAGE", "LONG", "STAGE", "Asked", "Form",
           "Ruled", "rule", "parse_stages", "parse_scope"]
