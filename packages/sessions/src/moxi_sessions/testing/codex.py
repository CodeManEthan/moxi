"""`CodexRollout`: a Codex rollout written line by line, as Codex 0.153.0 writes one.

    r = CodexRollout(sessions_root)          # line 1, `session_meta`, is written for you
    turn = r.turn()                          # `task_started` and `turn_context`
    r.user("read the notes")
    call = r.tool_call("exec", "cat notes.md")
    r.tool_output(call, "the notes")
    r.request(token_usage(input=1200, cached=1000, output=40))
    r.assistant("done")
    r.turn_end()
    path = r.write()

A request is a `token_usage_record` line followed by the `token_count` event Codex writes
after it, carrying `rate_limits`. A rollout made with a legacy `cli_version` (0.145, 0.146)
writes the `token_count` event alone, its `info.last_token_usage` the request, as those
versions did. Every line gets the next time on the builder's clock (`start`, then `step`
seconds apart) and an `ordinal`. Methods that add a line return its 1-based line number;
`turn` returns the turn id, `tool_call` the call id and `request` the response id.
`offset(n)` is the byte offset where line `n` starts once written.

`write_imports` writes the imports file Codex keeps beside its sessions root.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moxi.files import atomic_write

#: The thread id a builder uses when none is given.
THREAD = "0190c000-0000-7000-8000-0000000c0de1"

#: 2026-10-30T10:00:00Z, the default start of a builder's clock.
START = 1793354400.0

#: The model a builder's turns name when none is given.
MODEL = "codex-model-x"

#: The context window a builder's turns write when none is given.
CONTEXT_WINDOW = 258400

#: The imports file's name, beside the sessions root.
IMPORTS_FILE = "external_agent_session_imports.json"

#: Line 1's `originator` and the turn id prefix of an imported thread.
IMPORT_ORIGINATOR = "Codex Desktop"
IMPORT_TURN_PREFIX = "external-import-turn-"

#: The versions that write no `token_usage_record`.
LEGACY_VERSIONS = ("0.145", "0.146")


def token_usage(*, input: int = 1000, cached: int = 800, cache_write: int = 0,
                output: int = 50, reasoning: int = 10) -> dict:
    """A Codex usage object. `input` includes `cached` and `output` includes `reasoning`,
    as Codex counts them."""
    return {"input_tokens": input, "cached_input_tokens": cached,
            "cache_write_input_tokens": cache_write, "output_tokens": output,
            "reasoning_output_tokens": reasoning, "total_tokens": input + output}


def rate_limits(*, limit_id: str | None = "codex", limit_name: str | None = None,
                primary: tuple | None = (5.0, 300, 1793370000),
                secondary: tuple | None = (40.0, 10080, 1793900000),
                plan_type: str | None = "plus", reached: str | None = None,
                **windows: tuple | None) -> dict:
    """A `rate_limits` object as a `token_count` event carries it. Each window is
    `(used_percent, window_minutes, resets_at)`, or None for a null; `windows` adds more
    by name."""
    def window(value: tuple | None) -> dict | None:
        if value is None:
            return None
        used, minutes, resets = value
        return {"used_percent": used, "window_minutes": minutes, "resets_at": resets}

    block: dict[str, Any] = {"limit_id": limit_id, "limit_name": limit_name,
                             "primary": window(primary), "secondary": window(secondary)}
    block.update((name, window(value)) for name, value in windows.items())
    block.update({"credits": {"has_credits": False, "unlimited": False, "balance": "0"},
                  "individual_limit": None, "spend_control_reached": None,
                  "plan_type": plan_type, "rate_limit_reached_type": reached})
    return block


def _stamp(epoch: float) -> str:
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def write_imports(sessions_root: str | Path, thread_ids: list[str] | tuple[str, ...] = (),
                  *, text: str | None = None) -> Path:
    """Write the imports file beside `sessions_root`, listing `thread_ids`, as Codex
    writes it; `text` writes that text instead (one that will not parse, say)."""
    path = Path(sessions_root).parent / IMPORTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        records = [{"source_path": f"/work/imported/{n}.jsonl",
                    "content_sha256": f"{n:064x}", "imported_thread_id": thread,
                    "imported_at": "2026-10-30T10:00:00Z",
                    "source_modified_at": "2026-10-30T09:00:00Z",
                    "connector_names": []}
                   for n, thread in enumerate(thread_ids, start=1)]
        text = json.dumps({"records": records, "detected_connector_records": []})
    atomic_write(path, text, mode=0o644)
    return path


class CodexRollout:
    """A rollout under `root` (a sessions root):
    `<root>/YYYY/MM/DD/rollout-<YYYY-MM-DDThh-mm-ss>-<thread>.jsonl`, dated by `start`.

    `cli_version` sets the format: a legacy one writes requests as `token_count` events
    alone. `imported` makes the thread look like one Codex imported (its originator and
    turn ids, and no `turn_context`). `meta` False leaves line 1 out; `meta_fields` adds
    or replaces line 1's payload keys (a None value removes the key).

    Lines are held until `write` (the whole file) or `flush` (only the lines added since
    the last write). Nothing is read from the environment.
    """

    def __init__(self, root: str | Path, *, thread_id: str = THREAD,
                 cli_version: str = "0.153.0", originator: str = "codex_exec",
                 source: Any = "exec", provider: str = "openai",
                 history_mode: str = "paginated", cwd: str = "/work/project",
                 start: float = START, step: float = 1.0, imported: bool = False,
                 meta: bool = True, meta_fields: dict | None = None,
                 path: str | Path | None = None):
        self.root = Path(root)
        self.thread_id = thread_id
        self.cli_version = cli_version
        self.imported = imported
        self.clock = start
        self.step = step
        moment = datetime.fromtimestamp(start, timezone.utc)
        self.path = Path(path) if path is not None else (
            self.root / moment.strftime("%Y/%m/%d")
            / f"rollout-{moment.strftime('%Y-%m-%dT%H-%M-%S')}-{thread_id}.jsonl")
        self.lines: list[str] = []
        self.turn_id: str | None = None
        self.info: dict | None = None
        self._count = 0
        self._written = 0
        self._terminated = True
        if meta:
            payload: dict[str, Any] = {
                "session_id": thread_id, "id": thread_id, "timestamp": _stamp(start),
                "cwd": cwd, "originator": IMPORT_ORIGINATOR if imported else originator,
                "cli_version": cli_version, "source": source, "model_provider": provider,
                "base_instructions": {"text": "You are a coding agent."},
                "history_mode": history_mode, "multi_agent_version": "disabled",
                "context_window": {"window_id": f"{thread_id[:-4]}cafe"},
            }
            for key, value in (meta_fields or {}).items():
                if value is None:
                    payload.pop(key, None)
                else:
                    payload[key] = value
            self.line("session_meta", payload)

    @property
    def legacy(self) -> bool:
        return any(self.cli_version == v or self.cli_version.startswith(v + ".")
                   for v in LEGACY_VERSIONS)

    # -- the pieces of a line ------------------------------------------------------------

    def _serial(self) -> int:
        self._count += 1
        return self._count

    def _tick(self) -> str:
        stamp = _stamp(self.clock)
        self.clock += self.step
        return stamp

    def _passthrough(self) -> dict:
        return {"turn_id": self.turn_id} if self.turn_id is not None else {}

    # -- adding lines --------------------------------------------------------------------

    def add(self, line: dict) -> int:
        """Any line, as given. Returns its line number."""
        self.lines.append(json.dumps(line))
        return len(self.lines)

    def add_raw(self, text: str) -> int:
        """Any text as one line (a line that will not decode, say)."""
        self.lines.append(text)
        return len(self.lines)

    def line(self, kind: str, payload: Any) -> int:
        """A line of top-level `kind` with `payload`, stamped and numbered."""
        return self.add({"timestamp": self._tick(), "ordinal": len(self.lines),
                         "type": kind, "payload": payload})

    def event(self, sub: str, **payload: Any) -> int:
        """An `event_msg` line of payload type `sub`."""
        return self.line("event_msg", {"type": sub, **payload})

    def item(self, sub: str, **payload: Any) -> int:
        """A `response_item` line of payload type `sub`, in the current turn."""
        meta = self._passthrough()
        body = {"type": sub, **payload}
        if meta:
            body["internal_chat_message_metadata_passthrough"] = meta
        return self.line("response_item", body)

    def turn(self, model: str = MODEL, *, context_window: int | None = CONTEXT_WINDOW,
             turn_id: str | None = None, context: bool | None = None, **fields: Any) -> str:
        """A new turn: `task_started`, then its `turn_context` naming `model` (left out of
        an imported thread unless `context` is true). Returns the turn id."""
        serial = self._serial()
        if turn_id is None:
            turn_id = (f"{IMPORT_TURN_PREFIX}{serial}" if self.imported
                       else f"turn-{serial:04d}")
        self.turn_id = turn_id
        self.event("task_started", turn_id=turn_id, started_at=int(self.clock),
                   model_context_window=context_window, collaboration_mode_kind="default")
        if context is None:
            context = not self.imported
        if context:
            self.turn_context(model, **fields)
        return turn_id

    def turn_context(self, model: str = MODEL, **fields: Any) -> int:
        """A `turn_context` line for the current turn; `fields` (`service_tier`, say) are
        set on its payload."""
        return self.line("turn_context", {
            "turn_id": self.turn_id, "cwd": "/work/project", "approval_policy": "never",
            "sandbox_policy": {"type": "workspace-write"}, "model": model,
            "effort": "high", "summary": "auto", **fields})

    def settings(self, model: str = MODEL, provider: str = "openai", **fields: Any) -> int:
        """A `thread_settings_applied` event; `fields` (`service_tier`, say) are set in
        its `thread_settings`."""
        return self.event("thread_settings_applied", thread_id=self.thread_id,
                          thread_settings={"model": model, "model_provider_id": provider,
                                           "approval_policy": "never", **fields})

    def world_state(self) -> int:
        return self.line("world_state", {"full": True, "state": {"model": MODEL}})

    def user(self, text: str = "Hello.", *, role: str = "user") -> int:
        """A `response_item` message (`role` user, developer or assistant)."""
        kind = "output_text" if role == "assistant" else "input_text"
        return self.item("message", id=f"msg_{self._serial():04d}", role=role,
                         content=[{"type": kind, "text": text}])

    def assistant(self, text: str = "Done.") -> int:
        return self.user(text, role="assistant")

    def reasoning(self, summary: str | None = "Thinking it over.") -> int:
        blocks = [] if summary is None else [{"type": "summary_text", "text": summary}]
        return self.item("reasoning", id=f"rs_{self._serial():04d}", summary=blocks,
                         encrypted_content="gAAAA")

    def tool_call(self, name: str = "exec", tool_input: Any = "ls", *,
                  kind: str = "custom_tool_call", call_id: str | None = None) -> str:
        """A call item; `kind` `function_call` writes `tool_input` as its `arguments`
        (JSON text when given a dict). Returns its call id."""
        call_id = call_id or f"call_{self._serial():04d}"
        if kind == "function_call":
            arguments = (json.dumps(tool_input) if not isinstance(tool_input, str)
                         else tool_input)
            self.item(kind, id=f"fc_{call_id}", name=name, arguments=arguments,
                      call_id=call_id)
        elif kind == "custom_tool_call":
            self.item(kind, id=f"ctc_{call_id}", status="completed", call_id=call_id,
                      name=name, input=tool_input)
        else:
            self.item(kind, id=f"it_{call_id}", call_id=call_id, action=tool_input,
                      status="completed")
        return call_id

    def tool_output(self, call_id: str, output: Any = "ok", *,
                    kind: str = "custom_tool_call_output") -> int:
        """A call's output item; a string `output` is written as one text block."""
        if isinstance(output, str):
            output = [{"type": "input_text", "text": output}]
        return self.item(kind, id=f"out_{call_id}", call_id=call_id, output=output)

    def request(self, usage: dict | None = None, *, response_id: str | None = None,
                limits: dict | None | bool = True, context_window: int | None =
                CONTEXT_WINDOW, count: bool = True) -> str:
        """One model request. Record mode: a `token_usage_record` keyed by
        `response_id`, then (with `count`) its `token_count` event. Legacy mode: the
        `token_count` event alone. `limits` True writes `rate_limits()`, a dict that
        object, and None or False a null. Returns the response id."""
        usage = usage if usage is not None else token_usage()
        response_id = response_id or f"resp_{self._serial():04d}"
        if not self.legacy:
            self.line("token_usage_record", {
                "thread_id": self.thread_id, "turn_id": self.turn_id,
                "session_id": self.thread_id, "root_turn_id": self.turn_id,
                "response_id": response_id, "usage": usage, "turn_token_usage": usage,
                "thread_token_usage": usage})
        if count or self.legacy:
            total = dict(usage)
            if self.info is not None:
                total = {k: v + self.info["total_token_usage"].get(k, 0)
                         if isinstance(v, int) else v for k, v in usage.items()}
            info = {"total_token_usage": total, "last_token_usage": usage,
                    "model_context_window": context_window}
            self.token_count(info=info, limits=limits)
        return response_id

    def token_count(self, *, info: dict | None = None,
                    limits: dict | None | bool = True) -> int:
        """A `token_count` event as given: `info` (None for a null) and `limits` as in
        `request`."""
        if limits is True:
            limits = rate_limits()
        elif limits is False:
            limits = None
        if info is not None:
            self.info = info
        return self.event("token_count", info=info, rate_limits=limits)

    def turn_end(self, duration_ms: int = 1200) -> int:
        """The `task_complete` event that ends the current turn."""
        return self.event("task_complete", turn_id=self.turn_id, last_agent_message="Done.",
                          duration_ms=duration_ms)

    def abort(self, reason: str = "interrupted") -> int:
        """A `turn_aborted` event for the current turn."""
        return self.event("turn_aborted", turn_id=self.turn_id, reason=reason)

    def rollback(self, num_turns: int = 1) -> int:
        """A `thread_rolled_back` event."""
        return self.event("thread_rolled_back", num_turns=num_turns)

    def compaction(self, kind: str = "compacted") -> int:
        """A compaction, as one of the lines Codex writes for one: a top-level
        `compacted` line, a `context_compacted` event, or a `compaction` or
        `context_compaction` response item."""
        if kind == "compacted":
            return self.line("compacted", {"message": "", "replacement_history": [],
                                           "window_number": 1})
        if kind == "context_compacted":
            return self.event("context_compacted")
        return self.item(kind, id=f"cmp_{self._serial():04d}", encrypted_content="gAAAA")

    # -- writing -------------------------------------------------------------------------

    @property
    def imports_path(self) -> Path:
        """Where Codex keeps its imports file for this rollout's sessions root."""
        return self.root.parent / IMPORTS_FILE

    def text(self, *, terminated: bool = True) -> str:
        """The whole file's text as `write` would write it."""
        body = "\n".join(self.lines)
        return body + ("\n" if terminated and self.lines else "")

    def offset(self, line: int) -> int:
        """The byte offset where 1-based line `line` starts in the written file."""
        return sum(len(text.encode("utf-8")) + 1 for text in self.lines[:line - 1])

    def write(self, *, terminated: bool = True) -> Path:
        """Write the whole file. With `terminated` false the last line has no newline,
        as a line being written."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, self.text(terminated=terminated), mode=0o644)
        self._written = len(self.lines)
        self._terminated = terminated
        return self.path

    def flush(self, *, terminated: bool = True) -> Path:
        """Append the lines added since the last write, as Codex appends them. A last
        line left without its newline is ended first."""
        if not self.path.exists():
            return self.write(terminated=terminated)
        new = self.lines[self._written:]
        data = "" if self._terminated else "\n"
        data += "\n".join(new) + ("\n" if terminated and new else "")
        with self.path.open("ab") as handle:
            handle.write(data.encode("utf-8"))
        self._written = len(self.lines)
        self._terminated = terminated or not new
        return self.path
