"""OpenCode's database: how its rows map into the one shape, read from one snapshot.

OpenCode keeps its sessions in one SQLite database (`opencode.db`, in WAL mode): a
`session` row per session, a `message` row per user or assistant message, and a `part`
row per piece of a message (its text, a thought, a tool call, the start and finish of each
model step). This module owns how those rows are read and what records they make. Which
records count for anything is the caller's.

**The connection.** `connect` opens the database read only (`mode=ro`), with a 200 ms
busy timeout and `isolation_level=None`, so the reader issues `BEGIN` and `COMMIT` itself.
It never opens with `immutable=1`, which would read a live WAL database wrong. Its
authorizer (`authorize`) allows exactly: `SELECT`; reads of `session`, `message` and
`part`; `PRAGMA table_info` on those three; `BEGIN`, `COMMIT` and `ROLLBACK`; and the
function `count`. It denies everything else, because the database also holds accounts and
credentials, which sessions has no reason to read. A later reader that needs another table
or function adds it to `TABLES` or `FUNCTIONS`, and the test kit runs every statement the
reader runs under the authorizer.

**One snapshot.** Every read runs inside one read transaction (`Snapshot`), so a session's
row and all its messages and parts come from the same state of the database, and checks
the columns it reads (`COLUMNS`) before it reads them.

**Order.** Records of one session come with its messages by `(time_created, id)` and each
message's parts by `id`, the order OpenCode's own indexes give. A record's `at` is
`{"table": t, "id": id}`, the row's own `id`, never its rowid: SQLite hands a rowid out
again once the rows after it are deleted.

**The mapping** (`message_records`):

* a user message is a **message** with `model` (the `providerID` and `modelID` it asked
  for, as written) and its text parts joined into `text`;
* an assistant message is a **message**, and each model step in it is one **request**: a
  `step-start` part opens it (`start_time`), a `step-finish` part closes it (`time`, the
  tokens, `cost_usd`). Its `request_id` is the `step-start` part's id, so it stays the same
  once the step finishes; a step with no `step-start` takes its `step-finish` part's id. A
  step still open has `final` false and unknown tokens. The message's own creation time
  rides on each of its requests as `extra["message_time"]`;
* OpenCode counts five token classes apart (records of its totals add all five), so
  `input_tokens` is `input + cache.read + cache.write` and `output_tokens` is `output +
  reasoning`, with `reasoning_output_tokens` the reasoning alone;
* a `tool` part is a **tool_call**, and also a **tool_result** once its state holds an
  output or an error; a `reasoning` part is a message with `reasoning` true;
* an assistant message by the `compaction` agent, or with `summary` true, also gives a
  **marker** `compaction`;
* any other part is **other**, `recognised` when its type is in `KNOWN_PART_TYPES`; a row
  whose `data` is not a JSON object is **malformed**.

The `agent`, `mode`, `finish` and `error` an assistant message carries stay in the
message's `extra`, and its booleans (`summary`) in `flags`. Records of a child session (a
subagent's) carry the child's id as `agent_id` as well as `session_id`.

**The forward cursor** (`since`, `Cursor`). The rule: the cursor never depends on a value
SQLite may reuse, and a read returns, whole, every message whose row or any of whose parts
is new or changed since the cursor, and no other message. OpenCode stamps `time_updated`
on every insert and update, so the cursor keeps the highest `time_updated` read over
messages and parts, the ids of the rows stamped exactly that, and the highest message
`id` read (OpenCode's message ids grow with time). A row stamped from a clock stepped back
past the cursor is not seen until it changes again, unless it is a new message by `id`.
`gone` counts the message rows read earlier that are no longer there.

sqlite3 is imported on first use, so importing this module costs nothing until a read.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import Unreadable
from .shape import (BAD_STRUCTURE, NOT_OBJECT, OPENCODE, UNDECODABLE, UNKNOWN, Malformed,
                    Marker, Message, Other, Record, Request, ToolCall, ToolResult,
                    tool_summary)

#: The OpenCode versions whose rows the mapping was checked against.
CHECKED_VERSIONS = ("1.18.34",)

#: The tables the reader reads, the only ones its authorizer lets it read.
TABLES = ("session", "message", "part")

#: The SQL functions the authorizer lets the reader call.
FUNCTIONS = frozenset({"count"})

#: The transaction statements the authorizer lets the reader issue.
TRANSACTIONS = frozenset({"BEGIN", "COMMIT", "ROLLBACK"})

#: The columns the reader reads from each table, checked before every read of it.
COLUMNS: Mapping[str, tuple[str, ...]] = {
    "session": ("id", "parent_id", "title", "directory", "version", "model", "agent",
                "revert", "tokens_input", "tokens_output", "tokens_reasoning",
                "tokens_cache_read", "tokens_cache_write", "cost", "time_created",
                "time_updated", "time_compacting"),
    "message": ("id", "session_id", "time_created", "time_updated", "data"),
    "part": ("id", "message_id", "session_id", "time_created", "time_updated", "data"),
}

#: The session row's token and cost totals, as OpenCode adds them up.
TOTALS = ("tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read",
          "tokens_cache_write", "cost")

#: The busy timeout, so a read waits briefly on OpenCode's own writer instead of failing.
BUSY_MS = 200

#: The part types OpenCode 1.18.34 writes.
KNOWN_PART_TYPES = frozenset({"text", "reasoning", "tool", "step-start", "step-finish",
                              "file", "agent", "subtask", "snapshot", "patch", "retry",
                              "compaction"})

#: How a read back over a session ends: it always reaches the session's first record, as
#: a file read back to its first line ends `start`.
BACK_ENDED = "start"

#: Ids handed to one `IN (...)` list at most.
_CHUNK = 500


class MissingSession(Unreadable):
    """The database was read and holds no row for the session."""

    def __init__(self, session_id: str, path: str | Path):
        super().__init__(f"no session {session_id} in {path}")
        self.session_id = session_id


def _sqlite():
    import sqlite3
    return sqlite3


# --- the connection ---------------------------------------------------------------------

def authorize(action: int, arg1: str | None, arg2: str | None, _db: str | None,
              _source: str | None) -> int:
    """The authorizer every connection runs under: the reads, pragmas, transaction
    statements and functions listed in the module docstring, and nothing else."""
    sqlite3 = _sqlite()
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and arg1 in TABLES:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and arg1 == "table_info" and arg2 in TABLES:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_TRANSACTION and arg1 in TRANSACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and arg2 in FUNCTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def connect(path: str | Path):
    """The database at `path`, read only, with the busy timeout and the authorizer, in
    autocommit so the caller issues its own `BEGIN`. Raises Unreadable."""
    sqlite3 = _sqlite()
    path = Path(path)
    if not path.is_file():
        raise Unreadable(f"the OpenCode database {path} is missing")
    if not os.access(path, os.R_OK):
        raise Unreadable(f"the OpenCode database {path} cannot be read")
    try:
        conn = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True,
                               timeout=BUSY_MS / 1000, isolation_level=None)
    except sqlite3.Error as e:
        raise Unreadable(f"the OpenCode database {path} could not be opened ({e})") from e
    conn.set_authorizer(authorize)
    return conn


# --- rows into records ------------------------------------------------------------------

def _seconds(ms: Any) -> Any:
    """Epoch milliseconds as written, in epoch seconds; else `UNKNOWN`."""
    if isinstance(ms, (int, float)) and not isinstance(ms, bool):
        return ms / 1000
    return UNKNOWN


def _count(obj: Any, key: str) -> Any:
    """A token count written under `key`, else `UNKNOWN`."""
    value = obj.get(key) if isinstance(obj, dict) else None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return UNKNOWN


def _number(obj: Any, key: str) -> Any:
    value = obj.get(key) if isinstance(obj, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return UNKNOWN


def _text(obj: dict, key: str) -> Any:
    """A string written under `key`; None for a null; else `UNKNOWN`."""
    value = obj.get(key, UNKNOWN)
    return value if value is None or isinstance(value, str) else UNKNOWN


def _sum(*values: Any) -> Any:
    return sum(values) if UNKNOWN not in values else UNKNOWN


def _decode(raw: Any) -> tuple[dict | None, str | None]:
    """A row's `data` as `(object, None)`, or `(None, why)`."""
    if not isinstance(raw, (str, bytes)):
        return None, UNDECODABLE
    try:
        obj = json.loads(raw)
    except ValueError:
        return None, UNDECODABLE
    if not isinstance(obj, dict):
        return None, NOT_OBJECT
    return obj, None


def _bytes(raw: Any) -> bytes:
    if isinstance(raw, bytes):
        return raw
    return str(raw).encode("utf-8", "replace") if raw is not None else b""


def _row(columns: tuple[str, ...], row: tuple, data: Any) -> dict:
    """A row as `raw`: its columns by name, `data` decoded where it decodes."""
    out = dict(zip(columns, row))
    out["data"] = data
    return out


#: The keys of a message's `data` the mapping names, by role; the rest go to `extra`.
_NAMED_MESSAGE = {
    "user": frozenset({"role", "time", "model"}),
    "assistant": frozenset({"role", "time", "parentID", "modelID", "providerID", "tokens",
                            "cost"}),
}
_NAMED_STEP = frozenset({"type", "tokens", "cost"})
_NAMED_PART = frozenset({"type", "text", "time", "tool", "callID", "state"})


def _step(message: dict, start: tuple | None, finish: tuple | None,
          keep_raw: bool) -> Request:
    """One model step's request. `start` and `finish` are `(row, data)` of its
    `step-start` and `step-finish` parts; one of them is set."""
    own_row, own = finish if finish is not None else start
    tokens = own.get("tokens") if finish is not None else None
    cache = tokens.get("cache") if isinstance(tokens, dict) else None
    fresh, output = _count(tokens, "input"), _count(tokens, "output")
    reasoning = _count(tokens, "reasoning")
    read, written = _count(cache, "read"), _count(cache, "write")
    extra = {key: value for key, value in own.items() if key not in _NAMED_STEP}
    extra["message_time"] = message["time"]
    return Request(
        harness=OPENCODE, session_id=message["session_id"], agent_id=message["agent_id"],
        at={"table": "part", "id": own_row[0]},
        time=_seconds(finish[0][3]) if finish is not None else UNKNOWN,
        turn_id=message["turn_id"],
        raw_kind=f"part/{own.get('type')}",
        extra=extra,
        raw=_row(COLUMNS["part"], own_row, own) if keep_raw else None,
        request_id=(start or finish)[0][0],
        model=message["model"],
        provider=message["provider"],
        input_tokens=_sum(fresh, read, written),
        cache_read_input_tokens=read,
        cache_creation_input_tokens=written,
        output_tokens=_sum(output, reasoning),
        reasoning_output_tokens=reasoning,
        cost_usd=_number(own, "cost") if finish is not None else UNKNOWN,
        start_time=_seconds(start[0][3]) if start is not None else UNKNOWN,
        sidechain=False,
        synthetic=False,
        final=finish is not None,
    )


def message_records(message: tuple, parts: Iterable[tuple], *, agent_id: str | None = None,
                    keep_raw: bool = False) -> list[Record]:
    """One message row and its part rows as records, in order: the message, then its
    parts' records by part `id`, a step's request where the step finishes (an open step's
    at the end), and a compaction marker last.

    `message` is a row of `COLUMNS["message"]`, `parts` rows of `COLUMNS["part"]` in `id`
    order. `agent_id` is the subagent's id for a child session's rows. A message whose
    `data` is not an object is one malformed record standing for the message and its text;
    its other parts still map.
    """
    mid, session_id, created, _updated, raw = message
    parts = list(parts)
    data, why = _decode(raw)
    at = {"table": "message", "id": mid}
    common = {"harness": OPENCODE, "session_id": session_id, "agent_id": agent_id,
              "at": at, "time": _seconds(created)}
    out: list[Record] = []
    role = data.get("role") if data is not None else None
    if data is None:
        out.append(Malformed(**common, raw_kind="message", raw=_bytes(raw), why=why))
        data = {}
    elif not isinstance(role, str):
        out.append(Malformed(**common, raw_kind="message", raw=_bytes(raw),
                             why=BAD_STRUCTURE))
    turn_id = mid if role == "user" else _text(data, "parentID")
    if turn_id is None:
        turn_id = UNKNOWN
    context = {"session_id": session_id, "agent_id": agent_id, "turn_id": turn_id,
               "time": common["time"],
               "model": _text(data, "modelID"), "provider": _text(data, "providerID")}

    texts: list[str] = []
    kinds: list[str] = []
    after: list[Record] = []
    open_step: tuple | None = None
    for prow in parts:
        pdata, pwhy = _decode(prow[5])
        pcommon = {"harness": OPENCODE, "session_id": session_id, "agent_id": agent_id,
                   "at": {"table": "part", "id": prow[0]}, "time": _seconds(prow[3]),
                   "turn_id": turn_id}
        if pdata is None:
            after.append(Malformed(**pcommon, raw_kind="part", raw=_bytes(prow[5]),
                                   why=pwhy))
            continue
        ptype = pdata.get("type")
        if not isinstance(ptype, str):
            after.append(Malformed(**pcommon, raw_kind="part", raw=_bytes(prow[5]),
                                   why=BAD_STRUCTURE))
            continue
        kinds.append(ptype)
        pcommon["raw_kind"] = f"part/{ptype}"
        pcommon["extra"] = {key: value for key, value in pdata.items()
                            if key not in _NAMED_PART}
        pcommon["raw"] = _row(COLUMNS["part"], prow, pdata) if keep_raw else None
        if ptype == "text":
            if isinstance(pdata.get("text"), str):
                texts.append(pdata["text"])
        elif ptype == "reasoning":
            thought = pdata.get("text")
            after.append(Message(**pcommon, role=role if isinstance(role, str) else UNKNOWN,
                                 reasoning=True,
                                 text=thought if isinstance(thought, str) else "",
                                 content_kinds=(ptype,), synthetic=False))
        elif ptype == "tool":
            state = pdata.get("state")
            if not isinstance(state, dict):
                after.append(Malformed(**{**pcommon, "raw": _bytes(prow[5])},
                                       why=BAD_STRUCTURE))
                continue
            name, tool_input = _text(pdata, "tool"), state.get("input")
            call_id = _text(pdata, "callID")
            after.append(ToolCall(**pcommon, call_id=call_id, name=name, input=tool_input,
                                  summary=tool_summary(name, tool_input)))
            output = state.get("output")
            if not isinstance(output, str) and state.get("status") == "error":
                output = state.get("error")
            if isinstance(output, str):
                ended = state.get("time")
                after.append(ToolResult(
                    **{**pcommon, "time": _seconds(ended.get("end"))
                       if isinstance(ended, dict) else UNKNOWN},
                    call_id=call_id, output=output,
                    is_error=state.get("status") == "error",
                    size_bytes=len(output.encode("utf-8", "replace"))))
        elif ptype == "step-start":
            if open_step is not None:
                after.append(_step(context, open_step, None, keep_raw))
            open_step = (prow, pdata)
        elif ptype == "step-finish":
            after.append(_step(context, open_step, (prow, pdata), keep_raw))
            open_step = None
        else:
            after.append(Other(**pcommon, recognised=ptype in KNOWN_PART_TYPES))
    if open_step is not None:
        after.append(_step(context, open_step, None, keep_raw))

    if isinstance(role, str):
        named = _NAMED_MESSAGE.get(role, frozenset({"role"}))
        flags = {key: value for key, value in data.items() if isinstance(value, bool)}
        mcommon = {**common, "turn_id": turn_id, "raw_kind": f"message/{role}",
                   "extra": {key: value for key, value in data.items()
                             if key not in named and key not in flags},
                   "raw": _row(COLUMNS["message"], message, data) if keep_raw else None}
        if role in ("user", "assistant"):
            out.append(Message(**mcommon, role=role, text="\n".join(texts),
                               content_kinds=tuple(kinds),
                               model=data["model"] if role == "user" and "model" in data
                               else UNKNOWN,
                               flags=flags, synthetic=False))
        else:
            out.append(Other(**mcommon, recognised=False))
    out.extend(after)
    if role == "assistant" and (data.get("agent") == "compaction"
                                or data.get("summary") is True):
        out.append(Marker(**common, turn_id=turn_id, raw_kind="message/assistant",
                          marker="compaction",
                          fields={"trigger": UNKNOWN, "pre_tokens": UNKNOWN}))
    return out


# --- the session row --------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True, slots=True)
class SessionRow:
    """A `session` row: the session's facts as OpenCode wrote them.

    Times are epoch seconds (OpenCode writes milliseconds), None where the column is
    null. `model` is the column decoded from JSON (OpenCode writes `{"id", "providerID",
    "variant"}`), None for a null and `UNKNOWN` for text that is not JSON.
    `revert_pending` is true while a revert is waiting to be applied. The totals are
    OpenCode's own sums over the session.
    """

    id: str
    parent_id: str | None
    title: Any
    directory: Any
    version: Any
    model: Any
    agent: Any
    revert_pending: bool
    tokens_input: Any
    tokens_output: Any
    tokens_reasoning: Any
    tokens_cache_read: Any
    tokens_cache_write: Any
    cost: Any
    time_created: Any
    time_updated: Any
    time_compacting: Any


def _session_row(row: tuple) -> SessionRow:
    values = dict(zip(COLUMNS["session"], row))
    model = values["model"]
    if isinstance(model, str):
        try:
            model = json.loads(model)
        except ValueError:
            model = UNKNOWN

    def when(key: str) -> Any:
        return None if values[key] is None else _seconds(values[key])

    return SessionRow(
        id=values["id"], parent_id=values["parent_id"], title=values["title"],
        directory=values["directory"], version=values["version"], model=model,
        agent=values["agent"], revert_pending=values["revert"] is not None,
        **{key: values[key] for key in TOTALS},
        time_created=when("time_created"), time_updated=when("time_updated"),
        time_compacting=when("time_compacting"))


@dataclass(frozen=True, kw_only=True, slots=True)
class Child:
    """A child session, a subagent's, as its parent's listing names it.

    `agent_id` and `session_id` are both the child's session id. `agent_type` is the
    child's `agent` and `description` its title. `tool_use_id` is the call id of the
    parent's `task` tool part whose metadata names the child, else None. `spawn_depth` is
    1 for a child of a top-level session; `parent_agent_id` is the parent's id when the
    parent is itself a child, else None.
    """

    agent_id: str
    session_id: str
    agent_type: Any
    description: Any
    tool_use_id: str | None
    spawn_depth: int
    parent_agent_id: str | None
    row: SessionRow


# --- one snapshot -----------------------------------------------------------------------

def _chunks(items: list, size: int = _CHUNK) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _marks(count: int) -> str:
    return ", ".join("?" * count)


class Snapshot:
    """One read transaction on the database: every query through it sees one state.

        with Snapshot(path) as snap:
            row = snap.session(session_id)
            records = list(snap.records(session_id))

    Each read checks the columns it reads first (`require`). Raises Unreadable from any
    read that fails, the connection included. Closing ends the transaction.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn = connect(self.path)
        self._checked: set[str] = set()
        self._parents: dict[str, str | None] = {}
        try:
            self.query("BEGIN")
        except Unreadable:
            self.conn.close()
            raise

    def __enter__(self) -> Snapshot:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        """End the read transaction and close the connection."""
        if self.conn is None:
            return
        sqlite3 = _sqlite()
        try:
            if self.conn.in_transaction:
                self.conn.execute("COMMIT")
        except sqlite3.Error:
            pass
        finally:
            self.conn.close()
            self.conn = None

    def query(self, sql: str, args: Iterable[Any] = ()) -> list[tuple]:
        sqlite3 = _sqlite()
        try:
            return self.conn.execute(sql, tuple(args)).fetchall()
        except sqlite3.Error as e:
            raise Unreadable(f"the OpenCode database {self.path} could not be read "
                             f"({e})") from e

    def rows(self, sql: str, args: Iterable[Any] = ()) -> Iterator[tuple]:
        """The query's rows one at a time, so a long result is never held whole."""
        sqlite3 = _sqlite()
        try:
            cursor = self.conn.execute(sql, tuple(args))
            while (row := cursor.fetchone()) is not None:
                yield row
        except sqlite3.Error as e:
            raise Unreadable(f"the OpenCode database {self.path} could not be read "
                             f"({e})") from e

    def require(self, *tables: str) -> None:
        """Each column the reader reads from `tables` exists. Raises Unreadable naming
        the first table or column that does not."""
        for table in tables:
            if table in self._checked:
                continue
            have = {row[1] for row in self.query(f"PRAGMA table_info({table})")}
            if not have:
                raise Unreadable(f"the OpenCode database has no `{table}` table")
            missing = [column for column in COLUMNS[table] if column not in have]
            if missing:
                raise Unreadable(f"the OpenCode database's `{table}` table has no "
                                 f"`{missing[0]}` column")
            self._checked.add(table)

    # -- sessions --------------------------------------------------------------------

    def has_session(self, session_id: str) -> bool:
        self.require("session")
        return bool(self.query("SELECT id FROM session WHERE id = ?", (session_id,)))

    def session(self, session_id: str) -> SessionRow:
        """The session's row. Raises MissingSession when there is none."""
        self.require("session")
        rows = self.query(f"SELECT {', '.join(COLUMNS['session'])} FROM session "
                          "WHERE id = ?", (session_id,))
        if not rows:
            raise MissingSession(session_id, self.path)
        row = _session_row(rows[0])
        self._parents[row.id] = row.parent_id
        return row

    def sessions(self, *, updated_since: float | None = None) -> list[SessionRow]:
        """Every session row, oldest change first; with `updated_since` (epoch seconds)
        only those changed after it."""
        self.require("session")
        sql = f"SELECT {', '.join(COLUMNS['session'])} FROM session"
        args: tuple = ()
        if updated_since is not None:
            sql += " WHERE time_updated > ?"
            args = (updated_since * 1000,)
        rows = [_session_row(row) for row in self.query(sql + " ORDER BY time_updated, id",
                                                        args)]
        self._parents.update((row.id, row.parent_id) for row in rows)
        return rows

    def _agent_id(self, session_id: str) -> str | None:
        """The session's id when it is a child session, else None."""
        if session_id not in self._parents:
            self.require("session")
            rows = self.query("SELECT parent_id FROM session WHERE id = ?", (session_id,))
            self._parents[session_id] = rows[0][0] if rows else None
        return session_id if self._parents[session_id] is not None else None

    def _load_parents(self, session_ids: Iterable[str]) -> None:
        wanted = sorted(set(session_ids) - set(self._parents))
        if not wanted:
            return
        self.require("session")
        for chunk in _chunks(wanted):
            for sid, parent in self.query(
                    f"SELECT id, parent_id FROM session WHERE id IN ({_marks(len(chunk))})",
                    chunk):
                self._parents[sid] = parent
        for sid in wanted:
            self._parents.setdefault(sid, None)

    def children(self, session_id: str) -> list[Child]:
        """The session's child sessions, oldest first. An empty list means the database
        was read and holds none."""
        self.require("session", "part")
        cols = ", ".join(COLUMNS["session"])
        rows = [_session_row(row) for row in self.query(
            f"SELECT {cols} FROM session WHERE parent_id = ? ORDER BY time_created, id",
            (session_id,))]
        if not rows:
            return []
        depth, parent = self._depth(session_id)
        calls = self._task_calls(session_id)
        return [Child(agent_id=row.id, session_id=row.id, agent_type=row.agent,
                      description=row.title, tool_use_id=calls.get(row.id),
                      spawn_depth=depth + 1,
                      parent_agent_id=session_id if parent is not None else None,
                      row=row)
                for row in rows]

    def _depth(self, session_id: str) -> tuple[int, str | None]:
        """How many parents above the session, and its own parent."""
        depth, seen, current, first = 0, {session_id}, session_id, UNKNOWN
        while True:
            rows = self.query("SELECT parent_id FROM session WHERE id = ?", (current,))
            parent = rows[0][0] if rows else None
            if first is UNKNOWN:
                first = parent
            if parent is None or parent in seen:
                return depth, first
            depth += 1
            seen.add(parent)
            current = parent

    def _task_calls(self, session_id: str) -> dict[str, str]:
        """Child session id to the call id of the parent's tool part that names it in
        `state.metadata.sessionId`."""
        out: dict[str, str] = {}
        for (raw,) in self.query("SELECT data FROM part WHERE session_id = ? ORDER BY id",
                                 (session_id,)):
            data, _why = _decode(raw)
            if data is None or data.get("type") != "tool":
                continue
            state = data.get("state")
            meta = state.get("metadata") if isinstance(state, dict) else None
            child = meta.get("sessionId") if isinstance(meta, dict) else None
            if isinstance(child, str) and isinstance(data.get("callID"), str):
                out.setdefault(child, data["callID"])
        return out

    # -- records ---------------------------------------------------------------------

    def _parts(self, message_id: str) -> list[tuple]:
        return self.query(f"SELECT {', '.join(COLUMNS['part'])} FROM part "
                          "WHERE message_id = ? ORDER BY id", (message_id,))

    def _messages(self, session_id: str, *, newest_first: bool = False) -> Iterator[tuple]:
        order = "DESC" if newest_first else ""
        return self.rows(f"SELECT {', '.join(COLUMNS['message'])} FROM message "
                          f"WHERE session_id = ? ORDER BY time_created {order}, id {order}",
                          (session_id,))

    def records(self, session_id: str, *, kinds: Iterable[str] | None = None,
                keep_raw: bool = False) -> Iterator[Record]:
        """Every record of the session, forward, one message and its parts held at a
        time. With `kinds`, only records of those kinds."""
        self.require("message", "part")
        wanted = frozenset(kinds) if kinds is not None else None
        agent_id = self._agent_id(session_id)
        for message in self._messages(session_id):
            for record in message_records(message, self._parts(message[0]),
                                          agent_id=agent_id, keep_raw=keep_raw):
                if wanted is None or record.kind in wanted:
                    yield record

    def records_back(self, session_id: str, *, kinds: Iterable[str] | None = None,
                     keep_raw: bool = False) -> list[Record]:
        """Every record of the session, newest first: the exact reverse of `records`.
        The read always reaches the session's first record (`BACK_ENDED`)."""
        out = list(self.records(session_id, kinds=kinds, keep_raw=keep_raw))
        out.reverse()
        return out

    def last_request(self, session_id: str, *, keep_raw: bool = False) -> Request | None:
        """The session's newest request whose step has finished, else None. A step still
        open has no tokens yet, so it is passed over. Reads messages newest first and
        stops at the first that has one."""
        self.require("message", "part")
        agent_id = self._agent_id(session_id)
        for message in self._messages(session_id, newest_first=True):
            data, _why = _decode(message[4])
            if data is None or data.get("role") != "assistant":
                continue
            found = [record for record in message_records(
                message, self._parts(message[0]), agent_id=agent_id, keep_raw=keep_raw)
                if record.kind == "request" and record.final]
            if found:
                return found[-1]
        return None


# --- the forward cursor -----------------------------------------------------------------

def _stat(path: Path) -> tuple[int, int, int]:
    """(ino, size, mtime_ns) of the database. Raises Unreadable."""
    try:
        st = os.stat(path)
    except OSError as e:
        raise Unreadable(f"the OpenCode database {path} cannot be read ({e.strerror})") \
            from e
    return st.st_ino, st.st_size, st.st_mtime_ns


def _stat_wal(path: Path) -> tuple[int, int] | None:
    try:
        st = os.stat(f"{path}-wal")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Unreadable(f"the OpenCode database's write-ahead log beside {path} cannot "
                         f"be read ({e.strerror})") from e
    return st.st_size, st.st_mtime_ns


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, kw_only=True, slots=True)
class Cursor:
    """Where a forward read of the database (or of one session in it) got to.

    * `ino`, `size`, `mtime_ns`: the database file's, and `wal` the size and `mtime_ns`
      of its `-wal` file (None when it had none), all taken before the read; when none of
      them has changed, nothing has, and the next read reads nothing.
    * `t`: the highest `time_updated` read over messages and parts, None before any row;
      `t_messages` and `t_parts` the ids of the rows read that are stamped exactly `t`.
    * `m`: the highest message `id` read, and `count` the number of message rows with
      `id <= m` at that read.
    * `session`: the session the cursor was read for, or None for the whole database.
    """

    ino: int
    size: int
    mtime_ns: int
    wal: tuple[int, int] | None
    t: int | None
    t_messages: tuple[str, ...]
    t_parts: tuple[str, ...]
    m: str | None
    count: int
    session: str | None

    def to_dict(self) -> dict[str, Any]:
        """The cursor as JSON-ready data."""
        return {"ino": self.ino, "size": self.size, "mtime_ns": self.mtime_ns,
                "wal": list(self.wal) if self.wal is not None else None, "t": self.t,
                "t_messages": list(self.t_messages), "t_parts": list(self.t_parts),
                "m": self.m, "count": self.count, "session": self.session}

    @classmethod
    def from_dict(cls, obj: Any) -> Cursor | None:
        """A cursor back from `to_dict`'s data, or None when a field is missing or not
        of its type, so a cursor the reader cannot trust restarts the read."""
        if not isinstance(obj, dict):
            return None
        wal = obj.get("wal")
        ok = (all(_is_int(obj.get(key)) for key in ("ino", "size", "mtime_ns", "count"))
              and (wal is None or (isinstance(wal, list) and len(wal) == 2
                                   and all(_is_int(v) for v in wal)))
              and (obj.get("t") is None or _is_int(obj.get("t")))
              and all(isinstance(obj.get(key), list)
                      and all(isinstance(v, str) for v in obj[key])
                      for key in ("t_messages", "t_parts"))
              and (obj.get("m") is None or isinstance(obj.get("m"), str))
              and (obj.get("session") is None or isinstance(obj.get("session"), str))
              and "t" in obj and "m" in obj and "session" in obj and "wal" in obj)
        if not ok:
            return None
        return cls(ino=obj["ino"], size=obj["size"], mtime_ns=obj["mtime_ns"],
                   wal=tuple(wal) if wal is not None else None, t=obj["t"],
                   t_messages=tuple(obj["t_messages"]), t_parts=tuple(obj["t_parts"]),
                   m=obj["m"], count=obj["count"], session=obj["session"])


@dataclass(frozen=True, kw_only=True, slots=True)
class Since:
    """One forward read: the `records` of every message new or changed since the cursor,
    whole, in order; the new `cursor`; `reset` (true when the cursor handed in was for
    another database file or another session, so the read started over and the caller
    drops what it holds); `gone`, at least how many message rows read earlier are no
    longer there; `read`, false when the files showed no change and nothing was read; and
    `session`, the session's row when the read was for one session and read it (None when
    nothing was read or the row is gone)."""

    records: tuple[Record, ...]
    cursor: Cursor
    reset: bool
    gone: int
    read: bool
    session: SessionRow | None


def since(path: str | Path, cursor: Cursor | None = None, *, session_id: str | None = None,
          kinds: Iterable[str] | None = None, keep_raw: bool = False) -> Since:
    """The records new or changed since `cursor`, for the whole database or one session.

    With no cursor, every message is read. A cursor for another database file (its
    `ino`) or another session is dropped and the read starts over with `reset` true. When
    the database and its `-wal` file have the same sizes and modification times as the
    cursor saw, nothing is read. Otherwise one read transaction selects every message
    whose row is stamped after the cursor's `t` (or at `t` and not read then), every
    message with an `id` above its `m`, and every message owning a part that meets the
    same time test, and reads each whole. Raises Unreadable.
    """
    path = Path(path)
    ino, size, mtime_ns = _stat(path)
    wal = _stat_wal(path)
    reset = False
    if cursor is not None and (cursor.ino != ino or cursor.session != session_id):
        cursor, reset = None, True
    if (cursor is not None and (cursor.size, cursor.mtime_ns, cursor.wal)
            == (size, mtime_ns, wal)):
        return Since(records=(), cursor=cursor, reset=False, gone=0, read=False,
                     session=None)
    wanted = frozenset(kinds) if kinds is not None else None
    scope = " AND session_id = ?" if session_id is not None else ""
    scoped: tuple = (session_id,) if session_id is not None else ()
    with Snapshot(path) as snap:
        snap.require("message", "part")
        row = None
        if session_id is not None:
            try:
                row = snap.session(session_id)
            except MissingSession:
                row = None
        cols = ", ".join(COLUMNS["message"])
        if cursor is None or (cursor.t is None and cursor.m is None):
            messages = snap.query(f"SELECT {cols} FROM message WHERE 1{scope}", scoped)
        else:
            messages = _changed(snap, cursor, cols, scope, scoped)
        messages.sort(key=lambda message: (message[2], message[0]))
        snap._load_parents(message[1] for message in messages)

        records: list[Record] = []
        t = cursor.t if cursor is not None else None
        stamped: dict[str, set[str]] = {"message": set(), "part": set()}
        if cursor is not None:
            stamped["message"].update(cursor.t_messages)
            stamped["part"].update(cursor.t_parts)
        m = cursor.m if cursor is not None else None

        def stamp(table: str, rid: str, updated: Any) -> None:
            nonlocal t
            if not _is_int(updated):
                return
            if t is None or updated > t:
                t = updated
                stamped["message"].clear()
                stamped["part"].clear()
            if updated == t:
                stamped[table].add(rid)

        for message in messages:
            parts = snap._parts(message[0])
            stamp("message", message[0], message[3])
            for part in parts:
                stamp("part", part[0], part[4])
            if m is None or message[0] > m:
                m = message[0]
            agent_id = message[1] if snap._parents.get(message[1]) is not None else None
            for record in message_records(message, parts, agent_id=agent_id,
                                          keep_raw=keep_raw):
                if wanted is None or record.kind in wanted:
                    records.append(record)

        gone = 0
        if cursor is not None and cursor.m is not None:
            left = snap.query(f"SELECT count(*) FROM message WHERE id <= ?{scope}",
                              (cursor.m, *scoped))[0][0]
            gone = max(0, cursor.count - left)
        count = 0
        if m is not None:
            count = snap.query(f"SELECT count(*) FROM message WHERE id <= ?{scope}",
                               (m, *scoped))[0][0]
    new = Cursor(ino=ino, size=size, mtime_ns=mtime_ns, wal=wal, t=t,
                 t_messages=tuple(sorted(stamped["message"])),
                 t_parts=tuple(sorted(stamped["part"])), m=m, count=count,
                 session=session_id)
    return Since(records=tuple(records), cursor=new, reset=reset, gone=gone, read=True,
                 session=row)


def _changed(snap: Snapshot, cursor: Cursor, cols: str, scope: str,
             scoped: tuple) -> list[tuple]:
    """The message rows the cursor has not seen in their current state: their own row
    changed or new, or one of their parts changed or new."""
    t = cursor.t if cursor.t is not None else -1
    time_test = (f"(time_updated > ? OR (time_updated = ? AND id NOT IN "
                 f"({_marks(len(cursor.t_messages))})))")
    clauses = [time_test]
    args: list[Any] = [t, t, *cursor.t_messages]
    if cursor.m is not None:
        clauses.append("id > ?")
        args.append(cursor.m)
    rows = snap.query(f"SELECT {cols} FROM message WHERE ({' OR '.join(clauses)}){scope}",
                      (*args, *scoped))
    have = {row[0] for row in rows}
    owners = [mid for (mid,) in snap.query(
        "SELECT DISTINCT message_id FROM part WHERE (time_updated > ? OR "
        f"(time_updated = ? AND id NOT IN ({_marks(len(cursor.t_parts))}))){scope}",
        (t, t, *cursor.t_parts, *scoped)) if mid not in have]
    for chunk in _chunks(owners):
        rows.extend(snap.query(f"SELECT {cols} FROM message WHERE id IN "
                               f"({_marks(len(chunk))})", chunk))
    return rows


__all__ = [
    "CHECKED_VERSIONS", "TABLES", "FUNCTIONS", "TRANSACTIONS", "COLUMNS", "TOTALS",
    "BUSY_MS", "KNOWN_PART_TYPES", "BACK_ENDED",
    "Unreadable", "MissingSession", "authorize", "connect",
    "message_records", "SessionRow", "Child", "Snapshot",
    "Cursor", "Since", "since",
]
