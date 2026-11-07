"""The one shape: the record kinds every harness's session records map into.

Claude Code transcripts, Codex rollouts and OpenCode's database each map, line by line or
row by row, into the same eight record kinds with the same field names. A mapping owns
which lines exist and what their fields say; which records count for anything is the
caller's.

Every record is a frozen dataclass carrying the common fields (`Record`) and its kind's
own. `kind` is a class attribute, so `record.kind` reads like a field and is written into
the JSON form like one.

**Unknown.** `UNKNOWN` is one sentinel, neither None nor 0. None means the harness wrote
a null, or wrote nothing where the field has a meaning; 0 means it wrote zero; `UNKNOWN`
means the harness did not say. `bool(UNKNOWN)` raises TypeError, so `x or 0` fails loudly
instead of turning unknown into zero, and so does arithmetic on it. A field a harness
never carries is unknown on every record of that harness.

**The shape version.** `SHAPE` is an integer written at the top of every JSON document
sessions writes (`{"shape": 1, ...}`). Adding a field or a kind keeps the number;
renaming or removing one, or changing what a field means, raises it. A reader treats an
unknown `kind` as `other` and ignores fields it does not know (`from_json` does both). The
shape version is separate from the Python interface number.

**Names.** Python attributes and JSON keys are the same snake_case names. Where the
OpenTelemetry GenAI conventions have an attribute, the name is its tail with dots made
underscores: `session_id` is `gen_ai.conversation.id`, `model` and `response_model` are
`gen_ai.request.model` and `gen_ai.response.model`, `provider` is `gen_ai.provider.name`,
`request_id` is `gen_ai.response.id`, the token counts are the `gen_ai.usage.*` names
(`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens`, `reasoning_output_tokens`), and a tool's `name` and
`call_id` are `gen_ai.tool.name` and `gen_ai.tool.call.id`. The names were checked
against `OTEL_CONVENTIONS`. The conventions are experimental: a name they later change is
a shape version bump here, never a silent rename.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar

#: The shape version. See the module docstring for when it changes.
SHAPE = 1

#: The OpenTelemetry semantic conventions release the field names were checked against.
#: The GenAI attributes are kept in their own conventions repository from that release
#: on, under the same names.
OTEL_CONVENTIONS = "1.41.0"

#: The three harnesses, as `Record.harness` names them.
CLAUDE = "claude"
CODEX = "codex"
OPENCODE = "opencode"
HARNESSES = (CLAUDE, CODEX, OPENCODE)


# --- unknown ----------------------------------------------------------------------------

class _Unknown:
    """The type of `UNKNOWN`. There is one instance; copying or pickling it gives it back."""

    _instance: ClassVar[_Unknown | None] = None
    __slots__ = ()

    def __new__(cls) -> _Unknown:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        raise TypeError("UNKNOWN has no truth value: test it with `is UNKNOWN`")

    def __repr__(self) -> str:
        return "UNKNOWN"

    def __reduce__(self) -> str:
        return "UNKNOWN"

    def __copy__(self) -> _Unknown:
        return self

    def __deepcopy__(self, memo: dict) -> _Unknown:
        return self


#: The value of a field the harness did not say. Test it with `is UNKNOWN`.
UNKNOWN: Any = _Unknown()


def known(value: Any) -> bool:
    """True when `value` is anything but `UNKNOWN`, None and 0 included."""
    return value is not UNKNOWN


# --- the records ------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True, slots=True)
class Record:
    """What every record carries.

    * `harness`: `claude`, `codex` or `opencode`.
    * `session_id`: the harness's own id: the Claude session id, the Codex thread id or the
      OpenCode session id. A Claude subagent's records carry their parent session's id.
    * `agent_id`: the subagent's id when the record comes from a subagent's file or child
      session, else None.
    * `at`: where the record came from, `{"line": n, "offset": b}` in a file (1-based line,
      byte offset of the line's start) or `{"table": t, "id": id}` in OpenCode's
      database. Records from one line or row share it.
    * `time`: epoch seconds, or `UNKNOWN`.
    * `turn_id`: the harness's turn key (Claude's `promptId`, Codex's `turn_id`, OpenCode's
      user message id the assistant message answers), or `UNKNOWN`.
    * `raw_kind`: the harness's own kind for the line or row (`assistant`,
      `system/compact_boundary`, `event_msg/token_count`, `part/step-finish`).
    * `extra`: the source object's top-level keys the mapping does not name, as decoded.
      Treat it as read-only.
    * `raw`: the whole decoded line or row, only when the read asked for it; else None.
    """

    kind: ClassVar[str] = ""

    harness: str
    session_id: Any = UNKNOWN
    agent_id: str | None = None
    at: Mapping[str, Any] = field(default_factory=dict)
    time: Any = UNKNOWN
    turn_id: Any = UNKNOWN
    raw_kind: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)
    raw: Any = None


@dataclass(frozen=True, kw_only=True, slots=True)
class Request(Record):
    """One model request.

    `request_id` is the dedupe key and never changes between reads of the same request.
    `input_tokens` is the whole prompt, cache reads and writes included; `output_tokens`
    includes reasoning. `cache_creation_5m_input_tokens` and
    `cache_creation_1h_input_tokens` split the cache write by its lifetime where the
    harness says. `cost_usd` is the harness's own figure. `context_window` is set only
    when the harness wrote one. `final` is false while more of the request can still
    arrive in a later read.
    """

    kind: ClassVar[str] = "request"

    request_id: str = ""
    model: Any = UNKNOWN
    response_model: Any = UNKNOWN
    provider: Any = UNKNOWN
    input_tokens: Any = UNKNOWN
    cache_read_input_tokens: Any = UNKNOWN
    cache_creation_input_tokens: Any = UNKNOWN
    cache_creation_5m_input_tokens: Any = UNKNOWN
    cache_creation_1h_input_tokens: Any = UNKNOWN
    output_tokens: Any = UNKNOWN
    reasoning_output_tokens: Any = UNKNOWN
    service_tier: Any = UNKNOWN
    speed: Any = UNKNOWN
    cost_usd: Any = UNKNOWN
    context_window: Any = UNKNOWN
    start_time: Any = UNKNOWN
    sidechain: Any = UNKNOWN
    synthetic: Any = UNKNOWN
    final: bool = True


@dataclass(frozen=True, kw_only=True, slots=True)
class Message(Record):
    """A message, or a thinking or reasoning item (`reasoning` true).

    `role` is `user`, `assistant`, `system` or `developer`. `text` joins the text it held.
    `content_kinds` names the block or part types it held. `model` is a user message's
    requested model where the harness writes one. `flags` holds the harness's own
    booleans on the line, verbatim (`isMeta`, `isCompactSummary`, `isApiErrorMessage`,
    OpenCode's `summary`). `origin` is where the harness says the message came from.

    `injected` says whether something other than the person at the session's prompt
    submitted the message: the harness, a plugin, a channel, a peer session or a
    background task. It is read from the field the harness writes about where the message
    came from, never from the message's text. True when that field names such a
    submitter, or when the harness marks the message as one it composed or summarised
    itself; false when it names the person, or the message is the model's own; `UNKNOWN`
    when the harness writes no such field for the message, or a value sessions does not
    know. Where no source field is written, a fixed wrapper the harness puts around text
    it composed can make the message true, and nothing makes it false. Each harness's
    mapping keeps its own list of the values and wrappers it reads.
    """

    kind: ClassVar[str] = "message"

    role: Any = UNKNOWN
    text: str = ""
    reasoning: bool = False
    content_kinds: tuple[str, ...] = ()
    model: Any = UNKNOWN
    flags: Mapping[str, Any] = field(default_factory=dict)
    origin: Any = UNKNOWN
    synthetic: Any = UNKNOWN
    injected: Any = UNKNOWN


@dataclass(frozen=True, kw_only=True, slots=True)
class ToolCall(Record):
    """A tool call: `call_id`, `name`, `input` as written, `server` (the MCP server when
    the harness names one, else None) and `summary`, the name and its identifying
    argument on one line (`tool_summary`)."""

    kind: ClassVar[str] = "tool_call"

    call_id: Any = UNKNOWN
    name: Any = UNKNOWN
    input: Any = None
    server: str | None = None
    summary: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class ToolResult(Record):
    """A tool's result: `call_id`, `output` (its text as written), `is_error`, and
    `size_bytes`, the UTF-8 size of `output`."""

    kind: ClassVar[str] = "tool_result"

    call_id: Any = UNKNOWN
    output: str = ""
    is_error: Any = UNKNOWN
    size_bytes: int = 0


#: The names a marker is given, and the fields each carries.
MARKERS: Mapping[str, tuple[str, ...]] = {
    "compaction": ("trigger", "pre_tokens"),
    "turn_start": ("model_context_window",),
    "turn_end": ("duration_ms",),
    "turn_aborted": (),
    "rollback": (),
    "settings": ("model", "provider", "service_tier"),
    "title": ("title", "source"),
    "session_meta": ("history_mode", "originator", "cli_version", "source",
                     "model_provider", "context_window", "forked_from_id",
                     "unlisted_keys"),
}


@dataclass(frozen=True, kw_only=True, slots=True)
class Marker(Record):
    """A point in a session: `marker` names it, and its own fields are read as attributes
    (`m.trigger`, `m.duration_ms`).

    Each marker's fields differ, so they are held in `fields` and reached through the
    attribute; `MARKERS` lists the fields each marker name carries. A field the harness
    did not say is `UNKNOWN`. A name `fields` does not hold raises AttributeError.
    """

    kind: ClassVar[str] = "marker"

    marker: str = ""
    fields: Mapping[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return object.__getattribute__(self, "fields")[name]
        except KeyError:
            raise AttributeError(
                f"marker {self.marker!r} has no field {name!r}") from None


@dataclass(frozen=True, kw_only=True, slots=True)
class LimitWindow:
    """One window of a limits reading: `name` (the harness's key for it, such as
    `primary`), `used_percent`, `window_minutes` and `resets_at` (epoch seconds)."""

    name: str
    used_percent: Any = UNKNOWN
    window_minutes: Any = UNKNOWN
    resets_at: Any = UNKNOWN


@dataclass(frozen=True, kw_only=True, slots=True)
class Limits(Record):
    """One rate-limit reading: `observed_at` (epoch seconds), `source` (`rollout` or
    `app-server`), `limit_id`, `limit_name`, `plan_type`, `windows` in the order the
    harness wrote them, and `reached_type`. Everything else, credits included, rides in
    `extra`."""

    kind: ClassVar[str] = "limits"

    observed_at: Any = UNKNOWN
    source: Any = UNKNOWN
    limit_id: Any = UNKNOWN
    limit_name: Any = UNKNOWN
    plan_type: Any = UNKNOWN
    windows: tuple[LimitWindow, ...] = ()
    reached_type: Any = UNKNOWN


#: The values of `Malformed.why`.
UNDECODABLE = "undecodable"
NOT_OBJECT = "not_object"
BAD_STRUCTURE = "bad_structure"


@dataclass(frozen=True, kw_only=True, slots=True)
class Malformed(Record):
    """A line or row the mapping cannot read, yielded rather than dropped.

    `raw` holds its bytes (always, whatever the read asked for). `unterminated` is true
    for a file's last line written without its newline: the harness may still be writing
    it. `why` is `undecodable` (not JSON), `not_object` (JSON but not an object) or
    `bad_structure` (a kind the mapping recognises whose required structure is wrong). A
    line of a recognised kind that cannot be read is never `other`, so no caller reads
    it as nothing there.
    """

    kind: ClassVar[str] = "malformed"

    unterminated: bool = False
    why: str = UNDECODABLE


@dataclass(frozen=True, kw_only=True, slots=True)
class Other(Record):
    """A line or row no other kind maps, yielded rather than dropped. `recognised` says
    whether the harness's kind is on the mapping's list of kinds it knows."""

    kind: ClassVar[str] = "other"

    recognised: bool = False


#: Each kind's class, by its `kind`.
KINDS: Mapping[str, type[Record]] = {
    cls.kind: cls for cls in (Request, Message, ToolCall, ToolResult, Marker, Limits,
                              Malformed, Other)}


# --- tool summaries ---------------------------------------------------------------------

#: The tool input key that best names what a call was about, most specific first. A tool
#: with none of them falls back to its whole input as compact JSON, so a new tool still
#: shows, badly at worst.
SUMMARY_KEYS = ("file_path", "path", "notebook_path", "command", "pattern", "url", "query",
                "prompt", "description", "skill", "old_string", "content")

#: Where a summary's argument is cut.
SUMMARY_CHARS = 160


def _flat(text: str, limit: int, redact: Callable[[str], str] | None = None) -> str:
    """One line, cut at `limit` characters, the cut admitted. With `redact`, the whole
    text is redacted first and cut after, so no secret is cut short of the shape that
    would match it."""
    text = str(text)
    if redact is not None:
        text = redact(text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [+{len(text) - limit} chars]"


def tool_summary(name: Any, tool_input: Any, *,
                 redact: Callable[[str], str] | None = None) -> str:
    """`name(argument)` on one line: the tool's name and the first of `SUMMARY_KEYS` its
    input holds as non-blank text, else its input as compact JSON, the argument cut at
    `SUMMARY_CHARS` characters.

    `redact`, a function from text to text, is applied to the name and to the whole
    argument before the cut. A record's own `summary` is made without it."""
    label = name if isinstance(name, str) else "?"
    if redact is not None:
        label = redact(label)
    if isinstance(tool_input, dict):
        arg = next((_flat(tool_input[key], SUMMARY_CHARS, redact) for key in SUMMARY_KEYS
                    if isinstance(tool_input.get(key), str) and tool_input[key].strip()),
                   None)
        if arg is None:
            arg = (_flat(json.dumps(tool_input, default=str, sort_keys=True), SUMMARY_CHARS,
                         redact)
                   if tool_input else "")
    elif tool_input is None:
        arg = ""
    else:
        arg = _flat(json.dumps(tool_input, default=str), SUMMARY_CHARS, redact)
    return f"{label}({arg})"


# --- the JSON form ----------------------------------------------------------------------

def _plain(value: Any) -> Any:
    """A field's value as JSON-ready data."""
    if isinstance(value, LimitWindow):
        out: dict[str, Any] = {}
        unknown = []
        for f in fields(value):
            item = getattr(value, f.name)
            if item is UNKNOWN:
                unknown.append(f.name)
            else:
                out[f.name] = _plain(item)
        if unknown:
            out["unknown"] = unknown
        return out
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def to_json(record: Record) -> dict[str, Any]:
    """A record as a JSON object: `kind` first, then every field by its own name.

    A field that is `UNKNOWN` is left out and named in the object's `"unknown"` list.
    `raw` is left out when it is None; a malformed record's bytes are written as text when
    they are UTF-8, else as `"raw_base64"`. A marker's own fields sit beside the common
    ones, with its unknown fields in the same list.
    """
    out: dict[str, Any] = {"kind": record.kind}
    unknown: list[str] = []
    for f in fields(record):
        name = f.name
        value = getattr(record, name)
        if name == "fields" and isinstance(record, Marker):
            for key, item in value.items():
                if item is UNKNOWN:
                    unknown.append(key)
                else:
                    out[key] = _plain(item)
            continue
        if name == "raw":
            if value is None:
                continue
            if isinstance(value, (bytes, bytearray)):
                try:
                    out["raw"] = bytes(value).decode("utf-8")
                except UnicodeDecodeError:
                    out["raw_base64"] = base64.b64encode(bytes(value)).decode("ascii")
                continue
        if value is UNKNOWN:
            unknown.append(name)
        else:
            out[name] = _plain(value)
    if unknown:
        out["unknown"] = unknown
    return out


def _window(obj: Mapping[str, Any]) -> LimitWindow:
    unknown = set(obj.get("unknown") or ())

    def value(name: str) -> Any:
        return obj[name] if name in obj and name not in unknown else UNKNOWN

    return LimitWindow(name=str(obj.get("name", "")), used_percent=value("used_percent"),
                       window_minutes=value("window_minutes"),
                       resets_at=value("resets_at"))


#: The keys of a record's JSON object that are not a marker's own fields.
_COMMON = frozenset(f.name for f in fields(Record)) | {"kind", "unknown", "marker",
                                                       "raw_base64"}


def from_json(obj: Mapping[str, Any]) -> Record:
    """A record back from its JSON object, by the reader's rule: an unknown `kind` reads
    as `other` (not recognised), a key no field has is ignored, and a field named in
    `"unknown"` or missing from the object is `UNKNOWN`. A marker keeps every key that is
    not a common field as one of its own, so a marker or a marker field this reader does
    not know still comes through. Raises ValueError for an object with no `harness`."""
    if not isinstance(obj, Mapping) or not isinstance(obj.get("harness"), str):
        raise ValueError("a record's JSON object names its harness")
    known_kind = obj.get("kind") in KINDS
    cls = KINDS[obj["kind"]] if known_kind else Other
    names = {f.name for f in fields(cls)}
    unknown = set(obj.get("unknown") or ())
    values: dict[str, Any] = {name: obj[name] for name in names if name in obj}
    for name in unknown & names:
        values[name] = UNKNOWN
    if cls is Malformed:
        if "raw_base64" in obj:
            values["raw"] = base64.b64decode(obj["raw_base64"])
        elif isinstance(values.get("raw"), str):
            values["raw"] = values["raw"].encode("utf-8")
    elif cls is Marker:
        own = {name: UNKNOWN for name in MARKERS.get(obj.get("marker", ""), ())}
        own.update((key, value) for key, value in obj.items() if key not in _COMMON)
        own.update((name, UNKNOWN) for name in unknown - _COMMON)
        values["fields"] = own
    elif cls is Limits and isinstance(values.get("windows"), list):
        values["windows"] = tuple(_window(w) for w in values["windows"]
                                  if isinstance(w, Mapping))
    elif cls is Message and isinstance(values.get("content_kinds"), list):
        values["content_kinds"] = tuple(values["content_kinds"])
    if not known_kind:
        values["recognised"] = False
    for f in fields(cls):
        if f.name not in values and f.default is UNKNOWN:
            values[f.name] = UNKNOWN
    return cls(**values)


def document(**parts: Any) -> dict[str, Any]:
    """A JSON document sessions writes: `{"shape": SHAPE, ...}` with `parts` after it,
    each record in them (alone, or in a list or tuple) in its JSON form."""
    out: dict[str, Any] = {"shape": SHAPE}
    for key, value in parts.items():
        out[key] = _document_value(value)
    return out


def _document_value(value: Any) -> Any:
    if isinstance(value, Record):
        return to_json(value)
    if isinstance(value, (list, tuple)):
        return [_document_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(k): _document_value(v) for k, v in value.items()}
    return _plain(value)


__all__ = [
    "SHAPE", "OTEL_CONVENTIONS", "CLAUDE", "CODEX", "OPENCODE", "HARNESSES",
    "UNKNOWN", "known",
    "Record", "Request", "Message", "ToolCall", "ToolResult", "Marker", "MARKERS",
    "LimitWindow", "Limits", "Malformed", "Other", "KINDS",
    "UNDECODABLE", "NOT_OBJECT", "BAD_STRUCTURE",
    "SUMMARY_KEYS", "SUMMARY_CHARS", "tool_summary",
    "to_json", "from_json", "document",
]
