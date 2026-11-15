"""Corrections: replacing a ruling and taking one back.

Any ruling of the operator's can be corrected by a later ruling of his, which carries
its own pointer. A correction is one `ruling.recorded` event that names the earlier
ruling: `supersedes` when the same thing is ruled again, `revokes` when the ruling is
taken back and nothing is put in its place. Both rulings stay in the log. The fold marks
the earlier one (`Ruling.replaced_by`, `Ruling.revoked_by`), and every reader takes the
rulings that stand: `live` gives them.

A correction changes what is read from then on and undoes no act. A ruling that was
replaced or taken back is corrected no further: the ruling that stands in its place is.

**Kinds.** What a correction may do depends on what the earlier ruling ruled. `KINDS`
is the table, one `Kind` for each sort of ruling this code knows:

- `item` tells whether a ruling is of the kind, from the item it rules, and gives the
  thing ruled: two rulings are of the same thing when their kinds give equal values. A
  second ruling of a thing names the first, and replaces it.
- `undo` gives the fields that the ruling that takes one back carries beside `revokes`.
  A ruling that changed a question names the question, and the fold then reads the
  question from the changes that are left; an answer taken back also says the question
  is open, whatever it was when he answered. None means the kind is never taken back,
  and `instead` then gives the refusal, with the command that is the way back.
- `again` says whether the kind is ruled a second time at all.

A ruling of no kind in the table is neither replaced nor taken back: the verb refuses
it and guesses nothing. A new kind of ruling is one more `Kind` in `KINDS`. What it
changed is read from then on by whoever reads rulings through `live`; when taking it
back must also change a record, `undo` gives the fields that say so and the fold
reads them from the event.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from . import ending, flows, questions
from . import state as states
from .errors import Unmet

#: What starts the item of a ruling that takes another back; the ruling's id follows.
REVOKE = "revoke:"
#: What starts the item of a ruling that moved a question to another log.
MOVE = "move:"
#: What starts the items of the rulings that park a question, narrow what it holds up,
#: and withdraw it; the question's id follows.
PARK = "park:"
NARROW = "narrow:"
WITHDRAW = "withdraw:"
#: What starts the item of a read-back, the operator's word that he read the list of
#: rulings not yet shown to him; the time he read it through follows.
READBACK = "readback="

#: What starts the item of a skip, the ruling that a step is not done; the step's name
#: follows, and for a step of one stage the stage's id.
SKIP = "skip:"
#: What starts the item of a ruling that moves the round to another flow; the flow's
#: name follows.
FLOW = "flow:"

#: What starts the items of the rulings that take a stage out of the plan, and one
#: stage off what another waits for.
UNSTAGE = questions.UNSTAGE
UNAFTER = questions.UNAFTER

#: The thing a ruling that stands on its own rules: any such ruling may replace another.
ALONE = ""

#: The id of a ruling: `C<n>` in a round's log, `S<n>` in a project's.
ID = re.compile(r"[CS]([1-9][0-9]*)")

_QUESTION = re.compile(r"[QP][1-9][0-9]*")


@dataclass(frozen=True)
class Kind:
    """One sort of ruling, and how it is corrected.

    - `name`: what it is, in words, for a refusal.
    - `item`: `fn(rules) -> str | None`, the thing ruled when `rules` (a ruling's item,
      None for a ruling that stands on its own) is of this kind, else None.
    - `undo`: `fn(ruling) -> fields`, what the ruling that takes `ruling` back carries
      beside `revokes`; None for a kind that is never taken back.
    - `instead`: for such a kind, `fn(ruling, state, name) -> Unmet`, the refusal:
      why, and the command that is the way back. `state` is the log's and `name` the
      round or project it is of.
    - `again`: whether the thing is ruled a second time, replacing the first.
    """

    name: str
    item: Callable[[str | None], str | None]
    undo: Callable[[states.Ruling], Mapping[str, object]] | None = None
    instead: Callable[[states.Ruling, states.State, str], Unmet] | None = None
    again: bool = True


def _starts(prefix: str, *,
            upto: str | None = None) -> Callable[[str | None], str | None]:
    """An `item` for the rulings whose item starts with `prefix`. The thing ruled is
    the item, cut at `upto` when what follows it is the value given and not the thing."""
    def item(rules):
        if rules is None or not rules.startswith(prefix):
            return None
        return rules if upto is None else rules.partition(upto)[0]
    return item


def _names_its_question(ruling: states.Ruling) -> Mapping[str, object]:
    # The fold reads the question from the changes that are left.
    return {} if ruling.question is None else {"question": ruling.question}


def _reopens_its_question(ruling: states.Ruling) -> Mapping[str, object]:
    # An answer taken back leaves the question open, also when it was parked before.
    return {"question": ruling.rules, "state": questions.OPEN}


def _move_it_back(ruling: states.Ruling, state: states.State, name: str) -> Unmet:
    question = questions.find(state, ruling.question or "")
    became = None if question is None else question.moved_to
    if became is None:
        return Unmet(f"{ruling.id} moved a question to another log, and a move is not "
                     "taken back: the question is moved again from where it is",
                     f"moxi rounds show {name}")
    if ":" in became:
        # A question of the project's list that a round adopted with a list of stages.
        return Unmet(
            f"{ruling.id} brought {ruling.question} into {became.partition(':')[0]} as "
            f"{became.partition(':')[2]}, and a move is not taken back: the question "
            "is moved to the project's list again",
            f"moxi rounds ask {name} --move {became} --record PTR")
    return Unmet(
        f"{ruling.id} moved {ruling.question} to the project's list, where it is "
        f"{became}, and a move is not taken back: the question is adopted again",
        f"moxi rounds ask {name} --adopt {became}")


def _rule_it_again(ruling: states.Ruling, state: states.State, name: str) -> Unmet:
    first = find(state, ruling.revokes or "")
    item = "ITEM" if first is None else first.rules
    said = "" if first is not None and first.text is None else " --text TEXT"
    return Unmet(
        f"{ruling.id} took {ruling.revokes} back, and a taking back is not itself "
        f"taken back: what {ruling.revokes} ruled is ruled again",
        f"moxi rounds rule {name}{'' if item is None else ' ' + item}{said} "
        "--record PTR")


def _move_it_again(ruling: states.Ruling, state: states.State, name: str) -> Unmet:
    moved = next((event for event in state.history
                  if event["type"] == "ruling.recorded" and event.get("id") == ruling.id),
                 {})
    return Unmet(
        f"{ruling.id} moved {name} to the flow {(ruling.rules or '')[len(FLOW):]}, "
        "and a change of flow is neither taken back nor replaced: the round is "
        "moved again, to the flow it had or to another",
        f"moxi rounds rule {name} {FLOW}{moved.get('was') or 'NAME'} --record PTR")


def _declare_it_again(ruling: states.Ruling, state: states.State, name: str) -> Unmet:
    stage = (ruling.rules or "")[len(UNSTAGE):]
    designed = flows.has(state.steps, "design")
    how = ("in its design, frozen again" if designed else
           "when the work is commissioned")
    return Unmet(
        f"{ruling.id} took {stage} out of the plan of {name}, and a stage that left "
        "does not come back, so the ruling is neither taken back nor replaced: the "
        f"same work is declared again under a new id, {how}",
        f"moxi rounds {'fold' if designed else 'show'} {name}")


def _mark_it_after(ruling: states.Ruling, state: states.State, name: str) -> Unmet:
    stage, _equals, waited = (ruling.rules or "")[len(UNAFTER):].partition("=")
    return Unmet(
        f"{ruling.id} took {waited} off what {stage} waits for, and that is neither "
        "taken back nor replaced: the wait is added again, which anyone may do",
        f"moxi rounds mark {name} {stage} --after {waited}")


def _read_back_again(ruling: states.Ruling, state: states.State, name: str) -> Unmet:
    return Unmet(
        f"{ruling.id} is his word that he read the list of rulings through "
        f"{(ruling.rules or '')[len(READBACK):]}, and a read-back is neither taken "
        "back nor replaced: each one is a ruling of its own, and the newest covers "
        "every one before it",
        f"moxi rounds rule {name} {READBACK}TIME --record PTR")


#: Every kind of ruling that is corrected, or refused with its way back. The first
#: whose `item` knows a ruling's item is the ruling's kind.
KINDS: tuple[Kind, ...] = (
    Kind("a ruling that stands on its own",
         lambda rules: ALONE if rules is None else None, undo=lambda ruling: {}),
    Kind("an answer",
         lambda rules: rules if rules and _QUESTION.fullmatch(rules) else None,
         undo=_reopens_its_question),
    Kind("a parking", _starts(PARK), undo=_names_its_question),
    Kind("a narrowing", _starts(NARROW, upto="="), undo=_names_its_question),
    Kind("a withdrawal", _starts(WITHDRAW), undo=_names_its_question),
    Kind("a move", _starts(MOVE), instead=_move_it_back, again=False),
    Kind("a taking back", _starts(REVOKE), instead=_rule_it_again, again=False),
    Kind("a read-back", _starts(READBACK, upto="="), instead=_read_back_again,
         again=False),
    # Readers take the skips that stand, so one taken back leaves its step due again
    # with nothing more said.
    Kind("a skip", _starts(SKIP), undo=lambda ruling: {}),
    Kind("a change of flow", _starts(FLOW), instead=_move_it_again, again=False),
    Kind("a stage taken out of the plan", _starts(UNSTAGE), instead=_declare_it_again,
         again=False),
    Kind("a wait taken off a stage", _starts(UNAFTER), instead=_mark_it_after,
         again=False),
)


def kind_of(rules: str | None) -> Kind | None:
    """The kind of a ruling whose item is `rules`, or None for one no kind knows."""
    for kind in KINDS:
        if kind.item(rules) is not None:
            return kind
    return None


def item_of(rules: str | None) -> str | None:
    """The thing a ruling whose item is `rules` rules, as its kind gives it; None for
    a ruling of no known kind."""
    kind = kind_of(rules)
    return None if kind is None else kind.item(rules)


# -- what the rulings of a log say now ---------------------------------------------------

def stands(ruling: states.Ruling) -> bool:
    """Whether `ruling` was neither replaced nor taken back."""
    return ruling.replaced_by is None and ruling.revoked_by is None


def live(rulings: Iterable[states.Ruling]) -> tuple[states.Ruling, ...]:
    """The rulings that stand, oldest first: the ones every reader takes."""
    return tuple(ruling for ruling in rulings if stands(ruling))


def find(state: states.State, ruling_id: str) -> states.Ruling | None:
    """The ruling of `state` with that id, or None."""
    for ruling in state.rulings:
        if ruling.id == ruling_id:
            return ruling
    return None


def standing(state: states.State, item: str) -> states.Ruling | None:
    """The newest ruling of `state` that stands and rules the thing `item`, or None."""
    for ruling in reversed(state.rulings):
        if stands(ruling) and item_of(ruling.rules) == item:
            return ruling
    return None


def put_by(question: states.Question) -> str | None:
    """The id of the ruling that put `question` in the state it reads as, or None when
    no ruling did: it was asked so, or a change that is no ruling made it so."""
    for change in reversed(question.changes):
        if change.undone_by is None and change.state is not None:
            return change.ruling
    return None


def ended(state: states.State) -> str | None:
    """How the round `state` is of ended, `closed` or `dropped`, or None while it has
    not."""
    return ending.how(state)


# -- the gates ---------------------------------------------------------------------------

def _listing(name: str, named: str | None = None) -> str:
    """The command that shows the rulings of the round `name`. No command lists a
    project's register, so for a project it is `named`, the command with the place of
    the id left open, which is the taking back when none is given."""
    if "/" in name:
        return f"moxi rounds show {name}"
    return named or f"moxi rounds rule {name} {REVOKE}S<n> --record PTR"


def _spent(ruling: states.Ruling, name: str) -> Unmet | None:
    """Why `ruling` is corrected no further, when it was replaced or taken back."""
    if ruling.revoked_by is not None:
        return Unmet(f"{ruling.id} was taken back already, by {ruling.revoked_by}; "
                     "what it ruled is ruled again, naming no earlier ruling",
                     _listing(name))
    if ruling.replaced_by is not None:
        return Unmet(f"{ruling.id} was replaced by {ruling.replaced_by}, which is the "
                     "ruling that stands in its place and the one to correct",
                     f"moxi rounds rule {name} {REVOKE}{ruling.replaced_by} "
                     "--record PTR")
    return None


def can_be_taken_back(ruling_id: str, *, name: str,
                      held: Callable[[object], states.State]) -> Callable:
    """The gate of a ruling that takes `ruling_id` back: the log holds that ruling, it
    stands, and its kind is one that is taken back. `name` is the round or project
    and `held(view)` gives the state of the log the ruling is in."""
    def gate(view):
        state = held(view)
        ruling = find(state, ruling_id)
        if ruling is None:
            return Unmet(f"{name} has no ruling {ruling_id}", _listing(name))
        kind = kind_of(ruling.rules)
        if kind is None:
            return Unmet(f"{ruling_id} rules {ruling.rules}, and nothing here says "
                         "how such a ruling is taken back", _listing(name))
        if kind.undo is None:
            return kind.instead(ruling, state, name)
        spent = _spent(ruling, name)
        if spent is not None:
            return spent
        question = questions.find(state, ruling.question or "")
        if question is not None and question.state == questions.MOVED:
            return Unmet(f"{ruling_id} rules {ruling.rules}, and {question.id} was "
                         f"moved out of {name} since and is now {question.moved_to}; "
                         "what it reads as there is not changed from here",
                         _listing(name))
        return None
    return gate


def taking_back(state: states.State, ruling_id: str) -> Mapping[str, object]:
    """The fields of the ruling that takes `ruling_id` back, for a ruling that
    `can_be_taken_back`: the id it revokes and what its kind's `undo` adds."""
    ruling = find(state, ruling_id)
    return {"revokes": ruling_id, **kind_of(ruling.rules).undo(ruling)}


def replaces(ruling_id: str, rules: str | None, *, name: str,
             held: Callable[[object], states.State], again: str) -> Callable:
    """The gate of a ruling of `rules` that replaces `ruling_id`: the log holds that
    ruling, it stands, and it rules the same thing. `again` is the command that
    records the new ruling with no earlier one named."""
    def gate(view):
        state = held(view)
        ruling = find(state, ruling_id)
        if ruling is None:
            return Unmet(f"{name} has no ruling {ruling_id} to replace",
                         _listing(name, f"{again} --supersedes S<n>"))
        if ruling.revoked_by is not None:
            return Unmet(f"{ruling_id} was taken back by {ruling.revoked_by}, so "
                         "nothing stands for this ruling to replace", again)
        if ruling.replaced_by is not None:
            return Unmet(f"{ruling_id} was replaced already, by {ruling.replaced_by}; "
                         "a ruling replaces the one that stands",
                         f"{again} --supersedes {ruling.replaced_by}")
        was, now = kind_of(ruling.rules), kind_of(rules)
        if was is None or now is None or not (was.again and now.again):
            odd = ruling.rules if was is None or not was.again else rules
            return Unmet(f"a ruling of {odd} is not ruled a second time, so "
                         f"{ruling_id} is not replaced by this one",
                         _listing(name, again))
        if was.item(ruling.rules) != now.item(rules):
            return Unmet(f"{ruling_id} {_said(ruling.rules)}, and this ruling "
                         f"{_said(rules)}; a ruling replaces an earlier ruling of the "
                         "same thing", _listing(name, f"{again} --supersedes S<n>"))
        return None
    return gate


def _said(rules: str | None) -> str:
    return "stands on its own" if rules is None else f"rules {rules}"


__all__ = ["REVOKE", "MOVE", "PARK", "NARROW", "WITHDRAW", "READBACK", "SKIP", "FLOW",
           "UNSTAGE", "UNAFTER",
           "ALONE", "ID", "Kind",
           "KINDS", "kind_of", "item_of", "stands", "live", "find", "standing", "put_by",
           "ended", "can_be_taken_back", "taking_back", "replaces"]
