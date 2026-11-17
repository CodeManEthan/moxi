"""A headless session's exit rule, and the watch that ends the session by it.

A headless launch (`--headless`) binds the name, starts the session with no window, hands
it the brief as its first turn and then runs as a session nobody sits in. It ends on the
first of: a cause its exit rule fires, `moxi comms exit-session`, the ready bound, a
failed brief, its host ending, or its adapter ending. Each of them ends the host, and the
name goes with it.

The exit rule is the launch's `--exit` values, each one of:

    done        the brief's turn ended `answer`, no turn runs, and the daemon's read
                after the turn's settle window shows nothing handed to the binding
                and unacked
    cold        the daemon reads the session's cache `cold` while no turn runs; also,
                cause `cache-unreadable`, when every read for `HEADLESS_READY_WAIT_S`
                with no turn between was `unknown`; never on `no-cache`
    idle=<D>    no turn has run for D since the last turn ended
    after=<D>   D since the bind, a running turn included

D is a whole number with its unit inside it, `s`, `m` or `h` (`90m`, `2h`). With no
`--exit` the rule is `done`. `parse_exit` is the one parser of a rule: the wrappers, the
adapters and, through comms' API, the launcher use it.

`ExitWatch` holds the ending rule's clauses on arming, the `unknown` span, the reset at a
turn and the wait for background work; the adapter feeds it the turn events it already
watches and the reads it makes, and asks `due()` for the cause that fires now. It is pure:
its clock is injected and it reads nothing itself.

- Nothing but `after=` and the ready bound runs before the brief's turn ends. `done`,
  `cold` and `idle=` are armed only when the brief's turn ends `answer`. A brief turns
  refuses or loses ends the session at once, cause `brief-failed`; so does a session the
  adapter has not found within `HEADLESS_READY_WAIT_S` of the bind. A brief turn that
  ends `error`, `aborted` or `refusal` ends it too, after the wait for background work.
- A turn drops any cause that waits for background work, and is judged again after it.
  `idle=` and the `unknown` span count from the last turn's end. `after=` fires whatever
  runs.
- An automatic cause on an idle session after a turn (`done`, `cold`,
  `cache-unreadable`, `idle`, and `brief-failed` on a brief turn that ended) waits while
  the session lists busy work (shells, monitors, background agents), until the list is
  empty or the session has been idle `SHELLS_WAIT_S`. A list turns can't vouch for counts
  as busy. A session that offers no such list waits for nothing.

`end(handle, cause)` logs the cause and sends SIGTERM to the host through its pidfd, so
a pid the system has given to another process since can't take it.
"""

from __future__ import annotations

import logging
import re
import signal
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

log = logging.getLogger("comms.headless")

#: How long a headless session has to get ready, and how long its cache may read
#: `unknown` with no turn before `cold` fires as `cache-unreadable`, in seconds.
HEADLESS_READY_WAIT_S = 300
#: How often the adapter asks the watch for a cause when no event comes, in seconds.
TICK_S = 5.0
#: How long after a turn's end `done` reads the daemon's rows, in seconds: the last read
#: of the daemon's settle window after an unmarked turn end (8 s,
#: `daemon.RELEASE_SETTLE_STEPS`), plus one tick.
DONE_READ_AFTER_S = 8.0 + TICK_S
#: How often the adapter reads its own name's cache while no turn runs, in seconds.
CACHE_READ_EVERY_S = 60.0
#: How long an automatic cause waits for the session's busy work, counted from the last
#: turn's end, in seconds.
SHELLS_WAIT_S = 3600.0

#: The brief's delivery ref.
BRIEF_REF = "brief"
#: The harnesses whose wrapper a rule may be parsed for.
HARNESSES = ("claude", "codex")
#: The rule with no `--exit`.
DEFAULT_EXIT = ("done",)

#: The causes of a headless session's end, as its adapter's log line names them.
DONE, COLD, CACHE_UNREADABLE, IDLE, AFTER = "done", "cold", "cache-unreadable", "idle", "after"
EXIT_SESSION, BRIEF_FAILED, HOST_ENDED = "exit-session", "brief-failed", "host-ended"
CAUSES = (DONE, COLD, CACHE_UNREADABLE, IDLE, AFTER, EXIT_SESSION, BRIEF_FAILED, HOST_ENDED)
#: The causes that wait for the session's busy work.
WAITS_FOR_SHELLS = (DONE, COLD, CACHE_UNREADABLE, IDLE, BRIEF_FAILED)

#: The turn outcome that arms the rule.
ANSWER = "answer"

#: The warning a rule with `idle=` or `after=` and no `cold` gets.
COLD_GAP = ("mail sent once its cache is cold waits for the name's next holder; add "
            "--exit cold to end the session then")

_DURATION = re.compile(r"(\d+)([smh])")
_UNIT_S = {"s": 1, "m": 60, "h": 3600}


@dataclass(frozen=True)
class ExitRule:
    """A parsed exit rule. `values` is the rule as `--exit` values, in the order given
    (`("done",)` when none was given), the form the claim file records."""

    values: tuple[str, ...]
    done: bool = False
    cold: bool = False
    idle_s: int | None = None
    after_s: int | None = None

    @property
    def cold_gap(self) -> bool:
        """Whether mail held for a cold cache can outlast the session unanswered: the
        rule has `idle=` or `after=` and no `cold`."""
        return (self.idle_s is not None or self.after_s is not None) and not self.cold


def _duration_s(value: str, text: str) -> int:
    match = _DURATION.fullmatch(text)
    if match is None or int(match.group(1)) == 0:
        raise ValueError(f"--exit {value}: the duration is a whole number above 0 with its "
                         "unit, s, m or h (like 90m or 2h)")
    return int(match.group(1)) * _UNIT_S[match.group(2)]


def parse_exit(values: Iterable[str] | None, *, harness: str) -> ExitRule:
    """The exit rule `values` (the `--exit` values, in order) give a headless launch of
    `harness`. Raises `ValueError` with the wrapper's words, one line naming the fix,
    for a value outside the rule's grammar, a value given twice, a harness that has no
    headless launch, or `cold` while the cache module is not installed. No values is the
    rule `done`."""
    if harness not in HARNESSES:
        raise ValueError(f"{harness} has no headless launch; one of "
                         f"{', '.join(HARNESSES)} has")
    if isinstance(values, str):
        raise TypeError("values is an iterable of --exit values, not one string")
    given = tuple(values or ())
    fields: dict = {}
    for value in given:
        if not isinstance(value, str):
            raise ValueError(f"--exit {value!r} is not text")
        kind, sep, rest = value.partition("=")
        if value in ("done", "cold"):
            key, field = value, True
        elif sep and kind == "idle":
            key, field = "idle_s", _duration_s(value, rest)
        elif sep and kind == "after":
            key, field = "after_s", _duration_s(value, rest)
        else:
            raise ValueError(f"--exit {value}: give done, cold, idle=<D> or after=<D>, D "
                             "like 90m or 2h")
        if key in fields:
            name = value if key in ("done", "cold") else f"{kind}="
            raise ValueError(f"--exit {name} is given twice; give each value once")
        fields[key] = field
    if fields.get("cold"):
        from .. import cache

        if not cache.present():
            raise ValueError("--exit cold needs the cache module, which is not installed; "
                             "install moxi-cache or leave cold out")
    return ExitRule(values=given or DEFAULT_EXIT, **({"done": True} if not given else {}),
                    **fields)


class ExitWatch:
    """One headless session's ending rule, fed by its adapter. See the module's
    docstring for the clauses; every time is read from `clock` (monotonic seconds).

    The adapter calls the feeds from the events it watches and the reads it makes:
    `on_found()` once it has found the session, `on_brief_failed(why)` when turns refused
    or lost the brief, `on_turn_started()`, `on_turn_ended(outcome, brief=...)`,
    `note_mail(handed=, waiting=)` (the daemon's `agents` answer, after a `look`),
    `note_cache_kind(kind)` (the daemon's `session` read), and `note_shells(busy=)` (the
    session's busy count, None when its list is not complete). It asks `wants_mail_read()`,
    `wants_cache_read()` and `wants_shells()` which read is owed now, and `due()` for the
    cause that fires now. `offers_shells` says whether the session lists its busy work;
    without it no cause waits.
    """

    def __init__(self, rule: ExitRule, clock: Callable[[], float] = time.monotonic, *,
                 ready_wait_s: float = HEADLESS_READY_WAIT_S,
                 done_read_after_s: float = DONE_READ_AFTER_S,
                 cache_read_every_s: float = CACHE_READ_EVERY_S,
                 shells_wait_s: float = SHELLS_WAIT_S, tick_s: float = TICK_S):
        self.rule = rule
        self.clock = clock
        self.ready_wait_s = ready_wait_s
        self.done_read_after_s = done_read_after_s
        self.cache_read_every_s = cache_read_every_s
        self.shells_wait_s = shells_wait_s
        self.tick_s = tick_s
        #: When the name was bound: `after=` and the ready bound count from here.
        self.bound_at = clock()
        self.found = False
        self.offers_shells = False
        #: Why the brief failed before any turn ran, once it has.
        self.brief_refused: str | None = None
        #: The brief turn's outcome, once it has ended.
        self.brief_outcome: str | None = None
        self.turn_running = False
        self.last_turn_end: float | None = None
        #: The daemon's last mail read since the last turn's end: (at, handed, waiting).
        self.mail: tuple[float, int, int] | None = None
        #: The rows the daemon last said wait for the name, whenever that read was.
        self.waiting = 0
        self.cache_at: float | None = None
        self.cache_kind: str | None = None
        #: When the run of `unknown` reads with no turn between began.
        self.unknown_since: float | None = None
        #: Whether the first `no-cache` read was said.
        self.no_cache_said = False
        #: The automatic cause that waits for the session's busy work, and since when.
        self.pending: tuple[str, float] | None = None
        #: The last busy count read: (at, busy), busy None for a list not complete.
        self.shells: tuple[float, int | None] | None = None

    # ------------------------------------------------------------------ feeds
    def on_found(self) -> None:
        self.found = True

    def on_brief_failed(self, why: str) -> None:
        """turns refused or lost the brief: no turn ran, and none will."""
        if self.brief_refused is None and self.brief_outcome is None:
            self.brief_refused = why or "refused"

    def on_turn_started(self) -> None:
        self.turn_running = True
        self.pending = None
        self.shells = None
        self.mail = None
        self.cache_kind = None
        self.unknown_since = None

    def on_turn_ended(self, outcome: str, *, brief: bool = False) -> None:
        self.turn_running = False
        self.last_turn_end = self.clock()
        self.mail = None
        self.cache_kind = None
        self.unknown_since = None
        if brief and self.brief_outcome is None:
            self.brief_outcome = outcome

    def note_mail(self, *, handed: int, waiting: int) -> None:
        self.waiting = waiting
        if not self.turn_running:
            self.mail = (self.clock(), handed, waiting)

    def note_cache_kind(self, kind: str) -> str | None:
        """One read of the session's cache. Returns a line to log once, at the first
        `no-cache` read of a rule with `cold`, else None."""
        now = self.clock()
        self.cache_at = now
        if self.turn_running:
            return None
        self.cache_kind = kind
        if kind == "unknown":
            if self.unknown_since is None:
                self.unknown_since = now
        else:
            self.unknown_since = None
        if kind == "no-cache" and self.rule.cold and not self.no_cache_said:
            self.no_cache_said = True
            return "cold will not fire: the cache reads no-cache"
        return None

    def note_shells(self, *, busy: int | None) -> None:
        self.shells = (self.clock(), busy)

    # ------------------------------------------------------------------ asks
    @property
    def armed(self) -> bool:
        """Whether the brief's turn has ended `answer`: `done`, `cold` and `idle=` may
        fire from then on."""
        return self.brief_outcome == ANSWER

    def wants_mail_read(self) -> bool:
        """Whether `done` needs the daemon's rows read now: armed, no turn runs, the last
        turn's settle window has closed, and no read was made since it did."""
        if not (self.rule.done and self.armed and not self.turn_running
                and self.last_turn_end is not None):
            return False
        ready_at = self.last_turn_end + self.done_read_after_s
        if self.clock() < ready_at:
            return False
        return self.mail is None or self.mail[0] < ready_at

    def wants_cache_read(self) -> bool:
        """Whether `cold` needs the cache read now: armed, no turn runs, and no read in
        the last `cache_read_every_s`."""
        if not (self.rule.cold and self.armed and not self.turn_running):
            return False
        return self.cache_at is None or self.clock() - self.cache_at >= self.cache_read_every_s

    def wants_shells(self) -> bool:
        """Whether a cause waits on the session's busy work and the count is owed: none
        read since the cause became pending, or the last read was busy and a tick old."""
        if self.pending is None or not self.offers_shells:
            return False
        if self.shells is None or self.shells[0] < self.pending[1]:
            return True
        return self.shells[1] != 0 and self.clock() - self.shells[0] >= self.tick_s

    def _automatic(self, now: float) -> str | None:
        """The automatic cause that holds now on an idle session, or None."""
        if self.brief_outcome is not None and self.brief_outcome != ANSWER:
            return BRIEF_FAILED
        if not self.armed:
            return None
        rule = self.rule
        if rule.done and self.mail is not None and self.mail[1] == 0 \
                and self.mail[0] >= self.last_turn_end + self.done_read_after_s:
            return DONE
        if rule.cold and self.cache_kind == "cold":
            return COLD
        if rule.cold and self.unknown_since is not None \
                and now - self.unknown_since >= self.ready_wait_s:
            return CACHE_UNREADABLE
        if rule.idle_s is not None and now - self.last_turn_end >= rule.idle_s:
            return IDLE
        return None

    def due(self) -> str | None:
        """The cause that fires now, or None. The first to fire wins; the adapter ends
        the session on it and asks no more."""
        now = self.clock()
        if self.rule.after_s is not None and now - self.bound_at >= self.rule.after_s:
            return AFTER
        if self.brief_refused is not None:
            return BRIEF_FAILED
        if not self.found:
            return BRIEF_FAILED if now - self.bound_at >= self.ready_wait_s else None
        if self.turn_running or self.last_turn_end is None:
            return None
        cause = self._automatic(now)
        if cause is None:
            self.pending = None
            return None
        if not self.offers_shells:
            return cause
        if self.pending is None or self.pending[0] != cause:
            self.pending = (cause, now)
        if self.shells is not None and self.shells[0] >= self.pending[1] \
                and self.shells[1] == 0:
            return cause
        if now - self.last_turn_end >= self.shells_wait_s:
            return cause
        return None

    def why(self, cause: str) -> str:
        """Words for the end line that say more than the cause: which way the brief
        failed, or nothing."""
        if cause != BRIEF_FAILED:
            return ""
        if self.brief_refused is not None:
            return f"the brief was not taken ({self.brief_refused})"
        if self.brief_outcome is not None:
            return f"the brief's turn ended {self.brief_outcome}"
        return f"not ready within {self.ready_wait_s:g}s"


def end_line(cause: str, waiting: int | None, why: str = "") -> str:
    """The one log line a headless session's end writes."""
    rows = "" if waiting is None else f"; {waiting} row(s) left waiting for the name"
    return f"headless session ended: {cause}{f' ({why})' if why else ''}{rows}"


def end(handle: int, cause: str, *, waiting: int | None = None, why: str = "",
        say: Callable[[str], None] | None = None) -> bool:
    """End a headless session: log its cause (`end_line`) and send SIGTERM to the host
    through `handle`, a pidfd on it. True when the signal went; False when the host had
    ended already."""
    (say or log.info)(end_line(cause, waiting, why))
    try:
        signal.pidfd_send_signal(handle, signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True


__all__ = ["AFTER", "BRIEF_FAILED", "BRIEF_REF", "CACHE_READ_EVERY_S", "CACHE_UNREADABLE",
           "CAUSES", "COLD", "COLD_GAP", "DEFAULT_EXIT", "DONE", "DONE_READ_AFTER_S",
           "EXIT_SESSION", "ExitRule", "ExitWatch", "HARNESSES", "HEADLESS_READY_WAIT_S",
           "HOST_ENDED", "IDLE", "SHELLS_WAIT_S", "TICK_S", "WAITS_FOR_SHELLS", "end",
           "end_line", "parse_exit"]
