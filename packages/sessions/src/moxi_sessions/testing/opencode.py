"""`OpenCodeDb`: an `opencode.db` with OpenCode 1.18.34's columns, written fast.

    db = OpenCodeDb(data_root)
    s = db.session(title="fix the tests")
    s.user("run the tests")
    a = s.assistant(model=("opencode", "big-pickle"))
    a.step_start()
    a.reasoning("which suite?")
    a.tool("bash", {"command": "pytest -q"}, output="3 passed")
    a.step_finish(input=900, output=40, cache_read=1200)
    a.complete()
    path = db.write()

Rows are held until `write`, which writes every row added or changed since the last
write, and every delete, in one transaction with `synchronous=OFF`, so a test's database
costs no fsync per statement. A database built in several writes is how a test shows a
reader rows arriving: each `write` is one commit, as OpenCode's own writes are.

Every row gets the next time on the builder's clock (`start`, then `step` seconds apart),
in milliseconds as OpenCode writes them; `at=` (epoch seconds) sets a row's time instead.
Message and part ids grow with every row added, whatever its time, as OpenCode's do;
session ids shrink, as OpenCode's do. An assistant message's token and cost totals and its
session's totals are kept up to date as steps finish, the way OpenCode keeps them.

The database also holds an `account` and a `credential` table, as OpenCode's does, so a
test can show that a reader never reads them. Nothing is read from the environment.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

#: 2026-10-30T10:00:00Z, the default start of a builder's clock.
START = 1793354400.0

#: The OpenCode version a builder's sessions name when none is given.
VERSION = "1.18.34"

#: The provider and model a builder's messages name when none is given.
MODEL = ("opencode", "big-pickle")

#: OpenCode 1.18.34's `session`, `message` and `part` tables with their indexes, and two
#: tables standing for what a reader must never read.
SCHEMA = """
CREATE TABLE `session` (
  `id` text PRIMARY KEY, `project_id` text NOT NULL, `workspace_id` text, `parent_id` text,
  `slug` text NOT NULL, `directory` text NOT NULL, `path` text, `title` text NOT NULL,
  `version` text NOT NULL, `share_url` text, `summary_additions` integer,
  `summary_deletions` integer, `summary_files` integer, `summary_diffs` text,
  `metadata` text, `cost` real DEFAULT 0 NOT NULL,
  `tokens_input` integer DEFAULT 0 NOT NULL, `tokens_output` integer DEFAULT 0 NOT NULL,
  `tokens_reasoning` integer DEFAULT 0 NOT NULL,
  `tokens_cache_read` integer DEFAULT 0 NOT NULL,
  `tokens_cache_write` integer DEFAULT 0 NOT NULL, `revert` text, `permission` text,
  `agent` text, `model` text, `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL, `time_compacting` integer, `time_archived` integer);
CREATE TABLE `message` (
  `id` text PRIMARY KEY, `session_id` text NOT NULL, `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL, `data` text NOT NULL,
  CONSTRAINT `fk_message_session_id_session_id_fk` FOREIGN KEY (`session_id`)
    REFERENCES `session`(`id`) ON DELETE CASCADE);
CREATE TABLE `part` (
  `id` text PRIMARY KEY, `message_id` text NOT NULL, `session_id` text NOT NULL,
  `time_created` integer NOT NULL, `time_updated` integer NOT NULL, `data` text NOT NULL,
  CONSTRAINT `fk_part_message_id_message_id_fk` FOREIGN KEY (`message_id`)
    REFERENCES `message`(`id`) ON DELETE CASCADE);
CREATE INDEX `message_session_time_created_id_idx` ON `message`
  (`session_id`,`time_created`,`id`);
CREATE INDEX `part_message_id_id_idx` ON `part` (`message_id`,`id`);
CREATE INDEX `part_session_idx` ON `part` (`session_id`);
CREATE INDEX `session_parent_idx` ON `session` (`parent_id`);
CREATE TABLE `account` (`id` text PRIMARY KEY, `email` text);
CREATE TABLE `credential` (`id` text PRIMARY KEY, `secret` text);
INSERT INTO `account` VALUES ('acc_test', 'someone@example.invalid');
INSERT INTO `credential` VALUES ('cred_test', 'NOT-A-REAL-SECRET');
"""

_COLUMNS = {
    "session": ("id", "project_id", "parent_id", "slug", "directory", "title", "version",
                "cost", "tokens_input", "tokens_output", "tokens_reasoning",
                "tokens_cache_read", "tokens_cache_write", "revert", "agent", "model",
                "time_created", "time_updated", "time_compacting"),
    "message": ("id", "session_id", "time_created", "time_updated", "data"),
    "part": ("id", "message_id", "session_id", "time_created", "time_updated", "data"),
}

_TOTALS = {"tokens_input": "input", "tokens_output": "output",
           "tokens_reasoning": "reasoning"}


def tokens(*, input: int = 0, output: int = 0, reasoning: int = 0, cache_read: int = 0,
           cache_write: int = 0) -> dict:
    """A `tokens` object as OpenCode writes one: the five classes apart, and `total`
    adding all five."""
    return {"total": input + output + reasoning + cache_read + cache_write,
            "input": input, "output": output, "reasoning": reasoning,
            "cache": {"write": cache_write, "read": cache_read}}


class OpenCodeDb:
    """`<root>/<name>`, an OpenCode database. Nothing touches the disk until `write`."""

    def __init__(self, root: str | Path, *, name: str = "opencode.db",
                 start: float = START, step: float = 1.0, wal: bool = True):
        self.path = Path(root) / name
        self.clock = int(start * 1000)
        self.step = int(step * 1000)
        self.wal = wal
        self.rows: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in _COLUMNS}
        self._dirty: dict[tuple[str, str], None] = {}
        self._deleted: list[tuple[str, str]] = []
        self._serial = 0
        self._newest = self.clock
        self._unsummed: set[str] = set()
        self.sessions: dict[str, OpenCodeSession] = {}

    # -- the clock and ids -----------------------------------------------------------

    def tick(self, at: float | None = None) -> int:
        """The next time on the clock in milliseconds, or `at` (epoch seconds)."""
        if at is not None:
            return round(at * 1000)
        now = self.clock
        self.clock += self.step
        return now

    def mint(self, prefix: str, ms: int) -> str:
        """A new id: growing for messages and parts, shrinking for sessions."""
        self._serial += 1
        self._newest = max(self._newest, ms)
        stamp = (2 ** 48 - 1 - self._newest) if prefix == "ses" else self._newest
        return f"{prefix}_{stamp:012x}{self._serial:014d}"

    # -- rows ------------------------------------------------------------------------

    def put(self, table: str, row: dict[str, Any]) -> None:
        """Add or replace a row as given, to be written by the next `write`."""
        self.rows[table][row["id"]] = row
        self._dirty[(table, row["id"])] = None

    def session(self, *, title: str = "a session", directory: str = "/work/project",
                parent: str | None = None, agent: str | None = "build",
                model: tuple[str, str] | None = MODEL, raw_model: Any = ...,
                version: str = VERSION, revert: str | None = None,
                totals: dict[str, Any] | None = None,
                at: float | None = None) -> OpenCodeSession:
        """A session row. `model` is written as OpenCode writes it; `raw_model` writes the
        column's text as given (None for a null). `totals` fixes the token and cost
        columns (keys as in the table, such as `tokens_input` and `cost`) instead of
        adding them up from the messages."""
        ms = self.tick(at)
        sid = self.mint("ses", ms)
        if raw_model is ...:
            raw_model = None if model is None else json.dumps(
                {"id": model[1], "providerID": model[0], "variant": "default"},
                separators=(",", ":"))
        row = {"id": sid, "project_id": "global", "parent_id": parent, "slug": "a-session",
               "directory": directory, "title": title, "version": version, "cost": 0,
               "tokens_input": 0, "tokens_output": 0, "tokens_reasoning": 0,
               "tokens_cache_read": 0, "tokens_cache_write": 0, "revert": revert,
               "agent": agent, "model": raw_model, "time_created": ms, "time_updated": ms,
               "time_compacting": None}
        self.put("session", row)
        handle = OpenCodeSession(self, sid, fixed=totals)
        self.sessions[sid] = handle
        handle.sum()
        return handle

    def delete(self, message_id: str) -> None:
        """Delete a message and its parts at the next `write`, as a revert does."""
        message = self.rows["message"].pop(message_id)
        for pid in [pid for pid, part in self.rows["part"].items()
                    if part["message_id"] == message_id]:
            del self.rows["part"][pid]
            self._dirty.pop(("part", pid), None)
        self._dirty.pop(("message", message_id), None)
        self._deleted.append(("message", message_id))
        self.sessions[message["session_id"]].sum()

    def delete_session(self, session_id: str) -> None:
        """Delete a session, its messages and their parts at the next `write`."""
        for mid in [mid for mid, m in self.rows["message"].items()
                    if m["session_id"] == session_id]:
            self.delete(mid)
        self.rows["session"].pop(session_id)
        self._dirty.pop(("session", session_id), None)
        self._deleted.append(("session", session_id))

    # -- writing ---------------------------------------------------------------------

    def write(self) -> Path:
        """Write every row added or changed since the last write, and every delete, in
        one transaction. Creates the database on the first write. Returns its path."""
        for sid in sorted(self._unsummed):
            if sid in self.sessions and sid in self.rows["session"]:
                self.sessions[sid].sum_now()
        self._unsummed.clear()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        conn = sqlite3.connect(self.path, isolation_level=None)
        try:
            conn.execute("PRAGMA synchronous=OFF")
            if new:
                if self.wal:
                    conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
            conn.execute("BEGIN")
            for table, rid in self._deleted:
                if table == "message":
                    conn.execute("DELETE FROM part WHERE message_id = ?", (rid,))
                conn.execute(f"DELETE FROM {table} WHERE id = ?", (rid,))
            for table, rid in self._dirty:
                cols = _COLUMNS[table]
                row = self.rows[table][rid]
                sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES "
                    f"({', '.join('?' * len(cols))}) ON CONFLICT(id) DO UPDATE SET {sets}",
                    tuple(row[c] for c in cols))
            conn.execute("COMMIT")
        finally:
            conn.close()
        self._dirty.clear()
        self._deleted.clear()
        return self.path


class OpenCodeSession:
    """One session's rows. Methods that add a message return its handle."""

    def __init__(self, db: OpenCodeDb, session_id: str, *, fixed: dict | None = None):
        self.db = db
        self.id = session_id
        self.fixed = fixed
        self.last_user: str | None = None

    @property
    def row(self) -> dict[str, Any]:
        return self.db.rows["session"][self.id]

    def update(self, *, at: float | None = None, **columns: Any) -> None:
        """Set columns of the session row (`revert`, `title`, ...) and stamp it."""
        row = dict(self.row)
        row.update(columns)
        row["time_updated"] = self.db.tick(at)
        self.db.put("session", row)

    def sum(self) -> None:
        """Mark the session's totals to be added up again at the next write."""
        self.db._unsummed.add(self.id)

    def sum_now(self) -> None:
        """Add the session's totals up from its assistant messages, as OpenCode does."""
        row = dict(self.row)
        if self.fixed is not None:
            row.update(self.fixed)
        else:
            for column in ("cost", *_TOTALS, "tokens_cache_read", "tokens_cache_write"):
                row[column] = 0
            for message in self.db.rows["message"].values():
                if message["session_id"] != self.id:
                    continue
                data = json.loads(message["data"]) if _is_json(message["data"]) else None
                if not isinstance(data, dict) or data.get("role") != "assistant":
                    continue
                t = data.get("tokens") or {}
                for column, key in _TOTALS.items():
                    row[column] += t.get(key, 0)
                row["tokens_cache_read"] += (t.get("cache") or {}).get("read", 0)
                row["tokens_cache_write"] += (t.get("cache") or {}).get("write", 0)
                row["cost"] += data.get("cost", 0)
        if row != self.row:
            self.db.put("session", row)

    def child(self, **kw: Any) -> OpenCodeSession:
        """A child session of this one, as a subagent's task makes."""
        kw.setdefault("agent", "general")
        return self.db.session(parent=self.id, **kw)

    def message(self, data: dict | str, *, at: float | None = None,
                cls: type[OpenCodeMessage] | None = None) -> OpenCodeMessage:
        """A message row holding `data` as given: a dict is written as JSON, a string as
        it is (a row that will not decode, say)."""
        ms = self.db.tick(at)
        mid = self.db.mint("msg", ms)
        if isinstance(data, dict):
            data = {**data}
            data.setdefault("time", {"created": ms})
        self.db.put("message", {"id": mid, "session_id": self.id, "time_created": ms,
                                "time_updated": ms, "data": _dump(data)})
        return (cls or OpenCodeMessage)(self, mid)

    def user(self, text: str | None = "Hello.", *, model: tuple[str, str] | None = MODEL,
             raw_model: Any = ..., agent: str = "build",
             at: float | None = None) -> OpenCodeMessage:
        """A user message with one text part (none for `text=None`), the part at the
        message's time when `at` is given. `model` is the pair it asks for; `raw_model`
        writes the `model` key as given."""
        data: dict[str, Any] = {"role": "user", "agent": agent}
        if raw_model is not ...:
            data["model"] = raw_model
        elif model is not None:
            data["model"] = {"providerID": model[0], "modelID": model[1]}
        data["summary"] = {"diffs": []}
        message = self.message(data, at=at)
        self.last_user = message.id
        if text is not None:
            message.text(text, at=at)
        return message

    def assistant(self, *, model: tuple[str, str] = MODEL, agent: str = "build",
                  mode: str | None = None, parent: str | None = None,
                  summary: bool | None = None, error: dict | None = None,
                  at: float | None = None) -> OpenCodeAssistant:
        """An assistant message answering `parent` (default: the last user message),
        with no steps yet and not completed."""
        data: dict[str, Any] = {
            "parentID": parent if parent is not None else self.last_user,
            "role": "assistant", "mode": mode or agent, "agent": agent,
            "path": {"cwd": self.row["directory"], "root": "/"},
            "cost": 0, "tokens": tokens(), "modelID": model[1], "providerID": model[0]}
        if summary is not None:
            data["summary"] = summary
        if error is not None:
            data["error"] = error
        message = self.message(data, at=at, cls=OpenCodeAssistant)
        return message  # type: ignore[return-value]


class OpenCodeMessage:
    """One message's row and its parts. Methods that add a part return its id."""

    def __init__(self, session: OpenCodeSession, message_id: str):
        self.session = session
        self.db = session.db
        self.id = message_id

    @property
    def row(self) -> dict[str, Any]:
        return self.db.rows["message"][self.id]

    @property
    def data(self) -> dict[str, Any]:
        return json.loads(self.row["data"])

    def update(self, changes: dict[str, Any] | None = None, *,
               at: float | None = None) -> int:
        """Merge `changes` into the message's data and stamp the row. Returns the
        stamp."""
        return self._update(changes, self.db.tick(at))

    def _update(self, changes: dict[str, Any] | None, ms: int) -> int:
        row = dict(self.row)
        if changes:
            row["data"] = _dump({**json.loads(row["data"]), **changes})
        row["time_updated"] = ms
        self.db.put("message", row)
        self.session.sum()
        return row["time_updated"]

    def part(self, data: dict | str, *, at: float | None = None) -> str:
        """A part row holding `data` as given (a string is written as it is)."""
        return self._part(data, self.db.tick(at))

    def _part(self, data: dict | str, ms: int) -> str:
        pid = self.db.mint("prt", ms)
        self.db.put("part", {"id": pid, "message_id": self.id,
                             "session_id": self.session.id, "time_created": ms,
                             "time_updated": ms, "data": _dump(data)})
        return pid

    def update_part(self, part_id: str, changes: dict[str, Any] | None = None, *,
                    at: float | None = None) -> int:
        """Merge `changes` into a part's data and stamp the row. Returns the stamp."""
        row = dict(self.db.rows["part"][part_id])
        if changes:
            row["data"] = _dump({**json.loads(row["data"]), **changes})
        row["time_updated"] = self.db.tick(at)
        self.db.put("part", row)
        return row["time_updated"]

    def text(self, text: str = "Done.", *, synthetic: bool | None = None,
             at: float | None = None) -> str:
        """A text part. `synthetic` sets OpenCode's mark for a part it added itself; None
        leaves the key out, as OpenCode does for text the person or the model wrote."""
        ms = self.db.tick(at)
        data: dict[str, Any] = {"type": "text", "text": text,
                                "time": {"start": ms, "end": ms}}
        if synthetic is not None:
            data["synthetic"] = synthetic
        return self._part(data, ms)


class OpenCodeAssistant(OpenCodeMessage):
    """An assistant message: its steps, thoughts, tool calls and completion."""

    def step_start(self, *, at: float | None = None) -> str:
        return self.part({"type": "step-start"}, at=at)

    def step_finish(self, *, input: int = 0, output: int = 0, reasoning: int = 0,
                    cache_read: int = 0, cache_write: int = 0, cost: float = 0,
                    reason: str = "stop", at: float | None = None) -> str:
        """Finish the open step with its tokens and cost, and add them to the message's
        and the session's totals, as OpenCode does when a step ends."""
        used = tokens(input=input, output=output, reasoning=reasoning,
                      cache_read=cache_read, cache_write=cache_write)
        pid = self.part({"reason": reason, "type": "step-finish", "tokens": used,
                         "cost": cost}, at=at)
        data = self.data
        total = data.get("tokens") or tokens()
        merged = tokens(input=total["input"] + input, output=total["output"] + output,
                        reasoning=total["reasoning"] + reasoning,
                        cache_read=total["cache"]["read"] + cache_read,
                        cache_write=total["cache"]["write"] + cache_write)
        self._update({"tokens": merged, "cost": data.get("cost", 0) + cost},
                     self.db.rows["part"][pid]["time_created"])
        return pid

    def step(self, *, text: str | None = None, **used: Any) -> tuple[str, str]:
        """One whole step: its start, a text part when given, its finish. Returns the
        start and finish part ids."""
        start = self.step_start()
        if text is not None:
            self.text(text)
        return start, self.step_finish(**used)

    def reasoning(self, text: str = "Thinking it over.", *,
                  at: float | None = None) -> str:
        ms = self.db.tick(at)
        return self._part({"type": "reasoning", "text": text,
                           "time": {"start": ms, "end": ms}}, ms)

    def tool(self, name: str = "bash", tool_input: dict | None = None, *,
             output: str | None = "", status: str = "completed", error: str | None = None,
             metadata: dict | None = None, call_id: str | None = None,
             at: float | None = None) -> str:
        """A tool part. `status` is OpenCode's (`pending`, `running`, `completed`,
        `error`); `output` is written only for `completed`, `error` only for `error`."""
        ms = self.db.tick(at)
        state: dict[str, Any] = {"status": status,
                                 "input": tool_input if tool_input is not None else {}}
        if status == "completed":
            state.update(output=output if output is not None else "",
                         metadata=metadata if metadata is not None else {},
                         title=name, time={"start": ms, "end": ms})
        elif status == "error":
            state.update(error=error or "failed", time={"start": ms, "end": ms})
            if metadata is not None:
                state["metadata"] = metadata
        elif status == "running":
            state.update(time={"start": ms})
            if metadata is not None:
                state["metadata"] = metadata
        self.db._serial += 1
        return self._part({"type": "tool", "tool": name,
                           "callID": call_id or f"call_{self.db._serial:024d}",
                           "state": state}, ms)

    def complete(self, finish: str = "stop", *, at: float | None = None) -> int:
        """Mark the message completed (`time.completed`, `finish`) and stamp it."""
        ms = self.db.tick(at)
        data = self.data
        return self._update({"time": {**data.get("time", {}), "completed": ms},
                             "finish": finish}, ms)


def _dump(data: Any) -> str:
    return data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


__all__ = ["OpenCodeDb", "OpenCodeSession", "OpenCodeMessage", "OpenCodeAssistant",
           "tokens", "SCHEMA", "VERSION"]
