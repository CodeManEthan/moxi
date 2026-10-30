"""What a slot is, as the API returns it, and the rules for numbers, names, labels,
holders and meta.

A slot is tmux session `slot-<n>`, n from 1 to 999. Its first window is a persistent
shell marked `@moxi_slot_home=on`; launches run in one run window marked
`@moxi_slot_run=on`. A window without a mark is the user's and invisible here.

States:

    missing    registered, but no `slot-<n>` session (a reboot, a stopped server)
    free       the session is live and has no run window
    ended      the run pane is dead (its last screen stays); a respawn reuses it
    occupied   the run pane is live

A slot is launchable when it is `free` or `ended` and unheld, or held by the caller. A
hold is a reservation by a `holder` word until `until` (epoch seconds); past `until` it
reads as absent.

A slot is idle when no client is attached, it has no window but the home window and a
dead run window, and the home pane runs the shell it started with (or has died).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from .errors import UsageError

LOWEST = 1
HIGHEST = 999
SESSION_PREFIX = "slot-"
#: The marks that find a slot's windows; never an index or a window name.
HOME_MARK = "@moxi_slot_home"
RUN_MARK = "@moxi_slot_run"

MISSING = "missing"
FREE = "free"
ENDED = "ended"
OCCUPIED = "occupied"
STATES = (MISSING, FREE, ENDED, OCCUPIED)

NAME_MAX = 32
LABEL_MAX = 64
HOLDER_MAX = 128
META_MAX = 4096
#: The longest a hold may run, in seconds.
HOLD_MAX = 24 * 3600

_NAME = re.compile(r"[a-z0-9-]+")
_SESSION = re.compile(r"slot-([1-9][0-9]{0,2})")


def session_name(number: int) -> str:
    return f"{SESSION_PREFIX}{number}"


def session_target(number: int) -> str:
    """The exact-match target of slot `number`'s session, for commands that take a
    session (`has-session`, `kill-session`, `attach-session`, `switch-client`)."""
    return f"={session_name(number)}"


def window_target(number: int) -> str:
    """The exact-match target of the session's current window, for commands that take
    a window or pane (`set-option`, `new-window`)."""
    return f"={session_name(number)}:"


def number_of(session: str) -> int | None:
    """The slot number of a session name, or None when it isn't `slot-<n>`, n 1-999."""
    found = _SESSION.fullmatch(session)
    return int(found.group(1)) if found else None


def check_number(number: object) -> int:
    if isinstance(number, bool) or not isinstance(number, int):
        raise UsageError(f"a slot number is a whole number, not {number!r}")
    if not LOWEST <= number <= HIGHEST:
        raise UsageError(f"a slot number is from {LOWEST} to {HIGHEST}, not {number}")
    return number


def check_name(name: object) -> str:
    """A slot name: lower-case letters, digits and `-`, 1-32 characters, not all
    digits."""
    if not isinstance(name, str) or not _NAME.fullmatch(name) or len(name) > NAME_MAX \
            or name.isdigit():
        raise UsageError(f"a slot name is 1-{NAME_MAX} lower-case letters, digits and "
                         f"'-', not all digits; {name!r} is not one")
    return name


def check_label(label: object) -> str:
    """A label: up to 64 printable characters."""
    if not isinstance(label, str) or len(label) > LABEL_MAX or not label.isprintable():
        raise UsageError(f"a label is up to {LABEL_MAX} printable characters, not "
                         f"{label!r}")
    return label


def check_holder(holder: object) -> str:
    """A holder word: 1-128 printable characters, no spaces."""
    if not isinstance(holder, str) or not holder or len(holder) > HOLDER_MAX \
            or not holder.isprintable() or any(c.isspace() for c in holder):
        raise UsageError(f"a holder is one word of 1-{HOLDER_MAX} printable characters, "
                         f"not {holder!r}")
    return holder


def check_meta(meta: object) -> dict | None:
    """The caller's meta: a JSON object of at most 4 KiB, or None."""
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise UsageError(f"meta is a JSON object, not {type(meta).__name__}")
    try:
        text = json.dumps(meta, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise UsageError(f"meta is not JSON: {exc}") from None
    if len(text.encode("utf-8")) > META_MAX:
        raise UsageError(f"meta is at most {META_MAX} bytes as JSON")
    return json.loads(text)


@dataclass(frozen=True)
class Hold:
    """A reservation: `holder` has the slot until `until` (epoch seconds)."""

    holder: str
    until: float

    def live(self, now: float) -> bool:
        return self.until > now


@dataclass(frozen=True)
class Last:
    """The last launch placed in the slot: when, its label, and the caller's meta."""

    at: float
    label: str
    meta: dict | None = None


@dataclass(frozen=True)
class Slot:
    """One slot as tmux and the registry show it now.

    `hold` is a live hold or None. The run pane's facts (`pane_id`, `window_id`,
    `pane_pid`, `current_command`, `dead_status`) are None when there is no run
    window. `label` is the last launch's label while its run pane is there. `created`
    is None for a session with no registry row.
    """

    number: int
    name: str | None
    state: str
    hold: Hold | None
    launchable: bool
    idle: bool
    attached: int
    label: str | None
    pane_id: str | None
    window_id: str | None
    pane_pid: int | None
    current_command: str | None
    dead_status: int | None
    last: Last | None
    created: float | None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Drift:
    """Something tmux and the registry disagree on, reported and never repaired.

    `kind` is `no-session` (a registered slot with no session), `no-row` (a `slot-<n>`
    session with no registry row) or `two-run-windows`."""

    number: int
    kind: str
    words: str


@dataclass(frozen=True)
class Listing:
    """Every slot. `ok` False means the slots could not be read (`why` says why), never
    that there are none. `unregistered` are live `slot-<n>` sessions with no row."""

    ok: bool
    why: str = ""
    slots: tuple[Slot, ...] = ()
    unregistered: tuple[Slot, ...] = ()
    drift: tuple[Drift, ...] = ()

    def find(self, number: int) -> Slot | None:
        return next((s for s in (*self.slots, *self.unregistered) if s.number == number),
                    None)

    def to_json(self) -> dict:
        return {"ok": self.ok, "why": self.why,
                "slots": [s.to_json() for s in self.slots],
                "unregistered": [s.to_json() for s in self.unregistered],
                "drift": [asdict(d) for d in self.drift]}


@dataclass(frozen=True)
class Removed:
    """What `remove` did: the numbers it removed, and those it skipped with why."""

    removed: tuple[int, ...] = ()
    skipped: tuple[tuple[int, str], ...] = field(default_factory=tuple)

    def to_json(self) -> dict:
        return {"removed": list(self.removed),
                "skipped": [{"number": n, "why": why} for n, why in self.skipped]}


def launchable(state: str, hold: Hold | None, holder: str | None = None) -> bool:
    """Whether a slot in `state` with live hold `hold` takes a launch from `holder`."""
    return state in (FREE, ENDED) and (hold is None or hold.holder == holder)


__all__ = ["LOWEST", "HIGHEST", "SESSION_PREFIX", "HOME_MARK", "RUN_MARK", "MISSING",
           "FREE", "ENDED", "OCCUPIED", "STATES", "NAME_MAX", "LABEL_MAX", "HOLDER_MAX",
           "META_MAX", "HOLD_MAX", "session_name", "session_target", "window_target",
           "number_of", "check_number", "check_name", "check_label", "check_holder",
           "check_meta", "Hold", "Last", "Slot", "Drift", "Listing", "Removed",
           "launchable"]
