"""The screens drawn outside a session, as data, and the one rule that matches them.

slots types only on screens no session has control of: Claude Code's startup warnings
and its exit dialog. Each is a `Screen` here, and `answer_screen` presses a key only
after a full match against one of them (see `answer`).

The matcher reads plain text: `capture-pane -p`, or an `-e` capture with its escape
sequences removed (`plain`), since a capture with colour can put a colour change between
the words of one phrase. Claude Code draws a no-break space after its marker; it reads
as a space.

The region of a screen is the rows below the last full-width rule: a row of `─` at
least as wide as the pane at the read. A screen with no such row has no region, so it
matches nothing.

The rule: a screen fully matches when every phrase is in its region in order, its
option row is there, exactly one row in the region is marked, its item rows are only of
the kinds allowed, and the pane is not in copy mode. Where the marker sits is not part of
the match; it decides the key.

An option row is a region row whose text, without the marker, spaces and any
`<digits>. ` index, equals the option. A marked row is a region row whose text starts
with the marker.

Recognition is by content only: no Claude Code version is read.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

#: Claude Code's marker for the chosen row of a dialog.
MARKER = "❯"
#: The rule a dialog sits below.
RULE = "─"
#: What `then` may be: the pane must die, or the screen must leave.
ENDED = "ended"
GONE = "gone"

# Escape sequences a `capture-pane -e` holds: CSI (colours, styles), OSC (links, titles,
# ended by BEL or ST), and the two-byte ones.
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC = re.compile(r"\x1b[@-Z\\-_]")
_INDEX = re.compile(r"\d+\.\s+")


@dataclass(frozen=True)
class Items:
    """The rows a screen lists between a phrase and its choices, each `<kind> · <what>`.

    `after` is the phrase on the row they follow; `kinds` the kinds allowed. The item
    rows end at the first marked or numbered row."""

    after: str
    kinds: tuple[str, ...]
    sep: str = " · "


@dataclass(frozen=True)
class Screen:
    """A screen slots may answer.

    `phrases` must all be in the region, in order; `option` is the row to choose;
    `marker` marks the chosen row; `items`, when set, limits the item rows to its kinds;
    `move` says whether Up and Down may move the marker toward the option (when it is
    False the key is Enter alone); `then` is what must follow Enter: `ended` (the pane
    dies) or `gone` (the screen leaves)."""

    name: str
    phrases: tuple[str, ...]
    option: str
    move: bool
    then: str
    marker: str = MARKER
    items: Items | None = None

    def __post_init__(self):
        if self.then not in (ENDED, GONE):
            raise ValueError(f"then is {ENDED!r} or {GONE!r}, not {self.then!r}")
        if not self.phrases or not self.option or not self.marker:
            raise ValueError("a screen needs phrases, an option and a marker")

    def to_json(self) -> dict:
        return {"name": self.name, "phrases": list(self.phrases), "option": self.option,
                "move": self.move, "then": self.then, "marker": self.marker,
                "items": None if self.items is None else
                {"after": self.items.after, "kinds": list(self.items.kinds)}}


#: Claude Code's exit dialog with background work running. Its one key is Enter, on
#: "Exit and stop tasks", and only shells and monitors may be listed.
CLAUDE_EXIT_DIALOG = Screen(
    name="CLAUDE_EXIT_DIALOG",
    phrases=("Background work is running", "will stop when you exit"),
    option="Exit and stop tasks", move=False, then=ENDED,
    items=Items(after="will stop when you exit", kinds=("shell", "monitor")))

#: The folder trust warning. It draws the marker on "No, exit" above the option, so
#: answering it takes one Down, then Enter.
CLAUDE_TRUST_FOLDER = Screen(
    name="CLAUDE_TRUST_FOLDER",
    phrases=("Accessing workspace:", "Enter to confirm"),
    option="Yes, I trust this folder", move=True, then=GONE)

#: The development channels warning, drawn with the marker on the option.
CLAUDE_DEV_CHANNELS = Screen(
    name="CLAUDE_DEV_CHANNELS",
    phrases=("Loading development channels", "Enter to confirm"),
    option="I am using this for local development", move=True, then=GONE)

#: Every screen slots ships.
SCREENS = (CLAUDE_EXIT_DIALOG, CLAUDE_TRUST_FOLDER, CLAUDE_DEV_CHANNELS)


def plain(text: str) -> str:
    """`text` without escape sequences, a no-break space read as a space."""
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _ESC.sub("", text)
    return text.replace(" ", " ")


def rows(text: str) -> list[str]:
    """The rows of a capture, as plain text with trailing blanks cut."""
    return [row.rstrip() for row in plain(text).split("\n")]


def is_rule(row: str, width: int) -> bool:
    """Whether `row` is a full-width rule: only `─`, at least `width` of them."""
    row = row.rstrip()
    return len(row) >= max(width, 1) and set(row) == {RULE}


def region(text: str, width: int) -> tuple[str, ...] | None:
    """The rows below the last full-width rule (trailing blank rows dropped), or None
    when there is no such rule."""
    found = rows(text)
    last = None
    for i, row in enumerate(found):
        if is_rule(row, width):
            last = i
    if last is None:
        return None
    below = found[last + 1:]
    while below and not below[-1].strip():
        below.pop()
    return tuple(below)


def bare(row: str, marker: str = MARKER) -> str:
    """A row's text without the marker, spaces and a `<digits>. ` index."""
    text = row.strip()
    if text.startswith(marker):
        text = text[len(marker):].strip()
    found = _INDEX.match(text)
    if found:
        text = text[found.end():]
    return text.strip()


def marked(row: str, marker: str = MARKER) -> bool:
    return row.lstrip().startswith(marker)


@dataclass(frozen=True)
class Match:
    """One screen held against one read.

    `present`: every phrase is in the region, in order. `full`: the whole rule holds.
    `why` says what failed (empty on a full match). `rows` is the region. `marked` and
    `option` are the region indexes of the marked row (when exactly one) and of the
    option row, else None."""

    screen: Screen
    present: bool
    full: bool
    why: str
    rows: tuple[str, ...]
    marked: int | None = None
    option: int | None = None


def _in_order(phrases: Sequence[str], body: str) -> str | None:
    """The first phrase not found after the one before it, or None when all are."""
    at = 0
    for phrase in phrases:
        found = body.find(phrase, at)
        if found < 0:
            return phrase
        at = found + len(phrase)
    return None


def _items_why(screen: Screen, region_rows: tuple[str, ...]) -> str:
    items = screen.items
    start = next((i for i, row in enumerate(region_rows) if items.after in row), None)
    if start is None:
        return f"no row holds {items.after!r}"
    for row in region_rows[start + 1:]:
        text = row.strip()
        if not text:
            continue
        if marked(row, screen.marker) or _INDEX.match(text):
            break
        kind = text.split(items.sep, 1)[0] if items.sep in text else None
        if kind not in items.kinds:
            return (f"the item row {text!r} is not of the kinds "
                    f"{', '.join(items.kinds)}")
    return ""


def match(screen: Screen, text: str, width: int, *, in_mode: bool = False) -> Match:
    """Hold `screen` against a capture `text` of a pane `width` columns wide."""
    found = region(text, width)
    if found is None:
        return Match(screen, False, False, "no full-width rule on screen", ())
    missing = _in_order(screen.phrases, "\n".join(found))
    if missing is not None:
        return Match(screen, False, False,
                     f"{missing!r} is not below the last full-width rule, after the "
                     "phrases before it", found)
    marks = [i for i, row in enumerate(found) if marked(row, screen.marker)]
    options = [i for i, row in enumerate(found)
               if bare(row, screen.marker) == screen.option]
    mark = marks[0] if len(marks) == 1 else None
    option = options[-1] if options else None
    why = ""
    if in_mode:
        why = "the pane is in copy mode"
    elif option is None:
        why = f"no row is the option {screen.option!r}"
    elif len(marks) != 1:
        why = f"{len(marks)} rows are marked with {screen.marker!r}, not one"
    elif screen.items is not None:
        why = _items_why(screen, found)
    return Match(screen, True, not why, why, found, mark, option)


def find(screens: Sequence[Screen], text: str, width: int, *,
         in_mode: bool = False) -> Match | None:
    """The match of the first of `screens` present in the capture, or None."""
    for screen in screens:
        held = match(screen, text, width, in_mode=in_mode)
        if held.present:
            return held
    return None


__all__ = ["MARKER", "RULE", "ENDED", "GONE", "Items", "Screen", "CLAUDE_EXIT_DIALOG",
           "CLAUDE_TRUST_FOLDER", "CLAUDE_DEV_CHANNELS", "SCREENS", "plain", "rows",
           "is_rule", "region", "bare", "marked", "Match", "match", "find"]
