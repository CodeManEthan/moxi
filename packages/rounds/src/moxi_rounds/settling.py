"""The lead's acts on a decision: settling a minor one, and raising one to major.

A minor decision of a finished build that is not the operator's to rule
(`decisions.to_settle`) holds the round's close until the lead settles it:

    moxi rounds mark ROUND B2.M1 --accept --record PTR
    moxi rounds mark ROUND B2.M1 --reject --record PTR

`settle` appends one `decision.settled` event: the decision's key, what was chosen, the
pointer and `by`, the role, `lead`; the event's actor is who ran it. The decision then
reads `settled`, never `ruled`. A reject owes a fix, a waiting item until `mark ROUND
B2.M1 --done --record PTR` names it. The settlement is refused for a key that is no
decision of the round, a major decision, one already ruled or settled, a minor one
that is the operator's, or one that will be once its stage finishes, a missing or
malformed pointer, and a round that has ended.

    moxi rounds mark ROUND B2.M1 --major --record PTR

`make_major` appends one `decision.raised` event: the key, the pointer and `by`,
`lead`. The decision is major from then on, as when a later report lists it heavier; a
settlement of it lapses. It is refused for a major decision, one the operator ruled, a
missing or malformed pointer, and a round that has ended.

Both write through the write path and read the project's `ratify` setting; neither
reads git.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import decisions, ending, folders, pointers, write
from . import settings as own
from . import state as states
from .errors import BadArgument, NoLog, NoRound, Unmet


@dataclass(frozen=True)
class Settled:
    """A decision after the lead's act: the round's `name`, the decision as the log now
    gives it, and the `event` written. `failed` pairs each after-append callback that
    raised with what it raised."""

    name: str
    decision: decisions.Decision
    event: dict
    failed: tuple[tuple[Callable, Exception], ...] = ()


def _key(key: str) -> None:
    if decisions.KEY.fullmatch(key or "") is None:
        raise BadArgument(f"{key!r} is not the key of a decision",
                          "name the decision as show lists it: B<n>.M<k>")


def _write(target: str, key: str, *, record: str | None, by: str, what: str,
           gate: Callable[[decisions.Decision, states.State, str, str], Unmet | None],
           entry: Callable[[], write.Entry], settings, wait_s: float,
           at: str | None) -> Settled:
    """The one write both acts make: find the decision, run `gate` on it, check the
    pointer, and append `entry`."""
    _key(key)
    project, slug = folders.split(target)
    settings = own.load() if settings is None else settings
    home = folders.home(project, settings)
    name = f"{home.project}/{slug}"
    setting = str(own.round_setting(settings, home.project, decisions.RATIFY))

    def held(view):
        found = decisions.find(view.state, key)
        if found is None:
            return Unmet(f"{name} has no decision {key}", f"moxi rounds show {name}")
        return gate(found, view.state, name, setting)

    def has_a_pointer(view):
        again = f"moxi rounds mark {name} {key} {what} --record PTR"
        if record is None:
            return Unmet(f"{what} names where the lead wrote why, with --record", again)
        return pointers.unmet(record, again, names="where the lead wrote why")

    try:
        written = write.record(
            write.Where(home.project, home.rounds_dir, slug), by=by,
            gates=[ending.takes_new_work(name, f"the lead's {what}"), held,
                   has_a_pointer],
            event=lambda view, result: entry(), wait_s=wait_s, at=at)
    except NoLog:
        raise NoRound(name) from None
    after = decisions.find(states.fold(written.round_events), key)
    return Settled(name=name, decision=after, event=written.events[0],
                   failed=written.failed)


def settle(target: str, key: str, chose: str, *, record: str | None, by: str,
           settings=None, wait_s: float = write.WAIT_S, at: str | None = None
           ) -> Settled:
    """Settle the minor decision `key` of the round `target`: `chose` is `accept` or
    `reject`, `record` the pointer to where the lead wrote why.

    Raises `BadArgument` for a key or a choice that can't be one; `UnknownProject`,
    `NoFolder` or `NoRound` for a round that can't be found; and `Refused` for what the
    module's docstring lists. A refusal leaves the log as it was."""
    if chose not in (decisions.ACCEPT, decisions.REJECT):
        raise BadArgument(f"{chose!r} is not what the lead settles a decision with",
                          f"{decisions.ACCEPT} or {decisions.REJECT}")

    def gate(found, state, name, setting):
        if found.state == decisions.RULED:
            return Unmet(f"{key} is ruled by the operator, {found.chose}, by "
                         f"{found.ruling}, and takes no settlement",
                         f"moxi rounds show {name}")
        if found.state == decisions.SETTLED:
            return Unmet(f"{key} is settled already, {found.chose}, record "
                         f"{found.record}", f"moxi rounds show {name}")
        if found.major:
            return Unmet(f"{key} is major, and the lead settles only a minor decision; "
                         "a major one is the operator's, or is left open",
                         decisions.rule_line(name, key))
        why = decisions.why_not(state, setting).get(key)
        if why is None:
            return Unmet(f"{key} is the operator's to rule, and the lead does not "
                         "settle it", decisions.rule_line(name, key))
        if decisions.is_yet_to_come(why, found.stage):
            return Unmet(f"{key} is put to the operator once {found.stage} finishes "
                         f"({why}), and the lead does not settle it",
                         decisions.rule_line(name, key))
        return None

    return _write(target, key, record=record, by=by, what=f"--{chose}", gate=gate,
                  entry=lambda: write.Entry(decisions.SETTLE, {
                      "id": key, "chose": chose, "record": record,
                      "by": decisions.LEAD}),
                  settings=settings, wait_s=wait_s, at=at)


def make_major(target: str, key: str, *, record: str | None, by: str, settings=None,
               wait_s: float = write.WAIT_S, at: str | None = None) -> Settled:
    """Make the decision `key` of the round `target` major, `record` the pointer to
    where the lead wrote why. Raises as `settle` does."""

    def gate(found, state, name, setting):
        if found.state == decisions.RULED:
            return Unmet(f"{key} is ruled by the operator, {found.chose}, by "
                         f"{found.ruling}, and its weight stays",
                         f"moxi rounds show {name}")
        if found.major:
            return Unmet(f"{key} is major already", f"moxi rounds show {name}")
        return None

    return _write(target, key, record=record, by=by, what="--major", gate=gate,
                  entry=lambda: write.Entry(decisions.RAISE, {
                      "id": key, "record": record, "by": decisions.LEAD}),
                  settings=settings, wait_s=wait_s, at=at)


__all__ = ["Settled", "settle", "make_major"]
