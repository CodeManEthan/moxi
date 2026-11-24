"""`search_memory`'s search: a private variant of memory's own, run in one reader of the
index, that never lets text the chat can't see choose, place or count a hit.

**Which rows.** A row is a candidate only when its writer is `person`, `model` or
`unknown` (`SHOWN_WRITERS`), and a row with no writer never is. Its stored text, after
the opening characters, must not open with an output wrapper, and an `unknown` row's
must not open with `<` and a letter (any Unicode letter, as `str.isalpha` says) or with a
placeholder. These are clauses of the query: the opening characters are skipped with
`ltrim(text, ?)` and sessions' `OPENING` bound, less U+0000, which a bound string can't
hold, and SQL's tag test knows only the ASCII letters. A row whose text, so trimmed,
opens with U+0000, or with `<` and a character past ASCII, is judged again in Python
with sessions' `opening` and skipped when that judgment hides it (SQLite's `substr`
reads a text only up to a U+0000, so SQL's own tests see nothing there and keep the
row). Tails (with `results`) are output and take none of these
clauses. The filters of memory's own search apply: project, time, harness, subagents,
scratch, forks.

**The windows.** The rows ranked are chosen by time, which no text decides: the newest
`WINDOW` rows holding every word that still hold every word once redacted, and the
newest `WINDOW` holding any word that still hold one once redacted; a row in both is
ranked once. Each row read goes through the steps below in turn, until its window is
full or `deadline` passes, when the walk stops, ranks what it kept and says `cut`.

**Each row's steps.**

1. The text within `rows.EDGE_CHARS` of a stored cut, widened to whitespace, becomes
   `CUT_EDGE` (`rows.hide_cut_edges`).
2. The chat redaction runs over the whole result, with its step between the two passes
   (`snippet_between`) making each whitespace run one space and then cutting each local
   path to its last segment and each loopback origin to `local` (sessions' `cut_local`),
   so its last pass sees the text exactly as a snippet shows it. A text it couldn't scan
   drops the row.
3. The redacted text goes into an in-memory FTS5 table with memory's tokenizer, and the
   snippet is FTS5's `snippet()` of it for the words, with no match marks, `…` at a cut
   edge and at most `SNIPPET_TOKENS` tokens, then cut to `SNIPPET_CHARS` at a space.
   Nothing but those cuts changes the redacted text.
4. A row whose redacted text doesn't match as its window needs is dropped, so a row
   whose words matched only inside a hidden span or a cut path is dropped. The count of
   dropped rows goes to the call log only.

The rows kept are ranked by bm25 over their redacted text, in a table holding only them,
a tie going to the newer. So which rows are ranked, their order, and every count the
answer holds are the same whether a word sits in a hidden span or in no row at all.

`rows` counts the rows the filters and the writer clauses let through, words aside.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import index, roots, rows, search
from ..index import IndexUnreadable, StateUnreadable
from .redaction import PLACEHOLDER, UNSCANNED

#: The writers whose rows a search may return.
SHOWN_WRITERS = ("person", "model", "unknown")
#: How many kept rows each window holds at most.
WINDOW = 500
#: The most tokens a snippet holds, and its cut in characters.
SNIPPET_TOKENS = 64
SNIPPET_CHARS = 300
#: What stands for cut text in a snippet.
SNIPPET_CUT = "…"
#: What replaces the text near a stored cut.
CUT_EDGE = "[REDACTED:cut-edge]"


class SchemaTooOld(IndexUnreadable):
    """The index's generation is at a schema whose rows record no writer."""


@dataclass(frozen=True)
class Query:
    """What to search for: the words' tokens and the filters."""

    tokens: tuple[str, ...]
    project: str | None = None
    since: float | None = None
    harness: str | None = None
    results: bool = False
    subagents: bool = False
    scratch: bool = False
    forks: bool = False
    limit: int = 8


@dataclass(frozen=True)
class Found:
    """One hit. `session_id` is the main session's; `agent_id` the subagent's own, or
    None. `writer` is None on a `result`; `failed` is None but on a `result`."""

    harness: str
    session_id: str
    agent_id: str | None
    time: float | None
    label: str | None
    kind: str
    writer: str | None
    failed: bool | None
    snippet: str


@dataclass
class Walked:
    """A walk's answer. `notes` are memory's incomplete-answer lines, unredacted.
    `dropped` and `unscanned` are for the call log only."""

    hits: list[Found] = field(default_factory=list)
    rows: int = 0
    notes: list[str] = field(default_factory=list)
    cut: bool = False
    dropped: int = 0
    unscanned: int = 0


@dataclass(frozen=True)
class _Candidate:
    table: str
    rid: int
    harness: str
    session_id: str
    agent_id: str | None
    label: str | None
    time: float | None
    kind: str
    writer: str | None
    failed: bool | None
    text: str


@dataclass(frozen=True)
class _Read:
    """A candidate after steps 1 to 3: its redacted text and snippet, and whether every
    word and any word match that text."""

    clean: str
    snippet: str
    every: bool
    any: bool


def match_every(tokens: tuple[str, ...]) -> str:
    """The FTS5 query holding every token: each quoted, joined by FTS5's implicit AND."""
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def squeeze(text: str) -> str:
    """`text` with each whitespace run made one space and none at either end."""
    return " ".join(text.split())


def path_cut(sessions: Any, env: Mapping[str, str] | None) -> Callable[[str], str]:
    """sessions' `cut_local` with the home folder `env` names: each loopback origin made
    `local` and each local path cut to its last segment. Every string `search_memory`
    shows passes it as the chat redaction's step between its two passes."""
    return lambda text: sessions.cut_local(text, env)


def snippet_between(sessions: Any, env: Mapping[str, str] | None) -> Callable[[str], str]:
    """The chat redaction's step between its passes for a row: `squeeze`, then
    `path_cut`."""
    cut = path_cut(sessions, env)
    return lambda text: cut(squeeze(text))


def cut_snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    """`text` cut to at most `limit` characters at the last space before, `…` closing a
    cut. It only cuts, so the snippet is a slice of the text the redaction last saw."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit - len(SNIPPET_CUT)]
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).rstrip() + SNIPPET_CUT


class _Hidden:
    """The writer clauses, in SQL and, for a text that opens with U+0000, or with `<`
    and a character past ASCII, once SQL's trim is done, in Python."""

    def __init__(self, sessions: Any):
        self.sessions = sessions
        self.bound = sessions.OPENING.replace("\x00", "")
        self.wrappers = tuple(sessions.OUTPUT_WRAPPERS)

    def clause(self, alias: str) -> tuple[str, list[object]]:
        trimmed = f"ltrim({alias}.text, ?)"
        wraps = " OR ".join(f"substr({trimmed}, 1, {len(w)}) = ?" for w in self.wrappers)
        shown = ", ".join(f"'{w}'" for w in SHOWN_WRITERS)
        sql = (f"{alias}.writer IN ({shown}) AND NOT ({wraps}) AND "
               f"({alias}.writer != 'unknown' OR NOT ({trimmed} GLOB '<[A-Za-z]*' OR "
               f"substr({trimmed}, 1, {len(PLACEHOLDER)}) = ?))")
        params: list[object] = [x for w in self.wrappers for x in (self.bound, w)]
        params += [self.bound, self.bound, PLACEHOLDER]
        return sql, params

    def needs_python(self, text: str) -> bool:
        """Whether SQL's verdict on `text` is judged again: its trim stops at a U+0000,
        or the text opens with `<` and a character past ASCII, which SQL's tag test
        can't judge a letter."""
        trimmed = text.lstrip(self.bound)
        return trimmed.startswith("\x00") or (
            trimmed[:1] == "<" and len(trimmed) > 1 and not trimmed[1].isascii())

    def hides(self, writer: str | None, text: str) -> bool:
        """Python's verdict, over every opening character."""
        s = self.sessions
        if s.opens_with_output(text):
            return True
        opened = s.opening(text)
        return writer == "unknown" and (opens_with_tag(opened)
                                        or opened.startswith(PLACEHOLDER))


def opens_with_tag(opened: str) -> bool:
    """Whether `opened`, a text past its opening characters, opens with `<` and a
    letter, any Unicode letter."""
    return len(opened) > 1 and opened[0] == "<" and opened[1].isalpha()


def _filters(query: Query, *, table: str, with_state: bool,
             hidden: _Hidden) -> search._Filters:
    filters = search._Filters("r", project=query.project, since=query.since,
                              harness=query.harness, subagents=query.subagents,
                              scratch=query.scratch, forks=query.forks,
                              with_state=with_state,
                              has_agent=table == "rows")
    if table == "rows":
        sql, params = hidden.clause("r")
        filters.clauses.append(sql)
        filters.params.extend(params)
    return filters


def _candidates(conn, expr: str, filters: Mapping[str, search._Filters]):
    """Every candidate holding `expr`, rows and tails together, newest first."""
    parts, params = [], []
    for table, filt in filters.items():
        fts = f"{table}_fts"
        if table == "rows":
            cols = "r.agent_id, r.label, r.time, r.kind, r.writer, NULL"
        else:
            cols = f"NULL, r.label, r.time, '{search.RESULT}', NULL, r.failed"
        parts.append(f"SELECT '{table}', r.id, r.harness, r.session_id, {cols}, r.text "
                     f"FROM {fts} JOIN {table} AS r ON r.id = {fts}.rowid "
                     f"WHERE {fts} MATCH ? AND {filt.where()}")
        params += [expr, *filt.params]
    sql = " UNION ALL ".join(parts) + " ORDER BY 7 DESC, 1 DESC, 2 DESC"
    for (table, rid, harness, sid, agent, label, at, kind, writer, failed,
         text) in conn.execute(sql, params):
        yield _Candidate(table, rid, harness, sid, agent, label, at, kind, writer,
                         None if failed is None else bool(failed), text or "")


def _count(conn, filters: Mapping[str, search._Filters], hidden: _Hidden) -> int:
    total = sum(search._count(conn, table, filt) for table, filt in filters.items())
    rows_filter = filters["rows"]
    trimmed = "ltrim(r.text, ?)"
    for writer, text in conn.execute(
            f"SELECT r.writer, r.text FROM rows AS r WHERE {rows_filter.where()} "
            f"AND (substr(CAST({trimmed} AS BLOB), 1, 1) = x'00' OR "
            f"(substr({trimmed}, 1, 1) = '<' AND unicode(substr({trimmed}, 2, 1)) > 127))",
            [*rows_filter.params, hidden.bound, hidden.bound, hidden.bound]):
        if hidden.hides(writer, text or ""):
            total -= 1
    return total


class _Probe:
    """The in-memory FTS5 tables: `probe` holds one row at a time, for its match and
    snippet; `ranked` holds only the rows kept, for their ranking."""

    def __init__(self, every: str, any_word: str):
        self.every, self.any = every, any_word
        self.conn = sqlite3.connect(":memory:")
        for name in ("probe", "ranked"):
            self.conn.execute(f"CREATE VIRTUAL TABLE {name} USING fts5(text, "
                              f"tokenize = '{index.TOKENIZER}')")

    def read(self, clean: str) -> tuple[bool, bool, str]:
        c = self.conn
        c.execute("INSERT INTO probe (rowid, text) VALUES (1, ?)", (clean,))
        try:
            every = c.execute("SELECT 1 FROM probe WHERE probe MATCH ?",
                              (self.every,)).fetchone() is not None
            got = c.execute("SELECT snippet(probe, 0, '', '', ?, ?) FROM probe "
                            "WHERE probe MATCH ?",
                            (SNIPPET_CUT, SNIPPET_TOKENS, self.any)).fetchone()
        finally:
            c.execute("DELETE FROM probe")
        return every, got is not None, "" if got is None else str(got[0] or "")

    def rank(self, texts: list[str]) -> dict[int, float]:
        c = self.conn
        c.executemany("INSERT INTO ranked (rowid, text) VALUES (?, ?)", enumerate(texts))
        return {rid: float(score) for rid, score in c.execute(
            "SELECT rowid, bm25(ranked) FROM ranked WHERE ranked MATCH ?", (self.any,))}

    def close(self) -> None:
        self.conn.close()


class _Walk:
    def __init__(self, query: Query, redact: Callable[..., str], deadline: float,
                 clock: Callable[[], float], between: Callable[[str], str]):
        self.query = query
        self.redact = redact
        self.between = between
        self.deadline = deadline
        self.clock = clock
        self.read: dict[tuple[str, int], _Read | None] = {}
        self.kept: dict[tuple[str, int], tuple[_Candidate, _Read]] = {}
        self.cut = False
        self.unscanned = 0

    def _step(self, cand: _Candidate, probe: _Probe) -> _Read | None:
        key = (cand.table, cand.rid)
        if key in self.read:
            return self.read[key]
        text = rows.hide_cut_edges(cand.text, CUT_EDGE)
        clean = self.redact(text, between=self.between)
        if clean == UNSCANNED and text != UNSCANNED:
            self.unscanned += 1
            got = None
        else:
            every, any_word, snippet = probe.read(clean)
            got = _Read(clean, cut_snippet(snippet), every, any_word)
        self.read[key] = got
        return got

    def window(self, conn, expr: str, filters, hidden: _Hidden, probe: _Probe, *,
               every: bool) -> None:
        held = 0
        for cand in _candidates(conn, expr, filters):
            if self.clock() >= self.deadline:
                self.cut = True
                return
            if (cand.table == "rows" and hidden.needs_python(cand.text)
                    and hidden.hides(cand.writer, cand.text)):
                continue
            got = self._step(cand, probe)
            if got is None or not (got.every if every else got.any):
                continue
            self.kept.setdefault((cand.table, cand.rid), (cand, got))
            held += 1
            if held >= WINDOW:
                return

    def ranked(self, probe: _Probe) -> list[Found]:
        kept = list(self.kept.values())
        scores = probe.rank([got.clean for _, got in kept])

        def order(i: int):
            cand = kept[i][0]
            return (scores.get(i, math.inf),
                    -(cand.time if cand.time is not None else -math.inf),
                    0 if cand.table == "rows" else 1, cand.rid)

        out = []
        for i in sorted(range(len(kept)), key=order)[:self.query.limit]:
            cand, got = kept[i]
            out.append(Found(harness=cand.harness, session_id=cand.session_id,
                             agent_id=cand.agent_id, time=cand.time, label=cand.label,
                             kind=cand.kind, writer=cand.writer, failed=cand.failed,
                             snippet=got.snippet))
        return out


def _notes(conn, rebuild_at: float | None) -> list[str]:
    notes = []
    fill = search.filling(conn)
    if fill is not None:
        notes.append("memory's index is still filling: its first full read hasn't "
                     "finished, so older sessions may be missing")
    if rebuild_at is not None:
        notes.append("a rebuild of memory's index is running; this searched the index "
                     "from before it")
    unread = roots.not_read_yet(roots.read_all(conn))
    if unread:
        notes.append(f"{len(unread)} archive folder{'s' if len(unread) != 1 else ''} "
                     "haven't been read whole yet; their sessions aren't searched")
    return notes


def walk(query: Query, *, redact: Callable[[str], str], deadline: float, sessions: Any,
         env: Mapping[str, str] | None = None,
         clock: Callable[[], float] = time.monotonic) -> Walked:
    """Search memory's index for `query`, in one reader, redacting with `redact`;
    `deadline` is on `clock`'s scale. `sessions` is sessions' API at interface 7, and
    `env` names the home folder its path cut reads.

    Raises `IndexMissing` with no index, `IndexUnreadable` for one that can't be read,
    `SchemaTooOld` (an `IndexUnreadable`) for a generation whose rows record no writer,
    and `StateUnreadable` when memory's state store can't be read."""
    hidden = _Hidden(sessions)
    rebuild_at = search.rebuilding(env)
    with index.open_read(env) as reader:
        conn = reader.conn
        if not index.has_writer(conn):
            raise SchemaTooOld(f"{reader.path} is at schema "
                               f"{index.schema_version(conn)}")
        try:
            with_state = reader.attach_state(search.STATE) and search._has_sessions(conn)
        except sqlite3.DatabaseError as exc:
            from .. import state

            raise StateUnreadable(f"{state.path(env)}: {exc}") from None
        tables = ("rows", "tails") if query.results else ("rows",)
        filters = {t: _filters(query, table=t, with_state=with_state, hidden=hidden)
                   for t in tables}
        out = Walked()
        try:
            out.rows = _count(conn, filters, hidden)
            out.notes = _notes(conn, rebuild_at)
            if query.tokens:
                every, any_word = match_every(query.tokens), search.match(query.tokens)
                probe = _Probe(every, any_word)
                try:
                    job = _Walk(query, redact, deadline, clock,
                                snippet_between(sessions, env))
                    job.window(conn, every, filters, hidden, probe, every=True)
                    if len(query.tokens) > 1 and not job.cut:
                        job.window(conn, any_word, filters, hidden, probe, every=False)
                    out.hits = job.ranked(probe)
                finally:
                    probe.close()
                out.cut = job.cut
                out.unscanned = job.unscanned
                out.dropped = sum(1 for key, got in job.read.items()
                                  if got is not None and key not in job.kept)
        except sqlite3.DatabaseError as exc:
            raise IndexUnreadable(f"{reader.path}: {exc}") from None
    return out


__all__ = ["CUT_EDGE", "SHOWN_WRITERS", "WINDOW", "Found", "Query", "SchemaTooOld",
           "Walked", "cut_snippet", "match_every", "path_cut", "snippet_between",
           "squeeze", "walk"]
