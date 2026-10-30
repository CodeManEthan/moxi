"""Claude Code's transcript: how a line decodes, where a session's file lives, and the root.

This module owns four facts for every Moxi module, and no other module owns any of them:

* **How a transcript line decodes** (`parse_line`, `iter_entries`, `stream_entries`,
  `MALFORMED`). Every reader walks the same decoder; Codex's rollout reader in `codex`
  decodes through it too. A line that will not decode is yielded as a `MALFORMED` entry
  carrying its raw text, never dropped, so a reader can see that a file is not complete.
* **Where a session's transcript lives** (`probe_root`, `find_for_session`,
  `search_roots`). Claude Code keys a transcript's directory on the session's launch
  directory, which nothing can derive afterwards, so finding one is a search over a root's
  project directories, never a derivation. Each root is asked once and what asking said is
  recorded: a hit, a miss, a root that could not be read, or an id the search refuses.
* **The projects root** (`projects_root`): `${CLAUDE_CONFIG_DIR:-~/.claude}/projects` in
  the environment it is handed, which is the session's own when the session reports it.
* **How a line maps into the one shape** (`Mapper`, `line_records`, `read_records`): each
  line to the records of `moxi_sessions.shape`, a request's repeated lines to one
  request record, and when a request has ended.

Nothing here decides anything about a session. Which lines count, and what a miss means,
are the caller's.

The session id. Claude Code writes `sessionId` on every line; older transcripts carry
`session_id`. A reader that wants the id off a line reads `sessionId` first and falls back
to `session_id`, since a key that was ever written stays readable.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .shape import (BAD_STRUCTURE, CLAUDE, NOT_OBJECT, UNDECODABLE, UNKNOWN, Malformed,
                    Marker, Message, Other, Record, Request, ToolCall, ToolResult,
                    tool_summary)

#: The `type` of an entry for a line that would not decode. It carries `lineno` and `raw`.
MALFORMED = "<malformed>"

#: What a session id may hold for the search: no path separator, no glob character, no
#: leading dot. Claude Code's ids are UUIDs; an id this refuses is never searched for.
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")


# --- the decoder ----------------------------------------------------------------------

def parse_line(line: str | bytes) -> dict | None:
    """One raw line, text or bytes, to its entry dict; None when the line is blank, will
    not decode, or is not a JSON object. Bytes are decoded as UTF-8, so a reader handed
    raw lines (`read_back`) decodes them here rather than with a parser of its own."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:                   # JSONDecodeError and UnicodeDecodeError both
        return None
    return obj if isinstance(obj, dict) else None


def decode(line: str | bytes) -> tuple[dict | None, str | None]:
    """One raw line to `(entry, None)`, or `(None, why)` when it is not an entry: `why` is
    `undecodable` (not JSON, or not UTF-8) or `not_object` (JSON but not an object). A
    blank line is `(None, None)`. `parse_line` is this with the reason dropped."""
    try:
        obj = json.loads(line)
    except ValueError:
        # The decoder takes JSON's own blanks around a value; other blanks a strip takes
        # (a form feed, say) are taken off before a second try.
        line = line.strip()
        if not line:
            return None, None
        try:
            obj = json.loads(line)
        except ValueError:
            return None, UNDECODABLE
    if not isinstance(obj, dict):
        return None, NOT_OBJECT
    return obj, None


def iter_entries(text: str) -> Iterator[dict]:
    """Every entry of a whole file's text, in file order.

    Each entry gains `lineno`, its 1-based line number, unless it already has one. A line
    that will not decode is yielded as `{"type": MALFORMED, "lineno": n, "raw": line}`.
    Blank lines are skipped.
    """
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        obj = parse_line(line)
        if obj is None:
            yield {"type": MALFORMED, "lineno": lineno, "raw": line}
        else:
            obj.setdefault("lineno", lineno)
            yield obj


def stream_entries(handle: Iterable[str], *, start_line: int = 0) -> Iterator[dict]:
    """Every entry from an open handle (any iterable of text lines), from `start_line` on.

    `start_line` is the count of lines already consumed: the 0-based index of the first
    line to read, so an incremental reader resumes where it stopped. `lineno` stays the
    absolute 1-based line number in the file. The handle is the caller's: this opens and
    closes nothing.

    Same bytes and `start_line=0` give the same entries as `iter_entries`, MALFORMED ones
    included. One difference at the margin: a file handle splits only on `\\n`, while
    `str.splitlines` also splits on `\\r`, `\\x0b`, `\\x0c` and their kind, which no
    transcript writes.
    """
    for index, raw in enumerate(handle):
        if index < start_line:
            continue
        line = raw.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        obj = parse_line(line)
        if obj is None:
            yield {"type": MALFORMED, "lineno": index + 1, "raw": line}
        else:
            obj.setdefault("lineno", index + 1)
            yield obj


def parse_timestamp(value: object) -> float | None:
    """An ISO 8601 timestamp to epoch seconds; a stamp with no zone is UTC. None for
    anything that is not one, a non-string included, so a reader that promises not to
    raise does not."""
    if not value or not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


# --- finding a session's transcript ---------------------------------------------------

HIT = "hit"
MISS = "miss"
UNREADABLE = "unreadable"
REFUSED = "refused"


@dataclass(frozen=True)
class RootProbe:
    """One root, asked once for one session id, and what asking said.

    `outcome` is `hit` (with `path`), `miss` (the root was read and holds no file for the
    id), `unreadable: <why>` (the root could not be read: an absent or unmounted directory
    is this, never a miss) or `refused: <why>` (the id is not one the search looks up, so
    nothing was looked at). Only `miss` says the file is absent.
    """

    root: str
    outcome: str
    path: str | None = None

    @property
    def hit(self) -> bool:
        return self.outcome == HIT

    def path_or_none(self) -> Path | None:
        return Path(self.path) if self.path else None

    def as_dict(self) -> dict:
        return {"root": self.root, "outcome": self.outcome, "path": self.path}


#: (root, session id) -> the transcript path found there. Searching a root's project
#: directories is the costly part of a read, so each process keeps what it has found; a
#: kept path that is no longer a file is searched for again.
_found: dict[tuple[str, str], Path] = {}
_found_lock = threading.Lock()


def _unreadable(root: Path) -> str | None:
    """Why `root` cannot be listed, or None when it can."""
    try:
        if not root.is_dir():
            return "not a directory (absent or unmounted?)"
        if not os.access(root, os.R_OK | os.X_OK):
            return "permission denied"
        with os.scandir(root):
            return None
    except OSError as exc:
        return str(exc)


def probe_root(root: str | Path, session_id: str) -> RootProbe:
    """Ask one root for one session's transcript, `<root>/<project>/<session_id>.jsonl`.

    Never raises: every OSError is a recorded outcome. When the id is in more than one
    project directory, the newest by modification time is the hit. A hit is kept for the
    process (see `_found`) and returned again while it is still a file.
    """
    root = Path(root)
    label = str(root)
    if not isinstance(session_id, str) or not SAFE_ID_RE.match(session_id):
        return RootProbe(label, f"{REFUSED}: {session_id!r} is not a session id the "
                                "search looks up")
    why = _unreadable(root)
    if why is not None:
        return RootProbe(label, f"{UNREADABLE}: {why}")
    key = (label, session_id)
    try:
        with _found_lock:
            kept = _found.get(key)
        if kept is not None and kept.is_file():
            return RootProbe(label, HIT, str(kept))
        matches = [p for p in root.glob(f"*/{session_id}.jsonl") if p.is_file()]
        if not matches:
            with _found_lock:
                _found.pop(key, None)
            return RootProbe(label, MISS)
        path = max(matches, key=lambda p: p.stat().st_mtime)
    except OSError as exc:
        return RootProbe(label, f"{UNREADABLE}: {exc}")
    with _found_lock:
        _found[key] = path
    return RootProbe(label, HIT, str(path))


def find_for_session(projects_root: str | Path, session_id: str) -> Path | None:
    """The session's transcript under one projects root, or None.

    None covers a miss, a root that could not be read and a refused id alike. A caller
    that decides anything from a transcript's absence asks `probe_root`, whose outcome
    tells the three apart.
    """
    return probe_root(projects_root, session_id).path_or_none()


def search_roots(*, live: str | Path, archives: Iterable[str | Path] | None = None,
                 extra: Iterable[str | Path] | None = None) -> list[Path]:
    """The ordered roots to probe: `live` first, then `archives`, then `extra`.

    `live` is required: the caller knows its live root (`projects_root` gives the usual
    one) and a default here would reach past a caller's redirect. Live comes first because
    a session in both places is a stale copy in the archive; `extra` (a command's
    `--root`) appends, so the default order holds whatever is added. `~` is expanded on
    `archives` and `extra`; a root named twice is probed once.
    """
    roots: list[Path] = [Path(live)]
    for group in (archives or (), extra or ()):
        roots.extend(Path(item).expanduser() for item in group)
    seen: set[str] = set()
    ordered: list[Path] = []
    for root in roots:
        if str(root) not in seen:
            seen.add(str(root))
            ordered.append(root)
    return ordered


# --- the projects root ----------------------------------------------------------------

def projects_root(environ: Mapping[str, str] | None = None) -> Path:
    """Where Claude Code writes transcripts, by its own rule over an environment.

    With `environ`: `${CLAUDE_CONFIG_DIR:-~/.claude}/projects` as that environment says,
    `~` and the home directory taken from its `HOME`. A process that reports its session's
    root passes its own environment, which is the session's.

    With None: the root for a session that stated none, which is the config key
    `claude.projects` when it is set, else the same rule over this process's environment.
    Raises `moxi.config.ConfigError` when the config file cannot be read.
    """
    if environ is None:
        from .settings import configured_projects

        configured = configured_projects()
        if configured is not None:
            return configured
        environ = os.environ
    home = environ.get("HOME") or str(Path.home())
    config = environ.get("CLAUDE_CONFIG_DIR")
    if config:
        if config == "~" or config.startswith("~/"):
            config = home + config[1:]
        base = Path(config)
    else:
        base = Path(home) / ".claude"
    return base / "projects"


# --- the mapping into the shape -------------------------------------------------------
#
# Each line maps to records of `moxi_sessions.shape`:
#
# | line                                      | records                                  |
# |-------------------------------------------|------------------------------------------|
# | `assistant` with `message.usage`          | one request per request key (below)      |
# | `assistant` content blocks                | text: one message; thinking: a reasoning |
# |                                           | message each; tool_use: a tool_call each |
# | `user`                                    | its text: a message; tool_result blocks: |
# |                                           | a tool_result each                       |
# | `system` `compact_boundary`               | marker `compaction`                      |
# | `system` `turn_duration`                  | marker `turn_end`                        |
# | `custom-title`, `ai-title`                | marker `title`                           |
# | anything else                             | other, `recognised` from `KNOWN_KINDS`   |
# | a line that is not an object              | malformed                                |
#
# A `progress` line's nested message is never read: counting it would count a subagent's
# requests twice.

#: The line kinds Claude Code 2.1.314 writes, the ones mapped above included. A kind off
#: this list is `other` with `recognised` false: a format change to look at.
KNOWN_KINDS = frozenset({
    "assistant", "user", "system", "attachment", "progress", "summary",
    "custom-title", "ai-title", "agent-name", "agent-setting", "api-request",
    "api-request-blob", "api-request-shape", "artifact-autoreact-ledger",
    "artifact-comment-monitor", "atis-latch", "bridge-session", "cost-state",
    "file-history-delta", "file-history-snapshot", "frame-link", "last-prompt", "mode",
    "permission-mode", "queue-operation", "tools-used",
})

#: The `system` line subtypes Claude Code 2.1.314 writes.
KNOWN_SYSTEM_SUBTYPES = frozenset({
    "turn_duration", "stop_hook_summary", "local_command", "bridge_status",
    "away_summary", "informational", "compact_boundary", "scheduled_task_fire",
    "model_refusal_fallback", "api_error", "agents_killed",
})

#: The model a request Claude Code made up itself names (an error turn, an interrupt).
SYNTHETIC_MODEL = "<synthetic>"

#: Block types that are a tool call, and on a user line a tool's result.
TOOL_CALL_BLOCKS = frozenset({"tool_use", "server_tool_use", "mcp_tool_use"})
TOOL_RESULT_BLOCKS = frozenset({"tool_result", "mcp_tool_result"})
#: Block types that are a thought rather than text.
THINKING_BLOCKS = frozenset({"thinking", "redacted_thinking"})

#: The bytes a line must hold to give a record of each kind, for a reader that decodes
#: only the lines a `kinds` filter can use. A kind with no entry can come from any line,
#: so asking for it decodes every line.
MARKER_BYTES: dict[str, tuple[bytes, ...]] = {
    "request": (b'"usage"',),
    "marker": (b'"compact_boundary"', b'"turn_duration"', b'"custom-title"',
               b'"ai-title"'),
    "limits": (),                            # Claude transcripts hold no limits readings
}

#: Lines decoded whatever the filter: they set the mapping's state (`promptId`) or can
#: end a request (`turn_duration`).
STATE_BYTES = (b'"promptId"', b'"turn_duration"')


def needs_decode(raw: bytes, kinds: frozenset[str] | set[str] | None) -> bool:
    """Whether a reader asking for `kinds` must decode this raw line.

    The rule: it never skips a line the full decode would turn into a record of a kind
    asked for, or that changes such a record (the state it reads, its `final`, a
    `closed` entry). With `kinds` None, or a kind `MARKER_BYTES` has no entry for, every
    line is decoded.
    """
    if kinds is None:
        return True
    for kind in kinds:
        markers = MARKER_BYTES.get(kind)
        if markers is None:
            return True
        if any(marker in raw for marker in markers):
            return True
    return any(marker in raw for marker in STATE_BYTES)


#: The line types each record kind can come from, for the kinds that come from only some.
#: A `turn_duration` (a `system` line) can also end a request, so asking for requests makes
#: `system` lines matter too.
KIND_LINE_TYPES: dict[str, frozenset[str]] = {
    "request": frozenset({"assistant", "system"}),
    "marker": frozenset({"system", "custom-title", "ai-title"}),
    "limits": frozenset(),
}

_WS = rb'[ \t\r\n]*'
#: A top-level key and its value, inside a head: a string that a colon follows is a key.
#: The group is a string value; any other value leaves it empty.
_HEAD_VALUE = re.compile(rb'"[^"]*"' + _WS + rb':' + _WS + rb'(?:"([^"]*)"|[-0-9tfn])')

#: The length from which a filtered read looks at a line's head before its bytes: below
#: it, looking for the marker bytes through the whole line costs less.
HEAD_FIRST = 4096

#: What `line_head` returns for a key it did not see.
_UNSEEN = object()


def line_head(raw: bytes) -> tuple[Any, Any, bool]:
    """The top-level `type` and `promptId` a line writes in its head, the part before
    anything nested or escaped, and whether the head is the whole line.

    Returns `(type, prompt, whole)`: each value as bytes when it is a string with
    something in it, None when it is empty or not a string, `_UNSEEN` when the head does
    not hold the key. A key the head writes twice reads as its last, as the decode reads
    it. Claude Code writes `type` and `promptId` among a line's first keys, ahead of its
    message, so a read that needs only a line's turn can take it from here without
    decoding a long tool result. Nothing here says the line is valid JSON; that needs the
    whole decode."""
    if raw[:1] != b"{":
        return _UNSEEN, _UNSEEN, False
    # The head ends at the first brace, bracket or backslash after the opening brace.
    # Before it nothing is nested and no string holds an escape, so every quote in it
    # opens or closes a string, and a string that a colon follows is a top-level key.
    end = raw.find(b"{", 1)
    whole = end < 0
    if whole:
        end = len(raw)
    for stop in (b"[", b"\\"):
        cut = raw.find(stop, 1, end)
        if cut >= 0:
            end, whole = cut, False
    return _head_value(raw, b'"type"', end), _head_value(raw, b'"promptId"', end), whole


def _head_value(raw: bytes, key: bytes, end: int) -> Any:
    at = raw.rfind(key, 1, end)
    if at < 0:
        return _UNSEEN
    found = _HEAD_VALUE.match(raw, at, end)
    if found is None:
        return _UNSEEN
    return found.group(1) or None


#: A `promptId` as Claude Code writes it, for reading a line's turn without decoding it.
_PROMPT = re.compile(rb'"promptId"[ \t\r\n]*:[ \t\r\n]*"([^"\\]*)"')


def turn_after(raw: bytes, current: str | None) -> str | None:
    """The turn after a line that can change nothing but the turn. When the line holds
    one `promptId` and it names the current turn, the line is not decoded; otherwise it
    is, and its own top-level `promptId` (if any) is the turn."""
    if current is not None:
        first = raw.find(b'"promptId"')
        found = _PROMPT.match(raw, first) if first >= 0 else None
        if (found is not None and found.group(1) == current.encode("utf-8")
                and raw.find(b'"promptId"', first + 10) < 0):
            return current
    entry = parse_line(raw)
    prompt = entry.get("promptId") if entry is not None else None
    return prompt if isinstance(prompt, str) and prompt else current


def _said(obj: dict, key: str) -> Any:
    """The value the line wrote under `key`, None for a null, `UNKNOWN` when absent."""
    return obj[key] if key in obj else UNKNOWN


def _text(obj: dict, key: str) -> Any:
    """A string the line wrote under `key`; None for a null; else `UNKNOWN`."""
    value = obj.get(key, UNKNOWN)
    return value if value is None or isinstance(value, str) else UNKNOWN


def _count(obj: object, key: str) -> Any:
    """A token count the line wrote under `key`, else `UNKNOWN`."""
    value = obj.get(key) if isinstance(obj, dict) else None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return UNKNOWN


def request_key(entry: dict, line: int | None = None) -> str:
    """A request's dedupe key: `requestId`, else `msg:<message.id>`, else
    `uuid:<uuid>`, else `line:<n>`. The middle step stops a request with no `requestId`
    counting once per line."""
    request_id = entry.get("requestId")
    if isinstance(request_id, str) and request_id:
        return request_id
    message = entry.get("message")
    message_id = message.get("id") if isinstance(message, dict) else None
    if isinstance(message_id, str) and message_id:
        return f"msg:{message_id}"
    uuid = entry.get("uuid")
    if isinstance(uuid, str) and uuid:
        return f"uuid:{uuid}"
    return f"line:{line}"


def _server(name: Any) -> str | None:
    """The MCP server a tool name carries (`mcp__<server>__<tool>`), else None."""
    if isinstance(name, str) and name.startswith("mcp__"):
        server, sep, _tool = name[5:].partition("__")
        if sep and server:
            return server
    return None


def _result_text(content: Any) -> str:
    """A tool result's text as written: the string, or its text blocks joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(block.get("text", "")) for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return ""


def _usage_request(entry: dict, message: dict, usage: dict, common: dict,
                   key: str, final: bool = True) -> Request:
    fresh = _count(usage, "input_tokens")
    read = _count(usage, "cache_read_input_tokens")
    written = _count(usage, "cache_creation_input_tokens")
    total = (fresh + read + written
             if UNKNOWN not in (fresh, read, written) else UNKNOWN)
    # The 1-hour write is the `cache_creation` object's, clamped to the creation total, and
    # the 5-minute write the remainder. Claude Code wrote the 1-hour tier and that object
    # together, so a line without the object wrote no 1-hour cache: its whole write is
    # 5-minute, not an unknown split.
    creation = usage.get("cache_creation")
    if written is UNKNOWN:
        one_hour = five_minutes = UNKNOWN
    else:
        one_hour = (_count(creation, "ephemeral_1h_input_tokens")
                    if isinstance(creation, dict) else 0)
        one_hour = 0 if one_hour is UNKNOWN else min(max(one_hour, 0), written)
        five_minutes = written - one_hour
    model = message.get("model")
    synthetic = model == SYNTHETIC_MODEL
    requested = entry.get("requestedModel")
    return Request(
        **common,
        request_id=key,
        model=requested if isinstance(requested, str) else UNKNOWN,
        response_model=model if model is None or isinstance(model, str) else UNKNOWN,
        provider=None if synthetic else UNKNOWN,
        input_tokens=total,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=written,
        cache_creation_5m_input_tokens=five_minutes,
        cache_creation_1h_input_tokens=one_hour,
        output_tokens=_count(usage, "output_tokens"),
        reasoning_output_tokens=_count(usage.get("output_tokens_details"),
                                       "thinking_tokens"),
        service_tier=_said(usage, "service_tier"),
        speed=_said(usage, "speed"),
        sidechain=entry.get("isSidechain") is True,
        synthetic=synthetic,
        final=final,
    )


def _origin(entry: dict) -> Any:
    origin = entry.get("origin")
    if isinstance(origin, dict) and isinstance(origin.get("kind"), str):
        return origin["kind"]
    return _text(entry, "userType")


def _content_records(entry: dict, message: dict, role: str, common: dict,
                     synthetic: bool) -> list[Record]:
    """A user or assistant line's content blocks as records, in block order.

    The line's text and every block that is neither a thought nor a tool's make one
    message, placed where its first block was; each thought is a reasoning message; each
    tool block is its own record."""
    flags = {key: value for key, value in entry.items()
             if isinstance(value, bool) and key != "isSidechain"}
    origin = _origin(entry)
    out: list[Record] = []
    texts: list[str] = []
    kinds: list[str] = []
    slot: int | None = None
    content = message.get("content")
    if isinstance(content, str):
        texts.append(content)
        kinds.append("text")
        slot = 0
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            btype = btype if isinstance(btype, str) else "?"
            if btype in TOOL_CALL_BLOCKS:
                name = _said(block, "name")
                tool_input = block.get("input")
                out.append(ToolCall(**common, call_id=_said(block, "id"), name=name,
                                    input=tool_input, server=_server(name),
                                    summary=tool_summary(name, tool_input)))
            elif role == "user" and btype in TOOL_RESULT_BLOCKS:
                output = _result_text(block.get("content"))
                out.append(ToolResult(**common, call_id=_said(block, "tool_use_id"),
                                      output=output, is_error=block.get("is_error") is True,
                                      size_bytes=len(output.encode("utf-8", "replace"))))
            elif btype in THINKING_BLOCKS:
                thought = block.get("thinking")
                out.append(Message(**common, role=role, reasoning=True,
                                   text=thought if isinstance(thought, str) else "",
                                   content_kinds=(btype,), flags=flags, origin=origin,
                                   synthetic=synthetic))
            else:
                if slot is None:
                    slot = len(out)
                kinds.append(btype)
                if btype == "text" and isinstance(block.get("text"), str):
                    texts.append(block["text"])
    if slot is not None:
        out.insert(slot, Message(**common, role=role, text="\n".join(texts),
                                 content_kinds=tuple(kinds), flags=flags, origin=origin,
                                 synthetic=synthetic))
    return out


#: The keys every line's mapping names, so they stay out of `extra`.
_NAMED = frozenset({"type", "timestamp", "sessionId", "session_id", "agentId", "promptId",
                    "isSidechain"})
_NAMED_BY_KIND = {
    "assistant": frozenset({"message", "requestId", "requestedModel", "origin",
                            "userType"}),
    "user": frozenset({"message", "origin", "userType"}),
    "system": frozenset({"subtype", "compactMetadata", "durationMs"}),
    "custom-title": frozenset({"customTitle"}),
    "ai-title": frozenset({"aiTitle"}),
}
_NAMED_FOR = {kind: _NAMED | named for kind, named in _NAMED_BY_KIND.items()}

#: The kinds that come from a line's content blocks. A read that asks for none of them
#: does not map the blocks.
CONTENT_KINDS = frozenset({"message", "tool_call", "tool_result"})

def line_records(raw: bytes | str, *, at: dict, session_id: Any = None,
                 agent_id: str | None = None, prompt_id: str | None = None,
                 keep_raw: bool = False, unterminated: bool = False,
                 ) -> tuple[Request | None, list[Record], str | None]:
    """One line's records, with nothing carried between lines.

    Returns `(request, records, prompt_id)`: the request this line writes (one per line,
    before any merging of a request's lines), the line's other records in block order, and
    the `promptId` current after the line (its own, else the one handed in). A blank line
    gives `(None, [], prompt_id)`.

    `at` is the record's position (`{"line": n, "offset": b}`). `session_id` and
    `agent_id` are the source's; where they are None the line's own `sessionId` and
    `agentId` are used. `prompt_id` is the turn the line is in, from the lines before it.
    `unterminated` marks the file's last line written without its newline; it is carried
    only by a malformed record.
    """
    pending, records, prompt_id, _ends = _map_line(raw, at, session_id, agent_id,
                                                   prompt_id, keep_raw, unterminated)
    return (_build(pending, True) if pending is not None else None), records, prompt_id


def _common(entry: dict, kind: str, raw_kind: str, at: dict, session_id: Any,
            agent_id: str | None, prompt_id: str | None, keep_raw: bool) -> dict:
    """The fields every record of one line shares."""
    if session_id is None:
        session_id = entry.get("sessionId")
        if not isinstance(session_id, str):
            session_id = entry.get("session_id")
        if not isinstance(session_id, str):
            session_id = UNKNOWN
    if agent_id is None and isinstance(entry.get("agentId"), str):
        agent_id = entry["agentId"]
    stamp = parse_timestamp(entry.get("timestamp"))
    named = _NAMED_FOR.get(kind, _NAMED)
    if kind == "assistant" or kind == "user":
        # A user or assistant line's booleans are its message's `flags`, not `extra`.
        extra = {key: value for key, value in entry.items()
                 if key not in named and value is not True and value is not False}
    else:
        extra = {key: value for key, value in entry.items() if key not in named}
    return {
        "harness": CLAUDE, "session_id": session_id, "agent_id": agent_id, "at": at,
        "time": UNKNOWN if stamp is None else stamp,
        "turn_id": prompt_id if prompt_id else UNKNOWN,
        "raw_kind": raw_kind,
        "extra": extra,
        "raw": entry if keep_raw else None,
    }


def _build(pending: tuple, final: bool) -> Request:
    """The request a line wrote, from what `_map_line` kept of it."""
    entry, message, usage, common, key = pending
    if isinstance(common, tuple):
        common = _common(entry, "assistant", "assistant", *common)
    return _usage_request(entry, message, usage, common, key, final)


def _map_line(raw: bytes | str, at: dict, session_id: Any, agent_id: str | None,
              prompt_id: str | None, keep_raw: bool, unterminated: bool,
              content: bool = True, others: bool = True,
              ) -> tuple[tuple | None, list[Record], str | None, bool]:
    """`line_records` for a read that may not keep every kind: `content` false when it
    keeps no message, tool call or tool result, `others` false when it keeps no marker and
    no other record.

    Returns `(pending, records, prompt_id, ends)`. The request is not built: `pending` is
    what builds it (`_build`), so a forward read builds each request once, from its
    newest line, when it hands it on. `ends` is true for a line that ends a request (a
    `turn_duration`). Records of a kind the read does not keep may be left unmapped; a
    malformed line's record always comes."""
    entry, why = decode(raw)
    if why is not None:
        data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        return None, [Malformed(harness=CLAUDE, session_id=session_id or UNKNOWN,
                                agent_id=agent_id, at=at,
                                turn_id=prompt_id if prompt_id else UNKNOWN,
                                raw=data.strip(b"\r\n"),
                                unterminated=unterminated, why=why)], prompt_id, False
    if entry is None:
        return None, [], prompt_id, False

    kind = entry.get("type")
    kind = kind if isinstance(kind, str) else ""
    subtype = entry.get("subtype") if kind == "system" else None
    raw_kind = f"system/{subtype}" if isinstance(subtype, str) else kind
    own_prompt = entry.get("promptId")
    if isinstance(own_prompt, str) and own_prompt:
        prompt_id = own_prompt
    ends = raw_kind == "system/turn_duration"

    if (kind == "assistant" or kind == "user") and not content:
        # No content kind is kept: a line with a message gives at most its request, and
        # what the request shares with the line's other records is worked out only for
        # the request that is handed on.
        message = entry.get("message")
        if isinstance(message, dict):
            usage = message.get("usage") if kind == "assistant" else None
            if not isinstance(usage, dict):
                return None, [], prompt_id, False
            lazy = (at, session_id, agent_id, prompt_id, keep_raw)
            return ((entry, message, usage, lazy, request_key(entry, at.get("line"))), [],
                    prompt_id, False)
    elif not others and kind != "assistant" and kind != "user":
        return None, [], prompt_id, ends

    common = _common(entry, kind, raw_kind, at, session_id, agent_id, prompt_id, keep_raw)
    if kind == "assistant" or kind == "user":
        message = entry.get("message")
        if not isinstance(message, dict):
            data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
            return None, [Malformed(**{**common, "raw": data.strip(b"\r\n")},
                                    unterminated=unterminated, why=BAD_STRUCTURE)], \
                prompt_id, False
        pending = None
        synthetic = False
        if kind == "assistant":
            synthetic = message.get("model") == SYNTHETIC_MODEL
            usage = message.get("usage")
            if isinstance(usage, dict):
                pending = (entry, message, usage, common, request_key(entry, at.get("line")))
        if not content:
            return pending, [], prompt_id, False
        role = message.get("role")
        role = role if isinstance(role, str) else kind
        return (pending, _content_records(entry, message, role, common, synthetic),
                prompt_id, False)

    if kind == "system" and subtype == "compact_boundary":
        meta = entry.get("compactMetadata")
        meta = meta if isinstance(meta, dict) else {}
        return None, [Marker(**common, marker="compaction", fields={
            "trigger": _said(meta, "trigger"), "pre_tokens": _said(meta, "preTokens")})], \
            prompt_id, ends
    if kind == "system" and subtype == "turn_duration":
        return None, [Marker(**common, marker="turn_end",
                             fields={"duration_ms": _said(entry, "durationMs")})], \
            prompt_id, ends
    if kind == "custom-title":
        return None, [Marker(**common, marker="title", fields={
            "title": _said(entry, "customTitle"), "source": "custom"})], prompt_id, ends
    if kind == "ai-title":
        return None, [Marker(**common, marker="title", fields={
            "title": _said(entry, "aiTitle"), "source": "ai"})], prompt_id, ends
    if kind == "system":
        recognised = subtype in KNOWN_SYSTEM_SUBTYPES
    else:
        recognised = kind in KNOWN_KINDS
    return None, [Other(**common, recognised=recognised)], prompt_id, ends


class Mapper:
    """Lines to records, forward, merging each request's lines into one request record.

    Claude Code writes 2 to 8 lines per request, one per content block, each with the same
    usage. Lines of other kinds (tool results, attachments) can fall between a request's
    lines, but no line of another request does. So a request has ended when a line of
    another request or a `turn_duration` line follows it, and the mapper holds the newest
    line's request until then:

    * `feed` returns the records ready to hand on: an ended request first (with `final`
      true, at its newest line, its values from that line), then the line's own records.
      A request is the one record that comes later than its lines: it comes where it
      ended.
    * `finish` returns the request still held, with `final` false: more of it may still be
      written. Call it once, at the end of a read.

    A read that resumes where an earlier one stopped hands in that read's `prompt_id` and
    `open_key` (the request it left with `final` false). If this read meets more lines of
    that request, it comes again, whole, from them; if it meets the end of it first, the
    key is put in `closed` and the request does not come again. A request that comes with
    `final` true is never also in `closed`. Only request records ever come twice, and a
    caller replaces them by `request_id`; a key that comes back after another request
    ended it (subagent lines written inline by old versions can interleave) is handed on
    again the same way.

    `session_id` and `agent_id` are the source's (None to take each line's own).

    `kinds`, when given, are the record kinds the caller keeps. The mapper then leaves
    out the work no kept record needs: it maps no content blocks unless a content kind is
    kept, builds no marker or other record unless one is kept, works out a request's
    common fields only for the line it is handed on from, and `skip` passes by the lines
    that can give no kept record. The kept records,
    `final`, `closed` and `open_key` are the same as without it.
    """

    def __init__(self, *, session_id: str | None = None, agent_id: str | None = None,
                 keep_raw: bool = False, prompt_id: str | None = None,
                 open_key: str | None = None,
                 kinds: frozenset[str] | set[str] | None = None):
        self.session_id = session_id
        self.agent_id = agent_id
        self.keep_raw = keep_raw
        self.prompt_id = prompt_id
        self.open_key = open_key
        self.closed: list[str] = []
        self._held: tuple | None = None
        self.kinds = frozenset(kinds) if kinds is not None else None
        self._content = self.kinds is None or not self.kinds.isdisjoint(CONTENT_KINDS)
        self._others = self.kinds is None or not self.kinds.isdisjoint({"marker", "other"})
        # The marker bytes of every kind kept (None when a kept kind can come from any
        # line), and the line types that can give a kept record (None likewise).
        self._markers: tuple[bytes, ...] | None = None
        self._types: frozenset[bytes] | None = None
        if self.kinds is not None and all(k in MARKER_BYTES for k in self.kinds):
            self._markers = tuple(m for k in sorted(self.kinds) for m in MARKER_BYTES[k])
        if self.kinds is not None and all(k in KIND_LINE_TYPES for k in self.kinds):
            self._types = frozenset(t.encode() for k in self.kinds
                                    for t in KIND_LINE_TYPES[k])

    def _end_open(self, out: list[Record], key: str | None) -> None:
        """A line of request `key` (None for a turn's end) was read: end what it ends."""
        held = self._held
        if held is not None and held[4] != key:
            out.append(_build(held, True))
            self._held = None
        if self.open_key is not None and self.open_key != key:
            self.closed.append(self.open_key)
            self.open_key = None

    def skip(self, raw: bytes) -> bool:
        """Whether a read keeping `kinds` can pass this line by. A line passed by gives no
        kept record and changes none (its state, its `final`, a `closed` entry); its turn,
        if it writes one, is taken. Always false without `kinds`.

        Two ways to tell:

        * For a line longer than `HEAD_FIRST`, the line's head (`line_head`) names a type no kept record comes from. Then only
          its `promptId` matters: one the head shows naming the current turn, or none in a
          head that is the whole line, changes nothing. Claude Code writes each key once,
          so the head's `type` and `promptId` are the line's; a line that wrote one of
          them again past its head would be read by its head.
        * The bytes: the line holds no kept kind's marker bytes and no `turn_duration`, so
          only a `promptId` in it can matter, and `turn_after` reads that.
        """
        if self.kinds is None:
            return False
        if self._types is not None and len(raw) > HEAD_FIRST:
            kind, prompt, whole = line_head(raw)
            if kind is not _UNSEEN and kind not in self._types:
                if prompt is _UNSEEN:
                    if whole:
                        return True
                elif prompt is None:
                    return True
                elif self.prompt_id is not None and prompt == self.prompt_id.encode("utf-8"):
                    return True
                else:
                    return False                 # a new turn: the whole decode reads it
        if self._markers is None:
            return False
        for marker in self._markers:
            if marker in raw:
                return False
        if b'"turn_duration"' in raw:
            return False
        if b'"promptId"' in raw:
            self.prompt_id = turn_after(raw, self.prompt_id)
        return True

    def feed(self, raw: bytes | str, *, line: int, offset: int,
             unterminated: bool = False) -> list[Record]:
        """One line, `line` its 1-based number and `offset` the byte offset of its start.
        Returns the records ready to hand on, in order."""
        pending, records, self.prompt_id, ends = _map_line(
            raw, {"line": line, "offset": offset}, self.session_id, self.agent_id,
            self.prompt_id, self.keep_raw, unterminated, self._content, self._others)
        out: list[Record] = []
        if pending is not None:
            key = pending[4]
            self._end_open(out, key)
            if self.open_key == key:
                self.open_key = None             # it comes again, whole, from here
            self._held = pending
        elif ends:
            self._end_open(out, None)
        out.extend(records)
        return out

    def finish(self) -> list[Record]:
        """The request still held, `final` false, as the read's last record; its key is
        the next read's `open_key`."""
        held, self._held = self._held, None
        if held is None:
            return []
        self.open_key = held[4]
        return [_build(held, False)]


def read_records(path: str | Path, *, session_id: str | None = None,
                 agent_id: str | None = None, kinds: set[str] | frozenset[str] | None = None,
                 keep_raw: bool = False) -> Iterator[Record]:
    """Every record of one transcript, forward, one line held at a time.

    The file's size is taken from the open handle once, and no byte past it is read, so
    a line being written as the read starts is not half read. A last line with no newline
    is read too; if it will not decode, its malformed record is `unterminated`. With
    `kinds`, only records of those kinds come back, and a line that cannot give one is
    not decoded (`Mapper.skip`); the records that do come back are the same as without
    the filter. Raises OSError when the file cannot be opened or read.
    """
    wanted = frozenset(kinds) if kinds is not None else None
    mapper = Mapper(session_id=session_id, agent_id=agent_id, keep_raw=keep_raw,
                    kinds=wanted)
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
            unterminated = not data.endswith(b"\n")
            if mapper.skip(data):
                continue
            for record in mapper.feed(data, line=number, offset=start,
                                      unterminated=unterminated):
                if wanted is None or record.kind in wanted:
                    yield record
    for record in mapper.finish():
        if wanted is None or record.kind in wanted:
            yield record
