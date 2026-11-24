"""The handlers of rounds' page, the gather that makes the board, and its cache.

**One gather** makes the board's value (`gather`), under one lock, in this order: it
takes the token; reads the open builds once (`open_builds.read`), and nothing else in
that gather reads them again; reads each project's listing once, with no git; reuses
each open round's cached part or makes it again (the round's show, `seen` read with
that one reading of the open builds, `steps.of` once, the groups without any step's
lines); keeps each open build's git state from this gather's reading for the records;
makes the strip; and makes what waits on the operator and the closed rounds from the
listing, with no git. Nothing is written: not a log, not the index.

**The token** (`Token`) is the stat (size, `mtime_ns`) of every round's log, every
project's log, every round's `design.md` and rounds' config file, the listing of each
rounds folder, and each round's status. Each file is stat'ed before it is read, so an
append between the two is caught at the next tick, never filed under the new token. A
round's status is read again only when its log's stat changed.

**A round's part** is kept while its log, its project's log, its `design.md`, the config
file and the status of every round are as they were when it was made. Any round's
status changing drops every part, since a question parked until another round closes
reads open once it has. Git can change with no file written, so every `GIT_PASS_S`
seconds a gather runs whatever the token says (the git pass), and it makes again the
part of every round with an open build or at a step whose state git says. An open
build's own git state is always this gather's reading.

**When a gather runs.** Whoever wants the value, the poller or a route, takes the token
again under the lock: when it matches the last gather's and the git pass is not due, the
last value answers and nothing is gathered. So a route that waited on the poller's
gather answers that gather's value, and a poller that waited on a route's gathers
nothing. The stream's first call starts the poller, which every `PERIOD_S` seconds
takes the token and asks the `Snapshot` for the value only when the token changed, the
git pass is due or a route gathered since it last published; the `Snapshot` publishes
only a value that changed. The poller survives every exception, logged once per class,
and never holds the server's idle exit.

**The strip** is the `board.STRIP_SIZE` builds last acted on, open rounds and closed. A
closed round is read only while its log was written at or after the oldest act of the
strip found so far, newest log first: every event of a log is at or before its last
write, so a round not read could not have put an act in.

**The record route** answers from the last gather's parts and never gathers. A gather
replaces the parts whole when it finishes, so a record never waits on one in flight.

**The step** a round is at is the one `next` reads, with git and its design read. On a
round whose `design.md` changed since its version was frozen, `moxi rounds list` keeps
the step its log alone gives; the board follows `next`, as its here marks and its
group do.

Free text is cut to `TEXT_MAX_CHARS`, and a question's passage to `PASSAGE_MAX_CHARS`,
before a value leaves a handler, so one huge question can't break an answer.

**The acts** (`answers`) run `pageacts` off the server's event loop, with the one store
of receipts this server process keeps (`RECEIPTS`) and comms' API as `comms_api` gives
it: an act posted twice with one act id runs once while the process lives, and the
round's log guards it after a restart.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from moxi import aio, modules

from . import board, builds, checking, corrections, export, folders, index, listing, log
from . import open_builds, pageacts, paths, questions, showing
from . import settings as own
from . import state as states
from .errors import RoundsError, UnfencedTest

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"rounds' page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

#: Seconds between the poller's ticks.
PERIOD_S = 5.0
#: Seconds between git passes: a part fed by git lags git by at most this.
GIT_PASS_S = 60.0
TOPIC = "rounds"
#: The longest free text a value carries; longer text is cut, ending in `…`.
TEXT_MAX_CHARS = 2000
#: The longest passage of a question's design a record carries.
PASSAGE_MAX_CHARS = 4000

_logger = logging.getLogger("moxi.rounds.page")

_logged: set = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        _logger.error("%s (%s)", what, type(err).__name__, exc_info=err)


# -- the token ---------------------------------------------------------------------------

Stat = tuple[int, int] | None


def _stat(path: Path) -> Stat:
    try:
        found = os.stat(path)
    except OSError:
        return None
    return (found.st_size, found.st_mtime_ns)


def _listed(rounds_dir: Path) -> tuple[str, ...]:
    try:
        return tuple(sorted(os.listdir(rounds_dir)))
    except OSError:
        return ()


@dataclass(frozen=True)
class Token:
    """What the board was made from, read without reading a log through: `stats`, by
    path, of every file the gather reads; `listings`, each rounds folder's entries;
    `statuses`, each round's status by its name folded to one case; `config`, the config
    file's path. `settings`, `homes` and `unplaced` ride along and are not compared."""

    stats: tuple[tuple[str, Stat], ...]
    listings: tuple[tuple[str, tuple[str, ...]], ...]
    statuses: tuple[tuple[str, str | None], ...]
    config: str
    settings: Any = field(default=None, compare=False)
    homes: tuple = field(default=(), compare=False)
    unplaced: tuple = field(default=(), compare=False)

    def stat(self, path: Path | str) -> Stat:
        return dict(self.stats).get(str(path))

    def key(self, home: folders.Home, slug: str) -> tuple:
        """What a round's part is kept under."""
        round_dir = home.round_dir(slug)
        return (self.stat(log.round_log(round_dir)),
                self.stat(log.project_log(home.rounds_dir)),
                self.stat(round_dir / "design.md"), self.stat(self.config), self.statuses)

    def on_closed(self) -> Callable[[str], bool]:
        """Whether a round, by name, is closed, as the statuses say."""
        statuses = dict(self.statuses)
        return lambda name: statuses.get(str(name).casefold()) == states.CLOSED


class _Memo:
    """What taking the token reads, kept under the stat it was read at."""

    def __init__(self):
        self.lock = threading.Lock()
        self.settings: tuple[Stat, Any] | None = None
        self.statuses: dict[str, tuple[Stat, str | None]] = {}


def take_token(memo: _Memo) -> Token:
    """The token as the files stand now. Raises what finding the rounds folders
    raises."""
    config = paths.LAYOUT.config_file()
    with memo.lock:
        config_stat = _stat(config)
        if memo.settings is None or memo.settings[0] != config_stat:
            memo.settings = (config_stat, own.load())
        settings = memo.settings[1]
        homes, unresolved = folders.homes(settings)
        stats: list[tuple[str, Stat]] = [(str(config), config_stat)]
        listings, statuses = [], []
        for home in homes:
            listings.append((str(home.rounds_dir), _listed(home.rounds_dir)))
            stats.append((str(log.project_log(home.rounds_dir)),
                          _stat(log.project_log(home.rounds_dir))))
            for slug in folders.slugs(home.rounds_dir):
                round_dir = home.round_dir(slug)
                own_log = log.round_log(round_dir)
                stat = _stat(own_log)
                stats += [(str(own_log), stat),
                          (str(round_dir / "design.md"), _stat(round_dir / "design.md"))]
                name = f"{home.project}/{slug}"
                kept = memo.statuses.get(str(own_log))
                if kept is None or kept[0] != stat:
                    kept = (stat, _status(home, slug))
                    memo.statuses[str(own_log)] = kept
                statuses.append((name.casefold(), kept[1]))
    return Token(tuple(stats), tuple(listings), tuple(sorted(statuses)), str(config),
                 settings=settings, homes=tuple(homes), unplaced=tuple(unresolved))


def _status(home: folders.Home, slug: str) -> str | None:
    try:
        return index.read(home.project, home.rounds_dir, slug).status
    except RoundsError:
        return None


# -- the shared state --------------------------------------------------------------------

@dataclass(frozen=True)
class Kept:
    """One round's part, the key it was made under, and whether a git pass makes it
    again."""

    key: tuple
    part: board.Part
    reads_git: bool = False


@dataclass(frozen=True)
class Held:
    """What the records answer from, replaced whole by each gather: the open rounds'
    parts and the closed ones the strip read, by name; each open build's git state as
    the gather read it, by round and then stage; what waits on the operator as the value
    has it; the round folders, by name; and the settings."""

    parts: Mapping[str, board.Part] = field(default_factory=dict)
    reading: Mapping[str, Mapping[str, open_builds.OpenBuild]] = field(
        default_factory=dict)
    waiting: Mapping = field(default_factory=dict)
    round_dirs: Mapping[str, Path] = field(default_factory=dict)
    settings: Any = None
    rev: Mapping[str, str] = field(default_factory=dict)


class Board:
    """The board's one shared place: the lock every gather runs under, the last token,
    value and git pass, the cached parts, and what the records answer from."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self.lock = threading.Lock()
        self.clock = clock
        self.memo = _Memo()
        self.token: Token | None = None
        self.value: dict | None = None
        self.git_at: float | None = None
        self.parts: dict[str, Kept] = {}
        self.closed: dict[str, Kept] = {}
        self.held = Held()
        #: How many gathers have run: a poller that published an older one asks again.
        self.gathers = 0

    def _git_due(self) -> bool:
        return self.git_at is None or self.clock() - self.git_at >= GIT_PASS_S

    def current(self) -> dict:
        """The board's value: the last one, when the token is as it was and the git
        pass is not due; else a gather's. Never raises: a failure of the reads every
        row needs is a value in `state` `error`."""
        with self.lock:
            try:
                token = take_token(self.memo)
            except UnfencedTest:
                raise
            except Exception as exc:  # noqa: BLE001 - the page shows it whole
                _log_once(exc, "finding the rounds folders failed")
                return self._failed(exc)
            due = self._git_due()
            if self.value is not None and token == self.token and not due:
                return self.value
            self.value = self._gather(token, git_pass=due)
            self.token = token
            if due:
                self.git_at = self.clock()
            self.gathers += 1
            return self.value

    def due(self) -> bool:
        """Whether a gather is due: the token changed or the git pass is due. False
        while a gather runs, since that gather is the one the asker would get."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            if self.value is None or self._git_due():
                return True
            return take_token(self.memo) != self.token
        finally:
            self.lock.release()

    def _failed(self, exc: BaseException) -> dict:
        value = _empty(log.stamp())
        value.update(state="error", detail=_detail(exc))
        self.value, self.token = value, None
        self.gathers += 1
        return value

    # -- one gather ----------------------------------------------------------------------

    def _gather(self, token: Token, *, git_pass: bool) -> dict:
        now = log.stamp()
        settings = token.settings
        value = _empty(now)
        try:
            reading = open_builds.read(settings=settings, now=now)
            given = listing.projects(settings=settings, now=now, refresh=False)
        except UnfencedTest:
            raise
        except Exception as exc:  # noqa: BLE001 - the page shows it whole
            _log_once(exc, "reading the open builds or the projects failed")
            value.update(state="error", detail=_detail(exc))
            return value
        on_closed = token.on_closed()
        by_round: dict[str, dict[str, open_builds.OpenBuild]] = {}
        for one in reading.builds:
            by_round.setdefault(one.round, {})[one.build.stage] = one
        unreadable = [{"name": error.project, "reason": error.reason, "fix": error.fix}
                      for error in token.unplaced]
        parts: dict[str, Kept] = {}
        round_dirs: dict[str, Path] = {}
        closed_summaries = []
        for project in given.found:
            if project.unreadable is not None:
                unreadable.append(_row(project.home.project, project.unreadable))
            for summary in project.rounds:
                round_dirs[summary.name] = summary.log_dir
                if summary.unreadable is not None:
                    unreadable.append(_row(summary.name, summary.unreadable))
                    continue
                if summary.status != states.OPEN:
                    closed_summaries.append((project.home, summary))
                    continue
                key = token.key(project.home, summary.slug)
                kept = self.parts.get(summary.name)
                step = next((item.body for item in summary.of(index.STEP)), {})
                if (kept is None or kept.key != key
                        or (git_pass and (kept.reads_git or step.get("git")))):
                    try:
                        kept = Kept(key, _make(summary.name, settings, on_closed,
                                               read=by_round.get(summary.name, {})))
                    except UnfencedTest:
                        raise
                    except Exception as exc:  # noqa: BLE001 - one round's, shown on its row
                        unreadable.append(_row(summary.name, exc))
                        continue
                    kept = dataclasses.replace(kept, reads_git=bool(
                        step.get("git") or _open_builds(kept.part)))
                parts[summary.name] = kept
        open_parts = [kept.part for kept in parts.values()]
        closed = self._strip_closed(token, closed_summaries, open_parts, on_closed)
        waited = listing.waiting(given=given, now=now, read_git=False)
        waiting = _waiting(waited)
        listed = listing.rounds(given=given, now=now, read=reading,
                                seen={name: kept.part.seen for name, kept in parts.items()},
                                on_closed=on_closed)
        counts = {one.name: dict(one.counts) for one in listed.rounds
                  if one.unreadable is None}
        columns = board.columns([one.state.steps for one in open_parts])
        rounds = []
        for name, kept in parts.items():
            try:
                rounds.append(board.round_value(
                    kept.part, columns, board.items_of(waiting, name),
                    step=board.step_of(kept.part), counts=counts.get(name, {})))
            except UnfencedTest:
                raise
            except Exception as exc:  # noqa: BLE001 - one round's, shown on its row
                unreadable.append(_row(name, exc))
        ended = listing.rounds(given=given, now=now, ended=True)
        value.update(
            columns=columns, waiting=waiting,
            recent=board.strip([*open_parts, *(kept.part for kept in closed.values())]),
            rounds=board.ordered(rounds), closed=_closed(ended), unreadable=unreadable)
        self.parts = parts
        self.closed = closed
        self.held = Held(
            parts={**{name: kept.part for name, kept in closed.items()},
                   **{name: kept.part for name, kept in parts.items()}},
            reading=by_round, waiting=waiting, round_dirs=round_dirs, settings=settings,
            rev={one["name"]: one["rev"] for one in rounds})
        return cut(value)

    def _strip_closed(self, token: Token, summaries, open_parts, on_closed) -> dict:
        """The closed rounds the strip reads: newest log first, each while its log was
        written at or after the oldest act of the strip found so far."""
        made: dict[str, Kept] = {}
        found = list(open_parts)
        newest = sorted(summaries, key=lambda pair: (
            token.stat(log.round_log(pair[1].log_dir)) or (0, 0))[1], reverse=True)
        for home, summary in newest:
            tenth = board.tenth_act(found)
            stat = token.stat(log.round_log(summary.log_dir))
            if stat is None or (tenth is not None and _when(stat) < tenth):
                break
            key = (stat, token.stat(token.config))
            kept = self.closed.get(summary.name)
            if kept is None or kept.key != key:
                try:
                    kept = Kept(key, _make(summary.name, token.settings, on_closed, read={}))
                except UnfencedTest:
                    raise
                except Exception as exc:  # noqa: BLE001 - the strip goes on without it
                    _log_once(exc, "reading a closed round for the strip failed")
                    continue
            made[summary.name] = kept
            found.append(kept.part)
        return made

    # -- the records ---------------------------------------------------------------------

    def record(self, what: str, name: str, *, column: str | None = None,
               stage: str | None = None, item_id: str | None = None) -> dict | None:
        """The record of one marker, item or card, from the last gather's parts; None
        for a round the last gather held no part of. Never gathers."""
        held = self.held
        if what == board.QUESTION:
            return self._question(name, item_id)
        if what == board.RULING:
            return self._ruling(name, item_id)
        if what in (board.DECISION, board.WAIT, board.SHOWN):
            entry = _item(held.waiting, what, name, item_id)
            if entry is None:
                return None
            round_dir = held.round_dirs.get(name)
            return cut(board.item_record(what, entry, round_dir=round_dir,
                                         max_chars=PASSAGE_MAX_CHARS))
        part_ = held.parts.get(name)
        if what == board.CARD:
            if part_ is None:
                part_ = _closed_card(name, held.settings)
                if part_ is None:
                    return None
            items = board.items_of(held.waiting, name)
            return cut({**board.card(part_, items), "rev": held.rev.get(name)})
        if part_ is None:
            return None
        items = board.items_of(held.waiting, name)
        made = board.record(part_, column, stage, items, now=held.reading.get(name),
                            settings=held.settings)
        return cut({**made, "rev": held.rev.get(name)})


    def _question(self, name: str, item_id: str | None) -> dict | None:
        """A question's record in whatever state it reads now: the entry as what waits
        on the operator has it, or for one that no longer waits as its log gives it,
        with the ruling that put it there. None when neither holds it."""
        held = self.held
        entry = _item(held.waiting, board.QUESTION, name, item_id)
        found = _question_of(held, name, item_id)
        if entry is None and found is None:
            return None
        if entry is None:
            question, logged, _register, on_closed = found
            entry = board.question_entry(name, question, logged, on_closed=on_closed)
        made = board.item_record(board.QUESTION, entry, round_dir=held.round_dirs.get(name),
                                 max_chars=PASSAGE_MAX_CHARS)
        made["ruling"] = None if found is None else board.ruling_of(*found[:3])
        return cut(made)

    def _ruling(self, name: str, item_id: str | None) -> dict | None:
        """A ruling's record from its log, whatever its shown state, with the newest
        ruling that replaces it. None when the log isn't held or has no such ruling."""
        found = _log_of(self.held, name) if item_id else None
        if found is None:
            return None
        logged, register, _on_closed = found
        ruling = corrections.find(logged, item_id)
        if ruling is None:
            return None
        return cut(board.ruling_record(name, ruling, logged, register))


def _log_of(held: Held, name: str):
    """The log of the round or project `name` as it reads now: `(the log's state, the
    project's state, on_closed)`, or None. A round's comes from its part; a project's
    from the project's log as the part of one of its open rounds holds it, else read
    now. A closed round's part is kept while its own log is unchanged, so the project's
    log it holds may be older than the gather."""
    part_ = held.parts.get(name)
    if part_ is not None:
        return part_.state, part_.found.register, part_.found.on_closed
    if "/" in name:
        return None
    register = next((one.found.register for one in held.parts.values()
                     if one.found.project.casefold() == name.casefold()
                     and one.state.ended is None and one.found.register is not None),
                    None)
    if register is None:
        register = _register(name, held.settings)
    return None if register is None else (register, register, None)


def _question_of(held: Held, name: str, item_id: str | None):
    """The question `item_id` of the round or project `name` as its log gives it:
    `(question, the log's state, the project's state, on_closed)`, or None."""
    found = _log_of(held, name) if item_id else None
    if found is None:
        return None
    logged, register, on_closed = found
    question = questions.find(logged, item_id)
    return None if question is None else (question, logged, register, on_closed)


def _register(project: str, settings) -> states.State | None:
    """The project's own log as it reads now, or None when it can't be read."""
    try:
        home = folders.home(project, settings)
        return states.fold(log.read(log.project_log(home.rounds_dir)))
    except (RoundsError, OSError, ValueError):
        return None


def _make(name: str, settings, on_closed, *, read: Mapping) -> board.Part:
    """A round's part: its show from its logs alone, what git says of its open builds
    with `read` the gather's reading of them, its steps read once and its groups."""
    found = showing.show(name, settings=settings, refresh=False)
    if found.unreadable is not None:
        raise found.unreadable
    found = dataclasses.replace(found, on_closed=on_closed)
    seen = checking.seen_of(found.state, found.name, settings=settings, read=read)
    return board.part(found, seen, models=settings["models"])


def _open_builds(part_: board.Part) -> bool:
    return any(one.ended is None for one in builds.builds(part_.state))


_cards: dict[str, tuple[Stat, board.Part]] = {}
_cards_lock = threading.Lock()


def _closed_card(name: str, settings) -> board.Part | None:
    """A closed round's part for its card, read from its log alone and kept under the
    log's stat; None for a round that can't be found or read."""
    try:
        project, slug = folders.split(name)
        home = folders.home(project, settings)
    except RoundsError:
        return None
    stat = _stat(log.round_log(home.round_dir(slug)))
    with _cards_lock:
        kept = _cards.get(name)
        if kept is not None and kept[0] == stat:
            return kept[1]
    try:
        found = showing.show(name, settings=settings, refresh=False)
    except RoundsError:
        return None
    if found.unreadable is not None:
        return None
    made = board.part(found, {}, models=None if settings is None else settings["models"])
    with _cards_lock:
        _cards[name] = (stat, made)
    return made


# -- the value ---------------------------------------------------------------------------

def _empty(now: str) -> dict:
    return {"today": dt.date.today().isoformat(), "home": _home(), "read_at": now,
            "state": "up", "detail": None, "columns": [],
            "waiting": {"questions": [], "parked_questions": [], "decisions": [], "waits": [],
                        "not_shown": [], "parked": 0},
            "recent": [], "rounds": [], "closed": [], "unreadable": []}


def _home() -> str | None:
    try:
        return str(Path.home())
    except (RuntimeError, KeyError, OSError):
        return None


def _detail(exc: BaseException) -> str:
    if isinstance(exc, RoundsError):
        return exc.reason if exc.fix is None else f"{exc.reason}; {exc.fix}"
    return f"{type(exc).__name__}: {exc}"


def _row(name: str, exc: BaseException) -> dict:
    if isinstance(exc, RoundsError):
        return {"name": name, "reason": exc.reason, "fix": exc.fix}
    return {"name": name, "reason": f"{type(exc).__name__}: {exc}", "fix": None}


def _when(stat: tuple[int, int]) -> str:
    """A file's last write as an event's `at`."""
    return log.stamp(stat[1] / 1e9)


def _entry(data: Mapping) -> dict:
    """A question or decision as what waits on the operator gives it, with `blocks` a
    list and `blocks_word` the word it may be instead, and `prepared` None where no
    answer was prepared."""
    made = {key: value for key, value in data.items() if key != "held"}
    made["prepared"] = data.get("prepared")
    blocks = data.get("blocks")
    made["blocks"] = list(blocks) if isinstance(blocks, (list, tuple)) else []
    made["blocks_word"] = blocks if isinstance(blocks, str) else None
    return made


def _waiting(waited: listing.Waiting) -> dict:
    """What waits on the operator, as the board carries it: the operator's open
    questions, the majors not ruled, the operator's jobs (not the fixes the lead owes),
    the rulings not shown, and how many questions are parked with each of them. Parked
    questions are not put to the operator: they are listed apart."""
    found = dict(waited.found)
    return {"questions": [_entry(one.data()) for one in found["open"]],
            "parked_questions": [_entry(one.data()) for one in found["parked"]],
            "decisions": [_entry(one.data()) for one in found["decisions"]],
            "waits": [one.data() for one in found["waits"] if board.is_job(one.id)],
            "not_shown": [{"project": back.project, **export.read_back_data(back)}
                          for back in found["not_shown"]],
            "parked": len(found["parked"])}


def _closed(ended: listing.Rounds) -> list[dict]:
    made = [{"name": one.name, "project": one.project, "flow": one.flow,
             "ended_at": (one.ended or {}).get("at"), "how": one.status}
            for one in ended.rounds if one.unreadable is None]
    return sorted(made, key=lambda one: one["ended_at"] or "", reverse=True)


def _item(waiting: Mapping, what: str, name: str, item_id: str | None) -> dict | None:
    if what == board.SHOWN:
        return next(({**row, "project": back["project"]}
                     for back in waiting.get("not_shown", ())
                     for row in back.get("rows", ())
                     if row.get("holder") == name and row.get("id") == item_id), None)
    listed = {board.QUESTION: "questions", board.DECISION: "decisions",
              board.WAIT: "waits"}[what]
    holder = "round" if what == board.WAIT else "holder"
    return next((one for one in waiting.get(listed, ())
                 if one.get(holder) == name and one.get("id") == item_id), None)


def cut(value: Any, *, max_chars: int = TEXT_MAX_CHARS) -> Any:
    """`value` with every string longer than `max_chars` cut to it, ending in `…`; a
    `passage` keeps its own size."""
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars - 1] + "…"
    if isinstance(value, Mapping):
        return {key: one if key == "passage" else cut(one, max_chars=max_chars)
                for key, one in value.items()}
    if isinstance(value, (list, tuple)):
        return [cut(one, max_chars=max_chars) for one in value]
    return value


# -- the routes --------------------------------------------------------------------------

BOARD = Board()


def rounds(call: Any) -> dict:
    """`GET /api/rounds`: the board's value with the server's `now`."""
    return {"now": time.time(), **BOARD.current()}


def record(call: Any) -> dict:
    """`GET /api/record`: one marker's, item's or card's record, from the last gather;
    `gone` true for a round that is no longer open."""
    query = call.query
    what = query.get("what")
    if what not in board.WHATS:
        raise lb.BadRequest("what", "not a kind of record")
    name = query.get("round")
    if not name:
        raise lb.BadRequest("round", "missing")
    if what == board.STEP and not query.get("column"):
        raise lb.BadRequest("column", "missing")
    if what not in (board.STEP, board.CARD) and not query.get("id"):
        raise lb.BadRequest("id", "missing")
    found = BOARD.record(what, name, column=query.get("column"),
                         stage=query.get("stage") or None, item_id=query.get("id"))
    return {"gone": found is None, "record": found}


#: The receipts of this server's acts, by act id, kept for the life of the process.
RECEIPTS = pageacts.Receipts()


def comms_api() -> Any | None:
    """comms' API, None when comms is not installed."""
    return pageacts.comms_loaded()


def _drafts(body: Mapping) -> list[pageacts.Draft]:
    try:
        return pageacts.drafts_of(body)
    except ValueError as exc:
        raise lb.BadRequest(str(exc), "a draft's value can't be read") from None


async def answers(call: Any) -> dict:
    """`POST /api/answers`: the tray's drafts sent to `to` (`each`, or one agent's
    name): the page's kinds recorded, then one message per recipient; a receipt per
    draft, in the order posted."""
    drafts = _drafts(call.body)
    to = call.body.get("to") or pageacts.EACH
    comms = comms_api()
    return await aio.offload(lambda: pageacts.send(drafts, to, receipts=RECEIPTS,
                                                   comms=comms))


async def agents(call: Any) -> dict:
    """`GET /api/agents`: the names comms' roster gives as bound, for the To picker; an
    empty list with comms' reason when comms is not installed or can't be read."""
    comms = comms_api()
    return await aio.offload(lambda: pageacts.agents(comms))


async def drop_confirm(call: Any) -> dict:
    """`GET /api/drop`: what the drop's confirm says of the open build of `stage` of
    `round`, read now; writes nothing."""
    name, stage = call.query.get("round"), call.query.get("stage")
    if not name:
        raise lb.BadRequest("round", "missing")
    if not stage:
        raise lb.BadRequest("stage", "missing")
    return await aio.offload(lambda: pageacts.drop_facts(name, stage))


async def drop(call: Any) -> dict:
    """`POST /api/drop/build`: the operator's drop of the open build of `stage` of
    `round`, with the page pointer of `act`, then the round's lead told."""
    body = call.body
    act = body.get("act") or ""
    if not pageacts.ACT_ID.fullmatch(act):
        raise lb.BadRequest("act", "not an act id")
    comms = comms_api()
    return await aio.offload(lambda: pageacts.drop(body["round"], body["stage"], act,
                                                   receipts=RECEIPTS, comms=comms))


async def events(call: Any) -> Any:
    """`GET /api/events`: the stream over the `rounds` topic. The first call starts the
    poller."""
    return call.stream(_feed().topics)


class _Feed:
    """The topic, its Snapshot and the poller, on one event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, board_: Board):
        self.loop = loop
        self.board = board_
        self.topics = lb.Topics(TOPIC)
        self.snapshot = lb.Snapshot(self._value, self.topics, TOPIC)
        #: The gather count of the value the Snapshot last had.
        self.had: int | None = None
        self.task = loop.create_task(self._poll(), name="rounds board poller")

    def _value(self) -> dict:
        value = self.board.current()
        self.had = self.board.gathers
        return value

    async def tick(self) -> None:
        """One tick: ask the Snapshot for the value when the token changed, the git
        pass is due or a gather ran that it did not have."""
        if self.had != self.board.gathers or await aio.offload(self.board.due):
            await self.snapshot.on_changed()

    async def _poll(self) -> None:
        while True:
            try:
                await self.tick()
            except UnfencedTest:
                raise
            except Exception as exc:  # noqa: BLE001 - the poller outlives every failure
                _log_once(exc, "the rounds poller's tick failed")
            await asyncio.sleep(PERIOD_S)


_current: _Feed | None = None


def _feed() -> _Feed:
    # A page server runs one loop for its life; a loop of its own per in-process test
    # gets a fresh feed, since a task and its Snapshot belong to one loop.
    global _current
    loop = asyncio.get_running_loop()
    if _current is None or _current.loop is not loop:
        _current = _Feed(loop, BOARD)
    return _current


__all__ = ["PERIOD_S", "GIT_PASS_S", "TOPIC", "TEXT_MAX_CHARS", "PASSAGE_MAX_CHARS",
           "Token", "take_token", "Kept", "Held", "Board", "BOARD", "cut", "rounds",
           "record", "events"]
