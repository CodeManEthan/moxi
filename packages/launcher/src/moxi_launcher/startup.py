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
`startup-screen` event. Then:

- `mismatch` with slots saying a key never visibly took effect in its bound (`why` starts
  with `not-settled`): recorded as `not-settled`, not as a mismatch, and the loop goes on.
- any other `mismatch`: the pane is read once more. If the same screen still shows, the
  launch fails `screen-mismatch`, with slots' capture of the screen, and nothing more is
  sent to the session. If not, the loop goes on.
- a dead pane, at any read, fails the launch `exited`.

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
#: The outcome recorded when slots spent its settle bound after a key.
NOT_SETTLED = "not-settled"
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
    `stopped` (slots could not be read; the ready wait decides), or `failed`, with
    `reason` (`exited` or `screen-mismatch`), `why`, and slots' `capture` of a screen it
    could not answer. `answered` lists each answer's screen and recorded outcome."""

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
    slots declares it, by name. None for Codex, which has no screen declared yet."""
    wanted = [name for key, name in SCREENS.get(harness, ()) if _setting(current, key)]
    found = []
    for name in wanted:
        screen = getattr(api, name, None)
        if screen is None:
            screen = getattr(getattr(api, "screens", None), name, None)
        if screen is None:
            log.warning("slots declares no screen %s; it is not answered", name)
            continue
        found.append((name, screen))
    return found


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

    def done(ended: str, **more: Any) -> Watched:
        return Watched(ended, answered=tuple(answered), **more)

    while True:
        left = deadline - clock()
        if left <= 0:
            return done("bound")
        try:
            seen = api.read_screen(slot, screens=shown)
        except Exception as error:  # noqa: BLE001  (slots unreadable: the ready wait decides)
            return _stopped(conn, request_id, error, done)
        if _dead(seen):
            return done("failed", reason=EXITED, why=_died(seen.dead))
        name, screen = _named(getattr(seen, "screen", None), enabled)
        if screen is not None:
            left = deadline - clock()
            if left < MIN_ANSWER_S:
                return done("bound")
            wait = min(ANSWER_WAIT_S, left)
            try:
                answer = api.answer_screen(slot, screen, wait=wait)
            except Exception as error:  # noqa: BLE001
                return _stopped(conn, request_id, error, done)
            outcome = str(getattr(answer, "outcome", ""))
            why = str(getattr(answer, "why", "") or "")
            capture = getattr(answer, "capture", None)
            settled = not (outcome == "mismatch" and why.startswith(NOT_SETTLED))
            recorded = outcome if settled else NOT_SETTLED
            answered.append((name, recorded))
            detail = {"screen": name, "outcome": recorded, "why": why, "wait": wait,
                      "capture": str(capture) if capture else None}
            if not settled:
                detail["answer"] = outcome
            store.add_event(conn, EVENT, detail, request_id=request_id)
            if outcome == "no-pane":
                return done("failed", reason=EXITED, why="the slot's run pane is gone")
            if outcome == "mismatch" and settled:
                try:
                    again = api.read_screen(slot, screens=shown)
                except Exception as error:  # noqa: BLE001
                    return _stopped(conn, request_id, error, done)
                if _dead(again):
                    return done("failed", reason=EXITED, why=_died(again.dead))
                if _named(getattr(again, "screen", None), enabled)[0] == name:
                    return done("failed", reason=SCREEN_MISMATCH,
                                why=f"the {name} screen showed and could not be answered"
                                    + (f": {why}" if why else ""),
                                capture=str(capture) if capture else None)
            continue
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


def _named(shown: Any, enabled: Sequence[tuple[str, Any]]) -> tuple[str | None, Any]:
    """The enabled screen `shown` names, whether slots gave the screen itself or its
    name."""
    if shown is None:
        return None, None
    for name, screen in enabled:
        if shown is screen or shown == screen or shown == name \
                or getattr(shown, "name", None) == name:
            return name, screen
    return None, None


def _dead(seen: Any) -> bool:
    """Whether a read saw the pane dead: `dead` set, a status of 0 included."""
    dead = getattr(seen, "dead", None)
    return dead is not None and dead is not False


def _died(dead: Any) -> str:
    if dead is True:
        return "the session exited before it was ready"
    return f"the session exited before it was ready (pane status {dead})"


def _stopped(conn, request_id: str, error: BaseException,
             done: Callable[..., Watched]) -> Watched:
    why = f"{type(error).__name__}: {error}".rstrip(": ")
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


__all__ = ["ANSWER_WAIT_S", "EVENT", "EXITED", "INTERVAL_S", "MIN_ANSWER_S", "NOT_SETTLED",
           "QUESTIONS", "SCREENS", "SCREEN_MISMATCH", "Watched", "screens", "setup", "watch"]
