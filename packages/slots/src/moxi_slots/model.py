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


#: What a launch's exec verdict says: the command started (`execve` succeeded), it did not
#: (the line says why), or no verdict came within the bound.
STARTED = "started"
FAILED = "failed"
UNCONFIRMED = "unconfirmed"
EXEC_OUTCOMES = (STARTED, FAILED, UNCONFIRMED)


@dataclass(frozen=True)
class Placed:
    """Where a `start` or `respawn` put a launch, and its exec verdict.

    `exec` is `started` only on evidence that `execve` succeeded; `failed` with
    `exec_line` saying why (`failed: cwd: ...`, `not taken`, `trampoline died`, ...);
    `unconfirmed` when the launch was taken but no verdict came within the bound.
    `dead_status` is the run pane's exit status when it had already died at the
    verdict (a command that ran and ended, or one that failed), else None."""

    slot: int
    pane_id: str
    window_id: str
    exec: str
    exec_line: str
    dead_status: int | None = None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Job:
    """A detached job in session `moxi-jobs`. `running` is whether its pane lives;
    `exec`, `exec_line` and `dead_status` are the exec verdict, set by `run_job` and
    None from a read."""

    name: str
    window_id: str
    pane_id: str
    running: bool
    pane_pid: int | None = None
    exec: str | None = None
    exec_line: str | None = None
    dead_status: int | None = None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Ended:
    """What `wait_ended` saw: whether the pane ended within the bound, its exit status
    when tmux kept it, and whether the pane is gone altogether."""

    slot: int
    pane_id: str
    ended: bool
    dead_status: int | None = None
    gone: bool = False

    def to_json(self) -> dict:
        return asdict(self)


#: What `answer_screen` ends in. `gone` and `ended` are the verify step's success for a
#: screen's `then`; `not-ended` is a pane still live 15 s after Enter on an `ended`
#: screen; `mismatch` is no Enter sent (or a screen that stayed after it), `why` saying
#: what differed and, for a spent settle bound, starting with `not-settled`;
#: `ended-without-screen` is a pane found dead, no key sent; `no-pane` a slot with no
#: run pane.
ANSWER_OUTCOMES = ("gone", "ended", "not-ended", "mismatch", "ended-without-screen",
                   "no-pane")
#: The one `Answer.why` prefix a caller may match: a key sent whose effect never settled.
NOT_SETTLED = "not-settled"


@dataclass(frozen=True)
class Seen:
    """One read of a slot's run pane against the screens asked about.

    `screen` is the first of them whose phrases are in its region (the `Screen`), or
    None; `full` whether it fully matches; `why` why not (or why no screen is there);
    `rows` the region read; `dead` whether the pane has ended (a dead pane's last frame
    never fully matches); `pane_id` the run pane read, None when the slot has none."""

    screen: object | None
    full: bool
    why: str
    rows: tuple[str, ...] = ()
    dead: bool = False
    pane_id: str | None = None

    def to_json(self) -> dict:
        return {"screen": None if self.screen is None else self.screen.name,
                "full": self.full, "why": self.why, "rows": list(self.rows),
                "dead": self.dead, "pane_id": self.pane_id}


@dataclass(frozen=True)
class Answer:
    """What `answer_screen` did: `outcome` (a word of `ANSWER_OUTCOMES`), the `screen`
    asked for, `why` (prose, empty on success), the last read `seen`, and `capture`, the
    saved region's path on a `mismatch` (None otherwise, or when it could not be
    saved). `keys` lists the keys sent, in order."""

    outcome: str
    screen: object
    why: str = ""
    seen: Seen | None = None
    capture: str | None = None
    keys: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {"outcome": self.outcome, "screen": self.screen.name, "why": self.why,
                "seen": None if self.seen is None else self.seen.to_json(),
                "capture": self.capture, "keys": list(self.keys)}


@dataclass(frozen=True)
class PaneUse:
    """Whether a person may be at a slot's run pane: `pane_in_mode` (copy mode or another
    tmux mode), `attached` (clients on the slot's session), `client_activity` (the
    latest activity of those clients, epoch seconds, None with none attached) and
    `pane_activity` (the run window's last output or input; the run window has the one
    pane). `pane_id` is None, and the rest empty, when the slot has no run pane."""

    slot: int
    pane_id: str | None
    pane_in_mode: bool = False
    attached: int = 0
    client_activity: float | None = None
    pane_activity: float | None = None

    def to_json(self) -> dict:
        return asdict(self)


#: A job name: letters, digits, `.`, `_`, `:` and `-`, 1-128 characters.
JOB_NAME_MAX = 128
_JOB_NAME = re.compile(r"[A-Za-z0-9._:-]+")


def check_job_name(name: object) -> str:
    if not isinstance(name, str) or not _JOB_NAME.fullmatch(name) \
            or len(name) > JOB_NAME_MAX:
        raise UsageError(f"a job name is 1-{JOB_NAME_MAX} letters, digits, '.', '_', ':' "
                         f"and '-'; {name!r} is not one")
    return name


def launchable(state: str, hold: Hold | None, holder: str | None = None) -> bool:
    """Whether a slot in `state` with live hold `hold` takes a launch from `holder`."""
    return state in (FREE, ENDED) and (hold is None or hold.holder == holder)


__all__ = ["LOWEST", "HIGHEST", "SESSION_PREFIX", "HOME_MARK", "RUN_MARK", "MISSING",
           "FREE", "ENDED", "OCCUPIED", "STATES", "NAME_MAX", "LABEL_MAX", "HOLDER_MAX",
           "META_MAX", "HOLD_MAX", "session_name", "session_target", "window_target",
           "number_of", "check_number", "check_name", "check_label", "check_holder",
           "check_meta", "check_job_name", "Hold", "Last", "Slot", "Drift", "Listing",
           "Removed", "STARTED", "FAILED", "UNCONFIRMED", "EXEC_OUTCOMES", "Placed", "Job",
           "Ended", "ANSWER_OUTCOMES", "NOT_SETTLED", "Seen", "Answer", "PaneUse",
           "JOB_NAME_MAX", "launchable"]
