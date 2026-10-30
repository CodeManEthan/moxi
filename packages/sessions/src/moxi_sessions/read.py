"""Reading one source's records: `records`, `records_back`, `last_request`,
`records_since` and `window`.

Every read takes a `Source` (or a listing entry, which also bounds the read at the size the
listing saw) and returns its records with the session's `Header` from the same snapshot: a
file read takes the size from the open handle once and reads no byte past it, and an
OpenCode read runs in one read transaction. A read that cannot open or read its source
raises `Unreadable`; it never stands for an empty read.

* `records` streams every record forward, one line or one message at a time.
* `records_back` maps the newest lines first, for a question about the end of a session.
* `last_request` is the newest request on the source's own chain.
* `records_since` is the forward cursor: the records written since the caller's cursor.
* `window` is the last few turns, with a position to page further back.

**Records read backward.** A line's records need state from lines before it (Claude's
current `promptId`; Codex's turn, model, provider and context window), and a backward read
meets those lines last. So the backward read collects lines back to the line that sets the
turn (a Claude line that writes its own `promptId`, a Codex `task_started`), maps that run
forward, and hands its records on newest first. A Codex run starts from the state after
line 1, which the read always takes. Two facts stay out of reach of a run: a Codex
`thread_settings_applied` written before the request's turn (the provider is then line
1's), and, for lines the byte limit cut off from their turn, the turn itself (their
`turn_id` and model are unknown). A backward read counts no lines, so its records' `at` is
`{"line": None, "offset": b}`, except where a request's key is its line number (a Claude
request with no id of any kind, a legacy Codex request): that line is numbered, so its
`request_id` is the one a forward read gives.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO

from . import claude, codex
from .errors import Unreadable
from .reader import LIMIT, START, read_back
from .shape import CLAUDE, CODEX, OPENCODE, UNKNOWN, Record, Request, known
from .source import Header, Source, file_header, opencode_header, unpack

#: The cursor format this reader writes. A cursor with a newer version is refused, and
#: one with an older version restarts the read.
CURSOR_VERSION = 1

#: How many bytes from a file's start the cursor's fingerprint covers (fewer while the
#: file is shorter).
FINGERPRINT_BYTES = 4096

#: The backward read's block and byte limit, and `window`'s limit.
BLOCK = 65536
BACK_LIMIT = 67108864
WINDOW_LIMIT = 8388608

#: The reason a compressed rollout cannot be read.
ZSTD_REASON = ("is a compressed rollout, and sessions reads no zstd (Python 3.12's "
               "standard library has none)")

#: The reason a newer cursor is refused.
NEWER_CURSOR = "cursor from a newer sessions"


# --- opening a file ------------------------------------------------------------------------

def _refuse_compressed(source: Source) -> None:
    if source.harness == CODEX and str(source.path).endswith(".zst"):
        raise Unreadable(f"{source.path} {ZSTD_REASON}")


def _cannot(source: Source, error: OSError) -> Unreadable:
    return Unreadable(f"{source.path} cannot be read ({error.strerror or error})")


def _open(source: Source, bound: int | None) -> tuple[BinaryIO, os.stat_result, int]:
    """The open file, its stat, and the byte the read treats as its end."""
    _refuse_compressed(source)
    try:
        handle = open(source.path, "rb")
    except OSError as e:
        raise _cannot(source, e) from e
    try:
        st = os.fstat(handle.fileno())
    except OSError as e:
        handle.close()
        raise _cannot(source, e) from e
    end = st.st_size if bound is None else min(st.st_size, bound)
    return handle, st, end


def _lazy_header(source: Source, dev: int, ino: int, end: int) -> Callable[[], Header]:
    """The file's header, read from its head up to `end` when first asked for. It is read
    from the file the read opened: if the path names another file by then, it raises
    Unreadable rather than describe that one."""
    def read() -> Header:
        try:
            handle = open(source.path, "rb")
        except OSError as e:
            raise _cannot(source, e) from e
        with handle:
            st = os.fstat(handle.fileno())
            if (st.st_dev, st.st_ino) != (dev, ino):
                raise Unreadable(f"{source.path} was replaced after it was read")
            return file_header(source, handle, end)
    return read


class _Once:
    """A value computed on first use."""

    __slots__ = ("_make", "_value", "_done")

    def __init__(self, make: Callable[[], Any] | None = None, value: Any = None):
        self._make = make
        self._value = value
        self._done = make is None

    def get(self) -> Any:
        if not self._done:
            self._value = self._make()
            self._done = True
            self._make = None
        return self._value


def _wanted(kinds: Iterable[str] | None) -> frozenset[str] | None:
    if kinds is None:
        return None
    if isinstance(kinds, str):
        return frozenset({kinds})
    return frozenset(kinds)


def _needs_session(source: Source) -> str:
    if source.session_id is None:
        raise ValueError("an OpenCode read of records names its session "
                         "(Source.session_id); only records_since reads the database whole")
    return source.session_id


def _opencode():
    # Imported on first use: it brings sqlite3, which a file reader never needs.
    from . import opencode
    return opencode


# --- results -------------------------------------------------------------------------------

class Read:
    """A forward read: iterate it for the records, in `at` order (a Claude request comes
    where it ended, at its newest line). `header` is the session's header from the same
    snapshot. The source stays open until the records run out or `close()` is called; use
    it as a context manager to be sure."""

    def __init__(self, harness: str, header: _Once, records: Iterator[Record],
                 closer: Callable[[], None]):
        self.harness = harness
        self._header = header
        self._records = records
        self._closer = closer

    @property
    def header(self) -> Header:
        return self._header.get()

    def __iter__(self) -> Iterator[Record]:
        return self._records

    def close(self) -> None:
        self._records.close()  # type: ignore[attr-defined]
        self._closer()

    def __enter__(self) -> Read:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class Back:
    """A backward read: iterate it for the records, newest first, each request key once
    (from its newest line, with that line's values).

    * `ended`: how the read ended, as `read_back` says it: `start` once every line back to
      line 1 (line 2 with `head`) was read, `limit` when the byte limit stopped it with
      lines left, None until the records run out or when the caller stops first. An
      OpenCode read always reaches the session's first record, so it is `start`.
    * `first`: with `head`, line 1's records (an OpenCode session's first message's), not
      also among the records iterated; None without `head`, or when line 1 does not end
      within the limit.
    * `header`: the session's header, read when first asked for.
    """

    def __init__(self, harness: str, header: _Once, records: Iterator[Record],
                 first: tuple[Record, ...] | None, ended: str | None = None,
                 closer: Callable[[], None] | None = None):
        self.harness = harness
        self._header = header
        self._records = records
        self._closer = closer
        self.first = first
        self.ended = ended

    @property
    def header(self) -> Header:
        return self._header.get()

    def __iter__(self) -> Iterator[Record]:
        return self._records

    def close(self) -> None:
        close = getattr(self._records, "close", None)
        if close is not None:
            close()
        if self._closer is not None:
            self._closer()

    def __enter__(self) -> Back:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class LastRequest:
    """The newest request on a source's own chain (`request`, None when the read found
    none) and how the read ended (`ended`): None when it found one, `start` when it read
    back to the source's start and found none, `limit` when the byte limit stopped it
    first. `header` is read when first asked for."""

    __slots__ = ("request", "ended", "_header")

    def __init__(self, request: Request | None, ended: str | None, header: _Once):
        self.request = request
        self.ended = ended
        self._header = header

    @property
    def header(self) -> Header:
        return self._header.get()

    def __repr__(self) -> str:
        key = self.request.request_id if self.request is not None else None
        return f"LastRequest(request={key!r}, ended={self.ended!r})"


class Batch:
    """One `records_since` call.

    * `records`: the records new since the cursor, in order. Only requests ever come
      again: a request a later line adds to, or (OpenCode) every record of a message read
      again; the caller replaces them by `request_id` (OpenCode: by `request_id`,
      `call_id` or `at`).
    * `cursor`: the opaque cursor to hand back next time.
    * `reset`: true when the caller must drop what it holds for the source: the cursor
      could not be trusted, or the file was rewritten, and the read started over.
    * `complete`: false when `max_bytes` stopped the batch with complete lines left. An
      unterminated last line is left for a later call and does not make a batch
      incomplete.
    * `closed`: the keys of requests an earlier batch handed on with `final` false that
      this batch saw end. A key this batch yields is never also in `closed`.
    * `gone`: OpenCode only, at least how many message rows read earlier are gone; None
      for a file.
    * `header`: the session's header, read when first asked for; None when nothing was
      read (the caller keeps the one it has).
    """

    __slots__ = ("records", "cursor", "reset", "complete", "closed", "gone", "_header")

    def __init__(self, *, records: tuple[Record, ...], cursor: str, reset: bool,
                 complete: bool, closed: tuple[str, ...], gone: int | None,
                 header: _Once | None):
        self.records = records
        self.cursor = cursor
        self.reset = reset
        self.complete = complete
        self.closed = closed
        self.gone = gone
        self._header = header

    @property
    def header(self) -> Header | None:
        return self._header.get() if self._header is not None else None

    def __repr__(self) -> str:
        return (f"Batch(records={len(self.records)}, reset={self.reset}, "
                f"complete={self.complete}, closed={self.closed!r}, gone={self.gone!r})")


class Window:
    """The last turns of a session, oldest record first.

    * `records`: the turns' records, oldest first.
    * `has_earlier`: true when the session has records before them.
    * `before`: with `has_earlier`, the position to hand back as `window(before=...)` for
      the turns before these (a byte offset in a file, a message id in OpenCode); None
      otherwise.
    * `ended`: the backward read's ending, as `Back.ended`. At `limit` the byte limit
      stopped the read: a turn it may have cut short is left out unless it is the only
      one, and then it is returned as far as it was read.
    * `header`: read when first asked for.
    """

    __slots__ = ("records", "has_earlier", "before", "ended", "_header")

    def __init__(self, records: tuple[Record, ...], has_earlier: bool, before: Any,
                 ended: str | None, header: _Once):
        self.records = records
        self.has_earlier = has_earlier
        self.before = before
        self.ended = ended
        self._header = header

    @property
    def header(self) -> Header:
        return self._header.get()

    def __repr__(self) -> str:
        return (f"Window(records={len(self.records)}, has_earlier={self.has_earlier}, "
                f"before={self.before!r}, ended={self.ended!r})")


# --- the forward read ----------------------------------------------------------------------

def _mapper(source: Source, keep_raw: bool, *, prompt_id: str | None = None,
            open_key: str | None = None, state: codex.State | None = None):
    if source.harness == CLAUDE:
        return claude.Mapper(session_id=source.session_id, agent_id=source.agent_id,
                             keep_raw=keep_raw, prompt_id=prompt_id, open_key=open_key)
    return codex.Mapper(session_id=source.session_id, agent_id=source.agent_id,
                        keep_raw=keep_raw, state=state)


def _needs(harness: str, mapper: Any, raw: bytes, wanted: frozenset[str] | None) -> bool:
    if wanted is None:
        return True
    if harness == CLAUDE:
        return claude.needs_decode(raw, wanted)
    return codex.needs_decode(raw, wanted, legacy=mapper.state.legacy)


#: A `promptId` as Claude Code writes it, for reading a line's turn without decoding it.
_PROMPT = re.compile(rb'"promptId"\s*:\s*"([^"\\]*)"')


def _claude_prompt_only(raw: bytes, wanted: frozenset[str] | None) -> bool:
    """Whether a filtered read decodes this Claude line only for its `promptId`: it holds
    no requested kind's marker bytes and cannot end a request."""
    if wanted is None or b'"turn_duration"' in raw:
        return False
    for kind in wanted:
        markers = claude.MARKER_BYTES.get(kind)
        if markers is None or any(marker in raw for marker in markers):
            return False
    return True


def _claude_prompt(raw: bytes, current: str | None) -> str | None:
    """The turn after a line read only for its `promptId`. When every `promptId` the line
    holds names the current turn, the line cannot change it and is not decoded; otherwise
    it is, and its own top-level `promptId` (if any) is the turn."""
    if current is not None:
        found = _PROMPT.findall(raw)
        mine = current.encode("utf-8")
        if found and len(found) == raw.count(b'"promptId"') and all(
                value == mine for value in found):
            return current
    entry = claude.parse_line(raw)
    prompt = entry.get("promptId") if entry is not None else None
    return prompt if isinstance(prompt, str) and prompt else current


def _feed(source: Source, mapper: Any, data: bytes, wanted: frozenset[str] | None, *,
          line: int, offset: int, unterminated: bool = False) -> list[Record]:
    """One line through the mapper, skipping what a filtered read does not need: a line
    that cannot give a requested record or change one is not decoded, and a Claude line
    that can only move the turn is read for its `promptId` alone."""
    if not _needs(source.harness, mapper, data, wanted):
        return []
    if source.harness == CLAUDE and _claude_prompt_only(data, wanted):
        mapper.prompt_id = _claude_prompt(data, mapper.prompt_id)
        return []
    return mapper.feed(data, line=line, offset=offset, unterminated=unterminated)


def _file_records(source: Source, handle: BinaryIO, end: int,
                  wanted: frozenset[str] | None, keep_raw: bool) -> Iterator[Record]:
    try:
        mapper = _mapper(source, keep_raw)
        handle.seek(0)
        offset = 0
        number = 0
        while offset < end:
            data = handle.readline(end - offset)
            if not data:
                break
            number += 1
            start, offset = offset, offset + len(data)
            for record in _feed(source, mapper, data, wanted, line=number, offset=start,
                                unterminated=not data.endswith(b"\n")):
                if wanted is None or record.kind in wanted:
                    yield record
        for record in mapper.finish():
            if wanted is None or record.kind in wanted:
                yield record
    except OSError as e:
        raise _cannot(source, e) from e
    finally:
        handle.close()


def records(source: Any, *, kinds: Iterable[str] | None = None,
            keep_raw: bool = False) -> Read:
    """Every record of the source, forward, one line (OpenCode: one message and its
    parts) held at a time.

    `kinds` keeps only records of those kinds, and a line that cannot give one is not
    decoded; the records that come are the same as without it. `keep_raw` puts each
    line's or row's decoded object in its records' `raw`. A request still open at the end
    of the file comes with `final` false. Raises Unreadable when the source cannot be
    opened or read (a compressed rollout among them), and ValueError for an OpenCode
    source that names no session.
    """
    src, bound = unpack(source)
    wanted = _wanted(kinds)
    if src.harness == OPENCODE:
        oc = _opencode()
        session_id = _needs_session(src)
        snap = oc.Snapshot(src.path)
        try:
            row = snap.session(session_id)
            snap.require("message", "part")
        except BaseException:
            snap.close()
            raise

        def stream() -> Iterator[Record]:
            try:
                yield from snap.records(session_id, kinds=wanted, keep_raw=keep_raw)
            finally:
                snap.close()
        return Read(OPENCODE, _Once(value=opencode_header(src, row)), stream(), snap.close)
    handle, st, end = _open(src, bound)
    return Read(src.harness, _Once(_lazy_header(src, st.st_dev, st.st_ino, end)),
                _file_records(src, handle, end, wanted, keep_raw), handle.close)


# --- the backward read ---------------------------------------------------------------------

class _LineNumbers:
    """Line numbers for byte offsets of one file, counted from the newlines before the
    offset. The first count reads from the file's start; later ones count only between
    the offset asked for and the one counted last."""

    def __init__(self, source: Source, end: int):
        self.source = source
        self.end = end
        self.known: tuple[int, int] = (0, 1)

    def _newlines(self, start: int, stop: int) -> int:
        try:
            with open(self.source.path, "rb") as handle:
                handle.seek(start)
                count = 0
                left = stop - start
                while left > 0:
                    data = handle.read(min(BLOCK, left))
                    if not data:
                        break
                    count += data.count(b"\n")
                    left -= len(data)
                return count
        except OSError as e:
            raise _cannot(self.source, e) from e

    def number(self, offset: int) -> int:
        at, line = self.known
        if offset >= at:
            line += self._newlines(at, offset)
        else:
            line -= self._newlines(offset, at)
        self.known = (offset, line)
        return line


class _Pushback:
    """An iterator that takes lines back."""

    def __init__(self, items: Iterator):
        self.items = items
        self.back: deque = deque()

    def __iter__(self) -> _Pushback:
        return self

    def __next__(self):
        if self.back:
            return self.back.popleft()
        return next(self.items)

    def push(self, items: list) -> None:
        self.back.extendleft(reversed(items))


def _claude_anchor(raw: bytes) -> bool:
    """A line that writes its own `promptId`: the turn of every line after it, up to the
    next such line, is known from it."""
    if b'"promptId"' not in raw:
        return False
    entry = claude.parse_line(raw)
    prompt = entry.get("promptId") if entry is not None else None
    return isinstance(prompt, str) and bool(prompt)


def _codex_anchor(raw: bytes) -> bool:
    """A `task_started` event: a turn's start, from which its lines map forward."""
    if b'"task_started"' not in raw:
        return False
    entry = claude.parse_line(raw)
    payload = entry.get("payload") if entry is not None else None
    return (entry.get("type") == "event_msg" and isinstance(payload, dict)
            and payload.get("type") == "task_started")


def _codex_token_count(raw: bytes) -> bool:
    if b'"token_count"' not in raw:
        return False
    entry = claude.parse_line(raw)
    payload = entry.get("payload") if entry is not None else None
    return (entry.get("type") == "event_msg" and isinstance(payload, dict)
            and payload.get("type") == "token_count")


def _unnumbered(request_id: str) -> bool:
    """A request key that fell back on a line number the backward read did not know."""
    return request_id == "line:None" or (request_id.startswith("tc:")
                                         and request_id.endswith(":None"))


class _ClaudeBack:
    """Claude Code lines, newest first, to records, newest first."""

    def __init__(self, source: Source, wanted: frozenset[str] | None, keep_raw: bool,
                 numbers: _LineNumbers):
        self.source = source
        self.wanted = wanted
        self.keep_raw = keep_raw
        self.numbers = numbers
        self.seen: set[str] = set()
        self.ended_newer = False       # a newer line ended every request older than it

    def _map(self, raw: bytes, at_line: int | None, offset: int, prompt: str | None,
             unterminated: bool):
        return claude.line_records(raw, at={"line": at_line, "offset": offset},
                                   session_id=self.source.session_id,
                                   agent_id=self.source.agent_id, prompt_id=prompt,
                                   keep_raw=self.keep_raw, unterminated=unterminated)

    def run(self, segment: list[tuple[bytes, bool, int]]) -> list[Record]:
        """One run of lines (newest first) whose oldest line sets the turn, or that
        starts the file. Returns its records, newest first."""
        lines = []
        prompt = None
        for raw, unterminated, offset in reversed(segment):
            if self.wanted is not None and not claude.needs_decode(raw, self.wanted):
                continue
            before = prompt
            request, recs, prompt = self._map(raw, None, offset, prompt, unterminated)
            if request is not None and _unnumbered(request.request_id):
                request, recs, prompt = self._map(raw, self.numbers.number(offset),
                                                  offset, before, unterminated)
            lines.append((request, recs))
        out: list[Record] = []
        for request, recs in reversed(lines):
            if request is not None:
                if request.request_id not in self.seen:
                    self.seen.add(request.request_id)
                    out.append(replace(request, final=self.ended_newer))
                self.ended_newer = True
            elif recs and recs[0].raw_kind == "system/turn_duration":
                self.ended_newer = True
            out.extend(reversed(recs))
        return out

    def first(self, raw: bytes, unterminated: bool) -> tuple[Record, ...]:
        request, recs, _ = self._map(raw, 1, 0, None, unterminated)
        out = list(recs)
        if request is not None:
            out.insert(0, request)
        return tuple(out)

    def records(self, lines: Iterator) -> Iterator[Record]:
        segment: list = []
        for item in lines:
            segment.append(item)
            if _claude_anchor(item[0]):
                yield from self.run(segment)
                segment = []
        if segment:
            yield from self.run(segment)


class _CodexBack:
    """Codex lines, newest first, to records, newest first, each run mapped forward from
    the state after line 1."""

    def __init__(self, source: Source, wanted: frozenset[str] | None, keep_raw: bool,
                 numbers: _LineNumbers, start: codex.State):
        self.source = source
        self.wanted = wanted
        self.keep_raw = keep_raw
        self.numbers = numbers
        self.start = start

    def _map(self, raw: bytes, at_line: int | None, offset: int, state: codex.State,
             unterminated: bool):
        return codex.line_records(raw, at={"line": at_line, "offset": offset}, state=state,
                                  agent_id=self.source.agent_id, keep_raw=self.keep_raw,
                                  unterminated=unterminated)

    def run(self, segment: list[tuple[bytes, bool, int]], state: codex.State) -> list[Record]:
        lines = []
        for raw, unterminated, offset in reversed(segment):
            if self.wanted is not None and not codex.needs_decode(raw, self.wanted,
                                                                  legacy=state.legacy):
                continue
            before = state
            recs, state = self._map(raw, None, offset, state, unterminated)
            if any(r.kind == "request" and _unnumbered(r.request_id) for r in recs):
                recs, state = self._map(raw, self.numbers.number(offset), offset, before,
                                        unterminated)
            lines.append(recs)
        out: list[Record] = []
        for recs in reversed(lines):
            out.extend(reversed(recs))
        return out

    def records(self, lines: Iterator) -> Iterator[Record]:
        pending = _Pushback(iter(lines))
        while True:
            segment: list = []
            anchored = False
            for item in pending:
                segment.append(item)
                if _codex_anchor(item[0]):
                    anchored = True
                    break
            if not segment:
                return
            state = self.start
            if anchored and state.legacy:
                # A legacy request skips a repeat of the previous `token_count`, which can
                # sit before the turn: read back to it for the state, and hand the lines
                # read on the way to the next run.
                looked = []
                for item in pending:
                    looked.append(item)
                    if _codex_token_count(item[0]):
                        _, state = self._map(item[0], None, item[2], self.start, False)
                        break
                pending.push(looked)
            yield from self.run(segment, state)


def _filtered(records: Iterable[Record], wanted: frozenset[str] | None) -> Iterator[Record]:
    if wanted is None:
        yield from records
    else:
        for record in records:
            if record.kind in wanted:
                yield record


def _file_back(src: Source, *, end: int | None, wanted: frozenset[str] | None,
               block: int, limit: int, head: bool, keep_raw: bool) -> Back:
    _refuse_compressed(src)
    codex_file = src.harness == CODEX
    try:
        reader = read_back(src.path, block=block, limit=limit, head=head or codex_file,
                           end=end, offsets=True)
    except OSError as e:
        raise _cannot(src, e) from e
    header = _Once(_lazy_header(src, reader.stat.st_dev, reader.stat.st_ino, reader.size))
    numbers = _LineNumbers(src, reader.size)
    first: tuple[Record, ...] | None = None
    tail: tuple[Record, ...] = ()
    # Line 1 is unterminated only when it is the whole file and ends without a newline.
    first_open = reader.first is not None and 0 < reader.size == len(reader.first)
    if codex_file:
        state = codex.State(session_id=src.session_id) if src.session_id is not None \
            else codex.State()
        line_one: list[Record] = []
        if reader.first is not None and reader.first.strip():
            line_one, state = codex.line_records(
                reader.first, at={"line": 1, "offset": 0}, state=state,
                agent_id=src.agent_id, keep_raw=keep_raw, unterminated=first_open)
        mapper: Any = _CodexBack(src, wanted, keep_raw, numbers, state)
        if head:
            first = tuple(_filtered(line_one, wanted)) if reader.first is not None else None
        else:
            tail = tuple(_filtered(reversed(line_one), wanted))
    else:
        mapper = _ClaudeBack(src, wanted, keep_raw, numbers)
        if head and reader.first is not None:
            first = tuple(_filtered(mapper.first(reader.first, first_open), wanted)) \
                if reader.first.strip() else ()

    back = Back(src.harness, header, iter(()), first, closer=reader.close)

    def stream() -> Iterator[Record]:
        try:
            yield from _filtered(mapper.records(reader), wanted)
            if tail and reader.ended == START:
                yield from tail
            back.ended = reader.ended
        except OSError as e:
            raise _cannot(src, e) from e
        finally:
            reader.close()

    back._records = stream()
    return back


def records_back(source: Any, *, kinds: Iterable[str] | None = None, block: int = BLOCK,
                 limit: int = BACK_LIMIT, head: bool = False, keep_raw: bool = False) -> Back:
    """The source's records, newest first, read backward from its end in `block`-byte
    reads up to `limit` bytes (`read_back`'s read), each request key once, from its newest
    line.

    A Claude request's `final` is true when a newer line belongs to another request or ends
    the turn. With `head`, line 1 is read first and its records are `Back.first`. An
    unterminated last line that does not decode is a `malformed` record with
    `unterminated` true. An OpenCode session is read whole, in one read transaction, and
    its records come in the exact reverse of a forward read's order. Raises Unreadable
    when the source cannot be opened or read.
    """
    src, bound = unpack(source)
    wanted = _wanted(kinds)
    if src.harness == OPENCODE:
        return _opencode_back(src, wanted, head, keep_raw)
    return _file_back(src, end=bound, wanted=wanted, block=block, limit=limit, head=head,
                      keep_raw=keep_raw)


def _opencode_back(src: Source, wanted: frozenset[str] | None, head: bool,
                   keep_raw: bool) -> Back:
    oc = _opencode()
    session_id = _needs_session(src)
    with oc.Snapshot(src.path) as snap:
        row = snap.session(session_id)
        snap.require("message", "part")
        agent_id = snap._agent_id(session_id)
        messages = [list(_filtered(oc.message_records(message, snap._parts(message[0]),
                                                      agent_id=agent_id,
                                                      keep_raw=keep_raw), wanted))
                    for message in snap._messages(session_id)]
    first = None
    if head:
        first = tuple(messages.pop(0)) if messages else ()
    out = [record for recs in reversed(messages) for record in reversed(recs)]
    return Back(OPENCODE, _Once(value=opencode_header(src, row)), iter(out), first,
                ended=oc.BACK_ENDED)


def last_request(source: Any, *, synthetic: bool = False, limit: int = BACK_LIMIT,
                 block: int = BLOCK) -> LastRequest:
    """The newest request on the source's own chain: its input tokens are the session's
    current prompt size.

    The rule: on any source it is that source's own newest request. On a main transcript
    the own chain is the lines that are not sidechain; on a subagent's file (`agent_id`
    set), where every line is sidechain, it is every line. A synthetic request (Claude
    Code's `<synthetic>`) is passed over unless `synthetic` is true. OpenCode's is the
    session's newest request whose step has finished, since an open step has no tokens
    yet. The read is backward only, and stops at the first request it takes.
    """
    src, bound = unpack(source)
    if src.harness == OPENCODE:
        oc = _opencode()
        session_id = _needs_session(src)
        with oc.Snapshot(src.path) as snap:
            row = snap.session(session_id)
            found = snap.last_request(session_id)
        return LastRequest(found, None if found is not None else oc.BACK_ENDED,
                           _Once(value=opencode_header(src, row)))
    back = _file_back(src, end=bound, wanted=frozenset({"request"}), block=block,
                      limit=limit, head=False, keep_raw=False)
    own_chain_only = src.agent_id is None
    with back:
        for record in back:
            if own_chain_only and record.sidechain is True:
                continue
            if record.synthetic is True and not synthetic:
                continue
            return LastRequest(record, None, back._header)
    return LastRequest(None, back.ended, back._header)


# --- the forward cursor --------------------------------------------------------------------

def _encode(obj: dict) -> str:
    text = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(text.encode("utf-8")).rstrip(b"=").decode("ascii")


def _decode(cursor: Any) -> dict | None:
    if not isinstance(cursor, str):
        return None
    try:
        data = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        obj = json.loads(data)
    except (ValueError, binascii.Error):
        return None
    return obj if isinstance(obj, dict) else None


def _read_cursor(cursor: str | None, harness: str) -> tuple[dict | None, bool]:
    """The cursor's object, and whether the read resets. The version is read first: a
    newer one raises, so a caller keeps what it holds; one that is older, missing or not a
    number, or a cursor for another harness, reads as no cursor, with a reset."""
    if cursor is None:
        return None, False
    obj = _decode(cursor)
    if obj is None:
        return None, True
    version = obj.get("v")
    if isinstance(version, int) and not isinstance(version, bool) \
            and version > CURSOR_VERSION:
        raise Unreadable(NEWER_CURSOR)
    if version != CURSOR_VERSION or isinstance(version, bool) \
            or obj.get("harness") != harness:
        return None, True
    return obj, False


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _optional_str(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _file_cursor(obj: dict, harness: str) -> dict | None:
    """The file cursor's fields, checked, with `state` decoded for Codex; None when one
    is missing or wrong."""
    ok = (all(_count(obj.get(key)) for key in ("dev", "ino", "size", "offset", "line",
                                                  "fp_len"))
          and obj["offset"] <= obj["size"] and obj["fp_len"] <= min(obj["size"],
                                                                      FINGERPRINT_BYTES)
          and isinstance(obj.get("fp"), str) and isinstance(obj.get("complete"), bool))
    if not ok:
        return None
    if harness == CLAUDE:
        if not (_optional_str(obj.get("prompt_id", 0)) and _optional_str(
                obj.get("open_key", 0))):
            return None
        return dict(obj)
    try:
        state = codex.State.from_json(obj.get("state"))
    except (ValueError, TypeError):
        return None
    return {**obj, "state": state}


def records_since(source: Any, cursor: str | None = None, *,
                  kinds: Iterable[str] | None = None, max_bytes: int | None = None,
                  keep_raw: bool = False) -> Batch:
    """The records written since `cursor` (from the start with None), and the cursor to
    hand back next time. sessions stores nothing: the cursor is the caller's to keep.

    A cursor that does not parse, lacks a field, has an older version, or was read for
    another harness or another source reads as None, and the batch has `reset` true; one
    with a newer version raises Unreadable ("cursor from a newer sessions"), so the caller
    keeps what it holds. A cursor the reader cannot trust restarts the read; one it cannot
    understand stops it.

    A file: a batch never hands back a line an earlier batch read, and an append never
    reads as a rewrite. A file smaller than the size the cursor saw, another file at the
    path (its `dev` and `ino`), or other bytes at its start (a hash over up to its first
    4 KiB) is a rewrite: the read starts over with `reset` true. An unterminated last line
    is left for a later call. `max_bytes` bounds one call, but a call always reads at
    least one complete line. With nothing new, the call is one stat and one 4 KiB read.

    A Claude request no later line has ended comes with `final` false; if a later batch
    reads more lines of it, that batch hands it on again, and if a later batch sees it
    end, `closed` names it. A Codex request is one line and always `final`.

    OpenCode: the source is the database, and the session when `session_id` is set (None
    reads the whole database). A batch returns, whole, every message new or changed since
    the cursor; `gone` counts message rows that were read earlier and are gone; the batch
    is always complete, and `max_bytes` does not apply.
    """
    src, bound = unpack(source)
    wanted = _wanted(kinds)
    if max_bytes is not None and (not isinstance(max_bytes, int) or max_bytes < 1):
        raise ValueError(f"max_bytes must be a positive number of bytes, not {max_bytes!r}")
    obj, reset = _read_cursor(cursor, src.harness)
    if src.harness == OPENCODE:
        return _opencode_since(src, obj, reset, wanted, keep_raw)
    fields = _file_cursor(obj, src.harness) if obj is not None else None
    if obj is not None and fields is None:
        reset = True
    handle, st, end = _open(src, bound)
    with handle:
        try:
            return _file_since(src, handle, st, end, fields, reset, cursor, wanted,
                               max_bytes, keep_raw)
        except OSError as e:
            if isinstance(e, Unreadable):
                raise
            raise _cannot(src, e) from e


def _file_since(src: Source, handle: BinaryIO, st: os.stat_result, end: int,
                fields: dict | None, reset: bool, cursor: str | None,
                wanted: frozenset[str] | None, max_bytes: int | None,
                keep_raw: bool) -> Batch:
    head = handle.read(min(FINGERPRINT_BYTES, end))
    if fields is not None:
        same = ((st.st_dev, st.st_ino) == (fields["dev"], fields["ino"])
                and end >= fields["size"]
                and hashlib.sha256(head[:fields["fp_len"]]).hexdigest() == fields["fp"])
        if not same:
            fields, reset = None, True
    if fields is not None and end == fields["size"] and fields["complete"]:
        return Batch(records=(), cursor=cursor, reset=False, complete=True, closed=(),
                     gone=None, header=None)

    if fields is None:
        offset, number = 0, 0
        mapper = _mapper(src, keep_raw)
    elif src.harness == CLAUDE:
        offset, number = fields["offset"], fields["line"]
        mapper = _mapper(src, keep_raw, prompt_id=fields.get("prompt_id"),
                         open_key=fields.get("open_key"))
    else:
        offset, number = fields["offset"], fields["line"]
        mapper = _mapper(src, keep_raw, state=fields["state"])

    out: list[Record] = []
    taken = 0
    complete = True
    handle.seek(offset)
    while offset < end:
        data = handle.readline(end - offset)
        if not data or not data.endswith(b"\n"):
            break                                  # a line still being written
        if max_bytes is not None and taken and taken + len(data) > max_bytes:
            complete = False
            break
        number += 1
        start, offset = offset, offset + len(data)
        taken += len(data)
        out.extend(_filtered(_feed(src, mapper, data, wanted, line=number, offset=start),
                             wanted))
    out.extend(_filtered(mapper.finish(), wanted))

    yielded = {r.request_id for r in out if r.kind == "request"}
    closed = tuple(key for key in mapper.closed if key not in yielded)
    new: dict[str, Any] = {
        "v": CURSOR_VERSION, "harness": src.harness, "dev": st.st_dev, "ino": st.st_ino,
        "size": end, "offset": offset, "line": number, "complete": complete,
        "fp_len": len(head), "fp": hashlib.sha256(head).hexdigest(),
    }
    if src.harness == CLAUDE:
        new["prompt_id"] = mapper.prompt_id
        new["open_key"] = mapper.open_key
    else:
        new["state"] = mapper.state.to_json()
    return Batch(records=tuple(out), cursor=_encode(new), reset=reset, complete=complete,
                 closed=closed, gone=None,
                 header=_Once(_lazy_header(src, st.st_dev, st.st_ino, end)))


def _opencode_since(src: Source, obj: dict | None, reset: bool,
                    wanted: frozenset[str] | None, keep_raw: bool) -> Batch:
    oc = _opencode()
    old = None
    if obj is not None:
        old = oc.Cursor.from_dict(obj.get("db"))
        if old is None:
            reset = True
    since = oc.since(src.path, old, session_id=src.session_id, kinds=wanted,
                     keep_raw=keep_raw)
    header = _Once(value=opencode_header(src, since.session)) if since.read else None
    cursor = _encode({"v": CURSOR_VERSION, "harness": OPENCODE,
                      "db": since.cursor.to_dict()})
    return Batch(records=since.records, cursor=cursor, reset=reset or since.reset,
                 complete=True, closed=(), gone=since.gone, header=header)


# --- the window ----------------------------------------------------------------------------

def _same_turn(a: Any, b: Any) -> bool:
    return a is b or (known(a) and known(b) and a == b)


class _Turns:
    """Records newest first, counted into turns: a turn is a run of one `turn_id`.
    Records whose turn is unknown (a file's opening lines) join the run they fall in and
    count as no turn."""

    def __init__(self, turns: int):
        self.turns = turns
        self.counted: list[Any] = []
        self.starts: list[int] = []        # where each counted turn's records begin
        self.records: list[Record] = []
        self.current: Any = None
        self.full = False

    def take(self, record: Record) -> bool:
        """Add the record; False (and nothing added) when it opens a turn past the
        window's."""
        turn = record.turn_id
        if self.current is None or not _same_turn(turn, self.current):
            if known(turn) and not any(_same_turn(turn, t) for t in self.counted):
                if len(self.counted) == self.turns:
                    self.full = True
                    return False
                self.counted.append(turn)
                self.starts.append(len(self.records))
            self.current = turn
        self.records.append(record)
        return True

    def drop_cut_turn(self) -> None:
        """Leave out the oldest turn, which a limit may have cut, when it is not the only
        one."""
        if len(self.counted) > 1:
            del self.records[self.starts[-1]:]
            self.counted.pop()
            self.starts.pop()


def window(source: Any, *, turns: int = 5, before: Any = None,
           limit: int = WINDOW_LIMIT) -> Window:
    """The last `turns` turns of a session as records, oldest first, read backward.

    A turn starts at a Claude user line with a new `promptId`, a Codex turn start, or an
    OpenCode user message, and runs to the next. `before` (a `Window.before`) reads the
    turns before an earlier window's instead of the last ones. `limit` bounds the bytes a
    file's backward read takes. Raises Unreadable when the source cannot be read, and
    ValueError for a `before` that is not one of this source's positions.
    """
    if not isinstance(turns, int) or isinstance(turns, bool) or turns < 1:
        raise ValueError(f"turns must be 1 or more, not {turns!r}")
    src, bound = unpack(source)
    if src.harness == OPENCODE:
        return _opencode_window(src, turns, before)
    end = bound
    if before is not None:
        if not _count(before):
            raise ValueError(f"before must be a byte offset in a file, not {before!r}")
        end = before if end is None else min(end, before)
    back = _file_back(src, end=end, wanted=None, block=BLOCK, limit=limit, head=False,
                      keep_raw=False)
    taken = _Turns(turns)
    with back:
        for record in back:
            if not taken.take(record):
                break
    ended = back.ended
    if not taken.full and ended == LIMIT:
        taken.drop_cut_turn()
    has_earlier = taken.full or ended == LIMIT
    records_ = taken.records
    earliest = min((r.at["offset"] for r in records_), default=None)
    return Window(tuple(reversed(records_)), has_earlier,
                  earliest if has_earlier else None, ended, back._header)


def _opencode_window(src: Source, turns: int, before: Any) -> Window:
    oc = _opencode()
    session_id = _needs_session(src)
    if before is not None and not isinstance(before, str):
        raise ValueError(f"before must be an OpenCode message id, not {before!r}")
    taken = _Turns(turns)
    oldest = None
    with oc.Snapshot(src.path) as snap:
        row = snap.session(session_id)
        snap.require("message", "part")
        agent_id = snap._agent_id(session_id)
        skipping = before is not None
        for message in snap._messages(session_id, newest_first=True):
            if skipping:
                skipping = message[0] != before
                continue
            recs = oc.message_records(message, snap._parts(message[0]),
                                      agent_id=agent_id)
            if not all(taken.take(record) for record in reversed(recs)):
                break
            oldest = message[0]
        if skipping:
            raise ValueError(f"before names no message of session {session_id}: "
                             f"{before!r}")
    return Window(tuple(reversed(taken.records)), taken.full,
                  oldest if taken.full else None, oc.BACK_ENDED,
                  _Once(value=opencode_header(src, row)))


__all__ = [
    "CURSOR_VERSION", "FINGERPRINT_BYTES", "BLOCK", "BACK_LIMIT", "WINDOW_LIMIT",
    "Read", "Back", "LastRequest", "Batch", "Window",
    "records", "records_back", "last_request", "records_since", "window",
]
