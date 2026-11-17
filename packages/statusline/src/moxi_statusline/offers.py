"""The two offers made over a user's own status line, and what each would set.

After detection (`moxi_statusline.detect`) the user sees what their line shows, then
chooses:

1. **Switch** to Moxi's line, set up to show the same: `show` is the pieces found, in their
   order and rows (the 5h/7d limits are usage's item), then the installed modules' segments
   the old line lacked, at the end of the first row. Base fields the old line didn't show
   stay off: the user left them out. Limits found while usage isn't installed are left
   off, and `moxi install usage` is said. A detection that found nothing switches to the
   default `show`. The old line is kept for `moxi statusline restore`.
2. **Keep** the line. Nothing is written; the commands for Moxi's pieces are printed
   (`moxi_statusline.pieces`), cache's timer first, each with its measured cost.
"""

from __future__ import annotations

from collections.abc import Sequence

from . import pieces as pieces_
from .detect import Detection
from .fields import ROW

#: What the summary calls a piece, when not its own name.
NAMES = {"limits": "5h/7d limits"}
#: The item of `show` a piece becomes, when not its own name.
ITEMS = {"limits": "usage"}
#: The module whose segment shows Claude's limits.
LIMITS_MODULE = "usage"
INSTALL_USAGE = ("Your line's 5h/7d limits show in Moxi's line once usage is installed: "
                 "moxi install usage")
CHOICES = (
    "  1. Switch to Moxi's line, set up to show the same, plus what yours lacks.",
    "     Your current setting is kept; moxi statusline restore puts it back.",
    "  2. Keep your line. Moxi prints the commands to add its pieces to it.",
)
CHOICES_DEFAULT = (
    "  1. Switch to Moxi's line with its default items.",
    "     Your current setting is kept; moxi statusline restore puts it back.",
    "  2. Keep your line. Moxi prints the commands to add its pieces to it.",
)
CHOICE = "Which? [1/2] "


def name(piece: str) -> str:
    return NAMES.get(piece, piece)


def _segment_order(segments: Sequence[str], default: Sequence[str]) -> list[str]:
    """`segments` in the order the default `show` gives them, then the rest by name."""
    known = [item for item in default if item in segments]
    return known + sorted(s for s in set(segments) if s not in known)


def preset(found: Detection, segments: Sequence[str], default: Sequence[str]
           ) -> tuple[list[str] | None, list[str], bool]:
    """`(show, added, needs_usage)` for the switch: the items, the segments added to them,
    and whether limits were found with usage not installed. `show` is None when nothing
    found becomes an item: the switch then takes the default `show`."""
    needs_usage = "limits" in found.pieces() and LIMITS_MODULE not in segments
    rows = []
    for row in found.rows():
        items = [ITEMS.get(piece, piece) for piece in row
                 if not (piece == "limits" and needs_usage)]
        if items:
            rows.append(items)
    if not rows:
        return None, [], needs_usage
    first = rows[0]
    second = [item for row in rows[1:] for item in row]
    added = [s for s in _segment_order(segments, default) if s not in first + second]
    show = first + added + ([ROW, *second] if second else [])
    return show, added, needs_usage


def summary(found: Detection, segments: Sequence[str], default: Sequence[str]
            ) -> list[str]:
    """What is printed before the choice: what the line shows, other text it shows, and
    what Moxi's line would add (or, when nothing was found, what it would show)."""
    out = []
    if found.problem:
        out.append(found.problem)
    rows = found.rows()
    if rows:
        out.append("Your status line shows: "
                   + " | ".join(", ".join(name(p) for p in row) for row in rows))
    elif found.ran and not found.problem:
        out.append("Moxi found none of its pieces on your status line.")
    if found.also:
        out.append(f"It also shows other text (it may be only labels or separators): "
                   f"\"{found.also}\"")
    show, added, _ = preset(found, segments, default)
    if show is None:
        out.append("Moxi's line would show its default items: "
                   + ", ".join(item for item in default))
    elif added:
        out.append("Moxi's line would add: " + ", ".join(pieces_.label(s) for s in added))
    return out


def choices(found: Detection, segments: Sequence[str], default: Sequence[str]
            ) -> tuple[str, ...]:
    show, _, _ = preset(found, segments, default)
    return CHOICES_DEFAULT if show is None else CHOICES


def unattended(command: str | None, found: Sequence[pieces_.Piece] | None) -> list[str]:
    """With nobody at the keyboard: both offers' commands, and nothing run or written.
    `found` is None when the pieces' commands can't be given (the render isn't
    installed here)."""
    runs = f" ({command})" if command else ""
    out = [f"Claude Code's status line is your own{runs}; it is left as it is.",
           "To switch to Moxi's line, keeping yours for restore, run at a terminal: moxi "
           "statusline use"]
    if found is None:
        out.append("To add Moxi's pieces to your line: moxi statusline pieces")
    elif found:
        out += pieces_.lines(found)
        out.append("moxi statusline pieces prints these with what each costs.")
    return out


__all__ = ["preset", "summary", "choices", "unattended", "name", "INSTALL_USAGE", "CHOICE",
           "CHOICES", "CHOICES_DEFAULT"]
