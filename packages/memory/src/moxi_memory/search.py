"""Searching the index: the query, the filters, the ranking, and what a search can see.

**The query.** A query is cut into tokens the way the index's tokenizer cuts text
(`cut`): letters, digits and marks make up a token, and everything else (spaces,
punctuation, symbols, control characters) separates two. Each token is quoted, so no
character an agent types can reach FTS5's query syntax, and the tokens are joined with
OR. FTS5 stems each quoted token the way it stemmed the text, so `running` finds `runs`.
Phrase, prefix and NEAR queries are not offered.

**The ranking.** Hits are ranked by BM25, best first; a tie goes to the newer row, then to
the lower row id.

**What is searched.** Tier 1 (`rows`: main-session messages and tool calls) always; tier 2
(`tails`: the end of each tool result) only with `results`; subagent rows only with
`subagents` (they exist only when config `index.include_subagents` is on). A session whose launch
kind is `scratch` is left out of every tier unless `scratch` is asked. The kind is read
from the session table, attached read-only beside the index, so a kind learned after the
rows were written applies at the next search with no rewrite of the index.

**What a search can see that leaves its answer incomplete**, and returns beside the hits:
`filling`, while the first full read hasn't finished, and `rebuilding`, the start time of
a rebuild that is running (the search reads the generation from before it). A missing
or unreadable index raises `IndexMissing` or `IndexUnreadable`, and a state store that can't
be read raises `StateUnreadable`, a kind of `IndexUnreadable` that names the store; none
is ever an empty answer.

A search only reads. It holds the shared lock of the generation it opened until it ends,
so a rebuild that switches meanwhile leaves that generation for it to read to the end,
and it never takes the writer lock, even for a moment.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from . import index
from .index import IndexMissing, IndexUnreadable, StateUnreadable

#: The most tokens a query keeps; the rest are dropped.
MAX_TOKENS = 64
#: The most characters one token keeps.
MAX_TOKEN_CHARS = 100
#: How many tokens a snippet holds at most.
SNIPPET_TOKENS = 16
#: The marks around a matched token in a snippet, and what stands for cut text.
SNIPPET_OPEN = "«"
SNIPPET_CLOSE = "»"
SNIPPET_CUT = "…"
#: The session table's alias on a reader's connection.
STATE = "st"
#: The kind a tier 2 hit shows: the end of a tool result.
RESULT = "result"
#: The Unicode categories that separate tokens besides separators (spaces), punctuation
#: and symbols: control, format and surrogate characters. Everything else (letters,
#: digits, marks, private use) is part of a token, as in the index's tokenizer.
_SEPARATES = ("Cc", "Cf", "Cs")


@dataclass(frozen=True)
class Hit:
    """One hit.

    `tier` is 1 (a main-session message or tool call), 2 (the end of a tool result) or
    `subagent` (a subagent's message or tool call). `session_id` is always the main
    session's id; `agent_id` is the subagent's own id on a subagent hit, else None.
    `kind` is the row's kind: `user`, `assistant`, `tool`, or `result` for tier 2.
    `failed` says whether the tool failed, on a tier 2 hit (else None). `time` is
    seconds since the epoch, or None when the record had none. `snippet` is the matched
    part of the text, redacted as stored, with each match marked. `score` is BM25's
    (lower is better).
    """

    harness: str
    session_id: str
    agent_id: str | None
    time: float | None
    project: str | None
    kind: str
    tier: int | str
    snippet: str
    key: str
    score: float
    failed: bool | None = None


@dataclass(frozen=True)
class Filling:
    """The first full read is still running: `read` sources of `listed`. `listed` is
    None while no harness has been listed yet."""

    read: int
    listed: int | None


@dataclass(frozen=True)
class Result:
    """A search's answer.

    `hits`, best first. `rows`: how many rows the search looked through, after its
    filters. `tokens`: the query as searched. `filling`: a `Filling` while the first full
    read hasn't finished, else None. `rebuilding`: the start time of a rebuild that is
    running (seconds since the epoch; 0.0 when it hasn't written its time yet), else
    None. `generation`: the index generation read.
    """

    hits: tuple[Hit, ...]
    rows: int
    tokens: tuple[str, ...]
    filling: Filling | None
    rebuilding: float | None
    generation: int


# -- the query ------------------------------------------------------------------------

def _token_char(ch: str) -> bool:
    category = unicodedata.category(ch)
    return not (category[0] in "ZPS" or category in _SEPARATES)


def cut(query: str) -> tuple[str, ...]:
    """The query's tokens, as the tokenizer cuts text: in order, each kept once (case
    folded), at most `MAX_TOKENS` of them, each at most `MAX_TOKEN_CHARS` long."""
    if not isinstance(query, str):
        return ()
    tokens: list[str] = []
    seen: set[str] = set()
    word: list[str] = []

    def end() -> None:
        if word:
            token = "".join(word)[:MAX_TOKEN_CHARS]
            folded = token.casefold()
            if folded not in seen:
                seen.add(folded)
                tokens.append(token)
            word.clear()

    for ch in query:
        if _token_char(ch):
            word.append(ch)
        else:
            end()
        if len(tokens) >= MAX_TOKENS:
            break
    end()
    return tuple(tokens[:MAX_TOKENS])


def match(tokens: tuple[str, ...]) -> str:
    """The FTS5 query for `tokens`: each quoted (a quote inside doubled), OR-joined."""
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def since_seconds(since: float | int | datetime | None) -> float | None:
    """`since` as seconds since the epoch: a number as given, a datetime with no zone
    read as local time."""
    if since is None:
        return None
    if isinstance(since, datetime):
        return (since if since.tzinfo else since.astimezone()).timestamp()
    if isinstance(since, bool) or not isinstance(since, (int, float)):
        raise TypeError(f"since is {since!r}: give seconds since the epoch or a datetime")
    return float(since)


# -- the search -----------------------------------------------------------------------

class _Filters:
    """The WHERE clauses and parameters shared by the hit query and the count."""

    def __init__(self, alias: str, *, project, since, harness, subagents, scratch,
                 with_state: bool, has_agent: bool):
        self.alias = alias
        self.clauses: list[str] = []
        self.params: list[object] = []
        if project is not None:
            self.clauses.append(f"{alias}.label = ?")
            self.params.append(project)
        if harness is not None:
            self.clauses.append(f"{alias}.harness = ?")
            self.params.append(harness)
        if since is not None:
            self.clauses.append(f"{alias}.time >= ?")
            self.params.append(since)
        if has_agent and not subagents:
            self.clauses.append(f"{alias}.agent_id IS NULL")
        self.scratch_out = with_state and not scratch

    def where(self, *, scratch: bool = True) -> str:
        clauses = list(self.clauses)
        if scratch and self.scratch_out:
            clauses.append(
                f"NOT EXISTS (SELECT 1 FROM {STATE}.sessions AS s WHERE "
                f"s.harness = {self.alias}.harness AND s.session_id = {self.alias}.session_id "
                f"AND s.kind = 'scratch')")
        return " AND ".join(clauses) if clauses else "1"


def _hits(conn, table: str, expr: str, filters: _Filters, limit: int) -> list[Hit]:
    fts = f"{table}_fts"
    if table == "rows":
        extra = "r.agent_id, r.kind, NULL"
    else:
        extra = f"NULL, '{RESULT}', r.failed"
    sql = (f"SELECT r.id, r.harness, r.session_id, r.label, r.time, r.key, {extra}, "
           f"snippet({fts}, 0, ?, ?, ?, ?), bm25({fts}) AS score "
           f"FROM {fts} JOIN {table} AS r ON r.id = {fts}.rowid "
           f"WHERE {fts} MATCH ? AND {filters.where()} "
           f"ORDER BY score, r.time DESC, r.id LIMIT ?")
    params = [SNIPPET_OPEN, SNIPPET_CLOSE, SNIPPET_CUT, SNIPPET_TOKENS, expr,
              *filters.params, limit]
    found = []
    for (rid, harness, sid, label, at, key, agent, kind, failed, snippet,
         score) in conn.execute(sql, params):
        tier: int | str = 2 if table == "tails" else ("subagent" if agent else 1)
        found.append((score, -(at if at is not None else float("-inf")),
                      0 if table == "rows" else 1, rid,
                      Hit(harness=harness, session_id=sid, agent_id=agent, time=at,
                          project=label, kind=kind, tier=tier,
                          snippet=" ".join(str(snippet or "").split()), key=key,
                          score=float(score),
                          failed=None if failed is None else bool(failed))))
    return found


def _count(conn, table: str, filters: _Filters) -> int:
    """The rows `filters` let through in `table`: those its plain clauses admit, less
    those of scratch-kind sessions, counted from the session table's few scratch rows
    so the count never looks a kind up row by row."""
    total = conn.execute(f"SELECT count(*) FROM {table} AS r WHERE "
                         f"{filters.where(scratch=False)}", filters.params).fetchone()[0]
    if filters.scratch_out:
        total -= conn.execute(
            f"SELECT count(*) FROM {STATE}.sessions AS s JOIN {table} AS r "
            f"ON r.harness = s.harness AND r.session_id = s.session_id "
            f"WHERE s.kind = 'scratch' AND {filters.where(scratch=False)}",
            filters.params).fetchone()[0]
    return int(total)


def _has_sessions(conn) -> bool:
    return conn.execute(f"SELECT 1 FROM {STATE}.sqlite_master WHERE type = 'table' "
                        "AND name = 'sessions'").fetchone() is not None


def filling(conn) -> Filling | None:
    """A `Filling` while the generation's first full read hasn't finished, else None."""
    if index.meta_get(conn, "first_read") == "1":
        return None
    read = conn.execute("SELECT count(*) FROM sources").fetchone()[0]
    listed = [int(v) for (v,) in conn.execute(
        "SELECT value FROM meta WHERE key LIKE 'listed.%'") if str(v).isdigit()]
    return Filling(int(read), max(sum(listed), int(read)) if listed else None)


def rebuilding(env: Mapping[str, str] | None = None) -> float | None:
    """The start time of a rebuild that is running, or None. Tests the rebuild lock and
    lets it go at once; never the writer lock."""
    running = index.rebuild_running(env)
    if running is None:
        return None
    return running.started if running.started is not None else 0.0


def search(query: str, *, project: str | None = None,
           since: float | int | datetime | None = None, harness: str | None = None,
           results: bool = False, subagents: bool = False, scratch: bool = False,
           limit: int = 10, env: Mapping[str, str] | None = None) -> Result:
    """Search the index for `query` and return the best `limit` hits.

    `project` keeps hits of that project label, `since` those at or after that time
    (seconds since the epoch, or a datetime), `harness` those of that harness
    (`claude`, `codex` or `opencode`). `results` searches tier 2, the ends of tool
    results, too; `subagents` subagent rows too; `scratch` scratch-kind sessions too.

    Raises `IndexMissing` when there is no index, `IndexUnreadable` when there is one
    that can't be read, and `StateUnreadable` (an `IndexUnreadable`) when the index reads
    but memory's state store doesn't. A query with no token in it finds nothing.
    """
    import sqlite3

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"limit is {limit!r}: give a whole number of hits, at least 1")
    since_at = since_seconds(since)
    tokens = cut(query)
    rebuild_at = rebuilding(env)
    with index.open_read(env) as reader:
        conn = reader.conn
        try:
            with_state = reader.attach_state(STATE) and _has_sessions(conn)
        except sqlite3.DatabaseError as exc:
            from . import state

            raise StateUnreadable(f"{state.path(env)}: {exc}") from None
        try:
            tables = ("rows", "tails") if results else ("rows",)
            found = []
            rows = 0
            for table in tables:
                filters = _Filters("r", project=project, since=since_at, harness=harness,
                                   subagents=subagents, scratch=scratch,
                                   with_state=with_state, has_agent=table == "rows")
                if tokens:
                    found += _hits(conn, table, match(tokens), filters, limit)
                rows += _count(conn, table, filters)
            fill = filling(conn)
        except sqlite3.DatabaseError as exc:
            raise IndexUnreadable(f"{reader.path}: {exc}") from None
        generation = reader.generation
    found.sort(key=lambda item: item[:4])
    hits = tuple(item[-1] for item in found[:limit])
    return Result(hits=hits, rows=rows, tokens=tokens, filling=fill,
                  rebuilding=rebuild_at, generation=generation)


def stamp(epoch: float | None) -> str:
    """Seconds since the epoch as UTC `YYYY-MM-DDTHH:MM:SSZ`, the form
    `moxi sessions read --around` takes back; `-` for none."""
    if epoch is None:
        return "-"
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


__all__ = ["MAX_TOKENS", "Hit", "Filling", "Result", "IndexMissing", "IndexUnreadable",
           "StateUnreadable", "cut", "match", "since_seconds", "filling", "rebuilding",
           "search", "stamp"]
