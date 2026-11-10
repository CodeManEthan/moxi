"""The catalog: every main session of the three harnesses, newest change first, and the
details a list shows of one.

**The entries.** `Catalog.gather()` lists each harness's root without opening a session
(`sources(harness, heads=False)`) and keeps one `Entry` per main session:

* Claude Code: each main transcript, with the number of subagent files under its session.
* OpenCode: each session row with no parent, with the number of rows naming it as parent.
* Codex: each rollout that is not compressed and that no thread spawned, with the number
  of rollouts whose spawn link names its thread. A forked thread is a main session. The
  spawn link is on a rollout's line 1, so a gather reads the head of each rollout it has
  not read before. A rollout whose line 1 could not be read is listed: a fact that was not
  read hides no session. Compressed rollouts are not read, and are counted in
  `Gathered.unreadable`.

Entries are ordered by their last change, newest first, then by harness and session id
(`Entry.order`). A root that cannot be listed gives no entries and one `RootFailure` with
the reason; the other roots still answer. One gather runs at a time, callers that arrive
meanwhile take its result, and a result is reused for `REUSE_S` seconds. A gather takes no
filter, so every filter shares it.

**The details.** `Catalog.details(entry)` reads what a row shows of one session, a
`Details`. A fact that was not read is None; nothing is filled in for it.

* From the head of the file (`Head`; an OpenCode session's come from its row): `folder`,
  `started`, `by_program` and, for Claude Code, the name its first lines give.
  `by_program` is True where a Claude head's `entrypoint` was read and starts with `sdk`,
  and None everywhere else: a session is taken as a person's unless its record says a
  program started it.
* From the session's last turn (`window(entry, turns=1)`): `name`, `model`, `activity`
  and `prompt`.

A read that fails leaves None where it would have filled and is not kept, so the next call
reads again.

**What is kept.** A catalog keeps what it read for as long as it lives, in memory only:

* The last-turn facts of at most `TAIL_MAX` sessions, least recently used dropped. A
  file's are reused while its size and mtime are the ones they were read at, an OpenCode
  session's while its `time_updated` is.
* Each file's head facts, by path, device and inode. A head is read again only when the
  file has grown and the earlier read left a fact open: one that ended at the file's end,
  where later lines can still add a folder, a start, an entrypoint or a name, or a Codex
  read that found no line 1. Once a file has outgrown the head's bounds its head reads the
  same for ever, and it is read once.

One lock guards what is kept. It is never held while a file or a database is read.
`Catalog.read_heads(entries)` makes sure the heads of some entries are read;
`Catalog.read_heads()` does it for every session, one such call at a time.
"""

from __future__ import annotations

import os
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from moxi.config import ConfigError

from . import claude
from .errors import Unreadable
from .find import Listed, Listing, sources
from .read import window
from .shape import CLAUDE, CODEX, HARNESSES, OPENCODE, UNKNOWN, Marker, Message, Request
from .source import claude_header, claude_named_head, codex_head, codex_header

#: How long a gather's result is reused, in seconds.
REUSE_S = 2.0

#: How many sessions' last-turn facts a catalog keeps.
TAIL_MAX = 5000

#: The most bytes of a message `Details.activity` carries.
ACTIVITY_BYTES = 300

#: How many turns before the last one a read looks through for the person's prompt.
PROMPT_TURNS = 4

#: What an `entrypoint` starts with when a program started the session.
PROGRAM_ENTRYPOINT = "sdk"

#: `Said.who` for a message the person typed, and for the model's.
YOU = "you"
AGENT = "agent"


# --- what a catalog hands out ---------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    """One main session, as a listing of its root found it; no session was opened.

    * `harness`, `session`: whose it is and the harness's own id for it.
    * `path`: the transcript, the rollout, or OpenCode's database.
    * `changed_ns`: its last change in epoch nanoseconds: the file's mtime, or an OpenCode
      row's `time_updated`; None where the row has none. `changed` is the same in seconds.
    * `size`: the file's size when it was listed; None for an OpenCode session.
    * `subagents`: how many subagent files, child rows or spawned threads it has.
    * `listed`: the listing entry itself, which `window` and the other reads take.
    """

    harness: str
    session: str
    path: str
    changed_ns: int | None
    size: int | None
    subagents: int
    listed: Listed = field(compare=False, repr=False)

    @property
    def changed(self) -> float | None:
        return None if self.changed_ns is None else self.changed_ns / 1_000_000_000

    @property
    def order(self) -> tuple[int, str, str]:
        """The entry's place in the list: a smaller one comes first. Newest change
        first, then harness, then session id; an entry with no change time comes last."""
        return (-(self.changed_ns or 0), self.harness, self.session)


@dataclass(frozen=True)
class RootFailure:
    """A harness whose root could not be listed, with the root (None when the config
    that names it could not be read) and the reason."""

    harness: str
    root: str | None
    reason: str


@dataclass(frozen=True)
class Gathered:
    """One gather: `entries` in the list's order, `roots` the roots that could not be
    listed, `unreadable` the number of compressed Codex rollouts left out, and `at`, the
    catalog's clock when the gather ended."""

    entries: tuple[Entry, ...]
    roots: tuple[RootFailure, ...] = ()
    unreadable: int = 0
    at: float = 0.0


@dataclass(frozen=True)
class Head:
    """What the head of a session's record says. Each is None where it was not read.

    * `folder`: the session's directory.
    * `started`: when it started, in epoch seconds.
    * `by_program`: True where a Claude head's `entrypoint` starts with `sdk`, else None.
    * `name`: the name a Claude file's first lines give the session.
    * `spawn`: a Codex rollout's spawn link (`Header.spawn`): None for a thread no thread
      spawned, `UNKNOWN` where line 1 was not read. None for the other harnesses.
    * `dev`, `ino`, `size`: the file the head was read from and its size then; None for an
      OpenCode session.
    * `settled`: no later read of the same file can say more.
    """

    folder: str | None = None
    started: float | None = None
    by_program: bool | None = None
    name: str | None = None
    spawn: Any = None
    dev: int | None = None
    ino: int | None = None
    size: int | None = None
    settled: bool = True


@dataclass(frozen=True)
class Said:
    """A message a row shows: `at` (epoch seconds, None when the record has no time), who
    said it, and its text.

    `who` is `you` for a user message the record says the person at the prompt typed
    (`Message.injected` is false), `agent` for the model's, and None for a user message
    whose submitter the record does not give or names as something else. It is None for
    every message of a session a program started."""

    at: float | None
    who: str | None
    text: str


@dataclass(frozen=True)
class Details:
    """What a row shows of one session. Each is None where it was not read.

    * `folder`, `started`, `by_program`: the head's (`Head`).
    * `name`: a name the person or a launcher gave the session in the harness: the newest
      `custom` title marker in the last turn, else the head's name. A title Claude Code
      made up is not a name. None for Codex, which writes no title, and for OpenCode,
      whose title does not say who wrote it.
    * `model`: the newest model the last turn names, Claude Code's `<synthetic>` left out.
    * `activity`: the newest message of the last turn that is a user message with text or
      the model's reply, its text cut to `ACTIVITY_BYTES` bytes at a character boundary.
    * `prompt`: the newest user message the record says the person typed, whole, with
      `who` `you`. Where the last turn holds none, up to `PROMPT_TURNS` turns before it are
      read. None, with no further turn read, for a session a program started.
    """

    folder: str | None = None
    started: float | None = None
    by_program: bool | None = None
    name: str | None = None
    model: str | None = None
    activity: Said | None = None
    prompt: Said | None = None


# --- reading one session ---------------------------------------------------------------------

def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _time(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def by_program(entrypoint: Any) -> bool | None:
    """True for an `entrypoint` a program's run writes (`sdk-cli`, `sdk-py`); None for
    any other value and for one that was not read."""
    if isinstance(entrypoint, str) and entrypoint.startswith(PROGRAM_ENTRYPOINT):
        return True
    return None


def cut(text: str, limit: int = ACTIVITY_BYTES) -> str:
    """`text` cut to at most `limit` bytes of UTF-8, at a character boundary."""
    data = text.encode("utf-8", "replace")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", "ignore")


def row_head(listed: Listed) -> Head:
    """An OpenCode session's head facts, from the row its listing read."""
    header = listed.header
    if header is None:
        return Head()
    return Head(folder=_str(header.cwd), started=_time(header.started))


def read_head(listed: Listed, imports: Any = None) -> Head | None:
    """The head facts of a Claude transcript or a Codex rollout, from one open of the
    file; None when it cannot be opened or read. `imports` is the Codex imports file as a
    listing read it; without it a rollout's read looks for the file itself."""
    source = listed.source
    try:
        with open(source.path, "rb") as handle:
            st = os.fstat(handle.fileno())
            end = st.st_size
            if source.harness == CLAUDE:
                header = claude_header(source, handle, end)
                name, settled = claude_named_head(handle, end)
                spawn = None
            else:
                head = codex_head(handle, end)
                header = codex_header(source, head, imports)
                name = None
                spawn = header.spawn
                settled = head.meta is not None
    except OSError:
        return None
    return Head(folder=_str(header.cwd), started=_time(header.started),
                by_program=by_program(header.entrypoint), name=name, spawn=spawn,
                dev=st.st_dev, ino=st.st_ino, size=end, settled=settled)


@dataclass(frozen=True)
class Tail:
    """What a session's last turn says: `name` (the newest `custom` title marker),
    `model`, `activity` and `prompt` as in `Details`, read for a session whose
    `by_program` was `program`."""

    program: bool | None = None
    name: str | None = None
    model: str | None = None
    activity: Said | None = None
    prompt: Said | None = None


def _spoken(record: Any) -> bool:
    return (isinstance(record, Message) and not record.reasoning
            and bool(record.text.strip()))


def _name(records: Sequence[Any]) -> str | None:
    for record in reversed(records):
        if (isinstance(record, Marker) and record.marker == "title"
                and record.fields.get("source") == "custom"):
            title = _str(record.fields.get("title"))
            if title is not None:
                return title
    return None


def _model(records: Sequence[Any]) -> str | None:
    for record in reversed(records):
        if isinstance(record, Request):
            named = (record.response_model, record.model)
        elif isinstance(record, Message):
            named = (record.model,)
        elif isinstance(record, Marker) and record.marker == "settings":
            named = (record.fields.get("model"),)
        else:
            continue
        for model in named:
            if _str(model) is not None and model != claude.SYNTHETIC_MODEL:
                return model
    return None


def _activity(records: Sequence[Any], program: bool | None) -> Said | None:
    for record in reversed(records):
        if not _spoken(record):
            continue
        if record.role == "assistant":
            if record.synthetic is True:
                continue                 # the harness's own line, not the model's reply
            who = AGENT
        elif record.role == "user":
            who = YOU if record.injected is False else None
        else:
            continue
        return Said(_time(record.time), None if program is True else who,
                    cut(record.text))
    return None


def _prompt(records: Sequence[Any]) -> Said | None:
    for record in reversed(records):
        if _spoken(record) and record.role == "user" and record.injected is False:
            return Said(_time(record.time), YOU, record.text)
    return None


def read_tail(listed: Listed, program: bool | None = None) -> Tail | None:
    """The last-turn facts of a session whose `by_program` is `program`; None when a read
    of it failed."""
    try:
        page = window(listed, turns=1)
        records = page.records
        prompt = None
        if program is not True:
            prompt = _prompt(records)
            for _ in range(PROMPT_TURNS):
                if prompt is not None or not page.has_earlier or page.before is None:
                    break
                page = window(listed, turns=1, before=page.before)
                prompt = _prompt(page.records)
    except (Unreadable, OSError, ValueError):
        return None
    return Tail(program=program,
                name=_name(records) if listed.source.harness == CLAUDE else None,
                model=_model(records), activity=_activity(records, program), prompt=prompt)


# --- the entries -----------------------------------------------------------------------------

def _entry(listed: Listed, subagents: int, changed_ns: int | None = None) -> Entry:
    """A listing entry as a main session; a file's change time is its mtime."""
    source = listed.source
    return Entry(harness=source.harness, session=source.session_id or "",
                 path=source.path,
                 changed_ns=listed.mtime_ns if changed_ns is None else changed_ns,
                 size=listed.size, subagents=subagents, listed=listed)


def _claude_entries(listing: Listing) -> list[Entry]:
    agents = Counter(e.source.session_id for e in listing if e.kind == "subagent")
    return [_entry(e, agents[e.source.session_id]) for e in listing if e.kind == "main"]


def _opencode_entries(listing: Listing) -> list[Entry]:
    out = []
    children: Counter[str] = Counter()
    for listed in listing:
        row = listed.header.session_row if listed.header is not None else None
        if row is not None and row.parent_id is not None:
            children[row.parent_id] += 1
    for listed in listing:
        row = listed.header.session_row if listed.header is not None else None
        if row is None or row.parent_id is not None:
            continue
        updated = _time(row.time_updated)        # epoch seconds, written in milliseconds
        out.append(_entry(listed, children[row.id],
                          None if updated is None else round(updated * 1000) * 1_000_000))
    return out


class _Flight:
    """One call in flight: who waits on it, and what it gave."""

    __slots__ = ("done", "value", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None


class Catalog:
    """The sessions of the three roots and what was read of them (see the module).

    `roots` names a root per harness (a projects root, a sessions root, a database); a
    harness it leaves out takes its configured or default root. A page server keeps one
    catalog for its life (`shared()`); a test makes its own. `clock` is the time a
    gather's reuse is measured on. `head_reads` and `tail_reads` count the heads and the
    last turns the catalog has read, failed reads included.
    """

    def __init__(self, roots: Mapping[str, str | Path] | None = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 reuse_s: float = REUSE_S, tail_max: int = TAIL_MAX):
        self._roots = dict(roots or {})
        self._clock = clock
        self._reuse_s = reuse_s
        self._tail_max = tail_max
        self._lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}
        self._gathered: Gathered | None = None
        self._heads: dict[str, Head] = {}
        self._tails: OrderedDict[tuple[str, str], tuple[tuple, Tail]] = OrderedDict()
        self.head_reads = 0
        self.tail_reads = 0

    # -- one call at a time -------------------------------------------------------------

    def _single(self, name: str, make: Callable[[], Any],
                kept: Callable[[], Any] | None = None) -> Any:
        """`make()`, run by one caller at a time: a caller that arrives while it runs
        waits and takes its result, or its error. `kept` is asked first, under the lock,
        for a result that needs no call."""
        with self._lock:
            if kept is not None:
                value = kept()
                if value is not None:
                    return value
            flight = self._flights.get(name)
            mine = flight is None
            if flight is None:
                flight = self._flights[name] = _Flight()
        if not mine:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value
        try:
            flight.value = make()
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with self._lock:
                del self._flights[name]
            flight.done.set()
        return flight.value

    # -- the entries ----------------------------------------------------------------------

    def gather(self) -> Gathered:
        """Every main session of the three roots, in the list's order. One gather runs at
        a time and its result is reused for the catalog's reuse time."""
        def kept() -> Gathered | None:
            last = self._gathered
            if last is not None and self._clock() - last.at < self._reuse_s:
                return last
            return None

        return self._single("gather", self._gather, kept)

    def _gather(self) -> Gathered:
        entries: list[Entry] = []
        failures: list[RootFailure] = []
        unreadable = 0
        for harness in HARNESSES:
            try:
                listing = sources(harness, root=self._roots.get(harness), heads=False)
            except ConfigError as exc:
                failures.append(RootFailure(harness, None, str(exc)))
                continue
            if not listing.ok:
                failures.append(RootFailure(harness, listing.root,
                                            listing.reason or "could not be listed"))
            elif harness == CLAUDE:
                entries += _claude_entries(listing)
            elif harness == OPENCODE:
                entries += _opencode_entries(listing)
            else:
                unreadable += sum(1 for e in listing if e.compressed)
                entries += self._codex_entries(listing)
        entries.sort(key=lambda e: e.order)
        seen: set[tuple[str, str]] = set()
        kept = []
        for entry in entries:
            # A session two files hold is the one that changed last.
            if (entry.harness, entry.session) not in seen:
                seen.add((entry.harness, entry.session))
                kept.append(entry)
        result = Gathered(tuple(kept), tuple(failures), unreadable, self._clock())
        with self._lock:
            self._gathered = result
        return result

    def _codex_entries(self, listing: Listing) -> list[Entry]:
        rollouts = [e for e in listing if not e.compressed]
        heads = self._read_heads(rollouts, listing.imports)
        spawned: Counter[str] = Counter()
        mains = []
        for listed, head in zip(rollouts, heads):
            spawn = head.spawn if head is not None else UNKNOWN
            if spawn is None or spawn is UNKNOWN:
                mains.append(listed)
            elif isinstance(spawn.parent_thread_id, str):
                spawned[spawn.parent_thread_id] += 1
        return [_entry(e, spawned[e.source.session_id]) for e in mains]

    # -- the heads ------------------------------------------------------------------------

    def _kept_head(self, listed: Listed) -> tuple[Head | None, bool]:
        """The head kept for a file, and whether it stands for the file as listed. Called
        under the lock."""
        head = self._heads.get(listed.source.path)
        if head is None or (head.dev, head.ino) != (listed.dev, listed.ino):
            return None, False
        grown = (listed.size or 0) > (head.size or 0)
        return head, head.settled or not grown

    def _read_heads(self, listeds: Sequence[Listed], imports: Any = None
                    ) -> list[Head | None]:
        """The head of each listing entry, in order: the kept one where it stands, else
        one read now and kept. None where the file could not be read and none is kept."""
        out: list[Head | None] = [None] * len(listeds)
        todo = []
        with self._lock:
            for index, listed in enumerate(listeds):
                if listed.source.harness == OPENCODE:
                    out[index] = row_head(listed)
                    continue
                out[index], stands = self._kept_head(listed)
                if not stands:
                    todo.append(index)
        read = [(index, read_head(listeds[index], imports)) for index in todo]
        if read:
            with self._lock:
                self.head_reads += len(read)
                for index, head in read:
                    if head is not None:
                        self._heads[listeds[index].source.path] = head
                        out[index] = head
        return out

    def read_heads(self, entries: Iterable[Entry] | None = None) -> None:
        """Make sure the head of each of `entries` is read, so `head(entry)` answers
        from what is kept. With None, the head of every session: one such call runs at a
        time, and a caller that arrives meanwhile waits for it and reads nothing."""
        if entries is not None:
            self._read_heads([e.listed for e in entries])
            return

        def every() -> None:
            self._read_heads([e.listed for e in self.gather().entries])

        self._single("heads", every)

    def head(self, entry: Entry) -> Head | None:
        """The entry's head facts, read now unless a kept read stands; None when the file
        could not be read."""
        return self._read_heads([entry.listed])[0]

    def kept_head(self, entry: Entry) -> Head | None:
        """The entry's head facts as last read, with no read; None when none is kept."""
        if entry.harness == OPENCODE:
            return row_head(entry.listed)
        with self._lock:
            return self._kept_head(entry.listed)[0]

    # -- the details ------------------------------------------------------------------------

    def _tail(self, entry: Entry, program: bool | None) -> Tail | None:
        listed = entry.listed
        if entry.harness == OPENCODE:
            key, stamp = (entry.path, entry.session), (entry.changed_ns,)
        else:
            key, stamp = (entry.path, ""), (listed.size, listed.mtime_ns)
        with self._lock:
            kept = self._tails.get(key)
            if kept is not None and kept[0] == stamp and kept[1].program == program:
                self._tails.move_to_end(key)
                return kept[1]
        tail = read_tail(listed, program)
        with self._lock:
            self.tail_reads += 1
            if tail is not None:
                self._tails[key] = (stamp, tail)
                self._tails.move_to_end(key)
                while len(self._tails) > self._tail_max:
                    self._tails.popitem(last=False)
        return tail

    def details(self, entry: Entry) -> Details:
        """What a row shows of `entry`: its head facts and its last turn's, each read
        now unless a kept read stands. Never raises for a session that cannot be read: a
        fact that was not read is None."""
        head = self.head(entry) or Head()
        tail = self._tail(entry, head.by_program) or Tail()
        return Details(folder=head.folder, started=head.started,
                       by_program=head.by_program,
                       name=(tail.name or head.name) if entry.harness == CLAUDE else None,
                       model=tail.model, activity=tail.activity, prompt=tail.prompt)


# --- the process's catalog ------------------------------------------------------------------

_shared: Catalog | None = None
_shared_lock = threading.Lock()


def shared() -> Catalog:
    """The process's one catalog over the configured roots, made on first use. A page
    server reads through it for its whole life, so what one request read the next
    reuses."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Catalog()
        return _shared


def reset() -> None:
    """Forget the process's catalog and everything it kept; the next `shared()` makes a
    new one."""
    global _shared
    with _shared_lock:
        _shared = None


__all__ = ["REUSE_S", "TAIL_MAX", "ACTIVITY_BYTES", "PROMPT_TURNS", "YOU", "AGENT",
           "Entry", "RootFailure", "Gathered", "Head", "Said", "Details", "Tail",
           "Catalog", "shared", "reset", "by_program", "cut", "row_head", "read_head",
           "read_tail"]
