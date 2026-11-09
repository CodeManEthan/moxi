"""The endpoint's guard runner: the table a harness matches from, and the judges.

The endpoint (the Claude endpoint, or a Codex host) loads the guards that are on at its
start and at each `ready`, and builds one harness's **table** from them: per guard its id,
its patterns for that harness, `who`, `match`, `match_flags`, whether it has a judge, its
`deny`, `wants`, `on_error` and its deadline clamped to `guards.max_wait_ms`. A guard that
names no pattern for the harness is not in that harness's table. The Claude runner gets
the table over the leg and matches in its own process; a host matches here, with
`decide`.

**Matching.** A guard covers a call when one of its patterns matches the tool name (the
whole name; `*` matches any run of characters, every other character only itself), `who`
matches (`main`: no agent id, `agents`: an agent id, `any`: both; an empty id is the main
loop's), and every `match` regex finds its input field, which must be a string. The
decision for a call: no covering guard lets it run with no trip; else the first covering
data-only guard, in table order, refuses it with its `deny`; else the covering judge
guards are asked, in table order.

**Judging.** Each guard's judge runs on a lane of its own with at most `GUARD_WORKERS`
threads. A call's deadline starts when the ask arrives and covers the wait for a thread
and the judge together. A call that finds every thread busy waits for one; it is broken,
"still judging earlier calls", when its deadline passes first, and at once when every
busy thread's judge has passed its own deadline (hung), since then none can free in time.
A waiting call holds the thread that asked (the leg's request thread) until its deadline
at most, which is at most `guards.max_wait_ms`. A hung judge holds its thread until it
returns: a guard never holds more than `GUARD_WORKERS` threads, and lanes outlive a
reload, so a new table brings no new threads for a hung guard.

**Breaks.** A guard is broken for a call when its judge raised, overran the deadline,
answered something other than None, `Allow` or `Deny`, or its module did not load; or no
thread freed in time. A broken guard lets the call through with one line the model reads
after the tool, unless it declared `on_error = "deny"`, which refuses the call. Other
guards covering the call are unaffected. Every break leaves a doctor record keyed
`guard:<id>`, the newer replacing the older, and the guard's next clean judge clears it.
A guard on the list that does not load gets one line in the next `ready`'s start context,
once per reason.

Threads here are daemon threads, so a hung judge never holds the endpoint's exit.
"""

from __future__ import annotations

import concurrent.futures
import itertools
import logging
import queue
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from . import (MAX_WAIT_KEY, MAX_WAIT_MS, ON_KEY, Allow, Deny, Found, Loaded, Prompt,
               ToolCall, compile_match, judge_of)

log = logging.getLogger("moxi.turns.guards")

#: The most threads one guard's judge runs on.
GUARD_WORKERS = 4
#: The reason a call is broken when no thread of its guard freed in time.
STILL_JUDGING = "still judging earlier calls"
#: An offering module whose guards take longer than this to import is named in the log
#: (and by the doctor).
IMPORT_WARN_MS = 10.0
#: The doctor record's module and key prefix.
RECORD_MODULE = "turns"
RECORD_PREFIX = "guard:"
#: The problem kinds that mean a guard on the list did not load.
LOAD_KINDS = ("load", "shape", "missing")


def break_line(guard_id: str, why: str) -> str:
    """The line the model reads after a call a broken guard let through."""
    return f"[turns] guard {guard_id} could not check this call ({why}); it ran unchecked."


def break_deny(guard_id: str, why: str) -> str:
    """The refusal of a call whose broken guard declared `on_error = "deny"`."""
    return f"turns could not check this call with guard {guard_id} ({why})"


def load_line(guard_id: str, why: str) -> str:
    """The start-context line for a guard on the list that did not load."""
    return f"[turns] guard {guard_id} could not load ({why}); the calls it covers run unchecked."


def record_key(guard_id: str) -> str:
    return RECORD_PREFIX + guard_id


# -- the table ---------------------------------------------------------------------------

def glob_regex(pattern: str) -> re.Pattern:
    """A tool-name pattern as a regex over the whole name: `*` is any run of characters,
    every other character only itself."""
    return re.compile("".join(".*" if part == "*" else re.escape(part)
                              for part in re.split(r"(\*)", pattern) if part), re.DOTALL)


@dataclass(frozen=True)
class Entry:
    """One guard's row in a harness's table."""

    id: str
    #: Its tool-name patterns for this harness.
    calls: tuple[str, ...]
    who: str
    match: Mapping[str, str] | None
    match_flags: str
    #: Whether it has a judge; a guard with none refuses with `deny`.
    judge: bool
    deny: str
    wants: tuple[str, ...]
    on_error: str
    #: The judge's bound, clamped to `guards.max_wait_ms`.
    deadline_ms: int
    #: What turned it on: `list`, `switch` or both.
    by: tuple[str, ...] = ()
    found: Found | None = field(default=None, repr=False, compare=False)
    _names: tuple[re.Pattern, ...] = field(default=(), repr=False, compare=False)
    _match: Mapping[str, re.Pattern] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def of(cls, guard_id: str, data: Mapping[str, Any], *, found: Found | None = None,
           by: tuple[str, ...] = ()) -> "Entry":
        """An entry from its leg form (`to_leg`'s keys, `id` given apart). Raises
        `re.error` when a `match` regex does not compile."""
        calls = tuple(data.get("calls") or ())
        match = dict(data["match"]) if data.get("match") else None
        flags = data.get("match_flags") or ""
        rx = re.ASCII | (re.IGNORECASE if flags == "i" else 0)
        return cls(guard_id, calls, data.get("who") or "any", match, flags,
                   bool(data.get("judge")), data.get("deny") or "",
                   tuple(data.get("wants") or ()), data.get("on_error") or "allow",
                   int(data.get("deadline_ms") or 1), tuple(by), found,
                   tuple(glob_regex(p) for p in calls),
                   {k: re.compile(v, rx) for k, v in (match or {}).items()})

    def to_leg(self) -> dict[str, Any]:
        """The entry as the leg carries it: plain JSON."""
        return {"id": self.id, "calls": list(self.calls), "who": self.who,
                "match": dict(self.match) if self.match else None,
                "match_flags": self.match_flags, "judge": self.judge, "deny": self.deny,
                "wants": list(self.wants), "on_error": self.on_error,
                "deadline_ms": self.deadline_ms}

    def covers(self, tool: str, agent_id: str | None, input: Mapping[str, Any]) -> bool:
        """Whether this guard covers the call (the module's docstring has the rule)."""
        if not any(p.fullmatch(tool) for p in self._names):
            return False
        if self.who == "main" and agent_id:
            return False
        if self.who == "agents" and not agent_id:
            return False
        for name, rx in self._match.items():
            value = input.get(name) if isinstance(input, Mapping) else None
            if not isinstance(value, str) or rx.search(value) is None:
                return False
        return True


def entry_for(on, harness: str, max_wait_ms: int) -> Entry | None:
    """`on`'s row in `harness`'s table, or None when it names no pattern there. Raises
    `re.error` when a `match` regex does not compile."""
    g = on.guard
    patterns = g.patterns(harness)
    if not patterns:
        return None
    compile_match(g)                     # the loader checked; this raises if it lied
    return Entry.of(on.id, {
        "calls": patterns, "who": g.who, "match": g.match, "match_flags": g.match_flags,
        "judge": g.judge is not None, "deny": g.deny, "wants": g.wants,
        "on_error": g.on_error, "deadline_ms": g.deadline(max_wait_ms)},
        found=on.found, by=on.by)


@dataclass(frozen=True)
class Decision:
    """What a harness does with one call: `covering`, the ids of the guards that cover
    it in table order; `deny`, a data-only guard's refusal; `ask`, the judge guards to
    ask when nothing refused it. Neither: the call runs with no trip."""

    covering: tuple[str, ...] = ()
    deny: str | None = None
    ask: tuple[str, ...] = ()

    @property
    def verdict(self) -> str:
        return "deny" if self.deny is not None else "ask" if self.ask else "next"


def decide(entries: Iterable[Entry], tool: str, agent_id: str | None,
           input: Mapping[str, Any]) -> Decision:
    """The decision for one call against a table."""
    covering = [e for e in entries if e.covers(tool, agent_id, input)]
    ids = tuple(e.id for e in covering)
    for e in covering:
        if not e.judge:
            return Decision(ids, deny=e.deny)
    return Decision(ids, ask=ids)


# -- the answer --------------------------------------------------------------------------

@dataclass(frozen=True)
class Break:
    """One guard broken for one call: why, and whether its break refused the call."""

    id: str
    why: str
    denied: bool


@dataclass(frozen=True)
class Answer:
    """The answer to one ask. `deny` refuses the call; else `lines` (the judges' allow
    lines) and `broken` (one line per broken guard that let it through) go after the
    tool's result, in that order."""

    deny: str | None = None
    lines: tuple[str, ...] = ()
    broken: tuple[str, ...] = ()
    breaks: tuple[Break, ...] = ()

    def to_leg(self) -> dict[str, Any]:
        out: dict[str, Any] = {"lines": list(self.lines), "broken": list(self.broken)}
        if self.deny is not None:
            out["deny"] = self.deny
        return out


@dataclass(frozen=True)
class _Outcome:
    allow: str | None = None        # an Allow's line
    deny: str | None = None         # a Deny's reason
    why: str | None = None          # a break's reason
    clean: bool = True


def _judged(answer: object) -> _Outcome:
    if answer is None:
        return _Outcome()
    if isinstance(answer, Allow):
        if answer.line is None or (isinstance(answer.line, str) and answer.line):
            return _Outcome(allow=answer.line)
        return _Outcome(why=f"it answered Allow with a line that is {type(answer.line).__name__}, "
                            "not text", clean=False)
    if isinstance(answer, Deny):
        if isinstance(answer.reason, str) and answer.reason.strip():
            return _Outcome(deny=answer.reason)
        return _Outcome(why="it answered Deny with no reason", clean=False)
    return _Outcome(why=f"it answered {type(answer).__name__}, not None, Allow or Deny",
                    clean=False)


# -- lanes -------------------------------------------------------------------------------

class _Lane:
    """One guard's threads: at most `GUARD_WORKERS`, each a daemon pulling jobs. `running`
    holds each admitted job's deadline until its judge returns. Every field is guarded by
    the runner's condition."""

    def __init__(self, guard_id: str):
        self.id = guard_id
        self.running: dict[int, float] = {}
        self.threads = 0
        self.idle = 0
        self.jobs: queue.SimpleQueue = queue.SimpleQueue()

    def free(self) -> bool:
        return len(self.running) < GUARD_WORKERS

    def hung(self, now: float) -> bool:
        """Every thread is busy with a judge past its deadline."""
        return not self.free() and all(d <= now for d in self.running.values())

    def next_hang(self, now: float) -> float | None:
        later = [d for d in self.running.values() if d > now]
        return min(later) if later else None


# -- the runner --------------------------------------------------------------------------

#: `() -> (guards.run, guards.max_wait_ms)`.
Settings = Callable[[], "tuple[list[str], int]"]
#: `(text, key, fix) -> None` and `(key) -> None`: the doctor's record and clear.
Record = Callable[[str, str, "str | None"], None]
Clear = Callable[[str], None]


class GuardRunner:
    """One endpoint's guards for one harness: the table, the lanes, the records.

    `load(on)` stands in for the loader, `settings()` for turns' config, `record` and
    `clear` for the doctor's, and `clock` for `time.monotonic` (tests)."""

    def __init__(self, harness: str, *, env: Mapping[str, str] | None = None,
                 load: Callable[[list[str]], Loaded] | None = None,
                 settings: Settings | None = None, record: Record | None = None,
                 clear: Clear | None = None, clock: Callable[[], float] = time.monotonic):
        self.harness = harness
        self.env = dict(env) if env is not None else None
        self._load = load or self._default_load
        self._settings = settings or self._default_settings
        self._clock = clock
        self._cond = threading.Condition()
        self._entries: tuple[Entry, ...] = ()
        self._by_id: dict[str, Entry] = {}
        self._lanes: dict[str, _Lane] = {}
        #: Judges that did not import for this table, by id: their calls break at once.
        self._dead: dict[str, str] = {}
        #: Load lines said already, and those waiting for the next start context.
        self._said: set[tuple[str, str]] = set()
        self._notes: list[str] = []
        self._loaded: Loaded | None = None
        self._jobs = itertools.count(1)
        self._warm: threading.Thread | None = None
        self._closed = False
        #: Guards whose doctor record may stand: unknown at start (an earlier endpoint's
        #: record), so the first clean judge of each clears once.
        self._clean: set[str] = set()
        self._records = _Recorder(record or _doctor_record, clear or _doctor_clear)

    # -- the table ---------------------------------------------------------------------

    @property
    def entries(self) -> tuple[Entry, ...]:
        with self._cond:
            return self._entries

    @property
    def loaded(self) -> Loaded | None:
        return self._loaded

    def table(self) -> list[dict[str, Any]]:
        """The table in the leg's form."""
        return [e.to_leg() for e in self.entries]

    def reload(self) -> list[dict[str, Any]]:
        """Read `guards.run`, the switches and `guards.max_wait_ms` again, load what they
        name, and make the table. Returns it in the leg's form. Never raises."""
        try:
            on, max_wait = self._settings()
        except Exception as exc:  # noqa: BLE001 - a bad config costs the list, not the endpoint
            log.warning("guards: turns' config does not read (%s); no guard is on by the list",
                        exc)
            on, max_wait = [], MAX_WAIT_MS
        try:
            loaded = self._load(list(on))
        except Exception as exc:  # noqa: BLE001 - the loader never raises; be sure
            log.exception("guards: the loader failed")
            loaded = Loaded(problems=())
            self._note("guards", f"the loader failed: {type(exc).__name__}: {exc}")
        entries: list[Entry] = []
        for o in loaded.on:
            try:
                e = entry_for(o, self.harness, max_wait)
            except re.error as exc:
                self._note(o.id, f"its match does not compile: {exc}")
                continue
            if e is not None:
                entries.append(e)
        for p in loaded.problems:
            if p.on and p.kind in LOAD_KINDS:
                self._note(p.id, p.reason)
        for module, ms in loaded.import_ms.items():
            if ms > IMPORT_WARN_MS:
                log.warning("guards: module %s's guards took %.1f ms to import (over %.0f)",
                            module, ms, IMPORT_WARN_MS)
        with self._cond:
            self._loaded = loaded
            self._entries = tuple(entries)
            self._by_id = {e.id: e for e in entries}
            self._dead = {}
            for e in entries:
                self._lanes.setdefault(e.id, _Lane(e.id))
        self._warm_judges([e for e in entries if e.judge and e.found is not None])
        return [e.to_leg() for e in entries]

    def take_notes(self) -> list[str]:
        """The start-context lines not said yet: a guard on the list that did not load,
        once per reason. Each is returned once."""
        with self._cond:
            notes, self._notes = self._notes, []
        return notes

    def _note(self, guard_id: str, why: str) -> None:
        with self._cond:
            if (guard_id, why) in self._said:
                return
            self._said.add((guard_id, why))
            self._notes.append(load_line(guard_id, why))
        log.warning("guards: %s did not load: %s", guard_id, why)

    def _warm_judges(self, entries: list[Entry]) -> None:
        """Import the judges off the ask's path, so a first call's deadline is not spent
        on an import. One that does not import breaks every call until the next table,
        and is named in the next start context."""
        if not entries or (self._warm is not None and self._warm.is_alive()):
            return

        def warm() -> None:
            for e in entries:
                try:
                    judge_of(e.found)
                except Exception as exc:  # noqa: BLE001 - any import failure is the guard's
                    why = f"its module did not load: {type(exc).__name__}: {exc}"
                    with self._cond:
                        if self._by_id.get(e.id) is e:
                            self._dead[e.id] = why
                    self._note(e.id, why)

        self._warm = threading.Thread(target=warm, name="turns-guard-import", daemon=True)
        self._warm.start()

    # -- asking ------------------------------------------------------------------------

    def ask(self, call: ToolCall, ids: Iterable[str]) -> Answer:
        """Judge `call` by the guards `ids` names, each bounded by its deadline from now.
        An id not on the table (it changed at a `ready`) is skipped. Never raises."""
        t0 = self._clock()
        wanted = list(dict.fromkeys(i for i in ids if isinstance(i, str)))
        with self._cond:
            chosen = [e for e in self._entries if e.id in wanted]
            dead = dict(self._dead)
            closed = self._closed
        for i in wanted:
            if all(e.id != i for e in chosen):
                log.info("guards: asked about %s, which is not on the table; skipped", i)
        outcomes: dict[str, _Outcome] = {}
        futures: dict[str, tuple[concurrent.futures.Future, float]] = {}
        pending: dict[str, float] = {}
        for e in chosen:
            if not e.judge:
                outcomes[e.id] = _Outcome(deny=e.deny)
            elif closed:
                outcomes[e.id] = _Outcome(why="the endpoint is stopping", clean=False)
            elif e.id in dead:
                outcomes[e.id] = _Outcome(why=dead[e.id], clean=False)
            else:
                pending[e.id] = t0 + e.deadline_ms / 1000
        by_id = {e.id: e for e in chosen}
        if pending:
            with self._cond:
                while pending:
                    now = self._clock()
                    for gid in list(pending):
                        lane = self._lanes[gid]
                        if lane.free():
                            per = call if "prompts" in by_id[gid].wants else replace(call, prompts=())
                            futures[gid] = (self._start(lane, by_id[gid], per, pending[gid]),
                                            pending[gid])
                            del pending[gid]
                        elif pending[gid] <= now or lane.hung(now):
                            outcomes[gid] = _Outcome(why=STILL_JUDGING, clean=False)
                            del pending[gid]
                    if not pending:
                        break
                    wake = list(pending.values())
                    for gid in pending:
                        nxt = self._lanes[gid].next_hang(now)
                        if nxt is not None:
                            wake.append(nxt)
                    self._cond.wait(max(0.0, min(wake) - now) + 0.001)
        for gid, (future, deadline) in futures.items():
            try:
                outcomes[gid] = future.result(timeout=max(0.0, deadline - self._clock()))
            except concurrent.futures.TimeoutError:
                outcomes[gid] = _Outcome(
                    why=f"it took longer than {by_id[gid].deadline_ms} ms", clean=False)
        return self._answer(call, chosen, outcomes)

    def _start(self, lane: _Lane, e: Entry, call: ToolCall,
               deadline: float) -> concurrent.futures.Future:
        """Under the condition: admit one job on `lane` and hand it to a thread."""
        job = next(self._jobs)
        lane.running[job] = deadline
        future: concurrent.futures.Future = concurrent.futures.Future()
        lane.jobs.put((job, e, call, future))
        if lane.idle == 0 and lane.threads < GUARD_WORKERS:
            lane.threads += 1
            lane.idle += 1
            threading.Thread(target=self._work, args=(lane,), daemon=True,
                             name=f"turns-guard-{lane.id}").start()
        lane.idle -= 1
        return future

    def _work(self, lane: _Lane) -> None:
        while True:
            item = lane.jobs.get()
            if item is None:
                return
            job, e, call, future = item
            try:
                outcome = self._judge(e, call)
            except BaseException as exc:  # noqa: BLE001 - a judge's exit is its break
                outcome = _Outcome(why=f"it raised {type(exc).__name__}: {exc}", clean=False)
            with self._cond:
                lane.running.pop(job, None)
                lane.idle += 1
                self._cond.notify_all()
            if not future.done():
                future.set_result(outcome)

    def _judge(self, e: Entry, call: ToolCall) -> _Outcome:
        try:
            fn = judge_of(e.found)
        except Exception as exc:  # noqa: BLE001 - an import failure is the guard's break
            why = f"its module did not load: {type(exc).__name__}: {exc}"
            with self._cond:
                if self._by_id.get(e.id) is e:
                    self._dead[e.id] = why
            return _Outcome(why=why, clean=False)
        try:
            answer = fn(call)
        except Exception as exc:  # noqa: BLE001 - a judge that raises is broken
            return _Outcome(why=f"it raised {type(exc).__name__}: {exc}", clean=False)
        return _judged(answer)

    def _answer(self, call: ToolCall, chosen: list[Entry],
                outcomes: dict[str, _Outcome]) -> Answer:
        deny: str | None = None
        lines: list[str] = []
        broken: list[str] = []
        breaks: list[Break] = []
        for e in chosen:
            o = outcomes.get(e.id)
            if o is None:
                continue
            if o.why is not None:
                denied = e.on_error == "deny"
                breaks.append(Break(e.id, o.why, denied))
                if denied:
                    deny = deny if deny is not None else break_deny(e.id, o.why)
                else:
                    broken.append(break_line(e.id, o.why))
                self._broke(e, call, o.why, denied)
                continue
            if e.judge:
                self._judged_clean(e.id)
            if o.deny is not None and deny is None:
                deny = o.deny
            elif o.allow:
                lines.append(o.allow)
        return Answer(deny, tuple(lines), tuple(broken), tuple(breaks))

    # -- records -----------------------------------------------------------------------

    def _broke(self, e: Entry, call: ToolCall, why: str, denied: bool) -> None:
        what = "was refused" if denied else "ran unchecked"
        log.warning("guards: %s could not check a %s call (%s); it %s", e.id, call.tool, why,
                    what)
        with self._cond:
            self._clean.discard(e.id)
        self._records.put(("record", f"guard {e.id} could not check a {call.tool} call on "
                          f"{self.harness} ({why}); the call {what}", record_key(e.id),
                           _off_fix(e)))

    def _judged_clean(self, guard_id: str) -> None:
        with self._cond:
            if guard_id in self._clean:
                return
            self._clean.add(guard_id)
        self._records.put(("clear", record_key(guard_id)))

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until every record and clear asked so far is written (tests)."""
        return self._records.flush(timeout)

    def close(self) -> None:
        """Stop taking asks; let idle threads go. A hung judge's thread is a daemon and
        holds nothing past the process."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            lanes = list(self._lanes.values())
            for lane in lanes:
                for _ in range(lane.threads):
                    lane.jobs.put(None)
            self._cond.notify_all()
        self._records.flush(1.0)
        self._records.close()

    # -- defaults ------------------------------------------------------------------------

    def _default_settings(self) -> tuple[list[str], int]:
        from .. import config

        settings = config.load(self.env)
        return list(settings[ON_KEY]), int(settings[MAX_WAIT_KEY])

    def _default_load(self, on: list[str]) -> Loaded:
        from . import load

        return load(on, env=self.env)


def _off_fix(e: Entry) -> str | None:
    """The command that turns guard `e` off: off the list, else its switch off."""
    found = e.found
    if "list" in e.by or not e.by:
        entry = found.path if found is not None and found.path else e.id
        return f"moxi config set turns {ON_KEY} --remove {entry}"
    if found is not None and found.module and found.guard.switch:
        return f"moxi config set {found.module} {found.guard.switch} false"
    return None


def prompts_of(value: Any) -> tuple[Prompt, ...]:
    """A leg's prompt list (`[{origin, text}]`) as `Prompt`s; malformed entries are
    left out."""
    if not isinstance(value, list):
        return ()
    out = []
    for p in value:
        if isinstance(p, Mapping) and isinstance(p.get("text"), str):
            origin = p.get("origin")
            out.append(Prompt(origin if isinstance(origin, str) and origin else None, p["text"]))
    return tuple(out)


# -- the doctor's records, off the ask's path -------------------------------------------

def _doctor_record(text: str, key: str, fix: str | None) -> None:
    from moxi import doctor

    doctor.record(RECORD_MODULE, text, key=key, fix=fix)


def _doctor_clear(key: str) -> None:
    from moxi import doctor

    doctor.clear(RECORD_MODULE, key)


class _Recorder:
    """One daemon thread that writes the doctor's records in the order they were asked,
    so an ask never waits on a file."""

    def __init__(self, record: Record, clear: Clear):
        self._record, self._clear = record, clear
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def put(self, item: tuple) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name="turns-guard-records")
                self._thread.start()
        self._q.put(item)

    def flush(self, timeout: float) -> bool:
        with self._lock:
            if self._thread is None:
                return True
        done = threading.Event()
        self._q.put(("mark", done))
        return done.wait(timeout)

    def close(self) -> None:
        with self._lock:
            if self._thread is not None:
                self._q.put(None)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            try:
                if item[0] == "record":
                    self._record(item[1], item[2], item[3])
                elif item[0] == "clear":
                    self._clear(item[1])
                else:
                    item[1].set()
            except Exception:  # noqa: BLE001 - a record that can't be written is logged
                log.exception("guards: a doctor record could not be written")


__all__ = ["GUARD_WORKERS", "STILL_JUDGING", "IMPORT_WARN_MS", "RECORD_MODULE",
           "RECORD_PREFIX", "break_line", "break_deny", "load_line", "record_key",
           "glob_regex", "Entry", "entry_for", "Decision", "decide", "Break", "Answer",
           "GuardRunner", "prompts_of"]
