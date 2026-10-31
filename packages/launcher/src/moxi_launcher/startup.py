"""Startup screens: the screens a harness shows before any hook can run, answered in a slot
launch, and the install questions that turn that on.

Claude Code can open on a screen of its own before the session starts: the folder-trust
screen, and the development-channels screen. Nothing inside the session can answer them,
so in a slot launch the launcher may, through slots' screen calls, which recognise each
screen by its content and press only the keys it declares. Each screen is answered only
when its setting is on: `startup.trust_dialog` and `startup.dev_channels_dialog`, both off
by default. In this terminal the person answers them; headless runs do not show them.

**The rule: the launcher presses a key only on a screen it has just seen showing, and fails
a launch only when a screen is still showing that it cannot answer.** A screen that never
shows, or leaves on its own (a person attached and answered it), is not a failure.

`watch` is the loop. After the slot's session starts, every `INTERVAL_S` until the session
is ready or `startup.wait_s` has passed (without turns it runs to the bound), it reads the
pane with `read_screen`, which sends nothing. When an enabled screen shows, it calls
`answer_screen` with what remains of the bound (at most `ANSWER_WAIT_S`), since that bound
covers the whole call, keys and settling included, and records the outcome as a
`startup-screen` event. slots' outcome words decide what follows:

- `gone`: the screen was answered and left; the loop goes on. So does `not-ended`, which
  slots gives only for a screen that must end its pane, and the pane is still there.
- `mismatch` whose `why` starts with the word `not-settled`: slots' settle bound ran out
  before the screen held still. It is recorded as `not-settled`, and one interval later
  the loop answers the screen again if it still shows, while the bound has time left. If
  the bound runs out this way, the watch ends at its bound and the ready wait decides.
- any other `mismatch`: slots could not answer the screen. The loop reads the pane once
  more. If that read still shows the same screen, the launch fails `screen-mismatch`, with
  slots' capture of the screen, and nothing more is sent to the session. If the screen has
  left (a person attached and answered it, or it went on its own), the loop goes on.
- `ended`, `ended-without-screen` or `no-pane`: the pane died or is gone, and the launch
  fails `exited`. So does a dead pane at any read, the read after a `mismatch` included,
  and a `mismatch` whose last read (`Answer.seen`) found the pane dead: a session that died
  after Enter on the screen exited, it did not show another screen.

No answer is started with less than `MIN_ANSWER_S` left. An answer may run up to 5 s past
the wait it is given (slots waits that long for a screen to leave after Enter), so the time
left is read from the clock after each one, and a watch may end up to 5 s past its bound. slots' `why` is words for a
person, recorded beside the outcome with the keys slots sent; its leading `not-settled` is
the one word the loop reads in it. Any slots error from a read or an answer (its server
not answering, tmux failing, the slot gone from its registry) stops the watch, and the
ready wait decides.

No Codex screen is declared yet, so a Codex slot launch answers none: a folder Codex has
not trusted may show Codex's own trust prompt, which waits for a person who attaches, and a
launch that owes a first turn then ends `no-ready`. The `trust_cwd` row trusts the
launch's folder for that launch only.

`setup` is the launcher's `SETUP`, which `moxi install launcher` runs: when slots is
installed it asks the two `startup.*` questions, each once (a key set in the launcher's
config file has been answered, and install never resets it). With nobody at the keyboard
both stay off and it prints the `moxi config set` lines that turn them on. Without slots
it asks nothing.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TextIO

from moxi import config

from . import slots, store

log = logging.getLogger("moxi.launcher.startup")

MODULE = "launcher"
#: How often the loop reads the pane, in seconds.
INTERVAL_S = 0.25
#: The most one `answer_screen` call may take, in seconds.
ANSWER_WAIT_S = 5.0
#: The least time left in the bound worth starting an answer with: slots needs two reads
#: half a second apart before it presses anything.
MIN_ANSWER_S = 1.0
#: The `events` kind of each screen the loop answered.
EVENT = "startup-screen"
#: slots' answers after which the loop goes on watching.
GO_ON = frozenset({"gone", "not-ended"})
#: slots' answer when it could not answer a screen in its bound.
MISMATCH = "mismatch"
#: The word a `mismatch`'s `why` starts with when slots' settle bound ran out: the screen
#: had not settled. It is also the outcome the loop records for that answer. slots' API
#: module does not export it, so the launcher keeps its own.
NOT_SETTLED = "not-settled"
#: slots' answers that mean the pane died or is gone.
PANE_ENDED = frozenset({"ended", "ended-without-screen", "no-pane"})
#: The reasons a launch fails with here.
EXITED = "exited"
SCREEN_MISMATCH = "screen-mismatch"

#: The setting that turns each screen on, by the name slots declares it under.
SCREENS = {
    "claude": (("startup.trust_dialog", "CLAUDE_TRUST_FOLDER"),
               ("startup.dev_channels_dialog", "CLAUDE_DEV_CHANNELS")),
}

#: The install questions: each `startup.*` switch and the question asked for it.
QUESTIONS = (
    ("startup.trust_dialog",
     "Answer Claude Code's folder-trust screen when a slot launch opens on it? The "
     "launcher answers only a screen recognised by its content, with its declared keys."),
    ("startup.dev_channels_dialog",
     "Answer Claude Code's development-channels screen when a slot launch opens on it?"),
)


# -- the screens loop -------------------------------------------------------------------


@dataclass(frozen=True)
class Watched:
    """How the loop ended.

    `ended` is `ready` (the session said it is ready), `bound` (`startup.wait_s` passed),
    `stopped` (slots could not be read, or answered in a word the loop does not know; the
    ready wait decides), or `failed`, with `reason` (`exited` or `screen-mismatch`), `why`,
    and slots' `capture` of a screen it could not answer. `answered` lists each answer's
    screen and its outcome: slots' word, or `not-settled`."""

    ended: str
    reason: str | None = None
    why: str = ""
    capture: str | None = None
    answered: tuple[tuple[str, str], ...] = ()

    @property
    def failed(self) -> bool:
        return self.ended == "failed"


def screens(harness: str, api: Any, current: Mapping[str, Any] | None = None
            ) -> list[tuple[str, Any]]:
    """The screens a slot launch of `harness` answers: each one whose setting is on, as
    slots' API module declares it, by name. None for Codex, which has no screen declared
    yet."""
    return [(name, getattr(api, name)) for key, name in SCREENS.get(harness, ())
            if _setting(current, key)]


def watch(conn: sqlite3.Connection, request_id: str, slot: Any,
          enabled: Sequence[tuple[str, Any]], *, api: Any,
          ready: Callable[[float], Any] | None = None, wait_s: float | None = None,
          interval: float = INTERVAL_S, clock: Callable[[], float] = time.monotonic,
          sleep: Callable[[float], None] = time.sleep) -> Watched:
    """Watch slot `slot` for the `enabled` screens (`(name, screen)` pairs) until the
    session is ready or `wait_s` (`startup.wait_s`) passes, answering each that shows.

    `ready(timeout)` asks whether the session is ready, waiting at most `timeout`: truthy
    when it is, falsy or raising (`TimeoutError`) when not. None (no turns) runs the loop
    to its bound. Writes a `startup-screen` event per answer; never writes the row's
    state, which the caller writes from what this returns."""
    if wait_s is None:
        from . import settings

        wait_s = float(settings.current()["startup.wait_s"])
    shown = [screen for _, screen in enabled]
    answered: list[tuple[str, str]] = []
    deadline = clock() + wait_s
    failures = api.SlotsError

    def done(ended: str, **more: Any) -> Watched:
        return Watched(ended, answered=tuple(answered), **more)

    while True:
        if deadline - clock() <= 0:
            return done("bound")
        try:
            seen = api.read_screen(slot, screens=shown)
        except failures as error:  # slots unreadable: the ready wait decides
            return _stopped(conn, request_id, error, done)
        if seen.dead:
            return done("failed", reason=EXITED, why=_exited(api, slot))
        name, screen = _named(seen.screen, enabled)
        if screen is not None:
            left = deadline - clock()
            if left < MIN_ANSWER_S:
                return done("bound")
            wait = min(ANSWER_WAIT_S, left)
            # slots may take up to 5 s past `wait` on a screen that must leave after
            # Enter, so the loop never counts on the call ending within it: what remains
            # is read from the clock again after it. A watch can so run up to 5 s past
            # `startup.wait_s`, which is accepted.
            try:
                answer = api.answer_screen(slot, screen, wait=wait)
            except failures as error:
                return _stopped(conn, request_id, error, done)
            outcome, why = answer.outcome, answer.why or ""
            capture = str(answer.capture) if answer.capture else None
            unsettled = outcome == MISMATCH and _not_settled(why)
            recorded = NOT_SETTLED if unsettled else outcome
            answered.append((name, recorded))
            detail = {"screen": name, "outcome": recorded, "why": why, "wait": wait,
                      "capture": capture, "keys": list(answer.keys)}
            if unsettled:
                detail["answer"] = outcome
            store.add_event(conn, EVENT, detail, request_id=request_id)
            if outcome in PANE_ENDED or (outcome == MISMATCH and _dead(answer.seen)):
                return done("failed", reason=EXITED, why=_exited(api, slot))
            if outcome in GO_ON:
                continue
            if outcome != MISMATCH:
                return _stopped(conn, request_id,
                                f"slots answered the {name} screen {outcome!r}", done)
            if not unsettled:
                # A failure only if the screen is still showing: read once more.
                try:
                    again = api.read_screen(slot, screens=shown)
                except failures as error:
                    return _stopped(conn, request_id, error, done)
                if again.dead:
                    return done("failed", reason=EXITED, why=_exited(api, slot))
                if _named(again.screen, enabled)[0] == name:
                    return done("failed", reason=SCREEN_MISMATCH,
                                why=f"the {name} screen showed and slots could not answer "
                                    "it" + (f": {why}" if why else ""),
                                capture=capture)
            # Not settled yet, or the screen left after a mismatch: the loop reads again
            # after one interval, and answers a screen that shows while the bound has time.
        step = min(interval, max(deadline - clock(), 0))
        began = clock()
        if ready is not None:
            try:
                if ready(step):
                    return done("ready")
            except Exception:  # noqa: BLE001  (not ready yet, or turns can't say)
                pass
        spent = clock() - began
        if spent < step:
            sleep(step - spent)


def _not_settled(why: str) -> bool:
    """Whether slots' `why` starts with the word `not-settled`: its settle bound ran out."""
    if not why.startswith(NOT_SETTLED):
        return False
    rest = why[len(NOT_SETTLED):]
    return not rest or not (rest[0].isalnum() or rest[0] in "-_")


def _dead(seen: Any) -> bool:
    """Whether slots' last read of the pane (`Answer.seen`) found it dead."""
    return seen is not None and bool(seen.dead)


def _named(shown: Any, enabled: Sequence[tuple[str, Any]]) -> tuple[str | None, Any]:
    """The enabled screen that slots' read matched, by the name the launcher knows it
    under, or `(None, None)`."""
    if shown is None:
        return None, None
    for name, screen in enabled:
        if shown is screen or shown == screen:
            return name, screen
    return None, None


def _exited(api: Any, slot: Any) -> str:
    """Why the launch failed when its pane died: with the pane's exit status, when slots
    can show it."""
    try:
        found = api.show(slot)
    except api.SlotsError:
        found = None
    status = found.dead_status if found is not None else None
    if status is None:
        return "the session exited before it was ready"
    return f"the session exited before it was ready (pane status {status})"


def _stopped(conn, request_id: str, error: BaseException | str,
             done: Callable[..., Watched]) -> Watched:
    why = error if isinstance(error, str) else f"{type(error).__name__}: {error}".rstrip(": ")
    log.warning("startup screens of %s not watched further: %s", request_id, why)
    store.add_event(conn, EVENT, {"outcome": "stopped", "why": why},
                    request_id=request_id)
    return done("stopped", why=why)


def _setting(current: Mapping[str, Any] | None, key: str) -> bool:
    if current is None:
        from . import settings

        current = settings.current()
    return bool(current[key])


# -- the install questions ----------------------------------------------------------------


def setup(*, ask: Callable[[str], bool] | None = None, out: TextIO | None = None) -> int:
    """`SETUP`: ask the `startup.*` questions when slots is installed, each once.

    A key already set in the launcher's config file was answered before and is not asked
    again. Each answer is written with `moxi config set`'s rules, by `moxi install`. With
    nobody at the keyboard (stdin not a terminal) nothing is written: both stay off, and
    the line that turns each on is printed. `ask` stands in for the question at the
    keyboard. Returns 0, or 1 when the config file can't be read or written."""
    out = sys.stdout if out is None else out
    if not slots.available():
        return 0
    from . import settings

    try:
        current = settings.current()
    except config.ConfigError as error:
        print(f"moxi launcher: the startup questions were not asked: {error}", file=out)
        return 1
    if ask is None and sys.stdin is not None and sys.stdin.isatty():
        ask = _ask
    for key, question in QUESTIONS:
        if current.source(key) == config.FILE:
            continue
        turn_on = f"moxi config set {MODULE} {key} true"
        if ask is None:
            print(f"launcher: {key} is off. {question}\n    turn it on with: {turn_on}",
                  file=out)
            continue
        answer = bool(ask(f"launcher: {question} [y/N] "))
        try:
            config.set(MODULE, key, answer, by="moxi install")
        except config.ConfigError as error:
            print(f"moxi launcher: {key} was not set: {error}", file=out)
            return 1
        if not answer:
            print(f"    left off; turn it on later with: {turn_on}", file=out)
    return 0


def _ask(question: str) -> bool:
    try:
        answer = input(question)
    except EOFError:
        print()
        return False
    return answer.strip().lower() in ("y", "yes")


__all__ = ["ANSWER_WAIT_S", "EVENT", "EXITED", "GO_ON", "INTERVAL_S", "MIN_ANSWER_S",
           "MISMATCH", "NOT_SETTLED", "PANE_ENDED", "QUESTIONS", "SCREENS",
           "SCREEN_MISMATCH", "Watched", "screens", "setup", "watch"]
