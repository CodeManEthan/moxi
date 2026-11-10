"""Options: the choices a question offers, a line each under a short key.

A question may be asked bare, or with options, and options may be added to one on
record. `fill("<project>/<slug>", "Q1", options=["a=...", "b=..."], retire=["c"],
actor=...)` appends one `question.changed` event through the write path and returns the
question as the log then has it. Anyone may: options are no act of the operator's and
carry no pointer.

**What options change.** What is shown, and nothing else: not the question's state, not
what it holds up. An answer may name the key that was chosen.

**A key keeps its text.** A key the question has is refused with another line. A wrong
line is corrected by retiring its key and adding the right line under a new one, which
one call may do. A retired key stays in the log, is offered no longer, and is never
used again, not even with the line it had. So a key in an answer always means the line
that was shown under it.

**How many.** A question that has options has `FEWEST` to `MOST`, retired ones not
counted. A call that would leave fewer or more is refused whole.

**When.** While the question is open or parked.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from . import ending, folders, questions, state, write
from . import settings as own
from .errors import BadArgument, NoLog, NoRound, Unmet

#: A key: lower-case letters, digits and `-`, at most sixteen characters.
KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,15}")

#: The fewest and the most options a question that has options may have.
FEWEST = 2
MOST = 4

#: The id of a question, a round's or a project's.
QUESTION_ID = re.compile(r"[QP][1-9][0-9]*")

Pairs = tuple[tuple[str, str], ...]

_KEY_SAID = "a key is short: lower-case letters, digits and -, sixteen at most"


def parse(given: Sequence[str]) -> Pairs:
    """The options of `K=TEXT` values, each as `(key, text)`, in the order given. The
    text is kept as given. Raises `BadArgument` for a value with no `=`, a key that is
    no key, a text that is blank or has a line break, or a key given twice."""
    pairs: list[tuple[str, str]] = []
    for one in given:
        key, equals, text = one.partition("=")
        if not equals:
            problem = "it has no ="
        elif not KEY.fullmatch(key):
            problem = f"{key!r} is not a key; {_KEY_SAID}"
        elif not text.strip():
            problem = "its line says nothing"
        elif "\n" in text or "\r" in text:
            problem = "an option is one line, and this one has a line break"
        elif any(key == had for had, _text in pairs):
            problem = f"the key {key} is given twice"
        else:
            pairs.append((key, text))
            continue
        raise BadArgument(f"--option {one!r} can't be read: {problem}",
                          "give each option as --option K=TEXT, a key and its one line")
    return tuple(pairs)


def parse_retired(given: Sequence[str]) -> tuple[str, ...]:
    """The keys of `--retire` values, in the order given. Raises `BadArgument` for one
    that is no key, or one given twice."""
    keys: list[str] = []
    for key in given:
        if not KEY.fullmatch(key):
            problem = f"{key!r} is not a key; {_KEY_SAID}"
        elif key in keys:
            problem = f"the key {key} is given twice"
        else:
            keys.append(key)
            continue
        raise BadArgument(f"--retire {key!r} can't be read: {problem}",
                          "name the key of one option the question has: --retire K")
    return tuple(keys)


def text_of(question: state.Question, key: str) -> str | None:
    """The line the question offers under `key` now, or None when it offers none: a
    retired key gives None."""
    return dict(question.options).get(key)


def said(question: state.Question) -> str:
    """The keys the question offers now, in words, for a refusal."""
    keys = [key for key, _text in question.options]
    return f"its options are {', '.join(keys)}" if keys else "it has no options"


def how_many(count: int, fix: str) -> Unmet | None:
    """The unmet condition of a question left with `count` options, or None when the
    count may stand."""
    if FEWEST <= count <= MOST:
        return None
    return Unmet(f"a question that has options has {FEWEST} to {MOST}, retired ones "
                 f"not counted, and this would leave it {count}", fix)


@dataclass(frozen=True)
class Filled:
    """A question after its options changed: the `name` of the round or project whose
    log holds it, the `question` as the log now has it, and the `event` written, None
    when every option given was already there and nothing was. `failed` pairs each
    after-append callback that raised with what it raised."""

    name: str
    question: state.Question
    event: dict | None
    failed: tuple[tuple[Callable, Exception], ...] = ()


def fill(target: str, question_id: str, *, actor: str, options: Sequence[str] = (),
         retire: Sequence[str] = (), settings=None, wait_s: float = write.WAIT_S,
         at: str | None = None) -> Filled:
    """Add options to the question `question_id` of `target`, a round `<project>/<slug>`
    or a project's key, retire some, or both.

    - `options`: the options to add, each `K=TEXT`. One the question already offers,
      under the same key with the same line, is accepted and not written again.
    - `retire`: the keys to take out.
    - `actor`: who does it, the event's actor.
    - `settings`: rounds' settings as `settings.load()` gives them, read now when left
      out.
    - `wait_s`: how long to wait for the lock; `at`: the event's time.

    Raises `BadName` or `BadArgument` for a value that can't be used, `UnknownProject`,
    `NoFolder` or `NoRound` for a round or project that can't be found, and `Refused`
    when the log has no such question, the question is ruled or withdrawn, a key is
    given with another line than it has or after it was retired, a key to retire is
    not one the question offers, or the question would be left with fewer than `FEWEST`
    options or more than `MOST`. A refusal leaves the log as it was.
    """
    if not isinstance(target, str) or not target.strip():
        raise BadArgument("nothing says whose question it is",
                          "name a round as <project>/<slug>, or a project by its key")
    project, slug = folders.split(target) if "/" in target else (target, None)
    if not QUESTION_ID.fullmatch(question_id or ""):
        raise BadArgument(f"{question_id!r} is not the id of a question",
                          "name a round's question as Q<n>, a project's as P<n>")
    adding = parse(options)
    retiring = parse_retired(retire)
    if not adding and not retiring:
        raise BadArgument("nothing says what to change of the question's options",
                          "add options with --option K=TEXT, retire one with "
                          "--retire K")

    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = home.project if slug is None else f"{home.project}/{slug}"
    again = f"moxi rounds ask {name} {question_id}"
    listing = (f"moxi rounds show {name}" if slug is not None else
               f"moxi rounds list {name} --questions")

    def held(view):
        return view.state if slug is not None else view.project_state

    def found(view):
        return questions.find(held(view), question_id)

    def exists(view):
        if found(view) is not None:
            return None
        return Unmet(f"{name} has no question {question_id}", listing)

    def still_asked(view):
        question = found(view)
        if question is None or question.state in (questions.OPEN, questions.PARKED):
            return None
        return Unmet(f"{question_id} is {question.state}; options are added and "
                     "retired only while a question is open or parked", listing)

    def keys_keep_their_text(view):
        question = found(view)
        if question is None:
            return None
        unmet = []
        for key, text in adding:
            if key in question.retired:
                unmet.append(Unmet(
                    f"the key {key} was retired from {question_id} and is never used "
                    "again, with any line; add the line under a new key",
                    f"{again} --option K=TEXT"))
            elif text_of(question, key) not in (None, text):
                unmet.append(Unmet(
                    f"{question_id} offers {text_of(question, key)!r} under {key}, and "
                    "a key keeps its text; to correct a line, retire its key and add "
                    "the right line under a new one",
                    f"{again} --retire {key} --option K=TEXT"))
        return unmet

    def retires_what_is_offered(view):
        question = found(view)
        if question is None:
            return None
        unmet = []
        for key in retiring:
            if key in question.retired:
                unmet.append(Unmet(f"the key {key} of {question_id} is retired "
                                   f"already; {said(question)}", listing))
            elif text_of(question, key) is None:
                unmet.append(Unmet(f"{question_id} never had an option {key}; "
                                   f"{said(question)}", listing))
        return unmet

    def keeps_two_to_four(view):
        question = found(view)
        if question is None:
            return None
        keys = {key for key, _text in question.options}
        left = len((keys - set(retiring)) | {key for key, _text in adding})
        return how_many(left, f"{again} --option K=TEXT... [--retire K]...")

    def changed(view, result):
        question = found(view)
        new = {key: text for key, text in adding if text_of(question, key) is None}
        fields: dict[str, object] = {"id": question_id}
        if new:
            fields["options"] = new
        if retiring:
            fields["retired"] = list(retiring)
        if len(fields) == 1:
            # Every option given is offered already, line for line: nothing changed.
            return None
        return write.Entry("question.changed", fields,
                           log=write.ROUND if slug is not None else write.PROJECT)

    try:
        written = write.record(
            write.Where(home.project, home.folder, slug), actor=actor,
            gates=[ending.takes_new_work(name, "a change to a question's options"),
                   exists, still_asked, keys_keep_their_text, retires_what_is_offered,
                   keeps_two_to_four],
            event=changed, wait_s=wait_s, at=at)
    except NoLog:
        # Only a round's log can be missing here: a project's reads as empty.
        raise NoRound(name) from None
    events = written.round_events if slug is not None else written.project_events
    after = questions.find(state.fold(events), question_id)
    return Filled(name=name, question=after,
                  event=written.events[0] if written.events else None,
                  failed=written.failed)


__all__ = ["KEY", "FEWEST", "MOST", "QUESTION_ID", "Filled", "fill", "parse",
           "parse_retired", "text_of", "said", "how_many"]
