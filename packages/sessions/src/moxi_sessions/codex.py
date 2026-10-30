"""Codex's rollout: where a thread's rollout files are, how its lines map into the one
shape, its limits readings, and the marks of an imported thread.

* **Finding them** (`find_rollout`). Codex writes a thread's rollout under
  `<sessions root>/YYYY/MM/DD/` and names it, by its own rule,
  `rollout-<YYYY-MM-DDThh-mm-ss>-<thread id>[_<rollout id>].jsonl`, compressed to
  `.jsonl.zst` after a week idle. A reverted thread can leave a second file with a rollout
  id suffix. `find_rollout` returns every file whose name parses as the thread's rollout;
  what none, one, a compressed one or two of them mean is the caller's.
* **Reading one forward** (`stream_rollout`): every line's entry, through the one decoder
  in `claude` (`stream_entries`), so a rollout line decodes exactly as a transcript line
  does. A backward read is `read_back`'s.
* **How a line maps into the one shape** (`line_records`, `Mapper`, `read_records`): each
  line to the records of `moxi_sessions.shape`, with the little state a later line needs
  carried forward explicitly (`State`).
* **Limits readings** (`limits_from_app_server`): Codex's app-server rate-limit payload
  as the same limits record a rollout's `token_count` gives.
* **Import marks** (`read_imports`, `read_head`, `import_marks`): the three facts that
  can say a thread was imported from another harness, each as found.
* **Spawned threads** (`read_spawns`, `spawn_link`): the child threads a rollout's spawn
  items name, and the parent a spawned thread's line 1 names.

Nothing here judges a line: which records count, and what an import or a missing reading
means, are the caller's.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .claude import (SAFE_ID_RE, _count, _said, decode, parse_line, parse_timestamp,
                     stream_entries)
from .shape import (BAD_STRUCTURE, CODEX, NOT_OBJECT, UNKNOWN, LimitWindow, Limits,
                    Malformed, Marker, Message, Other, Record, Request, ToolCall,
                    ToolResult, tool_summary)


#: Codex's rollout name rule. `thread` is the thread id, `rollout` the suffix a revert
#: adds, `zst` present when the file is compressed.
NAME_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
    r"(?P<thread>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"(?:_(?P<rollout>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}))?\.jsonl(?P<zst>\.zst)?$")


def find_rollout(sessions_root: str | Path, thread_id: str) -> list[Path]:
    """Every rollout file for `thread_id` under `sessions_root`, sorted by path.

    A file is a candidate when it sits three directories down (`YYYY/MM/DD`) and its name
    parses by `NAME_RE` with this thread id; `.jsonl` and `.jsonl.zst` alike. The id is
    matched as Codex writes it, in lower case. An empty list means the root was read and holds none.

    Raises ValueError for an id the search refuses (`claude.SAFE_ID_RE`: empty, a path
    separator, a glob character or a leading dot), and OSError when the root is not a
    directory that can be read, so an empty list is never said of a root nobody looked in.
    """
    if not isinstance(thread_id, str) or not SAFE_ID_RE.match(thread_id):
        raise ValueError(f"{thread_id!r} is not a thread id the search looks up")
    root = Path(sessions_root)
    with os.scandir(root):
        pass                              # raises OSError for an absent or unreadable root
    wanted = thread_id.lower()
    found = []
    for path in root.glob(f"*/*/*/rollout-*{thread_id}*"):
        match = NAME_RE.match(path.name)
        if match and match.group("thread").lower() == wanted and path.is_file():
            found.append(path)
    return sorted(found)


def stream_rollout(path: str | Path, *, start_line: int = 0) -> Iterator[dict]:
    """Every entry of one rollout, in file order, from `start_line` on.

    Lines decode by `claude.stream_entries`: a line that will not decode, a last line
    Codex is still writing among them, is yielded as a MALFORMED entry, never dropped. The
    file is read as UTF-8 with undecodable bytes replaced, and opened when iteration
    starts; an OSError opening or reading it is raised from the iteration. A compressed
    rollout, which this does not read, raises ValueError at the call.
    """
    if str(path).endswith(".zst"):
        raise ValueError(f"{path} is a compressed rollout; it is not read")
    return _entries(Path(path), start_line)


def _entries(path: Path, start_line: int) -> Iterator[dict]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        yield from stream_entries(handle, start_line=start_line)


# --- what Codex writes -----------------------------------------------------------------

#: The Codex versions the mapping was checked against.
CHECKED_VERSIONS = ("0.153.0",)

#: The Codex versions that write no `token_usage_record`. In a rollout whose line 1 names
#: one of them (a patch release included), each `token_count` event's
#: `info.last_token_usage` is a request (legacy mode). Any other version, a version this
#: list does not know included, reads in record mode.
LEGACY_VERSIONS = ("0.145", "0.146")

#: Every top-level line kind Codex 0.153.0 persists in a rollout, as far as one has been
#: seen. `inter_agent_communication_metadata`, written by a spawning thread and its child
#: when a message passes between them, carries only whether the message starts a turn
#: (`trigger_turn`), so it gives no record of its own. Codex 0.153.0 can also write
#: `inter_agent_communication`, `security_risk_score` and `realtime_item`; none has been
#: seen, so each reads as not recognised until the mapping is checked against one.
TOP_KINDS = ("session_meta", "turn_context", "world_state", "token_usage_record",
             "compacted", "event_msg", "response_item",
             "inter_agent_communication_metadata")

#: `event_msg` payload types Codex 0.153.0 persists in a rollout (its rollout policy's
#: `should_persist_event_msg`), by their serialized names.
EVENT_KINDS = ("item_completed", "token_count", "thread_goal_updated", "thread_rolled_back",
               "turn_aborted", "task_started", "task_complete", "thread_settings_applied",
               "user_message", "agent_message", "agent_reasoning",
               "agent_reasoning_raw_content", "entered_review_mode", "exited_review_mode",
               "patch_apply_end", "context_compacted", "mcp_tool_call_end", "web_search_end",
               "image_generation_end", "sub_agent_activity")

#: `response_item` payload types Codex 0.153.0 persists (`should_persist_response_item`).
RESPONSE_KINDS = ("message", "agent_message", "reasoning", "local_shell_call",
                  "function_call", "tool_search_call", "function_call_output",
                  "tool_search_output", "custom_tool_call", "custom_tool_call_output",
                  "web_search_call", "image_generation_call", "compaction",
                  "context_compaction")

#: The `session_meta` payload keys Codex 0.153.0 writes on line 1. A key off this list
#: (`forked_from_id`, `history_base`, a key not seen before) is named in the
#: `session_meta` marker's `unlisted_keys`.
META_KEYS = ("session_id", "id", "timestamp", "cwd", "originator", "cli_version", "source",
             "thread_source", "model_provider", "base_instructions", "history_mode",
             "multi_agent_version", "context_window", "git")

#: The `history_mode` values line 1 can hold.
HISTORY_MODES = ("legacy", "paginated")

#: `response_item` types that are a tool call, and those that are a call's output.
CALL_ITEMS = frozenset({"function_call", "custom_tool_call", "local_shell_call",
                        "web_search_call", "tool_search_call", "image_generation_call"})
OUTPUT_ITEMS = frozenset({"function_call_output", "custom_tool_call_output",
                          "tool_search_output"})
#: Where a call item holds its input, the first key present.
CALL_INPUT_KEYS = ("arguments", "input", "action")

#: Lines that mark a compaction: a top-level kind, an event, and two response items.
COMPACTION_KINDS = frozenset({"compacted", "event_msg/context_compacted",
                              "response_item/compaction",
                              "response_item/context_compaction"})

def _is_legacy(cli_version: Any) -> bool:
    return isinstance(cli_version, str) and any(
        cli_version == version or cli_version.startswith(version + ".")
        for version in LEGACY_VERSIONS)


# --- the mapping into the shape --------------------------------------------------------
#
# Each rollout line, `{timestamp, type, payload}`, maps to records of
# `moxi_sessions.shape`:
#
# | line                                         | records                                |
# |----------------------------------------------|----------------------------------------|
# | `session_meta` (line 1)                      | marker `session_meta`                  |
# | `token_usage_record`                         | request                                |
# | `event_msg/token_count`                      | limits, when it carries `rate_limits`; |
# |                                              | in legacy mode also a request          |
# | `turn_context`, `thread_settings_applied`    | marker `settings`                      |
# | `task_started`, `task_complete`              | marker `turn_start`, `turn_end`        |
# | `turn_aborted`, `thread_rolled_back`         | marker `turn_aborted`, `rollback`      |
# | `compacted`, `context_compacted`, and the    | marker `compaction`                    |
# | `compaction` response items                  |                                        |
# | `response_item/message`, `reasoning`         | message (`reasoning` for the second)   |
# | `response_item` call items and their outputs | tool_call, tool_result                 |
# | anything else                                | other, `recognised` from the lists     |
# | a line that is not an object, or a known     | malformed                              |
# | kind without its payload object              |                                        |
#
# A record's `extra` holds the line's payload keys the mapping does not name, with the
# line's own top-level keys other than `timestamp`, `type` and `payload` beside them
# (`ordinal`).

#: How many turns' models a `State` keeps: a request's turn is almost always the newest.
TURNS_KEPT = 8


@dataclass(frozen=True, kw_only=True)
class State:
    """What a later line's records need from the lines before it.

    * `session_id`: the thread, from the source or else line 1's `id`.
    * `cli_version`: line 1's, which picks legacy mode (`legacy`).
    * `provider`: the newest `thread_settings_applied` provider, else line 1's
      `model_provider`.
    * `context_window`: the newest `model_context_window` written (`task_started`, or a
      `token_count`'s `info`).
    * `service_tier`: the newest one a `turn_context` or the thread settings wrote.
    * `turn`: the newest turn a `task_started` or `turn_context` opened; a line that
      names no turn of its own is in it.
    * `models`: `(turn, model)` from the newest `turn_context` lines, at most
      `TURNS_KEPT` turns.
    * `previous_info`: in legacy mode, the previous `token_count`'s `info`, so a repeat
      is not counted twice.

    Each is `UNKNOWN` until a line says it. `to_json` and `from_json` carry it between
    reads, so a read that resumes where another stopped maps its lines the same.
    """

    session_id: Any = UNKNOWN
    cli_version: Any = UNKNOWN
    provider: Any = UNKNOWN
    context_window: Any = UNKNOWN
    service_tier: Any = UNKNOWN
    turn: Any = UNKNOWN
    models: tuple[tuple[str, Any], ...] = ()
    previous_info: Any = UNKNOWN

    @property
    def legacy(self) -> bool:
        """True when line 1 named a version on `LEGACY_VERSIONS`."""
        return _is_legacy(self.cli_version)

    def model_of(self, turn: Any) -> Any:
        """The model the turn's `turn_context` named, else `UNKNOWN`."""
        for kept, model in reversed(self.models):
            if kept == turn:
                return model
        return UNKNOWN

    def with_model(self, turn: str, model: Any) -> State:
        models = tuple(pair for pair in self.models if pair[0] != turn) + ((turn, model),)
        return replace(self, models=models[-TURNS_KEPT:])

    def to_json(self) -> dict[str, Any]:
        """The state as a JSON object; a field that is `UNKNOWN` is left out."""
        out: dict[str, Any] = {}
        for name in ("session_id", "cli_version", "provider", "context_window",
                     "service_tier", "turn", "previous_info"):
            value = getattr(self, name)
            if value is not UNKNOWN:
                out[name] = value
        if self.models:
            out["models"] = [list(pair) for pair in self.models]
        return out

    @classmethod
    def from_json(cls, obj: Any) -> State:
        """A state back from `to_json`'s object. Raises ValueError for anything else."""
        if not isinstance(obj, Mapping):
            raise ValueError("a Codex mapping state is a JSON object")
        values: dict[str, Any] = {}
        for name in ("session_id", "cli_version", "provider", "context_window",
                     "service_tier", "turn", "previous_info"):
            if name in obj:
                values[name] = obj[name]
        models = obj.get("models", [])
        if not isinstance(models, list) or not all(
                isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)
                for pair in models):
            raise ValueError("a Codex mapping state's models are [turn, model] pairs")
        values["models"] = tuple((turn, model) for turn, model in models)[-TURNS_KEPT:]
        return cls(**values)


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _own_turn(payload: dict) -> str | None:
    """The turn a payload names: its `turn_id`, else the one a response item carries in
    its passthrough metadata."""
    turn = _str(payload.get("turn_id"))
    if turn is None:
        meta = payload.get("internal_chat_message_metadata_passthrough")
        if isinstance(meta, dict):
            turn = _str(meta.get("turn_id"))
    return turn


def _advance(state: State, kind: str, sub: str | None, payload: dict) -> State:
    """The state after one line."""
    if kind == "session_meta":
        changes: dict[str, Any] = {}
        if state.session_id is UNKNOWN:
            thread = _str(payload.get("id")) or _str(payload.get("session_id"))
            if thread is not None:
                changes["session_id"] = thread
        if "cli_version" in payload:
            changes["cli_version"] = payload["cli_version"]
        if state.provider is UNKNOWN and "model_provider" in payload:
            changes["provider"] = payload["model_provider"]
        return replace(state, **changes) if changes else state
    if kind == "turn_context":
        turn = _str(payload.get("turn_id"))
        if turn is not None:
            state = replace(state, turn=turn)
            if "model" in payload:
                state = state.with_model(turn, payload["model"])
        if "service_tier" in payload:
            state = replace(state, service_tier=payload["service_tier"])
        return state
    if sub == "thread_settings_applied":
        settings = payload.get("thread_settings")
        if isinstance(settings, dict):
            if "model_provider_id" in settings:
                state = replace(state, provider=settings["model_provider_id"])
            if "service_tier" in settings:
                state = replace(state, service_tier=settings["service_tier"])
        return state
    if sub == "task_started":
        turn = _str(payload.get("turn_id"))
        if turn is not None:
            state = replace(state, turn=turn)
        if "model_context_window" in payload:
            state = replace(state, context_window=payload["model_context_window"])
        return state
    if sub == "token_count":
        info = payload.get("info")
        if isinstance(info, dict) and "model_context_window" in info:
            state = replace(state, context_window=info["model_context_window"])
        if state.legacy:
            state = replace(state, previous_info=info)
        return state
    return state


def _tokens(usage: dict) -> dict[str, Any]:
    """A Codex usage object's token classes, as the shape names them. Codex's input
    includes the cached tokens and its output includes reasoning, as the shape's do."""
    return {
        "input_tokens": _count(usage, "input_tokens"),
        "cache_read_input_tokens": _count(usage, "cached_input_tokens"),
        "cache_creation_input_tokens": _count(usage, "cache_write_input_tokens"),
        "output_tokens": _count(usage, "output_tokens"),
        "reasoning_output_tokens": _count(usage, "reasoning_output_tokens"),
    }


def _request(common: dict, state: State, request_id: str, usage: dict,
             turn: Any) -> Request:
    return Request(
        **common, request_id=request_id, model=state.model_of(turn),
        provider=state.provider, context_window=state.context_window,
        service_tier=state.service_tier, sidechain=False, synthetic=False, final=True,
        **_tokens(usage))


def _windows(obj: Mapping[str, Any], *, by: str, minutes: str, used: str,
             resets: str) -> tuple[LimitWindow, ...]:
    """Every sub-object whose `by` key holds a value, in the order written. Keys are not
    read for meaning, so a window under a name not seen before comes through too."""
    return tuple(
        LimitWindow(name=str(key), used_percent=_said(value, used),
                    window_minutes=_said(value, minutes), resets_at=_said(value, resets))
        for key, value in obj.items()
        if isinstance(value, Mapping) and value.get(by) is not None)


def _rollout_limits(common: dict, rate_limits: dict) -> Limits:
    windows = _windows(rate_limits, by="window_minutes", minutes="window_minutes",
                       used="used_percent", resets="resets_at")
    named = {"limit_id", "limit_name", "plan_type", "rate_limit_reached_type"} | {
        window.name for window in windows}
    return Limits(
        **{**common, "extra": {k: v for k, v in rate_limits.items() if k not in named}},
        observed_at=common["time"], source="rollout",
        limit_id=_said(rate_limits, "limit_id"),
        limit_name=_said(rate_limits, "limit_name"),
        plan_type=_said(rate_limits, "plan_type"), windows=windows,
        reached_type=_said(rate_limits, "rate_limit_reached_type"))


def _legacy_request(common: dict, state: State, before: State, payload: dict,
                    line: Any) -> Request | None:
    """A legacy `token_count`'s request: none for a null `info`, a repeat of the previous
    event's `info`, or zero input with zero output."""
    info = payload.get("info")
    if not isinstance(info, dict) or info == before.previous_info:
        return None
    usage = info.get("last_token_usage")
    if not isinstance(usage, dict):
        return None
    tokens = _tokens(usage)
    if all(tokens[name] in (UNKNOWN, 0) for name in ("input_tokens", "output_tokens")):
        return None
    extra = {k: v for k, v in info.items()
             if k not in ("last_token_usage", "model_context_window")}
    return _request({**common, "extra": extra}, state,
                    f"tc:{_thread_label(state)}:{line}", usage, state.turn)


def _thread_label(state: State) -> str:
    return state.session_id if isinstance(state.session_id, str) else "unknown"


def _text_of(content: Any) -> tuple[str, tuple[str, ...]]:
    """A content list's text blocks joined, and the block types it held."""
    if isinstance(content, str):
        return content, ("text",)
    texts: list[str] = []
    kinds: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            kinds.append(btype if isinstance(btype, str) else "?")
            if isinstance(block.get("text"), str):
                texts.append(block["text"])
    return "\n".join(texts), tuple(kinds)


def _output(payload: dict) -> tuple[str, Any]:
    """A call output's text as written, and whether it says it failed."""
    output = payload.get("output")
    is_error: Any = UNKNOWN
    if isinstance(output, dict):
        if isinstance(output.get("success"), bool):
            is_error = not output["success"]
        output = output.get("content")
    if isinstance(output, str):
        return output, is_error
    return _text_of(output)[0], is_error


def _call(common: dict, sub: str, payload: dict) -> tuple[ToolCall, str | None]:
    name = payload.get("name")
    if not isinstance(name, str):
        name = sub.removesuffix("_call")
    key = next((k for k in CALL_INPUT_KEYS if k in payload), None)
    tool_input = payload.get(key) if key is not None else None
    shown = tool_input
    if isinstance(tool_input, str):
        decoded = parse_line(tool_input)
        if decoded is not None:
            shown = decoded
    return ToolCall(**common, call_id=_said(payload, "call_id"), name=name,
                    input=tool_input, summary=tool_summary(name, shown)), key


def _marker(common: dict, name: str, **fields: Any) -> Marker:
    return Marker(**common, marker=name, fields=fields)


#: The payload keys each line kind's mapping names, so they stay out of `extra`.
_NAMED: dict[str, frozenset[str]] = {
    "session_meta": frozenset({"history_mode", "originator", "cli_version", "source",
                               "model_provider", "context_window", "forked_from_id"}),
    "token_usage_record": frozenset({"response_id", "usage", "turn_id"}),
    "turn_context": frozenset({"turn_id", "model", "service_tier"}),
    "event_msg/thread_settings_applied": frozenset({"type", "thread_settings"}),
    "event_msg/task_started": frozenset({"type", "turn_id", "model_context_window"}),
    "event_msg/task_complete": frozenset({"type", "turn_id", "duration_ms"}),
    "event_msg/token_count": frozenset({"type", "info", "rate_limits"}),
    "response_item/message": frozenset({"type", "role", "content"}),
    "response_item/reasoning": frozenset({"type", "summary", "content"}),
}
_LINE_KEYS = frozenset({"timestamp", "type", "payload"})


def line_records(raw: bytes | str, *, at: dict, state: State, agent_id: str | None = None,
                 keep_raw: bool = False, unterminated: bool = False,
                 ) -> tuple[list[Record], State]:
    """One line's records, and the state after it.

    The state handed in is the state before the line (`State()` for line 1 of a read from
    the start); nothing else is carried between lines. `at` is the record's position
    (`{"line": n, "offset": b}`) and `agent_id` the source's. `unterminated` marks the
    file's last line written without its newline; only a malformed record carries it. A
    blank line gives no records.

    Every request is `final`: Codex writes a request in one line.
    """
    entry, why = decode(raw)
    # A line of a kind on the list must carry its payload object and its time as text;
    # one that does not is a line Codex did not write as it writes that kind.
    if why is not None or (entry is not None and entry.get("type") in TOP_KINDS
                           and (not isinstance(entry.get("payload"), dict)
                                or not isinstance(entry.get("timestamp"), str))):
        data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        kind = entry.get("type") if entry is not None else None
        return [Malformed(harness=CODEX, session_id=state.session_id, agent_id=agent_id,
                          at=at, turn_id=state.turn,
                          time=_time(entry) if entry is not None else UNKNOWN,
                          raw_kind=kind if isinstance(kind, str) else "",
                          raw=data.strip(b"\r\n"), unterminated=unterminated,
                          why=why or BAD_STRUCTURE)], state
    if entry is None:
        return [], state

    kind = entry.get("type")
    kind = kind if isinstance(kind, str) else ""
    payload = entry.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    sub = None
    if kind in ("event_msg", "response_item"):
        sub = payload.get("type")
        if not isinstance(sub, str):
            data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
            return [Malformed(harness=CODEX, session_id=state.session_id,
                              agent_id=agent_id, at=at, turn_id=state.turn,
                              time=_time(entry), raw_kind=kind, raw=data.strip(b"\r\n"),
                              unterminated=unterminated, why=BAD_STRUCTURE)], state
    raw_kind = f"{kind}/{sub}" if sub is not None else kind

    before = state
    state = _advance(state, kind, sub, payload)
    named = _NAMED.get(raw_kind, frozenset({"type", "turn_id"}))
    turn = _own_turn(payload) or state.turn
    common = {
        "harness": CODEX, "session_id": state.session_id, "agent_id": agent_id, "at": at,
        "time": _time(entry), "turn_id": turn, "raw_kind": raw_kind,
        "extra": {**{k: v for k, v in entry.items() if k not in _LINE_KEYS},
                  **{k: v for k, v in payload.items() if k not in named}},
        "raw": entry if keep_raw else None,
    }

    if kind == "session_meta":
        fields = {name: _said(payload, name) for name in _NAMED["session_meta"]}
        fields["unlisted_keys"] = tuple(k for k in payload if k not in META_KEYS)
        return [_marker({**common, "turn_id": UNKNOWN}, "session_meta", **fields)], state
    if kind == "token_usage_record":
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
            return [Malformed(**{**common, "raw": data.strip(b"\r\n")},
                              unterminated=unterminated, why=BAD_STRUCTURE)], state
        request_id = (_str(payload.get("response_id"))
                      or f"tc:{_thread_label(state)}:{at.get('line')}")
        return [_request(common, state, request_id, usage, turn)], state
    if kind == "turn_context":
        return [_marker(common, "settings", model=_said(payload, "model"),
                        provider=UNKNOWN,
                        service_tier=_said(payload, "service_tier"))], state
    if raw_kind in COMPACTION_KINDS:
        return [_marker(common, "compaction", trigger=UNKNOWN, pre_tokens=UNKNOWN)], state
    if kind == "event_msg":
        return _event(common, sub, payload, state, before, at), state
    if kind == "response_item":
        return _item(common, sub, payload), state
    return [Other(**common, recognised=kind in TOP_KINDS)], state


def _time(entry: dict) -> Any:
    stamp = parse_timestamp(entry.get("timestamp"))
    return UNKNOWN if stamp is None else stamp


def _event(common: dict, sub: str, payload: dict, state: State, before: State,
           at: dict) -> list[Record]:
    if sub == "token_count":
        out: list[Record] = []
        if state.legacy:
            request = _legacy_request(common, state, before, payload, at.get("line"))
            if request is not None:
                out.append(request)
        rate_limits = payload.get("rate_limits")
        if isinstance(rate_limits, dict):
            out.append(_rollout_limits(common, rate_limits))
        return out or [Other(**common, recognised=True)]
    if sub == "thread_settings_applied":
        settings = payload.get("thread_settings")
        settings = settings if isinstance(settings, dict) else {}
        return [_marker(common, "settings", model=_said(settings, "model"),
                        provider=_said(settings, "model_provider_id"),
                        service_tier=_said(settings, "service_tier"))]
    if sub == "task_started":
        return [_marker(common, "turn_start",
                        model_context_window=_said(payload, "model_context_window"))]
    if sub == "task_complete":
        return [_marker(common, "turn_end", duration_ms=_said(payload, "duration_ms"))]
    if sub == "turn_aborted":
        return [_marker(common, "turn_aborted")]
    if sub == "thread_rolled_back":
        return [_marker(common, "rollback")]
    return [Other(**common, recognised=sub in EVENT_KINDS)]


def _item(common: dict, sub: str, payload: dict) -> list[Record]:
    if sub == "message":
        text, kinds = _text_of(payload.get("content"))
        return [Message(**common, role=_said(payload, "role"), text=text,
                        content_kinds=kinds, synthetic=False)]
    if sub == "reasoning":
        summary, summary_kinds = _text_of(payload.get("summary"))
        content, content_kinds = _text_of(payload.get("content"))
        return [Message(**common, role="assistant", reasoning=True,
                        text="\n".join(t for t in (summary, content) if t),
                        content_kinds=summary_kinds + content_kinds, synthetic=False)]
    if sub in CALL_ITEMS:
        call, key = _call(common, sub, payload)
        named = {"type", "call_id", "name"} | ({key} if key else set())
        extra = {k: v for k, v in common["extra"].items() if k not in named}
        return [replace(call, extra=extra)]
    if sub in OUTPUT_ITEMS:
        output, is_error = _output(payload)
        extra = {k: v for k, v in common["extra"].items()
                 if k not in ("call_id", "output")}
        return [ToolResult(**{**common, "extra": extra}, call_id=_said(payload, "call_id"),
                           output=output, is_error=is_error,
                           size_bytes=len(output.encode("utf-8", "replace")))]
    return [Other(**common, recognised=sub in RESPONSE_KINDS)]


# --- reading forward ---------------------------------------------------------------------

#: The bytes a line must hold to give a record of each kind, for a reader that decodes
#: only the lines a `kinds` filter can use. A kind with no entry can come from any line,
#: so asking for it decodes every line.
MARKER_BYTES: dict[str, tuple[bytes, ...]] = {
    "request": (b'"token_usage_record"',),
    "marker": (b'"session_meta"', b'"turn_context"', b'"thread_settings_applied"',
               b'"task_started"', b'"task_complete"', b'"turn_aborted"',
               b'"thread_rolled_back"', b'"compacted"', b'"context_compacted"',
               b'"compaction"', b'"context_compaction"'),
    "limits": (b'"rate_limits"',),
}

#: In legacy mode a request also comes from a `token_count` event.
LEGACY_REQUEST_BYTES = (b'"token_count"',)

#: Lines decoded whatever the filter, because they change `State`: line 1, a turn's
#: context and start, the thread settings, and a written context window.
STATE_BYTES = (b'"session_meta"', b'"turn_context"', b'"thread_settings_applied"',
               b'"task_started"', b'"model_context_window"')

#: In legacy mode every `token_count` changes `State` (`previous_info`).
LEGACY_STATE_BYTES = (b'"token_count"',)


def needs_decode(raw: bytes, kinds: frozenset[str] | set[str] | None, *,
                 legacy: bool = False) -> bool:
    """Whether a reader asking for `kinds` must decode this raw line. `legacy` is the
    mapping state's (`State.legacy`).

    The rule: it never skips a line the full decode would turn into a record of a kind
    asked for, or that changes such a record (the state it reads). With `kinds` None, or
    a kind `MARKER_BYTES` has no entry for, every line is decoded.
    """
    if kinds is None:
        return True
    for kind in kinds:
        markers = MARKER_BYTES.get(kind)
        if markers is None:
            return True
        if kind == "request" and legacy:
            markers = markers + LEGACY_REQUEST_BYTES
        if any(marker in raw for marker in markers):
            return True
    state = STATE_BYTES + LEGACY_STATE_BYTES if legacy else STATE_BYTES
    return any(marker in raw for marker in state)


class Mapper:
    """Lines to records, forward, carrying `State` between them.

    Codex writes each request in one line, so every request comes `final`, once, and
    `closed` stays empty and `open_key` None; they are here so a caller reads every
    harness's mapper alike. A read that resumes where another stopped hands in that
    read's `state`.

    `session_id` is the source's thread (None to take line 1's) and `agent_id` the
    source's.
    """

    def __init__(self, *, session_id: str | None = None, agent_id: str | None = None,
                 keep_raw: bool = False, state: State | None = None):
        state = state if state is not None else State()
        if session_id is not None:
            state = replace(state, session_id=session_id)
        self.state = state
        self.agent_id = agent_id
        self.keep_raw = keep_raw
        self.closed: list[str] = []
        self.open_key: str | None = None

    def feed(self, raw: bytes | str, *, line: int, offset: int,
             unterminated: bool = False) -> list[Record]:
        """One line, `line` its 1-based number and `offset` the byte offset of its start.
        Returns its records, in order."""
        records, self.state = line_records(
            raw, at={"line": line, "offset": offset}, state=self.state,
            agent_id=self.agent_id, keep_raw=self.keep_raw, unterminated=unterminated)
        return records

    def finish(self) -> list[Record]:
        """Nothing: no Codex request is left open at the end of a read."""
        return []


def read_records(path: str | Path, *, session_id: str | None = None,
                 agent_id: str | None = None, kinds: set[str] | frozenset[str] | None = None,
                 keep_raw: bool = False) -> Iterator[Record]:
    """Every record of one rollout, forward, one line held at a time.

    The file's size is taken from the open handle once, and no byte past it is read. A
    last line with no newline is read too; if it will not decode, its malformed record is
    `unterminated`. With `kinds`, only records of those kinds come back, and a line that
    cannot give one or change the state is not decoded (`needs_decode`); the records that
    do come back are the same as without the filter. Raises OSError when the file cannot
    be opened or read, and ValueError for a compressed rollout, which this does not read.
    """
    if str(path).endswith(".zst"):
        raise ValueError(f"{path} is a compressed rollout; it is not read")
    wanted = frozenset(kinds) if kinds is not None else None
    mapper = Mapper(session_id=session_id, agent_id=agent_id, keep_raw=keep_raw)
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        offset = 0
        number = 0
        while offset < size:
            data = handle.readline(size - offset)
            if not data:
                break
            number += 1
            start, offset = offset, offset + len(data)
            if wanted is not None and not needs_decode(data, wanted,
                                                       legacy=mapper.state.legacy):
                continue
            for record in mapper.feed(data, line=number, offset=start,
                                      unterminated=not data.endswith(b"\n")):
                if wanted is None or record.kind in wanted:
                    yield record


# --- limits readings from the app-server -------------------------------------------------

def _app_server_limits(snapshot: Any, observed_at: Any, at: dict,
                       key: str | None) -> Limits:
    if not isinstance(snapshot, Mapping):
        raise ValueError("a rate-limit snapshot is a JSON object")
    windows = _windows(snapshot, by="usedPercent", minutes="windowDurationMins",
                       used="usedPercent", resets="resetsAt")
    named = {"limitId", "limitName", "planType", "rateLimitReachedType"} | {
        window.name for window in windows}
    limit_id = _said(snapshot, "limitId")
    if limit_id is UNKNOWN and key is not None:
        limit_id = key
    return Limits(
        harness=CODEX, session_id=None, at=at, time=observed_at,
        raw_kind="app-server/" + at["app_server"],
        extra={k: v for k, v in snapshot.items() if k not in named},
        observed_at=observed_at, source="app-server", limit_id=limit_id,
        limit_name=_said(snapshot, "limitName"), plan_type=_said(snapshot, "planType"),
        windows=windows, reached_type=_said(snapshot, "rateLimitReachedType"))


def limits_from_app_server(payload: Mapping[str, Any], observed_at: float) -> list[Limits]:
    """Codex's app-server rate-limit payload as limits records, `source` `app-server`.

    `payload` is an `account/rateLimits/read` response or an
    `account/rateLimits/updated` notification's params, and `observed_at` the epoch
    seconds the caller received it. The records are one per entry of
    `rateLimitsByLimitId` when it holds any (an entry with no `limitId` takes its key),
    else one from `rateLimits`. Each window is a sub-object carrying `usedPercent`, which
    Codex always writes on one, named by its key: `used_percent` from `usedPercent`,
    `window_minutes` from `windowDurationMins`, `resets_at` from `resetsAt`. Credits and
    the rest ride in `extra`. The reading belongs to the account, not a thread, so `session_id` is None.

    Values come as written. Codex says a null in an update notification means the value
    was not sent and does not clear an earlier one; merging readings is the caller's.
    Raises ValueError for a payload with no snapshot object.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("an app-server rate-limit payload is a JSON object")
    by_id = payload.get("rateLimitsByLimitId")
    if isinstance(by_id, Mapping) and by_id:
        return [_app_server_limits(snapshot, observed_at,
                                   {"app_server": "rateLimitsByLimitId", "key": str(key)},
                                   str(key))
                for key, snapshot in by_id.items()]
    return [_app_server_limits(payload.get("rateLimits"), observed_at,
                               {"app_server": "rateLimits"}, None)]


# --- import marks -------------------------------------------------------------------------

#: The file, beside the sessions root, where Codex lists the threads it imported.
IMPORTS_FILE = "external_agent_session_imports.json"
#: Line 1's `originator` on a thread Codex imported.
IMPORT_ORIGINATOR = "Codex Desktop"
#: How an imported thread's turn ids begin.
IMPORT_TURN_PREFIX = "external-import-turn-"
#: Why `listed` is unknown when the imports file is absent: the user never imported.
IMPORTS_NOT_FOUND = "imports file not found"


def imports_path(sessions_root: str | Path) -> Path:
    """Where Codex keeps its imports file: `<sessions root>/../` + `IMPORTS_FILE`."""
    return Path(sessions_root).parent / IMPORTS_FILE


@dataclass(frozen=True)
class Imports:
    """The imports file, read once: the thread ids it lists, or None with `reason`
    ("imports file not found", or "imports file unreadable: <why>")."""

    path: str
    ids: frozenset[str] | None
    reason: str | None = None


def read_imports(path: str | Path) -> Imports:
    """Read the imports file at `path` (`imports_path` gives Codex's place for it). Never
    raises: a file that cannot be read or parsed is an `Imports` with its reason."""
    label = str(path)
    try:
        text = Path(path).read_bytes()
    except FileNotFoundError:
        return Imports(label, None, IMPORTS_NOT_FOUND)
    except OSError as exc:
        return Imports(label, None, f"imports file unreadable: {exc.strerror or exc}")
    data, why = decode(text)
    if data is None:
        what = "it is not a JSON object" if why == NOT_OBJECT else "it is not JSON"
        return Imports(label, None, f"imports file unreadable: {what}")
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return Imports(label, None, "imports file unreadable: it holds no records list")
    return Imports(label, frozenset(
        r["imported_thread_id"] for r in records
        if isinstance(r, dict) and _str(r.get("imported_thread_id"))))


@dataclass(frozen=True)
class Head:
    """The head of a rollout: line 1's `session_meta` payload (None when line 1 is not
    one) and the first turn id any line names (None when no line does yet)."""

    meta: dict | None
    first_turn_id: str | None


def read_head(path: str | Path) -> Head:
    """Read forward from line 1 until the first turn id, reading no byte past the size
    the file had when opened. Raises OSError when the file cannot be read."""
    meta: dict | None = None
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        offset = 0
        number = 0
        while offset < size:
            data = handle.readline(size - offset)
            if not data:
                break
            number += 1
            offset += len(data)
            entry = parse_line(data)
            payload = entry.get("payload") if entry is not None else None
            if not isinstance(payload, dict):
                continue
            if number == 1 and entry.get("type") == "session_meta":
                meta = payload
            turn = _str(payload.get("turn_id"))
            if turn is not None:
                return Head(meta, turn)
    return Head(meta, None)


@dataclass(frozen=True)
class ImportMarks:
    """The three marks of an imported thread, each as found. Whether the thread was
    imported, and what to do about it, is the caller's.

    * `originator`: line 1's `originator` is `IMPORT_ORIGINATOR`; `UNKNOWN` with no
      line 1 `session_meta`.
    * `turn_id`: the first turn id begins `IMPORT_TURN_PREFIX`; `UNKNOWN` before any
      turn is written.
    * `listed`: the thread is in the imports file; `UNKNOWN` when the file was not read,
      with `listed_reason` saying why, so a missing listing never reads as "not
      imported".

    `raw_originator` is line 1's `originator` as written and `imports_path` the file
    asked.
    """

    originator: Any
    turn_id: Any
    listed: Any
    listed_reason: str | None
    raw_originator: Any
    imports_path: str

    def as_dict(self) -> dict[str, Any]:
        """The marks as JSON-ready data: an unknown mark left out and named in
        `"unknown"`."""
        out: dict[str, Any] = {}
        unknown = []
        for name in ("originator", "turn_id", "listed", "listed_reason", "raw_originator",
                     "imports_path"):
            value = getattr(self, name)
            if value is UNKNOWN:
                unknown.append(name)
            else:
                out[name] = value
        if unknown:
            out["unknown"] = unknown
        return out


def import_marks(head: Head, imports: Imports, *, thread_id: str | None = None
                 ) -> ImportMarks:
    """A rollout's import marks from its head and the imports file. `thread_id` is the
    thread to look up, by default line 1's `id`."""
    meta = head.meta
    raw_originator = meta.get("originator", UNKNOWN) if meta is not None else UNKNOWN
    if thread_id is None and meta is not None:
        thread_id = _str(meta.get("id")) or _str(meta.get("session_id"))
    if imports.ids is None:
        listed, reason = UNKNOWN, imports.reason
    elif thread_id is None:
        listed, reason = UNKNOWN, "the rollout names no thread id"
    else:
        listed, reason = thread_id in imports.ids, None
    return ImportMarks(
        originator=(UNKNOWN if raw_originator is UNKNOWN
                    else raw_originator == IMPORT_ORIGINATOR),
        turn_id=(UNKNOWN if head.first_turn_id is None
                 else head.first_turn_id.startswith(IMPORT_TURN_PREFIX)),
        listed=listed, listed_reason=reason, raw_originator=raw_originator,
        imports_path=imports.path)


# --- spawned threads ----------------------------------------------------------------------
#
# A thread Codex spawns is a thread of its own, with its own rollout. Both sides name the
# link. The spawning thread writes an `event_msg` `item_completed` whose `item` is a
# `SubAgentActivity` (kinds `started`, `interacted`, `interrupted`, `completed`), naming the
# child at `agent_thread_id` and its task at `agent_path`; on `started`, the item's `id` is
# the spawn call's `call_id`. The child's line 1 names its parent at
# `source.subagent.thread_spawn` (`parent_thread_id`, `depth`, `agent_path`,
# `agent_nickname`, `agent_role`), and again at its own `parent_thread_id` and
# `forked_from_id`. Codex's state database has a table for these links, but Codex 0.153.0
# left it empty for a real spawn, so it is not read.

#: The `item_completed` item type that names a spawned child thread.
SPAWN_ITEM = "SubAgentActivity"
#: The bytes a line must hold to be a spawn item, so a scan decodes no other line.
SPAWN_ITEM_BYTES = b'"SubAgentActivity"'
#: The spawn item kind whose `id` is the spawn call's `call_id`.
SPAWN_STARTED = "started"
#: The tool call that spawns a thread, by its `response_item` payload `name`.
SPAWN_CALL = "spawn_agent"
#: The `event_msg` payload type Codex 0.153.0 may persist for a spawn, never seen written.
SPAWN_EVENT = "sub_agent_activity"
#: The bytes a line must hold to be a spawn call or a spawn event, so a scan decodes no
#: other line.
SPAWN_CALL_BYTES = b'"spawn_agent"'
SPAWN_EVENT_BYTES = b'"sub_agent_activity"'


@dataclass(frozen=True)
class SpawnLink:
    """What a spawned thread's line 1 says of its spawn, from
    `source.subagent.thread_spawn`, each as written (a null as None) and `UNKNOWN` where
    line 1 does not say: `parent_thread_id`, `depth` (1 for a child of a main thread),
    `agent_path` (the task's path, `/root/<task>`), `agent_nickname` and `agent_role`."""

    parent_thread_id: Any
    depth: Any
    agent_path: Any
    agent_nickname: Any
    agent_role: Any

    def as_dict(self) -> dict[str, Any]:
        """The link as JSON-ready data: an unknown fact left out and named in
        `"unknown"`."""
        out: dict[str, Any] = {}
        unknown = []
        for name in ("parent_thread_id", "depth", "agent_path", "agent_nickname",
                     "agent_role"):
            value = getattr(self, name)
            if value is UNKNOWN:
                unknown.append(name)
            else:
                out[name] = value
        if unknown:
            out["unknown"] = unknown
        return out


def spawn_link(meta: Mapping[str, Any] | None) -> SpawnLink | None:
    """The spawn link line 1's payload `meta` names, or None when it names no spawn (a
    thread the user started, or no line 1 at all). A fact the `thread_spawn` object leaves
    out is taken from line 1's own key of that name when it has one."""
    if not isinstance(meta, Mapping):
        return None
    source = meta.get("source")
    sub = source.get("subagent") if isinstance(source, Mapping) else None
    spawn = sub.get("thread_spawn") if isinstance(sub, Mapping) else None
    if not isinstance(spawn, Mapping):
        return None

    def fact(name: str) -> Any:
        if name in spawn:
            return spawn[name]
        return meta.get(name, UNKNOWN)

    return SpawnLink(parent_thread_id=fact("parent_thread_id"), depth=fact("depth"),
                     agent_path=fact("agent_path"), agent_nickname=fact("agent_nickname"),
                     agent_role=fact("agent_role"))


@dataclass(frozen=True)
class Spawned:
    """One child thread a rollout's spawn items name, gathered over every item that names
    it: `thread_id`, `agent_path` (the first an item names, else `UNKNOWN`), `call_id`
    (the `started` item's `id`, the spawn call; `UNKNOWN` when no `started` item was
    read) and `kinds`, each item's kind in file order."""

    thread_id: str
    agent_path: Any
    call_id: Any
    kinds: tuple[str, ...]


@dataclass(frozen=True)
class Spawns:
    """A rollout's spawn items, read once: line 1's `session_meta` payload (None when
    line 1 is not one), the children in the order first named, `size`, the bytes read,
    and `unread_spelling`, what the rollout holds that marks a spawn in a spelling this
    reader does not take children from (a `spawn_agent` call, a `sub_agent_activity`
    event), the first one seen, else None."""

    meta: dict | None
    children: tuple[Spawned, ...]
    size: int
    unread_spelling: str | None = None


def _unread_spelling(data: bytes) -> str | None:
    """What line `data` is when it marks a spawn without a spawn item: a `spawn_agent`
    call or a `sub_agent_activity` event. Decodes only a line holding one of the two
    names in quotes."""
    call = SPAWN_CALL_BYTES in data
    if not call and SPAWN_EVENT_BYTES not in data:
        return None
    entry = parse_line(data)
    payload = entry.get("payload") if entry is not None else None
    if not isinstance(payload, dict):
        return None
    if call and entry.get("type") == "response_item" and payload.get("name") == SPAWN_CALL:
        return f"a `{SPAWN_CALL}` call"
    if entry.get("type") == "event_msg" and payload.get("type") == SPAWN_EVENT:
        return f"a `{SPAWN_EVENT}` event"
    return None


def read_spawns(path: str | Path, *, thread_id: str | None = None,
                end: int | None = None) -> Spawns:
    """The child threads one rollout's spawn items name.

    Reads forward from line 1, no byte past `end` (a listing's size) or the size the file
    has when opened, whichever is less; only line 1 and lines holding `SPAWN_ITEM_BYTES`
    are decoded. A spawn item counts when its `item.type` is `SPAWN_ITEM` and it names an
    `agent_thread_id`. An item whose `thread_id` names a thread other than this one
    (`thread_id`, else line 1's `id`) is history the rollout carries from another thread,
    and is not this thread's spawn. A last line still being written does not decode and
    is skipped. A `spawn_agent` call or a `sub_agent_activity` event is noted in
    `unread_spelling`, so a rollout that spawned in a spelling not read here is not
    taken for one that spawned nothing.

    Raises OSError when the file cannot be read, and ValueError for a compressed rollout,
    which this does not read.
    """
    if str(path).endswith(".zst"):
        raise ValueError(f"{path} is a compressed rollout; it is not read")
    meta: dict | None = None
    order: list[str] = []
    paths: dict[str, Any] = {}
    calls: dict[str, Any] = {}
    kinds: dict[str, list[str]] = {}
    unread: str | None = None
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if end is not None:
            size = min(size, end)
        offset = 0
        number = 0
        while offset < size:
            data = handle.readline(size - offset)
            if not data:
                break
            number += 1
            offset += len(data)
            if number == 1:
                entry = parse_line(data)
                if entry is not None and entry.get("type") == "session_meta":
                    payload = entry.get("payload")
                    meta = payload if isinstance(payload, dict) else None
                    if thread_id is None and meta is not None:
                        thread_id = _str(meta.get("id"))
                continue
            if SPAWN_ITEM_BYTES not in data:
                if unread is None:
                    unread = _unread_spelling(data)
                continue
            entry = parse_line(data)
            payload = entry.get("payload") if entry is not None else None
            if (entry is None or entry.get("type") != "event_msg"
                    or not isinstance(payload, dict)
                    or payload.get("type") != "item_completed"):
                continue
            item = payload.get("item")
            if not isinstance(item, dict) or item.get("type") != SPAWN_ITEM:
                continue
            child = _str(item.get("agent_thread_id"))
            writer = _str(payload.get("thread_id"))
            if child is None or (writer is not None and thread_id is not None
                                 and writer != thread_id):
                continue
            if child not in kinds:
                order.append(child)
                kinds[child] = []
                paths[child] = UNKNOWN
                calls[child] = UNKNOWN
            kind = item.get("kind")
            kinds[child].append(kind if isinstance(kind, str) else "?")
            if paths[child] is UNKNOWN and _str(item.get("agent_path")) is not None:
                paths[child] = item["agent_path"]
            if (kind == SPAWN_STARTED and calls[child] is UNKNOWN
                    and _str(item.get("id")) is not None):
                calls[child] = item["id"]
    return Spawns(meta, tuple(Spawned(child, paths[child], calls[child],
                                      tuple(kinds[child])) for child in order), size,
                  unread)
