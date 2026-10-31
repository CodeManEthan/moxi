"""The listener group: modules registered with turns for session events, and the start list.

A module registers by declaring an entry point in the group `moxi.turns.listeners`,
named by the module, whose target is a stdlib-only module holding `LISTENERS`:

    [project.entry-points."moxi.turns.listeners"]
    comms = "moxi_comms.listeners"

    # moxi_comms/listeners.py
    LISTENERS = (
        (("start", "prompt"), "moxi_comms.context:answer"),
    )

Each row is `(events, target)`: the events it wants and a function,
`"package.module:function"`, called with one `SessionEvent`. The events:

- `start` adds start context, at each session start, resume, clear and compaction. It is
  asked only for a module on the start list (below), and returns text or None.
- `ready` is the same moment, observe only: every row naming it is called, on the list or
  not, and its answer is ignored. A module that only wants to know a session started
  needs no place on the list.
- `prompt` adds context at each prompt: the texts, joined in module-name order.
- `turn` (a main-loop turn ended) and `gone` (the endpoint is ending); answers ignored.

The names `tool`, `after-tool`, `usage` and `codex_limits` are kept for a later turns:
a registration naming one is refused whole, so no module ships one early. A row grows
only by optional trailing items; this turns reads a longer row as its first two items and
says so in the doctor.

The start list is turns' config key `start.order`: module names and file paths, in
order. A module entry adds the answers of its `start` rows, joined in the order its
`LISTENERS` declares them; a module that is not installed, or has no `start` row, is
skipped. A file entry (one holding a path separator or starting with `~`) adds the file's
text, read at each start; a missing or unreadable file adds nothing and is logged once
per endpoint. The total is `start.limit`, at most the harness's ceiling. Each entry on the
effective list gets an equal share; in list order, an answer shorter than what it holds
passes the rest on to the entries after it, and one longer is cut on a line boundary,
with a last line `[turns: <entry> cut at <n> characters]`.

Asking: every function that names the event runs in its own thread; all start together
and are bounded together by one `listen.wait_ms`. An exception or a late answer is logged
once and counts as None, so a broken listener costs its own context and never the
session's turn. A function is imported in its thread at its first ask, so a slow import
costs that ask and not the endpoint's start.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from moxi import names

from .results import SessionEvent

log = logging.getLogger("moxi.turns.listeners")

#: The entry-point group.
GROUP = "moxi.turns.listeners"
#: The events a listener may name.
EVENTS = ("ready", "start", "prompt", "turn", "gone")
#: Event names kept for a later turns; a registration naming one is refused.
RESERVED = ("tool", "after-tool", "usage", "codex_limits")
#: The events whose answers turns passes back.
CONTEXT_EVENTS = ("start", "prompt")
#: How listener texts are joined into one added context.
JOIN = "\n\n"
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
    _fn: Callable[[SessionEvent], object] | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def function(self) -> Callable[[SessionEvent], object]:
        with self._lock:
            if self._fn is None:
                module_name, _, attr = self.target.partition(":")
                self._fn = getattr(importlib.import_module(module_name), attr)
            return self._fn


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
            rows, longer = _rows(claims[0].load())
        except Exception as exc:  # noqa: BLE001
            broken.append(Broken(name, f"its registration does not load: "
                                       f"{type(exc).__name__}: {exc}"))
            continue
        listeners.extend(Listener(name, events, target) for events, target in rows)
        if longer:
            notices.append(Notice(name, f"{longer} of its rows carry more than (events, "
                                        "target); this turns is older than the row and "
                                        "reads only its first two items"))
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
    return tuple(settings["start.order"]), int(settings["start.limit"])


def _rows(module) -> tuple[list[tuple[frozenset[str], str]], int]:
    """The rows of a `LISTENERS`, and how many carried more than two items."""
    rows = getattr(module, "LISTENERS", None)
    if not isinstance(rows, (tuple, list)):
        raise ValueError("it defines no LISTENERS tuple")
    out = []
    longer = 0
    for row in rows:
        if not (isinstance(row, (tuple, list)) and len(row) >= 2):
            raise ValueError(f"the row {row!r} is not (events, target)")
        if len(row) > 2:
            longer += 1
        events, target = row[0], row[1]
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
        out.append((events, target))
    return out, longer


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

def ask(registry: Registry, event: SessionEvent, wait_s: float, *,
        wait: bool = False) -> str | None:
    """Ask every listener that names `event.event`, bounded together by `wait_s`.

    A `ready` or `start` event is the start moment: every `ready` row is told, and the
    start list's entries are asked; the result is the start context, or None. For
    `prompt`, the texts joined in module-name order, or None. For `turn` and `gone` the
    answers are ignored and this returns None at once, the calls running on, unless
    `wait` asks it to wait for them up to `wait_s` too (the endpoint's own end, so a
    `gone` call is not cut off by the process exiting).
    """
    if event.event in ("ready", "start"):
        return _start(registry, event, wait_s)
    chosen = registry.naming(event.event)
    if not chosen:
        return None
    answers, done, threads = _launch(chosen, event)
    context = event.event in CONTEXT_EVENTS
    if not (context or wait):
        return None
    _join(threads, wait_s)
    if not context:
        return None
    texts = [t for t in (_text(ls, event, answers[i], done[i], wait_s)
                         for i, ls in enumerate(chosen)) if t]
    return JOIN.join(texts) if texts else None


def _start(registry: Registry, event: SessionEvent, wait_s: float) -> str | None:
    ready = registry.naming("ready")
    if ready:
        _launch(ready, replace(event, event="ready"))
    the_plan = plan(registry)
    for entry, why in the_plan.skipped:
        if is_file_entry(entry) and entry not in registry._logged:
            registry._logged.add(entry)
            log.warning("start list entry %s adds nothing: %s", entry, why)
    if not the_plan.entries:
        return None
    start = replace(event, event="start")
    rows = [ls for e in the_plan.entries for ls in e.rows]
    answers, done, threads = _launch(rows, start) if rows else ([], [], [])
    total = start_ceiling(event.harness)
    if 0 < registry.limit < total:
        total = registry.limit
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


def _launch(chosen: list[Listener], event: SessionEvent):
    answers: list[object] = [None] * len(chosen)
    done = [False] * len(chosen)
    # Each call gets its own copy of the environment, so no listener changes another's.
    threads = [threading.Thread(target=_call,
                                args=(ls, replace(event, env=dict(event.env)), answers,
                                      done, i),
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
          i: int) -> None:
    try:
        answers[i] = listener.function()(event)
    except Exception as exc:  # noqa: BLE001 - a broken listener costs its own answer
        log.warning("listener %s (%s) raised at %s: %s: %s; it counts as no answer",
                    listener.module, listener.target, event.event, type(exc).__name__, exc)
        answers[i] = None
    done[i] = True


__all__ = ["GROUP", "EVENTS", "RESERVED", "CONTEXT_EVENTS", "JOIN", "START_CEILINGS",
           "DEFAULT_CEILING", "Listener", "Broken", "Notice", "Registry", "Entry", "Plan",
           "SessionEvent", "default_order", "start_ceiling", "load", "plan",
           "is_file_entry", "cut", "share_out", "ask"]
