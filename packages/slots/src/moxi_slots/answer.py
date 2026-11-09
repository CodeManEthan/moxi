"""Reading a slot's run pane for a known screen, answering one, and the pane-use read.

`read_screen(slot, screens)` makes one read of the run pane (its text, `pane_dead`,
`pane_in_mode` and width, in one tmux call) and says which of `screens` is there and
whether it fully matches (see `screens` for the rule). It sends nothing.

`answer_screen(slot, screen, wait_s)` presses the screen's keys, and only those: Up and
Down toward the option on a screen that allows them, then Enter on the option. There is
no other way to send a key or text into a pane: a session is typed into only through
its own channel.

1. With `wait_s=0` it reads once. With `wait_s` above 0 it reads every 250 ms until the pane
   is dead (`ended-without-screen`), the screen has fully matched on two reads at least
   0.5 s apart with the marker on the same row (the trust warning drops keys while it
   opens), or `wait_s` runs out (`mismatch`). A caller passing `wait_s=0` has seen the
   screen with `read_screen` at least 0.5 s before.
2. The rule for keys: every key is decided on a settled read, and a read is settled only
   once the previous key has visibly taken effect. The first read is settled by step 1.
   After a key, reads repeat every 100 ms until the marked row differs from the one
   marked before the key and the new one shows on two reads at least 0.3 s apart; only
   that read decides the next key. A read that does not fully match, inside that window,
   is a frame being drawn: the reads go on. Each key's settle bound is 2 s; a spent one
   ends the call `mismatch` with `why` starting `not-settled`, and so does a key there
   is too little of `wait_s` left to settle. Enter goes only on a settled read with the
   marker on the option. On a screen that allows moving, one Up or Down toward the
   option is sent per settled read, at most one per row between the first marked row
   and the option; a marker that moves away from the option ends the call `mismatch`.
   On a screen that does not, a marker off the option is a `mismatch` and no key is
   sent. A dead pane is never a settled read: the call ends `ended-without-screen` and
   sends nothing more.
3. After Enter: a screen whose `then` is `ended` waits up to 15 s for the pane to die
   (`ended`, else `not-ended`); one whose `then` is `gone` needs the screen to leave
   within 5 s with the pane still live (`gone`, else `mismatch`).

`wait_s` bounds steps 1 and 2, the Enter included, so a caller's bound holds; step 3 has
its own bound. One call presses Enter at most once, and every key follows a read taken
after the screen appeared. With `wait_s=0` on a screen that takes Enter alone (the exit
dialog), the call is one read and at most Enter, back to back.

A `mismatch` saves the screen's region, plain and with its escape sequences, as one
file in the `mismatches` store, which keeps the last 20; `Answer.capture` is its path.
A new layout can then be read and added as a fixture.

`pane_use(slot)` is the read behind a launcher's check for a person at the pane: copy
mode, the attached clients' latest activity, and the run window's.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from moxi import files

from . import places, tmux
from .errors import Refused, TmuxError, UsageError
from .model import NOT_SETTLED, Answer, PaneUse, Seen, session_target
from .screens import ENDED, GONE, Match, Screen, find, is_rule, match, rows

#: Seconds between reads while waiting for the screen to settle.
POLL = 0.25
#: Seconds two full reads must be apart before the first key.
STABLE = 0.5
#: Seconds between reads after a key.
KEY_POLL = 0.1
#: Seconds the moved marker must hold, across two reads.
SETTLE_GAP = 0.3
#: Seconds a key may take to show its effect.
SETTLE_BOUND = 2.0
#: Seconds an `ended` screen's pane gets to die after Enter.
ENDED_WAIT = 15.0
#: Seconds a `gone` screen gets to leave after Enter.
GONE_WAIT = 5.0
#: Mismatched regions kept.
KEEP = 20
#: The only keys slots ever sends.
KEYS = ("Enter", "Up", "Down")

_READ = "#{pane_id}\t#{pane_dead}\t#{pane_in_mode}\t#{pane_width}"


@dataclass(frozen=True)
class Frame:
    """One read of a pane: its text, width, whether it is dead and whether it is in a
    tmux mode."""

    text: str
    width: int
    dead: bool = False
    in_mode: bool = False


class TmuxPane:
    """A run pane read and keyed through tmux. Tests put a fake with the same methods in
    its place."""

    def __init__(self, number: int, pane_id: str):
        self.number = number
        self.pane_id = pane_id

    def read(self) -> Frame | None:
        """One read in one tmux call; None when the pane (or the server) is gone."""
        result = tmux.attempt("display-message", "-p", "-t", self.pane_id, _READ,
                              ";", "capture-pane", "-p", "-t", self.pane_id)
        head, _, text = result.out.partition("\n")
        parts = head.split("\t")
        gone = tmux.no_server(result.err) or "can't find" in result.err
        # A pane tmux no longer has prints empty fields, and the capture then fails.
        if len(parts) != 4 or parts[0] != self.pane_id:
            if result.ok or gone:
                return None
        if not result.ok:
            if gone:
                return None
            raise TmuxError(f"tmux capture-pane failed (exit {result.code}): "
                            f"{result.err.strip() or 'no message'}")
        width = int(parts[3]) if parts[3].isdigit() else 0
        return Frame(text, width, parts[1] == "1", parts[2] not in ("", "0"))

    def send(self, key: str) -> None:
        if key not in KEYS:
            raise ValueError(f"slots sends only {', '.join(KEYS)}, not {key!r}")
        tmux.call("send-keys", "-t", self.pane_id, key)

    def ended(self, timeout: float) -> bool:
        from .launch import wait_ended

        return wait_ended(self.number, pane_id=self.pane_id, timeout_s=timeout).ended

    def ansi(self) -> str:
        result = tmux.attempt("capture-pane", "-p", "-e", "-t", self.pane_id)
        return result.out if result.ok else ""

    now = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


@dataclass
class Driven:
    """What `drive` ended in: the outcome word, why, the last frame and its match, and
    the keys sent."""

    outcome: str
    why: str = ""
    frame: Frame | None = None
    match: Match | None = None
    keys: list[str] = field(default_factory=list)


def _row(held: Match, index: int | None) -> str:
    if index is None:
        return "no row"
    return f"row {index + 1} ({held.rows[index].strip()!r})"


def _settle(pane, screen: Screen, before: int, bound: float) -> tuple[str, Frame | None,
                                                                        Match | None]:
    """Read every 100 ms until the marked row has left row `before` and holds on its new
    row across two reads at least 0.3 s apart (`settled`), the pane dies (`dead`) or
    goes (`gone`), or `bound` seconds pass (`bound`)."""
    until = pane.now() + bound
    candidate, since = None, 0.0
    frame = held = None
    while True:
        pane.sleep(max(0.0, min(KEY_POLL, until - pane.now())))
        frame = pane.read()
        if frame is None:
            return "gone", None, held
        held = match(screen, frame.text, frame.width, in_mode=frame.in_mode)
        if frame.dead:
            return "dead", frame, held
        now = pane.now()
        if held.full and held.marked is not None and held.marked != before:
            if candidate != held.marked:
                candidate, since = held.marked, now
            elif now - since >= SETTLE_GAP:
                return "settled", frame, held
        else:
            candidate = None
        if now >= until:
            return "bound", frame, held


def _not_settled(key: str, before: int, held: Match | None, bound: float) -> str:
    if held is None:
        what = "nothing could be read"
    elif not held.full:
        what = f"the screen no longer fully matches: {held.why}"
    elif held.marked == before:
        what = f"the marker stayed on {_row(held, before)}"
    else:
        what = f"the marker on {_row(held, held.marked)} did not hold for {SETTLE_GAP} s"
    return f"{NOT_SETTLED}: {bound:.1f} s after {key}, {what}"


def drive(pane, screen: Screen, wait: float) -> Driven:
    """Steps 1-3 of `answer_screen` against `pane` (a `TmuxPane` or a fake)."""
    deadline = pane.now() + wait
    done = Driven("mismatch")

    def ending(outcome: str, why: str, frame=None, held=None) -> Driven:
        done.outcome, done.why = outcome, why
        if frame is not None:
            done.frame, done.match = frame, held
        return done

    # Step 1: a settled first read.
    frame = pane.read()
    first_at, first_mark = None, None
    while True:
        if frame is None:
            return ending("no-pane", "the run pane is gone")
        held = match(screen, frame.text, frame.width, in_mode=frame.in_mode)
        if frame.dead:
            return ending("ended-without-screen", "the pane had ended; no key was sent",
                          frame, held)
        if wait == 0:
            if not held.full:
                return ending("mismatch", held.why, frame, held)
            break
        now = pane.now()
        if held.full:
            if first_at is None or held.marked != first_mark:
                first_at, first_mark = now, held.marked
            elif now - first_at >= STABLE:
                break
        else:
            first_at = None
        if now >= deadline:
            why = held.why or f"it did not hold still on two reads {STABLE} s apart"
            return ending("mismatch", f"the screen did not settle within {wait:g} s: {why}",
                          frame, held)
        pane.sleep(max(0.0, min(POLL, deadline - now)))
        frame = pane.read()

    # Step 2: the keys, each decided on a settled read.
    budget = abs(held.option - held.marked) if screen.move else 0
    while held.marked != held.option:
        if not screen.move:
            return ending("mismatch", f"the marker is on {_row(held, held.marked)}, not "
                                      f"the option {screen.option!r}; this screen takes "
                                      "Enter alone", frame, held)
        if budget <= 0:
            return ending("mismatch", "the key budget is spent with the marker on "
                                      f"{_row(held, held.marked)}", frame, held)
        left = deadline - pane.now()
        if left < SETTLE_GAP:
            return ending("mismatch", f"{NOT_SETTLED}: {max(left, 0.0):.2f} s of the "
                                      "call's wait is left, too little to settle a key",
                          frame, held)
        key = "Down" if held.option > held.marked else "Up"
        before, distance = held.marked, abs(held.option - held.marked)
        pane.send(key)
        done.keys.append(key)
        budget -= 1
        bound = min(SETTLE_BOUND, left)
        state, after, after_held = _settle(pane, screen, before, bound)
        if state == "gone":
            return ending("no-pane", f"the run pane went after {key}")
        if state == "dead":
            return ending("ended-without-screen", f"the pane ended after {key}; no further "
                                                  "key was sent", after, after_held)
        if state == "bound":
            return ending("mismatch", _not_settled(key, before, after_held, bound), after,
                          after_held)
        frame, held = after, after_held
        if abs(held.option - held.marked) >= distance:
            return ending("mismatch", f"the marker moved away from the option, to "
                                      f"{_row(held, held.marked)}", frame, held)
    pane.send("Enter")
    done.keys.append("Enter")
    done.frame, done.match = frame, held

    # Step 3: what must follow Enter.
    if screen.then == ENDED:
        if pane.ended(ENDED_WAIT):
            return ending("ended", "")
        return ending("not-ended", f"the pane is still live {ENDED_WAIT:g} s after Enter")
    until = pane.now() + GONE_WAIT
    while True:
        pane.sleep(max(0.0, min(POLL, until - pane.now())))
        frame = pane.read()
        if frame is None or frame.dead:
            return ending("mismatch", "the pane ended after Enter; this screen was to leave "
                                      "with its program running", frame, None if frame is None
                          else match(screen, frame.text, frame.width))
        held = match(screen, frame.text, frame.width, in_mode=frame.in_mode)
        if not held.present:
            return ending(GONE, "", frame, held)
        if pane.now() >= until:
            return ending("mismatch", f"the screen was still there {GONE_WAIT:g} s after "
                                      "Enter", frame, held)


# -- the run pane ---------------------------------------------------------------------------

def _run_pane(slot: int | str) -> tuple[int, str | None]:
    """The slot's number and its run pane's id (None when it has none). Refuses
    `no-slot` for a slot that is neither registered nor a live session."""
    from .registry import read as read_registry
    from .slots import resolve, show

    number = resolve(slot, read_registry())
    found = show(number)
    if found is None:
        raise Refused("no-slot", f"there is no slot {slot}; `moxi slots list` shows the "
                                 "slots")
    return number, found.pane_id


def _screens(screens) -> tuple[Screen, ...]:
    if isinstance(screens, Screen):
        return (screens,)
    if isinstance(screens, (str, bytes)) or not isinstance(screens, Sequence) \
            or not screens or not all(isinstance(s, Screen) for s in screens):
        raise UsageError("screens is a Screen or a list of them (CLAUDE_TRUST_FOLDER, "
                         "CLAUDE_DEV_CHANNELS, CLAUDE_EXIT_DIALOG)")
    return tuple(screens)


def _seen(held: Match | None, frame: Frame | None, pane_id: str | None,
          screens: Sequence[Screen]) -> Seen:
    if frame is None:
        return Seen(None, False, "the run pane is gone", (), True, pane_id)
    if held is None or not held.present:
        names = ", ".join(s.name for s in screens)
        found = None if held is None else held.rows
        return Seen(None, False, f"none of {names} is on the pane", found or (),
                    frame.dead, pane_id)
    if frame.dead:
        return Seen(held.screen, False, "the pane has ended; its last frame is not "
                                        "answered", held.rows, True, pane_id)
    return Seen(held.screen, held.full, held.why, held.rows, False, pane_id)


def read_screen(slot: int | str, screens) -> Seen:
    """Which of `screens` (a `Screen` or a list) the slot's run pane shows, from one
    read, and whether it fully matches. Sends nothing."""
    screens = _screens(screens)
    number, pane_id = _run_pane(slot)
    if pane_id is None:
        return Seen(None, False, f"slot {number} has no run pane")
    frame = TmuxPane(number, pane_id).read()
    held = None if frame is None else find(screens, frame.text, frame.width,
                                           in_mode=frame.in_mode)
    if frame is not None and held is None:
        held = match(screens[0], frame.text, frame.width, in_mode=frame.in_mode)
    return _seen(held, frame, pane_id, screens)


def answer_screen(slot: int | str, screen: Screen, wait_s: float = 5) -> Answer:
    """Answer `screen` on the slot's run pane by its keys, after a full match; see the
    module's text for the steps. Returns an `Answer` and never raises for what it found
    on the pane."""
    if not isinstance(screen, Screen):
        raise UsageError("screen is one Screen (CLAUDE_TRUST_FOLDER, CLAUDE_DEV_CHANNELS, "
                         "CLAUDE_EXIT_DIALOG)")
    if isinstance(wait_s, bool) or not isinstance(wait_s, (int, float)) or wait_s < 0:
        raise UsageError(f"wait_s is a number of seconds, not {wait_s!r}")
    number, pane_id = _run_pane(slot)
    if pane_id is None:
        return Answer("no-pane", screen, f"slot {number} has no run pane")
    pane = TmuxPane(number, pane_id)
    done = drive(pane, screen, float(wait_s))
    seen = None
    if done.frame is not None or done.outcome == "no-pane":
        seen = _seen(done.match, done.frame, pane_id, (screen,))
    capture, why = None, done.why
    if done.outcome == "mismatch" and done.frame is not None:
        try:
            capture = str(save_mismatch(screen, why, done.frame, pane.ansi()))
        except OSError as exc:
            why = f"{why} (the region could not be saved: {exc})"
    return Answer(done.outcome, screen, why, seen, capture, tuple(done.keys))


# -- the mismatches store -------------------------------------------------------------------

def _region_rows(text: str, width: int) -> list[str]:
    """The raw rows of `text` (escape sequences kept) below its last full-width rule."""
    raw = text.split("\n")
    flat = rows(text)
    last = max((i for i, row in enumerate(flat) if is_rule(row, width)), default=None)
    if last is None:
        return []
    below = raw[last + 1:]
    while below and not below[-1].strip():
        below.pop()
    return below


def save_mismatch(screen: Screen, why: str, frame: Frame, ansi: str) -> Path:
    """Save the region of a mismatched screen, plain and with escape sequences, as one
    file in the `mismatches` store; keep the last `KEEP`. Returns the file's path."""
    directory = files.private_dir(places.mismatches_dir())
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "screen": screen.name, "why": why, "width": frame.width,
              "plain": _region_rows(frame.text, frame.width),
              "ansi": _region_rows(ansi, frame.width)}
    path = directory / f"{time.time_ns():020d}-{screen.name.lower()}.json"
    files.atomic_write(path, json.dumps(record, indent=1, ensure_ascii=False) + "\n")
    prune(directory)
    return path


def prune(directory: Path | None = None, keep: int = KEEP) -> list[str]:
    """Remove all but the newest `keep` saved regions; returns the names removed."""
    directory = places.mismatches_dir() if directory is None else directory
    try:
        names = sorted(e.name for e in os.scandir(directory)
                       if e.name.endswith(".json") and e.is_file(follow_symlinks=False))
    except FileNotFoundError:
        return []
    gone = names[:-keep] if keep > 0 else names
    for name in gone:
        try:
            os.remove(directory / name)
        except FileNotFoundError:
            pass
    return gone


# -- pane use --------------------------------------------------------------------------------

def pane_use(slot: int | str) -> PaneUse:
    """The read behind a launcher's check for a person at the slot's run pane, in one
    tmux call: whether the pane is in a tmux mode (copy mode sends keys to tmux, not to
    the program), the attached clients' latest activity, and the run window's."""
    number, pane_id = _run_pane(slot)
    if pane_id is None:
        return PaneUse(number, None)
    result = tmux.attempt("display-message", "-p", "-t", pane_id,
                          "#{pane_id}\t#{pane_in_mode}\t#{window_activity}"
                          "\t#{session_attached}",
                          ";", "list-clients", "-t", session_target(number), "-F",
                          "#{client_activity}")
    lines = result.out.splitlines()
    parts = lines[0].split("\t") if lines else []
    if len(parts) != 4 or parts[0] != pane_id:
        if result.ok or tmux.no_server(result.err) or "can't find" in result.err:
            return PaneUse(number, None)
        raise TmuxError(f"tmux display-message failed (exit {result.code}): "
                        f"{result.err.strip() or 'no message'}")
    if not result.ok and not tmux.no_server(result.err):
        raise TmuxError(f"tmux list-clients failed (exit {result.code}): "
                        f"{result.err.strip() or 'no message'}")
    clients = [int(x) for x in (line.strip() for line in lines[1:]) if x.isdigit()]
    return PaneUse(number, pane_id, parts[1] not in ("", "0"),
                   int(parts[3]) if parts[3].isdigit() else 0,
                   float(max(clients)) if clients else None,
                   float(parts[2]) if parts[2].isdigit() else None)


__all__ = ["POLL", "STABLE", "KEY_POLL", "SETTLE_GAP", "SETTLE_BOUND", "ENDED_WAIT",
           "GONE_WAIT", "KEEP", "KEYS", "Frame", "TmuxPane", "Driven", "drive",
           "read_screen", "answer_screen", "save_mismatch", "prune", "pane_use"]
