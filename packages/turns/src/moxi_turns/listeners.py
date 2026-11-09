"""The listener group: modules registered with turns for session events, and the start list.

A module registers by declaring an entry point in the group `moxi.turns.listeners`,
named by the module, whose target is a stdlib-only module holding `LISTENERS`:

    [project.entry-points."moxi.turns.listeners"]
    comms = "moxi_comms.listeners"

    # moxi_comms/listeners.py
    LISTENERS = (
        (("start", "prompt"), "moxi_comms.context:answer"),
    )

Each row is `(events, target)` or `(events, target, options)`: the events it wants, a
function, `"package.module:function"`, called with one `SessionEvent`, and a table of
named options. The events:

- `start` adds start context, at each session start, resume, clear and compaction. It is
  asked only for a module on the start list (below), and returns text or None.
- `ready` is the same moment, observe only: every row naming it is called, on the list or
  not, and its answer is ignored. A module that only wants to know a session started
  needs no place on the list.
- `prompt` adds context at each prompt: the texts, joined in module-name order.
- `turn` (a main-loop turn ended) and `gone` (the endpoint is ending); answers ignored.
- `after-tool`: a tool call ran; the text answer is added to what the model reads after
  it.
- `usage`: a usage reading from the harness, as given; answers ignored.
- `codex_limits`: a Codex rate-limits reading, as given; answers ignored.

The name `tool` is kept for a later turns: a registration naming it is refused whole, so
no module ships one early.

The one option is `floor`, `{"floor": "package.module:function"}`: a function called with
a `SessionEvent` that returns a token count, below which the main loop's events skip the
row. An option this turns doesn't know is ignored and named in the doctor. A row grows
only by optional trailing items; this turns reads a longer row as its first three items
and says so in the doctor.

The start list is turns' config key `start.order`: module names and file paths, in
order. A module entry adds the answers of its `start` rows, joined in the order its
`LISTENERS` declares them; a module that is not installed, or has no `start` row, is
skipped. A file entry (one holding a path separator or starting with `~`) adds the file's
text, read at each start; a missing or unreadable file adds nothing and is logged once
per endpoint. The total is `start.limit_chars`, at most the harness's ceiling. Each entry on the
effective list gets an equal share; in list order, an answer shorter than what it holds
passes the rest on to the entries after it, and one longer is cut on a line boundary,
with a last line `[turns: <entry> cut at <n> characters]`. `start_plan` reads the list
and its shares as a session of a harness would get them, running nothing.

Asking: every function that names the event runs in its own thread; all start together
and are bounded together by one `listen.wait_ms`. An exception or a late answer is logged
once and counts as None, so a broken listener costs its own context and never the
session's turn. A function is imported in its thread at its first ask, so a slow import
costs that ask and not the endpoint's start.

After-tool rows (`AfterTool`) are asked another way, since a tool call is frequent and
calls run in parallel: each row runs on a lane of its own with at most `AFTER_WORKERS`
threads, and the rows asked for one call are bounded together by `after_tool.wait_ms`
from the ask's arrival. A call waits for a free thread inside that bound; a row is
skipped for the call when the bound passes first, or at once when every busy thread of
the row has passed its own call's bound (hung), since then none can free in time. A row
that overruns, raises or answers something other than text adds nothing, and each kind
of fault is logged once per endpoint. Texts are joined in module-name order.

Floors (`Floors`): each after-tool row's floor function is read at each `ready` and after
each call of its row's listener, on whatever event, bounded by `after_tool.wait_ms`. A
floor that raises, overruns or answers something other than an int reads 0, logged once
per endpoint. While one read of a row is still running inside its bound a second one
shares it; one running past its bound reads 0 and starts no other thread. A row with no
floor reads 0, which means "always call". A harness that gates the main loop's calls by
the floors is told each time a value changes.
"""

from __future__ import annotations

import concurrent.futures
import importlib
import itertools
import logging
import os
import queue
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from moxi import names

from .results import SessionEvent

log = logging.getLogger("moxi.turns.listeners")

#: The entry-point group.
GROUP = "moxi.turns.listeners"
#: The events a listener may name.
EVENTS = ("ready", "start", "prompt", "turn", "gone", "after-tool", "usage", "codex_limits")
#: Event names kept for a later turns; a registration naming one is refused.
RESERVED = ("tool",)
#: The options a row's third item may name.
OPTIONS = ("floor",)
#: The events whose answers turns passes back.
CONTEXT_EVENTS = ("start", "prompt")
#: How listener texts are joined into one added context.
JOIN = "\n\n"
#: The most threads one after-tool row runs on.
AFTER_WORKERS = 4
#: The after-tool event's name.
AFTER_TOOL = "after-tool"
#: The most start context each harness takes, in characters: Claude's under Claude
#: Code's 10,000-character preview, Codex's about its 2,500-token spill.
START_CEILINGS = {"claude": 9500, "codex": 9000, "opencode": 9500}
#: The ceiling for any other harness.
DEFAULT_CEILING = min(START_CEILINGS.values())

_TARGET = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*")


@dataclass
class Listener:
    """One row of one module's `LISTENERS`."""

    module: str
    events: frozenset[str]
    target: str
    #: The row's floor function, `"package.module:function"`, or None for no floor.
    floor: str | None = None
    _fn: Callable[[SessionEvent], object] | None = field(default=None, repr=False)
    _floor_fn: Callable[[SessionEvent], object] | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def function(self) -> Callable[[SessionEvent], object]:
        with self._lock:
            if self._fn is None:
                self._fn = _import(self.target)
            return self._fn

    def floor_function(self) -> Callable[[SessionEvent], object]:
        """The row's floor function; raises when the row has none or it does not
        import."""
        if self.floor is None:
            raise LookupError(f"the row {self.target!r} has no floor")
        with self._lock:
            if self._floor_fn is None:
                self._floor_fn = _import(self.floor)
            return self._floor_fn


def _import(target: str) -> Callable[[SessionEvent], object]:
    module_name, _, attr = target.partition(":")
    return getattr(importlib.import_module(module_name), attr)


@dataclass(frozen=True)
class Broken:
    """A registration that could not be read, and why (for the doctor)."""

    module: str
    reason: str


@dataclass(frozen=True)
class Notice:
    """A registration that loaded with something turns ignored, and what (for the
    doctor)."""

    module: str
    reason: str


@dataclass(frozen=True)
class Registry:
    """What the group held when it was loaded, and the start list it is asked with.

    `order` None is the start list's default, `start.order`'s in the schema; `limit` 0
    is the harness's ceiling.
    """

    listeners: tuple[Listener, ...] = ()
    broken: tuple[Broken, ...] = ()
    notices: tuple[Notice, ...] = ()
    order: tuple[str, ...] | None = None
    limit: int = 0
    #: The file entries already logged as unreadable: each is logged once per endpoint.
    _logged: set = field(default_factory=set, repr=False, compare=False)

    def naming(self, event: str) -> list[Listener]:
        return [ls for ls in self.listeners if event in ls.events]

    def start_order(self) -> tuple[str, ...]:
        return default_order() if self.order is None else self.order

    def start_rows(self, module: str) -> list[Listener]:
        """`module`'s `start` rows, in the order its `LISTENERS` declares them."""
        return [ls for ls in self.listeners if ls.module == module and "start" in ls.events]


def default_order() -> tuple[str, ...]:
    """The start list's default: the schema's default of `start.order`."""
    from . import config

    return tuple(config.schema().keys["start.order"].default)


def start_ceiling(harness: str) -> int:
    return START_CEILINGS.get(harness, DEFAULT_CEILING)


def load(entries: Iterable | None = None, *, order: Iterable[str] | None = None,
         limit: int | None = None, env=None) -> Registry:
    """Read the group from this environment (or from `entries`, each with `.name` and
    `.load()`, for tests), with the start list from turns' config unless `order` and
    `limit` are given. Never raises."""
    if entries is None:
        from moxi import modules

        entries = modules.extensions(GROUP)
    if order is None or limit is None:
        cfg_order, cfg_limit = _start_settings(env)
        order = cfg_order if order is None else order
        limit = cfg_limit if limit is None else limit
    by_name: dict[str, list] = {}
    for ep in entries:
        by_name.setdefault(ep.name, []).append(ep)
    listeners: list[Listener] = []
    broken: list[Broken] = []
    notices: list[Notice] = []
    for name in sorted(by_name):
        claims = by_name[name]
        if len(claims) > 1:
            broken.append(Broken(name, f"{len(claims)} distributions register listeners "
                                       f"named {name!r}; uninstall all but one"))
            continue
        if not names.is_module_name(name):
            broken.append(Broken(name, f"{name!r} is not a Moxi module name"))
            continue
        try:
            rows, said = _rows(claims[0].load())
        except Exception as exc:  # noqa: BLE001
            broken.append(Broken(name, f"its registration does not load: "
                                       f"{type(exc).__name__}: {exc}"))
            continue
        listeners.extend(Listener(name, events, target, floor)
                         for events, target, floor in rows)
        notices.extend(Notice(name, reason) for reason in said)
    for b in broken:
        log.warning("listeners of %s are off: %s", b.module, b.reason)
    return Registry(tuple(listeners), tuple(broken), tuple(notices),
                    None if order is None else tuple(order), int(limit or 0))


def _start_settings(env) -> tuple[tuple[str, ...] | None, int]:
    from moxi.config import ConfigError

    from . import config

    try:
        settings = config.load(env)
    except ConfigError as exc:
        log.warning("turns' config does not read (%s); the start list is the default", exc)
        return None, 0
    return tuple(settings["start.order"]), int(settings["start.limit_chars"])


def _rows(module) -> tuple[list[tuple[frozenset[str], str, str | None]], list[str]]:
    """The rows of a `LISTENERS` as `(events, target, floor)`, and what the doctor should
    say of what was ignored: options this turns doesn't know, and rows longer than
    three items."""
    rows = getattr(module, "LISTENERS", None)
    if not isinstance(rows, (tuple, list)):
        raise ValueError("it defines no LISTENERS tuple")
    out = []
    said: list[str] = []
    longer = 0
    for row in rows:
        if not (isinstance(row, (tuple, list)) and len(row) >= 2):
            raise ValueError(f"the row {row!r} is not (events, target)")
        if len(row) > 3:
            longer += 1
        events, target = row[0], row[1]
        floor, unknown_options = _options(row, row[2] if len(row) > 2 else None)
        said.extend(f"this turns doesn't know option {k!r} (row {target!r}); it is "
                    "ignored" for k in unknown_options)
        if isinstance(events, str):
            events = (events,)
        events = frozenset(events)
        reserved = sorted(e for e in events if e in RESERVED)
        if reserved:
            raise ValueError(f"the row {row!r} names {', '.join(reserved)}, kept for a "
                             "later turns; this one takes " + ", ".join(EVENTS))
        unknown = sorted(str(e) for e in events if e not in EVENTS)
        if not events or unknown:
            raise ValueError(f"the row {row!r} names events {unknown or '(none)'}; "
                             f"use {', '.join(EVENTS)}")
        if not isinstance(target, str) or not _TARGET.fullmatch(target):
            raise ValueError(f"the row {row!r} has target {target!r}; use "
                             '"package.module:function"')
        out.append((events, target, floor))
    if longer:
        said.append(f"{longer} of its rows carry more than (events, target, options); "
                    "this turns is older than the row and reads only its first three "
                    "items")
    return out, said


def _options(row, options) -> tuple[str | None, list[str]]:
    """A row's floor from its options table, and the option names this turns doesn't
    know. A table that isn't one, or a floor that isn't a target, refuses the row."""
    if options is None:
        return None, []
    if not isinstance(options, Mapping):
        raise ValueError(f"the row {row!r} has a third item that is not a table of "
                         "options; use {\"floor\": \"package.module:function\"}")
    floor = options.get("floor")
    if floor is not None and (not isinstance(floor, str) or not _TARGET.fullmatch(floor)):
        raise ValueError(f"the row {row!r} has floor {floor!r}; use "
                         '"package.module:function"')
    return floor, sorted(str(k) for k in options if k not in OPTIONS)


# -- the start list ----------------------------------------------------------------------

def is_file_entry(entry: str) -> bool:
    """A start-list entry naming a file: it holds a path separator or starts with `~`."""
    return entry.startswith("~") or "/" in entry or os.sep in entry \
        or bool(os.altsep and os.altsep in entry)


@dataclass(frozen=True)
class Entry:
    """One entry of the effective start list: a module with `start` rows, or a file."""

    entry: str
    rows: tuple[Listener, ...] = ()
    path: Path | None = None


@dataclass(frozen=True)
class Plan:
    """The start list as it stands: the entries asked, and those skipped with why."""

    entries: tuple[Entry, ...]
    skipped: tuple[tuple[str, str], ...]
    #: Modules with a `start` row that are not on the list.
    off_list: tuple[str, ...]


def plan(registry: Registry, *, present: Callable[[str], bool] | None = None) -> Plan:
    """The effective start list. A module entry is skipped when it has no `start` row
    (`present`, when given, tells "not installed" from "installed, with no start row");
    a file entry when it is not an absolute path to a readable file."""
    entries: list[Entry] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    broken = {b.module for b in registry.broken}
    for entry in registry.start_order():
        if entry in seen:
            skipped.append((entry, "listed twice; the first counts"))
            continue
        seen.add(entry)
        if is_file_entry(entry):
            path, why = _file(entry)
            if why is None:
                entries.append(Entry(entry, path=path))
            else:
                skipped.append((entry, why))
            continue
        rows = registry.start_rows(entry)
        if rows:
            entries.append(Entry(entry, rows=tuple(rows)))
        elif not names.is_module_name(entry):
            skipped.append((entry, "neither a module name nor a file path"))
        elif entry in broken:
            skipped.append((entry, "its listener registration does not load"))
        elif present is not None and not present(entry):
            skipped.append((entry, "not installed"))
        else:
            skipped.append((entry, "registers no start row"))
    listed = set(registry.start_order())
    off = sorted({ls.module for ls in registry.listeners if "start" in ls.events} - listed)
    return Plan(tuple(entries), tuple(skipped), tuple(off))


def start_total(registry: Registry, harness: str) -> int:
    """The start context's effective total for `harness`: `start.limit_chars`, at most the
    harness's ceiling, 0 meaning the ceiling."""
    total = start_ceiling(harness)
    return registry.limit if 0 < registry.limit < total else total


@dataclass(frozen=True)
class StartEntry:
    """One entry of a start plan.

    `kind` is `"file"` or `"listener"` (a module's `start` rows). `share` is what the entry
    holds when every entry before it fills its own: the total split equally as the start
    does it, the first entry taking the remainder and each later one giving up the
    two-character separator before it. It is a floor, not a promise: an earlier entry
    that answers short passes its room on. `size` is a file's present length in
    characters, read up to the total (a longer file reads as the total; one that cannot
    be read now as 0), and None for a listener entry, whose length is known only by
    running it.
    """

    name: str
    kind: str
    share: int
    size: int | None = None


@dataclass(frozen=True)
class StartPlan:
    """The start list as an endpoint of `harness` would order and limit it now.

    `total` is the effective total (`limit`, the configured `start.limit_chars` with 0 meaning
    the ceiling, at most `ceiling`, the harness's). `entries` are asked in order;
    `skipped` names each entry left out and each module whose `start` rows are off the
    list, with why.
    """

    harness: str
    total: int
    limit: int
    ceiling: int
    entries: tuple[StartEntry, ...]
    skipped: tuple[tuple[str, str], ...]


def start_plan(harness: str, env=None) -> StartPlan:
    """The start plan of a session of `harness` in the environment `env` (this process's
    when None): the start list from turns' config there and the listener group installed
    here, each entry's nominal share of the total, and a file entry's present size. A read
    only: it runs no listener and opens no endpoint. Raises `ValueError` for a harness
    turns does not know."""
    from moxi import modules

    if harness not in START_CEILINGS:
        raise ValueError(f"harness {harness!r} is not one of {', '.join(START_CEILINGS)}")
    registry = load(env=env)
    the_plan = plan(registry, present=modules.present)
    total = start_total(registry, harness)
    count = len(the_plan.entries)
    share = total // count if count else 0
    entries: list[StartEntry] = []
    for i, e in enumerate(the_plan.entries):
        held = share + (total - share * count) if i == 0 else max(0, share - len(JOIN))
        if e.path is None:
            entries.append(StartEntry(e.entry, "listener", held))
            continue
        try:
            size = min(len(_read_file(e.path, total)), total)
        except (OSError, UnicodeError):
            size = 0
        entries.append(StartEntry(e.entry, "file", held, size))
    skipped = the_plan.skipped + tuple(
        (module, "registers a start row but is not on start.order")
        for module in the_plan.off_list)
    return StartPlan(harness, total, registry.limit, start_ceiling(harness), tuple(entries),
                     skipped)


def _file(entry: str) -> tuple[Path | None, str | None]:
    path = Path(entry).expanduser()
    if not path.is_absolute():
        return None, "not an absolute path; write it from / or ~"
    try:
        if not path.is_file():
            return None, "does not exist" if not path.exists() else "is not a file"
        if not os.access(path, os.R_OK):
            return None, "cannot be read"
    except OSError as exc:
        return None, f"cannot be read: {exc}"
    return path, None


def _read_file(path: Path, most: int) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read(max(0, most) + 1)


def cut(text: str, holds: int, entry: str) -> str:
    """`text` as it fits in `holds` characters: whole, or cut on a line boundary where one
    exists, with the last line `[turns: <entry> cut at <n> characters]` (counted in)."""
    if len(text) <= holds:
        return text
    marker = "[turns: {entry} cut at {n} characters]"
    room = holds - 1 - len(marker.format(entry=entry, n=holds))
    if room <= 0:
        log.warning("start context of %s dropped: its %d characters left no room for "
                    "the cut line", entry, holds)
        return ""
    kept = text[:room]
    newline = kept.rfind("\n")
    if newline > 0:
        kept = kept[:newline]
    log.warning("start context of %s cut at %d characters (it had %d, it held %d)",
                entry, len(kept), len(text), holds)
    return f"{kept}\n{marker.format(entry=entry, n=len(kept))}"


def share_out(answers: list[tuple[str, str | None]], total: int) -> str | None:
    """Join the entries' answers, in list order, within `total` characters. Each entry
    holds an equal share plus what the entries before it left; the separator between two
    answers comes out of the later one's."""
    if not answers:
        return None
    share = total // len(answers)
    carry = total - share * len(answers)
    parts: list[str] = []
    for entry, text in answers:
        holds = share + carry
        if not text:
            carry = holds
            continue
        if parts:
            holds -= len(JOIN)
        kept = cut(text, max(0, holds), entry)
        carry = max(0, holds - len(kept))
        if kept:
            parts.append(kept)
    return JOIN.join(parts) if parts else None


# -- asking ------------------------------------------------------------------------------

#: Called after each listener call, in its thread: `(listener, event) -> None`.
After = Callable[[Listener, SessionEvent], None]


def ask(registry: Registry, event: SessionEvent, wait_s: float, *,
        wait: bool = False, after: After | None = None) -> str | None:
    """Ask every listener that names `event.event`, bounded together by `wait_s`.

    A `ready` or `start` event is the start moment: every `ready` row is told, and the
    start list's entries are asked; the result is the start context, or None. For
    `prompt`, the texts joined in module-name order, or None. For `turn` and `gone` the
    answers are ignored and this returns None at once, the calls running on, unless
    `wait` asks it to wait for them up to `wait_s` too (the endpoint's own end, so a
    `gone` call is not cut off by the process exiting). `after`, when given, is called in
    each listener's thread once its call returned (the endpoint reads floors there).
    """
    if event.event in ("ready", "start"):
        return _start(registry, event, wait_s, after)
    chosen = registry.naming(event.event)
    if not chosen:
        return None
    answers, done, threads = _launch(chosen, event, after)
    context = event.event in CONTEXT_EVENTS
    if not (context or wait):
        return None
    _join(threads, wait_s)
    if not context:
        return None
    texts = [t for t in (_text(ls, event, answers[i], done[i], wait_s)
                         for i, ls in enumerate(chosen)) if t]
    return JOIN.join(texts) if texts else None


def _start(registry: Registry, event: SessionEvent, wait_s: float,
           after: After | None = None) -> str | None:
    ready = registry.naming("ready")
    if ready:
        _launch(ready, replace(event, event="ready"), after)
    the_plan = plan(registry)
    for entry, why in the_plan.skipped:
        if is_file_entry(entry) and entry not in registry._logged:
            registry._logged.add(entry)
            log.warning("start list entry %s adds nothing: %s", entry, why)
    if not the_plan.entries:
        return None
    start = replace(event, event="start")
    rows = [ls for e in the_plan.entries for ls in e.rows]
    answers, done, threads = _launch(rows, start, after) if rows else ([], [], [])
    total = start_total(registry, event.harness)
    files: dict[str, str | None] = {}
    for e in the_plan.entries:
        if e.path is not None:
            try:
                files[e.entry] = _read_file(e.path, total)
            except (OSError, UnicodeError) as exc:
                files[e.entry] = None
                if e.entry not in registry._logged:
                    registry._logged.add(e.entry)
                    log.warning("start list entry %s adds nothing: it cannot be read: %s",
                                e.entry, exc)
    _join(threads, wait_s)
    texts = {id(ls): _text(ls, start, answers[i], done[i], wait_s)
             for i, ls in enumerate(rows)}
    per_entry: list[tuple[str, str | None]] = []
    for e in the_plan.entries:
        if e.path is not None:
            per_entry.append((e.entry, files.get(e.entry)))
        else:
            joined = JOIN.join(t for t in (texts[id(ls)] for ls in e.rows) if t)
            per_entry.append((e.entry, joined or None))
    return share_out(per_entry, total)


def _launch(chosen: list[Listener], event: SessionEvent, after: After | None = None):
    answers: list[object] = [None] * len(chosen)
    done = [False] * len(chosen)
    # Each call gets its own copy of the environment, so no listener changes another's.
    threads = [threading.Thread(target=_call,
                                args=(ls, replace(event, env=dict(event.env)), answers,
                                      done, i, after),
                                name=f"turns-listener-{ls.module}", daemon=True)
               for i, ls in enumerate(chosen)]
    for t in threads:
        t.start()
    return answers, done, threads


def _join(threads: list[threading.Thread], wait_s: float) -> None:
    deadline = time.monotonic() + max(0.0, wait_s)
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))


def _text(listener: Listener, event: SessionEvent, answer: object, done: bool,
          wait_s: float) -> str | None:
    if not done:
        log.warning("listener %s (%s) did not answer %s within %d ms; it counts as "
                    "no answer", listener.module, listener.target, event.event,
                    int(wait_s * 1000))
        return None
    if answer is None:
        return None
    if not isinstance(answer, str):
        log.warning("listener %s (%s) answered %s with a %s, not text; it counts as "
                    "no answer", listener.module, listener.target, event.event,
                    type(answer).__name__)
        return None
    return answer or None


def _call(listener: Listener, event: SessionEvent, answers: list, done: list,
          i: int, after: After | None = None) -> None:
    try:
        answers[i] = listener.function()(event)
    except Exception as exc:  # noqa: BLE001 - a broken listener costs its own answer
        log.warning("listener %s (%s) raised at %s: %s: %s; it counts as no answer",
                    listener.module, listener.target, event.event, type(exc).__name__, exc)
        answers[i] = None
    done[i] = True
    _after(after, listener, event)


def _after(after: After | None, listener: Listener, event: SessionEvent) -> None:
    if after is None:
        return
    try:
        after(listener, event)
    except Exception:  # noqa: BLE001 - the hook's fault is never the listener's
        log.exception("after the call of listener %s (%s)", listener.module, listener.target)


# -- after-tool rows ----------------------------------------------------------------------

def after_rows(registry: Registry) -> dict[str, Listener]:
    """The rows naming `after-tool`, by id, in module-name order then each module's own.
    A row's id is its target; a second row with the same target gets `#2`, and so on."""
    rows: dict[str, Listener] = {}
    for ls in registry.naming(AFTER_TOOL):
        rid, n = ls.target, 1
        while rid in rows:
            n += 1
            rid = f"{ls.target}#{n}"
        rows[rid] = ls
    return rows


class _Lane:
    """One after-tool row's threads: at most `AFTER_WORKERS`, each a daemon pulling
    jobs. `running` holds each admitted job's bound until its call (and the row's floor
    read after it) returns. Every field is guarded by the asker's condition."""

    def __init__(self, row_id: str):
        self.id = row_id
        self.running: dict[int, float] = {}
        self.threads = 0
        self.idle = 0
        self.jobs: queue.SimpleQueue = queue.SimpleQueue()

    def free(self) -> bool:
        return len(self.running) < AFTER_WORKERS

    def hung(self, now: float) -> bool:
        return not self.free() and all(d <= now for d in self.running.values())

    def next_hang(self, now: float) -> float | None:
        later = [d for d in self.running.values() if d > now]
        return min(later) if later else None


class AfterTool:
    """One endpoint's after-tool rows, each on a lane of its own (the rule above).

    `after` is called in the row's thread once its listener returned, before the thread
    is free again; `clock` stands in for `time.monotonic` (tests)."""

    def __init__(self, rows: Mapping[str, Listener], wait_s: float, *,
                 after: After | None = None, clock: Callable[[], float] = time.monotonic):
        self.rows = dict(rows)
        self.wait_s = max(0.0, wait_s)
        self._after = after
        self._clock = clock
        self._cond = threading.Condition()
        self._lanes = {rid: _Lane(rid) for rid in self.rows}
        self._jobs = itertools.count(1)
        self._said: set[tuple[str, str]] = set()
        self._closed = False

    def ask(self, event: SessionEvent, ids: Iterable[str] | None = None) -> str | None:
        """Ask the rows `ids` names (every row when None) about one tool call, bounded
        together by `wait_s` from now. Returns their texts joined, or None. An id that
        names no row is skipped. Never raises."""
        t0 = self._clock()
        deadline = t0 + self.wait_s
        wanted = None if ids is None else set(i for i in ids if isinstance(i, str))
        chosen = [rid for rid in self.rows if wanted is None or rid in wanted]
        if not chosen:
            return None
        futures: dict[str, concurrent.futures.Future] = {}
        with self._cond:
            if self._closed:
                return None
            pending = list(chosen)
            while pending:
                now = self._clock()
                for rid in list(pending):
                    lane = self._lanes[rid]
                    if lane.free():
                        futures[rid] = self._start(lane, self.rows[rid], event, deadline)
                        pending.remove(rid)
                    elif deadline <= now or lane.hung(now):
                        self._say(rid, "busy", f"every thread of after-tool row {rid} was "
                                  "still busy with earlier calls; it was skipped for a call")
                        pending.remove(rid)
                if not pending:
                    break
                wake = [deadline]
                for rid in pending:
                    nxt = self._lanes[rid].next_hang(now)
                    if nxt is not None:
                        wake.append(nxt)
                self._cond.wait(max(0.0, min(wake) - now) + 0.001)
        texts: list[str] = []
        for rid in chosen:
            future = futures.get(rid)
            if future is None:
                continue
            try:
                text = future.result(timeout=max(0.0, deadline - self._clock()))
            except concurrent.futures.TimeoutError:
                self._say(rid, "late", f"after-tool row {rid} did not answer within "
                          f"{int(self.wait_s * 1000)} ms; it adds nothing")
                continue
            if text:
                texts.append(text)
        return JOIN.join(texts) if texts else None

    def _start(self, lane: _Lane, listener: Listener, event: SessionEvent,
               deadline: float) -> concurrent.futures.Future:
        """Under the condition: admit one job on `lane` and hand it to a thread."""
        job = next(self._jobs)
        lane.running[job] = deadline
        future: concurrent.futures.Future = concurrent.futures.Future()
        lane.jobs.put((job, listener, replace(event, env=dict(event.env)), future))
        if lane.idle == 0 and lane.threads < AFTER_WORKERS:
            lane.threads += 1
            lane.idle += 1
            threading.Thread(target=self._work, args=(lane,), daemon=True,
                             name=f"turns-after-tool-{listener.module}").start()
        lane.idle -= 1
        return future

    def _work(self, lane: _Lane) -> None:
        while True:
            item = lane.jobs.get()
            if item is None:
                return
            job, listener, event, future = item
            try:
                answer = listener.function()(event)
            except BaseException as exc:  # noqa: BLE001 - a row's fault adds nothing
                self._say(lane.id, "raise", f"after-tool row {lane.id} raised "
                          f"{type(exc).__name__}: {exc}; it adds nothing")
                answer = None
            if answer is not None and not isinstance(answer, str):
                self._say(lane.id, "type", f"after-tool row {lane.id} answered a "
                          f"{type(answer).__name__}, not text; it adds nothing")
                answer = None
            if not future.done():
                future.set_result(answer or None)
            _after(self._after, listener, event)
            with self._cond:
                lane.running.pop(job, None)
                lane.idle += 1
                self._cond.notify_all()

    def _say(self, rid: str, kind: str, text: str) -> None:
        with self._cond:
            if (rid, kind) in self._said:
                return
            self._said.add((rid, kind))
        log.warning("%s", text)

    def close(self) -> None:
        """Take no more asks; let idle threads go. A hung row's thread is a daemon."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            for lane in self._lanes.values():
                for _ in range(lane.threads):
                    lane.jobs.put(None)
            self._cond.notify_all()


class _Read:
    """One floor read in flight: its thread, its bound and, once done, its value."""

    def __init__(self, deadline: float):
        self.deadline = deadline
        self.done = threading.Event()
        self.value = 0


class Floors:
    """Each after-tool row's current floor, read as the rule above says. `send(values)`
    is told the whole map, `{row id: tokens}`, each time a value changes, in the order
    the changes were made."""

    def __init__(self, rows: Mapping[str, Listener], wait_s: float, *,
                 send: Callable[[dict[str, int]], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.rows = dict(rows)
        self.wait_s = max(0.0, wait_s)
        self._send = send
        self._clock = clock
        self._lock = threading.Lock()
        self._values = {rid: 0 for rid in self.rows}
        self._by_listener = {id(ls): rid for rid, ls in self.rows.items() if ls.floor}
        self._reads: dict[str, _Read] = {}
        self._said: set[tuple[str, str]] = set()

    @property
    def any(self) -> bool:
        """Some row has a floor function."""
        return bool(self._by_listener)

    def values(self) -> dict[str, int]:
        with self._lock:
            return dict(self._values)

    def after_call(self, listener: Listener, event: SessionEvent) -> None:
        """A row's listener was called: read its floor again."""
        rid = self._by_listener.get(id(listener))
        if rid is None:
            return
        read = self._begin(rid, event)
        self._set({rid: self._finish(rid, read)})

    def refresh(self, event: SessionEvent) -> None:
        """Read every floor again (a `ready`), together, under one bound."""
        reads = {rid: self._begin(rid, event) for rid in self._by_listener.values()}
        if reads:
            self._set({rid: self._finish(rid, read) for rid, read in reads.items()})

    def _begin(self, rid: str, event: SessionEvent) -> _Read | None:
        """The read of `rid` to wait for: the one already running inside its bound, else
        a new one; None when one runs past its bound (it reads 0)."""
        now = self._clock()
        with self._lock:
            running = self._reads.get(rid)
            if running is not None and not running.done.is_set():
                return running if running.deadline > now else None
            read = self._reads[rid] = _Read(now + self.wait_s)
        listener = self.rows[rid]
        threading.Thread(target=self._read, args=(rid, listener, replace(
            event, env=dict(event.env)), read), daemon=True,
            name=f"turns-floor-{listener.module}").start()
        return read

    def _read(self, rid: str, listener: Listener, event: SessionEvent, read: _Read) -> None:
        try:
            value = listener.floor_function()(event)
            if isinstance(value, bool) or not isinstance(value, int):
                self._say(rid, "type", f"answered a {type(value).__name__}, not a token count")
                value = 0
        except BaseException as exc:  # noqa: BLE001 - a floor's fault reads 0
            self._say(rid, "raise", f"raised {type(exc).__name__}: {exc}")
            value = 0
        read.value = max(0, value)
        read.done.set()

    def _finish(self, rid: str, read: _Read | None) -> int:
        if read is None:
            self._say(rid, "late", "is still running from an earlier read")
            return 0
        if not read.done.wait(max(0.0, read.deadline - self._clock())):
            self._say(rid, "late", f"took longer than {int(self.wait_s * 1000)} ms")
            return 0
        return read.value

    def _set(self, new: dict[str, int]) -> None:
        with self._lock:
            changed = any(self._values.get(rid) != v for rid, v in new.items())
            self._values.update(new)
            if changed and self._send is not None:
                try:
                    self._send(dict(self._values))
                except Exception:  # noqa: BLE001 - a send's fault costs the line
                    log.exception("the floors could not be sent")

    def _say(self, rid: str, kind: str, what: str) -> None:
        with self._lock:
            if (rid, kind) in self._said:
                return
            self._said.add((rid, kind))
        log.warning("the floor of after-tool row %s %s; it reads 0", rid, what)


__all__ = ["GROUP", "EVENTS", "RESERVED", "OPTIONS", "CONTEXT_EVENTS", "JOIN",
           "AFTER_WORKERS", "AFTER_TOOL", "START_CEILINGS", "DEFAULT_CEILING", "Listener",
           "Broken", "Notice", "Registry", "Entry", "Plan", "SessionEvent", "After",
           "default_order", "start_ceiling", "load", "plan", "is_file_entry", "cut",
           "share_out", "ask", "after_rows", "AfterTool", "Floors"]
